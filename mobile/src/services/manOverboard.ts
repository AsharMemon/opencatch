/**
 * OpenCatch — Man Overboard (MOB) Service
 *
 * One-tap emergency feature that records exact GPS position when
 * triggered, starts tracking distance/bearing to the MOB point,
 * and sounds continuous alarms until cancelled.
 */

import { Platform, Alert } from 'react-native';
import { haversineMeters, bearingDeg } from './anchorAlarm';

// ── Types ────────────────────────────────────────────────────────────────────

export interface MOBEvent {
  /** GPS latitude where MOB was triggered */
  lat: number;
  /** GPS longitude where MOB was triggered */
  lon: number;
  /** Timestamp of the MOB event */
  triggeredAt: number;
  /** Unique identifier for this MOB event */
  id: string;
}

export interface MOBStatus {
  /** Whether MOB mode is currently active */
  active: boolean;
  /** The MOB event, if active */
  event: MOBEvent | null;
  /** Current distance to MOB point in meters */
  distanceMeters: number;
  /** Current bearing to MOB point in degrees */
  bearing: number;
  /** Current vessel position */
  currentLat: number | null;
  currentLon: number | null;
}

type MOBListener = (status: MOBStatus) => void;

// ── Module state ─────────────────────────────────────────────────────────────

let _event: MOBEvent | null = null;
let _locationSub: { remove(): void } | null = null;
let _currentLat: number | null = null;
let _currentLon: number | null = null;
let _listeners: MOBListener[] = [];
let _alarmInterval: ReturnType<typeof setInterval> | null = null;

// ── Private helpers ──────────────────────────────────────────────────────────

function computeStatus(): MOBStatus {
  if (!_event) {
    return {
      active: false,
      event: null,
      distanceMeters: 0,
      bearing: 0,
      currentLat: null,
      currentLon: null,
    };
  }

  const dist =
    _currentLat != null && _currentLon != null
      ? haversineMeters(_currentLat, _currentLon, _event.lat, _event.lon)
      : 0;

  const brng =
    _currentLat != null && _currentLon != null
      ? bearingDeg(_currentLat, _currentLon, _event.lat, _event.lon)
      : 0;

  return {
    active: true,
    event: _event,
    distanceMeters: dist,
    bearing: brng,
    currentLat: _currentLat,
    currentLon: _currentLon,
  };
}

function notifyListeners() {
  const status = computeStatus();
  for (const fn of _listeners) {
    try {
      fn(status);
    } catch {
      // ignore
    }
  }
}

async function pulseAlarm() {
  if (Platform.OS !== 'web') {
    try {
      const Haptics = require('expo-haptics');
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } catch {
      // haptics unavailable
    }
  }
}

function startAlarmLoop() {
  if (_alarmInterval) return;
  pulseAlarm(); // immediate first pulse
  _alarmInterval = setInterval(pulseAlarm, 1500);
}

function stopAlarmLoop() {
  if (_alarmInterval) {
    clearInterval(_alarmInterval);
    _alarmInterval = null;
  }
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Trigger Man Overboard. Records current GPS position and starts
 * continuous alarm + tracking mode.
 */
export async function triggerMOB(): Promise<MOBEvent | null> {
  // Stop any existing MOB first
  if (_event) {
    stopAlarmLoop();
    if (_locationSub) {
      _locationSub.remove();
      _locationSub = null;
    }
  }

  try {
    const Location = require('expo-location') as typeof import('expo-location');
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Location Required', 'Location permission is needed for Man Overboard tracking.');
      return null;
    }

    // Get immediate high-accuracy position
    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.BestForNavigation,
    });

    _event = {
      lat: loc.coords.latitude,
      lon: loc.coords.longitude,
      triggeredAt: Date.now(),
      id: `mob-${Date.now()}`,
    };

    _currentLat = loc.coords.latitude;
    _currentLon = loc.coords.longitude;

    // Start alarm
    startAlarmLoop();

    // Start continuous tracking to compute distance/bearing back to MOB point
    _locationSub = await Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.BestForNavigation,
        distanceInterval: 1,
        timeInterval: 2000,
      },
      (position) => {
        _currentLat = position.coords.latitude;
        _currentLon = position.coords.longitude;
        notifyListeners();
      },
    );

    notifyListeners();
    return _event;
  } catch {
    Alert.alert('Error', 'Unable to get GPS position for Man Overboard.');
    return null;
  }
}

/** Get the MOB marked position, if active. */
export function getMOBPosition(): { lat: number; lon: number } | null {
  return _event ? { lat: _event.lat, lon: _event.lon } : null;
}

/** Get current distance and bearing from vessel to MOB point. */
export function getDistanceToMOB(): { distanceMeters: number; bearing: number } | null {
  if (!_event || _currentLat == null || _currentLon == null) return null;
  return {
    distanceMeters: haversineMeters(_currentLat, _currentLon, _event.lat, _event.lon),
    bearing: bearingDeg(_currentLat, _currentLon, _event.lat, _event.lon),
  };
}

/** Get full MOB status snapshot. */
export function getMOBStatus(): MOBStatus {
  return computeStatus();
}

/**
 * Cancel the MOB event. Shows a confirmation dialog first.
 * Returns true if cancelled, false if user declined.
 */
export function cancelMOB(skipConfirmation = false): Promise<boolean> {
  return new Promise((resolve) => {
    const doCancel = () => {
      stopAlarmLoop();
      if (_locationSub) {
        _locationSub.remove();
        _locationSub = null;
      }
      _event = null;
      _currentLat = null;
      _currentLon = null;
      notifyListeners();
      resolve(true);
    };

    if (skipConfirmation) {
      doCancel();
      return;
    }

    Alert.alert(
      'Cancel Man Overboard?',
      'Are you sure you want to cancel the Man Overboard alert? Only cancel if the person has been recovered.',
      [
        { text: 'Keep Active', style: 'cancel', onPress: () => resolve(false) },
        { text: 'Cancel MOB', style: 'destructive', onPress: doCancel },
      ],
    );
  });
}

/** Check if MOB is active. */
export function isMOBActive(): boolean {
  return _event !== null;
}

// ── Listener management ──────────────────────────────────────────────────────

/** Subscribe to MOB status updates. Returns unsubscribe function. */
export function addMOBListener(fn: MOBListener): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}
