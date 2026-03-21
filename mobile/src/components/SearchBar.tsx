import React from 'react';
import { View, TextInput, StyleSheet, Pressable } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

interface Props {
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  onClear?: () => void;
}

export function SearchBar({
  value,
  onChangeText,
  placeholder = 'Search lakes, rivers...',
  onClear,
}: Props) {
  return (
    <View style={styles.container}>
      <Ionicons name="search-outline" size={18} color={palette.textMuted} />
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={palette.textDim}
        selectionColor={palette.accent}
        returnKeyType="search"
      />
      {value.length > 0 && (
        <Pressable onPress={onClear} hitSlop={8}>
          <Ionicons name="close-circle" size={18} color={palette.textMuted} />
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.96)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 11,
    gap: 10,
    shadowColor: '#000',
    shadowOpacity: 0.10,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 3 },
    elevation: 4,
  },
  input: {
    flex: 1,
    color: palette.text,
    fontSize: 15,
    padding: 0,
  },
});
