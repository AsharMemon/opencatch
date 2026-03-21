import { Platform, ViewStyle } from 'react-native';

/**
 * Shared shadow presets for the OpenCatch design system.
 * Use these instead of ad-hoc shadow values for visual consistency.
 */

export const shadows = {
  /** Subtle surface card — default for most cards */
  surface: Platform.select<ViewStyle>({
    ios: {
      shadowColor: '#000',
      shadowOpacity: 0.06,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 2 },
    },
    android: {
      elevation: 2,
    },
    default: {
      shadowColor: '#000',
      shadowOpacity: 0.06,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 2 },
    },
  })!,

  /** Elevated card — interactive elements, popovers */
  elevated: Platform.select<ViewStyle>({
    ios: {
      shadowColor: '#000',
      shadowOpacity: 0.10,
      shadowRadius: 12,
      shadowOffset: { width: 0, height: 4 },
    },
    android: {
      elevation: 6,
    },
    default: {
      shadowColor: '#000',
      shadowOpacity: 0.10,
      shadowRadius: 12,
      shadowOffset: { width: 0, height: 4 },
    },
  })!,

  /** Strong shadow — modals, bottom sheets, FABs */
  modal: Platform.select<ViewStyle>({
    ios: {
      shadowColor: '#000',
      shadowOpacity: 0.15,
      shadowRadius: 20,
      shadowOffset: { width: 0, height: -6 },
    },
    android: {
      elevation: 10,
    },
    default: {
      shadowColor: '#000',
      shadowOpacity: 0.15,
      shadowRadius: 20,
      shadowOffset: { width: 0, height: -6 },
    },
  })!,

  /** Upward shadow for bottom sheet handle area */
  sheetHandle: Platform.select<ViewStyle>({
    ios: {
      shadowColor: '#000',
      shadowOpacity: 0.12,
      shadowRadius: 16,
      shadowOffset: { width: 0, height: -4 },
    },
    android: {
      elevation: 8,
    },
    default: {
      shadowColor: '#000',
      shadowOpacity: 0.12,
      shadowRadius: 16,
      shadowOffset: { width: 0, height: -4 },
    },
  })!,

  /** No shadow — flat/inset elements */
  none: {
    shadowColor: 'transparent',
    shadowOpacity: 0,
    shadowRadius: 0,
    shadowOffset: { width: 0, height: 0 },
    elevation: 0,
  } as ViewStyle,
} as const;
