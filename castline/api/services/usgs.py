"""USGS Water Services client for real-time streamflow and water conditions."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from castline.api.config import settings

logger = logging.getLogger(__name__)

# USGS parameter codes
PARAM_DISCHARGE = "00060"   # Discharge (cfs)
PARAM_GAGE_HEIGHT = "00065" # Gage height (ft)
PARAM_WATER_TEMP = "00010"  # Water temperature (°C)

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"


async def get_nearest_usgs_site(
    lat: float,
    lon: float,
    radius_miles: float = 25,
    redis=None,
) -> Optional[dict]:
    """Find the nearest active USGS site with streamflow data.

    Searches within a bounding box around the given coordinates for USGS
    sites that report discharge, gage height, or water temperature.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        radius_miles: Search radius in miles (converted to a bounding box).
        redis: Optional aioredis.Redis instance for caching.

    Returns:
        Dict with keys ``site_id``, ``site_name``, ``lat``, ``lon``,
        ``distance_miles`` or None if nothing found.
    """
    cache_key = f"usgs:nearest:{round(lat, 2)}:{round(lon, 2)}"
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", cache_key)

    # Convert radius to rough bounding box (1 degree ≈ 69 miles)
    deg_offset = radius_miles / 69.0
    bbox = f"{lon - deg_offset:.4f},{lat - deg_offset:.4f},{lon + deg_offset:.4f},{lat + deg_offset:.4f}"

    params = {
        "format": "json",
        "bBox": bbox,
        "parameterCd": f"{PARAM_DISCHARGE},{PARAM_GAGE_HEIGHT},{PARAM_WATER_TEMP}",
        "siteStatus": "active",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(USGS_IV_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("USGS site search failed: %s", exc)
        return None

    time_series = data.get("value", {}).get("timeSeries", [])
    if not time_series:
        return None

    # Collect unique sites
    sites: dict[str, dict] = {}
    for ts in time_series:
        si = ts.get("sourceInfo", {})
        site_id = si.get("siteCode", [{}])[0].get("value")
        if not site_id or site_id in sites:
            continue
        geo = si.get("geoLocation", {}).get("geogLocation", {})
        site_lat = geo.get("latitude")
        site_lon = geo.get("longitude")
        if site_lat is None or site_lon is None:
            continue
        # Approximate distance in miles
        dist = ((site_lat - lat) ** 2 + (site_lon - lon) ** 2) ** 0.5 * 69.0
        sites[site_id] = {
            "site_id": site_id,
            "site_name": si.get("siteName", ""),
            "lat": site_lat,
            "lon": site_lon,
            "distance_miles": round(dist, 1),
        }

    if not sites:
        return None

    nearest = min(sites.values(), key=lambda s: s["distance_miles"])

    if redis:
        try:
            await redis.set(cache_key, json.dumps(nearest), ex=settings.cache_usgs_ttl)
        except Exception:
            logger.debug("Redis write failed for %s", cache_key)

    return nearest


async def get_usgs_conditions(
    site_id: str,
    redis=None,
) -> Optional[dict]:
    """Fetch current water conditions from USGS Instantaneous Values service.

    Retrieves the latest discharge, gage height, and water temperature for
    a given USGS site and computes simple trends by comparing the most
    recent reading against the value from approximately 6 hours prior.

    Args:
        site_id: USGS site number (e.g. ``"07381600"``).
        redis: Optional aioredis.Redis instance for caching.

    Returns:
        Dict with keys ``discharge_cfs``, ``gage_height_ft``,
        ``water_temp_f``, ``level_trend``, ``discharge_trend``,
        ``retrieved_at``, or None on failure.
    """
    cache_key = f"usgs:{site_id}"
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", cache_key)

    params = {
        "format": "json",
        "sites": site_id,
        "parameterCd": f"{PARAM_DISCHARGE},{PARAM_GAGE_HEIGHT},{PARAM_WATER_TEMP}",
        "period": "PT12H",  # last 12 hours for trend calc
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(USGS_IV_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("USGS conditions fetch failed for %s: %s", site_id, exc)
        return None

    time_series = data.get("value", {}).get("timeSeries", [])
    if not time_series:
        return None

    result: dict = {
        "site_id": site_id,
        "discharge_cfs": None,
        "gage_height_ft": None,
        "water_temp_f": None,
        "level_trend": None,
        "discharge_trend": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    for ts in time_series:
        var_code = ts.get("variable", {}).get("variableCode", [{}])[0].get("value")
        values_list = ts.get("values", [{}])[0].get("value", [])
        if not values_list:
            continue

        # Parse all values with timestamps
        parsed = []
        for v in values_list:
            try:
                val = float(v["value"])
                # Skip USGS no-data sentinel values
                if val < -999:
                    continue
                ts_str = v.get("dateTime", "")
                parsed.append((ts_str, val))
            except (ValueError, KeyError):
                continue

        if not parsed:
            continue

        latest_val = parsed[-1][1]
        trend = _compute_trend(parsed)

        if var_code == PARAM_DISCHARGE:
            result["discharge_cfs"] = round(latest_val, 1)
            result["discharge_trend"] = trend
        elif var_code == PARAM_GAGE_HEIGHT:
            result["gage_height_ft"] = round(latest_val, 2)
            result["level_trend"] = trend
        elif var_code == PARAM_WATER_TEMP:
            # Convert Celsius to Fahrenheit
            result["water_temp_f"] = round(latest_val * 9.0 / 5.0 + 32.0, 1)

    if redis:
        try:
            await redis.set(cache_key, json.dumps(result), ex=settings.cache_usgs_ttl)
        except Exception:
            logger.debug("Redis write failed for %s", cache_key)

    return result


def _compute_trend(parsed: list[tuple[str, float]], lookback_hours: int = 6) -> str:
    """Compare the latest value to the value ~lookback_hours ago.

    Returns ``"rising"``, ``"falling"``, or ``"stable"``.
    """
    if len(parsed) < 2:
        return "stable"

    latest_val = parsed[-1][1]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # Find the value closest to the cutoff time
    past_val = None
    for ts_str, val in parsed:
        try:
            # USGS timestamps like 2024-03-15T10:00:00.000-05:00
            ts_dt = datetime.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            else:
                ts_dt = ts_dt.astimezone(timezone.utc)
            if ts_dt <= cutoff:
                past_val = val
        except (ValueError, TypeError):
            continue

    if past_val is None:
        # Fall back to the oldest reading
        past_val = parsed[0][1]

    if past_val == 0:
        return "stable"

    pct_change = (latest_val - past_val) / abs(past_val) * 100.0

    if pct_change > 5.0:
        return "rising"
    elif pct_change < -5.0:
        return "falling"
    return "stable"
