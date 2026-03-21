"""Locations discovery and detail endpoints."""
import csv
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from castline.api.models.schemas import LocationEmbeddingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Pre-load location stats from V15 model artifacts
_LOCATION_STATS: dict | None = None

# ── Embedding cache (keyed by rounded lat/lon) ──────────────
_GEOCLIP_EMBEDDINGS: dict[tuple[float, float], list[float]] | None = None
_SATCLIP_EMBEDDINGS: dict[tuple[float, float], list[float]] | None = None

_DATA_DIR = Path(__file__).resolve().parents[2] / "validation" / "data" / "raw"


def _round_coord(val: float) -> float:
    """Round to 7 decimal places to match CSV precision."""
    return round(val, 7)


def _load_embedding_csv(path: Path, prefix: str) -> dict[tuple[float, float], list[float]]:
    """Load an embedding CSV into a dict keyed by (lat, lon)."""
    result: dict[tuple[float, float], list[float]] = {}
    if not path.exists():
        logger.warning("Embedding file not found: %s", path)
        return result
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = _round_coord(float(row["lat"]))
            lon = _round_coord(float(row["lon"]))
            cols = sorted([c for c in row if c.startswith(prefix)], key=lambda c: int(c.split("_")[1]))
            embedding = [float(row[c]) for c in cols]
            result[(lat, lon)] = embedding
    return result


def _load_geoclip() -> dict[tuple[float, float], list[float]]:
    global _GEOCLIP_EMBEDDINGS
    if _GEOCLIP_EMBEDDINGS is not None:
        return _GEOCLIP_EMBEDDINGS
    _GEOCLIP_EMBEDDINGS = _load_embedding_csv(
        _DATA_DIR / "location_embeddings_geoclip.csv", "geoclip"
    )
    logger.info("Loaded %d GeoCLIP embeddings", len(_GEOCLIP_EMBEDDINGS))
    return _GEOCLIP_EMBEDDINGS


def _load_satclip() -> dict[tuple[float, float], list[float]]:
    global _SATCLIP_EMBEDDINGS
    if _SATCLIP_EMBEDDINGS is not None:
        return _SATCLIP_EMBEDDINGS
    _SATCLIP_EMBEDDINGS = _load_embedding_csv(
        _DATA_DIR / "location_embeddings_satclip.csv", "satclip"
    )
    logger.info("Loaded %d SatCLIP embeddings", len(_SATCLIP_EMBEDDINGS))
    return _SATCLIP_EMBEDDINGS


def _load_location_stats() -> dict:
    global _LOCATION_STATS
    if _LOCATION_STATS is not None:
        return _LOCATION_STATS

    # Search for latest location stats file
    model_dir = Path(__file__).resolve().parents[2] / "models"
    for version in ("v15", "v14", "v13"):
        stats_file = model_dir / f"cpue_{version}_location_stats.json"
        if stats_file.exists():
            with open(stats_file) as f:
                _LOCATION_STATS = json.load(f)
            logger.info("Loaded %d location stats from %s", len(_LOCATION_STATS), stats_file)
            return _LOCATION_STATS

    _LOCATION_STATS = {}
    return _LOCATION_STATS


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@router.get("/locations")
async def list_locations(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Center latitude for nearby search"),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Center longitude for nearby search"),
    radius_km: float = Query(100, ge=1, le=500, description="Search radius in km"),
    q: Optional[str] = Query(None, min_length=2, description="Search by location name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Discover fishing locations.

    Returns locations from the model's location stats database, with optional
    geo/text filtering. Each location includes historical CPUE stats.
    """
    stats = _load_location_stats()
    if not stats:
        return {"locations": [], "total": 0}

    results = []
    for loc_name, loc_data in stats.items():
        entry = {
            "name": loc_name,
            "event_count": loc_data.get("n_events", loc_data.get("count", 0)),
            "mean_cpue": round(loc_data.get("mean_weight", loc_data.get("mean", 0)), 3),
            "lat": loc_data.get("lat"),
            "lon": loc_data.get("lon"),
        }

        # Text filter
        if q and q.lower() not in loc_name.lower():
            continue

        # Geo filter (if lat/lon provided)
        if lat is not None and lon is not None:
            loc_lat = loc_data.get("lat")
            loc_lon = loc_data.get("lon")
            if loc_lat is not None and loc_lon is not None:
                dist = _haversine_km(lat, lon, loc_lat, loc_lon)
                if dist > radius_km:
                    continue
                entry["distance_km"] = round(dist, 1)
            else:
                continue  # Skip locations without coordinates for geo search

        results.append(entry)

    # Sort: by distance if geo search, else by event_count descending
    if lat is not None and lon is not None:
        results.sort(key=lambda x: x.get("distance_km", 9999))
    else:
        results.sort(key=lambda x: x.get("event_count", 0), reverse=True)

    total = len(results)
    results = results[offset : offset + limit]

    return {"locations": results, "total": total, "limit": limit, "offset": offset}


@router.get("/locations/{location_name}")
async def get_location_detail(
    request: Request,
    location_name: str,
):
    """Get detailed stats for a specific location."""
    stats = _load_location_stats()

    # Try exact match first, then case-insensitive
    loc_data = stats.get(location_name)
    if loc_data is None:
        for key, val in stats.items():
            if key.lower() == location_name.lower():
                loc_data = val
                location_name = key
                break

    if loc_data is None:
        raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")

    n_events = loc_data.get("n_events", loc_data.get("count", 0))
    return {
        "name": location_name,
        "event_count": n_events,
        "mean_cpue": round(loc_data.get("mean_weight", loc_data.get("mean", 0)), 3),
        "std_cpue": round(loc_data.get("std", 0), 3),
        "min_cpue": round(loc_data.get("min", 0), 3),
        "max_cpue": round(loc_data.get("max", 0), 3),
        "lat": loc_data.get("lat"),
        "lon": loc_data.get("lon"),
        "model_type": "seen" if (n_events or 0) >= 3 else "unseen",
    }


@router.get("/locations/{location_name}/embedding", response_model=LocationEmbeddingResponse)
async def get_location_embedding(
    request: Request,
    location_name: str,
):
    """Return the precomputed GeoCLIP + SatCLIP embedding for a location.

    Embeddings are 512-dim (GeoCLIP) and 256-dim (SatCLIP) vectors derived from
    satellite imagery at the location's coordinates. They capture geographic and
    environmental context used by the CASTLINE spatial models.
    """
    # Resolve location name to lat/lon via location stats
    stats = _load_location_stats()
    loc_data = stats.get(location_name)
    if loc_data is None:
        for key, val in stats.items():
            if key.lower() == location_name.lower():
                loc_data = val
                location_name = key
                break

    if loc_data is None:
        raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")

    lat = loc_data.get("lat")
    lon = loc_data.get("lon")
    if lat is None or lon is None:
        raise HTTPException(
            status_code=404,
            detail=f"Location '{location_name}' has no coordinates; cannot look up embedding",
        )

    coord_key = (_round_coord(lat), _round_coord(lon))

    # Look up GeoCLIP embedding (required)
    geoclip = _load_geoclip()
    geoclip_vec = geoclip.get(coord_key)
    if geoclip_vec is None:
        raise HTTPException(
            status_code=404,
            detail=f"No embedding found for '{location_name}' at ({lat}, {lon})",
        )

    # Look up SatCLIP embedding (optional)
    satclip = _load_satclip()
    satclip_vec = satclip.get(coord_key)

    return LocationEmbeddingResponse(
        name=location_name,
        lat=lat,
        lon=lon,
        geoclip_embedding=geoclip_vec,
        satclip_embedding=satclip_vec,
    )
