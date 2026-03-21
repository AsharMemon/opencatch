import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SpotCardModel } from '../types/spots';
import { palette } from '../theme/palette';

interface SpotCardProps {
  spot: SpotCardModel;
  onPress: () => void;
}

export function SpotCard({ spot, onPress }: SpotCardProps) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}>
      <View style={styles.row}>
        <View style={styles.badge} />
        <Text style={styles.title}>{spot.name}</Text>
      </View>
      <Text style={styles.subtitle}>{spot.subtitle}</Text>
      <View style={styles.metricRow}>
        <Text style={styles.metricLabel}>{spot.conditionLabel}</Text>
        <Text style={styles.metricValue}>{spot.conditionValue}</Text>
      </View>
      <Text style={styles.note}>{spot.note}</Text>
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
  cardPressed: {
    opacity: 0.9,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  badge: {
    width: 10,
    height: 10,
    borderRadius: 999,
    backgroundColor: palette.accent,
  },
  title: {
    color: palette.text,
    fontSize: 20,
    fontWeight: '600',
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },
  metricRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  metricLabel: {
    color: palette.textMuted,
    fontSize: 13,
  },
  metricValue: {
    color: palette.success,
    fontSize: 18,
    fontWeight: '600',
  },
  note: {
    color: palette.text,
    fontSize: 15,
    lineHeight: 21,
  },
});
