/**
 * USACE Depth Survey Service for OpenCatch.
 *
 * Integrates multiple US Army Corps of Engineers data sources:
 *
 * 1. **eHydro** — Hydrographic survey data for navigation channels.
 *    ArcGIS Feature Service on geospatial-usace.opendata.arcgis.com
 *    Format: GeoJSON via ArcGIS REST API (no key required).
 *    Coverage: All USACE-maintained navigation channels nationwide.
 *    Update frequency: Surveys conducted on varying schedules per district.
 *
 * 2. **CWMS Data API (CDA)** — Real-time reservoir levels, dam releases, flows.
 *    REST API at cwms-data.usace.army.mil/cwms-data
 *    Swagger docs: cwms-data.usace.army.mil/cwms-data/swagger-ui.html
 *    Format: JSON time-series data (no key required).
 *    Coverage: ~700 USACE reservoirs, locks, and water control projects.
 *    Update frequency: Near real-time (hourly for most stations).
 *
 * 3. **National Inventory of Dams (NID)** — Dam locations and metadata.
 *    ArcGIS Feature Service on geospatial-usace.opendata.arcgis.com
 *    Format: GeoJSON (no key required).
 *    Coverage: ~90,000 dams in the US.
 *
 * 4. **National Channel Framework (NCF)** — Maintained channel geometries.
 *    ArcGIS Feature Service for channel boundaries and authorized depths.
 *
 * 5. **Corps Locks** — Lock operational status from LPMS data.
 *    ndc.ops.usace.army.mil — lock performance and status data.
 *
 * All data is free, public, and requires no API key.
 */

// ── Types ────────────────────────────────────────────────────────

/** Bounding box for spatial queries. */
export interface SurveyBBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** A hydrographic channel survey from eHydro. */
export interface ChannelSurvey {
  id: string;
  /** Channel or project name. */
  channelName: string;
  /** USACE district responsible for the channel. */
  district: string;
  /** Authorized (design) depth in feet. */
  authorizedDepthFt: number | null;
  /** Most recent surveyed controlling depth in feet. */
  surveyedDepthFt: number | null;
  /** Date of the most recent survey (ISO-8601). */
  surveyDate: string | null;
  /** Channel status based on depth comparison. */
  status: 'adequate' | 'shoaling' | 'restricted' | 'unknown';
  /** GeoJSON geometry for the survey area (polyline or polygon). */
  geometry: GeoJSON.Geometry | null;
  /** Survey condition: ratio of surveyed/authorized depth. */
  depthRatio: number | null;
}

/** Maintained channel geometry from the National Channel Framework. */
export interface MaintainedChannel {
  id: string;
  channelName: string;
  district: string;
  authorizedDepthFt: number | null;
  maintainedWidthFt: number | null;
  geometry: GeoJSON.Geometry | null;
}

/** Real-time reservoir/lake level from CWMS. */
export interface ReservoirLevel {
  /** CWMS location identifier. */
  stationId: string;
  /** Human-readable station name. */
  name: string;
  /** Current pool elevation in feet NGVD29/NAVD88. */
  currentElevationFt: number | null;
  /** Normal/conservation pool elevation in feet. */
  normalPoolFt: number | null;
  /** Flood pool elevation in feet. */
  floodPoolFt: number | null;
  /** Storage in acre-feet, if available. */
  storageAcreFt: number | null;
  /** Percent of normal pool. */
  percentNormal: number | null;
  /** Trend: rising, falling, or stable. */
  trend: 'rising' | 'falling' | 'stable';
  /** Last reading timestamp (ISO-8601). */
  lastUpdated: string | null;
  /** CWMS office identifier (e.g. "SWL", "LRL"). */
  office: string;
}

/** Downstream release data from a USACE dam. */
export interface DamRelease {
  /** CWMS location identifier. */
  stationId: string;
  /** Dam name. */
  name: string;
  /** Current tailwater release rate in cubic feet per second. */
  releaseCfs: number | null;
  /** Current tailwater elevation in feet. */
  tailwaterElevFt: number | null;
  /** Trend of releases. */
  trend: 'increasing' | 'decreasing' | 'stable';
  /** Last reading timestamp (ISO-8601). */
  lastUpdated: string | null;
  /** CWMS office identifier. */
  office: string;
}

/** Lock operational status from Corps Locks / LPMS. */
export interface LockStatus {
  /** Lock identifier. */
  lockId: string;
  /** Lock name. */
  name: string;
  /** Latitude. */
  lat: number;
  /** Longitude. */
  lon: number;
  /** Operational status. */
  status: 'open' | 'closed' | 'restricted' | 'unknown';
  /** River or waterway name. */
  waterway: string;
  /** Chamber dimensions if known. */
  chamberLengthFt: number | null;
  chamberWidthFt: number | null;
  /** Average wait time in minutes, if available. */
  avgWaitMinutes: number | null;
}

/** Harbor/port survey from eHydro. */
export interface HarborSurvey {
  id: string;
  /** Harbor or port name. */
  name: string;
  /** Latitude of the harbor center. */
  lat: number;
  /** Longitude of the harbor center. */
  lon: number;
  /** Authorized depth in feet. */
  authorizedDepthFt: number | null;
  /** Most recent surveyed depth in feet. */
  surveyedDepthFt: number | null;
  /** Survey date (ISO-8601). */
  surveyDate: string | null;
  /** USACE district. */
  district: string;
  /** Channel status. */
  status: 'adequate' | 'shoaling' | 'restricted' | 'unknown';
}

/** Dam from the National Inventory of Dams. */
export interface NIDDam {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** River the dam is on. */
  river: string;
  /** State abbreviation. */
  state: string;
  /** Dam height in feet. */
  heightFt: number | null;
  /** Normal storage in acre-feet. */
  storageAcreFt: number | null;
  /** Primary purpose (flood control, navigation, hydroelectric, etc.). */
  purpose: string;
  /** Owner type (federal, state, local, private). */
  ownerType: string;
  /** Year completed. */
  yearCompleted: number | null;
  /** Hazard classification. */
  hazard: 'high' | 'significant' | 'low' | 'undetermined';
}

// ── Constants ────────────────────────────────────────────────────

/** eHydro survey data — ArcGIS Feature Service (public, no key). */
const EHYDRO_SURVEY_URL =
  'https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/eHydro_Survey_Data/FeatureServer/0/query';

/** National Channel Framework — channel geometries. */
const NCF_CHANNEL_URL =
  'https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/National_Channel_Framework/FeatureServer/0/query';

/** National Inventory of Dams — ArcGIS Feature Service. */
const NID_URL =
  'https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/NID/FeatureServer/0/query';

/** CWMS Data API — time series and location catalog. */
const CWMS_BASE = 'https://cwms-data.usace.army.mil/cwms-data';

/** Corps Locks — lock status data. */
const CORPS_LOCKS_URL =
  'https://ndc.ops.usace.army.mil/ords/lpms_pub/lpms/locks';

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 20_000;

/** Cache TTL: 30 minutes for survey data (changes infrequently). */
const CACHE_TTL_MS = 30 * 60 * 1000;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const surveyCache = new Map<string, CacheEntry<any>>();

function getCached<T>(key: string): T | null {
  const entry = surveyCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    surveyCache.delete(key);
    return null;
  }
  return entry.data as T;
}

function setCache<T>(key: string, data: T): void {
  surveyCache.set(key, { data, timestamp: Date.now() });
}

function bboxKey(prefix: string, bbox: SurveyBBox): string {
  const r = (n: number) => Math.round(n * 100) / 100;
  return `${prefix}:${r(bbox.west)},${r(bbox.south)},${r(bbox.east)},${r(bbox.north)}`;
}

// ── Fetch helpers ────────────────────────────────────────────────

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

interface ArcGISFeatureResponse {
  features?: Array<{
    attributes: Record<string, any>;
    geometry?: any;
  }>;
}

// ── eHydro: Channel Surveys ──────────────────────────────────────

function classifyChannelStatus(
  authorizedFt: number | null,
  surveyedFt: number | null,
): ChannelSurvey['status'] {
  if (authorizedFt == null || surveyedFt == null) return 'unknown';
  const ratio = surveyedFt / authorizedFt;
  if (ratio >= 0.95) return 'adequate';
  if (ratio >= 0.85) return 'shoaling';
  return 'restricted';
}

/**
 * Fetch USACE eHydro channel depth surveys within a bounding box.
 *
 * Returns survey data for navigation channels including authorized
 * vs. surveyed depths, survey dates, and channel status.
 */
export async function getChannelSurveys(bbox: SurveyBBox): Promise<ChannelSurvey[]> {
  const key = bboxKey('ehydro-channel', bbox);
  const cached = getCached<ChannelSurvey[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${EHYDRO_SURVEY_URL}` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=geojson&resultRecordCount=500`;

  try {
    const data = await fetchJSON<GeoJSON.FeatureCollection>(url);
    const results: ChannelSurvey[] = (data.features ?? []).map((f) => {
      const p = f.properties ?? {};
      const authDepth = p.authDepth ?? p.AuthorizedDepth ?? p.AUTHORIZED_DEPTH ?? null;
      const survDepth = p.surveyDepth ?? p.SurveyedDepth ?? p.SURVEYED_DEPTH ?? p.controllingDepth ?? null;
      const status = classifyChannelStatus(authDepth, survDepth);
      const depthRatio = authDepth && survDepth ? survDepth / authDepth : null;

      return {
        id: String(p.OBJECTID ?? p.objectid ?? p.FID ?? ''),
        channelName: p.channelName ?? p.ChannelName ?? p.PROJECT_NAME ?? p.projectName ?? 'Unknown Channel',
        district: p.district ?? p.District ?? p.DISTRICT ?? '',
        authorizedDepthFt: typeof authDepth === 'number' ? authDepth : null,
        surveyedDepthFt: typeof survDepth === 'number' ? survDepth : null,
        surveyDate: p.surveyDate ?? p.SurveyDate ?? p.SURVEY_DATE ?? null,
        status,
        geometry: f.geometry ?? null,
        depthRatio,
      };
    });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getChannelSurveys failed:', err);
    return [];
  }
}

/**
 * Fetch maintained channel network geometry from the National Channel Framework.
 *
 * This is the better routing backbone than generic survey footprints because it
 * represents the maintained navigation corridor itself.
 */
export async function getMaintainedChannels(bbox: SurveyBBox): Promise<MaintainedChannel[]> {
  const key = bboxKey('ncf-channel', bbox);
  const cached = getCached<MaintainedChannel[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${NCF_CHANNEL_URL}` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=geojson&resultRecordCount=500`;

  try {
    const data = await fetchJSON<GeoJSON.FeatureCollection>(url);
    const results: MaintainedChannel[] = (data.features ?? []).map((f) => {
      const p = f.properties ?? {};
      const depth =
        p.authorizedDepth ??
        p.AUTHORIZED_DEPTH ??
        p.auth_depth ??
        p.channelDepth ??
        p.designDepth ??
        null;
      const width =
        p.authorizedWidth ??
        p.AUTHORIZED_WIDTH ??
        p.maintainedWidth ??
        p.channelWidth ??
        p.designWidth ??
        null;
      return {
        id: String(p.OBJECTID ?? p.objectid ?? p.FID ?? p.id ?? ''),
        channelName:
          p.channelName ??
          p.ChannelName ??
          p.projectName ??
          p.PROJECT_NAME ??
          p.name ??
          'Maintained Channel',
        district: p.district ?? p.District ?? p.DISTRICT ?? '',
        authorizedDepthFt: typeof depth === 'number' ? depth : null,
        maintainedWidthFt: typeof width === 'number' ? width : null,
        geometry: f.geometry ?? null,
      };
    });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getMaintainedChannels failed:', err);
    return [];
  }
}

// ── CWMS: Reservoir Levels ───────────────────────────────────────

interface CWMSTimeSeriesResponse {
  values?: Array<[number, number, number]>; // [timestamp_ms, value, quality]
  name?: string;
  'office-id'?: string;
}

interface CWMSLocationResponse {
  locations?: {
    locations?: Array<{
      name: string;
      'office-id': string;
      latitude: number;
      longitude: number;
      'public-name'?: string;
    }>;
  };
}

/**
 * Fetch current reservoir/lake levels from CWMS.
 *
 * Uses the CWMS Data API (CDA) to retrieve the latest pool elevation
 * time-series data for a given USACE station.
 *
 * @param stationId — CWMS location identifier (e.g. "Table Rock Lake-Lake")
 * @param office — CWMS office code (e.g. "SWL" for Little Rock district)
 */
export async function getReservoirLevels(
  stationId: string,
  office: string = '',
): Promise<ReservoirLevel | null> {
  const key = `cwms-level:${office}:${stationId}`;
  const cached = getCached<ReservoirLevel>(key);
  if (cached) return cached;

  // Request the last 48 hours of pool elevation data
  const now = new Date();
  const begin = new Date(now.getTime() - 48 * 3600 * 1000);
  const tsName = encodeURIComponent(`${stationId}.Elev.Inst.1Hour.0.Raw-USACE`);
  const officeParam = office ? `&office=${encodeURIComponent(office)}` : '';

  const url =
    `${CWMS_BASE}/timeseries?name=${tsName}` +
    `&begin=${begin.toISOString()}&end=${now.toISOString()}` +
    `${officeParam}&format=json`;

  try {
    const data = await fetchJSON<CWMSTimeSeriesResponse>(url);
    const values = data.values ?? [];
    if (values.length === 0) return null;

    // Latest reading
    const latest = values[values.length - 1];
    const currentElev = latest[1];

    // Determine trend from last few readings
    let trend: ReservoirLevel['trend'] = 'stable';
    if (values.length >= 4) {
      const prev = values[values.length - 4][1];
      const diff = currentElev - prev;
      if (diff > 0.05) trend = 'rising';
      else if (diff < -0.05) trend = 'falling';
    }

    const result: ReservoirLevel = {
      stationId,
      name: data.name ?? stationId,
      currentElevationFt: currentElev,
      normalPoolFt: null, // Would need separate metadata lookup
      floodPoolFt: null,
      storageAcreFt: null,
      percentNormal: null,
      trend,
      lastUpdated: new Date(latest[0]).toISOString(),
      office: data['office-id'] ?? office,
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getReservoirLevels failed:', err);
    return null;
  }
}

// ── CWMS: Dam Releases ───────────────────────────────────────────

/**
 * Fetch downstream release data for a USACE dam.
 *
 * @param stationId — CWMS location identifier for the dam tailwater.
 * @param office — CWMS office code.
 */
export async function getDamReleases(
  stationId: string,
  office: string = '',
): Promise<DamRelease | null> {
  const key = `cwms-release:${office}:${stationId}`;
  const cached = getCached<DamRelease>(key);
  if (cached) return cached;

  const now = new Date();
  const begin = new Date(now.getTime() - 48 * 3600 * 1000);
  const tsName = encodeURIComponent(`${stationId}.Flow-Out.Ave.1Hour.1Hour.Rev-USACE`);
  const officeParam = office ? `&office=${encodeURIComponent(office)}` : '';

  const url =
    `${CWMS_BASE}/timeseries?name=${tsName}` +
    `&begin=${begin.toISOString()}&end=${now.toISOString()}` +
    `${officeParam}&format=json`;

  try {
    const data = await fetchJSON<CWMSTimeSeriesResponse>(url);
    const values = data.values ?? [];
    if (values.length === 0) return null;

    const latest = values[values.length - 1];
    const currentFlow = latest[1];

    let trend: DamRelease['trend'] = 'stable';
    if (values.length >= 4) {
      const prev = values[values.length - 4][1];
      const diff = currentFlow - prev;
      if (diff > currentFlow * 0.05) trend = 'increasing';
      else if (diff < -(currentFlow * 0.05)) trend = 'decreasing';
    }

    const result: DamRelease = {
      stationId,
      name: data.name ?? stationId,
      releaseCfs: currentFlow,
      tailwaterElevFt: null,
      trend,
      lastUpdated: new Date(latest[0]).toISOString(),
      office: data['office-id'] ?? office,
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getDamReleases failed:', err);
    return null;
  }
}

// ── Lock Status ──────────────────────────────────────────────────

/**
 * Fetch lock & dam operational status within a bounding box.
 *
 * Uses the NID dam dataset filtered by purpose="navigation" as a proxy,
 * since the full LPMS API requires additional auth. The NID dataset
 * includes lock locations and basic metadata.
 */
export async function getLockStatus(bbox: SurveyBBox): Promise<LockStatus[]> {
  const key = bboxKey('locks', bbox);
  const cached = getCached<LockStatus[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  // Query NID for navigation dams (locks) in the area
  const url =
    `${NID_URL}` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&where=${encodeURIComponent("PURPOSES LIKE '%N%'")}` +
    `&outFields=*&f=json&resultRecordCount=200`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: LockStatus[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        return {
          lockId: String(a.NIDID ?? a.NID_ID ?? a.OBJECTID ?? ''),
          name: a.DAM_NAME ?? a.Name ?? 'Unknown Lock',
          lat: f.geometry?.y ?? 0,
          lon: f.geometry?.x ?? 0,
          status: 'open' as const, // NID doesn't have real-time status
          waterway: a.RIVER ?? a.River ?? '',
          chamberLengthFt: a.LOCK_LENGTH ?? null,
          chamberWidthFt: a.LOCK_WIDTH ?? null,
          avgWaitMinutes: null,
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getLockStatus failed:', err);
    return [];
  }
}

// ── eHydro: Harbor Surveys ───────────────────────────────────────

/**
 * Fetch harbor/port depth survey results within a bounding box.
 *
 * Returns eHydro survey data for harbor entrance channels and
 * turning basins, which are critical for vessel draft clearance.
 */
export async function getHarborSurveys(bbox: SurveyBBox): Promise<HarborSurvey[]> {
  const key = bboxKey('harbor-survey', bbox);
  const cached = getCached<HarborSurvey[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${EHYDRO_SURVEY_URL}` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&where=${encodeURIComponent("projectType LIKE '%Harbor%' OR projectType LIKE '%Port%'")}` +
    `&outFields=*&f=json&resultRecordCount=300`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: HarborSurvey[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        const authDepth = a.authDepth ?? a.AuthorizedDepth ?? a.AUTHORIZED_DEPTH ?? null;
        const survDepth = a.surveyDepth ?? a.SurveyedDepth ?? a.SURVEYED_DEPTH ?? null;

        return {
          id: String(a.OBJECTID ?? a.objectid ?? ''),
          name: a.channelName ?? a.ChannelName ?? a.PROJECT_NAME ?? 'Harbor Channel',
          lat: f.geometry?.y ?? f.geometry?.coordinates?.[1] ?? 0,
          lon: f.geometry?.x ?? f.geometry?.coordinates?.[0] ?? 0,
          authorizedDepthFt: typeof authDepth === 'number' ? authDepth : null,
          surveyedDepthFt: typeof survDepth === 'number' ? survDepth : null,
          surveyDate: a.surveyDate ?? a.SurveyDate ?? a.SURVEY_DATE ?? null,
          district: a.district ?? a.District ?? a.DISTRICT ?? '',
          status: classifyChannelStatus(authDepth, survDepth),
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getHarborSurveys failed:', err);
    return [];
  }
}

// ── NID: Dams in area ────────────────────────────────────────────

/**
 * Fetch dams from the National Inventory of Dams within a bounding box.
 * Useful for identifying USACE-managed reservoirs near the user.
 */
export async function getDamsInArea(bbox: SurveyBBox): Promise<NIDDam[]> {
  const key = bboxKey('nid-dams', bbox);
  const cached = getCached<NIDDam[]>(key);
  if (cached) return cached;

  const envelope = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const url =
    `${NID_URL}` +
    `?geometry=${encodeURIComponent(envelope)}` +
    `&geometryType=esriGeometryEnvelope` +
    `&inSR=4326&outSR=4326&spatialRel=esriSpatialRelIntersects` +
    `&outFields=*&f=json&resultRecordCount=300`;

  try {
    const data = await fetchJSON<ArcGISFeatureResponse>(url);
    const results: NIDDam[] = (data.features ?? [])
      .filter((f) => f.geometry)
      .map((f) => {
        const a = f.attributes;
        const hazardRaw = (a.HAZARD ?? a.Hazard ?? '').toLowerCase();
        let hazard: NIDDam['hazard'] = 'undetermined';
        if (hazardRaw.includes('high')) hazard = 'high';
        else if (hazardRaw.includes('significant')) hazard = 'significant';
        else if (hazardRaw.includes('low')) hazard = 'low';

        return {
          id: String(a.NIDID ?? a.NID_ID ?? a.OBJECTID ?? ''),
          name: a.DAM_NAME ?? a.Name ?? 'Unknown Dam',
          lat: f.geometry?.y ?? 0,
          lon: f.geometry?.x ?? 0,
          river: a.RIVER ?? a.River ?? '',
          state: a.STATE ?? a.State ?? '',
          heightFt: a.DAM_HEIGHT ?? a.NID_HEIGHT ?? null,
          storageAcreFt: a.NID_STORAGE ?? a.NORMAL_STORAGE ?? null,
          purpose: a.PURPOSES ?? a.Purpose ?? '',
          ownerType: a.OWNER_TYPE ?? a.OwnerType ?? '',
          yearCompleted: a.YEAR_COMPLETED ?? a.YearCompleted ?? null,
          hazard,
        };
      });

    setCache(key, results);
    return results;
  } catch (err) {
    console.warn('[usaceDepthSurveys] getDamsInArea failed:', err);
    return [];
  }
}

// ── GeoJSON Helpers ──────────────────────────────────────────────

/** Status-to-color mapping for channel surveys. */
export const CHANNEL_STATUS_COLORS: Record<ChannelSurvey['status'], string> = {
  adequate: '#4CAF50',   // green
  shoaling: '#FFC107',   // yellow/amber
  restricted: '#F44336', // red
  unknown: '#9E9E9E',    // grey
};

/** Convert ChannelSurvey array to GeoJSON for map rendering. */
export function channelSurveysToGeoJSON(
  surveys: ChannelSurvey[],
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: surveys
      .filter((s) => s.geometry)
      .map((s) => ({
        type: 'Feature' as const,
        geometry: s.geometry!,
        properties: {
          id: s.id,
          channelName: s.channelName,
          status: s.status,
          authorizedDepthFt: s.authorizedDepthFt,
          surveyedDepthFt: s.surveyedDepthFt,
          surveyDate: s.surveyDate,
          color: CHANNEL_STATUS_COLORS[s.status],
        },
      })),
  };
}

/** Convert LockStatus array to GeoJSON for map rendering. */
export function locksToGeoJSON(locks: LockStatus[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: locks.map((lock) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [lock.lon, lock.lat],
      },
      properties: {
        id: lock.lockId,
        name: lock.name,
        status: lock.status,
        waterway: lock.waterway,
      },
    })),
  };
}

/** Convert HarborSurvey array to GeoJSON. */
export function harborSurveysToGeoJSON(
  harbors: HarborSurvey[],
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: harbors.map((h) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [h.lon, h.lat],
      },
      properties: {
        id: h.id,
        name: h.name,
        status: h.status,
        authorizedDepthFt: h.authorizedDepthFt,
        surveyedDepthFt: h.surveyedDepthFt,
        surveyDate: h.surveyDate,
        color: CHANNEL_STATUS_COLORS[h.status],
      },
    })),
  };
}

/** Convert NIDDam array to GeoJSON. */
export function damsToGeoJSON(dams: NIDDam[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: dams.map((d) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [d.lon, d.lat],
      },
      properties: {
        id: d.id,
        name: d.name,
        river: d.river,
        state: d.state,
        purpose: d.purpose,
        hazard: d.hazard,
      },
    })),
  };
}
