/**
 * Weather Alert Integration for OpenCatch (US + Canada).
 *
 * US:     National Weather Service (NWS) API — free, no key required.
 * Canada: Environment Canada CAP alerts via Geomet OGC-API — free, no key required.
 *
 * Auto-detects country based on latitude and routes to the correct source.
 * Both sources are merged into a unified WeatherAlert type.
 *
 * NWS API docs: https://www.weather.gov/documentation/services-web-api
 * EC CAP alerts: https://dd.weather.gc.ca/alerts/cap/
 * EC Geomet:     https://api.weather.gc.ca
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
const EC_GEOMET_BASE = 'https://api.weather.gc.ca';

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
  if (isLikelyCanada(lat, lon)) {
    return [];
  }
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

// ── Country detection ────────────────────────────────────────────

/**
 * Determine if coordinates are in Canada (rough bounding box).
 * Canada spans roughly 41.7N-84N latitude, -141 to -52 longitude.
 * @internal
 */
function isCanadianLocation(lat: number, lon: number): boolean {
  return lat >= 41.7 && lat <= 84 && lon >= -141 && lon <= -52;
}

/**
 * More precise check: is the point north of the US-Canada border?
 * Uses the 49th parallel for western provinces, and approximate
 * boundaries for eastern provinces and Great Lakes region.
 * @internal
 */
function isLikelyCanada(lat: number, lon: number): boolean {
  // West of Ontario: 49th parallel is the border
  if (lon <= -95 && lat >= 49) return true;
  // Ontario/Great Lakes region: border dips to ~42N
  if (lon > -95 && lon <= -74 && lat >= 42) return true;
  // Quebec/Maritimes: border is roughly at ~45-47N
  if (lon > -74 && lon <= -52 && lat >= 45) return true;
  return false;
}

// ── Environment Canada (EC) Alert Fetching ───────────────────────

/**
 * Fetch JSON from the EC Geomet OGC-API with timeout.
 * @internal
 */
async function ecFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${EC_GEOMET_BASE}${path}`);
  url.searchParams.set('f', 'json');
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url.toString(), { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`EC Geomet API ${response.status}: ${path}`);
    }
    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`EC Geomet API timed out after ${REQUEST_TIMEOUT_MS}ms: ${path}`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Map EC alert_type to our AlertSeverity.
 *
 * EC Geomet uses "alert_type" with values: "warning", "watch", "advisory",
 * "statement", "ended". We map these to CAP severity levels.
 * @internal
 */
const EC_TYPE_TO_SEVERITY: Record<string, AlertSeverity> = {
  warning: 'Severe',
  watch: 'Moderate',
  advisory: 'Moderate',
  statement: 'Minor',
  ended: 'Minor',
};

/**
 * Parse an Environment Canada alert feature into our WeatherAlert type.
 *
 * EC Geomet weather-alerts collection uses these property names:
 *   id, alert_code, alert_type, alert_name_en, alert_short_name_en,
 *   publication_datetime, expiration_datetime, validity_datetime,
 *   event_end_datetime, alert_text_en, feature_name_en, province,
 *   status_en, feature_id.
 *
 * @internal
 */
function parseECAlert(feature: any): WeatherAlert {
  const p = feature.properties || {};

  const alertType = (p.alert_type ?? '').toLowerCase();
  const severity = EC_TYPE_TO_SEVERITY[alertType] ?? 'Unknown';

  // Build a headline from the alert name and area
  const alertName = p.alert_name_en ?? p.alert_short_name_en ?? 'Weather Alert';
  const areaName = p.feature_name_en ?? '';
  const headline = areaName ? `${alertName} for ${areaName}` : alertName;

  return {
    id: p.id ?? feature.id ?? '',
    event: alertName,
    severity,
    certainty: 'Unknown',  // EC Geomet does not provide CAP certainty
    urgency: alertType === 'warning' ? 'Immediate' : alertType === 'watch' ? 'Expected' : 'Unknown',
    headline,
    description: p.alert_text_en ?? '',
    instruction: null,  // EC Geomet does not separate instructions from description
    onset: p.publication_datetime ?? p.validity_datetime ?? new Date().toISOString(),
    expires: p.expiration_datetime ?? p.event_end_datetime ?? new Date().toISOString(),
    senderName: 'Environment Canada',
    areaDesc: areaName,
    affectedZones: p.province ? [p.province] : [],
  };
}

/**
 * Get active Environment Canada weather alerts near a location.
 *
 * Uses the Geomet OGC-API to fetch CAP alerts within a bounding box
 * around the given coordinates. Results are cached for 5 minutes.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns Array of active alerts, sorted by severity (most severe first).
 *
 * @example
 * ```ts
 * const alerts = await getCanadianAlerts(43.65, -79.38);
 * console.log(alerts.length, 'active alerts near Toronto');
 * ```
 */
export async function getCanadianAlerts(lat: number, lon: number): Promise<WeatherAlert[]> {
  const cacheKey = `ca-alerts:${lat.toFixed(4)},${lon.toFixed(4)}`;
  const cached = getCached(cacheKey);
  if (cached) return cached;

  try {
    // Build a ~50 km bounding box
    const dLat = 50 / 111;
    const dLon = 50 / (111 * Math.cos((lat * Math.PI) / 180));
    const bbox = [lon - dLon, lat - dLat, lon + dLon, lat + dLat].join(',');

    const data = await ecFetch<any>('/collections/weather-alerts/items', {
      bbox,
      limit: '50',
    });

    const alerts: WeatherAlert[] = (data.features ?? []).map(parseECAlert);
    const sorted = sortBySeverity(alerts);
    setCache(cacheKey, sorted);
    return sorted;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch Environment Canada alerts:', err);
    return [];
  }
}

// ── Unified alert API (auto-detect country) ──────────────────────

/**
 * Get weather alerts for any North American location, auto-detecting country.
 *
 * Routes to Environment Canada (EC) for Canadian coordinates and to the
 * National Weather Service (NWS) for US coordinates. Results are merged
 * into the same WeatherAlert type.
 *
 * For locations near the border, alerts from both sources are fetched
 * and merged to ensure nothing is missed.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns Merged array of active alerts, sorted by severity.
 *
 * @example
 * ```ts
 * // Works for both countries:
 * const alerts = await getUnifiedAlerts(43.65, -79.38); // Toronto
 * const alerts2 = await getUnifiedAlerts(35.22, -97.44); // Oklahoma
 * ```
 */
export async function getUnifiedAlerts(lat: number, lon: number): Promise<WeatherAlert[]> {
  const canada = isLikelyCanada(lat, lon);
  const nearBorder = isNearBorder(lat, lon);

  if (nearBorder) {
    // Fetch from both sources and merge
    const [usAlerts, caAlerts] = await Promise.all([
      getActiveAlerts(lat, lon).catch(() => [] as WeatherAlert[]),
      getCanadianAlerts(lat, lon).catch(() => [] as WeatherAlert[]),
    ]);
    return sortBySeverity(deduplicateAlerts([...usAlerts, ...caAlerts]));
  }

  if (canada) {
    return getCanadianAlerts(lat, lon);
  }

  return getActiveAlerts(lat, lon);
}

/**
 * Get fishing-classified alerts for any North American location.
 *
 * Auto-detects country, fetches alerts, and enriches each with
 * fishing-specific relevance and summary text.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns Array of fishing-enriched alerts, sorted by relevance.
 */
export async function getUnifiedFishingAlerts(
  lat: number,
  lon: number,
): Promise<FishingWeatherAlert[]> {
  const alerts = await getUnifiedAlerts(lat, lon);
  const enriched = alerts.map(formatAlertForFishing);

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

/**
 * Check if coordinates are near the US-Canada border (within ~50 km).
 * @internal
 */
function isNearBorder(lat: number, lon: number): boolean {
  // Western provinces: 49th parallel
  if (lon <= -95 && Math.abs(lat - 49) < 0.5) return true;
  // Great Lakes: border roughly at ~42-49N depending on location
  if (lon > -95 && lon <= -74 && lat >= 41.5 && lat <= 49.5) {
    // Only flag as near-border if within a narrow band
    if (Math.abs(lat - 42) < 0.5 || Math.abs(lat - 49) < 0.5) return true;
    // Niagara/St. Lawrence region
    if (lon > -80 && lon <= -74 && Math.abs(lat - 44) < 1) return true;
  }
  // Maritimes: border around 45-47N
  if (lon > -74 && lon <= -52 && Math.abs(lat - 46) < 1) return true;
  return false;
}

/**
 * Remove duplicate alerts when merging US and Canadian sources.
 * Uses alert headline and onset time for deduplication.
 * @internal
 */
function deduplicateAlerts(alerts: WeatherAlert[]): WeatherAlert[] {
  const seen = new Set<string>();
  return alerts.filter((alert) => {
    const key = `${alert.headline}|${alert.onset}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
