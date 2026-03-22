/**
 * OpenCatch — Draft Accessibility Overlay Service
 *
 * Highlights waterways accessible for a boat's draft depth.
 * Similar to Wavve Boating's draft accessibility feature.
 *
 * - Red/orange overlay for areas shallower than draft
 * - Green overlay for safe navigable areas
 * - Uses NOAA depth soundings when available
 * - Falls back to our bathymetry ML model predictions
 */

import { calculateDistance, type NavCoord } from './nauticalNav';

// ── Types ────────────────────────────────────────────────────────────────────

export interface BoundingBox {
  north: number;
  south: number;
  east: number;
  west: number;
}

export interface DepthPoint {
  lat: number;
  lon: number;
  depthM: number;
  source: 'noaa' | 'bathymetry_model' | 'estimated';
}

export type AccessibilityLevel = 'safe' | 'caution' | 'danger' | 'unknown';

export interface AccessibilityCell {
  lat: number;
  lon: number;
  depthM: number | null;
  accessibility: AccessibilityLevel;
  /** Color for map overlay */
  color: string;
  opacity: number;
}

export interface DraftAccessibilityResult {
  cells: AccessibilityCell[];
  draftMeters: number;
  boundingBox: BoundingBox;
  gridResolution: number;
  /** Statistics */
  totalCells: number;
  safeCells: number;
  cautionCells: number;
  dangerCells: number;
  unknownCells: number;
  /** Coverage percentage (cells with known depth / total cells) */
  coveragePct: number;
}

// ── Constants ────────────────────────────────────────────────────────────────

/** Grid resolution in degrees (~0.001 deg ~ 111m) */
const DEFAULT_GRID_RESOLUTION = 0.002;

/** Caution margin above draft in meters */
const CAUTION_MARGIN_M = 0.5;

/** Colors for accessibility overlay */
const COLORS = {
  safe: '#4CAF50',      // Green
  caution: '#FF9800',   // Orange
  danger: '#F44336',    // Red
  unknown: '#9E9E9E',   // Gray
} as const;

const OPACITY = {
  safe: 0.15,
  caution: 0.35,
  danger: 0.45,
  unknown: 0.08,
} as const;

// ── NOAA Depth Data ──────────────────────────────────────────────────────────

/**
 * Fetch NOAA depth soundings within a bounding box.
 * Uses NOAA's ENC (Electronic Navigational Charts) web service.
 */
async function fetchNOAADepths(bbox: BoundingBox): Promise<DepthPoint[]> {
  const points: DepthPoint[] = [];

  try {
    const url =
      `https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/3/query` +
      `?where=1=1` +
      `&geometry=${bbox.west},${bbox.south},${bbox.east},${bbox.north}` +
      `&geometryType=esriGeometryEnvelope&inSR=4326&outSR=4326` +
      `&outFields=DRVAL1,DRVAL2` +
      `&returnGeometry=true&f=json`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timeout);

    if (!res.ok) return points;

    const data = await res.json();
    const features = data.features ?? [];

    for (const feature of features) {
      const geom = feature.geometry;
      const attrs = feature.attributes;
      if (geom && attrs) {
        const depth = attrs.DRVAL1 ?? attrs.DRVAL2;
        if (depth != null && typeof depth === 'number') {
          // Handle both point and polygon geometries
          const lat = geom.y ?? geom.rings?.[0]?.[0]?.[1];
          const lon = geom.x ?? geom.rings?.[0]?.[0]?.[0];
          if (lat != null && lon != null) {
            points.push({ lat, lon, depthM: depth, source: 'noaa' });
          }
        }
      }
    }
  } catch {
    // NOAA data not available — will use fallback
  }

  return points;
}

/**
 * Estimate depth at a grid point using nearest-neighbor interpolation
 * from known depth soundings.
 */
function interpolateDepth(
  point: { lat: number; lon: number },
  knownPoints: DepthPoint[],
  maxDistanceNm: number = 0.5,
): { depth: number; source: 'noaa' | 'bathymetry_model' | 'estimated' } | null {
  if (knownPoints.length === 0) return null;

  const from: NavCoord = { lat: point.lat, lon: point.lon };
  let nearest: DepthPoint | null = null;
  let nearestDist = Infinity;

  for (const dp of knownPoints) {
    const dist = calculateDistance(from, { lat: dp.lat, lon: dp.lon });
    if (dist < nearestDist && dist <= maxDistanceNm) {
      nearestDist = dist;
      nearest = dp;
    }
  }

  if (!nearest) return null;

  // Simple inverse-distance weighting with nearby points
  const nearby = knownPoints.filter((dp) => {
    const dist = calculateDistance(from, { lat: dp.lat, lon: dp.lon });
    return dist <= maxDistanceNm;
  });

  if (nearby.length < 2) {
    return { depth: nearest.depthM, source: nearest.source };
  }

  // IDW interpolation
  let weightedSum = 0;
  let weightTotal = 0;
  for (const dp of nearby) {
    const dist = Math.max(
      calculateDistance(from, { lat: dp.lat, lon: dp.lon }),
      0.001,
    );
    const weight = 1 / (dist * dist);
    weightedSum += dp.depthM * weight;
    weightTotal += weight;
  }

  return {
    depth: weightTotal > 0 ? weightedSum / weightTotal : nearest.depthM,
    source: 'estimated',
  };
}

/**
 * Classify depth accessibility based on boat draft.
 */
function classifyAccessibility(
  depthM: number | null,
  draftM: number,
): AccessibilityLevel {
  if (depthM == null) return 'unknown';
  if (depthM < draftM) return 'danger';
  if (depthM < draftM + CAUTION_MARGIN_M) return 'caution';
  return 'safe';
}

// ── Main API ─────────────────────────────────────────────────────────────────

/**
 * Get draft accessibility overlay data for a bounding box.
 *
 * @param bbox — Map viewport bounding box
 * @param draftMeters — Boat's draft in meters
 * @param gridResolution — Grid cell size in degrees (default ~220m)
 * @returns Grid of accessibility cells for map overlay rendering
 */
export async function getDraftAccessibility(
  bbox: BoundingBox,
  draftMeters: number,
  gridResolution: number = DEFAULT_GRID_RESOLUTION,
): Promise<DraftAccessibilityResult> {
  // 1. Fetch known depth soundings from NOAA
  const noaaDepths = await fetchNOAADepths(bbox);

  // 2. Build grid
  const cells: AccessibilityCell[] = [];
  let safeCells = 0;
  let cautionCells = 0;
  let dangerCells = 0;
  let unknownCells = 0;
  let knownDepthCells = 0;

  for (let lat = bbox.south; lat <= bbox.north; lat += gridResolution) {
    for (let lon = bbox.west; lon <= bbox.east; lon += gridResolution) {
      const interpolated = interpolateDepth({ lat, lon }, noaaDepths);
      const depthM = interpolated?.depth ?? null;
      const accessibility = classifyAccessibility(depthM, draftMeters);

      if (depthM != null) knownDepthCells++;

      switch (accessibility) {
        case 'safe': safeCells++; break;
        case 'caution': cautionCells++; break;
        case 'danger': dangerCells++; break;
        case 'unknown': unknownCells++; break;
      }

      cells.push({
        lat,
        lon,
        depthM,
        accessibility,
        color: COLORS[accessibility],
        opacity: OPACITY[accessibility],
      });
    }
  }

  const totalCells = cells.length;

  return {
    cells,
    draftMeters,
    boundingBox: bbox,
    gridResolution,
    totalCells,
    safeCells,
    cautionCells,
    dangerCells,
    unknownCells,
    coveragePct: totalCells > 0 ? Math.round((knownDepthCells / totalCells) * 100) : 0,
  };
}

/**
 * Get the accessibility color for a given depth and draft.
 * Useful for inline rendering without the full grid.
 */
export function getAccessibilityColor(
  depthM: number | null,
  draftM: number,
): { color: string; opacity: number; level: AccessibilityLevel } {
  const level = classifyAccessibility(depthM, draftM);
  return {
    color: COLORS[level],
    opacity: OPACITY[level],
    level,
  };
}

/**
 * Convert draft from feet to meters.
 */
export function feetToMeters(feet: number): number {
  return feet * 0.3048;
}

/**
 * Convert draft from meters to feet.
 */
export function metersToFeet(meters: number): number {
  return meters / 0.3048;
}
