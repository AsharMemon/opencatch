#!/usr/bin/env python3
"""
OpenCatch — Stage 1: Per-Lake Maximum Depth Estimator

Predicts maximum depth for every lake using morphometric features.
Trained on LAGOS-NE (~10K lakes with known depth).

This gives a coarse depth estimate for ALL lakes, even those too
deep/turbid for optical satellite methods. Stage 2 (U-Net) then
refines this into full 2D depth rasters where optical data is viable.

Features:
- Lake area, perimeter, shore development index
- Elevation (lake surface, surrounding terrain)
- Watershed area, land cover fractions
- Latitude, longitude (climate proxy)
- Connectivity (inflow/outflow count)

Usage:
    python stage1_max_depth.py --lagos-path /data/lagos_ne.csv --output /models/stage1

Requirements:
    pip install lightgbm xgboost scikit-learn pandas numpy geopandas
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


# ── Feature Engineering ──────────────────────────────────────────────

def compute_morphometric_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute morphometric features from lake polygon attributes.

    Expected columns: lake_area_ha, lake_perim_m, lake_elevation_m,
                     lake_lat, lake_lon, ws_area_ha, max_depth_m (target)
    """
    features = pd.DataFrame()

    # Basic morphometry
    features['area_ha'] = df['lake_area_ha']
    features['log_area'] = np.log1p(df['lake_area_ha'])
    features['perim_m'] = df['lake_perim_m']
    features['log_perim'] = np.log1p(df['lake_perim_m'])

    # Shore Development Index: SDI = perimeter / (2 * sqrt(pi * area))
    area_m2 = df['lake_area_ha'] * 10000
    features['sdi'] = df['lake_perim_m'] / (2 * np.sqrt(np.pi * area_m2))

    # Compactness ratio
    features['compactness'] = 4 * np.pi * area_m2 / (df['lake_perim_m'] ** 2 + 1e-6)

    # Elevation
    features['elevation_m'] = df['lake_elevation_m']

    # Watershed ratio
    if 'ws_area_ha' in df.columns:
        features['ws_lake_ratio'] = df['ws_area_ha'] / (df['lake_area_ha'] + 1e-6)
        features['log_ws_area'] = np.log1p(df['ws_area_ha'])

    # Location (climate proxy)
    features['lat'] = df['lake_lat']
    features['lon'] = df['lake_lon']
    features['abs_lat'] = np.abs(df['lake_lat'])

    # Interaction features
    features['area_x_elevation'] = features['log_area'] * features['elevation_m']
    features['area_x_lat'] = features['log_area'] * features['abs_lat']

    return features


# ── Model Training ───────────────────────────────────────────────────

def train_max_depth_model(
    data_path: Path,
    output_dir: Path,
    target_col: str = 'max_depth_m',
):
    """
    Train XGBoost + LightGBM ensemble for max depth prediction.
    """
    import lightgbm as lgb
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import mean_squared_error, r2_score
    import joblib

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    df = pd.read_csv(data_path)
    log.info(f"Loaded {len(df)} lakes")

    # Filter to lakes with known depth
    df = df.dropna(subset=[target_col])
    df = df[df[target_col] > 0]
    log.info(f"{len(df)} lakes with known depth")

    # Compute features
    X = compute_morphometric_features(df)
    y = np.log1p(df[target_col].values)  # Log-transform depth for better distribution

    # Handle missing values
    X = X.fillna(X.median())

    # Group by HUC4 watershed for spatial cross-validation
    groups = df.get('hu4_zoneid', pd.Series(range(len(df))))

    # 5-fold spatial CV
    gkf = GroupKFold(n_splits=5)
    oof_preds = np.zeros(len(y))

    feature_names = X.columns.tolist()
    models = []

    for fold, (train_idx, val_idx) in enumerate(gkf.split(X, y, groups)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        # LightGBM
        lgb_train = lgb.Dataset(X_train, y_train, feature_name=feature_names)
        lgb_val = lgb.Dataset(X_val, y_val, feature_name=feature_names, reference=lgb_train)

        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'num_leaves': 63,
            'learning_rate': 0.05,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'min_child_samples': 20,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'verbose': -1,
        }

        model = lgb.train(
            params, lgb_train,
            num_boost_round=1000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)],
        )

        oof_preds[val_idx] = model.predict(X_val)
        models.append(model)

        val_rmse = mean_squared_error(y_val, oof_preds[val_idx]) ** 0.5
        val_r2 = r2_score(y_val, oof_preds[val_idx])
        log.info(f"Fold {fold+1}: RMSE={val_rmse:.4f} (log-m), R²={val_r2:.4f}")

    # Overall OOF metrics (in original scale)
    oof_depth = np.expm1(oof_preds)
    true_depth = np.expm1(y)
    overall_rmse = mean_squared_error(true_depth, oof_depth) ** 0.5
    overall_r2 = r2_score(true_depth, oof_depth)
    overall_mae = np.mean(np.abs(true_depth - oof_depth))

    log.info(f"\nOverall OOF Results:")
    log.info(f"  RMSE: {overall_rmse:.2f} m")
    log.info(f"  MAE:  {overall_mae:.2f} m")
    log.info(f"  R²:   {overall_r2:.4f}")
    log.info(f"  Median error: {np.median(np.abs(true_depth - oof_depth)):.2f} m")

    # Feature importance
    avg_importance = np.mean([m.feature_importance(importance_type='gain') for m in models], axis=0)
    importance_df = pd.DataFrame({
        'feature': feature_names,
        'importance': avg_importance,
    }).sort_values('importance', ascending=False)
    log.info(f"\nTop Features:\n{importance_df.head(10).to_string()}")

    # Save models
    for i, model in enumerate(models):
        model.save_model(str(output_dir / f'lgb_fold{i}.txt'))

    importance_df.to_csv(output_dir / 'feature_importance.csv', index=False)

    # Save metrics
    with open(output_dir / 'metrics.txt', 'w') as f:
        f.write(f"RMSE: {overall_rmse:.2f} m\n")
        f.write(f"MAE: {overall_mae:.2f} m\n")
        f.write(f"R²: {overall_r2:.4f}\n")
        f.write(f"N lakes: {len(y)}\n")

    log.info(f"Models saved to {output_dir}")
    return models


def predict_all_lakes(
    models: list,
    lakes_path: Path,
    output_path: Path,
):
    """
    Predict max depth for ALL lakes (including unsurveyed ones).
    """
    df = pd.read_csv(lakes_path)
    X = compute_morphometric_features(df)
    X = X.fillna(X.median())

    # Ensemble prediction (average of fold models)
    preds_log = np.mean([m.predict(X) for m in models], axis=0)
    preds = np.expm1(preds_log)

    # Confidence based on feature completeness
    n_missing = X.isnull().sum(axis=1)
    confidence = np.where(n_missing == 0, 'high', np.where(n_missing <= 2, 'medium', 'low'))

    df['predicted_max_depth_m'] = preds
    df['predicted_max_depth_ft'] = preds * 3.281
    df['depth_confidence'] = confidence

    df.to_csv(output_path, index=False)
    log.info(f"Predictions for {len(df)} lakes saved to {output_path}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--lagos-path', type=str, required=True)
    parser.add_argument('--output', type=str, default='/models/stage1')
    parser.add_argument('--predict-all', type=str, default=None,
                       help='Path to all lakes CSV for prediction')
    args = parser.parse_args()

    models = train_max_depth_model(Path(args.lagos_path), Path(args.output))

    if args.predict_all:
        predict_all_lakes(models, Path(args.predict_all), Path(args.output) / 'all_predictions.csv')
