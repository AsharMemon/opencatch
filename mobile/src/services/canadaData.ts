/**
 * Canadian open data service for OpenCatch.
 *
 * Uses Environment Canada's OGC-API (api.weather.gc.ca) for:
 * - Real-time hydrometric data (water levels, flow) via Water Survey of Canada
 * - Weather conditions and forecasts
 * - Air Quality Health Index (AQHI)
 *
 * All endpoints are free, anonymous, no API key required.
 * Rate limit: 10,000 features per request.
 */

const BASE_URL = 'https://api.weather.gc.ca';
const REQUEST_TIMEOUT = 8000;

// ── Types ───────────────────────────────────────────────────────────

export interface HydrometricStation {
  id: string;
  name: string;
  province: string;
  lat: number;
  lon: number;
  status: 'Active' | 'Discontinued';
  drainageArea?: number; // km²
}

export interface WaterLevelReading {
  stationId: string;
  timestamp: string;
  level: number | null;      // metres
  discharge: number | null;  // m³/s
}

export interface AQHIReading {
  stationId: string;
  location: string;
  aqhi: number;          // 1-10+ scale
  forecastToday?: number;
  forecastTonight?: number;
  forecastTomorrow?: number;
}

export interface WeatherObservation {
  stationId: string;
  name: string;
  lat: number;
  lon: number;
  timestamp: string;
  airTemp: number | null;       // °C
  dewPoint: number | null;      // °C
  humidity: number | null;      // %
  windSpeed: number | null;     // km/h
  windDir: number | null;       // degrees
  pressure: number | null;      // kPa
  visibility: number | null;    // km
  condition: string | null;
}

// ── Fetch helper ────────────────────────────────────────────────────

async function ogcFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`);
  url.searchParams.set('f', 'json');
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const res = await fetch(url.toString(), { signal: controller.signal });
    clearTimeout(timeout);
    if (!res.ok) throw new Error(`Canada API ${res.status}: ${path}`);
    return res.json();
  } catch (err) {
    clearTimeout(timeout);
    throw err;
  }
}

// ── Hydrometric (Water Levels & Flow) ───────────────────────────────

/**
 * Get nearby hydrometric stations within a bounding box.
 */
export async function getHydrometricStations(
  bbox: [number, number, number, number], // [minLon, minLat, maxLon, maxLat]
  limit = 50,
): Promise<HydrometricStation[]> {
  const data = await ogcFetch<any>('/collections/hydrometric-stations/items', {
    bbox: bbox.join(','),
    limit: String(limit),
  });

  return (data.features || []).map((f: any) => ({
    id: f.properties.STATION_NUMBER,
    name: f.properties.STATION_NAME,
    province: f.properties.PROV_TERR_STATE_LOC,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    status: f.properties.STATION_STATUS === 'Active' ? 'Active' : 'Discontinued',
    drainageArea: f.properties.DRAINAGE_AREA_GROSS,
  }));
}

/**
 * Get real-time water level readings for a station (last 30 days).
 */
export async function getWaterLevels(
  stationId: string,
  limit = 168, // ~7 days of hourly
): Promise<WaterLevelReading[]> {
  const data = await ogcFetch<any>('/collections/hydrometric-realtime/items', {
    STATION_NUMBER: stationId,
    limit: String(limit),
    sortby: '-DATETIME',
  });

  return (data.features || []).map((f: any) => ({
    stationId: f.properties.STATION_NUMBER,
    timestamp: f.properties.DATETIME,
    level: f.properties.LEVEL ?? null,
    discharge: f.properties.DISCHARGE ?? null,
  }));
}

/**
 * Get daily mean water levels for a station (historical).
 */
export async function getDailyMeanLevels(
  stationId: string,
  startDate: string, // YYYY-MM-DD
  endDate: string,
): Promise<WaterLevelReading[]> {
  const data = await ogcFetch<any>('/collections/hydrometric-daily-mean/items', {
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
}

// ── Air Quality (AQHI) ─────────────────────────────────────────────

/**
 * Get current AQHI readings near a location.
 */
export async function getAQHI(
  bbox: [number, number, number, number],
  limit = 10,
): Promise<AQHIReading[]> {
  const data = await ogcFetch<any>('/collections/aqhi-observations-realtime/items', {
    bbox: bbox.join(','),
    limit: String(limit),
    sortby: '-latest_reading_date',
  });

  return (data.features || []).map((f: any) => ({
    stationId: f.properties.location_id || f.id,
    location: f.properties.location_name_en || '',
    aqhi: f.properties.current_aqhi ?? 0,
    forecastToday: f.properties.forecast_aqhi_today,
    forecastTonight: f.properties.forecast_aqhi_tonight,
    forecastTomorrow: f.properties.forecast_aqhi_tomorrow,
  }));
}

// ── Weather Observations ────────────────────────────────────────────

/**
 * Get current weather observations near a location.
 * Uses MSC GeoMet climate observations.
 */
export async function getCurrentWeather(
  lat: number,
  lon: number,
  radiusKm = 50,
): Promise<WeatherObservation[]> {
  // Create bounding box from radius (approximate)
  const dLat = radiusKm / 111;
  const dLon = radiusKm / (111 * Math.cos((lat * Math.PI) / 180));
  const bbox: [number, number, number, number] = [lon - dLon, lat - dLat, lon + dLon, lat + dLat];

  const data = await ogcFetch<any>('/collections/climate-hourly/items', {
    bbox: bbox.join(','),
    limit: '20',
    sortby: '-LOCAL_DATE',
  });

  return (data.features || []).map((f: any) => ({
    stationId: f.properties.CLIMATE_IDENTIFIER || f.id,
    name: f.properties.STATION_NAME || '',
    lat: f.geometry?.coordinates?.[1] ?? 0,
    lon: f.geometry?.coordinates?.[0] ?? 0,
    timestamp: f.properties.LOCAL_DATE || '',
    airTemp: f.properties.TEMP ?? null,
    dewPoint: f.properties.DEW_POINT_TEMP ?? null,
    humidity: f.properties.REL_HUM ?? null,
    windSpeed: f.properties.WIND_SPD ?? null,
    windDir: f.properties.WIND_DIR ?? null,
    pressure: f.properties.STN_PRESS ?? null,
    visibility: f.properties.VISIBILITY ?? null,
    condition: f.properties.WEATHER ?? null,
  }));
}

// ── Convenience: Is this a Canadian coordinate? ─────────────────────

export function isCanadianLocation(lat: number, _lon: number): boolean {
  // Rough check: Canada spans ~42°N to ~84°N
  return lat >= 42 && lat <= 84;
}

/**
 * Convert AQHI (1-10+) to US AQI equivalent (0-500) for unified display.
 * Rough mapping — AQHI is a different scale.
 */
export function aqhiToAqi(aqhi: number): number {
  if (aqhi <= 3) return Math.round(aqhi * 17); // 1-3 → 0-50 (Good)
  if (aqhi <= 6) return Math.round(50 + (aqhi - 3) * 17); // 4-6 → 50-100 (Moderate)
  if (aqhi <= 10) return Math.round(100 + (aqhi - 6) * 25); // 7-10 → 100-200 (Unhealthy)
  return Math.round(200 + (aqhi - 10) * 50); // 10+ → 200+ (Very Unhealthy)
}
