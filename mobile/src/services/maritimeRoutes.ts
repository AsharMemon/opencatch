/**
 * OpenCatch -- Maritime Routes & Boundaries Service
 *
 * Shipping lanes, traffic separation schemes, restricted areas,
 * military zones, marine sanctuaries, and no-go zones.
 *
 * Data sources:
 *   - NOAA ENC (TSSLPT, TSSRON, RESARE, M_NPUB features)
 *   - OSM seamark tags (separation_zone, restricted_area)
 *   - MarineRegions.org WFS (marine protected areas)
 *   - NOAA Marine Protected Areas inventory
 */

// ── Types ────────────────────────────────────────────────────────

export interface LatLng {
  lat: number;
  lon: number;
}

export interface BBox {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

export type RestrictedAreaType =
  | 'military'
  | 'marine_sanctuary'
  | 'nature_reserve'
  | 'security_zone'
  | 'anchoring_prohibited'
  | 'fishing_prohibited'
  | 'speed_restricted'
  | 'other';

export interface ShippingLane {
  id: string;
  name: string;
  coordinates: LatLng[];
  /** Direction of traffic flow in degrees (null if bidirectional) */
  directionDeg: number | null;
  /** Width in nautical miles (estimated) */
  widthNm: number;
  source: 'noaa-enc' | 'osm';
}

export interface RestrictedArea {
  id: string;
  name: string;
  type: RestrictedAreaType;
  /** Polygon boundary */
  coordinates: LatLng[];
  /** Authority responsible */
  authority: string;
  /** Restrictions description */
  restrictions: string;
  /** Severity: warning, caution, or danger */
  severity: 'warning' | 'caution' | 'danger';
  /** Color for rendering */
  color: string;
  /** Icon to display */
  icon: string;
  source: 'noaa-enc' | 'osm' | 'marine-regions' | 'noaa-mpa';
}

export interface TrafficSeparationScheme {
  id: string;
  name: string;
  /** Separation zone polygon */
  separationZone: LatLng[];
  /** Traffic lanes (line segments) */
  lanes: Array<{
    coordinates: LatLng[];
    directionDeg: number;
  }>;
  source: 'noaa-enc' | 'osm';
}

// ── Constants ────────────────────────────────────────────────────

const REQUEST_TIMEOUT_MS = 12_000;

const RESTRICTED_AREA_COLORS: Record<RestrictedAreaType, string> = {
  military: '#F44336',
  marine_sanctuary: '#4CAF50',
  nature_reserve: '#66BB6A',
  security_zone: '#FF5722',
  anchoring_prohibited: '#FF9800',
  fishing_prohibited: '#E91E63',
  speed_restricted: '#FFC107',
  other: '#9E9E9E',
};

const RESTRICTED_AREA_ICONS: Record<RestrictedAreaType, string> = {
  military: 'warning',
  marine_sanctuary: 'leaf',
  nature_reserve: 'leaf',
  security_zone: 'shield',
  anchoring_prohibited: 'close-circle',
  fishing_prohibited: 'fish',
  speed_restricted: 'speedometer',
  other: 'information-circle',
};

const RESTRICTED_AREA_SEVERITY: Record<RestrictedAreaType, RestrictedArea['severity']> = {
  military: 'danger',
  security_zone: 'danger',
  marine_sanctuary: 'warning',
  nature_reserve: 'warning',
  anchoring_prohibited: 'caution',
  fishing_prohibited: 'caution',
  speed_restricted: 'caution',
  other: 'warning',
};

// ── Fetch helper ─────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
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

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  bbox: BBox;
}

const CACHE_TTL_MS = 20 * 60 * 1000; // 20 minutes
let shippingLaneCache: CacheEntry<ShippingLane[]> | null = null;
let restrictedAreaCache: CacheEntry<RestrictedArea[]> | null = null;
let tssCache: CacheEntry<TrafficSeparationScheme[]> | null = null;

function isCacheValid<T>(cache: CacheEntry<T> | null, bbox: BBox): boolean {
  if (!cache) return false;
  if (Date.now() - cache.timestamp > CACHE_TTL_MS) return false;
  return (
    bbox.minLat >= cache.bbox.minLat - 0.01 &&
    bbox.maxLat <= cache.bbox.maxLat + 0.01 &&
    bbox.minLon >= cache.bbox.minLon - 0.01 &&
    bbox.maxLon <= cache.bbox.maxLon + 0.01
  );
}

// ── NOAA ENC — Traffic Separation Schemes ────────────────────────

async function fetchNOAAShippingLanes(bbox: BBox): Promise<ShippingLane[]> {
  const lanes: ShippingLane[] = [];
  try {
    // NOAA ENC Online — TSS lanes (TSSLPT layer)
    const url =
      `https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/identify` +
      `?geometry=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&geometryType=esriGeometryEnvelope&sr=4326` +
      `&layers=all` +
      `&tolerance=20` +
      `&mapExtent=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&imageDisplay=800,600,96&returnGeometry=true&f=json`;

    const data = await fetchJSON<{
      results?: Array<{
        layerName?: string;
        geometry?: {
          paths?: number[][][];
          x?: number;
          y?: number;
        };
        attributes?: {
          OBJNAM?: string;
          ORIENT?: number;
          CATTRK?: string;
          STATUS?: string;
        };
      }>;
    }>(url);

    if (data.results) {
      for (const result of data.results) {
        const layer = result.layerName ?? '';
        // Look for TSS-related layers
        if (!layer.match(/TSSLPT|TSSRON|TSSBND|TSEZNE|TWRTPT/i)) continue;

        const attrs = result.attributes;
        const geom = result.geometry;
        if (!geom?.paths || geom.paths.length === 0) continue;

        const coordinates: LatLng[] = geom.paths[0].map(([lon, lat]) => ({ lat, lon }));
        if (coordinates.length < 2) continue;

        lanes.push({
          id: `noaa-lane-${lanes.length}`,
          name: attrs?.OBJNAM ?? 'Shipping Lane',
          coordinates,
          directionDeg: attrs?.ORIENT ?? null,
          widthNm: 0.5, // Default estimate
          source: 'noaa-enc',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] NOAA shipping lanes fetch failed:', err);
  }
  return lanes;
}

// ── OSM Overpass — Shipping lanes & separation zones ─────────────

async function fetchOSMShippingLanes(bbox: BBox): Promise<ShippingLane[]> {
  const lanes: ShippingLane[] = [];
  try {
    const query = `[out:json][timeout:10];
      (
        way["seamark:type"="separation_lane"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
        way["seamark:type"="recommended_track"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
        way["seamark:type"="fairway"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
      );
      out body geom;`;
    const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

    const data = await fetchJSON<{
      elements?: Array<{
        id: number;
        geometry?: Array<{ lat: number; lon: number }>;
        tags?: Record<string, string>;
      }>;
    }>(url);

    if (data.elements) {
      for (const el of data.elements) {
        if (!el.geometry || el.geometry.length < 2) continue;
        const coords = el.geometry.map((g) => ({ lat: g.lat, lon: g.lon }));
        const orient = el.tags?.['seamark:separation_lane:orientation'];

        lanes.push({
          id: `osm-lane-${el.id}`,
          name: el.tags?.name ?? el.tags?.['seamark:name'] ?? 'Shipping Lane',
          coordinates: coords,
          directionDeg: orient ? parseFloat(orient) : null,
          widthNm: 0.5,
          source: 'osm',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] OSM shipping lanes fetch failed:', err);
  }
  return lanes;
}

// ── NOAA ENC — Restricted Areas ──────────────────────────────────

async function fetchNOAARestrictedAreas(bbox: BBox): Promise<RestrictedArea[]> {
  const areas: RestrictedArea[] = [];
  try {
    const url =
      `https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/identify` +
      `?geometry=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&geometryType=esriGeometryEnvelope&sr=4326` +
      `&layers=all` +
      `&tolerance=20` +
      `&mapExtent=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&imageDisplay=800,600,96&returnGeometry=true&f=json`;

    const data = await fetchJSON<{
      results?: Array<{
        layerName?: string;
        geometry?: {
          rings?: number[][][];
        };
        attributes?: {
          OBJNAM?: string;
          CATRES?: string;
          RESTRN?: string;
          INFORM?: string;
          STATUS?: string;
        };
      }>;
    }>(url);

    if (data.results) {
      for (const result of data.results) {
        const layer = result.layerName ?? '';
        if (!layer.match(/RESARE|MIPARE|ADMARE|ACHARE/i)) continue;

        const geom = result.geometry;
        const attrs = result.attributes;
        if (!geom?.rings || geom.rings.length === 0) continue;

        const coordinates = geom.rings[0].map(([lon, lat]) => ({ lat, lon }));
        const areaType = classifyRestrictedAreaType(layer, attrs?.CATRES, attrs?.RESTRN);

        areas.push({
          id: `noaa-restricted-${areas.length}`,
          name: attrs?.OBJNAM ?? 'Restricted Area',
          type: areaType,
          coordinates,
          authority: 'NOAA / USCG',
          restrictions: attrs?.INFORM ?? attrs?.RESTRN ?? 'Area restricted — check chart',
          severity: RESTRICTED_AREA_SEVERITY[areaType],
          color: RESTRICTED_AREA_COLORS[areaType],
          icon: RESTRICTED_AREA_ICONS[areaType],
          source: 'noaa-enc',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] NOAA restricted areas fetch failed:', err);
  }
  return areas;
}

// ── MarineRegions.org WFS — Marine Protected Areas ───────────────

async function fetchMarineRegionsMPA(bbox: BBox): Promise<RestrictedArea[]> {
  const areas: RestrictedArea[] = [];
  try {
    const url =
      `https://geo.vliz.be/geoserver/MarineRegions/wfs` +
      `?service=WFS&version=1.1.0&request=GetFeature` +
      `&typeName=MarineRegions:eez` +
      `&outputFormat=application/json` +
      `&bbox=${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon},EPSG:4326` +
      `&maxFeatures=50`;

    // This may not return MPAs directly, fall back to NOAA MPA inventory
  } catch (err) {
    console.warn('[MaritimeRoutes] MarineRegions fetch failed:', err);
  }
  return areas;
}

// ── NOAA MPA Inventory ───────────────────────────────────────────

async function fetchNOAAMPA(bbox: BBox): Promise<RestrictedArea[]> {
  const areas: RestrictedArea[] = [];
  try {
    const url =
      `https://marineprotectedareas.noaa.gov/arcgis/rest/services/MPAI/MPAInventory_v2/MapServer/0/query` +
      `?where=1=1` +
      `&geometry=${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}` +
      `&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326` +
      `&outFields=Site_Name,Gov_Level,Mgmt_Plan,Protection_Level,Primary_Conservation_Focus,Fishing_Restrictions` +
      `&returnGeometry=true&f=json` +
      `&resultRecordCount=50`;

    const data = await fetchJSON<{
      features?: Array<{
        geometry?: {
          rings?: number[][][];
        };
        attributes?: {
          Site_Name?: string;
          Gov_Level?: string;
          Mgmt_Plan?: string;
          Protection_Level?: string;
          Primary_Conservation_Focus?: string;
          Fishing_Restrictions?: string;
        };
      }>;
    }>(url);

    if (data.features) {
      for (const feature of data.features) {
        const geom = feature.geometry;
        const attrs = feature.attributes;
        if (!geom?.rings || geom.rings.length === 0 || !attrs) continue;

        const coordinates = geom.rings[0].map(([lon, lat]) => ({ lat, lon }));
        const protLevel = (attrs.Protection_Level ?? '').toLowerCase();
        const isSanctuary = protLevel.includes('no-take') || protLevel.includes('sanctuary');
        const areaType: RestrictedAreaType = isSanctuary ? 'marine_sanctuary' : 'nature_reserve';

        const restrictions: string[] = [];
        if (attrs.Fishing_Restrictions) restrictions.push(`Fishing: ${attrs.Fishing_Restrictions}`);
        if (attrs.Mgmt_Plan) restrictions.push(`Plan: ${attrs.Mgmt_Plan}`);

        areas.push({
          id: `noaa-mpa-${areas.length}`,
          name: attrs.Site_Name ?? 'Marine Protected Area',
          type: areaType,
          coordinates,
          authority: attrs.Gov_Level ?? 'Federal',
          restrictions: restrictions.join('. ') || 'Marine protected area — check regulations',
          severity: RESTRICTED_AREA_SEVERITY[areaType],
          color: RESTRICTED_AREA_COLORS[areaType],
          icon: RESTRICTED_AREA_ICONS[areaType],
          source: 'noaa-mpa',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] NOAA MPA fetch failed:', err);
  }
  return areas;
}

// ── OSM Overpass — Restricted areas ──────────────────────────────

async function fetchOSMRestrictedAreas(bbox: BBox): Promise<RestrictedArea[]> {
  const areas: RestrictedArea[] = [];
  try {
    const query = `[out:json][timeout:10];
      (
        way["seamark:type"="restricted_area"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
        relation["seamark:type"="restricted_area"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
        way["seamark:type"="military_area"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
      );
      out body geom;`;
    const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

    const data = await fetchJSON<{
      elements?: Array<{
        id: number;
        geometry?: Array<{ lat: number; lon: number }>;
        tags?: Record<string, string>;
      }>;
    }>(url);

    if (data.elements) {
      for (const el of data.elements) {
        if (!el.geometry || el.geometry.length < 3) continue;
        const coords = el.geometry.map((g) => ({ lat: g.lat, lon: g.lon }));
        const seamarkType = el.tags?.['seamark:type'] ?? '';
        const areaType: RestrictedAreaType = seamarkType.includes('military')
          ? 'military'
          : 'other';

        areas.push({
          id: `osm-restricted-${el.id}`,
          name: el.tags?.name ?? el.tags?.['seamark:name'] ?? 'Restricted Area',
          type: areaType,
          coordinates: coords,
          authority: el.tags?.['seamark:restricted_area:authority'] ?? 'Unknown',
          restrictions: el.tags?.['seamark:restricted_area:restriction'] ?? 'Restricted area',
          severity: RESTRICTED_AREA_SEVERITY[areaType],
          color: RESTRICTED_AREA_COLORS[areaType],
          icon: RESTRICTED_AREA_ICONS[areaType],
          source: 'osm',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] OSM restricted areas fetch failed:', err);
  }
  return areas;
}

// ── Classification helpers ───────────────────────────────────────

function classifyRestrictedAreaType(
  layer: string,
  catRes?: string,
  restrn?: string,
): RestrictedAreaType {
  const combined = `${layer} ${catRes ?? ''} ${restrn ?? ''}`.toLowerCase();
  if (combined.includes('mipar') || combined.includes('military')) return 'military';
  if (combined.includes('sanctuary') || combined.includes('nature')) return 'marine_sanctuary';
  if (combined.includes('security')) return 'security_zone';
  if (combined.includes('anchor')) return 'anchoring_prohibited';
  if (combined.includes('fish')) return 'fishing_prohibited';
  if (combined.includes('speed')) return 'speed_restricted';
  return 'other';
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get shipping lanes in the bounding box.
 */
export async function getShippingLanes(bbox: BBox): Promise<ShippingLane[]> {
  if (isCacheValid(shippingLaneCache, bbox) && shippingLaneCache) {
    return shippingLaneCache.data;
  }

  const expandedBBox: BBox = {
    minLat: bbox.minLat - 0.1,
    maxLat: bbox.maxLat + 0.1,
    minLon: bbox.minLon - 0.1,
    maxLon: bbox.maxLon + 0.1,
  };

  const [noaaLanes, osmLanes] = await Promise.all([
    fetchNOAAShippingLanes(expandedBBox),
    fetchOSMShippingLanes(expandedBBox),
  ]);

  const allLanes = [...noaaLanes, ...osmLanes];
  shippingLaneCache = { data: allLanes, timestamp: Date.now(), bbox: expandedBBox };
  return allLanes;
}

/**
 * Get restricted areas in the bounding box.
 * Fetches from NOAA ENC, OSM, and NOAA MPA in parallel.
 */
export async function getRestrictedAreas(bbox: BBox): Promise<RestrictedArea[]> {
  if (isCacheValid(restrictedAreaCache, bbox) && restrictedAreaCache) {
    return restrictedAreaCache.data;
  }

  const expandedBBox: BBox = {
    minLat: bbox.minLat - 0.1,
    maxLat: bbox.maxLat + 0.1,
    minLon: bbox.minLon - 0.1,
    maxLon: bbox.maxLon + 0.1,
  };

  const [noaaAreas, osmAreas, mpaAreas] = await Promise.all([
    fetchNOAARestrictedAreas(expandedBBox),
    fetchOSMRestrictedAreas(expandedBBox),
    fetchNOAAMPA(expandedBBox),
  ]);

  const allAreas = [...noaaAreas, ...osmAreas, ...mpaAreas];
  restrictedAreaCache = { data: allAreas, timestamp: Date.now(), bbox: expandedBBox };
  return allAreas;
}

/**
 * Get traffic separation schemes in the bounding box.
 */
export async function getTrafficSeparationSchemes(bbox: BBox): Promise<TrafficSeparationScheme[]> {
  if (isCacheValid(tssCache, bbox) && tssCache) {
    return tssCache.data;
  }

  // TSS data comes from the same NOAA/OSM queries as shipping lanes
  // Build TSS from paired lane data
  const schemes: TrafficSeparationScheme[] = [];

  try {
    const query = `[out:json][timeout:10];
      (
        way["seamark:type"="separation_zone"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
        relation["seamark:type"="separation_scheme"](${bbox.minLat},${bbox.minLon},${bbox.maxLat},${bbox.maxLon});
      );
      out body geom;`;
    const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

    const data = await fetchJSON<{
      elements?: Array<{
        id: number;
        geometry?: Array<{ lat: number; lon: number }>;
        tags?: Record<string, string>;
      }>;
    }>(url);

    if (data.elements) {
      for (const el of data.elements) {
        if (!el.geometry || el.geometry.length < 3) continue;
        schemes.push({
          id: `osm-tss-${el.id}`,
          name: el.tags?.name ?? el.tags?.['seamark:name'] ?? 'Traffic Separation Scheme',
          separationZone: el.geometry.map((g) => ({ lat: g.lat, lon: g.lon })),
          lanes: [],
          source: 'osm',
        });
      }
    }
  } catch (err) {
    console.warn('[MaritimeRoutes] TSS fetch failed:', err);
  }

  tssCache = { data: schemes, timestamp: Date.now(), bbox };
  return schemes;
}

// ── Point-in-polygon test ────────────────────────────────────────

function isPointInPolygon(point: LatLng, polygon: LatLng[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].lon;
    const yi = polygon[i].lat;
    const xj = polygon[j].lon;
    const yj = polygon[j].lat;

    const intersect =
      yi > point.lat !== yj > point.lat &&
      point.lon < ((xj - xi) * (point.lat - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Check if a specific point is inside any restricted area.
 */
export function isPointRestricted(
  point: LatLng,
  areas: RestrictedArea[],
): RestrictedArea | null {
  for (const area of areas) {
    if (isPointInPolygon(point, area.coordinates)) {
      return area;
    }
  }
  return null;
}

/**
 * Check if a route segment intersects any restricted area.
 * Samples points along the segment at fine intervals.
 */
export function doesSegmentCrossRestricted(
  from: LatLng,
  to: LatLng,
  areas: RestrictedArea[],
  samples: number = 20,
): RestrictedArea | null {
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const point: LatLng = {
      lat: from.lat + (to.lat - from.lat) * t,
      lon: from.lon + (to.lon - from.lon) * t,
    };
    const restricted = isPointRestricted(point, areas);
    if (restricted) return restricted;
  }
  return null;
}

// ── GeoJSON builders ─────────────────────────────────────────────

export function shippingLanesToGeoJSON(lanes: ShippingLane[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: lanes.map((lane) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'LineString' as const,
        coordinates: lane.coordinates.map((c) => [c.lon, c.lat]),
      },
      properties: {
        id: lane.id,
        name: lane.name,
        directionDeg: lane.directionDeg,
        widthNm: lane.widthNm,
        type: 'shipping_lane',
      },
    })),
  };
}

export function restrictedAreasToGeoJSON(areas: RestrictedArea[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: areas.map((area) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Polygon' as const,
        coordinates: [area.coordinates.map((c) => [c.lon, c.lat])],
      },
      properties: {
        id: area.id,
        name: area.name,
        areaType: area.type,
        authority: area.authority,
        restrictions: area.restrictions,
        severity: area.severity,
        color: area.color,
        icon: area.icon,
      },
    })),
  };
}

export function tssToGeoJSON(schemes: TrafficSeparationScheme[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: schemes.map((tss) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Polygon' as const,
        coordinates: [tss.separationZone.map((c) => [c.lon, c.lat])],
      },
      properties: {
        id: tss.id,
        name: tss.name,
        type: 'separation_zone',
      },
    })),
  };
}
