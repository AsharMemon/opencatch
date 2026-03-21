#!/usr/bin/env python3
"""
Compute NOAA-style temperature departure features for the tournament dataset.

Uses a latitude-based sinusoidal approximation of 1991-2020 monthly climate
normals, then computes how much each event's observed air temperature deviates
from the expected normal for that location and month.

Input:  validation_dataset_v16.csv
Output: tournament_temp_departure.csv
"""

import math
import numpy as np
import pandas as pd

# ---------- paths ----------
V16_PATH = "/Users/Ashar/Documents/fish/castline/validation/data/assembled/validation_dataset_v16.csv"
OUT_PATH = "/Users/Ashar/Documents/fish/castline/validation/data/raw/tournament_temp_departure.csv"

# ---------- load ----------
print("Loading V16 dataset ...")
df = pd.read_csv(V16_PATH)
print(f"  Rows: {len(df):,}  Columns: {df.shape[1]}")

# ---------- resolve best available air temperature (Fahrenheit) ----------
# Priority: air_temp_c > om_air_temp_mean > np_temp_mean_c
# All source columns are in Celsius; convert to Fahrenheit.

temp_c = df["air_temp_c"].copy()
temp_c = temp_c.fillna(df["om_air_temp_mean"])
temp_c = temp_c.fillna(df["np_temp_mean_c"])

temp_f = temp_c * 9.0 / 5.0 + 32.0
print(f"  Temperature coverage: {temp_f.notna().sum():,} / {len(df):,} "
      f"({100 * temp_f.notna().sum() / len(df):.1f}%)")

# ---------- latitude-based climate normal (Fahrenheit) ----------
# Sinusoidal model:
#   T_normal = A - B * cos(2*pi*(month - 7) / 12)
# where
#   A (annual mean)        ~ 75 - 1.2 * (lat - 25)   [F]
#   B (seasonal amplitude) ~ 10 + 0.6 * (lat - 25)   [F]

lat = df["lat"].values
month = df["month"].values

A = 75.0 - 1.2 * (lat - 25.0)        # annual mean temp (F)
B = 10.0 + 0.6 * (lat - 25.0)        # seasonal amplitude (F)
T_normal = A - B * np.cos(2.0 * np.pi * (month - 7.0) / 12.0)

# ---------- departure features ----------
departure_f = temp_f.values - T_normal  # positive = warmer than normal

# Avoid division by zero for very cold normals
safe_normal = np.where(np.abs(T_normal) < 1.0, 1.0, T_normal)
departure_pct = (departure_f / safe_normal) * 100.0

is_warm_anomaly = (departure_f > 5.0).astype(int)
is_cold_anomaly = (departure_f < -5.0).astype(int)
warm_spell_indicator = (departure_f > 5.0).astype(int)

# ---------- build output ----------
out = pd.DataFrame({
    "location": df["location"],
    "date": df["date"],
    "temp_departure_f": np.round(departure_f, 2),
    "temp_departure_pct": np.round(departure_pct, 2),
    "is_warm_anomaly": is_warm_anomaly,
    "is_cold_anomaly": is_cold_anomaly,
    "warm_spell_indicator": warm_spell_indicator,
})

# Drop rows where we had no temperature at all
valid = out["temp_departure_f"].notna()
out = out[valid].copy()
print(f"  Output rows (with valid temp): {len(out):,}")

# ---------- save ----------
out.to_csv(OUT_PATH, index=False)
print(f"  Saved to {OUT_PATH}")

# ---------- summary statistics ----------
dep = out["temp_departure_f"]
print("\n--- Temperature Departure Summary ---")
print(f"  Count:    {len(dep):,}")
print(f"  Mean:     {dep.mean():.2f} F")
print(f"  Median:   {dep.median():.2f} F")
print(f"  Std Dev:  {dep.std():.2f} F")
print(f"  Min:      {dep.min():.2f} F")
print(f"  Max:      {dep.max():.2f} F")
print(f"  25th pct: {dep.quantile(0.25):.2f} F")
print(f"  75th pct: {dep.quantile(0.75):.2f} F")
print(f"\n  Warm anomalies (>+5 F):  {out['is_warm_anomaly'].sum():,}  "
      f"({100 * out['is_warm_anomaly'].mean():.1f}%)")
print(f"  Cold anomalies (<-5 F):  {out['is_cold_anomaly'].sum():,}  "
      f"({100 * out['is_cold_anomaly'].mean():.1f}%)")

# Distribution buckets
print("\n--- Departure Distribution ---")
bins = [-999, -15, -10, -5, 0, 5, 10, 15, 999]
labels = ["< -15", "-15 to -10", "-10 to -5", "-5 to 0",
          "0 to 5", "5 to 10", "10 to 15", "> 15"]
out["bucket"] = pd.cut(dep, bins=bins, labels=labels)
dist = out["bucket"].value_counts().sort_index()
for label, count in dist.items():
    pct = 100 * count / len(out)
    bar = "#" * int(pct)
    print(f"  {label:>12s}: {count:5,} ({pct:5.1f}%)  {bar}")

print("\nDone.")
