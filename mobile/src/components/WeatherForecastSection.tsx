import React from 'react';
import { ScrollView, View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import type { FishingLocation, CurrentConditions } from '../types/models';

// ── Props ──────────────────────────────────────────────────────────
interface Props {
  location: FishingLocation;
}

// ── Weather icon helper ──────────────────────────────────────────
function weatherIonicon(icon: string): string {
  const map: Record<string, string> = {
    sunny: 'sunny-outline',
    'partly-cloudy': 'partly-sunny-outline',
    cloudy: 'cloud-outline',
    rainy: 'rainy-outline',
    stormy: 'thunderstorm-outline',
    snowy: 'snow-outline',
    foggy: 'cloud-outline',
    windy: 'flag-outline',
  };
  return map[icon] ?? 'sunny-outline';
}

// ── Moon phase icon helper ─────────────────────────────────────────
function moonIonicon(phase: string): string {
  const p = phase.toLowerCase();
  if (p.includes('new')) return 'moon-outline';
  if (p.includes('full')) return 'moon';
  return 'moon-outline';
}

// ── Pressure trend pill colors ─────────────────────────────────────
function pressureTrendConfig(trend: 'rising' | 'falling' | 'steady'): {
  label: string;
  color: string;
  bg: string;
} {
  switch (trend) {
    case 'rising':
      return { label: 'Rising', color: '#3D8B37', bg: 'rgba(61,139,55,0.12)' };
    case 'falling':
      return { label: 'Falling', color: '#C44B4B', bg: 'rgba(196,75,75,0.12)' };
    case 'steady':
      return { label: 'Steady', color: '#78909C', bg: 'rgba(120,144,156,0.12)' };
  }
}

// ── Solunar badge ──────────────────────────────────────────────────
function solunarBadgeConfig(rating: string): { label: string; color: string; bg: string } {
  switch (rating) {
    case 'excellent':
      return { label: 'Excellent', color: '#E53935', bg: 'rgba(229,57,53,0.10)' };
    case 'good':
      return { label: 'Good', color: '#3D8B37', bg: 'rgba(61,139,55,0.10)' };
    case 'fair':
      return { label: 'Fair', color: '#C4841D', bg: 'rgba(196,132,29,0.10)' };
    default:
      return { label: 'Poor', color: '#78909C', bg: 'rgba(120,144,156,0.10)' };
  }
}

// ── Wind direction arrow rotation ──────────────────────────────────
function windArrowRotation(dir: string): string {
  const dirs: Record<string, number> = {
    N: 180, NNE: 202, NE: 225, ENE: 247,
    E: 270, ESE: 292, SE: 315, SSE: 337,
    S: 0, SSW: 22, SW: 45, WSW: 67,
    W: 90, WNW: 112, NW: 135, NNW: 157,
  };
  return `${dirs[dir] ?? 0}deg`;
}

// ── Generate mock hourly data ──────────────────────────────────────
interface HourData {
  hour: string;
  weatherIcon: string;
  temp: number;
  wind: number;
  pressure: number;
  period: 'night' | 'morning' | 'afternoon' | 'evening';
}

function generateHourlyData(conditions: CurrentConditions): HourData[] {
  const hours: HourData[] = [];
  const now = new Date();
  const currentHour = now.getHours();

  for (let i = 0; i < 24; i++) {
    const h = (currentHour + i) % 24;
    const ampm = h >= 12 ? 'PM' : 'AM';
    const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
    const label = i === 0 ? 'Now' : `${display}${ampm}`;

    let period: HourData['period'];
    if (h >= 5 && h < 12) period = 'morning';
    else if (h >= 12 && h < 17) period = 'afternoon';
    else if (h >= 17 && h < 21) period = 'evening';
    else period = 'night';

    const tempOffset = Math.sin(((h - 6) / 24) * Math.PI * 2) * 8;
    const temp = Math.round(conditions.airTemp + tempOffset + (Math.random() * 2 - 1));
    const wind = Math.max(0, Math.round(conditions.windSpeed + (Math.random() * 6 - 3)));
    const pressureDrift = (Math.random() - 0.5) * 0.04;
    const pressure = +(conditions.pressure + pressureDrift * i).toFixed(2);

    let weatherIcon: string;
    if (h >= 6 && h < 20) {
      weatherIcon = weatherIonicon(conditions.weatherIcon);
    } else {
      weatherIcon = 'moon-outline';
    }

    hours.push({ hour: label, weatherIcon, temp, wind, pressure, period });
  }

  return hours;
}

// ── Mock illumination from moon phase ──────────────────────────────
function moonIllumination(phase: string): number {
  const p = phase.toLowerCase();
  if (p.includes('new')) return 2;
  if (p.includes('waxing crescent')) return 18;
  if (p.includes('first quarter')) return 50;
  if (p.includes('waxing gibbous')) return 78;
  if (p.includes('full')) return 99;
  if (p.includes('waning gibbous')) return 78;
  if (p.includes('last quarter') || p.includes('third quarter')) return 50;
  if (p.includes('waning crescent')) return 18;
  return 50;
}

// ── Conditions Grid icons ────────────────────────────────────────
const COND_GRID_ICONS: Record<string, string> = {
  'Cloud Cover': 'cloud-outline',
  'Visibility': 'eye-outline',
  'Humidity': 'water-outline',
  'UV Index': 'sunny-outline',
  'Precip Chance': 'rainy-outline',
  'Dew Point': 'thermometer-outline',
};

// ── A. Current Conditions Summary Card ─────────────────────────────
function CurrentConditionsSummary({ conditions }: { conditions: CurrentConditions }) {
  const pTrend = pressureTrendConfig(conditions.pressureTrend);
  const solunar = solunarBadgeConfig(conditions.solunarRating);

  return (
    <View style={s.card}>
      <Text style={s.sectionTitle}>Current Conditions</Text>

      {/* Temperature row */}
      <View style={s.tempRow}>
        <View style={s.tempBlock}>
          <Text style={s.waterTempValue}>{conditions.waterTemp}{'°'}</Text>
          <Text style={s.waterTempLabel}>Water</Text>
        </View>
        <View style={s.tempDivider} />
        <View style={s.tempBlock}>
          <Text style={s.airTempValue}>{conditions.airTemp}{'°'}</Text>
          <Text style={s.airTempLabel}>Air</Text>
        </View>
        <View style={s.weatherIconBlock}>
          <Ionicons name={weatherIonicon(conditions.weatherIcon) as any} size={32} color={palette.textSecondary} />
          <Text style={s.currentWeatherLabel}>{conditions.weather}</Text>
        </View>
      </View>

      {/* Wind + Pressure + Moon row */}
      <View style={s.metaRow}>
        {/* Wind */}
        <View style={s.metaCell}>
          <View style={s.windRow}>
            <Text style={s.metaCellValue}>{conditions.windSpeed} mph</Text>
            <View style={{ transform: [{ rotate: windArrowRotation(conditions.windDirection) }] }}>
              <Ionicons name="arrow-up" size={14} color={palette.accent} />
            </View>
          </View>
          <Text style={s.metaCellLabel}>Wind {conditions.windDirection}</Text>
        </View>

        {/* Pressure */}
        <View style={s.metaCell}>
          <Text style={s.metaCellValue}>{conditions.pressure} inHg</Text>
          <View style={[s.trendPill, { backgroundColor: pTrend.bg }]}>
            <Text style={[s.trendPillText, { color: pTrend.color }]}>{pTrend.label}</Text>
          </View>
        </View>

        {/* Moon */}
        <View style={s.metaCell}>
          <Ionicons name={moonIonicon(conditions.moonPhase) as any} size={22} color={palette.textSecondary} />
          <Text style={s.metaCellLabel}>{conditions.moonPhase}</Text>
        </View>
      </View>

      {/* Solunar badge */}
      <View style={s.solunarRow}>
        <Text style={s.solunarLabel}>Solunar Rating</Text>
        <View style={[s.solunarBadge, { backgroundColor: solunar.bg }]}>
          <Text style={[s.solunarBadgeText, { color: solunar.color }]}>{solunar.label}</Text>
        </View>
      </View>
    </View>
  );
}

// ── B. Hourly Forecast Strip ───────────────────────────────────────
function HourlyForecastStrip({ conditions }: { conditions: CurrentConditions }) {
  const hourlyData = generateHourlyData(conditions);

  let lastPeriod: HourData['period'] | null = null;

  return (
    <View style={s.card}>
      <Text style={s.sectionTitle}>Hourly Forecast</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={s.hourlyStrip}
      >
        {hourlyData.map((h, i) => {
          const showDivider = lastPeriod !== null && lastPeriod !== h.period;
          lastPeriod = h.period;

          return (
            <React.Fragment key={i}>
              {showDivider && <View style={s.hourlyDivider} />}
              <View style={[s.hourlyCol, i === 0 && s.hourlyColNow]}>
                <Text style={[s.hourlyHour, i === 0 && s.hourlyHourNow]}>{h.hour}</Text>
                <Ionicons name={h.weatherIcon as any} size={18} color={palette.textSecondary} />
                <Text style={s.hourlyTemp}>{h.temp}{'°'}</Text>
                <Text style={s.hourlyWind}>{h.wind} mph</Text>
                <Text style={s.hourlyPressure}>{h.pressure}</Text>
              </View>
            </React.Fragment>
          );
        })}
      </ScrollView>
    </View>
  );
}

// ── C. Conditions Grid ─────────────────────────────────────────────
function ConditionsGrid({ conditions }: { conditions: CurrentConditions }) {
  const cloudCover = conditions.weather.toLowerCase().includes('cloud')
    ? conditions.weather.toLowerCase().includes('partly') ? 45 : 80
    : conditions.weather.toLowerCase().includes('rain') || conditions.weather.toLowerCase().includes('storm')
      ? 90
      : 10;
  const visibility = cloudCover > 70 ? 5 : cloudCover > 40 ? 8 : 10;
  const uvIndex = conditions.weather.toLowerCase().includes('sunny') ? 7
    : conditions.weather.toLowerCase().includes('partly') ? 5
    : 3;
  const precipChance = conditions.weather.toLowerCase().includes('rain') ? 65
    : conditions.weather.toLowerCase().includes('storm') ? 85
    : conditions.weather.toLowerCase().includes('snow') ? 55
    : 10;
  const dewPoint = Math.round(conditions.airTemp - ((100 - conditions.humidity) / 5));

  const cells: Array<{ ionicon: string; value: string; label: string }> = [
    { ionicon: 'cloud-outline', value: `${cloudCover}%`, label: 'Cloud Cover' },
    { ionicon: 'eye-outline', value: `${visibility} mi`, label: 'Visibility' },
    { ionicon: 'water-outline', value: `${conditions.humidity}%`, label: 'Humidity' },
    { ionicon: 'sunny-outline', value: `${uvIndex}`, label: 'UV Index' },
    { ionicon: 'rainy-outline', value: `${precipChance}%`, label: 'Precip Chance' },
    { ionicon: 'thermometer-outline', value: `${dewPoint}°F`, label: 'Dew Point' },
  ];

  return (
    <View style={s.card}>
      <Text style={s.sectionTitle}>Conditions</Text>
      <View style={s.condGrid}>
        {cells.map((cell) => (
          <View key={cell.label} style={s.condCell}>
            <Ionicons name={cell.ionicon as any} size={20} color={palette.accent} />
            <Text style={s.condCellValue}>{cell.value}</Text>
            <Text style={s.condCellLabel}>{cell.label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ── D. Sun & Moon Card ─────────────────────────────────────────────
function SunMoonCard({ conditions }: { conditions: CurrentConditions }) {
  const illumination = moonIllumination(conditions.moonPhase);

  const now = new Date();
  const [srH, srM] = conditions.sunrise.split(':').map(Number);
  const [ssH, ssM] = conditions.sunset.split(':').map(Number);
  const sunriseMin = (srH ?? 0) * 60 + (srM ?? 0);
  const sunsetMin = (ssH ?? 0) * 60 + (ssM ?? 0);
  const currentMin = now.getHours() * 60 + now.getMinutes();
  const dayLength = sunsetMin - sunriseMin;
  const sunProgress = dayLength > 0
    ? Math.max(0, Math.min(1, (currentMin - sunriseMin) / dayLength))
    : 0.5;

  const arcAngle = Math.PI * sunProgress;
  const arcRadius = 60;
  const sunX = arcRadius - Math.cos(arcAngle) * arcRadius;
  const sunY = -Math.sin(arcAngle) * arcRadius;

  const majorStart1 = `${((srH ?? 6) + 1) % 24}:${String(srM ?? 0).padStart(2, '0')}`;
  const majorEnd1 = `${((srH ?? 6) + 3) % 24}:${String(srM ?? 0).padStart(2, '0')}`;
  const minorStart1 = `${((ssH ?? 18) - 2 + 24) % 24}:${String(ssM ?? 0).padStart(2, '0')}`;
  const minorEnd1 = `${((ssH ?? 18)) % 24}:${String(ssM ?? 0).padStart(2, '0')}`;

  return (
    <View style={s.card}>
      <Text style={s.sectionTitle}>Sun & Moon</Text>

      {/* Sun arc */}
      <View style={s.sunArcContainer}>
        <View style={s.sunArcTrack}>
          <View style={s.sunArcHalf} />
        </View>
        <View
          style={[
            s.sunIndicator,
            {
              left: sunX + 10,
              bottom: -sunY + 14,
            },
          ]}
        >
          <Ionicons name="sunny" size={22} color="#FB8C00" />
        </View>
        <View style={s.sunTimesRow}>
          <View style={s.sunTimeBlock}>
            <Ionicons name="sunny-outline" size={16} color={palette.textSecondary} />
            <Text style={s.sunTimeValue}>{conditions.sunrise}</Text>
            <Text style={s.sunTimeLabel}>Sunrise</Text>
          </View>
          <View style={s.sunTimeBlock}>
            <Ionicons name="moon-outline" size={16} color={palette.textSecondary} />
            <Text style={s.sunTimeValue}>{conditions.sunset}</Text>
            <Text style={s.sunTimeLabel}>Sunset</Text>
          </View>
        </View>
      </View>

      {/* Moon info */}
      <View style={s.moonInfoRow}>
        <Ionicons name={moonIonicon(conditions.moonPhase) as any} size={28} color={palette.textSecondary} />
        <View style={s.moonInfoText}>
          <Text style={s.moonPhaseName}>{conditions.moonPhase}</Text>
          <Text style={s.moonIllum}>{illumination}% illumination</Text>
        </View>
      </View>

      {/* Solunar periods */}
      <View style={s.solunarPeriods}>
        <View style={s.solunarPeriodRow}>
          <View style={[s.solunarPeriodBadge, { backgroundColor: 'rgba(229,57,53,0.10)' }]}>
            <Text style={[s.solunarPeriodType, { color: '#E53935' }]}>Major</Text>
          </View>
          <Text style={s.solunarPeriodTime}>{majorStart1} - {majorEnd1}</Text>
        </View>
        <View style={s.solunarPeriodRow}>
          <View style={[s.solunarPeriodBadge, { backgroundColor: 'rgba(196,132,29,0.10)' }]}>
            <Text style={[s.solunarPeriodType, { color: '#C4841D' }]}>Minor</Text>
          </View>
          <Text style={s.solunarPeriodTime}>{minorStart1} - {minorEnd1}</Text>
        </View>
      </View>
    </View>
  );
}

// ── Main Component ─────────────────────────────────────────────────
export function WeatherForecastSection({ location }: Props) {
  const { conditions } = location;

  return (
    <View style={s.container}>
      <CurrentConditionsSummary conditions={conditions} />
      <HourlyForecastStrip conditions={conditions} />
      <ConditionsGrid conditions={conditions} />
      <SunMoonCard conditions={conditions} />
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────
const s = StyleSheet.create({
  container: {
    gap: 16,
  },

  card: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 16,
    gap: 12,
  },
  sectionTitle: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },

  // ── A. Current Conditions ─────────────────────────────────────────
  tempRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
  },
  tempBlock: {
    alignItems: 'center',
    gap: 2,
  },
  waterTempValue: {
    fontSize: 38,
    fontWeight: '700',
    color: palette.accent,
    lineHeight: 42,
  },
  waterTempLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.accent,
  },
  tempDivider: {
    width: 1,
    height: 40,
    backgroundColor: palette.borderLight,
  },
  airTempValue: {
    fontSize: 28,
    fontWeight: '700',
    color: palette.textSecondary,
    lineHeight: 32,
  },
  airTempLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textMuted,
  },
  weatherIconBlock: {
    marginLeft: 'auto',
    alignItems: 'center',
    gap: 4,
  },
  currentWeatherLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
  },

  metaRow: {
    flexDirection: 'row',
    gap: 8,
  },
  metaCell: {
    flex: 1,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    padding: 8,
    alignItems: 'center',
    gap: 4,
  },
  metaCellValue: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.text,
  },
  metaCellLabel: {
    fontSize: 10,
    fontWeight: '500',
    color: palette.textMuted,
    textAlign: 'center',
  },
  windRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  trendPill: {
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  trendPillText: {
    fontSize: 10,
    fontWeight: '700',
  },

  solunarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  solunarLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  solunarBadge: {
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
  solunarBadgeText: {
    fontSize: 12,
    fontWeight: '700',
  },

  // ── B. Hourly Forecast Strip ──────────────────────────────────────
  hourlyStrip: {
    flexDirection: 'row',
    gap: 0,
    paddingVertical: 4,
  },
  hourlyCol: {
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 6,
    gap: 4,
    minWidth: 62,
  },
  hourlyColNow: {
    backgroundColor: palette.accentDim,
    borderRadius: 8,
  },
  hourlyHour: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textMuted,
  },
  hourlyHourNow: {
    color: palette.accent,
    fontWeight: '700',
  },
  hourlyTemp: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
  },
  hourlyWind: {
    fontSize: 10,
    fontWeight: '500',
    color: palette.textMuted,
  },
  hourlyPressure: {
    fontSize: 9,
    fontWeight: '500',
    color: palette.textDim,
  },
  hourlyDivider: {
    width: 1,
    alignSelf: 'stretch',
    backgroundColor: palette.borderLight,
    marginHorizontal: 2,
  },

  // ── C. Conditions Grid ────────────────────────────────────────────
  condGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  condCell: {
    width: '30%',
    flexGrow: 1,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    padding: 12,
    alignItems: 'center',
    gap: 4,
  },
  condCellValue: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  condCellLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: palette.textMuted,
    textAlign: 'center',
  },

  // ── D. Sun & Moon ─────────────────────────────────────────────────
  sunArcContainer: {
    alignItems: 'center',
    paddingTop: 10,
    paddingBottom: 6,
    position: 'relative',
    height: 110,
  },
  sunArcTrack: {
    position: 'absolute',
    top: 10,
    width: 140,
    height: 70,
    overflow: 'hidden',
  },
  sunArcHalf: {
    width: 140,
    height: 140,
    borderRadius: 70,
    borderWidth: 2,
    borderColor: palette.borderLight,
    borderStyle: 'dashed',
  },
  sunIndicator: {
    position: 'absolute',
  },
  sunTimesRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    width: '100%',
    position: 'absolute',
    bottom: 0,
    paddingHorizontal: 20,
  },
  sunTimeBlock: {
    alignItems: 'center',
    gap: 2,
  },
  sunTimeValue: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
  },
  sunTimeLabel: {
    fontSize: 10,
    fontWeight: '500',
    color: palette.textMuted,
  },

  moonInfoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    padding: 12,
  },
  moonInfoText: {
    gap: 2,
  },
  moonPhaseName: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
  },
  moonIllum: {
    fontSize: 12,
    fontWeight: '500',
    color: palette.textMuted,
  },

  solunarPeriods: {
    gap: 8,
  },
  solunarPeriodRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  solunarPeriodBadge: {
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    minWidth: 56,
    alignItems: 'center',
  },
  solunarPeriodType: {
    fontSize: 11,
    fontWeight: '700',
  },
  solunarPeriodTime: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
  },
});
