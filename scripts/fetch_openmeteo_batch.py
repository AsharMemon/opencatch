"""Fetch Open-Meteo historical weather - BATCHED by location.

Instead of one API call per event (6000+), we batch by unique location
and fetch the full date range at once. This reduces API calls to ~2000
and avoids rate limiting.
"""
import pandas as pd
import numpy as np
import requests
import time
from pathlib import Path
from datetime import datetime

BASE_DIR = Path("/Users/Ashar/Documents/fish")
DATASET_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"
OUTPUT_PATH = BASE_DIR / "castline/validation/data/raw/openmeteo_weather_full.csv"
CHECKPOINT_PATH = BASE_DIR / "castline/validation/data/raw/openmeteo_batch_checkpoint.csv"

API_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_DELAY = 2.0
MAX_RETRIES = 5
CHECKPOINT_EVERY = 25

DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "apparent_temperature_max", "apparent_temperature_min", "apparent_temperature_mean",
    "precipitation_sum", "rain_sum", "snowfall_sum", "precipitation_hours",
    "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant",
    "shortwave_radiation_sum", "et0_fao_evapotranspiration",
    "pressure_msl_max", "pressure_msl_min", "pressure_msl_mean",
]


def fetch_location_batch(lat, lon, start_date, end_date):
    """Fetch all daily weather for a location in one API call."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(API_URL, params=params, timeout=60)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 400:
                print(f"  Bad request for ({lat},{lon}) {start_date}-{end_date}", flush=True)
                return None
            r.raise_for_status()
            data = r.json()

            if "daily" not in data:
                return None

            daily = data["daily"]
            dates = daily.get("time", [])
            rows = []
            for i, d in enumerate(dates):
                row = {"lat": lat, "lon": lon, "date": d}
                for var in DAILY_VARS:
                    col_name = var_to_col(var)
                    vals = daily.get(var, [])
                    row[col_name] = vals[i] if i < len(vals) else None
                rows.append(row)
            return rows

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  Failed after {MAX_RETRIES} attempts: {e}", flush=True)
                return None

    return None


def var_to_col(var):
    """Convert Open-Meteo variable name to column name."""
    mapping = {
        "temperature_2m_max": "om_air_temp_max",
        "temperature_2m_min": "om_air_temp_min",
        "temperature_2m_mean": "om_air_temp_mean",
        "apparent_temperature_max": "om_apparent_temp_max",
        "apparent_temperature_min": "om_apparent_temp_min",
        "apparent_temperature_mean": "om_apparent_temp",
        "precipitation_sum": "om_precip_mm",
        "rain_sum": "om_rain_mm",
        "snowfall_sum": "om_snowfall_mm",
        "precipitation_hours": "om_precip_hours",
        "wind_speed_10m_max": "om_wind_max_kph",
        "wind_gusts_10m_max": "om_wind_gust_kph",
        "wind_direction_10m_dominant": "om_wind_dir_dominant",
        "shortwave_radiation_sum": "om_solar_radiation",
        "et0_fao_evapotranspiration": "om_et0",
        "pressure_msl_max": "om_pressure_max",
        "pressure_msl_min": "om_pressure_min",
        "pressure_msl_mean": "om_pressure_msl",
    }
    return mapping.get(var, f"om_{var}")


def main():
    # Load dataset
    df = pd.read_csv(DATASET_PATH, low_memory=False, usecols=["lat", "lon", "date", "location"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # Group by unique location (lat/lon)
    df["_lat_r"] = df["lat"].round(2)
    df["_lon_r"] = df["lon"].round(2)

    loc_dates = df.groupby(["_lat_r", "_lon_r"]).agg(
        min_date=("date", "min"),
        max_date=("date", "max"),
        n_events=("date", "count"),
    ).reset_index()

    print(f"Unique locations: {len(loc_dates)}", flush=True)

    # Load checkpoint
    done_locs = set()
    all_results = []
    if CHECKPOINT_PATH.exists():
        cp = pd.read_csv(CHECKPOINT_PATH, low_memory=False)
        all_results = cp.to_dict("records")
        for r in all_results:
            done_locs.add((round(r["lat"], 2), round(r["lon"], 2)))
        print(f"Loaded checkpoint: {len(done_locs)} locations done, {len(all_results)} rows", flush=True)

    todo = loc_dates[~loc_dates.apply(
        lambda r: (r["_lat_r"], r["_lon_r"]) in done_locs, axis=1
    )].reset_index(drop=True)

    print(f"Locations to fetch: {len(todo)}", flush=True)

    for i, row in todo.iterrows():
        lat, lon = row["_lat_r"], row["_lon_r"]
        rows = fetch_location_batch(lat, lon, row["min_date"], row["max_date"])

        if rows:
            all_results.extend(rows)
            if (i + 1) % 10 == 0:
                success_rate = len([r for r in all_results if r.get("om_pressure_msl") is not None]) / max(len(all_results), 1)
                print(f"  {i+1}/{len(todo)} ({(i+1)/len(todo)*100:.1f}%) - {len(all_results)} rows, success={success_rate:.1%}", flush=True)

        if (i + 1) % CHECKPOINT_EVERY == 0:
            pd.DataFrame(all_results).to_csv(CHECKPOINT_PATH, index=False)
            print(f"  Checkpoint saved: {len(all_results)} rows", flush=True)

        time.sleep(REQUEST_DELAY)

    # Save final - filter to only event dates
    out_df = pd.DataFrame(all_results)
    out_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\n✓ Saved {len(out_df)} rows to {OUTPUT_PATH}", flush=True)

    # Filter to only matching event dates
    event_dates = set(zip(df["_lat_r"], df["_lon_r"], df["date"]))
    out_df["_key"] = list(zip(out_df["lat"].round(2), out_df["lon"].round(2), out_df["date"]))
    matched = out_df[out_df["_key"].isin(event_dates)].drop(columns=["_key"])
    print(f"  Matched to events: {len(matched)} rows", flush=True)


if __name__ == "__main__":
    main()
