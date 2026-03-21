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
  Image,
  Dimensions,
  StatusBar,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { fonts } from '../theme/typography';
import { auth, AuthError } from '../services/auth';
import type { AuthState } from '../services/auth';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const IMAGE_HEIGHT = SCREEN_H * 0.55;

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
  const [secureEntry, setSecureEntry] = useState(true);

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
    <View style={styles.root}>
      <StatusBar barStyle="light-content" translucent backgroundColor="transparent" />

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          style={styles.flex}
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          bounces={false}
        >
          {/* ── Hero illustration ── */}
          <View style={styles.heroContainer}>
            <Image
              source={require('../../assets/auth-bg.png')}
              style={styles.heroImage}
              resizeMode="cover"
            />
            {/* Gradient overlay: transparent at top, fading to dark at bottom */}
            <LinearGradient
              colors={['transparent', 'rgba(10, 22, 40, 0.3)', 'rgba(10, 22, 40, 0.85)', '#0a1628']}
              locations={[0, 0.4, 0.75, 1]}
              style={styles.heroGradient}
            />
          </View>

          {/* ── Form area ── */}
          <View style={[styles.formArea, { paddingBottom: insets.bottom + 24 }]}>
            {/* Logo */}
            <View style={styles.logoContainer}>
              <Image
                source={require('../../assets/vector-logo.png')}
                style={styles.logoImage}
                resizeMode="contain"
              />
            </View>

            {/* Form title */}
            <Text style={styles.formTitle}>
              {mode === 'login' ? 'Welcome back' : 'Create your account'}
            </Text>

            {/* Display name (register only) */}
            {mode === 'register' && (
              <View style={styles.inputWrapper}>
                <View style={styles.inputContainer}>
                  <Ionicons
                    name="person-outline"
                    size={18}
                    color="rgba(255,255,255,0.5)"
                    style={styles.inputIcon}
                  />
                  <TextInput
                    style={styles.input}
                    value={displayName}
                    onChangeText={setDisplayName}
                    placeholder="Display name"
                    placeholderTextColor="rgba(255,255,255,0.5)"
                    autoCapitalize="words"
                    autoCorrect={false}
                    selectionColor="#0A6EBD"
                  />
                </View>
              </View>
            )}

            {/* Email */}
            <View style={styles.inputWrapper}>
              <View style={styles.inputContainer}>
                <Ionicons
                  name="mail-outline"
                  size={18}
                  color="rgba(255,255,255,0.5)"
                  style={styles.inputIcon}
                />
                <TextInput
                  style={styles.input}
                  value={email}
                  onChangeText={setEmail}
                  placeholder="Email address"
                  placeholderTextColor="rgba(255,255,255,0.5)"
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                  autoComplete="email"
                  selectionColor="#0A6EBD"
                />
              </View>
            </View>

            {/* Password */}
            <View style={styles.inputWrapper}>
              <View style={styles.inputContainer}>
                <Ionicons
                  name="lock-closed-outline"
                  size={18}
                  color="rgba(255,255,255,0.5)"
                  style={styles.inputIcon}
                />
                <TextInput
                  style={[styles.input, styles.passwordInput]}
                  value={password}
                  onChangeText={setPassword}
                  placeholder="Password"
                  placeholderTextColor="rgba(255,255,255,0.5)"
                  secureTextEntry={secureEntry}
                  autoCapitalize="none"
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  selectionColor="#0A6EBD"
                />
                <TouchableOpacity
                  onPress={() => setSecureEntry(!secureEntry)}
                  activeOpacity={0.7}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <Ionicons
                    name={secureEntry ? 'eye-off-outline' : 'eye-outline'}
                    size={18}
                    color="rgba(255,255,255,0.5)"
                  />
                </TouchableOpacity>
              </View>
            </View>

            {/* Forgot password */}
            {mode === 'login' && (
              <TouchableOpacity style={styles.forgotButton} activeOpacity={0.7}>
                <Text style={styles.forgotText}>Forgot password?</Text>
              </TouchableOpacity>
            )}

            {/* Error */}
            {error && (
              <View style={styles.errorContainer}>
                <Ionicons name="alert-circle-outline" size={16} color="#FF6B6B" />
                <Text style={styles.errorText}>{error}</Text>
              </View>
            )}

            {/* Submit button */}
            <TouchableOpacity
              style={[styles.submitButton, !isValid && styles.submitButtonDisabled]}
              onPress={handleSubmit}
              activeOpacity={0.8}
              disabled={!isValid || loading}
            >
              {loading ? (
                <ActivityIndicator color="#FFFFFF" size="small" />
              ) : (
                <Text style={styles.submitText}>
                  {mode === 'login' ? 'Sign In' : 'Create Account'}
                </Text>
              )}
            </TouchableOpacity>

            {/* Continue as guest */}
            <TouchableOpacity
              style={styles.guestButton}
              onPress={() =>
                onAuth({
                  isAuthenticated: false,
                  user: { user_id: 0, email: 'guest', display_name: 'Guest', created_at: null },
                  accessToken: null,
                })
              }
              activeOpacity={0.7}
            >
              <Text style={styles.guestText}>Continue as Guest</Text>
            </TouchableOpacity>

            {/* Switch mode */}
            <TouchableOpacity onPress={switchMode} activeOpacity={0.7} style={styles.switchContainer}>
              <Text style={styles.switchText}>
                {mode === 'login'
                  ? "Don't have an account? "
                  : 'Already have an account? '}
                <Text style={styles.switchLink}>
                  {mode === 'login' ? 'Sign Up' : 'Log In'}
                </Text>
              </Text>
            </TouchableOpacity>

            {/* Terms */}
            <Text style={styles.termsText}>
              By continuing, you agree to our{' '}
              <Text style={styles.termsLink}>Terms</Text> and{' '}
              <Text style={styles.termsLink}>Privacy Policy</Text>
            </Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#0a1628',
  },
  flex: {
    flex: 1,
  },
  scrollContent: {
    flexGrow: 1,
  },

  // ── Hero ──
  heroContainer: {
    height: IMAGE_HEIGHT,
    width: '100%',
    position: 'relative',
  },
  heroImage: {
    width: '100%',
    height: '100%',
  },
  heroGradient: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    height: IMAGE_HEIGHT * 0.6,
  },

  // ── Form area ──
  formArea: {
    paddingHorizontal: 28,
    marginTop: -40, // overlap into the gradient
    marginBottom: 32, // push content up from bottom edge
  },

  // ── Logo ──
  logoContainer: {
    alignItems: 'center',
    marginBottom: 20,
  },
  logoImage: {
    width: SCREEN_W * 0.45,
    height: (SCREEN_W * 0.45) * (800 / 2400), // match logo aspect ratio
    marginBottom: 8,
    tintColor: '#FFFFFF',
  },
  tagline: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.6)',
    letterSpacing: 1.5,
  },

  // ── Form ──
  formTitle: {
    fontFamily: fonts.serifBold,
    fontSize: 22,
    fontWeight: '400',
    color: '#FFFFFF',
    marginBottom: 16,
    textAlign: 'center',
  },
  inputWrapper: {
    marginBottom: 12,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
    borderRadius: 12,
    paddingHorizontal: 14,
    backgroundColor: 'rgba(255,255,255,0.06)',
  },
  inputIcon: {
    marginRight: 10,
  },
  input: {
    flex: 1,
    height: 50,
    fontSize: 16,
    color: '#FFFFFF',
  },
  passwordInput: {
    paddingRight: 8,
  },

  // ── Forgot ──
  forgotButton: {
    alignSelf: 'flex-end',
    marginBottom: 8,
    marginTop: -4,
  },
  forgotText: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.6)',
  },

  // ── Error ──
  errorContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 107, 107, 0.12)',
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
    gap: 8,
  },
  errorText: {
    color: '#FF6B6B',
    fontSize: 14,
    lineHeight: 20,
    flex: 1,
  },

  // ── Submit ──
  submitButton: {
    backgroundColor: '#0A6EBD',
    height: 52,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 12,
    // subtle shadow
    shadowColor: '#0A6EBD',
    shadowOpacity: 0.35,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  submitButtonDisabled: {
    opacity: 0.45,
  },
  submitText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: '600',
    letterSpacing: 0.3,
  },

  // ── Guest ──
  guestButton: {
    alignItems: 'center',
    justifyContent: 'center',
    height: 48,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
    marginTop: 12,
  },
  guestText: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 15,
    fontWeight: '500',
  },

  // ── Switch mode ──
  switchContainer: {
    alignItems: 'center',
    marginTop: 20,
  },
  switchText: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.6)',
  },
  switchLink: {
    color: '#4DA3E0',
    fontWeight: '600',
  },

  // ── Terms ──
  termsText: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.4)',
    textAlign: 'center',
    lineHeight: 18,
    marginTop: 16,
  },
  termsLink: {
    color: 'rgba(255,255,255,0.6)',
    fontWeight: '500',
  },
});
