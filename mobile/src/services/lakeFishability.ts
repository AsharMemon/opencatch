import { getDailyBiteForecast } from './bestTimeWindows';
import { getCurrentPressure } from './fishingPressure';
import { getSpeciesLikelihood } from './speciesDistribution';

export interface FishabilityProbe {
  id: string;
  lat: number;
  lon: number;
  row: number;
  col: number;
  depthFt: number | null;
}

export interface FishabilityReferenceSpot {
  id: string;
  lat: number;
  lon: number;
  name: string;
}

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function fishabilityColor(score: number): string {
  if (score >= 82) return '#C9884C';
  if (score >= 68) return '#E2B875';
  if (score >= 54) return '#7FAE9D';
  if (score >= 42) return '#99C3C8';
  if (score >= 30) return '#B5CAD9';
  if (score >= 18) return '#CCD8E2';
  return '#E2EAF0';
}

function fishabilityLabel(score: number): string {
  if (score >= 80) return 'Prime';
  if (score >= 65) return 'Good';
  if (score >= 50) return 'Fair';
  if (score >= 35) return 'Slow';
  return 'Cold';
}

const SPECIES_DEPTH_PREFS: Record<string, { min: number; max: number }> = {
  'largemouth-bass': { min: 4, max: 12 },
  'smallmouth-bass': { min: 8, max: 20 },
  'walleye': { min: 10, max: 26 },
  'rainbow-trout': { min: 14, max: 32 },
  'channel-catfish': { min: 12, max: 28 },
  'crappie': { min: 8, max: 16 },
  'bluegill': { min: 3, max: 10 },
  'northern-pike': { min: 5, max: 15 },
  'striped-bass': { min: 16, max: 34 },
  'muskie': { min: 7, max: 18 },
};

function getPreferredDepthRange(centerLat: number, centerLon: number, hour: number) {
  const topSpecies = getSpeciesLikelihood(centerLat, centerLon, { waterType: 'lake' }).slice(0, 6);
  let weightedMin = 0;
  let weightedMax = 0;
  let totalWeight = 0;

  for (const species of topSpecies) {
    const pref = SPECIES_DEPTH_PREFS[species.speciesId];
    if (!pref) continue;
    const weight = clamp(species.likelihood, 0.15, 1);
    weightedMin += pref.min * weight;
    weightedMax += pref.max * weight;
    totalWeight += weight;
  }

  let min = totalWeight > 0 ? weightedMin / totalWeight : 6;
  let max = totalWeight > 0 ? weightedMax / totalWeight : 18;

  if (hour <= 8 || hour >= 18) {
    min = Math.max(2, min - 3);
    max = Math.max(min + 3, max - 2);
  } else if (hour >= 11 && hour <= 15) {
    min += 4;
    max += 6;
  }

  return { min, max };
}

function depthSuitability(depthFt: number | null, minDepthFt: number, maxDepthFt: number): number {
  if (depthFt == null || !Number.isFinite(depthFt)) return 0.52;
  if (depthFt < 1.5) return 0.08;

  const mid = (minDepthFt + maxDepthFt) / 2;
  const halfSpan = Math.max(3, (maxDepthFt - minDepthFt) / 2);
  const dist = Math.abs(depthFt - mid);
  return clamp(1 - dist / (halfSpan * 1.7), 0.08, 1);
}

function computeStructureScores(probes: FishabilityProbe[]): Map<string, number> {
  const map = new Map<string, FishabilityProbe>();
  const structure = new Map<string, number>();

  for (const probe of probes) {
    map.set(`${probe.row}:${probe.col}`, probe);
  }

  for (const probe of probes) {
    const neighbors = [
      map.get(`${probe.row - 1}:${probe.col}`),
      map.get(`${probe.row + 1}:${probe.col}`),
      map.get(`${probe.row}:${probe.col - 1}`),
      map.get(`${probe.row}:${probe.col + 1}`),
    ].filter(Boolean) as FishabilityProbe[];

    if (probe.depthFt == null || neighbors.length === 0) {
      structure.set(probe.id, 0.25);
      continue;
    }

    const deltas = neighbors
      .filter((neighbor) => neighbor.depthFt != null)
      .map((neighbor) => Math.abs((neighbor.depthFt as number) - probe.depthFt!));

    if (deltas.length === 0) {
      structure.set(probe.id, 0.25);
      continue;
    }

    const avgDelta = deltas.reduce((sum, value) => sum + value, 0) / deltas.length;
    structure.set(probe.id, clamp(avgDelta / 10, 0, 1));
  }

  return structure;
}

function computeSpotAffinity(
  probe: FishabilityProbe,
  referenceSpots: FishabilityReferenceSpot[],
): number {
  if (referenceSpots.length === 0) return 0.2;

  let best = 0;
  for (const spot of referenceSpots) {
    const distKm = haversineKm(probe.lat, probe.lon, spot.lat, spot.lon);
    if (distKm > 4.5) continue;
    const proximity = clamp(1 - distKm / 4.5, 0, 1);
    if (proximity > best) best = proximity;
  }
  return Math.max(best, 0.15);
}

export function buildLakeFishabilityGeoJSON(
  probes: FishabilityProbe[],
  options: {
    centerLat: number;
    centerLon: number;
    referenceSpots?: FishabilityReferenceSpot[];
  },
): GeoJSON.FeatureCollection {
  const hour = new Date().getHours();
  const depthPref = getPreferredDepthRange(options.centerLat, options.centerLon, hour);
  const structureScores = computeStructureScores(probes);
  const referenceSpots = options.referenceSpots ?? [];

  return {
    type: 'FeatureCollection',
    features: probes.map((probe) => {
      const bite = getDailyBiteForecast(probe.lat, probe.lon);
      const baseScore = bite.hourlyScores[hour] ?? bite.overallRating;
      const pressure = getCurrentPressure({ lat: probe.lat, lon: probe.lon });
      const depthScore = depthSuitability(probe.depthFt, depthPref.min, depthPref.max);
      const structureScore = structureScores.get(probe.id) ?? 0.25;
      const spotAffinity = computeSpotAffinity(probe, referenceSpots);

      let score =
        baseScore * 0.56 +
        depthScore * 20 +
        structureScore * 15 +
        spotAffinity * 12 -
        pressure.score * 0.12;

      if (probe.depthFt != null && probe.depthFt > 45) {
        score -= 6;
      }

      score = Math.round(clamp(score, 5, 99));
      const color = fishabilityColor(score);
      const label = fishabilityLabel(score);

      return {
        type: 'Feature' as const,
        id: `fish-zone-${probe.id}`,
        geometry: {
          type: 'Point' as const,
          coordinates: [probe.lon, probe.lat],
        },
        properties: {
          score,
          color,
          label,
          scoreText: `${score}`,
          depthText: probe.depthFt != null ? `${Math.round(probe.depthFt)} ft` : '',
          structureScore,
          depthFit: depthScore,
          spotAffinity,
          overlayMode: 'field',
          isHot: score >= 72 ? 1 : 0,
          isGood: score >= 50 ? 1 : 0,
        },
      };
    }),
  };
}
