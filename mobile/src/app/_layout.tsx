import { useEffect } from 'react';
import { Stack, router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ActivityIndicator, Text, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { restoreSession, useAuth } from '../../lib/auth';
import { messages } from '../../lib/messages';
import { styles } from '@/components/student-ui';

export default function RootLayout() {
  const { user, loading, signOutId } = useAuth();
  const isStudent = user?.role === 'STUDENT';

  useEffect(() => { void restoreSession(); }, []);

  useEffect(() => {
    // Runs after the navigator mounts, including a 401 during startup. This also
    // leaves Register on a failed auto-login, with the backend message on Login.
    if (!loading && signOutId > 0 && !isStudent) router.replace('/login');
  }, [loading, signOutId, isStudent]);

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#173f7a" />
          <Text style={styles.body}>{messages.auth.restoring}</Text>
        </View>
      ) : (
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: '#f5f7fb' } }}>
          <Stack.Protected guard={isStudent}>
            <Stack.Screen name="index" />
            <Stack.Screen name="attendance" />
          </Stack.Protected>
          <Stack.Protected guard={!isStudent}>
            <Stack.Screen name="login" />
            <Stack.Screen name="register" />
          </Stack.Protected>
        </Stack>
      )}
    </SafeAreaProvider>
  );
}
