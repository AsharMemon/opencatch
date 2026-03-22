/**
 * Marine Gas Stations Service for OpenCatch.
 *
 * Fetches marine fuel docks from OpenStreetMap via Overpass API.
 * Queries for amenity=fuel with boat=yes or seamark:type=fuel_station.
 *
 * All data sources are free and require no API key.
 */

// ── Types ────────────────────────────────────────────────────────

export interface MarineGasStation {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Fuel types available, e.g. ['diesel', 'gasoline']. */
  fuelTypes: string[];
  /** Operating hours if known. */
  hours: string | null;
  /** Operator name. */
  operator: string | null;
}

export interface MarineGasBBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const REQUEST_TIMEOUT_MS = 20_000;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry {
  data: MarineGasStation[];
  timestamp: number;
}

const cache = new Map<string, CacheEntry>();

function bboxKey(bbox: MarineGasBBox): string {
  const r = (n: number) => Math.round(n * 100) / 100;
  return `fuel:${r(bbox.west)},${r(bbox.south)},${r(bbox.east)},${r(bbox.north)}`;
}

function getCached(key: string): MarineGasStation[] | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

// ── Overpass query ───────────────────────────────────────────────

function buildOverpassQuery(bbox: MarineGasBBox): string {
  return `[out:json][timeout:20];
(
  node["amenity"="fuel"]["boat"="yes"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
  node["seamark:type"="fuel_station"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
  way["amenity"="fuel"]["boat"="yes"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
  node["amenity"="fuel"]["waterway"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
  node["amenity"="fuel"]["seamark:type"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
);
out center body qt 200;`;
}

// ── Parse fuel types from OSM tags ──────────────────────────────

function parseFuelTypes(tags: Record<string, string>): string[] {
  const types: string[] = [];
  if (tags['fuel:diesel'] === 'yes') types.push('diesel');
  if (tags['fuel:gasoline'] === 'yes' || tags['fuel:octane_87'] === 'yes' || tags['fuel:octane_89'] === 'yes' || tags['fuel:octane_91'] === 'yes') {
    types.push('gasoline');
  }
  if (tags['fuel:e85'] === 'yes') types.push('E85');
  if (tags['fuel:lpg'] === 'yes') types.push('LPG');
  // If no specific tags, mark as unknown
  if (types.length === 0) types.push('fuel');
  return types;
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Fetch marine fuel docks within a bounding box from OpenStreetMap.
 */
export async function getMarineGasStations(bbox: MarineGasBBox): Promise<MarineGasStation[]> {
  const key = bboxKey(bbox);
  const cached = getCached(key);
  if (cached) return cached;

  const query = buildOverpassQuery(bbox);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(OVERPASS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `data=${encodeURIComponent(query)}`,
      signal: controller.signal,
    });

    if (!res.ok) return [];
    const data = await res.json();

    const stations: MarineGasStation[] = (data.elements ?? []).map((el: any) => {
      const lat = el.lat ?? el.center?.lat ?? 0;
      const lon = el.lon ?? el.center?.lon ?? 0;
      const tags = el.tags ?? {};

      return {
        id: `fuel-${el.id}`,
        name: tags.name ?? tags['seamark:name'] ?? 'Marine Fuel Dock',
        lat,
        lon,
        fuelTypes: parseFuelTypes(tags),
        hours: tags.opening_hours ?? null,
        operator: tags.operator ?? null,
      };
    });

    // Deduplicate by proximity (within 50m)
    const deduped = stations.filter((s, idx) => {
      for (let k = 0; k < idx; k++) {
        const dx = (s.lat - stations[k].lat) * 111000;
        const dy = (s.lon - stations[k].lon) * 111000 * Math.cos((s.lat * Math.PI) / 180);
        if (Math.sqrt(dx * dx + dy * dy) < 50) return false;
      }
      return true;
    });

    cache.set(key, { data: deduped, timestamp: Date.now() });
    return deduped;
  } catch (err) {
    console.warn('[marineGasStations] fetch failed:', err);
    return [];
  } finally {
    clearTimeout(timer);
  }
}

// ── GeoJSON helper ───────────────────────────────────────────────

export function marineGasToGeoJSON(stations: MarineGasStation[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: stations.map((s) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [s.lon, s.lat],
      },
      properties: {
        id: s.id,
        name: s.name,
        fuelTypes: s.fuelTypes.join(', '),
        hours: s.hours ?? 'Hours unknown',
        operator: s.operator ?? '',
      },
    })),
  };
}
