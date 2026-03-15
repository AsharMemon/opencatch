import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { NativeStackScreenProps } from '@react-navigation/native-stack';
import { conditionsService } from '../services/conditions';
import { RootStackParamList } from '../types/navigation';
import { SpotConditions } from '../types/spots';
import { palette } from '../theme/palette';

type Props = NativeStackScreenProps<RootStackParamList, 'SpotDetail'>;

export function SpotDetailScreen({ route }: Props) {
  const [conditions, setConditions] = useState<SpotConditions | null>(null);

  useEffect(() => {
    conditionsService.getSpotConditions(route.params.spotId).then(setConditions);
  }, [route.params.spotId]);

  if (!conditions) {
    return <View style={styles.screen} />;
  }

  return (
    <ScrollView contentContainerStyle={styles.content} style={styles.screen}>
      <View style={styles.hero}>
        <Text style={styles.kicker}>Placeholder conditions view</Text>
        <Text style={styles.summary}>{conditions.summary}</Text>
        <Text style={styles.updatedAt}>{conditions.updatedAt}</Text>
      </View>

      <View style={styles.panel}>
        <LabelValue label="Water" value={conditions.waterTemp} />
        <LabelValue label="Flow" value={conditions.discharge} />
        <LabelValue label="Trend" value={conditions.trend} />
      </View>

      <View style={styles.callout}>
        <Text style={styles.calloutTitle}>Next connection</Text>
        <Text style={styles.calloutBody}>{conditions.outlook}</Text>
      </View>
    </ScrollView>
  );
}

function LabelValue({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metricRow}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    gap: 18,
  },
  hero: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: 26,
    padding: 22,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 8,
  },
  kicker: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.9,
  },
  summary: {
    color: palette.text,
    fontSize: 24,
    lineHeight: 30,
    fontWeight: '700',
  },
  updatedAt: {
    color: palette.textMuted,
    fontSize: 13,
  },
  panel: {
    backgroundColor: palette.surface,
    borderRadius: 20,
    padding: 18,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 14,
  },
  metricRow: {
    gap: 6,
  },
  metricLabel: {
    color: palette.textMuted,
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  metricValue: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },
  callout: {
    backgroundColor: 'rgba(111, 214, 255, 0.08)',
    borderRadius: 20,
    padding: 18,
    borderWidth: 1,
    borderColor: 'rgba(111, 214, 255, 0.22)',
    gap: 6,
  },
  calloutTitle: {
    color: palette.accent,
    fontSize: 14,
    fontWeight: '700',
  },
  calloutBody: {
    color: palette.textMuted,
    fontSize: 15,
    lineHeight: 21,
  },
});
