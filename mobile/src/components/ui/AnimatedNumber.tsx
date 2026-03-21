/**
 * OpenCatch — Animated Number (Count-up)
 *
 * Smoothly counts up from 0 to the target value on mount.
 * Used for stats, scores, and numeric highlights.
 */

import React, { useEffect, useRef, useState } from 'react';
import { Animated, Text, TextStyle, StyleProp } from 'react-native';

interface AnimatedNumberProps {
  /** Target number to count up to */
  value: number;
  /** Animation duration in ms. Default 1000 */
  duration?: number;
  /** Number of decimal places. Default 0 */
  decimals?: number;
  /** Optional suffix (e.g. '%', 'km') */
  suffix?: string;
  /** Optional prefix (e.g. '$') */
  prefix?: string;
  style?: StyleProp<TextStyle>;
}

export function AnimatedNumber({
  value,
  duration = 1000,
  decimals = 0,
  suffix = '',
  prefix = '',
  style,
}: AnimatedNumberProps) {
  const animValue = useRef(new Animated.Value(0)).current;
  const [display, setDisplay] = useState(`${prefix}0${suffix}`);

  useEffect(() => {
    animValue.setValue(0);

    const listener = animValue.addListener(({ value: v }) => {
      setDisplay(`${prefix}${v.toFixed(decimals)}${suffix}`);
    });

    Animated.timing(animValue, {
      toValue: value,
      duration,
      useNativeDriver: false,
    }).start();

    return () => {
      animValue.removeListener(listener);
    };
  }, [value, duration, decimals, suffix, prefix]);

  return <Text style={style}>{display}</Text>;
}
