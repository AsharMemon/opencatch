/**
 * Coastal Fishing Spots Service for OpenCatch.
 *
 * Provides saltwater species identification, reef fishing spots,
 * inshore structure (jetties, piers, bridges, seawalls), offshore
 * ledge detection from GEBCO bathymetry, and surf fishing beach
 * access points from OpenStreetMap.
 *
 * All data sources are free and require no API key.
 */

// ── Types ────────────────────────────────────────────────────────

/** Bounding box for spatial queries (matches coastalCharts). */
export interface CoastalBBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** A saltwater fish species with regional/seasonal likelihood. */
export interface SaltwaterSpecies {
  /** Common name (e.g. "Red Drum"). */
  name: string;
  /** Scientific name. */
  scientificName: string;
  /** Region codes where this species is found. */
  regions: CoastalRegion[];
  /** Months when this species is most active (1-12). */
  peakMonths: number[];
  /** Preferred SST range in Fahrenheit [min, max]. */
  preferredTempF: [number, number];
  /** Primary habitat description. */
  habitat: string;
  /** Common techniques for catching this species. */
  techniques: string[];
  /** Minimum size limit note (varies by state). */
  sizeNote?: string;
}

/** US coastal region identifiers. */
export type CoastalRegion =
  | 'northeast'    // Maine to NJ
  | 'midAtlantic'  // VA to NC
  | 'southeast'    // SC to FL east coast
  | 'gulf'         // FL west coast to TX
  | 'pacific'      // CA to WA
  | 'alaska'
  | 'hawaii';

/** An artificial or natural reef fishing spot. */
export interface ReefSpot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Whether artificial or natural. */
  type: 'artificial' | 'natural';
  /** Approximate depth in feet. */
  depthFt: number | null;
  /** Description or material info. */
  description?: string;
  /** Source: state program, NOAA, etc. */
  source: string;
}

/** An inshore fishing structure (jetty, pier, bridge, seawall). */
export interface InshoreSpot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: 'jetty' | 'pier' | 'bridge' | 'seawall' | 'dock' | 'groin';
  /** Whether public access is confirmed. */
  publicAccess: boolean;
  /** Distance from queried center in miles. */
  distanceMiles?: number;
}

/** An offshore ledge or depth contour break. */
export interface OffshoreLedge {
  id: string;
  /** Human-readable name or depth description. */
  name: string;
  /** Center latitude of the feature. */
  lat: number;
  /** Center longitude of the feature. */
  lon: number;
  /** Shallow side depth in feet. */
  shallowDepthFt: number;
  /** Deep side depth in feet. */
  deepDepthFt: number;
  /** Approximate length of the ledge in nautical miles. */
  lengthNm?: number;
}

/** A beach or shore access point for surf fishing. */
export interface SurfAccessPoint {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Type of access. */
  type: 'beach_access' | 'parking' | 'walkover' | 'ramp';
  /** Whether vehicle access is allowed on the beach. */
  vehicleAccess: boolean;
  /** Additional notes (fees, hours, permits). */
  notes?: string;
}

// ── Constants ────────────────────────────────────────────────────

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 20_000;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<any>>();

function bboxKey(prefix: string, bbox: CoastalBBox): string {
  const r = (n: number) => Math.round(n * 100) / 100;
  return `${prefix}:${r(bbox.west)},${r(bbox.south)},${r(bbox.east)},${r(bbox.north)}`;
}

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

async function fetchText(url: string, body?: string): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const opts: RequestInit = {
      signal: controller.signal,
      headers: { 'User-Agent': USER_AGENT },
    };
    if (body) {
      opts.method = 'POST';
      opts.body = body;
      opts.headers = { ...opts.headers, 'Content-Type': 'application/x-www-form-urlencoded' };
    }
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
    return await res.text();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJSON<T>(url: string): Promise<T> {
  const text = await fetchText(url);
  return JSON.parse(text) as T;
}

// ── Haversine distance (miles) ───────────────────────────────────

function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8; // Earth radius in miles
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Region detection ─────────────────────────────────────────────

function detectRegion(lat: number, lon: number): CoastalRegion {
  // Alaska
  if (lat > 54 && lon < -130) return 'alaska';
  // Hawaii
  if (lat > 18 && lat < 23 && lon > -162 && lon < -154) return 'hawaii';
  // Pacific coast
  if (lon < -115) return 'pacific';
  // Gulf coast: south of ~31N and west of ~80W
  if (lat < 31 && lon > -98 && lon < -80) return 'gulf';
  // Southeast: SC, GA, FL east coast
  if (lat < 35 && lon >= -82 && lon < -75) return 'southeast';
  // Mid-Atlantic: VA to NC
  if (lat >= 34 && lat < 40 && lon >= -77 && lon < -70) return 'midAtlantic';
  // Northeast
  return 'northeast';
}

// ── Saltwater Species Database ───────────────────────────────────

const SPECIES_DB: SaltwaterSpecies[] = [
  {
    name: 'Red Drum',
    scientificName: 'Sciaenops ocellatus',
    regions: ['southeast', 'gulf', 'midAtlantic'],
    peakMonths: [3, 4, 5, 9, 10, 11],
    preferredTempF: [65, 85],
    habitat: 'Inshore marshes, flats, jetties, nearshore reefs',
    techniques: ['live bait', 'cut bait', 'soft plastics', 'topwater', 'fly'],
  },
  {
    name: 'Spotted Seatrout',
    scientificName: 'Cynoscion nebulosus',
    regions: ['southeast', 'gulf'],
    peakMonths: [3, 4, 5, 10, 11],
    preferredTempF: [60, 82],
    habitat: 'Grass flats, oyster bars, channels, piers',
    techniques: ['live shrimp', 'soft plastics', 'topwater', 'jigs'],
  },
  {
    name: 'Snook',
    scientificName: 'Centropomus undecimalis',
    regions: ['southeast', 'gulf'],
    peakMonths: [5, 6, 7, 8, 9],
    preferredTempF: [70, 88],
    habitat: 'Mangroves, jetties, bridges, inlets, beaches',
    techniques: ['live bait', 'jerkbaits', 'soft plastics', 'topwater'],
  },
  {
    name: 'Striped Bass',
    scientificName: 'Morone saxatilis',
    regions: ['northeast', 'midAtlantic'],
    peakMonths: [4, 5, 6, 9, 10, 11],
    preferredTempF: [55, 68],
    habitat: 'Surf, jetties, estuaries, river mouths, rocky shorelines',
    techniques: ['live eels', 'bunker', 'plugs', 'jigs', 'fly'],
  },
  {
    name: 'Mahi-Mahi',
    scientificName: 'Coryphaena hippurus',
    regions: ['southeast', 'gulf', 'midAtlantic', 'hawaii'],
    peakMonths: [4, 5, 6, 7, 8, 9],
    preferredTempF: [74, 84],
    habitat: 'Offshore weedlines, floating debris, current edges',
    techniques: ['trolling', 'live bait', 'cut bait', 'casting'],
  },
  {
    name: 'King Mackerel',
    scientificName: 'Scomberomorus cavalla',
    regions: ['southeast', 'gulf', 'midAtlantic'],
    peakMonths: [3, 4, 5, 10, 11],
    preferredTempF: [68, 82],
    habitat: 'Nearshore reefs, wrecks, piers, passes',
    techniques: ['slow trolling', 'live bait', 'fast trolling', 'drifting'],
  },
  {
    name: 'Flounder',
    scientificName: 'Paralichthys spp.',
    regions: ['northeast', 'midAtlantic', 'southeast', 'gulf'],
    peakMonths: [3, 4, 5, 9, 10, 11],
    preferredTempF: [55, 75],
    habitat: 'Sandy bottoms, inlets, channels, structure edges',
    techniques: ['live bait', 'jigs', 'bucktails', 'drifting'],
  },
  {
    name: 'Yellowfin Tuna',
    scientificName: 'Thunnus albacares',
    regions: ['gulf', 'southeast', 'midAtlantic', 'hawaii'],
    peakMonths: [5, 6, 7, 8, 9],
    preferredTempF: [72, 82],
    habitat: 'Offshore, oil rigs, seamounts, current edges',
    techniques: ['trolling', 'chunking', 'popping', 'jigging'],
  },
  {
    name: 'Lingcod',
    scientificName: 'Ophiodon elongatus',
    regions: ['pacific', 'alaska'],
    peakMonths: [3, 4, 5, 6, 9, 10],
    preferredTempF: [46, 58],
    habitat: 'Rocky reefs, kelp beds, pinnacles',
    techniques: ['jigs', 'swimbaits', 'live bait', 'bottom fishing'],
  },
  {
    name: 'Rockfish',
    scientificName: 'Sebastes spp.',
    regions: ['pacific', 'alaska'],
    peakMonths: [4, 5, 6, 7, 8, 9, 10],
    preferredTempF: [48, 60],
    habitat: 'Rocky structure, kelp forests, deep reefs',
    techniques: ['bottom fishing', 'jigs', 'cut bait', 'shrimp flies'],
  },
  {
    name: 'Pacific Halibut',
    scientificName: 'Hippoglossus stenolepis',
    regions: ['pacific', 'alaska'],
    peakMonths: [5, 6, 7, 8],
    preferredTempF: [40, 55],
    habitat: 'Sandy/muddy bottoms, deep channels, ledges',
    techniques: ['herring', 'jigs', 'spreader bars', 'drifting'],
  },
  {
    name: 'Bluefish',
    scientificName: 'Pomatomus saltatrix',
    regions: ['northeast', 'midAtlantic', 'southeast'],
    peakMonths: [5, 6, 7, 8, 9, 10],
    preferredTempF: [60, 78],
    habitat: 'Surf, inlets, bays, offshore, near baitfish schools',
    techniques: ['metal lures', 'plugs', 'cut bait', 'trolling'],
  },
  {
    name: 'Cobia',
    scientificName: 'Rachycentron canadum',
    regions: ['southeast', 'gulf'],
    peakMonths: [3, 4, 5, 6, 7],
    preferredTempF: [68, 86],
    habitat: 'Buoys, wrecks, rays, floating structure, nearshore',
    techniques: ['sight casting', 'live bait', 'jigs', 'eels'],
  },
  {
    name: 'Sheepshead',
    scientificName: 'Archosargus probatocephalus',
    regions: ['southeast', 'gulf', 'midAtlantic'],
    peakMonths: [1, 2, 3, 11, 12],
    preferredTempF: [55, 75],
    habitat: 'Pilings, bridges, jetties, oyster bars',
    techniques: ['fiddler crabs', 'shrimp', 'barnacle scraping', 'light tackle'],
  },
];

/**
 * Get saltwater species likely at a location based on region and
 * current season. Optionally filter by SST if provided.
 */
export function getSaltWaterSpecies(
  lat: number,
  lon: number,
  options?: { sstF?: number; month?: number },
): SaltwaterSpecies[] {
  const region = detectRegion(lat, lon);
  const month = options?.month ?? new Date().getMonth() + 1;
  const sst = options?.sstF;

  return SPECIES_DB.filter((sp) => {
    if (!sp.regions.includes(region)) return false;
    // Check if current month is a peak month (or within 1 month).
    const inSeason = sp.peakMonths.some(
      (pm) => Math.abs(pm - month) <= 1 || Math.abs(pm - month) >= 11,
    );
    if (!inSeason) return false;
    // If SST is provided, filter by preferred temperature range (with tolerance).
    if (sst != null) {
      if (sst < sp.preferredTempF[0] - 5 || sst > sp.preferredTempF[1] + 5) return false;
    }
    return true;
  });
}

export function getAllSaltWaterSpecies(): SaltwaterSpecies[] {
  return [...SPECIES_DB];
}

// ── Reef Fishing Spots ───────────────────────────────────────────

/**
 * Fetch both artificial and natural reef fishing spots within a
 * bounding box. Natural reefs are detected from OSM data tagged as
 * natural=reef or geological=reef.
 */
export async function getReefFishingSpots(bbox: CoastalBBox): Promise<ReefSpot[]> {
  const key = bboxKey('reefspots', bbox);
  const cached = getCached<ReefSpot[]>(key);
  if (cached) return cached;

  // Query OSM for natural reefs.
  const query = `
    [out:json][timeout:20];
    (
      node["natural"="reef"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      way["natural"="reef"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      node["geological"="reef"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
    );
    out center body qt 200;
  `;

  try {
    const text = await fetchText(OVERPASS_URL, `data=${encodeURIComponent(query)}`);
    const data = JSON.parse(text) as { elements: Array<{ id: number; lat?: number; lon?: number; center?: { lat: number; lon: number }; tags?: Record<string, string> }> };

    const results: ReefSpot[] = (data.elements ?? []).map((el) => {
      const lat = el.lat ?? el.center?.lat ?? 0;
      const lon = el.lon ?? el.center?.lon ?? 0;
      return {
        id: `osm-reef-${el.id}`,
        name: el.tags?.name ?? 'Natural Reef',
        lat,
        lon,
        type: 'natural' as const,
        depthFt: null,
        description: el.tags?.description ?? undefined,
        source: 'OpenStreetMap',
      };
    });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalFishing] getReefFishingSpots failed:', err);
    return [];
  }
}

// ── Inshore Spots (jetties, piers, bridges, seawalls) ────────────

/**
 * Fetch inshore fishing structures from OpenStreetMap: jetties,
 * piers, bridges over water, seawalls, and groins.
 */
export async function getInshoreSpots(bbox: CoastalBBox): Promise<InshoreSpot[]> {
  const key = bboxKey('inshore', bbox);
  const cached = getCached<InshoreSpot[]>(key);
  if (cached) return cached;

  const query = `
    [out:json][timeout:20];
    (
      way["man_made"="pier"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      way["man_made"="jetty"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      way["man_made"="groyne"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      way["man_made"="breakwater"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      node["leisure"="fishing"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
    );
    out center body qt 300;
  `;

  try {
    const text = await fetchText(OVERPASS_URL, `data=${encodeURIComponent(query)}`);
    const data = JSON.parse(text) as { elements: Array<{ id: number; lat?: number; lon?: number; center?: { lat: number; lon: number }; tags?: Record<string, string> }> };

    const centerLat = (bbox.south + bbox.north) / 2;
    const centerLon = (bbox.west + bbox.east) / 2;

    const results: InshoreSpot[] = (data.elements ?? []).map((el) => {
      const lat = el.lat ?? el.center?.lat ?? 0;
      const lon = el.lon ?? el.center?.lon ?? 0;
      const manMade = el.tags?.man_made ?? '';

      let type: InshoreSpot['type'] = 'pier';
      if (manMade === 'jetty') type = 'jetty';
      else if (manMade === 'groyne') type = 'groin';
      else if (manMade === 'breakwater') type = 'seawall';
      else if (el.tags?.leisure === 'fishing') type = 'dock';

      return {
        id: `osm-inshore-${el.id}`,
        name: el.tags?.name ?? `${type.charAt(0).toUpperCase() + type.slice(1)}`,
        lat,
        lon,
        type,
        publicAccess: el.tags?.access !== 'private',
        distanceMiles: haversineMiles(centerLat, centerLon, lat, lon),
      };
    });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalFishing] getInshoreSpots failed:', err);
    return [];
  }
}

// ── Offshore Ledges (GEBCO depth contour breaks) ─────────────────

/**
 * Identify offshore ledge/depth-break features within a bounding
 * box. Uses GEBCO bathymetric contour analysis to find significant
 * depth changes that attract pelagic fish.
 *
 * This is a simplified heuristic: we identify areas where depth
 * contours are closely spaced (indicating a steep drop-off).
 */
export async function getOffshoreLedges(bbox: CoastalBBox): Promise<OffshoreLedge[]> {
  const key = bboxKey('ledges', bbox);
  const cached = getCached<OffshoreLedge[]>(key);
  if (cached) return cached;

  // GEBCO WMS GetFeatureInfo for depth at grid points.
  const GEBCO_WMS = 'https://www.gebco.net/data_and_products/gebco_web_services/web_map_service/mapserv';

  const latStep = (bbox.north - bbox.south) / 5;
  const lonStep = (bbox.east - bbox.west) / 5;
  const depths: Array<{ lat: number; lon: number; depthM: number }> = [];

  // Sample a 6x6 grid of depth points.
  const promises: Promise<void>[] = [];
  for (let i = 0; i <= 5; i++) {
    for (let j = 0; j <= 5; j++) {
      const lat = bbox.south + i * latStep;
      const lon = bbox.west + j * lonStep;
      const url =
        `${GEBCO_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo` +
        `&LAYERS=GEBCO_LATEST&QUERY_LAYERS=GEBCO_LATEST&INFO_FORMAT=text/plain` +
        `&SRS=EPSG:4326&WIDTH=2&HEIGHT=2&X=1&Y=1` +
        `&BBOX=${lon - 0.001},${lat - 0.001},${lon + 0.001},${lat + 0.001}`;

      promises.push(
        fetchText(url)
          .then((text) => {
            const match = text.match(/value_list\s*=\s*['"]?([-\d.]+)/);
            if (match) {
              depths.push({ lat, lon, depthM: parseFloat(match[1]) });
            }
          })
          .catch(() => { /* skip failed points */ }),
      );
    }
  }

  try {
    await Promise.all(promises);

    // Find adjacent points with significant depth difference (> 30m / ~100ft).
    const ledges: OffshoreLedge[] = [];
    let ledgeId = 0;

    for (let i = 0; i < depths.length; i++) {
      for (let j = i + 1; j < depths.length; j++) {
        const d1 = depths[i];
        const d2 = depths[j];
        const dist = haversineMiles(d1.lat, d1.lon, d2.lat, d2.lon);
        if (dist > 5) continue; // Only check nearby points.

        const depthDiff = Math.abs(d1.depthM - d2.depthM);
        if (depthDiff > 30) {
          const shallow = d1.depthM > d2.depthM ? d1 : d2;
          const deep = d1.depthM > d2.depthM ? d2 : d1;
          ledgeId++;
          ledges.push({
            id: `ledge-${ledgeId}`,
            name: `${Math.round(Math.abs(shallow.depthM) * 3.28084)}ft → ${Math.round(Math.abs(deep.depthM) * 3.28084)}ft drop`,
            lat: (d1.lat + d2.lat) / 2,
            lon: (d1.lon + d2.lon) / 2,
            shallowDepthFt: Math.round(Math.abs(shallow.depthM) * 3.28084),
            deepDepthFt: Math.round(Math.abs(deep.depthM) * 3.28084),
            lengthNm: dist * 0.868976,
          });
        }
      }
    }

    // Deduplicate ledges that are very close together.
    const deduped = ledges.filter((l, idx) => {
      for (let k = 0; k < idx; k++) {
        if (haversineMiles(l.lat, l.lon, ledges[k].lat, ledges[k].lon) < 1) return false;
      }
      return true;
    });

    setCache(key, deduped);
    return deduped;
  } catch (err) {
    console.warn('[coastalFishing] getOffshoreLedges failed:', err);
    return [];
  }
}

// ── Surf Fishing Beach Access ────────────────────────────────────

/**
 * Fetch beach access points suitable for surf fishing from
 * OpenStreetMap within a bounding box.
 */
export async function getSurfFishingAccess(bbox: CoastalBBox): Promise<SurfAccessPoint[]> {
  const key = bboxKey('surfaccess', bbox);
  const cached = getCached<SurfAccessPoint[]>(key);
  if (cached) return cached;

  const query = `
    [out:json][timeout:20];
    (
      node["natural"="beach"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      node["leisure"="beach_resort"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      node["access"="yes"]["natural"="coastline"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
      node["highway"="path"]["access"="yes"](${bbox.south},${bbox.west},${bbox.north},${bbox.east});
    );
    out body qt 200;
  `;

  try {
    const text = await fetchText(OVERPASS_URL, `data=${encodeURIComponent(query)}`);
    const data = JSON.parse(text) as { elements: Array<{ id: number; lat: number; lon: number; tags?: Record<string, string> }> };

    const results: SurfAccessPoint[] = (data.elements ?? []).map((el) => {
      const tags = el.tags ?? {};
      let type: SurfAccessPoint['type'] = 'beach_access';
      if (tags.amenity === 'parking') type = 'parking';
      else if (tags.highway === 'path') type = 'walkover';

      return {
        id: `osm-surf-${el.id}`,
        name: tags.name ?? 'Beach Access',
        lat: el.lat,
        lon: el.lon,
        type,
        vehicleAccess: tags['4wd_only'] === 'yes' || tags.vehicle === 'yes',
        notes: tags.description ?? tags.note ?? undefined,
      };
    });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalFishing] getSurfFishingAccess failed:', err);
    return [];
  }
}

// ── GeoJSON helpers ──────────────────────────────────────────────

/** Convert InshoreSpot array to GeoJSON FeatureCollection. */
export function inshoreSpotsToGeoJSON(spots: InshoreSpot[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: spots.map((s) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [s.lon, s.lat] },
      properties: {
        id: s.id,
        name: s.name,
        type: s.type,
        publicAccess: s.publicAccess,
      },
    })),
  };
}

/** Convert ReefSpot array to GeoJSON FeatureCollection. */
export function reefSpotsToGeoJSON(spots: ReefSpot[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: spots.map((s) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [s.lon, s.lat] },
      properties: {
        id: s.id,
        name: s.name,
        type: s.type,
        depthFt: s.depthFt,
        source: s.source,
      },
    })),
  };
}
