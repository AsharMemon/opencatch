/**
 * OpenCatch — Trip Planner Service
 *
 * Plan fishing trips with weather forecasts, alerts, and reminders.
 * Stores trips in AsyncStorage and fetches weather from Open-Meteo.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export interface PlannedTrip {
  id: string;
  locationName: string;
  lat: number;
  lon: number;
  date: string; // ISO string
  notes: string;
  notifyBefore: number; // hours before trip to send reminder
  weatherChecked: boolean;
  forecastSummary: TripForecast | null;
}

export interface TripForecast {
  highTemp: number;
  lowTemp: number;
  windSpeed: number;
  windGusts: number;
  precipChance: number;
  weatherCode: number;
  weatherLabel: string;
  updatedAt: string;
}

export interface TripAlert {
  tripId: string;
  locationName: string;
  date: string;
  alertType: 'snow' | 'storm' | 'extreme_cold' | 'extreme_heat' | 'high_wind' | 'heavy_rain';
  message: string;
  severity: 'warning' | 'danger';
}

// ── Storage Key ──────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch_planned_trips';

// ── WMO Weather Code Mapping ─────────────────────────────────────────────────

function weatherCodeToLabel(code: number): string {
  if (code === 0) return 'Clear sky';
  if (code <= 3) return 'Partly cloudy';
  if (code <= 48) return 'Fog';
  if (code <= 55) return 'Drizzle';
  if (code <= 57) return 'Freezing drizzle';
  if (code <= 65) return 'Rain';
  if (code <= 67) return 'Freezing rain';
  if (code <= 75) return 'Snow';
  if (code === 77) return 'Snow grains';
  if (code <= 82) return 'Rain showers';
  if (code <= 86) return 'Snow showers';
  if (code === 95) return 'Thunderstorm';
  if (code <= 99) return 'Thunderstorm with hail';
  return 'Unknown';
}

function isStormCode(code: number): boolean {
  return code >= 95;
}

function isSnowCode(code: number): boolean {
  return (code >= 71 && code <= 77) || (code >= 85 && code <= 86);
}

function isHeavyRainCode(code: number): boolean {
  return code === 65 || code === 67 || (code >= 80 && code <= 82);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function generateId(): string {
  return `trip_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// ── Core CRUD ────────────────────────────────────────────────────────────────

export async function savePlannedTrip(
  trip: Omit<PlannedTrip, 'id' | 'weatherChecked' | 'forecastSummary'>,
): Promise<PlannedTrip> {
  const trips = await getAllTrips();
  const newTrip: PlannedTrip = {
    ...trip,
    id: generateId(),
    weatherChecked: false,
    forecastSummary: null,
  };
  trips.push(newTrip);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(trips));
  return newTrip;
}

export async function getPlannedTrips(): Promise<PlannedTrip[]> {
  const trips = await getAllTrips();
  return trips.sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
}

export async function deletePlannedTrip(id: string): Promise<void> {
  const trips = await getAllTrips();
  const filtered = trips.filter((t) => t.id !== id);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
}

export async function getUpcomingTrips(): Promise<PlannedTrip[]> {
  const trips = await getPlannedTrips();
  const now = Date.now();
  const sevenDays = 7 * 24 * 60 * 60 * 1000;
  return trips.filter((t) => {
    const tripTime = new Date(t.date).getTime();
    return tripTime >= now && tripTime <= now + sevenDays;
  });
}

// ── Weather Fetching ─────────────────────────────────────────────────────────

export async function updateTripForecast(id: string): Promise<PlannedTrip | null> {
  const trips = await getAllTrips();
  const idx = trips.findIndex((t) => t.id === id);
  if (idx === -1) return null;

  const trip = trips[idx];
  const tripDate = trip.date.slice(0, 10); // YYYY-MM-DD

  try {
    const url =
      `https://api.open-meteo.com/v1/forecast` +
      `?latitude=${trip.lat}&longitude=${trip.lon}` +
      `&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,precipitation_probability_max,weather_code` +
      `&temperature_unit=fahrenheit&wind_speed_unit=mph` +
      `&start_date=${tripDate}&end_date=${tripDate}` +
      `&timezone=auto`;

    const res = await fetch(url);
    if (!res.ok) throw new Error(`Open-Meteo ${res.status}`);

    const data = await res.json();
    const daily = data.daily;

    if (!daily || !daily.temperature_2m_max?.length) {
      return trip; // No data for this date
    }

    const forecast: TripForecast = {
      highTemp: daily.temperature_2m_max[0],
      lowTemp: daily.temperature_2m_min[0],
      windSpeed: daily.wind_speed_10m_max[0],
      windGusts: daily.wind_gusts_10m_max[0],
      precipChance: daily.precipitation_probability_max[0],
      weatherCode: daily.weather_code[0],
      weatherLabel: weatherCodeToLabel(daily.weather_code[0]),
      updatedAt: new Date().toISOString(),
    };

    trips[idx] = { ...trip, forecastSummary: forecast, weatherChecked: true };
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(trips));
    return trips[idx];
  } catch (err) {
    console.warn('[TripPlanner] Failed to fetch forecast:', err);
    return trip;
  }
}

export async function updateAllTripForecasts(): Promise<void> {
  const trips = await getUpcomingTrips();
  await Promise.allSettled(trips.map((t) => updateTripForecast(t.id)));
}

// ── Alert Checking ───────────────────────────────────────────────────────────

export async function checkTripAlerts(): Promise<TripAlert[]> {
  const upcoming = await getUpcomingTrips();
  const alerts: TripAlert[] = [];

  // Make sure forecasts are up to date
  await Promise.allSettled(
    upcoming
      .filter((t) => !t.weatherChecked)
      .map((t) => updateTripForecast(t.id)),
  );

  // Re-read to get updated forecasts
  const refreshed = await getUpcomingTrips();

  for (const trip of refreshed) {
    const f = trip.forecastSummary;
    if (!f) continue;

    // Snow
    if (isSnowCode(f.weatherCode)) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'snow',
        message: `Snow expected at ${trip.locationName} — consider rescheduling`,
        severity: 'warning',
      });
    }

    // Thunderstorm
    if (isStormCode(f.weatherCode)) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'storm',
        message: `Thunderstorms forecast at ${trip.locationName} — dangerous conditions on the water`,
        severity: 'danger',
      });
    }

    // Heavy rain
    if (isHeavyRainCode(f.weatherCode)) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'heavy_rain',
        message: `Heavy rain expected at ${trip.locationName}`,
        severity: 'warning',
      });
    }

    // Extreme cold (below 20F)
    if (f.lowTemp < 20) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'extreme_cold',
        message: `Extreme cold (low ${f.lowTemp}°F) at ${trip.locationName} — dress warmly`,
        severity: 'warning',
      });
    }

    // Extreme heat (above 100F)
    if (f.highTemp > 100) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'extreme_heat',
        message: `Extreme heat (high ${f.highTemp}°F) at ${trip.locationName} — stay hydrated`,
        severity: 'warning',
      });
    }

    // High wind (above 25 mph)
    if (f.windSpeed > 25) {
      alerts.push({
        tripId: trip.id,
        locationName: trip.locationName,
        date: trip.date,
        alertType: 'high_wind',
        message: `High winds (${f.windSpeed} mph, gusts ${f.windGusts} mph) at ${trip.locationName}`,
        severity: f.windSpeed > 35 ? 'danger' : 'warning',
      });
    }
  }

  return alerts;
}

// ── Internal ─────────────────────────────────────────────────────────────────

async function getAllTrips(): Promise<PlannedTrip[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as PlannedTrip[];
  } catch {
    return [];
  }
}
