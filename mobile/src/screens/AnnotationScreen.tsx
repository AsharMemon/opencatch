/**
 * OpenCatch — Chart Annotations Screen
 *
 * Manage all map annotations: view, edit, delete, and export as GeoJSON.
 * Annotation drawing itself happens on the MapScreen via the annotation toolbar;
 * this screen provides the list management view.
 *
 * Competitor parity: Navionics chart annotations editor.
 * OpenCatch EXCEEDS with GeoJSON export and bulk management.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  FlatList,
  Platform,
  Pressable,
  Share,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { spacing, radius } from '../theme/spacing';
import {
  loadAnnotations,
  deleteAnnotation,
  clearAnnotations,
  exportAnnotations,
  ANNOTATION_COLORS,
  type MapAnnotation,
  type AnnotationType,
} from '../services/chartAnnotations';
import type { RootStackProps } from '../types/navigation';

// ── Type label + icon mapping ──────────────────────────────────────────────────

const TYPE_META: Record<AnnotationType, { label: string; ionicon: string }> = {
  marker: { label: 'Marker', ionicon: 'location' },
  circle: { label: 'Circle', ionicon: 'ellipse-outline' },
  arrow: { label: 'Arrow', ionicon: 'arrow-forward-outline' },
  text: { label: 'Note', ionicon: 'chatbubble-outline' },
  polygon: { label: 'Area', ionicon: 'shapes-outline' },
};

// ── Component ──────────────────────────────────────────────────────────────────

export function AnnotationScreen({ navigation }: RootStackProps<'Annotations'>) {
  const [annotations, setAnnotations] = useState<MapAnnotation[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(async () => {
    const all = await loadAnnotations();
    // Sort newest first
    all.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
    setAnnotations(all);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, refreshKey]);

  // Re-fetch when screen comes back into focus
  useEffect(() => {
    const unsubscribe = navigation.addListener('focus', () => {
      setRefreshKey((k) => k + 1);
    });
    return unsubscribe;
  }, [navigation]);

  const handleDelete = (ann: MapAnnotation) => {
    Alert.alert(
      'Delete Annotation',
      `Remove "${ann.label || TYPE_META[ann.type].label}"?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await deleteAnnotation(ann.id);
            setRefreshKey((k) => k + 1);
          },
        },
      ],
    );
  };

  const handleClearAll = () => {
    if (annotations.length === 0) return;
    Alert.alert(
      'Clear All Annotations',
      `This will permanently delete all ${annotations.length} annotations. Continue?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete All',
          style: 'destructive',
          onPress: async () => {
            await clearAnnotations();
            setRefreshKey((k) => k + 1);
          },
        },
      ],
    );
  };

  const handleExport = async () => {
    try {
      const geojson = await exportAnnotations();
      const json = JSON.stringify(geojson, null, 2);
      await Share.share({
        message: json,
        title: 'OpenCatch Annotations (GeoJSON)',
      });
    } catch {
      Alert.alert('Export Failed', 'Could not export annotations.');
    }
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const formatCoord = (lat: number, lon: number) => {
    return `${Math.abs(lat).toFixed(4)}${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(4)}${lon >= 0 ? 'E' : 'W'}`;
  };

  const renderItem = ({ item }: { item: MapAnnotation }) => {
    const meta = TYPE_META[item.type];
    return (
      <View style={styles.card}>
        <View style={[styles.iconBadge, { backgroundColor: item.color + '18' }]}>
          <Ionicons name={meta.ionicon as any} size={20} color={item.color} />
        </View>
        <View style={styles.cardContent}>
          <Text style={styles.cardTitle} numberOfLines={1}>
            {item.label || meta.label}
          </Text>
          <Text style={styles.cardSubtitle}>
            {meta.label} {item.type === 'circle' && item.radiusMeters ? `\u2022 ${Math.round(item.radiusMeters)}m radius` : ''}
          </Text>
          <Text style={styles.cardCoord}>
            {formatCoord(item.coordinate.latitude, item.coordinate.longitude)} \u2022 {formatDate(item.createdAt)}
          </Text>
        </View>
        <Pressable
          style={styles.deleteBtn}
          onPress={() => handleDelete(item)}
          hitSlop={12}
        >
          <Ionicons name="trash-outline" size={18} color={palette.error} />
        </Pressable>
      </View>
    );
  };

  return (
    <View style={styles.container}>
      {/* Header actions */}
      <View style={styles.headerActions}>
        <Text style={styles.countLabel}>
          {annotations.length} annotation{annotations.length !== 1 ? 's' : ''}
        </Text>
        <View style={styles.headerButtons}>
          <Pressable style={styles.headerBtn} onPress={handleExport} disabled={annotations.length === 0}>
            <Ionicons
              name="share-outline"
              size={18}
              color={annotations.length === 0 ? palette.textDim : palette.accent}
            />
            <Text style={[styles.headerBtnText, annotations.length === 0 && { color: palette.textDim }]}>
              Export GeoJSON
            </Text>
          </Pressable>
          <Pressable style={styles.headerBtn} onPress={handleClearAll} disabled={annotations.length === 0}>
            <Ionicons
              name="trash-outline"
              size={18}
              color={annotations.length === 0 ? palette.textDim : palette.error}
            />
          </Pressable>
        </View>
      </View>

      {/* Annotation list */}
      {annotations.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="create-outline" size={48} color={palette.textDim} />
          <Text style={styles.emptyTitle}>No Annotations Yet</Text>
          <Text style={styles.emptySubtitle}>
            Open the map and tap the pencil icon to start annotating. Place markers, draw circles,
            add notes, and more.
          </Text>
        </View>
      ) : (
        <FlatList
          data={annotations}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  headerActions: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  countLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  headerButtons: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  headerBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: palette.surfaceRaised,
  },
  headerBtnText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.accent,
  },
  listContent: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.sm,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.md,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  iconBadge: {
    width: 40,
    height: 40,
    borderRadius: radius.sm,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardContent: {
    flex: 1,
    gap: 2,
  },
  cardTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  cardSubtitle: {
    fontSize: 12,
    color: palette.textSecondary,
  },
  cardCoord: {
    fontSize: 11,
    color: palette.textMuted,
  },
  deleteBtn: {
    padding: spacing.sm,
  },
  emptyState: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.xxxl,
    gap: spacing.md,
  },
  emptyTitle: {
    ...typeStyles.sectionHeader,
    color: palette.text,
  },
  emptySubtitle: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    lineHeight: 20,
  },
});
