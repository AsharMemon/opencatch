/**
 * OpenCatch — Chart Annotations Service
 *
 * Allows users to draw and annotate directly on the map:
 * circles, arrows, text notes, fishing spot markers, and polygons.
 *
 * All annotations persist via AsyncStorage and can be exported as GeoJSON
 * for sharing or backup.
 *
 * Competitor parity: Navionics chart annotations / sonar overlay.
 * OpenCatch EXCEEDS by adding GeoJSON export, color presets, and polygon support.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Constants ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/chart_annotations';

// ── Types ──────────────────────────────────────────────────────────────────────

export type AnnotationType = 'marker' | 'circle' | 'arrow' | 'text' | 'polygon';

export interface AnnotationCoordinate {
  latitude: number;
  longitude: number;
}

export interface MapAnnotation {
  id: string;
  type: AnnotationType;
  /** Primary coordinate (center for circle, start for arrow, placement for marker/text). */
  coordinate: AnnotationCoordinate;
  /** End coordinate for arrows; vertices for polygons. */
  endCoordinate?: AnnotationCoordinate;
  /** Additional vertices for polygon type. */
  vertices?: AnnotationCoordinate[];
  /** Radius in meters (circle type only). */
  radiusMeters?: number;
  /** User-facing label / note text. */
  label: string;
  /** Hex color string. */
  color: string;
  /** Ionicon name for marker type. */
  icon?: string;
  createdAt: string; // ISO 8601
  updatedAt: string; // ISO 8601
}

// ── Preset Colors ──────────────────────────────────────────────────────────────

export const ANNOTATION_COLORS = [
  { color: '#0A6EBD', label: 'Blue' },
  { color: '#C44B4B', label: 'Red' },
  { color: '#3D8B37', label: 'Green' },
  { color: '#C4841D', label: 'Amber' },
  { color: '#7B2D8E', label: 'Purple' },
  { color: '#1A1A18', label: 'Black' },
] as const;

export const ANNOTATION_ICONS = [
  { key: 'fish', label: 'Fish', ionicon: 'fish' },
  { key: 'location', label: 'Pin', ionicon: 'location' },
  { key: 'star', label: 'Star', ionicon: 'star' },
  { key: 'flag', label: 'Flag', ionicon: 'flag' },
  { key: 'warning', label: 'Hazard', ionicon: 'warning' },
  { key: 'boat', label: 'Anchor', ionicon: 'boat' },
] as const;

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Generate a unique annotation ID. */
function generateId(): string {
  return `ann_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// ── Persistence ────────────────────────────────────────────────────────────────

/** Load all saved annotations from AsyncStorage. */
export async function loadAnnotations(): Promise<MapAnnotation[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Save a new or updated annotation. If the annotation has no id, one is generated. */
export async function saveAnnotation(
  annotation: Omit<MapAnnotation, 'id' | 'createdAt' | 'updatedAt'> & { id?: string },
): Promise<MapAnnotation> {
  const existing = await loadAnnotations();
  const now = new Date().toISOString();

  if (annotation.id) {
    // Update existing
    const idx = existing.findIndex((a) => a.id === annotation.id);
    const updated: MapAnnotation = {
      ...annotation,
      id: annotation.id,
      createdAt: idx >= 0 ? existing[idx].createdAt : now,
      updatedAt: now,
    };
    if (idx >= 0) {
      existing[idx] = updated;
    } else {
      existing.push(updated);
    }
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(existing));
    return updated;
  }

  // Create new
  const created: MapAnnotation = {
    ...annotation,
    id: generateId(),
    createdAt: now,
    updatedAt: now,
  };
  existing.push(created);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(existing));
  return created;
}

/** Delete an annotation by ID. */
export async function deleteAnnotation(id: string): Promise<void> {
  const existing = await loadAnnotations();
  const filtered = existing.filter((a) => a.id !== id);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
}

/** Delete all annotations. */
export async function clearAnnotations(): Promise<void> {
  await AsyncStorage.removeItem(STORAGE_KEY);
}

// ── GeoJSON Export ─────────────────────────────────────────────────────────────

/** Convert all annotations to a GeoJSON FeatureCollection for sharing/backup. */
export async function exportAnnotations(): Promise<GeoJSON.FeatureCollection> {
  const annotations = await loadAnnotations();
  return annotationsToGeoJSON(annotations);
}

/** Convert an array of annotations to GeoJSON (synchronous helper). */
export function annotationsToGeoJSON(annotations: MapAnnotation[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = annotations.map((a) => {
    const properties: Record<string, any> = {
      id: a.id,
      type: a.type,
      label: a.label,
      color: a.color,
      icon: a.icon,
      createdAt: a.createdAt,
    };

    switch (a.type) {
      case 'marker':
      case 'text':
        return {
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: [a.coordinate.longitude, a.coordinate.latitude],
          },
          properties,
        };

      case 'circle':
        // Represent as a Point with radius in properties
        return {
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: [a.coordinate.longitude, a.coordinate.latitude],
          },
          properties: {
            ...properties,
            radiusMeters: a.radiusMeters ?? 0,
          },
        };

      case 'arrow':
        return {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [
              [a.coordinate.longitude, a.coordinate.latitude],
              ...(a.endCoordinate
                ? [[a.endCoordinate.longitude, a.endCoordinate.latitude]]
                : []),
            ],
          },
          properties,
        };

      case 'polygon':
        const verts = a.vertices ?? [];
        const coords = verts.map((v) => [v.longitude, v.latitude]);
        // Close the polygon
        if (coords.length > 0) {
          coords.push(coords[0]);
        }
        return {
          type: 'Feature',
          geometry: {
            type: 'Polygon',
            coordinates: [coords],
          },
          properties,
        };

      default:
        return {
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: [a.coordinate.longitude, a.coordinate.latitude],
          },
          properties,
        };
    }
  });

  return {
    type: 'FeatureCollection',
    features,
  };
}

// ── Map Rendering Helpers ──────────────────────────────────────────────────────

/** Build GeoJSON for only marker/text annotations (rendered as map symbols). */
export function markerAnnotationsGeoJSON(annotations: MapAnnotation[]): GeoJSON.FeatureCollection {
  const pts = annotations.filter((a) => a.type === 'marker' || a.type === 'text');
  return {
    type: 'FeatureCollection',
    features: pts.map((a) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [a.coordinate.longitude, a.coordinate.latitude],
      },
      properties: {
        id: a.id,
        type: a.type,
        label: a.label,
        color: a.color,
        icon: a.icon ?? 'location',
      },
    })),
  };
}

/** Build GeoJSON for arrow annotations (rendered as lines). */
export function arrowAnnotationsGeoJSON(annotations: MapAnnotation[]): GeoJSON.FeatureCollection {
  const arrows = annotations.filter((a) => a.type === 'arrow' && a.endCoordinate);
  return {
    type: 'FeatureCollection',
    features: arrows.map((a) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'LineString' as const,
        coordinates: [
          [a.coordinate.longitude, a.coordinate.latitude],
          [a.endCoordinate!.longitude, a.endCoordinate!.latitude],
        ],
      },
      properties: {
        id: a.id,
        color: a.color,
        label: a.label,
      },
    })),
  };
}

/** Build GeoJSON for circle annotations (rendered as points with radius). */
export function circleAnnotationsGeoJSON(annotations: MapAnnotation[]): GeoJSON.FeatureCollection {
  const circles = annotations.filter((a) => a.type === 'circle');
  return {
    type: 'FeatureCollection',
    features: circles.map((a) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Point' as const,
        coordinates: [a.coordinate.longitude, a.coordinate.latitude],
      },
      properties: {
        id: a.id,
        color: a.color,
        label: a.label,
        radiusMeters: a.radiusMeters ?? 100,
      },
    })),
  };
}

/**
 * Calculate the approximate radius in meters between two coordinates
 * (Haversine formula). Used when user draws a circle by dragging.
 */
export function distanceMeters(
  a: AnnotationCoordinate,
  b: AnnotationCoordinate,
): number {
  const R = 6371e3; // Earth radius in meters
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const sinDLat = Math.sin(dLat / 2);
  const sinDLon = Math.sin(dLon / 2);
  const h =
    sinDLat * sinDLat +
    Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * sinDLon * sinDLon;
  return R * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}
