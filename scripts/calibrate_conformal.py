#!/usr/bin/env python3
"""Calibrate conformal prediction intervals for the V15 ensemble.

Loads the V15 model and V17 dataset, performs a temporal split (train on
events before 2023, calibrate on 2023+), and saves the conformal calibration
data.  No model retraining is required — this is purely post-hoc.

Usage:
    python scripts/calibrate_conformal.py [--alpha 0.10] [--bandwidth 200]

Outputs:
    castline/models/cpue_v15_conformal.json   — calibration data
    Prints coverage statistics to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from castline.models.inference_v14 import V14Predictor
from castline.models.conformal import ConformalPredictor


# ── Constants ──────────────────────────────────────────────────

MODEL_DIR = ROOT / "castline" / "models"
DATASET_PATH = ROOT / "castline" / "validation" / "data" / "assembled" / "validation_dataset_v17.csv"
OUTPUT_PATH = MODEL_DIR / "cpue_v15_conformal.json"

TARGET = "median_weight_lb"
CAL_YEAR_CUTOFF = 2023  # calibrate on 2023+ data

# Columns that are metadata, not features
NON_FEATURE_COLS = {
    "event_id", "tournament_slug", "event_name", "date", "location",
    "species", "median_weight_lb", "baseline_signal", "usgs_site_id",
    "results_source", "num_anglers", "day_number", "tms_id", "trail",
    "source", "year", "month",
}


def main():
    parser = argparse.ArgumentParser(description="Calibrate conformal prediction intervals")
    parser.add_argument("--alpha", type=float, default=0.10,
                        help="Miscoverage rate (default: 0.10 for 90%% intervals)")
    parser.add_argument("--bandwidth", type=float, default=200.0,
                        help="Geo bandwidth in km (default: 200)")
    parser.add_argument("--version", type=str, default="v15",
                        help="Model version to load (default: v15)")
    parser.add_argument("--cal-year", type=int, default=CAL_YEAR_CUTOFF,
                        help=f"Year cutoff for calibration set (default: {CAL_YEAR_CUTOFF})")
    args = parser.parse_args()

    # ── Load model ────────────────────────────────────────────
    print(f"Loading {args.version} model from {MODEL_DIR} ...")
    predictor = V14Predictor.load(MODEL_DIR, version=args.version)
    print(f"  Seen models:   {list(predictor.seen_models.keys())}")
    print(f"  Unseen models: {list(predictor.unseen_models.keys())}")
    print(f"  Seen features: {len(predictor.seen_features)}")
    print(f"  Unseen features: {len(predictor.unseen_features)}")
    print(f"  Known locations: {len(predictor.location_stats)}")

    # ── Load dataset ──────────────────────────────────────────
    print(f"\nLoading dataset from {DATASET_PATH} ...")
    df = pd.read_csv(DATASET_PATH)
    print(f"  Total rows: {len(df)}")

    # Parse dates and extract year
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = df["date"].dt.year.astype("Int64")
    df = df.dropna(subset=[TARGET, "year"]).copy()
    print(f"  Valid rows (with target and year): {len(df)}")

    # ── Temporal split ────────────────────────────────────────
    cal_mask = df["year"] >= args.cal_year
    df_cal = df[cal_mask].copy()
    df_train = df[~cal_mask].copy()
    print(f"\n  Train set (< {args.cal_year}): {len(df_train)} rows")
    print(f"  Calibration set (>= {args.cal_year}): {len(df_cal)} rows")

    if len(df_cal) < 20:
        print(f"\nERROR: Calibration set too small ({len(df_cal)} rows). "
              f"Try a lower --cal-year.")
        sys.exit(1)

    # ── Prepare calibration features ──────────────────────────
    # Use the union of seen/unseen features — the predictor handles column selection
    all_features = sorted(set(predictor.seen_features) | set(predictor.unseen_features))
    for col in all_features:
        if col not in df_cal.columns:
            df_cal[col] = np.nan

    X_cal = df_cal[all_features].copy()
    y_cal = df_cal[TARGET].values
    locations_cal = df_cal["location"].tolist()

    # Geographic coordinates
    has_coords = "lat" in df_cal.columns and "lon" in df_cal.columns
    if has_coords:
        coords_cal = df_cal[["lat", "lon"]].values
        n_valid_coords = np.isfinite(coords_cal).all(axis=1).sum()
        print(f"  Rows with valid lat/lon: {n_valid_coords}")
        if n_valid_coords < 10:
            print("  WARNING: Very few valid coordinates — GeoConformal may be unreliable")
    else:
        coords_cal = None
        print("  No lat/lon columns — GeoConformal disabled")

    # ── Calibrate ─────────────────────────────────────────────
    print(f"\nCalibrating with alpha={args.alpha} "
          f"({(1-args.alpha)*100:.0f}% coverage target) ...")
    cp = ConformalPredictor(
        predictor=predictor,
        alpha=args.alpha,
        geo_bandwidth_km=args.bandwidth,
    )
    stats = cp.calibrate(
        X_cal=X_cal,
        y_cal=y_cal,
        locations_cal=locations_cal,
        coords_cal=coords_cal,
    )

    print("\n── Calibration Statistics ─────────────────────────")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:30s}: {v:.4f}")
        else:
            print(f"  {k:30s}: {v}")

    # ── Save ──────────────────────────────────────────────────
    cp.save(OUTPUT_PATH)
    print(f"\nSaved calibration to {OUTPUT_PATH}")

    # ── Verify coverage on calibration set ────────────────────
    print("\n── Empirical Coverage (on calibration set) ────────")
    cov = cp.empirical_coverage(
        X_cal, y_cal,
        locations=locations_cal,
        lats=coords_cal[:, 0] if coords_cal is not None else None,
        lons=coords_cal[:, 1] if coords_cal is not None else None,
    )
    for k, v in cov.items():
        if isinstance(v, float):
            print(f"  {k:30s}: {v:.4f}")
        else:
            print(f"  {k:30s}: {v}")

    target_cov = 1.0 - args.alpha
    actual_cov = cov["coverage"]
    if actual_cov >= target_cov - 0.02:
        print(f"\n  PASS: Coverage {actual_cov:.1%} >= target {target_cov:.1%}")
    else:
        print(f"\n  WARNING: Coverage {actual_cov:.1%} < target {target_cov:.1%} "
              f"(expected on small calibration sets)")

    # ── Example predictions with uncertainty ──────────────────
    print("\n── Example Predictions with Uncertainty ────────────")
    sample_idx = np.random.default_rng(42).choice(len(df_cal), size=min(5, len(df_cal)), replace=False)
    for i in sample_idx:
        row = X_cal.iloc[[i]]
        loc = locations_cal[i]
        lat_i = float(coords_cal[i, 0]) if coords_cal is not None else None
        lon_i = float(coords_cal[i, 1]) if coords_cal is not None else None
        actual = float(y_cal[i])

        result = cp.predict_with_interval(row, location=loc, lat=lat_i, lon=lon_i)
        in_band = "Y" if result.lower_bound <= actual <= result.upper_bound else "N"
        print(
            f"  {loc[:35]:35s}  actual={actual:.2f}  "
            f"pred={result.point_prediction:.2f} "
            f"[{result.lower_bound:.2f}, {result.upper_bound:.2f}]  "
            f"width={result.interval_width:.2f}  "
            f"in_band={in_band}  ({result.method})"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
