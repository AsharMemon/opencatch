/**
 * OpenCatch — Engine/Maintenance Log Service
 *
 * Track engine hours, maintenance records, and upcoming service schedules.
 * Persists to AsyncStorage.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export type MaintenanceType =
  | 'oil_change'
  | 'impeller'
  | 'spark_plugs'
  | 'lower_unit'
  | 'fuel_filter'
  | 'winterize'
  | 'de_winterize'
  | 'battery'
  | 'prop_service'
  | 'hull_cleaning'
  | 'trailer_service'
  | 'zincs'
  | 'electronics'
  | 'general'
  | 'other';

export interface MaintenanceRecord {
  id: string;
  type: MaintenanceType;
  date: string;         // ISO date string (YYYY-MM-DD)
  engineHoursAtService: number | null;
  notes: string;
  cost: number | null;  // dollars
  boatId?: string;      // links to boat profile
  createdAt: number;
}

export interface EngineHoursEntry {
  hours: number;
  date: string;          // ISO date string
  createdAt: number;
}

export interface MaintenanceScheduleItem {
  type: MaintenanceType;
  label: string;
  intervalHours: number;
  lastDoneHours: number | null;
  lastDoneDate: string | null;
  nextDueHours: number;
  isOverdue: boolean;
  hoursUntilDue: number;
}

// ── Labels ───────────────────────────────────────────────────────────────────

export const MAINTENANCE_TYPE_LABELS: Record<MaintenanceType, string> = {
  oil_change: 'Oil Change',
  impeller: 'Impeller Replacement',
  spark_plugs: 'Spark Plugs',
  lower_unit: 'Lower Unit Service',
  fuel_filter: 'Fuel Filter',
  winterize: 'Winterize',
  de_winterize: 'De-winterize / Spring Prep',
  battery: 'Battery Service',
  prop_service: 'Prop Service',
  hull_cleaning: 'Hull Cleaning',
  trailer_service: 'Trailer Service',
  zincs: 'Anodes / Zincs',
  electronics: 'Electronics',
  general: 'General Service',
  other: 'Other',
};

export const MAINTENANCE_TYPE_ICONS: Record<MaintenanceType, string> = {
  oil_change: 'water-outline',
  impeller: 'cog-outline',
  spark_plugs: 'flash-outline',
  lower_unit: 'build-outline',
  fuel_filter: 'funnel-outline',
  winterize: 'snow-outline',
  de_winterize: 'sunny-outline',
  battery: 'battery-charging-outline',
  prop_service: 'hardware-chip-outline',
  hull_cleaning: 'brush-outline',
  trailer_service: 'car-outline',
  zincs: 'shield-outline',
  electronics: 'radio-outline',
  general: 'construct-outline',
  other: 'ellipse-outline',
};

// ── Maintenance intervals (engine hours) ─────────────────────────────────────

const MAINTENANCE_INTERVALS: { type: MaintenanceType; label: string; intervalHours: number }[] = [
  { type: 'oil_change', label: 'Oil & Filter Change', intervalHours: 100 },
  { type: 'impeller', label: 'Impeller Replacement', intervalHours: 300 },
  { type: 'spark_plugs', label: 'Spark Plugs', intervalHours: 300 },
  { type: 'lower_unit', label: 'Lower Unit Fluid', intervalHours: 100 },
  { type: 'fuel_filter', label: 'Fuel Filter', intervalHours: 200 },
  { type: 'zincs', label: 'Anodes / Zincs', intervalHours: 200 },
  { type: 'prop_service', label: 'Prop Inspection', intervalHours: 200 },
];

// ── Storage keys ─────────────────────────────────────────────────────────────

const RECORDS_KEY = '@opencatch/maintenance_records';
const HOURS_KEY = '@opencatch/engine_hours';

// ── Engine Hours ─────────────────────────────────────────────────────────────

/**
 * Log current engine hours.
 */
export async function logEngineHours(hours: number, date?: string): Promise<EngineHoursEntry> {
  const entries = await getEngineHoursHistory();
  const entry: EngineHoursEntry = {
    hours,
    date: date ?? new Date().toISOString().slice(0, 10),
    createdAt: Date.now(),
  };
  entries.push(entry);
  // Keep sorted by date descending
  entries.sort((a, b) => b.createdAt - a.createdAt);
  await AsyncStorage.setItem(HOURS_KEY, JSON.stringify(entries));
  return entry;
}

/**
 * Get full engine hours history.
 */
export async function getEngineHoursHistory(): Promise<EngineHoursEntry[]> {
  try {
    const json = await AsyncStorage.getItem(HOURS_KEY);
    return json ? JSON.parse(json) : [];
  } catch {
    return [];
  }
}

/**
 * Get current (most recent) engine hours.
 */
export async function getCurrentEngineHours(): Promise<number> {
  const entries = await getEngineHoursHistory();
  return entries.length > 0 ? entries[0].hours : 0;
}

// ── Maintenance Records ──────────────────────────────────────────────────────

/**
 * Add a maintenance record.
 */
export async function addMaintenanceRecord(
  type: MaintenanceType,
  date: string,
  notes: string,
  cost: number | null,
  engineHoursAtService?: number | null,
  boatId?: string,
): Promise<MaintenanceRecord> {
  const records = await getMaintenanceHistory();
  const record: MaintenanceRecord = {
    id: `maint_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    type,
    date,
    engineHoursAtService: engineHoursAtService ?? null,
    notes,
    cost,
    boatId,
    createdAt: Date.now(),
  };
  records.push(record);
  records.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
  await AsyncStorage.setItem(RECORDS_KEY, JSON.stringify(records));
  return record;
}

/**
 * Get all maintenance records sorted by date (most recent first).
 */
export async function getMaintenanceHistory(): Promise<MaintenanceRecord[]> {
  try {
    const json = await AsyncStorage.getItem(RECORDS_KEY);
    const records: MaintenanceRecord[] = json ? JSON.parse(json) : [];
    return records.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
  } catch {
    return [];
  }
}

/**
 * Delete a maintenance record by ID.
 */
export async function deleteMaintenanceRecord(id: string): Promise<void> {
  let records = await getMaintenanceHistory();
  records = records.filter((r) => r.id !== id);
  await AsyncStorage.setItem(RECORDS_KEY, JSON.stringify(records));
}

/**
 * Get maintenance schedule — suggests upcoming maintenance based on engine hours.
 */
export async function getMaintenanceSchedule(currentHours?: number): Promise<MaintenanceScheduleItem[]> {
  const hours = currentHours ?? await getCurrentEngineHours();
  const records = await getMaintenanceHistory();

  return MAINTENANCE_INTERVALS.map((interval) => {
    // Find the most recent record of this type that has engine hours
    const lastRecord = records.find(
      (r) => r.type === interval.type && r.engineHoursAtService != null,
    );

    const lastDoneHours = lastRecord?.engineHoursAtService ?? null;
    const lastDoneDate = lastRecord?.date ?? null;
    const nextDueHours = (lastDoneHours ?? 0) + interval.intervalHours;
    const hoursUntilDue = nextDueHours - hours;

    return {
      type: interval.type,
      label: interval.label,
      intervalHours: interval.intervalHours,
      lastDoneHours,
      lastDoneDate,
      nextDueHours,
      isOverdue: hoursUntilDue <= 0,
      hoursUntilDue,
    };
  });
}

/**
 * Get count of overdue maintenance items.
 */
export async function getOverdueCount(): Promise<number> {
  const schedule = await getMaintenanceSchedule();
  return schedule.filter((s) => s.isOverdue).length;
}

/**
 * Format maintenance type for display.
 */
export function formatMaintenanceType(type: MaintenanceType): string {
  return MAINTENANCE_TYPE_LABELS[type] ?? type;
}

/**
 * Format cost for display.
 */
export function formatCost(cost: number | null): string {
  if (cost == null) return 'N/A';
  return `$${cost.toFixed(2)}`;
}
