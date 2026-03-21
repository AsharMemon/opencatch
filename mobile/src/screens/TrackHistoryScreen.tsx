/**
 * OpenCatch — Track History Screen
 *
 * Browse, sort, view, share, and delete saved GPS tracks.
 * - List all saved tracks with date, distance, duration, location
 * - Tap to view track on a full-screen map with speed coloring
 * - Share track as GPX file
 * - Swipe-to-delete tracks
 * - Sort by date, distance, or duration
 * - Track statistics summary at top
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Dimensions,
  FlatList,
  Modal,
  PanResponder,
  Platform,
  Pressable,
  Share,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles, fonts } from '../theme/typography';
import {
  trackRecorder,
  buildSpeedColoredGeoJSON,
  speedToColor,
  type FishingTrack,
  type TrackStats,
} from '../services/trackRecorder';

// MapLibre — optional (only works on native)
let MLMapView: any = null;
let Camera: any = null;
let ShapeSource: any = null;
let LineLayer: any = null;
let PointAnnotation: any = null;

try {
  const maplibre = require('@maplibre/maplibre-react-native');
  MLMapView = maplibre.MapView;
  Camera = maplibre.Camera;
  ShapeSource = maplibre.ShapeSource;
  LineLayer = maplibre.LineLayer;
  PointAnnotation = maplibre.PointAnnotation;
} catch {
  // Web fallback
}

const SCREEN_WIDTH = Dimensions.get('window').width;
const SWIPE_THRESHOLD = -80;

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatDistance(miles: number): string {
  if (miles < 0.1) return `${Math.round(miles * 5280)} ft`;
  return `${miles.toFixed(2)} mi`;
}

function formatSpeed(mph: number): string {
  return `${mph.toFixed(1)} mph`;
}

function formatDate(timestamp: number): string {
  return new Date(timestamp).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

type SortKey = 'date' | 'distance' | 'duration';

// ── Swipeable Track Row ──────────────────────────────────────────────────────

function SwipeableTrackCard({
  track,
  onPress,
  onExport,
  onDelete,
}: {
  track: FishingTrack;
  onPress: () => void;
  onExport: () => void;
  onDelete: () => void;
}) {
  const translateX = useRef(new Animated.Value(0)).current;
  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dx) > 10 && Math.abs(g.dx) > Math.abs(g.dy),
      onPanResponderMove: (_, g) => {
        if (g.dx < 0) {
          translateX.setValue(Math.max(g.dx, -120));
        }
      },
      onPanResponderRelease: (_, g) => {
        if (g.dx < SWIPE_THRESHOLD) {
          Animated.spring(translateX, { toValue: -100, useNativeDriver: true }).start();
        } else {
          Animated.spring(translateX, { toValue: 0, useNativeDriver: true }).start();
        }
      },
    }),
  ).current;

  const waypointCount = track.waypoints?.length ?? 0;
  const catchCount = (track.waypoints ?? []).filter((w) => w.type === 'catch').length;

  return (
    <View style={cardStyles.wrapper}>
      {/* Delete action behind */}
      <View style={cardStyles.deleteAction}>
        <Pressable style={cardStyles.deleteBtn} onPress={onDelete}>
          <Ionicons name="trash" size={22} color="#FFFFFF" />
          <Text style={cardStyles.deleteBtnText}>Delete</Text>
        </Pressable>
      </View>

      {/* Swipeable card */}
      <Animated.View
        style={[cardStyles.card, { transform: [{ translateX }] }]}
        {...panResponder.panHandlers}
      >
        <Pressable onPress={onPress} style={cardStyles.cardContent}>
          <View style={cardStyles.header}>
            <View style={{ flex: 1 }}>
              <Text style={cardStyles.name} numberOfLines={1}>{track.name}</Text>
              <Text style={cardStyles.date}>
                {formatDate(track.startTime)} at {formatTime(track.startTime)}
              </Text>
            </View>
            <Pressable onPress={onExport} style={cardStyles.shareBtn} hitSlop={8}>
              <Ionicons name="share-outline" size={18} color={palette.accent} />
            </Pressable>
          </View>

          <View style={cardStyles.statsRow}>
            <View style={cardStyles.statItem}>
              <Ionicons name="navigate-outline" size={13} color={palette.textMuted} />
              <Text style={cardStyles.statText}>{formatDistance(track.distanceMiles)}</Text>
            </View>
            <View style={cardStyles.statItem}>
              <Ionicons name="time-outline" size={13} color={palette.textMuted} />
              <Text style={cardStyles.statText}>{formatDuration(track.durationMinutes)}</Text>
            </View>
            <View style={cardStyles.statItem}>
              <Ionicons name="speedometer-outline" size={13} color={palette.textMuted} />
              <Text style={cardStyles.statText}>{formatSpeed(track.maxSpeedMph)} max</Text>
            </View>
            <View style={cardStyles.statItem}>
              <Ionicons name="location-outline" size={13} color={palette.textMuted} />
              <Text style={cardStyles.statText}>{track.points.length} pts</Text>
            </View>
          </View>

          {/* Waypoint/catch badges */}
          {waypointCount > 0 && (
            <View style={cardStyles.badgeRow}>
              {catchCount > 0 && (
                <View style={[cardStyles.badge, { backgroundColor: palette.success + '18' }]}>
                  <Ionicons name="fish" size={11} color={palette.success} />
                  <Text style={[cardStyles.badgeText, { color: palette.success }]}>
                    {catchCount} catch{catchCount !== 1 ? 'es' : ''}
                  </Text>
                </View>
              )}
              {waypointCount - catchCount > 0 && (
                <View style={[cardStyles.badge, { backgroundColor: palette.accentDim }]}>
                  <Ionicons name="flag" size={11} color={palette.accent} />
                  <Text style={[cardStyles.badgeText, { color: palette.accent }]}>
                    {waypointCount - catchCount} waypoint{waypointCount - catchCount !== 1 ? 's' : ''}
                  </Text>
                </View>
              )}
            </View>
          )}

          {track.notes ? (
            <Text style={cardStyles.notes} numberOfLines={2}>{track.notes}</Text>
          ) : null}

          <View style={cardStyles.viewRow}>
            <Text style={cardStyles.viewText}>View on map</Text>
            <Ionicons name="chevron-forward" size={14} color={palette.accent} />
          </View>
        </Pressable>
      </Animated.View>
    </View>
  );
}

const cardStyles = StyleSheet.create({
  wrapper: {
    marginBottom: 10,
    overflow: 'hidden',
    borderRadius: 14,
  },
  deleteAction: {
    position: 'absolute',
    right: 0,
    top: 0,
    bottom: 0,
    width: 100,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: palette.error,
    borderRadius: 14,
  },
  deleteBtn: {
    alignItems: 'center',
    gap: 4,
  },
  deleteBtnText: {
    fontSize: 11,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  cardContent: {
    padding: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 10,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  date: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  shareBtn: {
    padding: 6,
  },
  statsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 6,
  },
  statItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  statText: {
    fontSize: 13,
    color: palette.textSecondary,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 8,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
  },
  notes: {
    fontSize: 13,
    color: palette.textMuted,
    fontStyle: 'italic',
    marginTop: 8,
  },
  viewRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: 4,
    marginTop: 8,
  },
  viewText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.accent,
  },
});

// ── Track Detail Map Modal ───────────────────────────────────────────────────

function TrackDetailMap({
  track,
  visible,
  onClose,
}: {
  track: FishingTrack | null;
  visible: boolean;
  onClose: () => void;
}) {
  if (!track || !MLMapView) return null;

  const center = track.points.length > 0
    ? [track.points[Math.floor(track.points.length / 2)].lon, track.points[Math.floor(track.points.length / 2)].lat]
    : [-93.5, 45.0];

  // Compute bounds
  let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
  for (const p of track.points) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lon < minLon) minLon = p.lon;
    if (p.lon > maxLon) maxLon = p.lon;
  }

  const trackGeoJSON = buildSpeedColoredGeoJSON(track.points);

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="fullScreen">
      <View style={detailStyles.container}>
        {/* Header */}
        <View style={detailStyles.header}>
          <Pressable onPress={onClose} style={detailStyles.closeBtn}>
            <Ionicons name="arrow-back" size={24} color={palette.text} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={detailStyles.title} numberOfLines={1}>{track.name}</Text>
            <Text style={detailStyles.subtitle}>
              {formatDate(track.startTime)} at {formatTime(track.startTime)}
            </Text>
          </View>
        </View>

        {/* Map */}
        <MLMapView
          style={detailStyles.map}
          logoEnabled={false}
          attributionEnabled={false}
        >
          <Camera
            bounds={{
              ne: [maxLon + 0.01, maxLat + 0.01],
              sw: [minLon - 0.01, minLat - 0.01],
            }}
            padding={{ paddingTop: 60, paddingBottom: 60, paddingLeft: 40, paddingRight: 40 }}
            animationDuration={0}
          />

          {/* Speed-colored track */}
          {track.points.length >= 2 && ShapeSource && LineLayer && (
            <ShapeSource id="detail-track-source" shape={trackGeoJSON}>
              <LineLayer
                id="detail-track-layer"
                style={{
                  lineColor: ['get', 'color'],
                  lineWidth: 4,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </ShapeSource>
          )}

          {/* Start marker */}
          {track.points.length > 0 && PointAnnotation && (
            <PointAnnotation
              id="track-start"
              coordinate={[track.points[0].lon, track.points[0].lat]}
            >
              <View style={detailStyles.startPin}>
                <Ionicons name="flag" size={12} color="#FFFFFF" />
              </View>
            </PointAnnotation>
          )}

          {/* End marker */}
          {track.points.length > 1 && PointAnnotation && (
            <PointAnnotation
              id="track-end"
              coordinate={[track.points[track.points.length - 1].lon, track.points[track.points.length - 1].lat]}
            >
              <View style={detailStyles.endPin}>
                <Ionicons name="stop" size={12} color="#FFFFFF" />
              </View>
            </PointAnnotation>
          )}

          {/* Waypoints */}
          {PointAnnotation && (track.waypoints ?? []).map((wp) => (
            <PointAnnotation
              key={wp.id}
              id={wp.id}
              coordinate={[wp.lon, wp.lat]}
            >
              <View style={[
                detailStyles.waypointPin,
                { backgroundColor: wp.type === 'catch' ? palette.success : palette.accent },
              ]}>
                <Ionicons
                  name={wp.type === 'catch' ? 'fish' : 'flag'}
                  size={12}
                  color="#FFFFFF"
                />
              </View>
            </PointAnnotation>
          ))}
        </MLMapView>

        {/* Stats bar at bottom */}
        <View style={detailStyles.statsBar}>
          <View style={detailStyles.statCol}>
            <Text style={detailStyles.statValue}>{formatDistance(track.distanceMiles)}</Text>
            <Text style={detailStyles.statLabel}>Distance</Text>
          </View>
          <View style={detailStyles.statDivider} />
          <View style={detailStyles.statCol}>
            <Text style={detailStyles.statValue}>{formatDuration(track.durationMinutes)}</Text>
            <Text style={detailStyles.statLabel}>Duration</Text>
          </View>
          <View style={detailStyles.statDivider} />
          <View style={detailStyles.statCol}>
            <Text style={detailStyles.statValue}>{formatSpeed(track.avgSpeedMph ?? 0)}</Text>
            <Text style={detailStyles.statLabel}>Avg Speed</Text>
          </View>
          <View style={detailStyles.statDivider} />
          <View style={detailStyles.statCol}>
            <Text style={detailStyles.statValue}>{formatSpeed(track.maxSpeedMph)}</Text>
            <Text style={detailStyles.statLabel}>Max Speed</Text>
          </View>
        </View>

        {/* Speed legend */}
        <View style={detailStyles.legend}>
          <View style={[detailStyles.legendDot, { backgroundColor: speedToColor(0) }]} />
          <Text style={detailStyles.legendLabel}>Slow</Text>
          <View style={[detailStyles.legendDot, { backgroundColor: speedToColor(15) }]} />
          <Text style={detailStyles.legendLabel}>Med</Text>
          <View style={[detailStyles.legendDot, { backgroundColor: speedToColor(30) }]} />
          <Text style={detailStyles.legendLabel}>Fast</Text>
        </View>
      </View>
    </Modal>
  );
}

const detailStyles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingTop: Platform.OS === 'ios' ? 56 : 16,
    paddingHorizontal: 16,
    paddingBottom: 12,
    backgroundColor: palette.background,
    gap: 12,
  },
  closeBtn: {
    padding: 4,
  },
  title: {
    fontSize: 17,
    fontWeight: '700',
    color: palette.text,
  },
  subtitle: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  map: {
    flex: 1,
  },
  startPin: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: palette.success,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  endPin: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: palette.error,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  waypointPin: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  statsBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    backgroundColor: palette.surface,
    paddingVertical: 14,
    paddingHorizontal: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
  },
  statCol: {
    alignItems: 'center',
    flex: 1,
  },
  statValue: {
    fontSize: 15,
    fontWeight: '700',
    color: palette.text,
  },
  statLabel: {
    fontSize: 10,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 2,
  },
  statDivider: {
    width: StyleSheet.hairlineWidth,
    height: 30,
    backgroundColor: palette.border,
  },
  legend: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 8,
    paddingBottom: Platform.OS === 'ios' ? 28 : 12,
    backgroundColor: palette.surface,
  },
  legendDot: {
    width: 16,
    height: 4,
    borderRadius: 2,
  },
  legendLabel: {
    fontSize: 11,
    color: palette.textMuted,
    marginRight: 8,
  },
});

// ── Main Component ───────────────────────────────────────────────────────────

export function TrackHistoryScreen({ navigation }: any) {
  const [tracks, setTracks] = useState<FishingTrack[]>([]);
  const [stats, setStats] = useState<TrackStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>('date');
  const [viewingTrack, setViewingTrack] = useState<FishingTrack | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    const saved = await trackRecorder.getSavedTracks();
    const s = await trackRecorder.getStats();
    setTracks(saved);
    setStats(s);
    setLoading(false);
  };

  const sortedTracks = useMemo(() => {
    const copy = [...tracks];
    switch (sortKey) {
      case 'date':
        return copy.sort((a, b) => b.startTime - a.startTime);
      case 'distance':
        return copy.sort((a, b) => b.distanceMiles - a.distanceMiles);
      case 'duration':
        return copy.sort((a, b) => b.durationMinutes - a.durationMinutes);
      default:
        return copy;
    }
  }, [tracks, sortKey]);

  const handleExport = async (track: FishingTrack) => {
    try {
      const gpx = trackRecorder.exportGPX(track);
      await Share.share({
        message: gpx,
        title: `${track.name}.gpx`,
      });
    } catch {
      Alert.alert('Export Failed', 'Could not export track as GPX.');
    }
  };

  const handleDelete = (track: FishingTrack) => {
    Alert.alert(
      'Delete Track',
      `Delete "${track.name}"? This cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await trackRecorder.deleteTrack(track.id);
            setTracks((prev) => prev.filter((t) => t.id !== track.id));
            const s = await trackRecorder.getStats();
            setStats(s);
          },
        },
      ],
    );
  };

  const renderItem = useCallback(
    ({ item }: { item: FishingTrack }) => (
      <SwipeableTrackCard
        track={item}
        onPress={() => setViewingTrack(item)}
        onExport={() => handleExport(item)}
        onDelete={() => handleDelete(item)}
      />
    ),
    [],
  );

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Summary stats */}
      {stats && stats.totalTracks > 0 && (
        <View style={styles.summaryBar}>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryValue}>{stats.totalTracks}</Text>
            <Text style={styles.summaryLabel}>Trips</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryValue}>{stats.totalDistanceMiles.toFixed(1)} mi</Text>
            <Text style={styles.summaryLabel}>Total</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryValue}>{stats.totalDurationHours.toFixed(1)} hr</Text>
            <Text style={styles.summaryLabel}>Time</Text>
          </View>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryValue}>{stats.longestTrackMiles.toFixed(1)} mi</Text>
            <Text style={styles.summaryLabel}>Longest</Text>
          </View>
        </View>
      )}

      {/* Sort controls */}
      <View style={styles.sortRow}>
        <Text style={styles.sortTitle}>Sort by</Text>
        {(['date', 'distance', 'duration'] as SortKey[]).map((key) => (
          <Pressable
            key={key}
            style={[styles.sortChip, sortKey === key && styles.sortChipActive]}
            onPress={() => setSortKey(key)}
          >
            <Text style={[styles.sortChipText, sortKey === key && styles.sortChipTextActive]}>
              {key.charAt(0).toUpperCase() + key.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Track list */}
      {sortedTracks.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="trail-sign-outline" size={48} color={palette.textDim} />
          <Text style={styles.emptyTitle}>No Tracks Yet</Text>
          <Text style={styles.emptySubtitle}>Start recording a trip to see it here</Text>
          <Pressable
            style={styles.emptyBtn}
            onPress={() => navigation.navigate('TrackRecording')}
          >
            <Ionicons name="navigate" size={16} color="#FFFFFF" />
            <Text style={styles.emptyBtnText}>Start a Trip</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={sortedTracks}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
        />
      )}

      {/* Track detail map modal */}
      <TrackDetailMap
        track={viewingTrack}
        visible={viewingTrack !== null}
        onClose={() => setViewingTrack(null)}
      />
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
  },
  listContent: {
    padding: 16,
    paddingBottom: 40,
  },

  // Summary
  summaryBar: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    backgroundColor: palette.surface,
    paddingVertical: 14,
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  summaryItem: {
    alignItems: 'center',
  },
  summaryValue: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  summaryLabel: {
    fontSize: 10,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 2,
  },

  // Sort
  sortRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 8,
  },
  sortTitle: {
    fontSize: 13,
    color: palette.textMuted,
    fontWeight: '500',
    marginRight: 4,
  },
  sortChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: palette.surfaceRaised,
  },
  sortChipActive: {
    backgroundColor: palette.accentLight,
  },
  sortChipText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textMuted,
  },
  sortChipTextActive: {
    color: palette.accent,
  },

  // Empty state
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
  emptyBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 10,
    marginTop: 16,
  },
  emptyBtnText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
  },
});
