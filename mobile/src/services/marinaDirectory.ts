/**
 * Marina directory service for OpenCatch.
 *
 * Queries the OpenStreetMap Overpass API to find marinas, bait shops,
 * tackle shops, boat rentals, fishing piers, and boat ramps near a
 * given location. Results are cached in memory for 1 hour.
 */

import type { WaterbodyBounds } from './accessPointService';
import { getLocalLakeMarinaPois } from './lakePoiCatalog';

// ── Types ────────────────────────────────────────────────────────

/** The kind of point-of-interest returned by the directory. */
export type MarinaPOIType =
  | 'marina'
  | 'bait_shop'
  | 'boat_rental'
  | 'fishing_pier'
  | 'tackle_shop'
  | 'boat_ramp';

/** A single marina / fishing-related point of interest. */
export interface MarinaPOI {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: MarinaPOIType;
  phone?: string;
  website?: string;
  address?: string;
  amenities: string[];
  /** Populated when results are returned relative to a search origin. */
  distanceMiles?: number;
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URLS = [
  'https://overpass-api.de/api/interpreter',
  'https://lz4.overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
];
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry {
  data: MarinaPOI[];
  timestamp: number;
}

const cache = new Map<string, CacheEntry>();

function cacheKey(
  lat: number,
  lon: number,
  radiusMeters: number,
  bounds?: WaterbodyBounds,
  lakeId?: string,
): string {
  // Round coords to ~110 m so nearby requests share a cache slot.
  const latR = Math.round(lat * 1000) / 1000;
  const lonR = Math.round(lon * 1000) / 1000;
  const bboxKey = bounds
    ? `${Math.round(bounds.south * 1000) / 1000},${Math.round(bounds.west * 1000) / 1000},${Math.round(bounds.north * 1000) / 1000},${Math.round(bounds.east * 1000) / 1000}`
    : 'no-bounds';
  return `${latR},${lonR},${radiusMeters},${bboxKey},${lakeId ?? 'no-lake'}`;
}

function getCached(key: string): MarinaPOI[] | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

function setCache(key: string, data: MarinaPOI[]): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── Overpass helpers ─────────────────────────────────────────────

/**
 * Build an Overpass QL query that searches for all fishing-related
 * POI types within `radiusMeters` of `(lat, lon)`.
 */
function buildOverpassQuery(
  lat: number,
  lon: number,
  radiusMeters: number,
  bounds?: WaterbodyBounds,
): string {
  const around = bounds
    ? (() => {
        const latPad = radiusMeters / 111_320;
        const lonPad = radiusMeters / (111_320 * Math.cos((lat * Math.PI) / 180));
        return `(${bounds.south - latPad},${bounds.west - lonPad},${bounds.north + latPad},${bounds.east + lonPad})`;
      })()
    : `(around:${radiusMeters},${lat},${lon})`;
  return `
[out:json][timeout:25];
(
  node["leisure"="marina"]${around};
  way["leisure"="marina"]${around};
  node["seamark:type"="harbour"]${around};
  way["seamark:type"="harbour"]${around};
  node["harbour"="yes"]${around};
  way["harbour"="yes"]${around};
  node["shop"="fishing"]${around};
  node["shop"="bait"]${around};
  node["shop"="tackle"]${around};
  node["shop"="boat"]${around};
  node["amenity"="boat_rental"]${around};
  node["amenity"="boat_sharing"]${around};
  node["leisure"="fishing"]${around};
  way["leisure"="fishing"]${around};
  node["man_made"="pier"]["fishing"="yes"]${around};
  way["man_made"="pier"]["fishing"="yes"]${around};
  node["leisure"="slipway"]${around};
  way["leisure"="slipway"]${around};
  node["waterway"="dock"]${around};
  way["waterway"="dock"]${around};
);
out center body;
`.trim();
}

/**
 * Build an Overpass QL query that fetches a single element by its
 * numeric OSM id (prefixed with "node/", "way/", or "relation/").
 */
function buildDetailQuery(osmId: string): string {
  const [elementType, numericId] = osmId.split('/');
  if (!elementType || !numericId) {
    throw new Error(`Invalid osmId format "${osmId}". Expected "node/123" or "way/456".`);
  }
  return `
[out:json][timeout:10];
${elementType}(${numericId});
out center body;
`.trim();
}

// ── Tag → type mapping ──────────────────────────────────────────

function classifyElement(tags: Record<string, string>): MarinaPOIType {
  if (tags.leisure === 'marina') return 'marina';
  if (tags['seamark:type'] === 'harbour' || tags.harbour === 'yes') return 'marina';
  if (tags.leisure === 'slipway') return 'boat_ramp';
  if (tags.amenity === 'boat_rental' || tags.amenity === 'boat_sharing') return 'boat_rental';
  if (tags.shop === 'fishing' || tags.shop === 'tackle') return 'tackle_shop';
  if (tags.shop === 'bait') return 'bait_shop';
  if (tags.man_made === 'pier') return 'fishing_pier';
  if (tags.leisure === 'fishing') return 'fishing_pier';
  return 'marina'; // fallback
}

/** Extract human-facing amenities from OSM tags. */
function extractAmenities(tags: Record<string, string>): string[] {
  const amenities: string[] = [];
  if (tags.fuel === 'yes') amenities.push('Fuel');
  if (tags.toilets === 'yes' || tags.sanitary_dump_station === 'yes') amenities.push('Restrooms');
  if (tags.drinking_water === 'yes') amenities.push('Drinking water');
  if (tags.power_supply === 'yes' || tags.electricity === 'yes') amenities.push('Shore power');
  if (tags.slipway === 'yes' || tags.leisure === 'slipway') amenities.push('Boat ramp');
  if (tags.pump_out === 'yes') amenities.push('Pump-out');
  if (tags.repair === 'yes') amenities.push('Repair');
  if (tags.parking === 'yes' || tags.parking === 'surface') amenities.push('Parking');
  if (tags.ice === 'yes') amenities.push('Ice');
  if (tags.wifi === 'yes' || tags.internet_access === 'yes') amenities.push('Wi-Fi');
  if (tags.shop === 'bait' || tags.bait === 'yes') amenities.push('Live bait');
  if (tags.shop === 'boat') amenities.push('Boat shop');
  if (tags.boat_rental === 'yes' || tags.amenity === 'boat_rental' || tags.amenity === 'boat_sharing') amenities.push('Boat rental');
  return amenities;
}

/** Build a one-line address from OSM addr:* tags when available. */
function buildAddress(tags: Record<string, string>): string | undefined {
  const parts = [
    tags['addr:housenumber'],
    tags['addr:street'],
    tags['addr:city'],
    tags['addr:state'],
    tags['addr:postcode'],
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' ') : undefined;
}

// ── Element → MarinaPOI ─────────────────────────────────────────

interface OverpassElement {
  type: string;
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
}

function elementToPOI(el: OverpassElement): MarinaPOI | null {
  const tags = el.tags ?? {};
  const lat = el.lat ?? el.center?.lat;
  const lon = el.lon ?? el.center?.lon;
  if (lat == null || lon == null) return null;

  return {
    id: `${el.type}/${el.id}`,
    name: tags.name || tags['name:en'] || classifyElement(tags),
    lat,
    lon,
    type: classifyElement(tags),
    phone: tags.phone || tags['contact:phone'] || undefined,
    website: tags.website || tags['contact:website'] || undefined,
    address: buildAddress(tags),
    amenities: extractAmenities(tags),
  };
}

// ── Overpass fetch wrapper ───────────────────────────────────────

async function runOverpassQuery(query: string): Promise<OverpassElement[]> {
  let lastError: Error | null = null;

  for (const endpoint of OVERPASS_URLS) {
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `data=${encodeURIComponent(query)}`,
      });

      if (!response.ok) {
        lastError = new Error(`Overpass API error: ${response.status} ${response.statusText}`);
        continue;
      }

      const json = await response.json();
      return (json.elements ?? []) as OverpassElement[];
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  throw lastError ?? new Error('Overpass API failed');
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch all marina-related POIs within `radiusMeters` of the given
 * coordinates. Results are cached for 1 hour.
 *
 * @param lat      Latitude of the search centre.
 * @param lon      Longitude of the search centre.
 * @param radiusMeters  Search radius in metres (default 25 km).
 * @returns Sorted array of {@link MarinaPOI} (nearest first).
 */
export async function fetchNearbyMarinas(
  lat: number,
  lon: number,
  radiusMeters: number = 25_000,
  bounds?: WaterbodyBounds,
  lakeId?: string,
): Promise<MarinaPOI[]> {
  const key = cacheKey(lat, lon, radiusMeters, bounds, lakeId);
  const cached = getCached(key);
  if (cached) return attachDistances(cached, lat, lon);
  const localPois = getLocalLakeMarinaPois(lakeId, lat, lon);

  try {
    const query = buildOverpassQuery(lat, lon, radiusMeters, bounds);
    const elements = await runOverpassQuery(query);
    const pois = elements
      .map(elementToPOI)
      .filter((p): p is MarinaPOI => p !== null);

    // De-duplicate by id (ways can duplicate node results).
    const seen = new Set<string>();
    const unique = [...localPois, ...pois].filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });

    setCache(key, unique);
    return attachDistances(unique, lat, lon);
  } catch (err) {
    console.warn('[marinaDirectory] fetchNearbyMarinas failed:', err);
    return attachDistances(localPois, lat, lon);
  }
}

/**
 * Fetch detailed information about a single OSM element.
 *
 * @param osmId  Element identifier in "type/id" format, e.g. "node/12345".
 * @returns The {@link MarinaPOI} or `null` if not found.
 */
export async function fetchMarinaDetails(
  osmId: string,
): Promise<MarinaPOI | null> {
  try {
    const query = buildDetailQuery(osmId);
    const elements = await runOverpassQuery(query);
    if (elements.length === 0) return null;
    return elementToPOI(elements[0]);
  } catch (err) {
    console.warn('[marinaDirectory] fetchMarinaDetails failed:', err);
    return null;
  }
}

/**
 * Convenience wrapper: fetch nearby POIs and filter to a single type.
 *
 * @param lat      Latitude of the search centre.
 * @param lon      Longitude of the search centre.
 * @param type     The {@link MarinaPOIType} to filter by.
 * @param radiusMeters  Search radius in metres (default 25 km).
 */
export async function getNearbyByType(
  lat: number,
  lon: number,
  type: MarinaPOIType,
  radiusMeters: number = 25_000,
  lakeId?: string,
): Promise<MarinaPOI[]> {
  const all = await fetchNearbyMarinas(lat, lon, radiusMeters, undefined, lakeId);
  return all.filter((p) => p.type === type);
}

/**
 * Format an amenities array into a human-readable string.
 *
 * @example
 * formatAmenities(['Fuel', 'Restrooms', 'Ice'])
 * // => "Fuel, Restrooms, Ice"
 */
export function formatAmenities(amenities: string[]): string {
  if (amenities.length === 0) return 'No amenities listed';
  return amenities.join(', ');
}

/**
 * Compute the great-circle distance between two points using the
 * Haversine formula.
 *
 * @returns Distance in **miles**.
 */
export function haversineDistance(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 3958.8; // Earth radius in miles
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

// ── Internal helpers ─────────────────────────────────────────────

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/** Attach `distanceMiles` to every POI and sort nearest-first. */
function attachDistances(
  pois: MarinaPOI[],
  originLat: number,
  originLon: number,
): MarinaPOI[] {
  return pois
    .map((p) => ({
      ...p,
      distanceMiles: haversineDistance(originLat, originLon, p.lat, p.lon),
    }))
    .sort((a, b) => (a.distanceMiles ?? 0) - (b.distanceMiles ?? 0));
}
