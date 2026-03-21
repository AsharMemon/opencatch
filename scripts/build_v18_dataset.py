#!/usr/bin/env python3
"""
Build V18 assembled dataset from V17 base.

Adds:
  - Hurdle / two-part targets (has_catch, positive_cpue)
  - Catchability covariates (day_of_week, is_weekend, tournament_tier, etc.)
  - Temporal walk-forward features (year_trend, recent_location_activity, rolling_regional_cpue)
  - Interaction features (pressure x temp, wind x cloud, spawn x temp)
  - Stream-network flags (is_river, stream_order_estimate)

Usage:
    python scripts/build_v18_dataset.py
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
VALIDATION_DIR = BASE_DIR / "castline" / "validation"
ASSEMBLED_DIR = VALIDATION_DIR / "data" / "assembled"

V17_PATH = ASSEMBLED_DIR / "validation_dataset_v17.csv"
OUTPUT_PATH = ASSEMBLED_DIR / "validation_dataset_v18.csv"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# US federal holidays (month, day) — fixed-date ones; we also add approximate
# floating holidays for the most common ones.
US_HOLIDAYS_FIXED = [
    (1, 1),    # New Year's Day
    (7, 4),    # Independence Day
    (11, 11),  # Veterans Day
    (12, 25),  # Christmas
]

# Trail -> tier mapping (ordinal)
TRAIL_TIER = {
    "elite": 3,
    "classic": 3,
    "open": 2,
    "nation": 2,
    "college": 1,
    "high_school": 1,
    "junior": 1,
    "tourneyx_club": 0,
    "other": 0,
}

# Waterbody keywords for classification
WATERBODY_KEYWORDS = {
    "river": ["river", "creek", "fork", "branch", "run"],
    "reservoir": ["reservoir", "dam", "pool"],
    "bay": ["bay", "sound", "harbor", "harbour", "inlet", "estuary"],
    "delta": ["delta"],
    "chain": ["chain"],
}

# Two-letter US state abbreviations
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MA", "MD",
    "ME", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

# Full state name -> abbreviation (for locations like "Alder Lake, Wisconsin")
STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# Latitude band grouping by state (approximate)
SOUTH_STATES = {"FL", "TX", "LA", "MS", "AL", "GA", "SC", "AR", "AZ", "NM"}
NORTH_STATES = {
    "MN", "WI", "MI", "ME", "VT", "NH", "NY", "MT", "ND", "SD",
    "WA", "OR", "ID", "WY", "CT", "MA", "RI",
}
# Everything else is CENTRAL

# Major rivers with rough Strahler order estimates
MAJOR_RIVERS = {
    "mississippi": 10, "missouri": 9, "ohio": 9, "tennessee": 8,
    "arkansas": 8, "red": 7, "columbia": 9, "colorado": 8,
    "rio grande": 8, "snake": 7, "cumberland": 7, "savannah": 6,
    "potomac": 6, "james": 6, "susquehanna": 7, "delaware": 6,
    "st. johns": 6, "st johns": 6, "alabama": 7, "apalachicola": 6,
    "chattahoochee": 6, "tombigbee": 7, "ouachita": 6, "neches": 5,
    "trinity": 6, "brazos": 7, "sabine": 6, "wabash": 6,
    "illinois": 6, "st. lawrence": 10, "st lawrence": 10,
    "guadalupe": 5, "coosa": 6, "black warrior": 5, "flint": 5,
    "santee": 6, "altamaha": 5, "rappahannock": 5,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def extract_state(location: str) -> str:
    """Extract US state abbreviation from location string. Returns NaN if not found."""
    if not isinstance(location, str):
        return np.nan
    parts = [p.strip() for p in location.split(",")]
    # Check last part for 2-letter abbr
    for part in reversed(parts):
        token = part.strip().upper()
        if token in US_STATES:
            return token
        # Check full state name
        lower = part.strip().lower()
        if lower in STATE_NAME_TO_ABBR:
            return STATE_NAME_TO_ABBR[lower]
    return np.nan


def classify_waterbody(location: str, wtype_river: float, wtype_reservoir: float,
                       wtype_natural_lake: float) -> str:
    """Classify waterbody type from location name and existing wtype flags."""
    # Use existing flags first
    if wtype_river == 1.0:
        return "river"
    if wtype_reservoir == 1.0:
        return "reservoir"
    if wtype_natural_lake == 1.0:
        return "lake"

    if not isinstance(location, str):
        return "unknown"

    loc_lower = location.lower()
    for wtype, keywords in WATERBODY_KEYWORDS.items():
        for kw in keywords:
            if kw in loc_lower:
                return wtype
    # Default: lake (most common for bass tournaments)
    return "lake"


def estimate_stream_order(location: str) -> float:
    """Estimate Strahler stream order from location name. NaN if not a known river."""
    if not isinstance(location, str):
        return np.nan
    loc_lower = location.lower()
    for river_name, order in MAJOR_RIVERS.items():
        if river_name in loc_lower:
            return float(order)
    return np.nan


def is_near_holiday(dt, window_days=3):
    """Check if date is within `window_days` of a US federal holiday."""
    if pd.isna(dt):
        return np.nan
    year = dt.year
    # Fixed holidays
    for m, d in US_HOLIDAYS_FIXED:
        try:
            hol = pd.Timestamp(year=year, month=m, day=d)
            if abs((dt - hol).days) <= window_days:
                return 1
        except ValueError:
            pass
    # Approximate floating holidays
    # MLK Day: 3rd Monday of Jan (around Jan 15-21)
    # Presidents Day: 3rd Monday of Feb (around Feb 15-21)
    # Memorial Day: last Monday of May (around May 25-31)
    # Labor Day: 1st Monday of Sep (around Sep 1-7)
    # Thanksgiving: 4th Thursday of Nov (around Nov 22-28)
    floating_ranges = [
        (1, 15, 21),   # MLK
        (2, 15, 21),   # Presidents
        (5, 25, 31),   # Memorial
        (9, 1, 7),     # Labor
        (11, 22, 28),  # Thanksgiving
    ]
    for m, d_start, d_end in floating_ranges:
        for d in range(d_start, d_end + 1):
            try:
                hol = pd.Timestamp(year=year, month=m, day=d)
                if abs((dt - hol).days) <= window_days:
                    return 1
            except ValueError:
                pass
    return 0


def latitude_band(state: str) -> float:
    """Return latitude band: 0=south, 1=central, 2=north. NaN if state unknown."""
    if not isinstance(state, str) or state != state:  # NaN check
        return np.nan
    if state in SOUTH_STATES:
        return 0.0
    if state in NORTH_STATES:
        return 2.0
    return 1.0


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("BUILD V18 ASSEMBLED DATASET")
    print("=" * 60)

    if not V17_PATH.exists():
        print(f"ERROR: V17 not found at {V17_PATH}")
        sys.exit(1)

    df = pd.read_csv(V17_PATH, low_memory=False)
    n_rows_orig = len(df)
    n_cols_orig = len(df.columns)
    print(f"V17 base: {n_rows_orig} rows x {n_cols_orig} columns")

    # Parse date
    df["_date"] = pd.to_datetime(df["date"], errors="coerce")

    # ------------------------------------------------------------------
    # A) HURDLE TARGETS
    # ------------------------------------------------------------------
    print("\n--- Hurdle Targets ---")
    df["has_catch"] = (df["median_weight_lb"] > 0).astype(float)
    df["positive_cpue"] = df["median_weight_lb"].where(df["has_catch"] == 1.0)
    print(f"  has_catch: {df['has_catch'].sum():.0f} / {len(df)} ({df['has_catch'].mean()*100:.1f}%)")
    print(f"  positive_cpue non-null: {df['positive_cpue'].notna().sum()}")

    # ------------------------------------------------------------------
    # B) CATCHABILITY COVARIATES
    # ------------------------------------------------------------------
    print("\n--- Catchability Covariates ---")

    # day_of_week (Monday=0 .. Sunday=6)
    df["day_of_week"] = df["_date"].dt.dayofweek.astype("Float64")
    print(f"  day_of_week coverage: {df['day_of_week'].notna().sum()}/{len(df)}")

    # is_weekend
    df["is_weekend"] = df["day_of_week"].apply(
        lambda x: 1.0 if x >= 5 else (0.0 if pd.notna(x) else np.nan)
    )
    print(f"  is_weekend: {df['is_weekend'].sum():.0f} weekend rows ({df['is_weekend'].mean()*100:.1f}%)")

    # is_holiday (within 3 days of US federal holiday)
    df["is_holiday"] = df["_date"].apply(is_near_holiday).astype("Float64")
    print(f"  is_holiday: {df['is_holiday'].sum():.0f} near-holiday rows")

    # tournament_tier
    df["tournament_tier"] = df["trail"].map(TRAIL_TIER).astype("Float64")
    unmapped = df["tournament_tier"].isna().sum()
    if unmapped > 0:
        print(f"  WARNING: {unmapped} rows with unmapped trail -> tier=NaN")
    print(f"  tournament_tier distribution:\n{df['tournament_tier'].value_counts().sort_index().to_string()}")

    # month_sin, month_cos (cyclical encoding)
    month = df["_date"].dt.month.astype("Float64")
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    print(f"  month_sin/cos coverage: {df['month_sin'].notna().sum()}/{len(df)}")

    # waterbody_type (string -> one-hot later, for now store string)
    wtype_r = df.get("wtype_river", pd.Series(np.nan, index=df.index))
    wtype_res = df.get("wtype_reservoir", pd.Series(np.nan, index=df.index))
    wtype_nl = df.get("wtype_natural_lake", pd.Series(np.nan, index=df.index))
    df["waterbody_type"] = [
        classify_waterbody(loc, wr, wres, wnl)
        for loc, wr, wres, wnl in zip(df["location"], wtype_r, wtype_res, wtype_nl)
    ]
    print(f"  waterbody_type distribution:\n{df['waterbody_type'].value_counts().to_string()}")

    # One-hot encode waterbody_type
    wtype_dummies = pd.get_dummies(df["waterbody_type"], prefix="wbtype")
    wtype_dummies = wtype_dummies.astype(float)
    df = pd.concat([df, wtype_dummies], axis=1)
    df.drop(columns=["waterbody_type"], inplace=True)

    # state
    df["state"] = df["location"].apply(extract_state)
    n_state = df["state"].notna().sum()
    print(f"  state extracted: {n_state}/{len(df)} ({n_state/len(df)*100:.1f}%)")

    # state_latitude_band
    df["state_latitude_band"] = df["state"].apply(latitude_band)
    print(f"  state_latitude_band distribution:\n{df['state_latitude_band'].value_counts().sort_index().to_string()}")

    # Drop raw state column (high cardinality, use latitude_band instead)
    df.drop(columns=["state"], inplace=True)

    # log_num_anglers
    df["log_num_anglers"] = np.where(
        df["num_anglers"].notna() & (df["num_anglers"] > 0),
        np.log(df["num_anglers"]),
        np.nan,
    )
    print(f"  log_num_anglers coverage: {df['log_num_anglers'].notna().sum()}/{len(df)}")

    # anglers_per_acre
    has_area = df["area_acres"].notna() & (df["area_acres"] > 0)
    has_anglers = df["num_anglers"].notna() & (df["num_anglers"] > 0)
    df["anglers_per_acre"] = np.where(
        has_area & has_anglers,
        df["num_anglers"] / df["area_acres"],
        np.nan,
    )
    n_apa = pd.notna(df["anglers_per_acre"]).sum()
    print(f"  anglers_per_acre coverage: {n_apa}/{len(df)} ({n_apa/len(df)*100:.1f}%)")

    # multi_day_event and day_in_event
    # day_number is only valid when it's a small integer (1-5); many rows have
    # garbage values (large ints that are actually something else).
    valid_day = df["day_number"].notna() & (df["day_number"] >= 1) & (df["day_number"] <= 5)
    df["day_in_event"] = np.where(valid_day, df["day_number"], np.nan)
    df["multi_day_event"] = np.where(valid_day, (df["day_number"] > 1).astype(float), np.nan)
    print(f"  multi_day_event: {df['multi_day_event'].sum():.0f} multi-day rows "
          f"(valid day_number: {valid_day.sum()}/{len(df)})")

    # ------------------------------------------------------------------
    # C) TEMPORAL FEATURES
    # ------------------------------------------------------------------
    print("\n--- Temporal Features ---")

    # year_trend: normalized year
    year_min = df["_date"].dt.year.min()
    year_max = df["_date"].dt.year.max()
    if year_max > year_min:
        df["year_trend"] = (df["_date"].dt.year - year_min) / (year_max - year_min)
    else:
        df["year_trend"] = 0.0
    df["year_trend"] = df["year_trend"].astype("Float64")
    print(f"  year_trend range: {df['year_trend'].min():.3f} - {df['year_trend'].max():.3f}")

    # days_since_2020
    ref_date = pd.Timestamp("2020-01-01")
    df["days_since_2020"] = (df["_date"] - ref_date).dt.days.astype("Float64")
    print(f"  days_since_2020 range: {df['days_since_2020'].min():.0f} - {df['days_since_2020'].max():.0f}")

    # recent_location_activity: count of events at same location in prior 90 days
    # Sort by date to make lookback efficient
    df = df.sort_values("_date").reset_index(drop=True)
    print("  Computing recent_location_activity (prior 90 days)...")
    loc_date_groups = df.groupby("location")["_date"].apply(list).to_dict()

    # Build a mapping: for each (location, date) -> count of events in [date-90, date)
    activity_map = {}
    for loc, dates in loc_date_groups.items():
        sorted_dates = sorted(dates)
        for i, d in enumerate(sorted_dates):
            if pd.isna(d):
                continue
            cutoff = d - pd.Timedelta(days=90)
            count = sum(1 for dd in sorted_dates[:i] if dd >= cutoff and dd < d)
            activity_map[(loc, d)] = count

    df["recent_location_activity"] = [
        float(activity_map.get((loc, d), np.nan))
        for loc, d in zip(df["location"], df["_date"])
    ]
    print(f"  recent_location_activity: mean={df['recent_location_activity'].mean():.2f}, "
          f"max={df['recent_location_activity'].max():.0f}")

    # rolling_regional_cpue: mean CPUE within 200km in prior 30 days
    print("  Computing rolling_regional_cpue (200km, prior 30 days)...")
    has_coords = df["lat"].notna() & df["lon"].notna()
    coords_mask = has_coords.values

    # Pre-compute for efficiency: use vectorized haversine
    lats = df["lat"].values
    lons = df["lon"].values
    dates = df["_date"].values
    targets = df["median_weight_lb"].values
    n = len(df)

    regional_cpue = np.full(n, np.nan)

    if coords_mask.any():
        # Convert to radians once
        lat_rad = np.radians(lats)
        lon_rad = np.radians(lons)

        # Process in chunks to avoid memory issues
        RADIUS_KM = 200.0
        LOOKBACK_DAYS = np.timedelta64(30, "D")

        for i in range(n):
            if not coords_mask[i] or pd.isna(dates[i]):
                continue
            # Only look at rows before this date
            d_i = dates[i]
            cutoff = d_i - LOOKBACK_DAYS
            mask = (dates < d_i) & (dates >= cutoff) & coords_mask

            if not mask.any():
                continue

            # Haversine distance
            idx = np.where(mask)[0]
            dlat = lat_rad[idx] - lat_rad[i]
            dlon = lon_rad[idx] - lon_rad[i]
            a = np.sin(dlat / 2) ** 2 + np.cos(lat_rad[i]) * np.cos(lat_rad[idx]) * np.sin(dlon / 2) ** 2
            dist_km = 6371 * 2 * np.arcsin(np.sqrt(a))

            nearby = idx[dist_km <= RADIUS_KM]
            # Exclude same location to avoid leakage
            nearby_vals = targets[nearby]
            valid = ~np.isnan(nearby_vals)
            if valid.sum() > 0:
                regional_cpue[i] = np.nanmean(nearby_vals[valid])

        if i % 1000 == 0 and i > 0:
            pass  # Progress tracking removed for cleanliness

    df["rolling_regional_cpue"] = regional_cpue
    n_reg = np.sum(~np.isnan(regional_cpue))
    print(f"  rolling_regional_cpue coverage: {n_reg}/{n} ({n_reg/n*100:.1f}%)")

    # ------------------------------------------------------------------
    # D) INTERACTION FEATURES
    # ------------------------------------------------------------------
    print("\n--- Interaction Features ---")

    # pressure_x_temp_change
    # Try pressure_delta_1d first (best coverage), fall back to pressure_change_rate
    pressure_col = None
    for pcol in ["pressure_delta_1d", "pressure_change_rate"]:
        if pcol in df.columns and df[pcol].notna().sum() > 0:
            pressure_col = pcol
            break
    # Try temp_delta_1d first, fall back to temp_delta_24h_c
    temp_change_col = None
    for tcol in ["temp_delta_1d", "temp_delta_24h_c"]:
        if tcol in df.columns and df[tcol].notna().sum() > 0:
            temp_change_col = tcol
            break
    if pressure_col and temp_change_col:
        df["pressure_x_temp_change"] = df[pressure_col] * df[temp_change_col]
        n_pxt = df["pressure_x_temp_change"].notna().sum()
        print(f"  pressure_x_temp_change ({pressure_col} * {temp_change_col}) coverage: {n_pxt}/{n}")
    else:
        df["pressure_x_temp_change"] = np.nan
        print(f"  pressure_x_temp_change: missing source columns")

    # wind_x_cloud (turbidity proxy)
    wind_col = "wind_speed_kph" if "wind_speed_kph" in df.columns else None
    cloud_col = "np_cloud_pct" if "np_cloud_pct" in df.columns else None
    if wind_col and cloud_col:
        df["wind_x_cloud"] = df[wind_col] * df[cloud_col]
        n_wxc = df["wind_x_cloud"].notna().sum()
        print(f"  wind_x_cloud coverage: {n_wxc}/{n}")
    else:
        df["wind_x_cloud"] = np.nan
        print(f"  wind_x_cloud: missing source columns")

    # spawn_x_temp (spawn trigger interaction)
    spawn_col = "spawn_probability" if "spawn_probability" in df.columns else None
    wtemp_col = "water_temp_c" if "water_temp_c" in df.columns else None
    if spawn_col and wtemp_col:
        df["spawn_x_temp"] = df[spawn_col] * df[wtemp_col]
        n_sxt = df["spawn_x_temp"].notna().sum()
        print(f"  spawn_x_temp coverage: {n_sxt}/{n}")
    else:
        df["spawn_x_temp"] = np.nan
        print(f"  spawn_x_temp: missing source columns")

    # ------------------------------------------------------------------
    # E) STREAM-NETWORK FEATURES
    # ------------------------------------------------------------------
    print("\n--- Stream-Network Features ---")

    # is_river: 1.0 if waterbody classified as river
    df["is_river_v2"] = np.where(
        df.get("wbtype_river", pd.Series(0, index=df.index)) == 1.0,
        1.0,
        0.0,
    )
    print(f"  is_river_v2: {df['is_river_v2'].sum():.0f} river rows")

    # stream_order_estimate
    df["stream_order_estimate"] = df["location"].apply(estimate_stream_order)
    n_so = df["stream_order_estimate"].notna().sum()
    print(f"  stream_order_estimate coverage: {n_so}/{n}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    df.drop(columns=["_date"], inplace=True)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    new_cols = len(df.columns) - n_cols_orig
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total rows: {len(df)}")
    print(f"  Total columns: {len(df.columns)} (+{new_cols} new)")
    print(f"  Zero-catch fraction: {(df['median_weight_lb'] <= 0).mean()*100:.2f}%")
    print(f"  has_catch=1 fraction: {df['has_catch'].mean()*100:.2f}%")

    # New feature distributions
    new_feature_names = [
        "has_catch", "positive_cpue", "day_of_week", "is_weekend", "is_holiday",
        "tournament_tier", "month_sin", "month_cos", "state_latitude_band",
        "log_num_anglers", "anglers_per_acre", "multi_day_event", "day_in_event",
        "year_trend", "days_since_2020", "recent_location_activity",
        "rolling_regional_cpue", "pressure_x_temp_change", "wind_x_cloud",
        "spawn_x_temp", "is_river_v2", "stream_order_estimate",
    ]
    print(f"\nNew feature summary stats:")
    for col in new_feature_names:
        if col in df.columns:
            s = df[col]
            nn = s.notna().sum()
            if nn > 0:
                print(f"  {col:35s} | non-null: {nn:5d} | mean: {s.mean():10.4f} | std: {s.std():10.4f}")
            else:
                print(f"  {col:35s} | all NaN")

    # List all wbtype_ columns
    wbtype_cols = [c for c in df.columns if c.startswith("wbtype_")]
    if wbtype_cols:
        print(f"\n  Waterbody type one-hot columns: {wbtype_cols}")
        for c in wbtype_cols:
            print(f"    {c}: {df[c].sum():.0f} rows")

    # Save
    ASSEMBLED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved to {OUTPUT_PATH}")
    print(f"File size: {OUTPUT_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
