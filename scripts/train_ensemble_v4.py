#!/usr/bin/env python3
"""Train stacking + weighted ensemble on v4 dataset, save best to production.

Usage:
    python scripts/train_ensemble_v4.py
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
from sklearn.model_selection import cross_val_score

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from castline.validation.models.ensemble import (
    ENSEMBLE_FEATURES,
    TARGET,
    _temporal_split,
    _mean_encode_location,
    _ensure_columns,
    _metrics,
    train_weighted_ensemble,
    train_stacking_ensemble,
)

DATASET_PATH = ROOT / 'castline/validation/data/assembled/validation_dataset_v4.csv'
ARTIFACTS_DIR = ROOT / 'castline/validation/models/ensemble_artifacts'
PRODUCTION_DIR = ROOT / 'castline/validation/data/models/production'


def main():
    print("=" * 70)
    print("CASTLINE Ensemble Training (v4 dataset)")
    print("=" * 70)

    # ---- Load data ----
    df = pd.read_csv(DATASET_PATH)
    print(f"\nDataset: {DATASET_PATH}")
    print(f"  Rows: {len(df)}, Columns: {df.shape[1]}")

    # ---- Temporal split ----
    train, test = _temporal_split(df, train_end_year=2023)
    print(f"  Train: {len(train)} rows (<=2023), Test: {len(test)} rows (2024-2025)")

    # ---- Mean-encode location ----
    train, test, loc_encoding = _mean_encode_location(train, test)
    global_mean = loc_encoding['__global_mean__']

    # ---- Prepare features ----
    features = [f for f in ENSEMBLE_FEATURES if f in train.columns]
    missing = [f for f in ENSEMBLE_FEATURES if f not in train.columns]
    if missing:
        print(f"  Missing features (will be NaN): {missing}")
        train = _ensure_columns(train, ENSEMBLE_FEATURES)
        test = _ensure_columns(test, ENSEMBLE_FEATURES)
        features = ENSEMBLE_FEATURES

    print(f"  Features: {len(features)}")

    X_train = train[features].values.astype(np.float32)
    y_train = train[TARGET].values
    X_test = test[features].values.astype(np.float32)
    y_test = test[TARGET].values

    nan_pct = np.isnan(X_train).mean() * 100
    print(f"  NaN percentage in training data: {nan_pct:.1f}%")

    # ==================================================================
    # 1. Weighted ensemble (LGB + XGB + MLP + RF)
    # ==================================================================
    print("\n" + "=" * 70)
    print("1. WEIGHTED ENSEMBLE (LGB + XGB + MLP + RF)")
    print("=" * 70)

    weighted_result = train_weighted_ensemble(X_train, y_train, X_test, y_test, features)

    print("\n  Individual model test scores:")
    for name, m in weighted_result['individual_metrics'].items():
        print(f"    {name:>4s}: R²={m['r2']:.4f}  RMSE={m['rmse']:.2f}  MAE={m['mae']:.2f}")

    wm = weighted_result['ensemble_metrics']
    print(f"\n  Weighted ensemble test: R²={wm['r2']:.4f}  RMSE={wm['rmse']:.2f}  MAE={wm['mae']:.2f}")
    print(f"  Optimal weights: {weighted_result['weights']}")

    # ==================================================================
    # 2. Stacking ensemble (with SimpleImputer fix)
    # ==================================================================
    print("\n" + "=" * 70)
    print("2. STACKING ENSEMBLE (LGB+XGB+HGB+RF+MLP -> Imputer+RidgeCV)")
    print("=" * 70)

    stacking_result = train_stacking_ensemble(X_train, y_train, X_test, y_test, features)
    sm = stacking_result['metrics']
    print(f"\n  Stacking ensemble test: R²={sm['r2']:.4f}  RMSE={sm['rmse']:.2f}  MAE={sm['mae']:.2f}")

    # ==================================================================
    # 3. Cross-validation scores
    # ==================================================================
    print("\n" + "=" * 70)
    print("3. CROSS-VALIDATION (5-fold on training set)")
    print("=" * 70)

    from castline.validation.models.ensemble import (
        _make_lgb, _make_xgb, _make_hgb, _make_mlp_pipeline, _make_rf_pipeline,
    )

    cv_scores = {}
    for name, factory in [
        ('lgb', _make_lgb),
        ('xgb', _make_xgb),
        ('hgb', _make_hgb),
        ('mlp', _make_mlp_pipeline),
        ('rf', _make_rf_pipeline),
    ]:
        scores = cross_val_score(factory(), X_train, y_train, cv=5, scoring='r2')
        cv_scores[name] = {'mean': float(scores.mean()), 'std': float(scores.std())}
        print(f"  {name:>4s} CV R²: {scores.mean():.4f} (+/- {scores.std():.4f})")

    # ==================================================================
    # 4. Select best and save to production
    # ==================================================================
    print("\n" + "=" * 70)
    print("4. PRODUCTION DEPLOYMENT")
    print("=" * 70)

    weighted_r2 = weighted_result['ensemble_metrics']['r2']
    stacking_r2 = stacking_result['metrics']['r2']

    print(f"\n  Weighted ensemble test R²:  {weighted_r2:.4f}")
    print(f"  Stacking ensemble test R²: {stacking_r2:.4f}")

    best_method = 'weighted' if weighted_r2 >= stacking_r2 else 'stacking'
    best_r2 = max(weighted_r2, stacking_r2)
    best_metrics = weighted_result['ensemble_metrics'] if best_method == 'weighted' else stacking_result['metrics']

    print(f"\n  Best method: {best_method} (R²={best_r2:.4f})")

    # Save artifacts
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTION_DIR.mkdir(parents=True, exist_ok=True)

    # Build imputer fitted on training data
    imputer = SimpleImputer(strategy='median')
    imputer.fit(X_train)

    # Save weighted ensemble (always save since it's the proven approach)
    weighted_artifact = {
        'models': weighted_result['models'],
        'weights': weighted_result['weights'],
        'features': features,
        'imputer': imputer,
    }
    with open(ARTIFACTS_DIR / 'weighted_ensemble.pkl', 'wb') as fh:
        pickle.dump(weighted_artifact, fh)
    print(f"  Saved weighted ensemble -> {ARTIFACTS_DIR / 'weighted_ensemble.pkl'}")

    # Save stacking ensemble
    stacking_artifact = {
        'model': stacking_result['model'],
        'features': features,
    }
    with open(ARTIFACTS_DIR / 'stacking_ensemble.pkl', 'wb') as fh:
        pickle.dump(stacking_artifact, fh)
    print(f"  Saved stacking ensemble -> {ARTIFACTS_DIR / 'stacking_ensemble.pkl'}")

    # Save location encoding
    with open(ARTIFACTS_DIR / 'location_encoding.json', 'w') as fh:
        json.dump(loc_encoding, fh, indent=2)

    # Save best model to production
    if best_method == 'weighted':
        # Save individual models for production inference
        for name, model in weighted_result['models'].items():
            with open(PRODUCTION_DIR / f'{name}_model.pkl', 'wb') as fh:
                pickle.dump(model, fh)
        with open(PRODUCTION_DIR / 'ensemble_weights.json', 'w') as fh:
            json.dump(weighted_result['weights'], fh, indent=2)
        with open(PRODUCTION_DIR / 'production_ensemble.pkl', 'wb') as fh:
            pickle.dump(weighted_artifact, fh)
    else:
        with open(PRODUCTION_DIR / 'production_ensemble.pkl', 'wb') as fh:
            pickle.dump(stacking_artifact, fh)

    # Save location encoding to production
    with open(PRODUCTION_DIR / 'location_encoding.json', 'w') as fh:
        json.dump(loc_encoding, fh, indent=2)

    # ==================================================================
    # 5. Update model_metadata.json
    # ==================================================================
    # Determine best individual CV R² for metadata
    best_cv = max(cv_scores.values(), key=lambda x: x['mean'])

    metadata = {
        'feature_names': features,
        'version': 'v4-ensemble-' + best_method,
        'ensemble_method': best_method,
        'r2': best_metrics['r2'],
        'rmse': best_metrics['rmse'],
        'mae': best_metrics['mae'],
        'cv_r2_mean': best_cv['mean'],
        'cv_r2_std': best_cv['std'],
        'train_rows': len(train),
        'test_rows': len(test),
        'individual_test_r2': {
            name: m['r2'] for name, m in weighted_result['individual_metrics'].items()
        },
        'weighted_ensemble_r2': weighted_r2,
        'stacking_ensemble_r2': stacking_r2,
        'cv_scores': cv_scores,
        'weights': weighted_result['weights'] if best_method == 'weighted' else None,
        'location_means': {k: v for k, v in loc_encoding.items() if k != '__global_mean__'},
        'global_mean': global_mean,
        'trained_at': datetime.now().isoformat(),
    }

    with open(PRODUCTION_DIR / 'model_metadata.json', 'w') as fh:
        json.dump(metadata, fh, indent=2)
    print(f"  Updated model_metadata.json -> {PRODUCTION_DIR / 'model_metadata.json'}")

    # ==================================================================
    # Summary
    # ==================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n  Dataset:    {len(df)} rows, {len(features)} features")
    print(f"  Train/Test: {len(train)}/{len(test)}")
    print()
    print("  Individual model test R²:")
    for name, m in weighted_result['individual_metrics'].items():
        print(f"    {name:>4s}: {m['r2']:.4f}")
    print()
    print(f"  Weighted ensemble test R²:  {weighted_r2:.4f}")
    print(f"  Stacking ensemble test R²:  {stacking_r2:.4f}")
    print()
    print("  Cross-validation R² (5-fold):")
    for name, s in cv_scores.items():
        print(f"    {name:>4s}: {s['mean']:.4f} (+/- {s['std']:.4f})")
    print()
    print(f"  Best model: {best_method} ensemble (R²={best_r2:.4f})")
    print(f"  Production: {PRODUCTION_DIR}")
    print()


if __name__ == '__main__':
    main()
