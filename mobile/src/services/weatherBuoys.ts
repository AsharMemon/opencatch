/**
 * NDBC (National Data Buoy Center) Weather Buoy Service for OpenCatch.
 *
 * Fetches real-time observations from NOAA's network of weather buoys,
 * C-MAN stations, and ships. Data includes wind, waves, water temperature,
 * pressure, and visibility — all critical for fishing safety and planning.
 *
 * NDBC data is free and requires no API key.
 * Data docs: https://www.ndbc.noaa.gov/docs/ndbc_web_data_guide.pdf
 */

// ── Types ────────────────────────────────────────────────────────

/** An NDBC observation station (buoy, C-MAN, or ship). */
export interface BuoyStation {
  /** NDBC station identifier (e.g. "41009", "FWYF1"). */
  id: string;
  /** Human-readable station name. */
  name: string;
  /** Latitude in decimal degrees. */
  lat: number;
  /** Longitude in decimal degrees. */
  lon: number;
  /** Station platform type. */
  type: 'buoy' | 'cman' | 'ship';
  /** Station owner/operator (e.g. "NDBC", "NOS", "Environment Canada"). */
  owner: string;
}

/** A single real-time observation from an NDBC station. */
export interface BuoyObservation {
  /** NDBC station identifier that produced this reading. */
  stationId: string;
  /** ISO-8601 UTC timestamp of the observation. */
  timestamp: string;
  /** Sustained wind speed in knots, or null if unavailable. */
  windSpeed: number | null;
  /** Wind direction in compass degrees (0-360), or null if unavailable. */
  windDirection: number | null;
  /** Wind gust speed in knots, or null if unavailable. */
  gustSpeed: number | null;
  /** Significant wave height in feet, or null if unavailable. */
  waveHeight: number | null;
  /** Dominant wave period in seconds, or null if unavailable. */
  wavePeriod: number | null;
  /** Sea surface water temperature in degrees Fahrenheit, or null if unavailable. */
  waterTemp: number | null;
  /** Air temperature in degrees Fahrenheit, or null if unavailable. */
  airTemp: number | null;
  /** Atmospheric pressure in hectopascals (hPa), or null if unavailable. */
  pressure: number | null;
  /** Visibility in nautical miles, or null if unavailable. */
  visibility: number | null;
  /** Dewpoint temperature in degrees Fahrenheit, or null if unavailable. */
  dewpoint: number | null;
  /** Sea surface salinity in PSU, or null if unavailable. */
  salinity: number | null;
}

/** A nearby buoy station with distance and latest observation data. */
export interface NearbyBuoy extends BuoyStation {
  /** Distance from the queried coordinates, in statute miles. */
  distanceMiles: number;
  /** Latest observation from this station, or null if fetch failed. */
  latestObs: BuoyObservation | null;
}

// ── Constants ────────────────────────────────────────────────────

const NDBC_BASE_URL = 'https://www.ndbc.noaa.gov';

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';

/** Request timeout in milliseconds. */
const REQUEST_TIMEOUT_MS = 15_000;

/** Cache TTL for the station list: 24 hours. */
const STATION_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

/** Cache TTL for individual buoy observations: 15 minutes. */
const OBSERVATION_CACHE_TTL_MS = 15 * 60 * 1000;

/** Earth radius in statute miles (for haversine). */
const EARTH_RADIUS_MILES = 3958.8;

/**
 * NDBC realtime2 column indices (0-based).
 * Header: #YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
 */
const COL = {
  YY: 0,
  MM: 1,
  DD: 2,
  hh: 3,
  mm: 4,
  WDIR: 5,
  WSPD: 6,
  GST: 7,
  WVHT: 8,
  DPD: 9,
  APD: 10,
  MWD: 11,
  PRES: 12,
  ATMP: 13,
  WTMP: 14,
  DEWP: 15,
  VIS: 16,
  PTDY: 17,
  TIDE: 18,
} as const;

// ── In-memory cache ──────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const stationCache: { entry: CacheEntry<BuoyStation[]> | null } = { entry: null };
const observationCache = new Map<string, CacheEntry<BuoyObservation>>();

function getCachedStations(): BuoyStation[] | null {
  if (!stationCache.entry) return null;
  if (Date.now() - stationCache.entry.timestamp > STATION_CACHE_TTL_MS) {
    stationCache.entry = null;
    return null;
  }
  return stationCache.entry.data;
}

function setCachedStations(data: BuoyStation[]): void {
  stationCache.entry = { data, timestamp: Date.now() };
}

function getCachedObservation(stationId: string): BuoyObservation | null {
  const entry = observationCache.get(stationId);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > OBSERVATION_CACHE_TTL_MS) {
    observationCache.delete(stationId);
    return null;
  }
  return entry.data;
}

function setCachedObservation(stationId: string, data: BuoyObservation): void {
  observationCache.set(stationId, { data, timestamp: Date.now() });
}

/** Manually clear all caches (useful after extended offline periods). */
export function clearBuoyCache(): void {
  stationCache.entry = null;
  observationCache.clear();
}

// ── HTTP helper ──────────────────────────────────────────────────

async function fetchText(url: string): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: { 'User-Agent': USER_AGENT },
    });

    if (!response.ok) {
      throw new Error(`NDBC request failed: ${response.status} ${response.statusText}`);
    }

    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}

// ── Parsing helpers ──────────────────────────────────────────────

/**
 * Parse an NDBC value, returning null for missing data markers.
 *
 * NDBC uses "MM", "99.0", "99.00", "999", "999.0", "9999.0", and "99.000"
 * to indicate unavailable readings.
 */
function parseNdbcValue(raw: string): number | null {
  if (!raw || raw === 'MM') return null;
  const num = parseFloat(raw);
  if (isNaN(num)) return null;
  if (
    raw === '99.0' ||
    raw === '99.00' ||
    raw === '999' ||
    raw === '999.0' ||
    raw === '9999.0' ||
    raw === '99.000'
  ) {
    return null;
  }
  return num;
}

/** Convert Celsius to Fahrenheit, rounded to one decimal. */
function cToF(celsius: number | null): number | null {
  if (celsius === null) return null;
  return Math.round(((celsius * 9) / 5 + 32) * 10) / 10;
}

/** Convert meters/second to knots, rounded to one decimal. */
function msToKnots(ms: number | null): number | null {
  if (ms === null) return null;
  return Math.round(ms * 1.94384 * 10) / 10;
}

/** Convert meters to feet, rounded to one decimal. */
function mToFt(meters: number | null): number | null {
  if (meters === null) return null;
  return Math.round(meters * 3.28084 * 10) / 10;
}

/**
 * Determine station type from its NDBC identifier.
 *
 * Buoy IDs are typically numeric (e.g. "41009"); C-MAN stations
 * have letters followed by digits (e.g. "FWYF1"); ships use call signs.
 */
function inferStationType(id: string): 'buoy' | 'cman' | 'ship' {
  if (/^\d+$/.test(id)) return 'buoy';
  if (/^[A-Z]{3,5}\d{1,2}$/i.test(id)) return 'cman';
  return 'ship';
}

// ── Haversine distance ───────────────────────────────────────────

/**
 * Calculate the great-circle distance between two coordinates
 * using the haversine formula.
 *
 * @returns Distance in statute miles.
 */
function haversineDistanceMiles(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

  return EARTH_RADIUS_MILES * c;
}

// ── Station list parsing ─────────────────────────────────────────

/**
 * Parse the NDBC active stations XML into a typed station array.
 *
 * The XML format has <station> elements with attributes like:
 *   <station id="41009" lat="28.508" lon="-80.166" name="..." owner="..." type="buoy" />
 */
function parseStationsXml(xml: string): BuoyStation[] {
  const stations: BuoyStation[] = [];
  const stationRegex = /<station\s+([^>]+)\/?\s*>/gi;

  const getAttr = (attrs: string, name: string): string => {
    const m = attrs.match(new RegExp(`\\b${name}=["']([^"']*)["']`));
    return m ? m[1] : '';
  };

  let match: RegExpExecArray | null;
  while ((match = stationRegex.exec(xml)) !== null) {
    const attrs = match[1];

    // id attribute needs special handling (common attribute name)
    const idMatch = attrs.match(/\bid=["']([^"']+)["']/);
    const id = idMatch ? idMatch[1] : null;
    if (!id) continue;

    const lat = parseFloat(getAttr(attrs, 'lat'));
    const lon = parseFloat(getAttr(attrs, 'lon'));
    if (isNaN(lat) || isNaN(lon)) continue;

    const name = getAttr(attrs, 'name') || id;
    const owner = getAttr(attrs, 'owner') || 'NDBC';
    const rawType = getAttr(attrs, 'type')?.toLowerCase() || '';

    let stationType: 'buoy' | 'cman' | 'ship';
    if (rawType.includes('buoy')) {
      stationType = 'buoy';
    } else if (rawType.includes('cman') || rawType.includes('c-man')) {
      stationType = 'cman';
    } else if (rawType.includes('ship')) {
      stationType = 'ship';
    } else {
      stationType = inferStationType(id);
    }

    stations.push({ id, name, lat, lon, type: stationType, owner });
  }

  return stations;
}

// ── Observation parsing ──────────────────────────────────────────

/**
 * Parse the NDBC realtime2 fixed-width text format into a
 * {@link BuoyObservation}.
 *
 * The file structure is:
 * - Line 1: column headers  (#YY MM DD hh mm WDIR WSPD ...)
 * - Line 2: units           (#yr mo dy hr mn degT m/s  ...)
 * - Line 3+: data rows      (latest observation first)
 *
 * We only parse the latest row (line 3).
 */
function parseRealtimeText(
  text: string,
  stationId: string,
): BuoyObservation | null {
  const lines = text.trim().split('\n');

  // Need at least 2 header lines + 1 data row
  if (lines.length < 3) return null;

  // Find the first non-header data line
  let dataLine: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line && !line.startsWith('#')) {
      dataLine = line;
      break;
    }
  }

  if (!dataLine) return null;

  const cols = dataLine.split(/\s+/);
  if (cols.length < 15) return null;

  // Build UTC timestamp from YY MM DD hh mm
  const year = parseInt(cols[COL.YY], 10);
  const month = parseInt(cols[COL.MM], 10);
  const day = parseInt(cols[COL.DD], 10);
  const hour = parseInt(cols[COL.hh], 10);
  const minute = parseInt(cols[COL.mm], 10);

  if (isNaN(year) || isNaN(month) || isNaN(day)) return null;

  const fullYear = year < 100 ? 2000 + year : year;
  const timestamp = new Date(
    Date.UTC(fullYear, month - 1, day, hour, minute),
  ).toISOString();

  // NDBC reports wind in m/s and temps in Celsius — convert
  const windSpeedMs = parseNdbcValue(cols[COL.WSPD]);
  const gustSpeedMs = parseNdbcValue(cols[COL.GST]);
  const waveHeightM = parseNdbcValue(cols[COL.WVHT]);
  const airTempC = parseNdbcValue(cols[COL.ATMP]);
  const waterTempC = parseNdbcValue(cols[COL.WTMP]);
  const dewpointC = cols.length > COL.DEWP ? parseNdbcValue(cols[COL.DEWP]) : null;
  const vis = cols.length > COL.VIS ? parseNdbcValue(cols[COL.VIS]) : null;

  return {
    stationId,
    timestamp,
    windSpeed: msToKnots(windSpeedMs),
    windDirection: parseNdbcValue(cols[COL.WDIR]),
    gustSpeed: msToKnots(gustSpeedMs),
    waveHeight: mToFt(waveHeightM),
    wavePeriod: parseNdbcValue(cols[COL.DPD]),
    waterTemp: cToF(waterTempC),
    airTemp: cToF(airTempC),
    pressure: parseNdbcValue(cols[COL.PRES]),
    visibility: vis,
    dewpoint: cToF(dewpointC),
    salinity: null, // Salinity is not in the standard realtime2 files
  };
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch the list of all active NDBC stations.
 *
 * Parses the NDBC active stations XML feed and caches the result
 * for 24 hours. Returns an empty array on network failure.
 *
 * @returns Array of {@link BuoyStation} with id, name, coordinates, type, and owner.
 *
 * @example
 * ```ts
 * const stations = await fetchActiveStations();
 * console.log(`Found ${stations.length} active NDBC stations`);
 * const buoysOnly = stations.filter(s => s.type === 'buoy');
 * ```
 */
export async function fetchActiveStations(): Promise<BuoyStation[]> {
  const cached = getCachedStations();
  if (cached) return cached;

  try {
    const xml = await fetchText(`${NDBC_BASE_URL}/activestations.xml`);
    const stations = parseStationsXml(xml);
    if (stations.length > 0) {
      setCachedStations(stations);
    }
    return stations;
  } catch (err) {
    console.warn('[weatherBuoys] Failed to fetch active stations:', err);
    return [];
  }
}

/**
 * Fetch the latest observation from a specific NDBC station.
 *
 * Parses the realtime2 fixed-width text format. Results are cached
 * for 15 minutes. Returns null if the station has no data or the
 * request fails.
 *
 * @param stationId - NDBC station identifier (e.g. "41009", "FWYF1").
 * @returns The latest {@link BuoyObservation}, or null on failure.
 *
 * @example
 * ```ts
 * const obs = await fetchBuoyObservation('41009');
 * if (obs) {
 *   console.log(`Water temp: ${obs.waterTemp}°F, Waves: ${obs.waveHeight} ft`);
 * }
 * ```
 */
export async function fetchBuoyObservation(
  stationId: string,
): Promise<BuoyObservation | null> {
  const normalizedId = stationId.toUpperCase();

  const cached = getCachedObservation(normalizedId);
  if (cached) return cached;

  try {
    const text = await fetchText(
      `${NDBC_BASE_URL}/data/realtime2/${normalizedId}.txt`,
    );
    const observation = parseRealtimeText(text, normalizedId);
    if (observation) {
      setCachedObservation(normalizedId, observation);
    }
    return observation;
  } catch (err) {
    console.warn(
      `[weatherBuoys] Failed to fetch observation for ${normalizedId}:`,
      err,
    );
    return null;
  }
}

/**
 * Find NDBC buoy stations near a given coordinate, sorted by distance.
 *
 * Fetches the active station list (cached 24h), filters to those within
 * the specified radius, and sorts closest-first. Observations are NOT
 * fetched — use {@link fetchBuoyObservation} or {@link getBuoyWithObservation}
 * to get readings for individual stations.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @param radiusMiles - Search radius in statute miles. Defaults to 50.
 * @returns Array of {@link NearbyBuoy} sorted by distance (closest first).
 *          The `latestObs` field is null until populated separately.
 *
 * @example
 * ```ts
 * const nearby = await getNearbyBuoys(28.5, -80.6, 75);
 * console.log(`${nearby.length} buoys within 75 miles`);
 * nearby.forEach(b => console.log(`${b.name}: ${b.distanceMiles} mi`));
 * ```
 */
export async function getNearbyBuoys(
  lat: number,
  lon: number,
  radiusMiles: number = 50,
): Promise<NearbyBuoy[]> {
  const stations = await fetchActiveStations();

  const nearby: NearbyBuoy[] = [];

  for (const station of stations) {
    const distance = haversineDistanceMiles(lat, lon, station.lat, station.lon);
    if (distance <= radiusMiles) {
      nearby.push({
        ...station,
        distanceMiles: Math.round(distance * 10) / 10,
        latestObs: null,
      });
    }
  }

  nearby.sort((a, b) => a.distanceMiles - b.distanceMiles);

  return nearby;
}

/**
 * Fetch a station's metadata and latest observation in one call.
 *
 * Looks up the station in the active stations list and fetches the
 * latest realtime2 observation in parallel. Returns a {@link NearbyBuoy}
 * with `distanceMiles` set to 0 (no reference point provided).
 *
 * @param stationId - NDBC station identifier (e.g. "41009").
 * @returns A {@link NearbyBuoy} with observation data, or null if the
 *          station cannot be found and has no observation data.
 *
 * @example
 * ```ts
 * const buoy = await getBuoyWithObservation('41009');
 * if (buoy?.latestObs) {
 *   console.log(`${buoy.name}: ${buoy.latestObs.waterTemp}°F water`);
 * }
 * ```
 */
export async function getBuoyWithObservation(
  stationId: string,
): Promise<NearbyBuoy | null> {
  const normalizedId = stationId.toUpperCase();

  const [stations, observation] = await Promise.all([
    fetchActiveStations(),
    fetchBuoyObservation(normalizedId),
  ]);

  const station = stations.find((s) => s.id.toUpperCase() === normalizedId);

  if (!station) {
    // Station not in active list — still return obs if we got one
    if (!observation) return null;

    return {
      id: normalizedId,
      name: normalizedId,
      lat: 0,
      lon: 0,
      type: inferStationType(normalizedId),
      owner: 'Unknown',
      distanceMiles: 0,
      latestObs: observation,
    };
  }

  return {
    ...station,
    distanceMiles: 0,
    latestObs: observation,
  };
}

/**
 * Format a wave height in feet into a human-readable sea state description.
 *
 * Uses a simplified Beaufort-like scale tuned for fishing relevance:
 * - Calm: < 1 ft
 * - Slight: 1-3 ft
 * - Moderate: 3-5 ft
 * - Rough: 5-8 ft
 * - Very Rough: 8+ ft
 *
 * @param feet - Significant wave height in feet, or null/undefined.
 * @returns Descriptive string with the wave height range, or "Unknown" if null.
 *
 * @example
 * ```ts
 * formatWaveHeight(2.5);  // "Slight (1-3 ft)"
 * formatWaveHeight(6.1);  // "Rough (5-8 ft)"
 * formatWaveHeight(0.4);  // "Calm (< 1 ft)"
 * formatWaveHeight(null); // "Unknown"
 * ```
 */
export function formatWaveHeight(feet: number | null | undefined): string {
  if (feet === null || feet === undefined) return 'Unknown';
  if (feet < 1) return 'Calm (< 1 ft)';
  if (feet < 3) return 'Slight (1-3 ft)';
  if (feet < 5) return 'Moderate (3-5 ft)';
  if (feet < 8) return 'Rough (5-8 ft)';
  return 'Very Rough (8+ ft)';
}

// ── Bundled service object ───────────────────────────────────────

/**
 * NDBC weather buoy service for OpenCatch.
 *
 * All methods are available both as named exports and as properties
 * on this bundled service object.
 *
 * @example
 * ```ts
 * import { weatherBuoyService } from '../services/weatherBuoys';
 *
 * const nearby = await weatherBuoyService.getNearbyBuoys(28.5, -80.6);
 * for (const buoy of nearby.slice(0, 5)) {
 *   const obs = await weatherBuoyService.fetchBuoyObservation(buoy.id);
 *   if (obs?.waveHeight !== null) {
 *     console.log(`${buoy.name}: ${weatherBuoyService.formatWaveHeight(obs.waveHeight)}`);
 *   }
 * }
 * ```
 */
export const weatherBuoyService = {
  fetchActiveStations,
  fetchBuoyObservation,
  getNearbyBuoys,
  getBuoyWithObservation,
  formatWaveHeight,
  clearBuoyCache,
} as const;
