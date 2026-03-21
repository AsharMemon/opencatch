import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Svg, { Circle, Line, Text as SvgText, G, Polygon } from 'react-native-svg';
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
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
// MapLibre native modules only work on iOS/Android
let MLMapView: any = null;
let Camera: any = null;
let PointAnnotation: any = null;
let Callout: any = null;
let UserLocation: any = null;
let RasterSource: any = null;
let RasterLayer: any = null;
let ShapeSource: any = null;
let LineLayer: any = null;
let SymbolLayer: any = null;
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
  ShapeSource = maplibre.ShapeSource;
  LineLayer = maplibre.LineLayer;
  SymbolLayer = maplibre.SymbolLayer;
  setAccessToken = maplibre.setAccessToken;
  setAccessToken(null);
} catch (_) {
  // MapLibre not available on web — will show fallback UI
}

import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { palette, getConditionBand, conditionConfig, scoreColor } from '../theme/palette';
import { SearchBar } from '../components/SearchBar';
import { api, TILE_SERVER_URL } from '../services/api';
import { fetchNearbyMarinas, formatAmenities } from '../services/marinaDirectory';
import type { MarinaPOI, MarinaPOIType } from '../services/marinaDirectory';
import { fetchWindGrid, windGridToGeoJSON } from '../services/windOverlay';
import {
  getFishingAlerts,
  type FishingWeatherAlert,
  type AlertSeverity,
} from '../services/weatherAlerts';
import type { FishingLocation, Waypoint, WaypointIcon, BestFishingV2Entry } from '../types/models';
import type { TabProps } from '../types/navigation';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

// ── Constants ─────────────────────────────────────────────────────

const DEFAULT_CENTER: [number, number] = [-95.0, 45.0]; // [lng, lat] — centered to show US + Canada
const DEFAULT_ZOOM = 3.5;

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

/** Auto-refresh interval for weather alerts (10 minutes). */
const ALERT_REFRESH_MS = 10 * 60 * 1000;

// ── Map style identifiers ────────────────────────────────────────

type MapStyleKey = 'hybrid' | 'bathymetry' | 'satellite' | 'outdoors' | 'topo' | 'night';
const MAP_STYLE_LABELS: Record<MapStyleKey, string> = {
  hybrid: 'Hybrid',
  bathymetry: 'Bathymetry',
  satellite: 'Satellite',
  outdoors: 'Outdoors',
  topo: 'Topographic',
  night: 'Night',
};
const MAP_STYLE_IONICONS: Record<MapStyleKey, string> = {
  hybrid: 'earth-outline',
  bathymetry: 'water-outline',
  satellite: 'planet-outline',
  outdoors: 'compass-outline',
  topo: 'analytics-outline',
  night: 'moon-outline',
};
const MAP_STYLE_KEYS: MapStyleKey[] = ['bathymetry', 'satellite', 'outdoors', 'topo', 'night'];

// ── Overlay layer definitions ──────────────────────────────────────

interface OverlayLayer {
  key: string;
  label: string;
  ionicon: string;
  description: string;
}

const OVERLAY_LAYERS: OverlayLayer[] = [
  { key: 'nautical', label: 'Nautical Marks', ionicon: 'boat-outline', description: 'OpenSeaMap buoys, channels, marks' },
  { key: 'shaded-relief', label: 'Shaded Relief', ionicon: 'layers-outline', description: '3D terrain and elevation' },
  { key: 'water-flow', label: 'Hydrology', ionicon: 'water-outline', description: 'USGS streams & water features' },
  { key: 'depth-contours', label: 'Depth Contours', ionicon: 'resize-outline', description: 'GEBCO bathymetry lines' },
];

const OVERLAY_TILE_URLS: Record<string, string> = {
  'nautical': 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
  'shaded-relief': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}',
  'water-flow': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}',
  'depth-contours': 'https://tiles.arcgis.com/tiles/C8EMgrsFcRFL6LrL/arcgis/rest/services/GEBCO_contours/MapServer/tile/{z}/{y}/{x}',
};

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
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
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

    // ── Bathymetry contours reserved for future Martin/local data ──

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
        'text-font': ['Noto Sans Italic'],
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
        'text-font': ['Noto Sans Bold'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Bold'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Regular'],
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

// ── Night Mode Map Style ──────────────────────────────────────────
// Dark-adapted colors for nighttime on-the-water use.
// Inspired by Navionics night mode — low glare, preserves scotopic vision.

const NIGHT_STYLE: object = {
  version: 8,
  name: 'OpenCatch Night',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
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
        'text-font': ['Noto Sans Italic'],
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
        'text-font': ['Noto Sans Bold'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Bold'],
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
        'text-font': ['Noto Sans Regular'],
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
        'text-font': ['Noto Sans Regular'],
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

// ── Layer Picker ──────────────────────────────────────────────────

interface LayerPickerProps {
  visible: boolean;
  currentStyle: MapStyleKey;
  activeOverlays: Set<string>;
  showQualityPins: boolean;
  onSelectStyle: (style: MapStyleKey) => void;
  onToggleOverlay: (key: string) => void;
  onToggleQualityPins: () => void;
  onClose: () => void;
}

function LayerPicker({ visible, currentStyle, activeOverlays, showQualityPins, onSelectStyle, onToggleOverlay, onToggleQualityPins, onClose }: LayerPickerProps) {
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
          {MAP_STYLE_KEYS.map((key) => {
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
          {OVERLAY_LAYERS.map((layer) => {
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
            {location.name}
          </Text>
          <Text style={styles.cardSubtitle} numberOfLines={1}>
            {location.subtitle}
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
const cwStyles = StyleSheet.create({ container: { position: 'absolute', top: Platform.OS === 'ios' ? 220 : 180, right: 16, width: COMPASS_SIZE + 8, alignItems: 'center', backgroundColor: palette.surface, borderRadius: (COMPASS_SIZE + 8) / 2, paddingVertical: 4, paddingHorizontal: 4, shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 }, containerActive: { borderWidth: 1.5, borderColor: palette.accent }, readout: { flexDirection: 'row', alignItems: 'baseline', gap: 2, marginTop: 1, marginBottom: 2 }, degrees: { fontSize: 10, fontWeight: '700', color: palette.text }, cardinal: { fontSize: 8, fontWeight: '600', color: palette.textSecondary }, trackingDot: { position: 'absolute', top: 4, right: 4, width: 6, height: 6, borderRadius: 3, backgroundColor: palette.accent } });

// ── MapScreen ─────────────────────────────────────────────────────

type MarkerMode = 'locations' | 'waypoints';

export function MapScreen({ navigation }: TabProps<'MapTab'>) {
  const cameraRef = useRef<CameraRef>(null);
  const mapRef = useRef<MapViewRef>(null);

  const [locations, setLocations] = useState<FishingLocation[]>([]);
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);
  const [bestFishing, setBestFishing] = useState<BestFishingV2Entry[]>([]);
  const [search, setSearch] = useState('');
  const [activeFilters, setActiveFilters] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [userLocation, setUserLocation] = useState<{ lat: number; lon: number } | null>(null);

  // Map style selection — default to hybrid (satellite land + bathymetry water)
  const [mapStyle, setMapStyle] = useState<MapStyleKey>('hybrid');
  const [layerPickerVisible, setLayerPickerVisible] = useState(false);

  // Which set of markers to show
  const [markerMode, setMarkerMode] = useState<MarkerMode>('locations');

  // Overlay layers
  const [activeOverlays, setActiveOverlays] = useState<Set<string>>(new Set());
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

  // Selected marker for callout
  const [selectedMarkerId, setSelectedMarkerId] = useState<string | null>(null);

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
  const lastWindCenter = useRef<{ lat: number; lon: number } | null>(null);

  // Bottom sheet
  const sheetHeight = useRef(new Animated.Value(SHEET_COLLAPSED)).current;
  const currentHeight = useRef(SHEET_COLLAPSED);
  const dragStartHeight = useRef(SHEET_COLLAPSED);

  useEffect(() => {
    const id = sheetHeight.addListener(({ value }) => {
      currentHeight.current = value;
    });
    return () => sheetHeight.removeListener(id);
  }, [sheetHeight]);

  const animateSheetTo = useCallback((target: number) => {
    Animated.spring(sheetHeight, {
      toValue: target,
      damping: 22,
      stiffness: 260,
      mass: 0.8,
      useNativeDriver: false,
      overshootClamping: false,
      restDisplacementThreshold: 0.5,
      restSpeedThreshold: 0.5,
    }).start();
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

  // ── Data loading ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    async function loadData() {
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
          setLoading(false);
        }
      } catch {
        if (!cancelled) setLoading(false);
      }
    }

    loadData();

    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status === 'granted') {
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (!cancelled) setUserLocation({ lat: loc.coords.latitude, lon: loc.coords.longitude });
      }
    })();

    return () => {
      cancelled = true;
      if (marinaFetchTimer.current) clearTimeout(marinaFetchTimer.current);
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

  // ── Computed lists ──────────────────────────────────────────────

  const locationsWithDistance = useMemo(() => {
    return locations.map((loc) => ({
      location: loc,
      distanceMi: userLocation
        ? haversineDistance(userLocation.lat, userLocation.lon, loc.lat, loc.lon)
        : null,
    }));
  }, [locations, userLocation]);

  const displayList = useMemo(() => {
    let list = [...locationsWithDistance];
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (item) =>
          item.location.name.toLowerCase().includes(q) ||
          item.location.subtitle.toLowerCase().includes(q),
      );
    }
    return list.sort((a, b) => {
      const scoreA =
        a.location.score * 0.6 +
        (a.distanceMi !== null ? Math.max(0, 100 - a.distanceMi * 0.5) : 50) * 0.4;
      const scoreB =
        b.location.score * 0.6 +
        (b.distanceMi !== null ? Math.max(0, 100 - b.distanceMi * 0.5) : 50) * 0.4;
      return scoreB - scoreA;
    });
  }, [locationsWithDistance, search]);

  // ── GeoJSON sources for MapLibre markers ────────────────────────

  const locationGeoJSON = useMemo((): GeoJSON.FeatureCollection => ({
    type: 'FeatureCollection',
    features: (search.trim()
      ? locations.filter(
          (l) =>
            l.name.toLowerCase().includes(search.toLowerCase()) ||
            l.subtitle.toLowerCase().includes(search.toLowerCase()),
        )
      : locations
    ).map((loc) => ({
      type: 'Feature',
      id: loc.id,
      geometry: {
        type: 'Point',
        coordinates: [loc.lon, loc.lat], // MapLibre uses [lng, lat]
      },
      properties: {
        id: loc.id,
        name: loc.name,
        score: loc.score,
        band: getConditionBand(loc.score),
        color: conditionConfig[getConditionBand(loc.score)].color,
      },
    })),
  }), [locations, search]);

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

  const handleRegionChange = useCallback((feature: any) => {
    const zoom = feature?.properties?.zoomLevel;
    if (zoom !== undefined) setCurrentZoom(zoom);
    const bearing = feature?.properties?.heading ?? feature?.properties?.bearing;
    if (bearing !== undefined) setCompassHeading(bearing);

    // Debounced marina re-fetch on pan
    if (marinasEnabled && feature?.geometry?.coordinates) {
      const [lng, lat] = feature.geometry.coordinates;
      if (marinaFetchTimer.current) clearTimeout(marinaFetchTimer.current);
      marinaFetchTimer.current = setTimeout(() => {
        fetchMarinasForCenter(lat, lng);
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
  }, [marinasEnabled, fetchMarinasForCenter, windEnabled, loadWindData]);

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

  // Show more pins as user zooms in — all pins visible at zoom >= 8
  const visibleLocations = useMemo(() => {
    const searchFiltered = search.trim()
      ? locations.filter(
          (l) =>
            l.name.toLowerCase().includes(search.toLowerCase()) ||
            l.subtitle.toLowerCase().includes(search.toLowerCase()),
        )
      : locations;

    // At low zoom, show only top-scoring spots
    if (currentZoom < 4) {
      return searchFiltered.filter((l) => l.score >= 80).slice(0, 20);
    } else if (currentZoom < 5) {
      return searchFiltered.filter((l) => l.score >= 70).slice(0, 50);
    } else if (currentZoom < 6) {
      return searchFiltered.filter((l) => l.score >= 60).slice(0, 100);
    } else if (currentZoom < 8) {
      return searchFiltered.slice(0, 200);
    }
    return searchFiltered; // Show all at high zoom
  }, [locations, search, currentZoom]);

  const filteredLocations = visibleLocations;

  const filteredWaypoints = search.trim()
    ? waypoints.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
    : waypoints;

  const handleMarkerPress = useCallback(
    (location: FishingLocation) => {
      setSelectedMarkerId(location.id);
      cameraRef.current?.setCamera({
        centerCoordinate: [location.lon, location.lat],
        zoomLevel: 10,
        animationDuration: 500,
      });
      animateSheetTo(SHEET_HIDDEN);
    },
    [animateSheetTo],
  );

  const handleCalloutPress = useCallback(
    (location: FishingLocation) => {
      setSelectedMarkerId(null);
      navigation.navigate('LocationDetail', { locationId: location.id });
    },
    [navigation],
  );

  const [focusedLocation, setFocusedLocation] = useState<FishingLocation | null>(null);

  const handleCardPress = useCallback(
    (loc: FishingLocation) => {
      // Zoom to the location, highlight the pin, collapse the sheet
      cameraRef.current?.setCamera({
        centerCoordinate: [loc.lon, loc.lat],
        zoomLevel: 12,
        animationDuration: 800,
      });
      setFocusedLocation(loc);
      setSelectedMarkerId(loc.id);
      animateSheetTo(SHEET_HIDDEN);
    },
    [animateSheetTo],
  );

  const handleMapLongPress = useCallback((event: any) => {
    const coords = event.geometry?.coordinates;
    if (coords) {
      // MapLibre gives [lng, lat]; we store as {latitude, longitude}
      setPendingCoord({ latitude: coords[1], longitude: coords[0] });
      setShowWaypointModal(true);
    }
  }, []);

  const handleToggleLayerPicker = useCallback(() => {
    setLayerPickerVisible((prev) => !prev);
  }, []);

  // Only one overlay at a time
  const handleToggleOverlay = useCallback((key: string) => {
    setActiveOverlays((prev) => {
      if (prev.has(key)) {
        return new Set(); // toggle off
      }
      return new Set([key]); // replace with just this one
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

  const handleMapPress = useCallback((event?: any) => {
    if (measureMode && event?.geometry?.coordinates) {
      const [lng, lat] = event.geometry.coordinates as [number, number];
      setMeasurePoints((prev) => [...prev, [lat, lng]]);
      return;
    }
    setSelectedMarkerId(null);
    setSelectedMarina(null);
  }, [measureMode]);

  // ── Derived ─────────────────────────────────────────────────────
  const waypointIonicon = (wpIcon: WaypointIcon): string =>
    WAYPOINT_ICONS.find((i) => i.key === wpIcon)?.ionicon ?? 'location';

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
        mapStyle={mapStyle === 'night' ? NIGHT_STYLE : (mapStyle === 'bathymetry' || mapStyle === 'hybrid') ? BATHYMETRY_STYLE : ALT_STYLES[mapStyle]}
        logoEnabled={false}
        attributionEnabled={false}
        compassEnabled
        compassViewMargins={{ x: 16, y: Platform.OS === 'ios' ? 170 : 130 }}
        onPress={handleMapPress}
        onLongPress={handleMapLongPress}
        onRegionDidChange={handleRegionChange}
      >
        <Camera
          ref={cameraRef}
          defaultSettings={{
            centerCoordinate: DEFAULT_CENTER,
            zoomLevel: DEFAULT_ZOOM,
          }}
        />

        {/* User location */}
        <UserLocation visible={!!userLocation} />

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

        {/* Fishing location markers — using PointAnnotation for custom views */}
        {markerMode === 'locations' &&
          filteredLocations.map((loc) => {
            const band = getConditionBand(loc.score);
            const cfg = conditionConfig[band];
            return (
              <PointAnnotation
                key={loc.id}
                id={`loc-${loc.id}`}
                coordinate={[loc.lon, loc.lat]}
                onSelected={() => handleMarkerPress(loc)}
              >
                <ConditionPin score={loc.score} showQuality={showQualityPins} />
                <Callout title="">
                  <Pressable onPress={() => handleCalloutPress(loc)}>
                    <View style={styles.calloutBubble}>
                      <Text style={styles.calloutTitle} numberOfLines={1}>{loc.name}</Text>
                      <View style={styles.calloutScoreRow}>
                        <View style={[styles.calloutDot, { backgroundColor: cfg.color }]} />
                        <Text style={[styles.calloutScore, { color: cfg.color }]}>
                          {loc.score}/100 {cfg.label}
                        </Text>
                      </View>
                      <Text style={styles.calloutHint}>Tap for details</Text>
                    </View>
                  </Pressable>
                </Callout>
              </PointAnnotation>
            );
          })}

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
      </MLMapView>

      {/* Search bar overlay */}
      <View style={styles.searchOverlay}>
        <SearchBar
          value={search}
          onChangeText={setSearch}
          onClear={() => setSearch('')}
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

      {/* Ruler / measure toggle button */}
      <Pressable
        style={[styles.rulerButton, measureMode && styles.rulerButtonActive]}
        onPress={() => {
          setMeasureMode((prev) => {
            if (prev) setMeasurePoints([]);
            return !prev;
          });
        }}
        accessibilityLabel={measureMode ? 'Exit measure mode' : 'Measure distance'}
      >
        <Ionicons
          name="resize-outline"
          size={20}
          color={measureMode ? '#FFFFFF' : palette.textSecondary}
        />
      </Pressable>

      {/* Compass heading widget */}
      <CompassWidget heading={compassHeading} mode={compassMode} onToggleMode={handleToggleCompassMode} />

      {/* Marina POI toggle button */}
      <Pressable
        style={[styles.marinaButton, marinasEnabled && styles.marinaButtonActive]}
        onPress={() => setMarinasEnabled((prev) => !prev)}
        accessibilityLabel={marinasEnabled ? 'Hide marinas and POIs' : 'Show marinas and POIs'}
      >
        <Ionicons
          name="boat-outline"
          size={20}
          color={marinasEnabled ? '#FFFFFF' : palette.textSecondary}
        />
        {marinasLoading && (
          <ActivityIndicator
            size="small"
            color={marinasEnabled ? '#FFFFFF' : palette.accent}
            style={styles.marinaSpinner}
          />
        )}
      </Pressable>

      {/* Wind overlay toggle button */}
      <Pressable
        style={[styles.windButton, windEnabled && styles.windButtonActive]}
        onPress={() => setWindEnabled((prev) => !prev)}
        accessibilityLabel={windEnabled ? 'Hide wind overlay' : 'Show wind overlay'}
      >
        <Ionicons
          name="flag-outline"
          size={20}
          color={windEnabled ? '#FFFFFF' : palette.textSecondary}
        />
        {windLoading && (
          <ActivityIndicator
            size="small"
            color={windEnabled ? '#FFFFFF' : palette.accent}
            style={styles.windSpinner}
          />
        )}
      </Pressable>

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

      {/* Layer picker popover */}
      <LayerPicker
        visible={layerPickerVisible}
        currentStyle={mapStyle}
        activeOverlays={activeOverlays}
        showQualityPins={showQualityPins}
        onSelectStyle={setMapStyle}
        onToggleOverlay={handleToggleOverlay}
        onToggleQualityPins={() => setShowQualityPins((p) => !p)}
        onClose={() => setLayerPickerVisible(false)}
      />

      {/* Contextual topo fishing hints — only when zoomed in */}
      {currentZoom >= 10 && !focusedLocation && (
        <TopoHintBubble zoom={currentZoom} />
      )}

      {/* Focused location info card */}
      {focusedLocation && (
        <View style={styles.focusedCard}>
          <Pressable style={styles.focusedCardDismiss} onPress={() => setFocusedLocation(null)}>
            <Ionicons name="close" size={18} color={palette.textMuted} />
          </Pressable>
          <View style={styles.focusedCardContent}>
            <View style={styles.focusedScoreBadge}>
              <Text style={[styles.focusedScoreText, { color: scoreColor(focusedLocation.score) }]}>
                {focusedLocation.score}
              </Text>
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={styles.focusedName}>{focusedLocation.name}</Text>
              <Text style={styles.focusedSubtitle}>{focusedLocation.subtitle}</Text>
              <View style={styles.focusedTags}>
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
              </View>
            </View>
          </View>
          <Pressable
            style={styles.focusedDetailsBtn}
            onPress={() => {
              setFocusedLocation(null);
              navigation.navigate('LocationDetail', { locationId: focusedLocation.id });
            }}
          >
            <Ionicons name="information-circle-outline" size={16} color="#FFFFFF" />
            <Text style={styles.focusedDetailsBtnText}>More Details</Text>
          </Pressable>
        </View>
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

          {/* Count */}
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>
              {search.trim() ? `Results` : `Nearby`}
            </Text>
            <Text style={styles.sheetCount}>{displayList.length} spots</Text>
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

      {/* Loading overlay */}
      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator color={palette.accent} size="large" />
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
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 3 },
    elevation: 5,
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
    paddingRight: 8,
  },
  filterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
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
    top: Platform.OS === 'ios' ? 190 : 150,
    right: 12,
    maxWidth: 240,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(255, 255, 255, 0.95)',
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 10,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  topoHintBubbleText: {
    flex: 1,
    color: palette.text,
    fontSize: 11,
    lineHeight: 15,
  },
  // Focused location card
  focusedCard: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 100 : 72,
    left: 12,
    right: 12,
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    padding: 14,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
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
    paddingVertical: 10,
    borderRadius: 8,
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
    top: Platform.OS === 'ios' ? 120 : 80,
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

  // ── Layer picker popover ──────────────────────────────────────────
  layerPickerCard: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 170 : 130,
    right: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingVertical: 8,
    minWidth: 220,
    maxHeight: '70%',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
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
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: -4 },
    elevation: 6,
  },
  handleArea: {
    height: 56,
    alignItems: 'center',
    justifyContent: 'center',
    paddingTop: 8,
    cursor: 'grab' as any,
  },
  dragHandle: {
    width: 44,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: '#C5C5C0',
  },
  handleHint: {
    color: '#C5C5C0',
    fontSize: 10,
    marginTop: 2,
    letterSpacing: 2,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingBottom: 8,
  },
  sheetTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
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
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
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
    borderRadius: 10,
    padding: 14,
  },
  cardPressed: {
    opacity: 0.85,
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
    top: Platform.OS === 'ios' ? 170 : 130,
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
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(250, 250, 247, 0.7)',
    alignItems: 'center',
    justifyContent: 'center',
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
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 20,
    paddingBottom: Platform.OS === 'ios' ? 36 : 24,
    paddingTop: 12,
    maxHeight: '85%',
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
    fontSize: 22,
    fontWeight: '600',
    color: palette.text,
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
    top: Platform.OS === 'ios' ? 220 : 180,
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
  },
  marinaButtonActive: {
    backgroundColor: '#2563EB',
  },
  marinaSpinner: {
    position: 'absolute',
    top: -4,
    right: -4,
  },
  windButton: { position: 'absolute', top: Platform.OS === 'ios' ? 270 : 230, right: 16, width: 44, height: 44, borderRadius: 22, backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 4 },
  windButtonActive: { backgroundColor: palette.accent },
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
});
