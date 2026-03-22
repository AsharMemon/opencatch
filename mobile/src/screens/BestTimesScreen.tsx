/**
 * OpenCatch — Best Fishing Times Screen
 *
 * Shows optimal fishing hours for today based on solunar theory,
 * weather, sunrise/sunset, and species-specific patterns.
 *
 * Competitor parity: FishAngler "bite windows", Fishbrain "BiteTime".
 * OpenCatch EXCEEDS with 24-hour visual timeline, multi-factor scoring,
 * and 7-day weekly overview.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  View,
  Pressable,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import * as Location from 'expo-location';
import Svg, { Circle, Line, Rect, Text as SvgText } from 'react-native-svg';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  getDailyBiteForecast,
  getWeeklyBiteForecast,
  formatHour,
  type DailyBiteForecast,
  type TimeWindow,
} from '../services/bestTimeWindows';

// ── Defaults ─────────────────────────────────────────────────────────────────

// Use a central US location as fallback
const DEFAULT_LAT = 37.0;
const DEFAULT_LON = -95.0;

// ── 24-Hour Timeline ─────────────────────────────────────────────────────────

function Timeline({ forecast }: { forecast: DailyBiteForecast }) {
  const chartW = 340;
  const chartH = 100;
  const barW = chartW / 24 - 1;
  const currentHour = new Date().getHours();

  return (
    <View style={styles.timelineContainer}>
      <Svg width={chartW} height={chartH + 28}>
        {forecast.hourlyScores.map((score, h) => {
          const barH = (score / 100) * chartH;
          const x = h * (barW + 1);
          const y = chartH - barH;
          const isCurrent = h === currentHour;

          let fill: string;
          if (score >= 80) fill = '#E53935';
          else if (score >= 60) fill = '#FB8C00';
          else if (score >= 45) fill = '#0A6EBD';
          else if (score >= 25) fill = '#78909C';
          else fill = '#B0BEC5';

          return (
            <React.Fragment key={h}>
              <Rect
                x={x}
                y={y}
                width={barW}
                height={Math.max(2, barH)}
                fill={fill}
                rx={2}
                opacity={isCurrent ? 1 : 0.75}
                strokeWidth={isCurrent ? 1.5 : 0}
                stroke={isCurrent ? '#1A1A18' : 'none'}
              />
              {h % 3 === 0 && (
                <SvgText
                  x={x + barW / 2}
                  y={chartH + 16}
                  fontSize={8}
                  fill={palette.textMuted}
                  textAnchor="middle"
                >
                  {h === 0 ? '12a' : h === 12 ? '12p' : h < 12 ? `${h}a` : `${h - 12}p`}
                </SvgText>
              )}
            </React.Fragment>
          );
        })}

        {/* Sunrise marker */}
        {forecast.sunrise && (
          <>
            <Line
              x1={Math.round(forecast.sunrise) * (barW + 1)}
              y1={0}
              x2={Math.round(forecast.sunrise) * (barW + 1)}
              y2={chartH}
              stroke="#FFA726"
              strokeWidth={1}
              strokeDasharray="4,2"
            />
            <SvgText
              x={Math.round(forecast.sunrise) * (barW + 1)}
              y={chartH + 26}
              fontSize={7}
              fill="#FFA726"
              textAnchor="middle"
            >
              rise
            </SvgText>
          </>
        )}

        {/* Sunset marker */}
        {forecast.sunset && (
          <>
            <Line
              x1={Math.round(forecast.sunset) * (barW + 1)}
              y1={0}
              x2={Math.round(forecast.sunset) * (barW + 1)}
              y2={chartH}
              stroke="#E65100"
              strokeWidth={1}
              strokeDasharray="4,2"
            />
            <SvgText
              x={Math.round(forecast.sunset) * (barW + 1)}
              y={chartH + 26}
              fontSize={7}
              fill="#E65100"
              textAnchor="middle"
            >
              set
            </SvgText>
          </>
        )}
      </Svg>
    </View>
  );
}

// ── Window Card ──────────────────────────────────────────────────────────────

function WindowCard({ window: w }: { window: TimeWindow }) {
  const qualityColor = w.quality === 'prime' ? '#E53935' : w.quality === 'good' ? '#FB8C00' : '#0A6EBD';
  const qualityLabel = w.quality === 'prime' ? 'Prime' : w.quality === 'good' ? 'Good' : 'Fair';
  const qualityIcon = w.quality === 'prime' ? 'flame' : w.quality === 'good' ? 'trending-up' : 'fish';

  return (
    <View style={[styles.windowCard, { borderLeftColor: qualityColor, borderLeftWidth: 4 }]}>
      <View style={styles.windowHeader}>
        <View style={[styles.qualityBadge, { backgroundColor: qualityColor + '15' }]}>
          <Ionicons name={qualityIcon as any} size={14} color={qualityColor} />
          <Text style={[styles.qualityText, { color: qualityColor }]}>{qualityLabel}</Text>
        </View>
        <Text style={[styles.windowScore, { color: qualityColor }]}>{w.score}%</Text>
      </View>
      <Text style={styles.windowTime}>{w.label}</Text>
      <View style={styles.reasonsList}>
        {w.reasons.map((r, i) => (
          <View key={i} style={styles.reasonRow}>
            <Ionicons name="checkmark-circle" size={14} color={palette.success} />
            <Text style={styles.reasonText}>{r}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ── Weekly Mini Bars ─────────────────────────────────────────────────────────

function WeeklyOverview({ forecasts }: { forecasts: DailyBiteForecast[] }) {
  const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const today = new Date().getDay();

  return (
    <View style={styles.weeklyRow}>
      {forecasts.map((f, i) => {
        const dayIndex = (today + i) % 7;
        const rating = f.overallRating;
        let color: string;
        if (rating >= 70) color = '#E53935';
        else if (rating >= 55) color = '#FB8C00';
        else if (rating >= 40) color = '#0A6EBD';
        else color = '#78909C';

        return (
          <View key={i} style={styles.weeklyDay}>
            <Text style={[styles.weeklyLabel, i === 0 && styles.weeklyToday]}>
              {i === 0 ? 'Today' : dayLabels[dayIndex]}
            </Text>
            <View style={styles.weeklyBarBg}>
              <View
                style={[
                  styles.weeklyBarFill,
                  { height: `${rating}%`, backgroundColor: color },
                ]}
              />
            </View>
            <Text style={[styles.weeklyScore, { color }]}>{rating}</Text>
          </View>
        );
      })}
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function BestTimesScreen() {
  const navigation = useNavigation<any>();
  const [forecast, setForecast] = useState<DailyBiteForecast | null>(null);
  const [weekly, setWeekly] = useState<DailyBiteForecast[]>([]);
  const [locationLabel, setLocationLabel] = useState('');

  const handleShowOnMap = useCallback(() => {
    hapticLight();
    navigation.navigate('Tabs', {
      screen: 'MapTab',
      params: { activateOverlay: 'bite-time' },
    });
  }, [navigation]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let lat = DEFAULT_LAT;
      let lon = DEFAULT_LON;
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status === 'granted') {
          const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          lat = pos.coords.latitude;
          lon = pos.coords.longitude;
          if (!cancelled) setLocationLabel(`${lat.toFixed(2)}, ${lon.toFixed(2)}`);
        }
      } catch {
        // Use defaults
      }
      if (cancelled) return;
      const f = getDailyBiteForecast(lat, lon);
      setForecast(f);
      const w = getWeeklyBiteForecast(lat, lon);
      setWeekly(w);
    })();
    return () => { cancelled = true; };
  }, []);

  if (!forecast) {
    return (
      <View style={[styles.screen, { alignItems: 'center', justifyContent: 'center' }]}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  const ratingColor = forecast.overallRating >= 70 ? '#E53935'
    : forecast.overallRating >= 55 ? '#FB8C00'
    : forecast.overallRating >= 40 ? '#0A6EBD'
    : '#78909C';

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Map Action */}
      <Pressable style={styles.mapActionPrimary} onPress={handleShowOnMap}>
        <Ionicons name="map" size={20} color="#FFFFFF" />
        <View style={styles.mapActionTextCol}>
          <Text style={styles.mapActionTitle}>Bite Map</Text>
          <Text style={styles.mapActionSub}>See which spots are hot right now</Text>
        </View>
        <Ionicons name="chevron-forward" size={18} color="#FFFFFF80" />
      </Pressable>

      {/* Overall Rating Hero */}
      <View style={styles.heroCard}>
        {locationLabel ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 8 }}>
            <Ionicons name="location-outline" size={12} color={palette.textMuted} />
            <Text style={{ fontSize: 11, color: palette.textMuted }}>{locationLabel}</Text>
          </View>
        ) : null}
        <View style={styles.heroRow}>
          <View style={styles.heroLeft}>
            <Text style={styles.heroRatingLabel}>Today's Bite Rating</Text>
            <View style={styles.heroScoreRow}>
              <Text style={[styles.heroScore, { color: ratingColor }]}>{forecast.overallRating}</Text>
              <Text style={styles.heroScoreMax}>/100</Text>
            </View>
            <Text style={[styles.heroQuality, { color: ratingColor }]}>{forecast.ratingLabel}</Text>
          </View>
          <View style={styles.heroRight}>
            <View style={styles.astroRow}>
              <Ionicons name="sunny-outline" size={16} color="#FFA726" />
              <Text style={styles.astroText}>{formatHour(Math.round(forecast.sunrise))}</Text>
            </View>
            <View style={styles.astroRow}>
              <Ionicons name="moon-outline" size={16} color="#5C6BC0" />
              <Text style={styles.astroText}>{forecast.moonPhase}</Text>
            </View>
            <View style={styles.astroRow}>
              <Ionicons name="sunny" size={16} color="#E65100" />
              <Text style={styles.astroText}>{formatHour(Math.round(forecast.sunset))}</Text>
            </View>
          </View>
        </View>
      </View>

      {/* 24-Hour Timeline */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>24-Hour Activity Timeline</Text>
        <View style={styles.card}>
          <Timeline forecast={forecast} />
          <View style={styles.legendRow}>
            <LegendItem color="#E53935" label="Prime" />
            <LegendItem color="#FB8C00" label="Good" />
            <LegendItem color="#0A6EBD" label="Fair" />
            <LegendItem color="#78909C" label="Poor" />
          </View>
        </View>
      </View>

      {/* Best Windows */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Best Fishing Windows</Text>
        {forecast.windows.length > 0 ? (
          forecast.windows.slice(0, 4).map((w, i) => (
            <WindowCard key={i} window={w} />
          ))
        ) : (
          <View style={styles.card}>
            <Text style={styles.emptyText}>No strong bite windows today. Consider trying early morning or late evening.</Text>
          </View>
        )}
      </View>

      {/* 7-Day Overview */}
      {weekly.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>7-Day Outlook</Text>
          <View style={styles.card}>
            <WeeklyOverview forecasts={weekly} />
          </View>
        </View>
      )}

      {/* Solunar Periods */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Solunar Periods</Text>
        <View style={styles.card}>
          <Text style={styles.solunarLabel}>Major Periods (peak activity ~2 hrs)</Text>
          {forecast.solunarMajors.map((p, i) => (
            <View key={`maj-${i}`} style={styles.solunarRow}>
              <View style={[styles.solunarDot, { backgroundColor: '#E53935' }]} />
              <Text style={styles.solunarTime}>
                {formatHour(Math.floor(p.start))} \u2013 {formatHour(Math.ceil(p.end))}
              </Text>
            </View>
          ))}

          <Text style={[styles.solunarLabel, { marginTop: 16 }]}>Minor Periods (moderate activity ~1 hr)</Text>
          {forecast.solunarMinors.map((p, i) => (
            <View key={`min-${i}`} style={styles.solunarRow}>
              <View style={[styles.solunarDot, { backgroundColor: '#FFA726' }]} />
              <Text style={styles.solunarTime}>
                {formatHour(Math.floor(p.start))} \u2013 {formatHour(Math.ceil(p.end))}
              </Text>
            </View>
          ))}
        </View>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <Text style={styles.legendText}>{label}</Text>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 20 },

  heroCard: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: palette.border,
  },
  heroRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  heroLeft: { flex: 1 },
  heroRight: {
    justifyContent: 'center',
    gap: 8,
  },
  heroRatingLabel: {
    fontSize: 13,
    color: palette.textMuted,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  heroScoreRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    marginTop: 4,
  },
  heroScore: {
    fontSize: 52,
    fontWeight: '700',
  },
  heroScoreMax: {
    fontSize: 20,
    color: palette.textMuted,
    marginLeft: 2,
  },
  heroQuality: {
    ...typeStyles.sectionHeader,
    marginTop: 2,
  },
  astroRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  astroText: {
    fontSize: 13,
    color: palette.textSecondary,
  },

  section: { gap: 10 },
  sectionTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    paddingLeft: 4,
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },

  timelineContainer: {
    alignItems: 'center',
    paddingVertical: 4,
  },

  legendRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 16,
    marginTop: 10,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  legendText: {
    fontSize: 11,
    color: palette.textMuted,
  },

  windowCard: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },
  windowHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  qualityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  qualityText: {
    fontSize: 12,
    fontWeight: '700',
  },
  windowScore: {
    fontSize: 22,
    fontWeight: '700',
  },
  windowTime: {
    fontSize: 17,
    fontWeight: '600',
    color: palette.text,
    marginTop: 8,
  },
  reasonsList: {
    marginTop: 10,
    gap: 6,
  },
  reasonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  reasonText: {
    fontSize: 13,
    color: palette.textSecondary,
  },

  weeklyRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    height: 120,
  },
  weeklyDay: {
    alignItems: 'center',
    flex: 1,
    gap: 4,
  },
  weeklyLabel: {
    fontSize: 10,
    color: palette.textMuted,
    fontWeight: '600',
  },
  weeklyToday: {
    color: palette.accent,
    fontWeight: '700',
  },
  weeklyBarBg: {
    width: 20,
    height: 70,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 4,
    overflow: 'hidden',
    justifyContent: 'flex-end',
  },
  weeklyBarFill: {
    width: '100%',
    borderRadius: 4,
  },
  weeklyScore: {
    fontSize: 11,
    fontWeight: '700',
  },

  solunarLabel: {
    fontSize: 13,
    color: palette.textMuted,
    fontWeight: '600',
    marginBottom: 8,
  },
  solunarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 4,
  },
  solunarDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  solunarTime: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },

  emptyText: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    padding: 20,
  },

  // ── Map Action ──
  mapActionPrimary: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.accent,
    borderRadius: 14,
    padding: 16,
    gap: 14,
    shadowColor: palette.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  mapActionTextCol: {
    flex: 1,
  },
  mapActionTitle: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
  mapActionSub: {
    color: '#FFFFFFA0',
    fontSize: 12,
    marginTop: 2,
  },
});
