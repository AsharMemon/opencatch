/**
 * Location detail prefetch cache for OpenCatch.
 *
 * Caches location details (enriched with conditions) so that when a user
 * taps a pin the detail screen can load instantly from cache instead of
 * making a full API round-trip.
 *
 * The MapScreen calls `prefetchLocationDetail` when the user is near a pin
 * (e.g. panning over it or zoomed in close). The LocationDetailScreen
 * checks `getCachedLocationDetail` before calling the API.
 */

import type { FishingLocation } from '../types/models';

interface CacheEntry {
  location: FishingLocation;
  timestamp: number;
}

const CACHE_TTL_MS = 10 * 60 * 1000; // 10 minutes
const MAX_ENTRIES = 50;

const cache = new Map<string, CacheEntry>();

/** Store a location detail in cache. */
export function cacheLocationDetail(location: FishingLocation): void {
  // Evict oldest entries if over limit
  if (cache.size >= MAX_ENTRIES) {
    let oldestKey: string | null = null;
    let oldestTime = Infinity;
    for (const [key, entry] of cache) {
      if (entry.timestamp < oldestTime) {
        oldestTime = entry.timestamp;
        oldestKey = key;
      }
    }
    if (oldestKey) cache.delete(oldestKey);
  }
  cache.set(location.id, { location, timestamp: Date.now() });
}

/** Get a cached location detail (returns null if not cached or expired). */
export function getCachedLocationDetail(id: string): FishingLocation | null {
  const entry = cache.get(id);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(id);
    return null;
  }
  return entry.location;
}

/** Check if a location is in cache (even expired — for stale-while-revalidate). */
export function getStaleLocationDetail(id: string): FishingLocation | null {
  const entry = cache.get(id);
  return entry?.location ?? null;
}

/** Pre-fetch multiple location details into cache (fire and forget). */
export function prefetchLocationDetails(locations: FishingLocation[]): void {
  for (const loc of locations) {
    if (!cache.has(loc.id)) {
      cacheLocationDetail(loc);
    }
  }
}

/** Clear the cache. */
export function clearLocationDetailCache(): void {
  cache.clear();
}
