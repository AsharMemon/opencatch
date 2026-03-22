/**
 * OpenCatch — Lake Finder Screen
 *
 * Search and filter fishing spots by species, amenities, distance, and size.
 * Uses pre-bundled spots + Overpass-discovered spots for data.
 *
 * - Filter by species (bass, trout, walleye, catfish, pike, crappie, salmon, etc.)
 * - Filter by amenities (boat launch, shore access, parking, camping)
 * - Filter by distance from current location (10mi - 500mi)
 * - Filter by size (pond, small lake, medium lake, large lake/reservoir)
 * - Sort by distance, score, or name
 * - Tap result to navigate to map centered on that lake
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { type as typeStyles, fonts } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import { RAW_SPOTS, type StaticSpot } from '../data/topFishingSpots';
import { isCanadianLocation } from '../services/canadaData';
import { getCanadianPublicLands, type PublicLandArea } from '../services/canadianPublicLands';

// ── Types ────────────────────────────────────────────────────────────────────

interface LakeResult {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: string;
  state: string;
  country: string;
  distanceMiles: number;
  species: string[];
  amenities: string[];
  sizeCategory: SizeCategory;
}

type SizeCategory = 'pond' | 'small' | 'medium' | 'large';
type SortKey = 'distance' | 'name';

// ── Species definitions ──────────────────────────────────────────────────────

interface SpeciesFilter {
  id: string;
  label: string;
  icon: string;
  color: string;
  /** Latitude range where species is commonly found */
  latRange: [number, number];
  /** Water body types where species thrives */
  waterTypes: string[];
}

const SPECIES_FILTERS: SpeciesFilter[] = [
  { id: 'bass', label: 'Bass', icon: 'fish', color: '#4CAF50', latRange: [25, 48], waterTypes: ['lake', 'reservoir', 'pond', 'river'] },
  { id: 'trout', label: 'Trout', icon: 'fish', color: '#E91E63', latRange: [32, 52], waterTypes: ['river', 'lake', 'reservoir'] },
  { id: 'walleye', label: 'Walleye', icon: 'fish', color: '#FF9800', latRange: [38, 52], waterTypes: ['lake', 'reservoir', 'river'] },
  { id: 'catfish', label: 'Catfish', icon: 'fish', color: '#795548', latRange: [25, 45], waterTypes: ['river', 'lake', 'reservoir', 'pond'] },
  { id: 'pike', label: 'Pike', icon: 'fish', color: '#607D8B', latRange: [40, 55], waterTypes: ['lake', 'reservoir', 'river'] },
  { id: 'crappie', label: 'Crappie', icon: 'fish', color: '#9C27B0', latRange: [28, 48], waterTypes: ['lake', 'reservoir', 'pond'] },
  { id: 'salmon', label: 'Salmon', icon: 'fish', color: '#F44336', latRange: [38, 55], waterTypes: ['river', 'lake'] },
  { id: 'panfish', label: 'Panfish', icon: 'fish', color: '#2196F3', latRange: [25, 50], waterTypes: ['lake', 'pond', 'reservoir'] },
  { id: 'musky', label: 'Musky', icon: 'fish', color: '#3F51B5', latRange: [38, 50], waterTypes: ['lake', 'reservoir', 'river'] },
  { id: 'perch', label: 'Perch', icon: 'fish', color: '#CDDC39', latRange: [35, 52], waterTypes: ['lake', 'reservoir'] },
];

// ── Amenity definitions ──────────────────────────────────────────────────────

interface AmenityFilter {
  id: string;
  label: string;
  icon: string;
  color: string;
}

const AMENITY_FILTERS: AmenityFilter[] = [
  { id: 'boat_launch', label: 'Boat Launch', icon: 'boat', color: '#EA580C' },
  { id: 'shore', label: 'Shore Access', icon: 'walk', color: '#0D9488' },
  { id: 'parking', label: 'Parking', icon: 'car', color: '#2563EB' },
  { id: 'camping', label: 'Camping', icon: 'bonfire', color: '#059669' },
];

// ── Size definitions ─────────────────────────────────────────────────────────

const SIZE_OPTIONS: { id: SizeCategory; label: string; icon: string }[] = [
  { id: 'pond', label: 'Pond', icon: 'water' },
  { id: 'small', label: 'Small Lake', icon: 'water' },
  { id: 'medium', label: 'Medium Lake', icon: 'water' },
  { id: 'large', label: 'Large / Reservoir', icon: 'water' },
];

// ── Haversine ────────────────────────────────────────────────────────────────

function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Heuristic: guess species for a spot ──────────────────────────────────────

function guessSpecies(spot: StaticSpot): string[] {
  return SPECIES_FILTERS
    .filter((sp) =>
      spot.lat >= sp.latRange[0] &&
      spot.lat <= sp.latRange[1] &&
      sp.waterTypes.includes(spot.type),
    )
    .map((sp) => sp.id);
}

// ── Heuristic: guess amenities ───────────────────────────────────────────────

function guessAmenities(spot: StaticSpot): string[] {
  const amenities: string[] = [];
  // Larger water bodies are more likely to have amenities
  if (spot.type === 'reservoir' || spot.type === 'lake') {
    amenities.push('boat_launch', 'parking');
  }
  // All spots have some shore access
  amenities.push('shore');
  // Reservoirs + big lakes often have camping
  if (spot.type === 'reservoir') {
    amenities.push('camping');
  }
  return amenities;
}

// ── Heuristic: guess size category ───────────────────────────────────────────

function guessSizeCategory(spot: StaticSpot): SizeCategory {
  if (spot.type === 'pond') return 'pond';
  if (spot.type === 'reservoir') return 'large';
  // Name heuristics
  const name = spot.name.toLowerCase();
  if (name.includes('great') || name.includes('lake erie') || name.includes('lake michigan') ||
      name.includes('lake superior') || name.includes('lake huron') || name.includes('lake ontario')) {
    return 'large';
  }
  if (name.includes('reservoir') || name.includes('pool')) return 'large';
  if (spot.type === 'river') return 'medium';
  return 'medium';
}

// ── Filter Chip ──────────────────────────────────────────────────────────────

function FilterChip({
  label,
  icon,
  color,
  selected,
  onPress,
}: {
  label: string;
  icon: string;
  color: string;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={[
        chipStyles.chip,
        selected && { backgroundColor: color + '18', borderColor: color },
      ]}
      onPress={() => { hapticLight(); onPress(); }}
    >
      <Ionicons name={icon as any} size={14} color={selected ? color : palette.textMuted} />
      <Text style={[chipStyles.text, selected && { color }]}>{label}</Text>
    </Pressable>
  );
}

const chipStyles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 20,
    backgroundColor: palette.surfaceRaised,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  text: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textMuted,
  },
});

// ── Result Card ──────────────────────────────────────────────────────────────

function LakeResultCard({
  lake,
  onPress,
}: {
  lake: LakeResult;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={({ pressed }) => [resultStyles.card, pressed && { opacity: 0.9, transform: [{ scale: 0.98 }] }]}
      onPress={onPress}
    >
      <View style={resultStyles.header}>
        <View style={{ flex: 1 }}>
          <Text style={resultStyles.name} numberOfLines={1}>{lake.name}</Text>
          <Text style={resultStyles.location}>{lake.state}, {lake.country}</Text>
        </View>
        <View style={resultStyles.distBadge}>
          <Ionicons name="navigate-outline" size={12} color={palette.accent} />
          <Text style={resultStyles.distText}>{lake.distanceMiles.toFixed(0)} mi</Text>
        </View>
      </View>

      {/* Species badges */}
      <View style={resultStyles.badgeRow}>
        {lake.species.slice(0, 5).map((sp) => {
          const def = SPECIES_FILTERS.find((s) => s.id === sp);
          if (!def) return null;
          return (
            <View key={sp} style={[resultStyles.speciesBadge, { backgroundColor: def.color + '14' }]}>
              <Text style={[resultStyles.speciesBadgeText, { color: def.color }]}>{def.label}</Text>
            </View>
          );
        })}
      </View>

      {/* Amenity icons */}
      <View style={resultStyles.amenityRow}>
        {lake.amenities.map((am) => {
          const def = AMENITY_FILTERS.find((a) => a.id === am);
          if (!def) return null;
          return (
            <View key={am} style={resultStyles.amenityItem}>
              <Ionicons name={def.icon as any} size={13} color={def.color} />
              <Text style={resultStyles.amenityText}>{def.label}</Text>
            </View>
          );
        })}
        <View style={{ flex: 1 }} />
        <View style={resultStyles.typeBadge}>
          <Text style={resultStyles.typeText}>
            {lake.type === 'reservoir' ? 'Reservoir' : lake.type.charAt(0).toUpperCase() + lake.type.slice(1)}
          </Text>
        </View>
      </View>
    </Pressable>
  );
}

const resultStyles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    marginBottom: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  location: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  distBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.accentLight,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  distText: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.accent,
  },
  badgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginBottom: 8,
  },
  speciesBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  speciesBadgeText: {
    fontSize: 11,
    fontWeight: '600',
  },
  amenityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    flexWrap: 'wrap',
  },
  amenityItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
  },
  amenityText: {
    fontSize: 11,
    color: palette.textMuted,
  },
  typeBadge: {
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  typeText: {
    fontSize: 11,
    fontWeight: '500',
    color: palette.textSecondary,
  },
});

// ── Distance Slider ──────────────────────────────────────────────────────────

const DISTANCE_STEPS = [10, 25, 50, 100, 200, 500];

function DistanceSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <View style={distStyles.container}>
      <Text style={distStyles.label}>Max Distance</Text>
      <View style={distStyles.chips}>
        {DISTANCE_STEPS.map((d) => (
          <Pressable
            key={d}
            style={[distStyles.chip, value === d && distStyles.chipActive]}
            onPress={() => { hapticLight(); onChange(d); }}
          >
            <Text style={[distStyles.chipText, value === d && distStyles.chipTextActive]}>
              {d} mi
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const distStyles = StyleSheet.create({
  container: {
    gap: 8,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
    paddingLeft: 4,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 20,
    backgroundColor: palette.surfaceRaised,
  },
  chipActive: {
    backgroundColor: palette.accentLight,
  },
  chipText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textMuted,
  },
  chipTextActive: {
    color: palette.accent,
  },
});

// ── Main Screen ──────────────────────────────────────────────────────────────

export function LakeFinderScreen() {
  const navigation = useNavigation<any>();
  const [userLat, setUserLat] = useState<number | null>(null);
  const [userLon, setUserLon] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSpecies, setSelectedSpecies] = useState<Set<string>>(new Set());
  const [selectedAmenities, setSelectedAmenities] = useState<Set<string>>(new Set());
  const [selectedSizes, setSelectedSizes] = useState<Set<SizeCategory>>(new Set());
  const [maxDistance, setMaxDistance] = useState(200);
  const [sortBy, setSortBy] = useState<SortKey>('distance');
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [publicLands, setPublicLands] = useState<PublicLandArea[]>([]);

  // Get user location
  useEffect(() => {
    (async () => {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status === 'granted') {
          const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          setUserLat(loc.coords.latitude);
          setUserLon(loc.coords.longitude);
        } else {
          // Default to center of US
          setUserLat(39.8);
          setUserLon(-98.5);
        }
      } catch {
        setUserLat(39.8);
        setUserLon(-98.5);
      }
      setLoading(false);
    })();
  }, []);

  // Fetch Canadian public lands if user is in Canada
  useEffect(() => {
    if (userLat != null && userLon != null && isCanadianLocation(userLat, userLon)) {
      getCanadianPublicLands(userLat, userLon, 100)
        .then((lands) => setPublicLands(lands.slice(0, 10)))
        .catch(() => {});
    }
  }, [userLat, userLon]);

  // Build enriched lake results
  const allLakes = useMemo<LakeResult[]>(() => {
    if (userLat === null || userLon === null) return [];
    return RAW_SPOTS.map((spot) => ({
      id: spot.id,
      name: spot.name,
      lat: spot.lat,
      lon: spot.lon,
      type: spot.type,
      state: spot.state,
      country: spot.country,
      distanceMiles: haversine(userLat, userLon, spot.lat, spot.lon),
      species: guessSpecies(spot),
      amenities: guessAmenities(spot),
      sizeCategory: guessSizeCategory(spot),
    }));
  }, [userLat, userLon]);

  // Apply filters
  const filteredLakes = useMemo(() => {
    let results = allLakes;

    // Distance
    results = results.filter((l) => l.distanceMiles <= maxDistance);

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      results = results.filter((l) =>
        l.name.toLowerCase().includes(q) ||
        l.state.toLowerCase().includes(q),
      );
    }

    // Species
    if (selectedSpecies.size > 0) {
      results = results.filter((l) =>
        [...selectedSpecies].some((sp) => l.species.includes(sp)),
      );
    }

    // Amenities
    if (selectedAmenities.size > 0) {
      results = results.filter((l) =>
        [...selectedAmenities].every((am) => l.amenities.includes(am)),
      );
    }

    // Size
    if (selectedSizes.size > 0) {
      results = results.filter((l) => selectedSizes.has(l.sizeCategory));
    }

    // Sort
    if (sortBy === 'distance') {
      results.sort((a, b) => a.distanceMiles - b.distanceMiles);
    } else {
      results.sort((a, b) => a.name.localeCompare(b.name));
    }

    return results;
  }, [allLakes, maxDistance, searchQuery, selectedSpecies, selectedAmenities, selectedSizes, sortBy]);

  const toggleSpecies = useCallback((id: string) => {
    setSelectedSpecies((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAmenity = useCallback((id: string) => {
    setSelectedAmenities((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSize = useCallback((id: SizeCategory) => {
    setSelectedSizes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => {
    setSelectedSpecies(new Set());
    setSelectedAmenities(new Set());
    setSelectedSizes(new Set());
    setSearchQuery('');
    setMaxDistance(200);
  }, []);

  const activeFilterCount = selectedSpecies.size + selectedAmenities.size + selectedSizes.size +
    (maxDistance !== 200 ? 1 : 0);

  const handleSelectLake = useCallback((lake: LakeResult) => {
    hapticLight();
    navigation.navigate('Tabs', {
      screen: 'MapTab',
      params: { focusLat: lake.lat, focusLon: lake.lon, focusName: lake.name },
    });
  }, [navigation]);

  const renderItem = useCallback(({ item }: { item: LakeResult }) => (
    <LakeResultCard lake={item} onPress={() => handleSelectLake(item)} />
  ), [handleSelectLake]);

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={styles.loadingText}>Getting your location...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Search bar */}
      <View style={styles.searchRow}>
        <View style={styles.searchBar}>
          <Ionicons name="search" size={18} color={palette.textMuted} />
          <TextInput
            style={styles.searchInput}
            placeholder="Search lakes, rivers, states..."
            placeholderTextColor={palette.textDim}
            value={searchQuery}
            onChangeText={setSearchQuery}
            returnKeyType="search"
          />
          {searchQuery.length > 0 && (
            <Pressable onPress={() => setSearchQuery('')}>
              <Ionicons name="close-circle" size={18} color={palette.textMuted} />
            </Pressable>
          )}
        </View>
        <Pressable
          style={[styles.filterToggle, filtersExpanded && styles.filterToggleActive]}
          onPress={() => { hapticLight(); setFiltersExpanded(!filtersExpanded); }}
        >
          <Ionicons name="options" size={20} color={filtersExpanded ? palette.accent : palette.textSecondary} />
          {activeFilterCount > 0 && (
            <View style={styles.filterBadge}>
              <Text style={styles.filterBadgeText}>{activeFilterCount}</Text>
            </View>
          )}
        </Pressable>
      </View>

      {/* Expandable filters */}
      {filtersExpanded && (
        <ScrollView
          style={styles.filtersContainer}
          contentContainerStyle={styles.filtersContent}
          showsVerticalScrollIndicator={false}
        >
          {/* Species */}
          <View style={styles.filterSection}>
            <Text style={styles.filterSectionTitle}>Species</Text>
            <View style={styles.chipRow}>
              {SPECIES_FILTERS.map((sp) => (
                <FilterChip
                  key={sp.id}
                  label={sp.label}
                  icon={sp.icon}
                  color={sp.color}
                  selected={selectedSpecies.has(sp.id)}
                  onPress={() => toggleSpecies(sp.id)}
                />
              ))}
            </View>
          </View>

          {/* Amenities */}
          <View style={styles.filterSection}>
            <Text style={styles.filterSectionTitle}>Amenities</Text>
            <View style={styles.chipRow}>
              {AMENITY_FILTERS.map((am) => (
                <FilterChip
                  key={am.id}
                  label={am.label}
                  icon={am.icon}
                  color={am.color}
                  selected={selectedAmenities.has(am.id)}
                  onPress={() => toggleAmenity(am.id)}
                />
              ))}
            </View>
          </View>

          {/* Size */}
          <View style={styles.filterSection}>
            <Text style={styles.filterSectionTitle}>Size</Text>
            <View style={styles.chipRow}>
              {SIZE_OPTIONS.map((sz) => (
                <FilterChip
                  key={sz.id}
                  label={sz.label}
                  icon={sz.icon}
                  color={palette.accent}
                  selected={selectedSizes.has(sz.id)}
                  onPress={() => toggleSize(sz.id)}
                />
              ))}
            </View>
          </View>

          {/* Distance */}
          <DistanceSelector value={maxDistance} onChange={setMaxDistance} />

          {/* Clear */}
          {activeFilterCount > 0 && (
            <Pressable style={styles.clearBtn} onPress={clearFilters}>
              <Ionicons name="close" size={14} color={palette.error} />
              <Text style={styles.clearBtnText}>Clear all filters</Text>
            </Pressable>
          )}
        </ScrollView>
      )}

      {/* Canadian Public Lands (if in Canada) */}
      {publicLands.length > 0 && (
        <View style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Ionicons name="leaf-outline" size={16} color="#2E7D32" />
            <Text style={{ fontSize: 13, fontWeight: '700', color: palette.text }}>
              Public Fishing Access ({publicLands.length})
            </Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
            {publicLands.map((land) => (
              <View key={land.id} style={{ backgroundColor: '#FFFFFF', borderRadius: 10, padding: 10, width: 160, shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 4, elevation: 1 }}>
                <Text style={{ fontSize: 13, fontWeight: '600', color: palette.text }} numberOfLines={1}>{land.name}</Text>
                <Text style={{ fontSize: 11, color: palette.textMuted }}>{land.type.replace(/_/g, ' ')}</Text>
                {land.operator && <Text style={{ fontSize: 10, color: palette.textDim }} numberOfLines={1}>{land.operator}</Text>}
                {land.fishingAccess === 'yes' && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 4 }}>
                    <Ionicons name="fish-outline" size={12} color="#2E7D32" />
                    <Text style={{ fontSize: 10, color: '#2E7D32', fontWeight: '600' }}>Fishing Access</Text>
                  </View>
                )}
              </View>
            ))}
          </ScrollView>
        </View>
      )}

      {/* Sort + result count */}
      <View style={styles.resultHeader}>
        <Text style={styles.resultCount}>
          {filteredLakes.length} result{filteredLakes.length !== 1 ? 's' : ''}
        </Text>
        <View style={styles.sortRow}>
          {(['distance', 'name'] as SortKey[]).map((key) => (
            <Pressable
              key={key}
              style={[styles.sortChip, sortBy === key && styles.sortChipActive]}
              onPress={() => { hapticLight(); setSortBy(key); }}
            >
              <Text style={[styles.sortChipText, sortBy === key && styles.sortChipTextActive]}>
                {key.charAt(0).toUpperCase() + key.slice(1)}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {/* Results */}
      {filteredLakes.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="fish-outline" size={48} color={palette.textDim} />
          <Text style={styles.emptyTitle}>No Lakes Found</Text>
          <Text style={styles.emptySubtitle}>Try adjusting your filters or increasing the distance</Text>
        </View>
      ) : (
        <FlatList
          data={filteredLakes}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          initialNumToRender={15}
          maxToRenderPerBatch={10}
        />
      )}
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.background,
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    color: palette.textMuted,
  },

  // Search
  searchRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 4,
    gap: 8,
  },
  searchBar: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 12,
    paddingHorizontal: 12,
    height: 42,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 15,
    color: palette.text,
    paddingVertical: 0,
  },
  filterToggle: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  filterToggleActive: {
    backgroundColor: palette.accentLight,
    borderColor: palette.accent,
  },
  filterBadge: {
    position: 'absolute',
    top: 4,
    right: 4,
    backgroundColor: palette.accent,
    width: 16,
    height: 16,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  filterBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FFFFFF',
  },

  // Filters
  filtersContainer: {
    maxHeight: 320,
  },
  filtersContent: {
    padding: 16,
    paddingTop: 8,
    gap: 14,
  },
  filterSection: {
    gap: 8,
  },
  filterSectionTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
    paddingLeft: 4,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  clearBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'center',
    gap: 4,
    paddingVertical: 6,
  },
  clearBtnText: {
    fontSize: 13,
    color: palette.error,
    fontWeight: '500',
  },

  // Result header
  resultHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  resultCount: {
    fontSize: 13,
    color: palette.textMuted,
    fontWeight: '500',
  },
  sortRow: {
    flexDirection: 'row',
    gap: 6,
  },
  sortChip: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
    backgroundColor: palette.surfaceRaised,
  },
  sortChipActive: {
    backgroundColor: palette.accentLight,
  },
  sortChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
  },
  sortChipTextActive: {
    color: palette.accent,
  },

  // List
  listContent: {
    padding: 16,
    paddingTop: 4,
    paddingBottom: 40,
  },

  // Empty
  emptyState: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
    gap: 8,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
    marginTop: 8,
  },
  emptySubtitle: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
  },
});
