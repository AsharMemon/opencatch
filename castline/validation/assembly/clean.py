"""Data cleaning and validation for the CASTLINE validation pipeline.

Applies domain-informed rules to catch impossible values, flag outliers,
and deduplicate rows before they reach the model.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Domain bounds — values outside these ranges are physically impossible
# or almost certainly sensor errors.
# ---------------------------------------------------------------------------
SENSOR_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "water_temp_c": (-2.0, 45.0),          # below -2 is ice; above 45 is implausible
    "discharge_cfs": (0.0, 2_000_000.0),   # Mississippi peak ~2.3M
    "gage_height_ft": (-20.0, 120.0),
    "dissolved_oxygen_mgL": (0.0, 25.0),   # supersaturation tops ~20
    "turbidity_fnu": (0.0, 4000.0),
    "specific_conductance_us_cm": (0.0, 100_000.0),
    "ph": (2.0, 14.0),
    "reservoir_elevation_ft": (0.0, 10_000.0),
    "air_temp_c": (-50.0, 60.0),
    "pressure_mb": (850.0, 1100.0),
    "wind_speed_kph": (0.0, 250.0),
    "cloud_cover_pct": (0.0, 100.0),
    "precip_24h_mm": (0.0, 700.0),         # record ~600 mm
    "median_weight_lb": (0.1, 200.0),       # 0 weight means bad data
}


def _clip_to_bounds(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Replace out-of-bounds values with NaN. Returns (df, n_clipped)."""
    n_clipped = 0
    for col, (lo, hi) in SENSOR_BOUNDS.items():
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        mask = pd.Series(False, index=df.index)
        if lo is not None:
            mask |= series < lo
        if hi is not None:
            mask |= series > hi
        # Don't clip NaN — they're already missing
        mask &= series.notna()
        count = mask.sum()
        if count:
            n_clipped += int(count)
            df.loc[mask, col] = np.nan
    return df, n_clipped


def _deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove exact duplicate event_id rows, keeping first occurrence."""
    before = len(df)
    df = df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
    return df, before - len(df)


def _flag_outlier_weights(df: pd.DataFrame, z_threshold: float = 3.5) -> pd.DataFrame:
    """Flag rows where median_weight_lb is a statistical outlier (modified Z-score)."""
    if "median_weight_lb" not in df.columns:
        return df
    weights = df["median_weight_lb"]
    median = weights.median()
    mad = np.median(np.abs(weights - median))
    if mad == 0:
        df["weight_outlier"] = False
        return df
    modified_z = 0.6745 * (weights - median) / mad
    df["weight_outlier"] = modified_z.abs() > z_threshold
    return df


def _report_coverage(df: pd.DataFrame) -> dict[str, float]:
    """Return fraction of non-null values for key sensor columns."""
    sensor_cols = [c for c in SENSOR_BOUNDS if c in df.columns]
    return {col: float(df[col].notna().mean()) for col in sensor_cols}


def clean_validation_data(
    input_path: Path,
    output_path: Path,
    *,
    drop_outlier_weights: bool = False,
) -> pd.DataFrame:
    """Clean and validate the assembled validation dataset.

    Steps:
    1. Clip physically impossible values to NaN
    2. Deduplicate by event_id
    3. Flag statistical outlier weights
    4. Report sensor coverage

    Returns the cleaned DataFrame.
    """
    df = pd.read_csv(input_path)
    print(f"clean: loaded {len(df)} rows from {input_path}", file=sys.stderr)

    # 1. Clip impossible values
    df, n_clipped = _clip_to_bounds(df)
    if n_clipped:
        print(f"clean: clipped {n_clipped} out-of-bounds sensor values to NaN", file=sys.stderr)

    # 2. Deduplicate
    df, n_dupes = _deduplicate(df)
    if n_dupes:
        print(f"clean: removed {n_dupes} duplicate event_id rows", file=sys.stderr)

    # 3. Flag outlier weights
    df = _flag_outlier_weights(df)
    n_outliers = int(df.get("weight_outlier", pd.Series(dtype=bool)).sum())
    if n_outliers:
        print(f"clean: flagged {n_outliers} weight outlier rows", file=sys.stderr)
        if drop_outlier_weights:
            df = df.loc[~df["weight_outlier"]].reset_index(drop=True)
            print(f"clean: dropped outliers, {len(df)} rows remaining", file=sys.stderr)

    # 4. Coverage report
    coverage = _report_coverage(df)
    print("clean: sensor coverage:", file=sys.stderr)
    for col, pct in sorted(coverage.items(), key=lambda x: -x[1]):
        print(f"  {col}: {pct:.1%}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"clean: saved {len(df)} cleaned rows to {output_path}", file=sys.stderr)
    return df


# ─── Extended cleaning for v6+ ───────────────────────────────────────

def clean_v6(
    df: pd.DataFrame,
    target: str = "median_weight_lb",
    verbose: bool = True,
) -> pd.DataFrame:
    """Extended cleaning pipeline for v6 dataset.

    Adds to the base cleaning:
    - Cross-feature consistency checks
    - Smart imputation from correlated features
    - Location-level outlier detection
    - Normalize USGS site IDs
    - Feature gap filling from Open-Meteo columns
    """
    df = df.copy()
    n_start = len(df)

    # 1. Base cleaning (sensor bounds)
    df, n_clipped = _clip_to_bounds(df)
    if verbose and n_clipped:
        print(f"  Clipped {n_clipped} out-of-bounds values")

    # 2. Deduplicate on location+date
    if "location" in df.columns and "date" in df.columns:
        n_before = len(df)
        df = df.drop_duplicates(subset=["location", "date"], keep="last")
        if verbose and len(df) < n_before:
            print(f"  Removed {n_before - len(df)} location+date duplicates")

    # 3. Target validation
    if target in df.columns:
        n_before = len(df)
        df = df[df[target].notna() & (df[target] >= 0.5) & (df[target] <= 35)]
        if verbose and len(df) < n_before:
            print(f"  Removed {n_before - len(df)} invalid target rows")

    # 4. Cross-feature consistency
    if "water_temp_c" in df.columns and "air_temp_c" in df.columns:
        # Water temp > air temp by 20°C is likely sensor error
        bad = (df["water_temp_c"] - df["air_temp_c"]) > 20
        if bad.any():
            df.loc[bad, "water_temp_c"] = np.nan
            if verbose:
                print(f"  Fixed {bad.sum()} water temps inconsistent with air temp")

    # 5. Smart gap filling from Open-Meteo columns
    fill_map = {
        "air_temp_c": "om_air_temp_mean",
        "water_temp_c": "om_est_water_temp",
        "wind_speed_kph": "om_wind_max_kph",
        "pressure_mb": "om_pressure_msl",
    }
    for orig, om_col in fill_map.items():
        if orig in df.columns and om_col in df.columns:
            before = df[orig].notna().sum()
            df[orig] = df[orig].fillna(df[om_col])
            after = df[orig].notna().sum()
            if verbose and after > before:
                print(f"  Filled {orig}: {before} -> {after} (+{after - before})")

    # Water temp from air temp as last resort
    if "water_temp_c" in df.columns and "air_temp_c" in df.columns:
        mask = df["water_temp_c"].isna() & df["air_temp_c"].notna()
        if mask.any():
            df.loc[mask, "water_temp_c"] = df.loc[mask, "air_temp_c"] * 0.663 + 7.16
            if verbose:
                print(f"  Estimated {mask.sum()} water temps from air temp")

    # 6. Location-level outlier detection (z-score within location)
    if "location" in df.columns:
        for col in ["water_temp_c", "discharge_cfs", target]:
            if col not in df.columns:
                continue
            z = df.groupby("location")[col].transform(
                lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0
            )
            extreme = z.abs() > 3.5
            if extreme.any():
                df.loc[extreme, col] = np.nan
                if verbose:
                    print(f"  Removed {extreme.sum()} within-location outliers in {col}")

    # 7. Normalize USGS site IDs
    if "usgs_site_id" in df.columns:
        def _norm(val):
            try:
                return str(int(float(val))).zfill(8)
            except (ValueError, TypeError):
                return str(val).strip() if pd.notna(val) else np.nan
        df["usgs_site_id"] = df["usgs_site_id"].apply(_norm)

    # 8. Normalize dates
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "year" not in df.columns:
            df["year"] = pd.to_datetime(df["date"]).dt.year

    if verbose:
        print(f"\n  Cleaned: {len(df)} rows (from {n_start}), {len(df.columns)} cols")
        # Coverage summary
        key_features = [
            "water_temp_c", "air_temp_c", "discharge_cfs", "pressure_mb",
            "wind_speed_kph", "area_acres", "max_depth_ft", "lat", "lon",
        ]
        for f in key_features:
            if f in df.columns:
                print(f"    {f:<25s}: {df[f].notna().mean() * 100:5.1f}%")

    return df


def analyze_feature_importance_for_loo(
    df: pd.DataFrame,
    target: str = "median_weight_lb",
) -> pd.DataFrame:
    """Analyze which features help predict across locations vs within.

    Returns a DataFrame ranked by spatial (cross-location) correlation.
    """
    print("\n=== Feature Analysis for LOO Generalization ===")

    numeric_cols = df.select_dtypes(include=[np.number]).columns
    numeric_cols = [c for c in numeric_cols if c != target and df[c].notna().mean() > 0.1]

    loc_means = df.groupby("location")[target].transform("mean")
    residual = df[target] - loc_means

    results = []
    for col in numeric_cols:
        valid = df[[col, target]].dropna()
        if len(valid) < 20:
            continue

        overall_corr = valid[col].corr(valid[target])

        valid_loc = df[[col, target, "location"]].dropna()
        loc_m = valid_loc.groupby("location")[target].transform("mean")
        spatial_corr = valid_loc[col].corr(loc_m)

        valid_res = df[[col]].assign(residual=residual).dropna()
        env_corr = valid_res[col].corr(valid_res["residual"]) if len(valid_res) > 20 else 0

        results.append({
            "feature": col,
            "overall_r": round(overall_corr, 3),
            "spatial_r": round(spatial_corr, 3),
            "env_r": round(env_corr, 3),
            "coverage": round(df[col].notna().mean(), 2),
        })

    results_df = pd.DataFrame(results).sort_values("spatial_r", ascending=False, key=abs)

    print("\nTop features for SPATIAL signal (predicting location quality):")
    print(results_df.head(20).to_string(index=False))

    print("\nTop features for ENVIRONMENTAL signal (within-location variation):")
    print(results_df.sort_values("env_r", ascending=False, key=abs).head(15).to_string(index=False))

    return results_df
