import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { runAgents } from '../agents';

export default function HomeScreen({ navigation }) {
  const [data, setData] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadDashboard = async () => {
    setRefreshing(true);
    try {
      const results = await runAgents({
        weather: {},
        catchLog: { action: 'stats' },
        location: {},
      });
      setData(results);
    } catch (e) {
      console.error('Agent error:', e);
    }
    setRefreshing(false);
  };

  useEffect(() => {
    loadDashboard();
  }, []);

  const weather = data?.weather?.fishing;
  const stats = data?.catchLog?.stats;
  const topSpot = data?.location?.spots?.[0];

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadDashboard} colors={['#0A7AFF']} />}
    >
      <View style={styles.header}>
        <Text style={styles.title}>OpenCatch</Text>
        <Text style={styles.subtitle}>AI-Powered Fishing</Text>
      </View>

      {/* Weather Card */}
      <TouchableOpacity style={styles.card} onPress={() => navigation.navigate('Explore')}>
        <View style={styles.cardHeader}>
          <Ionicons name="partly-sunny" size={24} color="#FF9500" />
          <Text style={styles.cardTitle}>Fishing Conditions</Text>
        </View>
        {weather ? (
          <>
            <View style={styles.ratingRow}>
              {[1, 2, 3, 4, 5].map((i) => (
                <Ionicons key={i} name={i <= weather.rating ? 'fish' : 'fish-outline'} size={20} color={i <= weather.rating ? '#0A7AFF' : '#ccc'} />
              ))}
              <Text style={styles.activityText}> Activity: {weather.activity}</Text>
            </View>
            <Text style={styles.tipText}>{weather.tip}</Text>
            <Text style={styles.timeText}>Best: {weather.bestTimes?.join(', ')}</Text>
          </>
        ) : (
          <Text style={styles.loadingText}>Loading...</Text>
        )}
      </TouchableOpacity>

      {/* Stats Card */}
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Ionicons name="stats-chart" size={24} color="#34C759" />
          <Text style={styles.cardTitle}>Your Stats</Text>
        </View>
        {stats ? (
          <View style={styles.statsGrid}>
            <View style={styles.statItem}>
              <Text style={styles.statNumber}>{stats.totalCatches}</Text>
              <Text style={styles.statLabel}>Catches</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statNumber}>{stats.totalWeight}</Text>
              <Text style={styles.statLabel}>Total lbs</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statNumber}>{stats.topSpecies}</Text>
              <Text style={styles.statLabel}>Top Species</Text>
            </View>
          </View>
        ) : (
          <Text style={styles.loadingText}>Loading...</Text>
        )}
      </View>

      {/* Top Spot Card */}
      <TouchableOpacity style={styles.card} onPress={() => navigation.navigate('Explore')}>
        <View style={styles.cardHeader}>
          <Ionicons name="location" size={24} color="#FF3B30" />
          <Text style={styles.cardTitle}>Top Spot Nearby</Text>
        </View>
        {topSpot ? (
          <>
            <Text style={styles.spotName}>{topSpot.name}</Text>
            <Text style={styles.spotDetails}>
              {topSpot.type} - {topSpot.distance} away - {topSpot.rating}/5
            </Text>
            <Text style={styles.spotSpecies}>{topSpot.species.join(', ')}</Text>
          </>
        ) : (
          <Text style={styles.loadingText}>Loading...</Text>
        )}
      </TouchableOpacity>

      {/* Quick Actions */}
      <View style={styles.actionsRow}>
        <TouchableOpacity style={styles.actionBtn} onPress={() => navigation.navigate('Log')}>
          <Ionicons name="add-circle" size={32} color="#0A7AFF" />
          <Text style={styles.actionText}>Log Catch</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.actionBtn} onPress={() => navigation.navigate('Identify')}>
          <Ionicons name="camera" size={32} color="#0A7AFF" />
          <Text style={styles.actionText}>Identify</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.actionBtn} onPress={() => navigation.navigate('Explore')}>
          <Ionicons name="map" size={32} color="#0A7AFF" />
          <Text style={styles.actionText}>Explore</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>Powered by Vast Agents</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F2F2F7' },
  header: { paddingTop: 60, paddingBottom: 20, paddingHorizontal: 20, backgroundColor: '#0A7AFF' },
  title: { fontSize: 32, fontWeight: 'bold', color: '#fff' },
  subtitle: { fontSize: 16, color: '#D4E9FF', marginTop: 4 },
  card: { backgroundColor: '#fff', marginHorizontal: 16, marginTop: 16, borderRadius: 12, padding: 16, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, elevation: 2 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  cardTitle: { fontSize: 18, fontWeight: '600', marginLeft: 8 },
  ratingRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  activityText: { fontSize: 14, color: '#666', marginLeft: 4 },
  tipText: { fontSize: 14, color: '#333', marginBottom: 4 },
  timeText: { fontSize: 13, color: '#0A7AFF' },
  loadingText: { color: '#999', fontStyle: 'italic' },
  statsGrid: { flexDirection: 'row', justifyContent: 'space-around' },
  statItem: { alignItems: 'center' },
  statNumber: { fontSize: 24, fontWeight: 'bold', color: '#333' },
  statLabel: { fontSize: 12, color: '#999', marginTop: 4 },
  spotName: { fontSize: 16, fontWeight: '600', color: '#333' },
  spotDetails: { fontSize: 13, color: '#666', marginTop: 4 },
  spotSpecies: { fontSize: 13, color: '#0A7AFF', marginTop: 4 },
  actionsRow: { flexDirection: 'row', justifyContent: 'space-around', marginTop: 20, marginHorizontal: 16 },
  actionBtn: { alignItems: 'center', padding: 12 },
  actionText: { fontSize: 12, color: '#0A7AFF', marginTop: 4 },
  footer: { alignItems: 'center', paddingVertical: 24 },
  footerText: { fontSize: 12, color: '#999' },
});
