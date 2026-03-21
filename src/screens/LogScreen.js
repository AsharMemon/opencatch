import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TextInput, TouchableOpacity, FlatList, Alert, KeyboardAvoidingView, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { catchLogAgent } from '../agents';

export default function LogScreen() {
  const [catches, setCatches] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [species, setSpecies] = useState('');
  const [weight, setWeight] = useState('');
  const [length, setLength] = useState('');
  const [location, setLocation] = useState('');
  const [lure, setLure] = useState('');

  const loadCatches = async () => {
    const result = await catchLogAgent.run({ action: 'list' });
    setCatches(result.catches);
  };

  useEffect(() => {
    loadCatches();
  }, []);

  const addCatch = async () => {
    if (!species.trim()) {
      Alert.alert('Required', 'Please enter the fish species');
      return;
    }
    await catchLogAgent.run({
      action: 'add',
      data: {
        species: species.trim(),
        weight: parseFloat(weight) || 0,
        length: parseFloat(length) || 0,
        location: location.trim() || 'Unknown',
        lure: lure.trim() || 'Unknown',
      },
    });
    setSpecies('');
    setWeight('');
    setLength('');
    setLocation('');
    setLure('');
    setShowForm(false);
    loadCatches();
  };

  const renderCatch = ({ item }) => (
    <View style={styles.catchCard}>
      <View style={styles.catchHeader}>
        <Ionicons name="fish" size={22} color="#34C759" />
        <Text style={styles.catchSpecies}>{item.species}</Text>
        <Text style={styles.catchDate}>{item.date}</Text>
      </View>
      <View style={styles.catchDetails}>
        {item.weight > 0 && <Text style={styles.catchDetail}>{item.weight} lbs</Text>}
        {item.length > 0 && <Text style={styles.catchDetail}>{item.length} in</Text>}
        <Text style={styles.catchDetail}>{item.location}</Text>
        <Text style={styles.catchDetail}>{item.lure}</Text>
      </View>
    </View>
  );

  return (
    <KeyboardAvoidingView style={styles.container} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.header}>
        <Text style={styles.title}>Catch Log</Text>
        <Text style={styles.subtitle}>{catches.length} catches recorded</Text>
      </View>

      {showForm ? (
        <View style={styles.form}>
          <Text style={styles.formTitle}>Log New Catch</Text>
          <TextInput style={styles.input} placeholder="Species *" value={species} onChangeText={setSpecies} />
          <View style={styles.row}>
            <TextInput style={[styles.input, styles.halfInput]} placeholder="Weight (lbs)" value={weight} onChangeText={setWeight} keyboardType="numeric" />
            <TextInput style={[styles.input, styles.halfInput]} placeholder="Length (in)" value={length} onChangeText={setLength} keyboardType="numeric" />
          </View>
          <TextInput style={styles.input} placeholder="Location" value={location} onChangeText={setLocation} />
          <TextInput style={styles.input} placeholder="Lure / Bait" value={lure} onChangeText={setLure} />
          <View style={styles.formActions}>
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setShowForm(false)}>
              <Text style={styles.cancelText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.saveBtn} onPress={addCatch}>
              <Text style={styles.saveText}>Save Catch</Text>
            </TouchableOpacity>
          </View>
        </View>
      ) : (
        <>
          <FlatList
            data={catches}
            keyExtractor={(item) => String(item.id)}
            renderItem={renderCatch}
            contentContainerStyle={styles.list}
            ListEmptyComponent={<Text style={styles.emptyText}>No catches yet. Start logging!</Text>}
          />
          <TouchableOpacity style={styles.fab} onPress={() => setShowForm(true)}>
            <Ionicons name="add" size={28} color="#fff" />
          </TouchableOpacity>
        </>
      )}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F2F2F7' },
  header: { paddingTop: 60, paddingBottom: 16, paddingHorizontal: 20, backgroundColor: '#0A7AFF' },
  title: { fontSize: 28, fontWeight: 'bold', color: '#fff' },
  subtitle: { fontSize: 14, color: '#D4E9FF', marginTop: 4 },
  list: { padding: 16, paddingBottom: 100 },
  catchCard: { backgroundColor: '#fff', borderRadius: 12, padding: 14, marginBottom: 10, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 6, elevation: 2 },
  catchHeader: { flexDirection: 'row', alignItems: 'center' },
  catchSpecies: { fontSize: 16, fontWeight: '600', color: '#333', flex: 1, marginLeft: 8 },
  catchDate: { fontSize: 12, color: '#999' },
  catchDetails: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 8, gap: 8 },
  catchDetail: { fontSize: 13, color: '#666', backgroundColor: '#F2F2F7', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  form: { padding: 16 },
  formTitle: { fontSize: 20, fontWeight: '600', marginBottom: 16, color: '#333' },
  input: { backgroundColor: '#fff', borderRadius: 10, paddingHorizontal: 16, paddingVertical: 12, fontSize: 16, marginBottom: 10, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4, elevation: 1 },
  row: { flexDirection: 'row', gap: 10 },
  halfInput: { flex: 1 },
  formActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 12, marginTop: 8 },
  cancelBtn: { paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10 },
  cancelText: { fontSize: 16, color: '#999' },
  saveBtn: { backgroundColor: '#34C759', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10 },
  saveText: { fontSize: 16, color: '#fff', fontWeight: '600' },
  fab: { position: 'absolute', bottom: 30, right: 20, backgroundColor: '#0A7AFF', width: 56, height: 56, borderRadius: 28, alignItems: 'center', justifyContent: 'center', shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 8, elevation: 4 },
  emptyText: { textAlign: 'center', marginTop: 60, color: '#999', fontSize: 16 },
});
