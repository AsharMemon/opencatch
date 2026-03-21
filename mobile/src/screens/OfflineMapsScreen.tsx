/**
 * OpenCatch — Offline Maps Management Screen
 *
 * Allows users to download map regions for offline use, manage
 * downloaded packs, and see storage usage.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  Pressable,
  TextInput,
  Alert,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  offlineCharts,
  type OfflineRegion,
  type DownloadProgress,
} from '../services/offlineCharts';

export function OfflineMapsScreen() {
  const [regions, setRegions] = useState<OfflineRegion[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalSize, setTotalSize] = useState({ regions: 0, sizeMB: 0 });
  const [showNewRegion, setShowNewRegion] = useState(false);
  const [newRegionName, setNewRegionName] = useState('');
  const [progressMap, setProgressMap] = useState<Record<string, DownloadProgress>>({});

  const loadRegions = useCallback(async () => {
    try {
      const [regs, size] = await Promise.all([
        offlineCharts.getRegions(),
        offlineCharts.getTotalSize(),
      ]);
      setRegions(regs);
      setTotalSize(size);
    } catch (err) {
      console.warn('Failed to load offline regions:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRegions();
  }, [loadRegions]);

  // Subscribe to download progress
  useEffect(() => {
    const unsubscribers: (() => void)[] = [];
    for (const region of regions) {
      if (region.status === 'downloading') {
        const unsub = offlineCharts.onProgress(region.id, (progress) => {
          setProgressMap((prev) => ({ ...prev, [region.id]: progress }));
          if (progress.progress >= 1) {
            loadRegions();
          }
        });
        unsubscribers.push(unsub);
      }
    }
    return () => unsubscribers.forEach((u) => u());
  }, [regions, loadRegions]);

  const handleDelete = useCallback(
    (region: OfflineRegion) => {
      Alert.alert(
        'Delete Offline Map',
        `Delete "${region.name}"? This will free up ${Math.round(region.sizeBytes / (1024 * 1024))} MB.`,
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Delete',
            style: 'destructive',
            onPress: async () => {
              await offlineCharts.deleteRegion(region.id);
              loadRegions();
            },
          },
        ],
      );
    },
    [loadRegions],
  );

  const handlePause = useCallback(
    async (regionId: string) => {
      await offlineCharts.pauseDownload(regionId);
      loadRegions();
    },
    [loadRegions],
  );

  const handleResume = useCallback(
    async (regionId: string) => {
      await offlineCharts.resumeDownload(regionId);
      loadRegions();
    },
    [loadRegions],
  );

  const handleCreateSample = useCallback(async () => {
    const name = newRegionName.trim() || 'My Area';
    try {
      // Default: roughly a 50x50 mile area around center of US
      // In production, user would pick area on map
      await offlineCharts.createRegion(name, {
        north: 45.1,
        south: 44.8,
        east: -93.1,
        west: -93.5,
      }, 6, 13);
      setShowNewRegion(false);
      setNewRegionName('');
      loadRegions();
    } catch (err: any) {
      Alert.alert('Error', err.message);
    }
  }, [newRegionName, loadRegions]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} showsVerticalScrollIndicator={false}>
      {/* Header */}
      <View style={styles.header}>
        <Ionicons name="cloud-offline-outline" size={28} color={palette.accent} />
        <View style={{ flex: 1, marginLeft: 12 }}>
          <Text style={styles.headerTitle}>Offline Maps</Text>
          <Text style={styles.headerSubtitle}>
            Download map areas for use without internet
          </Text>
        </View>
      </View>

      {/* Storage summary */}
      <View style={styles.storageCard}>
        <View style={styles.storageStat}>
          <Text style={styles.storageValue}>{totalSize.regions}</Text>
          <Text style={styles.storageLabel}>Regions</Text>
        </View>
        <View style={styles.storageDivider} />
        <View style={styles.storageStat}>
          <Text style={styles.storageValue}>{totalSize.sizeMB}</Text>
          <Text style={styles.storageLabel}>MB Used</Text>
        </View>
        <View style={styles.storageDivider} />
        <View style={styles.storageStat}>
          <Text style={styles.storageValue}>
            {regions.filter((r) => r.status === 'complete').length}
          </Text>
          <Text style={styles.storageLabel}>Ready</Text>
        </View>
      </View>

      {/* New Region Button */}
      {!showNewRegion ? (
        <Pressable
          style={styles.addButton}
          onPress={() => setShowNewRegion(true)}
        >
          <Ionicons name="add-circle-outline" size={20} color={palette.accent} />
          <Text style={styles.addButtonText}>Download New Area</Text>
        </Pressable>
      ) : (
        <View style={styles.newRegionCard}>
          <Text style={styles.newRegionTitle}>New Offline Area</Text>
          <Text style={styles.newRegionHint}>
            Name this area. In a future update, you'll be able to select the
            exact area on the map.
          </Text>
          <TextInput
            style={styles.newRegionInput}
            placeholder="Area name (e.g., Lake Minnetonka)"
            placeholderTextColor={palette.textSecondary}
            value={newRegionName}
            onChangeText={setNewRegionName}
          />
          <View style={styles.newRegionActions}>
            <Pressable
              style={styles.cancelButton}
              onPress={() => { setShowNewRegion(false); setNewRegionName(''); }}
            >
              <Text style={styles.cancelButtonText}>Cancel</Text>
            </Pressable>
            <Pressable style={styles.downloadButton} onPress={handleCreateSample}>
              <Ionicons name="download-outline" size={18} color="#fff" />
              <Text style={styles.downloadButtonText}>Download</Text>
            </Pressable>
          </View>
        </View>
      )}

      {/* Regions List */}
      <Text style={styles.sectionTitle}>Downloaded Areas</Text>
      {regions.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="map-outline" size={48} color={palette.border} />
          <Text style={styles.emptyText}>No offline maps yet</Text>
          <Text style={styles.emptySubtext}>
            Download map areas to use when fishing in areas with no cell service
          </Text>
        </View>
      ) : (
        regions.map((region) => {
          const progress = progressMap[region.id];
          return (
            <View key={region.id} style={styles.regionCard}>
              <View style={styles.regionHeader}>
                <Ionicons
                  name={
                    region.status === 'complete'
                      ? 'checkmark-circle'
                      : region.status === 'downloading'
                        ? 'cloud-download-outline'
                        : region.status === 'error'
                          ? 'alert-circle-outline'
                          : region.status === 'paused'
                            ? 'pause-circle-outline'
                            : 'time-outline'
                  }
                  size={22}
                  color={
                    region.status === 'complete'
                      ? '#4CAF50'
                      : region.status === 'error'
                        ? '#F44336'
                        : palette.accent
                  }
                />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.regionName}>{region.name}</Text>
                  <Text style={styles.regionMeta}>
                    Zoom {region.minZoom}-{region.maxZoom} · {region.totalTiles.toLocaleString()} tiles
                    · {Math.round(region.sizeBytes / (1024 * 1024))} MB
                  </Text>
                </View>
                <Pressable
                  style={styles.regionAction}
                  onPress={() => handleDelete(region)}
                >
                  <Ionicons name="trash-outline" size={18} color="#F44336" />
                </Pressable>
              </View>

              {/* Progress bar for downloading/paused */}
              {(region.status === 'downloading' || region.status === 'paused') && (
                <View style={styles.progressSection}>
                  <View style={styles.progressBar}>
                    <View
                      style={[
                        styles.progressFill,
                        {
                          width: `${(progress?.progress ?? region.downloadedTiles / region.totalTiles) * 100}%`,
                        },
                      ]}
                    />
                  </View>
                  <View style={styles.progressInfo}>
                    <Text style={styles.progressText}>
                      {region.downloadedTiles.toLocaleString()} / {region.totalTiles.toLocaleString()} tiles
                    </Text>
                    {progress && progress.estimatedSecondsLeft > 0 && (
                      <Text style={styles.progressText}>
                        ~{Math.ceil(progress.estimatedSecondsLeft / 60)} min left
                      </Text>
                    )}
                  </View>
                  <Pressable
                    style={styles.pauseResumeButton}
                    onPress={() =>
                      region.status === 'downloading'
                        ? handlePause(region.id)
                        : handleResume(region.id)
                    }
                  >
                    <Ionicons
                      name={region.status === 'downloading' ? 'pause' : 'play'}
                      size={16}
                      color={palette.accent}
                    />
                    <Text style={styles.pauseResumeText}>
                      {region.status === 'downloading' ? 'Pause' : 'Resume'}
                    </Text>
                  </Pressable>
                </View>
              )}

              {/* Error message */}
              {region.status === 'error' && region.errorMessage && (
                <View style={styles.errorSection}>
                  <Text style={styles.errorText}>{region.errorMessage}</Text>
                  <Pressable
                    style={styles.retryButton}
                    onPress={() => handleResume(region.id)}
                  >
                    <Text style={styles.retryButtonText}>Retry</Text>
                  </Pressable>
                </View>
              )}

              {/* Completed info */}
              {region.status === 'complete' && (
                <Text style={styles.completedText}>
                  Updated {new Date(region.lastUpdated).toLocaleDateString()}
                </Text>
              )}
            </View>
          );
        })
      )}

      {/* Tips */}
      <View style={styles.tipsCard}>
        <Text style={styles.tipsTitle}>Tips</Text>
        <View style={styles.tipRow}>
          <Ionicons name="information-circle-outline" size={16} color={palette.textSecondary} />
          <Text style={styles.tipText}>
            Higher zoom levels show more detail but require more storage
          </Text>
        </View>
        <View style={styles.tipRow}>
          <Ionicons name="information-circle-outline" size={16} color={palette.textSecondary} />
          <Text style={styles.tipText}>
            Download on Wi-Fi to save cellular data
          </Text>
        </View>
        <View style={styles.tipRow}>
          <Ionicons name="information-circle-outline" size={16} color={palette.textSecondary} />
          <Text style={styles.tipText}>
            Maps are cached locally and work without internet
          </Text>
        </View>
      </View>

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

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
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
  },
  headerSubtitle: {
    fontSize: 13,
    color: palette.textSecondary,
    marginTop: 2,
  },
  storageCard: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginTop: 16,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.border,
  },
  storageStat: {
    flex: 1,
    alignItems: 'center',
  },
  storageValue: {
    fontSize: 24,
    fontWeight: '700',
    color: palette.text,
  },
  storageLabel: {
    fontSize: 12,
    color: palette.textSecondary,
    marginTop: 2,
  },
  storageDivider: {
    width: 1,
    backgroundColor: palette.border,
  },
  addButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginTop: 12,
    paddingVertical: 14,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: palette.accent,
    borderStyle: 'dashed',
    gap: 8,
  },
  addButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.accent,
  },
  newRegionCard: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: palette.accent,
  },
  newRegionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 6,
  },
  newRegionHint: {
    fontSize: 13,
    color: palette.textSecondary,
    marginBottom: 12,
    lineHeight: 18,
  },
  newRegionInput: {
    backgroundColor: '#F5F5F5',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: palette.text,
    marginBottom: 12,
  },
  newRegionActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 10,
  },
  cancelButton: {
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  cancelButtonText: {
    fontSize: 14,
    color: palette.textSecondary,
  },
  downloadButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.accent,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
    gap: 6,
  },
  downloadButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#fff',
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    paddingHorizontal: 16,
    marginTop: 20,
    marginBottom: 10,
  },
  emptyState: {
    alignItems: 'center',
    padding: 32,
  },
  emptyText: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
    marginTop: 12,
  },
  emptySubtext: {
    fontSize: 13,
    color: palette.textSecondary,
    textAlign: 'center',
    marginTop: 4,
    lineHeight: 18,
  },
  regionCard: {
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginBottom: 10,
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: palette.border,
  },
  regionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  regionName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  regionMeta: {
    fontSize: 12,
    color: palette.textSecondary,
    marginTop: 2,
  },
  regionAction: {
    padding: 8,
  },
  progressSection: {
    marginTop: 10,
  },
  progressBar: {
    height: 6,
    backgroundColor: '#E0E0E0',
    borderRadius: 3,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: palette.accent,
    borderRadius: 3,
  },
  progressInfo: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 4,
  },
  progressText: {
    fontSize: 11,
    color: palette.textSecondary,
  },
  pauseResumeButton: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    marginTop: 6,
    gap: 4,
  },
  pauseResumeText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.accent,
  },
  errorSection: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  errorText: {
    fontSize: 12,
    color: '#F44336',
    flex: 1,
  },
  retryButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
    backgroundColor: '#FFF3E0',
  },
  retryButtonText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#E65100',
  },
  completedText: {
    fontSize: 12,
    color: '#4CAF50',
    marginTop: 6,
  },
  tipsCard: {
    backgroundColor: '#F5F7F0',
    marginHorizontal: 16,
    marginTop: 16,
    borderRadius: 12,
    padding: 16,
  },
  tipsTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 8,
  },
  tipRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginBottom: 6,
  },
  tipText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
  },
});
