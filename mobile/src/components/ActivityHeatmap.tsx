import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { palette } from '../theme/palette';
import { getAllCatches, type EnhancedCatch } from '../services/catchEnhancements';
import { trackRecorder, type FishingTrack } from '../services/trackRecorder';

// ── Heatmap color scale ─────────────────────────────────────────────────────
// Empty → light → medium → dark green (GitHub-style, adapted to our palette)
const LEVEL_COLORS = [
  '#EDEDEA',           // 0 trips — surfaceRaised-ish gray
  '#B6E3B0',           // 1 trip  — light green
  '#5CB85C',           // 2 trips — medium green
  '#3D8B37',           // 3+ trips — palette.success (dark green)
] as const;

const CELL_SIZE = 13;
const CELL_GAP = 3;
const CELL_TOTAL = CELL_SIZE + CELL_GAP;
const LABEL_WIDTH = 28;
const WEEKS = 52;
const MONTH_LABELS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const DAY_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', ''] as const;

// ── Build trip counts from real data ────────────────────────────────────────

function buildTripData(
  catches: EnhancedCatch[],
  tracks: FishingTrack[],
): { trips: number[]; dateStrings: string[] } {
  const today = new Date();
  const totalDays = WEEKS * 7;

  // Start from `totalDays` days ago, aligned to Sunday
  const start = new Date(today);
  start.setDate(today.getDate() - totalDays + 1);
  start.setDate(start.getDate() - start.getDay());

  const trips: number[] = [];
  const dateStrings: string[] = [];

  // Build a map of date -> count from real data
  const dateCountMap = new Map<string, number>();

  for (const c of catches) {
    const d = new Date(c.timestamp).toISOString().slice(0, 10);
    dateCountMap.set(d, (dateCountMap.get(d) ?? 0) + 1);
  }

  for (const t of tracks) {
    const d = new Date(t.startTime).toISOString().slice(0, 10);
    dateCountMap.set(d, (dateCountMap.get(d) ?? 0) + 1);
  }

  for (let i = 0; i < totalDays; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const dateStr = d.toISOString().slice(0, 10);
    dateStrings.push(dateStr);

    if (d > today) {
      trips.push(0);
    } else {
      trips.push(dateCountMap.get(dateStr) ?? 0);
    }
  }

  return { trips, dateStrings };
}

// ── Stats derivation ────────────────────────────────────────────────────────

interface HeatmapStats {
  daysFished: number;
  totalTrips: number;
}

function computeStats(trips: number[]): HeatmapStats {
  let daysFished = 0;
  let totalTrips = 0;

  for (let i = 0; i < trips.length; i++) {
    if (trips[i] > 0) {
      daysFished++;
      totalTrips += trips[i];
    }
  }

  return { daysFished, totalTrips };
}

// ── Month label positions ───────────────────────────────────────────────────

function getMonthPositions(dateStrings: string[]): { label: string; col: number }[] {
  const positions: { label: string; col: number }[] = [];
  let lastMonth = -1;

  for (let i = 0; i < dateStrings.length; i++) {
    const month = parseInt(dateStrings[i].slice(5, 7), 10) - 1;
    const col = Math.floor(i / 7);
    if (month !== lastMonth) {
      // Only add if we haven't already placed this column
      if (positions.length === 0 || positions[positions.length - 1].col !== col) {
        positions.push({ label: MONTH_LABELS[month], col });
      }
      lastMonth = month;
    }
  }

  return positions;
}

// ── Component ───────────────────────────────────────────────────────────────

export function ActivityHeatmap() {
  const [catches, setCatches] = useState<EnhancedCatch[]>([]);
  const [tracks, setTracks] = useState<FishingTrack[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAllCatches(), trackRecorder.getSavedTracks()])
      .then(([c, t]) => {
        if (!cancelled) {
          setCatches(c);
          setTracks(t);
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => { cancelled = true; };
  }, []);

  const { trips, dateStrings } = useMemo(
    () => buildTripData(catches, tracks),
    [catches, tracks],
  );
  const stats = useMemo(() => computeStats(trips), [trips]);
  const monthPositions = useMemo(() => getMonthPositions(dateStrings), [dateStrings]);

  // Build flat cell array (column-major: week 0 day 0-6, week 1 day 0-6, ...)
  const cells = useMemo(() => {
    const result: { color: string; key: number }[] = new Array(trips.length);
    for (let i = 0; i < trips.length; i++) {
      const level = Math.min(trips[i], 3);
      result[i] = { color: LEVEL_COLORS[level], key: i };
    }
    return result;
  }, [trips]);

  return (
    <View style={s.container}>
      {/* Section title */}
      <Text style={s.sectionTitle}>Activity Calendar</Text>

      {/* Stats summary — streaks removed */}
      <View style={s.statsRow}>
        <StatPill label="Days fished" value={String(stats.daysFished)} />
        <StatPill label="Total trips" value={String(stats.totalTrips)} />
      </View>

      {/* Heatmap grid */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View>
          {/* Month labels */}
          <View style={[s.monthRow, { marginLeft: LABEL_WIDTH }]}>
            {monthPositions.map((mp) => (
              <Text
                key={`m-${mp.col}`}
                style={[s.monthLabel, { left: mp.col * CELL_TOTAL }]}
              >
                {mp.label}
              </Text>
            ))}
          </View>

          {/* Day labels + grid */}
          <View style={s.gridWrapper}>
            {/* Day-of-week labels */}
            <View style={s.dayLabels}>
              {DAY_LABELS.map((lbl, i) => (
                <View key={i} style={s.dayLabelCell}>
                  {lbl ? <Text style={s.dayLabelText}>{lbl}</Text> : null}
                </View>
              ))}
            </View>

            {/* The grid itself — rendered as columns (weeks) */}
            <View style={s.grid}>
              {Array.from({ length: WEEKS }, (_, weekIdx) => (
                <View key={weekIdx} style={s.column}>
                  {Array.from({ length: 7 }, (_, dayIdx) => {
                    const idx = weekIdx * 7 + dayIdx;
                    const cell = cells[idx];
                    return (
                      <View
                        key={cell.key}
                        style={[s.cell, { backgroundColor: cell.color }]}
                      />
                    );
                  })}
                </View>
              ))}
            </View>
          </View>

          {/* Legend */}
          <View style={s.legend}>
            <Text style={s.legendText}>Less</Text>
            {LEVEL_COLORS.map((c, i) => (
              <View key={i} style={[s.legendCell, { backgroundColor: c }]} />
            ))}
            <Text style={s.legendText}>More</Text>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ── Stat Pill sub-component ─────────────────────────────────────────────────

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.statPill}>
      <Text style={s.statPillValue}>{value}</Text>
      <Text style={s.statPillLabel}>{label}</Text>
    </View>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  container: {
    backgroundColor: palette.surface,
    borderRadius: 10,
    padding: 14,
    gap: 12,
  },
  sectionTitle: {
    color: palette.text,
    fontSize: 15,
    fontWeight: '600',
  },

  // Stats
  statsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 6,
  },
  statPill: {
    flex: 1,
    alignItems: 'center',
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  statPillValue: {
    color: palette.accent,
    fontSize: 16,
    fontWeight: '700',
  },
  statPillLabel: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '600',
    textAlign: 'center',
    marginTop: 2,
  },

  // Month labels row
  monthRow: {
    height: 16,
    position: 'relative',
    marginBottom: 4,
  },
  monthLabel: {
    position: 'absolute',
    top: 0,
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '500',
  },

  // Grid wrapper (labels + cells side by side)
  gridWrapper: {
    flexDirection: 'row',
  },
  dayLabels: {
    width: LABEL_WIDTH,
    gap: CELL_GAP,
  },
  dayLabelCell: {
    height: CELL_SIZE,
    justifyContent: 'center',
  },
  dayLabelText: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '500',
  },

  // Grid
  grid: {
    flexDirection: 'row',
    gap: CELL_GAP,
  },
  column: {
    gap: CELL_GAP,
  },
  cell: {
    width: CELL_SIZE,
    height: CELL_SIZE,
    borderRadius: 3,
  },

  // Legend
  legend: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: 4,
    marginTop: 8,
  },
  legendCell: {
    width: CELL_SIZE,
    height: CELL_SIZE,
    borderRadius: 3,
  },
  legendText: {
    color: palette.textMuted,
    fontSize: 10,
    fontWeight: '500',
  },
});
