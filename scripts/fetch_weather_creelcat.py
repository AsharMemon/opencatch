"""Fetch historical weather for CreelCat surveys from Open-Meteo Archive API."""
import pandas as pd
import numpy as np
import json
import time
import urllib.request
from pathlib import Path

SURVEY_PATH = "/workspace/castline/raw/Survey_Data.csv"
OUT_PATH = "/workspace/castline/raw/creelcat_weather.csv"

def main():
    survey = pd.read_csv(SURVEY_PATH, low_memory=False)
    survey = survey[survey['Lat'].notna() & survey['Lon'].notna()].copy()

    dates = pd.to_datetime(survey['Start_Date'], errors='coerce')
    survey['month'] = dates.dt.month.fillna(pd.to_numeric(survey['Start_Month'], errors='coerce'))

    # Create weather keys at 1-degree resolution
    survey['lat_r'] = survey['Lat'].round(0)
    survey['lon_r'] = survey['Lon'].round(0)
    survey['weather_key'] = (survey['lat_r'].astype(str) + '_' +
                              survey['lon_r'].astype(str) + '_' +
                              survey['Year'].astype(int).astype(str) + '_' +
                              survey['month'].fillna(6).astype(int).astype(str))

    unique_keys = survey['weather_key'].unique()
    print(f"Unique weather lookups: {len(unique_keys)}")

    results = {}
    fetched = 0
    errors = 0

    for i, key in enumerate(unique_keys):
        parts = key.split('_')
        lat, lon, year, month = float(parts[0]), float(parts[1]), int(parts[2]), int(parts[3])

        if year < 1940 or year > 2024:
            continue

        end_day = 28 if month == 2 else 30 if month in [4, 6, 9, 11] else 31
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-{end_day}"

        url = (f"https://archive-api.open-meteo.com/v1/archive?"
               f"latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
               f"&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
               f"precipitation_sum,windspeed_10m_max,pressure_msl_mean,"
               f"shortwave_radiation_sum"
               f"&timezone=auto")

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                daily = data.get('daily', {})

                temps_mean = [x for x in daily.get('temperature_2m_mean', []) if x is not None]
                temps_max = [x for x in daily.get('temperature_2m_max', []) if x is not None]
                temps_min = [x for x in daily.get('temperature_2m_min', []) if x is not None]
                precip = [x for x in daily.get('precipitation_sum', []) if x is not None]
                wind = [x for x in daily.get('windspeed_10m_max', []) if x is not None]
                pressure = [x for x in daily.get('pressure_msl_mean', []) if x is not None]
                solar = [x for x in daily.get('shortwave_radiation_sum', []) if x is not None]

                results[key] = {
                    'wx_temp_mean': np.mean(temps_mean) if temps_mean else np.nan,
                    'wx_temp_max': np.max(temps_max) if temps_max else np.nan,
                    'wx_temp_min': np.min(temps_min) if temps_min else np.nan,
                    'wx_temp_range': np.mean([mx - mn for mx, mn in zip(temps_max, temps_min)]) if temps_max and temps_min else np.nan,
                    'wx_precip_total': np.sum(precip) if precip else np.nan,
                    'wx_precip_days': sum(1 for p in precip if p > 0.1) if precip else np.nan,
                    'wx_wind_max': np.max(wind) if wind else np.nan,
                    'wx_wind_mean': np.mean(wind) if wind else np.nan,
                    'wx_pressure_mean': np.mean(pressure) if pressure else np.nan,
                    'wx_pressure_range': (np.max(pressure) - np.min(pressure)) if pressure and len(pressure) > 1 else np.nan,
                    'wx_solar_mean': np.mean(solar) if solar else np.nan,
                }
                fetched += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error for {key}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(unique_keys)} fetched={fetched} errors={errors}")

        time.sleep(0.05)  # Rate limiting

    print(f"\nDone: fetched={fetched}, errors={errors}")

    # Save as CSV
    rows = []
    for key, vals in results.items():
        parts = key.split('_')
        row = {'lat_r': float(parts[0]), 'lon_r': float(parts[1]),
               'year': int(parts[2]), 'month': int(parts[3]),
               'weather_key': key}
        row.update(vals)
        rows.append(row)

    wx_df = pd.DataFrame(rows)
    wx_df.to_csv(OUT_PATH, index=False)
    print(f"Saved {len(wx_df)} weather records to {OUT_PATH}")

if __name__ == '__main__':
    main()
