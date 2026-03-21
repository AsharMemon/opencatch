/**
 * OpenCatch — Speed/Course Heads-Up Display
 *
 * Floating pill at the top of MapScreen when moving at boating speed (>2 knots).
 * Shows: speed (knots/mph), course over ground, compass heading, GPS accuracy.
 * Updates every second from GPS.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Constants ────────────────────────────────────────────────────────────────

const MIN_SPEED_KNOTS = 2; // Only show HUD above 2 knots
const MS_TO_KNOTS = 1.94384;
const MS_TO_MPH = 2.23694;
const SETTINGS_KEY = '@opencatch/speed_unit';

type SpeedUnit = 'knots' | 'mph';

interface GPSData {
  speedMs: number;       // meters/second
  course: number;        // degrees (course over ground)
  heading: number;       // degrees (device compass heading)
  accuracy: number;      // meters
  timestamp: number;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function degreesToCompass(deg: number): string {
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const idx = Math.round(((deg % 360 + 360) % 360) / 22.5) % 16;
  return dirs[idx];
}

function formatSpeed(speedMs: number, unit: SpeedUnit): string {
  const converted = unit === 'knots' ? speedMs * MS_TO_KNOTS : speedMs * MS_TO_MPH;
  return converted.toFixed(1);
}

function getAccuracyLabel(meters: number): { label: string; color: string } {
  if (meters <= 5) return { label: 'Excellent', color: '#4CAF50' };
  if (meters <= 15) return { label: 'Good', color: '#8BC34A' };
  if (meters <= 30) return { label: 'Fair', color: '#FFC107' };
  return { label: 'Poor', color: '#FF5722' };
}

// ── Component ────────────────────────────────────────────────────────────────

export function SpeedCourseHUD() {
  const [gpsData, setGpsData] = useState<GPSData | null>(null);
  const [speedUnit, setSpeedUnit] = useState<SpeedUnit>('knots');
  const [visible, setVisible] = useState(false);
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const locationSub = useRef<Location.LocationSubscription | null>(null);
  const headingSub = useRef<Location.LocationSubscription | null>(null);
  const currentHeading = useRef(0);

  // Load speed unit preference
  useEffect(() => {
    AsyncStorage.getItem(SETTINGS_KEY).then((val) => {
      if (val === 'mph' || val === 'knots') setSpeedUnit(val);
    }).catch(() => {});
  }, []);

  // GPS position watcher — high accuracy, 1s interval
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted' || cancelled) return;

        locationSub.current = await Location.watchPositionAsync(
          {
            accuracy: Location.Accuracy.BestForNavigation,
            timeInterval: 1000,
            distanceInterval: 0,
          },
          (loc) => {
            if (cancelled) return;
            setGpsData({
              speedMs: Math.max(0, loc.coords.speed ?? 0),
              course: loc.coords.heading ?? 0,
              heading: currentHeading.current,
              accuracy: loc.coords.accuracy ?? 99,
              timestamp: loc.timestamp,
            });
          },
        );

        // Also watch compass heading
        headingSub.current = await Location.watchHeadingAsync((h) => {
          currentHeading.current = h.trueHeading ?? h.magHeading ?? 0;
        });
      } catch {
        // GPS unavailable
      }
    })();

    return () => {
      cancelled = true;
      locationSub.current?.remove();
      headingSub.current?.remove();
    };
  }, []);

  // Toggle visibility based on speed
  useEffect(() => {
    const shouldShow = gpsData !== null && gpsData.speedMs * MS_TO_KNOTS >= MIN_SPEED_KNOTS;
    if (shouldShow !== visible) {
      setVisible(shouldShow);
      Animated.timing(fadeAnim, {
        toValue: shouldShow ? 1 : 0,
        duration: 300,
        useNativeDriver: true,
      }).start();
    }
  }, [gpsData, visible, fadeAnim]);

  // Toggle speed unit on tap
  const handleToggleUnit = useCallback(() => {
    const next: SpeedUnit = speedUnit === 'knots' ? 'mph' : 'knots';
    setSpeedUnit(next);
    AsyncStorage.setItem(SETTINGS_KEY, next).catch(() => {});
  }, [speedUnit]);

  if (!visible || !gpsData) return null;

  const speedText = formatSpeed(gpsData.speedMs, speedUnit);
  const courseText = `${Math.round(gpsData.course)}°`;
  const compassDir = degreesToCompass(gpsData.course);
  const headingText = `${Math.round(gpsData.heading)}°`;
  const acc = getAccuracyLabel(gpsData.accuracy);

  return (
    <Animated.View style={[styles.container, { opacity: fadeAnim }]} pointerEvents="box-none">
      <View style={styles.pill}>
        {/* Speed */}
        <View style={styles.cell} onTouchEnd={handleToggleUnit}>
          <Ionicons name="speedometer-outline" size={14} color="#FFFFFF" />
          <Text style={styles.valueText}>{speedText}</Text>
          <Text style={styles.unitText}>{speedUnit === 'knots' ? 'kn' : 'mph'}</Text>
        </View>

        <View style={styles.divider} />

        {/* Course over ground */}
        <View style={styles.cell}>
          <Ionicons name="compass-outline" size={14} color="#FFFFFF" />
          <Text style={styles.valueText}>{courseText}</Text>
          <Text style={styles.unitText}>{compassDir}</Text>
        </View>

        <View style={styles.divider} />

        {/* Heading (compass) */}
        <View style={styles.cell}>
          <Ionicons name="navigate-outline" size={14} color="#FFFFFF" />
          <Text style={styles.valueText}>{headingText}</Text>
          <Text style={styles.unitText}>HDG</Text>
        </View>

        <View style={styles.divider} />

        {/* GPS accuracy */}
        <View style={styles.cell}>
          <View style={[styles.accuracyDot, { backgroundColor: acc.color }]} />
          <Text style={styles.unitText}>{Math.round(gpsData.accuracy)}m</Text>
        </View>
      </View>
    </Animated.View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    left: 16,
    right: 16,
    alignItems: 'center',
    zIndex: 100,
    pointerEvents: 'box-none',
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(20, 24, 30, 0.85)',
    borderRadius: 24,
    paddingHorizontal: 16,
    paddingVertical: 10,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
  },
  cell: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  valueText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
    fontVariant: ['tabular-nums'],
  },
  unitText: {
    fontSize: 11,
    fontWeight: '500',
    color: 'rgba(255, 255, 255, 0.6)',
    letterSpacing: 0.3,
  },
  divider: {
    width: 1,
    height: 16,
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
  },
  accuracyDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 2,
  },
});
