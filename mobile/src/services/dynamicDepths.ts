/**
 * OpenCatch -- Dynamic Depths Service
 *
 * Connects to 5,700+ NOAA CO-OPS water level stations and dynamically
 * adjusts charted depths based on real-time and forecasted tidal conditions.
 *
 * Inspired by Wavve Boating's "Dynamic Depths" (2025 Top Product),
 * this service provides:
 *   - Real-time water level from the nearest NOAA CO-OPS station
 *   - Forecasted water levels (predictions) for planned departures
 *   - Batch adjustment of charted depths over a bounding box
 *   - Safety color-coding relative to boat draft
 *
 * Data refreshes every 6 minutes (aligned with NOAA's update cadence).
 *
 * NOAA CO-OPS API docs: https://api.tidesandcurrents.noaa.gov/api/prod/
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface WaterLevelStation {
  /** NOAA CO-OPS station ID (e.g. "8518750" for NYC Battery) */
  id: string;
  /** Human-readable station name */
  name: string;
  /** Latitude of the station */
  lat: number;
  /** Longitude of the station */
  lon: number;
  /** Distance from query point in km */
  distanceKm: number;
  /** State abbreviation (e.g. "NY") */
  state?: string;
}

export interface WaterLevel {
  /** Station ID this reading came from */
  stationId: string;
  /** ISO-8601 timestamp of the reading (GMT) */
  timestamp: string;
  /** Water level in meters relative to MLLW datum */
  levelM: number;
  /** Quality flag: "p" preliminary, "v" verified */
  quality: string;
}

export interface DynamicDepth {
  /** Original charted depth in meters (relative to MLLW) */
  chartedDepthM: number;
  /** Current water level adjustment in meters */
  adjustmentM: number;
  /** Adjusted real-time depth = chartedDepth + waterLevel */
  adjustedDepthM: number;
  /** Latitude of this depth point */
  lat: number;
  /** Longitude of this depth point */
  lon: number;
  /** Safety level relative to boat draft */
  safety: DepthSafetyLevel;
  /** Station used for the adjustment */
  stationId: string;
  /** Timestamp of the water level reading */
  timestamp: string;
}

export type DepthSafetyLevel = 'danger' | 'caution' | 'tight' | 'safe';

export interface ForecastedDepth {
  /** ISO-8601 timestamp of the prediction */
  timestamp: string;
  /** Predicted water level in meters relative to MLLW */
  predictedLevelM: number;
  /** Adjusted depth at this predicted time */
  adjustedDepthM: number;
  /** Safety level at this predicted time */
  safety: DepthSafetyLevel;
}

export interface BBox {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

export interface ChartedDepthPoint {
  lat: number;
  lon: number;
  depthM: number;
}

// ── Constants ────────────────────────────────────────────────────────────────

const COOPS_BASE = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter';
const COOPS_STATIONS_URL = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels';

/** NOAA updates every 6 minutes */
export const REFRESH_INTERVAL_MS = 6 * 60 * 1000;

/** Cache water level readings for 5 minutes */
const WATER_LEVEL_CACHE_TTL_MS = 5 * 60 * 1000;

/** Maximum distance (km) to consider a station relevant */
const MAX_STATION_DISTANCE_KM = 100;

/** Safety buffer above draft before "tight" (meters) */
const TIGHT_BUFFER_M = 1.0;

/** Safety buffer above draft before "caution" (meters) */
const CAUTION_BUFFER_M = 0.3;

// ── Caches ───────────────────────────────────────────────────────────────────

interface StationCache {
  stations: WaterLevelStation[];
  fetchedAt: number;
}

interface WaterLevelCache {
  level: WaterLevel;
  fetchedAt: number;
}

let _stationCache: StationCache | null = null;
const _waterLevelCache = new Map<string, WaterLevelCache>();

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function padDate(d: Date): string {
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mi = String(d.getUTCMinutes()).padStart(2, '0');
  return `${yyyy}${mm}${dd} ${hh}:${mi}`;
}

async function fetchJSON<T>(url: string, timeoutMs: number = 8000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ── Station Discovery ────────────────────────────────────────────────────────

interface COOPSStationList {
  stations: Array<{
    id: string;
    name: string;
    lat: number;
    lng: number;
    state?: string;
  }>;
}

/**
 * Fetch all NOAA CO-OPS water level stations.
 * Cached for 24 hours (station list rarely changes).
 */
async function fetchAllStations(): Promise<WaterLevelStation[]> {
  const CACHE_TTL = 24 * 60 * 60 * 1000;
  if (_stationCache && Date.now() - _stationCache.fetchedAt < CACHE_TTL) {
    return _stationCache.stations;
  }

  const data = await fetchJSON<COOPSStationList>(COOPS_STATIONS_URL, 15000);

  const stations: WaterLevelStation[] = (data.stations ?? []).map((s) => ({
    id: s.id,
    name: s.name,
    lat: s.lat,
    lon: s.lng,
    distanceKm: 0,
    state: s.state,
  }));

  _stationCache = { stations, fetchedAt: Date.now() };
  return stations;
}

/**
 * Find the nearest NOAA CO-OPS water level station to a given coordinate.
 */
export async function getNearestWaterLevelStation(
  lat: number,
  lon: number,
): Promise<WaterLevelStation | null> {
  try {
    const stations = await fetchAllStations();
    let nearest: WaterLevelStation | null = null;
    let minDist = Infinity;

    for (const station of stations) {
      const dist = haversineKm(lat, lon, station.lat, station.lon);
      if (dist < minDist) {
        minDist = dist;
        nearest = { ...station, distanceKm: Math.round(dist * 10) / 10 };
      }
    }

    if (nearest && nearest.distanceKm > MAX_STATION_DISTANCE_KM) {
      return null; // Too far from any station -- likely inland
    }

    return nearest;
  } catch {
    return null;
  }
}

// ── Real-Time Water Level ────────────────────────────────────────────────────

interface COOPSWaterLevelResponse {
  data?: Array<{
    t: string;   // timestamp: "YYYY-MM-DD HH:MM"
    v: string;   // water level value in meters
    q: string;   // quality flag
  }>;
  error?: { message: string };
}

/**
 * Get the current (latest) water level from a NOAA CO-OPS station.
 * Returns the water level in meters relative to MLLW datum.
 */
export async function getCurrentWaterLevel(stationId: string): Promise<WaterLevel | null> {
  // Check cache
  const cached = _waterLevelCache.get(stationId);
  if (cached && Date.now() - cached.fetchedAt < WATER_LEVEL_CACHE_TTL_MS) {
    return cached.level;
  }

  try {
    const url =
      `${COOPS_BASE}?product=water_level&station=${stationId}` +
      `&date=latest&datum=MLLW&units=metric&time_zone=gmt&format=json`;

    const data = await fetchJSON<COOPSWaterLevelResponse>(url);

    if (!data.data || data.data.length === 0) return null;

    const reading = data.data[0];
    const level: WaterLevel = {
      stationId,
      timestamp: reading.t,
      levelM: parseFloat(reading.v),
      quality: reading.q,
    };

    _waterLevelCache.set(stationId, { level, fetchedAt: Date.now() });
    return level;
  } catch {
    return null;
  }
}

// ── Forecasted (Predicted) Water Levels ──────────────────────────────────────

interface COOPSPredictionResponse {
  predictions?: Array<{
    t: string;
    v: string;
  }>;
  error?: { message: string };
}

/**
 * Get forecasted (predicted) water levels for the next N hours.
 * Uses NOAA CO-OPS tide predictions.
 */
export async function getForecastedWaterLevels(
  stationId: string,
  hours: number = 18,
): Promise<WaterLevel[]> {
  try {
    const now = new Date();
    const end = new Date(now.getTime() + hours * 3600000);
    const beginDate = padDate(now);
    const endDate = padDate(end);

    const url =
      `${COOPS_BASE}?product=predictions&station=${stationId}` +
      `&begin_date=${encodeURIComponent(beginDate)}&end_date=${encodeURIComponent(endDate)}` +
      `&datum=MLLW&units=metric&time_zone=gmt&format=json&interval=6`;

    const data = await fetchJSON<COOPSPredictionResponse>(url, 10000);

    if (!data.predictions || data.predictions.length === 0) return [];

    return data.predictions.map((p) => ({
      stationId,
      timestamp: p.t,
      levelM: parseFloat(p.v),
      quality: 'p', // predictions
    }));
  } catch {
    return [];
  }
}

// ── Safety Color Coding ──────────────────────────────────────────────────────

/**
 * Determine the safety level of an adjusted depth relative to boat draft.
 *
 * RED    (danger):  depth < draft
 * ORANGE (caution): depth < draft + 0.3m buffer
 * YELLOW (tight):   depth < draft + 1.0m buffer
 * GREEN  (safe):    depth >= draft + 1.0m buffer
 */
export function getDynamicDepthSafety(
  adjustedDepthM: number,
  boatDraftM: number,
): DepthSafetyLevel {
  if (adjustedDepthM < boatDraftM) return 'danger';
  if (adjustedDepthM < boatDraftM + CAUTION_BUFFER_M) return 'caution';
  if (adjustedDepthM < boatDraftM + TIGHT_BUFFER_M) return 'tight';
  return 'safe';
}

/**
 * Get the hex color for a safety level.
 */
export function getDynamicDepthColor(
  adjustedDepthM: number,
  boatDraftM: number,
): string {
  const safety = getDynamicDepthSafety(adjustedDepthM, boatDraftM);
  return SAFETY_COLORS[safety];
}

export const SAFETY_COLORS: Record<DepthSafetyLevel, string> = {
  danger: '#F44336',   // Red
  caution: '#FF9800',  // Orange
  tight: '#FFC107',    // Yellow
  safe: '#4CAF50',     // Green
};

export const SAFETY_LABELS: Record<DepthSafetyLevel, string> = {
  danger: 'Too Shallow',
  caution: 'Caution',
  tight: 'Tight Clearance',
  safe: 'Safe',
};

// ── Core: Dynamic Depth Adjustment ───────────────────────────────────────────

/**
 * Adjust a single charted depth based on current water level at the nearest station.
 *
 * Charted depths on NOAA charts are referenced to MLLW (Mean Lower Low Water).
 * The actual depth = charted depth + current water level above MLLW.
 *
 * Example: Charted depth = 2.0m, Water level = +1.5m above MLLW
 *          Actual depth right now = 3.5m
 */
export async function getDynamicDepth(
  lat: number,
  lon: number,
  chartedDepthM: number,
  boatDraftM: number = 0.9,
): Promise<DynamicDepth | null> {
  const station = await getNearestWaterLevelStation(lat, lon);
  if (!station) return null;

  const level = await getCurrentWaterLevel(station.id);
  if (!level) return null;

  const adjustedDepthM = chartedDepthM + level.levelM;
  const safety = getDynamicDepthSafety(adjustedDepthM, boatDraftM);

  return {
    chartedDepthM,
    adjustmentM: level.levelM,
    adjustedDepthM: Math.round(adjustedDepthM * 100) / 100,
    lat,
    lon,
    safety,
    stationId: station.id,
    timestamp: level.timestamp,
  };
}

// ── Batch: Adjust All Depths in a Bounding Box ──────────────────────────────

/**
 * Batch adjust all charted depths within a bounding box.
 * Uses the nearest station for the center of the bbox (assumes
 * tidal influence is uniform within the area at this scale).
 */
export async function getAdjustedDepthGrid(
  bbox: BBox,
  chartedDepths: ChartedDepthPoint[],
  boatDraftM: number = 0.9,
): Promise<{
  depths: DynamicDepth[];
  station: WaterLevelStation | null;
  waterLevel: WaterLevel | null;
}> {
  const centerLat = (bbox.minLat + bbox.maxLat) / 2;
  const centerLon = (bbox.minLon + bbox.maxLon) / 2;

  const station = await getNearestWaterLevelStation(centerLat, centerLon);
  if (!station) {
    return { depths: [], station: null, waterLevel: null };
  }

  const level = await getCurrentWaterLevel(station.id);
  if (!level) {
    return { depths: [], station, waterLevel: null };
  }

  const adjustment = level.levelM;

  const depths: DynamicDepth[] = chartedDepths.map((pt) => {
    const adjustedDepthM = pt.depthM + adjustment;
    return {
      chartedDepthM: pt.depthM,
      adjustmentM: adjustment,
      adjustedDepthM: Math.round(adjustedDepthM * 100) / 100,
      lat: pt.lat,
      lon: pt.lon,
      safety: getDynamicDepthSafety(adjustedDepthM, boatDraftM),
      stationId: station.id,
      timestamp: level.timestamp,
    };
  });

  return { depths, station, waterLevel: level };
}

// ── Route Planner Integration ────────────────────────────────────────────────

export interface RouteLegDepthForecast {
  /** Segment index in the route */
  segmentIndex: number;
  /** Charted depth at this segment's shallowest point (meters) */
  chartedDepthM: number;
  /** Forecasted water level at planned arrival time (meters) */
  forecastedLevelM: number;
  /** Adjusted depth at planned time */
  adjustedDepthM: number;
  /** Safety level at planned time */
  safety: DepthSafetyLevel;
  /** ISO timestamp of the forecast */
  forecastTimestamp: string;
}

/**
 * Check route segments against forecasted tide levels at the planned departure time.
 * This is the key integration with the route planner -- it tells boaters which
 * segments will be safe/unsafe at their planned departure.
 */
export async function checkRouteDepthWithTides(
  segments: Array<{
    lat: number;
    lon: number;
    chartedDepthM: number | null;
    /** Hours from now that the boat expects to reach this segment */
    etaHours: number;
  }>,
  boatDraftM: number,
): Promise<RouteLegDepthForecast[]> {
  if (segments.length === 0) return [];

  // Find station near the route midpoint
  const midIdx = Math.floor(segments.length / 2);
  const station = await getNearestWaterLevelStation(
    segments[midIdx].lat,
    segments[midIdx].lon,
  );
  if (!station) return [];

  // Get forecast for the max ETA window
  const maxHours = Math.max(...segments.map((s) => s.etaHours), 1);
  const forecasts = await getForecastedWaterLevels(station.id, Math.ceil(maxHours) + 1);
  if (forecasts.length === 0) return [];

  const results: RouteLegDepthForecast[] = [];

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.chartedDepthM == null) continue;

    // Find the closest forecast to this segment's ETA
    const etaMs = Date.now() + seg.etaHours * 3600000;
    let closest = forecasts[0];
    let minTimeDiff = Infinity;

    for (const f of forecasts) {
      const fTime = new Date(f.timestamp).getTime();
      const diff = Math.abs(fTime - etaMs);
      if (diff < minTimeDiff) {
        minTimeDiff = diff;
        closest = f;
      }
    }

    const adjustedDepthM = seg.chartedDepthM + closest.levelM;

    results.push({
      segmentIndex: i,
      chartedDepthM: seg.chartedDepthM,
      forecastedLevelM: closest.levelM,
      adjustedDepthM: Math.round(adjustedDepthM * 100) / 100,
      safety: getDynamicDepthSafety(adjustedDepthM, boatDraftM),
      forecastTimestamp: closest.timestamp,
    });
  }

  return results;
}

// ── Utility: Format Water Level for Display ──────────────────────────────────

/**
 * Format the current water level as a user-friendly badge string.
 * e.g. "Tide: +1.2m" or "Tide: -0.3m"
 */
export function formatTideBadge(levelM: number): string {
  const sign = levelM >= 0 ? '+' : '';
  return `Tide: ${sign}${levelM.toFixed(1)}m`;
}

/**
 * Format adjustment for imperial users.
 */
export function formatTideBadgeFt(levelM: number): string {
  const ft = levelM * 3.28084;
  const sign = ft >= 0 ? '+' : '';
  return `Tide: ${sign}${ft.toFixed(1)}ft`;
}

/**
 * Convert meters to feet for display.
 */
export function depthToFeet(meters: number): number {
  return Math.round(meters * 3.28084 * 10) / 10;
}
