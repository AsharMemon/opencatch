/**
 * Storm Tracking Service for OpenCatch.
 *
 * Extends the existing weatherAlerts.ts with storm cell tracking capabilities.
 * Uses the NWS API to fetch severe weather polygons (tornado warnings,
 * severe thunderstorm warnings) and calculates ETAs to the user's location.
 *
 * NWS GeoJSON alerts include geometry polygons for affected areas, which
 * we render on the map as color-coded storm cells.
 *
 * NWS API docs: https://www.weather.gov/documentation/services-web-api
 */

// ── Types ────────────────────────────────────────────────────────

/** Type of storm cell. */
export type StormCellType = 'tornado' | 'severe-thunderstorm' | 'flash-flood' | 'other-severe';

/** A tracked storm cell with polygon geometry. */
export interface StormCell {
  /** NWS alert ID. */
  id: string;
  /** Storm type classification. */
  type: StormCellType;
  /** Event name from NWS, e.g. "Tornado Warning". */
  event: string;
  /** Short headline. */
  headline: string;
  /** Description text. */
  description: string;
  /** Protective action instructions. */
  instruction: string | null;
  /** GeoJSON polygon coordinates [lon, lat][][]. */
  polygon: number[][][];
  /** ISO 8601 onset time. */
  onset: string;
  /** ISO 8601 expiration time. */
  expires: string;
  /** Issuing NWS office. */
  senderName: string;
  /** Display color for the storm polygon. */
  color: string;
  /** Fill color (semi-transparent) for map overlay. */
  fillColor: string;
  /** Estimated distance to user in miles (null if unknown). */
  distanceMiles: number | null;
  /** Estimated time of arrival in minutes (null if unknown or stationary). */
  etaMinutes: number | null;
  /** Movement direction in degrees (null if unknown). */
  motionDegrees: number | null;
  /** Movement speed in mph (null if unknown). */
  motionSpeedMph: number | null;
}

/** GeoJSON feature collection for rendering storm polygons on the map. */
export interface StormGeoJSON {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    geometry: {
      type: 'Polygon';
      coordinates: number[][][];
    };
    properties: {
      id: string;
      type: StormCellType;
      event: string;
      headline: string;
      color: string;
      fillColor: string;
      distanceMiles: number | null;
      etaMinutes: number | null;
    };
  }>;
}

// ── Constants ────────────────────────────────────────────────────

const NWS_BASE_URL = 'https://api.weather.gov';

const NWS_HEADERS: Record<string, string> = {
  'User-Agent': 'OpenCatch/1.0 (contact@opencatch.app)',
  Accept: 'application/geo+json',
};

const REQUEST_TIMEOUT_MS = 10_000;
const CACHE_TTL_MS = 5 * 60 * 1000;

/** Color mapping for storm cell types. */
const STORM_COLORS: Record<StormCellType, { stroke: string; fill: string }> = {
  tornado: { stroke: '#D50000', fill: 'rgba(213, 0, 0, 0.25)' },
  'severe-thunderstorm': { stroke: '#FFD600', fill: 'rgba(255, 214, 0, 0.20)' },
  'flash-flood': { stroke: '#2E7D32', fill: 'rgba(46, 125, 50, 0.20)' },
  'other-severe': { stroke: '#FF6D00', fill: 'rgba(255, 109, 0, 0.20)' },
};

/** NWS event names that include polygon geometry. */
const SEVERE_EVENT_PATTERNS: Array<{ pattern: RegExp; type: StormCellType }> = [
  { pattern: /tornado/i, type: 'tornado' },
  { pattern: /severe thunderstorm/i, type: 'severe-thunderstorm' },
  { pattern: /flash flood/i, type: 'flash-flood' },
  { pattern: /extreme wind/i, type: 'other-severe' },
  { pattern: /hurricane/i, type: 'other-severe' },
];

// ── Cache ────────────────────────────────────────────────────────

let cachedStorms: StormCell[] | null = null;
let cachedAt = 0;
let cachedBbox = '';

// ── HTTP helper ──────────────────────────────────────────────────

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
      throw new Error(`NWS API ${response.status}: ${url}`);
    }

    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`NWS API request timed out after ${REQUEST_TIMEOUT_MS}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── NWS response shapes ──────────────────────────────────────────

interface NWSAlertGeometry {
  type: string;
  coordinates: number[][][];
}

interface NWSStormFeature {
  id: string;
  type: string;
  geometry: NWSAlertGeometry | null;
  properties: {
    id: string;
    event: string;
    severity: string;
    headline: string | null;
    description: string;
    instruction: string | null;
    onset: string;
    expires: string;
    senderName: string;
    parameters?: {
      /** NWS motion vector: e.g. "...MOT...DEG...270031KT..." */
      motionVector?: string[];
      maxHailSize?: string[];
      maxWindGust?: string[];
    };
  };
}

interface NWSAlertCollection {
  features: NWSStormFeature[];
}

// ── Internal helpers ─────────────────────────────────────────────

function classifyStormType(event: string): StormCellType {
  for (const rule of SEVERE_EVENT_PATTERNS) {
    if (rule.pattern.test(event)) {
      return rule.type;
    }
  }
  return 'other-severe';
}

/**
 * Parse NWS motion vector string.
 * Format: "...MOT...DEG...270031KT..." or embedded in description.
 */
function parseMotionVector(params: NWSStormFeature['properties']['parameters']): {
  degrees: number | null;
  speedMph: number | null;
} {
  if (!params?.motionVector?.[0]) {
    return { degrees: null, speedMph: null };
  }

  const motionStr = params.motionVector[0];
  // Pattern: DEG followed by direction and speed in knots
  const match = motionStr.match(/(\d{3})(\d{2,3})KT/);
  if (match) {
    const degrees = parseInt(match[1], 10);
    const speedKnots = parseInt(match[2], 10);
    return { degrees, speedMph: Math.round(speedKnots * 1.151) };
  }

  return { degrees: null, speedMph: null };
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

/** Get the centroid of a polygon. */
function polygonCentroid(coords: number[][][]): { lat: number; lon: number } {
  const ring = coords[0] ?? [];
  if (ring.length === 0) return { lat: 0, lon: 0 };

  let sumLon = 0;
  let sumLat = 0;
  for (const [lon, lat] of ring) {
    sumLon += lon;
    sumLat += lat;
  }

  return {
    lon: sumLon / ring.length,
    lat: sumLat / ring.length,
  };
}

function parseStormCell(
  feature: NWSStormFeature,
  userLat?: number,
  userLon?: number,
): StormCell | null {
  const props = feature.properties;
  const type = classifyStormType(props.event);
  const colors = STORM_COLORS[type];

  // Skip alerts without polygon geometry
  if (!feature.geometry?.coordinates || feature.geometry.type !== 'Polygon') {
    return null;
  }

  const motion = parseMotionVector(props.parameters);

  let distanceMiles: number | null = null;
  let etaMinutes: number | null = null;

  if (userLat != null && userLon != null) {
    const centroid = polygonCentroid(feature.geometry.coordinates);
    distanceMiles = Math.round(haversineDistanceMiles(userLat, userLon, centroid.lat, centroid.lon));

    if (motion.speedMph && motion.speedMph > 0 && distanceMiles > 0) {
      etaMinutes = Math.round((distanceMiles / motion.speedMph) * 60);
    }
  }

  return {
    id: props.id ?? feature.id,
    type,
    event: props.event,
    headline: props.headline ?? props.event,
    description: props.description ?? '',
    instruction: props.instruction ?? null,
    polygon: feature.geometry.coordinates,
    onset: props.onset,
    expires: props.expires,
    senderName: props.senderName ?? 'National Weather Service',
    color: colors.stroke,
    fillColor: colors.fill,
    distanceMiles,
    etaMinutes,
    motionDegrees: motion.degrees,
    motionSpeedMph: motion.speedMph,
  };
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get active storm cells within a bounding box.
 *
 * Fetches severe weather warnings from NWS that include polygon geometry
 * (tornado, severe thunderstorm, flash flood warnings).
 *
 * @param bbox - Bounding box [south, west, north, east] in decimal degrees.
 * @param userLat - User's latitude for distance/ETA calculations.
 * @param userLon - User's longitude for distance/ETA calculations.
 * @returns Array of storm cells with polygons for map rendering.
 */
export async function getActiveStormCells(
  bbox: [number, number, number, number],
  userLat?: number,
  userLon?: number,
): Promise<StormCell[]> {
  const bboxStr = bbox.map((v) => v.toFixed(4)).join(',');
  const [south, west, north, east] = bbox;
  const centerLat = (south + north) / 2;
  const centerLon = (west + east) / 2;

  if (cachedStorms && Date.now() - cachedAt < CACHE_TTL_MS && cachedBbox === bboxStr) {
    return cachedStorms;
  }

  // NWS severe polygon products are for US coverage. Avoid noisy 400s in Canada.
  if (centerLat > 49.2 || centerLon < -141 || centerLon > -52) {
    return cachedStorms ?? [];
  }

  try {
    const url = `${NWS_BASE_URL}/alerts/active?status=actual&limit=80`;

    const data = await nwsFetch<NWSAlertCollection>(url);

    const storms = (data.features ?? [])
      .map((f) => parseStormCell(f, userLat, userLon))
      .filter((s): s is StormCell => s !== null)
      // Filter to only storms with polygons that intersect our bbox (rough check)
      .filter((s) => {
        const centroid = polygonCentroid(s.polygon);
        return (
          centroid.lat >= south &&
          centroid.lat <= north &&
          centroid.lon >= west &&
          centroid.lon <= east
        );
      })
      .sort((a, b) => {
        // Sort: tornado first, then by distance
        const typeOrder: Record<StormCellType, number> = {
          tornado: 0,
          'severe-thunderstorm': 1,
          'flash-flood': 2,
          'other-severe': 3,
        };
        const typeDiff = typeOrder[a.type] - typeOrder[b.type];
        if (typeDiff !== 0) return typeDiff;
        return (a.distanceMiles ?? 999) - (b.distanceMiles ?? 999);
      });

    cachedStorms = storms;
    cachedAt = Date.now();
    cachedBbox = bboxStr;
    return storms;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch storm cells:', err);
    return cachedStorms ?? [];
  }
}

/**
 * Convert storm cells to GeoJSON for map rendering.
 *
 * Returns a FeatureCollection suitable for MapLibre ShapeSource with
 * FillLayer (for polygons) and LineLayer (for outlines).
 *
 * @param storms - Array of storm cells from getActiveStormCells.
 * @returns GeoJSON FeatureCollection.
 */
export function stormCellsToGeoJSON(storms: StormCell[]): StormGeoJSON {
  return {
    type: 'FeatureCollection',
    features: storms.map((storm) => ({
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: storm.polygon,
      },
      properties: {
        id: storm.id,
        type: storm.type,
        event: storm.event,
        headline: storm.headline,
        color: storm.color,
        fillColor: storm.fillColor,
        distanceMiles: storm.distanceMiles,
        etaMinutes: storm.etaMinutes,
      },
    })),
  };
}

/**
 * Get the nearest storm cell to the user.
 *
 * @param userLat - User's latitude.
 * @param userLon - User's longitude.
 * @returns Nearest storm cell, or null if none found.
 */
export async function getNearestStorm(
  userLat: number,
  userLon: number,
): Promise<StormCell | null> {
  // Build a 200-mile bbox around the user
  const dLat = 200 / 69;
  const dLon = 200 / (69 * Math.cos((userLat * Math.PI) / 180));
  const bbox: [number, number, number, number] = [
    userLat - dLat,
    userLon - dLon,
    userLat + dLat,
    userLon + dLon,
  ];

  const storms = await getActiveStormCells(bbox, userLat, userLon);
  return storms.length > 0 ? storms[0] : null;
}

/** Clear the storm tracking cache. */
export function clearStormCache(): void {
  cachedStorms = null;
  cachedAt = 0;
  cachedBbox = '';
}
