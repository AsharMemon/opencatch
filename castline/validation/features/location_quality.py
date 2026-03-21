"""Location-level quality estimation from physical/environmental characteristics.

This is the KEY module for improving leave-one-location-out (LOO) R².

The core problem: when we hold out a location entirely, the model has no
tournament history for it, so `location_mean_weight` (which explains ~66%
of variance) is unknown.  We need to *predict* that location quality from
observable features that transfer across waterbodies.

Feature groups:
1. Morphometric productivity  -- area, depth, shoreline complexity
2. Geographic/climate proxy   -- latitude -> growing season -> bass size
3. Nearest-neighbor creel     -- IDW of creel surveys within 100 km
4. Waterbody type encoding    -- reservoir vs natural lake vs river
5. Ecoregion clustering       -- lat/lon cluster with mean catch rates
6. Forage base proxy          -- shad habitat suitability score
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_KNOWLEDGE = _HERE.parent / "knowledge"
_RAW = _HERE.parent / "data" / "raw"

_MORPHOMETRY_PATH = _KNOWLEDGE / "lake_morphometry.json"
_CREEL_PATH = _RAW / "creel_gnn_locations.csv"

# ---------------------------------------------------------------------------
# Cached data (loaded once on first call)
# ---------------------------------------------------------------------------
_morphometry_cache: dict[str, dict[str, float]] | None = None
_creel_cache: pd.DataFrame | None = None


def _load_morphometry() -> dict[str, dict[str, float]]:
    global _morphometry_cache
    if _morphometry_cache is None:
        if _MORPHOMETRY_PATH.exists():
            with open(_MORPHOMETRY_PATH) as f:
                _morphometry_cache = json.load(f)
        else:
            _morphometry_cache = {}
    return _morphometry_cache


def _load_creel() -> pd.DataFrame:
    global _creel_cache
    if _creel_cache is None:
        if _CREEL_PATH.exists():
            _creel_cache = pd.read_csv(_CREEL_PATH)
        else:
            _creel_cache = pd.DataFrame(
                columns=[
                    "waterbody_name", "latitude", "longitude",
                    "state", "synthetic_weight_lb", "cpue_fish_per_hour",
                ]
            )
    return _creel_cache


# ---------------------------------------------------------------------------
# 1.  Morphometric productivity score
# ---------------------------------------------------------------------------

def _morphometric_productivity(
    area_acres: float,
    max_depth_ft: float,
    shore_dev: float,
) -> float:
    """Score 0-1 estimating productivity from lake shape.

    Productive bass lakes tend to be:
    - Large enough for forage diversity (> 5000 acres is good)
    - Relatively shallow (high littoral-zone fraction)
    - Complex shoreline (more bays/points = more structure)

    We combine three sub-scores via geometric mean so that one terrible
    dimension drags the score down (a 500-ft-deep tiny lake is still bad).
    """
    if _isnan(area_acres) or _isnan(max_depth_ft):
        return float("nan")

    # -- Area score: log-scaled, saturates around 100k acres --
    if area_acres <= 0:
        area_score = 0.2  # rivers get a low but nonzero baseline
    else:
        # log10(500)=2.7, log10(50000)=4.7, log10(500000)=5.7
        area_score = _clamp((math.log10(max(1, area_acres)) - 2.5) / 2.5, 0.05, 1.0)

    # -- Depth score: shallower is more productive (more littoral zone) --
    # Max-depth proxy: mean_depth ~ max_depth / 3 (typical for reservoirs).
    # Littoral fraction ~ fraction of area < 15 ft deep.
    # Shallower lakes have higher littoral fraction.
    # Optimal mean depth for bass: ~10-25 ft  (max ~30-75 ft).
    if max_depth_ft <= 0:
        depth_score = 0.5
    else:
        # Peak around 40 ft max depth, declining toward 0 and 400+
        depth_score = math.exp(-0.5 * ((math.log(max_depth_ft) - math.log(40)) / 0.8) ** 2)

    # -- Shore development score: more structure = better --
    sd = shore_dev if not _isnan(shore_dev) else 1.0
    # sd=1 is a perfect circle (boring), sd=5+ is very complex
    shore_score = _clamp((sd - 1.0) / 6.0, 0.05, 1.0)

    # Geometric mean of three sub-scores
    product = area_score * depth_score * shore_score
    return float(product ** (1.0 / 3.0))


# ---------------------------------------------------------------------------
# 2.  Geographic / climate quality proxy
# ---------------------------------------------------------------------------

def _latitude_growth_potential(lat: float) -> float:
    """Growing-season proxy from latitude.

    Bass growth rate is strongly correlated with growing degree-days (GDD).
    Southern US (lat ~28-32) has 250+ GDD; Northern US (lat ~44-48) has 100-130.

    Returns a 0-1 score where 1 = maximum growth potential.
    """
    if _isnan(lat):
        return float("nan")
    # Peak growth potential at lat ~29 (Florida), declining northward.
    # Slight decline south of 26 (tropical, not ideal for largemouth).
    optimal_lat = 29.0
    sigma = 10.0  # degrees of latitude
    score = math.exp(-0.5 * ((lat - optimal_lat) / sigma) ** 2)
    return float(score)


def _growing_degree_proxy(lat: float) -> float:
    """Estimate annual growing degree-days (base 50F) from latitude.

    Empirical approximation for the continental US:
        GDD_50 ~ 5500 - 90 * lat  (very rough, R2~0.7 against PRISM data)

    Returns GDD estimate (not normalized), useful as a raw feature.
    """
    if _isnan(lat):
        return float("nan")
    gdd = max(0.0, 5500.0 - 90.0 * lat)
    return float(gdd)


# ---------------------------------------------------------------------------
# 3.  Nearest-neighbor location quality from creel surveys
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _nearest_neighbor_creel(
    lat: float,
    lon: float,
    creel_df: pd.DataFrame,
    radius_km: float = 200.0,
) -> dict[str, float]:
    """Inverse-distance-weighted creel-survey statistics within *radius_km*.

    This is the most important feature for LOO: it gives a "regional fishing
    quality" signal even for locations never seen during training, as long as
    at least one creel survey exists nearby.

    Returns:
        regional_cpue_100km  -- IDW mean CPUE (fish/hr)
        regional_weight_100km -- IDW mean synthetic weight (lb)
        n_nearby_creel_surveys -- count of surveys within radius
    """
    result = {
        "regional_cpue_100km": float("nan"),
        "regional_weight_100km": float("nan"),
        "n_nearby_creel_surveys": 0.0,
    }
    if _isnan(lat) or _isnan(lon) or creel_df.empty:
        return result

    # Vectorised distance computation for speed
    clat = creel_df["latitude"].values
    clon = creel_df["longitude"].values

    # Quick bounding-box filter (~1 deg lat ~ 111 km)
    deg_margin = radius_km / 85.0  # conservative
    mask = (
        (np.abs(clat - lat) < deg_margin) &
        (np.abs(clon - lon) < deg_margin)
    )
    if not np.any(mask):
        return result

    idx = np.where(mask)[0]
    dists = np.array([
        _haversine_km(lat, lon, float(clat[i]), float(clon[i]))
        for i in idx
    ])
    within = dists <= radius_km
    if not np.any(within):
        return result

    sel_idx = idx[within]
    sel_dists = dists[within]

    # Inverse distance weights (add 1 km floor to avoid division by zero
    # and to prevent the exact-location creel from dominating)
    weights = 1.0 / np.maximum(sel_dists, 1.0)

    cpue_vals = creel_df["cpue_fish_per_hour"].values[sel_idx]
    wt_vals = creel_df["synthetic_weight_lb"].values[sel_idx]

    # Mask NaN values in source data
    cpue_valid = ~np.isnan(cpue_vals)
    wt_valid = ~np.isnan(wt_vals)

    if np.any(cpue_valid):
        w = weights[cpue_valid]
        result["regional_cpue_100km"] = float(
            np.sum(cpue_vals[cpue_valid] * w) / np.sum(w)
        )
    if np.any(wt_valid):
        w = weights[wt_valid]
        result["regional_weight_100km"] = float(
            np.sum(wt_vals[wt_valid] * w) / np.sum(w)
        )

    result["n_nearby_creel_surveys"] = float(len(sel_idx))
    return result


# ---------------------------------------------------------------------------
# 4.  Waterbody type features
# ---------------------------------------------------------------------------

def _waterbody_type_encoded(
    area_acres: float,
    shore_dev: float,
    is_lake: int,
) -> dict[str, float]:
    """Encode waterbody type from observable characteristics.

    We classify into three types:
      - River        (area_acres == 0 or is_lake == 0 with shore_dev ~1)
      - Reservoir     (is_lake == 1 with shore_dev > 2.5 — irregular shoreline)
      - Natural lake  (is_lake == 1 with shore_dev <= 2.5 — rounder shape)

    Returns one-hot-ish encoding plus a continuous reservoir score.
    """
    if _isnan(area_acres):
        area_acres = 0.0
    sd = shore_dev if not _isnan(shore_dev) else 1.0

    is_river = 1.0 if (area_acres <= 0 or is_lake == 0) else 0.0

    if is_river:
        return {
            "wtype_river": 1.0,
            "wtype_reservoir": 0.0,
            "wtype_natural_lake": 0.0,
            "reservoir_score": 0.0,
        }

    # Reservoirs tend to have higher shore development (dendritic shapes)
    # and larger areas. Natural lakes are rounder (shore_dev closer to 1).
    reservoir_prob = _clamp((sd - 1.5) / 4.0, 0.0, 1.0)

    return {
        "wtype_river": 0.0,
        "wtype_reservoir": reservoir_prob,
        "wtype_natural_lake": 1.0 - reservoir_prob,
        "reservoir_score": reservoir_prob,
    }


# ---------------------------------------------------------------------------
# 5.  Ecoregion proxy via lat/lon clustering
# ---------------------------------------------------------------------------

# Pre-defined ecoregion centroids (lat, lon) based on Level-II EPA ecoregions
# relevant to US bass fishing.  We assign each location to the nearest centroid.
_ECOREGION_CENTROIDS: list[tuple[float, float, str]] = [
    (28.5, -81.5, "florida_peninsula"),
    (30.5, -87.0, "gulf_coastal_plain"),
    (32.0, -93.0, "west_gulf_coastal"),
    (33.5, -85.0, "piedmont_south"),
    (35.0, -82.0, "southern_appalachian"),
    (36.5, -93.5, "ozark_highlands"),
    (34.0, -97.0, "central_plains_south"),
    (37.5, -78.0, "mid_atlantic"),
    (39.0, -96.0, "central_plains_north"),
    (41.0, -82.0, "great_lakes_south"),
    (43.5, -89.0, "upper_midwest"),
    (44.5, -75.0, "northeast"),
    (46.0, -93.0, "northern_forests"),
    (34.5, -114.0, "southwest_desert"),
    (38.5, -121.0, "california_central"),
]


def _assign_ecoregion(lat: float, lon: float) -> tuple[int, str]:
    """Assign a location to the nearest ecoregion centroid."""
    if _isnan(lat) or _isnan(lon):
        return -1, "unknown"
    best_dist = float("inf")
    best_idx = 0
    best_name = "unknown"
    for i, (clat, clon, name) in enumerate(_ECOREGION_CENTROIDS):
        d = _haversine_km(lat, lon, clat, clon)
        if d < best_dist:
            best_dist = d
            best_idx = i
            best_name = name
    return best_idx, best_name


def _ecoregion_features(
    lat: float,
    lon: float,
    creel_df: pd.DataFrame,
) -> dict[str, float]:
    """Compute ecoregion cluster index and mean weight from creel data.

    For each ecoregion, we compute the mean catch rate from all creel surveys
    in that region.  This transfers knowledge between locations in the same
    ecological zone.
    """
    eco_idx, _ = _assign_ecoregion(lat, lon)
    result: dict[str, float] = {
        "ecoregion_cluster": float(eco_idx),
        "ecoregion_mean_weight": float("nan"),
        "ecoregion_mean_cpue": float("nan"),
        "ecoregion_n_surveys": 0.0,
    }
    if eco_idx < 0 or creel_df.empty:
        return result

    # Assign every creel survey to an ecoregion and find same-cluster ones
    clat = creel_df["latitude"].values
    clon = creel_df["longitude"].values

    # Vectorised: find ecoregion for each creel survey
    same_eco_mask = np.zeros(len(creel_df), dtype=bool)
    for i in range(len(creel_df)):
        ci, _ = _assign_ecoregion(float(clat[i]), float(clon[i]))
        if ci == eco_idx:
            same_eco_mask[i] = True

    n_same = int(np.sum(same_eco_mask))
    result["ecoregion_n_surveys"] = float(n_same)

    if n_same > 0:
        wt = creel_df.loc[same_eco_mask, "synthetic_weight_lb"]
        cpue = creel_df.loc[same_eco_mask, "cpue_fish_per_hour"]
        if wt.notna().any():
            result["ecoregion_mean_weight"] = float(wt.mean())
        if cpue.notna().any():
            result["ecoregion_mean_cpue"] = float(cpue.mean())

    return result


# Cached mapping: eco_idx -> {mean_weight, mean_cpue, n_surveys}
_ecoregion_stats_cache: dict[int, dict[str, float]] | None = None


def _build_ecoregion_stats(creel_df: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Pre-compute ecoregion stats once for all creel surveys."""
    global _ecoregion_stats_cache
    if _ecoregion_stats_cache is not None:
        return _ecoregion_stats_cache

    stats: dict[int, dict[str, float]] = {}
    if creel_df.empty:
        _ecoregion_stats_cache = stats
        return stats

    clat = creel_df["latitude"].values
    clon = creel_df["longitude"].values
    eco_ids = np.array([
        _assign_ecoregion(float(clat[i]), float(clon[i]))[0]
        for i in range(len(creel_df))
    ])

    for eid in np.unique(eco_ids):
        if eid < 0:
            continue
        mask = eco_ids == eid
        wt = creel_df.loc[mask, "synthetic_weight_lb"]
        cpue = creel_df.loc[mask, "cpue_fish_per_hour"]
        stats[int(eid)] = {
            "mean_weight": float(wt.mean()) if wt.notna().any() else float("nan"),
            "mean_cpue": float(cpue.mean()) if cpue.notna().any() else float("nan"),
            "n_surveys": float(mask.sum()),
        }

    _ecoregion_stats_cache = stats
    return stats


def _ecoregion_features_fast(
    lat: float,
    lon: float,
    creel_df: pd.DataFrame,
) -> dict[str, float]:
    """Fast version using cached ecoregion stats."""
    eco_idx, _ = _assign_ecoregion(lat, lon)
    stats = _build_ecoregion_stats(creel_df)

    result: dict[str, float] = {
        "ecoregion_cluster": float(eco_idx),
        "ecoregion_mean_weight": float("nan"),
        "ecoregion_mean_cpue": float("nan"),
        "ecoregion_n_surveys": 0.0,
    }
    if eco_idx in stats:
        s = stats[eco_idx]
        result["ecoregion_mean_weight"] = s["mean_weight"]
        result["ecoregion_mean_cpue"] = s["mean_cpue"]
        result["ecoregion_n_surveys"] = s["n_surveys"]

    return result


# ---------------------------------------------------------------------------
# 6.  Forage base proxy (shad habitat score)
# ---------------------------------------------------------------------------

def _shad_habitat_score(
    lat: float,
    area_acres: float,
    is_lake: int,
) -> float:
    """Estimate shad-based forage availability.

    Shad are the primary forage base for largemouth bass in most US waters.

    Threadfin shad:
    - Die below ~45F water temp
    - Range: roughly south of 37N latitude
    - Prefer open-water reservoirs > 500 acres

    Gizzard shad:
    - Cold-tolerant, found throughout the eastern US
    - More common in larger reservoirs and rivers
    - Very high biomass in eutrophic systems

    A location with both shad species has a much richer forage base.
    """
    if _isnan(lat) or _isnan(area_acres):
        return float("nan")

    # Threadfin shad probability (based on latitude)
    if lat <= 33.0:
        threadfin = 0.9
    elif lat <= 37.0:
        threadfin = 0.9 - (lat - 33.0) / 4.0 * 0.8  # linear decline
    else:
        threadfin = 0.05  # rare, occasional stocking

    # Gizzard shad probability (based on area and type)
    if area_acres <= 0:
        # Rivers: gizzard shad common in larger rivers
        gizzard = 0.5
    elif area_acres < 500:
        gizzard = 0.2
    elif area_acres < 5000:
        gizzard = 0.5
    else:
        gizzard = 0.8

    # Latitude adjustment for gizzard (less common in far north)
    if lat > 43.0:
        gizzard *= max(0.2, 1.0 - (lat - 43.0) / 7.0)

    # Combined forage score: having both species is multiplicatively better
    # because threadfin provide open-water prey and gizzard provide
    # bottom/structure prey.
    score = 0.4 * threadfin + 0.4 * gizzard + 0.2 * threadfin * gizzard
    return float(_clamp(score, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _isnan(x: Any) -> bool:
    """Check for NaN, handling non-float types."""
    try:
        return math.isnan(float(x))
    except (ValueError, TypeError):
        return True


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ===================================================================
# PUBLIC API
# ===================================================================

def compute_location_quality_features(
    lat: float,
    lon: float,
    area_acres: float,
    max_depth_ft: float,
    shore_dev: float,
    is_lake: int,
    creel_data: pd.DataFrame | None = None,
) -> dict[str, float]:
    """Compute all location-quality features for a single location.

    These features estimate the intrinsic catch-quality of a waterbody
    from physical and environmental characteristics -- without requiring
    any tournament history from that location.

    Parameters
    ----------
    lat, lon : float
        Location coordinates (decimal degrees).
    area_acres : float
        Surface area in acres (0 for rivers).
    max_depth_ft : float
        Maximum depth in feet.
    shore_dev : float
        Shore development index (1.0 = perfect circle).
    is_lake : int
        1 if lake/reservoir, 0 if river.
    creel_data : pd.DataFrame or None
        Creel survey data with columns: latitude, longitude,
        synthetic_weight_lb, cpue_fish_per_hour.
        If None, loads from default path.

    Returns
    -------
    dict[str, float]
        Feature name -> value.
    """
    if creel_data is None:
        creel_data = _load_creel()

    features: dict[str, float] = {}

    # 1. Morphometric productivity
    features["morphometric_productivity_score"] = _morphometric_productivity(
        area_acres, max_depth_ft, shore_dev,
    )

    # 2. Geographic/climate proxy
    features["latitude_growth_potential"] = _latitude_growth_potential(lat)
    features["growing_degree_proxy"] = _growing_degree_proxy(lat)

    # 3. Nearest-neighbor creel (KEY for LOO)
    nn = _nearest_neighbor_creel(lat, lon, creel_data, radius_km=100.0)
    features.update(nn)

    # 4. Waterbody type
    wtype = _waterbody_type_encoded(area_acres, shore_dev, is_lake)
    features.update(wtype)

    # 5. Ecoregion
    eco = _ecoregion_features_fast(lat, lon, creel_data)
    features.update(eco)

    # 6. Forage base proxy
    features["shad_habitat_score"] = _shad_habitat_score(lat, area_acres, is_lake)

    # --- Derived interactions ---
    # Productivity x latitude: large southern reservoirs are the best
    morph = features["morphometric_productivity_score"]
    lat_gp = features["latitude_growth_potential"]
    if not (_isnan(morph) or _isnan(lat_gp)):
        features["productivity_x_latitude"] = morph * lat_gp
    else:
        features["productivity_x_latitude"] = float("nan")

    # Regional weight x shad: high forage + good region = great location
    rw = features["regional_weight_100km"]
    shad = features["shad_habitat_score"]
    if not (_isnan(rw) or _isnan(shad)):
        features["regional_weight_x_shad"] = rw * shad
    else:
        features["regional_weight_x_shad"] = float("nan")

    # --- Smallmouth bass habitat features ---
    # Critical for LOO: northern lakes like St. Clair, Champlain, Mille Lacs
    # produce huge smallmouth bass despite low "largemouth" scores
    features.update(_smallmouth_habitat_features(lat, max_depth_ft, area_acres, shore_dev))

    return features


def _smallmouth_habitat_features(
    lat: float, max_depth_ft: float, area_acres: float, shore_dev: float,
) -> dict[str, float]:
    """Estimate smallmouth bass habitat quality.

    Smallmouth bass (Micropterus dolomieu) thrive in:
    - Clear, cool, deep lakes (unlike largemouth which prefer warm/shallow)
    - Rocky substrate with moderate current
    - Northern US/Canada (40-48°N) where water stays 60-75°F in summer
    - Large, deep lakes with clear water (Great Lakes, Champlain, Mille Lacs)

    Key insight: many of the top tournament fisheries are actually
    smallmouth-dominated, especially in the Northeast/Midwest.
    Lake St. Clair (MI), Lake Champlain (NY), Mille Lacs (MN),
    St. Lawrence River are all world-class smallmouth waters.

    Without this feature, our model severely underpredicts these locations
    because the largemouth-oriented features (shad, warm climate) score them low.
    """
    if _isnan(lat):
        return {
            "smallmouth_habitat_score": float("nan"),
            "species_diversity_potential": float("nan"),
            "northern_trophy_potential": float("nan"),
        }

    # Smallmouth prefer 40-48°N latitude (peak at ~44°N)
    smb_lat_score = math.exp(-0.5 * ((lat - 44) / 4) ** 2) if not _isnan(lat) else 0

    # Smallmouth prefer deeper, clearer lakes (30-100+ ft)
    depth = max_depth_ft if not _isnan(max_depth_ft) else 30
    smb_depth_score = min(1.0, depth / 60.0)  # Deeper = better for SMB

    # Large lakes are better for smallmouth (more rock structure, clearer water)
    area = area_acres if not _isnan(area_acres) else 0
    smb_size_score = min(1.0, math.log1p(area) / 12.0) if area > 0 else 0.3

    # Combined smallmouth habitat score
    smallmouth_score = 0.5 * smb_lat_score + 0.3 * smb_depth_score + 0.2 * smb_size_score

    # Species diversity potential — both LMB and SMB present
    # Southern = mainly LMB, northern = mainly SMB, overlap zone 36-42°N
    lmb_potential = max(0, 1.0 - max(0, (lat - 36) / 10))  # High in south
    smb_potential = smb_lat_score

    # Northern trophy potential — big fish despite cold climate
    # This captures why St. Clair/Champlain produce 19+ lb median weights
    # These locations have excellent smallmouth + occasional giant largemouth
    northern_trophy = smb_lat_score * smb_depth_score * smb_size_score

    return {
        "smallmouth_habitat_score": round(smallmouth_score, 4),
        "species_diversity_potential": round(lmb_potential + smb_potential, 4),
        "northern_trophy_potential": round(northern_trophy, 4),
    }


def add_location_quality_to_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Add location-quality features to every row in a tournament dataset.

    Expects columns: lat, lon, area_acres, max_depth_ft, shore_dev, is_lake.
    Falls back to morphometry JSON for area/depth/shore_dev if columns
    contain NaN and the location name matches.

    Parameters
    ----------
    df : pd.DataFrame
        Tournament dataset (e.g., validation_dataset_v5.csv).

    Returns
    -------
    pd.DataFrame
        Copy of *df* with new location-quality columns appended.
    """
    df = df.copy()
    creel = _load_creel()
    morphometry = _load_morphometry()

    # Reset ecoregion cache so it is rebuilt fresh
    global _ecoregion_stats_cache
    _ecoregion_stats_cache = None

    # Pre-compute ecoregion stats once
    _build_ecoregion_stats(creel)

    all_features: list[dict[str, float]] = []

    for idx, row in df.iterrows():
        lat = _safe_float(row, "lat")
        lon = _safe_float(row, "lon")
        area = _safe_float(row, "area_acres")
        depth = _safe_float(row, "max_depth_ft")
        sd = _safe_float(row, "shore_dev")
        is_lk = int(row.get("is_lake", 1)) if not _isnan(_safe_float(row, "is_lake")) else 1

        # Fall back to morphometry JSON if we have a location name
        loc_name = row.get("location", "")
        if isinstance(loc_name, str) and loc_name in morphometry:
            m = morphometry[loc_name]
            if _isnan(area):
                area = m.get("area_acres", float("nan"))
            if _isnan(depth):
                depth = m.get("max_depth_ft", float("nan"))
            if _isnan(sd):
                sd = m.get("shore_dev", float("nan"))
            if _isnan(lat):
                lat = m.get("lat", float("nan"))
            if _isnan(lon):
                lon = m.get("lon", float("nan"))

        feats = compute_location_quality_features(
            lat=lat,
            lon=lon,
            area_acres=area,
            max_depth_ft=depth,
            shore_dev=sd,
            is_lake=is_lk,
            creel_data=creel,
        )
        all_features.append(feats)

    feat_df = pd.DataFrame(all_features, index=df.index)
    return pd.concat([df, feat_df], axis=1)


def _safe_float(row: Any, col: str) -> float:
    """Extract a float from a row, returning NaN on failure."""
    try:
        v = row[col]
        f = float(v)
        return f
    except (KeyError, ValueError, TypeError):
        return float("nan")


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Test with Lake Guntersville (well-known productive bass lake)
    feats = compute_location_quality_features(
        lat=34.37, lon=-86.3,
        area_acres=69100, max_depth_ft=60, shore_dev=4.2,
        is_lake=1,
    )
    print("=== Lake Guntersville (productive southern reservoir) ===")
    for k, v in sorted(feats.items()):
        print(f"  {k:40s} = {v:.4f}" if not _isnan(v) else f"  {k:40s} = NaN")

    print()

    # Test with Cayuga Lake (deep, natural, northern — less productive for bass)
    feats2 = compute_location_quality_features(
        lat=42.68, lon=-76.72,
        area_acres=42561, max_depth_ft=435, shore_dev=1.9,
        is_lake=1,
    )
    print("=== Cayuga Lake (deep northern natural lake) ===")
    for k, v in sorted(feats2.items()):
        print(f"  {k:40s} = {v:.4f}" if not _isnan(v) else f"  {k:40s} = NaN")

    print()

    # Test with a river
    feats3 = compute_location_quality_features(
        lat=30.08, lon=-93.73,
        area_acres=0, max_depth_ft=30, shore_dev=1.0,
        is_lake=0,
    )
    print("=== Sabine River ===")
    for k, v in sorted(feats3.items()):
        print(f"  {k:40s} = {v:.4f}" if not _isnan(v) else f"  {k:40s} = NaN")
