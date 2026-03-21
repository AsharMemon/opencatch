#!/usr/bin/env python3
"""
Compute bass spawn timing features for the V16 dataset.

Bass spawning is the biggest seasonal transition for fishing, dramatically
changing fish behavior. This script computes GDD-based spawn phase,
latitude-adjusted spawn timing, and seasonal activity features.

Input:  castline/validation/data/assembled/validation_dataset_v16.csv
Output: castline/validation/data/raw/spawn_timing_features.csv
"""

import numpy as np
import pandas as pd
from pathlib import Path

INPUT_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/assembled/validation_dataset_v16.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/raw/spawn_timing_features.csv"

# GDD base temp for bass (10°C / 50°F)
GDD_BASE_C = 10.0


def get_best_temp(row):
    """Pick the best available air temperature for GDD calculation."""
    for col in ["np_temp_mean_c", "om_air_temp_mean", "air_temp_c"]:
        v = row.get(col)
        if pd.notna(v):
            return v
    return np.nan


def compute_spawn_gdd_from_dataset(df):
    """
    Use the existing gdd_cumulative column when available, falling back to
    a latitude-based estimate when missing.

    The dataset already has gdd_cumulative computed from NASA POWER daily temps.
    We use that directly as spawn_gdd. For rows where it's NaN, we estimate
    from latitude, day-of-year, and available air temp.
    """
    df["spawn_gdd"] = df["gdd_cumulative"].copy()

    # Many rows have gdd_cumulative == 0 even in spring/summer due to missing
    # USGS water temp data. Treat 0-GDD events after Feb as missing so we
    # estimate from the latitude-based model instead.
    doy = df["date"].dt.dayofyear
    suspect_zero = (df["spawn_gdd"] == 0) & (doy > 60)  # after ~Mar 1
    df.loc[suspect_zero, "spawn_gdd"] = np.nan
    print(f"  Overriding {suspect_zero.sum()} suspect zero-GDD rows (spring/summer events)")

    missing = df["spawn_gdd"].isna()
    if missing.any():
        # Estimate GDD from latitude and day-of-year using a simple sinusoidal
        # temperature model: T(doy) = T_mean + amplitude * sin(2pi*(doy-80)/365)
        # where T_mean and amplitude depend on latitude
        lat = df.loc[missing, "lat"]
        doy = df.loc[missing, "date"].dt.dayofyear

        # Mean annual temp decreases ~0.7°C per degree latitude (rough US fit)
        t_mean = 22.0 - 0.5 * (lat - 30.0)
        amplitude = 8.0 + 0.15 * (lat - 30.0)  # seasonal swing bigger at higher lat

        # Integrate GDD from day 1 to doy using the sinusoidal model
        # Integral of max(T(d) - base, 0) from 1 to doy
        # Approximate by summing daily values
        gdd_est = np.zeros(missing.sum())
        for d in range(1, 366):
            daily_temp = t_mean + amplitude * np.sin(2 * np.pi * (d - 80) / 365)
            contrib = np.maximum(daily_temp - GDD_BASE_C, 0)
            mask_d = doy.values >= d
            gdd_est[mask_d] += contrib.values[mask_d]

        df.loc[missing, "spawn_gdd"] = gdd_est
        print(f"  Estimated GDD for {missing.sum()} rows with missing gdd_cumulative")

    return df


def compute_spawn_phase(df):
    """
    Assign spawn phase based on GDD thresholds, adjusted by latitude.

    Southern bass (lat ~28) spawn earlier with lower GDD thresholds.
    Northern bass (lat ~46) need more accumulated heat.
    """
    lat = df["lat"]
    gdd = df["spawn_gdd"]

    # Latitude adjustment factor: at lat=28 thresholds are ~0.7x, at lat=46 ~1.2x
    lat_factor = 0.4 + 0.02 * (lat - 25)
    lat_factor = lat_factor.clip(0.6, 1.4)

    pre_spawn_start = 100 * lat_factor
    spawn_start = 300 * lat_factor
    post_spawn_start = 600 * lat_factor
    summer_start = 900 * lat_factor

    conditions = [
        gdd < pre_spawn_start,           # winter
        gdd < spawn_start,               # pre_spawn
        gdd < post_spawn_start,          # spawn
        gdd < summer_start,              # post_spawn
        gdd >= summer_start,             # summer or fall
    ]
    choices = ["winter", "pre_spawn", "spawn", "post_spawn", "summer"]
    phase = np.select(conditions, choices, default="summer")

    # Distinguish fall from summer using day-of-year
    doy = df["date"].dt.dayofyear
    is_fall = (doy > 244) & (phase == "summer")  # After Sep 1
    phase = np.where(is_fall, "fall", phase)

    df["spawn_phase"] = phase
    return df


def compute_latitude_spawn_timing(df):
    """
    Latitude-adjusted expected spawn timing.
    Spawn month: ~Feb at lat 25, ~June at lat 48.
    Formula: spawn_month = 2 + (lat - 25) * 0.15, capped [2, 6].
    """
    lat = df["lat"]

    # Expected spawn month (fractional)
    spawn_month_frac = 2.0 + (lat - 25.0) * 0.15
    spawn_month_frac = spawn_month_frac.clip(2.0, 6.5)
    df["expected_spawn_month"] = spawn_month_frac.round(1)

    # Convert to expected spawn peak date (mid-month)
    # month 3.0 -> March 15 = day 74, month 5.0 -> May 15 = day 135
    spawn_peak_doy = ((spawn_month_frac - 1) * 30.44 + 15).round().astype(int)
    spawn_peak_doy = spawn_peak_doy.clip(32, 196)  # Feb 1 to Jul 15

    event_doy = df["date"].dt.dayofyear
    df["days_from_spawn_peak"] = (event_doy - spawn_peak_doy).astype(int)
    df["is_spawn_window"] = (df["days_from_spawn_peak"].abs() <= 30).astype(int)

    return df


def compute_seasonal_features(df):
    """
    Seasonal phase and bass activity index.

    Seasonal phases based on day-of-year adjusted by latitude:
    - winter, spring_transition, spring, summer, fall_transition, fall

    Activity index: 0-1 score of expected bass activity.
    """
    doy = df["date"].dt.dayofyear
    lat = df["lat"]

    # Latitude offset: spring comes later at higher latitudes (~2 days per degree)
    lat_offset = (lat - 35) * 2.0  # days shift relative to lat 35

    adjusted_doy = doy - lat_offset

    conditions = [
        adjusted_doy < 60,                                    # winter (before ~Mar 1)
        (adjusted_doy >= 60) & (adjusted_doy < 90),           # spring_transition
        (adjusted_doy >= 90) & (adjusted_doy < 160),          # spring (core)
        (adjusted_doy >= 160) & (adjusted_doy < 250),         # summer
        (adjusted_doy >= 250) & (adjusted_doy < 290),         # fall_transition
        adjusted_doy >= 290,                                   # fall/winter
    ]
    choices = [
        "winter", "spring_transition", "spring",
        "summer", "fall_transition", "fall"
    ]
    df["seasonal_phase"] = np.select(conditions, choices, default="winter")

    # Activity index based on spawn phase (the most biologically meaningful signal)
    phase_activity = {
        "winter": 0.2,
        "pre_spawn": 0.8,
        "spawn": 0.9,
        "post_spawn": 0.6,
        "summer": 0.5,
        "fall": 0.65,
    }
    df["seasonal_activity_index"] = df["spawn_phase"].map(phase_activity).fillna(0.3)

    # Smooth with a sinusoidal overlay so the transition isn't purely step-wise
    # Add a continuous component based on day-of-year
    sin_component = 0.5 + 0.5 * np.sin(2 * np.pi * (adjusted_doy - 80) / 365)
    # Blend: 70% phase-based, 30% continuous
    df["seasonal_activity_index"] = (
        0.7 * df["seasonal_activity_index"] + 0.3 * sin_component
    ).round(3)

    return df


def main():
    print(f"Loading V16 dataset from {INPUT_PATH}")
    cols_needed = [
        "date", "location", "lat", "lon",
        "gdd_cumulative", "np_temp_mean_c", "om_air_temp_mean", "air_temp_c",
    ]
    # Read only needed columns (plus event_id for joining)
    all_cols = pd.read_csv(INPUT_PATH, nrows=0).columns.tolist()
    use_cols = ["event_id"] + [c for c in cols_needed if c in all_cols]

    df = pd.read_csv(INPUT_PATH, usecols=use_cols, parse_dates=["date"])
    print(f"  Loaded {len(df):,} rows, {len(use_cols)} columns")
    print(f"  Date range: {df.date.min().date()} to {df.date.max().date()}")
    print(f"  Latitude range: {df.lat.min():.1f} to {df.lat.max():.1f}")
    print(f"  gdd_cumulative non-null: {df.gdd_cumulative.notna().sum():,} / {len(df):,}")

    # Step 1: Spawn GDD
    print("\n1. Computing spawn GDD...")
    df = compute_spawn_gdd_from_dataset(df)
    print(f"  spawn_gdd range: {df.spawn_gdd.min():.0f} - {df.spawn_gdd.max():.0f}")
    print(f"  spawn_gdd non-null: {df.spawn_gdd.notna().sum():,}")

    # Step 2: Spawn phase
    print("\n2. Computing spawn phase...")
    df = compute_spawn_phase(df)
    print("  Phase distribution:")
    print(df["spawn_phase"].value_counts().to_string(header=False))

    # Step 3: Latitude-adjusted spawn timing
    print("\n3. Computing latitude-adjusted spawn timing...")
    df = compute_latitude_spawn_timing(df)
    print(f"  expected_spawn_month range: {df.expected_spawn_month.min()} - {df.expected_spawn_month.max()}")
    print(f"  is_spawn_window == 1: {df.is_spawn_window.sum():,} / {len(df):,} ({100*df.is_spawn_window.mean():.1f}%)")

    # Step 4: Seasonal features
    print("\n4. Computing seasonal features...")
    df = compute_seasonal_features(df)
    print("  Seasonal phase distribution:")
    print(df["seasonal_phase"].value_counts().to_string(header=False))
    print(f"  seasonal_activity_index: mean={df.seasonal_activity_index.mean():.3f}, "
          f"std={df.seasonal_activity_index.std():.3f}")

    # Output
    out_cols = [
        "location", "date", "lat",
        "spawn_gdd", "spawn_phase",
        "expected_spawn_month", "days_from_spawn_peak", "is_spawn_window",
        "seasonal_phase", "seasonal_activity_index",
    ]
    out = df[out_cols].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out):,} rows to {OUTPUT_PATH}")

    # Summary stats
    print("\n=== SUMMARY ===")
    print(f"Total events: {len(out):,}")
    print(f"Unique locations: {out.location.nunique()}")
    print(f"\nSpawn phase counts:")
    for phase, count in out.spawn_phase.value_counts().items():
        print(f"  {phase:12s}: {count:5d} ({100*count/len(out):5.1f}%)")
    print(f"\nSeasonal activity index by spawn phase:")
    for phase in ["pre_spawn", "spawn", "post_spawn", "summer", "fall", "winter"]:
        subset = out[out.spawn_phase == phase]
        if len(subset):
            print(f"  {phase:12s}: {subset.seasonal_activity_index.mean():.3f} (n={len(subset)})")


if __name__ == "__main__":
    main()
