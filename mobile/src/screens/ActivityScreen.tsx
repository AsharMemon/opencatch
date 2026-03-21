import React, { useEffect, useState, useCallback } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { ActivityHeatmap } from '../components/ActivityHeatmap';
import { getAllCatches, type EnhancedCatch } from '../services/catchEnhancements';
import { trackRecorder, type FishingTrack } from '../services/trackRecorder';

// ── Types ────────────────────────────────────────────────────────────────────

interface StatCard {
  label: string;
  value: string;
  ionicon: string;
}

interface ActivityEntry {
  id: string;
  date: string;
  time: string;
  location: string;
  fishCount: number;
  speciesIcons: string[];
  temperature: number | null;
  weatherIonicon: string;
  photoPlaceholder: boolean;
}

type FilterChip = 'All' | 'This Week' | 'This Month' | 'This Year';

const FILTER_CHIPS: FilterChip[] = ['All', 'This Week', 'This Month', 'This Year'];

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(ts: number): string {
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function isInRange(ts: number, filter: FilterChip): boolean {
  if (filter === 'All') return true;
  const now = Date.now();
  const d = new Date(ts);
  const today = new Date();
  if (filter === 'This Week') {
    const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
    return ts >= weekAgo;
  }
  if (filter === 'This Month') {
    return d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear();
  }
  // This Year
  return d.getFullYear() === today.getFullYear();
}

function buildEntries(catches: EnhancedCatch[]): ActivityEntry[] {
  return catches
    .sort((a, b) => b.timestamp - a.timestamp)
    .map((c) => ({
      id: c.id,
      date: formatDate(c.timestamp),
      time: formatTime(c.timestamp),
      location: c.locationName || c.species || 'Catch',
      fishCount: 1,
      speciesIcons: c.species ? ['fish-outline'] : [],
      temperature: c.airTemp ?? null,
      weatherIonicon: 'partly-sunny-outline',
      photoPlaceholder: !(c.photos && c.photos.length > 0),
    }));
}

function buildStats(catches: EnhancedCatch[], tracks: FishingTrack[]): StatCard[] {
  let bestDay = 0;
  const dayCounts = new Map<string, number>();
  for (const c of catches) {
    const d = new Date(c.timestamp).toISOString().slice(0, 10);
    const count = (dayCounts.get(d) ?? 0) + 1;
    dayCounts.set(d, count);
    if (count > bestDay) bestDay = count;
  }

  return [
    { label: 'Total Trips', value: String(tracks.length), ionicon: 'boat-outline' },
    { label: 'Total Fish', value: String(catches.length), ionicon: 'fish-outline' },
    { label: 'Best Day', value: String(bestDay), ionicon: 'trophy-outline' },
  ];
}

// ── Stat Card Component ──────────────────────────────────────────────────────

function StatCardView({ stat }: { stat: StatCard }) {
  return (
    <View style={styles.statCard}>
      <Ionicons name={stat.ionicon as any} size={22} color={palette.accent} />
      <Text style={styles.statValue}>{stat.value}</Text>
      <Text style={styles.statLabel}>{stat.label}</Text>
    </View>
  );
}

// ── Activity Card Component ──────────────────────────────────────────────────

function ActivityCard({ entry }: { entry: ActivityEntry }) {
  return (
    <View style={styles.activityCard}>
      <View style={styles.activityRow}>
        {/* Photo thumbnail placeholder */}
        <View style={styles.photoThumb}>
          <Ionicons
            name={entry.photoPlaceholder ? 'camera-outline' : 'fish-outline'}
            size={22}
            color={palette.textMuted}
          />
        </View>

        {/* Main content */}
        <View style={styles.activityContent}>
          <View style={styles.activityHeader}>
            <Text style={styles.activityLocation} numberOfLines={1}>
              {entry.location}
            </Text>
          </View>

          <Text style={styles.activityDateTime}>
            {entry.date} {'\u00B7'} {entry.time}
          </Text>

          <View style={styles.activityMeta}>
            {/* Species tags */}
            <View style={styles.speciesTags}>
              {entry.speciesIcons.map((iconName, i) => (
                <View key={i} style={styles.speciesTag}>
                  <Ionicons name={iconName as any} size={14} color={palette.textSecondary} />
                </View>
              ))}
            </View>

            {/* Weather */}
            {entry.temperature != null && (
              <View style={styles.weatherBadge}>
                <Ionicons name={entry.weatherIonicon as any} size={14} color={palette.textSecondary} />
                <Text style={styles.weatherTemp}>{entry.temperature}{'\u00B0'}F</Text>
              </View>
            )}
          </View>
        </View>
      </View>
    </View>
  );
}

// ── Empty State ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <View style={styles.emptyState}>
      <Ionicons name="fish-outline" size={48} color={palette.textDim} />
      <Text style={styles.emptyTitle}>No catches yet</Text>
      <Text style={styles.emptySubtitle}>
        Head out and log your first catch!
      </Text>
    </View>
  );
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function ActivityScreen() {
  const [activeFilter, setActiveFilter] = useState<FilterChip>('All');
  const [catches, setCatches] = useState<EnhancedCatch[]>([]);
  const [tracks, setTracks] = useState<FishingTrack[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAllCatches(), trackRecorder.getSavedTracks()])
      .then(([c, t]) => {
        if (!cancelled) {
          setCatches(c);
          setTracks(t);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const entries = buildEntries(catches);
  const stats = buildStats(catches, tracks);
  const filteredEntries = entries.filter((e) => {
    const ts = catches.find((c) => c.id === e.id)?.timestamp ?? 0;
    return isInRange(ts, activeFilter);
  });

  if (loading) {
    return (
      <View style={[styles.screen, { alignItems: 'center', justifyContent: 'center' }]}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>Activity</Text>
          <Text style={styles.subtitle}>Your fishing logbook</Text>
        </View>

        {/* Activity heatmap calendar */}
        <ActivityHeatmap />

        {/* Stats row */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.statsRow}
        >
          {stats.map((stat) => (
            <StatCardView key={stat.label} stat={stat} />
          ))}
        </ScrollView>

        {/* Filter chips */}
        <View style={styles.filterRow}>
          {FILTER_CHIPS.map((chip) => (
            <Pressable
              key={chip}
              style={[
                styles.filterChip,
                activeFilter === chip && styles.filterChipActive,
              ]}
              onPress={() => setActiveFilter(chip)}
            >
              <Text
                style={[
                  styles.filterChipText,
                  activeFilter === chip && styles.filterChipTextActive,
                ]}
              >
                {chip}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Activity list */}
        {filteredEntries.length > 0 ? (
          <View style={styles.activityList}>
            {filteredEntries.map((entry) => (
              <ActivityCard key={entry.id} entry={entry} />
            ))}
          </View>
        ) : (
          <EmptyState />
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const STAT_CARD_WIDTH = 120;

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 20,
  },

  // Header
  header: {
    gap: 4,
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },

  // Stats row
  statsRow: {
    gap: 12,
    paddingRight: 20,
  },
  statCard: {
    width: STAT_CARD_WIDTH,
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
    alignItems: 'center',
    gap: 4,
  },
  statValue: {
    color: palette.accent,
    fontSize: 22,
    fontWeight: '700',
  },
  statLabel: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '600',
  },

  // Filter chips
  filterRow: {
    flexDirection: 'row',
    gap: 8,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: palette.surface,
  },
  filterChipActive: {
    backgroundColor: palette.accent,
  },
  filterChipText: {
    color: palette.textSecondary,
    fontSize: 13,
    fontWeight: '600',
  },
  filterChipTextActive: {
    color: '#FFFFFF',
  },

  // Activity list
  activityList: {
    gap: 10,
  },
  activityCard: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
  },
  activityRow: {
    flexDirection: 'row',
    gap: 12,
  },
  photoThumb: {
    width: 52,
    height: 52,
    borderRadius: 10,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  activityContent: {
    flex: 1,
    gap: 4,
  },
  activityHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  activityLocation: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
    flex: 1,
    marginRight: 8,
  },
  activityDateTime: {
    color: palette.textMuted,
    fontSize: 12,
  },
  activityMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 4,
  },
  speciesTags: {
    flexDirection: 'row',
    gap: 4,
  },
  speciesTag: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  weatherBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  weatherTemp: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '600',
  },

  // Empty state
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 12,
  },
  emptyTitle: {
    color: palette.text,
    fontSize: 20,
    fontWeight: '600',
  },
  emptySubtitle: {
    color: palette.textMuted,
    fontSize: 14,
    textAlign: 'center',
  },
});
