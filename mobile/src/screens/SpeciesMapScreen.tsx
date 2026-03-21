/**
 * OpenCatch — Species Distribution Map Screen
 *
 * Shows which species are likely found at the user's current location,
 * with monthly activity charts and habitat details.
 *
 * Competitor parity: Fishbrain "species distribution maps".
 * OpenCatch EXCEEDS with scientific habitat-based modeling, monthly
 * activity charts, and multi-species comparison in a single view.
 */

import React, { useEffect, useState } from 'react';
import {
  ScrollView,
  StyleSheet,
  Text,
  View,
  Pressable,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Rect, Text as SvgText, Line } from 'react-native-svg';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getSpeciesLikelihood,
  getMonthlyActivityChart,
  getAllDistributionSpecies,
  type SpeciesLikelihood,
  type MonthlyActivity,
} from '../services/speciesDistribution';

// ── Default Location ─────────────────────────────────────────────────────────

const DEFAULT_LAT = 37.0;
const DEFAULT_LON = -95.0;

// ── Likelihood Bar ───────────────────────────────────────────────────────────

function LikelihoodBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  let color: string;
  if (pct >= 70) color = '#2E7D32';
  else if (pct >= 50) color = '#66BB6A';
  else if (pct >= 30) color = '#FFA726';
  else color = '#BDBDBD';

  return (
    <View style={styles.likelihoodBarBg}>
      <View style={[styles.likelihoodBarFill, { width: `${pct}%`, backgroundColor: color }]} />
    </View>
  );
}

// ── Monthly Chart ────────────────────────────────────────────────────────────

function MonthlyChart({ data }: { data: MonthlyActivity[] }) {
  const chartW = 300;
  const chartH = 60;
  const barW = chartW / 12 - 3;
  const currentMonth = new Date().getMonth();

  return (
    <View style={styles.monthlyChartContainer}>
      <Svg width={chartW} height={chartH + 18}>
        {data.map((m, i) => {
          const barH = m.activity * chartH;
          const x = i * (barW + 3);
          const y = chartH - barH;
          const isCurrent = i === currentMonth;

          return (
            <React.Fragment key={i}>
              <Rect
                x={x}
                y={y}
                width={barW}
                height={Math.max(2, barH)}
                fill={isCurrent ? palette.accent : palette.accent + '60'}
                rx={2}
              />
              <SvgText
                x={x + barW / 2}
                y={chartH + 14}
                fontSize={8}
                fill={isCurrent ? palette.accent : palette.textMuted}
                textAnchor="middle"
                fontWeight={isCurrent ? '700' : '400'}
              >
                {m.label}
              </SvgText>
            </React.Fragment>
          );
        })}
      </Svg>
    </View>
  );
}

// ── Species Card ─────────────────────────────────────────────────────────────

function SpeciesCard({
  species,
  expanded,
  onToggle,
}: {
  species: SpeciesLikelihood;
  expanded: boolean;
  onToggle: () => void;
}) {
  const pct = Math.round(species.likelihood * 100);
  const confColor = species.confidence === 'high' ? '#2E7D32' : species.confidence === 'medium' ? '#FFA726' : '#BDBDBD';

  const monthly = expanded ? getMonthlyActivityChart(species.speciesId) : [];

  return (
    <Pressable style={styles.speciesCard} onPress={onToggle}>
      <View style={styles.speciesHeader}>
        <View style={styles.speciesLeft}>
          <Ionicons name={species.icon as any} size={28} color={palette.accent} />
          <View style={styles.speciesInfo}>
            <Text style={styles.speciesName}>{species.commonName}</Text>
            <View style={styles.confRow}>
              <View style={[styles.confDot, { backgroundColor: confColor }]} />
              <Text style={styles.confText}>{species.confidence} confidence</Text>
            </View>
          </View>
        </View>
        <View style={styles.speciesRight}>
          <Text style={styles.speciesPct}>{pct}%</Text>
          <Ionicons
            name={expanded ? 'chevron-up' : 'chevron-down'}
            size={18}
            color={palette.textMuted}
          />
        </View>
      </View>

      <LikelihoodBar value={species.likelihood} />

      {expanded && (
        <View style={styles.expandedSection}>
          <View style={styles.detailRow}>
            <Ionicons name="calendar-outline" size={16} color={palette.textMuted} />
            <Text style={styles.detailText}>Peak: {species.seasonalPeak}</Text>
          </View>
          <View style={styles.detailRow}>
            <Ionicons name="map-outline" size={16} color={palette.textMuted} />
            <Text style={styles.detailText}>{species.preferredHabitat}</Text>
          </View>

          {monthly.length > 0 && (
            <View style={styles.monthlySection}>
              <Text style={styles.monthlyLabel}>Monthly Activity</Text>
              <MonthlyChart data={monthly} />
            </View>
          )}
        </View>
      )}
    </Pressable>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function SpeciesMapScreen() {
  const [species, setSpecies] = useState<SpeciesLikelihood[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [waterType, setWaterType] = useState<'lake' | 'river' | 'reservoir'>('lake');

  useEffect(() => {
    const data = getSpeciesLikelihood(DEFAULT_LAT, DEFAULT_LON, {
      waterType,
    });
    setSpecies(data);
  }, [waterType]);

  const waterTypes: { key: 'lake' | 'river' | 'reservoir'; label: string; icon: string }[] = [
    { key: 'lake', label: 'Lake', icon: 'water-outline' },
    { key: 'river', label: 'River', icon: 'git-merge-outline' },
    { key: 'reservoir', label: 'Reservoir', icon: 'layers-outline' },
  ];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Header */}
      <View style={styles.headerCard}>
        <Ionicons name="analytics-outline" size={28} color={palette.accent} />
        <View style={styles.headerText}>
          <Text style={styles.headerTitle}>Species Distribution</Text>
          <Text style={styles.headerSubtitle}>
            Estimated species likelihood based on habitat, climate, and seasonal patterns.
          </Text>
        </View>
      </View>

      {/* Water Type Filter */}
      <View style={styles.filterRow}>
        {waterTypes.map((wt) => (
          <Pressable
            key={wt.key}
            style={[styles.filterChip, waterType === wt.key && styles.filterChipActive]}
            onPress={() => setWaterType(wt.key)}
          >
            <Ionicons
              name={wt.icon as any}
              size={16}
              color={waterType === wt.key ? palette.accent : palette.textMuted}
            />
            <Text
              style={[styles.filterText, waterType === wt.key && styles.filterTextActive]}
            >
              {wt.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Species List */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>
          {species.length} Species in Your Area
        </Text>
        {species.map((s) => (
          <SpeciesCard
            key={s.speciesId}
            species={s}
            expanded={expandedId === s.speciesId}
            onToggle={() =>
              setExpandedId(expandedId === s.speciesId ? null : s.speciesId)
            }
          />
        ))}
      </View>

      {/* Legend */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Understanding Scores</Text>
        <View style={styles.card}>
          <LegendRow color="#2E7D32" label="70-100%" desc="Very likely \u2014 prime habitat and range" />
          <LegendRow color="#66BB6A" label="50-69%" desc="Likely \u2014 good habitat overlap" />
          <LegendRow color="#FFA726" label="30-49%" desc="Possible \u2014 edge of range or habitat" />
          <LegendRow color="#BDBDBD" label="0-29%" desc="Unlikely \u2014 outside typical range" />
        </View>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

function LegendRow({ color, label, desc }: { color: string; label: string; desc: string }) {
  return (
    <View style={styles.legendRow}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <View style={styles.legendTextCol}>
        <Text style={styles.legendLabel}>{label}</Text>
        <Text style={styles.legendDesc}>{desc}</Text>
      </View>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 20 },

  headerCard: {
    backgroundColor: palette.accentDim,
    borderRadius: 14,
    padding: 18,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
  },
  headerText: { flex: 1 },
  headerTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  headerSubtitle: {
    fontSize: 13,
    color: palette.textSecondary,
    marginTop: 4,
    lineHeight: 18,
  },

  filterRow: {
    flexDirection: 'row',
    gap: 10,
  },
  filterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
  },
  filterChipActive: {
    backgroundColor: palette.accentLight,
    borderColor: palette.accent,
  },
  filterText: {
    fontSize: 13,
    color: palette.textMuted,
    fontWeight: '600',
  },
  filterTextActive: {
    color: palette.accent,
  },

  section: { gap: 10 },
  sectionTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
    paddingLeft: 4,
  },

  speciesCard: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 10,
  },
  speciesHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  speciesLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    flex: 1,
  },
  speciesInfo: { flex: 1 },
  speciesName: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
  },
  confRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
  },
  confDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  confText: {
    fontSize: 11,
    color: palette.textMuted,
    textTransform: 'capitalize',
  },
  speciesRight: {
    alignItems: 'flex-end',
    gap: 2,
  },
  speciesPct: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.text,
  },

  likelihoodBarBg: {
    height: 6,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 3,
    overflow: 'hidden',
  },
  likelihoodBarFill: {
    height: '100%',
    borderRadius: 3,
  },

  expandedSection: {
    marginTop: 8,
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: palette.borderLight,
    paddingTop: 12,
  },
  detailRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  detailText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
  },

  monthlySection: {
    marginTop: 8,
    gap: 8,
  },
  monthlyLabel: {
    fontSize: 12,
    color: palette.textMuted,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  monthlyChartContainer: {
    alignItems: 'center',
  },

  card: {
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 12,
  },

  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  legendDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  legendTextCol: { flex: 1 },
  legendLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  legendDesc: {
    fontSize: 11,
    color: palette.textMuted,
  },
});
