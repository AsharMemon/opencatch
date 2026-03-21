"""
Compute cold front passage and 1-day delta features for the V16 dataset.

Uses NASA POWER 7-day daily history (nasa_power_7day.csv) which has 8 daily
observations per event (day -7 through day 0, where day 0 = event date).

Output features:
  - is_cold_front: binary cold front detection
  - cold_front_severity: 0-3 scale
  - post_frontal_day: 1 if 1-2 days after a cold front
  - temp_delta_1d: temperature change from day before (C)
  - pressure_delta_1d: pressure change from day before (kPa)
  - wind_speed_delta_1d: wind speed change from day before (m/s)
  - temp_delta_magnitude: abs(temp_delta_1d)
  - is_temp_crash: 1 if temp dropped >5.56C (10F) in 1 day
  - is_pressure_crash: 1 if pressure dropped >0.508 kPa (0.15 inHg) in 1 day
  - weather_stability_3d: std dev of temperature over prior 3 days
  - pressure_stability_3d: std dev of pressure over prior 3 days
"""

import pandas as pd
import numpy as np
from pathlib import Path

# --- Paths ---
NP7_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/raw/nasa_power_7day.csv"
V16_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/assembled/validation_dataset_v16.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/raw/cold_front_features.csv"

# --- Unit conversions ---
# Thresholds specified in imperial; NASA POWER data is metric.
# 5 F = 2.778 C, 10 F = 5.556 C
# 0.10 inHg = 0.3386 kPa, 0.15 inHg = 0.5080 kPa
COLD_FRONT_TEMP_DROP_C = 2.778       # 5 F
COLD_FRONT_PRESSURE_DROP_KPA = 0.3386  # 0.10 inHg
TEMP_CRASH_C = 5.556                  # 10 F
PRESSURE_CRASH_KPA = 0.508            # 0.15 inHg


def compute_cold_front_features():
    print("Loading NASA POWER 7-day daily data...")
    np7 = pd.read_csv(NP7_PATH)
    np7["date"] = pd.to_datetime(np7["date"], format="%Y%m%d")
    np7["event_date"] = pd.to_datetime(np7["event_date"])
    print(f"  Rows: {len(np7)}, unique events: {np7.groupby(['event_lat','event_lon','event_date']).ngroups}")

    print("Loading V16 dataset for location mapping...")
    v16 = pd.read_csv(V16_PATH, usecols=["location", "date", "lat", "lon"])
    v16["date"] = pd.to_datetime(v16["date"])

    # Build a (rounded lat, rounded lon, event_date) -> location mapping
    # Round to 4 decimal places for matching
    v16["lat_r"] = v16["lat"].round(4)
    v16["lon_r"] = v16["lon"].round(4)
    loc_map = v16.set_index(["lat_r", "lon_r", "date"])["location"].to_dict()

    # Sort daily data by event then date within event
    np7 = np7.sort_values(["event_lat", "event_lon", "event_date", "date"]).reset_index(drop=True)

    # Group by event (unique lat/lon/event_date)
    groups = np7.groupby(["event_lat", "event_lon", "event_date"])

    results = []

    for (elat, elon, edate), g in groups:
        g = g.sort_values("date").reset_index(drop=True)

        # Look up location name
        lat_r = round(elat, 4)
        lon_r = round(elon, 4)
        location = loc_map.get((lat_r, lon_r, edate), None)
        if location is None:
            # Try without rounding (float matching issues)
            location = "UNKNOWN"

        temp = g["np_temp_mean_c"].values    # daily mean temp in C
        pres = g["np_pressure_kpa"].values   # daily surface pressure in kPa
        wind = g["np_wind_2m_ms"].values     # daily wind speed in m/s

        n = len(g)
        # Day 0 is last row (event day), day -1 is second-to-last, etc.
        # Index: 0=day-7, 1=day-6, ..., 6=day-1, 7=day0

        row = {
            "location": location,
            "date": edate.strftime("%Y-%m-%d"),
            "lat": elat,
            "lon": elon,
        }

        # --- 1-day deltas (day0 minus day-1) ---
        if n >= 2:
            row["temp_delta_1d"] = temp[-1] - temp[-2] if not (np.isnan(temp[-1]) or np.isnan(temp[-2])) else np.nan
            row["pressure_delta_1d"] = pres[-1] - pres[-2] if not (np.isnan(pres[-1]) or np.isnan(pres[-2])) else np.nan
            row["wind_speed_delta_1d"] = wind[-1] - wind[-2] if not (np.isnan(wind[-1]) or np.isnan(wind[-2])) else np.nan
        else:
            row["temp_delta_1d"] = np.nan
            row["pressure_delta_1d"] = np.nan
            row["wind_speed_delta_1d"] = np.nan

        # --- Magnitude features ---
        row["temp_delta_magnitude"] = abs(row["temp_delta_1d"]) if not np.isnan(row.get("temp_delta_1d", np.nan)) else np.nan

        # --- Crash flags ---
        td = row["temp_delta_1d"]
        pd_ = row["pressure_delta_1d"]
        row["is_temp_crash"] = 1 if (not np.isnan(td) and td < -TEMP_CRASH_C) else (0 if not np.isnan(td) else np.nan)
        row["is_pressure_crash"] = 1 if (not np.isnan(pd_) and pd_ < -PRESSURE_CRASH_KPA) else (0 if not np.isnan(pd_) else np.nan)

        # --- Cold front detection ---
        # Check day-1 to day0 AND day-2 to day-1 for front passage
        # A cold front could have passed on the event day or the day before
        is_cold_front = 0
        cold_front_severity = 0
        post_frontal_day = 0

        # Check each pair for front passage: (day-3 to day-2), (day-2 to day-1), (day-1 to day0)
        front_days = []  # which day offsets had a front
        if n >= 2:
            for i in range(max(0, n - 4), n - 1):
                t_drop = temp[i] - temp[i + 1]  # positive = cooling
                p_drop = pres[i] - pres[i + 1]  # positive = pressure fell
                if np.isnan(t_drop) or np.isnan(p_drop):
                    continue
                if p_drop > COLD_FRONT_PRESSURE_DROP_KPA and t_drop > COLD_FRONT_TEMP_DROP_C:
                    days_before_event = (n - 1) - (i + 1)  # how many days before event day
                    front_days.append({
                        "days_before": days_before_event,
                        "t_drop": t_drop,
                        "p_drop": p_drop,
                    })

        if front_days:
            # Use the most recent front
            most_recent = min(front_days, key=lambda x: x["days_before"])
            is_cold_front = 1 if most_recent["days_before"] == 0 else 0
            post_frontal_day = 1 if most_recent["days_before"] in (1, 2) else 0

            # If front is on event day, it's a cold front day; if 1-2 days ago, post-frontal
            # Severity: based on magnitude
            t_drop = most_recent["t_drop"]
            p_drop = most_recent["p_drop"]

            # Severity scale 0-3
            severity = 0
            # Temp component
            if t_drop > 2 * COLD_FRONT_TEMP_DROP_C:  # >10F drop
                severity += 2
            elif t_drop > COLD_FRONT_TEMP_DROP_C:     # >5F drop
                severity += 1
            # Pressure component
            if p_drop > 2 * COLD_FRONT_PRESSURE_DROP_KPA:  # >0.20 inHg
                severity += 1

            cold_front_severity = min(severity, 3)

        row["is_cold_front"] = is_cold_front
        row["cold_front_severity"] = cold_front_severity
        row["post_frontal_day"] = post_frontal_day

        # --- Weather stability (3-day std dev before event) ---
        if n >= 4:
            # Days -3, -2, -1 (indices n-4, n-3, n-2)
            temp_3d = temp[n - 4:n - 1]
            pres_3d = pres[n - 4:n - 1]
            valid_temp = temp_3d[~np.isnan(temp_3d)]
            valid_pres = pres_3d[~np.isnan(pres_3d)]
            row["weather_stability_3d"] = float(np.std(valid_temp, ddof=1)) if len(valid_temp) >= 2 else np.nan
            row["pressure_stability_3d"] = float(np.std(valid_pres, ddof=1)) if len(valid_pres) >= 2 else np.nan
        else:
            row["weather_stability_3d"] = np.nan
            row["pressure_stability_3d"] = np.nan

        results.append(row)

    df_out = pd.DataFrame(results)

    # Join back to get location for any UNKNOWN rows via lat/lon fuzzy match
    unknown_mask = df_out["location"] == "UNKNOWN"
    if unknown_mask.sum() > 0:
        print(f"  Attempting fuzzy location match for {unknown_mask.sum()} rows...")
        v16_dedup = v16.drop_duplicates(subset=["lat", "lon"])[["lat", "lon", "location"]].copy()
        for idx in df_out[unknown_mask].index:
            lat_q, lon_q = df_out.loc[idx, "lat"], df_out.loc[idx, "lon"]
            dists = (v16_dedup["lat"] - lat_q) ** 2 + (v16_dedup["lon"] - lon_q) ** 2
            best = dists.idxmin()
            if dists[best] < 0.001:  # ~100m tolerance
                df_out.loc[idx, "location"] = v16_dedup.loc[best, "location"]

    # Select output columns
    out_cols = [
        "location", "date",
        "is_cold_front", "cold_front_severity", "post_frontal_day",
        "temp_delta_1d", "pressure_delta_1d", "wind_speed_delta_1d",
        "temp_delta_magnitude", "is_temp_crash", "is_pressure_crash",
        "weather_stability_3d", "pressure_stability_3d",
    ]
    df_out = df_out[out_cols]

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved {len(df_out)} rows to {OUTPUT_PATH}")

    # --- Report ---
    print(f"\n{'='*60}")
    print("FEATURE COVERAGE AND DISTRIBUTION SUMMARY")
    print(f"{'='*60}")
    print(f"Total rows: {len(df_out)}")
    print(f"Unique locations: {df_out['location'].nunique()}")
    print(f"UNKNOWN locations: {(df_out['location'] == 'UNKNOWN').sum()}")
    print()

    for col in out_cols[2:]:  # skip location, date
        series = df_out[col]
        non_null = series.notna().sum()
        pct = 100 * non_null / len(df_out)
        print(f"--- {col} ---")
        print(f"  Coverage: {non_null}/{len(df_out)} ({pct:.1f}%)")
        if non_null > 0:
            if series.dropna().nunique() <= 5:
                # Categorical / binary - show value counts
                vc = series.dropna().value_counts().sort_index()
                for v, c in vc.items():
                    print(f"  {v}: {c} ({100*c/non_null:.1f}%)")
            else:
                desc = series.describe()
                print(f"  mean={desc['mean']:.4f}, std={desc['std']:.4f}")
                print(f"  min={desc['min']:.4f}, 25%={desc['25%']:.4f}, 50%={desc['50%']:.4f}, 75%={desc['75%']:.4f}, max={desc['max']:.4f}")
        print()


if __name__ == "__main__":
    compute_cold_front_features()
