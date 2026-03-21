"""Reverse geocoding service using Nominatim (OpenStreetMap)."""

import json
import logging
from typing import Optional

import httpx

from castline.api.config import settings

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


async def reverse_geocode(
    lat: float,
    lon: float,
    redis=None,
) -> Optional[str]:
    """Reverse-geocode coordinates to a human-readable location name.

    Uses the Nominatim (OpenStreetMap) reverse geocoding API. Returns a
    short location string like ``"Lake Fork, TX"`` or
    ``"Toledo Bend Reservoir, LA"``.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        redis: Optional aioredis.Redis instance for caching.

    Returns:
        Location name string, or None if geocoding fails.
    """
    lat_r = round(lat, 2)
    lon_r = round(lon, 2)
    cache_key = f"geocode:{lat_r}:{lon_r}"

    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return cached if isinstance(cached, str) else cached.decode("utf-8")
        except Exception:
            logger.debug("Redis read failed for %s", cache_key)

    params = {
        "lat": lat,
        "lon": lon,
        "format": "jsonv2",
        "zoom": 10,  # city/town level
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": "CASTLINE/1.0 (fishing conditions app)",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Nominatim reverse geocoding failed: %s", exc)
        return None

    location_name = _extract_location_name(data)

    if location_name and redis:
        try:
            await redis.set(cache_key, location_name, ex=settings.cache_geocode_ttl)
        except Exception:
            logger.debug("Redis write failed for %s", cache_key)

    return location_name


def _extract_location_name(data: dict) -> Optional[str]:
    """Build a concise location name from Nominatim response.

    Prefers water body names when available, falls back to
    ``"City, State"`` format.
    """
    if not data:
        return None

    address = data.get("address", {})

    # Check if the result is a named water body
    water_name = data.get("name") or data.get("display_name", "").split(",")[0]
    category = data.get("category", "")
    osm_type = data.get("type", "")

    # State abbreviation or name
    state = address.get("state", "")
    state_code = _state_abbreviation(state)

    # If it looks like a water feature, use its name directly
    if category == "natural" and osm_type in ("water", "lake", "reservoir"):
        if water_name and state_code:
            return f"{water_name}, {state_code}"
        if water_name:
            return water_name

    # Otherwise build "City, State" or "County, State"
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("county")
    )

    if city and state_code:
        return f"{city}, {state_code}"
    if city:
        return city

    # Last resort: use the display name truncated
    display = data.get("display_name", "")
    if display:
        parts = display.split(",")
        return ", ".join(p.strip() for p in parts[:2])

    return None


# Common US state name → abbreviation mapping
_US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
    # Canadian provinces
    "Ontario": "ON", "Quebec": "QC", "British Columbia": "BC",
    "Alberta": "AB", "Manitoba": "MB", "Saskatchewan": "SK",
}


def _state_abbreviation(state_name: str) -> str:
    """Convert a state name to its abbreviation, or return as-is."""
    if not state_name:
        return ""
    # Already an abbreviation
    if len(state_name) <= 3:
        return state_name
    return _US_STATES.get(state_name, state_name)
