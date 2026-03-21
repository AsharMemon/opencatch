import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
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
import { palette, getConditionBand, conditionConfig, scoreColor } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { getDailyBiteForecast, getWeeklyBiteForecast, formatHour, type DailyBiteForecast as BiteFC, type TimeWindow } from '../services/bestTimeWindows';
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

function conditionFromCloud(cloud: number): { label: string; icon: string } {
  if (cloud < 20) return { label: 'Clear/Sunny', icon: 'sunny-outline' };
  if (cloud < 50) return { label: 'Partly Cloudy', icon: 'partly-sunny-outline' };
  if (cloud < 80) return { label: 'Mostly Cloudy', icon: 'cloudy-outline' };
  return { label: 'Overcast', icon: 'cloudy-outline' };
}

/**
 * Compute a simple fish activity score from weather conditions.
 * Higher during dawn/dusk, stable pressure, moderate temps.
 */
function computeFishScore(hour: number, temp: number, pressure: number, cloudCover: number, windSpeed: number): number {
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
  return Math.max(5, Math.min(95, score));
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

      const isToday = dateStr === todayStr;
      const isNow = isToday && hour === currentHour;
      const hourLabel = isNow ? 'Now' : String(hour).padStart(2, '0');

      const cond = conditionFromCloud(cloud);
      const fishScore = computeFishScore(hour, temp, pressureHpa, cloud, windSpeed);

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

    // Update cache
    forecastCache = { data: forecasts, lat, lon, timestamp: Date.now() };

    return forecasts;
  } catch (err) {
    clearTimeout(timeoutId);
    // Return cached data if available, even if stale
    if (forecastCache?.data) {
      return forecastCache.data;
    }
    throw err;
  }
}

// ── Row definitions — matches FishAngler layout ─────────────────────────────

type RowKey = 'timezone' | 'fishForecast' | 'conditions' | 'cloudCover' | 'visibility' |
              'airTemp' | 'pressure' | 'precipitation' | 'precAccum' | 'snowAccum' |
              'humidity' | 'uvIndex';

interface RowConfig {
  key: RowKey;
  icon: string;
  label: string;
  sublabel?: string;
  getValue: (h: HourlyData) => string | number;
  getColor?: (h: HourlyData) => string | undefined;
  getBgColor?: (h: HourlyData) => string | undefined;
  renderCustom?: (h: HourlyData) => React.ReactNode;
  height?: number;
}

function fishScoreBg(score: number): string {
  if (score >= 50) return '#4A6741';  // Dark olive green
  if (score >= 30) return '#6B7F44';  // Olive
  if (score >= 15) return '#8B9550';  // Muted olive
  return '#5A5A50';                    // Gray-olive
}

function fishScoreText(score: number): string {
  return '#FFFFFF';
}

function tempBg(temp: number): string {
  if (temp >= 35) return '#E53935';
  if (temp >= 30) return '#FB8C00';
  if (temp >= 25) return '#FDD835';
  if (temp >= 20) return '#C8E6C9';
  if (temp >= 15) return '#B3E5FC';
  return '#90CAF9';
}

function tempText(temp: number): string {
  if (temp >= 30) return '#FFFFFF';
  return '#1A1A18';
}

function pressureBg(_p: number): string {
  return '#4DB6AC';  // Teal
}

function uvBg(uv: number): string {
  if (uv >= 8) return '#E53935';
  if (uv >= 6) return '#FB8C00';
  if (uv >= 3) return '#FDD835';
  if (uv >= 1) return '#A5D6A7';
  return 'transparent';
}

function uvText(uv: number): string {
  if (uv >= 6) return '#FFFFFF';
  return '#1A1A18';
}

const ROWS: RowConfig[] = [
  {
    key: 'timezone',
    icon: 'time-outline',
    label: 'Time · 1 Hour',
    getValue: (h) => h.hour,
    height: 32,
  },
  {
    key: 'fishForecast',
    icon: 'fish-outline',
    label: 'Fish Forecast',
    getValue: (h) => `${h.fishScore}%`,
    getBgColor: (h) => fishScoreBg(h.fishScore),
    getColor: () => '#FFFFFF',
    height: 48,
  },
  {
    key: 'conditions',
    icon: 'partly-sunny-outline',
    label: 'Conditions',
    sublabel: '',
    getValue: () => '',
    renderCustom: (h) => (
      <Ionicons name={h.conditionIcon as any} size={20} color="#FB8C00" />
    ),
    height: 48,
  },
  {
    key: 'cloudCover',
    icon: 'cloud-outline',
    label: 'Cloud Cover (%)',
    getValue: (h) => `${h.cloudCover}%`,
  },
  {
    key: 'visibility',
    icon: 'eye-outline',
    label: 'Visibility (km)',
    getValue: (h) => `${h.visibility}`,
  },
  {
    key: 'airTemp',
    icon: 'thermometer-outline',
    label: 'Air Temp (°C)',
    getValue: (h) => `${h.airTemp}°`,
    getBgColor: (h) => tempBg(h.airTemp),
    getColor: (h) => tempText(h.airTemp),
  },
  {
    key: 'pressure',
    icon: 'speedometer-outline',
    label: 'Air Pressure (Pa)',
    getValue: (h) => `${h.pressure}`,
    getBgColor: () => pressureBg(0),
    getColor: () => '#FFFFFF',
    height: 48,
  },
  {
    key: 'precipitation',
    icon: 'rainy-outline',
    label: 'Precipitation (mm)',
    getValue: (h) => h.precipitation > 0 ? `${h.precipitation}` : '-',
  },
  {
    key: 'precAccum',
    icon: 'water-outline',
    label: 'Prec Accum (mm)',
    getValue: (h) => h.precAccum > 0 ? `${h.precAccum}` : '-',
  },
  {
    key: 'snowAccum',
    icon: 'snow-outline',
    label: 'Snow Accum (cm)',
    getValue: (h) => h.snowAccum > 0 ? `${h.snowAccum}` : '-',
  },
  {
    key: 'humidity',
    icon: 'water',
    label: 'Humidity (%)',
    getValue: (h) => `${h.humidity}%`,
  },
  {
    key: 'uvIndex',
    icon: 'sunny-outline',
    label: 'UV Index',
    getValue: (h) => `${h.uvIndex}`,
    getBgColor: (h) => uvBg(h.uvIndex),
    getColor: (h) => uvText(h.uvIndex),
  },
];

// ── Column widths ───────────────────────────────────────────────────────────

const LABEL_COL_WIDTH = 140;
const DATA_COL_WIDTH = 72;
const ROW_HEIGHT = 36;

// ── Sub-components ──────────────────────────────────────────────────────────

function DaySelector({
  days,
  selectedIdx,
  onSelect,
}: {
  days: DayForecast[];
  selectedIdx: number;
  onSelect: (i: number) => void;
}) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={s.dayRow}
    >
      {days.map((d, i) => {
        const isActive = i === selectedIdx;
        return (
          <Pressable
            key={i}
            style={[s.dayPill, isActive && s.dayPillActive]}
            onPress={() => onSelect(i)}
          >
            <Text style={[s.dayPillLabel, isActive && s.dayPillLabelActive]}>
              {d.dayLabel}
            </Text>
            <Text style={[s.dayPillDate, isActive && s.dayPillDateActive]}>
              {d.date}
            </Text>
            <Ionicons
              name={d.weatherIcon as any}
              size={18}
              color={isActive ? '#FB8C00' : palette.textMuted}
            />
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

// Pressure sparkline between the pressure value row and the next row
function PressureSparkline({ hourly }: { hourly: HourlyData[] }) {
  const w = hourly.length * DATA_COL_WIDTH;
  const h = 24;
  const values = hourly.map((hr) => hr.pressure);
  const min = Math.min(...values) - 50;
  const max = Math.max(...values) + 50;

  const pts = values.map((v, i) => {
    const x = i * DATA_COL_WIDTH + DATA_COL_WIDTH / 2;
    const y = h - ((v - min) / (max - min || 1)) * (h - 4) - 2;
    return { x, y };
  });

  const pathD = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  return (
    <View style={s.sparklineRow}>
      <View style={{ width: LABEL_COL_WIDTH }} />
      <Svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <Path
          d={pathD}
          stroke="#FB8C00"
          strokeWidth={2}
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {pts.map((p, i) => (
          <Circle key={i} cx={p.x} cy={p.y} r={3} fill="#FB8C00" />
        ))}
      </Svg>
    </View>
  );
}

// Single data row in the grid
function DataRow({ row, hourly }: { row: RowConfig; hourly: HourlyData[] }) {
  const height = row.height || ROW_HEIGHT;

  return (
    <View style={[s.dataRow, { height }]}>
      {/* Fixed label column */}
      <View style={[s.labelCell, { height }]}>
        <Ionicons name={row.icon as any} size={14} color={palette.textSecondary} />
        <View style={{ flex: 1 }}>
          <Text style={s.labelText} numberOfLines={1}>{row.label}</Text>
          {row.sublabel ? (
            <Text style={s.sublabelText}>{row.sublabel}</Text>
          ) : null}
        </View>
      </View>

      {/* Scrollable data cells — these scroll together */}
      {hourly.map((h, i) => {
        const bgColor = row.getBgColor?.(h);
        const textColor = row.getColor?.(h) || palette.text;
        const value = row.getValue(h);

        return (
          <View
            key={i}
            style={[
              s.dataCell,
              { width: DATA_COL_WIDTH, height },
              bgColor ? { backgroundColor: bgColor } : null,
              h.isNow ? s.nowColumnHighlight : null,
            ]}
          >
            {row.renderCustom ? (
              row.renderCustom(h)
            ) : (
              <Text
                style={[
                  s.dataCellText,
                  { color: textColor },
                  row.key === 'fishForecast' ? s.fishScoreText : null,
                ]}
                numberOfLines={1}
              >
                {value}
              </Text>
            )}
          </View>
        );
      })}
    </View>
  );
}

// Hour header row
function HourHeaderRow({ hourly }: { hourly: HourlyData[] }) {
  return (
    <View style={[s.dataRow, { height: 28 }]}>
      <View style={[s.labelCell, { height: 28 }]}>
        <Text style={s.hourHeaderLabel}>Hour</Text>
      </View>
      {hourly.map((h, i) => (
        <View
          key={i}
          style={[
            s.hourHeaderCell,
            h.isNow ? s.nowColumnHeader : null,
          ]}
        >
          {h.isNow ? (
            <>
              <Text style={s.nowLabel}>Now</Text>
              <Ionicons name="caret-down" size={8} color={palette.accent} />
            </>
          ) : (
            <Text style={s.hourLabel}>{h.hour}</Text>
          )}
        </View>
      ))}
    </View>
  );
}

// ── Helpers for Extended Outlook ──────────────────────────────────────────

const DAYS_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function windDirectionLabel(deg: number): string {
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(deg / 45) % 8];
}

function moonPhaseIcon(phase: string): string {
  if (phase.includes('New')) return 'moon-outline';
  if (phase.includes('Full')) return 'moon';
  if (phase.includes('Waxing Crescent') || phase.includes('Waning Crescent')) return 'moon-outline';
  return 'moon';
}

function dayQualityColor(score: number): string {
  if (score >= 55) return '#2E7D32';  // green — great
  if (score >= 35) return '#C4841D';  // amber — fair
  return '#C44B4B';                   // red — poor
}

function dayQualityBg(score: number): string {
  if (score >= 55) return 'rgba(46,125,50,0.06)';
  if (score >= 35) return 'rgba(196,132,29,0.06)';
  return 'rgba(196,75,75,0.06)';
}

// Compute summary stats for a day from its hourly data
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
  return { hi, lo, avgWind, avgWindDir, maxPrecipChance, avgScore };
}

function DayOutlookCard({
  day,
  dayIndex,
  bite,
}: {
  day: DayForecast;
  dayIndex: number;
  bite: BiteFC | null;
}) {
  const today = new Date();
  const cardDate = new Date();
  cardDate.setDate(today.getDate() + dayIndex);

  const dayName = dayIndex === 0 ? 'Today' : dayIndex === 1 ? 'Tomorrow' : DAYS_FULL[cardDate.getDay()];
  const dateStr = `${MONTHS_SHORT[cardDate.getMonth()]} ${cardDate.getDate()}`;

  const { hi, lo, avgWind, avgWindDir, maxPrecipChance, avgScore } = computeDaySummary(day);
  const overallScore = bite?.overallRating ?? avgScore;
  const qColor = dayQualityColor(overallScore);
  const qBg = dayQualityBg(overallScore);

  const bestWindowStr = bite?.bestWindow?.label ?? null;
  const moonLabel = bite?.moonPhase ?? '';
  const sunriseStr = bite ? formatHour(Math.round(bite.sunrise)) : '';
  const sunsetStr = bite ? formatHour(Math.round(bite.sunset)) : '';

  return (
    <View style={[os.card, { backgroundColor: qBg, borderLeftColor: qColor }]}>
      {/* Top row: day + date + score */}
      <View style={os.cardHeader}>
        <View style={{ flex: 1 }}>
          <Text style={os.dayName}>{dayName}</Text>
          <Text style={os.dateStr}>{dateStr}</Text>
        </View>
        <View style={[os.scoreBadge, { backgroundColor: qColor }]}>
          <Ionicons name="fish-outline" size={14} color="#FFF" />
          <Text style={os.scoreText}>{overallScore}%</Text>
        </View>
      </View>

      {/* Middle row: weather icon + temps + wind + precip */}
      <View style={os.metricsRow}>
        <View style={os.metric}>
          <Ionicons name={day.weatherIcon as any} size={22} color="#FB8C00" />
          <Text style={os.metricValue}>{hi}° / {lo}°</Text>
          <Text style={os.metricLabel}>Hi / Lo</Text>
        </View>

        <View style={os.metric}>
          <Ionicons name="flag-outline" size={18} color={palette.textSecondary} />
          <Text style={os.metricValue}>{avgWind} km/h {windDirectionLabel(avgWindDir)}</Text>
          <Text style={os.metricLabel}>Wind</Text>
        </View>

        <View style={os.metric}>
          <Ionicons name="rainy-outline" size={18} color={palette.textSecondary} />
          <Text style={os.metricValue}>{maxPrecipChance > 0 ? `${maxPrecipChance.toFixed(1)} mm` : 'None'}</Text>
          <Text style={os.metricLabel}>Precip</Text>
        </View>
      </View>

      {/* Bottom row: best window + moon + sunrise/sunset */}
      <View style={os.bottomRow}>
        {bestWindowStr && (
          <View style={os.chip}>
            <Ionicons name="time-outline" size={12} color={qColor} />
            <Text style={[os.chipText, { color: qColor }]}>{bestWindowStr}</Text>
          </View>
        )}

        {moonLabel !== '' && (
          <View style={os.chip}>
            <Ionicons name={moonPhaseIcon(moonLabel) as any} size={12} color={palette.textMuted} />
            <Text style={os.chipText}>{moonLabel}</Text>
          </View>
        )}

        {sunriseStr !== '' && (
          <View style={os.chip}>
            <Ionicons name="sunny-outline" size={12} color="#FB8C00" />
            <Text style={os.chipText}>{sunriseStr} / {sunsetStr}</Text>
          </View>
        )}
      </View>
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function ForecastsScreen(_props: TabProps<'ForecastsTab'>) {
  const [selectedDayIdx, setSelectedDayIdx] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>('Daily');
  const [weekForecast, setWeekForecast] = useState<DayForecast[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [userLat, setUserLat] = useState<number | null>(null);
  const [userLon, setUserLon] = useState<number | null>(null);
  const scrollRef = useRef<ScrollView>(null);

  // Inline insight state
  const [biteForecast, setBiteForecast] = useState<BiteFC | null>(null);
  const [weeklyBite, setWeeklyBite] = useState<BiteFC[]>([]);
  const [pressureInfo, setPressureInfo] = useState<PressureReading | null>(null);
  const [waterData, setWaterData] = useState<WaterInsightsDashboard | null>(null);

  // Load inline insights when location is known
  useEffect(() => {
    if (userLat == null || userLon == null) return;
    let cancelled = false;

    const bite = getDailyBiteForecast(userLat, userLon);
    const weekly = getWeeklyBiteForecast(userLat, userLon);
    const pressure = getCurrentPressure({ lat: userLat, lon: userLon });
    if (!cancelled) {
      setBiteForecast(bite);
      setWeeklyBite(weekly);
      setPressureInfo(pressure);
    }

    getWaterInsights(userLat, userLon).then((w) => {
      if (!cancelled) setWaterData(w);
    }).catch(() => {});

    return () => { cancelled = true; };
  }, [userLat, userLon]);

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

      // Auto-retry once on network failure
      if (!isRetry) {
        setTimeout(() => fetchForecast(lat, lon, true), 3000);
        return;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') {
          setError('Location permission needed for forecast.');
          setLoading(false);
          return;
        }
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        setUserLat(loc.coords.latitude);
        setUserLon(loc.coords.longitude);
        await fetchForecast(loc.coords.latitude, loc.coords.longitude);
      } catch {
        setError('Unable to get your location. Please enable GPS.');
        setLoading(false);
      }
    })();
  }, [fetchForecast]);

  const selectedDay = weekForecast[selectedDayIdx];
  const hourly = selectedDay?.hourly ?? [];

  // Scroll to "Now" column on mount
  const handleScrollLayout = () => {
    const nowIdx = hourly.findIndex((h) => h.isNow);
    if (nowIdx > 0 && scrollRef.current) {
      scrollRef.current.scrollTo({ x: Math.max(0, (nowIdx - 1) * DATA_COL_WIDTH), animated: false });
    }
  };

  // Loading state
  if (loading && weekForecast.length === 0) {
    return (
      <View style={[s.screen, s.centerContent]}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={s.loadingText}>Loading forecast...</Text>
      </View>
    );
  }

  // Error state with no cached data
  if (error && weekForecast.length === 0) {
    return (
      <View style={[s.screen, s.centerContent]}>
        <Ionicons name="cloud-offline-outline" size={48} color={palette.textDim} />
        <Text style={s.errorTitle}>{error}</Text>
        <Pressable
          style={s.retryButton}
          onPress={() => userLat != null && userLon != null && fetchForecast(userLat, userLon)}
        >
          <Text style={s.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={s.screen}>
      {/* Header */}
      <View style={s.header}>
        <View style={s.headerTop}>
          <Ionicons name="search-outline" size={22} color={palette.text} />
          <View style={s.headerCenter}>
            <Text style={s.headerTitle}>Map Location</Text>
            <Text style={s.headerCoords}>
              {userLat != null && userLon != null
                ? `${userLat.toFixed(6)}, ${userLon.toFixed(6)}`
                : 'Locating...'}
            </Text>
          </View>
          <Pressable onPress={() => userLat != null && userLon != null && fetchForecast(userLat, userLon)}>
            <Ionicons name="refresh-outline" size={22} color={palette.text} />
          </Pressable>
        </View>

        {/* Daily / Extended toggle */}
        <View style={s.modeToggle}>
          {([['Daily', 'Hourly'], ['Extended', '14-Day Extended']] as [ViewMode, string][]).map(([mode, label]) => (
            <Pressable
              key={mode}
              style={[s.modeToggleBtn, viewMode === mode && s.modeToggleBtnActive]}
              onPress={() => setViewMode(mode)}
            >
              <Text style={[s.modeToggleText, viewMode === mode && s.modeToggleTextActive]}>
                {label}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Day selector — only in hourly mode */}
        {viewMode === 'Daily' && weekForecast.length > 0 && (
          <DaySelector
            days={weekForecast}
            selectedIdx={selectedDayIdx}
            onSelect={setSelectedDayIdx}
          />
        )}
      </View>

      {/* Error banner (shown over cached data) */}
      {error && weekForecast.length > 0 && (
        <View style={s.errorBanner}>
          <Ionicons name="warning-outline" size={14} color={palette.warning} />
          <Text style={s.errorBannerText}>Showing cached data. {error}</Text>
        </View>
      )}

      {/* ── Extended 14-Day Outlook View ─────────────────────────────── */}
      {viewMode === 'Extended' && weekForecast.length > 0 && (
        <ScrollView
          style={s.verticalScroll}
          showsVerticalScrollIndicator={false}
          contentContainerStyle={{ padding: 12, paddingBottom: 100, gap: 10 }}
        >
          <Text style={os.sectionTitle}>14-Day Extended Forecast</Text>
          {weekForecast.map((day, i) => (
            <DayOutlookCard
              key={i}
              day={day}
              dayIndex={i}
              bite={weeklyBite[i] ?? null}
            />
          ))}
        </ScrollView>
      )}

      {/* ── Hourly Grid View (existing) ─────────────────────────────── */}
      {viewMode === 'Daily' && hourly.length > 0 && (
        <ScrollView
          style={s.verticalScroll}
          showsVerticalScrollIndicator={false}
        >
          <View style={{ flexDirection: 'row' }}>
            {/* Fixed left label column */}
            <View style={{ width: LABEL_COL_WIDTH, zIndex: 2, backgroundColor: palette.background }}>
              <View style={[s.labelCell, { height: 28 }]}>
                <Text style={s.hourHeaderLabel}>Hour</Text>
              </View>
              {ROWS.map((row) => (
                <React.Fragment key={row.key}>
                  <View style={[s.labelCell, { height: row.height || ROW_HEIGHT }]}>
                    <Ionicons name={row.icon as any} size={14} color={palette.textSecondary} />
                    <View style={{ flex: 1 }}>
                      <Text style={s.labelText} numberOfLines={1}>{row.label}</Text>
                      {row.sublabel ? <Text style={s.sublabelText}>{row.sublabel}</Text> : null}
                    </View>
                  </View>
                  {row.key === 'pressure' && <View style={[s.sparklineRow, { height: 24 }]} />}
                </React.Fragment>
              ))}
              <View style={{ height: 16 }} />
            </View>

            {/* Scrollable right data columns */}
            <ScrollView
              ref={scrollRef}
              horizontal
              showsHorizontalScrollIndicator={false}
              onLayout={handleScrollLayout}
              style={{ flex: 1 }}
              contentContainerStyle={{ width: hourly.length * DATA_COL_WIDTH }}
            >
              <View>
                {/* Hour header (data only, no label) */}
                <View style={[s.dataRow, { height: 28 }]}>
                  {hourly.map((h, i) => (
                    <View key={i} style={[s.hourHeaderCell, h.isNow ? s.nowColumnHeader : null]}>
                      {h.isNow ? (
                        <>
                          <Text style={s.nowLabel}>Now</Text>
                          <Ionicons name="caret-down" size={8} color={palette.accent} />
                        </>
                      ) : (
                        <Text style={s.hourLabel}>{h.hour}</Text>
                      )}
                    </View>
                  ))}
                </View>

                {/* Data rows (data cells only, no labels) */}
                {ROWS.map((row) => {
                  const height = row.height || ROW_HEIGHT;
                  return (
                    <React.Fragment key={row.key}>
                      <View style={[s.dataRow, { height }]}>
                        {hourly.map((h, i) => {
                          const bgColor = row.getBgColor?.(h);
                          const textColor = row.getColor?.(h) || palette.text;
                          const value = row.getValue(h);
                          return (
                            <View
                              key={i}
                              style={[s.dataCell, { width: DATA_COL_WIDTH, height }, bgColor ? { backgroundColor: bgColor } : null, h.isNow ? s.nowColumnHighlight : null]}
                            >
                              {row.renderCustom ? row.renderCustom(h) : (
                                <Text style={[s.dataCellText, { color: textColor }]} numberOfLines={1}>{value}</Text>
                              )}
                            </View>
                          );
                        })}
                      </View>
                      {row.key === 'pressure' && <PressureSparkline hourly={hourly} />}
                    </React.Fragment>
                  );
                })}

                <View style={{ height: 16 }} />
              </View>
            </ScrollView>
          </View>

          {/* ── Inline Insight Sections ─────────────────────────────────── */}

          {/* Best Fishing Windows Today */}
          {biteForecast && biteForecast.windows.length > 0 && (
            <View style={s.insightCard}>
              <Text style={s.insightTitle}>Best Fishing Windows Today</Text>
              {/* Hour timeline bar */}
              <View style={s.timelineBar}>
                {biteForecast.hourlyScores.map((score, h) => (
                  <View
                    key={h}
                    style={[
                      s.timelineSegment,
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
              <View style={s.timelineLabels}>
                <Text style={s.timelineLabel}>12A</Text>
                <Text style={s.timelineLabel}>6A</Text>
                <Text style={s.timelineLabel}>12P</Text>
                <Text style={s.timelineLabel}>6P</Text>
                <Text style={s.timelineLabel}>12A</Text>
              </View>
              {/* Window pills */}
              <View style={s.windowPills}>
                {biteForecast.windows.slice(0, 3).map((w, i) => {
                  const qualityColor = w.quality === 'prime' ? '#2E7D32' : w.quality === 'good' ? '#66BB6A' : '#FFA726';
                  return (
                    <View key={i} style={[s.windowPill, { borderColor: qualityColor + '40' }]}>
                      <View style={[s.windowDot, { backgroundColor: qualityColor }]} />
                      <Text style={s.windowLabel}>{w.label}</Text>
                      <Text style={[s.windowQuality, { color: qualityColor }]}>
                        {w.quality.charAt(0).toUpperCase() + w.quality.slice(1)}
                      </Text>
                    </View>
                  );
                })}
              </View>
              {biteForecast.windows[0]?.reasons?.length > 0 && (
                <Text style={s.insightDetail}>
                  {biteForecast.windows[0].reasons.join(' \u00B7 ')}
                </Text>
              )}
            </View>
          )}

          {/* Fishing Pressure */}
          {pressureInfo && (
            <View style={s.insightCard}>
              <Text style={s.insightTitle}>Fishing Pressure</Text>
              <View style={s.pressureRow}>
                <Ionicons name={pressureInfo.icon as any} size={20} color={pressureInfo.color} />
                <View style={{ flex: 1 }}>
                  <Text style={[s.pressureLevel, { color: pressureInfo.color }]}>
                    {pressureInfo.label}
                  </Text>
                  <Text style={s.pressureDesc}>{pressureInfo.description}</Text>
                </View>
              </View>
              {pressureInfo.peakHours.length > 0 && (
                <Text style={s.insightDetail}>
                  Peak hours: {pressureInfo.peakHours.join(', ')}
                </Text>
              )}
            </View>
          )}

          {/* Water Conditions */}
          {waterData && waterData.insights.length > 0 && (
            <View style={s.insightCard}>
              <Text style={s.insightTitle}>Water Conditions</Text>
              <View style={s.waterGrid}>
                {waterData.insights.slice(0, 4).map((insight) => (
                  <View key={insight.label} style={s.waterItem}>
                    <Ionicons name={insight.icon as any} size={16} color={insight.color} />
                    <Text style={s.waterItemLabel}>{insight.label}</Text>
                    <Text style={[s.waterItemValue, { color: insight.color }]}>
                      {insight.value}{insight.unit}
                    </Text>
                  </View>
                ))}
              </View>
              <Text style={s.insightDetail}>{waterData.fishingImpact}</Text>
            </View>
          )}

          <View style={{ height: 100 }} />
        </ScrollView>
      )}
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
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
  loadingText: {
    color: palette.textMuted,
    fontSize: 14,
    marginTop: 8,
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

  // Header
  header: {
    backgroundColor: palette.surface,
    paddingTop: 56,
    paddingBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
    gap: 10,
  },
  headerTop: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    gap: 16,
  },
  headerCenter: {
    flex: 1,
    alignItems: 'center',
  },
  headerTitle: {
    ...typeStyles.navHeader,
    color: palette.text,
  },
  headerCoords: {
    color: palette.textMuted,
    fontSize: 12,
    marginTop: 2,
  },

  // Mode toggle
  modeToggle: {
    flexDirection: 'row',
    marginHorizontal: 16,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 3,
  },
  modeToggleBtn: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderRadius: 8,
  },
  modeToggleBtnActive: {
    backgroundColor: palette.surface,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowOffset: { width: 0, height: 1 },
    shadowRadius: 3,
    elevation: 2,
  },
  modeToggleText: {
    color: palette.textMuted,
    fontSize: 14,
    fontWeight: '600',
  },
  modeToggleTextActive: {
    color: palette.text,
    fontWeight: '700',
  },

  // Day selector
  dayRow: {
    paddingHorizontal: 12,
    gap: 4,
  },
  dayPill: {
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 16,
    gap: 2,
    minWidth: 52,
  },
  dayPillActive: {
    backgroundColor: '#D6EAF5',
    borderRadius: 16,
  },
  dayPillLabel: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '600',
  },
  dayPillLabelActive: {
    color: palette.accent,
    fontWeight: '700',
  },
  dayPillDate: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '700',
  },
  dayPillDateActive: {
    color: palette.accent,
  },

  // Vertical scroll
  verticalScroll: {
    flex: 1,
  },

  // Data grid
  dataRow: {
    flexDirection: 'row',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },

  // Label column (fixed left)
  labelCell: {
    width: LABEL_COL_WIDTH,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    gap: 6,
    backgroundColor: palette.surface,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: palette.borderLight,
  },
  labelText: {
    color: palette.textSecondary,
    fontSize: 11,
    fontWeight: '600',
  },
  sublabelText: {
    color: palette.textMuted,
    fontSize: 9,
  },

  // Data cells
  dataCell: {
    alignItems: 'center',
    justifyContent: 'center',
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: 'rgba(0,0,0,0.04)',
  },
  dataCellText: {
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
  },
  fishScoreText: {
    fontSize: 14,
    fontWeight: '800',
  },

  // Now column
  nowColumnHighlight: {
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderLeftColor: palette.accent,
    borderRightColor: palette.accent,
  },
  nowColumnHeader: {
    backgroundColor: palette.accentLight,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderLeftColor: palette.accent,
    borderRightColor: palette.accent,
  },
  nowLabel: {
    color: palette.accent,
    fontSize: 11,
    fontWeight: '800',
  },

  // Hour header
  hourHeaderCell: {
    width: DATA_COL_WIDTH,
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.surface,
  },
  hourHeaderLabel: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '600',
  },
  hourLabel: {
    color: palette.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },

  // Pressure sparkline
  sparklineRow: {
    flexDirection: 'row',
    height: 28,
    backgroundColor: '#FFF8E1',
  },

  // ── Inline Insight Cards ──────────────────────────────────────────
  insightCard: {
    backgroundColor: palette.surface,
    marginHorizontal: 12,
    marginTop: 12,
    borderRadius: 12,
    padding: 16,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  insightTitle: {
    fontFamily: fonts.serif,
    fontSize: 15,
    color: palette.text,
    fontWeight: '400',
  },
  insightDetail: {
    fontSize: 12,
    color: palette.textMuted,
    lineHeight: 16,
  },

  // Timeline bar
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

  // Window pills
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

  // Pressure inline
  pressureRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  pressureLevel: {
    fontSize: 15,
    fontWeight: '700',
  },
  pressureDesc: {
    fontSize: 12,
    color: palette.textSecondary,
    marginTop: 2,
    lineHeight: 16,
  },

  // Water conditions grid
  waterGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  waterItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
  },
  waterItemLabel: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '500',
  },
  waterItemValue: {
    fontSize: 13,
    fontWeight: '700',
  },
});

// ── Extended Outlook styles ───────────────────────────────────────────────

const os = StyleSheet.create({
  sectionTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 20,
    color: palette.text,
    marginBottom: 4,
    letterSpacing: -0.3,
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    gap: 10,
    borderLeftWidth: 4,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dayName: {
    fontFamily: fonts.serif,
    fontSize: 16,
    color: palette.text,
  },
  dateStr: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 1,
  },
  scoreBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 12,
  },
  scoreText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
  },
  metricsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  metric: {
    flex: 1,
    alignItems: 'center',
    gap: 3,
  },
  metricValue: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
    textAlign: 'center',
  },
  metricLabel: {
    fontSize: 10,
    color: palette.textMuted,
    fontWeight: '500',
  },
  bottomRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  chipText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textSecondary,
  },
});
