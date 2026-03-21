/**
 * Wind overlay service for OpenCatch.
 *
 * Fetches real-time wind data from the Open-Meteo API (free, no key required)
 * for a grid of points around a center coordinate and converts the result
 * into a GeoJSON FeatureCollection suitable for map arrow rendering.
 *
 * Results are cached in memory for 30 minutes to avoid redundant network calls.
 */

// ── Types ────────────────────────────────────────────────────────

/** A single wind observation at a geographic point. */
export interface WindPoint {
  lat: number;
  lon: number;
  speedMph: number;
  directionDeg: number;
  gustMph?: number;
}

/** A grid of wind observations around a center coordinate. */
export interface WindGridData {
  points: WindPoint[];
  fetchedAt: number;
  centerLat: number;
  centerLon: number;
}

/** GeoJSON types used by the wind arrow layer. */
interface WindFeatureProperties {
  speedMph: number;
  directionDeg: number;
  gustMph?: number;
  description: string;
  /** Rotation value for map arrow icons (meteorological convention). */
  iconRotation: number;
}

interface WindFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number]; // [lon, lat]
  };
  properties: WindFeatureProperties;
}

interface WindFeatureCollection {
  type: 'FeatureCollection';
  features: WindFeature[];
}

// ── Constants ────────────────────────────────────────────────────

const OPEN_METEO_BASE =
  'https://api.open-meteo.com/v1/forecast';

/** Cache lifetime in milliseconds (30 minutes). */
const CACHE_TTL_MS = 30 * 60 * 1000;

/** Default radius in degrees around the center coordinate. */
const DEFAULT_RADIUS_DEG = 1.0;

/** Default spacing between grid points in degrees. */
const DEFAULT_SPACING_DEG = 0.5;

// ── Cache ────────────────────────────────────────────────────────

let cachedGrid: WindGridData | null = null;

/**
 * Returns the cached grid if it covers the requested center and is still fresh.
 * A cache hit requires the center to be within 0.01 degrees of the cached center.
 */
function getCached(centerLat: number, centerLon: number): WindGridData | null {
  if (!cachedGrid) return null;
  const age = Date.now() - cachedGrid.fetchedAt;
  if (age > CACHE_TTL_MS) {
    cachedGrid = null;
    return null;
  }
  const latClose = Math.abs(cachedGrid.centerLat - centerLat) < 0.01;
  const lonClose = Math.abs(cachedGrid.centerLon - centerLon) < 0.01;
  if (latClose && lonClose) return cachedGrid;
  return null;
}

// ── Open-Meteo helpers ───────────────────────────────────────────

interface OpenMeteoCurrentResponse {
  current?: {
    wind_speed_10m?: number;
    wind_direction_10m?: number;
    wind_gusts_10m?: number;
  };
}

/**
 * Build a list of (lat, lon) pairs forming a rectangular grid around the
 * given center within `radiusDeg`, spaced by `spacingDeg`.
 */
function buildGridCoords(
  centerLat: number,
  centerLon: number,
  radiusDeg: number,
  spacingDeg: number,
): { lat: number; lon: number }[] {
  const coords: { lat: number; lon: number }[] = [];
  const latMin = centerLat - radiusDeg;
  const latMax = centerLat + radiusDeg;
  const lonMin = centerLon - radiusDeg;
  const lonMax = centerLon + radiusDeg;

  for (let lat = latMin; lat <= latMax + 1e-9; lat += spacingDeg) {
    for (let lon = lonMin; lon <= lonMax + 1e-9; lon += spacingDeg) {
      coords.push({
        lat: parseFloat(lat.toFixed(4)),
        lon: parseFloat(lon.toFixed(4)),
      });
    }
  }
  return coords;
}

/**
 * Fetch wind data for a single coordinate from Open-Meteo.
 * Returns `null` on any network or parsing error so the caller can skip it.
 */
async function fetchSinglePoint(
  lat: number,
  lon: number,
): Promise<WindPoint | null> {
  try {
    const url =
      `${OPEN_METEO_BASE}?latitude=${lat}&longitude=${lon}` +
      '&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m' +
      '&wind_speed_unit=mph';

    const res = await fetch(url);
    if (!res.ok) return null;

    const json: OpenMeteoCurrentResponse = await res.json();
    const current = json.current;
    if (!current) return null;

    return {
      lat,
      lon,
      speedMph: current.wind_speed_10m ?? 0,
      directionDeg: current.wind_direction_10m ?? 0,
      gustMph: current.wind_gusts_10m ?? undefined,
    };
  } catch {
    return null;
  }
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch a grid of wind observations around a center coordinate.
 *
 * @param centerLat  Center latitude in decimal degrees.
 * @param centerLon  Center longitude in decimal degrees.
 * @param radiusDeg  Half-width of the grid in degrees (default 1.0).
 * @param spacingDeg Distance between grid points in degrees (default 0.5).
 * @returns A `WindGridData` with all successfully fetched points.
 *          Returns an empty grid (no points) on total failure.
 */
export async function fetchWindGrid(
  centerLat: number,
  centerLon: number,
  radiusDeg: number = DEFAULT_RADIUS_DEG,
  spacingDeg: number = DEFAULT_SPACING_DEG,
): Promise<WindGridData> {
  // Return cached result when available.
  const cached = getCached(centerLat, centerLon);
  if (cached) return cached;

  const coords = buildGridCoords(centerLat, centerLon, radiusDeg, spacingDeg);

  // Fetch all points concurrently.
  const results = await Promise.all(
    coords.map((c) => fetchSinglePoint(c.lat, c.lon)),
  );

  const points: WindPoint[] = results.filter(
    (p): p is WindPoint => p !== null,
  );

  const grid: WindGridData = {
    points,
    fetchedAt: Date.now(),
    centerLat,
    centerLon,
  };

  // Update cache.
  cachedGrid = grid;
  return grid;
}

/**
 * Convert a `WindGridData` into a GeoJSON FeatureCollection with arrow
 * rotation properties suitable for a symbol layer on a Mapbox/MapLibre map.
 *
 * Each feature includes:
 * - `speedMph` / `directionDeg` / `gustMph` — raw values
 * - `description` — human-readable wind label (e.g. "Moderate")
 * - `iconRotation` — degrees to rotate an arrow icon so it points in the
 *   direction the wind is blowing (meteorological "from" convention).
 */
export function windGridToGeoJSON(grid: WindGridData): WindFeatureCollection {
  const features: WindFeature[] = grid.points.map((p) => ({
    type: 'Feature' as const,
    geometry: {
      type: 'Point' as const,
      coordinates: [p.lon, p.lat],
    },
    properties: {
      speedMph: p.speedMph,
      directionDeg: p.directionDeg,
      gustMph: p.gustMph,
      description: getWindDescription(p.speedMph),
      // Wind direction is reported as the direction the wind comes FROM.
      // Arrow should point in the direction it blows TO, so add 180°.
      iconRotation: (p.directionDeg + 180) % 360,
    },
  }));

  return {
    type: 'FeatureCollection',
    features,
  };
}

/**
 * Return a human-readable description of wind speed.
 *
 * | Range (mph) | Label        |
 * |-------------|--------------|
 * | 0 – 3       | Calm         |
 * | 4 – 12      | Light        |
 * | 13 – 24     | Moderate     |
 * | 25 – 38     | Strong       |
 * | 39 – 54     | Very Strong  |
 * | 55+         | Storm        |
 */
export function getWindDescription(speedMph: number): string {
  if (speedMph <= 3) return 'Calm';
  if (speedMph <= 12) return 'Light';
  if (speedMph <= 24) return 'Moderate';
  if (speedMph <= 38) return 'Strong';
  if (speedMph <= 54) return 'Very Strong';
  return 'Storm';
}
