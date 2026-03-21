/**
 * Access-point service for OpenCatch.
 *
 * Queries the OpenStreetMap Overpass API for boat launches, shore
 * fishing spots, kayak put-ins, parking areas, trailheads, fishing
 * piers, and fish-cleaning stations near the current map center.
 * Results are cached in memory for 1 hour.
 */

// ── Types ────────────────────────────────────────────────────────

/** Access-point category. */
export type AccessPointType =
  | 'boat_launch'
  | 'shore_fishing'
  | 'kayak_launch'
  | 'parking'
  | 'trailhead'
  | 'fishing_pier'
  | 'fish_cleaning'
  | 'picnic_site';

/** Visual config for rendering a category on the map. */
export interface AccessPointStyle {
  color: string;
  ionicon: string;
  label: string;
  /** Marker size multiplier (1 = normal, 1.4 = large). */
  scale: number;
}

/** Bounding box for a water body (SW corner → NE corner). */
export interface WaterbodyBounds {
  south: number;
  west: number;
  north: number;
  east: number;
}

/** A single access point of interest. */
export interface AccessPoint {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: AccessPointType;
  surface?: string;
  fee?: boolean;
  capacity?: number;
  operator?: string;
  website?: string;
}

// ── Styling config per type ──────────────────────────────────────

export const ACCESS_POINT_CONFIG: Record<AccessPointType, AccessPointStyle> = {
  boat_launch:   { color: '#EA580C', ionicon: 'boat',            label: 'Boat Launch',    scale: 1.4 },
  shore_fishing: { color: '#0D9488', ionicon: 'fish',            label: 'Shore Fishing',  scale: 1.0 },
  kayak_launch:  { color: '#EAB308', ionicon: 'water',           label: 'Kayak Launch',   scale: 1.1 },
  parking:       { color: '#2563EB', ionicon: 'car',             label: 'Parking',        scale: 1.0 },
  trailhead:     { color: '#16A34A', ionicon: 'walk',            label: 'Trailhead',      scale: 1.0 },
  fishing_pier:  { color: '#7C3AED', ionicon: 'flag',            label: 'Fishing Pier',   scale: 1.1 },
  fish_cleaning: { color: '#64748B', ionicon: 'cut',             label: 'Fish Cleaning',  scale: 0.9 },
  picnic_site:   { color: '#059669', ionicon: 'bonfire',         label: 'Picnic Site',    scale: 0.9 },
};

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry {
  data: AccessPoint[];
  timestamp: number;
}

const cache = new Map<string, CacheEntry>();

function cacheKey(lat: number, lon: number, radiusMeters: number): string {
  const latR = Math.round(lat * 1000) / 1000;
  const lonR = Math.round(lon * 1000) / 1000;
  return `ap-${latR},${lonR},${radiusMeters}`;
}

function getCached(key: string): AccessPoint[] | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

function setCache(key: string, data: AccessPoint[]): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── Overpass query ───────────────────────────────────────────────

/**
 * Build the spatial filter for Overpass. If a bounding box is supplied
 * we search the entire perimeter (bbox padded by `radiusMeters`),
 * otherwise fall back to a circle around the center point.
 */
function buildSpatialFilter(
  lat: number,
  lon: number,
  radiusMeters: number,
  bounds?: WaterbodyBounds,
): string {
  if (bounds) {
    // Pad the bounding box by radiusMeters on every side so we catch
    // access points on roads just outside the shoreline.
    const latPad = radiusMeters / 111_320;             // ~1 deg = 111.32 km
    const lonPad = radiusMeters / (111_320 * Math.cos((lat * Math.PI) / 180));
    return `(${bounds.south - latPad},${bounds.west - lonPad},${bounds.north + latPad},${bounds.east + lonPad})`;
  }
  return `(around:${radiusMeters},${lat},${lon})`;
}

function buildOverpassQuery(
  lat: number,
  lon: number,
  radiusMeters: number,
  bounds?: WaterbodyBounds,
): string {
  const area = buildSpatialFilter(lat, lon, radiusMeters, bounds);
  return `
[out:json][timeout:30];
(
  // Boat launches / ramps
  node["leisure"="slipway"]${area};
  way["leisure"="slipway"]${area};
  node["seamark:type"="slipway"]${area};
  node["waterway"="boat_ramp"]${area};
  way["waterway"="boat_ramp"]${area};

  // Shore fishing access
  node["leisure"="fishing"]${area};
  way["leisure"="fishing"]${area};
  node["sport"="fishing"]["access"!="private"]${area};
  node["access"="yes"]["waterway"]${area};

  // Kayak / canoe launches
  node["canoe"="put_in"]${area};
  node["sport"="canoe"]["access"!="private"]${area};
  node["leisure"="slipway"]["boat"~"canoe|kayak"]${area};

  // Parking near water
  node["amenity"="parking"]["access"!="private"]${area};
  way["amenity"="parking"]["access"!="private"]${area};

  // Trailheads
  node["highway"="trailhead"]${area};
  node["information"="guidepost"]["hiking"="yes"]${area};

  // Fishing piers & general piers
  node["man_made"="pier"]${area};
  way["man_made"="pier"]${area};

  // Picnic sites near water
  node["tourism"="picnic_site"]${area};
  way["tourism"="picnic_site"]${area};

  // Fish cleaning stations
  node["amenity"="fish_cleaning"]${area};
  node["man_made"="fish_cleaning_table"]${area};
);
out center body;
`.trim();
}

// ── Tag classification ──────────────────────────────────────────

function classifyElement(tags: Record<string, string>): AccessPointType {
  if (
    tags.leisure === 'slipway' ||
    tags['seamark:type'] === 'slipway' ||
    tags.waterway === 'boat_ramp'
  ) {
    // Distinguish kayak launches from motorboat ramps
    if (tags.boat && /canoe|kayak/i.test(tags.boat)) return 'kayak_launch';
    return 'boat_launch';
  }
  if (tags.canoe === 'put_in' || (tags.sport === 'canoe' && tags.leisure !== 'fishing')) {
    return 'kayak_launch';
  }
  if (tags.amenity === 'parking') return 'parking';
  if (tags.highway === 'trailhead' || (tags.information === 'guidepost' && tags.hiking === 'yes')) {
    return 'trailhead';
  }
  if (tags.man_made === 'pier') {
    // Piers with explicit fishing tag get the fishing_pier type
    if (tags.fishing === 'yes' || tags.leisure === 'fishing') return 'fishing_pier';
    return 'fishing_pier'; // general piers near water are still useful
  }
  if (tags.tourism === 'picnic_site') return 'picnic_site';
  if (tags.amenity === 'fish_cleaning' || tags.man_made === 'fish_cleaning_table') {
    return 'fish_cleaning';
  }
  if (tags.leisure === 'fishing' || tags.sport === 'fishing') return 'shore_fishing';
  if (tags.access === 'yes' && tags.waterway) return 'shore_fishing';
  return 'shore_fishing'; // fallback
}

// ── Element parsing ─────────────────────────────────────────────

interface OverpassElement {
  type: string;
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
}

function elementToAccessPoint(el: OverpassElement): AccessPoint | null {
  const tags = el.tags ?? {};
  const lat = el.lat ?? el.center?.lat;
  const lon = el.lon ?? el.center?.lon;
  if (lat == null || lon == null) return null;

  const apType = classifyElement(tags);

  return {
    id: `${el.type}/${el.id}`,
    name: tags.name || tags['name:en'] || ACCESS_POINT_CONFIG[apType].label,
    lat,
    lon,
    type: apType,
    surface: tags.surface || undefined,
    fee: tags.fee === 'yes' ? true : tags.fee === 'no' ? false : undefined,
    capacity: tags.capacity ? parseInt(tags.capacity, 10) || undefined : undefined,
    operator: tags.operator || undefined,
    website: tags.website || tags['contact:website'] || undefined,
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

// ── De-duplication by proximity ──────────────────────────────────

/** Haversine distance in meters between two lat/lon points. */
function haversineMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6_371_000; // Earth radius in meters
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * De-duplicate access points that are within `thresholdMeters` of
 * each other AND share the same type. When duplicates are found,
 * prefer the one with a real name (not the fallback label).
 */
function deduplicateByProximity(
  points: AccessPoint[],
  thresholdMeters: number = 50,
): AccessPoint[] {
  const kept: AccessPoint[] = [];

  for (const pt of points) {
    const duplicate = kept.find(
      (k) =>
        k.type === pt.type &&
        haversineMeters(k.lat, k.lon, pt.lat, pt.lon) < thresholdMeters,
    );

    if (duplicate) {
      // Prefer the one with a real name
      const ptHasName = pt.name !== ACCESS_POINT_CONFIG[pt.type].label;
      const dupHasName = duplicate.name !== ACCESS_POINT_CONFIG[duplicate.type].label;
      if (ptHasName && !dupHasName) {
        // Replace the existing entry with the better-named one
        const idx = kept.indexOf(duplicate);
        kept[idx] = pt;
      }
      // Otherwise keep existing
    } else {
      kept.push(pt);
    }
  }

  return kept;
}

// ── Public API ──────────────────────────────────────────────────

/**
 * Fetch all access points within `radiusMeters` of the given
 * coordinates. If `bounds` is supplied the search covers the full
 * water body perimeter instead of just a circle around the center.
 * Results are cached for 1 hour.
 */
export async function fetchNearbyAccessPoints(
  lat: number,
  lon: number,
  radiusMeters: number = 15_000,
  bounds?: WaterbodyBounds,
): Promise<AccessPoint[]> {
  // Use a minimum search radius of 5 km to avoid missing spots
  const effectiveRadius = Math.max(radiusMeters, 5_000);

  const key = cacheKey(lat, lon, effectiveRadius);
  const cached = getCached(key);
  if (cached) return cached;

  try {
    const query = buildOverpassQuery(lat, lon, effectiveRadius, bounds);
    const elements = await runOverpassQuery(query);
    const points = elements
      .map(elementToAccessPoint)
      .filter((p): p is AccessPoint => p !== null);

    // De-duplicate: first by OSM id, then by proximity (50 m)
    const seen = new Set<string>();
    const uniqueById = points.filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });

    const unique = deduplicateByProximity(uniqueById, 50);

    // Filter out parking lots that are far from water (basic heuristic:
    // keep parking only if there's at least one non-parking access point
    // within ~500m). This prevents flooding the map with random parking.
    // Apply the same logic to picnic sites.
    const waterRelated = unique.filter(
      (p) => p.type !== 'parking' && p.type !== 'picnic_site',
    );
    const filtered = unique.filter((p) => {
      if (p.type !== 'parking' && p.type !== 'picnic_site') return true;
      // Keep if any launch/pier/fishing point is within ~0.005 deg (~500m)
      return waterRelated.some(
        (np) => Math.abs(np.lat - p.lat) < 0.005 && Math.abs(np.lon - p.lon) < 0.005,
      );
    });

    setCache(key, filtered);
    return filtered;
  } catch (err) {
    console.warn('[accessPointService] fetchNearbyAccessPoints failed:', err);
    return [];
  }
}

/**
 * Build a GeoJSON FeatureCollection from access points for map rendering.
 */
export function accessPointsToGeoJSON(points: AccessPoint[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: points.map((p) => ({
      type: 'Feature' as const,
      id: p.id,
      geometry: {
        type: 'Point' as const,
        coordinates: [p.lon, p.lat],
      },
      properties: {
        id: p.id,
        name: p.name,
        apType: p.type,
        color: ACCESS_POINT_CONFIG[p.type].color,
        label: ACCESS_POINT_CONFIG[p.type].label,
        scale: ACCESS_POINT_CONFIG[p.type].scale,
        surface: p.surface ?? '',
        fee: p.fee != null ? (p.fee ? 'Fee required' : 'Free') : '',
        operator: p.operator ?? '',
      },
    })),
  };
}
