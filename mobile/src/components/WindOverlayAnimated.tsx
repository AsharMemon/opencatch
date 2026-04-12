import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchWindField,
  windFrameToArrowGeoJSON,
  windFrameToHeatGeoJSON,
  windFrameToStreamlineGeoJSON,
  type WindFieldData,
  type WindViewportBounds,
} from '../services/windOverlay';

interface Props {
  lat: number;
  lon: number;
  bounds?: WindViewportBounds | null;
  forecastHourIndex?: number;
  ShapeSource: any;
  SymbolLayer: any;
  CircleLayer: any;
  LineLayer: any;
  onLoadStart?: () => void;
  onLoadEnd?: () => void;
  onDataLoaded?: (payload: {
    vectorCount: number;
    center: { lat: number; lon: number };
    forecastCount: number;
    activeFrameLabel: string;
  }) => void;
}

const REFRESH_INTERVAL_MS = 15 * 60 * 1000;
const REFRESH_DISTANCE_M = 8000;
const FORECAST_HOURS = 8;

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const radiusM = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return radiusM * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function WindOverlayAnimated({
  lat,
  lon,
  bounds = null,
  forecastHourIndex = 0,
  ShapeSource,
  SymbolLayer,
  CircleLayer,
  LineLayer,
  onLoadStart,
  onLoadEnd,
  onDataLoaded,
}: Props) {
  const [fieldData, setFieldData] = useState<WindFieldData | null>(null);
  const lastCenter = useRef<{ lat: number; lon: number } | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const fetchData = useCallback(
    async (
      centerLat: number,
      centerLon: number,
      viewportBounds?: WindViewportBounds | null,
      force = false,
    ) => {
      if (loadingRef.current) return;

      if (!force && lastCenter.current) {
        const distance = haversineDistance(
          centerLat,
          centerLon,
          lastCenter.current.lat,
          lastCenter.current.lon,
        );
        if (distance < REFRESH_DISTANCE_M && fieldData) {
          return;
        }
      }

      loadingRef.current = true;
      lastCenter.current = { lat: centerLat, lon: centerLon };
      onLoadStart?.();

      try {
        const nextField = await fetchWindField(
          centerLat,
          centerLon,
          viewportBounds,
          FORECAST_HOURS,
        );
        setFieldData(nextField);
      } catch {
        // Keep the last successful field on transient failures.
      } finally {
        loadingRef.current = false;
        onLoadEnd?.();
      }
    },
    [fieldData, onLoadEnd, onLoadStart],
  );

  useEffect(() => {
    fetchData(lat, lon, bounds);
  }, [bounds, fetchData, lat, lon]);

  useEffect(() => {
    refreshTimer.current = setInterval(() => {
      if (lastCenter.current) {
        fetchData(lastCenter.current.lat, lastCenter.current.lon, bounds, true);
      }
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (refreshTimer.current) {
        clearInterval(refreshTimer.current);
      }
    };
  }, [bounds, fetchData]);

  const frame = useMemo(() => {
    if (!fieldData || fieldData.frames.length === 0) return null;
    const safeIndex = Math.max(
      0,
      Math.min(forecastHourIndex, fieldData.frames.length - 1),
    );
    return fieldData.frames[safeIndex] ?? fieldData.frames[0];
  }, [fieldData, forecastHourIndex]);

  const heatGeoJSON = useMemo(
    () => (frame ? windFrameToHeatGeoJSON(frame) : null),
    [frame],
  );
  const arrowGeoJSON = useMemo(
    () => (frame ? windFrameToArrowGeoJSON(frame) : null),
    [frame],
  );
  const streamGeoJSON = useMemo(
    () => (frame ? windFrameToStreamlineGeoJSON(frame) : null),
    [frame],
  );

  useEffect(() => {
    if (!frame || !arrowGeoJSON || !fieldData) return;
    onDataLoaded?.({
      vectorCount: arrowGeoJSON.features.length,
      center: { lat: fieldData.centerLat, lon: fieldData.centerLon },
      forecastCount: fieldData.frames.length,
      activeFrameLabel: frame.label,
    });
  }, [arrowGeoJSON, fieldData, frame, onDataLoaded]);

  if (!frame || (!arrowGeoJSON?.features.length && !heatGeoJSON?.features.length)) {
    return null;
  }

  return (
    <>
      {heatGeoJSON ? (
        <ShapeSource id="wind-heat-source" shape={heatGeoJSON}>
          <CircleLayer
            id="wind-heat-field"
            style={{
              circleRadius: [
                'interpolate',
                ['exponential', 1.2],
                ['zoom'],
                4, 9,
                7, 12,
                10, 15,
                13, 19,
              ],
              circleColor: ['get', 'color'],
              circleOpacity: [
                'interpolate',
                ['linear'],
                ['get', 'speedKn'],
                0, 0.015,
                10, 0.024,
                20, 0.032,
                32, 0.04,
              ],
              circleBlur: 0.95,
            }}
          />
        </ShapeSource>
      ) : null}

      {streamGeoJSON?.features.length ? (
        <ShapeSource id="wind-stream-source" shape={streamGeoJSON}>
          <LineLayer
            id="wind-stream-backdrop"
            style={{
              lineColor: 'rgba(255,255,255,0.28)',
              lineWidth: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.7,
                8, 1.3,
                12, 2.2,
                15, 2.8,
              ],
              lineOpacity: 0.3,
              lineCap: 'round',
              lineJoin: 'round',
            }}
          />
          <LineLayer
            id="wind-stream-lines"
            style={{
              lineColor: ['get', 'color'],
              lineOpacity: ['get', 'opacity'],
              lineWidth: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.45,
                8, 1,
                12, 1.8,
                15, 2.4,
              ],
              lineCap: 'round',
              lineJoin: 'round',
            }}
          />
        </ShapeSource>
      ) : null}

      {arrowGeoJSON ? (
        <ShapeSource id="wind-arrow-source" shape={arrowGeoJSON}>
          <SymbolLayer
            id="wind-flow-arrows-backdrop"
            style={{
              iconImage: 'triangle-11',
              iconSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.22,
                6, 0.27,
                9, 0.32,
                12, 0.39,
                14, 0.46,
              ],
              iconRotate: ['get', 'iconRotation'],
              iconAllowOverlap: true,
              iconIgnorePlacement: true,
              iconColor: 'rgba(255,255,255,0.76)',
              iconOpacity: 0.28,
              iconPitchAlignment: 'map',
              iconRotationAlignment: 'map',
            }}
          />

          <SymbolLayer
            id="wind-flow-arrows"
            style={{
              iconImage: 'triangle-11',
              iconSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                3, 0.18,
                6, 0.23,
                9, 0.29,
                12, 0.36,
                14, 0.42,
              ],
              iconRotate: ['get', 'iconRotation'],
              iconAllowOverlap: true,
              iconIgnorePlacement: true,
              iconColor: ['get', 'arrowColor'],
              iconOpacity: 0.96,
              iconPitchAlignment: 'map',
              iconRotationAlignment: 'map',
            }}
          />

          <SymbolLayer
            id="wind-speed-badges"
            minZoomLevel={13.4}
            style={{
              textField: ['concat', ['get', 'label'], ' m/s'],
              textSize: [
                'interpolate',
                ['linear'],
                ['zoom'],
                12.8, 9,
                14, 10.5,
              ],
              textColor: '#FFFFFF',
              textHaloColor: 'rgba(18, 48, 66, 0.72)',
              textHaloWidth: 1.8,
              textFont: ['Open Sans Bold'],
              textOffset: [0, 1.45],
              textAnchor: 'top',
              textOptional: true,
              textAllowOverlap: false,
              textPadding: 8,
            }}
          />
        </ShapeSource>
      ) : null}
    </>
  );
}
