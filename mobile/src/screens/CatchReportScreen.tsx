import React, { useEffect, useMemo, useState } from 'react';
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
import {
  takeCatchPhoto,
  pickCatchPhoto,
  fetchCurrentConditions,
  saveCatch,
  type CatchPhoto,
  type EnhancedCatch,
} from '../services/catchEnhancements';
import {
  identifySpecies,
  getAllSpecies,
  type PhotoIdentificationResult,
} from '../services/fishSpeciesAI';
import { getAllSaltWaterSpecies } from '../services/coastalFishing';
import type { CatchReport, CatchReportV2Create } from '../types/models';
import { shareCatchToSocial, generateCatchPreview } from '../services/socialSharing';
import type { RootStackProps } from '../types/navigation';

type Props = RootStackProps<'CatchReport'>;

const ACCENT = '#0A6EBD';

function parseOptionalInteger(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed < 0) return undefined;
  return parsed;
}

function parseOptionalDecimal(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseFloat(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0) return undefined;
  return parsed;
}

function hasUsableCoordinates(lat: number, lon: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);
}

export function CatchReportScreen({ route, navigation }: Props) {
  const passedLat = route.params?.lat;
  const passedLon = route.params?.lon;

  const [species, setSpecies] = useState<string>('');
  const [speciesQuery, setSpeciesQuery] = useState<string>('');
  const [numberCaught, setNumberCaught] = useState('');
  const [numberKept, setNumberKept] = useState('');
  const [largestWeight, setLargestWeight] = useState('');
  const [rating, setRating] = useState(0);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [photos, setPhotos] = useState<CatchPhoto[]>([]);

  const [lat, setLat] = useState(passedLat ?? 0);
  const [lon, setLon] = useState(passedLon ?? 0);
  const [locationResolved, setLocationResolved] = useState(!!passedLat);

  // Weather auto-fill state
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [autoWeather, setAutoWeather] = useState<{
    airTemp?: number;
    windSpeed?: number;
    windDirection?: string;
    pressure?: number;
    cloudCover?: string;
  } | null>(null);

  // AI species identification state
  const [aiIdentifying, setAiIdentifying] = useState(false);
  const [aiResult, setAiResult] = useState<PhotoIdentificationResult | null>(null);
  const [speciesAutoFilled, setSpeciesAutoFilled] = useState(false);

  // Bait/technique for enhanced catch
  const [bait, setBait] = useState('');
  const [technique, setTechnique] = useState('');
  const [lengthIn, setLengthIn] = useState('');

  const allSpeciesOptions = useMemo(() => {
    const freshwater = getAllSpecies().map((item) => item.commonName.trim());
    const saltwater = getAllSaltWaterSpecies().map((item) => item.name.trim());
    const seen = new Set<string>();
    const combined = [...freshwater, ...saltwater]
      .filter(Boolean)
      .filter((name) => {
        const key = name.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => a.localeCompare(b));

    return [...combined, 'Other'];
  }, []);

  const filteredSpeciesOptions = useMemo(() => {
    const query = speciesQuery.trim().toLowerCase();
    if (!query) return allSpeciesOptions;

    const startsWith = allSpeciesOptions.filter((name) => name.toLowerCase().startsWith(query));
    const includes = allSpeciesOptions.filter(
      (name) => !name.toLowerCase().startsWith(query) && name.toLowerCase().includes(query),
    );
    return [...startsWith, ...includes];
  }, [allSpeciesOptions, speciesQuery]);

  const visibleSpeciesOptions = useMemo(
    () => (speciesQuery.trim() ? filteredSpeciesOptions.slice(0, 60) : filteredSpeciesOptions.slice(0, 24)),
    [filteredSpeciesOptions, speciesQuery],
  );

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

  // Auto-fill weather conditions when location is resolved
  useEffect(() => {
    if (locationResolved && lat !== 0 && lon !== 0 && !autoWeather) {
      setWeatherLoading(true);
      fetchCurrentConditions(lat, lon)
        .then((conditions) => {
          if (conditions.airTemp != null || conditions.windSpeed != null) {
            setAutoWeather({
              airTemp: conditions.airTemp,
              windSpeed: conditions.windSpeed,
              windDirection: conditions.windDirection,
              pressure: conditions.pressure,
              cloudCover: conditions.cloudCover,
            });
          }
        })
        .catch(() => {
          // Weather auto-fill is best-effort; silent fail
        })
        .finally(() => setWeatherLoading(false));
    }
  }, [locationResolved, lat, lon]);

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

  const canSubmit = !submitting;

  /** Run AI species identification on a photo URI. Silent on failure. */
  const runSpeciesAI = async (photoUri: string) => {
    setAiIdentifying(true);
    try {
      const result = await identifySpecies(photoUri);
      if (result && result.confidence >= 60) {
        setAiResult(result);
        setSpecies(result.speciesName);
        setSpeciesQuery(result.speciesName);
        setSpeciesAutoFilled(true);
        // Auto-fill species-specific hints
        if (result.hints) {
          if (!bait.trim() && result.hints.commonBaits.length > 0) {
            setBait(result.hints.commonBaits[0]);
          }
          if (!technique.trim() && result.hints.topTechnique) {
            setTechnique(result.hints.topTechnique);
          }
        }
      }
      // If confidence < 60 or null result, do nothing — silent failure
    } catch {
      // Never block the UX
    } finally {
      setAiIdentifying(false);
    }
  };

  /** Clear AI auto-fill state when user manually changes species. */
  const handleManualSpeciesChange = (sp: string) => {
    setSpecies(sp);
    setSpeciesQuery(sp);
    setSpeciesAutoFilled(false);
    setAiResult(null);
  };

  const handleTakePhoto = async () => {
    const photo = await takeCatchPhoto();
    if (photo) {
      setPhotos((prev) => [...prev, photo]);
      // Run AI identification on the first photo taken
      if (photos.length === 0) {
        runSpeciesAI(photo.uri);
      }
    }
  };

  const handlePickPhoto = async () => {
    const photo = await pickCatchPhoto();
    if (photo) {
      setPhotos((prev) => [...prev, photo]);
      // Run AI identification on the first photo picked
      if (photos.length === 0) {
        runSpeciesAI(photo.uri);
      }
    }
  };

  const handlePhotoAction = () => {
    Alert.alert(
      'Add Photo',
      'Choose a photo source',
      [
        { text: 'Camera', onPress: handleTakePhoto },
        { text: 'Photo Library', onPress: handlePickPhoto },
        { text: 'Cancel', style: 'cancel' },
      ],
    );
  };

  const removePhoto = (idx: number) => {
    setPhotos((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);

    const now = new Date();
    const speciesInput = species.trim();
    const speciesLabel = speciesInput || 'Unspecified Catch';
    const catchCount = parseOptionalInteger(numberCaught);
    const keptCount = parseOptionalInteger(numberKept);
    const weightLb = parseOptionalDecimal(largestWeight);
    const lengthVal = parseOptionalDecimal(lengthIn);
    const effortHours = Math.max(0.5, (catchCount ?? 0) > 0 ? 2.0 : 1.0);

    if (numberCaught.trim() && catchCount == null) {
      Alert.alert('Check catch count', 'Enter a whole number for fish caught, or leave it blank.');
      setSubmitting(false);
      return;
    }

    if (numberKept.trim() && keptCount == null) {
      Alert.alert('Check kept count', 'Enter a whole number for fish kept, or leave it blank.');
      setSubmitting(false);
      return;
    }

    if (largestWeight.trim() && weightLb == null) {
      Alert.alert('Check weight', 'Enter a valid weight, or leave it blank.');
      setSubmitting(false);
      return;
    }

    if (lengthIn.trim() && lengthVal == null) {
      Alert.alert('Check length', 'Enter a valid length, or leave it blank.');
      setSubmitting(false);
      return;
    }

    if (catchCount != null && keptCount != null && keptCount > catchCount) {
      Alert.alert('Check kept count', 'Number kept cannot exceed number caught.');
      setSubmitting(false);
      return;
    }

    let saved: EnhancedCatch;
    try {
      const catchData: Omit<EnhancedCatch, 'id'> = {
        species: speciesLabel,
        catchCount,
        keptCount,
        rating: rating > 0 ? rating : undefined,
        weight: weightLb,
        length: lengthVal,
        lat,
        lon,
        photos,
        bait: bait.trim() || undefined,
        technique: technique.trim() || undefined,
        airTemp: autoWeather?.airTemp,
        windSpeed: autoWeather?.windSpeed,
        windDirection: autoWeather?.windDirection,
        pressure: autoWeather?.pressure,
        cloudCover: autoWeather?.cloudCover,
        notes: notes.trim() || undefined,
        released: keptCount == null ? true : keptCount === 0,
        timestamp: now.getTime(),
      };

      saved = await saveCatch(catchData);
    } catch (localErr) {
      console.warn('[CatchReport] Failed to save locally:', localErr);
      Alert.alert(
        'Unable to save catch',
        'We could not save this catch to your log right now. Please try again.',
      );
      setSubmitting(false);
      return;
    }

    let syncedRemotely = false;
    const shouldSyncRemotely = (
      speciesInput.length > 0 &&
      catchCount != null &&
      rating > 0 &&
      hasUsableCoordinates(lat, lon)
    );

    try {
      if (shouldSyncRemotely) {
        const reportV2: CatchReportV2Create = {
          user_id: 'mobile-user',
          lat,
          lon,
          trip_start: new Date(now.getTime() - effortHours * 3600000).toISOString(),
          trip_end: now.toISOString(),
          effort_hours: effortHours,
          species: speciesInput.toLowerCase().replace(/\s+/g, '_'),
          catch_count: catchCount,
          kept_count: keptCount ?? 0,
          largest_weight_lb: weightLb,
          rating,
          reported_at: now.toISOString(),
          notes: notes.trim() || undefined,
        };

        const legacyReport: CatchReport = {
          species: speciesInput,
          numberCaught: catchCount,
          numberKept: keptCount ?? 0,
          largestWeight: weightLb,
          rating,
          lat,
          lon,
          date: now.toISOString(),
          notes: notes.trim() || undefined,
          photoUri: photos[0]?.uri ?? undefined,
        };

        try {
          await api.submitCatchReportV2(reportV2);
          syncedRemotely = true;
        } catch (v2Err) {
          if (v2Err instanceof ApiError && v2Err.status >= 400 && v2Err.status < 500) {
            throw v2Err;
          }
          await api.submitCatchReport(legacyReport);
          syncedRemotely = true;
        }
      }
    } catch (err) {
      console.warn('[CatchReport] Remote sync failed after local save:', err);
    }

    if (weightLb && weightLb > 0 && speciesInput.length > 0) {
      const { getPersonalBests } = await import('../services/catchEnhancements');
      const bests = await getPersonalBests();
      const pb = bests.find(
        (b) => b.species.toLowerCase() === speciesLabel.toLowerCase() && b.catchId === saved.id,
      );
      if (pb) {
        const pbMessage = syncedRemotely
          ? `${weightLb} lb ${speciesLabel} is your new record.\n\nSaved to your log, activity calendar, and synced. Share it now?`
          : `${weightLb} lb ${speciesLabel} is your new record.\n\nSaved to your log and activity calendar. Share it now?`;
        Alert.alert(
          'New Personal Best!',
          pbMessage,
          [
            { text: 'Skip', onPress: () => navigation.goBack() },
            {
              text: 'Share',
              onPress: async () => {
                await shareCatchToSocial(saved);
                navigation.goBack();
              },
            },
          ],
        );
        setSubmitting(false);
        return;
      }
    }

    const successMessage = syncedRemotely
      ? 'Saved to your log, activity calendar, and synced.'
      : 'Saved to your log and activity calendar.';
    const shouldOfferShare = (
      speciesInput.length > 0 ||
      weightLb != null ||
      lengthVal != null ||
      photos.length > 0 ||
      !!notes.trim()
    );

    if (shouldOfferShare) {
      const preview = generateCatchPreview(saved);
      Alert.alert(
        'Catch Saved!',
        `${successMessage}\n\n${preview}\n\nShare it now?`,
        [
          { text: 'Skip', onPress: () => navigation.goBack() },
          {
            text: 'Share',
            onPress: async () => {
              await shareCatchToSocial(saved);
              navigation.goBack();
            },
          },
        ],
      );
    } else {
      Alert.alert('Catch Saved!', successMessage, [
        { text: 'OK', onPress: () => navigation.goBack() },
      ]);
    }

    setSubmitting(false);
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
          onPress={handlePhotoAction}
        >
          {photos.length > 0 ? (
            <View>
              <Image source={{ uri: photos[photos.length - 1].uri }} style={styles.cameraPreview} />
              <View style={styles.photoCountBadge}>
                <Text style={styles.photoCountText}>{photos.length} photo{photos.length > 1 ? 's' : ''}</Text>
              </View>
            </View>
          ) : (
            <View style={styles.cameraPlaceholder}>
              <Ionicons name="camera-outline" size={32} color={palette.accent} />
              <Text style={styles.cameraTitle}>Take a Photo</Text>
              <Text style={styles.cameraSubtitle}>Tap to use camera or select from gallery</Text>
            </View>
          )}
        </Pressable>

        {/* Photo thumbnails row */}
        {photos.length > 1 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.photoRow}>
            {photos.map((photo, idx) => (
              <Pressable key={idx} onLongPress={() => removePhoto(idx)} style={styles.photoThumb}>
                <Image source={{ uri: photo.uri }} style={styles.photoThumbImage} />
              </Pressable>
            ))}
          </ScrollView>
        )}

        {/* AI Species Identification Banner */}
        {aiIdentifying && (
          <View style={styles.aiBanner}>
            <ActivityIndicator size="small" color={palette.accent} />
            <Text style={styles.aiBannerText}>Identifying species...</Text>
          </View>
        )}
        {aiResult && speciesAutoFilled && !aiIdentifying && (
          <View style={styles.aiBannerSuccess}>
            <View style={styles.aiBannerRow}>
              <Ionicons name="checkmark-circle" size={18} color={palette.success} />
              <Text style={styles.aiBannerSuccessText}>
                Identified: {aiResult.speciesName} ({aiResult.confidence}% confident)
              </Text>
            </View>
            <Pressable
              onPress={() => {
                setSpeciesAutoFilled(false);
                setAiResult(null);
              }}
              hitSlop={8}
            >
              <Text style={styles.aiBannerChange}>Change</Text>
            </Pressable>
          </View>
        )}

        {/* Auto-captured info + weather conditions */}
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

          {/* Weather auto-fill display */}
          {weatherLoading && (
            <View style={styles.autoRow}>
              <ActivityIndicator size="small" color={palette.accent} />
              <Text style={styles.autoText}>Fetching weather conditions...</Text>
            </View>
          )}
          {autoWeather && (
            <View style={styles.weatherAutoFill}>
              <View style={styles.autoRow}>
                <Ionicons name="partly-sunny-outline" size={16} color={palette.accent} />
                <Text style={styles.autoTextAccent}>Weather auto-filled</Text>
              </View>
              <View style={styles.weatherGrid}>
                {autoWeather.airTemp != null && (
                  <View style={styles.weatherChip}>
                    <Ionicons name="thermometer-outline" size={12} color={palette.textSecondary} />
                    <Text style={styles.weatherChipText}>{autoWeather.airTemp}°F</Text>
                  </View>
                )}
                {autoWeather.windSpeed != null && (
                  <View style={styles.weatherChip}>
                    <Ionicons name="flag-outline" size={12} color={palette.textSecondary} />
                    <Text style={styles.weatherChipText}>
                      {autoWeather.windSpeed} mph {autoWeather.windDirection ?? ''}
                    </Text>
                  </View>
                )}
                {autoWeather.pressure != null && (
                  <View style={styles.weatherChip}>
                    <Ionicons name="speedometer-outline" size={12} color={palette.textSecondary} />
                    <Text style={styles.weatherChipText}>{autoWeather.pressure} hPa</Text>
                  </View>
                )}
                {autoWeather.cloudCover != null && (
                  <View style={styles.weatherChip}>
                    <Ionicons name="cloud-outline" size={12} color={palette.textSecondary} />
                    <Text style={styles.weatherChipText}>{autoWeather.cloudCover}</Text>
                  </View>
                )}
              </View>
            </View>
          )}
        </View>

        {/* Species selector — scrollable chip row */}
        <View style={styles.field}>
          <View style={styles.fieldLabelRow}>
            <Text style={styles.fieldLabel}>Species</Text>
            {speciesAutoFilled && (
              <View style={styles.aiBadge}>
                <Ionicons name="sparkles" size={10} color={palette.accent} />
                <Text style={styles.aiBadgeText}>AI</Text>
              </View>
            )}
            {aiIdentifying && (
              <View style={styles.aiIdentifyingLabel}>
                <ActivityIndicator size={10} color={palette.accent} />
                <Text style={styles.aiIdentifyingText}>Identifying...</Text>
              </View>
            )}
          </View>
          <TextInput
            style={styles.input}
            value={speciesQuery}
            onChangeText={(value) => {
              setSpeciesQuery(value);
              setSpeciesAutoFilled(false);
            }}
            placeholder="Search all species or leave blank..."
            placeholderTextColor={palette.textDim}
            autoCapitalize="words"
            autoCorrect={false}
          />
          <Text style={styles.speciesMetaText}>
            {filteredSpeciesOptions.length} match{filteredSpeciesOptions.length === 1 ? '' : 'es'} from {allSpeciesOptions.length} species
          </Text>
          <View style={styles.speciesSelectedRow}>
            <Text style={styles.speciesSelectedLabel}>Selected</Text>
            {species ? (
              <View style={[styles.speciesChip, styles.speciesChipActive]}>
                <Text style={[styles.speciesText, styles.speciesTextActive]}>{species}</Text>
              </View>
            ) : (
              <Text style={styles.speciesEmptyText}>Optional</Text>
            )}
          </View>
          <View style={styles.speciesChipWrap}>
            {visibleSpeciesOptions.map((sp) => (
              <Pressable
                key={sp}
                style={[
                  styles.speciesChip,
                  species === sp && styles.speciesChipActive,
                ]}
                onPress={() => handleManualSpeciesChange(sp)}
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
          </View>
          {!speciesQuery.trim() && allSpeciesOptions.length > visibleSpeciesOptions.length && (
            <Text style={styles.speciesHintText}>
              Type to search the full species catalog.
            </Text>
          )}
          {!!speciesQuery.trim() && filteredSpeciesOptions.length > visibleSpeciesOptions.length && (
            <Text style={styles.speciesHintText}>
              Narrow the search to see more than the first {visibleSpeciesOptions.length} matches.
            </Text>
          )}
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

        {/* Largest weight & length */}
        <View style={styles.rowFields}>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.fieldLabel}>Largest Weight (lbs)</Text>
            <TextInput
              style={styles.input}
              value={largestWeight}
              onChangeText={setLargestWeight}
              keyboardType="decimal-pad"
              placeholder="e.g. 4.5"
              placeholderTextColor={palette.textDim}
            />
          </View>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.fieldLabel}>Length (in)</Text>
            <TextInput
              style={styles.input}
              value={lengthIn}
              onChangeText={setLengthIn}
              keyboardType="decimal-pad"
              placeholder="e.g. 19"
              placeholderTextColor={palette.textDim}
            />
          </View>
        </View>

        {/* Bait & Technique */}
        <View style={styles.rowFields}>
          <View style={[styles.field, { flex: 1 }]}>
            <View style={styles.fieldLabelRow}>
              <Text style={styles.fieldLabel}>Bait / Lure</Text>
              {speciesAutoFilled && aiResult?.hints && bait === aiResult.hints.commonBaits[0] && (
                <View style={styles.aiBadge}>
                  <Ionicons name="sparkles" size={10} color={palette.accent} />
                  <Text style={styles.aiBadgeText}>AI</Text>
                </View>
              )}
            </View>
            <TextInput
              style={styles.input}
              value={bait}
              onChangeText={setBait}
              placeholder="e.g. Senko"
              placeholderTextColor={palette.textDim}
            />
          </View>
          <View style={[styles.field, { flex: 1 }]}>
            <View style={styles.fieldLabelRow}>
              <Text style={styles.fieldLabel}>Technique</Text>
              {speciesAutoFilled && aiResult?.hints && technique === aiResult.hints.topTechnique && (
                <View style={styles.aiBadge}>
                  <Ionicons name="sparkles" size={10} color={palette.accent} />
                  <Text style={styles.aiBadgeText}>AI</Text>
                </View>
              )}
            </View>
            <TextInput
              style={styles.input}
              value={technique}
              onChangeText={setTechnique}
              placeholder="e.g. Flipping"
              placeholderTextColor={palette.textDim}
            />
          </View>
        </View>

        {/* Species hints from AI */}
        {speciesAutoFilled && aiResult?.hints && (
          <View style={styles.speciesHintsCard}>
            <View style={styles.autoRow}>
              <Ionicons name="bulb-outline" size={14} color={palette.accent} />
              <Text style={styles.autoTextAccent}>Species tips for {aiResult.speciesName}</Text>
            </View>
            <View style={styles.weatherGrid}>
              <View style={styles.weatherChip}>
                <Ionicons name="scale-outline" size={12} color={palette.textSecondary} />
                <Text style={styles.weatherChipText}>{aiResult.hints.typicalWeightRange}</Text>
              </View>
              {aiResult.hints.commonBaits.slice(0, 3).map((b) => (
                <View key={b} style={styles.weatherChip}>
                  <Ionicons name="fish-outline" size={12} color={palette.textSecondary} />
                  <Text style={styles.weatherChipText}>{b}</Text>
                </View>
              ))}
            </View>
          </View>
        )}

        {/* Rating */}
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>How was the fishing? (optional)</Text>
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
            pressed && canSubmit && styles.submitPressed,
          ]}
          onPress={handleSubmit}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.submitText}>Save Catch</Text>
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
  photoCountBadge: {
    position: 'absolute',
    bottom: 8,
    right: 8,
    backgroundColor: 'rgba(0,0,0,0.7)',
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  photoCountText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '600',
  },
  photoRow: {
    gap: 8,
    paddingVertical: 4,
  },
  photoThumb: {
    width: 60,
    height: 60,
    borderRadius: 8,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: palette.border,
  },
  photoThumbImage: {
    width: '100%',
    height: '100%',
    resizeMode: 'cover',
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
  autoTextAccent: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '600',
  },
  weatherAutoFill: {
    marginTop: 4,
    paddingTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
    gap: 8,
  },
  weatherGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    paddingLeft: 26,
  },
  weatherChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.accentLight,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  weatherChipText: {
    color: palette.textSecondary,
    fontSize: 12,
    fontWeight: '500',
  },

  // ── AI Identification Banner ──────────────────────────────────
  aiBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: palette.accentLight,
    borderRadius: 8,
    padding: 12,
    borderWidth: 1,
    borderColor: palette.accent,
  },
  aiBannerText: {
    color: palette.accent,
    fontSize: 14,
    fontWeight: '600',
  },
  aiBannerSuccess: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#E8F5E9',
    borderRadius: 8,
    padding: 12,
    borderWidth: 1,
    borderColor: palette.success,
  },
  aiBannerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flex: 1,
  },
  aiBannerSuccessText: {
    color: palette.success,
    fontSize: 14,
    fontWeight: '600',
    flex: 1,
  },
  aiBannerChange: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  aiBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: palette.accentLight,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  aiBadgeText: {
    color: palette.accent,
    fontSize: 10,
    fontWeight: '700',
  },
  aiIdentifyingLabel: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  aiIdentifyingText: {
    color: palette.textMuted,
    fontSize: 11,
    fontStyle: 'italic',
  },
  fieldLabelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  speciesHintsCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    padding: 12,
    gap: 8,
    borderWidth: 1,
    borderColor: palette.borderLight,
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
  speciesMetaText: {
    color: palette.textDim,
    fontSize: 12,
  },
  speciesSelectedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  speciesSelectedLabel: {
    color: palette.textMuted,
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  speciesEmptyText: {
    color: palette.textMuted,
    fontSize: 13,
  },
  speciesChipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
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
  speciesHintText: {
    color: palette.textDim,
    fontSize: 12,
    lineHeight: 16,
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
