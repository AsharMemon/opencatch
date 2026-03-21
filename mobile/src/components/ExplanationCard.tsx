import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

interface Props {
  explanation: string;
}

export function ExplanationCard({ explanation }: Props) {
  return (
    <View style={styles.card}>
      <Ionicons name="bulb-outline" size={22} color={palette.accent} style={{ marginTop: 2 }} />
      <View style={styles.textContainer}>
        <Text style={styles.title}>AI Analysis</Text>
        <Text style={styles.body}>{explanation}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: palette.accentLight,
    borderRadius: 10,
    padding: 16,
    flexDirection: 'row',
    gap: 12,
  },
  textContainer: {
    flex: 1,
    gap: 6,
  },
  title: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
  },
  body: {
    color: palette.textSecondary,
    fontSize: 15,
    lineHeight: 22,
  },
});
