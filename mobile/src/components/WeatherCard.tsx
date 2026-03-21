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

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <Ionicons name={weatherIonicon(conditions.weatherIcon) as any} size={36} color={palette.textSecondary} />
        <View>
          <Text style={styles.weatherText}>{conditions.weather}</Text>
          <Text style={styles.airTemp}>
            {airTemp}{'\u00B0'}{tempUnit}
          </Text>
        </View>
      </View>

      <View style={styles.grid}>
        <MetricCell
          label="Water Temp"
          value={`${waterTemp}\u00B0${tempUnit}`}
          highlight
        />
        <MetricCell
          label="Wind"
          value={`${windSpeed} ${speedUnit} ${conditions.windDirection}`}
        />
        <MetricCell
          label="Pressure"
          value={`${conditions.pressure} inHg`}
          sub={conditions.pressureTrend}
        />
        <MetricCell label="Humidity" value={`${conditions.humidity}%`} />
        <MetricCell label="Moon" value={conditions.moonPhase} />
        <MetricCell
          label="Solunar"
          value={conditions.solunarRating}
          highlight={conditions.solunarRating === 'excellent'}
        />
      </View>

      <View style={styles.sunRow}>
        <View style={styles.sunItem}>
          <Ionicons name="sunny-outline" size={14} color={palette.textMuted} />
          <Text style={styles.sunText}>{conditions.sunrise}</Text>
        </View>
        <View style={styles.sunItem}>
          <Ionicons name="moon-outline" size={14} color={palette.textMuted} />
          <Text style={styles.sunText}>{conditions.sunset}</Text>
        </View>
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
    borderRadius: 10,
    padding: 16,
    gap: 16,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  weatherText: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },
  airTemp: {
    color: palette.textMuted,
    fontSize: 14,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 2,
  },
  cell: {
    width: '31%',
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    padding: 12,
    marginBottom: 6,
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
    paddingHorizontal: 4,
  },
  sunItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  sunText: {
    color: palette.textMuted,
    fontSize: 13,
  },
});
