import React, { useEffect, useRef, useCallback } from 'react';
import {
  View,
  StyleSheet,
  Animated,
  Easing,
  Dimensions,
  Image,
} from 'react-native';
import { palette } from '../theme/palette';

let ExpoSplashScreen: any = null;
try { ExpoSplashScreen = require('expo-splash-screen'); } catch {}

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

const LOGO_WIDTH = SCREEN_W * 0.55;
const LOGO_ASPECT = 1396 / 2560;
const LOGO_HEIGHT = LOGO_WIDTH * LOGO_ASPECT;
const MAX_SIZE = Math.max(SCREEN_W, SCREEN_H) * 1.4;

// ── Ripple ring configs ──────────────────────────────────────────────────
// 12 rings: tight inner cluster + wide outer spread
// Simulates real water where inner rings are close together and bright,
// outer rings spread wide and are barely visible
const RINGS = [
  // Inner cluster — fast, bright, tight
  { delay: 0,    dur: 900,  peak: 0.50, border: 3.0, fill: 0.06 },
  { delay: 80,   dur: 950,  peak: 0.42, border: 2.5, fill: 0.05 },
  { delay: 160,  dur: 1000, peak: 0.35, border: 2.2, fill: 0.04 },
  { delay: 250,  dur: 1050, peak: 0.28, border: 1.8, fill: 0.03 },
  // Mid range — moderate
  { delay: 360,  dur: 1150, peak: 0.22, border: 1.5, fill: 0.02 },
  { delay: 480,  dur: 1250, peak: 0.17, border: 1.2, fill: 0.015 },
  { delay: 620,  dur: 1350, peak: 0.13, border: 1.0, fill: 0.01 },
  { delay: 780,  dur: 1450, peak: 0.10, border: 0.8, fill: 0.008 },
  // Outer — slow, ghostly, wide
  { delay: 960,  dur: 1600, peak: 0.07, border: 0.6, fill: 0.005 },
  { delay: 1150, dur: 1750, peak: 0.05, border: 0.5, fill: 0.003 },
  { delay: 1350, dur: 1900, peak: 0.03, border: 0.4, fill: 0.002 },
  { delay: 1550, dur: 2050, peak: 0.02, border: 0.3, fill: 0.001 },
];

// Second drop — offset, delayed by 800ms
const DROP2_OFFSET = { x: -SCREEN_W * 0.08, y: SCREEN_H * 0.04 };
const DROP2_RINGS = RINGS.slice(0, 8).map(r => ({
  ...r,
  delay: r.delay + 800,
  peak: r.peak * 0.6,
  fill: r.fill * 0.5,
}));

const ALL_RINGS = [...RINGS, ...DROP2_RINGS];

interface SplashScreenProps {
  onFinish: () => void;
}

export function SplashScreen({ onFinish }: SplashScreenProps) {
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const logoScale = useRef(new Animated.Value(0.9)).current;
  const screenOpacity = useRef(new Animated.Value(1)).current;
  // Subtle logo "float" on water surface
  const logoFloat = useRef(new Animated.Value(0)).current;
  // Blue wash overlay
  const washOpacity = useRef(new Animated.Value(0)).current;
  // Ring animations
  const ringAnims = useRef(ALL_RINGS.map(() => ({
    scale: new Animated.Value(0),
    opacity: new Animated.Value(0),
  }))).current;
  const finishCalled = useRef(false);

  const handleFinish = useCallback(() => {
    if (finishCalled.current) return;
    finishCalled.current = true;
    onFinish();
  }, [onFinish]);

  useEffect(() => {
    ExpoSplashScreen?.hideAsync?.().catch(() => {});

    // Phase 1: Logo entrance (0-700ms)
    Animated.parallel([
      Animated.timing(logoOpacity, { toValue: 1, duration: 700, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(logoScale, { toValue: 1, duration: 700, easing: Easing.out(Easing.back(1.08)), useNativeDriver: true }),
    ]).start();

    // Logo gentle float (simulates sitting on water)
    Animated.loop(
      Animated.sequence([
        Animated.timing(logoFloat, { toValue: 3, duration: 1200, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(logoFloat, { toValue: -2, duration: 1400, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(logoFloat, { toValue: 0, duration: 1000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ])
    ).start();

    // Phase 2: Ripple rings (staggered from 300ms)
    const ringAnimations = ringAnims.map((anim, i) => {
      const cfg = ALL_RINGS[i];
      return Animated.sequence([
        Animated.delay(300 + cfg.delay),
        Animated.parallel([
          // Scale: ease-out for natural deceleration
          Animated.timing(anim.scale, {
            toValue: 1,
            duration: cfg.dur,
            easing: Easing.out(Easing.quad),
            useNativeDriver: true,
          }),
          // Opacity: quick fade-in, slow fade-out (like real water)
          Animated.sequence([
            Animated.timing(anim.opacity, {
              toValue: cfg.peak,
              duration: cfg.dur * 0.2,
              easing: Easing.out(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(anim.opacity, {
              toValue: 0,
              duration: cfg.dur * 0.8,
              easing: Easing.in(Easing.cubic),
              useNativeDriver: true,
            }),
          ]),
        ]),
      ]);
    });

    Animated.stagger(0, ringAnimations).start();

    // Blue wash overlay — breathes in and out
    Animated.sequence([
      Animated.delay(400),
      Animated.timing(washOpacity, { toValue: 0.08, duration: 800, easing: Easing.out(Easing.quad), useNativeDriver: true }),
      Animated.timing(washOpacity, { toValue: 0.03, duration: 600, useNativeDriver: true }),
      Animated.timing(washOpacity, { toValue: 0.06, duration: 500, useNativeDriver: true }),
      Animated.timing(washOpacity, { toValue: 0, duration: 800, easing: Easing.in(Easing.quad), useNativeDriver: true }),
    ]).start();

    // Phase 3: Fade out
    Animated.sequence([
      Animated.delay(3200),
      Animated.timing(screenOpacity, { toValue: 0, duration: 500, easing: Easing.in(Easing.cubic), useNativeDriver: true }),
    ]).start(() => handleFinish());

    const fallback = setTimeout(handleFinish, 5000);
    return () => clearTimeout(fallback);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Animated.View style={[styles.container, { opacity: screenOpacity }]}>
      {/* Primary drop ripples (centered) */}
      <View style={styles.rippleCenter} pointerEvents="none">
        {ringAnims.slice(0, RINGS.length).map((anim, i) => (
          <Animated.View
            key={`r1-${i}`}
            style={[styles.ring, {
              width: MAX_SIZE,
              height: MAX_SIZE,
              borderRadius: MAX_SIZE / 2,
              borderWidth: RINGS[i].border,
              backgroundColor: `rgba(37, 116, 169, ${RINGS[i].fill})`,
              opacity: anim.opacity,
              transform: [{ scale: anim.scale }],
            }]}
          />
        ))}
      </View>

      {/* Second drop ripples (offset) */}
      <View style={[styles.rippleCenter, { left: DROP2_OFFSET.x, top: DROP2_OFFSET.y }]} pointerEvents="none">
        {ringAnims.slice(RINGS.length).map((anim, i) => (
          <Animated.View
            key={`r2-${i}`}
            style={[styles.ring, {
              width: MAX_SIZE * 0.85,
              height: MAX_SIZE * 0.85,
              borderRadius: MAX_SIZE * 0.85 / 2,
              borderWidth: DROP2_RINGS[i].border,
              backgroundColor: `rgba(37, 116, 169, ${DROP2_RINGS[i].fill})`,
              opacity: anim.opacity,
              transform: [{ scale: anim.scale }],
            }]}
          />
        ))}
      </View>

      {/* Blue wash overlay */}
      <Animated.View style={[styles.wash, { opacity: washOpacity }]} pointerEvents="none" />

      {/* Logo */}
      <Animated.View style={[styles.logoContainer, {
        opacity: logoOpacity,
        transform: [{ scale: logoScale }, { translateY: logoFloat }],
      }]}>
        <Image source={require('../../assets/logo.png')} style={styles.logoImage} resizeMode="contain" />
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
    justifyContent: 'center',
    alignItems: 'center',
  },
  rippleCenter: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
  },
  ring: {
    position: 'absolute',
    borderColor: palette.accent,
  },
  wash: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: palette.accent,
  },
  logoContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  logoImage: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
});
