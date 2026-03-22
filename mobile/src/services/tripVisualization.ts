/**
 * OpenCatch — Trip Visualization Service
 *
 * Preview trips with predicted conditions, record actual GPS tracks,
 * compare planned vs actual routes, and generate trip summaries.
 * Core Navionics competitor feature.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import {
  calculateDistance,
  calculateBearing,
  type NavCoord,
} from './nauticalNav';
import { formatDuration } from './fuelCalculator';
import type { Route, LatLng, RouteMetrics } from './routePlanner';

// ── Types ────────────────────────────────────────────────────────────────────

export interface WeatherAtPoint {
  lat: number;
  lon: number;
  /** Distance along route in nm */
  distanceAlongNm: number;
  /** Air temperature in F */
  tempF: number;
  /** Wind speed in mph */
  windMph: number;
  /** Wind direction label */
  windDirection: string;
  /** Wind gust in mph */
  gustMph: number;
  /** Wave height in feet (coastal only) */
  waveHeightFt: number | null;
  /** Weather description */
  weather: string;
  /** Weather icon identifier */
  weatherIcon: string;
  /** Visibility in miles */
  visibilityMi: number;
  /** Precipitation probability % */
  precipPct: number;
}

export interface TideAtPoint {
  lat: number;
  lon: number;
  distanceAlongNm: number;
  /** Tide level relative to MLLW in feet */
  tideLevelFt: number;
  /** Tide state */
  tideState: 'rising' | 'falling' | 'high' | 'low' | 'unknown';
  /** Current speed in knots */
  currentSpeedKnots: number | null;
  /** Current direction in degrees */
  currentDirectionDeg: number | null;
}

export interface TripPreview {
  route: Route;
  departureTime: string; // ISO-8601
  /** Weather conditions at sample points along the route */
  weatherAlongRoute: WeatherAtPoint[];
  /** Tide conditions at sample points (coastal routes) */
  tidesAlongRoute: TideAtPoint[];
  /** Overall trip weather summary */
  weatherSummary: string;
  /** Safety advisories */
  advisories: string[];
  /** Predicted arrival time */
  predictedArrival: string;
}

export interface TripTrackPoint {
  lat: number;
  lon: number;
  timestamp: number;
  speedKnots: number;
  heading: number;
  accuracy: number;
}

export interface RecordedTrip {
  id: string;
  routeId: string | null;
  name: string;
  startTime: number;
  endTime: number | null;
  /** Actual GPS track points */
  trackPoints: TripTrackPoint[];
  /** Is actively recording */
  isRecording: boolean;
  createdAt: number;
}

export interface PlannedVsActualComparison {
  /** Planned route polyline */
  plannedPoints: LatLng[];
  /** Actual track polyline */
  actualPoints: LatLng[];
  /** Maximum deviation from planned route in nm */
  maxDeviationNm: number;
  /** Average deviation from planned route in nm */
  avgDeviationNm: number;
  /** Points where actual deviated significantly (>0.1nm) */
  deviationPoints: Array<{
    actual: LatLng;
    nearestPlanned: LatLng;
    deviationNm: number;
  }>;
}

export interface TripSummary {
  tripId: string;
  name: string;
  /** Total distance covered in nm */
  distanceCoveredNm: number;
  distanceCoveredMi: number;
  /** Duration */
  durationHours: number;
  durationLabel: string;
  /** Speeds */
  avgSpeedKnots: number;
  maxSpeedKnots: number;
  avgSpeedMph: number;
  maxSpeedMph: number;
  /** Fuel estimate based on time at speed */
  estimatedFuelGallons: number;
  /** Start/end timestamps */
  startTime: string;
  endTime: string;
  /** Start/end positions */
  startPosition: LatLng;
  endPosition: LatLng;
}

export interface ConditionTimelineEntry {
  timestamp: number;
  timeLabel: string;
  position: LatLng;
  tempF: number | null;
  windMph: number | null;
  weather: string | null;
  speedKnots: number;
}

// ── Storage ──────────────────────────────────────────────────────────────────

const TRIPS_KEY = '@opencatch/recorded_trips';
const ACTIVE_TRIP_KEY = '@opencatch/active_trip_recording';

// ── Weather Fetching ─────────────────────────────────────────────────────────

/**
 * Fetch forecast weather at a specific point and time from Open-Meteo.
 */
async function fetchWeatherAtPoint(
  lat: number,
  lon: number,
  targetTime: Date,
): Promise<WeatherAtPoint | null> {
  try {
    const dateStr = targetTime.toISOString().slice(0, 10);
    const hour = targetTime.getHours();

    const params = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      hourly: 'temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,weather_code,visibility,precipitation_probability',
      temperature_unit: 'fahrenheit',
      wind_speed_unit: 'mph',
      start_date: dateStr,
      end_date: dateStr,
      timezone: 'auto',
    });

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const res = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) return null;
    const data = await res.json();
    const hourly = data.hourly;
    if (!hourly) return null;

    const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    const windDirDeg = hourly.wind_direction_10m?.[hour] ?? 0;
    const windDir = dirs[Math.round(windDirDeg / 22.5) % 16];

    const weatherCode = hourly.weather_code?.[hour] ?? 0;
    let weather = 'Clear';
    let weatherIcon = 'sunny';
    if (weatherCode <= 1) { weather = 'Clear'; weatherIcon = 'sunny'; }
    else if (weatherCode <= 3) { weather = 'Partly Cloudy'; weatherIcon = 'partly-sunny'; }
    else if (weatherCode <= 48) { weather = 'Cloudy'; weatherIcon = 'cloudy'; }
    else if (weatherCode <= 67) { weather = 'Rain'; weatherIcon = 'rainy'; }
    else if (weatherCode <= 77) { weather = 'Snow'; weatherIcon = 'snow'; }
    else if (weatherCode >= 95) { weather = 'Thunderstorm'; weatherIcon = 'thunderstorm'; }
    else { weather = 'Showers'; weatherIcon = 'rainy'; }

    return {
      lat,
      lon,
      distanceAlongNm: 0, // Set by caller
      tempF: Math.round(hourly.temperature_2m?.[hour] ?? 70),
      windMph: Math.round(hourly.wind_speed_10m?.[hour] ?? 0),
      windDirection: windDir,
      gustMph: Math.round(hourly.wind_gusts_10m?.[hour] ?? 0),
      waveHeightFt: null, // Would come from marine forecast API
      weather,
      weatherIcon,
      visibilityMi: Math.round((hourly.visibility?.[hour] ?? 10000) / 1609),
      precipPct: hourly.precipitation_probability?.[hour] ?? 0,
    };
  } catch {
    return null;
  }
}

// ── Trip Preview ─────────────────────────────────────────────────────────────

/**
 * Preview a trip with predicted conditions along the route at planned departure time.
 */
export async function previewTrip(
  route: Route,
  departureTime: string,
): Promise<TripPreview> {
  const departure = new Date(departureTime);
  const weatherAlongRoute: WeatherAtPoint[] = [];
  const tidesAlongRoute: TideAtPoint[] = [];
  const advisories: string[] = [];

  // Sample weather at key points along the route:
  // start, every ~5nm, each waypoint, and end
  let cumulativeDistNm = 0;
  const samplePoints: Array<{ lat: number; lon: number; distNm: number }> = [];

  // Add start point
  if (route.waypoints.length > 0) {
    samplePoints.push({
      lat: route.waypoints[0].position.lat,
      lon: route.waypoints[0].position.lon,
      distNm: 0,
    });
  }

  // Add intermediate waypoints and sampled segments
  for (const segment of route.segments) {
    cumulativeDistNm += segment.distanceNm;

    // Add waypoint positions
    samplePoints.push({
      lat: segment.to.lat,
      lon: segment.to.lon,
      distNm: cumulativeDistNm,
    });
  }

  // Fetch weather at each sample point
  const totalDistNm = cumulativeDistNm;
  const cruiseSpeed = route.metrics?.cruiseSpeedKnots ?? 20;

  for (const sample of samplePoints) {
    const travelHours = cruiseSpeed > 0 ? sample.distNm / cruiseSpeed : 0;
    const pointTime = new Date(departure.getTime() + travelHours * 3600000);

    const weather = await fetchWeatherAtPoint(sample.lat, sample.lon, pointTime);
    if (weather) {
      weather.distanceAlongNm = sample.distNm;
      weatherAlongRoute.push(weather);

      // Check for advisories
      if (weather.windMph > 25) {
        advisories.push(`Strong winds (${weather.windMph} mph) expected at ${sample.distNm.toFixed(1)}nm mark`);
      }
      if (weather.precipPct > 70) {
        advisories.push(`High precipitation probability (${weather.precipPct}%) at ${sample.distNm.toFixed(1)}nm mark`);
      }
      if (weather.visibilityMi < 3) {
        advisories.push(`Reduced visibility (${weather.visibilityMi} mi) expected at ${sample.distNm.toFixed(1)}nm mark`);
      }
    }
  }

  // Build weather summary
  const temps = weatherAlongRoute.map((w) => w.tempF);
  const winds = weatherAlongRoute.map((w) => w.windMph);
  const maxWind = winds.length > 0 ? Math.max(...winds) : 0;
  const avgTemp = temps.length > 0 ? Math.round(temps.reduce((a, b) => a + b, 0) / temps.length) : 0;
  const weatherTypes = [...new Set(weatherAlongRoute.map((w) => w.weather))];

  let weatherSummary = `${weatherTypes.join(', ')} | ${avgTemp}°F avg`;
  if (maxWind > 15) {
    weatherSummary += ` | Winds up to ${maxWind} mph`;
  }

  // Predicted arrival
  const arrivalHours = cruiseSpeed > 0 ? totalDistNm / cruiseSpeed : 0;
  const predictedArrival = new Date(departure.getTime() + arrivalHours * 3600000).toISOString();

  return {
    route,
    departureTime,
    weatherAlongRoute,
    tidesAlongRoute,
    weatherSummary,
    advisories,
    predictedArrival,
  };
}

// ── Trip Recording ───────────────────────────────────────────────────────────

/**
 * Start recording a trip track.
 */
export async function recordTripTrack(
  routeId: string | null,
  name?: string,
): Promise<RecordedTrip> {
  const trip: RecordedTrip = {
    id: `trip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    routeId,
    name: name ?? `Trip ${new Date().toLocaleDateString()}`,
    startTime: Date.now(),
    endTime: null,
    trackPoints: [],
    isRecording: true,
    createdAt: Date.now(),
  };

  await AsyncStorage.setItem(ACTIVE_TRIP_KEY, JSON.stringify(trip));
  return trip;
}

/**
 * Add a track point to the active recording.
 */
export async function addTrackPoint(point: TripTrackPoint): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(ACTIVE_TRIP_KEY);
    if (!raw) return;
    const trip: RecordedTrip = JSON.parse(raw);
    if (!trip.isRecording) return;

    trip.trackPoints.push(point);
    await AsyncStorage.setItem(ACTIVE_TRIP_KEY, JSON.stringify(trip));
  } catch {
    // Silently fail — don't interrupt recording
  }
}

/**
 * Stop recording and save the trip.
 */
export async function stopRecording(): Promise<RecordedTrip | null> {
  try {
    const raw = await AsyncStorage.getItem(ACTIVE_TRIP_KEY);
    if (!raw) return null;

    const trip: RecordedTrip = JSON.parse(raw);
    trip.isRecording = false;
    trip.endTime = Date.now();

    // Save to trips list
    const trips = await getRecordedTrips();
    trips.push(trip);
    await AsyncStorage.setItem(TRIPS_KEY, JSON.stringify(trips));

    // Clear active recording
    await AsyncStorage.removeItem(ACTIVE_TRIP_KEY);

    return trip;
  } catch {
    return null;
  }
}

/**
 * Get the currently active recording, if any.
 */
export async function getActiveRecording(): Promise<RecordedTrip | null> {
  try {
    const raw = await AsyncStorage.getItem(ACTIVE_TRIP_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Get all recorded trips.
 */
export async function getRecordedTrips(): Promise<RecordedTrip[]> {
  try {
    const raw = await AsyncStorage.getItem(TRIPS_KEY);
    if (!raw) return [];
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

// ── Planned vs Actual Comparison ─────────────────────────────────────────────

/**
 * Compare planned route against actual GPS track.
 */
export function comparePlannedVsActual(
  planned: Route,
  actual: RecordedTrip,
): PlannedVsActualComparison {
  // Build planned polyline from waypoints
  const plannedPoints: LatLng[] = planned.waypoints.map((wp) => wp.position);

  // Build actual polyline from track points
  const actualPoints: LatLng[] = actual.trackPoints.map((tp) => ({
    lat: tp.lat,
    lon: tp.lon,
  }));

  // Calculate deviations
  const deviationPoints: PlannedVsActualComparison['deviationPoints'] = [];
  let totalDeviation = 0;
  let maxDeviation = 0;

  for (const actualPt of actualPoints) {
    // Find nearest point on planned route
    let minDist = Infinity;
    let nearestPt: LatLng = plannedPoints[0] ?? { lat: 0, lon: 0 };

    // Check distance to each segment of the planned route
    for (let i = 0; i < plannedPoints.length - 1; i++) {
      const segStart = plannedPoints[i];
      const segEnd = plannedPoints[i + 1];

      // Project actual point onto segment and find distance
      const dist = pointToSegmentDistance(actualPt, segStart, segEnd);
      if (dist < minDist) {
        minDist = dist;
        nearestPt = nearestPointOnSegment(actualPt, segStart, segEnd);
      }
    }

    totalDeviation += minDist;
    if (minDist > maxDeviation) maxDeviation = minDist;

    if (minDist > 0.1) {
      deviationPoints.push({
        actual: actualPt,
        nearestPlanned: nearestPt,
        deviationNm: Math.round(minDist * 100) / 100,
      });
    }
  }

  const avgDeviation = actualPoints.length > 0 ? totalDeviation / actualPoints.length : 0;

  return {
    plannedPoints,
    actualPoints,
    maxDeviationNm: Math.round(maxDeviation * 100) / 100,
    avgDeviationNm: Math.round(avgDeviation * 100) / 100,
    deviationPoints,
  };
}

/**
 * Distance from a point to a line segment in nm.
 */
function pointToSegmentDistance(point: LatLng, segA: LatLng, segB: LatLng): number {
  const nearest = nearestPointOnSegment(point, segA, segB);
  return calculateDistance(
    { lat: point.lat, lon: point.lon },
    { lat: nearest.lat, lon: nearest.lon },
  );
}

/**
 * Find the nearest point on a segment to a given point.
 */
function nearestPointOnSegment(point: LatLng, segA: LatLng, segB: LatLng): LatLng {
  const dx = segB.lon - segA.lon;
  const dy = segB.lat - segA.lat;
  const lenSq = dx * dx + dy * dy;

  if (lenSq === 0) return segA; // Segment is a point

  let t = ((point.lon - segA.lon) * dx + (point.lat - segA.lat) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));

  return {
    lat: segA.lat + t * dy,
    lon: segA.lon + t * dx,
  };
}

// ── Trip Summary ─────────────────────────────────────────────────────────────

/**
 * Generate a summary of a recorded trip.
 */
export function getTripSummary(trip: RecordedTrip): TripSummary {
  const points = trip.trackPoints;

  // Calculate total distance
  let totalDistNm = 0;
  for (let i = 1; i < points.length; i++) {
    totalDistNm += calculateDistance(
      { lat: points[i - 1].lat, lon: points[i - 1].lon },
      { lat: points[i].lat, lon: points[i].lon },
    );
  }

  // Duration
  const startTime = trip.startTime;
  const endTime = trip.endTime ?? Date.now();
  const durationHours = (endTime - startTime) / 3600000;

  // Speeds
  const speeds = points.map((p) => p.speedKnots).filter((s) => s > 0);
  const avgSpeedKnots = speeds.length > 0
    ? speeds.reduce((a, b) => a + b, 0) / speeds.length
    : 0;
  const maxSpeedKnots = speeds.length > 0 ? Math.max(...speeds) : 0;

  // Fuel estimate (rough: based on time and average consumption)
  const estimatedFuelGallons = durationHours * 5; // ~5 GPH default estimate

  const startPos = points.length > 0
    ? { lat: points[0].lat, lon: points[0].lon }
    : { lat: 0, lon: 0 };
  const endPos = points.length > 0
    ? { lat: points[points.length - 1].lat, lon: points[points.length - 1].lon }
    : { lat: 0, lon: 0 };

  return {
    tripId: trip.id,
    name: trip.name,
    distanceCoveredNm: Math.round(totalDistNm * 100) / 100,
    distanceCoveredMi: Math.round(totalDistNm * 1.15078 * 100) / 100,
    durationHours,
    durationLabel: formatDuration(durationHours),
    avgSpeedKnots: Math.round(avgSpeedKnots * 10) / 10,
    maxSpeedKnots: Math.round(maxSpeedKnots * 10) / 10,
    avgSpeedMph: Math.round(avgSpeedKnots * 1.15078 * 10) / 10,
    maxSpeedMph: Math.round(maxSpeedKnots * 1.15078 * 10) / 10,
    estimatedFuelGallons: Math.round(estimatedFuelGallons * 10) / 10,
    startTime: new Date(startTime).toISOString(),
    endTime: new Date(endTime).toISOString(),
    startPosition: startPos,
    endPosition: endPos,
  };
}

/**
 * Build a conditions timeline from a recorded trip.
 * Samples the track at regular intervals and estimates conditions.
 */
export function buildConditionsTimeline(
  trip: RecordedTrip,
  intervalMinutes: number = 30,
): ConditionTimelineEntry[] {
  const entries: ConditionTimelineEntry[] = [];
  const points = trip.trackPoints;
  if (points.length === 0) return entries;

  const intervalMs = intervalMinutes * 60000;
  let nextSampleTime = points[0].timestamp;

  for (const point of points) {
    if (point.timestamp >= nextSampleTime) {
      const time = new Date(point.timestamp);
      const timeLabel = time.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

      entries.push({
        timestamp: point.timestamp,
        timeLabel,
        position: { lat: point.lat, lon: point.lon },
        tempF: null, // Would be filled by weather data
        windMph: null,
        weather: null,
        speedKnots: point.speedKnots,
      });

      nextSampleTime = point.timestamp + intervalMs;
    }
  }

  return entries;
}

/**
 * Delete a recorded trip.
 */
export async function deleteRecordedTrip(tripId: string): Promise<void> {
  const trips = await getRecordedTrips();
  const filtered = trips.filter((t) => t.id !== tripId);
  await AsyncStorage.setItem(TRIPS_KEY, JSON.stringify(filtered));
}
