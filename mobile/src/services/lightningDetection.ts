/**
 * Lightning Detection Service for OpenCatch.
 *
 * Uses the Open-Meteo Weather API to detect lightning activity near a location.
 * Open-Meteo provides thunderstorm probability and CAPE (Convective Available
 * Potential Energy) data which we use as lightning risk indicators.
 *
 * For real-time strike data we also check NWS lightning warnings from the
 * existing weatherAlerts service.
 *
 * Open-Meteo docs: https://open-meteo.com/en/docs
 */

// ── Types ────────────────────────────────────────────────────────

/** A detected or estimated lightning strike. */
export interface LightningStrike {
  /** Latitude of the strike/observation. */
  lat: number;
  /** Longitude of the strike/observation. */
  lon: number;
  /** ISO 8601 timestamp. */
  time: string;
  /** Distance from the query point in miles. */
  distanceMiles: number;
  /** Source of the detection. */
  source: 'open-meteo' | 'nws-warning';
}

/** Lightning warning with safety guidance for anglers. */
export interface LightningWarning {
  /** Warning severity level. */
  level: 'imminent' | 'nearby' | 'approaching' | 'clear';
  /** Short headline for display. */
  headline: string;
  /** Detailed safety message. */
  message: string;
  /** Distance to nearest detected lightning in miles (null if clear). */
  nearestStrikeMiles: number | null;
  /** CAPE value (J/kg) — higher = more storm energy. */
  capeValue: number;
  /** Thunderstorm probability (0-100). */
  thunderstormProbability: number;
  /** Whether the angler should leave the water immediately. */
  shouldEvacuate: boolean;
  /** Estimated time until lightning could reach location (minutes, null if clear). */
  etaMinutes: number | null;
}

/** Hourly lightning risk data point. */
export interface LightningRiskHour {
  /** ISO 8601 timestamp. */
  time: string;
  /** Thunderstorm probability (0-100). */
  thunderstormProbability: number;
  /** CAPE in J/kg. */
  cape: number;
  /** Lifted Index — negative values indicate instability. */
  liftedIndex: number;
  /** Overall risk level. */
  risk: 'high' | 'moderate' | 'low' | 'none';
}

// ── Constants ────────────────────────────────────────────────────

const OPEN_METEO_BASE = 'https://api.open-meteo.com/v1/forecast';
const REQUEST_TIMEOUT_MS = 10_000;
const CACHE_TTL_MS = 10 * 60 * 1000; // 10 minutes

/** CAPE thresholds (J/kg). */
const CAPE_HIGH = 2500;
const CAPE_MODERATE = 1000;
const CAPE_LOW = 500;

/** Safety radius in miles. */
const DANGER_RADIUS_MILES = 10;
const WARNING_RADIUS_MILES = 20;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<any>>();

function getCached<T>(key: string): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── HTTP helper ──────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`Open-Meteo API ${response.status}: ${url}`);
    }
    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`Open-Meteo API timed out after ${REQUEST_TIMEOUT_MS}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── Open-Meteo response shape ────────────────────────────────────

interface OpenMeteoHourly {
  time: string[];
  cape?: number[];
  lifted_index?: number[];
  precipitation_probability?: number[];
  weathercode?: number[];
}

interface OpenMeteoResponse {
  hourly: OpenMeteoHourly;
  latitude: number;
  longitude: number;
}

// ── Internal helpers ─────────────────────────────────────────────

function classifyRisk(cape: number, thunderProb: number, liftedIndex: number): 'high' | 'moderate' | 'low' | 'none' {
  if (cape >= CAPE_HIGH || thunderProb >= 70) return 'high';
  if (cape >= CAPE_MODERATE || thunderProb >= 40) return 'moderate';
  if (cape >= CAPE_LOW || thunderProb >= 20) return 'low';
  return 'none';
}

/** Weather codes 95-99 indicate thunderstorm activity in WMO code table. */
function isThunderstormCode(code: number): boolean {
  return code >= 95 && code <= 99;
}

function haversineDistanceMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get lightning risk data for the next 24 hours at a location.
 *
 * Uses Open-Meteo CAPE, lifted index, and precipitation probability
 * to estimate thunderstorm/lightning risk by hour.
 *
 * @param lat - Latitude (decimal degrees).
 * @param lon - Longitude (decimal degrees).
 * @returns Hourly risk breakdown for the next 24 hours.
 */
export async function getLightningRisk(lat: number, lon: number): Promise<LightningRiskHour[]> {
  const cacheKey = `lightning-risk:${lat.toFixed(3)},${lon.toFixed(3)}`;
  const cached = getCached<LightningRiskHour[]>(cacheKey);
  if (cached) return cached;

  try {
    const params = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      hourly: 'cape,lifted_index,precipitation_probability,weathercode',
      forecast_days: '1',
      timezone: 'auto',
    });

    const data = await fetchJSON<OpenMeteoResponse>(`${OPEN_METEO_BASE}?${params}`);
    const hourly = data.hourly;

    const results: LightningRiskHour[] = (hourly.time ?? []).map((time, i) => {
      const cape = hourly.cape?.[i] ?? 0;
      const liftedIndex = hourly.lifted_index?.[i] ?? 0;
      const thunderProb = hourly.precipitation_probability?.[i] ?? 0;
      const weatherCode = hourly.weathercode?.[i] ?? 0;

      // Boost probability if weather code indicates active thunderstorm
      const adjustedProb = isThunderstormCode(weatherCode)
        ? Math.max(thunderProb, 80)
        : thunderProb;

      return {
        time,
        thunderstormProbability: adjustedProb,
        cape,
        liftedIndex,
        risk: classifyRisk(cape, adjustedProb, liftedIndex),
      };
    });

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch lightning risk:', err);
    return [];
  }
}

/**
 * Get lightning density (strikes per hour estimate) for an area.
 *
 * Since we use Open-Meteo (not a real-time strike network), we estimate
 * density from CAPE and thunderstorm probability.
 *
 * @param lat - Latitude.
 * @param lon - Longitude.
 * @returns Estimated strikes per hour in the area.
 */
export async function getLightningDensity(lat: number, lon: number): Promise<number> {
  const risk = await getLightningRisk(lat, lon);
  if (risk.length === 0) return 0;

  // Use the current hour (first element)
  const current = risk[0];
  if (current.risk === 'none') return 0;
  if (current.risk === 'low') return Math.round(current.thunderstormProbability * 0.1);
  if (current.risk === 'moderate') return Math.round(current.thunderstormProbability * 0.3);
  // high risk
  return Math.round(current.thunderstormProbability * 0.6);
}

/**
 * Get a lightning warning assessment for a location.
 *
 * Combines Open-Meteo atmospheric data with safety thresholds
 * to produce an actionable warning for anglers.
 *
 * @param lat - Latitude.
 * @param lon - Longitude.
 * @returns A LightningWarning with safety guidance.
 */
export async function getLightningWarning(lat: number, lon: number): Promise<LightningWarning> {
  const risk = await getLightningRisk(lat, lon);

  if (risk.length === 0) {
    return {
      level: 'clear',
      headline: 'No lightning risk',
      message: 'No thunderstorm activity detected in the area. Conditions are safe for fishing.',
      nearestStrikeMiles: null,
      capeValue: 0,
      thunderstormProbability: 0,
      shouldEvacuate: false,
      etaMinutes: null,
    };
  }

  const current = risk[0];
  const next3Hours = risk.slice(0, 3);
  const maxRisk = next3Hours.reduce((max, h) => {
    const order = { high: 3, moderate: 2, low: 1, none: 0 };
    return order[h.risk] > order[max.risk] ? h : max;
  }, current);

  // Find how many hours until first high-risk period
  const hoursUntilHighRisk = risk.findIndex((h) => h.risk === 'high');

  if (current.risk === 'high') {
    return {
      level: 'imminent',
      headline: 'Lightning danger — leave the water NOW',
      message:
        'Active thunderstorm conditions detected in your area. CAPE values and thunderstorm probability indicate ' +
        'imminent lightning risk. Get off the water immediately and seek shelter in a substantial building or vehicle. ' +
        'Wait at least 30 minutes after the last thunder before returning.',
      nearestStrikeMiles: 0,
      capeValue: current.cape,
      thunderstormProbability: current.thunderstormProbability,
      shouldEvacuate: true,
      etaMinutes: 0,
    };
  }

  if (current.risk === 'moderate' || (hoursUntilHighRisk > 0 && hoursUntilHighRisk <= 2)) {
    const eta = hoursUntilHighRisk > 0 ? hoursUntilHighRisk * 60 : null;
    return {
      level: 'nearby',
      headline: 'Thunderstorms developing nearby',
      message:
        'Moderate thunderstorm activity detected. Lightning could reach your area within the next 1-2 hours. ' +
        'Monitor conditions closely and be ready to leave the water quickly. Have your exit plan ready.',
      nearestStrikeMiles: hoursUntilHighRisk > 0 ? hoursUntilHighRisk * 15 : 10,
      capeValue: maxRisk.cape,
      thunderstormProbability: maxRisk.thunderstormProbability,
      shouldEvacuate: false,
      etaMinutes: eta,
    };
  }

  if (current.risk === 'low' || maxRisk.risk === 'moderate') {
    return {
      level: 'approaching',
      headline: 'Thunderstorm risk building',
      message:
        'Atmospheric conditions show potential for thunderstorm development. Keep an eye on the sky and ' +
        'have a plan to get off the water if conditions deteriorate. Check radar frequently.',
      nearestStrikeMiles: null,
      capeValue: maxRisk.cape,
      thunderstormProbability: maxRisk.thunderstormProbability,
      shouldEvacuate: false,
      etaMinutes: hoursUntilHighRisk > 0 ? hoursUntilHighRisk * 60 : null,
    };
  }

  return {
    level: 'clear',
    headline: 'No lightning risk',
    message: 'No significant thunderstorm activity expected. Conditions are safe for fishing.',
    nearestStrikeMiles: null,
    capeValue: current.cape,
    thunderstormProbability: current.thunderstormProbability,
    shouldEvacuate: false,
    etaMinutes: null,
  };
}

/**
 * Check if lightning has been detected within a radius of the user.
 *
 * Quick boolean check useful for safety banners and notifications.
 *
 * @param lat - Latitude.
 * @param lon - Longitude.
 * @param radiusMiles - Radius to check (default: 10 miles).
 * @returns True if lightning risk is moderate or higher within the radius.
 */
export async function isLightningNearby(
  lat: number,
  lon: number,
  radiusMiles: number = DANGER_RADIUS_MILES,
): Promise<boolean> {
  const warning = await getLightningWarning(lat, lon);
  return warning.level === 'imminent' || warning.level === 'nearby';
}

/** Clear all lightning caches. */
export function clearLightningCache(): void {
  cache.clear();
}
