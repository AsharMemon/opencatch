import { Platform } from 'react-native';

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 26,
  full: 999,
} as const;

export const typography = {
  // Playfair Display for headlines (must be loaded via expo-font)
  headlineFamily: 'PlayfairDisplay-Bold',
  headlineFamilyRegular: 'PlayfairDisplay-Regular',
  headlineFamilyItalic: 'PlayfairDisplay-Italic',
  // System sans-serif for body text
  bodyFamily: Platform.OS === 'ios' ? 'System' : 'Roboto',

  // Sizes
  h1: 32,
  h2: 26,
  h3: 22,
  h4: 18,
  body: 15,
  bodySmall: 13,
  caption: 11,
  label: 12,

  // Line heights
  h1LineHeight: 40,
  h2LineHeight: 34,
  h3LineHeight: 30,
  bodyLineHeight: 22,
} as const;
