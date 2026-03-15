import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { NativeStackScreenProps } from '@react-navigation/native-stack';
import { SpotCard } from '../components/SpotCard';
import { conditionsService } from '../services/conditions';
import { RootStackParamList } from '../types/navigation';
import { SpotCardModel } from '../types/spots';
import { palette } from '../theme/palette';

type Props = NativeStackScreenProps<RootStackParamList, 'Home'>;

export function HomeScreen({ navigation }: Props) {
  const [spots, setSpots] = useState<SpotCardModel[]>([]);

  useEffect(() => {
    conditionsService.listSavedSpots().then(setSpots);
  }, []);

  return (
    <ScrollView contentContainerStyle={styles.content} style={styles.screen}>
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>iPhone shell</Text>
        <Text style={styles.title}>Conditions-first, validation-safe.</Text>
        <Text style={styles.body}>
          A thin CASTLINE surface for saved spots and future live conditions. Clean enough to demo,
          small enough not to distract from Phase 0.
        </Text>
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Saved spots</Text>
        <Text style={styles.sectionMeta}>{spots.length} ready</Text>
      </View>

      <View style={styles.stack}>
        {spots.map((spot) => (
          <SpotCard
            key={spot.id}
            spot={spot}
            onPress={() => navigation.navigate('SpotDetail', { spotId: spot.id })}
          />
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    padding: 20,
    paddingBottom: 32,
    gap: 20,
  },
  hero: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: 26,
    padding: 22,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 10,
  },
  eyebrow: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
  title: {
    color: palette.text,
    fontSize: 28,
    fontWeight: '700',
    lineHeight: 32,
  },
  body: {
    color: palette.textMuted,
    fontSize: 15,
    lineHeight: 22,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
  },
  sectionTitle: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },
  sectionMeta: {
    color: palette.textMuted,
    fontSize: 13,
  },
  stack: {
    gap: 14,
  },
});
