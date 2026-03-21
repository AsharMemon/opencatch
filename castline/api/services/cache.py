"""Redis prediction caching service.

Caches prediction results with TTL based on data type:
- Current conditions: 1-hour TTL
- Forecasts: 6-hour TTL

Cache keys: pred:{location}:{date}:{model_version}
Supports invalidation when new weather/USGS data arrives.
"""

import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _make_cache_key(prefix: str, location: str, date: str, model_version: str = "") -> str:
    """Build a deterministic cache key from prediction parameters."""
    # Normalize location name for consistent caching
    loc_normalized = location.strip().lower()
    raw = f"{prefix}:{loc_normalized}:{date}:{model_version}"
    # Use a hash suffix to keep keys short for Redis
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"{prefix}:{h}"


def prediction_cache_key(location: str, date: str, model_version: str = "") -> str:
    return _make_cache_key("pred", location, date, model_version)


def forecast_cache_key(location: str, start_date: str, model_version: str = "") -> str:
    return _make_cache_key("fcast", location, start_date, model_version)


def batch_cache_key(locations_hash: str, date: str) -> str:
    return f"batch:{locations_hash}:{date}"


async def get_cached_prediction(redis, key: str) -> Optional[dict]:
    """Retrieve a cached prediction result from Redis."""
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw:
            logger.debug("Cache HIT: %s", key)
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Cache read error for %s: %s", key, exc)
    return None


async def set_cached_prediction(redis, key: str, data: dict, ttl: int) -> None:
    """Store a prediction result in Redis with TTL."""
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(data, default=str), ex=ttl)
        logger.debug("Cache SET: %s (TTL=%ds)", key, ttl)
    except Exception as exc:
        logger.warning("Cache write error for %s: %s", key, exc)


async def invalidate_location_cache(redis, location: str) -> int:
    """Invalidate all cached predictions for a location.

    Called when new weather/USGS data arrives for this location.
    Returns the number of keys deleted.
    """
    if redis is None:
        return 0
    try:
        loc_normalized = location.strip().lower()
        # Scan for matching prediction and forecast keys
        deleted = 0
        patterns = [f"pred:*", f"fcast:*"]
        # Since we use hashed keys, we track location->keys in a set
        loc_key = f"loc_keys:{hashlib.md5(loc_normalized.encode()).hexdigest()[:12]}"
        members = await redis.smembers(loc_key)
        if members:
            for k in members:
                await redis.delete(k)
                deleted += 1
            await redis.delete(loc_key)
        logger.info("Invalidated %d cache entries for %s", deleted, location)
        return deleted
    except Exception as exc:
        logger.warning("Cache invalidation error for %s: %s", location, exc)
        return 0


async def track_cache_key_for_location(redis, location: str, cache_key: str) -> None:
    """Track a cache key associated with a location for invalidation."""
    if redis is None:
        return
    try:
        loc_normalized = location.strip().lower()
        loc_set_key = f"loc_keys:{hashlib.md5(loc_normalized.encode()).hexdigest()[:12]}"
        await redis.sadd(loc_set_key, cache_key)
        # Expire the tracking set after 24h (longest possible cache TTL)
        await redis.expire(loc_set_key, 86400)
    except Exception as exc:
        logger.debug("Cache tracking error: %s", exc)


async def mark_location_for_refresh(redis, location: str) -> None:
    """Add a location to the refresh queue in Redis."""
    if redis is None:
        return
    try:
        await redis.sadd("refresh_queue", location)
    except Exception as exc:
        logger.debug("Refresh queue error: %s", exc)


async def get_refresh_queue(redis) -> list[str]:
    """Get all locations queued for refresh and clear the queue."""
    if redis is None:
        return []
    try:
        members = await redis.smembers("refresh_queue")
        if members:
            await redis.delete("refresh_queue")
        return list(members)
    except Exception as exc:
        logger.debug("Refresh queue read error: %s", exc)
        return []


async def track_popular_location(redis, location: str) -> None:
    """Increment the popularity counter for a location (for refresh prioritization)."""
    if redis is None:
        return
    try:
        await redis.zincrby("popular_locations", 1, location)
        # Trim to top 200 periodically
        count = await redis.zcard("popular_locations")
        if count > 250:
            await redis.zremrangebyrank("popular_locations", 0, count - 201)
    except Exception as exc:
        logger.debug("Popularity tracking error: %s", exc)


async def get_popular_locations(redis, limit: int = 50) -> list[str]:
    """Get the most popular locations by request count."""
    if redis is None:
        return []
    try:
        # Returns highest-score members first
        members = await redis.zrevrange("popular_locations", 0, limit - 1)
        return list(members)
    except Exception as exc:
        logger.debug("Popular locations read error: %s", exc)
        return []
