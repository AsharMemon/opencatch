#!/usr/bin/env python3
"""
OpenCatch — Reservoir Depth Model Trainer
==========================================
XGBoost/LightGBM trained on reservoir-specific features to predict
max depth and A-E curve shape parameters.

Features from reservoir_features.py:
  - NID: dam_height_m, surface_area_km2, max_storage_m3, drainage_area_km2,
    dam_age_years, hazard_numeric, purpose flags, height_to_area_ratio,
    storage_ratio, watershed_lake_ratio, log transforms
  - Cross-section: mean_valley_width_m, valley_width_std_m, mean_shape_exponent,
    shape_consistency, valley_asymmetry, n_valid_sections, mean_fit_r2,
    mean_confidence, thalweg_gradient_m_per_km, estimated_max_depth_m
  - SWOT: wse_range_m, wse_std_m, n_swot_observations, area-elevation power exponent
  - Terrain: latitude, longitude

Targets:
  1. max_depth_m — maximum reservoir depth
  2. ae_shape_n  — A-E curve power exponent n where A = A_max * ((E-E_min)/(E_max-E_min))^n

Training data: USBR survey reservoirs (ground truth) + 3D-LAKES reservoir subset

Validation: Leave-one-reservoir-out cross-validation.

Usage:
    python train_reservoir_depth.py \
        --features /data/reservoir_features.parquet \
        --output /data/models/reservoir_depth \
        --device cpu
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import LeaveOneOut

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("train_reservoir_depth")

EPS = 1e-8

# All features expected from reservoir_features.compute_reservoir_features()
NID_FEATURES = [
    "dam_height_m", "surface_area_km2", "max_storage_m3", "normal_storage_m3",
    "drainage_area_km2", "crest_elevation_m", "base_elevation_m", "dam_age_years",
    "height_to_area_ratio", "storage_ratio", "watershed_lake_ratio",
    "log_dam_height_m", "log_area_km2", "log_storage_m3",
    "hazard_numeric",
]

CROSS_SECTION_FEATURES = [
    "mean_valley_width_m", "valley_width_std_m", "valley_width_cv",
    "mean_shape_exponent", "shape_consistency",
    "frac_v_shaped", "frac_u_shaped",
    "n_valid_sections", "mean_fit_r2", "mean_confidence",
    "thalweg_gradient_m_per_km", "thalweg_gradient_std", "estimated_max_depth_m",
]

SWOT_FEATURES = [
    "wse_range_m", "wse_std_m", "wse_mean_m",
    "n_swot_observations", "area_range_km2", "area_mean_km2",
]

FLOWLINE_FEATURES = [
    "thalweg_length_m", "sinuosity", "n_tributaries", "max_stream_order",
]

TERRAIN_FEATURES = ["latitude", "longitude"]

INTERACTION_FEATURES = ["height_width_ratio", "height_length_ratio"]

TARGETS = ["max_depth_m", "ae_shape_n"]


# ══════════════════════════════════════════════════════════════════════
# Data Loading and Prep
# ══════════════════════════════════════════════════════════════════════

def get_feature_columns(df: pd.DataFrame) -> List[str]:
    """Identify available feature columns from the dataframe."""
    all_possible = (
        NID_FEATURES + CROSS_SECTION_FEATURES + SWOT_FEATURES +
        FLOWLINE_FEATURES + TERRAIN_FEATURES + INTERACTION_FEATURES
    )
    # Include purpose flags dynamically
    purpose_cols = [c for c in df.columns if c.startswith("purpose_")]
    all_possible += purpose_cols

    available = [c for c in all_possible if c in df.columns]
    log.info(f"Using {len(available)} / {len(all_possible)} possible features")
    return available


def load_features(features_path: Path) -> pd.DataFrame:
    """Load reservoir feature parquet."""
    if features_path.suffix == ".parquet":
        df = pd.read_parquet(features_path)
    elif features_path.suffix == ".csv":
        df = pd.read_csv(features_path)
    else:
        raise ValueError(f"Unsupported format: {features_path.suffix}")

    # Require reservoir_id as index or column
    if "reservoir_id" in df.columns:
        df = df.set_index("reservoir_id")

    log.info(f"Loaded features: {df.shape[0]} reservoirs, {df.shape[1]} columns")
    return df


def prepare_dataset(
    df: pd.DataFrame,
    target: str,
    feature_cols: List[str],
) -> Tuple[np.ndarray, np.ndarray, List[str], pd.Index]:
    """Prepare X, y arrays, dropping rows with missing target."""
    valid = df[target].notna()
    df_valid = df.loc[valid]

    X = df_valid[feature_cols].values.astype(np.float32)
    y = df_valid[target].values.astype(np.float32)
    ids = df_valid.index

    log.info(f"Target '{target}': {len(y)} samples, "
             f"range [{y.min():.2f}, {y.max():.2f}], "
             f"mean={y.mean():.2f}, std={y.std():.2f}")

    return X, y, feature_cols, ids


# ══════════════════════════════════════════════════════════════════════
# Hyperparameter Search Spaces
# ══════════════════════════════════════════════════════════════════════

def get_xgb_param_grid() -> List[dict]:
    """XGBoost hyperparameter candidates."""
    return [
        {"max_depth": 3, "learning_rate": 0.05, "n_estimators": 300,
         "subsample": 0.8, "colsample_bytree": 0.8, "reg_alpha": 0.1, "reg_lambda": 1.0},
        {"max_depth": 4, "learning_rate": 0.03, "n_estimators": 500,
         "subsample": 0.7, "colsample_bytree": 0.7, "reg_alpha": 0.5, "reg_lambda": 2.0},
        {"max_depth": 5, "learning_rate": 0.01, "n_estimators": 800,
         "subsample": 0.8, "colsample_bytree": 0.6, "reg_alpha": 1.0, "reg_lambda": 3.0},
        {"max_depth": 3, "learning_rate": 0.1, "n_estimators": 200,
         "subsample": 0.9, "colsample_bytree": 0.9, "reg_alpha": 0.0, "reg_lambda": 1.0},
        {"max_depth": 6, "learning_rate": 0.02, "n_estimators": 600,
         "subsample": 0.75, "colsample_bytree": 0.7, "reg_alpha": 0.3, "reg_lambda": 2.0},
    ]


def get_lgb_param_grid() -> List[dict]:
    """LightGBM hyperparameter candidates."""
    return [
        {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 300,
         "subsample": 0.8, "colsample_bytree": 0.8, "reg_alpha": 0.1, "reg_lambda": 1.0,
         "num_leaves": 15, "min_child_samples": 5},
        {"max_depth": 5, "learning_rate": 0.03, "n_estimators": 500,
         "subsample": 0.7, "colsample_bytree": 0.7, "reg_alpha": 0.5, "reg_lambda": 2.0,
         "num_leaves": 20, "min_child_samples": 3},
        {"max_depth": 3, "learning_rate": 0.1, "n_estimators": 200,
         "subsample": 0.9, "colsample_bytree": 0.9, "reg_alpha": 0.0, "reg_lambda": 1.0,
         "num_leaves": 10, "min_child_samples": 5},
        {"max_depth": 6, "learning_rate": 0.02, "n_estimators": 600,
         "subsample": 0.75, "colsample_bytree": 0.7, "reg_alpha": 0.3, "reg_lambda": 2.0,
         "num_leaves": 31, "min_child_samples": 3},
    ]


# ══════════════════════════════════════════════════════════════════════
# Leave-One-Reservoir-Out Cross-Validation
# ══════════════════════════════════════════════════════════════════════

def loo_cv(
    X: np.ndarray,
    y: np.ndarray,
    reservoir_ids: pd.Index,
    model_type: str,
    params: dict,
    device: str = "cpu",
) -> Dict:
    """Leave-one-reservoir-out cross-validation."""
    loo = LeaveOneOut()
    y_pred_all = np.full_like(y, np.nan)

    for train_idx, test_idx in loo.split(X):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = _build_model(model_type, params, device)
        model.fit(X_train, y_train)
        y_pred_all[test_idx] = model.predict(X_test)

    valid = np.isfinite(y_pred_all)
    if valid.sum() < 2:
        return {"error": "too_few_valid", "n": int(valid.sum())}

    t = y[valid]
    p = y_pred_all[valid]

    residuals = p - t
    rel_errors = np.abs(residuals) / np.maximum(np.abs(t), EPS)

    return {
        "n": int(valid.sum()),
        "rmse": float(np.sqrt(mean_squared_error(t, p))),
        "mae": float(mean_absolute_error(t, p)),
        "r2": float(r2_score(t, p)),
        "bias": float(np.mean(residuals)),
        "std_error": float(np.std(residuals)),
        "median_abs_error": float(np.median(np.abs(residuals))),
        "p90_error": float(np.percentile(np.abs(residuals), 90)),
        "mean_relative_error": float(np.mean(rel_errors)),
        "y_true": t.tolist(),
        "y_pred": p.tolist(),
        "reservoir_ids": reservoir_ids[valid].tolist(),
    }


def _build_model(model_type: str, params: dict, device: str):
    """Instantiate XGBoost or LightGBM regressor."""
    if model_type == "xgboost":
        import xgboost as xgb
        tree_method = "hist"
        if device == "cuda":
            tree_method = "hist"
            params = {**params, "device": "cuda"}
        return xgb.XGBRegressor(
            tree_method=tree_method,
            random_state=42,
            verbosity=0,
            **params,
        )
    elif model_type == "lightgbm":
        import lightgbm as lgb
        lgb_device = "gpu" if device == "cuda" else "cpu"
        return lgb.LGBMRegressor(
            device=lgb_device,
            random_state=42,
            verbose=-1,
            **params,
        )
    else:
        raise ValueError(f"Unknown model type: {model_type}")


# ══════════════════════════════════════════════════════════════════════
# Feature Importance (SHAP or built-in)
# ══════════════════════════════════════════════════════════════════════

def compute_feature_importance(
    model,
    X: np.ndarray,
    feature_names: List[str],
    model_type: str,
) -> Dict[str, float]:
    """Compute feature importance, preferring SHAP if available."""
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)
        importance = np.abs(shap_values).mean(axis=0)
        method = "shap"
    except (ImportError, Exception):
        if model_type == "xgboost":
            importance = model.feature_importances_
        elif model_type == "lightgbm":
            importance = model.feature_importances_
        else:
            importance = np.zeros(len(feature_names))
        method = "builtin"

    # Normalize
    total = importance.sum()
    if total > 0:
        importance = importance / total

    result = {
        "method": method,
        "features": {
            name: float(imp)
            for name, imp in sorted(
                zip(feature_names, importance), key=lambda x: -x[1]
            )
        },
    }
    return result


# ══════════════════════════════════════════════════════════════════════
# Main Training Pipeline
# ══════════════════════════════════════════════════════════════════════

def train_and_evaluate(
    features_path: Path,
    output_dir: Path,
    device: str = "cpu",
) -> Dict:
    """Full training pipeline: hyperparameter search + LOO-CV + final model."""
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Load data
    df = load_features(features_path)
    feature_cols = get_feature_columns(df)

    all_results = {}

    for target in TARGETS:
        if target not in df.columns:
            log.warning(f"Target '{target}' not in data, skipping")
            continue

        log.info(f"\n{'='*70}")
        log.info(f"  TARGET: {target}")
        log.info(f"{'='*70}")

        X, y, feat_names, ids = prepare_dataset(df, target, feature_cols)
        if len(y) < 10:
            log.warning(f"Only {len(y)} samples for '{target}', skipping")
            continue

        target_results = {"n_samples": len(y)}
        best_score = -np.inf
        best_config = None

        # -- XGBoost search --
        try:
            import xgboost  # noqa: F401
            xgb_available = True
        except ImportError:
            xgb_available = False
            log.warning("XGBoost not available, skipping")

        if xgb_available:
            log.info("\n  XGBoost hyperparameter search...")
            for i, params in enumerate(get_xgb_param_grid()):
                cv_result = loo_cv(X, y, ids, "xgboost", params, device)
                if "error" in cv_result:
                    continue
                r2 = cv_result["r2"]
                rmse = cv_result["rmse"]
                log.info(f"    XGB config {i}: R²={r2:.4f}, RMSE={rmse:.3f}")
                if r2 > best_score:
                    best_score = r2
                    best_config = ("xgboost", params, cv_result)

        # -- LightGBM search --
        try:
            import lightgbm  # noqa: F401
            lgb_available = True
        except ImportError:
            lgb_available = False
            log.warning("LightGBM not available, skipping")

        if lgb_available:
            log.info("\n  LightGBM hyperparameter search...")
            for i, params in enumerate(get_lgb_param_grid()):
                cv_result = loo_cv(X, y, ids, "lightgbm", params, device)
                if "error" in cv_result:
                    continue
                r2 = cv_result["r2"]
                rmse = cv_result["rmse"]
                log.info(f"    LGB config {i}: R²={r2:.4f}, RMSE={rmse:.3f}")
                if r2 > best_score:
                    best_score = r2
                    best_config = ("lightgbm", params, cv_result)

        if best_config is None:
            log.error(f"No valid model found for '{target}'")
            continue

        model_type, best_params, cv_metrics = best_config

        log.info(f"\n  Best model: {model_type}")
        log.info(f"  Best LOO-CV R²: {cv_metrics['r2']:.4f}")
        log.info(f"  Best LOO-CV RMSE: {cv_metrics['rmse']:.3f}")
        log.info(f"  Best LOO-CV MAE: {cv_metrics['mae']:.3f}")

        # -- Train final model on all data --
        log.info("\n  Training final model on all data...")
        final_model = _build_model(model_type, best_params, device)
        final_model.fit(X, y)

        # Feature importance
        importance = compute_feature_importance(
            final_model, X, feat_names, model_type
        )
        log.info(f"\n  Top 10 features ({importance['method']}):")
        for fname, fimp in list(importance["features"].items())[:10]:
            log.info(f"    {fname:<35s} {fimp:.4f}")

        # -- Save model --
        import pickle
        model_path = output_dir / f"{target}_{model_type}_final.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(final_model, f)
        log.info(f"  Model saved: {model_path}")

        # -- Collect results --
        # Strip arrays for JSON (keep metrics only)
        cv_summary = {k: v for k, v in cv_metrics.items()
                      if k not in ("y_true", "y_pred", "reservoir_ids")}

        target_results.update({
            "model_type": model_type,
            "best_params": best_params,
            "loo_cv": cv_summary,
            "feature_importance": importance,
            "model_path": str(model_path),
        })

        all_results[target] = target_results

    # -- Save config JSON --
    elapsed = time.time() - t0
    config = {
        "features_path": str(features_path),
        "output_dir": str(output_dir),
        "device": device,
        "feature_columns": feature_cols,
        "elapsed_seconds": round(elapsed, 1),
        "results": all_results,
    }

    config_path = output_dir / "training_config.json"

    def _serializable(obj):
        if isinstance(obj, dict):
            return {k: _serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_serializable(v) for v in obj]
        if isinstance(obj, (np.floating, np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.integer, np.int32, np.int64)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    with open(config_path, "w") as f:
        json.dump(_serializable(config), f, indent=2)

    log.info(f"\n{'='*70}")
    log.info(f"  Training complete in {elapsed:.1f}s")
    log.info(f"  Config saved: {config_path}")
    log.info(f"{'='*70}")

    # -- Summary table --
    log.info(f"\n  {'Target':<20s} {'Model':<12s} {'R²':>8s} {'RMSE':>8s} {'MAE':>8s} {'N':>5s}")
    log.info("-" * 60)
    for tgt, res in all_results.items():
        cv = res.get("loo_cv", {})
        log.info(f"  {tgt:<20s} {res.get('model_type','?'):<12s} "
                 f"{cv.get('r2', float('nan')):8.4f} "
                 f"{cv.get('rmse', float('nan')):8.3f} "
                 f"{cv.get('mae', float('nan')):8.3f} "
                 f"{cv.get('n', 0):5d}")

    return all_results


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Train Reservoir Depth Model (XGBoost/LightGBM)"
    )
    parser.add_argument(
        "--features", type=str, required=True,
        help="Path to reservoir features parquet or CSV",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for model artifacts and config",
    )
    parser.add_argument(
        "--device", type=str, default="cpu",
        choices=["cpu", "cuda"],
        help="Device for training (default: cpu)",
    )
    args = parser.parse_args()

    train_and_evaluate(
        features_path=Path(args.features),
        output_dir=Path(args.output),
        device=args.device,
    )


if __name__ == "__main__":
    main()
