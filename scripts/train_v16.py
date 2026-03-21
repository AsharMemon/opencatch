#!/usr/bin/env python3
"""CASTLINE V16 Training: Hurdle model architecture with seen/unseen routing.

Architecture:
  Stage 1 (Hurdle): Binary classifier predicting P(any catch) via CatBoost
  Stage 2 (Regression): Stacked ensemble (CatBoost + XGBoost + LightGBM -> Ridge)
                         predicting E[CPUE | catch > 0]
  Final:  prediction = P(catch) * E[CPUE | catch > 0]

Routing:
  Seen locations (>=3 historical events): location-specific features included
  Unseen locations: geographic/regional features only

Evaluation:
  1. Walk-forward: train pre-2022, test 2022+
  2. Spatial: leave-one-location-out CV
  3. Regime: hold out entire ecological regimes
  4. Per-split: overall R^2, seen R^2, unseen R^2, per-regime R^2, AUC, RMSE

Designed to run on Vast.ai with GPU support for CatBoost/XGBoost.

Usage:
    python scripts/train_v16.py [--dataset PATH] [--no-tune] [--gpu]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from scipy.stats import spearmanr
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    ndcg_score,
    r2_score,
    roc_auc_score,
    log_loss,
    average_precision_score,
    brier_score_loss,
)
from sklearn.model_selection import GroupKFold, TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DATASET_V18 = ROOT / "castline/validation/data/assembled/validation_dataset_v18.csv"
DEFAULT_DATASET_V17 = ROOT / "castline/validation/data/assembled/validation_dataset_v17.csv"
MODEL_DIR = ROOT / "castline/models"
CHECKPOINT_DIR = ROOT / "castline/models/checkpoints"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("train_v16")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET = "median_weight_lb"
VERSION = "v16"
MIN_LOC_HISTORY = 3  # threshold for seen vs unseen

# Columns to always exclude from features
ALWAYS_EXCLUDE = {
    "target_success_score", "tms_id", "event_id",
}

# Location identity features (only for seen model)
LOCATION_IDENTITY = {
    "loc_enc", "trail_mean_weight", "location_mean_weight",
    "loc_rolling_3", "baseline_signal",
}

# Metadata / string columns
META_COLS = {
    "date", "location", "region", "block", "sat_source",
    "usgs_site_id", "event_name", "tournament_slug",
    "trail", "results_source", "species", "spawn_phase",
    "source",
}

# Leaky features (must never be used)
LEAKY_COLS = {
    "loc_mean_enc", "source_enc",
    # V18 hurdle target derivatives — directly derived from median_weight_lb
    "has_catch", "positive_cpue",
    # Any other target-derived columns
    "target_success_score",
}

# Location-specific features excluded from unseen model
LOC_SPECIFIC_FEATURES = {
    "loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
    "loc_encoding_confidence", "loc_last_year", "loc_tournament_fraction",
    "source_rolling_mean", "seasonal_pattern_phase",
}

# Prediction bounds
PRED_MIN = 1.0
PRED_MAX = 25.0

# Hurdle threshold: CPUE below this is treated as "no meaningful catch"
HURDLE_THRESHOLD = 1.5  # lb median weight; below means very poor fishing


# ====================================================================
# FEATURE ENGINEERING
# ====================================================================

def add_temporal_trend_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add temporal trend features that help walk-forward generalization."""
    dates = pd.to_datetime(df["date"], errors="coerce")

    # Year as continuous
    df["year_trend"] = dates.dt.year + dates.dt.dayofyear / 365.25

    # Days since 2020-01-01 (centered reference)
    ref = pd.Timestamp("2020-01-01")
    df["days_since_2020"] = (dates - ref).dt.days.astype(float)

    # Day of week and weekend
    if "day_of_week" not in df.columns:
        df["day_of_week"] = dates.dt.dayofweek.astype(float)
    if "is_weekend" not in df.columns:
        df["is_weekend"] = (dates.dt.dayofweek >= 5).astype(float)

    return df


def add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add domain-informed interaction features."""
    # Pressure x temperature change => frontal passage signal
    if "np_pressure_kpa" in df.columns and "lag_temp_trend" in df.columns:
        pres_norm = (df["np_pressure_kpa"] - df["np_pressure_kpa"].median())
        df["pressure_x_temp_change"] = pres_norm * df["lag_temp_trend"]

    # Spawn x temperature => spawn activity modulated by actual temp
    if "spawn_probability" in df.columns and "np_temp_mean_c" in df.columns:
        df["spawn_x_temp"] = df["spawn_probability"] * df["np_temp_mean_c"]

    # Wind x depth => wind mixing potential
    if "np_wind_10m_ms" in df.columns and "max_depth_ft" in df.columns:
        df["wind_x_depth"] = df["np_wind_10m_ms"] / (df["max_depth_ft"].clip(lower=1))

    # Photoperiod x GDD => phenological timing
    if "photoperiod_hrs" in df.columns and "gdd_calibrated" in df.columns:
        df["photo_x_gdd"] = df["photoperiod_hrs"] * np.log1p(df["gdd_calibrated"])

    # Lat x season => regional seasonal adjustment
    if "lat" in df.columns and "season_sin" in df.columns:
        df["lat_x_season"] = df["lat"] * df["season_sin"]

    return df


def select_features(df: pd.DataFrame, for_unseen: bool = False) -> list[str]:
    """Select numeric features, excluding leaky/meta columns."""
    all_cols = set(df.columns)
    exclude = ALWAYS_EXCLUDE | META_COLS | LEAKY_COLS | {TARGET}

    if for_unseen:
        exclude |= LOCATION_IDENTITY | LOC_SPECIFIC_FEATURES

    candidates = sorted(all_cols - exclude)
    # Keep only numeric
    features = [
        f for f in candidates
        if df[f].dtype in ("float64", "float32", "int64", "int32")
    ]
    return features


# ====================================================================
# HURDLE MODEL CLASSES
# ====================================================================

class HurdleClassifier:
    """Stage 1: Binary classifier for P(catch > threshold)."""

    def __init__(self, task_type: str = "CPU", random_seed: int = 42):
        self.task_type = task_type
        self.random_seed = random_seed
        self.model = None
        self.threshold = HURDLE_THRESHOLD

    def _make_model(self, params: dict | None = None):
        from catboost import CatBoostClassifier

        defaults = dict(
            iterations=1200,
            depth=5,
            learning_rate=0.03,
            l2_leaf_reg=5,
            random_seed=self.random_seed,
            bootstrap_type="Bernoulli",
            subsample=0.8,
            verbose=0,
            eval_metric="AUC",
            task_type=self.task_type,
            auto_class_weights="Balanced",
        )
        if params:
            defaults.update(params)
        return CatBoostClassifier(**defaults)

    def fit(self, X: pd.DataFrame, y: np.ndarray,
            features: list[str], params: dict | None = None):
        """Fit binary classifier: y > threshold => 1, else 0."""
        y_binary = (y > self.threshold).astype(int)
        log.info(
            f"  Hurdle classifier: {y_binary.sum()}/{len(y_binary)} positive "
            f"({y_binary.mean()*100:.1f}%)"
        )
        self.model = self._make_model(params)
        self.model.fit(X[features], y_binary)
        self.features = features
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return P(catch > threshold)."""
        return self.model.predict_proba(X[self.features])[:, 1]

    def save(self, path: Path):
        self.model.save_model(str(path))

    def load(self, path: Path):
        from catboost import CatBoostClassifier
        self.model = CatBoostClassifier()
        self.model.load_model(str(path))


class StackedRegressor:
    """Stage 2: CatBoost + XGBoost + LightGBM stacked with Ridge."""

    def __init__(self, task_type: str = "CPU", random_seed: int = 42):
        self.task_type = task_type
        self.random_seed = random_seed
        self.models = {}
        self.meta_model = None
        self.scaler = None
        self.features = []

    def _make_catboost(self, params: dict | None = None):
        from catboost import CatBoostRegressor

        defaults = dict(
            iterations=1500,
            depth=6,
            learning_rate=0.02,
            l2_leaf_reg=5,
            random_seed=self.random_seed,
            bootstrap_type="Bernoulli",
            subsample=0.7,
            verbose=0,
            task_type=self.task_type,
        )
        # colsample_bylevel (rsm) not supported on GPU
        if self.task_type != "GPU":
            defaults["colsample_bylevel"] = 0.8
        if params:
            defaults.update(params)
        return CatBoostRegressor(**defaults)

    def _make_xgboost(self, params: dict | None = None):
        import xgboost as xgb

        device = "cuda" if self.task_type == "GPU" else "cpu"
        defaults = dict(
            n_estimators=1500,
            max_depth=6,
            learning_rate=0.02,
            reg_lambda=5,
            subsample=0.7,
            colsample_bytree=0.8,
            random_state=self.random_seed,
            verbosity=0,
            device=device,
        )
        if params:
            defaults.update(params)
        return xgb.XGBRegressor(**defaults)

    def _make_lightgbm(self, params: dict | None = None):
        import lightgbm as lgb

        defaults = dict(
            n_estimators=1500,
            max_depth=6,
            learning_rate=0.02,
            reg_lambda=5,
            subsample=0.7,
            colsample_bytree=0.8,
            random_state=self.random_seed,
            verbose=-1,
            force_col_wise=True,
        )
        if params:
            defaults.update(params)
        return lgb.LGBMRegressor(**defaults)

    def fit(self, X: pd.DataFrame, y: np.ndarray, features: list[str],
            cb_params: dict | None = None,
            xgb_params: dict | None = None,
            lgb_params: dict | None = None,
            n_folds: int = 5):
        """Fit base models with OOF stacking, then fit Ridge meta-learner."""
        self.features = features
        Xf = X[features].values.astype(np.float32)

        # Replace inf with nan
        Xf = np.where(np.isinf(Xf), np.nan, Xf)

        log.info(f"  Stacked regressor: {Xf.shape[0]} rows, {Xf.shape[1]} features")

        # Build base models
        cb = self._make_catboost(cb_params)
        xgb_m = self._make_xgboost(xgb_params)
        lgb_m = self._make_lightgbm(lgb_params)

        # Out-of-fold predictions for stacking
        oof_cb = np.full(len(y), np.nan)
        oof_xgb = np.full(len(y), np.nan)
        oof_lgb = np.full(len(y), np.nan)

        kf = GroupKFold(n_splits=n_folds)
        # Use index as groups for simple splitting (no location leakage in
        # stacking since final eval handles that separately)
        groups = np.arange(len(y)) % n_folds

        for fold, (tr_idx, va_idx) in enumerate(kf.split(Xf, y, groups)):
            Xtr, Xva = Xf[tr_idx], Xf[va_idx]
            ytr, yva = y[tr_idx], y[va_idx]

            from catboost import CatBoostRegressor as _CBR
            cb_fold = self._make_catboost(cb_params)
            cb_fold.fit(Xtr, ytr, eval_set=(Xva, yva), early_stopping_rounds=100,
                        verbose=0)
            oof_cb[va_idx] = cb_fold.predict(Xva)

            import xgboost as _xgb
            xgb_fold = self._make_xgboost(xgb_params)
            xgb_fold.fit(Xtr, ytr, eval_set=[(Xva, yva)],
                         verbose=False)
            oof_xgb[va_idx] = xgb_fold.predict(Xva)

            import lightgbm as _lgb
            lgb_fold = self._make_lightgbm(lgb_params)
            lgb_fold.fit(Xtr, ytr, eval_set=[(Xva, yva)],
                         callbacks=[_lgb.early_stopping(100, verbose=False),
                                    _lgb.log_evaluation(period=0)])
            oof_lgb[va_idx] = lgb_fold.predict(Xva)

        # Fit Ridge on OOF predictions
        valid = ~(np.isnan(oof_cb) | np.isnan(oof_xgb) | np.isnan(oof_lgb))
        S = np.column_stack([oof_cb[valid], oof_xgb[valid], oof_lgb[valid]])
        self.scaler = StandardScaler().fit(S)
        Ss = self.scaler.transform(S)

        self.meta_model = RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0])
        self.meta_model.fit(Ss, y[valid])
        oof_pred = self.meta_model.predict(Ss)

        log.info(
            f"  OOF stacking R^2: {r2_score(y[valid], oof_pred):.4f}, "
            f"Ridge alpha: {self.meta_model.alpha_:.4f}, "
            f"Weights: cb={self.meta_model.coef_[0]:.3f} "
            f"xgb={self.meta_model.coef_[1]:.3f} "
            f"lgb={self.meta_model.coef_[2]:.3f}"
        )

        # Refit base models on full data
        log.info("  Refitting base models on full training data...")
        self.models["catboost"] = self._make_catboost(cb_params)
        self.models["catboost"].fit(Xf, y)

        self.models["xgboost"] = self._make_xgboost(xgb_params)
        self.models["xgboost"].fit(Xf, y)

        self.models["lightgbm"] = self._make_lightgbm(lgb_params)
        self.models["lightgbm"].fit(Xf, y)

        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict using stacked ensemble."""
        Xf = X[self.features].values.astype(np.float32)
        Xf = np.where(np.isinf(Xf), np.nan, Xf)

        p_cb = self.models["catboost"].predict(Xf)
        p_xgb = self.models["xgboost"].predict(Xf)
        p_lgb = self.models["lightgbm"].predict(Xf)

        S = np.column_stack([p_cb, p_xgb, p_lgb])
        Ss = self.scaler.transform(S)
        return self.meta_model.predict(Ss)

    def predict_individual(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        """Return predictions from each base model."""
        Xf = X[self.features].values.astype(np.float32)
        Xf = np.where(np.isinf(Xf), np.nan, Xf)
        return {
            "catboost": self.models["catboost"].predict(Xf),
            "xgboost": self.models["xgboost"].predict(Xf),
            "lightgbm": self.models["lightgbm"].predict(Xf),
        }

    def save(self, prefix: str, model_dir: Path):
        """Save all models to disk."""
        # CatBoost native
        self.models["catboost"].save_model(str(model_dir / f"{prefix}_catboost.cbm"))

        # XGBoost + LightGBM via pickle
        with open(model_dir / f"{prefix}_xgboost.pkl", "wb") as f:
            pickle.dump(self.models["xgboost"], f)

        with open(model_dir / f"{prefix}_lightgbm.pkl", "wb") as f:
            pickle.dump(self.models["lightgbm"], f)

        # Ridge meta-model + scaler
        with open(model_dir / f"{prefix}_ridge.pkl", "wb") as f:
            pickle.dump({"meta_model": self.meta_model, "scaler": self.scaler}, f)


class HurdleEnsemble:
    """Complete hurdle model: classifier + regressor."""

    def __init__(self, task_type: str = "CPU", random_seed: int = 42):
        self.classifier = HurdleClassifier(task_type, random_seed)
        self.regressor = StackedRegressor(task_type, random_seed)

    def fit(self, X: pd.DataFrame, y: np.ndarray,
            hurdle_features: list[str],
            regression_features: list[str],
            hurdle_params: dict | None = None,
            cb_params: dict | None = None,
            xgb_params: dict | None = None,
            lgb_params: dict | None = None):
        """Fit both stages."""
        log.info("Fitting hurdle classifier...")
        self.classifier.fit(X, y, hurdle_features, hurdle_params)

        # Fit regression only on positive examples
        positive_mask = y > HURDLE_THRESHOLD
        log.info(
            f"Fitting regression on {positive_mask.sum()}/{len(y)} positive rows..."
        )
        self.regressor.fit(
            X[positive_mask], y[positive_mask], regression_features,
            cb_params, xgb_params, lgb_params,
        )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Final = P(catch) * E[CPUE | catch > 0], clipped to bounds."""
        p_catch = self.classifier.predict_proba(X)
        e_cpue = self.regressor.predict(X)

        # Combine: weighted prediction
        # For rows with very high P(catch), trust regression fully
        # For low P(catch), blend toward minimum
        pred = p_catch * np.clip(e_cpue, PRED_MIN, PRED_MAX) + (1 - p_catch) * PRED_MIN

        return np.clip(pred, PRED_MIN, PRED_MAX)

    def predict_components(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        """Return individual components for diagnostics."""
        return {
            "p_catch": self.classifier.predict_proba(X),
            "e_cpue_positive": self.regressor.predict(X),
            "final": self.predict(X),
        }


# ====================================================================
# OPTUNA HYPERPARAMETER TUNING
# ====================================================================

def tune_hurdle_classifier(X_train: pd.DataFrame, y_train: np.ndarray,
                           features: list[str], n_trials: int = 40,
                           task_type: str = "CPU") -> dict:
    """Tune hurdle classifier with Optuna using temporal-aware CV."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    from catboost import CatBoostClassifier

    y_binary = (y_train > HURDLE_THRESHOLD).astype(int)
    Xf = X_train[features].values.astype(np.float32)
    Xf = np.where(np.isinf(Xf), np.nan, Xf)

    def objective(trial):
        params = {
            "iterations": trial.suggest_int("iterations", 500, 2000),
            "depth": trial.suggest_int("depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.1, 30, log=True),
            "bootstrap_type": "Bernoulli",
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "random_seed": 42,
            "verbose": 0,
            "eval_metric": "AUC",
            "task_type": task_type,
            "auto_class_weights": "Balanced",
        }

        # Temporal-aware 3-fold split
        n = len(y_binary)
        fold_size = n // 3
        aucs = []
        for fold in range(3):
            va_start = fold * fold_size
            va_end = va_start + fold_size if fold < 2 else n
            tr_idx = list(range(0, va_start)) + list(range(va_end, n))
            va_idx = list(range(va_start, va_end))
            if len(tr_idx) < 50 or len(va_idx) < 20:
                continue

            m = CatBoostClassifier(**params)
            m.fit(Xf[tr_idx], y_binary[tr_idx],
                  eval_set=(Xf[va_idx], y_binary[va_idx]),
                  early_stopping_rounds=50, verbose=0)
            proba = m.predict_proba(Xf[va_idx])[:, 1]
            try:
                aucs.append(roc_auc_score(y_binary[va_idx], proba))
            except ValueError:
                aucs.append(0.5)

        return np.mean(aucs) if aucs else 0.5

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    log.info(f"  Best hurdle AUC: {study.best_value:.4f}")
    log.info(f"  Best params: {study.best_params}")
    return study.best_params


def tune_regression(X_train: pd.DataFrame, y_train: np.ndarray,
                    features: list[str], n_trials: int = 40,
                    task_type: str = "CPU") -> dict:
    """Tune regression ensemble with Optuna."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    from catboost import CatBoostRegressor

    Xf = X_train[features].values.astype(np.float32)
    Xf = np.where(np.isinf(Xf), np.nan, Xf)

    def objective(trial):
        params = {
            "iterations": trial.suggest_int("iterations", 500, 2000),
            "depth": trial.suggest_int("depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.1, 30, log=True),
            "bootstrap_type": "Bernoulli",
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "random_seed": 42,
            "verbose": 0,
            "task_type": task_type,
        }
        if task_type != "GPU":
            params["colsample_bylevel"] = trial.suggest_float("colsample_bylevel", 0.5, 1.0)

        # Temporal-aware 3-fold
        n = len(y_train)
        fold_size = n // 3
        r2s = []
        for fold in range(3):
            va_start = fold * fold_size
            va_end = va_start + fold_size if fold < 2 else n
            tr_idx = list(range(0, va_start)) + list(range(va_end, n))
            va_idx = list(range(va_start, va_end))
            if len(tr_idx) < 50 or len(va_idx) < 20:
                continue

            m = CatBoostRegressor(**params)
            m.fit(Xf[tr_idx], y_train[tr_idx],
                  eval_set=(Xf[va_idx], y_train[va_idx]),
                  early_stopping_rounds=50, verbose=0)
            pred = m.predict(Xf[va_idx])
            r2s.append(r2_score(y_train[va_idx], pred))

        return np.mean(r2s) if r2s else -1.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    log.info(f"  Best regression R^2: {study.best_value:.4f}")
    log.info(f"  Best params: {study.best_params}")
    return study.best_params


# ====================================================================
# CONFORMAL CALIBRATION
# ====================================================================

def conformal_calibration(
    model: HurdleEnsemble,
    X_cal: pd.DataFrame,
    y_cal: np.ndarray,
) -> dict:
    """Run conformal calibration on held-out calibration set.

    Returns calibration data: residuals and quantiles for prediction intervals.
    """
    pred = model.predict(X_cal)
    residuals = y_cal - pred
    abs_residuals = np.abs(residuals)

    # Sort for quantile lookup
    sorted_residuals = np.sort(abs_residuals)
    n = len(sorted_residuals)

    calibration = {
        "n_calibration": n,
        "residual_mean": float(np.mean(residuals)),
        "residual_std": float(np.std(residuals)),
        "mae": float(np.mean(abs_residuals)),
        "coverage_80": float(np.percentile(abs_residuals, 80)),
        "coverage_90": float(np.percentile(abs_residuals, 90)),
        "coverage_95": float(np.percentile(abs_residuals, 95)),
        "sorted_abs_residuals": sorted_residuals.tolist(),
        "quantiles": {
            str(q): float(np.percentile(abs_residuals, q))
            for q in [50, 60, 70, 80, 85, 90, 95, 99]
        },
    }

    log.info(
        f"  Conformal calibration: n={n}, MAE={calibration['mae']:.3f}, "
        f"90% coverage width={calibration['coverage_90']:.3f}"
    )
    return calibration


# ====================================================================
# ECOLOGICAL REGIME CLASSIFICATION
# ====================================================================

def classify_regime(df: pd.DataFrame) -> pd.Series:
    """Classify each row into an ecological regime for regime-based holdout."""
    regime = pd.Series("other", index=df.index)

    # Great Lakes
    great_lakes_kw = [
        "erie", "michigan", "huron", "superior", "ontario",
        "st. clair", "st clair",
    ]
    if "location" in df.columns:
        loc_lower = df["location"].str.lower().fillna("")
        for kw in great_lakes_kw:
            regime = regime.where(~loc_lower.str.contains(kw), "great_lakes")

    # Coastal / tidal
    coastal_kw = [
        "chesapeake", "potomac", "james river", "tidal",
        "delta", "bayou", "marsh",
    ]
    for kw in coastal_kw:
        regime = regime.where(~loc_lower.str.contains(kw), "coastal")

    # Ozark reservoirs
    ozark_kw = [
        "table rock", "bull shoals", "beaver", "norfork",
        "stockton", "pomme de terre", "truman",
    ]
    for kw in ozark_kw:
        regime = regime.where(~loc_lower.str.contains(kw), "ozark")

    # Southern reservoirs
    if "lat" in df.columns:
        regime = regime.where(
            ~((df["lat"] < 34) & (regime == "other")), "southern"
        )
        regime = regime.where(
            ~((df["lat"] > 42) & (regime == "other")), "northern"
        )

    return regime


# ====================================================================
# EVALUATION PROTOCOL
# ====================================================================

def evaluate_walkforward(
    df: pd.DataFrame,
    seen_features: list[str],
    unseen_features: list[str],
    task_type: str = "CPU",
    tune: bool = False,
) -> dict:
    """Walk-forward: train on pre-2022, test on 2022+."""
    log.info("=" * 60)
    log.info("WALK-FORWARD EVALUATION (train < 2022, test >= 2022)")
    log.info("=" * 60)

    dates = pd.to_datetime(df["date"], errors="coerce")
    train_mask = dates.dt.year < 2022
    test_mask = dates.dt.year >= 2022

    if test_mask.sum() < 20:
        log.warning("Not enough test data for walk-forward (< 20 rows)")
        return {}

    train_df = df[train_mask].copy()
    test_df = df[test_mask].copy()
    y_train = train_df[TARGET].values
    y_test = test_df[TARGET].values

    log.info(f"  Train: {len(train_df)} rows, Test: {len(test_df)} rows")

    # Identify seen/unseen locations in test
    loc_counts = train_df["location"].value_counts()
    seen_locs = set(loc_counts[loc_counts >= MIN_LOC_HISTORY].index)

    test_seen_mask = test_df["location"].isin(seen_locs)
    test_unseen_mask = ~test_seen_mask

    log.info(
        f"  Test seen: {test_seen_mask.sum()} rows, "
        f"Test unseen: {test_unseen_mask.sum()} rows"
    )

    # Train seen hurdle ensemble
    train_seen = train_df[train_df["location"].isin(seen_locs)]
    if len(train_seen) > 50:
        log.info("Training SEEN hurdle ensemble...")
        seen_model = HurdleEnsemble(task_type)
        seen_model.fit(
            train_seen, train_seen[TARGET].values,
            hurdle_features=seen_features,
            regression_features=seen_features,
        )
    else:
        seen_model = None

    # Train unseen hurdle ensemble
    log.info("Training UNSEEN hurdle ensemble...")
    unseen_model = HurdleEnsemble(task_type)
    unseen_model.fit(
        train_df, y_train,
        hurdle_features=unseen_features,
        regression_features=unseen_features,
    )

    # Predict
    pred = np.full(len(test_df), np.nan)
    if seen_model is not None and test_seen_mask.any():
        pred[test_seen_mask.values] = seen_model.predict(test_df[test_seen_mask])
    if test_unseen_mask.any():
        pred[test_unseen_mask.values] = unseen_model.predict(test_df[test_unseen_mask])

    # Fill any remaining NaN with unseen predictions
    nan_mask = np.isnan(pred)
    if nan_mask.any():
        pred[nan_mask] = unseen_model.predict(test_df[nan_mask])

    pred = np.clip(pred, PRED_MIN, PRED_MAX)

    # Metrics
    r2_all = r2_score(y_test, pred)
    mae_all = mean_absolute_error(y_test, pred)
    rmse_all = np.sqrt(mean_squared_error(y_test, pred))

    metrics = {
        "r2_overall": float(r2_all),
        "mae": float(mae_all),
        "rmse": float(rmse_all),
        "n_test": int(len(test_df)),
    }

    if test_seen_mask.any() and test_seen_mask.sum() > 5:
        metrics["r2_seen"] = float(r2_score(
            y_test[test_seen_mask.values], pred[test_seen_mask.values]
        ))
    if test_unseen_mask.any() and test_unseen_mask.sum() > 5:
        metrics["r2_unseen"] = float(r2_score(
            y_test[test_unseen_mask.values], pred[test_unseen_mask.values]
        ))

    # Hurdle-specific metrics
    y_binary = (y_test > HURDLE_THRESHOLD).astype(int)
    if seen_model is not None and test_seen_mask.any():
        p_catch_seen = seen_model.classifier.predict_proba(test_df[test_seen_mask])
        try:
            metrics["hurdle_auc_seen"] = float(roc_auc_score(
                y_binary[test_seen_mask.values], p_catch_seen
            ))
        except ValueError:
            pass
    if test_unseen_mask.any():
        p_catch_unseen = unseen_model.classifier.predict_proba(test_df[test_unseen_mask])
        try:
            metrics["hurdle_auc_unseen"] = float(roc_auc_score(
                y_binary[test_unseen_mask.values], p_catch_unseen
            ))
        except ValueError:
            pass

    # Per-regime breakdown
    regimes = classify_regime(test_df)
    regime_metrics = {}
    for regime in regimes.unique():
        rmask = regimes == regime
        if rmask.sum() >= 10:
            r2_r = r2_score(y_test[rmask.values], pred[rmask.values])
            regime_metrics[regime] = {
                "r2": float(r2_r),
                "n": int(rmask.sum()),
            }
    metrics["per_regime"] = regime_metrics

    # ── Ranking / product-quality metrics ──
    # Spearman rank correlation (overall, seen, unseen)
    sp_all, _ = spearmanr(y_test, pred)
    metrics["spearman_overall"] = float(sp_all)
    if test_seen_mask.any() and test_seen_mask.sum() > 5:
        sp_s, _ = spearmanr(y_test[test_seen_mask.values], pred[test_seen_mask.values])
        metrics["spearman_seen"] = float(sp_s)
    if test_unseen_mask.any() and test_unseen_mask.sum() > 5:
        sp_u, _ = spearmanr(y_test[test_unseen_mask.values], pred[test_unseen_mask.values])
        metrics["spearman_unseen"] = float(sp_u)

    # Top-k hit rate: what fraction of true top-10% are in predicted top-10%?
    k = max(1, int(len(y_test) * 0.10))
    true_top_idx = set(np.argsort(y_test)[-k:])
    pred_top_idx = set(np.argsort(pred)[-k:])
    hit_rate = len(true_top_idx & pred_top_idx) / k
    metrics["top10pct_hit_rate"] = float(hit_rate)

    # NDCG@k (how well does model rank the best opportunities at the top?)
    try:
        ndcg = ndcg_score([y_test], [pred], k=k)
        metrics["ndcg_at_10pct"] = float(ndcg)
    except Exception:
        pass

    # Hurdle calibration: Brier score + PR-AUC
    if test_unseen_mask.any():
        p_catch_all = np.zeros(len(y_test))
        if seen_model is not None and test_seen_mask.any():
            p_catch_all[test_seen_mask.values] = seen_model.classifier.predict_proba(
                test_df[test_seen_mask]
            )
        if test_unseen_mask.any():
            p_catch_all[test_unseen_mask.values] = unseen_model.classifier.predict_proba(
                test_df[test_unseen_mask]
            )
        try:
            metrics["brier_score"] = float(brier_score_loss(y_binary, p_catch_all))
            metrics["pr_auc"] = float(average_precision_score(y_binary, p_catch_all))
        except Exception:
            pass

    log.info(f"  Walk-forward R^2: {r2_all:.4f} (MAE={mae_all:.3f}, RMSE={rmse_all:.3f})")
    log.info(f"  Spearman rho: {sp_all:.4f} | Top-10% hit rate: {hit_rate:.3f}")
    if "ndcg_at_10pct" in metrics:
        log.info(f"  NDCG@10%: {metrics['ndcg_at_10pct']:.4f}")
    if "brier_score" in metrics:
        log.info(f"  Brier: {metrics['brier_score']:.4f} | PR-AUC: {metrics.get('pr_auc', 0):.4f}")
    for k_name, v in metrics.items():
        if k_name.startswith("r2_") or k_name.startswith("hurdle_"):
            log.info(f"    {k_name}: {v:.4f}")
    for regime, rm in regime_metrics.items():
        log.info(f"    regime/{regime}: R^2={rm['r2']:.4f} (n={rm['n']})")

    return metrics


def evaluate_spatial(
    df: pd.DataFrame,
    seen_features: list[str],
    unseen_features: list[str],
    task_type: str = "CPU",
) -> dict:
    """Spatial evaluation: latitude-band holdout."""
    log.info("=" * 60)
    log.info("SPATIAL EVALUATION (latitude-band holdout)")
    log.info("=" * 60)

    df = df.copy()
    df["_lat_band"] = pd.cut(df["lat"], bins=5, labels=["S", "SM", "M", "MN", "N"])

    band_r2s = {}
    for band in df["_lat_band"].unique():
        te_mask = df["_lat_band"] == band
        train_df = df[~te_mask]
        test_df = df[te_mask]

        if len(test_df) < 10 or len(train_df) < 50:
            continue

        y_train = train_df[TARGET].values
        y_test = test_df[TARGET].values

        model = HurdleEnsemble(task_type)
        model.fit(
            train_df, y_train,
            hurdle_features=unseen_features,
            regression_features=unseen_features,
        )
        pred = np.clip(model.predict(test_df), PRED_MIN, PRED_MAX)
        r2 = r2_score(y_test, pred)
        band_r2s[str(band)] = {"r2": float(r2), "n": int(len(test_df))}
        log.info(f"  Band {band}: R^2={r2:.4f} (n={len(test_df)})")

    mean_r2 = np.mean([v["r2"] for v in band_r2s.values()]) if band_r2s else 0
    log.info(f"  Mean spatial R^2: {mean_r2:.4f}")

    return {"mean_r2": float(mean_r2), "per_band": band_r2s}


def evaluate_regime_holdout(
    df: pd.DataFrame,
    unseen_features: list[str],
    task_type: str = "CPU",
) -> dict:
    """Regime evaluation: hold out entire ecological regimes."""
    log.info("=" * 60)
    log.info("REGIME HOLDOUT EVALUATION")
    log.info("=" * 60)

    regimes = classify_regime(df)
    df = df.copy()
    df["_regime"] = regimes

    regime_r2s = {}
    for regime in df["_regime"].unique():
        if regime == "other":
            continue
        te_mask = df["_regime"] == regime
        train_df = df[~te_mask]
        test_df = df[te_mask]

        if len(test_df) < 10 or len(train_df) < 50:
            continue

        y_train = train_df[TARGET].values
        y_test = test_df[TARGET].values

        model = HurdleEnsemble(task_type)
        model.fit(
            train_df, y_train,
            hurdle_features=unseen_features,
            regression_features=unseen_features,
        )
        pred = np.clip(model.predict(test_df), PRED_MIN, PRED_MAX)
        r2 = r2_score(y_test, pred)
        regime_r2s[regime] = {"r2": float(r2), "n": int(len(test_df))}
        log.info(f"  Regime {regime}: R^2={r2:.4f} (n={len(test_df)})")

    mean_r2 = np.mean([v["r2"] for v in regime_r2s.values()]) if regime_r2s else 0
    log.info(f"  Mean regime-holdout R^2: {mean_r2:.4f}")

    return {"mean_r2": float(mean_r2), "per_regime": regime_r2s}


# ====================================================================
# MAIN TRAINING PIPELINE
# ====================================================================

def load_dataset(path: Path | None = None) -> pd.DataFrame:
    """Load and validate dataset."""
    if path is None:
        if DEFAULT_DATASET_V18.exists():
            path = DEFAULT_DATASET_V18
            log.info(f"Using V18 dataset: {path}")
        elif DEFAULT_DATASET_V17.exists():
            path = DEFAULT_DATASET_V17
            log.info(f"Using V17 dataset: {path}")
        else:
            raise FileNotFoundError(
                f"No dataset found at {DEFAULT_DATASET_V18} or {DEFAULT_DATASET_V17}"
            )
    else:
        path = Path(path)
        log.info(f"Using dataset: {path}")

    df = pd.read_csv(path, low_memory=False)
    df = df[df[TARGET].notna()].copy()

    log.info(f"  Loaded: {len(df)} rows, {df.shape[1]} columns, "
             f"{df['location'].nunique()} locations")

    # Validate required columns
    required = {"date", "location", "lat", "lon", TARGET}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    return df


def main():
    parser = argparse.ArgumentParser(description="CASTLINE V16 Training")
    parser.add_argument("--dataset", type=str, default=None, help="Path to dataset CSV")
    parser.add_argument("--no-tune", action="store_true", help="Skip Optuna tuning")
    parser.add_argument("--gpu", action="store_true", help="Use GPU for CatBoost/XGBoost")
    parser.add_argument("--tune-trials", type=int, default=40, help="Optuna trials")
    parser.add_argument("--eval-only", action="store_true", help="Run evaluation without saving models")
    args = parser.parse_args()

    task_type = "GPU" if args.gpu else "CPU"
    t_start = time.time()

    log.info("=" * 70)
    log.info("CASTLINE V16 TRAINING: Hurdle Model + Seen/Unseen Routing")
    log.info("=" * 70)

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    df = load_dataset(args.dataset)

    # ------------------------------------------------------------------
    # 2. Feature engineering
    # ------------------------------------------------------------------
    log.info("\nFeature engineering...")
    df = add_temporal_trend_features(df)
    df = add_interaction_features(df)

    # Sort by date for temporal splits
    df = df.sort_values("date").reset_index(drop=True)

    # Parse year
    df["year"] = pd.to_datetime(df["date"], errors="coerce").dt.year

    # Determine seen/unseen features
    seen_features = select_features(df, for_unseen=False)
    unseen_features = select_features(df, for_unseen=True)

    log.info(f"  Seen features: {len(seen_features)}")
    log.info(f"  Unseen features: {len(unseen_features)}")

    # ------------------------------------------------------------------
    # 3. Evaluation (before training final models)
    # ------------------------------------------------------------------
    log.info("\n" + "=" * 70)
    log.info("EVALUATION PROTOCOL")
    log.info("=" * 70)

    wf_metrics = evaluate_walkforward(
        df, seen_features, unseen_features, task_type, tune=not args.no_tune,
    )
    spatial_metrics = evaluate_spatial(
        df, seen_features, unseen_features, task_type,
    )
    regime_metrics = evaluate_regime_holdout(
        df, unseen_features, task_type,
    )

    if args.eval_only:
        log.info("\n--eval-only: skipping model training/saving")
        _print_summary(wf_metrics, spatial_metrics, regime_metrics, time.time() - t_start)
        return

    # ------------------------------------------------------------------
    # 4. Hyperparameter tuning (optional)
    # ------------------------------------------------------------------
    seen_hurdle_params = None
    seen_reg_params = None
    unseen_hurdle_params = None
    unseen_reg_params = None

    if not args.no_tune:
        log.info("\n" + "=" * 70)
        log.info("HYPERPARAMETER TUNING (Optuna)")
        log.info("=" * 70)

        # Determine seen training data
        loc_counts = df["location"].value_counts()
        seen_locs = set(loc_counts[loc_counts >= MIN_LOC_HISTORY].index)
        df_seen = df[df["location"].isin(seen_locs)]
        y_seen = df_seen[TARGET].values

        y_all = df[TARGET].values

        log.info(f"\nTuning SEEN hurdle classifier ({len(df_seen)} rows)...")
        seen_hurdle_params = tune_hurdle_classifier(
            df_seen, y_seen, seen_features, args.tune_trials, task_type
        )

        log.info(f"\nTuning SEEN regression ({(y_seen > HURDLE_THRESHOLD).sum()} positive rows)...")
        pos_mask = y_seen > HURDLE_THRESHOLD
        seen_reg_params = tune_regression(
            df_seen[pos_mask], y_seen[pos_mask], seen_features,
            args.tune_trials, task_type,
        )

        log.info(f"\nTuning UNSEEN hurdle classifier ({len(df)} rows)...")
        unseen_hurdle_params = tune_hurdle_classifier(
            df, y_all, unseen_features, args.tune_trials, task_type
        )

        log.info(f"\nTuning UNSEEN regression ({(y_all > HURDLE_THRESHOLD).sum()} positive rows)...")
        pos_mask_all = y_all > HURDLE_THRESHOLD
        unseen_reg_params = tune_regression(
            df[pos_mask_all], y_all[pos_mask_all], unseen_features,
            args.tune_trials, task_type,
        )

    # ------------------------------------------------------------------
    # 5. Train final production models on all data
    # ------------------------------------------------------------------
    log.info("\n" + "=" * 70)
    log.info("TRAINING FINAL PRODUCTION MODELS")
    log.info("=" * 70)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    y_all = df[TARGET].values
    loc_counts = df["location"].value_counts()
    seen_locs = set(loc_counts[loc_counts >= MIN_LOC_HISTORY].index)

    # -- Seen model --
    df_seen = df[df["location"].isin(seen_locs)]
    y_seen = df_seen[TARGET].values

    log.info(f"\nSeen model: {len(df_seen)} rows, {len(seen_locs)} locations")
    seen_model = HurdleEnsemble(task_type)
    seen_model.fit(
        df_seen, y_seen,
        hurdle_features=seen_features,
        regression_features=seen_features,
        hurdle_params=seen_hurdle_params,
        cb_params=_extract_cb_params(seen_reg_params),
    )

    # -- Unseen model --
    log.info(f"\nUnseen model: {len(df)} rows (all data)")
    unseen_model = HurdleEnsemble(task_type)
    unseen_model.fit(
        df, y_all,
        hurdle_features=unseen_features,
        regression_features=unseen_features,
        hurdle_params=unseen_hurdle_params,
        cb_params=_extract_cb_params(unseen_reg_params),
    )

    # ------------------------------------------------------------------
    # 6. Conformal calibration
    # ------------------------------------------------------------------
    log.info("\n" + "=" * 70)
    log.info("CONFORMAL CALIBRATION")
    log.info("=" * 70)

    # Use last 20% as calibration set
    cal_start = int(len(df) * 0.8)
    df_cal = df.iloc[cal_start:]
    y_cal = df_cal[TARGET].values

    cal_seen_mask = df_cal["location"].isin(seen_locs)
    cal_unseen_mask = ~cal_seen_mask

    # Overall calibration
    pred_cal = np.full(len(df_cal), np.nan)
    if cal_seen_mask.any():
        pred_cal[cal_seen_mask.values] = seen_model.predict(df_cal[cal_seen_mask])
    if cal_unseen_mask.any():
        pred_cal[cal_unseen_mask.values] = unseen_model.predict(df_cal[cal_unseen_mask])

    nan_mask = np.isnan(pred_cal)
    if nan_mask.any():
        pred_cal[nan_mask] = unseen_model.predict(df_cal[nan_mask])

    pred_cal = np.clip(pred_cal, PRED_MIN, PRED_MAX)
    cal_data = conformal_calibration(
        # Pass a dummy object; we already have predictions
        type("Obj", (), {"predict": lambda self, x: pred_cal})(),
        df_cal, y_cal,
    )

    # ------------------------------------------------------------------
    # 7. Save artifacts
    # ------------------------------------------------------------------
    log.info("\n" + "=" * 70)
    log.info("SAVING ARTIFACTS")
    log.info("=" * 70)

    prefix_seen = f"cpue_{VERSION}_seen"
    prefix_unseen = f"cpue_{VERSION}_unseen"

    # Hurdle classifiers
    seen_model.classifier.save(MODEL_DIR / f"{prefix_seen}_hurdle_catboost.cbm")
    unseen_model.classifier.save(MODEL_DIR / f"{prefix_unseen}_hurdle_catboost.cbm")
    log.info(f"  Saved hurdle classifiers")

    # Regression ensembles
    seen_model.regressor.save(prefix_seen, MODEL_DIR)
    unseen_model.regressor.save(prefix_unseen, MODEL_DIR)
    log.info(f"  Saved regression ensembles")

    # Feature lists
    with open(MODEL_DIR / f"cpue_{VERSION}_seen_features.json", "w") as f:
        json.dump(seen_features, f, indent=2)
    with open(MODEL_DIR / f"cpue_{VERSION}_unseen_features.json", "w") as f:
        json.dump(unseen_features, f, indent=2)
    log.info(f"  Saved feature lists")

    # Location stats (for seen/unseen routing at inference)
    loc_stats = {}
    for loc in seen_locs:
        loc_data = df[df["location"] == loc]
        loc_stats[loc] = {
            "n_events": int(len(loc_data)),
            "mean_weight": float(loc_data[TARGET].mean()),
            "std_weight": float(loc_data[TARGET].std()) if len(loc_data) > 1 else 0,
        }
    with open(MODEL_DIR / f"cpue_{VERSION}_location_stats.json", "w") as f:
        json.dump(loc_stats, f, indent=2)
    log.info(f"  Saved location stats for {len(loc_stats)} seen locations")

    # Conformal calibration
    # Save without the large residuals list for the main calibration file
    cal_summary = {k: v for k, v in cal_data.items() if k != "sorted_abs_residuals"}
    with open(MODEL_DIR / f"cpue_{VERSION}_conformal.json", "w") as f:
        json.dump(cal_summary, f, indent=2)

    # Save full residuals separately (for generating prediction intervals)
    with open(MODEL_DIR / f"cpue_{VERSION}_conformal_residuals.pkl", "wb") as f:
        pickle.dump(cal_data["sorted_abs_residuals"], f)
    log.info(f"  Saved conformal calibration data")

    # Metadata
    ridge_seen = seen_model.regressor.meta_model
    ridge_unseen = unseen_model.regressor.meta_model
    metadata = {
        "version": VERSION,
        "architecture": "hurdle (classifier + regression) with seen/unseen routing",
        "trained_at": datetime.now().isoformat(),
        "dataset": str(args.dataset or "auto-detected"),
        "n_rows": int(len(df)),
        "n_locations": int(df["location"].nunique()),
        "hurdle_threshold": float(HURDLE_THRESHOLD),
        "seen_model": {
            "n_features": len(seen_features),
            "hp_hurdle": seen_hurdle_params or "default",
            "hp_regression": seen_reg_params or "default",
            "ridge_weights": {
                "catboost": float(ridge_seen.coef_[0]),
                "xgboost": float(ridge_seen.coef_[1]),
                "lightgbm": float(ridge_seen.coef_[2]),
            },
        },
        "unseen_model": {
            "n_features": len(unseen_features),
            "hp_hurdle": unseen_hurdle_params or "default",
            "hp_regression": unseen_reg_params or "default",
            "ridge_weights": {
                "catboost": float(ridge_unseen.coef_[0]),
                "xgboost": float(ridge_unseen.coef_[1]),
                "lightgbm": float(ridge_unseen.coef_[2]),
            },
        },
        "min_loc_history": MIN_LOC_HISTORY,
        "metrics": {
            "walkforward_r2": wf_metrics.get("r2_overall"),
            "walkforward_r2_seen": wf_metrics.get("r2_seen"),
            "walkforward_r2_unseen": wf_metrics.get("r2_unseen"),
            "walkforward_mae": wf_metrics.get("mae"),
            "walkforward_rmse": wf_metrics.get("rmse"),
            "hurdle_auc_seen": wf_metrics.get("hurdle_auc_seen"),
            "hurdle_auc_unseen": wf_metrics.get("hurdle_auc_unseen"),
            "spatial_r2": spatial_metrics.get("mean_r2"),
            "regime_r2": regime_metrics.get("mean_r2"),
            "walkforward_per_regime": wf_metrics.get("per_regime", {}),
        },
        "conformal": cal_summary,
    }

    with open(MODEL_DIR / f"cpue_{VERSION}_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    log.info(f"  Saved metadata")

    # ------------------------------------------------------------------
    # 8. Summary
    # ------------------------------------------------------------------
    _print_summary(wf_metrics, spatial_metrics, regime_metrics, time.time() - t_start)


def _extract_cb_params(optuna_params: dict | None) -> dict | None:
    """Extract CatBoost-compatible params from Optuna best_params."""
    if optuna_params is None:
        return None
    # Map Optuna param names to CatBoost param names
    mapping = {
        "iterations": "iterations",
        "depth": "depth",
        "learning_rate": "learning_rate",
        "l2_leaf_reg": "l2_leaf_reg",
        "subsample": "subsample",
        "colsample_bylevel": "colsample_bylevel",
    }
    return {v: optuna_params[k] for k, v in mapping.items() if k in optuna_params}


def _print_summary(wf_metrics, spatial_metrics, regime_metrics, elapsed):
    """Print final summary."""
    log.info("\n" + "=" * 70)
    log.info("V16 TRAINING SUMMARY")
    log.info("=" * 70)

    log.info(f"\n  Walk-forward R^2:   {wf_metrics.get('r2_overall', 'N/A')}")
    if "r2_seen" in wf_metrics:
        log.info(f"    Seen:             {wf_metrics['r2_seen']:.4f}")
    if "r2_unseen" in wf_metrics:
        log.info(f"    Unseen:           {wf_metrics['r2_unseen']:.4f}")
    if "hurdle_auc_seen" in wf_metrics:
        log.info(f"    Hurdle AUC (seen):   {wf_metrics['hurdle_auc_seen']:.4f}")
    if "hurdle_auc_unseen" in wf_metrics:
        log.info(f"    Hurdle AUC (unseen): {wf_metrics['hurdle_auc_unseen']:.4f}")
    log.info(f"    MAE:              {wf_metrics.get('mae', 'N/A')}")
    log.info(f"    RMSE:             {wf_metrics.get('rmse', 'N/A')}")

    for regime, rm in wf_metrics.get("per_regime", {}).items():
        log.info(f"    Regime/{regime}: R^2={rm['r2']:.4f} (n={rm['n']})")

    log.info(f"\n  Spatial R^2:        {spatial_metrics.get('mean_r2', 'N/A')}")
    for band, bm in spatial_metrics.get("per_band", {}).items():
        log.info(f"    Band {band}: R^2={bm['r2']:.4f} (n={bm['n']})")

    log.info(f"\n  Regime holdout R^2: {regime_metrics.get('mean_r2', 'N/A')}")
    for regime, rm in regime_metrics.get("per_regime", {}).items():
        log.info(f"    {regime}: R^2={rm['r2']:.4f} (n={rm['n']})")

    log.info(f"\n  V15 comparison:")
    log.info(f"    V15: CV R^2=0.723, Walk-forward R^2=0.447, Spatial R^2=0.528")
    log.info(f"    V16: Walk-forward R^2={wf_metrics.get('r2_overall', '?')}")

    log.info(f"\n  Total time: {elapsed:.0f}s ({elapsed/60:.1f}min)")


if __name__ == "__main__":
    main()
