/**
 * MapInfoBar — Persistent info bar at the bottom of the map (above the tab bar).
 *
 * Shows:
 * - Current speed (if moving), heading, GPS accuracy
 * - Tap to expand: lat/lon, altitude, nearest landmark
 * - When anchor watch is active: drift distance
 * - When navigating a route: next waypoint distance/bearing, ETA
 *
 * Design: minimal pill that blends with the map, Playfair Display headers,
 * Ionicons, off-white palette.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

// ── Constants ────────────────────────────────────────────────────────

const MS_TO_MPH = 2.23694;
const MS_TO_KNOTS = 1.94384;

interface GPSInfo {
  latitude: number;
  longitude: number;
  altitude: number | null;
  speedMs: number;
  heading: number;
  accuracy: number;
}

export interface AnchorWatchInfo {
  active: boolean;
  driftMeters: number;
  radiusMeters: number;
}

export interface RouteNavInfo {
  active: boolean;
  nextWaypointName: string;
  distanceMeters: number;
  bearingDeg: number;
  etaMinutes: number;
}

interface MapInfoBarProps {
  /** Positioned above tab bar — pass bottom offset */
  bottomOffset?: number;
  anchorWatch?: AnchorWatchInfo;
  routeNav?: RouteNavInfo;
}

function degreesToCompass(deg: number): string {
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const idx = Math.round(((deg % 360 + 360) % 360) / 22.5) % 16;
  return dirs[idx];
}

function getAccuracyBadge(meters: number): { label: string; color: string } {
  if (meters <= 5) return { label: 'GPS: Excellent', color: '#4CAF50' };
  if (meters <= 15) return { label: 'GPS: Good', color: '#8BC34A' };
  if (meters <= 30) return { label: 'GPS: Fair', color: '#FFC107' };
  return { label: 'GPS: Poor', color: '#FF5722' };
}

function formatDistance(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`;
  const mi = meters / 1609.34;
  return `${mi.toFixed(1)} mi`;
}

export function MapInfoBar({ bottomOffset = 0, anchorWatch, routeNav }: MapInfoBarProps) {
  const [gps, setGps] = useState<GPSInfo | null>(null);
  const [expanded, setExpanded] = useState(false);
  const expandAnim = useRef(new Animated.Value(0)).current;
  const locationSub = useRef<Location.LocationSubscription | null>(null);

  // GPS watcher
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const { status } = await Location.getForegroundPermissionsAsync();
      if (status !== 'granted' || cancelled) return;

      locationSub.current = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.High,
          timeInterval: 2000,
          distanceInterval: 1,
        },
        (loc) => {
          if (cancelled) return;
          setGps({
            latitude: loc.coords.latitude,
            longitude: loc.coords.longitude,
            altitude: loc.coords.altitude,
            speedMs: Math.max(0, loc.coords.speed ?? 0),
            heading: loc.coords.heading ?? 0,
            accuracy: loc.coords.accuracy ?? 999,
          });
        },
      );
    })();

    return () => {
      cancelled = true;
      locationSub.current?.remove();
    };
  }, []);

  // Expand/collapse animation
  const toggleExpand = useCallback(() => {
    const toExpanded = !expanded;
    setExpanded(toExpanded);
    Animated.spring(expandAnim, {
      toValue: toExpanded ? 1 : 0,
      friction: 10,
      tension: 80,
      useNativeDriver: false,
    }).start();
  }, [expanded, expandAnim]);

  if (!gps) return null;

  const isMoving = gps.speedMs > 0.5;
  const speedMph = gps.speedMs * MS_TO_MPH;
  const speedKnots = gps.speedMs * MS_TO_KNOTS;
  const accuracy = getAccuracyBadge(gps.accuracy);
  const compassDir = degreesToCompass(gps.heading);

  // Animated height for expanded section
  const expandedHeight = expandAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 60],
  });

  const showAnchor = anchorWatch?.active;
  const showRoute = routeNav?.active;

  return (
    <View style={[styles.container, { bottom: bottomOffset }]}>
      <Pressable style={styles.bar} onPress={toggleExpand}>
        {/* Main row: speed, heading, accuracy */}
        <View style={styles.mainRow}>
          {/* Speed */}
          {isMoving ? (
            <View style={styles.infoItem}>
              <Ionicons name="speedometer-outline" size={13} color={palette.accent} />
              <Text style={styles.infoValue}>{speedMph.toFixed(1)}</Text>
              <Text style={styles.infoUnit}>mph</Text>
            </View>
          ) : (
            <View style={styles.infoItem}>
              <Ionicons name="locate-outline" size={13} color={palette.textMuted} />
              <Text style={[styles.infoValue, { color: palette.textMuted }]}>Stationary</Text>
            </View>
          )}

          {/* Divider */}
          <View style={styles.divider} />

          {/* Heading */}
          <View style={styles.infoItem}>
            <Ionicons name="compass-outline" size={13} color={palette.textSecondary} />
            <Text style={styles.infoValue}>{Math.round(gps.heading)}{'\u00B0'}</Text>
            <Text style={styles.infoUnit}>{compassDir}</Text>
          </View>

          {/* Divider */}
          <View style={styles.divider} />

          {/* GPS accuracy */}
          <View style={styles.infoItem}>
            <View style={[styles.accuracyDot, { backgroundColor: accuracy.color }]} />
            <Text style={[styles.infoUnit, { color: accuracy.color }]}>
              {'\u00B1'}{Math.round(gps.accuracy)}m
            </Text>
          </View>

          {/* Anchor watch badge */}
          {showAnchor && (
            <>
              <View style={styles.divider} />
              <View style={styles.infoItem}>
                <Ionicons name="boat" size={13} color="#E65100" />
                <Text style={[styles.infoValue, { color: '#E65100' }]}>
                  {anchorWatch!.driftMeters.toFixed(0)}m
                </Text>
                <Text style={[styles.infoUnit, { color: '#E65100' }]}>drift</Text>
              </View>
            </>
          )}

          {/* Route nav badge */}
          {showRoute && (
            <>
              <View style={styles.divider} />
              <View style={styles.infoItem}>
                <Ionicons name="navigate" size={13} color="#1565C0" />
                <Text style={[styles.infoValue, { color: '#1565C0' }]}>
                  {formatDistance(routeNav!.distanceMeters)}
                </Text>
                <Text style={[styles.infoUnit, { color: '#1565C0' }]}>
                  {routeNav!.etaMinutes < 60
                    ? `${Math.round(routeNav!.etaMinutes)}min`
                    : `${(routeNav!.etaMinutes / 60).toFixed(1)}hr`
                  }
                </Text>
              </View>
            </>
          )}

          {/* Expand chevron */}
          <Ionicons
            name={expanded ? 'chevron-down' : 'chevron-up'}
            size={14}
            color={palette.textDim}
            style={{ marginLeft: 4 }}
          />
        </View>

        {/* Expanded details */}
        <Animated.View style={[styles.expandedRow, { height: expandedHeight, opacity: expandAnim }]}>
          <View style={styles.expandedContent}>
            <View style={styles.expandedItem}>
              <Text style={styles.expandedLabel}>Lat</Text>
              <Text style={styles.expandedValue}>{gps.latitude.toFixed(6)}</Text>
            </View>
            <View style={styles.expandedItem}>
              <Text style={styles.expandedLabel}>Lon</Text>
              <Text style={styles.expandedValue}>{gps.longitude.toFixed(6)}</Text>
            </View>
            {gps.altitude != null && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Alt</Text>
                <Text style={styles.expandedValue}>{Math.round(gps.altitude)} m</Text>
              </View>
            )}
            {isMoving && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Knots</Text>
                <Text style={styles.expandedValue}>{speedKnots.toFixed(1)}</Text>
              </View>
            )}

            {/* Route nav details */}
            {showRoute && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Next WP</Text>
                <Text style={styles.expandedValue} numberOfLines={1}>
                  {routeNav!.nextWaypointName} ({Math.round(routeNav!.bearingDeg)}{'\u00B0'})
                </Text>
              </View>
            )}
          </View>
        </Animated.View>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    left: 12,
    right: 12,
    zIndex: 50,
  },
  bar: {
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 8,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 2 },
    elevation: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderLight,
  },
  mainRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  infoItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  infoValue: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  infoUnit: {
    fontSize: 11,
    color: palette.textMuted,
  },
  divider: {
    width: 1,
    height: 14,
    backgroundColor: palette.borderLight,
  },
  accuracyDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  expandedRow: {
    overflow: 'hidden',
  },
  expandedContent: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    paddingTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
    marginTop: 6,
  },
  expandedItem: {
    gap: 1,
  },
  expandedLabel: {
    fontSize: 9,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  expandedValue: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
});
