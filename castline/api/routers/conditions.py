"""Conditions and forecast endpoints."""
from datetime import datetime, timedelta, timezone

import orjson
from fastapi import APIRouter, Request, Query

from castline.api.config import settings
from castline.api.models.schemas import (
    ConditionsResponse, WeatherData, WaterData, SpeciesActivity,
    ForecastResponse, ForecastPoint,
)

router = APIRouter()


def _score_to_label(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    return "Poor"


def _compute_heuristic_score(weather: dict | None, water: dict | None) -> float:
    """Compute a heuristic fishing score from weather + water data when ML model isn't loaded."""
    score = 50.0
    factors = []

    if weather:
        # Pressure trend (falling = good, rising = bad for fishing)
        trend = weather.get("pressure_trend", "steady")
        if trend == "falling":
            score += 12
            factors.append("Falling pressure")
        elif trend == "rising":
            score -= 5
            factors.append("Rising pressure")

        # Wind (light 5-15mph is ideal)
        wind = weather.get("wind_mph", 10)
        if 5 <= wind <= 15:
            score += 8
        elif wind > 25:
            score -= 10
            factors.append("High wind")

        # Cloud cover (overcast = better for most species)
        clouds = weather.get("cloud_cover_pct", 50)
        if clouds > 70:
            score += 6
            factors.append("Overcast skies")
        elif clouds < 15:
            score -= 3

        # Temperature comfort zone (60-80F)
        temp = weather.get("temp_f")
        if temp and 60 <= temp <= 80:
            score += 5

    if water:
        # Stable or slightly rising water is good
        level_trend = water.get("level_trend", "stable")
        if level_trend == "stable":
            score += 3
        elif level_trend == "rising":
            score += 5
            factors.append("Rising water levels")

    return max(0, min(100, round(score, 1))), factors


@router.get("/conditions", response_model=ConditionsResponse)
async def get_conditions(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """Get current fishing conditions for a location."""
    redis = request.app.state.redis
    predictor = request.app.state.predictor

    # Check cache
    cache_key = f"conditions:{round(lat, 2)}:{round(lon, 2)}:{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')}"
    cached = await redis.get(cache_key)
    if cached:
        return ConditionsResponse(**orjson.loads(cached))

    if predictor is not None:
        # ML model available — use it
        result = predictor.predict(lat=lat, lon=lon)
        score = result.get("conditions_score", 50.0)
        confidence = result.get("confidence", "medium")
        summary = result.get("summary", "")
        top_factors = result.get("top_factors", [])
        location_name = result.get("location_name", f"({lat:.2f}, {lon:.2f})")

        weather = WeatherData(
            temp_f=result.get("temp_f"),
            wind_mph=result.get("wind_mph"),
            pressure_mb=result.get("pressure_mb"),
            pressure_trend=result.get("pressure_trend"),
            humidity_pct=result.get("humidity_pct"),
            cloud_cover_pct=result.get("cloud_cover_pct"),
        )
        water = WaterData(
            temp_f=result.get("water_temp_f"),
            discharge_cfs=result.get("discharge_cfs"),
            level_ft=result.get("gage_height_ft"),
            level_trend=result.get("level_trend"),
            source=result.get("water_source", "USGS"),
        )
        species_list = [
            SpeciesActivity(**sp) for sp in result.get("species", [])
        ]
    else:
        # No ML model — fetch real data from services and compute heuristic score
        from castline.api.services.weather import get_current_weather
        from castline.api.services.usgs import get_nearest_usgs_site, get_usgs_conditions
        from castline.api.services.geocoding import reverse_geocode

        # Fetch weather, water, and location name in parallel
        import asyncio
        weather_data, site_info, location_name = await asyncio.gather(
            get_current_weather(lat, lon, redis=redis),
            get_nearest_usgs_site(lat, lon, redis=redis),
            reverse_geocode(lat, lon, redis=redis),
        )

        location_name = location_name or f"({lat:.2f}, {lon:.2f})"

        # Build weather response
        if weather_data:
            weather = WeatherData(
                temp_f=weather_data.get("temp_f"),
                wind_mph=weather_data.get("wind_mph"),
                pressure_mb=weather_data.get("pressure_mb"),
                pressure_trend=weather_data.get("pressure_trend"),
                humidity_pct=weather_data.get("humidity_pct"),
                cloud_cover_pct=weather_data.get("cloud_cover_pct"),
            )
        else:
            weather = WeatherData()

        # Fetch water conditions if we found a nearby USGS site
        water_data = None
        if site_info:
            water_data = await get_usgs_conditions(site_info["site_id"], redis=redis)

        if water_data:
            water = WaterData(
                temp_f=water_data.get("water_temp_f"),
                discharge_cfs=water_data.get("discharge_cfs"),
                level_ft=water_data.get("gage_height_ft"),
                level_trend=water_data.get("level_trend"),
                source="USGS",
            )
        else:
            water = WaterData()

        # Compute heuristic score from real data
        score, top_factors = _compute_heuristic_score(
            weather_data,
            water_data,
        )
        confidence = "medium" if (weather_data and water_data) else "low"
        summary = f"Based on current weather and water conditions near {location_name}"
        species_list = []

    response = ConditionsResponse(
        score=score,
        confidence=confidence,
        label=_score_to_label(score),
        summary=summary,
        location_name=location_name,
        weather=weather,
        water=water,
        species=species_list,
        top_factors=top_factors,
        updated_at=datetime.now(timezone.utc),
    )

    # Cache
    await redis.set(
        cache_key,
        orjson.dumps(response.model_dump(), default=str).decode(),
        ex=settings.cache_conditions_ttl,
    )
    return response


@router.get("/conditions/forecast", response_model=ForecastResponse)
async def get_forecast(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    hours: int = Query(72, ge=6, le=168),
):
    """Get fishing conditions forecast for the next N hours."""
    redis = request.app.state.redis

    cache_key = f"forecast:{round(lat, 2)}:{round(lon, 2)}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    cached = await redis.get(cache_key)
    if cached:
        return ForecastResponse(**orjson.loads(cached))

    # Fetch real forecast from Open-Meteo
    from castline.api.services.weather import get_weather_forecast
    from castline.api.services.geocoding import reverse_geocode

    forecast_data, location_name = await __import__('asyncio').gather(
        get_weather_forecast(lat, lon, hours=hours, redis=redis),
        reverse_geocode(lat, lon, redis=redis),
    )

    location_name = location_name or f"({lat:.2f}, {lon:.2f})"

    forecast_points = []
    best_score = 0
    best_time = None

    if forecast_data and isinstance(forecast_data, list):
        for entry in forecast_data:
            t = datetime.fromisoformat(entry["time"]) if isinstance(entry["time"], str) else entry["time"]

            # Compute forecast score from weather params
            hour_of_day = t.hour
            # Dawn/dusk bonus
            dawn_dusk_bonus = 10 * max(0, 1 - min(abs(hour_of_day - 6), abs(hour_of_day - 18)) / 3)

            base = 50 + dawn_dusk_bonus

            # Pressure trend
            trend = entry.get("pressure_trend", "steady")
            if trend == "falling":
                base += 10

            # Cloud cover
            clouds = entry.get("cloud_cover_pct", 50)
            if clouds > 60:
                base += 5

            # Wind
            wind = entry.get("wind_mph", 10)
            if wind > 25:
                base -= 10
            elif 5 <= wind <= 15:
                base += 5

            score = max(0, min(100, round(base, 1)))

            # Weather summary
            temp = entry.get("temp_f")
            weather_parts = []
            if temp is not None:
                weather_parts.append(f"{temp:.0f}°F")
            if wind:
                weather_parts.append(f"{wind:.0f} mph wind")
            weather_summary = ", ".join(weather_parts) if weather_parts else None

            forecast_points.append(ForecastPoint(
                time=t,
                score=score,
                label=_score_to_label(score),
                weather_summary=weather_summary,
            ))

            if score > best_score:
                best_score = score
                best_time = t
    else:
        # Fallback with diurnal pattern
        now = datetime.now(timezone.utc)
        for h in range(0, hours, 6):
            t = now + timedelta(hours=h)
            hour_of_day = (t.hour - 4) % 24
            base = 55 + 15 * max(0, 1 - abs(hour_of_day - 6) / 6)
            forecast_points.append(ForecastPoint(
                time=t,
                score=round(base, 1),
                label=_score_to_label(base),
                weather_summary=None,
            ))

    # Determine best window
    if best_time:
        best_window = best_time.strftime("%A %-I %p")
    else:
        best_window = "Tomorrow 6-9 AM"

    response = ForecastResponse(
        location_name=location_name,
        lat=lat,
        lon=lon,
        forecast=forecast_points,
        best_window=best_window,
    )

    await redis.set(
        cache_key,
        orjson.dumps(response.model_dump(), default=str).decode(),
        ex=settings.cache_forecast_ttl,
    )
    return response
