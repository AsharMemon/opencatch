import React, { useState } from 'react';
import { View, Text, StyleSheet, TextInput, TouchableOpacity, FlatList, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { fishIdentificationAgent } from '../agents';

export default function IdentifyScreen() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  const search = async () => {
    setLoading(true);
    const result = await fishIdentificationAgent.run({ query: query || undefined });
    setResults(result.results);
    setLoading(false);
  };

  const renderFish = ({ item }) => (
    <View style={styles.fishCard}>
      <View style={styles.fishHeader}>
        <Ionicons name="fish" size={24} color="#0A7AFF" />
        <View style={styles.fishInfo}>
          <Text style={styles.fishName}>{item.name}</Text>
          <Text style={styles.fishFamily}>{item.family}</Text>
        </View>
      </View>
      <View style={styles.detailsRow}>
        <View style={styles.detail}>
          <Text style={styles.detailLabel}>Habitat</Text>
          <Text style={styles.detailValue}>{item.habitat}</Text>
        </View>
        <View style={styles.detail}>
          <Text style={styles.detailLabel}>Avg Weight</Text>
          <Text style={styles.detailValue}>{item.avgWeight}</Text>
        </View>
        <View style={styles.detail}>
          <Text style={styles.detailLabel}>Season</Text>
          <Text style={styles.detailValue}>{item.season}</Text>
        </View>
      </View>
      <View style={styles.tipBox}>
        <Ionicons name="bulb" size={16} color="#FF9500" />
        <Text style={styles.tipText}>{item.tips}</Text>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Fish ID Agent</Text>
        <Text style={styles.subtitle}>Search species or browse the database</Text>
      </View>

      <View style={styles.searchRow}>
        <TextInput
          style={styles.input}
          placeholder="Search fish (e.g., bass, trout, saltwater...)"
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={search}
          returnKeyType="search"
        />
        <TouchableOpacity style={styles.searchBtn} onPress={search}>
          <Ionicons name="search" size={20} color="#fff" />
        </TouchableOpacity>
      </View>

      {loading ? (
        <Text style={styles.loadingText}>Agent analyzing...</Text>
      ) : (
        <FlatList
          data={results}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderFish}
          contentContainerStyle={styles.list}
          ListEmptyComponent={
            <TouchableOpacity style={styles.browseBtn} onPress={search}>
              <Ionicons name="fish" size={48} color="#0A7AFF" />
              <Text style={styles.browseText}>Browse All Species</Text>
            </TouchableOpacity>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F2F2F7' },
  header: { paddingTop: 60, paddingBottom: 16, paddingHorizontal: 20, backgroundColor: '#0A7AFF' },
  title: { fontSize: 28, fontWeight: 'bold', color: '#fff' },
  subtitle: { fontSize: 14, color: '#D4E9FF', marginTop: 4 },
  searchRow: { flexDirection: 'row', paddingHorizontal: 16, paddingVertical: 12 },
  input: { flex: 1, backgroundColor: '#fff', borderRadius: 10, paddingHorizontal: 16, paddingVertical: 12, fontSize: 16, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4, elevation: 1 },
  searchBtn: { backgroundColor: '#0A7AFF', width: 48, borderRadius: 10, alignItems: 'center', justifyContent: 'center', marginLeft: 8 },
  list: { paddingHorizontal: 16, paddingBottom: 100 },
  fishCard: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 8, elevation: 2 },
  fishHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  fishInfo: { marginLeft: 12 },
  fishName: { fontSize: 18, fontWeight: '600', color: '#333' },
  fishFamily: { fontSize: 13, color: '#999' },
  detailsRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 12 },
  detail: { alignItems: 'center', flex: 1 },
  detailLabel: { fontSize: 11, color: '#999', marginBottom: 2 },
  detailValue: { fontSize: 13, fontWeight: '500', color: '#333' },
  tipBox: { flexDirection: 'row', alignItems: 'flex-start', backgroundColor: '#FFF8EE', padding: 10, borderRadius: 8 },
  tipText: { fontSize: 13, color: '#666', marginLeft: 8, flex: 1 },
  loadingText: { textAlign: 'center', marginTop: 40, color: '#999', fontStyle: 'italic' },
  browseBtn: { alignItems: 'center', marginTop: 60 },
  browseText: { fontSize: 16, color: '#0A7AFF', marginTop: 12 },
});
