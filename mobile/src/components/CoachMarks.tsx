/**
 * CoachMarks — First-time user guidance overlays.
 *
 * Design principle: "Empty State" + "Onboarding" patterns (Gaigg Ch.1).
 * After onboarding, progressively introduce key map features with
 * non-blocking tooltip hints. Each tip is shown once and dismissed on tap.
 *
 * Implements progressive feature unlock: users discover complexity
 * gradually instead of being overwhelmed at first launch.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  Dimensions,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

const COACH_MARKS_KEY = '@opencatch_coach_marks_seen';
const { width: SCREEN_WIDTH } = Dimensions.get('window');

interface CoachMark {
  id: string;
  title: string;
  body: string;
  icon: keyof typeof Ionicons.glyphMap;
  /** Position on screen */
  position: 'top-center' | 'bottom-center' | 'bottom-right';
  /** Delay before showing (ms) */
  delay: number;
}

const COACH_MARKS: CoachMark[] = [
  {
    id: 'search-bar',
    title: 'Search for spots',
    body: 'Find any lake, river, or stream by name. We have data on thousands of fishing locations.',
    icon: 'search',
    position: 'top-center',
    delay: 1500,
  },
  {
    id: 'quick-action',
    title: 'Quick actions',
    body: 'Tap the + button to log a catch, record a trip, or mark a spot.',
    icon: 'add-circle',
    position: 'bottom-right',
    delay: 500,
  },
  {
    id: 'bottom-sheet',
    title: 'Explore nearby spots',
    body: 'Swipe up on the bottom panel to browse fishing spots near you, ranked by current conditions.',
    icon: 'chevron-up',
    position: 'bottom-center',
    delay: 500,
  },
];

interface CoachMarksProps {
  /** Set to true when the map screen has finished its initial load */
  mapReady: boolean;
}

export function CoachMarks({ mapReady }: CoachMarksProps) {
  const [currentMarkIndex, setCurrentMarkIndex] = useState(-1);
  const [seenMarks, setSeenMarks] = useState<Set<string>>(new Set());
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Load seen marks from storage
  useEffect(() => {
    AsyncStorage.getItem(COACH_MARKS_KEY).then((raw) => {
      if (raw) {
        try {
          const seen = JSON.parse(raw) as string[];
          setSeenMarks(new Set(seen));
        } catch { /* ignore */ }
      }
    });
  }, []);

  // Show next unseen mark when map is ready
  useEffect(() => {
    if (!mapReady || currentMarkIndex >= 0) return;

    const nextIndex = COACH_MARKS.findIndex((m) => !seenMarks.has(m.id));
    if (nextIndex === -1) return; // All marks seen

    const timer = setTimeout(() => {
      setCurrentMarkIndex(nextIndex);
      Animated.spring(fadeAnim, {
        toValue: 1,
        friction: 8,
        tension: 40,
        useNativeDriver: true,
      }).start();
    }, COACH_MARKS[nextIndex].delay);

    return () => clearTimeout(timer);
  }, [mapReady, seenMarks, currentMarkIndex, fadeAnim]);

  const dismissCurrent = useCallback(async () => {
    const mark = COACH_MARKS[currentMarkIndex];
    if (!mark) return;

    // Fade out
    Animated.timing(fadeAnim, {
      toValue: 0,
      duration: 200,
      useNativeDriver: true,
    }).start(async () => {
      // Mark as seen
      const newSeen = new Set(seenMarks);
      newSeen.add(mark.id);
      setSeenMarks(newSeen);
      await AsyncStorage.setItem(COACH_MARKS_KEY, JSON.stringify(Array.from(newSeen)));

      // Advance to next unseen
      const nextIndex = COACH_MARKS.findIndex((m, i) => i > currentMarkIndex && !newSeen.has(m.id));
      if (nextIndex === -1) {
        setCurrentMarkIndex(-1);
        return;
      }

      setTimeout(() => {
        setCurrentMarkIndex(nextIndex);
        Animated.spring(fadeAnim, {
          toValue: 1,
          friction: 8,
          tension: 40,
          useNativeDriver: true,
        }).start();
      }, COACH_MARKS[nextIndex].delay);
    });
  }, [currentMarkIndex, seenMarks, fadeAnim]);

  if (currentMarkIndex < 0 || currentMarkIndex >= COACH_MARKS.length) return null;

  const mark = COACH_MARKS[currentMarkIndex];
  const positionStyle = getPositionStyle(mark.position);

  return (
    <Animated.View
      style={[styles.container, positionStyle, { opacity: fadeAnim, transform: [{ scale: fadeAnim }] }]}
      pointerEvents="box-none"
    >
      <Pressable style={styles.tooltip} onPress={dismissCurrent}>
        <View style={styles.iconRow}>
          <View style={styles.iconCircle}>
            <Ionicons name={mark.icon} size={18} color={palette.accent} />
          </View>
          <View style={styles.textArea}>
            <Text style={styles.tooltipTitle}>{mark.title}</Text>
            <Text style={styles.tooltipBody}>{mark.body}</Text>
          </View>
        </View>
        <Text style={styles.dismissHint}>Tap to dismiss</Text>
      </Pressable>
    </Animated.View>
  );
}

function getPositionStyle(position: CoachMark['position']): any {
  switch (position) {
    case 'top-center':
      return {
        position: 'absolute',
        top: Platform.OS === 'ios' ? 120 : 100,
        left: 20,
        right: 20,
        alignItems: 'center',
        zIndex: 999,
      };
    case 'bottom-right':
      return {
        position: 'absolute',
        bottom: Platform.OS === 'ios' ? 170 : 140,
        right: 16,
        width: SCREEN_WIDTH * 0.7,
        zIndex: 999,
      };
    case 'bottom-center':
      return {
        position: 'absolute',
        bottom: Platform.OS === 'ios' ? 115 : 85,
        left: 20,
        right: 20,
        alignItems: 'center',
        zIndex: 999,
      };
    default:
      return {};
  }
}

const styles = StyleSheet.create({
  container: {},
  tooltip: {
    backgroundColor: 'rgba(30, 30, 30, 0.92)',
    borderRadius: 14,
    padding: 16,
    maxWidth: 320,
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 12,
  },
  iconRow: {
    flexDirection: 'row',
    gap: 12,
  },
  iconCircle: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: palette.accent + '25',
    alignItems: 'center',
    justifyContent: 'center',
  },
  textArea: {
    flex: 1,
    gap: 4,
  },
  tooltipTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  tooltipBody: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.8)',
    lineHeight: 18,
  },
  dismissHint: {
    fontSize: 11,
    color: 'rgba(255, 255, 255, 0.45)',
    textAlign: 'right',
    marginTop: 8,
  },
});
