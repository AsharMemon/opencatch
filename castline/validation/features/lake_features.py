"""Lake-specific feature engineering for CASTLINE.

Rivers and lakes require fundamentally different features:
- Rivers: discharge dynamics, upstream propagation, flow-based feeding
- Lakes: thermal stratification, wind-shoreline interaction, level management

This module computes lake-specific features that capture the dynamics
unique to standing water bodies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass
class LakeFeatures:
    """Computed lake features for a prediction."""
    # Turnover detection
    turnover_proximity: float  # 0-1, how close to turnover (1 = likely turning over)
    thermal_stability: float   # 0-1, how stratified the lake is

    # Wind-shoreline interaction
    wind_fetch_score: float    # 0-1, wind effectiveness based on direction + lake shape
    windblown_quality: float   # 0-1, predicted quality of windblown points

    # Water level dynamics
    level_trend: float         # ft/day rate of change
    level_anomaly: float       # ft deviation from seasonal norm

    # Barometric influence (stronger on lakes than rivers)
    pressure_fishing_score: float  # 0-1, pressure conditions for fishing

    # Moon/solunar (more important on lakes)
    lake_solunar_boost: float  # multiplier for solunar on lakes


def estimate_turnover_proximity(
    surface_temp_c: float,
    air_temp_c: float,
    season_day: int,  # day of year
    latitude: float,
    max_depth_ft: float,
) -> tuple[float, float]:
    """Estimate how close a lake is to turnover.

    Fall turnover occurs when surface temp drops to ~4°C (39°F).
    Spring turnover occurs when surface temp rises from ~4°C.

    Without deep water sensors, we estimate based on:
    - Surface temperature relative to 4°C
    - Rate of temperature change (from air temp as proxy)
    - Day of year and latitude (seasonal position)
    - Lake depth (deeper lakes stratify more strongly)

    Returns:
        (turnover_proximity, thermal_stability) both 0-1
    """
    if surface_temp_c != surface_temp_c:  # NaN check
        surface_temp_c = air_temp_c * 0.663 + 7.16 if air_temp_c == air_temp_c else 15.0

    # Distance from turnover temperature (4°C)
    turnover_temp = 4.0
    temp_diff = abs(surface_temp_c - turnover_temp)

    # Turnover proximity: high when temp is near 4°C
    # Use a Gaussian centered at 4°C with sigma based on depth
    sigma = max(2.0, max_depth_ft / 30.0)  # Deeper lakes have wider transition
    turnover_proximity = math.exp(-0.5 * (temp_diff / sigma) ** 2)

    # Season check: turnover happens in fall (day 260-340) and spring (day 60-120)
    in_fall = 240 <= season_day <= 350
    in_spring = 50 <= season_day <= 130

    # Adjust by latitude (northern lakes turn over later in fall, earlier in spring)
    lat_adjustment = (latitude - 35) / 15  # positive for northern lakes

    if not (in_fall or in_spring):
        turnover_proximity *= 0.1  # Very unlikely outside these windows

    # Thermal stability: inverse of turnover proximity
    # Also consider depth — deeper lakes are more stable in summer
    summer_stability = 1.0 - turnover_proximity
    depth_factor = min(1.0, max_depth_ft / 100.0)  # Deeper = more stable
    thermal_stability = summer_stability * (0.5 + 0.5 * depth_factor)

    return float(turnover_proximity), float(thermal_stability)


def compute_wind_fetch_score(
    wind_speed_kph: float,
    wind_dir_degrees: float,
    lake_area_acres: float,
    lake_shore_dev: float,
) -> tuple[float, float]:
    """Compute wind effectiveness on a lake.

    Fetch = unobstructed distance wind can travel across water surface.
    Larger lakes with simple shorelines have longer fetch.
    Wind must be above ~15 kph to create significant wave action.

    The key insight for fishing: windblown points concentrate baitfish
    and attract predators. But too much wind makes fishing difficult.

    Returns:
        (wind_fetch_score, windblown_quality) both 0-1
    """
    if wind_speed_kph != wind_speed_kph:  # NaN
        return 0.5, 0.5

    # Estimate lake diameter from area (assuming roughly circular)
    area_sqm = lake_area_acres * 4046.86  # acres to sq meters
    diameter_km = 2 * math.sqrt(area_sqm / math.pi) / 1000

    # Shore development factor: irregular shorelines break up fetch
    # shore_dev = 1.0 for perfect circle, higher = more irregular
    fetch_reduction = 1.0 / max(1.0, lake_shore_dev)

    # Effective fetch distance (km)
    effective_fetch = diameter_km * fetch_reduction

    # Wind speed effectiveness
    # Below 10 kph: minimal wave action
    # 15-30 kph: optimal for fishing (concentrates baitfish)
    # Above 40 kph: too rough, dangerous
    if wind_speed_kph < 8:
        wind_factor = wind_speed_kph / 8.0 * 0.3
    elif wind_speed_kph < 15:
        wind_factor = 0.3 + (wind_speed_kph - 8) / 7.0 * 0.4
    elif wind_speed_kph < 30:
        wind_factor = 0.7 + (wind_speed_kph - 15) / 15.0 * 0.3
    elif wind_speed_kph < 45:
        wind_factor = 1.0 - (wind_speed_kph - 30) / 15.0 * 0.5
    else:
        wind_factor = 0.2  # Too windy, fishing is difficult

    # Fetch score combines lake size, shape, and wind
    fetch_score = min(1.0, effective_fetch / 5.0) * wind_factor

    # Windblown quality: optimal when wind is moderate and fetch is meaningful
    # Anglers target "windblown points" — shoreline segments where wind pushes water
    optimal_wind = 20.0  # kph
    wind_quality = math.exp(-0.5 * ((wind_speed_kph - optimal_wind) / 10.0) ** 2)
    windblown_quality = wind_quality * min(1.0, effective_fetch / 3.0)

    return float(fetch_score), float(windblown_quality)


def compute_pressure_fishing_score(
    pressure_mb: float,
    pressure_delta_6h: float,
) -> float:
    """Score barometric pressure conditions for lake fishing.

    Pressure is more important on lakes than rivers because:
    - Flow-driven feeding cues are absent
    - Bass rely more on pressure-related comfort/activity levels
    - Classic "pre-frontal bite" is well-documented on lakes

    General consensus:
    - Falling pressure (pre-frontal): EXCELLENT — fish feed aggressively
    - Stable high pressure: GOOD — consistent feeding
    - Stable low pressure: FAIR — fish are sluggish
    - Rising pressure (post-frontal): POOR — fish lockjaw for 12-24h
    """
    if pressure_mb != pressure_mb or pressure_delta_6h != pressure_delta_6h:
        return 0.5

    score = 0.5  # baseline

    # Pressure trend is more important than absolute value
    if pressure_delta_6h < -2.0:
        # Rapidly falling — strong pre-frontal bite
        score = 0.95
    elif pressure_delta_6h < -0.5:
        # Slowly falling — good conditions
        score = 0.80
    elif -0.5 <= pressure_delta_6h <= 0.5:
        # Stable
        if pressure_mb > 1020:
            score = 0.70  # Stable high = good
        elif pressure_mb > 1010:
            score = 0.55  # Stable normal = average
        else:
            score = 0.40  # Stable low = sluggish
    elif pressure_delta_6h > 2.0:
        # Rapidly rising — post-frontal lockjaw
        score = 0.15
    elif pressure_delta_6h > 0.5:
        # Slowly rising — recovering
        score = 0.35

    return float(score)


def compute_lake_solunar_boost(
    solunar_score: float,
    area_acres: float,
    is_river: bool = False,
) -> float:
    """Compute solunar influence multiplier for lakes.

    Solunar theory (moon-based feeding windows) is more influential
    on lakes than rivers because:
    - Rivers have flow-driven feeding triggers that override solunar
    - Lakes lack current-based cues, so biological rhythms dominate
    - Many experienced lake anglers report strong solunar correlation
    """
    if is_river or area_acres == 0:
        return 1.0  # No boost for rivers

    if solunar_score != solunar_score:  # NaN
        return 1.0

    # Larger lakes show stronger solunar effects (more natural behavior)
    size_factor = min(1.5, 1.0 + math.log10(max(100, area_acres)) / 10)

    # Solunar boost: amplify the solunar score for lakes
    boost = 1.0 + (solunar_score - 0.5) * 0.3 * size_factor

    return float(max(0.5, min(1.5, boost)))


def compute_all_lake_features(
    surface_temp_c: float = float("nan"),
    air_temp_c: float = float("nan"),
    season_day: int = 180,
    latitude: float = 35.0,
    max_depth_ft: float = 30.0,
    wind_speed_kph: float = float("nan"),
    wind_dir_degrees: float = float("nan"),
    lake_area_acres: float = 0.0,
    lake_shore_dev: float = 1.0,
    pressure_mb: float = float("nan"),
    pressure_delta_6h: float = float("nan"),
    solunar_score: float = float("nan"),
    gage_height_ft: float = float("nan"),
    gage_height_7d_mean: float = float("nan"),
) -> dict[str, float]:
    """Compute all lake-specific features.

    Returns a dict of feature_name -> value that can be merged
    with the main feature set.
    """
    is_river = lake_area_acres == 0

    turnover_prox, thermal_stab = estimate_turnover_proximity(
        surface_temp_c, air_temp_c, season_day, latitude, max_depth_ft,
    )

    fetch_score, windblown_qual = compute_wind_fetch_score(
        wind_speed_kph, wind_dir_degrees, lake_area_acres, lake_shore_dev,
    )

    pressure_score = compute_pressure_fishing_score(pressure_mb, pressure_delta_6h)

    solunar_boost = compute_lake_solunar_boost(
        solunar_score, lake_area_acres, is_river,
    )

    # Level trend
    if gage_height_ft == gage_height_ft and gage_height_7d_mean == gage_height_7d_mean:
        level_trend = (gage_height_ft - gage_height_7d_mean) / 7.0
        level_anomaly = gage_height_ft - gage_height_7d_mean
    else:
        level_trend = 0.0
        level_anomaly = 0.0

    return {
        "turnover_proximity": turnover_prox,
        "thermal_stability": thermal_stab,
        "wind_fetch_score": fetch_score,
        "windblown_quality": windblown_qual,
        "pressure_fishing_score": pressure_score,
        "lake_solunar_boost": solunar_boost,
        "level_trend_ft_per_day": level_trend,
        "level_anomaly_ft": level_anomaly,
        "is_lake": 0.0 if is_river else 1.0,
    }
