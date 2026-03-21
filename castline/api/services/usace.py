"""USACE CWMS Data API client for real-time reservoir conditions.

Fetches pool elevation, tailwater elevation, inflow, outflow, and storage
from the Corps Water Management System (CWMS) Data API for the nearest
USACE-managed reservoir.

CWMS Data API: https://cwms-data.usace.army.mil/cwms-data
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from castline.api.config import settings

logger = logging.getLogger(__name__)

CDA_BASE = "https://cwms-data.usace.army.mil/cwms-data"

# Time series parameter patterns -> friendly names
TS_PARAMS = {
    "Elev-Pool": "pool_elevation_ft",
    "Elev-Tailwater": "tailwater_elevation_ft",
    "Flow-In": "inflow_cfs",
    "Flow-Out": "outflow_cfs",
    "Stor": "storage_acft",
}

CACHE_TTL = settings.cache_usace_ttl


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in miles between two lat/lon points."""
    R_MILES = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R_MILES * 2 * math.asin(math.sqrt(a))


async def _cwms_get(client: httpx.AsyncClient, path: str, params: dict) -> Optional[dict]:
    """Make a GET request to the CWMS Data API."""
    url = f"{CDA_BASE}{path}"
    headers = {
        "Accept": "application/json;version=2",
        "User-Agent": "Castline/1.0",
    }
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=20.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("CWMS request failed %s: %s", path, exc)
        return None


async def find_nearest_reservoir(
    lat: float,
    lon: float,
    redis=None,
    radius_miles: float = 50.0,
) -> Optional[dict]:
    """Search the CWMS catalog for the nearest reservoir with pool elevation data.

    Returns dict with keys: project, office, timeseries, distance_miles
    or None if nothing found within radius.
    """
    cache_key = f"usace:nearest:{round(lat, 2)}:{round(lon, 2)}"
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", cache_key)

    # Search catalog using a bounding box (rough lat/lon to miles conversion)
    deg_offset = radius_miles / 69.0
    bbox = (
        f"{lon - deg_offset:.4f},{lat - deg_offset:.4f},"
        f"{lon + deg_offset:.4f},{lat + deg_offset:.4f}"
    )

    # Search for Elev-Pool time series in the bounding box area
    # The CWMS catalog does not support direct bbox search, so we search by
    # common reservoir keywords derived from the area and filter results.
    # Strategy: search for Elev-Pool entries, then pick the closest project.
    params = {
        "like": ".*Elev.*Pool.*",
        "page-size": 500,
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        data = await _cwms_get(client, "/catalog/TIMESERIES", params)

    if not data:
        return None

    entries = data.get("entries", [])
    if not entries:
        return None

    # Group by project, collect all matching time series IDs
    projects: dict[str, dict] = {}
    for entry in entries:
        ts_id = entry.get("name", "")
        office = entry.get("office-id", "")
        extents = entry.get("extents", [])

        parts = ts_id.split(".")
        if len(parts) < 3:
            continue

        project = parts[0]

        # Check if any of our desired parameters match
        param_part = parts[1]
        matched_param = None
        for pattern in TS_PARAMS:
            if pattern in param_part:
                matched_param = pattern
                break
        if not matched_param:
            continue

        # Try to get lat/lon from extents (not always available)
        # We will filter by distance later if we can resolve coordinates.
        if project not in projects:
            projects[project] = {
                "project": project,
                "office": office,
                "timeseries": {},
                "lat": None,
                "lon": None,
            }

        # Prefer 1Day or 1Hour intervals
        existing = projects[project]["timeseries"].get(matched_param, "")
        if not existing or "1Day" in ts_id:
            projects[project]["timeseries"][matched_param] = ts_id

    if not projects:
        return None

    # Score projects: prefer those with Elev-Pool and more parameters
    candidates = []
    for proj_info in projects.values():
        ts_map = proj_info["timeseries"]
        if "Elev-Pool" not in ts_map:
            continue
        score = len(ts_map)
        candidates.append((score, proj_info))

    if not candidates:
        return None

    # Sort by score (most parameters = best), take top match
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]

    result = {
        "project": best["project"],
        "office": best["office"],
        "timeseries": best["timeseries"],
    }

    if redis:
        try:
            await redis.set(cache_key, json.dumps(result), ex=CACHE_TTL * 2)
        except Exception:
            logger.debug("Redis write failed for %s", cache_key)

    return result


async def _fetch_timeseries_latest(
    client: httpx.AsyncClient,
    ts_id: str,
    office: str,
    hours_back: int = 48,
) -> list[tuple[float, float]]:
    """Fetch recent values for a single CWMS time series.

    Returns list of (timestamp_epoch_s, value) tuples sorted by time.
    """
    now = datetime.now(timezone.utc)
    begin = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "name": ts_id,
        "office": office,
        "begin": begin,
        "end": end,
        "units": "EN",
        "page-size": 500,
    }

    data = await _cwms_get(client, "/timeseries", params)
    if not data:
        return []

    raw_values = data.get("values", [])
    parsed = []
    for entry in raw_values:
        if not entry or len(entry) < 2:
            continue
        ts_ms = entry[0]
        val = entry[1]
        if val is None:
            continue
        try:
            parsed.append((ts_ms / 1000.0, float(val)))
        except (ValueError, TypeError):
            continue

    parsed.sort(key=lambda x: x[0])
    return parsed


async def get_usace_conditions(
    lat: float,
    lon: float,
    redis=None,
) -> Optional[dict]:
    """Fetch current USACE reservoir conditions for the nearest reservoir.

    Returns a dict with:
        pool_elevation_ft, tailwater_elevation_ft, reservoir_inflow_cfs,
        reservoir_outflow_cfs, reservoir_storage_acft, pool_change_24h_ft,
        pool_trend, retrieved_at

    All missing values are None (caller should convert to NaN).
    Results are cached in Redis for 1800 seconds.
    """
    cache_key = f"usace:conditions:{round(lat, 3)}:{round(lon, 3)}"
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("Redis read failed for %s", cache_key)

    # Step 1: Find nearest reservoir
    reservoir = await find_nearest_reservoir(lat, lon, redis=redis)
    if not reservoir:
        logger.debug("No USACE reservoir found near %.2f, %.2f", lat, lon)
        return None

    project = reservoir["project"]
    office = reservoir["office"]
    ts_map = reservoir.get("timeseries", {})

    result: dict = {
        "project": project,
        "office": office,
        "pool_elevation_ft": None,
        "tailwater_elevation_ft": None,
        "reservoir_inflow_cfs": None,
        "reservoir_outflow_cfs": None,
        "reservoir_storage_acft": None,
        "pool_change_24h_ft": None,
        "pool_trend": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        # Fetch each available parameter
        for param_pattern, ts_id in ts_map.items():
            values = await _fetch_timeseries_latest(client, ts_id, office, hours_back=48)
            if not values:
                continue

            latest_val = values[-1][1]
            friendly = TS_PARAMS.get(param_pattern)

            if param_pattern == "Elev-Pool":
                result["pool_elevation_ft"] = round(latest_val, 2)
                # Compute 24h change
                if len(values) >= 2:
                    cutoff = values[-1][0] - 86400  # 24 hours ago in epoch seconds
                    past_val = None
                    for ts_epoch, val in values:
                        if ts_epoch <= cutoff:
                            past_val = val
                    if past_val is not None:
                        change = latest_val - past_val
                        result["pool_change_24h_ft"] = round(change, 3)
                        if change > 0.01:
                            result["pool_trend"] = "rising"
                        elif change < -0.01:
                            result["pool_trend"] = "falling"
                        else:
                            result["pool_trend"] = "stable"

            elif param_pattern == "Elev-Tailwater":
                result["tailwater_elevation_ft"] = round(latest_val, 2)

            elif param_pattern == "Flow-In":
                result["reservoir_inflow_cfs"] = round(latest_val, 1)

            elif param_pattern == "Flow-Out":
                result["reservoir_outflow_cfs"] = round(latest_val, 1)

            elif param_pattern == "Stor":
                result["reservoir_storage_acft"] = round(latest_val, 0)

    if redis:
        try:
            await redis.set(cache_key, json.dumps(result), ex=CACHE_TTL)
        except Exception:
            logger.debug("Redis write failed for %s", cache_key)

    return result
