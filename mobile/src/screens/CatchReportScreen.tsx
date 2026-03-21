import React, { useState, useEffect } from 'react';
import {
  ScrollView,
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  Alert,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Image,
} from 'react-native';
import * as Location from 'expo-location';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { StarRating } from '../components/StarRating';
import { api, ApiError } from '../services/api';
import type { CatchReport, CatchReportV2Create } from '../types/models';
import type { RootStackProps } from '../types/navigation';

type Props = RootStackProps<'CatchReport'>;

const SPECIES_OPTIONS: string[] = [
  'Largemouth Bass',
  'Smallmouth Bass',
  'Spotted Bass',
  'Bluegill',
  'Crappie',
  'Channel Catfish',
  'Walleye',
  'Rainbow Trout',
  'Northern Pike',
  'Other',
];

const ACCENT = '#0A6EBD';

export function CatchReportScreen({ route, navigation }: Props) {
  const passedLat = route.params?.lat;
  const passedLon = route.params?.lon;

  const [species, setSpecies] = useState<string>('Largemouth Bass');
  const [numberCaught, setNumberCaught] = useState('');
  const [numberKept, setNumberKept] = useState('0');
  const [largestWeight, setLargestWeight] = useState('');
  const [rating, setRating] = useState(0);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [photoUri, setPhotoUri] = useState<string | null>(null);

  const [lat, setLat] = useState(passedLat ?? 0);
  const [lon, setLon] = useState(passedLon ?? 0);
  const [locationResolved, setLocationResolved] = useState(!!passedLat);

  // Auto-detect GPS if not passed
  useEffect(() => {
    if (!passedLat) {
      (async () => {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status === 'granted') {
          const loc = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.Balanced,
          });
          setLat(loc.coords.latitude);
          setLon(loc.coords.longitude);
          setLocationResolved(true);
        }
      })();
    }
  }, []);

  const now = new Date();
  const dateLabel = now.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  const timeLabel = now.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });

  const canSubmit = rating > 0 && numberCaught.trim() !== '' && parseInt(numberCaught) >= 0;

  const handleTakePhoto = () => {
    Alert.alert(
      'Camera',
      'Camera feature coming soon. AI Fish ID will be available in a future update.',
    );
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);

    const now = new Date();
    const effortHours = Math.max(0.5, parseFloat(numberCaught) > 0 ? 2.0 : 1.0);

    // Build V2 report matching FastAPI CatchReportCreate schema
    const reportV2: CatchReportV2Create = {
      user_id: 'mobile-user', // placeholder until auth is wired
      lat,
      lon,
      trip_start: new Date(now.getTime() - effortHours * 3600000).toISOString(),
      trip_end: now.toISOString(),
      effort_hours: effortHours,
      species: species.toLowerCase().replace(/\s+/g, '_'),
      catch_count: parseInt(numberCaught) || 0,
      kept_count: parseInt(numberKept) || 0,
      largest_weight_lb: largestWeight ? parseFloat(largestWeight) : undefined,
      rating,
      reported_at: now.toISOString(),
      notes: notes.trim() || undefined,
    };

    // Also build legacy report for backward compat with mock path
    const legacyReport: CatchReport = {
      species,
      numberCaught: parseInt(numberCaught),
      numberKept: parseInt(numberKept) || 0,
      largestWeight: largestWeight ? parseFloat(largestWeight) : undefined,
      rating,
      lat,
      lon,
      date: now.toISOString(),
      notes: notes.trim() || undefined,
      photoUri: photoUri ?? undefined,
    };

    try {
      // Try V2 endpoint first, fall back to legacy
      try {
        const result = await api.submitCatchReportV2(reportV2);
        Alert.alert(
          'Catch Logged!',
          `Report saved (ID: ${result.id}). Status: ${result.status}.`,
          [{ text: 'OK', onPress: () => navigation.goBack() }],
        );
        return;
      } catch (v2Err) {
        // If the V2 endpoint fails with a non-4xx error, try legacy
        if (v2Err instanceof ApiError && v2Err.status >= 400 && v2Err.status < 500) {
          // Client error — surface it
          const detail = v2Err.message.includes('kept_count')
            ? 'Number kept cannot exceed number caught.'
            : v2Err.message;
          Alert.alert('Validation Error', detail);
          return;
        }
        // Fall through to legacy endpoint
        await api.submitCatchReport(legacyReport);
        Alert.alert('Catch Logged!', 'Your catch report has been submitted.', [
          { text: 'OK', onPress: () => navigation.goBack() },
        ]);
      }
    } catch (err: any) {
      const message = err instanceof ApiError
        ? `Server error (${err.status}). Please try again later.`
        : 'Network error. Check your connection and try again.';
      Alert.alert('Submission Failed', message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>Log a Catch</Text>
          <Text style={styles.subtitle}>Record your fishing trip details</Text>
        </View>

        {/* Camera button — prominent, at top of form */}
        <Pressable
          style={({ pressed }) => [
            styles.cameraButton,
            pressed && styles.cameraButtonPressed,
          ]}
          onPress={handleTakePhoto}
        >
          {photoUri ? (
            <Image source={{ uri: photoUri }} style={styles.cameraPreview} />
          ) : (
            <View style={styles.cameraPlaceholder}>
              <Ionicons name="camera-outline" size={32} color={palette.accent} />
              <Text style={styles.cameraTitle}>Take a Photo</Text>
              <Text style={styles.cameraSubtitle}>AI Fish ID coming soon</Text>
            </View>
          )}
        </Pressable>

        {/* AI Identification placeholder */}
        <View style={styles.aiSection}>
          <View style={styles.aiHeader}>
            <Text style={styles.aiTitle}>AI Identification</Text>
            <View style={styles.aiBadge}>
              <Text style={styles.aiBadgeText}>Coming Soon</Text>
            </View>
          </View>
          <Text style={styles.aiDescription}>
            Snap a photo of your catch and our ML model will identify the species automatically.
          </Text>
        </View>

        {/* Auto-captured info */}
        <View style={styles.autoSection}>
          <View style={styles.autoRow}>
            <Ionicons name="calendar-outline" size={16} color={palette.textMuted} />
            <Text style={styles.autoText}>
              {dateLabel} at {timeLabel}
            </Text>
          </View>
          <View style={styles.autoRow}>
            <Ionicons name="location-outline" size={16} color={palette.textMuted} />
            <Text style={styles.autoText}>
              {locationResolved
                ? `${lat.toFixed(4)}, ${lon.toFixed(4)}`
                : 'Detecting location...'}
            </Text>
          </View>
        </View>

        {/* Species selector — scrollable chip row */}
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>Species</Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.speciesScrollContent}
          >
            {SPECIES_OPTIONS.map((sp) => (
              <Pressable
                key={sp}
                style={[
                  styles.speciesChip,
                  species === sp && styles.speciesChipActive,
                ]}
                onPress={() => setSpecies(sp)}
              >
                <Text
                  style={[
                    styles.speciesText,
                    species === sp && styles.speciesTextActive,
                  ]}
                >
                  {sp}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>

        {/* Number caught / kept */}
        <View style={styles.rowFields}>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.fieldLabel}>Number Caught</Text>
            <TextInput
              style={styles.input}
              value={numberCaught}
              onChangeText={setNumberCaught}
              keyboardType="number-pad"
              placeholder="0"
              placeholderTextColor={palette.textDim}
            />
          </View>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.fieldLabel}>Number Kept</Text>
            <TextInput
              style={styles.input}
              value={numberKept}
              onChangeText={setNumberKept}
              keyboardType="number-pad"
              placeholder="0"
              placeholderTextColor={palette.textDim}
            />
          </View>
        </View>

        {/* Largest weight */}
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>Largest Weight (lbs) - Optional</Text>
          <TextInput
            style={styles.input}
            value={largestWeight}
            onChangeText={setLargestWeight}
            keyboardType="decimal-pad"
            placeholder="e.g. 4.5"
            placeholderTextColor={palette.textDim}
          />
        </View>

        {/* Rating */}
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>How was the fishing?</Text>
          <View style={styles.ratingContainer}>
            <StarRating rating={rating} onRate={setRating} size={40} />
            {rating > 0 && (
              <Text style={styles.ratingLabel}>
                {['', 'Poor', 'Below Average', 'Average', 'Good', 'Excellent'][rating]}
              </Text>
            )}
          </View>
        </View>

        {/* Notes */}
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>Notes (optional)</Text>
          <TextInput
            style={[styles.input, styles.inputMultiline]}
            value={notes}
            onChangeText={setNotes}
            placeholder="Lures used, techniques, observations..."
            placeholderTextColor={palette.textDim}
            multiline
            numberOfLines={3}
            textAlignVertical="top"
          />
        </View>

        {/* Submit */}
        <Pressable
          style={({ pressed }) => [
            styles.submitButton,
            !canSubmit && styles.submitDisabled,
            pressed && canSubmit && styles.submitPressed,
          ]}
          onPress={handleSubmit}
          disabled={!canSubmit || submitting}
        >
          {submitting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.submitText}>Submit Catch Report</Text>
          )}
        </Pressable>

        <View style={{ height: 40 }} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 20,
  },
  header: {
    gap: 4,
  },
  title: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  subtitle: {
    color: palette.textMuted,
    fontSize: 14,
  },

  // ── Camera button ──────────────────────────────────────────────
  cameraButton: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    borderWidth: 2,
    borderColor: ACCENT,
    borderStyle: 'dashed',
    overflow: 'hidden',
  },
  cameraButtonPressed: {
    opacity: 0.8,
  },
  cameraPlaceholder: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 32,
    gap: 6,
  },
  cameraIcon: {
    fontSize: 40,
  },
  cameraTitle: {
    color: ACCENT,
    fontSize: 18,
    fontWeight: '700',
  },
  cameraSubtitle: {
    color: palette.textMuted,
    fontSize: 13,
    fontStyle: 'italic',
  },
  cameraPreview: {
    width: '100%',
    height: 200,
    resizeMode: 'cover',
  },

  // ── AI Identification section ──────────────────────────────────
  aiSection: {
    backgroundColor: palette.accentLight,
    borderRadius: 8,
    padding: 16,
    gap: 8,
  },
  aiHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  aiTitle: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '700',
  },
  aiBadge: {
    backgroundColor: ACCENT,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 3,
  },
  aiBadgeText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700',
  },
  aiDescription: {
    color: palette.textSecondary,
    fontSize: 13,
    lineHeight: 19,
  },

  // ── Auto-captured info ─────────────────────────────────────────
  autoSection: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    padding: 16,
    gap: 8,
    borderWidth: 1,
    borderColor: palette.border,
  },
  autoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  autoIcon: {
    fontSize: 16,
  },
  autoText: {
    color: palette.textSecondary,
    fontSize: 14,
  },

  // ── Form fields ────────────────────────────────────────────────
  field: {
    gap: 8,
  },
  fieldLabel: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '700',
  },
  speciesScrollContent: {
    gap: 8,
    paddingRight: 4,
  },
  speciesChip: {
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: palette.border,
    alignItems: 'center',
  },
  speciesChipActive: {
    backgroundColor: palette.accentLight,
    borderColor: ACCENT,
  },
  speciesText: {
    color: palette.textMuted,
    fontSize: 14,
    fontWeight: '600',
  },
  speciesTextActive: {
    color: ACCENT,
  },
  rowFields: {
    flexDirection: 'row',
    gap: 14,
  },
  input: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: palette.border,
    padding: 12,
    color: palette.text,
    fontSize: 16,
  },
  inputMultiline: {
    minHeight: 80,
    paddingTop: 12,
  },
  ratingContainer: {
    alignItems: 'center',
    gap: 8,
    paddingVertical: 8,
  },
  ratingLabel: {
    color: palette.warning,
    fontSize: 14,
    fontWeight: '600',
  },
  submitButton: {
    backgroundColor: ACCENT,
    borderRadius: 8,
    paddingVertical: 16,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
  },
  submitDisabled: {
    opacity: 0.4,
  },
  submitPressed: {
    opacity: 0.85,
  },
  submitText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '700',
  },
});
