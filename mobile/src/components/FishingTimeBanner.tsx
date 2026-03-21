/**
 * OpenCatch — Fishing Time Banner
 *
 * Slim animated banner that slides in from the top of MapScreen
 * when the current time falls within a good fishing window.
 *
 * Shows: bite rating icon, short message, time remaining.
 * Dismissible with tap. Auto-hides when the window passes.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  Animated,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import {
  checkAndNotify,
  dismissBanner,
  isBannerDismissed,
  type FishingBannerState,
} from '../services/fishingNotifications';

// ── Props ────────────────────────────────────────────────────────────────────

interface FishingTimeBannerProps {
  lat: number;
  lon: number;
}

// ── Rating config ────────────────────────────────────────────────────────────

const RATING_CONFIG: Record<
  'prime' | 'good' | 'fair',
  { color: string; bgColor: string; icon: string }
> = {
  prime: { color: '#E53935', bgColor: 'rgba(229,57,53,0.08)', icon: 'flame' },
  good: { color: '#FB8C00', bgColor: 'rgba(251,140,0,0.08)', icon: 'trending-up' },
  fair: { color: palette.accent, bgColor: palette.accentDim, icon: 'fish' },
};

// ── Component ────────────────────────────────────────────────────────────────

export function FishingTimeBanner({ lat, lon }: FishingTimeBannerProps) {
  const [banner, setBanner] = useState<FishingBannerState | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const slideAnim = useRef(new Animated.Value(-80)).current;
  const checkInterval = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check for active window on mount and every 5 minutes
  useEffect(() => {
    if (!lat || !lon) return;

    const check = async () => {
      const wasDismissed = await isBannerDismissed();
      if (wasDismissed) {
        setDismissed(true);
        return;
      }

      const state = checkAndNotify(lat, lon);
      setBanner(state);
    };

    check();
    checkInterval.current = setInterval(check, 5 * 60 * 1000);

    return () => {
      if (checkInterval.current) clearInterval(checkInterval.current);
    };
  }, [lat, lon]);

  // Animate slide-in/out
  useEffect(() => {
    const shouldShow = banner?.visible && !dismissed;

    Animated.spring(slideAnim, {
      toValue: shouldShow ? 0 : -80,
      useNativeDriver: true,
      tension: 80,
      friction: 12,
    }).start();
  }, [banner?.visible, dismissed, slideAnim]);

  // Auto-hide when window ends
  useEffect(() => {
    if (!banner?.windowEnd) return;

    const remaining = banner.windowEnd - Date.now();
    if (remaining <= 0) {
      setDismissed(true);
      return;
    }

    const timer = setTimeout(() => {
      setDismissed(true);
    }, remaining);

    return () => clearTimeout(timer);
  }, [banner?.windowEnd]);

  const handleDismiss = async () => {
    setDismissed(true);
    await dismissBanner();
  };

  if (!banner || !banner.visible || dismissed) return null;

  const config = RATING_CONFIG[banner.biteRating === 'none' ? 'fair' : banner.biteRating];

  return (
    <Animated.View
      style={[
        styles.container,
        { transform: [{ translateY: slideAnim }] },
      ]}
    >
      <Pressable style={[styles.banner, { borderLeftColor: config.color }]} onPress={handleDismiss}>
        <View style={[styles.iconContainer, { backgroundColor: config.bgColor }]}>
          <Ionicons name={config.icon as any} size={18} color={config.color} />
        </View>
        <View style={styles.content}>
          <Text style={styles.message} numberOfLines={1}>
            {banner.message}
          </Text>
          <Text style={[styles.timeRemaining, { color: config.color }]}>
            {banner.timeRemaining}
          </Text>
        </View>
        <View style={styles.scoreBadge}>
          <Text style={[styles.scoreText, { color: config.color }]}>{banner.score}</Text>
        </View>
        <Ionicons name="close" size={14} color={palette.textMuted} style={styles.closeIcon} />
      </Pressable>
    </Animated.View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 54 : 34,
    left: 16,
    right: 70, // Avoid overlapping right-side map buttons
    zIndex: 25,
  },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderLeftWidth: 3,
    shadowColor: '#000',
    shadowOpacity: 0.1,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 5,
    gap: 8,
  },
  iconContainer: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    flex: 1,
    gap: 1,
  },
  message: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },
  timeRemaining: {
    fontSize: 11,
    fontWeight: '500',
  },
  scoreBadge: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scoreText: {
    fontSize: 12,
    fontWeight: '700',
  },
  closeIcon: {
    marginLeft: 2,
  },
});
