import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { HomeScreen } from '../screens/HomeScreen';
import { SpotDetailScreen } from '../screens/SpotDetailScreen';
import { RootStackParamList } from '../types/navigation';
import { palette } from '../theme/palette';

const Stack = createNativeStackNavigator<RootStackParamList>();

export function CastlineNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: palette.background },
        headerTintColor: palette.text,
        headerShadowVisible: false,
        contentStyle: { backgroundColor: palette.background },
        headerTitleStyle: { fontWeight: '600' },
      }}
    >
      <Stack.Screen name="Home" component={HomeScreen} options={{ title: 'CASTLINE' }} />
      <Stack.Screen name="SpotDetail" component={SpotDetailScreen} options={{ title: 'Spot detail' }} />
    </Stack.Navigator>
  );
}
