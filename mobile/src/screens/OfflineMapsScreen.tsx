/**
 * OpenCatch — Offline Maps (Coming Soon)
 *
 * Premium feature placeholder. Shows a "Coming Soon" screen
 * with a Notify Me button that persists a flag to AsyncStorage.
 */

import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { fonts } from '../theme/typography';

const NOTIFY_KEY = 'opencatch_offline_maps_notify';

export function OfflineMapsScreen() {
  const [notifyEnabled, setNotifyEnabled] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(NOTIFY_KEY).then((val) => {
      if (val === 'true') setNotifyEnabled(true);
    });
  }, []);

  const handleNotify = async () => {
    const next = !notifyEnabled;
    setNotifyEnabled(next);
    await AsyncStorage.setItem(NOTIFY_KEY, next ? 'true' : 'false');
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <View style={styles.iconCircle}>
          <Ionicons name="lock-closed-outline" size={48} color={palette.textMuted} />
        </View>
        <Text style={styles.title}>Offline Maps</Text>
        <Text style={styles.subtitle}>Coming soon as a premium feature</Text>
        <Text style={styles.description}>
          Download map regions for use without cell service. Save your favorite
          fishing spots for offline access.
        </Text>
        <Pressable
          style={[
            styles.notifyButton,
            notifyEnabled && styles.notifyButtonActive,
          ]}
          onPress={handleNotify}
        >
          <Ionicons
            name={notifyEnabled ? 'notifications' : 'notifications-outline'}
            size={18}
            color={notifyEnabled ? palette.accent : palette.textSecondary}
          />
          <Text
            style={[
              styles.notifyText,
              notifyEnabled && styles.notifyTextActive,
            ]}
          >
            {notifyEnabled ? 'Subscribed' : 'Notify Me'}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
    justifyContent: 'center',
    alignItems: 'center',
  },
  content: {
    alignItems: 'center',
    paddingHorizontal: 40,
    gap: 12,
  },
  iconCircle: {
    width: 96,
    height: 96,
    borderRadius: 48,
    backgroundColor: palette.border + '40',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
  },
  title: {
    fontFamily: fonts.serifBold,
    fontSize: 24,
    color: palette.text,
  },
  subtitle: {
    fontSize: 15,
    color: palette.textSecondary,
    fontWeight: '500',
  },
  description: {
    fontSize: 13,
    color: palette.textMuted,
    textAlign: 'center',
    lineHeight: 20,
    marginTop: 4,
  },
  notifyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 24,
    borderWidth: 1.5,
    borderColor: palette.border,
    marginTop: 16,
  },
  notifyButtonActive: {
    borderColor: palette.accent,
    backgroundColor: palette.accent + '10',
  },
  notifyText: {
    fontSize: 15,
    fontWeight: '600',
    color: palette.textSecondary,
  },
  notifyTextActive: {
    color: palette.accent,
  },
});
