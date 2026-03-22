/**
 * MapToolsDrawer — Slide-out panel for secondary map tools.
 *
 * Design principle: "Kitchen Sink" avoidance (Gaigg Ch.7).
 * Only essential controls stay visible on the map. Everything else
 * lives in this drawer, accessed via a single "tools" button.
 *
 * Progressive disclosure: tools are grouped and contextually filtered.
 * Active overlays show mini info cards with live data (wind speed,
 * radar timestamp, tide times, weather summary).
 */
import React, { useRef, useEffect } from 'react';
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { hapticLight } from '../utils/haptics';

const { width: SCREEN_WIDTH } = Dimensions.get('window');
const DRAWER_WIDTH = Math.min(300, SCREEN_WIDTH * 0.76);

// ── Tool definitions ─────────────────────────────────────────────

/** Mini info card data shown below active overlay tools */
export interface OverlayInfoCard {
  /** Primary value (e.g. "12 mph NW" for wind) */
  primary: string;
  /** Secondary detail (e.g. "Gusting to 18 mph") */
  secondary?: string;
  /** Icon name (Ionicons) */
  icon: string;
  /** Tint color for the icon and primary text */
  color?: string;
}

export interface MapTool {
  key: string;
  label: string;
  icon: string;
  description: string;
  isActive?: boolean;
  isLoading?: boolean;
  onPress: () => void;
  /** If true, tool is only shown when contextually relevant */
  contextual?: boolean;
  /** If false, tool is hidden (contextually not relevant right now) */
  visible?: boolean;
  /** Mini info card shown when this overlay is active */
  infoCard?: OverlayInfoCard | null;
}

export interface MapToolGroup {
  title: string;
  tools: MapTool[];
}

interface MapToolsDrawerProps {
  visible: boolean;
  onClose: () => void;
  toolGroups: MapToolGroup[];
}

export function MapToolsDrawer({ visible, onClose, toolGroups }: MapToolsDrawerProps) {
  const slideAnim = useRef(new Animated.Value(DRAWER_WIDTH)).current;
  const backdropAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.spring(slideAnim, {
          toValue: 0,
          friction: 9,
          tension: 65,
          useNativeDriver: true,
        }),
        Animated.timing(backdropAnim, {
          toValue: 1,
          duration: 200,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.spring(slideAnim, {
          toValue: DRAWER_WIDTH,
          friction: 9,
          tension: 65,
          useNativeDriver: true,
        }),
        Animated.timing(backdropAnim, {
          toValue: 0,
          duration: 150,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [visible, slideAnim, backdropAnim]);

  if (!visible) return null;

  // Filter groups to only show groups with visible tools
  const visibleGroups = toolGroups
    .map((group) => ({
      ...group,
      tools: group.tools.filter((t) => t.visible !== false),
    }))
    .filter((group) => group.tools.length > 0);

  return (
    <View style={styles.overlay} pointerEvents={visible ? 'auto' : 'none'}>
      {/* Backdrop */}
      <Animated.View style={[styles.backdrop, { opacity: backdropAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Drawer */}
      <Animated.View
        style={[
          styles.drawer,
          { transform: [{ translateX: slideAnim }] },
        ]}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Map Tools</Text>
          <Pressable onPress={onClose} style={styles.closeButton}>
            <Ionicons name="close" size={22} color={palette.textSecondary} />
          </Pressable>
        </View>

        {/* Tool groups */}
        <ScrollView
          style={styles.scrollArea}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {visibleGroups.map((group) => (
            <View key={group.title} style={styles.group}>
              <Text style={styles.groupTitle}>{group.title}</Text>
              {group.tools.map((tool) => (
                <View key={tool.key}>
                  <Pressable
                    style={({ pressed }) => [
                      styles.toolRow,
                      tool.isActive && styles.toolRowActive,
                      pressed && styles.toolRowPressed,
                    ]}
                    onPress={() => {
                      hapticLight();
                      tool.onPress();
                    }}
                    accessibilityLabel={`${tool.label}: ${tool.description}`}
                  >
                    <View
                      style={[
                        styles.toolIcon,
                        tool.isActive && styles.toolIconActive,
                      ]}
                    >
                      <Ionicons
                        name={tool.icon as any}
                        size={18}
                        color={tool.isActive ? '#FFFFFF' : palette.textSecondary}
                      />
                    </View>
                    <View style={styles.toolTextArea}>
                      <Text
                        style={[
                          styles.toolLabel,
                          tool.isActive && styles.toolLabelActive,
                        ]}
                      >
                        {tool.label}
                      </Text>
                      <Text style={styles.toolDescription} numberOfLines={1}>
                        {tool.description}
                      </Text>
                    </View>
                    {tool.isActive && (
                      <View style={styles.activeDot} />
                    )}
                    {tool.isLoading && (
                      <View style={styles.loadingDot} />
                    )}
                  </Pressable>

                  {/* Mini info card for active overlay */}
                  {tool.isActive && tool.infoCard && (
                    <View style={styles.infoCard}>
                      <Ionicons
                        name={tool.infoCard.icon as any}
                        size={14}
                        color={tool.infoCard.color || palette.accent}
                      />
                      <View style={styles.infoCardTextArea}>
                        <Text
                          style={[
                            styles.infoCardPrimary,
                            tool.infoCard.color ? { color: tool.infoCard.color } : undefined,
                          ]}
                          numberOfLines={1}
                        >
                          {tool.infoCard.primary}
                        </Text>
                        {tool.infoCard.secondary ? (
                          <Text style={styles.infoCardSecondary} numberOfLines={1}>
                            {tool.infoCard.secondary}
                          </Text>
                        ) : null}
                      </View>
                    </View>
                  )}
                  {tool.isActive && tool.isLoading && !tool.infoCard && (
                    <View style={styles.infoCard}>
                      <ActivityIndicator size="small" color={palette.accent} />
                      <Text style={styles.infoCardSecondary}>Loading data...</Text>
                    </View>
                  )}
                </View>
              ))}
            </View>
          ))}
          <View style={{ height: 40 }} />
        </ScrollView>
      </Animated.View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 200,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0, 0, 0, 0.3)',
  },
  drawer: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    width: DRAWER_WIDTH,
    backgroundColor: palette.background,
    borderTopLeftRadius: 20,
    borderBottomLeftRadius: 20,
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 20,
    shadowOffset: { width: -4, height: 0 },
    elevation: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: Platform.OS === 'ios' ? 60 : 40,
    paddingHorizontal: 20,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border ?? '#E8E8E8',
  },
  headerTitle: {
    fontFamily: 'PlayfairDisplay-Regular',
    fontSize: 20,
    color: palette.text,
    letterSpacing: -0.3,
  },
  closeButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    paddingHorizontal: 16,
    paddingTop: 16,
    gap: 20,
  },
  group: {
    gap: 6,
  },
  groupTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    paddingLeft: 4,
    marginBottom: 4,
  },
  toolRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 10,
    gap: 12,
  },
  toolRowActive: {
    backgroundColor: palette.accent + '12',
  },
  toolRowPressed: {
    opacity: 0.7,
  },
  toolIcon: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  toolIconActive: {
    backgroundColor: palette.accent,
  },
  toolTextArea: {
    flex: 1,
    gap: 1,
  },
  toolLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  toolLabelActive: {
    color: palette.accent,
  },
  toolDescription: {
    fontSize: 11,
    color: palette.textMuted,
  },
  activeDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: palette.accent,
  },
  loadingDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#FB8C00',
  },
  // ── Overlay info card ────────────────────────────────────────────
  infoCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginLeft: 56, // Align with tool text (34px icon + 10px padding + 12px gap)
    marginTop: 2,
    marginBottom: 6,
    backgroundColor: palette.surface,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderLight,
  },
  infoCardTextArea: {
    flex: 1,
    gap: 1,
  },
  infoCardPrimary: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.text,
  },
  infoCardSecondary: {
    fontSize: 10,
    color: palette.textMuted,
  },
});
