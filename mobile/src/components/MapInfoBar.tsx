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
import type { MarineInstrumentData } from '../services/aisWifiReceiver';

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
  source: 'device' | 'external';
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
  leftInset?: number;
  rightInset?: number;
  anchorWatch?: AnchorWatchInfo;
  routeNav?: RouteNavInfo;
  externalData?: MarineInstrumentData | null;
  embedded?: boolean;
  expandable?: boolean;
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

export function MapInfoBar({
  bottomOffset = 0,
  leftInset = 12,
  rightInset = 108,
  anchorWatch,
  routeNav,
  externalData,
  embedded = false,
  expandable = true,
}: MapInfoBarProps) {
  const [gps, setGps] = useState<GPSInfo | null>(null);
  const [expanded, setExpanded] = useState(false);
  const expandAnim = useRef(new Animated.Value(0)).current;
  const locationSub = useRef<Location.LocationSubscription | null>(null);

  // GPS watcher
  useEffect(() => {
    let cancelled = false;

    if (externalData?.position) {
      locationSub.current?.remove();
      return () => {
        cancelled = true;
      };
    }

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
            source: 'device',
          });
        },
      );
    })();

    return () => {
      cancelled = true;
      locationSub.current?.remove();
    };
  }, [externalData?.position]);

  useEffect(() => {
    if (!externalData?.position) return;
    setGps({
      latitude: externalData.position.lat,
      longitude: externalData.position.lon,
      altitude: null,
      speedMs: Math.max(0, (externalData.position.sogKnots ?? 0) / MS_TO_KNOTS),
      heading: externalData.heading?.headingDeg
        ?? externalData.position.cogDeg
        ?? 0,
      accuracy: 3,
      source: 'external',
    });
  }, [
    externalData?.heading?.headingDeg,
    externalData?.position?.cogDeg,
    externalData?.position?.lat,
    externalData?.position?.lon,
    externalData?.position?.sogKnots,
  ]);

  // Expand/collapse animation
  const toggleExpand = useCallback(() => {
    if (!expandable) return;
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
  const windDir = externalData?.wind ? degreesToCompass(externalData.wind.angleDeg) : null;

  // Animated height for expanded section
  const expandedHeight = expandAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 60],
  });

  const showAnchor = anchorWatch?.active;
  const showRoute = routeNav?.active;

  return (
    <View
      style={[
        styles.container,
        embedded
          ? styles.containerEmbedded
          : {
              bottom: bottomOffset,
              left: leftInset,
              right: rightInset,
            },
      ]}
    >
      <Pressable style={[styles.bar, embedded && styles.barEmbedded]} onPress={toggleExpand}>
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
            {gps.source === 'external' ? (
              <>
                <Ionicons name="hardware-chip-outline" size={13} color="#1565C0" />
                <Text style={[styles.infoUnit, { color: '#1565C0', fontWeight: '700' }]}>NMEA</Text>
              </>
            ) : (
              <>
                <View style={[styles.accuracyDot, { backgroundColor: accuracy.color }]} />
                <Text style={[styles.infoUnit, { color: accuracy.color }]}>
                  {'\u00B1'}{Math.round(gps.accuracy)}m
                </Text>
              </>
            )}
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
          {expandable && (
            <Ionicons
              name={expanded ? 'chevron-down' : 'chevron-up'}
              size={14}
              color={palette.textDim}
              style={{ marginLeft: 4 }}
            />
          )}
        </View>

        {/* Expanded details */}
        <Animated.View
          style={[
            styles.expandedRow,
            !expandable && styles.expandedRowHidden,
            { height: expandable ? expandedHeight : 0, opacity: expandable ? expandAnim : 0 },
          ]}
        >
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
            {externalData?.depth && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Depth</Text>
                <Text style={styles.expandedValue}>{externalData.depth.depthM.toFixed(1)} m</Text>
              </View>
            )}
            {externalData?.wind && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Wind</Text>
                <Text style={styles.expandedValue}>
                  {externalData.wind.speedKnots.toFixed(1)} kt {windDir}
                </Text>
              </View>
            )}
            {gps.source === 'external' && (
              <View style={styles.expandedItem}>
                <Text style={styles.expandedLabel}>Source</Text>
                <Text style={styles.expandedValue}>Boat instruments</Text>
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
    zIndex: 50,
  },
  containerEmbedded: {
    position: 'relative',
    zIndex: 1,
    width: '100%',
  },
  bar: {
    alignSelf: 'stretch',
    backgroundColor: 'rgba(250, 252, 251, 0.92)',
    borderRadius: 14,
    paddingHorizontal: 13,
    paddingVertical: 7,
    minWidth: 0,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: 'rgba(38, 70, 88, 0.08)',
  },
  barEmbedded: {
    backgroundColor: 'rgba(248, 250, 249, 0.98)',
    borderRadius: 16,
    shadowOpacity: 0,
    shadowRadius: 0,
    shadowOffset: { width: 0, height: 0 },
    elevation: 0,
    borderColor: 'rgba(53, 91, 117, 0.10)',
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
    fontSize: 12.5,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  infoUnit: {
    fontSize: 10.5,
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
  expandedRowHidden: {
    height: 0,
    opacity: 0,
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
