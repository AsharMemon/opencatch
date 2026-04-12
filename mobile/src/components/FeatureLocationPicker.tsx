import React, { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import type { FeatureLocationSource } from '../services/featureLocation';

interface Props {
  label: string;
  helperText?: string;
  loading?: boolean;
  compact?: boolean;
  activeSource?: FeatureLocationSource;
  searchPlaceholder?: string;
  onUseCurrent?: () => Promise<void> | void;
  onUseMapCenter?: () => Promise<void> | void;
  onSearchLocation?: (query: string) => Promise<void> | void;
}

export function FeatureLocationPicker({
  label,
  helperText,
  loading = false,
  compact = false,
  activeSource,
  searchPlaceholder = 'Search a lake, town, or region',
  onUseCurrent,
  onUseMapCenter,
  onSearchLocation,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const currentActive = activeSource === 'current' || activeSource === 'default';
  const mapCenterActive = activeSource === 'map-center';
  const searchActive = activeSource === 'search' || activeSource === 'selected';

  const handleSearch = async () => {
    if (!onSearchLocation || !query.trim()) return;
    setSubmitting(true);
    try {
      await onSearchLocation(query.trim());
      setExpanded(false);
      setQuery('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={[styles.card, compact && styles.cardCompact]}>
      <View style={styles.headerRow}>
        <View style={styles.headerCopy}>
          <View style={styles.labelRow}>
            <Ionicons name="location-outline" size={compact ? 13 : 14} color={palette.accent} />
            <Text style={[styles.label, compact && styles.labelCompact]} numberOfLines={1}>
              {label}
            </Text>
          </View>
          {helperText ? (
            <Text style={[styles.helper, compact && styles.helperCompact]} numberOfLines={compact ? 1 : 2}>
              {helperText}
            </Text>
          ) : null}
        </View>
        {(loading || submitting) ? (
          <ActivityIndicator size="small" color={palette.accent} />
        ) : null}
      </View>

      <View style={[styles.actionRow, compact && styles.actionRowCompact]}>
        {onUseCurrent ? (
          <Pressable
            style={[styles.actionChip, currentActive && styles.actionChipActive]}
            onPress={onUseCurrent}
          >
            <Ionicons
              name="locate-outline"
              size={13}
              color={currentActive ? '#FFFFFF' : palette.textSecondary}
            />
            <Text style={[styles.actionText, currentActive && styles.actionTextActive]}>Current</Text>
          </Pressable>
        ) : null}
        {onUseMapCenter ? (
          <Pressable
            style={[styles.actionChip, mapCenterActive && styles.actionChipActive]}
            onPress={onUseMapCenter}
          >
            <Ionicons
              name="map-outline"
              size={13}
              color={mapCenterActive ? '#FFFFFF' : palette.textSecondary}
            />
            <Text style={[styles.actionText, mapCenterActive && styles.actionTextActive]}>Map Center</Text>
          </Pressable>
        ) : null}
        {onSearchLocation ? (
          <Pressable
            style={[styles.actionChip, (expanded || searchActive) && styles.actionChipActive]}
            onPress={() => setExpanded((prev) => !prev)}
          >
            <Ionicons
              name="search-outline"
              size={13}
              color={expanded || searchActive ? '#FFFFFF' : palette.textSecondary}
            />
            <Text style={[styles.actionText, (expanded || searchActive) && styles.actionTextActive]}>Search</Text>
          </Pressable>
        ) : null}
      </View>

      {expanded && onSearchLocation ? (
        <View style={styles.searchWrap}>
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder={searchPlaceholder}
            placeholderTextColor={palette.textDim}
            style={styles.input}
            returnKeyType="search"
            onSubmitEditing={handleSearch}
          />
          <Pressable
            style={[styles.searchButton, !query.trim() && styles.searchButtonDisabled]}
            onPress={handleSearch}
            disabled={!query.trim() || submitting}
          >
            <Text style={styles.searchButtonText}>Go</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: 'rgba(255,255,255,0.94)',
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 10,
    borderWidth: 1,
    borderColor: 'rgba(17, 58, 82, 0.08)',
  },
  cardCompact: {
    borderRadius: 14,
    paddingHorizontal: 10,
    paddingVertical: 7,
    gap: 6,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  headerCopy: {
    flex: 1,
    gap: 2,
  },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  label: {
    color: palette.text,
    fontSize: 13.5,
    fontWeight: '700',
    flex: 1,
  },
  labelCompact: {
    fontSize: 12,
  },
  helper: {
    color: palette.textMuted,
    fontSize: 11,
    lineHeight: 15,
  },
  helperCompact: {
    fontSize: 10.5,
  },
  actionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  actionChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: palette.surfaceRaised,
  },
  actionChipActive: {
    backgroundColor: palette.accent,
  },
  actionText: {
    color: palette.textSecondary,
    fontSize: 11.5,
    fontWeight: '700',
  },
  actionTextActive: {
    color: '#FFFFFF',
  },
  actionRowCompact: {
    gap: 6,
  },
  searchWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  input: {
    flex: 1,
    height: 40,
    borderRadius: 12,
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 12,
    color: palette.text,
    fontSize: 13,
  },
  searchButton: {
    minWidth: 52,
    alignItems: 'center',
    justifyContent: 'center',
    height: 40,
    borderRadius: 12,
    backgroundColor: palette.accent,
    paddingHorizontal: 14,
  },
  searchButtonDisabled: {
    opacity: 0.45,
  },
  searchButtonText: {
    color: '#FFFFFF',
    fontSize: 12.5,
    fontWeight: '800',
  },
});
