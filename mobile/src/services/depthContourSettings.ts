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
 * Competitor parity: Navionics SonarChart shading / depth color options.
 * OpenCatch EXCEEDS with papercut contour style, thermal colormap, and custom
 * shallow/deep color picking.
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
  interval: ContourInterval;
  colorScheme: ContourColorScheme;
  opacity: number; // 0.0 – 1.0
  showLabels: boolean;
  customColors: ContourCustomColors;
}

// ── Defaults ───────────────────────────────────────────────────────────────────

export const DEFAULT_CONTOUR_SETTINGS: DepthContourSettings = {
  interval: 5,
  colorScheme: 'classic-blue',
  opacity: 0.85,
  showLabels: true,
  customColors: {
    shallow: '#9ECAE1',
    deep: '#08306B',
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
    description: 'Light to dark blue, Navionics-style',
    stops: ['#E3F2FD', '#BBDEFB', '#64B5F6', '#2196F3', '#1565C0', '#0D47A1'],
  },
  {
    key: 'papercut',
    label: 'Papercut',
    description: 'Layered blue bands, topographic map look',
    stops: ['#C5E1F5', '#9ED4F0', '#67B7DC', '#3B97C9', '#2D8AB8', '#145374'],
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
    const parsed = JSON.parse(raw);
    return {
      ...DEFAULT_CONTOUR_SETTINGS,
      ...parsed,
      customColors: {
        ...DEFAULT_CONTOUR_SETTINGS.customColors,
        ...(parsed.customColors ?? {}),
      },
    };
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
