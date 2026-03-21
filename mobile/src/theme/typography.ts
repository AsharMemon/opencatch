import { Platform, TextStyle } from 'react-native';

// Playfair Display loaded in App.tsx via @expo-google-fonts
const SERIF = Platform.select({
  ios: 'PlayfairDisplay-Regular',
  android: 'PlayfairDisplay-Regular',
  default: 'Georgia',
});

const SERIF_BOLD = Platform.select({
  ios: 'PlayfairDisplay-Bold',
  android: 'PlayfairDisplay-Bold',
  default: 'Georgia',
});

// System sans-serif is the default — no need to set fontFamily

export const fonts = {
  serif: SERIF!,
  serifBold: SERIF_BOLD!,
} as const;

// Pre-built text style mixins
export const type = {
  screenTitle: {
    fontFamily: SERIF_BOLD,
    fontSize: 24,
    fontWeight: '400', // weight baked into font file
    letterSpacing: -0.3,
  } as TextStyle,

  sectionHeader: {
    fontFamily: SERIF,
    fontSize: 18,
    fontWeight: '400',
    letterSpacing: -0.2,
  } as TextStyle,

  cardTitle: {
    fontFamily: SERIF,
    fontSize: 15,
    fontWeight: '400',
  } as TextStyle,

  navHeader: {
    fontFamily: SERIF_BOLD,
    fontSize: 17,
    fontWeight: '400',
    letterSpacing: 0.2,
  } as TextStyle,

  brand: {
    fontFamily: SERIF_BOLD,
    fontSize: 28,
    fontWeight: '400',
    letterSpacing: -0.5,
  } as TextStyle,

  // ── Body text presets (sans-serif) ──────────────────────────────

  body: {
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '400',
    letterSpacing: 0,
  } as TextStyle,

  bodySmall: {
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '400',
    letterSpacing: 0.1,
  } as TextStyle,

  caption: {
    fontSize: 11,
    lineHeight: 14,
    fontWeight: '500',
    letterSpacing: 0.3,
  } as TextStyle,

  /** Tabular figures for stats / numbers */
  stat: {
    fontSize: 22,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.2,
  } as TextStyle,

  /** Tab bar label */
  tabLabel: {
    fontSize: 10,
    fontWeight: '600',
    letterSpacing: 0.3,
  } as TextStyle,
};
