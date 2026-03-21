#!/usr/bin/env python3
"""
CPUE Model v12 — Lag Features + num_anglers Fix + Satellite Water Temp
======================================================================
Key changes over V11:
  1. DROP raw num_anglers from features (was 22.86% importance — model was
     learning tournament size, not weather/water). Keep only log_num_anglers
     CAPPED at 95th percentile so it's a mild covariate, not dominant.
  2. LAG FEATURES per Tanaka et al. — 3/7/14-day rolling means for
     temp_mean, pressure_mean, wind_mean, discharge_cfs per location group.
     These capture "conditions leading up to the event" which fish respond to.
  3. SATELLITE WATER TEMPERATURE — merge remote-sensing lake surface temps
     (from satellite_water_temp.csv) as a real measurement vs the estimated
     water temp proxy.
  4. SHAP-based feature importance — replaces basic CatBoost importance.
  5. Tighter regularization: deeper search over l2_leaf_reg and subsample
     to reduce overfitting gap (CV R²=0.52 vs Temporal R²=0.36).

Target: Temporal R² > 0.45 (up from 0.3582 in V11).
Insight: 76.4% variance is between-location → lag features should help the
23.6% within-location (temporal) signal that the model currently misses.

Usage:
    python scripts/build_cpue_model_v12.py --workspace /workspace/castline
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
# PATHS
# ---------------------------------------------------------------------------
WORKSPACE = os.environ.get("WORKSPACE", str(Path(__file__).resolve().parent.parent))
BASE_DIR = Path(WORKSPACE)
RAW_DIR = BASE_DIR / "raw"

# Also check the nested validation path (local dev vs vast)
if not RAW_DIR.exists():
    RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

TOURNAMENT_FILES = [
    RAW_DIR / "all_bassmaster_outcomes.csv",
    RAW_DIR / "elite_outcomes.csv",
    RAW_DIR / "flw_outcomes.csv",
    RAW_DIR / "tourneyx_outcomes.csv",
    RAW_DIR / "combined_all_outcomes_v2.csv",
]

WEATHER_DAILY = RAW_DIR / "tournament_weather_daily.csv"
FLW_WEATHER = RAW_DIR / "flw_weather_noaa.csv"
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
USGS_SITE_CACHE = RAW_DIR / "usgs_site_cache.csv"
LAGOS_CHAR = RAW_DIR / "lake_characteristics.csv"
LAGOS_DEPTH = RAW_DIR / "lake_depth.csv"
LAGOS_INFO = RAW_DIR / "lake_information.csv"
LAGOS_MATCHES = RAW_DIR / "lagos_matches.csv"
EMBEDDINGS_PATH = RAW_DIR / "location_embeddings_geoclip_pca32.csv"
CREEL_CPUE = RAW_DIR / "creel_cpue_bass.csv"
SATELLITE_TEMP = RAW_DIR / "satellite_water_temp.csv"

SEED = 42
EARTH_RADIUS_KM = 6371.0

SERIES_TIER = {
    "elite_api": 5, "elite": 5,
    "bassmaster_classic": 5,
    "bassmaster_open": 3, "open": 3,
    "flw": 4, "flw_tour": 4,
    "tourneyx": 1, "tourneyx_club": 1, "tourneyx_trail": 2,
    "mlf": 4,
}

PREMIUM_SOURCES = {
    "elite_outcomes", "all_bassmaster_outcomes", "flw_outcomes",
    "combined_all_outcomes_v2", "mlf_outcomes",
}
AUXILIARY_SOURCES = {"tourneyx_outcomes"}

PREMIUM_WEIGHT = 2.0
AUXILIARY_WEIGHT = 1.0

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
_log_file = None


def setup_logging():
    global _log_file
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = open(log_dir / "v12_pipeline.log", "w")


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
    log("CPUE MODEL v12 — LAG FEATURES + NUM_ANGLERS FIX + SAT WATER TEMP")
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

    all_df = all_df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
    log(f"  After dedup: {len(all_df)}")

    all_df["date"] = pd.to_datetime(all_df["date"], errors="coerce")
    all_df = all_df[all_df["date"].notna()].reset_index(drop=True)

    all_df["median_weight_lb"] = pd.to_numeric(all_df["median_weight_lb"], errors="coerce")
    all_df["num_anglers"] = pd.to_numeric(all_df["num_anglers"], errors="coerce")
    all_df["cpue"] = all_df["median_weight_lb"]
    all_df = all_df[all_df["cpue"].notna() & (all_df["cpue"] > 0)].reset_index(drop=True)
    log(f"  With valid CPUE: {len(all_df)}")

    if "measurement_type" in all_df.columns:
        length_mask = all_df["measurement_type"] == "length"
        log(f"  Dropping {length_mask.sum()} length-based events")
        all_df = all_df[~length_mask].reset_index(drop=True)

    all_df["is_premium_source"] = all_df["_source"].isin(PREMIUM_SOURCES).astype(float)
    log(f"  Premium: {(all_df['is_premium_source'] == 1).sum()}")
    log(f"  Auxiliary: {(all_df['is_premium_source'] == 0).sum()}")
    log(f"  Final tournament rows: {len(all_df)}")
    return all_df


def resolve_coordinates(df):
    """Resolve lat/lon from multiple sources."""
    log("\n--- Resolving coordinates ---")
    df["lat"] = np.nan
    df["lon"] = np.nan

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
    lat_rad = np.radians(np.clip(lat, -60, 60))
    decl = np.radians(23.45 * np.sin(np.radians(360.0 / 365 * (doy - 81))))
    cos_ha = (-np.tan(lat_rad) * np.tan(decl))
    cos_ha = np.clip(cos_ha, -1, 1)
    return 2 * np.degrees(np.arccos(cos_ha)) / 15.0


def compute_lunar_phase(dates):
    ref = pd.Timestamp("2000-01-06 18:14:00")
    synodic = 29.53058867
    days_since = (dates - ref).dt.total_seconds() / 86400.0
    return (days_since % synodic) / synodic


def engineer_features(df):
    """Build all features — V12: cap num_anglers, drop raw from features."""
    log("\n--- Feature Engineering (V12) ---")

    # Temporal
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["day_of_year"] = df["date"].dt.dayofyear
    df["day_of_week"] = df["date"].dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    df["doy_sin"] = np.sin(2 * np.pi * df["day_of_year"] / 365)
    df["doy_cos"] = np.cos(2 * np.pi * df["day_of_year"] / 365)

    df["day_length_hours"] = compute_photoperiod(df["lat"].values, df["day_of_year"].values)
    df["days_since_equinox"] = (df["day_of_year"] - 79).clip(lower=0)

    df["lunar_phase"] = compute_lunar_phase(df["date"])
    df["lunar_phase_sin"] = np.sin(2 * np.pi * df["lunar_phase"])
    df["lunar_phase_cos"] = np.cos(2 * np.pi * df["lunar_phase"])

    # Tournament context
    df["day_number"] = pd.to_numeric(df.get("day_number", pd.Series(1, index=df.index)),
                                     errors="coerce").fillna(1)

    source_col = df["results_source"] if "results_source" in df.columns else df.get("_source", "")
    if isinstance(source_col, pd.Series):
        df["series_tier"] = source_col.map(SERIES_TIER).fillna(2)
    else:
        df["series_tier"] = 2

    if "trail" in df.columns:
        trail_tier = df["trail"].map(SERIES_TIER)
        df["series_tier"] = df["series_tier"].fillna(trail_tier)

    # ---- V12 CHANGE: Cap num_anglers at 95th percentile, use only log form ----
    df["num_anglers"] = pd.to_numeric(df["num_anglers"], errors="coerce")
    p95 = df["num_anglers"].quantile(0.95)
    df["num_anglers_capped"] = df["num_anglers"].clip(upper=p95)
    df["log_num_anglers"] = np.log1p(df["num_anglers_capped"].fillna(0))
    log(f"  V12: num_anglers 95th pctile cap = {p95:.0f}")
    log(f"  V12: raw num_anglers EXCLUDED from features (was 22.86% in V11)")

    # Location
    df["abs_lat"] = df["lat"].abs()
    df["lat_band"] = pd.cut(df["lat"], bins=[20, 28, 33, 38, 42, 50],
                            labels=[0, 1, 2, 3, 4]).astype(float)
    df["loc_group"] = df["lat"].round(1).astype(str) + "_" + df["lon"].round(1).astype(str)

    log(f"  Temporal + context features: done")
    return df


def merge_weather(df):
    """Merge day-level weather and compute derived features."""
    log("\n--- Merging day-level weather ---")

    wx_merged = 0

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
        df = df.merge(wx_sub, on="event_id", how="left", suffixes=("", "_wx"))
        wx_merged = df["temp_mean"].notna().sum() if "temp_mean" in df.columns else 0
        log(f"  Merged from daily weather: {wx_merged}/{len(df)}")

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

    for col in ["temp_mean", "temp_max", "temp_min", "pressure_mean", "pressure_min",
                "pressure_max", "pressure_delta_1d", "pressure_delta_3d",
                "wind_mean", "wind_max", "precip_total", "humidity_mean",
                "cloud_cover", "dew_point"]:
        if col not in df.columns:
            df[col] = np.nan

    # Derived weather features
    log("  Computing derived weather features...")
    df["temp_range"] = df["temp_max"] - df["temp_min"]
    df["est_water_temp"] = df["temp_mean"] - 2 + df["month_sin"] * 1.5
    df["bass_thermal_comfort"] = 1 - np.minimum(np.abs(df["est_water_temp"] - 21) / 10, 1)
    df["spawn_window"] = (
        (df["est_water_temp"] >= 15) & (df["est_water_temp"] <= 21) &
        (df["month"] >= 3) & (df["month"] <= 5)
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

    # Interactions
    df["temp_x_lat"] = df["temp_mean"] * df["lat"]
    df["pressure_x_season"] = df["pressure_mean"].fillna(1013) * df["month_sin"]
    df["comfort_x_daylen"] = df["bass_thermal_comfort"] * df["day_length_hours"]
    df["temp_x_pressure_delta"] = df["temp_mean"] * df["pressure_delta_1d"].fillna(0)
    df["lat_x_month"] = df["lat"] * df["month_sin"]
    df["pressure_delta_1d_abs"] = df["pressure_delta_1d"].abs()
    df["pressure_delta_3d_abs"] = df["pressure_delta_3d"].abs()

    wx_coverage = df["temp_mean"].notna().sum()
    log(f"  Weather coverage: {wx_coverage}/{len(df)} ({wx_coverage/len(df)*100:.1f}%)")
    return df


# ======================================================================
# V12 NEW: LAG FEATURES (Tanaka et al.)
# ======================================================================

def add_lag_features(df):
    """
    V12: Add 3/7/14-day lag features per location group.

    For each location, compute rolling means of weather/water conditions
    from PRIOR events at the same location. This captures "how have conditions
    been trending" which fish respond to (acclimatization, migration, etc.).

    Since we have sparse event-level data (not daily), we use a time-window
    approach: for each event, look at all events at the same location within
    the past N days and compute the mean.
    """
    log("\n--- V12: Lag Features (Tanaka et al.) ---")

    lag_vars = []
    for col in ["temp_mean", "pressure_mean", "wind_mean", "precip_total",
                "discharge_cfs", "est_water_temp", "pressure_delta_1d"]:
        if col in df.columns and df[col].notna().sum() > 50:
            lag_vars.append(col)

    if not lag_vars:
        log("  No variables available for lag features, skipping")
        return df

    log(f"  Lag variables: {lag_vars}")
    df = df.sort_values(["loc_group", "date"]).reset_index(drop=True)

    # For each lag variable, compute location-level temporal context
    windows = [3, 7, 14]
    new_cols = {}

    for col in lag_vars:
        for window in windows:
            col_name = f"{col}_lag{window}d"
            new_cols[col_name] = np.full(len(df), np.nan)

    # Group by location and compute rolling stats
    for loc, group in df.groupby("loc_group"):
        if len(group) < 2:
            continue

        idx = group.index.values
        dates = group["date"].values

        for col in lag_vars:
            vals = group[col].values
            for window in windows:
                col_name = f"{col}_lag{window}d"
                for i, (date_i, val_i) in enumerate(zip(dates, vals)):
                    # Look back N days from this event
                    mask = (dates < date_i) & (dates >= date_i - np.timedelta64(window, 'D'))
                    if mask.sum() > 0:
                        past_vals = vals[mask]
                        past_valid = past_vals[~np.isnan(past_vals)]
                        if len(past_valid) > 0:
                            new_cols[col_name][idx[i]] = np.mean(past_valid)

    for col_name, values in new_cols.items():
        df[col_name] = values

    # Also compute change features: current - lag (how much did conditions change?)
    for col in lag_vars:
        if f"{col}_lag7d" in df.columns and col in df.columns:
            df[f"{col}_change_7d"] = df[col] - df[f"{col}_lag7d"]

    # Count how many lag features have data
    lag_cols = [c for c in df.columns if "_lag" in c or "_change_" in c]
    total_filled = sum(df[c].notna().sum() for c in lag_cols)
    total_cells = len(df) * len(lag_cols)
    log(f"  Lag features created: {len(lag_cols)}")
    log(f"  Lag data coverage: {total_filled}/{total_cells} ({total_filled/max(total_cells,1)*100:.1f}%)")

    # Since tournament events at the same location are sparse, also compute
    # GLOBAL temporal context: rolling means across ALL locations in same
    # lat band and time window (captures regional weather trends)
    log("  Computing regional lag features (lat-band temporal context)...")
    df["_lat_band_round"] = (df["lat"] / 3).round() * 3  # ~3 degree bands

    for col in ["temp_mean", "pressure_mean"]:
        if col not in df.columns:
            continue
        for window in [7, 14]:
            col_name = f"{col}_regional_lag{window}d"
            regional_vals = np.full(len(df), np.nan)

            for band, group in df.groupby("_lat_band_round"):
                if len(group) < 3:
                    continue
                idx = group.index.values
                dates = group["date"].values
                vals = group[col].values

                for i, date_i in enumerate(dates):
                    mask = (dates < date_i) & (dates >= date_i - np.timedelta64(window, 'D'))
                    if mask.sum() > 0:
                        past_vals = vals[mask]
                        past_valid = past_vals[~np.isnan(past_vals)]
                        if len(past_valid) >= 2:
                            regional_vals[idx[i]] = np.mean(past_valid)

            df[col_name] = regional_vals

    df.drop(columns=["_lat_band_round"], inplace=True)

    regional_cols = [c for c in df.columns if "_regional_lag" in c]
    if regional_cols:
        regional_filled = sum(df[c].notna().sum() for c in regional_cols)
        log(f"  Regional lag features: {len(regional_cols)}, coverage: {regional_filled}/{len(df)*len(regional_cols)}")

    return df


# ======================================================================
# V12 NEW: SATELLITE WATER TEMPERATURE
# ======================================================================

def merge_satellite_water_temp(df):
    """Merge satellite-derived water surface temperature."""
    log("\n--- V12: Satellite water temperature ---")

    if not SATELLITE_TEMP.exists():
        log("  Satellite temp file not found, skipping")
        return df

    try:
        sat = pd.read_csv(SATELLITE_TEMP)
        log(f"  Satellite temp file: {len(sat)} rows")

        if "event_id" in sat.columns:
            sat_cols = [c for c in sat.columns if c not in ("event_id",)]
            sat_sub = sat[["event_id"] + [c for c in sat_cols if "temp" in c.lower() or "lst" in c.lower() or "sst" in c.lower()]].drop_duplicates(subset=["event_id"])
            if len(sat_sub.columns) > 1:
                df = df.merge(sat_sub, on="event_id", how="left", suffixes=("", "_sat"))
                for c in sat_sub.columns:
                    if c != "event_id" and c in df.columns:
                        cov = df[c].notna().sum()
                        log(f"  Satellite {c}: {cov}/{len(df)} coverage")
        elif "lat" in sat.columns and "lon" in sat.columns:
            # Spatial match using BallTree
            sat_coords = sat[["lat", "lon"]].dropna()
            if len(sat_coords) > 0:
                tree = BallTree(np.radians(sat_coords.values), metric="haversine")
                dist, idx = tree.query(np.radians(df[["lat", "lon"]].values), k=1)
                dist_km = dist[:, 0] * EARTH_RADIUS_KM
                matched = dist_km < 30  # 30km match radius

                temp_cols = [c for c in sat.columns if "temp" in c.lower() or "lst" in c.lower() or "sst" in c.lower()]
                for col in temp_cols[:3]:
                    df[f"sat_{col}"] = np.where(matched, pd.to_numeric(sat.iloc[idx[:, 0]][col].values, errors="coerce"), np.nan)

                log(f"  Satellite spatially matched: {matched.sum()}/{len(df)}")

        # Derived: satellite vs estimated water temp difference
        sat_temp_col = None
        for c in df.columns:
            if "sat" in c.lower() and "temp" in c.lower() and df[c].notna().sum() > 10:
                sat_temp_col = c
                break

        if sat_temp_col and "est_water_temp" in df.columns:
            df["water_temp_sat_vs_est"] = df[sat_temp_col] - df["est_water_temp"]
            df["water_temp_best"] = df[sat_temp_col].fillna(df["est_water_temp"])
            log(f"  Created water_temp_best (satellite-backed)")
        else:
            df["water_temp_best"] = df.get("est_water_temp", pd.Series(np.nan, index=df.index))

    except Exception as e:
        log(f"  Satellite temp error: {e}")
        df["water_temp_best"] = df.get("est_water_temp", pd.Series(np.nan, index=df.index))

    return df


# ======================================================================
# USGS, REGIME, TEMP DEPARTURE, LAGOS, SPECIES, GEOCLIP
# (Same as V11 — no changes needed)
# ======================================================================

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
            return df
        td_sub = td[["event_id"] + avail].drop_duplicates(subset=["event_id"])
        df = df.merge(td_sub, on="event_id", how="left", suffixes=("", "_td"))

        if "temp_departure_c" in df.columns and "pressure_delta_1d" in df.columns:
            df["temp_departure_x_pressure"] = df["temp_departure_c"].fillna(0) * df["pressure_delta_1d"].fillna(0)
        if "temp_departure_zscore" in df.columns and "bass_thermal_comfort" in df.columns:
            df["temp_zscore_x_comfort"] = df["temp_departure_zscore"].fillna(0) * df["bass_thermal_comfort"].fillna(0.5)

        coverage = df["temp_departure_c"].notna().sum()
        log(f"  Temp departure matched: {coverage}/{len(df)} ({coverage/len(df)*100:.1f}%)")
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
            log(f"  LAGOS matched: {matched}/{len(df)} ({matched/len(df)*100:.1f}%)")
        except Exception as e:
            log(f"  LAGOS error: {e}")
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
                log(f"  LAGOS matched (raw): {matched.sum()}/{len(df)}")
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

    LMB_OPTIMAL_C, SMB_OPTIMAL_C = 23.5, 18.5

    if "est_water_temp" in df.columns:
        df["species_optimal_temp"] = df["lmb_probability"] * LMB_OPTIMAL_C + df["smb_probability"] * SMB_OPTIMAL_C
        df["species_temp_deviation"] = np.abs(df["est_water_temp"] - df["species_optimal_temp"])
        df["species_thermal_comfort"] = 1 - np.minimum(df["species_temp_deviation"] / 10, 1)

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
    month_vals = df["month"].values if "month" in df.columns else df["date"].dt.month.values

    sp_weights = np.zeros((len(df), len(SPECIES_CATALOG)))
    for i, (sp_name, sp) in enumerate(SPECIES_CATALOG.items()):
        lat_lo, lat_hi = sp["lat"]
        lat_mid = (lat_lo + lat_hi) / 2
        lat_scale = (lat_hi - lat_lo) / 4
        prob = 1 / (1 + np.exp(-(lat_vals - lat_mid + lat_scale) / (lat_scale / 2)))
        prob *= 1 / (1 + np.exp((lat_vals - lat_mid - lat_scale) / (lat_scale / 2)))
        sp_weights[:, i] = prob * sp["weight"]

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
                        df["creel_lmb_ratio"] = np.where(close, creel_locs.iloc[idx[:, 0]]["ratio_largemouth_bass"].values, np.nan)
                    if "ratio_smallmouth_bass" in creel_locs.columns:
                        df["creel_smb_ratio"] = np.where(close, creel_locs.iloc[idx[:, 0]]["ratio_smallmouth_bass"].values, np.nan)
                    log(f"  CreelCat matched: {close.sum()}/{len(df)}")
        except Exception as e:
            log(f"  CreelCat error: {e}")

    log(f"  Species features: done")
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
        log("  GeoCLIP embeddings not found, skipping")
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
    except Exception as e:
        log(f"  GeoCLIP error: {e}")
    return df


# ======================================================================
# BUILD DATASET
# ======================================================================

def build_dataset():
    """Full pipeline: V12 adds lag features and satellite water temp."""
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
    df = merge_satellite_water_temp(df)

    # V12: Lag features AFTER all weather/water data is merged
    df = add_lag_features(df)

    # Target variable
    df["log_cpue"] = np.log1p(df["cpue"])

    log(f"\n  Final dataset: {len(df)} rows x {df.shape[1]} columns")
    log(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Unique locations: {df['loc_group'].nunique()}")
    log(f"  CPUE range: {df['cpue'].min():.2f} - {df['cpue'].max():.2f} lb/angler")
    log(f"  CPUE median: {df['cpue'].median():.2f} lb/angler")
    return df


# ======================================================================
# FEATURE SELECTION — V12: exclude raw num_anglers
# ======================================================================

def get_features(df):
    """Get feature columns. V12: raw num_anglers EXCLUDED."""
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
        # Temporal leakage
        "year", "day_of_week", "is_weekend",
        # V11 removals
        "data_source_quality",
        # V12: DROP raw num_anglers (was dominating at 22.86%)
        "num_anglers", "num_anglers_capped",
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

    # Verify num_anglers is excluded
    if "num_anglers" in features:
        features.remove("num_anglers")
        log("  WARNING: Removed num_anglers from features (V12 exclusion)")

    return features


# ======================================================================
# MODEL TRAINING — V12
# ======================================================================

def train_and_evaluate(df):
    """V12 training with tighter regularization search."""
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        log("ERROR: catboost not installed"); sys.exit(1)
    try:
        from xgboost import XGBRegressor
    except ImportError:
        log("ERROR: xgboost not installed"); sys.exit(1)
    try:
        import lightgbm as lgb
    except ImportError:
        log("ERROR: lightgbm not installed"); sys.exit(1)

    features = get_features(df)
    target = "log_cpue"
    y = df[target].values
    groups = df["loc_group"].values

    sample_weights = np.where(df["is_premium_source"].values == 1, PREMIUM_WEIGHT, AUXILIARY_WEIGHT)

    # Check that num_anglers is truly excluded
    if "num_anglers" in features:
        log("  ERROR: num_anglers still in features!")
        features.remove("num_anglers")

    lag_feats = [f for f in features if "_lag" in f or "_change_" in f or "_regional_" in f]
    log(f"\n  V12 feature summary:")
    log(f"    Total features: {len(features)}")
    log(f"    Lag features: {len(lag_feats)}")
    log(f"    num_anglers in features: {'num_anglers' in features}")
    log(f"    log_num_anglers in features: {'log_num_anglers' in features}")

    premium_mask = df["is_premium_source"].values == 1
    df_premium = df[premium_mask].reset_index(drop=True)
    y_premium = y[premium_mask]
    groups_premium = groups[premium_mask]

    log(f"\n{'='*70}")
    log(f"TRAINING — {len(features)} features, {len(df)} total rows ({len(df_premium)} premium)")
    log(f"{'='*70}")

    # ---- Phase 1: Hyperparameter search (V12: wider search, more regularization) ----
    log(f"\n--- Phase 1: Hyperparameter search (premium-only, fold 1) ---")

    n_splits_premium = min(5, df_premium["loc_group"].nunique())
    gkf_premium = GroupKFold(n_splits=n_splits_premium)

    # V12: Wider HP search with stronger regularization options
    HP_CONFIGS = [
        {"depth": 4, "lr": 0.02, "l2": 10, "subsample": 0.6},
        {"depth": 4, "lr": 0.02, "l2": 20, "subsample": 0.5},   # V12: more reg
        {"depth": 3, "lr": 0.02, "l2": 20, "subsample": 0.5},   # V12: shallower + more reg
        {"depth": 3, "lr": 0.03, "l2": 15, "subsample": 0.6},
        {"depth": 4, "lr": 0.01, "l2": 15, "subsample": 0.5},   # V12: lower LR
        {"depth": 3, "lr": 0.01, "l2": 25, "subsample": 0.5},   # V12: most regularized
        {"depth": 5, "lr": 0.02, "l2": 10, "subsample": 0.6},
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

    # ---- Phase 2: Premium-only GroupKFold CV ----
    log(f"\n--- Phase 2: Premium-only {n_splits_premium}-fold GroupKFold ---")

    oof_cb_prem = np.full(len(df_premium), np.nan)
    oof_xgb_prem = np.full(len(df_premium), np.nan)
    oof_lgb_prem = np.full(len(df_premium), np.nan)
    cb_r2s_prem, xgb_r2s_prem, lgb_r2s_prem, ens_r2s_prem = [], [], [], []

    for fold, (tr, va) in enumerate(gkf_premium.split(df_premium, y_premium, groups_premium)):
        log(f"\n  Fold {fold+1}/{n_splits_premium} (train={len(tr)}, val={len(va)})")
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
        ens_r2s_prem.append(r2_score(y_va, p_ens))

    log(f"\n  Premium-only CV:")
    log(f"    CatBoost:     {np.mean(cb_r2s_prem):.4f} +/- {np.std(cb_r2s_prem):.4f}")
    log(f"    XGBoost:      {np.mean(xgb_r2s_prem):.4f} +/- {np.std(xgb_r2s_prem):.4f}")
    log(f"    LightGBM:     {np.mean(lgb_r2s_prem):.4f} +/- {np.std(lgb_r2s_prem):.4f}")
    log(f"    Avg Ensemble: {np.mean(ens_r2s_prem):.4f} +/- {np.std(ens_r2s_prem):.4f}")

    # ---- Phase 3: Two-tier GroupKFold CV (all data, weighted) ----
    log(f"\n--- Phase 3: Two-tier GroupKFold (all data, weighted) ---")

    n_splits = min(5, df["loc_group"].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    oof_cb = np.full(len(df), np.nan)
    oof_xgb = np.full(len(df), np.nan)
    oof_lgb = np.full(len(df), np.nan)
    cb_r2s, xgb_r2s, lgb_r2s, ens_r2s = [], [], [], []
    cb_maes, xgb_maes, lgb_maes = [], [], []

    for fold, (tr, va) in enumerate(gkf.split(df, y, groups)):
        log(f"\n  Fold {fold+1}/{n_splits} (train={len(tr)}, val={len(va)})")
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
        cb_r2s.append(r2); cb_maes.append(mae)
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
        xgb_r2s.append(r2); xgb_maes.append(mae)
        log(f"    XGBoost:   R2={r2:.4f}  MAE={mae:.3f} lb")

        lgb_train = lgb.Dataset(X_tr, y_tr, weight=sw_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=2000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)],
        )
        p_lgb = lgb_model.predict(X_va)
        oof_lgb[va] = p_lgb
        r2 = r2_score(y_va, p_lgb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_lgb))
        lgb_r2s.append(r2); lgb_maes.append(mae)
        log(f"    LightGBM:  R2={r2:.4f}  MAE={mae:.3f} lb")

        p_ens = (p_cb + p_xg + p_lgb) / 3
        ens_r2s.append(r2_score(y_va, p_ens))

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

    # ---- Temporal holdout ----
    log(f"\n{'='*70}")
    log("TEMPORAL HOLDOUT (train < 2023-06-01, test >= 2023-06-01)")
    log(f"{'='*70}")

    temporal_split = pd.Timestamp("2023-06-01")
    temporal_tr = df["date"] < temporal_split
    temporal_va = df["date"] >= temporal_split
    r2_t = None

    if temporal_va.sum() > 20:
        X_tr_t, X_va_t = df.loc[temporal_tr, features], df.loc[temporal_va, features]
        y_tr_t, y_va_t = y[temporal_tr.values], y[temporal_va.values]
        sw_tr_t = sample_weights[temporal_tr.values]

        cb_t = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0,
            early_stopping_rounds=150, subsample=subsamp, thread_count=8,
        )
        cb_t.fit(X_tr_t, y_tr_t, sample_weight=sw_tr_t, eval_set=(X_va_t, y_va_t))
        p_t = cb_t.predict(X_va_t)
        r2_t = r2_score(y_va_t, p_t)
        mae_t = mean_absolute_error(np.expm1(y_va_t), np.expm1(p_t))
        log(f"  Train: {temporal_tr.sum()}, Test: {temporal_va.sum()}")
        log(f"  Temporal R2: {r2_t:.4f}")
        log(f"  Temporal MAE: {mae_t:.3f} lb")

        prem_va = temporal_va & (df["is_premium_source"] == 1)
        if prem_va.sum() > 10:
            p_prem = cb_t.predict(df.loc[prem_va, features])
            r2_prem = r2_score(y[prem_va.values], p_prem)
            log(f"  Temporal R2 (premium-only test): {r2_prem:.4f} ({prem_va.sum()} rows)")

    # ---- Walk-forward ----
    log(f"\n{'='*70}")
    log("WALK-FORWARD TEMPORAL VALIDATION (2019+ folds)")
    log(f"{'='*70}")

    df["year"] = df["date"].dt.year
    wf_folds = [
        ("< 2020", "2020-2021", df["year"] < 2020, df["year"].between(2020, 2021)),
        ("< 2022", "2022-2023", df["year"] < 2022, df["year"].between(2022, 2023)),
        ("< 2024", "2024+", df["year"] < 2024, df["year"] >= 2024),
    ]

    wf_r2s = []
    for train_label, test_label, wf_tr, wf_va in wf_folds:
        if wf_tr.sum() < 30 or wf_va.sum() < 10:
            log(f"  Fold {train_label}/{test_label}: skipped")
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
        log(f"  {train_label} / {test_label}: R2={r2_wf:.4f}  MAE={mae_wf:.3f} lb  (train={wf_tr.sum()}, test={wf_va.sum()})")

    if wf_r2s:
        log(f"  Walk-forward mean R2: {np.mean(wf_r2s):.4f} +/- {np.std(wf_r2s):.4f}")

    # ---- Feature importance (SHAP if available, else CatBoost native) ----
    log(f"\n{'='*70}")
    log("FEATURE IMPORTANCE")
    log(f"{'='*70}")

    cb_full = CatBoostRegressor(
        iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
        l2_leaf_reg=best_cfg["l2"], random_seed=SEED, verbose=0, thread_count=8,
    )
    cb_full.fit(df[features], y, sample_weight=sample_weights)

    try:
        import shap
        log("  Using SHAP TreeExplainer...")
        explainer = shap.TreeExplainer(cb_full)
        # Use a sample for speed
        sample_idx = np.random.RandomState(SEED).choice(len(df), min(500, len(df)), replace=False)
        shap_values = explainer.shap_values(df.iloc[sample_idx][features])
        shap_imp = pd.Series(np.abs(shap_values).mean(axis=0), index=features).sort_values(ascending=False)
        log("  SHAP feature importance (top 30):")
        for feat, val in shap_imp.head(30).items():
            log(f"    {feat:>35}: {val:.4f}")

        # Save SHAP values for inference pipeline
        shap_path = BASE_DIR / "models" / "shap_importance_v12.json"
        shap_path.parent.mkdir(parents=True, exist_ok=True)
        shap_dict = {feat: float(val) for feat, val in shap_imp.items()}
        with open(shap_path, "w") as f:
            json.dump(shap_dict, f, indent=2)
        log(f"  SHAP importance saved to {shap_path}")
    except ImportError:
        log("  SHAP not installed, using CatBoost native importance...")
        imp = pd.Series(cb_full.feature_importances_, index=features).sort_values(ascending=False)
        for feat, val in imp.head(30).items():
            log(f"    {feat:>35}: {val:.2f}%")

    # ---- Save models ----
    log(f"\n{'='*70}")
    log("SAVING MODELS")
    log(f"{'='*70}")

    models_dir = BASE_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    cb_full.save_model(str(models_dir / "cpue_v12_catboost.cbm"))
    log(f"  Saved CatBoost → {models_dir / 'cpue_v12_catboost.cbm'}")

    import pickle

    # Train final XGBoost
    xg_full = XGBRegressor(
        n_estimators=3000, max_depth=best_cfg["depth"],
        learning_rate=best_cfg["lr"],
        reg_lambda=best_cfg["l2"], subsample=subsamp, colsample_bytree=0.7,
        random_state=SEED, verbosity=0, n_jobs=8,
    )
    xg_full.fit(df[features], y, sample_weight=sample_weights)
    with open(models_dir / "cpue_v12_xgboost.pkl", "wb") as f:
        pickle.dump(xg_full, f)
    log(f"  Saved XGBoost → {models_dir / 'cpue_v12_xgboost.pkl'}")

    # Train final LightGBM
    lgb_full_ds = lgb.Dataset(df[features], y, weight=sample_weights)
    lgb_params_final = {
        "objective": "regression", "metric": "rmse",
        "num_leaves": 31, "learning_rate": best_cfg["lr"],
        "feature_fraction": 0.7, "bagging_fraction": subsamp,
        "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
        "verbose": -1, "seed": SEED, "num_threads": 8,
    }
    lgb_full = lgb.train(lgb_params_final, lgb_full_ds, num_boost_round=2000)
    with open(models_dir / "cpue_v12_lightgbm.pkl", "wb") as f:
        pickle.dump(lgb_full, f)
    log(f"  Saved LightGBM → {models_dir / 'cpue_v12_lightgbm.pkl'}")

    # Save Ridge meta-learner
    with open(models_dir / "cpue_v12_ridge.pkl", "wb") as f:
        pickle.dump(ridge, f)
    log(f"  Saved Ridge → {models_dir / 'cpue_v12_ridge.pkl'}")

    # Save feature list
    with open(models_dir / "cpue_v12_features.json", "w") as f:
        json.dump(features, f, indent=2)
    log(f"  Saved features → {models_dir / 'cpue_v12_features.json'}")

    # ---- FINAL SUMMARY ----
    log(f"\n{'='*70}")
    log("FINAL SUMMARY — CPUE Model v12")
    log(f"{'='*70}")
    log(f"  Dataset: {len(df)} rows, {len(features)} features")
    log(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Locations: {df['loc_group'].nunique()}")
    log(f"  Lag features: {len(lag_feats)}")
    log(f"  Best HP: depth={best_cfg['depth']} lr={best_cfg['lr']} l2={best_cfg['l2']} sub={subsamp}")
    log(f"")
    log(f"  === PREMIUM-ONLY CV ===")
    log(f"  R2 (CB):  {np.mean(cb_r2s_prem):.4f} +/- {np.std(cb_r2s_prem):.4f}")
    log(f"  R2 (XGB): {np.mean(xgb_r2s_prem):.4f} +/- {np.std(xgb_r2s_prem):.4f}")
    log(f"  R2 (LGB): {np.mean(lgb_r2s_prem):.4f} +/- {np.std(lgb_r2s_prem):.4f}")
    log(f"  R2 (Ens): {np.mean(ens_r2s_prem):.4f} +/- {np.std(ens_r2s_prem):.4f}")
    log(f"")
    log(f"  === TWO-TIER CV ===")
    log(f"  R2 (CB):  {np.mean(cb_r2s):.4f} +/- {np.std(cb_r2s):.4f}")
    log(f"  R2 (Stacked): {meta_r2:.4f}")
    log(f"  MAE (Stacked): {meta_mae:.3f} lb")
    log(f"")
    if r2_t is not None:
        log(f"  Temporal R2: {r2_t:.4f}")
    if wf_r2s:
        log(f"  Walk-forward R2: {np.mean(wf_r2s):.4f} +/- {np.std(wf_r2s):.4f}")
    log(f"")
    log(f"  V12 CHANGES vs V11:")
    log(f"  1. DROPPED raw num_anglers (was 22.86% — learning tournament size)")
    log(f"  2. Added {len(lag_feats)} lag features (3/7/14-day rolling means)")
    log(f"  3. Satellite water temperature integration")
    log(f"  4. SHAP-based feature importance")
    log(f"  5. Stronger regularization search")
    log(f"")
    log(f"  COMPARISON TARGETS:")
    log(f"  V7:  Temporal R2=0.4039")
    log(f"  V11: CV R2=0.5190  Temporal R2=0.3582  Walk-forward R2=0.0327")
    log(f"  V12: CV R2={np.mean(cb_r2s):.4f}  Temporal R2={r2_t if r2_t else 'N/A'}  Walk-forward R2={np.mean(wf_r2s) if wf_r2s else 'N/A'}")
    log(f"{'='*70}")


# ======================================================================
# MAIN
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CPUE Model v12")
    parser.add_argument("--workspace", type=str, default=None)
    args = parser.parse_args()

    if args.workspace:
        WORKSPACE = args.workspace
        BASE_DIR = Path(WORKSPACE)
        RAW_DIR = BASE_DIR / "raw"
        if not RAW_DIR.exists():
            RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

    setup_logging()

    t0 = time.time()
    df = build_dataset()
    features = get_features(df)
    log(f"\nReady: {len(df)} rows, {len(features)} features")
    train_and_evaluate(df)
    elapsed = time.time() - t0
    log(f"\nTotal wall time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    if _log_file:
        _log_file.close()
