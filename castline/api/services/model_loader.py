"""Model loading utilities for the CASTLINE API.

Provides helpers to load different model versions (V13, V14) into a
unified predictor interface compatible with the prediction router.
"""

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class V13StackedPredictor:
    """Wrapper that loads V13 stacked ensemble artifacts and exposes
    the same interface as V14Predictor for the prediction router.

    V13 uses a single model route (no seen/unseen split) with
    CatBoost + XGBoost + LightGBM stacked via Ridge meta-learner.
    """

    def __init__(
        self,
        models: dict[str, Any],
        ridge: Any,
        feature_names: list[str],
        location_means: dict[str, float],
        metadata: dict[str, Any],
    ):
        self.models = models
        self.ridge = ridge
        self.feature_names = feature_names
        self.location_means = location_means
        self.model_metadata = metadata

    def is_seen_location(self, location: str) -> bool:
        return location in self.location_means

    def predict(
        self,
        location: str,
        date: str,
        usgs_site_id: str = "",
        precomputed_features: dict[str, float] | None = None,
    ):
        """Make a prediction and return a result object."""
        from castline.models.inference_v14 import V14PredictionResult

        if precomputed_features:
            df = pd.DataFrame([precomputed_features])
        else:
            # Build minimal feature vector with NaN defaults
            df = pd.DataFrame([{f: np.nan for f in self.feature_names}])

        # Fill location-level features
        loc_mean = self.location_means.get(location)
        if loc_mean is not None:
            if "loc_rolling_mean" in self.feature_names:
                df["loc_rolling_mean"] = loc_mean
            if "loc_n_prior" in self.feature_names:
                df["loc_n_prior"] = 5  # default for seen locations

        # Ensure all required columns exist
        for col in self.feature_names:
            if col not in df.columns:
                df[col] = np.nan

        # Run stacked ensemble
        X = df[self.feature_names]
        predictions = []

        if "catboost" in self.models:
            predictions.append(self.models["catboost"].predict(X))
        if "xgboost" in self.models:
            predictions.append(self.models["xgboost"].predict(X.fillna(-999)))
        if "lightgbm" in self.models:
            predictions.append(self.models["lightgbm"].predict(X))

        if len(predictions) == 3 and self.ridge is not None:
            stack = np.column_stack(predictions)
            log_pred = self.ridge.predict(stack)
        elif predictions:
            log_pred = np.mean(predictions, axis=0)
        else:
            log_pred = np.zeros(1)

        predicted_weight = float(np.expm1(np.clip(log_pred[0], 0, 4)))

        # Fishing score
        hist_avg = self.location_means.get(location, 3.0)
        if hist_avg <= 0:
            hist_avg = 3.0
        ratio = predicted_weight / hist_avg
        fishing_score = max(0, min(100, int(
            100.0 / (1.0 + 2.718 ** (-3.5 * (ratio - 1.0)))
        )))

        # Confidence
        route = "seen" if self.is_seen_location(location) else "unseen"
        if route == "seen":
            confidence = min(0.90, 0.5 + 0.04 * 5)
        else:
            confidence = 0.35

        # Explanation
        if fishing_score >= 80:
            explanation = "Excellent conditions expected — strong bite likely."
        elif fishing_score >= 60:
            explanation = "Good conditions — above-average activity likely."
        elif fishing_score >= 40:
            explanation = "Fair conditions — average fishing expected."
        elif fishing_score >= 20:
            explanation = "Below average — consider waiting for better conditions."
        else:
            explanation = "Poor conditions — most factors are unfavorable."

        if route == "unseen":
            explanation += " (New location — prediction based on regional patterns.)"

        return V14PredictionResult(
            location=location,
            date=date,
            predicted_weight_lb=predicted_weight,
            fishing_score=fishing_score,
            model_route=route,
            confidence=confidence,
            explanation=explanation,
        )


def load_v13_as_predictor(model_dir: str | Path) -> V13StackedPredictor:
    """Load V13 stacked ensemble artifacts and return a predictor."""
    model_dir = Path(model_dir)
    version = "v13"

    models = {}

    # CatBoost
    cb_path = model_dir / f"cpue_{version}_catboost.cbm"
    if cb_path.exists():
        from catboost import CatBoostRegressor
        cb = CatBoostRegressor()
        cb.load_model(str(cb_path))
        models["catboost"] = cb

    # XGBoost and LightGBM
    for name in ("xgboost", "lightgbm"):
        pkl_path = model_dir / f"cpue_{version}_{name}.pkl"
        if pkl_path.exists():
            with open(pkl_path, "rb") as f:
                models[name] = pickle.load(f)

    # Ridge meta-learner
    ridge = None
    ridge_path = model_dir / f"cpue_{version}_ridge.pkl"
    if ridge_path.exists():
        with open(ridge_path, "rb") as f:
            ridge = pickle.load(f)

    # Feature names
    feat_path = model_dir / f"cpue_{version}_features.json"
    feature_names = []
    if feat_path.exists():
        with open(feat_path) as f:
            feature_names = json.load(f)

    # Location means
    location_means = {}
    means_path = model_dir / f"cpue_{version}_location_means.json"
    if means_path.exists():
        with open(means_path) as f:
            location_means = json.load(f)

    # Metadata
    metadata = {}
    meta_path = model_dir / f"cpue_{version}_metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)

    metadata["model_version"] = version
    metadata["n_models"] = len(models)
    metadata["model_names"] = list(models.keys())

    return V13StackedPredictor(
        models=models,
        ridge=ridge,
        feature_names=feature_names,
        location_means=location_means,
        metadata=metadata,
    )
