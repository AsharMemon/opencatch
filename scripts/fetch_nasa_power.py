"""Fetch real daily weather from NASA POWER API for all events.

No rate limits, no API key needed. ~10 concurrent requests optimal.
Resolution ~50km (MERRA-2 reanalysis), 1981-present.
"""
import pandas as pd
import numpy as np
import requests
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

CACHE_FILE = "castline/validation/data/raw/nasa_power_weather.csv"
DATASET = "castline/validation/data/assembled/validation_dataset_v8.csv"

PARAMS = "T2M,T2M_MAX,T2M_MIN,T2MDEW,PRECTOTCORR,RH2M,WS2M,WS10M,WD2M,WD10M,ALLSKY_SFC_SW_DWN,PS,CLOUD_AMT"
PARAM_LIST = PARAMS.split(",")

# Map NASA POWER names to our column names
COL_MAP = {
    "T2M": "np_temp_mean_c",
    "T2M_MAX": "np_temp_max_c",
    "T2M_MIN": "np_temp_min_c",
    "T2MDEW": "np_dewpoint_c",
    "PRECTOTCORR": "np_precip_mm",
    "RH2M": "np_humidity_pct",
    "WS2M": "np_wind_2m_ms",
    "WS10M": "np_wind_10m_ms",
    "WD2M": "np_wind_dir_2m",
    "WD10M": "np_wind_dir_10m",
    "ALLSKY_SFC_SW_DWN": "np_solar_mj_m2",
    "PS": "np_pressure_kpa",
    "CLOUD_AMT": "np_cloud_pct",
}


def fetch_location_dates(lat, lon, dates):
    """Fetch weather for a single location across multiple dates.

    Groups dates by year and fetches year-ranges to minimize API calls.
    """
    results = []

    # Group dates by year for efficient fetching
    by_year = defaultdict(list)
    for d in dates:
        by_year[d.year].append(d)

    for year, year_dates in sorted(by_year.items()):
        # Fetch the full date range needed for this year
        start = min(year_dates)
        end = max(year_dates)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        url = "https://power.larc.nasa.gov/api/temporal/daily/point"
        params = {
            "start": start_str,
            "end": end_str,
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "community": "ag",
            "parameters": PARAMS,
            "format": "JSON",
        }

        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                power_data = data["properties"]["parameter"]

                # Extract only the dates we need
                for d in year_dates:
                    date_key = d.strftime("%Y%m%d")
                    row = {"lat": lat, "lon": lon, "date": d.strftime("%Y-%m-%d")}
                    for param in PARAM_LIST:
                        val = power_data.get(param, {}).get(date_key, -999)
                        col = COL_MAP[param]
                        row[col] = val if val != -999 else np.nan
                    results.append(row)
                break
            except Exception as e:
                if attempt == 2:
                    # Return NaN rows for failed fetches
                    for d in year_dates:
                        row = {"lat": lat, "lon": lon, "date": d.strftime("%Y-%m-%d")}
                        for param in PARAM_LIST:
                            row[COL_MAP[param]] = np.nan
                        results.append(row)
                else:
                    time.sleep(2 * (attempt + 1))

    return results


def main():
    print("=" * 60)
    print("NASA POWER Weather Fetch")
    print("=" * 60)

    # Load dataset
    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna() & df["lat"].notna() & df["lon"].notna()].copy()
    print(f"Events to fetch: {len(df)}")

    # Load existing cache
    cached = set()
    if os.path.exists(CACHE_FILE):
        cache_df = pd.read_csv(CACHE_FILE)
        for _, r in cache_df.iterrows():
            cached.add((round(r.lat, 4), round(r.lon, 4), r.date))
        print(f"Already cached: {len(cached)}")

    # Filter to uncached events
    need = []
    for _, r in df.iterrows():
        key = (round(r.lat, 4), round(r.lon, 4), r.date.strftime("%Y-%m-%d"))
        if key not in cached:
            need.append(r)

    if not need:
        print("All events already cached!")
        return

    print(f"Need to fetch: {len(need)} events")

    # Group by location (lat/lon rounded to 0.01 deg for batching)
    loc_groups = defaultdict(list)
    for r in need:
        key = (round(r.lat, 2), round(r.lon, 2))
        loc_groups[key].append(r.date)

    print(f"Unique locations: {len(loc_groups)}")

    # Fetch with thread pool
    all_results = []
    done = 0
    total = len(loc_groups)
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for (lat, lon), dates in loc_groups.items():
            f = pool.submit(fetch_location_dates, lat, lon, dates)
            futures[f] = (lat, lon, len(dates))

        for f in as_completed(futures):
            lat, lon, n_dates = futures[f]
            done += 1
            try:
                results = f.result()
                all_results.extend(results)
            except Exception as e:
                print(f"  ERROR {lat},{lon}: {e}")

            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} locations ({len(all_results)} rows) "
                      f"[{elapsed:.0f}s, {rate:.1f} loc/s, ETA {eta:.0f}s]")

                # Checkpoint save every 50 locations
                if all_results:
                    new_df = pd.DataFrame(all_results)
                    if os.path.exists(CACHE_FILE):
                        old = pd.read_csv(CACHE_FILE)
                        combined = pd.concat([old, new_df], ignore_index=True)
                        combined.drop_duplicates(subset=["lat", "lon", "date"], keep="last", inplace=True)
                    else:
                        combined = new_df
                    combined.to_csv(CACHE_FILE, index=False)

    # Final save
    if all_results:
        new_df = pd.DataFrame(all_results)
        if os.path.exists(CACHE_FILE):
            old = pd.read_csv(CACHE_FILE)
            combined = pd.concat([old, new_df], ignore_index=True)
            combined.drop_duplicates(subset=["lat", "lon", "date"], keep="last", inplace=True)
        else:
            combined = new_df
        combined.to_csv(CACHE_FILE, index=False)
        print(f"\nSaved {len(combined)} rows to {CACHE_FILE}")

    # Summary
    final = pd.read_csv(CACHE_FILE)
    valid = final[list(COL_MAP.values())].notna().mean()
    print("\nCoverage:")
    for col, pct in valid.items():
        print(f"  {col:25s}: {pct*100:.1f}%")

    print(f"\nTotal time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
