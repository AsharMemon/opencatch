import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import type { CurrentConditions } from '../types/models';

interface Props {
  conditions: CurrentConditions;
  units?: 'imperial' | 'metric';
}

function weatherIonicon(icon: string): string {
  const map: Record<string, string> = {
    'sunny': 'sunny-outline',
    'partly-cloudy': 'partly-sunny-outline',
    'cloudy': 'cloud-outline',
    'rainy': 'rainy-outline',
    'stormy': 'thunderstorm-outline',
    'snowy': 'snow-outline',
    'foggy': 'cloud-outline',
    'windy': 'flag-outline',
  };
  return map[icon] ?? 'sunny-outline';
}

export function WeatherCard({ conditions, units = 'imperial' }: Props) {
  const tempUnit = units === 'imperial' ? 'F' : 'C';
  const waterTemp =
    units === 'metric'
      ? Math.round(((conditions.waterTemp - 32) * 5) / 9)
      : conditions.waterTemp;
  const airTemp =
    units === 'metric'
      ? Math.round(((conditions.airTemp - 32) * 5) / 9)
      : conditions.airTemp;
  const speedUnit = units === 'imperial' ? 'mph' : 'km/h';
  const windSpeed =
    units === 'metric' ? Math.round(conditions.windSpeed * 1.609) : conditions.windSpeed;
  const pressureLabel =
    units === 'metric'
      ? `${Math.round(conditions.pressure * 33.8639)} hPa`
      : `${conditions.pressure} inHg`;
  const pressureTrendLabel =
    conditions.pressureTrend.charAt(0).toUpperCase() + conditions.pressureTrend.slice(1);

  return (
    <View style={styles.card}>
      <View style={styles.heroRow}>
        <View style={styles.heroLeft}>
          <View style={styles.heroIconWrap}>
            <Ionicons name={weatherIonicon(conditions.weatherIcon) as any} size={26} color={palette.accent} />
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.weatherText}>{conditions.weather}</Text>
            <Text style={styles.heroSub}>Conditions right now</Text>
          </View>
        </View>
        <View style={styles.heroTempWrap}>
          <Text style={styles.heroTemp}>
            {airTemp}{'\u00B0'}{tempUnit}
          </Text>
          <Text style={styles.heroTempLabel}>air</Text>
        </View>
      </View>

      <View style={styles.glanceRow}>
        <MetricPill
          icon="water-outline"
          label="Water"
          value={`${waterTemp}\u00B0${tempUnit}`}
          highlight
        />
        <MetricPill
          icon="navigate-outline"
          label="Wind"
          value={`${windSpeed} ${speedUnit}`}
          sub={conditions.windDirection}
        />
        <MetricPill
          icon="sunny-outline"
          label="Sunset"
          value={conditions.sunset}
        />
      </View>

      <View style={styles.grid}>
        <MetricCell
          label="Wind"
          value={`${windSpeed} ${speedUnit} ${conditions.windDirection}`}
        />
        <MetricCell
          label="Pressure"
          value={pressureLabel}
          sub={pressureTrendLabel}
        />
        <MetricCell label="Humidity" value={`${conditions.humidity}%`} />
        <MetricCell label="Moon" value={conditions.moonPhase} />
        <MetricCell
          label="Solunar"
          value={conditions.solunarRating.charAt(0).toUpperCase() + conditions.solunarRating.slice(1)}
          highlight={conditions.solunarRating === 'excellent'}
        />
      </View>

      <View style={styles.sunRow}>
        <View style={styles.sunItem}>
          <Ionicons name="sunny-outline" size={14} color={palette.textMuted} />
          <Text style={styles.sunLabel}>Sunrise</Text>
          <Text style={styles.sunText}>{conditions.sunrise}</Text>
        </View>
        <View style={styles.sunItem}>
          <Ionicons name="moon-outline" size={14} color={palette.textMuted} />
          <Text style={styles.sunLabel}>Sunset</Text>
          <Text style={styles.sunText}>{conditions.sunset}</Text>
        </View>
      </View>
    </View>
  );
}

function MetricPill({
  icon,
  label,
  value,
  sub,
  highlight,
}: {
  icon: string;
  label: string;
  value: string;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <View style={styles.glancePill}>
      <Ionicons name={icon as any} size={14} color={highlight ? palette.accent : palette.textSecondary} />
      <View style={{ flex: 1 }}>
        <Text style={styles.glanceLabel}>{label}</Text>
        <Text style={[styles.glanceValue, highlight && styles.cellValueHighlight]}>{value}</Text>
        {sub ? <Text style={styles.glanceSub}>{sub}</Text> : null}
      </View>
    </View>
  );
}

function MetricCell({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <View style={styles.cell}>
      <Text style={styles.cellLabel}>{label}</Text>
      <Text style={[styles.cellValue, highlight && styles.cellValueHighlight]}>{value}</Text>
      {sub && <Text style={styles.cellSub}>{sub}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 16,
    gap: 14,
    borderWidth: 1,
    borderColor: 'rgba(17, 58, 82, 0.07)',
  },
  heroRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  heroLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    flex: 1,
  },
  heroIconWrap: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#EEF6FB',
  },
  heroCopy: {
    gap: 2,
  },
  heroSub: {
    color: palette.textMuted,
    fontSize: 12,
    fontWeight: '500',
  },
  heroTempWrap: {
    alignItems: 'flex-end',
  },
  weatherText: {
    color: palette.text,
    fontSize: 19,
    fontWeight: '700',
  },
  heroTemp: {
    color: palette.text,
    fontSize: 28,
    fontWeight: '800',
    letterSpacing: -0.6,
  },
  heroTempLabel: {
    color: palette.textMuted,
    fontSize: 11,
    textTransform: 'uppercase',
    fontWeight: '700',
    letterSpacing: 0.45,
  },
  glanceRow: {
    flexDirection: 'row',
    gap: 8,
  },
  glancePill: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 10,
  },
  glanceLabel: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.35,
  },
  glanceValue: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '700',
    marginTop: 2,
  },
  glanceSub: {
    color: palette.textDim,
    fontSize: 10.5,
    marginTop: 1,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  cell: {
    width: '30.5%',
    backgroundColor: palette.surfaceRaised,
    borderRadius: 12,
    padding: 12,
    gap: 3,
  },
  cellLabel: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '500',
  },
  cellValue: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
  },
  cellValueHighlight: {
    color: palette.accent,
  },
  cellSub: {
    color: palette.textDim,
    fontSize: 11,
    textTransform: 'capitalize',
  },
  sunRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: 2,
  },
  sunItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  sunLabel: {
    color: palette.textDim,
    fontSize: 11,
    fontWeight: '600',
  },
  sunText: {
    color: palette.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },
});
