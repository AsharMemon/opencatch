/**
 * AIS Settings Screen — Configure local WiFi AIS receiver connection.
 *
 * Allows the user to enter host/port for their AIS receiver (Digital Yacht,
 * Vesper, etc.), scan for devices on common ports, and toggle auto-connect.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { SettingsToggle } from '../components/SettingsRow';
import {
  aisReceiver,
  type AISConnectionStatus,
  type MarineInstrumentData,
} from '../services/aisWifiReceiver';

const AIS_AUTO_CONNECT_KEY = '@opencatch_ais_auto_connect';

export function AISSettingsScreen() {
  const [host, setHost] = useState('192.168.1.1');
  const [port, setPort] = useState('10110');
  const [status, setStatus] = useState<AISConnectionStatus>('disconnected');
  const [vesselCount, setVesselCount] = useState(0);
  const [instrumentData, setInstrumentData] = useState<MarineInstrumentData>(() => aisReceiver.getInstrumentData());
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [autoConnect, setAutoConnect] = useState(false);
  const [connecting, setConnecting] = useState(false);

  // Load saved settings
  useEffect(() => {
    (async () => {
      const saved = await aisReceiver.loadSettings();
      if (saved) {
        setHost(saved.host);
        setPort(String(saved.port));
        setAutoConnect(saved.autoConnect);
      }
      const autoStr = await AsyncStorage.getItem(AIS_AUTO_CONNECT_KEY);
      if (autoStr != null) setAutoConnect(autoStr === 'true');
    })();
  }, []);

  // Poll status
  useEffect(() => {
    const interval = setInterval(() => {
      const state = aisReceiver.getAISStatus();
      setStatus(state.status);
      setVesselCount(state.vesselCount);
      setError(state.error);
      setInstrumentData(state.instruments);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleConnect = useCallback(async () => {
    const portNum = parseInt(port, 10);
    if (!host.trim() || isNaN(portNum)) {
      Alert.alert('Invalid Settings', 'Please enter a valid host and port number.');
      return;
    }
    setConnecting(true);
    const ok = await aisReceiver.connectToAIS(host.trim(), portNum);
    setConnecting(false);
    if (!ok) {
      Alert.alert(
        'Connection Failed',
        'Could not connect to AIS receiver. Check that the device is powered on and connected to the same WiFi network.',
      );
    }
  }, [host, port]);

  const handleDisconnect = useCallback(() => {
    aisReceiver.disconnectAIS();
  }, []);

  const handleScan = useCallback(async () => {
    if (!host.trim()) {
      Alert.alert('Enter Host', 'Please enter the IP address of your AIS receiver first.');
      return;
    }
    setScanning(true);
    const found = await aisReceiver.scanForAIS(host.trim());
    setScanning(false);
    if (found) {
      setPort(String(found));
      Alert.alert('Device Found', `AIS receiver detected on port ${found}. Connected successfully.`);
    } else {
      Alert.alert(
        'No Device Found',
        'Could not find an AIS receiver on common ports (10110, 2000, 39150). Check that the device is powered on.',
      );
    }
  }, [host]);

  const handleToggleAutoConnect = useCallback(async (val: boolean) => {
    setAutoConnect(val);
    await AsyncStorage.setItem(AIS_AUTO_CONNECT_KEY, String(val));
  }, []);

  const statusColor = status === 'connected' ? '#4CAF50' : status === 'error' ? '#E53935' : palette.textMuted;
  const liveSensorCount = [
    instrumentData.position,
    instrumentData.heading,
    instrumentData.depth,
    instrumentData.wind,
  ].filter(Boolean).length;
  const statusLabel =
    status === 'connected' ? 'Connected' :
    status === 'connecting' ? 'Connecting...' :
    status === 'error' ? 'Error' :
    'Disconnected';

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {/* Status Banner */}
      <View style={[styles.statusBanner, { borderLeftColor: statusColor }]}>
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
          <Text style={[styles.statusLabel, { color: statusColor }]}>{statusLabel}</Text>
        </View>
        {status === 'connected' && (
          <Text style={styles.statusDetail}>
            {vesselCount} vessel{vesselCount !== 1 ? 's' : ''} tracked · {liveSensorCount} live sensor{liveSensorCount !== 1 ? 's' : ''}
          </Text>
        )}
        {error && <Text style={styles.errorText}>{error}</Text>}
      </View>

      {/* Connection Settings */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Marine Electronics</Text>
        <View style={styles.sectionCard}>
          <View style={styles.inputGroup}>
            <Text style={styles.inputLabel}>Host / IP Address</Text>
            <TextInput
              style={styles.textInput}
              value={host}
              onChangeText={setHost}
              placeholder="192.168.1.1"
              placeholderTextColor={palette.textDim}
              keyboardType="decimal-pad"
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>

          <View style={styles.separator} />

          <View style={styles.inputGroup}>
            <Text style={styles.inputLabel}>Port</Text>
            <TextInput
              style={styles.textInput}
              value={port}
              onChangeText={setPort}
              placeholder="10110"
              placeholderTextColor={palette.textDim}
              keyboardType="number-pad"
            />
          </View>

          <View style={styles.separator} />

          {/* Connect / Disconnect Button */}
          {status === 'connected' ? (
            <Pressable style={styles.disconnectButton} onPress={handleDisconnect}>
              <Ionicons name="close-circle" size={18} color="#E53935" />
              <Text style={styles.disconnectText}>Disconnect</Text>
            </Pressable>
          ) : (
            <Pressable
              style={({ pressed }) => [styles.connectButton, pressed && styles.buttonPressed]}
              onPress={handleConnect}
              disabled={connecting}
            >
              {connecting ? (
                <ActivityIndicator size="small" color="#FFFFFF" />
              ) : (
                <>
                  <Ionicons name="radio" size={18} color="#FFFFFF" />
                  <Text style={styles.connectText}>Connect</Text>
                </>
              )}
            </Pressable>
          )}
        </View>
      </View>

      {/* Scan */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Device Discovery</Text>
        <View style={styles.sectionCard}>
          <Pressable
            style={({ pressed }) => [styles.scanButton, pressed && styles.buttonPressed]}
            onPress={handleScan}
            disabled={scanning}
          >
            {scanning ? (
              <>
                <ActivityIndicator size="small" color={palette.accent} />
                <Text style={styles.scanText}>Scanning ports...</Text>
              </>
            ) : (
              <>
                <Ionicons name="search" size={18} color={palette.accent} />
                <Text style={styles.scanText}>Scan for Marine Devices</Text>
              </>
            )}
          </Pressable>
          <Text style={styles.hintText}>
            Scans common NMEA/AIS ports 10110, 2000, and 39150 on the specified host.
          </Text>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Live Inputs</Text>
        <View style={styles.sectionCard}>
          <View style={styles.deviceRow}>
            <Ionicons name="navigate-outline" size={16} color={palette.textMuted} />
            <View style={styles.deviceInfo}>
              <Text style={styles.deviceName}>External GPS</Text>
              <Text style={styles.devicePort}>
                {instrumentData.position
                  ? `${instrumentData.position.lat.toFixed(4)}, ${instrumentData.position.lon.toFixed(4)} · ${instrumentData.position.sogKnots?.toFixed(1) ?? '0.0'} kt`
                  : 'No external GPS fix yet'}
              </Text>
            </View>
          </View>
          <View style={styles.separator} />
          <View style={styles.deviceRow}>
            <Ionicons name="compass-outline" size={16} color={palette.textMuted} />
            <View style={styles.deviceInfo}>
              <Text style={styles.deviceName}>Heading / Compass</Text>
              <Text style={styles.devicePort}>
                {instrumentData.heading
                  ? `${instrumentData.heading.headingDeg.toFixed(0)}° ${instrumentData.heading.reference}`
                  : 'No external heading yet'}
              </Text>
            </View>
          </View>
          <View style={styles.separator} />
          <View style={styles.deviceRow}>
            <Ionicons name="water-outline" size={16} color={palette.textMuted} />
            <View style={styles.deviceInfo}>
              <Text style={styles.deviceName}>Depth Sounder</Text>
              <Text style={styles.devicePort}>
                {instrumentData.depth
                  ? `${instrumentData.depth.depthM.toFixed(1)} m via ${instrumentData.depth.sourceSentence}`
                  : 'No depth sounder data yet'}
              </Text>
            </View>
          </View>
          <View style={styles.separator} />
          <View style={styles.deviceRow}>
            <Ionicons name="speedometer-outline" size={16} color={palette.textMuted} />
            <View style={styles.deviceInfo}>
              <Text style={styles.deviceName}>Wind Sensor</Text>
              <Text style={styles.devicePort}>
                {instrumentData.wind
                  ? `${instrumentData.wind.speedKnots.toFixed(1)} kt · ${instrumentData.wind.angleDeg.toFixed(0)}° ${instrumentData.wind.reference}`
                  : 'No wind data yet'}
              </Text>
            </View>
          </View>
        </View>
      </View>

      {/* Preferences */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Preferences</Text>
        <View style={styles.sectionCard}>
          <SettingsToggle
            label="Auto-connect on launch"
            value={autoConnect}
            onValueChange={handleToggleAutoConnect}
          />
        </View>
      </View>

      {/* Info */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Supported Devices</Text>
        <View style={styles.sectionCard}>
          {[
            { name: 'Digital Yacht iAISTX', port: '10110' },
            { name: 'Vesper Marine XB Series', port: '39150' },
            { name: 'dAISy AIS Receiver', port: '10110' },
            { name: 'Quark-elec QK-A027', port: '2000' },
            { name: 'Generic NMEA 0183 over WiFi', port: '10110' },
            { name: 'Signal K / NMEA bridge', port: '3000 / bridge port' },
          ].map((device, i) => (
            <View key={device.name}>
              {i > 0 && <View style={styles.separator} />}
              <View style={styles.deviceRow}>
                <Ionicons name="hardware-chip-outline" size={16} color={palette.textMuted} />
                <View style={styles.deviceInfo}>
                  <Text style={styles.deviceName}>{device.name}</Text>
                  <Text style={styles.devicePort}>Default port: {device.port}</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </View>

      <View style={{ height: 40 }} />
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
    gap: 24,
  },

  // ── Status banner ──────────────────────────────────────────────
  statusBanner: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 16,
    borderLeftWidth: 4,
    borderWidth: 1,
    borderColor: palette.border,
    gap: 4,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  statusLabel: {
    fontSize: 15,
    fontWeight: '700',
  },
  statusDetail: {
    fontSize: 13,
    color: palette.textSecondary,
    marginLeft: 18,
  },
  errorText: {
    fontSize: 12,
    color: '#E53935',
    marginLeft: 18,
  },

  // ── Sections ───────────────────────────────────────────────────
  section: {
    gap: 10,
  },
  sectionTitle: {
    color: palette.textMuted,
    fontSize: 13,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    paddingLeft: 4,
  },
  sectionCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderWidth: 1,
    borderColor: palette.border,
  },

  // ── Inputs ─────────────────────────────────────────────────────
  inputGroup: {
    paddingVertical: 8,
    gap: 4,
  },
  inputLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  textInput: {
    fontSize: 16,
    color: palette.text,
    paddingVertical: Platform.OS === 'ios' ? 6 : 4,
  },
  separator: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: palette.border,
  },

  // ── Buttons ────────────────────────────────────────────────────
  connectButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: palette.accent,
    borderRadius: 10,
    paddingVertical: 12,
    marginTop: 12,
  },
  connectText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  disconnectButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
    marginTop: 12,
  },
  disconnectText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#E53935',
  },
  scanButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 10,
  },
  scanText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.accent,
  },
  buttonPressed: {
    opacity: 0.7,
  },
  hintText: {
    fontSize: 11,
    color: palette.textMuted,
    textAlign: 'center',
    marginTop: 4,
  },

  // ── Device list ────────────────────────────────────────────────
  deviceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
  },
  deviceInfo: {
    flex: 1,
    gap: 1,
  },
  deviceName: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.text,
  },
  devicePort: {
    fontSize: 11,
    color: palette.textMuted,
  },
});
