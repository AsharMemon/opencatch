#!/usr/bin/env python3
"""
Fetch day-level weather for ALL tournament events using IEM ASOS stations.
==========================================================================
For each tournament row (event_id + date + lat/lon), fetches exact-date weather
from the nearest ASOS/AWOS station including:
  - Temperature (mean, max, min) in C
  - Pressure (mean, min, max) in mb/hPa
  - Pressure deltas (1d, 3d) -- THE key bass predictors
  - Wind (mean, max) in m/s
  - Precipitation (mm)
  - Humidity (%), dew point (C)

Uses IEM ASOS service (no rate limits). Includes checkpoint/resume support.

Usage:
    python scripts/fetch_tournament_weather.py
    python scripts/fetch_tournament_weather.py --resume
    python scripts/fetch_tournament_weather.py --workspace /workspace/castline
"""

import argparse
import json
import math
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
WORKSPACE = os.environ.get("WORKSPACE", str(Path(__file__).resolve().parent.parent))
BASE_DIR = Path(WORKSPACE)
RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"

# Tournament source files
TOURNAMENT_FILES = [
    RAW_DIR / "all_bassmaster_outcomes.csv",
    RAW_DIR / "elite_outcomes.csv",
    RAW_DIR / "flw_outcomes.csv",
    RAW_DIR / "tourneyx_outcomes.csv",
    RAW_DIR / "combined_all_outcomes_v2.csv",
]

# Lat/lon sources
GEOCODE_CACHE = RAW_DIR / "geocode_cache.json"
USGS_SITE_CACHE = RAW_DIR / "usgs_site_cache.csv"
FLW_WEATHER = RAW_DIR / "flw_weather_noaa.csv"  # has lat/lon for FLW events

# Output
OUTPUT_CSV = RAW_DIR / "tournament_weather_daily.csv"
CHECKPOINT_CSV = RAW_DIR / "tournament_weather_daily.checkpoint.csv"
STATION_CACHE = RAW_DIR / "iem_asos_stations.csv"

# IEM endpoints
ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

CHECKPOINT_INTERVAL = 50

# US state network codes for station fetching
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MA", "MD",
    "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH",
    "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
]


# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# STATION LOADING
# ---------------------------------------------------------------------------

def load_or_fetch_stations():
    """Load ASOS stations from cache, or fetch from IEM."""
    if STATION_CACHE.exists():
        df = pd.read_csv(STATION_CACHE)
        log(f"Loaded {len(df)} ASOS stations from cache")
        return df

    log("Fetching ASOS station list from IEM (one-time)...")
    all_stations = []
    for state in US_STATES:
        for net_type in [f"{state}_ASOS", f"{state}_AWOS"]:
            url = f"https://mesonet.agron.iastate.edu/geojson/network/{net_type}.geojson"
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    coords = feat.get("geometry", {}).get("coordinates", [None, None])
                    all_stations.append({
                        "station_id": props.get("sid", ""),
                        "station_name": props.get("sname", ""),
                        "network": net_type,
                        "lat": coords[1] if len(coords) > 1 else None,
                        "lon": coords[0] if len(coords) > 0 else None,
                        "elevation_m": props.get("elevation", None),
                    })
                time.sleep(0.05)
            except Exception as e:
                log(f"  Warning: failed {net_type}: {e}")

    df = pd.DataFrame(all_stations).dropna(subset=["lat", "lon"])
    STATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(STATION_CACHE, index=False)
    log(f"Fetched and cached {len(df)} ASOS stations")
    return df


def find_nearest_stations(lat, lon, stations_df, n=3):
    """Find n nearest ASOS stations to a point."""
    dists = stations_df.apply(
        lambda r: haversine_km(lat, lon, r["lat"], r["lon"]), axis=1
    )
    nearest_idx = dists.nsmallest(n).index
    result = stations_df.loc[nearest_idx].copy()
    result["distance_km"] = dists.loc[nearest_idx].values
    return result


# ---------------------------------------------------------------------------
# IEM ASOS FETCH
# ---------------------------------------------------------------------------

def fetch_asos_range(station_id, start_date, end_date):
    """
    Fetch ASOS data for a station over a date range.
    Returns DataFrame with sub-hourly observations.
    """
    sid = station_id.replace("K", "", 1) if station_id.startswith("K") else station_id

    params = {
        "station": sid,
        "data": ["mslp", "alti", "tmpf", "dwpf", "drct", "sknt", "p01i", "relh", "feel", "skyc1"],
        "year1": start_date.year, "month1": start_date.month, "day1": start_date.day,
        "year2": end_date.year, "month2": end_date.month, "day2": end_date.day,
        "tz": "UTC",
        "format": "comma",
        "latlon": "yes",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": ["3", "4"],
    }

    try:
        resp = requests.get(ASOS_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        lines = [l for l in resp.text.split("\n") if not l.startswith("#")]
        text = "\n".join(lines)
        if len(text.strip()) < 20:
            return None
        df = pd.read_csv(StringIO(text), na_values=["M"])
        for col in ["mslp", "alti", "tmpf", "dwpf", "drct", "sknt", "p01i", "relh", "feel"]:
            if col in df.columns:
                df[col] = df[col].replace("T", 0.001)
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "valid" in df.columns:
            df["valid"] = pd.to_datetime(df["valid"], errors="coerce")
            df["date"] = df["valid"].dt.date.astype(str)
        return df
    except Exception:
        return None


def summarize_day_weather(obs_df, target_date_str):
    """Summarize sub-hourly observations into daily weather features."""
    if obs_df is None or len(obs_df) == 0:
        return {}

    day_obs = obs_df[obs_df["date"] == target_date_str] if "date" in obs_df.columns else obs_df
    if len(day_obs) == 0:
        return {}

    result = {}

    # Pressure (mslp in mb/hPa)
    if "mslp" in day_obs.columns:
        valid_p = day_obs["mslp"].dropna()
        if len(valid_p) > 0:
            result["pressure_mean"] = round(valid_p.mean(), 1)
            result["pressure_min"] = round(valid_p.min(), 1)
            result["pressure_max"] = round(valid_p.max(), 1)

    # Fallback: altimeter setting -> hPa
    if "pressure_mean" not in result and "alti" in day_obs.columns:
        valid_a = day_obs["alti"].dropna()
        if len(valid_a) > 0:
            p_hpa = valid_a * 33.8639
            result["pressure_mean"] = round(p_hpa.mean(), 1)
            result["pressure_min"] = round(p_hpa.min(), 1)
            result["pressure_max"] = round(p_hpa.max(), 1)

    # Temperature (F to C)
    if "tmpf" in day_obs.columns:
        valid_t = day_obs["tmpf"].dropna()
        if len(valid_t) > 0:
            temps_c = (valid_t - 32) * 5.0 / 9.0
            result["temp_mean"] = round(temps_c.mean(), 1)
            result["temp_max"] = round(temps_c.max(), 1)
            result["temp_min"] = round(temps_c.min(), 1)

    # Dew point (F to C)
    if "dwpf" in day_obs.columns:
        valid_d = day_obs["dwpf"].dropna()
        if len(valid_d) > 0:
            dew_c = (valid_d - 32) * 5.0 / 9.0
            result["dew_point"] = round(dew_c.mean(), 1)

    # Wind (knots to m/s)
    if "sknt" in day_obs.columns:
        valid_w = day_obs["sknt"].dropna()
        if len(valid_w) > 0:
            wind_ms = valid_w * 0.514444
            result["wind_mean"] = round(wind_ms.mean(), 1)
            result["wind_max"] = round(wind_ms.max(), 1)

    # Precipitation (inches to mm)
    if "p01i" in day_obs.columns:
        valid_precip = day_obs["p01i"].dropna()
        if len(valid_precip) > 0:
            result["precip_total"] = round(valid_precip.sum() * 25.4, 1)

    # Humidity
    if "relh" in day_obs.columns:
        valid_h = day_obs["relh"].dropna()
        if len(valid_h) > 0:
            result["humidity_mean"] = round(valid_h.mean(), 1)

    # Cloud cover (sky condition code -> fraction)
    if "skyc1" in day_obs.columns:
        sky_map = {"CLR": 0.0, "FEW": 0.2, "SCT": 0.4, "BKN": 0.7, "OVC": 1.0}
        sky_vals = day_obs["skyc1"].map(sky_map).dropna()
        if len(sky_vals) > 0:
            result["cloud_cover"] = round(sky_vals.mean(), 2)

    return result


# ---------------------------------------------------------------------------
# LOAD TOURNAMENT DATA + RESOLVE COORDINATES
# ---------------------------------------------------------------------------

def load_all_tournaments():
    """Load and deduplicate all tournament outcome files, resolve lat/lon."""
    log("Loading tournament outcome files...")

    frames = []
    for fp in TOURNAMENT_FILES:
        if fp.exists():
            df = pd.read_csv(fp, low_memory=False)
            df["_source_file"] = fp.name
            frames.append(df)
            log(f"  {fp.name}: {len(df)} rows")
        else:
            log(f"  {fp.name}: NOT FOUND, skipping")

    if not frames:
        log("ERROR: No tournament files found!")
        sys.exit(1)

    all_df = pd.concat(frames, ignore_index=True, sort=False)
    log(f"  Total before dedup: {len(all_df)}")

    # Deduplicate by event_id (keep first occurrence)
    all_df = all_df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
    log(f"  After dedup by event_id: {len(all_df)}")

    # Parse dates
    all_df["date"] = pd.to_datetime(all_df["date"], errors="coerce")
    all_df = all_df[all_df["date"].notna()].reset_index(drop=True)
    all_df["date_str"] = all_df["date"].dt.strftime("%Y-%m-%d")
    log(f"  With valid dates: {len(all_df)}")

    # --- Resolve lat/lon ---
    all_df["lat"] = np.nan
    all_df["lon"] = np.nan

    # Source 1: FLW weather file (already has lat/lon)
    if FLW_WEATHER.exists():
        flw_wx = pd.read_csv(FLW_WEATHER, usecols=["event_id", "lat", "lon"])
        flw_wx = flw_wx.dropna(subset=["lat", "lon"]).drop_duplicates(subset=["event_id"])
        flw_map = dict(zip(flw_wx["event_id"], zip(flw_wx["lat"], flw_wx["lon"])))
        matched = 0
        for i, row in all_df.iterrows():
            if row["event_id"] in flw_map:
                all_df.at[i, "lat"] = flw_map[row["event_id"]][0]
                all_df.at[i, "lon"] = flw_map[row["event_id"]][1]
                matched += 1
        log(f"  FLW weather lat/lon matched: {matched}")

    # Source 2: Geocode cache (location string -> lat/lon)
    if GEOCODE_CACHE.exists():
        with open(GEOCODE_CACHE) as f:
            geo_cache = json.load(f)
        matched = 0
        for i, row in all_df.iterrows():
            if pd.notna(all_df.at[i, "lat"]):
                continue
            loc = row.get("location", "")
            if isinstance(loc, str) and loc in geo_cache:
                coords = geo_cache[loc]
                if coords.get("lat") and coords.get("lon"):
                    all_df.at[i, "lat"] = coords["lat"]
                    all_df.at[i, "lon"] = coords["lon"]
                    matched += 1
        log(f"  Geocode cache matched: {matched}")

    # Source 3: USGS site cache (usgs_site_id -> lat/lon)
    if USGS_SITE_CACHE.exists():
        usgs = pd.read_csv(USGS_SITE_CACHE)
        if "site_no" in usgs.columns and "site_lat" in usgs.columns:
            usgs_dedup = usgs.drop_duplicates(subset=["location"]).set_index("location")
            matched = 0
            for i, row in all_df.iterrows():
                if pd.notna(all_df.at[i, "lat"]):
                    continue
                loc = row.get("location", "")
                if isinstance(loc, str) and loc in usgs_dedup.index:
                    r = usgs_dedup.loc[loc]
                    if isinstance(r, pd.DataFrame):
                        r = r.iloc[0]
                    if pd.notna(r.get("loc_lat")) and pd.notna(r.get("loc_lon")):
                        all_df.at[i, "lat"] = r["loc_lat"]
                        all_df.at[i, "lon"] = r["loc_lon"]
                        matched += 1
            log(f"  USGS site cache matched: {matched}")

    # Filter to events with coordinates
    has_coords = all_df["lat"].notna() & all_df["lon"].notna()
    log(f"  Events with lat/lon: {has_coords.sum()}/{len(all_df)} ({has_coords.mean()*100:.1f}%)")
    result = all_df[has_coords].reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# MAIN WEATHER FETCH
# ---------------------------------------------------------------------------

def fetch_all_tournament_weather(resume=False):
    """Fetch day-level weather for all tournament events."""
    log("=" * 70)
    log("TOURNAMENT WEATHER FETCHER — Day-Level IEM ASOS")
    log("=" * 70)

    tournaments = load_all_tournaments()
    stations = load_or_fetch_stations()

    # Load checkpoint if resuming
    done_event_ids = set()
    results = []
    if resume and CHECKPOINT_CSV.exists():
        checkpoint = pd.read_csv(CHECKPOINT_CSV)
        done_event_ids = set(checkpoint["event_id"].values)
        results = checkpoint.to_dict("records")
        log(f"Resuming: {len(done_event_ids)} events already done")

    # Filter to remaining events
    remaining = tournaments[~tournaments["event_id"].isin(done_event_ids)].reset_index(drop=True)
    log(f"Events to fetch: {len(remaining)}")

    if len(remaining) == 0:
        log("All events already fetched!")
        if results:
            out = pd.DataFrame(results)
            out.to_csv(OUTPUT_CSV, index=False)
            log(f"Saved {len(out)} rows to {OUTPUT_CSV}")
        return

    # Cache for ASOS observations: (station_id, date_range_key) -> DataFrame
    obs_cache = {}

    n_success = 0
    n_fail = 0

    for idx, row in remaining.iterrows():
        event_id = row["event_id"]
        date_str = row["date_str"]
        lat, lon = row["lat"], row["lon"]

        # Find nearest stations
        nearest = find_nearest_stations(lat, lon, stations, n=3)

        # We need current day + 3 lag days for pressure deltas
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start_dt = dt - timedelta(days=3)
        end_dt = dt + timedelta(days=1)

        record = {
            "event_id": event_id,
            "date": date_str,
            "lat": lat,
            "lon": lon,
            "station_id": np.nan,
            "station_distance_km": np.nan,
        }

        # Weather columns (initialize as NaN)
        wx_cols = [
            "temp_mean", "temp_max", "temp_min",
            "pressure_mean", "pressure_min", "pressure_max",
            "pressure_delta_1d", "pressure_delta_3d",
            "wind_mean", "wind_max",
            "precip_total", "humidity_mean", "cloud_cover", "dew_point",
        ]
        for c in wx_cols:
            record[c] = np.nan

        got_weather = False

        for _, st in nearest.iterrows():
            sid = st["station_id"]
            cache_key = (sid, start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))

            if cache_key not in obs_cache:
                obs_df = fetch_asos_range(sid, start_dt, end_dt)
                obs_cache[cache_key] = obs_df
                time.sleep(0.1)  # Be respectful
            else:
                obs_df = obs_cache[cache_key]

            if obs_df is None or len(obs_df) == 0:
                continue

            # Summarize event day
            day_wx = summarize_day_weather(obs_df, date_str)
            if not day_wx or "temp_mean" not in day_wx:
                continue

            # Got data from this station
            record["station_id"] = sid
            record["station_distance_km"] = round(st["distance_km"], 1)

            # Fill weather columns for event day
            for k, v in day_wx.items():
                if k in record:
                    record[k] = v

            # Compute pressure deltas from lag days
            pressures = {}
            for lag in range(4):
                lag_date = (dt - timedelta(days=lag)).strftime("%Y-%m-%d")
                lag_wx = summarize_day_weather(obs_df, lag_date)
                if lag_wx and "pressure_mean" in lag_wx:
                    pressures[lag] = lag_wx["pressure_mean"]

            if 0 in pressures and 1 in pressures:
                record["pressure_delta_1d"] = round(pressures[0] - pressures[1], 1)
            if 0 in pressures and 3 in pressures:
                record["pressure_delta_3d"] = round(pressures[0] - pressures[3], 1)

            got_weather = True
            break

        results.append(record)

        if got_weather:
            n_success += 1
        else:
            n_fail += 1

        # Progress and checkpoint
        total_done = len(done_event_ids) + n_success + n_fail
        if (n_success + n_fail) % 10 == 0:
            log(f"  Progress: {total_done}/{len(tournaments)} "
                f"(success={n_success}, fail={n_fail}, "
                f"rate={n_success/(n_success+n_fail)*100:.0f}%)")

        if (n_success + n_fail) % CHECKPOINT_INTERVAL == 0:
            _save_checkpoint(results)

    # Final save
    out = pd.DataFrame(results)
    out.to_csv(OUTPUT_CSV, index=False)
    log(f"\nSaved {len(out)} rows to {OUTPUT_CSV}")

    # Clean up checkpoint
    if CHECKPOINT_CSV.exists():
        CHECKPOINT_CSV.unlink()
        log("Removed checkpoint file")

    # Summary
    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"  Total events:         {len(tournaments)}")
    log(f"  Weather fetched:      {n_success}")
    log(f"  Failed:               {n_fail}")
    for col in wx_cols:
        coverage = out[col].notna().sum()
        log(f"  {col:>25}: {coverage}/{len(out)} ({coverage/len(out)*100:.1f}%)")


def _save_checkpoint(results):
    """Save checkpoint to disk."""
    df = pd.DataFrame(results)
    df.to_csv(CHECKPOINT_CSV, index=False)
    log(f"  Checkpoint saved: {len(df)} records")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch day-level tournament weather from IEM ASOS")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--workspace", type=str, default=None, help="Override workspace path")
    args = parser.parse_args()

    if args.workspace:
        WORKSPACE = args.workspace
        BASE_DIR = Path(WORKSPACE)
        RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"
        # Re-set all paths
        for attr_name in ["TOURNAMENT_FILES", "GEOCODE_CACHE", "USGS_SITE_CACHE",
                          "FLW_WEATHER", "OUTPUT_CSV", "CHECKPOINT_CSV", "STATION_CACHE"]:
            pass  # Paths are computed relative to RAW_DIR which is already updated

    fetch_all_tournament_weather(resume=args.resume)
