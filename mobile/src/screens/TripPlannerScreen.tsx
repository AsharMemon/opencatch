/**
 * OpenCatch — Trip Planner Screen
 *
 * Plan fishing trips, view forecasts, get weather alerts, and share plans.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  Pressable,
  Alert,
  ActivityIndicator,
  Modal,
  TextInput,
  Platform,
  KeyboardAvoidingView,
  ScrollView,
  Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { palette } from '../theme/palette';
import { type as typeStyles, fonts } from '../theme/typography';
import { hapticLight } from '../utils/haptics';
import {
  getPlannedTrips,
  savePlannedTrip,
  deletePlannedTrip,
  updateTripForecast,
  checkTripAlerts,
  type PlannedTrip,
  type TripAlert,
} from '../services/tripPlanner';
import { shareTrip } from '../services/socialSharing';
import type { RootStackProps } from '../types/navigation';

type Props = RootStackProps<'TripPlanner'>;

// ── Weather code to Ionicon mapping ──────────────────────────────────────────

function weatherIcon(code: number | undefined): keyof typeof Ionicons.glyphMap {
  if (code == null) return 'cloud-outline';
  if (code === 0) return 'sunny';
  if (code <= 3) return 'partly-sunny';
  if (code <= 48) return 'cloud';
  if (code <= 67) return 'rainy';
  if (code <= 77) return 'snow';
  if (code <= 82) return 'rainy';
  if (code <= 86) return 'snow';
  return 'thunderstorm';
}

function weatherColor(code: number | undefined): string {
  if (code == null) return palette.textMuted;
  if (code === 0) return '#F59E0B';
  if (code <= 3) return '#60A5FA';
  if (code <= 48) return '#9CA3AF';
  if (code <= 67) return '#3B82F6';
  if (code <= 77) return '#94A3B8';
  if (code <= 82) return '#2563EB';
  if (code <= 86) return '#64748B';
  return '#EF4444';
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatTripDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

function formatTripTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function daysUntil(iso: string): number {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const trip = new Date(iso);
  trip.setHours(0, 0, 0, 0);
  return Math.ceil((trip.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function daysLabel(iso: string): string {
  const days = daysUntil(iso);
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days < 0) return 'Past';
  return `In ${days} days`;
}

// ── Main Screen ──────────────────────────────────────────────────────────────

export function TripPlannerScreen({ navigation }: Props) {
  const [trips, setTrips] = useState<PlannedTrip[]>([]);
  const [alerts, setAlerts] = useState<TripAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showForm, setShowForm] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [tripsData, alertsData] = await Promise.all([
        getPlannedTrips(),
        checkTripAlerts(),
      ]);
      setTrips(tripsData);
      setAlerts(alertsData);
    } catch {
      // Silent
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadData();
    }, [loadData]),
  );

  const handleRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const handleDelete = (trip: PlannedTrip) => {
    Alert.alert(
      'Delete Trip',
      `Remove your trip to ${trip.locationName}?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await deletePlannedTrip(trip.id);
            loadData();
          },
        },
      ],
    );
  };

  const handleShare = async (trip: PlannedTrip) => {
    hapticLight();
    await shareTrip(trip);
  };

  const handleUpdateForecast = async (trip: PlannedTrip) => {
    hapticLight();
    await updateTripForecast(trip.id);
    loadData();
  };

  const tripAlerts = (tripId: string) => alerts.filter((a) => a.tripId === tripId);

  const renderTrip = ({ item }: { item: PlannedTrip }) => {
    const tripWarnings = tripAlerts(item.id);
    const f = item.forecastSummary;
    const isPast = daysUntil(item.date) < 0;

    return (
      <View style={[styles.tripCard, isPast && styles.tripCardPast]}>
        {/* Alert banners */}
        {tripWarnings.map((alert, i) => (
          <View
            key={i}
            style={[
              styles.alertBanner,
              alert.severity === 'danger' ? styles.alertDanger : styles.alertWarning,
            ]}
          >
            <Ionicons
              name={alert.severity === 'danger' ? 'alert-circle' : 'warning'}
              size={16}
              color={alert.severity === 'danger' ? palette.error : palette.warning}
            />
            <Text
              style={[
                styles.alertText,
                { color: alert.severity === 'danger' ? palette.error : palette.warning },
              ]}
              numberOfLines={2}
            >
              {alert.message}
            </Text>
          </View>
        ))}

        {/* Header row */}
        <View style={styles.tripHeader}>
          <View style={styles.tripHeaderLeft}>
            <Text style={styles.tripLocationName} numberOfLines={1}>
              {item.locationName}
            </Text>
            <View style={styles.tripDateRow}>
              <Ionicons name="calendar-outline" size={14} color={palette.textMuted} />
              <Text style={styles.tripDateText}>
                {formatTripDate(item.date)} at {formatTripTime(item.date)}
              </Text>
              <View style={[
                styles.daysChip,
                daysUntil(item.date) === 0 && styles.daysChipToday,
                isPast && styles.daysChipPast,
              ]}>
                <Text style={[
                  styles.daysChipText,
                  daysUntil(item.date) === 0 && styles.daysChipTextToday,
                  isPast && styles.daysChipTextPast,
                ]}>
                  {daysLabel(item.date)}
                </Text>
              </View>
            </View>
          </View>
        </View>

        {/* Forecast row */}
        {f ? (
          <View style={styles.forecastRow}>
            <View style={styles.forecastItem}>
              <Ionicons name={weatherIcon(f.weatherCode) as any} size={20} color={weatherColor(f.weatherCode)} />
              <Text style={styles.forecastLabel}>{f.weatherLabel}</Text>
            </View>
            <View style={styles.forecastItem}>
              <Ionicons name="thermometer-outline" size={16} color={palette.textSecondary} />
              <Text style={styles.forecastValue}>{f.highTemp}° / {f.lowTemp}°</Text>
            </View>
            <View style={styles.forecastItem}>
              <Ionicons name="flag-outline" size={16} color={palette.textSecondary} />
              <Text style={styles.forecastValue}>{f.windSpeed} mph</Text>
            </View>
            <View style={styles.forecastItem}>
              <Ionicons name="water-outline" size={16} color={palette.textSecondary} />
              <Text style={styles.forecastValue}>{f.precipChance}%</Text>
            </View>
          </View>
        ) : (
          <Pressable style={styles.fetchForecastBtn} onPress={() => handleUpdateForecast(item)}>
            <Ionicons name="cloud-download-outline" size={16} color={palette.accent} />
            <Text style={styles.fetchForecastText}>Fetch Forecast</Text>
          </Pressable>
        )}

        {/* Notes */}
        {item.notes ? (
          <Text style={styles.tripNotes} numberOfLines={2}>
            {item.notes}
          </Text>
        ) : null}

        {/* Action buttons */}
        <View style={styles.tripActions}>
          <Pressable
            style={({ pressed }) => [styles.actionBtn, pressed && styles.actionBtnPressed]}
            onPress={() => handleShare(item)}
          >
            <Ionicons name="share-outline" size={16} color={palette.accent} />
            <Text style={styles.actionBtnText}>Share</Text>
          </Pressable>
          <Pressable
            style={({ pressed }) => [styles.actionBtn, pressed && styles.actionBtnPressed]}
            onPress={() => handleUpdateForecast(item)}
          >
            <Ionicons name="refresh-outline" size={16} color={palette.accent} />
            <Text style={styles.actionBtnText}>Update</Text>
          </Pressable>
          <Pressable
            style={({ pressed }) => [styles.actionBtn, styles.actionBtnDelete, pressed && styles.actionBtnPressed]}
            onPress={() => handleDelete(item)}
          >
            <Ionicons name="trash-outline" size={16} color={palette.error} />
            <Text style={[styles.actionBtnText, { color: palette.error }]}>Delete</Text>
          </Pressable>
        </View>
      </View>
    );
  };

  if (loading) {
    return (
      <View style={[styles.screen, styles.centered]}>
        <ActivityIndicator color={palette.accent} size="large" />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <FlatList
        data={trips}
        keyExtractor={(t) => t.id}
        renderItem={renderTrip}
        contentContainerStyle={styles.listContent}
        refreshing={refreshing}
        onRefresh={handleRefresh}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Trip Planner</Text>
            <Text style={styles.subtitle}>Plan your next fishing adventure</Text>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="map-outline" size={48} color={palette.textDim} />
            <Text style={styles.emptyTitle}>No Trips Planned</Text>
            <Text style={styles.emptySubtitle}>
              Tap the button below to plan your next fishing trip
            </Text>
          </View>
        }
        ListFooterComponent={<View style={{ height: 100 }} />}
      />

      {/* FAB */}
      <Pressable
        style={({ pressed }) => [styles.fab, pressed && styles.fabPressed]}
        onPress={() => {
          hapticLight();
          setShowForm(true);
        }}
      >
        <Ionicons name="add" size={28} color="#fff" />
      </Pressable>

      {/* New Trip Modal */}
      <NewTripModal
        visible={showForm}
        onClose={() => setShowForm(false)}
        onSave={async (trip) => {
          await savePlannedTrip(trip);
          setShowForm(false);
          loadData();
        }}
      />
    </View>
  );
}

// ── New Trip Modal ───────────────────────────────────────────────────────────

interface NewTripModalProps {
  visible: boolean;
  onClose: () => void;
  onSave: (trip: {
    locationName: string;
    lat: number;
    lon: number;
    date: string;
    notes: string;
    notifyBefore: number;
  }) => void;
}

function NewTripModal({ visible, onClose, onSave }: NewTripModalProps) {
  const [locationName, setLocationName] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('07:00');
  const [notes, setNotes] = useState('');
  const [notifyHours, setNotifyHours] = useState('12');

  const reset = () => {
    setLocationName('');
    setLat('');
    setLon('');
    setDate('');
    setTime('07:00');
    setNotes('');
    setNotifyHours('12');
  };

  const handleSave = () => {
    if (!locationName.trim()) {
      Alert.alert('Missing Location', 'Enter a location name for your trip.');
      return;
    }
    if (!date.trim()) {
      Alert.alert('Missing Date', 'Enter a date (YYYY-MM-DD) for your trip.');
      return;
    }

    const latNum = parseFloat(lat) || 0;
    const lonNum = parseFloat(lon) || 0;
    const isoDate = `${date}T${time}:00`;

    onSave({
      locationName: locationName.trim(),
      lat: latNum,
      lon: lonNum,
      date: isoDate,
      notes: notes.trim(),
      notifyBefore: parseInt(notifyHours) || 12,
    });
    reset();
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet">
      <KeyboardAvoidingView
        style={styles.modalContainer}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.modalHeader}>
          <Pressable onPress={() => { onClose(); reset(); }} hitSlop={12}>
            <Text style={styles.modalCancel}>Cancel</Text>
          </Pressable>
          <Text style={styles.modalTitle}>Plan a Trip</Text>
          <Pressable onPress={handleSave} hitSlop={12}>
            <Text style={styles.modalSave}>Save</Text>
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.modalContent}>
          {/* Location */}
          <View style={styles.formField}>
            <Text style={styles.formLabel}>Location Name</Text>
            <TextInput
              style={styles.formInput}
              value={locationName}
              onChangeText={setLocationName}
              placeholder="e.g. Ghost Lake, Bass Pro Pond"
              placeholderTextColor={palette.textDim}
            />
          </View>

          {/* Coordinates */}
          <View style={styles.formRow}>
            <View style={[styles.formField, { flex: 1 }]}>
              <Text style={styles.formLabel}>Latitude</Text>
              <TextInput
                style={styles.formInput}
                value={lat}
                onChangeText={setLat}
                placeholder="e.g. 44.95"
                placeholderTextColor={palette.textDim}
                keyboardType="decimal-pad"
              />
            </View>
            <View style={[styles.formField, { flex: 1 }]}>
              <Text style={styles.formLabel}>Longitude</Text>
              <TextInput
                style={styles.formInput}
                value={lon}
                onChangeText={setLon}
                placeholder="e.g. -93.27"
                placeholderTextColor={palette.textDim}
                keyboardType="decimal-pad"
              />
            </View>
          </View>

          {/* Date & Time */}
          <View style={styles.formRow}>
            <View style={[styles.formField, { flex: 1 }]}>
              <Text style={styles.formLabel}>Date (YYYY-MM-DD)</Text>
              <TextInput
                style={styles.formInput}
                value={date}
                onChangeText={setDate}
                placeholder="2026-03-28"
                placeholderTextColor={palette.textDim}
              />
            </View>
            <View style={[styles.formField, { flex: 1 }]}>
              <Text style={styles.formLabel}>Time (HH:MM)</Text>
              <TextInput
                style={styles.formInput}
                value={time}
                onChangeText={setTime}
                placeholder="07:00"
                placeholderTextColor={palette.textDim}
              />
            </View>
          </View>

          {/* Notify Before */}
          <View style={styles.formField}>
            <Text style={styles.formLabel}>Remind me (hours before)</Text>
            <View style={styles.notifyChips}>
              {['1', '6', '12', '24'].map((h) => (
                <Pressable
                  key={h}
                  style={[styles.notifyChip, notifyHours === h && styles.notifyChipActive]}
                  onPress={() => setNotifyHours(h)}
                >
                  <Text style={[styles.notifyChipText, notifyHours === h && styles.notifyChipTextActive]}>
                    {h}h
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>

          {/* Notes */}
          <View style={styles.formField}>
            <Text style={styles.formLabel}>Notes (optional)</Text>
            <TextInput
              style={[styles.formInput, styles.formInputMultiline]}
              value={notes}
              onChangeText={setNotes}
              placeholder="Gear to bring, target species, meeting spot..."
              placeholderTextColor={palette.textDim}
              multiline
              numberOfLines={3}
              textAlignVertical="top"
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Modal>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  centered: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  listContent: {
    padding: 20,
    gap: 14,
  },
  header: {
    gap: 4,
    marginBottom: 8,
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },

  // ── Trip Card ────────────────────────────────────────────────────
  tripCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 16,
    gap: 12,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  tripCardPast: {
    opacity: 0.5,
  },
  tripHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  tripHeaderLeft: {
    flex: 1,
    gap: 4,
  },
  tripLocationName: {
    fontFamily: fonts.serif,
    fontSize: 17,
    fontWeight: '400',
    color: palette.text,
    letterSpacing: -0.2,
  },
  tripDateRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  tripDateText: {
    color: palette.textSecondary,
    fontSize: 13,
  },
  daysChip: {
    backgroundColor: palette.accentDim,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  daysChipToday: {
    backgroundColor: 'rgba(229, 57, 53, 0.10)',
  },
  daysChipPast: {
    backgroundColor: palette.surfaceRaised,
  },
  daysChipText: {
    color: palette.accent,
    fontSize: 11,
    fontWeight: '700',
  },
  daysChipTextToday: {
    color: palette.error,
  },
  daysChipTextPast: {
    color: palette.textMuted,
  },

  // ── Forecast ─────────────────────────────────────────────────────
  forecastRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 8,
  },
  forecastItem: {
    alignItems: 'center',
    gap: 4,
  },
  forecastLabel: {
    fontSize: 11,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  forecastValue: {
    fontSize: 12,
    color: palette.textSecondary,
    fontWeight: '600',
  },
  fetchForecastBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    backgroundColor: palette.accentDim,
    borderRadius: 8,
  },
  fetchForecastText: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '600',
  },

  // ── Notes ────────────────────────────────────────────────────────
  tripNotes: {
    fontSize: 13,
    color: palette.textMuted,
    fontStyle: 'italic',
  },

  // ── Alert banners ────────────────────────────────────────────────
  alertBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    padding: 10,
    borderRadius: 8,
  },
  alertWarning: {
    backgroundColor: 'rgba(196, 132, 29, 0.10)',
  },
  alertDanger: {
    backgroundColor: 'rgba(196, 75, 75, 0.10)',
  },
  alertText: {
    fontSize: 12,
    fontWeight: '600',
    flex: 1,
  },

  // ── Action buttons ───────────────────────────────────────────────
  tripActions: {
    flexDirection: 'row',
    gap: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
    paddingTop: 10,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 6,
    backgroundColor: palette.accentDim,
  },
  actionBtnDelete: {
    backgroundColor: 'rgba(196, 75, 75, 0.08)',
    marginLeft: 'auto',
  },
  actionBtnPressed: {
    opacity: 0.7,
  },
  actionBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.accent,
  },

  // ── Empty state ──────────────────────────────────────────────────
  emptyState: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 60,
    gap: 10,
  },
  emptyTitle: {
    fontFamily: fonts.serif,
    fontSize: 18,
    color: palette.textSecondary,
  },
  emptySubtitle: {
    fontSize: 14,
    color: palette.textMuted,
    textAlign: 'center',
    paddingHorizontal: 40,
  },

  // ── FAB ──────────────────────────────────────────────────────────
  fab: {
    position: 'absolute',
    bottom: 30,
    right: 20,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  fabPressed: {
    transform: [{ scale: 0.92 }],
    opacity: 0.9,
  },

  // ── Modal ────────────────────────────────────────────────────────
  modalContainer: {
    flex: 1,
    backgroundColor: palette.background,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
  },
  modalTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 17,
    color: palette.text,
  },
  modalCancel: {
    fontSize: 16,
    color: palette.textMuted,
  },
  modalSave: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.accent,
  },
  modalContent: {
    padding: 20,
    gap: 18,
  },

  // ── Form fields ──────────────────────────────────────────────────
  formField: {
    gap: 6,
  },
  formRow: {
    flexDirection: 'row',
    gap: 12,
  },
  formLabel: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '700',
  },
  formInput: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: palette.border,
    padding: 12,
    color: palette.text,
    fontSize: 16,
  },
  formInputMultiline: {
    minHeight: 80,
    paddingTop: 12,
  },
  notifyChips: {
    flexDirection: 'row',
    gap: 8,
  },
  notifyChip: {
    paddingVertical: 8,
    paddingHorizontal: 18,
    borderRadius: 8,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: palette.border,
  },
  notifyChipActive: {
    backgroundColor: palette.accentLight,
    borderColor: palette.accent,
  },
  notifyChipText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textMuted,
  },
  notifyChipTextActive: {
    color: palette.accent,
  },
});
