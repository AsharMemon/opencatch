import { StatusBar } from 'expo-status-bar';
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { CastlineNavigator } from './src/navigation/CastlineNavigator';
import { palette } from './src/theme/palette';

const navTheme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: palette.background,
    card: palette.surface,
    text: palette.text,
    border: palette.border,
    primary: palette.accent,
  },
};

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer theme={navTheme}>
        <StatusBar style="light" />
        <CastlineNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
