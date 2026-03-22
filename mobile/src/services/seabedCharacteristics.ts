/**
 * OpenCatch -- Seabed Characteristics Service
 *
 * Provides ocean/lake bottom composition data critical for anchoring decisions.
 * Sources: NOAA ENC (SBDARE features), OSM seamark tags, NOAA ArcGIS services.
 *
 * Types: sand, mud, rock, gravel, shells, coral, clay, silt, weed, mixed
 * Each type has an anchoring suitability rating: good, fair, or poor.
 */

// ── Types ────────────────────────────────────────────────────────

export type SeabedType =
  | 'sand'
  | 'mud'
  | 'rock'
  | 'gravel'
  | 'shells'
  | 'coral'
  | 'clay'
  | 'silt'
  | 'weed'
  | 'mixed'
  | 'unknown';

export type AnchoringSuitability = 'good' | 'fair' | 'poor' | 'unknown';

export interface SeabedPoint {
  lat: number;
  lon: number;
  type: SeabedType;
  suitability: AnchoringSuitability;
  /** Nautical chart abbreviation */
  abbreviation: string;
  /** Human-readable description */
  description: string;
  /** Color for map rendering (anchoring-based) */
  color: string;
  /** Source of data */
  source: 'noaa-enc' | 'osm' | 'noaa-arcgis';
}

export interface BBox {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

// ── Constants ────────────────────────────────────────────────────

const REQUEST_TIMEOUT_MS = 12_000;

/** Nautical chart abbreviations per IHO S-57 */
const SEABED_ABBREVIATIONS: Record<SeabedType, string> = {
  sand: 'S',
  mud: 'M',
  rock: 'R',
  gravel: 'Gy',
  shells: 'Sh',
  coral: 'Co',
  clay: 'Cl',
  silt: 'Si',
  weed: 'Wd',
  mixed: 'Mx',
  unknown: '?',
};

const SEABED_DESCRIPTIONS: Record<SeabedType, string> = {
  sand: 'Sand — good holding for most anchors',
  mud: 'Mud — excellent holding, may be hard to break free',
  rock: 'Rock — poor holding, high risk of anchor fouling',
  gravel: 'Gravel — fair holding, may drag in strong current',
  shells: 'Shells — fair holding, anchor may skip on surface',
  coral: 'Coral — poor holding, damages reef and anchor',
  clay: 'Clay — good holding, firm bottom',
  silt: 'Silt — fair to good holding, soft bottom',
  weed: 'Weed/Kelp — poor holding, anchor may not set',
  mixed: 'Mixed bottom — variable holding conditions',
  unknown: 'Unknown bottom type',
};

const SUITABILITY_MAP: Record<SeabedType, AnchoringSuitability> = {
  sand: 'good',
  mud: 'good',
  clay: 'good',
  silt: 'fair',
  gravel: 'fair',
  shells: 'fair',
  mixed: 'fair',
  rock: 'poor',
  coral: 'poor',
  weed: 'poor',
  unknown: 'unknown',
};

const SUITABILITY_COLORS: Record<AnchoringSuitability, string> = {
  good: '#4CAF50',    // Green
  fair: '#FFC107',    // Yellow/Amber
  poor: '#F44336',    // Red
  unknown: '#9E9E9E', // Gray
};

// ── NOAA ENC S-57 NATSUR codes → SeabedType ─────────────────────

const NATSUR_CODE_MAP: Record<number, SeabedType> = {
  1: 'mud',
  2: 'clay',
  3: 'silt',
  4: 'sand',
  5: 'rock',       // stone
  6: 'gravel',
  7: 'gravel',     // pebbles
  8: 'rock',       // cobbles
  9: 'rock',       // rocky
  11: 'rock',      // lava
  14: 'coral',
  17: 'shells',
  18: 'weed',      // mud and shells mix → weed-like
};

// ── Helpers ──────────────────────────────────────────────────────

function classifySeabedType(raw: string): SeabedType {
  const lower = raw.toLowerCase().trim();
  if (lower.includes('sand')) return 'sand';
  if (lower.includes('mud')) return 'mud';
  if (lower.includes('rock') || lower.includes('stone') || lower.includes('cobble') || lower.includes('boulder')) return 'rock';
  if (lower.includes('gravel') || lower.includes('pebble')) return 'gravel';
  if (lower.includes('shell')) return 'shells';
  if (lower.includes('coral')) return 'coral';
  if (lower.includes('clay')) return 'clay';
  if (lower.includes('silt')) return 'silt';
  if (lower.includes('weed') || lower.includes('kelp') || lower.includes('grass')) return 'weed';
  if (lower.includes('mix')) return 'mixed';
  return 'unknown';
}

function buildSeabedPoint(
  lat: number,
  lon: number,
  type: SeabedType,
  source: SeabedPoint['source'],
): SeabedPoint {
  const suitability = SUITABILITY_MAP[type];
  return {
    lat,
    lon,
    type,
    suitability,
    abbreviation: SEABED_ABBREVIATIONS[type],
    description: SEABED_DESCRIPTIONS[type],
    color: SUITABILITY_COLORS[suitability],
    source,
  };
}

async function fetchWithTimeout<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── Data cache ───────────────────────────────────────────────────

interface CacheEntry {
  points: SeabedPoint[];
  timestamp: number;
  bbox: BBox;
}

let cache: CacheEntry | null = null;
const CACHE_TTL_MS = 15 * 60 * 1000; // 15 minutes

function isCacheValid(bbox: BBox): boolean {
  if (!cache) return false;
  if (Date.now() - cache.timestamp > CACHE_TTL_MS) return false;
  return (
    bbox.minLat >= cache.bbox.minLat &&
    bbox.maxLat <= cache.bbox.maxLat &&
    bbox.minLon >= cache.bbox.minLon &&
    bbox.maxLon <= cache.bbox.maxLon
  );
}

// ── NOAA ENC ArcGIS (SBDARE — Seabed Area) ──────────────────────

async function fetchNOAASeabed(bbox: BBox): Promise<SeabedPoint[]> {
  const points: SeabedPoint[] = [];
  try {
    // NOAA ENC Online MapServer — SBDARE layer (layer 2 in many services)
    const url =
      `https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/identify` +
      `?geometry=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&geometryType=esriGeometryEnvelope&sr=4326` +
      `&layers=all:2` +
      `&tolerance=10` +
      `&mapExtent=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&imageDisplay=800,600,96&returnGeometry=true&f=json`;

    const data = await fetchWithTimeout<{
      results?: Array<{
        geometry?: { x: number; y: number };
        attributes?: {
          NATSUR?: number | string;
          NATQUA?: string;
          COLOUR?: string;
          OBJNAM?: string;
        };
      }>;
    }>(url);

    if (data.results) {
      for (const result of data.results) {
        const geom = result.geometry;
        const attrs = result.attributes;
        if (!geom || !attrs) continue;

        let seabedType: SeabedType = 'unknown';
        const natSur = attrs.NATSUR;
        if (typeof natSur === 'number' && NATSUR_CODE_MAP[natSur]) {
          seabedType = NATSUR_CODE_MAP[natSur];
        } else if (typeof natSur === 'string') {
          seabedType = classifySeabedType(natSur);
        }

        if (seabedType !== 'unknown') {
          points.push(buildSeabedPoint(geom.y, geom.x, seabedType, 'noaa-enc'));
        }
      }
    }
  } catch (err) {
    console.warn('[SeabedCharacteristics] NOAA ENC fetch failed:', err);
  }
  return points;
}

// ── OSM Overpass (seamark:seabed_area) ───────────────────────────

async function fetchOSMSeabed(bbox: BBox): Promise<SeabedPoint[]> {
  const points: SeabedPoint[] = [];
  try {
    const query = `[out:json][timeout:10];
      node["seamark:seabed_area:surface"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
      out body;`;
    const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

    const data = await fetchWithTimeout<{
      elements?: Array<{
        lat: number;
        lon: number;
        tags?: Record<string, string>;
      }>;
    }>(url);

    if (data.elements) {
      for (const el of data.elements) {
        const surface = el.tags?.['seamark:seabed_area:surface'] ?? '';
        const seabedType = classifySeabedType(surface);
        if (seabedType !== 'unknown') {
          points.push(buildSeabedPoint(el.lat, el.lon, seabedType, 'osm'));
        }
      }
    }
  } catch (err) {
    console.warn('[SeabedCharacteristics] OSM fetch failed:', err);
  }
  return points;
}

// ── NOAA Marine Protected Areas (MPA) sediment info ──────────────

async function fetchNOAAArcGISSeabed(bbox: BBox): Promise<SeabedPoint[]> {
  const points: SeabedPoint[] = [];
  try {
    // NOAA National Geophysical Data Center — marine geology
    const url =
      `https://gis.ngdc.noaa.gov/arcgis/rest/services/web_mercator/sample_index/MapServer/0/query` +
      `?where=1=1` +
      `&geometry=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326` +
      `&outFields=LAT,LON,BOTTOM_TYPE,BOTTOM_DESCRIPTION` +
      `&returnGeometry=true&f=json` +
      `&resultRecordCount=100`;

    const data = await fetchWithTimeout<{
      features?: Array<{
        geometry?: { x: number; y: number };
        attributes?: {
          BOTTOM_TYPE?: string;
          BOTTOM_DESCRIPTION?: string;
          LAT?: number;
          LON?: number;
        };
      }>;
    }>(url);

    if (data.features) {
      for (const feature of data.features) {
        const attrs = feature.attributes;
        const geom = feature.geometry;
        if (!attrs || !geom) continue;

        const raw = attrs.BOTTOM_TYPE ?? attrs.BOTTOM_DESCRIPTION ?? '';
        const seabedType = classifySeabedType(raw);
        if (seabedType !== 'unknown') {
          points.push(buildSeabedPoint(geom.y, geom.x, seabedType, 'noaa-arcgis'));
        }
      }
    }
  } catch (err) {
    console.warn('[SeabedCharacteristics] NOAA ArcGIS fetch failed:', err);
  }
  return points;
}

// ── Deduplication ────────────────────────────────────────────────

function deduplicatePoints(points: SeabedPoint[], radiusDeg: number = 0.001): SeabedPoint[] {
  const result: SeabedPoint[] = [];
  for (const p of points) {
    const isDup = result.some(
      (r) => Math.abs(r.lat - p.lat) < radiusDeg && Math.abs(r.lon - p.lon) < radiusDeg,
    );
    if (!isDup) result.push(p);
  }
  return result;
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get seabed type at a specific point (searches small area around it).
 */
export async function getSeabedType(
  lat: number,
  lon: number,
): Promise<SeabedPoint | null> {
  const delta = 0.01; // ~1km
  const bbox: BBox = {
    minLat: lat - delta,
    maxLat: lat + delta,
    minLon: lon - delta,
    maxLon: lon + delta,
  };

  const points = await getSeabedInArea(bbox);
  if (points.length === 0) return null;

  // Return the closest point
  let closest = points[0];
  let minDist = Math.abs(closest.lat - lat) + Math.abs(closest.lon - lon);
  for (const p of points) {
    const dist = Math.abs(p.lat - lat) + Math.abs(p.lon - lon);
    if (dist < minDist) {
      minDist = dist;
      closest = p;
    }
  }
  return closest;
}

/**
 * Get all seabed data points in the bounding box.
 * Fetches from NOAA ENC, OSM, and NOAA ArcGIS in parallel, then deduplicates.
 */
export async function getSeabedInArea(bbox: BBox): Promise<SeabedPoint[]> {
  // Check cache first
  if (isCacheValid(bbox) && cache) {
    return cache.points.filter(
      (p) =>
        p.lat >= bbox.minLat &&
        p.lat <= bbox.maxLat &&
        p.lon >= bbox.minLon &&
        p.lon <= bbox.maxLon,
    );
  }

  // Expand bbox slightly for cache
  const expandedBBox: BBox = {
    minLat: bbox.minLat - 0.05,
    maxLat: bbox.maxLat + 0.05,
    minLon: bbox.minLon - 0.05,
    maxLon: bbox.maxLon + 0.05,
  };

  const [noaaPoints, osmPoints, arcgisPoints] = await Promise.all([
    fetchNOAASeabed(expandedBBox),
    fetchOSMSeabed(expandedBBox),
    fetchNOAAArcGISSeabed(expandedBBox),
  ]);

  // Merge and deduplicate — prioritize NOAA ENC over others
  const allPoints = [...noaaPoints, ...osmPoints, ...arcgisPoints];
  const deduped = deduplicatePoints(allPoints);

  // Update cache
  cache = {
    points: deduped,
    timestamp: Date.now(),
    bbox: expandedBBox,
  };

  return deduped.filter(
    (p) =>
      p.lat >= bbox.minLat &&
      p.lat <= bbox.maxLat &&
      p.lon >= bbox.minLon &&
      p.lon <= bbox.maxLon,
  );
}

/**
 * Get anchoring suitability for a type.
 */
export function getAnchoringSuitability(type: SeabedType): AnchoringSuitability {
  return SUITABILITY_MAP[type];
}

/**
 * Get the nautical abbreviation for a seabed type.
 */
export function getSeabedAbbreviation(type: SeabedType): string {
  return SEABED_ABBREVIATIONS[type];
}

/**
 * Get anchoring advice text for a seabed type.
 */
export function getAnchoringAdvice(type: SeabedType): string {
  const suit = SUITABILITY_MAP[type];
  switch (suit) {
    case 'good':
      return 'Good anchoring bottom. Set anchor firmly and verify hold.';
    case 'fair':
      return 'Fair anchoring. Use more scope and check anchor periodically.';
    case 'poor':
      return 'Poor anchoring bottom. Consider alternative anchorage if possible.';
    default:
      return 'Unknown bottom — proceed with caution when anchoring.';
  }
}

/**
 * Build GeoJSON for map rendering.
 */
export function seabedToGeoJSON(points: SeabedPoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        type: p.type,
        suitability: p.suitability,
        abbreviation: p.abbreviation,
        description: p.description,
        color: p.color,
        source: p.source,
      },
    })),
  };
}

// ── Legend data for external use ──────────────────────────────────

export const SEABED_LEGEND_STOPS = [
  { color: '#4CAF50', label: 'Good (S, M, Cl)' },
  { color: '#FFC107', label: 'Fair (Gy, Sh, Mx)' },
  { color: '#F44336', label: 'Poor (R, Co, Wd)' },
];
