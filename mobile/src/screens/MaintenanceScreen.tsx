/**
 * OpenCatch — Maintenance Log Screen
 *
 * Timeline view of maintenance records, engine hours display,
 * upcoming maintenance reminders, and add record form.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableWithoutFeedback,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts } from '../theme/typography';
import {
  addMaintenanceRecord,
  deleteMaintenanceRecord,
  getCurrentEngineHours,
  getMaintenanceHistory,
  getMaintenanceSchedule,
  logEngineHours,
  formatCost,
  MAINTENANCE_TYPE_LABELS,
  MAINTENANCE_TYPE_ICONS,
  type MaintenanceRecord,
  type MaintenanceScheduleItem,
  type MaintenanceType,
} from '../services/maintenanceLog';

// ── Main Screen ─────────────────────────────────────────────────────────────

export function MaintenanceScreen() {
  const [records, setRecords] = useState<MaintenanceRecord[]>([]);
  const [schedule, setSchedule] = useState<MaintenanceScheduleItem[]>([]);
  const [engineHours, setEngineHours] = useState(0);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showHoursModal, setShowHoursModal] = useState(false);
  const [tab, setTab] = useState<'history' | 'schedule'>('history');

  const refresh = useCallback(async () => {
    const [recs, hours, sched] = await Promise.all([
      getMaintenanceHistory(),
      getCurrentEngineHours(),
      getMaintenanceSchedule(),
    ]);
    setRecords(recs);
    setEngineHours(hours);
    setSchedule(sched);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleDelete = useCallback((id: string) => {
    Alert.alert('Delete Record', 'Are you sure you want to delete this maintenance record?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await deleteMaintenanceRecord(id);
          refresh();
        },
      },
    ]);
  }, [refresh]);

  const overdueCount = schedule.filter((s) => s.isOverdue).length;

  return (
    <View style={styles.screen}>
      {/* Engine hours header */}
      <Pressable style={styles.hoursCard} onPress={() => setShowHoursModal(true)}>
        <View style={styles.hoursLeft}>
          <Ionicons name="speedometer-outline" size={24} color={palette.accent} />
          <View>
            <Text style={styles.hoursValue}>{engineHours.toLocaleString()}</Text>
            <Text style={styles.hoursLabel}>Engine Hours</Text>
          </View>
        </View>
        <View style={styles.hoursRight}>
          {overdueCount > 0 && (
            <View style={styles.overdueBadge}>
              <Ionicons name="alert-circle" size={14} color="#FFFFFF" />
              <Text style={styles.overdueText}>{overdueCount} Overdue</Text>
            </View>
          )}
          <Ionicons name="create-outline" size={18} color={palette.textMuted} />
        </View>
      </Pressable>

      {/* Tab selector */}
      <View style={styles.tabRow}>
        <Pressable
          style={[styles.tabBtn, tab === 'history' && styles.tabBtnActive]}
          onPress={() => setTab('history')}
        >
          <Text style={[styles.tabText, tab === 'history' && styles.tabTextActive]}>History</Text>
        </Pressable>
        <Pressable
          style={[styles.tabBtn, tab === 'schedule' && styles.tabBtnActive]}
          onPress={() => setTab('schedule')}
        >
          <Text style={[styles.tabText, tab === 'schedule' && styles.tabTextActive]}>
            Schedule {overdueCount > 0 ? `(${overdueCount})` : ''}
          </Text>
        </Pressable>
      </View>

      {tab === 'history' ? (
        <FlatList
          data={records}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={
            <View style={styles.emptyState}>
              <Ionicons name="construct-outline" size={40} color={palette.textDim} />
              <Text style={styles.emptyText}>No maintenance records yet</Text>
              <Text style={styles.emptySubtext}>Tap + to log your first service</Text>
            </View>
          }
          renderItem={({ item, index }) => (
            <View style={styles.timelineRow}>
              {/* Timeline line */}
              <View style={styles.timelineLineContainer}>
                {index > 0 && <View style={styles.timelineLineTop} />}
                <View style={[styles.timelineDot, { backgroundColor: palette.accent }]}>
                  <Ionicons
                    name={(MAINTENANCE_TYPE_ICONS[item.type] ?? 'construct-outline') as any}
                    size={12}
                    color="#FFFFFF"
                  />
                </View>
                {index < records.length - 1 && <View style={styles.timelineLineBottom} />}
              </View>

              {/* Record card */}
              <View style={styles.recordCard}>
                <View style={styles.recordHeader}>
                  <Text style={styles.recordType}>
                    {MAINTENANCE_TYPE_LABELS[item.type] ?? item.type}
                  </Text>
                  <Pressable onPress={() => handleDelete(item.id)} hitSlop={8}>
                    <Ionicons name="trash-outline" size={16} color={palette.textDim} />
                  </Pressable>
                </View>
                <Text style={styles.recordDate}>{item.date}</Text>
                {item.notes ? <Text style={styles.recordNotes}>{item.notes}</Text> : null}
                <View style={styles.recordMeta}>
                  {item.cost != null && (
                    <Text style={styles.recordMetaText}>{formatCost(item.cost)}</Text>
                  )}
                  {item.engineHoursAtService != null && (
                    <Text style={styles.recordMetaText}>{item.engineHoursAtService}h</Text>
                  )}
                </View>
              </View>
            </View>
          )}
        />
      ) : (
        <FlatList
          data={schedule}
          keyExtractor={(item) => item.type}
          contentContainerStyle={styles.listContent}
          renderItem={({ item }) => (
            <View style={[styles.scheduleCard, item.isOverdue && styles.scheduleCardOverdue]}>
              <View style={styles.scheduleHeader}>
                <Ionicons
                  name={(MAINTENANCE_TYPE_ICONS[item.type] ?? 'construct-outline') as any}
                  size={18}
                  color={item.isOverdue ? palette.error : palette.accent}
                />
                <Text style={styles.scheduleLabel}>{item.label}</Text>
                {item.isOverdue && (
                  <View style={styles.overdueSmallBadge}>
                    <Text style={styles.overdueSmallText}>OVERDUE</Text>
                  </View>
                )}
              </View>
              <View style={styles.scheduleMeta}>
                <Text style={styles.scheduleMetaText}>
                  Every {item.intervalHours}h
                </Text>
                {item.lastDoneDate && (
                  <Text style={styles.scheduleMetaText}>
                    Last: {item.lastDoneDate} ({item.lastDoneHours}h)
                  </Text>
                )}
                <Text style={[
                  styles.scheduleMetaText,
                  item.isOverdue && { color: palette.error, fontWeight: '600' },
                ]}>
                  {item.isOverdue
                    ? `${Math.abs(item.hoursUntilDue)}h overdue`
                    : `Due in ${item.hoursUntilDue}h`}
                </Text>
              </View>
            </View>
          )}
        />
      )}

      {/* Add record FAB */}
      <Pressable style={styles.fab} onPress={() => setShowAddModal(true)}>
        <Ionicons name="add" size={28} color="#FFFFFF" />
      </Pressable>

      {/* Add record modal */}
      <AddRecordModal
        visible={showAddModal}
        engineHours={engineHours}
        onClose={() => setShowAddModal(false)}
        onSave={async (type, date, notes, cost, hours) => {
          await addMaintenanceRecord(type, date, notes, cost, hours);
          setShowAddModal(false);
          refresh();
        }}
      />

      {/* Update engine hours modal */}
      <UpdateHoursModal
        visible={showHoursModal}
        currentHours={engineHours}
        onClose={() => setShowHoursModal(false)}
        onSave={async (hours) => {
          await logEngineHours(hours);
          setShowHoursModal(false);
          refresh();
        }}
      />
    </View>
  );
}

// ── Add Record Modal ─────────────────────────────────────────────────────────

function AddRecordModal({
  visible,
  engineHours,
  onClose,
  onSave,
}: {
  visible: boolean;
  engineHours: number;
  onClose: () => void;
  onSave: (type: MaintenanceType, date: string, notes: string, cost: number | null, engineHours: number | null) => void;
}) {
  const [selectedType, setSelectedType] = useState<MaintenanceType>('oil_change');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [cost, setCost] = useState('');
  const [hours, setHours] = useState(String(engineHours));

  useEffect(() => {
    if (visible) {
      setDate(new Date().toISOString().slice(0, 10));
      setNotes('');
      setCost('');
      setHours(String(engineHours));
    }
  }, [visible, engineHours]);

  const types = Object.keys(MAINTENANCE_TYPE_LABELS) as MaintenanceType[];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <TouchableWithoutFeedback onPress={onClose}>
        <View style={styles.modalBackdrop}>
          <TouchableWithoutFeedback>
            <View style={styles.modalContent}>
              <View style={styles.modalHeader}>
                <Text style={styles.modalTitle}>Log Maintenance</Text>
                <Pressable onPress={onClose} hitSlop={8}>
                  <Ionicons name="close" size={22} color={palette.textMuted} />
                </Pressable>
              </View>

              <ScrollView style={{ maxHeight: 500 }} showsVerticalScrollIndicator={false}>
                {/* Type picker */}
                <Text style={styles.fieldLabel}>Service Type</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 12 }}>
                  <View style={styles.typeChips}>
                    {types.map((t) => (
                      <Pressable
                        key={t}
                        style={[styles.typeChip, selectedType === t && styles.typeChipActive]}
                        onPress={() => setSelectedType(t)}
                      >
                        <Ionicons
                          name={(MAINTENANCE_TYPE_ICONS[t] ?? 'construct-outline') as any}
                          size={14}
                          color={selectedType === t ? '#FFFFFF' : palette.textSecondary}
                        />
                        <Text style={[styles.typeChipText, selectedType === t && styles.typeChipTextActive]}>
                          {MAINTENANCE_TYPE_LABELS[t]}
                        </Text>
                      </Pressable>
                    ))}
                  </View>
                </ScrollView>

                {/* Date */}
                <Text style={styles.fieldLabel}>Date (YYYY-MM-DD)</Text>
                <TextInput
                  style={styles.input}
                  value={date}
                  onChangeText={setDate}
                  placeholder="2026-03-21"
                  placeholderTextColor={palette.textDim}
                />

                {/* Engine hours */}
                <Text style={styles.fieldLabel}>Engine Hours at Service</Text>
                <TextInput
                  style={styles.input}
                  value={hours}
                  onChangeText={setHours}
                  keyboardType="numeric"
                  placeholder="0"
                  placeholderTextColor={palette.textDim}
                />

                {/* Cost */}
                <Text style={styles.fieldLabel}>Cost ($)</Text>
                <TextInput
                  style={styles.input}
                  value={cost}
                  onChangeText={setCost}
                  keyboardType="decimal-pad"
                  placeholder="0.00"
                  placeholderTextColor={palette.textDim}
                />

                {/* Notes */}
                <Text style={styles.fieldLabel}>Notes</Text>
                <TextInput
                  style={[styles.input, { height: 80, textAlignVertical: 'top' }]}
                  value={notes}
                  onChangeText={setNotes}
                  multiline
                  placeholder="Add details..."
                  placeholderTextColor={palette.textDim}
                />

                <Pressable
                  style={styles.saveBtn}
                  onPress={() => {
                    onSave(
                      selectedType,
                      date,
                      notes,
                      cost ? parseFloat(cost) : null,
                      hours ? parseInt(hours, 10) : null,
                    );
                  }}
                >
                  <Ionicons name="checkmark" size={20} color="#FFFFFF" />
                  <Text style={styles.saveBtnText}>Save Record</Text>
                </Pressable>
              </ScrollView>
            </View>
          </TouchableWithoutFeedback>
        </View>
      </TouchableWithoutFeedback>
    </Modal>
  );
}

// ── Update Hours Modal ───────────────────────────────────────────────────────

function UpdateHoursModal({
  visible,
  currentHours,
  onClose,
  onSave,
}: {
  visible: boolean;
  currentHours: number;
  onClose: () => void;
  onSave: (hours: number) => void;
}) {
  const [value, setValue] = useState(String(currentHours));

  useEffect(() => {
    if (visible) setValue(String(currentHours));
  }, [visible, currentHours]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <TouchableWithoutFeedback onPress={onClose}>
        <View style={styles.modalBackdrop}>
          <TouchableWithoutFeedback>
            <View style={[styles.modalContent, { maxHeight: 250 }]}>
              <Text style={styles.modalTitle}>Update Engine Hours</Text>
              <TextInput
                style={[styles.input, { fontSize: 24, fontWeight: '700', textAlign: 'center' }]}
                value={value}
                onChangeText={setValue}
                keyboardType="numeric"
                autoFocus
              />
              <Pressable
                style={styles.saveBtn}
                onPress={() => {
                  const h = parseInt(value, 10);
                  if (!isNaN(h) && h >= 0) onSave(h);
                }}
              >
                <Text style={styles.saveBtnText}>Update</Text>
              </Pressable>
            </View>
          </TouchableWithoutFeedback>
        </View>
      </TouchableWithoutFeedback>
    </Modal>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: palette.background,
  },
  hoursCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#FFFFFF',
    marginHorizontal: 16,
    marginTop: 12,
    borderRadius: 14,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  hoursLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  hoursValue: {
    fontSize: 24,
    fontWeight: '800',
    color: palette.text,
    fontVariant: ['tabular-nums'],
  },
  hoursLabel: {
    fontSize: 12,
    color: palette.textMuted,
  },
  hoursRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  overdueBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.error,
    borderRadius: 12,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  overdueText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  tabRow: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginTop: 12,
    gap: 8,
  },
  tabBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.surfaceRaised,
    alignItems: 'center',
  },
  tabBtnActive: {
    backgroundColor: palette.accent,
  },
  tabText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  tabTextActive: {
    color: '#FFFFFF',
  },
  listContent: {
    padding: 16,
    paddingBottom: 100,
  },
  emptyState: {
    alignItems: 'center',
    paddingTop: 60,
    gap: 8,
  },
  emptyText: {
    fontSize: 16,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  emptySubtext: {
    fontSize: 13,
    color: palette.textMuted,
  },
  // Timeline
  timelineRow: {
    flexDirection: 'row',
    gap: 12,
  },
  timelineLineContainer: {
    width: 28,
    alignItems: 'center',
  },
  timelineDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  timelineLineTop: {
    width: 2,
    flex: 1,
    backgroundColor: palette.border,
  },
  timelineLineBottom: {
    width: 2,
    flex: 1,
    backgroundColor: palette.border,
  },
  recordCard: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
    gap: 4,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  recordHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  recordType: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  recordDate: {
    fontSize: 12,
    color: palette.textMuted,
  },
  recordNotes: {
    fontSize: 13,
    color: palette.textSecondary,
    marginTop: 2,
  },
  recordMeta: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 4,
  },
  recordMetaText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.accent,
  },
  // Schedule
  scheduleCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
    gap: 8,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  scheduleCardOverdue: {
    borderLeftWidth: 3,
    borderLeftColor: palette.error,
  },
  scheduleHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  scheduleLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
    flex: 1,
  },
  overdueSmallBadge: {
    backgroundColor: palette.error,
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  overdueSmallText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 0.5,
  },
  scheduleMeta: {
    gap: 2,
    paddingLeft: 26,
  },
  scheduleMetaText: {
    fontSize: 12,
    color: palette.textMuted,
  },
  // FAB
  fab: {
    position: 'absolute',
    bottom: 24,
    right: 20,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: palette.accent,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  // Modal
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 12,
    maxHeight: '85%',
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  modalTitle: {
    fontFamily: fonts.serif,
    fontSize: 18,
    color: palette.text,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textSecondary,
    marginBottom: 4,
    marginTop: 8,
  },
  input: {
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: 10,
    padding: 12,
    fontSize: 15,
    color: palette.text,
    backgroundColor: palette.surfaceRaised,
  },
  typeChips: {
    flexDirection: 'row',
    gap: 6,
    paddingVertical: 4,
  },
  typeChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: palette.surfaceRaised,
  },
  typeChipActive: {
    backgroundColor: palette.accent,
  },
  typeChipText: {
    fontSize: 12,
    color: palette.textSecondary,
  },
  typeChipTextActive: {
    color: '#FFFFFF',
    fontWeight: '600',
  },
  saveBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    backgroundColor: palette.accent,
    borderRadius: 12,
    paddingVertical: 14,
    marginTop: 12,
  },
  saveBtnText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FFFFFF',
  },
});
