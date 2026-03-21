"""V2 prediction endpoints — 4-layer decision system with real-time features.

Ported from Django castline.backend.apps.core.views.predict_v2 and upgraded
to use the V13/V14 stacked ensemble with live weather + USGS data collection.

Features:
- Redis prediction caching (1h for current, 6h for forecasts)
- JWT authentication required
- Popularity tracking for background refresh
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query, HTTPException

from castline.api.config import settings
from castline.api.models.schemas import (
    PredictRequest,
    BatchPredictRequest,
    PredictResponse,
    ForecastV2Response,
    BestFishingResponse,
    LayerBreakdown,
    PredictionBreakdown,
)
from castline.api.services.auth import get_optional_user
from castline.api.services.cache import (
    prediction_cache_key,
    forecast_cache_key,
    get_cached_prediction,
    set_cached_prediction,
    track_cache_key_for_location,
    track_popular_location,
)
from castline.api.services.features import collect_realtime_features

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Prediction logging ────────────────────────────────────────────

_LOG_DIR = Path(__file__).resolve().parents[2] / "data"


def _log_prediction(**kwargs):
    """Append prediction to JSONL log for performance tracking."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _LOG_DIR / "prediction_logs.jsonl"
        entry = {
            "_timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("Failed to log prediction: %s", exc)


# ── Score helpers ──────────────────────────────────────────────────

def _weight_to_fishing_score(predicted_weight: float, hist_avg: float) -> int:
    """Convert predicted weight to a 0-100 fishing score."""
    if hist_avg <= 0:
        hist_avg = 3.0
    ratio = predicted_weight / hist_avg
    score = 100.0 / (1.0 + 2.718 ** (-3.5 * (ratio - 1.0)))
    return max(0, min(100, int(round(score))))


def _score_label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    if score >= 20:
        return "Below Average"
    return "Poor"


# ── Breakdown builder ──────────────────────────────────────────────

def _build_breakdown(
    score: int,
    features: dict,
    predicted_weight: float,
) -> PredictionBreakdown:
    """Build the 4-layer decision breakdown from collected features.

    Dynamically adjusts layer contributions based on which live data
    sources are available.
    """
    raw_weather = features.get("_raw_weather") or {}
    raw_usgs = features.get("_raw_usgs") or {}

    has_hydro = bool(raw_usgs)
    has_weather = bool(raw_weather)

    # Dynamic weight allocation
    hydro_w = 0.40 if has_hydro else 0.25
    weather_w = 0.30 if has_weather else 0.20
    bio_w = 0.20
    history_w = round(1.0 - hydro_w - weather_w - bio_w, 2)

    # Hydrology description
    hydro_desc = "Flow rate, water level, and temperature signals"
    hydro_quality = "estimated"
    if has_hydro:
        hydro_quality = "live"
        parts = []
        discharge_trend = features.get("_live_discharge_trend")
        if discharge_trend:
            parts.append(f"{discharge_trend} discharge")
        level_trend = features.get("_live_level_trend")
        if level_trend:
            parts.append(f"{level_trend} water level")
        water_temp_f = features.get("_live_water_temp_f")
        if water_temp_f:
            parts.append(f"water {water_temp_f:.0f}F")
        if parts:
            hydro_desc = "Live: " + ", ".join(parts)

    # Weather description
    weather_desc = "Air temp, pressure, wind, precipitation outlook"
    weather_quality = "estimated"
    if has_weather:
        weather_quality = "live"
        parts = []
        conditions = raw_weather.get("conditions")
        if conditions:
            parts.append(conditions.lower())
        temp_f = raw_weather.get("temp_f")
        if temp_f is not None:
            parts.append(f"{temp_f:.0f}F")
        trend = raw_weather.get("pressure_trend")
        if trend and trend != "steady":
            parts.append(f"{trend} pressure")
        wind = raw_weather.get("wind_mph")
        if wind is not None:
            parts.append(f"{wind:.0f}mph wind")
        if parts:
            weather_desc = "Live: " + ", ".join(parts)

    # Biology description
    month = features.get("month", datetime.now().month)
    spawn_prob = features.get("spawn_probability", 0)
    if spawn_prob > 0.5:
        bio_desc = "Spawn phase active — bedding fish, finesse presentations"
    elif features.get("prespawn_intensity", 0) > 0.5:
        bio_desc = "Pre-spawn feeding frenzy — aggressive bites likely"
    elif features.get("post_spawn_lethargy", 0) > 0.5:
        bio_desc = "Post-spawn recovery — slower action, deeper structure"
    elif month in (12, 1, 2):
        bio_desc = "Winter patterns — slow metabolism, deep slow presentations"
    elif month in (6, 7, 8):
        bio_desc = "Summer patterns — early/late bite windows, deeper structure"
    else:
        bio_desc = "Seasonal patterns, spawn timing, forage availability"

    return PredictionBreakdown(
        fishing_score=score,
        layers={
            "hydrology": LayerBreakdown(
                label="Water Conditions",
                description=hydro_desc,
                contribution=round(score * hydro_w),
                data_quality=hydro_quality,
            ),
            "weather": LayerBreakdown(
                label="Weather",
                description=weather_desc,
                contribution=round(score * weather_w),
                data_quality=weather_quality,
            ),
            "biology": LayerBreakdown(
                label="Biological Activity",
                description=bio_desc,
                contribution=round(score * bio_w),
                data_quality="modeled",
            ),
            "history": LayerBreakdown(
                label="Historical Performance",
                description="Past tournament and creel survey data for this location",
                contribution=round(score * history_w),
                data_quality="historical",
            ),
        },
        predicted_weight_lb=predicted_weight,
    )


# ── Explanation builder ────────────────────────────────────────────

def _build_explanation(
    score: int,
    features: dict,
    model_route: str,
    location: str,
) -> str:
    """Build a natural language explanation from score and features."""
    parts: list[str] = []

    # Overall rating
    label = _score_label(score)
    parts.append(f"{label} fishing conditions ({score}/100).")

    # Water temperature commentary
    water_temp_f = features.get("_live_water_temp_f")
    if water_temp_f is not None:
        water_temp_c = (water_temp_f - 32) * 5.0 / 9.0
        if 15 <= water_temp_c <= 22:
            parts.append(f"Water temp ({water_temp_f:.0f}F) in the ideal bass range.")
        elif water_temp_c < 12:
            parts.append(f"Water temp ({water_temp_f:.0f}F) is cold; expect slow action.")
        elif water_temp_c > 27:
            parts.append(f"Water temp ({water_temp_f:.0f}F) is warm; fish deeper structure.")
        else:
            parts.append(f"Water temp {water_temp_f:.0f}F.")

    # Pressure
    raw_weather = features.get("_raw_weather") or {}
    trend = raw_weather.get("pressure_trend")
    if trend == "falling":
        parts.append("Falling barometric pressure favors active feeding.")
    elif trend == "rising":
        parts.append("Rising pressure — fish may be less aggressive.")

    # Wind
    wind = raw_weather.get("wind_mph")
    if wind is not None:
        if 5 <= wind <= 15:
            parts.append("Light wind creating good shore breaks.")
        elif wind > 25:
            parts.append("High wind may make fishing difficult.")

    # Solunar
    sol = features.get("moon_phase_score", 0)
    if sol > 0.7:
        parts.append("Strong solunar period enhances bite window.")

    # Spawn/biology
    spawn_prob = features.get("spawn_probability", 0)
    prespawn = features.get("prespawn_intensity", 0)
    if prespawn > 0.5:
        parts.append("Pre-spawn feeding window — prime time.")
    elif spawn_prob > 0.5:
        parts.append("Spawn phase — target bedding areas with finesse rigs.")

    # Confidence caveat
    if model_route == "unseen":
        parts.append("This is a new location; prediction is based on regional patterns.")

    return " ".join(parts)


# ══════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════

@router.post("/predict", response_model=PredictResponse)
async def predict_v2(
    request: Request,
    body: PredictRequest,
    user: dict | None = Depends(get_optional_user),
):
    """Composite-score prediction using the 4-layer decision system.

    JWT authentication optional (unauthenticated users get limited data).

    1. Checks Redis cache for existing prediction
    2. Resolves location coordinates
    3. Collects real-time weather + USGS features (async)
    4. Runs V13/V14 stacked ensemble prediction
    5. Caches result in Redis (1-hour TTL)
    6. Returns score, confidence, 4-layer breakdown, and explanation
    """
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(status_code=503, detail="ML model not loaded")

    # Validate date
    try:
        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    redis = getattr(request.app.state, "redis", None)
    model_version = predictor.model_metadata.get("model_version", "v13")

    # Check prediction cache
    cache_key = prediction_cache_key(body.location, body.date, model_version)
    cached = await get_cached_prediction(redis, cache_key)
    if cached:
        cached["_cached"] = True
        return PredictResponse(**cached)

    # Track popularity for background refresh
    await track_popular_location(redis, body.location)

    # Collect real-time features
    features = await collect_realtime_features(
        location=body.location,
        date=body.date,
        usgs_site_id=body.usgs_site_id or None,
        redis=redis,
    )

    # Run model prediction with collected features
    result = predictor.predict(
        location=body.location,
        date=body.date,
        usgs_site_id=body.usgs_site_id or "",
        precomputed_features={
            k: v for k, v in features.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        },
    )

    rd = result.to_dict()
    predicted_weight = rd["predicted_weight_lb"]
    hist_avg = predictor.location_means.get(body.location, 3.0)
    score = _weight_to_fishing_score(predicted_weight, hist_avg)
    confidence = rd.get("confidence", 0.5)
    model_route = rd.get("model_route", "unknown")

    # Build rich breakdown and explanation
    breakdown = _build_breakdown(score, features, predicted_weight)
    explanation = _build_explanation(score, features, model_route, body.location)

    # Log prediction for performance tracking
    _log_prediction(
        location=body.location,
        date=body.date,
        score=score,
        predicted_weight=predicted_weight,
        model_route=model_route,
        model_version=model_version,
        has_weather=bool(features.get("_raw_weather")),
        has_usgs=bool(features.get("_raw_usgs")),
        user_id=user.get("user_id") if user else None,
    )

    # Include conformal prediction interval if available
    interval_data = None
    pi = rd.get("prediction_interval")
    if pi is not None:
        interval_data = pi

    response_data = PredictResponse(
        fishing_score=score,
        confidence=round(confidence, 3),
        model_version=model_version,
        breakdown=breakdown,
        conditions={
            "location": body.location,
            "date": body.date,
            "predicted_weight_lb": round(predicted_weight, 2),
            "historical_avg_lb": round(hist_avg, 2),
            "data_sources": {
                "weather": "live" if features.get("_raw_weather") else "unavailable",
                "usgs": "live" if features.get("_raw_usgs") else "unavailable",
                "solunar": "computed",
                "biology": "modeled",
            },
        },
        explanation=explanation,
        prediction_interval=interval_data,
    )

    # Cache the prediction result (1-hour TTL for current conditions)
    await set_cached_prediction(
        redis, cache_key, response_data.model_dump(), settings.cache_conditions_ttl
    )
    await track_cache_key_for_location(redis, body.location, cache_key)

    return response_data


@router.post("/predict/batch")
async def batch_predict(
    request: Request,
    body: BatchPredictRequest,
    user: dict | None = Depends(get_optional_user),
):
    """Predict for multiple locations with real-time feature collection.

    JWT authentication optional (unauthenticated users get limited data).
    Uses per-location caching — cache hits skip feature collection.
    """
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(status_code=503, detail="ML model not loaded")

    redis = getattr(request.app.state, "redis", None)
    model_version = predictor.model_metadata.get("model_version", "v13")
    results = []

    for entry in body.predictions:
        try:
            # Check per-location cache
            cache_key = prediction_cache_key(entry.location, entry.date, model_version)
            cached = await get_cached_prediction(redis, cache_key)
            if cached:
                cached["_cached"] = True
                results.append(cached)
                continue

            await track_popular_location(redis, entry.location)

            features = await collect_realtime_features(
                location=entry.location,
                date=entry.date,
                usgs_site_id=entry.usgs_site_id or None,
                redis=redis,
            )
            result = predictor.predict(
                location=entry.location,
                date=entry.date,
                usgs_site_id=entry.usgs_site_id or "",
                precomputed_features={
                    k: v for k, v in features.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                },
            )
            rd = result.to_dict()
            hist_avg = predictor.location_means.get(entry.location, 3.0)
            score = _weight_to_fishing_score(rd["predicted_weight_lb"], hist_avg)
            rd["fishing_score"] = score
            rd["explanation"] = _build_explanation(
                score, features, rd.get("model_route", "unknown"), entry.location,
            )

            # Cache individual result
            await set_cached_prediction(
                redis, cache_key, rd, settings.cache_conditions_ttl
            )
            await track_cache_key_for_location(redis, entry.location, cache_key)

            results.append(rd)
        except Exception as exc:
            logger.error("Batch predict error for %s: %s", entry.location, exc)
            results.append({"location": entry.location, "error": str(exc)})

    return {"predictions": results}


@router.get("/forecast", response_model=ForecastV2Response)
async def forecast_v2(
    request: Request,
    location: str = Query(...),
    usgs_site_id: str = Query(""),
    date: str = Query(""),
    user: dict | None = Depends(get_optional_user),
):
    """7-day fishing forecast for a location with real-time features.

    JWT authentication optional (unauthenticated users get limited data).
    Cached for 6 hours (forecasts change less frequently).
    """
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(status_code=503, detail="ML model not loaded")

    try:
        base = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.utcnow().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    redis = getattr(request.app.state, "redis", None)
    model_version = predictor.model_metadata.get("model_version", "v13")

    # Check forecast cache (6-hour TTL)
    f_cache_key = forecast_cache_key(location, base.isoformat(), model_version)
    cached = await get_cached_prediction(redis, f_cache_key)
    if cached:
        cached["_cached"] = True
        return ForecastV2Response(**cached)

    await track_popular_location(redis, location)

    hist_avg = predictor.location_means.get(location, 3.0)
    daily = []

    for offset in range(7):
        day = base + timedelta(days=offset)
        day_str = day.isoformat()
        try:
            # Only fetch real-time features for today/tomorrow (cache beyond that)
            features = await collect_realtime_features(
                location=location,
                date=day_str,
                usgs_site_id=usgs_site_id or None,
                redis=redis,
            )
            result = predictor.predict(
                location=location,
                date=day_str,
                usgs_site_id=usgs_site_id,
                precomputed_features={
                    k: v for k, v in features.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                },
            )
            rd = result.to_dict()
            score = _weight_to_fishing_score(rd["predicted_weight_lb"], hist_avg)
            daily.append({
                "date": day_str,
                "fishing_score": score,
                "label": _score_label(score),
                "predicted_weight_lb": rd["predicted_weight_lb"],
                "explanation": _build_explanation(
                    score, features, rd.get("model_route", "unknown"), location,
                ),
            })
        except Exception:
            daily.append({
                "date": day_str,
                "fishing_score": None,
                "label": "Unknown",
                "predicted_weight_lb": None,
                "explanation": "Forecast unavailable for this date.",
            })

    response_data = ForecastV2Response(
        location=location,
        start_date=base.isoformat(),
        forecast=daily,
    )

    # Cache forecast (6-hour TTL)
    await set_cached_prediction(
        redis, f_cache_key, response_data.model_dump(), settings.cache_forecast_ttl
    )
    await track_cache_key_for_location(redis, location, f_cache_key)

    return response_data


@router.get("/best-fishing", response_model=BestFishingResponse)
async def best_fishing_v2(
    request: Request,
    date: str = Query(...),
    top_n: int = Query(10, ge=1, le=50),
    user: dict | None = Depends(get_optional_user),
):
    """Rank locations by composite fishing score for a given date.

    JWT authentication optional (unauthenticated users get limited data).
    """
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(status_code=503, detail="ML model not loaded")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    ranked = []
    for loc in predictor.location_means:
        try:
            result = predictor.predict(location=loc, date=date)
            rd = result.to_dict()
            hist_avg = predictor.location_means.get(loc, 3.0)
            score = _weight_to_fishing_score(rd["predicted_weight_lb"], hist_avg)
            ranked.append({
                "location": loc,
                "fishing_score": score,
                "label": _score_label(score),
                "predicted_weight_lb": rd["predicted_weight_lb"],
                "historical_avg_lb": round(hist_avg, 2),
            })
        except Exception:
            continue

    ranked.sort(key=lambda x: x["fishing_score"], reverse=True)

    return BestFishingResponse(
        date=date,
        top_locations=ranked[:top_n],
        total_evaluated=len(ranked),
    )


@router.get("/model/info")
async def model_info(request: Request):
    """Return model metadata and available locations.

    Public endpoint — no auth required.
    """
    predictor = request.app.state.predictor
    if predictor is None:
        return {"status": "no_model", "error": "Model not trained or loaded"}

    meta = {k: v for k, v in predictor.model_metadata.items()
            if k != "location_means"}
    return {
        "status": "loaded",
        "model_metadata": meta,
        "available_locations": sorted(predictor.location_means.keys())[:50],
        "total_locations": len(predictor.location_means),
        "feature_count": len(predictor.feature_names)
        if hasattr(predictor, "feature_names") else 0,
    }
