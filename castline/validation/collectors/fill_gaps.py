"""Fill feature gaps in the validation dataset using Open-Meteo Historical API.

Targets:
- pressure_mb: 37% → 100% (barometric pressure is HUGE for fishing)
- water_temp_c: 83% → ~98% (fill remaining with thermal lag model)
- air_temp_c: 79% → 100%
- wind_speed_kph: 78% → 100%
- humidity, dew_point, cloud_cover: new features from Open-Meteo
- precipitation: new feature (rain affects fishing significantly)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_full_weather(lat: float, lon: float, date: str, days_context: int = 7) -> dict:
    """Fetch comprehensive weather data from Open-Meteo for gap filling."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    start = (dt - timedelta(days=days_context)).strftime("%Y-%m-%d")
    end = date

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join([
            "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_mean",
            "precipitation_sum", "rain_sum",
            "windspeed_10m_max", "windgusts_10m_max", "winddirection_10m_dominant",
            "shortwave_radiation_sum",
            "et0_fao_evapotranspiration",
        ]),
        "hourly": ",".join([
            "pressure_msl",
            "relativehumidity_2m",
            "dewpoint_2m",
            "cloudcover",
            "soil_temperature_6cm",
        ]),
        "timezone": "America/Chicago",
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}

    result = {}
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})

    if not daily or not daily.get("time"):
        return {"error": "No daily data"}

    # --- Daily features (target date = last entry) ---
    def _last_valid(key):
        vals = daily.get(key, [])
        if vals and vals[-1] is not None:
            return float(vals[-1])
        return None

    def _mean_valid(key):
        vals = daily.get(key, [])
        valid = [v for v in vals if v is not None]
        return float(np.mean(valid)) if valid else None

    result["om_air_temp_mean"] = _last_valid("temperature_2m_mean")
    result["om_air_temp_max"] = _last_valid("temperature_2m_max")
    result["om_air_temp_min"] = _last_valid("temperature_2m_min")
    result["om_apparent_temp"] = _last_valid("apparent_temperature_mean")
    result["om_precip_mm"] = _last_valid("precipitation_sum")
    result["om_rain_mm"] = _last_valid("rain_sum")
    result["om_wind_max_kph"] = _last_valid("windspeed_10m_max")
    result["om_wind_gust_kph"] = _last_valid("windgusts_10m_max")
    result["om_wind_dir_dominant"] = _last_valid("winddirection_10m_dominant")
    result["om_solar_radiation"] = _last_valid("shortwave_radiation_sum")
    result["om_et0"] = _last_valid("et0_fao_evapotranspiration")

    # 7-day context
    result["om_air_temp_7d_mean"] = _mean_valid("temperature_2m_mean")
    result["om_precip_7d_total"] = None
    precip_vals = daily.get("precipitation_sum", [])
    valid_precip = [v for v in precip_vals if v is not None]
    if valid_precip:
        result["om_precip_7d_total"] = float(sum(valid_precip))

    # Temperature trend
    temps = daily.get("temperature_2m_mean", [])
    valid_temps = [t for t in temps if t is not None]
    if len(valid_temps) >= 3:
        result["om_temp_trend_7d"] = float(valid_temps[-1] - valid_temps[0])
        # Heating/cooling degree days
        result["om_hdd_7d"] = float(sum(max(0, t - 10) for t in valid_temps))
        result["om_cdd_7d"] = float(sum(max(0, 18 - t) for t in valid_temps))

    # --- Hourly features (pressure, humidity, cloud, soil temp) ---
    # Get hours for the target date (last 24 hours of the series)
    hourly_times = hourly.get("time", [])
    target_str = date
    target_indices = [i for i, t in enumerate(hourly_times) if t.startswith(target_str)]

    if target_indices:
        def _hourly_mean(key, indices):
            vals = hourly.get(key, [])
            valid = [vals[i] for i in indices if i < len(vals) and vals[i] is not None]
            return float(np.mean(valid)) if valid else None

        def _hourly_val(key, indices, pos=-1):
            vals = hourly.get(key, [])
            valid = [vals[i] for i in indices if i < len(vals) and vals[i] is not None]
            return float(valid[pos]) if valid else None

        result["om_pressure_msl"] = _hourly_mean("pressure_msl", target_indices)
        result["om_humidity"] = _hourly_mean("relativehumidity_2m", target_indices)
        result["om_dewpoint"] = _hourly_mean("dewpoint_2m", target_indices)
        result["om_cloudcover"] = _hourly_mean("cloudcover", target_indices)
        result["om_soil_temp_6cm"] = _hourly_mean("soil_temperature_6cm", target_indices)

        # Pressure delta: compare target day to day before
        prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        prev_indices = [i for i, t in enumerate(hourly_times) if t.startswith(prev_date)]
        if prev_indices:
            p_today = _hourly_mean("pressure_msl", target_indices)
            p_yesterday = _hourly_mean("pressure_msl", prev_indices)
            if p_today is not None and p_yesterday is not None:
                result["om_pressure_delta_24h"] = p_today - p_yesterday

        # Pressure at 6h intervals for fishing score
        pressure_vals = hourly.get("pressure_msl", [])
        if target_indices and len(target_indices) >= 12:
            morning = [pressure_vals[i] for i in target_indices[:6] if i < len(pressure_vals) and pressure_vals[i] is not None]
            afternoon = [pressure_vals[i] for i in target_indices[6:12] if i < len(pressure_vals) and pressure_vals[i] is not None]
            if morning and afternoon:
                result["om_pressure_delta_6h"] = float(np.mean(afternoon) - np.mean(morning))

    # --- Water temp estimate (thermal lag model) ---
    air_t = result.get("om_air_temp_mean")
    air_7d = result.get("om_air_temp_7d_mean")
    soil_t = result.get("om_soil_temp_6cm")

    if air_t is not None:
        month = dt.month
        if air_7d is not None:
            lag_weight = 0.6
            base_temp = air_t * (1 - lag_weight) + air_7d * lag_weight
            solar = result.get("om_solar_radiation", 15.0) or 15.0
            solar_boost = max(0, (solar - 10) * 0.15)

            if month in (6, 7, 8):
                result["om_est_water_temp"] = base_temp * 0.663 + 7.16 + solar_boost
            elif month in (12, 1, 2):
                result["om_est_water_temp"] = max(0.5, base_temp * 0.663 + 7.16 + 2.0)
            else:
                result["om_est_water_temp"] = base_temp * 0.663 + 7.16 + solar_boost * 0.5
        else:
            result["om_est_water_temp"] = air_t * 0.663 + 7.16

        # If we have soil temp, blend it in (better proxy for shallow water)
        if soil_t is not None and result.get("om_est_water_temp") is not None:
            result["om_est_water_temp"] = 0.7 * result["om_est_water_temp"] + 0.3 * soil_t

    return result


def fill_dataset_gaps(
    input_path: str,
    output_path: str,
    rate_limit_sec: float = 0.25,
) -> pd.DataFrame:
    """Fill gaps in dataset using Open-Meteo data."""
    df = pd.read_csv(input_path)
    print(f"Loaded {len(df)} rows from {input_path}")

    # Check which rows need filling (missing lat/lon means we can't fetch)
    needs_fill = df["lat"].notna() & df["lon"].notna() & df["date"].notna()
    print(f"  {needs_fill.sum()} rows have lat/lon/date for gap filling")

    # Track what we fill
    filled_counts = {}

    checkpoint_path = Path(output_path).with_suffix(".checkpoint.csv")
    if checkpoint_path.exists():
        checkpoint = pd.read_csv(checkpoint_path)
        checkpoint_keys = set(zip(
            checkpoint["lat"].round(2).astype(str),
            checkpoint["lon"].round(2).astype(str),
            checkpoint["date"].astype(str),
        ))
        print(f"  Loaded {len(checkpoint)} checkpoint records")
    else:
        checkpoint = pd.DataFrame()
        checkpoint_keys = set()

    new_records = []
    total = needs_fill.sum()
    fetched = 0

    for i, (idx, row) in enumerate(df[needs_fill].iterrows()):
        lat_r = round(float(row["lat"]), 2)
        lon_r = round(float(row["lon"]), 2)
        date = str(row["date"]).strip()

        key = (str(lat_r), str(lon_r), date)
        if key in checkpoint_keys:
            continue

        fetched += 1
        if fetched % 25 == 0:
            print(f"  [{fetched}/{total}] Fetching: lat={lat_r} lon={lon_r} date={date}")

        result = fetch_full_weather(lat_r, lon_r, date)
        if "error" not in result:
            result["lat"] = lat_r
            result["lon"] = lon_r
            result["date"] = date
            new_records.append(result)
            checkpoint_keys.add(key)

        time.sleep(rate_limit_sec)

        # Checkpoint every 100
        if len(new_records) > 0 and len(new_records) % 100 == 0:
            _save_checkpoint(checkpoint, new_records, checkpoint_path)
            print(f"    Checkpoint: {len(new_records)} new records")

    if new_records:
        _save_checkpoint(checkpoint, new_records, checkpoint_path)

    # Now merge the fetched data back into the dataset
    if checkpoint_path.exists():
        om = pd.read_csv(checkpoint_path)
        print(f"\n  Total Open-Meteo records: {len(om)}")

        # Merge on rounded lat/lon + date
        df["_lat_r"] = df["lat"].round(2)
        df["_lon_r"] = df["lon"].round(2)
        om["_lat_r"] = om["lat"].round(2)
        om["_lon_r"] = om["lon"].round(2)

        om_cols = [c for c in om.columns if c.startswith("om_")]
        merge_df = om[["_lat_r", "_lon_r", "date"] + om_cols].drop_duplicates(
            subset=["_lat_r", "_lon_r", "date"], keep="last"
        )

        df = df.merge(merge_df, on=["_lat_r", "_lon_r", "date"], how="left")
        df = df.drop(columns=["_lat_r", "_lon_r"], errors="ignore")

        # Fill gaps in existing columns using Open-Meteo data
        fill_map = {
            "air_temp_c": "om_air_temp_mean",
            "water_temp_c": "om_est_water_temp",
            "wind_speed_kph": "om_wind_max_kph",
            "pressure_mb": "om_pressure_msl",
        }
        for orig, om_col in fill_map.items():
            if orig in df.columns and om_col in df.columns:
                before = df[orig].notna().sum()
                df[orig] = df[orig].fillna(df[om_col])
                after = df[orig].notna().sum()
                filled_counts[orig] = after - before
                print(f"  Filled {orig}: {before} → {after} ({after-before} filled)")

    df.to_csv(output_path, index=False)
    print(f"\nSaved gap-filled dataset to {output_path}")
    print(f"  Rows: {len(df)}, Cols: {len(df.columns)}")
    return df


def _save_checkpoint(existing, new_records, path):
    new_df = pd.DataFrame(new_records)
    if len(existing) > 0:
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)


if __name__ == "__main__":
    fill_dataset_gaps(
        "castline/validation/data/assembled/validation_dataset_v5.csv",
        "castline/validation/data/assembled/validation_dataset_v6_gapfilled.csv",
    )
