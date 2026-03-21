import React, { useState } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  Modal,
  Dimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import type { TabProps } from '../types/navigation';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
const CARD_GAP = 12;
const CARD_WIDTH = (SCREEN_WIDTH - 40 - CARD_GAP) / 2;

type Rarity = 'Common' | 'Uncommon' | 'Rare' | 'Legendary';

interface FishSpecies {
  id: string;
  name: string;
  emoji: string;
  rarity: Rarity;
  caught: boolean;
  count: number;
  personalBest: number | null; // lbs
  firstCaught: string | null;
}

const RARITY_COLORS: Record<Rarity, string> = {
  Common: '#3D8B37',
  Uncommon: '#0A6EBD',
  Rare: '#8B5CF6',
  Legendary: '#D4A017',
};

const FISH_SPECIES: FishSpecies[] = [
  // ── Common tier (most frequently caught freshwater species) ────
  { id: '1', name: 'Bluegill', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 55, personalBest: 1.2, firstCaught: '2024-03-10' },
  { id: '2', name: 'Largemouth Bass', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 47, personalBest: 8.2, firstCaught: '2024-03-15' },
  { id: '3', name: 'Channel Catfish', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 12, personalBest: 9.4, firstCaught: '2024-06-01' },
  { id: '4', name: 'Black Crappie', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 31, personalBest: 2.1, firstCaught: '2024-03-20' },
  { id: '5', name: 'White Crappie', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 18, personalBest: 1.8, firstCaught: '2024-04-05' },
  { id: '6', name: 'Green Sunfish', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 26, personalBest: 0.8, firstCaught: '2024-03-12' },
  { id: '7', name: 'Redear Sunfish', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 14, personalBest: 1.1, firstCaught: '2024-05-22' },
  { id: '8', name: 'Yellow Perch', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 22, personalBest: 1.4, firstCaught: '2024-04-18' },
  { id: '9', name: 'Rock Bass', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 9, personalBest: 1.0, firstCaught: '2024-06-14' },
  { id: '10', name: 'Pumpkinseed', emoji: '\uD83D\uDC20', rarity: 'Common', caught: true, count: 19, personalBest: 0.7, firstCaught: '2024-03-28' },
  { id: '11', name: 'White Bass', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 11, personalBest: 3.2, firstCaught: '2024-05-01' },
  { id: '12', name: 'Common Carp', emoji: '\uD83D\uDC1F', rarity: 'Common', caught: true, count: 7, personalBest: 18.5, firstCaught: '2024-07-10' },

  // ── Uncommon tier ──────────────────────────────────────────────
  { id: '13', name: 'Smallmouth Bass', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '14', name: 'Spotted Bass', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '15', name: 'Walleye', emoji: '\uD83D\uDC20', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '16', name: 'Northern Pike', emoji: '\uD83D\uDC20', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '17', name: 'Rainbow Trout', emoji: '\uD83E\uDD88', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '18', name: 'Brown Trout', emoji: '\uD83E\uDD88', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '19', name: 'Brook Trout', emoji: '\uD83E\uDD88', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '20', name: 'Striped Bass', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '21', name: 'White Perch', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '22', name: 'Sauger', emoji: '\uD83D\uDC20', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '23', name: 'Freshwater Drum', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '24', name: 'Yellow Bullhead', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '25', name: 'Black Bullhead', emoji: '\uD83D\uDC1F', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '26', name: 'Warmouth', emoji: '\uD83D\uDC20', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '27', name: 'Longear Sunfish', emoji: '\uD83D\uDC20', rarity: 'Uncommon', caught: false, count: 0, personalBest: null, firstCaught: null },

  // ── Rare tier ──────────────────────────────────────────────────
  { id: '28', name: 'Muskellunge', emoji: '\uD83D\uDC20', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '29', name: 'Flathead Catfish', emoji: '\uD83D\uDC1F', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '30', name: 'Lake Trout', emoji: '\uD83E\uDD88', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '31', name: 'Steelhead', emoji: '\uD83E\uDD88', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '32', name: 'Saugeye', emoji: '\uD83D\uDC20', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '33', name: 'Blue Catfish', emoji: '\uD83D\uDC1F', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '34', name: 'Hybrid Striped Bass', emoji: '\uD83D\uDC1F', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '35', name: 'Chinook Salmon', emoji: '\uD83E\uDD88', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '36', name: 'Coho Salmon', emoji: '\uD83E\uDD88', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '37', name: 'Tiger Muskie', emoji: '\uD83D\uDC20', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '38', name: 'Bowfin', emoji: '\uD83D\uDC1F', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '39', name: 'Longnose Gar', emoji: '\uD83D\uDC1F', rarity: 'Rare', caught: false, count: 0, personalBest: null, firstCaught: null },

  // ── Legendary tier ─────────────────────────────────────────────
  { id: '40', name: 'Alligator Gar', emoji: '\uD83D\uDC1F', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '41', name: 'Paddlefish', emoji: '\uD83D\uDC1F', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '42', name: 'Lake Sturgeon', emoji: '\uD83D\uDC1F', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '43', name: 'Burbot', emoji: '\uD83D\uDC1F', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '44', name: 'Golden Trout', emoji: '\uD83E\uDD88', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
  { id: '45', name: 'Arctic Grayling', emoji: '\uD83E\uDD88', rarity: 'Legendary', caught: false, count: 0, personalBest: null, firstCaught: null },
];

// ── Personal Analytics Section (real data from AsyncStorage) ──────
function AnalyticsSection() {
  const [totalCatches, setTotalCatches] = React.useState<number | null>(null);
  const [totalTrips, setTotalTrips] = React.useState<number | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { getAllCatches } = await import('../services/catchEnhancements');
        const { trackRecorder } = await import('../services/trackRecorder');
        const [catches, tracks] = await Promise.all([
          getAllCatches(),
          trackRecorder.getSavedTracks(),
        ]);
        if (!cancelled) {
          setTotalCatches(catches.length);
          setTotalTrips(tracks.length);
        }
      } catch {
        if (!cancelled) {
          setTotalCatches(0);
          setTotalTrips(0);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (totalCatches === null) return null; // Still loading

  if (totalCatches === 0 && totalTrips === 0) {
    return (
      <View style={styles.analyticsWrapper}>
        <Text style={styles.analyticsSectionTitle}>Your Stats</Text>
        <View style={{ alignItems: 'center', paddingVertical: 16, gap: 6 }}>
          <Text style={{ color: palette.textMuted, fontSize: 14 }}>No data yet</Text>
          <Text style={{ color: palette.textDim, fontSize: 12 }}>Log catches to see your stats here</Text>
        </View>
      </View>
    );
  }

  const avgPerTrip = (totalTrips ?? 0) > 0 ? ((totalCatches ?? 0) / (totalTrips ?? 1)).toFixed(1) : '0';

  return (
    <View style={styles.analyticsWrapper}>
      <Text style={styles.analyticsSectionTitle}>Your Stats</Text>
      <View style={styles.statGrid}>
        <View style={styles.statCard}>
          <Text style={styles.statValue}>{totalCatches}</Text>
          <Text style={styles.statLabel}>Total Catches</Text>
        </View>
        <View style={styles.statCard}>
          <Text style={styles.statValue}>{totalTrips}</Text>
          <Text style={styles.statLabel}>Total Trips</Text>
        </View>
        <View style={styles.statCard}>
          <Text style={styles.statValue}>{avgPerTrip}</Text>
          <Text style={styles.statLabel}>Avg per Trip</Text>
        </View>
      </View>
    </View>
  );
}

export function CollectionScreen({ navigation }: TabProps<'ProfileTab'>) {
  const [selectedFish, setSelectedFish] = useState<FishSpecies | null>(null);
  const caughtCount = FISH_SPECIES.filter((f) => f.caught).length;
  const totalCount = FISH_SPECIES.length;
  const progress = caughtCount / totalCount;

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerTop}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>Profile</Text>
              <Text style={styles.subtitle}>
                {caughtCount} of {totalCount} species
              </Text>
            </View>
            <Pressable
              onPress={() => (navigation as any).navigate('ProfileTab')}
              style={styles.settingsButton}
            >
              <Ionicons name="settings-outline" size={20} color={palette.textSecondary} />
            </Pressable>
          </View>

          {/* Progress bar */}
          <View style={styles.progressContainer}>
            <View style={styles.progressTrack}>
              <View
                style={[styles.progressFill, { width: `${progress * 100}%` }]}
              />
            </View>
            <Text style={styles.progressText}>
              {Math.round(progress * 100)}% complete
            </Text>
          </View>
        </View>

        {/* Personal Analytics */}
        <AnalyticsSection />

        {/* Fish grid */}
        <View style={styles.grid}>
          {FISH_SPECIES.map((fish) => (
            <Pressable
              key={fish.id}
              style={({ pressed }) => [
                styles.fishCard,
                !fish.caught && styles.fishCardUncaught,
                pressed && fish.caught && styles.fishCardPressed,
              ]}
              onPress={() => fish.caught && setSelectedFish(fish)}
              disabled={!fish.caught}
            >
              {/* Rarity badge */}
              <View
                style={[
                  styles.rarityBadge,
                  { backgroundColor: RARITY_COLORS[fish.rarity] + '18' },
                ]}
              >
                <Text
                  style={[
                    styles.rarityText,
                    { color: RARITY_COLORS[fish.rarity] },
                  ]}
                >
                  {fish.rarity}
                </Text>
              </View>

              {/* Fish icon */}
              <View style={styles.fishIconContainer}>
                {fish.caught ? (
                  <Ionicons name="fish" size={32} color={palette.accent} />
                ) : (
                  <View style={styles.silhouette}>
                    <Text style={styles.silhouetteQuestion}>?</Text>
                  </View>
                )}
              </View>

              {/* Name */}
              <Text
                style={[
                  styles.fishName,
                  !fish.caught && styles.fishNameUncaught,
                ]}
                numberOfLines={1}
              >
                {fish.name}
              </Text>

              {/* Stats */}
              {fish.caught ? (
                <View style={styles.fishStats}>
                  <Text style={styles.fishCount}>{fish.count} caught</Text>
                  {fish.personalBest && (
                    <Text style={styles.fishPB}>PB: {fish.personalBest} lbs</Text>
                  )}
                </View>
              ) : (
                <Text style={styles.fishUndiscovered}>Undiscovered</Text>
              )}
            </Pressable>
          ))}
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>

      {/* Fish detail modal */}
      <Modal
        visible={selectedFish !== null}
        animationType="slide"
        transparent
        onRequestClose={() => setSelectedFish(null)}
      >
        <Pressable style={styles.modalOverlay} onPress={() => setSelectedFish(null)}>
          <View style={styles.modalContent}>
            {selectedFish && (
              <>
                <View style={styles.modalHandle} />
                <Ionicons name="fish" size={56} color={palette.accent} />
                <Text style={styles.modalName}>{selectedFish.name}</Text>
                <View
                  style={[
                    styles.modalRarity,
                    { backgroundColor: RARITY_COLORS[selectedFish.rarity] + '18' },
                  ]}
                >
                  <Text
                    style={[
                      styles.modalRarityText,
                      { color: RARITY_COLORS[selectedFish.rarity] },
                    ]}
                  >
                    {selectedFish.rarity}
                  </Text>
                </View>

                <View style={styles.modalStatsGrid}>
                  <View style={styles.modalStat}>
                    <Text style={styles.modalStatValue}>{selectedFish.count}</Text>
                    <Text style={styles.modalStatLabel}>Total Caught</Text>
                  </View>
                  <View style={styles.modalStat}>
                    <Text style={styles.modalStatValue}>
                      {selectedFish.personalBest ? `${selectedFish.personalBest} lbs` : '--'}
                    </Text>
                    <Text style={styles.modalStatLabel}>Personal Best</Text>
                  </View>
                  <View style={styles.modalStat}>
                    <Text style={styles.modalStatValue}>
                      {selectedFish.firstCaught
                        ? new Date(selectedFish.firstCaught).toLocaleDateString('en-US', {
                            month: 'short',
                            year: 'numeric',
                          })
                        : '--'}
                    </Text>
                    <Text style={styles.modalStatLabel}>First Caught</Text>
                  </View>
                </View>

                <Pressable
                  style={styles.modalClose}
                  onPress={() => setSelectedFish(null)}
                >
                  <Text style={styles.modalCloseText}>Close</Text>
                </Pressable>
              </>
            )}
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 20,
  },
  header: {
    gap: 16,
  },
  headerTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
    marginTop: 4,
  },
  settingsButton: {
    width: 40,
    height: 40,
    borderRadius: 8,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  settingsIcon: {
    fontSize: 20,
  },
  progressContainer: {
    gap: 6,
  },
  progressTrack: {
    height: 8,
    backgroundColor: palette.border,
    borderRadius: 4,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: palette.accent,
    borderRadius: 4,
  },
  progressText: {
    color: palette.textMuted,
    fontSize: 12,
    fontWeight: '600',
  },

  // ── Analytics ──────────────────────────────────────────────────
  analyticsWrapper: {
    gap: 12,
  },
  analyticsSectionTitle: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },
  statGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: CARD_GAP,
  },
  statCard: {
    width: CARD_WIDTH,
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 16,
    gap: 4,
  },
  statValue: {
    color: palette.accent,
    fontSize: 26,
    fontWeight: '700',
    lineHeight: 30,
  },
  statValueSmall: {
    fontSize: 16,
    lineHeight: 20,
  },
  statLabel: {
    color: palette.text,
    fontSize: 12,
    fontWeight: '600',
    marginTop: 2,
  },
  statTrend: {
    color: palette.textMuted,
    fontSize: 11,
  },
  insightsCard: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 16,
    gap: 12,
  },
  insightsList: {
    gap: 10,
  },
  insightRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  insightDot: {
    width: 7,
    height: 7,
    borderRadius: 3.5,
    backgroundColor: palette.accent,
    marginTop: 5,
    flexShrink: 0,
  },
  insightText: {
    color: palette.textSecondary,
    fontSize: 13,
    lineHeight: 19,
    flex: 1,
  },

  // ── Fish grid ──────────────────────────────────────────────────
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: CARD_GAP,
  },
  fishCard: {
    width: CARD_WIDTH,
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 12,
    alignItems: 'center',
    gap: 8,
  },
  fishCardUncaught: {
    opacity: 0.55,
  },
  fishCardPressed: {
    opacity: 0.85,
  },
  rarityBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  rarityText: {
    fontSize: 9,
    fontWeight: '700',
  },
  fishIconContainer: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
  },
  fishEmoji: {
    fontSize: 36,
  },
  silhouette: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  silhouetteQuestion: {
    color: palette.textDim,
    fontSize: 24,
    fontWeight: '700',
  },
  fishName: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
  },
  fishNameUncaught: {
    color: palette.textDim,
  },
  fishStats: {
    alignItems: 'center',
    gap: 2,
  },
  fishCount: {
    color: palette.textSecondary,
    fontSize: 11,
    fontWeight: '600',
  },
  fishPB: {
    color: palette.accent,
    fontSize: 10,
    fontWeight: '700',
  },
  fishUndiscovered: {
    color: palette.textDim,
    fontSize: 11,
    fontStyle: 'italic',
  },
  // Modal
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.3)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
    padding: 24,
    alignItems: 'center',
    gap: 12,
    paddingBottom: 40,
  },
  modalHandle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.border,
    marginBottom: 8,
  },
  modalEmoji: {
    fontSize: 56,
  },
  modalName: {
    color: palette.text,
    fontSize: 24,
    fontWeight: '600',
  },
  modalRarity: {
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 8,
  },
  modalRarityText: {
    fontSize: 12,
    fontWeight: '700',
  },
  modalStatsGrid: {
    flexDirection: 'row',
    gap: 20,
    marginTop: 16,
    marginBottom: 8,
  },
  modalStat: {
    alignItems: 'center',
    gap: 4,
    flex: 1,
  },
  modalStatValue: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '700',
  },
  modalStatLabel: {
    color: palette.textMuted,
    fontSize: 11,
    fontWeight: '600',
  },
  modalClose: {
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 32,
    paddingVertical: 12,
    borderRadius: 8,
    marginTop: 8,
  },
  modalCloseText: {
    color: palette.textSecondary,
    fontSize: 15,
    fontWeight: '600',
  },
});
