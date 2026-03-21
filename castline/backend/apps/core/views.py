"""CASTLINE API views for fishing predictions."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

# Lazy-loaded predictor singleton
_predictor = None
_MODEL_DIR = Path(__file__).resolve().parents[3] / "validation" / "data" / "models" / "production"


def _get_predictor():
    global _predictor
    if _predictor is None:
        from castline.validation.models.inference import CastlinePredictor

        if _MODEL_DIR.exists():
            _predictor = CastlinePredictor.load(_MODEL_DIR)
            print(f"castline: loaded model from {_MODEL_DIR}", file=sys.stderr)
        else:
            raise FileNotFoundError(f"No trained model at {_MODEL_DIR}")
    return _predictor


@csrf_exempt
@require_http_methods(["POST"])
def predict(request):
    """Predict fishing success for a location and date.

    POST /api/predict/
    {
        "location": "Lake Guntersville, Guntersville, AL",
        "date": "2025-04-15",
        "usgs_site_id": "03572110"
    }
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    location = body.get("location", "")
    date = body.get("date", "")
    usgs_site_id = body.get("usgs_site_id", "")

    if not location or not date:
        return JsonResponse(
            {"error": "location and date are required"},
            status=400,
        )

    try:
        predictor = _get_predictor()
        result = predictor.predict(
            location=location,
            date=date,
            usgs_site_id=usgs_site_id,
        )
        return JsonResponse(result.to_dict())
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse({"error": f"Prediction failed: {exc}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def batch_predict(request):
    """Predict for multiple locations.

    POST /api/predict/batch/
    {
        "predictions": [
            {"location": "Lake Guntersville, AL", "date": "2025-04-15", "usgs_site_id": "03572110"},
            {"location": "Lake Fork, TX", "date": "2025-04-15", "usgs_site_id": "08018500"}
        ]
    }
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    entries = body.get("predictions", [])
    if not entries:
        return JsonResponse({"error": "predictions array required"}, status=400)

    try:
        predictor = _get_predictor()
        results = predictor.batch_predict(entries)
        return JsonResponse({"predictions": [r.to_dict() for r in results]})
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse({"error": f"Batch prediction failed: {exc}"}, status=500)


def model_info(request):
    """Return model metadata and available locations.

    GET /api/model/info/
    """
    try:
        predictor = _get_predictor()
        meta = {k: v for k, v in predictor.model_metadata.items()
                if k != "location_means"}
        return JsonResponse({
            "status": "loaded",
            "model_metadata": meta,
            "available_locations": sorted(predictor.location_means.keys()),
            "feature_count": len(predictor.feature_names),
            "features": predictor.feature_names,
        })
    except FileNotFoundError:
        return JsonResponse({"status": "no_model", "error": "Model not trained"}, status=503)


def locations(request):
    """Return all known locations with their historical averages and USGS site IDs.

    GET /api/locations/
    """
    try:
        predictor = _get_predictor()
        # Load USGS mapping
        mapping_path = _MODEL_DIR.parents[2] / "data" / "raw" / "bassmaster_usgs_mapping.csv"
        usgs_map = {}
        if mapping_path.exists():
            import csv
            with open(mapping_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    loc = row.get("location", "")
                    site = row.get("usgs_site_id", "")
                    if loc and site:
                        usgs_map[loc] = site

        location_data = []
        for loc, mean_weight in sorted(predictor.location_means.items()):
            location_data.append({
                "location": loc,
                "historical_avg_weight_lb": round(mean_weight, 2),
                "usgs_site_id": usgs_map.get(loc, ""),
            })
        return JsonResponse({"locations": location_data})
    except FileNotFoundError:
        return JsonResponse({"error": "Model not trained"}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def best_fishing(request):
    """Find the best fishing locations for a given date.

    POST /api/best-fishing/
    {
        "date": "2025-04-15",
        "top_n": 10
    }

    Returns the top N locations ranked by predicted weight.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    date = body.get("date", "")
    top_n = min(body.get("top_n", 10), 50)

    if not date:
        return JsonResponse({"error": "date is required"}, status=400)

    try:
        predictor = _get_predictor()

        # Load USGS mapping
        mapping_path = _MODEL_DIR.parents[2] / "data" / "raw" / "bassmaster_usgs_mapping.csv"
        usgs_map = {}
        if mapping_path.exists():
            import csv
            with open(mapping_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    loc = row.get("location", "")
                    site = row.get("usgs_site_id", "")
                    if loc and site:
                        usgs_map[loc] = site

        # Predict for all known locations
        predictions = []
        for loc in predictor.location_means:
            usgs_id = usgs_map.get(loc, "")
            if not usgs_id:
                continue
            try:
                result = predictor.predict(
                    location=loc, date=date, usgs_site_id=usgs_id,
                )
                predictions.append(result.to_dict())
            except Exception:
                continue

        # Sort by predicted weight descending
        predictions.sort(key=lambda x: x["predicted_weight_lb"], reverse=True)

        return JsonResponse({
            "date": date,
            "top_locations": predictions[:top_n],
            "total_evaluated": len(predictions),
        })
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse({"error": f"Failed: {exc}"}, status=500)


# ---------------------------------------------------------------------------
# v2 API — 4-layer decision system
# ---------------------------------------------------------------------------

def _weight_to_fishing_score(predicted_weight: float, hist_avg: float) -> int:
    """Convert predicted weight to a 0-100 fishing score.

    Uses the ratio of predicted weight to the location's historical average
    to produce a human-friendly score.  A prediction at the historical average
    maps to 50; double the average maps to ~90.
    """
    if hist_avg <= 0:
        hist_avg = 3.0  # fallback baseline
    ratio = predicted_weight / hist_avg
    # Logistic-style mapping: ratio 1.0 -> 50, 2.0 -> ~90, 0.5 -> ~20
    score = 100.0 / (1.0 + 2.718 ** (-3.5 * (ratio - 1.0)))
    return max(0, min(100, int(round(score))))


def _build_breakdown(result_dict: dict, score: int) -> dict:
    """Build the 4-layer decision breakdown from a prediction result.

    Dynamically adjusts layer contributions based on which environmental
    data sources are available (live USGS hydro, weather, etc.).
    """
    weight = result_dict.get("predicted_weight_lb", 0)
    conditions = result_dict.get("conditions", {})
    features = result_dict.get("feature_contributions", {})

    # Determine data availability for dynamic weighting
    has_hydro = any(
        conditions.get(k) is not None
        for k in ("discharge_cfs", "gage_height_ft", "water_temp_c",
                   "discharge_delta_1d", "flow_regime")
    )
    has_weather = any(
        conditions.get(k) is not None
        for k in ("pressure_delta_6h", "wind_speed_kph", "temp_mean",
                   "hours_since_front", "stability_index")
    )

    # Allocate layer weights based on data availability
    # When live data is present, give that layer more weight
    hydro_w = 0.40 if has_hydro else 0.25
    weather_w = 0.30 if has_weather else 0.20
    bio_w = 0.20
    history_w = 1.0 - hydro_w - weather_w - bio_w

    # Build layer descriptors with contextual descriptions
    hydro_desc = "Flow rate, water level, and temperature signals"
    if has_hydro:
        parts = []
        if conditions.get("flow_regime"):
            parts.append(f"{conditions['flow_regime']} flow")
        if conditions.get("discharge_zscore") is not None:
            zs = conditions["discharge_zscore"]
            if abs(zs) < 0.5:
                parts.append("normal discharge")
            elif zs > 1.5:
                parts.append("high discharge")
            elif zs < -1.5:
                parts.append("low discharge")
        if parts:
            hydro_desc = "Live: " + ", ".join(parts)

    weather_desc = "Air temp, pressure, wind, precipitation outlook"
    if has_weather and conditions.get("hours_since_front") is not None:
        hsf = conditions["hours_since_front"]
        if 24 <= hsf <= 72:
            weather_desc = "Post-frontal recovery window — prime conditions"
        elif hsf < 6:
            weather_desc = "Recent front passage — fish may be inactive"

    return {
        "fishing_score": score,
        "layers": {
            "hydrology": {
                "label": "Water Conditions",
                "description": hydro_desc,
                "contribution": round(score * hydro_w),
                "data_quality": "live" if has_hydro else "estimated",
            },
            "weather": {
                "label": "Weather",
                "description": weather_desc,
                "contribution": round(score * weather_w),
                "data_quality": "live" if has_weather else "estimated",
            },
            "biology": {
                "label": "Biological Activity",
                "description": "Seasonal patterns, spawn timing, forage availability",
                "contribution": round(score * bio_w),
                "data_quality": "modeled",
            },
            "history": {
                "label": "Historical Performance",
                "description": "Past tournament and creel survey data for this location",
                "contribution": round(score * history_w),
                "data_quality": "historical",
            },
        },
        "predicted_weight_lb": weight,
    }


def _score_explanation(score: int) -> str:
    """Return a plain-English explanation for the fishing score."""
    if score >= 80:
        return "Excellent conditions — strong bite expected across multiple factors."
    if score >= 60:
        return "Good conditions — above-average activity likely."
    if score >= 40:
        return "Fair conditions — average fishing expected."
    if score >= 20:
        return "Below average — consider waiting for better conditions."
    return "Poor conditions — most factors are unfavorable right now."


@csrf_exempt
@require_http_methods(["POST"])
def predict_v2(request):
    """Composite-score prediction using the 4-layer decision system.

    POST /api/v2/predict/
    {
        "location": "Lake Guntersville, Guntersville, AL",
        "date": "2025-04-15",
        "usgs_site_id": "03572110"
    }

    Returns fishing_score (0-100), confidence, breakdown, conditions,
    and a plain-English explanation.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    location = body.get("location", "")
    date = body.get("date", "")
    usgs_site_id = body.get("usgs_site_id", "")

    if not location or not date:
        return JsonResponse(
            {"error": "location and date are required"}, status=400,
        )

    try:
        predictor = _get_predictor()
        result = predictor.predict(
            location=location, date=date, usgs_site_id=usgs_site_id,
        )
        rd = result.to_dict()
        hist_avg = predictor.location_means.get(location, 3.0)
        score = _weight_to_fishing_score(rd["predicted_weight_lb"], hist_avg)
        confidence = min(1.0, max(0.0, 1.0 - abs(rd.get("residual", 0.5))))

        # Log prediction for negative signal tracking
        model_version = rd.get("model_used", "v5")
        prediction_log_id = _log_prediction(
            user_id=body.get("user_id", ""),
            location=location,
            prediction_date=date,
            predicted_weight_lb=rd["predicted_weight_lb"],
            fishing_score=score,
            confidence=confidence,
            usgs_site_id=usgs_site_id,
            feature_snapshot=rd.get("features", {}),
            model_version=model_version,
        )

        response_data = {
            "fishing_score": score,
            "confidence": round(confidence, 3),
            "model_version": model_version,
            "breakdown": _build_breakdown(rd, score),
            "conditions": {
                "location": location,
                "date": date,
                "predicted_weight_lb": rd["predicted_weight_lb"],
                "historical_avg_lb": round(hist_avg, 2),
            },
            "explanation": _score_explanation(score),
        }
        if prediction_log_id:
            response_data["prediction_id"] = prediction_log_id

        return JsonResponse(response_data)
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse(
            {"error": f"Prediction failed: {exc}"}, status=500,
        )


@require_http_methods(["GET"])
def forecast_v2(request):
    """7-day fishing forecast for a location.

    GET /api/v2/forecast/?location=Lake+Guntersville&usgs_site_id=03572110&date=2025-04-15
    """
    location = request.GET.get("location", "")
    usgs_site_id = request.GET.get("usgs_site_id", "")
    start_date = request.GET.get("date", "")

    if not location:
        return JsonResponse({"error": "location is required"}, status=400)

    try:
        base = (
            datetime.strptime(start_date, "%Y-%m-%d").date()
            if start_date
            else datetime.utcnow().date()
        )
    except ValueError:
        return JsonResponse(
            {"error": "date must be YYYY-MM-DD format"}, status=400,
        )

    try:
        predictor = _get_predictor()
        hist_avg = predictor.location_means.get(location, 3.0)

        daily = []
        for offset in range(7):
            day = base + timedelta(days=offset)
            day_str = day.isoformat()
            try:
                result = predictor.predict(
                    location=location,
                    date=day_str,
                    usgs_site_id=usgs_site_id,
                )
                rd = result.to_dict()
                score = _weight_to_fishing_score(
                    rd["predicted_weight_lb"], hist_avg,
                )
                daily.append({
                    "date": day_str,
                    "fishing_score": score,
                    "predicted_weight_lb": rd["predicted_weight_lb"],
                    "explanation": _score_explanation(score),
                })
            except Exception:
                daily.append({
                    "date": day_str,
                    "fishing_score": None,
                    "predicted_weight_lb": None,
                    "explanation": "Forecast unavailable for this date.",
                })

        return JsonResponse({
            "location": location,
            "start_date": base.isoformat(),
            "forecast": daily,
        })
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse(
            {"error": f"Forecast failed: {exc}"}, status=500,
        )


@csrf_exempt
@require_http_methods(["POST"])
def catch_report(request):
    """Submit a user catch report.

    POST /api/v2/catch-report/
    {
        "user_id": "abc123",
        "lat": 34.35,
        "lon": -86.3,
        "reported_at": "2025-04-15T18:30:00Z",
        "trip_start": "2025-04-15T06:00:00Z",
        "trip_end": "2025-04-15T14:00:00Z",
        "effort_hours": 8.0,
        "species": "largemouth_bass",
        "catch_count": 5,
        "kept_count": 0,
        "largest_weight_lb": 4.2,
        "rating": 4,
        "conditions_snapshot": {}
    }
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # --- Validate required fields ---
    required = ["user_id", "lat", "lon", "trip_start", "trip_end",
                 "effort_hours", "catch_count", "rating"]
    missing = [f for f in required if f not in body]
    if missing:
        return JsonResponse(
            {"error": f"Missing required fields: {', '.join(missing)}"},
            status=400,
        )

    # --- Type / range validation ---
    errors = []
    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
        if not (-90 <= lat <= 90):
            errors.append("lat must be between -90 and 90")
        if not (-180 <= lon <= 180):
            errors.append("lon must be between -180 and 180")
    except (TypeError, ValueError):
        errors.append("lat and lon must be numeric")

    try:
        effort = float(body["effort_hours"])
        if effort <= 0 or effort > 48:
            errors.append("effort_hours must be between 0 and 48")
    except (TypeError, ValueError):
        errors.append("effort_hours must be numeric")

    try:
        rating = int(body["rating"])
        if rating < 1 or rating > 5:
            errors.append("rating must be 1-5")
    except (TypeError, ValueError):
        errors.append("rating must be an integer 1-5")

    catch_count = body.get("catch_count", 0)
    kept_count = body.get("kept_count", 0)
    try:
        catch_count = int(catch_count)
        kept_count = int(kept_count)
        if catch_count < 0:
            errors.append("catch_count must be >= 0")
        if kept_count < 0:
            errors.append("kept_count must be >= 0")
        if kept_count > catch_count:
            errors.append("kept_count cannot exceed catch_count")
    except (TypeError, ValueError):
        errors.append("catch_count and kept_count must be integers")

    # Validate datetime fields
    dt_fields = ["trip_start", "trip_end"]
    if "reported_at" in body:
        dt_fields.append("reported_at")
    for dtf in dt_fields:
        val = body.get(dtf, "")
        if val:
            try:
                datetime.fromisoformat(val.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                errors.append(f"{dtf} must be ISO-8601 datetime")

    if errors:
        return JsonResponse({"error": "; ".join(errors)}, status=400)

    # --- Persist ---
    try:
        from castline.backend.apps.core.models import CatchReport as CRModel

        reported_at = body.get("reported_at")
        if reported_at:
            reported_at = datetime.fromisoformat(
                reported_at.replace("Z", "+00:00"),
            )
        else:
            from django.utils import timezone
            reported_at = timezone.now()

        report = CRModel.objects.create(
            user_id=body["user_id"],
            lat=float(body["lat"]),
            lon=float(body["lon"]),
            reported_at=reported_at,
            trip_start=datetime.fromisoformat(
                body["trip_start"].replace("Z", "+00:00"),
            ),
            trip_end=datetime.fromisoformat(
                body["trip_end"].replace("Z", "+00:00"),
            ),
            effort_hours=float(body["effort_hours"]),
            species=body.get("species", "largemouth_bass"),
            catch_count=catch_count,
            kept_count=kept_count,
            largest_weight_lb=(
                float(body["largest_weight_lb"])
                if body.get("largest_weight_lb") is not None
                else None
            ),
            rating=int(body["rating"]),
            conditions_snapshot=body.get("conditions_snapshot", {}),
        )

        # Auto-resolve matching prediction logs (negative signal tracking)
        resolved_signals = _auto_resolve_predictions(report)

        response = {"id": report.id, "status": "created"}
        if resolved_signals:
            response["resolved_predictions"] = resolved_signals
        return JsonResponse(response, status=201)
    except Exception as exc:
        # Fallback: write to a JSONL file if the DB is not migrated yet
        fallback_path = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "catch_reports.jsonl"
        )
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_path, "a") as f:
            body["_fallback_error"] = str(exc)
            body["_saved_at"] = datetime.utcnow().isoformat()
            f.write(json.dumps(body) + "\n")
        return JsonResponse(
            {"status": "saved_to_file", "note": "DB unavailable, stored locally"},
            status=201,
        )


@require_http_methods(["GET"])
def best_fishing_v2(request):
    """Rank locations by composite fishing score for a given date.

    GET /api/v2/best-fishing/?date=2025-04-15&top_n=10&species=largemouth_bass
    """
    date = request.GET.get("date", "")
    if not date:
        return JsonResponse({"error": "date query param is required"}, status=400)

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return JsonResponse(
            {"error": "date must be YYYY-MM-DD format"}, status=400,
        )

    top_n = min(int(request.GET.get("top_n", 10)), 50)

    try:
        predictor = _get_predictor()

        # Load USGS mapping
        mapping_path = (
            _MODEL_DIR.parents[2] / "data" / "raw" / "bassmaster_usgs_mapping.csv"
        )
        usgs_map = {}
        if mapping_path.exists():
            import csv
            with open(mapping_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    loc = row.get("location", "")
                    site = row.get("usgs_site_id", "")
                    if loc and site:
                        usgs_map[loc] = site

        ranked = []
        for loc in predictor.location_means:
            usgs_id = usgs_map.get(loc, "")
            if not usgs_id:
                continue
            try:
                result = predictor.predict(
                    location=loc, date=date, usgs_site_id=usgs_id,
                )
                rd = result.to_dict()
                hist_avg = predictor.location_means.get(loc, 3.0)
                score = _weight_to_fishing_score(
                    rd["predicted_weight_lb"], hist_avg,
                )
                ranked.append({
                    "location": loc,
                    "fishing_score": score,
                    "predicted_weight_lb": rd["predicted_weight_lb"],
                    "historical_avg_lb": round(hist_avg, 2),
                    "explanation": _score_explanation(score),
                })
            except Exception:
                continue

        ranked.sort(key=lambda x: x["fishing_score"], reverse=True)

        return JsonResponse({
            "date": date,
            "top_locations": ranked[:top_n],
            "total_evaluated": len(ranked),
        })
    except FileNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse(
            {"error": f"Ranking failed: {exc}"}, status=500,
        )


# ---------------------------------------------------------------------------
# Negative Signal Logging
# ---------------------------------------------------------------------------

def _auto_resolve_predictions(catch_report) -> list:
    """Find unresolved PredictionLogs that match this catch report and resolve them.

    Matching criteria: same user_id + prediction_date matches trip_start date.
    Returns a list of signal classifications for the response.
    """
    try:
        from castline.backend.apps.core.models import PredictionLog

        matching = PredictionLog.objects.filter(
            user_id=catch_report.user_id,
            prediction_date=catch_report.trip_start.date(),
            signal_type=PredictionLog.SignalType.UNRESOLVED,
        )

        resolved = []
        for pred_log in matching:
            pred_log.resolve(catch_report)
            resolved.append({
                "prediction_id": pred_log.id,
                "signal_type": pred_log.signal_type,
                "signal_label": pred_log.get_signal_type_display(),
            })
        return resolved
    except Exception:
        return []


def _log_prediction(
    user_id: str,
    location: str,
    prediction_date: str,
    predicted_weight_lb: float,
    fishing_score: int,
    confidence: float,
    usgs_site_id: str = "",
    feature_snapshot: dict | None = None,
    model_version: str = "v5",
) -> int | None:
    """Persist a PredictionLog entry. Returns the log ID or None on failure.

    This runs fire-and-forget — prediction serving is never blocked by
    logging failures.
    """
    try:
        from castline.backend.apps.core.models import PredictionLog

        log_entry = PredictionLog.objects.create(
            user_id=user_id or "",
            location=location,
            prediction_date=prediction_date,
            predicted_weight_lb=predicted_weight_lb,
            fishing_score=fishing_score,
            confidence=confidence,
            model_version=model_version,
            usgs_site_id=usgs_site_id,
            feature_snapshot=feature_snapshot or {},
        )
        return log_entry.id
    except Exception as exc:
        # Fallback: append to JSONL file
        try:
            fallback_path = (
                Path(__file__).resolve().parents[2]
                / "data"
                / "prediction_logs.jsonl"
            )
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            with open(fallback_path, "a") as f:
                f.write(_json.dumps({
                    "user_id": user_id,
                    "location": location,
                    "prediction_date": prediction_date,
                    "predicted_weight_lb": predicted_weight_lb,
                    "fishing_score": fishing_score,
                    "confidence": confidence,
                    "usgs_site_id": usgs_site_id,
                    "_error": str(exc),
                    "_ts": datetime.utcnow().isoformat(),
                }) + "\n")
        except Exception:
            pass
        return None


@csrf_exempt
@require_http_methods(["POST"])
def resolve_prediction(request):
    """Link a catch report to a prediction for negative signal analysis.

    POST /api/v2/resolve-prediction/
    {
        "prediction_id": 42,
        "catch_report_id": 17
    }

    Or auto-match by user + location + date:
    {
        "prediction_id": 42,
        "user_id": "abc123",
        "trip_date": "2025-04-15"
    }
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    prediction_id = body.get("prediction_id")
    if not prediction_id:
        return JsonResponse(
            {"error": "prediction_id is required"}, status=400,
        )

    try:
        from castline.backend.apps.core.models import (
            PredictionLog,
            CatchReport as CRModel,
        )

        pred_log = PredictionLog.objects.get(id=prediction_id)

        # Find the catch report
        catch_report_id = body.get("catch_report_id")
        if catch_report_id:
            report = CRModel.objects.get(id=catch_report_id)
        else:
            # Auto-match: find the most recent catch report from this user
            # on the same date, near the same location
            user_id = body.get("user_id", pred_log.user_id)
            reports = CRModel.objects.filter(
                user_id=user_id,
                trip_start__date=pred_log.prediction_date,
            ).order_by("-created_at")
            if not reports.exists():
                return JsonResponse(
                    {"error": "No matching catch report found"}, status=404,
                )
            report = reports.first()

        # Resolve the prediction with the catch report
        pred_log.resolve(report)

        return JsonResponse({
            "prediction_id": pred_log.id,
            "catch_report_id": report.id,
            "signal_type": pred_log.signal_type,
            "signal_label": pred_log.get_signal_type_display(),
            "prediction_error": pred_log.prediction_error,
            "score_vs_rating": round(pred_log.score_vs_rating, 3)
            if pred_log.score_vs_rating is not None
            else None,
        })
    except Exception as exc:
        return JsonResponse(
            {"error": f"Resolution failed: {exc}"}, status=500,
        )


@require_http_methods(["GET"])
def signal_dashboard(request):
    """Dashboard data for negative signal analysis.

    GET /api/v2/signals/?days=30&location=Lake+Guntersville
    """
    from django.db.models import Avg, Count, Q

    days = int(request.GET.get("days", 30))
    location = request.GET.get("location", "")

    try:
        from castline.backend.apps.core.models import PredictionLog

        cutoff = datetime.utcnow() - timedelta(days=days)
        qs = PredictionLog.objects.filter(predicted_at__gte=cutoff)
        if location:
            qs = qs.filter(location__icontains=location)

        total = qs.count()
        resolved = qs.exclude(
            signal_type=PredictionLog.SignalType.UNRESOLVED,
        ).count()

        signal_counts = qs.values("signal_type").annotate(
            count=Count("id"),
        ).order_by("-count")

        # Top false-positive locations (model's biggest systematic errors)
        fp_locations = (
            qs.filter(signal_type=PredictionLog.SignalType.FALSE_POSITIVE)
            .values("location")
            .annotate(
                count=Count("id"),
                avg_error=Avg("prediction_error"),
            )
            .order_by("-count")[:10]
        )

        # Top false-negative locations (missed opportunities)
        fn_locations = (
            qs.filter(signal_type=PredictionLog.SignalType.FALSE_NEGATIVE)
            .values("location")
            .annotate(
                count=Count("id"),
                avg_error=Avg("prediction_error"),
            )
            .order_by("-count")[:10]
        )

        # Accuracy metrics
        tp = qs.filter(signal_type="TP").count()
        tn = qs.filter(signal_type="TN").count()
        fp = qs.filter(signal_type="FP").count()
        fn = qs.filter(signal_type="FN").count()

        accuracy = (tp + tn) / max(1, tp + tn + fp + fn)
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)

        return JsonResponse({
            "period_days": days,
            "total_predictions": total,
            "total_resolved": resolved,
            "resolution_rate": round(resolved / max(1, total), 3),
            "signal_distribution": {
                item["signal_type"]: item["count"]
                for item in signal_counts
            },
            "metrics": {
                "accuracy": round(accuracy, 3),
                "precision": round(precision, 3),
                "recall": round(recall, 3),
            },
            "top_false_positive_locations": list(fp_locations),
            "top_false_negative_locations": list(fn_locations),
        })
    except Exception as exc:
        return JsonResponse(
            {"error": f"Dashboard failed: {exc}"}, status=500,
        )
