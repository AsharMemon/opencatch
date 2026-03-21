/**
 * OpenCatch API service layer.
 *
 * Calls the FastAPI backend first, and falls back to local mock data
 * when the server is unreachable. This lets the app work both online and
 * offline during development.
 *
 * Backend: FastAPI at localhost:8000 (castline/api/main.py)
 * All endpoints are prefixed with /api/v1 (configured in castline/api/config.py)
 */
import { mockLocations, mockBestLocations, defaultSettings, mockWaypoints } from '../data/mockData';
import { auth as authService } from './auth';
import { API_BASE_URL, TILE_BASE_URL, TILE_SERVER_DEPLOYED } from '../config/network';
import type {
  FishingLocation,
  PredictionResponse,
  ForecastDay,
  CatchReport,
  BestFishingLocation,
  UserSettings,
  Waypoint,
  PredictV2Response,
  ForecastV2Response,
  BestFishingV2Response,
  ConditionsV2Response,
  CatchReportV2Create,
  CatchReportV2Response,
  WeatherIcon,
} from '../types/models';

// ── Config ───────────────────────────────────────────────────────
const USE_MOCK = false;
const API_PREFIX = '/api/v1';

const BASE_URL = API_BASE_URL;

// Martin tile server base URL (for map overlays)
export const TILE_SERVER_URL = TILE_BASE_URL;
export { TILE_SERVER_DEPLOYED } from '../config/network';

/**
 * Build a TileJSON URL for a Martin layer.
 * Returns `null` when the tile server is not deployed (prod without
 * EXPO_PUBLIC_TILE_SERVER_URL) so callers can skip the fetch entirely.
 */
export function buildTileSourceUrl(layer: string): string | null {
  if (!TILE_SERVER_DEPLOYED) return null;
  return `${TILE_BASE_URL}/${layer}`;
}

/** @deprecated – use buildTileSourceUrl (points at Martin directly) */
export function buildApiTileTemplate(layer: string): string {
  return `${BASE_URL}${API_PREFIX}/tiles/${layer}/{z}/{x}/{y}.pbf`;
}
/** @deprecated – use buildTileSourceUrl (points at Martin directly) */
export function buildApiTileSourceUrl(layer: string): string {
  return `${BASE_URL}${API_PREFIX}/tiles/${layer}`;
}

// ── Request timeout & retry config ──────────────────────────────
const REQUEST_TIMEOUT_MS = 10_000;
const MAX_RETRIES = 1;

// ── Auth header helper ──────────────────────────────────────────
async function getAuthHeaders(): Promise<Record<string, string>> {
  try {
    const token = await authService.getValidToken();
    if (token) {
      return { Authorization: `Bearer ${token}` };
    }
  } catch {
    // Auth unavailable — proceed without token
  }
  return {};
}

// ── Token expired callback (set by App.tsx to trigger logout) ──
let _onTokenExpired: (() => void) | null = null;
export function setOnTokenExpired(cb: () => void) {
  _onTokenExpired = cb;
}

// ── HTTP helper with timeout + retry + auth ─────────────────────
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let lastError: Error | null = null;
  const authHeaders = await getAuthHeaders();

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

      const res = await fetch(`${BASE_URL}${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders,
          ...options?.headers,
        },
      });

      clearTimeout(timeoutId);

      // Handle 401 — try to refresh token once
      if (res.status === 401 && attempt === 0) {
        const newToken = await authService.refresh();
        if (newToken) {
          // Retry with new token
          const retryRes = await fetch(`${BASE_URL}${path}`, {
            ...options,
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${newToken}`,
              ...options?.headers,
            },
          });
          if (retryRes.ok) {
            return retryRes.json();
          }
        }
        // Refresh failed — notify app to show login screen
        if (_onTokenExpired) _onTokenExpired();
        throw new ApiError('Session expired. Please log in again.', 401);
      }

      if (!res.ok) {
        const errorBody = await res.text().catch(() => '');
        throw new ApiError(
          `API ${res.status}: ${path}${errorBody ? ` — ${errorBody.slice(0, 200)}` : ''}`,
          res.status,
        );
      }

      return res.json();
    } catch (err: any) {
      lastError = err;

      // Don't retry on 4xx client errors — only retry on network/timeout/5xx
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        throw err;
      }

      // If this was the last attempt, throw
      if (attempt === MAX_RETRIES) {
        throw err;
      }

      // Brief pause before retry
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  throw lastError ?? new Error('Request failed');
}

// ── Custom error class ──────────────────────────────────────────
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// ── Simulated latency for mock ───────────────────────────────────
function delay<T>(data: T, ms = 300): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), ms));
}

// ── Smart fallback: try API, fall back to mock on failure ────────
async function requestWithFallback<T>(
  path: string,
  fallback: T,
  options?: RequestInit,
): Promise<T> {
  if (USE_MOCK) return delay(fallback);
  try {
    return await request<T>(path, options);
  } catch (e) {
    console.warn('[OpenCatch] API unavailable, using mock data:', e);
    return fallback;
  }
}

// ── Backend location shape from GET /api/v1/locations ───────────
// FastAPI returns: {locations: [{name, event_count, mean_cpue, lat, lon, distance_km?}], total, limit, offset}
interface BackendLocation {
  name: string;
  event_count: number;
  mean_cpue: number;
  lat: number | null;
  lon: number | null;
  distance_km?: number;
}

interface BackendLocationsResponse {
  locations: BackendLocation[];
  total: number;
  limit: number;
  offset: number;
}

// ── Backend location detail shape from GET /api/v1/locations/{name} ──
interface BackendLocationDetail {
  name: string;
  event_count: number;
  mean_cpue: number;
  std_cpue: number;
  min_cpue: number;
  max_cpue: number;
  lat: number | null;
  lon: number | null;
  model_type: 'seen' | 'unseen';
}

// ── Adapter: backend location → mobile FishingLocation ──────────
function adaptBackendLocations(
  backendLocs: BackendLocation[],
): FishingLocation[] {
  // Build a lookup from mock data for enrichment
  const mockByName = new Map<string, FishingLocation>();
  for (const ml of mockLocations) {
    mockByName.set(ml.name.toLowerCase(), ml);
  }

  return backendLocs.map((bl, index) => {
    // Try to find a matching mock location by name substring match
    const nameLower = bl.name.toLowerCase();
    let mock: FishingLocation | undefined;
    mockByName.forEach((val, key) => {
      if (!mock && (nameLower.includes(key) || key.includes(nameLower.split(',')[0]))) {
        mock = val;
      }
    });

    const lat = bl.lat ?? mock?.lat ?? 0;
    const lon = bl.lon ?? mock?.lon ?? 0;
    // Derive a score from mean_cpue (rough heuristic: 3lb avg = 50 score)
    const cpueScore = Math.max(0, Math.min(100, Math.round(bl.mean_cpue * 16)));

    if (mock) {
      // Return the mock enriched with the backend name and real coordinates
      return {
        ...mock,
        id: `backend-${index}`,
        name: bl.name,
        lat,
        lon,
        score: cpueScore || mock.score,
        subtitle: `${bl.event_count} events · Avg ${bl.mean_cpue.toFixed(1)} lb`,
      };
    }

    // Synthesize a minimal FishingLocation from backend data
    return {
      id: `backend-${index}`,
      name: bl.name,
      subtitle: `${bl.event_count} events · Avg ${bl.mean_cpue.toFixed(1)} lb`,
      lat,
      lon,
      score: cpueScore,
      scoreBreakdown: {
        catchProbability: 50,
        cpue: cpueScore,
        conditions: 50,
        trophyPotential: 50,
      },
      conditions: {
        waterTemp: 65,
        airTemp: 72,
        weather: 'Partly Cloudy',
        weatherIcon: 'partly-cloudy',
        windSpeed: 8,
        windDirection: 'SW',
        pressure: 29.92,
        pressureTrend: 'steady',
        humidity: 55,
        moonPhase: 'Waxing Crescent',
        solunarRating: 'fair',
        sunrise: '6:30 AM',
        sunset: '7:45 PM',
      },
      explanation: `Historical average weight: ${bl.mean_cpue.toFixed(1)} lb across ${bl.event_count} events. Live conditions pending.`,
      forecast: [],
    };
  });
}

// ── Adapter: ConditionsV2Response → FishingLocation conditions enrichment ──
function enrichLocationFromConditions(
  loc: FishingLocation,
  cond: ConditionsV2Response,
): FishingLocation {
  const weatherIconMap: Record<string, WeatherIcon> = {
    clear: 'sunny',
    sunny: 'sunny',
    'partly cloudy': 'partly-cloudy',
    cloudy: 'cloudy',
    overcast: 'cloudy',
    rain: 'rainy',
    rainy: 'rainy',
    drizzle: 'rainy',
    storm: 'stormy',
    thunderstorm: 'stormy',
    snow: 'snowy',
    fog: 'foggy',
    windy: 'windy',
  };

  const condStr = (cond.weather.conditions ?? '').toLowerCase();
  const icon: WeatherIcon = weatherIconMap[condStr] ?? 'partly-cloudy';

  return {
    ...loc,
    score: Math.round(cond.score),
    conditions: {
      waterTemp: cond.water.temp_f ?? loc.conditions.waterTemp,
      airTemp: cond.weather.temp_f ?? loc.conditions.airTemp,
      weather: cond.weather.conditions ?? cond.label,
      weatherIcon: icon,
      windSpeed: cond.weather.wind_mph ?? loc.conditions.windSpeed,
      windDirection: cond.weather.wind_direction ?? loc.conditions.windDirection,
      pressure: cond.weather.pressure_mb
        ? cond.weather.pressure_mb / 33.8639 // mb to inHg
        : loc.conditions.pressure,
      pressureTrend: (cond.weather.pressure_trend as 'rising' | 'falling' | 'steady') ?? loc.conditions.pressureTrend,
      humidity: cond.weather.humidity_pct ?? loc.conditions.humidity,
      moonPhase: loc.conditions.moonPhase,
      solunarRating: loc.conditions.solunarRating,
      sunrise: loc.conditions.sunrise,
      sunset: loc.conditions.sunset,
    },
    explanation: cond.summary || loc.explanation,
    waterLevel: cond.water.level_ft != null
      ? {
          stationId: '',
          stationName: cond.location_name,
          distanceKm: 0,
          currentLevel: cond.water.level_ft ?? 0,
          levelChange24h: 0,
          levelTrend: (cond.water.level_trend as 'rising' | 'falling' | 'stable') ?? 'stable',
          flowCfs: cond.water.discharge_cfs,
          waterTemp: cond.water.temp_f,
          lastUpdated: cond.updated_at,
        }
      : loc.waterLevel,
  };
}

// ── Helper: find closest mock location to a lat/lon ─────────────
function closestMockLocation(lat: number, lon: number): FishingLocation {
  return mockLocations.reduce((best, loc) => {
    const dist = Math.abs(loc.lat - lat) + Math.abs(loc.lon - lon);
    const bestDist = Math.abs(best.lat - lat) + Math.abs(best.lon - lon);
    return dist < bestDist ? loc : best;
  });
}

// ── Mock prediction response from a location ────────────────────
function mockPredictionFromLocation(loc: FishingLocation): PredictionResponse {
  return {
    score: loc.score,
    breakdown: loc.scoreBreakdown,
    explanation: loc.explanation,
    conditions: loc.conditions,
  };
}

// ── Locations cache to avoid redundant fetches ──────────────────
let _locationsCache: FishingLocation[] | null = null;
let _locationsCacheTime = 0;
const LOCATIONS_CACHE_TTL = 60_000; // 1 minute
const LOCATIONS_PAGE_SIZE = 500;

// ── Public API ───────────────────────────────────────────────────
export const api = {
  /**
   * Get all fishing locations (for map pins).
   *
   * FastAPI: GET /api/v1/locations
   * Returns: {locations: [{name, event_count, mean_cpue, lat, lon}], total}
   */
  async getLocations(): Promise<FishingLocation[]> {
    if (USE_MOCK) return delay(mockLocations);

    // Return cache if fresh
    if (_locationsCache && Date.now() - _locationsCacheTime < LOCATIONS_CACHE_TTL) {
      return _locationsCache;
    }

    try {
      const allLocations: BackendLocation[] = [];
      let offset = 0;
      let total = Infinity;

      while (offset < total) {
        const data = await request<BackendLocationsResponse>(
          `/api/v1/locations?limit=${LOCATIONS_PAGE_SIZE}&offset=${offset}`,
        );

        allLocations.push(...data.locations);
        total = data.total;

        if (data.locations.length === 0) break;
        offset += data.locations.length;
      }

      const adapted = adaptBackendLocations(allLocations);

      // If backend returned locations but none have lat/lon, merge with mocks
      // so the map still renders pins
      if (adapted.length > 0 && adapted.every((l) => l.lat === 0 && l.lon === 0)) {
        console.warn('[OpenCatch] Backend locations lack coordinates, merging with mock data');
        _locationsCache = mockLocations;
        _locationsCacheTime = Date.now();
        return mockLocations;
      }

      const result = adapted.length > 0 ? adapted : mockLocations;
      _locationsCache = result;
      _locationsCacheTime = Date.now();
      return result;
    } catch (e) {
      console.warn('[OpenCatch] getLocations failed, using mock data:', e);
      return mockLocations;
    }
  },

  /**
   * Get a single location by ID.
   * If the location has real coordinates, also fetches live conditions to enrich it.
   */
  async getLocation(id: string): Promise<FishingLocation | undefined> {
    if (USE_MOCK) {
      const loc = mockLocations.find((l) => l.id === id);
      return delay(loc);
    }
    try {
      const allLocs = await api.getLocations();
      const loc = allLocs.find((l) => l.id === id);
      if (!loc) return mockLocations.find((l) => l.id === id);

      // Enrich with live conditions if we have coordinates
      if (loc.lat !== 0 && loc.lon !== 0) {
        try {
          const cond = await api.conditionsV2(loc.lat, loc.lon);
          return enrichLocationFromConditions(loc, cond);
        } catch {
          // Live conditions unavailable — return the base location
          return loc;
        }
      }
      return loc;
    } catch (e) {
      console.warn('[OpenCatch] getLocation failed, using mock data:', e);
      return mockLocations.find((l) => l.id === id);
    }
  },

  /**
   * Get fishing conditions for a lat/lon.
   *
   * FastAPI: GET /api/v1/conditions?lat=&lon=
   * Returns: ConditionsResponse (score, weather, water, species, etc.)
   * Adapted to legacy PredictionResponse shape for backward compat.
   */
  async getConditions(lat: number, lon: number): Promise<PredictionResponse> {
    const mockFallback = mockPredictionFromLocation(closestMockLocation(lat, lon));
    if (USE_MOCK) return delay(mockFallback);
    try {
      const cond = await request<ConditionsV2Response>(`/api/v1/conditions?lat=${lat}&lon=${lon}`);
      // Map ConditionsResponse → PredictionResponse for legacy consumers
      return {
        score: Math.round(cond.score),
        breakdown: {
          catchProbability: Math.round(cond.score * 0.3),
          cpue: Math.round(cond.score * 0.3),
          conditions: Math.round(cond.score * 0.25),
          trophyPotential: Math.round(cond.score * 0.15),
        },
        explanation: cond.summary,
        conditions: {
          waterTemp: cond.water.temp_f ?? 65,
          airTemp: cond.weather.temp_f ?? 72,
          weather: cond.label,
          weatherIcon: 'partly-cloudy' as const,
          windSpeed: cond.weather.wind_mph ?? 8,
          windDirection: cond.weather.wind_direction ?? 'SW',
          pressure: cond.weather.pressure_mb
            ? cond.weather.pressure_mb / 33.8639
            : 29.92,
          pressureTrend: (cond.weather.pressure_trend as 'rising' | 'falling' | 'steady') ?? 'steady',
          humidity: cond.weather.humidity_pct ?? 55,
          moonPhase: 'Waxing Crescent',
          solunarRating: 'fair' as const,
          sunrise: '6:30 AM',
          sunset: '7:45 PM',
        },
      };
    } catch (e) {
      console.warn('[OpenCatch] getConditions failed, using mock data:', e);
      return mockFallback;
    }
  },

  /**
   * Get fishing score prediction for a location/date.
   *
   * FastAPI: POST /api/v1/predict
   * Body: {location, date, usgs_site_id?}
   * Returns: PredictResponse
   * Adapted to legacy PredictionResponse shape.
   */
  async predict(lat: number, lon: number, date: string): Promise<PredictionResponse> {
    const mockFallback = mockPredictionFromLocation(closestMockLocation(lat, lon));
    if (USE_MOCK) return delay(mockFallback);
    try {
      // Find the closest known location name for the predict endpoint
      const closest = closestMockLocation(lat, lon);
      const pred = await request<PredictV2Response>('/api/v1/predict', {
        method: 'POST',
        body: JSON.stringify({ location: closest.name, date }),
      });
      return {
        score: pred.fishing_score,
        breakdown: {
          catchProbability: Math.round(pred.fishing_score * 0.3),
          cpue: Math.round(pred.fishing_score * 0.3),
          conditions: Math.round(pred.fishing_score * 0.25),
          trophyPotential: Math.round(pred.fishing_score * 0.15),
        },
        explanation: pred.explanation,
        conditions: mockFallback.conditions, // Use mock conditions as base
      };
    } catch (e) {
      console.warn('[OpenCatch] predict failed, using mock data:', e);
      return mockFallback;
    }
  },

  /**
   * Get hourly forecast for a location.
   *
   * FastAPI: GET /api/v1/conditions/forecast?lat=&lon=
   * Returns: ForecastResponse {location_name, lat, lon, forecast: ForecastPoint[], best_window}
   * Adapted to ForecastDay[] for backward compat with the chart component.
   */
  async getForecast(lat: number, lon: number): Promise<ForecastDay[]> {
    const closest = closestMockLocation(lat, lon);
    const mockFallback = closest.forecast;
    if (USE_MOCK) return delay(mockFallback);
    try {
      const resp = await request<{
        location_name: string;
        lat: number;
        lon: number;
        forecast: Array<{
          time: string;
          score: number;
          label: string;
          weather_summary: string | null;
        }>;
        best_window: string | null;
      }>(`/api/v1/conditions/forecast?lat=${lat}&lon=${lon}`);

      if (!resp.forecast || resp.forecast.length === 0) {
        return mockFallback;
      }

      // Group hourly ForecastPoints into daily ForecastDay summaries
      const dayMap = new Map<string, Array<typeof resp.forecast[0]>>();
      for (const pt of resp.forecast) {
        const dateStr = pt.time.slice(0, 10);
        if (!dayMap.has(dateStr)) dayMap.set(dateStr, []);
        dayMap.get(dateStr)!.push(pt);
      }

      const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
      const today = new Date().toISOString().slice(0, 10);
      const result: ForecastDay[] = [];

      for (const [dateStr, points] of dayMap) {
        const d = new Date(dateStr + 'T12:00:00');
        const avgScore = Math.round(
          points.reduce((sum, p) => sum + p.score, 0) / points.length,
        );
        const bestLabel = points.reduce(
          (best, p) => (p.score > best.score ? p : best),
          points[0],
        ).label;

        result.push({
          date: dateStr,
          dayLabel: dateStr === today ? 'Today' : days[d.getDay()],
          score: avgScore,
          highTemp: 72, // Not available from hourly forecast — placeholder
          lowTemp: 55,
          weather: bestLabel,
          weatherIcon: avgScore >= 60 ? 'sunny' : 'cloudy',
          windSpeed: 8,
          precipChance: avgScore < 40 ? 60 : 20,
        });
      }

      return result.length > 0 ? result.slice(0, 7) : mockFallback;
    } catch (e) {
      console.warn('[OpenCatch] getForecast failed, using mock data:', e);
      return mockFallback;
    }
  },

  /**
   * Get species probabilities for a location.
   *
   * FastAPI: GET /api/v1/species?lat=&lon=
   * Returns: SpeciesListResponse {lat, lon, species: SpeciesActivity[], dominant_group}
   */
  async getSpecies(lat: number, lon: number): Promise<any> {
    return requestWithFallback(
      `/api/v1/species?lat=${lat}&lon=${lon}`,
      { lat, lon, species: [], dominant_group: 'bass' },
    );
  },

  /**
   * Submit a catch report.
   *
   * FastAPI: POST /api/v1/catch-report
   * Body: CatchReportCreate {user_id, lat, lon, trip_start, trip_end, effort_hours, species, catch_count, kept_count, ...}
   */
  async submitCatchReport(report: CatchReport): Promise<{ success: boolean }> {
    const mockFallback = { success: true };
    if (USE_MOCK) {
      console.log('[OpenCatch] Catch report submitted (mock):', report);
      return delay(mockFallback, 500);
    }
    try {
      await request('/api/v1/catch-report', {
        method: 'POST',
        body: JSON.stringify({
          user_id: 'mobile-user',
          lat: report.lat,
          lon: report.lon,
          trip_start: report.date,
          trip_end: report.date,
          effort_hours: 4,
          species: report.species,
          catch_count: report.numberCaught,
          kept_count: report.numberKept,
          largest_weight_lb: report.largestWeight,
          rating: report.rating,
          notes: report.notes,
        }),
      });
      return { success: true };
    } catch (e) {
      console.warn('[OpenCatch] submitCatchReport failed, returning mock:', e);
      return mockFallback;
    }
  },

  /**
   * Get top fishing locations for a date.
   *
   * FastAPI: GET /api/v1/best-fishing?date=&top_n=
   * Returns: BestFishingResponse {date, top_locations: [...], total_evaluated}
   * Adapted to BestFishingLocation[] for legacy consumers.
   */
  async getBestFishing(date: string): Promise<BestFishingLocation[]> {
    if (USE_MOCK) return delay(mockBestLocations);
    try {
      const resp = await request<{
        date: string;
        top_locations: Array<{
          location: string;
          fishing_score: number;
          label?: string;
          predicted_weight_lb: number;
          historical_avg_lb: number;
        }>;
        total_evaluated: number;
      }>(`/api/v1/best-fishing?date=${date}&top_n=10`);

      return resp.top_locations.map((entry, i) => ({
        id: `best-${i}`,
        name: entry.location,
        lat: 0,
        lon: 0,
        score: entry.fishing_score,
        topReason: `${entry.predicted_weight_lb.toFixed(1)} lb predicted (avg ${entry.historical_avg_lb.toFixed(1)} lb)`,
      }));
    } catch (e) {
      console.warn('[OpenCatch] getBestFishing failed, using mock data:', e);
      return mockBestLocations;
    }
  },

  /** Get user settings. */
  async getSettings(): Promise<UserSettings> {
    // Settings are client-side only for now
    return delay({ ...defaultSettings });
  },

  /** Update user settings. */
  async updateSettings(settings: Partial<UserSettings>): Promise<UserSettings> {
    // Settings are client-side only for now
    return delay({ ...defaultSettings, ...settings });
  },

  /** Get all personal waypoints. */
  async getWaypoints(): Promise<Waypoint[]> {
    // Waypoints are client-side only for now
    return delay(mockWaypoints);
  },

  /** Save a new personal waypoint. */
  async saveWaypoint(waypoint: Omit<Waypoint, 'id' | 'createdAt' | 'catches'>): Promise<Waypoint> {
    // Waypoints are client-side only for now
    const newWp: Waypoint = {
      ...waypoint,
      id: `wp-${Date.now()}`,
      createdAt: new Date().toISOString(),
      catches: 0,
    };
    return delay(newWp, 300);
  },

  /** Delete a personal waypoint by ID. */
  async deleteWaypoint(id: string): Promise<void> {
    // Waypoints are client-side only for now
    await delay(undefined, 200);
  },

  /**
   * Health check — verifies the backend is reachable.
   *
   * FastAPI: GET /health
   * Returns: {status: "ok", model_loaded: bool, environment: string}
   */
  async healthCheck(): Promise<{ status: string; model_loaded?: boolean; environment?: string }> {
    return request('/health');
  },

  // ── V2 Endpoints (4-layer prediction system) ────────────────────

  /**
   * V2 Prediction — POST /api/v1/predict
   * Uses the 4-layer decision system (hydrology, weather, biology, history).
   *
   * FastAPI: POST /api/v1/predict
   * Body: {location, date, usgs_site_id?}
   * Returns: PredictResponse {fishing_score, confidence, model_version, breakdown, conditions, explanation}
   */
  async predictV2(
    location: string,
    date: string,
    usgsSiteId?: string,
  ): Promise<PredictV2Response> {
    // Fallback returns score 0 so the UI shows a loading/unavailable state
    // instead of a misleadingly specific hardcoded number
    const mockFallback: PredictV2Response = {
      fishing_score: 0,
      confidence: 0,
      model_version: 'v2-ensemble',
      breakdown: {
        fishing_score: 0,
        layers: {
          hydrology: {
            label: 'Water Conditions',
            description: 'Flow rate, water level, and temperature signals',
            contribution: 0,
            data_quality: 'estimated' as const,
          },
          weather: {
            label: 'Weather',
            description: 'Air temp, pressure, wind, precipitation outlook',
            contribution: 0,
            data_quality: 'estimated' as const,
          },
          biology: {
            label: 'Biological Activity',
            description: 'Seasonal patterns, spawn timing, forage availability',
            contribution: 0,
            data_quality: 'modeled' as const,
          },
          history: {
            label: 'Historical Performance',
            description: 'Past tournament and creel survey data for this location',
            contribution: 0,
            data_quality: 'historical' as const,
          },
        },
        predicted_weight_lb: 0,
      },
      conditions: {
        location,
        date,
        predicted_weight_lb: 0,
        historical_avg_lb: 0,
      },
      explanation: 'Score unavailable — connect to the server for real-time predictions.',
    };

    return requestWithFallback('/api/v1/predict', mockFallback, {
      method: 'POST',
      body: JSON.stringify({
        location,
        date,
        usgs_site_id: usgsSiteId ?? '',
      }),
    });
  },

  /**
   * V2 Forecast — GET /api/v1/forecast
   * 7-day fishing forecast for a named location.
   *
   * FastAPI: GET /api/v1/forecast?location=&usgs_site_id=&date=
   * Returns: ForecastV2Response {location, start_date, forecast: [{date, fishing_score, label, predicted_weight_lb, explanation}]}
   */
  async forecastV2(
    location: string,
    usgsSiteId?: string,
    date?: string,
  ): Promise<ForecastV2Response> {
    const mockFallback = (() => {
      const loc = mockLocations.find(
        (l) => l.name === location || l.id === location,
      ) ?? mockLocations[0];
      const today = new Date();
      const forecast = loc.forecast.map((f) => ({
        date: f.date,
        fishing_score: f.score,
        predicted_weight_lb: 2.0 + Math.random() * 3,
        explanation: f.score >= 70
          ? 'Good conditions expected.'
          : 'Fair conditions expected.',
      }));
      return {
        location: loc.name,
        start_date: today.toISOString().slice(0, 10),
        forecast,
      };
    })();

    const params = new URLSearchParams({ location });
    if (usgsSiteId) params.set('usgs_site_id', usgsSiteId);
    if (date) params.set('date', date);
    return requestWithFallback(
      `/api/v1/forecast?${params.toString()}`,
      mockFallback,
    );
  },

  /**
   * V2 Best Fishing — GET /api/v1/best-fishing
   * Rank all locations by composite score for a given date.
   *
   * FastAPI: GET /api/v1/best-fishing?date=&top_n=
   * Returns: BestFishingResponse {date, top_locations: [{location, fishing_score, label, predicted_weight_lb, historical_avg_lb}], total_evaluated}
   */
  async bestFishingV2(
    date: string,
    topN: number = 10,
  ): Promise<BestFishingV2Response> {
    const mockFallback = (() => {
      const sorted = [...mockLocations].sort((a, b) => b.score - a.score);
      return {
        date,
        top_locations: sorted.slice(0, topN).map((loc) => ({
          location: loc.name,
          fishing_score: loc.score,
          predicted_weight_lb: 2.5 + Math.random() * 3,
          historical_avg_lb: 3.0,
          explanation: loc.explanation.split('.')[0] + '.',
        })),
        total_evaluated: mockLocations.length,
      };
    })();

    return requestWithFallback(
      `/api/v1/best-fishing?date=${date}&top_n=${topN}`,
      mockFallback,
    );
  },

  /**
   * V2 Conditions — GET /api/v1/conditions
   * Real-time conditions for a lat/lon with weather + water data.
   *
   * FastAPI: GET /api/v1/conditions?lat=&lon=
   * Returns: ConditionsResponse {score, confidence, label, summary, location_name, weather, water, species, top_factors, updated_at}
   */
  async conditionsV2(lat: number, lon: number): Promise<ConditionsV2Response> {
    const mockFallback = (() => {
      const closest = closestMockLocation(lat, lon);
      return {
        score: closest.score,
        confidence: 'medium',
        label: closest.score >= 80 ? 'Excellent'
          : closest.score >= 60 ? 'Good'
          : closest.score >= 40 ? 'Fair'
          : 'Poor',
        summary: closest.explanation,
        location_name: closest.name,
        weather: {
          temp_f: closest.conditions.airTemp,
          wind_mph: closest.conditions.windSpeed,
          pressure_mb: closest.conditions.pressure * 33.8639, // inHg to mb
          pressure_trend: closest.conditions.pressureTrend,
          humidity_pct: closest.conditions.humidity,
        },
        water: {
          temp_f: closest.conditions.waterTemp,
          level_ft: closest.waterLevel?.currentLevel,
          level_trend: closest.waterLevel?.levelTrend,
          discharge_cfs: closest.waterLevel?.flowCfs,
          source: 'USGS',
        },
        species: (closest.speciesActivity ?? []).map((sp) => ({
          species: sp.species,
          probability: sp.confidence,
          activity_level: sp.activity === 'very-active' ? 'high'
            : sp.activity === 'active' ? 'high'
            : sp.activity === 'moderate' ? 'moderate'
            : 'low',
        })),
        top_factors: [
          `${closest.conditions.pressureTrend} pressure`,
          `Water temp ${closest.conditions.waterTemp}F`,
        ],
        updated_at: new Date().toISOString(),
      };
    })();

    return requestWithFallback(
      `/api/v1/conditions?lat=${lat}&lon=${lon}`,
      mockFallback,
    );
  },

  /**
   * V2 Catch Report — POST /api/v1/catch-report
   * Submit a catch report matching the CatchReportCreate schema.
   *
   * FastAPI: POST /api/v1/catch-report
   * Body: CatchReportCreate {user_id, lat, lon, trip_start, trip_end, effort_hours, species, catch_count, kept_count, ...}
   * Returns: CatchReportResponse {id, status}
   */
  async submitCatchReportV2(
    report: CatchReportV2Create,
  ): Promise<CatchReportV2Response> {
    const mockFallback = {
      id: Math.floor(Math.random() * 10000),
      status: 'created',
    };

    if (USE_MOCK) {
      console.log('[OpenCatch] V2 Catch report submitted (mock):', report);
      return delay(mockFallback, 500);
    }

    try {
      return await request('/api/v1/catch-report', {
        method: 'POST',
        body: JSON.stringify(report),
      });
    } catch (e) {
      console.warn('[OpenCatch] submitCatchReportV2 failed, returning mock:', e);
      return mockFallback;
    }
  },
};
