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

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

// Logo sizing — ~60% of screen width
const LOGO_WIDTH = SCREEN_W * 0.6;
// Aspect ratio from 2560x1396
const LOGO_ASPECT = 1396 / 2560;
const LOGO_HEIGHT = LOGO_WIDTH * LOGO_ASPECT;

// ── Realistic water ripple config ────────────────────────────────────
// Multiple ripple rings with non-uniform spacing, variable speed & opacity
// to simulate real water surface disturbance.

interface RippleConfig {
  delay: number;       // stagger from ripple start (ms)
  duration: number;    // how long this ring expands (ms)
  maxScale: number;    // final scale (non-uniform sizing)
  peakOpacity: number; // max opacity before fade
  borderWidth: number; // ring thickness
}

const RIPPLE_CONFIGS: RippleConfig[] = [
  // Inner fast ripples — tight, bright, quick
  { delay: 0,    duration: 1100, maxScale: 0.15, peakOpacity: 0.45, borderWidth: 2.5 },
  { delay: 80,   duration: 1200, maxScale: 0.22, peakOpacity: 0.38, borderWidth: 2.0 },
  { delay: 180,  duration: 1300, maxScale: 0.32, peakOpacity: 0.32, borderWidth: 1.8 },
  // Mid ripples — medium pace
  { delay: 260,  duration: 1400, maxScale: 0.44, peakOpacity: 0.26, borderWidth: 1.5 },
  { delay: 360,  duration: 1500, maxScale: 0.56, peakOpacity: 0.22, borderWidth: 1.3 },
  { delay: 450,  duration: 1550, maxScale: 0.68, peakOpacity: 0.18, borderWidth: 1.2 },
  // Outer slow ripples — wide, faint, lingering
  { delay: 550,  duration: 1650, maxScale: 0.82, peakOpacity: 0.14, borderWidth: 1.0 },
  { delay: 650,  duration: 1750, maxScale: 0.95, peakOpacity: 0.10, borderWidth: 0.8 },
  { delay: 760,  duration: 1800, maxScale: 1.05, peakOpacity: 0.07, borderWidth: 0.6 },
  { delay: 880,  duration: 1900, maxScale: 1.15, peakOpacity: 0.04, borderWidth: 0.5 },
];

const RIPPLE_MAX_RADIUS = Math.max(SCREEN_W, SCREEN_H) * 0.75;

// Blue wash config
const WASH_PEAK_OPACITY = 0.06;

// ── Timing budget (≈ 3.3 s total) ──────────────────────────────────
// Logo fade-in:   0 – 800 ms
// Ripple effect:  400 – 2400 ms  (overlaps logo tail for smoothness)
// Logo pulse:     400 – 2400 ms  (subtle scale oscillation during ripples)
// Blue wash:      400 – 2200 ms
// Fade-out:       2600 – 3100 ms
// onFinish fires: 3100 ms

interface SplashScreenProps {
  onFinish: () => void;
}

export function SplashScreen({ onFinish }: SplashScreenProps) {
  // ── Animated values ───────────────────────────────────────────────
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const logoScale = useRef(new Animated.Value(0.92)).current;

  // Ripple rings
  const ripples = useRef(
    RIPPLE_CONFIGS.map(() => ({
      scale: new Animated.Value(0),
      opacity: new Animated.Value(0),
    })),
  ).current;

  // Blue tint wash overlay
  const washOpacity = useRef(new Animated.Value(0)).current;

  // Logo pulse (subtle sine-wave scale oscillation during ripple)
  const logoPulse = useRef(new Animated.Value(1)).current;

  // Screen fade-out
  const screenOpacity = useRef(new Animated.Value(1)).current;

  const finishCalled = useRef(false);
  const handleFinish = useCallback(() => {
    if (finishCalled.current) return;
    finishCalled.current = true;
    onFinish();
  }, [onFinish]);

  useEffect(() => {
    // ── Phase 1: Logo entrance (0 – 800 ms) ────────────────────────
    const logoEntrance = Animated.parallel([
      Animated.timing(logoOpacity, {
        toValue: 1,
        duration: 800,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(logoScale, {
        toValue: 1,
        duration: 800,
        easing: Easing.out(Easing.back(1.05)),
        useNativeDriver: true,
      }),
    ]);

    // ── Phase 2: Water ripple rings (staggered, overlapping logo tail) ──
    const rippleAnimations = ripples.map((ripple, i) => {
      const cfg = RIPPLE_CONFIGS[i];
      return Animated.sequence([
        Animated.delay(cfg.delay),
        Animated.parallel([
          // Scale outward
          Animated.timing(ripple.scale, {
            toValue: cfg.maxScale,
            duration: cfg.duration,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
          }),
          // Quick fade-in then slow fade-out
          Animated.sequence([
            Animated.timing(ripple.opacity, {
              toValue: cfg.peakOpacity,
              duration: cfg.duration * 0.12,
              easing: Easing.out(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(ripple.opacity, {
              toValue: 0,
              duration: cfg.duration * 0.88,
              easing: Easing.out(Easing.cubic),
              useNativeDriver: true,
            }),
          ]),
        ]),
      ]);
    });

    const ripplePhase = Animated.parallel(rippleAnimations);

    // ── Blue wash (tint overlay) ────────────────────────────────────
    const blueWash = Animated.sequence([
      Animated.timing(washOpacity, {
        toValue: WASH_PEAK_OPACITY,
        duration: 800,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
      Animated.timing(washOpacity, {
        toValue: 0,
        duration: 1000,
        easing: Easing.in(Easing.quad),
        useNativeDriver: true,
      }),
    ]);

    // ── Logo pulse (subtle breathing scale during ripple) ───────────
    const pulse = Animated.sequence([
      Animated.timing(logoPulse, {
        toValue: 1.03,
        duration: 500,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
      Animated.timing(logoPulse, {
        toValue: 0.98,
        duration: 500,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
      Animated.timing(logoPulse, {
        toValue: 1.02,
        duration: 400,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
      Animated.timing(logoPulse, {
        toValue: 1.0,
        duration: 400,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
    ]);

    // ── Phase 3: Fade-out ───────────────────────────────────────────
    const fadeOut = Animated.timing(screenOpacity, {
      toValue: 0,
      duration: 500,
      easing: Easing.in(Easing.cubic),
      useNativeDriver: true,
    });

    // ── Compose full sequence ───────────────────────────────────────
    const fullAnimation = Animated.sequence([
      // Logo fades in (800 ms)
      logoEntrance,
      // Short breath before ripples
      Animated.delay(100),
      // Ripples + wash + logo pulse all run together (≈ 2000 ms)
      Animated.parallel([ripplePhase, blueWash, pulse]),
      // Brief hold
      Animated.delay(100),
      // Fade everything out (500 ms)
      fadeOut,
    ]);

    fullAnimation.start(() => handleFinish());

    // Safety fallback — 5 s hard cap
    const fallback = setTimeout(handleFinish, 5000);
    return () => {
      clearTimeout(fallback);
      fullAnimation.stop();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Combined logo transform: entrance scale * pulse oscillation
  const combinedLogoScale = Animated.multiply(logoScale, logoPulse);

  return (
    <Animated.View style={[styles.container, { opacity: screenOpacity }]}>
      {/* Logo */}
      <Animated.View
        style={[
          styles.logoContainer,
          {
            opacity: logoOpacity,
            transform: [{ scale: combinedLogoScale }],
          },
        ]}
      >
        <Image
          source={require('../../assets/logo.png')}
          style={styles.logoImage}
          resizeMode="contain"
        />
      </Animated.View>

      {/* Water ripple rings */}
      {ripples.map((ripple, i) => (
        <Animated.View
          key={i}
          pointerEvents="none"
          style={[
            styles.ripple,
            {
              width: RIPPLE_MAX_RADIUS * 2,
              height: RIPPLE_MAX_RADIUS * 2,
              borderRadius: RIPPLE_MAX_RADIUS,
              borderWidth: RIPPLE_CONFIGS[i].borderWidth,
              opacity: ripple.opacity,
              transform: [{ scale: ripple.scale }],
            },
          ]}
        />
      ))}

      {/* Subtle blue tint wash overlay */}
      <Animated.View
        pointerEvents="none"
        style={[styles.blueWash, { opacity: washOpacity }]}
      />
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
  logoContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  logoImage: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
  ripple: {
    position: 'absolute',
    borderColor: palette.accent,
    backgroundColor: `${palette.accent}06`,
  },
  blueWash: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: palette.accent,
  },
});
