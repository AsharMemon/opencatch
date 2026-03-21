/**
 * OpenCatch — Location Data Cache
 *
 * Batches and caches data fetched for a single location so that multiple
 * consumers (SpotInsightsCard, bottom sheet, detail screen) don't each
 * independently call Open-Meteo, USGS, solunar, pressure, and species APIs.
 *
 * All data for a location is fetched in ONE call to `getLocationData()`,
 * cached for 5 minutes, and shared across the app.
 */

import { getDailyBiteForecast, type DailyBiteForecast } from './bestTimeWindows';
import { getCurrentPressure, type PressureReading } from './fishingPressure';
import { getWaterInsights, type WaterInsightsDashboard } from './waterInsights';
import { getSpeciesLikelihood, type SpeciesLikelihood } from './speciesDistribution';

// ── Types ────────────────────────────────────────────────────────────────────

export interface LocationData {
  lat: number;
  lon: number;
  bite: DailyBiteForecast;
  pressure: PressureReading;
  water: WaterInsightsDashboard | null;
  species: SpeciesLikelihood[];
  timestamp: number;
}

interface CacheEntry {
  data: LocationData;
  promise?: undefined;
}

interface PendingEntry {
  promise: Promise<LocationData>;
  data?: undefined;
}

type CacheSlot = CacheEntry | PendingEntry;

// ── Constants ────────────────────────────────────────────────────────────────

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const MAX_CACHE_SIZE = 30;

// ── Cache ────────────────────────────────────────────────────────────────────

const cache = new Map<string, CacheSlot>();

function cacheKey(lat: number, lon: number): string {
  return `${lat.toFixed(3)},${lon.toFixed(3)}`;
}

function evictIfNeeded(): void {
  if (cache.size <= MAX_CACHE_SIZE) return;
  const toRemove = cache.size - MAX_CACHE_SIZE;
  let removed = 0;
  for (const key of cache.keys()) {
    if (removed >= toRemove) break;
    cache.delete(key);
    removed++;
  }
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Fetch all location data in a single batched call.
 * If the same location is requested while a fetch is in-flight, the existing
 * promise is returned (request deduplication).
 */
export async function getLocationData(
  lat: number,
  lon: number,
  options?: {
    locationName?: string;
    skipSpecies?: boolean;
  },
): Promise<LocationData> {
  const key = cacheKey(lat, lon);

  // Check for valid cached data
  const existing = cache.get(key);
  if (existing) {
    if (existing.promise) {
      // In-flight request — deduplicate by returning the same promise
      return existing.promise;
    }
    if (Date.now() - existing.data.timestamp < CACHE_TTL_MS) {
      return existing.data;
    }
    // Expired — fetch fresh
    cache.delete(key);
  }

  // Create a new fetch promise
  const fetchPromise = (async (): Promise<LocationData> => {
    try {
      // Fetch all data in parallel
      const [bite, water] = await Promise.all([
        Promise.resolve(getDailyBiteForecast(lat, lon)),
        getWaterInsights(lat, lon, { locationName: options?.locationName }).catch(() => null),
      ]);

      // Synchronous computations
      const pressure = getCurrentPressure({ lat, lon });
      const species = options?.skipSpecies ? [] : getSpeciesLikelihood(lat, lon);

      const result: LocationData = {
        lat,
        lon,
        bite,
        pressure,
        water,
        species,
        timestamp: Date.now(),
      };

      // Replace the pending entry with a resolved one
      cache.set(key, { data: result });
      evictIfNeeded();

      return result;
    } catch (err) {
      // Remove the pending entry on failure so retries work
      cache.delete(key);
      throw err;
    }
  })();

  // Store the in-flight promise for deduplication
  cache.set(key, { promise: fetchPromise });

  return fetchPromise;
}

/**
 * Get cached data without triggering a fetch. Returns null if not cached.
 */
export function getCachedLocationData(lat: number, lon: number): LocationData | null {
  const key = cacheKey(lat, lon);
  const entry = cache.get(key);
  if (!entry || entry.promise) return null;
  if (Date.now() - entry.data.timestamp >= CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

/**
 * Clear all cached location data (e.g. on memory pressure).
 */
export function clearLocationDataCache(): void {
  cache.clear();
}
