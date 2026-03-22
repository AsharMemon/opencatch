/**
 * OpenCatch — USACE Survey Overlay
 *
 * Shows USACE hydrographic channel survey data on the map:
 * - Channel depth surveys as colored lines (green/yellow/red)
 * - Lock & dam locations with status icons
 * - Harbor survey results as colored markers
 *
 * Color coding follows navigation safety convention:
 *   Green  = adequate depth (>=95% of authorized depth)
 *   Yellow = shoaling (85-95% of authorized depth)
 *   Red    = restricted (<85% of authorized depth)
 *   Grey   = unknown/no data
 *
 * Data source: USACE eHydro (ArcGIS Feature Service, free, no key).
 */

import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { palette } from '../theme/palette';
import {
  getChannelSurveys,
  getLockStatus,
  getHarborSurveys,
  channelSurveysToGeoJSON,
  locksToGeoJSON,
  harborSurveysToGeoJSON,
  CHANNEL_STATUS_COLORS,
  type SurveyBBox,
  type ChannelSurvey,
  type LockStatus as LockStatusType,
  type HarborSurvey,
} from '../services/usaceDepthSurveys';

// ── Types ────────────────────────────────────────────────────────

interface Props {
  /** Current map center latitude. */
  lat: number;
  /** Current map center longitude. */
  lon: number;
  /** Current zoom level — overlay fetches data when zoom >= 8. */
  zoom: number;
  /** MapLibre ShapeSource component. */
  ShapeSource: any;
  /** MapLibre LineLayer component. */
  LineLayer: any;
  /** MapLibre CircleLayer component. */
  CircleLayer: any;
  /** MapLibre SymbolLayer component. */
  SymbolLayer: any;
  /** MapLibre FillLayer component (optional, for channel polygons). */
  FillLayer?: any;
  /** Whether to show channel surveys. Default true. */
  showChannels?: boolean;
  /** Whether to show lock & dam markers. Default true. */
  showLocks?: boolean;
  /** Whether to show harbor surveys. Default true. */
  showHarbors?: boolean;
}

// ── Constants ────────────────────────────────────────────────────

const MIN_ZOOM = 8;
const REFRESH_DISTANCE_M = 10_000; // Re-fetch when user pans >10km

const EMPTY_FC: GeoJSON.FeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

// ── Haversine helper ─────────────────────────────────────────────

function haversineDistance(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
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

/** Build a bbox ~50km around a center point. */
function buildBBox(lat: number, lon: number): SurveyBBox {
  const delta = 0.5; // ~50km at mid-latitudes
  return {
    west: lon - delta,
    south: lat - delta,
    east: lon + delta,
    north: lat + delta,
  };
}

// ── Component ────────────────────────────────────────────────────

export function USACESurveyOverlay({
  lat,
  lon,
  zoom,
  ShapeSource,
  LineLayer,
  CircleLayer,
  SymbolLayer,
  FillLayer,
  showChannels = true,
  showLocks = true,
  showHarbors = true,
}: Props) {
  const [channelGeoJSON, setChannelGeoJSON] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
  const [lockGeoJSON, setLockGeoJSON] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
  const [harborGeoJSON, setHarborGeoJSON] = useState<GeoJSON.FeatureCollection>(EMPTY_FC);
  const [surveyCount, setSurveyCount] = useState(0);

  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);

  useEffect(() => {
    // Only fetch at sufficient zoom level
    if (zoom < MIN_ZOOM) return;

    // Skip if user hasn't panned far enough
    if (
      lastCenter.current &&
      haversineDistance(lastCenter.current.lat, lastCenter.current.lon, lat, lon) <
        REFRESH_DISTANCE_M
    ) {
      return;
    }
    lastCenter.current = { lat, lon };

    const controller = new AbortController();
    const bbox = buildBBox(lat, lon);

    (async () => {
      try {
        const promises: Promise<any>[] = [];

        if (showChannels) {
          promises.push(
            getChannelSurveys(bbox).then((surveys) => {
              const geo = channelSurveysToGeoJSON(surveys);
              setChannelGeoJSON(geo);
              setSurveyCount((prev) => prev + surveys.length);
            }),
          );
        }

        if (showLocks) {
          promises.push(
            getLockStatus(bbox).then((locks) => {
              setLockGeoJSON(locksToGeoJSON(locks));
            }),
          );
        }

        if (showHarbors) {
          promises.push(
            getHarborSurveys(bbox).then((harbors) => {
              setHarborGeoJSON(harborSurveysToGeoJSON(harbors));
            }),
          );
        }

        await Promise.allSettled(promises);
      } catch {
        // Network error — keep showing any cached data
      }
    })();

    return () => controller.abort();
  }, [lat, lon, zoom, showChannels, showLocks, showHarbors]);

  // Don't render anything at low zoom
  if (zoom < MIN_ZOOM) return null;

  const hasChannels = channelGeoJSON.features.length > 0;
  const hasLocks = lockGeoJSON.features.length > 0;
  const hasHarbors = harborGeoJSON.features.length > 0;

  if (!hasChannels && !hasLocks && !hasHarbors) return null;

  return (
    <>
      {/* ── Channel surveys — colored lines/polygons ─────────── */}
      {hasChannels && showChannels && (
        <ShapeSource id="usace-channel-source" shape={channelGeoJSON}>
          {/* Line rendering for channel surveys */}
          <LineLayer
            id="usace-channel-lines"
            style={{
              lineColor: ['get', 'color'],
              lineWidth: [
                'interpolate',
                ['linear'],
                ['zoom'],
                8, 2,
                12, 4,
                16, 6,
              ],
              lineOpacity: 0.8,
            }}
          />

          {/* Channel name labels at higher zoom */}
          <SymbolLayer
            id="usace-channel-labels"
            minZoomLevel={11}
            style={{
              textField: [
                'concat',
                ['get', 'channelName'],
                '\n',
                ['case',
                  ['has', 'surveyedDepthFt'],
                  ['concat', ['to-string', ['get', 'surveyedDepthFt']], '\' / ',
                    ['to-string', ['get', 'authorizedDepthFt']], '\''],
                  '',
                ],
              ],
              textSize: 10,
              textColor: '#333333',
              textHaloColor: 'rgba(255, 255, 255, 0.95)',
              textHaloWidth: 1.5,
              textFont: ['Open Sans Regular'],
              symbolPlacement: 'line',
              textAllowOverlap: false,
              textOptional: true,
            }}
          />
        </ShapeSource>
      )}

      {/* ── Lock & dam markers ────────────────────────────────── */}
      {hasLocks && showLocks && (
        <ShapeSource id="usace-lock-source" shape={lockGeoJSON}>
          {/* Lock markers — distinctive diamond shape via circleLayer */}
          <CircleLayer
            id="usace-lock-circles"
            style={{
              circleRadius: [
                'interpolate',
                ['linear'],
                ['zoom'],
                8, 4,
                12, 8,
                16, 12,
              ],
              circleColor: [
                'match',
                ['get', 'status'],
                'open', '#4CAF50',
                'closed', '#F44336',
                'restricted', '#FFC107',
                '#78909C', // unknown
              ],
              circleStrokeColor: '#FFFFFF',
              circleStrokeWidth: 2,
              circleOpacity: 0.9,
            }}
          />

          {/* Lock name labels */}
          <SymbolLayer
            id="usace-lock-labels"
            minZoomLevel={10}
            style={{
              textField: ['get', 'name'],
              textSize: 10,
              textColor: '#333333',
              textHaloColor: 'rgba(255, 255, 255, 0.95)',
              textHaloWidth: 1.5,
              textFont: ['Open Sans Regular'],
              textOffset: [0, 1.4],
              textAnchor: 'top',
              textOptional: true,
              textMaxWidth: 10,
            }}
          />
        </ShapeSource>
      )}

      {/* ── Harbor survey markers ─────────────────────────────── */}
      {hasHarbors && showHarbors && (
        <ShapeSource id="usace-harbor-source" shape={harborGeoJSON}>
          <CircleLayer
            id="usace-harbor-circles"
            style={{
              circleRadius: [
                'interpolate',
                ['linear'],
                ['zoom'],
                8, 3,
                12, 6,
                16, 10,
              ],
              circleColor: ['get', 'color'],
              circleStrokeColor: '#1A237E',
              circleStrokeWidth: 1.5,
              circleOpacity: 0.85,
            }}
          />

          {/* Harbor labels */}
          <SymbolLayer
            id="usace-harbor-labels"
            minZoomLevel={11}
            style={{
              textField: [
                'concat',
                ['get', 'name'],
                '\n',
                ['case',
                  ['has', 'surveyedDepthFt'],
                  ['concat', ['to-string', ['get', 'surveyedDepthFt']], '\''],
                  '',
                ],
              ],
              textSize: 9,
              textColor: '#1A237E',
              textHaloColor: 'rgba(255, 255, 255, 0.9)',
              textHaloWidth: 1,
              textFont: ['Open Sans Regular'],
              textOffset: [0, 1.2],
              textAnchor: 'top',
              textOptional: true,
            }}
          />
        </ShapeSource>
      )}
    </>
  );
}

// ── Legend Component ──────────────────────────────────────────────

/** Compact legend for the USACE survey overlay, shown in the map. */
export function USACESurveyLegend() {
  return (
    <View style={legendStyles.container}>
      <Text style={legendStyles.title}>USACE Survey</Text>
      <View style={legendStyles.row}>
        <View style={[legendStyles.dot, { backgroundColor: CHANNEL_STATUS_COLORS.adequate }]} />
        <Text style={legendStyles.label}>Adequate depth</Text>
      </View>
      <View style={legendStyles.row}>
        <View style={[legendStyles.dot, { backgroundColor: CHANNEL_STATUS_COLORS.shoaling }]} />
        <Text style={legendStyles.label}>Shoaling</Text>
      </View>
      <View style={legendStyles.row}>
        <View style={[legendStyles.dot, { backgroundColor: CHANNEL_STATUS_COLORS.restricted }]} />
        <Text style={legendStyles.label}>Restricted</Text>
      </View>
      <View style={legendStyles.row}>
        <View style={[legendStyles.dot, { backgroundColor: CHANNEL_STATUS_COLORS.unknown }]} />
        <Text style={legendStyles.label}>No data</Text>
      </View>
    </View>
  );
}

const legendStyles = StyleSheet.create({
  container: {
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
    borderRadius: 8,
    padding: 8,
    gap: 4,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderLight,
  },
  title: {
    fontSize: 10,
    fontWeight: '700',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 2,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.1)',
  },
  label: {
    fontSize: 10,
    color: palette.textSecondary,
  },
});
