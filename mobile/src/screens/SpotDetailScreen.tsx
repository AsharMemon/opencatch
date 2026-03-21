/**
 * Legacy SpotDetailScreen - replaced by LocationDetailScreen.
 * Kept for reference; not currently mounted in navigation.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { palette } from '../theme/palette';

export function SpotDetailScreen() {
  return (
    <View style={styles.screen}>
      <Text style={styles.text}>Replaced by LocationDetailScreen</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.background, alignItems: 'center', justifyContent: 'center' },
  text: { color: palette.textMuted, fontSize: 14 },
});
