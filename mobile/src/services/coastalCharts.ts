/**
 * Coastal Charts Service for OpenCatch.
 *
 * Provides access to NOAA Electronic Navigational Chart (ENC) tiles,
 * Aids to Navigation (ATON), regulated speed zones, artificial reef
 * databases, and the Automated Wreck and Obstruction Information System
 * (AWOIS).
 *
 * All NOAA data is free and requires no API key.
 *
 * Tile service:  https://tileservice.charts.noaa.gov
 * ATON/AWOIS:    https://gis.charttools.noaa.gov/arcgis/rest/services
 */

// ── Types ────────────────────────────────────────────────────────

/** Bounding box for spatial queries. */
export interface CoastalBBox {
  /** Western longitude. */
  west: number;
  /** Southern latitude. */
  south: number;
  /** Eastern longitude. */
  east: number;
  /** Northern latitude. */
  north: number;
}

/** NOAA raster chart tile metadata. */
export interface ChartTile {
  /** Tile URL template with {z}/{x}/{y} placeholders. */
  urlTemplate: string;
  /** Minimum usable zoom level. */
  minZoom: number;
  /** Maximum usable zoom level. */
  maxZoom: number;
  /** Attribution string for display. */
  attribution: string;
}

/** An Aid to Navigation marker (buoy, light, daybeacon, etc.). */
export interface NavAid {
  id: string;
  /** Human-readable name or light-list number. */
  name: string;
  lat: number;
  lon: number;
  /** ATON category: buoy, light, daybeacon, range, fogSignal, etc. */
  type: 'buoy' | 'light' | 'daybeacon' | 'range' | 'fogSignal' | 'other';
  /** Color scheme if applicable (e.g. "red", "green", "red-white"). */
  color?: string;
  /** Characteristic pattern for lights (e.g. "Fl R 4s"). */
  characteristic?: string;
  /** Structure description. */
  description?: string;
}

/** A regulated speed / no-wake zone. */
export interface SpeedZone {
  id: string;
  /** Human-readable zone name or regulation reference. */
  name: string;
  /** Speed limit in knots, or null for "no wake". */
  speedLimitKnots: number | null;
  /** Whether this is a strict no-wake zone. */
  noWake: boolean;
  /** GeoJSON polygon coordinates (array of [lon, lat] rings). */
  boundary: number[][][];
  /** Governing authority. */
  authority: string;
}

/** An artificial reef deployment site. */
export interface ArtificialReef {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Depth in feet to the top of the reef structure. */
  depthFt: number | null;
  /** Material type (e.g. "concrete rubble", "steel barge", "reef balls"). */
  material?: string;
  /** Year the reef was deployed, if known. */
  yearDeployed?: number;
  /** Managing state abbreviation. */
  state: string;
  /** Distance from nearest shore in nautical miles, if known. */
  distanceFromShoreNm?: number;
}

/** A charted wreck or obstruction from NOAA AWOIS. */
export interface WreckSite {
  /** AWOIS record number. */
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Depth over the wreck in feet, or null if unknown. */
  depthFt: number | null;
  /** Whether the wreck is visible at the surface. */
  isVisible: boolean;
  /** Year the wreck was reported or surveyed. */
  yearReported?: number;
  /** Brief description (vessel name, type, condition). */
  description?: string;
  /** AWOIS history text if available. */
  history?: string;
}

// ── Constants ────────────────────────────────────────────────────

const NOAA_TILE_BASE = 'https://tileservice.charts.noaa.gov/tiles/50000_1';
const NOAA_GIS_BASE = 'https://gis.charttools.noaa.gov/arcgis/rest/services';

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 15_000;

/** Cache TTL for feature queries: 1 hour. */
const CACHE_TTL_MS = 60 * 60 * 1000;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const featureCache = new Map<string, CacheEntry<any>>();

function getCached<T>(key: string): T | null {
  const entry = featureCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    featureCache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  featureCache.set(key, { data, timestamp: Date.now() });
}

function bboxKey(prefix: string, bbox: CoastalBBox): string {
  const r = (n: number) => Math.round(n * 100) / 100;
  return `${prefix}:${r(bbox.west)},${r(bbox.south)},${r(bbox.east)},${r(bbox.north)}`;
}

// ── Fetch helper ─────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { 'User-Agent': USER_AGENT },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── NOAA ENC Chart Tiles ─────────────────────────────────────────

/**
 * Return the NOAA ENC raster chart tile source for the given area.
 *
 * The tiles cover US coastal waters at 1:50,000 scale. Use the
 * returned `urlTemplate` as a raster tile source in MapLibre.
 */
export function getCoastalChart(_bbox: CoastalBBox): ChartTile {
  return {
    urlTemplate: `${NOAA_TILE_BASE}/{z}/{x}/{y}.png`,
    minZoom: 3,
    maxZoom: 16,
    attribution: 'NOAA Office of Coast Survey',
  };
}

// ── Aids to Navigation (ATON) ────────────────────────────────────

interface ArcGISFeatureResponse {
  features?: Array<{
    attributes: Record<string, any>;
    geometry?: { x: number; y: number };
  }>;
}

function classifyATON(attrs: Record<string, any>): NavAid['type'] {
  const aidType = (attrs.aidType ?? attrs.AidType ?? '').toLowerCase();
  if (aidType.includes('buoy')) return 'buoy';
  if (aidType.includes('light') && !aidType.includes('daybeacon')) return 'light';
  if (aidType.includes('daybeacon') || aidType.includes('day beacon')) return 'daybeacon';
  if (aidType.includes('range')) return 'range';
  if (aidType.includes('fog')) return 'fogSignal';
  return 'other';
}

/**
 * Fetch NOAA Aids to Navigation (buoys, lights, daybeacons) within a
 * bounding box. Uses the NOAA ArcGIS feature service for ATON data.
 */
export async function getChannelMarkers(bbox: CoastalBBox): Promise<NavAid[]> {
  const key = bboxKey('aton', bbox);
  const cached = getCached<NavAid[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${NOAA_GIS_BASE}/MCS/ENCOnline/MapServer/3/query` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=json&resultRecordCount=500`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: NavAid[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        return {
          id: String(a.OBJECTID ?? a.objectid ?? a.aidNumber ?? ''),
          name: a.aidName ?? a.AidName ?? 'Unknown',
          lat: f.geometry!.y,
          lon: f.geometry!.x,
          type: classifyATON(a),
          color: a.color ?? a.Color ?? undefined,
          characteristic: a.characteristic ?? a.Characteristic ?? undefined,
          description: a.description ?? a.Description ?? undefined,
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalCharts] getChannelMarkers failed:', err);
    return [];
  }
}

// ── No-Wake / Speed Zones ────────────────────────────────────────

/**
 * Fetch regulated speed zones (no-wake, slow speed) within a bounding
 * box. Sources NOAA and USACE regulatory area data.
 */
export async function getNoWakeZones(bbox: CoastalBBox): Promise<SpeedZone[]> {
  const key = bboxKey('speedzone', bbox);
  const cached = getCached<SpeedZone[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${NOAA_GIS_BASE}/MCS/ENCOnline/MapServer/7/query` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=json&resultRecordCount=200`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse & {
      features?: Array<{
        attributes: Record<string, any>;
        geometry?: { rings?: number[][][] };
      }>;
    }>(url);

    const results: SpeedZone[] = (data.features ?? [])
      .filter((f) => f.geometry?.rings)
      .map((f) => {
        const a = f.attributes;
        const limit = a.speedLimit ?? a.SpeedLimit ?? null;
        const isNoWake =
          (a.regulation ?? a.Regulation ?? '').toLowerCase().includes('no wake') ||
          limit === 0;
        return {
          id: String(a.OBJECTID ?? a.objectid ?? ''),
          name: a.zoneName ?? a.ZoneName ?? a.regulation ?? 'Speed Zone',
          speedLimitKnots: typeof limit === 'number' ? limit : null,
          noWake: isNoWake,
          boundary: f.geometry!.rings!,
          authority: a.authority ?? a.Authority ?? 'NOAA',
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalCharts] getNoWakeZones failed:', err);
    return [];
  }
}

// ── Artificial Reefs ─────────────────────────────────────────────

/**
 * Known artificial reef program data endpoints by state.
 * Each state publishes reef GPS data in slightly different formats.
 * We aggregate them via a unified Overpass + state GIS approach.
 */
const REEF_STATE_GIS: Record<string, string> = {
  FL: 'https://geodata.myfwc.com/arcgis/rest/services/Habitat/ArtificialReefs/MapServer/0/query',
  TX: 'https://gis.tpwd.texas.gov/arcgis/rest/services/Habitat/ArtificialReefs/MapServer/0/query',
  LA: 'https://gis.wlf.la.gov/arcgis/rest/services/Habitat/ArtificialReefs/MapServer/0/query',
  AL: 'https://gis.outdooralabama.com/arcgis/rest/services/MarineResources/ArtificialReefs/MapServer/0/query',
  MS: 'https://gis.dmr.ms.gov/arcgis/rest/services/ArtificialReefs/MapServer/0/query',
  NC: 'https://gis.deq.nc.gov/arcgis/rest/services/ArtificialReefs/MapServer/0/query',
  SC: 'https://gis.dnr.sc.gov/arcgis/rest/services/ArtificialReefs/MapServer/0/query',
  GA: 'https://gis.gadnr.org/arcgis/rest/services/CRD/ArtificialReefs/MapServer/0/query',
  NJ: 'https://gis.dep.nj.gov/arcgis/rest/services/ArtificialReefs/MapServer/0/query',
  DE: 'https://gis.dnrec.delaware.gov/arcgis/rest/services/ArtificialReefs/MapServer/0/query',
};

/**
 * Fetch artificial reef locations for a given coastal state.
 *
 * @param state — Two-letter state abbreviation (FL, TX, LA, NC, SC, AL, MS, GA, NJ, DE).
 * @returns Array of reef sites with GPS coordinates and metadata.
 */
export async function getArtificialReefs(state: string): Promise<ArtificialReef[]> {
  const st = state.toUpperCase();
  const cacheKey = `reef:${st}`;
  const cached = getCached<ArtificialReef[]>(cacheKey);
  if (cached) return cached;

  const endpoint = REEF_STATE_GIS[st];
  if (!endpoint) {
    console.warn(`[coastalCharts] No artificial reef source for state: ${st}`);
    return [];
  }

  const url =
    `${endpoint}?where=1%3D1&outFields=*&outSR=4326&f=json&resultRecordCount=2000`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: ArtificialReef[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        return {
          id: String(a.OBJECTID ?? a.objectid ?? a.ReefID ?? ''),
          name: a.ReefName ?? a.Name ?? a.name ?? `Reef ${a.OBJECTID ?? ''}`,
          lat: f.geometry!.y,
          lon: f.geometry!.x,
          depthFt: a.Depth ?? a.depth ?? a.DepthFt ?? null,
          material: a.Material ?? a.material ?? undefined,
          yearDeployed: a.YearDeployed ?? a.Year ?? a.year ?? undefined,
          state: st,
          distanceFromShoreNm: a.DistanceFromShore ?? a.DistNM ?? undefined,
        };
      });

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn(`[coastalCharts] getArtificialReefs(${st}) failed:`, err);
    return [];
  }
}

// ── AWOIS Wrecks & Obstructions ──────────────────────────────────

/**
 * Fetch charted wrecks and obstructions from the NOAA AWOIS database.
 */
export async function getWreckLocations(bbox: CoastalBBox): Promise<WreckSite[]> {
  const key = bboxKey('wrecks', bbox);
  const cached = getCached<WreckSite[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${NOAA_GIS_BASE}/MCS/WrecksAndObstructions/MapServer/0/query` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=json&resultRecordCount=500`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: WreckSite[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        return {
          id: String(a.RECRD ?? a.OBJECTID ?? ''),
          name: a.VESSLTERMS ?? a.NAME ?? 'Unknown Wreck',
          lat: f.geometry!.y,
          lon: f.geometry!.x,
          depthFt: a.DEPTH ?? a.depth ?? null,
          isVisible: (a.SOUNDING ?? '').toString().toLowerCase() === 'visible',
          yearReported: a.YEARSUNK ?? a.YEAR ?? undefined,
          description: a.FEATURE_TYPE ?? a.HISTORY ?? undefined,
          history: a.HISTORY ?? undefined,
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[coastalCharts] getWreckLocations failed:', err);
    return [];
  }
}

// ── GeoJSON helpers ──────────────────────────────────────────────

/** Convert NavAid array to GeoJSON FeatureCollection for map rendering. */
export function navAidsToGeoJSON(aids: NavAid[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: aids.map((aid) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [aid.lon, aid.lat] },
      properties: {
        id: aid.id,
        name: aid.name,
        type: aid.type,
        color: aid.color ?? null,
        characteristic: aid.characteristic ?? null,
      },
    })),
  };
}

/** Convert ArtificialReef array to GeoJSON FeatureCollection. */
export function reefsToGeoJSON(reefs: ArtificialReef[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: reefs.map((reef) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [reef.lon, reef.lat] },
      properties: {
        id: reef.id,
        name: reef.name,
        depthFt: reef.depthFt,
        material: reef.material ?? null,
        state: reef.state,
      },
    })),
  };
}

/** Convert WreckSite array to GeoJSON FeatureCollection. */
export function wrecksToGeoJSON(wrecks: WreckSite[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: wrecks.map((w) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [w.lon, w.lat] },
      properties: {
        id: w.id,
        name: w.name,
        depthFt: w.depthFt,
        isVisible: w.isVisible,
        description: w.description ?? null,
      },
    })),
  };
}
