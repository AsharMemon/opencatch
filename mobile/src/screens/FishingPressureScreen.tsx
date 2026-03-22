/**
 * OpenCatch — Fishing Pressure Screen
 *
 * Shows how busy a fishing spot is right now and throughout the day.
 * Includes hourly chart, contributing factors, and recommendations.
 *
 * Competitor parity: Fishbrain "fishing pressure" indicator.
 * OpenCatch EXCEEDS with hourly breakdown, factor analysis, and avoidance tips.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import Svg, { Rect, Text as SvgText, Line } from 'react-native-svg';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  getCurrentPressure,
  getHourlyPressure,
  getPressureForecast,
  type PressureReading,
  type HourlyPressure,
  type PressureForecast,
  type PressureFactor,
} from '../services/fishingPressure';

// ── Hourly Bar Chart ─────────────────────────────────────────────────────────

function HourlyChart({ hourly, currentHour }: { hourly: HourlyPressure[]; currentHour: number }) {
  const chartW = 340;
  const chartH = 140;
  const barW = chartW / 24 - 2;
  const maxScore = 100;

  return (
    <View style={styles.chartContainer}>
      <Svg width={chartW} height={chartH + 24}>
        {hourly.map((h, i) => {
          const barH = (h.score / maxScore) * chartH;
          const x = i * (barW + 2);
          const y = chartH - barH;
          const isCurrent = h.hour === currentHour;

          let fill: string;
          if (h.score >= 80) fill = '#B71C1C';
          else if (h.score >= 60) fill = '#EF5350';
          else if (h.score >= 40) fill = '#FFA726';
          else if (h.score >= 20) fill = '#66BB6A';
          else fill = '#2E7D32';

          return (
            <React.Fragment key={h.hour}>
              <Rect
                x={x}
                y={y}
                width={barW}
                height={barH}
                fill={fill}
                rx={2}
                opacity={isCurrent ? 1 : 0.7}
                strokeWidth={isCurrent ? 1.5 : 0}
                stroke={isCurrent ? palette.text : 'none'}
              />
              {h.hour % 4 === 0 && (
                <SvgText
                  x={x + barW / 2}
                  y={chartH + 16}
                  fontSize={9}
                  fill={palette.textMuted}
                  textAnchor="middle"
                >
                  {h.hour === 0 ? '12a' : h.hour === 12 ? '12p' : h.hour < 12 ? `${h.hour}a` : `${h.hour - 12}p`}
                </SvgText>
              )}
            </React.Fragment>
          );
        })}
        {/* Current time indicator line */}
        <Line
          x1={currentHour * (barW + 2) + barW / 2}
          y1={0}
          x2={currentHour * (barW + 2) + barW / 2}
          y2={chartH}
          stroke={palette.text}
          strokeWidth={1}
          strokeDasharray="3,3"
          opacity={0.3}
        />
      </Svg>
    </View>
  );
}

// ── Factor Row ───────────────────────────────────────────────────────────────

function FactorRow({ factor }: { factor: PressureFactor }) {
  const icon = factor.impact === 'increases' ? 'arrow-up' : factor.impact === 'decreases' ? 'arrow-down' : 'remove';
  const color = factor.impact === 'increases' ? '#EF5350' : factor.impact === 'decreases' ? '#66BB6A' : palette.textMuted;

  return (
    <View style={styles.factorRow}>
      <View style={[styles.factorIcon, { backgroundColor: color + '15' }]}>
        <Ionicons name={icon as any} size={16} color={color} />
      </View>
      <View style={styles.factorTextCol}>
        <Text style={styles.factorName}>{factor.name}</Text>
        <Text style={styles.factorDetail}>{factor.detail}</Text>
      </View>
      <Text style={[styles.factorImpact, { color }]}>
        {factor.impact === 'increases' ? '+' : factor.impact === 'decreases' ? '-' : ''}
        {Math.round(factor.weight * 100)}%
      </Text>
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function FishingPressureScreen() {
  const navigation = useNavigation<any>();
  const [forecast, setForecast] = useState<PressureForecast | null>(null);

  useEffect(() => {
    const f = getPressureForecast();
    setForecast(f);
  }, []);

  const currentHour = new Date().getHours();

  const handleShowOnMap = useCallback(() => {
    hapticLight();
    navigation.navigate('Tabs', {
      screen: 'MapTab',
      params: { activateOverlay: 'fishing-pressure' },
    });
  }, [navigation]);

  const handleFindUncrowded = useCallback(() => {
    hapticLight();
    navigation.navigate('Tabs', {
      screen: 'MapTab',
      params: { activateOverlay: 'fishing-pressure', filterUncrowded: true },
    });
  }, [navigation]);

  if (!forecast) return <View style={styles.screen} />;

  const { overall, hourly, recommendation } = forecast;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Map Action Buttons */}
      <View style={styles.mapActions}>
        <Pressable style={styles.mapActionPrimary} onPress={handleShowOnMap}>
          <Ionicons name="map" size={20} color="#FFFFFF" />
          <View style={styles.mapActionTextCol}>
            <Text style={styles.mapActionTitle}>Pressure Heat Map</Text>
            <Text style={styles.mapActionSub}>See crowding across all spots</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color="#FFFFFF80" />
        </Pressable>
        <Pressable style={styles.findUncrowdedBtn} onPress={handleFindUncrowded}>
          <Ionicons name="leaf-outline" size={18} color={palette.success} />
          <Text style={styles.findUncrowdedText}>Find Uncrowded Spots</Text>
          <Ionicons name="chevron-forward" size={16} color={palette.textMuted} />
        </Pressable>
      </View>

      {/* Hero Card */}
      <View style={[styles.heroCard, { borderColor: overall.color + '30' }]}>
        <View style={[styles.heroIconCircle, { backgroundColor: overall.color + '15' }]}>
          <Ionicons name={overall.icon as any} size={36} color={overall.color} />
        </View>
        <Text style={styles.heroLabel}>{overall.label}</Text>
        <View style={styles.heroScoreRow}>
          <Text style={[styles.heroScore, { color: overall.color }]}>{overall.score}</Text>
          <Text style={styles.heroScoreUnit}>/100</Text>
        </View>
        <Text style={styles.heroDesc}>{overall.description}</Text>
      </View>

      {/* Hourly Breakdown */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Hourly Pressure</Text>
        <View style={styles.card}>
          <HourlyChart hourly={hourly} currentHour={currentHour} />
          <View style={styles.legendRow}>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: '#2E7D32' }]} />
              <Text style={styles.legendText}>Quiet</Text>
            </View>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: '#FFA726' }]} />
              <Text style={styles.legendText}>Moderate</Text>
            </View>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: '#EF5350' }]} />
              <Text style={styles.legendText}>Busy</Text>
            </View>
            <View style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: '#B71C1C' }]} />
              <Text style={styles.legendText}>Crowded</Text>
            </View>
          </View>
        </View>
      </View>

      {/* Peak Hours */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Peak Hours</Text>
        <View style={styles.card}>
          {overall.peakHours.map((ph, i) => (
            <View key={i} style={styles.peakRow}>
              <Ionicons name="time-outline" size={18} color={palette.warning} />
              <Text style={styles.peakText}>{ph}</Text>
            </View>
          ))}
          <Text style={styles.tipText}>
            Arrive early or go late to avoid the crowds.
          </Text>
        </View>
      </View>

      {/* Factors */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Contributing Factors</Text>
        <View style={styles.card}>
          {overall.factors.map((f, i) => (
            <FactorRow key={i} factor={f} />
          ))}
        </View>
      </View>

      {/* Recommendation */}
      <View style={styles.section}>
        <View style={styles.recommendCard}>
          <Ionicons name="bulb-outline" size={22} color={palette.accent} />
          <Text style={styles.recommendText}>{recommendation}</Text>
        </View>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 20 },

  heroCard: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 28,
    alignItems: 'center',
    gap: 12,
    borderWidth: 1.5,
  },
  heroIconCircle: {
    width: 72,
    height: 72,
    borderRadius: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroLabel: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  heroScoreRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
  },
  heroScore: {
    fontSize: 48,
    fontWeight: '700',
  },
  heroScoreUnit: {
    fontSize: 18,
    color: palette.textMuted,
    marginLeft: 4,
  },
  heroDesc: {
    fontSize: 14,
    color: palette.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
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

  chartContainer: {
    alignItems: 'center',
    paddingVertical: 8,
  },

  legendRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 16,
    marginTop: 12,
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

  peakRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
  },
  peakText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  tipText: {
    fontSize: 13,
    color: palette.textMuted,
    marginTop: 8,
    fontStyle: 'italic',
  },

  factorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 10,
  },
  factorIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  factorTextCol: {
    flex: 1,
  },
  factorName: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  factorDetail: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  factorImpact: {
    fontSize: 14,
    fontWeight: '700',
  },

  recommendCard: {
    backgroundColor: palette.accentDim,
    borderRadius: 12,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  recommendText: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    lineHeight: 20,
  },

  // ── Map Action Buttons ──
  mapActions: {
    gap: 10,
  },
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
  findUncrowdedBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: palette.border,
  },
  findUncrowdedText: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
});
