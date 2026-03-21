/**
 * OpenCatch — Track Recording Screen
 *
 * Navionics-inspired trip recording UI with:
 * - Mini-map showing live GPS track colored by speed
 * - Live stats: distance, duration, avg speed, max speed, current speed
 * - Mark Waypoint button to drop pins during recording
 * - Catch logging shortcut at current GPS position
 * - Big prominent elapsed timer
 * - Pause/resume with visual state changes
 * - Speed-colored track line (green=slow, red=fast)
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
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
  type TrackWaypoint,
} from '../services/trackRecorder';

// MapLibre — optional (only works on native)
let MLMapView: any = null;
let Camera: any = null;
let ShapeSource: any = null;
let LineLayer: any = null;
let PointAnnotation: any = null;
let UserLocation: any = null;

try {
  const maplibre = require('@maplibre/maplibre-react-native');
  MLMapView = maplibre.MapView;
  Camera = maplibre.Camera;
  ShapeSource = maplibre.ShapeSource;
  LineLayer = maplibre.LineLayer;
  PointAnnotation = maplibre.PointAnnotation;
  UserLocation = maplibre.UserLocation;
} catch {
  // Web fallback — no map
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  const s = Math.floor((minutes * 60) % 60);
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatDurationLarge(startTime: number): string {
  const elapsed = Math.max(0, Date.now() - startTime);
  const totalSec = Math.floor(elapsed / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function formatDistance(miles: number): string {
  if (miles < 0.1) return `${Math.round(miles * 5280)} ft`;
  return `${miles.toFixed(2)} mi`;
}

function formatSpeed(mph: number): string {
  return `${mph.toFixed(1)}`;
}

// ── Component ────────────────────────────────────────────────────────────────

export function TrackRecordingScreen({ navigation }: any) {
  const recorder = trackRecorder;

  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [activeTrack, setActiveTrack] = useState<FishingTrack | null>(null);
  const [stats, setStats] = useState<TrackStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [trackName, setTrackName] = useState('');
  const [timerDisplay, setTimerDisplay] = useState('00:00');

  // Pulse animation for recording indicator
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const cameraRef = useRef<any>(null);

  // Live timer update
  useEffect(() => {
    if (isRecording && !isPaused && activeTrack) {
      const interval = setInterval(() => {
        setTimerDisplay(formatDurationLarge(activeTrack.startTime));
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [isRecording, isPaused, activeTrack?.startTime]);

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

  // Load data on mount
  useEffect(() => {
    loadData();
  }, []);

  // Subscribe to recorder updates
  useEffect(() => {
    const unsub = recorder.subscribe((track: FishingTrack | null) => {
      setActiveTrack(track ? { ...track } : null);
      if (track) {
        setIsRecording(true);
        setIsPaused(recorder.getIsPaused());
      }
    });
    // Check if already recording
    const existing = recorder.getActiveTrack();
    if (existing) {
      setActiveTrack({ ...existing });
      setIsRecording(true);
      setIsPaused(recorder.getIsPaused());
      setTimerDisplay(formatDurationLarge(existing.startTime));
    }
    return unsub;
  }, []);

  const loadData = async () => {
    setLoading(true);
    const s = await recorder.getStats();
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
              const s = await recorder.getStats();
              setStats(s);
            }
          },
        },
      ],
    );
  };

  const handleMarkWaypoint = () => {
    const wp = recorder.addWaypoint();
    if (wp) {
      Alert.alert('Waypoint Marked', `Dropped pin at your current location.`);
    } else {
      Alert.alert('No GPS', 'Waiting for GPS signal to mark waypoint.');
    }
  };

  const handleLogCatch = () => {
    Alert.prompt
      ? Alert.prompt(
          'Log Catch',
          'Species (optional):',
          [
            { text: 'Cancel', style: 'cancel' },
            {
              text: 'Log',
              onPress: (species?: string) => {
                const wp = recorder.addCatchWaypoint(species);
                if (!wp) {
                  Alert.alert('No GPS', 'Waiting for GPS signal.');
                }
              },
            },
          ],
          'plain-text',
          '',
        )
      : (() => {
          const wp = recorder.addCatchWaypoint();
          if (!wp) {
            Alert.alert('No GPS', 'Waiting for GPS signal.');
          }
        })();
  };

  // ── Mini-map for active recording ────────────────────────────────────────

  const renderMiniMap = () => {
    if (!activeTrack || activeTrack.points.length === 0 || !MLMapView) return null;

    const lastPt = activeTrack.points[activeTrack.points.length - 1];
    const trackGeoJSON = buildSpeedColoredGeoJSON(activeTrack.points);

    // Simple solid line fallback for the full track
    const fullLineGeoJSON: GeoJSON.Feature = {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: activeTrack.points.map((p) => [p.lon, p.lat]),
      },
      properties: {},
    };

    return (
      <View style={styles.miniMapContainer}>
        <MLMapView
          style={styles.miniMap}
          logoEnabled={false}
          attributionEnabled={false}
          scrollEnabled={false}
          pitchEnabled={false}
          rotateEnabled={false}
          zoomEnabled={false}
          compassEnabled={false}
        >
          <Camera
            ref={cameraRef}
            centerCoordinate={[lastPt.lon, lastPt.lat]}
            zoomLevel={14}
            animationDuration={500}
          />
          {UserLocation && <UserLocation visible />}

          {/* Speed-colored track segments */}
          {activeTrack.points.length >= 2 && ShapeSource && LineLayer && (
            <ShapeSource id="track-speed-source" shape={trackGeoJSON}>
              <LineLayer
                id="track-speed-layer"
                style={{
                  lineColor: ['get', 'color'],
                  lineWidth: 4,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </ShapeSource>
          )}

          {/* Waypoint markers */}
          {PointAnnotation && (activeTrack.waypoints ?? []).map((wp) => (
            <PointAnnotation
              key={wp.id}
              id={wp.id}
              coordinate={[wp.lon, wp.lat]}
            >
              <View style={[
                styles.miniMapWaypoint,
                wp.type === 'catch' ? styles.miniMapCatchPin : styles.miniMapWaypointPin,
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

        {/* Waypoint/catch count overlay */}
        {(activeTrack.waypoints?.length ?? 0) > 0 && (
          <View style={styles.miniMapBadge}>
            <Ionicons name="flag" size={10} color="#FFFFFF" />
            <Text style={styles.miniMapBadgeText}>{activeTrack.waypoints?.length ?? 0}</Text>
          </View>
        )}
      </View>
    );
  };

  // ── Active Recording View ──────────────────────────────────────────────────

  const renderRecordingView = () => {
    const track = activeTrack;
    const distance = track?.distanceMiles ?? 0;
    const maxSpeed = track?.maxSpeedMph ?? 0;
    const avgSpeed = track?.avgSpeedMph ?? 0;
    const points = track?.points?.length ?? 0;
    const currentSpeed = track?.points?.length
      ? (track.points[track.points.length - 1].speed ?? 0) * 2.237
      : 0;
    const waypointCount = track?.waypoints?.length ?? 0;

    return (
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={styles.recordingScrollContent}
        showsVerticalScrollIndicator={false}
      >
        {/* Recording indicator header */}
        <View style={styles.recordingHeader}>
          <Animated.View style={[styles.recordingDot, { transform: [{ scale: pulseAnim }] }]}>
            <View style={[styles.recordingDotInner, isPaused && styles.recordingDotPaused]} />
          </Animated.View>
          <Text style={[styles.recordingLabel, isPaused && styles.recordingLabelPaused]}>
            {isPaused ? 'PAUSED' : 'RECORDING'}
          </Text>
          <Text style={styles.recordingPoints}>{points} pts</Text>
        </View>

        {/* Big timer */}
        <Text style={styles.bigTimer}>{timerDisplay}</Text>

        {/* Mini-map */}
        {renderMiniMap()}

        {/* Stats grid — 2x2 + current speed hero */}
        <View style={styles.currentSpeedRow}>
          <Ionicons name="speedometer" size={24} color={speedToColor(currentSpeed)} />
          <Text style={[styles.currentSpeedValue, { color: speedToColor(currentSpeed) }]}>
            {formatSpeed(currentSpeed)}
          </Text>
          <Text style={styles.currentSpeedUnit}>mph</Text>
        </View>

        <View style={styles.statGrid}>
          <View style={styles.statCell}>
            <Ionicons name="navigate-outline" size={18} color={palette.accent} />
            <Text style={styles.statValue}>{formatDistance(distance)}</Text>
            <Text style={styles.statLabel}>Distance</Text>
          </View>
          <View style={styles.statCell}>
            <Ionicons name="trending-up-outline" size={18} color={palette.success} />
            <Text style={styles.statValue}>{formatSpeed(avgSpeed)} mph</Text>
            <Text style={styles.statLabel}>Avg Speed</Text>
          </View>
          <View style={styles.statCell}>
            <Ionicons name="flash-outline" size={18} color={palette.warning} />
            <Text style={styles.statValue}>{formatSpeed(maxSpeed)} mph</Text>
            <Text style={styles.statLabel}>Max Speed</Text>
          </View>
          <View style={styles.statCell}>
            <Ionicons name="flag-outline" size={18} color={palette.accent} />
            <Text style={styles.statValue}>{waypointCount}</Text>
            <Text style={styles.statLabel}>Waypoints</Text>
          </View>
        </View>

        {/* Quick action buttons: Mark Waypoint + Log Catch */}
        <View style={styles.quickActionsRow}>
          <Pressable style={styles.quickActionBtn} onPress={handleMarkWaypoint}>
            <View style={[styles.quickActionIcon, { backgroundColor: palette.accent }]}>
              <Ionicons name="flag" size={18} color="#FFFFFF" />
            </View>
            <Text style={styles.quickActionLabel}>Mark Waypoint</Text>
          </Pressable>
          <Pressable style={styles.quickActionBtn} onPress={handleLogCatch}>
            <View style={[styles.quickActionIcon, { backgroundColor: palette.success }]}>
              <Ionicons name="fish" size={18} color="#FFFFFF" />
            </View>
            <Text style={styles.quickActionLabel}>Log Catch</Text>
          </Pressable>
        </View>

        {/* Waypoint list (if any) */}
        {waypointCount > 0 && (
          <View style={styles.waypointList}>
            <Text style={styles.waypointListTitle}>Markers</Text>
            {(track?.waypoints ?? []).map((wp, idx) => (
              <View key={wp.id} style={styles.waypointItem}>
                <View style={[
                  styles.waypointDot,
                  { backgroundColor: wp.type === 'catch' ? palette.success : palette.accent },
                ]} />
                <Ionicons
                  name={wp.type === 'catch' ? 'fish' : 'flag'}
                  size={14}
                  color={wp.type === 'catch' ? palette.success : palette.accent}
                />
                <Text style={styles.waypointLabel}>{wp.label}</Text>
                <Text style={styles.waypointTime}>
                  {new Date(wp.timestamp).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                </Text>
              </View>
            ))}
          </View>
        )}

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
      </ScrollView>
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

      {/* View track history */}
      <Pressable
        style={styles.historyButton}
        onPress={() => navigation.navigate('TrackHistory')}
      >
        <Ionicons name="time-outline" size={18} color={palette.accent} />
        <Text style={styles.historyButtonText}>View Track History</Text>
        <Ionicons name="chevron-forward" size={16} color={palette.textMuted} />
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

      {/* Speed legend */}
      <View style={styles.speedLegend}>
        <Text style={styles.speedLegendTitle}>Track Colors</Text>
        <View style={styles.speedLegendRow}>
          <View style={styles.speedLegendItem}>
            <View style={[styles.speedLegendDot, { backgroundColor: speedToColor(0) }]} />
            <Text style={styles.speedLegendLabel}>Slow</Text>
          </View>
          <View style={styles.speedLegendItem}>
            <View style={[styles.speedLegendDot, { backgroundColor: speedToColor(15) }]} />
            <Text style={styles.speedLegendLabel}>Medium</Text>
          </View>
          <View style={styles.speedLegendItem}>
            <View style={[styles.speedLegendDot, { backgroundColor: speedToColor(30) }]} />
            <Text style={styles.speedLegendLabel}>Fast</Text>
          </View>
        </View>
      </View>
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
      {isRecording ? (
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
  recordingScrollContent: {
    padding: 20,
    paddingBottom: 40,
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
    marginBottom: 24,
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

  // History button
  historyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
    gap: 10,
    width: '100%',
    marginBottom: 20,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  historyButtonText: {
    flex: 1,
    fontSize: 15,
    fontWeight: '600',
    color: palette.accent,
  },

  // Lifetime stats
  lifetimeStats: {
    width: '100%',
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    marginBottom: 16,
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

  // Speed legend
  speedLegend: {
    width: '100%',
    backgroundColor: palette.surface,
    borderRadius: 12,
    padding: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  speedLegendTitle: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 10,
  },
  speedLegendRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  speedLegendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  speedLegendDot: {
    width: 16,
    height: 4,
    borderRadius: 2,
  },
  speedLegendLabel: {
    fontSize: 12,
    color: palette.textSecondary,
  },

  // Recording view
  recordingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 8,
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
    fontSize: 13,
    fontWeight: '700',
    color: palette.error,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
  },
  recordingLabelPaused: {
    color: palette.warning,
  },
  recordingPoints: {
    fontSize: 12,
    color: palette.textMuted,
    marginLeft: 'auto',
  },

  // Big timer
  bigTimer: {
    fontSize: 72,
    fontWeight: '200',
    color: palette.text,
    letterSpacing: -2,
    textAlign: 'center',
    marginBottom: 12,
    fontVariant: ['tabular-nums'],
  },

  // Mini-map
  miniMapContainer: {
    width: '100%',
    height: 200,
    borderRadius: 16,
    overflow: 'hidden',
    marginBottom: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  miniMap: {
    flex: 1,
  },
  miniMapWaypoint: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  miniMapWaypointPin: {
    backgroundColor: palette.accent,
  },
  miniMapCatchPin: {
    backgroundColor: palette.success,
  },
  miniMapBadge: {
    position: 'absolute',
    top: 8,
    right: 8,
    backgroundColor: 'rgba(0,0,0,0.6)',
    borderRadius: 10,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 3,
    gap: 4,
  },
  miniMapBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#FFFFFF',
  },

  // Current speed hero
  currentSpeedRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'center',
    gap: 8,
    marginBottom: 16,
  },
  currentSpeedValue: {
    fontSize: 36,
    fontWeight: '700',
    letterSpacing: -1,
  },
  currentSpeedUnit: {
    fontSize: 16,
    color: palette.textMuted,
    fontWeight: '500',
  },

  // Stat grid — 2x2
  statGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 20,
  },
  statCell: {
    width: '47%',
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    alignItems: 'center',
    gap: 4,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  statValue: {
    fontSize: 17,
    fontWeight: '700',
    color: palette.text,
  },
  statLabel: {
    fontSize: 10,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },

  // Quick actions
  quickActionsRow: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  quickActionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    gap: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  quickActionIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  quickActionLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
    flexShrink: 1,
  },

  // Waypoint list
  waypointList: {
    width: '100%',
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    marginBottom: 20,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  waypointListTitle: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 10,
  },
  waypointItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
  },
  waypointDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  waypointLabel: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
  },
  waypointTime: {
    fontSize: 12,
    color: palette.textMuted,
  },

  // Controls
  controlRow: {
    flexDirection: 'row',
    gap: 16,
    marginTop: 8,
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
});
