import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Animated,
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  Dimensions,
  ActivityIndicator,
} from 'react-native';
import Svg, {
  Circle,
  Path,
} from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { SkeletonLoader } from '../components/ui/SkeletonLoader';
import { FeatureLocationPicker } from '../components/FeatureLocationPicker';
import { palette, getConditionBand, conditionConfig, scoreColor } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { getDailyBiteForecast, getWeeklyBiteForecast, formatHour, computeWeatherPenalty, type DailyBiteForecast as BiteFC, type TimeWindow } from '../services/bestTimeWindows';
import {
  getCurrentFeatureLocation,
  searchFeatureLocation,
  type FeatureLocation,
} from '../services/featureLocation';
import { getCurrentPressure, type PressureReading } from '../services/fishingPressure';
import { getWaterInsights, type WaterInsightsDashboard } from '../services/waterInsights';
import type { TabProps } from '../types/navigation';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

// ── Types ────────────────────────────────────────────────────────────────────

type ViewMode = 'Daily' | 'Extended';

interface HourlyData {
  hour: string;       // "07", "08", "Now", etc.
  isNow?: boolean;
  fishScore: number;
  condition: string;   // "Clear/Sunny", "Partly Cloudy"
  conditionIcon: string;
  cloudCover: number;
  visibility: number;  // km
  airTemp: number;     // °C
  pressure: number;    // Pa
  precipitation: number; // %
  precAccum: number;   // mm
  snowAccum: number;   // cm
  humidity: number;    // %
  uvIndex: number;
  windSpeed?: number;  // km/h
  windDirection?: number; // degrees
  weatherCode?: number; // WMO weather code
  aqi?: number;        // US AQI
}

interface DayForecast {
  dayLabel: string;    // "Fri"
  date: number;        // 20
  weatherIcon: string;
  hourly: HourlyData[];
}

// ── Open-Meteo API ───────────────────────────────────────────────────────────

const OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast';
const CACHE_TTL_MS = 30 * 60 * 1000; // 30 minutes

interface ForecastCache {
  data: DayForecast[];
  lat: number;
  lon: number;
  timestamp: number;
}

let forecastCache: ForecastCache | null = null;

function isCacheValid(lat: number, lon: number): boolean {
  if (!forecastCache) return false;
  if (Date.now() - forecastCache.timestamp > CACHE_TTL_MS) return false;
  // Check if location is roughly the same (within ~1km)
  const dlat = Math.abs(forecastCache.lat - lat);
  const dlon = Math.abs(forecastCache.lon - lon);
  return dlat < 0.01 && dlon < 0.01;
}

/**
 * Map WMO weather code to condition label + icon.
 * Falls back to cloud-cover heuristic only if weatherCode is absent.
 */
function conditionFromWMO(weatherCode: number | undefined, cloud: number): { label: string; icon: string } {
  if (weatherCode !== undefined) {
    if (weatherCode === 0) return { label: 'Clear', icon: 'sunny-outline' };
    if (weatherCode === 1) return { label: 'Mainly Clear', icon: 'sunny-outline' };
    if (weatherCode === 2) return { label: 'Partly Cloudy', icon: 'partly-sunny-outline' };
    if (weatherCode === 3) return { label: 'Overcast', icon: 'cloudy-outline' };
    if (weatherCode >= 45 && weatherCode <= 48) return { label: 'Foggy', icon: 'cloud-outline' };
    if (weatherCode >= 51 && weatherCode <= 55) return { label: 'Drizzle', icon: 'rainy-outline' };
    if (weatherCode >= 56 && weatherCode <= 57) return { label: 'Freezing Drizzle', icon: 'rainy-outline' };
    if (weatherCode >= 61 && weatherCode <= 65) return { label: 'Rain', icon: 'rainy-outline' };
    if (weatherCode >= 66 && weatherCode <= 67) return { label: 'Freezing Rain', icon: 'rainy-outline' };
    if (weatherCode >= 71 && weatherCode <= 75) return { label: 'Snow', icon: 'snow-outline' };
    if (weatherCode === 77) return { label: 'Snow Grains', icon: 'snow-outline' };
    if (weatherCode >= 80 && weatherCode <= 82) return { label: 'Rain Showers', icon: 'rainy-outline' };
    if (weatherCode >= 85 && weatherCode <= 86) return { label: 'Snow Showers', icon: 'snow-outline' };
    if (weatherCode >= 95 && weatherCode <= 99) return { label: 'Thunderstorm', icon: 'thunderstorm-outline' };
  }
  // Fallback: cloud-cover heuristic
  if (cloud < 20) return { label: 'Clear/Sunny', icon: 'sunny-outline' };
  if (cloud < 50) return { label: 'Partly Cloudy', icon: 'partly-sunny-outline' };
  if (cloud < 80) return { label: 'Mostly Cloudy', icon: 'cloudy-outline' };
  return { label: 'Overcast', icon: 'cloudy-outline' };
}

/**
 * Compute a simple fish activity score from weather conditions.
 * Higher during dawn/dusk, stable pressure, moderate temps.
 */
function computeFishScore(hour: number, temp: number, pressure: number, cloudCover: number, windSpeed: number, weatherCode?: number): number {
  let score = 20;
  // Dawn/dusk bonus
  if ((hour >= 5 && hour <= 9) || (hour >= 17 && hour <= 20)) score += 25;
  else if (hour >= 10 && hour <= 16) score += 10;
  // Cloud cover bonus (overcast is good)
  if (cloudCover >= 40 && cloudCover <= 80) score += 10;
  // Temperature sweet spot (15-25°C)
  if (temp >= 15 && temp <= 25) score += 15;
  else if (temp >= 10 && temp <= 30) score += 5;
  // Moderate wind is good
  if (windSpeed >= 5 && windSpeed <= 20) score += 10;
  // Pressure around 1013-1020 is good
  if (pressure >= 1008 && pressure <= 1025) score += 10;
  // Add some variation
  score += Math.round(Math.sin(hour * 0.7 + pressure * 0.01) * 5);

  // Apply weather penalty based on dangerous conditions
  const tempF = temp * 9 / 5 + 32;
  const windMph = windSpeed * 0.621371;
  const penalty = computeWeatherPenalty({
    weatherCode,
    tempF,
    windMph,
  });
  score = Math.round(score * penalty.multiplier);
  score = Math.min(score, penalty.cap);

  return Math.max(0, Math.min(95, score));
}

// AQI fetch from Open-Meteo Air Quality API
async function fetchAqi(lat: number, lon: number): Promise<Map<string, number[]>> {
  try {
    const params = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      hourly: 'us_aqi',
      forecast_days: '16',
      timezone: 'auto',
    });
    const resp = await fetch(`https://air-quality-api.open-meteo.com/v1/air-quality?${params}`);
    if (!resp.ok) return new Map();
    const data = await resp.json();
    const times: string[] = data.hourly?.time ?? [];
    const aqiVals: number[] = data.hourly?.us_aqi ?? [];
    const dayMap = new Map<string, number[]>();
    for (let i = 0; i < times.length; i++) {
      const dateStr = times[i].slice(0, 10);
      if (!dayMap.has(dateStr)) dayMap.set(dateStr, []);
      dayMap.get(dateStr)!.push(aqiVals[i] ?? 0);
    }
    return dayMap;
  } catch {
    return new Map();
  }
}

async function fetchOpenMeteoForecast(lat: number, lon: number): Promise<DayForecast[]> {
  if (isCacheValid(lat, lon) && forecastCache) {
    return forecastCache.data;
  }

  const params = new URLSearchParams({
    latitude: lat.toFixed(4),
    longitude: lon.toFixed(4),
    hourly: [
      'temperature_2m',
      'relative_humidity_2m',
      'pressure_msl',
      'cloud_cover',
      'precipitation',
      'snowfall',
      'visibility',
      'uv_index',
      'wind_speed_10m',
      'wind_direction_10m',
      'weather_code',
    ].join(','),
    forecast_days: '16',
    timezone: 'auto',
  });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);

  try {
    const resp = await fetch(`${OPEN_METEO_URL}?${params}`, {
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!resp.ok) {
      throw new Error(`Open-Meteo returned ${resp.status}`);
    }

    const data = await resp.json();
    const hourlyData = data.hourly;
    if (!hourlyData || !hourlyData.time) {
      throw new Error('Invalid Open-Meteo response');
    }

    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const now = new Date();
    const currentHour = now.getHours();
    const todayStr = now.toISOString().slice(0, 10);

    // Group hourly data by day
    const dayMap = new Map<string, HourlyData[]>();

    for (let i = 0; i < hourlyData.time.length; i++) {
      const timeStr: string = hourlyData.time[i];
      const dateStr = timeStr.slice(0, 10);
      const hour = parseInt(timeStr.slice(11, 13), 10);

      const temp = hourlyData.temperature_2m?.[i] ?? 0;
      const humidity = hourlyData.relative_humidity_2m?.[i] ?? 0;
      const pressureHpa = hourlyData.pressure_msl?.[i] ?? 1013;
      const cloud = hourlyData.cloud_cover?.[i] ?? 0;
      const precip = hourlyData.precipitation?.[i] ?? 0;
      const snow = hourlyData.snowfall?.[i] ?? 0;
      const visibilityM = hourlyData.visibility?.[i] ?? 16000;
      const uv = hourlyData.uv_index?.[i] ?? 0;
      const windSpeed = hourlyData.wind_speed_10m?.[i] ?? 0;
      const windDir = hourlyData.wind_direction_10m?.[i] ?? 0;
      const weatherCode: number | undefined = hourlyData.weather_code?.[i];

      const isToday = dateStr === todayStr;
      const isNow = isToday && hour === currentHour;
      const hourLabel = isNow ? 'Now' : String(hour).padStart(2, '0');

      const cond = conditionFromWMO(weatherCode, cloud);
      const fishScore = computeFishScore(hour, temp, pressureHpa, cloud, windSpeed, weatherCode);

      const entry: HourlyData = {
        hour: hourLabel,
        isNow,
        fishScore,
        condition: cond.label,
        conditionIcon: cond.icon,
        cloudCover: Math.round(cloud),
        visibility: Math.round(visibilityM / 1000),
        airTemp: Math.round(temp),
        pressure: Math.round(pressureHpa * 100), // hPa to Pa
        precipitation: Math.round(precip * 10) / 10 > 0 ? Math.round(precip * 100) / 100 : 0,
        precAccum: Math.round(precip * 10) / 10,
        snowAccum: Math.round(snow * 10) / 10,
        humidity: Math.round(humidity),
        uvIndex: Math.round(uv),
        windSpeed: Math.round(windSpeed),
        windDirection: Math.round(windDir),
        weatherCode,
      };

      if (!dayMap.has(dateStr)) dayMap.set(dateStr, []);
      dayMap.get(dateStr)!.push(entry);
    }

    // Convert to DayForecast array
    const forecasts: DayForecast[] = [];
    for (const [dateStr, hours] of dayMap) {
      const d = new Date(dateStr + 'T12:00:00');
      const dayLabel = days[d.getDay()];
      // Pick most common condition icon for the day
      const iconCounts = new Map<string, number>();
      for (const h of hours) {
        iconCounts.set(h.conditionIcon, (iconCounts.get(h.conditionIcon) || 0) + 1);
      }
      const weatherIcon = [...iconCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || 'sunny-outline';

      forecasts.push({
        dayLabel,
        date: d.getDate(),
        weatherIcon,
        hourly: hours,
      });
    }

    // Fetch AQI data and merge into hourly entries
    try {
      const aqiMap = await fetchAqi(lat, lon);
      for (const forecast of forecasts) {
        const d = new Date();
        const idx = forecasts.indexOf(forecast);
        d.setDate(d.getDate() + idx);
        const dateKey = d.toISOString().slice(0, 10);
        for (const [key, aqiValues] of aqiMap) {
          const keyDate = new Date(key + 'T12:00:00');
          if (keyDate.getDate() === forecast.date && keyDate.getDay() === ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].indexOf(forecast.dayLabel)) {
            for (let i = 0; i < Math.min(forecast.hourly.length, aqiValues.length); i++) {
              forecast.hourly[i].aqi = Math.round(aqiValues[i]);
            }
            break;
          }
        }
        if (!forecast.hourly[0]?.aqi) {
          const allKeys = [...aqiMap.keys()].sort();
          if (allKeys[idx]) {
            const aqiValues = aqiMap.get(allKeys[idx])!;
            for (let i = 0; i < Math.min(forecast.hourly.length, aqiValues.length); i++) {
              forecast.hourly[i].aqi = Math.round(aqiValues[i]);
            }
          }
        }
      }
    } catch {
      // AQI is non-critical — continue without it
    }

    // Update cache
    forecastCache = { data: forecasts, lat, lon, timestamp: Date.now() };

    return forecasts;
  } catch (err) {
    clearTimeout(timeoutId);
    if (forecastCache?.data) {
      return forecastCache.data;
    }
    throw err;
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

const DAYS_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function windDirectionLabel(deg: number): string {
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(deg / 45) % 8];
}

function computeDaySummary(day: DayForecast) {
  const temps = day.hourly.map((h) => h.airTemp);
  const hi = Math.max(...temps);
  const lo = Math.min(...temps);
  const windSpeeds = day.hourly.filter((h) => h.windSpeed != null).map((h) => h.windSpeed!);
  const avgWind = windSpeeds.length > 0 ? Math.round(windSpeeds.reduce((a, b) => a + b, 0) / windSpeeds.length) : 0;
  const windDirs = day.hourly.filter((h) => h.windDirection != null).map((h) => h.windDirection!);
  const avgWindDir = windDirs.length > 0 ? Math.round(windDirs.reduce((a, b) => a + b, 0) / windDirs.length) : 0;
  const maxPrecipChance = Math.max(...day.hourly.map((h) => h.precipitation));
  const avgScore = Math.round(day.hourly.reduce((a, h) => a + h.fishScore, 0) / day.hourly.length);
  const maxHumidity = Math.max(...day.hourly.map((h) => h.humidity));
  const pressures = day.hourly.map((h) => h.pressure / 100); // Pa to hPa
  const avgPressure = Math.round(pressures.reduce((a, b) => a + b, 0) / pressures.length);
  const pressureTrend = pressures.length > 1 ? pressures[pressures.length - 1] - pressures[0] : 0;
  const maxUV = Math.max(...day.hourly.map((h) => h.uvIndex));
  return { hi, lo, avgWind, avgWindDir, maxPrecipChance, avgScore, maxHumidity, avgPressure, pressureTrend, maxUV };
}

function scoreGaugeColor(score: number): string {
  if (score >= 60) return '#2E7D32';
  if (score >= 45) return '#66BB6A';
  if (score >= 30) return '#FFA726';
  if (score >= 15) return '#FB8C00';
  return '#C44B4B';
}

function scoreLabel(score: number): string {
  if (score >= 60) return 'Great';
  if (score >= 45) return 'Good';
  if (score >= 30) return 'Fair';
  if (score >= 15) return 'Slow';
  return 'Poor';
}

function pressureTrendLabel(trend: number): string {
  if (trend > 1.5) return 'Rising';
  if (trend < -1.5) return 'Falling';
  return 'Steady';
}

function pressureTrendIcon(trend: number): string {
  if (trend > 1.5) return 'trending-up-outline';
  if (trend < -1.5) return 'trending-down-outline';
  return 'remove-outline';
}

function aqiLabel(aqi: number | undefined): string {
  if (aqi === undefined) return '-';
  if (aqi <= 50) return 'Good';
  if (aqi <= 100) return 'Moderate';
  if (aqi <= 150) return 'Unhealthy (Sens.)';
  if (aqi <= 200) return 'Unhealthy';
  return 'Very Unhealthy';
}

function aqiColor(aqi: number | undefined): string {
  if (aqi === undefined) return palette.textMuted;
  if (aqi <= 50) return '#4CAF50';
  if (aqi <= 100) return '#FDD835';
  if (aqi <= 150) return '#FB8C00';
  if (aqi <= 200) return '#E53935';
  return '#7B1FA2';
}

function moonPhaseIcon(phase: string): string {
  if (phase.includes('New')) return 'moon-outline';
  if (phase.includes('Full')) return 'moon';
  if (phase.includes('Waxing Crescent') || phase.includes('Waning Crescent')) return 'moon-outline';
  return 'moon';
}

// ── Bite Score Gauge (SVG arc) ───────────────────────────────────────────────

function BiteScoreGauge({ score, size = 130 }: { score: number; size?: number }) {
  const strokeWidth = 10;
  const radius = (size - strokeWidth) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * radius;
  // Arc from 225deg to -45deg (270deg total sweep, bottom-center gap)
  const sweepAngle = 270;
  const arcLength = (sweepAngle / 360) * circumference;
  const filledLength = (score / 100) * arcLength;
  const color = scoreGaugeColor(score);

  // Start angle: 135deg (bottom-left)
  const startAngle = 135;
  const endAngle = startAngle + sweepAngle;

  const polarToCartesian = (angle: number) => {
    const rad = (angle * Math.PI) / 180;
    return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
  };

  const start = polarToCartesian(startAngle);
  const end = polarToCartesian(endAngle);

  const largeArc = sweepAngle > 180 ? 1 : 0;
  const trackPath = `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;

  return (
    <View style={{ alignItems: 'center', justifyContent: 'center', width: size, height: size }}>
      <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* Track */}
        <Path
          d={trackPath}
          stroke={palette.borderLight}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
        />
        {/* Filled arc */}
        <Path
          d={trackPath}
          stroke={color}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${filledLength} ${arcLength - filledLength}`}
        />
      </Svg>
      {/* Center text */}
      <View style={StyleSheet.absoluteFill as any}>
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 4 }}>
          <Text style={{ fontSize: 36, fontWeight: '800', color }}>{score}</Text>
          <Text style={{ fontSize: 12, color: palette.textMuted, fontWeight: '600', marginTop: -2 }}>
            {scoreLabel(score)}
          </Text>
        </View>
      </View>
    </View>
  );
}

// ── Hourly Chip ─────────────────────────────────────────────────────────────

function HourlyChip({ data }: { data: HourlyData }) {
  const color = scoreGaugeColor(data.fishScore);
  return (
    <View style={[st.hourlyChip, data.isNow && st.hourlyChipNow]}>
      <Text style={[st.hourlyChipHour, data.isNow && { color: palette.accent, fontWeight: '800' }]}>
        {data.hour}
      </Text>
      <Ionicons name={data.conditionIcon as any} size={18} color={data.isNow ? palette.accent : palette.textMuted} />
      <View style={[st.hourlyScoreDot, { backgroundColor: color }]}>
        <Text style={st.hourlyScoreText}>{data.fishScore}</Text>
      </View>
      <Text style={st.hourlyTemp}>{data.airTemp}°</Text>
    </View>
  );
}

// ── Daily Forecast Row ──────────────────────────────────────────────────────

function DailyRow({ day, dayIndex, bite, isExpanded, onToggle }: {
  day: DayForecast;
  dayIndex: number;
  bite: BiteFC | null;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const today = new Date();
  const cardDate = new Date();
  cardDate.setDate(today.getDate() + dayIndex);

  const dayName = dayIndex === 0 ? 'Today' : dayIndex === 1 ? 'Tmrw' : day.dayLabel;
  const { hi, lo, avgWind, avgWindDir, maxPrecipChance, avgScore, maxHumidity, avgPressure, pressureTrend, maxUV } = computeDaySummary(day);
  const overallScore = bite?.overallRating ?? avgScore;
  const color = scoreGaugeColor(overallScore);
  const bestWindowStr = bite?.bestWindow?.label ?? null;
  const moonLabel = bite?.moonPhase ?? '';
  const sunriseStr = bite ? formatHour(Math.round(bite.sunrise)) : '';
  const sunsetStr = bite ? formatHour(Math.round(bite.sunset)) : '';

  // Score bar width (0-100 mapped to 0-60%)
  const barWidth = Math.max(8, (overallScore / 100) * 60);

  return (
    <Pressable onPress={onToggle} style={st.dailyRow}>
      {/* Main row */}
      <View style={st.dailyRowMain}>
        <View style={st.dailyDayCol}>
          <Text style={st.dailyDayName} numberOfLines={1}>
            {dayName}
          </Text>
          <Text style={st.dailyDate}>{cardDate.getDate()}</Text>
        </View>

        <Ionicons name={day.weatherIcon as any} size={20} color={palette.textMuted} style={{ width: 24 }} />

        <View style={st.dailyScoreBarCol}>
          <View style={[st.dailyScoreBar, { width: `${barWidth}%`, backgroundColor: color }]} />
          <Text style={[st.dailyScoreVal, { color }]}>{overallScore}</Text>
        </View>

        <View style={st.dailyTemps}>
          <Text style={st.dailyHi}>{hi}°</Text>
          <Text style={st.dailyLo}>{lo}°</Text>
        </View>

        <View style={st.dailyWindCol}>
          <Ionicons name="flag-outline" size={12} color={palette.textMuted} />
          <Text style={st.dailyWindText}>{avgWind}</Text>
        </View>

        <Ionicons
          name={isExpanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={palette.textDim}
        />
      </View>

      {/* Expanded detail */}
      {isExpanded && (
        <View style={st.dailyExpanded}>
          <View style={st.detailGrid}>
            <DetailChip icon="water-outline" label="Precip" value={maxPrecipChance > 0 ? `${maxPrecipChance.toFixed(1)}mm` : 'None'} />
            <DetailChip icon="speedometer-outline" label="Pressure" value={`${avgPressure} hPa`} />
            <DetailChip icon={pressureTrendIcon(pressureTrend)} label="Trend" value={pressureTrendLabel(pressureTrend)} />
            <DetailChip icon="water" label="Humidity" value={`${maxHumidity}%`} />
            <DetailChip icon="sunny-outline" label="UV Index" value={`${maxUV}`} />
            <DetailChip icon="flag-outline" label="Wind" value={`${avgWind} km/h ${windDirectionLabel(avgWindDir)}`} />
          </View>

          {/* Bite windows + moon/sun */}
          <View style={st.dailyChipsRow}>
            {bestWindowStr && (
              <View style={st.infoChip}>
                <Ionicons name="time-outline" size={11} color={color} />
                <Text style={[st.infoChipText, { color }]}>{bestWindowStr}</Text>
              </View>
            )}
            {moonLabel !== '' && (
              <View style={st.infoChip}>
                <Ionicons name={moonPhaseIcon(moonLabel) as any} size={11} color={palette.textMuted} />
                <Text style={st.infoChipText}>{moonLabel}</Text>
              </View>
            )}
            {sunriseStr !== '' && (
              <View style={st.infoChip}>
                <Ionicons name="sunny-outline" size={11} color="#FB8C00" />
                <Text style={st.infoChipText}>{sunriseStr} / {sunsetStr}</Text>
              </View>
            )}
          </View>
        </View>
      )}
    </Pressable>
  );
}

function DetailChip({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <View style={st.detailChip}>
      <Ionicons name={icon as any} size={13} color={palette.textMuted} />
      <View>
        <Text style={st.detailChipLabel}>{label}</Text>
        <Text style={st.detailChipValue}>{value}</Text>
      </View>
    </View>
  );
}

// ── Expandable Condition Card ───────────────────────────────────────────────

function ConditionCard({
  title,
  icon,
  children,
  defaultExpanded = false,
}: {
  title: string;
  icon: string;
  children: React.ReactNode;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <Pressable onPress={() => setExpanded(!expanded)} style={st.condCard}>
      <View style={st.condCardHeader}>
        <Ionicons name={icon as any} size={16} color={palette.accent} />
        <Text style={st.condCardTitle}>{title}</Text>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={palette.textDim} />
      </View>
      {expanded && <View style={st.condCardBody}>{children}</View>}
    </Pressable>
  );
}

// ── Skeleton ────────────────────────────────────────────────────────────────

function ForecastSkeleton() {
  return (
    <View style={st.screen}>
      <View style={{ alignItems: 'center', paddingTop: 24, gap: 12 }}>
        <SkeletonLoader width={130} height={130} borderRadius={65} />
        <SkeletonLoader width="60%" height={14} borderRadius={4} />
        <SkeletonLoader width="40%" height={12} borderRadius={3} />
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 20 }} contentContainerStyle={{ paddingHorizontal: 16, gap: 8 }}>
        {Array.from({ length: 8 }).map((_, i) => (
          <SkeletonLoader key={i} width={56} height={90} borderRadius={12} />
        ))}
      </ScrollView>
      <View style={{ padding: 16, gap: 8, marginTop: 12 }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonLoader key={i} width="100%" height={48} borderRadius={10} />
        ))}
      </View>
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function ForecastsScreen(_props: TabProps<'ForecastsTab'>) {
  const [weekForecast, setWeekForecast] = useState<DayForecast[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLocation, setSelectedLocation] = useState<FeatureLocation | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedDayIdx, setExpandedDayIdx] = useState<number | null>(null);

  // Fade-in animation
  const dataFadeAnim = useRef(new Animated.Value(0)).current;
  const hasAnimatedIn = useRef(false);

  // Inline insight state
  const [biteForecast, setBiteForecast] = useState<BiteFC | null>(null);
  const [weeklyBite, setWeeklyBite] = useState<BiteFC[]>([]);
  const [pressureInfo, setPressureInfo] = useState<PressureReading | null>(null);
  const [waterData, setWaterData] = useState<WaterInsightsDashboard | null>(null);
  const activeLat = selectedLocation?.lat ?? null;
  const activeLon = selectedLocation?.lon ?? null;

  // Load inline insights when location is known
  useEffect(() => {
    if (activeLat == null || activeLon == null) return;
    let cancelled = false;

    const bite = getDailyBiteForecast(activeLat, activeLon);
    const weekly = getWeeklyBiteForecast(activeLat, activeLon);
    const pressure = getCurrentPressure({ lat: activeLat, lon: activeLon });
    if (!cancelled) {
      setBiteForecast(bite);
      setWeeklyBite(weekly);
      setPressureInfo(pressure);
    }

    getWaterInsights(activeLat, activeLon).then((w) => {
      if (!cancelled) setWaterData(w);
    }).catch(() => {});

    return () => { cancelled = true; };
  }, [activeLat, activeLon]);

  const fetchForecast = useCallback(async (lat: number, lon: number, isRetry = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchOpenMeteoForecast(lat, lon);
      setWeekForecast(data);
    } catch (err: any) {
      const msg = err?.name === 'AbortError'
        ? 'Request timed out. Check your connection.'
        : 'Unable to load forecast. Pull down to retry.';
      setError(msg);

      if (!isRetry) {
        setTimeout(() => fetchForecast(lat, lon, true), 3000);
        return;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const handleRefresh = useCallback(async () => {
    if (activeLat == null || activeLon == null) return;
    setRefreshing(true);
    forecastCache = null;
    await fetchForecast(activeLat, activeLon);
    setRefreshing(false);
  }, [activeLat, activeLon, fetchForecast]);

  const applyLocation = useCallback(async (next: FeatureLocation) => {
    setSelectedLocation(next);
    await fetchForecast(next.lat, next.lon);
  }, [fetchForecast]);

  const handleUseCurrentLocation = useCallback(async () => {
    try {
      setRefreshing(true);
      const next = await getCurrentFeatureLocation({
        lat: 44.98,
        lon: -93.27,
        label: 'Default forecast area',
      });
      await applyLocation(next);
      setError(null);
    } catch (err: any) {
      setError(err?.message ?? 'Unable to get your current location.');
    } finally {
      setRefreshing(false);
    }
  }, [applyLocation]);

  const handleSearchLocation = useCallback(async (query: string) => {
    try {
      setRefreshing(true);
      const next = await searchFeatureLocation(query);
      await applyLocation(next);
      setError(null);
    } catch (err: any) {
      setError(err?.message ?? 'Unable to find that place.');
    } finally {
      setRefreshing(false);
    }
  }, [applyLocation]);

  useEffect(() => {
    (async () => {
      try {
        const next = await getCurrentFeatureLocation({
          lat: 44.98,
          lon: -93.27,
          label: 'Default forecast area',
        });
        setSelectedLocation(next);
        await fetchForecast(next.lat, next.lon);
      } catch (err: any) {
        setError(err?.message ?? 'Unable to load forecast location.');
        setLoading(false);
      }
    })();
  }, [fetchForecast]);

  // Fade in data
  useEffect(() => {
    if (weekForecast.length > 0 && !hasAnimatedIn.current) {
      hasAnimatedIn.current = true;
      Animated.timing(dataFadeAnim, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }).start();
    }
  }, [weekForecast.length, dataFadeAnim]);

  // Derive current data
  const todayForecast = weekForecast[0];
  const todayHourly = todayForecast?.hourly ?? [];
  const nowData = todayHourly.find((h) => h.isNow) ?? todayHourly[0];
  const todaySummary = todayForecast ? computeDaySummary(todayForecast) : null;
  const currentScore = biteForecast?.overallRating ?? todaySummary?.avgScore ?? 0;

  // Build next 12 hours from now
  const nowIdx = todayHourly.findIndex((h) => h.isNow);
  const upcomingHours: HourlyData[] = [];
  if (nowIdx >= 0) {
    for (let i = nowIdx; i < todayHourly.length && upcomingHours.length < 12; i++) {
      upcomingHours.push(todayHourly[i]);
    }
    // If we need more, pull from tomorrow
    if (upcomingHours.length < 12 && weekForecast[1]) {
      const remaining = 12 - upcomingHours.length;
      for (let i = 0; i < Math.min(remaining, weekForecast[1].hourly.length); i++) {
        upcomingHours.push(weekForecast[1].hourly[i]);
      }
    }
  } else if (todayHourly.length > 0) {
    for (let i = 0; i < Math.min(12, todayHourly.length); i++) {
      upcomingHours.push(todayHourly[i]);
    }
  }

  // Loading state
  if (loading && weekForecast.length === 0) {
    return <ForecastSkeleton />;
  }

  // Error state
  if (error && weekForecast.length === 0) {
    return (
      <View style={[st.screen, st.centerContent]}>
        <Ionicons name="cloud-offline-outline" size={48} color={palette.textDim} />
        <Text style={st.errorTitle}>{error}</Text>
        <Pressable
          style={st.retryButton}
          onPress={() => activeLat != null && activeLon != null && fetchForecast(activeLat, activeLon)}
        >
          <Text style={st.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <Animated.View style={[st.screen, { opacity: dataFadeAnim }]}>
      <ScrollView
        style={{ flex: 1 }}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 100 }}
      >
        {/* ── Top: Score Gauge + Summary ──────────────────────────── */}
        <View style={st.heroSection}>
          <FeatureLocationPicker
            label={selectedLocation?.label ?? 'Loading location'}
            helperText={
              selectedLocation?.source === 'search'
                ? 'Forecast, bite windows, and water insights are using the place you searched for.'
                : 'Forecast, bite windows, and water insights are using this location.'
            }
            loading={refreshing}
            activeSource={selectedLocation?.source}
            onUseCurrent={handleUseCurrentLocation}
            onSearchLocation={handleSearchLocation}
          />
          <View style={st.heroRow}>
            <BiteScoreGauge score={currentScore} />
            <View style={st.heroSummary}>
              <View style={st.locationRow}>
                <Ionicons name="location-outline" size={14} color={palette.accent} />
                <Text style={st.locationText} numberOfLines={1}>
                  {selectedLocation?.label ?? 'Current Location'}
                </Text>
                <Pressable onPress={handleRefresh} hitSlop={12}>
                  {refreshing ? (
                    <ActivityIndicator size="small" color={palette.accent} />
                  ) : (
                    <Ionicons name="refresh-outline" size={16} color={palette.textMuted} />
                  )}
                </Pressable>
              </View>
              {nowData && (
                <>
                  <Text style={st.heroCondition}>{nowData.condition}</Text>
                  <Text style={st.heroTemp}>{nowData.airTemp}°C</Text>
                  <Text style={st.heroWind}>
                    {nowData.windSpeed ?? 0} km/h {nowData.windDirection != null ? windDirectionLabel(nowData.windDirection) : ''}
                  </Text>
                </>
              )}
              {biteForecast?.bestWindow && (
                <View style={st.heroBestTime}>
                  <Ionicons name="time-outline" size={11} color="#2E7D32" />
                  <Text style={st.heroBestTimeText}>
                    Best: {biteForecast.bestWindow.label}
                  </Text>
                </View>
              )}
            </View>
          </View>
        </View>

        {/* Error banner over cached data */}
        {error && weekForecast.length > 0 && (
          <View style={st.errorBanner}>
            <Ionicons name="warning-outline" size={14} color={palette.warning} />
            <Text style={st.errorBannerText}>Showing cached data. {error}</Text>
          </View>
        )}

        {/* ── Hourly Scroll ──────────────────────────────────────── */}
        {upcomingHours.length > 0 && (
          <View style={st.sectionWrap}>
            <Text style={st.sectionTitle}>Next 12 Hours</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={st.hourlyScroll}
            >
              {upcomingHours.map((h, i) => (
                <HourlyChip key={i} data={h} />
              ))}
            </ScrollView>
          </View>
        )}

        {/* ── 7-Day Forecast ─────────────────────────────────────── */}
        {weekForecast.length > 0 && (
          <View style={st.sectionWrap}>
            <Text style={st.sectionTitle}>
              {weekForecast.length > 7 ? `${weekForecast.length}-Day Forecast` : '7-Day Forecast'}
            </Text>
            <View style={st.dailyList}>
              {weekForecast.slice(0, 14).map((day, i) => (
                <DailyRow
                  key={i}
                  day={day}
                  dayIndex={i}
                  bite={weeklyBite[i] ?? null}
                  isExpanded={expandedDayIdx === i}
                  onToggle={() => setExpandedDayIdx(expandedDayIdx === i ? null : i)}
                />
              ))}
            </View>
          </View>
        )}

        {/* ── Condition Detail Cards ──────────────────────────────── */}

        {/* Water Conditions */}
        {waterData && waterData.insights.length > 0 && (
          <ConditionCard title="Water Conditions" icon="water-outline" defaultExpanded>
            <View style={st.detailGrid}>
              {waterData.insights.slice(0, 6).map((insight) => (
                <View key={insight.label} style={st.detailChip}>
                  <Ionicons name={insight.icon as any} size={13} color={insight.color} />
                  <View>
                    <Text style={st.detailChipLabel}>{insight.label}</Text>
                    <Text style={[st.detailChipValue, { color: insight.color }]}>
                      {insight.value}{insight.unit}
                    </Text>
                  </View>
                </View>
              ))}
            </View>
            {waterData.fishingImpact ? (
              <Text style={st.condCardDetail}>{waterData.fishingImpact}</Text>
            ) : null}
          </ConditionCard>
        )}

        {/* Weather / Pressure */}
        {todaySummary && (
          <ConditionCard title="Weather" icon="partly-sunny-outline">
            <View style={st.detailGrid}>
              <DetailChip icon="speedometer-outline" label="Pressure" value={`${todaySummary.avgPressure} hPa`} />
              <DetailChip icon={pressureTrendIcon(todaySummary.pressureTrend)} label="Trend" value={pressureTrendLabel(todaySummary.pressureTrend)} />
              <DetailChip icon="water" label="Humidity" value={`${todaySummary.maxHumidity}%`} />
              <DetailChip icon="sunny-outline" label="UV Max" value={`${todaySummary.maxUV}`} />
              <DetailChip icon="eye-outline" label="Visibility" value={nowData ? `${nowData.visibility} km` : '-'} />
              <DetailChip icon="cloud-outline" label="Cloud" value={nowData ? `${nowData.cloudCover}%` : '-'} />
            </View>
            {nowData?.aqi !== undefined && (
              <View style={st.aqiRow}>
                <Ionicons name="leaf-outline" size={13} color={aqiColor(nowData.aqi)} />
                <Text style={st.detailChipLabel}>Air Quality</Text>
                <Text style={[st.detailChipValue, { color: aqiColor(nowData.aqi) }]}>
                  {nowData.aqi} - {aqiLabel(nowData.aqi)}
                </Text>
              </View>
            )}
          </ConditionCard>
        )}

        {/* Moon / Sun / Solunar */}
        {biteForecast && (
          <ConditionCard title="Moon & Sun" icon="moon-outline">
            <View style={st.detailGrid}>
              {biteForecast.moonPhase !== '' && (
                <DetailChip icon={moonPhaseIcon(biteForecast.moonPhase)} label="Moon" value={biteForecast.moonPhase} />
              )}
              <DetailChip icon="sunny-outline" label="Sunrise" value={formatHour(Math.round(biteForecast.sunrise))} />
              <DetailChip icon="moon-outline" label="Sunset" value={formatHour(Math.round(biteForecast.sunset))} />
            </View>
          </ConditionCard>
        )}

        {/* Best Fishing Windows */}
        {biteForecast && biteForecast.windows.length > 0 && (
          <ConditionCard title="Bite Windows" icon="fish-outline" defaultExpanded>
            {/* Hour timeline bar */}
            <View style={st.timelineBar}>
              {biteForecast.hourlyScores.map((score, h) => (
                <View
                  key={h}
                  style={[
                    st.timelineSegment,
                    {
                      backgroundColor:
                        score >= 60 ? '#2E7D32'
                        : score >= 45 ? '#66BB6A'
                        : score >= 30 ? '#FFA726'
                        : 'rgba(0,0,0,0.06)',
                    },
                  ]}
                />
              ))}
            </View>
            <View style={st.timelineLabels}>
              <Text style={st.timelineLabel}>12A</Text>
              <Text style={st.timelineLabel}>6A</Text>
              <Text style={st.timelineLabel}>12P</Text>
              <Text style={st.timelineLabel}>6P</Text>
              <Text style={st.timelineLabel}>12A</Text>
            </View>
            <View style={st.windowPills}>
              {biteForecast.windows.slice(0, 3).map((w, i) => {
                const qualityColor = w.quality === 'prime' ? '#2E7D32' : w.quality === 'good' ? '#66BB6A' : '#FFA726';
                return (
                  <View key={i} style={[st.windowPill, { borderColor: qualityColor + '40' }]}>
                    <View style={[st.windowDot, { backgroundColor: qualityColor }]} />
                    <Text style={st.windowLabel}>{w.label}</Text>
                    <Text style={[st.windowQuality, { color: qualityColor }]}>
                      {w.quality.charAt(0).toUpperCase() + w.quality.slice(1)}
                    </Text>
                  </View>
                );
              })}
            </View>
            {biteForecast.windows[0]?.reasons?.length > 0 && (
              <Text style={st.condCardDetail}>
                {biteForecast.windows[0].reasons.join(' \u00B7 ')}
              </Text>
            )}
          </ConditionCard>
        )}

        {/* Fishing Pressure */}
        {pressureInfo && (
          <ConditionCard title="Fishing Pressure" icon="people-outline">
            <View style={st.pressureRow}>
              <Ionicons name={pressureInfo.icon as any} size={18} color={pressureInfo.color} />
              <View style={{ flex: 1 }}>
                <Text style={[st.pressureLevel, { color: pressureInfo.color }]}>
                  {pressureInfo.label}
                </Text>
                <Text style={st.condCardDetail}>{pressureInfo.description}</Text>
              </View>
            </View>
            {pressureInfo.peakHours.length > 0 && (
              <Text style={st.condCardDetail}>
                Peak hours: {pressureInfo.peakHours.join(', ')}
              </Text>
            )}
          </ConditionCard>
        )}
      </ScrollView>
    </Animated.View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const st = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  centerContent: {
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
    padding: 24,
  },
  errorTitle: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
    textAlign: 'center',
    marginTop: 8,
  },
  retryButton: {
    marginTop: 12,
    paddingHorizontal: 24,
    paddingVertical: 10,
    backgroundColor: palette.accent,
    borderRadius: 8,
  },
  retryText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#FFF3E0',
  },
  errorBannerText: {
    color: palette.textSecondary,
    fontSize: 12,
    flex: 1,
  },

  // ── Hero Section ──────────────────────────────────────────────
  heroSection: {
    paddingTop: 20,
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  heroRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
  },
  heroSummary: {
    flex: 1,
    gap: 3,
  },
  locationRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginBottom: 4,
  },
  locationText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    fontWeight: '600',
  },
  heroCondition: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  heroTemp: {
    fontSize: 28,
    fontWeight: '800',
    color: palette.text,
    letterSpacing: -0.5,
  },
  heroWind: {
    fontSize: 12,
    color: palette.textMuted,
    fontWeight: '500',
  },
  heroBestTime: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
    backgroundColor: 'rgba(46,125,50,0.08)',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    alignSelf: 'flex-start',
  },
  heroBestTimeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#2E7D32',
  },

  // ── Sections ──────────────────────────────────────────────────
  sectionWrap: {
    marginTop: 16,
  },
  sectionTitle: {
    fontFamily: fonts.serif,
    fontSize: 17,
    color: palette.text,
    paddingHorizontal: 16,
    marginBottom: 10,
  },

  // ── Hourly Scroll ─────────────────────────────────────────────
  hourlyScroll: {
    paddingHorizontal: 12,
    gap: 6,
  },
  hourlyChip: {
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 14,
    paddingVertical: 10,
    paddingHorizontal: 8,
    width: 56,
    gap: 5,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  hourlyChipNow: {
    backgroundColor: palette.accentLight,
    borderWidth: 1,
    borderColor: palette.accent + '30',
  },
  hourlyChipHour: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  hourlyScoreDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  hourlyScoreText: {
    fontSize: 11,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  hourlyTemp: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
  },

  // ── Daily List ────────────────────────────────────────────────
  dailyList: {
    marginHorizontal: 12,
    backgroundColor: palette.surface,
    borderRadius: 14,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 1,
  },
  dailyRow: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  dailyRowMain: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 14,
    gap: 8,
  },
  dailyDayCol: {
    width: 48,
    alignItems: 'flex-start',
  },
  dailyDayName: {
    width: '100%',
    fontSize: 11,
    fontWeight: '700',
    color: palette.text,
  },
  dailyDate: {
    fontSize: 10,
    color: palette.textMuted,
    fontWeight: '500',
    textAlign: 'left',
  },
  dailyScoreBarCol: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  dailyScoreBar: {
    height: 6,
    borderRadius: 3,
    minWidth: 8,
  },
  dailyScoreVal: {
    fontSize: 13,
    fontWeight: '800',
  },
  dailyTemps: {
    flexDirection: 'row',
    gap: 4,
    width: 52,
    justifyContent: 'flex-end',
  },
  dailyHi: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
  },
  dailyLo: {
    fontSize: 13,
    fontWeight: '500',
    color: palette.textMuted,
  },
  dailyWindCol: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
    width: 32,
  },
  dailyWindText: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '600',
  },

  // ── Daily Expanded ────────────────────────────────────────────
  dailyExpanded: {
    paddingHorizontal: 14,
    paddingBottom: 12,
    gap: 8,
  },
  dailyChipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  infoChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  infoChipText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textSecondary,
  },

  // ── Detail Grid ───────────────────────────────────────────────
  detailGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  detailChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 8,
  },
  detailChipLabel: {
    fontSize: 10,
    color: palette.textMuted,
    fontWeight: '500',
  },
  detailChipValue: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
  },

  // ── Condition Cards ───────────────────────────────────────────
  condCard: {
    backgroundColor: palette.surface,
    marginHorizontal: 12,
    marginTop: 12,
    borderRadius: 14,
    padding: 14,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 1,
  },
  condCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  condCardTitle: {
    flex: 1,
    fontFamily: fonts.serif,
    fontSize: 15,
    color: palette.text,
  },
  condCardBody: {
    marginTop: 12,
    gap: 10,
  },
  condCardDetail: {
    fontSize: 12,
    color: palette.textMuted,
    lineHeight: 16,
  },

  // ── AQI Row ───────────────────────────────────────────────────
  aqiRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 4,
  },

  // ── Timeline ──────────────────────────────────────────────────
  timelineBar: {
    flexDirection: 'row',
    height: 14,
    borderRadius: 7,
    overflow: 'hidden',
    gap: 1,
  },
  timelineSegment: {
    flex: 1,
    borderRadius: 2,
  },
  timelineLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: 2,
  },
  timelineLabel: {
    fontSize: 9,
    color: palette.textDim,
    fontWeight: '600',
  },

  // ── Window Pills ──────────────────────────────────────────────
  windowPills: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  windowPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 10,
    borderWidth: 1,
    backgroundColor: palette.surfaceRaised,
  },
  windowDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  windowLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
  },
  windowQuality: {
    fontSize: 11,
    fontWeight: '700',
  },

  // ── Pressure Row ──────────────────────────────────────────────
  pressureRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  pressureLevel: {
    fontSize: 15,
    fontWeight: '700',
  },
});
