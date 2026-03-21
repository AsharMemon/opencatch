"""Production inference pipeline for CASTLINE fishing predictions.

Provides a unified interface for making predictions using trained models
(HGB, TFT, or ensemble). Handles feature collection, preprocessing,
and prediction in a single call.

Usage:
    from castline.validation.models.inference import CastlinePredictor

    predictor = CastlinePredictor.load(model_dir)
    prediction = predictor.predict(
        location="Lake Guntersville, Guntersville, AL",
        date="2025-04-15",
        usgs_site_id="03572110",
    )
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human-readable feature name mapping for explanations
# ---------------------------------------------------------------------------
_FEATURE_LABELS: dict[str, str] = {
    "water_temp_c": "Water temperature",
    "water_temp_7d_mean": "7-day water temperature",
    "water_temp_anomaly": "Water temp anomaly",
    "water_temp_c_daily_range": "Daily water temp range",
    "water_temp_c_dawn": "Dawn water temperature",
    "water_temp_c_6h_delta": "Water temp change (6h)",
    "discharge_cfs": "River discharge",
    "discharge_7d_mean": "7-day discharge",
    "discharge_cfs_daily_range": "Daily discharge range",
    "discharge_cfs_rate_of_change": "Discharge rate of change",
    "flow_delta_24h_pct": "Flow change (24h %)",
    "gage_height_ft": "Water level",
    "gage_height_7d_mean": "7-day water level",
    "gage_height_ft_daily_range": "Daily water level range",
    "gage_height_ft_6h_delta": "Water level change (6h)",
    "gage_delta_24h_ft": "Water level change (24h)",
    "air_temp_c": "Air temperature",
    "pressure_mb": "Barometric pressure",
    "pressure_delta_6h": "Pressure trend (6h)",
    "pressure_trend_6h": "Pressure trend (6h)",
    "wind_speed_kph": "Wind speed",
    "cloud_cover_pct": "Cloud cover",
    "precip_24h_mm": "Recent precipitation",
    "moon_phase_sin": "Moon phase",
    "moon_phase_cos": "Moon phase cycle",
    "solunar_score": "Solunar activity",
    "moon_illumination_pct": "Moon brightness",
    "spawn_phase_score": "Spawn phase",
    "day_length_hours": "Day length",
    "season_sin": "Season",
    "season_cos": "Season cycle",
    "location_mean_weight": "Location baseline",
    "loc_enc": "Location average",
    "loc_rolling_mean": "Location trend",
    "loc_rolling_std": "Location variability",
    "loc_n_prior": "Location history depth",
    "baseline_signal": "Baseline activity signal",
    "creel_median_cpue": "Historical catch rate",
    "creel_cpue_mean": "Average catch rate",
    "dissolved_oxygen_mgL": "Dissolved oxygen",
    "turbidity_fnu": "Water clarity",
    "ph": "Water pH",
    "specific_conductance_us_cm": "Conductivity",
    "temp_delta_24h_c": "Temp change (24h)",
    "cumulative_degree_days": "Degree-day accumulation",
    "day_of_year": "Day of year",
    "day_number": "Day number",
    "days_since_start": "Days since start",
    "days_from_end": "Days remaining",
    "month": "Month",
    "year": "Year",
    "area_acres": "Lake area",
    "shore_dev": "Shoreline complexity",
    "reservoir_score": "Reservoir score",
    "catch_potential_index": "Catch potential",
    "latitude_growth_potential": "Growth potential (lat)",
    "photoperiod_hrs": "Photoperiod",
    "photoperiod_spawn_proximity": "Spawn proximity (photoperiod)",
    "hours_since_front": "Hours since weather front",
    "stability_index": "Weather stability",
    "discharge_zscore": "Discharge z-score",
    "flow_regime": "Flow regime",
    "spawn_activity": "Spawn activity",
    "peak_feed_activity": "Peak feeding activity",
    "trophy_potential": "Trophy potential",
}


def _feature_label(name: str) -> str:
    """Return a human-readable label for a feature name."""
    if name in _FEATURE_LABELS:
        return _FEATURE_LABELS[name]
    # Auto-generate from snake_case
    return name.replace("_", " ").replace("geoclip", "location embedding").title()


# ---------------------------------------------------------------------------
# SHAP-based feature importance explanation
# ---------------------------------------------------------------------------

def _try_import_shap():
    """Try to import shap; return module or None."""
    try:
        import shap
        return shap
    except ImportError:
        return None


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


class SHAPExplainer:
    """Compute SHAP-based explanations for gradient-boosted model predictions.

    Supports CatBoost, XGBoost, LightGBM, and scikit-learn
    HistGradientBoostingRegressor.  Falls back to model-level feature
    importance when SHAP is not installed.
    """

    def __init__(self, model: Any, feature_names: list[str], model_type: str = "auto"):
        """
        Args:
            model: A trained tree-based model.
            feature_names: List of feature names matching model input columns.
            model_type: One of "catboost", "xgboost", "lightgbm", "hgb", "auto".
        """
        self.model = model
        self.feature_names = feature_names
        self.model_type = model_type if model_type != "auto" else self._detect_type()
        self._explainer = None
        self._shap = _try_import_shap()

    def _detect_type(self) -> str:
        cls_name = type(self.model).__name__.lower()
        if "catboost" in cls_name:
            return "catboost"
        elif "xgb" in cls_name or "xgboost" in cls_name:
            return "xgboost"
        elif "lgbm" in cls_name or "lightgbm" in cls_name:
            return "lightgbm"
        elif "histgradient" in cls_name:
            return "hgb"
        return "unknown"

    def _get_explainer(self):
        """Lazily create the SHAP TreeExplainer."""
        if self._explainer is not None:
            return self._explainer
        if self._shap is None:
            return None
        try:
            self._explainer = self._shap.TreeExplainer(self.model)
            return self._explainer
        except Exception as exc:
            logger.warning("Failed to create SHAP TreeExplainer: %s", exc)
            return None

    def explain(
        self,
        features_df: pd.DataFrame,
        top_n: int = 5,
    ) -> PredictionExplanation:
        """Explain a single-row prediction.

        Args:
            features_df: DataFrame with one row, columns matching self.feature_names.
            top_n: Number of top features to return.

        Returns:
            PredictionExplanation with the top contributing features.
        """
        X = features_df[self.feature_names].iloc[[0]]

        explainer = self._get_explainer()
        if explainer is not None:
            return self._explain_shap(X, explainer, top_n)
        else:
            return self._explain_fallback(X, top_n)

    def _explain_shap(
        self,
        X: pd.DataFrame,
        explainer: Any,
        top_n: int,
    ) -> PredictionExplanation:
        """Explain using SHAP values."""
        shap_values = explainer.shap_values(X)

        # shap_values may be a single array or nested; flatten to 1-D
        if hasattr(shap_values, "values"):
            vals = shap_values.values.flatten()
        elif isinstance(shap_values, list):
            vals = np.array(shap_values[0]).flatten()
        else:
            vals = np.array(shap_values).flatten()

        base_value = float(
            explainer.expected_value
            if isinstance(explainer.expected_value, (int, float, np.floating))
            else explainer.expected_value[0]
        )

        # Rank by absolute SHAP value
        indices = np.argsort(np.abs(vals))[::-1][:top_n]

        contributions = []
        for idx in indices:
            sv = float(vals[idx])
            fname = self.feature_names[idx]
            contributions.append(
                FeatureContribution(
                    feature_name=fname,
                    label=_feature_label(fname),
                    shap_value=sv,
                    direction="positive" if sv >= 0 else "negative",
                )
            )

        pred_value = base_value + float(vals.sum())

        return PredictionExplanation(
            top_features=contributions,
            base_value=base_value,
            prediction_value=pred_value,
            method="shap",
        )

    def _explain_fallback(
        self,
        X: pd.DataFrame,
        top_n: int,
    ) -> PredictionExplanation:
        """Fallback: use model-level feature importance weighted by feature values."""
        importances = self._get_feature_importances()
        if importances is None:
            return PredictionExplanation(
                top_features=[],
                base_value=0.0,
                prediction_value=0.0,
                method="importance_fallback",
            )

        # Weight importance by deviation from 0 (crude directionality proxy)
        row = X.iloc[0]
        weighted = []
        for i, fname in enumerate(self.feature_names):
            imp = importances[i] if i < len(importances) else 0.0
            val = row.get(fname, 0.0)
            if isinstance(val, float) and (np.isnan(val) or val == 0.0):
                direction = "positive"  # unknown direction
                magnitude = imp
            else:
                direction = "positive" if val > 0 else "negative"
                magnitude = imp
            weighted.append((fname, magnitude, direction))

        weighted.sort(key=lambda x: abs(x[1]), reverse=True)

        contributions = []
        for fname, mag, direction in weighted[:top_n]:
            contributions.append(
                FeatureContribution(
                    feature_name=fname,
                    label=_feature_label(fname),
                    shap_value=mag,
                    direction=direction,
                )
            )

        pred = float(self.model.predict(X)[0]) if hasattr(self.model, "predict") else 0.0
        return PredictionExplanation(
            top_features=contributions,
            base_value=0.0,
            prediction_value=pred,
            method="importance_fallback",
        )

    def _get_feature_importances(self) -> np.ndarray | None:
        """Extract feature importances from the model."""
        # Standard sklearn / XGBoost / LightGBM
        if hasattr(self.model, "feature_importances_"):
            return np.array(self.model.feature_importances_)
        # CatBoost
        if hasattr(self.model, "get_feature_importance"):
            return np.array(self.model.get_feature_importance())
        # sklearn HistGradientBoosting: compute from internal tree structure
        if hasattr(self.model, "_predictors"):
            try:
                n_features = len(self.feature_names)
                importances = np.zeros(n_features)
                for predictor_list in self.model._predictors:
                    for predictor in predictor_list:
                        nodes = predictor.nodes
                        for node in nodes:
                            if node["is_leaf"]:
                                continue
                            feat_idx = int(node["feature_idx"])
                            gain = float(node["gain"])
                            if 0 <= feat_idx < n_features and gain > 0:
                                importances[feat_idx] += gain
                if importances.sum() > 0:
                    importances = importances / importances.sum()
                    return importances
            except Exception:
                pass
        return None


def explain_ensemble_prediction(
    models: dict[str, Any],
    feature_names: list[str],
    features_df: pd.DataFrame,
    top_n: int = 5,
) -> PredictionExplanation:
    """Explain a stacked ensemble prediction by averaging SHAP values across base models.

    Handles CatBoost, XGBoost, and LightGBM models.  If SHAP is unavailable,
    falls back to averaged feature importances.

    Args:
        models: Dict mapping model name to trained model (e.g. {"catboost": cb, ...}).
        feature_names: Feature column names.
        features_df: Single-row DataFrame with feature values.
        top_n: Number of top features to return.

    Returns:
        PredictionExplanation with ensemble-averaged contributions.
    """
    shap_mod = _try_import_shap()
    all_shap_values: list[np.ndarray] = []
    base_values: list[float] = []
    method = "shap"

    X = features_df[feature_names].iloc[[0]]

    for name, model in models.items():
        explainer_obj = SHAPExplainer(model, feature_names, model_type=name)

        if shap_mod is not None:
            tree_explainer = explainer_obj._get_explainer()
            if tree_explainer is not None:
                try:
                    sv = tree_explainer.shap_values(X)
                    if hasattr(sv, "values"):
                        vals = sv.values.flatten()
                    elif isinstance(sv, list):
                        vals = np.array(sv[0]).flatten()
                    else:
                        vals = np.array(sv).flatten()

                    bv = float(
                        tree_explainer.expected_value
                        if isinstance(tree_explainer.expected_value, (int, float, np.floating))
                        else tree_explainer.expected_value[0]
                    )
                    all_shap_values.append(vals)
                    base_values.append(bv)
                    continue
                except Exception as exc:
                    logger.warning("SHAP failed for %s: %s", name, exc)

        # Fallback for this model
        method = "importance_fallback"
        imps = explainer_obj._get_feature_importances()
        if imps is not None:
            all_shap_values.append(imps)
            base_values.append(0.0)

    if not all_shap_values:
        return PredictionExplanation(
            top_features=[], base_value=0.0, prediction_value=0.0,
            method="importance_fallback",
        )

    # Average across models
    avg_vals = np.mean(all_shap_values, axis=0)
    avg_base = float(np.mean(base_values))

    indices = np.argsort(np.abs(avg_vals))[::-1][:top_n]
    contributions = []
    for idx in indices:
        sv = float(avg_vals[idx])
        fname = feature_names[idx]
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


@dataclass
class PredictionResult:
    """Result from a single prediction."""

    location: str
    date: str
    predicted_weight_lb: float
    confidence_interval: tuple[float, float]
    model_used: str
    feature_contributions: dict[str, float] = field(default_factory=dict)
    environmental_summary: dict[str, Any] = field(default_factory=dict)
    fishing_rating: str = ""  # "poor", "fair", "good", "excellent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "date": self.date,
            "predicted_weight_lb": round(self.predicted_weight_lb, 2),
            "confidence_interval": [round(v, 2) for v in self.confidence_interval],
            "model_used": self.model_used,
            "fishing_rating": self.fishing_rating,
            "feature_contributions": {
                k: round(v, 4) for k, v in self.feature_contributions.items()
            },
            "environmental_summary": self.environmental_summary,
        }


def _fishing_rating(predicted_weight: float, location_mean: float) -> str:
    """Rate fishing conditions relative to historical average for this location."""
    if location_mean <= 0:
        return "unknown"
    ratio = predicted_weight / location_mean
    if ratio >= 1.15:
        return "excellent"
    elif ratio >= 1.0:
        return "good"
    elif ratio >= 0.85:
        return "fair"
    else:
        return "poor"


@dataclass
class PredictionResultV2:
    """Result from the 4-layer decision system.

    Combines site prior, conditions, catch rate, and confidence layers
    into a single composite prediction with a user-facing 0-100 score.
    """

    location: str
    date: str
    fishing_score: int  # 0-100 composite score
    confidence_level: str  # "high" / "medium" / "low" / "extrapolating"
    catch_probability: float  # 0-1, P(catch > 0)
    expected_cpue: float  # fish per hour
    conditions_score: float  # 0-1, how favorable current conditions are
    trophy_potential: float  # 0-1, likelihood of above-average size
    explanation: str  # human-readable summary
    environmental_summary: dict[str, Any] = field(default_factory=dict)
    # Preserved from v1 for backwards compat
    predicted_weight_lb: float = 0.0
    confidence_interval: tuple[float, float] = (0.0, 0.0)
    model_used: str = "4-layer-v1"
    feature_contributions: dict[str, float] = field(default_factory=dict)
    confidence_details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "date": self.date,
            "fishing_score": self.fishing_score,
            "confidence": self.confidence_level,
            "breakdown": {
                "catch_probability": round(self.catch_probability, 3),
                "expected_cpue": round(self.expected_cpue, 3),
                "conditions_score": round(self.conditions_score, 3),
                "trophy_potential": round(self.trophy_potential, 3),
            },
            "predicted_weight_lb": round(self.predicted_weight_lb, 2),
            "confidence_interval": [round(v, 2) for v in self.confidence_interval],
            "model_used": self.model_used,
            "explanation": self.explanation,
            "conditions": self.environmental_summary,
            "confidence_details": self.confidence_details,
            "feature_contributions": {
                k: round(v, 4) for k, v in self.feature_contributions.items()
            },
        }


def _compute_composite_score(
    catch_probability: float,
    cpue_percentile: float,
    conditions_score: float,
    trophy_potential: float,
) -> int:
    """Compute the 0-100 fishing score from the 4-layer breakdown.

    Weights from PRODUCTION_ARCHITECTURE.md:
        catch_probability   x 30
        cpue_percentile     x 30
        conditions_score    x 25
        trophy_potential    x 15
    """
    raw = (
        catch_probability * 30
        + cpue_percentile * 30
        + conditions_score * 25
        + trophy_potential * 15
    )
    return max(0, min(100, round(raw)))


def _is_valid(v: Any) -> bool:
    """Check if a value is non-None and not NaN."""
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return True


def _conditions_score_from_features(features: dict[str, float], global_mean: float) -> float:
    """Estimate a 0-1 conditions score from environmental features.

    Higher is better for fishing. Considers water temperature, flow regime,
    weather regime, pressure trend, wind, and solunar activity.

    v6 enhancements: flow regime signals, frontal passage timing,
    species-weighted thermal optimum.
    """
    score = 0.5  # neutral baseline
    n_signals = 0

    # --- Water temperature ---
    # Use species-weighted thermal optimum if available, else default bass range
    thermal_opt = features.get("species_thermal_optimum")
    wt = features.get("water_temp_c")
    if _is_valid(wt):
        n_signals += 1
        if _is_valid(thermal_opt):
            # Score based on proximity to species-weighted optimum
            diff = abs(wt - thermal_opt)
            if diff <= 3:
                score += 0.15
            elif diff <= 6:
                score += 0.05
            elif diff > 10:
                score -= 0.15
            else:
                score -= 0.05
        else:
            # Default bass range (15-22C)
            if 15 <= wt <= 22:
                score += 0.15
            elif 12 <= wt < 15 or 22 < wt <= 27:
                score += 0.05
            elif wt < 8 or wt > 30:
                score -= 0.15
            else:
                score -= 0.05

    # --- Flow regime (v6: USGS discharge signals) ---
    flow_regime = features.get("flow_regime")
    if flow_regime is not None:
        n_signals += 1
        if flow_regime == "stable" or flow_regime == 1:  # stable flow is ideal
            score += 0.08
        elif flow_regime == "rising" or flow_regime == 2:  # gently rising can be good
            score += 0.03
        elif flow_regime == "falling" or flow_regime == 0:  # falling flow means muddier
            score -= 0.05

    # Discharge z-score: extreme flows are bad
    q_zscore = features.get("discharge_zscore")
    if _is_valid(q_zscore):
        n_signals += 1
        if abs(q_zscore) < 0.5:
            score += 0.05  # Normal flow
        elif abs(q_zscore) > 2.0:
            score -= 0.10  # Extreme flow (flood or drought)
        elif abs(q_zscore) > 1.0:
            score -= 0.03

    # --- Weather regime (v6: frontal passage timing) ---
    hours_since_front = features.get("hours_since_front")
    if _is_valid(hours_since_front):
        n_signals += 1
        if 24 <= hours_since_front <= 72:
            score += 0.10  # 1-3 days post-front = prime feeding
        elif 12 <= hours_since_front < 24:
            score += 0.05  # Still recovering
        elif hours_since_front < 6:
            score -= 0.08  # Front just passed, fish inactive

    stability_index = features.get("stability_index")
    if _is_valid(stability_index):
        n_signals += 1
        if stability_index > 0.7:
            score += 0.05  # Stable weather pattern
        elif stability_index < 0.3:
            score -= 0.05  # Unsettled weather

    # --- Multi-species signals (v7) ---
    # Spawn activity: what fraction of likely species are currently spawning
    spawn_activity = features.get("spawn_activity")
    if _is_valid(spawn_activity):
        n_signals += 1
        # Spawning species are harder to catch on most techniques
        if spawn_activity > 0.5:
            score -= 0.05  # Heavy spawn = tougher fishing
        elif spawn_activity > 0.2:
            score += 0.03  # Pre/post spawn transitions = active fish

    # Peak feed activity: fraction of species in peak feeding mode
    peak_feed = features.get("peak_feed_activity")
    if _is_valid(peak_feed):
        n_signals += 1
        if peak_feed > 0.6:
            score += 0.10  # Most species actively feeding
        elif peak_feed > 0.3:
            score += 0.05

    # Species-weighted pressure sensitivity
    pressure_sens = features.get("pressure_sensitivity_score")
    frontal_resp = features.get("frontal_response_score")
    if _is_valid(frontal_resp) and _is_valid(hours_since_front):
        # Apply species-specific frontal response
        if hours_since_front < 12:
            score += frontal_resp * 0.5  # Catfish: positive, Bass: negative

    # Species-weighted flow preference
    flow_pref = features.get("flow_preference_score")
    if _is_valid(flow_pref) and flow_regime is not None:
        if (flow_regime == "rising" or flow_regime == 2) and flow_pref > 0.5:
            score += 0.05  # Current-oriented species benefit from rising flow
        elif (flow_regime == "stable" or flow_regime == 1) and flow_pref < 0.3:
            score += 0.03  # Ambush species prefer stable

    # --- Legacy spawn phase ---
    spawn = features.get("spawn_phase_score")
    if _is_valid(spawn) and not _is_valid(spawn_activity):
        n_signals += 1
        score += (spawn - 0.5) * 0.2

    # --- Solunar ---
    solunar = features.get("solunar_score")
    if _is_valid(solunar):
        n_signals += 1
        score += (solunar - 0.5) * 0.15

    # --- Pressure trend ---
    pressure_delta = features.get("pressure_delta_6h")
    if not _is_valid(pressure_delta):
        pressure_delta = features.get("pressure_trend_6h")
    if _is_valid(pressure_delta):
        n_signals += 1
        # Modulate by species pressure sensitivity
        sens_mult = pressure_sens if _is_valid(pressure_sens) else 0.5
        if pressure_delta >= 0:
            score += 0.05 * (0.5 + sens_mult)
        else:
            score -= 0.05 * min(1.0, abs(pressure_delta) / 3.0) * (0.5 + sens_mult)

    # --- Wind ---
    wind = features.get("wind_speed_kph")
    if _is_valid(wind):
        n_signals += 1
        if wind < 20:
            score += 0.03
        elif wind > 40:
            score -= 0.10

    return max(0.0, min(1.0, score))


def _trophy_potential(predicted_weight: float, location_mean: float) -> float:
    """Estimate trophy potential as a 0-1 score based on weight vs. location average."""
    if location_mean <= 0:
        return 0.3  # unknown baseline — neutral
    ratio = predicted_weight / location_mean
    # Sigmoid-ish mapping: ratio 1.0 -> 0.35, 1.3 -> 0.7, 1.5+ -> 0.9
    raw = 1.0 / (1.0 + math.exp(-5.0 * (ratio - 1.15)))
    return max(0.0, min(1.0, raw))


def _catch_probability_from_cpue(cpue: float) -> float:
    """Estimate P(catch > 0) from expected CPUE.

    At 0 CPUE → ~0.1 probability (still possible).
    At 1+ CPUE → ~0.95 probability.
    """
    return max(0.05, min(0.99, 1.0 - math.exp(-2.5 * cpue)))


def _determine_confidence(
    features: dict[str, float],
    location_means: dict[str, float],
    location: str,
) -> tuple[str, dict[str, str]]:
    """Determine confidence level and details based on data availability."""
    details: dict[str, str] = {}

    # Site data quality
    if location in location_means:
        details["site_data_quality"] = "high"
    else:
        details["site_data_quality"] = "extrapolating"

    # Conditions freshness
    has_usgs = features.get("water_temp_c") is not None and not (
        isinstance(features.get("water_temp_c"), float)
        and math.isnan(features.get("water_temp_c", float("nan")))
    )
    has_discharge = features.get("discharge_cfs") is not None and not (
        isinstance(features.get("discharge_cfs"), float)
        and math.isnan(features.get("discharge_cfs", float("nan")))
    )

    if has_usgs and has_discharge:
        details["conditions_freshness"] = "live"
    elif has_usgs or has_discharge:
        details["conditions_freshness"] = "partial"
    else:
        details["conditions_freshness"] = "estimated"

    # Model coverage
    details["model_coverage"] = "in_distribution" if location in location_means else "out_of_distribution"

    # Overall confidence
    if details["site_data_quality"] == "extrapolating":
        level = "extrapolating"
    elif details["conditions_freshness"] == "estimated":
        level = "low"
    elif details["conditions_freshness"] == "partial":
        level = "medium"
    else:
        level = "high"

    return level, details


def _build_explanation(
    fishing_score: int,
    conditions_score: float,
    catch_probability: float,
    trophy_potential: float,
    features: dict[str, float],
    confidence_level: str,
) -> str:
    """Build a human-readable explanation string."""
    parts: list[str] = []

    # Overall rating
    if fishing_score >= 75:
        parts.append("Good catch conditions.")
    elif fishing_score >= 50:
        parts.append("Fair catch conditions.")
    elif fishing_score >= 25:
        parts.append("Below-average conditions.")
    else:
        parts.append("Poor conditions expected.")

    # Water temperature commentary
    wt = features.get("water_temp_c")
    if wt is not None and not (isinstance(wt, float) and math.isnan(wt)):
        if 15 <= wt <= 22:
            parts.append(f"Water temp ({wt:.1f}C) ideal for bass activity.")
        elif 12 <= wt < 15:
            parts.append(f"Water temp ({wt:.1f}C) on the cool side; fish may be sluggish.")
        elif 22 < wt <= 27:
            parts.append(f"Water temp ({wt:.1f}C) warm; fish moving to deeper structure.")
        elif wt > 27:
            parts.append(f"Water temp ({wt:.1f}C) very warm; expect reduced activity.")
        elif wt < 12:
            parts.append(f"Water temp ({wt:.1f}C) cold; slow bite expected.")

    # Flow regime (v6)
    flow_regime = features.get("flow_regime")
    if flow_regime is not None:
        if flow_regime in ("rising", 2):
            parts.append("Rising water levels may push fish to shoreline cover.")
        elif flow_regime in ("falling", 0):
            parts.append("Falling water; fish likely moving to deeper structure.")

    # Frontal passage (v6)
    hours_since_front = features.get("hours_since_front")
    if _is_valid(hours_since_front):
        if 24 <= hours_since_front <= 72:
            parts.append("Post-frontal recovery window — prime feeding conditions.")
        elif hours_since_front < 6:
            parts.append("Recent front passage; fish may be lockjaw.")

    # Pressure
    pd6 = features.get("pressure_delta_6h")
    if not _is_valid(pd6):
        pd6 = features.get("pressure_trend_6h")
    if _is_valid(pd6):
        if pd6 > 0.5:
            parts.append("Rising barometric pressure favors feeding.")
        elif pd6 < -0.5:
            parts.append("Falling pressure may suppress activity.")

    # Solunar
    solunar = features.get("solunar_score")
    if _is_valid(solunar):
        if solunar >= 0.7:
            parts.append("Strong solunar period enhances bite window.")

    # Wind
    wind = features.get("wind_speed_kph")
    if _is_valid(wind):
        if 10 <= wind <= 25:
            parts.append("Moderate wind creating shore breaks.")
        elif wind > 35:
            parts.append("High wind may make fishing difficult.")

    # Confidence caveat
    if confidence_level == "extrapolating":
        parts.append("Limited data for this location; prediction is extrapolated.")
    elif confidence_level == "low":
        parts.append("Some environmental data unavailable; lower confidence.")

    return " ".join(parts)


def _compute_solunar_features(date: datetime) -> dict[str, float]:
    """Compute solunar features for a single date without ephem dependency."""
    # Simplified solunar computation (fallback if ephem not available)
    try:
        from castline.validation.features.solunar import (
            _moon_phase_continuous,
            _moon_illumination,
            _solunar_score,
            _day_length_hours,
        )

        phase = _moon_phase_continuous(date)
        return {
            "moon_phase_sin": math.sin(2 * math.pi * phase),
            "moon_phase_cos": math.cos(2 * math.pi * phase),
            "solunar_score": _solunar_score(date),
            "moon_illumination_pct": _moon_illumination(date),
            "day_length_hours": _day_length_hours(date, 35.0, -85.0),
        }
    except ImportError:
        # Fallback: basic lunar approximation
        # Synodic month ~29.53 days
        ref_new_moon = datetime(2024, 1, 11)  # Known new moon
        days_since = (date - ref_new_moon).days
        phase = (days_since % 29.53) / 29.53
        score = (math.cos(4 * math.pi * phase) + 1) / 2
        return {
            "moon_phase_sin": math.sin(2 * math.pi * phase),
            "moon_phase_cos": math.cos(2 * math.pi * phase),
            "solunar_score": score,
            "moon_illumination_pct": 50.0,
            "day_length_hours": 12.0,
        }


def _compute_spawn_features(date: datetime, water_temp_c: float | None) -> dict[str, float]:
    """Compute spawn cycle features."""
    month = date.month
    day_of_year = date.timetuple().tm_yday

    # Seasonal encoding
    season_sin = math.sin(2 * math.pi * day_of_year / 365.25)
    season_cos = math.cos(2 * math.pi * day_of_year / 365.25)

    # Spawn phase score based on water temperature
    spawn_score = 0.5  # neutral default
    if water_temp_c is not None and not math.isnan(water_temp_c):
        temp_f = water_temp_c * 9 / 5 + 32
        if 55 <= temp_f <= 65:
            spawn_score = 1.0  # pre-spawn peak feeding
        elif 65 < temp_f <= 75:
            spawn_score = 0.3  # spawning, reduced feeding
        elif 75 < temp_f <= 85:
            spawn_score = 0.7  # post-spawn recovery

    # Expected water temp by month (rough US average)
    expected_temps = {
        1: 5, 2: 5, 3: 10, 4: 15, 5: 20, 6: 25,
        7: 28, 8: 27, 9: 23, 10: 17, 11: 11, 12: 7,
    }
    expected = expected_temps.get(month, 15)
    anomaly = (water_temp_c - expected) if water_temp_c is not None and not math.isnan(water_temp_c) else 0.0

    return {
        "month": month,
        "day_of_year": day_of_year,
        "season_sin": season_sin,
        "season_cos": season_cos,
        "spawn_phase_score": spawn_score,
        "water_temp_anomaly": anomaly,
    }


def collect_live_features(
    usgs_site_id: str,
    date: str,
    lat: float = 35.0,
    lon: float = -85.0,
) -> dict[str, float]:
    """Collect real-time environmental features for a prediction.

    Fetches USGS water data and weather for the given site and date,
    computes solunar and spawn features, and returns a complete
    feature dictionary ready for model input.

    Since v6, also collects flow delta features (discharge_delta_1d,
    discharge_delta_3d, discharge_zscore, flow_regime) via the
    USGSWaterFetcher.fetch_flow_features() method.
    """
    import requests

    dt = pd.to_datetime(date)
    features: dict[str, float] = {}

    # Try the unified EnvironmentCollector first (includes flow deltas)
    try:
        from castline.services.data_fetcher import EnvironmentCollector

        collector = EnvironmentCollector(timeout=15)
        env_data = collector.collect(lat, lon, date, usgs_site_id=usgs_site_id)
        # Pull in all numeric features
        for key, value in env_data.items():
            if isinstance(value, (int, float)):
                features[key] = value
    except Exception:
        pass  # Fall through to legacy fetch below

    # 1. USGS daily values
    try:
        from castline.validation.collectors.usgs import fetch_usgs_daily_values

        site_id_clean = usgs_site_id.replace("USGS-", "").strip().zfill(8)
        start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
        end = dt.strftime("%Y-%m-%d")

        history = fetch_usgs_daily_values(site_id_clean, start, end, timeout=15)
        if not history.empty:
            history = history.sort_values("date")
            current = history.iloc[-1]
            previous = history.iloc[-2] if len(history) > 1 else current

            for col in ["water_temp_c", "discharge_cfs", "gage_height_ft",
                        "dissolved_oxygen_mgL", "ph", "turbidity_fnu",
                        "specific_conductance_us_cm"]:
                val = current.get(col)
                if val is not None and not (isinstance(val, float) and pd.isna(val)):
                    features[col] = float(val)

            # Deltas
            for col, delta_name in [
                ("water_temp_c", "temp_delta_24h_c"),
                ("discharge_cfs", "flow_delta_24h_pct"),
                ("gage_height_ft", "gage_delta_24h_ft"),
            ]:
                curr_val = features.get(col)
                prev_val = previous.get(col) if previous is not None else None
                if curr_val is not None and prev_val is not None:
                    prev_f = float(prev_val) if not pd.isna(prev_val) else None
                    if prev_f is not None:
                        if delta_name == "flow_delta_24h_pct" and prev_f != 0:
                            features[delta_name] = ((curr_val - prev_f) / prev_f) * 100
                        elif delta_name != "flow_delta_24h_pct":
                            features[delta_name] = curr_val - prev_f

            # 7-day means and stability metrics
            if len(history) >= 2:
                for col, mean_name in [
                    ("water_temp_c", "water_temp_7d_mean"),
                    ("discharge_cfs", "discharge_7d_mean"),
                    ("gage_height_ft", "gage_height_7d_mean"),
                ]:
                    vals = history[col].dropna() if col in history.columns else pd.Series()
                    if len(vals) > 0:
                        features[mean_name] = float(vals.mean())

                # Gage stability (std dev over 7 days)
                if "gage_height_ft" in history.columns:
                    gh_vals = history["gage_height_ft"].dropna()
                    if len(gh_vals) >= 2:
                        features["gage_stability_7d"] = float(gh_vals.std())

                # Discharge as % of 30-day mean
                if "discharge_cfs" in history.columns:
                    d_vals = history["discharge_cfs"].dropna()
                    if len(d_vals) > 0 and d_vals.mean() > 0:
                        features["discharge_pct_of_30d"] = float(
                            features.get("discharge_cfs", d_vals.iloc[-1]) / d_vals.mean() * 100
                        )
    except Exception as exc:
        print(f"inference: USGS fetch error: {exc}", file=sys.stderr)

    # 2. USGS instantaneous values (72h temporal window)
    try:
        from castline.validation.collectors.usgs_iv import (
            fetch_iv_window,
            compute_temporal_features,
        )

        site_id_clean = usgs_site_id.replace("USGS-", "").strip().zfill(8)
        iv_df = fetch_iv_window(site_id_clean, date, hours_before=72)
        if not iv_df.empty:
            temporal = compute_temporal_features(iv_df)
            features.update(temporal)
    except Exception as exc:
        print(f"inference: USGS IV fetch error: {exc}", file=sys.stderr)

    # 3. Weather (simplified — use IEM ASOS)
    try:
        from castline.validation.collectors.weather import _find_nearest_iem_station

        # Would need lat/lon for proper station lookup
        # For now, basic weather features are NaN if not available
    except Exception:
        pass

    # 4. Solunar features
    solunar = _compute_solunar_features(dt.to_pydatetime())
    features.update(solunar)

    # 5. Spawn features
    water_temp = features.get("water_temp_c")
    spawn = _compute_spawn_features(dt.to_pydatetime(), water_temp)
    features.update(spawn)

    # 6. Multi-species features (v7)
    try:
        from castline.services.site_prior import compute_multi_species_features

        depth_m = features.get("lagos_max_depth_m", float("nan"))
        area_ha = features.get("lagos_area_ha", float("nan"))
        wt_c = features.get("water_temp_c", float("nan"))
        month = dt.month

        species_feats = compute_multi_species_features(
            lat=lat, lon=lon,
            water_temp_c=wt_c if isinstance(wt_c, (int, float)) else float("nan"),
            month=month,
            depth_m=depth_m if isinstance(depth_m, (int, float)) else float("nan"),
            lake_area_ha=area_ha if isinstance(area_ha, (int, float)) else float("nan"),
        )
        for key, val in species_feats.items():
            if isinstance(val, (int, float)):
                features[key] = val
            elif isinstance(val, str):
                features[key] = val
    except Exception as exc:
        print(f"inference: multi-species features error: {exc}", file=sys.stderr)

    return features


class CastlinePredictor:
    """Production predictor for CASTLINE fishing success forecasting."""

    def __init__(
        self,
        hgb_model: Any = None,
        location_means: dict[str, float] = None,
        feature_names: list[str] = None,
        global_mean: float = 15.0,
        model_metadata: dict[str, Any] = None,
    ):
        self.hgb_model = hgb_model
        self.location_means = location_means or {}
        self.feature_names = feature_names or []
        self.global_mean = global_mean
        self.model_metadata = model_metadata or {}

    @classmethod
    def load(cls, model_dir: str | Path) -> CastlinePredictor:
        """Load a trained predictor from disk."""
        model_dir = Path(model_dir)

        # Load HGB model
        import joblib

        hgb_path = model_dir / "hgb_model.joblib"
        hgb_model = joblib.load(hgb_path) if hgb_path.exists() else None

        # Load metadata
        meta_path = model_dir / "model_metadata.json"
        metadata = {}
        if meta_path.exists():
            with open(meta_path) as f:
                metadata = json.load(f)

        return cls(
            hgb_model=hgb_model,
            location_means=metadata.get("location_means", {}),
            feature_names=metadata.get("feature_names", []),
            global_mean=metadata.get("global_mean", 15.0),
            model_metadata=metadata,
        )

    def save(self, model_dir: str | Path) -> None:
        """Save the predictor to disk."""
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        if self.hgb_model is not None:
            import joblib

            joblib.dump(self.hgb_model, model_dir / "hgb_model.joblib")

        metadata = {
            "location_means": self.location_means,
            "feature_names": self.feature_names,
            "global_mean": self.global_mean,
            **self.model_metadata,
        }
        with open(model_dir / "model_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

    def train(self, dataset_path: str | Path) -> dict[str, Any]:
        """Train the HGB model from the enriched dataset."""
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

        df = pd.read_csv(dataset_path)
        df["date"] = pd.to_datetime(df["date"])
        df["year"] = df["date"].dt.year

        target = "median_weight_lb"
        train = df[df["year"] <= 2023].copy()
        test = df[df["year"] > 2023].copy()

        # Location mean encoding from train set
        loc_means = train.groupby("location")[target].mean().to_dict()
        self.location_means = {k: float(v) for k, v in loc_means.items()}
        self.global_mean = float(train[target].mean())

        df["location_mean_weight"] = df["location"].map(loc_means).fillna(self.global_mean)
        train = df[df["year"] <= 2023].copy()
        test = df[df["year"] > 2023].copy()

        # Feature list
        self.feature_names = [
            "location_mean_weight", "baseline_signal",
            "air_temp_c", "pressure_mb", "wind_speed_kph",
            "cloud_cover_pct", "precip_24h_mm",
            "water_temp_c", "discharge_cfs", "gage_height_ft",
            "temp_delta_24h_c", "flow_delta_24h_pct", "gage_delta_24h_ft",
            "dissolved_oxygen_mgL", "specific_conductance_us_cm", "ph", "turbidity_fnu",
            "water_temp_7d_mean", "discharge_7d_mean", "gage_height_7d_mean",
            "moon_phase_sin", "moon_phase_cos", "solunar_score", "moon_illumination_pct",
            "spawn_phase_score", "day_length_hours", "water_temp_anomaly",
            "season_sin", "season_cos",
            "creel_median_cpue",
            "discharge_cfs_daily_range", "discharge_cfs_rate_of_change",
            "gage_height_ft_daily_range", "gage_height_ft_6h_delta",
            "water_temp_c_daily_range", "water_temp_c_dawn", "water_temp_c_6h_delta",
        ]
        self.feature_names = [f for f in self.feature_names if f in df.columns]

        X_train = train[self.feature_names]
        y_train = train[target]
        X_test = test[self.feature_names]
        y_test = test[target]

        # Train HGB
        self.hgb_model = HistGradientBoostingRegressor(
            max_iter=300, max_depth=4, learning_rate=0.05,
            min_samples_leaf=10, l2_regularization=1.0,
            random_state=42,
        )
        self.hgb_model.fit(X_train, y_train)

        y_pred = self.hgb_model.predict(X_test)
        r2 = r2_score(y_test, y_pred)
        rmse = mean_squared_error(y_test, y_pred) ** 0.5
        mae = mean_absolute_error(y_test, y_pred)

        self.model_metadata["train_rows"] = len(train)
        self.model_metadata["test_rows"] = len(test)
        self.model_metadata["r2"] = float(r2)
        self.model_metadata["rmse"] = float(rmse)
        self.model_metadata["mae"] = float(mae)
        self.model_metadata["trained_at"] = datetime.now().isoformat()

        return {"r2": r2, "rmse": rmse, "mae": mae, "train_rows": len(train), "test_rows": len(test)}

    def predict(
        self,
        location: str,
        date: str,
        usgs_site_id: str,
        trail: str = "recreational",
        precomputed_features: dict[str, float] | None = None,
    ) -> PredictionResult:
        """Make a prediction for a specific location and date.

        If precomputed_features is None, attempts to fetch live data.
        """
        if self.hgb_model is None:
            raise RuntimeError("Model not trained. Call train() or load() first.")

        # Collect features
        if precomputed_features is not None:
            features = precomputed_features.copy()
        else:
            features = collect_live_features(usgs_site_id, date)

        # Add location mean weight (loc_enc)
        loc_mean = self.location_means.get(location, self.global_mean)
        features["loc_enc"] = loc_mean
        features["location_mean_weight"] = loc_mean

        # Add baseline signal
        dt = pd.to_datetime(date)
        features.setdefault(
            "baseline_signal",
            float(_compute_solunar_features(dt.to_pydatetime()).get("solunar_score", 0.5)),
        )

        # Trail mean weight (from training data)
        trail_means = self.model_metadata.get("trail_means", {})
        features["trail_mean_weight"] = trail_means.get(trail, self.global_mean)

        # Year
        features["year"] = dt.year

        # Estimate water temp from air temp if missing
        if (features.get("water_temp_c") is None or
                (isinstance(features.get("water_temp_c"), float) and math.isnan(features["water_temp_c"]))):
            air_temp = features.get("air_temp_c")
            if air_temp is not None and not (isinstance(air_temp, float) and math.isnan(air_temp)):
                features["water_temp_c"] = 0.663 * air_temp + 7.16
                features["water_temp_estimated"] = 1
            else:
                features["water_temp_estimated"] = 0
        else:
            features["water_temp_estimated"] = 0

        # Derived features
        wt = features.get("water_temp_c")
        dc = features.get("discharge_cfs")
        if wt is not None and dc is not None:
            if not (isinstance(wt, float) and math.isnan(wt)) and not (isinstance(dc, float) and math.isnan(dc)):
                features["water_temp_x_flow"] = wt * dc

        # Cumulative degree days estimate
        if wt is not None and not (isinstance(wt, float) and math.isnan(wt)):
            features.setdefault("cumulative_degree_days", max(0, (wt - 15) * 30))
            features.setdefault("water_temp_anomaly", 0.0)

        # Front phase dummies
        front = features.get("front_phase", "stable")
        features.setdefault("front_pre_frontal", 1 if front == "pre_frontal" else 0)
        features.setdefault("front_post_frontal", 1 if front == "post_frontal" else 0)

        # Lake-specific features
        try:
            from castline.validation.features.lake_features import compute_all_lake_features

            # Get morphometry from metadata or defaults
            morph = self.model_metadata.get("morphometry", {}).get(location, {})
            lake_area = morph.get("area_acres", features.get("area_acres", 0))
            max_depth = morph.get("max_depth_ft", features.get("max_depth_ft", 30))
            shore_dev = morph.get("shore_dev", features.get("shore_dev", 1.0))
            lat_val = morph.get("lat", features.get("lat", 35.0))

            lake_feats = compute_all_lake_features(
                surface_temp_c=features.get("water_temp_c", float("nan")),
                air_temp_c=features.get("air_temp_c", float("nan")),
                season_day=dt.timetuple().tm_yday,
                latitude=lat_val or 35.0,
                max_depth_ft=max_depth or 30.0,
                wind_speed_kph=features.get("wind_speed_kph", float("nan")),
                lake_area_acres=lake_area or 0,
                lake_shore_dev=shore_dev or 1.0,
                pressure_mb=features.get("pressure_mb", float("nan")),
                pressure_delta_6h=features.get("pressure_delta_6h", float("nan")),
                solunar_score=features.get("solunar_score", float("nan")),
                gage_height_ft=features.get("gage_height_ft", float("nan")),
                gage_height_7d_mean=features.get("gage_height_7d_mean", float("nan")),
            )
            features.update(lake_feats)
        except Exception as exc:
            print(f"inference: lake features error: {exc}", file=sys.stderr)

        # Build feature vector
        X = pd.DataFrame([features])
        for col in self.feature_names:
            if col not in X.columns:
                X[col] = float("nan")
        X = X[self.feature_names]

        # Predict
        pred = float(self.hgb_model.predict(X)[0])

        # Confidence interval (approximate using training RMSE)
        rmse = self.model_metadata.get("rmse", 4.0)
        ci = (max(0, pred - 1.96 * rmse), pred + 1.96 * rmse)

        # Feature contributions via SHAP (or fallback to importance)
        shap_explanation = self.explain_prediction(X)
        top_features = {
            fc.feature_name: fc.shap_value for fc in shap_explanation.top_features
        }

        # Environmental summary
        env_summary = {}
        for key in ["water_temp_c", "discharge_cfs", "wind_speed_kph",
                     "pressure_mb", "pressure_delta_6h",
                     "solunar_score", "spawn_phase_score",
                     "discharge_delta_1d", "discharge_zscore",
                     "flow_regime", "hours_since_front",
                     "stability_index", "gage_height_ft"]:
            val = features.get(key)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                env_summary[key] = round(float(val), 2)

        rating = _fishing_rating(pred, loc_mean)

        return PredictionResult(
            location=location,
            date=date,
            predicted_weight_lb=pred,
            confidence_interval=ci,
            model_used="hgb-v3",
            feature_contributions=top_features,
            environmental_summary=env_summary,
            fishing_rating=rating,
        )

    def _shap_explanation_string(
        self,
        feature_contributions: dict[str, float],
    ) -> str:
        """Build a SHAP-driver summary string from feature contributions.

        Returns an empty string if contributions are empty or all zero.
        """
        if not feature_contributions:
            return ""

        positive = []
        negative = []
        for fname, val in sorted(
            feature_contributions.items(), key=lambda kv: abs(kv[1]), reverse=True
        ):
            if val > 0:
                positive.append(_feature_label(fname))
            elif val < 0:
                negative.append(_feature_label(fname))

        parts = []
        if positive:
            top_pos = positive[:2]
            parts.append(f"Key drivers: {' and '.join(top_pos)}.")
        if negative:
            top_neg = negative[:2]
            parts.append(f"Limiting factors: {' and '.join(top_neg)}.")

        return " ".join(parts)

    def explain_prediction(
        self,
        X: pd.DataFrame,
        top_n: int = 5,
    ) -> PredictionExplanation:
        """Explain a prediction using SHAP TreeExplainer or feature-importance fallback.

        Args:
            X: Single-row DataFrame with columns matching self.feature_names.
            top_n: Number of top contributing features to return.

        Returns:
            PredictionExplanation with the top contributing features, suitable
            for rendering in the mobile ExplanationCard via .to_explanation_string().
        """
        if self.hgb_model is None:
            return PredictionExplanation(
                top_features=[], base_value=0.0, prediction_value=0.0,
                method="importance_fallback",
            )
        explainer = SHAPExplainer(self.hgb_model, self.feature_names, model_type="hgb")
        return explainer.explain(X, top_n=top_n)

    def batch_predict(
        self,
        locations: list[dict[str, str]],
    ) -> list[PredictionResult]:
        """Make predictions for multiple location/date combinations.

        Each entry should have: location, date, usgs_site_id
        """
        results = []
        for entry in locations:
            try:
                result = self.predict(
                    location=entry["location"],
                    date=entry["date"],
                    usgs_site_id=entry["usgs_site_id"],
                )
                results.append(result)
            except Exception as exc:
                print(f"inference: error predicting {entry}: {exc}", file=sys.stderr)
        return results

    # ------------------------------------------------------------------
    # V2: 4-layer decision system
    # ------------------------------------------------------------------

    def predict_v2(
        self,
        location: str,
        date: str,
        usgs_site_id: str,
        trail: str = "recreational",
        precomputed_features: dict[str, float] | None = None,
    ) -> PredictionResultV2:
        """Make a prediction using the 4-layer decision system.

        Layer 1 (Site Prior): location mean weight / CPUE from training data.
        Layer 2 (Conditions): real-time environmental conditions scoring.
        Layer 3 (CPUE Model): catch rate prediction (stub — uses weight model
                              until dedicated hurdle model is trained).
        Layer 4 (Confidence): data quality and OOD detection.

        Returns a PredictionResultV2 with a 0-100 composite fishing_score.
        """
        # --- Get the v1 prediction (preserves existing weight model) ---
        v1 = self.predict(
            location=location,
            date=date,
            usgs_site_id=usgs_site_id,
            trail=trail,
            precomputed_features=precomputed_features,
        )

        # Reconstruct feature dict so we can compute layer scores.
        # Re-fetch only if we need them (predict() already fetched).
        if precomputed_features is not None:
            features = precomputed_features.copy()
        else:
            features = collect_live_features(usgs_site_id, date)

        # Carry over computed values from v1
        features.update(v1.environmental_summary)
        loc_mean = self.location_means.get(location, self.global_mean)

        # --- Layer 1: Site Prior ---
        # Use the CreelCat CPUE if available, else fall back to a
        # weight-based proxy.  The creel_median_cpue feature is populated
        # during training; at inference we read it from metadata or
        # the feature vector.
        creel_cpue = features.get("creel_median_cpue")
        site_cpue: float
        if creel_cpue is not None and not (isinstance(creel_cpue, float) and math.isnan(creel_cpue)):
            site_cpue = float(creel_cpue)
        else:
            # Proxy: normalise predicted weight vs global mean to a rough CPUE
            site_cpue = max(0.0, v1.predicted_weight_lb / max(self.global_mean, 1.0) * 0.4)

        # --- Layer 2: Conditions ---
        conditions = _conditions_score_from_features(features, self.global_mean)

        # --- Layer 3: CPUE Model ---
        # TODO: Replace with trained hurdle model once saved.
        # For now, modulate site CPUE by conditions score.
        expected_cpue = site_cpue * (0.5 + 0.5 * conditions)
        catch_prob = _catch_probability_from_cpue(expected_cpue)

        # CPUE percentile (relative to global mean proxy)
        global_cpue_proxy = 0.4  # rough median CPUE for bass (fish/hr)
        cpue_percentile = min(1.0, expected_cpue / max(global_cpue_proxy * 2.0, 0.01))

        # --- Layer 4: Confidence ---
        confidence_level, confidence_details = _determine_confidence(
            features, self.location_means, location,
        )

        # --- Trophy Potential ---
        trophy = _trophy_potential(v1.predicted_weight_lb, loc_mean)

        # --- Composite Score ---
        fishing_score = _compute_composite_score(
            catch_probability=catch_prob,
            cpue_percentile=cpue_percentile,
            conditions_score=conditions,
            trophy_potential=trophy,
        )

        # --- Explanation (conditions-based + SHAP-driven) ---
        explanation = _build_explanation(
            fishing_score=fishing_score,
            conditions_score=conditions,
            catch_probability=catch_prob,
            trophy_potential=trophy,
            features=features,
            confidence_level=confidence_level,
        )

        # Append SHAP-based driver summary if available
        shap_summary = self._shap_explanation_string(v1.feature_contributions)
        if shap_summary:
            explanation = explanation + " " + shap_summary

        return PredictionResultV2(
            location=location,
            date=date,
            fishing_score=fishing_score,
            confidence_level=confidence_level,
            catch_probability=catch_prob,
            expected_cpue=expected_cpue,
            conditions_score=conditions,
            trophy_potential=trophy,
            explanation=explanation,
            environmental_summary=v1.environmental_summary,
            predicted_weight_lb=v1.predicted_weight_lb,
            confidence_interval=v1.confidence_interval,
            model_used="4-layer-v1",
            feature_contributions=v1.feature_contributions,
            confidence_details=confidence_details,
        )

    def batch_predict_v2(
        self,
        locations: list[dict[str, str]],
    ) -> list[PredictionResultV2]:
        """Make v2 predictions for multiple location/date combinations.

        Each entry should have keys: location, date, usgs_site_id.
        Optionally: trail, precomputed_features.
        """
        results: list[PredictionResultV2] = []
        for entry in locations:
            try:
                result = self.predict_v2(
                    location=entry["location"],
                    date=entry["date"],
                    usgs_site_id=entry["usgs_site_id"],
                    trail=entry.get("trail", "recreational"),
                    precomputed_features=entry.get("precomputed_features"),
                )
                results.append(result)
            except Exception as exc:
                print(f"inference: error in predict_v2 for {entry}: {exc}", file=sys.stderr)
        return results

    @classmethod
    def available_at_deployment(cls) -> dict[str, Any]:
        """List data sources and dependencies required for production deployment.

        Returns a dict describing each data layer, its source, update
        frequency, and whether it is currently implemented.
        """
        return {
            "real_time": {
                "usgs_nwis": {
                    "data": ["water_temp_c", "discharge_cfs", "gage_height_ft",
                             "dissolved_oxygen_mgL", "ph", "turbidity_fnu",
                             "specific_conductance_us_cm"],
                    "api": "https://waterservices.usgs.gov/nwis/",
                    "update_freq": "hourly",
                    "cost": "free",
                    "implemented": True,
                },
                "usgs_iv": {
                    "data": ["72h instantaneous values", "temporal features",
                             "rate_of_change", "daily_range"],
                    "api": "https://waterservices.usgs.gov/nwis/iv/",
                    "update_freq": "15min",
                    "cost": "free",
                    "implemented": True,
                },
                "open_meteo": {
                    "data": ["air_temp_c", "pressure_mb", "wind_speed_kph",
                             "cloud_cover_pct", "precip_24h_mm"],
                    "api": "https://api.open-meteo.com/v1/forecast",
                    "update_freq": "hourly",
                    "cost": "free",
                    "implemented": False,
                    "note": "Weather collector exists but not wired into live inference",
                },
                "solunar": {
                    "data": ["moon_phase_sin", "moon_phase_cos",
                             "solunar_score", "moon_illumination_pct",
                             "day_length_hours"],
                    "api": "computed locally",
                    "update_freq": "daily",
                    "cost": "free",
                    "implemented": True,
                },
            },
            "static_quarterly": {
                "creelcat": {
                    "data": ["creel_median_cpue", "species composition"],
                    "source": "CreelCat database",
                    "coverage": "~1,540 US water bodies",
                    "implemented": True,
                },
                "lagos": {
                    "data": ["lake morphometry", "area_acres", "max_depth_ft",
                             "shore_dev"],
                    "source": "LAGOS-NE/US",
                    "coverage": "50K+ US lakes",
                    "implemented": True,
                },
                "usgs_fish_community": {
                    "data": ["species richness", "bass occurrence"],
                    "source": "USGS BioData",
                    "coverage": "35K reaches",
                    "implemented": True,
                },
                "location_embeddings": {
                    "data": ["GeoCLIP 32-dim", "SatCLIP 32-dim"],
                    "source": "Pre-computed from satellite imagery",
                    "implemented": True,
                },
            },
            "models_required": {
                "weight_model": {
                    "type": "HistGradientBoostingRegressor",
                    "file": "hgb_model.joblib",
                    "implemented": True,
                },
                "cpue_hurdle_model": {
                    "type": "Hurdle (P(catch) x E(CPUE|catch))",
                    "file": "cpue_hurdle.joblib",
                    "implemented": False,
                    "note": "Stubbed — using weight-model proxy in predict_v2",
                },
                "confidence_model": {
                    "type": "Conformal / OOD detector",
                    "file": "confidence_model.joblib",
                    "implemented": False,
                    "note": "Using heuristic confidence in predict_v2",
                },
            },
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train or predict with CASTLINE")
    sub = parser.add_subparsers(dest="command")

    train_cmd = sub.add_parser("train", help="Train the production model")
    train_cmd.add_argument(
        "--dataset",
        type=Path,
        default=Path("castline/validation/data/assembled/validation_dataset_enriched.csv"),
    )
    train_cmd.add_argument(
        "--output",
        type=Path,
        default=Path("castline/validation/data/models/production"),
    )

    predict_cmd = sub.add_parser("predict", help="Make a prediction")
    predict_cmd.add_argument("--model-dir", type=Path, required=True)
    predict_cmd.add_argument("--location", required=True)
    predict_cmd.add_argument("--date", required=True)
    predict_cmd.add_argument("--usgs-site", required=True)

    args = parser.parse_args()

    if args.command == "train":
        predictor = CastlinePredictor()
        result = predictor.train(args.dataset)
        predictor.save(args.output)
        print(f"Trained: R²={result['r2']:.4f}  RMSE={result['rmse']:.3f}")
        print(f"Saved to {args.output}")

    elif args.command == "predict":
        predictor = CastlinePredictor.load(args.model_dir)
        prediction = predictor.predict(
            location=args.location,
            date=args.date,
            usgs_site_id=args.usgs_site,
        )
        print(json.dumps(prediction.to_dict(), indent=2))
