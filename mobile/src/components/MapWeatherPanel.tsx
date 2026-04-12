import React from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import type { MapWeatherForecast, MapWeatherSummary, MapWeatherHour } from '../services/mapWeatherForecast';

export interface MapTideSummaryPoint {
  label: string;
  heightFt: number;
}

export interface MapTideSummary {
  stationName: string;
  primary: string;
  secondary: string;
  currentLevelFt?: number | null;
  depthAdjustmentFt?: number | null;
  trendPoints?: MapTideSummaryPoint[];
}

interface Props {
  forecast: MapWeatherForecast | null;
  tideSummary?: MapTideSummary | null;
  loading?: boolean;
  compact?: boolean;
  onClose?: () => void;
  selectedWindHourIndex?: number;
  onSelectWindHour?: (index: number) => void;
  windPlaybackActive?: boolean;
  onToggleWindPlayback?: () => void;
}

function windColor(mph: number): string {
  if (mph < 6) return '#4E8DD6';
  if (mph < 11) return '#3FB3D0';
  if (mph < 17) return '#55B871';
  if (mph < 24) return '#D6A74B';
  return '#D36A54';
}

function directionToCompass(deg?: number | null): string {
  if (deg == null || !Number.isFinite(deg)) return '--';
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
}

function labelTime(timestamp?: string): string {
  if (!timestamp) return 'now';
  return new Date(timestamp).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatSignedFeet(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)} ft`;
}

function seaStateLabel(current?: MapWeatherSummary | null): string {
  if (!current) return '--';
  if (current.waveHeightFt != null) return `${current.waveHeightFt.toFixed(1)} ft`;
  if (current.visibilityNm != null) return `${current.visibilityNm.toFixed(1)} nm`;
  if (current.precipitationProbability != null) return `${current.precipitationProbability}% rain`;
  return '--';
}

function tideLabel(tideSummary?: MapTideSummary | null): string | null {
  if (!tideSummary) return null;
  if (tideSummary.depthAdjustmentFt != null) return formatSignedFeet(tideSummary.depthAdjustmentFt);
  if (tideSummary.currentLevelFt != null) return formatSignedFeet(tideSummary.currentLevelFt);
  return tideSummary.primary || null;
}

function currentLabel(current?: MapWeatherSummary | null): string | null {
  if (!current || current.currentSpeedKnots == null) return null;
  return `${current.currentSpeedKnots.toFixed(1)} kt ${directionToCompass(current.currentDirectionDeg)}`;
}

function SmallStat({
  icon,
  label,
  value,
  tint,
}: {
  icon: string;
  label: string;
  value: string;
  tint: string;
}) {
  return (
    <View style={styles.smallStat}>
      <View style={[styles.smallStatIcon, { backgroundColor: `${tint}18` }]}>
        <Ionicons name={icon as any} size={13} color={tint} />
      </View>
      <View style={styles.smallStatCopy}>
        <Text style={styles.smallStatLabel}>{label}</Text>
        <Text style={styles.smallStatValue} numberOfLines={1}>
          {value}
        </Text>
      </View>
    </View>
  );
}

function HourPill({
  hour,
  active,
  onPress,
}: {
  hour: MapWeatherHour;
  active: boolean;
  onPress?: () => void;
}) {
  const content = (
    <View style={[styles.hourPill, active && styles.hourPillActive]}>
      <Text style={styles.hourLabel}>{hour.label}</Text>
      <Ionicons
        name="arrow-up"
        size={13}
        color={windColor(hour.windMph)}
        style={{ transform: [{ rotate: `${(hour.windDirectionDeg + 180) % 360}deg` }] }}
      />
      <Text style={styles.hourWind}>{hour.windMph}</Text>
      <Text style={styles.hourWindUnit}>mph</Text>
      <Text style={styles.hourDetail}>
        {hour.gustMph != null ? `g ${hour.gustMph}` : hour.waveHeightFt != null ? `${hour.waveHeightFt.toFixed(1)} ft` : `${hour.tempF}°`}
      </Text>
    </View>
  );

  if (!onPress) return content;

  return <Pressable onPress={onPress}>{content}</Pressable>;
}

export function MapWeatherPanel({
  forecast,
  tideSummary,
  loading = false,
  compact = false,
  onClose,
  selectedWindHourIndex = 0,
  onSelectWindHour,
  windPlaybackActive = false,
  onToggleWindPlayback,
}: Props) {
  const current = forecast?.current;
  const forecastHours = forecast?.hourly ?? [];
  const boundedSelectedIndex =
    forecastHours.length > 0
      ? Math.max(0, Math.min(selectedWindHourIndex, forecastHours.length - 1))
      : 0;
  const selectedHour = forecastHours[boundedSelectedIndex] ?? null;
  const hourly = forecastHours.slice(0, 8);
  const displayWindMph = selectedHour?.windMph ?? current?.windMph ?? 0;
  const displayGustMph = selectedHour?.gustMph ?? current?.gustMph ?? null;
  const displayDirectionDeg = selectedHour?.windDirectionDeg ?? current?.windDirectionDeg;
  const displayTempF = selectedHour?.tempF ?? current?.tempF ?? 0;
  const displayWaveHeightFt = selectedHour?.waveHeightFt ?? current?.waveHeightFt ?? null;
  const displayPrecipitation =
    selectedHour?.precipitationProbability ?? current?.precipitationProbability ?? null;
  const currentDirection = directionToCompass(displayDirectionDeg);
  const currentFlow = currentLabel(current);
  const tideQuick = tideLabel(tideSummary);
  const compactMeta = [
    forecast ? `${displayTempF}\u00B0 air` : null,
    displayGustMph ? `gust ${displayGustMph}` : null,
    tideQuick ? `tide ${tideQuick}` : null,
    currentFlow ? `current ${currentFlow}` : null,
  ].filter(Boolean).join(' · ');

  return (
    <View style={[styles.card, compact && styles.cardCompact]}>
      <View style={styles.headerRow}>
        <View style={styles.headerCopy}>
          <Text style={styles.eyebrow}>Wind & Conditions</Text>
          <Text style={styles.title}>
            {forecast ? `${displayWindMph} mph ${currentDirection}` : loading ? 'Loading wind' : 'Wind unavailable'}
          </Text>
          <Text style={styles.subtitle}>
            {forecast
              ? `${selectedHour?.label ?? 'Now'} · refreshed ${labelTime(forecast.updatedAt)}`
              : 'Forecast tuned to the current map view'}
          </Text>
        </View>
        <View style={styles.headerActions}>
          {onToggleWindPlayback && forecastHours.length > 1 ? (
            <Pressable style={styles.headerCloseWrap} onPress={onToggleWindPlayback}>
              <Ionicons
                name={windPlaybackActive ? 'pause' : 'play'}
                size={15}
                color={palette.accentDeep}
              />
            </Pressable>
          ) : null}
          <View style={styles.headerBadge}>
            {loading ? (
              <ActivityIndicator size="small" color={palette.accent} />
            ) : (
              <Ionicons
                name={(current?.icon ?? 'partly-sunny-outline') as any}
                size={18}
                color={palette.accentDeep}
              />
            )}
          </View>
          {onClose ? (
            <View style={styles.headerCloseWrap}>
              <Ionicons name="close" size={16} color={palette.textMuted} onPress={onClose} />
            </View>
          ) : null}
        </View>
      </View>

      <View style={[styles.primaryRow, compact && styles.primaryRowCompact]}>
        <View style={[styles.windHero, compact && styles.windHeroCompact]}>
          <View
            style={[
              styles.windArrowWrap,
              { backgroundColor: `${windColor(displayWindMph)}1A` },
            ]}
          >
            <Ionicons
              name="arrow-up"
              size={16}
              color={windColor(displayWindMph)}
              style={{ transform: [{ rotate: `${((displayDirectionDeg ?? 0) + 180) % 360}deg` }] }}
            />
          </View>
          <View style={styles.windHeroCopy}>
            <Text style={styles.windHeroValue}>{forecast ? `${displayWindMph} mph` : '--'}</Text>
            <Text style={styles.windHeroSub}>
              {displayGustMph ? `gust ${displayGustMph} · ${currentDirection}` : currentDirection}
            </Text>
          </View>
        </View>
      </View>
      {compact ? (
        compactMeta ? <Text style={styles.compactMeta}>{compactMeta}</Text> : null
      ) : (
        <>
          <View style={styles.primaryStatsRow}>
            <SmallStat
              icon="thermometer-outline"
              label="Air"
              value={forecast ? `${displayTempF}°` : '--'}
              tint="#355B75"
            />
            <SmallStat
              icon="water-outline"
              label="Sea"
              value={
                displayWaveHeightFt != null
                  ? `${displayWaveHeightFt.toFixed(1)} ft`
                  : seaStateLabel(current)
              }
              tint="#5D8FCB"
            />
          </View>

          <View style={styles.supportRow}>
            {tideQuick ? (
              <SmallStat icon="swap-vertical-outline" label="Tide" value={tideQuick} tint="#5D8FCB" />
            ) : null}
            {currentFlow ? (
              <SmallStat icon="navigate-outline" label="Current" value={currentFlow} tint="#699C8C" />
            ) : null}
            {displayPrecipitation != null ? (
              <SmallStat
                icon="rainy-outline"
                label="Rain"
                value={`${displayPrecipitation}%`}
                tint="#7A8FB5"
              />
            ) : null}
          </View>
        </>
      )}

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={[styles.hourlyRow, compact && styles.hourlyRowCompact]}
      >
        {hourly.length > 0 ? (
          hourly.map((hour, index) => (
            <HourPill
              key={hour.time}
              hour={hour}
              active={index === boundedSelectedIndex}
              onPress={
                onSelectWindHour
                  ? () => onSelectWindHour(index)
                  : undefined
              }
            />
          ))
        ) : (
          <View style={styles.emptyState}>
            <Ionicons name="cloudy-outline" size={16} color={palette.textMuted} />
            <Text style={styles.emptyText}>Forecast is loading for this area.</Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: 296,
    maxWidth: 296,
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(250, 252, 251, 0.94)',
    borderRadius: 22,
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.10)',
    shadowColor: '#0C2232',
    shadowOpacity: 0.1,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 5 },
    elevation: 8,
    gap: 10,
  },
  cardCompact: {
    width: 268,
    maxWidth: 268,
    paddingHorizontal: 10,
    paddingVertical: 10,
    gap: 8,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 10,
  },
  headerCopy: {
    flex: 1,
    gap: 2,
  },
  eyebrow: {
    color: palette.accentDeep,
    fontSize: 10,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 0.75,
  },
  title: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '800',
    letterSpacing: -0.25,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 10.5,
    lineHeight: 14,
  },
  headerActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  headerBadge: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(83, 130, 160, 0.10)',
  },
  headerCloseWrap: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderWidth: 1,
    borderColor: 'rgba(35, 80, 104, 0.08)',
  },
  primaryRow: {
    flexDirection: 'row',
    gap: 8,
  },
  primaryRowCompact: {
    gap: 0,
  },
  primaryStatsRow: {
    flexDirection: 'row',
    gap: 8,
  },
  windHero: {
    flex: 1.2,
    minWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderRadius: 18,
    paddingHorizontal: 12,
    paddingVertical: 11,
    backgroundColor: 'rgba(255,255,255,0.82)',
    borderWidth: 1,
    borderColor: 'rgba(30, 62, 82, 0.06)',
  },
  windHeroCompact: {
    flex: 1,
    paddingHorizontal: 11,
    paddingVertical: 10,
  },
  windArrowWrap: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  windHeroCopy: {
    flex: 1,
    minWidth: 0,
  },
  windHeroValue: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  windHeroSub: {
    color: palette.textMuted,
    fontSize: 10.5,
    fontWeight: '600',
    marginTop: 2,
  },
  smallStat: {
    minWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flex: 1,
    borderRadius: 18,
    paddingHorizontal: 10,
    paddingVertical: 10,
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderWidth: 1,
    borderColor: 'rgba(30, 62, 82, 0.06)',
  },
  smallStatIcon: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
  },
  smallStatCopy: {
    flex: 1,
    minWidth: 0,
  },
  smallStatLabel: {
    color: palette.textDim,
    fontSize: 9.5,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.45,
  },
  smallStatValue: {
    color: palette.text,
    fontSize: 12,
    fontWeight: '800',
    marginTop: 2,
  },
  supportRow: {
    flexDirection: 'row',
    gap: 8,
  },
  compactMeta: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '600',
    lineHeight: 14,
  },
  hourlyRow: {
    gap: 8,
    paddingRight: 4,
  },
  hourlyRowCompact: {
    gap: 6,
  },
  hourPill: {
    width: 60,
    borderRadius: 16,
    backgroundColor: 'rgba(255,255,255,0.84)',
    borderWidth: 1,
    borderColor: 'rgba(18, 58, 79, 0.06)',
    paddingHorizontal: 8,
    paddingVertical: 9,
    alignItems: 'center',
  },
  hourPillActive: {
    backgroundColor: 'rgba(21, 101, 192, 0.12)',
    borderColor: 'rgba(21, 101, 192, 0.28)',
  },
  hourLabel: {
    color: palette.textSecondary,
    fontSize: 10,
    fontWeight: '700',
    marginBottom: 4,
  },
  hourWind: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '800',
    marginTop: 3,
    lineHeight: 16,
  },
  hourWindUnit: {
    color: palette.textMuted,
    fontSize: 9,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  hourDetail: {
    color: palette.textSecondary,
    fontSize: 9.5,
    marginTop: 4,
    fontWeight: '600',
  },
  emptyState: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  emptyText: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '600',
  },
});
