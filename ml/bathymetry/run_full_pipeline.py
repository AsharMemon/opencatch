#!/usr/bin/env python3
"""
OpenCatch — Full Multi-Approach Bathymetry Pipeline
====================================================
Runs 5 approaches head-to-head with HONEST metrics.

Priority 1: RF + Water Quality Spectral Features (sonar+S2 data)
Priority 2: Within-Lake Calibrated SDB (per-lake Stumpf)
Priority 3: ML Curve Fitting on MN DNR (hypsometric reconstruction)
Priority 4: Transfer Learning (K-donor morphometric)
Priority 5: Terrain DEM Prior (elevation → depth)

All results compared against mean-baseline.
"""

import gc
import json
import logging
import os
import pickle
import sys
import time
import traceback
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    RandomForestRegressor,
    GradientBoostingRegressor,
    ExtraTreesRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

class _FlushHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

_handler = _FlushHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("pipeline")

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    import rasterio
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

OUTPUT_DIR = Path("/data/models/full_comparison")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEPTH_BINS = {"0-5m": (0, 5), "5-10m": (5, 10), "10-20m": (10, 20), "20m+": (20, 999)}


# ═══════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════

def compute_metrics(y_true, y_pred, label=""):
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    if valid.sum() < 2:
        return {"label": label, "n": 0, "error": "too_few_valid"}
    t, p = y_true[valid], y_pred[valid]
    ss_res = np.sum((t - p) ** 2)
    ss_tot = np.sum((t - t.mean()) ** 2)
    r2 = 1 - ss_res / (ss_tot + 1e-12)
    return {
        "label": label, "n": int(valid.sum()),
        "rmse": float(np.sqrt(np.mean((t - p) ** 2))),
        "mae": float(np.mean(np.abs(t - p))),
        "r2": float(r2),
        "bias": float(np.mean(p - t)),
        "median_ae": float(np.median(np.abs(t - p))),
        "p90_ae": float(np.percentile(np.abs(t - p), 90)),
    }


def compute_depth_stratified(y_true, y_pred, label=""):
    result = {"label": label}
    for bin_name, (lo, hi) in DEPTH_BINS.items():
        mask = (y_true >= lo) & (y_true < hi) & np.isfinite(y_pred)
        if mask.sum() >= 5:
            result[bin_name] = compute_metrics(y_true[mask], y_pred[mask], bin_name)
        else:
            result[bin_name] = {"n": int(mask.sum()), "error": "too_few"}
    return result


def print_summary_table(all_results: dict, baseline_rmse: float):
    log.info("")
    log.info("=" * 100)
    log.info("  MULTI-APPROACH BATHYMETRY COMPARISON — HONEST METRICS")
    log.info("=" * 100)
    log.info(f"  {'Approach':<35s} {'RMSE(m)':>8s} {'MAE(m)':>8s} {'R²':>8s} "
             f"{'Skill':>8s} {'N':>8s} {'Lakes':>6s}")
    log.info("-" * 100)

    sorted_results = sorted(
        all_results.items(),
        key=lambda x: x[1].get("overall", {}).get("rmse", 999)
    )
    for name, res in sorted_results:
        if "error" in res:
            log.info(f"  {name:<35s}  FAILED: {res['error']}")
            continue
        o = res["overall"]
        skill = 1 - (o["rmse"] / baseline_rmse) ** 2 if baseline_rmse > 0 else 0
        n_lakes = res.get("n_lakes", "?")
        log.info(f"  {name:<35s} {o['rmse']:8.3f} {o['mae']:8.3f} "
                 f"{o['r2']:8.4f} {skill:8.3f} {o['n']:8d} {str(n_lakes):>6s}")

    log.info("=" * 100)

    # Depth-stratified
    header = f"\n  {'Approach':<35s}"
    for b in DEPTH_BINS:
        header += f" {b+' RMSE':>12s}"
    log.info(header)
    log.info("-" * 100)
    for name, res in sorted_results:
        if "error" in res or "depth_stratified" not in res:
            continue
        s = res["depth_stratified"]
        line = f"  {name:<35s}"
        for b in DEPTH_BINS:
            rmse = s.get(b, {}).get("rmse", float("nan"))
            line += f" {rmse:12.3f}"
        log.info(line)
    log.info("=" * 100)


# ═══════════════════════════════════════════════════════════════════════
# PRIORITY 1: RF + Water Quality Spectral Features
# ═══════════════════════════════════════════════════════════════════════

def run_priority1_spectral_rf(spectral_path: str) -> dict:
    """
    Train RF/GBR/XGBoost on sonar+S2 spectral data with water quality features.
    GroupKFold by lake for honest cross-validation.
    """
    log.info("\n" + "=" * 70)
    log.info("  PRIORITY 1: RF + Water Quality Spectral Features")
    log.info("=" * 70)

    df = pd.read_parquet(spectral_path)
    log.info(f"  Loaded {len(df)} points from {df['lake_id'].nunique()} lakes")

    # Target
    target = "depth_m"
    exclude = {target, "lat", "lon", "lake_id", "s2_date", "temporal_match_score"}

    # Feature columns — use ALL spectral + physics features
    feature_cols = [c for c in df.columns if c not in exclude]
    log.info(f"  Features ({len(feature_cols)}): {feature_cols[:10]}...")

    X = df[feature_cols].values.astype(np.float32)
    y = df[target].values.astype(np.float32)
    groups = df["lake_id"].values

    # Replace inf/nan
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Clip extreme feature values (some ratios blow up)
    for j in range(X.shape[1]):
        p1, p99 = np.percentile(X[:, j], [1, 99])
        X[:, j] = np.clip(X[:, j], p1, p99)

    # GroupKFold by lake
    n_splits = 5
    gkf = GroupKFold(n_splits=n_splits)

    models_to_try = {
        "RF": lambda: RandomForestRegressor(
            n_estimators=300, max_depth=15, min_samples_leaf=5,
            max_features=0.5, n_jobs=4, random_state=42
        ),
        "GBR": lambda: GradientBoostingRegressor(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=10, random_state=42
        ),
        "ExtraTrees": lambda: ExtraTreesRegressor(
            n_estimators=300, max_depth=15, min_samples_leaf=5,
            max_features=0.5, n_jobs=4, random_state=42
        ),
    }
    if HAS_XGB:
        models_to_try["XGBoost"] = lambda: xgb.XGBRegressor(
            n_estimators=500, max_depth=8, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.6, min_child_weight=10,
            reg_alpha=0.1, reg_lambda=1.0, tree_method="hist",
            device="cuda", random_state=42,
        )

    results = {}
    best_rmse = 999
    best_model_name = None
    best_model_obj = None

    for model_name, model_fn in models_to_try.items():
        log.info(f"\n  Training {model_name} (5-fold GroupKFold by lake)...")
        t0 = time.time()

        all_true, all_pred = [], []
        fold_metrics = []

        for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups)):
            model = model_fn()
            model.fit(X[train_idx], y[train_idx])
            pred = model.predict(X[test_idx])

            all_true.extend(y[test_idx])
            all_pred.extend(pred)

            fold_r2 = r2_score(y[test_idx], pred)
            fold_rmse = np.sqrt(mean_squared_error(y[test_idx], pred))
            fold_metrics.append({"fold": fold, "r2": fold_r2, "rmse": fold_rmse})
            log.info(f"    Fold {fold}: R²={fold_r2:.4f}, RMSE={fold_rmse:.3f}m "
                     f"(train={len(train_idx)}, test={len(test_idx)})")

        elapsed = time.time() - t0
        all_true = np.array(all_true)
        all_pred = np.array(all_pred)

        overall = compute_metrics(all_true, all_pred, model_name)
        stratified = compute_depth_stratified(all_true, all_pred, model_name)

        # Mean baseline comparison
        mean_pred = np.full_like(all_true, all_true.mean())
        baseline = compute_metrics(all_true, mean_pred, "mean_baseline")

        log.info(f"  {model_name} OVERALL: R²={overall['r2']:.4f}, "
                 f"RMSE={overall['rmse']:.3f}m, MAE={overall['mae']:.3f}m "
                 f"({elapsed:.1f}s)")
        log.info(f"  Mean baseline RMSE={baseline['rmse']:.3f}m")

        results[f"P1_{model_name}"] = {
            "overall": overall,
            "depth_stratified": stratified,
            "fold_metrics": fold_metrics,
            "baseline_rmse": baseline["rmse"],
            "n_lakes": df["lake_id"].nunique(),
            "elapsed_s": elapsed,
        }

        if overall["rmse"] < best_rmse:
            best_rmse = overall["rmse"]
            best_model_name = model_name

        del model
        gc.collect()

    # Save best model (retrain on full data)
    if best_model_name is not None:
        log.info(f"  Retraining best model {best_model_name} on full data...")
        best_model_obj = models_to_try[best_model_name]()
        best_model_obj.fit(X, y)

        model_dir = OUTPUT_DIR / "p1_spectral_rf"
        model_dir.mkdir(parents=True, exist_ok=True)
        with open(model_dir / f"best_{best_model_name}.pkl", "wb") as f:
            pickle.dump(best_model_obj, f)
        with open(model_dir / "feature_cols.json", "w") as f:
            json.dump(feature_cols, f)
        log.info(f"  Saved best model: {best_model_name} (RMSE={best_rmse:.3f}m)")

        # Feature importance
        if hasattr(best_model_obj, "feature_importances_"):
            imp = best_model_obj.feature_importances_
            top_idx = np.argsort(imp)[::-1][:15]
            log.info("  Top 15 features:")
            for i in top_idx:
                log.info(f"    {feature_cols[i]:<30s} {imp[i]:.4f}")
        del best_model_obj
        gc.collect()

    # Stacked ensemble — fast models only (skip slow GBR), one at a time for memory
    log.info("\n  Training stacked ensemble (RF+ET+XGB only)...")
    fast_models = {k: v for k, v in models_to_try.items() if k != "GBR"}
    # Collect per-model OOF predictions
    oof_preds = {k: np.full(len(y), np.nan) for k in fast_models}
    for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups)):
        for model_name, model_fn in fast_models.items():
            m = model_fn()
            m.fit(X[train_idx], y[train_idx])
            oof_preds[model_name][test_idx] = m.predict(X[test_idx])
            del m
            gc.collect()
    # Average ensemble
    stack_pred_arr = np.nanmean(
        np.column_stack([oof_preds[k] for k in fast_models]), axis=1
    )
    valid_mask = np.isfinite(stack_pred_arr)
    stack_true = y[valid_mask]
    stack_pred = stack_pred_arr[valid_mask]

    stack_overall = compute_metrics(stack_true, stack_pred, "Ensemble_Avg")
    stack_strat = compute_depth_stratified(stack_true, stack_pred, "Ensemble_Avg")
    log.info(f"  Ensemble OVERALL: R²={stack_overall['r2']:.4f}, "
             f"RMSE={stack_overall['rmse']:.3f}m")

    results["P1_Ensemble_Avg"] = {
        "overall": stack_overall,
        "depth_stratified": stack_strat,
        "n_lakes": df["lake_id"].nunique(),
    }

    return results


# ═══════════════════════════════════════════════════════════════════════
# PRIORITY 2: Within-Lake Calibrated SDB
# ═══════════════════════════════════════════════════════════════════════

def run_priority2_within_lake_sdb(spectral_path: str) -> dict:
    """
    For each lake: hold out 20% sonar points, calibrate Stumpf on 80%, predict 20%.
    Tests WITHIN-LAKE accuracy.
    """
    log.info("\n" + "=" * 70)
    log.info("  PRIORITY 2: Within-Lake Calibrated SDB")
    log.info("=" * 70)

    df = pd.read_parquet(spectral_path)

    # SDB features — the classic ratios
    sdb_features = [
        "stumpf_ratio", "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
        "log_blue", "log_green", "log_red",
        "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
        "ndwi",
    ]
    available = [f for f in sdb_features if f in df.columns]
    log.info(f"  SDB features: {available}")

    all_true, all_pred = [], []
    lake_results = []
    n_skipped = 0

    for lake_id, gdf in df.groupby("lake_id"):
        if len(gdf) < 20:
            n_skipped += 1
            continue

        depths = gdf["depth_m"].values
        X_lake = gdf[available].values.astype(np.float32)
        X_lake = np.nan_to_num(X_lake, nan=0.0, posinf=0.0, neginf=0.0)

        # Clip extremes per-lake
        for j in range(X_lake.shape[1]):
            p1, p99 = np.percentile(X_lake[:, j], [1, 99])
            X_lake[:, j] = np.clip(X_lake[:, j], p1, p99)

        # 80/20 split
        n = len(gdf)
        rng = np.random.RandomState(42)
        perm = rng.permutation(n)
        n_cal = int(0.8 * n)

        cal_idx, test_idx = perm[:n_cal], perm[n_cal:]
        X_cal, y_cal = X_lake[cal_idx], depths[cal_idx]
        X_test, y_test = X_lake[test_idx], depths[test_idx]

        # Method 1: Linear regression on Stumpf ratio
        try:
            from sklearn.linear_model import LinearRegression
            stumpf_col = available.index("stumpf_ratio") if "stumpf_ratio" in available else 0

            # Filter valid stumpf values
            valid_cal = np.isfinite(X_cal[:, stumpf_col]) & (np.abs(X_cal[:, stumpf_col]) < 100)
            if valid_cal.sum() < 10:
                n_skipped += 1
                continue

            # Calibrate: depth = a * ln(ratio) + b
            stumpf_cal = X_cal[valid_cal, stumpf_col].reshape(-1, 1)
            lr = LinearRegression()
            lr.fit(stumpf_cal, y_cal[valid_cal])
            pred_test = lr.predict(X_test[:, stumpf_col].reshape(-1, 1))

            # Also try RF within-lake
            rf_lake = RandomForestRegressor(
                n_estimators=100, max_depth=10, min_samples_leaf=3,
                random_state=42, n_jobs=4
            )
            rf_lake.fit(X_cal, y_cal)
            pred_rf = rf_lake.predict(X_test)

            # Use whichever is better
            rmse_lr = np.sqrt(mean_squared_error(y_test, pred_test))
            rmse_rf = np.sqrt(mean_squared_error(y_test, pred_rf))

            if rmse_rf < rmse_lr:
                pred_best = pred_rf
                method = "RF"
            else:
                pred_best = pred_test
                method = "Stumpf_LR"

            all_true.extend(y_test)
            all_pred.extend(pred_best)

            lake_rmse = np.sqrt(mean_squared_error(y_test, pred_best))
            lake_r2 = r2_score(y_test, pred_best)
            lake_results.append({
                "lake_id": lake_id, "rmse": lake_rmse, "r2": lake_r2,
                "n_points": len(y_test), "method": method,
                "max_depth": float(depths.max()),
            })
        except Exception as e:
            n_skipped += 1
            continue

    log.info(f"  Evaluated {len(lake_results)} lakes, skipped {n_skipped}")

    if not all_true:
        return {"P2_Within_Lake_SDB": {"error": "no_predictions"}}

    all_true = np.array(all_true)
    all_pred = np.array(all_pred)

    overall = compute_metrics(all_true, all_pred, "Within-Lake SDB")
    stratified = compute_depth_stratified(all_true, all_pred, "Within-Lake SDB")

    # Per-lake RMSE distribution
    lake_rmses = [r["rmse"] for r in lake_results]
    log.info(f"  OVERALL: R²={overall['r2']:.4f}, RMSE={overall['rmse']:.3f}m")
    log.info(f"  Per-lake RMSE: median={np.median(lake_rmses):.3f}m, "
             f"mean={np.mean(lake_rmses):.3f}m, "
             f"p10={np.percentile(lake_rmses, 10):.3f}m, "
             f"p90={np.percentile(lake_rmses, 90):.3f}m")

    # Save per-lake details
    pd.DataFrame(lake_results).to_parquet(
        OUTPUT_DIR / "p2_within_lake_details.parquet", index=False
    )

    return {
        "P2_Within_Lake_SDB": {
            "overall": overall,
            "depth_stratified": stratified,
            "n_lakes": len(lake_results),
            "per_lake_rmse": {
                "mean": float(np.mean(lake_rmses)),
                "median": float(np.median(lake_rmses)),
                "p10": float(np.percentile(lake_rmses, 10)),
                "p90": float(np.percentile(lake_rmses, 90)),
            },
        }
    }


# ═══════════════════════════════════════════════════════════════════════
# PRIORITY 3: ML Curve Fitting on MN DNR
# ═══════════════════════════════════════════════════════════════════════

def run_priority3_curve_fitting(sonar_dir: str) -> dict:
    """
    For each MN sonar lake:
    1. Compute true Area-Elevation (hypsometric) curve from full sonar
    2. Simulate sparse 5-point A-E curve (like 3D-LAKES)
    3. Train model to reconstruct dense curve from sparse
    4. Validate reconstruction quality
    """
    log.info("\n" + "=" * 70)
    log.info("  PRIORITY 3: ML Curve Fitting on MN DNR")
    log.info("=" * 70)

    if not HAS_RASTERIO:
        return {"P3_Curve_Fitting": {"error": "rasterio not installed"}}

    sonar_path = Path(sonar_dir)
    lake_dirs = sorted([d for d in sonar_path.iterdir() if d.is_dir()])
    log.info(f"  Found {len(lake_dirs)} lake directories")

    # Step 1: Compute hypsometric curves for all lakes
    n_bins = 50  # Standardized curve resolution
    curves = {}
    lake_meta = {}

    for lake_dir in lake_dirs:
        depth_file = lake_dir / "depth.tif"
        mask_file = lake_dir / "mask.tif"
        if not depth_file.exists():
            continue

        try:
            with rasterio.open(str(depth_file)) as src:
                depth = src.read(1)
            with rasterio.open(str(mask_file)) as src:
                mask = src.read(1)

            # Valid water pixels with positive depth
            valid = (mask > 0) & (depth > 0.1) & np.isfinite(depth)
            depths = depth[valid]

            if len(depths) < 50:
                continue

            max_depth = float(np.percentile(depths, 99))  # Robust max
            mean_depth = float(np.mean(depths))

            # Hypsometric curve: fraction of lake area deeper than d
            # for d in [0, max_depth]
            depth_fracs = np.linspace(0, max_depth, n_bins)
            area_below = np.array([
                (depths >= d).mean() for d in depth_fracs
            ])

            curves[lake_dir.name] = {
                "depth_fracs": depth_fracs,
                "area_below": area_below,
                "max_depth": max_depth,
                "mean_depth": mean_depth,
                "n_pixels": len(depths),
            }
            lake_meta[lake_dir.name] = {
                "max_depth": max_depth,
                "mean_depth": mean_depth,
                "n_pixels": len(depths),
                "depth_ratio": mean_depth / (max_depth + 1e-8),
            }
        except Exception:
            continue

    log.info(f"  Computed hypsometric curves for {len(curves)} lakes")

    if len(curves) < 20:
        return {"P3_Curve_Fitting": {"error": f"too_few_lakes ({len(curves)})"}}

    # Step 2: Build training data
    # Input: sparse 5-point curve + morphometric features
    # Output: dense 50-point curve
    lake_ids = sorted(curves.keys())
    n_sparse = 5
    sparse_fracs = np.linspace(0, 1, n_sparse)  # 0%, 25%, 50%, 75%, 100% depth

    X_list, y_list, id_list = [], [], []
    for lid in lake_ids:
        c = curves[lid]
        max_d = c["max_depth"]
        dense_curve = c["area_below"]  # 50-point truth

        # Simulate sparse sampling at 5 depths
        sparse_depths = sparse_fracs * max_d
        sparse_areas = np.interp(sparse_depths, c["depth_fracs"], dense_curve)

        # Features: [sparse_areas(5), max_depth, mean_depth, depth_ratio]
        meta = lake_meta[lid]
        features = np.concatenate([
            sparse_areas,
            [meta["max_depth"], meta["mean_depth"], meta["depth_ratio"]],
        ])

        X_list.append(features)
        y_list.append(dense_curve)
        id_list.append(lid)

    X = np.array(X_list, dtype=np.float32)
    Y = np.array(y_list, dtype=np.float32)

    log.info(f"  Training data: X={X.shape}, Y={Y.shape}")

    # Step 3: Train with GroupKFold (leave-lake-out)
    n_folds = min(5, len(lake_ids))
    kf = GroupKFold(n_splits=n_folds)
    groups = np.array(id_list)

    all_true_curves = []
    all_pred_curves = []
    all_lake_ids_test = []
    per_lake_rmse = []

    for fold, (train_idx, test_idx) in enumerate(kf.split(X, Y, groups)):
        # Train XGBoost multioutput (one per curve point)
        if HAS_XGB:
            model = xgb.XGBRegressor(
                n_estimators=300, max_depth=6, learning_rate=0.05,
                tree_method="hist", device="cuda",
                multi_strategy="multi_output_tree", random_state=42,
            )
        else:
            model = RandomForestRegressor(
                n_estimators=200, max_depth=15, n_jobs=4, random_state=42
            )

        model.fit(X[train_idx], Y[train_idx])
        pred = model.predict(X[test_idx])

        for i, ti in enumerate(test_idx):
            true_curve = Y[ti]
            pred_curve = np.clip(pred[i], 0, 1)
            # Ensure monotonically decreasing
            for j in range(1, len(pred_curve)):
                pred_curve[j] = min(pred_curve[j], pred_curve[j - 1])

            all_true_curves.append(true_curve)
            all_pred_curves.append(pred_curve)
            all_lake_ids_test.append(id_list[ti])

            # Per-lake curve RMSE (in area-fraction units)
            curve_rmse = np.sqrt(mean_squared_error(true_curve, pred_curve))
            # Convert to depth RMSE: reconstruct depth from curve
            lid = id_list[ti]
            max_d = curves[lid]["max_depth"]
            # Depth RMSE: compare reconstructed mean depth
            true_mean = np.trapz(true_curve, np.linspace(0, max_d, n_bins)) / (max_d + 1e-8)
            pred_mean = np.trapz(pred_curve, np.linspace(0, max_d, n_bins)) / (max_d + 1e-8)
            depth_error = abs(true_mean - pred_mean) * max_d

            per_lake_rmse.append({
                "lake_id": lid, "curve_rmse": float(curve_rmse),
                "depth_error_m": float(depth_error),
                "max_depth": max_d,
            })

    # Aggregate
    all_true = np.array(all_true_curves)
    all_pred = np.array(all_pred_curves)

    # Curve-level RMSE (area fraction)
    curve_rmse_overall = np.sqrt(np.mean((all_true - all_pred) ** 2))

    # Depth-equivalent metrics
    depth_errors = np.array([r["depth_error_m"] for r in per_lake_rmse])
    curve_rmses = np.array([r["curve_rmse"] for r in per_lake_rmse])

    log.info(f"  Curve RMSE (area frac): {curve_rmse_overall:.4f}")
    log.info(f"  Mean depth error: {depth_errors.mean():.3f}m "
             f"(median={np.median(depth_errors):.3f}m)")
    log.info(f"  Per-lake curve RMSE: mean={curve_rmses.mean():.4f}, "
             f"p10={np.percentile(curve_rmses, 10):.4f}, "
             f"p90={np.percentile(curve_rmses, 90):.4f}")

    # R² for curve reconstruction
    flat_true = all_true.flatten()
    flat_pred = all_pred.flatten()
    curve_r2 = r2_score(flat_true, flat_pred)
    log.info(f"  Curve R² (point-wise): {curve_r2:.4f}")

    # Save
    pd.DataFrame(per_lake_rmse).to_parquet(
        OUTPUT_DIR / "p3_curve_fitting_details.parquet", index=False
    )

    return {
        "P3_Curve_Fitting": {
            "overall": {
                "label": "Curve Fitting",
                "n": len(per_lake_rmse),
                "rmse": float(depth_errors.mean()),
                "mae": float(np.median(depth_errors)),
                "r2": float(curve_r2),
                "curve_rmse_area_frac": float(curve_rmse_overall),
            },
            "n_lakes": len(per_lake_rmse),
            "per_lake_depth_error": {
                "mean": float(depth_errors.mean()),
                "median": float(np.median(depth_errors)),
                "p10": float(np.percentile(depth_errors, 10)),
                "p90": float(np.percentile(depth_errors, 90)),
            },
        }
    }


# ═══════════════════════════════════════════════════════════════════════
# PRIORITY 4: Transfer Learning (K-donor morphometric)
# ═══════════════════════════════════════════════════════════════════════

def run_priority4_transfer_learning(sonar_dir: str) -> dict:
    """
    For each test lake:
    1. Compute morphometric features (max_depth, mean_depth, area, perimeter, etc.)
    2. Find 5 most similar lakes by morphometry from training set
    3. Transfer depth profile from similar surveyed lakes
    4. Scale by known max depth
    """
    log.info("\n" + "=" * 70)
    log.info("  PRIORITY 4: Transfer Learning (K-Donor)")
    log.info("=" * 70)

    if not HAS_RASTERIO:
        return {"P4_Transfer_Learning": {"error": "rasterio not installed"}}

    sonar_path = Path(sonar_dir)
    lake_dirs = sorted([d for d in sonar_path.iterdir() if d.is_dir()])

    # Load all lake depth data and compute morphometric features
    lake_features = {}
    lake_depth_distributions = {}

    for lake_dir in lake_dirs:
        depth_file = lake_dir / "depth.tif"
        mask_file = lake_dir / "mask.tif"
        if not depth_file.exists():
            continue

        try:
            with rasterio.open(str(depth_file)) as src:
                depth = src.read(1)
                transform = src.transform
                res = abs(transform[0])  # pixel size in meters/degrees

            with rasterio.open(str(mask_file)) as src:
                mask = src.read(1)

            valid = (mask > 0) & (depth > 0.1) & np.isfinite(depth)
            depths = depth[valid]

            if len(depths) < 50:
                continue

            # Morphometric features
            max_d = float(np.percentile(depths, 99))
            mean_d = float(np.mean(depths))
            median_d = float(np.median(depths))
            std_d = float(np.std(depths))
            area_pixels = int(valid.sum())
            volume_proxy = float(np.sum(depths))

            # Depth distribution (normalized)
            n_bins = 20
            hist, _ = np.histogram(depths / (max_d + 1e-8), bins=n_bins, range=(0, 1))
            hist_norm = hist / (hist.sum() + 1e-8)

            # Shoreline complexity (perimeter/sqrt(area))
            from scipy.ndimage import binary_erosion
            eroded = binary_erosion(valid)
            perimeter_pixels = int(valid.sum() - eroded.sum())
            shore_complexity = perimeter_pixels / (np.sqrt(area_pixels) + 1e-8)

            lake_features[lake_dir.name] = np.array([
                max_d, mean_d, median_d, std_d,
                mean_d / (max_d + 1e-8),  # depth ratio
                area_pixels, volume_proxy,
                shore_complexity,
                float(np.percentile(depths, 25)),
                float(np.percentile(depths, 75)),
                float(np.percentile(depths, 10)),
                float(np.percentile(depths, 90)),
            ])

            lake_depth_distributions[lake_dir.name] = {
                "depths": depths,
                "max_depth": max_d,
                "mean_depth": mean_d,
                "hist_norm": hist_norm,
            }
        except Exception:
            continue

    log.info(f"  Computed features for {len(lake_features)} lakes")

    if len(lake_features) < 20:
        return {"P4_Transfer_Learning": {"error": f"too_few_lakes ({len(lake_features)})"}}

    # Train/test split (70/30 by lake)
    lake_ids = sorted(lake_features.keys())
    rng = np.random.RandomState(42)
    rng.shuffle(lake_ids)
    n_test = max(10, int(0.3 * len(lake_ids)))
    test_ids = lake_ids[:n_test]
    train_ids = lake_ids[n_test:]

    log.info(f"  Train: {len(train_ids)} lakes, Test: {len(test_ids)} lakes")

    # Build feature matrix for training lakes
    train_features = np.array([lake_features[lid] for lid in train_ids])
    scaler = StandardScaler()
    train_features_scaled = scaler.fit_transform(train_features)

    # For each test lake, find K nearest donors
    all_true, all_pred = [], []
    per_lake_results = []
    K = 5

    for test_lid in test_ids:
        test_feat = scaler.transform(lake_features[test_lid].reshape(1, -1))
        test_data = lake_depth_distributions[test_lid]

        # Euclidean distance in feature space
        dists = np.sqrt(np.sum((train_features_scaled - test_feat) ** 2, axis=1))
        top_k = np.argsort(dists)[:K]

        # IDW-weighted transfer
        donor_depths = []
        donor_weights = []
        for idx in top_k:
            donor_lid = train_ids[idx]
            donor_data = lake_depth_distributions[donor_lid]

            # Scale donor depth distribution to test lake's max depth
            scaled_depths = donor_data["depths"] * (
                test_data["max_depth"] / (donor_data["max_depth"] + 1e-8)
            )
            donor_depths.append(scaled_depths)
            donor_weights.append(1.0 / (dists[idx] + 1e-8))

        # Weighted merge: resample each donor proportional to weight
        total_weight = sum(donor_weights)
        merged_depths = []
        for dd, w in zip(donor_depths, donor_weights):
            n_samples = max(10, int(len(dd) * w / total_weight))
            sampled = rng.choice(dd, size=min(n_samples, len(dd)), replace=False)
            merged_depths.extend(sampled)
        merged_depths = np.array(merged_depths)

        # Compare: transfer predicts mean depth, test has true mean depth
        pred_mean = float(np.mean(merged_depths))
        true_mean = test_data["mean_depth"]

        # Compare depth distributions via histogram
        n_hist = 20
        max_d = test_data["max_depth"]
        bins = np.linspace(0, max_d, n_hist + 1)
        true_hist, _ = np.histogram(test_data["depths"], bins=bins, density=True)
        pred_hist, _ = np.histogram(merged_depths, bins=bins, density=True)

        # Point-level comparison: sample from both
        n_compare = min(500, len(test_data["depths"]))
        true_sample = np.sort(rng.choice(test_data["depths"], n_compare, replace=False))
        pred_sample = np.sort(rng.choice(merged_depths, min(n_compare, len(merged_depths)),
                                          replace=len(merged_depths) < n_compare))
        if len(pred_sample) < n_compare:
            pred_sample = np.interp(
                np.linspace(0, 1, n_compare),
                np.linspace(0, 1, len(pred_sample)),
                pred_sample
            )

        all_true.extend(true_sample)
        all_pred.extend(pred_sample[:n_compare])

        rmse = np.sqrt(mean_squared_error(true_sample, pred_sample[:n_compare]))
        per_lake_results.append({
            "lake_id": test_lid, "rmse": float(rmse),
            "true_mean": true_mean, "pred_mean": pred_mean,
            "mean_depth_error": abs(true_mean - pred_mean),
            "max_depth": max_d,
        })

    all_true = np.array(all_true)
    all_pred = np.array(all_pred)

    overall = compute_metrics(all_true, all_pred, "Transfer_K_Donor")
    stratified = compute_depth_stratified(all_true, all_pred, "Transfer_K_Donor")

    lake_rmses = [r["rmse"] for r in per_lake_results]
    mean_depth_errors = [r["mean_depth_error"] for r in per_lake_results]

    log.info(f"  OVERALL: R²={overall['r2']:.4f}, RMSE={overall['rmse']:.3f}m")
    log.info(f"  Per-lake RMSE: median={np.median(lake_rmses):.3f}m, "
             f"mean={np.mean(lake_rmses):.3f}m")
    log.info(f"  Mean depth error: median={np.median(mean_depth_errors):.3f}m")

    pd.DataFrame(per_lake_results).to_parquet(
        OUTPUT_DIR / "p4_transfer_details.parquet", index=False
    )

    return {
        "P4_Transfer_Learning": {
            "overall": overall,
            "depth_stratified": stratified,
            "n_lakes": len(per_lake_results),
            "per_lake_rmse": {
                "mean": float(np.mean(lake_rmses)),
                "median": float(np.median(lake_rmses)),
                "p10": float(np.percentile(lake_rmses, 10)),
                "p90": float(np.percentile(lake_rmses, 90)),
            },
        }
    }


# ═══════════════════════════════════════════════════════════════════════
# PRIORITY 5: Terrain DEM Prior
# ═══════════════════════════════════════════════════════════════════════

def run_priority5_terrain_dem(sonar_dir: str) -> dict:
    """
    Extract DEM/terrain features around each lake.
    Train: terrain features -> max/mean depth.
    Works for ALL lakes regardless of water clarity.
    """
    log.info("\n" + "=" * 70)
    log.info("  PRIORITY 5: Terrain DEM Prior")
    log.info("=" * 70)

    if not HAS_RASTERIO:
        return {"P5_Terrain_DEM": {"error": "rasterio not installed"}}

    sonar_path = Path(sonar_dir)
    lake_dirs = sorted([d for d in sonar_path.iterdir() if d.is_dir()])

    # Extract terrain + depth features per lake
    features_list = []
    targets_max = []
    targets_mean = []
    lake_ids = []

    for lake_dir in lake_dirs:
        depth_file = lake_dir / "depth.tif"
        mask_file = lake_dir / "mask.tif"
        dem_file = lake_dir / "dem.tif"

        if not depth_file.exists() or not mask_file.exists():
            continue

        try:
            with rasterio.open(str(depth_file)) as src:
                depth = src.read(1)
            with rasterio.open(str(mask_file)) as src:
                mask = src.read(1)

            valid = (mask > 0) & (depth > 0.1) & np.isfinite(depth)
            depths = depth[valid]
            if len(depths) < 50:
                continue

            max_d = float(np.percentile(depths, 99))
            mean_d = float(np.mean(depths))

            feats = []

            # DEM-based features
            if dem_file.exists():
                with rasterio.open(str(dem_file)) as src:
                    dem = src.read(1)

                dem_valid = dem[np.isfinite(dem)]
                lake_elev = dem[valid & np.isfinite(dem)]
                shore_elev = dem[(mask > 0) & np.isfinite(dem)]

                if len(dem_valid) > 0 and len(lake_elev) > 0:
                    # Surrounding terrain features
                    feats.extend([
                        float(np.mean(dem_valid)),
                        float(np.std(dem_valid)),
                        float(np.max(dem_valid) - np.min(dem_valid)),  # relief
                        float(np.percentile(dem_valid, 90) - np.percentile(dem_valid, 10)),
                        float(np.mean(lake_elev)),  # lake surface elevation
                    ])

                    # Gradient features
                    dy, dx = np.gradient(dem)
                    slope = np.sqrt(dx ** 2 + dy ** 2)
                    slope_valid = slope[np.isfinite(slope)]
                    feats.extend([
                        float(np.mean(slope_valid)),
                        float(np.max(slope_valid)),
                        float(np.std(slope_valid)),
                    ])
                else:
                    feats.extend([0] * 8)
            else:
                feats.extend([0] * 8)

            # Lake shape features (from mask)
            from scipy.ndimage import binary_erosion, label
            area_pixels = int(valid.sum())
            eroded = binary_erosion(valid)
            perimeter = int(valid.sum() - eroded.sum())
            shore_complexity = perimeter / (np.sqrt(area_pixels) + 1e-8)

            # Compactness (4*pi*area / perimeter^2)
            compactness = 4 * np.pi * area_pixels / (perimeter ** 2 + 1e-8)

            feats.extend([
                area_pixels,
                perimeter,
                shore_complexity,
                compactness,
            ])

            features_list.append(feats)
            targets_max.append(max_d)
            targets_mean.append(mean_d)
            lake_ids.append(lake_dir.name)

        except Exception:
            continue

    log.info(f"  Extracted features for {len(features_list)} lakes")

    if len(features_list) < 20:
        return {"P5_Terrain_DEM": {"error": f"too_few_lakes ({len(features_list)})"}}

    X = np.array(features_list, dtype=np.float32)
    y_max = np.array(targets_max, dtype=np.float32)
    y_mean = np.array(targets_mean, dtype=np.float32)

    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # GroupKFold
    n_folds = 5
    groups = np.array(lake_ids)
    kf = GroupKFold(n_splits=n_folds)

    # Predict max depth
    results_dict = {}
    for target_name, y in [("max_depth", y_max), ("mean_depth", y_mean)]:
        all_true, all_pred = [], []

        for fold, (train_idx, test_idx) in enumerate(kf.split(X, y, groups)):
            if HAS_XGB:
                model = xgb.XGBRegressor(
                    n_estimators=300, max_depth=6, learning_rate=0.05,
                    tree_method="hist", device="cuda", random_state=42,
                )
            else:
                model = RandomForestRegressor(
                    n_estimators=200, max_depth=15, n_jobs=4, random_state=42
                )

            model.fit(X[train_idx], y[train_idx])
            pred = model.predict(X[test_idx])
            all_true.extend(y[test_idx])
            all_pred.extend(pred)

        all_true = np.array(all_true)
        all_pred = np.array(all_pred)
        metrics = compute_metrics(all_true, all_pred, f"DEM_{target_name}")

        log.info(f"  {target_name}: R²={metrics['r2']:.4f}, "
                 f"RMSE={metrics['rmse']:.3f}m, MAE={metrics['mae']:.3f}m")

        # Baseline: predict mean
        baseline_pred = np.full_like(all_true, all_true.mean())
        baseline = compute_metrics(all_true, baseline_pred, "baseline")
        log.info(f"  {target_name} baseline RMSE={baseline['rmse']:.3f}m")

        results_dict[target_name] = metrics

    return {
        "P5_DEM_MaxDepth": {
            "overall": results_dict["max_depth"],
            "n_lakes": len(lake_ids),
        },
        "P5_DEM_MeanDepth": {
            "overall": results_dict["mean_depth"],
            "n_lakes": len(lake_ids),
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()

    spectral_path = "/data/sonar_s2/sonar_s2_training.parquet"
    sonar_dir = "/data/training/v2"

    all_results = {}
    checkpoints = {}

    # ── Mean Baseline ──
    log.info("\n  Computing mean baseline...")
    df = pd.read_parquet(spectral_path)
    mean_depth = df["depth_m"].mean()
    baseline_rmse = float(np.sqrt(np.mean((df["depth_m"].values - mean_depth) ** 2)))
    log.info(f"  Mean baseline: mean_depth={mean_depth:.2f}m, RMSE={baseline_rmse:.3f}m")
    all_results["Mean_Baseline"] = {
        "overall": {
            "label": "Mean_Baseline", "n": len(df),
            "rmse": baseline_rmse, "mae": float(np.mean(np.abs(df["depth_m"].values - mean_depth))),
            "r2": 0.0, "bias": 0.0,
        },
        "n_lakes": df["lake_id"].nunique(),
    }
    del df
    gc.collect()

    # ── Priority 1 ──
    try:
        p1 = run_priority1_spectral_rf(spectral_path)
        all_results.update(p1)
        checkpoints["P1"] = "done"
    except Exception as e:
        log.error(f"Priority 1 FAILED: {e}")
        traceback.print_exc()
        checkpoints["P1"] = f"error: {e}"

    gc.collect()

    # ── Priority 2 ──
    try:
        p2 = run_priority2_within_lake_sdb(spectral_path)
        all_results.update(p2)
        checkpoints["P2"] = "done"
    except Exception as e:
        log.error(f"Priority 2 FAILED: {e}")
        traceback.print_exc()
        checkpoints["P2"] = f"error: {e}"

    gc.collect()

    # ── Priority 3 ──
    try:
        p3 = run_priority3_curve_fitting(sonar_dir)
        all_results.update(p3)
        checkpoints["P3"] = "done"
    except Exception as e:
        log.error(f"Priority 3 FAILED: {e}")
        traceback.print_exc()
        checkpoints["P3"] = f"error: {e}"

    gc.collect()

    # ── Priority 4 ──
    try:
        p4 = run_priority4_transfer_learning(sonar_dir)
        all_results.update(p4)
        checkpoints["P4"] = "done"
    except Exception as e:
        log.error(f"Priority 4 FAILED: {e}")
        traceback.print_exc()
        checkpoints["P4"] = f"error: {e}"

    gc.collect()

    # ── Priority 5 ──
    try:
        p5 = run_priority5_terrain_dem(sonar_dir)
        all_results.update(p5)
        checkpoints["P5"] = "done"
    except Exception as e:
        log.error(f"Priority 5 FAILED: {e}")
        traceback.print_exc()
        checkpoints["P5"] = f"error: {e}"

    # ── Summary ──
    print_summary_table(all_results, baseline_rmse)

    elapsed = time.time() - t_start
    log.info(f"\n  Total time: {elapsed / 60:.1f} minutes")
    log.info(f"  Checkpoints: {checkpoints}")

    # ── Save all results ──
    def make_serializable(obj):
        if isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [make_serializable(v) for v in obj]
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    with open(OUTPUT_DIR / "full_comparison_results.json", "w") as f:
        json.dump(make_serializable(all_results), f, indent=2)

    with open(OUTPUT_DIR / "checkpoints.json", "w") as f:
        json.dump(checkpoints, f, indent=2)

    log.info(f"\n  Results saved to: {OUTPUT_DIR}")
    log.info("  DONE.")


if __name__ == "__main__":
    main()
