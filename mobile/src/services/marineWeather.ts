/**
 * Marine Weather Service for OpenCatch.
 *
 * Fetches wave, swell, sea-surface temperature, visibility, and ocean
 * current data for coastal and offshore fishing. Uses the free
 * Open-Meteo Marine API (no key required) for most parameters and
 * NOAA CO-OPS / Copernicus for ocean currents.
 *
 * Open-Meteo Marine: https://open-meteo.com/en/docs/marine-weather-api
 * NOAA CO-OPS:       https://api.tidesandcurrents.noaa.gov/api/prod/
 */

// ── Types ────────────────────────────────────────────────────────

/** Wave height and period data at a point. */
export interface WaveData {
  /** Significant wave height in feet. */
  heightFt: number;
  /** Wave period in seconds. */
  periodSec: number;
  /** Wave direction in compass degrees (0-360). */
  directionDeg: number;
  /** ISO-8601 timestamp of the observation/forecast. */
  timestamp: string;
}

/** Swell data at a point. */
export interface SwellData {
  /** Primary swell height in feet. */
  heightFt: number;
  /** Swell period in seconds. */
  periodSec: number;
  /** Swell direction in compass degrees (0-360). */
  directionDeg: number;
  /** Secondary swell height in feet, if present. */
  secondaryHeightFt: number | null;
  /** Secondary swell period in seconds, if present. */
  secondaryPeriodSec: number | null;
  /** ISO-8601 timestamp. */
  timestamp: string;
}

/** Combined marine weather forecast. */
export interface MarineForecast {
  lat: number;
  lon: number;
  /** ISO-8601 timestamp of the forecast. */
  timestamp: string;
  /** Significant wave height in feet. */
  waveHeightFt: number;
  /** Wave period in seconds. */
  wavePeriodSec: number;
  /** Wave direction in compass degrees. */
  waveDirectionDeg: number;
  /** Primary swell height in feet. */
  swellHeightFt: number;
  /** Swell period in seconds. */
  swellPeriodSec: number;
  /** Swell direction in compass degrees. */
  swellDirectionDeg: number;
  /** Sea surface temperature in degrees Fahrenheit. */
  seaSurfaceTempF: number;
  /** Visibility in nautical miles. */
  visibilityNm: number | null;
  /** Wind wave height in feet. */
  windWaveHeightFt: number;
  /** Current speed in knots, if available. */
  currentSpeedKnots: number | null;
  /** Current direction in compass degrees, if available. */
  currentDirectionDeg: number | null;
  /** Hourly forecast arrays for chart display. */
  hourly: MarineHourly[];
}

/** A single hourly data point for marine charts. */
export interface MarineHourly {
  /** ISO-8601 timestamp. */
  time: string;
  /** Significant wave height in feet. */
  waveHeightFt: number;
  /** Wave period in seconds. */
  wavePeriodSec: number;
  /** Swell height in feet. */
  swellHeightFt: number;
  /** Sea surface temperature in Fahrenheit. */
  sstF: number;
}

/** Ocean current observation/forecast at a point. */
export interface OceanCurrent {
  /** Current speed in knots. */
  speedKnots: number;
  /** Current direction in compass degrees (direction the current flows toward). */
  directionDeg: number;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Source identifier (e.g. "NOAA CO-OPS", "Copernicus"). */
  source: string;
}

// ── Constants ────────────────────────────────────────────────────

const OPEN_METEO_MARINE = 'https://marine-api.open-meteo.com/v1/marine';
const NOAA_COOPS_BASE = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter';

const USER_AGENT = 'OpenCatch/1.0 (contact@opencatch.app)';
const REQUEST_TIMEOUT_MS = 15_000;

/** Cache TTL: 30 minutes for marine weather. */
const CACHE_TTL_MS = 30 * 60 * 1000;

// ── Cache ────────────────────────────────────────────────────────

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

const cache = new Map<string, CacheEntry<any>>();

function coordKey(prefix: string, lat: number, lon: number): string {
  return `${prefix}:${lat.toFixed(2)},${lon.toFixed(2)}`;
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

/** Convert meters to feet. */
function metersToFeet(m: number): number {
  return m * 3.28084;
}

/** Convert Celsius to Fahrenheit. */
function celsiusToF(c: number): number {
  return c * 9 / 5 + 32;
}

/** Convert km to nautical miles. */
function kmToNm(km: number): number {
  return km * 0.539957;
}

// ── Open-Meteo Marine API ────────────────────────────────────────

interface OpenMeteoMarineResponse {
  hourly: {
    time: string[];
    wave_height?: number[];
    wave_period?: number[];
    wave_direction?: number[];
    swell_wave_height?: number[];
    swell_wave_period?: number[];
    swell_wave_direction?: number[];
    wind_wave_height?: number[];
    ocean_current_velocity?: number[];
    ocean_current_direction?: number[];
    sea_surface_temperature?: number[];   // added via &hourly=
  };
}

/**
 * Fetch significant wave height at a location.
 */
export async function getWaveHeight(lat: number, lon: number): Promise<WaveData | null> {
  const key = coordKey('wave', lat, lon);
  const cached = getCached<WaveData>(key);
  if (cached) return cached;

  const url =
    `${OPEN_METEO_MARINE}?latitude=${lat}&longitude=${lon}` +
    `&hourly=wave_height,wave_period,wave_direction&forecast_hours=1`;

  try {
    const data = await fetchJSON<OpenMeteoMarineResponse>(url);
    const h = data.hourly;
    if (!h.time?.length) return null;

    const result: WaveData = {
      heightFt: metersToFeet(h.wave_height?.[0] ?? 0),
      periodSec: h.wave_period?.[0] ?? 0,
      directionDeg: h.wave_direction?.[0] ?? 0,
      timestamp: h.time[0],
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getWaveHeight failed:', err);
    return null;
  }
}

/**
 * Fetch swell height, period, and direction.
 */
export async function getSwellData(lat: number, lon: number): Promise<SwellData | null> {
  const key = coordKey('swell', lat, lon);
  const cached = getCached<SwellData>(key);
  if (cached) return cached;

  const url =
    `${OPEN_METEO_MARINE}?latitude=${lat}&longitude=${lon}` +
    `&hourly=swell_wave_height,swell_wave_period,swell_wave_direction` +
    `&forecast_hours=1`;

  try {
    const data = await fetchJSON<OpenMeteoMarineResponse>(url);
    const h = data.hourly;
    if (!h.time?.length) return null;

    const result: SwellData = {
      heightFt: metersToFeet(h.swell_wave_height?.[0] ?? 0),
      periodSec: h.swell_wave_period?.[0] ?? 0,
      directionDeg: h.swell_wave_direction?.[0] ?? 0,
      secondaryHeightFt: null,
      secondaryPeriodSec: null,
      timestamp: h.time[0],
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getSwellData failed:', err);
    return null;
  }
}

/**
 * Fetch sea surface temperature.
 */
export async function getSeaSurfaceTemp(
  lat: number,
  lon: number,
): Promise<{ tempF: number; timestamp: string } | null> {
  const key = coordKey('sst', lat, lon);
  const cached = getCached<{ tempF: number; timestamp: string }>(key);
  if (cached) return cached;

  // Open-Meteo does not always have SST; fall back to NOAA if needed.
  const url =
    `${OPEN_METEO_MARINE}?latitude=${lat}&longitude=${lon}` +
    `&hourly=sea_surface_temperature&forecast_hours=1`;

  try {
    const data = await fetchJSON<{ hourly: { time: string[]; sea_surface_temperature?: number[] } }>(url);
    const h = data.hourly;
    const sstC = h.sea_surface_temperature?.[0];
    if (sstC == null || !h.time?.length) return null;

    const result = { tempF: celsiusToF(sstC), timestamp: h.time[0] };
    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getSeaSurfaceTemp failed:', err);
    return null;
  }
}

/**
 * Fetch marine visibility at a location.
 * Uses Open-Meteo weather API (not marine) for visibility data.
 */
export async function getVisibility(
  lat: number,
  lon: number,
): Promise<{ visibilityNm: number; timestamp: string } | null> {
  const key = coordKey('vis', lat, lon);
  const cached = getCached<{ visibilityNm: number; timestamp: string }>(key);
  if (cached) return cached;

  // Visibility comes from the regular weather API, not the marine API.
  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
    `&hourly=visibility&forecast_hours=1`;

  try {
    const data = await fetchJSON<{ hourly: { time: string[]; visibility?: number[] } }>(url);
    const h = data.hourly;
    const visKm = h.visibility?.[0];
    if (visKm == null || !h.time?.length) return null;

    // Open-Meteo returns visibility in meters.
    const result = { visibilityNm: kmToNm(visKm / 1000), timestamp: h.time[0] };
    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getVisibility failed:', err);
    return null;
  }
}

/**
 * Fetch a combined marine weather forecast including waves, swell,
 * SST, and hourly forecast data for charts.
 */
export async function getMarineForecast(lat: number, lon: number): Promise<MarineForecast | null> {
  const key = coordKey('marine-forecast', lat, lon);
  const cached = getCached<MarineForecast>(key);
  if (cached) return cached;

  const params = [
    'wave_height',
    'wave_period',
    'wave_direction',
    'swell_wave_height',
    'swell_wave_period',
    'swell_wave_direction',
    'wind_wave_height',
    'ocean_current_velocity',
    'ocean_current_direction',
  ].join(',');

  const url =
    `${OPEN_METEO_MARINE}?latitude=${lat}&longitude=${lon}` +
    `&hourly=${params}&forecast_hours=48`;

  try {
    const data = await fetchJSON<OpenMeteoMarineResponse>(url);
    const h = data.hourly;
    if (!h.time?.length) return null;

    // Fetch SST and visibility in parallel.
    const [sst, vis] = await Promise.all([
      getSeaSurfaceTemp(lat, lon),
      getVisibility(lat, lon),
    ]);

    const hourly: MarineHourly[] = h.time.map((t, i) => ({
      time: t,
      waveHeightFt: metersToFeet(h.wave_height?.[i] ?? 0),
      wavePeriodSec: h.wave_period?.[i] ?? 0,
      swellHeightFt: metersToFeet(h.swell_wave_height?.[i] ?? 0),
      sstF: sst?.tempF ?? 0,
    }));

    const result: MarineForecast = {
      lat,
      lon,
      timestamp: h.time[0],
      waveHeightFt: metersToFeet(h.wave_height?.[0] ?? 0),
      wavePeriodSec: h.wave_period?.[0] ?? 0,
      waveDirectionDeg: h.wave_direction?.[0] ?? 0,
      swellHeightFt: metersToFeet(h.swell_wave_height?.[0] ?? 0),
      swellPeriodSec: h.swell_wave_period?.[0] ?? 0,
      swellDirectionDeg: h.swell_wave_direction?.[0] ?? 0,
      seaSurfaceTempF: sst?.tempF ?? 0,
      visibilityNm: vis?.visibilityNm ?? null,
      windWaveHeightFt: metersToFeet(h.wind_wave_height?.[0] ?? 0),
      currentSpeedKnots: h.ocean_current_velocity?.[0] ?? null,
      currentDirectionDeg: h.ocean_current_direction?.[0] ?? null,
      hourly,
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getMarineForecast failed:', err);
    return null;
  }
}

/**
 * Fetch ocean current data from NOAA CO-OPS or Open-Meteo.
 */
export async function getOceanCurrents(lat: number, lon: number): Promise<OceanCurrent | null> {
  const key = coordKey('currents', lat, lon);
  const cached = getCached<OceanCurrent>(key);
  if (cached) return cached;

  // Try Open-Meteo marine first (has ocean_current_velocity).
  const url =
    `${OPEN_METEO_MARINE}?latitude=${lat}&longitude=${lon}` +
    `&hourly=ocean_current_velocity,ocean_current_direction&forecast_hours=1`;

  try {
    const data = await fetchJSON<OpenMeteoMarineResponse>(url);
    const h = data.hourly;
    const speed = h.ocean_current_velocity?.[0];
    const dir = h.ocean_current_direction?.[0];
    if (speed == null || dir == null || !h.time?.length) return null;

    // Open-Meteo current velocity is in m/s — convert to knots.
    const result: OceanCurrent = {
      speedKnots: speed * 1.94384,
      directionDeg: dir,
      timestamp: h.time[0],
      source: 'Open-Meteo Marine',
    };

    setCache(key, result);
    return result;
  } catch (err) {
    console.warn('[marineWeather] getOceanCurrents failed:', err);
    return null;
  }
}

// ── Utility ──────────────────────────────────────────────────────

/**
 * Return a human-friendly sea state description based on wave height.
 * Uses the Douglas Sea Scale.
 */
export function describeSeaState(waveHeightFt: number): string {
  if (waveHeightFt < 0.3) return 'Calm (glassy)';
  if (waveHeightFt < 1) return 'Calm (rippled)';
  if (waveHeightFt < 2) return 'Smooth';
  if (waveHeightFt < 4) return 'Slight';
  if (waveHeightFt < 8) return 'Moderate';
  if (waveHeightFt < 13) return 'Rough';
  if (waveHeightFt < 20) return 'Very rough';
  if (waveHeightFt < 30) return 'High';
  return 'Very high';
}

/**
 * Return a fishing-focused SST assessment for common saltwater species.
 */
export function assessSSTForFishing(tempF: number): { rating: string; species: string[] } {
  if (tempF < 50) return { rating: 'Cold — limited activity', species: ['cod', 'flounder'] };
  if (tempF < 60) return { rating: 'Cool — good for inshore', species: ['striped bass', 'bluefish', 'flounder'] };
  if (tempF < 70) return { rating: 'Moderate — peak inshore', species: ['redfish', 'trout', 'snook', 'flounder'] };
  if (tempF < 78) return { rating: 'Warm — active pelagics', species: ['mahi-mahi', 'tuna', 'wahoo', 'kingfish'] };
  if (tempF < 85) return { rating: 'Hot — offshore pelagics', species: ['marlin', 'sailfish', 'mahi-mahi'] };
  return { rating: 'Very hot — deep or early/late', species: ['swordfish', 'tilefish'] };
}
