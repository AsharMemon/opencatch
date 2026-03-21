import React, { useRef, useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  Dimensions,
  NativeSyntheticEvent,
  NativeScrollEvent,
} from 'react-native';
import Svg, {
  Circle,
  Path,
  G,
  Defs,
  LinearGradient,
  Stop,
} from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { palette, getConditionBand, conditionConfig, scoreColor } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import type { TabProps } from '../types/navigation';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

// ── Types ────────────────────────────────────────────────────────────────────

type ViewMode = 'Daily' | 'Monthly';

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
}

interface DayForecast {
  dayLabel: string;    // "Fri"
  date: number;        // 20
  weatherIcon: string;
  hourly: HourlyData[];
}

// ── Mock Data — matches FishAngler screenshot style ─────────────────────────

const now = new Date();

function generateHourlyData(): HourlyData[] {
  const hours: HourlyData[] = [];
  const currentHour = now.getHours();

  for (let h = 0; h < 24; h++) {
    const isNow = h === currentHour;
    const hourLabel = isNow ? 'Now' : String(h).padStart(2, '0');

    // Simulate realistic patterns
    const tempBase = 18 + Math.sin((h - 6) * Math.PI / 12) * 10;
    const temp = Math.round(Math.max(10, Math.min(35, tempBase + (Math.random() - 0.5) * 3)));
    const fishBase = h >= 5 && h <= 9 ? 50 + Math.random() * 30 : (h >= 17 && h <= 20 ? 40 + Math.random() * 25 : 10 + Math.random() * 20);
    const fishScore = Math.round(fishBase);
    const cloud = Math.round(Math.max(0, Math.min(100, 5 + Math.random() * 15)));
    const uv = h >= 6 && h <= 18 ? Math.round(Math.sin((h - 6) * Math.PI / 12) * 10) : 0;

    hours.push({
      hour: hourLabel,
      isNow,
      fishScore,
      condition: cloud < 20 ? 'Clear/Sunny' : cloud < 50 ? 'Partly Cloudy' : 'Cloudy',
      conditionIcon: cloud < 20 ? 'sunny-outline' : cloud < 50 ? 'partly-sunny-outline' : 'cloudy-outline',
      cloudCover: cloud,
      visibility: 16,
      airTemp: temp,
      pressure: 101700 + Math.round((Math.random() - 0.5) * 200),
      precipitation: Math.round(Math.random() * 5),
      precAccum: 0,
      snowAccum: 0,
      humidity: Math.round(20 + Math.random() * 15),
      uvIndex: Math.max(0, uv),
    });
  }
  return hours;
}

function generateWeekForecast(): DayForecast[] {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const forecasts: DayForecast[] = [];

  for (let d = 0; d < 7; d++) {
    const date = new Date(now);
    date.setDate(date.getDate() + d);
    const dayOfWeek = days[date.getDay()];
    const icons = ['sunny-outline', 'partly-sunny-outline', 'cloudy-outline', 'sunny-outline', 'sunny-outline', 'partly-sunny-outline', 'sunny-outline'];

    forecasts.push({
      dayLabel: dayOfWeek,
      date: date.getDate(),
      weatherIcon: icons[d],
      hourly: generateHourlyData(),
    });
  }
  return forecasts;
}

const WEEK_FORECAST = generateWeekForecast();

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
    label: 'PDT • 1 Hour',
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
    label: 'Precipitation (%)',
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

// ── Main Screen ──────────────────────────────────────────────────────────────

export function ForecastsScreen(_props: TabProps<'ForecastsTab'>) {
  const [selectedDayIdx, setSelectedDayIdx] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>('Daily');
  const scrollRef = useRef<ScrollView>(null);

  const selectedDay = WEEK_FORECAST[selectedDayIdx];
  const hourly = selectedDay.hourly;

  // Scroll to "Now" column on mount
  const handleScrollLayout = () => {
    const nowIdx = hourly.findIndex((h) => h.isNow);
    if (nowIdx > 0 && scrollRef.current) {
      scrollRef.current.scrollTo({ x: Math.max(0, (nowIdx - 1) * DATA_COL_WIDTH), animated: false });
    }
  };

  return (
    <View style={s.screen}>
      {/* Header */}
      <View style={s.header}>
        <View style={s.headerTop}>
          <Ionicons name="search-outline" size={22} color={palette.text} />
          <View style={s.headerCenter}>
            <Text style={s.headerTitle}>Map Location</Text>
            <Text style={s.headerCoords}>
              {(34.9001).toFixed(6)}, {(-118.5206).toFixed(6)}
            </Text>
          </View>
          <Ionicons name="navigate-outline" size={22} color={palette.text} />
        </View>

        {/* Daily / Monthly toggle */}
        <View style={s.modeToggle}>
          {(['Daily', 'Monthly'] as ViewMode[]).map((mode) => (
            <Pressable
              key={mode}
              style={[s.modeToggleBtn, viewMode === mode && s.modeToggleBtnActive]}
              onPress={() => setViewMode(mode)}
            >
              <Text style={[s.modeToggleText, viewMode === mode && s.modeToggleTextActive]}>
                {mode}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Day selector */}
        <DaySelector
          days={WEEK_FORECAST}
          selectedIdx={selectedDayIdx}
          onSelect={setSelectedDayIdx}
        />
      </View>

      {/* Scrollable forecast grid */}
      <ScrollView
        style={s.verticalScroll}
        showsVerticalScrollIndicator={false}
      >
        <ScrollView
          ref={scrollRef}
          horizontal
          showsHorizontalScrollIndicator={false}
          onLayout={handleScrollLayout}
          contentContainerStyle={{ width: LABEL_COL_WIDTH + hourly.length * DATA_COL_WIDTH }}
        >
          <View>
            {/* Hour header */}
            <HourHeaderRow hourly={hourly} />

            {/* Data rows */}
            {ROWS.map((row) => (
              <React.Fragment key={row.key}>
                <DataRow row={row} hourly={hourly} />
                {/* Insert pressure sparkline after the pressure row */}
                {row.key === 'pressure' && (
                  <PressureSparkline hourly={hourly} />
                )}
              </React.Fragment>
            ))}

            <View style={{ height: 100 }} />
          </View>
        </ScrollView>
      </ScrollView>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
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
});
