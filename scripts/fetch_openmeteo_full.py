"""Fetch Open-Meteo historical weather for ALL events in v15.

Open-Meteo Archive API is free, no API key needed, generous rate limits.
Provides: pressure, wind gusts, precipitation, apparent temperature.
These complement NASA POWER with intra-day pressure range (crucial for
barometric pressure features).
"""
import pandas as pd
import numpy as np
import requests
import time
import json
from pathlib import Path

BASE_DIR = Path("/Users/Ashar/Documents/fish")
DATASET_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"
OUTPUT_PATH = BASE_DIR / "castline/validation/data/raw/openmeteo_weather_full.csv"
CHECKPOINT_PATH = BASE_DIR / "castline/validation/data/raw/openmeteo_checkpoint.csv"

API_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_DELAY = 1.5  # Open-Meteo archive needs ~1.5s between requests
MAX_RETRIES = 3
CHECKPOINT_EVERY = 100

def fetch_weather(lat, lon, date_str):
    """Fetch daily weather from Open-Meteo Archive API."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": date_str,
        "end_date": date_str,
        "daily": ",".join([
            "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
            "apparent_temperature_max", "apparent_temperature_min", "apparent_temperature_mean",
            "precipitation_sum", "rain_sum", "snowfall_sum",
            "precipitation_hours",
            "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant",
            "shortwave_radiation_sum", "et0_fao_evapotranspiration",
            "pressure_msl_max", "pressure_msl_min", "pressure_msl_mean",
        ]),
        "timezone": "auto",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(API_URL, params=params, timeout=30)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()

            if "daily" not in data or not data["daily"]:
                return None

            daily = data["daily"]
            result = {
                "om_air_temp_max": daily.get("temperature_2m_max", [None])[0],
                "om_air_temp_min": daily.get("temperature_2m_min", [None])[0],
                "om_air_temp_mean": daily.get("temperature_2m_mean", [None])[0],
                "om_apparent_temp_max": daily.get("apparent_temperature_max", [None])[0],
                "om_apparent_temp_min": daily.get("apparent_temperature_min", [None])[0],
                "om_apparent_temp": daily.get("apparent_temperature_mean", [None])[0],
                "om_precip_mm": daily.get("precipitation_sum", [None])[0],
                "om_rain_mm": daily.get("rain_sum", [None])[0],
                "om_snowfall_mm": daily.get("snowfall_sum", [None])[0],
                "om_precip_hours": daily.get("precipitation_hours", [None])[0],
                "om_wind_max_kph": daily.get("wind_speed_10m_max", [None])[0],
                "om_wind_gust_kph": daily.get("wind_gusts_10m_max", [None])[0],
                "om_wind_dir_dominant": daily.get("wind_direction_10m_dominant", [None])[0],
                "om_solar_radiation": daily.get("shortwave_radiation_sum", [None])[0],
                "om_et0": daily.get("et0_fao_evapotranspiration", [None])[0],
                "om_pressure_max": daily.get("pressure_msl_max", [None])[0],
                "om_pressure_min": daily.get("pressure_msl_min", [None])[0],
                "om_pressure_msl": daily.get("pressure_msl_mean", [None])[0],
            }
            return result

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  Failed: {e}", flush=True)
                return None

    return None


def main():
    # Load dataset
    df = pd.read_csv(DATASET_PATH, low_memory=False, usecols=["lat", "lon", "date"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    events = df.drop_duplicates(subset=["lat", "lon", "date"]).reset_index(drop=True)
    print(f"Total unique events: {len(events)}", flush=True)

    # Load checkpoint
    done = set()
    results = []
    if CHECKPOINT_PATH.exists():
        cp = pd.read_csv(CHECKPOINT_PATH, low_memory=False)
        results = cp.to_dict("records")
        for r in results:
            done.add((round(r["lat"], 2), round(r["lon"], 2), r["date"]))
        print(f"Loaded checkpoint: {len(done)} events done", flush=True)

    todo = []
    for _, row in events.iterrows():
        key = (round(row["lat"], 2), round(row["lon"], 2), row["date"])
        if key not in done:
            todo.append(row)

    print(f"Events to fetch: {len(todo)}", flush=True)

    for i, row in enumerate(todo):
        lat, lon, date = row["lat"], row["lon"], row["date"]
        result = fetch_weather(lat, lon, date)

        if result:
            result["lat"] = lat
            result["lon"] = lon
            result["date"] = date
            results.append(result)
        else:
            results.append({"lat": lat, "lon": lon, "date": date})

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(todo)} ({(i+1)/len(todo)*100:.1f}%)", flush=True)

        if (i + 1) % CHECKPOINT_EVERY == 0:
            pd.DataFrame(results).to_csv(CHECKPOINT_PATH, index=False)

        time.sleep(REQUEST_DELAY)

    # Save final
    out_df = pd.DataFrame(results)
    out_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\n✓ Saved {len(out_df)} events to {OUTPUT_PATH}", flush=True)

    # Also save checkpoint
    out_df.to_csv(CHECKPOINT_PATH, index=False)


if __name__ == "__main__":
    main()
