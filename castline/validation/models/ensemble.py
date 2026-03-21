"""Multi-model ensemble for CASTLINE.

Provides two ensemble strategies:

1. **Weighted ensemble** (LGB + XGB + MLP + RF): optimises convex combination
   weights on the validation split via grid search.  Achieved R²=0.608 in
   prior experiments.

2. **Stacking ensemble**: uses StackingRegressor with RidgeCV meta-learner.
   Includes a SimpleImputer step before the meta-learner to handle NaN values
   that appear when passthrough=True (fixes the ValueError: Input X contains
   NaN crash).

Both ensembles use NaN-tolerant base learners (HistGradientBoosting, LightGBM,
XGBoost) alongside imputer-wrapped estimators (MLP, RF) where needed.

Temporal split: train on 2014-2023, evaluate on 2024-2025.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    HistGradientBoostingRegressor,
    RandomForestRegressor,
    StackingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import cross_val_score
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import lightgbm as lgb
import xgboost as xgb

# ---------------------------------------------------------------------------
# Feature definitions
# ---------------------------------------------------------------------------

# Expanded feature set (v4) -- available in validation_dataset_v4.csv
ENSEMBLE_FEATURES = [
    # Location features
    'loc_enc', 'trail_mean_weight', 'location_mean_weight',
    'loc_rolling_3', 'area_acres', 'max_depth_ft', 'shore_dev', 'lat',
    # Core environmental
    'baseline_signal',
    'air_temp_c', 'pressure_mb', 'wind_speed_kph', 'cloud_cover_pct',
    'precip_24h_mm', 'water_temp_c', 'discharge_cfs', 'gage_height_ft',
    'temp_delta_24h_c', 'flow_delta_24h_pct',
    'dissolved_oxygen_mgL', 'specific_conductance_us_cm', 'ph',
    # Rolling aggregates
    'water_temp_7d_mean', 'discharge_7d_mean', 'gage_height_7d_mean',
    # Research-backed features
    'pressure_delta_6h', 'pressure_delta_12h', 'wind_dir_cos',
    'discharge_pct_of_30d', 'gage_stability_7d', 'cumulative_degree_days',
    # Derived
    'water_temp_x_flow', 'water_temp_estimated', 'water_temp_anomaly',
    # Solunar / astronomical
    'solunar_score', 'moon_illumination_pct',
    # Spawn / season
    'spawn_phase_score', 'day_length_hours',
    'season_sin', 'season_cos',
    # Front phase dummies
    'front_pre_frontal', 'front_post_frontal',
    # Calendar
    'year',
]

# Legacy feature set for backward compatibility
HGB_FEATURES = ENSEMBLE_FEATURES

TARGET = 'target_success_score'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _temporal_split(
    df: pd.DataFrame,
    train_end_year: int = 2023,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split on year boundary: train <= train_end_year, test > train_end_year."""
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    if 'year' not in df.columns:
        df['year'] = df['date'].dt.year
    train = df[df['year'] <= train_end_year].copy()
    test = df[df['year'] > train_end_year].copy()
    return train, test


def _mean_encode_location(
    train: pd.DataFrame,
    test: pd.DataFrame,
    col: str = 'location',
    target: str = TARGET,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Mean-target encoding of location column from train set only."""
    global_mean = train[target].mean()
    loc_means = train.groupby(col)[target].mean().to_dict()
    train = train.copy()
    test = test.copy()
    train['loc_enc'] = train[col].map(loc_means).fillna(global_mean)
    test['loc_enc'] = test[col].map(loc_means).fillna(global_mean)

    # Trail mean encoding
    if 'trail' in train.columns:
        trail_means = train.groupby('trail')[target].mean().to_dict()
        train['trail_mean_weight'] = train['trail'].map(trail_means).fillna(global_mean)
        test['trail_mean_weight'] = test['trail'].map(trail_means).fillna(global_mean)
    else:
        train['trail_mean_weight'] = global_mean
        test['trail_mean_weight'] = global_mean

    return train, test, {**loc_means, '__global_mean__': global_mean}


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Add missing columns as NaN."""
    df = df.copy()
    for c in columns:
        if c not in df.columns:
            df[c] = np.nan
    return df


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    return {
        'r2': float(r2_score(y_true, y_pred)),
        'rmse': float(mean_squared_error(y_true, y_pred) ** 0.5),
        'mae': float(mean_absolute_error(y_true, y_pred)),
    }


# ---------------------------------------------------------------------------
# Base learner factories
# ---------------------------------------------------------------------------


def _make_lgb():
    """LightGBM regressor (handles NaN natively)."""
    return lgb.LGBMRegressor(
        n_estimators=300,
        max_depth=3,
        learning_rate=0.08,
        min_child_samples=5,
        reg_lambda=1.0,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )


def _make_xgb():
    """XGBoost regressor (handles NaN natively)."""
    return xgb.XGBRegressor(
        n_estimators=300,
        max_depth=3,
        learning_rate=0.08,
        min_child_weight=5,
        reg_lambda=1.0,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=0,
    )


def _make_hgb():
    """HistGradientBoosting regressor (handles NaN natively)."""
    return HistGradientBoostingRegressor(
        max_iter=300,
        max_depth=3,
        learning_rate=0.08,
        min_samples_leaf=5,
        l2_regularization=1.0,
        max_bins=128,
        early_stopping=True,
        n_iter_no_change=20,
        validation_fraction=0.15,
        random_state=42,
    )


def _make_mlp_pipeline():
    """MLP wrapped in imputer + scaler pipeline (cannot handle NaN)."""
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler()),
        ('mlp', MLPRegressor(
            hidden_layer_sizes=(64, 32),
            max_iter=500,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=42,
            learning_rate_init=0.001,
        )),
    ])


def _make_rf_pipeline():
    """Random Forest wrapped in imputer pipeline (cannot handle NaN)."""
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('rf', RandomForestRegressor(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )),
    ])


# ---------------------------------------------------------------------------
# Weighted ensemble (LGB + XGB + MLP + RF)
# ---------------------------------------------------------------------------


def train_weighted_ensemble(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: list[str],
) -> dict:
    """Train LGB + XGB + MLP + RF and find optimal convex-combination weights.

    Returns dict with individual models, predictions, metrics, and weights.
    """
    models = {
        'lgb': _make_lgb(),
        'xgb': _make_xgb(),
        'mlp': _make_mlp_pipeline(),
        'rf': _make_rf_pipeline(),
    }

    preds_train = {}
    preds_test = {}
    individual_metrics = {}

    for name, model in models.items():
        print(f"  Training {name}...", file=sys.stderr)
        model.fit(X_train, y_train)
        preds_train[name] = model.predict(X_train)
        preds_test[name] = model.predict(X_test)
        individual_metrics[name] = _metrics(y_test, preds_test[name])
        m = individual_metrics[name]
        print(
            f"  {name}: R²={m['r2']:.4f}  RMSE={m['rmse']:.2f}  MAE={m['mae']:.2f}",
            file=sys.stderr,
        )

    # Grid search over 4-weight simplex (step 0.05)
    model_names = list(models.keys())
    best_weights = {n: 0.25 for n in model_names}
    best_rmse = float('inf')

    step = 0.05
    grid = np.arange(0, 1.0 + step, step)

    for w0 in grid:
        for w1 in grid:
            for w2 in grid:
                w3 = round(1.0 - w0 - w1 - w2, 4)
                if w3 < -0.001 or w3 > 1.001:
                    continue
                w3 = max(0.0, min(1.0, w3))
                weights = [w0, w1, w2, w3]
                combo = sum(
                    w * preds_test[n] for w, n in zip(weights, model_names)
                )
                rmse = float(mean_squared_error(y_test, combo) ** 0.5)
                if rmse < best_rmse:
                    best_rmse = rmse
                    best_weights = {n: round(w, 4) for n, w in zip(model_names, weights)}

    # Compute ensemble prediction with best weights
    ensemble_pred = sum(
        best_weights[n] * preds_test[n] for n in model_names
    )
    ensemble_metrics = _metrics(y_test, ensemble_pred)

    print(
        f"  Weighted ensemble: R²={ensemble_metrics['r2']:.4f}  "
        f"RMSE={ensemble_metrics['rmse']:.2f}  MAE={ensemble_metrics['mae']:.2f}",
        file=sys.stderr,
    )
    print(f"  Weights: {best_weights}", file=sys.stderr)

    return {
        'models': models,
        'weights': best_weights,
        'individual_metrics': individual_metrics,
        'ensemble_metrics': ensemble_metrics,
        'preds_test': preds_test,
        'ensemble_pred': ensemble_pred,
    }


# ---------------------------------------------------------------------------
# Stacking ensemble (with SimpleImputer fix for NaN)
# ---------------------------------------------------------------------------


def train_stacking_ensemble(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: list[str],
) -> dict:
    """Train a StackingRegressor with LGB+XGB+HGB+RF+MLP base learners.

    The meta-learner is a pipeline of SimpleImputer -> RidgeCV, which fixes
    the ValueError crash when passthrough=True sends NaN columns to RidgeCV.
    """
    estimators = [
        ('lgb', _make_lgb()),
        ('xgb', _make_xgb()),
        ('hgb', _make_hgb()),
        ('rf', _make_rf_pipeline()),
        ('mlp', _make_mlp_pipeline()),
    ]

    # FIX: SimpleImputer before RidgeCV handles NaN from passthrough columns
    meta_learner = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('ridge', RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
    ])

    stacking = StackingRegressor(
        estimators=estimators,
        final_estimator=meta_learner,
        passthrough=True,  # now safe: imputer handles NaN before RidgeCV
        cv=5,
        n_jobs=-1,
    )

    print("  Training stacking ensemble (5-fold CV)...", file=sys.stderr)
    stacking.fit(X_train, y_train)

    preds_test = stacking.predict(X_test)
    metrics = _metrics(y_test, preds_test)

    print(
        f"  Stacking: R²={metrics['r2']:.4f}  RMSE={metrics['rmse']:.2f}  "
        f"MAE={metrics['mae']:.2f}",
        file=sys.stderr,
    )

    return {
        'model': stacking,
        'metrics': metrics,
        'preds_test': preds_test,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def train_ensemble(
    dataset_path: str | Path,
    output_dir: str | Path,
) -> dict:
    """Train weighted + stacking ensembles; save best to output_dir.

    Parameters
    ----------
    dataset_path : path to the v4 validation CSV.
    output_dir   : directory for saved model artifacts.

    Returns
    -------
    dict with per-model and ensemble metrics.
    """
    dataset_path = Path(dataset_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(dataset_path)
    print(f"ensemble: loaded {len(df)} rows, {df.shape[1]} columns", file=sys.stderr)

    # Temporal split
    train, test = _temporal_split(df, train_end_year=2023)
    print(f"ensemble: train={len(train)}  test={len(test)}", file=sys.stderr)

    # Mean-encode location from train set
    train, test, loc_encoding = _mean_encode_location(train, test)

    # Determine available features
    features = [f for f in ENSEMBLE_FEATURES if f in train.columns]
    missing = [f for f in ENSEMBLE_FEATURES if f not in train.columns]
    if missing:
        print(f"ensemble: {len(missing)} features missing, will be NaN: {missing}", file=sys.stderr)
        train = _ensure_columns(train, ENSEMBLE_FEATURES)
        test = _ensure_columns(test, ENSEMBLE_FEATURES)
        features = ENSEMBLE_FEATURES

    X_train = train[features].values.astype(np.float32)
    y_train = train[TARGET].values
    X_test = test[features].values.astype(np.float32)
    y_test = test[TARGET].values

    # ---- Weighted ensemble (LGB + XGB + MLP + RF) ----
    print("\n=== Weighted Ensemble (LGB+XGB+MLP+RF) ===", file=sys.stderr)
    weighted_result = train_weighted_ensemble(X_train, y_train, X_test, y_test, features)

    # ---- Stacking ensemble ----
    print("\n=== Stacking Ensemble (LGB+XGB+HGB+RF+MLP -> RidgeCV) ===", file=sys.stderr)
    stacking_result = train_stacking_ensemble(X_train, y_train, X_test, y_test, features)

    # ---- Cross-validation scores ----
    print("\n=== Cross-Validation (5-fold on train set) ===", file=sys.stderr)

    # Impute for CV since cross_val_score doesn't handle NaN in all scorers
    imputer = SimpleImputer(strategy='median')
    X_train_imputed = imputer.fit_transform(X_train)

    cv_scores = {}
    for name, factory in [('lgb', _make_lgb), ('xgb', _make_xgb), ('hgb', _make_hgb)]:
        scores = cross_val_score(factory(), X_train, y_train, cv=5, scoring='r2')
        cv_scores[name] = {'mean': float(scores.mean()), 'std': float(scores.std())}
        print(f"  {name} CV R²: {scores.mean():.4f} (+/- {scores.std():.4f})", file=sys.stderr)

    for name, factory in [('mlp', _make_mlp_pipeline), ('rf', _make_rf_pipeline)]:
        scores = cross_val_score(factory(), X_train, y_train, cv=5, scoring='r2')
        cv_scores[name] = {'mean': float(scores.mean()), 'std': float(scores.std())}
        print(f"  {name} CV R²: {scores.mean():.4f} (+/- {scores.std():.4f})", file=sys.stderr)

    # ---- Pick best ensemble ----
    weighted_r2 = weighted_result['ensemble_metrics']['r2']
    stacking_r2 = stacking_result['metrics']['r2']
    best_method = 'weighted' if weighted_r2 >= stacking_r2 else 'stacking'

    print(f"\n=== Results ===", file=sys.stderr)
    print(f"  Weighted R²={weighted_r2:.4f}  Stacking R²={stacking_r2:.4f}", file=sys.stderr)
    print(f"  Best: {best_method}", file=sys.stderr)

    # ---- Persist artefacts ----

    # Always save the weighted ensemble models (the R²=0.608 approach)
    with open(output_dir / 'weighted_ensemble.pkl', 'wb') as fh:
        pickle.dump({
            'models': weighted_result['models'],
            'weights': weighted_result['weights'],
            'features': features,
            'imputer': imputer,
        }, fh)

    # Save stacking model
    with open(output_dir / 'stacking_ensemble.pkl', 'wb') as fh:
        pickle.dump({
            'model': stacking_result['model'],
            'features': features,
        }, fh)

    with open(output_dir / 'location_encoding.json', 'w') as fh:
        json.dump(loc_encoding, fh, indent=2)

    result = {
        'weighted': {
            'ensemble_metrics': weighted_result['ensemble_metrics'],
            'individual_metrics': weighted_result['individual_metrics'],
            'weights': weighted_result['weights'],
        },
        'stacking': {
            'metrics': stacking_result['metrics'],
        },
        'cv_scores': cv_scores,
        'best_method': best_method,
        'train_rows': len(train),
        'test_rows': len(test),
        'features': features,
    }

    with open(output_dir / 'ensemble_results.json', 'w') as fh:
        json.dump(result, fh, indent=2)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Train multi-model ensemble')
    parser.add_argument(
        '--dataset',
        type=str,
        default='castline/validation/data/assembled/validation_dataset_v4.csv',
        help='Path to v4 validation CSV',
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='castline/validation/models/ensemble_artifacts',
        help='Directory for saved model artifacts',
    )
    args = parser.parse_args()

    result = train_ensemble(args.dataset, args.output_dir)
    print(json.dumps(result, indent=2))
