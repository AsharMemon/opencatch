/**
 * OpenCatch — Contour Map Service
 *
 * Integrates bathymetry contour tiles (PMTiles) into the MapLibre GL map.
 * Loads 15 state/province tilesets (~640 MB total) from B2/CDN and renders:
 * - Filled depth band polygons with PAPERCUT_BLUES palette
 * - Contour line outlines (solid for survey data, dashed for ML-derived)
 * - Depth number labels at band centroids (zoom-dependent + user toggle)
 * - Draft-aware safety overlay (red zones where depth < boat draft)
 * - Lake boundary outlines and per-lake source attribution
 *
 * Competitor parity: Navionics SonarChart tiles.
 * OpenCatch EXCEEDS with 30K+ inland lakes from satellite bathymetry.
 *
 * Settings persist via AsyncStorage.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { palette } from '../theme/palette';
import {
  buildDraftSafetyExpression,
  buildDraftOverlayOpacity,
  metersToFeet,
  feetToMeters,
  type DraftSettings,
} from './draftRouting';
import {
  buildConfidenceLineWidth,
  buildConfidenceLineOpacity,
  buildConfidenceFillOpacity,
  getSourceLabel,
  type ContourQuality,
} from './depthContourSettings';

// ── Constants ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/contour_map_config';

const M_TO_FT = 3.28084;

/** MapLibre map type alias — the actual type comes from the RN MapLibre bindings. */
type MapLibreMap = any;

// ── PMTiles Tileset Registry ─────────────────────────────────────────────────

/**
 * All 15 state/province tilesets served from B2 via CDN.
 * Each tileset covers one state or province and contains vector tiles
 * with depth_contours and lake_boundaries source layers.
 */
const TILESETS: { id: string; filename: string; label: string }[] = [
  { id: 'mn', filename: 'minnesota.pmtiles', label: 'Minnesota' },
  { id: 'wi', filename: 'wisconsin.pmtiles', label: 'Wisconsin' },
  { id: 'mi', filename: 'michigan.pmtiles', label: 'Michigan' },
  { id: 'oh', filename: 'ohio.pmtiles', label: 'Ohio' },
  { id: 'ny', filename: 'new-york.pmtiles', label: 'New York' },
  { id: 'pa', filename: 'pennsylvania.pmtiles', label: 'Pennsylvania' },
  { id: 'me', filename: 'maine.pmtiles', label: 'Maine' },
  { id: 'nh', filename: 'new-hampshire.pmtiles', label: 'New Hampshire' },
  { id: 'vt', filename: 'vermont.pmtiles', label: 'Vermont' },
  { id: 'mt', filename: 'montana.pmtiles', label: 'Montana' },
  { id: 'id', filename: 'idaho.pmtiles', label: 'Idaho' },
  { id: 'wa', filename: 'washington.pmtiles', label: 'Washington' },
  { id: 'or', filename: 'oregon.pmtiles', label: 'Oregon' },
  { id: 'on', filename: 'ontario.pmtiles', label: 'Ontario' },
  { id: 'mb', filename: 'manitoba.pmtiles', label: 'Manitoba' },
];

// ── Layer IDs ────────────────────────────────────────────────────────────────

const LAYER_IDS = {
  fills: 'contour-fills',
  lines: 'contour-lines',
  labels: 'depth-labels',
  draftSafety: 'draft-safety',
  lakeOutlines: 'lake-outlines',
} as const;

// ── PAPERCUT_BLUES Palette ───────────────────────────────────────────────────

/**
 * Depth band fill colors — the "papercut" layered blue look.
 * Ordered shallow (light) to deep (dark), 8 bands.
 */
const PAPERCUT_BLUES = [
  '#D6EAF8', // 0–3 ft
  '#AED6F1', // 3–6 ft
  '#85C1E9', // 6–10 ft
  '#5DADE2', // 10–15 ft
  '#3498DB', // 15–25 ft
  '#2E86C1', // 25–40 ft
  '#2471A3', // 40–60 ft
  '#1A5276', // 60+ ft
];

/**
 * Depth break points in feet for the 8 bands.
 */
const DEPTH_BREAKS_FT = [0, 3, 6, 10, 15, 25, 40, 60];

// ── Types ────────────────────────────────────────────────────────────────────

export interface ContourMapConfig {
  pmtilesBaseUrl: string;
  showDepthNumbers: boolean;
  showContourLines: boolean;
  showFilledBands: boolean;
  showDraftSafety: boolean;
  draftMeters: number;
  depthUnit: 'meters' | 'feet';
  confidenceFilter: 'all' | 'high' | 'survey_only';
}

export interface DepthResult {
  depthFt: number;
  depthM: number;
  confidence: number;
  quality: ContourQuality;
  source: string;
  sourceLabel: string;
  lakeId: string;
  lakeName: string;
}

export interface LakeAttribution {
  lakeId: string;
  lakeName: string;
  source: string;
  sourceLabel: string;
  quality: ContourQuality;
  surveyDate?: string;
}

// ── Defaults ─────────────────────────────────────────────────────────────────

const DEFAULT_CONFIG: ContourMapConfig = {
  pmtilesBaseUrl: __DEV__
    ? 'http://localhost:3000'
    : 'https://tiles.opencatch.app',
  showDepthNumbers: true,
  showContourLines: true,
  showFilledBands: true,
  showDraftSafety: false,
  draftMeters: 0.5,
  depthUnit: 'feet',
  confidenceFilter: 'all',
};

// ── PMTiles Protocol ─────────────────────────────────────────────────────────

let protocolRegistered = false;

/**
 * Register the PMTiles protocol handler with MapLibre.
 * Must be called once before adding any PMTiles sources.
 *
 * Uses the pmtiles.js library which is bundled with the app.
 */
function ensurePMTilesProtocol(map: MapLibreMap): void {
  if (protocolRegistered) return;

  try {
    // pmtiles.js exposes a protocol object compatible with MapLibre
    const pmtiles = require('pmtiles');
    const protocol = new pmtiles.Protocol();

    map.addProtocol('pmtiles', (params: any) => {
      return new Promise((resolve, reject) => {
        const url = params.url.replace('pmtiles://', '');
        protocol
          .tile(params)
          .then((result: any) => resolve(result))
          .catch((err: any) => reject(err));
      });
    });

    protocolRegistered = true;
  } catch (err) {
    console.error('[ContourMap] Failed to register PMTiles protocol:', err);
  }
}

// ── Source Registration ──────────────────────────────────────────────────────

/**
 * Build the PMTiles URL for a given tileset.
 */
function getTilesetUrl(baseUrl: string, filename: string): string {
  return `pmtiles://${baseUrl}/${filename}`;
}

/**
 * Get the source ID for a tileset.
 */
function getSourceId(tilesetId: string): string {
  return `contour-${tilesetId}`;
}

/**
 * Register all 15 PMTiles tilesets as MapLibre vector sources.
 */
function registerSources(map: MapLibreMap, baseUrl: string): void {
  for (const tileset of TILESETS) {
    const sourceId = getSourceId(tileset.id);

    // Skip if already registered
    if (map.getSource(sourceId)) continue;

    map.addSource(sourceId, {
      type: 'vector',
      url: getTilesetUrl(baseUrl, tileset.filename),
      minzoom: 6,
      maxzoom: 16,
    });
  }
}

// ── Layer Definitions ────────────────────────────────────────────────────────

/**
 * Build the fill-color expression for depth band polygons.
 * Maps depth_ft values to PAPERCUT_BLUES colors using step interpolation.
 */
function buildFillColorExpression(): any[] {
  const expr: any[] = [
    'step',
    ['coalesce', ['get', 'depth_ft'], 0],
    PAPERCUT_BLUES[0],
  ];

  for (let i = 1; i < DEPTH_BREAKS_FT.length; i++) {
    expr.push(DEPTH_BREAKS_FT[i], PAPERCUT_BLUES[i]);
  }

  return expr;
}

/**
 * Build the line-dasharray expression based on contour quality.
 * Survey data = solid, ML-derived = dashed.
 */
function buildConfidenceDashExpression(): any[] {
  return [
    'match',
    ['get', 'contour_quality'],
    'survey', ['literal', [1]],
    'high', ['literal', [1]],
    'moderate', ['literal', [6, 3]],
    'coarse', ['literal', [4, 4]],
    'estimate', ['literal', [2, 4]],
    ['literal', [4, 3]], // fallback
  ];
}

/**
 * Build a confidence filter expression.
 */
function buildConfidenceFilterExpr(level: string): any[] | null {
  switch (level) {
    case 'high':
      return ['in', ['get', 'contour_quality'], ['literal', ['survey', 'high']]];
    case 'survey_only':
      return ['==', ['get', 'contour_quality'], 'survey'];
    default:
      return null; // no filter — show all
  }
}

/**
 * Build the depth label text expression.
 * Shows depth in the configured unit with appropriate formatting.
 */
function buildLabelTextExpression(unit: 'meters' | 'feet'): any[] {
  if (unit === 'feet') {
    return [
      'concat',
      ['to-string', ['round', ['coalesce', ['get', 'depth_ft'], 0]]],
      '\'',
    ];
  }
  return [
    'concat',
    ['to-string', ['round', ['*', ['coalesce', ['get', 'depth_ft'], 0], 0.3048]]],
    'm',
  ];
}

/**
 * Build zoom-dependent label density filter.
 * Shows fewer labels at low zoom, more as you zoom in.
 */
function buildLabelDensityFilter(): any[] {
  return [
    'step',
    ['zoom'],
    // zoom < 10: only major contours (multiples of 25ft)
    ['==', ['%', ['coalesce', ['get', 'depth_ft'], 0], 25], 0],
    10,
    // zoom 10-12: multiples of 10ft
    ['==', ['%', ['coalesce', ['get', 'depth_ft'], 0], 10], 0],
    12,
    // zoom 12-14: multiples of 5ft
    ['==', ['%', ['coalesce', ['get', 'depth_ft'], 0], 5], 0],
    14,
    // zoom 14+: all labels
    true,
  ];
}

// ── Add Layers ───────────────────────────────────────────────────────────────

/**
 * Add all contour map layers for a single source.
 * Layers are added in correct rendering order (bottom to top):
 *   1. Filled depth bands
 *   2. Lake boundary outlines
 *   3. Contour lines
 *   4. Draft safety overlay
 *   5. Depth labels (topmost)
 */
function addLayersForSource(
  map: MapLibreMap,
  sourceId: string,
  config: ContourMapConfig,
  draftSettings: DraftSettings,
): void {
  const suffix = sourceId.replace('contour-', '');

  // 1. Filled depth band polygons
  map.addLayer({
    id: `${LAYER_IDS.fills}-${suffix}`,
    type: 'fill',
    source: sourceId,
    'source-layer': 'depth_contours',
    paint: {
      'fill-color': buildFillColorExpression(),
      'fill-opacity': buildConfidenceFillOpacity(0.7),
    },
    layout: {
      visibility: config.showFilledBands ? 'visible' : 'none',
    },
    minzoom: 8,
  });

  // 2. Lake boundary outlines
  map.addLayer({
    id: `${LAYER_IDS.lakeOutlines}-${suffix}`,
    type: 'line',
    source: sourceId,
    'source-layer': 'lake_boundaries',
    paint: {
      'line-color': palette.accentDeep,
      'line-width': [
        'interpolate', ['linear'], ['zoom'],
        8, 0.5,
        12, 1,
        16, 1.5,
      ],
      'line-opacity': 0.6,
    },
    minzoom: 7,
  });

  // 3. Contour lines
  map.addLayer({
    id: `${LAYER_IDS.lines}-${suffix}`,
    type: 'line',
    source: sourceId,
    'source-layer': 'depth_contours',
    paint: {
      'line-color': palette.waterDeep,
      'line-width': buildConfidenceLineWidth(),
      'line-opacity': buildConfidenceLineOpacity(),
      'line-dasharray': buildConfidenceDashExpression(),
    },
    layout: {
      visibility: config.showContourLines ? 'visible' : 'none',
    },
    minzoom: 9,
  });

  // 4. Draft safety overlay
  map.addLayer({
    id: `${LAYER_IDS.draftSafety}-${suffix}`,
    type: 'fill',
    source: sourceId,
    'source-layer': 'depth_contours',
    paint: {
      'fill-color': buildDraftSafetyExpression(draftSettings),
      'fill-opacity': buildDraftOverlayOpacity(),
    },
    layout: {
      visibility: config.showDraftSafety ? 'visible' : 'none',
    },
    minzoom: 8,
  });

  // 5. Depth labels
  map.addLayer({
    id: `${LAYER_IDS.labels}-${suffix}`,
    type: 'symbol',
    source: sourceId,
    'source-layer': 'depth_contours',
    filter: buildLabelDensityFilter(),
    layout: {
      'text-field': buildLabelTextExpression(config.depthUnit),
      'text-size': [
        'interpolate', ['linear'], ['zoom'],
        10, 9,
        13, 11,
        16, 14,
      ],
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
      'text-allow-overlap': false,
      'text-ignore-placement': false,
      'text-padding': 8,
      'symbol-placement': 'point',
      visibility: config.showDepthNumbers ? 'visible' : 'none',
    },
    paint: {
      'text-color': palette.waterDeep,
      'text-halo-color': palette.surface,
      'text-halo-width': 1.5,
      'text-opacity': [
        'interpolate', ['linear'], ['zoom'],
        9, 0,
        10, 0.7,
        13, 1,
      ],
    },
    minzoom: 10,
  });
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Initialize all contour map sources and layers.
 * Call this once after the MapLibre map is loaded.
 */
export function initContourSources(map: MapLibreMap): void {
  const configPromise = loadContourConfig();
  const draftPromise = loadDraftSettingsForContours();

  Promise.all([configPromise, draftPromise])
    .then(([config, draftSettings]) => {
      ensurePMTilesProtocol(map);
      registerSources(map, config.pmtilesBaseUrl);

      for (const tileset of TILESETS) {
        const sourceId = getSourceId(tileset.id);
        addLayersForSource(map, sourceId, config, draftSettings);
      }

      // Apply confidence filter if not "all"
      if (config.confidenceFilter !== 'all') {
        applyConfidenceFilter(map, config.confidenceFilter);
      }
    })
    .catch((err) => {
      console.error('[ContourMap] Failed to initialize contour sources:', err);
    });
}

/**
 * Toggle depth number labels on or off.
 */
export function toggleDepthNumbers(map: MapLibreMap, visible: boolean): void {
  const visibility = visible ? 'visible' : 'none';

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.labels}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'visibility', visibility);
    }
  }

  // Persist preference
  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, showDepthNumbers: visible });
  });
}

/**
 * Toggle contour line visibility.
 */
export function toggleContourLines(map: MapLibreMap, visible: boolean): void {
  const visibility = visible ? 'visible' : 'none';

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.lines}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'visibility', visibility);
    }
  }

  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, showContourLines: visible });
  });
}

/**
 * Toggle filled depth band polygons.
 */
export function toggleFilledBands(map: MapLibreMap, visible: boolean): void {
  const visibility = visible ? 'visible' : 'none';

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.fills}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'visibility', visibility);
    }
  }

  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, showFilledBands: visible });
  });
}

/**
 * Toggle draft safety overlay.
 */
export function toggleDraftSafety(map: MapLibreMap, visible: boolean): void {
  const visibility = visible ? 'visible' : 'none';

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.draftSafety}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'visibility', visibility);
    }
  }

  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, showDraftSafety: visible });
  });
}

/**
 * Switch between metric (meters) and imperial (feet) depth labels.
 */
export function setDepthUnit(map: MapLibreMap, unit: 'meters' | 'feet'): void {
  const textExpr = buildLabelTextExpression(unit);

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.labels}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'text-field', textExpr);
    }
  }

  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, depthUnit: unit });
  });
}

/**
 * Filter contours by data confidence level.
 * - 'all': show everything
 * - 'high': survey + high confidence only
 * - 'survey_only': only verified survey data
 */
export function setConfidenceFilter(map: MapLibreMap, level: string): void {
  applyConfidenceFilter(map, level);

  loadContourConfig().then((config) => {
    saveContourConfig({
      ...config,
      confidenceFilter: level as ContourMapConfig['confidenceFilter'],
    });
  });
}

/**
 * Apply a confidence filter across all tileset layers.
 */
function applyConfidenceFilter(map: MapLibreMap, level: string): void {
  const filterExpr = buildConfidenceFilterExpr(level);
  const layerPrefixes = [
    LAYER_IDS.fills,
    LAYER_IDS.lines,
    LAYER_IDS.labels,
    LAYER_IDS.draftSafety,
  ];

  for (const tileset of TILESETS) {
    for (const prefix of layerPrefixes) {
      const layerId = `${prefix}-${tileset.id}`;
      if (!map.getLayer(layerId)) continue;

      if (filterExpr) {
        // For labels, combine with density filter
        if (prefix === LAYER_IDS.labels) {
          map.setFilter(layerId, ['all', filterExpr, buildLabelDensityFilter()]);
        } else {
          map.setFilter(layerId, filterExpr);
        }
      } else {
        // Reset to default filter
        if (prefix === LAYER_IDS.labels) {
          map.setFilter(layerId, buildLabelDensityFilter());
        } else {
          map.setFilter(layerId, null);
        }
      }
    }
  }
}

/**
 * Update the draft safety overlay when the user changes boat draft.
 */
export function updateDraftOverlay(
  map: MapLibreMap,
  draftSettings: DraftSettings,
): void {
  const safetyExpr = buildDraftSafetyExpression(draftSettings);

  for (const tileset of TILESETS) {
    const layerId = `${LAYER_IDS.draftSafety}-${tileset.id}`;
    if (map.getLayer(layerId)) {
      map.setPaintProperty(layerId, 'fill-color', safetyExpr);
    }
  }

  loadContourConfig().then((config) => {
    saveContourConfig({ ...config, draftMeters: draftSettings.draftM });
  });
}

// ── Touch Interaction ────────────────────────────────────────────────────────

/**
 * Get depth information at a tap point on the map.
 * Queries all contour fill layers for features under the given coordinates.
 *
 * Returns the shallowest depth result at that point, or null if no data.
 */
export function getDepthAtPoint(
  map: MapLibreMap,
  lngLat: [number, number],
): DepthResult | null {
  // Query all contour fill layers for features at the tapped point
  const fillLayerIds = TILESETS.map((t) => `${LAYER_IDS.fills}-${t.id}`).filter(
    (id) => map.getLayer(id),
  );

  if (fillLayerIds.length === 0) return null;

  const point = map.project({ lng: lngLat[0], lat: lngLat[1] });
  const features = map.queryRenderedFeatures(point, { layers: fillLayerIds });

  if (!features || features.length === 0) return null;

  // Find the feature with the most specific (shallowest) depth
  let bestFeature = features[0];
  let bestDepth = bestFeature.properties?.depth_ft ?? Infinity;

  for (const feature of features) {
    const depthFt = feature.properties?.depth_ft ?? Infinity;
    if (depthFt < bestDepth) {
      bestDepth = depthFt;
      bestFeature = feature;
    }
  }

  const props = bestFeature.properties || {};
  const depthFt = props.depth_ft ?? 0;
  const depthM = depthFt * 0.3048;

  return {
    depthFt,
    depthM: Math.round(depthM * 10) / 10,
    confidence: props.confidence ?? 0.5,
    quality: (props.contour_quality as ContourQuality) || 'estimate',
    source: props.source || 'unknown',
    sourceLabel: getSourceLabel(props.source || 'unknown'),
    lakeId: props.lake_id || '',
    lakeName: props.lake_name || '',
  };
}

/**
 * Get attribution info for a specific lake.
 * Queries the lake boundary layer at the given point.
 */
export function getLakeAttribution(
  map: MapLibreMap,
  lngLat: [number, number],
): LakeAttribution | null {
  const outlineLayerIds = TILESETS.map(
    (t) => `${LAYER_IDS.lakeOutlines}-${t.id}`,
  ).filter((id) => map.getLayer(id));

  if (outlineLayerIds.length === 0) return null;

  const point = map.project({ lng: lngLat[0], lat: lngLat[1] });

  // Query fills instead — boundaries are lines, which are harder to hit-test
  const fillLayerIds = TILESETS.map((t) => `${LAYER_IDS.fills}-${t.id}`).filter(
    (id) => map.getLayer(id),
  );
  const features = map.queryRenderedFeatures(point, { layers: fillLayerIds });

  if (!features || features.length === 0) return null;

  const props = features[0].properties || {};
  const source = props.source || 'unknown';

  return {
    lakeId: props.lake_id || '',
    lakeName: props.lake_name || '',
    source,
    sourceLabel: getSourceLabel(source),
    quality: (props.contour_quality as ContourQuality) || 'estimate',
    surveyDate: props.survey_date || undefined,
  };
}

// ── Layer Cleanup ────────────────────────────────────────────────────────────

/**
 * Remove all contour map sources and layers from the map.
 * Call before unmounting the map component.
 */
export function removeContourLayers(map: MapLibreMap): void {
  const allPrefixes = Object.values(LAYER_IDS);

  for (const tileset of TILESETS) {
    // Remove layers (reverse order to avoid dependency issues)
    for (const prefix of [...allPrefixes].reverse()) {
      const layerId = `${prefix}-${tileset.id}`;
      if (map.getLayer(layerId)) {
        map.removeLayer(layerId);
      }
    }

    // Remove source
    const sourceId = getSourceId(tileset.id);
    if (map.getSource(sourceId)) {
      map.removeSource(sourceId);
    }
  }
}

// ── Persistence ──────────────────────────────────────────────────────────────

/** Load contour map config from AsyncStorage. Falls back to defaults. */
export async function loadContourConfig(): Promise<ContourMapConfig> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_CONFIG, ...parsed };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

/** Save contour map config to AsyncStorage. */
export async function saveContourConfig(config: ContourMapConfig): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

/** Reset contour map config to defaults. */
export async function resetContourConfig(): Promise<ContourMapConfig> {
  const defaults = { ...DEFAULT_CONFIG };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(defaults));
  return defaults;
}

/**
 * Load draft settings for contour overlay initialization.
 * Reads from the draftRouting storage key as a convenience.
 */
async function loadDraftSettingsForContours(): Promise<DraftSettings> {
  try {
    const raw = await AsyncStorage.getItem('@opencatch/draft_routing_settings');
    if (!raw) return { draftM: 0.5, safetyMarginM: 0.5, showUnsafeZones: true, showRoute: true };
    return JSON.parse(raw);
  } catch {
    return { draftM: 0.5, safetyMarginM: 0.5, showUnsafeZones: true, showRoute: true };
  }
}

// ── Utility Helpers ──────────────────────────────────────────────────────────

/**
 * Get the list of registered tilesets and their labels.
 * Useful for the settings UI to show which regions have data.
 */
export function getAvailableTilesets(): { id: string; label: string }[] {
  return TILESETS.map((t) => ({ id: t.id, label: t.label }));
}

/**
 * Check if contour data is currently loaded for a given map viewport.
 * Returns true if any contour source has tiles loaded at the current zoom.
 */
export function hasContoursInView(map: MapLibreMap): boolean {
  for (const tileset of TILESETS) {
    const sourceId = getSourceId(tileset.id);
    const source = map.getSource(sourceId);
    if (source && map.isSourceLoaded(sourceId)) {
      return true;
    }
  }
  return false;
}

/**
 * Format a depth value for display.
 */
export function formatDepth(depthFt: number, unit: 'meters' | 'feet'): string {
  if (unit === 'meters') {
    const m = depthFt * 0.3048;
    return `${m.toFixed(1)}m`;
  }
  return `${Math.round(depthFt)}'`;
}

/**
 * Get Ionicons icon name for a data quality level.
 */
export function getQualityIcon(quality: ContourQuality): string {
  switch (quality) {
    case 'survey':   return 'checkmark-circle';
    case 'high':     return 'checkmark-circle-outline';
    case 'moderate': return 'ellipse-outline';
    case 'coarse':   return 'alert-circle-outline';
    case 'estimate': return 'help-circle-outline';
  }
}

/**
 * Get the PAPERCUT_BLUES color for a given depth in feet.
 */
export function getColorForDepth(depthFt: number): string {
  for (let i = DEPTH_BREAKS_FT.length - 1; i >= 0; i--) {
    if (depthFt >= DEPTH_BREAKS_FT[i]) {
      return PAPERCUT_BLUES[i];
    }
  }
  return PAPERCUT_BLUES[0];
}

/**
 * Get all depth bands with their colors (for legend rendering).
 */
export function getDepthBandLegend(
  unit: 'meters' | 'feet',
): { color: string; label: string }[] {
  return PAPERCUT_BLUES.map((color, i) => {
    const minFt = DEPTH_BREAKS_FT[i];
    const maxFt = i < DEPTH_BREAKS_FT.length - 1 ? DEPTH_BREAKS_FT[i + 1] : null;

    let label: string;
    if (unit === 'feet') {
      label = maxFt !== null ? `${minFt}–${maxFt}'` : `${minFt}'+`;
    } else {
      const minM = Math.round(minFt * 0.3048);
      const maxM = maxFt !== null ? Math.round(maxFt * 0.3048) : null;
      label = maxM !== null ? `${minM}–${maxM}m` : `${minM}m+`;
    }

    return { color, label };
  });
}
