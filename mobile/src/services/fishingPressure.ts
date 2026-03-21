/**
 * OpenCatch — Fishing Pressure & Activity Service
 *
 * Estimates fishing pressure (how busy a spot is) using:
 * - Time of day (dawn/dusk peak)
 * - Day of week (weekend vs weekday)
 * - Season and month
 * - Weather conditions (nice weather = more anglers)
 * - Holidays and tournament schedules
 *
 * Competitor parity: Fishbrain "fishing pressure", FishAngler activity feed.
 * OpenCatch EXCEEDS by adding real-time weather-adjusted estimates.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export type PressureLevel = 'very-low' | 'low' | 'moderate' | 'high' | 'very-high';

export interface PressureReading {
  level: PressureLevel;
  score: number;             // 0-100
  label: string;
  description: string;
  color: string;
  icon: string;              // Ionicon name
  peakHours: string[];       // e.g. ["6:00 AM - 9:00 AM", "5:00 PM - 8:00 PM"]
  factors: PressureFactor[];
}

export interface PressureFactor {
  name: string;
  impact: 'increases' | 'decreases' | 'neutral';
  weight: number;  // 0-1
  detail: string;
}

export interface HourlyPressure {
  hour: number;    // 0-23
  score: number;   // 0-100
  level: PressureLevel;
}

export interface PressureForecast {
  date: Date;
  overall: PressureReading;
  hourly: HourlyPressure[];
  bestAvoidHours: number[];   // Hours with lowest pressure
  recommendation: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const PRESSURE_CONFIG: Record<PressureLevel, { label: string; color: string; icon: string }> = {
  'very-low':  { label: 'Very Low',  color: '#2E7D32', icon: 'leaf-outline' },
  'low':       { label: 'Low',       color: '#66BB6A', icon: 'happy-outline' },
  'moderate':  { label: 'Moderate',  color: '#FFA726', icon: 'people-outline' },
  'high':      { label: 'High',      color: '#EF5350', icon: 'warning-outline' },
  'very-high': { label: 'Very High', color: '#B71C1C', icon: 'alert-circle-outline' },
};

/** US federal holidays (month-day) plus common fishing weekends */
const HOLIDAY_DATES: string[] = [
  '01-01', '05-26', '05-27', '05-28', // New Year, Memorial Day weekend
  '07-04', '07-05',                     // Independence Day
  '09-01', '09-02',                     // Labor Day weekend
  '11-28', '11-29',                     // Thanksgiving
];

// ── Pressure Calculation Engine ──────────────────────────────────────────────

function getHourMultiplier(hour: number): number {
  // Dawn and dusk peaks, midday moderate, night low
  const curve: Record<number, number> = {
    0: 0.05, 1: 0.03, 2: 0.02, 3: 0.02, 4: 0.05, 5: 0.30,
    6: 0.70, 7: 0.85, 8: 0.80, 9: 0.65, 10: 0.50, 11: 0.40,
    12: 0.35, 13: 0.30, 14: 0.35, 15: 0.40, 16: 0.55, 17: 0.75,
    18: 0.85, 19: 0.70, 20: 0.45, 21: 0.25, 22: 0.15, 23: 0.08,
  };
  return curve[hour] ?? 0.3;
}

function getDayOfWeekMultiplier(day: number): number {
  // 0=Sunday, 6=Saturday
  const multipliers = [1.0, 0.4, 0.35, 0.4, 0.45, 0.6, 0.95];
  return multipliers[day] ?? 0.5;
}

function getMonthMultiplier(month: number): number {
  // 0=Jan, peak in spring/summer
  const multipliers = [0.15, 0.20, 0.35, 0.60, 0.80, 0.95, 1.0, 0.90, 0.75, 0.55, 0.30, 0.15];
  return multipliers[month] ?? 0.5;
}

function getWeatherMultiplier(tempF?: number, windMph?: number, rainChance?: number): number {
  let mult = 1.0;

  if (tempF !== undefined) {
    if (tempF >= 65 && tempF <= 85) mult *= 1.2;      // Perfect weather
    else if (tempF >= 50 && tempF <= 95) mult *= 1.0;  // Acceptable
    else if (tempF >= 35 && tempF <= 100) mult *= 0.6; // Uncomfortable
    else mult *= 0.2;                                    // Extreme
  }

  if (windMph !== undefined) {
    if (windMph <= 10) mult *= 1.1;
    else if (windMph <= 20) mult *= 0.85;
    else if (windMph <= 30) mult *= 0.5;
    else mult *= 0.2;
  }

  if (rainChance !== undefined) {
    if (rainChance >= 70) mult *= 0.4;
    else if (rainChance >= 40) mult *= 0.7;
    else mult *= 1.0;
  }

  return mult;
}

function isHoliday(date: Date): boolean {
  const key = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  return HOLIDAY_DATES.includes(key);
}

function scoreToLevel(score: number): PressureLevel {
  if (score >= 80) return 'very-high';
  if (score >= 60) return 'high';
  if (score >= 40) return 'moderate';
  if (score >= 20) return 'low';
  return 'very-low';
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Calculate current fishing pressure for a location.
 */
export function getCurrentPressure(options?: {
  date?: Date;
  lat?: number;
  lon?: number;
  tempF?: number;
  windMph?: number;
  rainChance?: number;
  isPopularSpot?: boolean;
}): PressureReading {
  const date = options?.date ?? new Date();
  const hour = date.getHours();
  const dayOfWeek = date.getDay();
  const month = date.getMonth();

  // Base score from time factors
  const hourMult = getHourMultiplier(hour);
  const dayMult = getDayOfWeekMultiplier(dayOfWeek);
  const monthMult = getMonthMultiplier(month);
  const weatherMult = getWeatherMultiplier(options?.tempF, options?.windMph, options?.rainChance);
  const holidayMult = isHoliday(date) ? 1.5 : 1.0;
  const popularMult = options?.isPopularSpot ? 1.3 : 1.0;

  const rawScore = hourMult * dayMult * monthMult * weatherMult * holidayMult * popularMult * 100;
  const score = Math.min(100, Math.max(0, Math.round(rawScore)));
  const level = scoreToLevel(score);
  const config = PRESSURE_CONFIG[level];

  // Build factors list
  const factors: PressureFactor[] = [];

  const dayNames = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  if (dayOfWeek === 0 || dayOfWeek === 6) {
    factors.push({ name: 'Weekend', impact: 'increases', weight: dayMult, detail: `${dayNames[dayOfWeek]} — more anglers on the water` });
  } else {
    factors.push({ name: 'Weekday', impact: 'decreases', weight: dayMult, detail: `${dayNames[dayOfWeek]} — fewer anglers` });
  }

  if (hour >= 5 && hour <= 9) {
    factors.push({ name: 'Morning bite', impact: 'increases', weight: hourMult, detail: 'Peak morning fishing hours' });
  } else if (hour >= 16 && hour <= 20) {
    factors.push({ name: 'Evening bite', impact: 'increases', weight: hourMult, detail: 'Peak evening fishing hours' });
  } else if (hour >= 22 || hour <= 4) {
    factors.push({ name: 'Night', impact: 'decreases', weight: hourMult, detail: 'Very few anglers at night' });
  }

  if (month >= 4 && month <= 8) {
    factors.push({ name: 'Peak season', impact: 'increases', weight: monthMult, detail: 'May-Sep is prime fishing season' });
  } else if (month >= 10 || month <= 2) {
    factors.push({ name: 'Off season', impact: 'decreases', weight: monthMult, detail: 'Nov-Mar has fewer anglers' });
  }

  if (options?.tempF !== undefined && options.tempF >= 65 && options.tempF <= 85) {
    factors.push({ name: 'Nice weather', impact: 'increases', weight: 0.8, detail: `${options.tempF}\u00B0F — pleasant conditions draw crowds` });
  }

  if (options?.rainChance !== undefined && options.rainChance >= 60) {
    factors.push({ name: 'Rain expected', impact: 'decreases', weight: 0.5, detail: `${options.rainChance}% rain chance keeps anglers home` });
  }

  if (isHoliday(date)) {
    factors.push({ name: 'Holiday', impact: 'increases', weight: 1.0, detail: 'Holiday weekend — expect heavy traffic' });
  }

  // Determine peak hours
  const peakHours: string[] = [];
  if (monthMult >= 0.5) {
    peakHours.push('6:00 AM \u2013 9:00 AM');
    peakHours.push('5:00 PM \u2013 8:00 PM');
  } else {
    peakHours.push('8:00 AM \u2013 10:00 AM');
    peakHours.push('3:00 PM \u2013 5:00 PM');
  }

  // Build description
  let description: string;
  if (score >= 80) description = 'Crowded — try off-peak hours or less popular spots.';
  else if (score >= 60) description = 'Busy — good spots may be taken early.';
  else if (score >= 40) description = 'Average traffic — plenty of room.';
  else if (score >= 20) description = 'Quiet — great time for a peaceful outing.';
  else description = 'Empty — water to yourself.';

  return {
    level,
    score,
    label: config.label,
    description,
    color: config.color,
    icon: config.icon,
    peakHours,
    factors,
  };
}

/**
 * Get hourly pressure forecast for today.
 */
export function getHourlyPressure(options?: {
  date?: Date;
  tempF?: number;
  windMph?: number;
  rainChance?: number;
  isPopularSpot?: boolean;
}): HourlyPressure[] {
  const date = options?.date ?? new Date();
  const result: HourlyPressure[] = [];

  for (let h = 0; h < 24; h++) {
    const hourDate = new Date(date);
    hourDate.setHours(h, 0, 0, 0);
    const reading = getCurrentPressure({ ...options, date: hourDate });
    result.push({ hour: h, score: reading.score, level: reading.level });
  }

  return result;
}

/**
 * Get full pressure forecast for a date.
 */
export function getPressureForecast(options?: {
  date?: Date;
  tempF?: number;
  windMph?: number;
  rainChance?: number;
  isPopularSpot?: boolean;
}): PressureForecast {
  const date = options?.date ?? new Date();
  const hourly = getHourlyPressure(options);
  const overall = getCurrentPressure(options);

  // Find the 3 hours with lowest pressure
  const sorted = [...hourly].sort((a, b) => a.score - b.score);
  const bestAvoidHours = sorted.slice(0, 3).map((h) => h.hour);

  // Build recommendation
  const lowestHour = sorted[0];
  const hLabel = lowestHour.hour === 0 ? '12 AM' : lowestHour.hour <= 12 ? `${lowestHour.hour} AM` : `${lowestHour.hour - 12} PM`;
  const recommendation = overall.score >= 60
    ? `For a quieter experience, try fishing around ${hLabel} when pressure is lowest.`
    : 'Low fishing pressure today \u2014 good conditions for a relaxed trip.';

  return { date, overall, hourly, bestAvoidHours, recommendation };
}

// ── Persistence (user spot popularity tracking) ──────────────────────────────

const VISIT_KEY = 'opencatch_spot_visits';

interface SpotVisitLog {
  [spotId: string]: number[];  // Array of Unix timestamps
}

export async function logSpotVisit(spotId: string): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(VISIT_KEY);
    const log: SpotVisitLog = raw ? JSON.parse(raw) : {};
    if (!log[spotId]) log[spotId] = [];
    log[spotId].push(Date.now());
    // Keep only last 100 visits per spot
    if (log[spotId].length > 100) log[spotId] = log[spotId].slice(-100);
    await AsyncStorage.setItem(VISIT_KEY, JSON.stringify(log));
  } catch {
    // Silent fail for persistence
  }
}

export async function getSpotVisitCount(spotId: string, daysBack: number = 30): Promise<number> {
  try {
    const raw = await AsyncStorage.getItem(VISIT_KEY);
    if (!raw) return 0;
    const log: SpotVisitLog = JSON.parse(raw);
    const cutoff = Date.now() - daysBack * 86400000;
    return (log[spotId] ?? []).filter((t) => t >= cutoff).length;
  } catch {
    return 0;
  }
}
