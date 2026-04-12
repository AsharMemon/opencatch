/**
 * International Hydrographic Survey Service for OpenCatch.
 *
 * Provides a unified interface to government hydrographic survey data
 * from multiple countries. Auto-detects the user's country from GPS
 * coordinates and routes to the appropriate national data source.
 *
 * ## Supported Countries & Data Sources
 *
 * ### United States — USACE eHydro
 * - Portal: navigation.usace.army.mil/Survey/Hydro
 * - API: ArcGIS Feature Service (geospatial-usace.opendata.arcgis.com)
 * - Format: GeoJSON via ArcGIS REST API
 * - Coverage: All USACE navigation channels, 700+ reservoirs
 * - Cost: Free, no API key
 * - Update: Varies by district (weeks to months)
 *
 * ### Canada — Canadian Hydrographic Service (CHS) NONNA
 * - Portal: data.chs-shc.ca (NONNA Data Portal)
 * - API: OGC WMS/WCS/WMTS at charts.gc.ca
 * - Format: Raster tiles (WMS), bathymetric grids (WCS at 10m/100m res)
 * - Coverage: All Canadian coastal and major inland waterways
 * - Cost: Free, non-navigational use only
 * - Update: Varies by region
 *
 * ### Europe — EMODnet Bathymetry
 * - Portal: emodnet.ec.europa.eu/en/bathymetry
 * - API: REST at rest.emodnet-bathymetry.eu + OGC WMS/WCS/WFS
 *   - GET /depth_sample?lat=X&lon=Y — single point depth
 *   - GET /depth_profile?lat1=X&lon1=Y&lat2=X&lon2=Y — depth profile
 * - Format: JSON (REST), raster tiles (WMS)
 * - Coverage: All European seas (North Sea, Med, Baltic, Black Sea, Atlantic)
 * - Resolution: ~115m x 115m gridded DTM
 * - Cost: Free, no API key
 * - Update: Major releases (DTM 2020, 2022)
 *
 * ### United Kingdom — UK Hydrographic Office (UKHO)
 * - Portal: ADMIRALTY Marine Data Portal (gov.uk/guidance/inspire-portal)
 * - API: UKHO ADMIRALTY Marine Data Portal + OGC WMS
 *   - Seabed Mapping App for bathymetry holdings
 *   - api.gov.uk/ukho — API catalog
 * - Format: Bathymetry surfaces, GeoTIFF, ISO 19115 metadata
 * - Coverage: UK Exclusive Economic Zone (EEZ), 4000+ surveys from 1970-present
 * - Cost: Free under UKHO Bathymetry Data Licence
 * - Update: Ongoing surveys
 *
 * ### Australia — AusSeabed / Australian Hydrographic Office (AHO)
 * - Portal: ausseabed.gov.au/data (AusSeabed Marine Data Portal)
 * - API: Geoscience Australia catalog (ecat.ga.gov.au), OGC WMS
 * - Format: Multibeam bathymetry grids, GeoTIFF
 * - Coverage: Australian marine jurisdiction
 * - Cost: Free (open data via Geoscience Australia)
 * - Update: Ongoing HIPP program surveys
 *
 * ### New Zealand — Land Information NZ (LINZ)
 * - Portal: data.linz.govt.nz (LINZ Data Service)
 * - API: OGC WFS/WMS/WMTS at data.linz.govt.nz/services
 *   - Requires free API key (register at data.linz.govt.nz)
 * - Format: Vector data via WFS, raster tiles via WMS/WMTS
 * - Coverage: NZ coastal waters, parts of SW Pacific, Antarctica
 * - Cost: Free under Creative Commons licence
 * - Update: Regular updates
 *
 * ### Japan — Japan Oceanographic Data Center (JODC)
 * - Portal: jodc.go.jp (J-DOSS system)
 * - API: J-DOSS web data service
 * - Format: Various (BATHY format, netCDF)
 * - Coverage: Japanese coastal waters and NW Pacific
 * - Products: JTOPO30 (30-sec grid), JHA 5-sec/15-sec meshed sounding
 * - Cost: Free for basic data via J-DOSS
 * - Update: Varies
 */

// ── Types ────────────────────────────────────────────────────────

/** Bounding box for spatial queries. */
export interface SurveyBBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** Supported country codes for survey data. */
export type SurveyCountry = 'US' | 'CA' | 'GB' | 'AU' | 'NZ' | 'EU' | 'JP';

/** A depth sample point from any national survey source. */
export interface DepthSample {
  lat: number;
  lon: number;
  /** Depth in meters (positive downward). */
  depthM: number;
  /** Source agency/service name. */
  source: string;
  /** Survey date if known (ISO-8601). */
  surveyDate: string | null;
  /** Data quality indicator if available. */
  quality: 'measured' | 'interpolated' | 'estimated' | 'unknown';
}

/** A depth profile (series of depth samples along a line). */
export interface DepthProfile {
  /** Ordered depth samples along the profile line. */
  samples: DepthSample[];
  /** Total profile length in meters. */
  lengthM: number;
  /** Source agency. */
  source: string;
}

/** Tile source info for WMS/WMTS bathymetry layers. */
export interface BathymetryTileSource {
  /** URL template with {z}/{x}/{y} or WMS GetMap URL. */
  urlTemplate: string;
  /** Tile type. */
  type: 'wms' | 'wmts' | 'raster';
  /** Min usable zoom. */
  minZoom: number;
  /** Max usable zoom. */
  maxZoom: number;
  /** Attribution. */
  attribution: string;
  /** Country code. */
  country: SurveyCountry;
}

/** Unified survey result from any country. */
export interface InternationalSurveyResult {
  /** Country detected or specified. */
  country: SurveyCountry;
  /** Source agency name. */
  source: string;
  /** Depth samples if point/area query. */
  depthSamples: DepthSample[];
  /** Tile source for map overlay if available. */
  tileSource: BathymetryTileSource | null;
  /** Any error message. */
  error: string | null;
}

// ── Constants ────────────────────────────────────────────────────

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 20_000;

/** Cache TTL: 1 hour for international survey data. */
const CACHE_TTL_MS = 60 * 60 * 1000;

// ── EMODnet REST API ─────────────────────────────────────────────

const EMODNET_REST_BASE = 'https://rest.emodnet-bathymetry.eu';

/** EMODnet WMS for European bathymetry tiles. */
const EMODNET_WMS = 'https://ows.emodnet-bathymetry.eu/wms';

// ── CHS (Canada) WMS ─────────────────────────────────────────────

const CHS_WMS = 'https://nonna-geoserver.data.chs-shc.ca/geoserver/ows';
const NR_CAN_HYDRO_NETWORK_WMS = 'https://maps.geogratis.gc.ca/wms/hydro_network_en';

// ── LINZ (NZ) WMS ────────────────────────────────────────────────

const LINZ_WMS = 'https://data.linz.govt.nz/services;key=YOUR_KEY/wms';

// ── AusSeabed (Australia) ────────────────────────────────────────

const AUSSEABED_WMS = 'https://www.ausseabed.gov.au/surveys/grid/wms';

// ── UKHO (UK) ────────────────────────────────────────────────────

const UKHO_WMS = 'https://tiles.admiralty.co.uk/bathymetry';

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const intlCache = new Map<string, CacheEntry<any>>();

function getCached<T>(key: string): T | null {
  const entry = intlCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    intlCache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  intlCache.set(key, { data, timestamp: Date.now() });
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

// ── Country Detection ────────────────────────────────────────────

/** Rough bounding boxes for country detection from GPS coordinates. */
const COUNTRY_BOXES: Array<{ country: SurveyCountry; bounds: SurveyBBox }> = [
  // Continental US (including Alaska and Hawaii handled separately)
  { country: 'US', bounds: { west: -125, south: 24, east: -66, north: 50 } },
  // Alaska
  { country: 'US', bounds: { west: -180, south: 50, east: -128, north: 72 } },
  // Hawaii
  { country: 'US', bounds: { west: -162, south: 18, east: -154, north: 23 } },
  // Canada
  { country: 'CA', bounds: { west: -141, south: 41, east: -52, north: 84 } },
  // UK + Ireland
  { country: 'GB', bounds: { west: -11, south: 49, east: 2, north: 62 } },
  // Australia
  { country: 'AU', bounds: { west: 112, south: -45, east: 155, north: -10 } },
  // New Zealand
  { country: 'NZ', bounds: { west: 165, south: -48, east: 179, north: -34 } },
  // Japan
  { country: 'JP', bounds: { west: 122, south: 24, east: 154, north: 46 } },
  // Europe (broad — catches most EU countries)
  { country: 'EU', bounds: { west: -12, south: 34, east: 45, north: 72 } },
];

/**
 * Detect the user's country from GPS coordinates.
 * Returns 'US' as default if no match.
 */
export function detectCountry(lat: number, lon: number): SurveyCountry {
  for (const { country, bounds } of COUNTRY_BOXES) {
    if (
      lon >= bounds.west &&
      lon <= bounds.east &&
      lat >= bounds.south &&
      lat <= bounds.north
    ) {
      return country;
    }
  }
  // Default: try EMODnet for any ocean location, else US
  return 'US';
}

// ── EMODnet (Europe) ─────────────────────────────────────────────

interface EMODnetDepthResponse {
  avg?: number;
  min?: number;
  max?: number;
  stdev?: number;
  depth?: number;
}

/**
 * Get a single depth sample from EMODnet REST API.
 * Works for any point in European waters.
 */
async function getEMODnetDepth(lat: number, lon: number): Promise<DepthSample | null> {
  const key = `emodnet:${lat.toFixed(4)},${lon.toFixed(4)}`;
  const cached = getCached<DepthSample>(key);
  if (cached) return cached;

  try {
    const url = `${EMODNET_REST_BASE}/depth_sample?lat=${lat}&lon=${lon}`;
    const data = await fetchJSON<EMODnetDepthResponse>(url);

    const depth = data.avg ?? data.depth ?? null;
    if (depth == null) return null;

    const sample: DepthSample = {
      lat,
      lon,
      depthM: Math.abs(depth), // EMODnet returns negative for depth below sea level
      source: 'EMODnet Bathymetry',
      surveyDate: null,
      quality: 'interpolated', // DTM gridded data
    };

    setCache(key, sample);
    return sample;
  } catch (err) {
    console.warn('[internationalSurveys] EMODnet depth_sample failed:', err);
    return null;
  }
}

/**
 * Get a depth profile from EMODnet REST API along a line.
 */
async function getEMODnetProfile(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): Promise<DepthProfile | null> {
  try {
    const url =
      `${EMODNET_REST_BASE}/depth_profile` +
      `?geom=LINESTRING(${lon1} ${lat1},${lon2} ${lat2})`;
    const data = await fetchJSON<any>(url);

    if (!data?.length) return null;

    const samples: DepthSample[] = data.map((pt: any) => ({
      lat: pt.lat ?? pt.y ?? lat1,
      lon: pt.lon ?? pt.x ?? lon1,
      depthM: Math.abs(pt.depth ?? pt.avg ?? 0),
      source: 'EMODnet Bathymetry',
      surveyDate: null,
      quality: 'interpolated' as const,
    }));

    // Rough length calculation
    const dLat = lat2 - lat1;
    const dLon = lon2 - lon1;
    const lengthM = Math.sqrt(dLat ** 2 + dLon ** 2) * 111_320;

    return { samples, lengthM, source: 'EMODnet Bathymetry' };
  } catch (err) {
    console.warn('[internationalSurveys] EMODnet depth_profile failed:', err);
    return null;
  }
}

// ── Tile sources by country ──────────────────────────────────────

/** Get the appropriate bathymetry tile source for a country. */
export function getBathymetryTileSource(country: SurveyCountry): BathymetryTileSource | null {
  switch (country) {
    case 'EU':
    case 'GB':
      return {
        urlTemplate:
          `${EMODNET_WMS}?service=WMS&request=GetMap&layers=emodnet:mean_atlas_land` +
          `&styles=&format=image/png&transparent=true&version=1.1.1` +
          `&width=256&height=256&srs=EPSG:3857&bbox={bbox-epsg-3857}`,
        type: 'wms',
        minZoom: 3,
        maxZoom: 14,
        attribution: 'EMODnet Bathymetry',
        country,
      };

    case 'CA':
      return {
        urlTemplate:
          `${CHS_WMS}?service=WMS&request=GetMap&layers=nonna:NONNA%2010` +
          `&styles=&format=image/png&transparent=true&version=1.3.0` +
          `&width=256&height=256&crs=EPSG:3857&bbox={bbox-epsg-3857}`,
        type: 'wms',
        minZoom: 3,
        maxZoom: 16,
        attribution: 'Canadian Hydrographic Service (CHS) NONNA 10 (non-navigational)',
        country,
      };

    case 'AU':
      return {
        urlTemplate:
          `${AUSSEABED_WMS}?service=WMS&request=GetMap&layers=AusBathyTopo` +
          `&styles=&format=image/png&transparent=true&version=1.1.1` +
          `&width=256&height=256&srs=EPSG:3857&bbox={bbox-epsg-3857}`,
        type: 'wms',
        minZoom: 3,
        maxZoom: 14,
        attribution: 'AusSeabed / Geoscience Australia',
        country,
      };

    case 'NZ':
      return {
        urlTemplate:
          `${LINZ_WMS}?service=WMS&request=GetMap&layers=layer-50554` +
          `&styles=&format=image/png&transparent=true&version=1.1.1` +
          `&width=256&height=256&srs=EPSG:3857&bbox={bbox-epsg-3857}`,
        type: 'wms',
        minZoom: 3,
        maxZoom: 14,
        attribution: 'LINZ (Land Information New Zealand)',
        country,
      };

    case 'JP':
      // JODC doesn't have a public WMS tile service —
      // fall back to GEBCO global tiles which cover Japan well
      return {
        urlTemplate:
          'https://tiles.emodnet-bathymetry.eu/v11/gebco/{z}/{x}/{y}.png',
        type: 'raster',
        minZoom: 0,
        maxZoom: 12,
        attribution: 'GEBCO / JODC',
        country,
      };

    case 'US':
      // US uses USACE eHydro (vector data) — no raster tile overlay needed.
      // Could use NOAA BAG tiles for coastal areas but that's in coastalCharts.
      return null;

    default:
      return null;
  }
}

/** Official NRCan NHN hydrography tiles for Canada-wide river completeness. */
export function getCanadianHydroNetworkTileSource(): BathymetryTileSource {
  return {
    urlTemplate:
      `${NR_CAN_HYDRO_NETWORK_WMS}?service=WMS&request=GetMap&layers=hydro_network_en` +
      `&styles=&format=image/png&transparent=true&version=1.3.0` +
      `&width=256&height=256&crs=EPSG:3857&bbox={bbox-epsg-3857}`,
    type: 'wms',
    minZoom: 3,
    maxZoom: 14,
    attribution: 'Natural Resources Canada NHN',
    country: 'CA',
  };
}

// ── Unified Query Interface ──────────────────────────────────────

/**
 * Unified interface to get survey/depth data for any supported country.
 *
 * Auto-detects the user's country from GPS if not specified, then
 * routes to the appropriate national survey data source.
 *
 * @param bbox — Bounding box to query.
 * @param country — Override country detection. If omitted, auto-detected from bbox center.
 */
export async function getSurveyData(
  bbox: SurveyBBox,
  country?: SurveyCountry,
): Promise<InternationalSurveyResult> {
  const centerLat = (bbox.north + bbox.south) / 2;
  const centerLon = (bbox.east + bbox.west) / 2;
  const detectedCountry = country ?? detectCountry(centerLat, centerLon);

  const key = `intl:${detectedCountry}:${centerLat.toFixed(2)},${centerLon.toFixed(2)}`;
  const cached = getCached<InternationalSurveyResult>(key);
  if (cached) return cached;

  const tileSource = getBathymetryTileSource(detectedCountry);

  try {
    let depthSamples: DepthSample[] = [];

    switch (detectedCountry) {
      case 'US':
        // US data comes from usaceDepthSurveys.ts (separate service)
        break;

      case 'EU':
      case 'GB': {
        // Sample depth at the center point via EMODnet REST API
        const sample = await getEMODnetDepth(centerLat, centerLon);
        if (sample) depthSamples = [sample];
        break;
      }

      case 'CA':
      case 'AU':
      case 'NZ':
      case 'JP':
        // These countries primarily expose WMS tile layers.
        // Point depth queries would need WCS GetCoverage calls
        // which require more complex coordinate transformation.
        // For now, provide tile source for map overlay.
        break;
    }

    const result: InternationalSurveyResult = {
      country: detectedCountry,
      source: getSourceName(detectedCountry),
      depthSamples,
      tileSource,
      error: null,
    };

    setCache(key, result);
    return result;
  } catch (err) {
    return {
      country: detectedCountry,
      source: getSourceName(detectedCountry),
      depthSamples: [],
      tileSource,
      error: String(err),
    };
  }
}

/**
 * Get a single depth reading at a point, auto-detecting the country.
 * Currently only EMODnet (Europe) supports point queries via REST.
 * Other countries return null (use tile overlay instead).
 */
export async function getDepthAtPoint(
  lat: number,
  lon: number,
): Promise<DepthSample | null> {
  const country = detectCountry(lat, lon);

  switch (country) {
    case 'EU':
    case 'GB':
      return getEMODnetDepth(lat, lon);
    default:
      // Other countries don't have simple point-query REST APIs
      return null;
  }
}

/**
 * Get a depth profile along a line, auto-detecting the country.
 * Currently only EMODnet (Europe) supports profile queries.
 */
export async function getDepthProfile(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): Promise<DepthProfile | null> {
  const country = detectCountry((lat1 + lat2) / 2, (lon1 + lon2) / 2);

  switch (country) {
    case 'EU':
    case 'GB':
      return getEMODnetProfile(lat1, lon1, lat2, lon2);
    default:
      return null;
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function getSourceName(country: SurveyCountry): string {
  switch (country) {
    case 'US': return 'USACE eHydro / CWMS';
    case 'CA': return 'Canadian Hydrographic Service (CHS)';
    case 'GB': return 'UK Hydrographic Office (UKHO)';
    case 'AU': return 'AusSeabed / Australian Hydrographic Office';
    case 'NZ': return 'LINZ Hydrographic Authority';
    case 'EU': return 'EMODnet Bathymetry';
    case 'JP': return 'JODC / Japan Hydrographic Association';
  }
}

/** Human-readable label for the data source of a country. */
export function getCountryLabel(country: SurveyCountry): string {
  switch (country) {
    case 'US': return 'United States';
    case 'CA': return 'Canada';
    case 'GB': return 'United Kingdom';
    case 'AU': return 'Australia';
    case 'NZ': return 'New Zealand';
    case 'EU': return 'Europe';
    case 'JP': return 'Japan';
  }
}

/** All supported countries. */
export const SUPPORTED_COUNTRIES: SurveyCountry[] = [
  'US', 'CA', 'GB', 'AU', 'NZ', 'EU', 'JP',
];
