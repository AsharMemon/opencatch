"""Build v6 dataset with satellite water temps, IV v2, gap-filled weather, and biology features.

v6 adds:
1. Satellite-derived water temperature estimates (gap-fill water_temp_c)
2. USGS IV v2 temporal features (finer-grained discharge/gage/temp dynamics)
3. Open-Meteo gap-filled air_temp, pressure, wind (when checkpoint exists)
4. Fish biology features (spawn timing, metabolic rate, feeding activity)
5. Location quality features (data richness, historical consistency)
6. Moon phase from ephemeris calculation
7. Additional interaction features combining new signals
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# ── Optional feature modules (may not exist yet) ──
try:
    from castline.validation.features.fish_biology import compute_fish_biology_features

    _HAS_FISH_BIOLOGY = True
except ImportError:
    _HAS_FISH_BIOLOGY = False

try:
    from castline.validation.features.location_quality import compute_location_quality_features

    _HAS_LOCATION_QUALITY = True
except ImportError:
    _HAS_LOCATION_QUALITY = False


# ────────────────────────────────────────────────────────────
# Utilities
# ────────────────────────────────────────────────────────────

def _normalize_site_id(val) -> str:
    """Normalize USGS site ID to 8-digit zero-padded string."""
    try:
        return str(int(float(val))).zfill(8)
    except (ValueError, TypeError):
        return str(val).strip()


def compute_moon_phase(date_str: str) -> dict:
    """Compute moon phase metrics from a date string.

    Uses the synodic month (29.53 days) from a known new moon reference
    (2000-01-06 18:14 UTC).

    Returns dict with moon_phase (0-1), moon_phase_sin, moon_phase_cos,
    and moon_illumination_pct.
    """
    SYNODIC_MONTH = 29.530588853
    # Known new moon: 2000-01-06 18:14 UTC
    REFERENCE_NEW_MOON = pd.Timestamp("2000-01-06 18:14", tz="UTC")

    try:
        dt = pd.to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        days_since = (dt - REFERENCE_NEW_MOON).total_seconds() / 86400.0
        phase = (days_since % SYNODIC_MONTH) / SYNODIC_MONTH  # 0=new, 0.5=full
        angle = 2.0 * math.pi * phase
        # Illumination peaks at full moon (phase=0.5)
        illumination = (1.0 - math.cos(angle)) / 2.0 * 100.0
        return {
            "moon_phase": phase,
            "moon_phase_sin": math.sin(angle),
            "moon_phase_cos": math.cos(angle),
            "moon_illumination_pct": illumination,
        }
    except Exception:
        return {
            "moon_phase": float("nan"),
            "moon_phase_sin": float("nan"),
            "moon_phase_cos": float("nan"),
            "moon_illumination_pct": float("nan"),
        }


def _print_coverage(df: pd.DataFrame, columns: list[str], label: str = "") -> None:
    """Print coverage statistics for selected columns."""
    if label:
        print(f"\n  [{label}]")
    for col in columns:
        if col in df.columns:
            n_valid = df[col].notna().sum()
            pct = n_valid / len(df) * 100 if len(df) > 0 else 0.0
            print(f"    {col}: {n_valid}/{len(df)} ({pct:.1f}%)")


# ────────────────────────────────────────────────────────────
# Main build
# ────────────────────────────────────────────────────────────

def build_v6(
    v5_path: str = "castline/validation/data/assembled/validation_dataset_v5.csv",
    satellite_path: str = "castline/validation/data/raw/satellite_water_temps.csv",
    iv_v2_path: str = "castline/validation/data/raw/usgs_iv_features_v2.csv",
    gapfill_path: str = "castline/validation/data/assembled/validation_dataset_v6_gapfilled.checkpoint.csv",
    output_path: str = "castline/validation/data/assembled/validation_dataset_v6.csv",
) -> pd.DataFrame:
    """Build v6 dataset from v5 + satellite temps + IV v2 + gap-fill + biology."""

    print("=" * 60)
    print("Building v6 dataset")
    print("=" * 60)

    # ── Load v5 ──
    print("\n1. Loading v5 dataset...")
    df = pd.read_csv(v5_path)
    v5_cols = list(df.columns)
    print(f"   v5: {len(df)} rows, {len(df.columns)} columns")

    key_cols = ["water_temp_c", "air_temp_c", "pressure_mb", "wind_speed_kph", "median_weight_lb"]
    _print_coverage(df, key_cols, "v5 baseline coverage")

    # ── 2. Merge satellite water temps ──
    sat_file = Path(satellite_path)
    if sat_file.exists():
        print("\n2. Merging satellite water temperatures...")
        sat = pd.read_csv(sat_file)
        print(f"   Satellite records: {len(sat)}")

        # Build merge keys: rounded lat/lon + date
        df["_sat_lat"] = df["lat"].round(2)
        df["_sat_lon"] = df["lon"].round(2)
        df["_sat_date"] = df["date"].astype(str)

        sat["_sat_lat"] = sat["lat"].round(2)
        sat["_sat_lon"] = sat["lon"].round(2)
        sat["_sat_date"] = sat["date"].astype(str)

        # Select satellite columns to merge (avoid collisions)
        sat_merge_cols = ["_sat_lat", "_sat_lon", "_sat_date"]
        sat_feature_cols = [
            c for c in sat.columns
            if c not in ("lat", "lon", "date", "location", "_sat_lat", "_sat_lon", "_sat_date")
        ]
        # Prefix satellite cols to avoid collision
        sat_renamed = {}
        for c in sat_feature_cols:
            new_name = f"sat_{c}" if not c.startswith("sat_") else c
            sat_renamed[c] = new_name

        sat_subset = sat[sat_merge_cols + sat_feature_cols].copy()
        sat_subset = sat_subset.rename(columns=sat_renamed)
        sat_subset = sat_subset.drop_duplicates(subset=["_sat_lat", "_sat_lon", "_sat_date"])

        pre_cols = len(df.columns)
        df = df.merge(sat_subset, on=["_sat_lat", "_sat_lon", "_sat_date"], how="left")
        df = df.drop(columns=["_sat_lat", "_sat_lon", "_sat_date"], errors="ignore")

        sat_new_cols = [c for c in df.columns if c not in v5_cols and c.startswith("sat_")]
        sat_matched = df[sat_new_cols].notna().any(axis=1).sum() if sat_new_cols else 0
        print(f"   Matched {sat_matched}/{len(df)} rows with satellite data")
        print(f"   Added columns: {sat_new_cols}")

        # Gap-fill water_temp_c with satellite estimate
        est_col = "sat_estimated_water_temp_c"
        if est_col in df.columns:
            water_gaps_before = df["water_temp_c"].isna().sum()
            df["water_temp_c"] = df["water_temp_c"].fillna(df[est_col])
            water_gaps_after = df["water_temp_c"].isna().sum()
            filled = water_gaps_before - water_gaps_after
            print(f"   Gap-filled water_temp_c: {filled} rows ({water_gaps_before} -> {water_gaps_after} missing)")
    else:
        print(f"\n2. No satellite file at {sat_file} — skipping")

    # ── 3. Merge USGS IV v2 temporal features ──
    iv_file = Path(iv_v2_path)
    if iv_file.exists():
        print("\n3. Merging USGS IV v2 temporal features...")
        iv = pd.read_csv(iv_file)
        print(f"   IV v2 records: {len(iv)}")

        # Normalize site IDs
        df["_site_key"] = df["usgs_site_id"].apply(_normalize_site_id)
        iv["_site_key"] = iv["usgs_site_id"].apply(_normalize_site_id)
        iv["date"] = iv["date"].astype(str)
        df["date"] = df["date"].astype(str)

        # Determine which IV columns are new (not already in df)
        existing_cols = set(df.columns)
        iv_feature_cols = [
            c for c in iv.columns
            if c not in ("usgs_site_id", "date", "_site_key", "iv_readings")
            and c not in existing_cols
        ]

        if iv_feature_cols:
            iv_subset = iv[["_site_key", "date"] + iv_feature_cols].copy()
            iv_subset = iv_subset.drop_duplicates(subset=["_site_key", "date"])

            df = df.merge(iv_subset, on=["_site_key", "date"], how="left")

            iv_matched = df[iv_feature_cols].notna().any(axis=1).sum()
            print(f"   Matched {iv_matched}/{len(df)} rows with IV v2 features")
            print(f"   Added {len(iv_feature_cols)} new IV columns")
        else:
            print("   All IV v2 columns already present — skipping merge")

        df = df.drop(columns=["_site_key"], errors="ignore")
    else:
        print(f"\n3. No IV v2 file at {iv_file} — skipping")

    # ── 4. Merge Open-Meteo gap-filled data ──
    gapfill_file = Path(gapfill_path)
    if gapfill_file.exists():
        print("\n4. Merging Open-Meteo gap-filled checkpoint...")
        gf = pd.read_csv(gapfill_file)
        print(f"   Gap-fill checkpoint: {len(gf)} rows")

        # Merge on rounded lat/lon + date
        df["_lat_r"] = df["lat"].round(2)
        df["_lon_r"] = df["lon"].round(2)
        gf["_lat_r"] = gf["lat"].round(2)
        gf["_lon_r"] = gf["lon"].round(2)

        om_cols = [c for c in gf.columns if c.startswith("om_")]
        # Drop any om_ columns already in df to avoid _x/_y suffixes
        existing_om = [c for c in om_cols if c in df.columns]
        if existing_om:
            df = df.drop(columns=existing_om, errors="ignore")

        merge_df = gf[["_lat_r", "_lon_r", "date"] + om_cols].drop_duplicates(
            subset=["_lat_r", "_lon_r", "date"], keep="last"
        )
        df = df.merge(merge_df, on=["_lat_r", "_lon_r", "date"], how="left")
        df = df.drop(columns=["_lat_r", "_lon_r"], errors="ignore")

        om_matched = df[om_cols].notna().any(axis=1).sum()
        print(f"   Matched {om_matched}/{len(df)} rows with Open-Meteo data")
        print(f"   Added {len(om_cols)} om_ columns")

        # Now fill gaps in core features using om_ data
        fill_map = {
            "air_temp_c": "om_air_temp_mean",
            "pressure_mb": "om_pressure_msl",
            "wind_speed_kph": "om_wind_max_kph",
            "water_temp_c": "om_est_water_temp",
        }
        for target_col, om_col in fill_map.items():
            if target_col in df.columns and om_col in df.columns:
                gaps_before = df[target_col].isna().sum()
                df[target_col] = df[target_col].fillna(df[om_col])
                gaps_after = df[target_col].isna().sum()
                filled = gaps_before - gaps_after
                if filled > 0:
                    print(f"   Gap-filled {target_col}: {filled} rows ({gaps_before} -> {gaps_after} missing)")
    else:
        print(f"\n4. No gap-fill checkpoint at {gapfill_file} — skipping")

    # ── 5. Fish biology features ──
    if _HAS_FISH_BIOLOGY:
        print("\n5. Computing fish biology features...")
        bio_features_list = []
        for _, row in df.iterrows():
            doy = 180
            try:
                doy = pd.to_datetime(row.get("date", "")).dayofyear
            except Exception:
                pass
            feats = compute_fish_biology_features(
                water_temp_c=float(row.get("water_temp_c", float("nan"))),
                air_temp_c=float(row.get("air_temp_c", float("nan"))),
                pressure_mb=float(row.get("pressure_mb", float("nan"))),
                pressure_delta_6h=float(row.get("pressure_delta_6h", float("nan"))),
                pressure_delta_24h=float(row.get("om_pressure_delta_24h", float("nan"))),
                wind_speed_kph=float(row.get("wind_speed_kph", float("nan"))),
                cloud_cover_pct=float(row.get("cloud_cover_pct", float("nan"))),
                day_of_year=int(doy),
                latitude=float(row.get("lat", float("nan"))),
                humidity_pct=float(row.get("om_humidity", float("nan"))),
                precip_mm=float(row.get("precip_24h_mm", 0.0) or 0.0),
                dissolved_oxygen_mgL=float(row.get("dissolved_oxygen_mgL", float("nan"))),
                moon_phase=float(row.get("moon_phase", float("nan"))),
            )
            bio_features_list.append(feats)
        bio_df = pd.DataFrame(bio_features_list)
        bio_cols = list(bio_df.columns)
        for col in bio_cols:
            df[col] = bio_df[col].values
        print(f"   Added {len(bio_cols)} fish biology features: {bio_cols}")
    else:
        print("\n5. Fish biology module not available — skipping")

    # ── 6. Location quality features ──
    if _HAS_LOCATION_QUALITY:
        print("\n6. Computing location quality features...")
        # Load creel data for regional quality estimation
        creel_path = Path(v5_path).parents[1] / "raw" / "creel_gnn_locations.csv"
        creel_df = pd.read_csv(creel_path) if creel_path.exists() else None
        if creel_df is not None:
            print(f"   Loaded {len(creel_df)} creel survey locations")

        lq_features_list = []
        for _, row in df.iterrows():
            feats = compute_location_quality_features(
                lat=float(row.get("lat", float("nan"))),
                lon=float(row.get("lon", float("nan"))),
                area_acres=float(row.get("area_acres", float("nan"))),
                max_depth_ft=float(row.get("max_depth_ft", float("nan"))),
                shore_dev=float(row.get("shore_dev", float("nan"))),
                is_lake=int(row.get("is_lake", 0) or 0),
                creel_data=creel_df,
            )
            lq_features_list.append(feats)
        lq_df = pd.DataFrame(lq_features_list)
        lq_cols = list(lq_df.columns)
        for col in lq_cols:
            df[col] = lq_df[col].values
        print(f"   Added {len(lq_cols)} location quality features: {lq_cols}")
    else:
        print("\n6. Location quality module not available — skipping")

    # ── 7. Recompute moon phase (ephemeris-based) ──
    print("\n7. Computing moon phase (ephemeris)...")
    moon_data = df["date"].apply(compute_moon_phase).apply(pd.Series)
    # Overwrite existing moon columns with more accurate computation
    for col in moon_data.columns:
        df[col] = moon_data[col].values
    print(f"   Updated moon columns: {list(moon_data.columns)}")

    # ── 8. Interaction features ──
    print("\n8. Computing interaction features...")
    n_interactions = 0

    # Water temp × spawn phase: warm water during spawn phase = strong signal
    if "water_temp_c" in df.columns and "spawn_phase_score" in df.columns:
        df["water_temp_spawn_interaction"] = df["water_temp_c"] * df["spawn_phase_score"]
        n_interactions += 1

    # Satellite air temp trend × water temp delta: weather-driven temp changes
    if "sat_air_temp_trend_7d" in df.columns and "temp_delta_24h_c" in df.columns:
        df["air_trend_water_delta_interaction"] = (
            df["sat_air_temp_trend_7d"] * df["temp_delta_24h_c"]
        )
        n_interactions += 1

    # Solar radiation × cloud cover: available light
    if "sat_solar_radiation_mj" in df.columns and "cloud_cover_pct" in df.columns:
        df["solar_cloud_interaction"] = (
            df["sat_solar_radiation_mj"] * (1.0 - df["cloud_cover_pct"] / 100.0)
        )
        n_interactions += 1

    # Moon illumination × solunar score: combined lunar influence
    if "moon_illumination_pct" in df.columns and "solunar_score" in df.columns:
        df["moon_solunar_interaction"] = (
            df["moon_illumination_pct"] / 100.0 * df["solunar_score"]
        )
        n_interactions += 1

    # Discharge variability × gage range: hydrological instability
    for cv_col, range_col in [
        ("discharge_cfs_cv_24h", "gage_height_ft_range_72h"),
        ("iv_discharge_cfs_cv_24h", "iv_gage_height_ft_range_72h"),
    ]:
        if cv_col in df.columns and range_col in df.columns:
            df["hydro_instability"] = df[cv_col] * df[range_col]
            n_interactions += 1
            break

    # Thermal stability × wind fetch: stratification disruption potential
    if "thermal_stability" in df.columns and "wind_speed_kph" in df.columns:
        df["wind_mixing_potential"] = (
            df["thermal_stability"] * df["wind_speed_kph"]
        )
        n_interactions += 1

    # Pressure trend × front phase encoding
    if "pressure_delta_6h" in df.columns and "pressure_fishing_score" in df.columns:
        df["pressure_trend_quality"] = df["pressure_delta_6h"] * df["pressure_fishing_score"]
        n_interactions += 1

    print(f"   Added {n_interactions} interaction features")

    # ── 9. Final coverage report ──
    print("\n" + "=" * 60)
    print("v6 Dataset Summary")
    print("=" * 60)
    print(f"   Rows: {len(df)}")
    print(f"   Columns: {len(df.columns)} (was {len(v5_cols)} in v5, +{len(df.columns) - len(v5_cols)} new)")

    new_cols = [c for c in df.columns if c not in v5_cols]
    print(f"   New columns: {new_cols}")

    core_cols = [
        "median_weight_lb", "water_temp_c", "air_temp_c", "pressure_mb",
        "wind_speed_kph", "discharge_cfs", "gage_height_ft",
        "moon_illumination_pct", "solunar_score",
    ]
    _print_coverage(df, core_cols, "Final coverage — core columns")

    sat_cols_final = [c for c in df.columns if c.startswith("sat_")]
    if sat_cols_final:
        _print_coverage(df, sat_cols_final, "Final coverage — satellite columns")

    interaction_cols = [c for c in df.columns if c.endswith("_interaction") or c in ("hydro_instability", "wind_mixing_potential", "pressure_trend_quality")]
    if interaction_cols:
        _print_coverage(df, interaction_cols, "Final coverage — interaction features")

    # Target column sanity check
    target_valid = df["median_weight_lb"].notna().sum()
    print(f"\n   Target (median_weight_lb): {target_valid}/{len(df)} valid ({target_valid / len(df) * 100:.1f}%)")

    # ── Save ──
    df.to_csv(output_path, index=False)
    print(f"\n   Saved to {output_path}")

    return df


if __name__ == "__main__":
    build_v6()
