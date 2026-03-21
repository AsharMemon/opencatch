/**
 * Canadian Public Lands Service for OpenCatch
 *
 * Canada has no single equivalent to PAD-US. Instead, public fishing
 * access comes from multiple sources:
 *
 * 1. National Parks — Parks Canada (federal)
 * 2. Provincial Parks — each province manages its own parks
 * 3. Crown Land — vast public land open to fishing (varies by province)
 * 4. Conservation Areas — managed by conservation authorities
 * 5. Protected Areas — CPCAD (Canadian Protected and Conserved Areas Database)
 *
 * Data source: OpenStreetMap Overpass API for park/protected area boundaries.
 * This avoids needing to host large GIS files and provides pan-Canada coverage.
 *
 * CPCAD (Environment Canada) is available as WMS/ESRI but not as a simple
 * GeoJSON API, so we use OSM which has excellent Canadian park coverage.
 *
 * All endpoints are free, anonymous, no API key required.
 * Overpass API fair-use: max ~10,000 requests/day, keep bbox reasonable.
 */

// ── Types ────────────────────────────────────────────────────────

export interface PublicLandArea {
  /** OSM relation or way ID. */
  id: string;
  /** Human-readable name. */
  name: string;
  /** Type of public land. */
  type: PublicLandType;
  /** IUCN protection class (1-6) if tagged. */
  protectClass?: string;
  /** Operator or manager (e.g. "Parks Canada", "Ontario Parks"). */
  operator?: string;
  /** Whether fishing access is known to be available. */
  fishingAccess?: 'yes' | 'no' | 'unknown';
  /** Province code (if determinable). */
  province?: string;
  /** Website URL for the area. */
  website?: string;
  /** Bounding box [minLon, minLat, maxLon, maxLat]. */
  bbox?: [number, number, number, number];
}

export type PublicLandType =
  | 'national_park'
  | 'provincial_park'
  | 'conservation_area'
  | 'crown_land'
  | 'wildlife_area'
  | 'protected_area'
  | 'recreation_area'
  | 'park';

// ── Configuration ────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const REQUEST_TIMEOUT = 30_000;

/** Cache TTL: 1 hour for park boundary queries. */
const CACHE_TTL = 60 * 60 * 1000;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<unknown>>();

function getCached<T>(key: string): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL) {
    cache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

export function clearPublicLandsCache(): void {
  cache.clear();
}

// ── Overpass Query Helper ────────────────────────────────────────

/**
 * Execute an Overpass QL query and return the raw elements.
 * @internal
 */
async function overpassQuery(query: string): Promise<any[]> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const res = await fetch(OVERPASS_URL, {
      method: 'POST',
      body: `data=${encodeURIComponent(query)}`,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      throw new Error(`Overpass API ${res.status}`);
    }

    const data = await res.json();
    return data.elements || [];
  } catch (err: any) {
    clearTimeout(timeout);
    if (err.name === 'AbortError') {
      throw new Error(`Overpass API timed out after ${REQUEST_TIMEOUT}ms`);
    }
    throw err;
  }
}

// ── Type Classification ──────────────────────────────────────────

/**
 * Classify an OSM element's tags into a PublicLandType.
 * @internal
 */
function classifyLandType(tags: Record<string, string>): PublicLandType {
  const name = (tags.name || '').toLowerCase();
  const boundary = tags.boundary || '';
  const leisure = tags.leisure || '';
  const designation = (tags.designation || '').toLowerCase();
  const operator = (tags.operator || '').toLowerCase();

  if (boundary === 'national_park' || name.includes('national park') || name.includes('parc national')) {
    return 'national_park';
  }
  if (name.includes('provincial park') || name.includes('parc provincial') || designation.includes('provincial park')) {
    return 'provincial_park';
  }
  if (name.includes('conservation') || name.includes('conserv')) {
    return 'conservation_area';
  }
  if (name.includes('wildlife') || name.includes('faune') || boundary === 'protected_area') {
    return 'wildlife_area';
  }
  if (name.includes('recreation') || name.includes('récréatif')) {
    return 'recreation_area';
  }
  if (operator.includes('parks canada') || operator.includes('parcs canada')) {
    return 'national_park';
  }

  return 'park';
}

/**
 * Infer province from operator or name tags.
 * @internal
 */
function inferProvince(tags: Record<string, string>): string | undefined {
  const operator = (tags.operator || '').toLowerCase();
  const name = (tags.name || '').toLowerCase();
  const combined = `${operator} ${name}`;

  const provincePatterns: [string, string][] = [
    ['ontario', 'ON'],
    ['british columbia', 'BC'],
    ['alberta', 'AB'],
    ['saskatchewan', 'SK'],
    ['manitoba', 'MB'],
    ['quebec', 'QC'],
    ['québec', 'QC'],
    ['nova scotia', 'NS'],
    ['new brunswick', 'NB'],
    ['newfoundland', 'NL'],
    ['prince edward', 'PE'],
    ['yukon', 'YT'],
    ['northwest territories', 'NT'],
    ['nunavut', 'NU'],
  ];

  for (const [pattern, code] of provincePatterns) {
    if (combined.includes(pattern)) return code;
  }

  return undefined;
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get public lands (parks, protected areas) near a Canadian location.
 *
 * Queries the OpenStreetMap Overpass API for national parks, provincial
 * parks, conservation areas, and other protected areas within a bounding
 * box around the given coordinates.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @param radiusKm - Search radius in km (default 50).
 * @returns Array of public land areas sorted by type (national first).
 *
 * @example
 * ```ts
 * const lands = await getCanadianPublicLands(45.42, -75.69, 30);
 * lands.forEach(l => console.log(l.name, l.type));
 * ```
 */
export async function getCanadianPublicLands(
  lat: number,
  lon: number,
  radiusKm = 50,
): Promise<PublicLandArea[]> {
  const cacheKey = `ca-lands:${lat.toFixed(2)},${lon.toFixed(2)},${radiusKm}`;
  const cached = getCached<PublicLandArea[]>(cacheKey);
  if (cached) return cached;

  try {
    const dLat = radiusKm / 111;
    const dLon = radiusKm / (111 * Math.cos((lat * Math.PI) / 180));
    const bbox = `${lat - dLat},${lon - dLon},${lat + dLat},${lon + dLon}`;

    const query = `
      [out:json][timeout:25];
      (
        relation["boundary"="national_park"](${bbox});
        relation["boundary"="protected_area"](${bbox});
        relation["leisure"="nature_reserve"](${bbox});
        way["boundary"="national_park"](${bbox});
        way["boundary"="protected_area"](${bbox});
        way["leisure"="nature_reserve"](${bbox});
        relation["leisure"="park"]["name"](${bbox});
        way["leisure"="park"]["name"](${bbox});
      );
      out tags;
    `;

    const elements = await overpassQuery(query);

    const results: PublicLandArea[] = elements
      .filter((e: any) => e.tags?.name) // skip unnamed areas
      .map((e: any) => {
        const tags = e.tags || {};
        return {
          id: `osm:${e.type}/${e.id}`,
          name: tags.name,
          type: classifyLandType(tags),
          protectClass: tags.protect_class,
          operator: tags.operator,
          fishingAccess: tags.fishing === 'yes' ? 'yes' as const
            : tags.fishing === 'no' ? 'no' as const
            : 'unknown' as const,
          province: inferProvince(tags),
          website: tags.website || tags['contact:website'],
        };
      });

    // Deduplicate by name (OSM can have both relation and way for same area)
    const seen = new Set<string>();
    const deduped = results.filter((r) => {
      const key = r.name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    // Sort: national parks first, then provincial, then others
    const typeOrder: Record<PublicLandType, number> = {
      national_park: 0,
      provincial_park: 1,
      conservation_area: 2,
      wildlife_area: 3,
      protected_area: 4,
      recreation_area: 5,
      crown_land: 6,
      park: 7,
    };

    deduped.sort((a, b) => (typeOrder[a.type] ?? 99) - (typeOrder[b.type] ?? 99));

    setCache(cacheKey, deduped);
    return deduped;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch Canadian public lands:', err);
    return [];
  }
}

/**
 * Known Crown Land information by province.
 *
 * Crown land access varies dramatically by province. This provides
 * high-level guidance for anglers about where they can fish on
 * public land in each province.
 *
 * Note: Crown land boundaries are not readily available as open data
 * GeoJSON for most provinces. Ontario is the best with CLUPA data.
 */
export const CROWN_LAND_INFO: Record<string, {
  description: string;
  accessUrl: string;
  generallyOpenToFishing: boolean;
  notes: string[];
}> = {
  ON: {
    description: 'Ontario Crown Land — ~87% of Ontario is Crown land',
    accessUrl: 'https://www.ontario.ca/page/crown-land-use-policy-atlas',
    generallyOpenToFishing: true,
    notes: [
      'Use the Crown Land Use Policy Atlas (CLUPA) to find fishable Crown land',
      'Most unoccupied Crown land is open to recreational fishing',
      'Some areas restricted near mines, parks, or First Nations reserves',
      'Free camping on Crown land for up to 21 days in one spot',
    ],
  },
  BC: {
    description: 'British Columbia Crown Land — ~94% of BC is Crown land',
    accessUrl: 'https://www2.gov.bc.ca/gov/content/data/geographic-data-services/land-use/crown-land-registry',
    generallyOpenToFishing: true,
    notes: [
      'Most Crown land is open to recreational fishing with valid licence',
      'Some areas are classified waters requiring additional surcharge',
      'Use iMapBC for Crown land mapping',
      'BC has the most Crown land of any province',
    ],
  },
  AB: {
    description: 'Alberta Crown Land — ~60% of Alberta is Crown land',
    accessUrl: 'https://www.alberta.ca/access-public-land',
    generallyOpenToFishing: true,
    notes: [
      'Public Land Use Zones (PLUZs) have specific access rules',
      'Eastern Slopes and Foothills are popular fishing Crown land',
      'Check for industrial leases before accessing',
    ],
  },
  SK: {
    description: 'Saskatchewan Crown Land',
    accessUrl: 'https://www.saskatchewan.ca/residents/environment-public-health-and-safety/crown-resource-land',
    generallyOpenToFishing: true,
    notes: [
      'Northern Crown land is vast and largely accessible',
      'Southern agricultural Crown land may have restricted access',
      'Provincial forest areas open to fishing',
    ],
  },
  MB: {
    description: 'Manitoba Crown Land',
    accessUrl: 'https://www.gov.mb.ca/sd/parks/education_outreach/crown_land.html',
    generallyOpenToFishing: true,
    notes: [
      'Crown land in northern Manitoba is abundant',
      'Southern areas may have more restricted access',
      'Provincial forest reserves open to recreational use',
    ],
  },
  QC: {
    description: 'Quebec Public Land (terres du domaine de l\'etat)',
    accessUrl: 'https://www.quebec.ca/en/tourism-and-recreation/sporting-and-outdoor-activities/controlled-harvesting-zones-zecs',
    generallyOpenToFishing: true,
    notes: [
      'ZECs (controlled harvesting zones) require separate access fees',
      'Outfitter territories (pourvoiries) may have exclusive fishing rights',
      'Free public land fishing available outside ZECs/outfitters',
      'SEPAQ parks require park entry fee plus fishing licence',
    ],
  },
  NB: {
    description: 'New Brunswick Crown Land — ~48% of NB',
    accessUrl: 'https://www2.gnb.ca/content/gnb/en/departments/erd/natural_resources/content/CrownLandsForests.html',
    generallyOpenToFishing: true,
    notes: [
      'Crown reserve waters require additional daily permit',
      'Most Crown land is open to fishing',
      'Industrial freehold land may look like Crown land — verify',
    ],
  },
  NS: {
    description: 'Nova Scotia Crown Land — ~29% of NS',
    accessUrl: 'https://novascotia.ca/natr/land/',
    generallyOpenToFishing: true,
    notes: [
      'Smaller percentage of Crown land than western provinces',
      'Many lakes and rivers accessible from road allowances',
      'Provincial parks and nature reserves have fishing access',
    ],
  },
};

/**
 * Get Crown land information for a province.
 *
 * @param provinceCode - Two-letter province code (e.g. "ON", "BC").
 * @returns Crown land info or undefined if not available.
 */
export function getCrownLandInfo(provinceCode: string): typeof CROWN_LAND_INFO[string] | undefined {
  return CROWN_LAND_INFO[provinceCode.toUpperCase()];
}
