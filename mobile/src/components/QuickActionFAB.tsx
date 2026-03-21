import React, { useRef, useState } from 'react';
import {
  Animated,
  Pressable,
  StyleSheet,
  Text,
  View,
  Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { hapticMedium, hapticLight } from '../utils/haptics';

interface FABAction {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  color?: string; // optional — falls back to palette.accent
  onPress: () => void;
}

interface QuickActionFABProps {
  actions: FABAction[];
}

export function QuickActionFAB({ actions }: QuickActionFABProps) {
  const [expanded, setExpanded] = useState(false);
  const animation = useRef(new Animated.Value(0)).current;
  const rotation = animation.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '45deg'],
  });

  const toggleMenu = () => {
    hapticMedium();
    const toValue = expanded ? 0 : 1;
    Animated.spring(animation, {
      toValue,
      friction: 6,
      tension: 80,
      useNativeDriver: true,
    }).start();
    setExpanded(!expanded);
  };

  const handleAction = (action: FABAction) => {
    hapticLight();
    // Collapse first, then trigger action
    Animated.timing(animation, {
      toValue: 0,
      duration: 150,
      useNativeDriver: true,
    }).start(() => {
      setExpanded(false);
      action.onPress();
    });
  };

  return (
    <View style={styles.container} pointerEvents="box-none">
      {/* Backdrop */}
      {expanded && (
        <Pressable style={styles.backdrop} onPress={toggleMenu} />
      )}

      {/* Main FAB (rendered first so action items are on top with higher zIndex) */}
      <Pressable onPress={toggleMenu} style={[styles.mainButton, { zIndex: 1 }]}>
        <Animated.View style={[styles.mainButtonInner, { transform: [{ rotate: rotation }] }]}>
          <Ionicons name="add" size={28} color="#FFFFFF" />
        </Animated.View>
      </Pressable>

      {/* Action items — expand UPWARD from the FAB, labels to the LEFT of icons */}
      {actions.map((action, index) => {
        const reverseIndex = actions.length - index;
        const translateY = animation.interpolate({
          inputRange: [0, 1],
          outputRange: [0, -(reverseIndex * 52)],
        });
        const scale = animation.interpolate({
          inputRange: [0, 0.4, 1],
          outputRange: [0, 0, 1],
        });
        const opacity = animation.interpolate({
          inputRange: [0, 0.4, 1],
          outputRange: [0, 0, 1],
        });

        return (
          <Animated.View
            key={action.key}
            style={[
              styles.actionRow,
              {
                transform: [{ translateY }, { scale }],
                opacity,
                zIndex: 10,
              },
            ]}
            pointerEvents={expanded ? 'auto' : 'none'}
          >
            <Pressable
              style={styles.actionLabel}
              onPress={() => handleAction(action)}
            >
              <Text style={styles.actionLabelText}>{action.label}</Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, { backgroundColor: palette.accent }]}
              onPress={() => handleAction(action)}
            >
              <Ionicons name={action.icon} size={18} color="#FFFFFF" />
            </Pressable>
          </Animated.View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 110 : 80,
    right: 16,
    alignItems: 'flex-end',
    zIndex: 50,
    // Ensure enough width for labels + icon without clipping
    width: 200,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    // Invisible but catches taps
  },
  mainButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: palette.accent,
    shadowOpacity: 0.35,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
  },
  mainButtonInner: {
    width: 48,
    height: 48,
    borderRadius: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionRow: {
    position: 'absolute',
    bottom: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  actionLabel: {
    backgroundColor: 'rgba(255, 255, 255, 0.96)',
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 4,
  },
  actionLabelText: {
    color: palette.text,
    fontSize: 13,
    fontWeight: '600',
  },
  actionButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.15,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 6,
  },
});
