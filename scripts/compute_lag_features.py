#!/usr/bin/env python3
"""
Compute 30-day lag trend features for the V16 dataset.

Since we cannot fetch historical daily weather for each event's prior 30 days,
this script uses the weather columns already in the dataset:
  - NASA POWER (np_*): available for ~5500 events (event-day values)
  - Open-Meteo (om_*): available for ~370 events
  - Existing 3d/7d lag features from NASA POWER lookups
  - Within-location rolling windows where multiple events exist

Strategy:
  1. For locations with multiple events sorted by date, compute rolling
     statistics (30-day windows when date gaps allow, or expanding windows).
  2. Use the existing lag_temp_7d_mean vs event-day temp to estimate trends.
  3. For single-event locations, use latitude-based climate normals as fallback.
  4. Derive seasonal transition indicators from available data.

Output: lag_trend_features.csv with columns:
  location, date, temp_30d_mean, temp_30d_trend, pressure_30d_mean,
  pressure_30d_trend, wind_30d_mean, temp_7d_vs_30d, pressure_7d_vs_30d,
  is_warming_trend, is_cooling_trend, is_stable_weather
"""

import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

warnings.filterwarnings("ignore")

INPUT_PATH = "/Users/Ashar/Documents/fish/castline/validation/data/assembled/validation_dataset_v16.csv"
OUTPUT_PATH = "/Users/Ashar/Documents/fish/castline/validation/data/raw/lag_trend_features.csv"

# Thresholds
WARMING_THRESHOLD = 0.1    # °C/day  (~3°C over 30 days)
COOLING_THRESHOLD = -0.1   # °C/day
STABILITY_THRESHOLD = 0.5  # combined stability index threshold


def build_unified_weather(df: pd.DataFrame) -> pd.DataFrame:
    """Create unified temp/pressure/wind columns from best available source."""
    # Temperature (°C): prefer np_temp_mean_c, fall back to om, then air_temp_c
    df["_temp_c"] = df["np_temp_mean_c"].fillna(df["om_air_temp_mean"]).fillna(df["air_temp_c"])

    # Pressure (mbar): np_pressure_kpa * 10 -> mbar, or om_pressure_msl, or pressure_mb
    np_pressure_mbar = df["np_pressure_kpa"] * 10  # kPa -> mbar
    df["_pressure_mb"] = np_pressure_mbar.fillna(
        df.get("np_pressure_mbar", pd.Series(np.nan, index=df.index))
    ).fillna(df["om_pressure_msl"]).fillna(df["pressure_mb"])

    # Wind (m/s): prefer np_wind_10m_ms, convert om from kph
    om_wind_ms = df["om_wind_max_kph"] / 3.6
    df["_wind_ms"] = df["np_wind_10m_ms"].fillna(om_wind_ms).fillna(
        df["wind_speed_kph"] / 3.6
    )

    return df


def latitude_climate_normal(lat: float, month: int) -> dict:
    """
    Rough latitude-based climate normals for the continental US.
    Returns approximate monthly mean temp (°C), pressure (mbar), wind (m/s).
    """
    # Simple latitude-month model for US bass fishing locations (25-48°N)
    # Base temp from latitude
    base_temp = 30.0 - 0.7 * (lat - 25.0)  # ~30°C at 25°N, ~14°C at 48°N

    # Seasonal amplitude increases with latitude
    amplitude = 5.0 + 0.3 * (lat - 25.0)  # 5-12°C amplitude

    # Month adjustment (peak in July=7)
    month_offset = np.cos(2 * np.pi * (month - 7) / 12)
    temp_normal = base_temp + amplitude * month_offset

    # Pressure: fairly stable, slight seasonal variation
    pressure_normal = 1013.25 + 2.0 * np.cos(2 * np.pi * (month - 1) / 12)

    # Wind: slightly higher in spring/fall
    wind_normal = 3.5 + 0.5 * np.cos(2 * np.pi * (month - 4) / 6)

    return {
        "temp_normal": temp_normal,
        "pressure_normal": pressure_normal,
        "wind_normal": wind_normal,
    }


def compute_rolling_features_for_location(group: pd.DataFrame) -> pd.DataFrame:
    """
    For a group of events at the same location (sorted by date),
    compute 30-day rolling features using available events within
    the 30-day lookback window.
    """
    group = group.sort_values("date").copy()
    n = len(group)

    results = []
    for i in range(n):
        row = group.iloc[i]
        event_date = row["date"]

        # Find events within 30 days before this event (exclusive of current)
        mask_30d = (group["date"] < event_date) & (
            group["date"] >= event_date - pd.Timedelta(days=30)
        )
        window_30d = group.loc[mask_30d]

        # Find events within 7 days before
        mask_7d = (group["date"] < event_date) & (
            group["date"] >= event_date - pd.Timedelta(days=7)
        )
        window_7d = group.loc[mask_7d]

        result = {"_idx": group.index[i]}

        if len(window_30d) >= 2:
            # Enough data for rolling stats
            result["temp_30d_mean"] = window_30d["_temp_c"].mean()
            result["pressure_30d_mean"] = window_30d["_pressure_mb"].mean()
            result["wind_30d_mean"] = window_30d["_wind_ms"].mean()

            # Linear trend over 30-day window
            days = (window_30d["date"] - window_30d["date"].min()).dt.total_seconds() / 86400
            if days.std() > 0 and window_30d["_temp_c"].notna().sum() >= 2:
                valid = window_30d["_temp_c"].notna() & days.notna()
                if valid.sum() >= 2:
                    slope, _, _, _, _ = sp_stats.linregress(
                        days[valid], window_30d.loc[valid, "_temp_c"]
                    )
                    result["temp_30d_trend"] = slope
            if days.std() > 0 and window_30d["_pressure_mb"].notna().sum() >= 2:
                valid = window_30d["_pressure_mb"].notna() & days.notna()
                if valid.sum() >= 2:
                    slope, _, _, _, _ = sp_stats.linregress(
                        days[valid], window_30d.loc[valid, "_pressure_mb"]
                    )
                    result["pressure_30d_trend"] = slope

            # 7d vs 30d departure
            if len(window_7d) >= 1:
                mean_7d_temp = window_7d["_temp_c"].mean()
                mean_30d_temp = window_30d["_temp_c"].mean()
                if pd.notna(mean_7d_temp) and pd.notna(mean_30d_temp):
                    result["temp_7d_vs_30d"] = mean_7d_temp - mean_30d_temp

                mean_7d_pres = window_7d["_pressure_mb"].mean()
                mean_30d_pres = window_30d["_pressure_mb"].mean()
                if pd.notna(mean_7d_pres) and pd.notna(mean_30d_pres):
                    result["pressure_7d_vs_30d"] = mean_7d_pres - mean_30d_pres

        results.append(result)

    return pd.DataFrame(results)


def main():
    print(f"Loading dataset from {INPUT_PATH}")
    df = pd.read_csv(INPUT_PATH)
    print(f"  Shape: {df.shape}")

    df["date"] = pd.to_datetime(df["date"])
    df = build_unified_weather(df)

    # Check unified column coverage
    for col in ["_temp_c", "_pressure_mb", "_wind_ms"]:
        n = df[col].notna().sum()
        print(f"  {col}: {n}/{len(df)} non-null ({100*n/len(df):.1f}%)")

    # ──────────────────────────────────────────────────────────────────
    # Phase 1: Rolling features for multi-event locations
    # ──────────────────────────────────────────────────────────────────
    print("\nPhase 1: Computing rolling features for multi-event locations...")

    # Initialize output columns
    out_cols = [
        "temp_30d_mean", "temp_30d_trend", "pressure_30d_mean",
        "pressure_30d_trend", "wind_30d_mean", "temp_7d_vs_30d",
        "pressure_7d_vs_30d",
    ]
    for col in out_cols:
        df[col] = np.nan

    # Process locations with multiple events
    loc_counts = df.groupby("location").size()
    multi_locs = loc_counts[loc_counts >= 3].index
    print(f"  Processing {len(multi_locs)} locations with >= 3 events...")

    filled_by_rolling = 0
    for loc in multi_locs:
        mask = df["location"] == loc
        group = df.loc[mask].copy()
        rolling_df = compute_rolling_features_for_location(group)

        for col in out_cols:
            if col in rolling_df.columns:
                valid = rolling_df[col].notna()
                if valid.any():
                    idx_map = rolling_df.loc[valid, "_idx"]
                    df.loc[idx_map, col] = rolling_df.loc[valid, col].values
                    filled_by_rolling += valid.sum()

    print(f"  Filled {filled_by_rolling} values from rolling windows")

    # ──────────────────────────────────────────────────────────────────
    # Phase 2: Use existing lag features to fill gaps
    # ──────────────────────────────────────────────────────────────────
    print("\nPhase 2: Using existing lag features for remaining gaps...")

    # For temp_30d_mean: use lag_temp_7d_mean as proxy (slightly biased but
    # captures recent conditions; extend with event-day temp for 30d estimate)
    missing_temp_30d = df["temp_30d_mean"].isna()
    if "lag_temp_7d_mean" in df.columns:
        # Weighted blend: 70% 7-day mean + 30% event-day temp as 30d approximation
        proxy_30d_temp = 0.7 * df["lag_temp_7d_mean"] + 0.3 * df["_temp_c"]
        df.loc[missing_temp_30d, "temp_30d_mean"] = proxy_30d_temp[missing_temp_30d]

    # For temp_30d_trend: use existing lag_temp_trend (which is 7d-based)
    # Scale it as approximate 30d trend
    missing_temp_trend = df["temp_30d_trend"].isna()
    if "lag_temp_trend" in df.columns:
        df.loc[missing_temp_trend, "temp_30d_trend"] = df.loc[
            missing_temp_trend, "lag_temp_trend"
        ]

    # For pressure_30d_mean: use lag_pressure_3d_mean as proxy
    # NOTE: lag_pressure_3d_mean is in kPa (same as np_pressure_kpa), convert to mbar
    missing_pres_30d = df["pressure_30d_mean"].isna()
    if "lag_pressure_3d_mean" in df.columns:
        df.loc[missing_pres_30d, "pressure_30d_mean"] = (
            df.loc[missing_pres_30d, "lag_pressure_3d_mean"] * 10.0
        )

    # For pressure_30d_trend: use lag_pressure_trend (in kPa/day -> mbar/day)
    missing_pres_trend = df["pressure_30d_trend"].isna()
    if "lag_pressure_trend" in df.columns:
        df.loc[missing_pres_trend, "pressure_30d_trend"] = (
            df.loc[missing_pres_trend, "lag_pressure_trend"] * 10.0
        )

    # For wind_30d_mean: use event-day wind as proxy
    missing_wind_30d = df["wind_30d_mean"].isna()
    df.loc[missing_wind_30d, "wind_30d_mean"] = df.loc[missing_wind_30d, "_wind_ms"]

    # For temp_7d_vs_30d: (7d mean - 30d mean)
    missing_departure = df["temp_7d_vs_30d"].isna()
    if "lag_temp_7d_mean" in df.columns:
        departure = df["lag_temp_7d_mean"] - df["temp_30d_mean"]
        df.loc[missing_departure, "temp_7d_vs_30d"] = departure[missing_departure]

    # For pressure_7d_vs_30d (both in mbar after conversion)
    missing_pres_dep = df["pressure_7d_vs_30d"].isna()
    if "lag_pressure_3d_mean" in df.columns:
        # Convert lag_pressure_3d_mean from kPa to mbar for comparison
        pres_departure = (df["lag_pressure_3d_mean"] * 10.0) - df["pressure_30d_mean"]
        df.loc[missing_pres_dep, "pressure_7d_vs_30d"] = pres_departure[missing_pres_dep]

    # ──────────────────────────────────────────────────────────────────
    # Phase 3: Latitude-based climate normal fallback
    # ──────────────────────────────────────────────────────────────────
    print("\nPhase 3: Latitude-based climate normal fallback...")

    still_missing = df["temp_30d_mean"].isna()
    filled_by_normal = 0
    if still_missing.any():
        for idx in df.index[still_missing]:
            lat = df.loc[idx, "lat"]
            month = df.loc[idx, "date"].month
            if pd.notna(lat):
                normals = latitude_climate_normal(lat, month)
                if pd.isna(df.loc[idx, "temp_30d_mean"]):
                    df.loc[idx, "temp_30d_mean"] = normals["temp_normal"]
                if pd.isna(df.loc[idx, "pressure_30d_mean"]):
                    df.loc[idx, "pressure_30d_mean"] = normals["pressure_normal"]
                if pd.isna(df.loc[idx, "wind_30d_mean"]):
                    df.loc[idx, "wind_30d_mean"] = normals["wind_normal"]
                if pd.isna(df.loc[idx, "temp_30d_trend"]):
                    # Approximate monthly trend from climate normal
                    normals_next = latitude_climate_normal(lat, month + 1 if month < 12 else 1)
                    df.loc[idx, "temp_30d_trend"] = (
                        normals_next["temp_normal"] - normals["temp_normal"]
                    ) / 30.0
                if pd.isna(df.loc[idx, "pressure_30d_trend"]):
                    df.loc[idx, "pressure_30d_trend"] = 0.0  # pressure trends ~0 climatologically
                if pd.isna(df.loc[idx, "temp_7d_vs_30d"]):
                    df.loc[idx, "temp_7d_vs_30d"] = 0.0  # no departure info
                if pd.isna(df.loc[idx, "pressure_7d_vs_30d"]):
                    df.loc[idx, "pressure_7d_vs_30d"] = 0.0
                filled_by_normal += 1

    print(f"  Filled {filled_by_normal} events from climate normals")

    # ──────────────────────────────────────────────────────────────────
    # Phase 4: Derived seasonal transition indicators
    # ──────────────────────────────────────────────────────────────────
    print("\nPhase 4: Computing seasonal transition indicators...")

    df["is_warming_trend"] = (df["temp_30d_trend"] > WARMING_THRESHOLD).astype(int)
    df["is_cooling_trend"] = (df["temp_30d_trend"] < COOLING_THRESHOLD).astype(int)

    # Weather stability: combine pressure and temperature variability
    # Use existing columns where available
    temp_var = df["temp_7d_vs_30d"].abs().fillna(0)
    pres_var = df["pressure_7d_vs_30d"].abs().fillna(0)

    # Normalize to [0, 1] range for combining
    temp_var_norm = temp_var / (temp_var.quantile(0.95) + 1e-6)
    pres_var_norm = pres_var / (pres_var.quantile(0.95) + 1e-6)
    weather_instability = 0.5 * temp_var_norm.clip(0, 1) + 0.5 * pres_var_norm.clip(0, 1)
    df["is_stable_weather"] = (weather_instability < STABILITY_THRESHOLD).astype(int)

    # ──────────────────────────────────────────────────────────────────
    # Build and save output
    # ──────────────────────────────────────────────────────────────────
    output_cols = [
        "location", "date",
        "temp_30d_mean", "temp_30d_trend",
        "pressure_30d_mean", "pressure_30d_trend",
        "wind_30d_mean",
        "temp_7d_vs_30d", "pressure_7d_vs_30d",
        "is_warming_trend", "is_cooling_trend", "is_stable_weather",
    ]
    out = df[output_cols].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")

    # Round numeric columns
    numeric_cols = output_cols[2:9]
    out[numeric_cols] = out[numeric_cols].round(4)

    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved {len(out)} rows to {OUTPUT_PATH}")

    # ──────────────────────────────────────────────────────────────────
    # Summary statistics
    # ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for col in output_cols[2:]:
        n = out[col].notna().sum()
        pct = 100 * n / len(out)
        if col in numeric_cols:
            desc = out[col].describe()
            print(
                f"  {col:25s}: {n:5d}/{len(out)} ({pct:5.1f}%) "
                f"mean={desc['mean']:8.3f}  std={desc['std']:8.3f}  "
                f"min={desc['min']:8.3f}  max={desc['max']:8.3f}"
            )
        else:
            ones = (out[col] == 1).sum()
            print(f"  {col:25s}: {n:5d}/{len(out)} ({pct:5.1f}%) positive={ones} ({100*ones/len(out):.1f}%)")

    # Check warming/cooling distribution by season
    df["_month"] = df["date"].dt.month
    print("\nWarming/Cooling by season:")
    for season, months in [("Spring", [3, 4, 5]), ("Summer", [6, 7, 8]),
                           ("Fall", [9, 10, 11]), ("Winter", [12, 1, 2])]:
        mask = df["_month"].isin(months)
        n = mask.sum()
        warming = df.loc[mask, "is_warming_trend"].sum()
        cooling = df.loc[mask, "is_cooling_trend"].sum()
        print(f"  {season:8s}: {n:4d} events, "
              f"warming={warming:4.0f} ({100*warming/max(n,1):.0f}%), "
              f"cooling={cooling:4.0f} ({100*cooling/max(n,1):.0f}%)")


if __name__ == "__main__":
    main()
