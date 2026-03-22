/**
 * MapLongPressMenu — Contextual popup when user long-presses on the map.
 *
 * Provides quick actions at the pressed location:
 * - Drop Pin (save waypoint)
 * - Get Conditions (weather at that point)
 * - Add to Route (add waypoint to current route)
 * - What's Here? (nearby POIs)
 * - Measure Distance (start measurement from this point)
 */
import React, { useEffect, useRef } from 'react';
import {
  Animated,
  Dimensions,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { hapticLight } from '../utils/haptics';

const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

export interface LongPressCoordinate {
  latitude: number;
  longitude: number;
  /** Screen X where user pressed */
  screenX?: number;
  /** Screen Y where user pressed */
  screenY?: number;
}

export type LongPressAction =
  | 'drop-pin'
  | 'get-conditions'
  | 'add-to-route'
  | 'whats-here'
  | 'measure-distance';

interface MenuAction {
  key: LongPressAction;
  label: string;
  icon: string;
  color: string;
}

const MENU_ACTIONS: MenuAction[] = [
  { key: 'drop-pin', label: 'Drop Pin', icon: 'pin', color: '#2E7D32' },
  { key: 'get-conditions', label: 'Get Conditions', icon: 'partly-sunny', color: '#1565C0' },
  { key: 'add-to-route', label: 'Add to Route', icon: 'navigate', color: '#E65100' },
  { key: 'whats-here', label: "What's Here?", icon: 'search', color: '#7B1FA2' },
  { key: 'measure-distance', label: 'Measure Distance', icon: 'resize', color: '#455A64' },
];

const MENU_WIDTH = 200;
const MENU_ITEM_HEIGHT = 44;

interface MapLongPressMenuProps {
  visible: boolean;
  coordinate: LongPressCoordinate | null;
  onAction: (action: LongPressAction, coordinate: LongPressCoordinate) => void;
  onClose: () => void;
}

export function MapLongPressMenu({
  visible,
  coordinate,
  onAction,
  onClose,
}: MapLongPressMenuProps) {
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.85)).current;

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 180,
          useNativeDriver: true,
        }),
        Animated.spring(scaleAnim, {
          toValue: 1,
          friction: 8,
          tension: 100,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      Animated.timing(fadeAnim, {
        toValue: 0,
        duration: 120,
        useNativeDriver: true,
      }).start();
    }
  }, [visible, fadeAnim, scaleAnim]);

  if (!visible || !coordinate) return null;

  // Compute menu position from screen tap location
  const menuHeight = MENU_ACTIONS.length * MENU_ITEM_HEIGHT + 48; // items + header
  const screenX = coordinate.screenX ?? SCREEN_WIDTH / 2;
  const screenY = coordinate.screenY ?? SCREEN_HEIGHT / 2;

  // Position: prefer below-right of tap, but clamp to screen
  let menuLeft = screenX - MENU_WIDTH / 2;
  let menuTop = screenY + 12;

  // Clamp horizontal
  menuLeft = Math.max(12, Math.min(menuLeft, SCREEN_WIDTH - MENU_WIDTH - 12));

  // If menu would go off bottom, show above
  if (menuTop + menuHeight > SCREEN_HEIGHT - 100) {
    menuTop = screenY - menuHeight - 12;
  }
  // Clamp vertical
  menuTop = Math.max(Platform.OS === 'ios' ? 60 : 40, menuTop);

  return (
    <Animated.View style={[styles.overlay, { opacity: fadeAnim }]}>
      {/* Backdrop */}
      <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />

      {/* Tap indicator dot */}
      <View
        style={[
          styles.tapDot,
          { left: screenX - 6, top: screenY - 6 },
        ]}
      />

      {/* Menu card */}
      <Animated.View
        style={[
          styles.menuCard,
          {
            left: menuLeft,
            top: menuTop,
            transform: [{ scale: scaleAnim }],
          },
        ]}
      >
        {/* Coordinate header */}
        <View style={styles.menuHeader}>
          <Ionicons name="location" size={12} color={palette.textMuted} />
          <Text style={styles.menuHeaderText} numberOfLines={1}>
            {coordinate.latitude.toFixed(5)}, {coordinate.longitude.toFixed(5)}
          </Text>
        </View>

        {/* Actions */}
        {MENU_ACTIONS.map((action) => (
          <Pressable
            key={action.key}
            style={({ pressed }) => [
              styles.menuItem,
              pressed && styles.menuItemPressed,
            ]}
            onPress={() => {
              hapticLight();
              onAction(action.key, coordinate);
              onClose();
            }}
          >
            <View style={[styles.menuItemIcon, { backgroundColor: action.color + '15' }]}>
              <Ionicons name={action.icon as any} size={16} color={action.color} />
            </View>
            <Text style={styles.menuItemLabel}>{action.label}</Text>
          </Pressable>
        ))}
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 500,
    elevation: 500,
  },
  tapDot: {
    position: 'absolute',
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: palette.accent,
    borderWidth: 2,
    borderColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.25,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 8,
  },
  menuCard: {
    position: 'absolute',
    width: MENU_WIDTH,
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    paddingVertical: 6,
    shadowColor: '#000',
    shadowOpacity: 0.18,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 16,
  },
  menuHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
    marginBottom: 2,
  },
  menuHeaderText: {
    fontSize: 11,
    color: palette.textMuted,
    fontVariant: ['tabular-nums'],
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  menuItemPressed: {
    backgroundColor: palette.surfaceRaised,
  },
  menuItemIcon: {
    width: 28,
    height: 28,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  menuItemLabel: {
    fontSize: 14,
    fontWeight: '500',
    color: palette.text,
  },
});
