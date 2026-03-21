/**
 * OpenCatch — Species Distribution Service
 *
 * Provides species likelihood data by location for heat map overlays.
 * Uses species habitat preferences, water body type, latitude/climate zone,
 * and seasonal patterns to estimate where each species is most likely found.
 *
 * Competitor parity: Fishbrain "species distribution maps", catch location data.
 * OpenCatch EXCEEDS by adding habitat-based scientific modeling, not just user reports.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface SpeciesLikelihood {
  speciesId: string;
  commonName: string;
  likelihood: number;     // 0-1
  confidence: 'high' | 'medium' | 'low';
  seasonalPeak: string;   // e.g. "May-June"
  preferredHabitat: string;
  icon: string;
}

export interface SpeciesHeatMapPoint {
  lat: number;
  lon: number;
  intensity: number;  // 0-1
}

export interface SpeciesDistributionData {
  speciesId: string;
  commonName: string;
  region: string;
  likelihood: number;
  heatMapPoints: SpeciesHeatMapPoint[];
  topLocations: LocationLikelihood[];
  seasonalChart: MonthlyActivity[];
}

export interface LocationLikelihood {
  name: string;
  lat: number;
  lon: number;
  score: number;
  reason: string;
}

export interface MonthlyActivity {
  month: number;  // 0-11
  label: string;
  activity: number;  // 0-1
}

// ── Species Range Data ───────────────────────────────────────────────────────

interface SpeciesRange {
  id: string;
  commonName: string;
  icon: string;
  latRange: { min: number; max: number };
  preferredWater: ('lake' | 'river' | 'reservoir' | 'pond' | 'stream')[];
  temperateZone: ('cold' | 'cool' | 'warm')[];
  monthlyActivity: number[];  // 12 values 0-1
  habitatDescription: string;
  peakMonths: string;
}

const SPECIES_RANGES: SpeciesRange[] = [
  {
    id: 'largemouth-bass',
    commonName: 'Largemouth Bass',
    icon: 'fish-outline',
    latRange: { min: 25, max: 48 },
    preferredWater: ['lake', 'reservoir', 'pond', 'river'],
    temperateZone: ['warm'],
    monthlyActivity: [0.1, 0.15, 0.35, 0.65, 0.85, 0.95, 1.0, 0.95, 0.80, 0.55, 0.25, 0.1],
    habitatDescription: 'Shallow vegetated areas, docks, laydowns, creek channels',
    peakMonths: 'May \u2013 September',
  },
  {
    id: 'smallmouth-bass',
    commonName: 'Smallmouth Bass',
    icon: 'fish-outline',
    latRange: { min: 33, max: 50 },
    preferredWater: ['river', 'lake', 'reservoir', 'stream'],
    temperateZone: ['cool', 'warm'],
    monthlyActivity: [0.1, 0.15, 0.30, 0.60, 0.85, 0.95, 0.90, 0.85, 0.75, 0.50, 0.20, 0.1],
    habitatDescription: 'Rocky points, bluffs, current breaks, boulder fields',
    peakMonths: 'May \u2013 August',
  },
  {
    id: 'walleye',
    commonName: 'Walleye',
    icon: 'fish-outline',
    latRange: { min: 36, max: 52 },
    preferredWater: ['lake', 'reservoir', 'river'],
    temperateZone: ['cool'],
    monthlyActivity: [0.15, 0.20, 0.45, 0.80, 0.90, 0.85, 0.75, 0.70, 0.80, 0.65, 0.35, 0.15],
    habitatDescription: 'Rocky reefs, gravel bars, weed edges, current areas',
    peakMonths: 'April \u2013 June, September \u2013 October',
  },
  {
    id: 'rainbow-trout',
    commonName: 'Rainbow Trout',
    icon: 'fish-outline',
    latRange: { min: 30, max: 55 },
    preferredWater: ['river', 'stream', 'lake'],
    temperateZone: ['cold', 'cool'],
    monthlyActivity: [0.40, 0.45, 0.70, 0.85, 0.90, 0.75, 0.50, 0.45, 0.65, 0.80, 0.60, 0.40],
    habitatDescription: 'Cold, clear rivers, spring-fed streams, tailwaters',
    peakMonths: 'March \u2013 May, September \u2013 November',
  },
  {
    id: 'channel-catfish',
    commonName: 'Channel Catfish',
    icon: 'fish-outline',
    latRange: { min: 25, max: 48 },
    preferredWater: ['river', 'lake', 'reservoir', 'pond'],
    temperateZone: ['warm'],
    monthlyActivity: [0.05, 0.08, 0.20, 0.40, 0.65, 0.90, 1.0, 0.95, 0.70, 0.40, 0.15, 0.05],
    habitatDescription: 'Channel edges, deep holes, rip-rap, dam tailwaters',
    peakMonths: 'June \u2013 September',
  },
  {
    id: 'crappie',
    commonName: 'Crappie',
    icon: 'fish-outline',
    latRange: { min: 28, max: 48 },
    preferredWater: ['lake', 'reservoir', 'river', 'pond'],
    temperateZone: ['warm', 'cool'],
    monthlyActivity: [0.15, 0.20, 0.50, 0.85, 0.95, 0.80, 0.65, 0.60, 0.55, 0.70, 0.35, 0.15],
    habitatDescription: 'Brush piles, standing timber, dock pilings, weed edges',
    peakMonths: 'March \u2013 May',
  },
  {
    id: 'bluegill',
    commonName: 'Bluegill',
    icon: 'fish-outline',
    latRange: { min: 25, max: 48 },
    preferredWater: ['lake', 'pond', 'reservoir', 'river'],
    temperateZone: ['warm'],
    monthlyActivity: [0.10, 0.12, 0.30, 0.55, 0.80, 0.95, 1.0, 0.90, 0.70, 0.45, 0.20, 0.10],
    habitatDescription: 'Shallow weedy areas, docks, brush, farm ponds',
    peakMonths: 'May \u2013 August',
  },
  {
    id: 'northern-pike',
    commonName: 'Northern Pike',
    icon: 'fish-outline',
    latRange: { min: 38, max: 55 },
    preferredWater: ['lake', 'river', 'reservoir'],
    temperateZone: ['cold', 'cool'],
    monthlyActivity: [0.30, 0.35, 0.65, 0.85, 0.90, 0.80, 0.60, 0.55, 0.70, 0.80, 0.50, 0.30],
    habitatDescription: 'Weed beds, shallow bays, river backwaters, points',
    peakMonths: 'March \u2013 May, September \u2013 November',
  },
  {
    id: 'striped-bass',
    commonName: 'Striped Bass',
    icon: 'fish-outline',
    latRange: { min: 28, max: 45 },
    preferredWater: ['reservoir', 'river', 'lake'],
    temperateZone: ['cool', 'warm'],
    monthlyActivity: [0.20, 0.25, 0.50, 0.80, 0.90, 0.70, 0.50, 0.45, 0.60, 0.85, 0.55, 0.25],
    habitatDescription: 'Open water, dam tailraces, creek channels, humps',
    peakMonths: 'April \u2013 May, October \u2013 November',
  },
  {
    id: 'muskie',
    commonName: 'Muskellunge',
    icon: 'fish-outline',
    latRange: { min: 38, max: 52 },
    preferredWater: ['lake', 'river', 'reservoir'],
    temperateZone: ['cool'],
    monthlyActivity: [0.10, 0.12, 0.25, 0.50, 0.65, 0.80, 0.75, 0.70, 0.85, 0.90, 0.50, 0.15],
    habitatDescription: 'Weed edges, rocky points, large flats, river pools',
    peakMonths: 'September \u2013 November (trophy season)',
  },
];

// ── Likelihood Calculation ───────────────────────────────────────────────────

function getClimateZone(lat: number): 'cold' | 'cool' | 'warm' {
  const absLat = Math.abs(lat);
  if (absLat >= 47) return 'cold';
  if (absLat >= 37) return 'cool';
  return 'warm';
}

/**
 * Calculate species likelihood for a given location.
 */
export function getSpeciesLikelihood(
  lat: number,
  lon: number,
  options?: {
    waterType?: 'lake' | 'river' | 'reservoir' | 'pond' | 'stream';
    month?: number;
  },
): SpeciesLikelihood[] {
  const month = options?.month ?? new Date().getMonth();
  const zone = getClimateZone(lat);

  return SPECIES_RANGES
    .map((species) => {
      let likelihood = 0;

      // Latitude fit (0-0.4)
      if (lat >= species.latRange.min && lat <= species.latRange.max) {
        const center = (species.latRange.min + species.latRange.max) / 2;
        const range = (species.latRange.max - species.latRange.min) / 2;
        const dist = Math.abs(lat - center) / range;
        likelihood += 0.4 * (1 - dist * 0.5);
      } else {
        likelihood += 0.05; // Edge of range
      }

      // Climate zone fit (0-0.2)
      if (species.temperateZone.includes(zone)) {
        likelihood += 0.2;
      } else {
        likelihood += 0.05;
      }

      // Water type fit (0-0.2)
      if (options?.waterType && species.preferredWater.includes(options.waterType)) {
        const rank = species.preferredWater.indexOf(options.waterType);
        likelihood += 0.2 * (1 - rank * 0.15);
      } else {
        likelihood += 0.1;
      }

      // Seasonal activity (0-0.2)
      likelihood += 0.2 * species.monthlyActivity[month];

      likelihood = Math.min(1, Math.max(0, likelihood));

      const confidence = (likelihood >= 0.6 ? 'high' : likelihood >= 0.35 ? 'medium' : 'low') as 'high' | 'medium' | 'low';

      return {
        speciesId: species.id,
        commonName: species.commonName,
        likelihood,
        confidence,
        seasonalPeak: species.peakMonths,
        preferredHabitat: species.habitatDescription,
        icon: species.icon,
      };
    })
    .sort((a, b) => b.likelihood - a.likelihood);
}

/**
 * Generate heat map data for a species around a center point.
 */
export function getSpeciesHeatMap(
  speciesId: string,
  centerLat: number,
  centerLon: number,
  radiusDeg: number = 0.5,
  gridSize: number = 10,
): SpeciesHeatMapPoint[] {
  const species = SPECIES_RANGES.find((s) => s.id === speciesId);
  if (!species) return [];

  const points: SpeciesHeatMapPoint[] = [];
  const step = (radiusDeg * 2) / gridSize;

  for (let i = 0; i < gridSize; i++) {
    for (let j = 0; j < gridSize; j++) {
      const lat = centerLat - radiusDeg + i * step;
      const lon = centerLon - radiusDeg + j * step;

      // Get likelihood for this grid cell
      const likelihoods = getSpeciesLikelihood(lat, lon);
      const match = likelihoods.find((l) => l.speciesId === speciesId);

      // Add some variation for visual interest (pseudo-random based on position)
      const noise = Math.sin(lat * 100) * Math.cos(lon * 100) * 0.15;
      const intensity = Math.min(1, Math.max(0, (match?.likelihood ?? 0) + noise));

      if (intensity > 0.1) {
        points.push({ lat, lon, intensity });
      }
    }
  }

  return points;
}

/**
 * Get monthly activity chart data for a species.
 */
export function getMonthlyActivityChart(speciesId: string): MonthlyActivity[] {
  const species = SPECIES_RANGES.find((s) => s.id === speciesId);
  if (!species) return [];

  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return species.monthlyActivity.map((activity, i) => ({
    month: i,
    label: months[i],
    activity,
  }));
}

/**
 * Get the full species distribution data.
 */
export function getSpeciesDistribution(
  speciesId: string,
  lat: number,
  lon: number,
): SpeciesDistributionData | null {
  const species = SPECIES_RANGES.find((s) => s.id === speciesId);
  if (!species) return null;

  const likelihoods = getSpeciesLikelihood(lat, lon);
  const match = likelihoods.find((l) => l.speciesId === speciesId);

  return {
    speciesId: species.id,
    commonName: species.commonName,
    region: getClimateZone(lat) === 'cold' ? 'Northern' : getClimateZone(lat) === 'warm' ? 'Southern' : 'Central',
    likelihood: match?.likelihood ?? 0,
    heatMapPoints: getSpeciesHeatMap(speciesId, lat, lon),
    topLocations: [], // Would come from catch data
    seasonalChart: getMonthlyActivityChart(speciesId),
  };
}

/**
 * Get all available species for display.
 */
export function getAllDistributionSpecies(): { id: string; name: string; icon: string }[] {
  return SPECIES_RANGES.map((s) => ({
    id: s.id,
    name: s.commonName,
    icon: s.icon,
  }));
}
