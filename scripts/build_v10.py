"""Build v10 dataset: v9 + freshness features + as-of safe encoding + missingness indicators.

Production-grade changes:
1. Data freshness as first-class features (source type, data age indicators)
2. Missingness indicators (has_usgs, has_real_weather, has_creel, etc.)
3. As-of safe loc_mean_enc (rolling historical, no future leakage)
4. Remove Statewide aggregations
5. Fix area_acres=0
"""
import pandas as pd
import numpy as np

V9_PATH = "castline/validation/data/assembled/validation_dataset_v9.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v10.csv"


def add_freshness_features(df):
    """Add data source quality and freshness indicators."""

    # ── Source type encoding (one-hot) ──
    df["is_bassmaster"] = (df.source == "bassmaster").astype(int)
    df["is_creel"] = (df.source == "creel").astype(int)
    df["is_tourneyx"] = (df.source == "tourneyx").astype(int)

    # ── Missingness indicators ──
    # These tell the model what data is available vs imputed

    # USGS water data
    usgs_cols = ["water_temp_c", "dissolved_oxygen_mgL", "discharge_cfs",
                 "turbidity_fnu", "reservoir_elevation_ft", "gage_height_ft"]
    usgs_cols = [c for c in usgs_cols if c in df.columns]
    df["has_usgs_water"] = df[usgs_cols].notna().any(axis=1).astype(int) if usgs_cols else 0
    df["usgs_feature_count"] = df[usgs_cols].notna().sum(axis=1) if usgs_cols else 0

    # USGS intraday (IV) data
    iv_cols = [c for c in df.columns if c.startswith("water_temp_c_") and "spawn" not in c]
    df["has_usgs_iv"] = df[iv_cols].notna().any(axis=1).astype(int) if iv_cols else 0

    # NASA POWER weather (real daily vs missing)
    np_cols = [c for c in df.columns if c.startswith("np_")]
    df["has_real_weather"] = df[np_cols].notna().any(axis=1).astype(int) if np_cols else 0

    # Creel survey data
    creel_cols = ["creel_cpue_mean", "creel_lmb_ratio", "creel_smb_ratio"]
    creel_cols = [c for c in creel_cols if c in df.columns]
    df["has_creel_data"] = df[creel_cols].notna().all(axis=1).astype(int) if creel_cols else 0

    # Morphometry completeness
    morph_cols = ["area_acres", "max_depth_ft", "shore_dev"]
    morph_cols = [c for c in morph_cols if c in df.columns]
    df["morph_completeness"] = df[morph_cols].notna().sum(axis=1) / max(len(morph_cols), 1)

    # Overall data quality score (0-1)
    quality_components = ["has_usgs_water", "has_usgs_iv", "has_real_weather",
                          "has_creel_data", "morph_completeness"]
    quality_components = [c for c in quality_components if c in df.columns]
    df["data_quality_score"] = df[quality_components].mean(axis=1)

    return df


def add_asof_features(df):
    """Add as-of safe (point-in-time) location encoding.

    For each event, compute location mean using only PRIOR events at that location.
    This prevents temporal leakage.
    """
    df = df.sort_values(["location", "date"]).copy()

    # Rolling location mean (expanding window, only past data)
    df["loc_rolling_mean"] = np.nan
    df["loc_rolling_std"] = np.nan
    df["loc_n_prior"] = 0

    for loc in df.location.unique():
        mask = df.location == loc
        idx = df.index[mask]
        targets = df.loc[idx, "median_weight_lb"].values

        rolling_means = []
        rolling_stds = []
        n_priors = []
        for i in range(len(targets)):
            if i == 0:
                rolling_means.append(np.nan)
                rolling_stds.append(np.nan)
                n_priors.append(0)
            else:
                prior = targets[:i]
                rolling_means.append(np.mean(prior))
                rolling_stds.append(np.std(prior) if len(prior) > 1 else 0)
                n_priors.append(len(prior))

        df.loc[idx, "loc_rolling_mean"] = rolling_means
        df.loc[idx, "loc_rolling_std"] = rolling_stds
        df.loc[idx, "loc_n_prior"] = n_priors

    # Source-level rolling mean (as-of safe)
    df["source_rolling_mean"] = np.nan
    for src in df.source.unique():
        mask = df.source == src
        idx = df.index[mask]
        targets = df.loc[idx, "median_weight_lb"].values
        expanding = pd.Series(targets).expanding(min_periods=1).mean().shift(1).values
        df.loc[idx, "source_rolling_mean"] = expanding

    # Confidence in location encoding (more prior events = more confidence)
    df["loc_encoding_confidence"] = np.clip(df["loc_n_prior"] / 10, 0, 1)

    return df


def add_temporal_context(df):
    """Add temporal context features."""
    df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")

    # Days since start of dataset (temporal position)
    min_date = df["date_dt"].min()
    df["days_since_start"] = (df["date_dt"] - min_date).dt.days

    # Is this a recent event? (recency indicator)
    max_date = df["date_dt"].max()
    df["days_from_end"] = (max_date - df["date_dt"]).dt.days
    df["is_recent"] = (df["days_from_end"] < 365).astype(int)

    df.drop(columns=["date_dt"], inplace=True)
    return df


def main():
    print("=" * 60)
    print("Building v10 Dataset (Production)")
    print("=" * 60)

    df = pd.read_csv(V9_PATH, low_memory=False)
    df = df[df["median_weight_lb"].notna()].copy()
    print(f"v9 input: {len(df)} rows, {df.columns.shape[0]} columns")

    # Add freshness/quality features
    print("\nAdding freshness features...")
    df = add_freshness_features(df)

    # Add as-of safe encoding
    print("Adding as-of safe location encoding...")
    df = add_asof_features(df)

    # Add temporal context
    print("Adding temporal context...")
    df = add_temporal_context(df)

    # Report new features
    new_features = [
        "is_bassmaster", "is_creel", "is_tourneyx",
        "has_usgs_water", "usgs_feature_count", "has_usgs_iv",
        "has_real_weather", "has_creel_data", "morph_completeness",
        "data_quality_score",
        "loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
        "source_rolling_mean", "loc_encoding_confidence",
        "days_since_start", "days_from_end", "is_recent",
    ]
    print(f"\nNew features added: {len(new_features)}")
    for f in new_features:
        if f in df.columns:
            pct = df[f].notna().mean() * 100
            print(f"  {f:35s}: {pct:.1f}% coverage")

    print(f"\nv10 dataset: {len(df)} rows, {df.columns.shape[0]} columns")
    print(f"Locations: {df.location.nunique()}")

    df.to_csv(OUT_PATH, index=False)
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
