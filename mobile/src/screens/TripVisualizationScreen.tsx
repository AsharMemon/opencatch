/**
 * OpenCatch — Trip Visualization Screen
 *
 * Before trip: map with route + weather/wind/wave forecast along route
 * After trip: map with planned (blue) vs actual (green) track overlay
 * Stats card: distance, time, avg speed, fuel used
 * Timeline of conditions during the trip
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Dimensions,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  previewTrip,
  getRecordedTrips,
  comparePlannedVsActual,
  getTripSummary,
  buildConditionsTimeline,
  type TripPreview,
  type RecordedTrip,
  type PlannedVsActualComparison,
  type TripSummary,
  type ConditionTimelineEntry,
  type WeatherAtPoint,
} from '../services/tripVisualization';
import { getRoutes, type Route } from '../services/routePlanner';
import type { RootStackProps } from '../types/navigation';

type Props = RootStackProps<'TripVisualization'>;

type ViewMode = 'preview' | 'review';

// ── Main Screen ──────────────────────────────────────────────────────────────

export function TripVisualizationScreen({ route: navRoute, navigation }: Props) {
  const routeId = navRoute.params?.routeId;
  const tripId = navRoute.params?.tripId;

  const [viewMode, setViewMode] = useState<ViewMode>(tripId ? 'review' : 'preview');
  const [loading, setLoading] = useState(true);

  // Preview mode state
  const [selectedRoute, setSelectedRoute] = useState<Route | null>(null);
  const [tripPreview, setTripPreview] = useState<TripPreview | null>(null);
  const [departureTime, setDepartureTime] = useState(new Date().toISOString());

  // Review mode state
  const [recordedTrip, setRecordedTrip] = useState<RecordedTrip | null>(null);
  const [tripSummary, setTripSummary] = useState<TripSummary | null>(null);
  const [comparison, setComparison] = useState<PlannedVsActualComparison | null>(null);
  const [timeline, setTimeline] = useState<ConditionTimelineEntry[]>([]);

  // All recorded trips for selection
  const [recordedTrips, setRecordedTrips] = useState<RecordedTrip[]>([]);
  const [availableRoutes, setAvailableRoutes] = useState<Route[]>([]);

  // Load data
  useEffect(() => {
    async function loadData() {
      setLoading(true);

      const routes = await getRoutes();
      setAvailableRoutes(routes);

      const trips = await getRecordedTrips();
      setRecordedTrips(trips);

      // Load specific route if provided
      if (routeId) {
        const route = routes.find((r) => r.id === routeId);
        if (route) {
          setSelectedRoute(route);
          // Generate preview
          const preview = await previewTrip(route, departureTime);
          setTripPreview(preview);
        }
      }

      // Load specific trip if provided
      if (tripId) {
        const trip = trips.find((t) => t.id === tripId);
        if (trip) {
          setRecordedTrip(trip);
          setTripSummary(getTripSummary(trip));
          setTimeline(buildConditionsTimeline(trip));

          // Compare with planned route if available
          if (trip.routeId) {
            const plannedRoute = routes.find((r) => r.id === trip.routeId);
            if (plannedRoute) {
              setSelectedRoute(plannedRoute);
              setComparison(comparePlannedVsActual(plannedRoute, trip));
            }
          }
        }
      }

      setLoading(false);
    }

    loadData();
  }, [routeId, tripId, departureTime]);

  const handleSelectRoute = useCallback(async (route: Route) => {
    hapticLight();
    setSelectedRoute(route);
    setLoading(true);
    const preview = await previewTrip(route, departureTime);
    setTripPreview(preview);
    setLoading(false);
  }, [departureTime]);

  const handleSelectTrip = useCallback((trip: RecordedTrip) => {
    hapticLight();
    setRecordedTrip(trip);
    setTripSummary(getTripSummary(trip));
    setTimeline(buildConditionsTimeline(trip));

    if (trip.routeId) {
      const plannedRoute = availableRoutes.find((r) => r.id === trip.routeId);
      if (plannedRoute) {
        setSelectedRoute(plannedRoute);
        setComparison(comparePlannedVsActual(plannedRoute, trip));
      }
    }
  }, [availableRoutes]);

  // ── Render ───────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <View style={[styles.screen, styles.loadingContainer]}>
        <ActivityIndicator color={palette.accent} size="large" />
        <Text style={styles.loadingText}>Loading trip data...</Text>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      {/* Mode Toggle */}
      <View style={styles.modeToggle}>
        <Pressable
          style={[styles.modeBtn, viewMode === 'preview' && styles.modeBtnActive]}
          onPress={() => { hapticLight(); setViewMode('preview'); }}
        >
          <Ionicons
            name="eye-outline"
            size={16}
            color={viewMode === 'preview' ? '#fff' : palette.textSecondary}
          />
          <Text style={[styles.modeBtnText, viewMode === 'preview' && styles.modeBtnTextActive]}>
            Preview
          </Text>
        </Pressable>
        <Pressable
          style={[styles.modeBtn, viewMode === 'review' && styles.modeBtnActive]}
          onPress={() => { hapticLight(); setViewMode('review'); }}
        >
          <Ionicons
            name="analytics-outline"
            size={16}
            color={viewMode === 'review' ? '#fff' : palette.textSecondary}
          />
          <Text style={[styles.modeBtnText, viewMode === 'review' && styles.modeBtnTextActive]}>
            Review
          </Text>
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {viewMode === 'preview' ? (
          <PreviewView
            route={selectedRoute}
            preview={tripPreview}
            routes={availableRoutes}
            onSelectRoute={handleSelectRoute}
          />
        ) : (
          <ReviewView
            trip={recordedTrip}
            summary={tripSummary}
            comparison={comparison}
            timeline={timeline}
            trips={recordedTrips}
            route={selectedRoute}
            onSelectTrip={handleSelectTrip}
          />
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Preview View ─────────────────────────────────────────────────────────────

function PreviewView({
  route,
  preview,
  routes,
  onSelectRoute,
}: {
  route: Route | null;
  preview: TripPreview | null;
  routes: Route[];
  onSelectRoute: (route: Route) => void;
}) {
  if (!route) {
    return (
      <View style={styles.selectorSection}>
        <Text style={styles.sectionTitle}>Select a Route to Preview</Text>
        {routes.length === 0 ? (
          <View style={styles.emptyState}>
            <Ionicons name="navigate-outline" size={32} color={palette.textDim} />
            <Text style={styles.emptyText}>No saved routes</Text>
            <Text style={styles.emptySubtext}>Plan a route first in the Route Planner</Text>
          </View>
        ) : (
          routes.map((r) => (
            <Pressable
              key={r.id}
              style={({ pressed }) => [styles.routeSelectCard, pressed && styles.pressed]}
              onPress={() => onSelectRoute(r)}
            >
              <Ionicons name="navigate-outline" size={20} color={palette.accent} />
              <View style={styles.routeSelectInfo}>
                <Text style={styles.routeSelectName}>{r.name}</Text>
                <Text style={styles.routeSelectMeta}>
                  {r.waypoints.length} waypoints
                  {r.metrics ? ` | ${r.metrics.totalDistanceNm} nm | ${r.metrics.adjustedTimeLabel}` : ''}
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={16} color={palette.textMuted} />
            </Pressable>
          ))
        )}
      </View>
    );
  }

  return (
    <>
      {/* Map Preview Placeholder */}
      <View style={styles.mapPreview}>
        <View style={styles.mapPreviewInner}>
          <Ionicons name="map" size={40} color={palette.water} />
          <Text style={styles.mapPreviewTitle}>Route Preview</Text>
          <Text style={styles.mapPreviewSub}>{route.name}</Text>
          <View style={styles.routeLineIndicator}>
            <View style={styles.routeLineDot} />
            <View style={styles.routeLine} />
            <View style={[styles.routeLineDot, styles.routeLineDotEnd]} />
          </View>
        </View>
      </View>

      {/* Weather Summary */}
      {preview && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Weather Along Route</Text>
          <Text style={styles.weatherSummary}>{preview.weatherSummary}</Text>

          {/* Weather at points */}
          {preview.weatherAlongRoute.length > 0 && (
            <View style={styles.weatherPoints}>
              {preview.weatherAlongRoute.map((wp, idx) => (
                <WeatherPointCard key={idx} weather={wp} />
              ))}
            </View>
          )}
        </View>
      )}

      {/* Advisories */}
      {preview && preview.advisories.length > 0 && (
        <View style={[styles.card, styles.advisoryCard]}>
          <View style={styles.advisoryHeader}>
            <Ionicons name="warning" size={18} color="#E65100" />
            <Text style={styles.advisoryTitle}>Safety Advisories</Text>
          </View>
          {preview.advisories.map((adv, idx) => (
            <View key={idx} style={styles.advisoryItem}>
              <Ionicons name="alert-circle" size={14} color="#FF9800" />
              <Text style={styles.advisoryText}>{adv}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Route Metrics */}
      {route.metrics && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Route Details</Text>
          <View style={styles.statsGrid}>
            <StatCell icon="speedometer-outline" color={palette.accent} value={`${route.metrics.totalDistanceNm} nm`} label="Distance" />
            <StatCell icon="time-outline" color="#4CAF50" value={route.metrics.adjustedTimeLabel} label="ETA" />
            <StatCell icon="water-outline" color="#E65100" value={`${route.metrics.fuelNeededGallons} gal`} label="Fuel" />
            <StatCell icon="cash-outline" color="#7C3AED" value={route.metrics.fuelCostEstimate != null ? `$${route.metrics.fuelCostEstimate.toFixed(2)}` : '--'} label="Cost" />
          </View>
        </View>
      )}
    </>
  );
}

// ── Review View ──────────────────────────────────────────────────────────────

function ReviewView({
  trip,
  summary,
  comparison,
  timeline,
  trips,
  route,
  onSelectTrip,
}: {
  trip: RecordedTrip | null;
  summary: TripSummary | null;
  comparison: PlannedVsActualComparison | null;
  timeline: ConditionTimelineEntry[];
  trips: RecordedTrip[];
  route: Route | null;
  onSelectTrip: (trip: RecordedTrip) => void;
}) {
  if (!trip) {
    return (
      <View style={styles.selectorSection}>
        <Text style={styles.sectionTitle}>Select a Trip to Review</Text>
        {trips.length === 0 ? (
          <View style={styles.emptyState}>
            <Ionicons name="trail-sign-outline" size={32} color={palette.textDim} />
            <Text style={styles.emptyText}>No recorded trips</Text>
            <Text style={styles.emptySubtext}>Record a trip using the Route Planner</Text>
          </View>
        ) : (
          trips.map((t) => (
            <Pressable
              key={t.id}
              style={({ pressed }) => [styles.routeSelectCard, pressed && styles.pressed]}
              onPress={() => onSelectTrip(t)}
            >
              <Ionicons name="trail-sign" size={20} color="#4CAF50" />
              <View style={styles.routeSelectInfo}>
                <Text style={styles.routeSelectName}>{t.name}</Text>
                <Text style={styles.routeSelectMeta}>
                  {new Date(t.startTime).toLocaleDateString()} |{' '}
                  {t.trackPoints.length} points
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={16} color={palette.textMuted} />
            </Pressable>
          ))
        )}
      </View>
    );
  }

  return (
    <>
      {/* Map Comparison Placeholder */}
      <View style={styles.mapPreview}>
        <View style={styles.mapPreviewInner}>
          <Ionicons name="git-compare-outline" size={40} color={palette.accent} />
          <Text style={styles.mapPreviewTitle}>Trip Track</Text>
          <Text style={styles.mapPreviewSub}>{trip.name}</Text>
          {comparison && (
            <View style={styles.comparisonLegend}>
              <View style={styles.legendItem}>
                <View style={[styles.legendLine, { backgroundColor: palette.accent }]} />
                <Text style={styles.legendText}>Planned</Text>
              </View>
              <View style={styles.legendItem}>
                <View style={[styles.legendLine, { backgroundColor: '#4CAF50' }]} />
                <Text style={styles.legendText}>Actual</Text>
              </View>
            </View>
          )}
        </View>
      </View>

      {/* Trip Stats Card */}
      {summary && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Trip Summary</Text>
          <View style={styles.statsGrid}>
            <StatCell
              icon="speedometer-outline"
              color={palette.accent}
              value={`${summary.distanceCoveredNm} nm`}
              label="Distance"
            />
            <StatCell
              icon="time-outline"
              color="#4CAF50"
              value={summary.durationLabel}
              label="Duration"
            />
            <StatCell
              icon="speedometer"
              color="#7C3AED"
              value={`${summary.avgSpeedKnots} kts`}
              label="Avg Speed"
            />
            <StatCell
              icon="flash"
              color="#E65100"
              value={`${summary.maxSpeedKnots} kts`}
              label="Max Speed"
            />
          </View>
          <View style={[styles.statsGrid, { marginTop: 8 }]}>
            <StatCell
              icon="water-outline"
              color="#E65100"
              value={`${summary.estimatedFuelGallons} gal`}
              label="Est. Fuel"
            />
            <StatCell
              icon="navigate-outline"
              color={palette.accent}
              value={`${summary.distanceCoveredMi} mi`}
              label="Miles"
            />
            <StatCell
              icon="speedometer-outline"
              color="#4CAF50"
              value={`${summary.avgSpeedMph} mph`}
              label="Avg MPH"
            />
            <StatCell
              icon="flash-outline"
              color="#7C3AED"
              value={`${summary.maxSpeedMph} mph`}
              label="Max MPH"
            />
          </View>
        </View>
      )}

      {/* Planned vs Actual Deviation */}
      {comparison && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Route Accuracy</Text>
          <View style={styles.deviationRow}>
            <View style={styles.deviationStat}>
              <Text style={styles.deviationValue}>{comparison.avgDeviationNm} nm</Text>
              <Text style={styles.deviationLabel}>Avg Deviation</Text>
            </View>
            <View style={styles.deviationDivider} />
            <View style={styles.deviationStat}>
              <Text style={styles.deviationValue}>{comparison.maxDeviationNm} nm</Text>
              <Text style={styles.deviationLabel}>Max Deviation</Text>
            </View>
            <View style={styles.deviationDivider} />
            <View style={styles.deviationStat}>
              <Text style={styles.deviationValue}>{comparison.deviationPoints.length}</Text>
              <Text style={styles.deviationLabel}>Diversions</Text>
            </View>
          </View>
        </View>
      )}

      {/* Conditions Timeline */}
      {timeline.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Trip Timeline</Text>
          <View style={styles.timeline}>
            {timeline.map((entry, idx) => (
              <View key={idx} style={styles.timelineEntry}>
                <View style={styles.timelineLeft}>
                  <View style={styles.timelineDot} />
                  {idx < timeline.length - 1 && <View style={styles.timelineLine} />}
                </View>
                <View style={styles.timelineContent}>
                  <Text style={styles.timelineTime}>{entry.timeLabel}</Text>
                  <View style={styles.timelineStats}>
                    <Text style={styles.timelineStat}>
                      {entry.speedKnots.toFixed(1)} kts
                    </Text>
                    {entry.tempF != null && (
                      <Text style={styles.timelineStat}>{entry.tempF}°F</Text>
                    )}
                    {entry.weather && (
                      <Text style={styles.timelineStat}>{entry.weather}</Text>
                    )}
                  </View>
                </View>
              </View>
            ))}
          </View>
        </View>
      )}
    </>
  );
}

// ── Sub-components ───────────────────────────────────────────────────────────

function WeatherPointCard({ weather }: { weather: WeatherAtPoint }) {
  return (
    <View style={styles.weatherPointCard}>
      <View style={styles.weatherPointHeader}>
        <Ionicons name={weather.weatherIcon as any} size={18} color={palette.accent} />
        <Text style={styles.weatherPointDist}>
          {weather.distanceAlongNm.toFixed(1)} nm
        </Text>
      </View>
      <View style={styles.weatherPointStats}>
        <Text style={styles.weatherPointStat}>{weather.tempF}°F</Text>
        <Text style={styles.weatherPointStat}>
          {weather.windMph} mph {weather.windDirection}
        </Text>
        <Text style={styles.weatherPointStat}>{weather.weather}</Text>
      </View>
      {weather.precipPct > 0 && (
        <Text style={styles.weatherPointPrecip}>{weather.precipPct}% precip</Text>
      )}
    </View>
  );
}

function StatCell({
  icon,
  color,
  value,
  label,
}: {
  icon: string;
  color: string;
  value: string;
  label: string;
}) {
  return (
    <View style={styles.statCell}>
      <Ionicons name={icon as any} size={16} color={color} />
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  loadingContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  loadingText: {
    color: palette.textMuted,
    fontSize: 14,
  },
  content: {
    padding: 16,
    gap: 16,
  },

  // ── Mode Toggle ─────────────────────────────────────────────────
  modeToggle: {
    flexDirection: 'row',
    margin: 16,
    marginBottom: 0,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 3,
  },
  modeBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 8,
  },
  modeBtnActive: {
    backgroundColor: palette.accent,
  },
  modeBtnText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  modeBtnTextActive: {
    color: '#fff',
  },

  // ── Selector ────────────────────────────────────────────────────
  selectorSection: {
    gap: 12,
  },
  sectionTitle: {
    fontFamily: fonts.serif,
    fontSize: 18,
    color: palette.text,
  },
  routeSelectCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    backgroundColor: palette.surface,
    borderRadius: 12,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  routeSelectInfo: {
    flex: 1,
    gap: 2,
  },
  routeSelectName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  routeSelectMeta: {
    fontSize: 12,
    color: palette.textMuted,
  },
  pressed: {
    opacity: 0.7,
    transform: [{ scale: 0.98 }],
  },

  // ── Empty State ─────────────────────────────────────────────────
  emptyState: {
    alignItems: 'center',
    paddingVertical: 40,
    gap: 8,
  },
  emptyText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.textMuted,
  },
  emptySubtext: {
    fontSize: 13,
    color: palette.textDim,
    textAlign: 'center',
  },

  // ── Map Preview ─────────────────────────────────────────────────
  mapPreview: {
    backgroundColor: palette.waterLight,
    borderRadius: 16,
    overflow: 'hidden',
  },
  mapPreviewInner: {
    alignItems: 'center',
    paddingVertical: 40,
    gap: 8,
  },
  mapPreviewTitle: {
    fontFamily: fonts.serif,
    fontSize: 18,
    color: palette.waterDeep,
  },
  mapPreviewSub: {
    fontSize: 13,
    color: palette.textMuted,
  },
  routeLineIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 12,
    gap: 0,
  },
  routeLineDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#4CAF50',
  },
  routeLine: {
    width: 80,
    height: 3,
    backgroundColor: palette.accent,
  },
  routeLineDotEnd: {
    backgroundColor: '#F44336',
  },
  comparisonLegend: {
    flexDirection: 'row',
    gap: 20,
    marginTop: 12,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  legendLine: {
    width: 20,
    height: 3,
    borderRadius: 2,
  },
  legendText: {
    fontSize: 12,
    color: palette.textSecondary,
    fontWeight: '500',
  },

  // ── Card ────────────────────────────────────────────────────────
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardTitle: {
    fontFamily: fonts.serif,
    fontSize: 15,
    color: palette.textSecondary,
  },

  // ── Weather ─────────────────────────────────────────────────────
  weatherSummary: {
    fontSize: 14,
    color: palette.text,
    fontWeight: '500',
  },
  weatherPoints: {
    gap: 8,
  },
  weatherPointCard: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 10,
    gap: 4,
  },
  weatherPointHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  weatherPointDist: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  weatherPointStats: {
    flexDirection: 'row',
    gap: 12,
    paddingLeft: 24,
  },
  weatherPointStat: {
    fontSize: 12,
    color: palette.textMuted,
  },
  weatherPointPrecip: {
    fontSize: 11,
    color: palette.warning,
    paddingLeft: 24,
  },

  // ── Advisory ────────────────────────────────────────────────────
  advisoryCard: {
    backgroundColor: '#FFF3E0',
    borderWidth: 1,
    borderColor: '#FFE0B2',
  },
  advisoryHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  advisoryTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#E65100',
  },
  advisoryItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingLeft: 4,
  },
  advisoryText: {
    flex: 1,
    fontSize: 13,
    color: '#BF360C',
    lineHeight: 18,
  },

  // ── Stats Grid ──────────────────────────────────────────────────
  statsGrid: {
    flexDirection: 'row',
    gap: 8,
  },
  statCell: {
    flex: 1,
    alignItems: 'center',
    gap: 3,
    paddingVertical: 8,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
  },
  statValue: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  statLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },

  // ── Deviation ───────────────────────────────────────────────────
  deviationRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  deviationStat: {
    flex: 1,
    alignItems: 'center',
    gap: 4,
  },
  deviationValue: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  deviationLabel: {
    fontSize: 11,
    fontWeight: '500',
    color: palette.textMuted,
  },
  deviationDivider: {
    width: 1,
    height: 30,
    backgroundColor: palette.border,
  },

  // ── Timeline ────────────────────────────────────────────────────
  timeline: {
    gap: 0,
  },
  timelineEntry: {
    flexDirection: 'row',
    gap: 12,
    minHeight: 48,
  },
  timelineLeft: {
    alignItems: 'center',
    width: 20,
  },
  timelineDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: palette.accent,
    marginTop: 4,
  },
  timelineLine: {
    flex: 1,
    width: 2,
    backgroundColor: palette.borderLight,
    marginVertical: 2,
  },
  timelineContent: {
    flex: 1,
    gap: 4,
    paddingBottom: 12,
  },
  timelineTime: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  timelineStats: {
    flexDirection: 'row',
    gap: 10,
  },
  timelineStat: {
    fontSize: 12,
    color: palette.textMuted,
  },
});
