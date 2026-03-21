/**
 * National Weather Service (NWS) Alert Integration for OpenCatch.
 *
 * Fetches real-time weather alerts from the free NWS API and classifies
 * them by fishing relevance so the app can surface actionable safety info.
 *
 * NWS API docs: https://www.weather.gov/documentation/services-web-api
 * No API key required — just a descriptive User-Agent header.
 */

// ── Types ────────────────────────────────────────────────────────

/** Severity levels defined by the NWS Common Alerting Protocol (CAP). */
export type AlertSeverity = 'Extreme' | 'Severe' | 'Moderate' | 'Minor' | 'Unknown';

/** How a weather alert affects a fishing trip. */
export type FishingRelevance = 'dangerous' | 'caution' | 'advisory' | 'info';

/** Parsed weather alert from the NWS API. */
export interface WeatherAlert {
  /** NWS alert identifier (URN). */
  id: string;
  /** Event name, e.g. "Tornado Warning", "Small Craft Advisory". */
  event: string;
  /** CAP severity level. */
  severity: AlertSeverity;
  /** How certain the issuing office is: Observed, Likely, Possible, Unlikely, Unknown. */
  certainty: string;
  /** How quickly action should be taken: Immediate, Expected, Future, Unknown. */
  urgency: string;
  /** Short human-readable headline. */
  headline: string;
  /** Full alert description text. */
  description: string;
  /** Recommended protective actions (may be null). */
  instruction: string | null;
  /** ISO 8601 onset time. */
  onset: string;
  /** ISO 8601 expiration time. */
  expires: string;
  /** Name of the issuing NWS office. */
  senderName: string;
  /** Comma-separated description of affected areas. */
  areaDesc: string;
  /** NWS zone IDs affected by this alert. */
  affectedZones: string[];
}

/** A weather alert enriched with fishing-specific context. */
export interface FishingWeatherAlert extends WeatherAlert {
  /** How this alert affects fishing safety and planning. */
  fishingRelevance: FishingRelevance;
  /** Plain-language fishing-oriented summary. */
  fishingSummary: string;
}

// ── Constants ────────────────────────────────────────────────────

const NWS_BASE_URL = 'https://api.weather.gov';

const NWS_HEADERS: Record<string, string> = {
  'User-Agent': 'OpenCatch/1.0 (contact@opencatch.app)',
  Accept: 'application/geo+json',
};

/** Request timeout in milliseconds. */
const REQUEST_TIMEOUT_MS = 10_000;

/** Cache time-to-live: 5 minutes (alerts don't change faster than this). */
const CACHE_TTL_MS = 5 * 60 * 1000;

/** Marine-related event types we specifically surface. */
const MARINE_EVENT_TYPES = new Set([
  'Marine Weather Statement',
  'Small Craft Advisory',
  'Storm Warning',
  'Gale Warning',
  'Hurricane Warning',
  'Hurricane Force Wind Warning',
  'Special Marine Warning',
  'Hazardous Seas Warning',
  'Heavy Freezing Spray Warning',
  'Coastal Flood Warning',
  'Coastal Flood Watch',
  'Rip Current Statement',
  'Beach Hazards Statement',
]);

// ── Event → fishing relevance mapping ────────────────────────────

/**
 * Patterns matched against the alert event name (case-insensitive) to
 * determine fishing relevance.  Checked in order — first match wins.
 */
const RELEVANCE_RULES: Array<{ pattern: RegExp; relevance: FishingRelevance }> = [
  // Dangerous — get off the water immediately
  { pattern: /tornado/i, relevance: 'dangerous' },
  { pattern: /hurricane/i, relevance: 'dangerous' },
  { pattern: /typhoon/i, relevance: 'dangerous' },
  { pattern: /tsunami/i, relevance: 'dangerous' },
  { pattern: /lightning/i, relevance: 'dangerous' },
  { pattern: /extreme wind/i, relevance: 'dangerous' },
  { pattern: /storm surge/i, relevance: 'dangerous' },

  // Caution — consider postponing or staying close to shore
  { pattern: /severe thunderstorm/i, relevance: 'caution' },
  { pattern: /flash flood/i, relevance: 'caution' },
  { pattern: /high wind/i, relevance: 'caution' },
  { pattern: /gale/i, relevance: 'caution' },
  { pattern: /storm warning/i, relevance: 'caution' },
  { pattern: /small craft/i, relevance: 'caution' },
  { pattern: /special marine/i, relevance: 'caution' },
  { pattern: /hazardous seas/i, relevance: 'caution' },
  { pattern: /blizzard/i, relevance: 'caution' },
  { pattern: /ice storm/i, relevance: 'caution' },
  { pattern: /winter storm/i, relevance: 'caution' },
  { pattern: /flood warning/i, relevance: 'caution' },
  { pattern: /rip current/i, relevance: 'caution' },

  // Advisory — be aware, adjust plans if needed
  { pattern: /frost/i, relevance: 'advisory' },
  { pattern: /freeze/i, relevance: 'advisory' },
  { pattern: /heat/i, relevance: 'advisory' },
  { pattern: /excessive heat/i, relevance: 'advisory' },
  { pattern: /air quality/i, relevance: 'advisory' },
  { pattern: /dense fog/i, relevance: 'advisory' },
  { pattern: /wind advisory/i, relevance: 'advisory' },
  { pattern: /wind chill/i, relevance: 'advisory' },
  { pattern: /lake effect/i, relevance: 'advisory' },
  { pattern: /coastal flood/i, relevance: 'advisory' },
  { pattern: /beach hazard/i, relevance: 'advisory' },
];

// ── In-memory cache ──────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<WeatherAlert[]>>();

/**
 * Return cached data if it exists and hasn't expired, otherwise null.
 */
function getCached(key: string): WeatherAlert[] | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

/**
 * Store data in the cache.
 */
function setCache(key: string, data: WeatherAlert[]): void {
  cache.set(key, { data, timestamp: Date.now() });
}

/** Manually clear the entire alert cache (useful after location changes). */
export function clearAlertCache(): void {
  cache.clear();
}

// ── HTTP helper ──────────────────────────────────────────────────

/**
 * Make a GET request to the NWS API with timeout handling.
 *
 * @throws {Error} On network failure, timeout, or non-2xx response.
 */
async function nwsFetch<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: NWS_HEADERS,
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text().catch(() => '');
      throw new Error(
        `NWS API ${response.status}: ${url}${body ? ` — ${body.slice(0, 200)}` : ''}`,
      );
    }

    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`NWS API request timed out after ${REQUEST_TIMEOUT_MS}ms: ${url}`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── NWS response shapes (only the fields we use) ────────────────

interface NWSAlertFeature {
  id: string;
  properties: {
    id: string;
    event: string;
    severity: string;
    certainty: string;
    urgency: string;
    headline: string | null;
    description: string;
    instruction: string | null;
    onset: string;
    expires: string;
    senderName: string;
    areaDesc: string;
    affectedZones: string[];
  };
}

interface NWSAlertCollection {
  features: NWSAlertFeature[];
}

// ── Parsing ──────────────────────────────────────────────────────

/**
 * Parse a raw NWS alert feature into our WeatherAlert type.
 */
function parseAlert(feature: NWSAlertFeature): WeatherAlert {
  const p = feature.properties;

  const validSeverities: Set<string> = new Set([
    'Extreme',
    'Severe',
    'Moderate',
    'Minor',
    'Unknown',
  ]);

  return {
    id: p.id ?? feature.id,
    event: p.event ?? 'Unknown',
    severity: (validSeverities.has(p.severity) ? p.severity : 'Unknown') as AlertSeverity,
    certainty: p.certainty ?? 'Unknown',
    urgency: p.urgency ?? 'Unknown',
    headline: p.headline ?? p.event ?? 'Weather Alert',
    description: p.description ?? '',
    instruction: p.instruction ?? null,
    onset: p.onset ?? new Date().toISOString(),
    expires: p.expires ?? new Date().toISOString(),
    senderName: p.senderName ?? 'National Weather Service',
    areaDesc: p.areaDesc ?? '',
    affectedZones: p.affectedZones ?? [],
  };
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get all active weather alerts for a geographic point.
 *
 * @param lat - Latitude (decimal degrees, WGS84).
 * @param lon - Longitude (decimal degrees, WGS84).
 * @returns Array of active alerts, sorted by severity (most severe first).
 *
 * @example
 * ```ts
 * const alerts = await getActiveAlerts(35.22, -97.44);
 * console.log(alerts.length, 'active alerts near Norman, OK');
 * ```
 */
export async function getActiveAlerts(lat: number, lon: number): Promise<WeatherAlert[]> {
  const cacheKey = `point:${lat.toFixed(4)},${lon.toFixed(4)}`;
  const cached = getCached(cacheKey);
  if (cached) return cached;

  try {
    const data = await nwsFetch<NWSAlertCollection>(
      `${NWS_BASE_URL}/alerts/active?point=${lat},${lon}`,
    );
    const alerts = (data.features ?? []).map(parseAlert);
    const sorted = sortBySeverity(alerts);
    setCache(cacheKey, sorted);
    return sorted;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch NWS alerts for point:', err);
    return [];
  }
}

/**
 * Get all active weather alerts for a specific NWS forecast zone.
 *
 * @param zoneId - NWS zone identifier, e.g. "OKZ025" or "ANZ335".
 * @returns Array of active alerts for the zone.
 *
 * @example
 * ```ts
 * const alerts = await getAlertsByZone('FLZ050');
 * ```
 */
export async function getAlertsByZone(zoneId: string): Promise<WeatherAlert[]> {
  if (!zoneId || typeof zoneId !== 'string') {
    console.warn('[OpenCatch] Invalid zone ID provided to getAlertsByZone');
    return [];
  }

  const cacheKey = `zone:${zoneId}`;
  const cached = getCached(cacheKey);
  if (cached) return cached;

  try {
    const data = await nwsFetch<NWSAlertCollection>(
      `${NWS_BASE_URL}/alerts/active?zone=${encodeURIComponent(zoneId)}`,
    );
    const alerts = (data.features ?? []).map(parseAlert);
    const sorted = sortBySeverity(alerts);
    setCache(cacheKey, sorted);
    return sorted;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch NWS alerts for zone:', zoneId, err);
    return [];
  }
}

/**
 * Get marine-specific alerts near a location.
 *
 * Fetches all active alerts for the point and filters to marine event
 * types (Small Craft Advisory, Storm Warning, Gale Warning, etc.).
 * Useful for anglers on larger bodies of water or coastal areas.
 *
 * @param lat - Latitude (decimal degrees, WGS84).
 * @param lon - Longitude (decimal degrees, WGS84).
 * @returns Array of marine-related alerts only.
 *
 * @example
 * ```ts
 * const marine = await getMarineAlerts(27.76, -82.63);
 * if (marine.length > 0) {
 *   console.warn('Marine alert:', marine[0].headline);
 * }
 * ```
 */
export async function getMarineAlerts(lat: number, lon: number): Promise<WeatherAlert[]> {
  const allAlerts = await getActiveAlerts(lat, lon);
  return allAlerts.filter((alert) => MARINE_EVENT_TYPES.has(alert.event));
}

/**
 * Classify how a weather alert affects fishing safety and planning.
 *
 * @param alert - A parsed weather alert.
 * @returns The fishing relevance level.
 *
 * - `dangerous` — Lightning, tornado, hurricane. Do not go fishing.
 * - `caution`   — High wind, flash flood, severe thunderstorm. Consider postponing.
 * - `advisory`  — Frost, heat, air quality. Be prepared but fishing is feasible.
 * - `info`      — Everything else. Informational, no action required.
 *
 * @example
 * ```ts
 * const relevance = classifyFishingRelevance(alert);
 * if (relevance === 'dangerous') showFullScreenWarning(alert);
 * ```
 */
export function classifyFishingRelevance(alert: WeatherAlert): FishingRelevance {
  // Extreme severity is always dangerous regardless of event type
  if (alert.severity === 'Extreme') {
    return 'dangerous';
  }

  // Match against known patterns
  for (const rule of RELEVANCE_RULES) {
    if (rule.pattern.test(alert.event)) {
      return rule.relevance;
    }
  }

  // Fall back based on severity when the event name is unrecognized
  if (alert.severity === 'Severe') {
    return 'caution';
  }

  return 'info';
}

/**
 * Create a fishing-oriented summary for a weather alert.
 *
 * Returns a plain-language string that tells an angler what to do,
 * rather than just restating the alert.
 *
 * @param alert - A parsed weather alert.
 * @returns A `FishingWeatherAlert` with `fishingRelevance` and `fishingSummary` added.
 *
 * @example
 * ```ts
 * const enriched = formatAlertForFishing(alert);
 * // enriched.fishingSummary → "Severe Thunderstorm Warning until 4:30 PM.
 * //   Get off the water and seek shelter. Lightning is the #1 weather
 * //   danger for anglers."
 * ```
 */
export function formatAlertForFishing(alert: WeatherAlert): FishingWeatherAlert {
  const relevance = classifyFishingRelevance(alert);
  const expiresDate = new Date(alert.expires);
  const expiresStr = formatTime(expiresDate);

  let fishingSummary: string;

  switch (relevance) {
    case 'dangerous':
      fishingSummary =
        `${alert.event} until ${expiresStr}. ` +
        'Do not go out on the water. If you are already fishing, return to shore immediately and seek shelter. ' +
        'Lightning is the leading cause of weather-related fatalities for anglers.';
      break;

    case 'caution':
      fishingSummary =
        `${alert.event} until ${expiresStr}. ` +
        'Consider postponing your trip or staying close to shore. ' +
        buildCautionDetail(alert.event);
      break;

    case 'advisory':
      fishingSummary =
        `${alert.event} until ${expiresStr}. ` +
        'Fishing is still feasible but take precautions. ' +
        buildAdvisoryDetail(alert.event);
      break;

    default:
      fishingSummary =
        `${alert.event} until ${expiresStr}. ` +
        'No immediate impact on fishing safety expected. Check conditions before heading out.';
      break;
  }

  return {
    ...alert,
    fishingRelevance: relevance,
    fishingSummary,
  };
}

/**
 * Convenience: fetch alerts for a point and return them pre-classified
 * for fishing relevance, sorted by relevance (dangerous first).
 *
 * @param lat - Latitude.
 * @param lon - Longitude.
 * @returns Array of fishing-enriched alerts.
 */
export async function getFishingAlerts(
  lat: number,
  lon: number,
): Promise<FishingWeatherAlert[]> {
  const alerts = await getActiveAlerts(lat, lon);
  const enriched = alerts.map(formatAlertForFishing);

  // Sort: dangerous > caution > advisory > info
  const relevanceOrder: Record<FishingRelevance, number> = {
    dangerous: 0,
    caution: 1,
    advisory: 2,
    info: 3,
  };

  return enriched.sort(
    (a, b) => relevanceOrder[a.fishingRelevance] - relevanceOrder[b.fishingRelevance],
  );
}

// ── Internal helpers ─────────────────────────────────────────────

/** Sort alerts by severity: Extreme > Severe > Moderate > Minor > Unknown. */
function sortBySeverity(alerts: WeatherAlert[]): WeatherAlert[] {
  const order: Record<AlertSeverity, number> = {
    Extreme: 0,
    Severe: 1,
    Moderate: 2,
    Minor: 3,
    Unknown: 4,
  };
  return [...alerts].sort((a, b) => order[a.severity] - order[b.severity]);
}

/** Format a Date as a readable time string, e.g. "3:30 PM" or "Thu 3:30 PM". */
function formatTime(date: Date): string {
  const now = new Date();
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  const timeStr = date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });

  if (isToday) return timeStr;

  const dayStr = date.toLocaleDateString('en-US', { weekday: 'short' });
  return `${dayStr} ${timeStr}`;
}

/** Build additional context for caution-level alerts. */
function buildCautionDetail(event: string): string {
  const lower = event.toLowerCase();

  if (lower.includes('thunderstorm'))
    return 'Sudden lightning strikes are extremely dangerous on open water.';
  if (lower.includes('flash flood'))
    return 'Rising water levels can be dangerous in wading areas and near dams.';
  if (lower.includes('high wind') || lower.includes('gale'))
    return 'Strong winds create hazardous wave conditions, especially on large lakes.';
  if (lower.includes('small craft'))
    return 'Conditions are unsafe for small boats. Bank fishing may still be an option.';
  if (lower.includes('flood'))
    return 'Elevated water levels may affect access points and wade-fishing safety.';
  if (lower.includes('winter') || lower.includes('blizzard') || lower.includes('ice'))
    return 'Ice and cold conditions create hypothermia risk. Dress in layers and tell someone your plan.';

  return 'Monitor conditions closely and have an exit plan.';
}

/** Build additional context for advisory-level alerts. */
function buildAdvisoryDetail(event: string): string {
  const lower = event.toLowerCase();

  if (lower.includes('heat'))
    return 'Stay hydrated and fish during cooler morning or evening hours. Fish may also be deeper and less active.';
  if (lower.includes('frost') || lower.includes('freeze'))
    return 'Dress warmly. Cold-front fishing can actually be productive — bass often feed aggressively before a front.';
  if (lower.includes('fog'))
    return 'Use navigation lights if on a boat and reduce speed. Topwater fishing in fog can be excellent.';
  if (lower.includes('air quality'))
    return 'Limit prolonged outdoor exertion. Consider a shorter trip.';
  if (lower.includes('wind'))
    return 'Wind can concentrate baitfish on windblown banks — try fishing the windward side.';

  return 'Conditions are manageable with proper preparation.';
}
