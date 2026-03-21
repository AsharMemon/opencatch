"""Species probability endpoint."""
from fastapi import APIRouter, Request, Query
import orjson

from castline.api.config import settings
from castline.api.models.schemas import SpeciesListResponse, SpeciesActivity

router = APIRouter()


@router.get("/species", response_model=SpeciesListResponse)
async def get_species(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    depth_m: float = Query(5.0, ge=0),
    lake_area_ha: float = Query(100.0, ge=0),
):
    """Get species probabilities and activity for a location."""
    redis = request.app.state.redis

    cache_key = f"species:{round(lat, 1)}:{round(lon, 1)}"
    cached = await redis.get(cache_key)
    if cached:
        return SpeciesListResponse(**orjson.loads(cached))

    from castline.services.site_prior import (
        compute_species_probabilities,
        compute_multi_species_features,
        _species_group,
        SPECIES_CATALOG,
    )
    from datetime import datetime, timezone
    import math

    month = datetime.now(timezone.utc).month

    # Get probabilities
    probs = compute_species_probabilities(lat, lon, depth_m, lake_area_ha)

    # Get multi-species features for activity assessment
    features = compute_multi_species_features(
        lat, lon, water_temp_c=None, month=month,
        depth_m=depth_m, lake_area_ha=lake_area_ha,
    )

    # Build species list sorted by probability
    species_list = []
    for species_key, prob in sorted(probs.items(), key=lambda x: -x[1]):
        if prob < 0.01:
            continue
        sp = SPECIES_CATALOG.get(species_key, {})

        # Determine activity level from spawn/feed
        is_spawning = month in sp.get("spawn_months", [])
        is_feeding = month in sp.get("peak_feed_months", [])
        if is_feeding:
            activity = "high"
            notes = "Peak feeding period"
        elif is_spawning:
            activity = "moderate"
            notes = "Spawning — aggressive but selective"
        else:
            activity = "low"
            notes = None

        opt_c = sp.get("optimal_temp_c", 20)
        opt_f = round(opt_c * 9 / 5 + 32, 1)

        species_list.append(SpeciesActivity(
            species=sp.get("common_name", species_key),
            probability=round(prob, 3),
            activity_level=activity,
            optimal_temp_f=opt_f,
            notes=notes,
        ))

    # Dominant group
    dominant = features.get("dominant_species_group", "bass")

    response = SpeciesListResponse(
        lat=lat, lon=lon,
        species=species_list[:20],  # top 20
        dominant_group=dominant,
    )

    await redis.set(
        cache_key,
        orjson.dumps(response.model_dump(), default=str).decode(),
        ex=settings.cache_species_ttl,
    )
    return response
