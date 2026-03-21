import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { palette, scoreColor, scoreLabel } from '../theme/palette';

interface ScoreGaugeProps {
  score: number;
  size?: number;
  strokeWidth?: number;
}

export function ScoreGauge({ score, size = 160, strokeWidth = 10 }: ScoreGaugeProps) {
  const center = size / 2;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 100) * circumference;
  const color = scoreColor(score);
  const label = scoreLabel(score);

  return (
    <View style={[styles.container, { width: size, height: size }]}>
      <Svg width={size} height={size}>
        {/* Background track */}
        <Circle
          cx={center}
          cy={center}
          r={radius}
          stroke={palette.border}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
          transform={`rotate(-90, ${center}, ${center})`}
        />
        {/* Progress arc */}
        <Circle
          cx={center}
          cy={center}
          r={radius}
          stroke={color}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${progress} ${circumference}`}
          transform={`rotate(-90, ${center}, ${center})`}
        />
      </Svg>
      <View style={styles.labelContainer}>
        <Text style={[styles.scoreText, { color, fontSize: size * 0.25 }]}>{score}</Text>
        <Text style={[styles.labelText, { color: palette.textMuted, fontSize: size * 0.09 }]}>
          {label}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  labelContainer: {
    position: 'absolute',
    alignItems: 'center',
    gap: 2,
  },
  scoreText: {
    fontWeight: '700',
  },
  labelText: {
    fontWeight: '600',
  },
});
