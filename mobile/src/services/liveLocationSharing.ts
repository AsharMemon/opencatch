/**
 * OpenCatch — Live Location Sharing Service
 *
 * Share real-time position with non-app users via native share sheet.
 * Since there is no backend yet, this generates Google Maps links
 * with the current position that can be shared via text/email.
 *
 * Future: Generate a unique URL backed by a real-time server.
 */

import { Share, Alert, Platform } from 'react-native';

// ── Types ────────────────────────────────────────────────────────────────────

export interface SharingSession {
  /** Unique session identifier */
  id: string;
  /** When sharing started */
  startedAt: number;
  /** Duration in hours (for display / auto-stop) */
  durationHours: number;
  /** When sharing should auto-stop */
  expiresAt: number;
  /** Latest shared position */
  lastLat: number | null;
  lastLon: number | null;
  /** How many position updates have been shared */
  updateCount: number;
}

type SharingListener = (session: SharingSession | null) => void;

// ── Module state ─────────────────────────────────────────────────────────────

let _session: SharingSession | null = null;
let _locationSub: { remove(): void } | null = null;
let _expiryTimer: ReturnType<typeof setTimeout> | null = null;
let _listeners: SharingListener[] = [];

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatCoordinate(value: number, isLat: boolean): string {
  const dir = isLat ? (value >= 0 ? 'N' : 'S') : (value >= 0 ? 'E' : 'W');
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const minRaw = (abs - deg) * 60;
  const min = Math.floor(minRaw);
  const sec = ((minRaw - min) * 60).toFixed(1);
  return `${deg}\u00B0${min}'${sec}"${dir}`;
}

function makeGoogleMapsLink(lat: number, lon: number): string {
  return `https://maps.google.com/?q=${lat.toFixed(6)},${lon.toFixed(6)}`;
}

function notifyListeners() {
  for (const fn of _listeners) {
    try {
      fn(_session);
    } catch {
      // ignore
    }
  }
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Start a location sharing session. Begins GPS tracking and provides
 * a mechanism to periodically share updated positions.
 *
 * Since we don't have a server, this sets up the session and the user
 * can manually re-share their updated position via shareCurrentPosition().
 */
export async function startSharing(durationHours: number = 4): Promise<SharingSession | null> {
  // Stop any existing session
  await stopSharing();

  try {
    const Location = require('expo-location') as typeof import('expo-location');
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Permission Required', 'Location permission is needed to share your position.');
      return null;
    }

    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.High,
    });

    const now = Date.now();
    _session = {
      id: `share-${now}`,
      startedAt: now,
      durationHours,
      expiresAt: now + durationHours * 60 * 60 * 1000,
      lastLat: loc.coords.latitude,
      lastLon: loc.coords.longitude,
      updateCount: 0,
    };

    // Track position updates
    _locationSub = await Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.High,
        distanceInterval: 10,
        timeInterval: 10000,
      },
      (position) => {
        if (_session) {
          _session.lastLat = position.coords.latitude;
          _session.lastLon = position.coords.longitude;
          notifyListeners();
        }
      },
    );

    // Auto-stop when duration expires
    _expiryTimer = setTimeout(() => {
      stopSharing();
      Alert.alert('Sharing Ended', 'Your location sharing session has expired.');
    }, durationHours * 60 * 60 * 1000);

    // Immediately share the first position
    await shareCurrentPosition();

    notifyListeners();
    return _session;
  } catch {
    Alert.alert('Error', 'Unable to start location sharing.');
    return null;
  }
}

/** Stop the active sharing session. */
export async function stopSharing(): Promise<void> {
  if (_locationSub) {
    _locationSub.remove();
    _locationSub = null;
  }
  if (_expiryTimer) {
    clearTimeout(_expiryTimer);
    _expiryTimer = null;
  }
  _session = null;
  notifyListeners();
}

/**
 * One-time share of current GPS position as a Google Maps link.
 * Works whether or not a sharing session is active.
 */
export async function shareCurrentPosition(): Promise<void> {
  try {
    let lat: number;
    let lon: number;

    if (_session?.lastLat != null && _session?.lastLon != null) {
      lat = _session.lastLat;
      lon = _session.lastLon;
    } else {
      const Location = require('expo-location') as typeof import('expo-location');
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permission Required', 'Location permission is needed to share your position.');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.High,
      });
      lat = loc.coords.latitude;
      lon = loc.coords.longitude;
    }

    const mapsLink = makeGoogleMapsLink(lat, lon);
    const coordStr = `${formatCoordinate(lat, true)}, ${formatCoordinate(lon, false)}`;
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const message = [
      `My current location (${time}):`,
      coordStr,
      `${lat.toFixed(6)}, ${lon.toFixed(6)}`,
      '',
      mapsLink,
      '',
      'Shared from OpenCatch',
    ].join('\n');

    if (_session) {
      _session.updateCount += 1;
    }

    await Share.share({
      message,
      title: 'My Location - OpenCatch',
    });

    notifyListeners();
  } catch {
    // User cancelled share or location failed
  }
}

/** Get the current sharing session, if active. */
export function getSharingSession(): SharingSession | null {
  // Check if session has expired
  if (_session && Date.now() >= _session.expiresAt) {
    stopSharing();
    return null;
  }
  return _session;
}

/** Check if sharing is currently active. */
export function isSharingActive(): boolean {
  return _session !== null && Date.now() < _session.expiresAt;
}

/** Get remaining time in the sharing session as a formatted string. */
export function getSharingTimeRemaining(): string | null {
  if (!_session) return null;
  const remaining = _session.expiresAt - Date.now();
  if (remaining <= 0) return null;
  const hours = Math.floor(remaining / (60 * 60 * 1000));
  const mins = Math.floor((remaining % (60 * 60 * 1000)) / (60 * 1000));
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

// ── Listener management ──────────────────────────────────────────────────────

/** Subscribe to sharing session updates. Returns unsubscribe function. */
export function addSharingListener(fn: SharingListener): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}
