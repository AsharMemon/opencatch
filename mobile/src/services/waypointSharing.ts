/**
 * OpenCatch — Waypoint & Marker Sharing Service (B8)
 *
 * Share fishing waypoints/markers between users via:
 * - Deep links (opencatch://waypoint?lat=X&lon=Y&name=Z)
 * - Formatted text messages
 * - JSON export/import for bulk sharing
 * - Google Maps link for non-app users
 */

import { Share, Linking, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export interface SharedWaypoint {
  id: string;
  name: string;
  lat: number;
  lon: number;
  icon?: string;
  color?: string;
  notes?: string;
  species?: string;
  depth?: number;      // feet
  createdAt: number;   // Unix ms
  sharedBy?: string;
}

export interface WaypointCollection {
  name: string;
  description?: string;
  waypoints: SharedWaypoint[];
  createdAt: number;
  version: number;
}

// ── Constants ────────────────────────────────────────────────────────────────

const SHARED_WAYPOINTS_KEY = '@opencatch/shared_waypoints';
const APP_SCHEME = 'opencatch';

// ── Share Functions ──────────────────────────────────────────────────────────

/**
 * Share a single waypoint via the system share sheet.
 *
 * @example
 * shareWaypoint({ id: '1', name: 'Bass Spot', lat: 44.98, lon: -93.27, createdAt: Date.now() });
 */
export async function shareWaypoint(waypoint: SharedWaypoint): Promise<boolean> {
  const deepLink = buildDeepLink(waypoint);
  const googleMapsLink = `https://www.google.com/maps?q=${waypoint.lat},${waypoint.lon}`;

  const message = [
    `📍 ${waypoint.name}`,
    `📐 ${waypoint.lat.toFixed(6)}, ${waypoint.lon.toFixed(6)}`,
    waypoint.species ? `🐟 ${waypoint.species}` : null,
    waypoint.depth ? `📏 ${waypoint.depth}ft depth` : null,
    waypoint.notes ? `📝 ${waypoint.notes}` : null,
    '',
    `Open in OpenCatch: ${deepLink}`,
    `Open in Maps: ${googleMapsLink}`,
  ].filter(Boolean).join('\n');

  try {
    const result = await Share.share({
      message,
      title: `Fishing Spot: ${waypoint.name}`,
    });
    return result.action === Share.sharedAction;
  } catch {
    return false;
  }
}

/**
 * Share a collection of waypoints as a JSON export.
 *
 * @example
 * shareCollection({ name: 'Lake Trip', waypoints: [...], createdAt: Date.now(), version: 1 });
 */
export async function shareCollection(collection: WaypointCollection): Promise<boolean> {
  const json = JSON.stringify(collection, null, 2);
  const summary = `🗺️ ${collection.name} — ${collection.waypoints.length} waypoints\n\n${json}`;

  try {
    const result = await Share.share({
      message: summary,
      title: collection.name,
    });
    return result.action === Share.sharedAction;
  } catch {
    return false;
  }
}

/**
 * Copy waypoint coordinates to clipboard (text format).
 */
export function formatWaypointText(waypoint: SharedWaypoint): string {
  return [
    waypoint.name,
    `${waypoint.lat.toFixed(6)}, ${waypoint.lon.toFixed(6)}`,
    waypoint.species ? `Species: ${waypoint.species}` : null,
    waypoint.depth ? `Depth: ${waypoint.depth}ft` : null,
    waypoint.notes ?? null,
  ].filter(Boolean).join(' | ');
}

// ── Deep Link Handling ───────────────────────────────────────────────────────

/**
 * Build a deep link URL for a waypoint.
 */
export function buildDeepLink(waypoint: SharedWaypoint): string {
  const params = new URLSearchParams({
    lat: waypoint.lat.toFixed(6),
    lon: waypoint.lon.toFixed(6),
    name: waypoint.name,
  });
  if (waypoint.icon) params.set('icon', waypoint.icon);
  if (waypoint.color) params.set('color', waypoint.color);
  if (waypoint.species) params.set('species', waypoint.species);
  if (waypoint.depth) params.set('depth', String(waypoint.depth));
  if (waypoint.notes) params.set('notes', waypoint.notes);

  return `${APP_SCHEME}://waypoint?${params.toString()}`;
}

/**
 * Parse a deep link URL into a SharedWaypoint.
 */
export function parseDeepLink(url: string): SharedWaypoint | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== `${APP_SCHEME}:` || parsed.hostname !== 'waypoint') {
      return null;
    }

    const lat = parseFloat(parsed.searchParams.get('lat') ?? '');
    const lon = parseFloat(parsed.searchParams.get('lon') ?? '');
    const name = parsed.searchParams.get('name') ?? 'Shared Spot';

    if (isNaN(lat) || isNaN(lon)) return null;

    return {
      id: `shared_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name,
      lat,
      lon,
      icon: parsed.searchParams.get('icon') ?? undefined,
      color: parsed.searchParams.get('color') ?? undefined,
      species: parsed.searchParams.get('species') ?? undefined,
      depth: parsed.searchParams.get('depth') ? parseFloat(parsed.searchParams.get('depth')!) : undefined,
      notes: parsed.searchParams.get('notes') ?? undefined,
      createdAt: Date.now(),
      sharedBy: 'Deep Link',
    };
  } catch {
    return null;
  }
}

// ── Import / Export ──────────────────────────────────────────────────────────

/**
 * Import a JSON collection string into saved waypoints.
 */
export async function importCollection(json: string): Promise<WaypointCollection | null> {
  try {
    const collection = JSON.parse(json) as WaypointCollection;
    if (!collection.waypoints || !Array.isArray(collection.waypoints)) {
      return null;
    }

    // Save imported waypoints
    const existing = await getSavedSharedWaypoints();
    const newWaypoints = collection.waypoints.map(wp => ({
      ...wp,
      id: `imported_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      sharedBy: collection.name,
    }));

    await AsyncStorage.setItem(
      SHARED_WAYPOINTS_KEY,
      JSON.stringify([...newWaypoints, ...existing]),
    );

    return { ...collection, waypoints: newWaypoints };
  } catch {
    return null;
  }
}

/**
 * Export all saved shared waypoints as a collection.
 */
export async function exportAllWaypoints(name?: string): Promise<WaypointCollection> {
  const waypoints = await getSavedSharedWaypoints();
  return {
    name: name ?? `OpenCatch Export ${new Date().toLocaleDateString()}`,
    waypoints,
    createdAt: Date.now(),
    version: 1,
  };
}

// ── Storage ──────────────────────────────────────────────────────────────────

/**
 * Save a received shared waypoint.
 */
export async function saveSharedWaypoint(waypoint: SharedWaypoint): Promise<void> {
  const existing = await getSavedSharedWaypoints();
  existing.unshift(waypoint);
  await AsyncStorage.setItem(SHARED_WAYPOINTS_KEY, JSON.stringify(existing));
}

/**
 * Get all saved shared waypoints.
 */
export async function getSavedSharedWaypoints(): Promise<SharedWaypoint[]> {
  try {
    const json = await AsyncStorage.getItem(SHARED_WAYPOINTS_KEY);
    return json ? JSON.parse(json) : [];
  } catch {
    return [];
  }
}

/**
 * Delete a shared waypoint by ID.
 */
export async function deleteSharedWaypoint(id: string): Promise<void> {
  const existing = await getSavedSharedWaypoints();
  const filtered = existing.filter(wp => wp.id !== id);
  await AsyncStorage.setItem(SHARED_WAYPOINTS_KEY, JSON.stringify(filtered));
}

/**
 * Clear all saved shared waypoints.
 */
export async function clearSharedWaypoints(): Promise<void> {
  await AsyncStorage.removeItem(SHARED_WAYPOINTS_KEY);
}

// ── Utility ──────────────────────────────────────────────────────────────────

/**
 * Generate a Google Maps URL for a waypoint.
 */
export function googleMapsUrl(lat: number, lon: number, label?: string): string {
  const q = label
    ? `${encodeURIComponent(label)}@${lat},${lon}`
    : `${lat},${lon}`;
  return `https://www.google.com/maps/search/?api=1&query=${q}`;
}

/**
 * Open a waypoint in the device's default maps app.
 */
export async function openInMaps(lat: number, lon: number, label?: string): Promise<void> {
  const encodedLabel = label ? encodeURIComponent(label) : '';
  const url = Platform.select({
    ios: `maps:0,0?q=${encodedLabel}@${lat},${lon}`,
    android: `geo:${lat},${lon}?q=${lat},${lon}(${encodedLabel})`,
    default: googleMapsUrl(lat, lon, label),
  });

  if (url && await Linking.canOpenURL(url)) {
    await Linking.openURL(url);
  } else {
    await Linking.openURL(googleMapsUrl(lat, lon, label));
  }
}
