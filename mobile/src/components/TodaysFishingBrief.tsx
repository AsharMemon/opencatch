/**
 * TodaysFishingBrief — "Today's Fishing Brief" card for Profile/Home.
 *
 * Displays at a glance:
 * - Overall bite rating for your area
 * - Best time window today
 * - Current fishing pressure
 * - Weather alerts (if any)
 *
 * Data loads on-demand when the component mounts via user's current location.
 */

import React, { useEffect, useState, memo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { palette } from '../theme/palette';
import { fonts, type as typeStyles } from '../theme/typography';
import { getDailyBiteForecast, formatHour, type DailyBiteForecast } from '../services/bestTimeWindows';
import { getCurrentPressure, type PressureReading } from '../services/fishingPressure';
import { getFishingAlerts, type FishingWeatherAlert } from '../services/weatherAlerts';

// ── Types ────────────────────────────────────────────────────────────────────

interface CurrentWeather {
  tempC: number;
  description: string;
  icon: keyof typeof Ionicons.glyphMap;
  isBad: boolean;
}

interface BriefData {
  bite: DailyBiteForecast;
  pressure: PressureReading;
  alerts: FishingWeatherAlert[];
  weather: CurrentWeather | null;
}

function ratingColor(rating: number): string {
  if (rating >= 70) return '#2E7D32';
  if (rating >= 55) return '#66BB6A';
  if (rating >= 40) return '#FFA726';
  if (rating >= 25) return '#EF5350';
  return '#B71C1C';
}

// ── Weather code to info mapper (WMO codes) ──────────────────────────────────

function weatherCodeToInfo(code: number): { desc: string; icon: keyof typeof Ionicons.glyphMap; isBad: boolean } {
  if (code === 0) return { desc: 'Clear', icon: 'sunny-outline', isBad: false };
  if (code <= 3) return { desc: 'Cloudy', icon: 'cloudy-outline', isBad: false };
  if (code <= 49) return { desc: 'Fog', icon: 'cloud-outline', isBad: false };
  if (code <= 59) return { desc: 'Drizzle', icon: 'rainy-outline', isBad: false };
  if (code <= 69) return { desc: 'Rain', icon: 'rainy-outline', isBad: true };
  if (code <= 79) return { desc: 'Snow', icon: 'snow-outline', isBad: true };
  if (code <= 84) return { desc: 'Rain showers', icon: 'rainy-outline', isBad: true };
  if (code <= 86) return { desc: 'Snow showers', icon: 'snow-outline', isBad: true };
  if (code <= 99) return { desc: 'Thunderstorm', icon: 'thunderstorm-outline', isBad: true };
  return { desc: 'Unknown', icon: 'cloud-outline', isBad: false };
}

// ── Component ────────────────────────────────────────────────────────────────

export const TodaysFishingBrief = memo(function TodaysFishingBrief() {
  const [data, setData] = useState<BriefData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') {
          setError(true);
          setLoading(false);
          return;
        }
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        const lat = loc.coords.latitude;
        const lon = loc.coords.longitude;

        const bite = getDailyBiteForecast(lat, lon);
        const pressure = getCurrentPressure({ lat, lon });
        let alerts: FishingWeatherAlert[] = [];
        let weather: CurrentWeather | null = null;
        try {
          alerts = await getFishingAlerts(lat, lon);
        } catch {
          // Alerts are optional
        }

        // Fetch current weather from Open-Meteo
        try {
          const weatherRes = await fetch(
            `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code&timezone=auto`
          );
          if (weatherRes.ok) {
            const weatherData = await weatherRes.json();
            const current = weatherData?.current;
            if (current) {
              const tempC = current.temperature_2m ?? 0;
              const code = current.weather_code ?? 0;
              const { desc, icon, isBad } = weatherCodeToInfo(code);
              weather = { tempC: Math.round(tempC), description: desc, icon, isBad };
            }
          }
        } catch {
          // Weather is optional
        }

        if (!cancelled) {
          setData({ bite, pressure, alerts, weather });
        }
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <View style={s.card}>
        <View style={s.loadingRow}>
          <ActivityIndicator size="small" color={palette.accent} />
          <Text style={s.loadingText}>Loading today's brief...</Text>
        </View>
      </View>
    );
  }

  if (error || !data) {
    return null; // Silently hide if location unavailable
  }

  const { bite, pressure, alerts } = data;
  const color = ratingColor(bite.overallRating);

  return (
    <View style={s.card}>
      {/* Header */}
      <View style={s.header}>
        <Ionicons name="sunny-outline" size={18} color={palette.accent} />
        <Text style={s.headerTitle}>Today's Fishing Brief</Text>
      </View>

      {/* Weather warning if conditions are bad */}
      {data.weather?.isBad && (
        <View style={s.alertRow}>
          <Ionicons name="warning" size={14} color="#E53935" />
          <Text style={s.alertText}>
            Not recommended — {data.weather.description.toLowerCase()}
          </Text>
        </View>
      )}

      {/* Compact badge grid */}
      <View style={s.badgeGrid}>
        {/* Current Weather — first badge (most immediately relevant) */}
        {data.weather && (
          <View style={[s.badge, { backgroundColor: data.weather.isBad ? '#FFEBEE' : '#E3F2FD' }]}>
            <Ionicons
              name={data.weather.icon as any}
              size={14}
              color={data.weather.isBad ? '#C62828' : '#1565C0'}
            />
            <Text style={[s.badgeLabel, { color: data.weather.isBad ? '#C62828' : '#1565C0' }]}>
              {data.weather.description} {data.weather.tempC}°C
            </Text>
          </View>
        )}

        {/* Bite Rating */}
        <View style={[s.badge, { backgroundColor: color + '15' }]}>
          <Ionicons name="fish" size={14} color={color} />
          <Text style={[s.badgeLabel, { color }]}>{bite.ratingLabel}</Text>
        </View>

        {/* Moon Phase (no percentage) */}
        <View style={[s.badge, { backgroundColor: '#78909C12' }]}>
          <Ionicons name="moon-outline" size={14} color="#78909C" />
          <Text style={[s.badgeLabel, { color: '#78909C' }]}>{bite.moonPhase}</Text>
        </View>

        {/* Best Time */}
        {bite.bestWindow && (
          <View style={[s.badge, { backgroundColor: palette.accent + '15' }]}>
            <Ionicons name="time-outline" size={14} color={palette.accent} />
            <Text style={[s.badgeLabel, { color: palette.accent }]}>
              Best: {bite.bestWindow.label}
            </Text>
          </View>
        )}

        {/* Pressure */}
        <View style={[s.badge, { backgroundColor: pressure.color + '15' }]}>
          <Ionicons name={pressure.icon as any} size={14} color={pressure.color} />
          <Text style={[s.badgeLabel, { color: pressure.color }]}>
            {pressure.label} Pressure
          </Text>
        </View>
      </View>

      {/* Weather Alerts */}
      {alerts.length > 0 && (
        <View style={s.alertRow}>
          <Ionicons name="warning" size={14} color="#E53935" />
          <Text style={s.alertText} numberOfLines={2}>
            {alerts[0].headline}
          </Text>
        </View>
      )}
    </View>
  );
});

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 3 },
    elevation: 3,
  },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
  },
  loadingText: {
    fontSize: 13,
    color: palette.textMuted,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  headerTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 16,
    color: palette.text,
  },
  badgeGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
  },
  badgeLabel: {
    fontSize: 13,
    fontWeight: '700',
  },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#FFEBEE',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  alertText: {
    flex: 1,
    fontSize: 12,
    color: '#C62828',
    fontWeight: '600',
  },
});
