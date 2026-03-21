/**
 * Trail service for OpenCatch.
 *
 * Fetches hiking and walking trails near water from OpenStreetMap
 * via the Overpass API. Returns trail LineStrings for rendering as
 * actual path lines on the map, not just point markers.
 * Results are cached in memory for 1 hour.
 */

// ── Types ────────────────────────────────────────────────────────

/** A single trail segment. */
export interface Trail {
  id: string;
  name: string;
  /** Total length in metres (from OSM tags, if available). */
  lengthMeters?: number;
  /** Difficulty: easy, intermediate, difficult, expert. */
  difficulty?: string;
  /** Surface type: gravel, paved, dirt, etc. */
  surface?: string;
  /** The trail geometry as a GeoJSON LineString coordinate array. */
  coordinates: [number, number][];
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry {
  data: Trail[];
  timestamp: number;
}

const cache = new Map<string, CacheEntry>();

function cacheKey(lat: number, lon: number, radiusMeters: number): string {
  const latR = Math.round(lat * 1000) / 1000;
  const lonR = Math.round(lon * 1000) / 1000;
  return `trail-${latR},${lonR},${radiusMeters}`;
}

function getCached(key: string): Trail[] | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

function setCache(key: string, data: Trail[]): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── Overpass query ───────────────────────────────────────────────

function buildTrailQuery(lat: number, lon: number, radiusMeters: number): string {
  const around = `(around:${radiusMeters},${lat},${lon})`;
  // Fetch hiking/walking/nature trails that are ways (have geometry)
  return `
[out:json][timeout:25];
(
  way["highway"="path"]["sac_scale"]${around};
  way["highway"="footway"]["name"]${around};
  way["highway"="path"]["name"]${around};
  way["route"="hiking"]${around};
  way["highway"="track"]["tracktype"~"grade[1-3]"]["name"]${around};
  relation["route"="hiking"]${around};
);
out body geom;
`.trim();
}

// ── Difficulty mapping ──────────────────────────────────────────

function parseDifficulty(tags: Record<string, string>): string | undefined {
  const sac = tags.sac_scale;
  if (!sac) return undefined;
  if (sac === 'hiking') return 'easy';
  if (sac === 'mountain_hiking') return 'intermediate';
  if (sac === 'demanding_mountain_hiking') return 'difficult';
  return 'expert';
}

// ── Element parsing ─────────────────────────────────────────────

interface OverpassElement {
  type: string;
  id: number;
  tags?: Record<string, string>;
  geometry?: { lat: number; lon: number }[];
  members?: { type: string; ref: number; role: string; geometry?: { lat: number; lon: number }[] }[];
}

function elementToTrail(el: OverpassElement): Trail | null {
  const tags = el.tags ?? {};

  let coords: [number, number][] = [];

  if (el.type === 'way' && el.geometry) {
    coords = el.geometry.map((g) => [g.lon, g.lat]);
  } else if (el.type === 'relation' && el.members) {
    // Concatenate way members
    for (const member of el.members) {
      if (member.geometry) {
        const memberCoords = member.geometry.map((g) => [g.lon, g.lat] as [number, number]);
        coords.push(...memberCoords);
      }
    }
  }

  if (coords.length < 2) return null;

  // Parse length from tags or compute approximate length
  let lengthMeters: number | undefined;
  if (tags.length) {
    const parsed = parseFloat(tags.length);
    if (!isNaN(parsed)) lengthMeters = parsed;
  }
  if (tags.distance) {
    const parsed = parseFloat(tags.distance);
    if (!isNaN(parsed)) {
      // distance tag is usually in km
      lengthMeters = parsed * 1000;
    }
  }

  return {
    id: `${el.type}/${el.id}`,
    name: tags.name || tags['name:en'] || 'Unnamed Trail',
    lengthMeters,
    difficulty: parseDifficulty(tags),
    surface: tags.surface || undefined,
    coordinates: coords,
  };
}

// ── Fetch wrapper ───────────────────────────────────────────────

async function runOverpassQuery(query: string): Promise<OverpassElement[]> {
  const response = await fetch(OVERPASS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `data=${encodeURIComponent(query)}`,
  });

  if (!response.ok) {
    throw new Error(`Overpass API error: ${response.status} ${response.statusText}`);
  }

  const json = await response.json();
  return (json.elements ?? []) as OverpassElement[];
}

// ── Public API ──────────────────────────────────────────────────

/**
 * Fetch trails within `radiusMeters` of the given coordinates.
 * Results are cached for 1 hour.
 */
export async function fetchNearbyTrails(
  lat: number,
  lon: number,
  radiusMeters: number = 10_000,
): Promise<Trail[]> {
  const key = cacheKey(lat, lon, radiusMeters);
  const cached = getCached(key);
  if (cached) return cached;

  try {
    const query = buildTrailQuery(lat, lon, radiusMeters);
    const elements = await runOverpassQuery(query);
    const trails = elements
      .map(elementToTrail)
      .filter((t): t is Trail => t !== null);

    // De-duplicate by id
    const seen = new Set<string>();
    const unique = trails.filter((t) => {
      if (seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });

    setCache(key, unique);
    return unique;
  } catch (err) {
    console.warn('[trailService] fetchNearbyTrails failed:', err);
    return [];
  }
}

/**
 * Build a GeoJSON FeatureCollection of trail LineStrings for map rendering.
 */
export function trailsToGeoJSON(trails: Trail[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: trails.map((t) => ({
      type: 'Feature' as const,
      id: t.id,
      geometry: {
        type: 'LineString' as const,
        coordinates: t.coordinates,
      },
      properties: {
        id: t.id,
        name: t.name,
        difficulty: t.difficulty ?? 'unknown',
        surface: t.surface ?? '',
        lengthMi: t.lengthMeters ? (t.lengthMeters / 1609.344).toFixed(1) : '',
      },
    })),
  };
}
