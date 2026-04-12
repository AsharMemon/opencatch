import { calculateBearing, calculateDistance } from './nauticalNav';

export interface LatLng {
  lat: number;
  lon: number;
}

export interface NavigationFieldSample extends LatLng {
  onWater: boolean;
  depthM: number | null;
}

export interface NavigationFieldCell extends NavigationFieldSample {
  key: string;
  row: number;
  col: number;
  x: number;
  y: number;
  valid: boolean;
}

export interface NavigationFieldFrame {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface NavigationField {
  rows: number;
  cols: number;
  cells: NavigationFieldCell[];
  matrix: NavigationFieldCell[][];
  frame: NavigationFieldFrame;
  cellWidthPx: number;
  cellHeightPx: number;
  cellSpacingNm: number;
  waterCellCount: number;
  depthCellCount: number;
  coveragePct: number;
  builtAt: number;
}

export interface NavigationFieldWarning {
  position: LatLng;
  depthM: number;
  draftM: number;
  segmentIndex: number;
}

export type NavigationScreenSampler = (
  screenPoint: [number, number],
) => Promise<NavigationFieldSample | null>;

const SAMPLE_BATCH_SIZE = 24;

function buildGridScreenPoints(
  frame: NavigationFieldFrame,
  rows: number,
  cols: number,
): Array<{ row: number; col: number; x: number; y: number }> {
  const points: Array<{ row: number; col: number; x: number; y: number }> = [];
  const width = Math.max(frame.right - frame.left, 1);
  const height = Math.max(frame.bottom - frame.top, 1);

  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = frame.left + ((col + 0.5) / cols) * width;
      const y = frame.top + ((row + 0.5) / rows) * height;
      points.push({ row, col, x, y });
    }
  }

  return points;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function estimateCellSpacingNm(matrix: NavigationFieldCell[][]): number {
  const distances: number[] = [];

  for (let row = 0; row < matrix.length; row += 1) {
    for (let col = 0; col < matrix[row].length; col += 1) {
      const cell = matrix[row][col];
      if (!cell.valid) continue;

      const right = matrix[row][col + 1];
      const down = matrix[row + 1]?.[col];

      if (right?.valid) {
        distances.push(
          calculateDistance(
            { lat: cell.lat, lon: cell.lon },
            { lat: right.lat, lon: right.lon },
          ),
        );
      }
      if (down?.valid) {
        distances.push(
          calculateDistance(
            { lat: cell.lat, lon: cell.lon },
            { lat: down.lat, lon: down.lon },
          ),
        );
      }
    }
  }

  if (distances.length === 0) return 0.18;
  const sorted = distances.sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0.18;
}

function toCellArray(matrix: NavigationFieldCell[][]): NavigationFieldCell[] {
  const flat: NavigationFieldCell[] = [];
  for (const row of matrix) {
    for (const cell of row) {
      flat.push(cell);
    }
  }
  return flat;
}

export async function buildNavigationField(options: {
  frame: NavigationFieldFrame;
  rows: number;
  cols: number;
  sampler: NavigationScreenSampler;
}): Promise<NavigationField | null> {
  const { frame, rows, cols, sampler } = options;
  if (rows < 2 || cols < 2) return null;

  const screenPoints = buildGridScreenPoints(frame, rows, cols);
  const matrix: NavigationFieldCell[][] = Array.from({ length: rows }, () => []);
  let waterCellCount = 0;
  let depthCellCount = 0;

  for (let i = 0; i < screenPoints.length; i += SAMPLE_BATCH_SIZE) {
    const batch = screenPoints.slice(i, i + SAMPLE_BATCH_SIZE);
    const batchResults = await Promise.all(
      batch.map(async (entry) => {
        try {
          return await sampler([entry.x, entry.y]);
        } catch {
          return null;
        }
      }),
    );

    batch.forEach((entry, index) => {
      const sample = batchResults[index];
      const cell: NavigationFieldCell = {
        key: `${entry.row},${entry.col}`,
        row: entry.row,
        col: entry.col,
        x: entry.x,
        y: entry.y,
        lat: sample?.lat ?? Number.NaN,
        lon: sample?.lon ?? Number.NaN,
        onWater: sample?.onWater ?? false,
        depthM: sample?.depthM ?? null,
        valid:
          sample != null &&
          Number.isFinite(sample.lat) &&
          Number.isFinite(sample.lon),
      };

      if (cell.valid && cell.onWater) {
        waterCellCount += 1;
      }
      if (cell.valid && typeof cell.depthM === 'number' && Number.isFinite(cell.depthM)) {
        depthCellCount += 1;
      }

      matrix[entry.row][entry.col] = cell;
    });
  }

  const cells = toCellArray(matrix);
  const validCount = cells.filter((cell) => cell.valid).length;
  if (validCount < Math.max(rows * cols * 0.4, 16)) {
    return null;
  }

  return {
    rows,
    cols,
    cells,
    matrix,
    frame,
    cellWidthPx: Math.max((frame.right - frame.left) / cols, 1),
    cellHeightPx: Math.max((frame.bottom - frame.top) / rows, 1),
    cellSpacingNm: estimateCellSpacingNm(matrix),
    waterCellCount,
    depthCellCount,
    coveragePct: Math.round((depthCellCount / Math.max(validCount, 1)) * 100),
    builtAt: Date.now(),
  };
}

function findNearestCell(
  field: NavigationField,
  point: LatLng,
  options?: { waterOnly?: boolean; maxDistanceNm?: number },
): NavigationFieldCell | null {
  const waterOnly = options?.waterOnly ?? false;
  const maxDistanceNm =
    options?.maxDistanceNm ?? Math.max(field.cellSpacingNm * 3, 0.16);

  let best: NavigationFieldCell | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;

  for (const cell of field.cells) {
    if (!cell.valid) continue;
    if (waterOnly && !cell.onWater) continue;
    const distanceNm = calculateDistance(
      { lat: point.lat, lon: point.lon },
      { lat: cell.lat, lon: cell.lon },
    );
    if (distanceNm < bestDistance) {
      bestDistance = distanceNm;
      best = cell;
    }
  }

  if (!best || bestDistance > maxDistanceNm) {
    return null;
  }
  return best;
}

export function snapPointToNavigationField(
  field: NavigationField,
  point: LatLng,
  maxDistanceNm: number = Math.max(field.cellSpacingNm * 3.2, 0.18),
): LatLng | null {
  const nearest = findNearestCell(field, point, { waterOnly: true, maxDistanceNm });
  if (!nearest) return null;
  return { lat: nearest.lat, lon: nearest.lon };
}

export function probeNavigationField(
  field: NavigationField,
  point: LatLng,
): { onWater: boolean; depthM: number | null } {
  const nearestWater = findNearestCell(field, point, {
    waterOnly: true,
    maxDistanceNm: Math.max(field.cellSpacingNm * 2.5, 0.14),
  });

  if (!nearestWater) {
    return { onWater: false, depthM: null };
  }

  const nearby = field.cells.filter((cell) => {
    if (!cell.valid || !cell.onWater) return false;
    const distanceNm = calculateDistance(
      { lat: point.lat, lon: point.lon },
      { lat: cell.lat, lon: cell.lon },
    );
    return distanceNm <= Math.max(field.cellSpacingNm * 1.6, 0.1);
  });

  if (nearby.length === 0) {
    return { onWater: true, depthM: nearestWater.depthM };
  }

  let weightedDepth = 0;
  let depthWeight = 0;

  for (const cell of nearby) {
    if (typeof cell.depthM !== 'number' || !Number.isFinite(cell.depthM)) continue;
    const distanceNm = Math.max(
      calculateDistance(
        { lat: point.lat, lon: point.lon },
        { lat: cell.lat, lon: cell.lon },
      ),
      0.01,
    );
    const weight = 1 / (distanceNm * distanceNm);
    weightedDepth += cell.depthM * weight;
    depthWeight += weight;
  }

  return {
    onWater: true,
    depthM: depthWeight > 0 ? weightedDepth / depthWeight : nearestWater.depthM,
  };
}

function simplifyPath(path: LatLng[]): LatLng[] {
  if (path.length <= 2) return path;
  const simplified: LatLng[] = [path[0]];

  for (let i = 1; i < path.length - 1; i += 1) {
    const previous = simplified[simplified.length - 1];
    const current = path[i];
    const next = path[i + 1];

    const distanceToPrevious = calculateDistance(
      { lat: previous.lat, lon: previous.lon },
      { lat: current.lat, lon: current.lon },
    );
    if (distanceToPrevious < 0.03) {
      continue;
    }

    const bearingA = calculateBearing(
      { lat: previous.lat, lon: previous.lon },
      { lat: current.lat, lon: current.lon },
    );
    const bearingB = calculateBearing(
      { lat: current.lat, lon: current.lon },
      { lat: next.lat, lon: next.lon },
    );
    const delta = Math.abs((((bearingB - bearingA) + 540) % 360) - 180);
    if (delta < 12) {
      continue;
    }

    simplified.push(current);
  }

  simplified.push(path[path.length - 1]);
  return simplified;
}

function dedupePath(path: LatLng[]): LatLng[] {
  const deduped: LatLng[] = [];
  for (const point of path) {
    const last = deduped[deduped.length - 1];
    if (!last) {
      deduped.push(point);
      continue;
    }

    const distanceNm = calculateDistance(
      { lat: last.lat, lon: last.lon },
      { lat: point.lat, lon: point.lon },
    );
    if (distanceNm > 0.01) {
      deduped.push(point);
    }
  }
  return deduped;
}

function countBlockedNeighbors(field: NavigationField, cell: NavigationFieldCell): number {
  let blocked = 0;
  for (let dRow = -1; dRow <= 1; dRow += 1) {
    for (let dCol = -1; dCol <= 1; dCol += 1) {
      if (dRow === 0 && dCol === 0) continue;
      const neighbor = field.matrix[cell.row + dRow]?.[cell.col + dCol];
      if (!neighbor || !neighbor.valid || !neighbor.onWater) {
        blocked += 1;
      }
    }
  }
  return blocked;
}

function routeLegThroughField(
  field: NavigationField,
  start: LatLng,
  end: LatLng,
  draftMeters: number,
): LatLng[] | null {
  const startCell = findNearestCell(field, start, {
    waterOnly: true,
    maxDistanceNm: Math.max(field.cellSpacingNm * 4, 0.25),
  });
  const endCell = findNearestCell(field, end, {
    waterOnly: true,
    maxDistanceNm: Math.max(field.cellSpacingNm * 4, 0.25),
  });

  if (!startCell || !endCell) return null;

  const open = new Set<string>([startCell.key]);
  const cameFrom = new Map<string, string>();
  const gScore = new Map<string, number>([[startCell.key, 0]]);
  const fScore = new Map<string, number>([
    [
      startCell.key,
      calculateDistance(
        { lat: startCell.lat, lon: startCell.lon },
        { lat: endCell.lat, lon: endCell.lon },
      ),
    ],
  ]);

  const neighborOffsets = [
    [-1, -1], [-1, 0], [-1, 1],
    [0, -1],           [0, 1],
    [1, -1],  [1, 0],  [1, 1],
  ] as const;

  while (open.size > 0) {
    let currentKey: string | null = null;
    let currentScore = Number.POSITIVE_INFINITY;

    for (const key of open) {
      const score = fScore.get(key) ?? Number.POSITIVE_INFINITY;
      if (score < currentScore) {
        currentScore = score;
        currentKey = key;
      }
    }

    if (!currentKey) break;
    if (currentKey === endCell.key) {
      const pathKeys: string[] = [currentKey];
      let cursor = currentKey;
      while (cameFrom.has(cursor)) {
        cursor = cameFrom.get(cursor)!;
        pathKeys.unshift(cursor);
      }

      const points = pathKeys
        .map((key) => {
          const [row, col] = key.split(',').map(Number);
          return field.matrix[row]?.[col] ?? null;
        })
        .filter(Boolean)
        .map((cell) => ({ lat: cell!.lat, lon: cell!.lon }));

      return simplifyPath(dedupePath([start, ...points, end]));
    }

    open.delete(currentKey);
    const [row, col] = currentKey.split(',').map(Number);
    const current = field.matrix[row]?.[col];
    if (!current || !current.valid) continue;

    for (const [dRow, dCol] of neighborOffsets) {
      const next = field.matrix[row + dRow]?.[col + dCol];
      if (!next || !next.valid || !next.onWater) continue;
      if (typeof next.depthM === 'number' && next.depthM < draftMeters) continue;

      const stepCost = calculateDistance(
        { lat: current.lat, lon: current.lon },
        { lat: next.lat, lon: next.lon },
      );
      const unknownPenalty =
        next.depthM == null || !Number.isFinite(next.depthM)
          ? 0.7
          : 0;
      const shallowPenalty =
        next.depthM != null && next.depthM < draftMeters + 0.35
          ? 5
          : next.depthM != null && next.depthM < draftMeters + 0.9
            ? 1.8
            : 0;
      const shorePenalty = countBlockedNeighbors(field, next) * 0.09;
      const tentative =
        (gScore.get(currentKey) ?? Number.POSITIVE_INFINITY) +
        stepCost +
        unknownPenalty +
        shallowPenalty +
        shorePenalty;

      if (tentative < (gScore.get(next.key) ?? Number.POSITIVE_INFINITY)) {
        cameFrom.set(next.key, currentKey);
        gScore.set(next.key, tentative);
        fScore.set(
          next.key,
          tentative +
            calculateDistance(
              { lat: next.lat, lon: next.lon },
              { lat: endCell.lat, lon: endCell.lon },
            ),
        );
        open.add(next.key);
      }
    }
  }

  return null;
}

export function routeWaypointsThroughNavigationField(
  field: NavigationField,
  waypoints: LatLng[],
  draftMeters: number,
): LatLng[] | null {
  if (waypoints.length < 2) return waypoints;

  const routed: LatLng[] = [waypoints[0]];
  for (let index = 0; index < waypoints.length - 1; index += 1) {
    const start = routed[routed.length - 1];
    const end = waypoints[index + 1];
    const leg = routeLegThroughField(field, start, end, draftMeters);
    if (!leg || leg.length < 2) {
      return null;
    }
    for (const point of leg.slice(1)) {
      routed.push(point);
    }
  }

  return dedupePath(routed);
}

function interpolateSegment(
  from: LatLng,
  to: LatLng,
  intervalNm: number,
): LatLng[] {
  const distanceNm = calculateDistance(
    { lat: from.lat, lon: from.lon },
    { lat: to.lat, lon: to.lon },
  );
  const steps = Math.max(Math.ceil(distanceNm / Math.max(intervalNm, 0.05)), 1);
  const points: LatLng[] = [];

  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps;
    points.push({
      lat: from.lat + (to.lat - from.lat) * t,
      lon: from.lon + (to.lon - from.lon) * t,
    });
  }

  return points;
}

export function evaluateRouteAgainstNavigationField(
  segments: Array<{ from: LatLng; to: LatLng }>,
  field: NavigationField,
  draftMeters: number,
): { segmentDepths: Array<number | null>; warnings: NavigationFieldWarning[] } {
  const warnings: NavigationFieldWarning[] = [];
  const segmentDepths: Array<number | null> = [];
  const intervalNm = clamp(field.cellSpacingNm * 0.8, 0.06, 0.2);

  segments.forEach((segment, segmentIndex) => {
    let minDepth: number | null = null;
    const points = interpolateSegment(segment.from, segment.to, intervalNm);

    for (const point of points) {
      const probe = probeNavigationField(field, point);
      if (!probe.onWater) {
        minDepth = minDepth == null ? 0 : Math.min(minDepth, 0);
        continue;
      }
      if (typeof probe.depthM === 'number' && Number.isFinite(probe.depthM)) {
        minDepth = minDepth == null ? probe.depthM : Math.min(minDepth, probe.depthM);
      }
    }

    segmentDepths.push(minDepth);

    if (minDepth != null && minDepth < draftMeters + 0.8) {
      warnings.push({
        position: {
          lat: (segment.from.lat + segment.to.lat) / 2,
          lon: (segment.from.lon + segment.to.lon) / 2,
        },
        depthM: minDepth,
        draftM: draftMeters,
        segmentIndex,
      });
    }
  });

  return { segmentDepths, warnings };
}

type NavigationZone = 'safe' | 'caution' | 'danger' | 'unknown' | 'land';

function getZoneForCell(
  cell: NavigationFieldCell,
  draftMeters: number,
): NavigationZone {
  if (!cell.valid) return 'land';
  if (!cell.onWater) return 'land';
  if (cell.depthM == null || !Number.isFinite(cell.depthM)) return 'unknown';
  if (cell.depthM < draftMeters) return 'danger';
  if (cell.depthM < draftMeters + 0.8) return 'caution';
  return 'safe';
}

const ZONE_STYLE: Record<NavigationZone, { color: string; opacity: number }> = {
  safe: { color: '#2D8F73', opacity: 0.16 },
  caution: { color: '#D17E2A', opacity: 0.24 },
  danger: { color: '#C44B4B', opacity: 0.34 },
  unknown: { color: '#72849A', opacity: 0.11 },
  land: { color: '#7F2E2E', opacity: 0.08 },
};

export function navigationFieldToGeoJSON(
  field: NavigationField,
  draftMeters: number,
  options?: {
    includeSafe?: boolean;
    includeLand?: boolean;
  },
): GeoJSON.FeatureCollection | null {
  const includeSafe = options?.includeSafe ?? true;
  const includeLand = options?.includeLand ?? false;
  const radiusPx = Math.max(
    8,
    Math.min(26, Math.min(field.cellWidthPx, field.cellHeightPx) * 0.72),
  );

  const features = field.cells
    .filter((cell) => cell.valid)
    .map((cell) => {
      const zone = getZoneForCell(cell, draftMeters);
      if (!includeSafe && zone === 'safe') return null;
      if (!includeLand && zone === 'land') return null;

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [cell.lon, cell.lat],
        },
        properties: {
          zone,
          color: ZONE_STYLE[zone].color,
          opacity: ZONE_STYLE[zone].opacity,
          radiusPx,
          label:
            zone === 'danger'
              ? 'NO GO'
              : zone === 'caution'
                ? 'TIGHT'
                : zone === 'safe'
                  ? 'GO'
                  : '',
        },
      };
    })
    .filter(Boolean) as GeoJSON.Feature[];

  if (features.length === 0) return null;
  return {
    type: 'FeatureCollection',
    features,
  };
}
