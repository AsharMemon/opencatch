/**
 * OpenCatch — Skeleton Loader
 *
 * Shimmering placeholder for content that is loading.
 * Use instead of ActivityIndicator for a more premium feel.
 */

import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, ViewStyle, StyleProp, View } from 'react-native';
import { palette } from '../../theme/palette';
import { radius } from '../../theme/spacing';

interface SkeletonProps {
  /** Width — number (px) or string ('100%'). Default '100%' */
  width?: number | string;
  /** Height in px. Default 16 */
  height?: number;
  /** Border radius. Default 8 */
  borderRadius?: number;
  style?: StyleProp<ViewStyle>;
}

export function SkeletonLoader({
  width = '100%',
  height = 16,
  borderRadius = radius.sm,
  style,
}: SkeletonProps) {
  const opacity = useRef(new Animated.Value(0.3)).current;

  useEffect(() => {
    const shimmer = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.7,
          duration: 800,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.3,
          duration: 800,
          useNativeDriver: true,
        }),
      ]),
    );
    shimmer.start();
    return () => shimmer.stop();
  }, [opacity]);

  return (
    <Animated.View
      style={[
        {
          width: width as any,
          height,
          borderRadius,
          backgroundColor: palette.surfaceRaised,
          opacity,
        },
        style,
      ]}
    />
  );
}

/** Pre-built skeleton for a card with title + 2 lines of body text */
export function SkeletonCard({ style }: { style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[skeletonStyles.card, style]}>
      <SkeletonLoader width="60%" height={18} borderRadius={4} />
      <SkeletonLoader width="100%" height={12} borderRadius={4} style={{ marginTop: 12 }} />
      <SkeletonLoader width="80%" height={12} borderRadius={4} style={{ marginTop: 8 }} />
      <View style={skeletonStyles.row}>
        <SkeletonLoader width={60} height={24} borderRadius={12} />
        <SkeletonLoader width={60} height={24} borderRadius={12} />
        <SkeletonLoader width={60} height={24} borderRadius={12} />
      </View>
    </View>
  );
}

const skeletonStyles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: 16,
  },
  row: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 16,
  },
});
