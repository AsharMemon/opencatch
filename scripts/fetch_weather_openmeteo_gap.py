#!/usr/bin/env python3
"""
Fetch weather from Open-Meteo for tournament events missing weather data.
Appends to existing tournament_weather_daily.csv.

Uses Open-Meteo historical archive API (free, no key needed).
Rate limit: 0.3s between calls.
Checkpoints every 50 events.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
RAW_DIR = Path("/workspace/castline/raw")
TOURNAMENT_FILES = [
    RAW_DIR / "all_bassmaster_outcomes.csv",
    RAW_DIR / "elite_outcomes.csv",
    RAW_DIR / "flw_outcomes.csv",
    RAW_DIR / "tourneyx_outcomes.csv",
    RAW_DIR / "combined_all_outcomes_v2.csv",
]
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
FLW_WEATHER = RAW_DIR / "flw_weather_noaa.csv"
OUTPUT_CSV = RAW_DIR / "tournament_weather_daily.csv"
CHECKPOINT_CSV = RAW_DIR / "tournament_weather_openmeteo.checkpoint.csv"

CHECKPOINT_INTERVAL = 50
RATE_LIMIT_SLEEP = 0.35  # seconds between API calls

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "precipitation_sum", "windspeed_10m_max", "windgusts_10m_max",
    "surface_pressure_mean", "surface_pressure_max", "surface_pressure_min",
    "relative_humidity_2m_mean", "cloudcover_mean", "dewpoint_2m_mean",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_all_events():
    """Load all tournament events, deduplicate, resolve coords."""
    frames = []
    for fp in TOURNAMENT_FILES:
        if fp.exists():
            df = pd.read_csv(fp, low_memory=False)
            frames.append(df)
            log(f"  {fp.name}: {len(df)} rows")

    all_df = pd.concat(frames, ignore_index=True, sort=False)
    all_df = all_df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
    all_df["date"] = pd.to_datetime(all_df["date"], errors="coerce")
    all_df = all_df[all_df["date"].notna()].reset_index(drop=True)
    all_df["date_str"] = all_df["date"].dt.strftime("%Y-%m-%d")
    log(f"  Unique events with valid dates: {len(all_df)}")

    # Resolve coordinates from geocode cache
    with open(GEOCODE_CACHE) as f:
        gc = json.load(f)

    # Also try FLW weather file for coords
    flw_coords = {}
    if FLW_WEATHER.exists():
        flw = pd.read_csv(FLW_WEATHER, usecols=["event_id", "lat", "lon"]).dropna(subset=["lat", "lon"])
        flw_coords = dict(zip(flw["event_id"], zip(flw["lat"], flw["lon"])))

    lats, lons = [], []
    for _, row in all_df.iterrows():
        eid = row["event_id"]
        loc = row.get("location", "")
        lat = lon = np.nan

        # Try FLW coords first
        if eid in flw_coords:
            lat, lon = flw_coords[eid]
        # Then geocode cache
        elif isinstance(loc, str) and loc in gc:
            c = gc[loc]
            if c.get("lat") and c.get("lon"):
                lat, lon = c["lat"], c["lon"]

        lats.append(lat)
        lons.append(lon)

    all_df["lat"] = lats
    all_df["lon"] = lons

    has_coords = all_df["lat"].notna() & all_df["lon"].notna()
    log(f"  Events with coords: {has_coords.sum()}/{len(all_df)}")
    return all_df[has_coords].reset_index(drop=True)


def fetch_openmeteo_weather(lat, lon, event_date_str):
    """
    Fetch weather from Open-Meteo for event_date and 3 days before (for pressure deltas).
    Returns dict with weather columns or None on failure.
    """
    dt = datetime.strptime(event_date_str, "%Y-%m-%d")
    start_date = (dt - timedelta(days=3)).strftime("%Y-%m-%d")
    end_date = event_date_str

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
        if resp.status_code == 429:
            log("  Rate limited! Sleeping 60s...")
            time.sleep(60)
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
        if resp.status_code != 200:
            log(f"  HTTP {resp.status_code} for {lat},{lon} on {event_date_str}")
            return None
        data = resp.json()
    except Exception as e:
        log(f"  Request error: {e}")
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates or event_date_str not in dates:
        return None

    idx = dates.index(event_date_str)

    def get_val(key, i):
        vals = daily.get(key, [])
        if i < len(vals) and vals[i] is not None:
            return vals[i]
        return np.nan

    # Event day values
    temp_mean = get_val("temperature_2m_mean", idx)
    temp_max = get_val("temperature_2m_max", idx)
    temp_min = get_val("temperature_2m_min", idx)
    pressure_mean = get_val("surface_pressure_mean", idx)
    pressure_max = get_val("surface_pressure_max", idx)
    pressure_min = get_val("surface_pressure_min", idx)
    precip = get_val("precipitation_sum", idx)
    wind_max = get_val("windspeed_10m_max", idx)
    wind_gust = get_val("windgusts_10m_max", idx)
    humidity = get_val("relative_humidity_2m_mean", idx)
    cloud = get_val("cloudcover_mean", idx)
    dewpoint = get_val("dewpoint_2m_mean", idx)

    # Convert wind from km/h to m/s
    if not np.isnan(wind_max):
        wind_max_ms = round(wind_max / 3.6, 1)
    else:
        wind_max_ms = np.nan

    # wind_mean: approximate as 0.6 * wind_max (Open-Meteo only gives max)
    if not np.isnan(wind_max):
        wind_mean_ms = round(wind_max * 0.6 / 3.6, 1)
    else:
        wind_mean_ms = np.nan

    # Cloud cover: Open-Meteo gives % (0-100), convert to fraction (0-1)
    if not np.isnan(cloud):
        cloud_frac = round(cloud / 100.0, 2)
    else:
        cloud_frac = np.nan

    # Pressure deltas
    pressure_delta_1d = np.nan
    pressure_delta_3d = np.nan
    if not np.isnan(pressure_mean):
        # 1-day ago
        if idx >= 1:
            p_1d = get_val("surface_pressure_mean", idx - 1)
            if not np.isnan(p_1d):
                pressure_delta_1d = round(pressure_mean - p_1d, 1)
        # 3-days ago
        if idx >= 3:
            p_3d = get_val("surface_pressure_mean", idx - 3)
            if not np.isnan(p_3d):
                pressure_delta_3d = round(pressure_mean - p_3d, 1)

    return {
        "temp_mean": round(temp_mean, 1) if not np.isnan(temp_mean) else np.nan,
        "temp_max": round(temp_max, 1) if not np.isnan(temp_max) else np.nan,
        "temp_min": round(temp_min, 1) if not np.isnan(temp_min) else np.nan,
        "pressure_mean": round(pressure_mean, 1) if not np.isnan(pressure_mean) else np.nan,
        "pressure_min": round(pressure_min, 1) if not np.isnan(pressure_min) else np.nan,
        "pressure_max": round(pressure_max, 1) if not np.isnan(pressure_max) else np.nan,
        "pressure_delta_1d": pressure_delta_1d,
        "pressure_delta_3d": pressure_delta_3d,
        "wind_mean": wind_mean_ms,
        "wind_max": wind_max_ms,
        "precip_total": round(precip, 1) if not np.isnan(precip) else np.nan,
        "humidity_mean": round(humidity, 1) if not np.isnan(humidity) else np.nan,
        "cloud_cover": cloud_frac,
        "dew_point": round(dewpoint, 1) if not np.isnan(dewpoint) else np.nan,
    }


def main():
    log("=" * 70)
    log("OPEN-METEO WEATHER GAP FILLER")
    log("=" * 70)

    # Load all events
    events = load_all_events()

    # Load existing weather
    if OUTPUT_CSV.exists():
        existing_wx = pd.read_csv(OUTPUT_CSV)
        existing_ids = set(existing_wx["event_id"].values)
        log(f"Existing weather: {len(existing_ids)} event_ids")
    else:
        existing_ids = set()
        log("No existing weather file found")

    # Load checkpoint if exists
    checkpoint_ids = set()
    checkpoint_results = []
    if CHECKPOINT_CSV.exists():
        cp = pd.read_csv(CHECKPOINT_CSV)
        checkpoint_ids = set(cp["event_id"].values)
        checkpoint_results = cp.to_dict("records")
        log(f"Checkpoint: {len(checkpoint_ids)} events already fetched")

    # Find events needing weather (have coords, not in existing or checkpoint)
    already_done = existing_ids | checkpoint_ids
    missing = events[~events["event_id"].isin(already_done)].reset_index(drop=True)
    log(f"Events needing weather: {len(missing)}")

    if len(missing) == 0:
        log("Nothing to fetch!")
        if checkpoint_results:
            _append_checkpoint_to_output(checkpoint_results)
        return

    results = list(checkpoint_results)
    n_success = 0
    n_fail = 0
    n_already = len(checkpoint_ids)

    for i, row in missing.iterrows():
        event_id = row["event_id"]
        date_str = row["date_str"]
        lat, lon = row["lat"], row["lon"]

        wx = fetch_openmeteo_weather(lat, lon, date_str)
        time.sleep(RATE_LIMIT_SLEEP)

        record = {
            "event_id": event_id,
            "date": date_str,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "station_id": "open-meteo",
            "station_distance_km": 0.0,
        }

        if wx:
            record.update(wx)
            n_success += 1
        else:
            # Fill NaN for all weather columns
            for c in ["temp_mean", "temp_max", "temp_min", "pressure_mean",
                       "pressure_min", "pressure_max", "pressure_delta_1d",
                       "pressure_delta_3d", "wind_mean", "wind_max",
                       "precip_total", "humidity_mean", "cloud_cover", "dew_point"]:
                record[c] = np.nan
            n_fail += 1

        results.append(record)

        total = n_already + n_success + n_fail
        if (n_success + n_fail) % 10 == 0:
            pct = n_success / max(1, n_success + n_fail) * 100
            log(f"  Progress: {total}/{len(missing) + n_already} "
                f"(success={n_success}, fail={n_fail}, rate={pct:.0f}%)")

        if (n_success + n_fail) % CHECKPOINT_INTERVAL == 0:
            _save_checkpoint(results)

    # Save final checkpoint
    _save_checkpoint(results)

    # Append to output
    _append_checkpoint_to_output(results)

    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"  Fetched:  {n_success}")
    log(f"  Failed:   {n_fail}")
    log(f"  Total weather rows now: check output file")


def _save_checkpoint(results):
    df = pd.DataFrame(results)
    df.to_csv(CHECKPOINT_CSV, index=False)
    log(f"  Checkpoint saved: {len(df)} records")


def _append_checkpoint_to_output(results):
    """Append new results to existing weather CSV."""
    new_df = pd.DataFrame(results)
    # Only keep rows with actual weather data (temp_mean not NaN)
    new_df = new_df[new_df["temp_mean"].notna()].reset_index(drop=True)
    log(f"New rows with weather data: {len(new_df)}")

    if len(new_df) == 0:
        log("No new weather data to append")
        return

    # Ensure column order matches existing file
    col_order = [
        "event_id", "date", "lat", "lon", "station_id", "station_distance_km",
        "temp_mean", "temp_max", "temp_min",
        "pressure_mean", "pressure_min", "pressure_max",
        "pressure_delta_1d", "pressure_delta_3d",
        "wind_mean", "wind_max", "precip_total",
        "humidity_mean", "cloud_cover", "dew_point",
    ]
    # Reorder (handle missing cols gracefully)
    for c in col_order:
        if c not in new_df.columns:
            new_df[c] = np.nan
    new_df = new_df[col_order]

    if OUTPUT_CSV.exists():
        existing = pd.read_csv(OUTPUT_CSV)
        # Don't duplicate
        new_df = new_df[~new_df["event_id"].isin(existing["event_id"])].reset_index(drop=True)
        if len(new_df) == 0:
            log("All events already in output file")
            return
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(OUTPUT_CSV, index=False)
    log(f"Output saved: {len(combined)} total rows ({len(new_df)} new)")

    # Clean up checkpoint
    if CHECKPOINT_CSV.exists():
        CHECKPOINT_CSV.unlink()
        log("Removed checkpoint file")


if __name__ == "__main__":
    main()
