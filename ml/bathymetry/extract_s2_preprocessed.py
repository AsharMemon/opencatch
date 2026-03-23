#!/usr/bin/env python3
"""
OpenCatch — Preprocessed Sentinel-2 Feature Extractor for SDB

Same bbox-based STAC approach as build_icesat2_training_set.py (~0.1 pts/sec),
but applies the full SDB preprocessing pipeline before saving:

  1. DN -> reflectance conversion
  2. Sun glint removal (Hedley et al. 2005)
  3. Quality filtering (cloud, shadow, saturation, land, turbidity)
  4. Physics-based feature computation (~35 features)
  5. Temporal match scoring (S2 date vs ICESat-2 date)

Input: Existing ICESat-2 depth points (parquet with lat, lon, depth_m, + metadata)
Output: Preprocessed parquet with ~35 SDB features per point

Usage:
    # Process existing ICESat-2 points from cache
    python extract_s2_preprocessed.py \
        --input /data/icesat2_cache \
        --output /data/sdb_preprocessed.parquet

    # Process a specific parquet file
    python extract_s2_preprocessed.py \
        --input /data/icesat2_s2_training.parquet \
        --output /data/sdb_preprocessed.parquet

    # Limit for testing
    python extract_s2_preprocessed.py \
        --input /data/icesat2_cache \
        --output /data/sdb_test.parquet \
        --max-points 1000

Requirements:
    pip install pystac-client rasterio numpy pandas pyarrow tqdm geopandas
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

from sdb_preprocessing import SDBPreprocessor, EXTENDED_SDB_FEATURES, get_feature_list

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract_s2_preprocessed")

# Element84 Earth Search — free, no auth, hosts S2 L2A COGs
E84_STAC_URL = "https://earth-search.aws.element84.com/v1"

# Sentinel-2 band names in Element84 STAC
S2_BANDS = [
    "blue", "green", "red",
    "rededge1", "rededge2", "rededge3",
    "nir", "nir08",
    "swir16", "swir22",
]

# Additional assets to extract for preprocessing
S2_EXTRA_ASSETS = ["scl"]  # Scene Classification Layer


def load_icesat2_points(input_path: str, max_points: Optional[int] = None) -> pd.DataFrame:
    """
    Load ICESat-2 depth points from parquet file(s).

    Supports:
    - Single parquet file
    - Directory of cached regional parquets (from build_icesat2_training_set.py)
    """
    input_path = Path(input_path)

    if input_path.is_file():
        df = pd.read_parquet(input_path)
        log.info(f"Loaded {len(df):,} points from {input_path}")
    elif input_path.is_dir():
        parquets = sorted(input_path.glob("icesat2_*.parquet"))
        if not parquets:
            parquets = sorted(input_path.glob("*.parquet"))
        if not parquets:
            raise FileNotFoundError(f"No parquet files in {input_path}")

        dfs = []
        for p in parquets:
            try:
                chunk = pd.read_parquet(p)
                # Filter each file BEFORE concatenation to avoid OOM
                # (raw ICESat-2 caches can be 10M+ rows per file)
                if "depth_m" in chunk.columns:
                    chunk = chunk[chunk["depth_m"].between(0.1, 50.0)]
                # Keep only needed columns to save memory
                keep_cols = [c for c in ["lat", "lon", "depth_m", "quality",
                             "lake_id", "region", "time", "h_mean", "h_sigma",
                             "geometry"] if c in chunk.columns]
                if keep_cols:
                    chunk = chunk[keep_cols]
                dfs.append(chunk)
                log.info(f"  {p.name}: {len(chunk):,} points (after depth filter)")
            except Exception as e:
                log.warning(f"  Failed to load {p.name}: {e}")

        df = pd.concat(dfs, ignore_index=True)
        log.info(f"Loaded {len(df):,} total points from {len(parquets)} files")
    else:
        raise FileNotFoundError(f"{input_path} not found")

    # Ensure required columns
    required = ["lat", "lon", "depth_m"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Remove invalid depths
    df = df[df["depth_m"].between(0.1, 50.0)].reset_index(drop=True)

    if max_points and len(df) > max_points:
        df = df.sample(n=max_points, random_state=42).reset_index(drop=True)
        log.info(f"Subsampled to {len(df):,} points")

    return df


def get_stac_client():
    """Get Element84 Earth Search STAC client."""
    from pystac_client import Client
    return Client.open(E84_STAC_URL)


def find_best_s2_scene(
    client,
    bbox: tuple,
    target_date: Optional[str] = None,
    max_cloud: float = 15.0,
    date_range: str = "2019-05-01/2025-09-30",
):
    """
    Find the best (least-cloudy, summer) S2 scene for a bbox.

    If target_date is provided (ICESat-2 overpass date), searches +/- 90 days
    around that date first, then falls back to any summer scene.

    Returns:
        (stac_item, scene_date) or (None, None)
    """
    # If we have a target date, search near it first
    if target_date:
        try:
            dt = pd.to_datetime(target_date)
            start = (dt - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
            end = (dt + pd.Timedelta(days=90)).strftime("%Y-%m-%d")
            narrow_range = f"{start}/{end}"

            search = client.search(
                collections=["sentinel-2-l2a"],
                bbox=bbox,
                datetime=narrow_range,
                query={"eo:cloud_cover": {"lt": max_cloud}},
                sortby=["+properties.eo:cloud_cover"],
                max_items=5,
            )
            items = list(search.items())
            if items:
                scene_date = items[0].properties.get("datetime", "")[:10]
                return items[0], scene_date
        except Exception:
            pass

    # Fallback: any low-cloud scene in the full date range
    try:
        search = client.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            sortby=["+properties.eo:cloud_cover"],
            max_items=5,
        )
        items = list(search.items())
        if items:
            scene_date = items[0].properties.get("datetime", "")[:10]
            return items[0], scene_date
    except Exception as e:
        log.debug(f"STAC search failed: {e}")

    return None, None


def extract_pixel_values(item, lon: float, lat: float, window_size: int = 3):
    """
    Extract raw S2 pixel values at a point location.

    Uses a small window (default 3x3) and takes the mean for robustness
    against single-pixel noise.

    Returns:
        Dict of band_name -> raw_DN_value, or None if extraction fails.
        Also includes 'scl' if available.
    """
    import rasterio

    values = {}

    for asset_name in S2_BANDS + S2_EXTRA_ASSETS:
        if asset_name not in item.assets:
            if asset_name in S2_EXTRA_ASSETS:
                continue  # Optional
            return None

        href = item.assets[asset_name].href
        try:
            with rasterio.open(href) as ds:
                py, px = ds.index(lon, lat)
                if not (0 <= py < ds.height and 0 <= px < ds.width):
                    return None

                half = window_size // 2
                window = rasterio.windows.Window(
                    max(0, px - half),
                    max(0, py - half),
                    window_size,
                    window_size,
                )
                data = ds.read(1, window=window)

                if data.size == 0:
                    return None

                if asset_name == "scl":
                    # SCL: take the center pixel (classification, not continuous)
                    cy = min(half, data.shape[0] - 1)
                    cx = min(half, data.shape[1] - 1)
                    values["scl"] = int(data[cy, cx])
                else:
                    # Spectral bands: mean of window (raw DN)
                    val = float(np.nanmean(data))
                    if val <= 0 or np.isnan(val):
                        return None
                    values[asset_name] = val

        except Exception:
            if asset_name in S2_BANDS:
                return None

    # Verify we got all required bands
    if not all(b in values for b in S2_BANDS):
        return None

    return values


def extract_preprocessed_features(
    df: pd.DataFrame,
    max_cloud: float = 15.0,
    batch_size: int = 500,
    rate_limit_sec: float = 0.3,
) -> pd.DataFrame:
    """
    Main extraction loop: for each ICESat-2 point, find S2 scene,
    extract raw pixels, and apply full SDB preprocessing pipeline.

    Groups points into spatial tiles (~0.5 deg) to minimize STAC queries
    (same tile reuses the same S2 scene).
    """
    log.info(f"Extracting preprocessed S2 features for {len(df):,} points...")

    client = get_stac_client()
    preprocessor = SDBPreprocessor()

    # Group points into spatial tiles
    df = df.copy()
    df["tile_lat"] = (df["lat"] * 2).round() / 2
    df["tile_lon"] = (df["lon"] * 2).round() / 2

    tiles = df.groupby(["tile_lat", "tile_lon"])
    log.info(f"  {len(tiles)} spatial tiles to process")

    results = []
    total_matched = 0
    total_failed = 0
    total_filtered = 0

    for (tlat, tlon), tile_points in tqdm(tiles, desc="Tiles", total=len(tiles)):
        # Bounding box with buffer
        bbox = (tlon - 0.3, tlat - 0.3, tlon + 0.3, tlat + 0.3)

        # Get a representative target date from the tile's ICESat-2 points
        # (use median date if available for temporal matching)
        target_date = None
        if "time" in tile_points.columns:
            target_date = str(tile_points["time"].iloc[0])[:10]

        # Find best S2 scene for this tile
        item, scene_date = find_best_s2_scene(
            client, bbox, target_date=target_date, max_cloud=max_cloud
        )

        if item is None:
            total_failed += len(tile_points)
            continue

        # Get sun zenith if available
        sun_zenith = item.properties.get("view:sun_elevation")
        if sun_zenith is not None:
            sun_zenith = 90.0 - sun_zenith  # elevation -> zenith

        # Collect raw pixels for all points in this tile
        tile_raw = []
        for _, point in tile_points.iterrows():
            raw = extract_pixel_values(item, point["lon"], point["lat"])
            if raw is not None:
                raw["lat"] = point["lat"]
                raw["lon"] = point["lon"]
                raw["depth_m"] = point["depth_m"]
                raw["quality"] = point.get("quality", 3)
                raw["lake_id"] = point.get("lake_id", "unknown")
                raw["region"] = point.get("region", "unknown")
                raw["s2_date"] = scene_date
                if "time" in point.index:
                    raw["icesat2_date"] = str(point["time"])[:10]
                raw["sun_zenith"] = sun_zenith
                tile_raw.append(raw)
            else:
                total_failed += 1

        if not tile_raw:
            continue

        # Build a mini-DataFrame for this tile
        tile_df = pd.DataFrame(tile_raw)

        # Estimate glint slopes for this tile (uses all water pixels in the tile)
        blue_arr = tile_df["blue"].values / 10000.0
        green_arr = tile_df["green"].values / 10000.0
        red_arr = tile_df["red"].values / 10000.0
        nir_arr = tile_df["nir"].values / 10000.0
        preprocessor.estimate_glint_slopes(blue_arr, green_arr, red_arr, nir_arr)

        # Process each point through the full pipeline
        for _, row in tile_df.iterrows():
            bands = {b: row[b] for b in S2_BANDS if b in row.index}
            features = preprocessor.process_point(
                bands=bands,
                scl=row.get("scl"),
                sun_zenith=row.get("sun_zenith"),
                s2_date=pd.to_datetime(row.get("s2_date")) if row.get("s2_date") else None,
                icesat2_date=pd.to_datetime(row.get("icesat2_date")) if row.get("icesat2_date") else None,
                is_raw_dn=True,
            )

            if features is None:
                total_filtered += 1
                continue

            # Add metadata
            features["lat"] = row["lat"]
            features["lon"] = row["lon"]
            features["depth_m"] = row["depth_m"]
            features["quality"] = row.get("quality", 3)
            features["lake_id"] = row.get("lake_id", "unknown")
            features["region"] = row.get("region", "unknown")
            features["s2_date"] = row.get("s2_date", "")
            features["icesat2_date"] = row.get("icesat2_date", "")

            results.append(features)
            total_matched += 1

        if total_matched % 2000 < batch_size:
            log.info(
                f"  Progress: {total_matched:,} matched, "
                f"{total_filtered:,} filtered, {total_failed:,} failed"
            )

        time.sleep(rate_limit_sec)

    if not results:
        log.error("No features extracted!")
        return pd.DataFrame()

    result_df = pd.DataFrame(results)

    # Clean inf/nan
    for col in result_df.select_dtypes(include=[np.number]).columns:
        result_df[col] = result_df[col].replace([np.inf, -np.inf], np.nan)

    log.info(f"\nExtraction complete:")
    log.info(f"  Matched:  {total_matched:,}")
    log.info(f"  Filtered: {total_filtered:,} (quality)")
    log.info(f"  Failed:   {total_failed:,} (no S2 data)")
    log.info(f"  Features: {len(get_feature_list(list(result_df.columns)))}")

    return result_df


def assign_lake_ids_if_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Assign lake_id via DBSCAN if not already present."""
    if "lake_id" in df.columns and df["lake_id"].nunique() > 1:
        return df

    try:
        from sklearn.cluster import DBSCAN
        log.info("Assigning lake IDs via spatial clustering...")
        coords = df[["lat", "lon"]].values
        clustering = DBSCAN(eps=0.005, min_samples=3, metric="euclidean")
        labels = clustering.fit_predict(coords)
        df = df.copy()
        df["lake_id"] = labels
        df = df[df["lake_id"] >= 0].reset_index(drop=True)

        import hashlib
        lake_centers = df.groupby("lake_id")[["lat", "lon"]].mean()
        id_map = {}
        for lid, row in lake_centers.iterrows():
            raw = f"{row['lat']:.4f}_{row['lon']:.4f}"
            id_map[lid] = hashlib.md5(raw.encode()).hexdigest()[:12]
        df["lake_id"] = df["lake_id"].map(id_map)

        n_lakes = df["lake_id"].nunique()
        log.info(f"  {n_lakes} lakes identified")
    except ImportError:
        log.warning("sklearn not available; skipping lake ID assignment")

    return df


def split_by_lake(df: pd.DataFrame) -> pd.DataFrame:
    """Train/val/test split by lake_id for spatial cross-validation."""
    lake_ids = df["lake_id"].unique()
    np.random.seed(42)
    np.random.shuffle(lake_ids)

    n_train = int(len(lake_ids) * 0.70)
    n_val = int(len(lake_ids) * 0.15)

    train_lakes = set(lake_ids[:n_train])
    val_lakes = set(lake_ids[n_train:n_train + n_val])

    df = df.copy()
    df["split"] = "test"
    df.loc[df["lake_id"].isin(train_lakes), "split"] = "train"
    df.loc[df["lake_id"].isin(val_lakes), "split"] = "val"

    for split in ["train", "val", "test"]:
        n = (df["split"] == split).sum()
        log.info(f"  {split}: {n:,} points")

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Extract preprocessed S2 features for SDB training",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", type=str, required=True,
                        help="ICESat-2 parquet file or cache directory")
    parser.add_argument("--output", type=str, default="/data/sdb_preprocessed.parquet",
                        help="Output parquet path")
    parser.add_argument("--max-cloud", type=float, default=15.0,
                        help="Maximum S2 cloud cover percentage")
    parser.add_argument("--max-points", type=int, default=None,
                        help="Limit number of points (for testing)")
    parser.add_argument("--rate-limit", type=float, default=0.3,
                        help="Seconds between STAC queries")

    args = parser.parse_args()

    # Load ICESat-2 points
    df = load_icesat2_points(args.input, max_points=args.max_points)

    # Extract preprocessed features
    result = extract_preprocessed_features(
        df,
        max_cloud=args.max_cloud,
        rate_limit_sec=args.rate_limit,
    )

    if len(result) == 0:
        log.error("No data extracted. Exiting.")
        return

    # Assign lake IDs and split
    result = assign_lake_ids_if_missing(result)
    result = split_by_lake(result)

    # Save
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output, index=False)

    log.info(f"\nSaved {len(result):,} points to {output}")
    log.info(f"File size: {output.stat().st_size / 1024 / 1024:.1f} MB")
    log.info(f"Columns ({len(result.columns)}): {sorted(result.columns.tolist())}")

    # Summary
    sdb_features = get_feature_list(list(result.columns))
    log.info(f"\nSDB features ({len(sdb_features)}): {sdb_features}")

    for col in ["depth_m", "stumpf_ratio", "lyzenga_bg", "ndwi", "temporal_match_score"]:
        if col in result.columns:
            log.info(f"  {col}: mean={result[col].mean():.4f}, "
                     f"std={result[col].std():.4f}, "
                     f"nan%={result[col].isna().mean()*100:.1f}")


if __name__ == "__main__":
    main()
