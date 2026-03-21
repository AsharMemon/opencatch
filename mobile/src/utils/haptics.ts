/**
 * OpenCatch — Haptic Feedback Utilities
 *
 * Wraps expo-haptics with safe fallback for web/simulator.
 * Import and call these instead of expo-haptics directly.
 */

import { Platform } from 'react-native';

let Haptics: typeof import('expo-haptics') | null = null;

// Lazy-load expo-haptics only on native platforms
if (Platform.OS !== 'web') {
  try {
    Haptics = require('expo-haptics');
  } catch {
    // expo-haptics not available — fallback to no-op
  }
}

/** Light tap — tab switches, toggles, selections */
export function hapticLight() {
  Haptics?.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
}

/** Medium tap — button presses, card interactions */
export function hapticMedium() {
  Haptics?.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
}

/** Heavy tap — important actions, destructive confirmations */
export function hapticHeavy() {
  Haptics?.impactAsync(Haptics.ImpactFeedbackStyle.Heavy).catch(() => {});
}

/** Selection feedback — list item selection, picker changes */
export function hapticSelection() {
  Haptics?.selectionAsync().catch(() => {});
}

/** Success notification — task completed, save confirmed */
export function hapticSuccess() {
  Haptics?.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
}

/** Warning notification — approaching limit, caution */
export function hapticWarning() {
  Haptics?.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
}

/** Error notification — failed action */
export function hapticError() {
  Haptics?.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
}
