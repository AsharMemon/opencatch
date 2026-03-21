"""Background worker: refreshes conditions predictions and caches.

Runs on a loop, fetching fresh USGS/weather data for popular locations
and precomputing conditions scores into Redis.

Refresh schedule:
- Every 15 minutes: refresh USGS and weather caches
- Every 60 minutes: re-run predictions for popular locations
- On startup: pre-warm cache for top 50 locations
"""
import asyncio
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis

from castline.api.config import settings


async def refresh_loop():
    """Main refresh loop — runs every 15 minutes."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    # Try to load predictor (auto-detect V15 > V14)
    predictor = None
    try:
        from castline.models.inference_v14 import V14Predictor
        from pathlib import Path
        model_dir = Path(settings.model_dir)
        alt_model_dir = Path(__file__).resolve().parents[2] / "models"
        for try_dir in (model_dir, alt_model_dir):
            for v in ("v15", "v14"):
                if (try_dir / f"cpue_{v}_seen_catboost.cbm").exists():
                    predictor = V14Predictor.load(try_dir, version=v)
                    print(f"[WORKER] {v.upper()} model loaded from {try_dir}", flush=True)
                    break
            if predictor is not None:
                break
        if predictor is None:
            print(f"[WORKER] No model artifacts found", flush=True)
    except Exception as e:
        print(f"[WORKER] WARNING: Model not available: {e}", flush=True)

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()
        now = datetime.now(timezone.utc)
        print(f"[WORKER] Refresh cycle {cycle} at {now.isoformat()}", flush=True)

        try:
            # Load top locations from model stats for pre-warming cache
            top_locations = _get_top_locations(limit=50)
            if top_locations and cycle == 1:
                print(f"[WORKER] Pre-warming cache for {len(top_locations)} top locations", flush=True)
                await _prewarm_cache(r, top_locations)

            # Refresh USGS data cache
            await _refresh_usgs_cache(r)

            # Refresh weather cache
            await _refresh_weather_cache(r)

            # Every 4th cycle (hourly), refresh predictions for popular locations
            if cycle % 4 == 0 or cycle == 1:
                await _refresh_popular_predictions(r, predictor)

            # Process any locations queued for refresh
            await _process_refresh_queue(r, predictor)

            # Invalidate stale prediction caches when data changes
            await _invalidate_stale_predictions(r)

            elapsed = time.time() - t0
            print(f"[WORKER] Cycle {cycle} done in {elapsed:.1f}s", flush=True)
        except Exception as e:
            print(f"[WORKER] Error in cycle {cycle}: {e}", flush=True)

        # Sleep 15 minutes between refreshes
        await asyncio.sleep(900)


def _get_top_locations(limit: int = 50) -> list[dict]:
    """Get top locations by event count from model location stats."""
    import json
    from pathlib import Path

    model_dir = Path(__file__).resolve().parents[2] / "models"
    for version in ("v15", "v14"):
        stats_file = model_dir / f"cpue_{version}_location_stats.json"
        if stats_file.exists():
            with open(stats_file) as f:
                stats = json.load(f)
            # Sort by event count, filter to those with coordinates
            locations = []
            for name, data in stats.items():
                lat = data.get("lat")
                lon = data.get("lon")
                n_events = data.get("n_events", data.get("count", 0)) or 0
                if lat is not None and lon is not None and n_events >= 3:
                    locations.append({"name": name, "lat": lat, "lon": lon, "n_events": n_events})
            locations.sort(key=lambda x: x["n_events"], reverse=True)
            return locations[:limit]
    return []


async def _prewarm_cache(r: aioredis.Redis, locations: list[dict]):
    """Pre-warm weather cache for popular locations."""
    from castline.api.services.weather import get_current_weather

    for loc in locations:
        try:
            await get_current_weather(loc["lat"], loc["lon"], redis=r)
        except Exception:
            pass
        await asyncio.sleep(0.2)  # Rate limit


async def _refresh_usgs_cache(r: aioredis.Redis):
    """Fetch latest USGS data for cached site IDs."""
    import httpx

    # Get all cached USGS keys to find which sites to refresh
    keys = []
    async for key in r.scan_iter("usgs:*"):
        keys.append(key)

    if not keys:
        print(f"[WORKER] No USGS sites in cache to refresh", flush=True)
        return

    print(f"[WORKER] Refreshing {len(keys)} USGS sites", flush=True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for key in keys:
            site_id = key.split(":", 1)[1]
            try:
                url = (
                    f"https://waterservices.usgs.gov/nwis/iv/"
                    f"?format=json&sites={site_id}"
                    f"&parameterCd=00060,00065,00010"
                    f"&siteStatus=all"
                )
                resp = await client.get(url)
                if resp.status_code == 200:
                    await r.set(key, resp.text, ex=settings.cache_usgs_ttl)
                    # Mark that fresh data arrived — predictions may need invalidation
                    await r.sadd("data_updated_sites", site_id)
            except Exception:
                pass
            await asyncio.sleep(0.5)  # Rate limit


async def _refresh_weather_cache(r: aioredis.Redis):
    """Refresh weather for cached coordinate keys."""
    from castline.api.services.weather import get_current_weather

    keys = []
    async for key in r.scan_iter("weather:*:current"):
        keys.append(key)

    if not keys:
        print("[WORKER] No weather locations in cache to refresh", flush=True)
        return

    print(f"[WORKER] Refreshing weather for {len(keys)} locations", flush=True)

    for key in keys:
        try:
            # Parse lat/lon from key format "weather:{lat}:{lon}:current"
            parts = key.split(":")
            if len(parts) >= 3:
                lat, lon = float(parts[1]), float(parts[2])
                await get_current_weather(lat, lon, redis=r)
        except Exception:
            pass
        await asyncio.sleep(0.3)  # Rate limit Open-Meteo


async def _refresh_popular_predictions(r: aioredis.Redis, predictor):
    """Re-run predictions for popular locations and update the cache.

    Uses the popularity-sorted set from the cache service to determine
    which locations to refresh. Runs hourly.
    """
    if predictor is None:
        return

    from castline.api.services.cache import (
        get_popular_locations,
        prediction_cache_key,
        set_cached_prediction,
        track_cache_key_for_location,
    )
    from castline.api.services.features import collect_realtime_features

    popular = await get_popular_locations(r, limit=50)
    if not popular:
        # Fall back to model's top locations
        top_locs = _get_top_locations(limit=20)
        popular = [loc["name"] for loc in top_locs]

    if not popular:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model_version = predictor.model_metadata.get("model_version", "v13")
    refreshed = 0

    print(f"[WORKER] Refreshing predictions for {len(popular)} popular locations", flush=True)

    for location in popular:
        try:
            features = await collect_realtime_features(
                location=location,
                date=today,
                redis=r,
            )
            result = predictor.predict(
                location=location,
                date=today,
                precomputed_features={
                    k: v for k, v in features.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                },
            )
            rd = result.to_dict()
            hist_avg = predictor.location_means.get(location, 3.0)

            # Build a minimal cached result
            from castline.api.routers.predictions import _weight_to_fishing_score
            score = _weight_to_fishing_score(rd["predicted_weight_lb"], hist_avg)
            cache_data = {
                "fishing_score": score,
                "confidence": round(rd.get("confidence", 0.5), 3),
                "model_version": model_version,
                "conditions": {
                    "location": location,
                    "date": today,
                    "predicted_weight_lb": round(rd["predicted_weight_lb"], 2),
                    "historical_avg_lb": round(hist_avg, 2),
                },
            }

            cache_key = prediction_cache_key(location, today, model_version)
            await set_cached_prediction(r, cache_key, cache_data, settings.cache_conditions_ttl)
            await track_cache_key_for_location(r, location, cache_key)
            refreshed += 1

        except Exception as exc:
            pass  # Skip failures silently
        await asyncio.sleep(0.1)

    print(f"[WORKER] Refreshed {refreshed}/{len(popular)} predictions", flush=True)


async def _process_refresh_queue(r: aioredis.Redis, predictor):
    """Process locations explicitly queued for refresh via the cache service."""
    from castline.api.services.cache import get_refresh_queue, invalidate_location_cache

    queued = await get_refresh_queue(r)
    if not queued:
        return

    print(f"[WORKER] Processing {len(queued)} queued refresh requests", flush=True)
    for location in queued:
        await invalidate_location_cache(r, location)


async def _invalidate_stale_predictions(r: aioredis.Redis):
    """When new USGS/weather data arrives, invalidate related prediction caches.

    Checks the data_updated_sites set (populated by _refresh_usgs_cache)
    and invalidates prediction caches for affected locations.
    """
    from castline.api.services.cache import invalidate_location_cache

    updated_sites = await r.smembers("data_updated_sites")
    if not updated_sites:
        return

    # Clear the set
    await r.delete("data_updated_sites")

    # Map USGS site IDs back to locations (via the known locations in model stats)
    import json
    from pathlib import Path
    model_dir = Path(__file__).resolve().parents[2] / "models"

    site_to_location = {}
    for version in ("v15", "v14"):
        stats_file = model_dir / f"cpue_{version}_location_stats.json"
        if stats_file.exists():
            with open(stats_file) as f:
                stats = json.load(f)
            for name, data in stats.items():
                site_id = data.get("usgs_site_id")
                if site_id:
                    site_to_location[site_id] = name
            break

    invalidated = 0
    for site_id in updated_sites:
        location = site_to_location.get(site_id)
        if location:
            count = await invalidate_location_cache(r, location)
            invalidated += count

    if invalidated > 0:
        print(f"[WORKER] Invalidated {invalidated} cache entries from {len(updated_sites)} data updates", flush=True)


if __name__ == "__main__":
    print("[WORKER] Starting CASTLINE conditions refresh worker", flush=True)
    asyncio.run(refresh_loop())
