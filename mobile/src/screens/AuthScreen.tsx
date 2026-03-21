import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { palette } from '../theme/palette';
import { type as typeStyles } from '../theme/typography';
import { auth, AuthError } from '../services/auth';
import type { AuthState } from '../services/auth';

type Mode = 'login' | 'register';

interface AuthScreenProps {
  onAuth: (state: AuthState) => void;
}

export function AuthScreen({ onAuth }: AuthScreenProps) {
  const insets = useSafeAreaInsets();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isValid =
    email.includes('@') &&
    password.length >= 8 &&
    (mode === 'login' || displayName.trim().length > 0);

  const handleSubmit = async () => {
    if (!isValid || loading) return;
    setError(null);
    setLoading(true);

    try {
      let state: AuthState;
      if (mode === 'register') {
        state = await auth.register(email.trim(), password, displayName.trim());
      } else {
        state = await auth.login(email.trim(), password);
      }
      onAuth(state);
    } catch (err) {
      if (err instanceof AuthError) {
        setError(err.message);
      } else {
        setError('Unable to connect. Check your network and try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const switchMode = () => {
    setMode((m) => (m === 'login' ? 'register' : 'login'));
    setError(null);
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView
        style={styles.flex}
        contentContainerStyle={[
          styles.container,
          { paddingTop: insets.top + 60, paddingBottom: insets.bottom + 24 },
        ]}
        keyboardShouldPersistTaps="handled"
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.logo}>OpenCatch</Text>
          <Text style={styles.tagline}>AI-Powered Fishing Predictions</Text>
        </View>

        {/* Form */}
        <View style={styles.form}>
          <Text style={styles.formTitle}>
            {mode === 'login' ? 'Welcome back' : 'Create your account'}
          </Text>

          {mode === 'register' && (
            <View style={styles.inputContainer}>
              <Text style={styles.inputLabel}>Display Name</Text>
              <TextInput
                style={styles.input}
                value={displayName}
                onChangeText={setDisplayName}
                placeholder="Your name"
                placeholderTextColor={palette.textDim}
                autoCapitalize="words"
                autoCorrect={false}
                selectionColor={palette.accent}
              />
            </View>
          )}

          <View style={styles.inputContainer}>
            <Text style={styles.inputLabel}>Email</Text>
            <TextInput
              style={styles.input}
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              placeholderTextColor={palette.textDim}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="email"
              selectionColor={palette.accent}
            />
          </View>

          <View style={styles.inputContainer}>
            <Text style={styles.inputLabel}>Password</Text>
            <TextInput
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder="At least 8 characters"
              placeholderTextColor={palette.textDim}
              secureTextEntry
              autoCapitalize="none"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              selectionColor={palette.accent}
            />
          </View>

          {error && (
            <View style={styles.errorContainer}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          )}

          <TouchableOpacity
            style={[styles.submitButton, !isValid && styles.submitButtonDisabled]}
            onPress={handleSubmit}
            activeOpacity={0.7}
            disabled={!isValid || loading}
          >
            {loading ? (
              <ActivityIndicator color="#FFFFFF" size="small" />
            ) : (
              <Text style={styles.submitText}>
                {mode === 'login' ? 'Log In' : 'Create Account'}
              </Text>
            )}
          </TouchableOpacity>
        </View>

        {/* Footer */}
        <View style={styles.footer}>
          <TouchableOpacity
            style={styles.skipButton}
            onPress={() => onAuth({ isAuthenticated: false, user: { user_id: 0, email: 'guest', display_name: 'Guest', created_at: null }, accessToken: null })}
            activeOpacity={0.7}
          >
            <Text style={styles.skipText}>Continue as Guest</Text>
          </TouchableOpacity>

          <Text style={styles.termsText}>
            By continuing, you agree to our{' '}
            <Text style={styles.termsLink}>Terms</Text> and{' '}
            <Text style={styles.termsLink}>Privacy Policy</Text>
          </Text>

          <TouchableOpacity onPress={switchMode} activeOpacity={0.7}>
            <Text style={styles.switchText}>
              {mode === 'login'
                ? "Don't have an account? "
                : 'Already have an account? '}
              <Text style={styles.switchLink}>
                {mode === 'login' ? 'Sign Up' : 'Log In'}
              </Text>
            </Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: {
    flex: 1,
    backgroundColor: palette.background,
  },
  container: {
    flexGrow: 1,
    paddingHorizontal: 24,
    justifyContent: 'space-between',
  },
  header: {
    marginBottom: 32,
  },
  logo: {
    ...typeStyles.brand,
    color: palette.text,
    marginBottom: 6,
  },
  tagline: {
    fontSize: 15,
    color: palette.textMuted,
  },
  form: {
    gap: 16,
  },
  formTitle: {
    ...typeStyles.screenTitle,
    color: palette.text,
    marginBottom: 8,
  },
  inputContainer: {
    gap: 6,
  },
  inputLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.textSecondary,
    paddingLeft: 2,
  },
  input: {
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: 8,
    padding: 14,
    fontSize: 16,
    color: palette.text,
  },
  errorContainer: {
    backgroundColor: 'rgba(196, 75, 75, 0.08)',
    borderRadius: 8,
    padding: 12,
  },
  errorText: {
    color: palette.error,
    fontSize: 14,
    lineHeight: 20,
  },
  submitButton: {
    backgroundColor: palette.accent,
    height: 48,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
  },
  submitButtonDisabled: {
    opacity: 0.5,
  },
  submitText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  footer: {
    alignItems: 'center',
    gap: 16,
    marginTop: 32,
  },
  termsText: {
    fontSize: 12,
    color: palette.textMuted,
    textAlign: 'center',
    lineHeight: 18,
  },
  termsLink: {
    color: palette.accent,
    fontWeight: '500',
  },
  switchText: {
    fontSize: 14,
    color: palette.textSecondary,
  },
  switchLink: {
    color: palette.accent,
    fontWeight: '600',
  },
  skipButton: {
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: palette.border,
    marginBottom: 8,
  },
  skipText: {
    color: palette.textSecondary,
    fontSize: 15,
    fontWeight: '500',
    textAlign: 'center',
  },
});
