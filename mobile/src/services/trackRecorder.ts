/**
 * OpenCatch — GPS Track Recording Service
 *
 * Records fishing trips as GPS tracks with:
 * - Background location tracking
 * - Distance and duration calculation
 * - Track persistence via AsyncStorage
 * - GPX export support
 * - Waypoint marking during recording
 * - Speed-based track coloring data
 * - Auto-save on app backgrounding
 *
 * Uses expo-location for background location updates.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import { AppState, type AppStateStatus } from 'react-native';

// ── Types ────────────────────────────────────────────────────────────────────

export interface TrackPoint {
  lat: number;
  lon: number;
  altitude?: number;    // metres
  speed?: number;       // m/s
  heading?: number;     // degrees
  accuracy?: number;    // metres
  timestamp: number;    // Unix ms
}

export interface TrackWaypoint {
  id: string;
  lat: number;
  lon: number;
  timestamp: number;
  label?: string;
  type: 'waypoint' | 'catch';
  species?: string;     // For catch waypoints
}

export interface FishingTrack {
  id: string;
  name: string;
  startTime: number;
  endTime?: number;
  points: TrackPoint[];
  waypoints: TrackWaypoint[];
  distanceMiles: number;
  durationMinutes: number;
  maxSpeedMph: number;
  avgSpeedMph: number;
  photos?: string[];     // Photo URIs attached to track
  notes?: string;
  locationName?: string;
}

export interface TrackStats {
  totalTracks: number;
  totalDistanceMiles: number;
  totalDurationHours: number;
  longestTrackMiles: number;
}

/**
 * Speed-to-color mapping for track rendering.
 * Returns a hex color from green (slow) through yellow to red (fast).
 */
export function speedToColor(speedMph: number): string {
  // Clamp to 0-30 mph range for color mapping
  const t = Math.min(Math.max(speedMph / 30, 0), 1);
  if (t < 0.5) {
    // Green → Yellow
    const r = Math.round(255 * (t * 2));
    return `rgb(${r}, 200, 60)`;
  }
  // Yellow → Red
  const g = Math.round(200 * (1 - (t - 0.5) * 2));
  return `rgb(255, ${g}, 60)`;
}

/**
 * Build a GeoJSON FeatureCollection of LineString segments colored by speed.
 * Each segment connects two consecutive track points and carries the speed
 * of the starting point so the map layer can interpolate color.
 */
export function buildSpeedColoredGeoJSON(points: TrackPoint[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const speedMph = ((prev.speed ?? 0) + (curr.speed ?? 0)) / 2 * 2.237;
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [prev.lon, prev.lat],
          [curr.lon, curr.lat],
        ],
      },
      properties: {
        speedMph,
        color: speedToColor(speedMph),
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

// ── Constants ────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/tracks';
const ACTIVE_TRACK_KEY = '@opencatch/active_track';
const LOCATION_TASK_NAME = 'opencatch-track-recording';
const MIN_DISTANCE_M = 10;       // Minimum 10m between points
const MIN_INTERVAL_MS = 5000;    // At least 5 seconds between points

// ── Haversine Distance ───────────────────────────────────────────────────────

function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8; // Earth radius in miles
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function totalDistance(points: TrackPoint[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    total += haversine(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon);
  }
  return total;
}

// ── Track Recorder Class ─────────────────────────────────────────────────────

class TrackRecorderService {
  private activeTrack: FishingTrack | null = null;
  private locationSubscription: Location.LocationSubscription | null = null;
  private listeners: Set<(track: FishingTrack | null) => void> = new Set();
  private appStateSubscription: any = null;
  private isPaused = false;

  constructor() {
    // Auto-save when app goes to background
    this.appStateSubscription = AppState.addEventListener(
      'change',
      this._handleAppStateChange,
    );
  }

  private _handleAppStateChange = (nextState: AppStateStatus) => {
    if (nextState === 'background' || nextState === 'inactive') {
      if (this.activeTrack) {
        this._saveActiveTrack();
      }
    }
  };

  /**
   * Whether recording is currently paused.
   */
  getIsPaused(): boolean {
    return this.isPaused;
  }

  /**
   * Start recording a new track.
   */
  async startRecording(name?: string): Promise<FishingTrack> {
    // Request permissions
    const { status: fg } = await Location.requestForegroundPermissionsAsync();
    if (fg !== 'granted') {
      throw new Error('Location permission required to record tracks');
    }

    // Try background permission too (for when app is backgrounded)
    try {
      await Location.requestBackgroundPermissionsAsync();
    } catch {
      // Background not critical — foreground works fine
    }

    const now = Date.now();
    const track: FishingTrack = {
      id: `track-${now}`,
      name: name ?? `Trip ${new Date(now).toLocaleDateString()}`,
      startTime: now,
      points: [],
      waypoints: [],
      distanceMiles: 0,
      durationMinutes: 0,
      maxSpeedMph: 0,
      avgSpeedMph: 0,
    };

    this.activeTrack = track;
    this.isPaused = false;
    await this._saveActiveTrack();

    // Start location updates
    this.locationSubscription = await Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.High,
        distanceInterval: MIN_DISTANCE_M,
        timeInterval: MIN_INTERVAL_MS,
      },
      (location) => this._onLocationUpdate(location),
    );

    this._notify();
    return track;
  }

  /**
   * Stop recording and save the track.
   */
  async stopRecording(): Promise<FishingTrack | null> {
    if (!this.activeTrack) return null;

    // Stop location updates
    if (this.locationSubscription) {
      this.locationSubscription.remove();
      this.locationSubscription = null;
    }

    this.activeTrack.endTime = Date.now();
    this.activeTrack.durationMinutes = Math.round(
      (this.activeTrack.endTime - this.activeTrack.startTime) / 60000,
    );
    this.activeTrack.distanceMiles = totalDistance(this.activeTrack.points);
    this._updateAvgSpeed();

    // Save to persistent storage
    const tracks = await this._loadTracks();
    tracks.unshift(this.activeTrack);
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(tracks));

    const saved = this.activeTrack;
    this.activeTrack = null;
    this.isPaused = false;
    await AsyncStorage.removeItem(ACTIVE_TRACK_KEY);
    this._notify();

    return saved;
  }

  /**
   * Pause recording (stop updates but keep track active).
   */
  async pauseRecording(): Promise<void> {
    if (this.locationSubscription) {
      this.locationSubscription.remove();
      this.locationSubscription = null;
    }
    this.isPaused = true;
    this._notify();
  }

  /**
   * Resume recording.
   */
  async resumeRecording(): Promise<void> {
    if (!this.activeTrack) return;

    this.isPaused = false;
    this.locationSubscription = await Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.High,
        distanceInterval: MIN_DISTANCE_M,
        timeInterval: MIN_INTERVAL_MS,
      },
      (location) => this._onLocationUpdate(location),
    );
    this._notify();
  }

  /**
   * Discard the active recording.
   */
  async discardRecording(): Promise<void> {
    if (this.locationSubscription) {
      this.locationSubscription.remove();
      this.locationSubscription = null;
    }
    this.activeTrack = null;
    this.isPaused = false;
    await AsyncStorage.removeItem(ACTIVE_TRACK_KEY);
    this._notify();
  }

  /**
   * Mark a waypoint at the current GPS position during recording.
   */
  addWaypoint(label?: string): TrackWaypoint | null {
    if (!this.activeTrack || this.activeTrack.points.length === 0) return null;

    const lastPt = this.activeTrack.points[this.activeTrack.points.length - 1];
    const wp: TrackWaypoint = {
      id: `wp-${Date.now()}`,
      lat: lastPt.lat,
      lon: lastPt.lon,
      timestamp: Date.now(),
      label: label || `Waypoint ${(this.activeTrack.waypoints?.length ?? 0) + 1}`,
      type: 'waypoint',
    };

    if (!this.activeTrack.waypoints) this.activeTrack.waypoints = [];
    this.activeTrack.waypoints.push(wp);
    this._saveActiveTrack();
    this._notify();
    return wp;
  }

  /**
   * Log a catch at the current GPS position during recording.
   */
  addCatchWaypoint(species?: string): TrackWaypoint | null {
    if (!this.activeTrack || this.activeTrack.points.length === 0) return null;

    const lastPt = this.activeTrack.points[this.activeTrack.points.length - 1];
    const wp: TrackWaypoint = {
      id: `catch-${Date.now()}`,
      lat: lastPt.lat,
      lon: lastPt.lon,
      timestamp: Date.now(),
      label: species || 'Catch',
      type: 'catch',
      species,
    };

    if (!this.activeTrack.waypoints) this.activeTrack.waypoints = [];
    this.activeTrack.waypoints.push(wp);
    this._saveActiveTrack();
    this._notify();
    return wp;
  }

  /**
   * Get the active recording (if any).
   */
  getActiveTrack(): FishingTrack | null {
    return this.activeTrack;
  }

  /**
   * Check if recording is active.
   */
  isRecording(): boolean {
    return this.activeTrack !== null;
  }

  /**
   * Get all saved tracks.
   */
  async getSavedTracks(): Promise<FishingTrack[]> {
    return this._loadTracks();
  }

  /**
   * Delete a saved track.
   */
  async deleteTrack(trackId: string): Promise<void> {
    const tracks = await this._loadTracks();
    const filtered = tracks.filter((t) => t.id !== trackId);
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  }

  /**
   * Get aggregate stats across all tracks.
   */
  async getStats(): Promise<TrackStats> {
    const tracks = await this._loadTracks();
    return {
      totalTracks: tracks.length,
      totalDistanceMiles: tracks.reduce((sum, t) => sum + t.distanceMiles, 0),
      totalDurationHours: tracks.reduce((sum, t) => sum + t.durationMinutes, 0) / 60,
      longestTrackMiles: Math.max(0, ...tracks.map((t) => t.distanceMiles)),
    };
  }

  /**
   * Export a track as GPX XML string (includes waypoints).
   */
  exportGPX(track: FishingTrack): string {
    const pts = track.points.map((p) => {
      const time = new Date(p.timestamp).toISOString();
      let ele = '';
      if (p.altitude !== undefined) ele = `<ele>${p.altitude.toFixed(1)}</ele>`;
      let spd = '';
      if (p.speed !== undefined) spd = `<speed>${p.speed.toFixed(2)}</speed>`;
      return `      <trkpt lat="${p.lat}" lon="${p.lon}">
        ${ele}
        ${spd}
        <time>${time}</time>
      </trkpt>`;
    }).join('\n');

    const wpts = (track.waypoints ?? []).map((wp) => {
      const time = new Date(wp.timestamp).toISOString();
      return `  <wpt lat="${wp.lat}" lon="${wp.lon}">
    <name>${this._escapeXml(wp.label ?? wp.type)}</name>
    <time>${time}</time>
    <type>${wp.type}</type>
  </wpt>`;
    }).join('\n');

    return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="OpenCatch"
  xmlns="http://www.topografix.com/GPX/1/1"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">
  <metadata>
    <name>${this._escapeXml(track.name)}</name>
    <time>${new Date(track.startTime).toISOString()}</time>
  </metadata>
${wpts}
  <trk>
    <name>${this._escapeXml(track.name)}</name>
    <trkseg>
${pts}
    </trkseg>
  </trk>
</gpx>`;
  }

  /**
   * Import a GPX file and create a FishingTrack.
   */
  importGPX(gpxString: string): FishingTrack | null {
    try {
      // Simple XML parsing for GPX trackpoints
      const points: TrackPoint[] = [];
      const trkptRegex = /<trkpt\s+lat="([^"]+)"\s+lon="([^"]+)"[^>]*>([\s\S]*?)<\/trkpt>/g;
      const timeRegex = /<time>([^<]+)<\/time>/;
      const eleRegex = /<ele>([^<]+)<\/ele>/;

      let match;
      while ((match = trkptRegex.exec(gpxString)) !== null) {
        const lat = parseFloat(match[1]);
        const lon = parseFloat(match[2]);
        const content = match[3];

        const timeMatch = timeRegex.exec(content);
        const eleMatch = eleRegex.exec(content);

        points.push({
          lat,
          lon,
          altitude: eleMatch ? parseFloat(eleMatch[1]) : undefined,
          timestamp: timeMatch ? new Date(timeMatch[1]).getTime() : Date.now(),
        });
      }

      if (points.length === 0) return null;

      // Extract name
      const nameMatch = /<name>([^<]+)<\/name>/.exec(gpxString);
      const name = nameMatch ? nameMatch[1] : 'Imported Track';

      const now = Date.now();
      return {
        id: `imported-${now}`,
        name,
        startTime: points[0].timestamp,
        endTime: points[points.length - 1].timestamp,
        points,
        waypoints: [],
        distanceMiles: totalDistance(points),
        durationMinutes: Math.round((points[points.length - 1].timestamp - points[0].timestamp) / 60000),
        maxSpeedMph: 0,
        avgSpeedMph: 0,
      };
    } catch {
      return null;
    }
  }

  /**
   * Subscribe to active track changes.
   */
  subscribe(listener: (track: FishingTrack | null) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // ── Private methods ──────────────────────────────────────────────────────

  private _onLocationUpdate(location: Location.LocationObject): void {
    if (!this.activeTrack) return;

    const point: TrackPoint = {
      lat: location.coords.latitude,
      lon: location.coords.longitude,
      altitude: location.coords.altitude ?? undefined,
      speed: location.coords.speed ?? undefined,
      heading: location.coords.heading ?? undefined,
      accuracy: location.coords.accuracy ?? undefined,
      timestamp: location.timestamp,
    };

    // Track max speed
    if (point.speed !== undefined) {
      const speedMph = point.speed * 2.237; // m/s to mph
      if (speedMph > this.activeTrack.maxSpeedMph) {
        this.activeTrack.maxSpeedMph = Math.round(speedMph * 10) / 10;
      }
    }

    this.activeTrack.points.push(point);
    this.activeTrack.distanceMiles = totalDistance(this.activeTrack.points);
    this.activeTrack.durationMinutes = Math.round(
      (Date.now() - this.activeTrack.startTime) / 60000,
    );
    this._updateAvgSpeed();

    this._saveActiveTrack();
    this._notify();
  }

  private _updateAvgSpeed(): void {
    if (!this.activeTrack) return;
    const durationHours = this.activeTrack.durationMinutes / 60;
    if (durationHours > 0) {
      this.activeTrack.avgSpeedMph =
        Math.round((this.activeTrack.distanceMiles / durationHours) * 10) / 10;
    }
  }

  private async _saveActiveTrack(): Promise<void> {
    if (this.activeTrack) {
      await AsyncStorage.setItem(ACTIVE_TRACK_KEY, JSON.stringify(this.activeTrack));
    }
  }

  private async _loadTracks(): Promise<FishingTrack[]> {
    try {
      const data = await AsyncStorage.getItem(STORAGE_KEY);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  }

  private _notify(): void {
    for (const listener of this.listeners) {
      listener(this.activeTrack);
    }
  }

  private _escapeXml(str: string): string {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * Restore active track from storage (call on app startup).
   */
  async restoreActiveTrack(): Promise<void> {
    try {
      const data = await AsyncStorage.getItem(ACTIVE_TRACK_KEY);
      if (data) {
        this.activeTrack = JSON.parse(data);
        // Don't auto-resume recording — let user decide
        this._notify();
      }
    } catch {
      // Ignore restore failures
    }
  }
}

// Singleton instance
export const trackRecorder = new TrackRecorderService();
