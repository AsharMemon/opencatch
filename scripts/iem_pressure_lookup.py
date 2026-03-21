#!/usr/bin/env python3
"""
IEM (Iowa Environmental Mesonet) ASOS/AWOS pressure lookup utility.

Fetches barometric pressure and other weather data from airport weather stations
via the IEM ASOS download service. Used to fill gaps in NOAA GHCN-Daily data
which has poor pressure coverage.

IEM has NO rate limits but we use 0.1s delays to be respectful.
"""

import argparse
import math
import time
import warnings
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("future.no_silent_downcasting", True)

BASE_DIR = Path(__file__).resolve().parent.parent
WEATHER_CSV = BASE_DIR / "castline/validation/data/raw/flw_weather_noaa.csv"
ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
STATION_URL = "https://mesonet.agron.iastate.edu/api/1/stations.json"

# US state ASOS network codes
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MA", "MD",
    "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH",
    "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
]

# Cache for station list
_station_cache = None
_station_cache_path = BASE_DIR / "castline/validation/data/raw/iem_asos_stations.csv"


def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_all_asos_stations(force_refresh=False):
    """Fetch all US ASOS/AWOS station locations from IEM."""
    global _station_cache

    if _station_cache is not None and not force_refresh:
        return _station_cache

    if _station_cache_path.exists() and not force_refresh:
        _station_cache = pd.read_csv(_station_cache_path)
        print(f"  Loaded {len(_station_cache)} cached ASOS stations")
        return _station_cache

    print("  Fetching ASOS station list from IEM (one-time)...")
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
                print(f"    Warning: failed to fetch {net_type}: {e}")
                continue

    df = pd.DataFrame(all_stations)
    df = df.dropna(subset=["lat", "lon"])
    df.to_csv(_station_cache_path, index=False)
    _station_cache = df
    print(f"  Fetched {len(df)} ASOS/AWOS stations, cached to {_station_cache_path}")
    return df


def find_nearest_stations(lat, lon, stations_df, n=3):
    """Find n nearest ASOS stations to a lat/lon."""
    dists = stations_df.apply(
        lambda r: haversine_km(lat, lon, r["lat"], r["lon"]), axis=1
    )
    nearest_idx = dists.nsmallest(n).index
    result = stations_df.loc[nearest_idx].copy()
    result["distance_km"] = dists.loc[nearest_idx].values
    return result


def fetch_asos_day(station_id, date_str, days_before=0):
    """
    Fetch ASOS data for a station and date (plus optional days before).
    Returns DataFrame with hourly/sub-hourly observations.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = dt - timedelta(days=days_before)
    end = dt + timedelta(days=1)

    params = {
        "station": station_id.replace("K", "", 1) if station_id.startswith("K") else station_id,
        "data": ["mslp", "alti", "tmpf", "dwpf", "drct", "sknt", "p01i", "relh", "feel"],
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
        "tz": "UTC",
        "format": "comma",
        "latlon": "yes",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": ["3", "4"],  # METAR and special
    }

    try:
        resp = requests.get(ASOS_URL, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        # Skip comment lines
        lines = [l for l in resp.text.split("\n") if not l.startswith("#")]
        text = "\n".join(lines)
        if len(text.strip()) < 20:
            return None
        df = pd.read_csv(StringIO(text), na_values=["M"])
        for col in ["mslp", "alti", "tmpf", "dwpf", "drct", "sknt", "p01i", "relh", "feel"]:
            if col in df.columns:
                # Handle trace precipitation
                df[col] = df[col].replace("T", 0.001)
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "valid" in df.columns:
            df["valid"] = pd.to_datetime(df["valid"], errors="coerce")
            df["date"] = df["valid"].dt.date.astype(str)
        return df
    except Exception as e:
        print(f"    Warning: ASOS fetch failed for {station_id} on {date_str}: {e}")
        return None


def summarize_day(obs_df, target_date_str):
    """Summarize sub-hourly observations into daily values."""
    if obs_df is None or len(obs_df) == 0:
        return None

    day_obs = obs_df[obs_df["date"] == target_date_str] if "date" in obs_df.columns else obs_df
    if len(day_obs) == 0:
        return None

    result = {}

    # Pressure: mean sea-level pressure in hPa (mslp is already in mb = hPa)
    if "mslp" in day_obs.columns:
        valid_p = day_obs["mslp"].dropna()
        if len(valid_p) > 0:
            result["pressure_hpa"] = round(valid_p.mean(), 1)

    # If mslp missing, try altimeter setting (convert inches Hg to hPa)
    if "pressure_hpa" not in result and "alti" in day_obs.columns:
        valid_a = day_obs["alti"].dropna()
        if len(valid_a) > 0:
            result["pressure_hpa"] = round(valid_a.mean() * 33.8639, 1)

    # Temperature (F to C)
    if "tmpf" in day_obs.columns:
        valid_t = day_obs["tmpf"].dropna()
        if len(valid_t) > 0:
            temps_c = (valid_t - 32) * 5.0 / 9.0
            result["temp_max"] = round(temps_c.max(), 1)
            result["temp_min"] = round(temps_c.min(), 1)
            result["temp_mean"] = round(temps_c.mean(), 1)

    # Wind (knots to kph)
    if "sknt" in day_obs.columns:
        valid_w = day_obs["sknt"].dropna()
        if len(valid_w) > 0:
            wind_kph = valid_w * 1.852
            result["wind_max_kph"] = round(wind_kph.max(), 1)
            result["wind_avg_kph"] = round(wind_kph.mean(), 1)

    # Wind direction (circular mean)
    if "drct" in day_obs.columns:
        valid_d = day_obs["drct"].dropna()
        if len(valid_d) > 0:
            sin_avg = np.sin(np.radians(valid_d)).mean()
            cos_avg = np.cos(np.radians(valid_d)).mean()
            result["wind_dir"] = round(np.degrees(np.arctan2(sin_avg, cos_avg)) % 360, 0)

    # Precipitation (inches to mm)
    if "p01i" in day_obs.columns:
        valid_precip = day_obs["p01i"].dropna()
        if len(valid_precip) > 0:
            result["precip_mm"] = round(valid_precip.sum() * 25.4, 1)

    # Humidity
    if "relh" in day_obs.columns:
        valid_h = day_obs["relh"].dropna()
        if len(valid_h) > 0:
            result["humidity_pct"] = round(valid_h.mean(), 0)

    return result if result else None


def classify_front_phase(p0, p1, p2, p3):
    """Classify frontal phase from pressure readings (day0, day-1, day-2, day-3)."""
    if pd.isna(p0) or pd.isna(p1):
        return None

    d1 = p0 - p1
    d2 = p0 - p2 if pd.notna(p2) else None
    d3 = p0 - p3 if pd.notna(p3) else None

    if d1 > 5:
        return "post_frontal"
    elif d1 < -5:
        return "pre_frontal"
    elif d1 > 2:
        return "clearing"
    elif d1 < -2:
        return "approaching"
    else:
        return "stable"


def fill_pressure_gaps(weather_csv=None, dry_run=False):
    """Main function: fill missing pressure data using IEM ASOS."""
    csv_path = Path(weather_csv) if weather_csv else WEATHER_CSV
    print(f"Loading weather data from {csv_path}")
    df = pd.read_csv(csv_path)

    total = len(df)
    before_pressure = df["pressure_hpa"].notna().sum()
    print(f"  Total events: {total}")
    print(f"  Events with pressure: {before_pressure} ({before_pressure/total*100:.1f}%)")
    print(f"  Events missing pressure: {total - before_pressure}")

    # Get station list
    stations = fetch_all_asos_stations()

    # Find rows needing pressure
    missing_mask = df["pressure_hpa"].isna()
    missing_indices = df[missing_mask].index.tolist()

    if len(missing_indices) == 0:
        print("  No missing pressure data!")
        return df

    print(f"\n  Fetching pressure for {len(missing_indices)} events...")

    # Group by unique lat/lon/date to minimize API calls
    # But also need to fetch lag days
    filled_count = 0
    fail_count = 0

    # Cache: (station_id, date) -> daily_summary
    day_cache = {}

    for i, idx in enumerate(missing_indices):
        row = df.loc[idx]
        lat, lon = row["lat"], row["lon"]
        date_str = str(row["date"])

        if pd.isna(lat) or pd.isna(lon):
            fail_count += 1
            continue

        # Find nearest stations
        nearest = find_nearest_stations(lat, lon, stations, n=3)

        # Try each station until we get pressure
        got_data = False
        for _, st in nearest.iterrows():
            sid = st["station_id"]
            cache_key = (sid, date_str)

            if cache_key not in day_cache:
                # Fetch 4 days of data (event day + 3 lag days)
                obs = fetch_asos_day(sid, date_str, days_before=3)
                time.sleep(0.1)

                if obs is not None and len(obs) > 0:
                    # Cache all days
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    for d in range(4):
                        d_str = (dt - timedelta(days=d)).strftime("%Y-%m-%d")
                        summary = summarize_day(obs, d_str)
                        day_cache[(sid, d_str)] = summary
                else:
                    day_cache[cache_key] = None

            summary = day_cache.get(cache_key)
            if summary and "pressure_hpa" in summary:
                # Fill pressure
                df.at[idx, "pressure_hpa"] = summary["pressure_hpa"]

                # Fill other missing fields from ASOS
                for field in ["temp_max", "temp_min", "temp_mean", "precip_mm",
                              "wind_max_kph", "wind_avg_kph", "wind_dir", "humidity_pct"]:
                    if pd.isna(df.at[idx, field]) and field in summary:
                        df.at[idx, field] = summary[field]

                # Update station info
                if pd.isna(df.at[idx, "station_id"]) or df.at[idx, "station_id"] == "":
                    df.at[idx, "station_id"] = sid
                    df.at[idx, "station_distance_km"] = round(st["distance_km"], 1)

                # Fill lag pressures
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                for lag in [1, 2, 3]:
                    lag_col = f"pressure_hpa_lag{lag}"
                    if pd.isna(df.at[idx, lag_col]):
                        lag_date = (dt - timedelta(days=lag)).strftime("%Y-%m-%d")
                        lag_summary = day_cache.get((sid, lag_date))
                        if lag_summary and "pressure_hpa" in lag_summary:
                            df.at[idx, lag_col] = lag_summary["pressure_hpa"]

                        # Also fill temp/wind lags if missing
                        for base_field, lag_field in [
                            ("temp_mean", f"temp_mean_lag{lag}"),
                            ("temp_max", f"temp_max_lag{lag}"),
                            ("temp_min", f"temp_min_lag{lag}"),
                            ("precip_mm", f"precip_mm_lag{lag}"),
                            ("wind_avg_kph", f"wind_avg_kph_lag{lag}"),
                        ]:
                            if lag_field in df.columns and pd.isna(df.at[idx, lag_field]):
                                if lag_summary and base_field in lag_summary:
                                    df.at[idx, lag_field] = lag_summary[base_field]

                # Compute pressure deltas and front phase
                p0 = df.at[idx, "pressure_hpa"]
                p1 = df.at[idx, "pressure_hpa_lag1"]
                p2 = df.at[idx, "pressure_hpa_lag2"]
                p3 = df.at[idx, "pressure_hpa_lag3"]

                if pd.notna(p0) and pd.notna(p1):
                    df.at[idx, "pressure_delta_1d"] = round(p0 - p1, 1)
                if pd.notna(p0) and pd.notna(p2):
                    df.at[idx, "pressure_delta_2d"] = round(p0 - p2, 1)
                if pd.notna(p0) and pd.notna(p3):
                    df.at[idx, "pressure_delta_3d"] = round(p0 - p3, 1)

                front = classify_front_phase(p0, p1, p2, p3)
                if front:
                    df.at[idx, "front_phase"] = front

                got_data = True
                filled_count += 1
                break

        if not got_data:
            fail_count += 1

        if (i + 1) % 25 == 0 or i == len(missing_indices) - 1:
            print(f"    Progress: {i+1}/{len(missing_indices)} "
                  f"(filled: {filled_count}, failed: {fail_count})")

    # Final stats
    after_pressure = df["pressure_hpa"].notna().sum()
    print(f"\n  === RESULTS ===")
    print(f"  Pressure before: {before_pressure}/{total} ({before_pressure/total*100:.1f}%)")
    print(f"  Pressure after:  {after_pressure}/{total} ({after_pressure/total*100:.1f}%)")
    print(f"  Newly filled:    {filled_count}")
    print(f"  Still missing:   {total - after_pressure}")

    # Lag coverage
    for lag in [1, 2, 3]:
        col = f"pressure_hpa_lag{lag}"
        coverage = df[col].notna().sum()
        print(f"  {col}: {coverage}/{total} ({coverage/total*100:.1f}%)")

    front_coverage = df["front_phase"].notna().sum()
    print(f"  front_phase: {front_coverage}/{total} ({front_coverage/total*100:.1f}%)")

    if not dry_run:
        df.to_csv(csv_path, index=False)
        print(f"\n  Saved to {csv_path}")

    return df


def build_frontal_timeseries(weather_csv=None):
    """
    Build supplementary file with 4-day pressure timeseries for frontal detection.
    For each event: pressure on event day, day-1, day-2, day-3.
    """
    csv_path = Path(weather_csv) if weather_csv else WEATHER_CSV
    df = pd.read_csv(csv_path)

    frontal = df[["event_id", "date", "lat", "lon",
                  "pressure_hpa", "pressure_hpa_lag1", "pressure_hpa_lag2", "pressure_hpa_lag3",
                  "pressure_delta_1d", "pressure_delta_2d", "pressure_delta_3d",
                  "front_phase"]].copy()

    # Add rate-of-change features
    frontal["pressure_roc_12h"] = np.nan  # would need sub-daily data
    frontal["pressure_trend"] = np.where(
        frontal["pressure_delta_1d"] > 3, "rising",
        np.where(frontal["pressure_delta_1d"] < -3, "falling", "steady")
    )

    out_path = csv_path.parent / "flw_frontal_pressure.csv"
    frontal.to_csv(out_path, index=False)
    print(f"  Saved frontal timeseries to {out_path}")
    print(f"  Events with complete 4-day pressure: "
          f"{frontal[['pressure_hpa','pressure_hpa_lag1','pressure_hpa_lag2','pressure_hpa_lag3']].notna().all(axis=1).sum()}/{len(frontal)}")
    return frontal


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fill pressure gaps using IEM ASOS data")
    parser.add_argument("--csv", type=str, default=None, help="Path to weather CSV")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    parser.add_argument("--frontal-only", action="store_true", help="Only build frontal timeseries")
    args = parser.parse_args()

    if args.frontal_only:
        build_frontal_timeseries(args.csv)
    else:
        df = fill_pressure_gaps(args.csv, dry_run=args.dry_run)
        build_frontal_timeseries(args.csv)
