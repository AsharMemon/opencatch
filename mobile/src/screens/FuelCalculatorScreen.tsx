/**
 * OpenCatch — Fuel Calculator Screen
 *
 * Input trip distance, speed, fuel consumption rate (from boat profile or manual).
 * Output: fuel needed, estimated cost, range with current fuel.
 * Sliders for speed/fuel to see tradeoffs.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles, fonts } from '../theme/typography';
import {
  calculateTrip,
  calculateRangeDetails,
  formatDuration,
  getDefaultFuelProfile,
  FUEL_CONSUMPTION_ESTIMATES,
  type BoatFuelProfile,
} from '../services/fuelCalculator';
import { getDefaultBoat, BOAT_TYPE_LABELS, type BoatProfile } from '../services/boatProfile';

// ── Slider-like stepper ─────────────────────────────────────────────────────

function ValueStepper({
  label,
  value,
  unit,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const decrement = () => onChange(Math.max(min, value - step));
  const increment = () => onChange(Math.min(max, value + step));

  return (
    <View style={styles.stepperRow}>
      <Text style={styles.stepperLabel}>{label}</Text>
      <View style={styles.stepperControls}>
        <Pressable style={styles.stepperBtn} onPress={decrement}>
          <Ionicons name="remove" size={18} color={palette.accent} />
        </Pressable>
        <Text style={styles.stepperValue}>
          {value % 1 === 0 ? value : value.toFixed(1)} <Text style={styles.stepperUnit}>{unit}</Text>
        </Text>
        <Pressable style={styles.stepperBtn} onPress={increment}>
          <Ionicons name="add" size={18} color={palette.accent} />
        </Pressable>
      </View>
    </View>
  );
}

// ── Main Screen ─────────────────────────────────────────────────────────────

export function FuelCalculatorScreen() {
  const [boat, setBoat] = useState<BoatProfile | null>(null);
  const [fuelProfile, setFuelProfile] = useState<BoatFuelProfile | null>(null);

  // Inputs
  const [distance, setDistance] = useState(20);   // nm
  const [speed, setSpeed] = useState(20);         // knots
  const [fuelRate, setFuelRate] = useState(6);     // GPH
  const [fuelPrice, setFuelPrice] = useState('4.50');
  const [currentFuel, setCurrentFuel] = useState(30); // gallons on board

  // Load boat profile
  useEffect(() => {
    (async () => {
      const b = await getDefaultBoat();
      setBoat(b);
      const profile = await getDefaultFuelProfile();
      if (profile) {
        setFuelProfile(profile);
        setSpeed(profile.cruiseSpeedKnots);
        setFuelRate(profile.fuelConsumptionGPH);
        setCurrentFuel(profile.fuelCapacityGallons);
      }
    })();
  }, []);

  // Calculations
  const trip = useMemo(
    () => calculateTrip(distance, speed, fuelRate, parseFloat(fuelPrice) || undefined),
    [distance, speed, fuelRate, fuelPrice],
  );

  const range = useMemo(
    () => calculateRangeDetails(currentFuel, speed, fuelRate),
    [currentFuel, speed, fuelRate],
  );

  const fuelPercent = useMemo(() => {
    if (!fuelProfile || fuelProfile.fuelCapacityGallons <= 0) return 1;
    return Math.min(1, trip.fuelNeededGallons / fuelProfile.fuelCapacityGallons);
  }, [trip, fuelProfile]);

  const hasSufficientFuel = trip.fuelNeededGallons <= currentFuel;

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Boat info */}
        {boat && (
          <View style={styles.boatCard}>
            <Ionicons name="boat" size={20} color={palette.accent} />
            <View style={{ flex: 1 }}>
              <Text style={styles.boatName}>{boat.name}</Text>
              <Text style={styles.boatType}>{BOAT_TYPE_LABELS[boat.type]}{boat.engineType ? ` — ${boat.engineType}` : ''}</Text>
            </View>
          </View>
        )}

        {/* Input controls */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Trip Parameters</Text>

          <ValueStepper
            label="Distance"
            value={distance}
            unit="nm"
            min={1}
            max={500}
            step={5}
            onChange={setDistance}
          />

          <ValueStepper
            label="Speed"
            value={speed}
            unit="kn"
            min={1}
            max={60}
            step={1}
            onChange={setSpeed}
          />

          <ValueStepper
            label="Fuel Rate"
            value={fuelRate}
            unit="GPH"
            min={0.5}
            max={50}
            step={0.5}
            onChange={setFuelRate}
          />

          <ValueStepper
            label="Fuel on Board"
            value={currentFuel}
            unit="gal"
            min={1}
            max={500}
            step={5}
            onChange={setCurrentFuel}
          />

          <View style={styles.priceRow}>
            <Text style={styles.stepperLabel}>Fuel Price</Text>
            <View style={styles.priceInput}>
              <Text style={styles.pricePrefix}>$</Text>
              <TextInput
                style={styles.priceField}
                value={fuelPrice}
                onChangeText={setFuelPrice}
                keyboardType="decimal-pad"
                placeholder="4.50"
                placeholderTextColor={palette.textDim}
              />
              <Text style={styles.priceUnit}>/gal</Text>
            </View>
          </View>
        </View>

        {/* Results */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Trip Estimate</Text>

          <View style={styles.resultGrid}>
            <View style={styles.resultItem}>
              <Ionicons name="water" size={20} color={palette.accent} />
              <Text style={styles.resultValue}>{trip.fuelNeededGallons.toFixed(1)}</Text>
              <Text style={styles.resultLabel}>Gallons Needed</Text>
            </View>

            <View style={styles.resultItem}>
              <Ionicons name="time-outline" size={20} color={palette.accent} />
              <Text style={styles.resultValue}>{formatDuration(trip.tripTimeHours)}</Text>
              <Text style={styles.resultLabel}>Trip Time</Text>
            </View>

            {trip.costEstimate != null && (
              <View style={styles.resultItem}>
                <Ionicons name="cash-outline" size={20} color={palette.accent} />
                <Text style={styles.resultValue}>${trip.costEstimate.toFixed(2)}</Text>
                <Text style={styles.resultLabel}>Fuel Cost</Text>
              </View>
            )}

            <View style={styles.resultItem}>
              <Ionicons name={hasSufficientFuel ? 'checkmark-circle' : 'alert-circle'} size={20} color={hasSufficientFuel ? palette.success : palette.error} />
              <Text style={[styles.resultValue, { color: hasSufficientFuel ? palette.success : palette.error }]}>
                {hasSufficientFuel ? 'Yes' : 'No'}
              </Text>
              <Text style={styles.resultLabel}>Enough Fuel?</Text>
            </View>
          </View>

          {/* Fuel gauge bar */}
          <View style={styles.gaugeContainer}>
            <Text style={styles.gaugeLabel}>
              {trip.fuelNeededGallons.toFixed(1)} of {currentFuel} gal
            </Text>
            <View style={styles.gaugeTrack}>
              <View
                style={[
                  styles.gaugeFill,
                  {
                    width: `${Math.min(100, fuelPercent * 100)}%`,
                    backgroundColor: hasSufficientFuel ? palette.accent : palette.error,
                  },
                ]}
              />
            </View>
          </View>
        </View>

        {/* Range with current fuel */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Range with Current Fuel</Text>
          <View style={styles.resultGrid}>
            <View style={styles.resultItem}>
              <Ionicons name="navigate" size={20} color="#1565C0" />
              <Text style={styles.resultValue}>{range.rangeNm.toFixed(0)}</Text>
              <Text style={styles.resultLabel}>Nautical Miles</Text>
            </View>
            <View style={styles.resultItem}>
              <Ionicons name="car-outline" size={20} color="#1565C0" />
              <Text style={styles.resultValue}>{range.rangeMi.toFixed(0)}</Text>
              <Text style={styles.resultLabel}>Statute Miles</Text>
            </View>
            <View style={styles.resultItem}>
              <Ionicons name="time-outline" size={20} color="#1565C0" />
              <Text style={styles.resultValue}>{formatDuration(range.tripTimeHours)}</Text>
              <Text style={styles.resultLabel}>Running Time</Text>
            </View>
          </View>
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 16,
  },
  boatCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: palette.accentLight,
    borderRadius: 12,
    padding: 14,
  },
  boatName: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.text,
  },
  boatType: {
    fontSize: 12,
    color: palette.textSecondary,
  },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    padding: 16,
    gap: 14,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  sectionTitle: {
    fontFamily: fonts.serif,
    fontSize: 16,
    color: palette.text,
    letterSpacing: -0.2,
  },
  stepperRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  stepperLabel: {
    fontSize: 14,
    color: palette.textSecondary,
    flex: 1,
  },
  stepperControls: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  stepperBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: palette.accentDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepperValue: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
    minWidth: 70,
    textAlign: 'center',
  },
  stepperUnit: {
    fontSize: 12,
    fontWeight: '400',
    color: palette.textMuted,
  },
  priceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  priceInput: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
  },
  pricePrefix: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
  },
  priceField: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.text,
    fontVariant: ['tabular-nums'],
    width: 60,
    textAlign: 'center',
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
    paddingVertical: 4,
  },
  priceUnit: {
    fontSize: 12,
    color: palette.textMuted,
  },
  resultGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  resultItem: {
    width: '46%',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 10,
    padding: 14,
  },
  resultValue: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  resultLabel: {
    fontSize: 11,
    color: palette.textMuted,
    textAlign: 'center',
  },
  gaugeContainer: {
    gap: 6,
  },
  gaugeLabel: {
    fontSize: 12,
    color: palette.textSecondary,
    textAlign: 'center',
  },
  gaugeTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: palette.surfaceRaised,
    overflow: 'hidden',
  },
  gaugeFill: {
    height: '100%',
    borderRadius: 4,
  },
});
