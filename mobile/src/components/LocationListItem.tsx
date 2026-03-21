import React from 'react';
import { Pressable, View, Text, StyleSheet } from 'react-native';
import { palette, scoreColor, scoreLabel } from '../theme/palette';

interface Props {
  name: string;
  subtitle: string;
  score: number;
  topReason?: string;
  onPress: () => void;
}

export function LocationListItem({ name, subtitle, score, topReason, onPress }: Props) {
  const color = scoreColor(score);
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
    >
      <View style={styles.row}>
        <View style={[styles.scoreBadge, { borderColor: color }]}>
          <Text style={[styles.scoreText, { color }]}>{score}</Text>
        </View>
        <View style={styles.info}>
          <Text style={styles.name} numberOfLines={1}>
            {name}
          </Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {subtitle}
          </Text>
        </View>
        <View style={[styles.labelBadge, { backgroundColor: color + '15' }]}>
          <Text style={[styles.labelText, { color }]}>{scoreLabel(score)}</Text>
        </View>
      </View>
      {topReason && (
        <Text style={styles.reason} numberOfLines={2}>
          {topReason}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    padding: 16,
    gap: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  pressed: {
    opacity: 0.85,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  scoreBadge: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 2.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scoreText: {
    fontSize: 16,
    fontWeight: '700',
  },
  info: {
    flex: 1,
    gap: 2,
  },
  name: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '600',
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 13,
  },
  labelBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
  },
  labelText: {
    fontSize: 11,
    fontWeight: '700',
  },
  reason: {
    color: palette.textMuted,
    fontSize: 13,
    lineHeight: 19,
    paddingLeft: 56,
  },
});
