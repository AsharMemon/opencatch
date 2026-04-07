#!/usr/bin/env python3
"""
OpenCatch — Full Bathymetry Pipeline (1,951 MN DNR Lakes)

Master orchestrator for scaling to all lakes with HONEST evaluation.

CRITICAL: All evaluation uses LAKE-LEVEL splits.
- Train/test lakes are COMPLETELY SEPARATE
- Reports both within-lake R² and cross-lake R²
- Mean baseline RMSE reported alongside every result

Execution order:
  1. Scale S2 extraction to 1,951 lakes (longest, ~17 hrs)
  2. While extracting, train terrain/morphometric on existing 181 lakes
  3. When extraction completes, run full 6-step pipeline
  4. Cross-lake generalization test (30% held-out lakes)
  5. Back up everything to B2

Usage:
    python run_full_bathymetry.py                    # Full run
    python run_full_bathymetry.py --skip-extraction  # Skip S2 if already done
    python run_full_bathymetry.py --step 3           # Run specific step
"""

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/data/full_pipeline.log"),
    ],
)
log = logging.getLogger("pipeline")

# ── Paths ─────────────────────────────────────────────────────────────

DATA_DIR = Path("/data/training/v2")
SONAR_S2_DIR = Path("/data/sonar_s2_full")
CHECKPOINT_PATH = SONAR_S2_DIR / "checkpoint.parquet"
MODELS_DIR = Path("/data/models")
OUTPUT_DIR = Path("/data/pipeline_outputs")
STEP_OUTPUT_DIR = Path("/root/ml/bathymetry/step_outputs")

# B2 credentials
B2_BUCKET = "opencatch-data"
B2_KEY_ID = "004b6da11e9f7ad0000000004"
B2_APP_KEY = "K004pK63g6FSh2nPyxRw76B7ZmYgcrI"


def ensure_dirs():
    for d in [SONAR_S2_DIR, MODELS_DIR, OUTPUT_DIR, STEP_OUTPUT_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ── TASK 1: Scale S2 Extraction to 1,951 Lakes ───────────────────────

def run_extraction(max_lakes=1951, threads=2, resume=True):
    """
    Extract sonar DEM + S2 spectral data for all lakes.
    Uses train_sonar_spectral.py with checkpointing every 50 lakes.
    """
    log.info("=" * 70)
    log.info(f"TASK 1: S2 Extraction for {max_lakes} lakes")
    log.info("=" * 70)

    cmd = [
        sys.executable, "/root/ml/bathymetry/train_sonar_spectral.py",
        "--data-dir", str(DATA_DIR),
        "--output", str(SONAR_S2_DIR),
        "--max-lakes", str(max_lakes),
        "--threads", str(threads),
    ]

    # Resume from existing checkpoint
    if resume and CHECKPOINT_PATH.exists():
        cmd.extend(["--resume", str(CHECKPOINT_PATH)])
        try:
            df = pd.read_parquet(CHECKPOINT_PATH)
            log.info(f"  Resuming: {df['lake_id'].nunique()} lakes already done")
        except Exception:
            pass

    log.info(f"  Command: {' '.join(cmd)}")
    start = time.time()

    result = subprocess.run(cmd, timeout=86400)  # 24hr timeout

    elapsed = time.time() - start
    log.info(f"  Extraction completed in {elapsed/3600:.1f} hours (exit {result.returncode})")

    if CHECKPOINT_PATH.exists():
        df = pd.read_parquet(CHECKPOINT_PATH)
        log.info(f"  Result: {len(df):,} points from {df['lake_id'].nunique()} lakes")
        return True
    return result.returncode == 0


# ── TASK 2: Run 6-Step Pipeline on Full Data ─────────────────────────

def run_6step_pipeline(data_path=None):
    """
    Run all 6 steps of the reviewer pipeline on the full dataset.
    Steps use lake-level splits throughout.
    """
    log.info("=" * 70)
    log.info("TASK 2: Full 6-Step Pipeline")
    log.info("=" * 70)

    # Determine data source
    if data_path is None:
        full_path = SONAR_S2_DIR / "sonar_s2_training.parquet"
        ckpt_path = SONAR_S2_DIR / "checkpoint.parquet"
        if full_path.exists():
            data_path = str(full_path)
        elif ckpt_path.exists():
            data_path = str(ckpt_path)
        else:
            log.error("No training data found! Run extraction first.")
            return False

    log.info(f"  Using data: {data_path}")

    # Set environment variable so step scripts can find data
    os.environ["SONAR_DATA_PATH"] = data_path

    # Run existing 6-step pipeline
    cmd = [sys.executable, "/root/ml/bathymetry/run_6step_pipeline.py"]
    result = subprocess.run(cmd, timeout=14400)  # 4hr timeout
    return result.returncode == 0


# ── TASK 3: Terrain + Morphometric Backbone ──────────────────────────

def run_terrain_morphometric():
    """
    Train terrain and morphometric models for cross-lake generalization.
    These work for ALL lakes regardless of water clarity.
    Uses lake-level train/test split.
    """
    log.info("=" * 70)
    log.info("TASK 3: Terrain + Morphometric Backbone")
    log.info("=" * 70)

    output = MODELS_DIR / "terrain_morphometric"
    output.mkdir(parents=True, exist_ok=True)

    # Load whatever data we have
    data_path = None
    for candidate in [
        SONAR_S2_DIR / "sonar_s2_training.parquet",
        SONAR_S2_DIR / "checkpoint.parquet",
        Path("/data/sonar_s2/checkpoint.parquet"),
        Path("/data/sonar_s2/preprocessed_v2.parquet"),
    ]:
        if candidate.exists():
            data_path = candidate
            break

    if data_path is None:
        log.error("No training data available for morphometric model")
        return False

    df = pd.read_parquet(data_path)
    log.info(f"  Loaded {len(df):,} points from {df['lake_id'].nunique()} lakes")

    # ── Compute morphometric features from lake directories ──
    log.info("  Computing morphometric features from sonar DEMs...")
    morpho_features = compute_morphometric_features(df)

    if morpho_features is not None and len(morpho_features) > 0:
        train_morphometric_model(morpho_features, output)
    else:
        log.warning("  Could not compute morphometric features, skipping")

    return True


def compute_morphometric_features(df):
    """
    Compute lake-level morphometric features from the sonar DEM files.
    Returns DataFrame with one row per lake + morphometric features.
    """
    import rasterio

    lake_ids = df["lake_id"].unique()
    log.info(f"  Computing morphometrics for {len(lake_ids)} lakes...")

    records = []
    for lid in lake_ids:
        depth_path = DATA_DIR / lid / "depth.tif"
        if not depth_path.exists():
            continue

        try:
            with rasterio.open(str(depth_path)) as ds:
                depth = ds.read(1)
                pixel_size = abs(ds.transform.a)

            # Lake mask (where depth > 0.5m)
            lake_mask = depth > 0.5
            n_water_px = lake_mask.sum()
            if n_water_px < 10:
                continue

            # Area in m²
            area_m2 = n_water_px * pixel_size ** 2

            # Perimeter: count edge pixels (water pixels adjacent to non-water)
            from scipy import ndimage
            eroded = ndimage.binary_erosion(lake_mask)
            edge_pixels = lake_mask.astype(int) - eroded.astype(int)
            perimeter_m = edge_pixels.sum() * pixel_size

            # Depth statistics
            valid_depths = depth[lake_mask]
            max_depth = float(np.max(valid_depths))
            mean_depth = float(np.mean(valid_depths))
            median_depth = float(np.median(valid_depths))
            depth_std = float(np.std(valid_depths))

            # Shape metrics
            area_km2 = area_m2 / 1e6
            perimeter_km = perimeter_m / 1000
            circularity = 4 * np.pi * area_m2 / (perimeter_m ** 2 + 1e-8)
            sdf = perimeter_km / (2 * np.sqrt(np.pi * area_km2) + 1e-8)  # Shoreline dev factor

            # Volume development factor
            vol_dev = 3 * mean_depth / (max_depth + 1e-8)

            # Slope from DEM edges
            shore_depths = valid_depths[edge_pixels[lake_mask] > 0] if edge_pixels[lake_mask].sum() > 0 else valid_depths[:10]
            # Approximate shore slope from depth gradient
            dy, dx = np.gradient(depth * lake_mask, pixel_size)
            slope = np.sqrt(dx**2 + dy**2)
            shore_slope = float(np.mean(slope[lake_mask])) if lake_mask.any() else 0

            # Get lat/lon from lake's data
            lake_df = df[df["lake_id"] == lid]
            lat = float(lake_df["lat"].mean())
            lon = float(lake_df["lon"].mean())

            records.append({
                "lake_id": lid,
                "area_km2": area_km2,
                "perimeter_km": perimeter_km,
                "max_depth_m": max_depth,
                "mean_depth_m": mean_depth,
                "median_depth_m": median_depth,
                "depth_std_m": depth_std,
                "circularity": circularity,
                "sdf": sdf,
                "vol_dev": vol_dev,
                "shore_slope": shore_slope,
                "lat": lat,
                "lon": lon,
                "n_water_pixels": int(n_water_px),
            })

        except Exception as e:
            log.debug(f"  Morphometric failed for {lid}: {e}")
            continue

    if not records:
        return None

    morpho_df = pd.DataFrame(records)
    log.info(f"  Computed morphometrics for {len(morpho_df)} lakes")
    return morpho_df


def train_morphometric_model(morpho_df, output_dir):
    """
    Train morphometric models with LAKE-LEVEL splits.
    Target: max_depth_m prediction from morphometric features only.
    """
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    import xgboost as xgb

    log.info("  Training morphometric depth model...")
    output_dir.mkdir(parents=True, exist_ok=True)

    features = [
        "area_km2", "perimeter_km", "circularity", "sdf", "vol_dev",
        "shore_slope", "lat", "lon", "n_water_pixels",
    ]

    # Check available features
    available = [f for f in features if f in morpho_df.columns]
    if len(available) < 3:
        log.error("  Too few morphometric features available")
        return

    # Lake-level split (70/30)
    lake_ids = morpho_df["lake_id"].unique()
    np.random.seed(42)
    np.random.shuffle(lake_ids)

    n_train = int(len(lake_ids) * 0.7)
    train_lakes = set(lake_ids[:n_train])
    test_lakes = set(lake_ids[n_train:])

    train_df = morpho_df[morpho_df["lake_id"].isin(train_lakes)]
    test_df = morpho_df[morpho_df["lake_id"].isin(test_lakes)]

    log.info(f"  Train: {len(train_df)} lakes, Test: {len(test_df)} lakes")

    X_train = train_df[available].values
    y_train = train_df["max_depth_m"].values
    X_test = test_df[available].values
    y_test = test_df["max_depth_m"].values

    # Mean baseline
    mean_depth = y_train.mean()
    baseline_rmse = np.sqrt(np.mean((y_test - mean_depth) ** 2))
    log.info(f"  Mean baseline: predict {mean_depth:.2f}m -> RMSE={baseline_rmse:.3f}m")

    # XGBoost
    model = xgb.XGBRegressor(
        n_estimators=500,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=3,
        tree_method="hist",
        random_state=42,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=50)

    pred = model.predict(X_test)
    r2 = r2_score(y_test, pred)
    rmse = np.sqrt(mean_squared_error(y_test, pred))
    mae = mean_absolute_error(y_test, pred)

    r2_flag = "LEARNING" if r2 > 0 else "WORSE THAN MEAN"
    log.info(f"\n  MORPHOMETRIC MODEL (cross-lake):")
    log.info(f"    R²={r2:.4f} [{r2_flag}], RMSE={rmse:.3f}m, MAE={mae:.3f}m")
    log.info(f"    Baseline RMSE={baseline_rmse:.3f}m")
    log.info(f"    Improvement over baseline: {(1 - rmse/baseline_rmse)*100:.1f}%")

    # Save
    model.save_model(str(output_dir / "morphometric_xgb.json"))
    results = {
        "cross_lake_r2": float(r2),
        "cross_lake_rmse": float(rmse),
        "cross_lake_mae": float(mae),
        "baseline_rmse": float(baseline_rmse),
        "n_train_lakes": len(train_df),
        "n_test_lakes": len(test_df),
        "features": available,
    }
    with open(output_dir / "morphometric_results.json", "w") as f:
        json.dump(results, f, indent=2)

    log.info(f"  Saved to {output_dir}")
    return results


# ── TASK 4: Multi-Temporal Compositing ────────────────────────────────

def run_multitemporal_compositing():
    """
    For each lake, find 10-20 S2 scenes from summer 2023,
    take median per pixel across scenes to reduce noise.
    """
    log.info("=" * 70)
    log.info("TASK 4: Multi-Temporal Compositing")
    log.info("=" * 70)

    try:
        import rasterio
        from pystac_client import Client
        from pyproj import Transformer
    except ImportError as e:
        log.error(f"  Missing dependency: {e}")
        return False

    client = Client.open("https://earth-search.aws.element84.com/v1")

    # Get list of lakes we've already extracted
    ckpt_path = None
    for p in [SONAR_S2_DIR / "checkpoint.parquet", Path("/data/sonar_s2/checkpoint.parquet")]:
        if p.exists():
            ckpt_path = p
            break

    if ckpt_path is None:
        log.error("  No extracted data to composite from")
        return False

    df = pd.read_parquet(ckpt_path)
    lake_ids = df["lake_id"].unique()
    log.info(f"  Multi-temporal compositing for {len(lake_ids)} lakes")

    # For each lake, find multiple S2 scenes and take band-wise medians
    BANDS = ["blue", "green", "red", "nir", "rededge1", "rededge2", "rededge3",
             "nir08", "swir16", "swir22"]

    composite_results = []
    n_improved = 0

    for i, lid in enumerate(lake_ids):
        if (i + 1) % 50 == 0:
            log.info(f"  Compositing {i+1}/{len(lake_ids)} ({n_improved} improved)")

        depth_path = DATA_DIR / lid / "depth.tif"
        if not depth_path.exists():
            continue

        try:
            # Get lake bbox
            with rasterio.open(str(depth_path)) as ds:
                bounds = ds.bounds
                src_crs = ds.crs

            transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
            west, south = transformer.transform(bounds.left, bounds.bottom)
            east, north = transformer.transform(bounds.right, bounds.top)
            bbox = (west, south, east, north)

            # Search for multiple scenes (summer 2023, low cloud)
            search = client.search(
                collections=["sentinel-2-l2a"],
                bbox=bbox,
                datetime="2023-06-01/2023-09-30",
                query={"eo:cloud_cover": {"lt": 15}},
                max_items=20,
            )
            items = list(search.items())

            if len(items) < 3:
                continue  # Not enough scenes to composite

            # Get lake's existing points
            lake_df = df[df["lake_id"] == lid].copy()

            # For each band, gather values across scenes and take median
            band_stacks = {b: [] for b in BANDS}
            valid_scenes = 0

            for item in items[:15]:  # Cap at 15 scenes
                try:
                    # Extract bands at lake points from this scene
                    scene_bands = {}
                    for band in BANDS:
                        if band not in item.assets:
                            break
                        href = item.assets[band].href
                        with rasterio.open(href) as src:
                            s2_crs = src.crs
                            tr = Transformer.from_crs("EPSG:4326", s2_crs, always_xy=True)
                            xs, ys = tr.transform(lake_df["lon"].values, lake_df["lat"].values)

                            vals = []
                            for x, y in zip(xs, ys):
                                try:
                                    py, px = src.index(x, y)
                                    window = rasterio.windows.Window(max(0, px-1), max(0, py-1), 3, 3)
                                    data = src.read(1, window=window)
                                    val = float(np.nanmean(data[data > 0])) if (data > 0).any() else np.nan
                                except Exception:
                                    val = np.nan
                                vals.append(val)
                            scene_bands[band] = vals
                    else:
                        # All bands extracted
                        for b, vals in scene_bands.items():
                            band_stacks[b].append(vals)
                        valid_scenes += 1
                except Exception:
                    continue

            if valid_scenes < 3:
                continue

            # Compute per-point median across scenes
            for b in BANDS:
                if band_stacks[b] and b in lake_df.columns:
                    stack = np.array(band_stacks[b])  # (n_scenes, n_points)
                    medians = np.nanmedian(stack, axis=0)
                    lake_df[b] = medians

            lake_df["n_composite_scenes"] = valid_scenes
            composite_results.append(lake_df)
            n_improved += 1

        except Exception as e:
            log.debug(f"  Composite failed for {lid}: {e}")
            continue

    if composite_results:
        composite_df = pd.concat(composite_results, ignore_index=True)
        out_path = SONAR_S2_DIR / "multitemporal_composite.parquet"
        composite_df.to_parquet(out_path, index=False)
        log.info(f"\n  Multi-temporal compositing complete:")
        log.info(f"    {n_improved} lakes improved from multiple scenes")
        log.info(f"    {len(composite_df):,} points saved to {out_path}")
        return True

    log.warning("  No lakes had enough scenes for compositing")
    return False


# ── TASK 5: Cross-Lake Generalization Test ────────────────────────────

def run_cross_lake_test():
    """
    THE REAL TEST: predict depth for lakes we've NEVER seen.

    Hold out 30% of lakes completely. Train everything on 70%.
    Predict held-out lakes using spectral, morphometric, and ensemble.
    Report HONEST cross-lake R² and RMSE.
    """
    log.info("=" * 70)
    log.info("TASK 5: Cross-Lake Generalization Test")
    log.info("=" * 70)

    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    import xgboost as xgb
    import lightgbm as lgb

    # Load best available data
    data_path = None
    for candidate in [
        SONAR_S2_DIR / "multitemporal_composite.parquet",
        SONAR_S2_DIR / "sonar_s2_training.parquet",
        SONAR_S2_DIR / "checkpoint.parquet",
        Path("/data/sonar_s2/preprocessed_v2.parquet"),
        Path("/data/sonar_s2/checkpoint.parquet"),
    ]:
        if candidate.exists():
            data_path = candidate
            break

    if data_path is None:
        log.error("  No training data for cross-lake test!")
        return False

    df = pd.read_parquet(data_path)
    log.info(f"  Loaded {len(df):,} points from {df['lake_id'].nunique()} lakes")

    # ── LAKE-LEVEL split: 70% train, 30% test ──
    lake_ids = df["lake_id"].unique()
    np.random.seed(42)
    np.random.shuffle(lake_ids)

    n_train = int(len(lake_ids) * 0.70)
    train_lake_set = set(lake_ids[:n_train])
    test_lake_set = set(lake_ids[n_train:])

    train_df = df[df["lake_id"].isin(train_lake_set)].copy()
    test_df = df[df["lake_id"].isin(test_lake_set)].copy()

    log.info(f"  LAKE-LEVEL SPLIT:")
    log.info(f"    Train: {len(train_df):,} points from {train_df['lake_id'].nunique()} lakes")
    log.info(f"    Test:  {len(test_df):,} points from {test_df['lake_id'].nunique()} lakes")
    log.info(f"    ZERO overlap between train and test lakes")

    # Verify no leakage
    overlap = train_lake_set & test_lake_set
    assert len(overlap) == 0, f"LEAKAGE DETECTED: {len(overlap)} lakes in both splits!"

    # Determine spectral features
    SDB_FEATURES = [
        "blue", "green", "red", "nir",
        "log_blue", "log_green", "log_red", "log_nir",
        "stumpf_ratio", "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
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

    available = [f for f in SDB_FEATURES if f in df.columns]
    log.info(f"  Using {len(available)} spectral features")

    if len(available) < 5:
        log.error("  Too few features available")
        return False

    # Handle NaN
    for col in available:
        med = train_df[col].median()
        train_df[col] = train_df[col].fillna(med if pd.notna(med) else 0)
        test_df[col] = test_df[col].fillna(med if pd.notna(med) else 0)

    X_train = train_df[available].values
    y_train = train_df["depth_m"].values
    X_test = test_df[available].values
    y_test = test_df["depth_m"].values

    # ── Mean Baseline ──
    mean_depth = y_train.mean()
    baseline_rmse = np.sqrt(np.mean((y_test - mean_depth) ** 2))
    baseline_r2 = r2_score(y_test, np.full_like(y_test, mean_depth))
    log.info(f"\n  MEAN BASELINE: predict {mean_depth:.2f}m for all")
    log.info(f"    RMSE={baseline_rmse:.3f}m, R²={baseline_r2:.4f}")

    results = {}
    predictions = {}

    # ── XGBoost ──
    log.info("\n  --- XGBoost (cross-lake) ---")
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
    xgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=100)
    xgb_pred = xgb_model.predict(X_test)
    predictions["xgboost"] = xgb_pred

    # ── LightGBM ──
    log.info("\n  --- LightGBM (cross-lake) ---")
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
    lgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
    lgb_pred = lgb_model.predict(X_test)
    predictions["lightgbm"] = lgb_pred

    # ── MLP ──
    log.info("\n  --- MLP (cross-lake) ---")
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        device = "cuda" if torch.cuda.is_available() else "cpu"

        X_mean = X_train.mean(axis=0)
        X_std = X_train.std(axis=0)
        X_std[X_std < 1e-6] = 1.0
        X_train_n = np.nan_to_num((X_train - X_mean) / X_std, 0)
        X_test_n = np.nan_to_num((X_test - X_mean) / X_std, 0)

        n_feat = X_train_n.shape[1]

        class DepthMLP(nn.Module):
            def __init__(self, in_f):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(in_f, 256), nn.BatchNorm1d(256), nn.SiLU(), nn.Dropout(0.1),
                    nn.Linear(256, 128), nn.BatchNorm1d(128), nn.SiLU(), nn.Dropout(0.1),
                    nn.Linear(128, 64), nn.BatchNorm1d(64), nn.SiLU(),
                    nn.Linear(64, 1), nn.Softplus(),
                )
            def forward(self, x):
                return self.net(x).squeeze(-1).clamp(0, 50)

        mlp = DepthMLP(n_feat).to(device)
        optimizer = torch.optim.AdamW(mlp.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=200)
        criterion = nn.HuberLoss(delta=2.0)

        train_ds = TensorDataset(
            torch.tensor(X_train_n, dtype=torch.float32),
            torch.tensor(y_train, dtype=torch.float32),
        )
        train_dl = DataLoader(train_ds, batch_size=1024, shuffle=True)

        best_state = None
        best_loss = float("inf")

        for epoch in range(200):
            mlp.train()
            total_loss = 0
            for xb, yb in train_dl:
                xb, yb = xb.to(device), yb.to(device)
                pred = mlp(xb)
                loss = criterion(pred, yb)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total_loss += loss.item() * len(xb)
            total_loss /= len(train_ds)
            scheduler.step()

            if total_loss < best_loss:
                best_loss = total_loss
                best_state = {k: v.cpu().clone() for k, v in mlp.state_dict().items()}

            if (epoch + 1) % 50 == 0:
                log.info(f"    Epoch {epoch+1}: loss={total_loss:.4f}")

        if best_state:
            mlp.load_state_dict(best_state)
        mlp.eval()
        with torch.no_grad():
            mlp_pred = mlp(torch.tensor(X_test_n, dtype=torch.float32).to(device)).cpu().numpy()
        predictions["mlp"] = mlp_pred

    except Exception as e:
        log.warning(f"  MLP failed: {e}")
        traceback.print_exc()

    # ── Ensemble (average) ──
    pred_stack = np.stack([predictions[k] for k in predictions])
    ensemble_pred = pred_stack.mean(axis=0)
    predictions["ensemble"] = ensemble_pred

    # ── REPORT ──
    log.info("\n" + "=" * 70)
    log.info("CROSS-LAKE GENERALIZATION RESULTS (HONEST)")
    log.info("=" * 70)
    log.info(f"Mean baseline RMSE: {baseline_rmse:.3f}m")
    log.info("")

    DEPTH_BINS = [(0.5, 2), (2, 5), (5, 10), (10, 20), (20, 50)]

    for name, pred in predictions.items():
        r2 = r2_score(y_test, pred)
        rmse = np.sqrt(mean_squared_error(y_test, pred))
        mae = mean_absolute_error(y_test, pred)

        r2_flag = "LEARNING" if r2 > 0 else "WORSE THAN MEAN"
        improvement = (1 - rmse / baseline_rmse) * 100

        results[name] = {
            "cross_lake_r2": float(r2),
            "cross_lake_rmse": float(rmse),
            "cross_lake_mae": float(mae),
            "baseline_rmse": float(baseline_rmse),
            "improvement_pct": float(improvement),
        }

        log.info(f"  {name:12s}: R²={r2:.4f} [{r2_flag}], RMSE={rmse:.3f}m, MAE={mae:.3f}m")
        log.info(f"  {'':12s}  Improvement over baseline: {improvement:.1f}%")

        # Per-depth-bin
        for lo, hi in DEPTH_BINS:
            mask = (y_test >= lo) & (y_test < hi)
            if mask.sum() < 5:
                continue
            bin_rmse = np.sqrt(np.mean((pred[mask] - y_test[mask]) ** 2))
            bin_r2 = r2_score(y_test[mask], pred[mask]) if mask.sum() > 1 else float("nan")
            log.info(f"    {lo:>2.0f}-{hi:<2.0f}m: R²={bin_r2:.3f}, RMSE={bin_rmse:.3f}m (n={mask.sum()})")

        log.info("")

    # ── Per-lake analysis ──
    log.info("\n  Per-lake cross-lake R² distribution:")
    lake_r2s = []
    for lid in test_lake_set:
        mask = test_df["lake_id"] == lid
        if mask.sum() < 5:
            continue
        y_lake = test_df.loc[mask, "depth_m"].values
        pred_lake = ensemble_pred[mask.values]
        r2_lake = r2_score(y_lake, pred_lake)
        lake_r2s.append(r2_lake)

    lake_r2s = np.array(lake_r2s)
    if len(lake_r2s) > 0:
        log.info(f"    Lakes evaluated: {len(lake_r2s)}")
        log.info(f"    Median per-lake R²: {np.median(lake_r2s):.4f}")
        log.info(f"    Mean per-lake R²:   {np.mean(lake_r2s):.4f}")
        log.info(f"    R² > 0.5:           {(lake_r2s > 0.5).sum()}/{len(lake_r2s)} ({100*(lake_r2s > 0.5).mean():.0f}%)")
        log.info(f"    R² > 0.0:           {(lake_r2s > 0.0).sum()}/{len(lake_r2s)} ({100*(lake_r2s > 0.0).mean():.0f}%)")
        results["per_lake_stats"] = {
            "n_lakes": len(lake_r2s),
            "median_r2": float(np.median(lake_r2s)),
            "mean_r2": float(np.mean(lake_r2s)),
            "pct_r2_gt_0.5": float((lake_r2s > 0.5).mean()),
            "pct_r2_gt_0.0": float((lake_r2s > 0.0).mean()),
        }

    # Save results
    out_path = OUTPUT_DIR / "cross_lake_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"\n  Results saved to {out_path}")

    # Save models
    xgb_model.save_model(str(MODELS_DIR / "cross_lake_xgb.json"))
    lgb_model.booster_.save_model(str(MODELS_DIR / "cross_lake_lgb.txt"))

    return True


# ── TASK 6: Back Up to B2 ────────────────────────────────────────────

def backup_to_b2():
    """Upload all outputs to Backblaze B2."""
    log.info("=" * 70)
    log.info("TASK 6: Backup to B2")
    log.info("=" * 70)

    # Install b2 CLI if needed
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "b2"], capture_output=True)

    # Authorize
    auth_cmd = [
        sys.executable, "-m", "b2", "authorize-account",
        B2_KEY_ID, B2_APP_KEY,
    ]
    result = subprocess.run(auth_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Try b2 v4 CLI
        auth_cmd = [
            sys.executable, "-m", "b2", "account", "authorize",
            B2_KEY_ID, B2_APP_KEY,
        ]
        result = subprocess.run(auth_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"  B2 auth failed: {result.stderr}")
            return False

    log.info("  B2 authorized")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Files to upload
    upload_targets = [
        (SONAR_S2_DIR / "checkpoint.parquet", f"bathymetry/full_1951/{timestamp}/checkpoint.parquet"),
        (SONAR_S2_DIR / "sonar_s2_training.parquet", f"bathymetry/full_1951/{timestamp}/sonar_s2_training.parquet"),
        (SONAR_S2_DIR / "multitemporal_composite.parquet", f"bathymetry/full_1951/{timestamp}/multitemporal_composite.parquet"),
        (OUTPUT_DIR / "cross_lake_results.json", f"bathymetry/full_1951/{timestamp}/cross_lake_results.json"),
        (MODELS_DIR / "cross_lake_xgb.json", f"bathymetry/full_1951/{timestamp}/cross_lake_xgb.json"),
        (MODELS_DIR / "cross_lake_lgb.txt", f"bathymetry/full_1951/{timestamp}/cross_lake_lgb.txt"),
        (MODELS_DIR / "terrain_morphometric/morphometric_xgb.json", f"bathymetry/full_1951/{timestamp}/morphometric_xgb.json"),
        (MODELS_DIR / "terrain_morphometric/morphometric_results.json", f"bathymetry/full_1951/{timestamp}/morphometric_results.json"),
        (Path("/data/full_pipeline.log"), f"bathymetry/full_1951/{timestamp}/pipeline.log"),
    ]

    n_uploaded = 0
    for local_path, b2_key in upload_targets:
        if not local_path.exists():
            log.info(f"  Skip (not found): {local_path}")
            continue

        cmd = [
            sys.executable, "-m", "b2", "upload-file",
            B2_BUCKET, str(local_path), b2_key,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            # Try v4 syntax
            cmd = [
                sys.executable, "-m", "b2", "file", "upload",
                B2_BUCKET, str(local_path), b2_key,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0:
            log.info(f"  Uploaded: {local_path.name} -> {b2_key}")
            n_uploaded += 1
        else:
            log.warning(f"  Upload failed for {local_path.name}: {result.stderr[:200]}")

    log.info(f"\n  B2 backup: {n_uploaded} files uploaded")
    return n_uploaded > 0


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch Full Bathymetry Pipeline (1,951 lakes)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--skip-extraction", action="store_true",
                        help="Skip S2 extraction (use existing data)")
    parser.add_argument("--step", type=int, default=None,
                        help="Run only this step (1-6)")
    parser.add_argument("--max-lakes", type=int, default=1951,
                        help="Max lakes for extraction")
    parser.add_argument("--threads", type=int, default=2,
                        help="Extraction threads")
    args = parser.parse_args()

    ensure_dirs()

    pipeline_start = time.time()
    step_results = {}

    log.info("=" * 70)
    log.info("OpenCatch Full Bathymetry Pipeline")
    log.info(f"Target: {args.max_lakes} MN DNR lakes")
    log.info(f"Started: {datetime.datetime.now().isoformat()}")
    log.info("=" * 70)

    steps = {
        1: ("S2 Extraction", lambda: run_extraction(args.max_lakes, args.threads)),
        2: ("6-Step Pipeline", run_6step_pipeline),
        3: ("Terrain + Morphometric", run_terrain_morphometric),
        4: ("Multi-Temporal Compositing", run_multitemporal_compositing),
        5: ("Cross-Lake Generalization", run_cross_lake_test),
        6: ("B2 Backup", backup_to_b2),
    }

    if args.step:
        steps_to_run = [args.step]
    elif args.skip_extraction:
        steps_to_run = [3, 2, 4, 5, 6]  # Run morphometric first, then pipeline, then rest
    else:
        # Full execution order per instructions:
        # 1. Start extraction (longest)
        # But morphometric can run on existing data while extraction happens
        # So: 3 first (fast), then 1 (long), then 2, 4, 5, 6
        steps_to_run = [3, 1, 2, 4, 5, 6]

    for step_num in steps_to_run:
        name, func = steps[step_num]
        log.info(f"\n{'#' * 70}")
        log.info(f"# Starting Step {step_num}: {name}")
        log.info(f"{'#' * 70}")

        start = time.time()
        try:
            success = func()
            elapsed = time.time() - start
            step_results[step_num] = success
            log.info(f"\n  Step {step_num} ({name}): {'PASS' if success else 'FAIL'} ({elapsed/60:.1f} min)")

            # Backup after major steps
            if step_num in [1, 5] and success:
                log.info("  Running intermediate B2 backup...")
                backup_to_b2()

        except Exception as e:
            elapsed = time.time() - start
            step_results[step_num] = False
            log.error(f"\n  Step {step_num} ({name}): EXCEPTION ({elapsed/60:.1f} min)")
            log.error(f"    {e}")
            traceback.print_exc()
            # Continue to next step rather than halt
            continue

    # ── Final Summary ──
    total_time = time.time() - pipeline_start
    log.info("\n" + "=" * 70)
    log.info("PIPELINE COMPLETE")
    log.info("=" * 70)
    for step_num, success in sorted(step_results.items()):
        name = steps[step_num][0]
        status = "PASS" if success else "FAIL"
        log.info(f"  Step {step_num} ({name:30s}): {status}")
    log.info(f"\nTotal time: {total_time/3600:.2f} hours")


if __name__ == "__main__":
    main()
