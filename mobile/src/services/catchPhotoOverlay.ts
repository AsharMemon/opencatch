/**
 * OpenCatch — Catch Photo Map Overlay Service
 *
 * Displays catch photos as circular thumbnail markers on the map,
 * similar to Fishbrain's catch feed but integrated with the map view.
 *
 * - Reads catches from AsyncStorage (catchEnhancements storage)
 * - Converts photo catches to GeoJSON for MapLibre rendering
 * - Generates/caches thumbnails for map display
 *
 * Competitor parity: Fishbrain catch feed on map.
 * OpenCatch EXCEEDS by showing weather conditions at time of catch.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import type { EnhancedCatch, CatchPhoto } from './catchEnhancements';

// ── Types ────────────────────────────────────────────────────────────────────

export interface CatchWithPhoto {
  id: string;
  species: string;
  weight?: number;
  length?: number;
  lat: number;
  lon: number;
  locationName?: string;
  photo: CatchPhoto;
  allPhotos: CatchPhoto[];
  bait?: string;
  technique?: string;
  airTemp?: number;
  windSpeed?: number;
  windDirection?: string;
  pressure?: number;
  cloudCover?: string;
  notes?: string;
  released: boolean;
  timestamp: number;
}

export interface CatchPhotoGeoJSON {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    geometry: {
      type: 'Point';
      coordinates: [number, number];
    };
    properties: {
      id: string;
      species: string;
      weight?: number;
      length?: number;
      photoUri: string;
      photoCount: number;
      bait?: string;
      technique?: string;
      airTemp?: number;
      windSpeed?: number;
      windDirection?: string;
      pressure?: number;
      cloudCover?: string;
      notes?: string;
      released: boolean;
      timestamp: number;
      locationName?: string;
      dateLabel: string;
    };
  }>;
}

// ── Constants ────────────────────────────────────────────────────────────────

const CATCHES_KEY = '@opencatch/catches';

// Simple in-memory thumbnail cache (URI -> boolean indicating availability)
const thumbnailCache = new Map<string, boolean>();

// ── Data Access ──────────────────────────────────────────────────────────────

/**
 * Read all catches that have at least one photo attached.
 */
export async function getCatchesWithPhotos(): Promise<CatchWithPhoto[]> {
  try {
    const raw = await AsyncStorage.getItem(CATCHES_KEY);
    if (!raw) return [];

    const catches: EnhancedCatch[] = JSON.parse(raw);
    const withPhotos: CatchWithPhoto[] = [];

    for (const c of catches) {
      if (c.photos && c.photos.length > 0 && c.lat && c.lon) {
        withPhotos.push({
          id: c.id,
          species: c.species,
          weight: c.weight,
          length: c.length,
          lat: c.lat,
          lon: c.lon,
          locationName: c.locationName,
          photo: c.photos[0],
          allPhotos: c.photos,
          bait: c.bait,
          technique: c.technique,
          airTemp: c.airTemp,
          windSpeed: c.windSpeed,
          windDirection: c.windDirection,
          pressure: c.pressure,
          cloudCover: c.cloudCover,
          notes: c.notes,
          released: c.released,
          timestamp: c.timestamp,
        });
      }
    }

    // Sort newest first
    withPhotos.sort((a, b) => b.timestamp - a.timestamp);
    return withPhotos;
  } catch {
    return [];
  }
}

// ── GeoJSON Conversion ───────────────────────────────────────────────────────

/**
 * Convert photo catches to a GeoJSON FeatureCollection for MapLibre rendering.
 */
export function catchPhotosToGeoJSON(catches: CatchWithPhoto[]): CatchPhotoGeoJSON {
  return {
    type: 'FeatureCollection',
    features: catches.map((c) => {
      const date = new Date(c.timestamp);
      const dateLabel = date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [c.lon, c.lat] as [number, number],
        },
        properties: {
          id: c.id,
          species: c.species,
          weight: c.weight,
          length: c.length,
          photoUri: c.photo.uri,
          photoCount: c.allPhotos.length,
          bait: c.bait,
          technique: c.technique,
          airTemp: c.airTemp,
          windSpeed: c.windSpeed,
          windDirection: c.windDirection,
          pressure: c.pressure,
          cloudCover: c.cloudCover,
          notes: c.notes,
          released: c.released,
          timestamp: c.timestamp,
          locationName: c.locationName,
          dateLabel,
        },
      };
    }),
  };
}

// ── Thumbnail Management ─────────────────────────────────────────────────────

/**
 * Get a thumbnail URI for a catch photo.
 * On React Native with expo-image-picker, photos are already stored locally.
 * We use the original URI directly as expo handles image sizing efficiently.
 * The size hint is used for Image component resizing, not filesystem operations.
 */
export function getCatchPhotoThumbnail(
  uri: string,
  _size: number = 48,
): string {
  // Mark as cached for tracking purposes
  if (!thumbnailCache.has(uri)) {
    thumbnailCache.set(uri, true);
  }
  // Return original URI — React Native Image component handles display sizing
  return uri;
}

/**
 * Get catches that fall within a bounding box (for counting photos in view).
 */
export function filterCatchesInBounds(
  catches: CatchWithPhoto[],
  bounds: { north: number; south: number; east: number; west: number },
): CatchWithPhoto[] {
  return catches.filter(
    (c) =>
      c.lat >= bounds.south &&
      c.lat <= bounds.north &&
      c.lon >= bounds.west &&
      c.lon <= bounds.east,
  );
}

/**
 * Cluster nearby catches when zoomed out.
 * Returns catches grouped by proximity at a given zoom level.
 * At zoom < 10, clusters within ~0.1 degree; at zoom < 8, within ~0.5 degree.
 */
export function clusterCatchPhotos(
  catches: CatchWithPhoto[],
  zoom: number,
): CatchWithPhoto[][] {
  if (zoom >= 12) {
    // No clustering at high zoom — show all individually
    return catches.map((c) => [c]);
  }

  const gridSize = zoom < 8 ? 0.5 : zoom < 10 ? 0.1 : 0.05;
  const clusters = new Map<string, CatchWithPhoto[]>();

  for (const c of catches) {
    const gridKey = `${Math.floor(c.lat / gridSize)}_${Math.floor(c.lon / gridSize)}`;
    const existing = clusters.get(gridKey);
    if (existing) {
      existing.push(c);
    } else {
      clusters.set(gridKey, [c]);
    }
  }

  return Array.from(clusters.values());
}

/**
 * Format weather conditions for display in the photo detail popup.
 */
export function formatCatchConditions(catchData: CatchWithPhoto): string[] {
  const conditions: string[] = [];

  if (catchData.airTemp != null) {
    conditions.push(`${catchData.airTemp}\u00B0F`);
  }
  if (catchData.windSpeed != null) {
    const dir = catchData.windDirection ? ` ${catchData.windDirection}` : '';
    conditions.push(`${catchData.windSpeed} mph${dir}`);
  }
  if (catchData.pressure != null) {
    conditions.push(`${catchData.pressure} hPa`);
  }
  if (catchData.cloudCover != null) {
    conditions.push(catchData.cloudCover);
  }

  return conditions;
}
