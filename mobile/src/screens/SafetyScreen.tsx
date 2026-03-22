import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  ScrollView,
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  Linking,
  Alert,
  Share,
  Platform,
  ActivityIndicator,
  Animated,
} from 'react-native';
import Slider from '@react-native-community/slider';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import {
  getBoatProfiles,
  getDefaultBoat,
  BOAT_TYPE_LABELS,
  formatBoatDescription,
  type BoatProfile,
} from '../services/boatProfile';
import {
  startAnchorWatch,
  stopAnchorWatch,
  getAnchorStatus,
  setAnchorRadius,
  addAnchorListener,
  isAnchorWatchActive,
  type AnchorStatus,
} from '../services/anchorAlarm';
import {
  triggerMOB,
  cancelMOB,
  isMOBActive,
  addMOBListener,
  type MOBStatus,
} from '../services/manOverboard';
import {
  shareCurrentPosition,
  startSharing,
  stopSharing,
  isSharingActive,
  getSharingTimeRemaining,
  addSharingListener,
  type SharingSession,
} from '../services/liveLocationSharing';
import { hapticHeavy, hapticError, hapticSuccess } from '../utils/haptics';
import {
  getLightningWarning,
  type LightningWarning,
} from '../services/lightningDetection';
import {
  getClosestPort,
  getClosestCoastGuard,
  nmToMiles,
  type Port,
  type CoastGuardStation,
} from '../services/nauticalNav';
import {
  getNearbyVessels,
  type NearbyVessel,
} from '../services/vesselTracking';
import type { RootStackProps } from '../types/navigation';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmergencyContact {
  name: string;
  phone: string;
}

interface FloatPlan {
  id: string;
  createdAt: string;
  departure: string;
  destination: string;
  departureTime: string;
  returnTime: string;
  peopleAboard: string;
  boatType: string;
  boatColor: string;
  registration: string;
  contacts: EmergencyContact[];
  equipment: Record<string, boolean>;
}

interface ChecklistItem {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EQUIPMENT_ITEMS: { key: string; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { key: 'lifeJackets', label: 'Life Jackets / PFDs', icon: 'shield-checkmark-outline' },
  { key: 'flares', label: 'Visual Distress Signals / Flares', icon: 'flash-outline' },
  { key: 'radio', label: 'VHF Radio', icon: 'radio-outline' },
  { key: 'firstAid', label: 'First Aid Kit', icon: 'medkit-outline' },
  { key: 'fireExtinguisher', label: 'Fire Extinguisher', icon: 'flame-outline' },
  { key: 'anchor', label: 'Anchor & Line', icon: 'git-branch-outline' },
  { key: 'whistle', label: 'Sound Signaling Device', icon: 'megaphone-outline' },
];

const PRE_DEPARTURE_CHECKLIST: ChecklistItem[] = [
  { key: 'weather', label: 'Checked weather forecast', icon: 'partly-sunny-outline' },
  { key: 'pfd', label: 'Life jackets for all aboard', icon: 'shield-checkmark-outline' },
  { key: 'fuel', label: 'Fuel level adequate (1/3 rule)', icon: 'speedometer-outline' },
  { key: 'comms', label: 'Communication device charged', icon: 'call-outline' },
  { key: 'floatPlan', label: 'Float plan shared with contact', icon: 'document-text-outline' },
  { key: 'lights', label: 'Navigation lights working', icon: 'bulb-outline' },
  { key: 'drain', label: 'Drain plug installed', icon: 'water-outline' },
  { key: 'registration', label: 'Registration & license aboard', icon: 'card-outline' },
  { key: 'firstAid', label: 'First aid kit stocked', icon: 'medkit-outline' },
  { key: 'sunProtection', label: 'Sun protection (hat, sunscreen)', icon: 'sunny-outline' },
];

const USCG_PHONE = '18004244600';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFloatPlanText(plan: Omit<FloatPlan, 'id' | 'createdAt'>): string {
  const lines = [
    'FLOAT PLAN - OpenCatch',
    '========================',
    '',
    `Departure Point: ${plan.departure || 'Not specified'}`,
    `Destination: ${plan.destination || 'Not specified'}`,
    `Departure Time: ${plan.departureTime || 'Not specified'}`,
    `Expected Return: ${plan.returnTime || 'Not specified'}`,
    `People Aboard: ${plan.peopleAboard || 'Not specified'}`,
    '',
    'BOAT DESCRIPTION',
    `Type: ${plan.boatType || 'Not specified'}`,
    `Color: ${plan.boatColor || 'Not specified'}`,
    `Registration #: ${plan.registration || 'Not specified'}`,
    '',
    'EQUIPMENT',
  ];

  for (const item of EQUIPMENT_ITEMS) {
    const checked = plan.equipment[item.key] ? '[x]' : '[ ]';
    lines.push(`  ${checked} ${item.label}`);
  }

  if (plan.contacts.length > 0) {
    lines.push('', 'EMERGENCY CONTACTS');
    for (const c of plan.contacts) {
      if (c.name || c.phone) {
        lines.push(`  ${c.name} - ${c.phone}`);
      }
    }
  }

  lines.push(
    '',
    'If I have not returned or contacted you by my expected return time,',
    'please call local authorities or the Coast Guard at 1-800-424-4600.',
    '',
    'Sent via OpenCatch',
  );

  return lines.join('\n');
}

function formatCoordinate(value: number, isLat: boolean): string {
  const dir = isLat ? (value >= 0 ? 'N' : 'S') : (value >= 0 ? 'E' : 'W');
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const minRaw = (abs - deg) * 60;
  const min = Math.floor(minRaw);
  const sec = ((minRaw - min) * 60).toFixed(1);
  return `${deg}\u00B0${min}'${sec}"${dir}`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SectionHeader({ title, icon }: { title: string; icon: keyof typeof Ionicons.glyphMap }) {
  return (
    <View style={styles.sectionHeader}>
      <Ionicons name={icon} size={20} color={palette.accent} style={{ marginRight: 8 }} />
      <Text style={[typeStyles.sectionHeader, { color: palette.text }]}>{title}</Text>
    </View>
  );
}

function EquipmentCheckbox({
  label,
  icon,
  checked,
  onToggle,
}: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <Pressable style={styles.checkRow} onPress={onToggle}>
      <Ionicons
        name={checked ? 'checkbox' : 'square-outline'}
        size={22}
        color={checked ? palette.success : palette.textMuted}
      />
      <Ionicons name={icon} size={18} color={palette.textSecondary} style={{ marginLeft: 10, marginRight: 6 }} />
      <Text style={[styles.checkLabel, checked && styles.checkLabelDone]}>{label}</Text>
    </Pressable>
  );
}

function InputField({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType,
  multiline,
}: {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  placeholder?: string;
  keyboardType?: 'default' | 'phone-pad' | 'numeric';
  multiline?: boolean;
}) {
  return (
    <View style={styles.fieldGroup}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        style={[styles.textInput, multiline && { height: 60, textAlignVertical: 'top' }]}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={palette.textDim}
        keyboardType={keyboardType}
        multiline={multiline}
      />
    </View>
  );
}

// ---------------------------------------------------------------------------
// Saved Plan Row
// ---------------------------------------------------------------------------

function SavedPlanRow({
  plan,
  onLoad,
}: {
  plan: FloatPlan;
  onLoad: () => void;
}) {
  const date = new Date(plan.createdAt);
  const label = plan.destination || plan.departure || 'Untitled Plan';
  return (
    <Pressable style={styles.savedPlanRow} onPress={onLoad}>
      <Ionicons name="document-text-outline" size={20} color={palette.accent} />
      <View style={{ flex: 1, marginLeft: 10 }}>
        <Text style={styles.savedPlanTitle} numberOfLines={1}>{label}</Text>
        <Text style={styles.savedPlanDate}>{date.toLocaleDateString()}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={palette.textMuted} />
    </Pressable>
  );
}

// ---------------------------------------------------------------------------
// Main Screen
// ---------------------------------------------------------------------------

export function SafetyScreen({ navigation }: RootStackProps<'Safety'>) {
  // -- Float Plan state --
  const [departure, setDeparture] = useState('');
  const [destination, setDestination] = useState('');
  const [departureTime, setDepartureTime] = useState('');
  const [returnTime, setReturnTime] = useState('');
  const [peopleAboard, setPeopleAboard] = useState('');
  const [boatType, setBoatType] = useState('');
  const [boatColor, setBoatColor] = useState('');
  const [registration, setRegistration] = useState('');
  const [contacts, setContacts] = useState<EmergencyContact[]>([{ name: '', phone: '' }]);
  const [equipment, setEquipment] = useState<Record<string, boolean>>({});

  // -- SOS state --
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [loadingLocation, setLoadingLocation] = useState(false);

  // -- Pre-departure checklist --
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});
  const checkedCount = PRE_DEPARTURE_CHECKLIST.filter((c) => checklist[c.key]).length;

  // -- Saved plans (in-memory for now) --
  const [savedPlans, setSavedPlans] = useState<FloatPlan[]>([]);

  // -- MOB state --
  const [mobStatus, setMobStatus] = useState<MOBStatus>({
    active: false,
    event: null,
    distanceMeters: 0,
    bearing: 0,
    currentLat: null,
    currentLon: null,
  });
  const mobPulse = useRef(new Animated.Value(1)).current;

  // -- Anchor Watch state --
  const [anchorStatus, setAnchorStatus] = useState<AnchorStatus>(getAnchorStatus());
  const [anchorRadius, setAnchorRadiusLocal] = useState(150); // default 150m
  const [settingAnchor, setSettingAnchor] = useState(false);
  const anchorPulse = useRef(new Animated.Value(1)).current;

  // -- Live Location Sharing state --
  const [sharingSession, setSharingSession] = useState<SharingSession | null>(null);
  const [sharingTimeLeft, setSharingTimeLeft] = useState<string | null>(null);

  // -- Lightning state --
  const [lightningWarning, setLightningWarning] = useState<LightningWarning | null>(null);
  const [lightningLoading, setLightningLoading] = useState(false);

  // -- Nearest port/coast guard --
  const [nearestPort, setNearestPort] = useState<Port | null>(null);
  const [nearestCG, setNearestCG] = useState<CoastGuardStation | null>(null);

  // -- Nearby vessels (AIS) --
  const [nearbyVessels, setNearbyVessels] = useState<NearbyVessel[]>([]);

  // -- MOB pulse animation --
  useEffect(() => {
    if (!mobStatus.active) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(mobPulse, { toValue: 1.15, duration: 600, useNativeDriver: true }),
        Animated.timing(mobPulse, { toValue: 1, duration: 600, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [mobStatus.active, mobPulse]);

  // -- Anchor alarm pulse animation --
  useEffect(() => {
    if (!anchorStatus.alarm) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(anchorPulse, { toValue: 1.1, duration: 400, useNativeDriver: true }),
        Animated.timing(anchorPulse, { toValue: 1, duration: 400, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [anchorStatus.alarm, anchorPulse]);

  // -- Subscribe to service updates --
  useEffect(() => {
    const unsub1 = addMOBListener(setMobStatus);
    const unsub2 = addAnchorListener(setAnchorStatus);
    const unsub3 = addSharingListener(setSharingSession);
    return () => { unsub1(); unsub2(); unsub3(); };
  }, []);

  // -- Lightning detection on mount --
  useEffect(() => {
    (async () => {
      try {
        setLightningLoading(true);
        const Location = await import('expo-location');
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') return;
        const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
        const warning = await getLightningWarning(pos.coords.latitude, pos.coords.longitude);
        setLightningWarning(warning);
        // Also fetch nearest port, coast guard, and nearby vessels
        getClosestPort(pos.coords.latitude, pos.coords.longitude, 1).then((ports) => { if (ports.length > 0) setNearestPort(ports[0]); }).catch(() => {});
        getClosestCoastGuard(pos.coords.latitude, pos.coords.longitude, 1).then((stations) => { if (stations.length > 0) setNearestCG(stations[0]); }).catch(() => {});
        getNearbyVessels(pos.coords.latitude, pos.coords.longitude).then((v) => setNearbyVessels(v.slice(0, 5))).catch(() => {});
      } catch {
        // Silent — non-critical
      } finally {
        setLightningLoading(false);
      }
    })();
  }, []);

  // -- Sharing timer countdown --
  useEffect(() => {
    if (!sharingSession) {
      setSharingTimeLeft(null);
      return;
    }
    const tick = () => setSharingTimeLeft(getSharingTimeRemaining());
    tick();
    const iv = setInterval(tick, 30000);
    return () => clearInterval(iv);
  }, [sharingSession]);

  // -- MOB handler --
  const handleMOB = useCallback(async () => {
    if (mobStatus.active) {
      await cancelMOB();
    } else {
      hapticHeavy();
      await triggerMOB();
    }
  }, [mobStatus.active]);

  // -- Anchor Watch handlers --
  const handleStartAnchor = useCallback(async () => {
    setSettingAnchor(true);
    try {
      const Location = await import('expo-location');
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permission Denied', 'Location is required for anchor watch.');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      await startAnchorWatch(loc.coords.latitude, loc.coords.longitude, anchorRadius);
      hapticSuccess();
    } catch {
      Alert.alert('Error', 'Unable to set anchor position.');
    } finally {
      setSettingAnchor(false);
    }
  }, [anchorRadius]);

  const handleStopAnchor = useCallback(async () => {
    await stopAnchorWatch();
    hapticSuccess();
  }, []);

  const handleRadiusChange = useCallback((value: number) => {
    const rounded = Math.round(value);
    setAnchorRadiusLocal(rounded);
    if (isAnchorWatchActive()) {
      setAnchorRadius(rounded);
    }
  }, []);

  // -- Share Location handler --
  const handleShareLocation = useCallback(async () => {
    await shareCurrentPosition();
  }, []);

  // -- Boat profiles --
  const [boatProfiles, setBoatProfiles] = useState<BoatProfile[]>([]);
  const [selectedBoatId, setSelectedBoatId] = useState<string | null>(null);
  const [loadingBoats, setLoadingBoats] = useState(true);

  // Load boat profiles on mount and auto-fill default boat
  useEffect(() => {
    (async () => {
      try {
        const profiles = await getBoatProfiles();
        setBoatProfiles(profiles);
        if (profiles.length > 0) {
          const defaultBoat = await getDefaultBoat();
          if (defaultBoat) {
            applyBoatProfile(defaultBoat);
            setSelectedBoatId(defaultBoat.id);
          }
        }
      } catch {
        // Silent fail — user can fill manually
      } finally {
        setLoadingBoats(false);
      }
    })();
  }, []);

  const applyBoatProfile = (boat: BoatProfile) => {
    const typeLabel = BOAT_TYPE_LABELS[boat.type] || '';
    const desc = [typeLabel, boat.make, boat.model, boat.length ? `${boat.length}ft` : null]
      .filter(Boolean)
      .join(' ');
    setBoatType(desc || typeLabel);
    setBoatColor(boat.color || '');
    setRegistration(boat.registrationNumber || '');
    if (boat.maxCapacity) {
      setPeopleAboard(String(boat.maxCapacity));
    }
  };

  const handleSelectBoat = (boatId: string) => {
    const boat = boatProfiles.find((b) => b.id === boatId);
    if (boat) {
      applyBoatProfile(boat);
      setSelectedBoatId(boatId);
    }
  };

  // ── Location ───────────────────────────────────────────────────────────
  const fetchLocation = useCallback(async () => {
    setLoadingLocation(true);
    try {
      // Lazy-load expo-location so it is not required at import time
      const Location = await import('expo-location');
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permission Denied', 'Location permission is required to share your position.');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      setCoords({ lat: loc.coords.latitude, lon: loc.coords.longitude });
    } catch {
      Alert.alert('Error', 'Unable to retrieve your location.');
    } finally {
      setLoadingLocation(false);
    }
  }, []);

  // ── Float Plan actions ─────────────────────────────────────────────────
  const shareFloatPlan = useCallback(async () => {
    const text = makeFloatPlanText({
      departure,
      destination,
      departureTime,
      returnTime,
      peopleAboard,
      boatType,
      boatColor,
      registration,
      contacts,
      equipment,
    });
    try {
      await Share.share({ message: text, title: 'Float Plan' });
    } catch {
      // User cancelled
    }
  }, [departure, destination, departureTime, returnTime, peopleAboard, boatType, boatColor, registration, contacts, equipment]);

  const saveFloatPlan = useCallback(() => {
    const plan: FloatPlan = {
      id: Date.now().toString(),
      createdAt: new Date().toISOString(),
      departure,
      destination,
      departureTime,
      returnTime,
      peopleAboard,
      boatType,
      boatColor,
      registration,
      contacts,
      equipment,
    };
    setSavedPlans((prev) => [plan, ...prev]);
    Alert.alert('Saved', 'Float plan saved for quick reuse.');
  }, [departure, destination, departureTime, returnTime, peopleAboard, boatType, boatColor, registration, contacts, equipment]);

  const loadPlan = useCallback((plan: FloatPlan) => {
    setDeparture(plan.departure);
    setDestination(plan.destination);
    setDepartureTime(plan.departureTime);
    setReturnTime(plan.returnTime);
    setPeopleAboard(plan.peopleAboard);
    setBoatType(plan.boatType);
    setBoatColor(plan.boatColor);
    setRegistration(plan.registration);
    setContacts(plan.contacts);
    setEquipment(plan.equipment);
  }, []);

  // ── Emergency actions ──────────────────────────────────────────────────
  const callCoastGuard = () => Linking.openURL(`tel:${USCG_PHONE}`);
  const call911 = () => Linking.openURL('tel:911');

  const shareLocation = useCallback(async () => {
    if (!coords) {
      await fetchLocation();
      return;
    }
    const msg = `EMERGENCY - My current location:\n${formatCoordinate(coords.lat, true)}, ${formatCoordinate(coords.lon, false)}\nDecimal: ${coords.lat.toFixed(6)}, ${coords.lon.toFixed(6)}\nhttps://maps.google.com/?q=${coords.lat},${coords.lon}\n\nSent via OpenCatch`;
    try {
      await Share.share({ message: msg, title: 'My Location' });
    } catch {
      // cancelled
    }
  }, [coords, fetchLocation]);

  // ── Contacts helpers ───────────────────────────────────────────────────
  const updateContact = (index: number, field: keyof EmergencyContact, value: string) => {
    setContacts((prev) => {
      const copy = [...prev];
      copy[index] = { ...copy[index], [field]: value };
      return copy;
    });
  };

  const addContact = () => setContacts((prev) => [...prev, { name: '', phone: '' }]);

  const removeContact = (index: number) => {
    if (contacts.length <= 1) return;
    setContacts((prev) => prev.filter((_, i) => i !== index));
  };

  // ── Toggle helpers ─────────────────────────────────────────────────────
  const toggleEquipment = (key: string) =>
    setEquipment((prev) => ({ ...prev, [key]: !prev[key] }));
  const toggleChecklist = (key: string) =>
    setChecklist((prev) => ({ ...prev, [key]: !prev[key] }));

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {/* ─── Man Overboard Button ──────────────────────────────────────── */}
      <Animated.View style={{ transform: [{ scale: mobStatus.active ? mobPulse : 1 }] }}>
        <Pressable
          style={[
            styles.mobButton,
            mobStatus.active && styles.mobButtonActive,
          ]}
          onPress={handleMOB}
        >
          <View style={styles.mobIconContainer}>
            <Ionicons name="person" size={28} color="#FFFFFF" />
            {mobStatus.active && (
              <Ionicons name="alert-circle" size={14} color="#FFFFFF" style={styles.mobAlertBadge} />
            )}
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.mobTitle}>
              {mobStatus.active ? 'MOB ACTIVE' : 'Man Overboard'}
            </Text>
            {mobStatus.active && mobStatus.event ? (
              <Text style={styles.mobSubtitle}>
                {mobStatus.distanceMeters < 1000
                  ? `${Math.round(mobStatus.distanceMeters)}m away`
                  : `${(mobStatus.distanceMeters / 1000).toFixed(2)}km away`}
                {' \u00B7 '}
                {Math.round(mobStatus.bearing)}\u00B0
              </Text>
            ) : (
              <Text style={styles.mobSubtitle}>One-tap emergency GPS marker</Text>
            )}
          </View>
          {mobStatus.active ? (
            <View style={styles.mobCancelBadge}>
              <Text style={styles.mobCancelText}>CANCEL</Text>
            </View>
          ) : (
            <Ionicons name="hand-left" size={22} color="rgba(255,255,255,0.7)" />
          )}
        </Pressable>
      </Animated.View>

      {/* ─── Lightning Detection ───────────────────────────────────────── */}
      <View style={[
        styles.card,
        lightningWarning && lightningWarning.level === 'imminent' && styles.cardAlarm,
      ]}>
        <SectionHeader title="Lightning Risk" icon="flash-outline" />
        {lightningLoading ? (
          <ActivityIndicator size="small" color={palette.accent} style={{ paddingVertical: 12 }} />
        ) : lightningWarning ? (
          <View style={{ gap: 8, paddingTop: 4 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Ionicons
                name={lightningWarning.level === 'clear' ? 'checkmark-circle' : lightningWarning.level === 'imminent' ? 'warning' : 'alert-circle'}
                size={20}
                color={lightningWarning.level === 'clear' ? '#4CAF50' : lightningWarning.level === 'imminent' ? '#D32F2F' : '#F57C00'}
              />
              <Text style={[styles.cardDescription, { fontWeight: '700', flex: 1, color: lightningWarning.level === 'clear' ? '#4CAF50' : lightningWarning.level === 'imminent' ? '#D32F2F' : '#F57C00' }]}>
                {lightningWarning.headline}
              </Text>
            </View>
            <Text style={styles.cardDescription}>{lightningWarning.message}</Text>
            {lightningWarning.nearestStrikeMiles != null && (
              <Text style={[styles.cardDescription, { fontWeight: '600' }]}>
                Nearest activity: {lightningWarning.nearestStrikeMiles.toFixed(1)} mi away
              </Text>
            )}
          </View>
        ) : (
          <Text style={styles.cardDescription}>Unable to check lightning conditions.</Text>
        )}
      </View>

      {/* ─── Nearby Vessels (AIS) ──────────────────────────────────────── */}
      {nearbyVessels.length > 0 && (
        <View style={styles.card}>
          <SectionHeader title="Nearby Vessels" icon="boat-outline" />
          <Text style={styles.cardDescription}>
            AIS vessel traffic within 10 nautical miles.
          </Text>
          {nearbyVessels.map((v) => (
            <View key={v.mmsi} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.borderLight }}>
              <Ionicons name="navigate-outline" size={16} color={palette.accent} />
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 13, fontWeight: '600', color: palette.text }} numberOfLines={1}>
                  {v.name || `MMSI ${v.mmsi}`}
                </Text>
                <Text style={{ fontSize: 11, color: palette.textMuted }}>
                  {v.shipTypeLabel} {'\u00B7'} {v.distanceNm.toFixed(1)} nm
                  {v.lastPosition?.sogKnots ? ` \u00B7 ${v.lastPosition.sogKnots.toFixed(1)} kts` : ''}
                </Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* ─── Anchor Watch ──────────────────────────────────────────────── */}
      <View style={[styles.card, anchorStatus.alarm && styles.cardAlarm]}>
        <SectionHeader title="Anchor Watch" icon="locate-outline" />
        <Text style={styles.cardDescription}>
          Set your anchor position and get alerted if you drift beyond the safe radius.
        </Text>

        {/* Radius slider */}
        <View style={styles.anchorSliderRow}>
          <Text style={styles.anchorSliderLabel}>Watch Radius</Text>
          <Text style={styles.anchorSliderValue}>{anchorRadius}m</Text>
        </View>
        <Slider
          style={styles.anchorSlider}
          minimumValue={50}
          maximumValue={500}
          step={10}
          value={anchorRadius}
          onValueChange={handleRadiusChange}
          minimumTrackTintColor={palette.accent}
          maximumTrackTintColor={palette.borderLight}
          thumbTintColor={palette.accent}
        />
        <View style={styles.anchorSliderLabels}>
          <Text style={styles.anchorSliderMark}>50m</Text>
          <Text style={styles.anchorSliderMark}>250m</Text>
          <Text style={styles.anchorSliderMark}>500m</Text>
        </View>

        {/* Status display */}
        {anchorStatus.active && (
          <Animated.View
            style={[
              styles.anchorStatusBox,
              anchorStatus.alarm && styles.anchorStatusAlarm,
              { transform: [{ scale: anchorStatus.alarm ? anchorPulse : 1 }] },
            ]}
          >
            <Ionicons
              name={anchorStatus.alarm ? 'warning' : 'checkmark-circle'}
              size={20}
              color={anchorStatus.alarm ? palette.error : palette.success}
            />
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Text style={[styles.anchorStatusLabel, anchorStatus.alarm && { color: palette.error }]}>
                {anchorStatus.alarm ? 'DRIFT ALARM!' : 'Holding Position'}
              </Text>
              <Text style={styles.anchorStatusDrift}>
                Drift: {Math.round(anchorStatus.driftDistance)}m / {anchorStatus.watch?.radiusMeters ?? anchorRadius}m
                {' \u00B7 '}{Math.round(anchorStatus.bearing)}\u00B0
              </Text>
            </View>
          </Animated.View>
        )}

        {/* Start / Stop button */}
        <Pressable
          style={[
            styles.anchorButton,
            anchorStatus.active ? styles.anchorButtonStop : styles.anchorButtonStart,
          ]}
          onPress={anchorStatus.active ? handleStopAnchor : handleStartAnchor}
          disabled={settingAnchor}
        >
          {settingAnchor ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <>
              <Ionicons
                name={anchorStatus.active ? 'stop-circle' : 'navigate'}
                size={20}
                color="#FFFFFF"
              />
              <Text style={styles.anchorButtonText}>
                {anchorStatus.active ? 'Stop Anchor Watch' : 'Drop Anchor & Start Watch'}
              </Text>
            </>
          )}
        </Pressable>
      </View>

      {/* ─── Live Location Sharing ─────────────────────────────────────── */}
      <View style={styles.card}>
        <SectionHeader title="Share Location" icon="location-outline" />
        <Text style={styles.cardDescription}>
          Share your real-time position with someone on shore via text or email.
        </Text>

        {sharingSession ? (
          <View style={styles.sharingActiveBox}>
            <View style={styles.sharingDot} />
            <View style={{ flex: 1 }}>
              <Text style={styles.sharingActiveText}>Sharing Active</Text>
              {sharingTimeLeft && (
                <Text style={styles.sharingTimeText}>{sharingTimeLeft} remaining</Text>
              )}
            </View>
            <Pressable style={styles.sharingUpdateBtn} onPress={handleShareLocation}>
              <Ionicons name="refresh" size={16} color={palette.accent} />
              <Text style={styles.sharingUpdateText}>Update</Text>
            </Pressable>
            <Pressable style={styles.sharingStopBtn} onPress={stopSharing}>
              <Ionicons name="stop-circle" size={16} color={palette.error} />
            </Pressable>
          </View>
        ) : null}

        <View style={styles.shareButtonRow}>
          <Pressable style={styles.secondaryBtn} onPress={handleShareLocation}>
            <Ionicons name="share-outline" size={18} color={palette.accent} />
            <Text style={styles.secondaryBtnText}>Share Now</Text>
          </Pressable>
          {!sharingSession && (
            <Pressable
              style={styles.primaryBtn}
              onPress={() => startSharing(4)}
            >
              <Ionicons name="radio-outline" size={18} color="#FFFFFF" />
              <Text style={styles.primaryBtnText}>Start Live Sharing</Text>
            </Pressable>
          )}
        </View>
      </View>

      {/* ─── Emergency SOS ─────────────────────────────────────────────── */}
      <View style={styles.sosContainer}>
        <Text style={[typeStyles.screenTitle, { color: '#FFFFFF', textAlign: 'center', marginBottom: 4 }]}>
          Emergency SOS
        </Text>
        <Text style={styles.sosSubtitle}>In case of emergency on the water</Text>

        {/* GPS Coordinates */}
        <Pressable style={styles.coordsBox} onPress={fetchLocation}>
          <Ionicons name="navigate-outline" size={18} color={palette.accent} />
          <Text style={styles.coordsText}>
            {loadingLocation
              ? 'Locating...'
              : coords
              ? `${formatCoordinate(coords.lat, true)}  ${formatCoordinate(coords.lon, false)}`
              : 'Tap to get current GPS coordinates'}
          </Text>
        </Pressable>
        {coords && (
          <Text style={styles.coordsDecimal}>
            {coords.lat.toFixed(6)}, {coords.lon.toFixed(6)}
          </Text>
        )}

        {/* SOS Buttons */}
        <View style={styles.sosButtonRow}>
          <Pressable style={styles.sosButton} onPress={call911}>
            <Ionicons name="call" size={22} color="#FFFFFF" />
            <Text style={styles.sosButtonLabel}>Call 911</Text>
          </Pressable>
          <Pressable style={[styles.sosButton, { backgroundColor: palette.accentDeep }]} onPress={callCoastGuard}>
            <Ionicons name="boat" size={22} color="#FFFFFF" />
            <Text style={styles.sosButtonLabel}>Coast Guard</Text>
          </Pressable>
        </View>

        <Pressable style={styles.shareLocationBtn} onPress={shareLocation}>
          <Ionicons name="share-outline" size={18} color="#FFFFFF" />
          <Text style={styles.shareLocationText}>Share My Location</Text>
        </Pressable>

        {/* Nearest port / coast guard from nauticalNav */}
        {(nearestPort || nearestCG) && (
          <View style={{ marginTop: 12, gap: 6 }}>
            {nearestPort && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 }}>
                <Ionicons name="boat-outline" size={16} color={palette.accent} />
                <Text style={{ fontSize: 13, color: palette.text, flex: 1 }} numberOfLines={1}>
                  Nearest port: {nearestPort.name}
                </Text>
                <Text style={{ fontSize: 12, color: palette.textMuted }}>
                  {nmToMiles(nearestPort.distanceNm).toFixed(1)} mi
                </Text>
              </View>
            )}
            {nearestCG && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 }}>
                <Ionicons name="shield-outline" size={16} color="#D32F2F" />
                <Text style={{ fontSize: 13, color: palette.text, flex: 1 }} numberOfLines={1}>
                  Nearest USCG: {nearestCG.name}
                </Text>
                <Text style={{ fontSize: 12, color: palette.textMuted }}>
                  {nmToMiles(nearestCG.distanceNm).toFixed(1)} mi
                </Text>
              </View>
            )}
          </View>
        )}
      </View>

      {/* ─── Pre-Departure Checklist ───────────────────────────────────── */}
      <View style={styles.card}>
        <SectionHeader title="Pre-Departure Checklist" icon="checkmark-done-outline" />
        <View style={styles.progressBar}>
          <View style={[styles.progressFill, { width: `${(checkedCount / PRE_DEPARTURE_CHECKLIST.length) * 100}%` }]} />
        </View>
        <Text style={styles.progressLabel}>
          {checkedCount} of {PRE_DEPARTURE_CHECKLIST.length} items checked
        </Text>
        {PRE_DEPARTURE_CHECKLIST.map((item) => (
          <EquipmentCheckbox
            key={item.key}
            label={item.label}
            icon={item.icon}
            checked={!!checklist[item.key]}
            onToggle={() => toggleChecklist(item.key)}
          />
        ))}
      </View>

      {/* ─── Float Plan ────────────────────────────────────────────────── */}
      <View style={styles.card}>
        <SectionHeader title="Float Plan" icon="document-text-outline" />
        <Text style={styles.cardDescription}>
          Share your trip details with someone staying on shore. If you do not return on time, they can alert authorities.
        </Text>

        <InputField label="Departure Point" value={departure} onChangeText={setDeparture} placeholder="Marina name or address" />
        <InputField label="Destination / Fishing Spots" value={destination} onChangeText={setDestination} placeholder="Lake, river, or coordinates" multiline />
        <InputField label="Departure Time" value={departureTime} onChangeText={setDepartureTime} placeholder="e.g. 6:00 AM, March 20" />
        <InputField label="Expected Return" value={returnTime} onChangeText={setReturnTime} placeholder="e.g. 3:00 PM, March 20" />
        <InputField label="People Aboard" value={peopleAboard} onChangeText={setPeopleAboard} placeholder="2" keyboardType="numeric" />

        {/* Boat profile selector */}
        <Text style={[styles.fieldLabel, { marginTop: 16, marginBottom: 4, fontWeight: '600' }]}>Boat Description</Text>
        {loadingBoats ? (
          <View style={styles.boatLoadingRow}>
            <ActivityIndicator size="small" color={palette.accent} />
            <Text style={styles.boatLoadingText}>Loading saved boats...</Text>
          </View>
        ) : boatProfiles.length > 0 ? (
          <View style={styles.boatPickerContainer}>
            <Text style={[styles.fieldLabel, { marginBottom: 6 }]}>Select Boat</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.boatPickerRow}>
              {boatProfiles.map((boat) => {
                const isSelected = selectedBoatId === boat.id;
                return (
                  <Pressable
                    key={boat.id}
                    style={[styles.boatPickerChip, isSelected && styles.boatPickerChipActive]}
                    onPress={() => handleSelectBoat(boat.id)}
                  >
                    <Ionicons name="boat-outline" size={14} color={isSelected ? palette.accent : palette.textMuted} />
                    <Text style={[styles.boatPickerText, isSelected && styles.boatPickerTextActive]}>
                      {boat.name}
                    </Text>
                    {boat.isDefault && (
                      <View style={styles.boatDefaultBadge}>
                        <Text style={styles.boatDefaultText}>Default</Text>
                      </View>
                    )}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        ) : (
          <Text style={styles.noBoatsText}>No saved boats. Fill in details below or add a boat in Settings.</Text>
        )}
        <InputField label="Type" value={boatType} onChangeText={setBoatType} placeholder="e.g. 18ft Bass Boat" />
        <InputField label="Color" value={boatColor} onChangeText={setBoatColor} placeholder="e.g. White / Blue" />
        <InputField label="Registration #" value={registration} onChangeText={setRegistration} placeholder="e.g. MN 1234 AB" />

        {/* Equipment checklist */}
        <Text style={[styles.fieldLabel, { marginTop: 16, marginBottom: 8, fontWeight: '600' }]}>Equipment Aboard</Text>
        {EQUIPMENT_ITEMS.map((item) => (
          <EquipmentCheckbox
            key={item.key}
            label={item.label}
            icon={item.icon}
            checked={!!equipment[item.key]}
            onToggle={() => toggleEquipment(item.key)}
          />
        ))}

        {/* Emergency contacts */}
        <Text style={[styles.fieldLabel, { marginTop: 16, marginBottom: 8, fontWeight: '600' }]}>Emergency Contacts</Text>
        {contacts.map((contact, idx) => (
          <View key={idx} style={styles.contactRow}>
            <View style={{ flex: 1 }}>
              <TextInput
                style={styles.textInput}
                value={contact.name}
                onChangeText={(v) => updateContact(idx, 'name', v)}
                placeholder="Name"
                placeholderTextColor={palette.textDim}
              />
            </View>
            <View style={{ flex: 1, marginLeft: 8 }}>
              <TextInput
                style={styles.textInput}
                value={contact.phone}
                onChangeText={(v) => updateContact(idx, 'phone', v)}
                placeholder="Phone"
                placeholderTextColor={palette.textDim}
                keyboardType="phone-pad"
              />
            </View>
            {contacts.length > 1 && (
              <Pressable onPress={() => removeContact(idx)} style={{ paddingLeft: 8 }}>
                <Ionicons name="close-circle-outline" size={22} color={palette.error} />
              </Pressable>
            )}
          </View>
        ))}
        <Pressable style={styles.addContactBtn} onPress={addContact}>
          <Ionicons name="add-circle-outline" size={18} color={palette.accent} />
          <Text style={styles.addContactText}>Add Contact</Text>
        </Pressable>

        {/* Actions */}
        <View style={styles.floatPlanActions}>
          <Pressable style={styles.secondaryBtn} onPress={saveFloatPlan}>
            <Ionicons name="save-outline" size={18} color={palette.accent} />
            <Text style={styles.secondaryBtnText}>Save Plan</Text>
          </Pressable>
          <Pressable style={styles.primaryBtn} onPress={shareFloatPlan}>
            <Ionicons name="share-outline" size={18} color="#FFFFFF" />
            <Text style={styles.primaryBtnText}>Share Float Plan</Text>
          </Pressable>
        </View>
      </View>

      {/* ─── Saved Float Plans ─────────────────────────────────────────── */}
      {savedPlans.length > 0 && (
        <View style={styles.card}>
          <SectionHeader title="Saved Float Plans" icon="folder-outline" />
          {savedPlans.map((plan) => (
            <SavedPlanRow key={plan.id} plan={plan} onLoad={() => loadPlan(plan)} />
          ))}
        </View>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: palette.background,
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 40,
  },

  // SOS
  sosContainer: {
    backgroundColor: palette.error,
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
  },
  sosSubtitle: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 13,
    textAlign: 'center',
    marginBottom: 16,
  },
  coordsBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 8,
  },
  coordsText: {
    fontSize: 13,
    color: palette.text,
    flex: 1,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  coordsDecimal: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 4,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  sosButtonRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 14,
  },
  sosButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#B71C1C',
    borderRadius: 10,
    paddingVertical: 14,
    gap: 8,
  },
  sosButtonLabel: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
  shareLocationBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 10,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.4)',
    borderRadius: 10,
    gap: 6,
  },
  shareLocationText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },

  // Cards
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 16,
    marginBottom: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  cardDescription: {
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
    marginBottom: 16,
  },

  // Section header
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 14,
  },

  // Progress bar
  progressBar: {
    height: 6,
    backgroundColor: palette.surfaceRaised,
    borderRadius: 3,
    overflow: 'hidden',
    marginBottom: 6,
  },
  progressFill: {
    height: '100%',
    backgroundColor: palette.success,
    borderRadius: 3,
  },
  progressLabel: {
    fontSize: 12,
    color: palette.textMuted,
    marginBottom: 12,
  },

  // Checklist rows
  checkRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  checkLabel: {
    fontSize: 14,
    color: palette.text,
    flex: 1,
    marginLeft: 4,
  },
  checkLabelDone: {
    color: palette.textMuted,
    textDecorationLine: 'line-through',
  },

  // Inputs
  fieldGroup: {
    marginBottom: 12,
  },
  fieldLabel: {
    fontSize: 12,
    color: palette.textSecondary,
    fontWeight: '500',
    marginBottom: 4,
    letterSpacing: 0.2,
  },
  textInput: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === 'ios' ? 10 : 8,
    fontSize: 14,
    color: palette.text,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderLight,
  },

  // Contacts
  contactRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  addContactBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    gap: 6,
  },
  addContactText: {
    fontSize: 14,
    color: palette.accent,
    fontWeight: '500',
  },

  // Float plan actions
  floatPlanActions: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 20,
  },
  primaryBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.accent,
    borderRadius: 10,
    paddingVertical: 14,
    gap: 6,
  },
  primaryBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '600',
  },
  secondaryBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: palette.accent,
    borderRadius: 10,
    paddingVertical: 14,
    gap: 6,
  },
  secondaryBtnText: {
    color: palette.accent,
    fontSize: 15,
    fontWeight: '600',
  },

  // Boat picker
  boatLoadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  boatLoadingText: {
    fontSize: 13,
    color: palette.textMuted,
  },
  boatPickerContainer: {
    marginBottom: 12,
  },
  boatPickerRow: {
    gap: 8,
  },
  boatPickerChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: palette.borderLight,
    backgroundColor: palette.surfaceRaised,
  },
  boatPickerChipActive: {
    borderColor: palette.accent,
    backgroundColor: palette.accentLight,
  },
  boatPickerText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  boatPickerTextActive: {
    color: palette.accent,
  },
  boatDefaultBadge: {
    backgroundColor: palette.accent,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  boatDefaultText: {
    fontSize: 9,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  noBoatsText: {
    fontSize: 12,
    color: palette.textMuted,
    fontStyle: 'italic',
    marginBottom: 8,
  },

  // MOB
  mobButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#B71C1C',
    borderRadius: 16,
    padding: 18,
    marginBottom: 16,
    gap: 14,
  },
  mobButtonActive: {
    backgroundColor: '#D32F2F',
    borderWidth: 2,
    borderColor: '#FF8A80',
  },
  mobIconContainer: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(255,255,255,0.2)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  mobAlertBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
  },
  mobTitle: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  mobSubtitle: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 13,
    marginTop: 2,
  },
  mobCancelBadge: {
    backgroundColor: 'rgba(255,255,255,0.25)',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  mobCancelText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },

  // Anchor Watch
  cardAlarm: {
    borderColor: palette.error,
    borderWidth: 2,
  },
  anchorSliderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  anchorSliderLabel: {
    fontSize: 13,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  anchorSliderValue: {
    fontSize: 15,
    color: palette.accent,
    fontWeight: '700',
    fontVariant: ['tabular-nums' as const],
  },
  anchorSlider: {
    width: '100%',
    height: 36,
  } as any,
  anchorSliderLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 14,
  },
  anchorSliderMark: {
    fontSize: 10,
    color: palette.textDim,
  },
  anchorStatusBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(61, 139, 55, 0.08)',
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
  },
  anchorStatusAlarm: {
    backgroundColor: 'rgba(196, 75, 75, 0.1)',
  },
  anchorStatusLabel: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.success,
  },
  anchorStatusDrift: {
    fontSize: 12,
    color: palette.textSecondary,
    marginTop: 2,
    fontVariant: ['tabular-nums' as const],
  },
  anchorButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    paddingVertical: 14,
    gap: 8,
  },
  anchorButtonStart: {
    backgroundColor: palette.accent,
  },
  anchorButtonStop: {
    backgroundColor: palette.error,
  },
  anchorButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '600',
  },

  // Live Location Sharing
  sharingActiveBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(61, 139, 55, 0.08)',
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
    gap: 10,
  },
  sharingDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: palette.success,
  },
  sharingActiveText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.success,
  },
  sharingTimeText: {
    fontSize: 12,
    color: palette.textSecondary,
    marginTop: 1,
  },
  sharingUpdateBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: palette.accent,
  },
  sharingUpdateText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.accent,
  },
  sharingStopBtn: {
    padding: 6,
  },
  shareButtonRow: {
    flexDirection: 'row',
    gap: 10,
  },

  // Saved plans
  savedPlanRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.borderLight,
  },
  savedPlanTitle: {
    fontSize: 14,
    fontWeight: '500',
    color: palette.text,
  },
  savedPlanDate: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 2,
  },
});
