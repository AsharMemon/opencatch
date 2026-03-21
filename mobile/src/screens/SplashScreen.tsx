import React, { useEffect, useRef, useCallback } from 'react';
import {
  View,
  StyleSheet,
  Animated,
  Easing,
  Dimensions,
  Image,
} from 'react-native';
import LottieView from 'lottie-react-native';
import * as ExpoSplashScreen from 'expo-splash-screen';
import { palette } from '../theme/palette';

const { width: SCREEN_W } = Dimensions.get('window');

// Logo sizing — ~60% of screen width
const LOGO_WIDTH = SCREEN_W * 0.6;
// Aspect ratio from 2560x1396
const LOGO_ASPECT = 1396 / 2560;
const LOGO_HEIGHT = LOGO_WIDTH * LOGO_ASPECT;

// Lottie ripple covers the full screen behind the logo
const LOTTIE_SIZE = Math.max(SCREEN_W * 1.4, 800);

// ── Timing budget (~ 3 s total) ──────────────────────────────────────
// Native splash hides:    0 ms
// Logo fade-in:           0 – 500 ms
// Lottie ripple plays:    200 – 2200 ms  (2 s loop, single play)
// Hold:                   2200 – 2500 ms
// Fade-out:               2500 – 3000 ms
// onFinish fires:         3000 ms

interface SplashScreenProps {
  onFinish: () => void;
}

export function SplashScreen({ onFinish }: SplashScreenProps) {
  // ── Animated values ─────────────────────────────────────────────────
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const logoScale = useRef(new Animated.Value(0.92)).current;
  const screenOpacity = useRef(new Animated.Value(1)).current;

  const lottieRef = useRef<LottieView>(null);
  const finishCalled = useRef(false);

  const handleFinish = useCallback(() => {
    if (finishCalled.current) return;
    finishCalled.current = true;
    onFinish();
  }, [onFinish]);

  useEffect(() => {
    // Hide the native static splash screen immediately
    ExpoSplashScreen.hideAsync().catch(() => {});

    // ── Phase 1: Logo entrance (0 – 500 ms) ──────────────────────────
    const logoEntrance = Animated.parallel([
      Animated.timing(logoOpacity, {
        toValue: 1,
        duration: 500,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(logoScale, {
        toValue: 1,
        duration: 500,
        easing: Easing.out(Easing.back(1.05)),
        useNativeDriver: true,
      }),
    ]);

    // Start the Lottie ripple slightly after logo begins fading in
    const startLottie = () => {
      if (lottieRef.current) {
        lottieRef.current.play();
      }
    };

    // ── Phase 2: Hold + Fade-out ──────────────────────────────────────
    const holdAndFade = Animated.sequence([
      Animated.delay(2500), // Wait for lottie + hold
      Animated.timing(screenOpacity, {
        toValue: 0,
        duration: 500,
        easing: Easing.in(Easing.cubic),
        useNativeDriver: true,
      }),
    ]);

    // Run logo entrance, then start lottie + fade sequence
    logoEntrance.start(() => {
      startLottie();
    });

    holdAndFade.start(() => {
      handleFinish();
    });

    // Safety fallback — 5 s hard cap
    const fallback = setTimeout(handleFinish, 5000);
    return () => {
      clearTimeout(fallback);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Animated.View style={[styles.container, { opacity: screenOpacity }]}>
      {/* Lottie ripple animation — behind logo */}
      <View style={styles.lottieContainer} pointerEvents="none">
        <LottieView
          ref={lottieRef}
          source={require('../../assets/animations/water-ripple.json')}
          style={styles.lottie}
          autoPlay={false}
          loop={false}
          speed={1}
          resizeMode="cover"
        />
      </View>

      {/* Logo — centered on top of ripple */}
      <Animated.View
        style={[
          styles.logoContainer,
          {
            opacity: logoOpacity,
            transform: [{ scale: logoScale }],
          },
        ]}
      >
        <Image
          source={require('../../assets/logo.png')}
          style={styles.logoImage}
          resizeMode="contain"
        />
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
  logoContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  logoImage: {
    width: LOGO_WIDTH,
    height: LOGO_HEIGHT,
  },
  lottieContainer: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 1,
  },
  lottie: {
    width: LOTTIE_SIZE,
    height: LOTTIE_SIZE,
  },
});
