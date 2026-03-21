#!/usr/bin/env python3
"""
Build V17 assembled dataset — merges V16 base with new feature files:
  - Temperature departure (NOAA-based)
  - Solunar feeding windows
  - Cold front / 1-day delta features
  - Spawn timing features
  - 30-day lag trends (if available)

Usage:
    python castline/validation/assembly/build_v17.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
ASSEMBLED_DIR = BASE_DIR / "data" / "assembled"

V16_PATH = ASSEMBLED_DIR / "validation_dataset_v16.csv"
OUTPUT_PATH = ASSEMBLED_DIR / "validation_dataset_v17.csv"

FEATURE_FILES = {
    "temp_departure": RAW_DIR / "tournament_temp_departure.csv",
    "solunar": RAW_DIR / "solunar_features.csv",
    "cold_front": RAW_DIR / "cold_front_features.csv",
    "spawn": RAW_DIR / "spawn_timing_features.csv",
    "lag_trends": RAW_DIR / "lag_trend_features.csv",
}


def main():
    print("=" * 60)
    print("BUILD V17 ASSEMBLED DATASET")
    print("=" * 60)

    if not V16_PATH.exists():
        print(f"ERROR: V16 not found at {V16_PATH}")
        sys.exit(1)

    df = pd.read_csv(V16_PATH, low_memory=False)
    print(f"V16 base: {len(df)} rows x {len(df.columns)} columns")

    # Normalize date columns for merging
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    merged_count = 0
    for name, path in FEATURE_FILES.items():
        if not path.exists():
            print(f"  {name}: NOT FOUND — skipping")
            continue

        feat = pd.read_csv(path)
        feat["date"] = pd.to_datetime(feat["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        # Columns to merge (exclude location, date, lat which are join keys)
        skip_cols = {"location", "date", "lat", "lon"}
        new_cols = [c for c in feat.columns if c not in skip_cols]

        # Drop any columns that already exist in base
        existing = set(df.columns)
        new_cols = [c for c in new_cols if c not in existing]

        if not new_cols:
            print(f"  {name}: all columns already exist — skipping")
            continue

        merge_cols = ["location", "date"]
        feat_subset = feat[merge_cols + new_cols].copy()
        # Deduplicate: keep first occurrence per (location, date)
        feat_subset = feat_subset.drop_duplicates(subset=merge_cols, keep="first")

        before = len(df.columns)
        n_before = len(df)
        df = df.merge(feat_subset, on=merge_cols, how="left")
        after = len(df.columns)
        if len(df) != n_before:
            print(f"  WARNING: merge changed row count {n_before} -> {len(df)}")
            # Deduplicate base dataset too
            df = df.drop_duplicates(subset=merge_cols, keep="first")
            print(f"  After dedup: {len(df)} rows")

        # Coverage stats
        for c in new_cols:
            n = df[c].notna().sum()
            pct = n / len(df) * 100
            print(f"  {name}/{c}: {n}/{len(df)} ({pct:.0f}%)")

        merged_count += after - before
        print(f"  {name}: +{after - before} columns")

    print(f"\nTotal new features merged: {merged_count}")
    print(f"Final dataset: {len(df)} rows x {len(df.columns)} columns")

    # Save
    ASSEMBLED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved to {OUTPUT_PATH}")
    print(f"File size: {OUTPUT_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
