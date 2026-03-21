/**
 * NOAA CO-OPS Tides & Currents Service
 *
 * Integrates with the NOAA Center for Operational Oceanographic Products
 * and Services (CO-OPS) API for tide predictions, water levels, and
 * current data. Free, no API key required.
 *
 * API docs: https://api.tidesandcurrents.noaa.gov/api/prod/
 * Station metadata: https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/
 */

// ── Types ────────────────────────────────────────────────────────

/** A NOAA CO-OPS tide prediction station. */
export interface TideStation {
  /** NOAA station ID (e.g. "8518750") */
  id: string;
  /** Human-readable station name (e.g. "The Battery, NY") */
  name: string;
  lat: number;
  lon: number;
  /** Distance from the queried coordinates, in kilometers. */
  distanceKm: number;
  /** Station state abbreviation, if available (e.g. "NY"). */
  state?: string;
}

/** A single high/low tide prediction event. */
export interface TidePrediction {
  /** ISO-8601 datetime string in local station time. */
  time: string;
  /** Water height relative to MLLW datum, in feet. */
  heightFt: number;
  /** Whether this is a high or low tide. */
  type: 'H' | 'L';
  /** Human-readable label: "High" or "Low". */
  label: string;
}

/** An hourly tide level reading for chart display. */
export interface TideHourly {
  /** ISO-8601 datetime string in local station time. */
  time: string;
  /** Water height relative to MLLW datum, in feet. */
  heightFt: number;
}

/** A current prediction data point. */
export interface CurrentPrediction {
  /** ISO-8601 datetime string in local station time. */
  time: string;
  /** Current speed in knots. */
  speedKnots: number;
  /** Current direction in compass degrees (0-360). */
  directionDeg: number;
  /** Human-readable event type (e.g. "max_flood", "slack", "max_ebb"). */
  type?: string;
}

/** An observed water level measurement. */
export interface WaterLevelReading {
  /** ISO-8601 datetime string in local station time. */
  time: string;
  /** Observed water level relative to MLLW datum, in feet. */
  heightFt: number;
  /** NOAA quality flag (e.g. "v" = verified, "p" = preliminary). */
  quality: string;
}

/** Return type for {@link getNextTide}. */
export interface NextTideInfo {
  /** The next upcoming tide event. */
  prediction: TidePrediction;
  /** Minutes remaining until the tide event. */
  minutesUntil: number;
  /** Human-readable time remaining (e.g. "2h 34m"). */
  timeRemaining: string;
}

// ── Internal NOAA response shapes ────────────────────────────────

interface NoaaStationEntry {
  id: string;
  name: string;
  lat: number;
  lng: number;
  state?: string;
}

interface NoaaStationsResponse {
  stationList: NoaaStationEntry[];
}

interface NoaaPredictionEntry {
  t: string; // "2026-03-20 06:12"
  v: string; // "4.321"
  type?: string; // "H" or "L" (only in hilo)
}

interface NoaaPredictionsResponse {
  predictions: NoaaPredictionEntry[];
}

interface NoaaCurrentEntry {
  Time: string;
  Speed_kts: string;
  Direction_deg?: string;
  Type?: string;
}

interface NoaaCurrentsResponse {
  current_predictions: {
    cp: NoaaCurrentEntry[];
  };
}

interface NoaaWaterLevelEntry {
  t: string;
  v: string;
  q: string;
  f: string;
}

interface NoaaWaterLevelResponse {
  data: NoaaWaterLevelEntry[];
}

interface NoaaErrorResponse {
  error: {
    message: string;
  };
}

// ── Config ───────────────────────────────────────────────────────

const DATA_BASE_URL =
  'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter';
const STATIONS_BASE_URL =
  'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json';

const REQUEST_TIMEOUT_MS = 15_000;

/** Maximum number of stations to evaluate when finding the nearest one. */
const MAX_STATION_CANDIDATES = 500;

// ── Helpers ──────────────────────────────────────────────────────

/**
 * Haversine distance between two points on Earth.
 * @returns Distance in kilometers.
 */
function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371; // Earth radius in km
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
 * Format a date as NOAA expects: "YYYYMMDD" or "YYYYMMDD HH:mm".
 */
function formatNoaaDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}${m}${d}`;
}

/**
 * Convert NOAA local time string "YYYY-MM-DD HH:mm" to ISO-8601 format.
 * NOAA returns times in the station's local time zone without offset,
 * so we preserve the string as-is with a 'T' separator.
 */
function noaaTimeToISO(noaaTime: string): string {
  return noaaTime.replace(' ', 'T');
}

/**
 * Fetch JSON from a URL with timeout and error handling.
 * @throws {TidesServiceError} on network, timeout, or NOAA API errors.
 */
async function fetchNoaa<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);

    if (!res.ok) {
      throw new TidesServiceError(
        `NOAA API returned HTTP ${res.status}: ${url}`,
        res.status,
      );
    }

    const json = await res.json();

    // NOAA returns 200 with an error object for invalid requests
    if (json && typeof json === 'object' && 'error' in json) {
      const errMsg = (json as NoaaErrorResponse).error?.message ?? 'Unknown NOAA error';
      throw new TidesServiceError(`NOAA API error: ${errMsg}`, 400);
    }

    return json as T;
  } catch (err: any) {
    clearTimeout(timeoutId);

    if (err instanceof TidesServiceError) throw err;

    if (err.name === 'AbortError') {
      throw new TidesServiceError(
        `NOAA API request timed out after ${REQUEST_TIMEOUT_MS}ms`,
        408,
      );
    }

    throw new TidesServiceError(
      `NOAA API network error: ${err.message ?? 'unknown'}`,
      0,
    );
  }
}

// ── Error class ──────────────────────────────────────────────────

export class TidesServiceError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'TidesServiceError';
    this.status = status;
  }
}

// ── Station cache ────────────────────────────────────────────────

let _stationsCache: NoaaStationEntry[] | null = null;
let _stationsCacheTime = 0;
const STATIONS_CACHE_TTL = 24 * 60 * 60 * 1000; // 24 hours

/**
 * Fetch the full list of NOAA tide prediction stations.
 * Results are cached for 24 hours since the station list rarely changes.
 */
async function fetchStationList(): Promise<NoaaStationEntry[]> {
  if (_stationsCache && Date.now() - _stationsCacheTime < STATIONS_CACHE_TTL) {
    return _stationsCache;
  }

  const url = `${STATIONS_BASE_URL}?type=tidepredictions`;
  const data = await fetchNoaa<NoaaStationsResponse>(url);

  if (!data.stationList || data.stationList.length === 0) {
    throw new TidesServiceError('NOAA returned no tide stations', 404);
  }

  _stationsCache = data.stationList;
  _stationsCacheTime = Date.now();
  return _stationsCache;
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Find the nearest NOAA tide prediction station to given coordinates.
 *
 * Fetches the full station list from NOAA's metadata API and computes
 * haversine distance to each station. The station list is cached for
 * 24 hours.
 *
 * @param lat - Latitude in decimal degrees.
 * @param lon - Longitude in decimal degrees.
 * @returns The closest {@link TideStation} with distance populated.
 * @throws {TidesServiceError} if the station list cannot be fetched.
 *
 * @example
 * ```ts
 * const station = await tidesService.getNearestTideStation(28.6, -80.6);
 * console.log(station.name, station.distanceKm);
 * ```
 */
export async function getNearestTideStation(
  lat: number,
  lon: number,
): Promise<TideStation> {
  const stations = await fetchStationList();

  let bestStation: NoaaStationEntry | null = null;
  let bestDist = Infinity;

  // Evaluate all stations — the list is ~3K entries, fast enough in JS
  const limit = Math.min(stations.length, MAX_STATION_CANDIDATES);
  for (let i = 0; i < stations.length; i++) {
    const s = stations[i];
    // Quick bounding-box pre-filter: skip stations more than ~5 degrees away
    if (Math.abs(s.lat - lat) > 5 || Math.abs(s.lng - lon) > 5) continue;

    const dist = haversineKm(lat, lon, s.lat, s.lng);
    if (dist < bestDist) {
      bestDist = dist;
      bestStation = s;
    }
  }

  // If bounding-box found nothing, fall back to full brute-force search
  if (!bestStation) {
    for (const s of stations) {
      const dist = haversineKm(lat, lon, s.lat, s.lng);
      if (dist < bestDist) {
        bestDist = dist;
        bestStation = s;
      }
    }
  }

  if (!bestStation) {
    throw new TidesServiceError(
      'No NOAA tide stations found',
      404,
    );
  }

  return {
    id: bestStation.id,
    name: bestStation.name,
    lat: bestStation.lat,
    lon: bestStation.lng,
    distanceKm: Math.round(bestDist * 10) / 10,
    state: bestStation.state,
  };
}

/**
 * Get tide predictions (high/low events) for a station.
 *
 * Returns the predicted high and low tide times and heights for the
 * requested number of days, starting from today.
 *
 * @param stationId - NOAA station ID (e.g. "8518750").
 * @param days - Number of days to retrieve (1-10). Defaults to 3.
 * @returns Array of {@link TidePrediction} sorted chronologically.
 * @throws {TidesServiceError} on API errors.
 *
 * @example
 * ```ts
 * const predictions = await tidesService.getTidePredictions('8518750', 3);
 * predictions.forEach(p => console.log(p.label, p.time, p.heightFt));
 * ```
 */
export async function getTidePredictions(
  stationId: string,
  days: number = 3,
): Promise<TidePrediction[]> {
  const clampedDays = Math.max(1, Math.min(days, 10));
  const beginDate = new Date();
  const endDate = new Date();
  endDate.setDate(endDate.getDate() + clampedDays);

  const params = new URLSearchParams({
    product: 'predictions',
    station: stationId,
    begin_date: formatNoaaDate(beginDate),
    end_date: formatNoaaDate(endDate),
    datum: 'MLLW',
    time_zone: 'lst_ldt',
    units: 'english',
    interval: 'hilo',
    format: 'json',
    application: 'OpenCatch',
  });

  const data = await fetchNoaa<NoaaPredictionsResponse>(
    `${DATA_BASE_URL}?${params.toString()}`,
  );

  if (!data.predictions || data.predictions.length === 0) {
    return [];
  }

  return data.predictions.map((p) => ({
    time: noaaTimeToISO(p.t),
    heightFt: parseFloat(p.v),
    type: (p.type ?? 'H') as 'H' | 'L',
    label: p.type === 'L' ? 'Low' : 'High',
  }));
}

/**
 * Get hourly tide level predictions for chart display.
 *
 * Returns one data point per hour for the next 24 hours, suitable for
 * rendering a smooth tide curve in the UI.
 *
 * @param stationId - NOAA station ID.
 * @returns Array of {@link TideHourly} sorted chronologically.
 * @throws {TidesServiceError} on API errors.
 *
 * @example
 * ```ts
 * const hourly = await tidesService.getTideHourly('8518750');
 * // Use hourly[].time / hourly[].heightFt for charting
 * ```
 */
export async function getTideHourly(
  stationId: string,
): Promise<TideHourly[]> {
  const beginDate = new Date();
  const endDate = new Date();
  endDate.setDate(endDate.getDate() + 1);

  const params = new URLSearchParams({
    product: 'predictions',
    station: stationId,
    begin_date: formatNoaaDate(beginDate),
    end_date: formatNoaaDate(endDate),
    datum: 'MLLW',
    time_zone: 'lst_ldt',
    units: 'english',
    interval: 'h',
    format: 'json',
    application: 'OpenCatch',
  });

  const data = await fetchNoaa<NoaaPredictionsResponse>(
    `${DATA_BASE_URL}?${params.toString()}`,
  );

  if (!data.predictions || data.predictions.length === 0) {
    return [];
  }

  return data.predictions.map((p) => ({
    time: noaaTimeToISO(p.t),
    heightFt: parseFloat(p.v),
  }));
}

/**
 * Get current predictions for a station.
 *
 * Retrieves predicted tidal current speed and direction for the next
 * 24 hours. Note: current prediction stations are a different set from
 * tide prediction stations. Use a current station ID (typically prefixed
 * with a letter, e.g. "ACT4996").
 *
 * @param stationId - NOAA current station ID.
 * @returns Array of {@link CurrentPrediction} sorted chronologically.
 * @throws {TidesServiceError} on API errors.
 *
 * @example
 * ```ts
 * const currents = await tidesService.getCurrents('ACT4996');
 * currents.forEach(c => console.log(c.speedKnots, c.directionDeg));
 * ```
 */
export async function getCurrents(
  stationId: string,
): Promise<CurrentPrediction[]> {
  const beginDate = new Date();
  const endDate = new Date();
  endDate.setDate(endDate.getDate() + 1);

  const params = new URLSearchParams({
    product: 'currents_predictions',
    station: stationId,
    begin_date: formatNoaaDate(beginDate),
    end_date: formatNoaaDate(endDate),
    time_zone: 'lst_ldt',
    units: 'english',
    format: 'json',
    application: 'OpenCatch',
  });

  const data = await fetchNoaa<NoaaCurrentsResponse>(
    `${DATA_BASE_URL}?${params.toString()}`,
  );

  const entries = data.current_predictions?.cp;
  if (!entries || entries.length === 0) {
    return [];
  }

  return entries.map((c) => ({
    time: noaaTimeToISO(c.Time),
    speedKnots: parseFloat(c.Speed_kts) || 0,
    directionDeg: parseFloat(c.Direction_deg ?? '0') || 0,
    type: c.Type,
  }));
}

/**
 * Get observed (actual) water level readings for a station.
 *
 * Returns the last 24 hours of 6-minute interval observed water levels.
 * Unlike predictions, this is real measured data from the tide gauge.
 *
 * @param stationId - NOAA station ID.
 * @returns Array of {@link WaterLevelReading} sorted chronologically.
 * @throws {TidesServiceError} on API errors.
 *
 * @example
 * ```ts
 * const levels = await tidesService.getWaterLevel('8518750');
 * const latest = levels[levels.length - 1];
 * console.log(`Current water level: ${latest.heightFt} ft`);
 * ```
 */
export async function getWaterLevel(
  stationId: string,
): Promise<WaterLevelReading[]> {
  const params = new URLSearchParams({
    product: 'water_level',
    station: stationId,
    range: '24',
    datum: 'MLLW',
    time_zone: 'lst_ldt',
    units: 'english',
    format: 'json',
    application: 'OpenCatch',
  });

  const data = await fetchNoaa<NoaaWaterLevelResponse>(
    `${DATA_BASE_URL}?${params.toString()}`,
  );

  if (!data.data || data.data.length === 0) {
    return [];
  }

  return data.data.map((d) => ({
    time: noaaTimeToISO(d.t),
    heightFt: parseFloat(d.v),
    quality: d.q,
  }));
}

/**
 * Find the next upcoming tide event from a list of predictions.
 *
 * Scans the predictions array to find the first event that has not yet
 * occurred, and computes the time remaining. Useful for displaying
 * "Next high tide in 2h 34m" in the UI.
 *
 * @param predictions - Array of {@link TidePrediction} (from {@link getTidePredictions}).
 * @param now - Optional reference time. Defaults to current time.
 * @returns The next tide event with time remaining, or `null` if all
 *          predictions are in the past.
 *
 * @example
 * ```ts
 * const predictions = await tidesService.getTidePredictions('8518750', 2);
 * const next = tidesService.getNextTide(predictions);
 * if (next) {
 *   console.log(`Next ${next.prediction.label} tide in ${next.timeRemaining}`);
 * }
 * ```
 */
export function getNextTide(
  predictions: TidePrediction[],
  now?: Date,
): NextTideInfo | null {
  const refTime = (now ?? new Date()).getTime();

  for (const prediction of predictions) {
    // Parse the local time string — NOAA returns times without TZ offset,
    // so we treat them as local times.
    const predTime = new Date(prediction.time).getTime();
    if (predTime > refTime) {
      const diffMs = predTime - refTime;
      const totalMinutes = Math.round(diffMs / 60_000);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;

      let timeRemaining: string;
      if (hours === 0) {
        timeRemaining = `${minutes}m`;
      } else {
        timeRemaining = `${hours}h ${minutes}m`;
      }

      return {
        prediction,
        minutesUntil: totalMinutes,
        timeRemaining,
      };
    }
  }

  return null;
}

// ── Bundled service object ───────────────────────────────────────

/**
 * NOAA CO-OPS tides and currents service.
 *
 * All methods are available both as named exports and as properties
 * on this default service object.
 *
 * @example
 * ```ts
 * import { tidesService } from '../services/tidesService';
 *
 * const station = await tidesService.getNearestTideStation(28.6, -80.6);
 * const tides = await tidesService.getTidePredictions(station.id, 3);
 * const next = tidesService.getNextTide(tides);
 * ```
 */
export const tidesService = {
  getNearestTideStation,
  getTidePredictions,
  getTideHourly,
  getCurrents,
  getWaterLevel,
  getNextTide,
} as const;
