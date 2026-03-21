"""Two-Model Inference Pipeline (V14/V15+).

Loads the seen-location and unseen-location models from training.
Routes predictions based on whether a location has historical data.

Usage:
    from castline.models.inference_v14 import V14Predictor

    predictor = V14Predictor.load("/path/to/models")               # loads latest version
    predictor = V14Predictor.load("/path/to/models", version="v15") # explicit version
    result = predictor.predict(location="Lake Guntersville, AL", date="2025-04-15")
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _try_import_shap():
    """Try to import shap; return module or None."""
    try:
        import shap
        return shap
    except ImportError:
        return None


# Human-readable feature labels (subset; full map in validation.models.inference)
_FEATURE_LABELS: dict[str, str] = {
    "water_temp_c": "Water temperature",
    "discharge_cfs": "River discharge",
    "loc_rolling_mean": "Location trend",
    "loc_rolling_std": "Location variability",
    "loc_n_prior": "Location history depth",
    "day_number": "Day number",
    "days_since_start": "Days since start",
    "days_from_end": "Days remaining",
    "creel_cpue_mean": "Average catch rate",
    "moon_phase_score": "Moon phase",
    "solunar_score": "Solunar activity",
    "air_temp_c": "Air temperature",
    "wind_speed_kph": "Wind speed",
    "pressure_mb": "Barometric pressure",
    "area_acres": "Lake area",
    "shore_dev": "Shoreline complexity",
    "reservoir_score": "Reservoir score",
    "catch_potential_index": "Catch potential",
    "latitude_growth_potential": "Growth potential (lat)",
    "photoperiod_hrs": "Photoperiod",
    "photoperiod_spawn_proximity": "Spawn proximity",
    "day_of_year": "Day of year",
    "year": "Year",
    "lon": "Longitude",
    # V15 enhanced features
    "creel2_cpue_hour_max": "Peak local catch rate",
    "creel2_cpue_hour_median": "Typical local catch rate",
    "creel2_cpue_day_median": "Daily catch rate (local)",
    "creel2_nearby_waterbodies": "Nearby waterbodies",
    "creel2_best_smb_cpue": "Best smallmouth rate",
    "creel2_best_lmb_cpue": "Best largemouth rate",
    "creel2_bass_species_count": "Bass species present",
    "creel2_total_surveys_nearby": "Survey coverage",
    "creel2_has_spotted": "Spotted bass present",
    "morph_completeness": "Lake data completeness",
    "depth_area_ratio": "Depth-to-area ratio",
    "northern_trophy_potential": "Trophy potential",
    "is_spawn_window": "Spawn window active",
    "is_largemouth_water": "Largemouth water",
    "is_smallmouth_water": "Smallmouth water",
    "precip_fishing_effect": "Rain impact",
    "temp_delta_1d": "24h temp change",
    "lag_diurnal_mean": "Day/night temp swing",
    "seasonal_pattern_phase": "Seasonal phase",
    "data_quality_score": "Data quality",
    "loc_encoding_confidence": "Location data confidence",
    "loc_tournament_fraction": "Tournament fraction",
    "loc_last_year": "Last year's catch",
    "source_rolling_mean": "Source trend",
    "light_penetration_index": "Water clarity",
    "wqp_secchi_depth_m": "Secchi depth (clarity)",
    "has_wqp_data": "Water quality data available",
    "moon_phase_cos": "Moon phase",
    "lagos_glaciated": "Glaciated lake",
    "lagos_mbg_length_m": "Lake maximum length",
    "usgs_largemouth_bass_presence": "USGS bass presence",
    "usgs_reaches_nearby": "Stream reaches nearby",
    "photoperiod_change_rate": "Day length change rate",
    "photoperiod_change_min": "Day length change",
}


def _feature_label(name: str) -> str:
    """Return a human-readable label for a feature name."""
    if name in _FEATURE_LABELS:
        return _FEATURE_LABELS[name]
    return name.replace("_", " ").replace("geoclip", "location embedding").title()


@dataclass
class FeatureContribution:
    """A single feature's contribution to a prediction."""
    feature_name: str
    label: str
    shap_value: float
    direction: str  # "positive" or "negative"

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature_name,
            "label": self.label,
            "impact": round(self.shap_value, 4),
            "direction": self.direction,
        }


@dataclass
class PredictionExplanation:
    """SHAP-based explanation for a single prediction."""
    top_features: list[FeatureContribution]
    base_value: float
    prediction_value: float
    method: str  # "shap" or "importance_fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_features": [f.to_dict() for f in self.top_features],
            "base_value": round(self.base_value, 4),
            "prediction_value": round(self.prediction_value, 4),
            "method": self.method,
        }

    def to_explanation_string(self) -> str:
        """Format as a human-readable string for the mobile ExplanationCard."""
        if not self.top_features:
            return "Insufficient data for detailed explanation."

        parts: list[str] = []
        positive = [f for f in self.top_features if f.direction == "positive"]
        negative = [f for f in self.top_features if f.direction == "negative"]

        if positive:
            labels = [f.label for f in positive[:3]]
            if len(labels) == 1:
                parts.append(f"{labels[0]} is boosting the forecast.")
            else:
                parts.append(
                    f"{', '.join(labels[:-1])} and {labels[-1]} are boosting the forecast."
                )

        if negative:
            labels = [f.label for f in negative[:3]]
            if len(labels) == 1:
                parts.append(f"{labels[0]} is holding it back.")
            else:
                parts.append(
                    f"{', '.join(labels[:-1])} and {labels[-1]} are holding it back."
                )

        return " ".join(parts)


@dataclass
class PredictionInterval:
    """Calibrated prediction interval from conformal inference."""
    lower_bound: float
    upper_bound: float
    interval_width: float
    margin: float  # half-width, for display as "+/- X"
    coverage_target: float  # e.g. 0.90
    method: str  # "conformal" or "geo_conformal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower_bound": round(self.lower_bound, 2),
            "upper_bound": round(self.upper_bound, 2),
            "interval_width": round(self.interval_width, 2),
            "margin": round(self.margin, 2),
            "coverage_target": self.coverage_target,
            "method": self.method,
        }


@dataclass
class V14PredictionResult:
    location: str
    date: str
    predicted_weight_lb: float
    fishing_score: int
    model_route: str  # "seen" or "unseen"
    confidence: float
    explanation: str
    prediction_interval: PredictionInterval | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "location": self.location,
            "date": self.date,
            "predicted_weight_lb": round(self.predicted_weight_lb, 2),
            "fishing_score": self.fishing_score,
            "model_route": self.model_route,
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
        }
        if self.prediction_interval is not None:
            d["prediction_interval"] = self.prediction_interval.to_dict()
        return d


class V14Predictor:
    """Two-model predictor: routes to seen or unseen model based on location history."""

    MIN_LOC_HISTORY = 3  # same as training

    def __init__(
        self,
        seen_models: dict[str, Any] | None = None,
        unseen_models: dict[str, Any] | None = None,
        seen_features: list[str] | None = None,
        unseen_features: list[str] | None = None,
        seen_ridge: Any = None,
        unseen_ridge: Any = None,
        location_stats: dict[str, dict] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.seen_models = seen_models or {}
        self.unseen_models = unseen_models or {}
        self.seen_features = seen_features or []
        self.unseen_features = unseen_features or []
        self.seen_ridge = seen_ridge
        self.unseen_ridge = unseen_ridge
        self.location_stats = location_stats or {}
        self.metadata = metadata or {}

        # Conformal predictor (loaded separately via load_conformal)
        self.conformal = None  # type: Any | None

        # For backwards compat with predictions.py
        self.location_means = {
            loc: stats["mean_weight"]
            for loc, stats in self.location_stats.items()
        }
        self.feature_names = self.seen_features
        self.model_metadata = self.metadata

    @classmethod
    def load(cls, model_dir: str | Path, version: str | None = None) -> V14Predictor:
        """Load two-model artifacts from disk.

        Args:
            model_dir: Directory containing model artifacts.
            version: Model version to load ("v14", "v15", etc.).
                     If None, auto-detects latest available version.
        """
        model_dir = Path(model_dir)
        if version is None:
            # Auto-detect latest version by checking for metadata files
            for v in ("v15", "v14"):
                if (model_dir / f"cpue_{v}_metadata.json").exists():
                    version = v
                    break
            else:
                version = "v14"
        logger.info("Loading %s model from %s", version, model_dir)

        # Seen models
        seen_models = {}
        cb_path = model_dir / f"cpue_{version}_seen_catboost.cbm"
        if cb_path.exists():
            from catboost import CatBoostRegressor
            cb = CatBoostRegressor()
            cb.load_model(str(cb_path))
            seen_models["catboost"] = cb

        for name in ("xgboost", "lightgbm"):
            pkl_path = model_dir / f"cpue_{version}_seen_{name}.pkl"
            if pkl_path.exists():
                with open(pkl_path, "rb") as f:
                    seen_models[name] = pickle.load(f)

        # Unseen models
        unseen_models = {}
        cb_path = model_dir / f"cpue_{version}_unseen_catboost.cbm"
        if cb_path.exists():
            from catboost import CatBoostRegressor
            cb = CatBoostRegressor()
            cb.load_model(str(cb_path))
            unseen_models["catboost"] = cb

        for name in ("xgboost", "lightgbm"):
            pkl_path = model_dir / f"cpue_{version}_unseen_{name}.pkl"
            if pkl_path.exists():
                with open(pkl_path, "rb") as f:
                    unseen_models[name] = pickle.load(f)

        # Ridge meta-learners
        seen_ridge = None
        unseen_ridge = None
        for label, var in [("seen", "seen_ridge"), ("unseen", "unseen_ridge")]:
            pkl_path = model_dir / f"cpue_{version}_{label}_ridge.pkl"
            if pkl_path.exists():
                with open(pkl_path, "rb") as f:
                    if label == "seen":
                        seen_ridge = pickle.load(f)
                    else:
                        unseen_ridge = pickle.load(f)

        # Feature lists
        seen_features = []
        unseen_features = []
        for label in ("seen", "unseen"):
            feat_path = model_dir / f"cpue_{version}_{label}_features.json"
            if feat_path.exists():
                with open(feat_path) as f:
                    if label == "seen":
                        seen_features = json.load(f)
                    else:
                        unseen_features = json.load(f)

        # Location stats
        location_stats = {}
        stats_path = model_dir / f"cpue_{version}_location_stats.json"
        if stats_path.exists():
            with open(stats_path) as f:
                location_stats = json.load(f)

        # Metadata
        metadata = {}
        meta_path = model_dir / f"cpue_{version}_metadata.json"
        if meta_path.exists():
            with open(meta_path) as f:
                metadata = json.load(f)

        return cls(
            seen_models=seen_models,
            unseen_models=unseen_models,
            seen_features=seen_features,
            unseen_features=unseen_features,
            seen_ridge=seen_ridge,
            unseen_ridge=unseen_ridge,
            location_stats=location_stats,
            metadata=metadata,
        )

    def is_seen_location(self, location: str) -> bool:
        """Check if a location has enough history to use the seen model."""
        stats = self.location_stats.get(location)
        if stats is None:
            return False
        return stats.get("n_events", 0) >= self.MIN_LOC_HISTORY

    def _stacked_predict(self, features_df: pd.DataFrame, models: dict, ridge, feature_list: list) -> np.ndarray:
        """Run stacked ensemble prediction."""
        # Add missing columns as NaN (real-time features may not include all model features)
        for col in feature_list:
            if col not in features_df.columns:
                features_df[col] = np.nan
        X = features_df[feature_list]
        predictions = []

        if "catboost" in models:
            predictions.append(models["catboost"].predict(X))

        if "xgboost" in models:
            predictions.append(models["xgboost"].predict(X.fillna(-999)))

        if "lightgbm" in models:
            predictions.append(models["lightgbm"].predict(X))

        if len(predictions) == 3 and ridge is not None:
            stack = np.column_stack(predictions)
            return ridge.predict(stack)
        elif predictions:
            return np.mean(predictions, axis=0)
        else:
            return np.zeros(len(X))

    def explain_prediction(
        self,
        features_df: pd.DataFrame,
        models: dict[str, Any],
        feature_list: list[str],
        top_n: int = 5,
    ) -> PredictionExplanation:
        """Explain a prediction using SHAP across the ensemble models.

        Averages SHAP values from CatBoost, XGBoost, and LightGBM.
        Falls back to model-level feature importance if SHAP is unavailable.

        Args:
            features_df: Single-row DataFrame with feature values.
            models: Dict of {"catboost": model, "xgboost": model, ...}.
            feature_list: Feature column names.
            top_n: Number of top contributing features to return.

        Returns:
            PredictionExplanation suitable for .to_explanation_string().
        """
        shap_mod = _try_import_shap()
        all_shap_values: list[np.ndarray] = []
        base_values: list[float] = []
        method = "shap"

        X = features_df[feature_list].iloc[[0]]

        for name, model in models.items():
            if shap_mod is not None:
                try:
                    explainer = shap_mod.TreeExplainer(model)
                    sv = explainer.shap_values(X)

                    if hasattr(sv, "values"):
                        vals = sv.values.flatten()
                    elif isinstance(sv, list):
                        vals = np.array(sv[0]).flatten()
                    else:
                        vals = np.array(sv).flatten()

                    bv = float(
                        explainer.expected_value
                        if isinstance(explainer.expected_value, (int, float, np.floating))
                        else explainer.expected_value[0]
                    )
                    all_shap_values.append(vals)
                    base_values.append(bv)
                    continue
                except Exception as exc:
                    logger.warning("SHAP failed for %s: %s", name, exc)

            # Fallback: use feature importance
            method = "importance_fallback"
            imps = None
            if hasattr(model, "feature_importances_") and model.feature_importances_ is not None:
                imps = np.array(model.feature_importances_, dtype=float)
            elif hasattr(model, "get_feature_importance"):
                raw = model.get_feature_importance()
                if raw is not None:
                    imps = np.array(raw, dtype=float)
            if imps is not None and imps.size > 0 and imps.size == len(feature_list):
                # Normalize to sum to 1
                total = float(imps.sum())
                if total > 0:
                    imps = imps / total
                all_shap_values.append(imps)
                base_values.append(0.0)

        if not all_shap_values:
            return PredictionExplanation(
                top_features=[], base_value=0.0, prediction_value=0.0,
                method="importance_fallback",
            )

        avg_vals = np.mean(all_shap_values, axis=0)
        avg_base = float(np.mean(base_values))

        indices = np.argsort(np.abs(avg_vals))[::-1][:top_n]
        contributions = []
        for idx in indices:
            sv = float(avg_vals[idx])
            fname = feature_list[idx]
            contributions.append(
                FeatureContribution(
                    feature_name=fname,
                    label=_feature_label(fname),
                    shap_value=sv,
                    direction="positive" if sv >= 0 else "negative",
                )
            )

        pred_value = avg_base + float(avg_vals.sum())
        return PredictionExplanation(
            top_features=contributions,
            base_value=avg_base,
            prediction_value=pred_value,
            method=method,
        )

    def predict_from_features(self, features_df: pd.DataFrame, location: str) -> np.ndarray:
        """Predict from a pre-built feature DataFrame."""
        if self.is_seen_location(location):
            return self._stacked_predict(
                features_df, self.seen_models, self.seen_ridge, self.seen_features
            )
        else:
            return self._stacked_predict(
                features_df, self.unseen_models, self.unseen_ridge, self.unseen_features
            )

    def predict(
        self,
        location: str,
        date: str,
        usgs_site_id: str = "",
        precomputed_features: dict[str, float] | None = None,
    ) -> V14PredictionResult:
        """Make a prediction for a location and date.

        If precomputed_features is provided, uses those directly.
        Otherwise, builds a minimal feature vector from available data.
        """
        route = "seen" if self.is_seen_location(location) else "unseen"
        models = self.seen_models if route == "seen" else self.unseen_models
        ridge = self.seen_ridge if route == "seen" else self.unseen_ridge
        feature_list = self.seen_features if route == "seen" else self.unseen_features

        if precomputed_features:
            df = pd.DataFrame([precomputed_features])
        else:
            # Build minimal feature vector
            df = pd.DataFrame([{f: np.nan for f in feature_list}])

            # Fill in what we can from location stats
            stats = self.location_stats.get(location, {})
            if "mean_weight" in stats:
                if "loc_rolling_mean" in feature_list:
                    df["loc_rolling_mean"] = stats["mean_weight"]
                if "loc_n_prior" in feature_list:
                    df["loc_n_prior"] = stats.get("n_events", 0)

        # Predict
        log_pred = self._stacked_predict(df, models, ridge, feature_list)
        predicted_weight = float(np.expm1(np.clip(log_pred[0], 0, 4)))

        # Compute fishing score
        hist_avg = self.location_means.get(location, 3.0)
        if hist_avg <= 0:
            hist_avg = 3.0
        ratio = predicted_weight / hist_avg
        fishing_score = max(0, min(100, int(100.0 / (1.0 + 2.718 ** (-3.5 * (ratio - 1.0))))))

        # Confidence based on route and data availability
        if route == "seen":
            n_events = self.location_stats.get(location, {}).get("n_events", 0)
            confidence = min(0.95, 0.5 + 0.05 * n_events)
        else:
            confidence = 0.35

        # Explanation: score-based summary + SHAP-driven feature detail
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

        # Append SHAP-based driver detail
        try:
            shap_expl = self.explain_prediction(df, models, feature_list)
            shap_text = shap_expl.to_explanation_string()
            if shap_text and shap_text != "Insufficient data for detailed explanation.":
                explanation = explanation + " " + shap_text
        except Exception:
            pass  # SHAP is optional — degrade gracefully

        if route == "unseen":
            explanation += " (New location — prediction based on weather/seasonal patterns only.)"

        # Attach conformal prediction interval if calibration is loaded
        interval = None
        if self.conformal is not None:
            try:
                # Get lat/lon from precomputed features or location stats
                lat = (precomputed_features or {}).get("lat")
                lon = (precomputed_features or {}).get("lon")
                if lat is None or lon is None:
                    stats = self.location_stats.get(location, {})
                    lat = stats.get("lat")
                    lon = stats.get("lon")

                unc = self.conformal.predict_with_interval(
                    df, location=location, lat=lat, lon=lon,
                )
                # Convert from model output space to weight space
                lo_weight = float(np.expm1(np.clip(unc.lower_bound, 0, 4)))
                hi_weight = float(np.expm1(np.clip(unc.upper_bound, 0, 4)))
                width = hi_weight - lo_weight
                margin = width / 2.0
                interval = PredictionInterval(
                    lower_bound=lo_weight,
                    upper_bound=hi_weight,
                    interval_width=width,
                    margin=margin,
                    coverage_target=unc.coverage_target,
                    method=unc.method,
                )
            except Exception as exc:
                logger.debug("Conformal interval failed: %s", exc)

        return V14PredictionResult(
            location=location,
            date=date,
            predicted_weight_lb=predicted_weight,
            fishing_score=fishing_score,
            model_route=route,
            confidence=confidence,
            explanation=explanation,
            prediction_interval=interval,
        )

    def load_conformal(self, path: str | Path) -> None:
        """Load conformal calibration data for uncertainty estimation.

        After calling this, all subsequent predict() calls will include
        a prediction_interval in the result.

        Args:
            path: Path to the conformal calibration JSON file
                  (e.g. cpue_v15_conformal.json).
        """
        from castline.models.conformal import ConformalPredictor
        self.conformal = ConformalPredictor.load(path, predictor=self)
        logger.info(
            "Conformal calibration loaded: q_hat=%.4f, n=%d",
            self.conformal.q_hat_, len(self.conformal.scores_),
        )

    def predict_with_uncertainty(
        self,
        location: str,
        date: str,
        usgs_site_id: str = "",
        precomputed_features: dict[str, float] | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> V14PredictionResult:
        """Predict with explicit uncertainty estimation.

        Convenience wrapper that ensures conformal intervals are included.
        If conformal calibration is not loaded, raises RuntimeError.

        Returns the same V14PredictionResult but guarantees
        prediction_interval is populated.
        """
        if self.conformal is None:
            raise RuntimeError(
                "Conformal calibration not loaded. "
                "Call predictor.load_conformal(path) first."
            )

        # Inject lat/lon into precomputed features if provided
        if lat is not None or lon is not None:
            pf = dict(precomputed_features or {})
            if lat is not None:
                pf["lat"] = lat
            if lon is not None:
                pf["lon"] = lon
            precomputed_features = pf

        result = self.predict(
            location=location,
            date=date,
            usgs_site_id=usgs_site_id,
            precomputed_features=precomputed_features,
        )

        if result.prediction_interval is None:
            raise RuntimeError(
                "Conformal interval was not generated despite calibration being loaded. "
                "Check logs for errors."
            )

        return result
