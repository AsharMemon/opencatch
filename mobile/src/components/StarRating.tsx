import React from 'react';
import { Pressable, View, Text, StyleSheet } from 'react-native';
import { palette } from '../theme/palette';

interface Props {
  rating: number;
  onRate: (value: number) => void;
  size?: number;
}

export function StarRating({ rating, onRate, size = 36 }: Props) {
  return (
    <View style={styles.row}>
      {[1, 2, 3, 4, 5].map((star) => (
        <Pressable key={star} onPress={() => onRate(star)} hitSlop={6}>
          <Text style={[styles.star, { fontSize: size }]}>
            {star <= rating ? '\u2605' : '\u2606'}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 6,
    justifyContent: 'center',
  },
  star: {
    color: palette.warning,
  },
});
