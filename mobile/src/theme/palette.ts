export const palette = {
  // Backgrounds
  background: '#FAFAF7',
  surface: '#FFFFFF',
  surfaceRaised: '#F5F5F0',
  surfaceHighlight: '#E6F2FB',

  // Borders
  border: '#E5E5E0',
  borderLight: '#EDEDEA',

  // Text
  text: '#1A1A18',
  textSecondary: '#4A4A45',
  textMuted: '#8A8A85',
  textDim: '#B5B5B0',

  // Brand — ocean blue
  accent: '#0A6EBD',
  accentDeep: '#064A80',
  accentDim: 'rgba(10, 110, 189, 0.08)',
  accentLight: '#E6F2FB',

  // Semantic
  success: '#3D8B37',
  warning: '#C4841D',
  error: '#C44B4B',

  // Water / map
  water: '#2196F3',
  waterLight: '#E3F2FD',
  waterDeep: '#0D47A1',

  // Bathymetric depth contours (shallow → deep) — Material blue scale
  depthContours: ['#E3F2FD', '#BBDEFB', '#64B5F6', '#2196F3', '#1565C0', '#0D47A1'],

  // Map pin colors — condition band palette
  pinHot: '#E53935',
  pinHeatingUp: '#FB8C00',
  pinFair: '#0A6EBD',
  pinSlow: '#78909C',
  pinCold: '#546E7A',

  // Misc
  tabInactive: '#B5B5B0',
  overlay: 'rgba(26, 26, 24, 0.5)',
  shadow: 'rgba(0, 0, 0, 0.06)',
};

// ---------------------------------------------------------------------------
// Condition band system (replaces numeric scores)
// ---------------------------------------------------------------------------

export type ConditionBand = 'hot' | 'heating-up' | 'fair' | 'slow' | 'cold';

// icon: Ionicons name for use in JSX; textIcon kept for non-JSX contexts
export const conditionConfig: Record<
  ConditionBand,
  { label: string; color: string; icon: string; ionicon: string; bgTint: string }
> = {
  'hot': { label: 'Prime', color: '#E53935', icon: 'flame', ionicon: 'flame', bgTint: 'rgba(229, 57, 53, 0.08)' },
  'heating-up': { label: 'High Activity', color: '#FB8C00', icon: 'trending-up', ionicon: 'trending-up', bgTint: 'rgba(251, 140, 0, 0.08)' },
  'fair': { label: 'Moderate', color: '#0A6EBD', icon: 'fish', ionicon: 'fish', bgTint: 'rgba(10, 110, 189, 0.08)' },
  'slow': { label: 'Low', color: '#78909C', icon: 'water', ionicon: 'water', bgTint: 'rgba(120, 144, 156, 0.08)' },
  'cold': { label: 'Poor', color: '#546E7A', icon: 'snow', ionicon: 'snow', bgTint: 'rgba(84, 110, 122, 0.08)' },
};

export function getConditionBand(score: number): ConditionBand {
  if (score >= 80) return 'hot';
  if (score >= 65) return 'heating-up';
  if (score >= 45) return 'fair';
  if (score >= 25) return 'slow';
  return 'cold';
}

// ---------------------------------------------------------------------------
// Legacy helpers — kept for backward compatibility, now backed by condition bands
// ---------------------------------------------------------------------------

export function scoreColor(score: number): string {
  return conditionConfig[getConditionBand(score)].color;
}

export function scoreLabel(score: number): string {
  return conditionConfig[getConditionBand(score)].label;
}

export function scorePinColor(score: number): string {
  return conditionConfig[getConditionBand(score)].color;
}
