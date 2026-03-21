import React from 'react';
import { View, Text, Switch, Pressable, StyleSheet } from 'react-native';
import { palette } from '../theme/palette';

interface ToggleProps {
  label: string;
  value: boolean;
  onValueChange: (val: boolean) => void;
}

export function SettingsToggle({ label, value, onValueChange }: ToggleProps) {
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{ false: palette.border, true: palette.accent }}
        thumbColor={value ? '#FFFFFF' : palette.surfaceRaised}
      />
    </View>
  );
}

interface ButtonProps {
  label: string;
  value?: string;
  onPress: () => void;
}

export function SettingsButton({ label, value, onPress }: ButtonProps) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <Text style={styles.label}>{label}</Text>
      <View style={styles.valueRow}>
        {value && <Text style={styles.value}>{value}</Text>}
        <Text style={styles.chevron}>{'\u203A'}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  pressed: {
    opacity: 0.7,
  },
  label: {
    color: palette.text,
    fontSize: 16,
  },
  valueRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  value: {
    color: palette.textMuted,
    fontSize: 15,
  },
  chevron: {
    color: palette.textDim,
    fontSize: 22,
    fontWeight: '600',
  },
});
