/**
 * OpenCatch -- Dynamic Depth Overlay
 *
 * Map overlay showing depth numbers that UPDATE IN REAL-TIME based on
 * current tide/water level at the nearest NOAA CO-OPS station.
 *
 * Key behaviors:
 *   - Color-coded by safety relative to boat draft (red/orange/yellow/green)
 *   - Numbers change as tide rises/falls
 *   - "Tide: +1.2m" badge shows current adjustment
 *   - Refreshes every 6 minutes (NOAA update cadence)
 *   - Integrates with route planner for forecasted depth safety
 *
 * Data flow:
 *   1. Get charted depths from DepthNumberOverlay / GEBCO
 *   2. Find nearest NOAA CO-OPS water level station
 *   3. Fetch real-time water level
 *   4. adjusted_depth = charted_depth + water_level
 *   5. Color by safety vs. boat draft
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, View, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import {
  getNearestWaterLevelStation,
  getCurrentWaterLevel,
  getAdjustedDepthGrid,
  getDynamicDepthSafety,
  formatTideBadge,
  formatTideBadgeFt,
  depthToFeet,
  SAFETY_COLORS,
  SAFETY_LABELS,
  REFRESH_INTERVAL_MS,
  type WaterLevelStation,
  type WaterLevel,
  type DynamicDepth,
  type ChartedDepthPoint,
  type BBox,
} from '../services/dynamicDepths';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  /** Current map center latitude */
  lat: number;
  /** Current map center longitude */
  lon: number;
  /** Current map zoom level */
  zoom: number;
  /** Boat draft in feet (from boatProfile) */
  boatDraftFt: number;
  /** 'metric' for meters, 'imperial' for feet */
  units?: 'metric' | 'imperial';
  /** Mapbox / MapLibre ShapeSource component */
  ShapeSource: any;
  /** Mapbox / MapLibre SymbolLayer component */
  SymbolLayer: any;
  /** Mapbox / MapLibre CircleLayer component */
  CircleLayer: any;
  /** Called when data starts loading */
  onLoadStart?: () => void;
  /** Called when data finishes loading */
  onLoadEnd?: () => void;
  /** Called with the tide badge text for parent display */
  onTideBadgeUpdate?: (badge: string, levelM: number) => void;
  /** Called with the station info for parent display */
  onStationUpdate?: (station: WaterLevelStation | null) => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

/** Minimum zoom to show dynamic depths */
const MIN_ZOOM = 12;

/** Spacing between sample points in degrees (~200m) */
const SAMPLE_SPACING_DEG = 0.002;

/** Grid radius in degrees */
const GRID_RADIUS_DEG = 0.04;

/** Minimum move distance before refetching (meters) */
const REFRESH_DISTANCE_M = 400;

/** GEBCO WMS endpoint for depth queries */
const GEBCO_WMS = 'https://www.gebco.net/data_and_products/gebco_web_services/web_map_service/mapserv';

// ── Helpers ──────────────────────────────────────────────────────────────────

function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Sample charted depths from GEBCO WMS across a grid.
 * Returns underwater points only (GEBCO negative = below sea level).
 */
async function sampleChartedDepths(
  centerLat: number,
  centerLon: number,
): Promise<ChartedDepthPoint[]> {
  const points: ChartedDepthPoint[] = [];
  const promises: Promise<void>[] = [];

  const latMin = centerLat - GRID_RADIUS_DEG;
  const latMax = centerLat + GRID_RADIUS_DEG;
  const lonMin = centerLon - GRID_RADIUS_DEG;
  const lonMax = centerLon + GRID_RADIUS_DEG;

  for (let lat = latMin; lat <= latMax + 1e-9; lat += SAMPLE_SPACING_DEG) {
    for (let lon = lonMin; lon <= lonMax + 1e-9; lon += SAMPLE_SPACING_DEG) {
      const sLat = parseFloat(lat.toFixed(5));
      const sLon = parseFloat(lon.toFixed(5));

      const url =
        `${GEBCO_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo` +
        `&LAYERS=GEBCO_LATEST&QUERY_LAYERS=GEBCO_LATEST&INFO_FORMAT=text/plain` +
        `&SRS=EPSG:4326&WIDTH=2&HEIGHT=2&X=1&Y=1` +
        `&BBOX=${sLon - 0.0001},${sLat - 0.0001},${sLon + 0.0001},${sLat + 0.0001}`;

      promises.push(
        fetch(url)
          .then(async (res) => {
            if (!res.ok) return;
            const text = await res.text();
            const match = text.match(/value_list\s*=\s*['"]?([-\d.]+)/);
            if (match) {
              const val = parseFloat(match[1]);
              // GEBCO: negative = below sea level
              if (val < 0) {
                points.push({ lat: sLat, lon: sLon, depthM: Math.abs(val) });
              }
            }
          })
          .catch(() => { /* skip failed points */ }),
      );
    }
  }

  // Batch concurrency
  const BATCH = 20;
  for (let i = 0; i < promises.length; i += BATCH) {
    await Promise.all(promises.slice(i, i + BATCH));
  }

  return points;
}

// ── GeoJSON Conversion ───────────────────────────────────────────────────────

function dynamicDepthsToGeoJSON(
  depths: DynamicDepth[],
  units: 'metric' | 'imperial',
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: depths.map((d) => {
      const displayDepth =
        units === 'imperial'
          ? depthToFeet(d.adjustedDepthM)
          : Math.round(d.adjustedDepthM * 10) / 10;

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [d.lon, d.lat],
        },
        properties: {
          depthDisplay: `${displayDepth}`,
          safetyColor: SAFETY_COLORS[d.safety],
          safety: d.safety,
          chartedDepthM: d.chartedDepthM,
          adjustedDepthM: d.adjustedDepthM,
          adjustmentM: d.adjustmentM,
        },
      };
    }),
  };
}

// ── Component ────────────────────────────────────────────────────────────────

export function DynamicDepthOverlay({
  lat,
  lon,
  zoom,
  boatDraftFt,
  units = 'imperial',
  ShapeSource,
  SymbolLayer,
  CircleLayer,
  onLoadStart,
  onLoadEnd,
  onTideBadgeUpdate,
  onStationUpdate,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [tideBadge, setTideBadge] = useState<string>('');
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const currentStationRef = useRef<WaterLevelStation | null>(null);
  const currentChartedRef = useRef<ChartedDepthPoint[]>([]);

  const boatDraftM = boatDraftFt * 0.3048;

  // ── Refresh just the water level (not the charted depths) ────────
  const refreshWaterLevel = useCallback(async () => {
    const station = currentStationRef.current;
    const charted = currentChartedRef.current;
    if (!station || charted.length === 0) return;

    const level = await getCurrentWaterLevel(station.id);
    if (!level) return;

    // Recompute adjusted depths with the new water level
    const adjustment = level.levelM;
    const depths: DynamicDepth[] = charted.map((pt) => {
      const adjusted = pt.depthM + adjustment;
      return {
        chartedDepthM: pt.depthM,
        adjustmentM: adjustment,
        adjustedDepthM: Math.round(adjusted * 100) / 100,
        lat: pt.lat,
        lon: pt.lon,
        safety: getDynamicDepthSafety(adjusted, boatDraftM),
        stationId: station.id,
        timestamp: level.timestamp,
      };
    });

    setGeoJSON(dynamicDepthsToGeoJSON(depths, units));

    const badge = units === 'imperial'
      ? formatTideBadgeFt(level.levelM)
      : formatTideBadge(level.levelM);
    setTideBadge(badge);
    onTideBadgeUpdate?.(badge, level.levelM);
  }, [boatDraftM, units, onTideBadgeUpdate]);

  // ── Full fetch: charted depths + water level ─────────────────────
  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    // Check movement threshold
    if (lastCenter.current) {
      const dist = haversineM(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    onLoadStart?.();

    try {
      // 1) Find nearest station
      const station = await getNearestWaterLevelStation(centerLat, centerLon);
      currentStationRef.current = station;
      onStationUpdate?.(station);

      if (!station) {
        setGeoJSON(null);
        setTideBadge('');
        onTideBadgeUpdate?.('', 0);
        return;
      }

      // 2) Sample charted depths from GEBCO
      const charted = await sampleChartedDepths(centerLat, centerLon);
      currentChartedRef.current = charted;

      if (charted.length === 0) {
        setGeoJSON(null);
        return;
      }

      // 3) Adjust all depths using the station's water level
      const bbox: BBox = {
        minLat: centerLat - GRID_RADIUS_DEG,
        maxLat: centerLat + GRID_RADIUS_DEG,
        minLon: centerLon - GRID_RADIUS_DEG,
        maxLon: centerLon + GRID_RADIUS_DEG,
      };

      const { depths, waterLevel } = await getAdjustedDepthGrid(bbox, charted, boatDraftM);

      if (depths.length > 0) {
        setGeoJSON(dynamicDepthsToGeoJSON(depths, units));
      } else {
        setGeoJSON(null);
      }

      if (waterLevel) {
        const badge = units === 'imperial'
          ? formatTideBadgeFt(waterLevel.levelM)
          : formatTideBadge(waterLevel.levelM);
        setTideBadge(badge);
        onTideBadgeUpdate?.(badge, waterLevel.levelM);
      }
    } catch {
      // Keep previous data on error
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [boatDraftM, units, onLoadStart, onLoadEnd, onTideBadgeUpdate, onStationUpdate]);

  // ── Trigger fetch when map moves ─────────────────────────────────
  useEffect(() => {
    if (zoom < MIN_ZOOM) return;
    fetchData(lat, lon);
  }, [lat, lon, zoom, fetchData]);

  // ── Auto-refresh every 6 minutes ─────────────────────────────────
  useEffect(() => {
    refreshTimerRef.current = setInterval(() => {
      refreshWaterLevel();
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
      }
    };
  }, [refreshWaterLevel]);

  // ── Render ───────────────────────────────────────────────────────
  if (!geoJSON || geoJSON.features.length === 0 || zoom < MIN_ZOOM) return null;

  return (
    <>
      {/* Depth number circles with safety colors */}
      <ShapeSource id="dynamic-depth-source" shape={geoJSON}>
        {/* Background circles for each depth point */}
        <CircleLayer
          id="dynamic-depth-circles"
          style={{
            circleRadius: [
              'interpolate', ['linear'], ['zoom'],
              12, 10,
              15, 14,
              17, 18,
            ],
            circleColor: ['get', 'safetyColor'],
            circleOpacity: 0.85,
            circleStrokeWidth: 1.5,
            circleStrokeColor: '#FFFFFF',
          }}
        />

        {/* Depth number labels on top of circles */}
        <SymbolLayer
          id="dynamic-depth-labels"
          style={{
            textField: ['get', 'depthDisplay'],
            textSize: [
              'interpolate', ['linear'], ['zoom'],
              12, 10,
              15, 12,
              17, 15,
            ],
            textColor: '#FFFFFF',
            textFont: ['Open Sans Bold'],
            textAllowOverlap: true,
            textIgnorePlacement: true,
            textPadding: 0,
          }}
        />
      </ShapeSource>
    </>
  );
}

// ── Tide Badge Component ─────────────────────────────────────────────────────

interface TideBadgeProps {
  /** Current tide badge text (e.g. "Tide: +1.2m") */
  badge: string;
  /** Current water level in meters */
  levelM: number;
  /** Station name */
  stationName?: string;
}

/**
 * Floating badge showing current tide adjustment.
 * Place this as an overlay on the map screen.
 */
export function TideBadge({ badge, levelM, stationName }: TideBadgeProps) {
  const pulseAnim = useRef(new Animated.Value(1)).current;

  // Subtle pulse when level updates
  useEffect(() => {
    Animated.sequence([
      Animated.timing(pulseAnim, {
        toValue: 1.05,
        duration: 200,
        useNativeDriver: true,
      }),
      Animated.timing(pulseAnim, {
        toValue: 1,
        duration: 200,
        useNativeDriver: true,
      }),
    ]).start();
  }, [levelM, pulseAnim]);

  if (!badge) return null;

  const isRising = levelM > 0;
  const iconName = isRising ? 'trending-up' : 'trending-down';
  const badgeColor = isRising ? '#1E88E5' : '#E65100';

  return (
    <Animated.View
      style={[
        styles.tideBadge,
        { backgroundColor: badgeColor, transform: [{ scale: pulseAnim }] },
      ]}
    >
      <Ionicons name={iconName} size={14} color="#FFFFFF" />
      <Text style={styles.tideBadgeText}>{badge}</Text>
      {stationName && (
        <Text style={styles.tideBadgeStation} numberOfLines={1}>
          {stationName}
        </Text>
      )}
    </Animated.View>
  );
}

// ── Safety Legend Component ───────────────────────────────────────────────────

interface DepthSafetyLegendProps {
  boatDraftFt: number;
}

/**
 * Small legend showing what each color means.
 */
export function DepthSafetyLegend({ boatDraftFt }: DepthSafetyLegendProps) {
  return (
    <View style={styles.legend}>
      <Text style={styles.legendTitle}>Draft: {boatDraftFt}ft</Text>
      {(['danger', 'caution', 'tight', 'safe'] as const).map((level) => (
        <View key={level} style={styles.legendRow}>
          <View style={[styles.legendDot, { backgroundColor: SAFETY_COLORS[level] }]} />
          <Text style={styles.legendLabel}>{SAFETY_LABELS[level]}</Text>
        </View>
      ))}
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // ── Tide Badge ──
  tideBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  tideBadgeText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  tideBadgeStation: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 10,
    maxWidth: 100,
  },

  // ── Legend ──
  legend: {
    backgroundColor: 'rgba(255,255,255,0.95)',
    borderRadius: 10,
    padding: 10,
    gap: 4,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 3,
  },
  legendTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 2,
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendLabel: {
    fontSize: 10,
    color: palette.textSecondary,
  },
});
