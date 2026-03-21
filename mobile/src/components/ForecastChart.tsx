import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Rect, Line, Text as SvgText } from 'react-native-svg';
import { palette, scoreColor } from '../theme/palette';
import type { ForecastDay } from '../types/models';

interface Props {
  forecast: ForecastDay[];
}

const CHART_HEIGHT = 140;
const BAR_GAP = 6;

export function ForecastChart({ forecast }: Props) {
  const barCount = forecast.length;

  return (
    <View style={styles.card}>
      <Text style={styles.title}>7-Day Forecast</Text>
      <View style={styles.chartContainer}>
        <Svg width="100%" height={CHART_HEIGHT + 30} viewBox={`0 0 ${barCount * 44} ${CHART_HEIGHT + 30}`}>
          {/* Reference lines */}
          {[25, 50, 75].map((level) => {
            const y = CHART_HEIGHT - (level / 100) * CHART_HEIGHT;
            return (
              <Line
                key={level}
                x1={0}
                y1={y}
                x2={barCount * 44}
                y2={y}
                stroke={palette.border}
                strokeWidth={0.5}
                strokeDasharray="4,4"
              />
            );
          })}
          {/* Bars */}
          {forecast.map((day, i) => {
            const barHeight = (day.score / 100) * CHART_HEIGHT;
            const x = i * 44 + BAR_GAP;
            const y = CHART_HEIGHT - barHeight;
            const color = scoreColor(day.score);
            return (
              <React.Fragment key={day.date}>
                <Rect
                  x={x}
                  y={y}
                  width={32}
                  height={barHeight}
                  rx={6}
                  fill={color}
                  opacity={0.85}
                />
                {/* Score label */}
                <SvgText
                  x={x + 16}
                  y={y - 5}
                  textAnchor="middle"
                  fontSize={10}
                  fontWeight="700"
                  fill={color}
                >
                  {day.score}
                </SvgText>
                {/* Day label */}
                <SvgText
                  x={x + 16}
                  y={CHART_HEIGHT + 16}
                  textAnchor="middle"
                  fontSize={10}
                  fontWeight="600"
                  fill={palette.textMuted}
                >
                  {day.dayLabel}
                </SvgText>
              </React.Fragment>
            );
          })}
        </Svg>
      </View>
      {/* Day detail row */}
      <View style={styles.detailRow}>
        {forecast.map((day) => (
          <View key={day.date} style={styles.dayDetail}>
            <Text style={styles.dayTemp}>
              {day.highTemp}\u00B0
            </Text>
            <Text style={styles.dayWind}>
              {day.windSpeed}mph
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 8,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: palette.border,
  },
  title: {
    color: palette.text,
    fontSize: 18,
    fontWeight: '600',
  },
  chartContainer: {
    alignItems: 'center',
  },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  dayDetail: {
    alignItems: 'center',
    gap: 2,
  },
  dayTemp: {
    color: palette.textSecondary,
    fontSize: 11,
    fontWeight: '600',
  },
  dayWind: {
    color: palette.textDim,
    fontSize: 10,
  },
});
