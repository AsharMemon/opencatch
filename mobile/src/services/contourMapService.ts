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
  '#EEF6FB',
  '#DDEAF1',
  '#CBDDE8',
  '#B5CEDD',
  '#97B7CC',
  '#789AB4',
  '#587A97',
  '#3D5F7B',
];

const DEPTH_BREAKS_FT = [0, 2, 4, 7, 11, 18, 30, 48];

export interface SourceBounds {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
}

export interface MartinContourSource {
  id: string;
  label: string;
  bounds?: SourceBounds;
}

export const MARTIN_CONTOUR_SOURCES: MartinContourSource[] = [
  { id: 'al_contours', label: 'Alabama', bounds: { minLon: -88.6, minLat: 30.1, maxLon: -84.7, maxLat: 35.1 } },
  { id: 'ak_contours', label: 'Alaska', bounds: { minLon: -151.1456, minLat: 60.1109, maxLon: -145.6705, maxLat: 62.1833 } },
  { id: 'ar_contours', label: 'Arkansas', bounds: { minLon: -93.5018, minLat: 34.8960, maxLon: -93.1598, maxLat: 35.0026 } },
  { id: 'bc_contours', label: 'British Columbia', bounds: { minLon: -128.2041, minLat: 49.2013, maxLon: -118.5059, maxLat: 59.9034 } },
  { id: 'mo_contours', label: 'Missouri', bounds: { minLon: -90.8600, minLat: 37.1245, maxLon: -90.7596, maxLat: 37.3175 } },
  { id: 'mn_contours', label: 'Minnesota', bounds: { minLon: -97.5, minLat: 43.0, maxLon: -89.0, maxLat: 49.8 } },
  { id: 'on_contours', label: 'Ontario', bounds: { minLon: -95.5, minLat: 41.5, maxLon: -74.0, maxLat: 57.8 } },
  { id: 'qc_contours', label: 'Quebec', bounds: { minLon: -80.5, minLat: 44.0, maxLon: -56.0, maxLat: 63.5 } },
  { id: 'mi_contours', label: 'Michigan', bounds: { minLon: -91.0, minLat: 41.5, maxLon: -82.0, maxLat: 48.8 } },
  { id: 'nh_contours', label: 'New Hampshire', bounds: { minLon: -72.8, minLat: 42.4, maxLon: -70.5, maxLat: 45.5 } },
  { id: 'ct_contours', label: 'Connecticut', bounds: { minLon: -73.8, minLat: 40.9, maxLon: -71.7, maxLat: 42.2 } },
  { id: 'de_contours', label: 'Delaware', bounds: { minLon: -75.80, minLat: 38.43, maxLon: -75.00, maxLat: 39.85 } },
  { id: 'nj_contours', label: 'New Jersey', bounds: { minLon: -75.6, minLat: 38.8, maxLon: -73.8, maxLat: 41.4 } },
  { id: 'ny_contours', label: 'New York', bounds: { minLon: -79.9, minLat: 40.4, maxLon: -71.8, maxLat: 45.2 } },
  { id: 'ri_contours', label: 'Rhode Island', bounds: { minLon: -71.95, minLat: 41.10, maxLon: -71.05, maxLat: 42.05 } },
  { id: 'nd_contours', label: 'North Dakota', bounds: { minLon: -104.0168, minLat: 45.9517, maxLon: -96.8351, maxLat: 49.0148 } },
  { id: 'sd_contours', label: 'South Dakota', bounds: { minLon: -104.0577, minLat: 42.4796, maxLon: -96.4366, maxLat: 45.9455 } },
  { id: 'fl_contours', label: 'Florida', bounds: { minLon: -87.8, minLat: 24.0, maxLon: -79.5, maxLat: 31.5 } },
  { id: 'in_contours', label: 'Indiana', bounds: { minLon: -88.2, minLat: 37.5, maxLon: -84.7, maxLat: 41.9 } },
  { id: 'il_contours', label: 'Illinois', bounds: { minLon: -91.7, minLat: 36.8, maxLon: -87.0, maxLat: 42.6 } },
  { id: 'ks_contours', label: 'Kansas', bounds: { minLon: -102.1, minLat: 36.9, maxLon: -94.5, maxLat: 40.1 } },
  { id: 'ab_contours', label: 'Alberta', bounds: { minLon: -121.0, minLat: 48.8, maxLon: -109.0, maxLat: 60.2 } },
  { id: 'mt_contours', label: 'Montana', bounds: { minLon: -116.5, minLat: 44.0, maxLon: -103.5, maxLat: 49.5 } },
  { id: 'wa_contours', label: 'Washington', bounds: { minLon: -125.5, minLat: 45.3, maxLon: -116.5, maxLat: 49.3 } },
  { id: 'ma_contours', label: 'Massachusetts', bounds: { minLon: -73.8, minLat: 41.0, maxLon: -69.5, maxLat: 43.1 } },
  { id: 'oh_contours', label: 'Ohio', bounds: { minLon: -84.95, minLat: 38.35, maxLon: -80.45, maxLat: 42.35 } },
  { id: 'ok_contours', label: 'Oklahoma', bounds: { minLon: -103.0, minLat: 33.6, maxLon: -94.3, maxLat: 37.1 } },
  { id: 'pa_contours', label: 'Pennsylvania', bounds: { minLon: -80.6, minLat: 39.6, maxLon: -74.6, maxLat: 42.4 } },
  { id: 'ne_contours', label: 'Nebraska', bounds: { minLon: -103.8419, minLat: 40.0178, maxLon: -95.7218, maxLat: 42.9263 } },
  { id: 'tx_contours', label: 'Texas', bounds: { minLon: -98.01, minLat: 30.35, maxLon: -94.60, maxLat: 33.31 } },
  { id: 'va_contours', label: 'Virginia', bounds: { minLon: -83.9, minLat: 36.4, maxLon: -75.1, maxLat: 39.7 } },
  { id: 'vt_contours', label: 'Vermont', bounds: { minLon: -73.6, minLat: 42.6, maxLon: -71.3, maxLat: 45.2 } },
  { id: 'wv_contours', label: 'West Virginia', bounds: { minLon: -82.7, minLat: 37.1, maxLon: -77.7, maxLat: 40.7 } },
  { id: 'ia_contours', label: 'Iowa', bounds: { minLon: -97.3, minLat: 40.2, maxLon: -89.8, maxLat: 43.8 } },
  { id: 'wi_contours', label: 'Wisconsin', bounds: { minLon: -92.7013, minLat: 43.0046, maxLon: -88.9535, maxLat: 46.3899 } },
  { id: 'nb_contours', label: 'New Brunswick', bounds: { minLon: -69.0539, minLat: 44.6148, maxLon: -64.2294, maxLat: 47.9657 } },
  { id: 'ga_pfa_contours', label: 'Georgia PFAs', bounds: { minLon: -85.6052, minLat: 30.3558, maxLon: -80.7514, maxLat: 35.0008 } },
  { id: 'ky_kdfwr_contours', label: 'Kentucky KDFWR', bounds: { minLon: -87.8619, minLat: 36.6730, maxLon: -82.9469, maxLat: 38.9999 } },
  { id: 'ms_contours', label: 'Mississippi', bounds: { minLon: -91.6550, minLat: 30.1739, maxLon: -88.0979, maxLat: 35.0080 } },
  { id: 'nl_contours', label: 'Newfoundland & Labrador', bounds: { minLon: -67.9125, minLat: 46.6353, maxLon: -52.6803, maxLat: 60.3580 } },
  { id: 'nt_contours', label: 'Northwest Territories', bounds: { minLon: -136.4268, minLat: 60.0003, maxLon: -102.0044, maxLat: 77.8412 } },
  { id: 'nu_contours', label: 'Nunavut', bounds: { minLon: -120.33, minLat: 55.9681, maxLon: -64.1314, maxLat: 81.8456 } },
  { id: 'ns_contours', label: 'Nova Scotia', bounds: { minLon: -66.1560, minLat: 43.5784, maxLon: -59.9115, maxLat: 46.8713 } },
  { id: 'pe_contours', label: 'Prince Edward Island', bounds: { minLon: -64.2604, minLat: 45.9557, maxLon: -61.9948, maxLat: 47.0014 } },
  { id: 'me_contours', label: 'Maine', bounds: { minLon: -70.8600, minLat: 43.3421, maxLon: -67.2759, maxLat: 47.3166 } },
  { id: 'mb_contours', label: 'Manitoba', bounds: { minLon: -101.9750, minLat: 49.0000, maxLon: -92.1897, maxLat: 59.9989 } },
  { id: 'sk_contours', label: 'Saskatchewan', bounds: { minLon: -110.2059, minLat: 49.0109, maxLon: -101.4519, maxLat: 59.6982 } },
  { id: 'yt_contours', label: 'Yukon', bounds: { minLon: -141.0045, minLat: 60.0002, maxLon: -124.3006, maxLat: 69.6383 } },
  { id: 'hi_contours', label: 'Hawaii', bounds: { minLon: -159.4642, minLat: 20.7892, maxLon: -156.4688, maxLat: 22.0679 } },
  { id: 'lagos_contours', label: 'LAGOS-US', bounds: { minLon: -127.0, minLat: 24.0, maxLon: -65.0, maxLat: 50.0 } },
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

export type ContourConfidenceFilter = ContourMapConfig['confidenceFilter'];

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

const QUALITY_RANK: Record<string, number> = {
  survey: 5,
  high: 4,
  moderate: 3,
  coarse: 2,
  estimate: 1,
};

const CONTOUR_QUALITY_FILTERS: Record<ContourConfidenceFilter, ContourQuality[]> = {
  all: ['survey', 'high', 'moderate', 'coarse', 'estimate'],
  high: ['survey', 'high', 'moderate', 'coarse'],
  survey_only: ['survey', 'high', 'moderate'],
};

export function getContourQualitiesForFilter(filter: ContourConfidenceFilter): ContourQuality[] {
  return [...CONTOUR_QUALITY_FILTERS[filter]];
}

function pickBestFeature(features: any[]): any | null {
  const candidates = features.filter((feature) => {
    const depth = feature?.properties?.depth_ft;
    return typeof depth === 'number' && Number.isFinite(depth);
  });
  if (!candidates.length) return null;

  let best = candidates[0];
  let bestScore =
    (QUALITY_RANK[String(best?.properties?.contour_quality || 'estimate')] || 0) * 100000 -
    Number(best?.properties?.depth_ft || 0);
  for (const feature of candidates) {
    const quality = QUALITY_RANK[String(feature?.properties?.contour_quality || 'estimate')] || 0;
    const depthFt = Number(feature?.properties?.depth_ft || 0);
    const sourceBonus = String(feature?.properties?.source || '').includes('survey') ? 5000 : 0;
    const score = quality * 100000 + sourceBonus - depthFt;
    if (score > bestScore) {
      best = feature;
      bestScore = score;
    }
  }
  return best;
}

function parseQuality(raw: any): ContourQuality {
  return (raw as ContourQuality) || 'estimate';
}

export function getDepthFromRenderedFeatures(input: any): DepthResult | null {
  const feature = pickBestFeature(toFeatures(input));
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

function intersects(a: SourceBounds, b: SourceBounds): boolean {
  return !(a.maxLon < b.minLon || a.minLon > b.maxLon || a.maxLat < b.minLat || a.minLat > b.maxLat);
}

export function getMartinContourSourcesForBounds(bounds: SourceBounds | null): MartinContourSource[] {
  const lagos = MARTIN_CONTOUR_SOURCES.find((source) => source.id === 'lagos_contours');
  if (!bounds) {
    return lagos ? [lagos] : [];
  }

  const primary = MARTIN_CONTOUR_SOURCES.filter(
    (source) => source.id !== 'lagos_contours' && source.bounds && intersects(source.bounds, bounds),
  );
  const includeLagos = !!(lagos?.bounds && intersects(lagos.bounds, bounds));
  if (includeLagos) {
    return lagos ? [lagos, ...primary] : primary;
  }
  if (primary.length > 0) return primary;
  return lagos ? [lagos] : [];
}

export function hasContoursInView(map: any): boolean {
  return [...getBathyLayerIds('fill'), ...getBathyLayerIds('line')].some((layerId) => {
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
