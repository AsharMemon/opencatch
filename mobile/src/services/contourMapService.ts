/**
 * OpenCatch — Contour Map Helpers
 *
 * Shared helpers for the live inland bathymetry path rendered directly in
 * MapScreen from Martin-served vector contour sources.
 *
 * This module intentionally does not mount its own map sources/layers anymore.
 * The old PMTiles-only implementation drifted out of sync with the actual app
 * runtime. Rendering now lives in MapScreen, while this file owns:
 * - the canonical inland contour source registry
 * - layer id helpers
 * - tap hit-test parsing for depth + lake attribution
 * - persisted contour UI config helpers
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { TILE_BASE_URL } from '../config/network';
import { getSourceLabel, type ContourQuality } from './depthContourSettings';

const STORAGE_KEY = '@opencatch/contour_map_config';

const PAPERCUT_BLUES = [
  '#D6EAF8',
  '#AED6F1',
  '#85C1E9',
  '#5DADE2',
  '#3498DB',
  '#2E86C1',
  '#2471A3',
  '#1A5276',
];

const DEPTH_BREAKS_FT = [0, 3, 6, 10, 15, 25, 40, 60];

export const MARTIN_CONTOUR_SOURCES: { id: string; label: string }[] = [
  { id: 'mn_contours', label: 'Minnesota' },
  { id: 'on_contours', label: 'Ontario' },
  { id: 'qc_contours', label: 'Quebec' },
  { id: 'mi_contours', label: 'Michigan' },
  { id: 'nh_contours', label: 'New Hampshire' },
  { id: 'fl_contours', label: 'Florida' },
  { id: 'ab_contours', label: 'Alberta' },
  { id: 'mt_contours', label: 'Montana' },
  { id: 'wa_contours', label: 'Washington' },
  { id: 'ma_contours', label: 'Massachusetts' },
  { id: 'ne_contours', label: 'Nebraska' },
  { id: 'vt_contours', label: 'Vermont' },
  { id: 'ia_contours', label: 'Iowa' },
  { id: 'lagos_contours', label: 'LAGOS-US' },
];

export type BathyLayerKind = 'source' | 'fill' | 'line' | 'label';

export interface ContourMapConfig {
  tileBaseUrl: string;
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

const DEFAULT_CONFIG: ContourMapConfig = {
  tileBaseUrl: TILE_BASE_URL,
  showDepthNumbers: true,
  showContourLines: true,
  showFilledBands: true,
  showDraftSafety: false,
  draftMeters: 0.5,
  depthUnit: 'feet',
  confidenceFilter: 'all',
};

export function buildBathyLayerId(kind: BathyLayerKind, sourceId: string): string {
  switch (kind) {
    case 'source':
      return `bathy-${sourceId}`;
    case 'fill':
      return `bathy-fill-${sourceId}`;
    case 'line':
      return `bathy-line-${sourceId}`;
    case 'label':
      return `bathy-label-${sourceId}`;
  }
}

export function getBathyLayerIds(kind: Exclude<BathyLayerKind, 'source'>): string[] {
  return MARTIN_CONTOUR_SOURCES.map(({ id }) => buildBathyLayerId(kind, id));
}

function toFeatures(input: any): any[] {
  if (Array.isArray(input)) return input;
  if (Array.isArray(input?.features)) return input.features;
  return [];
}

function pickShallowestFeature(features: any[]): any | null {
  const candidates = features.filter((feature) => {
    const depth = feature?.properties?.depth_ft;
    return typeof depth === 'number' && Number.isFinite(depth);
  });
  if (!candidates.length) return null;

  let best = candidates[0];
  let bestDepth = best.properties.depth_ft;
  for (const feature of candidates) {
    const depthFt = feature.properties.depth_ft;
    if (depthFt < bestDepth) {
      best = feature;
      bestDepth = depthFt;
    }
  }
  return best;
}

function parseQuality(raw: any): ContourQuality {
  return (raw as ContourQuality) || 'estimate';
}

export function getDepthFromRenderedFeatures(input: any): DepthResult | null {
  const feature = pickShallowestFeature(toFeatures(input));
  if (!feature) return null;

  const props = feature.properties ?? {};
  const depthFt = Number(props.depth_ft ?? 0);
  const depthM = depthFt * 0.3048;
  const source = String(props.source || 'unknown');

  return {
    depthFt,
    depthM: Math.round(depthM * 10) / 10,
    confidence: Number(props.confidence ?? 0.5),
    quality: parseQuality(props.contour_quality),
    source,
    sourceLabel: getSourceLabel(source),
    lakeId: String(props.lake_id || ''),
    lakeName: String(props.lake_name || ''),
  };
}

export function getLakeAttributionFromRenderedFeatures(input: any): LakeAttribution | null {
  const features = toFeatures(input);
  if (!features.length) return null;

  const feature =
    features.find((f) => f?.properties?.lake_id || f?.properties?.lake_name) ??
    features[0];

  const props = feature?.properties ?? {};
  const source = String(props.source || 'unknown');

  return {
    lakeId: String(props.lake_id || ''),
    lakeName: String(props.lake_name || ''),
    source,
    sourceLabel: getSourceLabel(source),
    quality: parseQuality(props.contour_quality),
    surveyDate: props.survey_date ? String(props.survey_date) : undefined,
  };
}

// Legacy exports kept as no-ops so older callers don't break if reintroduced.
export function initContourSources(_map: any): void {}
export function toggleDepthNumbers(_map: any, _visible: boolean): void {}
export function toggleContourLines(_map: any, _visible: boolean): void {}
export function toggleFilledBands(_map: any, _visible: boolean): void {}
export function toggleDraftSafety(_map: any, _visible: boolean): void {}
export function setDepthUnit(_map: any, _unit: 'meters' | 'feet'): void {}
export function setConfidenceFilter(_map: any, _level: string): void {}
export function updateDraftOverlay(_map: any, _draftSettings: any): void {}
export function removeContourLayers(_map: any): void {}

export async function loadContourConfig(): Promise<ContourMapConfig> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export async function saveContourConfig(config: ContourMapConfig): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

export async function resetContourConfig(): Promise<ContourMapConfig> {
  const defaults = { ...DEFAULT_CONFIG };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(defaults));
  return defaults;
}

export function getAvailableTilesets(): { id: string; label: string }[] {
  return [...MARTIN_CONTOUR_SOURCES];
}

export function hasContoursInView(map: any): boolean {
  return getBathyLayerIds('fill').some((layerId) => {
    try {
      return !!map?.getLayer?.(layerId);
    } catch {
      return false;
    }
  });
}

export function formatDepth(depthFt: number, unit: 'meters' | 'feet'): string {
  if (unit === 'meters') {
    return `${(depthFt * 0.3048).toFixed(1)}m`;
  }
  return `${Math.round(depthFt)} ft`;
}

export function getQualityIcon(quality: ContourQuality): string {
  switch (quality) {
    case 'survey':
      return 'checkmark-circle';
    case 'high':
      return 'checkmark-circle-outline';
    case 'moderate':
      return 'ellipse-outline';
    case 'coarse':
      return 'alert-circle-outline';
    case 'estimate':
      return 'help-circle-outline';
  }
}

export function getColorForDepth(depthFt: number): string {
  for (let i = DEPTH_BREAKS_FT.length - 1; i >= 0; i--) {
    if (depthFt >= DEPTH_BREAKS_FT[i]) {
      return PAPERCUT_BLUES[i];
    }
  }
  return PAPERCUT_BLUES[0];
}

export function getDepthBandLegend(
  unit: 'meters' | 'feet',
): { color: string; label: string }[] {
  return PAPERCUT_BLUES.map((color, i) => {
    const minFt = DEPTH_BREAKS_FT[i];
    const maxFt = i < DEPTH_BREAKS_FT.length - 1 ? DEPTH_BREAKS_FT[i + 1] : null;
    if (unit === 'feet') {
      return {
        color,
        label: maxFt !== null ? `${minFt}-${maxFt} ft` : `${minFt}+ ft`,
      };
    }
    const minM = Math.round(minFt * 0.3048);
    const maxM = maxFt !== null ? Math.round(maxFt * 0.3048) : null;
    return {
      color,
      label: maxM !== null ? `${minM}-${maxM} m` : `${minM}+ m`,
    };
  });
}
