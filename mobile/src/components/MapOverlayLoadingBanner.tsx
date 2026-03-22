/**
 * MapOverlayLoadingBanner — Shows a subtle loading indicator at the top
 * of the map when any overlay is loading data.
 *
 * Displays the name of the overlay(s) currently loading with a spinner.
 * Auto-hides when all overlays finish loading.
 */

import React, { useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  Animated,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { palette } from '../theme/palette';

interface Props {
  /** Set of overlay names currently loading (e.g. "Wind", "Radar") */
  loadingOverlays: Set<string>;
}

export function MapOverlayLoadingBanner({ loadingOverlays }: Props) {
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(-40)).current;
  const isVisible = loadingOverlays.size > 0;

  useEffect(() => {
    if (isVisible) {
      Animated.parallel([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
        Animated.spring(translateY, {
          toValue: 0,
          friction: 8,
          tension: 60,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(opacity, {
          toValue: 0,
          duration: 150,
          useNativeDriver: true,
        }),
        Animated.timing(translateY, {
          toValue: -40,
          duration: 150,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [isVisible, opacity, translateY]);

  if (!isVisible) return null;

  const names = Array.from(loadingOverlays);
  const label =
    names.length === 1
      ? `Loading ${names[0]} Data...`
      : `Loading ${names.length} Overlays...`;

  return (
    <Animated.View
      style={[
        styles.container,
        { opacity, transform: [{ translateY }] },
      ]}
      pointerEvents="none"
    >
      <View style={styles.pill}>
        <ActivityIndicator size="small" color="#FFFFFF" />
        <Text style={styles.text}>{label}</Text>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 100 : 80,
    left: 0,
    right: 0,
    alignItems: 'center',
    zIndex: 150,
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: 'rgba(26, 26, 24, 0.85)',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 6,
  },
  text: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '600',
    letterSpacing: 0.1,
  },
});
