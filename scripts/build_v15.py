"""Build v15 dataset: v13 + novel research-backed features.

New features:
1. Growing Degree Days (GDD) — cumulative thermal units above 10°C since Jan 1
2. Photoperiod (day length) — astronomical calculation from lat + date
3. Pressure change features — from NASA POWER + Open-Meteo pressure data
4. NASA POWER 7-day lag features — from ongoing fetch (partial coverage OK)
5. Barometric pressure classification — pre-frontal, post-frontal, stable
6. Spawn timing index — GDD-based spawn phase (more precise than temp alone)
7. Thermal regime features — heating/cooling rate, seasonal position
"""
import pandas as pd
import numpy as np
from pathlib import Path
import math
import warnings

warnings.filterwarnings("ignore")

def p(msg=""):
    print(msg, flush=True)

BASE_DIR = Path("/Users/Ashar/Documents/fish")
V13_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v13.csv"
NASA_7DAY_PATH = BASE_DIR / "castline/validation/data/raw/nasa_power_7day.csv"
NASA_POWER_PATH = BASE_DIR / "castline/validation/data/raw/nasa_power_weather.csv"
OPENMETEO_PATH = BASE_DIR / "castline/validation/data/raw/openmeteo_weather_cache.csv"
OUTPUT_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"

# Bass biology constants
GDD_BASE_TEMP_C = 10.0    # Base temperature for bass (warmwater guild)
SPAWN_GDD_START = 200     # GDD when bass begin pre-spawn staging
SPAWN_GDD_PEAK = 400      # GDD at peak spawn
SPAWN_GDD_END = 700       # GDD when post-spawn recovery ends
BASS_OPTIMAL_TEMP_C = 21  # ~70°F optimal bass feeding temp


# ═══════════════════════════════════════════════════════════
# 1. PHOTOPERIOD (Day Length)
# ═══════════════════════════════════════════════════════════
def compute_photoperiod(lat_deg, day_of_year):
    """Compute day length in hours using astronomical formula.

    Uses the CBM model (Civil twilight-based) which is standard
    for biological applications.
    """
    lat_rad = np.radians(lat_deg)

    # Solar declination (Spencer 1971)
    gamma = 2 * np.pi * (day_of_year - 1) / 365.0
    decl = (0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
            - 0.006758 * np.cos(2*gamma) + 0.000907 * np.sin(2*gamma)
            - 0.002697 * np.cos(3*gamma) + 0.00148 * np.sin(3*gamma))

    # Hour angle at sunrise/sunset
    cos_ha = -np.tan(lat_rad) * np.tan(decl)
    # Clamp for polar regions
    cos_ha = np.clip(cos_ha, -1.0, 1.0)
    ha = np.arccos(cos_ha)

    # Day length in hours
    day_length = (2.0 * ha * 12.0) / np.pi
    return day_length


def compute_photoperiod_features(df):
    """Add photoperiod-related features."""
    p("Computing photoperiod features...")

    dates = pd.to_datetime(df["date"])
    doy = dates.dt.dayofyear.values.astype(float)
    lat = df["lat"].values

    # Basic day length
    df["photoperiod_hrs"] = compute_photoperiod(lat, doy)

    # Rate of change of day length (proxy for seasonal acceleration)
    # Compute day length for day+1 and day-1
    dl_plus = compute_photoperiod(lat, np.clip(doy + 1, 1, 365))
    dl_minus = compute_photoperiod(lat, np.clip(doy - 1, 1, 365))
    df["photoperiod_change_min"] = (dl_plus - dl_minus) * 30  # minutes change per day

    # Photoperiod relative to spawn trigger (13.5 hours)
    df["photoperiod_spawn_proximity"] = np.abs(df["photoperiod_hrs"] - 13.5)

    # Photoperiod × temperature interaction
    if "np_temp_mean_c" in df.columns:
        df["photoperiod_x_temp"] = df["photoperiod_hrs"] * df["np_temp_mean_c"]

    coverage = df["photoperiod_hrs"].notna().mean() * 100
    p(f"  Photoperiod coverage: {coverage:.1f}%")
    p(f"  Day length range: {df['photoperiod_hrs'].min():.1f} - {df['photoperiod_hrs'].max():.1f} hrs")

    return df


# ═══════════════════════════════════════════════════════════
# 2. GROWING DEGREE DAYS (GDD)
# ═══════════════════════════════════════════════════════════
def estimate_daily_temp(lat, day_of_year):
    """Estimate daily mean temperature using sinusoidal climate model.

    Calibrated for continental US (25-48°N).
    T(day) = T_annual_mean + amplitude * sin(2π * (day - phase) / 365)

    Parameters derived from PRISM climate normals:
    - Annual mean temp decreases with latitude: ~28 - 0.55*lat °C
    - Seasonal amplitude increases with latitude: 8 + 0.18*lat °C
    - Phase: peak around day 200 (mid-July), so sin peaks at day 200
    """
    # Annual mean temperature (latitude-dependent)
    t_mean = 28.0 - 0.55 * lat

    # Seasonal amplitude
    amplitude = 8.0 + 0.18 * lat

    # Phase: sin peaks at day 200 (July 19), so phase offset = 200 - 365/4 = 109
    phase = 109.0

    t_daily = t_mean + amplitude * np.sin(2 * np.pi * (day_of_year - phase) / 365.0)
    return t_daily


def compute_gdd_features(df):
    """Compute Growing Degree Days and related thermal features.

    GDD = cumulative sum of max(0, T_daily - T_base) from Jan 1 to event date.
    Uses sinusoidal climate model calibrated with actual NASA POWER temps.
    """
    p("Computing Growing Degree Day features...")

    dates = pd.to_datetime(df["date"])
    doy = dates.dt.dayofyear.values.astype(float)
    lat = df["lat"].values

    # Compute GDD by integrating the sinusoidal temperature model
    gdd_values = np.zeros(len(df))

    for i in range(len(df)):
        days = np.arange(1, int(doy[i]) + 1)
        daily_temps = estimate_daily_temp(lat[i], days)
        dd = np.maximum(0, daily_temps - GDD_BASE_TEMP_C)
        gdd_values[i] = np.sum(dd)

    df["gdd_cumulative"] = gdd_values

    # Calibration: adjust GDD using actual event-day temp vs model prediction
    if "np_temp_mean_c" in df.columns:
        model_temp = estimate_daily_temp(lat, doy)
        temp_ratio = df["np_temp_mean_c"].values / np.clip(model_temp, 1, None)
        # Calibrated GDD = model GDD * ratio (warmer year = more GDD)
        df["gdd_calibrated"] = np.where(
            df["np_temp_mean_c"].notna(),
            gdd_values * np.clip(temp_ratio, 0.5, 2.0),
            gdd_values
        )
    else:
        df["gdd_calibrated"] = gdd_values

    # Log GDD (more linear relationship)
    df["gdd_log"] = np.log1p(df["gdd_calibrated"])

    # Spawn phase from GDD (more biologically accurate than temp alone)
    df["gdd_spawn_phase"] = np.where(
        df["gdd_calibrated"] < SPAWN_GDD_START, 0,  # Pre-spawn cold
        np.where(df["gdd_calibrated"] < SPAWN_GDD_PEAK, 1,  # Pre-spawn staging
        np.where(df["gdd_calibrated"] < SPAWN_GDD_END, 2,  # Active spawn
        3  # Post-spawn / summer
    )))

    # Distance from optimal GDD for bass feeding (around 800-1200 GDD)
    df["gdd_feeding_optimality"] = 1.0 / (1.0 + ((df["gdd_calibrated"] - 1000) / 500) ** 2)

    # GDD rate (how fast are degree days accumulating right now)
    event_day_dd = np.maximum(0, estimate_daily_temp(lat, doy) - GDD_BASE_TEMP_C)
    df["gdd_daily_rate"] = event_day_dd

    # Seasonal position (0-1, where in the annual thermal cycle)
    max_gdd_for_lat = np.zeros(len(df))
    for i in range(len(df)):
        days_full = np.arange(1, 366)
        full_dd = np.maximum(0, estimate_daily_temp(lat[i], days_full) - GDD_BASE_TEMP_C)
        max_gdd_for_lat[i] = np.sum(full_dd)
    df["seasonal_position"] = df["gdd_calibrated"] / np.clip(max_gdd_for_lat, 1, None)

    p(f"  GDD range: {df['gdd_calibrated'].min():.0f} - {df['gdd_calibrated'].max():.0f}")
    p(f"  Spawn phase distribution: {df['gdd_spawn_phase'].value_counts().sort_index().to_dict()}")

    return df


# ═══════════════════════════════════════════════════════════
# 3. PRESSURE CHANGE FEATURES
# ═══════════════════════════════════════════════════════════
def compute_pressure_features(df):
    """Enhanced barometric pressure features.

    Uses existing pressure data to compute:
    - Pressure change rate
    - Pre/post frontal classification
    - Pressure stability
    """
    p("Computing pressure change features...")

    # We have np_pressure_kpa and om_pressure_msl
    # Use whichever is available
    pressure = df.get("np_pressure_mbar", pd.Series(dtype=float))
    if pressure.isna().all() and "np_pressure_kpa" in df.columns:
        pressure = df["np_pressure_kpa"] * 10  # kPa to mbar

    # If we have Open-Meteo pressure, use max/min for intra-day range
    has_om_pressure = "om_pressure_max" in df.columns and "om_pressure_min" in df.columns

    if has_om_pressure:
        om_range = df["om_pressure_max"] - df["om_pressure_min"]
        df["pressure_intraday_range"] = om_range

        # Classify pressure pattern
        # Large range = frontal passage
        df["pressure_frontal_indicator"] = np.where(
            om_range > 8, 1,   # Strong frontal passage (>8 mbar swing)
            np.where(om_range > 4, 0.5, 0)  # Moderate front
        )

        # Use MSL pressure for absolute classification
        if "om_pressure_msl" in df.columns:
            msl = df["om_pressure_msl"]
            # High pressure (>1020) = stable, good fishing
            # Low pressure (<1005) = storm, poor fishing
            # Falling (range>5, max>min by a lot) = pre-frontal, BEST fishing
            df["pressure_regime"] = np.where(
                msl > 1025, 3,  # Strong high
                np.where(msl > 1015, 2,  # Normal high
                np.where(msl > 1005, 1,  # Normal low
                0  # Deep low
            )))

            # Pressure-based fishing quality
            # Best: falling pressure (pre-frontal), Worst: just after front passes
            df["pressure_fishing_idx"] = np.where(
                (om_range > 5) & (msl > 1010), 1.0,  # Pre-frontal (best)
                np.where(msl > 1020, 0.7,  # Stable high (good)
                np.where(msl < 1005, 0.2,  # Deep low (poor)
                0.5  # Neutral
            )))

    # From NASA POWER: absolute pressure
    if "np_pressure_kpa" in df.columns:
        kpa = df["np_pressure_kpa"]
        # Convert to approximate altitude-adjusted (station pressure)
        df["pressure_anomaly"] = kpa - kpa.median()

        # Pressure × temperature interaction (frontal indicator)
        if "np_temp_mean_c" in df.columns:
            # Cold + high pressure = post-frontal (bad)
            # Warm + falling pressure = pre-frontal (great)
            df["pressure_temp_interaction"] = (
                (kpa - kpa.median()) * df["np_temp_mean_c"]
            )

    coverage = df.get("pressure_intraday_range", pd.Series()).notna().mean() * 100
    p(f"  Pressure feature coverage: {coverage:.1f}%")

    return df


# ═══════════════════════════════════════════════════════════
# 4. NASA POWER 7-DAY LAG FEATURES
# ═══════════════════════════════════════════════════════════
def compute_lag_features(df):
    """Integrate NASA POWER 7-day lag features from ongoing fetch."""
    p("Integrating NASA POWER 7-day lag features...")

    if not NASA_7DAY_PATH.exists():
        p("  ⚠ NASA 7-day data not found, skipping")
        return df

    lag_raw = pd.read_csv(NASA_7DAY_PATH, low_memory=False)
    p(f"  Raw 7-day data: {len(lag_raw)} rows")

    # Group by event
    lag_raw["event_date"] = pd.to_datetime(lag_raw["event_date"])
    lag_raw["date"] = pd.to_datetime(lag_raw["date"])
    lag_raw["days_before"] = (lag_raw["event_date"] - lag_raw["date"]).dt.days

    lag_features = []

    for (elat, elon, edate), group in lag_raw.groupby(["event_lat", "event_lon", "event_date"]):
        g = group.sort_values("days_before")

        if len(g) < 3:
            continue

        row = {"_lag_lat": elat, "_lag_lon": elon, "_lag_date": edate}

        # Temperature features
        if "np_temp_mean_c" in g.columns:
            temps = g["np_temp_mean_c"].dropna()
            if len(temps) >= 3:
                row["lag_temp_3d_mean"] = temps.head(3).mean()
                row["lag_temp_7d_mean"] = temps.mean()
                row["lag_temp_range"] = temps.max() - temps.min()
                # Temperature trend (positive = warming)
                if len(temps) >= 5:
                    x = np.arange(len(temps))
                    slope = np.polyfit(x, temps.values, 1)[0]
                    row["lag_temp_trend"] = slope
                row["lag_warming"] = (temps.diff().dropna() > 0).sum() / max(len(temps)-1, 1)
                row["lag_cooling"] = (temps.diff().dropna() < 0).sum() / max(len(temps)-1, 1)

        # Diurnal range
        if "np_temp_max_c" in g.columns and "np_temp_min_c" in g.columns:
            diurnal = g["np_temp_max_c"] - g["np_temp_min_c"]
            diurnal = diurnal.dropna()
            if len(diurnal) >= 3:
                row["lag_diurnal_mean"] = diurnal.mean()
                row["lag_diurnal_std"] = diurnal.std()

        # Precipitation
        if "np_precip_mm" in g.columns:
            precip = g["np_precip_mm"].dropna()
            if len(precip) >= 3:
                row["lag_precip_3d_sum"] = precip.head(3).sum()
                row["lag_precip_7d_sum"] = precip.sum()
                row["lag_precip_days"] = (precip > 1.0).sum()

        # Wind
        if "np_wind_10m_ms" in g.columns:
            wind = g["np_wind_10m_ms"].dropna()
            if len(wind) >= 3:
                row["lag_wind_3d_mean"] = wind.head(3).mean()
                row["lag_wind_7d_mean"] = wind.mean()
                row["lag_wind_variability"] = wind.std()

        # Pressure trend (KEY for fishing prediction)
        if "np_pressure_kpa" in g.columns:
            pres = g["np_pressure_kpa"].dropna()
            if len(pres) >= 3:
                row["lag_pressure_3d_mean"] = pres.head(3).mean()
                row["lag_pressure_trend"] = np.polyfit(np.arange(len(pres)), pres.values, 1)[0] if len(pres) >= 4 else np.nan
                row["lag_pressure_range"] = pres.max() - pres.min()
                row["lag_pressure_std"] = pres.std()

        # Solar/cloud
        if "np_cloud_pct" in g.columns:
            cloud = g["np_cloud_pct"].dropna()
            if len(cloud) >= 3:
                row["lag_cloud_3d_mean"] = cloud.head(3).mean()
                row["lag_cloud_trend"] = np.polyfit(np.arange(len(cloud)), cloud.values, 1)[0] if len(cloud) >= 4 else np.nan

        # Humidity
        if "np_humidity_pct" in g.columns:
            humid = g["np_humidity_pct"].dropna()
            if len(humid) >= 3:
                row["lag_humidity_3d_mean"] = humid.head(3).mean()
                row["lag_humidity_trend"] = np.polyfit(np.arange(len(humid)), humid.values, 1)[0] if len(humid) >= 4 else np.nan

        lag_features.append(row)

    if not lag_features:
        p("  ⚠ No lag features computed")
        return df

    lag_df = pd.DataFrame(lag_features)
    p(f"  Lag features computed for {len(lag_df)} events")

    # Match to main dataset
    df["_date_dt"] = pd.to_datetime(df["date"])
    df["_lat_r"] = df["lat"].round(2)
    df["_lon_r"] = df["lon"].round(2)
    lag_df["_lat_r"] = lag_df["_lag_lat"].round(2)
    lag_df["_lon_r"] = lag_df["_lag_lon"].round(2)
    lag_df["_date_dt"] = lag_df["_lag_date"]

    lag_cols = [c for c in lag_df.columns if c.startswith("lag_")]
    merge_df = lag_df[["_lat_r", "_lon_r", "_date_dt"] + lag_cols]
    # Deduplicate on merge keys (take first match)
    merge_df = merge_df.drop_duplicates(subset=["_lat_r", "_lon_r", "_date_dt"], keep="first")

    before = len(df)
    df = df.merge(merge_df, on=["_lat_r", "_lon_r", "_date_dt"], how="left")
    df.drop(columns=["_date_dt", "_lat_r", "_lon_r"], inplace=True)
    assert len(df) == before, f"Merge changed row count: {before} → {len(df)}"

    for c in lag_cols:
        cov = df[c].notna().mean() * 100
        if cov > 0:
            corr = df[c].corr(df["median_weight_lb"])
            p(f"    {c:30s} coverage={cov:5.1f}%  r={corr:+.3f}")

    return df


# ═══════════════════════════════════════════════════════════
# 5. THERMAL REGIME FEATURES
# ═══════════════════════════════════════════════════════════
def compute_thermal_features(df):
    """Advanced thermal features combining temp, GDD, and photoperiod."""
    p("Computing thermal regime features...")

    # Thermal comfort index: how close to bass optimal (21°C)
    if "np_temp_mean_c" in df.columns:
        temp = df["np_temp_mean_c"]
        df["thermal_comfort"] = 1.0 / (1.0 + ((temp - BASS_OPTIMAL_TEMP_C) / 8.0) ** 2)

        # Thermal shock risk (very cold or very hot)
        df["thermal_stress"] = np.where(
            temp < 5, (5 - temp) / 10,  # Cold stress
            np.where(temp > 30, (temp - 30) / 10, 0)  # Heat stress
        )

    # Combine GDD + photoperiod for spawn timing
    if "gdd_calibrated" in df.columns and "photoperiod_hrs" in df.columns:
        # Spawn probability peaks when GDD is 300-500 AND day length > 13 hrs
        gdd_spawn = np.exp(-((df["gdd_calibrated"] - 400) / 150) ** 2)
        photo_spawn = np.where(df["photoperiod_hrs"] > 12.5, 1, 0)
        df["spawn_probability"] = gdd_spawn * photo_spawn

        # Post-spawn recovery (GDD 600-900, fish are lethargic)
        df["post_spawn_lethargy"] = np.exp(-((df["gdd_calibrated"] - 750) / 200) ** 2)

        # Fall feeding frenzy (GDD > 2000, photoperiod decreasing)
        if "photoperiod_change_min" in df.columns:
            df["fall_feed_frenzy"] = np.where(
                (df["gdd_calibrated"] > 1800) & (df["photoperiod_change_min"] < -1),
                np.abs(df["photoperiod_change_min"]) / 3.0,
                0
            )

    # Stratification likelihood (warm + deep + calm = stratified)
    if all(c in df.columns for c in ["np_temp_mean_c", "max_depth_ft", "np_wind_10m_ms"]):
        temp_factor = np.clip((df["np_temp_mean_c"] - 15) / 15, 0, 1)
        depth_factor = np.clip(df["max_depth_ft"] / 50, 0, 1)
        wind_factor = np.clip(1 - df["np_wind_10m_ms"] / 8, 0, 1)
        df["stratification_likelihood"] = temp_factor * depth_factor * wind_factor

    return df


# ═══════════════════════════════════════════════════════════
# MAIN BUILD
# ═══════════════════════════════════════════════════════════
p("=" * 60)
p("Building v15 dataset")
p("=" * 60)

df = pd.read_csv(V13_PATH, low_memory=False)
df = df[df["median_weight_lb"].notna()].copy()
p(f"Base v13: {len(df)} rows, {df.shape[1]} columns, {df.location.nunique()} locations")

# Merge Open-Meteo data if not already present
if "om_pressure_msl" not in df.columns and OPENMETEO_PATH.exists():
    p("\nMerging Open-Meteo pressure data...")
    om = pd.read_csv(OPENMETEO_PATH, low_memory=False)
    om["date"] = pd.to_datetime(om["date"]).dt.strftime("%Y-%m-%d")
    om["lat"] = om["lat"].round(2)
    om["lon"] = om["lon"].round(2)

    om_cols = [c for c in om.columns if c.startswith("om_")]
    # Only merge columns not already in df
    new_om_cols = [c for c in om_cols if c not in df.columns]
    if new_om_cols:
        df["_lat_r"] = df["lat"].round(2)
        df["_lon_r"] = df["lon"].round(2)
        df["_date_s"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        merge_om = om[["lat", "lon", "date"] + new_om_cols].rename(
            columns={"lat": "_lat_r", "lon": "_lon_r", "date": "_date_s"})

        before = len(df)
        df = df.merge(merge_om, on=["_lat_r", "_lon_r", "_date_s"], how="left")
        df.drop(columns=["_lat_r", "_lon_r", "_date_s"], inplace=True)
        assert len(df) == before

        for c in new_om_cols:
            cov = df[c].notna().mean() * 100
            p(f"  {c}: {cov:.1f}% coverage")

# 1. Photoperiod
df = compute_photoperiod_features(df)

# 2. Growing Degree Days
df = compute_gdd_features(df)

# 3. Pressure features
df = compute_pressure_features(df)

# 4. Thermal regime features
df = compute_thermal_features(df)

# 5. NASA 7-day lag features
df = compute_lag_features(df)

# ═══════════════════════════════════════════════════════════
# FEATURE CORRELATIONS WITH TARGET
# ═══════════════════════════════════════════════════════════
p("\n" + "=" * 60)
p("New feature correlations with median_weight_lb:")
p("=" * 60)

new_features = [
    # Photoperiod
    "photoperiod_hrs", "photoperiod_change_min", "photoperiod_spawn_proximity",
    "photoperiod_x_temp",
    # GDD
    "gdd_cumulative", "gdd_calibrated", "gdd_log", "gdd_spawn_phase",
    "gdd_feeding_optimality", "gdd_daily_rate", "seasonal_position",
    # Pressure
    "pressure_intraday_range", "pressure_frontal_indicator", "pressure_regime",
    "pressure_fishing_idx", "pressure_anomaly", "pressure_temp_interaction",
    # Thermal
    "thermal_comfort", "thermal_stress", "spawn_probability",
    "post_spawn_lethargy", "fall_feed_frenzy", "stratification_likelihood",
    # Lag features
    "lag_temp_3d_mean", "lag_temp_7d_mean", "lag_temp_trend", "lag_temp_range",
    "lag_warming", "lag_cooling", "lag_diurnal_mean", "lag_diurnal_std",
    "lag_precip_3d_sum", "lag_precip_7d_sum", "lag_precip_days",
    "lag_wind_3d_mean", "lag_wind_7d_mean", "lag_wind_variability",
    "lag_pressure_3d_mean", "lag_pressure_trend", "lag_pressure_range",
    "lag_pressure_std", "lag_cloud_3d_mean", "lag_cloud_trend",
    "lag_humidity_3d_mean", "lag_humidity_trend",
]

results = []
for f in new_features:
    if f in df.columns and df[f].notna().any():
        corr = df[f].corr(df["median_weight_lb"])
        cov = df[f].notna().mean() * 100
        results.append((f, corr, cov))

results.sort(key=lambda x: abs(x[1]), reverse=True)
for f, corr, cov in results:
    p(f"  {f:35s} r={corr:+.4f}  cov={cov:.0f}%")

# Save
df.to_csv(OUTPUT_PATH, index=False)
p(f"\n✓ v15 saved: {len(df)} rows, {df.shape[1]} columns")
p(f"  Output: {OUTPUT_PATH}")
