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
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

type CoachTargetOverrides = Partial<Record<'search-bar' | 'quick-action' | 'bottom-sheet', TargetRegion>>;

/**
 * Coach marks are defined with approximate screen target positions.
 * These correspond to key UI elements on the MapScreen.
 */
function getCoachMarks(targetOverrides?: CoachTargetOverrides): CoachMark[] {
  return [
    {
      id: 'search-bar',
      title: 'Search the map',
      body: 'Jump straight to a lake, river, coastline, or saved spot from here.',
      icon: 'search',
      target: targetOverrides?.['search-bar'] ?? {
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
      body: 'Use the plus button to log a catch, record a trip, or drop a new spot instantly.',
      icon: 'add-circle',
      target: targetOverrides?.['quick-action'] ?? {
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
      title: 'Pull this panel up',
      body: 'Drag this header upward for nearby spots, bite windows, pressure, and water conditions.',
      icon: 'chevron-up',
      target: targetOverrides?.['bottom-sheet'] ?? {
        x: SCREEN_WIDTH / 2,
        y: SCREEN_HEIGHT - TAB_BAR_HEIGHT - 20,
        width: SCREEN_WIDTH - 32,
        height: 56,
      },
      preferredPlacement: 'above',
      delay: 500,
    },
  ];
}

interface CoachMarksProps {
  /** Set to true when the map screen has finished its initial load */
  mapReady: boolean;
  targets?: CoachTargetOverrides;
}

export function CoachMarks({ mapReady, targets }: CoachMarksProps) {
  const [currentMarkIndex, setCurrentMarkIndex] = useState(-1);
  const [seenMarks, setSeenMarks] = useState<Set<string>>(new Set());
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const coachMarks = useMemo(() => getCoachMarks(targets), [targets]);

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
            width: layout.width,
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
  width: number;
  placement: TooltipPlacement;
  arrowOffsetX: number;
}

function estimateTooltipHeight(mark: CoachMark, width: number): number {
  const textWidth = Math.max(150, width - 88);
  const titleLines = Math.max(1, Math.ceil(mark.title.length / 24));
  const charsPerLine = Math.max(22, Math.floor(textWidth / 7));
  const bodyLines = Math.max(2, Math.ceil(mark.body.length / charsPerLine));
  return 72 + titleLines * 18 + bodyLines * 18 + 34;
}

function computeTooltipLayout(
  mark: CoachMark,
  insets: { top: number; bottom: number },
): TooltipLayout {
  const TOOLTIP_PADDING = 16;
  const TOOLTIP_MAX_WIDTH = Math.min(320, SCREEN_WIDTH - 24);
  const TOOLTIP_MIN_WIDTH = Math.min(248, SCREEN_WIDTH - 24);
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

  const width = Math.max(
    TOOLTIP_MIN_WIDTH,
    Math.min(TOOLTIP_MAX_WIDTH, mark.target.width + 36),
  );
  const estimatedHeight = estimateTooltipHeight(mark, width);

  // Horizontal: center tooltip on target, but clamp to screen
  let left = mark.target.x - width / 2;
  left = Math.max(TOOLTIP_PADDING, Math.min(left, SCREEN_WIDTH - width - TOOLTIP_PADDING));

  // Vertical
  let top: number;
  if (placement === 'below') {
    top = targetBottom + GAP + ARROW_SIZE;
    top = Math.min(top, SCREEN_HEIGHT - insets.bottom - estimatedHeight - 12);
  } else {
    // Place above — tooltip bottom edge above target top
    top = targetTop - GAP - ARROW_SIZE - estimatedHeight;
    top = Math.max(insets.top + 8, top);
  }

  // Arrow: point at target center X, relative to tooltip left
  const arrowOffsetX = Math.max(22, Math.min(mark.target.x - left - ARROW_SIZE / 2, width - 30));

  return {
    top,
    left,
    width,
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
    width: '100%',
    backgroundColor: 'rgba(26, 26, 24, 0.95)',
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 15,
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 20,
  },
  iconRow: {
    flexDirection: 'row',
    gap: 12,
    alignItems: 'flex-start',
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
    minWidth: 0,
    flexShrink: 1,
    gap: 4,
  },
  tooltipTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: -0.2,
  },
  tooltipBody: {
    fontSize: 12.5,
    color: 'rgba(255, 255, 255, 0.82)',
    lineHeight: 18,
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
    fontSize: 10.5,
    color: 'rgba(255, 255, 255, 0.48)',
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
