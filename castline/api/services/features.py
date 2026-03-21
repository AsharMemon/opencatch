"""Real-time feature collection service for the prediction pipeline.

Bridges the async API-layer data services (weather, USGS, geocoding)
with the model's feature requirements.  Produces a flat dict of numeric
features ready for V14Predictor.predict(precomputed_features=...).
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from castline.api.services.geocoding import reverse_geocode
from castline.api.services.usace import get_usace_conditions
from castline.api.services.usgs import get_nearest_usgs_site, get_usgs_conditions
from castline.api.services.weather import get_current_weather

logger = logging.getLogger(__name__)


def _safe_float(v, default=float("nan")) -> float:
    """Coerce a value to float, returning NaN on failure."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_nan(v) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return False


# ── Solunar (pure-Python, no external deps) ─────────────────────────

def _moon_phase(dt: datetime) -> float:
    """Return lunar phase as 0-1 (0 = new moon)."""
    SYNODIC = 29.53058868
    REF_NEW = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    age = (dt - REF_NEW).total_seconds() / 86400.0
    return (age % SYNODIC) / SYNODIC


def _solunar_score(phase: float) -> float:
    """Score 0-1 where new/full moon = 1.0 (best fishing)."""
    return (math.cos(4 * math.pi * phase) + 1) / 2


def _photoperiod_hours(dt: datetime, lat: float) -> float:
    """Approximate day length in hours for a date and latitude."""
    doy = dt.timetuple().tm_yday
    lat_rad = math.radians(lat)
    # Solar declination (approximate)
    decl = 23.45 * math.sin(math.radians(360 / 365 * (doy - 81)))
    decl_rad = math.radians(decl)

    cos_ha = -math.tan(lat_rad) * math.tan(decl_rad)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))
    return ha * 2 / 15.0


def compute_temporal_features(date_str: str, lat: float = 35.0) -> dict:
    """Compute calendar/temporal/solunar features from date string."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    doy = dt.timetuple().tm_yday

    phase = _moon_phase(dt)
    sol_score = _solunar_score(phase)
    photoperiod = _photoperiod_hours(dt, lat)

    # Compute photoperiod change rate (day-over-day delta in minutes)
    yesterday = dt - __import__('datetime').timedelta(days=1)
    photoperiod_yesterday = _photoperiod_hours(yesterday, lat)
    photoperiod_change_hrs = photoperiod - photoperiod_yesterday
    photoperiod_change_min = photoperiod_change_hrs * 60.0  # convert to minutes

    return {
        "year": dt.year,
        "month": dt.month,
        "day_of_year": doy,
        "day_number": doy,
        "season_sin": math.sin(2 * math.pi * doy / 365.25),
        "season_cos": math.cos(2 * math.pi * doy / 365.25),
        "moon_phase": phase,
        "moon_phase_cos": math.cos(2 * math.pi * phase),
        "moon_phase_score": sol_score,
        "solunar_period_quality": sol_score,
        "solunar_major": sol_score,
        "solunar_minor": sol_score * 0.6,
        "moon_night_feeding_adj": 0.5 + 0.5 * sol_score,
        "photoperiod_hrs": photoperiod,
        "photoperiod_change_min": photoperiod_change_min,
        "photoperiod_change_rate": photoperiod_change_hrs,
        "photoperiod_spawn_proximity": max(0, 1.0 - abs(photoperiod - 14.0) / 4.0),
    }


def _weather_to_model_features(weather: dict) -> dict:
    """Map async weather service output to model feature names."""
    if not weather:
        return {}

    features = {}
    # Pressure lag features
    pressure_mb = _safe_float(weather.get("pressure_mb"))
    if not _is_nan(pressure_mb):
        features["lag_pressure_3d_mean"] = pressure_mb
        features["lag_pressure_std"] = 0.0  # single-point, no variance
        features["lag_pressure_range"] = 0.0
        trend = weather.get("pressure_trend", "steady")
        features["lag_pressure_trend"] = (
            1.0 if trend == "rising" else (-1.0 if trend == "falling" else 0.0)
        )

    # Temperature
    temp_f = _safe_float(weather.get("temp_f"))
    if not _is_nan(temp_f):
        temp_c = (temp_f - 32) * 5.0 / 9.0
        features["lag_temp_7d_mean"] = temp_c
        features["lag_temp_3d_mean"] = temp_c
        features["lag_temp_trend"] = 0.0
        features["lag_temp_range"] = 0.0
        features["np_temp_range_c"] = 0.0

    # Wind
    wind_mph = _safe_float(weather.get("wind_mph"))
    if not _is_nan(wind_mph):
        wind_ms = wind_mph * 0.44704
        features["np_wind_10m_ms"] = wind_ms
        features["np_wind_2m_ms"] = wind_ms * 0.75

    # Wind direction
    wind_dir = weather.get("wind_direction")
    if wind_dir and isinstance(wind_dir, str):
        compass = {
            "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
            "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
            "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
            "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
        }
        deg = compass.get(wind_dir, 0)
        features["np_wind_dir_sin"] = math.sin(math.radians(deg))
        features["np_wind_dir_cos"] = math.cos(math.radians(deg))

    # Humidity
    humidity = _safe_float(weather.get("humidity_pct"))
    if not _is_nan(humidity):
        features["np_humidity_pct"] = humidity
        features["np_humid_comfort"] = max(0, 1.0 - abs(humidity - 60) / 40.0)
        features["lag_humidity_trend"] = 0.0

    # Cloud cover
    cloud = _safe_float(weather.get("cloud_cover_pct"))
    if not _is_nan(cloud):
        features["np_cloud_pct"] = cloud
        features["np_cloud_fishing"] = min(1.0, cloud / 80.0)
        features["lag_cloud_3d_mean"] = cloud
        features["lag_cloud_trend"] = 0.0

    # Solar radiation estimate from cloud cover
    if not _is_nan(cloud):
        features["np_solar_mj_m2"] = max(0, 25.0 * (1.0 - cloud / 100.0))

    # Precipitation
    precip_in = _safe_float(weather.get("precip_in"))
    if not _is_nan(precip_in):
        precip_mm = precip_in * 25.4
        features["lag_precip_3d_sum"] = precip_mm
        features["lag_precip_7d_sum"] = precip_mm
        features["lag_diurnal_mean"] = 0.0
        features["lag_diurnal_std"] = 0.0
        # V15: precipitation fishing effect (light rain good, heavy bad)
        features["precip_fishing_effect"] = (
            0.5 if precip_mm < 2 else (0.8 if precip_mm < 10 else max(0, 1.0 - precip_mm / 50))
        )

    # V15: temp_delta_1d (estimate from current conditions — full computation
    # requires yesterday's data, so approximate from diurnal range)
    temp_c = _safe_float(weather.get("air_temp_c", weather.get("temp_f")))
    if not _is_nan(temp_c):
        if "temp_f" in str(weather.get("temp_f", "")):
            temp_c = (temp_c - 32) * 5.0 / 9.0
        features["temp_delta_1d"] = 0.0  # neutral default; real pipeline computes from lag

    return features


def _usace_to_model_features(usace: dict) -> dict:
    """Map async USACE reservoir service output to model feature names."""
    if not usace:
        return {}

    features = {}

    pool = _safe_float(usace.get("pool_elevation_ft"))
    if not _is_nan(pool):
        features["pool_elevation"] = pool

    tailwater = _safe_float(usace.get("tailwater_elevation_ft"))
    if not _is_nan(tailwater):
        features["tailwater_elevation"] = tailwater

    inflow = _safe_float(usace.get("reservoir_inflow_cfs"))
    if not _is_nan(inflow):
        features["reservoir_inflow"] = inflow

    outflow = _safe_float(usace.get("reservoir_outflow_cfs"))
    if not _is_nan(outflow):
        features["reservoir_outflow"] = outflow

    storage = _safe_float(usace.get("reservoir_storage_acft"))
    if not _is_nan(storage):
        features["reservoir_storage"] = storage

    pool_change = _safe_float(usace.get("pool_change_24h_ft"))
    if not _is_nan(pool_change):
        features["pool_change_24h"] = pool_change

    # Derived: inflow/outflow ratio (water balance indicator)
    if not _is_nan(inflow) and not _is_nan(outflow) and outflow > 0:
        features["reservoir_inflow_outflow_ratio"] = inflow / outflow

    # Store trend for breakdown builder
    pool_trend = usace.get("pool_trend")
    if pool_trend:
        features["_live_pool_trend"] = pool_trend

    return features


def _usgs_to_model_features(usgs: dict) -> dict:
    """Map async USGS service output to model feature names."""
    if not usgs:
        return {}

    features = {}
    # These are condition-level signals used for the breakdown description
    # but not direct V13 model features (V13 uses pre-computed lag features).
    # We store them for the breakdown builder.
    discharge = _safe_float(usgs.get("discharge_cfs"))
    if not _is_nan(discharge):
        features["_live_discharge_cfs"] = discharge

    gage = _safe_float(usgs.get("gage_height_ft"))
    if not _is_nan(gage):
        features["_live_gage_height_ft"] = gage

    water_temp_f = _safe_float(usgs.get("water_temp_f"))
    if not _is_nan(water_temp_f):
        features["_live_water_temp_f"] = water_temp_f
        features["_live_water_temp_c"] = (water_temp_f - 32) * 5.0 / 9.0

    level_trend = usgs.get("level_trend")
    if level_trend:
        features["_live_level_trend"] = level_trend

    discharge_trend = usgs.get("discharge_trend")
    if discharge_trend:
        features["_live_discharge_trend"] = discharge_trend

    return features


async def collect_realtime_features(
    location: str,
    date: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    usgs_site_id: Optional[str] = None,
    redis=None,
) -> dict:
    """Collect all real-time features for a prediction request.

    Combines:
      - Calendar/temporal/solunar features (computed locally)
      - Weather features from Open-Meteo (async)
      - USGS water conditions (async)

    Returns a flat dict of features.  Missing data is NaN, never 0.0.
    """
    features: dict = {}

    # --- Resolve coordinates from location name if not provided ---
    # TODO: Implement forward geocoding (location name -> lat/lon).
    # For now, use defaults for known tournament lakes or fallback.
    if lat is None or lon is None:
        lat, lon = _resolve_coordinates(location)

    features["lat"] = lat
    features["lon"] = lon

    # --- Temporal/solunar features ---
    temporal = compute_temporal_features(date, lat=lat)
    features.update(temporal)

    # --- Biological/spawn features ---
    bio = _compute_biology_features(date, lat)
    features.update(bio)

    # --- Weather (async) ---
    try:
        weather = await get_current_weather(lat, lon, redis=redis)
        if weather:
            features.update(_weather_to_model_features(weather))
            features["_raw_weather"] = weather  # for breakdown builder
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)

    # --- USGS (async) ---
    if usgs_site_id:
        try:
            usgs = await get_usgs_conditions(usgs_site_id, redis=redis)
            if usgs:
                features.update(_usgs_to_model_features(usgs))
                features["_raw_usgs"] = usgs
        except Exception as exc:
            logger.warning("USGS fetch failed for %s: %s", usgs_site_id, exc)
    elif lat and lon:
        # Try to find nearest USGS site
        try:
            site = await get_nearest_usgs_site(lat, lon, redis=redis)
            if site:
                usgs_site_id = site["site_id"]
                features["_resolved_usgs_site"] = usgs_site_id
                usgs = await get_usgs_conditions(usgs_site_id, redis=redis)
                if usgs:
                    features.update(_usgs_to_model_features(usgs))
                    features["_raw_usgs"] = usgs
        except Exception as exc:
            logger.warning("USGS auto-discovery failed: %s", exc)

    # --- USACE reservoir conditions (async) ---
    if lat and lon:
        try:
            usace = await get_usace_conditions(lat, lon, redis=redis)
            if usace:
                features.update(_usace_to_model_features(usace))
                features["_raw_usace"] = usace
        except Exception as exc:
            logger.warning("USACE fetch failed: %s", exc)

    return features


def _compute_biology_features(date_str: str, lat: float) -> dict:
    """Compute spawn/biology features from date and latitude."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    doy = dt.timetuple().tm_yday
    month = dt.month

    # Growing degree days (rough cumulative estimate)
    # Assume avg temp ~ 10 + 15 * sin(2pi*(doy-80)/365)
    avg_temp_c = 10 + 15 * math.sin(2 * math.pi * (doy - 80) / 365)
    gdd = max(0, avg_temp_c - 10) * doy / 365 * 30
    gdd_calibrated = gdd * (1.0 + (lat - 35) * -0.02)

    # Spawn timing varies by latitude
    spawn_peak_doy = 100 + (lat - 25) * 2  # roughly April-May
    days_from_spawn = doy - spawn_peak_doy

    prespawn = max(0, 1.0 - abs(days_from_spawn + 20) / 30.0)
    spawn_prob = max(0, 1.0 - abs(days_from_spawn) / 20.0)
    bedding = max(0, 1.0 - abs(days_from_spawn - 5) / 15.0)
    post_spawn = max(0, 1.0 - abs(days_from_spawn - 30) / 25.0)
    winter_dormancy = max(0, 1.0 - avg_temp_c / 10.0) if avg_temp_c < 10 else 0.0

    # Feeding optimality based on GDD
    feeding_opt = min(1.0, gdd / 500.0) if gdd > 0 else 0.0

    # Metabolic rate index (Arrhenius-like)
    metabolic = min(1.0, max(0, (avg_temp_c - 5) / 25.0))

    # Catch potential index
    catch_potential = (
        0.3 * feeding_opt + 0.3 * (1 - spawn_prob) + 0.2 * metabolic + 0.2 * prespawn
    )

    return {
        "gdd_cumulative": gdd,
        "gdd_calibrated": gdd_calibrated,
        "gdd_log": math.log1p(gdd),
        "gdd_feeding_optimality": feeding_opt,
        "prespawn_intensity": prespawn,
        "spawn_probability": spawn_prob,
        "spawn_bedding_prob": bedding,
        "post_spawn_lethargy": post_spawn,
        "winter_dormancy": winter_dormancy,
        "metabolic_rate_index": metabolic,
        "catch_potential_index": catch_potential,
        "seasonal_pattern_phase": math.sin(2 * math.pi * (doy - spawn_peak_doy) / 365),
        "latitude_growth_potential": max(0, 1.0 - abs(lat - 33) / 15.0),
        "northern_trophy_potential": max(0, (lat - 40) / 10.0) if lat > 40 else 0.0,
        "days_since_start": max(0, doy - 60),
        "days_from_end": max(0, 300 - doy),
        "is_spawn_window": 1 if abs(days_from_spawn) < 20 else 0,
        "is_premium": 0,
    }


# ── Known location coordinates ─────────────────────────────────────

_KNOWN_LOCATIONS: dict[str, tuple[float, float]] = {
    # Major US bass tournament lakes
    "lake guntersville": (34.38, -86.29),
    "lake fork": (32.87, -95.56),
    "toledo bend reservoir": (31.17, -93.56),
    "sam rayburn reservoir": (31.07, -94.10),
    "lake okeechobee": (26.95, -80.80),
    "table rock lake": (36.60, -93.30),
    "grand lake": (36.25, -94.75),
    "lake champlain": (44.53, -73.33),
    "lake erie": (42.20, -81.20),
    "lake st. clair": (42.43, -82.67),
    "kentucky lake": (36.62, -88.07),
    "wheeler lake": (34.59, -87.08),
    "chickamauga lake": (35.23, -85.12),
    "ross barnett reservoir": (32.43, -89.98),
    "clarks hill lake": (33.66, -82.20),
    "santee cooper": (33.52, -80.15),
    "lake havasu": (34.47, -114.35),
    "kissimmee chain": (28.12, -81.37),
    "delta": (38.05, -121.80),
    "lay lake": (33.07, -86.55),
    "tenkiller lake": (35.65, -95.05),
    "norris lake": (36.30, -84.10),
    "dale hollow lake": (36.53, -85.45),
    "raystown lake": (40.42, -78.10),
    "pickwick lake": (34.90, -88.25),
}


def _resolve_coordinates(location: str) -> tuple[float, float]:
    """Resolve a location name to (lat, lon).

    Checks a built-in lookup of known fishing locations.
    Falls back to a US-central default.
    """
    loc_lower = location.lower().strip()

    # Try exact and partial matches
    for key, coords in _KNOWN_LOCATIONS.items():
        if key in loc_lower or loc_lower in key:
            return coords

    # Strip state suffix and retry
    parts = loc_lower.split(",")
    if len(parts) >= 2:
        name_part = parts[0].strip()
        for key, coords in _KNOWN_LOCATIONS.items():
            if key in name_part or name_part in key:
                return coords

    # TODO: Use async forward geocoding service
    # Default to central US
    logger.warning("Could not resolve coordinates for '%s', using default", location)
    return (35.0, -85.0)
