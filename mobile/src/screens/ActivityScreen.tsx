import React, { useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { ActivityHeatmap } from '../components/ActivityHeatmap';
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
  temperature: number;
  weatherIonicon: string;
  photoPlaceholder: boolean;
}

type FilterChip = 'All' | 'This Week' | 'This Month' | 'This Year';

// ── Mock Data ────────────────────────────────────────────────────────────────

const STATS: StatCard[] = [
  { label: 'Total Trips', value: '34', ionicon: 'boat-outline' },
  { label: 'Total Fish', value: '205', ionicon: 'fish-outline' },
  { label: 'Best Day', value: '14', ionicon: 'trophy-outline' },
];

const ACTIVITIES: ActivityEntry[] = [
  {
    id: '1',
    date: 'Mar 16, 2026',
    time: '6:30 AM',
    location: 'Lake Fork, TX',
    fishCount: 7,
    speciesIcons: ['fish-outline', 'fish'],
    temperature: 62,
    weatherIonicon: 'partly-sunny-outline',
    photoPlaceholder: true,
  },
  {
    id: '2',
    date: 'Mar 14, 2026',
    time: '7:00 AM',
    location: 'Grand Lake, OK',
    fishCount: 4,
    speciesIcons: ['fish-outline'],
    temperature: 58,
    weatherIonicon: 'sunny-outline',
    photoPlaceholder: true,
  },
  {
    id: '3',
    date: 'Mar 10, 2026',
    time: '5:45 AM',
    location: 'Sam Rayburn, TX',
    fishCount: 11,
    speciesIcons: ['fish-outline', 'fish', 'fish-outline'],
    temperature: 55,
    weatherIonicon: 'sunny-outline',
    photoPlaceholder: true,
  },
  {
    id: '4',
    date: 'Mar 7, 2026',
    time: '6:15 AM',
    location: 'Table Rock Lake, MO',
    fishCount: 3,
    speciesIcons: ['fish-outline'],
    temperature: 51,
    weatherIonicon: 'rainy-outline',
    photoPlaceholder: false,
  },
  {
    id: '5',
    date: 'Mar 3, 2026',
    time: '7:30 AM',
    location: 'Lake Texoma, TX/OK',
    fishCount: 14,
    speciesIcons: ['fish-outline', 'fish'],
    temperature: 64,
    weatherIonicon: 'sunny-outline',
    photoPlaceholder: true,
  },
  {
    id: '6',
    date: 'Feb 28, 2026',
    time: '6:00 AM',
    location: 'Beaver Lake, AR',
    fishCount: 5,
    speciesIcons: ['fish-outline'],
    temperature: 48,
    weatherIonicon: 'partly-sunny-outline',
    photoPlaceholder: false,
  },
];

const FILTER_CHIPS: FilterChip[] = ['All', 'This Week', 'This Month', 'This Year'];

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
            <Text style={styles.activityFishCount}>
              {entry.fishCount} fish
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
            <View style={styles.weatherBadge}>
              <Ionicons name={entry.weatherIonicon as any} size={14} color={palette.textSecondary} />
              <Text style={styles.weatherTemp}>{entry.temperature}{'\u00B0'}F</Text>
            </View>
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

  const hasActivities = ACTIVITIES.length > 0;

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
          {STATS.map((stat) => (
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
        {hasActivities ? (
          <View style={styles.activityList}>
            {ACTIVITIES.map((entry) => (
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
  activityFishCount: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
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
