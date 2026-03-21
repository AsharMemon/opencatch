"""Build v16 dataset: v15 + USGS fish community features + GeoCLIP PCA-32 embeddings."""

import pandas as pd
import numpy as np

V15_PATH = "castline/validation/data/assembled/validation_dataset_v15.csv"
USGS_PATH = "castline/validation/data/raw/usgs_fish_community_features.csv"
GEOCLIP_PATH = "castline/validation/data/raw/location_embeddings_geoclip_pca32.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v16.csv"

DECIMALS = 4


def round_latlon(df):
    df = df.copy()
    df["lat_r"] = df["lat"].round(DECIMALS)
    df["lon_r"] = df["lon"].round(DECIMALS)
    return df


def main():
    # Load datasets
    v15 = pd.read_csv(V15_PATH)
    usgs = pd.read_csv(USGS_PATH)
    geoclip = pd.read_csv(GEOCLIP_PATH)

    print(f"v15:     {v15.shape[0]:,} rows x {v15.shape[1]} cols")
    print(f"USGS:    {usgs.shape[0]:,} rows x {usgs.shape[1]} cols")
    print(f"GeoCLIP: {geoclip.shape[0]:,} rows x {geoclip.shape[1]} cols")

    v15_cols = set(v15.columns)

    # Round lat/lon for matching
    v15 = round_latlon(v15)
    usgs = round_latlon(usgs)
    geoclip = round_latlon(geoclip)

    # Drop lat/lon from enrichment tables (keep only rounded keys + feature cols)
    usgs_features = [c for c in usgs.columns if c not in ("lat", "lon", "lat_r", "lon_r")]
    geoclip_features = [c for c in geoclip.columns if c not in ("lat", "lon", "lat_r", "lon_r")]

    # Deduplicate enrichment tables on rounded coords
    usgs_merge = usgs[["lat_r", "lon_r"] + usgs_features].drop_duplicates(subset=["lat_r", "lon_r"])
    geoclip_merge = geoclip[["lat_r", "lon_r"] + geoclip_features].drop_duplicates(subset=["lat_r", "lon_r"])

    # Left join USGS features
    v16 = v15.merge(usgs_merge, on=["lat_r", "lon_r"], how="left")
    usgs_matched = v16[usgs_features[0]].notna().sum()
    print(f"\nUSGS match rate:    {usgs_matched:,} / {len(v16):,} rows ({usgs_matched / len(v16) * 100:.1f}%)")

    # Left join GeoCLIP features
    v16 = v16.merge(geoclip_merge, on=["lat_r", "lon_r"], how="left")
    geoclip_matched = v16[geoclip_features[0]].notna().sum()
    print(f"GeoCLIP match rate: {geoclip_matched:,} / {len(v16):,} rows ({geoclip_matched / len(v16) * 100:.1f}%)")

    # Drop temporary rounding columns
    v16.drop(columns=["lat_r", "lon_r"], inplace=True)

    # Identify new columns
    new_cols = [c for c in v16.columns if c not in v15_cols]

    print(f"\nv16 output: {v16.shape[0]:,} rows x {v16.shape[1]} cols")
    print(f"New columns added ({len(new_cols)}):")
    for c in new_cols:
        non_null = v16[c].notna().sum()
        print(f"  {c:45s} {non_null:,} non-null")

    v16.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
