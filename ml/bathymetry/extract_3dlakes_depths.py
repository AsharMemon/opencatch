#!/usr/bin/env python3
"""
OpenCatch — Extract Max Depth from 3D-LAKES L1 A-E Products

Reads the per-lake L1 CSVs (Elevation, Area) and computes:
  - max_depth_m: elevation range = max(elev) - min(elev)
  - n_ae_points: number of A-E data points (quality indicator)
  - max_area_m2: maximum water surface area

Merges with the main 3D-LAKES-ST.csv to add coordinates and HydroLAKES IDs.

Output: /data/3d_lakes_with_depths.parquet
  Columns: hylak_id, lat, lon, max_depth_m, n_ae_points, max_area_m2

Usage:
    python extract_3dlakes_depths.py \
        --l1-dir /data/3d_lakes_l1 \
        --main-csv /data/3d_lakes_st.csv \
        --output /data/3d_lakes_with_depths.parquet
"""

import argparse
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract_3dlakes")


def process_l1_files(l1_dir: str, batch_size: int = 10000) -> pd.DataFrame:
    """
    Process all L1 A-E CSV files to extract max depth per lake.

    Each file is named {hylak_id}_L1.csv with columns:
        Elevation (m), Area (m2)

    Max depth = max(Elevation) - min(Elevation)
    """
    l1_path = Path(l1_dir)
    csv_files = list(l1_path.glob("*_L1.csv"))
    log.info(f"Found {len(csv_files):,} L1 A-E files in {l1_dir}")

    records = []
    errors = 0

    for f in tqdm(csv_files, desc="Processing L1 files"):
        try:
            # Extract hylak_id from filename
            hylak_id = int(f.stem.replace("_L1", ""))

            # Read the A-E data
            ae = pd.read_csv(f)
            if len(ae) == 0:
                continue

            elev_col = ae.columns[0]  # "Elevation (m)"
            area_col = ae.columns[1]  # "Area (m2)"

            elevations = ae[elev_col].values
            areas = ae[area_col].values

            max_depth = float(elevations.max() - elevations.min())
            n_points = len(ae)
            max_area = float(areas.max())
            min_elev = float(elevations.min())
            max_elev = float(elevations.max())

            records.append({
                "hylak_id": hylak_id,
                "max_depth_m": max_depth,
                "n_ae_points": n_points,
                "max_area_m2": max_area,
                "min_elev_m": min_elev,
                "max_elev_m": max_elev,
            })

        except Exception as e:
            errors += 1
            if errors < 10:
                log.warning(f"  Error processing {f.name}: {e}")

    if errors > 0:
        log.warning(f"  Total errors: {errors}")

    df = pd.DataFrame(records)
    log.info(f"Extracted depth info for {len(df):,} lakes")

    if len(df) > 0:
        log.info(f"  Max depth range: {df['max_depth_m'].min():.2f} - "
                 f"{df['max_depth_m'].max():.2f}m")
        log.info(f"  Median max depth: {df['max_depth_m'].median():.2f}m")
        log.info(f"  A-E points per lake: min={df['n_ae_points'].min()}, "
                 f"median={df['n_ae_points'].median():.0f}, "
                 f"max={df['n_ae_points'].max()}")

        # Depth distribution
        for threshold in [1, 5, 10, 20, 50]:
            n = (df["max_depth_m"] >= threshold).sum()
            log.info(f"  Lakes >= {threshold}m deep: {n:,} "
                     f"({n/len(df)*100:.1f}%)")

    return df


def merge_with_main(depths_df: pd.DataFrame, main_csv: str) -> pd.DataFrame:
    """Merge depth data with main 3D-LAKES CSV to add coordinates."""
    log.info(f"Merging with main CSV: {main_csv}")
    main = pd.read_csv(main_csv)
    main = main.rename(columns={
        "Hylak_id": "hylak_id",
        "Pour_long": "lon",
        "Pour_lat": "lat",
    })

    # Merge
    merged = depths_df.merge(main[["hylak_id", "lat", "lon"]], on="hylak_id", how="left")
    n_matched = merged["lat"].notna().sum()
    log.info(f"  Matched with coordinates: {n_matched:,} / {len(merged):,}")

    # Drop unmatched
    merged = merged.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    # Filter to reasonable depths
    merged = merged[merged["max_depth_m"] > 0].reset_index(drop=True)

    # North American subset
    na_mask = (merged["lon"].between(-130, -60)) & (merged["lat"].between(25, 55))
    n_na = na_mask.sum()
    log.info(f"  North American lakes: {n_na:,}")

    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Extract max depth from 3D-LAKES L1 A-E products",
    )
    parser.add_argument("--l1-dir", type=str, default="/data/3d_lakes_l1",
                        help="Directory with L1 A-E CSV files")
    parser.add_argument("--main-csv", type=str, default="/data/3d_lakes_st.csv",
                        help="Main 3D-LAKES CSV with coordinates")
    parser.add_argument("--output", type=str,
                        default="/data/3d_lakes_with_depths.parquet",
                        help="Output parquet path")

    args = parser.parse_args()

    # Process L1 files
    depths = process_l1_files(args.l1_dir)

    if len(depths) == 0:
        log.error("No depth data extracted!")
        return

    # Merge with coordinates
    merged = merge_with_main(depths, args.main_csv)

    # Save
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output, index=False)

    log.info(f"\nSaved: {output}")
    log.info(f"  Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
    log.info(f"  Shape: {merged.shape}")
    log.info(f"  Columns: {list(merged.columns)}")


if __name__ == "__main__":
    main()
