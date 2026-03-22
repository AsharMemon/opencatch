import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Svg, { Circle, Line, Text as SvgText, G, Polygon } from 'react-native-svg';
import {
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
import { api, buildTileSourceUrl } from '../services/api';
import { TILE_SERVER_DEPLOYED } from '../config/network';
import { fetchNearbyMarinas, formatAmenities } from '../services/marinaDirectory';
import type { MarinaPOI, MarinaPOIType } from '../services/marinaDirectory';
import { fetchWindGrid, windGridToGeoJSON } from '../services/windOverlay';
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
import { SpeedCourseHUD } from '../components/SpeedCourseHUD';
import { NoWakeZoneOverlay } from '../components/NoWakeZoneOverlay';
import { NavigationAidsOverlay } from '../components/NavigationAidsOverlay';
import { ArtificialReefOverlay } from '../components/ArtificialReefOverlay';
import { WindOverlayAnimated } from '../components/WindOverlayAnimated';
import { WaveOverlayAnimated } from '../components/WaveOverlayAnimated';
import { DepthNumberOverlay } from '../components/DepthNumberOverlay';
import { MarineGasOverlay } from '../components/MarineGasOverlay';
import { OceanFishingSpotsOverlay } from '../components/OceanFishingSpotsOverlay';
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
import { cacheLocationDetail, prefetchLocationDetails } from '../services/locationDetailCache';
import { clearLocationDataCache } from '../services/locationDataCache';
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
  buildContourColorExpression,
  buildContourWidthExpression,
  getColorStops,
  CONTOUR_INTERVALS,
  COLOR_SCHEMES,
  DEFAULT_CONTOUR_SETTINGS,
  type DepthContourSettings,
} from '../services/depthContourSettings';
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
import { QuickActionFAB } from '../components/QuickActionFAB';
import { MapToolsDrawer, type MapToolGroup, type OverlayInfoCard } from '../components/MapToolsDrawer';
import { CoachMarks } from '../components/CoachMarks';
import { MapLongPressMenu, type LongPressAction, type LongPressCoordinate } from '../components/MapLongPressMenu';
import { MapInfoBar, type AnchorWatchInfo } from '../components/MapInfoBar';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

// ── Constants ─────────────────────────────────────────────────────

const DEFAULT_CENTER: [number, number] = [-95.0, 45.0]; // [lng, lat] — centered to show US + Canada
const DEFAULT_ZOOM = 3.5;

// AsyncStorage keys for persisting last map view
const MAP_STATE_KEY = 'opencatch_map_state';
const LAST_LOCATION_KEY = 'opencatch_last_location';

// Module-level cache for discovered spots — survives tab switches (component unmount/remount)
let _cachedDiscoveredSpots: DiscoveredSpot[] = [];
let _cachedDiscoveryBbox: BoundingBox | null = null;
let _cachedDiscoveryZoom: number = 0;

// Bottom sheet snap points
const SHEET_HIDDEN = 40; // Fully collapsed — just the drag handle visible
const SHEET_PEEK = 100;
const SHEET_COLLAPSED = 260;
const SHEET_EXPANDED = SCREEN_HEIGHT * 0.75;
const SNAP_POINTS = [SHEET_HIDDEN, SHEET_PEEK, SHEET_COLLAPSED, SHEET_EXPANDED];
const HANDLE_HEIGHT = 32;

function closestSnap(value: number, velocity: number = 0): number {
  const projected = value - velocity * 0.15;
  return SNAP_POINTS.reduce((prev, curr) =>
    Math.abs(curr - projected) < Math.abs(prev - projected) ? curr : prev,
  );
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

type MapStyleKey = 'hybrid' | 'bathymetry' | 'satellite' | 'outdoors' | 'topo' | 'night' | 'nautical-chart';
const MAP_STYLE_LABELS: Record<MapStyleKey, string> = {
  hybrid: 'Hybrid',
  bathymetry: 'Bathymetry',
  satellite: 'Satellite',
  outdoors: 'Outdoors',
  topo: 'Topographic',
  night: 'Night',
  'nautical-chart': 'Nautical Chart',
};
const MAP_STYLE_IONICONS: Record<MapStyleKey, string> = {
  hybrid: 'earth-outline',
  bathymetry: 'water-outline',
  satellite: 'planet-outline',
  outdoors: 'compass-outline',
  topo: 'analytics-outline',
  night: 'moon-outline',
  'nautical-chart': 'boat-outline',
};
const MAP_STYLE_KEYS: MapStyleKey[] = ['hybrid', 'bathymetry', 'satellite', 'outdoors', 'topo', 'night', 'nautical-chart'];

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
  { key: 'local-bathymetry', label: 'Lake Contours', ionicon: 'analytics-outline', description: 'Local bathymetry vector contours from PostGIS' },
  { key: 'public-lands', label: 'Public Lands', ionicon: 'leaf-outline', description: 'Protected and public-access lands overlay' },
  { key: 'access-points', label: 'Access Points', ionicon: 'fish-outline', description: 'Boat launches, shore access, campgrounds' },
  { key: 'parking', label: 'Parking', ionicon: 'car-outline', description: 'Parking lots and pull-offs near access' },
  { key: 'trails', label: 'Trails', ionicon: 'walk-outline', description: 'Named access trails and paths to water' },
  { key: 'nautical', label: 'Nautical Marks', ionicon: 'boat-outline', description: 'OpenSeaMap buoys, channels, marks', context: 'coastal' },
  { key: 'shaded-relief', label: 'Shaded Relief', ionicon: 'layers-outline', description: '3D terrain and elevation' },
  { key: 'water-flow', label: 'Hydrology', ionicon: 'water-outline', description: 'USGS streams & water features' },
  { key: 'depth-contours', label: 'Depth Contours', ionicon: 'resize-outline', description: 'GEBCO bathymetry lines' },
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
} as const;

const ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS = true;
const GLYPH_URL = 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf';
const FONT_STACKS = {
  regular: ['Open Sans Regular'],
  italic: ['Open Sans Italic'],
  bold: ['Open Sans Bold'],
} as const;
// Vector overlays enabled by default.
// 'local-bathymetry' is OFF until tiles.opencatch.app is deployed.
const DEFAULT_VECTOR_OVERLAYS = new Set([
  'public-lands',
  'access-points',
  'parking',
  'trails',
]);

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
        'background-color': '#0a1628',
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
          0, '#08306b',   // zoomed out = deep ocean indigo
          4, '#08519c',
          6, '#2171b5',
          8, '#4292c6',
          10, '#6baed6',
          14, '#9ecae1',  // zoomed in = shallow coastal blue
        ],
        'fill-opacity': 0.92,
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
        'fill-color': '#6baed6',
        'fill-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0,
          5, 0.08,
          8, 0.15,
          12, 0.05,
        ],
      },
    },

    // ── GEBCO bathymetry contour raster overlay ─────────────────
    {
      id: 'gebco-contour-overlay',
      type: 'raster',
      source: 'gebco-contours',
      paint: {
        'raster-opacity': [
          'interpolate',
          ['linear'],
          ['zoom'],
          0, 0.35,
          6, 0.55,
          10, 0.4,
          14, 0.2,
        ],
        'raster-contrast': 0.1,
        'raster-brightness-min': 0.0,
        'raster-brightness-max': 0.7,
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
          'grass', '#e8e4d0',
          'wood', '#ddd8c4',
          'ice', '#eef4f8',
          'crop', '#ebe7d3',
          '#f5f0e1', // default warm beige
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
          '#f5f0e1',
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
        'fill-color': '#f5f0e1',
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
        'line-color': '#6baed6',
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
          4, 0.3,
          8, 0.6,
          14, 0.8,
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
  'local-bathymetry': TILE_LAYER_NAMES.bathymetryContours,
  'public-lands': TILE_LAYER_NAMES.publicLands,
  'access-points': TILE_LAYER_NAMES.accessPoints,
  'parking': TILE_LAYER_NAMES.accessPoints,
  'trails': TILE_LAYER_NAMES.accessPoints,
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

interface LayerPickerProps {
  visible: boolean;
  currentStyle: MapStyleKey;
  activeOverlays: Set<string>;
  showQualityPins: boolean;
  userLocation: { lat: number; lon: number } | null;
  onSelectStyle: (style: MapStyleKey) => void;
  onToggleOverlay: (key: string) => void;
  onToggleQualityPins: () => void;
  onClose: () => void;
}

function LayerPicker({ visible, currentStyle, activeOverlays, showQualityPins, userLocation: pickerUserLoc, onSelectStyle, onToggleOverlay, onToggleQualityPins, onClose }: LayerPickerProps) {
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
      <Animated.View style={[styles.layerPickerCard, { opacity: fadeAnim }]}>
        <ScrollView style={{ maxHeight: 460 }} showsVerticalScrollIndicator={false} bounces={false}>
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

          <View style={styles.layerDivider} />
          <Text style={styles.layerSectionTitle}>OVERLAYS</Text>
          {OVERLAY_LAYERS.filter((layer) => {
            // Contextual filtering: only show relevant overlays (Gaigg Ch.4 — reduce data overload)
            if (layer.context === 'coastal') {
              return pickerUserLoc ? isNearCoast(pickerUserLoc.lat, pickerUserLoc.lon) : false;
            }
            if (layer.context === 'winter') {
              const m = new Date().getMonth() + 1;
              return m >= 11 || m <= 3;
            }
            return true;
          }).map((layer) => {
            const isActive = activeOverlays.has(layer.key);
            return (
              <Pressable
                key={layer.key}
                style={[styles.layerPickerRow, isActive && styles.layerPickerRowActive]}
                onPress={() => onToggleOverlay(layer.key)}
              >
                <Ionicons
                  name={layer.ionicon as any}
                  size={18}
                  color={isActive ? palette.accent : palette.textSecondary}
                />
                <View style={styles.overlayLabelArea}>
                  <Text
                    style={[
                      styles.layerPickerLabel,
                      isActive && styles.layerPickerLabelActive,
                    ]}
                  >
                    {layer.label}
                  </Text>
                  <Text style={styles.overlayDescription}>{layer.description}</Text>
                </View>
                {isActive && <Ionicons name="checkmark" size={16} color={palette.accent} />}
              </Pressable>
            );
          })}

          <View style={styles.layerDivider} />
          <Text style={styles.layerSectionTitle}>READING THE MAP</Text>
          <View style={styles.topoHintsContainer}>
            {[
              { icon: 'trending-down-outline', title: 'Drop-offs & Ledges', text: 'Look for closely spaced contour lines — steep depth changes attract bass, especially in spring.' },
              { icon: 'git-merge-outline', title: 'Points & Humps', text: 'Underwater points jutting into deep water are ambush spots. Fish stage here before moving shallow.' },
              { icon: 'water-outline', title: 'Creek Channels', text: 'Submerged creek beds act as highways for fish. Follow the deepest contour lines.' },
              { icon: 'leaf-outline', title: 'Flats Near Deep Water', text: 'Shallow flats adjacent to deep water are feeding zones. Bass move up to feed and retreat to depth.' },
              { icon: 'snow-outline', title: 'Early Spring', text: 'Bass spawn in 2-6 ft on hard bottom near drop-offs. Look for flats with nearby deep water access.' },
              { icon: 'flash-outline', title: 'Pike & Musky', text: 'Ambush predators love weed edges, creek mouths, and shallow bays connected to deep channels.' },
            ].map((hint, i) => (
              <View key={i} style={styles.topoHintRow}>
                <Ionicons name={hint.icon as any} size={14} color={palette.accent} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.topoHintTitle}>{hint.title}</Text>
                  <Text style={styles.topoHintText}>{hint.text}</Text>
                </View>
              </View>
            ))}
          </View>
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

  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
      onPress={onPress}
    >
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleArea}>
          <Text style={styles.cardName} numberOfLines={1}>
            {location.name || 'Unseen Site'}
          </Text>
          <Text style={styles.cardSubtitle} numberOfLines={1}>
            {location.subtitle || 'Water Body'}
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
function CompassWidget({ heading, mode, onToggleMode }: { heading: number; mode: CompassMode; onToggleMode: () => void }) { const animatedRotation = useRef(new Animated.Value(0)).current; const lastH = useRef(0); useEffect(() => { let d = heading - lastH.current; if (d > 180) d -= 360; if (d < -180) d += 360; const t = lastH.current + d; lastH.current = t; Animated.timing(animatedRotation, { toValue: -t, duration: 250, useNativeDriver: true }).start(); }, [heading, animatedRotation]); const rotI = animatedRotation.interpolate({ inputRange: [-720, 720], outputRange: ['-720deg', '720deg'] }); const cardinal = degreesToCardinal(heading); const degLabel = `${Math.round(((heading % 360) + 360) % 360)}\u00B0`; return (<Pressable style={[cwStyles.container, mode === 'heading' && cwStyles.containerActive]} onPress={onToggleMode} accessibilityLabel={`Compass: ${degLabel} ${cardinal}. Tap to toggle heading mode.`}><Animated.View style={{ transform: [{ rotate: rotI }] }}><CompassRoseSvg /></Animated.View><View style={cwStyles.readout}><Text style={cwStyles.degrees}>{degLabel}</Text><Text style={cwStyles.cardinal}>{cardinal}</Text></View>{mode === 'heading' && <View style={cwStyles.trackingDot} />}</Pressable>); }
const cwStyles = StyleSheet.create({ container: { position: 'absolute', top: Platform.OS === 'ios' ? 155 : 115, left: 16, width: COMPASS_SIZE + 8, alignItems: 'center', backgroundColor: palette.surface, borderRadius: (COMPASS_SIZE + 8) / 2, paddingVertical: 4, paddingHorizontal: 4, shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 }, containerActive: { borderWidth: 1.5, borderColor: palette.accent }, readout: { flexDirection: 'row', alignItems: 'baseline', gap: 2, marginTop: 1, marginBottom: 2 }, degrees: { fontSize: 10, fontWeight: '700', color: palette.text }, cardinal: { fontSize: 8, fontWeight: '600', color: palette.textSecondary }, trackingDot: { position: 'absolute', top: 4, right: 4, width: 6, height: 6, borderRadius: 3, backgroundColor: palette.accent } });

// ── MapScreen ─────────────────────────────────────────────────────

type MarkerMode = 'locations' | 'waypoints';

export function MapScreen({ navigation }: TabProps<'MapTab'>) {
  const cameraRef = useRef<CameraRef>(null);
  const mapRef = useRef<MapViewRef>(null);
  const locationSourceRef = useRef<any>(null);

  const [locations, setLocations] = useState<FishingLocation[]>([]);
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);
  const [bestFishing, setBestFishing] = useState<BestFishingV2Entry[]>([]);
  const [search, setSearch] = useState('');
  const [activeFilters, setActiveFilters] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [userLocation, setUserLocation] = useState<{ lat: number; lon: number } | null>(null);

  // Map style selection — default to hybrid (satellite land + bathymetry water)
  const [mapStyle, setMapStyle] = useState<MapStyleKey>('hybrid');
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

  // Map Tools drawer (progressive disclosure — avoids Kitchen Sink pattern)
  const [mapToolsDrawerOpen, setMapToolsDrawerOpen] = useState(false);

  // Measure / ruler mode
  const [measureMode, setMeasureMode] = useState(false);
  const [measurePoints, setMeasurePoints] = useState<[number, number][]>([]);

  // Compass heading
  const [compassMode, setCompassMode] = useState<CompassMode>('static');
  const [compassHeading, setCompassHeading] = useState(0);

  // Weather alerts
  const [weatherAlerts, setWeatherAlerts] = useState<FishingWeatherAlert[]>([]);
  const [alertBannerDismissed, setAlertBannerDismissed] = useState(false);
  const [alertBannerExpanded, setAlertBannerExpanded] = useState(false);
  const alertRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Wind overlay
  const [windEnabled, setWindEnabled] = useState(false);
  const [windGeoJSON, setWindGeoJSON] = useState<any>(null);
  const [windLoading, setWindLoading] = useState(false);
  const windDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
  const lastWindCenter = useRef<{ lat: number; lon: number } | null>(null);

  // Access points overlay
  const [accessEnabled, setAccessEnabled] = useState(false);
  const [accessPoints, setAccessPoints] = useState<AccessPoint[]>([]);
  const [accessTrailGeoJSON, setAccessTrailGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const accessFetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAccessCenter = useRef<{ lat: number; lon: number } | null>(null);

  // Animated wind overlay (enhanced)
  const [windAnimatedEnabled, setWindAnimatedEnabled] = useState(false);

  // Wave height overlay (coastal only)
  const [waveEnabled, setWaveEnabled] = useState(false);

  // Depth number overlay (nautical chart soundings)
  const [depthNumbersEnabled, setDepthNumbersEnabled] = useState(false);

  // Marine gas stations overlay
  const [marineGasEnabled, setMarineGasEnabled] = useState(false);

  // Ocean fishing spots overlay (coastal only)
  const [oceanSpotsEnabled, setOceanSpotsEnabled] = useState(false);

  // 3D terrain / relief shading
  const [terrain3DEnabled, setTerrain3DEnabled] = useState(false);

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

  // Bottom sheet
  const sheetHeight = useRef(new Animated.Value(SHEET_COLLAPSED)).current;
  const currentHeight = useRef(SHEET_COLLAPSED);
  const dragStartHeight = useRef(SHEET_COLLAPSED);
  const [sheetSettled, setSheetSettled] = useState(true);

  // Tips modal (shown on-demand from focused card)
  const [showTipsModal, setShowTipsModal] = useState(false);

  // Load annotations and contour settings from AsyncStorage on mount
  useEffect(() => {
    loadAnnotations().then(setAnnotations);
    loadContourSettings().then(setContourSettings);
  }, []);

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
      damping: 28,
      stiffness: 300,
      mass: 0.7,
      useNativeDriver: false,
      overshootClamping: false,
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
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (_, gs) => Math.abs(gs.dy) > 2,
        onMoveShouldSetPanResponderCapture: (_, gs) => Math.abs(gs.dy) > 2,
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
      if (windDebounceRef.current) clearTimeout(windDebounceRef.current);
      if (accessFetchTimer.current) clearTimeout(accessFetchTimer.current);
      if (mapStateSaveTimer.current) clearTimeout(mapStateSaveTimer.current);
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
      if (alertRefreshRef.current) { clearInterval(alertRefreshRef.current); alertRefreshRef.current = null; }
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

  // ── Pause all timers when app goes to background (saves battery) ──
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState: AppStateStatus) => {
      if (appStateRef.current.match(/active/) && nextState.match(/inactive|background/)) {
        // App going to background — clear polling timers
        if (alertRefreshRef.current) { clearInterval(alertRefreshRef.current); alertRefreshRef.current = null; }
        if (marinaFetchTimer.current) { clearTimeout(marinaFetchTimer.current); marinaFetchTimer.current = null; }
        if (windDebounceRef.current) { clearTimeout(windDebounceRef.current); windDebounceRef.current = null; }
        if (accessFetchTimer.current) { clearTimeout(accessFetchTimer.current); accessFetchTimer.current = null; }
        if (discoveryTimer.current) { clearTimeout(discoveryTimer.current); discoveryTimer.current = null; }
        if (mapStateSaveTimer.current) { clearTimeout(mapStateSaveTimer.current); mapStateSaveTimer.current = null; }
        // Release cached location data to free memory while backgrounded
        clearLocationDataCache();
      } else if (nextState === 'active' && appStateRef.current.match(/inactive|background/)) {
        // App returning to foreground — restart alert polling
        if (userLocation) {
          fetchWeatherAlerts(userLocation.lat, userLocation.lon);
          alertRefreshRef.current = setInterval(() => {
            fetchWeatherAlerts(userLocation.lat, userLocation.lon);
          }, ALERT_REFRESH_MS);
        }
      }
      appStateRef.current = nextState;
    });
    return () => sub.remove();
  }, [userLocation, fetchWeatherAlerts]);

  useEffect(() => {
    if (!userLocation) return;
    fetchWeatherAlerts(userLocation.lat, userLocation.lon);

    alertRefreshRef.current = setInterval(() => {
      fetchWeatherAlerts(userLocation.lat, userLocation.lon);
    }, ALERT_REFRESH_MS);

    return () => {
      if (alertRefreshRef.current) {
        clearInterval(alertRefreshRef.current);
        alertRefreshRef.current = null;
      }
    };
  }, [userLocation, fetchWeatherAlerts]);

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

  // Pre-bundled top fishing spots (appear instantly on startup)
  const staticSpots = useMemo(() => getTopFishingSpots(), []);

  // Merge backend locations + static spots + dynamically discovered spots
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

    // 3. Static pre-bundled spots (lowest priority, fill the map on startup)
    for (const sl of staticSpots) {
      const ck = coordKey(sl.lat, sl.lon);
      if (!seen.has(sl.id) && !seen.has(ck)) {
        seen.add(sl.id);
        seen.add(ck);
        result.push(sl);
      }
    }

    return result;
  }, [locations, discoveredSpots, staticSpots]);

  // ── Computed lists ──────────────────────────────────────────────

  const locationsWithDistance = useMemo(() => {
    return allLocations.map((loc) => ({
      location: loc,
      distanceMi: userLocation
        ? haversineDistance(userLocation.lat, userLocation.lon, loc.lat, loc.lon)
        : null,
    }));
  }, [allLocations, userLocation]);

  const displayList = useMemo(() => {
    let list = [...locationsWithDistance];
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (item) =>
          item.location.name.toLowerCase().includes(q) ||
          item.location.subtitle.toLowerCase().includes(q),
      );
    } else if (userLocation) {
      // When not searching, only show spots within 100 km (~62 mi) of user
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
  }, [locationsWithDistance, search, userLocation]);

  // ── GeoJSON sources for MapLibre markers ────────────────────────

  // Main location GeoJSON — no longer depends on selectedMarkerId to avoid
  // expensive full-collection rebuilds on every pin tap.
  const locationGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: (search.trim()
      ? allLocations.filter(
          (l) =>
            l.name.toLowerCase().includes(search.toLowerCase()) ||
            l.subtitle.toLowerCase().includes(search.toLowerCase()),
        )
      : allLocations
    ).map((loc) => {
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
  }), [allLocations, search, showQualityPins]);

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

  const marinaGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: marinaPOIs.map((poi) => ({
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
  }), [marinaPOIs]);

  const accessGeoJSON = useMemo(
    () => accessPointsToGeoJSON(accessPoints),
    [accessPoints],
  );

  // ── Overlay raster sources (built dynamically) ──────────────────
  // Overlays are added as raster sources within the style. For simplicity
  // we render them as additional RasterSource + RasterLayer components.

  // ── Handlers ────────────────────────────────────────────────────

  const [currentZoom, setCurrentZoom] = useState(3.5);

  // ── Marina POI fetching ──────────────────────────────────────────

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
      const pois = await fetchNearbyMarinas(lat, lon, 25_000);
      setMarinaPOIs(pois);
      lastMarinaCenter.current = { lat, lon };
    } catch {
      // Silently fail — cache may still serve stale data
    } finally {
      setMarinasLoading(false);
    }
  }, []);

  // When marinas are toggled on, fetch for current map center
  useEffect(() => {
    if (!marinasEnabled) {
      setMarinaPOIs([]);
      setSelectedMarina(null);
      lastMarinaCenter.current = null;
      return;
    }
    // Use user location as initial center or default
    const center = userLocation ?? { lat: DEFAULT_CENTER[1], lon: DEFAULT_CENTER[0] };
    fetchMarinasForCenter(center.lat, center.lon);
  }, [marinasEnabled, userLocation, fetchMarinasForCenter]);

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

  // ── Wind overlay data fetching ──────────────────────────────────
  const loadWindData = useCallback(async (lat: number, lon: number) => {
    setWindLoading(true);
    try {
      const grid = await fetchWindGrid(lat, lon);
      const geoJSON = windGridToGeoJSON(grid);
      setWindGeoJSON(geoJSON);
      lastWindCenter.current = { lat, lon };
    } catch {
      // Silently fail — keep previous data if any
    } finally {
      setWindLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!windEnabled) {
      setWindGeoJSON(null);
      lastWindCenter.current = null;
      if (windDebounceRef.current) clearTimeout(windDebounceRef.current);
      return;
    }
    const lat = userLocation?.lat ?? DEFAULT_CENTER[1];
    const lon = userLocation?.lon ?? DEFAULT_CENTER[0];
    loadWindData(lat, lon);
  }, [windEnabled, userLocation, loadWindData]);

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
    }, 700);

    return () => {
      if (radarAnimRef.current) { clearInterval(radarAnimRef.current); radarAnimRef.current = null; }
    };
  }, [radarPlaying, radarFrames]);

  // ── Storm cell data fetching ──────────────────────────────────────
  useEffect(() => {
    if (!userLocation) return;
    // Fetch storm cells for a wide area around the user
    const lat = userLocation.lat;
    const lon = userLocation.lon;
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
  }, [userLocation]);

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
      const [points, trails] = await Promise.all([
        fetchNearbyAccessPoints(lat, lon, 15_000),
        fetchNearbyTrails(lat, lon, 10_000),
      ]);
      setAccessPoints(points);
      setAccessTrailGeoJSON(trailsToGeoJSON(trails));
      lastAccessCenter.current = { lat, lon };
    } catch {
      // Silently fail
    } finally {
      setAccessLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!accessEnabled) {
      setAccessPoints([]);
      setAccessTrailGeoJSON(null);
      lastAccessCenter.current = null;
      return;
    }
    const center = userLocation ?? { lat: DEFAULT_CENTER[1], lon: DEFAULT_CENTER[0] };
    fetchAccessForCenter(center.lat, center.lon);
  }, [accessEnabled, userLocation, fetchAccessForCenter]);

  // Ref to debounce map state persistence
  const mapStateSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Frame-skip counter — only process heavy work every 3rd region-change event */
  const regionChangeCounter = useRef(0);

  const handleRegionChange = useCallback((feature: any) => {
    // Always update zoom/bearing (lightweight state, needed for UI)
    const zoom = feature?.properties?.zoomLevel;
    if (zoom !== undefined) setCurrentZoom(zoom);
    const bearing = feature?.properties?.heading ?? feature?.properties?.bearing;
    if (bearing !== undefined) setCompassHeading(bearing);

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

    // Debounced wind re-fetch on significant pan
    if (windEnabled && feature?.geometry?.coordinates) {
      const [wLon, wLat] = feature.geometry.coordinates;
      const prev = lastWindCenter.current;
      const moved = !prev || Math.abs(prev.lat - wLat) > 0.3 || Math.abs(prev.lon - wLon) > 0.3;
      if (moved) {
        if (windDebounceRef.current) clearTimeout(windDebounceRef.current);
        windDebounceRef.current = setTimeout(() => loadWindData(wLat, wLon), 2000);
      }
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
  }, [marinasEnabled, fetchMarinasForCenter, accessEnabled, fetchAccessForCenter, windEnabled, loadWindData, fetchDiscoveredSpots, currentZoom]);

  // Compass mode toggle: static (north-up) vs heading (map follows device bearing)
  const handleToggleCompassMode = useCallback(() => {
    setCompassMode((prev) => {
      const next = prev === 'static' ? 'heading' : 'static';
      if (next === 'static' && cameraRef.current) {
        cameraRef.current.setCamera({ heading: 0, animationDuration: 500 });
        setCompassHeading(0);
      }
      return next;
    });
  }, []);

  // In heading mode, use expo-location heading if available
  useEffect(() => {
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
  }, [compassMode]);

  // Handle tapping a search result — fly to the location and focus it
  const handleSearchSelectLocation = useCallback((locationId: string) => {
    const loc = allLocations.find((l) => l.id === locationId);
    if (!loc) return;
    setFocusedLocation(loc);
    setSelectedMarkerId(loc.id);
    cacheLocationDetail(loc);
    cameraRef.current?.setCamera({
      centerCoordinate: [loc.lon, loc.lat],
      zoomLevel: 13,
      animationDuration: 800,
    });
  }, [allLocations]);

  const visibleLocations = useMemo(() => {
    return search.trim()
      ? allLocations.filter(
          (l) =>
            l.name.toLowerCase().includes(search.toLowerCase()) ||
            l.subtitle.toLowerCase().includes(search.toLowerCase()),
        )
      : allLocations;
  }, [allLocations, search]);

  const filteredLocations = visibleLocations;

  const filteredWaypoints = search.trim()
    ? waypoints.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
    : waypoints;

  const handleMarkerPress = useCallback(
    (location: FishingLocation) => {
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
      fetchNearbyAccessPoints(location.lat, location.lon, 5_000).then((pts) => { // min 5km enforced in service
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
    [animateSheetTo, currentZoom],
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
        handleMarkerPress(location);
      }
    },
    [currentZoom, handleMarkerPress, allLocations],
  );

  const handleMapLongPress = useCallback((event: any) => {
    const coords = event.geometry?.coordinates;
    if (coords) {
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
  }, []);

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
        // For now, same as drop-pin — future route planning
        setPendingCoord({ latitude: coordinate.latitude, longitude: coordinate.longitude });
        setShowWaypointModal(true);
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
  }, [navigation, marinasEnabled, accessEnabled]);

  const handleToggleLayerPicker = useCallback(() => {
    setLayerPickerVisible((prev) => !prev);
  }, []);

  const handleToggleOverlay = useCallback((key: string) => {
    setActiveOverlays((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

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
        try {
          const waterLayerIds = ['water', 'water-polygon', 'waterway'];
          const screenPt = event.properties?.screenPointX != null
            ? [event.properties.screenPointX, event.properties.screenPointY]
            : [SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2];
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
              id: `water-tap-${Date.now()}`,
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
            setFocusedLocation(tmpLoc);
            cacheLocationDetail(tmpLoc);
            cameraRef.current?.setCamera({
              centerCoordinate: [tappedLng, tappedLat],
              zoomLevel: Math.max(currentZoom, 12),
              animationDuration: 500,
            });
            animateSheetTo(SHEET_HIDDEN);
            return;
          }
        } catch { /* queryRenderedFeatures unavailable */ }
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

    setSelectedMarkerId(null);
    setFocusedLocation(null);
    setSelectedMarina(null);
    // Clear highlighted access points (Feature 2)
    setHighlightedAccessPoints([]);
    setHighlightedAccessSummary(null);
    // Clear contextual tips (Feature 1)
    setContextualTips([]);
    setContextualTipsDismissed(false);
  }, [measureMode, annotationMode, annotationTool, annotationColor, annotationIcon, arrowStart, handleSaveAnnotation, currentZoom, animateSheetTo]);

  // ── Derived ─────────────────────────────────────────────────────
  const waypointIonicon = (wpIcon: WaypointIcon): string =>
    WAYPOINT_ICONS.find((i) => i.key === wpIcon)?.ionicon ?? 'location';

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

  // ── Map Tools drawer groups (progressive disclosure) ─────────────
  // Contextual: only show tools relevant to current location/season
  const currentMonth = new Date().getMonth() + 1; // 1-12
  const isWinterSeason = currentMonth >= 11 || currentMonth <= 3;
  const isNearCoastNow = userLocation ? isNearCoast(userLocation.lat, userLocation.lon) : false;

  const mapToolGroups: MapToolGroup[] = [
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
          infoCard: windEnabled && windGeoJSON?.features?.length > 0
            ? {
                primary: `${windGeoJSON.features.length} wind vectors loaded`,
                secondary: lastWindCenter.current
                  ? `Center: ${lastWindCenter.current.lat.toFixed(2)}, ${lastWindCenter.current.lon.toFixed(2)}`
                  : undefined,
                icon: 'flag',
                color: '#1565C0',
              } as OverlayInfoCard
            : null,
        },
        {
          key: 'wind-animated',
          label: 'Wind (Animated)',
          icon: 'cellular-outline',
          description: 'Windy-style animated wind grid (knots)',
          isActive: windAnimatedEnabled,
          onPress: () => setWindAnimatedEnabled((prev) => !prev),
        },
        {
          key: 'waves',
          label: 'Wave Height',
          icon: 'water-outline',
          description: 'Wave height and direction (coastal)',
          isActive: waveEnabled,
          visible: isNearCoastNow,
          onPress: () => setWaveEnabled((prev) => !prev),
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
      title: 'Fishing',
      tools: [
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
          onPress: () => setDepthNumbersEnabled((prev) => !prev),
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
          label: '3D Terrain',
          icon: 'cube-outline',
          description: 'Terrain exaggeration and hillshade',
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
      ],
    },
  ];

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
          mapStyle === 'night'
            ? NIGHT_STYLE
            : mapStyle === 'hybrid'
              ? HYBRID_STYLE
              : mapStyle === 'bathymetry'
                ? BATHYMETRY_STYLE
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
        {activeOverlays.has('no-wake-zones') && userLocation && ShapeSource && FillLayer && SymbolLayer && (
          <NoWakeZoneOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            FillLayer={FillLayer}
            SymbolLayer={SymbolLayer}
            LineLayer={LineLayer}
          />
        )}

        {/* Navigation aids overlay (buoys, lights) */}
        {activeOverlays.has('nav-aids') && userLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <NavigationAidsOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Artificial reef markers */}
        {activeOverlays.has('artificial-reefs') && userLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <ArtificialReefOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Animated wind overlay (enhanced Windy-style) */}
        {windAnimatedEnabled && userLocation && ShapeSource && SymbolLayer && CircleLayer && (
          <WindOverlayAnimated
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            SymbolLayer={SymbolLayer}
            CircleLayer={CircleLayer}
          />
        )}

        {/* Wave height overlay (coastal only) */}
        {waveEnabled && userLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <WaveOverlayAnimated
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Depth number overlay (nautical chart soundings) */}
        {depthNumbersEnabled && userLocation && ShapeSource && SymbolLayer && (
          <DepthNumberOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            zoom={currentZoom}
            units="imperial"
            ShapeSource={ShapeSource}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Marine gas station markers */}
        {marineGasEnabled && userLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <MarineGasOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* Ocean fishing spots (wrecks, reefs, ledges) */}
        {oceanSpotsEnabled && userLocation && ShapeSource && CircleLayer && SymbolLayer && (
          <OceanFishingSpotsOverlay
            lat={userLocation.lat}
            lon={userLocation.lon}
            ShapeSource={ShapeSource}
            CircleLayer={CircleLayer}
            SymbolLayer={SymbolLayer}
          />
        )}

        {/* 3D Terrain — hillshade + terrain exaggeration */}
        {terrain3DEnabled && RasterSource && RasterLayer && (
          <RasterSource
            id="terrain-hillshade-source"
            tileUrlTemplates={['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png']}
            tileSize={256}
          >
            <RasterLayer
              id="terrain-hillshade-layer"
              style={{ rasterOpacity: 0.35 }}
            />
          </RasterSource>
        )}

        {/* Overlay raster tile layers */}
        {Array.from(activeOverlays).map((key) => {
          const url = OVERLAY_TILE_URLS[key];
          if (!url) return null;
          const opacity =
            key === 'nautical' ? 0.7
            : key === 'shaded-relief' ? 0.5
            : key === 'water-flow' ? 0.7
            : key === 'depth-contours' ? 0.8
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

        {/* Real vector-tile overlays from Martin/PostGIS */}
        {ENABLE_EXPERIMENTAL_VECTOR_OVERLAYS &&
          isVectorOverlayReady('local-bathymetry') &&
          activeOverlays.has('local-bathymetry') &&
          VectorSource &&
          LineLayer && (
          <VectorSource
            id="local-bathymetry-source"
            url={buildTileSourceUrl(TILE_LAYER_NAMES.bathymetryContours)!}
            maxZoomLevel={14}
          >
            <LineLayer
              id="local-bathymetry-lines"
              sourceLayerID={TILE_LAYER_NAMES.bathymetryContours}
              style={{
                lineColor: buildContourColorExpression(contourSettings) as any,
                lineWidth: buildContourWidthExpression(contourSettings) as any,
                lineOpacity: contourSettings.opacity,
              }}
            />
          </VectorSource>
        )}

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
                  filter={['==', ['get', 'access_type'], 'trail'] as any}
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
                  filter={['==', ['get', 'access_type'], 'parking'] as any}
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
                  filter={['match', ['get', 'access_type'], ['boat_launch', 'shore_access', 'campground'], true, false] as any}
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

        {/* Fishing location markers — clustered for smooth zoomed-out rendering */}
        {markerMode === 'locations' && ShapeSource && CircleLayer && SymbolLayer && (
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
        {markerMode === 'locations' && selectedPinGeoJSON && ShapeSource && CircleLayer && (
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

        {/* Wind arrow overlay */}
        {windEnabled && windGeoJSON && ShapeSource && SymbolLayer && (
          <ShapeSource id="wind-arrow-source" shape={windGeoJSON}>
            <SymbolLayer
              id="wind-arrow-layer"
              style={{
                iconImage: 'triangle-11',
                iconSize: 1.2,
                iconRotate: ['get', 'iconRotation'],
                iconAllowOverlap: true,
                iconIgnorePlacement: true,
                iconColor: [
                  'interpolate',
                  ['linear'],
                  ['get', 'speedMph'],
                  0, '#4CAF50',
                  12, '#FFC107',
                  24, '#FF9800',
                  38, '#F44336',
                ],
                iconOpacity: 0.85,
              }}
            />
          </ShapeSource>
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
              style={{ rasterOpacity: 0.55 }}
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
          style={styles.dismissTrackBanner}
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
        <View style={styles.mobBanner}>
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
      <View style={styles.searchOverlay}>
        <EnhancedSearchBar
          value={search}
          onChangeText={setSearch}
          onClear={() => setSearch('')}
          onSelectLocation={handleSearchSelectLocation}
          nearbySpots={locationsWithDistance.slice(0, 10).map((item) => ({
            id: item.location.id,
            name: item.location.name,
            subtitle: item.location.subtitle,
            distanceMi: item.distanceMi ?? undefined,
            type: item.location.subtitle,
          }))}
          allLocations={allLocations.map((loc) => ({
            id: loc.id,
            name: loc.name,
            subtitle: loc.subtitle,
            distanceMi: userLocation
              ? haversineDistance(userLocation.lat, userLocation.lon, loc.lat, loc.lon)
              : undefined,
            type: loc.subtitle,
          }))}
        />
        {/* Filter chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterChipsRow}>
          {[
            { key: 'shore', label: 'Shore Fishing', icon: 'walk-outline' },
            { key: 'boat-launch', label: 'Boat Launch', icon: 'boat-outline' },
            { key: 'kayak', label: 'Kayak', icon: 'water-outline' },
            { key: 'free-parking', label: 'Free Parking', icon: 'car-outline' },
            { key: 'public', label: 'Public Access', icon: 'globe-outline' },
          ].map((f) => {
            const isActive = activeFilters.has(f.key);
            return (
              <Pressable
                key={f.key}
                style={[styles.filterChip, isActive && styles.filterChipActive]}
                onPress={() => setActiveFilters((prev) => {
                  const next = new Set(prev);
                  if (next.has(f.key)) next.delete(f.key);
                  else next.add(f.key);
                  return next;
                })}
              >
                <Ionicons
                  name={f.icon as any}
                  size={12}
                  color={isActive ? '#FFFFFF' : palette.textSecondary}
                />
                <Text style={[styles.filterChipText, isActive && styles.filterChipTextActive]}>
                  {f.label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {/* Weather alert banner */}
      {topAlert && !alertBannerDismissed && (
        <View style={[styles.alertBanner, { backgroundColor: bannerColor }]}>
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

      {/* Segmented control: Locations / My Spots */}
      <View style={styles.segmentedOverlay}>
        <View style={styles.segmentedControl}>
          <Pressable
            style={[
              styles.segmentButton,
              markerMode === 'locations' && styles.segmentButtonActive,
            ]}
            onPress={() => setMarkerMode('locations')}
          >
            <Ionicons
              name="location-outline"
              size={14}
              color={markerMode === 'locations' ? '#FFFFFF' : palette.textMuted}
              style={{ marginRight: 4 }}
            />
            <Text
              style={[
                styles.segmentText,
                markerMode === 'locations' && styles.segmentTextActive,
              ]}
            >
              Locations
            </Text>
          </Pressable>
          <Pressable
            style={[
              styles.segmentButton,
              markerMode === 'waypoints' && styles.segmentButtonActive,
            ]}
            onPress={() => setMarkerMode('waypoints')}
          >
            <Ionicons
              name="bookmark-outline"
              size={14}
              color={markerMode === 'waypoints' ? '#FFFFFF' : palette.textMuted}
              style={{ marginRight: 4 }}
            />
            <Text
              style={[
                styles.segmentText,
                markerMode === 'waypoints' && styles.segmentTextActive,
              ]}
            >
              My Spots
            </Text>
          </Pressable>
        </View>
      </View>

      {/* Map layer toggle button */}
      <Pressable
        style={styles.layerButton}
        onPress={handleToggleLayerPicker}
        accessibilityLabel={`Map style: ${MAP_STYLE_LABELS[mapStyle]}. ${activeOverlays.size} overlays active. Tap to change.`}
      >
        <Ionicons name="layers-outline" size={20} color={palette.textSecondary} />
        {activeOverlays.size > 0 && (
          <View style={styles.overlayBadge}>
            <Text style={styles.overlayBadgeText}>{activeOverlays.size}</Text>
          </View>
        )}
      </Pressable>

      {/* Map Tools button — opens slide-out drawer (replaces 7 individual buttons) */}
      {/* Design: "Kitchen Sink" avoidance per Gaigg Ch.7 */}
      <Pressable
        style={[styles.mapToolsButton, (measureMode || annotationMode || windEnabled || radarEnabled || marinasEnabled || accessEnabled || windAnimatedEnabled || waveEnabled || depthNumbersEnabled || marineGasEnabled || oceanSpotsEnabled || terrain3DEnabled) && styles.mapToolsButtonActive]}
        onPress={() => setMapToolsDrawerOpen(true)}
        accessibilityLabel="Open map tools drawer"
      >
        <Ionicons
          name="build-outline"
          size={20}
          color={(measureMode || annotationMode || windEnabled || radarEnabled || marinasEnabled || accessEnabled || windAnimatedEnabled || waveEnabled || depthNumbersEnabled || marineGasEnabled || oceanSpotsEnabled || terrain3DEnabled) ? '#FFFFFF' : palette.textSecondary}
        />
        {(measureMode || annotationMode || windEnabled || radarEnabled || marinasEnabled || accessEnabled || windAnimatedEnabled || waveEnabled || depthNumbersEnabled || marineGasEnabled || oceanSpotsEnabled || terrain3DEnabled) && (
          <View style={styles.mapToolsBadge}>
            <Text style={styles.mapToolsBadgeText}>
              {[measureMode, annotationMode, windEnabled, radarEnabled, marinasEnabled, accessEnabled, windAnimatedEnabled, waveEnabled, depthNumbersEnabled, marineGasEnabled, oceanSpotsEnabled, terrain3DEnabled].filter(Boolean).length}
            </Text>
          </View>
        )}
      </Pressable>

      {/* Compass heading widget — only visible when map is rotated */}
      <CompassWidget heading={compassHeading} mode={compassMode} onToggleMode={handleToggleCompassMode} />

      {/* Map Tools Drawer (slide-out panel for secondary tools) */}
      <MapToolsDrawer
        visible={mapToolsDrawerOpen}
        onClose={() => setMapToolsDrawerOpen(false)}
        toolGroups={mapToolGroups}
      />

      {/* Catch photos toggle available via layer picker */}

      {/* Speed/Course HUD — shows when moving at boating speed */}
      <SpeedCourseHUD />

      {/* Fishing time banner — shows when conditions are good */}
      {userLocation && (
        <FishingTimeBanner lat={userLocation.lat} lon={userLocation.lon} />
      )}

      {/* Access points legend banner */}
      {accessEnabled && (
        <View style={styles.accessBanner}>
          <Ionicons name="trail-sign-outline" size={14} color="#16A34A" style={{ marginRight: 6 }} />
          <Text style={styles.windBannerText}>Access</Text>
          <View style={styles.windLegend}>
            <View style={[styles.windLegendDot, { backgroundColor: '#EA580C' }]} />
            <Text style={styles.windLegendLabel}>Launch</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#2563EB' }]} />
            <Text style={styles.windLegendLabel}>Parking</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#16A34A' }]} />
            <Text style={styles.windLegendLabel}>Trail</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#0D9488' }]} />
            <Text style={styles.windLegendLabel}>Shore</Text>
          </View>
          {accessLoading && <ActivityIndicator size="small" color="#16A34A" style={{ marginLeft: 8 }} />}
        </View>
      )}

      {/* Wind speed legend banner */}
      {windEnabled && (
        <View style={styles.windBanner}>
          <Ionicons name="flag-outline" size={14} color={palette.accent} style={{ marginRight: 6 }} />
          <Text style={styles.windBannerText}>Wind</Text>
          <View style={styles.windLegend}>
            <View style={[styles.windLegendDot, { backgroundColor: '#4CAF50' }]} />
            <Text style={styles.windLegendLabel}>Calm</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#FFC107' }]} />
            <Text style={styles.windLegendLabel}>Moderate</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#FF9800' }]} />
            <Text style={styles.windLegendLabel}>Strong</Text>
            <View style={[styles.windLegendDot, { backgroundColor: '#F44336' }]} />
            <Text style={styles.windLegendLabel}>Storm</Text>
          </View>
          {windLoading && <ActivityIndicator size="small" color={palette.accent} style={{ marginLeft: 8 }} />}
        </View>
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

      {/* Storm cell warnings */}
      {stormCells.length > 0 && (
        <View style={styles.stormBanner}>
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
        onSelectStyle={setMapStyle}
        onToggleOverlay={handleToggleOverlay}
        onToggleQualityPins={() => setShowQualityPins((p) => !p)}
        onClose={() => setLayerPickerVisible(false)}
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
              <Text style={styles.focusedName}>{focusedLocation.name || 'Unseen Site'}</Text>
              <Text style={styles.focusedSubtitle}>{focusedLocation.subtitle || 'Water Body'}</Text>
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
            <View style={{ flex: 1 }} />
          </View>
          {/* Main action buttons row */}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              style={[styles.focusedDetailsBtn, { backgroundColor: '#3B82C4', flex: 1 }]}
              onPress={() => {
                const url = Platform.select({
                  ios: `maps:?daddr=${focusedLocation.lat},${focusedLocation.lon}`,
                  android: `google.navigation:q=${focusedLocation.lat},${focusedLocation.lon}`,
                  default: `https://www.google.com/maps/dir/?api=1&destination=${focusedLocation.lat},${focusedLocation.lon}`,
                });
                if (url) Linking.openURL(url).catch(() => {});
              }}
            >
              <Ionicons name="navigate-outline" size={16} color="#FFFFFF" />
              <Text style={styles.focusedDetailsBtnText}>Directions</Text>
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

      {/* Quick Action FAB */}
      {!focusedLocation && (
        <QuickActionFAB
          actions={[
            {
              key: 'catch',
              label: 'Log Catch',
              icon: 'camera',
              color: '#E53935',
              onPress: () => navigation.navigate('CatchReport', {
                lat: userLocation?.lat,
                lon: userLocation?.lon,
              }),
            },
            {
              key: 'track',
              label: 'Record Trip',
              icon: 'play',
              color: '#E65100',
              onPress: () => navigation.navigate('TrackRecording'),
            },
            {
              key: 'spot',
              label: 'Mark Spot',
              icon: 'pin',
              color: '#2E7D32',
              onPress: () => {
                if (userLocation) {
                  setPendingCoord({ latitude: userLocation.lat, longitude: userLocation.lon });
                  setShowWaypointModal(true);
                }
              },
            },
            {
              key: 'conditions',
              label: 'Check Conditions',
              icon: 'water',
              color: '#1565C0',
              onPress: () => navigation.navigate('WaterInsights', {
                lat: userLocation?.lat,
                lon: userLocation?.lon,
              }),
            },
          ]}
        />
      )}

      {/* First-time user coach marks (Gaigg Ch.1 — Onboarding pattern) */}
      <CoachMarks mapReady={!loading && userLocation !== null} />

      {/* Long-press contextual menu */}
      <MapLongPressMenu
        visible={longPressMenuVisible}
        coordinate={longPressCoord}
        onAction={handleLongPressAction}
        onClose={() => setLongPressMenuVisible(false)}
      />

      {/* Persistent map info bar — speed, heading, GPS accuracy */}
      {!focusedLocation && (
        <MapInfoBar
          bottomOffset={Platform.OS === 'ios' ? 100 : 72}
          anchorWatch={anchorStatus.active ? { active: true, driftMeters: anchorStatus.driftDistance, radiusMeters: anchorStatus.watch?.radiusMeters ?? 30 } as AnchorWatchInfo : undefined}
        />
      )}

      {/* Bottom sheet with location list (merged from Explore) */}
      {markerMode === 'locations' && (
        <Animated.View style={[styles.sheet, { height: sheetHeight }]}>
          <View style={styles.handleArea} {...panResponder.panHandlers}>
            <View style={styles.dragHandle} />
            <Text style={styles.handleHint}>—</Text>
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
          {userLocation && !search.trim() && (
            <View style={styles.sheetInsightsWrapper}>
              <SpotInsightsCard
                lat={userLocation.lat}
                lon={userLocation.lon}
                locationName="Your Area"
              />
            </View>
          )}

          {/* Count */}
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>
              {search.trim() ? `Results` : userLocation ? `Nearby` : `Spots`}
            </Text>
            <Text style={styles.sheetCount}>
              {displayList.length} spots{discoveredSpots.length > 0 ? ` (${discoveredSpots.length} discovered)` : ''}{discoveryLoading ? ' ...' : ''}{!search.trim() && !userLocation ? ' (enable GPS for nearby)' : ''}
            </Text>
          </View>

          {/* Site card list */}
          <FlatList
            data={displayList}
            renderItem={renderSiteCard}
            keyExtractor={keyExtractor}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
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
    gap: 8,
  },
  filterChipsRow: {
    gap: 6,
    paddingRight: 56,
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
    position: 'absolute',
    top: Platform.OS === 'ios' ? 150 : 110,
    alignSelf: 'center',
    zIndex: 1,
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
    top: Platform.OS === 'ios' ? 155 : 115,
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
    top: Platform.OS === 'ios' ? 170 : 130,
    right: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    paddingVertical: 8,
    minWidth: 220,
    maxHeight: '70%',
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 20,
    zIndex: 100,
  },
  layerSectionTitle: {
    fontSize: 10,
    fontWeight: '700',
    color: palette.textMuted,
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: 4,
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
    fontSize: 11,
    color: palette.textMuted,
    marginTop: 1,
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
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  layerPickerRowActive: {
    backgroundColor: palette.accentDim,
  },
  layerPickerLabel: {
    fontSize: 14,
    fontWeight: '500',
    color: palette.textSecondary,
    flex: 1,
  },
  layerPickerLabelActive: {
    color: palette.accent,
    fontWeight: '600',
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
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: -6 },
    elevation: 10,
  },
  handleArea: {
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    paddingTop: 10,
    cursor: 'grab' as any,
  },
  dragHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.border,
  },
  handleHint: {
    color: 'transparent',
    fontSize: 0,
    height: 0,
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
