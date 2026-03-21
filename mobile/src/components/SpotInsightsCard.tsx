/**
 * SpotInsightsCard — Inline fishing intelligence summary for a location.
 *
 * Shows bite rating, fishing pressure, water conditions, top species,
 * and best time window. Used in MapScreen focused card and bottom sheet.
 * Data loads on-demand when the component mounts.
 */

import React, { useEffect, useState, memo } from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts } from '../theme/typography';
import { getDailyBiteForecast, formatHour } from '../services/bestTimeWindows';
import { getCurrentPressure, type PressureReading } from '../services/fishingPressure';
import { getWaterInsights, type WaterInsightsDashboard } from '../services/waterInsights';
import { getSpeciesLikelihood, type SpeciesLikelihood } from '../services/speciesDistribution';

// ── Types ────────────────────────────────────────────────────────────────────

interface SpotInsightsCardProps {
  lat: number;
  lon: number;
  locationName?: string;
  compact?: boolean; // true = minimal for focused card; false = full for expanded sheet
}

interface InsightsData {
  biteRating: number;
  biteLabel: string;
  biteColor: string;
  bestWindow: string | null;
  pressure: PressureReading;
  water: WaterInsightsDashboard | null;
  topSpecies: SpeciesLikelihood[];
}

// ── Rating helpers ───────────────────────────────────────────────────────────

function ratingColor(rating: number): string {
  if (rating >= 70) return '#2E7D32';
  if (rating >= 55) return '#66BB6A';
  if (rating >= 40) return '#FFA726';
  if (rating >= 25) return '#EF5350';
  return '#B71C1C';
}

function ratingLabel(rating: number): string {
  if (rating >= 70) return 'Great';
  if (rating >= 55) return 'Good';
  if (rating >= 40) return 'Fair';
  if (rating >= 25) return 'Slow';
  return 'Poor';
}

// ── Insights cache (prevents re-fetch on mount/unmount cycles) ───────────────

const insightsCache = new Map<string, { data: InsightsData; timestamp: number }>();
const INSIGHTS_CACHE_TTL = 5 * 60 * 1000; // 5 minutes

function getCacheKey(lat: number, lon: number): string {
  return `${lat.toFixed(3)},${lon.toFixed(3)}`;
}

// ── Component ────────────────────────────────────────────────────────────────

export const SpotInsightsCard = memo(function SpotInsightsCard({
  lat,
  lon,
  locationName,
  compact = false,
}: SpotInsightsCardProps) {
  const [data, setData] = useState<InsightsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    // Check cache first
    const key = getCacheKey(lat, lon);
    const cached = insightsCache.get(key);
    if (cached && Date.now() - cached.timestamp < INSIGHTS_CACHE_TTL) {
      setData(cached.data);
      setLoading(false);
      return;
    }

    async function loadInsights() {
      try {
        // Run all data fetches in parallel
        const [biteResult, waterResult] = await Promise.all([
          Promise.resolve(getDailyBiteForecast(lat, lon)),
          getWaterInsights(lat, lon, { locationName }).catch(() => null),
        ]);

        // Synchronous computations
        const pressure = getCurrentPressure({ lat, lon });
        const species = getSpeciesLikelihood(lat, lon);

        if (cancelled) return;

        const bestWin = biteResult.bestWindow;
        const result: InsightsData = {
          biteRating: biteResult.overallRating,
          biteLabel: biteResult.ratingLabel,
          biteColor: ratingColor(biteResult.overallRating),
          bestWindow: bestWin ? bestWin.label : null,
          pressure,
          water: waterResult,
          topSpecies: species.slice(0, 3),
        };
        setData(result);
        insightsCache.set(key, { data: result, timestamp: Date.now() });
      } catch {
        // Silently fail — card just won't show
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadInsights();
    return () => { cancelled = true; };
  }, [lat, lon, locationName]);

  if (loading) {
    return (
      <View style={s.loadingRow}>
        <ActivityIndicator size="small" color={palette.accent} />
      </View>
    );
  }

  if (!data) return null;

  // ── Compact mode (MapScreen focused card) ──────────────────────────────
  if (compact) {
    return (
      <View style={s.compactContainer}>
        {/* Bite badge + pressure in one row */}
        <View style={s.compactRow}>
          <View style={[s.biteBadge, { backgroundColor: data.biteColor + '18' }]}>
            <Ionicons name="fish" size={12} color={data.biteColor} />
            <Text style={[s.biteBadgeText, { color: data.biteColor }]}>
              {data.biteLabel}
            </Text>
          </View>
          <View style={[s.pressurePill, { backgroundColor: data.pressure.color + '18' }]}>
            <Ionicons name={data.pressure.icon as any} size={11} color={data.pressure.color} />
            <Text style={[s.pressurePillText, { color: data.pressure.color }]}>
              {data.pressure.label}
            </Text>
          </View>
          {data.bestWindow && (
            <View style={s.timePill}>
              <Ionicons name="time-outline" size={11} color={palette.accent} />
              <Text style={s.timePillText} numberOfLines={1}>{data.bestWindow}</Text>
            </View>
          )}
        </View>

        {/* Top species */}
        {data.topSpecies.length > 0 && (
          <View style={s.compactSpeciesRow}>
            {data.topSpecies.map((sp) => (
              <Text key={sp.speciesId} style={s.compactSpeciesName}>
                {sp.commonName}
              </Text>
            ))}
          </View>
        )}
      </View>
    );
  }

  // ── Full mode (expanded bottom sheet / detail) ─────────────────────────
  return (
    <View style={s.container}>
      {/* Bite Rating Row */}
      <View style={s.fullRow}>
        <View style={[s.biteBadgeFull, { backgroundColor: data.biteColor + '15' }]}>
          <Ionicons name="fish" size={16} color={data.biteColor} />
          <Text style={[s.biteBadgeFullText, { color: data.biteColor }]}>
            Bite: {data.biteLabel} ({data.biteRating}%)
          </Text>
        </View>
      </View>

      {/* Pressure + Best Time */}
      <View style={s.twoCol}>
        <View style={s.infoBlock}>
          <Ionicons name={data.pressure.icon as any} size={16} color={data.pressure.color} />
          <View style={{ flex: 1 }}>
            <Text style={s.infoLabel}>Fishing Pressure</Text>
            <Text style={[s.infoValue, { color: data.pressure.color }]}>
              {data.pressure.label}
            </Text>
            <Text style={s.infoDetail} numberOfLines={1}>
              {data.pressure.score >= 60
                ? 'Busy'
                : data.pressure.score >= 40
                  ? 'Moderate'
                  : 'Low'}
            </Text>
          </View>
        </View>
        <View style={s.infoBlock}>
          <Ionicons name="time-outline" size={16} color={palette.accent} />
          <View style={{ flex: 1 }}>
            <Text style={s.infoLabel}>Best Time Today</Text>
            <Text style={[s.infoValue, { color: palette.text }]} numberOfLines={1}>
              {data.bestWindow ?? 'No peak window'}
            </Text>
          </View>
        </View>
      </View>

      {/* Water Conditions */}
      {data.water && data.water.insights.length > 0 && (
        <View style={s.waterRow}>
          <Text style={s.sectionLabel}>Water Conditions</Text>
          <View style={s.waterChips}>
            {data.water.insights.slice(0, 3).map((insight) => (
              <View key={insight.label} style={s.waterChip}>
                <Ionicons name={insight.icon as any} size={13} color={insight.color} />
                <Text style={s.waterChipLabel}>{insight.label}</Text>
                <Text style={[s.waterChipValue, { color: insight.color }]}>
                  {insight.value}{insight.unit}
                </Text>
              </View>
            ))}
          </View>
        </View>
      )}

      {/* Top Species */}
      {data.topSpecies.length > 0 && (
        <View style={s.speciesRow}>
          <Text style={s.sectionLabel}>Top Species</Text>
          <View style={s.speciesChips}>
            {data.topSpecies.map((sp) => {
              const pct = Math.round(sp.likelihood * 100);
              return (
                <View key={sp.speciesId} style={s.speciesChip}>
                  <Text style={s.speciesChipName}>{sp.commonName}</Text>
                  <Text style={s.speciesChipPct}>{pct}%</Text>
                </View>
              );
            })}
          </View>
        </View>
      )}
    </View>
  );
});

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  loadingRow: {
    paddingVertical: 8,
    alignItems: 'center',
  },

  // ── Compact ────────────────────────────────────────────────────────
  compactContainer: {
    gap: 6,
    marginTop: 6,
  },
  compactRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap',
  },
  biteBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  biteBadgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  pressurePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 10,
  },
  pressurePillText: {
    fontSize: 11,
    fontWeight: '600',
  },
  timePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 10,
    backgroundColor: palette.accentDim,
  },
  timePillText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.accent,
    maxWidth: 100,
  },
  compactSpeciesRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  compactSpeciesName: {
    fontSize: 11,
    color: palette.textSecondary,
    fontWeight: '500',
  },

  // ── Full ───────────────────────────────────────────────────────────
  container: {
    gap: 12,
    paddingVertical: 4,
  },
  fullRow: {
    flexDirection: 'row',
  },
  biteBadgeFull: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
  },
  biteBadgeFullText: {
    fontSize: 14,
    fontWeight: '700',
  },
  biteScore: {
    fontSize: 13,
    fontWeight: '600',
    marginLeft: 4,
  },
  twoCol: {
    flexDirection: 'row',
    gap: 10,
  },
  infoBlock: {
    flex: 1,
    flexDirection: 'row',
    gap: 8,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 10,
    alignItems: 'flex-start',
  },
  infoLabel: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '600',
  },
  infoValue: {
    fontSize: 13,
    fontWeight: '700',
    marginTop: 1,
  },
  infoDetail: {
    fontSize: 10,
    color: palette.textMuted,
    marginTop: 2,
  },
  sectionLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.textSecondary,
    marginBottom: 6,
  },
  waterRow: {},
  waterChips: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  waterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  waterChipLabel: {
    fontSize: 11,
    color: palette.textMuted,
    fontWeight: '500',
  },
  waterChipValue: {
    fontSize: 12,
    fontWeight: '700',
  },
  speciesRow: {},
  speciesChips: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
  },
  speciesChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 5,
  },
  speciesChipName: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
  },
  speciesChipPct: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.accent,
  },
});
