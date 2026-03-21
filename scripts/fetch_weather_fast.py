"""
Fast parallel weather fetcher for CreelCat surveys.
Uses ThreadPool for concurrent requests with rate limiting and retry logic.
Saves incrementally every 200 records.
"""
import pandas as pd
import numpy as np
import json
import time
import urllib.request
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

SURVEY_PATH = "/workspace/castline/raw/Survey_Data.csv"
OUT_PATH = "/workspace/castline/raw/creelcat_weather.csv"

save_lock = Lock()
results_buffer = []
total_fetched = 0
total_errors = 0


def fetch_one(key):
    """Fetch weather for a single key with retry."""
    parts = key.split('_')
    lat, lon, year, month = float(parts[0]), float(parts[1]), int(parts[2]), int(parts[3])

    if year < 1940 or year > 2024:
        return None

    end_day = 28 if month == 2 else 30 if month in [4, 6, 9, 11] else 31
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{end_day}"

    url = (f"https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           f"&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
           f"precipitation_sum,windspeed_10m_max,pressure_msl_mean,"
           f"shortwave_radiation_sum"
           f"&timezone=auto")

    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'CastlineResearch/1.0')
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                d = data.get('daily', {})

                tm = [x for x in d.get('temperature_2m_mean', []) if x is not None]
                tx = [x for x in d.get('temperature_2m_max', []) if x is not None]
                tn = [x for x in d.get('temperature_2m_min', []) if x is not None]
                pr = [x for x in d.get('precipitation_sum', []) if x is not None]
                wi = [x for x in d.get('windspeed_10m_max', []) if x is not None]
                ps = [x for x in d.get('pressure_msl_mean', []) if x is not None]
                sr = [x for x in d.get('shortwave_radiation_sum', []) if x is not None]

                return {
                    'weather_key': key, 'lat_r': lat, 'lon_r': lon,
                    'year': year, 'month': month,
                    'wx_temp_mean': np.mean(tm) if tm else np.nan,
                    'wx_temp_max': np.max(tx) if tx else np.nan,
                    'wx_temp_min': np.min(tn) if tn else np.nan,
                    'wx_temp_range': np.mean([mx - mn for mx, mn in zip(tx, tn)]) if tx and tn else np.nan,
                    'wx_precip_total': np.sum(pr) if pr else np.nan,
                    'wx_precip_days': sum(1 for p in pr if p > 0.1) if pr else np.nan,
                    'wx_wind_max': np.max(wi) if wi else np.nan,
                    'wx_wind_mean': np.mean(wi) if wi else np.nan,
                    'wx_pressure_mean': np.mean(ps) if ps else np.nan,
                    'wx_pressure_range': (np.max(ps) - np.min(ps)) if ps and len(ps) > 1 else np.nan,
                    'wx_solar_mean': np.mean(sr) if sr else np.nan,
                }
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait = 2 ** (attempt + 2)  # 4, 8, 16 seconds
                time.sleep(wait)
            elif e.code >= 500:
                time.sleep(2)
            else:
                return None
        except Exception:
            time.sleep(1)
    return None


def save_incremental(new_rows):
    """Thread-safe incremental save."""
    with save_lock:
        if os.path.exists(OUT_PATH):
            existing = pd.read_csv(OUT_PATH)
            df = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        else:
            df = pd.DataFrame(new_rows)
        df.to_csv(OUT_PATH, index=False)
        return len(df)


def main():
    global total_fetched, total_errors

    survey = pd.read_csv(SURVEY_PATH, low_memory=False)
    survey = survey[survey['Lat'].notna() & survey['Lon'].notna()]
    dates = pd.to_datetime(survey['Start_Date'], errors='coerce')
    survey['month'] = dates.dt.month.fillna(pd.to_numeric(survey['Start_Month'], errors='coerce'))
    survey['weather_key'] = (survey['Lat'].round(0).astype(str) + '_' +
                              survey['Lon'].round(0).astype(str) + '_' +
                              survey['Year'].astype(int).astype(str) + '_' +
                              survey['month'].fillna(6).astype(int).astype(str))

    all_keys = survey['weather_key'].unique()
    print(f"Total unique keys: {len(all_keys)}")

    # Load existing
    done_keys = set()
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH)
        done_keys = set(existing['weather_key'])
        print(f"Already fetched: {len(done_keys)}")

    remaining = [k for k in all_keys if k not in done_keys]
    print(f"Remaining: {len(remaining)}")

    if not remaining:
        print("All done!")
        return

    # Process in batches with ThreadPool (4 concurrent to avoid rate limits)
    batch_size = 200
    buffer = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for i, key in enumerate(remaining):
            futures[executor.submit(fetch_one, key)] = key

            # Throttle submission
            if (i + 1) % 4 == 0:
                time.sleep(0.2)  # 20 req/s max

        for future in as_completed(futures):
            result = future.result()
            if result:
                buffer.append(result)
                total_fetched += 1
            else:
                total_errors += 1

            # Save every batch_size records
            if len(buffer) >= batch_size:
                total = save_incremental(buffer)
                elapsed = time.time() - start_time
                rate = total_fetched / elapsed if elapsed > 0 else 0
                print(f"  Saved: {total} total | fetched={total_fetched} errors={total_errors} | {rate:.1f} req/s")
                buffer = []

    # Save remainder
    if buffer:
        total = save_incremental(buffer)
        print(f"  Final save: {total} total")

    elapsed = time.time() - start_time
    print(f"\nDone: fetched={total_fetched}, errors={total_errors}, time={elapsed:.0f}s")


if __name__ == '__main__':
    main()
