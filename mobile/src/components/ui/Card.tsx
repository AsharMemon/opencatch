/**
 * OpenCatch — Unified Card Component
 *
 * Provides consistent card styling across the entire app:
 * - Surface cards: white, subtle shadow, 12px radius
 * - Elevated cards: stronger shadow for interactive items
 * - Pressable variant with spring scale-down on press
 */

import React, { useCallback, useRef } from 'react';
import {
  Animated,
  Pressable,
  StyleSheet,
  ViewStyle,
  StyleProp,
  PressableProps,
} from 'react-native';
import { palette } from '../../theme/palette';
import { shadows } from '../../theme/shadows';
import { radius, spacing } from '../../theme/spacing';

// ── Types ────────────────────────────────────────────────────────────────────

interface CardBaseProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  /** 'surface' (default) or 'elevated' for stronger shadow */
  variant?: 'surface' | 'elevated' | 'flat';
}

interface StaticCardProps extends CardBaseProps {
  onPress?: undefined;
}

interface PressableCardProps extends CardBaseProps {
  onPress: () => void;
  onLongPress?: () => void;
  disabled?: boolean;
}

type CardProps = StaticCardProps | PressableCardProps;

// ── Component ────────────────────────────────────────────────────────────────

export function Card(props: CardProps) {
  const { children, style, variant = 'surface' } = props;

  const shadowStyle =
    variant === 'elevated'
      ? shadows.elevated
      : variant === 'flat'
        ? shadows.none
        : shadows.surface;

  if (props.onPress) {
    return (
      <AnimatedPressableCard
        style={style}
        shadowStyle={shadowStyle}
        onPress={props.onPress}
        onLongPress={(props as PressableCardProps).onLongPress}
        disabled={(props as PressableCardProps).disabled}
      >
        {children}
      </AnimatedPressableCard>
    );
  }

  return (
    <Animated.View style={[styles.card, shadowStyle, style]}>
      {children}
    </Animated.View>
  );
}

// ── Internal animated pressable ──────────────────────────────────────────────

function AnimatedPressableCard({
  children,
  style,
  shadowStyle,
  onPress,
  onLongPress,
  disabled,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  shadowStyle: ViewStyle;
  onPress: () => void;
  onLongPress?: () => void;
  disabled?: boolean;
}) {
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

  return (
    <Pressable
      onPress={onPress}
      onLongPress={onLongPress}
      onPressIn={handlePressIn}
      onPressOut={handlePressOut}
      disabled={disabled}
    >
      <Animated.View
        style={[
          styles.card,
          shadowStyle,
          style,
          { transform: [{ scale }] },
        ]}
      >
        {children}
      </Animated.View>
    </Pressable>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
  },
});
