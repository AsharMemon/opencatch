"""Build v9 dataset: v8 + real NASA POWER weather + data quality fixes.

Changes from v8:
1. Replace seasonal om_ weather with real daily NASA POWER data
2. Fix area_acres=0 → NaN
3. Remove Statewide aggregations
4. Add derived weather features (pressure change, wind chill, etc.)
"""
import pandas as pd
import numpy as np
import math

V8_PATH = "castline/validation/data/assembled/validation_dataset_v8.csv"
POWER_PATH = "castline/validation/data/raw/nasa_power_weather.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v9.csv"


def compute_derived_weather(df):
    """Compute derived features from raw NASA POWER weather."""
    # Wind chill (Fahrenheit formula converted to Celsius)
    t_f = df["np_temp_mean_c"] * 9/5 + 32
    ws_mph = df["np_wind_10m_ms"] * 2.237
    # Wind chill only valid when T < 50F and wind > 3 mph
    wc_f = 35.74 + 0.6215 * t_f - 35.75 * ws_mph**0.16 + 0.4275 * t_f * ws_mph**0.16
    valid_wc = (t_f < 50) & (ws_mph > 3)
    df["np_wind_chill_c"] = np.where(valid_wc, (wc_f - 32) * 5/9, df["np_temp_mean_c"])

    # Diurnal range
    df["np_temp_range_c"] = df["np_temp_max_c"] - df["np_temp_min_c"]

    # Humidity comfort
    df["np_humid_comfort"] = 1 - abs(df["np_humidity_pct"] - 60) / 60

    # Wind direction as sin/cos (for circular nature)
    wd_rad = np.radians(df["np_wind_dir_10m"])
    df["np_wind_dir_sin"] = np.sin(wd_rad)
    df["np_wind_dir_cos"] = np.cos(wd_rad)

    # Estimated water temp from air temp (simplified)
    # Lakes lag air temp; use 7-day-like smoothing proxy
    df["np_est_water_temp_c"] = df["np_temp_mean_c"] * 0.85 + 2.0

    # Pressure in mbar (from kPa)
    df["np_pressure_mbar"] = df["np_pressure_kpa"] * 10

    # Cloud cover fishing quality (moderate cloud = best)
    df["np_cloud_fishing"] = 1 - abs(df["np_cloud_pct"] - 50) / 50

    # Solar radiation fishing quality (less radiation = better for bass)
    solar_norm = df["np_solar_mj_m2"].clip(0, 30) / 30
    df["np_solar_fishing"] = 1 - solar_norm

    return df


def main():
    print("=" * 60)
    print("Building v9 Dataset")
    print("=" * 60)

    # Load v8
    v8 = pd.read_csv(V8_PATH, low_memory=False)
    print(f"v8: {len(v8)} rows, {v8.columns.shape[0]} columns")

    # Load NASA POWER weather
    power = pd.read_csv(POWER_PATH)
    print(f"NASA POWER: {len(power)} rows")

    # Data quality fixes
    # 1. Remove Statewide
    statewide = v8.location.str.contains("Statewide", case=False, na=False)
    print(f"Removing {statewide.sum()} Statewide rows")
    v8 = v8[~statewide].copy()

    # 2. Fix area_acres = 0
    zero_area = v8.area_acres == 0
    print(f"Fixing {zero_area.sum()} area_acres=0 → NaN")
    v8.loc[zero_area, "area_acres"] = np.nan

    # 3. Merge NASA POWER weather
    v8["date_str"] = pd.to_datetime(v8["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    v8["_lat_r"] = v8.lat.round(2)
    v8["_lon_r"] = v8.lon.round(2)

    power["_lat_r"] = power.lat.round(2)
    power["_lon_r"] = power.lon.round(2)
    power.rename(columns={"date": "date_str"}, inplace=True)

    np_cols = [c for c in power.columns if c.startswith("np_")]
    merge_cols = ["_lat_r", "_lon_r", "date_str"]

    v9 = v8.merge(power[merge_cols + np_cols], on=merge_cols, how="left")
    print(f"Merged: {v9[np_cols[0]].notna().sum()}/{len(v9)} rows have NASA weather")

    # Compute derived weather features
    v9 = compute_derived_weather(v9)

    # Drop temp columns
    v9.drop(columns=["date_str", "_lat_r", "_lon_r"], inplace=True)

    # Drop old seasonal om_ columns that NASA POWER replaces
    old_om_to_drop = [
        "om_air_temp_max", "om_air_temp_min", "om_air_temp_mean",
        "om_apparent_temp", "om_precip_mm", "om_rain_mm",
        "om_wind_max_kph", "om_wind_gust_kph", "om_wind_dir_dominant",
        "om_solar_radiation", "om_et0", "om_pressure_max", "om_pressure_min",
        "om_pressure_msl", "om_pressure_delta_24h", "om_est_water_temp",
    ]
    existing_om = [c for c in old_om_to_drop if c in v9.columns]
    print(f"Dropping {len(existing_om)} old seasonal om_ columns")
    v9.drop(columns=existing_om, inplace=True)

    # Report
    all_np = [c for c in v9.columns if c.startswith("np_")]
    print(f"\nv9 dataset: {len(v9)} rows, {v9.columns.shape[0]} columns")
    print(f"Locations: {v9.location.nunique()}")
    print(f"NASA POWER columns: {len(all_np)}")
    print("\nNASA POWER coverage:")
    for c in sorted(all_np):
        pct = v9[c].notna().mean() * 100
        print(f"  {c:30s}: {pct:.1f}%")

    # Save
    v9.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
