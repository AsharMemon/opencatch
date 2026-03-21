import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { runAgents } from '../agents';

const TYPE_ICONS = {
  lake: 'water',
  stream: 'water-outline',
  river: 'water',
  pond: 'ellipse',
  marina: 'boat',
};

export default function ExploreScreen() {
  const [data, setData] = useState(null);
  const [weather, setWeather] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const results = await runAgents({
        location: {},
        weather: {},
      });
      setData(results.location);
      setWeather(results.weather);
      setLoading(false);
    })();
  }, []);

  const renderSpot = ({ item }) => (
    <TouchableOpacity style={styles.spotCard}>
      <View style={styles.spotHeader}>
        <Ionicons name={TYPE_ICONS[item.type] || 'location'} size={28} color="#0A7AFF" />
        <View style={styles.spotInfo}>
          <Text style={styles.spotName}>{item.name}</Text>
          <Text style={styles.spotType}>{item.type} - {item.distance}</Text>
        </View>
        <View style={styles.ratingBadge}>
          <Text style={styles.ratingText}>{item.rating}</Text>
          <Ionicons name="star" size={12} color="#FF9500" />
        </View>
      </View>
      <View style={styles.speciesRow}>
        {item.species.map((s) => (
          <View key={s} style={styles.speciesChip}>
            <Text style={styles.speciesText}>{s}</Text>
          </View>
        ))}
      </View>
    </TouchableOpacity>
  );

  if (loading) {
    return (
      <View style={[styles.container, styles.center]}>
        <Ionicons name="compass" size={48} color="#0A7AFF" />
        <Text style={styles.loadingText}>Agents scouting locations...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Explore</Text>
        <Text style={styles.subtitle}>AI-recommended fishing spots</Text>
      </View>

      {/* Weather Summary */}
      {weather && (
        <View style={styles.weatherBar}>
          <Ionicons name="partly-sunny" size={20} color="#FF9500" />
          <Text style={styles.weatherText}>
            {weather.weather.condition} - {weather.weather.temperature} - Wind {weather.weather.wind}
          </Text>
        </View>
      )}

      <FlatList
        data={data?.spots || []}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderSpot}
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          <Text style={styles.recommendation}>{data?.recommendation}</Text>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F2F2F7' },
  center: { alignItems: 'center', justifyContent: 'center' },
  header: { paddingTop: 60, paddingBottom: 16, paddingHorizontal: 20, backgroundColor: '#0A7AFF' },
  title: { fontSize: 28, fontWeight: 'bold', color: '#fff' },
  subtitle: { fontSize: 14, color: '#D4E9FF', marginTop: 4 },
  weatherBar: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFF8EE', padding: 12, marginHorizontal: 16, marginTop: 12, borderRadius: 10 },
  weatherText: { fontSize: 14, color: '#666', marginLeft: 8 },
  recommendation: { fontSize: 14, color: '#34C759', fontWeight: '500', marginBottom: 12, paddingHorizontal: 4 },
  list: { padding: 16, paddingBottom: 100 },
  spotCard: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, elevation: 2 },
  spotHeader: { flexDirection: 'row', alignItems: 'center' },
  spotInfo: { flex: 1, marginLeft: 12 },
  spotName: { fontSize: 17, fontWeight: '600', color: '#333' },
  spotType: { fontSize: 13, color: '#999', marginTop: 2 },
  ratingBadge: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFF8EE', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8 },
  ratingText: { fontSize: 14, fontWeight: '600', color: '#FF9500', marginRight: 4 },
  speciesRow: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 12 },
  speciesChip: { backgroundColor: '#E8F4FD', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, marginRight: 8, marginBottom: 4 },
  speciesText: { fontSize: 12, color: '#0A7AFF' },
  loadingText: { marginTop: 16, color: '#999', fontSize: 16 },
});
