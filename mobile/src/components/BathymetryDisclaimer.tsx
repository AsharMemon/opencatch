/**
 * OpenCatch — Bathymetry Data Transparency Popup
 *
 * Shows on first map open (or when user taps "i" on depth data).
 * Explains that depth data comes from multiple sources with varying accuracy.
 * Dismissable, with "Don't show again" option.
 *
 * Required for user trust and regulatory compliance.
 */

import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
  Switch,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';

const STORAGE_KEY = '@opencatch/bathy_disclaimer_dismissed';

interface BathymetryDisclaimerProps {
  /** Force show even if previously dismissed. */
  forceShow?: boolean;
  onDismiss?: () => void;
}

interface DataSourceInfo {
  icon: string;
  name: string;
  description: string;
  accuracy: string;
  color: string;
}

const DATA_SOURCES: DataSourceInfo[] = [
  {
    icon: 'volume-high',
    name: 'Sonar Surveys',
    description:
      'Professional sonar surveys from state agencies (MN DNR, NOAA). The gold standard for depth data.',
    accuracy: 'Sub-meter accuracy',
    color: '#4CAF50',
  },
  {
    icon: 'boat',
    name: 'Nautical Charts',
    description:
      'NOAA hydrographic charts and coastal elevation models. Survey-grade data for US coastline and navigable waters.',
    accuracy: '1-3m accuracy',
    color: '#2196F3',
  },
  {
    icon: 'satellite',
    name: 'Satellite Models',
    description:
      'OpenCatch uses satellite imagery and machine learning to estimate depth where surveys are unavailable. Accuracy varies by water clarity and depth.',
    accuracy: '2-6m accuracy (varies)',
    color: '#FFC107',
  },
  {
    icon: 'analytics',
    name: 'Basin Estimates',
    description:
      'For deep or turbid lakes without survey data, we estimate general basin shape from terrain and similar lakes. These are approximate.',
    accuracy: 'Approximate only',
    color: '#FF9800',
  },
  {
    icon: 'water',
    name: 'River Estimates',
    description:
      'River depths are estimated from channel geometry and real-time flow data (USGS). Depths change with water level.',
    accuracy: 'Flow-dependent',
    color: '#00BCD4',
  },
];

export function BathymetryDisclaimer({ forceShow, onDismiss }: BathymetryDisclaimerProps) {
  const [visible, setVisible] = useState(false);
  const [dontShowAgain, setDontShowAgain] = useState(false);

  useEffect(() => {
    if (forceShow) {
      setVisible(true);
      return;
    }
    AsyncStorage.getItem(STORAGE_KEY).then((val) => {
      if (val !== 'true') {
        setVisible(true);
      }
    });
  }, [forceShow]);

  const handleDismiss = async () => {
    if (dontShowAgain) {
      await AsyncStorage.setItem(STORAGE_KEY, 'true');
    }
    setVisible(false);
    onDismiss?.();
  };

  if (!visible) return null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={handleDismiss}
    >
      <View style={styles.overlay}>
        <View style={styles.card}>
          <ScrollView showsVerticalScrollIndicator={false}>
            {/* Header */}
            <View style={styles.header}>
              <Ionicons name="information-circle" size={28} color="#2196F3" />
              <Text style={styles.title}>About Depth Data</Text>
            </View>

            <Text style={styles.intro}>
              OpenCatch combines multiple data sources to show you water depth.
              The accuracy varies by location and source:
            </Text>

            {/* Data sources */}
            {DATA_SOURCES.map((source, i) => (
              <View key={i} style={styles.sourceRow}>
                <View style={[styles.iconCircle, { backgroundColor: source.color + '20' }]}>
                  <Ionicons name={source.icon as any} size={20} color={source.color} />
                </View>
                <View style={styles.sourceText}>
                  <Text style={styles.sourceName}>{source.name}</Text>
                  <Text style={styles.sourceDesc}>{source.description}</Text>
                  <Text style={[styles.sourceAccuracy, { color: source.color }]}>
                    {source.accuracy}
                  </Text>
                </View>
              </View>
            ))}

            {/* Safety warning */}
            <View style={styles.warningBox}>
              <Ionicons name="warning" size={20} color="#F44336" />
              <Text style={styles.warningText}>
                Depth data is for informational purposes only.{' '}
                <Text style={styles.bold}>
                  Never rely solely on this data for navigation safety.
                </Text>{' '}
                Always use proper nautical charts, sonar, and visual observation
                when operating a vessel.
              </Text>
            </View>

            {/* How to see data quality */}
            <Text style={styles.tipText}>
              <Ionicons name="color-palette" size={14} color="#666" />{' '}
              Tip: Toggle "Data Quality" in the overlay menu to see which
              source is used for each area. Tap any water body for accuracy details.
            </Text>

            {/* Don't show again */}
            <View style={styles.dontShowRow}>
              <Switch
                value={dontShowAgain}
                onValueChange={setDontShowAgain}
                trackColor={{ false: '#E0E0E0', true: '#81C784' }}
                thumbColor={dontShowAgain ? '#4CAF50' : '#FAFAFA'}
              />
              <Text style={styles.dontShowText}>Don't show this again</Text>
            </View>
          </ScrollView>

          {/* Dismiss button */}
          <TouchableOpacity style={styles.button} onPress={handleDismiss}>
            <Text style={styles.buttonText}>Got it</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 20,
    padding: 24,
    maxHeight: '85%',
    width: '100%',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.15,
    shadowRadius: 24,
    elevation: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 12,
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
    fontFamily: 'PlayfairDisplay_700Bold',
    color: '#1A1A1A',
  },
  intro: {
    fontSize: 14,
    color: '#555',
    lineHeight: 20,
    marginBottom: 16,
  },
  sourceRow: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 14,
  },
  iconCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sourceText: {
    flex: 1,
  },
  sourceName: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1A1A1A',
    marginBottom: 2,
  },
  sourceDesc: {
    fontSize: 12,
    color: '#666',
    lineHeight: 17,
  },
  sourceAccuracy: {
    fontSize: 11,
    fontWeight: '600',
    marginTop: 2,
  },
  warningBox: {
    flexDirection: 'row',
    gap: 10,
    backgroundColor: '#FFF3F3',
    borderRadius: 12,
    padding: 14,
    marginTop: 8,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#FFCDD2',
  },
  warningText: {
    flex: 1,
    fontSize: 12,
    color: '#B71C1C',
    lineHeight: 17,
  },
  bold: {
    fontWeight: '700',
  },
  tipText: {
    fontSize: 12,
    color: '#888',
    lineHeight: 17,
    marginBottom: 16,
  },
  dontShowRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 8,
  },
  dontShowText: {
    fontSize: 13,
    color: '#666',
  },
  button: {
    backgroundColor: '#2196F3',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 8,
  },
  buttonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
});

export default BathymetryDisclaimer;
