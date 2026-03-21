/**
 * Nautical Navigation Service for OpenCatch.
 *
 * Provides bearing, distance, and ETA calculations in nautical units,
 * plus nearest port/marina and nearest USCG station lookups via
 * OpenStreetMap Overpass API.
 *
 * All calculations use the WGS-84 ellipsoid via haversine formula
 * (sufficient accuracy for recreational navigation).
 */

// ── Types ────────────────────────────────────────────────────────

/** A geographic coordinate. */
export interface NavCoord {
  lat: number;
  lon: number;
}

/** Result of a navigation calculation (bearing, distance, ETA). */
export interface NavCalculation {
  /** True bearing from origin to destination in degrees (0-360). */
  bearingDeg: number;
  /** Compass bearing label (e.g. "NNE", "SW"). */
  bearingLabel: string;
  /** Distance in nautical miles. */
  distanceNm: number;
  /** Distance in statute miles. */
  distanceMi: number;
  /** Estimated time of arrival as ISO-8601 string, if speed provided. */
  eta: string | null;
  /** Estimated travel time in minutes, if speed provided. */
  travelTimeMin: number | null;
  /** Human-readable travel time (e.g. "2h 15m"), if speed provided. */
  travelTimeLabel: string | null;
}

/** A port, marina, or harbor. */
export interface Port {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Port type classification. */
  type: 'marina' | 'harbor' | 'port' | 'yacht_club' | 'boat_ramp';
  /** Phone number if available. */
  phone?: string;
  /** Website URL if available. */
  website?: string;
  /** VHF channel for port operations. */
  vhfChannel?: string;
  /** Distance from the queried position in nautical miles. */
  distanceNm: number;
  /** True bearing from the queried position in degrees. */
  bearingDeg: number;
}

/** A US Coast Guard station. */
export interface CoastGuardStation {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Phone number if available. */
  phone?: string;
  /** VHF channel (typically 16 for emergencies). */
  vhfChannel: string;
  /** Distance from the queried position in nautical miles. */
  distanceNm: number;
  /** True bearing from the queried position in degrees. */
  bearingDeg: number;
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 20_000;

/** Cache TTL: 6 hours for port/USCG data. */
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;

/** Earth radius in nautical miles. */
const EARTH_RADIUS_NM = 3440.065;

/** Earth radius in statute miles. */
const EARTH_RADIUS_MI = 3958.8;

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
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── Fetch helper ─────────────────────────────────────────────────

async function overpassQuery<T>(query: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(OVERPASS_URL, {
      method: 'POST',
      body: `data=${encodeURIComponent(query)}`,
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': USER_AGENT,
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: Overpass`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── Math helpers ─────────────────────────────────────────────────

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

function toDeg(rad: number): number {
  return (rad * 180) / Math.PI;
}

/**
 * Haversine distance between two points.
 * @param radiusUnit — Earth radius in desired units.
 */
function haversine(from: NavCoord, to: NavCoord, radiusUnit: number): number {
  const dLat = toRad(to.lat - from.lat);
  const dLon = toRad(to.lon - from.lon);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(from.lat)) * Math.cos(toRad(to.lat)) * Math.sin(dLon / 2) ** 2;
  return radiusUnit * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Compass label ────────────────────────────────────────────────

const COMPASS_POINTS = [
  'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
];

function compassLabel(deg: number): string {
  const idx = Math.round(((deg % 360) + 360) % 360 / 22.5) % 16;
  return COMPASS_POINTS[idx];
}

// ── Public Navigation Calculations ───────────────────────────────

/**
 * Calculate true bearing from one point to another.
 * Returns degrees (0-360), where 0 = north, 90 = east.
 */
export function calculateBearing(from: NavCoord, to: NavCoord): number {
  const dLon = toRad(to.lon - from.lon);
  const lat1 = toRad(from.lat);
  const lat2 = toRad(to.lat);

  const y = Math.sin(dLon) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);

  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/**
 * Calculate distance between two points in nautical miles.
 */
export function calculateDistance(from: NavCoord, to: NavCoord): number {
  return haversine(from, to, EARTH_RADIUS_NM);
}

/**
 * Calculate ETA given a starting point, destination, and speed in knots.
 * Returns an ISO-8601 datetime string.
 */
export function calculateETA(
  from: NavCoord,
  to: NavCoord,
  speedKnots: number,
): { eta: string; travelTimeMin: number; travelTimeLabel: string } | null {
  if (speedKnots <= 0) return null;

  const distNm = calculateDistance(from, to);
  const hours = distNm / speedKnots;
  const totalMinutes = Math.round(hours * 60);

  const eta = new Date(Date.now() + hours * 3600 * 1000).toISOString();
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  const label = h > 0 ? `${h}h ${m}m` : `${m}m`;

  return { eta, travelTimeMin: totalMinutes, travelTimeLabel: label };
}

/**
 * Full navigation calculation between two points.
 * Includes bearing, distance, and optional ETA if speed is provided.
 */
export function navigate(
  from: NavCoord,
  to: NavCoord,
  speedKnots?: number,
): NavCalculation {
  const bearingDeg = calculateBearing(from, to);
  const distanceNm = calculateDistance(from, to);
  const distanceMi = haversine(from, to, EARTH_RADIUS_MI);
  const etaResult = speedKnots ? calculateETA(from, to, speedKnots) : null;

  return {
    bearingDeg: Math.round(bearingDeg * 10) / 10,
    bearingLabel: compassLabel(bearingDeg),
    distanceNm: Math.round(distanceNm * 100) / 100,
    distanceMi: Math.round(distanceMi * 100) / 100,
    eta: etaResult?.eta ?? null,
    travelTimeMin: etaResult?.travelTimeMin ?? null,
    travelTimeLabel: etaResult?.travelTimeLabel ?? null,
  };
}

// ── Nearest Port / Marina ────────────────────────────────────────

interface OverpassResponse {
  elements: Array<{
    id: number;
    lat?: number;
    lon?: number;
    center?: { lat: number; lon: number };
    tags?: Record<string, string>;
  }>;
}

/**
 * Find the closest port, marina, or harbor to the given position.
 * Searches within ~50nm radius via Overpass.
 *
 * @param lat — Latitude in decimal degrees.
 * @param lon — Longitude in decimal degrees.
 * @param limit — Max results to return (default 5).
 */
export async function getClosestPort(
  lat: number,
  lon: number,
  limit: number = 5,
): Promise<Port[]> {
  const key = `ports:${lat.toFixed(1)},${lon.toFixed(1)}`;
  const cached = getCached<Port[]>(key);
  if (cached) return cached.slice(0, limit);

  const radiusM = 90_000; // ~50nm
  const query = `
    [out:json][timeout:20];
    (
      node["leisure"="marina"](around:${radiusM},${lat},${lon});
      way["leisure"="marina"](around:${radiusM},${lat},${lon});
      node["seamark:type"="harbour"](around:${radiusM},${lat},${lon});
      node["harbour"="yes"](around:${radiusM},${lat},${lon});
      node["leisure"="yacht_club"](around:${radiusM},${lat},${lon});
    );
    out center body qt 50;
  `;

  try {
    const data = await overpassQuery<OverpassResponse>(query);
    const from: NavCoord = { lat, lon };

    const ports: Port[] = (data.elements ?? []).map((el) => {
      const elLat = el.lat ?? el.center?.lat ?? 0;
      const elLon = el.lon ?? el.center?.lon ?? 0;
      const tags = el.tags ?? {};
      const to: NavCoord = { lat: elLat, lon: elLon };

      let type: Port['type'] = 'marina';
      if (tags['seamark:type'] === 'harbour' || tags.harbour === 'yes') type = 'harbor';
      else if (tags.leisure === 'yacht_club') type = 'yacht_club';

      return {
        id: `osm-port-${el.id}`,
        name: tags.name ?? 'Marina',
        lat: elLat,
        lon: elLon,
        type,
        phone: tags.phone ?? tags['contact:phone'] ?? undefined,
        website: tags.website ?? tags['contact:website'] ?? undefined,
        vhfChannel: tags['seamark:radio_station:channel'] ?? tags.vhf ?? undefined,
        distanceNm: calculateDistance(from, to),
        bearingDeg: calculateBearing(from, to),
      };
    });

    ports.sort((a, b) => a.distanceNm - b.distanceNm);
    setCache(key, ports);
    return ports.slice(0, limit);
  } catch (err) {
    console.warn('[nauticalNav] getClosestPort failed:', err);
    return [];
  }
}

// ── Nearest Coast Guard Station ──────────────────────────────────

/**
 * Find the nearest US Coast Guard station to the given position.
 * Searches via OSM for USCG-tagged amenities.
 *
 * @param lat — Latitude in decimal degrees.
 * @param lon — Longitude in decimal degrees.
 * @param limit — Max results to return (default 3).
 */
export async function getClosestCoastGuard(
  lat: number,
  lon: number,
  limit: number = 3,
): Promise<CoastGuardStation[]> {
  const key = `uscg:${lat.toFixed(1)},${lon.toFixed(1)}`;
  const cached = getCached<CoastGuardStation[]>(key);
  if (cached) return cached.slice(0, limit);

  const radiusM = 150_000; // ~80nm
  const query = `
    [out:json][timeout:20];
    (
      node["operator"~"Coast Guard|USCG",i](around:${radiusM},${lat},${lon});
      way["operator"~"Coast Guard|USCG",i](around:${radiusM},${lat},${lon});
      node["name"~"Coast Guard",i](around:${radiusM},${lat},${lon});
      node["military"="coast_guard"](around:${radiusM},${lat},${lon});
    );
    out center body qt 20;
  `;

  try {
    const data = await overpassQuery<OverpassResponse>(query);
    const from: NavCoord = { lat, lon };

    const stations: CoastGuardStation[] = (data.elements ?? []).map((el) => {
      const elLat = el.lat ?? el.center?.lat ?? 0;
      const elLon = el.lon ?? el.center?.lon ?? 0;
      const tags = el.tags ?? {};
      const to: NavCoord = { lat: elLat, lon: elLon };

      return {
        id: `osm-uscg-${el.id}`,
        name: tags.name ?? 'Coast Guard Station',
        lat: elLat,
        lon: elLon,
        phone: tags.phone ?? tags['contact:phone'] ?? undefined,
        vhfChannel: tags.vhf ?? 'Ch 16',
        distanceNm: calculateDistance(from, to),
        bearingDeg: calculateBearing(from, to),
      };
    });

    stations.sort((a, b) => a.distanceNm - b.distanceNm);
    setCache(key, stations);
    return stations.slice(0, limit);
  } catch (err) {
    console.warn('[nauticalNav] getClosestCoastGuard failed:', err);
    return [];
  }
}

// ── Utility ──────────────────────────────────────────────────────

/** Convert knots to mph. */
export function knotsToMph(knots: number): number {
  return knots * 1.15078;
}

/** Convert knots to km/h. */
export function knotsToKmh(knots: number): number {
  return knots * 1.852;
}

/** Convert nautical miles to statute miles. */
export function nmToMiles(nm: number): number {
  return nm * 1.15078;
}

/** Convert nautical miles to kilometers. */
export function nmToKm(nm: number): number {
  return nm * 1.852;
}

/** Format a bearing for display (e.g. "045° NE"). */
export function formatBearing(deg: number): string {
  const padded = String(Math.round(deg)).padStart(3, '0');
  return `${padded}° ${compassLabel(deg)}`;
}
