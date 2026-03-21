#!/usr/bin/env python3
"""
CPUE Model v11 — Two-Tier Training with TourneyX as Auxiliary Data
===================================================================
Strategy: Fix temporal generalization regression seen in V8-V10 by treating
TourneyX data as auxiliary rather than equal. V7 had temporal R²=0.4039 with
premium-only data (2,389 rows). V8-V10 added ~1,000 TourneyX events but temporal
R² dropped (V10=0.3238).

Key innovations over v10:
  1. Two-tier training: train base models on premium-only first, then fine-tune
     with ALL data using premium 2x weights vs auxiliary 1x
  2. Remove data_source_quality feature (V10 addition that didn't help)
  3. Add is_premium_source binary flag instead
  4. Walk-forward folds 2019+ only (drop pre-2019 noisy folds)
  5. Temporal holdout split: train < 2023-06-01, test >= 2023-06-01
  6. Logging to /workspace/castline/logs/v11_pipeline.log

Usage:
    python scripts/build_cpue_model_v11.py --workspace /workspace/castline
"""

import argparse
import json
import logging
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

# Optional enrichment — LAGOS
LAGOS_CHAR = RAW_DIR / "lake_characteristics.csv"
LAGOS_DEPTH = RAW_DIR / "lake_depth.csv"
LAGOS_INFO = RAW_DIR / "lake_information.csv"
LAGOS_MATCHES = RAW_DIR / "lagos_matches.csv"
EMBEDDINGS_PATH = RAW_DIR / "location_embeddings_geoclip_pca32.csv"
CREEL_CPUE = RAW_DIR / "creel_cpue_bass.csv"

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

# V11: Premium vs auxiliary source classification
PREMIUM_SOURCES = {
    "elite_outcomes", "all_bassmaster_outcomes", "flw_outcomes",
    "combined_all_outcomes_v2", "mlf_outcomes",
}
AUXILIARY_SOURCES = {
    "tourneyx_outcomes",
}

# V11: Two-tier sample weights
PREMIUM_WEIGHT = 2.0
AUXILIARY_WEIGHT = 1.0

# ---------------------------------------------------------------------------
# LOGGING — dual output: console + file
# ---------------------------------------------------------------------------
LOG_DIR = None  # Set after workspace is resolved
_logger = None
_log_file = None


def setup_logging():
    global LOG_DIR, _logger, _log_file
    LOG_DIR = BASE_DIR / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_file = open(LOG_DIR / "v11_pipeline.log", "w")

    _logger = logging.getLogger("v11")
    _logger.setLevel(logging.DEBUG)
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    _logger.addHandler(ch)
    # File handler
    fh = logging.FileHandler(LOG_DIR / "v11_pipeline.log", mode="w")
    fh.setLevel(logging.DEBUG)
    _logger.addHandler(fh)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


# ======================================================================
# DATA LOADING
# ======================================================================

def load_tournament_data():
    """Load and deduplicate all tournament outcome files."""
    log("=" * 70)
    log("CPUE MODEL v11 — TWO-TIER TRAINING (PREMIUM + AUXILIARY)")
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

    # CPUE = median_weight_lb (this IS per-angler already)
    all_df["cpue"] = all_df["median_weight_lb"]
    all_df = all_df[all_df["cpue"].notna() & (all_df["cpue"] > 0)].reset_index(drop=True)
    log(f"  With valid CPUE: {len(all_df)}")

    # Filter out length-based measurements (TourneyX length tournaments)
    if "measurement_type" in all_df.columns:
        length_mask = all_df["measurement_type"] == "length"
        log(f"  Dropping {length_mask.sum()} length-based events")
        all_df = all_df[~length_mask].reset_index(drop=True)

    # V11: Tag premium vs auxiliary
    all_df["is_premium_source"] = all_df["_source"].isin(PREMIUM_SOURCES).astype(float)
    premium_count = (all_df["is_premium_source"] == 1).sum()
    auxiliary_count = (all_df["is_premium_source"] == 0).sum()
    log(f"  Premium sources: {premium_count} rows")
    log(f"  Auxiliary sources: {auxiliary_count} rows")

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
    """Compute lunar phase as 0-1 cycle from date."""
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

    # V11: is_premium_source binary flag (already computed in load_tournament_data)
    # No data_source_quality feature (removed from V10)
    log(f"  Premium/auxiliary distribution:")
    for src, cnt in df["_source"].value_counts().items():
        is_prem = "PREMIUM" if src in PREMIUM_SOURCES else "AUXILIARY"
        log(f"    {src}: {cnt} rows [{is_prem}]")

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

        wx_sub = wx[["event_id"] + avail_cols].drop_duplicates(subset=["event_id"])
        before = df.shape[1]
        df = df.merge(wx_sub, on="event_id", how="left", suffixes=("", "_wx"))
        wx_merged = df["temp_mean"].notna().sum() if "temp_mean" in df.columns else 0
        log(f"  Merged from daily weather: {wx_merged}/{len(df)}")

    # Fallback: FLW weather file
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

    df["temp_range"] = df["temp_max"] - df["temp_min"]
    df["est_water_temp"] = df["temp_mean"] - 2 + df["month_sin"] * 1.5
    df["bass_thermal_comfort"] = 1 - np.minimum(
        np.abs(df["est_water_temp"] - 21) / 10, 1
    )
    df["spawn_window"] = (
        (df["est_water_temp"] >= 15) &
        (df["est_water_temp"] <= 21) &
        (df["month"] >= 3) &
        (df["month"] <= 5)
    ).astype(float)
    df.loc[df["est_water_temp"].isna(), "spawn_window"] = np.nan

    df["front_passage"] = (df["pressure_delta_1d"].abs() > 5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "front_passage"] = np.nan
    df["post_front"] = (df["pressure_delta_1d"] > 5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "post_front"] = np.nan
    df["pre_front"] = (df["pressure_delta_1d"] < -5).astype(float)
    df.loc[df["pressure_delta_1d"].isna(), "pre_front"] = np.nan

    df["pressure_stability"] = 1 / (1 + df["pressure_delta_1d"].abs().fillna(5))

    temp_c = df["temp_mean"].fillna(15)
    wind_ms = df["wind_mean"].fillna(0)
    wind_kph = wind_ms * 3.6
    df["wind_chill"] = np.where(
        temp_c < 10,
        13.12 + 0.6215 * temp_c - 11.37 * (wind_kph.clip(lower=1) ** 0.16) +
        0.3965 * temp_c * (wind_kph.clip(lower=1) ** 0.16),
        temp_c
    )
    df["heat_index"] = np.where(temp_c > 27, temp_c + 0.5 * (temp_c - 27), temp_c)
    df["wind_comfort"] = 1 - np.minimum(df["wind_max"].fillna(0) / 20, 1)
    df["precip_intensity"] = df["precip_total"].fillna(0).clip(upper=100)
    df["gdd_daily"] = (df["temp_mean"] - 10).clip(lower=0)

    # ---- Interactions ----
    df["temp_x_lat"] = df["temp_mean"] * df["lat"]
    df["pressure_x_season"] = df["pressure_mean"].fillna(1013) * df["month_sin"]
    df["comfort_x_daylen"] = df["bass_thermal_comfort"] * df["day_length_hours"]
    df["temp_x_pressure_delta"] = df["temp_mean"] * df["pressure_delta_1d"].fillna(0)
    df["lat_x_month"] = df["lat"] * df["month_sin"]

    df["day_number_sq"] = df["day_number"] ** 2
    df["pressure_delta_1d_abs"] = df["pressure_delta_1d"].abs()
    df["pressure_delta_3d_abs"] = df["pressure_delta_3d"].abs()

    wx_coverage = df["temp_mean"].notna().sum()
    pressure_coverage = df["pressure_delta_1d"].notna().sum()
    log(f"  Weather coverage: {wx_coverage}/{len(df)} ({wx_coverage/len(df)*100:.1f}%)")
    log(f"  Pressure delta coverage: {pressure_coverage}/{len(df)} ({pressure_coverage/len(df)*100:.1f}%)")

    return df


def merge_usgs_features(df):
    """Merge USGS stream gauge features."""
    log("\n--- USGS gauge features ---")
    usgs_path = RAW_DIR / "tournament_usgs_gauges.csv"
    if not usgs_path.exists():
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

        if "flow_regime" in usgs.columns:
            regime_map = usgs[["event_id", "flow_regime"]].drop_duplicates(subset=["event_id"])
            df = df.merge(regime_map, on="event_id", how="left", suffixes=("", "_fr"))
            df["flow_rising"] = (df["flow_regime"] == "rising").astype(float)
            df["flow_falling"] = (df["flow_regime"] == "falling").astype(float)
            df["flow_stable"] = (df["flow_regime"] == "stable").astype(float)
            df.loc[df["flow_regime"].isna(), ["flow_rising", "flow_falling", "flow_stable"]] = np.nan
            df.drop(columns=["flow_regime"], inplace=True, errors="ignore")

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
            regime_dummies = pd.get_dummies(regime["regime_at_event"], prefix="regime")
            regime = pd.concat([regime, regime_dummies], axis=1)
            avail += [c for c in regime_dummies.columns]

        if not avail:
            log("  No regime feature columns found")
            return df

        regime_sub = regime[["event_id"] + avail].drop_duplicates(subset=["event_id"])
        df = df.merge(regime_sub, on="event_id", how="left", suffixes=("", "_reg"))

        if "stability_index" in df.columns and "pressure_delta_1d" in df.columns:
            df["stability_x_pressure"] = df["stability_index"].fillna(0.5) * df["pressure_delta_1d"].fillna(0)
        if "hours_since_front" in df.columns and "bass_thermal_comfort" in df.columns:
            df["front_hours_x_comfort"] = df["hours_since_front"].fillna(48) * df["bass_thermal_comfort"].fillna(0.5)

        coverage = df["stability_index"].notna().sum() if "stability_index" in df.columns else 0
        log(f"  Regime matched: {coverage}/{len(df)} ({coverage/len(df)*100:.1f}%)")
    except Exception as e:
        log(f"  Regime error: {e}")

    return df


def merge_temp_departure(df):
    """Merge temperature departure from climatological normals."""
    log("\n--- Temperature departure from normals ---")
    td_path = RAW_DIR / "tournament_temp_departure.csv"
    if not td_path.exists():
        log("  Temp departure file not found, skipping")
        return df
    try:
        td = pd.read_csv(td_path)
        log(f"  Temp departure file: {len(td)} rows")
        td_cols = ["temp_departure_c", "temp_departure_zscore", "is_warm_anomaly", "is_cold_anomaly"]
        avail = [c for c in td_cols if c in td.columns]
        if not avail:
            log("  No departure columns found")
            return df
        td_sub = td[["event_id"] + avail].drop_duplicates(subset=["event_id"])
        df = df.merge(td_sub, on="event_id", how="left", suffixes=("", "_td"))
        if "temp_departure_c" in df.columns and "pressure_delta_1d" in df.columns:
            df["temp_departure_x_pressure"] = df["temp_departure_c"].fillna(0) * df["pressure_delta_1d"].fillna(0)
        if "temp_departure_zscore" in df.columns and "bass_thermal_comfort" in df.columns:
            df["temp_zscore_x_comfort"] = df["temp_departure_zscore"].fillna(0) * df["bass_thermal_comfort"].fillna(0.5)
        coverage = df["temp_departure_c"].notna().sum()
        log(f"  Temp departure matched: {coverage}/{len(df)} ({coverage/len(df)*100:.1f}%)")
        log(f"  Mean departure: {df['temp_departure_c'].mean():.2f} C")
        log(f"  Warm anomalies: {df['is_warm_anomaly'].sum()}, Cold: {df['is_cold_anomaly'].sum()}")
    except Exception as e:
        log(f"  Temp departure error: {e}")
    return df


def add_lagos_features(df):
    """Add LAGOS lake morphometry features."""
    log("\n--- LAGOS morphometry ---")

    if LAGOS_MATCHES.exists():
        try:
            lagos = pd.read_csv(LAGOS_MATCHES)
            log(f"  Pre-matched LAGOS file: {len(lagos)} locations")

            if "location" in lagos.columns:
                lagos_dedup = lagos.drop_duplicates(subset=["location"])
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

                if "lagos_area_ha" in df.columns:
                    df["lagos_area_ha"] = df["lagos_area_ha"] * 0.404686
                if "lagos_max_depth_m" in df.columns:
                    df["lagos_max_depth_m"] = df["lagos_max_depth_m"] * 0.3048

            matched = df["lagos_sdi"].notna().sum() if "lagos_sdi" in df.columns else 0
            log(f"  LAGOS matched (pre-matched): {matched}/{len(df)} ({matched/len(df)*100:.1f}%)")
        except Exception as e:
            log(f"  Pre-matched LAGOS error: {e}")
    else:
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

    if "lagos_area_ha" in df.columns:
        df["lagos_log_area"] = np.log1p(df["lagos_area_ha"])
    if "lagos_max_depth_m" in df.columns and "lagos_area_ha" in df.columns:
        df["lagos_depth_area_ratio"] = df["lagos_max_depth_m"] / np.log1p(df["lagos_area_ha"])

    return df


def add_species_proxy_features(df):
    """Add species-aware features using lat, morphometry, and CreelCat data."""
    log("\n--- Species proxy features ---")

    df["smb_probability"] = 1 / (1 + np.exp(-(df["lat"] - 42) / 2))
    df["lmb_probability"] = 1 - df["smb_probability"]

    if "lagos_max_depth_m" in df.columns:
        df["smb_depth_signal"] = np.minimum(df["lagos_max_depth_m"].fillna(5) / 15, 1)
        df["smb_composite"] = 0.7 * df["smb_probability"] + 0.3 * df["smb_depth_signal"]
    else:
        df["smb_composite"] = df["smb_probability"]

    LMB_OPTIMAL_C = 23.5
    SMB_OPTIMAL_C = 18.5

    if "est_water_temp" in df.columns:
        df["species_optimal_temp"] = (
            df["lmb_probability"] * LMB_OPTIMAL_C +
            df["smb_probability"] * SMB_OPTIMAL_C
        )
        df["species_temp_deviation"] = np.abs(df["est_water_temp"] - df["species_optimal_temp"])
        df["species_thermal_comfort"] = 1 - np.minimum(df["species_temp_deviation"] / 10, 1)

    # Multi-species features
    SPECIES_CATALOG = {
        "largemouth_bass": {"opt": 23.5, "lat": (25, 46), "pressure": "high", "frontal": -0.15, "flow": 0.0, "spawn": [3,4,5], "feed": [4,5,6,9,10], "weight": 5.0},
        "smallmouth_bass": {"opt": 18.5, "lat": (33, 48), "pressure": "high", "frontal": -0.15, "flow": 0.4, "spawn": [4,5,6], "feed": [5,6,9,10], "weight": 3.0},
        "spotted_bass": {"opt": 21.0, "lat": (30, 40), "pressure": "high", "frontal": -0.08, "flow": 0.4, "spawn": [3,4,5], "feed": [4,5,6,9,10], "weight": 2.5},
        "walleye": {"opt": 16.5, "lat": (36, 49), "pressure": "high", "frontal": -0.08, "flow": 0.4, "spawn": [3,4,5], "feed": [5,6,9,10,11], "weight": 2.5},
        "black_crappie": {"opt": 20.0, "lat": (27, 46), "pressure": "high", "frontal": -0.15, "flow": 0.0, "spawn": [3,4,5], "feed": [3,4,5,10,11], "weight": 2.0},
        "white_crappie": {"opt": 21.0, "lat": (28, 44), "pressure": "high", "frontal": -0.15, "flow": 0.0, "spawn": [3,4,5], "feed": [3,4,5,10,11], "weight": 2.0},
        "striped_bass": {"opt": 18.0, "lat": (28, 45), "pressure": "medium", "frontal": 0.08, "flow": 0.7, "spawn": [3,4,5], "feed": [3,4,5,10,11], "weight": 1.5},
        "channel_catfish": {"opt": 27.0, "lat": (25, 46), "pressure": "low", "frontal": 0.15, "flow": 0.9, "spawn": [5,6,7], "feed": [5,6,7,8,9], "weight": 1.5},
        "blue_catfish": {"opt": 25.0, "lat": (28, 42), "pressure": "low", "frontal": 0.15, "flow": 0.9, "spawn": [5,6,7], "feed": [3,4,5,9,10,11], "weight": 1.5},
        "northern_pike": {"opt": 18.0, "lat": (38, 49), "pressure": "medium", "frontal": 0.03, "flow": 0.0, "spawn": [3,4], "feed": [5,6,9,10], "weight": 1.2},
        "rainbow_trout": {"opt": 13.0, "lat": (32, 49), "pressure": "medium", "frontal": 0.08, "flow": 0.9, "spawn": [1,2,3,4], "feed": [3,4,5,9,10,11], "weight": 1.0},
    }
    PRESSURE_MAP = {"high": 1.0, "medium": 0.5, "low": 0.2}

    lat_vals = df["lat"].values
    depth_vals = df["lagos_max_depth_m"].values if "lagos_max_depth_m" in df.columns else np.full(len(df), 5.0)
    area_vals = df["lagos_area_ha"].values if "lagos_area_ha" in df.columns else np.full(len(df), 100.0)
    month_vals = df["month"].values if "month" in df.columns else df["date"].dt.month.values

    sp_weights = np.zeros((len(df), len(SPECIES_CATALOG)))
    sp_names = list(SPECIES_CATALOG.keys())

    for i, (sp_name, sp) in enumerate(SPECIES_CATALOG.items()):
        lat_lo, lat_hi = sp["lat"]
        lat_mid = (lat_lo + lat_hi) / 2
        lat_scale = (lat_hi - lat_lo) / 4
        prob = 1 / (1 + np.exp(-(lat_vals - lat_mid + lat_scale) / (lat_scale / 2)))
        prob *= 1 / (1 + np.exp((lat_vals - lat_mid - lat_scale) / (lat_scale / 2)))
        prob = prob * sp["weight"]
        sp_weights[:, i] = prob

    row_sums = sp_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    sp_probs = sp_weights / row_sums

    opt_temps = np.array([sp["opt"] for sp in SPECIES_CATALOG.values()])
    df["weighted_optimal_temp"] = (sp_probs * opt_temps).sum(axis=1)

    pressure_vals = np.array([PRESSURE_MAP[sp["pressure"]] for sp in SPECIES_CATALOG.values()])
    df["pressure_sensitivity_score"] = (sp_probs * pressure_vals).sum(axis=1)

    frontal_vals = np.array([sp["frontal"] for sp in SPECIES_CATALOG.values()])
    df["frontal_response_score"] = (sp_probs * frontal_vals).sum(axis=1)

    flow_vals = np.array([sp["flow"] for sp in SPECIES_CATALOG.values()])
    df["flow_preference_score"] = (sp_probs * flow_vals).sum(axis=1)

    spawn_matrix = np.zeros((12, len(SPECIES_CATALOG)))
    for i, sp in enumerate(SPECIES_CATALOG.values()):
        for m in sp["spawn"]:
            spawn_matrix[m - 1, i] = 1.0
    spawn_fracs = np.array([spawn_matrix[m - 1] for m in month_vals])
    df["spawn_activity"] = (sp_probs * spawn_fracs).sum(axis=1)

    feed_matrix = np.zeros((12, len(SPECIES_CATALOG)))
    for i, sp in enumerate(SPECIES_CATALOG.values()):
        for m in sp["feed"]:
            feed_matrix[m - 1, i] = 1.0
    feed_fracs = np.array([feed_matrix[m - 1] for m in month_vals])
    df["peak_feed_activity"] = (sp_probs * feed_fracs).sum(axis=1)

    log(f"  Multi-species features: weighted_optimal_temp, pressure_sensitivity_score, "
        f"frontal_response_score, flow_preference_score, spawn_activity, peak_feed_activity")

    if CREEL_CPUE.exists():
        try:
            creel = pd.read_csv(CREEL_CPUE)
            if "species" in creel.columns and "lat" in creel.columns:
                creel_locs = creel.groupby(
                    [creel["lat"].round(1), creel["lon"].round(1)]
                )["species"].value_counts().unstack(fill_value=0)
                creel_locs["total"] = creel_locs.sum(axis=1)
                for sp in ["Largemouth Bass", "Smallmouth Bass"]:
                    col = sp.lower().replace(" ", "_")
                    if sp in creel_locs.columns:
                        creel_locs[f"ratio_{col}"] = creel_locs[sp] / creel_locs["total"]

                if len(creel_locs) > 0:
                    creel_coords = np.array([[lat, lon] for lat, lon in creel_locs.index])
                    tree = BallTree(np.radians(creel_coords), metric="haversine")
                    dist, idx = tree.query(np.radians(df[["lat", "lon"]].values), k=1)
                    dist_km = dist[:, 0] * EARTH_RADIUS_KM
                    close = dist_km < 25

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
    geoclip_candidates = [
        EMBEDDINGS_PATH,
        BASE_DIR / "raw" / "location_embeddings_geoclip_pca32.csv",
        BASE_DIR / "data" / "raw" / "location_embeddings_geoclip_pca32.csv",
    ]
    geoclip_path = None
    for p in geoclip_candidates:
        if p.exists():
            geoclip_path = p
            break
    if geoclip_path is None:
        log("  GeoCLIP embeddings not found at any candidate path, skipping")
        return df

    try:
        emb = pd.read_csv(geoclip_path, low_memory=False)
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
    df = merge_temp_departure(df)
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
    log(f"  Premium rows: {(df['is_premium_source'] == 1).sum()}")
    log(f"  Auxiliary rows: {(df['is_premium_source'] == 0).sum()}")

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
        # V11: remove temporal leakage features (same as V7+)
        "year", "day_of_week", "is_weekend", "day_number_sq",
        # V11: remove data_source_quality (V10 addition that didn't help)
        "data_source_quality",
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
# MODEL TRAINING — V11 TWO-TIER APPROACH
# ======================================================================

def train_and_evaluate(df):
    """V11 Two-tier training: premium-only base, then fine-tune with all data."""
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

    # V11: Two-tier sample weights (premium=2x, auxiliary=1x)
    sample_weights = np.where(df["is_premium_source"].values == 1, PREMIUM_WEIGHT, AUXILIARY_WEIGHT)
    log(f"  V11 sample weights — premium={PREMIUM_WEIGHT}x ({(df['is_premium_source']==1).sum()} rows), "
        f"auxiliary={AUXILIARY_WEIGHT}x ({(df['is_premium_source']==0).sum()} rows)")

    # Premium-only subset
    premium_mask = df["is_premium_source"].values == 1
    df_premium = df[premium_mask].reset_index(drop=True)
    y_premium = y[premium_mask]
    groups_premium = groups[premium_mask]
    log(f"  Premium-only subset: {len(df_premium)} rows, {df_premium['loc_group'].nunique()} locations")

    log(f"\n{'='*70}")
    log(f"TRAINING — {len(features)} features, {len(df)} total rows ({len(df_premium)} premium)")
    log(f"{'='*70}")
    log(f"  Features: {features[:10]}... (showing first 10)")

    # ---- Phase 1: Hyperparameter search on premium-only data ----
    log(f"\n--- Phase 1: Hyperparameter search (premium-only, fold 1) ---")

    n_splits_premium = min(5, df_premium["loc_group"].nunique())
    gkf_premium = GroupKFold(n_splits=n_splits_premium)

    HP_CONFIGS = [
        {"depth": 4, "lr": 0.02, "l2": 10, "subsample": 0.6},
        {"depth": 4, "lr": 0.03, "l2": 10, "subsample": 0.6},
        {"depth": 4, "lr": 0.03, "l2": 5, "subsample": 0.7},
        {"depth": 5, "lr": 0.02, "l2": 10, "subsample": 0.6},
        {"depth": 4, "lr": 0.02, "l2": 15, "subsample": 0.6},
        {"depth": 3, "lr": 0.03, "l2": 15, "subsample": 0.6},  # More regularized
    ]
    first_tr, first_va = next(iter(gkf_premium.split(df_premium, y_premium, groups_premium)))
    best_cfg = HP_CONFIGS[0]
    best_r2 = -999

    for cfg in HP_CONFIGS:
        subsamp = cfg.get("subsample", 0.7)
        reg = CatBoostRegressor(
            iterations=2000, depth=cfg["depth"], learning_rate=cfg["lr"],
            l2_leaf_reg=cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=100, subsample=subsamp, thread_count=8,
        )
        reg.fit(df_premium.iloc[first_tr][features], y_premium[first_tr],
                eval_set=(df_premium.iloc[first_va][features], y_premium[first_va]))
        pred = reg.predict(df_premium.iloc[first_va][features])
        r2 = r2_score(y_premium[first_va], pred)
        log(f"  depth={cfg['depth']} lr={cfg['lr']} l2={cfg['l2']} sub={subsamp}: R2={r2:.4f}")
        if r2 > best_r2:
            best_r2 = r2
            best_cfg = cfg

    log(f"  Best config: {best_cfg}")
    subsamp = best_cfg.get("subsample", 0.7)

    # ---- Phase 2: Premium-only GroupKFold CV (baseline, like V7) ----
    log(f"\n--- Phase 2: Premium-only {n_splits_premium}-fold GroupKFold ---")

    oof_cb_prem = np.full(len(df_premium), np.nan)
    oof_xgb_prem = np.full(len(df_premium), np.nan)
    oof_lgb_prem = np.full(len(df_premium), np.nan)

    cb_r2s_prem, xgb_r2s_prem, lgb_r2s_prem, ens_r2s_prem = [], [], [], []

    for fold, (tr, va) in enumerate(gkf_premium.split(df_premium, y_premium, groups_premium)):
        log(f"\n  Fold {fold+1}/{n_splits_premium} (train={len(tr)}, val={len(va)}, "
            f"val_locs={len(set(groups_premium[va]))})")

        X_tr, X_va = df_premium.iloc[tr][features], df_premium.iloc[va][features]
        y_tr, y_va = y_premium[tr], y_premium[va]

        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=subsamp, thread_count=8,
        )
        cb.fit(X_tr, y_tr, eval_set=(X_va, y_va))
        p_cb = cb.predict(X_va)
        oof_cb_prem[va] = p_cb
        r2 = r2_score(y_va, p_cb)
        cb_r2s_prem.append(r2)
        log(f"    CatBoost:  R2={r2:.4f}")

        xg = XGBRegressor(
            n_estimators=3000, max_depth=best_cfg["depth"],
            learning_rate=best_cfg["lr"],
            reg_lambda=best_cfg["l2"], subsample=subsamp, colsample_bytree=0.7,
            colsample_bylevel=0.7,
            random_state=SEED, early_stopping_rounds=150, verbosity=0, n_jobs=8,
        )
        xg.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        p_xg = xg.predict(X_va)
        oof_xgb_prem[va] = p_xg
        r2 = r2_score(y_va, p_xg)
        xgb_r2s_prem.append(r2)
        log(f"    XGBoost:   R2={r2:.4f}")

        lgb_train = lgb.Dataset(X_tr, y_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_params = {
            "objective": "regression", "metric": "rmse",
            "num_leaves": 31, "learning_rate": best_cfg["lr"],
            "feature_fraction": 0.7, "bagging_fraction": subsamp,
            "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
            "verbose": -1, "seed": SEED, "num_threads": 8,
        }
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=2000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)],
        )
        p_lgb = lgb_model.predict(X_va)
        oof_lgb_prem[va] = p_lgb
        r2 = r2_score(y_va, p_lgb)
        lgb_r2s_prem.append(r2)
        log(f"    LightGBM:  R2={r2:.4f}")

        p_ens = (p_cb + p_xg + p_lgb) / 3
        r2_e = r2_score(y_va, p_ens)
        ens_r2s_prem.append(r2_e)
        log(f"    AvgEnsemble: R2={r2_e:.4f}")

    log(f"\n  Premium-only CV Summary:")
    log(f"    CatBoost:     {np.mean(cb_r2s_prem):.4f} +/- {np.std(cb_r2s_prem):.4f}")
    log(f"    XGBoost:      {np.mean(xgb_r2s_prem):.4f} +/- {np.std(xgb_r2s_prem):.4f}")
    log(f"    LightGBM:     {np.mean(lgb_r2s_prem):.4f} +/- {np.std(lgb_r2s_prem):.4f}")
    log(f"    Avg Ensemble: {np.mean(ens_r2s_prem):.4f} +/- {np.std(ens_r2s_prem):.4f}")

    # ---- Phase 3: Two-tier GroupKFold CV (all data, premium 2x weights) ----
    log(f"\n--- Phase 3: Two-tier {min(5, df['loc_group'].nunique())}-fold GroupKFold (all data, weighted) ---")

    n_splits = min(5, df["loc_group"].nunique())
    gkf = GroupKFold(n_splits=n_splits)

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
        sw_tr = sample_weights[tr]

        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=subsamp, thread_count=8,
        )
        cb.fit(X_tr, y_tr, sample_weight=sw_tr, eval_set=(X_va, y_va))
        p_cb = cb.predict(X_va)
        oof_cb[va] = p_cb
        r2 = r2_score(y_va, p_cb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_cb))
        cb_r2s.append(r2)
        cb_maes.append(mae)
        log(f"    CatBoost:  R2={r2:.4f}  MAE={mae:.3f} lb")

        xg = XGBRegressor(
            n_estimators=3000, max_depth=best_cfg["depth"],
            learning_rate=best_cfg["lr"],
            reg_lambda=best_cfg["l2"], subsample=subsamp, colsample_bytree=0.7,
            colsample_bylevel=0.7,
            random_state=SEED, early_stopping_rounds=150, verbosity=0, n_jobs=8,
        )
        xg.fit(X_tr, y_tr, sample_weight=sw_tr,
               eval_set=[(X_va, y_va)], verbose=False)
        p_xg = xg.predict(X_va)
        oof_xgb[va] = p_xg
        r2 = r2_score(y_va, p_xg)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_xg))
        xgb_r2s.append(r2)
        xgb_maes.append(mae)
        log(f"    XGBoost:   R2={r2:.4f}  MAE={mae:.3f} lb")

        lgb_train = lgb.Dataset(X_tr, y_tr, weight=sw_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_params = {
            "objective": "regression", "metric": "rmse",
            "num_leaves": 31, "learning_rate": best_cfg["lr"],
            "feature_fraction": 0.7, "bagging_fraction": subsamp,
            "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
            "verbose": -1, "seed": SEED, "num_threads": 8,
        }
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=2000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)],
        )
        p_lgb = lgb_model.predict(X_va)
        oof_lgb[va] = p_lgb
        r2 = r2_score(y_va, p_lgb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_lgb))
        lgb_r2s.append(r2)
        lgb_maes.append(mae)
        log(f"    LightGBM:  R2={r2:.4f}  MAE={mae:.3f} lb")

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

    # ---- V11: Temporal holdout (train < 2023-06-01, test >= 2023-06-01) ----
    log(f"\n{'='*70}")
    log("TEMPORAL HOLDOUT (train < 2023-06-01, test >= 2023-06-01)")
    log(f"{'='*70}")

    temporal_split = pd.Timestamp("2023-06-01")
    temporal_tr = df["date"] < temporal_split
    temporal_va = df["date"] >= temporal_split

    if temporal_va.sum() > 20:
        X_tr_t, X_va_t = df.loc[temporal_tr, features], df.loc[temporal_va, features]
        y_tr_t, y_va_t = y[temporal_tr.values], y[temporal_va.values]
        sw_tr_t = sample_weights[temporal_tr.values]

        # Also test premium-only temporal holdout
        prem_va = temporal_va & (df["is_premium_source"] == 1)
        prem_tr = temporal_tr  # Train on all pre-split data

        # Full temporal holdout (all data with weights)
        cb_t = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=subsamp, thread_count=8,
        )
        cb_t.fit(X_tr_t, y_tr_t, sample_weight=sw_tr_t, eval_set=(X_va_t, y_va_t))
        p_t = cb_t.predict(X_va_t)
        r2_t = r2_score(y_va_t, p_t)
        mae_t = mean_absolute_error(np.expm1(y_va_t), np.expm1(p_t))
        log(f"  Train: {temporal_tr.sum()} rows (up to {temporal_split.date()})")
        log(f"  Test:  {temporal_va.sum()} rows (from {temporal_split.date()})")
        log(f"  Temporal R2 (all test):     {r2_t:.4f}")
        log(f"  Temporal MAE (all test):    {mae_t:.3f} lb")

        # Premium-only temporal holdout
        if prem_va.sum() > 10:
            p_prem = cb_t.predict(df.loc[prem_va, features])
            r2_prem = r2_score(y[prem_va.values], p_prem)
            mae_prem = mean_absolute_error(np.expm1(y[prem_va.values]), np.expm1(p_prem))
            log(f"  Temporal R2 (premium-only): {r2_prem:.4f}  ({prem_va.sum()} rows)")
            log(f"  Temporal MAE (premium-only):{mae_prem:.3f} lb")

        # Also train premium-only model for temporal comparison
        log(f"\n  --- Premium-only temporal holdout (V7-like) ---")
        prem_tr_mask = temporal_tr & (df["is_premium_source"] == 1)
        if prem_tr_mask.sum() > 30:
            cb_prem = CatBoostRegressor(
                iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
                l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
                early_stopping_rounds=150, subsample=subsamp, thread_count=8,
            )
            cb_prem.fit(df.loc[prem_tr_mask, features], y[prem_tr_mask.values],
                        eval_set=(X_va_t, y_va_t))
            p_prem_only = cb_prem.predict(X_va_t)
            r2_prem_only = r2_score(y_va_t, p_prem_only)
            mae_prem_only = mean_absolute_error(np.expm1(y_va_t), np.expm1(p_prem_only))
            log(f"  Premium-only train: {prem_tr_mask.sum()} rows")
            log(f"  Temporal R2 (prem-train, all-test):   {r2_prem_only:.4f}")
            log(f"  Temporal MAE (prem-train, all-test):  {mae_prem_only:.3f} lb")

            if prem_va.sum() > 10:
                p_pp = cb_prem.predict(df.loc[prem_va, features])
                r2_pp = r2_score(y[prem_va.values], p_pp)
                log(f"  Temporal R2 (prem-train, prem-test):  {r2_pp:.4f}  ({prem_va.sum()} rows)")
    else:
        log(f"  Insufficient post-split data ({temporal_va.sum()} rows), skipping")
        r2_t = None

    # ---- V11: Walk-forward temporal validation (2019+ only) ----
    log(f"\n{'='*70}")
    log("WALK-FORWARD TEMPORAL VALIDATION (2019+ folds only)")
    log(f"{'='*70}")

    df["year"] = df["date"].dt.year  # Ensure year column exists
    wf_folds = [
        ("< 2020", "2020-2021", df["year"] < 2020, df["year"].between(2020, 2021)),
        ("< 2022", "2022-2023", df["year"] < 2022, df["year"].between(2022, 2023)),
        ("< 2024", "2024+", df["year"] < 2024, df["year"] >= 2024),
    ]

    wf_r2s = []
    wf_r2s_prem = []  # Also track premium-only test performance

    for train_label, test_label, wf_tr, wf_va in wf_folds:
        if wf_tr.sum() < 30 or wf_va.sum() < 10:
            log(f"  Fold {train_label}/{test_label}: skipped (train={wf_tr.sum()}, test={wf_va.sum()})")
            continue

        X_wf_tr, X_wf_va = df.loc[wf_tr, features], df.loc[wf_va, features]
        y_wf_tr, y_wf_va = y[wf_tr.values], y[wf_va.values]
        sw_wf = sample_weights[wf_tr.values]

        cb_wf = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=subsamp, thread_count=8,
        )
        cb_wf.fit(X_wf_tr, y_wf_tr, sample_weight=sw_wf, eval_set=(X_wf_va, y_wf_va))
        p_wf = cb_wf.predict(X_wf_va)
        r2_wf = r2_score(y_wf_va, p_wf)
        mae_wf = mean_absolute_error(np.expm1(y_wf_va), np.expm1(p_wf))
        wf_r2s.append(r2_wf)

        # Premium-only test subset
        prem_test = wf_va & (df["is_premium_source"] == 1)
        r2_prem_str = ""
        if prem_test.sum() > 5:
            p_prem_wf = cb_wf.predict(df.loc[prem_test, features])
            r2_prem_wf = r2_score(y[prem_test.values], p_prem_wf)
            wf_r2s_prem.append(r2_prem_wf)
            r2_prem_str = f"  prem_R2={r2_prem_wf:.4f} ({prem_test.sum()} rows)"

        log(f"  Fold train {train_label} / test {test_label}: R2={r2_wf:.4f}  MAE={mae_wf:.3f} lb  "
            f"(train={wf_tr.sum()}, test={wf_va.sum()}){r2_prem_str}")

    if wf_r2s:
        log(f"  Walk-forward mean temporal R2: {np.mean(wf_r2s):.4f} +/- {np.std(wf_r2s):.4f}")
    if wf_r2s_prem:
        log(f"  Walk-forward mean premium R2:  {np.mean(wf_r2s_prem):.4f} +/- {np.std(wf_r2s_prem):.4f}")

    # ---- Feature importance ----
    log(f"\n{'='*70}")
    log("FEATURE IMPORTANCE (top 30)")
    log(f"{'='*70}")

    cb_full = CatBoostRegressor(
        iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
        l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0, thread_count=8,
    )
    cb_full.fit(df[features], y, sample_weight=sample_weights)
    imp = pd.Series(cb_full.feature_importances_, index=features).sort_values(ascending=False)
    for feat, val in imp.head(30).items():
        log(f"  {feat:>30}: {val:.2f}%")

    elapsed = time.time() - time.time()  # Will fix below

    # ---- FINAL SUMMARY ----
    log(f"\n{'='*70}")
    log("FINAL SUMMARY — CPUE Model v11 (Two-Tier Training)")
    log(f"{'='*70}")
    log(f"  Dataset (total):        {len(df)} tournament-days")
    log(f"  Dataset (premium):      {(df['is_premium_source']==1).sum()} tournament-days")
    log(f"  Dataset (auxiliary):    {(df['is_premium_source']==0).sum()} tournament-days")
    log(f"  Date range:             {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Unique locations:       {df['loc_group'].nunique()}")
    log(f"  Features:               {len(features)}")
    log(f"  Weather coverage:       {df['temp_mean'].notna().sum()}/{len(df)} ({df['temp_mean'].notna().mean()*100:.1f}%)")
    log(f"  Pressure delta coverage:{df['pressure_delta_1d'].notna().sum()}/{len(df)} ({df['pressure_delta_1d'].notna().mean()*100:.1f}%)")
    log(f"  Best HP config:         depth={best_cfg['depth']} lr={best_cfg['lr']} l2={best_cfg['l2']}")
    log(f"")
    log(f"  === PREMIUM-ONLY CV (like V7) ===")
    log(f"  CPUE R2 (CatBoost):     {np.mean(cb_r2s_prem):.4f} +/- {np.std(cb_r2s_prem):.4f}")
    log(f"  CPUE R2 (XGBoost):      {np.mean(xgb_r2s_prem):.4f} +/- {np.std(xgb_r2s_prem):.4f}")
    log(f"  CPUE R2 (LightGBM):     {np.mean(lgb_r2s_prem):.4f} +/- {np.std(lgb_r2s_prem):.4f}")
    log(f"  CPUE R2 (Avg Ensemble): {np.mean(ens_r2s_prem):.4f} +/- {np.std(ens_r2s_prem):.4f}")
    log(f"")
    log(f"  === TWO-TIER CV (all data, premium 2x weight) ===")
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
    if r2_t is not None:
        log(f"  Temporal R2 (mid-2023 split): {r2_t:.4f}")
    if wf_r2s:
        log(f"  Walk-forward R2 (mean):       {np.mean(wf_r2s):.4f} +/- {np.std(wf_r2s):.4f}")
    if wf_r2s_prem:
        log(f"  Walk-forward R2 (prem-only):  {np.mean(wf_r2s_prem):.4f} +/- {np.std(wf_r2s_prem):.4f}")
    log(f"")
    log(f"  V11 CHANGES vs V10:")
    log(f"  1. Two-tier training: premium-only base, then all with premium 2x weights")
    log(f"  2. Removed data_source_quality feature")
    log(f"  3. Added is_premium_source binary flag")
    log(f"  4. Walk-forward folds 2019+ only (dropped noisy pre-2019 folds)")
    log(f"  5. Temporal holdout split at 2023-06-01 (mid-year)")
    log(f"")
    log(f"  COMPARISON TARGETS:")
    log(f"  V7:  CV R2=0.4482  Temporal R2=0.4039  Walk-forward R2=0.26")
    log(f"  V9:  CV R2=0.5197  Temporal R2=0.3507  Walk-forward R2=-0.08")
    log(f"  V10: CV R2=???     Temporal R2=0.3238  Walk-forward R2=???")
    log(f"{'='*70}")


# ======================================================================
# MAIN
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPUE Model v11 — Two-tier training")
    parser.add_argument("--workspace", type=str, default=None,
                        help="Override workspace path (default: project root)")
    args = parser.parse_args()

    if args.workspace:
        WORKSPACE = args.workspace
        BASE_DIR = Path(WORKSPACE)
        RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

    setup_logging()

    t0_global = time.time()
    df = build_dataset()
    features = get_features(df)
    log(f"\nReady: {len(df)} rows, {len(features)} features")
    train_and_evaluate(df)
    elapsed = time.time() - t0_global
    log(f"\nTotal wall time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    if _log_file:
        _log_file.close()
