import React, { useEffect, useState, useMemo } from 'react';
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  Dimensions,
} from 'react-native';
import Svg, {
  Path,
  Line,
  Circle,
  Text as SvgText,
  Defs,
  LinearGradient,
  Stop,
  Rect,
  ClipPath,
} from 'react-native-svg';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  tidesService,
  type TideHourly,
  type TidePrediction,
  type NextTideInfo,
} from '../services/tidesService';

// ── Constants ────────────────────────────────────────────────────

const CHART_PADDING = { top: 28, right: 20, bottom: 36, left: 44 };
const CHART_HEIGHT = 220;
const SCREEN_H_PADDING = 20;

// ── Helpers ──────────────────────────────────────────────────────

/** Parse an ISO-ish time string into a Date object. */
function parseTime(iso: string): Date {
  return new Date(iso);
}

/** Format a Date to "h:mm a" (e.g. "6:12 AM"). */
function formatTime(d: Date): string {
  let h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, '0');
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m} ${ampm}`;
}

/** Format a Date to "Mon, Mar 20". */
function formatDate(d: Date): string {
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

/** Get the fractional hour (0-24) from a Date. */
function fractionalHour(d: Date): number {
  return d.getHours() + d.getMinutes() / 60;
}

/**
 * Build a smooth cubic Bezier SVG path through a series of (x, y) points.
 * Uses Catmull-Rom to Bezier conversion for natural-looking curves.
 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (points.length < 2) return '';
  if (points.length === 2) {
    return `M${points[0].x},${points[0].y} L${points[1].x},${points[1].y}`;
  }

  let d = `M${points[0].x},${points[0].y}`;

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(i - 1, 0)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(i + 2, points.length - 1)];

    // Catmull-Rom tension factor
    const t = 0.35;

    const cp1x = p1.x + ((p2.x - p0.x) * t);
    const cp1y = p1.y + ((p2.y - p0.y) * t);
    const cp2x = p2.x - ((p3.x - p1.x) * t);
    const cp2y = p2.y - ((p3.y - p1.y) * t);

    d += ` C${cp1x},${cp1y} ${cp2x},${cp2y} ${p2.x},${p2.y}`;
  }

  return d;
}

// ── Component ────────────────────────────────────────────────────

export function TideChartScreen({ route }: any) {
  const { stationId, stationName } = route.params as {
    stationId: string;
    stationName: string;
  };

  const [hourly, setHourly] = useState<TideHourly[]>([]);
  const [predictions, setPredictions] = useState<TidePrediction[]>([]);
  const [nextTide, setNextTide] = useState<NextTideInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [hourlyData, predData] = await Promise.all([
          tidesService.getTideHourly(stationId),
          tidesService.getTidePredictions(stationId, 3),
        ]);

        if (cancelled) return;

        setHourly(hourlyData);
        setPredictions(predData);
        setNextTide(tidesService.getNextTide(predData));
      } catch (err: any) {
        if (!cancelled) {
          setError(err.message ?? 'Failed to load tide data');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [stationId]);

  // ── Loading state ──────────────────────────────────────────────
  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={palette.accent} />
        <Text style={styles.loadingText}>Loading tide data...</Text>
      </View>
    );
  }

  // ── Error state ────────────────────────────────────────────────
  if (error) {
    return (
      <View style={styles.center}>
        <Ionicons name="warning-outline" size={48} color={palette.error} />
        <Text style={styles.errorTitle}>Unable to Load Tides</Text>
        <Text style={styles.errorMessage}>{error}</Text>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.scrollContent}
    >
      {/* Station header */}
      <StationHeader name={stationName} stationId={stationId} />

      {/* Tide curve chart */}
      <TideCurveChart hourly={hourly} predictions={predictions} />

      {/* Next tide card */}
      {nextTide && <NextTideCard info={nextTide} />}

      {/* Multi-day predictions */}
      <PredictionsList predictions={predictions} />
    </ScrollView>
  );
}

// ── Station Header ───────────────────────────────────────────────

function StationHeader({
  name,
  stationId,
}: {
  name: string;
  stationId: string;
}) {
  return (
    <View style={styles.headerContainer}>
      <View style={styles.headerIcon}>
        <Ionicons name="water" size={22} color={palette.water} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.screenTitle}>{name}</Text>
        <Text style={styles.stationSubtitle}>
          Station {stationId} &middot; NOAA CO-OPS
        </Text>
      </View>
    </View>
  );
}

// ── Tide Curve Chart ─────────────────────────────────────────────

function TideCurveChart({
  hourly,
  predictions,
}: {
  hourly: TideHourly[];
  predictions: TidePrediction[];
}) {
  const screenWidth = Dimensions.get('window').width;
  const chartWidth = screenWidth - SCREEN_H_PADDING * 2;
  const plotWidth = chartWidth - CHART_PADDING.left - CHART_PADDING.right;
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;

  const chartData = useMemo(() => {
    if (hourly.length === 0) return null;

    // Determine the start hour from the first data point
    const startDate = parseTime(hourly[0].time);
    const startHour = fractionalHour(startDate);

    // Compute min/max for Y axis
    const heights = hourly.map((h) => h.heightFt);
    const rawMin = Math.min(...heights);
    const rawMax = Math.max(...heights);
    const yPad = Math.max((rawMax - rawMin) * 0.15, 0.3);
    const yMin = rawMin - yPad;
    const yMax = rawMax + yPad;

    // Map hourly points to chart coordinates
    const points = hourly.map((h, i) => {
      const x =
        CHART_PADDING.left + (i / Math.max(hourly.length - 1, 1)) * plotWidth;
      const y =
        CHART_PADDING.top +
        plotHeight -
        ((h.heightFt - yMin) / (yMax - yMin)) * plotHeight;
      return { x, y, hour: fractionalHour(parseTime(h.time)), heightFt: h.heightFt };
    });

    // Map high/low predictions that fall within the chart time range
    const endDate = parseTime(hourly[hourly.length - 1].time);
    const hiloMarkers = predictions
      .filter((p) => {
        const t = parseTime(p.time);
        return t >= startDate && t <= endDate;
      })
      .map((p) => {
        const t = parseTime(p.time);
        const hoursFromStart =
          (t.getTime() - startDate.getTime()) / (1000 * 60 * 60);
        const frac = hoursFromStart / ((endDate.getTime() - startDate.getTime()) / (1000 * 60 * 60));
        const x = CHART_PADDING.left + frac * plotWidth;
        const y =
          CHART_PADDING.top +
          plotHeight -
          ((p.heightFt - yMin) / (yMax - yMin)) * plotHeight;
        return { x, y, prediction: p, time: t };
      });

    // Current time marker
    const now = new Date();
    let currentTimeX: number | null = null;
    if (now >= startDate && now <= endDate) {
      const frac =
        (now.getTime() - startDate.getTime()) /
        (endDate.getTime() - startDate.getTime());
      currentTimeX = CHART_PADDING.left + frac * plotWidth;
    }

    // Y axis tick values
    const yRange = yMax - yMin;
    const yTickCount = 5;
    const yTicks: number[] = [];
    for (let i = 0; i < yTickCount; i++) {
      yTicks.push(yMin + (yRange * i) / (yTickCount - 1));
    }

    // X axis labels (every 4 hours approximately)
    const totalHours = (endDate.getTime() - startDate.getTime()) / (1000 * 60 * 60);
    const xLabelStep = totalHours <= 12 ? 2 : 4;
    const xLabels: { x: number; label: string }[] = [];
    for (let i = 0; i < hourly.length; i++) {
      const h = parseTime(hourly[i].time);
      if (h.getMinutes() === 0 && h.getHours() % xLabelStep === 0) {
        const frac = i / Math.max(hourly.length - 1, 1);
        xLabels.push({
          x: CHART_PADDING.left + frac * plotWidth,
          label: formatTime(h),
        });
      }
    }

    return {
      points,
      hiloMarkers,
      currentTimeX,
      yMin,
      yMax,
      yTicks,
      xLabels,
    };
  }, [hourly, predictions, plotWidth, plotHeight]);

  if (!chartData || chartData.points.length < 2) {
    return (
      <View style={[styles.card, styles.chartEmpty]}>
        <Text style={styles.textMuted}>No hourly data available</Text>
      </View>
    );
  }

  const { points, hiloMarkers, currentTimeX, yMin, yMax, yTicks, xLabels } =
    chartData;

  const curvePath = smoothPath(points);

  // Build fill path (area under curve)
  const fillPath =
    curvePath +
    ` L${points[points.length - 1].x},${CHART_PADDING.top + plotHeight}` +
    ` L${points[0].x},${CHART_PADDING.top + plotHeight} Z`;

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>24-Hour Tide Curve</Text>
      <Svg width={chartWidth} height={CHART_HEIGHT}>
        <Defs>
          <LinearGradient id="tideFill" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={palette.water} stopOpacity="0.35" />
            <Stop offset="1" stopColor={palette.waterLight} stopOpacity="0.05" />
          </LinearGradient>
          <ClipPath id="plotClip">
            <Rect
              x={CHART_PADDING.left}
              y={CHART_PADDING.top}
              width={plotWidth}
              height={plotHeight}
            />
          </ClipPath>
        </Defs>

        {/* Y axis grid lines and labels */}
        {yTicks.map((val, i) => {
          const y =
            CHART_PADDING.top +
            plotHeight -
            ((val - yMin) / (yMax - yMin)) * plotHeight;
          return (
            <React.Fragment key={`y-${i}`}>
              <Line
                x1={CHART_PADDING.left}
                y1={y}
                x2={CHART_PADDING.left + plotWidth}
                y2={y}
                stroke={palette.borderLight}
                strokeWidth={1}
              />
              <SvgText
                x={CHART_PADDING.left - 8}
                y={y + 4}
                textAnchor="end"
                fill={palette.textMuted}
                fontSize={10}
              >
                {val.toFixed(1)}
              </SvgText>
            </React.Fragment>
          );
        })}

        {/* X axis labels */}
        {xLabels.map((lbl, i) => (
          <SvgText
            key={`x-${i}`}
            x={lbl.x}
            y={CHART_PADDING.top + plotHeight + 16}
            textAnchor="middle"
            fill={palette.textMuted}
            fontSize={10}
          >
            {lbl.label}
          </SvgText>
        ))}

        {/* Y axis label */}
        <SvgText
          x={12}
          y={CHART_PADDING.top + plotHeight / 2}
          textAnchor="middle"
          fill={palette.textMuted}
          fontSize={9}
          rotation={-90}
          originX={12}
          originY={CHART_PADDING.top + plotHeight / 2}
        >
          ft (MLLW)
        </SvgText>

        {/* Gradient fill under curve */}
        <Path
          d={fillPath}
          fill="url(#tideFill)"
          clipPath="url(#plotClip)"
        />

        {/* Smooth curve */}
        <Path
          d={curvePath}
          fill="none"
          stroke={palette.water}
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          clipPath="url(#plotClip)"
        />

        {/* Current time indicator */}
        {currentTimeX != null && (
          <>
            <Line
              x1={currentTimeX}
              y1={CHART_PADDING.top}
              x2={currentTimeX}
              y2={CHART_PADDING.top + plotHeight}
              stroke={palette.accent}
              strokeWidth={1.5}
              strokeDasharray="4,3"
            />
            <SvgText
              x={currentTimeX}
              y={CHART_PADDING.top - 6}
              textAnchor="middle"
              fill={palette.accent}
              fontSize={9}
              fontWeight="600"
            >
              Now
            </SvgText>
          </>
        )}

        {/* High/Low markers */}
        {hiloMarkers.map((m, i) => {
          const isHigh = m.prediction.type === 'H';
          const markerColor = isHigh ? palette.waterDeep : palette.water;
          const labelY = isHigh ? m.y - 14 : m.y + 18;

          return (
            <React.Fragment key={`hilo-${i}`}>
              <Circle
                cx={m.x}
                cy={m.y}
                r={5}
                fill={palette.surface}
                stroke={markerColor}
                strokeWidth={2.5}
              />
              <SvgText
                x={m.x}
                y={labelY}
                textAnchor="middle"
                fill={markerColor}
                fontSize={9}
                fontWeight="600"
              >
                {m.prediction.label} {m.prediction.heightFt.toFixed(1)}ft
              </SvgText>
              <SvgText
                x={m.x}
                y={labelY + 11}
                textAnchor="middle"
                fill={palette.textMuted}
                fontSize={8}
              >
                {formatTime(m.time)}
              </SvgText>
            </React.Fragment>
          );
        })}
      </Svg>
    </View>
  );
}

// ── Next Tide Card ───────────────────────────────────────────────

function NextTideCard({ info }: { info: NextTideInfo }) {
  const { prediction, timeRemaining } = info;
  const isHigh = prediction.type === 'H';

  return (
    <View style={[styles.card, styles.nextTideCard]}>
      <View style={styles.nextTideIconWrap}>
        <Ionicons
          name={isHigh ? 'arrow-up-circle' : 'arrow-down-circle'}
          size={32}
          color={isHigh ? palette.waterDeep : palette.water}
        />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.nextTideLabel}>
          Next {prediction.label} Tide
        </Text>
        <Text style={styles.nextTideTime}>in {timeRemaining}</Text>
        <Text style={styles.nextTideDetail}>
          {prediction.heightFt.toFixed(1)} ft at{' '}
          {formatTime(parseTime(prediction.time))}
        </Text>
      </View>
    </View>
  );
}

// ── Predictions List (3-day) ─────────────────────────────────────

function PredictionsList({
  predictions,
}: {
  predictions: TidePrediction[];
}) {
  // Group by date
  const grouped = useMemo(() => {
    const map = new Map<string, TidePrediction[]>();
    for (const p of predictions) {
      const d = parseTime(p.time);
      const key = formatDate(d);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(p);
    }
    return Array.from(map.entries());
  }, [predictions]);

  if (grouped.length === 0) return null;

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>3-Day Forecast</Text>
      {grouped.map(([dateStr, preds], gi) => (
        <View key={dateStr} style={gi > 0 ? styles.daySection : undefined}>
          <Text style={styles.dayLabel}>{dateStr}</Text>
          {preds.map((p, pi) => {
            const isHigh = p.type === 'H';
            return (
              <View key={pi} style={styles.predRow}>
                <View
                  style={[
                    styles.predTypeBadge,
                    {
                      backgroundColor: isHigh
                        ? palette.waterDeep
                        : palette.waterLight,
                    },
                  ]}
                >
                  <Ionicons
                    name={isHigh ? 'caret-up' : 'caret-down'}
                    size={12}
                    color={isHigh ? '#fff' : palette.waterDeep}
                  />
                  <Text
                    style={[
                      styles.predTypeText,
                      { color: isHigh ? '#fff' : palette.waterDeep },
                    ]}
                  >
                    {p.label}
                  </Text>
                </View>
                <Text style={styles.predTime}>
                  {formatTime(parseTime(p.time))}
                </Text>
                <Text style={styles.predHeight}>
                  {p.heightFt.toFixed(1)} ft
                </Text>
              </View>
            );
          })}
        </View>
      ))}
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  scrollContent: {
    padding: SCREEN_H_PADDING,
    paddingBottom: 40,
  },
  center: {
    flex: 1,
    backgroundColor: palette.background,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },

  // Loading / Error
  loadingText: {
    marginTop: 12,
    fontSize: 14,
    color: palette.textMuted,
  },
  errorTitle: {
    ...typeStyles.sectionHeader,
    color: palette.error,
    marginTop: 12,
  },
  errorMessage: {
    marginTop: 8,
    fontSize: 13,
    color: palette.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
  },

  // Header
  headerContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 20,
  },
  headerIcon: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: palette.waterLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  screenTitle: {
    ...typeStyles.screenTitle,
    color: palette.text,
  },
  stationSubtitle: {
    fontSize: 13,
    color: palette.textMuted,
    marginTop: 2,
  },

  // Cards
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    marginBottom: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderLight,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.04,
    shadowRadius: 3,
    elevation: 1,
  },
  cardTitle: {
    ...typeStyles.cardTitle,
    color: palette.text,
    marginBottom: 12,
  },
  chartEmpty: {
    alignItems: 'center',
    paddingVertical: 40,
  },
  textMuted: {
    fontSize: 13,
    color: palette.textMuted,
  },

  // Next Tide card
  nextTideCard: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  nextTideIconWrap: {
    marginRight: 14,
  },
  nextTideLabel: {
    ...typeStyles.cardTitle,
    color: palette.text,
  },
  nextTideTime: {
    fontSize: 20,
    fontWeight: '700',
    color: palette.accent,
    marginTop: 2,
  },
  nextTideDetail: {
    fontSize: 13,
    color: palette.textSecondary,
    marginTop: 3,
  },

  // Predictions list
  daySection: {
    marginTop: 14,
    paddingTop: 14,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.borderLight,
  },
  dayLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
    marginBottom: 8,
  },
  predRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 7,
  },
  predTypeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    width: 64,
  },
  predTypeText: {
    fontSize: 11,
    fontWeight: '600',
    marginLeft: 4,
  },
  predTime: {
    flex: 1,
    fontSize: 14,
    color: palette.text,
    marginLeft: 12,
  },
  predHeight: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
    textAlign: 'right',
    width: 60,
  },
});
