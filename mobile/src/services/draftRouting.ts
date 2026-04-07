/**
 * OpenCatch — Draft-Aware Route Planner
 *
 * Uses bathymetry contour data to:
 * 1. Highlight areas too shallow for the user's boat draft
 * 2. Find safe navigation routes avoiding shallow areas
 * 3. Show real-time depth warnings as the user pans/navigates
 *
 * Similar to: FishAngler draft visualization, Navionics route charting.
 * OpenCatch EXCEEDS with satellite-derived bathymetry coverage on inland
 * lakes that Navionics has zero data for.
 *
 * Settings persist via AsyncStorage.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { palette } from '../theme/palette';

// ── Constants ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/draft_routing_settings';

const M_TO_FT = 3.28084;

// ── Types ──────────────────────────────────────────────────────────────────────

export interface DraftSettings {
  draftM: number;          // Boat draft in meters (how deep the hull sits)
  safetyMarginM: number;   // Additional safety margin (default 0.5m)
  showUnsafeZones: boolean;
  showRoute: boolean;
}

export interface RoutePoint {
  lat: number;
  lon: number;
  depthM: number;
  isSafe: boolean;
  warning?: string;
}

export interface SafeRoute {
  points: RoutePoint[];
  totalDistanceKm: number;
  minDepthM: number;
  hasUnsafeSegments: boolean;
  warnings: string[];
}

export type DepthWarningLevel = 'danger' | 'caution' | 'safe';

export interface DepthCheck {
  safe: boolean;
  warning: DepthWarningLevel;
  message?: string;
}

// ── Boat Draft Presets ─────────────────────────────────────────────────────────

export interface BoatPreset {
  key: string;
  label: string;
  draftM: number;
  ionicon: string; // Ionicons icon name
}

export const BOAT_PRESETS: BoatPreset[] = [
  { key: 'kayak',          label: 'Kayak / Canoe',   draftM: 0.1, ionicon: 'boat-outline' },
  { key: 'jon-boat',       label: 'Jon Boat',        draftM: 0.3, ionicon: 'boat-outline' },
  { key: 'walleye-boat',   label: 'Walleye Boat',    draftM: 0.4, ionicon: 'boat-outline' },
  { key: 'bass-boat',      label: 'Bass Boat',       draftM: 0.5, ionicon: 'boat' },
  { key: 'pontoon',        label: 'Pontoon',         draftM: 0.6, ionicon: 'boat' },
  { key: 'center-console', label: 'Center Console',  draftM: 0.7, ionicon: 'boat' },
  { key: 'sailboat',       label: 'Sailboat',        draftM: 1.5, ionicon: 'navigate-outline' },
];

// ── Defaults ───────────────────────────────────────────────────────────────────

export const DEFAULT_DRAFT_SETTINGS: DraftSettings = {
  draftM: 0.5,           // bass boat default
  safetyMarginM: 0.5,    // half-meter cushion
  showUnsafeZones: true,
  showRoute: true,
};

// ── Core Depth Logic ───────────────────────────────────────────────────────────

/**
 * Get the minimum safe depth for navigation.
 */
export function getMinSafeDepth(settings: DraftSettings): number {
  return settings.draftM + settings.safetyMarginM;
}

/**
 * Convert meters to feet.
 */
export function metersToFeet(m: number): number {
  return m * M_TO_FT;
}

/**
 * Convert feet to meters.
 */
export function feetToMeters(ft: number): number {
  return ft / M_TO_FT;
}

/**
 * Check if a point is safe for the current draft.
 * Uses local depth data from the map tiles.
 */
export function isPointSafe(depthM: number, settings: DraftSettings): DepthCheck {
  const minSafe = getMinSafeDepth(settings);

  if (depthM < settings.draftM) {
    return {
      safe: false,
      warning: 'danger',
      message: `Depth ${depthM.toFixed(1)}m (${metersToFeet(depthM).toFixed(0)}ft) — GROUNDING RISK`,
    };
  }

  if (depthM < minSafe) {
    return {
      safe: false,
      warning: 'caution',
      message: `Depth ${depthM.toFixed(1)}m (${metersToFeet(depthM).toFixed(0)}ft) — below safety margin`,
    };
  }

  return { safe: true, warning: 'safe' };
}

/**
 * Classify an array of depth samples along a potential route segment.
 * Returns the worst warning level encountered.
 */
export function classifySegment(
  depths: number[],
  settings: DraftSettings,
): { worstWarning: DepthWarningLevel; minDepth: number; unsafeCount: number } {
  let worstWarning: DepthWarningLevel = 'safe';
  let minDepth = Infinity;
  let unsafeCount = 0;

  for (const d of depths) {
    if (d < minDepth) minDepth = d;
    const check = isPointSafe(d, settings);
    if (!check.safe) unsafeCount++;
    if (check.warning === 'danger') {
      worstWarning = 'danger';
    } else if (check.warning === 'caution' && worstWarning !== 'danger') {
      worstWarning = 'caution';
    }
  }

  return { worstWarning, minDepth, unsafeCount };
}

// ── MapLibre Style Expressions ─────────────────────────────────────────────────

/** Colors for draft-safety overlay zones. */
export const DRAFT_ZONE_COLORS = {
  danger: 'rgba(196, 75, 75, 0.4)',       // palette.error with alpha
  caution: 'rgba(196, 132, 29, 0.25)',     // palette.warning with alpha
  safe: 'rgba(0, 0, 0, 0)',               // transparent
} as const;

/**
 * Build a MapLibre style expression that colors the map by safe/unsafe zones.
 * Areas shallower than draft+safety are highlighted in red/orange.
 *
 * Expects vector tiles with a `depth_ft` property.
 */
export function buildDraftSafetyExpression(settings: DraftSettings): any[] {
  const minSafeFt = metersToFeet(getMinSafeDepth(settings));
  const draftFt = metersToFeet(settings.draftM);

  return [
    'case',
    ['<', ['coalesce', ['get', 'depth_ft'], 0], draftFt],
    DRAFT_ZONE_COLORS.danger,
    ['<', ['coalesce', ['get', 'depth_ft'], 0], minSafeFt],
    DRAFT_ZONE_COLORS.caution,
    DRAFT_ZONE_COLORS.safe,
  ];
}

/**
 * Build a MapLibre fill-opacity expression for the draft overlay.
 * Fades in as you zoom closer.
 */
export function buildDraftOverlayOpacity(): any[] {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    8, 0.0,
    10, 0.3,
    13, 0.6,
    16, 0.8,
  ];
}

/**
 * MapLibre layer definition for the draft-safety fill overlay.
 * Add this layer on top of the bathymetry contour fills.
 */
export function getDraftSafetyLayer(settings: DraftSettings, sourceId: string): object {
  return {
    id: 'draft-safety-overlay',
    type: 'fill',
    source: sourceId,
    'source-layer': 'depth_contours',
    paint: {
      'fill-color': buildDraftSafetyExpression(settings),
      'fill-opacity': buildDraftOverlayOpacity(),
    },
    layout: {
      visibility: settings.showUnsafeZones ? 'visible' : 'none',
    },
  };
}

/**
 * MapLibre layer definition for the draft-safety boundary lines.
 * Draws a dashed line at the draft threshold contour.
 */
export function getDraftBoundaryLayer(settings: DraftSettings, sourceId: string): object {
  const draftFt = metersToFeet(settings.draftM);

  return {
    id: 'draft-boundary-line',
    type: 'line',
    source: sourceId,
    'source-layer': 'depth_contours',
    filter: ['==', ['get', 'depth_ft'], Math.round(draftFt)],
    paint: {
      'line-color': palette.error,
      'line-width': 2,
      'line-dasharray': [4, 3],
      'line-opacity': 0.8,
    },
    layout: {
      visibility: settings.showUnsafeZones ? 'visible' : 'none',
    },
  };
}

// ── Route GeoJSON ──────────────────────────────────────────────────────────────

/**
 * Convert a SafeRoute to GeoJSON LineString for map display.
 * Each segment is colored by safety level.
 */
export function routeToGeoJSON(route: SafeRoute): GeoJSON.FeatureCollection {
  if (route.points.length < 2) {
    return { type: 'FeatureCollection', features: [] };
  }

  const features: GeoJSON.Feature[] = [];

  // Build line segments with per-segment safety coloring
  for (let i = 0; i < route.points.length - 1; i++) {
    const a = route.points[i];
    const b = route.points[i + 1];
    const segmentSafe = a.isSafe && b.isSafe;
    const minDepth = Math.min(a.depthM, b.depthM);

    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[a.lon, a.lat], [b.lon, b.lat]],
      },
      properties: {
        safe: segmentSafe,
        minDepthM: minDepth,
        minDepthFt: metersToFeet(minDepth),
        warning: a.warning || b.warning || null,
      },
    });
  }

  // Start/end markers
  const start = route.points[0];
  const end = route.points[route.points.length - 1];

  features.push({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [start.lon, start.lat] },
    properties: { type: 'route-start', label: 'Start' },
  });

  features.push({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [end.lon, end.lat] },
    properties: { type: 'route-end', label: 'End' },
  });

  return { type: 'FeatureCollection', features };
}

/**
 * MapLibre layer for the safe route line segments.
 */
export function getRouteLineLayer(sourceId: string): object {
  return {
    id: 'draft-route-line',
    type: 'line',
    source: sourceId,
    filter: ['==', ['geometry-type'], 'LineString'],
    paint: {
      'line-color': [
        'case',
        ['get', 'safe'], palette.accent,
        palette.error,
      ],
      'line-width': 4,
      'line-opacity': 0.9,
    },
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
  };
}

/**
 * MapLibre layer for route start/end markers.
 */
export function getRouteMarkerLayer(sourceId: string): object {
  return {
    id: 'draft-route-markers',
    type: 'circle',
    source: sourceId,
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-radius': 8,
      'circle-color': [
        'match',
        ['get', 'type'],
        'route-start', palette.success,
        'route-end', palette.accent,
        palette.textMuted,
      ],
      'circle-stroke-width': 2,
      'circle-stroke-color': palette.surface,
    },
  };
}

// ── API Integration ────────────────────────────────────────────────────────────

/**
 * Find a safe route between two points, avoiding areas shallower than draft.
 * Uses A* pathfinding on a depth grid, computed server-side.
 */
export async function findSafeRoute(
  startLat: number,
  startLon: number,
  endLat: number,
  endLon: number,
  settings: DraftSettings,
  lakeId?: string,
): Promise<SafeRoute> {
  const response = await fetch('/api/route/safe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: { lat: startLat, lon: startLon },
      end: { lat: endLat, lon: endLon },
      min_depth_m: getMinSafeDepth(settings),
      draft_m: settings.draftM,
      lake_id: lakeId,
    }),
  });

  if (!response.ok) {
    throw new Error(`Route planning failed: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// ── Persistence ────────────────────────────────────────────────────────────────

/** Load draft routing settings from AsyncStorage. Falls back to defaults. */
export async function loadDraftSettings(): Promise<DraftSettings> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_DRAFT_SETTINGS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_DRAFT_SETTINGS, ...parsed };
  } catch {
    return { ...DEFAULT_DRAFT_SETTINGS };
  }
}

/** Save draft routing settings to AsyncStorage. */
export async function saveDraftSettings(settings: DraftSettings): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

/** Reset draft routing settings to defaults. */
export async function resetDraftSettings(): Promise<DraftSettings> {
  const defaults = { ...DEFAULT_DRAFT_SETTINGS };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(defaults));
  return defaults;
}

// ── Draft Settings Panel Props ─────────────────────────────────────────────────

/**
 * Props for the DraftSettingsPanel component.
 *
 * Usage (in a React Native component):
 *
 *   <DraftSettingsPanel
 *     settings={draftSettings}
 *     onSettingsChange={handleDraftChange}
 *     collapsed={panelCollapsed}
 *     onToggleCollapse={() => setPanelCollapsed(!panelCollapsed)}
 *   />
 */
export interface DraftSettingsPanelProps {
  settings: DraftSettings;
  onSettingsChange: (settings: DraftSettings) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/**
 * Format draft depth for display in the panel.
 * Shows both metric and imperial: "0.5m (1.6ft)"
 */
export function formatDraftDisplay(draftM: number): string {
  return `${draftM.toFixed(1)}m (${metersToFeet(draftM).toFixed(1)}ft)`;
}

/**
 * Get the boat preset matching a given draft, or null if custom.
 */
export function getPresetForDraft(draftM: number): BoatPreset | null {
  return BOAT_PRESETS.find((p) => Math.abs(p.draftM - draftM) < 0.01) ?? null;
}

/**
 * Panel slider range and step values.
 */
export const DRAFT_SLIDER = {
  min: 0.05,
  max: 3.0,
  step: 0.05,
} as const;

export const SAFETY_MARGIN_SLIDER = {
  min: 0.0,
  max: 2.0,
  step: 0.1,
} as const;

// ── Warning Utilities ──────────────────────────────────────────────────────────

/**
 * Generate human-readable warnings for a safe route.
 * Called after receiving a SafeRoute from the API.
 */
export function generateRouteWarnings(route: SafeRoute, settings: DraftSettings): string[] {
  const warnings: string[] = [];

  if (route.hasUnsafeSegments) {
    warnings.push(
      `Route includes shallow areas. Min depth: ${route.minDepthM.toFixed(1)}m ` +
      `(${metersToFeet(route.minDepthM).toFixed(0)}ft). ` +
      `Your draft: ${formatDraftDisplay(settings.draftM)}.`,
    );
  }

  const dangerPoints = route.points.filter(
    (p) => p.depthM < settings.draftM,
  );
  if (dangerPoints.length > 0) {
    warnings.push(
      `${dangerPoints.length} point(s) with grounding risk along route.`,
    );
  }

  const cautionPoints = route.points.filter(
    (p) => p.depthM >= settings.draftM && p.depthM < getMinSafeDepth(settings),
  );
  if (cautionPoints.length > 0) {
    warnings.push(
      `${cautionPoints.length} point(s) within safety margin.`,
    );
  }

  return warnings;
}

/**
 * Ionicons icon name for a given warning level.
 */
export function getWarningIcon(level: DepthWarningLevel): string {
  switch (level) {
    case 'danger':  return 'alert-circle';
    case 'caution': return 'warning';
    case 'safe':    return 'checkmark-circle';
  }
}

/**
 * Color for a given warning level, drawn from the app palette.
 */
export function getWarningColor(level: DepthWarningLevel): string {
  switch (level) {
    case 'danger':  return palette.error;
    case 'caution': return palette.warning;
    case 'safe':    return palette.success;
  }
}
