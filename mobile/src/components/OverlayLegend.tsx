/**
 * OpenCatch -- Shared Overlay Legend Component
 *
 * Renders a compact, semi-transparent color legend in a map corner.
 * Used by all Windy-style map overlays to explain their color scales.
 *
 * Supports horizontal and vertical layouts. Fades in/out with the overlay.
 */

import React from 'react';
import { View, Text, StyleSheet, Platform } from 'react-native';

// ── Types ────────────────────────────────────────────────────────────────────

export interface LegendStop {
  /** Color hex for this stop. */
  color: string;
  /** Short label, e.g. "0m", "4+m", "Cold". */
  label: string;
}

interface Props {
  /** Title shown above the gradient bar, e.g. "Wave Height" */
  title: string;
  /** Ordered color stops from low to high. */
  stops: LegendStop[];
  /** Position on screen. Default: 'bottom-left'. */
  position?: 'bottom-left' | 'bottom-right' | 'top-left' | 'top-right';
  /** Layout of the gradient bar. Default: 'horizontal'. */
  layout?: 'horizontal' | 'vertical';
  /** Optional unit suffix shown after the title, e.g. "(m)" */
  unit?: string;
}

// ── Component ────────────────────────────────────────────────────────────────

export function OverlayLegend({
  title,
  stops,
  position = 'bottom-left',
  layout = 'horizontal',
  unit,
}: Props) {
  if (stops.length === 0) return null;

  const positionStyle = POSITION_STYLES[position];

  if (layout === 'vertical') {
    return (
      <View style={[styles.container, positionStyle]} pointerEvents="none">
        <Text style={styles.title}>
          {title}
          {unit ? ` ${unit}` : ''}
        </Text>
        <View style={styles.verticalBar}>
          {stops.map((stop, i) => (
            <View key={i} style={styles.verticalRow}>
              <View style={[styles.colorBlock, { backgroundColor: stop.color }]} />
              <Text style={styles.stopLabel}>{stop.label}</Text>
            </View>
          ))}
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, positionStyle]} pointerEvents="none">
      <Text style={styles.title}>
        {title}
        {unit ? ` ${unit}` : ''}
      </Text>
      <View style={styles.gradientRow}>
        {stops.map((stop, i) => (
          <View key={i} style={[styles.gradientSegment, { backgroundColor: stop.color }]} />
        ))}
      </View>
      <View style={styles.labelRow}>
        {stops.map((stop, i) => (
          <Text key={i} style={[styles.stopLabel, styles.stopLabelHorizontal]}>
            {stop.label}
          </Text>
        ))}
      </View>
    </View>
  );
}

// ── Position presets ─────────────────────────────────────────────────────────

const POSITION_STYLES: Record<string, any> = {
  'bottom-left': {
    position: 'absolute' as const,
    bottom: Platform.OS === 'ios' ? 170 : 150,
    left: 10,
  },
  'bottom-right': {
    position: 'absolute' as const,
    bottom: Platform.OS === 'ios' ? 170 : 150,
    right: 10,
  },
  'top-left': {
    position: 'absolute' as const,
    top: Platform.OS === 'ios' ? 110 : 90,
    left: 10,
  },
  'top-right': {
    position: 'absolute' as const,
    top: Platform.OS === 'ios' ? 110 : 90,
    right: 10,
  },
};

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    backgroundColor: 'rgba(0, 0, 0, 0.70)',
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 6,
    maxWidth: 200,
    zIndex: 20,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 10,
    fontWeight: '700',
    marginBottom: 4,
    letterSpacing: 0.3,
  },
  // Horizontal gradient layout
  gradientRow: {
    flexDirection: 'row',
    height: 10,
    borderRadius: 3,
    overflow: 'hidden',
  },
  gradientSegment: {
    flex: 1,
    height: 10,
  },
  labelRow: {
    flexDirection: 'row',
    marginTop: 2,
  },
  // Vertical block layout
  verticalBar: {
    gap: 2,
  },
  verticalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  colorBlock: {
    width: 14,
    height: 10,
    borderRadius: 2,
  },
  stopLabel: {
    color: 'rgba(255, 255, 255, 0.85)',
    fontSize: 8,
    fontWeight: '500',
  },
  stopLabelHorizontal: {
    flex: 1,
    textAlign: 'center',
  },
});
