#!/usr/bin/env python3
"""
OpenCatch — Combine SDB Training Data from Multiple Sources

Merges depth labels and spectral features from:
  1. ICESat-2 + S2 matched points (batch_s2_extract.py output)
  2. 3D-LAKES dataset (510K global lakes, area-elevation relationships)
  3. MN DNR in-situ bathymetry (if available)

Creates a unified training set with columns:
  [S2 bands, physics features, depth_label, source, lake_id, split]

3D-LAKES integration strategy:
  - 3D-LAKES provides max depth estimates per lake (from ICESat-2 + Landsat)
  - We match these to our S2 spectral points by lake location (HydroLAKES ID)
  - Use 3D-LAKES depth as a secondary label/feature for lakes where
    ICESat-2 point depth is also available (cross-validation)
  - For lakes with ONLY 3D-LAKES data, we can use their A-E relationship
    to estimate depth at specific water surface areas

Usage:
    python combine_sdb_sources.py \
        --icesat2-s2 /data/sdb_training_50k.parquet \
        --three-d-lakes /data/3d_lakes_st.csv \
        --three-d-lakes-qa /data/3d_lakes_qa.csv \
        --output /data/sdb_combined_training.parquet

    # With MN DNR data
    python combine_sdb_sources.py \
        --icesat2-s2 /data/sdb_training_50k.parquet \
        --three-d-lakes /data/3d_lakes_st.csv \
        --mndnr /data/mndnr_depths.parquet \
        --output /data/sdb_combined_training.parquet
"""

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("combine_sdb")


def load_icesat2_s2(path: str) -> pd.DataFrame:
    """Load the ICESat-2 + S2 matched training data."""
    df = pd.read_parquet(path)
    df["source"] = "icesat2"
    log.info(f"ICESat-2 + S2: {len(df):,} points, {df['lake_id'].nunique()} lakes")
    if "depth_m" in df.columns:
        log.info(f"  Depth range: {df['depth_m'].min():.1f} - {df['depth_m'].max():.1f}m")
    return df


def load_3d_lakes(csv_path: str, qa_path: str = None) -> pd.DataFrame:
    """
    Load 3D-LAKES dataset and extract usable depth information.

    The 3D-LAKES CSV has: Hylak_id, Pour_long, Pour_lat, PLD_id, Grand_id
    The actual A-E relationships are in the JSON/ZIP files. From the CSV we
    get lake locations which we can spatially match to our ICESat-2 points.

    The QA file has: Hylak_id, QA_RMSE, QA_NRMSE, QA_Extrapolation,
                     Lake_area, Slope_100, NRMSE_model
    """
    log.info("Loading 3D-LAKES dataset...")
    df = pd.read_csv(csv_path)
    log.info(f"  {len(df):,} lakes total")
    log.info(f"  Columns: {list(df.columns)}")

    # Rename for consistency
    df = df.rename(columns={
        "Pour_long": "lon",
        "Pour_lat": "lat",
        "Hylak_id": "hylak_id",
    })

    # Load QA if available
    if qa_path and Path(qa_path).exists():
        qa = pd.read_csv(qa_path)
        qa = qa.rename(columns={"Hylak_id": "hylak_id"})
        df = df.merge(qa, on="hylak_id", how="left")
        log.info(f"  QA data merged: {len(qa):,} records")
        if "Lake_area" in df.columns:
            log.info(f"  Lake area range: {df['Lake_area'].min():.2f} - "
                     f"{df['Lake_area'].max():.2f} km²")

    # Filter to North American lakes
    na_mask = (df["lon"].between(-130, -60)) & (df["lat"].between(25, 55))
    df_na = df[na_mask].copy()
    log.info(f"  North American lakes: {len(df_na):,}")

    return df, df_na


def spatial_match_3dlakes_to_points(
    points_df: pd.DataFrame,
    lakes_df: pd.DataFrame,
    match_radius_deg: float = 0.01,
) -> pd.DataFrame:
    """
    Spatially match 3D-LAKES lake metadata to ICeSat-2 + S2 training points.

    For each training point, find the nearest 3D-LAKES lake within
    match_radius_deg (~1km) and add its metadata.

    This enriches training points with:
      - hylak_id: HydroLAKES identifier for the lake
      - Lake_area: Lake surface area (km²)
      - Slope_100: Shoreline slope metric
      - QA_RMSE, QA_NRMSE: Quality metrics from 3D-LAKES validation

    These become useful features for the depth model.
    """
    from scipy.spatial import cKDTree

    log.info(f"Spatial matching {len(points_df):,} points to "
             f"{len(lakes_df):,} 3D-LAKES entries...")

    # Build KD-tree from lake locations
    lake_coords = lakes_df[["lat", "lon"]].values
    tree = cKDTree(lake_coords)

    # Query for each training point
    point_coords = points_df[["lat", "lon"]].values
    distances, indices = tree.query(point_coords)

    # Filter matches within radius
    matched = distances < match_radius_deg
    log.info(f"  Matched: {matched.sum():,} / {len(points_df):,} points "
             f"({matched.mean()*100:.1f}%)")

    # Add lake metadata
    result = points_df.copy()
    result["hylak_id"] = np.nan
    result["lake_area_km2"] = np.nan
    result["slope_100"] = np.nan
    result["qa_rmse"] = np.nan
    result["match_dist_deg"] = np.nan

    for col_src, col_dst in [("hylak_id", "hylak_id"),
                              ("Lake_area", "lake_area_km2"),
                              ("Slope_100", "slope_100"),
                              ("QA_RMSE", "qa_rmse")]:
        if col_src in lakes_df.columns:
            vals = lakes_df[col_src].values
            result.loc[matched, col_dst] = vals[indices[matched]]

    result.loc[matched, "match_dist_deg"] = distances[matched]

    log.info(f"  Matched lakes: {result['hylak_id'].notna().sum():,}")
    if "lake_area_km2" in result.columns:
        matched_areas = result.loc[result["lake_area_km2"].notna(), "lake_area_km2"]
        if len(matched_areas) > 0:
            log.info(f"  Matched lake areas: median={matched_areas.median():.2f} km²")

    return result


def load_mndnr_depths(path: str) -> pd.DataFrame:
    """Load MN DNR in-situ bathymetry data if available."""
    if not Path(path).exists():
        log.info(f"MN DNR file not found at {path}, skipping")
        return pd.DataFrame()

    df = pd.read_parquet(path)
    df["source"] = "mndnr"
    log.info(f"MN DNR: {len(df):,} points")
    return df


def combine_all_sources(
    icesat2_s2: pd.DataFrame,
    lakes_3d_na: pd.DataFrame,
    mndnr: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Combine all data sources into a unified training set.

    Strategy:
    1. Start with ICESat-2 + S2 matched points (primary dataset)
    2. Enrich with 3D-LAKES metadata (lake area, quality, HydroLAKES ID)
    3. Add MN DNR in-situ points if available
    4. Ensure consistent columns and split by lake
    """
    log.info("Combining all sources...")

    # Enrich ICeSat-2 points with 3D-LAKES metadata
    enriched = spatial_match_3dlakes_to_points(icesat2_s2, lakes_3d_na)

    all_dfs = [enriched]

    if mndnr is not None and len(mndnr) > 0:
        # Ensure MN DNR has same columns structure
        # (it may have different feature columns)
        mndnr_enriched = spatial_match_3dlakes_to_points(mndnr, lakes_3d_na)
        all_dfs.append(mndnr_enriched)

    result = pd.concat(all_dfs, ignore_index=True)

    log.info(f"\nCombined dataset: {len(result):,} points")
    if "source" in result.columns:
        for src in result["source"].unique():
            n = (result["source"] == src).sum()
            log.info(f"  {src}: {n:,} points")

    return result


def final_split(df: pd.DataFrame) -> pd.DataFrame:
    """
    Final train/val/test split by lake_id.
    Ensures no lake appears in multiple splits.
    """
    if "lake_id" not in df.columns:
        log.warning("No lake_id column, skipping split")
        df["split"] = "train"
        return df

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
        n_l = df.loc[df["split"] == split, "lake_id"].nunique()
        log.info(f"  {split}: {n:,} points, {n_l} lakes")

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Combine SDB training data from multiple sources",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--icesat2-s2", type=str,
                        default="/data/sdb_training_50k.parquet",
                        help="ICeSat-2 + S2 matched training data")
    parser.add_argument("--three-d-lakes", type=str,
                        default="/data/3d_lakes_st.csv",
                        help="3D-LAKES CSV file")
    parser.add_argument("--three-d-lakes-qa", type=str,
                        default="/data/3d_lakes_qa.csv",
                        help="3D-LAKES QA file")
    parser.add_argument("--mndnr", type=str, default=None,
                        help="MN DNR in-situ bathymetry parquet")
    parser.add_argument("--output", type=str,
                        default="/data/sdb_combined_training.parquet",
                        help="Output combined parquet")

    args = parser.parse_args()

    # Load sources
    icesat2_s2 = load_icesat2_s2(args.icesat2_s2)

    lakes_3d_all, lakes_3d_na = load_3d_lakes(
        args.three_d_lakes,
        qa_path=args.three_d_lakes_qa,
    )

    mndnr = None
    if args.mndnr:
        mndnr = load_mndnr_depths(args.mndnr)

    # Combine
    combined = combine_all_sources(icesat2_s2, lakes_3d_na, mndnr)

    # Final split
    combined = final_split(combined)

    # Save
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output, index=False)

    log.info(f"\nSaved combined dataset: {output}")
    log.info(f"  Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
    log.info(f"  Shape: {combined.shape}")
    log.info(f"  Columns: {sorted(combined.columns.tolist())}")

    # Feature summary
    feature_cols = [c for c in combined.columns
                    if c not in ["lat", "lon", "depth_m", "quality", "lake_id",
                                 "region", "source", "split", "tile_key",
                                 "s2_date", "hylak_id", "match_dist_deg",
                                 "geometry", "h_mean", "h_sigma",
                                 "w_surface_window_final", "spot", "rgt", "cycle"]]
    log.info(f"\nFeature columns ({len(feature_cols)}): {feature_cols}")

    # Depth distribution
    if "depth_m" in combined.columns:
        log.info(f"\nDepth distribution:")
        for q in [0.1, 0.25, 0.5, 0.75, 0.9, 0.95]:
            log.info(f"  {q*100:.0f}th percentile: "
                     f"{combined['depth_m'].quantile(q):.2f}m")


if __name__ == "__main__":
    main()
