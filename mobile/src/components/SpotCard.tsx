import React, { useCallback, useRef } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import { SpotCardModel } from '../types/spots';
import { palette } from '../theme/palette';
import { hapticLight } from '../utils/haptics';

interface SpotCardProps {
  spot: SpotCardModel;
  onPress: () => void;
}

export function SpotCard({ spot, onPress }: SpotCardProps) {
  const scale = useRef(new Animated.Value(1)).current;

  const handlePressIn = useCallback(() => {
    Animated.spring(scale, {
      toValue: 0.98,
      useNativeDriver: true,
      speed: 50,
      bounciness: 4,
    }).start();
  }, [scale]);

  const handlePressOut = useCallback(() => {
    Animated.spring(scale, {
      toValue: 1,
      useNativeDriver: true,
      speed: 40,
      bounciness: 6,
    }).start();
  }, [scale]);

  const handlePress = useCallback(() => {
    hapticLight();
    onPress();
  }, [onPress]);

  return (
    <Pressable
      onPress={handlePress}
      onPressIn={handlePressIn}
      onPressOut={handlePressOut}
    >
      <Animated.View style={[styles.card, { transform: [{ scale }] }]}>
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
      </Animated.View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 16,
    gap: 8,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
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
    fontFamily: 'PlayfairDisplay-Bold',
    color: palette.text,
    fontSize: 18,
    fontWeight: '400',
    letterSpacing: -0.2,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 13,
    lineHeight: 18,
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
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  note: {
    color: palette.text,
    fontSize: 15,
    lineHeight: 22,
  },
});
