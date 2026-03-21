"""Build v5 dataset with lake features, temporal features, and expanded morphometry.

v5 adds:
1. Lake-specific features (turnover, wind-fetch, pressure dynamics, solunar boost)
2. Temporal features from USGS IV data (rate of change, spike detection, stability)
3. Expanded morphometry (74 lakes, 94% coverage)
4. River vs lake classification
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from castline.validation.features.lake_features import compute_all_lake_features


def build_v5(
    v4_path: str = "castline/validation/data/assembled/validation_dataset_v4.csv",
    iv_path: str = "castline/validation/data/raw/usgs_iv_features_v2.csv",
    output_path: str = "castline/validation/data/assembled/validation_dataset_v5.csv",
) -> pd.DataFrame:
    """Build v5 dataset from v4 + new features."""

    print("Loading v4 dataset...")
    df = pd.read_csv(v4_path)
    print(f"  v4: {len(df)} rows, {len(df.columns)} columns")

    # ── 1. Add lake features ──
    print("\nComputing lake features...")
    lake_feature_cols = []
    lake_features_list = []

    for idx, row in df.iterrows():
        # Determine day of year
        try:
            season_day = pd.to_datetime(row.get("date", "2020-06-15")).dayofyear
        except Exception:
            season_day = 180

        features = compute_all_lake_features(
            surface_temp_c=row.get("water_temp_c", float("nan")),
            air_temp_c=row.get("air_temp_c", float("nan")),
            season_day=season_day,
            latitude=row.get("lat", 35.0),
            max_depth_ft=row.get("max_depth_ft", 30.0),
            wind_speed_kph=row.get("wind_speed_kph", float("nan")),
            wind_dir_degrees=_wind_cos_to_degrees(row.get("wind_dir_cos", float("nan"))),
            lake_area_acres=row.get("area_acres", 0.0) or 0.0,
            lake_shore_dev=row.get("shore_dev", 1.0) or 1.0,
            pressure_mb=row.get("pressure_mb", float("nan")),
            pressure_delta_6h=row.get("pressure_delta_6h", float("nan")),
            solunar_score=row.get("solunar_score", float("nan")),
            gage_height_ft=row.get("gage_height_ft", float("nan")),
            gage_height_7d_mean=row.get("gage_height_7d_mean", float("nan")),
        )

        lake_features_list.append(features)
        if not lake_feature_cols:
            lake_feature_cols = list(features.keys())

    lake_df = pd.DataFrame(lake_features_list)
    for col in lake_df.columns:
        df[col] = lake_df[col].values

    print(f"  Added {len(lake_feature_cols)} lake features: {lake_feature_cols}")

    # ── 2. Merge temporal IV features ──
    iv_path = Path(iv_path)
    if iv_path.exists():
        print("\nMerging temporal IV features...")
        iv = pd.read_csv(iv_path)
        print(f"  IV records: {len(iv)}")

        # Normalize site IDs for matching
        df["_site_key"] = df["usgs_site_id"].apply(_normalize_site_id)
        iv["_site_key"] = iv["usgs_site_id"].apply(_normalize_site_id)

        # Merge on site + date
        iv_cols = [c for c in iv.columns if c not in ("usgs_site_id", "date", "_site_key", "iv_readings")]
        merge_cols = ["_site_key", "date"] + iv_cols

        df = df.merge(
            iv[merge_cols].rename(columns={c: f"iv_{c}" if not c.startswith("iv_") and c not in ("_site_key", "date") else c for c in merge_cols}),
            on=["_site_key", "date"],
            how="left",
        )
        df = df.drop(columns=["_site_key"], errors="ignore")

        iv_matched = df[[c for c in df.columns if c.startswith("iv_")]].notna().any(axis=1).sum()
        print(f"  Matched {iv_matched}/{len(df)} rows with IV features")
    else:
        print(f"\n  No IV features file at {iv_path}")

    # ── 3. Compute interaction features ──
    print("\nComputing interaction features...")

    # Wind × Lake size interaction
    if "wind_fetch_score" in df.columns and "windblown_quality" in df.columns:
        df["wind_lake_interaction"] = df["wind_fetch_score"] * df["windblown_quality"]

    # Pressure × Solunar interaction (both matter more on lakes)
    if "pressure_fishing_score" in df.columns and "lake_solunar_boost" in df.columns:
        df["pressure_solunar_interaction"] = df["pressure_fishing_score"] * df["lake_solunar_boost"]

    # Temperature stability × depth interaction
    if "thermal_stability" in df.columns and "max_depth_ft" in df.columns:
        df["depth_stability_interaction"] = df["thermal_stability"] * np.log1p(df["max_depth_ft"])

    print(f"  Added 3 interaction features")

    # ── 4. Summary ──
    print(f"\n=== v5 Dataset ===")
    print(f"  Rows: {len(df)}")
    print(f"  Columns: {len(df.columns)}")
    print(f"  New columns: {len(df.columns) - len(pd.read_csv(v4_path).columns)}")

    # Save
    df.to_csv(output_path, index=False)
    print(f"\n  Saved to {output_path}")

    return df


def _normalize_site_id(val) -> str:
    """Normalize USGS site ID to 8-digit zero-padded string."""
    try:
        return str(int(float(val))).zfill(8)
    except (ValueError, TypeError):
        return str(val).strip()


def _wind_cos_to_degrees(cos_val) -> float:
    """Convert wind_dir_cos back to approximate degrees."""
    if cos_val != cos_val:  # NaN
        return float("nan")
    try:
        return np.degrees(np.arccos(np.clip(cos_val, -1, 1)))
    except Exception:
        return float("nan")


if __name__ == "__main__":
    build_v5()
