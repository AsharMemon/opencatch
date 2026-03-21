/**
 * OpenCatch — Full Activity Log Screen
 *
 * Shows all catches and tracks merged by date, with filter chips.
 * Tapping a catch shows a detail modal; tapping a trip navigates to TrackDetail.
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  Pressable,
  ActivityIndicator,
  Modal,
  ScrollView,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts } from '../theme/typography';
import { getAllCatches, type EnhancedCatch } from '../services/catchEnhancements';
import { trackRecorder, type FishingTrack } from '../services/trackRecorder';
import { shareCatchToSocial } from '../services/socialSharing';

// ── Types ────────────────────────────────────────────────────────

type FilterType = 'All' | 'Catches' | 'Trips';

interface ActivityEntry {
  id: string;
  type: 'catch' | 'trip';
  title: string;
  subtitle: string;
  stat: string;
  timestamp: number;
  icon: keyof typeof Ionicons.glyphMap;
  /** Raw catch data for modal display */
  catchData?: EnhancedCatch;
  /** Raw track data for navigation */
  trackData?: FishingTrack;
}

// ── Helpers ──────────────────────────────────────────────────────

function formatDate(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleDateString('en-US', {
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

function buildEntries(
  catches: EnhancedCatch[],
  tracks: FishingTrack[],
): ActivityEntry[] {
  const entries: ActivityEntry[] = [];

  for (const c of catches) {
    entries.push({
      id: `catch-${c.id}`,
      type: 'catch',
      title: c.species || 'Unknown Species',
      subtitle: c.locationName || formatDate(c.timestamp),
      stat: c.weight ? `${c.weight} lb` : c.length ? `${c.length} in` : '',
      timestamp: c.timestamp,
      icon: 'fish-outline',
      catchData: c,
    });
  }

  for (const t of tracks) {
    entries.push({
      id: `trip-${t.id}`,
      type: 'trip',
      title: t.name || 'Fishing Trip',
      subtitle: formatDate(t.startTime),
      stat: t.distanceMiles > 0 ? `${t.distanceMiles.toFixed(1)} mi` : `${t.durationMinutes} min`,
      timestamp: t.startTime,
      icon: 'navigate-outline',
      trackData: t,
    });
  }

  entries.sort((a, b) => b.timestamp - a.timestamp);
  return entries;
}

// ── Catch Detail Modal ────────────────────────────────────────────

function CatchDetailModal({
  catchData,
  visible,
  onClose,
}: {
  catchData: EnhancedCatch | null;
  visible: boolean;
  onClose: () => void;
}) {
  if (!catchData) return null;

  const rows: { label: string; value: string }[] = [];
  if (catchData.species) rows.push({ label: 'Species', value: catchData.species });
  if (catchData.weight) rows.push({ label: 'Weight', value: `${catchData.weight} lb` });
  if (catchData.length) rows.push({ label: 'Length', value: `${catchData.length} in` });
  if (catchData.locationName) rows.push({ label: 'Location', value: catchData.locationName });
  rows.push({ label: 'Date', value: formatDate(catchData.timestamp) });
  rows.push({ label: 'Time', value: formatTime(catchData.timestamp) });
  if (catchData.bait) rows.push({ label: 'Bait/Lure', value: catchData.bait });
  if (catchData.technique) rows.push({ label: 'Technique', value: catchData.technique });
  if (catchData.waterTemp) rows.push({ label: 'Water Temp', value: `${catchData.waterTemp}\u00B0F` });
  if (catchData.airTemp) rows.push({ label: 'Air Temp', value: `${catchData.airTemp}\u00B0F` });
  if (catchData.windSpeed != null) rows.push({ label: 'Wind', value: `${catchData.windSpeed} mph` });
  if (catchData.waterClarity) rows.push({ label: 'Water Clarity', value: catchData.waterClarity });
  if (catchData.notes) rows.push({ label: 'Notes', value: catchData.notes });

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={modalStyles.backdrop} onPress={onClose}>
        <Pressable style={modalStyles.sheet} onPress={() => {}}>
          <View style={modalStyles.handle} />
          <Text style={modalStyles.title}>{catchData.species || 'Catch Details'}</Text>
          <ScrollView style={modalStyles.scroll} showsVerticalScrollIndicator={false}>
            {rows.map((row) => (
              <View key={row.label} style={modalStyles.row}>
                <Text style={modalStyles.label}>{row.label}</Text>
                <Text style={modalStyles.value}>{row.value}</Text>
              </View>
            ))}
          </ScrollView>
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <Pressable
              style={[modalStyles.closeBtn, { flex: 1, backgroundColor: palette.accentDim }]}
              onPress={() => shareCatchToSocial(catchData)}
            >
              <Text style={[modalStyles.closeBtnText, { color: palette.accent }]}>Share</Text>
            </Pressable>
            <Pressable style={[modalStyles.closeBtn, { flex: 1 }]} onPress={onClose}>
              <Text style={modalStyles.closeBtnText}>Close</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ── Component ────────────────────────────────────────────────────

export function ActivityLogScreen() {
  const navigation = useNavigation<any>();
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [filter, setFilter] = useState<FilterType>('All');
  const [loading, setLoading] = useState(true);
  const [selectedCatch, setSelectedCatch] = useState<EnhancedCatch | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [catches, tracks] = await Promise.all([
          getAllCatches(),
          trackRecorder.getSavedTracks(),
        ]);
        setEntries(buildEntries(catches, tracks));
      } catch {
        // Silently fail
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const filtered = entries.filter((e) => {
    if (filter === 'All') return true;
    if (filter === 'Catches') return e.type === 'catch';
    return e.type === 'trip';
  });

  const handleEntryPress = useCallback((item: ActivityEntry) => {
    if (item.type === 'catch' && item.catchData) {
      setSelectedCatch(item.catchData);
    } else if (item.type === 'trip' && item.trackData) {
      navigation.navigate('TrackDetail', { trackId: item.trackData.id });
    }
  }, [navigation]);

  const renderItem = useCallback(({ item }: { item: ActivityEntry }) => (
    <Pressable
      style={styles.row}
      onPress={() => handleEntryPress(item)}
    >
      <View style={[styles.iconCircle, { backgroundColor: item.type === 'catch' ? palette.accent + '15' : '#E6510015' }]}>
        <Ionicons
          name={item.icon as any}
          size={18}
          color={item.type === 'catch' ? palette.accent : '#E65100'}
        />
      </View>
      <View style={styles.rowInfo}>
        <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
        <Text style={styles.rowSubtitle}>{item.subtitle}</Text>
      </View>
      {item.stat !== '' && (
        <Text style={styles.rowStat}>{item.stat}</Text>
      )}
      <Ionicons name="chevron-forward" size={16} color={palette.textDim} />
    </Pressable>
  ), [handleEntryPress]);

  const FILTERS: FilterType[] = ['All', 'Catches', 'Trips'];

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Filter chips */}
      <View style={styles.chipRow}>
        {FILTERS.map((f) => (
          <Pressable
            key={f}
            style={[styles.chip, filter === f && styles.chipActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.chipText, filter === f && styles.chipTextActive]}>
              {f}
            </Text>
          </Pressable>
        ))}
      </View>

      {filtered.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="document-text-outline" size={48} color={palette.border} />
          <Text style={styles.emptyText}>No activity yet</Text>
          <Text style={styles.emptySubtext}>
            Log catches and record trips to see them here
          </Text>
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
        />
      )}

      <CatchDetailModal
        catchData={selectedCatch}
        visible={selectedCatch !== null}
        onClose={() => setSelectedCatch(null)}
      />
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.background,
  },
  chipRow: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  chip: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
  },
  chipActive: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  chipText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  chipTextActive: {
    color: '#fff',
  },
  list: {
    paddingHorizontal: 16,
    paddingBottom: 40,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  iconCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowInfo: {
    flex: 1,
    gap: 2,
  },
  rowTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  rowSubtitle: {
    fontSize: 13,
    color: palette.textMuted,
  },
  rowStat: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.accent,
  },
  empty: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: 40,
  },
  emptyText: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
    marginTop: 8,
  },
  emptySubtext: {
    fontSize: 13,
    color: palette.textMuted,
    textAlign: 'center',
  },
});

const modalStyles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 32,
    maxHeight: '70%',
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.border,
    alignSelf: 'center',
    marginBottom: 12,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 16,
  },
  scroll: {
    marginBottom: 16,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  label: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  value: {
    fontSize: 14,
    color: palette.text,
    flex: 1,
    textAlign: 'right',
    marginLeft: 16,
  },
  closeBtn: {
    backgroundColor: palette.accent,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  closeBtnText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});
