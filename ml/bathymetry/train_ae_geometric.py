#!/usr/bin/env python3
"""
OpenCatch — Area-Elevation (A-E) Geometric Bathymetry from 3D-LAKES

Implements the 3D-LAKES approach where bathymetry is derived from water-level
variation rather than spectral bands:

  1. ICESat-2 ATL08 gives terrain elevation around lakeshores
  2. Landsat GSW occurrence shows where water was at different times
  3. Pairing elevation + water occurrence = Area-Elevation (A-E) curve
  4. A-E curve IS the bathymetry profile

This script:
  - Loads 3D-LAKES L1 A-E CSVs (510K lakes)
  - Fits parametric curves to each lake's A-E profile
  - Extracts geometric features (slope, curvature, concavity)
  - Trains a model to predict max depth from A-E curve shape
  - Compares A-E geometric predictions vs direct elevation range

The key insight: the SHAPE of the A-E curve encodes bathymetric information
beyond just the elevation range. A steep convex curve = bowl-shaped lake bed.
A concave curve = shelf with deep center.

Usage:
    python train_ae_geometric.py \
        --l1-dir /data/3d_lakes_l1 \
        --depths /data/3d_lakes_with_depths.parquet \
        --qa /data/3d_lakes_qa.csv \
        --output /data/models/ae_geometric \
        --n-workers 8

Requirements:
    pip install pandas numpy scipy scikit-learn xgboost lightgbm tqdm pyarrow
"""

import argparse
import gc
import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import interpolate, optimize
from scipy.integrate import trapezoid
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ae_geometric")


# ── A-E Curve Feature Extraction ────────────────────────────────────

def extract_ae_features(elevations: np.ndarray, areas: np.ndarray) -> Optional[Dict]:
    """
    Extract geometric features from an Area-Elevation curve.

    Features encode the SHAPE of the lake bed:
      - slope statistics: how steeply the bed drops
      - curvature: bowl vs shelf shape
      - volume ratios: how volume distributes with depth
      - parametric fit: power-law / polynomial coefficients
    """
    if len(elevations) < 3:
        return None

    # Sort by elevation ascending
    sort_idx = np.argsort(elevations)
    elev = elevations[sort_idx].astype(np.float64)
    area = areas[sort_idx].astype(np.float64)

    # Normalize to depth (0 = surface, positive = deeper)
    elev_range = elev[-1] - elev[0]
    if elev_range <= 0:
        return None

    depth = elev[-1] - elev  # convert to depth below surface
    depth_norm = depth / elev_range  # 0-1 normalized depth
    area_norm = area / area.max() if area.max() > 0 else area

    n_points = len(elev)

    features = {
        "n_ae_points": n_points,
        "elev_range_m": elev_range,
        "max_area_m2": float(area.max()),
        "min_area_m2": float(area.min()),
        "area_ratio": float(area.min() / area.max()) if area.max() > 0 else 0,
    }

    # ── Slope features ──
    # dA/dE at various points along the curve
    if n_points >= 3:
        darea_delev = np.gradient(area, elev)
        features["slope_mean"] = float(np.mean(darea_delev))
        features["slope_std"] = float(np.std(darea_delev))
        features["slope_max"] = float(np.max(np.abs(darea_delev)))
        features["slope_bottom"] = float(darea_delev[0])  # slope at deepest
        features["slope_surface"] = float(darea_delev[-1])  # slope at surface
        # Ratio of bottom to surface slope
        if abs(darea_delev[-1]) > 1e-6:
            features["slope_ratio"] = float(darea_delev[0] / darea_delev[-1])
        else:
            features["slope_ratio"] = 0.0
    else:
        features.update({
            "slope_mean": 0, "slope_std": 0, "slope_max": 0,
            "slope_bottom": 0, "slope_surface": 0, "slope_ratio": 0,
        })

    # ── Curvature features ──
    if n_points >= 4:
        d2area_delev2 = np.gradient(np.gradient(area, elev), elev)
        features["curvature_mean"] = float(np.mean(d2area_delev2))
        features["curvature_std"] = float(np.std(d2area_delev2))
        # Positive curvature = convex (bowl), negative = concave (shelf)
        features["convexity"] = float(np.mean(np.sign(d2area_delev2)))
    else:
        features["curvature_mean"] = 0
        features["curvature_std"] = 0
        features["convexity"] = 0

    # ── Volume features ──
    # Hypsometric integral: ratio of actual volume to theoretical max
    try:
        total_vol = trapezoid(area, elev)
        max_vol = area.max() * elev_range
        features["hypsometric_integral"] = float(total_vol / max_vol) if max_vol > 0 else 0.5
    except Exception:
        features["hypsometric_integral"] = 0.5

    # Volume at 25%, 50%, 75% depth
    if n_points >= 5:
        try:
            interp_func = interpolate.interp1d(
                depth_norm, area_norm, kind="linear",
                bounds_error=False, fill_value=(area_norm[0], area_norm[-1]),
            )
            for pct in [0.25, 0.50, 0.75]:
                features[f"area_at_depth_{int(pct*100)}pct"] = float(interp_func(pct))
        except Exception:
            for pct in [0.25, 0.50, 0.75]:
                features[f"area_at_depth_{int(pct*100)}pct"] = 0.5
    else:
        for pct in [0.25, 0.50, 0.75]:
            features[f"area_at_depth_{int(pct*100)}pct"] = 0.5

    # ── Power-law fit: Area = a * depth^b ──
    # Many natural lakes follow a power-law A-E relationship
    if n_points >= 4 and depth_norm.min() < depth_norm.max():
        try:
            # Fit A = c * (1 - d/D)^p  (normalized)
            valid = (depth_norm > 0) & (area_norm > 0)
            if valid.sum() >= 3:
                log_d = np.log(depth_norm[valid])
                log_a = np.log(area_norm[valid])
                # Linear fit in log-log space
                coeffs = np.polyfit(log_d, log_a, 1)
                features["power_exponent"] = float(coeffs[0])
                features["power_intercept"] = float(coeffs[1])
                # Residual of power-law fit (lower = better fit)
                predicted = np.polyval(coeffs, log_d)
                features["power_residual"] = float(np.sqrt(np.mean((log_a - predicted)**2)))
            else:
                features["power_exponent"] = 1.0
                features["power_intercept"] = 0.0
                features["power_residual"] = 1.0
        except Exception:
            features["power_exponent"] = 1.0
            features["power_intercept"] = 0.0
            features["power_residual"] = 1.0
    else:
        features["power_exponent"] = 1.0
        features["power_intercept"] = 0.0
        features["power_residual"] = 1.0

    # ── Polynomial fit coefficients (2nd-order) ──
    if n_points >= 4:
        try:
            coeffs = np.polyfit(depth_norm, area_norm, min(3, n_points - 1))
            for i, c in enumerate(coeffs):
                features[f"poly_coeff_{i}"] = float(c)
            # Pad if < 4 coefficients
            for i in range(len(coeffs), 4):
                features[f"poly_coeff_{i}"] = 0.0
        except Exception:
            for i in range(4):
                features[f"poly_coeff_{i}"] = 0.0
    else:
        for i in range(4):
            features[f"poly_coeff_{i}"] = 0.0

    # ── Shape descriptors ──
    # Gini coefficient of area distribution (inequality)
    if n_points >= 3:
        sorted_a = np.sort(area_norm)
        n = len(sorted_a)
        cumulative = np.cumsum(sorted_a)
        features["gini_area"] = float(
            (2 * np.sum((np.arange(1, n + 1)) * sorted_a) / (n * np.sum(sorted_a))) - (n + 1) / n
        )
    else:
        features["gini_area"] = 0.0

    # Area at different elevation percentiles
    if n_points >= 5:
        for pct in [10, 25, 50, 75, 90]:
            idx = int(len(area_norm) * pct / 100)
            idx = min(idx, len(area_norm) - 1)
            features[f"area_pct_{pct}"] = float(area_norm[idx])
    else:
        for pct in [10, 25, 50, 75, 90]:
            features[f"area_pct_{pct}"] = 0.5

    return features


# ── Batch Processing ────────────────────────────────────────────────

def process_batch(file_paths: List[Path]) -> List[Dict]:
    """Process a batch of L1 CSV files and extract A-E features."""
    records = []
    for f in file_paths:
        try:
            hylak_id = int(f.stem.replace("_L1", ""))
            ae = pd.read_csv(f)
            if len(ae) < 3:
                continue

            elev = ae.iloc[:, 0].values
            area = ae.iloc[:, 1].values

            feats = extract_ae_features(elev, area)
            if feats is None:
                continue

            feats["hylak_id"] = hylak_id
            records.append(feats)

        except Exception:
            pass

    return records


def build_ae_feature_dataset(
    l1_dir: str,
    depths_path: str,
    qa_path: str,
    output_dir: str,
    batch_size: int = 10000,
) -> pd.DataFrame:
    """
    Build full A-E feature dataset for all 510K lakes.

    Processes L1 CSVs in batches of 10K to manage memory.
    Merges with depth labels and QA scores.
    Saves checkpoints every 50K lakes.
    """
    l1_path = Path(l1_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(l1_path.glob("*_L1.csv"))
    log.info(f"Found {len(csv_files):,} L1 A-E files")

    # Check for existing checkpoint
    checkpoint_files = sorted(out_path.glob("ae_features_checkpoint_*.parquet"))
    start_idx = 0
    all_records = []

    if checkpoint_files:
        last_ckpt = checkpoint_files[-1]
        log.info(f"Resuming from checkpoint: {last_ckpt}")
        prev_df = pd.read_parquet(last_ckpt)
        all_records = prev_df.to_dict("records")
        # Extract the batch number from filename
        start_idx = int(last_ckpt.stem.split("_")[-1]) * batch_size
        log.info(f"  Loaded {len(all_records):,} records, starting from index {start_idx:,}")
        del prev_df
        gc.collect()

    # Process in batches
    n_batches = (len(csv_files) - start_idx + batch_size - 1) // batch_size
    log.info(f"Processing {len(csv_files) - start_idx:,} files in {n_batches} batches")

    checkpoint_interval = 50000  # Save every 50K lakes
    next_checkpoint_at = len(all_records) + checkpoint_interval

    for batch_idx in range(n_batches):
        batch_start = start_idx + batch_idx * batch_size
        batch_end = min(batch_start + batch_size, len(csv_files))
        batch_files = csv_files[batch_start:batch_end]

        batch_records = process_batch(batch_files)
        all_records.extend(batch_records)

        if batch_idx % 10 == 0:
            log.info(f"  Batch {batch_idx + 1}/{n_batches}: "
                     f"{len(all_records):,} total records")

        # Checkpoint
        if len(all_records) >= next_checkpoint_at:
            ckpt_num = (batch_start + batch_size) // batch_size
            ckpt_path = out_path / f"ae_features_checkpoint_{ckpt_num}.parquet"
            pd.DataFrame(all_records).to_parquet(ckpt_path, index=False)
            log.info(f"  Checkpoint saved: {ckpt_path} ({len(all_records):,} records)")
            next_checkpoint_at += checkpoint_interval
            gc.collect()

    # Build dataframe
    df = pd.DataFrame(all_records)
    log.info(f"Extracted A-E features for {len(df):,} lakes")

    # Merge with depth labels
    log.info(f"Loading depth labels from {depths_path}")
    depths = pd.read_parquet(depths_path)
    df = df.merge(
        depths[["hylak_id", "max_depth_m", "lat", "lon", "min_elev_m", "max_elev_m"]],
        on="hylak_id", how="inner",
    )
    log.info(f"  After merging with depths: {len(df):,} lakes")

    # Merge with QA
    log.info(f"Loading QA from {qa_path}")
    qa = pd.read_csv(qa_path)
    qa = qa.rename(columns={"Hylak_id": "hylak_id"})
    df = df.merge(
        qa[["hylak_id", "QA_RMSE", "QA_NRMSE", "QA_Extrapolation",
            "Lake_area", "Slope_100", "NRMSE_model"]],
        on="hylak_id", how="left",
    )

    # Save full feature dataset
    feat_path = out_path / "ae_features_all.parquet"
    df.to_parquet(feat_path, index=False)
    log.info(f"Saved full A-E feature dataset: {feat_path} ({len(df):,} lakes)")

    return df


# ── Model Training ──────────────────────────────────────────────────

AE_FEATURE_COLS = [
    "n_ae_points", "elev_range_m", "max_area_m2", "min_area_m2", "area_ratio",
    "slope_mean", "slope_std", "slope_max", "slope_bottom", "slope_surface",
    "slope_ratio", "curvature_mean", "curvature_std", "convexity",
    "hypsometric_integral",
    "area_at_depth_25pct", "area_at_depth_50pct", "area_at_depth_75pct",
    "power_exponent", "power_intercept", "power_residual",
    "poly_coeff_0", "poly_coeff_1", "poly_coeff_2", "poly_coeff_3",
    "gini_area",
    "area_pct_10", "area_pct_25", "area_pct_50", "area_pct_75", "area_pct_90",
    "lat", "lon",  # geographic context
    "Lake_area", "Slope_100",  # from QA
]


def train_ae_models(df: pd.DataFrame, output_dir: str) -> Dict:
    """
    Train models to predict max depth from A-E curve features.

    Models:
      1. Baseline: elev_range = max_depth (identity)
      2. XGBoost on A-E shape features
      3. LightGBM on A-E shape features
      4. Ensemble average
    """
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split

    out_path = Path(output_dir)

    # Filter to high-quality lakes
    # QA_RMSE: 0=good, 1=moderate, 2=poor
    # Need at least 5 A-E points for meaningful shape
    mask = (
        (df["n_ae_points"] >= 5) &
        (df["max_depth_m"] > 0.1) &
        (df["max_depth_m"] < 500) &
        (df["QA_RMSE"].fillna(2) <= 1)  # good or moderate quality
    )
    df_train = df[mask].copy()
    log.info(f"Training data after QA filter: {len(df_train):,} lakes "
             f"(from {len(df):,})")

    # Target: log-transformed depth for better distribution
    y = np.log1p(df_train["max_depth_m"].values)

    # Features
    X = df_train[AE_FEATURE_COLS].copy()
    X = X.fillna(0).replace([np.inf, -np.inf], 0)

    # Split: 80% train, 10% val, 10% test
    X_trainval, X_test, y_trainval, y_test = train_test_split(
        X, y, test_size=0.1, random_state=42,
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_trainval, y_trainval, test_size=0.111, random_state=42,
    )

    log.info(f"Split: train={len(X_train):,} val={len(X_val):,} test={len(X_test):,}")

    results = {}

    # ── Baseline: elev_range as depth ──
    log.info("\n═══ Baseline: elevation range = depth ═══")
    baseline_pred = np.log1p(X_test["elev_range_m"].values)
    baseline_rmse = np.sqrt(mean_squared_error(
        np.expm1(y_test), np.expm1(baseline_pred),
    ))
    baseline_mae = mean_absolute_error(np.expm1(y_test), np.expm1(baseline_pred))
    baseline_r2 = r2_score(y_test, baseline_pred)
    log.info(f"  Baseline RMSE: {baseline_rmse:.3f}m")
    log.info(f"  Baseline MAE:  {baseline_mae:.3f}m")
    log.info(f"  Baseline R²:   {baseline_r2:.4f}")
    results["baseline"] = {"rmse": baseline_rmse, "mae": baseline_mae, "r2": baseline_r2}

    # ── XGBoost ──
    log.info("\n═══ XGBoost on A-E Features ═══")
    try:
        import xgboost as xgb

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)
        dtest = xgb.DMatrix(X_test, label=y_test)

        params = {
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
            "max_depth": 8,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 10,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "tree_method": "hist",
            "device": "cuda",
            "verbosity": 1,
        }

        xgb_model = xgb.train(
            params, dtrain,
            num_boost_round=2000,
            evals=[(dtrain, "train"), (dval, "val")],
            early_stopping_rounds=50,
            verbose_eval=100,
        )

        xgb_pred = xgb_model.predict(dtest)
        xgb_rmse = np.sqrt(mean_squared_error(np.expm1(y_test), np.expm1(xgb_pred)))
        xgb_mae = mean_absolute_error(np.expm1(y_test), np.expm1(xgb_pred))
        xgb_r2 = r2_score(y_test, xgb_pred)
        log.info(f"  XGBoost RMSE: {xgb_rmse:.3f}m")
        log.info(f"  XGBoost MAE:  {xgb_mae:.3f}m")
        log.info(f"  XGBoost R²:   {xgb_r2:.4f}")
        results["xgboost"] = {"rmse": xgb_rmse, "mae": xgb_mae, "r2": xgb_r2}

        # Save model
        xgb_model.save_model(str(out_path / "ae_xgboost.json"))

        # Feature importance
        imp = xgb_model.get_score(importance_type="gain")
        imp_sorted = sorted(imp.items(), key=lambda x: x[1], reverse=True)[:15]
        log.info("  Top features:")
        for feat, score in imp_sorted:
            log.info(f"    {feat}: {score:.1f}")

    except Exception as e:
        log.error(f"XGBoost failed: {e}")
        xgb_pred = baseline_pred
        results["xgboost"] = results["baseline"]

    # ── LightGBM ──
    log.info("\n═══ LightGBM on A-E Features ═══")
    try:
        import lightgbm as lgb

        lgb_train = lgb.Dataset(X_train, y_train)
        lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)

        lgb_params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": 127,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "min_data_in_leaf": 20,
            "lambda_l1": 0.1,
            "lambda_l2": 1.0,
            "device": "gpu",
            "verbose": -1,
        }

        lgb_model = lgb.train(
            lgb_params, lgb_train,
            num_boost_round=2000,
            valid_sets=[lgb_train, lgb_val],
            valid_names=["train", "val"],
            callbacks=[
                lgb.early_stopping(50),
                lgb.log_evaluation(100),
            ],
        )

        lgb_pred = lgb_model.predict(X_test)
        lgb_rmse = np.sqrt(mean_squared_error(np.expm1(y_test), np.expm1(lgb_pred)))
        lgb_mae = mean_absolute_error(np.expm1(y_test), np.expm1(lgb_pred))
        lgb_r2 = r2_score(y_test, lgb_pred)
        log.info(f"  LightGBM RMSE: {lgb_rmse:.3f}m")
        log.info(f"  LightGBM MAE:  {lgb_mae:.3f}m")
        log.info(f"  LightGBM R²:   {lgb_r2:.4f}")
        results["lightgbm"] = {"rmse": lgb_rmse, "mae": lgb_mae, "r2": lgb_r2}

        # Save model
        lgb_model.save_model(str(out_path / "ae_lightgbm.txt"))

    except Exception as e:
        log.error(f"LightGBM failed: {e}")
        lgb_pred = baseline_pred
        results["lightgbm"] = results["baseline"]

    # ── Ensemble ──
    log.info("\n═══ Ensemble (XGB + LGB average) ═══")
    ens_pred = (xgb_pred + lgb_pred) / 2.0
    ens_rmse = np.sqrt(mean_squared_error(np.expm1(y_test), np.expm1(ens_pred)))
    ens_mae = mean_absolute_error(np.expm1(y_test), np.expm1(ens_pred))
    ens_r2 = r2_score(y_test, ens_pred)
    log.info(f"  Ensemble RMSE: {ens_rmse:.3f}m")
    log.info(f"  Ensemble MAE:  {ens_mae:.3f}m")
    log.info(f"  Ensemble R²:   {ens_r2:.4f}")
    results["ensemble"] = {"rmse": ens_rmse, "mae": ens_mae, "r2": ens_r2}

    # ── Depth-stratified metrics ──
    log.info("\n═══ Depth-Stratified Metrics (Ensemble) ═══")
    test_depths = np.expm1(y_test)
    pred_depths = np.expm1(ens_pred)
    for lo, hi in [(0, 1), (1, 5), (5, 10), (10, 20), (20, 50), (50, 500)]:
        mask = (test_depths >= lo) & (test_depths < hi)
        if mask.sum() < 10:
            continue
        rmse_bin = np.sqrt(mean_squared_error(test_depths[mask], pred_depths[mask]))
        mae_bin = mean_absolute_error(test_depths[mask], pred_depths[mask])
        r2_bin = r2_score(test_depths[mask], pred_depths[mask])
        log.info(f"  [{lo:3d}-{hi:3d}m] n={mask.sum():6,}  "
                 f"RMSE={rmse_bin:.3f}m  MAE={mae_bin:.3f}m  R²={r2_bin:.4f}")

    # Save results
    with open(out_path / "ae_results.json", "w") as fp:
        json.dump(results, fp, indent=2)

    # Save feature list
    with open(out_path / "ae_feature_cols.json", "w") as fp:
        json.dump(AE_FEATURE_COLS, fp, indent=2)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Train A-E geometric bathymetry models on 3D-LAKES data",
    )
    parser.add_argument("--l1-dir", type=str, default="/data/3d_lakes_l1")
    parser.add_argument("--depths", type=str, default="/data/3d_lakes_with_depths.parquet")
    parser.add_argument("--qa", type=str, default="/data/3d_lakes_qa.csv")
    parser.add_argument("--output", type=str, default="/data/models/ae_geometric")
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip feature extraction, load from checkpoint")
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # Step 1: Extract A-E features from all 510K lakes
    feat_path = out_path / "ae_features_all.parquet"
    if args.skip_extract and feat_path.exists():
        log.info(f"Loading pre-extracted features from {feat_path}")
        df = pd.read_parquet(feat_path)
    else:
        df = build_ae_feature_dataset(
            args.l1_dir, args.depths, args.qa, args.output, args.batch_size,
        )

    log.info(f"Feature dataset: {df.shape}")
    log.info(f"Columns: {list(df.columns)}")

    # Step 2: Train models
    results = train_ae_models(df, args.output)

    elapsed = time.time() - t0
    log.info(f"\nTotal time: {elapsed / 60:.1f} minutes")
    log.info(f"Results saved to: {args.output}")

    # Summary
    log.info("\n" + "=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for name, metrics in results.items():
        log.info(f"  {name:12s}: RMSE={metrics['rmse']:.3f}m  "
                 f"MAE={metrics['mae']:.3f}m  R²={metrics['r2']:.4f}")


if __name__ == "__main__":
    main()
