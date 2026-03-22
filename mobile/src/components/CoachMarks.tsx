/**
 * CoachMarks — First-time user guidance overlays.
 *
 * Design principle: "Empty State" + "Onboarding" patterns (Gaigg Ch.1).
 * After onboarding, progressively introduce key map features with
 * non-blocking tooltip hints. Each tip is shown once and dismissed on tap.
 *
 * Implements progressive feature unlock: users discover complexity
 * gradually instead of being overwhelmed at first launch.
 *
 * Tooltips use smart positioning (above/below/left/right) based on target
 * location, with a semi-transparent backdrop, animated arrow, and smooth
 * fade in/out transitions.
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
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

const COACH_MARKS_KEY = '@opencatch_coach_marks_seen';
const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

// Safe area constants (fallbacks when insets unavailable)
const STATUS_BAR_HEIGHT = Platform.OS === 'ios' ? 50 : 30;
const TAB_BAR_HEIGHT = Platform.OS === 'ios' ? 88 : 64;

// Arrow dimensions
const ARROW_SIZE = 10;

type TooltipPlacement = 'above' | 'below';

interface TargetRegion {
  /** Center X of the target element (screen coords) */
  x: number;
  /** Center Y of the target element (screen coords) */
  y: number;
  /** Width of the target element */
  width: number;
  /** Height of the target element */
  height: number;
}

interface CoachMark {
  id: string;
  title: string;
  body: string;
  icon: keyof typeof Ionicons.glyphMap;
  /** Target region on screen (approximate) */
  target: TargetRegion;
  /** Preferred placement relative to target */
  preferredPlacement: TooltipPlacement;
  /** Delay before showing (ms) */
  delay: number;
}

/**
 * Coach marks are defined with approximate screen target positions.
 * These correspond to key UI elements on the MapScreen.
 */
function getCoachMarks(): CoachMark[] {
  return [
    {
      id: 'search-bar',
      title: 'Search for spots',
      body: 'Find any lake, river, or stream by name. We have data on thousands of fishing locations.',
      icon: 'search',
      target: {
        x: SCREEN_WIDTH / 2,
        y: STATUS_BAR_HEIGHT + 30,
        width: SCREEN_WIDTH - 32,
        height: 44,
      },
      preferredPlacement: 'below',
      delay: 1500,
    },
    {
      id: 'quick-action',
      title: 'Quick actions',
      body: 'Tap the + button to log a catch, record a trip, or mark a spot.',
      icon: 'add-circle',
      target: {
        x: SCREEN_WIDTH - 40,
        y: SCREEN_HEIGHT - TAB_BAR_HEIGHT - 80,
        width: 56,
        height: 56,
      },
      preferredPlacement: 'above',
      delay: 500,
    },
    {
      id: 'bottom-sheet',
      title: 'Explore nearby spots',
      body: 'Swipe up on the bottom panel to browse fishing spots near you, ranked by current conditions.',
      icon: 'chevron-up',
      target: {
        x: SCREEN_WIDTH / 2,
        y: SCREEN_HEIGHT - TAB_BAR_HEIGHT - 20,
        width: SCREEN_WIDTH - 32,
        height: 40,
      },
      preferredPlacement: 'above',
      delay: 500,
    },
  ];
}

interface CoachMarksProps {
  /** Set to true when the map screen has finished its initial load */
  mapReady: boolean;
}

export function CoachMarks({ mapReady }: CoachMarksProps) {
  const [currentMarkIndex, setCurrentMarkIndex] = useState(-1);
  const [seenMarks, setSeenMarks] = useState<Set<string>>(new Set());
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const coachMarks = useRef(getCoachMarks()).current;

  let insets = { top: STATUS_BAR_HEIGHT, bottom: TAB_BAR_HEIGHT };
  try {
    const safeInsets = useSafeAreaInsets();
    if (safeInsets) {
      insets = { top: safeInsets.top, bottom: safeInsets.bottom };
    }
  } catch {
    // SafeAreaProvider not available — use fallbacks
  }

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

    const nextIndex = coachMarks.findIndex((m) => !seenMarks.has(m.id));
    if (nextIndex === -1) return; // All marks seen

    const timer = setTimeout(() => {
      setCurrentMarkIndex(nextIndex);
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }).start();
    }, coachMarks[nextIndex].delay);

    return () => clearTimeout(timer);
  }, [mapReady, seenMarks, currentMarkIndex, fadeAnim, coachMarks]);

  const dismissCurrent = useCallback(async () => {
    const mark = coachMarks[currentMarkIndex];
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
      const nextIndex = coachMarks.findIndex((m, i) => i > currentMarkIndex && !newSeen.has(m.id));
      if (nextIndex === -1) {
        setCurrentMarkIndex(-1);
        return;
      }

      setTimeout(() => {
        setCurrentMarkIndex(nextIndex);
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: true,
        }).start();
      }, coachMarks[nextIndex].delay);
    });
  }, [currentMarkIndex, seenMarks, fadeAnim, coachMarks]);

  if (currentMarkIndex < 0 || currentMarkIndex >= coachMarks.length) return null;

  const mark = coachMarks[currentMarkIndex];
  const layout = computeTooltipLayout(mark, insets);

  return (
    <Animated.View
      style={[styles.fullOverlay, { opacity: fadeAnim }]}
      pointerEvents="auto"
    >
      {/* Semi-transparent backdrop — tap anywhere to dismiss */}
      <Pressable style={styles.backdrop} onPress={dismissCurrent} />

      {/* Spotlight cutout effect around target */}
      <View
        style={[
          styles.spotlight,
          {
            left: mark.target.x - mark.target.width / 2 - 8,
            top: mark.target.y - mark.target.height / 2 - 8,
            width: mark.target.width + 16,
            height: mark.target.height + 16,
            borderRadius: 14,
          },
        ]}
        pointerEvents="none"
      />

      {/* Tooltip card */}
      <Animated.View
        style={[
          styles.tooltipContainer,
          {
            top: layout.top,
            left: layout.left,
            maxWidth: layout.maxWidth,
            transform: [
              {
                translateY: fadeAnim.interpolate({
                  inputRange: [0, 1],
                  outputRange: [layout.placement === 'below' ? -8 : 8, 0],
                }),
              },
            ],
          },
        ]}
        pointerEvents="box-none"
      >
        {/* Arrow pointing at target — top arrow if tooltip is below target */}
        {layout.placement === 'below' && (
          <View style={[styles.arrowUp, { left: layout.arrowOffsetX }]} />
        )}

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

          {/* Step indicator + dismiss hint */}
          <View style={styles.footer}>
            <View style={styles.stepIndicator}>
              {coachMarks.map((_, i) => (
                <View
                  key={i}
                  style={[
                    styles.stepDot,
                    i === currentMarkIndex && styles.stepDotActive,
                    i < currentMarkIndex && seenMarks.has(coachMarks[i].id) && styles.stepDotSeen,
                  ]}
                />
              ))}
            </View>
            <Text style={styles.dismissHint}>Tap to dismiss</Text>
          </View>
        </Pressable>

        {/* Arrow pointing at target — bottom arrow if tooltip is above target */}
        {layout.placement === 'above' && (
          <View style={[styles.arrowDown, { left: layout.arrowOffsetX }]} />
        )}
      </Animated.View>
    </Animated.View>
  );
}

// ── Layout computation ────────────────────────────────────────────

interface TooltipLayout {
  top: number;
  left: number;
  maxWidth: number;
  placement: TooltipPlacement;
  arrowOffsetX: number;
}

function computeTooltipLayout(
  mark: CoachMark,
  insets: { top: number; bottom: number },
): TooltipLayout {
  const TOOLTIP_PADDING = 16;
  const TOOLTIP_MAX_WIDTH = Math.min(320, SCREEN_WIDTH - 32);
  const GAP = 12; // Gap between target and tooltip

  const targetTop = mark.target.y - mark.target.height / 2;
  const targetBottom = mark.target.y + mark.target.height / 2;

  // Decide placement: prefer the side with more space
  const spaceAbove = targetTop - insets.top;
  const spaceBelow = SCREEN_HEIGHT - targetBottom - insets.bottom - 20;
  let placement = mark.preferredPlacement;

  // Override if not enough space (assume tooltip needs ~120px)
  if (placement === 'below' && spaceBelow < 140 && spaceAbove > spaceBelow) {
    placement = 'above';
  } else if (placement === 'above' && spaceAbove < 140 && spaceBelow > spaceAbove) {
    placement = 'below';
  }

  // Horizontal: center tooltip on target, but clamp to screen
  let left = mark.target.x - TOOLTIP_MAX_WIDTH / 2;
  left = Math.max(TOOLTIP_PADDING, Math.min(left, SCREEN_WIDTH - TOOLTIP_MAX_WIDTH - TOOLTIP_PADDING));

  // Vertical
  let top: number;
  if (placement === 'below') {
    top = targetBottom + GAP + ARROW_SIZE;
  } else {
    // Place above — tooltip bottom edge above target top
    // We estimate tooltip height at ~130px; actual rendering handles overflow
    top = targetTop - GAP - ARROW_SIZE - 130;
    top = Math.max(insets.top + 8, top);
  }

  // Arrow: point at target center X, relative to tooltip left
  const arrowOffsetX = Math.max(20, Math.min(mark.target.x - left - ARROW_SIZE / 2, TOOLTIP_MAX_WIDTH - 30));

  return {
    top,
    left,
    maxWidth: TOOLTIP_MAX_WIDTH,
    placement,
    arrowOffsetX,
  };
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  fullOverlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 9999,
    elevation: 9999,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0, 0, 0, 0.55)',
  },
  spotlight: {
    position: 'absolute',
    backgroundColor: 'transparent',
    borderWidth: 2,
    borderColor: 'rgba(255, 255, 255, 0.6)',
    borderStyle: 'dashed',
  },
  tooltipContainer: {
    position: 'absolute',
  },
  tooltip: {
    backgroundColor: 'rgba(26, 26, 24, 0.95)',
    borderRadius: 14,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 20,
  },
  iconRow: {
    flexDirection: 'row',
    gap: 12,
  },
  iconCircle: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: palette.accent + '30',
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
    letterSpacing: -0.2,
  },
  tooltipBody: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.82)',
    lineHeight: 19,
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 12,
  },
  stepIndicator: {
    flexDirection: 'row',
    gap: 6,
  },
  stepDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: 'rgba(255, 255, 255, 0.25)',
  },
  stepDotActive: {
    backgroundColor: palette.accent,
    width: 18,
    borderRadius: 3,
  },
  stepDotSeen: {
    backgroundColor: 'rgba(255, 255, 255, 0.5)',
  },
  dismissHint: {
    fontSize: 11,
    color: 'rgba(255, 255, 255, 0.4)',
  },
  arrowUp: {
    position: 'absolute',
    top: -ARROW_SIZE,
    width: 0,
    height: 0,
    borderLeftWidth: ARROW_SIZE,
    borderRightWidth: ARROW_SIZE,
    borderBottomWidth: ARROW_SIZE,
    borderLeftColor: 'transparent',
    borderRightColor: 'transparent',
    borderBottomColor: 'rgba(26, 26, 24, 0.95)',
  },
  arrowDown: {
    position: 'absolute',
    bottom: -ARROW_SIZE,
    width: 0,
    height: 0,
    borderLeftWidth: ARROW_SIZE,
    borderRightWidth: ARROW_SIZE,
    borderTopWidth: ARROW_SIZE,
    borderLeftColor: 'transparent',
    borderRightColor: 'transparent',
    borderTopColor: 'rgba(26, 26, 24, 0.95)',
  },
});
