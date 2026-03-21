"""Robust Open-Meteo weather fetcher with proper rate limiting.

Open-Meteo free tier: 10,000 requests/day, ~600/hour.
Strategy: 1 request per 6 seconds = 600/hour, well under limit.
Each request covers one location's full date range.
"""
import json, ssl, time, urllib.request, urllib.parse, sys
import pandas as pd
import numpy as np
from collections import defaultdict
from pathlib import Path

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

BASE = Path("castline/validation")
CACHE_PATH = BASE / "data/raw/openmeteo_weather_cache.csv"
DATASET_PATH = BASE / "data/assembled/validation_dataset_v7.csv"

DAILY_VARS = ",".join([
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "apparent_temperature_max", "precipitation_sum", "rain_sum",
    "windspeed_10m_max", "windgusts_10m_max", "winddirection_10m_dominant",
    "shortwave_radiation_sum", "et0_fao_evapotranspiration",
    "pressure_msl_max", "pressure_msl_min", "pressure_msl_mean",
])

COL_MAP = {
    "temperature_2m_max": "om_air_temp_max",
    "temperature_2m_min": "om_air_temp_min",
    "temperature_2m_mean": "om_air_temp_mean",
    "apparent_temperature_max": "om_apparent_temp",
    "precipitation_sum": "om_precip_mm",
    "rain_sum": "om_rain_mm",
    "windspeed_10m_max": "om_wind_max_kph",
    "windgusts_10m_max": "om_wind_gust_kph",
    "winddirection_10m_dominant": "om_wind_dir_dominant",
    "shortwave_radiation_sum": "om_solar_radiation",
    "et0_fao_evapotranspiration": "om_et0",
    "pressure_msl_max": "om_pressure_max",
    "pressure_msl_min": "om_pressure_min",
    "pressure_msl_mean": "om_pressure_msl",
}


def load_needed():
    df = pd.read_csv(DATASET_PATH, low_memory=False)
    print(f"Dataset: {len(df)} rows", flush=True)

    try:
        cached = pd.read_csv(CACHE_PATH)
        cached_keys = set(zip(
            cached.lat.round(2), cached.lon.round(2), cached.date.astype(str)
        ))
        print(f"Cache: {len(cached)} existing rows", flush=True)
    except Exception:
        cached = pd.DataFrame()
        cached_keys = set()

    df["_lr"] = df.lat.round(2)
    df["_lonr"] = df.lon.round(2)
    all_keys = set(zip(df._lr, df._lonr, df.date.astype(str)))
    needed = all_keys - cached_keys

    loc_dates = defaultdict(list)
    for lat, lon, date in needed:
        if pd.isna(lat) or pd.isna(lon) or pd.isna(date):
            continue
        loc_dates[(lat, lon)].append(date)

    print(f"Need: {len(needed)} events across {len(loc_dates)} locations", flush=True)
    return loc_dates, cached


def fetch_one(lat, lon, dates):
    """Fetch weather for one location, all dates in range."""
    dates_sorted = sorted(set(str(d) for d in dates))
    # Filter out dates before 1940 (ERA5 coverage start)
    dates_sorted = [d for d in dates_sorted if d >= "1940-01-01"]
    if not dates_sorted:
        return []

    start_date = dates_sorted[0]
    end_date = dates_sorted[-1]

    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start_date, "end_date": end_date,
        "daily": DAILY_VARS,
        "timezone": "America/New_York",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "CASTLINE/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        data = json.loads(resp.read().decode())

    rows = []
    if "daily" in data:
        daily = data["daily"]
        dates_set = set(dates_sorted)
        times = daily.get("time", [])
        for i, d in enumerate(times):
            if d in dates_set:
                row = {"lat": lat, "lon": lon, "date": d}
                for api_col, our_col in COL_MAP.items():
                    vals = daily.get(api_col, [])
                    row[our_col] = vals[i] if i < len(vals) else None
                rows.append(row)
    return rows


def main():
    loc_dates, cached = load_needed()
    if not loc_dates:
        print("All weather data already cached!", flush=True)
        return

    items = list(loc_dates.items())
    new_rows = []
    done = 0
    errors = 0
    consecutive_429 = 0

    for (lat, lon), dates in items:
        try:
            rows = fetch_one(lat, lon, dates)
            new_rows.extend(rows)
            done += 1
            consecutive_429 = 0

            if done % 50 == 0:
                # Save checkpoint
                if new_rows:
                    batch = pd.DataFrame(new_rows)
                    total = pd.concat([cached, batch], ignore_index=True) if len(cached) > 0 else batch
                    total.to_csv(CACHE_PATH, index=False)
                print(f"  {done}/{len(items)} locs | {len(new_rows)} rows | {errors} errors", flush=True)

            time.sleep(6)  # 600/hour = 1 every 6 seconds

        except urllib.error.HTTPError as e:
            errors += 1
            done += 1
            if e.code == 429:
                consecutive_429 += 1
                backoff = min(300, 30 * consecutive_429)  # 30s, 60s, ... up to 5 min
                print(f"  429 rate limited (#{consecutive_429}), backing off {backoff}s", flush=True)
                # Save what we have before sleeping
                if new_rows:
                    batch = pd.DataFrame(new_rows)
                    total = pd.concat([cached, batch], ignore_index=True) if len(cached) > 0 else batch
                    total.to_csv(CACHE_PATH, index=False)
                time.sleep(backoff)
            else:
                print(f"  HTTP {e.code} for ({lat},{lon}): {e.reason}", flush=True)
                time.sleep(6)
        except Exception as e:
            errors += 1
            done += 1
            print(f"  Error for ({lat},{lon}): {e}", flush=True)
            time.sleep(6)

    # Final save
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        final = pd.concat([cached, new_df], ignore_index=True) if len(cached) > 0 else new_df
        final.drop_duplicates(subset=["lat", "lon", "date"], keep="last", inplace=True)
        final.to_csv(CACHE_PATH, index=False)
        print(f"\nDone: {len(new_rows)} new rows, {errors} errors. Cache total: {len(final)}", flush=True)
    else:
        print(f"\nNo new rows fetched. {errors} errors.", flush=True)


if __name__ == "__main__":
    main()
