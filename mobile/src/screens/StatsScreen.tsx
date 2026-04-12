import React, { useEffect, useState, useCallback } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Dimensions,
  Pressable,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import Svg, {
  Circle,
  Rect,
  Path,
  G,
  Defs,
  LinearGradient,
  Stop,
  Text as SvgText,
} from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { palette, scoreColor } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getAllCatches,
  getPersonalBests,
  subscribeToCatchUpdates,
  type EnhancedCatch,
  type PersonalBest,
} from '../services/catchEnhancements';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

// ── Types ────────────────────────────────────────────────────────────────────

interface CatchRecord {
  id: string;
  date: string;
  species: string;
  weightLb: number;
  lengthIn?: number;
  location: string;
  bait: string;
  weather: string;
  waterTemp?: number;
}

interface SpeciesStats {
  species: string;
  count: number;
  totalWeight: number;
  avgWeight: number;
  bestWeight: number;
  bestDate: string;
}

interface MonthlyStats {
  month: string;
  catches: number;
  avgWeight: number;
}

type TimeRange = 'all' | 'year' | '90days' | '30days';

// ── Adapters ─────────────────────────────────────────────────────────────────

/**
 * Convert EnhancedCatch records from AsyncStorage to the CatchRecord shape
 * used by this screen's statistics/charts.
 */
function adaptCatches(enhanced: EnhancedCatch[]): CatchRecord[] {
  return enhanced.map((c) => ({
    id: c.id,
    date: new Date(c.timestamp).toISOString().slice(0, 10),
    species: c.species,
    weightLb: c.weight ?? 0,
    lengthIn: c.length,
    location: c.locationName ?? `${c.lat.toFixed(2)}, ${c.lon.toFixed(2)}`,
    bait: c.bait ?? 'Unknown',
    weather: c.cloudCover ?? 'Unknown',
    waterTemp: c.waterTemp,
  }));
}

function filterByTimeRange(catches: CatchRecord[], range: TimeRange): CatchRecord[] {
  if (range === 'all') return catches;
  const now = Date.now();
  const msMap: Record<string, number> = {
    year: 365 * 24 * 60 * 60 * 1000,
    '90days': 90 * 24 * 60 * 60 * 1000,
    '30days': 30 * 24 * 60 * 60 * 1000,
  };
  const cutoff = now - (msMap[range] ?? 0);
  return catches.filter((c) => new Date(c.date).getTime() >= cutoff);
}

// ── Computation ──────────────────────────────────────────────────────────────

function computeSpeciesStats(catches: CatchRecord[]): SpeciesStats[] {
  const map = new Map<string, CatchRecord[]>();
  for (const c of catches) {
    if (!map.has(c.species)) map.set(c.species, []);
    map.get(c.species)!.push(c);
  }

  return Array.from(map.entries())
    .map(([species, records]) => {
      const withWeight = records.filter((r) => r.weightLb > 0);
      const totalWeight = withWeight.reduce((s, r) => s + r.weightLb, 0);
      const best = withWeight.length > 0
        ? withWeight.reduce((b, r) => r.weightLb > b.weightLb ? r : b, withWeight[0])
        : records[0];
      return {
        species,
        count: records.length,
        totalWeight,
        avgWeight: withWeight.length > 0 ? totalWeight / withWeight.length : 0,
        bestWeight: best?.weightLb ?? 0,
        bestDate: best?.date ?? '',
      };
    })
    .sort((a, b) => b.count - a.count);
}

function computeMonthlyStats(catches: CatchRecord[]): MonthlyStats[] {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const map = new Map<number, CatchRecord[]>();

  for (const c of catches) {
    const month = new Date(c.date).getMonth();
    if (!map.has(month)) map.set(month, []);
    map.get(month)!.push(c);
  }

  return months.map((label, i) => {
    const records = map.get(i) || [];
    const withWeight = records.filter((r) => r.weightLb > 0);
    const totalWeight = withWeight.reduce((s, r) => s + r.weightLb, 0);
    return {
      month: label,
      catches: records.length,
      avgWeight: withWeight.length > 0 ? totalWeight / withWeight.length : 0,
    };
  });
}

// ── Chart Components ─────────────────────────────────────────────────────────

function SpeciesPieChart({ stats }: { stats: SpeciesStats[] }) {
  const total = stats.reduce((s, sp) => s + sp.count, 0);
  if (total === 0) return null;

  const colors = [palette.accent, palette.pinHot, palette.warning, palette.success, '#7E57C2', '#26A69A'];
  const size = 120;
  const r = 50;
  const cx = size / 2;
  const cy = size / 2;
  let startAngle = -90;

  const slices = stats.map((sp, i) => {
    const pct = sp.count / total;
    const angle = pct * 360;
    const endAngle = startAngle + angle;

    const largeArc = angle > 180 ? 1 : 0;
    const startRad = (startAngle * Math.PI) / 180;
    const endRad = (endAngle * Math.PI) / 180;

    const x1 = cx + r * Math.cos(startRad);
    const y1 = cy + r * Math.sin(startRad);
    const x2 = cx + r * Math.cos(endRad);
    const y2 = cy + r * Math.sin(endRad);

    const d = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`;
    startAngle = endAngle;

    return { d, color: colors[i % colors.length], species: sp.species, count: sp.count, pct };
  });

  return (
    <View style={s.pieContainer}>
      <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {slices.map((sl, i) => (
          <Path key={i} d={sl.d} fill={sl.color} />
        ))}
        <Circle cx={cx} cy={cy} r={25} fill={palette.surface} />
        <SvgText x={cx} y={cy - 4} textAnchor="middle" fontSize={16} fontWeight="800" fill={palette.text}>
          {total}
        </SvgText>
        <SvgText x={cx} y={cy + 10} textAnchor="middle" fontSize={9} fill={palette.textMuted}>
          total
        </SvgText>
      </Svg>
      <View style={s.pieLegend}>
        {slices.map((sl, i) => (
          <View key={i} style={s.legendRow}>
            <View style={[s.legendDot, { backgroundColor: sl.color }]} />
            <Text style={s.legendSpecies}>{sl.species}</Text>
            <Text style={s.legendCount}>{sl.count} ({Math.round(sl.pct * 100)}%)</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function MonthlyCatchChart({ data }: { data: MonthlyStats[] }) {
  const chartW = SCREEN_WIDTH - 48;
  const chartH = 100;
  const maxCatches = Math.max(1, ...data.map((d) => d.catches));
  const barW = chartW / 12 - 4;

  return (
    <View style={s.chartContainer}>
      <Svg width={chartW} height={chartH + 20} viewBox={`0 0 ${chartW} ${chartH + 20}`}>
        {data.map((d, i) => {
          const barH = (d.catches / maxCatches) * chartH;
          const x = i * (barW + 4) + 2;
          const y = chartH - barH;
          const hasData = d.catches > 0;

          return (
            <G key={i}>
              <Rect x={x} y={0} width={barW} height={chartH} rx={3} fill={palette.surfaceRaised} opacity={0.4} />
              {hasData && (
                <Rect x={x} y={y} width={barW} height={barH} rx={3} fill={palette.accent} opacity={0.75} />
              )}
              {hasData && (
                <SvgText x={x + barW / 2} y={y - 4} textAnchor="middle" fontSize={9} fontWeight="700" fill={palette.accent}>
                  {d.catches}
                </SvgText>
              )}
              <SvgText x={x + barW / 2} y={chartH + 14} textAnchor="middle" fontSize={8} fill={palette.textMuted}>
                {d.month}
              </SvgText>
            </G>
          );
        })}
      </Svg>
    </View>
  );
}

// ── Stat Card ────────────────────────────────────────────────────────────────

function StatCard({ icon, value, label, color }: { icon: string; value: string; label: string; color?: string }) {
  return (
    <View style={s.statCard}>
      <Ionicons name={icon as any} size={20} color={color || palette.accent} />
      <Text style={[s.statValue, color ? { color } : null]}>{value}</Text>
      <Text style={s.statLabel}>{label}</Text>
    </View>
  );
}

// ── Empty State ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <View style={s.emptyContainer}>
      <Ionicons name="fish-outline" size={64} color={palette.textDim} />
      <Text style={s.emptyTitle}>No Catches Yet</Text>
      <Text style={s.emptySubtitle}>
        Log your first catch using the Catch Report screen and your stats will appear here.
      </Text>
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function StatsScreen() {
  const [timeRange, setTimeRange] = useState<TimeRange>('all');
  const [allCatches, setAllCatches] = useState<CatchRecord[]>([]);
  const [personalBests, setPersonalBests] = useState<PersonalBest[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const [enhancedCatches, bests] = await Promise.all([
        getAllCatches(),
        getPersonalBests(),
      ]);
      setAllCatches(adaptCatches(enhancedCatches));
      setPersonalBests(bests);
    } catch (err) {
      setError('Unable to load catch history.');
      console.warn('[StatsScreen] Failed to load catches:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const unsubscribe = subscribeToCatchUpdates(() => {
      loadData();
    });
    return unsubscribe;
  }, [loadData]);

  const onRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  const catches = filterByTimeRange(allCatches, timeRange);

  // Loading state
  if (loading) {
    return (
      <View style={[s.screen, s.centerContent]}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={s.loadingText}>Loading your stats...</Text>
      </View>
    );
  }

  // Empty state
  if (allCatches.length === 0 && !error) {
    return (
      <View style={s.screen}>
        <ScrollView
          contentContainerStyle={s.content}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.accent} />
          }
        >
          <View style={s.header}>
            <Text style={s.title}>My Stats</Text>
            <Text style={s.subtitle}>Your fishing analytics & patterns</Text>
          </View>
          <EmptyState />
        </ScrollView>
      </View>
    );
  }

  const speciesStats = computeSpeciesStats(catches);
  const monthlyStats = computeMonthlyStats(catches);

  const totalCatches = catches.length;
  const catchesWithWeight = catches.filter((c) => c.weightLb > 0);
  const totalWeight = catchesWithWeight.reduce((s, c) => s + c.weightLb, 0);
  const avgWeight = catchesWithWeight.length > 0 ? totalWeight / catchesWithWeight.length : 0;
  const bestCatch = catchesWithWeight.length > 0
    ? catchesWithWeight.reduce((b, c) => c.weightLb > b.weightLb ? c : b, catchesWithWeight[0])
    : null;
  const uniqueLocations = new Set(catches.map((c) => c.location)).size;
  const uniqueSpecies = new Set(catches.map((c) => c.species)).size;

  // Top bait analysis
  const baitCounts = new Map<string, number>();
  for (const c of catches) {
    if (c.bait && c.bait !== 'Unknown') {
      baitCounts.set(c.bait, (baitCounts.get(c.bait) || 0) + 1);
    }
  }
  const topBait = baitCounts.size > 0
    ? Array.from(baitCounts.entries()).sort((a, b) => b[1] - a[1])[0]
    : null;

  return (
    <View style={s.screen}>
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.accent} />
        }
      >
        {/* Header */}
        <View style={s.header}>
          <Text style={s.title}>My Stats</Text>
          <Text style={s.subtitle}>Your fishing analytics & patterns</Text>
        </View>

        {/* Error banner */}
        {error && (
          <View style={s.errorBanner}>
            <Ionicons name="warning-outline" size={14} color={palette.warning} />
            <Text style={s.errorBannerText}>{error}</Text>
            <Pressable onPress={loadData}>
              <Text style={s.errorRetryText}>Retry</Text>
            </Pressable>
          </View>
        )}

        {/* Time range filter */}
        <View style={s.filterRow}>
          {(['all', 'year', '90days', '30days'] as TimeRange[]).map((r) => {
            const labels: Record<TimeRange, string> = { all: 'All Time', year: 'This Year', '90days': '90 Days', '30days': '30 Days' };
            const isActive = timeRange === r;
            return (
              <Pressable
                key={r}
                style={[s.filterChip, isActive && s.filterChipActive]}
                onPress={() => setTimeRange(r)}
              >
                <Text style={[s.filterText, isActive && s.filterTextActive]}>
                  {labels[r]}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Filtered empty state */}
        {catches.length === 0 && allCatches.length > 0 && (
          <View style={s.filteredEmpty}>
            <Ionicons name="calendar-outline" size={32} color={palette.textDim} />
            <Text style={s.filteredEmptyText}>No catches in this time period.</Text>
          </View>
        )}

        {catches.length > 0 && (
          <>
            {/* Summary stats */}
            <View style={s.statsGrid}>
              <StatCard icon="fish-outline" value={`${totalCatches}`} label="Total Catches" />
              <StatCard
                icon="trophy-outline"
                value={bestCatch ? `${bestCatch.weightLb} lb` : '-'}
                label="Personal Best"
                color={palette.pinHot}
              />
              <StatCard icon="scale-outline" value={avgWeight > 0 ? `${avgWeight.toFixed(1)} lb` : '-'} label="Avg Weight" />
              <StatCard icon="location-outline" value={`${uniqueLocations}`} label="Locations" />
              <StatCard icon="layers-outline" value={`${uniqueSpecies}`} label="Species" />
              <StatCard icon="color-wand-outline" value={topBait ? topBait[0] : '-'} label="Top Bait" color={palette.success} />
            </View>

            {/* Personal Bests from catchEnhancements */}
            {personalBests.length > 0 && (
              <View style={s.section}>
                <Text style={s.sectionTitle}>Personal Bests</Text>
                {personalBests.map((pb) => (
                  <View key={pb.species} style={s.pbRow}>
                    <Ionicons name="trophy" size={16} color="#FFD700" />
                    <View style={{ flex: 1 }}>
                      <Text style={s.pbSpecies}>{pb.species}</Text>
                      <Text style={s.pbMeta}>
                        {pb.weight} lb{pb.length ? ` / ${pb.length}"` : ''}
                        {pb.locationName ? ` at ${pb.locationName}` : ''}
                      </Text>
                    </View>
                    <Text style={s.pbDate}>
                      {new Date(pb.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </Text>
                  </View>
                ))}
              </View>
            )}

            {/* Species breakdown */}
            <View style={s.section}>
              <Text style={s.sectionTitle}>Species Breakdown</Text>
              <View style={s.card}>
                <SpeciesPieChart stats={speciesStats} />
              </View>
            </View>

            {/* Monthly catches */}
            <View style={s.section}>
              <Text style={s.sectionTitle}>Monthly Catches</Text>
              <View style={s.card}>
                <MonthlyCatchChart data={monthlyStats} />
              </View>
            </View>

            {/* Species detail rows */}
            <View style={s.section}>
              <Text style={s.sectionTitle}>Species Details</Text>
              {speciesStats.map((sp) => (
                <View key={sp.species} style={s.speciesRow}>
                  <View style={s.speciesInfo}>
                    <Ionicons name="fish-outline" size={16} color={palette.accent} />
                    <View>
                      <Text style={s.speciesName}>{sp.species}</Text>
                      <Text style={s.speciesMeta}>
                        {sp.count} caught{sp.avgWeight > 0 ? ` · Avg ${sp.avgWeight.toFixed(1)} lb` : ''}{sp.bestWeight > 0 ? ` · Best ${sp.bestWeight} lb` : ''}
                      </Text>
                    </View>
                  </View>
                </View>
              ))}
            </View>

            {/* Recent catches */}
            <View style={s.section}>
              <Text style={s.sectionTitle}>Recent Catches</Text>
              {catches.slice(0, 5).map((c) => (
                <View key={c.id} style={s.catchRow}>
                  <View style={s.catchDate}>
                    <Text style={s.catchDateText}>
                      {new Date(c.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </Text>
                  </View>
                  <View style={s.catchInfo}>
                    <Text style={s.catchSpecies}>{c.species}</Text>
                    <Text style={s.catchMeta}>
                      {c.weightLb > 0 ? `${c.weightLb} lb` : ''}{c.lengthIn ? ` · ${c.lengthIn}"` : ''}{c.bait !== 'Unknown' ? ` · ${c.bait}` : ''}
                    </Text>
                    <Text style={s.catchLocation}>{c.location}</Text>
                  </View>
                  {c.weightLb > 0 && (
                    <Text style={s.catchWeight}>{c.weightLb} lb</Text>
                  )}
                </View>
              ))}
            </View>
          </>
        )}

        <View style={{ height: 60 }} />
      </ScrollView>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background },
  content: { padding: 20, gap: 16 },
  centerContent: { justifyContent: 'center', alignItems: 'center' },
  loadingText: { color: palette.textMuted, fontSize: 14, marginTop: 8 },

  header: { gap: 4 },
  title: { ...typeStyles.screenTitle, color: palette.text },
  subtitle: { color: palette.textMuted, fontSize: 14 },

  // Error
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    padding: 12,
    backgroundColor: '#FFF3E0',
    borderRadius: 8,
  },
  errorBannerText: { color: palette.textSecondary, fontSize: 12, flex: 1 },
  errorRetryText: { color: palette.accent, fontSize: 12, fontWeight: '700' },

  // Empty states
  emptyContainer: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 12,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.text,
  },
  emptySubtitle: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    paddingHorizontal: 24,
    lineHeight: 20,
  },
  filteredEmpty: {
    alignItems: 'center',
    paddingVertical: 40,
    gap: 8,
  },
  filteredEmptyText: {
    color: palette.textMuted,
    fontSize: 14,
  },

  filterRow: { flexDirection: 'row', gap: 6 },
  filterChip: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, backgroundColor: palette.surface },
  filterChipActive: { backgroundColor: palette.accent },
  filterText: { color: palette.textSecondary, fontSize: 12, fontWeight: '600' },
  filterTextActive: { color: '#FFFFFF' },

  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  statCard: {
    width: (SCREEN_WIDTH - 56) / 3,
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 12,
    alignItems: 'center',
    gap: 4,
  },
  statValue: { color: palette.text, fontSize: 16, fontWeight: '700' },
  statLabel: { color: palette.textMuted, fontSize: 10, fontWeight: '500', textAlign: 'center' },

  section: { gap: 8 },
  sectionTitle: { ...typeStyles.sectionHeader, color: palette.text },
  card: { backgroundColor: palette.surface, borderRadius: 12, padding: 16 },

  chartContainer: { alignItems: 'center' },

  pieContainer: { flexDirection: 'row', alignItems: 'center', gap: 20 },
  pieLegend: { flex: 1, gap: 6 },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendSpecies: { color: palette.text, fontSize: 12, fontWeight: '600', flex: 1 },
  legendCount: { color: palette.textMuted, fontSize: 11 },

  // Personal Bests
  pbRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
  },
  pbSpecies: { color: palette.text, fontSize: 14, fontWeight: '600' },
  pbMeta: { color: palette.textMuted, fontSize: 11, marginTop: 2 },
  pbDate: { color: palette.textDim, fontSize: 11 },

  speciesRow: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
  },
  speciesInfo: { flexDirection: 'row', alignItems: 'center', gap: 10, flex: 1 },
  speciesName: { color: palette.text, fontSize: 14, fontWeight: '600' },
  speciesMeta: { color: palette.textMuted, fontSize: 11, marginTop: 2 },

  catchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
  },
  catchDate: {
    width: 44,
    alignItems: 'center',
  },
  catchDateText: { color: palette.textMuted, fontSize: 11, fontWeight: '600', textAlign: 'center' },
  catchInfo: { flex: 1, gap: 2 },
  catchSpecies: { color: palette.text, fontSize: 13, fontWeight: '600' },
  catchMeta: { color: palette.textSecondary, fontSize: 11 },
  catchLocation: { color: palette.accent, fontSize: 11, fontWeight: '500' },
  catchWeight: { color: palette.text, fontSize: 16, fontWeight: '700' },
});
