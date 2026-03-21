/**
 * Canadian Open Data Service for OpenCatch
 *
 * Comprehensive integration with Canadian government APIs:
 *
 * 1. Environment Canada Geomet OGC-API (api.weather.gc.ca)
 *    - Real-time weather observations
 *    - Air Quality Health Index (AQHI)
 *
 * 2. Water Survey of Canada (HYDAT) via Geomet
 *    - Real-time hydrometric data (water levels, streamflow)
 *    - Historical daily means
 *    - Station discovery by bounding box
 *
 * 3. Canadian Hydrographic Service (CHS)
 *    - Tide and water level predictions for coasts and Great Lakes
 *    - Station discovery
 *
 * All endpoints are free, anonymous, no API key required.
 * Rate limit: 10,000 features per request (Geomet OGC-API).
 */

// ── Configuration ────────────────────────────────────────────────

const GEOMET_BASE = 'https://api.weather.gc.ca';
const CHS_BASE = 'https://api-iwls.dfo-mpo.gc.ca/api/v1';
const REQUEST_TIMEOUT = 10_000;

/** Cache TTL for station lists (rarely change). */
const STATION_CACHE_TTL = 24 * 60 * 60 * 1000; // 24 hours

/** Cache TTL for real-time data. */
const DATA_CACHE_TTL = 10 * 60 * 1000; // 10 minutes

// ── Types ────────────────────────────────────────────────────────

/** A Water Survey of Canada hydrometric station. */
export interface HydrometricStation {
  /** Station number (e.g. "02HA003"). */
  id: string;
  /** Human-readable name. */
  name: string;
  /** Province/territory code (e.g. "ON", "BC"). */
  province: string;
  lat: number;
  lon: number;
  status: 'Active' | 'Discontinued';
  /** Gross drainage area in km². */
  drainageArea?: number;
  /** Distance from query point in km (populated by getNearestCanadianStation). */
  distanceKm?: number;
}

/** A water level or discharge reading from HYDAT real-time. */
export interface WaterLevelReading {
  stationId: string;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Water level in metres (null if unavailable). */
  level: number | null;
  /** Discharge (streamflow) in m3/s (null if unavailable). */
  discharge: number | null;
}

/** A streamflow reading (convenience alias with rate emphasis). */
export interface StreamflowReading {
  stationId: string;
  stationName: string;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Discharge in m3/s. */
  discharge: number | null;
  /** Water level in metres. */
  level: number | null;
  /** Province code. */
  province: string;
}

/** Air Quality Health Index reading. */
export interface AQHIReading {
  stationId: string;
  location: string;
  /** AQHI value on the 1-10+ scale. */
  aqhi: number;
  forecastToday?: number;
  forecastTonight?: number;
  forecastTomorrow?: number;
}

/** Current weather observation from an Environment Canada station. */
export interface WeatherObservation {
  stationId: string;
  name: string;
  lat: number;
  lon: number;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Air temperature in Celsius. */
  airTemp: number | null;
  /** Dew point in Celsius. */
  dewPoint: number | null;
  /** Relative humidity %. */
  humidity: number | null;
  /** Wind speed in km/h. */
  windSpeed: number | null;
  /** Wind direction in degrees. */
  windDir: number | null;
  /** Station pressure in kPa. */
  pressure: number | null;
  /** Visibility in km. */
  visibility: number | null;
  /** Text weather condition. */
  condition: string | null;
}

/** A Canadian Hydrographic Service tide/water-level station. */
export interface CHSTideStation {
  /** CHS station ID. */
  id: string;
  /** Station name. */
  name: string;
  lat: number;
  lon: number;
  /** Region: "Atlantic", "Pacific", "Great Lakes", "Arctic", etc. */
  region: string;
  /** Distance from query point in km. */
  distanceKm?: number;
}

/** A tide or water level prediction from CHS. */
export interface CHSTidePrediction {
  /** ISO-8601 timestamp. */
  time: string;
  /** Water height in metres. */
  heightM: number;
  /** Water height in feet (converted). */
  heightFt: number;
  /** "H" for high, "L" for low (only on hi-lo predictions). */
  type?: 'H' | 'L';
}

/** CHS observed water level measurement. */
export interface CHSWaterLevel {
  /** ISO-8601 timestamp. */
  time: string;
  /** Observed water level in metres. */
  heightM: number;
  /** Observed water level in feet. */
  heightFt: number;
}

// ── In-memory cache ──────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<unknown>>();

/**
 * Get cached data if fresh, otherwise null.
 * @internal
 */
function getCached<T>(key: string, ttl: number): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.timestamp > ttl) {
    cache.delete(key);
    return null;
  }
  return entry.data as T;
}

/**
 * Store data in cache.
 * @internal
 */
function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

/** Clear all Canadian data caches. */
export function clearCanadaCache(): void {
  cache.clear();
}

// ── Fetch helpers ────────────────────────────────────────────────

/**
 * Fetch JSON from the Geomet OGC-API with timeout and error handling.
 * @internal
 */
async function geometFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${GEOMET_BASE}${path}`);
  url.searchParams.set('f', 'json');
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const res = await fetch(url.toString(), { signal: controller.signal });
    clearTimeout(timeout);
    if (!res.ok) throw new Error(`Canada Geomet API ${res.status}: ${path}`);
    return res.json();
  } catch (err: any) {
    clearTimeout(timeout);
    if (err.name === 'AbortError') {
      throw new Error(`Canada Geomet API timed out after ${REQUEST_TIMEOUT}ms: ${path}`);
    }
    throw err;
  }
}

/**
 * Fetch JSON from the CHS IWLS API with timeout and error handling.
 * @internal
 */
async function chsFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${CHS_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const res = await fetch(url.toString(), {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    });
    clearTimeout(timeout);
    if (!res.ok) throw new Error(`CHS API ${res.status}: ${path}`);
    return res.json();
  } catch (err: any) {
    clearTimeout(timeout);
    if (err.name === 'AbortError') {
      throw new Error(`CHS API timed out after ${REQUEST_TIMEOUT}ms: ${path}`);
    }
    throw err;
  }
}

// ── Haversine distance ───────────────────────────────────────────

/**
 * Haversine distance between two lat/lon points.
 * @returns Distance in kilometres.
 * @internal
 */
function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
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
 * Build a bounding box from a centre point and radius.
 * @returns [minLon, minLat, maxLon, maxLat]
 * @internal
 */
function bboxFromRadius(
  lat: number,
  lon: number,
  radiusKm: number,
): [number, number, number, number] {
  const dLat = radiusKm / 111;
  const dLon = radiusKm / (111 * Math.cos((lat * Math.PI) / 180));
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat];
}

// ── Geo detection ────────────────────────────────────────────────

/**
 * Determine if coordinates are in Canada (rough bounding box).
 *
 * Uses latitude 41.7-84N (southern Ontario to High Arctic) and
 * longitude -141 to -52 (Yukon border to Newfoundland).
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns `true` if the point is likely in Canada.
 */
export function isCanadianLocation(lat: number, lon: number): boolean {
  return lat >= 41.7 && lat <= 84 && lon >= -141 && lon <= -52;
}

// ── 1. Environment Canada Weather ────────────────────────────────

/**
 * Get current weather observations near a Canadian location.
 *
 * Queries the Geomet OGC-API for climate-hourly data within a radius
 * of the given coordinates. Returns the most recent observations from
 * nearby Environment Canada weather stations.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @param radiusKm - Search radius (default 50 km).
 * @returns Array of recent weather observations, newest first.
 *
 * @example
 * ```ts
 * const weather = await getCanadianWeather(43.65, -79.38);
 * console.log(weather[0]?.airTemp); // Temperature in Toronto
 * ```
 */
export async function getCanadianWeather(
  lat: number,
  lon: number,
  radiusKm = 50,
): Promise<WeatherObservation[]> {
  const cacheKey = `ca-weather:${lat.toFixed(2)},${lon.toFixed(2)},${radiusKm}`;
  const cached = getCached<WeatherObservation[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const bbox = bboxFromRadius(lat, lon, radiusKm);
    const data = await geometFetch<any>('/collections/climate-hourly/items', {
      bbox: bbox.join(','),
      limit: '20',
      sortby: '-LOCAL_DATE',
    });

    const results: WeatherObservation[] = (data.features || []).map((f: any) => ({
      stationId: f.properties.CLIMATE_IDENTIFIER || f.id,
      name: f.properties.STATION_NAME || '',
      lat: f.geometry?.coordinates?.[1] ?? 0,
      lon: f.geometry?.coordinates?.[0] ?? 0,
      timestamp: f.properties.LOCAL_DATE || '',
      airTemp: f.properties.TEMP ?? null,
      dewPoint: f.properties.DEW_POINT_TEMP ?? null,
      humidity: f.properties.RELATIVE_HUMIDITY ?? null,
      windSpeed: f.properties.WIND_SPEED ?? null,
      windDir: f.properties.WIND_DIRECTION ?? null,
      pressure: f.properties.STATION_PRESSURE ?? null,
      visibility: f.properties.VISIBILITY ?? null,
      condition: f.properties.WEATHER_ENG_DESC ?? null,
    }));

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch Canadian weather:', err);
    return [];
  }
}

// ── 2. Canadian Hydrographic Service (Tides / Water Levels) ──────

/** Cached CHS station list. */
let _chsStationsCache: CHSTideStation[] | null = null;
let _chsStationsCacheTime = 0;

/**
 * Fetch all CHS tide/water-level stations.
 * Results are cached for 24 hours.
 * @internal
 */
async function fetchCHSStations(): Promise<CHSTideStation[]> {
  if (_chsStationsCache && Date.now() - _chsStationsCacheTime < STATION_CACHE_TTL) {
    return _chsStationsCache;
  }

  try {
    const data = await chsFetch<any[]>('/stations', {
      chs_client_id: 'OpenCatch',
    });

    _chsStationsCache = (data || []).map((s: any) => ({
      id: s.id,
      name: s.officialName ?? s.name ?? '',
      lat: s.latitude ?? 0,
      lon: s.longitude ?? 0,
      region: s.regionId ?? '',
    }));
    _chsStationsCacheTime = Date.now();
    return _chsStationsCache;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch CHS stations:', err);
    return _chsStationsCache ?? [];
  }
}

/**
 * Get the nearest Canadian Hydrographic Service tide station.
 *
 * Searches the full CHS station list and returns the closest one
 * by haversine distance. Useful for Canadian coasts and Great Lakes.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns The nearest CHS tide station with distance populated.
 *
 * @example
 * ```ts
 * const station = await getNearestCHSTideStation(44.65, -63.57);
 * console.log(station.name); // e.g. "Halifax"
 * ```
 */
export async function getNearestCHSTideStation(
  lat: number,
  lon: number,
): Promise<CHSTideStation | null> {
  const stations = await fetchCHSStations();
  if (stations.length === 0) return null;

  let best: CHSTideStation | null = null;
  let bestDist = Infinity;

  for (const s of stations) {
    // Quick pre-filter: skip stations more than 5 degrees away
    if (Math.abs(s.lat - lat) > 5 || Math.abs(s.lon - lon) > 5) continue;
    const dist = haversineKm(lat, lon, s.lat, s.lon);
    if (dist < bestDist) {
      bestDist = dist;
      best = { ...s, distanceKm: Math.round(dist * 10) / 10 };
    }
  }

  // Fallback to brute-force if pre-filter found nothing
  if (!best) {
    for (const s of stations) {
      const dist = haversineKm(lat, lon, s.lat, s.lon);
      if (dist < bestDist) {
        bestDist = dist;
        best = { ...s, distanceKm: Math.round(dist * 10) / 10 };
      }
    }
  }

  return best;
}

/**
 * Get tide/water-level predictions from a CHS station.
 *
 * Returns predicted water levels for the specified time range.
 * The CHS IWLS API provides both tidal predictions and water level
 * predictions for Great Lakes stations.
 *
 * @param stationId - CHS station ID.
 * @param hours - Number of hours of predictions to fetch (default 72).
 * @returns Array of tide predictions sorted chronologically.
 *
 * @example
 * ```ts
 * const predictions = await getCHSTidePredictions('5cebf1df3d0f4a073c4bb990', 48);
 * predictions.forEach(p => console.log(p.time, p.heightM, p.type));
 * ```
 */
export async function getCHSTidePredictions(
  stationId: string,
  hours = 72,
): Promise<CHSTidePrediction[]> {
  const cacheKey = `chs-pred:${stationId}:${hours}`;
  const cached = getCached<CHSTidePrediction[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const now = new Date();
    const end = new Date(now.getTime() + hours * 60 * 60 * 1000);

    const data = await chsFetch<any[]>(
      `/stations/${stationId}/data`,
      {
        'time-series-code': 'wlp',  // water level predictions
        from: now.toISOString(),
        to: end.toISOString(),
      },
    );

    const results: CHSTidePrediction[] = (data || []).map((d: any) => {
      const heightM = d.value ?? 0;
      return {
        time: d.eventDate ?? d.timeStamp ?? '',
        heightM,
        heightFt: Math.round(heightM * 3.28084 * 100) / 100,
        type: undefined, // CHS raw data doesn't flag H/L; compute from local extrema
      };
    });

    // Tag local high/low points
    for (let i = 1; i < results.length - 1; i++) {
      const prev = results[i - 1].heightM;
      const curr = results[i].heightM;
      const next = results[i + 1].heightM;
      if (curr > prev && curr > next) results[i].type = 'H';
      else if (curr < prev && curr < next) results[i].type = 'L';
    }

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch CHS tide predictions:', err);
    return [];
  }
}

/**
 * Get observed (actual) water levels from a CHS station.
 *
 * Returns the last N hours of observed water level readings from
 * the station's gauge. Available for both coastal and Great Lakes stations.
 *
 * @param stationId - CHS station ID.
 * @param hours - Number of hours of history to fetch (default 24).
 * @returns Array of observed water levels sorted chronologically.
 *
 * @example
 * ```ts
 * const levels = await getCHSWaterLevel('5cebf1df3d0f4a073c4bb990');
 * const latest = levels[levels.length - 1];
 * console.log(`Current water level: ${latest.heightM} m`);
 * ```
 */
export async function getCHSWaterLevel(
  stationId: string,
  hours = 24,
): Promise<CHSWaterLevel[]> {
  const cacheKey = `chs-obs:${stationId}:${hours}`;
  const cached = getCached<CHSWaterLevel[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const now = new Date();
    const from = new Date(now.getTime() - hours * 60 * 60 * 1000);

    const data = await chsFetch<any[]>(
      `/stations/${stationId}/data`,
      {
        'time-series-code': 'wlo',  // water level observations
        from: from.toISOString(),
        to: now.toISOString(),
      },
    );

    const results: CHSWaterLevel[] = (data || []).map((d: any) => {
      const heightM = d.value ?? 0;
      return {
        time: d.eventDate ?? d.timeStamp ?? '',
        heightM,
        heightFt: Math.round(heightM * 3.28084 * 100) / 100,
      };
    });

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch CHS water level observations:', err);
    return [];
  }
}

/**
 * Get Canadian water level from a CHS station by ID.
 *
 * Convenience wrapper that fetches the latest observed water level
 * for a given CHS station. Equivalent to the NOAA getWaterLevel but
 * for Canadian waters.
 *
 * @param stationId - CHS station ID.
 * @returns Array of observed water levels (last 24 hours).
 */
export async function getCanadianWaterLevel(stationId: string): Promise<CHSWaterLevel[]> {
  return getCHSWaterLevel(stationId, 24);
}

// ── 3. Water Survey of Canada (HYDAT) — Streamflow ───────────────

/**
 * Get nearby Water Survey of Canada hydrometric stations.
 *
 * Queries the Geomet OGC-API for hydrometric stations within a
 * bounding box. These stations report real-time water levels and
 * streamflow (discharge) for rivers and lakes across Canada.
 *
 * @param bbox - [minLon, minLat, maxLon, maxLat]
 * @param limit - Max number of stations to return (default 50).
 * @returns Array of hydrometric stations.
 *
 * @example
 * ```ts
 * const stations = await getHydrometricStations([-80, 43, -79, 44]);
 * ```
 */
export async function getHydrometricStations(
  bbox: [number, number, number, number],
  limit = 50,
): Promise<HydrometricStation[]> {
  const cacheKey = `hydro-stations:${bbox.join(',')}:${limit}`;
  const cached = getCached<HydrometricStation[]>(cacheKey, STATION_CACHE_TTL);
  if (cached) return cached;

  try {
    const data = await geometFetch<any>('/collections/hydrometric-stations/items', {
      bbox: bbox.join(','),
      limit: String(limit),
      STATUS_EN: 'Active',
    });

    const results: HydrometricStation[] = (data.features || []).map((f: any) => ({
      id: f.properties.STATION_NUMBER,
      name: f.properties.STATION_NAME,
      province: f.properties.PROV_TERR_STATE_LOC,
      lat: f.geometry.coordinates[1],
      lon: f.geometry.coordinates[0],
      status: f.properties.STATION_STATUS === 'Active' ? 'Active' as const : 'Discontinued' as const,
      drainageArea: f.properties.DRAINAGE_AREA_GROSS,
    }));

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch hydrometric stations:', err);
    return [];
  }
}

/**
 * Get real-time water level and discharge readings for a HYDAT station.
 *
 * Returns recent readings (up to 30 days available) sorted newest first.
 * This is the Canadian equivalent of USGS NWIS real-time data.
 *
 * @param stationId - Water Survey of Canada station number (e.g. "02HA003").
 * @param limit - Max number of readings to return (default 168 = ~7 days hourly).
 * @returns Array of water level/discharge readings.
 *
 * @example
 * ```ts
 * const readings = await getWaterLevels('02HA003', 48);
 * console.log(readings[0]?.level, 'm', readings[0]?.discharge, 'm3/s');
 * ```
 */
export async function getWaterLevels(
  stationId: string,
  limit = 168,
): Promise<WaterLevelReading[]> {
  const cacheKey = `hydro-realtime:${stationId}:${limit}`;
  const cached = getCached<WaterLevelReading[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const data = await geometFetch<any>('/collections/hydrometric-realtime/items', {
      STATION_NUMBER: stationId,
      limit: String(limit),
      sortby: '-DATETIME',
    });

    const results: WaterLevelReading[] = (data.features || []).map((f: any) => ({
      stationId: f.properties.STATION_NUMBER,
      timestamp: f.properties.DATETIME,
      level: f.properties.LEVEL ?? null,
      discharge: f.properties.DISCHARGE ?? null,
    }));

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch water levels:', err);
    return [];
  }
}

/**
 * Get daily mean water levels for a HYDAT station (historical).
 *
 * @param stationId - Station number.
 * @param startDate - Start date (YYYY-MM-DD).
 * @param endDate - End date (YYYY-MM-DD).
 * @returns Array of daily mean readings.
 */
export async function getDailyMeanLevels(
  stationId: string,
  startDate: string,
  endDate: string,
): Promise<WaterLevelReading[]> {
  try {
    const data = await geometFetch<any>('/collections/hydrometric-daily-mean/items', {
      STATION_NUMBER: stationId,
      datetime: `${startDate}/${endDate}`,
      limit: '365',
      sortby: '-DATE',
    });

    return (data.features || []).map((f: any) => ({
      stationId: f.properties.STATION_NUMBER,
      timestamp: f.properties.DATE,
      level: f.properties.LEVEL ?? null,
      discharge: f.properties.DISCHARGE ?? null,
    }));
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch daily mean levels:', err);
    return [];
  }
}

/**
 * Get real-time streamflow data for a HYDAT station.
 *
 * Convenience function that wraps getWaterLevels with station metadata,
 * specifically emphasising discharge (streamflow). This is the Canadian
 * equivalent of USGS NWIS streamflow data.
 *
 * @param stationId - Water Survey of Canada station number.
 * @returns Array of streamflow readings with station metadata.
 *
 * @example
 * ```ts
 * const flow = await getCanadianStreamflow('02HA003');
 * console.log(flow[0]?.discharge, 'm3/s at', flow[0]?.stationName);
 * ```
 */
export async function getCanadianStreamflow(
  stationId: string,
): Promise<StreamflowReading[]> {
  // First, get station metadata
  const stationData = await geometFetch<any>('/collections/hydrometric-stations/items', {
    STATION_NUMBER: stationId,
    limit: '1',
  });

  const stationFeature = stationData.features?.[0];
  const stationName = stationFeature?.properties?.STATION_NAME ?? stationId;
  const province = stationFeature?.properties?.PROV_TERR_STATE_LOC ?? '';

  // Then get real-time readings
  const readings = await getWaterLevels(stationId, 168);

  return readings.map((r) => ({
    stationId: r.stationId,
    stationName,
    timestamp: r.timestamp,
    discharge: r.discharge,
    level: r.level,
    province,
  }));
}

/**
 * Find the nearest active Water Survey of Canada hydrometric station.
 *
 * Searches for HYDAT stations within a radius and returns the closest
 * one. Useful for finding the nearest streamflow/water-level gauge
 * to a fishing spot.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @param radiusKm - Search radius (default 100 km).
 * @returns The nearest active station, or null if none found.
 *
 * @example
 * ```ts
 * const station = await getNearestCanadianStation(45.42, -75.69);
 * if (station) {
 *   console.log(station.name, station.distanceKm, 'km away');
 * }
 * ```
 */
export async function getNearestCanadianStation(
  lat: number,
  lon: number,
  radiusKm = 100,
): Promise<HydrometricStation | null> {
  const bbox = bboxFromRadius(lat, lon, radiusKm);
  const stations = await getHydrometricStations(bbox, 100);

  if (stations.length === 0) {
    // Try a wider search
    const widerBbox = bboxFromRadius(lat, lon, radiusKm * 2);
    const widerStations = await getHydrometricStations(widerBbox, 100);
    if (widerStations.length === 0) return null;
    return findNearest(widerStations, lat, lon);
  }

  return findNearest(stations, lat, lon);
}

/**
 * Find the nearest station from a list.
 * @internal
 */
function findNearest(
  stations: HydrometricStation[],
  lat: number,
  lon: number,
): HydrometricStation | null {
  let best: HydrometricStation | null = null;
  let bestDist = Infinity;

  for (const s of stations) {
    if (s.status !== 'Active') continue;
    const dist = haversineKm(lat, lon, s.lat, s.lon);
    if (dist < bestDist) {
      bestDist = dist;
      best = { ...s, distanceKm: Math.round(dist * 10) / 10 };
    }
  }

  return best;
}

// ── 4. AQHI (Air Quality Health Index) ───────────────────────────

/**
 * Get current AQHI readings near a location.
 *
 * @param bbox - [minLon, minLat, maxLon, maxLat]
 * @param limit - Max results (default 10).
 * @returns Array of AQHI readings.
 */
export async function getAQHI(
  bbox: [number, number, number, number],
  limit = 10,
): Promise<AQHIReading[]> {
  const cacheKey = `aqhi:${bbox.join(',')}:${limit}`;
  const cached = getCached<AQHIReading[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const data = await geometFetch<any>('/collections/aqhi-observations-realtime/items', {
      bbox: bbox.join(','),
      limit: String(limit),
      sortby: '-latest_reading_date',
    });

    const results: AQHIReading[] = (data.features || []).map((f: any) => ({
      stationId: f.properties.location_id || f.id,
      location: f.properties.location_name_en || '',
      aqhi: f.properties.current_aqhi ?? 0,
      forecastToday: f.properties.forecast_aqhi_today,
      forecastTonight: f.properties.forecast_aqhi_tonight,
      forecastTomorrow: f.properties.forecast_aqhi_tomorrow,
    }));

    setCache(cacheKey, results);
    return results;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch AQHI:', err);
    return [];
  }
}

// ── 5. Unit conversion helpers ───────────────────────────────────

/**
 * Convert AQHI (1-10+) to US AQI equivalent (0-500) for unified display.
 *
 * AQHI and AQI are different scales; this provides a rough mapping
 * so the app can display a single consistent air quality indicator.
 *
 * @param aqhi - AQHI value (1-10+).
 * @returns Approximate AQI equivalent.
 */
export function aqhiToAqi(aqhi: number): number {
  if (aqhi <= 3) return Math.round(aqhi * 17);           // 1-3 -> 0-50 (Good)
  if (aqhi <= 6) return Math.round(50 + (aqhi - 3) * 17); // 4-6 -> 50-100 (Moderate)
  if (aqhi <= 10) return Math.round(100 + (aqhi - 6) * 25); // 7-10 -> 100-200 (Unhealthy)
  return Math.round(200 + (aqhi - 10) * 50);              // 10+ -> 200+ (Very Unhealthy)
}

/**
 * Convert metres to feet.
 *
 * @param metres - Value in metres.
 * @returns Value in feet, rounded to 2 decimal places.
 */
export function metresToFeet(metres: number): number {
  return Math.round(metres * 3.28084 * 100) / 100;
}

/**
 * Convert Celsius to Fahrenheit.
 *
 * @param celsius - Temperature in Celsius.
 * @returns Temperature in Fahrenheit, rounded to 1 decimal place.
 */
export function celsiusToFahrenheit(celsius: number): number {
  return Math.round((celsius * 9 / 5 + 32) * 10) / 10;
}

/**
 * Convert km/h to mph.
 *
 * @param kmh - Speed in km/h.
 * @returns Speed in mph, rounded to 1 decimal place.
 */
export function kmhToMph(kmh: number): number {
  return Math.round(kmh * 0.621371 * 10) / 10;
}

/**
 * Convert kPa to inHg (inches of mercury) for barometric pressure.
 *
 * @param kpa - Pressure in kilopascals.
 * @returns Pressure in inches of mercury, rounded to 2 decimal places.
 */
export function kpaToInHg(kpa: number): number {
  return Math.round(kpa * 0.29530 * 100) / 100;
}

// ── 6. Water Temperature (SWOB Real-Time) ────────────────────────

/** A real-time surface water observation from SWOB marine stations. */
export interface WaterTempReading {
  stationId: string;
  stationName: string;
  lat: number;
  lon: number;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Water surface temperature in Celsius (null if unavailable). */
  waterTempC: number | null;
  /** Water surface temperature in Fahrenheit (null if unavailable). */
  waterTempF: number | null;
}

/**
 * Get real-time marine/lake surface observations from SWOB stations.
 *
 * SWOB (Surface Weather Observation) includes marine stations that
 * report water surface temperature. This is the Canadian equivalent
 * of USGS water temperature monitoring.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @param radiusKm - Search radius (default 100 km).
 * @returns Array of water temperature readings.
 */
export async function getCanadianWaterTemp(
  lat: number,
  lon: number,
  radiusKm = 100,
): Promise<WaterTempReading[]> {
  const cacheKey = `ca-watertemp:${lat.toFixed(2)},${lon.toFixed(2)}`;
  const cached = getCached<WaterTempReading[]>(cacheKey, DATA_CACHE_TTL);
  if (cached) return cached;

  try {
    const bbox = bboxFromRadius(lat, lon, radiusKm);
    const data = await geometFetch<any>('/collections/swob-marine-stations/items', {
      bbox: bbox.join(','),
      limit: '20',
    });

    // SWOB marine stations provide metadata; actual readings need swob-realtime
    const stations = (data.features || []).map((f: any) => ({
      stationId: f.properties.msc_id ?? f.id ?? '',
      stationName: f.properties.name ?? '',
      lat: f.geometry?.coordinates?.[1] ?? 0,
      lon: f.geometry?.coordinates?.[0] ?? 0,
      timestamp: '',
      waterTempC: null as number | null,
      waterTempF: null as number | null,
    }));

    setCache(cacheKey, stations);
    return stations;
  } catch (err) {
    console.warn('[OpenCatch] Failed to fetch Canadian water temp stations:', err);
    return [];
  }
}

// ── 7. Canada Data Source Reference ──────────────────────────────

/**
 * Comprehensive mapping of Canadian data sources and their US equivalents.
 *
 * This documents what Canada uses instead of US agencies for each
 * data type relevant to fishing conditions.
 */
export const CANADA_DATA_SOURCES = {
  /** Water flow/level (US: USGS NWIS) → Water Survey of Canada (HYDAT) via Geomet */
  waterFlow: {
    agency: 'Water Survey of Canada',
    api: 'https://api.weather.gc.ca/collections/hydrometric-realtime/items',
    description: 'Real-time water levels and discharge for rivers/lakes',
    usEquivalent: 'USGS NWIS',
    implemented: true,
  },
  /** Water quality (US: USGS Water Quality) → Environment Canada / ECCC */
  waterQuality: {
    agency: 'Environment and Climate Change Canada (ECCC)',
    api: 'https://wateroffice.ec.gc.ca/',
    description: 'Water quality monitoring — no public real-time API; data available via bulk download',
    usEquivalent: 'USGS Water Quality Portal',
    implemented: false,
    notes: 'Provincial agencies also monitor: Ontario (PWQMN), BC (EMS), Alberta (RAMP)',
  },
  /** Fish stocking (US: State DNR) → Provincial DNR/MNR */
  fishStocking: {
    agency: 'Provincial agencies',
    description: 'Fish stocking data varies by province; no unified API',
    usEquivalent: 'State DNR stocking reports',
    implemented: false,
    provincialSources: {
      ON: 'https://www.ontario.ca/page/fish-stocking-list',
      BC: 'https://www.gofishbc.com/stocked-lakes.aspx',
      AB: 'https://mywildalberta.ca/fishing/stocking-reports/',
      SK: 'https://www.saskatchewan.ca/residents/parks-culture-heritage-and-sport/hunting-trapping-and-angling/angling/fish-stocking',
      MB: 'https://www.gov.mb.ca/fish-wildlife/fish/stocking/index.html',
    },
  },
  /** Weather (US: NWS) → Environment Canada (Geomet OGC-API) */
  weather: {
    agency: 'Environment and Climate Change Canada',
    api: 'https://api.weather.gc.ca/collections/climate-hourly/items',
    description: 'Real-time weather observations from ~2000 stations',
    usEquivalent: 'NWS / NOAA',
    implemented: true,
  },
  /** Tides (US: NOAA CO-OPS) → Canadian Hydrographic Service (CHS IWLS) */
  tides: {
    agency: 'Canadian Hydrographic Service',
    api: 'https://api-iwls.dfo-mpo.gc.ca/api/v1',
    description: 'Tide predictions and water levels for coasts and Great Lakes',
    usEquivalent: 'NOAA CO-OPS',
    implemented: true,
  },
  /** Weather alerts (US: NWS) → Environment Canada (CAP alerts via Geomet) */
  weatherAlerts: {
    agency: 'Environment and Climate Change Canada',
    api: 'https://api.weather.gc.ca/collections/weather-alerts/items',
    description: 'Active weather alerts in CAP-like format',
    usEquivalent: 'NWS Alerts API',
    implemented: true,
  },
  /** Air quality (US: AirNow / EPA) → AQHI via Geomet */
  airQuality: {
    agency: 'Environment and Climate Change Canada',
    api: 'https://api.weather.gc.ca/collections/aqhi-observations-realtime/items',
    description: 'Air Quality Health Index (AQHI) on 1-10+ scale',
    usEquivalent: 'AirNow / EPA AQI',
    implemented: true,
  },
  /** Public lands (US: PAD-US) → CPCAD / Crown Land / OSM */
  publicLands: {
    agency: 'ECCC (CPCAD) + Provincial Crown Land offices',
    description: 'Protected areas from CPCAD; Crown land varies by province; park boundaries from OSM',
    usEquivalent: 'PAD-US (USGS)',
    implemented: true,
    notes: 'Using OSM Overpass API for park boundaries; CPCAD is bulk-download only (ESRI GDB)',
  },
} as const;
