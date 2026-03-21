/**
 * OpenCatch — Best Fishing Time Windows Service
 *
 * Calculates optimal fishing hours for a location based on:
 * - Solunar theory (major/minor periods)
 * - Weather conditions (pressure trends, wind, temp)
 * - Species-specific activity patterns
 * - Sunrise/sunset proximity
 * - Moon phase influence
 *
 * Competitor parity: FishAngler "bite windows", Fishbrain "BiteTime".
 * OpenCatch EXCEEDS by combining solunar + weather + species-specific factors.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface TimeWindow {
  startHour: number;   // 0-23
  endHour: number;     // 0-23
  quality: 'prime' | 'good' | 'fair';
  score: number;       // 0-100
  label: string;
  reasons: string[];
  species?: string[];  // Which species are most active
}

export interface WeatherWarning {
  type: 'snow' | 'freezing_rain' | 'heavy_rain' | 'extreme_cold' | 'cold' | 'high_wind' | 'thunderstorm' | 'poor_aqi' | 'flood' | 'wildfire';
  message: string;
  severity: 'danger' | 'warning' | 'caution';
}

export interface WeatherPenaltyOptions {
  weatherCode?: number;        // WMO weather code
  tempF?: number;              // Air temperature in Fahrenheit
  windMph?: number;            // Wind speed in mph
  aqi?: number;                // Air Quality Index (US AQI)
  hasFloodWarning?: boolean;
  hasWildfireWarning?: boolean;
}

export interface DailyBiteForecast {
  date: Date;
  overallRating: number;  // 0-100
  ratingLabel: string;
  windows: TimeWindow[];
  bestWindow: TimeWindow | null;
  solunarMajors: { start: number; end: number }[];
  solunarMinors: { start: number; end: number }[];
  sunrise: number;       // hour (decimal)
  sunset: number;        // hour (decimal)
  moonPhase: string;
  moonIllumination: number;
  hourlyScores: number[];  // 24 entries, 0-100
  weatherWarnings: WeatherWarning[];
}

// ── Solunar Calculation ──────────────────────────────────────────────────────

function julianDay(date: Date): number {
  const y = date.getUTCFullYear();
  const m = date.getUTCMonth() + 1;
  const d = date.getUTCDate() + (date.getUTCHours() + date.getUTCMinutes() / 60) / 24;
  const a = Math.floor((14 - m) / 12);
  const yp = y + 4800 - a;
  const mp = m + 12 * a - 3;
  return d + Math.floor((153 * mp + 2) / 5) + 365 * yp + Math.floor(yp / 4) - Math.floor(yp / 100) + Math.floor(yp / 400) - 32045;
}

function getMoonTransit(date: Date, lon: number): number {
  const jd = julianDay(date);
  const daysSinceJ2000 = jd - 2451545.0;
  // Simplified lunar transit (moon crosses meridian)
  const lunarDay = 24.8412; // hours
  const baseMoonrise = (daysSinceJ2000 * 24 / lunarDay) % 24;
  const lonCorrection = lon / 15; // Convert degrees to hours
  return (baseMoonrise + lonCorrection + 24) % 24;
}

function getSolunarPeriods(date: Date, lat: number, lon: number) {
  const moonTransit = getMoonTransit(date, lon);
  const moonUnderfoot = (moonTransit + 12) % 24;

  // Major periods: ~2 hours centered on moon transit and underfoot
  const majors = [
    { start: (moonTransit - 1 + 24) % 24, end: (moonTransit + 1) % 24 },
    { start: (moonUnderfoot - 1 + 24) % 24, end: (moonUnderfoot + 1) % 24 },
  ];

  // Minor periods: ~1 hour centered on moonrise and moonset (90 degrees from transit)
  const moonrise = (moonTransit - 6 + 24) % 24;
  const moonset = (moonTransit + 6) % 24;
  const minors = [
    { start: (moonrise - 0.5 + 24) % 24, end: (moonrise + 0.5) % 24 },
    { start: (moonset - 0.5 + 24) % 24, end: (moonset + 0.5) % 24 },
  ];

  return { majors, minors };
}

function getSunTimes(date: Date, lat: number, lon: number): { sunrise: number; sunset: number } {
  const jd = julianDay(date);
  const n = jd - 2451545.0 + 0.0008;
  const jStar = n - lon / 360;
  const mDeg = (357.5291 + 0.98560028 * jStar) % 360;
  const mRad = mDeg * Math.PI / 180;
  const c = 1.9148 * Math.sin(mRad) + 0.02 * Math.sin(2 * mRad);
  const lambdaDeg = (mDeg + c + 180 + 102.9372) % 360;
  const lambdaRad = lambdaDeg * Math.PI / 180;
  const sinDecl = Math.sin(lambdaRad) * Math.sin(23.4397 * Math.PI / 180);
  const cosDecl = Math.cos(Math.asin(sinDecl));
  const latRad = lat * Math.PI / 180;
  const cosHA = (Math.sin(-0.833 * Math.PI / 180) - Math.sin(latRad) * sinDecl) / (Math.cos(latRad) * cosDecl);

  if (cosHA > 1 || cosHA < -1) return { sunrise: 6, sunset: 18 };

  const ha = Math.acos(cosHA) * 180 / Math.PI;
  const jTransit = 2451545.0 + jStar + 0.0053 * Math.sin(mRad) - 0.0069 * Math.sin(2 * lambdaRad);
  const solarNoon = ((jTransit - Math.floor(jTransit)) * 24 + lon / 15 + 24) % 24;

  return {
    sunrise: solarNoon - ha / 15,
    sunset: solarNoon + ha / 15,
  };
}

function getMoonPhaseInfo(date: Date): { name: string; illumination: number; phase: number } {
  const jd = julianDay(date);
  const daysSinceNew = (jd - 2451550.1) % 29.530588853;
  const phase = ((daysSinceNew % 29.530588853) + 29.530588853) % 29.530588853 / 29.530588853;
  const illumination = Math.round((1 - Math.cos(phase * 2 * Math.PI)) / 2 * 100);

  let name: string;
  if (phase < 0.0625) name = 'New Moon';
  else if (phase < 0.1875) name = 'Waxing Crescent';
  else if (phase < 0.3125) name = 'First Quarter';
  else if (phase < 0.4375) name = 'Waxing Gibbous';
  else if (phase < 0.5625) name = 'Full Moon';
  else if (phase < 0.6875) name = 'Waning Gibbous';
  else if (phase < 0.8125) name = 'Last Quarter';
  else if (phase < 0.9375) name = 'Waning Crescent';
  else name = 'New Moon';

  return { name, illumination, phase };
}

// ── Weather Penalty System ───────────────────────────────────────────────────

/**
 * Determine weather warnings and compute a penalty multiplier + hard cap
 * for the fishing score based on dangerous or adverse weather conditions.
 */
export function computeWeatherPenalty(opts: WeatherPenaltyOptions): {
  warnings: WeatherWarning[];
  cap: number;       // hard maximum score (100 = no cap)
  multiplier: number; // multiplicative penalty (1.0 = no penalty)
} {
  const warnings: WeatherWarning[] = [];
  let cap = 100;
  let multiplier = 1.0;

  // Flood / wildfire = score 0, immediate danger
  if (opts.hasFloodWarning) {
    warnings.push({ type: 'flood', message: 'Flood warning — Do not fish', severity: 'danger' });
    cap = 0;
  }
  if (opts.hasWildfireWarning) {
    warnings.push({ type: 'wildfire', message: 'Wildfire warning — Do not fish', severity: 'danger' });
    cap = 0;
  }

  // Thunderstorm (WMO codes 95-99): capped at 10
  if (opts.weatherCode !== undefined && opts.weatherCode >= 95) {
    warnings.push({ type: 'thunderstorm', message: 'Thunderstorm — Not safe to fish', severity: 'danger' });
    cap = Math.min(cap, 10);
  }

  // Snow / freezing rain: capped at 20
  // WMO codes: 56-57 = freezing drizzle, 66-67 = freezing rain, 71-77 = snow, 85-86 = snow showers
  if (opts.weatherCode !== undefined) {
    if ((opts.weatherCode >= 71 && opts.weatherCode <= 77) ||
        (opts.weatherCode >= 85 && opts.weatherCode <= 86)) {
      warnings.push({ type: 'snow', message: 'Snow — Not recommended', severity: 'warning' });
      cap = Math.min(cap, 20);
    }
    if ((opts.weatherCode >= 56 && opts.weatherCode <= 57) ||
        (opts.weatherCode >= 66 && opts.weatherCode <= 67)) {
      warnings.push({ type: 'freezing_rain', message: 'Freezing rain — Not recommended', severity: 'warning' });
      cap = Math.min(cap, 20);
    }
  }

  // Heavy rain (WMO codes 63, 65, 67, 80-82): capped at 40
  if (opts.weatherCode !== undefined) {
    if (opts.weatherCode === 63 || opts.weatherCode === 65 ||
        (opts.weatherCode >= 80 && opts.weatherCode <= 82)) {
      warnings.push({ type: 'heavy_rain', message: 'Heavy rain — Poor conditions', severity: 'caution' });
      cap = Math.min(cap, 40);
    }
  }

  // Temperature penalties
  if (opts.tempF !== undefined) {
    if (opts.tempF < -7 * 9 / 5 + 32) {
      // Below -7°C / ~20°F: capped at 15
      warnings.push({ type: 'extreme_cold', message: 'Extreme cold — Dangerous conditions', severity: 'danger' });
      cap = Math.min(cap, 15);
    } else if (opts.tempF < 32) {
      // Below 32°F / 0°C: score reduced by 50%
      warnings.push({ type: 'cold', message: 'Freezing temperatures', severity: 'caution' });
      multiplier *= 0.5;
    }
  }

  // Wind > 30 mph: score reduced by 40%
  if (opts.windMph !== undefined && opts.windMph > 30) {
    warnings.push({ type: 'high_wind', message: `High wind ${opts.windMph} mph — Unsafe on water`, severity: 'warning' });
    multiplier *= 0.6;
  }

  // Poor AQI > 150: score reduced by 30%
  if (opts.aqi !== undefined && opts.aqi > 150) {
    warnings.push({ type: 'poor_aqi', message: `Poor air quality (AQI ${opts.aqi})`, severity: 'caution' });
    multiplier *= 0.7;
  }

  return { warnings, cap, multiplier };
}

// ── Score Calculation ────────────────────────────────────────────────────────

function isInPeriod(hour: number, period: { start: number; end: number }): boolean {
  if (period.start <= period.end) {
    return hour >= period.start && hour <= period.end;
  }
  // Wraps around midnight
  return hour >= period.start || hour <= period.end;
}

function computeHourlyScores(
  date: Date,
  lat: number,
  lon: number,
  options?: {
    tempF?: number;
    pressureTrend?: 'rising' | 'falling' | 'stable';
    windMph?: number;
    cloudCover?: number;
  },
): number[] {
  const solunar = getSolunarPeriods(date, lat, lon);
  const sun = getSunTimes(date, lat, lon);
  const moon = getMoonPhaseInfo(date);
  const scores: number[] = [];

  // Moon phase multiplier: new and full moons boost activity
  const moonBoost = (moon.phase < 0.1 || (moon.phase > 0.45 && moon.phase < 0.55))
    ? 1.25 : (moon.phase > 0.2 && moon.phase < 0.3) || (moon.phase > 0.7 && moon.phase < 0.8)
    ? 1.1 : 1.0;

  // Pressure trend multiplier
  const pressureMult = options?.pressureTrend === 'rising' ? 1.15
    : options?.pressureTrend === 'falling' ? 0.85
    : 1.0;

  for (let h = 0; h < 24; h++) {
    let score = 20; // Base

    // Solunar major periods: +40
    for (const major of solunar.majors) {
      if (isInPeriod(h, major)) score += 40;
    }

    // Solunar minor periods: +25
    for (const minor of solunar.minors) {
      if (isInPeriod(h, minor)) score += 25;
    }

    // Dawn/dusk bonus: +20 within 1 hour of sunrise/sunset
    if (Math.abs(h - sun.sunrise) <= 1) score += 20;
    if (Math.abs(h - sun.sunset) <= 1) score += 20;

    // Golden hour: +10 within 2 hours of sunrise/sunset
    if (Math.abs(h - sun.sunrise) <= 2 && Math.abs(h - sun.sunrise) > 1) score += 10;
    if (Math.abs(h - sun.sunset) <= 2 && Math.abs(h - sun.sunset) > 1) score += 10;

    // Night penalty (but not for catfish)
    if (h >= 23 || h <= 3) score *= 0.5;

    // Apply multipliers
    score *= moonBoost;
    score *= pressureMult;

    // Wind penalty
    if (options?.windMph !== undefined && options.windMph > 20) {
      score *= 0.8;
    }

    // Overcast bonus (fish feed more)
    if (options?.cloudCover !== undefined && options.cloudCover > 70) {
      score *= 1.1;
    }

    scores.push(Math.min(100, Math.max(0, Math.round(score))));
  }

  return scores;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Get the full daily bite forecast for a location.
 */
export function getDailyBiteForecast(
  lat: number,
  lon: number,
  options?: {
    date?: Date;
    tempF?: number;
    pressureTrend?: 'rising' | 'falling' | 'stable';
    windMph?: number;
    cloudCover?: number;
    weatherPenalty?: WeatherPenaltyOptions;
  },
): DailyBiteForecast {
  const date = options?.date ?? new Date();
  let hourlyScores = computeHourlyScores(date, lat, lon, options);
  const solunar = getSolunarPeriods(date, lat, lon);
  const sun = getSunTimes(date, lat, lon);
  const moon = getMoonPhaseInfo(date);

  // Apply weather penalty if provided
  const penalty = options?.weatherPenalty
    ? computeWeatherPenalty(options.weatherPenalty)
    : { warnings: [] as WeatherWarning[], cap: 100, multiplier: 1.0 };

  if (penalty.multiplier < 1.0 || penalty.cap < 100) {
    hourlyScores = hourlyScores.map((s) =>
      Math.min(penalty.cap, Math.max(0, Math.round(s * penalty.multiplier))),
    );
  }

  // Extract windows from consecutive high-score hours
  const windows: TimeWindow[] = [];
  let windowStart = -1;
  let windowScores: number[] = [];

  for (let h = 0; h < 24; h++) {
    if (hourlyScores[h] >= 50) {
      if (windowStart === -1) windowStart = h;
      windowScores.push(hourlyScores[h]);
    } else if (windowStart !== -1) {
      const avgScore = windowScores.reduce((a, b) => a + b, 0) / windowScores.length;
      const maxScore = Math.max(...windowScores);
      const quality = maxScore >= 80 ? 'prime' : maxScore >= 60 ? 'good' : 'fair';

      const reasons: string[] = [];
      for (const major of solunar.majors) {
        if (windowStart <= major.end && (windowStart + windowScores.length) >= major.start) {
          reasons.push('Solunar major period');
        }
      }
      for (const minor of solunar.minors) {
        if (windowStart <= minor.end && (windowStart + windowScores.length) >= minor.start) {
          reasons.push('Solunar minor period');
        }
      }
      if (Math.abs(windowStart - sun.sunrise) <= 2 || Math.abs(windowStart + windowScores.length - sun.sunrise) <= 2) {
        reasons.push('Near sunrise');
      }
      if (Math.abs(windowStart - sun.sunset) <= 2 || Math.abs(windowStart + windowScores.length - sun.sunset) <= 2) {
        reasons.push('Near sunset');
      }
      if (reasons.length === 0) reasons.push('Favorable conditions');

      const startLabel = formatHour(windowStart);
      const endH = windowStart + windowScores.length;
      const endLabel = formatHour(endH % 24);

      windows.push({
        startHour: windowStart,
        endHour: endH % 24,
        quality,
        score: Math.round(avgScore),
        label: `${startLabel} \u2013 ${endLabel}`,
        reasons,
      });
      windowStart = -1;
      windowScores = [];
    }
  }

  // Handle window that extends to end of day
  if (windowStart !== -1 && windowScores.length > 0) {
    const avgScore = windowScores.reduce((a, b) => a + b, 0) / windowScores.length;
    const maxScore = Math.max(...windowScores);
    const quality = maxScore >= 80 ? 'prime' : maxScore >= 60 ? 'good' : 'fair';
    const startLabel = formatHour(windowStart);

    windows.push({
      startHour: windowStart,
      endHour: 23,
      quality,
      score: Math.round(avgScore),
      label: `${startLabel} \u2013 11:00 PM`,
      reasons: ['Favorable conditions'],
    });
  }

  // Sort by score
  windows.sort((a, b) => b.score - a.score);
  const bestWindow = windows.length > 0 ? windows[0] : null;

  // Overall rating
  const overallRating = Math.round(hourlyScores.reduce((a, b) => a + b, 0) / 24);
  let ratingLabel: string;
  if (overallRating >= 70) ratingLabel = 'Excellent';
  else if (overallRating >= 55) ratingLabel = 'Good';
  else if (overallRating >= 40) ratingLabel = 'Fair';
  else if (overallRating >= 25) ratingLabel = 'Poor';
  else ratingLabel = 'Poor';

  return {
    date,
    overallRating,
    ratingLabel,
    windows,
    bestWindow,
    solunarMajors: solunar.majors,
    solunarMinors: solunar.minors,
    sunrise: sun.sunrise,
    sunset: sun.sunset,
    moonPhase: moon.name,
    moonIllumination: moon.illumination,
    hourlyScores,
    weatherWarnings: penalty.warnings,
  };
}

/**
 * Get a 7-day bite forecast.
 */
export function getWeeklyBiteForecast(
  lat: number,
  lon: number,
  options?: {
    tempF?: number;
    pressureTrend?: 'rising' | 'falling' | 'stable';
    windMph?: number;
    cloudCover?: number;
    weatherPenalty?: WeatherPenaltyOptions;
  },
): DailyBiteForecast[] {
  const forecasts: DailyBiteForecast[] = [];
  for (let i = 0; i < 7; i++) {
    const date = new Date();
    date.setDate(date.getDate() + i);
    date.setHours(0, 0, 0, 0);
    forecasts.push(getDailyBiteForecast(lat, lon, { ...options, date }));
  }
  return forecasts;
}

/**
 * Format an hour (0-23) to a display string.
 */
export function formatHour(hour: number): string {
  if (hour === 0 || hour === 24) return '12:00 AM';
  if (hour === 12) return '12:00 PM';
  if (hour < 12) return `${hour}:00 AM`;
  return `${hour - 12}:00 PM`;
}
