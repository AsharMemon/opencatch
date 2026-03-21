/**
 * OpenCatch — Sun, Moon & Solunar Screen
 *
 * Displays astronomical data critical for fishing:
 * - Sunrise / sunset times with golden hour
 * - Moon phase with illumination %
 * - Solunar major/minor periods
 * - Best fishing windows based on lunar activity
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';

// ── Astronomical Calculations ────────────────────────────────────────────────

function julianDay(date: Date): number {
  const y = date.getUTCFullYear();
  const m = date.getUTCMonth() + 1;
  const d = date.getUTCDate() + (date.getUTCHours() + date.getUTCMinutes() / 60) / 24;
  const a = Math.floor((14 - m) / 12);
  const yp = y + 4800 - a;
  const mp = m + 12 * a - 3;
  return d + Math.floor((153 * mp + 2) / 5) + 365 * yp + Math.floor(yp / 4) - Math.floor(yp / 100) + Math.floor(yp / 400) - 32045;
}

function getMoonPhase(date: Date): { phase: number; name: string; illumination: number; emoji: string } {
  const jd = julianDay(date);
  const daysSinceNew = (jd - 2451550.1) % 29.530588853;
  const phase = daysSinceNew / 29.530588853;
  const illumination = Math.round((1 - Math.cos(phase * 2 * Math.PI)) / 2 * 100);

  let name: string;
  let emoji: string;
  if (phase < 0.0625) { name = 'New Moon'; emoji = '🌑'; }
  else if (phase < 0.1875) { name = 'Waxing Crescent'; emoji = '🌒'; }
  else if (phase < 0.3125) { name = 'First Quarter'; emoji = '🌓'; }
  else if (phase < 0.4375) { name = 'Waxing Gibbous'; emoji = '🌔'; }
  else if (phase < 0.5625) { name = 'Full Moon'; emoji = '🌕'; }
  else if (phase < 0.6875) { name = 'Waning Gibbous'; emoji = '🌖'; }
  else if (phase < 0.8125) { name = 'Last Quarter'; emoji = '🌗'; }
  else if (phase < 0.9375) { name = 'Waning Crescent'; emoji = '🌘'; }
  else { name = 'New Moon'; emoji = '🌑'; }

  return { phase, name, illumination, emoji };
}

function getSunTimes(date: Date, lat: number, lon: number) {
  const jd = julianDay(date);
  const n = jd - 2451545.0 + 0.0008;
  const jStar = n - lon / 360;
  const mDeg = (357.5291 + 0.98560028 * jStar) % 360;
  const mRad = mDeg * Math.PI / 180;
  const c = 1.9148 * Math.sin(mRad) + 0.02 * Math.sin(2 * mRad) + 0.0003 * Math.sin(3 * mRad);
  const lambdaDeg = (mDeg + c + 180 + 102.9372) % 360;
  const lambdaRad = lambdaDeg * Math.PI / 180;
  const sinDecl = Math.sin(lambdaRad) * Math.sin(23.4397 * Math.PI / 180);
  const cosDecl = Math.cos(Math.asin(sinDecl));
  const latRad = lat * Math.PI / 180;

  const cosHourAngle = (Math.sin(-0.833 * Math.PI / 180) - Math.sin(latRad) * sinDecl) / (Math.cos(latRad) * cosDecl);

  if (cosHourAngle > 1 || cosHourAngle < -1) {
    return null; // Polar day/night
  }

  const hourAngle = Math.acos(cosHourAngle) * 180 / Math.PI;
  const jTransit = 2451545.0 + jStar + 0.0053 * Math.sin(mRad) - 0.0069 * Math.sin(2 * lambdaRad);
  const jRise = jTransit - hourAngle / 360;
  const jSet = jTransit + hourAngle / 360;

  const toDate = (jd: number) => {
    const ms = (jd - 2440587.5) * 86400000;
    return new Date(ms);
  };

  const sunrise = toDate(jRise);
  const sunset = toDate(jSet);
  const solarNoon = toDate(jTransit);

  // Golden hour: ~1 hour after sunrise, ~1 hour before sunset
  const goldenMorningEnd = new Date(sunrise.getTime() + 60 * 60 * 1000);
  const goldenEveningStart = new Date(sunset.getTime() - 60 * 60 * 1000);

  return { sunrise, sunset, solarNoon, goldenMorningEnd, goldenEveningStart };
}

function getSolunarPeriods(date: Date, lat: number, lon: number) {
  // Simplified solunar calculation based on moon transit
  const moon = getMoonPhase(date);
  const hours = date.getHours();

  // Major periods: moonrise and moonset (approx 2 hours)
  // Minor periods: moon overhead and underfoot (approx 1 hour)
  // Using simplified approximation based on lunar day offset
  const lunarDayOffset = (moon.phase * 24.8) % 24.8;

  const majorPeriods = [
    { start: (6 + lunarDayOffset) % 24, label: 'Major Period 1' },
    { start: (18 + lunarDayOffset) % 24, label: 'Major Period 2' },
  ];

  const minorPeriods = [
    { start: (0 + lunarDayOffset) % 24, label: 'Minor Period 1' },
    { start: (12 + lunarDayOffset) % 24, label: 'Minor Period 2' },
  ];

  const formatHour = (h: number) => {
    const hr = Math.floor(h) % 12 || 12;
    const min = Math.round((h % 1) * 60);
    const ampm = Math.floor(h) % 24 >= 12 ? 'PM' : 'AM';
    return `${hr}:${min.toString().padStart(2, '0')} ${ampm}`;
  };

  return {
    major: majorPeriods.map(p => ({
      ...p,
      startTime: formatHour(p.start),
      endTime: formatHour(p.start + 2),
      duration: '2 hours',
    })),
    minor: minorPeriods.map(p => ({
      ...p,
      startTime: formatHour(p.start),
      endTime: formatHour(p.start + 1),
      duration: '1 hour',
    })),
  };
}

function getFishingRating(moonPhase: number, illumination: number): { rating: string; stars: number; tip: string } {
  // New and full moons are best for fishing
  const distFromNewFull = Math.min(moonPhase, Math.abs(moonPhase - 0.5), 1 - moonPhase);

  if (distFromNewFull < 0.08) return { rating: 'Excellent', stars: 5, tip: 'New/full moon — peak solunar activity. Fish feed aggressively.' };
  if (distFromNewFull < 0.15) return { rating: 'Very Good', stars: 4, tip: 'Near new/full moon. Strong feeding activity expected.' };
  if (distFromNewFull < 0.25) return { rating: 'Good', stars: 3, tip: 'Moderate lunar influence. Focus on major solunar periods.' };
  return { rating: 'Fair', stars: 2, tip: 'Quarter moon phase. Best action during dawn/dusk transitions.' };
}

// ── Component ────────────────────────────────────────────────────────────────

export function SunMoonScreen() {
  const now = new Date();
  const lat = 44.98; // Default — would use user location
  const lon = -93.27;

  const moon = useMemo(() => getMoonPhase(now), []);
  const sun = useMemo(() => getSunTimes(now, lat, lon), []);
  const solunar = useMemo(() => getSolunarPeriods(now, lat, lon), []);
  const fishingRating = useMemo(() => getFishingRating(moon.phase, moon.illumination), []);

  const formatTime = (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

  // Moon phase SVG
  const renderMoonPhase = () => {
    const size = 120;
    const r = size / 2 - 8;
    const cx = size / 2;
    const cy = size / 2;
    const phase = moon.phase;

    // Calculate illuminated portion path
    let d: string;
    const sweep = phase < 0.5 ? 1 : 0;
    const innerR = Math.abs(r * (1 - 4 * Math.abs(phase - 0.5)));

    if (phase < 0.25) {
      d = `M ${cx} ${cy - r} A ${r} ${r} 0 1 0 ${cx} ${cy + r} A ${innerR} ${r} 0 0 1 ${cx} ${cy - r}`;
    } else if (phase < 0.5) {
      d = `M ${cx} ${cy - r} A ${r} ${r} 0 1 0 ${cx} ${cy + r} A ${innerR} ${r} 0 0 0 ${cx} ${cy - r}`;
    } else if (phase < 0.75) {
      d = `M ${cx} ${cy - r} A ${r} ${r} 0 0 1 ${cx} ${cy + r} A ${innerR} ${r} 0 0 1 ${cx} ${cy - r}`;
    } else {
      d = `M ${cx} ${cy - r} A ${r} ${r} 0 0 1 ${cx} ${cy + r} A ${innerR} ${r} 0 0 0 ${cx} ${cy - r}`;
    }

    return (
      <Svg width={size} height={size}>
        <Circle cx={cx} cy={cy} r={r} fill="#1A1A2E" />
        <Path d={d} fill="#F5E6CA" />
        <Circle cx={cx} cy={cy} r={r} fill="none" stroke={palette.border} strokeWidth={1} />
      </Svg>
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* Fishing Rating Card */}
      <View style={styles.ratingCard}>
        <View style={styles.ratingHeader}>
          <Ionicons name="fish" size={24} color={palette.accent} />
          <Text style={styles.ratingTitle}>Today's Fishing Rating</Text>
        </View>
        <View style={styles.ratingStars}>
          {[1, 2, 3, 4, 5].map(i => (
            <Ionicons
              key={i}
              name={i <= fishingRating.stars ? 'star' : 'star-outline'}
              size={28}
              color={i <= fishingRating.stars ? '#FFC107' : palette.textDim}
            />
          ))}
          <Text style={styles.ratingLabel}>{fishingRating.rating}</Text>
        </View>
        <Text style={styles.ratingTip}>{fishingRating.tip}</Text>
      </View>

      {/* Sun Section */}
      {sun && (
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="sunny" size={22} color="#FFA726" />
            <Text style={styles.sectionTitle}>Sun</Text>
          </View>
          <View style={styles.sunGrid}>
            <View style={styles.sunItem}>
              <Ionicons name="arrow-up" size={18} color="#FFA726" />
              <Text style={styles.sunLabel}>Sunrise</Text>
              <Text style={styles.sunTime}>{formatTime(sun.sunrise)}</Text>
            </View>
            <View style={styles.sunItem}>
              <Ionicons name="sunny" size={18} color="#FF8F00" />
              <Text style={styles.sunLabel}>Solar Noon</Text>
              <Text style={styles.sunTime}>{formatTime(sun.solarNoon)}</Text>
            </View>
            <View style={styles.sunItem}>
              <Ionicons name="arrow-down" size={18} color="#E65100" />
              <Text style={styles.sunLabel}>Sunset</Text>
              <Text style={styles.sunTime}>{formatTime(sun.sunset)}</Text>
            </View>
          </View>

          {/* Golden hours */}
          <View style={styles.goldenRow}>
            <View style={[styles.goldenBadge, { backgroundColor: '#FFF3E0' }]}>
              <Ionicons name="sunny-outline" size={14} color="#FF8F00" />
              <Text style={styles.goldenText}>
                Morning Golden: {formatTime(sun.sunrise)} – {formatTime(sun.goldenMorningEnd)}
              </Text>
            </View>
            <View style={[styles.goldenBadge, { backgroundColor: '#FFF3E0' }]}>
              <Ionicons name="sunny-outline" size={14} color="#E65100" />
              <Text style={styles.goldenText}>
                Evening Golden: {formatTime(sun.goldenEveningStart)} – {formatTime(sun.sunset)}
              </Text>
            </View>
          </View>
        </View>
      )}

      {/* Moon Section */}
      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Ionicons name="moon" size={22} color="#90CAF9" />
          <Text style={styles.sectionTitle}>Moon</Text>
        </View>
        <View style={styles.moonRow}>
          {renderMoonPhase()}
          <View style={styles.moonInfo}>
            <Text style={styles.moonPhaseName}>{moon.name}</Text>
            <Text style={styles.moonIllumination}>{moon.illumination}% illuminated</Text>
            <View style={styles.moonDetail}>
              <Text style={styles.moonDetailLabel}>Phase:</Text>
              <Text style={styles.moonDetailValue}>{(moon.phase * 100).toFixed(1)}%</Text>
            </View>
            <View style={styles.moonDetail}>
              <Text style={styles.moonDetailLabel}>Cycle Day:</Text>
              <Text style={styles.moonDetailValue}>{Math.round(moon.phase * 29.5)}</Text>
            </View>
          </View>
        </View>
      </View>

      {/* Solunar Periods */}
      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Ionicons name="time" size={22} color={palette.accent} />
          <Text style={styles.sectionTitle}>Solunar Periods</Text>
        </View>
        <Text style={styles.solunarDescription}>
          Major and minor feeding periods based on lunar position
        </Text>

        {solunar.major.map((p, i) => (
          <View key={`major-${i}`} style={styles.periodCard}>
            <View style={[styles.periodBadge, { backgroundColor: palette.accent }]}>
              <Text style={styles.periodBadgeText}>MAJOR</Text>
            </View>
            <View style={styles.periodInfo}>
              <Text style={styles.periodTime}>{p.startTime} – {p.endTime}</Text>
              <Text style={styles.periodDuration}>{p.duration} — Peak feeding</Text>
            </View>
          </View>
        ))}

        {solunar.minor.map((p, i) => (
          <View key={`minor-${i}`} style={styles.periodCard}>
            <View style={[styles.periodBadge, { backgroundColor: palette.textMuted }]}>
              <Text style={styles.periodBadgeText}>MINOR</Text>
            </View>
            <View style={styles.periodInfo}>
              <Text style={styles.periodTime}>{p.startTime} – {p.endTime}</Text>
              <Text style={styles.periodDuration}>{p.duration} — Moderate feeding</Text>
            </View>
          </View>
        ))}
      </View>

      {/* Pro Tips */}
      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Ionicons name="bulb" size={22} color="#FFC107" />
          <Text style={styles.sectionTitle}>Lunar Fishing Tips</Text>
        </View>
        <View style={styles.tipCard}>
          <Text style={styles.tipText}>
            {moon.illumination > 70
              ? 'Bright moonlight can extend night feeding. Use darker colored lures and fish deeper during the day.'
              : moon.illumination < 30
              ? 'Low light conditions favor topwater action. Fish are more likely to feed in shallow water.'
              : 'Moderate moonlight — focus on structure and cover. Fish transition areas between deep and shallow.'}
          </Text>
        </View>
        <View style={styles.tipCard}>
          <Text style={styles.tipText}>
            Dawn and dusk remain the most reliable feeding windows regardless of moon phase. Plan your major effort around these times.
          </Text>
        </View>
      </View>
    </ScrollView>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 16,
    paddingBottom: 40,
  },

  // Rating card
  ratingCard: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  ratingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 12,
  },
  ratingTitle: {
    ...typeStyles.sectionHeader,
  },
  ratingStars: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginBottom: 10,
  },
  ratingLabel: {
    fontSize: 18,
    fontWeight: '700',
    color: palette.text,
    marginLeft: 8,
  },
  ratingTip: {
    fontSize: 14,
    color: palette.textSecondary,
    lineHeight: 20,
  },

  // Sections
  section: {
    backgroundColor: palette.surface,
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 16,
  },
  sectionTitle: {
    ...typeStyles.sectionHeader,
  },

  // Sun grid
  sunGrid: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 16,
  },
  sunItem: {
    alignItems: 'center',
    gap: 4,
  },
  sunLabel: {
    fontSize: 11,
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  sunTime: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },

  // Golden hours
  goldenRow: {
    gap: 8,
  },
  goldenBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  goldenText: {
    fontSize: 13,
    color: '#E65100',
    fontWeight: '500',
  },

  // Moon
  moonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 20,
  },
  moonInfo: {
    flex: 1,
  },
  moonPhaseName: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 4,
  },
  moonIllumination: {
    fontSize: 14,
    color: palette.textSecondary,
    marginBottom: 12,
  },
  moonDetail: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  moonDetailLabel: {
    fontSize: 13,
    color: palette.textMuted,
  },
  moonDetailValue: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.text,
  },

  // Solunar
  solunarDescription: {
    fontSize: 13,
    color: palette.textMuted,
    marginBottom: 12,
  },
  periodCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  periodBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  periodBadgeText: {
    fontSize: 10,
    fontWeight: '800',
    color: '#FFF',
    letterSpacing: 0.5,
  },
  periodInfo: {
    flex: 1,
  },
  periodTime: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  periodDuration: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },

  // Tips
  tipCard: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 14,
    marginBottom: 8,
  },
  tipText: {
    fontSize: 14,
    color: palette.textSecondary,
    lineHeight: 20,
  },
});
