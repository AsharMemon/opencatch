/**
 * OpenCatch — Route Planner Screen
 *
 * Full nautical route planning interface:
 * - Tap to add waypoints (numbered markers)
 * - Route line drawn between waypoints (blue polyline)
 * - Bottom sheet with: total distance, ETA, fuel needed, fuel cost
 * - Shallow water warnings highlighted in red
 * - Speed limit zones shown along route
 * - "Navigate" button starts turn-by-turn nav mode
 * - Save/load routes
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  Alert,
  TextInput,
  Dimensions,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  createRoute,
  calculateRouteMetrics,
  checkRouteDepth,
  getSpeedLimitsAlongRoute,
  saveRoute,
  getRoutes,
  deleteRoute,
  getDefaultBoatRouteProfile,
  getNextWaypointNav,
  type Route,
  type LatLng,
  type RouteMetrics,
  type ShallowWarning,
  type BoatRouteProfile,
} from '../services/routePlanner';
import {
  checkRouteDepthWithTides,
  type RouteLegDepthForecast,
  SAFETY_COLORS,
  SAFETY_LABELS,
} from '../services/dynamicDepths';
import { formatDuration } from '../services/fuelCalculator';
import type { RootStackProps } from '../types/navigation';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

type Props = RootStackProps<'RoutePlanner'>;

// ── Nav Mode State ───────────────────────────────────────────────────────────

interface NavState {
  active: boolean;
  currentWaypointIndex: number;
  bearing: number;
  bearingLabel: string;
  distanceNm: number;
  distanceMi: number;
  isLastWaypoint: boolean;
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function RoutePlannerScreen({ route: navRoute, navigation }: Props) {
  const destinationLat = navRoute.params?.destinationLat;
  const destinationLon = navRoute.params?.destinationLon;
  const destinationName = navRoute.params?.destinationName;

  // State
  const [waypoints, setWaypoints] = useState<LatLng[]>([]);
  const [currentRoute, setCurrentRoute] = useState<Route | null>(null);
  const [metrics, setMetrics] = useState<RouteMetrics | null>(null);
  const [boatProfile, setBoatProfile] = useState<BoatRouteProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [shallowWarnings, setShallowWarnings] = useState<ShallowWarning[]>([]);
  const [savedRoutes, setSavedRoutes] = useState<Route[]>([]);
  const [showSavedRoutes, setShowSavedRoutes] = useState(false);
  const [routeName, setRouteName] = useState('');
  const [navState, setNavState] = useState<NavState>({
    active: false,
    currentWaypointIndex: 1,
    bearing: 0,
    bearingLabel: '---',
    distanceNm: 0,
    distanceMi: 0,
    isLastWaypoint: false,
  });
  const [currentPosition, setCurrentPosition] = useState<LatLng | null>(null);
  const [bottomSheetExpanded, setBottomSheetExpanded] = useState(true);
  const [tidalDepthForecasts, setTidalDepthForecasts] = useState<RouteLegDepthForecast[]>([]);

  // Load boat profile
  useEffect(() => {
    getDefaultBoatRouteProfile().then(setBoatProfile);
  }, []);

  // Initialize with destination if provided
  useEffect(() => {
    if (destinationLat != null && destinationLon != null) {
      // Get current location as start point
      Location.requestForegroundPermissionsAsync().then(({ status }) => {
        if (status === 'granted') {
          Location.getCurrentPositionAsync({}).then((loc) => {
            const start: LatLng = { lat: loc.coords.latitude, lon: loc.coords.longitude };
            const end: LatLng = { lat: destinationLat, lon: destinationLon };
            setWaypoints([start, end]);
            setCurrentPosition(start);
            if (destinationName) {
              setRouteName(`To ${destinationName}`);
            }
          });
        }
      });
    } else {
      // Just get current location
      Location.requestForegroundPermissionsAsync().then(({ status }) => {
        if (status === 'granted') {
          Location.getCurrentPositionAsync({}).then((loc) => {
            setCurrentPosition({
              lat: loc.coords.latitude,
              lon: loc.coords.longitude,
            });
          });
        }
      });
    }
  }, [destinationLat, destinationLon, destinationName]);

  // Recalculate route when waypoints change
  useEffect(() => {
    if (waypoints.length < 2 || !boatProfile) {
      setCurrentRoute(null);
      setMetrics(null);
      return;
    }

    const route = createRoute(waypoints, routeName || undefined);
    const routeMetrics = calculateRouteMetrics(route, boatProfile);
    setCurrentRoute({ ...route, metrics: routeMetrics });
    setMetrics(routeMetrics);

    // Check depth in background
    checkRouteDepth(route, boatProfile.draftMeters).then(({ warnings }) => {
      setShallowWarnings(warnings);
    });

    // Check tidal depth forecasts for the route
    const segmentsForTide = route.segments.map((seg, idx) => ({
      lat: (seg.from.lat + seg.to.lat) / 2,
      lon: (seg.from.lon + seg.to.lon) / 2,
      chartedDepthM: seg.minDepthM,
      etaHours: idx * (routeMetrics.adjustedTimeHours / Math.max(route.segments.length, 1)),
    }));
    checkRouteDepthWithTides(segmentsForTide, boatProfile.draftMeters).then((forecasts) => {
      setTidalDepthForecasts(forecasts);
    });
  }, [waypoints, boatProfile, routeName]);

  // Load saved routes
  useEffect(() => {
    getRoutes().then(setSavedRoutes);
  }, []);

  // Navigation mode location tracking
  useEffect(() => {
    if (!navState.active || !currentRoute) return;

    let subscription: Location.LocationSubscription | null = null;

    Location.watchPositionAsync(
      {
        accuracy: Location.Accuracy.High,
        distanceInterval: 10, // update every 10m
        timeInterval: 2000,
      },
      (loc) => {
        const pos: LatLng = {
          lat: loc.coords.latitude,
          lon: loc.coords.longitude,
        };
        setCurrentPosition(pos);

        // Update nav to next waypoint
        const nav = getNextWaypointNav(pos, currentRoute, navState.currentWaypointIndex);
        if (nav) {
          setNavState((prev) => ({
            ...prev,
            bearing: nav.bearing,
            bearingLabel: nav.bearingLabel,
            distanceNm: nav.distanceNm,
            distanceMi: nav.distanceMi,
            isLastWaypoint: nav.isLastWaypoint,
          }));

          // Auto-advance to next waypoint when within 0.05nm (~100m)
          if (nav.distanceNm < 0.05 && !nav.isLastWaypoint) {
            setNavState((prev) => ({
              ...prev,
              currentWaypointIndex: prev.currentWaypointIndex + 1,
            }));
          }
        }
      },
    ).then((sub) => {
      subscription = sub;
    });

    return () => {
      subscription?.remove();
    };
  }, [navState.active, navState.currentWaypointIndex, currentRoute]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleAddWaypoint = useCallback(() => {
    if (!currentPosition) {
      Alert.alert('Location Unavailable', 'Cannot determine your current position.');
      return;
    }

    hapticLight();
    // Add a waypoint slightly offset from current position (user would tap map in real implementation)
    const offset = waypoints.length * 0.005;
    setWaypoints((prev) => [
      ...prev,
      {
        lat: currentPosition.lat + offset,
        lon: currentPosition.lon + offset,
      },
    ]);
  }, [currentPosition, waypoints.length]);

  const handleRemoveLastWaypoint = useCallback(() => {
    hapticLight();
    setWaypoints((prev) => prev.slice(0, -1));
  }, []);

  const handleClearRoute = useCallback(() => {
    Alert.alert('Clear Route', 'Remove all waypoints?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Clear',
        style: 'destructive',
        onPress: () => {
          hapticLight();
          setWaypoints([]);
          setCurrentRoute(null);
          setMetrics(null);
          setShallowWarnings([]);
          setRouteName('');
        },
      },
    ]);
  }, []);

  const handleSaveRoute = useCallback(async () => {
    if (!currentRoute) return;
    hapticLight();

    const name = routeName || `Route ${new Date().toLocaleDateString()}`;
    await saveRoute(currentRoute, name);
    const routes = await getRoutes();
    setSavedRoutes(routes);
    Alert.alert('Route Saved', `"${name}" has been saved.`);
  }, [currentRoute, routeName]);

  const handleLoadRoute = useCallback((route: Route) => {
    hapticLight();
    setWaypoints(route.waypoints.map((wp) => wp.position));
    setRouteName(route.name);
    setShowSavedRoutes(false);
  }, []);

  const handleDeleteSavedRoute = useCallback(async (routeId: string) => {
    await deleteRoute(routeId);
    const routes = await getRoutes();
    setSavedRoutes(routes);
  }, []);

  const handleStartNav = useCallback(() => {
    if (!currentRoute || currentRoute.waypoints.length < 2) return;
    hapticLight();
    setNavState({
      active: true,
      currentWaypointIndex: 1, // Navigate toward first waypoint after start
      bearing: 0,
      bearingLabel: '---',
      distanceNm: 0,
      distanceMi: 0,
      isLastWaypoint: false,
    });
  }, [currentRoute]);

  const handleStopNav = useCallback(() => {
    hapticLight();
    setNavState({
      active: false,
      currentWaypointIndex: 1,
      bearing: 0,
      bearingLabel: '---',
      distanceNm: 0,
      distanceMi: 0,
      isLastWaypoint: false,
    });
  }, []);

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <View style={styles.screen}>
      {/* Map Placeholder Area */}
      <View style={styles.mapArea}>
        <View style={styles.mapPlaceholder}>
          <Ionicons name="map-outline" size={48} color={palette.water} />
          <Text style={styles.mapPlaceholderText}>
            Route Map
          </Text>
          <Text style={styles.mapPlaceholderSub}>
            {waypoints.length === 0
              ? 'Add waypoints to plan a route'
              : `${waypoints.length} waypoint${waypoints.length !== 1 ? 's' : ''} placed`}
          </Text>
          {/* Show waypoint markers */}
          {waypoints.length > 0 && (
            <View style={styles.waypointIndicators}>
              {waypoints.map((wp, idx) => (
                <View key={idx} style={styles.waypointMarker}>
                  <View style={[
                    styles.waypointDot,
                    idx === 0 && styles.waypointDotStart,
                    idx === waypoints.length - 1 && styles.waypointDotEnd,
                  ]}>
                    <Text style={styles.waypointIndex}>{idx + 1}</Text>
                  </View>
                  <Text style={styles.waypointCoord}>
                    {wp.lat.toFixed(3)}, {wp.lon.toFixed(3)}
                  </Text>
                </View>
              ))}
            </View>
          )}

          {/* Shallow warnings */}
          {shallowWarnings.length > 0 && (
            <View style={styles.warningBanner}>
              <Ionicons name="warning" size={16} color="#F44336" />
              <Text style={styles.warningText}>
                {shallowWarnings.length} shallow area{shallowWarnings.length !== 1 ? 's' : ''} detected along route
              </Text>
            </View>
          )}
        </View>

        {/* Map action buttons */}
        <View style={styles.mapActions}>
          <Pressable
            style={({ pressed }) => [styles.mapActionBtn, pressed && styles.btnPressed]}
            onPress={handleAddWaypoint}
          >
            <Ionicons name="add-circle" size={20} color={palette.accent} />
            <Text style={styles.mapActionLabel}>Add Point</Text>
          </Pressable>
          {waypoints.length > 0 && (
            <Pressable
              style={({ pressed }) => [styles.mapActionBtn, pressed && styles.btnPressed]}
              onPress={handleRemoveLastWaypoint}
            >
              <Ionicons name="remove-circle" size={20} color={palette.error} />
              <Text style={styles.mapActionLabel}>Undo</Text>
            </Pressable>
          )}
          {waypoints.length > 0 && (
            <Pressable
              style={({ pressed }) => [styles.mapActionBtn, pressed && styles.btnPressed]}
              onPress={handleClearRoute}
            >
              <Ionicons name="trash-outline" size={20} color={palette.error} />
              <Text style={styles.mapActionLabel}>Clear</Text>
            </Pressable>
          )}
          <Pressable
            style={({ pressed }) => [styles.mapActionBtn, pressed && styles.btnPressed]}
            onPress={() => setShowSavedRoutes(!showSavedRoutes)}
          >
            <Ionicons name="folder-outline" size={20} color={palette.textSecondary} />
            <Text style={styles.mapActionLabel}>Saved</Text>
          </Pressable>
        </View>
      </View>

      {/* Navigation Mode HUD */}
      {navState.active && (
        <View style={styles.navHud}>
          <View style={styles.navHudHeader}>
            <Ionicons name="navigate" size={20} color="#fff" />
            <Text style={styles.navHudTitle}>Navigating</Text>
            <Pressable onPress={handleStopNav} style={styles.navStopBtn}>
              <Text style={styles.navStopText}>Stop</Text>
            </Pressable>
          </View>
          <View style={styles.navHudBody}>
            <View style={styles.navHudStat}>
              <Text style={styles.navHudValue}>{navState.bearingLabel}</Text>
              <Text style={styles.navHudLabel}>Bearing</Text>
            </View>
            <View style={styles.navHudDivider} />
            <View style={styles.navHudStat}>
              <Text style={styles.navHudValue}>{navState.distanceNm} nm</Text>
              <Text style={styles.navHudLabel}>Distance</Text>
            </View>
            <View style={styles.navHudDivider} />
            <View style={styles.navHudStat}>
              <Text style={styles.navHudValue}>
                WP {navState.currentWaypointIndex + 1}/{currentRoute?.waypoints.length ?? 0}
              </Text>
              <Text style={styles.navHudLabel}>Next Waypoint</Text>
            </View>
          </View>
        </View>
      )}

      {/* Bottom Sheet — Route Metrics */}
      <View style={[styles.bottomSheet, !bottomSheetExpanded && styles.bottomSheetCollapsed]}>
        <Pressable
          style={styles.bottomSheetHandle}
          onPress={() => setBottomSheetExpanded(!bottomSheetExpanded)}
        >
          <View style={styles.handleBar} />
        </Pressable>

        {/* Route Name Input */}
        <View style={styles.routeNameRow}>
          <Ionicons name="flag-outline" size={18} color={palette.accent} />
          <TextInput
            style={styles.routeNameInput}
            placeholder="Route name..."
            placeholderTextColor={palette.textMuted}
            value={routeName}
            onChangeText={setRouteName}
          />
        </View>

        {/* Saved Routes List */}
        {showSavedRoutes && (
          <View style={styles.savedRoutesSection}>
            <Text style={styles.savedRoutesTitle}>Saved Routes</Text>
            {savedRoutes.length === 0 ? (
              <Text style={styles.noRoutesText}>No saved routes yet</Text>
            ) : (
              <ScrollView style={styles.savedRoutesList} nestedScrollEnabled>
                {savedRoutes.map((sr) => (
                  <Pressable
                    key={sr.id}
                    style={({ pressed }) => [styles.savedRouteCard, pressed && styles.btnPressed]}
                    onPress={() => handleLoadRoute(sr)}
                  >
                    <View style={styles.savedRouteInfo}>
                      <Text style={styles.savedRouteName}>{sr.name}</Text>
                      <Text style={styles.savedRouteMeta}>
                        {sr.waypoints.length} waypoints
                        {sr.metrics ? ` | ${sr.metrics.totalDistanceNm} nm` : ''}
                      </Text>
                    </View>
                    <Pressable
                      onPress={() => handleDeleteSavedRoute(sr.id)}
                      hitSlop={8}
                    >
                      <Ionicons name="trash-outline" size={18} color={palette.error} />
                    </Pressable>
                  </Pressable>
                ))}
              </ScrollView>
            )}
          </View>
        )}

        {/* Route Metrics */}
        {metrics && bottomSheetExpanded && (
          <ScrollView style={styles.metricsScroll} showsVerticalScrollIndicator={false}>
            {/* Distance & Time */}
            <View style={styles.metricsRow}>
              <View style={styles.metricCard}>
                <Ionicons name="speedometer-outline" size={20} color={palette.accent} />
                <Text style={styles.metricValue}>{metrics.totalDistanceNm} nm</Text>
                <Text style={styles.metricLabel}>Distance</Text>
                <Text style={styles.metricSub}>{metrics.totalDistanceMi} mi</Text>
              </View>
              <View style={styles.metricCard}>
                <Ionicons name="time-outline" size={20} color="#4CAF50" />
                <Text style={styles.metricValue}>{metrics.adjustedTimeLabel}</Text>
                <Text style={styles.metricLabel}>ETA</Text>
                <Text style={styles.metricSub}>@ {metrics.cruiseSpeedKnots} kts</Text>
              </View>
              <View style={styles.metricCard}>
                <Ionicons name="water-outline" size={20} color="#E65100" />
                <Text style={styles.metricValue}>{metrics.fuelNeededGallons} gal</Text>
                <Text style={styles.metricLabel}>Fuel</Text>
                {metrics.fuelCostEstimate != null && (
                  <Text style={styles.metricSub}>${metrics.fuelCostEstimate.toFixed(2)}</Text>
                )}
              </View>
            </View>

            {/* Shallow Warnings */}
            {shallowWarnings.length > 0 && (
              <View style={styles.warningCard}>
                <View style={styles.warningHeader}>
                  <Ionicons name="warning" size={18} color="#F44336" />
                  <Text style={styles.warningTitle}>Shallow Water Warnings</Text>
                </View>
                {shallowWarnings.map((warn, idx) => (
                  <View key={idx} style={styles.warningItem}>
                    <Text style={styles.warningItemText}>
                      Segment {warn.segmentIndex + 1}: Depth {warn.depthM.toFixed(1)}m
                      (need {warn.draftM.toFixed(1)}m)
                    </Text>
                  </View>
                ))}
              </View>
            )}

            {/* Tidal Depth Forecast Warnings */}
            {tidalDepthForecasts.filter((f) => f.safety !== 'safe').length > 0 && (
              <View style={styles.warningCard}>
                <View style={styles.warningHeader}>
                  <Ionicons name="water" size={18} color="#1E88E5" />
                  <Text style={[styles.warningTitle, { color: '#1565C0' }]}>
                    Tide-Adjusted Depth Warnings
                  </Text>
                </View>
                {tidalDepthForecasts
                  .filter((f) => f.safety !== 'safe')
                  .map((forecast) => (
                    <View key={forecast.segmentIndex} style={styles.warningItem}>
                      <Text style={[styles.warningItemText, { color: SAFETY_COLORS[forecast.safety] }]}>
                        Segment {forecast.segmentIndex + 1}: {forecast.adjustedDepthM.toFixed(1)}m at{' '}
                        {new Date(forecast.forecastTimestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        {' '}({SAFETY_LABELS[forecast.safety]})
                      </Text>
                    </View>
                  ))}
              </View>
            )}

            {/* Speed Zones */}
            {metrics.speedZones.length > 0 && (
              <View style={styles.speedZoneCard}>
                <View style={styles.speedZoneHeader}>
                  <Ionicons name="speedometer" size={18} color="#FF9800" />
                  <Text style={styles.speedZoneTitle}>Speed Zones</Text>
                </View>
                {metrics.speedZones.map((zone, idx) => (
                  <View key={idx} style={styles.speedZoneItem}>
                    <Ionicons
                      name={zone.isNoWake ? 'water' : 'speedometer-outline'}
                      size={14}
                      color={zone.isNoWake ? '#F44336' : '#FF9800'}
                    />
                    <Text style={styles.speedZoneText}>
                      {zone.name}
                      {!zone.isNoWake && ` (${zone.speedLimitKnots} kts max)`}
                    </Text>
                  </View>
                ))}
              </View>
            )}

            {/* Action Buttons */}
            <View style={styles.actionRow}>
              <Pressable
                style={({ pressed }) => [styles.navButton, pressed && styles.btnPressed]}
                onPress={handleStartNav}
                disabled={navState.active}
              >
                <Ionicons name="navigate" size={20} color="#fff" />
                <Text style={styles.navButtonText}>Navigate</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [styles.saveButton, pressed && styles.btnPressed]}
                onPress={handleSaveRoute}
              >
                <Ionicons name="bookmark-outline" size={20} color={palette.accent} />
                <Text style={styles.saveButtonText}>Save</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [styles.vizButton, pressed && styles.btnPressed]}
                onPress={() => {
                  hapticLight();
                  navigation.navigate('TripVisualization', {
                    routeId: currentRoute?.id,
                  });
                }}
              >
                <Ionicons name="analytics-outline" size={20} color="#4CAF50" />
                <Text style={styles.vizButtonText}>Preview</Text>
              </Pressable>
            </View>
          </ScrollView>
        )}

        {/* Empty state */}
        {!metrics && waypoints.length < 2 && (
          <View style={styles.emptyState}>
            <Ionicons name="navigate-outline" size={32} color={palette.textDim} />
            <Text style={styles.emptyStateText}>
              Add at least 2 waypoints to plan a route
            </Text>
            <Text style={styles.emptyStateSub}>
              Tap "Add Point" to place waypoints on the map
            </Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  mapArea: {
    flex: 1,
  },
  mapPlaceholder: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.waterLight,
    gap: 8,
    padding: 20,
  },
  mapPlaceholderText: {
    fontFamily: fonts.serif,
    fontSize: 18,
    color: palette.waterDeep,
  },
  mapPlaceholderSub: {
    fontSize: 13,
    color: palette.textMuted,
  },
  waypointIndicators: {
    marginTop: 16,
    gap: 8,
    width: '100%',
    paddingHorizontal: 20,
  },
  waypointMarker: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  waypointDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  waypointDotStart: {
    backgroundColor: '#4CAF50',
  },
  waypointDotEnd: {
    backgroundColor: '#F44336',
  },
  waypointIndex: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '700',
  },
  waypointCoord: {
    fontSize: 12,
    color: palette.textSecondary,
    fontVariant: ['tabular-nums'],
  },
  warningBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 12,
    backgroundColor: '#FFEBEE',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  warningText: {
    fontSize: 13,
    color: '#C62828',
    fontWeight: '600',
  },
  mapActions: {
    flexDirection: 'row',
    gap: 8,
    padding: 12,
    backgroundColor: palette.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
  },
  mapActionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.surfaceRaised,
  },
  mapActionLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  btnPressed: {
    opacity: 0.7,
    transform: [{ scale: 0.97 }],
  },

  // ── Nav HUD ──────────────────────────────────────────────────────
  navHud: {
    backgroundColor: palette.accentDeep,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  navHudHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  navHudTitle: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '700',
    flex: 1,
  },
  navStopBtn: {
    backgroundColor: 'rgba(255,255,255,0.2)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
  },
  navStopText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
  navHudBody: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  navHudStat: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
  },
  navHudValue: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  navHudLabel: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 11,
    fontWeight: '500',
  },
  navHudDivider: {
    width: 1,
    height: 30,
    backgroundColor: 'rgba(255,255,255,0.2)',
  },

  // ── Bottom Sheet ──────────────────────────────────────────────────
  bottomSheet: {
    backgroundColor: palette.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: -4 },
    elevation: 8,
    maxHeight: '50%',
    paddingHorizontal: 16,
    paddingBottom: Platform.OS === 'ios' ? 34 : 16,
  },
  bottomSheetCollapsed: {
    maxHeight: 80,
  },
  bottomSheetHandle: {
    alignItems: 'center',
    paddingVertical: 10,
  },
  handleBar: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.border,
  },

  // ── Route Name ────────────────────────────────────────────────────
  routeNameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  routeNameInput: {
    flex: 1,
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
    paddingVertical: 4,
  },

  // ── Saved Routes ──────────────────────────────────────────────────
  savedRoutesSection: {
    marginBottom: 12,
    gap: 8,
  },
  savedRoutesTitle: {
    fontFamily: fonts.serif,
    fontSize: 15,
    color: palette.textSecondary,
  },
  noRoutesText: {
    fontSize: 13,
    color: palette.textMuted,
    textAlign: 'center',
    paddingVertical: 12,
  },
  savedRoutesList: {
    maxHeight: 150,
  },
  savedRouteCard: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    marginBottom: 6,
  },
  savedRouteInfo: {
    flex: 1,
    gap: 2,
  },
  savedRouteName: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  savedRouteMeta: {
    fontSize: 12,
    color: palette.textMuted,
  },

  // ── Metrics ───────────────────────────────────────────────────────
  metricsScroll: {
    flex: 1,
  },
  metricsRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
  },
  metricCard: {
    flex: 1,
    alignItems: 'center',
    gap: 4,
    padding: 12,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 12,
  },
  metricValue: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  metricLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  metricSub: {
    fontSize: 11,
    color: palette.textDim,
  },

  // ── Warning card ──────────────────────────────────────────────────
  warningCard: {
    backgroundColor: '#FFF3E0',
    borderRadius: 12,
    padding: 12,
    gap: 8,
    marginBottom: 12,
  },
  warningHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  warningTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#E65100',
  },
  warningItem: {
    paddingLeft: 26,
  },
  warningItemText: {
    fontSize: 13,
    color: '#BF360C',
  },

  // ── Speed zone card ───────────────────────────────────────────────
  speedZoneCard: {
    backgroundColor: '#FFF8E1',
    borderRadius: 12,
    padding: 12,
    gap: 8,
    marginBottom: 12,
  },
  speedZoneHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  speedZoneTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#E65100',
  },
  speedZoneItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingLeft: 8,
  },
  speedZoneText: {
    fontSize: 13,
    color: '#F57F17',
  },

  // ── Action buttons ────────────────────────────────────────────────
  actionRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
    marginBottom: 8,
  },
  navButton: {
    flex: 2,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    paddingVertical: 14,
    borderRadius: 12,
  },
  navButtonText: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '700',
  },
  saveButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderWidth: 1.5,
    borderColor: palette.accent,
    paddingVertical: 14,
    borderRadius: 12,
  },
  saveButtonText: {
    color: palette.accent,
    fontSize: 14,
    fontWeight: '700',
  },
  vizButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderWidth: 1.5,
    borderColor: '#4CAF50',
    paddingVertical: 14,
    borderRadius: 12,
  },
  vizButtonText: {
    color: '#4CAF50',
    fontSize: 14,
    fontWeight: '700',
  },

  // ── Empty state ───────────────────────────────────────────────────
  emptyState: {
    alignItems: 'center',
    paddingVertical: 24,
    gap: 8,
  },
  emptyStateText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textMuted,
    textAlign: 'center',
  },
  emptyStateSub: {
    fontSize: 12,
    color: palette.textDim,
    textAlign: 'center',
  },
});
