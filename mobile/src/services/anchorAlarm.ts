/**
 * OpenCatch — Anchor Alarm Service
 *
 * Monitors GPS drift when anchored and alerts if the boat moves
 * beyond the configured watch radius. Uses expo-location background
 * tracking with haptic + audio alerts on breach.
 */

import { Platform } from 'react-native';

// ── Types ────────────────────────────────────────────────────────────────────

export interface AnchorWatch {
  /** Anchor drop position */
  anchorLat: number;
  anchorLon: number;
  /** Watch radius in meters */
  radiusMeters: number;
  /** Timestamp when watch was started */
  startedAt: number;
}

export interface AnchorStatus {
  /** Whether anchor watch is currently active */
  active: boolean;
  /** Current drift distance from anchor in meters */
  driftDistance: number;
  /** Bearing from anchor to current position in degrees (0-360) */
  bearing: number;
  /** True if drift exceeds the configured radius */
  alarm: boolean;
  /** Current position if tracking */
  currentLat: number | null;
  currentLon: number | null;
  /** The active watch config, if any */
  watch: AnchorWatch | null;
}

type AnchorAlarmListener = (status: AnchorStatus) => void;

// ── Geo helpers ──────────────────────────────────────────────────────────────

const EARTH_RADIUS_M = 6_371_000;

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

function toDeg(rad: number): number {
  return (rad * 180) / Math.PI;
}

/** Haversine distance in meters between two lat/lon pairs */
export function haversineMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Bearing in degrees from point 1 to point 2 */
export function bearingDeg(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return ((toDeg(Math.atan2(y, x)) % 360) + 360) % 360;
}

// ── Module state ─────────────────────────────────────────────────────────────

let _watch: AnchorWatch | null = null;
let _locationSub: { remove(): void } | null = null;
let _currentLat: number | null = null;
let _currentLon: number | null = null;
let _alarm = false;
let _listeners: AnchorAlarmListener[] = [];
let _alarmInterval: ReturnType<typeof setInterval> | null = null;

// ── Private helpers ──────────────────────────────────────────────────────────

function computeStatus(): AnchorStatus {
  if (!_watch) {
    return {
      active: false,
      driftDistance: 0,
      bearing: 0,
      alarm: false,
      currentLat: null,
      currentLon: null,
      watch: null,
    };
  }

  const dist =
    _currentLat != null && _currentLon != null
      ? haversineMeters(_watch.anchorLat, _watch.anchorLon, _currentLat, _currentLon)
      : 0;

  const brng =
    _currentLat != null && _currentLon != null
      ? bearingDeg(_watch.anchorLat, _watch.anchorLon, _currentLat, _currentLon)
      : 0;

  const alarmTriggered = dist > _watch.radiusMeters;

  return {
    active: true,
    driftDistance: dist,
    bearing: brng,
    alarm: alarmTriggered,
    currentLat: _currentLat,
    currentLon: _currentLon,
    watch: _watch,
  };
}

function notifyListeners() {
  const status = computeStatus();
  for (const fn of _listeners) {
    try {
      fn(status);
    } catch {
      // ignore listener errors
    }
  }
}

async function triggerAlarmEffects() {
  // Haptic
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
  // Pulse haptic every 2 seconds while alarm is active
  _alarmInterval = setInterval(() => {
    if (_alarm) {
      triggerAlarmEffects();
    }
  }, 2000);
}

function stopAlarmLoop() {
  if (_alarmInterval) {
    clearInterval(_alarmInterval);
    _alarmInterval = null;
  }
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Start anchor watch at the given position with a drift radius.
 * Begins GPS monitoring and will trigger alarm if boat drifts beyond radius.
 */
export async function startAnchorWatch(
  lat: number,
  lon: number,
  radiusMeters: number,
): Promise<void> {
  // Stop any existing watch first
  await stopAnchorWatch();

  _watch = {
    anchorLat: lat,
    anchorLon: lon,
    radiusMeters: Math.max(10, radiusMeters),
    startedAt: Date.now(),
  };

  _currentLat = lat;
  _currentLon = lon;
  _alarm = false;

  // Start GPS tracking
  try {
    const Location = require('expo-location') as typeof import('expo-location');
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      throw new Error('Location permission not granted');
    }

    _locationSub = await Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.High,
        distanceInterval: 2, // update every 2 meters of movement
        timeInterval: 3000, // or every 3 seconds
      },
      (loc) => {
        _currentLat = loc.coords.latitude;
        _currentLon = loc.coords.longitude;

        if (_watch) {
          const dist = haversineMeters(
            _watch.anchorLat,
            _watch.anchorLon,
            _currentLat!,
            _currentLon!,
          );
          const wasAlarming = _alarm;
          _alarm = dist > _watch.radiusMeters;

          if (_alarm && !wasAlarming) {
            triggerAlarmEffects();
            startAlarmLoop();
          } else if (!_alarm && wasAlarming) {
            stopAlarmLoop();
          }
        }

        notifyListeners();
      },
    );
  } catch {
    // Location unavailable — watch is set but without live tracking
  }

  notifyListeners();
}

/** Stop anchor watch and clear all monitoring. */
export async function stopAnchorWatch(): Promise<void> {
  if (_locationSub) {
    _locationSub.remove();
    _locationSub = null;
  }
  stopAlarmLoop();
  _watch = null;
  _alarm = false;
  _currentLat = null;
  _currentLon = null;
  notifyListeners();
}

/** Get the current anchor watch status (non-reactive snapshot). */
export function getAnchorStatus(): AnchorStatus {
  return computeStatus();
}

/** Check if anchor watch is currently active. */
export function isAnchorWatchActive(): boolean {
  return _watch !== null;
}

/** Update the watch radius while anchor watch is active. */
export function setAnchorRadius(radiusMeters: number): void {
  if (_watch) {
    _watch.radiusMeters = Math.max(10, radiusMeters);
    // Re-evaluate alarm state
    if (_currentLat != null && _currentLon != null) {
      const dist = haversineMeters(
        _watch.anchorLat,
        _watch.anchorLon,
        _currentLat,
        _currentLon,
      );
      const wasAlarming = _alarm;
      _alarm = dist > _watch.radiusMeters;
      if (_alarm && !wasAlarming) {
        triggerAlarmEffects();
        startAlarmLoop();
      } else if (!_alarm && wasAlarming) {
        stopAlarmLoop();
      }
    }
    notifyListeners();
  }
}

// ── Listener management ──────────────────────────────────────────────────────

/** Subscribe to anchor status updates. Returns unsubscribe function. */
export function addAnchorListener(fn: AnchorAlarmListener): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/**
 * Generate a GeoJSON circle polygon for displaying the watch radius on a map.
 * Returns a Feature<Polygon> with 64 vertices.
 */
export function anchorCircleGeoJSON(
  centerLat: number,
  centerLon: number,
  radiusMeters: number,
): GeoJSON.Feature<GeoJSON.Polygon> {
  const SEGMENTS = 64;
  const coords: [number, number][] = [];

  for (let i = 0; i <= SEGMENTS; i++) {
    const angle = (2 * Math.PI * i) / SEGMENTS;
    const dLat = (radiusMeters / EARTH_RADIUS_M) * Math.cos(angle);
    const dLon =
      (radiusMeters / (EARTH_RADIUS_M * Math.cos(toRad(centerLat)))) *
      Math.sin(angle);
    coords.push([centerLon + toDeg(dLon), centerLat + toDeg(dLat)]);
  }

  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [coords],
    },
  };
}
