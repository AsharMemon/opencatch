/**
 * OpenCatch — Fishing Time Notification Service
 *
 * Pushes local notifications and in-app banners when optimal
 * fishing windows are active or approaching.
 *
 * Uses expo-notifications for local push notifications and
 * the bestTimeWindows service for solunar/weather-based scoring.
 *
 * Competitor parity: FishAngler "bite alerts", Fishbrain "BiteTime" push.
 * OpenCatch EXCEEDS by combining solunar + weather + species tips.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDailyBiteForecast, formatHour, type TimeWindow } from './bestTimeWindows';

// ── Types ────────────────────────────────────────────────────────────────────

export interface FishingBannerState {
  visible: boolean;
  message: string;
  biteRating: 'prime' | 'good' | 'fair' | 'none';
  timeRemaining: string;       // e.g. "2h 15m remaining"
  windowEnd: number;           // Unix ms
  score: number;               // 0-100
  species?: string;            // Suggested target species
  technique?: string;          // Suggested technique
}

export interface NotificationPayload {
  title: string;
  body: string;
  data: {
    type: 'fishing_window';
    lat: number;
    lon: number;
    windowStart: number;
    windowEnd: number;
    score: number;
  };
}

// ── Constants ────────────────────────────────────────────────────────────────

const NOTIFICATION_PREFS_KEY = '@opencatch/notification_prefs';
const LAST_NOTIFICATION_KEY = '@opencatch/last_notification';
const BANNER_DISMISSED_KEY = '@opencatch/banner_dismissed';

// Species activity patterns by time of day
const SPECIES_ACTIVITY: Record<string, { peakHours: number[]; technique: string }> = {
  'Largemouth Bass': { peakHours: [5, 6, 7, 8, 17, 18, 19, 20], technique: 'topwater near structure' },
  'Smallmouth Bass': { peakHours: [6, 7, 8, 9, 17, 18, 19], technique: 'crankbaits along rocky points' },
  'Walleye': { peakHours: [4, 5, 6, 20, 21, 22], technique: 'jigs tipped with minnows' },
  'Crappie': { peakHours: [5, 6, 7, 8, 17, 18, 19], technique: 'small jigs near brush piles' },
  'Channel Catfish': { peakHours: [20, 21, 22, 23, 0, 1, 2, 3, 4], technique: 'stink bait on bottom rigs' },
  'Bluegill': { peakHours: [7, 8, 9, 10, 16, 17, 18], technique: 'worms under a bobber in shallows' },
  'Rainbow Trout': { peakHours: [6, 7, 8, 9, 10, 16, 17, 18], technique: 'spinners in moving water' },
  'Northern Pike': { peakHours: [7, 8, 9, 10, 11, 15, 16, 17], technique: 'large spinnerbaits near weed edges' },
};

// ── Notification Scheduling ──────────────────────────────────────────────────

/**
 * Schedule a local push notification for the best fishing window tomorrow.
 * Notification fires 30 minutes before the window starts.
 *
 * Requires expo-notifications to be installed. Gracefully degrades if unavailable.
 */
export async function scheduleBestTimeNotification(
  lat: number,
  lon: number,
): Promise<boolean> {
  try {
    // Dynamic import — expo-notifications may not be installed
    const Notifications = await import('expo-notifications').catch(() => null);
    if (!Notifications) return false;

    // Request permissions
    const { status } = await Notifications.requestPermissionsAsync();
    if (status !== 'granted') return false;

    // Get tomorrow's forecast
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(0, 0, 0, 0);

    const forecast = getDailyBiteForecast(lat, lon, { date: tomorrow });
    if (!forecast.bestWindow || forecast.bestWindow.score < 50) {
      return false; // Not worth notifying
    }

    const bestWindow = forecast.bestWindow;
    const activeSpecies = getActiveSpeciesForWindow(bestWindow);

    // Build notification content
    const speciesMsg = activeSpecies
      ? `${activeSpecies.species} are active now — try ${activeSpecies.technique}`
      : `${bestWindow.quality === 'prime' ? 'Prime' : 'Good'} conditions for fishing`;

    const title = bestWindow.quality === 'prime'
      ? 'Prime Fishing Conditions!'
      : 'Good Fishing Window Ahead';

    // Schedule 30 minutes before the window starts
    const notifyDate = new Date(tomorrow);
    notifyDate.setHours(bestWindow.startHour);
    notifyDate.setMinutes(-30);

    // Cancel any existing scheduled notifications for fishing windows
    const existing = await Notifications.getAllScheduledNotificationsAsync();
    for (const n of existing) {
      if ((n.content.data as any)?.type === 'fishing_window') {
        await Notifications.cancelScheduledNotificationAsync(n.identifier);
      }
    }

    // Schedule the notification
    await Notifications.scheduleNotificationAsync({
      content: {
        title,
        body: speciesMsg,
        data: {
          type: 'fishing_window',
          lat,
          lon,
          windowStart: bestWindow.startHour,
          windowEnd: bestWindow.endHour,
          score: bestWindow.score,
        },
        sound: true,
      },
      trigger: {
        type: Notifications.SchedulableTriggerInputTypes.DATE,
        date: notifyDate,
      },
    });

    // Also schedule a morning summary notification at 6 AM
    const morningSummary = new Date(tomorrow);
    morningSummary.setHours(6, 0, 0, 0);

    const windowSummary = forecast.windows
      .filter((w) => w.score >= 50)
      .slice(0, 3)
      .map((w) => `${w.label} (${w.quality})`)
      .join(', ');

    if (windowSummary) {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: `Today's Fishing Windows`,
          body: `Best times: ${windowSummary}. Overall: ${forecast.ratingLabel}.`,
          data: {
            type: 'fishing_window',
            lat,
            lon,
            windowStart: forecast.bestWindow?.startHour ?? 0,
            windowEnd: forecast.bestWindow?.endHour ?? 0,
            score: forecast.overallRating,
          },
          sound: true,
        },
        trigger: {
          type: Notifications.SchedulableTriggerInputTypes.DATE,
          date: morningSummary,
        },
      });
    }

    // Save that we scheduled
    await AsyncStorage.setItem(LAST_NOTIFICATION_KEY, JSON.stringify({
      scheduledAt: Date.now(),
      lat,
      lon,
      windowStart: bestWindow.startHour,
      score: bestWindow.score,
    }));

    return true;
  } catch {
    return false;
  }
}

// ── In-App Banner Logic ──────────────────────────────────────────────────────

/**
 * Check if the current time falls within a good fishing window.
 * Returns banner state for display on MapScreen.
 *
 * Called on app open and periodically while the app is active.
 */
export function checkAndNotify(
  lat: number,
  lon: number,
): FishingBannerState {
  const now = new Date();
  const currentHour = now.getHours();
  const currentMinute = now.getMinutes();
  const currentDecimalHour = currentHour + currentMinute / 60;

  const forecast = getDailyBiteForecast(lat, lon, { date: now });
  const currentScore = forecast.hourlyScores[currentHour] ?? 0;

  // Find the active window (if any)
  const activeWindow = forecast.windows.find((w) => {
    if (w.startHour <= w.endHour) {
      return currentDecimalHour >= w.startHour && currentDecimalHour <= w.endHour;
    }
    // Wraps midnight
    return currentDecimalHour >= w.startHour || currentDecimalHour <= w.endHour;
  });

  if (!activeWindow || currentScore < 45) {
    return {
      visible: false,
      message: '',
      biteRating: 'none',
      timeRemaining: '',
      windowEnd: 0,
      score: currentScore,
    };
  }

  // Calculate time remaining in the window
  let hoursRemaining: number;
  if (activeWindow.endHour > currentDecimalHour) {
    hoursRemaining = activeWindow.endHour - currentDecimalHour;
  } else {
    hoursRemaining = (24 - currentDecimalHour) + activeWindow.endHour;
  }

  const hrs = Math.floor(hoursRemaining);
  const mins = Math.round((hoursRemaining - hrs) * 60);
  const timeRemaining = hrs > 0 ? `${hrs}h ${mins}m remaining` : `${mins}m remaining`;

  // Determine species and technique suggestion
  const activeSpecies = getActiveSpeciesForWindow(activeWindow);
  const biteRating = activeWindow.quality === 'prime'
    ? 'prime'
    : activeWindow.quality === 'good'
    ? 'good'
    : 'fair';

  // Build message
  let message: string;
  if (activeSpecies) {
    message = `${activeSpecies.species} are active — try ${activeSpecies.technique}`;
  } else if (biteRating === 'prime') {
    message = 'Prime fishing conditions right now!';
  } else if (biteRating === 'good') {
    message = 'Good fishing window is active';
  } else {
    message = 'Fair conditions — worth a cast';
  }

  // Window end in Unix ms
  const windowEndDate = new Date(now);
  windowEndDate.setHours(activeWindow.endHour, 0, 0, 0);
  if (activeWindow.endHour <= currentHour) {
    windowEndDate.setDate(windowEndDate.getDate() + 1);
  }

  return {
    visible: true,
    message,
    biteRating,
    timeRemaining,
    windowEnd: windowEndDate.getTime(),
    score: currentScore,
    species: activeSpecies?.species,
    technique: activeSpecies?.technique,
  };
}

// ── Banner Dismissal Persistence ─────────────────────────────────────────────

/**
 * Mark the current banner as dismissed. Resets after the window passes.
 */
export async function dismissBanner(): Promise<void> {
  await AsyncStorage.setItem(BANNER_DISMISSED_KEY, JSON.stringify({
    dismissedAt: Date.now(),
    hour: new Date().getHours(),
  }));
}

/**
 * Check if the banner was dismissed for the current hour.
 */
export async function isBannerDismissed(): Promise<boolean> {
  try {
    const raw = await AsyncStorage.getItem(BANNER_DISMISSED_KEY);
    if (!raw) return false;

    const data = JSON.parse(raw);
    const currentHour = new Date().getHours();

    // Reset dismissal when the hour changes (new window)
    if (data.hour !== currentHour) return false;

    // Also reset after 2 hours
    if (Date.now() - data.dismissedAt > 2 * 60 * 60 * 1000) return false;

    return true;
  } catch {
    return false;
  }
}

// ── Notification Preferences ─────────────────────────────────────────────────

export interface NotificationPrefs {
  enabled: boolean;
  morningBrief: boolean;      // 6 AM summary
  windowAlerts: boolean;      // 30 min before good windows
  minQuality: 'prime' | 'good' | 'fair';
}

const DEFAULT_PREFS: NotificationPrefs = {
  enabled: true,
  morningBrief: true,
  windowAlerts: true,
  minQuality: 'good',
};

export async function getNotificationPrefs(): Promise<NotificationPrefs> {
  try {
    const raw = await AsyncStorage.getItem(NOTIFICATION_PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export async function saveNotificationPrefs(prefs: Partial<NotificationPrefs>): Promise<void> {
  const current = await getNotificationPrefs();
  await AsyncStorage.setItem(NOTIFICATION_PREFS_KEY, JSON.stringify({ ...current, ...prefs }));
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function getActiveSpeciesForWindow(
  window: TimeWindow,
): { species: string; technique: string } | null {
  const windowHours: number[] = [];
  if (window.startHour <= window.endHour) {
    for (let h = window.startHour; h <= window.endHour; h++) {
      windowHours.push(h);
    }
  } else {
    for (let h = window.startHour; h < 24; h++) windowHours.push(h);
    for (let h = 0; h <= window.endHour; h++) windowHours.push(h);
  }

  let bestMatch: { species: string; technique: string; overlap: number } | null = null;

  for (const [species, info] of Object.entries(SPECIES_ACTIVITY)) {
    const overlap = windowHours.filter((h) => info.peakHours.includes(h)).length;
    if (overlap > 0 && (!bestMatch || overlap > bestMatch.overlap)) {
      bestMatch = { species, technique: info.technique, overlap };
    }
  }

  return bestMatch ? { species: bestMatch.species, technique: bestMatch.technique } : null;
}
