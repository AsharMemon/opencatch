#!/usr/bin/env python3
"""
OpenCatch — Build Global Training Dataset from ALL 510K 3D-LAKES

Creates a comprehensive training dataset combining:
  1. A-E geometric features (shape of area-elevation curve)
  2. Morphometric features (area, perimeter, shape factor)
  3. Geographic features (lat, lon, elevation, climate zone)
  4. QA metadata from 3D-LAKES

Label: max_depth from A-E curve (elevation range)

This is the base dataset. Spectral (S2) features can be added later
for a stratified sample via batch_s2_extract.py.

Memory management:
  - Processes L1 CSVs in batches of 10K
  - Saves checkpoints every 50K lakes
  - Final dataset ~510K rows, ~50 features = ~200MB parquet

Usage:
    python build_global_training.py \
        --l1-dir /data/3d_lakes_l1 \
        --depths /data/3d_lakes_with_depths.parquet \
        --qa /data/3d_lakes_qa.csv \
        --output /data/training/global_510k.parquet

Requirements:
    pip install pandas numpy scipy scikit-learn pyarrow tqdm
"""

import argparse
import gc
import json
import logging
import math
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import interpolate
from scipy.integrate import trapezoid
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_global")


# ── Climate Zone Classification ─────────────────────────────────────

def classify_climate_zone(lat: float, lon: float, elev: float) -> int:
    """
    Simple climate zone from lat/lon/elevation.

    Returns zone 0-7:
      0: Tropical (<23.5 abs lat)
      1: Subtropical (23.5-35)
      2: Temperate (35-50)
      3: Boreal (50-66.5)
      4: Arctic (>66.5)
      5: Arid (tropical+subtropical, continental interior)
      6: Alpine (>2000m elevation)
      7: Maritime (within ~200km of coast, rough heuristic)
    """
    abs_lat = abs(lat)

    # Elevation override
    if elev > 2000:
        return 6  # Alpine

    if abs_lat < 23.5:
        return 0  # Tropical
    elif abs_lat < 35:
        return 1  # Subtropical
    elif abs_lat < 50:
        return 2  # Temperate
    elif abs_lat < 66.5:
        return 3  # Boreal
    else:
        return 4  # Arctic


def compute_morphometric_features(
    area_m2: float, elev_range: float,
    ae_elevations: np.ndarray, ae_areas: np.ndarray,
) -> Dict:
    """
    Compute morphometric features from the A-E curve and lake area.

    GLOBathy approach: area + perimeter + shape → depth
    """
    features = {}

    # Area-derived
    area_km2 = area_m2 / 1e6
    features["area_km2"] = area_km2
    features["log_area_km2"] = np.log1p(area_km2)

    # Approximate perimeter from area assuming elliptical shape
    # P ≈ 2π * sqrt((a²+b²)/2) where a*b = area/π
    # For a circle: P = 2*sqrt(π*A)
    equiv_radius = np.sqrt(area_m2 / np.pi)
    features["equiv_radius_m"] = equiv_radius
    features["approx_perimeter_m"] = 2 * np.pi * equiv_radius  # circular approx

    # Shoreline development index: actual perimeter / circular perimeter
    # Without actual perimeter, use area-elevation relationship as proxy
    # Lakes with irregular shapes have more A-E variation
    features["shoreline_dev_proxy"] = 1.0  # placeholder, overridden if data available

    # Shape factor: depth / sqrt(area)
    if area_m2 > 0 and elev_range > 0:
        features["shape_factor"] = elev_range / np.sqrt(area_km2)
    else:
        features["shape_factor"] = 0.0

    # Volume development: ratio of actual volume to cone volume
    if len(ae_elevations) >= 3 and elev_range > 0:
        sort_idx = np.argsort(ae_elevations)
        elev_sorted = ae_elevations[sort_idx]
        area_sorted = ae_areas[sort_idx]

        try:
            actual_vol = trapezoid(area_sorted, elev_sorted)
            cone_vol = area_sorted.max() * elev_range / 3.0
            features["volume_dev"] = actual_vol / cone_vol if cone_vol > 0 else 1.0
        except Exception:
            features["volume_dev"] = 1.0

        # Mean depth from volume / surface area
        features["mean_depth_m"] = actual_vol / area_sorted.max() if area_sorted.max() > 0 else 0
        features["relative_depth"] = (
            elev_range / (2 * np.sqrt(area_km2 * np.pi / 1e6))
            if area_km2 > 0 else 0
        )
    else:
        features["volume_dev"] = 1.0
        features["mean_depth_m"] = elev_range / 2.0 if elev_range > 0 else 0
        features["relative_depth"] = 0.0

    # Depth-area ratio
    if area_km2 > 0:
        features["depth_area_ratio"] = elev_range / area_km2
    else:
        features["depth_area_ratio"] = 0.0

    return features


# ── Main Processing ─────────────────────────────────────────────────

def process_lake_batch(
    file_paths: List[Path],
    depths_lookup: Dict,
    qa_lookup: Dict,
    coords_lookup: Dict,
) -> List[Dict]:
    """Process a batch of L1 files into full feature records."""
    records = []

    for f in file_paths:
        try:
            hylak_id = int(f.stem.replace("_L1", ""))

            # Skip if no coordinates
            if hylak_id not in coords_lookup:
                continue

            ae = pd.read_csv(f)
            if len(ae) < 2:
                continue

            elev = ae.iloc[:, 0].values.astype(np.float64)
            area = ae.iloc[:, 1].values.astype(np.float64)

            elev_range = float(elev.max() - elev.min())
            if elev_range <= 0:
                continue

            coords = coords_lookup[hylak_id]
            lat, lon = coords["lat"], coords["lon"]

            record = {"hylak_id": hylak_id, "lat": lat, "lon": lon}

            # ── A-E features ──
            n_pts = len(elev)
            record["n_ae_points"] = n_pts
            record["elev_range_m"] = elev_range
            record["min_elev_m"] = float(elev.min())
            record["max_elev_m"] = float(elev.max())
            record["max_area_m2"] = float(area.max())
            record["min_area_m2"] = float(area.min())
            record["area_ratio"] = float(area.min() / area.max()) if area.max() > 0 else 0

            # Slope features
            if n_pts >= 3:
                sort_idx = np.argsort(elev)
                e_s, a_s = elev[sort_idx], area[sort_idx]
                da_de = np.gradient(a_s, e_s)
                record["slope_mean"] = float(np.mean(da_de))
                record["slope_std"] = float(np.std(da_de))
                record["slope_max"] = float(np.max(np.abs(da_de)))

                # Curvature
                if n_pts >= 4:
                    d2a = np.gradient(da_de, e_s)
                    record["curvature_mean"] = float(np.mean(d2a))
                    record["convexity"] = float(np.mean(np.sign(d2a)))
                else:
                    record["curvature_mean"] = 0.0
                    record["convexity"] = 0.0

                # Hypsometric integral
                try:
                    vol = trapezoid(a_s, e_s)
                    max_vol = a_s.max() * elev_range
                    record["hypsometric_integral"] = float(vol / max_vol) if max_vol > 0 else 0.5
                except Exception:
                    record["hypsometric_integral"] = 0.5

                # Depth-normalized area at percentiles
                depth = e_s[-1] - e_s
                depth_norm = depth / elev_range
                area_norm = a_s / a_s.max() if a_s.max() > 0 else a_s

                if n_pts >= 5:
                    try:
                        interp_f = interpolate.interp1d(
                            depth_norm, area_norm, kind="linear",
                            bounds_error=False,
                            fill_value=(area_norm[0], area_norm[-1]),
                        )
                        for pct in [0.25, 0.50, 0.75]:
                            record[f"area_at_d{int(pct*100)}"] = float(interp_f(pct))
                    except Exception:
                        for pct in [0.25, 0.50, 0.75]:
                            record[f"area_at_d{int(pct*100)}"] = 0.5
                else:
                    for pct in [0.25, 0.50, 0.75]:
                        record[f"area_at_d{int(pct*100)}"] = 0.5

                # Power-law exponent
                valid = (depth_norm > 0) & (area_norm > 0)
                if valid.sum() >= 3:
                    try:
                        coeffs = np.polyfit(
                            np.log(depth_norm[valid]),
                            np.log(area_norm[valid]), 1,
                        )
                        record["power_exponent"] = float(coeffs[0])
                    except Exception:
                        record["power_exponent"] = 1.0
                else:
                    record["power_exponent"] = 1.0

            else:
                record.update({
                    "slope_mean": 0, "slope_std": 0, "slope_max": 0,
                    "curvature_mean": 0, "convexity": 0,
                    "hypsometric_integral": 0.5,
                    "area_at_d25": 0.5, "area_at_d50": 0.5, "area_at_d75": 0.5,
                    "power_exponent": 1.0,
                })

            # ── Morphometric features ──
            morph = compute_morphometric_features(
                area.max(), elev_range, elev, area,
            )
            record.update(morph)

            # ── Geographic features ──
            record["abs_lat"] = abs(lat)
            record["climate_zone"] = classify_climate_zone(
                lat, lon, float(elev.max()),
            )

            # Continental indicator
            if -170 < lon < -30:
                record["continent"] = 0  # Americas
            elif -30 < lon < 60:
                record["continent"] = 1  # Europe/Africa
            elif 60 < lon < 180:
                record["continent"] = 2  # Asia/Oceania
            else:
                record["continent"] = 3  # Other

            # ── QA features ──
            qa = qa_lookup.get(hylak_id, {})
            record["qa_rmse"] = qa.get("QA_RMSE", np.nan)
            record["qa_nrmse"] = qa.get("QA_NRMSE", np.nan)
            record["qa_extrap"] = qa.get("QA_Extrapolation", np.nan)
            record["qa_slope_100"] = qa.get("Slope_100", np.nan)
            record["qa_nrmse_model"] = qa.get("NRMSE_model", np.nan)

            # ── Label ──
            depth_info = depths_lookup.get(hylak_id, {})
            record["max_depth_m"] = depth_info.get("max_depth_m", elev_range)
            record["log_depth"] = np.log1p(record["max_depth_m"])

            records.append(record)

        except Exception:
            pass

    return records


def main():
    parser = argparse.ArgumentParser(
        description="Build global 510K lake training dataset",
    )
    parser.add_argument("--l1-dir", type=str, default="/data/3d_lakes_l1")
    parser.add_argument("--depths", type=str, default="/data/3d_lakes_with_depths.parquet")
    parser.add_argument("--qa", type=str, default="/data/3d_lakes_qa.csv")
    parser.add_argument("--output", type=str, default="/data/training/global_510k.parquet")
    parser.add_argument("--batch-size", type=int, default=10000)
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # ── Load lookups ──
    log.info("Loading depth labels...")
    depths_df = pd.read_parquet(args.depths)
    depths_lookup = {
        row["hylak_id"]: {"max_depth_m": row["max_depth_m"]}
        for _, row in depths_df.iterrows()
    }
    coords_lookup = {
        row["hylak_id"]: {"lat": row["lat"], "lon": row["lon"]}
        for _, row in depths_df.iterrows()
    }
    log.info(f"  {len(depths_lookup):,} depth labels")
    del depths_df
    gc.collect()

    log.info("Loading QA data...")
    qa_df = pd.read_csv(args.qa)
    qa_lookup = {
        row["Hylak_id"]: {
            "QA_RMSE": row["QA_RMSE"],
            "QA_NRMSE": row["QA_NRMSE"],
            "QA_Extrapolation": row["QA_Extrapolation"],
            "Slope_100": row["Slope_100"],
            "NRMSE_model": row["NRMSE_model"],
        }
        for _, row in qa_df.iterrows()
    }
    log.info(f"  {len(qa_lookup):,} QA records")
    del qa_df
    gc.collect()

    # ── Process all L1 files ──
    l1_path = Path(args.l1_dir)
    csv_files = sorted(l1_path.glob("*_L1.csv"))
    log.info(f"Found {len(csv_files):,} L1 files")

    # Check for checkpoint
    ckpt_path = out_path.parent / "global_510k_checkpoint.parquet"
    all_records = []
    start_idx = 0

    if ckpt_path.exists():
        log.info(f"Loading checkpoint: {ckpt_path}")
        ckpt_df = pd.read_parquet(ckpt_path)
        all_records = ckpt_df.to_dict("records")
        processed_ids = set(ckpt_df["hylak_id"].values)
        # Filter out already-processed files
        csv_files = [
            f for f in csv_files
            if int(f.stem.replace("_L1", "")) not in processed_ids
        ]
        log.info(f"  Resuming with {len(all_records):,} done, "
                 f"{len(csv_files):,} remaining")
        del ckpt_df, processed_ids
        gc.collect()

    batch_size = args.batch_size
    n_batches = (len(csv_files) + batch_size - 1) // batch_size
    checkpoint_interval = 50000

    for batch_idx in tqdm(range(n_batches), desc="Processing batches"):
        batch_start = batch_idx * batch_size
        batch_end = min(batch_start + batch_size, len(csv_files))
        batch_files = csv_files[batch_start:batch_end]

        records = process_lake_batch(
            batch_files, depths_lookup, qa_lookup, coords_lookup,
        )
        all_records.extend(records)

        # Checkpoint every 50K
        if len(all_records) % checkpoint_interval < batch_size and len(all_records) >= checkpoint_interval:
            pd.DataFrame(all_records).to_parquet(ckpt_path, index=False)
            log.info(f"  Checkpoint: {len(all_records):,} records saved")
            gc.collect()

    # ── Build final dataset ──
    df = pd.DataFrame(all_records)
    log.info(f"\nFinal dataset: {df.shape}")
    log.info(f"Columns ({len(df.columns)}): {list(df.columns)}")

    # Stats
    log.info(f"\nDepth distribution:")
    log.info(f"  Mean:   {df['max_depth_m'].mean():.2f}m")
    log.info(f"  Median: {df['max_depth_m'].median():.2f}m")
    log.info(f"  Std:    {df['max_depth_m'].std():.2f}m")
    log.info(f"  Min:    {df['max_depth_m'].min():.2f}m")
    log.info(f"  Max:    {df['max_depth_m'].max():.2f}m")

    for lo, hi in [(0, 1), (1, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 700)]:
        n = ((df["max_depth_m"] >= lo) & (df["max_depth_m"] < hi)).sum()
        log.info(f"  [{lo:4d}-{hi:4d}m]: {n:7,} ({n/len(df)*100:.1f}%)")

    log.info(f"\nGeographic distribution:")
    log.info(f"  Lat range: {df['lat'].min():.1f} to {df['lat'].max():.1f}")
    log.info(f"  Lon range: {df['lon'].min():.1f} to {df['lon'].max():.1f}")

    climate_names = {
        0: "Tropical", 1: "Subtropical", 2: "Temperate",
        3: "Boreal", 4: "Arctic", 5: "Arid", 6: "Alpine",
    }
    for zone in sorted(df["climate_zone"].unique()):
        n = (df["climate_zone"] == zone).sum()
        name = climate_names.get(zone, f"Zone {zone}")
        log.info(f"  {name}: {n:,} ({n/len(df)*100:.1f}%)")

    # Save
    df.to_parquet(args.output, index=False)
    log.info(f"\nSaved: {args.output}")
    log.info(f"  Size: {Path(args.output).stat().st_size / 1024 / 1024:.1f} MB")

    # Clean up checkpoint
    if ckpt_path.exists():
        ckpt_path.unlink()
        log.info(f"  Removed checkpoint: {ckpt_path}")

    elapsed = time.time() - t0
    log.info(f"Total time: {elapsed / 60:.1f} minutes")


if __name__ == "__main__":
    main()
