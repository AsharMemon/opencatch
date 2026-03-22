/**
 * OpenCatch — Fish Species Guide & Identification Screen
 *
 * Browse fish species, identify catches, and compare similar species.
 * Includes search, filtering, detailed species profiles, and a
 * feature-based identification wizard.
 */

import React, { useState, useMemo, useCallback } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  TextInput,
  Pressable,
  FlatList,
  Dimensions,
  Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getAllSpecies,
  searchSpecies,
  identifyFromFeatures,
  getComparisonTips,
  CONFUSION_PAIRS,
  type FishSpecies,
  type IdentificationResult,
} from '../services/fishSpeciesAI';
import {
  getSaltWaterSpecies,
  type SaltwaterSpecies,
} from '../services/coastalFishing';

const { width: SCREEN_WIDTH } = Dimensions.get('window');

// ── Filter Types ────────────────────────────────────────────────────────────

type WaterFilter = 'all' | 'freshwater' | 'saltwater';
type ShapeFilter = 'all' | 'elongated' | 'deep' | 'torpedo' | 'flat';
type SortBy = 'name' | 'size' | 'family';

// ── Component ───────────────────────────────────────────────────────────────

export function SpeciesGuideScreen() {
  const [searchQuery, setSearchQuery] = useState('');
  const [waterFilter, setWaterFilter] = useState<WaterFilter>('all');
  const [shapeFilter, setShapeFilter] = useState<ShapeFilter>('all');
  const [sortBy, setSortBy] = useState<SortBy>('name');
  const [selectedSpecies, setSelectedSpecies] = useState<FishSpecies | null>(null);
  const [showIdentify, setShowIdentify] = useState(false);
  const [showCompare, setShowCompare] = useState(false);

  // Identification wizard state
  const [idColor, setIdColor] = useState('');
  const [idShape, setIdShape] = useState('');
  const [idSize, setIdSize] = useState('');
  const [idResults, setIdResults] = useState<IdentificationResult[]>([]);

  const allSpecies = useMemo(() => getAllSpecies(), []);

  // Coastal/saltwater species from coastalFishing service (shown when saltwater filter active)
  const [coastalSpecies, setCoastalSpecies] = useState<SaltwaterSpecies[]>([]);
  React.useEffect(() => {
    if (waterFilter === 'saltwater') {
      // Use a generic coastal US coordinate when no location
      const species = getSaltWaterSpecies(28.5, -80.5); // Florida coast default
      setCoastalSpecies(species);
    } else {
      setCoastalSpecies([]);
    }
  }, [waterFilter]);

  const filteredSpecies = useMemo(() => {
    let results = searchQuery ? searchSpecies(searchQuery) : [...allSpecies];

    // Water type filter
    if (waterFilter !== 'all') {
      results = results.filter((s) => {
        const isCoastal = s.habitat.some(
          (h) => h.toLowerCase().includes('coastal') || h.toLowerCase().includes('estuar'),
        );
        return waterFilter === 'saltwater' ? isCoastal : !isCoastal;
      });
    }

    // Shape filter
    if (shapeFilter !== 'all') {
      results = results.filter((s) => s.bodyShape === shapeFilter);
    }

    // Sort
    switch (sortBy) {
      case 'name':
        results.sort((a, b) => a.commonName.localeCompare(b.commonName));
        break;
      case 'size':
        results.sort((a, b) => b.typicalSizeIn.max - a.typicalSizeIn.max);
        break;
      case 'family':
        results.sort((a, b) => a.family.localeCompare(b.family));
        break;
    }

    return results;
  }, [searchQuery, waterFilter, shapeFilter, sortBy, allSpecies]);

  const runIdentification = useCallback(() => {
    const results = identifyFromFeatures({
      hints: {
        bodyColor: idColor || undefined,
        bodyShape: idShape || undefined,
        estimatedLengthIn: idSize ? parseInt(idSize, 10) : undefined,
        waterType: 'freshwater',
      },
    });
    setIdResults(results.slice(0, 5));
  }, [idColor, idShape, idSize]);

  const renderSpeciesCard = useCallback(
    ({ item }: { item: FishSpecies }) => (
      <Pressable
        style={styles.speciesCard}
        onPress={() => setSelectedSpecies(item)}
      >
        <View style={styles.cardHeader}>
          <Text style={styles.speciesIcon}>{item.icon}</Text>
          <View style={styles.cardHeaderText}>
            <Text style={styles.speciesName}>{item.commonName}</Text>
            <Text style={styles.scientificName}>{item.scientificName}</Text>
          </View>
          <View style={[styles.statusBadge, statusBadgeStyle(item.status)]}>
            <Text style={styles.statusText}>{item.status}</Text>
          </View>
        </View>
        <Text style={styles.cardDescription} numberOfLines={2}>
          {item.description}
        </Text>
        <View style={styles.cardFooter}>
          <View style={styles.cardStat}>
            <Ionicons name="resize-outline" size={14} color={palette.textSecondary} />
            <Text style={styles.cardStatText}>
              {item.typicalSizeIn.min}-{item.typicalSizeIn.max}"
            </Text>
          </View>
          <View style={styles.cardStat}>
            <Ionicons name="water-outline" size={14} color={palette.textSecondary} />
            <Text style={styles.cardStatText}>{item.habitat[0]}</Text>
          </View>
          {item.recordLb && (
            <View style={styles.cardStat}>
              <Ionicons name="trophy-outline" size={14} color="#D4A017" />
              <Text style={styles.cardStatText}>{item.recordLb} lb record</Text>
            </View>
          )}
        </View>
      </Pressable>
    ),
    [],
  );

  return (
    <View style={styles.container}>
      {/* Search Bar */}
      <View style={styles.searchContainer}>
        <View style={styles.searchInputWrapper}>
          <Ionicons name="search" size={18} color={palette.textSecondary} />
          <TextInput
            style={styles.searchInput}
            placeholder="Search species..."
            placeholderTextColor={palette.textSecondary}
            value={searchQuery}
            onChangeText={setSearchQuery}
          />
          {searchQuery.length > 0 && (
            <Pressable onPress={() => setSearchQuery('')}>
              <Ionicons name="close-circle" size={18} color={palette.textSecondary} />
            </Pressable>
          )}
        </View>
      </View>

      {/* Action Buttons */}
      <View style={styles.actionRow}>
        <Pressable
          style={[styles.actionButton, showIdentify && styles.actionButtonActive]}
          onPress={() => { setShowIdentify(!showIdentify); setShowCompare(false); }}
        >
          <Ionicons name="camera-outline" size={18} color={showIdentify ? '#fff' : palette.accent} />
          <Text style={[styles.actionButtonText, showIdentify && styles.actionButtonTextActive]}>
            Identify
          </Text>
        </Pressable>
        <Pressable
          style={[styles.actionButton, showCompare && styles.actionButtonActive]}
          onPress={() => { setShowCompare(!showCompare); setShowIdentify(false); }}
        >
          <Ionicons name="git-compare-outline" size={18} color={showCompare ? '#fff' : palette.accent} />
          <Text style={[styles.actionButtonText, showCompare && styles.actionButtonTextActive]}>
            Compare
          </Text>
        </Pressable>
      </View>

      {/* Identification Wizard */}
      {showIdentify && (
        <View style={styles.identifyPanel}>
          <Text style={styles.identifyTitle}>What does it look like?</Text>
          <View style={styles.identifyRow}>
            <View style={styles.identifyField}>
              <Text style={styles.identifyLabel}>Color</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                {['green', 'bronze', 'gold', 'silver', 'gray', 'brown', 'blue'].map((c) => (
                  <Pressable
                    key={c}
                    style={[styles.colorPill, idColor === c && styles.colorPillActive]}
                    onPress={() => setIdColor(idColor === c ? '' : c)}
                  >
                    <View style={[styles.colorDot, { backgroundColor: colorMap[c] || c }]} />
                    <Text style={[styles.colorPillText, idColor === c && styles.colorPillTextActive]}>{c}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          </View>
          <View style={styles.identifyRow}>
            <View style={styles.identifyField}>
              <Text style={styles.identifyLabel}>Body Shape</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                {['elongated', 'deep', 'torpedo', 'flat'].map((s) => (
                  <Pressable
                    key={s}
                    style={[styles.shapePill, idShape === s && styles.shapePillActive]}
                    onPress={() => setIdShape(idShape === s ? '' : s)}
                  >
                    <Text style={[styles.shapePillText, idShape === s && styles.shapePillTextActive]}>{s}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          </View>
          <View style={styles.identifyRow}>
            <View style={styles.identifyField}>
              <Text style={styles.identifyLabel}>Est. Length (inches)</Text>
              <TextInput
                style={styles.sizeInput}
                placeholder="e.g. 14"
                placeholderTextColor={palette.textSecondary}
                keyboardType="numeric"
                value={idSize}
                onChangeText={setIdSize}
              />
            </View>
            <Pressable style={styles.identifyButton} onPress={runIdentification}>
              <Ionicons name="search" size={18} color="#fff" />
              <Text style={styles.identifyButtonText}>Identify</Text>
            </Pressable>
          </View>

          {/* Results */}
          {idResults.length > 0 && (
            <View style={styles.idResults}>
              <Text style={styles.idResultsTitle}>Top Matches</Text>
              {idResults.map((r) => (
                <Pressable
                  key={r.species.id}
                  style={styles.idResultRow}
                  onPress={() => setSelectedSpecies(r.species)}
                >
                  <Text style={styles.idResultIcon}>{r.species.icon}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.idResultName}>{r.species.commonName}</Text>
                    <Text style={styles.idResultReason}>{r.matchReason}</Text>
                  </View>
                  <View style={styles.confidenceBar}>
                    <View
                      style={[
                        styles.confidenceFill,
                        {
                          width: `${r.confidence * 100}%`,
                          backgroundColor: r.confidence > 0.7 ? '#4CAF50' : r.confidence > 0.4 ? '#FF9800' : '#F44336',
                        },
                      ]}
                    />
                  </View>
                  <Text style={styles.confidenceText}>{Math.round(r.confidence * 100)}%</Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      )}

      {/* Comparison Panel */}
      {showCompare && (
        <View style={styles.comparePanel}>
          <Text style={styles.identifyTitle}>Commonly Confused Species</Text>
          {CONFUSION_PAIRS.map((pair, i) => {
            const s1 = getAllSpecies().find((s) => s.id === pair.species1);
            const s2 = getAllSpecies().find((s) => s.id === pair.species2);
            if (!s1 || !s2) return null;
            return (
              <View key={i} style={styles.comparePair}>
                <View style={styles.compareSpecies}>
                  <Text style={styles.compareIcon}>{s1.icon}</Text>
                  <Text style={styles.compareName}>{s1.commonName}</Text>
                </View>
                <Ionicons name="swap-horizontal" size={20} color={palette.textSecondary} />
                <View style={styles.compareSpecies}>
                  <Text style={styles.compareIcon}>{s2.icon}</Text>
                  <Text style={styles.compareName}>{s2.commonName}</Text>
                </View>
                <Text style={styles.compareTip}>{pair.keyDifference}</Text>
              </View>
            );
          })}
        </View>
      )}

      {/* Filter Pills */}
      <View style={styles.filterRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          {(['all', 'freshwater', 'saltwater'] as WaterFilter[]).map((f) => (
            <Pressable
              key={f}
              style={[styles.filterPill, waterFilter === f && styles.filterPillActive]}
              onPress={() => setWaterFilter(f)}
            >
              <Text style={[styles.filterPillText, waterFilter === f && styles.filterPillTextActive]}>
                {f === 'all' ? 'All Water' : f.charAt(0).toUpperCase() + f.slice(1)}
              </Text>
            </Pressable>
          ))}
          <View style={styles.filterDivider} />
          {(['name', 'size', 'family'] as SortBy[]).map((s) => (
            <Pressable
              key={s}
              style={[styles.filterPill, sortBy === s && styles.filterPillActive]}
              onPress={() => setSortBy(s)}
            >
              <Ionicons
                name={s === 'name' ? 'text-outline' : s === 'size' ? 'resize-outline' : 'folder-outline'}
                size={14}
                color={sortBy === s ? '#fff' : palette.textSecondary}
              />
              <Text style={[styles.filterPillText, sortBy === s && styles.filterPillTextActive]}>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {/* Coastal saltwater species from coastalFishing service */}
      {coastalSpecies.length > 0 && (
        <View style={{ paddingHorizontal: 16, gap: 8, marginBottom: 8 }}>
          <Text style={[styles.resultCount, { fontWeight: '700', fontSize: 13 }]}>
            In Season Saltwater ({coastalSpecies.length})
          </Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {coastalSpecies.slice(0, 8).map((sp) => (
              <View key={sp.name} style={{ backgroundColor: '#FFFFFF', borderRadius: 10, padding: 10, width: 140, shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 }}>
                <Text style={{ fontSize: 13, fontWeight: '700', color: palette.text }} numberOfLines={1}>{sp.name}</Text>
                <Text style={{ fontSize: 10, color: palette.textMuted, fontStyle: 'italic' }} numberOfLines={1}>{sp.scientificName}</Text>
                <Text style={{ fontSize: 10, color: palette.textSecondary, marginTop: 4 }} numberOfLines={2}>{sp.habitat}</Text>
                <Text style={{ fontSize: 10, color: palette.accent, marginTop: 2 }}>{sp.techniques.slice(0, 2).join(', ')}</Text>
              </View>
            ))}
          </ScrollView>
        </View>
      )}

      {/* Species count */}
      <Text style={styles.resultCount}>{filteredSpecies.length} species</Text>

      {/* Species List */}
      <FlatList
        data={filteredSpecies}
        keyExtractor={(item) => item.id}
        renderItem={renderSpeciesCard}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
      />

      {/* Species Detail Modal */}
      <Modal
        visible={!!selectedSpecies}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setSelectedSpecies(null)}
      >
        {selectedSpecies && (
          <SpeciesDetailView
            species={selectedSpecies}
            onClose={() => setSelectedSpecies(null)}
          />
        )}
      </Modal>
    </View>
  );
}

// ── Species Detail View ─────────────────────────────────────────────────────

function SpeciesDetailView({
  species,
  onClose,
}: {
  species: FishSpecies;
  onClose: () => void;
}) {
  return (
    <View style={styles.detailContainer}>
      <View style={styles.detailHeader}>
        <Pressable onPress={onClose} style={styles.closeButton}>
          <Ionicons name="close" size={24} color={palette.text} />
        </Pressable>
        <Text style={styles.detailTitle}>{species.commonName}</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView style={styles.detailScroll} showsVerticalScrollIndicator={false}>
        {/* Hero section */}
        <View style={styles.heroSection}>
          <Text style={styles.heroIcon}>{species.icon}</Text>
          <Text style={styles.heroScientific}>{species.scientificName}</Text>
          <Text style={styles.heroFamily}>Family: {species.family}</Text>
          <View style={[styles.statusBadge, statusBadgeStyle(species.status), { alignSelf: 'center', marginTop: 8 }]}>
            <Text style={styles.statusText}>{species.status}</Text>
          </View>
        </View>

        {/* Description */}
        <View style={styles.detailSection}>
          <Text style={styles.sectionTitle}>Description</Text>
          <Text style={styles.sectionBody}>{species.description}</Text>
        </View>

        {/* Identifying Features */}
        <View style={styles.detailSection}>
          <Text style={styles.sectionTitle}>How to Identify</Text>
          {species.identifyingFeatures.map((f, i) => (
            <View key={i} style={styles.featureRow}>
              <Ionicons name="checkmark-circle" size={18} color={palette.accent} />
              <Text style={styles.featureText}>{f}</Text>
            </View>
          ))}
        </View>

        {/* Size & Records */}
        <View style={styles.detailSection}>
          <Text style={styles.sectionTitle}>Size</Text>
          <View style={styles.sizeRow}>
            <View style={styles.sizeCard}>
              <Text style={styles.sizeLabel}>Typical Range</Text>
              <Text style={styles.sizeValue}>
                {species.typicalSizeIn.min}" - {species.typicalSizeIn.max}"
              </Text>
            </View>
            {species.recordLb && (
              <View style={styles.sizeCard}>
                <Text style={styles.sizeLabel}>World Record</Text>
                <Text style={[styles.sizeValue, { color: '#D4A017' }]}>
                  {species.recordLb} lb
                </Text>
              </View>
            )}
          </View>
        </View>

        {/* Habitat & Range */}
        <View style={styles.detailSection}>
          <Text style={styles.sectionTitle}>Habitat</Text>
          <View style={styles.habitatTags}>
            {species.habitat.map((h, i) => (
              <View key={i} style={styles.habitatTag}>
                <Text style={styles.habitatTagText}>{h}</Text>
              </View>
            ))}
          </View>
          <Text style={[styles.sectionBody, { marginTop: 8 }]}>{species.range}</Text>
        </View>

        {/* Fun Facts */}
        {species.funFacts && species.funFacts.length > 0 && (
          <View style={styles.detailSection}>
            <Text style={styles.sectionTitle}>Did You Know?</Text>
            {species.funFacts.map((f, i) => (
              <View key={i} style={styles.factRow}>
                <Text style={styles.factBullet}>💡</Text>
                <Text style={styles.factText}>{f}</Text>
              </View>
            ))}
          </View>
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const colorMap: Record<string, string> = {
  green: '#4A7A4A',
  bronze: '#CD7F32',
  gold: '#DAA520',
  silver: '#C0C0C0',
  gray: '#808080',
  brown: '#8B4513',
  blue: '#4682B4',
};

function statusBadgeStyle(status: FishSpecies['status']) {
  switch (status) {
    case 'sport':
      return { backgroundColor: '#E8F5E9' };
    case 'common':
      return { backgroundColor: '#E3F2FD' };
    case 'threatened':
      return { backgroundColor: '#FFF3E0' };
    case 'endangered':
      return { backgroundColor: '#FFEBEE' };
    case 'invasive':
      return { backgroundColor: '#F3E5F5' };
    default:
      return { backgroundColor: '#F5F5F5' };
  }
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  searchContainer: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 4,
  },
  searchInputWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: palette.border,
  },
  searchInput: {
    flex: 1,
    fontSize: 15,
    color: palette.text,
    marginLeft: 8,
  },
  actionRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingVertical: 8,
    gap: 10,
  },
  actionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1.5,
    borderColor: palette.accent,
    gap: 6,
  },
  actionButtonActive: {
    backgroundColor: palette.accent,
  },
  actionButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.accent,
  },
  actionButtonTextActive: {
    color: '#fff',
  },
  identifyPanel: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    borderRadius: 12,
    padding: 16,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  identifyTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 12,
  },
  identifyRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginBottom: 10,
    gap: 10,
  },
  identifyField: {
    flex: 1,
  },
  identifyLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  colorPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: '#F5F5F5',
    marginRight: 6,
    gap: 4,
  },
  colorPillActive: {
    backgroundColor: palette.accent,
  },
  colorDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  colorPillText: {
    fontSize: 12,
    color: palette.text,
  },
  colorPillTextActive: {
    color: '#fff',
  },
  shapePill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: '#F5F5F5',
    marginRight: 6,
  },
  shapePillActive: {
    backgroundColor: palette.accent,
  },
  shapePillText: {
    fontSize: 12,
    color: palette.text,
    textTransform: 'capitalize',
  },
  shapePillTextActive: {
    color: '#fff',
  },
  sizeInput: {
    backgroundColor: '#F5F5F5',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 14,
    color: palette.text,
    width: 100,
  },
  identifyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.accent,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    gap: 6,
  },
  identifyButtonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 14,
  },
  idResults: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: palette.border,
  },
  idResultsTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 8,
  },
  idResultRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    gap: 10,
  },
  idResultIcon: {
    fontSize: 24,
  },
  idResultName: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  idResultReason: {
    fontSize: 11,
    color: palette.textSecondary,
  },
  confidenceBar: {
    width: 50,
    height: 6,
    backgroundColor: '#E0E0E0',
    borderRadius: 3,
    overflow: 'hidden',
  },
  confidenceFill: {
    height: '100%',
    borderRadius: 3,
  },
  confidenceText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
    width: 36,
    textAlign: 'right',
  },
  comparePanel: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    borderRadius: 12,
    padding: 16,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  comparePair: {
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 6,
  },
  compareSpecies: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  compareIcon: {
    fontSize: 18,
  },
  compareName: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  compareTip: {
    fontSize: 12,
    color: palette.textSecondary,
    width: '100%',
    marginTop: 4,
    lineHeight: 16,
  },
  filterRow: {
    paddingLeft: 16,
    paddingVertical: 6,
  },
  filterPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: '#F0F0EC',
    marginRight: 6,
    gap: 4,
  },
  filterPillActive: {
    backgroundColor: palette.accent,
  },
  filterPillText: {
    fontSize: 12,
    fontWeight: '500',
    color: palette.text,
  },
  filterPillTextActive: {
    color: '#fff',
  },
  filterDivider: {
    width: 1,
    height: 20,
    backgroundColor: palette.border,
    marginHorizontal: 4,
    alignSelf: 'center',
  },
  resultCount: {
    fontSize: 12,
    color: palette.textSecondary,
    paddingHorizontal: 16,
    paddingVertical: 4,
  },
  listContent: {
    paddingHorizontal: 16,
    paddingBottom: 20,
  },
  speciesCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: palette.border,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  speciesIcon: {
    fontSize: 28,
    marginRight: 10,
  },
  cardHeaderText: {
    flex: 1,
  },
  speciesName: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  scientificName: {
    fontSize: 12,
    fontStyle: 'italic',
    color: palette.textSecondary,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  statusText: {
    fontSize: 10,
    fontWeight: '600',
    color: palette.text,
    textTransform: 'capitalize',
  },
  cardDescription: {
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
    marginBottom: 8,
  },
  cardFooter: {
    flexDirection: 'row',
    gap: 12,
  },
  cardStat: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  cardStatText: {
    fontSize: 12,
    color: palette.textSecondary,
  },
  // Detail modal
  detailContainer: {
    flex: 1,
    backgroundColor: palette.background,
  },
  detailHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  closeButton: {
    width: 40,
    height: 40,
    justifyContent: 'center',
    alignItems: 'center',
  },
  detailTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
  },
  detailScroll: {
    flex: 1,
  },
  heroSection: {
    alignItems: 'center',
    paddingVertical: 24,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  heroIcon: {
    fontSize: 64,
    marginBottom: 8,
  },
  heroScientific: {
    fontSize: 16,
    fontStyle: 'italic',
    color: palette.textSecondary,
  },
  heroFamily: {
    fontSize: 13,
    color: palette.textSecondary,
    marginTop: 4,
  },
  detailSection: {
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 10,
  },
  sectionBody: {
    fontSize: 14,
    color: palette.textSecondary,
    lineHeight: 20,
  },
  featureRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginBottom: 8,
  },
  featureText: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    lineHeight: 20,
  },
  sizeRow: {
    flexDirection: 'row',
    gap: 12,
  },
  sizeCard: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: palette.border,
  },
  sizeLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.textSecondary,
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  sizeValue: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
  },
  habitatTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  habitatTag: {
    backgroundColor: '#E8F5E9',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  habitatTagText: {
    fontSize: 12,
    color: '#2E7D32',
    fontWeight: '500',
  },
  factRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 8,
  },
  factBullet: {
    fontSize: 16,
  },
  factText: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    lineHeight: 20,
  },
});
