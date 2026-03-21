"""Open-Meteo weather client for current conditions and hourly forecasts."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from castline.api.config import settings

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Variables we request for current conditions
CURRENT_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "apparent_temperature",
    "weather_code",
]

# Variables we request for hourly forecast
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
]

# WMO weather codes to human-readable descriptions
WMO_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _c_to_f(c: Optional[float]) -> Optional[float]:
    """Convert Celsius to Fahrenheit."""
    if c is None:
        return None
    return round(c * 9.0 / 5.0 + 32.0, 1)


def _kmh_to_mph(kmh: Optional[float]) -> Optional[float]:
    """Convert km/h to mph."""
    if kmh is None:
        return None
    return round(kmh * 0.621371, 1)


def _hpa_to_mb(hpa: Optional[float]) -> Optional[float]:
    """Convert hectopascals to millibars (1:1, but round for display)."""
    if hpa is None:
        return None
    return round(hpa, 1)


def _wind_direction_str(deg: Optional[float]) -> Optional[str]:
    """Convert wind direction in degrees to a compass label."""
    if deg is None:
        return None
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                   "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(deg / 22.5) % 16
    return directions[idx]


def _mm_to_in(mm: Optional[float]) -> Optional[float]:
    """Convert millimeters to inches."""
    if mm is None:
        return None
    return round(mm * 0.03937, 2)


def _cache_key(lat: float, lon: float, suffix: str = "") -> str:
    """Build a Redis cache key with rounded coordinates."""
    lat_r = round(lat, 2)
    lon_r = round(lon, 2)
    base = f"weather:{lat_r}:{lon_r}"
    return f"{base}:{suffix}" if suffix else base


async def get_current_weather(
    lat: float,
    lon: float,
    redis=None,
) -> Optional[dict]:
    """Fetch current weather conditions from Open-Meteo.

    Returns a dict compatible with the ``WeatherData`` schema including
    temperature in Fahrenheit, wind in mph, and pressure in millibars.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        redis: Optional aioredis.Redis instance for caching.

    Returns:
        Dict with weather fields or None on failure.
    """
    ckey = _cache_key(lat, lon, "current")
    if redis:
        try:
            cached = await redis.get(ckey)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", ckey)

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(CURRENT_VARS),
        "hourly": "surface_pressure",
        "past_hours": 6,
        "forecast_hours": 1,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Open-Meteo current weather fetch failed: %s", exc)
        return None

    current = data.get("current", {})
    if not current:
        return None

    # Compute pressure trend from the hourly pressure data
    pressure_trend = _pressure_trend_from_hourly(data.get("hourly", {}))

    wmo_code = current.get("weather_code")
    conditions_text = WMO_DESCRIPTIONS.get(wmo_code, "Unknown") if wmo_code is not None else None

    result = {
        "temp_f": _c_to_f(current.get("temperature_2m")),
        "feels_like_f": _c_to_f(current.get("apparent_temperature")),
        "wind_mph": _kmh_to_mph(current.get("wind_speed_10m")),
        "wind_direction": _wind_direction_str(current.get("wind_direction_10m")),
        "pressure_mb": _hpa_to_mb(current.get("surface_pressure")),
        "pressure_trend": pressure_trend,
        "humidity_pct": current.get("relative_humidity_2m"),
        "cloud_cover_pct": current.get("cloud_cover"),
        "precip_in": _mm_to_in(current.get("precipitation")),
        "conditions": conditions_text,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    if redis:
        try:
            await redis.set(ckey, json.dumps(result), ex=settings.cache_weather_ttl)
        except Exception:
            logger.debug("Redis write failed for %s", ckey)

    return result


async def get_weather_forecast(
    lat: float,
    lon: float,
    hours: int = 72,
    redis=None,
) -> Optional[list[dict]]:
    """Fetch an hourly weather forecast from Open-Meteo.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        hours: Number of forecast hours to retrieve (default 72).
        redis: Optional aioredis.Redis instance for caching.

    Returns:
        List of hourly forecast dicts or None on failure.
    """
    ckey = _cache_key(lat, lon, f"forecast:{hours}")
    if redis:
        try:
            cached = await redis.get(ckey)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", ckey)

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARS),
        "forecast_hours": hours,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Open-Meteo forecast fetch failed: %s", exc)
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    n = len(times)
    forecast: list[dict] = []
    for i in range(n):
        wmo_code = _safe_index(hourly.get("weather_code", []), i)
        conditions_text = WMO_DESCRIPTIONS.get(wmo_code, "Unknown") if wmo_code is not None else None

        point = {
            "time": times[i],
            "temp_f": _c_to_f(_safe_index(hourly.get("temperature_2m", []), i)),
            "humidity_pct": _safe_index(hourly.get("relative_humidity_2m", []), i),
            "pressure_mb": _hpa_to_mb(_safe_index(hourly.get("surface_pressure", []), i)),
            "cloud_cover_pct": _safe_index(hourly.get("cloud_cover", []), i),
            "wind_mph": _kmh_to_mph(_safe_index(hourly.get("wind_speed_10m", []), i)),
            "wind_direction": _wind_direction_str(_safe_index(hourly.get("wind_direction_10m", []), i)),
            "precip_probability_pct": _safe_index(hourly.get("precipitation_probability", []), i),
            "precip_in": _mm_to_in(_safe_index(hourly.get("precipitation", []), i)),
            "conditions": conditions_text,
        }
        forecast.append(point)

    if redis:
        try:
            await redis.set(ckey, json.dumps(forecast), ex=settings.cache_forecast_ttl)
        except Exception:
            logger.debug("Redis write failed for %s", ckey)

    return forecast


def _safe_index(lst: list, idx: int):
    """Return lst[idx] if in bounds, else None."""
    if idx < len(lst):
        return lst[idx]
    return None


def _pressure_trend_from_hourly(hourly: dict) -> str:
    """Derive pressure trend from hourly pressure readings.

    Compares the most recent reading against the one ~3 hours prior.
    Returns ``"rising"``, ``"falling"``, or ``"steady"``.
    """
    pressures = hourly.get("surface_pressure", [])
    if not pressures or len(pressures) < 2:
        return "steady"

    # Filter out None values from the end
    valid = [(i, p) for i, p in enumerate(pressures) if p is not None]
    if len(valid) < 2:
        return "steady"

    current_p = valid[-1][1]
    # Look ~3 hours back (3 entries in hourly data)
    lookback_idx = max(0, len(valid) - 4)
    past_p = valid[lookback_idx][1]

    diff = current_p - past_p
    if diff > 1.0:
        return "rising"
    elif diff < -1.0:
        return "falling"
    return "steady"
