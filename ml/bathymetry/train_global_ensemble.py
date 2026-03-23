#!/usr/bin/env python3
"""
OpenCatch — Train Multi-Model Ensemble on 510K Global Lake Dataset

Trains four model types on the global 3D-LAKES dataset:

  1. Morphometric model: area + perimeter + shape → depth (GLOBathy approach)
  2. A-E Geometric model: area-elevation curve features → depth
  3. Combined model: morphometric + A-E + geographic → depth
  4. Stacked ensemble: meta-learner on model predictions → depth

Each model is XGBoost + LightGBM, ensembled. The stacked ensemble
uses a ridge meta-learner on OOF predictions from all base models.

Evaluation:
  - 80/10/10 train/val/test split
  - Depth-stratified metrics
  - QA-stratified metrics (high/medium/low quality)
  - Geographic cross-validation (continent holdout)

Usage:
    python train_global_ensemble.py \
        --data /data/training/global_510k.parquet \
        --output /data/models/global_ensemble \
        --device cuda

Requirements:
    pip install xgboost lightgbm scikit-learn pandas numpy pyarrow tqdm
"""

import argparse
import gc
import json
import logging
import pickle
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, train_test_split
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("train_global")


# ── Feature Groups ──────────────────────────────────────────────────

MORPHOMETRIC_FEATURES = [
    "area_km2", "log_area_km2", "equiv_radius_m", "approx_perimeter_m",
    "shape_factor", "volume_dev", "mean_depth_m", "relative_depth",
    "depth_area_ratio",
]

AE_FEATURES = [
    "n_ae_points", "elev_range_m", "max_area_m2", "min_area_m2", "area_ratio",
    "slope_mean", "slope_std", "slope_max",
    "curvature_mean", "convexity",
    "hypsometric_integral",
    "area_at_d25", "area_at_d50", "area_at_d75",
    "power_exponent",
]

GEO_FEATURES = [
    "lat", "lon", "abs_lat", "climate_zone", "continent",
    "min_elev_m", "max_elev_m",
]

QA_FEATURES = [
    "qa_rmse", "qa_nrmse", "qa_extrap", "qa_slope_100",
]

ALL_FEATURES = MORPHOMETRIC_FEATURES + AE_FEATURES + GEO_FEATURES + QA_FEATURES


def evaluate_model(y_true: np.ndarray, y_pred: np.ndarray, name: str) -> Dict:
    """Evaluate predictions in original depth scale."""
    # Convert from log to original
    true_m = np.expm1(y_true)
    pred_m = np.expm1(y_pred)

    # Clip negative predictions
    pred_m = np.clip(pred_m, 0, None)

    rmse = np.sqrt(mean_squared_error(true_m, pred_m))
    mae = mean_absolute_error(true_m, pred_m)
    r2 = r2_score(y_true, y_pred)
    r2_orig = r2_score(true_m, pred_m)

    # Median absolute error
    medae = np.median(np.abs(true_m - pred_m))

    log.info(f"  {name}: RMSE={rmse:.3f}m  MAE={mae:.3f}m  "
             f"MedAE={medae:.3f}m  R²={r2:.4f}  R²(orig)={r2_orig:.4f}")

    return {
        "rmse": float(rmse), "mae": float(mae), "medae": float(medae),
        "r2_log": float(r2), "r2_orig": float(r2_orig),
    }


def depth_stratified_eval(
    y_true: np.ndarray, y_pred: np.ndarray, name: str,
) -> Dict:
    """Evaluate by depth bins."""
    true_m = np.expm1(y_true)
    pred_m = np.expm1(np.clip(y_pred, -1, 10))

    log.info(f"\n  {name} — Depth-stratified:")
    bins = {}
    for lo, hi in [(0, 1), (1, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 700)]:
        mask = (true_m >= lo) & (true_m < hi)
        n = mask.sum()
        if n < 10:
            continue
        rmse = np.sqrt(mean_squared_error(true_m[mask], pred_m[mask]))
        mae = mean_absolute_error(true_m[mask], pred_m[mask])
        r2 = r2_score(true_m[mask], pred_m[mask]) if n > 1 else 0
        log.info(f"    [{lo:4d}-{hi:4d}m] n={n:7,}  "
                 f"RMSE={rmse:.3f}m  MAE={mae:.3f}m  R²={r2:.4f}")
        bins[f"{lo}-{hi}m"] = {"n": int(n), "rmse": float(rmse), "mae": float(mae), "r2": float(r2)}

    return bins


def train_xgb(X_train, y_train, X_val, y_val, params_override=None):
    """Train an XGBoost model."""
    import xgboost as xgb

    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)

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
        "verbosity": 0,
    }
    if params_override:
        params.update(params_override)

    model = xgb.train(
        params, dtrain,
        num_boost_round=2000,
        evals=[(dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=0,
    )
    return model


def train_lgb(X_train, y_train, X_val, y_val, params_override=None):
    """Train a LightGBM model."""
    import lightgbm as lgb

    dtrain = lgb.Dataset(X_train, y_train)
    dval = lgb.Dataset(X_val, y_val, reference=dtrain)

    params = {
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
    if params_override:
        params.update(params_override)

    model = lgb.train(
        params, dtrain,
        num_boost_round=2000,
        valid_sets=[dval],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(50)],
    )
    return model


def predict_xgb(model, X):
    import xgboost as xgb
    return model.predict(xgb.DMatrix(X))


def predict_lgb(model, X):
    return model.predict(X)


def main():
    parser = argparse.ArgumentParser(
        description="Train global ensemble on 510K lake dataset",
    )
    parser.add_argument("--data", type=str, default="/data/training/global_510k.parquet")
    parser.add_argument("--output", type=str, default="/data/models/global_ensemble")
    parser.add_argument("--device", type=str, default="cuda")
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # ── Load data ──
    log.info(f"Loading data from {args.data}")
    df = pd.read_parquet(args.data)
    log.info(f"  Shape: {df.shape}")

    # Filter
    mask = (
        (df["max_depth_m"] > 0.05) &
        (df["max_depth_m"] < 700) &
        (df["n_ae_points"] >= 3)
    )
    df = df[mask].reset_index(drop=True)
    log.info(f"  After filtering: {len(df):,} lakes")

    # Target
    y = df["log_depth"].values
    if "log_depth" not in df.columns:
        y = np.log1p(df["max_depth_m"].values)

    # ── Split ──
    indices = np.arange(len(df))
    train_val_idx, test_idx = train_test_split(indices, test_size=0.1, random_state=42)
    train_idx, val_idx = train_test_split(train_val_idx, test_size=0.111, random_state=42)

    log.info(f"  Split: train={len(train_idx):,} val={len(val_idx):,} test={len(test_idx):,}")

    results = {}

    # ── Model 1: Morphometric Only (GLOBathy approach) ──
    log.info("\n" + "=" * 60)
    log.info("MODEL 1: Morphometric (GLOBathy approach)")
    log.info("=" * 60)

    morph_cols = [c for c in MORPHOMETRIC_FEATURES if c in df.columns]
    log.info(f"  Features: {morph_cols}")

    X_morph = df[morph_cols].fillna(0).replace([np.inf, -np.inf], 0)

    xgb_morph = train_xgb(
        X_morph.iloc[train_idx], y[train_idx],
        X_morph.iloc[val_idx], y[val_idx],
    )
    lgb_morph = train_lgb(
        X_morph.iloc[train_idx], y[train_idx],
        X_morph.iloc[val_idx], y[val_idx],
    )

    pred_morph_test = (
        predict_xgb(xgb_morph, X_morph.iloc[test_idx]) +
        predict_lgb(lgb_morph, X_morph.iloc[test_idx])
    ) / 2.0

    results["morphometric"] = evaluate_model(y[test_idx], pred_morph_test, "Morphometric")
    depth_stratified_eval(y[test_idx], pred_morph_test, "Morphometric")

    # Save
    xgb_morph.save_model(str(out_path / "morph_xgb.json"))
    lgb_morph.save_model(str(out_path / "morph_lgb.txt"))

    # ── Model 2: A-E Geometric Only ──
    log.info("\n" + "=" * 60)
    log.info("MODEL 2: A-E Geometric")
    log.info("=" * 60)

    ae_cols = [c for c in AE_FEATURES if c in df.columns]
    log.info(f"  Features: {ae_cols}")

    X_ae = df[ae_cols].fillna(0).replace([np.inf, -np.inf], 0)

    xgb_ae = train_xgb(
        X_ae.iloc[train_idx], y[train_idx],
        X_ae.iloc[val_idx], y[val_idx],
    )
    lgb_ae = train_lgb(
        X_ae.iloc[train_idx], y[train_idx],
        X_ae.iloc[val_idx], y[val_idx],
    )

    pred_ae_test = (
        predict_xgb(xgb_ae, X_ae.iloc[test_idx]) +
        predict_lgb(lgb_ae, X_ae.iloc[test_idx])
    ) / 2.0

    results["ae_geometric"] = evaluate_model(y[test_idx], pred_ae_test, "A-E Geometric")
    depth_stratified_eval(y[test_idx], pred_ae_test, "A-E Geometric")

    xgb_ae.save_model(str(out_path / "ae_xgb.json"))
    lgb_ae.save_model(str(out_path / "ae_lgb.txt"))

    # ── Model 3: Combined (all features) ──
    log.info("\n" + "=" * 60)
    log.info("MODEL 3: Combined (Morph + A-E + Geo + QA)")
    log.info("=" * 60)

    all_cols = [c for c in ALL_FEATURES if c in df.columns]
    log.info(f"  Features ({len(all_cols)}): {all_cols}")

    X_all = df[all_cols].fillna(0).replace([np.inf, -np.inf], 0)

    xgb_all = train_xgb(
        X_all.iloc[train_idx], y[train_idx],
        X_all.iloc[val_idx], y[val_idx],
    )
    lgb_all = train_lgb(
        X_all.iloc[train_idx], y[train_idx],
        X_all.iloc[val_idx], y[val_idx],
    )

    pred_all_test = (
        predict_xgb(xgb_all, X_all.iloc[test_idx]) +
        predict_lgb(lgb_all, X_all.iloc[test_idx])
    ) / 2.0

    results["combined"] = evaluate_model(y[test_idx], pred_all_test, "Combined")
    depth_stratified_eval(y[test_idx], pred_all_test, "Combined")

    xgb_all.save_model(str(out_path / "combined_xgb.json"))
    lgb_all.save_model(str(out_path / "combined_lgb.txt"))

    # Feature importance from combined XGBoost
    log.info("\n  Top-15 features (XGBoost gain):")
    imp = xgb_all.get_score(importance_type="gain")
    for feat, score in sorted(imp.items(), key=lambda x: x[1], reverse=True)[:15]:
        log.info(f"    {feat}: {score:.1f}")

    # ── Model 4: Stacked Ensemble ──
    log.info("\n" + "=" * 60)
    log.info("MODEL 4: Stacked Ensemble (Ridge meta-learner)")
    log.info("=" * 60)

    # Generate OOF predictions for stacking
    log.info("  Generating 5-fold OOF predictions...")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    oof_morph = np.zeros(len(train_val_idx))
    oof_ae = np.zeros(len(train_val_idx))
    oof_all = np.zeros(len(train_val_idx))

    X_morph_tv = X_morph.iloc[train_val_idx]
    X_ae_tv = X_ae.iloc[train_val_idx]
    X_all_tv = X_all.iloc[train_val_idx]
    y_tv = y[train_val_idx]

    for fold, (tr, va) in enumerate(kf.split(X_morph_tv)):
        log.info(f"  Fold {fold + 1}/5...")

        # Morph
        m = train_xgb(X_morph_tv.iloc[tr], y_tv[tr], X_morph_tv.iloc[va], y_tv[va])
        oof_morph[va] = predict_xgb(m, X_morph_tv.iloc[va])

        # AE
        m = train_xgb(X_ae_tv.iloc[tr], y_tv[tr], X_ae_tv.iloc[va], y_tv[va])
        oof_ae[va] = predict_xgb(m, X_ae_tv.iloc[va])

        # All
        m = train_xgb(X_all_tv.iloc[tr], y_tv[tr], X_all_tv.iloc[va], y_tv[va])
        oof_all[va] = predict_xgb(m, X_all_tv.iloc[va])

        gc.collect()

    # Train meta-learner
    meta_X_train = np.column_stack([oof_morph, oof_ae, oof_all])
    meta_X_test = np.column_stack([pred_morph_test, pred_ae_test, pred_all_test])

    scaler = StandardScaler()
    meta_X_train_s = scaler.fit_transform(meta_X_train)
    meta_X_test_s = scaler.transform(meta_X_test)

    meta_model = Ridge(alpha=1.0)
    meta_model.fit(meta_X_train_s, y_tv)

    log.info(f"  Meta-learner weights: {meta_model.coef_}")

    pred_stacked = meta_model.predict(meta_X_test_s)
    results["stacked_ensemble"] = evaluate_model(
        y[test_idx], pred_stacked, "Stacked Ensemble",
    )
    stacked_bins = depth_stratified_eval(
        y[test_idx], pred_stacked, "Stacked Ensemble",
    )

    # Save meta-learner
    with open(out_path / "meta_learner.pkl", "wb") as fp:
        pickle.dump({"model": meta_model, "scaler": scaler}, fp)

    # ── Baseline comparison ──
    log.info("\n" + "=" * 60)
    log.info("BASELINE: elevation range = depth")
    log.info("=" * 60)
    baseline_pred = np.log1p(df["elev_range_m"].iloc[test_idx].values)
    results["baseline_elev_range"] = evaluate_model(
        y[test_idx], baseline_pred, "Baseline (elev range)",
    )

    # ── Geographic Cross-Val ──
    log.info("\n" + "=" * 60)
    log.info("GEOGRAPHIC CROSS-VALIDATION (Continent Holdout)")
    log.info("=" * 60)

    if "continent" in df.columns:
        continent_names = {0: "Americas", 1: "Europe/Africa", 2: "Asia/Oceania"}
        for holdout_cont in sorted(df["continent"].unique()):
            if holdout_cont > 2:
                continue
            ho_mask = df["continent"] == holdout_cont
            tr_mask = ~ho_mask
            if ho_mask.sum() < 100:
                continue

            m = train_xgb(
                X_all[tr_mask], y[tr_mask],
                X_all[ho_mask][:1000], y[ho_mask][:1000],  # small val from holdout
            )
            pred_ho = predict_xgb(m, X_all[ho_mask])
            name = continent_names.get(holdout_cont, f"Cont-{holdout_cont}")
            evaluate_model(y[ho_mask], pred_ho, f"Holdout: {name}")

    # ── QA-Stratified Results ──
    log.info("\n" + "=" * 60)
    log.info("QA-STRATIFIED RESULTS (Stacked Ensemble)")
    log.info("=" * 60)

    test_df = df.iloc[test_idx]
    for qa_val, qa_name in [(0, "Good"), (1, "Moderate"), (2, "Poor")]:
        qa_mask = test_df["qa_rmse"].values == qa_val
        if qa_mask.sum() < 10:
            continue
        evaluate_model(
            y[test_idx][qa_mask], pred_stacked[qa_mask],
            f"QA={qa_name} (n={qa_mask.sum():,})",
        )

    # ── Save all results ──
    with open(out_path / "results.json", "w") as fp:
        json.dump(results, fp, indent=2, default=str)

    # Save feature lists
    with open(out_path / "feature_config.json", "w") as fp:
        json.dump({
            "morphometric": morph_cols,
            "ae_geometric": ae_cols,
            "combined": all_cols,
        }, fp, indent=2)

    # ── Summary ──
    elapsed = time.time() - t0
    log.info("\n" + "=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for name, metrics in sorted(results.items(), key=lambda x: x[1].get("r2_log", 0), reverse=True):
        log.info(f"  {name:20s}: RMSE={metrics['rmse']:.3f}m  "
                 f"MAE={metrics['mae']:.3f}m  "
                 f"R²={metrics.get('r2_log', metrics.get('r2', 0)):.4f}")
    log.info(f"\nTotal time: {elapsed / 60:.1f} minutes")
    log.info(f"Models saved to: {args.output}")


if __name__ == "__main__":
    main()
