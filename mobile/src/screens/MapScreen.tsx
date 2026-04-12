import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Svg, { Circle, Line, Text as SvgText, G, Polygon } from 'react-native-svg';
import {
  Alert,
  ActivityIndicator,
  Animated,
  AppState,
  Dimensions,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  Linking,
  Modal,
  PanResponder,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableWithoutFeedback,
  type StyleProp,
  type ViewStyle,
  View,
} from 'react-native';
import type { AppStateStatus } from 'react-native';
// MapLibre native modules only work on iOS/Android
let MLMapView: any = null;
let Camera: any = null;
let PointAnnotation: any = null;
let Callout: any = null;
let UserLocation: any = null;
let RasterSource: any = null;
let RasterLayer: any = null;
let VectorSource: any = null;
let ShapeSource: any = null;
let LineLayer: any = null;
let SymbolLayer: any = null;
let CircleLayer: any = null;
let FillLayer: any = null;
let setAccessToken: any = (_: any) => {};
type CameraRef = any;
type MapViewRef = any;
type RoutePlacementMode = 'start' | 'stop' | 'end';

try {
  const maplibre = require('@maplibre/maplibre-react-native');
  MLMapView = maplibre.MapView;
  Camera = maplibre.Camera;
  PointAnnotation = maplibre.PointAnnotation;
  Callout = maplibre.Callout;
  UserLocation = maplibre.UserLocation;
  RasterSource = maplibre.RasterSource;
  RasterLayer = maplibre.RasterLayer;
  VectorSource = maplibre.VectorSource;
  ShapeSource = maplibre.ShapeSource;
  LineLayer = maplibre.LineLayer;
  SymbolLayer = maplibre.SymbolLayer;
  CircleLayer = maplibre.CircleLayer;
  FillLayer = maplibre.FillLayer;
  setAccessToken = maplibre.setAccessToken;
  setAccessToken(null);
} catch (_) {
  // MapLibre not available on web — will show fallback UI
}

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { palette, getConditionBand, conditionConfig, scoreColor } from '../theme/palette';
import { FishingTimeBanner } from '../components/FishingTimeBanner';
import {
  getCatchesWithPhotos,
  catchPhotosToGeoJSON,
  getCatchPhotoThumbnail,
  formatCatchConditions,
  filterCatchesInBounds,
  type CatchWithPhoto,
} from '../services/catchPhotoOverlay';
import { scheduleBestTimeNotification } from '../services/fishingNotifications';
import { EnhancedSearchBar } from '../components/EnhancedSearchBar';
import { SpotInsightsCard } from '../components/SpotInsightsCard';
import { api, buildTileSourceUrl, buildTileTemplateUrl } from '../services/api';
import { TILE_SERVER_DEPLOYED } from '../config/network';
import { fetchNearbyMarinas, formatAmenities } from '../services/marinaDirectory';
import type { MarinaPOI, MarinaPOIType } from '../services/marinaDirectory';
import {
  fetchRadarData,
  getRadarFrames,
  buildRadarTileUrl,
  RADAR_LEGEND,
  type RadarTimestamp,
} from '../services/precipRadar';
import {
  getActiveStormCells,
  stormCellsToGeoJSON,
  type StormCell,
  type StormGeoJSON,
} from '../services/stormTracking';
import {
  fetchNearbyAccessPoints,
  accessPointsToGeoJSON,
  ACCESS_POINT_CONFIG,
} from '../services/accessPointService';
import type { AccessPoint, AccessPointType } from '../services/accessPointService';
import { fetchNearbyTrails, trailsToGeoJSON } from '../services/trailService';
import {
  getFishingAlerts,
  type FishingWeatherAlert,
  type AlertSeverity,
} from '../services/weatherAlerts';
import type { FishingLocation, Waypoint, WaypointIcon, BestFishingV2Entry } from '../types/models';
import type { TabProps } from '../types/navigation';
import { NoWakeZoneOverlay } from '../components/NoWakeZoneOverlay';
import { NavigationAidsOverlay } from '../components/NavigationAidsOverlay';
import { ArtificialReefOverlay } from '../components/ArtificialReefOverlay';
import { WindOverlayAnimated } from '../components/WindOverlayAnimated';
import { WaveOverlayAnimated } from '../components/WaveOverlayAnimated';
import { DepthNumberOverlay } from '../components/DepthNumberOverlay';
import { MarineGasOverlay } from '../components/MarineGasOverlay';
import { OceanFishingSpotsOverlay } from '../components/OceanFishingSpotsOverlay';
import { TidalCurrentOverlay } from '../components/TidalCurrentOverlay';
import { SeabedOverlay } from '../components/SeabedOverlay';
import { SEABED_LEGEND_STOPS } from '../services/seabedCharacteristics';
import { MaritimeBoundariesOverlay } from '../components/MaritimeBoundariesOverlay';
import { USACESurveyOverlay } from '../components/USACESurveyOverlay';
import { DraftAccessibilityOverlay } from '../components/DraftAccessibilityOverlay';
import { DynamicDepthOverlay } from '../components/DynamicDepthOverlay';
import { IceThicknessOverlay } from '../components/IceThicknessOverlay';
import { FishingPressureOverlay } from '../components/FishingPressureOverlay';
import { BiteTimeOverlay } from '../components/BiteTimeOverlay';
import { SSTOverlay } from '../components/SSTOverlay';
import { WAVE_LEGEND_STOPS } from '../components/WaveOverlayAnimated';
import { SST_LEGEND_STOPS } from '../components/SSTOverlay';
import { PRESSURE_LEGEND_STOPS } from '../components/FishingPressureOverlay';
import { ICE_LEGEND_STOPS } from '../components/IceThicknessOverlay';
import { BITE_LEGEND_STOPS } from '../components/BiteTimeOverlay';
import { TIDAL_LEGEND_STOPS } from '../components/TidalCurrentOverlay';
import { getDefaultBoat } from '../services/boatProfile';
import { aisReceiver, type MarineInstrumentData } from '../services/aisWifiReceiver';
import {
  trackRecorder,
  buildSpeedColoredGeoJSON,
  type FishingTrack,
} from '../services/trackRecorder';
import {
  discoverFishingSpots,
  cancelPendingDiscovery,
  discoveredToLocations,
  getCachedSpotsForBBox,
  prefetchAdjacentCells,
  type DiscoveredSpot,
  type BoundingBox,
} from '../services/fishingSpotDiscovery';
import { getTopFishingSpots, isStaticSpot } from '../data/topFishingSpots';
import {
  getCatalogLakeBoundsForLocationId,
  getGpsLakeCatalogEntryById,
  getNearbyCatalogLocations,
  searchCatalogLocations,
} from '../services/gpsLakeCatalog';
import { cacheLocationDetail, prefetchLocationDetails } from '../services/locationDetailCache';
import { clearLocationDataCache } from '../services/locationDataCache';
import { useUnits } from '../hooks/useUnits';
import {
  buildLakeFishabilityGeoJSON,
  type FishabilityProbe,
} from '../services/lakeFishability';
import {
  getTipsForWaterbody,
  inferWaterbodyType,
  type ContextualTip,
  type WaterbodyContext,
  type WeatherConditions,
} from '../services/contextualTips';
import {
  loadAnnotations,
  saveAnnotation,
  deleteAnnotation as removeAnnotation,
  distanceMeters,
  markerAnnotationsGeoJSON,
  arrowAnnotationsGeoJSON,
  circleAnnotationsGeoJSON,
  ANNOTATION_COLORS,
  type MapAnnotation,
  type AnnotationType,
  type AnnotationCoordinate,
} from '../services/chartAnnotations';
import {
  loadContourSettings,
  saveContourSettings,
  resetContourSettings,
  getColorStops,
  CONTOUR_INTERVALS,
  COLOR_SCHEMES,
  DEFAULT_CONTOUR_SETTINGS,
  type DepthContourSettings,
} from '../services/depthContourSettings';
import {
  getMartinContourSourcesForBounds,
  getBathyLayerIds,
  getDepthFromRenderedFeatures,
  getLakeAttributionFromRenderedFeatures,
  formatDepth as formatContourDepth,
  getQualityIcon as getContourQualityIcon,
  getColorForDepth,
  type SourceBounds as ContourSourceBounds,
  type DepthResult as ContourDepthResult,
  type LakeAttribution as ContourLakeAttribution,
} from '../services/contourMapService';
import {
  detectCountry as detectSurveyCountry,
  getBathymetryTileSource as getInternationalBathymetryTileSource,
  getCanadianHydroNetworkTileSource,
} from '../services/internationalSurveys';
import {
  getAnchorStatus,
  addAnchorListener,
  anchorCircleGeoJSON,
  type AnchorStatus,
} from '../services/anchorAlarm';
import {
  isMOBActive,
  addMOBListener,
  type MOBStatus,
} from '../services/manOverboard';
import { MapToolsDrawer, type MapToolGroup, type OverlayInfoCard } from '../components/MapToolsDrawer';
import { MapOverlayLoadingBanner } from '../components/MapOverlayLoadingBanner';
import { CoachMarks } from '../components/CoachMarks';
import { MapLongPressMenu, type LongPressAction, type LongPressCoordinate } from '../components/MapLongPressMenu';
import { MapWeatherPanel, type MapTideSummary } from '../components/MapWeatherPanel';
import { MapInfoBar, type AnchorWatchInfo, type RouteNavInfo } from '../components/MapInfoBar';
import {
  calculateRouteMetrics,
  autorouteWaypoints,
  checkRouteDepth,
  createRoute,
  getDefaultBoatRouteProfile,
  getNextWaypointNav,
  saveRoute,
  type BoatRouteProfile,
  type LatLng as RouteLatLng,
  type Route as PlannedRoute,
  type RouteMetrics,
  type ShallowWarning,
} from '../services/routePlanner';
import { getMapWeatherForecast, type MapWeatherForecast } from '../services/mapWeatherForecast';
import {
  getNextTide,
  getUnifiedNearestTideStation,
  getUnifiedTideHourly,
  getUnifiedTidePredictions,
  getUnifiedWaterLevel,
} from '../services/tidesService';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

// ── Constants ─────────────────────────────────────────────────────

const DEFAULT_CENTER: [number, number] = [-95.0, 45.0]; // [lng, lat] — centered to show US + Canada
const DEFAULT_ZOOM = 3.5;
const LOCAL_BATHY_MIN_ZOOM = 8.5;
const BROWSE_LAGOS_CONTOUR_MIN_ZOOM = LOCAL_BATHY_MIN_ZOOM;
const CRITICAL_LAGOS_CONTOUR_MIN_ZOOM = 9.5;
const DEFAULT_NEARBY_SKIP_PATTERNS = [
  /\bunnamed\b/i,
  /\bstorm\b/i,
  /\bretention\b/i,
  /\bdetention\b/i,
  /\bwastewater\b/i,
  /\bsewage\b/i,
  /\btreatment\b/i,
];

function cleanLocationText(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim();
}

function getPrimaryLocationLabel(location: FishingLocation): string {
  return cleanLocationText(location.name) || cleanLocationText(location.subtitle) || 'Unnamed spot';
}

function getSecondaryLocationLabel(location: FishingLocation): string {
  const name = cleanLocationText(location.name);
  const subtitle = cleanLocationText(location.subtitle);
  if (subtitle && subtitle !== name) return subtitle;
  if (name) return 'Water body';
  return 'Water body';
}

function isRenderableLocation(location: FishingLocation): boolean {
  if (!Number.isFinite(location.lat) || !Number.isFinite(location.lon)) {
    return false;
  }
  return Boolean(cleanLocationText(location.name) || cleanLocationText(location.subtitle));
}

function buildContourFeatureFilter(featureKinds: string | string[]): any[] {
  const kindFilter = Array.isArray(featureKinds)
    ? ['in', ['get', 'feature_kind'], ['literal', featureKinds]]
    : ['==', ['get', 'feature_kind'], featureKinds];
  return ['all', kindFilter];
}

function shouldIncludeInDefaultNearbyList(location: FishingLocation): boolean {
  const name = `${cleanLocationText(location.name)} ${cleanLocationText(location.subtitle)}`.trim();
  if (!name || DEFAULT_NEARBY_SKIP_PATTERNS.some((pattern) => pattern.test(name))) {
    return false;
  }
  const lower = name.toLowerCase();
  if (lower.includes('pond') && !lower.includes('millpond')) {
    return false;
  }
  return true;
}

function isPrimaryMapLocation(location: FishingLocation): boolean {
  return !location.id.startsWith('osm-') && !isStaticSpot(location.id);
}

// AsyncStorage keys for persisting last map view
const MAP_STATE_KEY = 'opencatch_map_state';
const LAST_LOCATION_KEY = 'opencatch_last_location';

// Module-level cache for discovered spots — survives tab switches (component unmount/remount)
let _cachedDiscoveredSpots: DiscoveredSpot[] = [];
let _cachedDiscoveryBbox: BoundingBox | null = null;
let _cachedDiscoveryZoom: number = 0;

// Bottom sheet snap points
const SHEET_HIDDEN = 96; // Keep the compact status row visible at all times
const SHEET_PEEK = 132;
const SHEET_COLLAPSED = 276;
const SHEET_EXPANDED = SCREEN_HEIGHT * 0.75;
const SNAP_POINTS = [SHEET_HIDDEN, SHEET_PEEK, SHEET_COLLAPSED, SHEET_EXPANDED];
const HANDLE_HEIGHT = 32;

function closestSnap(value: number, velocity: number = 0): number {
  const projected = value - velocity * 0.15;
  return SNAP_POINTS.reduce((prev, curr) =>
    Math.abs(curr - projected) < Math.abs(prev - projected) ? curr : prev,
  );
}

function makeRouteLineGeoJSON(points: RouteLatLng[]): GeoJSON.FeatureCollection | null {
  if (points.length < 2) return null;
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'LineString',
          coordinates: points.map((point) => [point.lon, point.lat]),
        },
      },
    ],
  };
}

function makeRoutePointGeoJSON(points: RouteLatLng[]): GeoJSON.FeatureCollection | null {
  if (!points.length) return null;
  return {
    type: 'FeatureCollection',
    features: points.map((point, index) => ({
      type: 'Feature',
      properties: {
        index: index + 1,
        label: index === 0 ? 'START' : index === points.length - 1 ? 'END' : `WP ${index}`,
        markerKind:
          index === 0
            ? 'start'
            : index === points.length - 1
              ? 'end'
              : 'waypoint',
        shortLabel:
          index === 0
            ? 'A'
            : index === points.length - 1
              ? 'B'
              : `${index}`,
      },
      geometry: {
        type: 'Point',
        coordinates: [point.lon, point.lat],
      },
    })),
  };
}

function makeRouteTargetGeoJSON(
  point: RouteLatLng | null,
  markerKind: 'destination' | 'start',
  label: string,
): GeoJSON.FeatureCollection | null {
  if (!point) return null;
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {
          markerKind,
          label,
          shortLabel: markerKind === 'destination' ? 'B' : 'A',
        },
        geometry: {
          type: 'Point',
          coordinates: [point.lon, point.lat],
        },
      },
    ],
  };
}

function makeRouteWarningGeoJSON(
  warnings: ShallowWarning[],
  isMetric: boolean,
): GeoJSON.FeatureCollection | null {
  if (!warnings.length) return null;

  return {
    type: 'FeatureCollection',
    features: warnings.map((warning, index) => {
      const depthLabel = isMetric
        ? `${warning.depthM.toFixed(1)} m`
        : `${(warning.depthM * 3.28084).toFixed(0)} ft`;
      const draftLabel = isMetric
        ? `${warning.draftM.toFixed(1)} m`
        : `${(warning.draftM * 3.28084).toFixed(0)} ft`;
      const severity = warning.depthM < warning.draftM ? 'danger' : 'caution';

      return {
        type: 'Feature' as const,
        id: `route-warning-${index}`,
        properties: {
          severity,
          shortLabel: severity === 'danger' ? 'NO' : '!',
          label: severity === 'danger' ? `No-go ${depthLabel}` : `Shallow ${depthLabel}`,
          detail: `Need ${draftLabel}`,
        },
        geometry: {
          type: 'Point' as const,
          coordinates: [warning.position.lon, warning.position.lat],
        },
      };
    }),
  };
}

function normalizeBearingDelta(delta: number): number {
  let normalized = ((delta + 180) % 360 + 360) % 360 - 180;
  if (normalized === -180) normalized = 180;
  return normalized;
}

function offsetCoordinateMeters(
  point: RouteLatLng,
  bearingDeg: number,
  distanceMeters: number,
): RouteLatLng {
  const earthRadiusMeters = 6378137;
  const angularDistance = distanceMeters / earthRadiusMeters;
  const bearingRad = (bearingDeg * Math.PI) / 180;
  const lat1 = (point.lat * Math.PI) / 180;
  const lon1 = (point.lon * Math.PI) / 180;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearingRad),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearingRad) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2),
    );

  return {
    lat: (lat2 * 180) / Math.PI,
    lon: (((lon2 * 180) / Math.PI + 540) % 360) - 180,
  };
}

function buildRouteCorridorPolygon(
  from: RouteLatLng,
  to: RouteLatLng,
  halfWidthMeters: number,
): RouteLatLng[] {
  const bearingDeg =
    (Math.atan2(
      Math.sin(((to.lon - from.lon) * Math.PI) / 180) * Math.cos((to.lat * Math.PI) / 180),
      Math.cos((from.lat * Math.PI) / 180) * Math.sin((to.lat * Math.PI) / 180) -
        Math.sin((from.lat * Math.PI) / 180) *
          Math.cos((to.lat * Math.PI) / 180) *
          Math.cos(((to.lon - from.lon) * Math.PI) / 180),
    ) *
      180) /
      Math.PI;
  const normalizedBearing = (bearingDeg + 360) % 360;
  const leftBearing = normalizedBearing - 90;
  const rightBearing = normalizedBearing + 90;
  const leftStart = offsetCoordinateMeters(from, leftBearing, halfWidthMeters);
  const leftEnd = offsetCoordinateMeters(to, leftBearing, halfWidthMeters);
  const rightEnd = offsetCoordinateMeters(to, rightBearing, halfWidthMeters);
  const rightStart = offsetCoordinateMeters(from, rightBearing, halfWidthMeters);
  return [leftStart, leftEnd, rightEnd, rightStart, leftStart];
}

function getRouteSegmentSeverity(
  minDepthM: number | null,
  draftMeters: number | null | undefined,
): 'safe' | 'caution' | 'danger' {
  if (draftMeters == null || minDepthM == null || !Number.isFinite(minDepthM)) {
    return 'safe';
  }
  if (minDepthM < draftMeters) return 'danger';
  if (minDepthM < draftMeters + 0.8) return 'caution';
  return 'safe';
}

function makeRouteCorridorGeoJSON(
  route: PlannedRoute | null,
  draftMeters: number | null | undefined,
  navActive: boolean,
  navIndex: number,
  userLocation: RouteLatLng | null,
): GeoJSON.FeatureCollection | null {
  if (!route || route.segments.length === 0) return null;
  const startSegmentIndex = navActive ? Math.max(navIndex - 1, 0) : 0;
  const features: GeoJSON.Feature[] = [];

  for (let segmentIndex = startSegmentIndex; segmentIndex < route.segments.length; segmentIndex++) {
    const segment = route.segments[segmentIndex];
    const from =
      navActive && segmentIndex === startSegmentIndex && userLocation
        ? userLocation
        : segment.from;
    const to = segment.to;
    const severity = getRouteSegmentSeverity(segment.minDepthM, draftMeters);
    const polygon = buildRouteCorridorPolygon(from, to, navActive ? 42 : 30);
    const fillColor =
      severity === 'danger'
        ? '#C44B4B'
        : severity === 'caution'
          ? '#C96A18'
          : '#2E7D5B';
    features.push({
      type: 'Feature',
      properties: {
        segmentIndex,
        severity,
        fillColor,
        lineColor: fillColor,
      },
      geometry: {
        type: 'Polygon',
        coordinates: [polygon.map((point) => [point.lon, point.lat])],
      },
    });
  }

  return features.length > 0
    ? {
        type: 'FeatureCollection',
        features,
      }
    : null;
}

function toContourBounds(bounds: any): ContourSourceBounds | null {
  if (!Array.isArray(bounds) || !Array.isArray(bounds[0]) || !Array.isArray(bounds[1])) {
    return null;
  }

  const lonA = Number(bounds[0][0]);
  const latA = Number(bounds[0][1]);
  const lonB = Number(bounds[1][0]);
  const latB = Number(bounds[1][1]);

  if (![lonA, latA, lonB, latB].every(Number.isFinite)) {
    return null;
  }

  return {
    minLon: Math.min(lonA, lonB),
    minLat: Math.min(latA, latB),
    maxLon: Math.max(lonA, lonB),
    maxLat: Math.max(latA, latB),
  };
}

function estimateBoundsFromCenter(center: RouteLatLng, zoom: number): ContourSourceBounds {
  const latSpan = 180 / Math.pow(2, Math.max(zoom - 1, 1));
  const lonSpan = latSpan * Math.max(Math.cos((center.lat * Math.PI) / 180), 0.35);
  return {
    minLon: center.lon - lonSpan,
    minLat: center.lat - latSpan / 2,
    maxLon: center.lon + lonSpan,
    maxLat: center.lat + latSpan / 2,
  };
}

function boundsChangedEnough(
  previous: ContourSourceBounds | null,
  next: ContourSourceBounds | null,
): boolean {
  if (!next) return false;
  if (!previous) return true;

  const prevLatSpan = Math.max(previous.maxLat - previous.minLat, 0.0001);
  const prevLonSpan = Math.max(previous.maxLon - previous.minLon, 0.0001);
  const nextLatSpan = Math.max(next.maxLat - next.minLat, 0.0001);
  const nextLonSpan = Math.max(next.maxLon - next.minLon, 0.0001);

  const prevCenterLat = (previous.minLat + previous.maxLat) / 2;
  const prevCenterLon = (previous.minLon + previous.maxLon) / 2;
  const nextCenterLat = (next.minLat + next.maxLat) / 2;
  const nextCenterLon = (next.minLon + next.maxLon) / 2;

  const latShift = Math.abs(nextCenterLat - prevCenterLat);
  const lonShift = Math.abs(nextCenterLon - prevCenterLon);
  const latSpanChange = Math.abs(nextLatSpan - prevLatSpan) / prevLatSpan;
  const lonSpanChange = Math.abs(nextLonSpan - prevLonSpan) / prevLonSpan;

  return (
    latShift > prevLatSpan * 0.18 ||
    lonShift > prevLonSpan * 0.18 ||
    latSpanChange > 0.2 ||
    lonSpanChange > 0.2
  );
}

function expandBounds(bounds: ContourSourceBounds, latPadFactor = 0.15, lonPadFactor = 0.15): ContourSourceBounds {
  const latSpan = Math.max(bounds.maxLat - bounds.minLat, 0.01);
  const lonSpan = Math.max(bounds.maxLon - bounds.minLon, 0.01);
  return {
    minLat: bounds.minLat - latSpan * latPadFactor,
    maxLat: bounds.maxLat + latSpan * latPadFactor,
    minLon: bounds.minLon - lonSpan * lonPadFactor,
    maxLon: bounds.maxLon + lonSpan * lonPadFactor,
  };
}

function pointWithinBounds(lat: number, lon: number, bounds: ContourSourceBounds): boolean {
  return lat >= bounds.minLat && lat <= bounds.maxLat && lon >= bounds.minLon && lon <= bounds.maxLon;
}

function hexToRgb(color: string): [number, number, number] {
  const normalized = color.replace('#', '');
  return [
    parseInt(normalized.slice(0, 2), 16),
    parseInt(normalized.slice(2, 4), 16),
    parseInt(normalized.slice(4, 6), 16),
  ];
}

function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (value: number) => Math.round(Math.max(0, Math.min(255, value))).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function mixHex(from: string, to: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(from);
  const [r2, g2, b2] = hexToRgb(to);
  return rgbToHex(
    r1 + (r2 - r1) * t,
    g1 + (g2 - g1) * t,
    b1 + (b2 - b1) * t,
  );
}

function darkenHex(color: string, amount = 0.18): string {
  return mixHex(color, '#27445D', amount);
}

function lightenHex(color: string, amount = 0.18): string {
  return mixHex(color, '#FCF8EF', amount);
}

function buildDepthBreaksFt(interval: number): number[] {
  const base = Math.max(interval, 1);
  return [
    0,
    base * 0.125,
    base * 0.25,
    base * 0.375,
    base * 0.5,
    base * 0.75,
    base,
    base * 1.25,
    base * 1.5,
    base * 1.75,
    base * 2,
    base * 2.5,
    base * 3,
    base * 4,
    base * 5,
    base * 6,
    base * 8,
    base * 10,
    base * 12,
    base * 15,
    base * 18,
  ].map((value) => Math.round(value * 10) / 10);
}

function sampleColorRamp(colors: string[], count: number): string[] {
  if (colors.length === 0) return [];
  if (colors.length === 1 || count <= 1) return [colors[0]];
  const out: string[] = [];
  const maxIndex = colors.length - 1;
  for (let i = 0; i < count; i++) {
    const t = (i / (count - 1)) * maxIndex;
    const idx = Math.floor(t);
    const frac = t - idx;
    const start = colors[idx];
    const end = colors[Math.min(idx + 1, maxIndex)];
    out.push(frac <= 0 ? start : mixHex(start, end, frac));
  }
  return out;
}

function buildSteppedDepthExpression(
  colors: string[],
  breaks: number[],
  fallback: string,
  valueExpression: any = ['coalesce', ['get', 'depth_ft'], 0],
): any[] {
  const usableCount = Math.min(colors.length, breaks.length);
  if (usableCount === 0) return fallback as any;
  const expression: any[] = ['step', valueExpression, colors[0] ?? fallback];
  for (let i = 1; i < usableCount; i++) {
    expression.push(breaks[i], colors[i] ?? colors[i - 1] ?? fallback);
  }
  return expression as any;
}

function formatArrivalClock(isoTime?: string | null): string {
  if (!isoTime) return '--';
  const parsed = new Date(isoTime);
  if (Number.isNaN(parsed.getTime())) return '--';
  return parsed.toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });
}

// ── Weather alert banner colors by severity ─────────────────────
const SEVERITY_BANNER_COLORS: Record<AlertSeverity, string> = {
  Extreme: '#D32F2F',
  Severe: '#D32F2F',
  Moderate: '#F57C00',
  Minor: '#F9A825',
  Unknown: '#1976D2',
};

/** Auto-refresh interval for weather alerts (30 minutes — reduced from 10 to save battery). */
const ALERT_REFRESH_MS = 30 * 60 * 1000;

// ── Map style identifiers ────────────────────────────────────────

type MapStyleKey = 'opencatch' | 'hybrid' | 'bathymetry' | 'satellite' | 'outdoors' | 'topo' | 'night' | 'nautical-chart';
const MAP_STYLE_LABELS: Record<MapStyleKey, string> = {
  opencatch: 'OpenCatch',
  hybrid: 'Hybrid',
  bathymetry: 'Bathymetry',
  satellite: 'Satellite',
  outdoors: 'Outdoors',
  topo: 'Topographic',
  night: 'Night',
  'nautical-chart': 'Nautical Chart',
};
const MAP_STYLE_IONICONS: Record<MapStyleKey, string> = {
  opencatch: 'fish-outline',
  hybrid: 'earth-outline',
  bathymetry: 'water-outline',
  satellite: 'planet-outline',
  outdoors: 'compass-outline',
  topo: 'analytics-outline',
  night: 'moon-outline',
  'nautical-chart': 'boat-outline',
};
const MAP_STYLE_KEYS: MapStyleKey[] = ['opencatch', 'satellite', 'outdoors', 'night', 'nautical-chart'];

// NOAA nautical chart raster tile URL
const NOAA_CHART_TILE_URL = 'https://tileservice.charts.noaa.gov/tiles/50000_1/{z}/{x}/{y}.png';

// Coastal bounding boxes — show nautical chart option when user is near coast
const COASTAL_BOUNDING_BOXES = [
  // US East Coast
  { minLat: 24.5, maxLat: 47.5, minLon: -81.5, maxLon: -65.0 },
  // US West Coast
  { minLat: 32.0, maxLat: 49.0, minLon: -125.0, maxLon: -117.0 },
  // Gulf of Mexico
  { minLat: 24.0, maxLat: 31.0, minLon: -98.0, maxLon: -80.0 },
  // Alaska
  { minLat: 51.0, maxLat: 72.0, minLon: -180.0, maxLon: -129.0 },
  // Hawaii
  { minLat: 18.5, maxLat: 22.5, minLon: -161.0, maxLon: -154.0 },
  // Great Lakes
  { minLat: 41.0, maxLat: 49.0, minLon: -92.5, maxLon: -76.0 },
  // Pacific NW / Puget Sound
  { minLat: 46.0, maxLat: 49.5, minLon: -125.0, maxLon: -122.0 },
  // Chesapeake Bay
  { minLat: 36.5, maxLat: 39.7, minLon: -77.5, maxLon: -75.5 },
  // Canadian Atlantic
  { minLat: 42.0, maxLat: 52.0, minLon: -67.0, maxLon: -52.0 },
  // Canadian Pacific
  { minLat: 48.0, maxLat: 55.0, minLon: -134.0, maxLon: -122.0 },
];

function isNearCoast(lat: number, lon: number): boolean {
  return COASTAL_BOUNDING_BOXES.some(
    (box) => lat >= box.minLat && lat <= box.maxLat && lon >= box.minLon && lon <= box.maxLon
  );
}

// ── Overlay layer definitions ──────────────────────────────────────

interface OverlayLayer {
  key: string;
  label: string;
  ionicon: string;
  description: string;
  /** Contextual filter: 'coastal' = only near coast, 'winter' = only Nov-Mar, 'always' = always shown */
  context?: 'coastal' | 'winter' | 'always';
}

const OVERLAY_LAYERS: OverlayLayer[] = [
  { key: 'local-bathymetry', label: 'Lake Depth', ionicon: 'analytics-outline', description: 'Bathymetry depth contours (28K+ lakes)' },
  { key: 'public-lands', label: 'Public Lands', ionicon: 'leaf-outline', description: 'Protected and public-access lands overlay' },
  { key: 'access-points', label: 'Access Points', ionicon: 'fish-outline', description: 'Boat launches, shore access, campgrounds' },
  { key: 'parking', label: 'Parking', ionicon: 'car-outline', description: 'Parking lots and pull-offs near access' },
  { key: 'trails', label: 'Trails', ionicon: 'walk-outline', description: 'Named access trails and paths to water' },
  { key: 'nautical', label: 'Nautical Marks', ionicon: 'boat-outline', description: 'OpenSeaMap buoys, channels, marks', context: 'coastal' },
  { key: 'marine-navigation', label: 'NOAA Marine Navigation', ionicon: 'boat-outline', description: 'Maintained channels, shipping regulations, maritime boundaries', context: 'coastal' },
  { key: 'river-network', label: 'River Network', ionicon: 'git-network-outline', description: 'Official USGS + NHN river-network backbone across North America' },
  { key: 'river-navigation', label: 'USACE River Navigation', ionicon: 'git-branch-outline', description: 'Official river depth areas, contours, wrecks, and bridges' },
  { key: 'shaded-relief', label: 'Shaded Relief', ionicon: 'layers-outline', description: '3D terrain and elevation' },
  { key: 'water-flow', label: 'Hydrology', ionicon: 'water-outline', description: 'USGS streams & water features' },
  { key: 'depth-contours', label: 'Depth Contours', ionicon: 'resize-outline', description: 'Official coastal and global bathymetry' },
  { key: 'no-wake-zones', label: 'No-Wake Zones', ionicon: 'speedometer-outline', description: 'Speed-restricted areas on water', context: 'coastal' },
  { key: 'nav-aids', label: 'Nav Aids', ionicon: 'radio-outline', description: 'Buoys, lights, channel markers', context: 'coastal' },
  { key: 'artificial-reefs', label: 'Artificial Reefs', ionicon: 'flag-outline', description: 'State artificial reef GPS locations', context: 'coastal' },
  { key: 'radar', label: 'Precip Radar', ionicon: 'rainy-outline', description: 'Real-time precipitation radar' },
  { key: 'satellite-imagery', label: 'Satellite Imagery', ionicon: 'planet-outline', description: 'ESRI high-res satellite tiles' },
];

const OVERLAY_TILE_URLS: Record<string, string> = {
  'nautical': 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
  'shaded-relief': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}',
  'water-flow': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}',
  'depth-contours': 'https://tiles.arcgis.com/tiles/C8EMgrsFcRFL6LrL/arcgis/rest/services/GEBCO_contours/MapServer/tile/{z}/{y}/{x}',
  'satellite-imagery': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
};

const WORLD_IMAGERY_TILES = [
  'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
];

const TILE_LAYER_NAMES = {
  bathymetryContours: 'bathymetry_contours',
  publicLands: 'public_lands',
  accessPoints: 'access_points',
  oceanContours: 'ocean_contours',
  riverNetwork: 'na_river_network',
} as const;

const ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS = true;
const ENABLE_VECTOR_OCEAN_BATHY = process.env.EXPO_PUBLIC_USE_VECTOR_OCEAN_BATHY !== '0';
const SHOW_RASTER_OCEAN_UNDERLAY = !ENABLE_VECTOR_OCEAN_BATHY;
const GLYPH_URL = 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf';
const FONT_STACKS = {
  regular: ['Open Sans Regular'],
  italic: ['Open Sans Italic'],
  bold: ['Open Sans Bold'],
} as const;
// Vector overlays enabled by default.
// Bathymetry contours now deployed at 24.199.80.77 (tiles.opencatch.app)
const DEFAULT_VECTOR_OVERLAYS = new Set([
  'local-bathymetry',
  'depth-contours',
]);

const WIND_LEGEND_STOPS = [
  { color: '#4CAF50', label: 'Calm' },
  { color: '#FFC107', label: 'Moderate' },
  { color: '#FF9800', label: 'Strong' },
  { color: '#F44336', label: 'Storm' },
];

// Waypoint icon mappings (Ionicons instead of emoji)
const WAYPOINT_ICONS: { key: WaypointIcon; label: string; ionicon: string }[] = [
  { key: 'pin', label: 'Pin', ionicon: 'location' },
  { key: 'fish', label: 'Fish', ionicon: 'fish' },
  { key: 'anchor', label: 'Anchor', ionicon: 'boat' },
  { key: 'star', label: 'Star', ionicon: 'star' },
  { key: 'warning', label: 'Alert', ionicon: 'warning' },
];

const WAYPOINT_COLORS = [
  { color: '#0A6EBD', label: 'Blue' },
  { color: '#C4841D', label: 'Amber' },
  { color: '#C44B4B', label: 'Red' },
  { color: '#4A8DB5', label: 'Teal' },
  { color: '#3D8B37', label: 'Green' },
];

// ── Marina POI styling ────────────────────────────────────────────

const MARINA_POI_CONFIG: Record<MarinaPOIType, { color: string; ionicon: string; label: string }> = {
  marina: { color: '#2563EB', ionicon: 'boat', label: 'Marina' },
  bait_shop: { color: '#16A34A', ionicon: 'fish', label: 'Bait Shop' },
  boat_ramp: { color: '#EA580C', ionicon: 'boat-outline', label: 'Boat Ramp' },
  tackle_shop: { color: '#9333EA', ionicon: 'cart-outline', label: 'Tackle Shop' },
  fishing_pier: { color: '#0D9488', ionicon: 'flag-outline', label: 'Fishing Pier' },
  boat_rental: { color: '#2563EB', ionicon: 'boat', label: 'Boat Rental' },
};

// ── Bathymetry Map Style ─────────────────────────────────────────
// Inspired by wooden Lake Tahoe bathymetry maps and
// https://snailbones.medium.com/styling-oceans-with-bathymetry-in-maplibre-a326e912e02f

const BATHYMETRY_STYLE: object = {
  version: 8,
  name: 'OpenCatch Bathymetry',
  glyphs: GLYPH_URL,
  sprite: 'https://tiles.openfreemap.org/sprites/ofm_f384/ofm',
  sources: {
    // Base map vector tiles — OpenFreeMap (free, no key)
    'openmaptiles': {
      type: 'vector',
      url: 'https://tiles.openfreemap.org/planet',
    },
    // GEBCO bathymetry contour lines (raster)
    'gebco-contours': {
      type: 'raster',
      tiles: [
        'https://tiles.arcgis.com/tiles/C8EMgrsFcRFL6LrL/arcgis/rest/services/GEBCO_contours/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      maxzoom: 12,
    },
    // Inland lake bathymetry filled contours (papercut blue style)
    // NOT included in static style — added dynamically via VectorSource
    // once the tile server is deployed and serving PMTiles.
    // See: ml/bathymetry/fetch_globathy.py for the pipeline.
  },
  layers: [
    // ── Background ──────────────────────────────────────────────
    {
      id: 'background',
      type: 'background',
      paint: {
        'background-color': '#F6F0E4',
      },
    },

    // ── Ocean / water base — graduated depth fills ──────────────
    // The ocean water itself is the dark background. We use the
    // OpenMapTiles "water" layer to paint water features.
    {
      id: 'water-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        // Shallow near-shore → deep ocean gradient via zoom
        'fill-color': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, '#235D80',
          4, '#2B7298',
          6, '#3D8FB8',
          8, '#59ABD0',
          10, '#86CAE3',
          14, '#D5EFF8',
        ],
        'fill-opacity': 0.98,
      },
    },

    // ── Bathymetry depth contour bands ──────────────────────────
    // Graduated fills from shallow (light) to deep (dark)
    // These create the carved wooden map look
    {
      id: 'water-depth-glow',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#D7EDF7',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0,
          5, 0.12,
          8, 0.2,
          12, 0.1,
        ],
      },
    },
    {
      id: 'water-shelf-highlight',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#F4FBFE',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.01,
          5, 0.05,
          8, 0.08,
          12, 0.04,
        ],
      },
    },

    {
      id: 'water-edge-shadow',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#215779',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.06,
          5, 0.14,
          8, 0.24,
          12, 0.16,
        ],
      },
    },

    // ── GEBCO bathymetry contour raster overlay ─────────────────
    {
      id: 'gebco-contour-shadow',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.24,
          6, 0.38,
          10, 0.34,
          14, 0.2,
        ],
        'raster-contrast': 0.36,
        'raster-saturation': -0.52,
        'raster-brightness-min': 0.0,
        'raster-brightness-max': 0.24,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-mid',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.22,
          6, 0.34,
          10, 0.28,
          14, 0.16,
        ],
        'raster-contrast': 0.16,
        'raster-saturation': -0.28,
        'raster-brightness-min': 0.1,
        'raster-brightness-max': 0.56,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-overlay',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.14,
          6, 0.24,
          10, 0.18,
          14, 0.1,
        ],
        'raster-contrast': 0.08,
        'raster-brightness-min': 0.24,
        'raster-brightness-max': 0.94,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-crest',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.08,
          6, 0.14,
          10, 0.12,
          14, 0.06,
        ],
        'raster-contrast': 0.02,
        'raster-brightness-min': 0.54,
        'raster-brightness-max': 1.0,
      },
      minzoom: 0,
      maxzoom: 12,
    },

    // ── Inland lake bathymetry — papercut blue filled contours ──
    // Layers added dynamically via VectorSource once tile server is deployed.
    // Palette: #E8F4FD → #B8DCF0 → #7BB8DE → #4A98C9 → #2574A9 → #1A5276 → #0E3D5C → #071E2E

    // ── Land fill — warm tan/beige like wooden map ──────────────
    {
      id: 'landcover',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landcover',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'grass', '#EDE7D8',
          'wood', '#E4DCCB',
          'ice', '#eef4f8',
          'crop', '#EEE7D6',
          '#F6F0E4',
        ],
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0.5,
          10, 0.8,
        ],
      },
    },
    {
      id: 'landuse',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'park', '#dde8d0',
          'cemetery', '#e0ddd0',
          'hospital', '#f0e8e4',
          'school', '#f0eee4',
          'industrial', '#e8e4dc',
          '#F6F0E4',
        ],
        'fill-opacity': 0.5,
      },
    },
    {
      id: 'land-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': '#F6F0E4',
        'fill-opacity': 0.1,
      },
    },

    // ── Hillshade reserved for future terrain data ──

    // ── Flowlines reserved for future Martin tile server ──

    // ── Waterway lines (rivers, streams from base tiles) ────────
    {
      id: 'waterway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'waterway',
      paint: {
        'line-color': '#2B7AA0',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.6,
          8, 1.3,
          14, 3.0,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.34,
          8, 0.72,
          14, 0.9,
        ],
      },
    },

    // ── Administrative boundaries ───────────────────────────────
    {
      id: 'admin-boundaries',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'boundary',
      filter: ['<=', ['get', 'admin_level'], 4],
      paint: {
        'line-color': '#c5bfa8',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          2, 0.4,
          6, 0.8,
          10, 1.2,
        ],
        'line-dasharray': [3, 2],
        'line-opacity': 0.5,
      },
    },

    // ── Roads hierarchy ─────────────────────────────────────────
    {
      id: 'road-highway-casing',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['==', ['get', 'class'], 'motorway'],
      paint: {
        'line-color': '#d4cdb8',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 2,
          12, 5,
          16, 12,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 0.5,
        ],
      },
      minzoom: 6,
    },
    {
      id: 'road-highway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['==', ['get', 'class'], 'motorway'],
      paint: {
        'line-color': '#e8e2cc',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 1.2,
          12, 3.5,
          16, 9,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 0.7,
        ],
      },
      minzoom: 6,
    },
    {
      id: 'road-primary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['primary', 'trunk']]],
      paint: {
        'line-color': '#e0dac4',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          8, 0,
          10, 0.8,
          14, 2.5,
          18, 7,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          8, 0,
          10, 0.5,
        ],
      },
      minzoom: 8,
    },
    {
      id: 'road-secondary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['secondary', 'tertiary']]],
      paint: {
        'line-color': '#e8e4d0',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          10, 0,
          12, 0.5,
          16, 2,
          18, 5,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          10, 0,
          12, 0.4,
        ],
      },
      minzoom: 10,
    },
    {
      id: 'road-minor',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['minor', 'service', 'track']]],
      paint: {
        'line-color': '#ebe7d6',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          14, 0.5,
          18, 3,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          14, 0.3,
        ],
      },
      minzoom: 13,
    },

    // ── Building footprints ─────────────────────────────────────
    {
      id: 'buildings',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'building',
      paint: {
        'fill-color': '#e0dcc8',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          15, 0.4,
        ],
      },
      minzoom: 14,
    },

    // ── Labels — water bodies ───────────────────────────────────
    {
      id: 'water-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'water_name',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.italic,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 10,
          8, 13,
          14, 16,
        ],
        'text-max-width': 8,
        'text-letter-spacing': 0.15,
      },
      paint: {
        'text-color': '#c5dff0',
        'text-halo-color': 'rgba(10, 22, 40, 0.7)',
        'text-halo-width': 1.5,
      },
    },

    // ── Labels — places (cities, towns) ─────────────────────────
    {
      id: 'place-city',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'city'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.bold,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 11,
          8, 15,
          12, 18,
        ],
        'text-max-width': 8,
      },
      paint: {
        'text-color': '#6b6352',
        'text-halo-color': 'rgba(245, 240, 225, 0.85)',
        'text-halo-width': 1.5,
      },
    },
    {
      id: 'place-town',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['in', ['get', 'class'], ['literal', ['town', 'village']]],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 9,
          10, 12,
          14, 14,
        ],
        'text-max-width': 7,
      },
      paint: {
        'text-color': '#8a8170',
        'text-halo-color': 'rgba(245, 240, 225, 0.8)',
        'text-halo-width': 1.2,
      },
      minzoom: 7,
    },

    // ── Labels — state / province ───────────────────────────────
    {
      id: 'state-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'state'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          3, 9,
          6, 12,
        ],
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.2,
        'text-max-width': 10,
      },
      paint: {
        'text-color': '#a09880',
        'text-halo-color': 'rgba(245, 240, 225, 0.6)',
        'text-halo-width': 1,
      },
      maxzoom: 8,
    },

    // ── Labels — country ────────────────────────────────────────
    {
      id: 'country-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'country'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.bold,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          1, 10,
          4, 14,
          6, 16,
        ],
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.15,
        'text-max-width': 10,
      },
      paint: {
        'text-color': '#8a7e68',
        'text-halo-color': 'rgba(245, 240, 225, 0.7)',
        'text-halo-width': 1.5,
      },
      maxzoom: 6,
    },

    // ── Labels — road names ─────────────────────────────────────
    {
      id: 'road-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'transportation_name',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          12, 9,
          16, 12,
        ],
        'symbol-placement': 'line',
        'text-rotation-alignment': 'map',
        'text-max-angle': 30,
      },
      paint: {
        'text-color': '#9a9484',
        'text-halo-color': 'rgba(245, 240, 225, 0.8)',
        'text-halo-width': 1,
      },
      minzoom: 13,
    },

    // ── Labels — POI ────────────────────────────────────────────
    {
      id: 'poi-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'poi',
      filter: ['<=', ['get', 'rank'], 2],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': 11,
        'text-offset': [0, 0.8],
        'text-anchor': 'top',
        'text-max-width': 6,
      },
      paint: {
        'text-color': '#8a8170',
        'text-halo-color': 'rgba(245, 240, 225, 0.75)',
        'text-halo-width': 1,
      },
      minzoom: 14,
    },
  ],
};

function createHybridStyle(baseStyle: any): object {
  const cloned = JSON.parse(JSON.stringify(baseStyle));
  cloned.name = 'OpenCatch Hybrid';
  cloned.sources = {
    ...cloned.sources,
    'world-imagery': {
      type: 'raster',
      tiles: WORLD_IMAGERY_TILES,
      tileSize: 256,
      maxzoom: 19,
    },
  };

  const imageryLayer = {
    id: 'world-imagery-base',
    type: 'raster',
    source: 'world-imagery',
    paint: {
      'raster-opacity': 1,
      'raster-saturation': 0.05,
      'raster-contrast': 0.08,
    },
  };

  cloned.layers = [
    cloned.layers[0],
    imageryLayer,
    ...cloned.layers.filter((layer: any) => !['landcover', 'landuse', 'land-base', 'buildings'].includes(layer.id)),
  ];

  return cloned;
}

const HYBRID_STYLE = createHybridStyle(BATHYMETRY_STYLE);

// ── OpenCatch Branded Map Style ──────────────────────────────────
// Light cream land with subtle infrastructure outlines, soft blue
// depth contours on water. The signature OpenCatch aesthetic.
const OPENCATCH_STYLE: object = {
  version: 8,
  name: 'OpenCatch',
  glyphs: GLYPH_URL,
  sprite: 'https://tiles.openfreemap.org/sprites/ofm_f384/ofm',
  sources: {
    'openmaptiles': {
      type: 'vector',
      url: 'https://tiles.openfreemap.org/planet',
    },
    'gebco-contours': {
      type: 'raster',
      tiles: [
        'https://tiles.arcgis.com/tiles/C8EMgrsFcRFL6LrL/arcgis/rest/services/GEBCO_contours/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      maxzoom: 12,
    },
  },
  layers: [
    // ── Background — off-white / cream (palette.background) ─────
    {
      id: 'background',
      type: 'background',
      paint: {
        'background-color': '#F7F1E7',
      },
    },

    // ── Water base — soft blue gradient ─────────────────────────
    {
      id: 'water-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, '#E1F1F9',
          4, '#CCE8F4',
          6, '#A9D4E8',
          8, '#7DB8D7',
          10, '#4F96BE',
          14, '#266B93',
        ],
        'fill-opacity': 0.96,
      },
    },

    // ── Depth contour band glow ─────────────────────────────────
    {
      id: 'water-depth-glow',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#F3FAFD',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0,
          5, 0.1,
          8, 0.18,
          12, 0.08,
        ],
      },
    },
    {
      id: 'water-shelf-highlight',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#F8FCFE',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.01,
          5, 0.04,
          8, 0.08,
          12, 0.04,
        ],
      },
    },

    {
      id: 'water-edge-shadow',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#2A6B8F',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.05,
          5, 0.12,
          8, 0.2,
          12, 0.14,
        ],
      },
    },

    // ── GEBCO bathymetry contour overlay ─────────────────────────
    {
      id: 'gebco-contour-shadow',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.18,
          6, 0.28,
          10, 0.24,
          14, 0.12,
        ],
        'raster-contrast': 0.28,
        'raster-saturation': -0.4,
        'raster-brightness-min': 0.0,
        'raster-brightness-max': 0.28,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-mid',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.18,
          6, 0.28,
          10, 0.24,
          14, 0.1,
        ],
        'raster-contrast': 0.12,
        'raster-saturation': -0.2,
        'raster-brightness-min': 0.14,
        'raster-brightness-max': 0.62,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-overlay',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.12,
          6, 0.2,
          10, 0.16,
          14, 0.08,
        ],
        'raster-contrast': 0.06,
        'raster-brightness-min': 0.28,
        'raster-brightness-max': 0.94,
      },
      minzoom: 0,
      maxzoom: 12,
    },
    {
      id: 'gebco-contour-crest',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.06,
          6, 0.12,
          10, 0.1,
          14, 0.05,
        ],
        'raster-contrast': 0.02,
        'raster-brightness-min': 0.56,
        'raster-brightness-max': 1.0,
      },
      minzoom: 0,
      maxzoom: 12,
    },

    // ── Landcover — very subtle differentiation ─────────────────
    {
      id: 'landcover',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landcover',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'grass', '#EDE7D8',
          'wood', '#E4DCCB',
          'ice', '#EEF4F8',
          'crop', '#EEE7D6',
          '#F6F0E4',
        ],
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0.5,
          10, 0.82,
        ],
      },
    },

    // ── Landuse — barely visible ────────────────────────────────
    {
      id: 'landuse',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'park', '#DDE8D0',
          'cemetery', '#E0DDD0',
          'hospital', '#F0E8E4',
          'school', '#F0EEE4',
          'industrial', '#E8E4DC',
          '#F6F0E4',
        ],
        'fill-opacity': 0.52,
      },
    },
    {
      id: 'land-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': '#F6F0E4',
        'fill-opacity': 0.16,
      },
    },

    // ── Waterway lines ──────────────────────────────────────────
    {
      id: 'waterway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'waterway',
      paint: {
        'line-color': '#4F92B8',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.45,
          8, 1.05,
          14, 2.35,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.3,
          8, 0.6,
          14, 0.8,
        ],
      },
    },

    // ── Roads — very subtle gray outlines ───────────────────────
    {
      id: 'road-motorway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['==', ['get', 'class'], 'motorway'],
      paint: {
        'line-color': '#D4CDB8',
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.6, 10, 1.5, 14, 3.4],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.32, 10, 0.58],
      },
      minzoom: 6,
    },
    {
      id: 'road-trunk-primary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['trunk', 'primary']]],
      paint: {
        'line-color': '#E0DAC4',
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.45, 12, 1.2, 14, 2.4],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0.24, 12, 0.56],
      },
      minzoom: 8,
    },
    {
      id: 'road-secondary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['secondary', 'tertiary']]],
      paint: {
        'line-color': '#E8E4D0',
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.34, 14, 1.5],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.18, 14, 0.42],
      },
      minzoom: 10,
    },
    {
      id: 'road-minor',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['minor', 'service', 'path', 'track']]],
      paint: {
        'line-color': '#EBE7D6',
        'line-width': ['interpolate', ['linear'], ['zoom'], 12, 0.24, 16, 1],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 12, 0.12, 16, 0.32],
      },
      minzoom: 12,
    },

    // ── Buildings — very faint outlines ─────────────────────────
    {
      id: 'buildings',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'building',
      paint: {
        'fill-color': '#E0DCC8',
        'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0, 15, 0.38],
        'fill-outline-color': '#D9D2BE',
      },
      minzoom: 13,
    },

    // ── Parking areas — subtle outlines ─────────────────────────
    {
      id: 'parking',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      filter: ['==', ['get', 'class'], 'parking'],
      paint: {
        'fill-color': '#F0EFEC',
        'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0, 15, 0.25],
        'fill-outline-color': '#DCDCDC',
      },
      minzoom: 13,
    },

    // ── Place labels — minimal, dark text ───────────────────────
    {
      id: 'place-city',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'city'],
      layout: {
        'text-field': '{name:latin}',
        'text-font': ['Open Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 10, 8, 14, 12, 16],
        'text-max-width': 8,
      },
      paint: {
        'text-color': '#6B6B65',
        'text-halo-color': '#FAFAF7',
        'text-halo-width': 1.5,
        'text-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0.6, 8, 0.9],
      },
      minzoom: 4,
    },
    {
      id: 'place-town',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'town'],
      layout: {
        'text-field': '{name:latin}',
        'text-font': ['Open Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 6, 9, 10, 12, 14, 14],
        'text-max-width': 7,
      },
      paint: {
        'text-color': '#8A8A85',
        'text-halo-color': '#FAFAF7',
        'text-halo-width': 1.2,
      },
      minzoom: 7,
    },
    {
      id: 'place-village',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['in', ['get', 'class'], ['literal', ['village', 'suburb', 'neighbourhood']]],
      layout: {
        'text-field': '{name:latin}',
        'text-font': ['Open Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 10, 9, 14, 12],
        'text-max-width': 6,
      },
      paint: {
        'text-color': '#A0A098',
        'text-halo-color': '#FAFAF7',
        'text-halo-width': 1,
      },
      minzoom: 10,
    },

    // ── Water labels ────────────────────────────────────────────
    {
      id: 'water-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'water_name',
      layout: {
        'text-field': '{name:latin}',
        'text-font': ['Open Sans Italic'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 6, 10, 10, 13, 14, 15],
        'text-max-width': 6,
      },
      paint: {
        'text-color': '#4A7FA0',
        'text-halo-color': 'rgba(255,255,255,0.6)',
        'text-halo-width': 1,
      },
      minzoom: 5,
    },

    // ── Road labels — subtle ────────────────────────────────────
    {
      id: 'road-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'transportation_name',
      layout: {
        'text-field': '{name:latin}',
        'text-font': ['Open Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 12, 9, 16, 11],
        'symbol-placement': 'line',
        'text-max-angle': 30,
      },
      paint: {
        'text-color': '#B5B5B0',
        'text-halo-color': '#FAFAF7',
        'text-halo-width': 1,
      },
      minzoom: 13,
    },
  ],
};

// ── Night Mode Map Style ──────────────────────────────────────────
// Dark-adapted colors for nighttime on-the-water use.
// Inspired by Navionics night mode — low glare, preserves scotopic vision.

const NIGHT_STYLE: object = {
  version: 8,
  name: 'OpenCatch Night',
  glyphs: GLYPH_URL,
  sprite: 'https://tiles.openfreemap.org/sprites/ofm_f384/ofm',
  sources: {
    'openmaptiles': {
      type: 'vector',
      url: 'https://tiles.openfreemap.org/planet',
    },
    'gebco-contours': {
      type: 'raster',
      tiles: [
        'https://tiles.arcgis.com/tiles/C8EMgrsFcRFL6LrL/arcgis/rest/services/GEBCO_contours/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      maxzoom: 12,
    },
  },
  layers: [
    // ── Background — near-black ────────────────────────────────
    {
      id: 'background',
      type: 'background',
      paint: {
        'background-color': '#080c14',
      },
    },

    // ── Ocean / water base — dark navy depth gradient ──────────
    {
      id: 'water-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, '#040a18',   // zoomed out = near-black deep ocean
          4, '#061020',
          6, '#0a1830',
          8, '#0e2040',
          10, '#122848',
          14, '#183050',  // zoomed in = slightly lighter shallow
        ],
        'fill-opacity': 0.95,
      },
    },

    // ── Bathymetry depth glow — subtle teal shimmer ────────────
    {
      id: 'water-depth-glow',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'water',
      paint: {
        'fill-color': '#1a3a5c',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0,
          5, 0.06,
          8, 0.12,
          12, 0.04,
        ],
      },
    },

    // ── GEBCO bathymetry contour raster — darkened ─────────────
    {
      id: 'gebco-contour-overlay',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.2,
          6, 0.35,
          10, 0.25,
          14, 0.12,
        ],
        'raster-contrast': -0.2,
        'raster-brightness-min': 0.0,
        'raster-brightness-max': 0.35,
      },
      minzoom: 0,
      maxzoom: 12,
    },

    // ── Land — dark charcoal ───────────────────────────────────
    {
      id: 'landcover',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landcover',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'grass', '#141a14',
          'wood', '#101810',
          'ice', '#1a1e22',
          'crop', '#161a14',
          '#121418', // default dark charcoal
        ],
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0.5,
          10, 0.8,
        ],
      },
    },
    {
      id: 'landuse',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      paint: {
        'fill-color': [
          'match',
          ['get', 'class'],
          'park', '#101a10',
          'cemetery', '#141414',
          'hospital', '#1a1418',
          'school', '#18161a',
          'industrial', '#141416',
          '#121418',
        ],
        'fill-opacity': 0.5,
      },
    },
    {
      id: 'land-base',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'landuse',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': '#121418',
        'fill-opacity': 0.1,
      },
    },

    // ── Waterway lines — dim blue ──────────────────────────────
    {
      id: 'waterway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'waterway',
      paint: {
        'line-color': '#1a3050',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.4,
          8, 1,
          14, 2.5,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 0.25,
          8, 0.45,
          14, 0.6,
        ],
      },
    },

    // ── Administrative boundaries — dim ────────────────────────
    {
      id: 'admin-boundaries',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'boundary',
      filter: ['<=', ['get', 'admin_level'], 4],
      paint: {
        'line-color': '#2a2a30',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          2, 0.4,
          6, 0.8,
          10, 1.2,
        ],
        'line-dasharray': [3, 2],
        'line-opacity': 0.4,
      },
    },

    // ── Roads — subtle dark outlines ───────────────────────────
    {
      id: 'road-highway-casing',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['==', ['get', 'class'], 'motorway'],
      paint: {
        'line-color': '#1a1e28',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 2,
          12, 5,
          16, 12,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 0.4,
        ],
      },
      minzoom: 6,
    },
    {
      id: 'road-highway',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['==', ['get', 'class'], 'motorway'],
      paint: {
        'line-color': '#283040',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 1.2,
          12, 3.5,
          16, 9,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 0,
          8, 0.5,
        ],
      },
      minzoom: 6,
    },
    {
      id: 'road-primary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['primary', 'trunk']]],
      paint: {
        'line-color': '#242c38',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          8, 0,
          10, 0.8,
          14, 2.5,
          18, 7,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          8, 0,
          10, 0.4,
        ],
      },
      minzoom: 8,
    },
    {
      id: 'road-secondary',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['secondary', 'tertiary']]],
      paint: {
        'line-color': '#202830',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          10, 0,
          12, 0.5,
          16, 2,
          18, 5,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          10, 0,
          12, 0.3,
        ],
      },
      minzoom: 10,
    },
    {
      id: 'road-minor',
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['minor', 'service', 'track']]],
      paint: {
        'line-color': '#1c2028',
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          14, 0.5,
          18, 3,
        ],
        'line-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          14, 0.25,
        ],
      },
      minzoom: 13,
    },

    // ── Building footprints — barely visible ───────────────────
    {
      id: 'buildings',
      type: 'fill',
      source: 'openmaptiles',
      'source-layer': 'building',
      paint: {
        'fill-color': '#181c24',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          13, 0,
          15, 0.3,
        ],
      },
      minzoom: 14,
    },

    // ── Labels — water bodies (soft cyan on dark) ──────────────
    {
      id: 'water-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'water_name',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.italic,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 10,
          8, 13,
          14, 16,
        ],
        'text-max-width': 8,
        'text-letter-spacing': 0.15,
      },
      paint: {
        'text-color': '#3a7098',
        'text-halo-color': 'rgba(4, 8, 16, 0.8)',
        'text-halo-width': 1.5,
      },
    },

    // ── Labels — places (muted light gray) ─────────────────────
    {
      id: 'place-city',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'city'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.bold,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          4, 11,
          8, 15,
          12, 18,
        ],
        'text-max-width': 8,
      },
      paint: {
        'text-color': '#788090',
        'text-halo-color': 'rgba(8, 12, 20, 0.85)',
        'text-halo-width': 1.5,
      },
    },
    {
      id: 'place-town',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['in', ['get', 'class'], ['literal', ['town', 'village']]],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          6, 9,
          10, 12,
          14, 14,
        ],
        'text-max-width': 7,
      },
      paint: {
        'text-color': '#586070',
        'text-halo-color': 'rgba(8, 12, 20, 0.8)',
        'text-halo-width': 1.2,
      },
      minzoom: 7,
    },

    // ── Labels — state / province ──────────────────────────────
    {
      id: 'state-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'state'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          3, 9,
          6, 12,
        ],
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.2,
        'text-max-width': 10,
      },
      paint: {
        'text-color': '#485060',
        'text-halo-color': 'rgba(8, 12, 20, 0.6)',
        'text-halo-width': 1,
      },
      maxzoom: 8,
    },

    // ── Labels — country ───────────────────────────────────────
    {
      id: 'country-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'place',
      filter: ['==', ['get', 'class'], 'country'],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.bold,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          1, 10,
          4, 14,
          6, 16,
        ],
        'text-transform': 'uppercase',
        'text-letter-spacing': 0.15,
        'text-max-width': 10,
      },
      paint: {
        'text-color': '#586878',
        'text-halo-color': 'rgba(8, 12, 20, 0.7)',
        'text-halo-width': 1.5,
      },
      maxzoom: 6,
    },

    // ── Labels — road names ────────────────────────────────────
    {
      id: 'road-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'transportation_name',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': [
          'interpolate',
          ['linear'],
          ['zoom'],
          12, 9,
          16, 12,
        ],
        'symbol-placement': 'line',
        'text-rotation-alignment': 'map',
        'text-max-angle': 30,
      },
      paint: {
        'text-color': '#404858',
        'text-halo-color': 'rgba(8, 12, 20, 0.8)',
        'text-halo-width': 1,
      },
      minzoom: 13,
    },

    // ── Labels — POI ───────────────────────────────────────────
    {
      id: 'poi-label',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'poi',
      filter: ['<=', ['get', 'rank'], 2],
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT_STACKS.regular,
        'text-size': 11,
        'text-offset': [0, 0.8],
        'text-anchor': 'top',
        'text-max-width': 6,
      },
      paint: {
        'text-color': '#4a5260',
        'text-halo-color': 'rgba(8, 12, 20, 0.75)',
        'text-halo-width': 1,
      },
      minzoom: 14,
    },
  ],
};

// Alternative style URLs for non-bathymetry map types
const ALT_STYLES: Record<string, string> = {
  satellite: 'https://tiles.openfreemap.org/styles/liberty',
  outdoors: 'https://tiles.openfreemap.org/styles/liberty',
  topo: 'https://tiles.openfreemap.org/styles/liberty',
  hybrid: 'https://tiles.openfreemap.org/styles/liberty', // Will use bathymetry for water
};

const VECTOR_OVERLAY_LAYER_BY_KEY: Record<string, string> = {
  // 'local-bathymetry' served directly from Martin PMTiles, not PostGIS
  // Availability check bypassed — always available when Martin is up
  'public-lands': TILE_LAYER_NAMES.publicLands,
  'access-points': TILE_LAYER_NAMES.accessPoints,
  'parking': TILE_LAYER_NAMES.accessPoints,
  'trails': TILE_LAYER_NAMES.accessPoints,
  'river-network': TILE_LAYER_NAMES.riverNetwork,
};

// ── Helpers ───────────────────────────────────────────────────────

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function pointToSegmentDistanceMiles(
  point: RouteLatLng,
  start: RouteLatLng,
  end: RouteLatLng,
): number {
  const meanLatRad = (((start.lat + end.lat + point.lat) / 3) * Math.PI) / 180;
  const milesPerDegLat = 69.0;
  const milesPerDegLon = 69.172 * Math.max(Math.cos(meanLatRad), 0.2);

  const px = (point.lon - start.lon) * milesPerDegLon;
  const py = (point.lat - start.lat) * milesPerDegLat;
  const sx = (end.lon - start.lon) * milesPerDegLon;
  const sy = (end.lat - start.lat) * milesPerDegLat;
  const segLenSq = sx * sx + sy * sy;

  if (segLenSq <= 1e-9) {
    return Math.hypot(px, py);
  }

  const t = Math.max(0, Math.min(1, (px * sx + py * sy) / segLenSq));
  const nearestX = sx * t;
  const nearestY = sy * t;
  return Math.hypot(px - nearestX, py - nearestY);
}

function findNearestRouteInsertionIndex(
  point: RouteLatLng,
  path: RouteLatLng[],
): { insertIndex: number; distanceMiles: number } | null {
  if (path.length < 2) return null;

  let bestIndex = 1;
  let bestDistance = Number.POSITIVE_INFINITY;

  for (let i = 0; i < path.length - 1; i += 1) {
    const distance = pointToSegmentDistanceMiles(point, path[i], path[i + 1]);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = i + 1;
    }
  }

  return { insertIndex: bestIndex, distanceMiles: bestDistance };
}

function getTopSpecies(loc: FishingLocation): string[] {
  if (loc.speciesActivity && loc.speciesActivity.length > 0) {
    return loc.speciesActivity.slice(0, 3).map((s) => s.species);
  }
  return ['Largemouth', 'Smallmouth'];
}

function getTrendIcon(score: number): { name: string; color: string } {
  if (score >= 70) return { name: 'trending-up', color: palette.success };
  if (score >= 50) return { name: 'remove-outline', color: palette.warning };
  return { name: 'trending-down', color: palette.error };
}

// ── Condition Pin marker ──────────────────────────────────────────

function ConditionPin({ score, showQuality = false }: { score: number; showQuality?: boolean }) {
  if (showQuality) {
    const band = getConditionBand(score);
    const config = conditionConfig[band];
    return (
      <View style={styles.pinContainer}>
        <View style={[styles.pin, { backgroundColor: config.color }]}>
          <Ionicons name={config.ionicon as any} size={14} color="#FFFFFF" />
        </View>
        <View style={[styles.pinArrow, { borderTopColor: config.color }]} />
      </View>
    );
  }
  // Default: simple location pin
  return (
    <View style={styles.pinContainer}>
      <View style={[styles.pin, { backgroundColor: palette.accent }]}>
        <Ionicons name="location" size={14} color="#FFFFFF" />
      </View>
      <View style={[styles.pinArrow, { borderTopColor: palette.accent }]} />
    </View>
  );
}

// ── Waypoint diamond marker ────────────────────────────────────────

function WaypointMarkerView({ color, ionicon }: { color: string; ionicon: string }) {
  return (
    <View style={styles.wpMarkerContainer}>
      <View style={[styles.wpDiamond, { backgroundColor: color }]}>
        <View style={{ transform: [{ rotate: '-45deg' }] }}>
          <Ionicons name={ionicon as any} size={11} color="#FFFFFF" />
        </View>
      </View>
    </View>
  );
}

// ── New waypoint modal ────────────────────────────────────────────

interface NewWaypointModalProps {
  visible: boolean;
  coordinate: { latitude: number; longitude: number } | null;
  onSave: (draft: Omit<Waypoint, 'id' | 'createdAt' | 'catches'>) => void;
  onCancel: () => void;
}

function NewWaypointModal({ visible, coordinate, onSave, onCancel }: NewWaypointModalProps) {
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [icon, setIcon] = useState<WaypointIcon>('pin');
  const [color, setColor] = useState(WAYPOINT_COLORS[0].color);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (visible) {
      setName('');
      setNotes('');
      setIcon('pin');
      setColor(WAYPOINT_COLORS[0].color);
      setSaving(false);
    }
  }, [visible]);

  const handleSave = async () => {
    if (!name.trim() || !coordinate) return;
    setSaving(true);
    onSave({
      name: name.trim(),
      notes: notes.trim() || undefined,
      lat: coordinate.latitude,
      lon: coordinate.longitude,
      icon,
      color,
    });
  };

  const selectedIonicon = WAYPOINT_ICONS.find((i) => i.key === icon)?.ionicon ?? 'location';

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onCancel}
      statusBarTranslucent
    >
      <TouchableWithoutFeedback onPress={Keyboard.dismiss}>
        <View style={styles.modalBackdrop}>
          <KeyboardAvoidingView
            behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
            style={styles.modalKAV}
          >
            <View style={styles.modalCard}>
              <View style={styles.modalHandle} />
              <Text style={styles.modalTitle}>Save Spot</Text>
              {coordinate && (
                <Text style={styles.modalCoords}>
                  {coordinate.latitude.toFixed(5)}, {coordinate.longitude.toFixed(5)}
                </Text>
              )}

              <ScrollView
                showsVerticalScrollIndicator={false}
                keyboardShouldPersistTaps="handled"
              >
                <Text style={styles.fieldLabel}>Name</Text>
                <TextInput
                  style={styles.textInput}
                  placeholder="e.g. Big Rock Point"
                  placeholderTextColor={palette.textMuted}
                  value={name}
                  onChangeText={setName}
                  maxLength={60}
                  returnKeyType="next"
                  autoFocus
                />

                <Text style={styles.fieldLabel}>Notes (optional)</Text>
                <TextInput
                  style={[styles.textInput, styles.textInputMultiline]}
                  placeholder="Depth, structure, technique tips..."
                  placeholderTextColor={palette.textMuted}
                  value={notes}
                  onChangeText={setNotes}
                  multiline
                  numberOfLines={3}
                  maxLength={280}
                  textAlignVertical="top"
                />

                <Text style={styles.fieldLabel}>Icon</Text>
                <View style={styles.chipRow}>
                  {WAYPOINT_ICONS.map((item) => (
                    <Pressable
                      key={item.key}
                      style={[
                        styles.iconChip,
                        icon === item.key && { borderColor: palette.accent, backgroundColor: palette.accentLight },
                      ]}
                      onPress={() => setIcon(item.key)}
                    >
                      <Ionicons
                        name={item.ionicon as any}
                        size={16}
                        color={icon === item.key ? palette.accent : palette.textSecondary}
                      />
                      <Text style={[
                        styles.iconChipLabel,
                        icon === item.key && { color: palette.accent },
                      ]}>
                        {item.label}
                      </Text>
                    </Pressable>
                  ))}
                </View>

                <Text style={styles.fieldLabel}>Color</Text>
                <View style={styles.colorRow}>
                  {WAYPOINT_COLORS.map((item) => (
                    <Pressable
                      key={item.color}
                      style={[
                        styles.colorSwatch,
                        { backgroundColor: item.color },
                        color === item.color && styles.colorSwatchSelected,
                      ]}
                      onPress={() => setColor(item.color)}
                    >
                      {color === item.color && (
                        <Ionicons name="checkmark" size={16} color="#FFFFFF" />
                      )}
                    </Pressable>
                  ))}
                </View>

                {/* Preview */}
                <View style={styles.previewRow}>
                  <WaypointMarkerView color={color} ionicon={selectedIonicon} />
                  <Text style={styles.previewLabel}>{name.trim() || 'Unnamed spot'}</Text>
                </View>
              </ScrollView>

              <View style={styles.modalActions}>
                <Pressable style={styles.cancelButton} onPress={onCancel}>
                  <Text style={styles.cancelButtonText}>Cancel</Text>
                </Pressable>
                <Pressable
                  style={[styles.saveButton, (!name.trim() || saving) && styles.saveButtonDisabled]}
                  onPress={handleSave}
                  disabled={!name.trim() || saving}
                >
                  {saving ? (
                    <ActivityIndicator color="#fff" size="small" />
                  ) : (
                    <Text style={styles.saveButtonText}>Save Spot</Text>
                  )}
                </Pressable>
              </View>
            </View>
          </KeyboardAvoidingView>
        </View>
      </TouchableWithoutFeedback>
    </Modal>
  );
}

// ── Map Toggle Button with loading border animation ──────────────
interface MapToggleButtonProps {
  style: any;
  activeStyle?: any;
  isActive: boolean;
  isLoading: boolean;
  icon: string;
  iconSize?: number;
  onPress: () => void;
  accessibilityLabel: string;
  badge?: React.ReactNode;
}

function MapToggleButton({ style, activeStyle, isActive, isLoading, icon, iconSize = 20, onPress, accessibilityLabel, badge }: MapToggleButtonProps) {
  const borderAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (isLoading) {
      const loop = Animated.loop(
        Animated.timing(borderAnim, {
          toValue: 1,
          duration: 1200,
          useNativeDriver: false,
        }),
      );
      loop.start();
      return () => loop.stop();
    } else {
      borderAnim.setValue(0);
    }
  }, [isLoading, borderAnim]);

  const borderColor = borderAnim.interpolate({
    inputRange: [0, 0.5, 1],
    outputRange: [
      'transparent',
      isActive ? 'rgba(255,255,255,0.7)' : palette.accent,
      'transparent',
    ],
  });

  return (
    <Animated.View
      style={[
        { borderRadius: 24, borderWidth: isLoading ? 2.5 : 0, borderColor },
      ]}
    >
      <Pressable
        style={[style, isActive && activeStyle]}
        onPress={onPress}
        accessibilityLabel={accessibilityLabel}
      >
        <Ionicons
          name={icon as any}
          size={iconSize}
          color={isActive ? '#FFFFFF' : palette.textSecondary}
        />
        {badge}
      </Pressable>
    </Animated.View>
  );
}

// ── Layer Picker ──────────────────────────────────────────────────

interface POIFilter {
  key: string;
  label: string;
  ionicon: string;
  isActive: boolean;
  onToggle: () => void;
}

interface LayerPickerProps {
  visible: boolean;
  currentStyle: MapStyleKey;
  activeOverlays: Set<string>;
  showQualityPins: boolean;
  userLocation: { lat: number; lon: number } | null;
  poiFilters: POIFilter[];
  onSelectStyle: (style: MapStyleKey) => void;
  onToggleOverlay: (key: string) => void;
  onToggleQualityPins: () => void;
  onClose: () => void;
  panelStyle?: StyleProp<ViewStyle>;
}

function LayerPicker({ visible, currentStyle, activeOverlays: _activeOverlays, showQualityPins, userLocation: pickerUserLoc, poiFilters: _poiFilters, onSelectStyle, onToggleOverlay: _onToggleOverlay, onToggleQualityPins, onClose, panelStyle }: LayerPickerProps) {
  const fadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(fadeAnim, {
      toValue: visible ? 1 : 0,
      duration: 180,
      useNativeDriver: true,
    }).start();
  }, [visible, fadeAnim]);

  if (!visible) return null;

  return (
    <>
      <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      <Animated.View style={[styles.layerPickerCard, panelStyle, { opacity: fadeAnim }]}>
        <ScrollView style={{ maxHeight: 300 }} showsVerticalScrollIndicator={false} bounces={false}>
          <Text style={styles.layerSectionTitle}>MAP STYLE</Text>
          {MAP_STYLE_KEYS.filter((key) => {
            // Only show nautical chart option when near coast
            if (key === 'nautical-chart') {
              return pickerUserLoc ? isNearCoast(pickerUserLoc.lat, pickerUserLoc.lon) : false;
            }
            return true;
          }).map((key) => {
            const isActive = key === currentStyle;
            return (
              <Pressable
                key={key}
                style={[styles.layerPickerRow, isActive && styles.layerPickerRowActive]}
                onPress={() => { onSelectStyle(key); onClose(); }}
              >
                <Ionicons
                  name={(MAP_STYLE_IONICONS[key] ?? 'map-outline') as any}
                  size={18}
                  color={isActive ? palette.accent : palette.textSecondary}
                />
                <Text
                  style={[
                    styles.layerPickerLabel,
                    isActive && styles.layerPickerLabelActive,
                  ]}
                >
                  {MAP_STYLE_LABELS[key] ?? key}
                </Text>
                {isActive && <Ionicons name="checkmark" size={16} color={palette.accent} />}
              </Pressable>
            );
          })}

          <View style={styles.layerDivider} />
          <Text style={styles.layerSectionTitle}>PIN STYLE</Text>
          <Pressable
            style={[styles.layerPickerRow, showQualityPins && styles.layerPickerRowActive]}
            onPress={onToggleQualityPins}
          >
            <Ionicons
              name="color-palette-outline"
              size={18}
              color={showQualityPins ? palette.accent : palette.textSecondary}
            />
            <View style={styles.overlayLabelArea}>
              <Text style={[styles.layerPickerLabel, showQualityPins && styles.layerPickerLabelActive]}>
                Fishing Quality
              </Text>
              <Text style={styles.overlayDescription}>Color pins by forecast score</Text>
            </View>
            {showQualityPins && <Ionicons name="checkmark" size={16} color={palette.accent} />}
          </Pressable>
        </ScrollView>
      </Animated.View>
    </>
  );
}

// ── Site Card (from Explore) ──────────────────────────────────────

// ── Water body highlight circle GeoJSON generator ──────────────
function makeCircleGeoJSON(
  centerLon: number,
  centerLat: number,
  radiusKm: number = 0.8,
  points: number = 64,
): GeoJSON.FeatureCollection {
  const coords: [number, number][] = [];
  const km = radiusKm;
  for (let i = 0; i <= points; i++) {
    const angle = (i / points) * 2 * Math.PI;
    const dx = km * Math.cos(angle);
    const dy = km * Math.sin(angle);
    const lat = centerLat + (dy / 111.32);
    const lon = centerLon + (dx / (111.32 * Math.cos((centerLat * Math.PI) / 180)));
    coords.push([lon, lat]);
  }
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [coords],
        },
      },
    ],
  };
}

interface SiteCardProps {
  location: FishingLocation;
  distanceMi: number | null;
  onPress: () => void;
}

function SiteCard({ location, distanceMi, onPress }: SiteCardProps) {
  const band = getConditionBand(location.score);
  const config = conditionConfig[band];
  const trend = getTrendIcon(location.score);
  const species = getTopSpecies(location);
  const primaryLabel = getPrimaryLocationLabel(location);
  const secondaryLabel = getSecondaryLocationLabel(location);

  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
      onPress={onPress}
    >
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleArea}>
          <Text style={styles.cardName} numberOfLines={1}>
            {primaryLabel}
          </Text>
          <Text style={styles.cardSubtitle} numberOfLines={1}>
            {secondaryLabel}
          </Text>
        </View>
        <View style={[styles.conditionBadge, { backgroundColor: config.bgTint }]}>
          <Ionicons name={config.ionicon as any} size={13} color={config.color} />
          <Text style={[styles.conditionLabel, { color: config.color }]}>
            {config.label}
          </Text>
        </View>
      </View>

      <View style={styles.cardFooter}>
        <View style={styles.speciesRow}>
          {species.map((sp) => (
            <View key={sp} style={styles.speciesTag}>
              <Text style={styles.speciesTagText}>{sp}</Text>
            </View>
          ))}
        </View>
        <View style={styles.cardMeta}>
          {distanceMi !== null && (
            <Text style={styles.distanceText}>
              {distanceMi < 1 ? '<1' : Math.round(distanceMi)} mi
            </Text>
          )}
          <Ionicons name={trend.name as any} size={16} color={trend.color} />
        </View>
      </View>
    </Pressable>
  );
}

// ── Contextual Topo Hint Bubble ──────────────────────────────────

const TOPO_HINTS = [
  { icon: 'trending-down-outline', text: 'Close contour lines = steep drop-off. Fish stage along ledges like this.' },
  { icon: 'git-merge-outline', text: 'Underwater points are ambush spots. Cast along the edges.' },
  { icon: 'water-outline', text: 'Creek channels act as fish highways. Follow the deepest line.' },
  { icon: 'leaf-outline', text: 'Shallow flats near deep water are prime feeding zones.' },
  { icon: 'snow-outline', text: 'Early spring: bass spawn in 2-6 ft near drop-offs on hard bottom.' },
  { icon: 'flash-outline', text: 'Pike love weed edges, creek mouths, and shallow bays with deep access.' },
  { icon: 'analytics-outline', text: 'Rising bottom (hump) surrounded by deep water concentrates fish.' },
  { icon: 'navigate-outline', text: 'Wind-blown banks push baitfish to shore. Fish the downwind side.' },
];

function TopoHintBubble({ zoom }: { zoom: number }) {
  const [dismissed, setDismissed] = useState(false);
  const [hintIdx] = useState(() => Math.floor(Math.random() * TOPO_HINTS.length));

  if (dismissed) return null;

  const hint = TOPO_HINTS[hintIdx];
  return (
    <View style={styles.topoHintBubble}>
      <Ionicons name={hint.icon as any} size={14} color={palette.accent} />
      <Text style={styles.topoHintBubbleText} numberOfLines={2}>{hint.text}</Text>
      <Pressable onPress={() => setDismissed(true)} hitSlop={8}>
        <Ionicons name="close" size={14} color={palette.textMuted} />
      </Pressable>
    </View>
  );
}


// ── Contextual Tip Card (condition-aware, lake-specific) ─────────

const TIP_ROTATE_INTERVAL = 15_000; // 15 seconds

interface ContextualTipCardProps {
  tips: ContextualTip[];
  waterbodyName: string;
  onDismiss: () => void;
}

const ContextualTipCard = React.memo(function ContextualTipCard({ tips, waterbodyName, onDismiss }: ContextualTipCardProps) {
  const [currentIdx, setCurrentIdx] = useState(0);
  const fadeAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (tips.length <= 1) return;
    const interval = setInterval(() => {
      Animated.timing(fadeAnim, { toValue: 0, duration: 200, useNativeDriver: true }).start(() => {
        setCurrentIdx((prev) => (prev + 1) % tips.length);
        Animated.timing(fadeAnim, { toValue: 1, duration: 200, useNativeDriver: true }).start();
      });
    }, TIP_ROTATE_INTERVAL);
    return () => clearInterval(interval);
  }, [tips.length, fadeAnim]);

  if (tips.length === 0) return null;

  const tip = tips[currentIdx % tips.length];

  return (
    <View style={styles.contextualTipCard}>
      <View style={styles.contextualTipHeader}>
        <Ionicons name="bulb-outline" size={14} color={palette.warning} />
        <Text style={styles.contextualTipHeaderText} numberOfLines={1}>{waterbodyName}</Text>
        <Pressable onPress={onDismiss} hitSlop={8} style={styles.contextualTipClose}>
          <Ionicons name="close" size={14} color={palette.textMuted} />
        </Pressable>
      </View>
      <Animated.View style={{ opacity: fadeAnim }}>
        <View style={styles.contextualTipBody}>
          <Ionicons name={tip.icon as any} size={16} color={palette.accent} style={{ marginTop: 1 }} />
          <View style={{ flex: 1 }}>
            <Text style={styles.contextualTipTitle}>{tip.title}</Text>
            <Text style={styles.contextualTipText} numberOfLines={3}>{tip.text}</Text>
          </View>
        </View>
      </Animated.View>
      {tips.length > 1 && (
        <View style={styles.contextualTipDots}>
          {tips.map((_, i) => (
            <View
              key={i}
              style={[
                styles.contextualTipDot,
                i === currentIdx % tips.length && styles.contextualTipDotActive,
              ]}
            />
          ))}
        </View>
      )}
    </View>
  );
});

// ── Access Point Summary Pill ────────────────────────────────────

interface AccessPointSummary {
  boat_launch: number;
  shore_fishing: number;
  kayak_launch: number;
  parking: number;
  trailhead: number;
  fishing_pier: number;
  fish_cleaning: number;
}

function computeAccessSummary(points: AccessPoint[]): AccessPointSummary {
  const summary: AccessPointSummary = {
    boat_launch: 0, shore_fishing: 0, kayak_launch: 0,
    parking: 0, trailhead: 0, fishing_pier: 0, fish_cleaning: 0,
  };
  for (const p of points) {
    const k = p.type as string;
    if (k in summary) (summary as any)[k] = ((summary as any)[k] || 0) + 1;
  }
  return summary;
}

function AccessPointSummaryPill({
  summary,
  onDismiss,
}: {
  summary: AccessPointSummary;
  onDismiss: () => void;
}) {
  const parts: string[] = [];
  if (summary.boat_launch > 0) parts.push(`${summary.boat_launch} boat launch${summary.boat_launch > 1 ? 'es' : ''}`);
  if (summary.parking > 0) parts.push(`${summary.parking} parking`);
  if (summary.trailhead > 0) parts.push(`${summary.trailhead} trailhead${summary.trailhead > 1 ? 's' : ''}`);
  if (summary.shore_fishing > 0) parts.push(`${summary.shore_fishing} shore access`);
  if (summary.kayak_launch > 0) parts.push(`${summary.kayak_launch} kayak launch${summary.kayak_launch > 1 ? 'es' : ''}`);
  if (summary.fishing_pier > 0) parts.push(`${summary.fishing_pier} pier${summary.fishing_pier > 1 ? 's' : ''}`);
  if (summary.fish_cleaning > 0) parts.push(`${summary.fish_cleaning} cleaning station${summary.fish_cleaning > 1 ? 's' : ''}`);

  if (parts.length === 0) return null;

  return (
    <View style={styles.accessSummaryPill}>
      <Ionicons name="navigate-circle-outline" size={16} color={palette.accent} />
      <Text style={styles.accessSummaryText} numberOfLines={1}>
        {parts.join(', ')}
      </Text>
      <Pressable onPress={onDismiss} hitSlop={8}>
        <Ionicons name="close" size={14} color={palette.textMuted} />
      </Pressable>
    </View>
  );
}

// ── Measure helper ────────────────────────────────────────────────

function measureTotalDistance(points: [number, number][]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    total += haversineDistance(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]);
  }
  return total;
}

// ── Compass helpers ──────────────────────────────────────────────
const CARDINAL_LABELS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'] as const;
function degreesToCardinal(deg: number): string { const n = ((deg % 360) + 360) % 360; return CARDINAL_LABELS[Math.round(n / 22.5) % 16]; }
type CompassMode = 'static' | 'heading';
const COMPASS_SIZE = 52;
const COMPASS_HALF = COMPASS_SIZE / 2;
function CompassRoseSvg() { const r = COMPASS_HALF - 2; const tickR = r - 3; const labelR = r - 11; const cardinals = [{ label: 'N', angle: 0 },{ label: 'E', angle: 90 },{ label: 'S', angle: 180 },{ label: 'W', angle: 270 }]; return (<Svg width={COMPASS_SIZE} height={COMPASS_SIZE}><G origin={`${COMPASS_HALF}, ${COMPASS_HALF}`}><Circle cx={COMPASS_HALF} cy={COMPASS_HALF} r={r} stroke={palette.border} strokeWidth={1} fill="none" />{Array.from({ length: 12 }).map((_, i) => { const a = i * 30; const rad = (a * Math.PI) / 180; const iC = a % 90 === 0; const iR = iC ? tickR - 5 : tickR - 3; return (<Line key={a} x1={COMPASS_HALF + Math.sin(rad) * iR} y1={COMPASS_HALF - Math.cos(rad) * iR} x2={COMPASS_HALF + Math.sin(rad) * tickR} y2={COMPASS_HALF - Math.cos(rad) * tickR} stroke={iC ? palette.text : palette.textMuted} strokeWidth={iC ? 1.5 : 0.8} />); })}<Polygon points={`${COMPASS_HALF},${COMPASS_HALF - r + 1} ${COMPASS_HALF - 3},${COMPASS_HALF - r + 8} ${COMPASS_HALF + 3},${COMPASS_HALF - r + 8}`} fill="#C44B4B" />{cardinals.map(({ label, angle }) => { const rad = (angle * Math.PI) / 180; return (<SvgText key={label} x={COMPASS_HALF + Math.sin(rad) * labelR} y={COMPASS_HALF - Math.cos(rad) * labelR + 3.5} fontSize={label === 'N' ? 9 : 7} fontWeight={label === 'N' ? '700' : '600'} fill={label === 'N' ? '#C44B4B' : palette.textSecondary} textAnchor="middle">{label}</SvgText>); })}<Circle cx={COMPASS_HALF} cy={COMPASS_HALF} r={2} fill={palette.accent} /></G></Svg>); }
function CompassWidget({ heading, mode, onToggleMode, style }: { heading: number; mode: CompassMode; onToggleMode: () => void; style?: StyleProp<ViewStyle> }) { const animatedRotation = useRef(new Animated.Value(0)).current; const lastH = useRef(0); useEffect(() => { let d = heading - lastH.current; if (d > 180) d -= 360; if (d < -180) d += 360; const t = lastH.current + d; lastH.current = t; Animated.timing(animatedRotation, { toValue: -t, duration: 250, useNativeDriver: true }).start(); }, [heading, animatedRotation]); const rotI = animatedRotation.interpolate({ inputRange: [-720, 720], outputRange: ['-720deg', '720deg'] }); const cardinal = degreesToCardinal(heading); const degLabel = `${Math.round(((heading % 360) + 360) % 360)}\u00B0`; return (<Pressable style={[cwStyles.container, style, mode === 'heading' && cwStyles.containerActive]} onPress={onToggleMode} accessibilityLabel={`Compass: ${degLabel} ${cardinal}. Tap to toggle heading mode.`}><Animated.View style={{ transform: [{ rotate: rotI }] }}><CompassRoseSvg /></Animated.View><View style={cwStyles.readout}><Text style={cwStyles.degrees}>{degLabel}</Text><Text style={cwStyles.cardinal}>{cardinal}</Text></View>{mode === 'heading' && <View style={cwStyles.trackingDot} />}</Pressable>); }
const cwStyles = StyleSheet.create({ container: { position: 'absolute', left: 16, width: COMPASS_SIZE + 8, alignItems: 'center', backgroundColor: palette.surface, borderRadius: (COMPASS_SIZE + 8) / 2, paddingVertical: 4, paddingHorizontal: 4, shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4, zIndex: 40 }, containerActive: { borderWidth: 1.5, borderColor: palette.accent }, readout: { flexDirection: 'row', alignItems: 'baseline', gap: 2, marginTop: 1, marginBottom: 2 }, degrees: { fontSize: 10, fontWeight: '700', color: palette.text }, cardinal: { fontSize: 8, fontWeight: '600', color: palette.textSecondary }, trackingDot: { position: 'absolute', top: 4, right: 4, width: 6, height: 6, borderRadius: 3, backgroundColor: palette.accent } });

// ── MapScreen ─────────────────────────────────────────────────────

type MarkerMode = 'locations' | 'waypoints';
type RouteCameraMode = 'overview' | 'follow' | 'follow-3d';

const FEET_PER_METER = 3.28084;
const MILES_TO_KM = 1.60934;
const ROUTE_DRAFT_STEP_METERS = 0.1;
const MIN_ROUTE_DRAFT_METERS = 0.2;
const MAX_ROUTE_DRAFT_METERS = 6;

function buildWaterTapLocationId(name: string, lat: number, lon: number): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  return `water-tap-${slug || 'unnamed'}-${lat.toFixed(4)}-${lon.toFixed(4)}`;
}

function isNamedWaterbody(name: unknown): boolean {
  if (typeof name !== 'string') return false;
  const trimmed = name.trim();
  if (!trimmed) return false;
  const lowered = trimmed.toLowerCase();
  return lowered !== 'water body' && lowered !== 'water' && lowered !== 'unknown';
}

export function MapScreen({ navigation }: TabProps<'MapTab'>) {
  const { units, isMetric } = useUnits();
  const insets = useSafeAreaInsets();
  const cameraRef = useRef<CameraRef>(null);
  const mapRef = useRef<MapViewRef>(null);
  const locationSourceRef = useRef<any>(null);

  const [locations, setLocations] = useState<FishingLocation[]>([]);
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);
  const [bestFishing, setBestFishing] = useState<BestFishingV2Entry[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [userLocation, setUserLocation] = useState<{ lat: number; lon: number } | null>(null);

  // Map style selection — default to OpenCatch (cream land + depth contours)
  const [mapStyle, setMapStyle] = useState<MapStyleKey>('opencatch');
  const [layerPickerVisible, setLayerPickerVisible] = useState(false);

  // Which set of markers to show
  const [markerMode, setMarkerMode] = useState<MarkerMode>('locations');

  // Overlay layers
  const [activeOverlays, setActiveOverlays] = useState<Set<string>>(new Set(DEFAULT_VECTOR_OVERLAYS));
  const [availableVectorLayers, setAvailableVectorLayers] = useState<Record<string, boolean>>({});
  const [showQualityPins, setShowQualityPins] = useState(false);

  // Marina POI layer
  const [marinasEnabled, setMarinasEnabled] = useState(false);
  const [marinaPOIs, setMarinaPOIs] = useState<MarinaPOI[]>([]);
  const [marinasLoading, setMarinasLoading] = useState(false);
  const [selectedMarina, setSelectedMarina] = useState<MarinaPOI | null>(null);
  // POI type filters — when non-empty, only show these types on the map
  const [poiTypeFilters, setPOITypeFilters] = useState<Set<MarinaPOIType>>(new Set());
  const marinaFetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastMarinaCenter = useRef<{ lat: number; lon: number } | null>(null);

  // Long-press / waypoint creation
  const [pendingCoord, setPendingCoord] = useState<{ latitude: number; longitude: number } | null>(null);
  const [showWaypointModal, setShowWaypointModal] = useState(false);

  // Long-press contextual menu
  const [longPressMenuVisible, setLongPressMenuVisible] = useState(false);
  const [longPressCoord, setLongPressCoord] = useState<LongPressCoordinate | null>(null);

  // Selected marker for callout
  const [selectedMarkerId, setSelectedMarkerId] = useState<string | null>(null);
  const [focusedLocation, setFocusedLocation] = useState<FishingLocation | null>(null);
  const [selectedContourDepth, setSelectedContourDepth] = useState<ContourDepthResult | null>(null);
  const [selectedContourLake, setSelectedContourLake] = useState<ContourLakeAttribution | null>(null);

  const clearSelectedContour = useCallback(() => {
    setSelectedContourDepth(null);
    setSelectedContourLake(null);
  }, []);

  // Map Tools drawer (progressive disclosure — avoids Kitchen Sink pattern)
  const [mapToolsDrawerOpen, setMapToolsDrawerOpen] = useState(false);

  // Measure / ruler mode
  const [measureMode, setMeasureMode] = useState(false);
  const [measurePoints, setMeasurePoints] = useState<[number, number][]>([]);

  // Route planner — map-native route creation/editing
  const [routeMode, setRouteMode] = useState(false);
  const [routePoints, setRoutePoints] = useState<RouteLatLng[]>([]);
  const [pendingRouteDestination, setPendingRouteDestination] = useState<{
    point: RouteLatLng;
    label?: string;
  } | null>(null);
  const [routePlacementMode, setRoutePlacementMode] = useState<RoutePlacementMode>('start');
  const [routePlannerExpanded, setRoutePlannerExpanded] = useState(false);
  const [routeName, setRouteName] = useState('');
  const [plannedRoute, setPlannedRoute] = useState<PlannedRoute | null>(null);
  const [routeMetrics, setRouteMetrics] = useState<RouteMetrics | null>(null);
  const [routeWarnings, setRouteWarnings] = useState<ShallowWarning[]>([]);
  const [routeAlerts, setRouteAlerts] = useState<string[]>([]);
  const [routeBoatProfile, setRouteBoatProfile] = useState<BoatRouteProfile | null>(null);
  const [routeDepthChecking, setRouteDepthChecking] = useState(false);
  const [routeNavActive, setRouteNavActive] = useState(false);
  const [routeNavIndex, setRouteNavIndex] = useState(1);
  const [routeNavInfo, setRouteNavInfo] = useState<RouteNavInfo | undefined>(undefined);
  const [routeCameraMode, setRouteCameraMode] = useState<RouteCameraMode>('overview');
  const [routeTurnSheetVisible, setRouteTurnSheetVisible] = useState(false);
  const [searchOverlayHeight, setSearchOverlayHeight] = useState(0);
  const [visibleContourBounds, setVisibleContourBounds] = useState<ContourSourceBounds | null>(null);
  const lastContourBoundsRef = useRef<ContourSourceBounds | null>(null);
  const navLocationSubRef = useRef<Location.LocationSubscription | null>(null);

  // Compass heading
  const [compassMode, setCompassMode] = useState<CompassMode>('static');
  const [compassHeading, setCompassHeading] = useState(0);
  const [userCourseHeading, setUserCourseHeading] = useState(0);

  // Weather alerts
  const [weatherAlerts, setWeatherAlerts] = useState<FishingWeatherAlert[]>([]);
  const [alertBannerDismissed, setAlertBannerDismissed] = useState(false);
  const [alertBannerExpanded, setAlertBannerExpanded] = useState(false);
  const alertRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Wind overlay
  const [windEnabled, setWindEnabled] = useState(false);
  const [windVectorCount, setWindVectorCount] = useState(0);
  const [windLoading, setWindLoading] = useState(false);
  const [mapViewportCenter, setMapViewportCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [mapFeatureLocationMode, setMapFeatureLocationMode] = useState<'current' | 'map-center'>('current');
  const [mapWeatherForecast, setMapWeatherForecast] = useState<MapWeatherForecast | null>(null);
  const [mapTideSummary, setMapTideSummary] = useState<MapTideSummary | null>(null);
  const [mapWeatherLoading, setMapWeatherLoading] = useState(false);
  const [mapWeatherPanelDismissed, setMapWeatherPanelDismissed] = useState(false);
  const weatherForecastDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Precipitation radar overlay
  const [radarEnabled, setRadarEnabled] = useState(false);
  const [radarTileUrl, setRadarTileUrl] = useState<string | null>(null);
  const [radarLoading, setRadarLoading] = useState(false);
  const [radarFrames, setRadarFrames] = useState<Array<{ tileUrl: string; timestamp: RadarTimestamp }>>([]);
  const [radarFrameIndex, setRadarFrameIndex] = useState(0);
  const [radarPlaying, setRadarPlaying] = useState(false);
  const radarAnimRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Storm tracking overlay
  const [stormCells, setStormCells] = useState<StormCell[]>([]);
  const [stormGeoJSON, setStormGeoJSON] = useState<StormGeoJSON | null>(null);

  // Access points overlay
  const [accessEnabled, setAccessEnabled] = useState(false);
  const [accessPoints, setAccessPoints] = useState<AccessPoint[]>([]);
  const [accessTrailGeoJSON, setAccessTrailGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const accessFetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAccessCenter = useRef<{ lat: number; lon: number } | null>(null);

  // Wave height overlay (coastal only)
  const [waveEnabled, setWaveEnabled] = useState(false);
  const [waveLoading, setWaveLoading] = useState(false);

  // Depth number overlay (nautical chart soundings)
  const [depthNumbersEnabled, setDepthNumbersEnabled] = useState(false);
  const [depthLoading, setDepthLoading] = useState(false);

  // Marine gas stations overlay
  const [marineGasEnabled, setMarineGasEnabled] = useState(false);

  // Ocean fishing spots overlay (coastal only)
  const [oceanSpotsEnabled, setOceanSpotsEnabled] = useState(false);

  // Sea Surface Temperature overlay (coastal only)
  const [sstEnabled, setSSTEnabled] = useState(false);

  // 3D terrain / relief shading
  const [terrain3DEnabled, setTerrain3DEnabled] = useState(false);

  // Tidal current overlay (coastal only)
  const [tidalCurrentEnabled, setTidalCurrentEnabled] = useState(false);
  const [tidalLoading, setTidalLoading] = useState(false);

  // ── New map-integrated overlays ──
  const [iceThicknessEnabled, setIceThicknessEnabled] = useState(false);
  const [iceLoading, setIceLoading] = useState(false);
  const [fishingPressureOverlayEnabled, setFishingPressureOverlayEnabled] = useState(false);
  const [pressureLoading, setPressureLoading] = useState(false);
  const [biteTimeOverlayEnabled, setBiteTimeOverlayEnabled] = useState(false);
  const [biteLoading, setBiteLoading] = useState(false);
  const [biteFieldGeoJSON, setBiteFieldGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);

  // Draft accessibility overlay (shows safe/caution/danger based on boat draft)
  const [draftAccessEnabled, setDraftAccessEnabled] = useState(false);
  const [boatDraftFt, setBoatDraftFt] = useState<number>(3); // default 3ft

  // Load boat draft from profile on mount
  useEffect(() => {
    getDefaultBoat().then((boat) => {
      if (boat?.draftFt && boat.draftFt > 0) setBoatDraftFt(boat.draftFt);
    }).catch(() => { /* use default */ });
  }, []);

  useEffect(() => {
    getDefaultBoatRouteProfile()
      .then(setRouteBoatProfile)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!routeBoatProfile?.draftMeters || routeBoatProfile.draftMeters <= 0) return;
    setBoatDraftFt(Number((routeBoatProfile.draftMeters * FEET_PER_METER).toFixed(1)));
  }, [routeBoatProfile?.draftMeters]);

  // Dynamic Depths overlay (tide-adjusted charted depths)
  const [dynamicDepthsEnabled, setDynamicDepthsEnabled] = useState(false);
  const [dynamicDepthLoading, setDynamicDepthLoading] = useState(false);
  const [tideBadgeText, setTideBadgeText] = useState('');
  const [tideLevelM, setTideLevelM] = useState(0);
  const [tideStationName, setTideStationName] = useState<string | undefined>();

  // ── Overlay loading banner state ──
  const overlayLoadingSet = React.useMemo(() => {
    const s = new Set<string>();
    if (windLoading) s.add('Wind');
    if (waveLoading) s.add('Wave');
    if (depthLoading) s.add('Depth');
    if (dynamicDepthLoading) s.add('Dynamic Depths');
    if (tidalLoading) s.add('Tidal');
    if (iceLoading) s.add('Ice');
    if (pressureLoading) s.add('Pressure');
    if (biteLoading) s.add('Bite');
    if (radarLoading) s.add('Radar');
    return s;
  }, [windLoading, waveLoading, depthLoading, dynamicDepthLoading, tidalLoading, iceLoading, pressureLoading, biteLoading, radarLoading]);

  // USACE survey overlay (channel depths, locks, harbors)
  const [usaceSurveyEnabled, setUsaceSurveyEnabled] = useState(false);

  // Seabed characteristics overlay
  const [seabedEnabled, setSeabedEnabled] = useState(false);
  const [seabedLoading, setSeabedLoading] = useState(false);

  // Maritime boundaries overlay (shipping lanes, restricted areas)
  const [maritimeBoundariesEnabled, setMaritimeBoundariesEnabled] = useState(false);
  const [maritimeBoundariesLoading, setMaritimeBoundariesLoading] = useState(false);

  // AIS WiFi receiver overlay
  const [aisWifiEnabled, setAisWifiEnabled] = useState(false);
  const [aisWifiGeoJSON, setAisWifiGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [marineInstrumentData, setMarineInstrumentData] = useState<MarineInstrumentData>(() => aisReceiver.getInstrumentData());

  useEffect(() => {
    setMarineInstrumentData(aisReceiver.getInstrumentData());
    const unsub = aisReceiver.addInstrumentListener((snapshot) => {
      setMarineInstrumentData(snapshot);
    });
    return () => {
      unsub();
    };
  }, []);

  useEffect(() => {
    const externalPosition = marineInstrumentData.position;
    if (!externalPosition) return;

    const coords = { lat: externalPosition.lat, lon: externalPosition.lon };
    setUserLocation(coords);
    AsyncStorage.setItem(LAST_LOCATION_KEY, JSON.stringify(coords)).catch(() => {});

    if (marineInstrumentData.heading?.headingDeg != null) {
      setCompassHeading(marineInstrumentData.heading.headingDeg);
      setUserCourseHeading(marineInstrumentData.heading.headingDeg);
    } else if (externalPosition.cogDeg != null) {
      setUserCourseHeading(externalPosition.cogDeg);
    }
  }, [
    marineInstrumentData.heading?.headingDeg,
    marineInstrumentData.position?.cogDeg,
    marineInstrumentData.position?.lat,
    marineInstrumentData.position?.lon,
  ]);

  // Dynamic fishing spot discovery (OSM Overpass)
  // Initialize from module-level cache so pins survive tab switches
  const [discoveredSpots, setDiscoveredSpots] = useState<DiscoveredSpot[]>(_cachedDiscoveredSpots);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);
  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastDiscoveryBbox = useRef<BoundingBox | null>(_cachedDiscoveryBbox);
  const lastDiscoveryZoom = useRef<number>(_cachedDiscoveryZoom);
  /** Timestamp of component mount — Overpass discovery is deferred for 3s after mount */
  const mountTimeRef = useRef<number>(Date.now());
  /** Holds the last known good set of pins so we never flash empty during a fetch */
  const previousSpotsRef = useRef<DiscoveredSpot[]>(_cachedDiscoveredSpots);

  // Active track recording overlay
  const [liveTrack, setLiveTrack] = useState<FishingTrack | null>(null);
  const [liveTrackGeoJSON, setLiveTrackGeoJSON] = useState<any>(null);
  const [dismissedTrackId, setDismissedTrackId] = useState<string | null>(null);
  const recordingPulse = useRef(new Animated.Value(1)).current;

  // Focused location card slide-up animation
  const focusedCardTranslateY = useRef(new Animated.Value(120)).current;
  const focusedCardOpacity = useRef(new Animated.Value(0)).current;

  // Water body highlight opacity (static — rAF pulse removed to avoid 60fps re-renders)

  // Contextual tips (Feature 1)
  const [contextualTips, setContextualTips] = useState<ContextualTip[]>([]);
  const [contextualTipsDismissed, setContextualTipsDismissed] = useState(false);
  const [contextualWaterbodyName, setContextualWaterbodyName] = useState('');

  // Catch photo overlay
  const [photosEnabled, setPhotosEnabled] = useState(false);
  const [catchPhotos, setCatchPhotos] = useState<CatchWithPhoto[]>([]);
  const [catchPhotosLoading, setCatchPhotosLoading] = useState(false);
  const [selectedCatchPhoto, setSelectedCatchPhoto] = useState<CatchWithPhoto | null>(null);
  const [photosInView, setPhotosInView] = useState(0);

  // Highlighted access points on spot click (Feature 2)
  const [highlightedAccessPoints, setHighlightedAccessPoints] = useState<AccessPoint[]>([]);
  const [highlightedAccessSummary, setHighlightedAccessSummary] = useState<AccessPointSummary | null>(null);
  const [highlightedAccessLoading, setHighlightedAccessLoading] = useState(false);
  // River spot splitting — sub-spots built from access points along a river
  const [riverSpots, setRiverSpots] = useState<{ id: string; name: string; lat: number; lon: number; apType: string }[]>([]);

  // ── Anchor Watch & MOB overlays ──────────────────────────────────
  const [anchorStatus, setAnchorStatusMap] = useState<AnchorStatus>(getAnchorStatus());
  const [mobStatus, setMobStatusMap] = useState<MOBStatus>({
    active: false,
    event: null,
    distanceMeters: 0,
    bearing: 0,
    currentLat: null,
    currentLon: null,
  });
  const anchorBannerPulse = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const unsub1 = addAnchorListener(setAnchorStatusMap);
    const unsub2 = addMOBListener(setMobStatusMap);
    return () => { unsub1(); unsub2(); };
  }, []);

  // Pulse the anchor banner when alarming
  useEffect(() => {
    if (!anchorStatus.alarm) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(anchorBannerPulse, { toValue: 1.03, duration: 500, useNativeDriver: true }),
        Animated.timing(anchorBannerPulse, { toValue: 1, duration: 500, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [anchorStatus.alarm, anchorBannerPulse]);

  // Anchor watch GeoJSON for map overlay
  const anchorCircleFeature = useMemo(() => {
    if (!anchorStatus.active || !anchorStatus.watch) return null;
    return anchorCircleGeoJSON(
      anchorStatus.watch.anchorLat,
      anchorStatus.watch.anchorLon,
      anchorStatus.watch.radiusMeters,
    );
  }, [anchorStatus.active, anchorStatus.watch?.anchorLat, anchorStatus.watch?.anchorLon, anchorStatus.watch?.radiusMeters]);

  const anchorPointGeoJSON = useMemo(() => {
    if (!anchorStatus.active || !anchorStatus.watch) return null;
    return {
      type: 'FeatureCollection' as const,
      features: [{
        type: 'Feature' as const,
        properties: { icon: 'anchor' },
        geometry: {
          type: 'Point' as const,
          coordinates: [anchorStatus.watch.anchorLon, anchorStatus.watch.anchorLat],
        },
      }],
    };
  }, [anchorStatus.active, anchorStatus.watch?.anchorLat, anchorStatus.watch?.anchorLon]);

  const mobPointGeoJSON = useMemo(() => {
    if (!mobStatus.active || !mobStatus.event) return null;
    return {
      type: 'FeatureCollection' as const,
      features: [{
        type: 'Feature' as const,
        properties: { icon: 'mob' },
        geometry: {
          type: 'Point' as const,
          coordinates: [mobStatus.event.lon, mobStatus.event.lat],
        },
      }],
    };
  }, [mobStatus.active, mobStatus.event?.lat, mobStatus.event?.lon]);

  // ── Chart Annotations ──────────────────────────────────────────────
  const [annotationMode, setAnnotationMode] = useState(false);
  const [annotationTool, setAnnotationTool] = useState<AnnotationType>('marker');
  const [annotationColor, setAnnotationColor] = useState<string>(ANNOTATION_COLORS[0].color);
  const [annotationIcon, setAnnotationIcon] = useState('fish');
  const [annotations, setAnnotations] = useState<MapAnnotation[]>([]);
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [annotationTextInput, setAnnotationTextInput] = useState('');
  const [annotationTextCoord, setAnnotationTextCoord] = useState<AnnotationCoordinate | null>(null);
  const [showAnnotationTextModal, setShowAnnotationTextModal] = useState(false);
  // Arrow drawing state: first tap sets start, second sets end
  const [arrowStart, setArrowStart] = useState<AnnotationCoordinate | null>(null);

  // ── Depth Contour Settings ─────────────────────────────────────────
  const [contourSettings, setContourSettings] = useState<DepthContourSettings>(DEFAULT_CONTOUR_SETTINGS);
  const [showContourModal, setShowContourModal] = useState(false);
  const coachMarkTargets = useMemo(() => ({
    'search-bar': {
      x: SCREEN_WIDTH / 2,
      y: (Platform.OS === 'ios' ? 60 : 20) + 22,
      width: SCREEN_WIDTH - 40,
      height: 46,
    },
    'quick-action': {
      x: SCREEN_WIDTH - 40,
      y: SCREEN_HEIGHT - (Platform.OS === 'ios' ? 136 : 102) - 24,
      width: 52,
      height: 52,
    },
    'bottom-sheet': {
      x: SCREEN_WIDTH / 2,
      y: SCREEN_HEIGHT - SHEET_COLLAPSED + 26,
      width: SCREEN_WIDTH - 28,
      height: 54,
    },
  }), []);

  // Bottom sheet
  const sheetHeight = useRef(new Animated.Value(SHEET_COLLAPSED)).current;
  const currentHeight = useRef(SHEET_COLLAPSED);
  const dragStartHeight = useRef(SHEET_COLLAPSED);
  const [sheetSettled, setSheetSettled] = useState(true);

  // Tips modal (shown on-demand from focused card)
  const [showTipsModal, setShowTipsModal] = useState(false);

  useEffect(() => {
    if (mapStyle === 'hybrid') {
      setMapStyle('satellite');
      return;
    }
    if (mapStyle === 'bathymetry' || mapStyle === 'topo') {
      setMapStyle('opencatch');
    }
  }, [mapStyle]);

  // Load annotations and contour settings from AsyncStorage on mount
  useEffect(() => {
    loadAnnotations().then(setAnnotations);
    loadContourSettings().then(setContourSettings);
  }, []);

  const setRouteDraftMeters = useCallback((draftMeters: number) => {
    const rounded = Math.round(draftMeters * 10) / 10;
    const clamped = Math.max(MIN_ROUTE_DRAFT_METERS, Math.min(MAX_ROUTE_DRAFT_METERS, rounded));
    setRouteBoatProfile((prev) => (prev ? { ...prev, draftMeters: clamped } : prev));
    setBoatDraftFt(Number((clamped * FEET_PER_METER).toFixed(1)));
  }, []);

  const adjustRouteDraft = useCallback((deltaMeters: number) => {
    setRouteDraftMeters((routeBoatProfile?.draftMeters ?? 0.6) + deltaMeters);
  }, [routeBoatProfile?.draftMeters, setRouteDraftMeters]);

  const prepareRoutePlanner = useCallback(() => {
    setRouteMode(true);
    setMarineGasEnabled(true);
    setMapToolsDrawerOpen(false);
    setLayerPickerVisible(false);
    if (measureMode) {
      setMeasureMode(false);
      setMeasurePoints([]);
    }
    if (annotationMode) {
      setAnnotationMode(false);
      setArrowStart(null);
    }
    clearSelectedContour();
    setFocusedLocation(null);
    setSelectedMarkerId(null);
    setSelectedMarina(null);
    setHighlightedAccessPoints([]);
    setHighlightedAccessSummary(null);
    setContextualTips([]);
    setContextualTipsDismissed(false);
    setRouteNavActive(false);
    setRouteNavIndex(1);
    setRouteNavInfo(undefined);
    setRouteCameraMode('overview');
    setRouteTurnSheetVisible(false);
    setRoutePlannerExpanded(false);
    setRoutePlacementMode(routePoints.length > 0 ? 'stop' : 'start');
  }, [annotationMode, clearSelectedContour, measureMode, routePoints.length]);

  const openRouteBuilder = useCallback((seedPoint?: RouteLatLng, seedLabel?: string) => {
    prepareRoutePlanner();

    setRoutePoints((prev) => {
      const next = [...prev];
      if (seedPoint) {
        const last = next[next.length - 1];
        const isDuplicate =
          !!last &&
          Math.abs(last.lat - seedPoint.lat) < 1e-6 &&
          Math.abs(last.lon - seedPoint.lon) < 1e-6;
        if (!isDuplicate) {
          next.push(seedPoint);
        }
      }
      return next;
    });

    if (seedLabel) {
      setRouteName((prev) => prev || `Route to ${seedLabel}`);
    }
    setRoutePlacementMode(seedPoint ? 'end' : routePoints.length > 0 ? 'stop' : 'start');
  }, [prepareRoutePlanner, routePoints.length]);

  const appendRoutePointToPlan = useCallback((point: RouteLatLng) => {
    setRouteAlerts([]);
    setRoutePoints((prev) => {
      const last = prev[prev.length - 1];
      if (last && Math.abs(last.lat - point.lat) < 1e-6 && Math.abs(last.lon - point.lon) < 1e-6) {
        return prev;
      }

      if (prev.length === 0 && pendingRouteDestination) {
        const destination = pendingRouteDestination.point;
        const isSameAsDestination =
          Math.abs(destination.lat - point.lat) < 1e-6 &&
          Math.abs(destination.lon - point.lon) < 1e-6;
        return isSameAsDestination ? [point] : [point, destination];
      }

      return [...prev, point];
    });

    if (pendingRouteDestination) {
      setPendingRouteDestination(null);
      setRouteMode(false);
    }
  }, [pendingRouteDestination]);

  const placeRouteStart = useCallback((point: RouteLatLng) => {
    setRouteAlerts([]);
    setRouteNavActive(false);
    setRouteNavInfo(undefined);
    setRoutePoints((prev) => {
      if (pendingRouteDestination) {
        const destination = pendingRouteDestination.point;
        const isSameAsDestination =
          Math.abs(destination.lat - point.lat) < 1e-6 &&
          Math.abs(destination.lon - point.lon) < 1e-6;
        return isSameAsDestination ? [point] : [point, destination];
      }
      if (prev.length <= 1) {
        return [point];
      }
      return [point, ...prev.slice(1)];
    });
    if (pendingRouteDestination) {
      setPendingRouteDestination(null);
    }
    setRouteMode(true);
    setRoutePlacementMode('stop');
  }, [pendingRouteDestination]);

  const placeRouteStop = useCallback((point: RouteLatLng) => {
    if (pendingRouteDestination && routePoints.length === 0) {
      placeRouteStart(point);
      return;
    }
    setRouteAlerts([]);
    setRouteNavActive(false);
    setRouteNavInfo(undefined);
    setRoutePoints((prev) => {
      if (prev.length === 0) return [point];
      if (prev.length === 1) return [...prev, point];
      const last = prev[prev.length - 1];
      return [...prev.slice(0, -1), point, last];
    });
    setRouteMode(true);
    setRoutePlacementMode('stop');
  }, [pendingRouteDestination, placeRouteStart, routePoints.length]);

  const placeRouteEnd = useCallback((point: RouteLatLng, label?: string) => {
    setRouteAlerts([]);
    setRouteNavActive(false);
    setRouteNavInfo(undefined);
    if (routePoints.length === 0) {
      setPendingRouteDestination({ point, label: label ?? 'Pinned destination' });
      setRouteMode(true);
      setRoutePlacementMode('start');
      return;
    }
    setPendingRouteDestination(null);
    setRoutePoints((prev) => {
      if (prev.length === 0) return [point];
      if (prev.length === 1) return [prev[0], point];
      return [...prev.slice(0, -1), point];
    });
    setRouteMode(true);
    setRoutePlacementMode('stop');
  }, [routePoints.length]);

  const placeRoutePoint = useCallback((point: RouteLatLng, label?: string) => {
    if (routePlacementMode === 'start') {
      placeRouteStart(point);
      return;
    }
    if (routePlacementMode === 'end') {
      placeRouteEnd(point, label);
      return;
    }
    placeRouteStop(point);
  }, [placeRouteEnd, placeRouteStart, placeRouteStop, routePlacementMode]);

  const handleUndoRoutePoint = useCallback(() => {
    setRoutePoints((prev) => {
      if (prev.length <= 1) {
        setRouteNavActive(false);
        setRouteNavInfo(undefined);
        setRoutePlacementMode('start');
        return [];
      }
      return prev.slice(0, -1);
    });
  }, []);

  const handleClearRoutePlan = useCallback(() => {
    setRouteMode(false);
    setRouteNavActive(false);
    setRouteNavIndex(1);
    setRouteNavInfo(undefined);
    setRouteCameraMode('overview');
    setRoutePoints([]);
    setRouteName('');
    setPlannedRoute(null);
    setRouteMetrics(null);
    setRouteWarnings([]);
    setRouteAlerts([]);
    setRouteTurnSheetVisible(false);
    setPendingRouteDestination(null);
    setRoutePlacementMode('start');
  }, []);

  const handleSavePlannedRoute = useCallback(async () => {
    if (!plannedRoute) return;
    try {
      const saved = await saveRoute(plannedRoute, routeName || undefined);
      setRouteName(saved.name);
      Alert.alert('Route Saved', `${saved.name} is ready from the map anytime.`);
    } catch {
      Alert.alert('Could not save route', 'Try again in a moment.');
    }
  }, [plannedRoute, routeName]);

  const [currentZoom, setCurrentZoom] = useState(3.5);

  const probeRoutePoint = useCallback(async (point: RouteLatLng) => {
    if (!mapRef.current?.getPointInView || !mapRef.current?.queryRenderedFeaturesAtPoint) {
      return { onWater: true, depthM: null };
    }

    try {
      const screenPoint = await mapRef.current.getPointInView([point.lon, point.lat]);
      const waterResult = await mapRef.current.queryRenderedFeaturesAtPoint(
        screenPoint,
        undefined,
        ['water', 'water-polygon', 'waterway'],
      );
      const bathyResult = await mapRef.current.queryRenderedFeaturesAtPoint(
        screenPoint,
        undefined,
        [...getBathyLayerIds('fill'), ...getBathyLayerIds('line')],
      );
      const depth = getDepthFromRenderedFeatures(bathyResult);
      const hasBathyWater = !!bathyResult?.features?.length || depth?.depthM != null;
      return {
        onWater: !!waterResult?.features?.length || hasBathyWater,
        depthM: depth?.depthM ?? null,
      };
    } catch {
      return { onWater: true, depthM: null };
    }
  }, []);

  const probeWaterAtScreenPoint = useCallback(async (screenPoint: [number, number]) => {
    if (!mapRef.current?.getCoordinateFromView || !mapRef.current?.queryRenderedFeaturesAtPoint) {
      return null;
    }

    try {
      const coordinate = await mapRef.current.getCoordinateFromView(screenPoint);
      const waterResult = await mapRef.current.queryRenderedFeaturesAtPoint(
        screenPoint,
        undefined,
        ['water', 'water-polygon', 'waterway'],
      );

      const bathyResult = await mapRef.current.queryRenderedFeaturesAtPoint(
        screenPoint,
        undefined,
        [...getBathyLayerIds('fill'), ...getBathyLayerIds('line')],
      );
      const depth = getDepthFromRenderedFeatures(bathyResult);
      const hasBathyWater = !!bathyResult?.features?.length || depth?.depthM != null;
      if (!waterResult?.features?.length && !hasBathyWater) {
        return null;
      }

      return {
        lat: coordinate[1],
        lon: coordinate[0],
        depthFt: depth?.depthFt ?? null,
      };
    } catch {
      return null;
    }
  }, []);

  const handleAddRoutePointAtCenter = useCallback(async () => {
    const target = mapViewportCenter ?? userLocation;
    if (!target) return;
    const routeProbe = await probeRoutePoint({ lat: target.lat, lon: target.lon });
    if (!routeProbe.onWater) {
      setRouteAlerts([
        pendingRouteDestination
          ? 'Pick a start point on the water before routing to that destination.'
          : 'Route points need to be placed on the water.',
      ]);
      return;
    }
    placeRoutePoint({ lat: target.lat, lon: target.lon });
  }, [mapViewportCenter, pendingRouteDestination, placeRoutePoint, probeRoutePoint, userLocation]);

  const handlePullRouteToPoint = useCallback(async (point: RouteLatLng) => {
    if (routePoints.length < 2) return false;

    const nearest = findNearestRouteInsertionIndex(point, routePoints);
    if (!nearest || nearest.distanceMiles > 0.45) {
      return false;
    }

    const routeProbe = await probeRoutePoint(point);
    if (!routeProbe.onWater) {
      setRouteAlerts(['Route edits need to stay on the water.']);
      return true;
    }

    setRouteMode(true);
    setRouteNavActive(false);
    setRouteNavInfo(undefined);
    setRoutePlannerExpanded(false);
    setRoutePlacementMode('stop');
    setRoutePoints((prev) => {
      if (prev.length < 2) return prev;
      const safeIndex = Math.max(1, Math.min(nearest.insertIndex, prev.length - 1));
      const before = prev[safeIndex - 1];
      const after = prev[safeIndex];
      const nearlySameAsBefore =
        before &&
        haversineDistance(before.lat, before.lon, point.lat, point.lon) < 0.02;
      const nearlySameAsAfter =
        after &&
        haversineDistance(after.lat, after.lon, point.lat, point.lon) < 0.02;
      if (nearlySameAsBefore || nearlySameAsAfter) {
        return prev;
      }
      return [...prev.slice(0, safeIndex), point, ...prev.slice(safeIndex)];
    });
    setRouteAlerts(['Route pulled here. Long-press near the blue line again to fine-tune it.']);
    return true;
  }, [probeRoutePoint, routePoints]);

  const handleDragRoutePoint = useCallback((pointIndex: number, event: any) => {
    const coordinates =
      event?.geometry?.coordinates ??
      event?.nativeEvent?.payload?.geometry?.coordinates ??
      event?.nativeEvent?.geometry?.coordinates ??
      event?.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2) return;

    const [lon, lat] = coordinates as [number, number];
    void (async () => {
      const routeProbe = await probeRoutePoint({ lat, lon });
      if (!routeProbe.onWater) {
        setRouteAlerts(['Route handles need to stay on the water.']);
        return;
      }

      setRouteMode(true);
      setRouteNavActive(false);
      setRouteNavInfo(undefined);
      setRouteTurnSheetVisible(false);
      setRoutePlannerExpanded(false);
      setRoutePlacementMode('stop');
      setRoutePoints((prev) =>
        prev.map((point, index) => (index === pointIndex ? { lat, lon } : point)),
      );
      setRouteAlerts(['Route reshaped. Drag a handle again or hold the blue line to add another bend.']);
    })();
  }, [probeRoutePoint]);

  const buildLakeFishabilityField = useCallback(async (): Promise<GeoJSON.FeatureCollection | null> => {
    const fieldCenter = mapViewportCenter ?? userLocation;
    if (!fieldCenter || !mapRef.current?.getCoordinateFromView || !mapRef.current?.queryRenderedFeaturesAtPoint) {
      return null;
    }

    const cols =
      currentZoom >= 13 ? 8 :
      currentZoom >= 11 ? 7 :
      currentZoom >= 9 ? 6 : 5;
    const rows =
      currentZoom >= 13 ? 7 :
      currentZoom >= 11 ? 6 :
      currentZoom >= 9 ? 5 : 4;
    const left = 24;
    const right = SCREEN_WIDTH - 24;
    const top = 136;
    const bottomInset = markerMode === 'locations' ? Math.max(currentHeight.current + 24, 220) : 180;
    const bottom = Math.max(top + 120, SCREEN_HEIGHT - bottomInset);
    const probes: FishabilityProbe[] = [];

    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const x = left + ((col + 0.5) / cols) * (right - left);
        const y = top + ((row + 0.5) / rows) * (bottom - top);
        const sample = await probeWaterAtScreenPoint([x, y]);
        if (!sample) continue;
        probes.push({
          id: `${row}-${col}`,
          row,
          col,
          lat: sample.lat,
          lon: sample.lon,
          depthFt: sample.depthFt,
        });
      }
    }

    if (probes.length < 6) {
      return null;
    }

    const nearbyReferenceSpots = locations
      .filter((location) => haversineDistance(fieldCenter.lat, fieldCenter.lon, location.lat, location.lon) <= 12)
      .slice(0, 48)
      .map((location) => ({
        id: location.id,
        lat: location.lat,
        lon: location.lon,
        name: location.name,
      }));

    return buildLakeFishabilityGeoJSON(probes, {
      centerLat: fieldCenter.lat,
      centerLon: fieldCenter.lon,
      referenceSpots: nearbyReferenceSpots,
    });
  }, [currentZoom, locations, mapViewportCenter, markerMode, probeWaterAtScreenPoint, userLocation]);

  useEffect(() => {
    let cancelled = false;

    if (!routeBoatProfile || routePoints.length < 2) {
      setPlannedRoute(null);
      setRouteMetrics(null);
      setRouteWarnings([]);
      setRouteAlerts([]);
      if (routePoints.length < 2) {
        setRouteNavActive(false);
        setRouteNavIndex(1);
        setRouteNavInfo(undefined);
        setRouteCameraMode('overview');
      }
      return undefined;
    }

    setRouteDepthChecking(true);

    Promise.resolve()
      .then(async () => {
        const routedWaypoints = await autorouteWaypoints(
          routePoints,
          routeBoatProfile.draftMeters,
          probeRoutePoint,
        );
        const rerouted = routedWaypoints.length > routePoints.length;
        const baseRoute = createRoute(routedWaypoints, routeName || undefined);
        const baseMetrics = calculateRouteMetrics(baseRoute, routeBoatProfile);
        const seededRoute = { ...baseRoute, metrics: baseMetrics };
        if (!cancelled) {
          setPlannedRoute(seededRoute);
          setRouteMetrics(baseMetrics);
          setRouteWarnings(baseMetrics.shallowWarnings);
          setRouteAlerts(
            rerouted
              ? ['Auto-adjusted around land or shallow water where possible']
              : [],
          );
        }
        return checkRouteDepth(seededRoute, routeBoatProfile.draftMeters);
      })
      .then(({ route, warnings }) => {
        if (cancelled) return;
        const metrics = calculateRouteMetrics(route, routeBoatProfile);
        setPlannedRoute({ ...route, metrics });
        setRouteMetrics(metrics);
        setRouteWarnings(warnings);
      })
      .catch(() => {
        if (!cancelled) {
          setRouteWarnings([]);
          setRouteAlerts([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRouteDepthChecking(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [probeRoutePoint, routeBoatProfile, routeName, routePoints]);

  useEffect(() => {
    if (!(routeMode || routePoints.length > 0)) {
      setRoutePlannerExpanded(false);
    }
  }, [routeMode, routePoints.length]);

  useEffect(() => {
    if (!routeNavActive || !plannedRoute || !userLocation) {
      setRouteNavInfo(undefined);
      return;
    }

    const nextIndex = Math.min(
      Math.max(routeNavIndex, 1),
      Math.max(plannedRoute.waypoints.length - 1, 1),
    );
    const nav = getNextWaypointNav(
      { lat: userLocation.lat, lon: userLocation.lon },
      plannedRoute,
      nextIndex,
    );

    if (!nav) {
      setRouteNavInfo(undefined);
      return;
    }

    const cruiseSpeed = Math.max(routeBoatProfile?.cruiseSpeedKnots ?? 18, 0.5);
    setRouteNavInfo({
      active: true,
      nextWaypointName: plannedRoute.waypoints[nextIndex]?.label ?? `WP ${nextIndex}`,
      distanceMeters: nav.distanceNm * 1852,
      bearingDeg: nav.bearing,
      etaMinutes: (nav.distanceNm / cruiseSpeed) * 60,
    });

    if (nav.distanceNm < 0.05 && !nav.isLastWaypoint) {
      setRouteNavIndex((prev) => Math.min(prev + 1, plannedRoute.waypoints.length - 1));
    }
  }, [plannedRoute, routeBoatProfile, routeNavActive, routeNavIndex, userLocation]);

  useEffect(() => {
    if (!routeNavActive) {
      navLocationSubRef.current?.remove();
      navLocationSubRef.current = null;
      return undefined;
    }

    if (marineInstrumentData.position) {
      navLocationSubRef.current?.remove();
      navLocationSubRef.current = null;
      return undefined;
    }

    let cancelled = false;

    (async () => {
      try {
        let { status } = await Location.getForegroundPermissionsAsync();
        if (status !== 'granted') {
          const requested = await Location.requestForegroundPermissionsAsync();
          status = requested.status;
        }
        if (status !== 'granted' || cancelled) return;

        navLocationSubRef.current?.remove();
        navLocationSubRef.current = await Location.watchPositionAsync(
          {
            accuracy: Location.Accuracy.BestForNavigation,
            timeInterval: 1000,
            distanceInterval: 2,
          },
          (loc) => {
            if (cancelled) return;
            const coords = { lat: loc.coords.latitude, lon: loc.coords.longitude };
            setUserLocation(coords);
            AsyncStorage.setItem(LAST_LOCATION_KEY, JSON.stringify(coords)).catch(() => {});
            const nextHeading = loc.coords.heading ?? -1;
            if (nextHeading >= 0) {
              setUserCourseHeading(nextHeading);
            }
          },
        );
      } catch {
        // Route guidance can fall back to the last known position.
      }
    })();

    return () => {
      cancelled = true;
      navLocationSubRef.current?.remove();
      navLocationSubRef.current = null;
    };
  }, [marineInstrumentData.position, routeNavActive]);

  useEffect(() => {
    if (!ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS || !TILE_SERVER_DEPLOYED) {
      // Mark all vector layers as unavailable when tile server isn't reachable
      const allLayers = Object.values(VECTOR_OVERLAY_LAYER_BY_KEY);
      setAvailableVectorLayers(Object.fromEntries(allLayers.map((l) => [l, false])));
      return;
    }

    let cancelled = false;

    const loadVectorAvailability = async () => {
      const uniqueLayers = Array.from(new Set(Object.values(VECTOR_OVERLAY_LAYER_BY_KEY)));
      const checks = await Promise.all(
        uniqueLayers.map(async (layerName) => {
          const url = buildTileSourceUrl(layerName);
          if (!url) return [layerName, false] as const;
          try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 5000);
            const response = await fetch(url, { signal: controller.signal });
            clearTimeout(timeout);
            if (!response.ok) return [layerName, false] as const;
            const payload = await response.json();
            const hasTiles =
              Array.isArray(payload?.tiles) &&
              payload.tiles.length > 0 &&
              typeof payload.tiles[0] === 'string';
            return [layerName, hasTiles] as const;
          } catch {
            return [layerName, false] as const;
          }
        }),
      );

      if (!cancelled) {
        setAvailableVectorLayers(Object.fromEntries(checks));
      }
    };

    loadVectorAvailability();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const id = sheetHeight.addListener(({ value }) => {
      currentHeight.current = value;
    });
    return () => sheetHeight.removeListener(id);
  }, [sheetHeight]);

  // ── Track recorder subscription ─────────────────────────────────
  useEffect(() => {
    // Check for existing active track
    const existing = trackRecorder.getActiveTrack();
    if (existing) {
      setLiveTrack({ ...existing });
      if (existing.points.length >= 2) {
        setLiveTrackGeoJSON(buildSpeedColoredGeoJSON(existing.points));
      }
    }

    const unsub = trackRecorder.subscribe((track: FishingTrack | null) => {
      if (track) {
        setLiveTrack({ ...track });
        if (track.points.length >= 2) {
          setLiveTrackGeoJSON(buildSpeedColoredGeoJSON(track.points));
        }
      } else {
        // Recording stopped — keep last track visible until dismissed
        setLiveTrackGeoJSON((prev: any) => prev);
        setLiveTrack(null);
      }
    });
    return unsub;
  }, []);

  // Focused location card slide-up animation
  useEffect(() => {
    if (focusedLocation) {
      focusedCardTranslateY.setValue(60);
      focusedCardTranslateX.setValue(0);
      focusedCardOpacity.setValue(0);
      Animated.parallel([
        Animated.spring(focusedCardTranslateY, {
          toValue: 0,
          damping: 24,
          stiffness: 280,
          mass: 0.8,
          useNativeDriver: true,
        }),
        Animated.timing(focusedCardOpacity, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [focusedLocation]);

  // Focused card swipe-to-dismiss gesture (horizontal: left or right)
  const focusedCardTranslateX = useRef(new Animated.Value(0)).current;
  const focusedCardPanResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => false,
        onMoveShouldSetPanResponder: (_, gs) => Math.abs(gs.dx) > 8,
        onMoveShouldSetPanResponderCapture: (_, gs) => Math.abs(gs.dx) > 8,
        onPanResponderGrant: () => {
          focusedCardTranslateX.setValue(0);
        },
        onPanResponderMove: (_, gs) => {
          focusedCardTranslateX.setValue(gs.dx);
          focusedCardOpacity.setValue(Math.max(0, 1 - Math.abs(gs.dx) / 200));
        },
        onPanResponderRelease: (_, gs) => {
          if (Math.abs(gs.dx) > 60 || Math.abs(gs.vx) > 0.5) {
            // Dismiss — slide out in swipe direction
            const direction = gs.dx > 0 ? 1 : -1;
            Animated.parallel([
              Animated.timing(focusedCardTranslateX, {
                toValue: direction * SCREEN_WIDTH,
                duration: 200,
                useNativeDriver: true,
              }),
              Animated.timing(focusedCardOpacity, {
                toValue: 0,
                duration: 200,
                useNativeDriver: true,
              }),
            ]).start(() => {
              setFocusedLocation(null);
              setHighlightedAccessPoints([]);
              setHighlightedAccessSummary(null);
              setContextualTips([]);
              setContextualTipsDismissed(false);
              // Reset for next card
              focusedCardTranslateX.setValue(0);
            });
          } else {
            // Snap back
            Animated.parallel([
              Animated.spring(focusedCardTranslateX, {
                toValue: 0,
                damping: 24,
                stiffness: 280,
                mass: 0.8,
                useNativeDriver: true,
              }),
              Animated.timing(focusedCardOpacity, {
                toValue: 1,
                duration: 150,
                useNativeDriver: true,
              }),
            ]).start();
          }
        },
      }),
    [focusedCardTranslateX, focusedCardOpacity],
  );

  // Water body highlight — static opacity (was rAF+setState pulse causing 60fps re-renders)
  const waterHighlightOpacity = focusedLocation ? 0.5 : 0.3;

  // Recording indicator pulse animation
  useEffect(() => {
    if (liveTrack) {
      const pulse = Animated.loop(
        Animated.sequence([
          Animated.timing(recordingPulse, { toValue: 1.3, duration: 800, useNativeDriver: true }),
          Animated.timing(recordingPulse, { toValue: 1, duration: 800, useNativeDriver: true }),
        ]),
      );
      pulse.start();
      return () => pulse.stop();
    } else {
      recordingPulse.setValue(1);
    }
  }, [liveTrack !== null]);

  const animateSheetTo = useCallback((target: number) => {
    setSheetSettled(false);
    Animated.spring(sheetHeight, {
      toValue: target,
      damping: 34,
      stiffness: 240,
      mass: 0.78,
      useNativeDriver: false,
      overshootClamping: true,
      restDisplacementThreshold: 0.3,
      restSpeedThreshold: 0.3,
    }).start(() => {
      setSheetSettled(true);
    });
    currentHeight.current = target;
  }, [sheetHeight]);

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => false,
        onMoveShouldSetPanResponder: (_, gs) => Math.abs(gs.dy) > 1.5,
        onMoveShouldSetPanResponderCapture: (_, gs) => Math.abs(gs.dy) > 1.5,
        onPanResponderGrant: () => {
          dragStartHeight.current = currentHeight.current;
        },
        onPanResponderMove: (_, gs) => {
          const newHeight = dragStartHeight.current - gs.dy;
          const clamped = Math.max(SHEET_HIDDEN, Math.min(SHEET_EXPANDED, newHeight));
          sheetHeight.setValue(clamped);
        },
        onPanResponderRelease: (_, gs) => {
          // If barely moved, treat as a tap → toggle expand/collapse
          if (Math.abs(gs.dy) < 5 && Math.abs(gs.vy) < 0.1) {
            const h = currentHeight.current;
            if (h <= SHEET_COLLAPSED + 10) {
              animateSheetTo(SHEET_EXPANDED);
            } else {
              animateSheetTo(SHEET_COLLAPSED);
            }
            return;
          }
          const currentVal = currentHeight.current;
          // Use velocity for more responsive snapping
          const velocityBoost = -gs.vy * 200;
          const target = closestSnap(currentVal + velocityBoost, 0);
          animateSheetTo(target);
        },
      }),
    [sheetHeight, animateSheetTo],
  );

  // ── Restore last known location from cache (instant, no network) ──
  useEffect(() => {
    (async () => {
      try {
        const cached = await AsyncStorage.getItem(LAST_LOCATION_KEY);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (parsed?.lat && parsed?.lon) {
            setUserLocation(parsed);
          }
        }
      } catch {
        // Ignore cache read errors
      }
    })();
  }, []);

  // ── Restore last map center/zoom from cache ──
  const [initialMapCenter, setInitialMapCenter] = useState<[number, number] | null>(null);
  const [initialMapZoom, setInitialMapZoom] = useState<number | null>(null);
  useEffect(() => {
    (async () => {
      try {
        const cached = await AsyncStorage.getItem(MAP_STATE_KEY);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (parsed?.center && parsed?.zoom) {
            setInitialMapCenter(parsed.center);
            setInitialMapZoom(parsed.zoom);
          }
        }
      } catch {
        // Ignore cache read errors
      }
    })();
  }, []);

  // ── Data loading (background, non-blocking) ────────────────────
  useEffect(() => {
    let cancelled = false;

    // Backend data loads in background — map is already visible with static pins
    (async () => {
      try {
        const today = new Date().toISOString().slice(0, 10);
        const [locs, wps, bestRes] = await Promise.all([
          api.getLocations(),
          api.getWaypoints(),
          api.bestFishingV2(today, 10).catch(() => null),
        ]);
        if (!cancelled) {
          setLocations(locs);
          setWaypoints(wps);
          if (bestRes) setBestFishing(bestRes.top_locations);
        }
      } catch {
        // Backend unavailable — static pins still showing, no problem
      }
    })();

    // GPS location in background — don't block map render
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status === 'granted') {
          const loc = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.Balanced,
          });
          if (!cancelled) {
            const coords = { lat: loc.coords.latitude, lon: loc.coords.longitude };
            setUserLocation(coords);
            const nextHeading = loc.coords.heading ?? -1;
            if (nextHeading >= 0) {
              setUserCourseHeading(nextHeading);
            }
            // Cache for next launch
            AsyncStorage.setItem(LAST_LOCATION_KEY, JSON.stringify(coords)).catch(() => {});
            // Pan to user location smoothly
            if (cameraRef.current) {
              cameraRef.current.setCamera({
                centerCoordinate: [coords.lon, coords.lat],
                zoomLevel: 8,
                animationDuration: 1200,
              });
            }
          }
        }
      } catch {
        // GPS unavailable — map stays at default/cached center
      }
    })();

    return () => {
      cancelled = true;
      // Clean up ALL debounce/polling timers on unmount
      if (marinaFetchTimer.current) clearTimeout(marinaFetchTimer.current);
      if (weatherForecastDebounceRef.current) clearTimeout(weatherForecastDebounceRef.current);
      if (accessFetchTimer.current) clearTimeout(accessFetchTimer.current);
      if (mapStateSaveTimer.current) clearTimeout(mapStateSaveTimer.current);
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
      if (alertRefreshRef.current) { clearInterval(alertRefreshRef.current); alertRefreshRef.current = null; }
      navLocationSubRef.current?.remove();
      navLocationSubRef.current = null;
      cancelPendingDiscovery();
    };
  }, []);

  // ── Weather alerts fetching ────────────────────────────────────
  const fetchWeatherAlerts = useCallback(async (lat: number, lon: number) => {
    try {
      const alerts = await getFishingAlerts(lat, lon);
      setWeatherAlerts(alerts);
      if (alerts.length > 0) setAlertBannerDismissed(false);
    } catch (err) {
      console.warn('[MapScreen] Failed to fetch weather alerts:', err);
    }
  }, []);

  const mapFeatureLocation = useMemo(() => {
    if (mapFeatureLocationMode === 'map-center') {
      return mapViewportCenter ?? userLocation;
    }
    return userLocation ?? mapViewportCenter;
  }, [mapFeatureLocationMode, mapViewportCenter, userLocation]);

  // ── Pause all timers when app goes to background (saves battery) ──
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState: AppStateStatus) => {
      if (appStateRef.current.match(/active/) && nextState.match(/inactive|background/)) {
        // App going to background — clear polling timers
        if (alertRefreshRef.current) { clearInterval(alertRefreshRef.current); alertRefreshRef.current = null; }
        if (marinaFetchTimer.current) { clearTimeout(marinaFetchTimer.current); marinaFetchTimer.current = null; }
        if (weatherForecastDebounceRef.current) {
          clearTimeout(weatherForecastDebounceRef.current);
          weatherForecastDebounceRef.current = null;
        }
        if (accessFetchTimer.current) { clearTimeout(accessFetchTimer.current); accessFetchTimer.current = null; }
        if (discoveryTimer.current) { clearTimeout(discoveryTimer.current); discoveryTimer.current = null; }
        if (mapStateSaveTimer.current) { clearTimeout(mapStateSaveTimer.current); mapStateSaveTimer.current = null; }
        // Release cached location data to free memory while backgrounded
        clearLocationDataCache();
      } else if (nextState === 'active' && appStateRef.current.match(/inactive|background/)) {
        // App returning to foreground — restart alert polling
        if (mapFeatureLocation) {
          fetchWeatherAlerts(mapFeatureLocation.lat, mapFeatureLocation.lon);
          alertRefreshRef.current = setInterval(() => {
            fetchWeatherAlerts(mapFeatureLocation.lat, mapFeatureLocation.lon);
          }, ALERT_REFRESH_MS);
        }
      }
      appStateRef.current = nextState;
    });
    return () => sub.remove();
  }, [mapFeatureLocation, fetchWeatherAlerts]);

  useEffect(() => {
    if (!mapFeatureLocation) return;
    fetchWeatherAlerts(mapFeatureLocation.lat, mapFeatureLocation.lon);

    alertRefreshRef.current = setInterval(() => {
      fetchWeatherAlerts(mapFeatureLocation.lat, mapFeatureLocation.lon);
    }, ALERT_REFRESH_MS);

    return () => {
      if (alertRefreshRef.current) {
        clearInterval(alertRefreshRef.current);
        alertRefreshRef.current = null;
      }
    };
  }, [mapFeatureLocation, fetchWeatherAlerts]);

  const topAlert = weatherAlerts.length > 0 ? weatherAlerts[0] : null;
  const bannerColor = topAlert ? SEVERITY_BANNER_COLORS[topAlert.severity] : '#1976D2';

  // ── Dynamic fishing spot discovery (Overpass) ───────────────────

  const fetchDiscoveredSpots = useCallback(async (bbox: BoundingBox, zoom: number) => {
    const prev = lastDiscoveryBbox.current;
    const prevZoom = lastDiscoveryZoom.current;
    if (prev) {
      const latDiff = Math.abs(prev.south - bbox.south) + Math.abs(prev.north - bbox.north);
      const lonDiff = Math.abs(prev.west - bbox.west) + Math.abs(prev.east - bbox.east);
      const zoomTierChanged =
        (zoom <= 5) !== (prevZoom <= 5) || (zoom <= 8) !== (prevZoom <= 8);
      // Reduced threshold from 0.5 to 0.3 for better city-level panning responsiveness
      if (latDiff < 0.3 && lonDiff < 0.3 && !zoomTierChanged) return;
    }
    lastDiscoveryBbox.current = bbox;
    lastDiscoveryZoom.current = zoom;

    // Immediately show cached data (even stale) while we fetch fresh data
    const cached = getCachedSpotsForBBox(bbox, zoom);
    if (cached && cached.length > 0) {
      setDiscoveredSpots(cached);
      previousSpotsRef.current = cached;
      _cachedDiscoveredSpots = cached;
    }
    // If no cache and no previous spots, keep showing whatever we had

    setDiscoveryLoading(true);
    try {
      const spots = await discoverFishingSpots(bbox, zoom);
      // Only replace pins when we actually got results (cancel-and-replace, not cancel-and-clear)
      if (spots.length > 0) {
        setDiscoveredSpots(spots);
        previousSpotsRef.current = spots;
        _cachedDiscoveredSpots = spots;
        _cachedDiscoveryBbox = bbox;
        _cachedDiscoveryZoom = zoom;
      } else if (previousSpotsRef.current.length > 0) {
        // Empty result for this area — keep previous pins visible (stale-while-revalidate)
        setDiscoveredSpots(previousSpotsRef.current);
      }
      // Pre-fetch adjacent grid cells in the background for faster panning
      prefetchAdjacentCells(bbox, zoom);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.warn('[MapScreen] Fishing spot discovery failed:', err);
      }
      // On error/abort: keep showing previous pins, never clear to empty
      if (previousSpotsRef.current.length > 0) {
        setDiscoveredSpots(previousSpotsRef.current);
      }
    } finally {
      setDiscoveryLoading(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      cancelPendingDiscovery();
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    };
  }, []);

  const nearbyLocationContext =
    mapFeatureLocationMode === 'map-center'
      ? mapViewportCenter ?? userLocation
      : userLocation ?? mapViewportCenter;
  const visibleMapBounds = useMemo(() => {
    if (visibleContourBounds) return expandBounds(visibleContourBounds, 0.18, 0.18);
    if (mapFeatureLocation) {
      return expandBounds(
        estimateBoundsFromCenter({ lat: mapFeatureLocation.lat, lon: mapFeatureLocation.lon }, currentZoom || DEFAULT_ZOOM),
        0.18,
        0.18,
      );
    }
    return null;
  }, [currentZoom, mapFeatureLocation, visibleContourBounds]);

  // Pre-bundled top fishing spots (appear instantly on startup)
  const staticSpots = useMemo(() => getTopFishingSpots(), []);
  const gpsCatalogLocations = useMemo(() => {
    if (search.trim()) {
      return searchCatalogLocations(
        search,
        nearbyLocationContext?.lat,
        nearbyLocationContext?.lon,
        220,
      );
    }
    if (!nearbyLocationContext) return [];
    return getNearbyCatalogLocations(
      nearbyLocationContext.lat,
      nearbyLocationContext.lon,
      62,
      220,
    );
  }, [nearbyLocationContext, search]);

  // Merge backend locations + dynamically discovered spots + nearby GPS catalog spots + static spots
  const allLocations = useMemo(() => {
    // Build a coordinate lookup for de-duplication
    const coordKey = (lat: number, lon: number) =>
      `${Math.round(lat * 200)},${Math.round(lon * 200)}`; // ~500m grid
    const seen = new Set<string>();
    const result: FishingLocation[] = [];

    // 1. Backend locations take highest priority
    for (const loc of locations) {
      seen.add(loc.id);
      seen.add(coordKey(loc.lat, loc.lon));
      result.push(loc);
    }

    // 2. Discovered spots (from Overpass)
    if (discoveredSpots.length > 0) {
      const discovered = discoveredToLocations(discoveredSpots);
      for (const dl of discovered) {
        const ck = coordKey(dl.lat, dl.lon);
        if (!seen.has(dl.id) && !seen.has(ck)) {
          seen.add(dl.id);
          seen.add(ck);
          result.push(dl);
        }
      }
    }

    // 3. Nearby catalog lakes from the GPS discovery index
    for (const cl of gpsCatalogLocations) {
      const ck = coordKey(cl.lat, cl.lon);
      if (!seen.has(cl.id) && !seen.has(ck)) {
        seen.add(cl.id);
        seen.add(ck);
        result.push(cl);
      }
    }

    // 4. Static pre-bundled spots (lowest priority, fill the map on startup)
    for (const sl of staticSpots) {
      const ck = coordKey(sl.lat, sl.lon);
      if (!seen.has(sl.id) && !seen.has(ck)) {
        seen.add(sl.id);
        seen.add(ck);
        result.push(sl);
      }
    }

    return result;
  }, [locations, discoveredSpots, gpsCatalogLocations, staticSpots]);

  // ── Computed lists ──────────────────────────────────────────────

  const listLocationContext = nearbyLocationContext;

  const locationsWithDistance = useMemo(() => {
    return allLocations
      .filter(isRenderableLocation)
      .map((loc) => ({
      location: loc,
      distanceMi: listLocationContext
        ? haversineDistance(listLocationContext.lat, listLocationContext.lon, loc.lat, loc.lon)
        : null,
    }));
  }, [allLocations, listLocationContext]);
  const defaultNearbyLocations = useMemo(
    () => locationsWithDistance.filter((item) => shouldIncludeInDefaultNearbyList(item.location)),
    [locationsWithDistance],
  );

  const displayList = useMemo(() => {
    let list = search.trim() ? [...locationsWithDistance] : [...defaultNearbyLocations];
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (item) =>
          cleanLocationText(item.location.name).toLowerCase().includes(q) ||
          cleanLocationText(item.location.subtitle).toLowerCase().includes(q),
      );
    } else if (listLocationContext) {
      // When not searching, only show spots within 100 km (~62 mi) of the chosen location context
      const MAX_NEARBY_MI = 62;
      list = list.filter(
        (item) => item.distanceMi !== null && item.distanceMi <= MAX_NEARBY_MI,
      );
    }
    return list.sort((a, b) => {
      // When user location is available, weight distance more heavily
      if (a.distanceMi !== null && b.distanceMi !== null) {
        const distScoreA = Math.max(0, 100 - a.distanceMi * 1.5);
        const distScoreB = Math.max(0, 100 - b.distanceMi * 1.5);
        const scoreA = a.location.score * 0.4 + distScoreA * 0.6;
        const scoreB = b.location.score * 0.4 + distScoreB * 0.6;
        return scoreB - scoreA;
      }
      // Fallback: sort by fishing score alone when no GPS
      return b.location.score - a.location.score;
    });
  }, [defaultNearbyLocations, listLocationContext, locationsWithDistance, search]);

  const mapRenderableLocations = useMemo(() => {
    const q = search.trim().toLowerCase();
    let candidates = q
      ? allLocations.filter((loc) => cleanLocationText(loc.name).toLowerCase().includes(q))
      : allLocations.filter(isPrimaryMapLocation);

    if (!q && candidates.length === 0) {
      candidates = allLocations;
    }

    candidates = candidates.filter(isRenderableLocation);

    if (visibleMapBounds) {
      candidates = candidates.filter((loc) => pointWithinBounds(loc.lat, loc.lon, visibleMapBounds));
    }

    const mapLimit =
      q
        ? 180
        : currentZoom < 5
          ? 160
          : currentZoom < 7
            ? 260
            : currentZoom < 10
              ? 420
              : 700;

    return candidates.slice(0, mapLimit);
  }, [allLocations, currentZoom, search, visibleMapBounds]);

  const searchBarLocations = useMemo(() => {
    return allLocations
      .filter(isRenderableLocation)
      .map((loc) => ({
      id: loc.id,
      name: getPrimaryLocationLabel(loc),
      subtitle: getSecondaryLocationLabel(loc),
      distanceMi: mapFeatureLocation
        ? haversineDistance(mapFeatureLocation.lat, mapFeatureLocation.lon, loc.lat, loc.lon)
        : undefined,
      type: cleanLocationText(loc.subtitle),
    }));
  }, [allLocations, mapFeatureLocation]);

  // ── GeoJSON sources for MapLibre markers ────────────────────────

  // Main location GeoJSON — no longer depends on selectedMarkerId to avoid
  // expensive full-collection rebuilds on every pin tap.
  const locationGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: mapRenderableLocations.map((loc) => {
      const isOsm = loc.id.startsWith('osm-');
      const isStatic = isStaticSpot(loc.id);
      const band = getConditionBand(loc.score);
      return {
        type: 'Feature' as const,
        id: loc.id,
        geometry: {
          type: 'Point' as const,
          coordinates: [loc.lon, loc.lat],
        },
        properties: {
          id: loc.id,
          name: loc.name,
          score: loc.score,
          band,
          color: conditionConfig[band].color,
          displayColor: showQualityPins
            ? conditionConfig[band].color
            : isOsm ? '#3B82C4' : isStatic ? '#4A90C4' : palette.accent,
          isSelected: 0,
          isDiscovered: isOsm ? 1 : 0,
        },
        };
      }),
  }), [mapRenderableLocations, showQualityPins]);

  // Separate tiny GeoJSON just for the selected pin — rebuilds cheaply on tap
  const selectedPinGeoJSON = useMemo((): GeoJSON.FeatureCollection | null => {
    if (!selectedMarkerId) return null;
    const loc = allLocations.find((l) => l.id === selectedMarkerId);
    if (!loc) return null;
    const isOsm = loc.id.startsWith('osm-');
    const band = getConditionBand(loc.score);
    return {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature' as const,
        id: loc.id,
        geometry: { type: 'Point' as const, coordinates: [loc.lon, loc.lat] },
        properties: {
          id: loc.id,
          name: loc.name,
          displayColor: showQualityPins
            ? conditionConfig[band].color
            : isOsm ? '#3B82C4' : palette.accent,
        },
      }],
    };
  }, [selectedMarkerId, allLocations, showQualityPins]);

  const waypointGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: (search.trim()
      ? waypoints.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
      : waypoints
    ).map((wp) => ({
      type: 'Feature',
      id: wp.id,
      geometry: {
        type: 'Point',
        coordinates: [wp.lon, wp.lat],
      },
      properties: {
        id: wp.id,
        name: wp.name,
        notes: wp.notes || '',
        color: wp.color,
        icon: wp.icon,
      },
    })),
  }), [waypoints, search]);

  const filteredMarinaPOIs = useMemo(() => {
    if (poiTypeFilters.size === 0) return marinaPOIs;
    return marinaPOIs.filter((poi) => poiTypeFilters.has(poi.type));
  }, [marinaPOIs, poiTypeFilters]);

  const marinaGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: filteredMarinaPOIs.map((poi) => ({
      type: 'Feature',
      id: poi.id,
      geometry: {
        type: 'Point',
        coordinates: [poi.lon, poi.lat],
      },
      properties: {
        id: poi.id,
        name: poi.name,
        poiType: poi.type,
        phone: poi.phone ?? '',
        address: poi.address ?? '',
        amenities: formatAmenities(poi.amenities),
        distanceMiles: poi.distanceMiles?.toFixed(1) ?? '',
        color: MARINA_POI_CONFIG[poi.type]?.color ?? '#2563EB',
      },
    })),
  }), [filteredMarinaPOIs]);

  const accessGeoJSON = useMemo(
    () => accessPointsToGeoJSON(accessPoints),
    [accessPoints],
  );

  // ── Overlay raster sources (built dynamically) ──────────────────
  // Overlays are added as raster sources within the style. For simplicity
  // we render them as additional RasterSource + RasterLayer components.

  // ── Handlers ────────────────────────────────────────────────────

  // ── Marina POI fetching ──────────────────────────────────────────

  const shouldUseResolvedWaterbodyContext = useCallback((
    source: 'focused' | 'nearby',
    waterbodyType?: string | null,
    bboxDiagonalKm?: number | null,
  ) => {
    if (!waterbodyType) return false;
    if (source === 'focused') return true;
    if (waterbodyType === 'river' || waterbodyType === 'stream') return false;
    if ((bboxDiagonalKm ?? 0) > 90 && waterbodyType !== 'lake' && waterbodyType !== 'reservoir') {
      return false;
    }
    return true;
  }, []);

  const resolveNearestCatalogWaterbody = useCallback((lat: number, lon: number) => {
    if (focusedLocation) {
      const entry = getGpsLakeCatalogEntryById(focusedLocation.id);
      const bounds = getCatalogLakeBoundsForLocationId(focusedLocation.id);
      if (entry && bounds) {
        return { id: focusedLocation.id, bounds, entry, source: 'focused' as const };
      }
    }
    const candidates = getNearbyCatalogLocations(lat, lon, currentZoom < 9 ? 4 : 8, 16);
    for (const candidate of candidates) {
      const bounds = getCatalogLakeBoundsForLocationId(candidate.id);
      const entry = getGpsLakeCatalogEntryById(candidate.id);
      if (bounds && entry) {
        return { id: candidate.id, bounds, entry, source: 'nearby' as const };
      }
    }
    return null;
  }, [currentZoom, focusedLocation]);

  const fetchMarinasForCenter = useCallback(async (lat: number, lon: number) => {
    // Skip if we fetched recently for a nearby center (< 5 km)
    if (lastMarinaCenter.current) {
      const dlat = lat - lastMarinaCenter.current.lat;
      const dlon = lon - lastMarinaCenter.current.lon;
      const approxKm = Math.sqrt(dlat * dlat + dlon * dlon) * 111;
      if (approxKm < 5) return;
    }
    setMarinasLoading(true);
    try {
      const radiusMeters = currentZoom < 7 ? 45_000 : currentZoom < 10 ? 25_000 : 14_000;
      const resolvedWaterbody = resolveNearestCatalogWaterbody(lat, lon);
      const shouldUseWaterbodyContext = resolvedWaterbody
        ? shouldUseResolvedWaterbodyContext(
            resolvedWaterbody.source,
            resolvedWaterbody.entry.waterBodyType,
            resolvedWaterbody.entry.bboxDiagonalKm,
          )
        : false;
      const focusBounds = resolvedWaterbody?.bounds;
      if ((!focusBounds || !shouldUseWaterbodyContext) && !isNearCoast(lat, lon)) {
        setMarinaPOIs([]);
        lastMarinaCenter.current = { lat, lon };
        return;
      }
      const pois = await fetchNearbyMarinas(
        lat,
        lon,
        radiusMeters,
        shouldUseWaterbodyContext ? focusBounds : undefined,
        shouldUseWaterbodyContext ? resolvedWaterbody?.id : undefined,
      );
      setMarinaPOIs(pois);
      lastMarinaCenter.current = { lat, lon };
    } catch {
      // Silently fail — cache may still serve stale data
    } finally {
      setMarinasLoading(false);
    }
  }, [currentZoom, resolveNearestCatalogWaterbody, shouldUseResolvedWaterbodyContext]);

  // When marinas are toggled on, fetch for current map center
  useEffect(() => {
    if (!marinasEnabled) {
      setMarinaPOIs([]);
      setSelectedMarina(null);
      lastMarinaCenter.current = null;
      return;
    }
    // Use user location as initial center or default
    const center = mapFeatureLocation ?? { lat: DEFAULT_CENTER[1], lon: DEFAULT_CENTER[0] };
    fetchMarinasForCenter(center.lat, center.lon);
  }, [marinasEnabled, mapFeatureLocation, fetchMarinasForCenter]);

  // ── Catch photo overlay data fetching ──────────────────────────
  useEffect(() => {
    if (!photosEnabled) {
      setCatchPhotos([]);
      setPhotosInView(0);
      setSelectedCatchPhoto(null);
      return;
    }
    setCatchPhotosLoading(true);
    getCatchesWithPhotos()
      .then((photos) => {
        setCatchPhotos(photos);
        setPhotosInView(photos.length);
      })
      .catch(() => {})
      .finally(() => setCatchPhotosLoading(false));
  }, [photosEnabled]);

  // Schedule fishing time notification when we have user location
  useEffect(() => {
    if (userLocation) {
      scheduleBestTimeNotification(userLocation.lat, userLocation.lon).catch(() => {});
    }
  }, [userLocation]);

  useEffect(() => {
    if (!mapViewportCenter && userLocation) {
      setMapViewportCenter(userLocation);
    }
  }, [mapViewportCenter, userLocation]);

  useEffect(() => {
    if (!biteTimeOverlayEnabled) {
      setBiteFieldGeoJSON(null);
      return;
    }

    let cancelled = false;
    const timeout = setTimeout(() => {
      setBiteLoading(true);
      buildLakeFishabilityField()
        .then((geoJSON) => {
          if (!cancelled) {
            setBiteFieldGeoJSON(geoJSON);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setBiteFieldGeoJSON(null);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setBiteLoading(false);
          }
        });
    }, 260);

    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [biteTimeOverlayEnabled, buildLakeFishabilityField, mapViewportCenter, currentZoom]);

  const loadMapWeatherForecast = useCallback(async (lat: number, lon: number) => {
    setMapWeatherLoading(true);
    try {
      const forecast = await getMapWeatherForecast(lat, lon);
      setMapWeatherForecast(forecast);
    } catch {
      // Keep the previous forecast if a refresh fails.
    } finally {
      setMapWeatherLoading(false);
    }
  }, []);

  const loadMapTideSummary = useCallback(async (lat: number, lon: number) => {
    try {
      const station = await getUnifiedNearestTideStation(lat, lon);
      const [predictions, waterLevels, hourly] = await Promise.all([
        getUnifiedTidePredictions(station.id, 2),
        getUnifiedWaterLevel(station.id).catch(() => null),
        getUnifiedTideHourly(station.id).catch(() => []),
      ]);
      const next = getNextTide(predictions);
      const latestWaterLevel = waterLevels?.[0] ?? null;
      const depthAdjustmentFt = latestWaterLevel?.heightFt ?? hourly?.[0]?.heightFt ?? null;
      const secondary = latestWaterLevel
        ? `${latestWaterLevel.heightFt >= 0 ? '+' : ''}${latestWaterLevel.heightFt.toFixed(1)} ft now`
        : `${Math.max(1, Math.round(station.distanceKm))} km away`;

      setMapTideSummary({
        stationName: station.name,
        primary: next ? `${next.prediction.label} in ${next.timeRemaining}` : 'Tide forecast ready',
        secondary,
        currentLevelFt: latestWaterLevel?.heightFt ?? null,
        depthAdjustmentFt,
        trendPoints: hourly.slice(0, 5).map((point, index) => ({
          label:
            index === 0
              ? 'Now'
              : new Date(point.time).toLocaleTimeString([], { hour: 'numeric' }).replace(':00', ''),
          heightFt: point.heightFt,
        })),
      });
    } catch {
      setMapTideSummary(null);
    }
  }, []);

  useEffect(() => {
    const wantsConditionsPanel = windEnabled || radarEnabled || dynamicDepthsEnabled;
    if (!wantsConditionsPanel) {
      if (weatherForecastDebounceRef.current) clearTimeout(weatherForecastDebounceRef.current);
      setMapWeatherLoading(false);
      setMapTideSummary(null);
      return;
    }

    const center = mapFeatureLocation ?? { lat: DEFAULT_CENTER[1], lon: DEFAULT_CENTER[0] };
    if (weatherForecastDebounceRef.current) clearTimeout(weatherForecastDebounceRef.current);
    weatherForecastDebounceRef.current = setTimeout(() => {
      loadMapWeatherForecast(center.lat, center.lon);
      if (isNearCoast(center.lat, center.lon)) {
        loadMapTideSummary(center.lat, center.lon);
      } else {
        setMapTideSummary(null);
      }
    }, 900);

    return () => {
      if (weatherForecastDebounceRef.current) clearTimeout(weatherForecastDebounceRef.current);
    };
  }, [dynamicDepthsEnabled, loadMapTideSummary, loadMapWeatherForecast, mapFeatureLocation, radarEnabled, windEnabled]);

  useEffect(() => {
    if (!windEnabled) {
      setWindVectorCount(0);
    }
  }, [windEnabled]);

  // ── Precipitation radar data fetching ─────────────────────────────
  useEffect(() => {
    if (!radarEnabled) {
      setRadarTileUrl(null);
      setRadarFrames([]);
      setRadarFrameIndex(0);
      setRadarPlaying(false);
      if (radarAnimRef.current) { clearInterval(radarAnimRef.current); radarAnimRef.current = null; }
      return;
    }

    setRadarLoading(true);
    getRadarFrames(10).then(({ frames, nowcast }) => {
      const allFrames = [...frames, ...nowcast];
      setRadarFrames(allFrames);
      if (allFrames.length > 0) {
        // Start at last observed frame (before nowcast)
        const startIdx = Math.max(0, frames.length - 1);
        setRadarFrameIndex(startIdx);
        setRadarTileUrl(allFrames[startIdx].tileUrl);
      }
    }).catch((err) => {
      console.warn('[OpenCatch] Radar data error:', err);
    }).finally(() => {
      setRadarLoading(false);
    });
  }, [radarEnabled]);

  // Radar animation loop
  useEffect(() => {
    if (!radarPlaying || radarFrames.length === 0) {
      if (radarAnimRef.current) { clearInterval(radarAnimRef.current); radarAnimRef.current = null; }
      return;
    }

    radarAnimRef.current = setInterval(() => {
      setRadarFrameIndex((prev) => {
        const next = (prev + 1) % radarFrames.length;
        setRadarTileUrl(radarFrames[next].tileUrl);
        return next;
      });
    }, 500);

    return () => {
      if (radarAnimRef.current) { clearInterval(radarAnimRef.current); radarAnimRef.current = null; }
    };
  }, [radarPlaying, radarFrames]);

  // ── Storm cell data fetching ──────────────────────────────────────
  useEffect(() => {
    if (!mapFeatureLocation) return;
    // Fetch storm cells for a wide area around the user
    const lat = mapFeatureLocation.lat;
    const lon = mapFeatureLocation.lon;
    const dLat = 3; // ~200 mile radius
    const dLon = 3 / Math.cos((lat * Math.PI) / 180);
    const bbox: [number, number, number, number] = [lat - dLat, lon - dLon, lat + dLat, lon + dLon];

    getActiveStormCells(bbox, lat, lon).then((cells) => {
      setStormCells(cells);
      if (cells.length > 0) {
        setStormGeoJSON(stormCellsToGeoJSON(cells));
      } else {
        setStormGeoJSON(null);
      }
    }).catch(() => {
      // Silent fail — storms are supplementary
    });
  }, [mapFeatureLocation]);

  // ── Access points + trails data fetching ─────────────────────────

  const fetchAccessForCenter = useCallback(async (lat: number, lon: number) => {
    if (lastAccessCenter.current) {
      const dlat = lat - lastAccessCenter.current.lat;
      const dlon = lon - lastAccessCenter.current.lon;
      const approxKm = Math.sqrt(dlat * dlat + dlon * dlon) * 111;
      if (approxKm < 5) return;
    }
    setAccessLoading(true);
    try {
      const accessRadius = currentZoom < 8 ? 22_000 : currentZoom < 11 ? 15_000 : 9_000;
      const trailRadius = currentZoom < 8 ? 16_000 : currentZoom < 11 ? 10_000 : 6_000;
      const resolvedWaterbody = resolveNearestCatalogWaterbody(lat, lon);
      const shouldUseWaterbodyContext = resolvedWaterbody
        ? shouldUseResolvedWaterbodyContext(
            resolvedWaterbody.source,
            resolvedWaterbody.entry.waterBodyType,
            resolvedWaterbody.entry.bboxDiagonalKm,
          )
        : false;
      const focusBounds = resolvedWaterbody?.bounds;
      if (!focusBounds || !shouldUseWaterbodyContext) {
        setAccessPoints([]);
        setAccessTrailGeoJSON(null);
        lastAccessCenter.current = { lat, lon };
        return;
      }
      const [points, trails] = await Promise.all([
        fetchNearbyAccessPoints(lat, lon, accessRadius, focusBounds, resolvedWaterbody.id),
        fetchNearbyTrails(lat, lon, trailRadius),
      ]);
      setAccessPoints(points);
      setAccessTrailGeoJSON(trailsToGeoJSON(trails));
      lastAccessCenter.current = { lat, lon };
    } catch {
      // Silently fail
    } finally {
      setAccessLoading(false);
    }
  }, [currentZoom, resolveNearestCatalogWaterbody, shouldUseResolvedWaterbodyContext]);

  useEffect(() => {
    if (!accessEnabled) {
      setAccessPoints([]);
      setAccessTrailGeoJSON(null);
      lastAccessCenter.current = null;
      return;
    }
    const center = mapFeatureLocation ?? { lat: DEFAULT_CENTER[1], lon: DEFAULT_CENTER[0] };
    fetchAccessForCenter(center.lat, center.lon);
  }, [accessEnabled, mapFeatureLocation, fetchAccessForCenter]);

  // Ref to debounce map state persistence
  const mapStateSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Frame-skip counter — only process heavy work every 3rd region-change event */
  const regionChangeCounter = useRef(0);
  const updateVisibleContourBounds = useCallback((nextBounds: ContourSourceBounds | null) => {
    if (!nextBounds) return;
    if (!boundsChangedEnough(lastContourBoundsRef.current, nextBounds)) return;
    lastContourBoundsRef.current = nextBounds;
    setVisibleContourBounds(nextBounds);
  }, []);

  const handleRegionChange = useCallback((feature: any) => {
    // Always update zoom/bearing (lightweight state, needed for UI)
    const zoom = feature?.properties?.zoomLevel;
    if (zoom !== undefined) setCurrentZoom(zoom);
    const bearing = feature?.properties?.heading ?? feature?.properties?.bearing;
    if (bearing !== undefined) setCompassHeading(bearing);
    if (feature?.geometry?.coordinates) {
      const [lon, lat] = feature.geometry.coordinates as [number, number];
      setMapViewportCenter((prev) => {
        if (prev && Math.abs(prev.lat - lat) < 0.015 && Math.abs(prev.lon - lon) < 0.015) {
          return prev;
        }
        return { lat, lon };
      });
    }
    if (feature?.properties?.visibleBounds) {
      updateVisibleContourBounds(toContourBounds(feature.properties.visibleBounds));
    } else if (feature?.geometry?.coordinates && zoom !== undefined) {
      const [lon, lat] = feature.geometry.coordinates as [number, number];
      updateVisibleContourBounds(estimateBoundsFromCenter({ lat, lon }, zoom));
    }

    // Frame-skip: only run heavy work (network fetches, persistence) every 3rd call
    regionChangeCounter.current += 1;
    if (regionChangeCounter.current % 3 !== 0) return;

    // Persist map center/zoom to AsyncStorage (debounced)
    if (feature?.geometry?.coordinates && zoom !== undefined) {
      if (mapStateSaveTimer.current) clearTimeout(mapStateSaveTimer.current);
      mapStateSaveTimer.current = setTimeout(() => {
        AsyncStorage.setItem(MAP_STATE_KEY, JSON.stringify({
          center: feature.geometry.coordinates,
          zoom,
        })).catch(() => {});
      }, 2000);
    }

    // Debounced marina re-fetch on pan
    if (marinasEnabled && feature?.geometry?.coordinates) {
      const [lng, lat] = feature.geometry.coordinates;
      if (marinaFetchTimer.current) clearTimeout(marinaFetchTimer.current);
      marinaFetchTimer.current = setTimeout(() => {
        fetchMarinasForCenter(lat, lng);
      }, 2000);
    }

    // Debounced access-point re-fetch on pan
    if (accessEnabled && feature?.geometry?.coordinates) {
      const [aLon, aLat] = feature.geometry.coordinates;
      if (accessFetchTimer.current) clearTimeout(accessFetchTimer.current);
      accessFetchTimer.current = setTimeout(() => {
        fetchAccessForCenter(aLat, aLon);
      }, 2000);
    }

    // Debounced fishing spot discovery on pan/zoom (deferred 3s after mount)
    if (feature?.properties?.visibleBounds || feature?.geometry?.coordinates) {
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
      const msSinceMountVal = Date.now() - mountTimeRef.current;
      const discoveryDelay = msSinceMountVal < 3000 ? 3000 - msSinceMountVal + 500 : 600;
      discoveryTimer.current = setTimeout(async () => {
        const z = feature?.properties?.zoomLevel ?? currentZoom;
        // Try to get visible bounds from the map ref
        try {
          const bounds = await mapRef.current?.getVisibleBounds();
          if (bounds) {
            updateVisibleContourBounds(toContourBounds(bounds));
            // bounds = [[ne_lon, ne_lat], [sw_lon, sw_lat]]
            const bbox: BoundingBox = {
              south: bounds[1][1],
              west: bounds[1][0],
              north: bounds[0][1],
              east: bounds[0][0],
            };
            fetchDiscoveredSpots(bbox, z);
          } else if (feature?.geometry?.coordinates) {
            // Fallback: estimate bbox from center + zoom
            const [cLon, cLat] = feature.geometry.coordinates;
            const span = 180 / Math.pow(2, z); // approximate degree span
            const bbox: BoundingBox = {
              south: cLat - span / 2,
              west: cLon - span,
              north: cLat + span / 2,
              east: cLon + span,
            };
            fetchDiscoveredSpots(bbox, z);
          }
        } catch {
          // getVisibleBounds not available — use center estimate
          if (feature?.geometry?.coordinates) {
            const [cLon, cLat] = feature.geometry.coordinates;
            const span = 180 / Math.pow(2, z);
            updateVisibleContourBounds({
              minLat: cLat - span / 2,
              minLon: cLon - span,
              maxLat: cLat + span / 2,
              maxLon: cLon + span,
            });
            fetchDiscoveredSpots({
              south: cLat - span / 2,
              west: cLon - span,
              north: cLat + span / 2,
              east: cLon + span,
            }, z);
          }
        }
      }, discoveryDelay);
    }
  }, [accessEnabled, currentZoom, fetchAccessForCenter, fetchDiscoveredSpots, fetchMarinasForCenter, marinasEnabled, updateVisibleContourBounds]);

  // Compass mode toggle: static (north-up) vs heading (map follows device bearing)
  const handleToggleCompassMode = useCallback(() => {
    if (routeNavActive && routeCameraMode !== 'overview') {
      return;
    }
    setCompassMode((prev) => {
      const next = prev === 'static' ? 'heading' : 'static';
      if (next === 'static' && cameraRef.current) {
        cameraRef.current.setCamera({ heading: 0, animationDuration: 500 });
        setCompassHeading(0);
      }
      return next;
    });
  }, [routeCameraMode, routeNavActive]);

  // In heading mode, use expo-location heading if available
  useEffect(() => {
    if (routeNavActive && routeCameraMode !== 'overview') return;
    if (marineInstrumentData.heading) return;
    if (compassMode !== 'heading') return;
    let sub: Location.LocationSubscription | null = null;
    (async () => {
      try {
        sub = await Location.watchHeadingAsync((h) => {
          const deg = h.trueHeading >= 0 ? h.trueHeading : h.magHeading;
          setCompassHeading(deg);
          if (cameraRef.current) {
            cameraRef.current.setCamera({ heading: deg, animationDuration: 150 });
          }
        });
      } catch {
        // Heading not available — fall back to map camera bearing via handleRegionChange
      }
    })();
    return () => { sub?.remove(); };
  }, [compassMode, marineInstrumentData.heading, routeCameraMode, routeNavActive]);

  useEffect(() => {
    if (!marineInstrumentData.heading || compassMode !== 'heading') return;
    if (routeNavActive && routeCameraMode !== 'overview') return;
    const deg = marineInstrumentData.heading.headingDeg;
    setCompassHeading(deg);
    cameraRef.current?.setCamera({ heading: deg, animationDuration: 150 });
  }, [
    compassMode,
    marineInstrumentData.heading,
    routeCameraMode,
    routeNavActive,
  ]);

  useEffect(() => {
    if (!routeNavActive) {
      cameraRef.current?.setCamera({ pitch: 0, animationDuration: 350 });
      return;
    }
    if (routeCameraMode === 'overview') {
      cameraRef.current?.setCamera({ pitch: 0, heading: 0, animationDuration: 350 });
    }
  }, [routeCameraMode, routeNavActive]);

  useEffect(() => {
    if (!routeNavActive || routeCameraMode === 'overview' || !userLocation) return;

    const heading =
      userCourseHeading > 0
        ? userCourseHeading
        : routeNavInfo?.bearingDeg ?? 0;

    cameraRef.current?.setCamera({
      centerCoordinate: [userLocation.lon, userLocation.lat],
      zoomLevel: routeCameraMode === 'follow-3d' ? 15.9 : 14.9,
      pitch: routeCameraMode === 'follow-3d' ? 54 : 14,
      heading,
      animationDuration: 550,
    });
  }, [
    routeCameraMode,
    routeNavActive,
    routeNavInfo?.bearingDeg,
    userCourseHeading,
    userLocation?.lat,
    userLocation?.lon,
  ]);

  useEffect(() => {
    if (!windEnabled && !radarEnabled && !dynamicDepthsEnabled) {
      setMapWeatherPanelDismissed(false);
    }
  }, [dynamicDepthsEnabled, radarEnabled, windEnabled]);

  useEffect(() => {
    if (routeMode || routePoints.length > 0) {
      setMarineGasEnabled(true);
    }
  }, [routeMode, routePoints.length]);

  const findLocationBySearchText = useCallback((query: string): FishingLocation | null => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) return null;
    const exact = allLocations.find((loc) => loc.name.trim().toLowerCase() === trimmed);
    if (exact) return exact;
    const startsWith = allLocations.find((loc) => loc.name.trim().toLowerCase().startsWith(trimmed));
    if (startsWith) return startsWith;
    const includes = allLocations.find((loc) => loc.name.toLowerCase().includes(trimmed));
    return includes ?? null;
  }, [allLocations]);

  const flyToLocation = useCallback((loc: FishingLocation, zoomLevel: number = 13) => {
    cameraRef.current?.setCamera({
      centerCoordinate: [loc.lon, loc.lat],
      zoomLevel,
      animationDuration: 800,
    });
  }, []);

  // Handle tapping a search result — fly to the location and focus it
  const handleSearchSelectLocation = useCallback((locationId: string) => {
    const loc = allLocations.find((l) => l.id === locationId);
    if (!loc) return;
    clearSelectedContour();
    setFocusedLocation(loc);
    setSelectedMarkerId(loc.id);
    cacheLocationDetail(loc);
    setSearch(loc.name);
    flyToLocation(loc);
  }, [allLocations, clearSelectedContour, flyToLocation]);

  const startRouteToTarget = useCallback(async (loc: FishingLocation) => {
    const targetPoint = { lat: loc.lat, lon: loc.lon };
    const startCandidates = [
      userLocation ? { lat: userLocation.lat, lon: userLocation.lon } : null,
      mapViewportCenter ? { lat: mapViewportCenter.lat, lon: mapViewportCenter.lon } : null,
    ].filter(Boolean) as RouteLatLng[];

    let seededStart: RouteLatLng | null = null;
    for (const candidate of startCandidates) {
      const result = await probeRoutePoint(candidate);
      if (result.onWater) {
        seededStart = candidate;
        break;
      }
    }

    prepareRoutePlanner();
    setSearch(loc.name);
    flyToLocation(loc);
    setRouteName(`Route to ${loc.name}`);

    if (seededStart) {
      setPendingRouteDestination(null);
      setRoutePlacementMode('stop');
      setRouteMode(false);
      setRoutePoints([seededStart, targetPoint]);
      setRouteAlerts(['Depth-aware autoroute is checking channels and safe water.']);
      return;
    }

    setRoutePoints([]);
    setPendingRouteDestination({ point: targetPoint, label: loc.name });
    setRoutePlacementMode('start');
    setRouteAlerts(['Pick a starting point on the water, then use Set A or tap the map.']);
  }, [flyToLocation, mapViewportCenter, prepareRoutePlanner, probeRoutePoint, userLocation]);

  const handleStartRouteToLocation = useCallback((locationId: string) => {
    const loc = allLocations.find((l) => l.id === locationId);
    if (!loc) {
      openRouteBuilder();
      return;
    }
    void startRouteToTarget(loc);
  }, [allLocations, openRouteBuilder, startRouteToTarget]);

  const handleSearchSubmit = useCallback((text: string) => {
    const match = findLocationBySearchText(text);
    if (!match) return;
    handleSearchSelectLocation(match.id);
  }, [findLocationBySearchText, handleSearchSelectLocation]);

  const handleStartRouteFromSearch = useCallback(() => {
    const target = focusedLocation ?? findLocationBySearchText(search);
    if (target) {
      void startRouteToTarget(target);
      return;
    }
    setPendingRouteDestination(null);
    setRoutePlacementMode('start');
    openRouteBuilder();
  }, [findLocationBySearchText, focusedLocation, openRouteBuilder, search, startRouteToTarget]);

  const visibleLocations = useMemo(() => {
    return search.trim()
      ? allLocations.filter(
          (l) => l.name.toLowerCase().includes(search.toLowerCase()),
        )
      : allLocations;
  }, [allLocations, search]);

  const filteredLocations = visibleLocations;

  const filteredWaypoints = search.trim()
    ? waypoints.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
    : waypoints;

  const handleMarkerPress = useCallback(
    (location: FishingLocation) => {
      clearSelectedContour();
      setSelectedMarkerId(location.id);
      setFocusedLocation(location);
      // Pre-cache for faster LocationDetailScreen load
      cacheLocationDetail(location);
      cameraRef.current?.setCamera({
        centerCoordinate: [location.lon, location.lat],
        zoomLevel: Math.max(currentZoom, 12),
        animationDuration: 500,
      });
      animateSheetTo(SHEET_HIDDEN);

      // Feature 2: Auto-fetch nearby access points on spot click
      setHighlightedAccessLoading(true);
      setHighlightedAccessPoints([]);
      setHighlightedAccessSummary(null);
      setRiverSpots([]);
      fetchNearbyAccessPoints(
        location.lat,
        location.lon,
        5_000,
        getCatalogLakeBoundsForLocationId(location.id),
        location.id,
      ).then((pts) => { // min 5km enforced in service
        setHighlightedAccessPoints(pts);
        setHighlightedAccessSummary(computeAccessSummary(pts));
        setHighlightedAccessLoading(false);

        // For river-type water bodies, create sub-spots at each significant access point
        const wbType = inferWaterbodyType(location.name);
        if (wbType === 'river' || wbType === 'creek' || wbType === 'stream') {
          const significantTypes = ['boat_launch', 'kayak_launch', 'shore_fishing', 'fishing_pier', 'trailhead'];
          const spots = pts
            .filter((ap) => significantTypes.includes(ap.type))
            .map((ap) => ({
              id: `${location.id}-spot-${ap.id}`,
              name: ap.name !== ACCESS_POINT_CONFIG[ap.type].label
                ? `${location.name} \u2014 ${ap.name}`
                : `${location.name} \u2014 ${ACCESS_POINT_CONFIG[ap.type].label}`,
              lat: ap.lat,
              lon: ap.lon,
              apType: ap.type,
            }));
          setRiverSpots(spots);
        }
      }).catch(() => {
        setHighlightedAccessLoading(false);
      });

      // Feature 1: Generate contextual tips for this water body
      const species = location.speciesActivity?.map((s) => s.species) ?? [];
      const waterbody: WaterbodyContext = {
        name: location.name,
        type: inferWaterbodyType(location.name),
        lat: location.lat,
        lon: location.lon,
        species,
      };
      const wxConditions: WeatherConditions = {
        airTemp: location.conditions.airTemp,
        waterTemp: location.conditions.waterTemp,
        windSpeed: location.conditions.windSpeed,
        windDirection: location.conditions.windDirection,
        pressure: location.conditions.pressure,
        pressureTrend: location.conditions.pressureTrend,
        weather: location.conditions.weather,
        humidity: location.conditions.humidity,
        moonPhase: location.conditions.moonPhase,
        sunrise: location.conditions.sunrise,
        sunset: location.conditions.sunset,
      };
      const tips = getTipsForWaterbody(waterbody, wxConditions);
      setContextualTips(tips);
      setContextualWaterbodyName(location.name);
      setContextualTipsDismissed(false);
    },
    [animateSheetTo, clearSelectedContour, currentZoom],
  );

  const handleCardPress = useCallback(
    (loc: FishingLocation) => {
      handleMarkerPress(loc);
    },
    [handleMarkerPress],
  );

  const handleLocationSourcePress = useCallback(
    async (event: any) => {
      const feature = event?.features?.[0];
      if (!feature) return;

      const props = feature.properties ?? {};
      if (props.cluster) {
        try {
          const zoom = await locationSourceRef.current?.getClusterExpansionZoom(feature);
          const coords = feature.geometry?.coordinates as [number, number] | undefined;
          if (coords) {
            cameraRef.current?.setCamera({
              centerCoordinate: coords,
              zoomLevel: Math.max(zoom ?? currentZoom + 1, currentZoom + 1),
              animationDuration: 350,
            });
          }
        } catch {
          // Ignore cluster expansion failures.
        }
        return;
      }

      // Use the feature's properties.id (always a string like "osm-r12345")
      // feature.id may be coerced or lost by MapLibre, so prefer properties.
      const locationId = String(props.id ?? feature.id ?? '');
      if (!locationId) return;

      // Exact match by ID — never fall back to proximity
      const location = allLocations.find((loc) => loc.id === locationId);
      if (location) {
        if (routeMode && !routeNavActive) {
          placeRoutePoint({ lat: location.lat, lon: location.lon }, location.name);
          return;
        }
        handleMarkerPress(location);
      }
    },
    [allLocations, currentZoom, handleMarkerPress, placeRoutePoint, routeMode, routeNavActive],
  );

  const handleMapLongPress = useCallback((event: any) => {
    const coords = event.geometry?.coordinates;
    if (coords) {
      if ((routeMode || routePoints.length > 0) && !routeNavActive) {
        void (async () => {
          const pulled = await handlePullRouteToPoint({
            lat: coords[1],
            lon: coords[0],
          });
          if (!pulled) {
            const screenPoint = event.properties?.screenPointX != null
              ? { screenX: event.properties.screenPointX, screenY: event.properties.screenPointY }
              : { screenX: SCREEN_WIDTH / 2, screenY: SCREEN_HEIGHT / 3 };
            setLongPressCoord({
              latitude: coords[1],
              longitude: coords[0],
              ...screenPoint,
            });
            setLongPressMenuVisible(true);
          }
        })();
        return;
      }

      // MapLibre gives [lng, lat]; we store as {latitude, longitude}
      // Show context menu instead of directly opening waypoint modal
      const screenPoint = event.properties?.screenPointX != null
        ? { screenX: event.properties.screenPointX, screenY: event.properties.screenPointY }
        : { screenX: SCREEN_WIDTH / 2, screenY: SCREEN_HEIGHT / 3 };
      setLongPressCoord({
        latitude: coords[1],
        longitude: coords[0],
        ...screenPoint,
      });
      setLongPressMenuVisible(true);
    }
  }, [handlePullRouteToPoint, routeMode, routeNavActive, routePoints.length]);

  const handleLongPressAction = useCallback((action: LongPressAction, coordinate: LongPressCoordinate) => {
    switch (action) {
      case 'drop-pin':
        setPendingCoord({ latitude: coordinate.latitude, longitude: coordinate.longitude });
        setShowWaypointModal(true);
        break;
      case 'get-conditions':
        navigation.navigate('WaterInsights', {
          lat: coordinate.latitude,
          lon: coordinate.longitude,
        });
        break;
      case 'add-to-route':
        openRouteBuilder(
          { lat: coordinate.latitude, lon: coordinate.longitude },
          'Pinned Route Point',
        );
        break;
      case 'whats-here':
        // Fly to location and enable marinas/access points
        cameraRef.current?.setCamera({
          centerCoordinate: [coordinate.longitude, coordinate.latitude],
          zoomLevel: 14,
          animationDuration: 600,
        });
        if (!marinasEnabled) setMarinasEnabled(true);
        if (!accessEnabled) setAccessEnabled(true);
        break;
      case 'measure-distance':
        setMeasureMode(true);
        setMeasurePoints([[coordinate.latitude, coordinate.longitude]]);
        setMapToolsDrawerOpen(false);
        break;
    }
    setLongPressMenuVisible(false);
  }, [accessEnabled, marinasEnabled, navigation, openRouteBuilder]);

  const handleToggleLayerPicker = useCallback(() => {
    setLayerPickerVisible((prev) => !prev);
  }, []);

  /** Toggle a POI type filter and auto-enable the marinas layer */
  const togglePOIType = useCallback((type: MarinaPOIType) => {
    setPOITypeFilters((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
    // Auto-enable marina fetching when a filter is toggled on
    if (!marinasEnabled) setMarinasEnabled(true);
  }, [marinasEnabled]);

  const handleToggleOverlay = useCallback((key: string) => {
    setActiveOverlays((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
        if (key === 'local-bathymetry') {
          clearSelectedContour();
        }
      } else {
        next.add(key);
      }
      return next;
    });
  }, [clearSelectedContour]);

  const handleSaveWaypoint = useCallback(
    async (draft: Omit<Waypoint, 'id' | 'createdAt' | 'catches'>) => {
      setShowWaypointModal(false);
      setPendingCoord(null);
      const saved = await api.saveWaypoint(draft);
      setWaypoints((prev) => [...prev, saved]);
      setMarkerMode('waypoints');
    },
    [],
  );

  const handleCancelWaypoint = useCallback(() => {
    setShowWaypointModal(false);
    setPendingCoord(null);
  }, []);

  const handleHandleTap = useCallback(() => {
    const h = currentHeight.current;
    if (h <= SHEET_HIDDEN + 10) {
      animateSheetTo(SHEET_COLLAPSED);
    } else if (h <= SHEET_PEEK + 10) {
      animateSheetTo(SHEET_COLLAPSED);
    } else if (h <= SHEET_COLLAPSED + 10) {
      animateSheetTo(SHEET_EXPANDED);
    } else {
      animateSheetTo(SHEET_HIDDEN);
    }
  }, [animateSheetTo]);

  // ── Annotation save helper ────────────────────────────────────────
  const handleSaveAnnotation = useCallback(async (
    partial: Omit<MapAnnotation, 'id' | 'createdAt' | 'updatedAt'>,
  ) => {
    const saved = await saveAnnotation(partial);
    setAnnotations((prev) => [...prev, saved]);
  }, []);

  const handleMapPress = useCallback((event?: any) => {
    if (routeMode && event?.geometry?.coordinates) {
      const [lng, lat] = event.geometry.coordinates as [number, number];
      const screenPoint = event.properties?.screenPointX != null
        ? [event.properties.screenPointX, event.properties.screenPointY]
        : event.point?.x != null
          ? [event.point.x, event.point.y]
          : null;

      (async () => {
        const waterPoint = screenPoint
          ? await probeWaterAtScreenPoint(screenPoint as [number, number])
          : null;
        if (screenPoint && !waterPoint) {
          setRouteAlerts([
            pendingRouteDestination
              ? 'Pick a starting point on the water before routing to that destination.'
              : 'Route points need to be placed on the water.',
          ]);
          return;
        }

        const candidatePoint = waterPoint
          ? { lat: waterPoint.lat, lon: waterPoint.lon }
          : { lat, lon: lng };
        const fallbackProbe = !waterPoint ? await probeRoutePoint(candidatePoint) : { onWater: true, depthM: null };
        if (!fallbackProbe.onWater) {
          setRouteAlerts([
            pendingRouteDestination
              ? 'Pick a starting point on the water before routing to that destination.'
              : 'Route points need to be placed on the water.',
          ]);
          return;
        }
        setRouteAlerts([]);
        placeRoutePoint(candidatePoint);
      })();
      return;
    }

    if (measureMode && event?.geometry?.coordinates) {
      const [lng, lat] = event.geometry.coordinates as [number, number];
      setMeasurePoints((prev) => [...prev, [lat, lng]]);
      return;
    }

    // ── Annotation mode tap handling ──────────────────────────────
    if (annotationMode && event?.geometry?.coordinates) {
      const [lng, lat] = event.geometry.coordinates as [number, number];
      const coord: AnnotationCoordinate = { latitude: lat, longitude: lng };

      if (annotationTool === 'marker') {
        handleSaveAnnotation({
          type: 'marker',
          coordinate: coord,
          label: '',
          color: annotationColor,
          icon: annotationIcon,
        });
        return;
      }

      if (annotationTool === 'text') {
        setAnnotationTextCoord(coord);
        setAnnotationTextInput('');
        setShowAnnotationTextModal(true);
        return;
      }

      if (annotationTool === 'circle') {
        // First tap = center, second tap = edge (radius)
        if (!arrowStart) {
          setArrowStart(coord);
        } else {
          const radiusM = distanceMeters(arrowStart, coord);
          handleSaveAnnotation({
            type: 'circle',
            coordinate: arrowStart,
            radiusMeters: radiusM,
            label: `${Math.round(radiusM)}m`,
            color: annotationColor,
          });
          setArrowStart(null);
        }
        return;
      }

      if (annotationTool === 'arrow') {
        if (!arrowStart) {
          setArrowStart(coord);
        } else {
          handleSaveAnnotation({
            type: 'arrow',
            coordinate: arrowStart,
            endCoordinate: coord,
            label: '',
            color: annotationColor,
          });
          setArrowStart(null);
        }
        return;
      }

      return;
    }

    // ── Water body tap detection ──────────────────────────────────
    if (event?.geometry?.coordinates && mapRef.current) {
      const [tappedLng, tappedLat] = event.geometry.coordinates as [number, number];
      (async () => {
        const screenPt = event.properties?.screenPointX != null
          ? [event.properties.screenPointX, event.properties.screenPointY]
          : event.point?.x != null
            ? [event.point.x, event.point.y]
            : [SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2];

        try {
          if (activeOverlays.has('local-bathymetry')) {
            const bathyResult = await mapRef.current?.queryRenderedFeaturesAtPoint(
              screenPt,
              undefined,
              [
                ...getBathyLayerIds('fill'),
                ...getBathyLayerIds('line'),
                ...getBathyLayerIds('label'),
              ],
            );
            const depthResult = getDepthFromRenderedFeatures(bathyResult);
            if (depthResult) {
              const lakeAttribution = getLakeAttributionFromRenderedFeatures(bathyResult);
              setSelectedContourDepth(depthResult);
              setSelectedContourLake(lakeAttribution);
              setSelectedMarkerId(null);
              setFocusedLocation(null);
              setSelectedMarina(null);
              setHighlightedAccessPoints([]);
              setHighlightedAccessSummary(null);
              setContextualTips([]);
              setContextualTipsDismissed(false);
              return;
            }
          }
        } catch {
          // Ignore bathymetry hit-test failures and fall through to generic water tap.
        }

        try {
          const waterLayerIds = ['water', 'water-polygon', 'waterway'];
          const result = await mapRef.current?.queryRenderedFeaturesAtPoint(
            screenPt, undefined, waterLayerIds,
          );
          if (result?.features?.length) {
            const feat = result.features[0];
            const fp = feat.properties || {};
            const wName = fp.name || fp.name_en || fp.class || 'Water Body';
            const wClass = fp.class
              ? fp.class.charAt(0).toUpperCase() + fp.class.slice(1)
              : 'Water Body';
            const tmpLoc: FishingLocation = {
              id: buildWaterTapLocationId(wName, tappedLat, tappedLng),
              name: wName,
              subtitle: wClass,
              lat: tappedLat,
              lon: tappedLng,
              score: 0,
              scoreBreakdown: { catchProbability: 0, cpue: 0, conditions: 0, trophyPotential: 0 },
              conditions: {
                waterTemp: 0, airTemp: 0, weather: 'Unknown',
                weatherIcon: 'partly-cloudy' as any,
                windSpeed: 0, windDirection: '', pressure: 29.92,
                pressureTrend: 'steady', humidity: 50, moonPhase: '',
                solunarRating: 'fair', sunrise: '', sunset: '',
              },
              explanation: '',
              forecast: [],
            };
            clearSelectedContour();
            setFocusedLocation(tmpLoc);
            cacheLocationDetail(tmpLoc);
            cameraRef.current?.setCamera({
              centerCoordinate: [tappedLng, tappedLat],
              zoomLevel: Math.max(currentZoom, 12),
              animationDuration: 500,
            });
            animateSheetTo(SHEET_HIDDEN);
            if (isNamedWaterbody(wName)) {
              navigation.navigate('LocationDetail', { locationId: tmpLoc.id });
            }
            return;
          }
        } catch { /* queryRenderedFeatures unavailable */ }
        clearSelectedContour();
        setSelectedMarkerId(null);
        setFocusedLocation(null);
        setSelectedMarina(null);
        setHighlightedAccessPoints([]);
        setHighlightedAccessSummary(null);
        setContextualTips([]);
        setContextualTipsDismissed(false);
      })();
      return;
    }

    clearSelectedContour();
    setSelectedMarkerId(null);
    setFocusedLocation(null);
    setSelectedMarina(null);
    // Clear highlighted access points (Feature 2)
    setHighlightedAccessPoints([]);
    setHighlightedAccessSummary(null);
    // Clear contextual tips (Feature 1)
    setContextualTips([]);
    setContextualTipsDismissed(false);
  }, [routeMode, probeWaterAtScreenPoint, pendingRouteDestination, probeRoutePoint, placeRoutePoint, measureMode, annotationMode, annotationTool, annotationColor, annotationIcon, arrowStart, handleSaveAnnotation, activeOverlays, currentZoom, animateSheetTo, clearSelectedContour, navigation]);

  // ── Derived ─────────────────────────────────────────────────────
  const waypointIonicon = (wpIcon: WaypointIcon): string =>
    WAYPOINT_ICONS.find((i) => i.key === wpIcon)?.ionicon ?? 'location';

  const routeLineGeoJSON = useMemo(
    () => makeRouteLineGeoJSON(plannedRoute?.waypoints.map((wp) => wp.position) ?? routePoints),
    [plannedRoute, routePoints],
  );

  const routePointGeoJSON = useMemo(
    () => makeRoutePointGeoJSON(routePoints.length > 0 ? routePoints : plannedRoute?.waypoints.map((wp) => wp.position) ?? []),
    [plannedRoute, routePoints],
  );

  const routeCorridorGeoJSON = useMemo(
    () =>
      makeRouteCorridorGeoJSON(
        plannedRoute,
        routeBoatProfile?.draftMeters,
        routeNavActive,
        routeNavIndex,
        userLocation,
      ),
    [plannedRoute, routeBoatProfile?.draftMeters, routeNavActive, routeNavIndex, userLocation],
  );

  const pendingRouteDestinationGeoJSON = useMemo(
    () =>
      makeRouteTargetGeoJSON(
        routePoints.length === 0 ? pendingRouteDestination?.point ?? null : null,
        'destination',
        pendingRouteDestination?.label ?? 'DESTINATION',
      ),
    [pendingRouteDestination, routePoints.length],
  );

  const routeWarningGeoJSON = useMemo(
    () => makeRouteWarningGeoJSON(routeWarnings, isMetric),
    [isMetric, routeWarnings],
  );

  const routeTurnItems = useMemo(() => {
    if (!plannedRoute) return [];
    return plannedRoute.segments.map((segment, index) => {
      const previousBearing = plannedRoute.segments[index - 1]?.bearingDeg;
      const delta = previousBearing == null ? 0 : normalizeBearingDelta(segment.bearingDeg - previousBearing);
      const maneuver =
        index === 0
          ? 'Depart'
          : Math.abs(delta) < 18
            ? 'Continue'
            : Math.abs(delta) < 42
              ? delta > 0
                ? 'Bear starboard'
                : 'Bear port'
              : Math.abs(delta) < 140
                ? delta > 0
                  ? 'Turn starboard'
                  : 'Turn port'
                : 'Sharp turn';
      const distancePrimary = isMetric
        ? `${(segment.distanceNm * 1.852).toFixed(1)} km`
        : `${segment.distanceNm.toFixed(1)} nm`;
      const severity = getRouteSegmentSeverity(segment.minDepthM, routeBoatProfile?.draftMeters);
      return {
        id: `turn-${index}`,
        segmentIndex: index,
        title:
          index === 0
            ? `Depart on ${segment.bearingLabel}`
            : `${maneuver} to ${segment.bearingLabel}`,
        detail: `${distancePrimary} · leg ${index + 1}`,
        severity,
      };
    });
  }, [isMetric, plannedRoute, routeBoatProfile?.draftMeters]);

  const isVectorOverlayReady = useCallback(
    (overlayKey: string) => {
      const layerName = VECTOR_OVERLAY_LAYER_BY_KEY[overlayKey];
      return !layerName || !!availableVectorLayers[layerName];
    },
    [availableVectorLayers],
  );

  const totalVisible =
    markerMode === 'locations' ? filteredLocations.length : filteredWaypoints.length;
  const countLabel =
    markerMode === 'locations'
      ? `${totalVisible} location${totalVisible !== 1 ? 's' : ''}`
      : `${totalVisible} waypoint${totalVisible !== 1 ? 's' : ''}`;

  const renderSiteCard = useCallback(
    ({ item }: { item: { location: FishingLocation; distanceMi: number | null } }) => (
      <SiteCard
        location={item.location}
        distanceMi={item.distanceMi}
        onPress={() => handleCardPress(item.location)}
      />
    ),
    [handleCardPress],
  );

  const keyExtractor = useCallback(
    (item: { location: FishingLocation; distanceMi: number | null }) =>
      item.location.id,
    [],
  );

  // ── AIS WiFi vessel refresh ──────────────────────────────────────
  useEffect(() => {
    if (!aisWifiEnabled) {
      setAisWifiGeoJSON(null);
      return;
    }
    // Initial load
    setAisWifiGeoJSON(aisReceiver.getVesselsGeoJSON());
    // Subscribe to updates
    const unsub = aisReceiver.addAISListener(() => {
      setAisWifiGeoJSON(aisReceiver.getVesselsGeoJSON());
    });
    // Also poll every 10s in case of missed updates
    const interval = setInterval(() => {
      setAisWifiGeoJSON(aisReceiver.getVesselsGeoJSON());
    }, 10_000);
    return () => { unsub(); clearInterval(interval); };
  }, [aisWifiEnabled]);

  // ── Map Tools drawer groups (progressive disclosure) ─────────────
  // Contextual: only show tools relevant to current location/season
  const currentMonth = new Date().getMonth() + 1; // 1-12
  const isWinterSeason = currentMonth >= 11 || currentMonth <= 3;
  const isNearCoastNow = mapFeatureLocation ? isNearCoast(mapFeatureLocation.lat, mapFeatureLocation.lon) : false;
  const mapFeatureCountry = useMemo(() => {
    if (!mapFeatureLocation) return null;
    return detectSurveyCountry(mapFeatureLocation.lat, mapFeatureLocation.lon);
  }, [mapFeatureLocation]);
  const internationalMarineBathySource = useMemo(() => {
    if (!mapFeatureLocation || !isNearCoastNow || !mapFeatureCountry) return null;
    const country = mapFeatureCountry;
    if (country !== 'CA') return null;
    return getInternationalBathymetryTileSource(country);
  }, [isNearCoastNow, mapFeatureCountry, mapFeatureLocation]);
  const canadianRiverNetworkSource = useMemo(
    () => getCanadianHydroNetworkTileSource(),
    [],
  );
  const bathymetryDepthBreaks = useMemo(
    () => buildDepthBreaksFt(contourSettings.interval),
    [contourSettings.interval],
  );
  const bathymetryColorStops = useMemo(
    () => sampleColorRamp(getColorStops(contourSettings), bathymetryDepthBreaks.length),
    [bathymetryDepthBreaks.length, contourSettings],
  );
  const bathymetryShadowStops = useMemo(
    () => bathymetryColorStops.map((color) => darkenHex(color, 0.24)),
    [bathymetryColorStops],
  );
  const bathymetryHighlightStops = useMemo(
    () => bathymetryColorStops.map((color) => lightenHex(color, 0.3)),
    [bathymetryColorStops],
  );
  const bathymetryLegendStops = useMemo(() => {
    const colors = bathymetryColorStops;
    const labels = bathymetryDepthBreaks;
    return [
      { color: colors[0] ?? '#D6EAF8', label: `${Math.round(labels[0])}-${Math.round(labels[3])}` },
      { color: colors[3] ?? colors[2] ?? '#85C1E9', label: `${Math.round(labels[3])}-${Math.round(labels[5])}` },
      { color: colors[5] ?? colors[4] ?? '#3498DB', label: `${Math.round(labels[5])}-${Math.round(labels[8])}` },
      { color: colors[8] ?? colors[7] ?? '#2471A3', label: `${Math.round(labels[8])}-${Math.round(labels[10])}` },
      { color: colors[10] ?? colors[9] ?? '#1A5276', label: `${Math.round(labels[10])}+` },
    ];
  }, [bathymetryColorStops, bathymetryDepthBreaks]);
  const depthBandValueExpression = useMemo(
    () => ['coalesce', ['get', 'depth_max_ft'], ['get', 'depth_ft'], 0] as any,
    [],
  );
  const bathymetryFillExpression = useMemo(() => {
    return buildSteppedDepthExpression(
      bathymetryColorStops,
      bathymetryDepthBreaks,
      '#D6EAF8',
      depthBandValueExpression,
    );
  }, [bathymetryColorStops, bathymetryDepthBreaks, depthBandValueExpression]);
  const bathymetryShadowExpression = useMemo(() => {
    return buildSteppedDepthExpression(
      bathymetryShadowStops,
      bathymetryDepthBreaks,
      '#9BB5C8',
      depthBandValueExpression,
    );
  }, [bathymetryDepthBreaks, bathymetryShadowStops, depthBandValueExpression]);
  const bathymetryHighlightExpression = useMemo(() => {
    return buildSteppedDepthExpression(
      bathymetryHighlightStops,
      bathymetryDepthBreaks,
      '#F0F7FC',
      ['coalesce', ['get', 'depth_min_ft'], ['get', 'depth_ft'], 0],
    );
  }, [bathymetryDepthBreaks, bathymetryHighlightStops]);
  const routeArrivalLabel = useMemo(
    () => formatArrivalClock(routeMetrics?.adjustedEta),
    [routeMetrics?.adjustedEta],
  );
  const routeStatusTone = routeDepthChecking
    ? '#1565C0'
    : routeAlerts.length > 0
      ? '#2E7D32'
      : routeWarnings.length > 0
        ? '#C96A18'
        : '#1565C0';
  const routeStatusLabel = routeDepthChecking
    ? 'Checking depth'
    : routeAlerts.length > 0
      ? 'Auto-routed'
      : routeWarnings.length > 0
        ? `${routeWarnings.length} shallow`
        : 'Depth clear';
  const routeNextLegText = routeNavInfo
    ? `${routeNavInfo.nextWaypointName} · ${(routeNavInfo.distanceMeters / 1852).toFixed(1)} nm · ${Math.round(routeNavInfo.bearingDeg)}°`
    : null;
  const routeOverviewText = routeNavActive && routeNextLegText
    ? routeNextLegText
    : routeMetrics
    ? `${isMetric ? (routeMetrics.totalDistanceMi * MILES_TO_KM).toFixed(1) : routeMetrics.totalDistanceMi.toFixed(1)} ${isMetric ? 'km' : 'mi'} · ${routeMetrics.adjustedTimeLabel} · arrive ${routeArrivalLabel}`
    : pendingRouteDestination
      ? `Choose a water start for ${pendingRouteDestination.label ?? 'this destination'}`
    : routeMode
      ? routePlacementMode === 'start'
        ? 'Set A on the water, then pick your destination or stops'
        : routePlacementMode === 'end'
          ? 'Place B on the water to finish the route'
          : 'Add stops by tapping the water or using the center reticle'
      : routePoints.length > 1
      ? `${routePoints.length} route points placed`
      : 'Start a route from search, or tap the water to build one';
  const routeSupportText = routeNavActive
    ? routeCameraMode === 'overview'
      ? 'Navigation is active. Switch to Follow or 3D for the live helm view.'
      : 'Following your position. Amber and red water mark draft-limited zones.'
    : routeMode
    ? pendingRouteDestination
      ? 'Your destination is pinned. Set A on the water, then add stops if you want.'
      : routePlacementMode === 'start'
        ? 'Pick your start point on the water.'
        : routePlacementMode === 'end'
          ? 'Pick the destination point on the water.'
          : 'Tap the water or long-press near the blue line to pull the route through a stop.'
    : routeDepthChecking
    ? 'Checking land, channels, and draft depth'
    : routeAlerts[0]
      ? routeAlerts[0]
      : routeWarnings.length > 0
        ? `${routeWarnings.length} shallow segments need caution`
        : 'Channels, land, and draft fit checked';
  const routeDistancePrimaryLabel = routeMetrics
    ? `${isMetric ? (routeMetrics.totalDistanceMi * MILES_TO_KM).toFixed(1) : routeMetrics.totalDistanceMi.toFixed(1)} ${isMetric ? 'km' : 'mi'}`
    : 'Tap to start';
  const routeDistanceSecondaryLabel = routeMetrics ? `${routeMetrics.totalDistanceNm.toFixed(1)} nm` : '';
  const routeDraftPrimaryLabel = routeBoatProfile
    ? (isMetric
      ? `${routeBoatProfile.draftMeters.toFixed(1)} m`
      : `${boatDraftFt.toFixed(1)} ft`)
    : (isMetric ? '0.6 m' : '2.0 ft');
  const routeDraftSecondaryLabel = routeBoatProfile
    ? (isMetric
      ? `${boatDraftFt.toFixed(1)} ft`
      : `${routeBoatProfile.draftMeters.toFixed(1)} m`)
    : (isMetric ? '2.0 ft' : '0.6 m');
  const routePlannerVisible = routeMode || routePoints.length > 0;
  const showRoutePlannerDetails = routePlannerExpanded;
  const navPresentationActive = routeNavActive;
  const showTopMapAlerts = false;
  const topStackTop = Math.max(insets.top + 8, Platform.OS === 'ios' ? 18 : 12);
  const topStackBottom = topStackTop + searchOverlayHeight;
  const topBannerTop = topStackBottom + 8;
  const floatingControlsTop = topStackBottom + 10;
  const showFloatingMapControls = !routePlannerVisible;
  const routePlannerBottom = routeNavActive ? insets.bottom + 8 : insets.bottom + 66;
  const routePlannerMaxHeight = routeNavActive
    ? SCREEN_HEIGHT * 0.34
    : showRoutePlannerDetails
      ? SCREEN_HEIGHT * 0.42
      : routePoints.length > 0 || pendingRouteDestination
        ? SCREEN_HEIGHT * 0.27
        : SCREEN_HEIGHT * 0.23;
  const shouldShowChartLabels = contourSettings.showLabels && !navPresentationActive;
  const shouldShowBathymetryDetails = !navPresentationActive;
  const routeTapBannerTextValue = pendingRouteDestination && routePoints.length === 0
    ? `Tap water to set A for ${pendingRouteDestination.label ?? 'this route'}`
    : routePlacementMode === 'start'
      ? 'Tap water to set route start (A)'
      : routePlacementMode === 'end'
        ? 'Tap water to set destination (B)'
        : 'Tap water or hold near the blue line to add a stop'
  ;
  const connectedInstrumentCount = [
    marineInstrumentData.position,
    marineInstrumentData.heading,
    marineInstrumentData.depth,
    marineInstrumentData.wind,
  ].filter(Boolean).length;
  const shouldMountPointDetailOverlays = currentZoom >= 8;
  const shouldMountMarineDetailOverlays = currentZoom >= 7;

  const mapToolGroups: MapToolGroup[] = [
    {
      title: 'Map Layers',
      tools: [
        {
          key: 'lake-depth-layer',
          label: 'Lake Depth Contours',
          icon: 'analytics-outline',
          description: 'Survey-backed inland contour bands where available',
          isActive: activeOverlays.has('local-bathymetry'),
          onPress: () => handleToggleOverlay('local-bathymetry'),
          infoCard: activeOverlays.has('local-bathymetry')
            ? {
                primary: 'Inland bathymetry active',
                secondary: 'Best on survey-backed lakes; other lakes may stay coarse',
                icon: 'water',
                color: '#1565C0',
              } as OverlayInfoCard
            : null,
          legendStops: bathymetryLegendStops,
          legendUnit: '(ft)',
        },
        {
          key: 'ocean-depth-layer',
          label: 'Ocean Depth Lines',
          icon: 'navigate-circle-outline',
          description: 'GEBCO contour lines for oceans, coasts, and Great Lakes',
          isActive: activeOverlays.has('depth-contours'),
          onPress: () => handleToggleOverlay('depth-contours'),
          infoCard: activeOverlays.has('depth-contours')
            ? {
                primary: 'Ocean depth contours active',
                secondary: 'Great for coasts, inlets, and offshore structure',
                icon: 'navigate-circle',
                color: '#1F6FB2',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'noaa-marine-navigation-layer',
          label: 'NOAA Marine Navigation',
          icon: 'boat-outline',
          description: 'Official maintained channels, shipping regs, maritime boundaries',
          visible: isNearCoastNow && mapFeatureCountry === 'US',
          isActive: activeOverlays.has('marine-navigation'),
          onPress: () => handleToggleOverlay('marine-navigation'),
          infoCard: activeOverlays.has('marine-navigation')
            ? {
                primary: 'NOAA marine navigation active',
                secondary: 'Maintained channels, shipping regulations, and maritime limits',
                icon: 'boat',
                color: '#0F5D7A',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'north-america-river-network-layer',
          label: 'River Network',
          icon: 'git-network-outline',
          description: 'Official USGS + NHN hydrography backbone for North America',
          isActive: activeOverlays.has('river-network'),
          onPress: () => handleToggleOverlay('river-network'),
          infoCard: activeOverlays.has('river-network')
            ? {
                primary: 'North America river network active',
                secondary: 'Official hydrography backbone for river completeness; USACE adds surveyed corridor detail on top',
                icon: 'git-network',
                color: '#3D6D8A',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'usace-river-navigation-layer',
          label: 'USACE River Navigation',
          icon: 'git-branch-outline',
          description: 'Official river depth areas, contours, wrecks, and bridges',
          visible: mapFeatureCountry === 'US',
          isActive: activeOverlays.has('river-navigation'),
          onPress: () => handleToggleOverlay('river-navigation'),
          infoCard: activeOverlays.has('river-navigation')
            ? {
                primary: 'USACE river navigation active',
                secondary: 'Selected IENC chart overlays for maintained U.S. river corridors',
                icon: 'git-network',
                color: '#6A4C93',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'public-lands-layer',
          label: 'Public Lands',
          icon: 'leaf-outline',
          description: 'Protected land and public access context',
          isActive: activeOverlays.has('public-lands'),
          onPress: () => handleToggleOverlay('public-lands'),
        },
      ],
    },
    {
      title: 'Drawing & Measurement',
      tools: [
        {
          key: 'measure',
          label: 'Measure Distance',
          icon: 'resize-outline',
          description: 'Tap points to measure between them',
          isActive: measureMode,
          onPress: () => {
            setMeasureMode((prev) => { if (prev) setMeasurePoints([]); return !prev; });
            setMapToolsDrawerOpen(false);
          },
        },
        {
          key: 'annotate',
          label: 'Annotate Map',
          icon: 'create-outline',
          description: 'Draw markers, arrows, and circles',
          isActive: annotationMode,
          onPress: () => {
            setAnnotationMode((prev) => !prev);
            if (measureMode) { setMeasureMode(false); setMeasurePoints([]); }
            setMapToolsDrawerOpen(false);
          },
        },
        {
          key: 'contour-settings',
          label: 'Depth Contours',
          icon: 'analytics-outline',
          description: 'Customize contour colors and intervals',
          onPress: () => {
            setShowContourModal(true);
            setMapToolsDrawerOpen(false);
          },
        },
        {
          key: 'route-planner',
          label: 'Route Planner',
          icon: 'navigate-outline',
          description: routeMode
            ? 'Tap the map to add route points'
            : routePoints.length > 0
              ? 'Edit or continue the active route on the map'
              : 'Tap-to-build routes directly on the map',
          isActive: routeMode || routePoints.length > 0,
          onPress: () => {
            if (routeMode) {
              setRouteMode(false);
            } else {
              openRouteBuilder();
            }
            setMapToolsDrawerOpen(false);
          },
          infoCard: routeMetrics
            ? {
                primary: `${routeMetrics.totalDistanceMi.toFixed(1)} mi · ${routePoints.length} pts`,
                secondary: routeNavActive ? 'Live route guidance active' : 'Tap map to keep editing',
                icon: 'navigate',
                color: '#1565C0',
              } as OverlayInfoCard
            : null,
        },
      ],
    },
    {
      title: 'Weather Overlays',
      tools: [
        {
          key: 'wind',
          label: 'Wind',
          icon: 'flag-outline',
          description: 'Wind speed and direction arrows',
          isActive: windEnabled,
          isLoading: windLoading,
          onPress: () => setWindEnabled((prev) => !prev),
          infoCard: windEnabled && windVectorCount > 0
            ? {
                primary: `${windVectorCount} live wind arrows`,
                secondary: mapWeatherForecast
                  ? `${mapWeatherForecast.current.windMph} mph at the map center`
                  : 'Forecast pinned to the map center',
                icon: 'flag',
                color: '#1565C0',
              } as OverlayInfoCard
            : null,
          legendStops: WIND_LEGEND_STOPS,
        },
        {
          key: 'waves',
          label: 'Wave Height',
          icon: 'water-outline',
          description: 'Wave height and direction (coastal)',
          isActive: waveEnabled,
          isLoading: waveLoading,
          visible: isNearCoastNow,
          onPress: () => setWaveEnabled((prev) => !prev),
          legendStops: WAVE_LEGEND_STOPS,
          legendUnit: '(m)',
        },
        {
          key: 'radar',
          label: 'Precipitation Radar',
          icon: 'rainy-outline',
          description: 'Real-time rain and snow radar',
          isActive: radarEnabled,
          isLoading: radarLoading,
          onPress: () => setRadarEnabled((prev) => !prev),
          infoCard: radarEnabled && radarFrames.length > 0
            ? {
                primary: radarFrames[radarFrameIndex]?.timestamp
                  ? new Date(radarFrames[radarFrameIndex].timestamp.time * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
                  : 'Loading...',
                secondary: `${radarFrames.length} frames${radarPlaying ? ' (playing)' : ''}`,
                icon: 'rainy',
                color: '#7B1FA2',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'tidal-currents',
          label: 'Tidal Currents',
          icon: 'swap-horizontal-outline',
          description: 'NOAA tidal current predictions',
          isActive: tidalCurrentEnabled,
          isLoading: tidalLoading,
          visible: isNearCoastNow,
          onPress: () => setTidalCurrentEnabled((prev) => !prev),
          legendStops: TIDAL_LEGEND_STOPS,
        },
        {
          key: 'sst',
          label: 'Sea Surface Temp',
          icon: 'thermometer-outline',
          description: 'Ocean temperature map (find temp breaks)',
          isActive: sstEnabled,
          visible: isNearCoastNow,
          onPress: () => setSSTEnabled((prev) => !prev),
          infoCard: sstEnabled
            ? {
                primary: 'SST overlay active',
                secondary: 'Blue = cold, Red = warm',
                icon: 'thermometer',
                color: '#FF9800',
              } as OverlayInfoCard
            : null,
          legendStops: SST_LEGEND_STOPS,
        },
      ],
    },
    {
      title: 'Points of Interest',
      tools: [
        {
          key: 'marinas',
          label: 'Marinas & POIs',
          icon: 'boat-outline',
          description: 'Marinas, fuel docks, and boat ramps',
          isActive: marinasEnabled,
          isLoading: marinasLoading,
          onPress: () => setMarinasEnabled((prev) => !prev),
          infoCard: marinasEnabled && marinaPOIs.length > 0
            ? {
                primary: `${marinaPOIs.length} POIs nearby`,
                secondary: `Marinas, bait shops, ramps`,
                icon: 'boat',
                color: '#2563EB',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'access-points',
          label: 'Access Points',
          icon: 'trail-sign-outline',
          description: 'Boat launches, shore access, trails',
          isActive: accessEnabled,
          isLoading: accessLoading,
          onPress: () => setAccessEnabled((prev) => !prev),
          infoCard: accessEnabled && accessPoints.length > 0
            ? {
                primary: `${accessPoints.length} access points`,
                secondary: 'Launches, trails, shore access',
                icon: 'trail-sign',
                color: '#16A34A',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'marine-gas',
          label: 'Marine Fuel Docks',
          icon: 'car-outline',
          description: 'Marine gas stations and fuel docks',
          isActive: marineGasEnabled,
          onPress: () => setMarineGasEnabled((prev) => !prev),
        },
      ],
    },
    {
      title: 'Fishing Intelligence',
      tools: [
        {
          key: 'bite-time',
          label: 'Fishability',
          icon: 'flame-outline',
          description: 'Heat map of the best zones to fish on the water right now',
          isActive: biteTimeOverlayEnabled,
          isLoading: biteLoading,
          onPress: () => setBiteTimeOverlayEnabled((prev) => !prev),
          infoCard: biteTimeOverlayEnabled
            ? {
                primary: biteFieldGeoJSON?.features?.length
                  ? `${biteFieldGeoJSON.features.length} lake zones sampled`
                  : 'Building lake fishability map',
                secondary: 'Shows the best zones on the lake using bite timing, depth fit, and structure',
                icon: 'flame',
                color: '#2E7D32',
              } as OverlayInfoCard
            : null,
          legendStops: BITE_LEGEND_STOPS,
        },
        {
          key: 'fishing-pressure',
          label: 'Fishing Pressure',
          icon: 'people-outline',
          description: 'Heat map of crowding at spots',
          isActive: fishingPressureOverlayEnabled,
          isLoading: pressureLoading,
          onPress: () => setFishingPressureOverlayEnabled((prev) => !prev),
          infoCard: fishingPressureOverlayEnabled
            ? {
                primary: 'Pressure overlay active',
                secondary: 'Green = empty, Red = packed',
                icon: 'people',
                color: '#EF5350',
              } as OverlayInfoCard
            : null,
          legendStops: PRESSURE_LEGEND_STOPS,
        },
        {
          key: 'ice-thickness',
          label: 'Ice Thickness',
          icon: 'snow-outline',
          description: 'Estimated ice depth across lakes',
          isActive: iceThicknessEnabled,
          isLoading: iceLoading,
          visible: isWinterSeason,
          onPress: () => setIceThicknessEnabled((prev) => !prev),
          infoCard: iceThicknessEnabled
            ? {
                primary: 'Ice thickness overlay active',
                secondary: 'Red <4", Green 8-12", Blue 12"+',
                icon: 'snow',
                color: '#0D47A1',
              } as OverlayInfoCard
            : null,
          legendStops: ICE_LEGEND_STOPS,
        },
        {
          key: 'ocean-spots',
          label: 'Ocean Fishing Spots',
          icon: 'fish-outline',
          description: 'Wrecks, reefs, ledges',
          isActive: oceanSpotsEnabled,
          visible: isNearCoastNow,
          onPress: () => setOceanSpotsEnabled((prev) => !prev),
        },
      ],
    },
    {
      title: 'Nautical',
      tools: [
        {
          key: 'depth-numbers',
          label: 'Depth Numbers',
          icon: 'text-outline',
          description: 'Nautical chart depth soundings',
          isActive: depthNumbersEnabled,
          isLoading: depthLoading,
          onPress: () => setDepthNumbersEnabled((prev) => !prev),
        },
        {
          key: 'dynamic-depths',
          label: 'Dynamic Depths',
          icon: 'water-outline',
          description: 'Tide-adjusted depths (real-time)',
          isActive: dynamicDepthsEnabled,
          isLoading: dynamicDepthLoading,
          visible: isNearCoastNow,
          onPress: () => setDynamicDepthsEnabled((prev) => !prev),
          infoCard: dynamicDepthsEnabled && tideBadgeText
            ? {
                primary: tideBadgeText,
                secondary: tideStationName ? `Station: ${tideStationName}` : 'Adjusting depths by tide',
                icon: tideLevelM >= 0 ? 'trending-up' : 'trending-down',
                color: tideLevelM >= 0 ? '#1E88E5' : '#E65100',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'draft-access',
          label: 'Draft Accessibility',
          icon: 'boat-outline',
          description: `Safe/caution/danger for ${boatDraftFt}ft draft`,
          isActive: draftAccessEnabled,
          onPress: () => setDraftAccessEnabled((prev) => !prev),
          infoCard: draftAccessEnabled
            ? {
                primary: `Draft: ${boatDraftFt}ft`,
                secondary: 'Green = safe, Orange = caution, Red = danger',
                icon: 'boat',
                color: '#4CAF50',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'usace-surveys',
          label: 'USACE Surveys',
          icon: 'water-outline',
          description: 'Channel depths, locks, harbors',
          isActive: usaceSurveyEnabled,
          onPress: () => setUsaceSurveyEnabled((prev) => !prev),
        },
        {
          key: 'nav-aids-tool',
          label: 'Navigation Aids',
          icon: 'radio-outline',
          description: 'Buoys, lights, channel markers',
          visible: isNearCoastNow || marinasEnabled,
          onPress: () => {
            // Toggle nav-aids overlay
            handleToggleOverlay('nav-aids');
            setMapToolsDrawerOpen(false);
          },
          isActive: activeOverlays.has('nav-aids'),
        },
        {
          key: 'no-wake-tool',
          label: 'No-Wake Zones',
          icon: 'speedometer-outline',
          description: 'Speed-restricted areas on water',
          visible: isNearCoastNow || marinasEnabled,
          onPress: () => {
            handleToggleOverlay('no-wake-zones');
            setMapToolsDrawerOpen(false);
          },
          isActive: activeOverlays.has('no-wake-zones'),
        },
        {
          key: 'terrain-3d',
          label: 'Shaded Relief',
          icon: 'cube-outline',
          description: 'USGS terrain shading and elevation relief',
          isActive: terrain3DEnabled,
          onPress: () => setTerrain3DEnabled((prev) => !prev),
        },
        {
          key: 'artificial-reefs-tool',
          label: 'Artificial Reefs',
          icon: 'flag-outline',
          description: 'State artificial reef GPS locations',
          visible: isNearCoastNow,
          onPress: () => {
            handleToggleOverlay('artificial-reefs');
            setMapToolsDrawerOpen(false);
          },
          isActive: activeOverlays.has('artificial-reefs'),
        },
        {
          key: 'seabed-chars',
          label: 'Seabed Type',
          icon: 'layers-outline',
          description: 'Bottom composition & anchoring',
          isActive: seabedEnabled,
          isLoading: seabedLoading,
          visible: isNearCoastNow,
          onPress: () => setSeabedEnabled((prev) => !prev),
          infoCard: seabedEnabled
            ? {
                primary: 'Seabed Characteristics',
                secondary: 'Green = good, Yellow = fair, Red = poor anchoring',
                icon: 'layers',
                color: '#4CAF50',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'maritime-boundaries',
          label: 'Maritime Boundaries',
          icon: 'shield-outline',
          description: 'Shipping lanes & restricted areas',
          isActive: maritimeBoundariesEnabled,
          isLoading: maritimeBoundariesLoading,
          visible: isNearCoastNow,
          onPress: () => setMaritimeBoundariesEnabled((prev) => !prev),
          infoCard: maritimeBoundariesEnabled
            ? {
                primary: 'Shipping Lanes & Restrictions',
                secondary: 'Safety-critical boundaries shown',
                icon: 'shield-checkmark',
                color: '#F44336',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'ais-wifi',
          label: 'Marine Electronics',
          icon: 'radio-outline',
          description: 'AIS, GPS, depth, wind, and heading from onboard instruments',
          isActive: aisWifiEnabled,
          onPress: () => setAisWifiEnabled((prev) => !prev),
          infoCard: aisWifiEnabled && (connectedInstrumentCount > 0 || (aisWifiGeoJSON && aisWifiGeoJSON.features.length > 0))
            ? {
                primary: aisWifiGeoJSON && aisWifiGeoJSON.features.length > 0
                  ? `${aisWifiGeoJSON.features.length} vessel${aisWifiGeoJSON.features.length !== 1 ? 's' : ''} via onboard feed`
                  : `${connectedInstrumentCount} live instrument${connectedInstrumentCount !== 1 ? 's' : ''}`,
                secondary: aisReceiver.getAISStatus().status === 'connected'
                  ? `${connectedInstrumentCount} sensors online`
                  : 'Not connected',
                icon: 'radio',
                color: '#00897B',
              } as OverlayInfoCard
            : null,
        },
      ],
    },
  ];
  const mapToolsActiveCount = [
    measureMode,
    annotationMode,
    windEnabled,
    radarEnabled,
    marinasEnabled,
    accessEnabled,
    waveEnabled,
    depthNumbersEnabled,
    marineGasEnabled,
    oceanSpotsEnabled,
    terrain3DEnabled,
    tidalCurrentEnabled,
    aisWifiEnabled,
    draftAccessEnabled,
    dynamicDepthsEnabled,
    usaceSurveyEnabled,
    sstEnabled,
    biteTimeOverlayEnabled,
    fishingPressureOverlayEnabled,
    iceThicknessEnabled,
    seabedEnabled,
    maritimeBoundariesEnabled,
    activeOverlays.has('local-bathymetry'),
    activeOverlays.has('public-lands'),
    activeOverlays.has('marine-navigation'),
    activeOverlays.has('river-navigation'),
    activeOverlays.has('nav-aids'),
    activeOverlays.has('no-wake-zones'),
    activeOverlays.has('artificial-reefs'),
  ].filter(Boolean).length;
  const shouldHideCompass =
    mapToolsDrawerOpen ||
    layerPickerVisible ||
    routeMode ||
    !!focusedLocation ||
    !!selectedContourDepth;
  const isDepthCriticalMode =
    routeMode ||
    routePoints.length > 0 ||
    routeNavActive ||
    draftAccessEnabled;
  const contourFeatureFilter = useCallback(
    (featureKinds: string | string[]) => buildContourFeatureFilter(featureKinds),
    [],
  );
  const lagosContourMinZoom = isDepthCriticalMode
    ? CRITICAL_LAGOS_CONTOUR_MIN_ZOOM
    : BROWSE_LAGOS_CONTOUR_MIN_ZOOM;

  const activeContourSources = useMemo(() => {
    const filteredSources = (sources: ReturnType<typeof getMartinContourSourcesForBounds>) =>
      sources.filter((source) => source.id !== 'lagos_contours' || currentZoom >= lagosContourMinZoom);
    if (visibleContourBounds) {
      return filteredSources(getMartinContourSourcesForBounds(visibleContourBounds));
    }
    if (userLocation) {
      return filteredSources(getMartinContourSourcesForBounds(
        estimateBoundsFromCenter({ lat: userLocation.lat, lon: userLocation.lon }, currentZoom || DEFAULT_ZOOM),
      ));
    }
    return filteredSources(getMartinContourSourcesForBounds(null));
  }, [currentZoom, lagosContourMinZoom, userLocation, visibleContourBounds]);
  const shouldRenderLocalBathymetry =
    currentZoom >= LOCAL_BATHY_MIN_ZOOM &&
    activeContourSources.length > 0 &&
    (activeOverlays.has('local-bathymetry') || routeMode || routePoints.length > 0);
  const bathyOpacityScale = activeOverlays.has('local-bathymetry') ? 1 : 0.42;
  const bathyVisualOpacityScale = navPresentationActive
    ? (activeOverlays.has('local-bathymetry') ? 0.72 : 0.52)
    : bathyOpacityScale;
  const oceanContourOpacityScale = activeOverlays.has('depth-contours') ? 1 : 0.42;
  const oceanContourVisualOpacityScale = navPresentationActive
    ? (activeOverlays.has('depth-contours') ? 0.68 : 0.5)
    : oceanContourOpacityScale;

  // ── Render ───────────────────────────────────────────────────────
  if (Platform.OS === 'web') {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <Ionicons name="map-outline" size={64} color={palette.textSecondary} />
        <Text style={{ color: palette.text, fontSize: 18, marginTop: 16, fontWeight: '600' }}>
          Map requires native build
        </Text>
        <Text style={{ color: palette.textSecondary, fontSize: 14, marginTop: 8, textAlign: 'center', paddingHorizontal: 40 }}>
          Use EAS Build or TestFlight to test the full map experience on a physical device.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <MLMapView
        ref={mapRef}
        style={styles.map}
        mapStyle={
          mapStyle === 'opencatch'
            ? OPENCATCH_STYLE
            : mapStyle === 'night'
              ? NIGHT_STYLE
              : mapStyle === 'hybrid' || mapStyle === 'satellite'
                ? HYBRID_STYLE
                : mapStyle === 'bathymetry'
                  ? OPENCATCH_STYLE
                  : mapStyle === 'nautical-chart'
                    ? ALT_STYLES['satellite']
                    : ALT_STYLES[mapStyle]
        }
        logoEnabled={false}
        attributionEnabled={false}
        compassEnabled={false}
        onPress={handleMapPress}
        onLongPress={handleMapLongPress}
        onRegionDidChange={handleRegionChange}
      >
        <Camera
          ref={cameraRef}
          defaultSettings={{
            centerCoordinate: initialMapCenter ?? DEFAULT_CENTER,
            zoomLevel: initialMapZoom ?? DEFAULT_ZOOM,
          }}
        />

        {/* User location */}
        <UserLocation visible={!!userLocation} />

        {/* NOAA nautical chart overlay — shown when nautical-chart style is selected */}
        {mapStyle === 'nautical-chart' && RasterSource && RasterLayer && (
          <RasterSource
            id="noaa-chart-source"
            tileUrlTemplates={[NOAA_CHART_TILE_URL]}
            tileSize={256}
          >
            <RasterLayer
              id="noaa-chart-layer"
              style={{ rasterOpacity: 0.7 }}
            />
          </RasterSource>
        )}

        {/* No-wake zone overlay */}
        {activeOverlays.has('no-wake-zones') && mapFeatureLocation && ShapeSource && FillLayer && SymbolLayer && (
          <NoWakeZoneOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            FillLayer={FillLayer}
            SymbolLayer={SymbolLayer}
            LineLayer={LineLayer}
          />
        )}

        {/* Navigation aids overlay (buoys, lights) */}
        {activeOverlays.has('nav-aids') && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <NavigationAidsOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Artificial reef markers */}
        {activeOverlays.has('artificial-reefs') && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <ArtificialReefOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Wave height overlay (coastal only) */}
        {waveEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <WaveOverlayAnimated
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setWaveLoading(true)}
            onLoadEnd={() => setWaveLoading(false)}
          />
        )}

        {/* Sea Surface Temperature overlay (coastal only) */}
        {sstEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <SSTOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Depth number overlay (nautical chart soundings) */}
        {depthNumbersEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && SymbolLayer && !navPresentationActive && (
          <DepthNumberOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            units={units}
            ShapeSource={ShapeSource}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setDepthLoading(true)}
            onLoadEnd={() => setDepthLoading(false)}
          />
        )}

        {/* Marine gas station markers */}
        {marineGasEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <MarineGasOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            lakeId={focusedLocation?.id}
            bbox={focusedLocation ? getCatalogLakeBoundsForLocationId(focusedLocation.id) : undefined}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Ocean fishing spots (wrecks, reefs, ledges) */}
        {oceanSpotsEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <OceanFishingSpotsOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Ice thickness overlay (winter, northern latitudes) */}
        {iceThicknessEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <IceThicknessOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setIceLoading(true)}
            onLoadEnd={() => setIceLoading(false)}
          />
        )}

        {/* Fishing pressure heat map overlay */}
        {fishingPressureOverlayEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <FishingPressureOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            spots={locations.map((l) => ({ id: l.id, lat: l.lat, lon: l.lon, name: l.name }))}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setPressureLoading(true)}
            onLoadEnd={() => setPressureLoading(false)}
          />
        )}

        {/* Bite time activity overlay */}
        {biteTimeOverlayEnabled && shouldMountPointDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <BiteTimeOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            spots={locations.map((l) => ({ id: l.id, lat: l.lat, lon: l.lon, name: l.name }))}
            predictionGeoJSON={biteFieldGeoJSON}
            presentation={biteFieldGeoJSON ? 'field' : 'spots'}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setBiteLoading(true)}
            onLoadEnd={() => setBiteLoading(false)}
          />
        )}

        {/* Tidal current overlay (coastal only) */}
        {tidalCurrentEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <TidalCurrentOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setTidalLoading(true)}
            onLoadEnd={() => setTidalLoading(false)}
          />
        )}

        {/* Seabed characteristics overlay (zoom 10+) */}
        {seabedEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <SeabedOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            onLoadStart={() => setSeabedLoading(true)}
            onLoadEnd={() => setSeabedLoading(false)}
          />
        )}

        {/* Maritime boundaries overlay (shipping lanes, restricted areas — safety-critical) */}
        {maritimeBoundariesEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && LineLayer && FillLayer && SymbolLayer && CircleLayer && (
          <MaritimeBoundariesOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            ShapeSource={ShapeSource}
            LineLayer={LineLayer}
            FillLayer={FillLayer}
            SymbolLayer={SymbolLayer}
            CircleLayer={CircleLayer}
            onLoadStart={() => setMaritimeBoundariesLoading(true)}
            onLoadEnd={() => setMaritimeBoundariesLoading(false)}
          />
        )}

        {/* USACE survey overlay (channel depths, locks, harbors) */}
        {usaceSurveyEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && LineLayer && CircleLayer && SymbolLayer && (
          <USACESurveyOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            ShapeSource={ShapeSource}
            LineLayer={LineLayer}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            FillLayer={FillLayer}
          />
        )}

        {/* Draft accessibility overlay (safe/caution/danger for boat draft) */}
        {(draftAccessEnabled || navPresentationActive) && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <DraftAccessibilityOverlay
            lat={navPresentationActive ? (userLocation?.lat ?? mapFeatureLocation.lat) : mapFeatureLocation.lat}
            lon={navPresentationActive ? (userLocation?.lon ?? mapFeatureLocation.lon) : mapFeatureLocation.lon}
            draftFt={boatDraftFt}
            zoom={currentZoom}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
            presentation={navPresentationActive ? 'hazards' : 'full'}
          />
        )}

        {/* Dynamic Depths overlay (tide-adjusted charted depths) */}
        {dynamicDepthsEnabled && shouldMountMarineDetailOverlays && mapFeatureLocation && ShapeSource && SymbolLayer && CircleLayer && (
          <DynamicDepthOverlay
            lat={mapFeatureLocation.lat}
            lon={mapFeatureLocation.lon}
            zoom={currentZoom}
            boatDraftFt={boatDraftFt}
            ShapeSource={ShapeSource}
            SymbolLayer={SymbolLayer}
            CircleLayer={CircleLayer}
            onLoadStart={() => setDynamicDepthLoading(true)}
            onLoadEnd={() => setDynamicDepthLoading(false)}
            onTideBadgeUpdate={(badge, levelM) => {
              setTideBadgeText(badge);
              setTideLevelM(levelM);
            }}
            onStationUpdate={(station) => setTideStationName(station?.name)}
          />
        )}

        {/* AIS WiFi vessel markers from local receiver */}
        {aisWifiEnabled && aisWifiGeoJSON && ShapeSource && CircleLayer && SymbolLayer && (
          <ShapeSource id="ais-wifi-vessels-source" shape={aisWifiGeoJSON}>
            <CircleLayer
              id="ais-wifi-vessels-circle"
              style={{
                circleRadius: 6,
                circleColor: '#00897B',
                circleStrokeColor: '#FFFFFF',
                circleStrokeWidth: 2,
                circleOpacity: 0.9,
              }}
            />
            <SymbolLayer
              id="ais-wifi-vessels-label"
              minZoomLevel={10}
              style={{
                textField: ['get', 'name'],
                textSize: 10,
                textFont: ['Open Sans Bold'],
                textColor: '#00695C',
                textHaloColor: '#FFFFFF',
                textHaloWidth: 1.5,
                textOffset: [0, 1.5],
                textAllowOverlap: false,
                iconRotate: ['get', 'heading'],
                iconRotationAlignment: 'map',
              }}
            />
          </ShapeSource>
        )}

        {/* Shaded relief — real terrain shading raster */}
        {terrain3DEnabled && RasterSource && RasterLayer && (
          <RasterSource
            id="terrain-hillshade-source"
            tileUrlTemplates={[OVERLAY_TILE_URLS['shaded-relief']]}
            tileSize={256}
          >
            <RasterLayer
              id="terrain-hillshade-layer"
              style={{ rasterOpacity: 0.48 }}
            />
          </RasterSource>
        )}

        {/* Overlay raster tile layers */}
        {Array.from(activeOverlays).map((key) => {
          const url = OVERLAY_TILE_URLS[key];
          if (!url) return null;
          if (key === 'depth-contours') {
            return (
              <React.Fragment key="src-depth-contours-stack">
                {internationalMarineBathySource && RasterSource && RasterLayer && (
                  <RasterSource
                    id="overlay-source-depth-contours-canada"
                    tileUrlTemplates={[internationalMarineBathySource.urlTemplate]}
                    tileSize={256}
                  >
                    <RasterLayer
                      id="overlay-layer-depth-contours-canada-shadow"
                      style={{
                        rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.12 : 0,
                        rasterContrast: 0.26,
                        rasterSaturation: -0.42,
                        rasterBrightnessMin: 0.02,
                        rasterBrightnessMax: 0.28,
                      }}
                    />
                    <RasterLayer
                      id="overlay-layer-depth-contours-canada-core"
                      style={{
                        rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.22 : 0,
                        rasterContrast: 0.12,
                        rasterSaturation: -0.24,
                        rasterBrightnessMin: 0.16,
                        rasterBrightnessMax: 0.8,
                      }}
                    />
                  </RasterSource>
                )}
                <RasterSource
                  id="overlay-source-depth-contours"
                  tileUrlTemplates={[url]}
                  tileSize={256}
                >
                  <RasterLayer
                    id="overlay-layer-depth-contours-shadow"
                    style={{
                      rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.22 : 0,
                      rasterContrast: 0.28,
                      rasterSaturation: -0.62,
                      rasterBrightnessMin: 0.0,
                      rasterBrightnessMax: 0.18,
                    }}
                  />
                  <RasterLayer
                    id="overlay-layer-depth-contours-mid"
                    style={{
                      rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.28 : 0,
                      rasterContrast: 0.12,
                      rasterSaturation: -0.34,
                      rasterBrightnessMin: 0.08,
                      rasterBrightnessMax: 0.48,
                    }}
                  />
                  <RasterLayer
                    id="overlay-layer-depth-contours-core"
                    style={{
                      rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.14 : 0,
                      rasterContrast: 0.08,
                      rasterBrightnessMin: 0.24,
                      rasterBrightnessMax: 0.96,
                    }}
                  />
                  <RasterLayer
                    id="overlay-layer-depth-contours-crest"
                    style={{
                      rasterOpacity: SHOW_RASTER_OCEAN_UNDERLAY ? 0.1 : 0,
                      rasterContrast: 0.02,
                      rasterBrightnessMin: 0.58,
                      rasterBrightnessMax: 1,
                    }}
                  />
                </RasterSource>
                {ENABLE_VECTOR_OCEAN_BATHY && VectorSource && LineLayer && SymbolLayer && (
                  <VectorSource
                    id="overlay-source-depth-contours-vector"
                    url={buildTileSourceUrl(TILE_LAYER_NAMES.oceanContours)!}
                    maxZoomLevel={14}
                  >
                    <LineLayer
                      id="overlay-ocean-line-shadow"
                      sourceLayerID="contours"
                      filter={contourFeatureFilter('contour_line') as any}
                      style={{
                        lineColor: bathymetryShadowExpression,
                        lineWidth: [
                          'interpolate',
                          ['linear'],
                          ['zoom'],
                          5, 0.7,
                          8, 1.05,
                          11, 1.45,
                          14, 2,
                        ] as any,
                        lineBlur: 1.2,
                        lineOpacity: contourSettings.opacity * 0.12 * oceanContourVisualOpacityScale,
                        lineTranslate: [0, 1.4],
                        lineTranslateAnchor: 'viewport',
                        lineJoin: 'round',
                        lineCap: 'round',
                      }}
                    />
                    <LineLayer
                      id="overlay-ocean-line"
                      sourceLayerID="contours"
                      filter={contourFeatureFilter('contour_line') as any}
                      style={{
                        lineColor: bathymetryFillExpression,
                        lineWidth: [
                          'interpolate',
                          ['linear'],
                          ['zoom'],
                          5, 0.42,
                          8, 0.7,
                          11, 1.02,
                          14, 1.4,
                        ] as any,
                        lineOpacity: contourSettings.opacity * (navPresentationActive ? 0.72 : 0.9) * oceanContourVisualOpacityScale,
                        lineJoin: 'round',
                        lineCap: 'round',
                      }}
                    />
                    <LineLayer
                      id="overlay-ocean-line-crest"
                      sourceLayerID="contours"
                      filter={contourFeatureFilter('contour_line') as any}
                      style={{
                        lineColor: 'rgba(244, 251, 254, 0.82)',
                        lineWidth: [
                          'interpolate',
                          ['linear'],
                          ['zoom'],
                          5, 0.1,
                          8, 0.16,
                          11, 0.22,
                          14, 0.28,
                        ] as any,
                        lineOpacity: contourSettings.opacity * (navPresentationActive ? 0.1 : 0.22) * oceanContourVisualOpacityScale,
                        lineJoin: 'round',
                        lineCap: 'round',
                      }}
                    />
                    {shouldShowChartLabels && (
                      <SymbolLayer
                        id="overlay-ocean-line-label"
                        sourceLayerID="contours"
                        filter={contourFeatureFilter('contour_line') as any}
                        minZoomLevel={10}
                        style={{
                          symbolPlacement: 'line',
                          symbolSpacing: [
                            'interpolate',
                            ['linear'],
                            ['zoom'],
                            10, 260,
                            12, 180,
                            14, 120,
                            16, 80,
                          ] as any,
                          textField: ['coalesce', ['get', 'label'], ['concat', ['to-string', ['get', 'depth_ft']], ' ft']] as any,
                          textSize: [
                            'interpolate',
                            ['linear'],
                            ['zoom'],
                            10, 9,
                            12, 10.5,
                            15, 12,
                          ] as any,
                          textColor: 'rgba(36, 66, 86, 0.88)',
                          textHaloColor: 'rgba(252, 248, 239, 0.98)',
                          textHaloWidth: 1.35,
                          textOpacity: contourSettings.opacity * 0.9 * oceanContourVisualOpacityScale,
                          textFont: FONT_STACKS.bold,
                          textKeepUpright: true,
                          textOptional: true,
                          textAllowOverlap: false,
                          textIgnorePlacement: false,
                          textPadding: 4,
                        }}
                      />
                    )}
                    {shouldShowChartLabels && (
                    <SymbolLayer
                      id="overlay-ocean-label"
                      sourceLayerID="contours"
                      filter={contourFeatureFilter('depth_label') as any}
                      minZoomLevel={8}
                      style={{
                        textField: ['coalesce', ['get', 'label'], ['concat', ['to-string', ['get', 'depth_ft']], ' ft']] as any,
                        textSize: [
                          'interpolate',
                          ['linear'],
                          ['zoom'],
                          8, 9,
                          11, 10.5,
                          14, 12,
                        ] as any,
                        textColor: '#244256',
                        textHaloColor: '#FCF8EF',
                        textHaloWidth: 1.6,
                        textOpacity: contourSettings.opacity * 0.72 * oceanContourVisualOpacityScale,
                        textFont: FONT_STACKS.bold,
                        textAllowOverlap: false,
                        textIgnorePlacement: false,
                        textOptional: true,
                      }}
                    />
                    )}
                  </VectorSource>
                )}
              </React.Fragment>
            );
          }
          const opacity =
            key === 'nautical' ? 0.7
            : key === 'shaded-relief' ? 0.5
            : key === 'water-flow' ? 0.7
            : 0.6;
          return (
            <RasterSource
              key={`src-${key}`}
              id={`overlay-source-${key}`}
              tileUrlTemplates={[url]}
              tileSize={256}
            >
              <RasterLayer
                id={`overlay-layer-${key}`}
                style={{ rasterOpacity: opacity }}
              />
            </RasterSource>
          );
        })}

        {activeOverlays.has('marine-navigation') &&
          VectorSource &&
          FillLayer &&
          LineLayer &&
          SymbolLayer &&
          buildTileTemplateUrl('noaa_marine_navigation') && (
            <VectorSource
              id="overlay-source-marine-navigation"
              tileUrlTemplates={[buildTileTemplateUrl('noaa_marine_navigation')!]}
              maxZoomLevel={14}
            >
              <FillLayer
                id="overlay-marine-channel-fill-shadow"
                sourceLayerID="marine_navigation"
                filter={['==', ['get', 'feature_kind'], 'maintained_channel'] as any}
                style={{
                  fillColor: bathymetryShadowExpression,
                  fillOpacity: 0.18,
                  fillTranslate: [0, 3],
                  fillTranslateAnchor: 'viewport',
                }}
              />
              <FillLayer
                id="overlay-marine-channel-fill"
                sourceLayerID="marine_navigation"
                filter={['==', ['get', 'feature_kind'], 'maintained_channel'] as any}
                style={{
                  fillColor: bathymetryFillExpression,
                  fillOpacity: 0.34,
                  fillOutlineColor: 'rgba(12, 68, 95, 0.28)',
                }}
              />
              <LineLayer
                id="overlay-marine-channel-line"
                sourceLayerID="marine_navigation"
                filter={['==', ['get', 'feature_kind'], 'maintained_channel'] as any}
                style={{
                  lineColor: '#0F5D7A',
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    5, 0.8,
                    8, 1.4,
                    12, 2.3,
                    15, 3.1,
                  ] as any,
                  lineOpacity: 0.78,
                }}
              />
              <LineLayer
                id="overlay-marine-shipping-line"
                sourceLayerID="marine_navigation"
                filter={['==', ['get', 'feature_kind'], 'shipping_regulation'] as any}
                style={{
                  lineColor: '#C4841D',
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    5, 0.7,
                    8, 1.2,
                    12, 1.9,
                    15, 2.4,
                  ] as any,
                  lineDasharray: [2, 1.4],
                  lineOpacity: 0.86,
                }}
              />
              <LineLayer
                id="overlay-marine-boundary-line"
                sourceLayerID="marine_navigation"
                filter={['==', ['get', 'feature_kind'], 'maritime_boundary'] as any}
                style={{
                  lineColor: '#113A52',
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    3, 0.8,
                    6, 1,
                    10, 1.4,
                    14, 1.8,
                  ] as any,
                  lineDasharray: [3.2, 1.2],
                  lineOpacity: 0.7,
                }}
              />
              <SymbolLayer
                id="overlay-marine-channel-label"
                sourceLayerID="marine_navigation"
                filter={['all', ['==', ['get', 'feature_kind'], 'maintained_channel'], ['has', 'channel_name']] as any}
                minZoomLevel={9}
                style={{
                  textField: ['get', 'channel_name'],
                  textSize: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    9, 9,
                    12, 10.5,
                    15, 12,
                  ] as any,
                  textColor: '#143C52',
                  textHaloColor: '#F8F1E6',
                  textHaloWidth: 1.1,
                  textOpacity: 0.86,
                  textFont: FONT_STACKS.bold,
                  textAllowOverlap: false,
                  textIgnorePlacement: false,
                  textOptional: true,
                }}
              />
            </VectorSource>
          )}

        {activeOverlays.has('river-network') &&
          RasterSource &&
          RasterLayer && (
            <React.Fragment key="src-river-network-backbone">
              <RasterSource
                id="overlay-source-river-network-us"
                tileUrlTemplates={[OVERLAY_TILE_URLS['water-flow']]}
                tileSize={256}
              >
                <RasterLayer
                  id="overlay-layer-river-network-us"
                  style={{
                    rasterOpacity: 0.56,
                    rasterSaturation: -0.28,
                    rasterContrast: 0.08,
                  }}
                />
              </RasterSource>
              <RasterSource
                id="overlay-source-river-network-canada"
                tileUrlTemplates={[canadianRiverNetworkSource.urlTemplate]}
                tileSize={256}
              >
                <RasterLayer
                  id="overlay-layer-river-network-canada"
                  style={{
                    rasterOpacity: 0.52,
                    rasterSaturation: -0.24,
                    rasterContrast: 0.06,
                  }}
                />
              </RasterSource>
            </React.Fragment>
          )}

        {activeOverlays.has('river-network') &&
          isVectorOverlayReady('river-network') &&
          VectorSource &&
          LineLayer &&
          SymbolLayer &&
          buildTileTemplateUrl(TILE_LAYER_NAMES.riverNetwork) && (
            <VectorSource
              id="overlay-source-river-network-vector"
              tileUrlTemplates={[buildTileTemplateUrl(TILE_LAYER_NAMES.riverNetwork)!]}
              maxZoomLevel={13}
            >
              <LineLayer
                id="overlay-river-network-shadow"
                sourceLayerID="river_network"
                style={{
                  lineColor: bathymetryShadowExpression,
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    4, 1.2,
                    7, 1.7,
                    10, 2.4,
                    13, 3.2,
                  ] as any,
                  lineBlur: 1.8,
                  lineOpacity: 0.22,
                  lineTranslate: [0, 1.6],
                  lineTranslateAnchor: 'viewport',
                  lineJoin: 'round',
                  lineCap: 'round',
                }}
              />
              <LineLayer
                id="overlay-river-network-line"
                sourceLayerID="river_network"
                style={{
                  lineColor: [
                    'match',
                    ['get', 'river_class'],
                    'trunk', '#3D5F7B',
                    'major', '#557A96',
                    'secondary', '#7295AE',
                    '#93B1C5',
                  ] as any,
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    4, 0.45,
                    7, 0.7,
                    10, 1.05,
                    13, 1.45,
                  ] as any,
                  lineOpacity: 0.84,
                  lineJoin: 'round',
                  lineCap: 'round',
                }}
              />
              <SymbolLayer
                id="overlay-river-network-label"
                sourceLayerID="river_network"
                minZoomLevel={7}
                filter={['all', ['has', 'river_name'], ['!=', ['get', 'river_name'], '']] as any}
                style={{
                  textField: ['get', 'river_name'],
                  textSize: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    7, 9,
                    10, 10.5,
                    13, 12,
                  ] as any,
                  textColor: '#315A72',
                  textHaloColor: '#F7F1E6',
                  textHaloWidth: 1,
                  textOpacity: 0.82,
                  textFont: FONT_STACKS.regular,
                  symbolPlacement: 'line',
                  textAllowOverlap: false,
                  textIgnorePlacement: false,
                  textOptional: true,
                }}
              />
            </VectorSource>
          )}

        {activeOverlays.has('river-navigation') &&
          VectorSource &&
          FillLayer &&
          LineLayer &&
          SymbolLayer &&
          CircleLayer &&
          buildTileTemplateUrl('usace_river_navigation') && (
            <VectorSource
              id="overlay-source-river-navigation"
              tileUrlTemplates={[buildTileTemplateUrl('usace_river_navigation')!]}
              maxZoomLevel={14}
            >
              <FillLayer
                id="overlay-river-depth-fill-shadow"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'depth_area'] as any}
                style={{
                  fillColor: bathymetryShadowExpression,
                  fillOpacity: 0.2,
                  fillTranslate: [0, 3],
                  fillTranslateAnchor: 'viewport',
                }}
              />
              <FillLayer
                id="overlay-river-depth-fill"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'depth_area'] as any}
                style={{
                  fillColor: bathymetryFillExpression,
                  fillOpacity: 0.4,
                  fillOutlineColor: 'rgba(69, 92, 126, 0.24)',
                }}
              />
              <LineLayer
                id="overlay-river-contour-shadow"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'depth_contour'] as any}
                style={{
                  lineColor: bathymetryShadowExpression,
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    6, 1.2,
                    9, 1.8,
                    12, 2.5,
                    15, 3.4,
                  ] as any,
                  lineBlur: 2.1,
                  lineOpacity: 0.26,
                  lineTranslate: [0, 2],
                  lineTranslateAnchor: 'viewport',
                }}
              />
              <LineLayer
                id="overlay-river-contour-line"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'depth_contour'] as any}
                style={{
                  lineColor: '#314F77',
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    6, 0.55,
                    9, 0.9,
                    12, 1.2,
                    15, 1.6,
                  ] as any,
                  lineOpacity: 0.92,
                }}
              />
              <LineLayer
                id="overlay-river-land-edge"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'land_area'] as any}
                style={{
                  lineColor: 'rgba(115, 98, 72, 0.72)',
                  lineWidth: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    6, 0.4,
                    9, 0.7,
                    12, 1.1,
                    15, 1.5,
                  ] as any,
                  lineOpacity: 0.7,
                }}
              />
              <CircleLayer
                id="overlay-river-wrecks"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'wreck'] as any}
                style={{
                  circleColor: '#C44B4B',
                  circleStrokeColor: '#F8F1E6',
                  circleStrokeWidth: 1,
                  circleRadius: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    8, 1.8,
                    11, 3.2,
                    14, 4.8,
                  ] as any,
                  circleOpacity: 0.9,
                }}
              />
              <CircleLayer
                id="overlay-river-bridges"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'bridge'] as any}
                style={{
                  circleColor: '#7D5BA6',
                  circleStrokeColor: '#F8F1E6',
                  circleStrokeWidth: 1,
                  circleRadius: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    8, 1.6,
                    11, 2.8,
                    14, 4.2,
                  ] as any,
                  circleOpacity: 0.84,
                }}
              />
              <SymbolLayer
                id="overlay-river-depth-label"
                sourceLayerID="river_navigation"
                filter={['==', ['get', 'feature_kind'], 'depth_label'] as any}
                minZoomLevel={10}
                style={{
                  textField: ['coalesce', ['get', 'label'], ['concat', ['to-string', ['get', 'depth_ft']], ' ft']] as any,
                  textSize: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    10, 8.5,
                    13, 10,
                    15, 11,
                  ] as any,
                  textColor: '#2E4768',
                  textHaloColor: '#F8F1E6',
                  textHaloWidth: 1.1,
                  textOpacity: 0.84,
                  textFont: FONT_STACKS.bold,
                  textAllowOverlap: false,
                  textIgnorePlacement: false,
                  textOptional: true,
                }}
              />
            </VectorSource>
          )}

        {/* Bathymetry contour overlays from Martin PMTiles */}
        {shouldRenderLocalBathymetry &&
          VectorSource &&
          LineLayer &&
          SymbolLayer &&
          CircleLayer &&
          (() => {
            return activeContourSources.map(({ id: src }) => (
              <VectorSource
                key={src}
                id={`bathy-${src}`}
                tileUrlTemplates={[buildTileTemplateUrl(src)!]}
                maxZoomLevel={16}
              >
                <LineLayer
                  id={`bathy-band-shadow-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('contour_line') as any}
                  style={{
                    lineColor: bathymetryShadowExpression,
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      5, 1.6,
                      8, 3.4,
                      11, 6.2,
                      14, 10.2,
                    ] as any,
                    lineBlur: 3.6,
                    lineOpacity: contourSettings.opacity * 0.06 * bathyVisualOpacityScale,
                    lineJoin: 'round',
                    lineCap: 'round',
                  }}
                />
                <LineLayer
                  id={`bathy-band-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('contour_line') as any}
                  style={{
                    lineColor: bathymetryFillExpression,
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      5, 0.9,
                      8, 1.8,
                      11, 3.2,
                      14, 5.2,
                    ] as any,
                    lineBlur: 1.1,
                    lineOpacity: contourSettings.opacity * (navPresentationActive ? 0.08 : 0.14) * bathyVisualOpacityScale,
                    lineJoin: 'round',
                    lineCap: 'round',
                  }}
                />
                <LineLayer
                  id={`bathy-line-shadow-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('contour_line') as any}
                  style={{
                    lineColor: bathymetryShadowExpression,
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      5, 0.7,
                      8, 1.05,
                      11, 1.45,
                      14, 2,
                    ] as any,
                    lineBlur: 1.2,
                    lineOpacity: contourSettings.opacity * 0.12 * bathyVisualOpacityScale,
                  }}
                />
                <LineLayer
                  id={`bathy-line-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('contour_line') as any}
                  style={{
                    lineColor: bathymetryFillExpression,
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      5, 0.38,
                      8, 0.62,
                      11, 0.92,
                      14, 1.25,
                    ] as any,
                    lineOpacity: contourSettings.opacity * (navPresentationActive ? 0.58 : 0.74) * bathyVisualOpacityScale,
                    lineJoin: 'round',
                    lineCap: 'round',
                  }}
                />
                <LineLayer
                  id={`bathy-line-crest-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('contour_line') as any}
                  style={{
                    lineColor: 'rgba(240, 248, 252, 0.78)',
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      5, 0.12,
                      8, 0.18,
                      11, 0.24,
                      14, 0.32,
                    ] as any,
                    lineOpacity: contourSettings.opacity * (navPresentationActive ? 0.14 : 0.3) * bathyVisualOpacityScale,
                    lineJoin: 'round',
                    lineCap: 'round',
                  }}
                />
                {shouldShowChartLabels && (
                  <SymbolLayer
                    id={`bathy-line-label-${src}`}
                    sourceLayerID="contours"
                    filter={contourFeatureFilter('contour_line') as any}
                    minZoomLevel={10}
                    style={{
                      symbolPlacement: 'line',
                      symbolSpacing: [
                        'interpolate',
                        ['linear'],
                        ['zoom'],
                        10, 260,
                        12, 180,
                        14, 120,
                        16, 80,
                      ] as any,
                      textField: ['coalesce', ['get', 'label'], ['concat', ['to-string', ['get', 'depth_ft']], ' ft']] as any,
                      textSize: [
                        'interpolate',
                        ['linear'],
                        ['zoom'],
                        10, 8.5,
                        12, 10,
                        15, 11.5,
                      ] as any,
                      textColor: 'rgba(36, 66, 86, 0.78)',
                      textHaloColor: 'rgba(252, 248, 239, 0.95)',
                      textHaloWidth: 1.1,
                      textOpacity: contourSettings.opacity * 0.74 * bathyVisualOpacityScale,
                      textFont: FONT_STACKS.bold,
                      textKeepUpright: true,
                      textOptional: true,
                      textAllowOverlap: false,
                      textIgnorePlacement: false,
                      textPadding: 4,
                    }}
                  />
                )}
                {shouldShowChartLabels && (
                  <SymbolLayer
                    id={`bathy-label-${src}`}
                    sourceLayerID="contours"
                    filter={contourFeatureFilter('depth_label') as any}
                    minZoomLevel={8}
                    style={{
                      textField: ['coalesce', ['get', 'label'], ['to-string', ['get', 'depth_ft']]] as any,
                      textSize: [
                        'interpolate',
                        ['linear'],
                        ['zoom'],
                        8, 9.5,
                        11, 11,
                        14, 12.5,
                      ] as any,
                      textColor: '#314F67',
                      textHaloColor: '#FCF8EF',
                      textHaloWidth: 1.35,
                      textOpacity: contourSettings.opacity * 0.84 * bathyVisualOpacityScale,
                      textFont: FONT_STACKS.bold,
                      textAllowOverlap: false,
                      textIgnorePlacement: false,
                      textOptional: true,
                    }}
                  />
                )}
                {shouldShowBathymetryDetails && (
                <CircleLayer
                  id={`bathy-sounding-${src}`}
                  sourceLayerID="contours"
                  filter={contourFeatureFilter('depth_point') as any}
                  style={{
                    circleColor: bathymetryFillExpression,
                    circleStrokeColor: '#F8F1E6',
                    circleStrokeWidth: 0.9,
                    circleRadius: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      7, 0.6,
                      10, 1.4,
                      13, 2.2,
                      16, 3.1,
                    ] as any,
                    circleOpacity: contourSettings.opacity * 0.78 * bathyVisualOpacityScale,
                  }}
                />
                )}
                {shouldShowBathymetryDetails && shouldShowChartLabels && (
                  <SymbolLayer
                    id={`bathy-sounding-label-${src}`}
                    sourceLayerID="contours"
                    filter={contourFeatureFilter('depth_point') as any}
                    minZoomLevel={13}
                    style={{
                      textField: ['concat', ['to-string', ['get', 'depth_ft']], ' ft'] as any,
                      textSize: [
                        'interpolate',
                        ['linear'],
                        ['zoom'],
                        13, 8,
                        15, 9.5,
                        17, 10.5,
                      ] as any,
                      textOffset: [0, 1.2],
                      textColor: '#143C52',
                      textHaloColor: '#F8F1E6',
                      textHaloWidth: 1.05,
                      textOpacity: contourSettings.opacity * 0.8 * bathyVisualOpacityScale,
                      textFont: FONT_STACKS.bold,
                      textAllowOverlap: false,
                      textIgnorePlacement: false,
                      textOptional: true,
                    }}
                  />
                )}
              </VectorSource>
            ));
          })()
        }

        {ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS &&
          isVectorOverlayReady('public-lands') &&
          activeOverlays.has('public-lands') &&
          VectorSource &&
          FillLayer &&
          LineLayer && (
          <VectorSource
            id="public-lands-source"
            url={buildTileSourceUrl(TILE_LAYER_NAMES.publicLands)!}
            maxZoomLevel={14}
          >
            <FillLayer
              id="public-lands-fill"
              sourceLayerID={TILE_LAYER_NAMES.publicLands}
              style={{
                fillColor: '#6E9E4B',
                fillOpacity: 0.18,
              }}
            />
            <LineLayer
              id="public-lands-outline"
              sourceLayerID={TILE_LAYER_NAMES.publicLands}
              style={{
                lineColor: '#567B39',
                lineWidth: 1,
                lineOpacity: 0.45,
              }}
            />
          </VectorSource>
        )}

        {ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS &&
          isVectorOverlayReady('access-points') &&
          (activeOverlays.has('access-points') || activeOverlays.has('parking') || activeOverlays.has('trails')) &&
          VectorSource && LineLayer && CircleLayer && (
            <VectorSource
              id="access-points-source"
              url={buildTileSourceUrl(TILE_LAYER_NAMES.accessPoints)!}
              maxZoomLevel={14}
            >
              {activeOverlays.has('trails') && (
                <LineLayer
                  id="access-trails-layer"
                  sourceLayerID={TILE_LAYER_NAMES.accessPoints}
                  filter={[
                    'all',
                    ['==', ['get', 'access_type'], 'trail'],
                    ['<=', ['to-number', ['coalesce', ['get', 'distance_to_water_m'], 9999]], 220],
                  ] as any}
                  style={{
                    lineColor: '#C4841D',
                    lineWidth: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      9, 0.8,
                      14, 2.2,
                    ] as any,
                    lineOpacity: 0.85,
                    lineDasharray: [2, 1],
                  }}
                />
              )}
              {activeOverlays.has('parking') && (
                <CircleLayer
                  id="access-parking-layer"
                  sourceLayerID={TILE_LAYER_NAMES.accessPoints}
                  filter={[
                    'all',
                    ['==', ['get', 'access_type'], 'parking'],
                    ['<=', ['to-number', ['coalesce', ['get', 'distance_to_water_m'], 9999]], 140],
                  ] as any}
                  style={{
                    circleColor: '#E7B84B',
                    circleRadius: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      7, 2.5,
                      13, 5.5,
                    ] as any,
                    circleStrokeColor: '#6B4B1D',
                    circleStrokeWidth: 1,
                    circleOpacity: 0.95,
                  }}
                />
              )}
              {activeOverlays.has('access-points') && (
                <CircleLayer
                  id="access-points-layer"
                  sourceLayerID={TILE_LAYER_NAMES.accessPoints}
                  filter={[
                    'all',
                    ['match', ['get', 'access_type'], ['boat_launch', 'shore_access', 'campground'], true, false],
                    ['<=', ['to-number', ['coalesce', ['get', 'distance_to_water_m'], 9999]], 180],
                  ] as any}
                  style={{
                    circleColor: [
                      'match',
                      ['get', 'access_type'],
                      'boat_launch', '#2563EB',
                      'shore_access', '#0D9488',
                      'campground', '#16A34A',
                      '#2563EB',
                    ] as any,
                    circleRadius: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      7, 3,
                      13, 6.5,
                    ] as any,
                    circleStrokeColor: '#FFFFFF',
                    circleStrokeWidth: 1.2,
                    circleOpacity: 0.95,
                  }}
                />
              )}
            </VectorSource>
          )}

        {/* ── Chart Annotations on the map ──────────────────────────── */}
        {showAnnotations && annotations.length > 0 && ShapeSource && CircleLayer && (
          <>
            {/* Marker / text annotations as symbols */}
            <ShapeSource
              id="annotation-markers-source"
              shape={markerAnnotationsGeoJSON(annotations) as any}
            >
              <CircleLayer
                id="annotation-markers-circles"
                style={{
                  circleColor: ['get', 'color'] as any,
                  circleRadius: 8,
                  circleStrokeColor: '#FFFFFF',
                  circleStrokeWidth: 2,
                  circleOpacity: 0.9,
                }}
              />
            </ShapeSource>

            {/* Arrow annotations as lines */}
            {LineLayer && (
              <ShapeSource
                id="annotation-arrows-source"
                shape={arrowAnnotationsGeoJSON(annotations) as any}
              >
                <LineLayer
                  id="annotation-arrows-lines"
                  style={{
                    lineColor: ['get', 'color'] as any,
                    lineWidth: 3,
                    lineOpacity: 0.85,
                  }}
                />
              </ShapeSource>
            )}

            {/* Circle annotations as points with radius */}
            <ShapeSource
              id="annotation-circles-source"
              shape={circleAnnotationsGeoJSON(annotations) as any}
            >
              <CircleLayer
                id="annotation-circles-fill"
                style={{
                  circleColor: ['get', 'color'] as any,
                  circleRadius: [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    6, 4,
                    10, 12,
                    14, 24,
                  ] as any,
                  circleOpacity: 0.2,
                  circleStrokeColor: ['get', 'color'] as any,
                  circleStrokeWidth: 2,
                  circleStrokeOpacity: 0.7,
                }}
              />
            </ShapeSource>
          </>
        )}

        {(routeLineGeoJSON || routePointGeoJSON) &&
          ShapeSource &&
          LineLayer &&
          CircleLayer &&
          SymbolLayer && (
          <>
            {routeCorridorGeoJSON && FillLayer && (
              <ShapeSource id="route-corridor-source" shape={routeCorridorGeoJSON as any}>
                <FillLayer
                  id="route-corridor-fill"
                  style={{
                    fillColor: ['get', 'fillColor'],
                    fillOpacity: navPresentationActive ? 0.18 : 0.11,
                  }}
                />
                <LineLayer
                  id="route-corridor-outline"
                  style={{
                    lineColor: ['get', 'lineColor'],
                    lineWidth: navPresentationActive ? 1.4 : 1,
                    lineOpacity: navPresentationActive ? 0.55 : 0.32,
                  }}
                />
              </ShapeSource>
            )}

            {routeLineGeoJSON && (
              <ShapeSource id="route-line-source" shape={routeLineGeoJSON as any}>
                <LineLayer
                  id="route-line-shadow"
                  style={{
                    lineColor: 'rgba(10, 47, 78, 0.28)',
                    lineWidth: 6,
                    lineOpacity: 0.9,
                  }}
                />
                <LineLayer
                  id="route-line"
                  style={{
                    lineColor: '#1F6FB2',
                    lineWidth: 3.2,
                    lineOpacity: 0.96,
                  }}
                />
              </ShapeSource>
            )}

            {!routeNavActive && PointAnnotation && routePoints.map((point, index) => {
              const markerKind =
                index === 0 ? 'start' : index === routePoints.length - 1 ? 'end' : 'waypoint';
              const label =
                markerKind === 'start'
                  ? 'A'
                  : markerKind === 'end'
                    ? 'B'
                    : `${index}`;
              const bgColor =
                markerKind === 'start'
                  ? '#1E8E5A'
                  : markerKind === 'end'
                    ? '#C44B4B'
                    : '#1F6FB2';
              return (
                <PointAnnotation
                  key={`route-handle-${index}`}
                  id={`route-handle-${index}`}
                  coordinate={[point.lon, point.lat]}
                  draggable
                  anchor={{ x: 0.5, y: 0.5 }}
                  onDragEnd={(event: any) => handleDragRoutePoint(index, event)}
                >
                  <View style={[styles.routeHandlePin, { backgroundColor: bgColor }]}>
                    <Text style={styles.routeHandlePinText}>{label}</Text>
                  </View>
                </PointAnnotation>
              );
            })}

            {routePointGeoJSON && (
              <ShapeSource id="route-point-source" shape={routePointGeoJSON as any}>
                <CircleLayer
                  id="route-point-layer"
                  style={{
                    circleRadius: [
                      'match',
                      ['get', 'markerKind'],
                      'start', 10,
                      'end', 10,
                      8,
                    ] as any,
                    circleColor: [
                      'match',
                      ['get', 'markerKind'],
                      'start', '#1E8E5A',
                      'end', '#C44B4B',
                      '#FFFFFF',
                    ] as any,
                    circleStrokeColor: [
                      'match',
                      ['get', 'markerKind'],
                      'start', '#F3FBF7',
                      'end', '#FFF4F4',
                      '#1F6FB2',
                    ] as any,
                    circleStrokeWidth: 3,
                  }}
                />
                <SymbolLayer
                  id="route-point-labels"
                  style={{
                    textField: ['get', 'shortLabel'] as any,
                    textColor: [
                      'match',
                      ['get', 'markerKind'],
                      'waypoint', '#1F6FB2',
                      '#FFFFFF',
                    ] as any,
                    textSize: 11,
                    textFont: FONT_STACKS.bold,
                    textAllowOverlap: true,
                    textIgnorePlacement: true,
                  }}
                />
                <SymbolLayer
                  id="route-point-captions"
                  minZoomLevel={8}
                  style={{
                    textField: ['get', 'label'] as any,
                    textColor: '#355B75',
                    textHaloColor: '#FFFFFF',
                    textHaloWidth: 1.2,
                    textSize: 10,
                    textFont: FONT_STACKS.bold,
                    textOffset: [0, 2.1],
                    textAnchor: 'top',
                    textAllowOverlap: true,
                    textIgnorePlacement: true,
                  }}
                />
              </ShapeSource>
            )}

            {pendingRouteDestinationGeoJSON && (
              <ShapeSource id="route-pending-destination-source" shape={pendingRouteDestinationGeoJSON as any}>
                <CircleLayer
                  id="route-pending-destination-layer"
                  style={{
                    circleRadius: 10,
                    circleColor: '#C44B4B',
                    circleStrokeColor: '#FFF4F4',
                    circleStrokeWidth: 3,
                  }}
                />
                <SymbolLayer
                  id="route-pending-destination-label"
                  style={{
                    textField: 'B',
                    textColor: '#FFFFFF',
                    textSize: 11,
                    textFont: FONT_STACKS.bold,
                    textAllowOverlap: true,
                    textIgnorePlacement: true,
                  }}
                />
                <SymbolLayer
                  id="route-pending-destination-caption"
                  minZoomLevel={8}
                  style={{
                    textField: ['get', 'label'] as any,
                    textColor: '#355B75',
                    textHaloColor: '#FFFFFF',
                    textHaloWidth: 1.2,
                    textSize: 10,
                    textFont: FONT_STACKS.bold,
                    textOffset: [0, 2.1],
                    textAnchor: 'top',
                    textAllowOverlap: true,
                    textIgnorePlacement: true,
                  }}
                />
              </ShapeSource>
            )}

            {routeWarningGeoJSON && (
              <ShapeSource id="route-warning-source" shape={routeWarningGeoJSON as any}>
                <CircleLayer
                  id="route-warning-layer"
                  style={{
                    circleRadius: [
                      'interpolate',
                      ['linear'],
                      ['zoom'],
                      8, 8,
                      11, 12,
                      14, 18,
                    ] as any,
                    circleColor: [
                      'match',
                      ['get', 'severity'],
                      'danger', 'rgba(196, 75, 75, 0.34)',
                      'rgba(201, 106, 24, 0.24)',
                    ] as any,
                    circleStrokeColor: [
                      'match',
                      ['get', 'severity'],
                      'danger', '#C44B4B',
                      '#C96A18',
                    ] as any,
                    circleStrokeWidth: 1.4,
                  }}
                />
                <SymbolLayer
                  id="route-warning-short-label"
                  minZoomLevel={10}
                  style={{
                    textField: ['get', 'shortLabel'] as any,
                    textColor: [
                      'match',
                      ['get', 'severity'],
                      'danger', '#8A1F1F',
                      '#8A4D12',
                    ] as any,
                    textSize: 10,
                    textFont: FONT_STACKS.bold,
                    textAllowOverlap: true,
                    textIgnorePlacement: true,
                  }}
                />
                <SymbolLayer
                  id="route-warning-caption"
                  minZoomLevel={11}
                  style={{
                    textField: ['get', 'label'] as any,
                    textColor: [
                      'match',
                      ['get', 'severity'],
                      'danger', '#8A1F1F',
                      '#8A4D12',
                    ] as any,
                    textHaloColor: '#FFF7F2',
                    textHaloWidth: 1.15,
                    textSize: 10.5,
                    textFont: FONT_STACKS.bold,
                    textOffset: [0, 1.9],
                    textAnchor: 'top',
                    textAllowOverlap: false,
                    textIgnorePlacement: false,
                    textOptional: true,
                  }}
                />
              </ShapeSource>
            )}
          </>
        )}

        {/* Fishing location markers — clustered for smooth zoomed-out rendering */}
        {markerMode === 'locations' && !navPresentationActive && !routePlannerVisible && ShapeSource && CircleLayer && SymbolLayer && (
          <ShapeSource
            ref={locationSourceRef}
            id="location-marker-source"
            shape={locationGeoJSON as any}
            cluster
            clusterRadius={42}
            clusterMaxZoomLevel={11}
            hitbox={{ width: 24, height: 24 }}
            onPress={handleLocationSourcePress}
          >
            <CircleLayer
              id="location-clusters-layer"
              filter={['has', 'point_count'] as any}
              style={{
                circleColor: [
                  'step',
                  ['get', 'point_count'],
                  '#2D6A8E',
                  25, '#1F5E7C',
                  100, '#15465C',
                ] as any,
                circleRadius: [
                  'step',
                  ['get', 'point_count'],
                  18,
                  25, 22,
                  100, 28,
                ] as any,
                circleStrokeColor: 'rgba(255,255,255,0.85)',
                circleStrokeWidth: 1.5,
                circleOpacity: 0.92,
              }}
            />
            <SymbolLayer
              id="location-cluster-count-layer"
              filter={['has', 'point_count'] as any}
              style={{
                textField: ['get', 'point_count_abbreviated'] as any,
                textFont: FONT_STACKS.bold,
                textColor: '#FFFFFF',
                textSize: 12,
              }}
            />
            {/* Backend / verified location pins — solid fill */}
            <CircleLayer
              id="location-unclustered-layer"
              filter={['all', ['!', ['has', 'point_count']], ['!=', ['get', 'isDiscovered'], 1]] as any}
              style={{
                circleColor: ['get', 'displayColor'] as any,
                circleRadius: 6,
                circleStrokeColor: '#FFFFFF',
                circleStrokeWidth: 1.2,
                circleOpacity: 0.96,
              }}
            />
            {/* OSM / unseen / discovered pins — hollow circle (border only, no fill) */}
            <CircleLayer
              id="location-discovered-layer"
              filter={['all', ['!', ['has', 'point_count']], ['==', ['get', 'isDiscovered'], 1]] as any}
              style={{
                circleColor: 'rgba(255,255,255,0)',
                circleRadius: 6,
                circleStrokeColor: ['get', 'displayColor'] as any,
                circleStrokeWidth: 2,
                circleOpacity: 1,
              }}
            />
          </ShapeSource>
        )}

        {/* Selected pin highlight — separate layer to avoid full GeoJSON rebuild */}
        {markerMode === 'locations' && !navPresentationActive && !routePlannerVisible && selectedPinGeoJSON && ShapeSource && CircleLayer && (
          <ShapeSource
            id="selected-pin-source"
            shape={selectedPinGeoJSON as any}
          >
            <CircleLayer
              id="selected-pin-layer"
              style={{
                circleColor: ['get', 'displayColor'] as any,
                circleRadius: 9,
                circleStrokeColor: '#FFFFFF',
                circleStrokeWidth: 2.5,
                circleOpacity: 1,
              }}
            />
          </ShapeSource>
        )}

        {/* Water body highlight glow when a spot is focused */}
        {focusedLocation && ShapeSource && FillLayer && LineLayer && (
          <ShapeSource
            id="water-highlight-source"
            shape={makeCircleGeoJSON(focusedLocation.lon, focusedLocation.lat, 0.6) as any}
          >
            <FillLayer
              id="water-highlight-fill"
              style={{
                fillColor: palette.accent,
                fillOpacity: waterHighlightOpacity * 0.15,
              }}
            />
            <LineLayer
              id="water-highlight-stroke"
              style={{
                lineColor: palette.accent,
                lineWidth: 2.5,
                lineOpacity: waterHighlightOpacity,
                lineDasharray: [4, 3] as any,
              }}
            />
          </ShapeSource>
        )}

        {/* Waypoint markers */}
        {markerMode === 'waypoints' &&
          filteredWaypoints.map((wp) => (
            <PointAnnotation
              key={wp.id}
              id={`wp-${wp.id}`}
              coordinate={[wp.lon, wp.lat]}
            >
              <WaypointMarkerView color={wp.color} ionicon={waypointIonicon(wp.icon)} />
              <Callout title={wp.name} />
            </PointAnnotation>
          ))}

        {/* Marina POI markers */}
        {marinasEnabled &&
          marinaPOIs.map((poi) => {
            const cfg = MARINA_POI_CONFIG[poi.type] ?? MARINA_POI_CONFIG.marina;
            return (
              <PointAnnotation
                key={`marina-${poi.id}`}
                id={`marina-${poi.id}`}
                coordinate={[poi.lon, poi.lat]}
                onSelected={() => setSelectedMarina(poi)}
                onDeselected={() => {
                  if (selectedMarina?.id === poi.id) setSelectedMarina(null);
                }}
              >
                <View style={[styles.marinaPinOuter, { backgroundColor: cfg.color }]}>
                  <Ionicons name={cfg.ionicon as any} size={14} color="#FFFFFF" />
                </View>
                <Callout title="">
                  <View style={styles.marinaCallout}>
                    <View style={styles.marinaCalloutHeader}>
                      <View style={[styles.marinaCalloutBadge, { backgroundColor: cfg.color }]}>
                        <Ionicons name={cfg.ionicon as any} size={10} color="#FFFFFF" />
                      </View>
                      <Text style={styles.marinaCalloutTitle} numberOfLines={1}>
                        {poi.name}
                      </Text>
                    </View>
                    <Text style={styles.marinaCalloutType}>{cfg.label}</Text>
                    {poi.distanceMiles != null && (
                      <Text style={styles.marinaCalloutDistance}>
                        {poi.distanceMiles.toFixed(1)} mi away
                      </Text>
                    )}
                    {poi.phone ? (
                      <View style={styles.marinaCalloutRow}>
                        <Ionicons name="call-outline" size={11} color={palette.textSecondary} />
                        <Text style={styles.marinaCalloutDetail}>{poi.phone}</Text>
                      </View>
                    ) : null}
                    {poi.amenities.length > 0 && (
                      <View style={styles.marinaCalloutRow}>
                        <Ionicons name="list-outline" size={11} color={palette.textSecondary} />
                        <Text style={styles.marinaCalloutDetail} numberOfLines={2}>
                          {formatAmenities(poi.amenities)}
                        </Text>
                      </View>
                    )}
                  </View>
                </Callout>
              </PointAnnotation>
            );
          })}

        {/* Catch photo markers */}
        {photosEnabled &&
          catchPhotos.map((cp) => (
            <PointAnnotation
              key={`catch-photo-${cp.id}`}
              id={`catch-photo-${cp.id}`}
              coordinate={[cp.lon, cp.lat]}
              onSelected={() => setSelectedCatchPhoto(cp)}
              onDeselected={() => {
                if (selectedCatchPhoto?.id === cp.id) setSelectedCatchPhoto(null);
              }}
            >
              <View style={styles.catchPhotoPin}>
                <View style={styles.catchPhotoInner}>
                  <Ionicons name="camera" size={14} color="#FFFFFF" />
                </View>
              </View>
              <Callout title="">
                <View style={styles.catchPhotoCallout}>
                  <Text style={styles.catchPhotoCalloutTitle} numberOfLines={1}>
                    {cp.species}
                  </Text>
                  {cp.weight != null && (
                    <Text style={styles.catchPhotoCalloutDetail}>{cp.weight} lbs</Text>
                  )}
                  <Text style={styles.catchPhotoCalloutDate}>
                    {new Date(cp.timestamp).toLocaleDateString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })}
                  </Text>
                  {cp.airTemp != null && (
                    <Text style={styles.catchPhotoCalloutDetail}>
                      {cp.airTemp}{'\u00B0'}F
                      {cp.windSpeed != null ? ` \u00B7 ${cp.windSpeed} mph` : ''}
                    </Text>
                  )}
                  {cp.bait && (
                    <Text style={styles.catchPhotoCalloutDetail}>
                      Bait: {cp.bait}
                    </Text>
                  )}
                </View>
              </Callout>
            </PointAnnotation>
          ))}

        {/* Measure mode: polyline + point markers */}
        {measureMode && measurePoints.length >= 2 && ShapeSource && LineLayer && (
          <ShapeSource
            id="measure-line-source"
            shape={{
              type: 'Feature',
              geometry: {
                type: 'LineString',
                coordinates: measurePoints.map(([lat, lng]) => [lng, lat]),
              },
              properties: {},
            }}
          >
            <LineLayer
              id="measure-line-layer"
              style={{
                lineColor: palette.accent,
                lineWidth: 2.5,
                lineDasharray: [4, 3],
              }}
            />
          </ShapeSource>
        )}
        {measureMode &&
          measurePoints.map((pt, i) => (
            <PointAnnotation
              key={`measure-pt-${i}`}
              id={`measure-pt-${i}`}
              coordinate={[pt[1], pt[0]]}
            >
              <View style={styles.measureDot} />
            </PointAnnotation>
          ))}

        {/* Access-point trail lines */}
        {accessEnabled && accessTrailGeoJSON && ShapeSource && LineLayer && (
          <ShapeSource id="access-trail-source" shape={accessTrailGeoJSON}>
            <LineLayer
              id="access-trail-layer"
              style={{
                lineColor: '#16A34A',
                lineWidth: 3,
                lineDasharray: [6, 3],
                lineOpacity: 0.75,
                lineCap: 'round',
                lineJoin: 'round',
              }}
            />
          </ShapeSource>
        )}

        {/* Access-point markers */}
        {accessEnabled &&
          accessPoints.map((ap) => {
            const cfg = ACCESS_POINT_CONFIG[ap.type];
            const size = Math.round(28 * cfg.scale);
            return (
              <PointAnnotation
                key={`ap-${ap.id}`}
                id={`ap-${ap.id}`}
                coordinate={[ap.lon, ap.lat]}
              >
                <View
                  style={[
                    styles.accessPinOuter,
                    {
                      backgroundColor: cfg.color,
                      width: size,
                      height: size,
                      borderRadius: size / 2,
                    },
                  ]}
                >
                  <Ionicons name={cfg.ionicon as any} size={Math.round(14 * cfg.scale)} color="#FFFFFF" />
                </View>
                <Callout title="">
                  <View style={styles.marinaCallout}>
                    <View style={styles.marinaCalloutHeader}>
                      <View style={[styles.marinaCalloutBadge, { backgroundColor: cfg.color }]}>
                        <Ionicons name={cfg.ionicon as any} size={10} color="#FFFFFF" />
                      </View>
                      <Text style={styles.marinaCalloutTitle} numberOfLines={1}>
                        {ap.name}
                      </Text>
                    </View>
                    <Text style={styles.marinaCalloutType}>{cfg.label}</Text>
                    {ap.fee != null && (
                      <Text style={styles.marinaCalloutDistance}>
                        {ap.fee ? 'Fee required' : 'Free access'}
                      </Text>
                    )}
                    {ap.surface ? (
                      <View style={styles.marinaCalloutRow}>
                        <Ionicons name="trail-sign-outline" size={11} color={palette.textSecondary} />
                        <Text style={styles.marinaCalloutDetail}>Surface: {ap.surface}</Text>
                      </View>
                    ) : null}
                    {ap.operator ? (
                      <View style={styles.marinaCalloutRow}>
                        <Ionicons name="business-outline" size={11} color={palette.textSecondary} />
                        <Text style={styles.marinaCalloutDetail}>{ap.operator}</Text>
                      </View>
                    ) : null}
                  </View>
                </Callout>
              </PointAnnotation>
            );
          })}

        {/* Wind overlay */}
        {windEnabled && ShapeSource && CircleLayer && SymbolLayer && (
          <WindOverlayAnimated
            lat={mapFeatureLocation?.lat ?? DEFAULT_CENTER[1]}
            lon={mapFeatureLocation?.lon ?? DEFAULT_CENTER[0]}
            bounds={visibleContourBounds}
            ShapeSource={ShapeSource}
            SymbolLayer={SymbolLayer}
            CircleLayer={CircleLayer}
            onLoadStart={() => setWindLoading(true)}
            onLoadEnd={() => setWindLoading(false)}
            onDataLoaded={({ vectorCount }) => setWindVectorCount(vectorCount)}
          />
        )}
        {/* Precipitation radar overlay */}
        {radarEnabled && radarTileUrl && RasterSource && RasterLayer && (
          <RasterSource
            id="radar-tile-source"
            tileUrlTemplates={[radarTileUrl]}
            tileSize={256}
          >
            <RasterLayer
              id="radar-tile-layer"
              style={{ rasterOpacity: 0.75 }}
            />
          </RasterSource>
        )}

        {/* Storm cell polygons */}
        {stormGeoJSON && stormGeoJSON.features.length > 0 && ShapeSource && FillLayer && LineLayer && (
          <ShapeSource id="storm-cell-source" shape={stormGeoJSON as any}>
            <FillLayer
              id="storm-cell-fill"
              style={{
                fillColor: ['get', 'fillColor'],
                fillOpacity: 0.5,
              }}
            />
            <LineLayer
              id="storm-cell-outline"
              style={{
                lineColor: ['get', 'color'],
                lineWidth: 2,
                lineOpacity: 0.9,
              }}
            />
          </ShapeSource>
        )}

        {/* Live track recording line */}
        {liveTrackGeoJSON && ShapeSource && LineLayer && dismissedTrackId !== liveTrack?.id && (
          <ShapeSource id="live-track-source" shape={liveTrackGeoJSON}>
            <LineLayer
              id="live-track-layer"
              style={{
                lineColor: ['get', 'color'],
                lineWidth: 4,
                lineCap: 'round',
                lineJoin: 'round',
              }}
            />
          </ShapeSource>
        )}

        {/* Highlighted access points on spot click (Feature 2) */}
        {highlightedAccessPoints.length > 0 && PointAnnotation && (
          highlightedAccessPoints.map((ap) => {
            const cfg = ACCESS_POINT_CONFIG[ap.type];
            return (
              <PointAnnotation
                key={'highlight-ap-' + ap.id}
                id={'highlight-ap-' + ap.id}
                coordinate={[ap.lon, ap.lat]}
                anchor={{ x: 0.5, y: 0.5 }}
              >
                <View style={[
                  styles.highlightedApMarker,
                  { backgroundColor: cfg.color, transform: [{ scale: cfg.scale }] },
                ]}>
                  <Ionicons name={cfg.ionicon as any} size={14} color="#FFFFFF" />
                </View>
                <Callout title={ap.name}>
                  <View style={styles.calloutBubble}>
                    <Text style={styles.calloutTitle}>{ap.name}</Text>
                    <Text style={{ fontSize: 11, color: palette.textMuted }}>{cfg.label}</Text>
                    {ap.fee !== undefined && (
                      <Text style={{ fontSize: 10, color: palette.textSecondary, marginTop: 2 }}>
                        {ap.fee ? 'Fee required' : 'Free access'}
                      </Text>
                    )}
                  </View>
                </Callout>
              </PointAnnotation>
            );
          })
        )}

        {/* Lines from parking to nearest boat launch / fishing access (Feature 2) */}
        {highlightedAccessPoints.length > 0 && ShapeSource && LineLayer && (() => {
          const parkingPts = highlightedAccessPoints.filter((p) => p.type === 'parking');
          const launchTypes = ['boat_launch', 'kayak_launch', 'shore_fishing', 'fishing_pier', 'trailhead'];
          const launchPts = highlightedAccessPoints.filter((p) => launchTypes.includes(p.type));
          if (parkingPts.length === 0 || launchPts.length === 0) return null;

          // For each parking lot, draw a line to the NEAREST launch/access point
          const features = parkingPts.map((parking, i) => {
            let nearestLaunch = launchPts[0];
            let nearestDist = Infinity;
            for (const lp of launchPts) {
              const d = haversineDistance(parking.lat, parking.lon, lp.lat, lp.lon);
              if (d < nearestDist) {
                nearestDist = d;
                nearestLaunch = lp;
              }
            }
            // Only draw if parking is within 1 mile of the launch
            if (nearestDist > 1.0) return null;
            return {
              type: 'Feature' as const,
              id: 'route-' + i,
              geometry: {
                type: 'LineString' as const,
                coordinates: [
                  [parking.lon, parking.lat],
                  [nearestLaunch.lon, nearestLaunch.lat],
                ],
              },
              properties: { apType: nearestLaunch.type },
            };
          }).filter((f): f is NonNullable<typeof f> => f !== null);

          if (features.length === 0) return null;

          const geoJSON: GeoJSON.FeatureCollection = {
            type: 'FeatureCollection',
            features,
          };

          return (
            <ShapeSource id="highlighted-routes-source" shape={geoJSON}>
              <LineLayer
                id="highlighted-routes-layer"
                style={{
                  lineColor: palette.accent,
                  lineWidth: 2,
                  lineDasharray: [4, 3],
                  lineOpacity: 0.6,
                }}
              />
            </ShapeSource>
          );
        })()}

        {/* ── Anchor Watch overlay ─────────────────────────────────────── */}
        {anchorStatus.active && anchorCircleFeature && ShapeSource && FillLayer && LineLayer && (
          <ShapeSource
            id="anchor-circle-source"
            shape={{
              type: 'FeatureCollection',
              features: [anchorCircleFeature],
            } as any}
          >
            <FillLayer
              id="anchor-circle-fill"
              style={{
                fillColor: anchorStatus.alarm ? 'rgba(196, 75, 75, 0.15)' : 'rgba(10, 110, 189, 0.10)',
                fillOutlineColor: anchorStatus.alarm ? palette.error : palette.accent,
              }}
            />
            <LineLayer
              id="anchor-circle-outline"
              style={{
                lineColor: anchorStatus.alarm ? palette.error : palette.accent,
                lineWidth: 2,
                lineDasharray: [6, 4],
                lineOpacity: 0.8,
              }}
            />
          </ShapeSource>
        )}
        {anchorStatus.active && anchorPointGeoJSON && ShapeSource && CircleLayer && SymbolLayer && (
          <ShapeSource id="anchor-point-source" shape={anchorPointGeoJSON as any}>
            <CircleLayer
              id="anchor-point-circle"
              style={{
                circleColor: '#1A1A18',
                circleRadius: 10,
                circleStrokeColor: '#FFFFFF',
                circleStrokeWidth: 2,
                circleOpacity: 0.9,
              }}
            />
            <SymbolLayer
              id="anchor-point-label"
              style={{
                textField: '\u2693',
                textSize: 14,
                textColor: '#FFFFFF',
                textOffset: [0, 0],
              }}
            />
          </ShapeSource>
        )}

        {/* ── MOB marker overlay ───────────────────────────────────────── */}
        {mobStatus.active && mobPointGeoJSON && ShapeSource && CircleLayer && SymbolLayer && (
          <ShapeSource id="mob-point-source" shape={mobPointGeoJSON as any}>
            <CircleLayer
              id="mob-point-outer"
              style={{
                circleColor: 'rgba(196, 75, 75, 0.25)',
                circleRadius: 24,
                circleOpacity: 0.8,
              }}
            />
            <CircleLayer
              id="mob-point-inner"
              style={{
                circleColor: palette.error,
                circleRadius: 10,
                circleStrokeColor: '#FFFFFF',
                circleStrokeWidth: 3,
                circleOpacity: 1,
              }}
            />
            <SymbolLayer
              id="mob-point-label"
              style={{
                textField: 'MOB',
                textSize: 10,
                textColor: '#FFFFFF',
                textFont: ['Open Sans Bold'],
                textOffset: [0, -2.5],
              }}
            />
          </ShapeSource>
        )}

      </MLMapView>

      {/* Recording banner — tap to open TrackRecordingScreen */}
      {liveTrack && (
        <Pressable
          style={styles.recordingBanner}
          onPress={() => navigation.navigate('TrackRecording')}
        >
          <Animated.View style={[styles.recordingBannerDot, { transform: [{ scale: recordingPulse }] }]}>
            <View style={styles.recordingBannerDotInner} />
          </Animated.View>
          <View style={{ flex: 1 }}>
            <Text style={styles.recordingBannerTitle}>Recording Trip</Text>
            <Text style={styles.recordingBannerSub}>
              {liveTrack.points.length} pts ·{' '}
              {liveTrack.distanceMiles < 0.1
                ? `${Math.round(liveTrack.distanceMiles * 5280)} ft`
                : `${liveTrack.distanceMiles.toFixed(2)} mi`}
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color="#FFFFFF" />
        </Pressable>
      )}

      {/* Dismiss track overlay after recording stops */}
      {!liveTrack && liveTrackGeoJSON && dismissedTrackId !== (liveTrack as any)?.id && (
        <Pressable
          style={[styles.dismissTrackBanner, { top: topBannerTop }]}
          onPress={() => {
            setLiveTrackGeoJSON(null);
          }}
        >
          <Ionicons name="close-circle" size={16} color={palette.textMuted} />
          <Text style={styles.dismissTrackText}>Dismiss track overlay</Text>
        </Pressable>
      )}

      {/* ── Anchor Watch Active Banner ──────────────────────────────── */}
      {anchorStatus.active && (
        <Animated.View
          style={[
            styles.anchorWatchBanner,
            anchorStatus.alarm && styles.anchorWatchBannerAlarm,
            { top: topBannerTop },
            { transform: [{ scale: anchorStatus.alarm ? anchorBannerPulse : 1 }] },
          ]}
        >
          <Text style={styles.anchorWatchBannerIcon}>{'\u2693'}</Text>
          <View style={{ flex: 1 }}>
            <Text style={styles.anchorWatchBannerTitle}>
              {anchorStatus.alarm ? 'DRIFT ALARM!' : 'Anchor Watch Active'}
            </Text>
            <Text style={styles.anchorWatchBannerSub}>
              Drift: {Math.round(anchorStatus.driftDistance)}m / {anchorStatus.watch?.radiusMeters ?? 0}m
            </Text>
          </View>
          <Text style={[
            styles.anchorWatchBannerBearing,
            anchorStatus.alarm && { color: '#FFFFFF' },
          ]}>
            {Math.round(anchorStatus.bearing)}{'\u00B0'}
          </Text>
        </Animated.View>
      )}

      {/* ── MOB Active Banner ──────────────────────────────────────── */}
      {mobStatus.active && mobStatus.event && (
        <View style={[styles.mobBanner, { top: topBannerTop }]}>
          <View style={styles.mobBannerDot} />
          <View style={{ flex: 1 }}>
            <Text style={styles.mobBannerTitle}>MAN OVERBOARD</Text>
            <Text style={styles.mobBannerSub}>
              {mobStatus.distanceMeters < 1000
                ? `${Math.round(mobStatus.distanceMeters)}m`
                : `${(mobStatus.distanceMeters / 1000).toFixed(2)}km`}
              {' \u00B7 '}{Math.round(mobStatus.bearing)}{'\u00B0'}
            </Text>
          </View>
        </View>
      )}

      {/* Search bar overlay */}
      {!routeNavActive && (
        <View
          style={[styles.searchOverlay, { top: topStackTop }]}
          onLayout={(event) => {
            const nextHeight = Math.ceil(event.nativeEvent.layout.height);
            if (Math.abs(nextHeight - searchOverlayHeight) > 2) {
              setSearchOverlayHeight(nextHeight);
            }
          }}
        >
          <EnhancedSearchBar
            value={search}
            onChangeText={setSearch}
            onClear={() => setSearch('')}
            onSubmit={handleSearchSubmit}
            onSelectLocation={handleSearchSelectLocation}
            onStartRoute={handleStartRouteFromSearch}
            onStartRouteToLocation={handleStartRouteToLocation}
            routeActive={routePlannerVisible}
            nearbySpots={defaultNearbyLocations.slice(0, 10).map((item) => ({
              id: item.location.id,
              name: getPrimaryLocationLabel(item.location),
              subtitle: getSecondaryLocationLabel(item.location),
              distanceMi: item.distanceMi ?? undefined,
              type: cleanLocationText(item.location.subtitle),
            }))}
            allLocations={searchBarLocations}
          />
        </View>
      )}

      {/* Weather alert banner */}
      {showTopMapAlerts && topAlert && !alertBannerDismissed && !routePlannerVisible && (
        <View style={[styles.alertBanner, { backgroundColor: bannerColor, top: topBannerTop }]}>
          <Pressable
            style={styles.alertBannerContent}
            onPress={() => setAlertBannerExpanded((prev) => !prev)}
          >
            <View style={styles.alertBannerRow}>
              <Ionicons name="warning" size={16} color="#FFFFFF" />
              <Text style={styles.alertBannerTitle} numberOfLines={1}>
                {topAlert.event}
              </Text>
              {weatherAlerts.length > 1 && (
                <View style={styles.alertBadgeCount}>
                  <Text style={styles.alertBadgeCountText}>{weatherAlerts.length}</Text>
                </View>
              )}
              <Ionicons
                name={alertBannerExpanded ? 'chevron-up' : 'chevron-down'}
                size={16}
                color="#FFFFFF"
              />
              <Pressable
                onPress={() => {
                  setAlertBannerDismissed(true);
                  setAlertBannerExpanded(false);
                }}
                hitSlop={8}
                style={styles.alertBannerDismiss}
              >
                <Ionicons name="close" size={16} color="rgba(255,255,255,0.8)" />
              </Pressable>
            </View>
          </Pressable>
          {alertBannerExpanded && (
            <View style={styles.alertBannerExpanded}>
              <Text style={styles.alertBannerSummary}>{topAlert.fishingSummary}</Text>
              {topAlert.instruction && (
                <Text style={styles.alertBannerInstruction}>
                  {topAlert.instruction.length > 200
                    ? topAlert.instruction.slice(0, 200) + '...'
                    : topAlert.instruction}
                </Text>
              )}
              <Pressable
                style={styles.alertBannerViewAll}
                onPress={() => {
                  setAlertBannerExpanded(false);
                  navigation.navigate('Alerts');
                }}
              >
                <Text style={styles.alertBannerViewAllText}>
                  View All Alerts ({weatherAlerts.length})
                </Text>
                <Ionicons name="arrow-forward" size={14} color="#FFFFFF" />
              </Pressable>
            </View>
          )}
        </View>
      )}

      {/* Map style toggle button */}
      {showFloatingMapControls && (
        <Pressable
          style={[styles.layerButton, { top: floatingControlsTop }]}
          onPress={handleToggleLayerPicker}
          accessibilityLabel={`Map style: ${MAP_STYLE_LABELS[mapStyle]}. Tap to change the basemap.`}
        >
          <Ionicons name="layers-outline" size={20} color={palette.textSecondary} />
        </Pressable>
      )}

      {/* Map Tools button — opens slide-out drawer (replaces 7 individual buttons) */}
      {/* Design: "Kitchen Sink" avoidance per Gaigg Ch.7 */}
      {showFloatingMapControls && (
        <Pressable
          style={[
            styles.mapToolsButton,
            { top: floatingControlsTop + 48 },
            mapToolsActiveCount > 0 && styles.mapToolsButtonActive,
          ]}
          onPress={() => setMapToolsDrawerOpen(true)}
          accessibilityLabel="Open map tools drawer"
        >
          <Ionicons
            name="build-outline"
            size={20}
            color={mapToolsActiveCount > 0 ? '#FFFFFF' : palette.textSecondary}
          />
          {mapToolsActiveCount > 0 && (
            <View style={styles.mapToolsBadge}>
              <Text style={styles.mapToolsBadgeText}>
                {mapToolsActiveCount}
              </Text>
            </View>
          )}
        </Pressable>
      )}

      {/* Compass heading widget — only visible when map is rotated */}
      {showFloatingMapControls && !shouldHideCompass && (
        <CompassWidget
          heading={compassHeading}
          mode={compassMode}
          onToggleMode={handleToggleCompassMode}
          style={{ top: floatingControlsTop }}
        />
      )}

      {/* Overlay loading banner — shows when any overlay is fetching data */}
      <MapOverlayLoadingBanner loadingOverlays={overlayLoadingSet} />

      {/* Map Tools Drawer (slide-out panel for secondary tools) */}
      <MapToolsDrawer
        visible={mapToolsDrawerOpen}
        onClose={() => setMapToolsDrawerOpen(false)}
        toolGroups={mapToolGroups}
      />

      {/* Catch photos toggle available via layer picker */}

      {/* Fishing time banner — shows when conditions are good */}
      {mapFeatureLocation && !routePlannerVisible && (
        <FishingTimeBanner lat={mapFeatureLocation.lat} lon={mapFeatureLocation.lon} />
      )}

      {/* Precipitation radar controls */}
      {radarEnabled && radarFrames.length > 0 && (
        <View style={styles.radarControlBanner}>
          <Pressable
            onPress={() => setRadarPlaying((prev) => !prev)}
            style={styles.radarPlayButton}
          >
            <Ionicons
              name={radarPlaying ? 'pause' : 'play'}
              size={16}
              color="#FFFFFF"
            />
          </Pressable>
          <Text style={styles.radarTimestamp}>
            {radarFrames[radarFrameIndex]?.timestamp.label ?? ''}
            {radarFrames[radarFrameIndex]?.timestamp.isForecast ? ' (forecast)' : ''}
          </Text>
          <View style={styles.radarLegendRow}>
            {RADAR_LEGEND.map((entry) => (
              <View key={entry.label} style={styles.windLegend}>
                <View style={[styles.windLegendDot, { backgroundColor: entry.color }]} />
                <Text style={styles.windLegendLabel}>{entry.label}</Text>
              </View>
            ))}
          </View>
        </View>
      )}

      {(windEnabled || radarEnabled || dynamicDepthsEnabled) && !mapWeatherPanelDismissed && !routePlannerVisible && (
        <Animated.View
          pointerEvents="box-none"
          style={[
            styles.mapWeatherPanelWrap,
            {
              bottom: markerMode === 'locations'
                ? Animated.add(sheetHeight, 16)
                : radarEnabled && radarFrames.length > 0
                  ? (Platform.OS === 'ios' ? 170 : 150)
                  : 24,
            },
          ]}
        >
          <MapWeatherPanel
            forecast={mapWeatherForecast}
            tideSummary={mapTideSummary}
            compact={windEnabled && !radarEnabled && !dynamicDepthsEnabled}
            loading={mapWeatherLoading || windLoading}
            onClose={() => setMapWeatherPanelDismissed(true)}
          />
        </Animated.View>
      )}

      {/* Storm cell warnings */}
      {showTopMapAlerts && stormCells.length > 0 && !routePlannerVisible && (
        <View style={[styles.stormBanner, { top: topBannerTop }]}>
          <Ionicons name="thunderstorm" size={16} color="#D50000" style={{ marginRight: 6 }} />
          <View style={{ flex: 1 }}>
            <Text style={styles.stormBannerTitle}>
              {stormCells.length} severe warning{stormCells.length > 1 ? 's' : ''} nearby
            </Text>
            <Text style={styles.stormBannerDetail} numberOfLines={1}>
              {stormCells[0].event}
              {stormCells[0].distanceMiles != null ? ` — ${stormCells[0].distanceMiles} mi away` : ''}
              {stormCells[0].etaMinutes != null ? ` (ETA ${stormCells[0].etaMinutes} min)` : ''}
            </Text>
          </View>
        </View>
      )}

      {routePlannerVisible && (
        <View
          style={[
            styles.routePlannerCard,
            routeNavActive && styles.routePlannerCardNavActive,
            { bottom: routePlannerBottom, maxHeight: routePlannerMaxHeight },
          ]}
        >
          <View style={styles.routePlannerSheetHandle} />
          <View style={styles.routePlannerHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.routePlannerTitle}>
                {routeNavActive ? 'Navigation' : routeName || 'Route Builder'}
              </Text>
              <Text style={styles.routePlannerSubtitle} numberOfLines={showRoutePlannerDetails ? 2 : 1}>
                {routeNavActive
                  ? 'Follow the shaded lane ahead. Drag handles any time before or after you stop navigation.'
                  : routeMode
                  ? pendingRouteDestination
                    ? 'Choose a start on the water, then we will route to the pinned destination.'
                    : 'Tap the water to place turns. Adjust the route live.'
                  : routeMetrics
                    ? 'Draft-aware autoroute ready'
                    : routePoints.length > 0
                      ? `${routePoints.length} point${routePoints.length === 1 ? '' : 's'} placed`
                      : 'Start by tapping the map'}
              </Text>
            </View>
            <View style={styles.routePlannerHeaderActions}>
              <Pressable
                style={styles.routePlannerHeaderButton}
                onPress={() => setRoutePlannerExpanded((prev) => !prev)}
              >
                <Ionicons
                  name={showRoutePlannerDetails ? 'chevron-up' : 'chevron-down'}
                  size={16}
                  color={palette.textSecondary}
                />
              </Pressable>
              <Pressable
                style={styles.routePlannerClose}
                onPress={() => {
                  if (routeMode) {
                    setRouteMode(false);
                  } else {
                    handleClearRoutePlan();
                  }
                }}
              >
                <Ionicons
                  name={routeMode ? 'checkmark' : 'close'}
                  size={16}
                  color={routeMode ? '#1565C0' : palette.textSecondary}
                />
              </Pressable>
            </View>
          </View>

          {(routeNavActive || routeMetrics || routePoints.length > 0 || pendingRouteDestination) && (
            <View style={styles.routePlannerSummaryStrip}>
              <View style={styles.routePlannerSummaryCopy}>
                <Text style={styles.routePlannerSummaryEyebrow}>
                  {routeNavActive ? 'Live Navigation' : 'AutoRoute'}
                </Text>
                <Text style={styles.routePlannerSummaryPrimary} numberOfLines={1}>
                  {routeOverviewText}
                </Text>
                <Text style={styles.routePlannerSummarySecondary} numberOfLines={showRoutePlannerDetails ? 2 : 1}>
                  {routeSupportText}
                </Text>
              </View>
              <View style={[styles.routePlannerStatusBadge, { backgroundColor: `${routeStatusTone}14` }]}>
                <Ionicons
                  name={
                    routeNavActive
                      ? 'navigate'
                      : routeAlerts.length > 0
                        ? 'git-branch-outline'
                        : routeWarnings.length > 0
                          ? 'warning-outline'
                          : 'checkmark-circle-outline'
                  }
                  size={14}
                  color={routeStatusTone}
                />
                <Text style={[styles.routePlannerStatusBadgeText, { color: routeStatusTone }]}>
                  {routeStatusLabel}
                </Text>
              </View>
            </View>
          )}

          {routeMode && !routeNavActive && (
            <View style={styles.routePlannerGuideBanner}>
              <Ionicons name="navigate-outline" size={14} color="#1565C0" />
              <Text style={styles.routePlannerGuideText}>
                {routeTapBannerTextValue}
              </Text>
            </View>
          )}

          {!routeNavActive && (
            <View style={styles.routePlannerPlacementRow}>
              {([
                ['start', 'Set A'],
                ['stop', 'Add Stop'],
                ['end', 'Set B'],
              ] as const).map(([mode, label]) => {
                const active = routePlacementMode === mode;
                return (
                  <Pressable
                    key={mode}
                    style={[
                      styles.routePlannerPlacementBtn,
                      active && styles.routePlannerPlacementBtnActive,
                    ]}
                    onPress={() => {
                      setRouteMode(true);
                      setRoutePlacementMode(mode);
                    }}
                  >
                    <Text
                      style={[
                        styles.routePlannerPlacementText,
                        active && styles.routePlannerPlacementTextActive,
                      ]}
                    >
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}

          {routeMetrics && showRoutePlannerDetails && (
            <View style={styles.routePlannerHeroRow}>
              <View style={styles.routePlannerHeroCard}>
                <Text style={styles.routePlannerHeroLabel}>Distance</Text>
                <Text style={styles.routePlannerHeroValue}>{routeDistancePrimaryLabel}</Text>
                <Text style={styles.routePlannerHeroSub}>{routeDistanceSecondaryLabel}</Text>
              </View>
              <View style={styles.routePlannerHeroCard}>
                <Text style={styles.routePlannerHeroLabel}>Remaining</Text>
                <Text style={styles.routePlannerHeroValue}>{routeMetrics.adjustedTimeLabel}</Text>
                <Text style={styles.routePlannerHeroSub}>at {routeMetrics.cruiseSpeedKnots.toFixed(0)} kt cruise</Text>
              </View>
              <View style={styles.routePlannerHeroCard}>
                <Text style={styles.routePlannerHeroLabel}>Arrival</Text>
                <Text style={styles.routePlannerHeroValue}>{routeArrivalLabel}</Text>
                <Text style={styles.routePlannerHeroSub}>
                  {routeWarnings.length > 0 ? 'draft-aware caution' : 'clear path'}
                </Text>
              </View>
            </View>
          )}

          {!!routeNextLegText && showRoutePlannerDetails && (
            <View style={styles.routePlannerNavCard}>
              <View style={styles.routePlannerNavIcon}>
                <Ionicons name="navigate" size={15} color="#355B75" />
              </View>
              <View style={styles.routePlannerNavCopy}>
                <Text style={styles.routePlannerNavTitle}>Next leg</Text>
                <Text style={styles.routePlannerNavSubtitle} numberOfLines={1}>
                  {routeNextLegText}
                </Text>
              </View>
            </View>
          )}

          {(!routeNavActive || showRoutePlannerDetails) && (
          <View style={styles.routePlannerMetricRow}>
            {pendingRouteDestination && (
              <View style={styles.routePlannerMetricPill}>
                <Ionicons name="flag-outline" size={12} color="#C44B4B" />
                <Text style={styles.routePlannerMetricText}>
                  Destination: {pendingRouteDestination.label ?? 'Pinned'}
                </Text>
              </View>
            )}
            <View style={styles.routePlannerMetricPill}>
              <Ionicons name="pin-outline" size={12} color="#1565C0" />
              <Text style={styles.routePlannerMetricText}>{routePoints.length} pts</Text>
            </View>
            <View style={styles.routePlannerMetricPill}>
              <Ionicons name="resize-outline" size={12} color="#1565C0" />
              <Text style={styles.routePlannerMetricText}>
                {routeDistancePrimaryLabel}
              </Text>
            </View>
            <View style={styles.routePlannerMetricPill}>
              <Ionicons
                name={
                  routeDepthChecking
                    ? 'time-outline'
                    : routeAlerts.length > 0
                      ? 'git-branch-outline'
                      : routeWarnings.length > 0
                        ? 'warning-outline'
                        : 'water-outline'
                }
                size={12}
                color={routeStatusTone}
              />
              <Text style={styles.routePlannerMetricText}>
                {routeStatusLabel}
              </Text>
            </View>
            {routeMetrics && (
              <View style={[styles.routePlannerMetricPill, { backgroundColor: `${routeStatusTone}12` }]}>
                <Ionicons name="boat-outline" size={12} color={routeStatusTone} />
                <Text style={[styles.routePlannerMetricText, { color: routeStatusTone }]}>
                  Draft {routeDraftPrimaryLabel}
                </Text>
              </View>
            )}
            <Pressable
              style={styles.routePlannerMetricPill}
              onPress={() => setRoutePlannerExpanded((prev) => !prev)}
            >
              <Ionicons
                name={showRoutePlannerDetails ? 'chevron-up-outline' : 'options-outline'}
                size={12}
                color="#1565C0"
              />
              <Text style={styles.routePlannerMetricText}>
                {showRoutePlannerDetails ? 'Hide details' : 'Details'}
              </Text>
            </Pressable>
          </View>
          )}

          <View style={styles.routePlannerPrimaryActionRow}>
            {!routeNavActive ? (
              <Pressable
                style={[styles.routePlannerGoButton, styles.routePlannerGoButtonLarge]}
                onPress={() => {
                  setRouteNavIndex(1);
                  setCompassMode('static');
                  setRouteCameraMode('follow-3d');
                  setRouteMode(false);
                  setRouteNavActive(true);
                  setRouteTurnSheetVisible(false);
                  setRoutePlannerExpanded(false);
                }}
                disabled={!plannedRoute || routePoints.length < 2}
              >
                <Ionicons
                  name="navigate-outline"
                  size={17}
                  color="#FFFFFF"
                />
                <Text style={styles.routePlannerGoButtonText}>
                  Start Navigation
                </Text>
              </Pressable>
            ) : (
              <>
                <Pressable
                  style={[styles.routePlannerGoButton, routeNavActive && styles.routePlannerGoButtonActive]}
                  onPress={() => {
                    setRouteNavActive(false);
                    setRouteCameraMode('overview');
                    setRouteTurnSheetVisible(false);
                  }}
                  disabled={!plannedRoute || routePoints.length < 2}
                >
                  <Ionicons
                    name="pause-outline"
                    size={15}
                    color="#FFFFFF"
                  />
                  <Text style={styles.routePlannerGoButtonText}>
                    Stop Route
                  </Text>
                </Pressable>
                <Pressable
                  style={styles.routePlannerPrimarySecondaryBtn}
                  onPress={() => {
                    setRouteCameraMode((prev) => (prev === 'overview' ? 'follow-3d' : 'overview'));
                  }}
                >
                  <Ionicons
                    name={routeCameraMode === 'overview' ? 'navigate-outline' : 'map-outline'}
                    size={14}
                    color={palette.textSecondary}
                  />
                  <Text style={styles.routePlannerActionText}>
                    {routeCameraMode === 'overview' ? 'Follow' : 'Overview'}
                  </Text>
                </Pressable>
                <Pressable
                  style={styles.routePlannerPrimarySecondaryBtn}
                  onPress={() => setRouteTurnSheetVisible(true)}
                  disabled={!routeTurnItems.length}
                >
                  <Ionicons name="list-outline" size={14} color={palette.textSecondary} />
                  <Text style={styles.routePlannerActionText}>Turns</Text>
                </Pressable>
              </>
            )}
          </View>

          {!routeNavActive && (
            <View style={styles.routePlannerPrimaryActionRow}>
              <Pressable
                style={styles.routePlannerPrimarySecondaryBtn}
                onPress={handleSavePlannedRoute}
                disabled={!plannedRoute}
              >
                <Ionicons name="bookmark-outline" size={14} color={palette.textSecondary} />
                <Text style={styles.routePlannerActionText}>Save</Text>
              </Pressable>
              <Pressable
                style={styles.routePlannerPrimarySecondaryBtn}
                onPress={() => setRouteTurnSheetVisible(true)}
                disabled={!routeTurnItems.length}
              >
                <Ionicons name="list-outline" size={14} color={palette.textSecondary} />
                <Text style={styles.routePlannerActionText}>Turns</Text>
              </Pressable>
              <Pressable
                style={styles.routePlannerPrimarySecondaryBtn}
                onPress={() => navigation.navigate('RoutePlanner')}
              >
                <Ionicons name="albums-outline" size={14} color={palette.textSecondary} />
                <Text style={styles.routePlannerActionText}>Routes</Text>
              </Pressable>
            </View>
          )}

          {showRoutePlannerDetails && (
            <View style={styles.routePlannerControlRow}>
              <View style={styles.routePlannerControlBlock}>
                <Text style={styles.routePlannerControlLabel}>Boat draft</Text>
                <View style={styles.routePlannerStepper}>
                  <Pressable
                    style={styles.routePlannerStepperBtn}
                    onPress={() => adjustRouteDraft(-ROUTE_DRAFT_STEP_METERS)}
                    disabled={!routeBoatProfile}
                  >
                    <Ionicons name="remove" size={14} color={palette.textSecondary} />
                  </Pressable>
                  <View style={styles.routePlannerStepperValueWrap}>
                    <Text style={styles.routePlannerStepperValue}>
                      {routeDraftPrimaryLabel}
                    </Text>
                    <Text style={styles.routePlannerStepperSub}>{routeDraftSecondaryLabel}</Text>
                  </View>
                  <Pressable
                    style={styles.routePlannerStepperBtn}
                    onPress={() => adjustRouteDraft(ROUTE_DRAFT_STEP_METERS)}
                    disabled={!routeBoatProfile}
                  >
                    <Ionicons name="add" size={14} color={palette.textSecondary} />
                  </Pressable>
                </View>
              </View>

              <View style={styles.routePlannerControlBlock}>
                <Text style={styles.routePlannerControlLabel}>Nav camera</Text>
                <View style={styles.routePlannerSegmented}>
                  {([
                    ['overview', 'Overview'],
                    ['follow', 'Follow'],
                    ['follow-3d', '3D'],
                  ] as const).map(([mode, label]) => {
                    const active = routeCameraMode === mode;
                    return (
                      <Pressable
                        key={mode}
                        style={[
                          styles.routePlannerSegmentBtn,
                          active && styles.routePlannerSegmentBtnActive,
                        ]}
                        onPress={() => setRouteCameraMode(mode)}
                      >
                        <Text
                          style={[
                            styles.routePlannerSegmentText,
                            active && styles.routePlannerSegmentTextActive,
                          ]}
                        >
                          {label}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            </View>
          )}

          {!routeNavActive && (
          <View style={styles.routePlannerActionRow}>
            {!routeNavActive && (
              <Pressable
                style={[styles.routePlannerActionBtn, styles.routePlannerActionBtnPrimary]}
                onPress={handleAddRoutePointAtCenter}
              >
                <Ionicons name="locate-outline" size={14} color="#FFFFFF" />
                <Text style={[styles.routePlannerActionText, styles.routePlannerActionTextPrimary]}>
                  {routePlacementMode === 'start'
                    ? 'Use Center for A'
                    : routePlacementMode === 'end'
                      ? 'Use Center for B'
                      : 'Use Center for Stop'}
                </Text>
              </Pressable>
            )}
            <Pressable
              style={[styles.routePlannerActionBtn, routeMode && styles.routePlannerActionBtnPrimary]}
              onPress={() => {
                if (routeMode) {
                  setRouteMode(false);
                } else {
                  openRouteBuilder();
                }
              }}
            >
              <Ionicons
                name={routeMode ? 'checkmark' : 'create-outline'}
                size={14}
                color={routeMode ? '#FFFFFF' : palette.textSecondary}
              />
              <Text style={[styles.routePlannerActionText, routeMode && styles.routePlannerActionTextPrimary]}>
                {routeMode ? 'Done' : 'Edit'}
              </Text>
            </Pressable>
            <Pressable
              style={styles.routePlannerActionBtn}
              onPress={handleUndoRoutePoint}
              disabled={routePoints.length === 0}
            >
              <Ionicons name="arrow-undo-outline" size={14} color={palette.textSecondary} />
              <Text style={styles.routePlannerActionText}>Undo</Text>
            </Pressable>
            <Pressable
              style={[styles.routePlannerActionBtn, marineGasEnabled && styles.routePlannerActionBtnPrimary]}
              onPress={() => setMarineGasEnabled((prev) => !prev)}
            >
              <Ionicons
                name="car-outline"
                size={14}
                color={marineGasEnabled ? '#FFFFFF' : palette.textSecondary}
              />
              <Text style={[styles.routePlannerActionText, marineGasEnabled && styles.routePlannerActionTextPrimary]}>
                Fuel
              </Text>
            </Pressable>
          </View>
          )}
        </View>
      )}

      {routeMode && !routeNavActive && (
        <View pointerEvents="none" style={styles.routeTargetReticle}>
          <View style={styles.routeTargetReticleRing}>
            <View style={styles.routeTargetReticleDot} />
          </View>
          <View style={styles.routeTargetReticleHorizontal} />
          <View style={styles.routeTargetReticleVertical} />
        </View>
      )}

      <Modal
        transparent
        animationType="slide"
        visible={routeTurnSheetVisible}
        onRequestClose={() => setRouteTurnSheetVisible(false)}
      >
        <TouchableWithoutFeedback onPress={() => setRouteTurnSheetVisible(false)}>
          <View style={styles.routeTurnsBackdrop}>
            <TouchableWithoutFeedback>
              <View style={styles.routeTurnsSheet}>
                <View style={styles.routeTurnsHandle} />
                <View style={styles.routeTurnsHeader}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.routeTurnsTitle}>Turn List</Text>
                    <Text style={styles.routeTurnsSubtitle}>
                      {routeNavActive ? 'Next maneuver is highlighted.' : 'Route legs and course changes.'}
                    </Text>
                  </View>
                  <Pressable
                    style={styles.routeTurnsClose}
                    onPress={() => setRouteTurnSheetVisible(false)}
                  >
                    <Ionicons name="close" size={18} color={palette.textMuted} />
                  </Pressable>
                </View>

                <ScrollView
                  style={styles.routeTurnsScroll}
                  showsVerticalScrollIndicator={false}
                >
                  {routeTurnItems.map((item) => {
                    const active = routeNavActive && item.segmentIndex === Math.max(routeNavIndex - 1, 0);
                    const accent =
                      item.severity === 'danger'
                        ? '#C44B4B'
                        : item.severity === 'caution'
                          ? '#C96A18'
                          : '#1F6FB2';
                    return (
                      <View
                        key={item.id}
                        style={[
                          styles.routeTurnRow,
                          active && styles.routeTurnRowActive,
                        ]}
                      >
                        <View style={[styles.routeTurnIndexBubble, { backgroundColor: `${accent}18` }]}>
                          <Text style={[styles.routeTurnIndexText, { color: accent }]}>
                            {item.segmentIndex + 1}
                          </Text>
                        </View>
                        <View style={styles.routeTurnCopy}>
                          <Text style={styles.routeTurnTitle}>{item.title}</Text>
                          <Text style={styles.routeTurnDetail}>{item.detail}</Text>
                        </View>
                        <View style={[styles.routeTurnSeverityPill, { backgroundColor: `${accent}14` }]}>
                          <Text style={[styles.routeTurnSeverityText, { color: accent }]}>
                            {item.severity === 'danger' ? 'No-go' : item.severity === 'caution' ? 'Shallow' : 'Clear'}
                          </Text>
                        </View>
                      </View>
                    );
                  })}

                  {plannedRoute && (
                    <View style={styles.routeTurnArrivalRow}>
                      <Ionicons name="flag-outline" size={15} color="#C44B4B" />
                      <Text style={styles.routeTurnArrivalText}>
                        Arrive at {routeName || 'destination'}
                      </Text>
                    </View>
                  )}
                </ScrollView>
              </View>
            </TouchableWithoutFeedback>
          </View>
        </TouchableWithoutFeedback>
      </Modal>

      {/* Measure mode banner */}
      {measureMode && (
        <View style={styles.measureBanner}>
          <Ionicons name="resize-outline" size={14} color={palette.accent} style={{ marginRight: 6 }} />
          <Text style={styles.measureBannerText}>Tap points to measure distance</Text>
        </View>
      )}

      {/* Measure mode distance badge + controls */}
      {measureMode && measurePoints.length >= 2 && (
        <View style={styles.measureBadge}>
          <Text style={styles.measureBadgeValue}>
            {measureTotalDistance(measurePoints).toFixed(2)} mi
          </Text>
          <Text style={styles.measureBadgeSecondary}>
            {(measureTotalDistance(measurePoints) * 1.60934).toFixed(2)} km
          </Text>
        </View>
      )}

      {/* Measure mode action buttons */}
      {measureMode && (
        <View style={styles.measureActions}>
          <Pressable
            style={styles.measureActionBtn}
            onPress={() => setMeasurePoints([])}
          >
            <Ionicons name="refresh-outline" size={16} color={palette.textSecondary} />
            <Text style={styles.measureActionText}>Clear</Text>
          </Pressable>
          <Pressable
            style={[styles.measureActionBtn, styles.measureActionBtnDone]}
            onPress={() => {
              setMeasureMode(false);
              setMeasurePoints([]);
            }}
          >
            <Ionicons name="checkmark" size={16} color="#FFFFFF" />
            <Text style={[styles.measureActionText, { color: '#FFFFFF' }]}>Done</Text>
          </Pressable>
        </View>
      )}

      {/* ── Annotation toolbar (shown when annotation mode active) ─── */}
      {annotationMode && (
        <View style={annotStyles.toolbar}>
          <View style={annotStyles.toolbarRow}>
            {([
              { type: 'marker' as AnnotationType, icon: 'location', label: 'Marker' },
              { type: 'circle' as AnnotationType, icon: 'ellipse-outline', label: 'Circle' },
              { type: 'arrow' as AnnotationType, icon: 'arrow-forward-outline', label: 'Arrow' },
              { type: 'text' as AnnotationType, icon: 'chatbubble-outline', label: 'Text' },
            ]).map((tool) => (
              <Pressable
                key={tool.type}
                style={[annotStyles.toolBtn, annotationTool === tool.type && annotStyles.toolBtnActive]}
                onPress={() => { setAnnotationTool(tool.type); setArrowStart(null); }}
              >
                <Ionicons name={tool.icon as any} size={18} color={annotationTool === tool.type ? '#FFFFFF' : palette.textSecondary} />
                <Text style={[annotStyles.toolLabel, annotationTool === tool.type && { color: '#FFFFFF' }]}>{tool.label}</Text>
              </Pressable>
            ))}
            <Pressable style={annotStyles.toolBtn} onPress={async () => {
              if (annotations.length > 0) {
                const last = annotations[annotations.length - 1];
                await removeAnnotation(last.id);
                setAnnotations((prev) => prev.slice(0, -1));
              }
            }}>
              <Ionicons name="backspace-outline" size={18} color={palette.error} />
              <Text style={[annotStyles.toolLabel, { color: palette.error }]}>Undo</Text>
            </Pressable>
            <Pressable style={annotStyles.toolBtn} onPress={() => setShowAnnotations((p) => !p)}>
              <Ionicons name={showAnnotations ? 'eye' : 'eye-off-outline'} size={18} color={palette.textSecondary} />
            </Pressable>
            <Pressable style={annotStyles.toolBtn} onPress={() => { setAnnotationMode(false); navigation.navigate('Annotations'); }}>
              <Ionicons name="list-outline" size={18} color={palette.accent} />
            </Pressable>
          </View>
          <View style={annotStyles.colorRow}>
            {ANNOTATION_COLORS.map((c) => (
              <Pressable
                key={c.color}
                style={[annotStyles.colorDot, { backgroundColor: c.color }, annotationColor === c.color && annotStyles.colorDotActive]}
                onPress={() => setAnnotationColor(c.color)}
              />
            ))}
          </View>
          <Text style={annotStyles.hint}>
            {annotationTool === 'marker' && 'Tap map to place a marker'}
            {annotationTool === 'circle' && (arrowStart ? 'Tap to set radius' : 'Tap center of circle')}
            {annotationTool === 'arrow' && (arrowStart ? 'Tap to set arrow end' : 'Tap arrow start point')}
            {annotationTool === 'text' && 'Tap map to place a note'}
          </Text>
        </View>
      )}

      {/* ── Annotation text input modal ────────────────────────────── */}
      <Modal visible={showAnnotationTextModal} transparent animationType="fade" onRequestClose={() => setShowAnnotationTextModal(false)}>
        <TouchableWithoutFeedback onPress={Keyboard.dismiss}>
          <View style={annotStyles.modalOverlay}>
            <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={annotStyles.modalContainer}>
              <View style={annotStyles.modalCard}>
                <Text style={annotStyles.modalTitle}>Add Note</Text>
                <TextInput
                  style={annotStyles.modalInput}
                  placeholder="Enter annotation text..."
                  placeholderTextColor={palette.textDim}
                  value={annotationTextInput}
                  onChangeText={setAnnotationTextInput}
                  autoFocus
                  maxLength={200}
                  multiline
                />
                <View style={annotStyles.modalButtons}>
                  <Pressable style={annotStyles.modalBtnCancel} onPress={() => { setShowAnnotationTextModal(false); setAnnotationTextCoord(null); }}>
                    <Text style={annotStyles.modalBtnCancelText}>Cancel</Text>
                  </Pressable>
                  <Pressable
                    style={[annotStyles.modalBtnSave, !annotationTextInput.trim() && { opacity: 0.4 }]}
                    disabled={!annotationTextInput.trim()}
                    onPress={async () => {
                      if (annotationTextCoord && annotationTextInput.trim()) {
                        await handleSaveAnnotation({ type: 'text', coordinate: annotationTextCoord, label: annotationTextInput.trim(), color: annotationColor });
                        setShowAnnotationTextModal(false);
                        setAnnotationTextCoord(null);
                        setAnnotationTextInput('');
                      }
                    }}
                  >
                    <Text style={annotStyles.modalBtnSaveText}>Save</Text>
                  </Pressable>
                </View>
              </View>
            </KeyboardAvoidingView>
          </View>
        </TouchableWithoutFeedback>
      </Modal>

      {/* ── Depth Contour Settings Modal ───────────────────────────── */}
      <Modal visible={showContourModal} transparent animationType="slide" onRequestClose={() => setShowContourModal(false)}>
        <TouchableWithoutFeedback onPress={() => setShowContourModal(false)}>
          <View style={annotStyles.modalOverlay}>
            <TouchableWithoutFeedback onPress={() => {}}>
              <View style={contourStyles.card}>
                <View style={contourStyles.header}>
                  <Text style={contourStyles.title}>Depth Contours</Text>
                  <Pressable onPress={() => setShowContourModal(false)}>
                    <Ionicons name="close" size={22} color={palette.textMuted} />
                  </Pressable>
                </View>
                <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: SCREEN_HEIGHT * 0.55 }}>
                  <Text style={contourStyles.sectionLabel}>Contour Interval</Text>
                  <View style={contourStyles.intervalRow}>
                    {CONTOUR_INTERVALS.map((opt) => (
                      <Pressable
                        key={opt.value}
                        style={[contourStyles.intervalBtn, contourSettings.interval === opt.value && contourStyles.intervalBtnActive]}
                        onPress={() => { const u = { ...contourSettings, interval: opt.value }; setContourSettings(u); saveContourSettings(u); }}
                      >
                        <Text style={[contourStyles.intervalText, contourSettings.interval === opt.value && { color: '#FFFFFF' }]}>{opt.label}</Text>
                      </Pressable>
                    ))}
                  </View>

                  <Text style={contourStyles.sectionLabel}>Color Scheme</Text>
                  {COLOR_SCHEMES.map((scheme) => {
                    const isActive = contourSettings.colorScheme === scheme.key;
                    const stops = scheme.key === 'custom' ? getColorStops(contourSettings) : scheme.stops;
                    return (
                      <Pressable
                        key={scheme.key}
                        style={[contourStyles.schemeRow, isActive && contourStyles.schemeRowActive]}
                        onPress={() => { const u = { ...contourSettings, colorScheme: scheme.key }; setContourSettings(u); saveContourSettings(u); }}
                      >
                        <View style={contourStyles.schemeInfo}>
                          <Text style={[contourStyles.schemeLabel, isActive && { color: palette.accent }]}>{scheme.label}</Text>
                          <Text style={contourStyles.schemeDesc}>{scheme.description}</Text>
                        </View>
                        <View style={contourStyles.gradientPreview}>
                          {stops.map((stop, i) => (<View key={i} style={[contourStyles.gradientStop, { backgroundColor: stop }]} />))}
                        </View>
                        {isActive && <Ionicons name="checkmark-circle" size={20} color={palette.accent} />}
                      </Pressable>
                    );
                  })}

                  <Text style={contourStyles.sectionLabel}>Opacity: {Math.round(contourSettings.opacity * 100)}%</Text>
                  <View style={contourStyles.intervalRow}>
                    {[0.3, 0.5, 0.7, 0.85, 1.0].map((val) => (
                      <Pressable
                        key={val}
                        style={[contourStyles.intervalBtn, contourSettings.opacity === val && contourStyles.intervalBtnActive]}
                        onPress={() => { const u = { ...contourSettings, opacity: val }; setContourSettings(u); saveContourSettings(u); }}
                      >
                        <Text style={[contourStyles.intervalText, contourSettings.opacity === val && { color: '#FFFFFF' }]}>{Math.round(val * 100)}%</Text>
                      </Pressable>
                    ))}
                  </View>

                  <View style={contourStyles.toggleRow}>
                    <Text style={contourStyles.toggleLabel}>Show Depth Labels</Text>
                    <Pressable
                      style={[contourStyles.toggleSwitch, contourSettings.showLabels && contourStyles.toggleSwitchActive]}
                      onPress={() => { const u = { ...contourSettings, showLabels: !contourSettings.showLabels }; setContourSettings(u); saveContourSettings(u); }}
                    >
                      <View style={[contourStyles.toggleThumb, contourSettings.showLabels && contourStyles.toggleThumbActive]} />
                    </Pressable>
                  </View>

                  <Pressable style={contourStyles.resetBtn} onPress={async () => { const d = await resetContourSettings(); setContourSettings(d); }}>
                    <Ionicons name="refresh-outline" size={16} color={palette.textMuted} />
                    <Text style={contourStyles.resetText}>Reset to Defaults</Text>
                  </Pressable>
                </ScrollView>
              </View>
            </TouchableWithoutFeedback>
          </View>
        </TouchableWithoutFeedback>
      </Modal>

      {/* Layer picker popover */}
      <LayerPicker
        visible={layerPickerVisible}
        currentStyle={mapStyle}
        activeOverlays={activeOverlays}
        showQualityPins={showQualityPins}
        userLocation={userLocation}
        poiFilters={[
          { key: 'marinas', label: 'Marinas', ionicon: 'boat-outline', isActive: marinasEnabled && (poiTypeFilters.size === 0 || poiTypeFilters.has('marina')), onToggle: () => togglePOIType('marina') },
          { key: 'fuel-docks', label: 'Fuel Docks', ionicon: 'flame-outline', isActive: marineGasEnabled, onToggle: () => setMarineGasEnabled((p) => !p) },
          { key: 'boat-launches', label: 'Boat Launches', ionicon: 'trail-sign-outline', isActive: accessEnabled, onToggle: () => setAccessEnabled((p) => !p) },
          { key: 'tackle-shops', label: 'Tackle Shops', ionicon: 'cart-outline', isActive: marinasEnabled && poiTypeFilters.has('tackle_shop'), onToggle: () => togglePOIType('tackle_shop') },
        ]}
        onSelectStyle={setMapStyle}
        onToggleOverlay={handleToggleOverlay}
        onToggleQualityPins={() => setShowQualityPins((p) => !p)}
        onClose={() => setLayerPickerVisible(false)}
        panelStyle={{ top: floatingControlsTop + 8 }}
      />

      {/* Tips are only shown on-demand via the lightbulb button on the focused card */}

      {/* Tips modal — shown on-demand from focused card */}
      {showTipsModal && contextualTips.length > 0 && (
        <Modal transparent animationType="fade" visible={showTipsModal} onRequestClose={() => setShowTipsModal(false)}>
          <TouchableWithoutFeedback onPress={() => setShowTipsModal(false)}>
            <View style={styles.tipsModalBackdrop}>
              <TouchableWithoutFeedback>
                <View style={styles.tipsModalContent}>
                  <View style={styles.tipsModalHeader}>
                    <Ionicons name="bulb-outline" size={18} color={palette.warning} />
                    <Text style={styles.tipsModalTitle}>Fishing Tips</Text>
                    <Pressable onPress={() => setShowTipsModal(false)} hitSlop={8}>
                      <Ionicons name="close" size={20} color={palette.textMuted} />
                    </Pressable>
                  </View>
                  <ScrollView style={{ maxHeight: 300 }} showsVerticalScrollIndicator={false}>
                    {contextualTips.map((tip, i) => (
                      <View key={i} style={styles.tipsModalItem}>
                        <Ionicons name={tip.icon as any} size={16} color={palette.accent} style={{ marginTop: 2 }} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.tipsModalItemTitle}>{tip.title}</Text>
                          <Text style={styles.tipsModalItemText}>{tip.text}</Text>
                        </View>
                      </View>
                    ))}
                  </ScrollView>
                </View>
              </TouchableWithoutFeedback>
            </View>
          </TouchableWithoutFeedback>
        </Modal>
      )}

      {/* Access point summary pill — shown when access points are highlighted */}
      {focusedLocation && highlightedAccessSummary && (
        <AccessPointSummaryPill
          summary={highlightedAccessSummary}
          onDismiss={() => {
            setHighlightedAccessPoints([]);
            setHighlightedAccessSummary(null);
          }}
        />
      )}

      {/* Focused location info card */}
      {focusedLocation && (
        <Animated.View style={[styles.focusedCard, { transform: [{ translateX: focusedCardTranslateX }, { translateY: focusedCardTranslateY }], opacity: focusedCardOpacity }]}>
          {/* Swipe handle for dismiss gesture */}
          <View {...focusedCardPanResponder.panHandlers} style={styles.focusedCardSwipeHandle}>
            <View style={styles.focusedCardHandleBar} />
          </View>
          <Pressable style={styles.focusedCardDismiss} onPress={() => setFocusedLocation(null)}>
            <Ionicons name="close" size={18} color={palette.textMuted} />
          </Pressable>
          <View style={styles.focusedCardContent}>
            <View style={styles.focusedScoreBadge}>
              {focusedLocation.score > 0 ? (
                <Text style={[styles.focusedScoreText, { color: scoreColor(focusedLocation.score) }]}>
                  {focusedLocation.score}
                </Text>
              ) : (
                <ActivityIndicator size="small" color={palette.textMuted} />
              )}
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={styles.focusedName}>{getPrimaryLocationLabel(focusedLocation)}</Text>
              <Text style={styles.focusedSubtitle}>{getSecondaryLocationLabel(focusedLocation)}</Text>
              <View style={styles.focusedTags}>
                {focusedLocation.id.startsWith('osm-') ? (
                  <>
                    <View style={styles.focusedTag}>
                      <Ionicons name="water-outline" size={10} color="#3B82C4" />
                      <Text style={styles.focusedTagText}>Water Body</Text>
                    </View>
                    <View style={styles.focusedTag}>
                      <Ionicons name="globe-outline" size={10} color="#3B82C4" />
                      <Text style={styles.focusedTagText}>OSM</Text>
                    </View>
                  </>
                ) : highlightedAccessSummary ? (
                  <>
                  {highlightedAccessSummary.boat_launch > 0 && (
                      <View style={styles.focusedTag}>
                        <Ionicons name="boat-outline" size={10} color={ACCESS_POINT_CONFIG.boat_launch.color} />
                        <Text style={styles.focusedTagText}>{highlightedAccessSummary.boat_launch} Boat Launch{highlightedAccessSummary.boat_launch > 1 ? 'es' : ''}</Text>
                      </View>
                    )}
                    {highlightedAccessSummary.parking > 0 && (
                      <View style={styles.focusedTag}>
                        <Ionicons name="car-outline" size={10} color={ACCESS_POINT_CONFIG.parking.color} />
                        <Text style={styles.focusedTagText}>{highlightedAccessSummary.parking} Parking</Text>
                      </View>
                    )}
                    {highlightedAccessSummary.trailhead > 0 && (
                      <View style={styles.focusedTag}>
                        <Ionicons name="walk-outline" size={10} color={ACCESS_POINT_CONFIG.trailhead.color} />
                        <Text style={styles.focusedTagText}>{highlightedAccessSummary.trailhead} Trailhead{highlightedAccessSummary.trailhead > 1 ? 's' : ''}</Text>
                      </View>
                    )}
                    {highlightedAccessSummary.shore_fishing > 0 && (
                      <View style={styles.focusedTag}>
                        <Ionicons name="fish-outline" size={10} color={ACCESS_POINT_CONFIG.shore_fishing.color} />
                        <Text style={styles.focusedTagText}>{highlightedAccessSummary.shore_fishing} Shore Access</Text>
                      </View>
                    )}
                    {highlightedAccessLoading && (
                      <ActivityIndicator size="small" color={palette.accent} />
                    )}
                  </>
                ) : (
                  <>
                    <View style={styles.focusedTag}>
                      <Ionicons name="car-outline" size={10} color={palette.accent} />
                      <Text style={styles.focusedTagText}>Parking</Text>
                    </View>
                    <View style={styles.focusedTag}>
                      <Ionicons name="boat-outline" size={10} color={palette.accent} />
                      <Text style={styles.focusedTagText}>Boat Launch</Text>
                    </View>
                    <View style={styles.focusedTag}>
                      <Ionicons name="walk-outline" size={10} color={palette.accent} />
                      <Text style={styles.focusedTagText}>Shore Access</Text>
                    </View>
                  </>
                )}
              </View>
            </View>
          </View>
          {/* Inline fishing insights — bite rating, pressure, species, best time */}
          <SpotInsightsCard
            lat={focusedLocation.lat}
            lon={focusedLocation.lon}
            locationId={focusedLocation.id}
            locationName={focusedLocation.name}
            compact
          />
          {/* River spots — show clickable sub-spots along the river */}
          {riverSpots.length > 0 && (
            <View style={{ gap: 4, marginTop: 4 }}>
              <Text style={{ fontSize: 12, fontWeight: '600', color: palette.textSecondary, marginBottom: 2 }}>
                Spots along {focusedLocation.name}
              </Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -8 }} contentContainerStyle={{ paddingHorizontal: 8, gap: 6 }}>
                {riverSpots.map((spot) => (
                  <Pressable
                    key={spot.id}
                    style={{
                      flexDirection: 'row',
                      alignItems: 'center',
                      backgroundColor: palette.surfaceRaised,
                      borderRadius: 8,
                      paddingHorizontal: 10,
                      paddingVertical: 6,
                      gap: 4,
                    }}
                    onPress={() => {
                      cameraRef.current?.setCamera({
                        centerCoordinate: [spot.lon, spot.lat],
                        zoomLevel: 14,
                        animationDuration: 500,
                      });
                    }}
                  >
                    <Ionicons
                      name={(ACCESS_POINT_CONFIG[spot.apType as keyof typeof ACCESS_POINT_CONFIG]?.ionicon ?? 'location') as any}
                      size={12}
                      color={ACCESS_POINT_CONFIG[spot.apType as keyof typeof ACCESS_POINT_CONFIG]?.color ?? palette.accent}
                    />
                    <Text style={{ fontSize: 11, color: palette.text }} numberOfLines={1}>{spot.name}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          )}
          {/* Quick action row: Tips, Share, Save */}
          <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
            {contextualTips.length > 0 && (
              <Pressable
                style={[styles.focusedTipsBtn]}
                onPress={() => setShowTipsModal(true)}
              >
                <Ionicons name="bulb-outline" size={16} color={palette.warning} />
              </Pressable>
            )}
            <Pressable
              style={styles.focusedActionBtn}
              onPress={() => {
                const msg = focusedLocation.name + ' on OpenCatch';
                import('react-native').then(function(m) { m.Share.share({ message: msg }).catch(function() {}); }).catch(function() {});
              }}
            >
              <Ionicons name="share-outline" size={15} color={palette.accent} />
            </Pressable>
            <Pressable
              style={styles.focusedActionBtn}
              onPress={() => {
                setPendingCoord({ latitude: focusedLocation.lat, longitude: focusedLocation.lon });
                setShowWaypointModal(true);
              }}
            >
              <Ionicons name="bookmark-outline" size={15} color={palette.accent} />
            </Pressable>
            <Pressable
              style={styles.focusedActionBtn}
              onPress={() => {
                const url = Platform.select({
                  ios: `maps:?daddr=${focusedLocation.lat},${focusedLocation.lon}`,
                  android: `google.navigation:q=${focusedLocation.lat},${focusedLocation.lon}`,
                  default: `https://www.google.com/maps/dir/?api=1&destination=${focusedLocation.lat},${focusedLocation.lon}`,
                });
                if (url) Linking.openURL(url).catch(() => {});
              }}
            >
              <Ionicons name="compass-outline" size={15} color={palette.accent} />
            </Pressable>
            <View style={{ flex: 1 }} />
          </View>
          {/* Main action buttons row */}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              style={[styles.focusedDetailsBtn, { backgroundColor: '#3B82C4', flex: 1 }]}
              onPress={() => {
                openRouteBuilder(
                  { lat: focusedLocation.lat, lon: focusedLocation.lon },
                  focusedLocation.name,
                );
              }}
            >
              <Ionicons name="navigate-outline" size={16} color="#FFFFFF" />
              <Text style={styles.focusedDetailsBtnText}>Route Here</Text>
            </Pressable>
            <Pressable
              style={[styles.focusedDetailsBtn, { flex: 1 }]}
              onPress={() => {
                setFocusedLocation(null);
                navigation.navigate('LocationDetail', { locationId: focusedLocation.id });
              }}
            >
              <Ionicons name="information-circle-outline" size={16} color="#FFFFFF" />
              <Text style={styles.focusedDetailsBtnText}>Details</Text>
            </Pressable>
          </View>
        </Animated.View>
      )}

      {/* First-time user coach marks (Gaigg Ch.1 — Onboarding pattern) */}
      <CoachMarks mapReady={!loading && userLocation !== null} targets={coachMarkTargets} />

      {/* Long-press contextual menu */}
      <MapLongPressMenu
        visible={longPressMenuVisible}
        coordinate={longPressCoord}
        onAction={handleLongPressAction}
        onClose={() => setLongPressMenuVisible(false)}
      />

      {selectedContourDepth && !focusedLocation && (
        <View style={styles.contourDepthCard}>
          <View style={styles.contourDepthHeader}>
            <View
              style={[
                styles.contourDepthBadge,
                { backgroundColor: getColorForDepth(selectedContourDepth.depthFt) },
              ]}
            >
              <Ionicons name="water" size={15} color="#FFFFFF" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.contourDepthTitle} numberOfLines={1}>
                {selectedContourLake?.lakeName || selectedContourDepth.lakeName || 'Bathymetry'}
              </Text>
              <Text style={styles.contourDepthSubtitle} numberOfLines={1}>
                {selectedContourLake?.sourceLabel || selectedContourDepth.sourceLabel}
                {' · '}
                {(selectedContourLake?.quality || selectedContourDepth.quality).replace('_', ' ')}
              </Text>
            </View>
            <Pressable onPress={clearSelectedContour} hitSlop={8} style={styles.contourDepthDismiss}>
              <Ionicons name="close" size={16} color={palette.textMuted} />
            </Pressable>
          </View>
          <View style={styles.contourDepthMetrics}>
            <Text style={styles.contourDepthPrimary}>
              {formatContourDepth(selectedContourDepth.depthFt, 'feet')}
            </Text>
            <Text style={styles.contourDepthSecondary}>
              {formatContourDepth(selectedContourDepth.depthFt, 'meters')}
            </Text>
            <View style={styles.contourDepthQualityPill}>
              <Ionicons
                name={getContourQualityIcon(selectedContourLake?.quality || selectedContourDepth.quality) as any}
                size={12}
                color={palette.accentDeep}
              />
              <Text style={styles.contourDepthQualityText}>
                {Math.round(selectedContourDepth.confidence * 100)}%
              </Text>
            </View>
          </View>
        </View>
      )}

      {/* Bottom sheet with location list (merged from Explore) */}
      {markerMode === 'locations' && !routePlannerVisible && (
        <Animated.View style={[styles.sheet, { height: sheetHeight }]}>
          <View style={styles.handleArea} {...panResponder.panHandlers}>
            <View style={styles.sheetGrabber}>
              <View style={styles.dragHandle} />
              <Text style={styles.handleHint}>Swipe for nearby spots</Text>
            </View>
            <View style={styles.sheetStatusWrap}>
              <MapInfoBar
                embedded
                expandable={false}
                anchorWatch={anchorStatus.active ? { active: true, driftMeters: anchorStatus.driftDistance, radiusMeters: anchorStatus.watch?.radiusMeters ?? 30 } as AnchorWatchInfo : undefined}
                routeNav={routeNavInfo}
                externalData={marineInstrumentData}
              />
            </View>
          </View>

          {/* Top picks row */}
          {bestFishing.length > 0 && (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.topPicksRow}
            >
              {bestFishing.slice(0, 5).map((entry, i) => (
                <View key={entry.location} style={styles.topPickChip}>
                  <Text style={styles.topPickRank}>#{i + 1}</Text>
                  <Text style={styles.topPickName} numberOfLines={1}>{entry.location}</Text>
                  <Text style={styles.topPickScore}>{entry.fishing_score}</Text>
                </View>
              ))}
            </ScrollView>
          )}

          {/* Quick area insights when sheet is expanded */}
          {mapFeatureLocation && !search.trim() && (
            <View style={styles.sheetInsightsWrapper}>
              <SpotInsightsCard
                lat={mapFeatureLocation.lat}
                lon={mapFeatureLocation.lon}
                locationName={mapFeatureLocationMode === 'map-center' ? 'Map Area' : 'Your Area'}
              />
            </View>
          )}

          {/* Count */}
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>
              {search.trim() ? `Results` : mapFeatureLocation ? `Nearby` : `Spots`}
            </Text>
            <Text style={styles.sheetCount}>
              {displayList.length} spots{discoveredSpots.length > 0 ? ` (${discoveredSpots.length} discovered)` : ''}{discoveryLoading ? ' ...' : ''}{!search.trim() && !mapFeatureLocation ? ' (set a location for nearby)' : ''}
            </Text>
          </View>

          {/* Site card list */}
          <FlatList
            data={displayList}
            renderItem={renderSiteCard}
            keyExtractor={keyExtractor}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
            removeClippedSubviews={false}
            keyboardShouldPersistTaps="handled"
            initialNumToRender={10}
            windowSize={8}
            ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
            ListFooterComponent={<View style={{ height: 100 }} />}
          />
        </Animated.View>
      )}

      {/* Waypoint mode: count badge */}
      {markerMode === 'waypoints' && (
        <View style={styles.countBadge}>
          <Text style={styles.countText}>{countLabel}</Text>
          <Text style={styles.countHint}> -- Long-press to add</Text>
        </View>
      )}

      {/* Subtle loading indicator — never blocks the map */}
      {loading && (
        <View style={styles.loadingBadge}>
          <ActivityIndicator color={palette.accent} size="small" />
        </View>
      )}

      {/* New waypoint modal */}
      <NewWaypointModal
        visible={showWaypointModal}
        coordinate={pendingCoord}
        onSave={handleSaveWaypoint}
        onCancel={handleCancelWaypoint}
      />
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  map: {
    flex: 1,
  },

  // ── Location pin markers ─────────────────────────────────────────
  pinContainer: {
    alignItems: 'center',
  },
  pin: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2.5,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
  },
  pinArrow: {
    width: 0,
    height: 0,
    borderLeftWidth: 6,
    borderRightWidth: 6,
    borderTopWidth: 8,
    borderLeftColor: 'transparent',
    borderRightColor: 'transparent',
    marginTop: -2,
  },

  // ── Waypoint diamond markers ─────────────────────────────────────
  wpMarkerContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    width: 36,
    height: 36,
  },
  wpDiamond: {
    width: 26,
    height: 26,
    borderRadius: 4,
    transform: [{ rotate: '45deg' }],
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
  },

  // ── Map callout ──────────────────────────────────────────────────
  calloutBubble: {
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 10,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
    minWidth: 160,
    maxWidth: 220,
  },
  calloutTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
    marginBottom: 4,
  },
  calloutScoreRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  calloutDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  calloutScore: {
    fontSize: 13,
    fontWeight: '700',
  },
  calloutHint: {
    fontSize: 11,
    color: palette.accent,
    fontWeight: '600',
    marginTop: 2,
  },

  // ── Overlays ─────────────────────────────────────────────────────
  searchOverlay: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 20,
    left: 16,
    right: 16,
    gap: 6,
    zIndex: 30,
  },
  filterChipsRow: {
    gap: 6,
    paddingRight: 8,
  },
  filterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 18,
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  filterChipActive: {
    backgroundColor: palette.accent,
  },
  filterChipText: {
    color: palette.textSecondary,
    fontSize: 11,
    fontWeight: '600',
  },
  filterChipTextActive: {
    color: '#FFFFFF',
  },
  // Topo hint bubble
  topoHintBubble: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 130 : 100,
    alignSelf: 'center',
    left: 16,
    right: 76,
    maxWidth: SCREEN_WIDTH - 92,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.88)',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  topoHintBubbleText: {
    flex: 1,
    color: palette.textSecondary,
    fontSize: 10,
    lineHeight: 14,
  },
  // Contextual tip card (Feature 1)
  // Positioned above the focused card, avoiding right-side button column
  contextualTipCard: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 130 : 100,
    left: 12,
    right: 76,
    maxWidth: SCREEN_WIDTH - 88,
    backgroundColor: 'rgba(255, 255, 255, 0.90)',
    borderRadius: 12,
    padding: 10,
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 5,
  },
  contextualTipHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  contextualTipHeaderText: {
    flex: 1,
    fontFamily: Platform.OS === 'ios' ? 'Playfair Display' : undefined,
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
  },
  contextualTipClose: {
    padding: 2,
  },
  contextualTipBody: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'flex-start',
  },
  contextualTipTitle: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    marginBottom: 2,
  },
  contextualTipText: {
    fontSize: 11,
    lineHeight: 16,
    color: palette.textSecondary,
  },
  contextualTipDots: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 4,
    paddingTop: 2,
  },
  contextualTipDot: {
    width: 5,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: palette.borderLight,
  },
  contextualTipDotActive: {
    backgroundColor: palette.accent,
  },
  contourDepthCard: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 148 : 118,
    left: 12,
    right: 76,
    maxWidth: SCREEN_WIDTH - 88,
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    borderRadius: 14,
    padding: 12,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 3 },
    elevation: 5,
    zIndex: 6,
  },
  contourDepthHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  contourDepthBadge: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  contourDepthTitle: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '700',
  },
  contourDepthSubtitle: {
    color: palette.textSecondary,
    fontSize: 11,
    marginTop: 1,
    textTransform: 'capitalize',
  },
  contourDepthDismiss: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.surfaceRaised,
  },
  contourDepthMetrics: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  contourDepthPrimary: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '700',
  },
  contourDepthSecondary: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '600',
  },
  contourDepthQualityPill: {
    marginLeft: 'auto',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.accentDim,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 999,
  },
  contourDepthQualityText: {
    color: palette.accentDeep,
    fontSize: 11,
    fontWeight: '700',
  },
  // Access summary pill (Feature 2)
  accessSummaryPill: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 200 : 160,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.96)',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.10,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
    maxWidth: SCREEN_WIDTH - 60,
  },
  accessSummaryText: {
    flex: 1,
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  // Highlighted access point markers (Feature 2)
  highlightedApMarker: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2.5,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.20,
    shadowRadius: 5,
    shadowOffset: { width: 0, height: 2 },
    elevation: 6,
  },
  // Focused location card
  focusedCard: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 100 : 72,
    left: 12,
    right: 12,
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    paddingTop: 4,
    paddingHorizontal: 16,
    paddingBottom: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
  },
  focusedCardSwipeHandle: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 8,
  },
  focusedCardHandleBar: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.borderLight,
  },
  focusedTipsBtn: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: 'rgba(255,193,7,0.12)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  focusedActionBtn: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: palette.accentDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  // Tips modal styles
  tipsModalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
    paddingBottom: Platform.OS === 'ios' ? 120 : 90,
    paddingHorizontal: 16,
  },
  tipsModalContent: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
  },
  tipsModalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  tipsModalTitle: {
    flex: 1,
    fontFamily: Platform.OS === 'ios' ? 'Playfair Display' : undefined,
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  tipsModalItem: {
    flexDirection: 'row',
    gap: 10,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  tipsModalItemTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
    marginBottom: 2,
  },
  tipsModalItemText: {
    fontSize: 12,
    lineHeight: 17,
    color: palette.textSecondary,
  },
  focusedCardDismiss: {
    position: 'absolute',
    top: 8,
    right: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1,
  },
  focusedCardContent: {
    flexDirection: 'row',
    gap: 12,
    alignItems: 'flex-start',
  },
  focusedScoreBadge: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  focusedScoreText: {
    fontSize: 18,
    fontWeight: '700',
  },
  focusedName: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
  },
  focusedSubtitle: {
    color: palette.textMuted,
    fontSize: 12,
  },
  focusedTags: {
    flexDirection: 'row',
    gap: 6,
    marginTop: 4,
  },
  focusedTag: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: palette.accentDim,
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: 4,
  },
  focusedTagText: {
    color: palette.accent,
    fontSize: 9,
    fontWeight: '600',
  },
  focusedDetailsBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    backgroundColor: palette.accent,
    paddingVertical: 11,
    borderRadius: 10,
  },
  focusedDetailsBtnText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  segmentedOverlay: {
    marginTop: 2,
    alignSelf: 'center',
    zIndex: 35,
  },
  segmentedControl: {
    flexDirection: 'row',
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 3,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  segmentButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 7,
    borderRadius: 8,
  },
  segmentButtonActive: {
    backgroundColor: palette.accent,
  },
  segmentText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textMuted,
  },
  segmentTextActive: {
    color: '#FFFFFF',
  },

  // ── Map layer toggle ─────────────────────────────────────────────
  layerButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 250 : 210,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 110,
  },
  overlayBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: '#FFFFFF',
  },
  overlayBadgeText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  // ── Map Tools button (replaces 7 individual buttons) ──────────────
  mapToolsButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 300 : 260,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 5,
  },
  mapToolsButtonActive: {
    backgroundColor: palette.accent,
  },
  mapToolsBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
    width: 16,
    height: 16,
    borderRadius: 8,
    backgroundColor: '#FB8C00',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: '#FFFFFF',
  },
  mapToolsBadgeText: {
    fontSize: 9,
    fontWeight: '700',
    color: '#FFFFFF',
  },

  // ── Layer picker popover ──────────────────────────────────────────
  layerPickerCard: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 265 : 225,
    right: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingVertical: 6,
    minWidth: 188,
    maxWidth: 224,
    maxHeight: '62%',
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 20,
    zIndex: 100,
  },
  layerSectionTitle: {
    fontSize: 9,
    fontWeight: '700',
    color: palette.textMuted,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 3,
    letterSpacing: 0.8,
  },
  layerDivider: {
    height: 1,
    backgroundColor: palette.borderLight,
    marginHorizontal: 14,
    marginVertical: 6,
  },
  overlayLabelArea: {
    flex: 1,
  },
  overlayDescription: {
    fontSize: 10,
    color: palette.textMuted,
    marginTop: 1,
  },
  poiChipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    paddingHorizontal: 2,
  },
  poiChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingVertical: 7,
    paddingHorizontal: 12,
    borderRadius: 20,
    backgroundColor: '#F3F4F6',
    borderWidth: 1,
    borderColor: '#E5E7EB',
  },
  poiChipActive: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  poiChipLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  poiChipLabelActive: {
    color: '#FFFFFF',
  },
  topoHintsContainer: {
    gap: 10,
  },
  topoHintRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'flex-start',
  },
  topoHintTitle: {
    color: palette.text,
    fontSize: 12,
    fontWeight: '600',
  },
  topoHintText: {
    color: palette.textSecondary,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 1,
  },
  layerPickerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  layerPickerRowActive: {
    backgroundColor: palette.accentDim,
  },
  layerPickerLabel: {
    fontSize: 12,
    fontWeight: '500',
    color: palette.textSecondary,
    flex: 1,
  },
  layerPickerLabelActive: {
    color: palette.accent,
    fontWeight: '600',
  },
  layerPickerHintCard: {
    marginHorizontal: 14,
    marginTop: 10,
    marginBottom: 6,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    borderWidth: 1,
    borderColor: palette.borderLight,
    gap: 6,
  },
  layerPickerHintHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  layerPickerHintTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
  },
  layerPickerHintText: {
    fontSize: 12,
    lineHeight: 17,
    color: palette.textSecondary,
  },

  // ── Bottom sheet ────────────────────────────────────────────────
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: palette.background,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(18, 56, 79, 0.08)',
    shadowColor: '#000',
    shadowOpacity: 0.18,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: -8 },
    elevation: 24,
    zIndex: 80,
    overflow: 'hidden',
  },
  handleArea: {
    paddingTop: 10,
    paddingHorizontal: 14,
    paddingBottom: 12,
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(35, 80, 104, 0.08)',
    backgroundColor: 'rgba(248, 250, 249, 0.985)',
  },
  sheetGrabber: {
    height: 20,
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'grab' as any,
  },
  sheetStatusWrap: {
    alignSelf: 'stretch',
  },
  dragHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: 'rgba(80, 109, 128, 0.28)',
  },
  handleHint: {
    color: palette.textDim,
    fontSize: 9.5,
    fontWeight: '600',
    marginTop: 4,
  },
  sheetInsightsWrapper: {
    paddingHorizontal: 16,
    paddingBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
    marginBottom: 8,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingBottom: 8,
  },
  sheetTitle: {
    fontFamily: 'PlayfairDisplay-Bold',
    fontSize: 17,
    fontWeight: '400',
    color: palette.text,
    letterSpacing: -0.2,
  },
  sheetCount: {
    fontSize: 13,
    fontWeight: '500',
    color: palette.textMuted,
  },

  // ── Top picks row ────────────────────────────────────────────────
  topPicksRow: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 20,
    paddingBottom: 10,
  },
  topPickChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: palette.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  topPickRank: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.accent,
  },
  topPickName: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    maxWidth: 120,
  },
  topPickScore: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.accent,
  },

  // ── Site card ─────────────────────────────────────────────────
  card: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardPressed: {
    opacity: 0.92,
    transform: [{ scale: 0.98 }],
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
  },
  cardTitleArea: {
    flex: 1,
    gap: 2,
  },
  cardName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  cardSubtitle: {
    fontSize: 12,
    color: palette.textMuted,
  },
  conditionBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  conditionLabel: {
    fontSize: 11,
    fontWeight: '700',
  },
  cardFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 10,
  },
  speciesRow: {
    flexDirection: 'row',
    gap: 6,
    flexWrap: 'wrap',
    flex: 1,
  },
  speciesTag: {
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  speciesTagText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  cardMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  distanceText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
  },

  listContent: {
    paddingHorizontal: 16,
    paddingTop: 4,
  },

  // ── Count badge ──────────────────────────────────────────────────
  countBadge: {
    position: 'absolute',
    bottom: 24,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  countText: {
    color: palette.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },
  countHint: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '400',
  },

  // ── Ruler / measure mode ────────────────────────────────────────
  rulerButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 205 : 165,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 5,
  },
  rulerButtonActive: {
    backgroundColor: palette.accent,
  },
  measureDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: palette.accent,
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  measureBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  measureBannerText: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '600',
  },
  measureBadge: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 100 : 80,
    alignSelf: 'center',
    alignItems: 'center',
    backgroundColor: palette.surface,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 12,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  measureBadgeValue: {
    color: palette.accent,
    fontSize: 18,
    fontWeight: '700',
  },
  measureBadgeSecondary: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '500',
    marginTop: 1,
  },
  routePlannerCard: {
    position: 'absolute',
    left: 8,
    right: 8,
    backgroundColor: 'rgba(252, 253, 251, 0.98)',
    borderRadius: 26,
    paddingHorizontal: 14,
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.08)',
    shadowColor: '#0C2232',
    shadowOpacity: 0.14,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
    zIndex: 18,
    gap: 10,
    maxHeight: SCREEN_HEIGHT * 0.5,
  },
  routePlannerCardNavActive: {
    left: 10,
    right: 10,
    borderRadius: 24,
    paddingTop: 12,
    paddingBottom: 12,
    maxHeight: SCREEN_HEIGHT * 0.36,
  },
  routePlannerSheetHandle: {
    alignSelf: 'center',
    width: 42,
    height: 5,
    borderRadius: 999,
    backgroundColor: 'rgba(53, 91, 117, 0.22)',
    marginBottom: 2,
  },
  routePlannerHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  routePlannerHeaderActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  routePlannerHeaderButton: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  routePlannerTitle: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '700',
  },
  routePlannerSubtitle: {
    color: palette.textMuted,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 2,
  },
  routePlannerSummaryStrip: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    backgroundColor: 'rgba(244, 249, 252, 0.92)',
    borderRadius: 18,
    paddingHorizontal: 12,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.05)',
  },
  routePlannerSummaryCopy: {
    flex: 1,
    minWidth: 0,
  },
  routePlannerSummaryEyebrow: {
    color: palette.textDim,
    fontSize: 10,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 0.45,
  },
  routePlannerSummaryPrimary: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '800',
    marginTop: 2,
    letterSpacing: -0.2,
  },
  routePlannerSummarySecondary: {
    color: palette.textMuted,
    fontSize: 10.5,
    lineHeight: 14,
    marginTop: 3,
  },
  routePlannerStatusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 7,
  },
  routePlannerStatusBadgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  routePlannerPlacementRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'stretch',
  },
  routePlannerPlacementBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 14,
    paddingVertical: 9,
    paddingHorizontal: 10,
    backgroundColor: 'rgba(245, 248, 250, 0.92)',
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.06)',
  },
  routePlannerPlacementBtnActive: {
    backgroundColor: '#355B75',
    borderColor: 'rgba(53, 91, 117, 0.9)',
  },
  routePlannerPlacementText: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '700',
  },
  routePlannerPlacementTextActive: {
    color: '#FFFFFF',
  },
  routePlannerGuideBanner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: 'rgba(229, 241, 251, 0.92)',
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderWidth: 1,
    borderColor: 'rgba(21, 101, 192, 0.10)',
  },
  routePlannerGuideText: {
    flex: 1,
    color: '#355B75',
    fontSize: 11.5,
    lineHeight: 16,
    fontWeight: '700',
  },
  routePlannerHeroRow: {
    flexDirection: 'row',
    gap: 8,
  },
  routePlannerHeroCard: {
    flex: 1,
    minWidth: 0,
    backgroundColor: 'rgba(246, 250, 252, 0.92)',
    borderRadius: 16,
    paddingHorizontal: 10,
    paddingVertical: 10,
    gap: 3,
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.06)',
  },
  routePlannerHeroLabel: {
    color: palette.textDim,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.45,
  },
  routePlannerHeroValue: {
    color: palette.text,
    fontSize: 17,
    fontWeight: '800',
    letterSpacing: -0.4,
  },
  routePlannerHeroSub: {
    color: palette.textMuted,
    fontSize: 10.5,
    lineHeight: 13,
  },
  routePlannerClose: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  routePlannerMetricRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  routePlannerMetricPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: 'rgba(245, 248, 250, 0.92)',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  routePlannerMetricText: {
    color: palette.textSecondary,
    fontSize: 11.5,
    fontWeight: '600',
  },
  routePlannerNavCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: 'rgba(28, 68, 91, 0.06)',
  },
  routePlannerNavIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(53, 91, 117, 0.10)',
  },
  routePlannerNavCopy: {
    flex: 1,
    minWidth: 0,
  },
  routePlannerNavTitle: {
    color: palette.textDim,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.35,
  },
  routePlannerNavSubtitle: {
    color: palette.text,
    fontSize: 12.5,
    fontWeight: '700',
    marginTop: 2,
  },
  routePlannerPrimaryActionRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'stretch',
  },
  routePlannerGoButton: {
    flex: 1,
    minHeight: 42,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    borderRadius: 14,
    backgroundColor: '#355B75',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  routePlannerGoButtonLarge: {
    minHeight: 50,
  },
  routePlannerGoButtonActive: {
    backgroundColor: '#27485D',
  },
  routePlannerGoButtonText: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: 0.1,
  },
  routePlannerPrimarySecondaryBtn: {
    minWidth: 82,
    minHeight: 42,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  routeHandlePin: {
    minWidth: 26,
    height: 26,
    borderRadius: 13,
    paddingHorizontal: 7,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
    shadowColor: '#0C2232',
    shadowOpacity: 0.22,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
    elevation: 5,
  },
  routeHandlePinText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '800',
  },
  routeTurnsBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(12, 22, 30, 0.28)',
    justifyContent: 'flex-end',
  },
  routeTurnsSheet: {
    backgroundColor: 'rgba(252, 253, 251, 0.98)',
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 20,
    maxHeight: SCREEN_HEIGHT * 0.62,
    borderTopWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.08)',
  },
  routeTurnsHandle: {
    alignSelf: 'center',
    width: 42,
    height: 5,
    borderRadius: 999,
    backgroundColor: 'rgba(53, 91, 117, 0.22)',
    marginBottom: 12,
  },
  routeTurnsHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    marginBottom: 10,
  },
  routeTurnsTitle: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '800',
  },
  routeTurnsSubtitle: {
    color: palette.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
  routeTurnsClose: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  routeTurnsScroll: {
    maxHeight: SCREEN_HEIGHT * 0.5,
  },
  routeTurnRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 2,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(35, 80, 104, 0.08)',
  },
  routeTurnRowActive: {
    backgroundColor: 'rgba(230, 242, 251, 0.42)',
    borderRadius: 16,
    paddingHorizontal: 10,
    marginHorizontal: -8,
  },
  routeTurnIndexBubble: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  routeTurnIndexText: {
    fontSize: 12,
    fontWeight: '800',
  },
  routeTurnCopy: {
    flex: 1,
    minWidth: 0,
  },
  routeTurnTitle: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '700',
  },
  routeTurnDetail: {
    color: palette.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
  routeTurnSeverityPill: {
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  routeTurnSeverityText: {
    fontSize: 10.5,
    fontWeight: '700',
  },
  routeTurnArrivalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingTop: 14,
    paddingBottom: 4,
  },
  routeTurnArrivalText: {
    color: palette.textSecondary,
    fontSize: 12.5,
    fontWeight: '700',
  },
  routePlannerControlRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  routePlannerControlBlock: {
    flex: 1,
    minWidth: 150,
    gap: 6,
  },
  routePlannerControlLabel: {
    color: palette.textDim,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  routePlannerStepper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(245, 248, 250, 0.92)',
    borderRadius: 16,
    padding: 4,
    gap: 4,
  },
  routePlannerStepperBtn: {
    width: 28,
    height: 28,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.surfaceRaised,
  },
  routePlannerStepperValueWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  routePlannerStepperValue: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: -0.2,
  },
  routePlannerStepperSub: {
    color: palette.textMuted,
    fontSize: 10.5,
    marginTop: 1,
  },
  routePlannerSegmented: {
    flexDirection: 'row',
    backgroundColor: 'rgba(245, 248, 250, 0.92)',
    borderRadius: 16,
    padding: 4,
    gap: 4,
  },
  routePlannerSegmentBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
  },
  routePlannerSegmentBtnActive: {
    backgroundColor: '#355B75',
  },
  routePlannerSegmentText: {
    color: palette.textSecondary,
    fontSize: 11.5,
    fontWeight: '700',
  },
  routePlannerSegmentTextActive: {
    color: '#FFFFFF',
  },
  routePlannerActionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  routePlannerActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 10,
  },
  routePlannerActionBtnPrimary: {
    backgroundColor: '#355B75',
  },
  routePlannerActionText: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '700',
  },
  routePlannerActionTextPrimary: {
    color: '#FFFFFF',
  },
  routeTargetReticle: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    width: 44,
    height: 44,
    marginLeft: -22,
    marginTop: -22,
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 12,
  },
  routeTargetReticleRing: {
    width: 28,
    height: 28,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: 'rgba(53, 91, 117, 0.92)',
    backgroundColor: 'rgba(255,255,255,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  routeTargetReticleDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#355B75',
  },
  routeTargetReticleHorizontal: {
    position: 'absolute',
    width: 44,
    height: 2,
    backgroundColor: 'rgba(53, 91, 117, 0.45)',
  },
  routeTargetReticleVertical: {
    position: 'absolute',
    width: 2,
    height: 44,
    backgroundColor: 'rgba(53, 91, 117, 0.45)',
  },
  routeTapBanner: {
    position: 'absolute',
    top: '50%',
    alignSelf: 'center',
    marginTop: 36,
    backgroundColor: 'rgba(255,255,255,0.96)',
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 8,
    flexDirection: 'row',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 12,
    maxWidth: SCREEN_WIDTH - 28,
  },
  routeTapBannerText: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '700',
    flexShrink: 1,
    lineHeight: 16,
  },
  measureActions: {
    position: 'absolute',
    bottom: 40,
    alignSelf: 'center',
    flexDirection: 'row',
    gap: 12,
  },
  measureActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  measureActionBtnDone: {
    backgroundColor: palette.accent,
  },
  measureActionText: {
    color: palette.textSecondary,
    fontSize: 14,
    fontWeight: '600',
  },

  // ── Loading ──────────────────────────────────────────────────────
  loadingBadge: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    alignSelf: 'center',
    backgroundColor: 'rgba(250, 250, 247, 0.85)',
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 6,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },

  // ── Waypoint modal ───────────────────────────────────────────────
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(26, 26, 24, 0.45)',
    justifyContent: 'flex-end',
  },
  modalKAV: {
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: palette.background,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: Platform.OS === 'ios' ? 36 : 24,
    paddingTop: 12,
    maxHeight: '85%',
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: -6 },
    elevation: 10,
  },
  modalHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.border,
    alignSelf: 'center',
    marginBottom: 16,
  },
  modalTitle: {
    fontFamily: 'PlayfairDisplay-Bold',
    fontSize: 22,
    fontWeight: '400',
    color: palette.text,
    letterSpacing: -0.3,
    marginBottom: 4,
  },
  modalCoords: {
    fontSize: 12,
    color: palette.textMuted,
    marginBottom: 20,
    fontVariant: ['tabular-nums'],
  },

  // ── Form fields ──────────────────────────────────────────────────
  fieldLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.textMuted,
    marginBottom: 8,
    marginTop: 16,
  },
  textInput: {
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 11,
    fontSize: 15,
    color: palette.text,
  },
  textInputMultiline: {
    height: 80,
    paddingTop: 11,
  },

  // ── Icon chips ───────────────────────────────────────────────────
  chipRow: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  iconChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: palette.border,
    backgroundColor: palette.surface,
  },
  iconChipLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
  },

  // ── Color swatches ───────────────────────────────────────────────
  colorRow: {
    flexDirection: 'row',
    gap: 12,
  },
  colorSwatch: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  colorSwatchSelected: {
    borderWidth: 3,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 4,
  },

  // ── Preview row ──────────────────────────────────────────────────
  previewRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 20,
    backgroundColor: palette.surface,
    borderRadius: 8,
    padding: 12,
  },
  previewLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
    flex: 1,
  },

  // ── Modal action buttons ─────────────────────────────────────────
  modalActions: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 24,
  },
  cancelButton: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: palette.border,
    alignItems: 'center',
  },
  cancelButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  saveButton: {
    flex: 2,
    paddingVertical: 14,
    borderRadius: 10,
    backgroundColor: palette.accent,
    alignItems: 'center',
  },
  saveButtonDisabled: {
    opacity: 0.4,
  },
  saveButtonText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
  },

  // ── Marina POI styles ──────────────────────────────────────────────
  marinaButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 255 : 215,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 5,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  marinaButtonActive: {
    backgroundColor: '#2563EB',
  },
  marinaSpinner: {
    position: 'absolute',
    top: -4,
    right: -4,
  },
  annotateButton: { position: 'absolute', top: Platform.OS === 'ios' ? 405 : 365, zIndex: 5, right: 16, width: 40, height: 40, borderRadius: 20, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  annotateButtonActive: { backgroundColor: palette.accent },
  contourButton: { position: 'absolute', top: Platform.OS === 'ios' ? 455 : 415, zIndex: 5, right: 16, width: 40, height: 40, borderRadius: 20, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  windButton: { position: 'absolute', top: Platform.OS === 'ios' ? 305 : 265, zIndex: 5, right: 16, width: 40, height: 40, borderRadius: 20, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  windButtonActive: { backgroundColor: palette.accent },
  radarButton: { position: 'absolute', top: Platform.OS === 'ios' ? 505 : 465, zIndex: 5, right: 16, width: 40, height: 40, borderRadius: 20, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  radarButtonActive: { backgroundColor: '#1565C0' },
  radarControlBanner: { position: 'absolute', bottom: Platform.OS === 'ios' ? 120 : 100, left: 16, right: 16, flexDirection: 'row', alignItems: 'center', backgroundColor: 'rgba(0,0,0,0.75)', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, gap: 8, zIndex: 10 },
  mapWeatherPanelWrap: {
    position: 'absolute',
    left: 16,
    right: 16,
    zIndex: 26,
    alignItems: 'flex-start',
  },
  radarPlayButton: { width: 32, height: 32, borderRadius: 16, backgroundColor: palette.accent, alignItems: 'center', justifyContent: 'center' },
  radarTimestamp: { color: '#FFFFFF', fontSize: 12, fontWeight: '600', minWidth: 80 },
  radarLegendRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, flex: 1, justifyContent: 'flex-end' },
  stormBanner: { position: 'absolute', top: Platform.OS === 'ios' ? 100 : 80, left: 16, right: 16, flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFEBEE', paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: '#FFCDD2', zIndex: 10, shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  stormBannerTitle: { fontSize: 13, fontWeight: '700', color: '#D50000' },
  stormBannerDetail: { fontSize: 11, color: '#B71C1C', marginTop: 1 },
  windSpinner: { position: 'absolute', top: -4, right: -4 },
  windBanner: { position: 'absolute', top: Platform.OS === 'ios' ? 60 : 40, right: 70, flexDirection: 'row', alignItems: 'center', backgroundColor: palette.surface, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20, shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  windBannerText: { color: palette.text, fontSize: 13, fontWeight: '600', marginRight: 8 },
  windLegend: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  windLegendDot: { width: 8, height: 8, borderRadius: 4, marginLeft: 4 },
  windLegendLabel: { color: palette.textMuted, fontSize: 10, fontWeight: '500' },
  marinaPinOuter: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 1 },
    elevation: 4,
  },
  marinaCallout: {
    backgroundColor: '#FFFFFF',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    minWidth: 180,
    maxWidth: 240,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 5,
  },
  marinaCalloutHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 4,
  },
  marinaCalloutBadge: {
    width: 20,
    height: 20,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  marinaCalloutTitle: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  marinaCalloutType: {
    fontSize: 12,
    fontWeight: '500',
    color: palette.textSecondary,
    marginBottom: 2,
  },
  marinaCalloutDistance: {
    fontSize: 12,
    color: palette.accent,
    fontWeight: '600',
    marginBottom: 4,
  },
  marinaCalloutRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 5,
    marginTop: 3,
  },
  marinaCalloutDetail: {
    flex: 1,
    fontSize: 11,
    color: palette.textSecondary,
    lineHeight: 15,
  },

  // ── Access points styles ────────────────────────────────────────────
  accessButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 355 : 315,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 5,
  },
  accessButtonActive: {
    backgroundColor: '#16A34A',
  },
  accessSpinner: {
    position: 'absolute',
    top: -4,
    right: -4,
  },
  // ── Catch photo overlay styles ────────────────────────────────────
  photosButton: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 505 : 465,
    right: 16,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
    zIndex: 5,
  },
  photosButtonActive: {
    backgroundColor: '#7C3AED',
  },
  photosBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
    minWidth: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: '#7C3AED',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: '#FFFFFF',
    paddingHorizontal: 3,
  },
  photosBadgeText: {
    color: '#FFFFFF',
    fontSize: 9,
    fontWeight: '700',
  },
  photosSpinner: {
    position: 'absolute',
    top: -4,
    right: -4,
  },
  catchPhotoPin: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
    borderWidth: 2,
    borderColor: '#7C3AED',
  },
  catchPhotoInner: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#7C3AED',
    alignItems: 'center',
    justifyContent: 'center',
  },
  catchPhotoCallout: {
    width: 180,
    padding: 8,
    gap: 2,
  },
  catchPhotoCalloutTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
  },
  catchPhotoCalloutDate: {
    fontSize: 11,
    color: palette.textMuted,
  },
  catchPhotoCalloutDetail: {
    fontSize: 11,
    color: palette.textSecondary,
  },
  accessBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 60 : 40,
    right: 70,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  accessPinOuter: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2.5,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
  },

  // ── Weather alert banner ──────────────────────────────────────────
  alertBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 130 : 90,
    left: 16,
    right: 16,
    borderRadius: 10,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 3 },
    elevation: 6,
    zIndex: 20,
  },
  alertBannerContent: {
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  alertBannerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  alertBannerTitle: {
    flex: 1,
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  alertBadgeCount: {
    backgroundColor: 'rgba(255,255,255,0.3)',
    borderRadius: 10,
    paddingHorizontal: 7,
    paddingVertical: 1,
    minWidth: 20,
    alignItems: 'center',
  },
  alertBadgeCountText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  alertBannerDismiss: {
    marginLeft: 4,
  },
  alertBannerExpanded: {
    paddingHorizontal: 14,
    paddingBottom: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(255,255,255,0.3)',
    paddingTop: 10,
    gap: 8,
  },
  alertBannerSummary: {
    fontSize: 13,
    color: '#FFFFFF',
    lineHeight: 19,
  },
  alertBannerInstruction: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.85)',
    fontStyle: 'italic',
    lineHeight: 17,
  },
  alertBannerViewAll: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,255,255,0.25)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 14,
    marginTop: 4,
  },
  alertBannerViewAllText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#FFFFFF',
  },

  // ── Recording overlay ──────────────────────────────────────────
  recordingBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 118 : 78,
    left: 16,
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(196, 75, 75, 0.94)',
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 10,
    shadowColor: '#C44B4B',
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
  },
  recordingBannerDot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    justifyContent: 'center',
    alignItems: 'center',
  },
  recordingBannerDotInner: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#FFFFFF',
  },
  recordingBannerTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  recordingBannerSub: {
    fontSize: 11,
    color: 'rgba(255,255,255,0.8)',
    marginTop: 1,
  },
  dismissTrackBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 118 : 78,
    left: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    gap: 6,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 3,
  },
  dismissTrackText: {
    fontSize: 12,
    color: palette.textMuted,
    fontWeight: '500',
  },

  // Anchor Watch banner
  anchorWatchBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 118 : 78,
    left: 16,
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(10, 110, 189, 0.94)',
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 10,
    shadowColor: palette.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
  },
  anchorWatchBannerAlarm: {
    backgroundColor: 'rgba(196, 75, 75, 0.96)',
    shadowColor: '#C44B4B',
  },
  anchorWatchBannerIcon: {
    fontSize: 20,
    color: '#FFFFFF',
  },
  anchorWatchBannerTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  anchorWatchBannerSub: {
    fontSize: 11,
    color: 'rgba(255,255,255,0.8)',
    marginTop: 1,
    fontVariant: ['tabular-nums'],
  },
  anchorWatchBannerBearing: {
    fontSize: 15,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.9)',
    fontVariant: ['tabular-nums'],
  },

  // MOB banner
  mobBanner: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 118 : 78,
    left: 16,
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(183, 28, 28, 0.96)',
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 10,
    shadowColor: '#B71C1C',
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 4 },
    elevation: 10,
  },
  mobBannerDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: '#FFFFFF',
  },
  mobBannerTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 1,
  },
  mobBannerSub: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.85)',
    marginTop: 1,
    fontVariant: ['tabular-nums'],
  },
});

const annotStyles = StyleSheet.create({
  toolbar: { position: 'absolute', bottom: Platform.OS === 'ios' ? 100 : 80, left: 12, right: 12, backgroundColor: palette.surface, borderRadius: 16, padding: 10, shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 10, shadowOffset: { width: 0, height: -2 }, elevation: 6 },
  toolbarRow: { flexDirection: 'row', justifyContent: 'space-around', alignItems: 'center', marginBottom: 6 },
  toolBtn: { alignItems: 'center', justifyContent: 'center', paddingVertical: 6, paddingHorizontal: 10, borderRadius: 10, backgroundColor: palette.surfaceAlt || '#F0F0F0', minWidth: 52 },
  toolBtnActive: { backgroundColor: palette.accent },
  toolLabel: { fontSize: 10, fontWeight: '600', color: palette.textSecondary, marginTop: 2 },
  colorRow: { flexDirection: 'row', justifyContent: 'center', gap: 10, paddingTop: 4 },
  colorDot: { width: 24, height: 24, borderRadius: 12 },
  colorDotActive: { borderWidth: 2.5, borderColor: palette.text },
  hint: { fontSize: 11, color: palette.textMuted, textAlign: 'center', marginTop: 4 },
  modalOverlay: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center', zIndex: 9999 },
  modalContainer: { width: '85%' },
  modalCard: { backgroundColor: palette.surface, borderRadius: 16, padding: 20, shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 12, elevation: 8 },
  modalTitle: { fontSize: 18, fontFamily: 'PlayfairDisplay-Bold', color: palette.text, marginBottom: 12 },
  modalInput: { backgroundColor: palette.background, borderRadius: 10, padding: 12, fontSize: 15, color: palette.text, borderWidth: 1, borderColor: palette.border, minHeight: 80, textAlignVertical: 'top' },
  modalButtons: { flexDirection: 'row', justifyContent: 'flex-end', gap: 10, marginTop: 14 },
  modalBtnCancel: { paddingVertical: 10, paddingHorizontal: 18, borderRadius: 10 },
  modalBtnCancelText: { fontSize: 14, color: palette.textSecondary, fontWeight: '600' },
  modalBtnSave: { paddingVertical: 10, paddingHorizontal: 20, borderRadius: 10, backgroundColor: palette.accent },
  modalBtnSaveText: { fontSize: 14, color: '#FFFFFF', fontWeight: '700' },
  annotCallout: { backgroundColor: palette.surface, padding: 8, borderRadius: 8, maxWidth: 160, shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 4, elevation: 3 },
  annotCalloutLabel: { fontSize: 13, fontWeight: '600', color: palette.text },
  annotCalloutDate: { fontSize: 10, color: palette.textMuted, marginTop: 2 },
  annotCalloutDelete: { fontSize: 11, color: palette.error, fontWeight: '600', marginTop: 4 },
});

const contourStyles = StyleSheet.create({
  card: { backgroundColor: palette.surface, borderRadius: 16, padding: 20, width: '90%', maxWidth: 400, shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 12, elevation: 8 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  title: { fontSize: 18, fontFamily: 'PlayfairDisplay-Bold', color: palette.text },
  sectionLabel: { fontSize: 13, fontWeight: '700', color: palette.textSecondary, marginTop: 14, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 },
  intervalRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  intervalBtn: { paddingVertical: 8, paddingHorizontal: 14, borderRadius: 10, backgroundColor: palette.surfaceRaised, borderWidth: 1, borderColor: palette.border },
  intervalBtnActive: { backgroundColor: palette.accent, borderColor: palette.accent },
  intervalText: { fontSize: 13, fontWeight: '600', color: palette.text },
  schemeRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, paddingHorizontal: 10, borderRadius: 10, marginBottom: 6, borderWidth: 1, borderColor: palette.borderLight },
  schemeRowActive: { borderColor: palette.accent, backgroundColor: palette.accentDim },
  schemeInfo: { flex: 1, marginRight: 10 },
  schemeLabel: { fontSize: 14, fontWeight: '600', color: palette.text },
  schemeDesc: { fontSize: 11, color: palette.textMuted, marginTop: 2 },
  gradientPreview: { flexDirection: 'row', borderRadius: 4, overflow: 'hidden', marginRight: 8 },
  gradientStop: { width: 16, height: 16 },
  toggleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 14, paddingVertical: 8 },
  toggleLabel: { fontSize: 14, fontWeight: '600', color: palette.text },
  toggleSwitch: { width: 48, height: 28, borderRadius: 14, backgroundColor: palette.surfaceRaised, borderWidth: 1, borderColor: palette.border, justifyContent: 'center', paddingHorizontal: 2 },
  toggleSwitchActive: { backgroundColor: palette.accent, borderColor: palette.accent },
  toggleThumb: { width: 22, height: 22, borderRadius: 11, backgroundColor: '#FFFFFF', shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 2, elevation: 2 },
  toggleThumbActive: { alignSelf: 'flex-end' },
  resetBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16, paddingVertical: 10 },
  resetText: { fontSize: 13, color: palette.textMuted, fontWeight: '600' },
});
