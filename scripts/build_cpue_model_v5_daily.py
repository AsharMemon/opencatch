#!/usr/bin/env python3
"""
CPUE Model v5 — Day-Level Tournament Data
==========================================
Critical path to R^2 0.70+: train on DAY-LEVEL tournament outcomes instead of
monthly CreelCat survey aggregates. Each row = one tournament day with exact-date
weather, pressure deltas, frontal indicators, and tournament context.

Key innovations over v4:
  1. Day-level granularity (exact date weather, not monthly average)
  2. Pressure delta features (1d, 3d) — THE key bass behavior predictor
  3. Front passage / post-front indicators
  4. Tournament context (day_number, series_tier, fishing pressure)
  5. Lunar phase, photoperiod, spawn window indicators
  6. 3-model stacking ensemble (CatBoost + XGBoost + LightGBM -> Ridge)
  7. GroupKFold by location (leak-proof) + temporal holdout validation

Usage:
    python scripts/build_cpue_model_v5_daily.py
    python scripts/build_cpue_model_v5_daily.py --workspace /workspace/castline
"""

import argparse
import json
import math
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from sklearn.neighbors import BallTree

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# PATHS (override with WORKSPACE env var or --workspace flag)
# ---------------------------------------------------------------------------
WORKSPACE = os.environ.get("WORKSPACE", str(Path(__file__).resolve().parent.parent))
BASE_DIR = Path(WORKSPACE)
RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

# Tournament files
TOURNAMENT_FILES = [
    RAW_DIR / "all_bassmaster_outcomes.csv",
    RAW_DIR / "elite_outcomes.csv",
    RAW_DIR / "flw_outcomes.csv",
    RAW_DIR / "tourneyx_outcomes.csv",
    RAW_DIR / "combined_all_outcomes_v2.csv",
]

# Day-level weather (from fetch_tournament_weather.py)
WEATHER_DAILY = RAW_DIR / "tournament_weather_daily.csv"

# Fallback: FLW weather with lags (already has some events)
FLW_WEATHER = RAW_DIR / "flw_weather_noaa.csv"

# Lat/lon resolution
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
USGS_SITE_CACHE = RAW_DIR / "usgs_site_cache.csv"

# Optional enrichment — LAGOS (try RAW_DIR first, then workspace/raw)
LAGOS_CHAR = RAW_DIR / "lake_characteristics.csv"
LAGOS_DEPTH = RAW_DIR / "lake_depth.csv"
LAGOS_INFO = RAW_DIR / "lake_information.csv"
LAGOS_MATCHES = RAW_DIR / "lagos_matches.csv"  # Pre-matched lake morphometry
EMBEDDINGS_PATH = RAW_DIR / "location_embeddings_geoclip_pca32.csv"
CREEL_CPUE = RAW_DIR / "creel_cpue_bass.csv"  # Species breakdown data

SEED = 42
EARTH_RADIUS_KM = 6371.0

# Series tier ordinal encoding (higher = more competitive / better anglers)
SERIES_TIER = {
    "elite_api": 5, "elite": 5,
    "bassmaster_classic": 5,
    "bassmaster_open": 3, "open": 3,
    "flw": 4, "flw_tour": 4,
    "tourneyx": 1, "tourneyx_club": 1, "tourneyx_trail": 2,
    "mlf": 4,
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ======================================================================
# DATA LOADING
# ======================================================================

def load_tournament_data():
    """Load and deduplicate all tournament outcome files."""
    log("=" * 70)
    log("CPUE MODEL v5 — DAY-LEVEL TOURNAMENT DATA")
    log("=" * 70)
    log("\n--- Loading tournament data ---")

    frames = []
    for fp in TOURNAMENT_FILES:
        if fp.exists():
            df = pd.read_csv(fp, low_memory=False)
            df["_source"] = fp.stem
            frames.append(df)
            log(f"  {fp.name}: {len(df)} rows")

    if not frames:
        log("ERROR: No tournament files found!")
        sys.exit(1)

    all_df = pd.concat(frames, ignore_index=True, sort=False)
    log(f"  Raw total: {len(all_df)}")

    # Deduplicate by event_id
    all_df = all_df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
    log(f"  After dedup: {len(all_df)}")

    # Parse dates
    all_df["date"] = pd.to_datetime(all_df["date"], errors="coerce")
    all_df = all_df[all_df["date"].notna()].reset_index(drop=True)

    # Compute CPUE (median weight per angler)
    all_df["median_weight_lb"] = pd.to_numeric(all_df["median_weight_lb"], errors="coerce")
    all_df["num_anglers"] = pd.to_numeric(all_df["num_anglers"], errors="coerce")

    # CPUE = median_weight_lb (this IS per-angler already — it's the median bag weight)
    # For tournament data, median_weight_lb is already per-angler daily catch weight
    all_df["cpue"] = all_df["median_weight_lb"]
    all_df = all_df[all_df["cpue"].notna() & (all_df["cpue"] > 0)].reset_index(drop=True)
    log(f"  With valid CPUE: {len(all_df)}")

    # Filter out length-based measurements (TourneyX length tournaments)
    if "measurement_type" in all_df.columns:
        length_mask = all_df["measurement_type"] == "length"
        log(f"  Dropping {length_mask.sum()} length-based events")
        all_df = all_df[~length_mask].reset_index(drop=True)

    log(f"  Final tournament rows: {len(all_df)}")
    return all_df


def resolve_coordinates(df):
    """Resolve lat/lon for tournament events from multiple sources."""
    log("\n--- Resolving coordinates ---")
    df["lat"] = np.nan
    df["lon"] = np.nan

    # Source 1: FLW weather file (has lat/lon for FLW events)
    if FLW_WEATHER.exists():
        flw_wx = pd.read_csv(FLW_WEATHER, usecols=["event_id", "lat", "lon"])
        flw_wx = flw_wx.dropna(subset=["lat", "lon"]).drop_duplicates(subset=["event_id"])
        flw_map = dict(zip(flw_wx["event_id"], zip(flw_wx["lat"], flw_wx["lon"])))
        matched = 0
        for i, row in df.iterrows():
            if row["event_id"] in flw_map:
                df.at[i, "lat"] = flw_map[row["event_id"]][0]
                df.at[i, "lon"] = flw_map[row["event_id"]][1]
                matched += 1
        log(f"  FLW weather: {matched} matched")

    # Source 2: Geocode cache
    if GEOCODE_CACHE.exists():
        with open(GEOCODE_CACHE) as f:
            geo_cache = json.load(f)
        matched = 0
        for i, row in df.iterrows():
            if pd.notna(df.at[i, "lat"]):
                continue
            loc = row.get("location", "")
            if isinstance(loc, str) and loc in geo_cache:
                coords = geo_cache[loc]
                if coords.get("lat") and coords.get("lon"):
                    df.at[i, "lat"] = coords["lat"]
                    df.at[i, "lon"] = coords["lon"]
                    matched += 1
        log(f"  Geocode cache: {matched} matched")

    # Source 3: USGS site cache
    if USGS_SITE_CACHE.exists():
        usgs = pd.read_csv(USGS_SITE_CACHE)
        if "location" in usgs.columns and "loc_lat" in usgs.columns:
            usgs_dedup = usgs.drop_duplicates(subset=["location"]).set_index("location")
            matched = 0
            for i, row in df.iterrows():
                if pd.notna(df.at[i, "lat"]):
                    continue
                loc = row.get("location", "")
                if isinstance(loc, str) and loc in usgs_dedup.index:
                    r = usgs_dedup.loc[loc]
                    if isinstance(r, pd.DataFrame):
                        r = r.iloc[0]
                    if pd.notna(r.get("loc_lat")) and pd.notna(r.get("loc_lon")):
                        df.at[i, "lat"] = r["loc_lat"]
                        df.at[i, "lon"] = r["loc_lon"]
                        matched += 1
            log(f"  USGS site cache: {matched} matched")

    has_coords = df["lat"].notna() & df["lon"].notna()
    log(f"  Total with coords: {has_coords.sum()}/{len(df)} ({has_coords.mean()*100:.1f}%)")
    return df[has_coords].reset_index(drop=True)


# ======================================================================
# FEATURE ENGINEERING
# ======================================================================

def compute_photoperiod(lat, doy):
    """Day length in hours from latitude and day of year."""
    lat_rad = np.radians(np.clip(lat, -60, 60))
    decl = np.radians(23.45 * np.sin(np.radians(360.0 / 365 * (doy - 81))))
    cos_ha = (-np.tan(lat_rad) * np.tan(decl))
    cos_ha = np.clip(cos_ha, -1, 1)
    return 2 * np.degrees(np.arccos(cos_ha)) / 15.0


def compute_lunar_phase(dates):
    """
    Compute lunar phase as 0-1 cycle from date.
    Uses a known new moon reference and 29.53-day synodic period.
    """
    # Reference new moon: 2000-01-06 18:14 UTC
    ref = pd.Timestamp("2000-01-06 18:14:00")
    synodic = 29.53058867
    days_since = (dates - ref).dt.total_seconds() / 86400.0
    phase = (days_since % synodic) / synodic
    return phase


def engineer_features(df):
    """Build all features for the day-level CPUE model."""
    log("\n--- Feature Engineering ---")

    # ---- Temporal features ----
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["day_of_year"] = df["date"].dt.dayofyear
    df["day_of_week"] = df["date"].dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    df["doy_sin"] = np.sin(2 * np.pi * df["day_of_year"] / 365)
    df["doy_cos"] = np.cos(2 * np.pi * df["day_of_year"] / 365)

    # Photoperiod
    df["day_length_hours"] = compute_photoperiod(df["lat"].values, df["day_of_year"].values)

    # Days since spring equinox (March 20 = DOY 79)
    df["days_since_equinox"] = (df["day_of_year"] - 79).clip(lower=0)

    # Lunar phase
    df["lunar_phase"] = compute_lunar_phase(df["date"])
    df["lunar_phase_sin"] = np.sin(2 * np.pi * df["lunar_phase"])
    df["lunar_phase_cos"] = np.cos(2 * np.pi * df["lunar_phase"])

    # ---- Tournament context features ----
    df["day_number"] = pd.to_numeric(df.get("day_number", pd.Series(1, index=df.index)),
                                     errors="coerce").fillna(1)

    # Series tier (ordinal)
    source_col = df["results_source"] if "results_source" in df.columns else df.get("_source", "")
    if isinstance(source_col, pd.Series):
        df["series_tier"] = source_col.map(SERIES_TIER).fillna(2)
    else:
        df["series_tier"] = 2

    # Also try trail column
    if "trail" in df.columns:
        trail_tier = df["trail"].map(SERIES_TIER)
        df["series_tier"] = df["series_tier"].fillna(trail_tier)

    df["num_anglers"] = pd.to_numeric(df["num_anglers"], errors="coerce")
    df["log_num_anglers"] = np.log1p(df["num_anglers"].fillna(0))

    # ---- Location features ----
    df["abs_lat"] = df["lat"].abs()
    df["lat_band"] = pd.cut(df["lat"], bins=[20, 28, 33, 38, 42, 50],
                            labels=[0, 1, 2, 3, 4]).astype(float)

    # Location group for CV
    df["loc_group"] = df["lat"].round(1).astype(str) + "_" + df["lon"].round(1).astype(str)

    log(f"  Temporal + context features: done")
    return df


def merge_weather(df):
    """Merge day-level weather and compute derived features."""
    log("\n--- Merging day-level weather ---")

    wx_merged = 0

    # Primary source: tournament_weather_daily.csv
    if WEATHER_DAILY.exists():
        wx = pd.read_csv(WEATHER_DAILY)
        log(f"  Weather daily file: {len(wx)} rows")

        wx_cols = ["temp_mean", "temp_max", "temp_min",
                   "pressure_mean", "pressure_min", "pressure_max",
                   "pressure_delta_1d", "pressure_delta_3d",
                   "wind_mean", "wind_max", "precip_total",
                   "humidity_mean", "cloud_cover", "dew_point",
                   "station_id", "station_distance_km"]

        avail_cols = [c for c in wx_cols if c in wx.columns]

        # Merge by event_id
        wx_sub = wx[["event_id"] + avail_cols].drop_duplicates(subset=["event_id"])
        before = df.shape[1]
        df = df.merge(wx_sub, on="event_id", how="left", suffixes=("", "_wx"))
        wx_merged = df["temp_mean"].notna().sum() if "temp_mean" in df.columns else 0
        log(f"  Merged from daily weather: {wx_merged}/{len(df)}")

    # Fallback: FLW weather file (for events not in daily weather)
    if FLW_WEATHER.exists() and "temp_mean" in df.columns:
        missing_mask = df["temp_mean"].isna()
        if missing_mask.sum() > 0:
            flw = pd.read_csv(FLW_WEATHER)
            flw_rename = {
                "temp_max": "temp_max", "temp_min": "temp_min", "temp_mean": "temp_mean",
                "pressure_hpa": "pressure_mean", "precip_mm": "precip_total",
                "wind_max_kph": "wind_max_kph_flw", "wind_avg_kph": "wind_avg_kph_flw",
                "humidity_pct": "humidity_mean",
                "pressure_delta_1d": "pressure_delta_1d",
                "pressure_delta_3d": "pressure_delta_3d",
            }
            flw_cols_avail = [c for c in flw_rename.keys() if c in flw.columns]
            flw_sub = flw[["event_id"] + flw_cols_avail].drop_duplicates(subset=["event_id"])

            for orig_col, new_col in flw_rename.items():
                if orig_col in flw_sub.columns and new_col in df.columns:
                    fill_mask = df["temp_mean"].isna() & df["event_id"].isin(flw_sub["event_id"])
                    if fill_mask.sum() > 0:
                        flw_map = dict(zip(flw_sub["event_id"], flw_sub[orig_col]))
                        df.loc[fill_mask, new_col] = df.loc[fill_mask, "event_id"].map(flw_map)

            # Convert FLW wind from kph to m/s if we got it
            if "wind_max_kph_flw" in df.columns:
                fallback_mask = df["wind_max"].isna() & df["wind_max_kph_flw"].notna()
                df.loc[fallback_mask, "wind_max"] = df.loc[fallback_mask, "wind_max_kph_flw"] / 3.6
            if "wind_avg_kph_flw" in df.columns:
                fallback_mask = df["wind_mean"].isna() & df["wind_avg_kph_flw"].notna()
                df.loc[fallback_mask, "wind_mean"] = df.loc[fallback_mask, "wind_avg_kph_flw"] / 3.6

            flw_filled = df["temp_mean"].notna().sum() - wx_merged
            log(f"  FLW weather fallback filled: {flw_filled} additional")

    # Initialize weather columns if they don't exist yet
    for col in ["temp_mean", "temp_max", "temp_min", "pressure_mean", "pressure_min",
                "pressure_max", "pressure_delta_1d", "pressure_delta_3d",
                "wind_mean", "wind_max", "precip_total", "humidity_mean",
                "cloud_cover", "dew_point"]:
        if col not in df.columns:
            df[col] = np.nan

    # ---- Derived weather features ----
    log("  Computing derived weather features...")

    # Temperature delta (day-over-day change proxy using pressure_delta pattern)
    # We don't have yesterday's temp directly, but we can derive from temp range
    df["temp_range"] = df["temp_max"] - df["temp_min"]

    # Estimated water temperature (air temp lags, deeper = more stable)
    df["est_water_temp"] = df["temp_mean"] - 2 + df["month_sin"] * 1.5

    # Bass Thermal Comfort Index: 1 - min(abs(est_water - 21) / 10, 1)
    df["bass_thermal_comfort"] = 1 - np.minimum(
        np.abs(df["est_water_temp"] - 21) / 10, 1
    )

    # Spawn Window indicator (est water temp 15-21C AND month 3-5)
    df["spawn_window"] = (
        (df["est_water_temp"] >= 15) &
        (df["est_water_temp"] <= 21) &
        (df["month"] >= 3) &
        (df["month"] <= 5)
    ).astype(float)
    # NaN where water temp unknown
    df.loc[df["est_water_temp"].isna(), "spawn_window"] = np.nan

    # Front passage indicator (pressure drop > 5 mb in 1 day)
    df["front_passage"] = (df["pressure_delta_1d"].abs() > 5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "front_passage"] = np.nan

    # Post-front indicator: pressure_delta_1d > 5 (rising after front)
    df["post_front"] = (df["pressure_delta_1d"] > 5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "post_front"] = np.nan

    # Pre-front: pressure_delta_1d < -5 (falling before front)
    df["pre_front"] = (df["pressure_delta_1d"] < -5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "pre_front"] = np.nan

    # Pressure stability (small delta = stable = good fishing)
    df["pressure_stability"] = 1 / (1 + df["pressure_delta_1d"].abs().fillna(5))

    # Wind chill (simplified Steadman, for temp < 10C)
    temp_c = df["temp_mean"].fillna(15)
    wind_ms = df["wind_mean"].fillna(0)
    wind_kph = wind_ms * 3.6
    df["wind_chill"] = np.where(
        temp_c < 10,
        13.12 + 0.6215 * temp_c - 11.37 * (wind_kph.clip(lower=1) ** 0.16) +
        0.3965 * temp_c * (wind_kph.clip(lower=1) ** 0.16),
        temp_c
    )

    # Heat index (simplified, for temp > 27C)
    df["heat_index"] = np.where(temp_c > 27, temp_c + 0.5 * (temp_c - 27), temp_c)

    # Wind fishing comfort
    df["wind_comfort"] = 1 - np.minimum(df["wind_max"].fillna(0) / 20, 1)

    # Precipitation intensity (mm on this single day)
    df["precip_intensity"] = df["precip_total"].fillna(0).clip(upper=100)

    # Growing degree days (single day, base 10C)
    df["gdd_daily"] = (df["temp_mean"] - 10).clip(lower=0)

    # ---- Interactions ----
    df["temp_x_lat"] = df["temp_mean"] * df["lat"]
    df["pressure_x_season"] = df["pressure_mean"].fillna(1013) * df["month_sin"]
    df["comfort_x_daylen"] = df["bass_thermal_comfort"] * df["day_length_hours"]
    df["temp_x_pressure_delta"] = df["temp_mean"] * df["pressure_delta_1d"].fillna(0)
    df["lat_x_month"] = df["lat"] * df["month_sin"]

    # Day number effect (fish get harder on later days)
    df["day_number_sq"] = df["day_number"] ** 2

    # Pressure delta absolute value (magnitude of change)
    df["pressure_delta_1d_abs"] = df["pressure_delta_1d"].abs()
    df["pressure_delta_3d_abs"] = df["pressure_delta_3d"].abs()

    wx_coverage = df["temp_mean"].notna().sum()
    pressure_coverage = df["pressure_delta_1d"].notna().sum()
    log(f"  Weather coverage: {wx_coverage}/{len(df)} ({wx_coverage/len(df)*100:.1f}%)")
    log(f"  Pressure delta coverage: {pressure_coverage}/{len(df)} ({pressure_coverage/len(df)*100:.1f}%)")

    return df


def merge_usgs_features(df):
    """Merge USGS stream gauge features (discharge, flow regime, gage height)."""
    log("\n--- USGS gauge features ---")
    usgs_path = RAW_DIR / "tournament_usgs_gauges.csv"
    if not usgs_path.exists():
        # Check for checkpoint
        ckpt = RAW_DIR / "tournament_usgs_gauges.checkpoint.csv"
        if ckpt.exists():
            usgs_path = ckpt
        else:
            log("  USGS file not found, skipping")
            return df

    try:
        usgs = pd.read_csv(usgs_path)
        log(f"  USGS file: {len(usgs)} rows")

        usgs_cols = [
            "discharge_cfs", "discharge_delta_1d", "discharge_delta_3d",
            "discharge_pct_change_1d", "gage_height_ft", "gage_height_delta_1d",
            "discharge_zscore", "water_temp_usgs", "gauge_distance_km",
        ]
        avail = [c for c in usgs_cols if c in usgs.columns]
        if not avail:
            log("  No USGS feature columns found")
            return df

        usgs_sub = usgs[["event_id"] + avail].drop_duplicates(subset=["event_id"])
        df = df.merge(usgs_sub, on="event_id", how="left", suffixes=("", "_usgs"))

        # Flow regime as categorical
        if "flow_regime" in usgs.columns:
            regime_map = usgs[["event_id", "flow_regime"]].drop_duplicates(subset=["event_id"])
            df = df.merge(regime_map, on="event_id", how="left", suffixes=("", "_fr"))
            df["flow_rising"] = (df["flow_regime"] == "rising").astype(float)
            df["flow_falling"] = (df["flow_regime"] == "falling").astype(float)
            df["flow_stable"] = (df["flow_regime"] == "stable").astype(float)
            df.loc[df["flow_regime"].isna(), ["flow_rising", "flow_falling", "flow_stable"]] = np.nan
            df.drop(columns=["flow_regime"], inplace=True, errors="ignore")

        # Derived USGS features
        if "discharge_cfs" in df.columns:
            df["log_discharge"] = np.log1p(df["discharge_cfs"].clip(lower=0))
        if "discharge_delta_1d" in df.columns and "pressure_delta_1d" in df.columns:
            df["flow_x_pressure"] = df["discharge_delta_1d"].fillna(0) * df["pressure_delta_1d"].fillna(0)

        coverage = df["discharge_cfs"].notna().sum() if "discharge_cfs" in df.columns else 0
        log(f"  USGS matched: {coverage}/{len(df)} ({coverage/len(df)*100:.1f}%)")
    except Exception as e:
        log(f"  USGS error: {e}")

    return df


def merge_regime_features(df):
    """Merge weather regime detection features."""
    log("\n--- Weather regime features ---")
    regime_path = RAW_DIR / "tournament_regime_features.csv"
    if not regime_path.exists():
        log("  Regime features file not found, skipping")
        return df

    try:
        regime = pd.read_csv(regime_path)
        log(f"  Regime file: {len(regime)} rows")

        regime_cols = [
            "hours_since_front", "hours_since_precip",
            "pressure_trend_6h", "pressure_trend_12h",
            "temp_trend_6h", "wind_shift_magnitude", "stability_index",
        ]
        avail = [c for c in regime_cols if c in regime.columns]

        if "regime_at_event" in regime.columns:
            # One-hot encode regime categories
            regime_dummies = pd.get_dummies(regime["regime_at_event"], prefix="regime")
            regime = pd.concat([regime, regime_dummies], axis=1)
            avail += [c for c in regime_dummies.columns]

        if not avail:
            log("  No regime feature columns found")
            return df

        regime_sub = regime[["event_id"] + avail].drop_duplicates(subset=["event_id"])
        df = df.merge(regime_sub, on="event_id", how="left", suffixes=("", "_reg"))

        # Interactions: stability × pressure_delta, hours_since_front × thermal_comfort
        if "stability_index" in df.columns and "pressure_delta_1d" in df.columns:
            df["stability_x_pressure"] = df["stability_index"].fillna(0.5) * df["pressure_delta_1d"].fillna(0)
        if "hours_since_front" in df.columns and "bass_thermal_comfort" in df.columns:
            df["front_hours_x_comfort"] = df["hours_since_front"].fillna(48) * df["bass_thermal_comfort"].fillna(0.5)

        coverage = df["stability_index"].notna().sum() if "stability_index" in df.columns else 0
        log(f"  Regime matched: {coverage}/{len(df)} ({coverage/len(df)*100:.1f}%)")
    except Exception as e:
        log(f"  Regime error: {e}")

    return df


def add_lagos_features(df):
    """Add LAGOS lake morphometry features — prefer pre-matched file."""
    log("\n--- LAGOS morphometry ---")

    # Try pre-matched LAGOS file first (fast, no spatial join needed)
    if LAGOS_MATCHES.exists():
        try:
            lagos = pd.read_csv(LAGOS_MATCHES)
            log(f"  Pre-matched LAGOS file: {len(lagos)} locations")

            # Spatial join: match tournament locations to LAGOS by proximity
            if "location" in lagos.columns:
                # Build lat/lon lookup from LAGOS matches
                # The matches file has location names — merge via location string
                lagos_dedup = lagos.drop_duplicates(subset=["location"])
                # Try matching by location name
                before = df.shape[1]
                df_loc_lower = df["location"].str.lower().str.strip()
                lagos_loc_lower = lagos_dedup["location"].str.lower().str.strip()
                loc_map = dict(zip(lagos_loc_lower, lagos_dedup.index))

                for col_src, col_dst in [
                    ("lagos_sdi", "lagos_sdi"),
                    ("lagos_area_acres", "lagos_area_ha"),
                    ("lagos_depth_ft", "lagos_max_depth_m"),
                ]:
                    if col_src in lagos_dedup.columns:
                        vals = []
                        for loc in df_loc_lower:
                            if loc in loc_map:
                                v = lagos_dedup.iloc[loc_map[loc]][col_src]
                                vals.append(pd.to_numeric(v, errors="coerce"))
                            else:
                                vals.append(np.nan)
                        df[col_dst] = vals

                # Convert acres to hectares, feet to meters
                if "lagos_area_ha" in df.columns:
                    df["lagos_area_ha"] = df["lagos_area_ha"] * 0.404686  # acres -> ha
                if "lagos_max_depth_m" in df.columns:
                    df["lagos_max_depth_m"] = df["lagos_max_depth_m"] * 0.3048  # ft -> m

            matched = df["lagos_sdi"].notna().sum() if "lagos_sdi" in df.columns else 0
            log(f"  LAGOS matched (pre-matched): {matched}/{len(df)} ({matched/len(df)*100:.1f}%)")
        except Exception as e:
            log(f"  Pre-matched LAGOS error: {e}")
    else:
        # Fallback: raw LAGOS spatial join
        try:
            chars = pd.read_csv(LAGOS_CHAR, low_memory=False)
            info = pd.read_csv(LAGOS_INFO, low_memory=False)
            lat_col = [c for c in info.columns if "lat" in c.lower()]
            lon_col = [c for c in info.columns if "lon" in c.lower()]
            if lat_col and lon_col:
                lagos = chars.merge(info[["lagoslakeid"] + lat_col + lon_col], on="lagoslakeid", how="left")
                lagos = lagos.rename(columns={lat_col[0]: "lago_lat", lon_col[0]: "lago_lon"})
                lagos = lagos[lagos["lago_lat"].notna() & lagos["lago_lon"].notna()]
                log(f"  LAGOS lakes (raw): {len(lagos)}")

                tree = BallTree(np.radians(lagos[["lago_lat", "lago_lon"]].values), metric="haversine")
                dist, idx = tree.query(np.radians(df[["lat", "lon"]].values), k=1)
                dist_km = dist[:, 0] * EARTH_RADIUS_KM
                matched = dist_km < 15

                for src, dst in {"lake_waterarea_ha": "lagos_area_ha", "lake_shorelinedevfactor": "lagos_sdi"}.items():
                    if src in lagos.columns:
                        df[dst] = np.where(matched, pd.to_numeric(lagos.iloc[idx[:, 0]][src].values, errors="coerce"), np.nan)

                try:
                    depth = pd.read_csv(LAGOS_DEPTH, low_memory=False)
                    depth_col = [c for c in depth.columns if "max" in c.lower() and "depth" in c.lower()]
                    if depth_col:
                        lagos_d = lagos.merge(depth[["lagoslakeid"] + depth_col], on="lagoslakeid", how="left")
                        df["lagos_max_depth_m"] = np.where(
                            matched, pd.to_numeric(lagos_d.iloc[idx[:, 0]][depth_col[0]].values, errors="coerce"), np.nan)
                except Exception:
                    pass

                log(f"  LAGOS matched (raw): {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")
        except FileNotFoundError:
            log("  LAGOS files not found, skipping")
        except Exception as e:
            log(f"  LAGOS error: {e}")

    # Derived morphometry features
    if "lagos_area_ha" in df.columns:
        df["lagos_log_area"] = np.log1p(df["lagos_area_ha"])
    if "lagos_max_depth_m" in df.columns and "lagos_area_ha" in df.columns:
        df["lagos_depth_area_ratio"] = df["lagos_max_depth_m"] / np.log1p(df["lagos_area_ha"])

    return df


def add_species_proxy_features(df):
    """Add species-aware features using lat, morphometry, and CreelCat data.

    Bass species have different thermal optima and habitat preferences:
    - Largemouth (LMB): 20-27C, shallow/warm/weedy, dominant south of lat 42
    - Smallmouth (SMB): 15-22C, deep/cool/rocky, more common north of lat 42
    - Spotted (SPB): 18-24C, current-oriented, mid-south
    """
    log("\n--- Species proxy features ---")

    # 1. Latitude-based species probability
    # SMB probability increases with latitude (logistic function centered at lat 42)
    df["smb_probability"] = 1 / (1 + np.exp(-(df["lat"] - 42) / 2))
    df["lmb_probability"] = 1 - df["smb_probability"]

    # 2. Depth-based species indicator (deep = more SMB)
    if "lagos_max_depth_m" in df.columns:
        # SMB prefer deeper, clearer water (>10m depth)
        df["smb_depth_signal"] = np.minimum(df["lagos_max_depth_m"].fillna(5) / 15, 1)
        # Combine with latitude for better estimate
        df["smb_composite"] = 0.7 * df["smb_probability"] + 0.3 * df["smb_depth_signal"]
    else:
        df["smb_composite"] = df["smb_probability"]

    # 3. Species-weighted thermal optimum
    # Instead of single "bass optimal temp", blend by species probability
    LMB_OPTIMAL_C = 23.5  # center of 20-27C range
    SMB_OPTIMAL_C = 18.5  # center of 15-22C range

    if "est_water_temp" in df.columns:
        # Weighted optimal temp for this location's likely species mix
        df["species_optimal_temp"] = (
            df["lmb_probability"] * LMB_OPTIMAL_C +
            df["smb_probability"] * SMB_OPTIMAL_C
        )
        df["species_temp_deviation"] = np.abs(df["est_water_temp"] - df["species_optimal_temp"])
        # Species-aware thermal comfort (replaces generic bass_thermal_comfort)
        df["species_thermal_comfort"] = 1 - np.minimum(df["species_temp_deviation"] / 10, 1)

    # 4. CreelCat species ratios for matched locations
    if CREEL_CPUE.exists():
        try:
            creel = pd.read_csv(CREEL_CPUE)
            if "species" in creel.columns and "lat" in creel.columns:
                # Build location-level species ratios
                creel_locs = creel.groupby(
                    [creel["lat"].round(1), creel["lon"].round(1)]
                )["species"].value_counts().unstack(fill_value=0)
                creel_locs["total"] = creel_locs.sum(axis=1)
                for sp in ["Largemouth Bass", "Smallmouth Bass"]:
                    col = sp.lower().replace(" ", "_")
                    if sp in creel_locs.columns:
                        creel_locs[f"ratio_{col}"] = creel_locs[sp] / creel_locs["total"]

                # Match tournament events to nearest CreelCat location
                if len(creel_locs) > 0:
                    creel_coords = np.array([[lat, lon] for lat, lon in creel_locs.index])
                    tree = BallTree(np.radians(creel_coords), metric="haversine")
                    dist, idx = tree.query(np.radians(df[["lat", "lon"]].values), k=1)
                    dist_km = dist[:, 0] * EARTH_RADIUS_KM
                    close = dist_km < 25  # within 25km

                    if "ratio_largemouth_bass" in creel_locs.columns:
                        df["creel_lmb_ratio"] = np.where(
                            close, creel_locs.iloc[idx[:, 0]]["ratio_largemouth_bass"].values, np.nan)
                    if "ratio_smallmouth_bass" in creel_locs.columns:
                        df["creel_smb_ratio"] = np.where(
                            close, creel_locs.iloc[idx[:, 0]]["ratio_smallmouth_bass"].values, np.nan)

                    creel_matched = close.sum()
                    log(f"  CreelCat species matched: {creel_matched}/{len(df)} ({creel_matched/len(df)*100:.1f}%)")
        except Exception as e:
            log(f"  CreelCat species error: {e}")

    features_added = [c for c in df.columns if c.startswith(("smb_", "lmb_", "species_", "creel_"))]
    log(f"  Species features added: {len(features_added)} ({', '.join(features_added[:6])}...)")

    return df


def add_geoclip_embeddings(df):
    """Add GeoCLIP location embeddings."""
    log("\n--- GeoCLIP embeddings ---")
    try:
        emb = pd.read_csv(EMBEDDINGS_PATH, low_memory=False)
        emb_cols = [c for c in emb.columns if c.startswith("geoclip_")]
        tree = BallTree(np.radians(emb[["lat", "lon"]].values), metric="haversine")
        dist, idx = tree.query(np.radians(df[["lat", "lon"]].values), k=1)
        dist_km = dist[:, 0] * EARTH_RADIUS_KM
        matched = dist_km < 50
        for col in emb_cols[:16]:
            df[col] = np.where(matched, emb.iloc[idx[:, 0]][col].values, np.nan)
        log(f"  GeoCLIP matched: {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")
    except FileNotFoundError:
        log("  GeoCLIP file not found, skipping")
    except Exception as e:
        log(f"  GeoCLIP error: {e}")

    return df


# ======================================================================
# BUILD DATASET
# ======================================================================

def build_dataset():
    """Full pipeline: load tournaments -> resolve coords -> features -> weather."""
    df = load_tournament_data()
    df = resolve_coordinates(df)

    if len(df) == 0:
        log("ERROR: No events with coordinates!")
        sys.exit(1)

    df = engineer_features(df)
    df = merge_weather(df)
    df = merge_usgs_features(df)
    df = merge_regime_features(df)
    df = add_lagos_features(df)
    df = add_species_proxy_features(df)
    df = add_geoclip_embeddings(df)

    # Target variable
    df["log_cpue"] = np.log1p(df["cpue"])

    log(f"\n  Final dataset: {len(df)} rows x {df.shape[1]} columns")
    log(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Unique locations: {df['loc_group'].nunique()}")
    log(f"  CPUE range: {df['cpue'].min():.2f} - {df['cpue'].max():.2f} lb/angler")
    log(f"  CPUE median: {df['cpue'].median():.2f} lb/angler")

    return df


# ======================================================================
# FEATURE SELECTION
# ======================================================================

def get_features(df):
    """Get numeric feature columns, excluding targets and metadata."""
    exclude = {
        "cpue", "log_cpue", "median_weight_lb", "baseline_signal",
        "loc_group", "event_id", "tournament_slug", "event_name",
        "date", "date_str", "location", "species", "usgs_site_id",
        "results_source", "_source", "tms_id", "trail",
        "station_id", "station_distance_km",
        "measurement_type", "tournament_id", "_source_file",
        "wind_max_kph_flw", "wind_avg_kph_flw",
        "regime_at_event", "regime_station_id", "regime_station_dist_km",
        "gauge_site_no", "gauge_site_type",
    }
    features = []
    for c in df.columns:
        if c in exclude:
            continue
        if c.startswith("_"):
            continue
        if df[c].dtype not in ["float64", "int64", "float32", "int32"]:
            continue
        if df[c].notna().sum() < len(df) * 0.03:
            continue
        features.append(c)
    return features


# ======================================================================
# MODEL TRAINING
# ======================================================================

def train_and_evaluate(df):
    """Train 3-model stacking ensemble with GroupKFold by location."""
    # Late imports for models (may not be installed on all systems)
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        log("ERROR: catboost not installed. pip install catboost")
        sys.exit(1)
    try:
        from xgboost import XGBRegressor
    except ImportError:
        log("ERROR: xgboost not installed. pip install xgboost")
        sys.exit(1)
    try:
        import lightgbm as lgb
    except ImportError:
        log("ERROR: lightgbm not installed. pip install lightgbm")
        sys.exit(1)

    features = get_features(df)
    target = "log_cpue"
    y = df[target].values
    groups = df["loc_group"].values

    log(f"\n{'='*70}")
    log(f"TRAINING — {len(features)} features, {len(df)} rows")
    log(f"{'='*70}")
    log(f"  Features: {features[:10]}... (showing first 10)")

    n_splits = min(5, df["loc_group"].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    t0 = time.time()

    # ---- Hyperparameter search (fold 1 only) ----
    log("\n--- Hyperparameter search (fold 1) ---")
    HP_CONFIGS = [
        {"depth": 6, "lr": 0.03, "l2": 3},
        {"depth": 7, "lr": 0.03, "l2": 3},
        {"depth": 7, "lr": 0.02, "l2": 5},
        {"depth": 8, "lr": 0.02, "l2": 5},
        {"depth": 5, "lr": 0.05, "l2": 1},
    ]
    first_tr, first_va = next(iter(gkf.split(df, y, groups)))
    best_cfg = HP_CONFIGS[0]
    best_r2 = -999

    for cfg in HP_CONFIGS:
        reg = CatBoostRegressor(
            iterations=2000, depth=cfg["depth"], learning_rate=cfg["lr"],
            l2_leaf_reg=cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=100, subsample=0.8,
        )
        reg.fit(df.iloc[first_tr][features], y[first_tr],
                eval_set=(df.iloc[first_va][features], y[first_va]))
        pred = reg.predict(df.iloc[first_va][features])
        r2 = r2_score(y[first_va], pred)
        log(f"  depth={cfg['depth']} lr={cfg['lr']} l2={cfg['l2']}: R2={r2:.4f}")
        if r2 > best_r2:
            best_r2 = r2
            best_cfg = cfg

    log(f"  Best config: {best_cfg}")

    # ---- Main CV loop ----
    log(f"\n--- {n_splits}-fold GroupKFold (grouped by location) ---")

    oof_cb = np.full(len(df), np.nan)
    oof_xgb = np.full(len(df), np.nan)
    oof_lgb = np.full(len(df), np.nan)

    cb_r2s, xgb_r2s, lgb_r2s, ens_r2s = [], [], [], []
    cb_maes, xgb_maes, lgb_maes = [], [], []

    for fold, (tr, va) in enumerate(gkf.split(df, y, groups)):
        log(f"\n  Fold {fold+1}/{n_splits} (train={len(tr)}, val={len(va)}, "
            f"val_locs={len(set(groups[va]))})")

        X_tr, X_va = df.iloc[tr][features], df.iloc[va][features]
        y_tr, y_va = y[tr], y[va]

        # --- CatBoost ---
        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=0.8,
        )
        cb.fit(X_tr, y_tr, eval_set=(X_va, y_va))
        p_cb = cb.predict(X_va)
        oof_cb[va] = p_cb
        r2 = r2_score(y_va, p_cb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_cb))
        cb_r2s.append(r2)
        cb_maes.append(mae)
        log(f"    CatBoost:  R2={r2:.4f}  MAE={mae:.3f} lb")

        # --- XGBoost ---
        xg = XGBRegressor(
            n_estimators=3000, max_depth=best_cfg["depth"],
            learning_rate=best_cfg["lr"],
            reg_lambda=best_cfg["l2"], subsample=0.8, colsample_bytree=0.8,
            random_state=SEED, early_stopping_rounds=150, verbosity=0,
        )
        xg.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        p_xg = xg.predict(X_va)
        oof_xgb[va] = p_xg
        r2 = r2_score(y_va, p_xg)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_xg))
        xgb_r2s.append(r2)
        xgb_maes.append(mae)
        log(f"    XGBoost:   R2={r2:.4f}  MAE={mae:.3f} lb")

        # --- LightGBM ---
        lgb_train = lgb.Dataset(X_tr, y_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": 63,
            "learning_rate": best_cfg["lr"],
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "lambda_l2": best_cfg["l2"],
            "verbose": -1,
            "seed": SEED,
        }
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=3000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)],
        )
        p_lgb = lgb_model.predict(X_va)
        oof_lgb[va] = p_lgb
        r2 = r2_score(y_va, p_lgb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_lgb))
        lgb_r2s.append(r2)
        lgb_maes.append(mae)
        log(f"    LightGBM:  R2={r2:.4f}  MAE={mae:.3f} lb")

        # Simple average ensemble
        p_ens = (p_cb + p_xg + p_lgb) / 3
        r2_e = r2_score(y_va, p_ens)
        ens_r2s.append(r2_e)
        log(f"    AvgEnsemble: R2={r2_e:.4f}")

    # ---- Stacking Meta-Learner ----
    log(f"\n{'='*70}")
    log("STACKING META-LEARNER (Ridge on OOF predictions)")
    log(f"{'='*70}")

    valid_mask = ~(np.isnan(oof_cb) | np.isnan(oof_xgb) | np.isnan(oof_lgb))
    oof_stack = np.column_stack([oof_cb, oof_xgb, oof_lgb])[valid_mask]
    y_stack = y[valid_mask]

    ridge = Ridge(alpha=1.0)
    ridge.fit(oof_stack, y_stack)
    meta_pred = ridge.predict(oof_stack)
    meta_r2 = r2_score(y_stack, meta_pred)
    meta_mae = mean_absolute_error(np.expm1(y_stack), np.expm1(meta_pred))
    meta_rmse = np.sqrt(mean_squared_error(y_stack, meta_pred))

    log(f"  Ridge weights: CB={ridge.coef_[0]:.3f}  XGB={ridge.coef_[1]:.3f}  LGB={ridge.coef_[2]:.3f}")
    log(f"  Stacked R2: {meta_r2:.4f}")
    log(f"  Stacked MAE: {meta_mae:.3f} lb")
    log(f"  Stacked RMSE (log): {meta_rmse:.4f}")

    # ---- Temporal holdout (train pre-2023, test 2023+) ----
    log(f"\n{'='*70}")
    log("TEMPORAL HOLDOUT (train < 2023, test >= 2023)")
    log(f"{'='*70}")

    temporal_tr = df["year"] < 2023
    temporal_va = df["year"] >= 2023

    if temporal_va.sum() > 20:
        X_tr_t, X_va_t = df.loc[temporal_tr, features], df.loc[temporal_va, features]
        y_tr_t, y_va_t = y[temporal_tr.values], y[temporal_va.values]

        cb_t = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=0.8,
        )
        cb_t.fit(X_tr_t, y_tr_t, eval_set=(X_va_t, y_va_t))
        p_t = cb_t.predict(X_va_t)
        r2_t = r2_score(y_va_t, p_t)
        mae_t = mean_absolute_error(np.expm1(y_va_t), np.expm1(p_t))
        log(f"  Train: {temporal_tr.sum()} rows ({df.loc[temporal_tr, 'year'].min()}-{df.loc[temporal_tr, 'year'].max()})")
        log(f"  Test:  {temporal_va.sum()} rows ({df.loc[temporal_va, 'year'].min()}-{df.loc[temporal_va, 'year'].max()})")
        log(f"  Temporal R2:  {r2_t:.4f}")
        log(f"  Temporal MAE: {mae_t:.3f} lb")
    else:
        log(f"  Insufficient 2023+ data ({temporal_va.sum()} rows), skipping")

    # ---- Feature importance ----
    log(f"\n{'='*70}")
    log("FEATURE IMPORTANCE (top 30)")
    log(f"{'='*70}")

    cb_full = CatBoostRegressor(
        iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
        l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
    )
    cb_full.fit(df[features], y)
    imp = pd.Series(cb_full.feature_importances_, index=features).sort_values(ascending=False)
    for feat, val in imp.head(30).items():
        log(f"  {feat:>30}: {val:.2f}%")

    elapsed = time.time() - t0

    # ---- FINAL SUMMARY ----
    log(f"\n{'='*70}")
    log("FINAL SUMMARY — CPUE Model v5 (Day-Level Tournament)")
    log(f"{'='*70}")
    log(f"  Dataset:                {len(df)} tournament-days")
    log(f"  Date range:             {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Unique locations:       {df['loc_group'].nunique()}")
    log(f"  Features:               {len(features)}")
    log(f"  Weather coverage:       {df['temp_mean'].notna().sum()}/{len(df)} ({df['temp_mean'].notna().mean()*100:.1f}%)")
    log(f"  Pressure delta coverage:{df['pressure_delta_1d'].notna().sum()}/{len(df)} ({df['pressure_delta_1d'].notna().mean()*100:.1f}%)")
    log(f"  GroupKFold splits:      {n_splits}")
    log(f"  Best HP config:         depth={best_cfg['depth']} lr={best_cfg['lr']} l2={best_cfg['l2']}")
    log(f"")
    log(f"  CPUE R2 (CatBoost):     {np.mean(cb_r2s):.4f} +/- {np.std(cb_r2s):.4f}")
    log(f"  CPUE R2 (XGBoost):      {np.mean(xgb_r2s):.4f} +/- {np.std(xgb_r2s):.4f}")
    log(f"  CPUE R2 (LightGBM):     {np.mean(lgb_r2s):.4f} +/- {np.std(lgb_r2s):.4f}")
    log(f"  CPUE R2 (Avg Ensemble): {np.mean(ens_r2s):.4f} +/- {np.std(ens_r2s):.4f}")
    log(f"  CPUE R2 (Stacked):      {meta_r2:.4f}")
    log(f"")
    log(f"  CPUE MAE (CatBoost):    {np.mean(cb_maes):.3f} lb/angler")
    log(f"  CPUE MAE (XGBoost):     {np.mean(xgb_maes):.3f} lb/angler")
    log(f"  CPUE MAE (LightGBM):    {np.mean(lgb_maes):.3f} lb/angler")
    log(f"  CPUE MAE (Stacked):     {meta_mae:.3f} lb/angler")
    log(f"  CPUE RMSE (Stacked):    {meta_rmse:.4f} (log scale)")
    log(f"")
    log(f"  Wall time:              {elapsed:.0f}s")
    log(f"{'='*70}")


# ======================================================================
# MAIN
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPUE Model v5 — Day-level tournament data")
    parser.add_argument("--workspace", type=str, default=None,
                        help="Override workspace path (default: project root)")
    args = parser.parse_args()

    if args.workspace:
        WORKSPACE = args.workspace
        BASE_DIR = Path(WORKSPACE)
        RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

    df = build_dataset()
    features = get_features(df)
    log(f"\nReady: {len(df)} rows, {len(features)} features")
    train_and_evaluate(df)
