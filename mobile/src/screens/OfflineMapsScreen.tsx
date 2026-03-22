/**
 * OpenCatch — Offline Maps Screen
 *
 * Manages offline map tile regions using the offlineCharts service.
 * Users can view saved regions, estimate download sizes, and manage storage.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { fonts } from '../theme/typography';
import { offlineCharts, type OfflineRegion, type TileEstimate } from '../services/offlineCharts';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(ts: number): string {
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const STATUS_CONFIG: Record<string, { icon: string; color: string; label: string }> = {
  complete: { icon: 'checkmark-circle', color: '#4CAF50', label: 'Downloaded' },
  downloading: { icon: 'cloud-download', color: palette.accent, label: 'Downloading' },
  pending: { icon: 'time', color: '#F57C00', label: 'Pending' },
  paused: { icon: 'pause-circle', color: '#78909C', label: 'Paused' },
  error: { icon: 'alert-circle', color: '#D32F2F', label: 'Error' },
};

export function OfflineMapsScreen() {
  const [regions, setRegions] = useState<OfflineRegion[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalSize, setTotalSize] = useState(0);
  const [estimating, setEstimating] = useState(false);
  const [estimate, setEstimate] = useState<TileEstimate | null>(null);

  const loadRegions = useCallback(async () => {
    try {
      const r = await offlineCharts.getRegions();
      setRegions(r);
      const sizeInfo = await offlineCharts.getTotalSize();
      setTotalSize(sizeInfo.sizeMB * 1024 * 1024);
    } catch {
      // Silent fail
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRegions();
  }, [loadRegions]);

  const handleEstimate = useCallback(async () => {
    setEstimating(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Location Required', 'Enable location to estimate download for your current area.');
        return;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      // Create a ~10km bounding box around current position
      const delta = 0.09; // ~10km
      const est = offlineCharts.estimateRegion({
        north: lat + delta,
        south: lat - delta,
        east: lon + delta,
        west: lon - delta,
      }, 8, 14);
      setEstimate(est);
    } catch {
      Alert.alert('Error', 'Could not estimate download size.');
    } finally {
      setEstimating(false);
    }
  }, []);

  const handleDownloadHere = useCallback(async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') return;
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      const delta = 0.09;

      await offlineCharts.createRegion(
        'My Area',
        { north: lat + delta, south: lat - delta, east: lon + delta, west: lon - delta },
        8, 14,
      );
      loadRegions();
    } catch (err: any) {
      Alert.alert('Download Error', err.message || 'Failed to download region.');
    }
  }, [loadRegions]);

  const handleDelete = useCallback(async (regionId: string) => {
    Alert.alert(
      'Delete Region',
      'This will remove the offline map data for this region.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await offlineCharts.deleteRegion(regionId);
            loadRegions();
          },
        },
      ],
    );
  }, [loadRegions]);

  const renderRegion = useCallback(({ item }: { item: OfflineRegion }) => {
    const statusCfg = STATUS_CONFIG[item.status] || STATUS_CONFIG.pending;
    const progress = item.totalTiles > 0 ? item.downloadedTiles / item.totalTiles : 0;

    return (
      <View style={styles.regionCard}>
        <View style={styles.regionHeader}>
          <View style={{ flex: 1 }}>
            <Text style={styles.regionName}>{item.name}</Text>
            <Text style={styles.regionMeta}>
              {formatDate(item.createdAt)} {'\u00B7'} {formatBytes(item.sizeBytes)}
            </Text>
          </View>
          <View style={[styles.statusBadge, { backgroundColor: statusCfg.color + '15' }]}>
            <Ionicons name={statusCfg.icon as any} size={14} color={statusCfg.color} />
            <Text style={[styles.statusText, { color: statusCfg.color }]}>{statusCfg.label}</Text>
          </View>
        </View>

        {/* Progress bar */}
        {item.status === 'downloading' && (
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
          </View>
        )}

        <View style={styles.regionFooter}>
          <Text style={styles.tileCount}>
            {item.downloadedTiles}/{item.totalTiles} tiles {'\u00B7'} Z{item.minZoom}-{item.maxZoom}
          </Text>
          <Pressable onPress={() => handleDelete(item.id)} hitSlop={8}>
            <Ionicons name="trash-outline" size={18} color={palette.textMuted} />
          </Pressable>
        </View>
      </View>
    );
  }, [handleDelete]);

  if (loading) {
    return (
      <View style={[styles.container, styles.center]}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Storage summary */}
      <View style={styles.summaryCard}>
        <View style={styles.summaryRow}>
          <View style={styles.summaryIcon}>
            <Ionicons name="cloud-offline" size={22} color={palette.accent} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.summaryTitle}>Offline Storage</Text>
            <Text style={styles.summaryValue}>
              {regions.length} region{regions.length !== 1 ? 's' : ''} {'\u00B7'} {formatBytes(totalSize)}
            </Text>
          </View>
        </View>

        {/* Quick estimate */}
        {estimate && (
          <View style={styles.estimateRow}>
            <Text style={styles.estimateText}>
              Current area: ~{estimate.totalTiles.toLocaleString()} tiles ({estimate.estimatedSizeMB.toFixed(1)} MB)
            </Text>
          </View>
        )}

        <View style={styles.actionRow}>
          <Pressable
            style={styles.estimateBtn}
            onPress={handleEstimate}
            disabled={estimating}
          >
            {estimating ? (
              <ActivityIndicator size="small" color={palette.accent} />
            ) : (
              <>
                <Ionicons name="calculator-outline" size={16} color={palette.accent} />
                <Text style={styles.estimateBtnText}>Estimate Size</Text>
              </>
            )}
          </Pressable>
          <Pressable style={styles.downloadBtn} onPress={handleDownloadHere}>
            <Ionicons name="download-outline" size={16} color="#FFFFFF" />
            <Text style={styles.downloadBtnText}>Download Here</Text>
          </Pressable>
        </View>
      </View>

      {/* Region list */}
      {regions.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="map-outline" size={48} color={palette.border} />
          <Text style={styles.emptyTitle}>No Offline Regions</Text>
          <Text style={styles.emptyText}>
            Download map tiles for your favorite fishing areas to use without internet.
          </Text>
        </View>
      ) : (
        <FlatList
          data={regions}
          keyExtractor={(item) => item.id}
          renderItem={renderRegion}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  center: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  summaryCard: {
    margin: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  summaryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  summaryIcon: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: palette.accent + '14',
    alignItems: 'center',
    justifyContent: 'center',
  },
  summaryTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 18,
    color: palette.text,
  },
  summaryValue: {
    fontSize: 13,
    color: palette.textMuted,
    marginTop: 2,
  },
  estimateRow: {
    backgroundColor: palette.background,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  estimateText: {
    fontSize: 13,
    color: palette.textSecondary,
  },
  actionRow: {
    flexDirection: 'row',
    gap: 10,
  },
  estimateBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.accent + '12',
  },
  estimateBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: palette.accent,
  },
  downloadBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.accent,
  },
  downloadBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  list: {
    paddingHorizontal: 16,
    paddingBottom: 40,
  },
  regionCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    gap: 8,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  regionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  regionName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  regionMeta: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  statusText: {
    fontSize: 11,
    fontWeight: '700',
  },
  progressBar: {
    height: 4,
    backgroundColor: palette.border,
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: palette.accent,
    borderRadius: 2,
  },
  regionFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  tileCount: {
    fontSize: 12,
    color: palette.textDim,
  },
  empty: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
    gap: 10,
  },
  emptyTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 20,
    color: palette.text,
  },
  emptyText: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    lineHeight: 20,
  },
});
