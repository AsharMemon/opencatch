import React, { useEffect, useRef, useState } from 'react';
import {
  Animated,
  FlatList,
  Keyboard,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

const RECENT_SEARCHES_KEY = '@opencatch_recent_searches';
const MAX_RECENT = 10;

// Species quick filters
const SPECIES_FILTERS = [
  { key: 'bass', label: 'Bass spots', icon: 'fish' as const, query: 'bass' },
  { key: 'trout', label: 'Trout streams', icon: 'water' as const, query: 'trout' },
  { key: 'walleye', label: 'Walleye lakes', icon: 'fish' as const, query: 'walleye' },
  { key: 'pike', label: 'Pike waters', icon: 'fish' as const, query: 'pike' },
  { key: 'catfish', label: 'Catfish holes', icon: 'fish' as const, query: 'catfish' },
  { key: 'crappie', label: 'Crappie spots', icon: 'fish' as const, query: 'crappie' },
];

interface NearbySpot {
  id: string;
  name: string;
  subtitle: string;
  distanceMi?: number;
  type?: string; // lake, river, reservoir, etc.
}

interface EnhancedSearchBarProps {
  value: string;
  onChangeText: (text: string) => void;
  onClear: () => void;
  onSubmit?: (text: string) => void;
  onSelectLocation?: (id: string) => void;
  nearbySpots?: NearbySpot[];
  allLocations?: NearbySpot[];
}

function getWaterbodyIcon(type?: string): keyof typeof Ionicons.glyphMap {
  if (!type) return 'location-outline';
  const t = type.toLowerCase();
  if (t.includes('river') || t.includes('stream') || t.includes('creek')) return 'water-outline';
  if (t.includes('reservoir')) return 'server-outline';
  if (t.includes('pond')) return 'ellipse-outline';
  return 'location-outline';
}

function formatDistance(mi?: number): string {
  if (mi == null) return '';
  if (mi < 0.1) return '< 0.1 mi';
  if (mi < 10) return `${mi.toFixed(1)} mi`;
  return `${Math.round(mi)} mi`;
}

export function EnhancedSearchBar({
  value,
  onChangeText,
  onClear,
  onSubmit,
  onSelectLocation,
  nearbySpots = [],
  allLocations = [],
}: EnhancedSearchBarProps) {
  const [focused, setFocused] = useState(false);
  const [recentSearches, setRecentSearches] = useState<string[]>([]);
  const dropdownAnim = useRef(new Animated.Value(0)).current;

  // Load recent searches on mount
  useEffect(() => {
    AsyncStorage.getItem(RECENT_SEARCHES_KEY).then((val) => {
      if (val) {
        try { setRecentSearches(JSON.parse(val)); } catch { /* ignore */ }
      }
    });
  }, []);

  // Animate dropdown
  useEffect(() => {
    Animated.timing(dropdownAnim, {
      toValue: focused ? 1 : 0,
      duration: 200,
      useNativeDriver: false,
    }).start();
  }, [focused, dropdownAnim]);

  const saveRecentSearch = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const updated = [trimmed, ...recentSearches.filter((s) => s !== trimmed)].slice(0, MAX_RECENT);
    setRecentSearches(updated);
    await AsyncStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(updated));
  };

  const clearRecentSearches = async () => {
    setRecentSearches([]);
    await AsyncStorage.removeItem(RECENT_SEARCHES_KEY);
  };

  const handleSubmit = () => {
    if (value.trim()) {
      saveRecentSearch(value.trim());
      onSubmit?.(value.trim());
    }
    Keyboard.dismiss();
    setFocused(false);
  };

  const handleSelectRecent = (text: string) => {
    onChangeText(text);
    onSubmit?.(text);
    Keyboard.dismiss();
    setFocused(false);
  };

  const handleSelectSpecies = (query: string) => {
    onChangeText(query);
    saveRecentSearch(query);
    onSubmit?.(query);
    Keyboard.dismiss();
    setFocused(false);
  };

  // Autocomplete suggestions from all locations
  const suggestions = value.trim().length >= 2
    ? allLocations
        .filter((l) => {
          const q = value.toLowerCase();
          return l.name.toLowerCase().includes(q) || l.subtitle.toLowerCase().includes(q);
        })
        .slice(0, 8)
    : [];

  const showDropdown = focused && value.trim().length === 0;
  const showSuggestions = focused && suggestions.length > 0 && value.trim().length >= 2;

  const dropdownOpacity = dropdownAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 1],
  });

  const topNearby = nearbySpots.slice(0, 5);

  return (
    <View style={styles.wrapper}>
      {/* Search Input */}
      <View style={styles.container}>
        <Ionicons name="search-outline" size={18} color={palette.textMuted} />
        <TextInput
          style={styles.input}
          value={value}
          onChangeText={onChangeText}
          placeholder="Search lakes, rivers, species..."
          placeholderTextColor={palette.textDim}
          selectionColor={palette.accent}
          returnKeyType="search"
          onFocus={() => setFocused(true)}
          onBlur={() => {
            // Small delay so tap events on dropdown still fire
            setTimeout(() => setFocused(false), 200);
          }}
          onSubmitEditing={handleSubmit}
        />
        {value.length > 0 && (
          <Pressable onPress={() => { onClear(); setFocused(true); }} hitSlop={8}>
            <Ionicons name="close-circle" size={18} color={palette.textMuted} />
          </Pressable>
        )}
      </View>

      {/* Autocomplete suggestions while typing */}
      {showSuggestions && (
        <Animated.View style={[styles.dropdown, { opacity: dropdownOpacity }]}>
          {suggestions.map((spot) => (
            <Pressable
              key={spot.id}
              style={styles.suggestionRow}
              onPress={() => {
                onChangeText(spot.name);
                saveRecentSearch(spot.name);
                onSelectLocation?.(spot.id);
                Keyboard.dismiss();
                setFocused(false);
              }}
            >
              <Ionicons name={getWaterbodyIcon(spot.type)} size={16} color={palette.textMuted} />
              <View style={styles.suggestionTextArea}>
                <Text style={styles.suggestionName} numberOfLines={1}>{spot.name}</Text>
                <Text style={styles.suggestionSub} numberOfLines={1}>{spot.subtitle}</Text>
              </View>
              {spot.distanceMi != null && (
                <Text style={styles.suggestionDistance}>{formatDistance(spot.distanceMi)}</Text>
              )}
            </Pressable>
          ))}
        </Animated.View>
      )}

      {/* Dropdown when focused but empty */}
      {showDropdown && (
        <Animated.View style={[styles.dropdown, { opacity: dropdownOpacity }]}>
          {/* Recent Searches */}
          {recentSearches.length > 0 && (
            <View style={styles.section}>
              <View style={styles.sectionHeader}>
                <Text style={styles.sectionTitle}>Recent Searches</Text>
                <Pressable onPress={clearRecentSearches} hitSlop={8}>
                  <Text style={styles.clearText}>Clear</Text>
                </Pressable>
              </View>
              {recentSearches.slice(0, 5).map((text, i) => (
                <Pressable
                  key={`recent-${i}`}
                  style={styles.recentRow}
                  onPress={() => handleSelectRecent(text)}
                >
                  <Ionicons name="time-outline" size={16} color={palette.textDim} />
                  <Text style={styles.recentText} numberOfLines={1}>{text}</Text>
                </Pressable>
              ))}
            </View>
          )}

          {/* Nearby Popular Spots */}
          {topNearby.length > 0 && (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Nearby Popular Spots</Text>
              {topNearby.map((spot) => (
                <Pressable
                  key={spot.id}
                  style={styles.suggestionRow}
                  onPress={() => {
                    onChangeText(spot.name);
                    saveRecentSearch(spot.name);
                    onSelectLocation?.(spot.id);
                    Keyboard.dismiss();
                    setFocused(false);
                  }}
                >
                  <Ionicons name={getWaterbodyIcon(spot.type)} size={16} color={palette.accent} />
                  <View style={styles.suggestionTextArea}>
                    <Text style={styles.suggestionName} numberOfLines={1}>{spot.name}</Text>
                    <Text style={styles.suggestionSub} numberOfLines={1}>{spot.subtitle}</Text>
                  </View>
                  {spot.distanceMi != null && (
                    <Text style={styles.suggestionDistance}>{formatDistance(spot.distanceMi)}</Text>
                  )}
                </Pressable>
              ))}
            </View>
          )}

          {/* Species Filter */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Search by Species</Text>
            <View style={styles.speciesGrid}>
              {SPECIES_FILTERS.map((sp) => (
                <Pressable
                  key={sp.key}
                  style={styles.speciesChip}
                  onPress={() => handleSelectSpecies(sp.query)}
                >
                  <Ionicons name={sp.icon} size={14} color={palette.accent} />
                  <Text style={styles.speciesChipText}>{sp.label}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    zIndex: 100,
  },
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 8,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  input: {
    flex: 1,
    color: palette.text,
    fontSize: 15,
    padding: 0,
  },
  dropdown: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    marginTop: 6,
    paddingVertical: 8,
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
    maxHeight: 400,
  },
  section: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  clearText: {
    fontSize: 12,
    color: palette.accent,
    fontWeight: '600',
    marginBottom: 6,
  },
  recentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
  },
  recentText: {
    flex: 1,
    color: palette.text,
    fontSize: 14,
  },
  suggestionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  suggestionTextArea: {
    flex: 1,
    gap: 1,
  },
  suggestionName: {
    color: palette.text,
    fontSize: 14,
    fontWeight: '500',
  },
  suggestionSub: {
    color: palette.textMuted,
    fontSize: 11,
  },
  suggestionDistance: {
    color: palette.textDim,
    fontSize: 12,
    fontWeight: '600',
  },
  speciesGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  speciesChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 16,
    backgroundColor: palette.accentDim,
  },
  speciesChipText: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '600',
  },
});
