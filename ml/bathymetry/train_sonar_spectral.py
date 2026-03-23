#!/usr/bin/env python3
"""
OpenCatch — Sonar DEM + Sentinel-2 Spectral Training Pipeline

Pairs MN DNR sonar DEMs (1,951 lakes, 0-50m depth) with Sentinel-2 spectral
data to train a real bathymetry model. Replaces noisy ICESat-2 (3-5m cluster,
R²=-4.14) with REAL surveyed sonar depths.

For each lake:
  1. Load sonar DEM (depth raster, UTM projection)
  2. Sample ~100 points across depth bins (stratified)
  3. Find matching S2 scene via Element84 STAC (bbox search, summer, <20% cloud)
  4. Extract S2 bands via COG windowed reads (no full tile download)
  5. Apply full SDB preprocessing (DN→reflectance, glint removal, physics features)
  6. Collect all points into training DataFrame

Then:
  7. Split by LAKE (70/15/15) — no lake in both train and test
  8. Train ensemble: XGBoost + LightGBM + MLP
  9. Report HONEST metrics with R² check and per-depth-bin RMSE

Usage:
    # Quick test (200 lakes, ~1 hour)
    python train_sonar_spectral.py --max-lakes 200

    # Full run (1,951 lakes, ~4 hours with 4 threads)
    python train_sonar_spectral.py --threads 4

    # Resume from checkpoint
    python train_sonar_spectral.py --resume /data/sonar_s2_checkpoint.parquet

Requirements:
    pip install pystac-client rasterio numpy pandas pyarrow tqdm scikit-learn
    pip install xgboost lightgbm torch pyproj
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pyproj import Transformer
from tqdm import tqdm

# Import our preprocessing pipeline
sys.path.insert(0, str(Path(__file__).parent))
try:
    from sdb_preprocessing import SDBPreprocessor, compute_sdb_features_array
except ImportError:
    sys.path.insert(0, "/root")
    from sdb_preprocessing import SDBPreprocessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sonar_spectral")

# ── Constants ────────────────────────────────────────────────────────

E84_STAC_URL = "https://earth-search.aws.element84.com/v1"

S2_BANDS = ["blue", "green", "red", "rededge1", "rededge2", "rededge3",
            "nir", "nir08", "swir16", "swir22"]

DATA_DIR = Path("/data/training/v2")
OUTPUT_DIR = Path("/data/sonar_s2")
CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.parquet"

# Depth bins for stratified sampling
DEPTH_BINS = [(0.5, 2), (2, 5), (5, 10), (10, 20), (20, 50)]
POINTS_PER_BIN = 20  # → ~100 points per lake (5 bins × 20)

# S2 search parameters
S2_DATE_RANGE = "2020-06-01/2024-09-30"
S2_MAX_CLOUD = 20.0

EPS = 1e-8


# ── Lake Processing ──────────────────────────────────────────────────

def get_lake_bbox_wgs84(depth_path: str) -> tuple:
    """
    Get lake bounding box in WGS84 (lon/lat) from a UTM raster.
    Returns (west, south, east, north) in degrees.
    """
    import rasterio

    with rasterio.open(depth_path) as ds:
        bounds = ds.bounds
        src_crs = ds.crs

    # Transform UTM bounds to WGS84
    transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    west, south = transformer.transform(bounds.left, bounds.bottom)
    east, north = transformer.transform(bounds.right, bounds.top)

    return (west, south, east, north)


def sample_depth_points(depth_path: str, max_points: int = 100) -> pd.DataFrame:
    """
    Load sonar DEM and sample points stratified by depth bin.

    Returns DataFrame with columns: row, col, depth_m, lat, lon
    (lat/lon in WGS84)
    """
    import rasterio

    with rasterio.open(depth_path) as ds:
        depth = ds.read(1)
        transform = ds.transform
        src_crs = ds.crs

    # Find valid depth pixels (> 0.5m to exclude shoreline noise)
    valid_mask = depth > 0.5
    rows, cols = np.where(valid_mask)
    depths = depth[valid_mask]

    if len(depths) < 10:
        return pd.DataFrame()

    # Stratified sampling across depth bins
    sampled_indices = []
    for lo, hi in DEPTH_BINS:
        bin_mask = (depths >= lo) & (depths < hi)
        bin_indices = np.where(bin_mask)[0]
        if len(bin_indices) == 0:
            continue
        n_sample = min(POINTS_PER_BIN, len(bin_indices))
        chosen = np.random.choice(bin_indices, size=n_sample, replace=False)
        sampled_indices.extend(chosen.tolist())

    if not sampled_indices:
        return pd.DataFrame()

    sampled_indices = np.array(sampled_indices)
    s_rows = rows[sampled_indices]
    s_cols = cols[sampled_indices]
    s_depths = depths[sampled_indices]

    # Convert pixel coords to geographic coords (UTM)
    xs, ys = rasterio.transform.xy(transform, s_rows, s_cols)
    xs = np.array(xs)
    ys = np.array(ys)

    # Transform to WGS84
    transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    lons, lats = transformer.transform(xs, ys)

    return pd.DataFrame({
        "row": s_rows,
        "col": s_cols,
        "depth_m": s_depths,
        "lon": lons,
        "lat": lats,
    })


def find_s2_scene(client, bbox: tuple, max_cloud: float = S2_MAX_CLOUD):
    """
    Find best (least cloudy, summer) S2 L2A scene for a lake bbox.
    Returns (stac_item, scene_date) or (None, None).
    """
    try:
        search = client.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=S2_DATE_RANGE,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            sortby=["+properties.eo:cloud_cover"],
            max_items=5,
        )
        items = list(search.items())
        if items:
            # Prefer summer scenes (Jun-Sep)
            summer_items = []
            for item in items:
                dt_str = item.properties.get("datetime", "")
                if dt_str:
                    month = int(dt_str[5:7])
                    if 6 <= month <= 9:
                        summer_items.append(item)

            best = summer_items[0] if summer_items else items[0]
            scene_date = best.properties.get("datetime", "")[:10]
            return best, scene_date
    except Exception as e:
        log.debug(f"STAC search failed: {e}")

    return None, None


def extract_s2_at_points(item, points_df: pd.DataFrame, window_size: int = 3) -> pd.DataFrame:
    """
    Extract S2 band values at each point location using COG windowed reads.

    S2 COGs are in UTM projection, so we transform WGS84 lon/lat points
    to the S2 CRS before reading. Reads one window per band covering the
    entire lake bbox, then samples individual pixels.
    """
    import rasterio
    from rasterio.windows import from_bounds

    results = []

    # First, determine the S2 CRS by opening one band
    ref_band = "blue"
    ref_href = item.assets[ref_band].href
    try:
        with rasterio.open(ref_href) as ds:
            s2_crs = ds.crs
    except Exception:
        return pd.DataFrame()

    # Transform point coordinates from WGS84 to S2 CRS (UTM)
    transformer = Transformer.from_crs("EPSG:4326", s2_crs, always_xy=True)
    s2_xs, s2_ys = transformer.transform(
        points_df["lon"].values, points_df["lat"].values
    )

    # Lake bbox in S2 CRS (with buffer)
    buf = 100  # 100m buffer in UTM
    x_min, x_max = np.min(s2_xs) - buf, np.max(s2_xs) + buf
    y_min, y_max = np.min(s2_ys) - buf, np.max(s2_ys) + buf

    # Read each band once for the entire lake bbox, then sample pixels
    band_data = {}
    band_transforms = {}

    for band_name in S2_BANDS + ["scl"]:
        if band_name not in item.assets:
            if band_name == "scl":
                continue
            return pd.DataFrame()

        href = item.assets[band_name].href
        try:
            with rasterio.open(href) as ds:
                # Windowed read using UTM coordinates
                window = from_bounds(x_min, y_min, x_max, y_max, ds.transform)

                # Clamp window to raster bounds
                window = window.intersection(
                    rasterio.windows.Window(0, 0, ds.width, ds.height)
                )

                if window.width < 1 or window.height < 1:
                    return pd.DataFrame()

                data = ds.read(1, window=window)
                win_transform = ds.window_transform(window)

                band_data[band_name] = data
                band_transforms[band_name] = win_transform

        except Exception as e:
            log.debug(f"Failed to read {band_name}: {e}")
            if band_name in S2_BANDS:
                return pd.DataFrame()

    if not all(b in band_data for b in S2_BANDS):
        return pd.DataFrame()

    # Sample each point from the pre-loaded band arrays
    half = window_size // 2
    for i, (_, pt) in enumerate(points_df.iterrows()):
        point_values = {"depth_m": pt["depth_m"], "lat": pt["lat"], "lon": pt["lon"]}
        valid = True

        # Use pre-transformed UTM coordinates
        pt_x, pt_y = s2_xs[i], s2_ys[i]

        for band_name in S2_BANDS + (["scl"] if "scl" in band_data else []):
            data = band_data[band_name]
            transform = band_transforms[band_name]

            # Get pixel coords in this band's windowed array (UTM coords)
            try:
                col_f, row_f = ~transform * (pt_x, pt_y)
                px, py = int(round(col_f)), int(round(row_f))
            except Exception:
                valid = False
                break

            if not (0 <= py < data.shape[0] and 0 <= px < data.shape[1]):
                valid = False
                break

            # Extract window
            r_start = max(0, py - half)
            r_end = min(data.shape[0], py + half + 1)
            c_start = max(0, px - half)
            c_end = min(data.shape[1], px + half + 1)
            window_data = data[r_start:r_end, c_start:c_end]

            if window_data.size == 0:
                valid = False
                break

            if band_name == "scl":
                cy = min(half, window_data.shape[0] - 1)
                cx = min(half, window_data.shape[1] - 1)
                point_values["scl"] = int(window_data[cy, cx])
            else:
                val = float(np.nanmean(window_data))
                if val <= 0 or np.isnan(val):
                    valid = False
                    break
                point_values[band_name] = val

        if valid:
            results.append(point_values)

    return pd.DataFrame(results)


def process_single_lake(lake_dir: str, stac_client) -> Optional[pd.DataFrame]:
    """
    Process a single lake: load DEM, sample points, find S2, extract bands,
    compute physics features.

    Returns DataFrame of processed points, or None on failure.
    """
    lake_id = Path(lake_dir).name
    depth_path = os.path.join(lake_dir, "depth.tif")

    if not os.path.exists(depth_path):
        return None

    try:
        # 1. Sample depth points from sonar DEM
        points = sample_depth_points(depth_path)
        if len(points) < 5:
            return None

        # 2. Get lake bbox in WGS84
        bbox = get_lake_bbox_wgs84(depth_path)

        # 3. Find matching S2 scene
        item, scene_date = find_s2_scene(stac_client, bbox)
        if item is None:
            return None

        # 4. Extract S2 bands at sample points
        s2_data = extract_s2_at_points(item, points)
        if len(s2_data) < 5:
            return None

        # 5. Apply SDB preprocessing pipeline
        preprocessor = SDBPreprocessor()

        # Convert DN to reflectance for glint estimation
        blue_r = s2_data["blue"].values / 10000.0
        green_r = s2_data["green"].values / 10000.0
        red_r = s2_data["red"].values / 10000.0
        nir_r = s2_data["nir"].values / 10000.0

        # Estimate glint slopes from this lake's pixels
        preprocessor.estimate_glint_slopes(blue_r, green_r, red_r, nir_r)

        # Process each point through full pipeline
        processed = []
        sun_zenith = item.properties.get("view:sun_elevation")
        if sun_zenith is not None:
            sun_zenith = 90.0 - sun_zenith

        for _, row in s2_data.iterrows():
            bands = {b: row[b] for b in S2_BANDS if b in row.index}
            features = preprocessor.process_point(
                bands=bands,
                scl=row.get("scl"),
                sun_zenith=sun_zenith,
                is_raw_dn=True,
            )

            if features is None:
                continue

            features["depth_m"] = row["depth_m"]
            features["lat"] = row["lat"]
            features["lon"] = row["lon"]
            features["lake_id"] = lake_id
            features["s2_date"] = scene_date
            processed.append(features)

        if not processed:
            return None

        result = pd.DataFrame(processed)
        return result

    except Exception as e:
        log.debug(f"Lake {lake_id} failed: {e}")
        return None


# ── Data Collection ──────────────────────────────────────────────────

def collect_training_data(
    data_dir: Path,
    max_lakes: Optional[int] = None,
    n_threads: int = 1,
    checkpoint_path: Path = CHECKPOINT_PATH,
    resume_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Collect training data from all lakes.

    Processes lakes sequentially or in parallel, with checkpointing.
    """
    from pystac_client import Client

    # Find all lake directories
    lake_dirs = sorted([str(d) for d in data_dir.iterdir() if d.is_dir()])
    log.info(f"Found {len(lake_dirs)} lake directories")

    if max_lakes:
        # Shuffle for variety, then take first N
        np.random.seed(42)
        np.random.shuffle(lake_dirs)
        lake_dirs = lake_dirs[:max_lakes]
        log.info(f"Processing {len(lake_dirs)} lakes (limited)")

    # Resume from checkpoint
    existing_lake_ids = set()
    existing_data = []
    if resume_path and os.path.exists(resume_path):
        log.info(f"Resuming from {resume_path}")
        df_resume = pd.read_parquet(resume_path)
        existing_lake_ids = set(df_resume["lake_id"].unique())
        existing_data.append(df_resume)
        log.info(f"  {len(existing_lake_ids)} lakes already processed, {len(df_resume)} points")
        lake_dirs = [d for d in lake_dirs if Path(d).name not in existing_lake_ids]
        log.info(f"  {len(lake_dirs)} remaining")
    elif checkpoint_path.exists():
        log.info(f"Found checkpoint at {checkpoint_path}")
        df_ckpt = pd.read_parquet(checkpoint_path)
        existing_lake_ids = set(df_ckpt["lake_id"].unique())
        existing_data.append(df_ckpt)
        log.info(f"  {len(existing_lake_ids)} lakes in checkpoint, {len(df_ckpt)} points")
        lake_dirs = [d for d in lake_dirs if Path(d).name not in existing_lake_ids]
        log.info(f"  {len(lake_dirs)} remaining")

    if not lake_dirs:
        log.info("All lakes already processed!")
        return pd.concat(existing_data, ignore_index=True) if existing_data else pd.DataFrame()

    # Process lakes
    all_results = list(existing_data)
    n_success = len(existing_lake_ids)
    n_fail = 0
    total_points = sum(len(d) for d in existing_data)

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    if n_threads <= 1:
        # Sequential processing with shared STAC client
        client = Client.open(E84_STAC_URL)

        for i, lake_dir in enumerate(tqdm(lake_dirs, desc="Lakes")):
            result = process_single_lake(lake_dir, client)

            if result is not None and len(result) > 0:
                all_results.append(result)
                n_success += 1
                total_points += len(result)
            else:
                n_fail += 1

            # Checkpoint every 50 lakes
            if (i + 1) % 50 == 0 and all_results:
                ckpt = pd.concat(all_results, ignore_index=True)
                ckpt.to_parquet(checkpoint_path, index=False)
                log.info(
                    f"Checkpoint: {n_success} lakes, {total_points} points, "
                    f"{n_fail} failed → {checkpoint_path}"
                )

    else:
        # Parallel processing
        # Each thread gets its own STAC client
        def _process_with_client(lake_dir):
            client = Client.open(E84_STAC_URL)
            return process_single_lake(lake_dir, client)

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = {
                executor.submit(_process_with_client, ld): ld
                for ld in lake_dirs
            }

            pbar = tqdm(total=len(lake_dirs), desc="Lakes")
            batch_count = 0

            for future in as_completed(futures):
                pbar.update(1)
                batch_count += 1

                try:
                    result = future.result(timeout=120)
                    if result is not None and len(result) > 0:
                        all_results.append(result)
                        n_success += 1
                        total_points += len(result)
                    else:
                        n_fail += 1
                except Exception:
                    n_fail += 1

                # Checkpoint every 50 lakes
                if batch_count % 50 == 0 and all_results:
                    ckpt = pd.concat(all_results, ignore_index=True)
                    ckpt.to_parquet(checkpoint_path, index=False)
                    log.info(
                        f"Checkpoint: {n_success} lakes, {total_points} pts, "
                        f"{n_fail} failed"
                    )

            pbar.close()

    # Final save
    if not all_results:
        log.error("No data collected!")
        return pd.DataFrame()

    df = pd.concat(all_results, ignore_index=True)

    # Clean inf/nan in numeric columns
    for col in df.select_dtypes(include=[np.number]).columns:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    log.info(f"\nCollection complete:")
    log.info(f"  Lakes: {n_success} success, {n_fail} failed")
    log.info(f"  Points: {len(df):,}")
    log.info(f"  Depth range: {df['depth_m'].min():.1f} - {df['depth_m'].max():.1f}m")
    log.info(f"  Depth distribution:")
    for lo, hi in DEPTH_BINS:
        n = ((df["depth_m"] >= lo) & (df["depth_m"] < hi)).sum()
        log.info(f"    {lo}-{hi}m: {n:,} ({100*n/len(df):.1f}%)")

    return df


# ── Train/Val/Test Split ──────────────────────────────────────────────

def split_by_lake(df: pd.DataFrame) -> pd.DataFrame:
    """Split by lake_id (70/15/15). No lake appears in multiple splits."""
    lake_ids = df["lake_id"].unique()
    np.random.seed(42)
    np.random.shuffle(lake_ids)

    n_train = int(len(lake_ids) * 0.70)
    n_val = int(len(lake_ids) * 0.15)

    train_lakes = set(lake_ids[:n_train])
    val_lakes = set(lake_ids[n_train:n_train + n_val])
    # test = rest

    df = df.copy()
    df["split"] = "test"
    df.loc[df["lake_id"].isin(train_lakes), "split"] = "train"
    df.loc[df["lake_id"].isin(val_lakes), "split"] = "val"

    for split in ["train", "val", "test"]:
        n_pts = (df["split"] == split).sum()
        n_lk = df.loc[df["split"] == split, "lake_id"].nunique()
        log.info(f"  {split}: {n_pts:,} points from {n_lk} lakes")

    return df


# ── Model Training ────────────────────────────────────────────────────

# SDB feature columns (from sdb_preprocessing.py)
SDB_FEATURES = [
    "blue", "green", "red", "nir",
    "log_blue", "log_green", "log_red", "log_nir",
    "stumpf_ratio",
    "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
    "ndwi", "mndwi", "ndvi",
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    "turbidity_index", "cdom_proxy", "ndti",
    "rel_blue", "rel_green", "rel_red",
    "blue_x_green", "blue_x_red", "green_x_red",
    "blue_sq", "green_sq",
    "blue_minus_green", "green_minus_red", "red_minus_nir",
    "rededge1", "rededge2", "rededge3", "nir08",
    "swir16", "swir22",
    "cdom_rededge", "fai",
]


def train_ensemble(df: pd.DataFrame, output_dir: Path):
    """
    Train ensemble: XGBoost + LightGBM + MLP.
    Evaluate with HONEST metrics on held-out test lakes.
    """
    import xgboost as xgb
    import lightgbm as lgb
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine available features
    available = [f for f in SDB_FEATURES if f in df.columns]
    log.info(f"Using {len(available)} SDB features: {available}")

    if len(available) < 5:
        log.error(f"Only {len(available)} features available. Need >= 5.")
        return

    # Handle NaN in features
    for col in available:
        df[col] = df[col].fillna(df[col].median() if df[col].notna().any() else 0)

    # Split data
    train_df = df[df["split"] == "train"]
    val_df = df[df["split"] == "val"]
    test_df = df[df["split"] == "test"]

    X_train = train_df[available].values
    y_train = train_df["depth_m"].values
    X_val = val_df[available].values
    y_val = val_df["depth_m"].values
    X_test = test_df[available].values
    y_test = test_df["depth_m"].values

    log.info(f"Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")

    # ── Mean Baseline ──
    mean_depth = y_train.mean()
    baseline_rmse = np.sqrt(np.mean((y_test - mean_depth) ** 2))
    log.info(f"\nMean baseline: predict {mean_depth:.2f}m for all → RMSE={baseline_rmse:.3f}m")

    models = {}
    predictions = {}

    # ── XGBoost ──
    log.info("\n=== Training XGBoost ===")
    xgb_model = xgb.XGBRegressor(
        n_estimators=1000,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        min_child_weight=5,
        tree_method="hist",
        device="cuda",
        random_state=42,
    )
    xgb_model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=100,
    )
    models["xgboost"] = xgb_model
    predictions["xgboost"] = xgb_model.predict(X_test)

    # ── LightGBM ──
    log.info("\n=== Training LightGBM ===")
    lgb_model = lgb.LGBMRegressor(
        n_estimators=1000,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        min_child_weight=5,
        num_leaves=127,
        random_state=42,
        verbose=-1,
    )
    lgb_model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
    )
    models["lightgbm"] = lgb_model
    predictions["lightgbm"] = lgb_model.predict(X_test)

    # ── MLP (PyTorch) ──
    log.info("\n=== Training MLP ===")
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Normalize features
        X_mean = X_train.mean(axis=0)
        X_std = X_train.std(axis=0)
        X_std[X_std < 1e-6] = 1.0

        X_train_n = (X_train - X_mean) / X_std
        X_val_n = (X_val - X_mean) / X_std
        X_test_n = (X_test - X_mean) / X_std

        # Replace NaN
        X_train_n = np.nan_to_num(X_train_n, 0)
        X_val_n = np.nan_to_num(X_val_n, 0)
        X_test_n = np.nan_to_num(X_test_n, 0)

        n_features = X_train_n.shape[1]

        class DepthMLP(nn.Module):
            def __init__(self, in_features):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(in_features, 256),
                    nn.BatchNorm1d(256),
                    nn.SiLU(),
                    nn.Dropout(0.1),
                    nn.Linear(256, 128),
                    nn.BatchNorm1d(128),
                    nn.SiLU(),
                    nn.Dropout(0.1),
                    nn.Linear(128, 64),
                    nn.BatchNorm1d(64),
                    nn.SiLU(),
                    nn.Linear(64, 1),
                    nn.Softplus(),
                )

            def forward(self, x):
                return self.net(x).squeeze(-1).clamp(0, 50)

        mlp = DepthMLP(n_features).to(device)
        optimizer = torch.optim.AdamW(mlp.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=200)
        criterion = nn.HuberLoss(delta=2.0)

        train_ds = TensorDataset(
            torch.tensor(X_train_n, dtype=torch.float32),
            torch.tensor(y_train, dtype=torch.float32),
        )
        val_ds = TensorDataset(
            torch.tensor(X_val_n, dtype=torch.float32),
            torch.tensor(y_val, dtype=torch.float32),
        )
        train_dl = DataLoader(train_ds, batch_size=1024, shuffle=True)
        val_dl = DataLoader(val_ds, batch_size=2048)

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        for epoch in range(200):
            mlp.train()
            train_loss = 0
            for xb, yb in train_dl:
                xb, yb = xb.to(device), yb.to(device)
                pred = mlp(xb)
                loss = criterion(pred, yb)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss += loss.item() * len(xb)
            train_loss /= len(train_ds)
            scheduler.step()

            # Validation
            mlp.eval()
            val_loss = 0
            with torch.no_grad():
                for xb, yb in val_dl:
                    xb, yb = xb.to(device), yb.to(device)
                    pred = mlp(xb)
                    val_loss += criterion(pred, yb).item() * len(xb)
            val_loss /= len(val_ds)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                best_state = {k: v.cpu().clone() for k, v in mlp.state_dict().items()}
            else:
                patience_counter += 1

            if (epoch + 1) % 50 == 0:
                log.info(f"  Epoch {epoch+1}: train={train_loss:.4f}, val={val_loss:.4f}")

            if patience_counter >= 30:
                log.info(f"  Early stopping at epoch {epoch+1}")
                break

        if best_state:
            mlp.load_state_dict(best_state)

        mlp.eval()
        with torch.no_grad():
            X_test_t = torch.tensor(X_test_n, dtype=torch.float32).to(device)
            mlp_pred = mlp(X_test_t).cpu().numpy()
        predictions["mlp"] = mlp_pred

        # Save MLP
        torch.save({
            "state_dict": mlp.state_dict(),
            "X_mean": X_mean,
            "X_std": X_std,
            "features": available,
        }, output_dir / "mlp_model.pt")

    except Exception as e:
        log.warning(f"MLP training failed: {e}")
        traceback.print_exc()

    # ── Ensemble (simple average) ──
    pred_stack = np.stack([predictions[k] for k in predictions])
    ensemble_pred = pred_stack.mean(axis=0)
    predictions["ensemble"] = ensemble_pred

    # ── HONEST Evaluation ──
    log.info("\n" + "=" * 70)
    log.info("HONEST EVALUATION — Test set (unseen lakes)")
    log.info("=" * 70)

    results = {}
    for name, pred in predictions.items():
        r2 = r2_score(y_test, pred)
        rmse = np.sqrt(mean_squared_error(y_test, pred))
        mae = mean_absolute_error(y_test, pred)

        results[name] = {"r2": r2, "rmse": rmse, "mae": mae}

        r2_flag = "LEARNING" if r2 > 0 else "WORSE THAN MEAN"
        log.info(f"\n  {name:12s}: R²={r2:.4f} [{r2_flag}], RMSE={rmse:.3f}m, MAE={mae:.3f}m")

        # Per-depth-bin metrics
        for lo, hi in DEPTH_BINS:
            mask = (y_test >= lo) & (y_test < hi)
            if mask.sum() < 5:
                continue
            bin_rmse = np.sqrt(np.mean((pred[mask] - y_test[mask]) ** 2))
            bin_r2 = r2_score(y_test[mask], pred[mask]) if mask.sum() > 1 else float("nan")
            log.info(f"    {lo:>2.0f}-{hi:<2.0f}m: R²={bin_r2:.3f}, RMSE={bin_rmse:.3f}m (n={mask.sum()})")

    log.info(f"\n  Mean baseline RMSE: {baseline_rmse:.3f}m (predicting {mean_depth:.2f}m)")

    # ── Save models ──
    xgb_model.save_model(str(output_dir / "xgboost_model.json"))
    lgb_model.booster_.save_model(str(output_dir / "lightgbm_model.txt"))

    # Save feature importance
    importance = pd.DataFrame({
        "feature": available,
        "xgb_importance": xgb_model.feature_importances_,
        "lgb_importance": lgb_model.feature_importances_,
    }).sort_values("xgb_importance", ascending=False)
    importance.to_csv(output_dir / "feature_importance.csv", index=False)
    log.info(f"\nTop 10 features (XGBoost):")
    for _, row in importance.head(10).iterrows():
        log.info(f"  {row['feature']:25s}: {row['xgb_importance']:.4f}")

    # Save results
    with open(output_dir / "metrics.json", "w") as f:
        json.dump({
            k: {kk: float(vv) for kk, vv in v.items()}
            for k, v in results.items()
        }, f, indent=2)

    log.info(f"\nModels saved to {output_dir}")

    return results


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Train SDB model on MN DNR sonar DEMs + Sentinel-2",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-dir", type=str, default=str(DATA_DIR),
                        help="Directory containing lake subdirectories")
    parser.add_argument("--output", type=str, default=str(OUTPUT_DIR),
                        help="Output directory for models and data")
    parser.add_argument("--max-lakes", type=int, default=None,
                        help="Limit number of lakes (for testing)")
    parser.add_argument("--threads", type=int, default=1,
                        help="Number of parallel threads")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume from checkpoint parquet")
    parser.add_argument("--skip-collection", action="store_true",
                        help="Skip data collection, use existing parquet")
    parser.add_argument("--data-parquet", type=str, default=None,
                        help="Existing collected data parquet (with --skip-collection)")

    args = parser.parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_path = output_dir / "sonar_s2_training.parquet"

    if args.skip_collection and args.data_parquet:
        log.info(f"Loading existing data from {args.data_parquet}")
        df = pd.read_parquet(args.data_parquet)
    elif args.skip_collection and data_path.exists():
        log.info(f"Loading existing data from {data_path}")
        df = pd.read_parquet(data_path)
    else:
        # Collect training data
        log.info("=" * 70)
        log.info("PHASE 1: Collecting sonar DEM + S2 spectral training data")
        log.info("=" * 70)

        df = collect_training_data(
            data_dir=Path(args.data_dir),
            max_lakes=args.max_lakes,
            n_threads=args.threads,
            checkpoint_path=output_dir / "checkpoint.parquet",
            resume_path=args.resume,
        )

        if len(df) == 0:
            log.error("No training data collected. Exiting.")
            return

        # Save collected data
        df.to_parquet(data_path, index=False)
        log.info(f"Saved {len(df):,} points to {data_path}")

    # Split by lake
    df = split_by_lake(df)

    # Save split data
    split_path = output_dir / "sonar_s2_split.parquet"
    df.to_parquet(split_path, index=False)

    # Train ensemble
    log.info("\n" + "=" * 70)
    log.info("PHASE 2: Training ensemble model")
    log.info("=" * 70)

    train_ensemble(df, output_dir / "models")


if __name__ == "__main__":
    main()
