/**
 * OpenCatch -- Wind Field Service
 *
 * Builds a forecastable wind field from Open-Meteo multi-location queries.
 * The field powers a Windy-style overlay with:
 * - dense speed tint
 * - vector arrows
 * - flow lines / stream traces
 * - selectable forecast hours
 */

import { calculateDistance } from './nauticalNav';

export interface WindViewportBounds {
  minLat: number;
  minLon: number;
  maxLat: number;
  maxLon: number;
}

export interface WindGridPoint {
  lat: number;
  lon: number;
  row: number;
  col: number;
  speedKn: number;
  speedMs: number;
  speedMph: number;
  directionDeg: number;
  gustKn?: number;
  gustMph?: number;
}

export interface WindForecastFrame {
  hourOffset: number;
  time: string;
  label: string;
  points: WindGridPoint[];
}

export interface WindFieldData {
  centerLat: number;
  centerLon: number;
  extentKey: string;
  fetchedAt: number;
  frames: WindForecastFrame[];
}

const OPEN_METEO_BASE = 'https://api.open-meteo.com/v1/forecast';
const CACHE_TTL_MS = 15 * 60 * 1000;
const DEFAULT_GRID_RADIUS_DEG = 1.4;
const TARGET_GRID_COLUMNS_NEAR = 18;
const TARGET_GRID_COLUMNS_MEDIUM = 22;
const TARGET_GRID_COLUMNS_FAR = 26;
const TARGET_GRID_ROWS_NEAR = 14;
const TARGET_GRID_ROWS_MEDIUM = 16;
const TARGET_GRID_ROWS_FAR = 18;
const MIN_GRID_SPACING_DEG = 0.025;
const MAX_GRID_SPACING_DEG = 0.9;
const DEFAULT_FORECAST_HOURS = 8;
const MAX_FORECAST_HOURS = 12;
const BATCH_SIZE = 36;

const cache = new Map<string, WindFieldData>();

function kmhToKnots(kmh: number): number {
  return kmh * 0.539957;
}

function kmhToMs(kmh: number): number {
  return kmh / 3.6;
}

function kmhToMph(kmh: number): number {
  return kmh * 0.621371;
}

export function windSpeedColor(knots: number): string {
  if (knots < 3) return '#3B82F6';
  if (knots < 7) return '#06B6D4';
  if (knots < 12) return '#22C55E';
  if (knots < 18) return '#EAB308';
  if (knots < 25) return '#F97316';
  if (knots < 35) return '#EF4444';
  return '#B91C1C';
}

function formatForecastLabel(isoTime: string, hourOffset: number): string {
  if (hourOffset === 0) return 'Now';
  const date = new Date(isoTime);
  if (!Number.isFinite(date.getTime())) return `+${hourOffset}h`;
  return date.toLocaleTimeString([], { hour: 'numeric' }).replace(':00', '');
}

function normalizeBounds(
  centerLat: number,
  centerLon: number,
  bounds?: WindViewportBounds | null,
): WindViewportBounds {
  if (
    bounds &&
    Number.isFinite(bounds.minLat) &&
    Number.isFinite(bounds.minLon) &&
    Number.isFinite(bounds.maxLat) &&
    Number.isFinite(bounds.maxLon) &&
    bounds.maxLat > bounds.minLat &&
    bounds.maxLon > bounds.minLon
  ) {
    return bounds;
  }

  return {
    minLat: centerLat - DEFAULT_GRID_RADIUS_DEG,
    maxLat: centerLat + DEFAULT_GRID_RADIUS_DEG,
    minLon: centerLon - DEFAULT_GRID_RADIUS_DEG,
    maxLon: centerLon + DEFAULT_GRID_RADIUS_DEG,
  };
}

function boundsKey(bounds: WindViewportBounds): string {
  return [
    bounds.minLat.toFixed(2),
    bounds.minLon.toFixed(2),
    bounds.maxLat.toFixed(2),
    bounds.maxLon.toFixed(2),
  ].join(':');
}

interface WindGridCoord {
  lat: number;
  lon: number;
  row: number;
  col: number;
}

function buildGrid(
  centerLat: number,
  centerLon: number,
  bounds?: WindViewportBounds | null,
): WindGridCoord[] {
  const coords: WindGridCoord[] = [];
  const seen = new Set<string>();
  const frame = normalizeBounds(centerLat, centerLon, bounds);
  const latSpan = Math.max(frame.maxLat - frame.minLat, 0.2);
  const lonSpan = Math.max(frame.maxLon - frame.minLon, 0.2);
  const maxSpan = Math.max(latSpan, lonSpan);
  const targetColumns =
    maxSpan > 10
      ? TARGET_GRID_COLUMNS_FAR
      : maxSpan > 4
        ? TARGET_GRID_COLUMNS_MEDIUM
        : TARGET_GRID_COLUMNS_NEAR;
  const targetRows =
    maxSpan > 10
      ? TARGET_GRID_ROWS_FAR
      : maxSpan > 4
        ? TARGET_GRID_ROWS_MEDIUM
        : TARGET_GRID_ROWS_NEAR;
  const latSpacing = Math.min(
    MAX_GRID_SPACING_DEG,
    Math.max(MIN_GRID_SPACING_DEG, latSpan / targetRows),
  );
  const lonSpacing = Math.min(
    MAX_GRID_SPACING_DEG,
    Math.max(MIN_GRID_SPACING_DEG, lonSpan / targetColumns),
  );

  let row = 0;
  for (let lat = frame.minLat; lat <= frame.maxLat + 1e-9; lat += latSpacing) {
    const stagger = row % 2 === 0 ? 0 : lonSpacing / 2;
    let col = 0;
    for (
      let lon = frame.minLon - stagger;
      lon <= frame.maxLon + lonSpacing + 1e-9;
      lon += lonSpacing
    ) {
      const clampedLon = Math.min(frame.maxLon, Math.max(frame.minLon, lon));
      const point = {
        lat: parseFloat(lat.toFixed(4)),
        lon: parseFloat(clampedLon.toFixed(4)),
        row,
        col,
      };
      const key = `${point.lat}:${point.lon}`;
      if (!seen.has(key)) {
        seen.add(key);
        coords.push(point);
        col += 1;
      }
    }
    row += 1;
  }

  return coords;
}

interface OpenMeteoHourlyResponse {
  hourly?: {
    time?: string[];
    wind_speed_10m?: number[];
    wind_direction_10m?: number[];
    wind_gusts_10m?: number[];
  };
}

async function fetchWindBatch(
  coords: WindGridCoord[],
  forecastHours: number,
): Promise<{
  times: string[];
  frames: WindGridPoint[][];
}> {
  const limitedHours = Math.max(1, Math.min(forecastHours, MAX_FORECAST_HOURS));
  const frames: WindGridPoint[][] = Array.from({ length: limitedHours }, () => []);
  let times: string[] = [];

  for (let i = 0; i < coords.length; i += BATCH_SIZE) {
    const batch = coords.slice(i, i + BATCH_SIZE);
    const lats = batch.map((coord) => coord.lat.toFixed(4)).join(',');
    const lons = batch.map((coord) => coord.lon.toFixed(4)).join(',');
    const url =
      `${OPEN_METEO_BASE}?latitude=${lats}&longitude=${lons}` +
      '&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m' +
      `&forecast_hours=${limitedHours}` +
      '&wind_speed_unit=kmh';

    try {
      const response = await fetch(url);
      if (!response.ok) continue;
      const json = await response.json();
      const entries: OpenMeteoHourlyResponse[] = Array.isArray(json) ? json : [json];

      entries.forEach((entry, entryIndex) => {
        const coord = batch[entryIndex];
        if (!coord) return;

        const hourlyTimes = entry.hourly?.time?.slice(0, limitedHours) ?? [];
        if (times.length === 0 && hourlyTimes.length > 0) {
          times = hourlyTimes;
        }

        for (let hourIndex = 0; hourIndex < limitedHours; hourIndex += 1) {
          const speedKmh = entry.hourly?.wind_speed_10m?.[hourIndex];
          const directionDeg = entry.hourly?.wind_direction_10m?.[hourIndex];
          if (speedKmh == null || directionDeg == null) continue;

          const gustKmh = entry.hourly?.wind_gusts_10m?.[hourIndex];
          frames[hourIndex].push({
            lat: coord.lat,
            lon: coord.lon,
            row: coord.row,
            col: coord.col,
            speedKn: kmhToKnots(speedKmh),
            speedMs: kmhToMs(speedKmh),
            speedMph: kmhToMph(speedKmh),
            directionDeg,
            gustKn: gustKmh != null ? kmhToKnots(gustKmh) : undefined,
            gustMph: gustKmh != null ? kmhToMph(gustKmh) : undefined,
          });
        }
      });
    } catch {
      // Skip failed multi-location batches and keep any existing frames.
    }
  }

  return { times, frames };
}

function cacheKey(
  centerLat: number,
  centerLon: number,
  extentKey: string,
  forecastHours: number,
): string {
  return [
    centerLat.toFixed(2),
    centerLon.toFixed(2),
    extentKey,
    Math.min(forecastHours, MAX_FORECAST_HOURS),
  ].join(':');
}

export async function fetchWindField(
  centerLat: number,
  centerLon: number,
  bounds?: WindViewportBounds | null,
  forecastHours: number = DEFAULT_FORECAST_HOURS,
): Promise<WindFieldData> {
  const normalizedBounds = normalizeBounds(centerLat, centerLon, bounds);
  const extentKey = boundsKey(normalizedBounds);
  const key = cacheKey(centerLat, centerLon, extentKey, forecastHours);
  const cached = cache.get(key);

  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
    return cached;
  }

  const grid = buildGrid(centerLat, centerLon, normalizedBounds);
  const limitedHours = Math.max(1, Math.min(forecastHours, MAX_FORECAST_HOURS));
  const { times, frames } = await fetchWindBatch(grid, limitedHours);

  const windField: WindFieldData = {
    centerLat,
    centerLon,
    extentKey,
    fetchedAt: Date.now(),
    frames: frames.map((points, hourOffset) => ({
      hourOffset,
      time:
        times[hourOffset] ??
        new Date(Date.now() + hourOffset * 3600000).toISOString(),
      label: formatForecastLabel(
        times[hourOffset] ??
          new Date(Date.now() + hourOffset * 3600000).toISOString(),
        hourOffset,
      ),
      points,
    })),
  };

  cache.set(key, windField);
  return windField;
}

function nearestPoint(points: WindGridPoint[], lat: number, lon: number): WindGridPoint | null {
  let best: WindGridPoint | null = null;
  let bestScore = Number.POSITIVE_INFINITY;

  for (const point of points) {
    const latDelta = point.lat - lat;
    const lonDelta = (point.lon - lon) * Math.cos((lat * Math.PI) / 180);
    const score = latDelta * latDelta + lonDelta * lonDelta;
    if (score < bestScore) {
      bestScore = score;
      best = point;
    }
  }

  return best;
}

function movePoint(
  lat: number,
  lon: number,
  bearingDeg: number,
  distanceNm: number,
): { lat: number; lon: number } {
  const earthRadiusNm = 3440.065;
  const angularDistance = distanceNm / earthRadiusNm;
  const bearingRad = (bearingDeg * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lon1 = (lon * Math.PI) / 180;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearingRad),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearingRad) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2),
    );

  return {
    lat: (lat2 * 180) / Math.PI,
    lon: (((lon2 * 180) / Math.PI + 540) % 360) - 180,
  };
}

function estimatePointSpacingNm(points: WindGridPoint[]): number {
  const byRow = new Map<number, WindGridPoint[]>();
  for (const point of points) {
    const rowPoints = byRow.get(point.row) ?? [];
    rowPoints.push(point);
    byRow.set(point.row, rowPoints);
  }

  const distances: number[] = [];
  for (const rowPoints of byRow.values()) {
    const ordered = rowPoints.slice().sort((a, b) => a.col - b.col);
    for (let index = 0; index < ordered.length - 1; index += 1) {
      distances.push(
        calculateDistance(
          { lat: ordered[index].lat, lon: ordered[index].lon },
          { lat: ordered[index + 1].lat, lon: ordered[index + 1].lon },
        ),
      );
    }
  }

  if (distances.length === 0) return 0.4;
  const sorted = distances.sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0.4;
}

export function windFrameToHeatGeoJSON(
  frame: WindForecastFrame,
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: frame.points.map((point) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [point.lon, point.lat],
      },
      properties: {
        speedKn: Math.round(point.speedKn * 10) / 10,
        color: windSpeedColor(point.speedKn),
      },
    })),
  };
}

export function windFrameToArrowGeoJSON(
  frame: WindForecastFrame,
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: frame.points.map((point) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [point.lon, point.lat],
      },
      properties: {
        speedKn: Math.round(point.speedKn * 10) / 10,
        speedMs: Math.round(point.speedMs * 10) / 10,
        speedMph: Math.round(point.speedMph),
        gustMph: point.gustMph != null ? Math.round(point.gustMph) : null,
        directionDeg: point.directionDeg,
        iconRotation: (point.directionDeg + 180) % 360,
        label: `${Math.round(point.speedMs)}`,
        arrowColor: windSpeedColor(point.speedKn),
      },
    })),
  };
}

export function windFrameToStreamlineGeoJSON(
  frame: WindForecastFrame,
): GeoJSON.FeatureCollection {
  if (frame.points.length === 0) {
    return { type: 'FeatureCollection', features: [] };
  }

  const rows = Math.max(...frame.points.map((point) => point.row)) + 1;
  const cols = Math.max(...frame.points.map((point) => point.col)) + 1;
  const rowStride = rows > 16 ? 2 : 1;
  const colStride = cols > 20 ? 3 : 2;
  const spacingNm = estimatePointSpacingNm(frame.points);
  const stepNm = Math.max(0.18, Math.min(0.55, spacingNm * 0.8));
  const seedMap = new Map(frame.points.map((point) => [`${point.row},${point.col}`, point]));
  const features: GeoJSON.Feature[] = [];

  for (let row = 0; row < rows; row += rowStride) {
    for (let col = row % 2 === 0 ? 0 : 1; col < cols; col += colStride) {
      const seed = seedMap.get(`${row},${col}`);
      if (!seed || seed.speedKn < 2) continue;

      const coordinates: Array<[number, number]> = [[seed.lon, seed.lat]];
      let cursor = { lat: seed.lat, lon: seed.lon };
      let speedSum = 0;
      let sampleCount = 0;

      for (let step = 0; step < 6; step += 1) {
        const vector = nearestPoint(frame.points, cursor.lat, cursor.lon);
        if (!vector || vector.speedKn < 1.5) break;
        speedSum += vector.speedKn;
        sampleCount += 1;
        cursor = movePoint(
          cursor.lat,
          cursor.lon,
          (vector.directionDeg + 180) % 360,
          stepNm,
        );
        coordinates.push([cursor.lon, cursor.lat]);
      }

      if (coordinates.length < 3 || sampleCount === 0) continue;
      const averageSpeed = speedSum / sampleCount;
      features.push({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates,
        },
        properties: {
          color: windSpeedColor(averageSpeed),
          opacity: averageSpeed >= 20 ? 0.62 : averageSpeed >= 10 ? 0.48 : 0.34,
        },
      });
    }
  }

  return {
    type: 'FeatureCollection',
    features,
  };
}
