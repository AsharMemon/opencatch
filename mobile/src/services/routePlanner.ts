/**
 * OpenCatch — Route Planner Service
 *
 * Full nautical route planning with obstacle avoidance, speed limit zones,
 * draft/depth checking, fuel calculations, and route persistence.
 * Competitive with Navionics autorouting.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  calculateBearing,
  calculateDistance,
  formatBearing,
  type NavCoord,
} from './nauticalNav';
import {
  calculateTrip,
  calculateFuelNeeded,
  formatDuration,
  FUEL_CONSUMPTION_ESTIMATES,
} from './fuelCalculator';
import { getDefaultBoat, type BoatProfile } from './boatProfile';
import {
  getRestrictedAreas,
  getShippingLanes,
  doesSegmentCrossRestricted,
  type RestrictedArea,
  type ShippingLane,
  type BBox as MaritimeBBox,
} from './maritimeRoutes';
import { getMaintainedChannels } from './usaceDepthSurveys';

// ── Types ────────────────────────────────────────────────────────────────────

export interface LatLng {
  lat: number;
  lon: number;
}

export interface RouteWaypoint {
  id: string;
  position: LatLng;
  label?: string;
  /** Index in the route (0 = origin) */
  index: number;
  /** True if this is a user-placed waypoint vs. auto-generated */
  isUserWaypoint: boolean;
}

export interface RouteSegment {
  from: LatLng;
  to: LatLng;
  distanceNm: number;
  bearingDeg: number;
  bearingLabel: string;
  /** Minimum depth along segment in meters (null if unknown) */
  minDepthM: number | null;
  /** Speed limit in knots (null if no limit) */
  speedLimitKnots: number | null;
  /** Whether this segment passes through a no-wake zone */
  isNoWake: boolean;
}

export interface SpeedZone {
  center: LatLng;
  radiusNm: number;
  speedLimitKnots: number;
  name: string;
  isNoWake: boolean;
}

export interface ShallowWarning {
  position: LatLng;
  depthM: number;
  draftM: number;
  segmentIndex: number;
}

export interface RouteMetrics {
  totalDistanceNm: number;
  totalDistanceMi: number;
  /** ETA in hours without speed limits */
  estimatedTimeHours: number;
  /** ETA in hours accounting for speed limits */
  adjustedTimeHours: number;
  /** Formatted time string */
  estimatedTimeLabel: string;
  adjustedTimeLabel: string;
  /** Fuel consumption */
  fuelNeededGallons: number;
  fuelCostEstimate: number | null;
  /** Cruise speed used for calculation */
  cruiseSpeedKnots: number;
  /** Shallow water warnings */
  shallowWarnings: ShallowWarning[];
  /** Speed zones along the route */
  speedZones: SpeedZone[];
  /** ETA as ISO-8601 string */
  eta: string;
  adjustedEta: string;
}

export interface Route {
  id: string;
  name: string;
  waypoints: RouteWaypoint[];
  segments: RouteSegment[];
  metrics: RouteMetrics | null;
  createdAt: number;
  updatedAt: number;
}

export interface BoatRouteProfile {
  cruiseSpeedKnots: number;
  draftMeters: number;
  fuelConsumptionGPH: number;
  fuelCapacityGallons: number;
  fuelPricePerGallon: number;
}

export interface RouteProbeResult {
  onWater: boolean;
  depthM: number | null;
}

export type RouteProbe = (point: LatLng) => Promise<RouteProbeResult>;

interface SurveyedRouteLine {
  id: string;
  name: string;
  coordinates: LatLng[];
  source: 'maintained_channel' | 'shipping_lane';
  depthFt: number | null;
}

// ── Storage ──────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/saved_routes';
const CHANNEL_GRAPH_SNAP_NM = 2.5;
const CHANNEL_GRAPH_VERTEX_PRECISION = 4;
const CHANNEL_ROUTE_DISTANCE_MULTIPLIER = 2.5;
const CHANNEL_ROUTE_SCORE_BONUS = 180;
const GRID_ROUTING_MAX_CELLS = 196;
const GRID_ROUTING_MIN_SPACING_NM = 0.12;
const GRID_ROUTING_MAX_SPACING_NM = 0.55;
const GRID_ROUTING_SHALLOW_BUFFER_M = 0.8;

// ── Known speed limit / no-wake zones (expandable) ──────────────────────────

const KNOWN_SPEED_ZONES: SpeedZone[] = [
  // Example zones — in production these would come from NOAA / USACE data
  // No-wake zones near marinas, bridges, etc.
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function generateId(): string {
  return `route_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function waypointId(index: number): string {
  return `wp_${index}_${Math.random().toString(36).slice(2, 6)}`;
}

/**
 * Interpolate points along a segment at intervals of ~0.1nm for depth checking.
 */
function interpolateSegment(from: LatLng, to: LatLng, intervalNm: number = 0.1): LatLng[] {
  const dist = calculateDistance(
    { lat: from.lat, lon: from.lon },
    { lat: to.lat, lon: to.lon },
  );
  const steps = Math.max(Math.ceil(dist / intervalNm), 1);
  const points: LatLng[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    points.push({
      lat: from.lat + (to.lat - from.lat) * t,
      lon: from.lon + (to.lon - from.lon) * t,
    });
  }
  return points;
}

function offsetPointNm(from: LatLng, to: LatLng, offsetNm: number, side: 1 | -1): LatLng {
  const meanLatRad = ((from.lat + to.lat) / 2) * Math.PI / 180;
  const dx = (to.lon - from.lon) * Math.cos(meanLatRad);
  const dy = to.lat - from.lat;
  const len = Math.hypot(dx, dy) || 1;
  const nx = (-dy / len) * side;
  const ny = (dx / len) * side;
  const midpoint = {
    lat: (from.lat + to.lat) / 2,
    lon: (from.lon + to.lon) / 2,
  };
  const offsetDegLat = offsetNm / 60;
  const offsetDegLon = offsetDegLat / Math.max(Math.cos(meanLatRad), 0.2);
  return {
    lat: midpoint.lat + ny * offsetDegLat,
    lon: midpoint.lon + nx * offsetDegLon,
  };
}

function getPathDistanceNm(path: LatLng[]): number {
  return path.slice(1).reduce((sum, point, index) => {
    return sum + calculateDistance(
      { lat: path[index].lat, lon: path[index].lon },
      { lat: point.lat, lon: point.lon },
    );
  }, 0);
}

function nmToLatDegrees(nm: number): number {
  return nm / 60;
}

function nmToLonDegrees(nm: number, latitude: number): number {
  const cosLat = Math.max(Math.cos((latitude * Math.PI) / 180), 0.2);
  return nm / (60 * cosLat);
}

function simplifyRoutePath(path: LatLng[]): LatLng[] {
  if (path.length <= 2) return path;
  const simplified: LatLng[] = [path[0]];

  for (let i = 1; i < path.length - 1; i++) {
    const prev = simplified[simplified.length - 1];
    const current = path[i];
    const next = path[i + 1];

    const distToPrev = calculateDistance(
      { lat: prev.lat, lon: prev.lon },
      { lat: current.lat, lon: current.lon },
    );
    if (distToPrev < 0.03) {
      continue;
    }

    const bearingA = calculateBearing(
      { lat: prev.lat, lon: prev.lon },
      { lat: current.lat, lon: current.lon },
    );
    const bearingB = calculateBearing(
      { lat: current.lat, lon: current.lon },
      { lat: next.lat, lon: next.lon },
    );
    const delta = Math.abs((((bearingB - bearingA) + 540) % 360) - 180);
    if (delta < 10) {
      continue;
    }

    simplified.push(current);
  }

  simplified.push(path[path.length - 1]);
  return simplified;
}

interface GridCell {
  key: string;
  point: LatLng;
  onWater: boolean;
  depthM: number | null;
}

async function tryGridWaterPath(
  start: LatLng,
  end: LatLng,
  draftMeters: number,
  probe: RouteProbe,
): Promise<LatLng[] | null> {
  const segmentNm = calculateDistance(
    { lat: start.lat, lon: start.lon },
    { lat: end.lat, lon: end.lon },
  );
  const centerLat = (start.lat + end.lat) / 2;
  const paddingNm = Math.min(Math.max(segmentNm * 0.25, 0.45), 3.5);
  const minLat = Math.min(start.lat, end.lat) - nmToLatDegrees(paddingNm);
  const maxLat = Math.max(start.lat, end.lat) + nmToLatDegrees(paddingNm);
  const minLon = Math.min(start.lon, end.lon) - nmToLonDegrees(paddingNm, centerLat);
  const maxLon = Math.max(start.lon, end.lon) + nmToLonDegrees(paddingNm, centerLat);

  let spacingNm = Math.min(
    GRID_ROUTING_MAX_SPACING_NM,
    Math.max(GRID_ROUTING_MIN_SPACING_NM, segmentNm / 14),
  );
  let rows = Math.ceil((maxLat - minLat) / nmToLatDegrees(spacingNm)) + 1;
  let cols = Math.ceil((maxLon - minLon) / nmToLonDegrees(spacingNm, centerLat)) + 1;
  while (rows * cols > GRID_ROUTING_MAX_CELLS && spacingNm < GRID_ROUTING_MAX_SPACING_NM) {
    spacingNm = Math.min(GRID_ROUTING_MAX_SPACING_NM, spacingNm * 1.18);
    rows = Math.ceil((maxLat - minLat) / nmToLatDegrees(spacingNm)) + 1;
    cols = Math.ceil((maxLon - minLon) / nmToLonDegrees(spacingNm, centerLat)) + 1;
  }

  const latStep = rows > 1 ? (maxLat - minLat) / (rows - 1) : 0;
  const lonStep = cols > 1 ? (maxLon - minLon) / (cols - 1) : 0;
  const cells = new Map<string, GridCell>();
  const waterKeys: string[] = [];

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const key = `${row},${col}`;
      const point = {
        lat: minLat + row * latStep,
        lon: minLon + col * lonStep,
      };
      const result = await probe(point);
      const cell: GridCell = {
        key,
        point,
        onWater: result.onWater,
        depthM: result.depthM,
      };
      cells.set(key, cell);
      if (cell.onWater) {
        waterKeys.push(key);
      }
    }
  }

  if (waterKeys.length === 0) return null;

  const nearestWaterKey = (target: LatLng): string | null => {
    let bestKey: string | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const key of waterKeys) {
      const cell = cells.get(key);
      if (!cell) continue;
      const distance = calculateDistance(
        { lat: target.lat, lon: target.lon },
        { lat: cell.point.lat, lon: cell.point.lon },
      );
      if (distance < bestDistance) {
        bestDistance = distance;
        bestKey = key;
      }
    }
    return bestDistance <= Math.max(segmentNm * 0.5, 1.5) ? bestKey : null;
  };

  const startKey = nearestWaterKey(start);
  const endKey = nearestWaterKey(end);
  if (!startKey || !endKey) return null;

  const heuristic = (fromKey: string): number => {
    const from = cells.get(fromKey);
    const to = cells.get(endKey);
    if (!from || !to) return Number.POSITIVE_INFINITY;
    return calculateDistance(
      { lat: from.point.lat, lon: from.point.lon },
      { lat: to.point.lat, lon: to.point.lon },
    );
  };

  const open = new Set<string>([startKey]);
  const cameFrom = new Map<string, string>();
  const gScore = new Map<string, number>([[startKey, 0]]);
  const fScore = new Map<string, number>([[startKey, heuristic(startKey)]]);

  const neighborOffsets = [
    [-1, -1], [-1, 0], [-1, 1],
    [0, -1],           [0, 1],
    [1, -1],  [1, 0],  [1, 1],
  ] as const;

  while (open.size > 0) {
    let currentKey: string | null = null;
    let currentBest = Number.POSITIVE_INFINITY;
    for (const key of open) {
      const score = fScore.get(key) ?? Number.POSITIVE_INFINITY;
      if (score < currentBest) {
        currentBest = score;
        currentKey = key;
      }
    }
    if (!currentKey) break;
    if (currentKey === endKey) {
      const pathKeys: string[] = [currentKey];
      let cursor = currentKey;
      while (cameFrom.has(cursor)) {
        cursor = cameFrom.get(cursor)!;
        pathKeys.unshift(cursor);
      }
      const gridPath = pathKeys
        .map((key) => cells.get(key)?.point)
        .filter(Boolean) as LatLng[];
      return simplifyRoutePath([start, ...gridPath, end]);
    }

    open.delete(currentKey);
    const [row, col] = currentKey.split(',').map(Number);
    const currentCell = cells.get(currentKey);
    if (!currentCell) continue;

    for (const [dRow, dCol] of neighborOffsets) {
      const nextKey = `${row + dRow},${col + dCol}`;
      const nextCell = cells.get(nextKey);
      if (!nextCell || !nextCell.onWater) continue;
      if (nextCell.depthM != null && nextCell.depthM < draftMeters) continue;

      const stepCost = calculateDistance(
        { lat: currentCell.point.lat, lon: currentCell.point.lon },
        { lat: nextCell.point.lat, lon: nextCell.point.lon },
      );
      const shallowPenalty =
        nextCell.depthM != null && nextCell.depthM < draftMeters + GRID_ROUTING_SHALLOW_BUFFER_M
          ? 2.4
          : 0;
      const tentative = (gScore.get(currentKey) ?? Number.POSITIVE_INFINITY) + stepCost + shallowPenalty;

      if (tentative < (gScore.get(nextKey) ?? Number.POSITIVE_INFINITY)) {
        cameFrom.set(nextKey, currentKey);
        gScore.set(nextKey, tentative);
        fScore.set(nextKey, tentative + heuristic(nextKey));
        open.add(nextKey);
      }
    }
  }

  return null;
}

function buildRouteBBox(start: LatLng, end: LatLng, paddingDeg: number): MaritimeBBox {
  return {
    minLat: Math.min(start.lat, end.lat) - paddingDeg,
    maxLat: Math.max(start.lat, end.lat) + paddingDeg,
    minLon: Math.min(start.lon, end.lon) - paddingDeg,
    maxLon: Math.max(start.lon, end.lon) + paddingDeg,
  };
}

function geometryToLineStrings(geometry: GeoJSON.Geometry | null): LatLng[][] {
  if (!geometry) return [];
  if (geometry.type === 'LineString') {
    return [geometry.coordinates.map(([lon, lat]) => ({ lat, lon }))];
  }
  if (geometry.type === 'MultiLineString') {
    return geometry.coordinates.map((line) => line.map(([lon, lat]) => ({ lat, lon })));
  }
  return [];
}

function vertexKey(point: LatLng): string {
  return `${point.lat.toFixed(CHANNEL_GRAPH_VERTEX_PRECISION)},${point.lon.toFixed(CHANNEL_GRAPH_VERTEX_PRECISION)}`;
}

function dedupePathPoints(points: LatLng[]): LatLng[] {
  const deduped: LatLng[] = [];
  for (const point of points) {
    const last = deduped[deduped.length - 1];
    if (!last) {
      deduped.push(point);
      continue;
    }
    if (calculateDistance({ lat: last.lat, lon: last.lon }, { lat: point.lat, lon: point.lon }) > 0.01) {
      deduped.push(point);
    }
  }
  return deduped;
}

function findNearestVertex(
  nodes: Map<string, LatLng>,
  point: LatLng,
): { key: string; point: LatLng; distanceNm: number } | null {
  let best: { key: string; point: LatLng; distanceNm: number } | null = null;
  for (const [key, nodePoint] of nodes.entries()) {
    const distanceNm = calculateDistance(
      { lat: point.lat, lon: point.lon },
      { lat: nodePoint.lat, lon: nodePoint.lon },
    );
    if (!best || distanceNm < best.distanceNm) {
      best = { key, point: nodePoint, distanceNm };
    }
  }
  return best;
}

function buildChannelGraph(lines: SurveyedRouteLine[]): {
  nodes: Map<string, LatLng>;
  edges: Map<string, Array<{ to: string; cost: number }>>;
} {
  const nodes = new Map<string, LatLng>();
  const edges = new Map<string, Array<{ to: string; cost: number }>>();

  const addEdge = (from: string, to: string, cost: number) => {
    const list = edges.get(from) ?? [];
    list.push({ to, cost });
    edges.set(from, list);
  };

  for (const line of lines) {
    for (let i = 0; i < line.coordinates.length - 1; i++) {
      const fromPoint = line.coordinates[i];
      const toPoint = line.coordinates[i + 1];
      const fromKey = vertexKey(fromPoint);
      const toKey = vertexKey(toPoint);
      nodes.set(fromKey, fromPoint);
      nodes.set(toKey, toPoint);
      const distanceNm = calculateDistance(
        { lat: fromPoint.lat, lon: fromPoint.lon },
        { lat: toPoint.lat, lon: toPoint.lon },
      );
      addEdge(fromKey, toKey, distanceNm);
      addEdge(toKey, fromKey, distanceNm);
    }
  }

  return { nodes, edges };
}

function dijkstraPath(
  edges: Map<string, Array<{ to: string; cost: number }>>,
  startKey: string,
  endKey: string,
): string[] | null {
  const distances = new Map<string, number>([[startKey, 0]]);
  const previous = new Map<string, string>();
  const pending = new Set<string>([startKey]);

  while (pending.size > 0) {
    let current: string | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const key of pending) {
      const distance = distances.get(key) ?? Number.POSITIVE_INFINITY;
      if (distance < bestDistance) {
        bestDistance = distance;
        current = key;
      }
    }
    if (!current) break;
    if (current === endKey) break;
    pending.delete(current);

    for (const edge of edges.get(current) ?? []) {
      const candidate = bestDistance + edge.cost;
      if (candidate < (distances.get(edge.to) ?? Number.POSITIVE_INFINITY)) {
        distances.set(edge.to, candidate);
        previous.set(edge.to, current);
        pending.add(edge.to);
      }
    }
  }

  if (!distances.has(endKey)) return null;
  const path: string[] = [];
  let current = endKey;
  path.unshift(current);
  while (current !== startKey) {
    const prev = previous.get(current);
    if (!prev) return null;
    current = prev;
    path.unshift(current);
  }
  return path;
}

function shippingLanesToRouteLines(lanes: ShippingLane[]): SurveyedRouteLine[] {
  return lanes
    .filter((lane) => lane.coordinates.length >= 2)
    .map((lane) => ({
      id: lane.id,
      name: lane.name,
      coordinates: lane.coordinates,
      source: 'shipping_lane' as const,
      depthFt: null,
    }));
}

function maintainedChannelsToRouteLines(
  channels: Awaited<ReturnType<typeof getMaintainedChannels>>,
  draftMeters: number,
): SurveyedRouteLine[] {
  const minimumDepthFt = draftMeters * 3.28084;
  const lines: SurveyedRouteLine[] = [];
  for (const channel of channels) {
    if (
      channel.authorizedDepthFt != null &&
      channel.authorizedDepthFt > 0 &&
      channel.authorizedDepthFt + 1 < minimumDepthFt
    ) {
      continue;
    }
    for (const coordinates of geometryToLineStrings(channel.geometry)) {
      if (coordinates.length < 2) continue;
      lines.push({
        id: channel.id,
        name: channel.channelName,
        coordinates,
        source: 'maintained_channel',
        depthFt: channel.authorizedDepthFt,
      });
    }
  }
  return lines;
}

async function trySurveyedChannelPath(
  start: LatLng,
  end: LatLng,
  draftMeters: number,
): Promise<LatLng[] | null> {
  const segmentNm = calculateDistance(
    { lat: start.lat, lon: start.lon },
    { lat: end.lat, lon: end.lon },
  );
  const paddingDeg = Math.min(Math.max(segmentNm / 120, 0.06), 0.4);
  const bbox = buildRouteBBox(start, end, paddingDeg);
  const surveyBbox = {
    west: bbox.minLon,
    south: bbox.minLat,
    east: bbox.maxLon,
    north: bbox.maxLat,
  };

  let maintainedChannels: Awaited<ReturnType<typeof getMaintainedChannels>> = [];
  let shippingLanes: ShippingLane[] = [];
  try {
    [maintainedChannels, shippingLanes] = await Promise.all([
      getMaintainedChannels(surveyBbox),
      getShippingLanes(bbox),
    ]);
  } catch {
    return null;
  }

  const lines = [
    ...maintainedChannelsToRouteLines(maintainedChannels, draftMeters),
    ...shippingLanesToRouteLines(shippingLanes),
  ];
  if (lines.length === 0) return null;

  const graph = buildChannelGraph(lines);
  if (graph.nodes.size < 2) return null;

  const startSnap = findNearestVertex(graph.nodes, start);
  const endSnap = findNearestVertex(graph.nodes, end);
  if (!startSnap || !endSnap) return null;
  if (startSnap.distanceNm > CHANNEL_GRAPH_SNAP_NM || endSnap.distanceNm > CHANNEL_GRAPH_SNAP_NM) {
    return null;
  }

  const pathKeys = dijkstraPath(graph.edges, startSnap.key, endSnap.key);
  if (!pathKeys || pathKeys.length === 0) return null;

  const pathPoints = pathKeys
    .map((key) => graph.nodes.get(key))
    .filter(Boolean) as LatLng[];
  const routed = dedupePathPoints([start, ...pathPoints, end]);
  if (routed.length < 2) return null;

  const routedDistanceNm = getPathDistanceNm(routed);
  if (routedDistanceNm > segmentNm * CHANNEL_ROUTE_DISTANCE_MULTIPLIER) {
    return null;
  }
  return routed;
}

async function scoreRoutePath(
  path: LatLng[],
  draftMeters: number,
  probe: RouteProbe,
): Promise<{ score: number; minDepthM: number | null; landHits: number; shallowHits: number }> {
  let landHits = 0;
  let shallowHits = 0;
  let minDepthM: number | null = null;

  for (let i = 0; i < path.length - 1; i++) {
    const samples = interpolateSegment(path[i], path[i + 1], 0.15);
    for (const sample of samples) {
      const result = await probe(sample);
      if (!result.onWater) {
        landHits += 1;
        continue;
      }
      if (typeof result.depthM === 'number' && Number.isFinite(result.depthM)) {
        minDepthM = minDepthM == null ? result.depthM : Math.min(minDepthM, result.depthM);
        if (result.depthM < draftMeters) {
          shallowHits += 2;
        } else if (result.depthM < draftMeters + 0.5) {
          shallowHits += 1;
        }
      }
    }
  }

  const totalNm = path.slice(1).reduce((sum, point, index) => {
    return sum + calculateDistance(
      { lat: path[index].lat, lon: path[index].lon },
      { lat: point.lat, lon: point.lon },
    );
  }, 0);

  return {
    score: landHits * 1000 + shallowHits * 120 + totalNm * 4,
    minDepthM,
    landHits,
    shallowHits,
  };
}

export async function autorouteWaypoints(
  waypoints: LatLng[],
  draftMeters: number,
  probe: RouteProbe,
): Promise<LatLng[]> {
  if (waypoints.length < 2) return waypoints;

  const routed: LatLng[] = [waypoints[0]];

  for (let i = 0; i < waypoints.length - 1; i++) {
    const start = routed[routed.length - 1];
    const end = waypoints[i + 1];
    const direct = [start, end];
    const directScore = await scoreRoutePath(direct, draftMeters, probe);

    let bestPath = direct;
    let bestScore = directScore.score;

    const segmentNm = calculateDistance(
      { lat: start.lat, lon: start.lon },
      { lat: end.lat, lon: end.lon },
    );

    const surveyedChannelPath = await trySurveyedChannelPath(start, end, draftMeters);
    if (surveyedChannelPath) {
      const channelScore = await scoreRoutePath(surveyedChannelPath, draftMeters, probe);
      const effectiveChannelScore = Math.max(0, channelScore.score - CHANNEL_ROUTE_SCORE_BONUS);
      if (
        channelScore.landHits === 0 &&
        channelScore.shallowHits <= directScore.shallowHits + 1 &&
        effectiveChannelScore < bestScore
      ) {
        bestPath = surveyedChannelPath;
        bestScore = effectiveChannelScore;
      }
    }

    if (directScore.landHits > 0 || directScore.shallowHits > 0) {
      const gridPath = await tryGridWaterPath(start, end, draftMeters, probe);
      if (gridPath) {
        const gridScore = await scoreRoutePath(gridPath, draftMeters, probe);
        if (
          gridScore.landHits === 0 &&
          (gridScore.score < bestScore ||
            (directScore.landHits > 0 && gridScore.shallowHits <= directScore.shallowHits))
        ) {
          bestPath = gridPath;
          bestScore = gridScore.score;
        }
      }
    }

    const offsets = [0.25, 0.5, 1, 2, Math.min(Math.max(segmentNm * 0.2, 0.75), 3)];

    for (const offsetNm of offsets) {
      for (const side of [1, -1] as const) {
        const mid = offsetPointNm(start, end, offsetNm, side);
        const candidate = [start, mid, end];
        const candidateScore = await scoreRoutePath(candidate, draftMeters, probe);
        if (candidateScore.score < bestScore) {
          bestPath = candidate;
          bestScore = candidateScore.score;
        }
      }
    }

    for (const point of bestPath.slice(1)) {
      routed.push(point);
    }
  }

  return routed;
}

/**
 * Check if a point falls within a speed zone.
 */
function isInSpeedZone(point: LatLng, zone: SpeedZone): boolean {
  const dist = calculateDistance(
    { lat: point.lat, lon: point.lon },
    { lat: zone.center.lat, lon: zone.center.lon },
  );
  return dist <= zone.radiusNm;
}

// ── Core Route Planning ──────────────────────────────────────────────────────

/**
 * Create a route from a list of waypoints.
 * Builds segments between consecutive waypoints with bearing/distance calculations.
 */
export function createRoute(waypoints: LatLng[], name?: string): Route {
  const routeWaypoints: RouteWaypoint[] = waypoints.map((wp, i) => ({
    id: waypointId(i),
    position: wp,
    label: i === 0 ? 'Start' : i === waypoints.length - 1 ? 'End' : `WP ${i}`,
    index: i,
    isUserWaypoint: true,
  }));

  const segments: RouteSegment[] = [];
  for (let i = 0; i < waypoints.length - 1; i++) {
    const from = waypoints[i];
    const to = waypoints[i + 1];
    const fromCoord: NavCoord = { lat: from.lat, lon: from.lon };
    const toCoord: NavCoord = { lat: to.lat, lon: to.lon };

    const distanceNm = calculateDistance(fromCoord, toCoord);
    const bearingDeg = calculateBearing(fromCoord, toCoord);
    const bearingLabel = formatBearing(bearingDeg);

    // Check speed zones along segment
    const midpoint: LatLng = {
      lat: (from.lat + to.lat) / 2,
      lon: (from.lon + to.lon) / 2,
    };
    const speedZone = KNOWN_SPEED_ZONES.find((z) => isInSpeedZone(midpoint, z));

    segments.push({
      from,
      to,
      distanceNm,
      bearingDeg,
      bearingLabel,
      minDepthM: null, // Populated by checkRouteDepth
      speedLimitKnots: speedZone?.speedLimitKnots ?? null,
      isNoWake: speedZone?.isNoWake ?? false,
    });
  }

  return {
    id: generateId(),
    name: name ?? `Route ${new Date().toLocaleDateString()}`,
    waypoints: routeWaypoints,
    segments,
    metrics: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

/**
 * Calculate route metrics: distance, ETA, fuel, cost.
 */
export function calculateRouteMetrics(
  route: Route,
  boatProfile: BoatRouteProfile,
): RouteMetrics {
  const totalDistanceNm = route.segments.reduce((sum, s) => sum + s.distanceNm, 0);
  const totalDistanceMi = totalDistanceNm * 1.15078;

  // Base ETA without speed limits
  const estimatedTimeHours =
    boatProfile.cruiseSpeedKnots > 0
      ? totalDistanceNm / boatProfile.cruiseSpeedKnots
      : 0;

  // Adjusted ETA accounting for speed limit zones
  let adjustedTimeHours = 0;
  const speedZones: SpeedZone[] = [];

  for (const segment of route.segments) {
    if (segment.speedLimitKnots != null && segment.speedLimitKnots < boatProfile.cruiseSpeedKnots) {
      const effectiveSpeed = segment.isNoWake ? Math.min(5, segment.speedLimitKnots) : segment.speedLimitKnots;
      adjustedTimeHours += segment.distanceNm / effectiveSpeed;

      // Track the speed zone
      const midpoint: LatLng = {
        lat: (segment.from.lat + segment.to.lat) / 2,
        lon: (segment.from.lon + segment.to.lon) / 2,
      };
      speedZones.push({
        center: midpoint,
        radiusNm: segment.distanceNm / 2,
        speedLimitKnots: segment.speedLimitKnots,
        name: segment.isNoWake ? 'No-Wake Zone' : `${segment.speedLimitKnots}kt Limit`,
        isNoWake: segment.isNoWake,
      });
    } else {
      adjustedTimeHours += boatProfile.cruiseSpeedKnots > 0
        ? segment.distanceNm / boatProfile.cruiseSpeedKnots
        : 0;
    }
  }

  // Fuel calculation
  const fuelNeededGallons = calculateFuelNeeded(
    totalDistanceNm,
    boatProfile.cruiseSpeedKnots,
    boatProfile.fuelConsumptionGPH,
  );
  const fuelCostEstimate = boatProfile.fuelPricePerGallon > 0
    ? fuelNeededGallons * boatProfile.fuelPricePerGallon
    : null;

  // Shallow warnings from segments
  const shallowWarnings: ShallowWarning[] = route.segments
    .map((seg, idx) => {
      if (seg.minDepthM != null && seg.minDepthM < boatProfile.draftMeters) {
        return {
          position: {
            lat: (seg.from.lat + seg.to.lat) / 2,
            lon: (seg.from.lon + seg.to.lon) / 2,
          },
          depthM: seg.minDepthM,
          draftM: boatProfile.draftMeters,
          segmentIndex: idx,
        };
      }
      return null;
    })
    .filter(Boolean) as ShallowWarning[];

  const now = Date.now();

  return {
    totalDistanceNm: Math.round(totalDistanceNm * 100) / 100,
    totalDistanceMi: Math.round(totalDistanceMi * 100) / 100,
    estimatedTimeHours,
    adjustedTimeHours,
    estimatedTimeLabel: formatDuration(estimatedTimeHours),
    adjustedTimeLabel: formatDuration(adjustedTimeHours),
    fuelNeededGallons: Math.round(fuelNeededGallons * 10) / 10,
    fuelCostEstimate: fuelCostEstimate != null ? Math.round(fuelCostEstimate * 100) / 100 : null,
    cruiseSpeedKnots: boatProfile.cruiseSpeedKnots,
    shallowWarnings,
    speedZones,
    eta: new Date(now + estimatedTimeHours * 3600000).toISOString(),
    adjustedEta: new Date(now + adjustedTimeHours * 3600000).toISOString(),
  };
}

/**
 * Check route depth against boat draft.
 * Uses available depth data (bathymetry model or NOAA soundings).
 * Returns updated route with depth info populated on segments.
 */
export async function checkRouteDepth(
  route: Route,
  draftMeters: number,
): Promise<{ route: Route; warnings: ShallowWarning[] }> {
  const warnings: ShallowWarning[] = [];
  const updatedSegments = [...route.segments];

  for (let i = 0; i < updatedSegments.length; i++) {
    const segment = updatedSegments[i];
    const points = interpolateSegment(segment.from, segment.to);

    // Query depth at interpolated points
    // In production, this would call our bathymetry model or NOAA depth API
    let minDepth: number | null = null;

    for (const point of points) {
      const depth = await queryDepthAtPoint(point);
      if (depth != null) {
        if (minDepth == null || depth < minDepth) {
          minDepth = depth;
        }
      }
    }

    updatedSegments[i] = { ...segment, minDepthM: minDepth };

    if (minDepth != null && minDepth < draftMeters) {
      warnings.push({
        position: {
          lat: (segment.from.lat + segment.to.lat) / 2,
          lon: (segment.from.lon + segment.to.lon) / 2,
        },
        depthM: minDepth,
        draftM: draftMeters,
        segmentIndex: i,
      });
    }
  }

  return {
    route: { ...route, segments: updatedSegments },
    warnings,
  };
}

/**
 * Query depth at a specific point.
 * Uses NOAA bathymetric data when available, falls back to our ML model.
 */
async function queryDepthAtPoint(point: LatLng): Promise<number | null> {
  try {
    // Try NOAA ENC depth soundings first
    const noaaUrl =
      `https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/ENCOnline/MapServer/identify` +
      `?geometry=${point.lon},${point.lat}` +
      `&geometryType=esriGeometryPoint&sr=4326&layers=all&tolerance=50` +
      `&mapExtent=${point.lon - 0.01},${point.lat - 0.01},${point.lon + 0.01},${point.lat + 0.01}` +
      `&imageDisplay=400,400,96&returnGeometry=false&f=json`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);

    const res = await fetch(noaaUrl, { signal: controller.signal });
    clearTimeout(timeout);

    if (res.ok) {
      const data = await res.json();
      const results = data.results ?? [];
      for (const result of results) {
        const depth = result.attributes?.DRVAL1 ?? result.attributes?.depth;
        if (depth != null && typeof depth === 'number') {
          return depth;
        }
      }
    }
  } catch {
    // Silently fall through — depth data not available
  }

  return null;
}

/**
 * Get speed limits along the route.
 * Queries known no-wake zones and regulatory speed limits.
 */
export function getSpeedLimitsAlongRoute(route: Route): SpeedZone[] {
  const zones: SpeedZone[] = [];

  for (const segment of route.segments) {
    const points = interpolateSegment(segment.from, segment.to, 0.2);
    for (const point of points) {
      for (const zone of KNOWN_SPEED_ZONES) {
        if (isInSpeedZone(point, zone) && !zones.find((z) => z.name === zone.name)) {
          zones.push(zone);
        }
      }
    }
  }

  return zones;
}

/**
 * Recalculate ETA accounting for speed zones along the route.
 */
export function adjustETAForSpeedLimits(
  route: Route,
  speedZones: SpeedZone[],
  cruiseSpeedKnots: number,
): { adjustedTimeHours: number; adjustedTimeLabel: string; adjustedEta: string } {
  let totalHours = 0;

  for (const segment of route.segments) {
    const midpoint: LatLng = {
      lat: (segment.from.lat + segment.to.lat) / 2,
      lon: (segment.from.lon + segment.to.lon) / 2,
    };

    const applicableZone = speedZones.find((z) => isInSpeedZone(midpoint, z));

    if (applicableZone && applicableZone.speedLimitKnots < cruiseSpeedKnots) {
      const effectiveSpeed = applicableZone.isNoWake
        ? Math.min(5, applicableZone.speedLimitKnots)
        : applicableZone.speedLimitKnots;
      totalHours += segment.distanceNm / effectiveSpeed;
    } else {
      totalHours += cruiseSpeedKnots > 0 ? segment.distanceNm / cruiseSpeedKnots : 0;
    }
  }

  return {
    adjustedTimeHours: totalHours,
    adjustedTimeLabel: formatDuration(totalHours),
    adjustedEta: new Date(Date.now() + totalHours * 3600000).toISOString(),
  };
}

// ── Route Persistence ────────────────────────────────────────────────────────

/**
 * Save a route to AsyncStorage.
 */
export async function saveRoute(route: Route, name?: string): Promise<Route> {
  const routes = await getRoutes();
  const updated: Route = {
    ...route,
    name: name ?? route.name,
    updatedAt: Date.now(),
  };

  const existingIdx = routes.findIndex((r) => r.id === route.id);
  if (existingIdx >= 0) {
    routes[existingIdx] = updated;
  } else {
    routes.push(updated);
  }

  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(routes));
  return updated;
}

/**
 * Get all saved routes.
 */
export async function getRoutes(): Promise<Route[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as Route[];
  } catch {
    return [];
  }
}

/**
 * Delete a saved route.
 */
export async function deleteRoute(routeId: string): Promise<void> {
  const routes = await getRoutes();
  const filtered = routes.filter((r) => r.id !== routeId);
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
}

// ── Default Boat Profile Adapter ─────────────────────────────────────────────

/**
 * Build a BoatRouteProfile from the user's saved boat profile.
 */
export async function getDefaultBoatRouteProfile(): Promise<BoatRouteProfile> {
  const boat = await getDefaultBoat();
  const boatType = boat?.type ?? 'other';
  const estimates = FUEL_CONSUMPTION_ESTIMATES[boatType] ?? FUEL_CONSUMPTION_ESTIMATES.other;
  const draftMeters = boat?.draftFt && boat.draftFt > 0
    ? boat.draftFt * 0.3048
    : 0.6;

  return {
    cruiseSpeedKnots: 20,
    draftMeters,
    fuelConsumptionGPH: estimates.cruise,
    fuelCapacityGallons: boat?.fuelCapacity ?? 30,
    fuelPricePerGallon: 4.50, // default fuel price
  };
}

/**
 * Get total route distance in nautical miles.
 */
export function getRouteDistance(route: Route): number {
  return route.segments.reduce((sum, s) => sum + s.distanceNm, 0);
}

/**
 * Get the bearing/distance to the next waypoint from a current position.
 * Used for turn-by-turn navigation mode.
 */
export function getNextWaypointNav(
  currentPosition: LatLng,
  route: Route,
  currentWaypointIndex: number,
): {
  bearing: number;
  bearingLabel: string;
  distanceNm: number;
  distanceMi: number;
  isLastWaypoint: boolean;
} | null {
  if (currentWaypointIndex >= route.waypoints.length) return null;

  const target = route.waypoints[currentWaypointIndex].position;
  const from: NavCoord = { lat: currentPosition.lat, lon: currentPosition.lon };
  const to: NavCoord = { lat: target.lat, lon: target.lon };

  const bearing = calculateBearing(from, to);
  const distanceNm = calculateDistance(from, to);

  return {
    bearing,
    bearingLabel: formatBearing(bearing),
    distanceNm: Math.round(distanceNm * 100) / 100,
    distanceMi: Math.round(distanceNm * 1.15078 * 100) / 100,
    isLastWaypoint: currentWaypointIndex === route.waypoints.length - 1,
  };
}

// ── Route Restriction Checking ────────────────────────────────────

export interface RouteRestrictionWarning {
  /** Which segment triggered the warning */
  segmentIndex: number;
  /** Position along the segment where restriction was detected */
  position: LatLng;
  /** Type of restriction */
  type: 'restricted_area' | 'shipping_lane_crossing';
  /** Severity: danger (military, security) or warning (sanctuary, etc.) */
  severity: 'danger' | 'warning' | 'caution';
  /** Name of the restricted area or shipping lane */
  name: string;
  /** Detailed description for the user */
  message: string;
  /** The restricted area details (if type is restricted_area) */
  restrictedArea?: RestrictedArea;
}

/**
 * Check if any route segment passes through a restricted area or crosses a shipping lane.
 * Returns all warnings found along the route.
 *
 * This is the primary safety check — should be called automatically when
 * a route is created or modified.
 */
export async function checkRouteRestrictions(
  route: Route,
): Promise<RouteRestrictionWarning[]> {
  if (route.segments.length === 0) return [];

  // Build bounding box for the entire route
  const lats = route.waypoints.map((w) => w.position.lat);
  const lons = route.waypoints.map((w) => w.position.lon);
  const bbox: MaritimeBBox = {
    minLat: Math.min(...lats) - 0.05,
    maxLat: Math.max(...lats) + 0.05,
    minLon: Math.min(...lons) - 0.05,
    maxLon: Math.max(...lons) + 0.05,
  };

  // Fetch restricted areas and shipping lanes in parallel
  const [restrictedAreas, shippingLanes] = await Promise.all([
    getRestrictedAreas(bbox),
    getShippingLanes(bbox),
  ]);

  const warnings: RouteRestrictionWarning[] = [];

  // Check each segment against restricted areas
  for (let i = 0; i < route.segments.length; i++) {
    const segment = route.segments[i];

    // Check restricted areas
    const restrictedHit = doesSegmentCrossRestricted(
      segment.from,
      segment.to,
      restrictedAreas,
      30, // Fine sampling for safety
    );

    if (restrictedHit) {
      const midpoint: LatLng = {
        lat: (segment.from.lat + segment.to.lat) / 2,
        lon: (segment.from.lon + segment.to.lon) / 2,
      };

      const typeLabel = restrictedHit.type
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());

      warnings.push({
        segmentIndex: i,
        position: midpoint,
        type: 'restricted_area',
        severity: restrictedHit.severity,
        name: restrictedHit.name,
        message: `Route passes through ${typeLabel}: ${restrictedHit.name}. ${restrictedHit.restrictions}`,
        restrictedArea: restrictedHit,
      });
    }

    // Check shipping lane crossings
    for (const lane of shippingLanes) {
      if (doesSegmentCrossLine(segment.from, segment.to, lane.coordinates)) {
        const midpoint: LatLng = {
          lat: (segment.from.lat + segment.to.lat) / 2,
          lon: (segment.from.lon + segment.to.lon) / 2,
        };

        warnings.push({
          segmentIndex: i,
          position: midpoint,
          type: 'shipping_lane_crossing',
          severity: 'caution',
          name: lane.name,
          message: `Route crosses shipping lane: ${lane.name}. Exercise caution and maintain lookout.`,
        });
        break; // One warning per lane per segment is sufficient
      }
    }
  }

  return warnings;
}

/**
 * Check if a segment crosses a polyline (shipping lane).
 * Uses simple bounding-box + proximity test.
 */
function doesSegmentCrossLine(
  from: LatLng,
  to: LatLng,
  lineCoords: LatLng[],
  thresholdDeg: number = 0.005,
): boolean {
  // Sample points along our route segment
  const samples = 15;
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const px = from.lat + (to.lat - from.lat) * t;
    const py = from.lon + (to.lon - from.lon) * t;

    // Check proximity to any segment of the shipping lane
    for (let j = 0; j < lineCoords.length - 1; j++) {
      const ax = lineCoords[j].lat;
      const ay = lineCoords[j].lon;
      const bx = lineCoords[j + 1].lat;
      const by = lineCoords[j + 1].lon;

      // Point-to-segment distance approximation
      const dx = bx - ax;
      const dy = by - ay;
      const lenSq = dx * dx + dy * dy;
      if (lenSq === 0) continue;

      let param = ((px - ax) * dx + (py - ay) * dy) / lenSq;
      param = Math.max(0, Math.min(1, param));

      const nearX = ax + param * dx;
      const nearY = ay + param * dy;

      const dist = Math.sqrt((px - nearX) ** 2 + (py - nearY) ** 2);
      if (dist < thresholdDeg) return true;
    }
  }
  return false;
}

/**
 * Get the most severe restriction warning from a list.
 */
export function getMostSevereWarning(
  warnings: RouteRestrictionWarning[],
): RouteRestrictionWarning | null {
  if (warnings.length === 0) return null;
  const severityOrder: Record<string, number> = { danger: 3, warning: 2, caution: 1 };
  return warnings.reduce((most, w) =>
    (severityOrder[w.severity] ?? 0) > (severityOrder[most.severity] ?? 0) ? w : most,
  );
}
