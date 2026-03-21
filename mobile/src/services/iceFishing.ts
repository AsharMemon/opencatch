/**
 * Ice Fishing Service for OpenCatch.
 *
 * Provides ice thickness estimation, safety ratings, ice-out date prediction,
 * and ice fishing spot discovery. Uses Open-Meteo historical temperature data
 * to calculate accumulated Freezing Degree Days (FDD) which correlate with
 * ice growth following the Stefan equation.
 *
 * Reference: Stefan equation — ice thickness ~ alpha * sqrt(FDD)
 * where alpha is typically 0.8-1.0 for snow-free ice.
 *
 * Open-Meteo historical API: https://open-meteo.com/en/docs/historical-weather-api
 */

// ── Types ────────────────────────────────────────────────────────

/** Ice safety rating based on thickness. */
export type IceSafetyRating = 'unsafe' | 'caution' | 'walk' | 'snowmobile' | 'car' | 'truck';

/** Current ice condition assessment. */
export interface IceCondition {
  /** Estimated ice thickness in inches. */
  thicknessInches: number;
  /** Safety rating based on thickness. */
  safetyRating: IceSafetyRating;
  /** Human-readable safety label. */
  safetyLabel: string;
  /** Color for the safety badge. */
  safetyColor: string;
  /** Accumulated Freezing Degree Days (Fahrenheit). */
  freezingDegreeDays: number;
  /** Number of consecutive days below freezing. */
  consecutiveFreezingDays: number;
  /** Whether ice is currently growing, stable, or deteriorating. */
  trend: 'growing' | 'stable' | 'deteriorating';
  /** Average temperature over the last 7 days (F). */
  recentAvgTempF: number;
  /** Safety guidelines text. */
  safetyGuidelines: string;
  /** Detailed description of conditions. */
  description: string;
}

/** Ice fishing spot. */
export interface IceFishingSpot {
  /** Location ID. */
  id: string;
  /** Spot name. */
  name: string;
  /** Latitude. */
  lat: number;
  /** Longitude. */
  lon: number;
  /** Water body type (should be lake/reservoir/pond). */
  waterBodyType: string;
  /** Estimated ice condition. */
  condition: IceCondition;
}

/** Ice-out prediction. */
export interface IceOutPrediction {
  /** Estimated ice-out date (ISO 8601). */
  estimatedDate: string;
  /** Days from now until estimated ice-out. */
  daysUntilIceOut: number;
  /** Confidence level. */
  confidence: 'high' | 'moderate' | 'low';
  /** Description of the prediction. */
  description: string;
}

/** Winter species tip. */
export interface WinterSpeciesTip {
  species: string;
  /** Ionicon name for display. */
  icon: string;
  bestDepthFt: string;
  bestTime: string;
  bait: string[];
  technique: string;
}

/** Equipment checklist item. */
export interface IceEquipmentItem {
  name: string;
  category: 'essential' | 'recommended' | 'optional';
  checked: boolean;
  description: string;
}

// ── Constants ────────────────────────────────────────────────────

const OPEN_METEO_HISTORICAL = 'https://archive-api.open-meteo.com/v1/archive';
const OPEN_METEO_FORECAST = 'https://api.open-meteo.com/v1/forecast';
const REQUEST_TIMEOUT_MS = 15_000;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour — ice conditions change slowly

/**
 * Stefan equation alpha coefficient.
 * 0.8 for snow-covered ice, 1.0 for clear ice.
 * We use 0.85 as a conservative middle ground.
 */
const STEFAN_ALPHA = 0.85;

/** Ice thickness safety thresholds (inches). */
const SAFETY_THRESHOLDS = {
  walk: 4,        // Walking, ice fishing
  snowmobile: 5,  // Snowmobile or ATV
  car: 8,         // Small car (not recommended on most bodies)
  truck: 12,      // Medium truck
} as const;

// ── Safety colors ────────────────────────────────────────────────

const SAFETY_CONFIG: Record<IceSafetyRating, { label: string; color: string }> = {
  unsafe: { label: 'Unsafe', color: '#D50000' },
  caution: { label: 'Caution', color: '#FF6D00' },
  walk: { label: 'Safe — Walking', color: '#2E7D32' },
  snowmobile: { label: 'Safe — Snowmobile', color: '#1B5E20' },
  car: { label: 'Safe — Light Vehicle', color: '#0D47A1' },
  truck: { label: 'Safe — Heavy Vehicle', color: '#1A237E' },
};

// ── Cache ────────────────────────────────────────────────────────

const cache = new Map<string, { data: any; timestamp: number }>();

function getCached<T>(key: string): T | null {
  const entry = cache.get(key);
  if (!entry || Date.now() - entry.timestamp > CACHE_TTL_MS) {
    if (entry) cache.delete(key);
    return null;
  }
  return entry.data;
}

function setCache<T>(key: string, data: T): void {
  cache.set(key, { data, timestamp: Date.now() });
}

// ── HTTP helper ──────────────────────────────────────────────────

async function fetchJSON<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`Open-Meteo API ${response.status}: ${url}`);
    }
    return response.json() as Promise<T>;
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new Error(`Open-Meteo API timed out after ${REQUEST_TIMEOUT_MS}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── Internal helpers ─────────────────────────────────────────────

/**
 * Classify ice safety based on thickness.
 */
export function isIceSafe(thicknessInches: number): IceSafetyRating {
  if (thicknessInches >= SAFETY_THRESHOLDS.truck) return 'truck';
  if (thicknessInches >= SAFETY_THRESHOLDS.car) return 'car';
  if (thicknessInches >= SAFETY_THRESHOLDS.snowmobile) return 'snowmobile';
  if (thicknessInches >= SAFETY_THRESHOLDS.walk) return 'walk';
  if (thicknessInches >= 2) return 'caution';
  return 'unsafe';
}

function getSafetyGuidelines(rating: IceSafetyRating): string {
  switch (rating) {
    case 'unsafe':
      return 'Ice is too thin for any activity. Stay off the ice. Even ice that looks solid can be dangerously thin in spots.';
    case 'caution':
      return 'Ice is forming but not yet safe for walking. Check thickness in multiple spots before venturing out. Stay near shore.';
    case 'walk':
      return '4+ inches: Safe for walking and ice fishing. Always check thickness as you move. Carry ice picks and wear a float suit.';
    case 'snowmobile':
      return '5+ inches: Safe for snowmobiles and ATVs. Spread out weight and avoid driving in groups. Check for pressure cracks.';
    case 'car':
      return '8-12 inches: Can support a small vehicle. Use extreme caution. Check local regulations — many lakes prohibit vehicles on ice.';
    case 'truck':
      return '12+ inches: Can support a medium truck. Always check for springs, currents, and recent temperature swings that weaken ice.';
  }
}

function getConditionDescription(
  thicknessInches: number,
  trend: 'growing' | 'stable' | 'deteriorating',
  recentAvgF: number,
): string {
  const trendText =
    trend === 'growing'
      ? 'Ice is actively forming with cold temperatures'
      : trend === 'stable'
        ? 'Ice thickness is relatively stable'
        : 'Warming temperatures are weakening the ice';

  return `Estimated ${thicknessInches.toFixed(1)}" of ice. ${trendText}. Recent average temperature: ${Math.round(recentAvgF)}°F. Always verify thickness with a spud bar or auger — estimates are based on air temperature models and actual conditions vary by location, current, depth, and snow cover.`;
}

/**
 * Calculate Freezing Degree Days from daily temperature data.
 * FDD = sum of (32 - mean_daily_temp) for days where mean < 32°F.
 */
function calculateFDD(dailyMeanTempsF: number[]): {
  fdd: number;
  consecutiveFreezingDays: number;
  recentAvgF: number;
  trend: 'growing' | 'stable' | 'deteriorating';
} {
  let fdd = 0;
  let consecutiveFreezingDays = 0;
  let currentStreak = 0;

  for (const temp of dailyMeanTempsF) {
    if (temp < 32) {
      fdd += 32 - temp;
      currentStreak++;
    } else {
      // Thawing days reduce FDD somewhat (not a perfect reset)
      fdd = Math.max(0, fdd - (temp - 32) * 0.5);
      currentStreak = 0;
    }
    consecutiveFreezingDays = Math.max(consecutiveFreezingDays, currentStreak);
  }

  // Recent average (last 7 days)
  const recent = dailyMeanTempsF.slice(-7);
  const recentAvgF = recent.length > 0 ? recent.reduce((a, b) => a + b, 0) / recent.length : 32;

  // Trend based on last 3 days
  const last3 = dailyMeanTempsF.slice(-3);
  const last3Avg = last3.length > 0 ? last3.reduce((a, b) => a + b, 0) / last3.length : 32;

  let trend: 'growing' | 'stable' | 'deteriorating';
  if (last3Avg < 25) trend = 'growing';
  else if (last3Avg > 35) trend = 'deteriorating';
  else trend = 'stable';

  return { fdd, consecutiveFreezingDays, recentAvgF, trend };
}

/**
 * Celsius to Fahrenheit.
 */
function cToF(celsius: number): number {
  return celsius * 9 / 5 + 32;
}

// ── Open-Meteo response shapes ───────────────────────────────────

interface OMHistoricalResponse {
  daily: {
    time: string[];
    temperature_2m_mean?: number[];
    temperature_2m_max?: number[];
    temperature_2m_min?: number[];
  };
}

// ── Public API ───────────────────────────────────────────────────

/**
 * Get estimated ice thickness and conditions for a location.
 *
 * Uses 90 days of historical temperature data to calculate accumulated
 * Freezing Degree Days and apply the Stefan equation for ice growth.
 *
 * @param lat - Latitude (decimal degrees).
 * @param lon - Longitude (decimal degrees).
 * @returns Ice condition assessment.
 */
export async function getIceThickness(lat: number, lon: number): Promise<IceCondition> {
  const cacheKey = `ice:${lat.toFixed(3)},${lon.toFixed(3)}`;
  const cached = getCached<IceCondition>(cacheKey);
  if (cached) return cached;

  try {
    // Fetch 90 days of historical temperature data
    const endDate = new Date();
    const startDate = new Date();
    startDate.setDate(endDate.getDate() - 90);

    const params = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      start_date: startDate.toISOString().split('T')[0],
      end_date: endDate.toISOString().split('T')[0],
      daily: 'temperature_2m_mean,temperature_2m_max,temperature_2m_min',
      temperature_unit: 'fahrenheit',
      timezone: 'auto',
    });

    const data = await fetchJSON<OMHistoricalResponse>(
      `${OPEN_METEO_HISTORICAL}?${params}`,
    );

    const dailyMeans = data.daily?.temperature_2m_mean ?? [];
    if (dailyMeans.length === 0) {
      throw new Error('No temperature data available');
    }

    const { fdd, consecutiveFreezingDays, recentAvgF, trend } = calculateFDD(dailyMeans);

    // Stefan equation: thickness = alpha * sqrt(FDD)
    const thicknessInches = fdd > 0 ? STEFAN_ALPHA * Math.sqrt(fdd) : 0;
    const rating = isIceSafe(thicknessInches);
    const config = SAFETY_CONFIG[rating];

    const condition: IceCondition = {
      thicknessInches: Math.round(thicknessInches * 10) / 10,
      safetyRating: rating,
      safetyLabel: config.label,
      safetyColor: config.color,
      freezingDegreeDays: Math.round(fdd),
      consecutiveFreezingDays,
      trend,
      recentAvgTempF: Math.round(recentAvgF * 10) / 10,
      safetyGuidelines: getSafetyGuidelines(rating),
      description: getConditionDescription(thicknessInches, trend, recentAvgF),
    };

    setCache(cacheKey, condition);
    return condition;
  } catch (err) {
    console.warn('[OpenCatch] Failed to estimate ice thickness:', err);

    // Return a safe default — assume unsafe
    return {
      thicknessInches: 0,
      safetyRating: 'unsafe',
      safetyLabel: 'Unknown',
      safetyColor: '#D50000',
      freezingDegreeDays: 0,
      consecutiveFreezingDays: 0,
      trend: 'deteriorating',
      recentAvgTempF: NaN,
      safetyGuidelines: 'Unable to estimate ice conditions. Always verify ice thickness before venturing out.',
      description: 'Ice condition data is unavailable. Do not rely on estimates — check actual ice thickness with an auger or spud bar.',
    };
  }
}

/**
 * Get estimated ice-out date for a location based on climate normals.
 *
 * Uses latitude as the primary predictor: higher latitudes have later ice-out.
 * This is a rough estimate — actual ice-out depends on many factors.
 *
 * @param lat - Latitude.
 * @param lon - Longitude.
 * @returns Ice-out prediction.
 */
export async function getIceOutDate(lat: number, lon: number): Promise<IceOutPrediction> {
  // Empirical ice-out dates by latitude band (for North America):
  // 40-42°N: Late February to mid-March
  // 42-44°N: Mid-March to early April
  // 44-46°N: Late March to mid-April
  // 46-48°N: Mid-April to early May
  // 48-50°N: Late April to mid-May
  // 50-55°N: May to early June
  // 55-60°N: Late May to June
  // 60+°N: June to July

  const currentYear = new Date().getFullYear();
  let iceOutMonth: number;
  let iceOutDay: number;
  let confidence: 'high' | 'moderate' | 'low';

  if (lat < 40) {
    // Too far south for reliable ice
    return {
      estimatedDate: '',
      daysUntilIceOut: 0,
      confidence: 'low',
      description: 'This location is typically too far south for sustained lake ice formation.',
    };
  } else if (lat < 42) {
    iceOutMonth = 2; iceOutDay = 28; confidence = 'low';
  } else if (lat < 44) {
    iceOutMonth = 3; iceOutDay = 20; confidence = 'moderate';
  } else if (lat < 46) {
    iceOutMonth = 3; iceOutDay = 31; confidence = 'moderate';
  } else if (lat < 48) {
    iceOutMonth = 4; iceOutDay = 15; confidence = 'moderate';
  } else if (lat < 50) {
    iceOutMonth = 4; iceOutDay = 28; confidence = 'moderate';
  } else if (lat < 55) {
    iceOutMonth = 5; iceOutDay = 15; confidence = 'low';
  } else if (lat < 60) {
    iceOutMonth = 5; iceOutDay = 31; confidence = 'low';
  } else {
    iceOutMonth = 6; iceOutDay = 20; confidence = 'low';
  }

  // Adjust for longitude (continental vs. maritime climate)
  // Interior locations (around -90 to -100) tend to be later
  if (lon > -80 && lon < -60) {
    // Maritime east — slightly earlier
    iceOutDay -= 5;
  } else if (lon < -100) {
    // Interior west — slightly later
    iceOutDay += 5;
  }

  const estimatedDate = new Date(currentYear, iceOutMonth - 1, iceOutDay);
  const now = new Date();
  const diffMs = estimatedDate.getTime() - now.getTime();
  const daysUntil = Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));

  const dateStr = estimatedDate.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  });

  return {
    estimatedDate: estimatedDate.toISOString(),
    daysUntilIceOut: daysUntil,
    confidence,
    description:
      daysUntil > 0
        ? `Estimated ice-out around ${dateStr} (${daysUntil} days from now). This is a rough estimate based on latitude and climate normals — actual ice-out varies by year, lake size, and depth.`
        : `Ice-out has likely already occurred for this latitude. Ice season is typically over by ${dateStr}.`,
  };
}

/**
 * Check if it's currently ice fishing season for a location.
 * Based on latitude and current month.
 */
export function isIceFishingSeason(lat: number): boolean {
  const month = new Date().getMonth(); // 0-indexed
  // Ice fishing generally November (10) through March (2) for northern US
  // Extend for higher latitudes
  if (lat >= 50) return month >= 10 || month <= 4; // Nov-May
  if (lat >= 45) return month >= 11 || month <= 3; // Dec-April
  if (lat >= 42) return month === 11 || month <= 2; // Dec-March
  if (lat >= 40) return month === 0 || month === 1; // Jan-Feb only
  return false;
}

/**
 * Filter spots to those suitable for ice fishing.
 * Only includes lakes, reservoirs, and ponds (no rivers or streams).
 */
export function isIceFishingWater(waterBodyType: string): boolean {
  const type = waterBodyType.toLowerCase();
  return (
    type.includes('lake') ||
    type.includes('reservoir') ||
    type.includes('pond') ||
    type.includes('impoundment')
  );
}

// ── Winter species tips ──────────────────────────────────────────

export const WINTER_SPECIES_TIPS: WinterSpeciesTip[] = [
  {
    species: 'Walleye',
    icon: 'fish',
    bestDepthFt: '15-30 ft',
    bestTime: 'Dawn and dusk — first/last light',
    bait: ['Jigging Rap', 'Minnow on tip-up', 'Blade bait'],
    technique: 'Aggressive jigging near bottom. Set tip-ups in deeper water with live minnows. Focus on transitions between flats and drop-offs.',
  },
  {
    species: 'Yellow Perch',
    icon: 'fish',
    bestDepthFt: '10-25 ft',
    bestTime: 'Midday — 10 AM to 2 PM',
    bait: ['Waxworms', 'Minnow heads', 'Small tungsten jigs'],
    technique: 'Drill many holes and stay mobile. Perch school up in winter — when you find one, you find many. Use electronics to locate schools.',
  },
  {
    species: 'Northern Pike',
    icon: 'fish',
    bestDepthFt: '5-15 ft (weedy flats)',
    bestTime: 'Late morning through afternoon',
    bait: ['Large dead bait (smelt, herring)', 'Quick-strike rig', 'Sucker minnow'],
    technique: 'Set tip-ups over weed edges and shallow bays. Pike cruise the shallows under ice looking for easy meals. Use a wire leader.',
  },
  {
    species: 'Lake Trout',
    icon: 'fish',
    bestDepthFt: '40-80 ft',
    bestTime: 'Early morning',
    bait: ['White tube jig', 'Airplane jig', 'Cut cisco'],
    technique: 'Target deep structure and suspended fish. Long jigging strokes to attract fish from distance. Lake trout can be very deep under ice.',
  },
  {
    species: 'Crappie',
    icon: 'fish',
    bestDepthFt: '15-25 ft (often suspended)',
    bestTime: 'Late afternoon into evening',
    bait: ['Small plastics', 'Waxworms', 'Tungsten jigs'],
    technique: 'Crappie often suspend in the water column — use electronics. Target basin areas adjacent to structure. They feed up, so position bait above them.',
  },
  {
    species: 'Bluegill',
    icon: 'fish',
    bestDepthFt: '8-18 ft',
    bestTime: 'Midday warmth',
    bait: ['Waxworms', 'Euro larvae', 'Micro plastics'],
    technique: 'Finesse is key — use the lightest possible jigs (1/64 oz or smaller). Slow, subtle movements. Focus on weed edges and inside turns.',
  },
];

// ── Equipment checklist ──────────────────────────────────────────

export function getIceEquipmentChecklist(): IceEquipmentItem[] {
  return [
    { name: 'Ice auger (hand or power)', category: 'essential', checked: false, description: 'For drilling fishing holes through the ice' },
    { name: 'Ice skimmer/scoop', category: 'essential', checked: false, description: 'Remove ice shavings from holes' },
    { name: 'Ice fishing rod & reel', category: 'essential', checked: false, description: 'Short, sensitive rods for jigging' },
    { name: 'Bait (waxworms, minnows)', category: 'essential', checked: false, description: 'Live bait for winter species' },
    { name: 'Ice picks/safety claws', category: 'essential', checked: false, description: 'CRITICAL — wear around neck for self-rescue if you fall through' },
    { name: 'Float suit or PFD', category: 'essential', checked: false, description: 'Floatation in case of breakthrough' },
    { name: 'Spud bar', category: 'essential', checked: false, description: 'Check ice thickness as you walk out' },
    { name: 'Bucket or sled', category: 'essential', checked: false, description: 'Transport gear and sit on' },
    { name: 'Warm layered clothing', category: 'essential', checked: false, description: 'Moisture-wicking base, insulating mid, windproof outer' },
    { name: 'Ice shelter/tent', category: 'recommended', checked: false, description: 'Protection from wind and elements' },
    { name: 'Portable heater', category: 'recommended', checked: false, description: 'Propane heater for shelter (ventilation required!)' },
    { name: 'Electronics/sonar', category: 'recommended', checked: false, description: 'Flasher or fish finder for locating fish' },
    { name: 'Tip-ups', category: 'recommended', checked: false, description: 'Passive fishing rigs — set it and wait for flags' },
    { name: 'Hand/toe warmers', category: 'recommended', checked: false, description: 'Chemical warmers for extended outings' },
    { name: 'Headlamp', category: 'recommended', checked: false, description: 'For dawn/dusk fishing and early darkness' },
    { name: 'First aid kit', category: 'recommended', checked: false, description: 'Basic first aid supplies' },
    { name: 'Phone (charged) + whistle', category: 'essential', checked: false, description: 'Emergency communication' },
    { name: 'Sled/toboggan', category: 'optional', checked: false, description: 'Easier gear transport on ice' },
    { name: 'Underwater camera', category: 'optional', checked: false, description: 'See fish behavior and structure below ice' },
    { name: 'Ice cleats', category: 'recommended', checked: false, description: 'Traction on slippery ice surface' },
  ];
}

/** Clear the ice fishing cache. */
export function clearIceCache(): void {
  cache.clear();
}
