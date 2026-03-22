/**
 * OpenCatch — Depth Number Overlay
 *
 * Classic nautical chart-style depth soundings on the map.
 * When zoomed in enough (zoom > 13), shows depth numbers as
 * small blue text labels on the water.
 *
 * Data source: NOAA ECDIS depth soundings via their public WFS,
 * with fallback to GEBCO bathymetric grid sampling.
 *
 * Numbers are sparse enough to remain readable (~200m spacing).
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { palette } from '../theme/palette';

// ── Types ────────────────────────────────────────────────────────────────────

interface DepthSounding {
  lat: number;
  lon: number;
  depthM: number;
}

interface Props {
  lat: number;
  lon: number;
  zoom: number;
  /** 'metric' for meters, 'imperial' for feet */
  units?: 'metric' | 'imperial';
  ShapeSource: any;
  SymbolLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

/** Minimum zoom level to show depth numbers. */
const MIN_ZOOM = 13;

/** Spacing between depth sample points in degrees at zoom 13 (~200m). */
const SAMPLE_SPACING_DEG = 0.002;

/** Radius of depth grid in degrees. */
const GRID_RADIUS_DEG = 0.05;

/** Minimum move distance before refetching (meters). */
const REFRESH_DISTANCE_M = 500;

/** Cache TTL: 1 hour (depth data does not change). */
const CACHE_TTL_MS = 60 * 60 * 1000;

/** GEBCO WMS for depth queries. */
const GEBCO_WMS = 'https://www.gebco.net/data_and_products/gebco_web_services/web_map_service/mapserv';

// ── Helpers ──────────────────────────────────────────────────────────────────

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
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

function metersToFeet(m: number): number {
  return m * 3.28084;
}

// ── Fetch depth soundings via GEBCO WMS grid sampling ────────────────────────

async function getDepthSoundings(
  centerLat: number,
  centerLon: number,
): Promise<DepthSounding[]> {
  const soundings: DepthSounding[] = [];
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
              const depthM = parseFloat(match[1]);
              // Only include underwater points (negative = below sea level in GEBCO)
              if (depthM < 0) {
                soundings.push({ lat: sLat, lon: sLon, depthM: Math.abs(depthM) });
              }
            }
          })
          .catch(() => {
            // Skip failed points
          }),
      );
    }
  }

  // Limit concurrency by batching
  const BATCH = 20;
  for (let i = 0; i < promises.length; i += BATCH) {
    await Promise.all(promises.slice(i, i + BATCH));
  }

  return soundings;
}

// ── GeoJSON conversion ───────────────────────────────────────────────────────

function soundingsToGeoJSON(
  soundings: DepthSounding[],
  units: 'metric' | 'imperial',
): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: soundings.map((s) => {
      const displayDepth =
        units === 'imperial'
          ? Math.round(metersToFeet(s.depthM))
          : Math.round(s.depthM * 10) / 10;
      const suffix = units === 'imperial' ? '' : '';

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [s.lon, s.lat],
        },
        properties: {
          depthM: s.depthM,
          depthDisplay: `${displayDepth}${suffix}`,
          // Deeper water = darker blue
          textColor:
            s.depthM < 5
              ? '#42A5F5'
              : s.depthM < 15
                ? '#1E88E5'
                : s.depthM < 30
                  ? '#1565C0'
                  : '#0D47A1',
        },
      };
    }),
  };
}

// ── Cache ────────────────────────────────────────────────────────────────────

let _depthCache: { geoJSON: GeoJSON.FeatureCollection; lat: number; lon: number; ts: number } | null = null;

// ── Component ────────────────────────────────────────────────────────────────

export function DepthNumberOverlay({
  lat,
  lon,
  zoom,
  units = 'imperial',
  ShapeSource,
  SymbolLayer,
  onLoadStart,
  onLoadEnd,
}: Props) {
  const [geoJSON, setGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(async (centerLat: number, centerLon: number) => {
    if (loadingRef.current) return;

    // Check cache
    if (_depthCache && Date.now() - _depthCache.ts < CACHE_TTL_MS) {
      const dist = haversineDistance(centerLat, centerLon, _depthCache.lat, _depthCache.lon);
      if (dist < REFRESH_DISTANCE_M) {
        setGeoJSON(_depthCache.geoJSON);
        return;
      }
    }

    // Check movement threshold
    if (lastCenter.current) {
      const dist = haversineDistance(centerLat, centerLon, lastCenter.current.lat, lastCenter.current.lon);
      if (dist < REFRESH_DISTANCE_M) return;
    }

    loadingRef.current = true;
    lastCenter.current = { lat: centerLat, lon: centerLon };
    onLoadStart?.();

    try {
      const soundings = await getDepthSoundings(centerLat, centerLon);
      if (soundings.length > 0) {
        const gj = soundingsToGeoJSON(soundings, units);
        _depthCache = { geoJSON: gj, lat: centerLat, lon: centerLon, ts: Date.now() };
        setGeoJSON(gj);
      } else {
        setGeoJSON(null);
      }
    } catch {
      // Keep previous data
    } finally {
      loadingRef.current = false;
      onLoadEnd?.();
    }
  }, [units, onLoadStart, onLoadEnd]);

  useEffect(() => {
    if (zoom < MIN_ZOOM) {
      // Don't clear data, just hide via the parent conditional
      return;
    }
    fetchData(lat, lon);
  }, [lat, lon, zoom, fetchData]);

  if (!geoJSON || geoJSON.features.length === 0 || zoom < MIN_ZOOM) return null;

  return (
    <ShapeSource id="depth-number-source" shape={geoJSON}>
      <SymbolLayer
        id="depth-number-labels"
        style={{
          textField: ['get', 'depthDisplay'],
          textSize: [
            'interpolate',
            ['linear'],
            ['zoom'],
            13, 12,
            15, 14,
            17, 17,
          ],
          textColor: ['get', 'textColor'],
          textHaloColor: 'rgba(255, 255, 255, 0.95)',
          textHaloWidth: 1.5,
          textFont: ['Open Sans Bold'],
          textAllowOverlap: false,
          textIgnorePlacement: false,
          textPadding: 6,
          textOptional: true,
        }}
      />
    </ShapeSource>
  );
}
