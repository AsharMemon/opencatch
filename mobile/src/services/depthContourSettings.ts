/**
 * OpenCatch — Depth Contour Settings Service
 *
 * Lets users customize how bathymetry contours are displayed on the map:
 * - Contour interval (1ft, 2ft, 5ft, 10ft, 25ft)
 * - Color scheme (Classic Blue, Papercut, Thermal, Grayscale, Custom)
 * - Opacity (0.0 – 1.0)
 * - Depth label visibility
 *
 * Settings persist via AsyncStorage.
 *
 * Design goal: readable chart-first styling with soft, low-fatigue colors.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Constants ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/depth_contour_settings';

// ── Types ──────────────────────────────────────────────────────────────────────

export type ContourInterval = 1 | 2 | 5 | 10 | 25;

export type ContourColorScheme =
  | 'classic-blue'
  | 'papercut'
  | 'thermal'
  | 'grayscale'
  | 'custom';

export interface ContourCustomColors {
  shallow: string; // hex
  deep: string;    // hex
}

export interface DepthContourSettings {
  settingsVersion?: number;
  interval: ContourInterval;
  colorScheme: ContourColorScheme;
  opacity: number; // 0.0 – 1.0
  showLabels: boolean;
  customColors: ContourCustomColors;
}

// ── Defaults ───────────────────────────────────────────────────────────────────

export const DEFAULT_CONTOUR_SETTINGS: DepthContourSettings = {
  settingsVersion: 3,
  interval: 1,
  colorScheme: 'papercut',
  opacity: 0.85,
  showLabels: true,
  customColors: {
    shallow: '#CFE0EA',
    deep: '#385E7C',
  },
};

// ── Interval Options ───────────────────────────────────────────────────────────

export const CONTOUR_INTERVALS: { value: ContourInterval; label: string }[] = [
  { value: 1, label: '1 ft' },
  { value: 2, label: '2 ft' },
  { value: 5, label: '5 ft' },
  { value: 10, label: '10 ft' },
  { value: 25, label: '25 ft' },
];

// ── Color Schemes ──────────────────────────────────────────────────────────────

export interface ColorSchemeOption {
  key: ContourColorScheme;
  label: string;
  description: string;
  /** Gradient stops from shallow to deep (for preview rendering). */
  stops: string[];
}

export const COLOR_SCHEMES: ColorSchemeOption[] = [
  {
    key: 'classic-blue',
    label: 'Classic Blue',
    description: 'Quiet nautical blues with gentle contrast',
    stops: ['#EEF6FB', '#D9E9F3', '#BDD4E4', '#90B7CF', '#668CA9', '#456A88'],
  },
  {
    key: 'papercut',
    label: 'Papercut',
    description: 'Layered paper bands with soft depth separation',
    stops: ['#F7FBFC', '#ECF4F8', '#DDEAF1', '#CBDDE8', '#B5CEDD', '#97B7CC', '#789AB4', '#587A97', '#3D5F7B'],
  },
  {
    key: 'thermal',
    label: 'Thermal',
    description: 'Blue to green to yellow to red heatmap',
    stops: ['#2166AC', '#67A9CF', '#D1E5F0', '#FDDBC7', '#EF8A62', '#B2182B'],
  },
  {
    key: 'grayscale',
    label: 'Grayscale',
    description: 'White to black, minimal contrast',
    stops: ['#F5F5F5', '#D9D9D9', '#BDBDBD', '#969696', '#636363', '#252525'],
  },
  {
    key: 'custom',
    label: 'Custom',
    description: 'Pick your own shallow and deep colors',
    stops: [], // Filled from user preferences
  },
];

// ── Color Interpolation ────────────────────────────────────────────────────────

/** Parse a hex color to [r, g, b]. */
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

/** Convert [r, g, b] to hex string. */
function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/** Generate N evenly-spaced colors between two hex values. */
export function interpolateColors(shallow: string, deep: string, steps: number): string[] {
  const [r1, g1, b1] = hexToRgb(shallow);
  const [r2, g2, b2] = hexToRgb(deep);
  const result: string[] = [];
  for (let i = 0; i < steps; i++) {
    const t = steps === 1 ? 0 : i / (steps - 1);
    result.push(rgbToHex(
      r1 + (r2 - r1) * t,
      g1 + (g2 - g1) * t,
      b1 + (b2 - b1) * t,
    ));
  }
  return result;
}

// ── MapLibre Style Expression Builder ──────────────────────────────────────────

/**
 * Build a MapLibre line-color expression for depth contours based on settings.
 * Uses "depth_ft" property from the vector tile source.
 */
export function buildContourColorExpression(settings: DepthContourSettings): any[] {
  const stops = getColorStops(settings);
  // Map depth_ft values to color stops
  // Using 6 stops: 0, 5, 15, 30, 60, 100+ ft
  const depthBreaks = [0, 5, 15, 30, 60, 100];

  const expression: any[] = ['interpolate', ['linear'], ['coalesce', ['get', 'depth_ft'], 0]];
  for (let i = 0; i < Math.min(stops.length, depthBreaks.length); i++) {
    expression.push(depthBreaks[i], stops[i]);
  }
  return expression;
}

/**
 * Build a MapLibre line-width expression based on contour interval.
 * Major contours (multiples of the interval) are thicker.
 */
export function buildContourWidthExpression(settings: DepthContourSettings): any[] {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    6, settings.interval <= 2 ? 0.3 : 0.5,
    10, settings.interval <= 2 ? 0.8 : 1,
    14, settings.interval <= 2 ? 1.8 : 2.4,
  ];
}

/** Get the resolved color stops for a given settings object. */
export function getColorStops(settings: DepthContourSettings): string[] {
  switch (settings.colorScheme) {
    case 'classic-blue':
      return COLOR_SCHEMES[0].stops;
    case 'papercut':
      return COLOR_SCHEMES[1].stops;
    case 'thermal':
      return COLOR_SCHEMES[2].stops;
    case 'grayscale':
      return COLOR_SCHEMES[3].stops;
    case 'custom':
      return interpolateColors(settings.customColors.shallow, settings.customColors.deep, 6);
    default:
      return COLOR_SCHEMES[0].stops;
  }
}

// ── Persistence ────────────────────────────────────────────────────────────────

/** Load depth contour settings from AsyncStorage. Falls back to defaults. */
export async function loadContourSettings(): Promise<DepthContourSettings> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CONTOUR_SETTINGS };
    const currentSettingsVersion = DEFAULT_CONTOUR_SETTINGS.settingsVersion ?? 3;
    const parsed = JSON.parse(raw);
    const merged: DepthContourSettings = {
      ...DEFAULT_CONTOUR_SETTINGS,
      ...parsed,
      customColors: {
        ...DEFAULT_CONTOUR_SETTINGS.customColors,
        ...(parsed.customColors ?? {}),
      },
    };
    if (!parsed.settingsVersion && merged.colorScheme === 'papercut') {
      if (parsed.interval === 2) merged.interval = 1;
      if (parsed.opacity == null || parsed.opacity >= 0.9) merged.opacity = 0.85;
      merged.settingsVersion = currentSettingsVersion;
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
    }
    if ((parsed.settingsVersion ?? 0) < currentSettingsVersion) {
      if (parsed.colorScheme === 'papercut' && parsed.opacity >= 0.9) {
        merged.opacity = DEFAULT_CONTOUR_SETTINGS.opacity;
      }
      merged.settingsVersion = currentSettingsVersion;
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
    }
    return merged;
  } catch {
    return { ...DEFAULT_CONTOUR_SETTINGS };
  }
}

/** Save depth contour settings to AsyncStorage. */
export async function saveContourSettings(settings: DepthContourSettings): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

/** Reset depth contour settings to defaults. */
export async function resetContourSettings(): Promise<DepthContourSettings> {
  const defaults = { ...DEFAULT_CONTOUR_SETTINGS };
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(defaults));
  return defaults;
}

// ── Confidence-Adaptive Styling ──────────────────────────────────────────────

/**
 * Contour quality levels from the bathymetry pipeline.
 * Each contour polygon carries a `contour_quality` property.
 */
export type ContourQuality = 'survey' | 'high' | 'moderate' | 'coarse' | 'estimate';

/**
 * Build MapLibre line style that adapts to data confidence.
 * Survey data gets solid, thick lines. Estimates get thin, dashed lines.
 */
export function buildConfidenceLineWidth(): any[] {
  return [
    'match',
    ['get', 'contour_quality'],
    'survey', 2.0,
    'high', 1.5,
    'moderate', 1.0,
    'coarse', 0.7,
    'estimate', 0.5,
    1.0, // fallback
  ];
}

/**
 * Build MapLibre line opacity that fades with lower confidence.
 */
export function buildConfidenceLineOpacity(): any[] {
  return [
    'interpolate',
    ['linear'],
    ['coalesce', ['get', 'confidence'], 0.5],
    0.0, 0.25,
    0.3, 0.5,
    0.7, 0.8,
    1.0, 1.0,
  ];
}

/**
 * Build MapLibre fill opacity for filled contours.
 * Higher confidence = more opaque fills.
 */
export function buildConfidenceFillOpacity(baseOpacity: number): any[] {
  return [
    'interpolate',
    ['linear'],
    ['coalesce', ['get', 'confidence'], 0.5],
    0.0, baseOpacity * 0.3,
    0.5, baseOpacity * 0.6,
    1.0, baseOpacity,
  ];
}

/**
 * Data quality overlay colors (green = survey, red = estimate).
 * Used for the "Data Quality" toggle layer.
 */
export const DATA_QUALITY_COLORS: Record<ContourQuality, string> = {
  survey: '#4CAF50',    // green
  high: '#8BC34A',      // light green
  moderate: '#FFC107',  // yellow
  coarse: '#FF9800',    // orange
  estimate: '#F44336',  // red
};

/**
 * Build the data quality overlay fill-color expression.
 */
export function buildDataQualityColorExpression(): any[] {
  return [
    'match',
    ['get', 'contour_quality'],
    'survey', DATA_QUALITY_COLORS.survey,
    'high', DATA_QUALITY_COLORS.high,
    'moderate', DATA_QUALITY_COLORS.moderate,
    'coarse', DATA_QUALITY_COLORS.coarse,
    'estimate', DATA_QUALITY_COLORS.estimate,
    '#9E9E9E', // fallback gray
  ];
}

/**
 * Get human-readable source label for display.
 */
export function getSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    ak_survey: 'AK Survey',
    ar_survey: 'AR Survey',
    bc_survey: 'BC Survey',
    de_survey: 'DE Survey',
    hi_hydrolakes: 'HI HydroLAKES',
    mo_depth_points: 'MO Soundings',
    mn_dnr_survey: 'MN DNR Survey',
    wi_dnr_survey: 'WI DNR Survey',
    wi_hypsography: 'WI Hypsography',
    mi_dnr_survey: 'MI DNR Survey',
    nb_depth_points: 'NB Soundings',
    ok_depth_points: 'OK Depths',
    ri_lake_management_index: 'RI Lake Plans',
    ny_dec_contour_index: 'NY Contour Maps',
    nj_lake_plan_index: 'NJ Lake Plans',
    pa_pfbc_footprint: 'PA Lake Footprints',
    va_dwr_index: 'VA Waterbody Index',
    wv_lake_map_index: 'WV Lake Maps',
    nl_hydrolakes: 'NL HydroLAKES',
    nt_hydrolakes: 'NT HydroLAKES',
    ns_survey_footprint: 'NS Survey Coverage',
    nu_hydrolakes: 'NU HydroLAKES',
    pe_hydrolakes: 'PE HydroLAKES',
    tx_twdb_survey: 'TX TWDB Survey',
    me_index: 'Maine Survey Index',
    mb_index: 'Manitoba Survey Index',
    sk_index: 'Saskatchewan Survey Index',
    yt_hydrolakes: 'YT HydroLAKES',
    cudem: 'NOAA Chart',
    gebco: 'Global Ocean Chart',
    emodnet: 'European Chart',
    ml_tier1: 'Satellite Model',
    ml_tier2: 'Satellite Estimate',
    ml_tier3: 'Basin Estimate',
    nhdplus: 'River Estimate',
    morphometric: 'Shape Estimate',
    '3d_lakes': 'Global Lake Model',
  };
  return labels[source] || source;
}
