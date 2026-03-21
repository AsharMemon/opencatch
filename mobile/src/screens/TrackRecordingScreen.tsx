/**
 * OpenCatch — Track Recording Screen
 *
 * Full-screen trip recording UI with:
 * - Live GPS track stats (distance, duration, speed)
 * - Pause / resume / stop controls
 * - Track history with GPX export
 * - Track detail view with mini map preview
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  FlatList,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  trackRecorder,
  type FishingTrack,
  type TrackStats,
} from '../services/trackRecorder';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  const s = Math.floor((minutes * 60) % 60);
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatDistance(miles: number): string {
  if (miles < 0.1) return `${Math.round(miles * 5280)} ft`;
  return `${miles.toFixed(2)} mi`;
}

function formatSpeed(mph: number): string {
  return `${mph.toFixed(1)} mph`;
}

function formatDate(timestamp: number): string {
  const d = new Date(timestamp);
  return d.toLocaleDateString('en-US', {
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

// ── Component ────────────────────────────────────────────────────────────────

export function TrackRecordingScreen({ navigation }: any) {
  const recorder = trackRecorder;

  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTrack, setActiveTrack] = useState<FishingTrack | null>(null);
  const [savedTracks, setSavedTracks] = useState<FishingTrack[]>([]);
  const [stats, setStats] = useState<TrackStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const [trackName, setTrackName] = useState('');

  // Pulse animation for recording indicator
  const pulseAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (isRecording && !isPaused) {
      const pulse = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.3, duration: 800, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 800, useNativeDriver: true }),
        ]),
      );
      pulse.start();
      return () => pulse.stop();
    } else {
      pulseAnim.setValue(1);
    }
  }, [isRecording, isPaused]);

  // Load saved tracks on mount
  useEffect(() => {
    loadData();
  }, []);

  // Subscribe to recorder updates
  useEffect(() => {
    const unsub = recorder.subscribe((track: FishingTrack | null) => {
      setActiveTrack(track ? { ...track } : null);
    });
    return unsub;
  }, []);

  const loadData = async () => {
    setLoading(true);
    const tracks = await recorder.getSavedTracks();
    const s = await recorder.getStats();
    setSavedTracks(tracks);
    setStats(s);
    setLoading(false);
  };

  const handleStart = async () => {
    const name = trackName.trim() || `Trip ${new Date().toLocaleDateString()}`;
    await recorder.startRecording(name);
    setIsRecording(true);
    setIsPaused(false);
    setTrackName('');
  };

  const handlePause = async () => {
    await recorder.pauseRecording();
    setIsPaused(true);
  };

  const handleResume = async () => {
    await recorder.resumeRecording();
    setIsPaused(false);
  };

  const handleStop = async () => {
    Alert.alert(
      'End Trip',
      'Save this track to your history?',
      [
        {
          text: 'Discard',
          style: 'destructive',
          onPress: async () => {
            await recorder.discardRecording();
            setIsRecording(false);
            setIsPaused(false);
            setActiveTrack(null);
          },
        },
        {
          text: 'Save',
          onPress: async () => {
            const track = await recorder.stopRecording();
            setIsRecording(false);
            setIsPaused(false);
            setActiveTrack(null);
            if (track) {
              setSavedTracks((prev) => [track, ...prev]);
              const s = await recorder.getStats();
              setStats(s);
            }
          },
        },
      ],
    );
  };

  const handleExportGPX = async (track: FishingTrack) => {
    try {
      const gpx = recorder.exportGPX(track);
      await Share.share({
        message: gpx,
        title: `${track.name}.gpx`,
      });
    } catch {
      Alert.alert('Export Failed', 'Could not export track as GPX.');
    }
  };

  const handleDeleteTrack = (track: FishingTrack) => {
    Alert.alert(
      'Delete Track',
      `Delete "${track.name}"? This cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await recorder.deleteTrack(track.id);
            setSavedTracks((prev) => prev.filter((t) => t.id !== track.id));
            const s = await recorder.getStats();
            setStats(s);
          },
        },
      ],
    );
  };

  // ── Active Recording View ──────────────────────────────────────────────────

  const renderRecordingView = () => {
    const track = activeTrack;
    const distance = track?.distanceMiles ?? 0;
    const duration = track?.durationMinutes ?? 0;
    const maxSpeed = track?.maxSpeedMph ?? 0;
    const points = track?.points?.length ?? 0;
    const currentSpeed = track?.points?.length
      ? (track.points[track.points.length - 1].speed ?? 0) * 2.237 // m/s → mph
      : 0;

    return (
      <View style={styles.recordingView}>
        {/* Recording indicator */}
        <View style={styles.recordingHeader}>
          <Animated.View style={[styles.recordingDot, { transform: [{ scale: pulseAnim }] }]}>
            <View style={[styles.recordingDotInner, isPaused && styles.recordingDotPaused]} />
          </Animated.View>
          <Text style={styles.recordingLabel}>
            {isPaused ? 'PAUSED' : 'RECORDING'}
          </Text>
          <Text style={styles.recordingPoints}>{points} pts</Text>
        </View>

        {/* Main stat — duration */}
        <Text style={styles.bigDuration}>{formatDuration(duration)}</Text>

        {/* Stat grid */}
        <View style={styles.statGrid}>
          <View style={styles.statCell}>
            <Ionicons name="navigate-outline" size={20} color={palette.accent} />
            <Text style={styles.statValue}>{formatDistance(distance)}</Text>
            <Text style={styles.statLabel}>Distance</Text>
          </View>
          <View style={styles.statCell}>
            <Ionicons name="speedometer-outline" size={20} color={palette.accent} />
            <Text style={styles.statValue}>{formatSpeed(currentSpeed)}</Text>
            <Text style={styles.statLabel}>Current Speed</Text>
          </View>
          <View style={styles.statCell}>
            <Ionicons name="flash-outline" size={20} color={palette.warning} />
            <Text style={styles.statValue}>{formatSpeed(maxSpeed)}</Text>
            <Text style={styles.statLabel}>Max Speed</Text>
          </View>
        </View>

        {/* Controls */}
        <View style={styles.controlRow}>
          {isPaused ? (
            <Pressable style={[styles.controlBtn, styles.resumeBtn]} onPress={handleResume}>
              <Ionicons name="play" size={28} color="#FFF" />
              <Text style={styles.controlBtnText}>Resume</Text>
            </Pressable>
          ) : (
            <Pressable style={[styles.controlBtn, styles.pauseBtn]} onPress={handlePause}>
              <Ionicons name="pause" size={28} color="#FFF" />
              <Text style={styles.controlBtnText}>Pause</Text>
            </Pressable>
          )}
          <Pressable style={[styles.controlBtn, styles.stopBtn]} onPress={handleStop}>
            <Ionicons name="stop" size={28} color="#FFF" />
            <Text style={styles.controlBtnText}>End Trip</Text>
          </Pressable>
        </View>
      </View>
    );
  };

  // ── Start Trip View ────────────────────────────────────────────────────────

  const renderStartView = () => (
    <View style={styles.startView}>
      {/* Trip name input */}
      <View style={styles.nameInputRow}>
        <Ionicons name="create-outline" size={20} color={palette.textMuted} />
        <TextInput
          style={styles.nameInput}
          placeholder="Trip name (optional)"
          placeholderTextColor={palette.textDim}
          value={trackName}
          onChangeText={setTrackName}
        />
      </View>

      {/* Big start button */}
      <Pressable style={styles.startButton} onPress={handleStart}>
        <View style={styles.startButtonInner}>
          <Ionicons name="navigate" size={36} color="#FFF" />
          <Text style={styles.startButtonText}>Start Trip</Text>
          <Text style={styles.startButtonSub}>GPS track recording</Text>
        </View>
      </Pressable>

      {/* Lifetime stats */}
      {stats && stats.totalTracks > 0 && (
        <View style={styles.lifetimeStats}>
          <Text style={styles.lifetimeSectionTitle}>Your Stats</Text>
          <View style={styles.lifetimeRow}>
            <View style={styles.lifetimeStat}>
              <Text style={styles.lifetimeValue}>{stats.totalTracks}</Text>
              <Text style={styles.lifetimeLabel}>Trips</Text>
            </View>
            <View style={styles.lifetimeStat}>
              <Text style={styles.lifetimeValue}>{stats.totalDistanceMiles.toFixed(1)}</Text>
              <Text style={styles.lifetimeLabel}>Miles</Text>
            </View>
            <View style={styles.lifetimeStat}>
              <Text style={styles.lifetimeValue}>{stats.totalDurationHours.toFixed(1)}</Text>
              <Text style={styles.lifetimeLabel}>Hours</Text>
            </View>
            <View style={styles.lifetimeStat}>
              <Text style={styles.lifetimeValue}>{stats.longestTrackMiles.toFixed(1)}</Text>
              <Text style={styles.lifetimeLabel}>Longest (mi)</Text>
            </View>
          </View>
        </View>
      )}
    </View>
  );

  // ── Track History ──────────────────────────────────────────────────────────

  const renderTrackItem = ({ item }: { item: FishingTrack }) => (
    <View style={styles.trackCard}>
      <View style={styles.trackCardHeader}>
        <View style={{ flex: 1 }}>
          <Text style={styles.trackName}>{item.name}</Text>
          <Text style={styles.trackDate}>
            {formatDate(item.startTime)} • {formatTime(item.startTime)}
            {item.endTime ? ` – ${formatTime(item.endTime)}` : ''}
          </Text>
        </View>
        <View style={styles.trackActions}>
          <Pressable onPress={() => handleExportGPX(item)} style={styles.trackActionBtn}>
            <Ionicons name="share-outline" size={18} color={palette.accent} />
          </Pressable>
          <Pressable onPress={() => handleDeleteTrack(item)} style={styles.trackActionBtn}>
            <Ionicons name="trash-outline" size={18} color={palette.error} />
          </Pressable>
        </View>
      </View>
      <View style={styles.trackStats}>
        <View style={styles.trackStatItem}>
          <Ionicons name="navigate-outline" size={14} color={palette.textMuted} />
          <Text style={styles.trackStatText}>{formatDistance(item.distanceMiles)}</Text>
        </View>
        <View style={styles.trackStatItem}>
          <Ionicons name="time-outline" size={14} color={palette.textMuted} />
          <Text style={styles.trackStatText}>{formatDuration(item.durationMinutes)}</Text>
        </View>
        <View style={styles.trackStatItem}>
          <Ionicons name="speedometer-outline" size={14} color={palette.textMuted} />
          <Text style={styles.trackStatText}>{formatSpeed(item.maxSpeedMph)} max</Text>
        </View>
        <View style={styles.trackStatItem}>
          <Ionicons name="location-outline" size={14} color={palette.textMuted} />
          <Text style={styles.trackStatText}>{item.points.length} pts</Text>
        </View>
      </View>
      {item.notes ? (
        <Text style={styles.trackNotes} numberOfLines={2}>{item.notes}</Text>
      ) : null}
    </View>
  );

  // ── Main Render ────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={palette.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Tab toggle: Record / History */}
      <View style={styles.tabRow}>
        <Pressable
          style={[styles.tab, !showHistory && styles.tabActive]}
          onPress={() => setShowHistory(false)}
        >
          <Ionicons
            name="navigate-outline"
            size={18}
            color={!showHistory ? palette.accent : palette.textMuted}
          />
          <Text style={[styles.tabText, !showHistory && styles.tabTextActive]}>Record</Text>
        </Pressable>
        <Pressable
          style={[styles.tab, showHistory && styles.tabActive]}
          onPress={() => setShowHistory(true)}
        >
          <Ionicons
            name="list-outline"
            size={18}
            color={showHistory ? palette.accent : palette.textMuted}
          />
          <Text style={[styles.tabText, showHistory && styles.tabTextActive]}>
            History ({savedTracks.length})
          </Text>
        </Pressable>
      </View>

      {showHistory ? (
        savedTracks.length === 0 ? (
          <View style={styles.emptyState}>
            <Ionicons name="trail-sign-outline" size={48} color={palette.textDim} />
            <Text style={styles.emptyTitle}>No Tracks Yet</Text>
            <Text style={styles.emptySubtitle}>Start recording a trip to see it here</Text>
          </View>
        ) : (
          <FlatList
            data={savedTracks}
            keyExtractor={(item) => item.id}
            renderItem={renderTrackItem}
            contentContainerStyle={styles.listContent}
            showsVerticalScrollIndicator={false}
          />
        )
      ) : isRecording ? (
        renderRecordingView()
      ) : (
        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
          {renderStartView()}
        </ScrollView>
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
  },
  scrollContent: {
    padding: 20,
    paddingBottom: 40,
  },
  listContent: {
    padding: 16,
    paddingBottom: 40,
  },

  // Tab toggle
  tabRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
    gap: 8,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.surfaceRaised,
    gap: 6,
  },
  tabActive: {
    backgroundColor: palette.accentLight,
  },
  tabText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textMuted,
  },
  tabTextActive: {
    color: palette.accent,
  },

  // Start view
  startView: {
    alignItems: 'center',
  },
  nameInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 10,
    width: '100%',
    marginBottom: 24,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  nameInput: {
    flex: 1,
    fontSize: 15,
    color: palette.text,
  },
  startButton: {
    width: 200,
    height: 200,
    borderRadius: 100,
    backgroundColor: palette.accent,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: palette.accent,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.35,
    shadowRadius: 20,
    elevation: 12,
    marginBottom: 32,
  },
  startButtonInner: {
    alignItems: 'center',
    gap: 6,
  },
  startButtonText: {
    fontSize: 20,
    fontWeight: '700',
    color: '#FFF',
    letterSpacing: 0.5,
  },
  startButtonSub: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.7)',
  },

  // Lifetime stats
  lifetimeStats: {
    width: '100%',
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  lifetimeSectionTitle: {
    ...typeStyles.sectionHeader,
    marginBottom: 16,
  },
  lifetimeRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  lifetimeStat: {
    alignItems: 'center',
  },
  lifetimeValue: {
    fontSize: 22,
    fontWeight: '700',
    color: palette.text,
  },
  lifetimeLabel: {
    fontSize: 11,
    color: palette.textMuted,
    marginTop: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },

  // Recording view
  recordingView: {
    flex: 1,
    padding: 20,
    alignItems: 'center',
  },
  recordingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 16,
  },
  recordingDot: {
    width: 16,
    height: 16,
    borderRadius: 8,
    justifyContent: 'center',
    alignItems: 'center',
  },
  recordingDotInner: {
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: palette.error,
  },
  recordingDotPaused: {
    backgroundColor: palette.warning,
  },
  recordingLabel: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.error,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
  },
  recordingPoints: {
    fontSize: 12,
    color: palette.textMuted,
    marginLeft: 'auto',
  },
  bigDuration: {
    fontSize: 64,
    fontWeight: '200',
    color: palette.text,
    letterSpacing: -2,
    marginBottom: 24,
  },
  statGrid: {
    flexDirection: 'row',
    gap: 16,
    marginBottom: 32,
  },
  statCell: {
    flex: 1,
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    alignItems: 'center',
    gap: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  statValue: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
  },
  statLabel: {
    fontSize: 10,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  controlRow: {
    flexDirection: 'row',
    gap: 16,
    marginTop: 'auto',
    paddingBottom: 20,
  },
  controlBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 16,
    borderRadius: 14,
    gap: 8,
  },
  controlBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FFF',
  },
  pauseBtn: {
    backgroundColor: palette.warning,
  },
  resumeBtn: {
    backgroundColor: palette.success,
  },
  stopBtn: {
    backgroundColor: palette.error,
  },

  // Track history cards
  trackCard: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    marginBottom: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  trackCardHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 10,
  },
  trackName: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  trackDate: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
  trackActions: {
    flexDirection: 'row',
    gap: 8,
  },
  trackActionBtn: {
    padding: 6,
  },
  trackStats: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  trackStatItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  trackStatText: {
    fontSize: 13,
    color: palette.textSecondary,
  },
  trackNotes: {
    fontSize: 13,
    color: palette.textMuted,
    fontStyle: 'italic',
    marginTop: 8,
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
});
