import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { palette, scoreColor } from '../theme/palette';

interface Props {
  label: string;
  value: number; // 0-100
}

export function ScoreBreakdownBar({ label, value }: Props) {
  const color = scoreColor(value);
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${value}%`, backgroundColor: color }]} />
      </View>
      <Text style={[styles.value, { color }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  label: {
    color: palette.textMuted,
    fontSize: 12,
    fontWeight: '600',
    width: 90,
  },
  barTrack: {
    flex: 1,
    height: 6,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 3,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 3,
  },
  value: {
    fontSize: 14,
    fontWeight: '700',
    width: 28,
    textAlign: 'right',
  },
});
