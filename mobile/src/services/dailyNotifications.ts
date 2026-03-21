/**
 * OpenCatch — Daily Notifications Service
 *
 * Schedule local push notifications for:
 * - Morning brief (daily bite rating + best time + weather)
 * - Trip reminders (night before a planned trip)
 * - Condition alerts (when a saved location exceeds threshold)
 *
 * Uses expo-notifications for local push notifications.
 * Falls back gracefully if expo-notifications is not installed.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import type { PlannedTrip } from './tripPlanner';

// ── Lazy load expo-notifications to avoid crash if not linked ────────────────

let Notifications: typeof import('expo-notifications') | null = null;

async function getNotifications() {
  if (Notifications) return Notifications;
  try {
    Notifications = require('expo-notifications');
    return Notifications;
  } catch {
    return null;
  }
}

// ── Storage Keys ─────────────────────────────────────────────────────────────

const PREFS_KEY = '@opencatch_notification_prefs';

export interface NotificationPrefs {
  morningBrief: boolean;
  tripReminders: boolean;
  conditionAlerts: boolean;
  morningBriefLat: number;
  morningBriefLon: number;
  conditionThreshold: number; // score 0-100
  conditionLocations: Array<{
    name: string;
    lat: number;
    lon: number;
  }>;
}

const DEFAULT_PREFS: NotificationPrefs = {
  morningBrief: false,
  tripReminders: true,
  conditionAlerts: false,
  morningBriefLat: 0,
  morningBriefLon: 0,
  conditionThreshold: 70,
  conditionLocations: [],
};

// ── Preferences ──────────────────────────────────────────────────────────────

export async function getNotificationPrefs(): Promise<NotificationPrefs> {
  try {
    const raw = await AsyncStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export async function updateNotificationPrefs(
  patch: Partial<NotificationPrefs>,
): Promise<NotificationPrefs> {
  const current = await getNotificationPrefs();
  const updated = { ...current, ...patch };
  await AsyncStorage.setItem(PREFS_KEY, JSON.stringify(updated));
  return updated;
}

// ── Permission Request ───────────────────────────────────────────────────────

export async function requestNotificationPermissions(): Promise<boolean> {
  const Notif = await getNotifications();
  if (!Notif) return false;

  try {
    const { status: existing } = await Notif.getPermissionsAsync();
    if (existing === 'granted') return true;

    const { status } = await Notif.requestPermissionsAsync();
    return status === 'granted';
  } catch {
    return false;
  }
}

// ── Schedule Morning Brief ───────────────────────────────────────────────────

export async function scheduleMorningBrief(lat: number, lon: number): Promise<string | null> {
  const Notif = await getNotifications();
  if (!Notif) return null;

  const granted = await requestNotificationPermissions();
  if (!granted) return null;

  // Cancel existing morning briefs first
  await cancelMorningBrief();

  // Save the location for morning brief
  await updateNotificationPrefs({ morningBrief: true, morningBriefLat: lat, morningBriefLon: lon });

  try {
    // Fetch today's conditions to compose notification content
    const weather = await fetchBriefWeather(lat, lon);

    const id = await Notif.scheduleNotificationAsync({
      content: {
        title: 'OpenCatch Morning Brief',
        body: weather
          ? `${weather.label} today. High ${weather.highTemp}\u00B0F, wind ${weather.windSpeed} mph. ${weather.tip}`
          : 'Check today\'s fishing conditions in OpenCatch.',
        data: { type: 'morning_brief', lat, lon },
        sound: true,
      },
      trigger: {
        type: Notif.SchedulableTriggerInputTypes.DAILY,
        hour: 6,
        minute: 0,
      },
    });

    return id;
  } catch (err) {
    console.warn('[Notifications] Failed to schedule morning brief:', err);
    return null;
  }
}

export async function cancelMorningBrief(): Promise<void> {
  const Notif = await getNotifications();
  if (!Notif) return;

  try {
    const scheduled = await Notif.getAllScheduledNotificationsAsync();
    for (const n of scheduled) {
      if ((n.content.data as any)?.type === 'morning_brief') {
        await Notif.cancelScheduledNotificationAsync(n.identifier);
      }
    }
    await updateNotificationPrefs({ morningBrief: false });
  } catch {
    // Silent
  }
}

// ── Schedule Trip Reminder ───────────────────────────────────────────────────

export async function scheduleTripReminder(trip: PlannedTrip): Promise<string | null> {
  const Notif = await getNotifications();
  if (!Notif) return null;

  const granted = await requestNotificationPermissions();
  if (!granted) return null;

  try {
    const tripDate = new Date(trip.date);
    const reminderDate = new Date(tripDate.getTime() - trip.notifyBefore * 60 * 60 * 1000);

    // Don't schedule if reminder is in the past
    if (reminderDate.getTime() <= Date.now()) return null;

    let body = `Your trip to ${trip.locationName} is ${trip.notifyBefore >= 24 ? 'tomorrow' : `in ${trip.notifyBefore} hours`}.`;
    if (trip.forecastSummary) {
      body += ` Forecast: ${trip.forecastSummary.weatherLabel}, ${trip.forecastSummary.highTemp}\u00B0F.`;
    }

    const id = await Notif.scheduleNotificationAsync({
      content: {
        title: 'Trip Reminder',
        body,
        data: { type: 'trip_reminder', tripId: trip.id },
        sound: true,
      },
      trigger: {
        type: Notif.SchedulableTriggerInputTypes.DATE,
        date: reminderDate,
      },
    });

    return id;
  } catch (err) {
    console.warn('[Notifications] Failed to schedule trip reminder:', err);
    return null;
  }
}

export async function cancelTripReminder(tripId: string): Promise<void> {
  const Notif = await getNotifications();
  if (!Notif) return;

  try {
    const scheduled = await Notif.getAllScheduledNotificationsAsync();
    for (const n of scheduled) {
      if ((n.content.data as any)?.tripId === tripId) {
        await Notif.cancelScheduledNotificationAsync(n.identifier);
      }
    }
  } catch {
    // Silent
  }
}

// ── Schedule Condition Alert ─────────────────────────────────────────────────

export async function scheduleConditionAlert(
  lat: number,
  lon: number,
  locationName: string,
  threshold: number = 70,
): Promise<void> {
  const prefs = await getNotificationPrefs();
  const locations = prefs.conditionLocations.filter(
    (l) => !(Math.abs(l.lat - lat) < 0.01 && Math.abs(l.lon - lon) < 0.01),
  );
  locations.push({ name: locationName, lat, lon });

  await updateNotificationPrefs({
    conditionAlerts: true,
    conditionThreshold: threshold,
    conditionLocations: locations,
  });
}

export async function removeConditionAlert(lat: number, lon: number): Promise<void> {
  const prefs = await getNotificationPrefs();
  const locations = prefs.conditionLocations.filter(
    (l) => !(Math.abs(l.lat - lat) < 0.01 && Math.abs(l.lon - lon) < 0.01),
  );
  await updateNotificationPrefs({ conditionLocations: locations });
}

/**
 * Check all condition-alert locations and fire notifications for those above threshold.
 * This should be called periodically (e.g., via a background task or app foreground).
 */
export async function checkAndFireConditionAlerts(): Promise<void> {
  const Notif = await getNotifications();
  if (!Notif) return;

  const prefs = await getNotificationPrefs();
  if (!prefs.conditionAlerts || prefs.conditionLocations.length === 0) return;

  const granted = await requestNotificationPermissions();
  if (!granted) return;

  for (const loc of prefs.conditionLocations) {
    try {
      const weather = await fetchBriefWeather(loc.lat, loc.lon);
      if (!weather) continue;

      // Simple heuristic score: low wind, moderate temp, no precipitation = good
      const score = computeSimpleScore(weather);
      if (score >= prefs.conditionThreshold) {
        await Notif.scheduleNotificationAsync({
          content: {
            title: 'Great Conditions Alert!',
            body: `Excellent conditions at ${loc.name} right now! ${weather.label}, ${weather.highTemp}\u00B0F, wind ${weather.windSpeed} mph.`,
            data: { type: 'condition_alert', lat: loc.lat, lon: loc.lon },
            sound: true,
          },
          trigger: null, // Fire immediately
        });
      }
    } catch {
      // Skip this location
    }
  }
}

// ── Cancel All ───────────────────────────────────────────────────────────────

export async function cancelAllNotifications(): Promise<void> {
  const Notif = await getNotifications();
  if (!Notif) return;
  try {
    await Notif.cancelAllScheduledNotificationsAsync();
  } catch {
    // Silent
  }
}

// ── Weather Helper ───────────────────────────────────────────────────────────

interface BriefWeather {
  highTemp: number;
  lowTemp: number;
  windSpeed: number;
  precipChance: number;
  weatherCode: number;
  label: string;
  tip: string;
}

async function fetchBriefWeather(lat: number, lon: number): Promise<BriefWeather | null> {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const url =
      `https://api.open-meteo.com/v1/forecast` +
      `?latitude=${lat}&longitude=${lon}` +
      `&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max,precipitation_probability_max,weather_code` +
      `&temperature_unit=fahrenheit&wind_speed_unit=mph` +
      `&start_date=${today}&end_date=${today}` +
      `&timezone=auto`;

    const res = await fetch(url);
    if (!res.ok) return null;

    const data = await res.json();
    const d = data.daily;
    if (!d?.temperature_2m_max?.length) return null;

    const code = d.weather_code[0];
    const windSpeed = d.wind_speed_10m_max[0];
    const highTemp = d.temperature_2m_max[0];

    let tip = 'Good day to fish!';
    if (code >= 95) tip = 'Storms expected. Stay safe.';
    else if (code >= 71) tip = 'Snow in the forecast.';
    else if (code >= 61) tip = 'Rain likely. Pack rain gear.';
    else if (windSpeed > 20) tip = 'Windy. Try sheltered spots.';
    else if (highTemp > 90) tip = 'Hot day. Fish early.';
    else if (highTemp < 32) tip = 'Freezing. Bundle up.';

    return {
      highTemp,
      lowTemp: d.temperature_2m_min[0],
      windSpeed,
      precipChance: d.precipitation_probability_max[0],
      weatherCode: code,
      label: weatherCodeLabel(code),
      tip,
    };
  } catch {
    return null;
  }
}

function weatherCodeLabel(code: number): string {
  if (code === 0) return 'Clear';
  if (code <= 3) return 'Partly cloudy';
  if (code <= 48) return 'Foggy';
  if (code <= 67) return 'Rainy';
  if (code <= 77) return 'Snowy';
  if (code <= 82) return 'Showers';
  if (code >= 95) return 'Stormy';
  return 'Mixed';
}

function computeSimpleScore(weather: BriefWeather): number {
  let score = 70; // baseline

  // Wind penalty
  if (weather.windSpeed > 25) score -= 30;
  else if (weather.windSpeed > 15) score -= 15;
  else if (weather.windSpeed < 10) score += 10;

  // Precip penalty
  if (weather.precipChance > 70) score -= 20;
  else if (weather.precipChance > 40) score -= 10;

  // Temp bonuses/penalties
  if (weather.highTemp >= 55 && weather.highTemp <= 80) score += 15;
  else if (weather.highTemp > 95 || weather.highTemp < 25) score -= 20;

  // Weather code bonuses
  if (weather.weatherCode === 0) score += 5; // clear
  if (weather.weatherCode >= 95) score -= 30; // storm

  return Math.max(0, Math.min(100, score));
}
