import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { useAuth } from '../../lib/auth';
import { enrollDevice, isEnrolled } from '../../lib/device';
import { describeApiError, messages } from '../../lib/messages';
import { ActionButton, Heading, InlineError, Screen, styles } from '@/components/student-ui';

const BLOCKED_CODES = new Set(['secret_already_enrolled', 'device_already_bound']);

// Failures from lib/api.js carry the backend's structured code (check, error).
function errorCode(failure: unknown) {
  return typeof failure === 'object' && failure !== null && 'error' in failure
    ? String(failure.error ?? '')
    : '';
}

export default function Home() {
  const { user, profile, logout } = useAuth();
  const [error, setError] = useState('');
  const [setup, setSetup] = useState({ state: 'checking', error: '' });

  async function runSetup() {
    setSetup({ state: 'busy', error: '' });
    try {
      await enrollDevice();
      setSetup({ state: 'ready', error: '' });
    } catch (failure) {
      setSetup({
        state: 'failed',
        error: BLOCKED_CODES.has(errorCode(failure)) ? messages.setup.blocked : describeApiError(failure),
      });
    }
  }

  useEffect(() => {
    let active = true;
    (async () => {
      const enrolled = await isEnrolled();
      if (!active) return;
      if (enrolled) {
        setSetup({ state: 'ready', error: '' });
      } else {
        await runSetup();
      }
    })();
    return () => { active = false; };
  }, []);

  async function signOut() {
    try {
      await logout();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : messages.home.logoutFailed);
    }
  }

  const ready = setup.state === 'ready';

  return (
    <Screen>
      <Heading title={messages.home.title} subtitle={messages.home.subtitle} />
      <View style={styles.card}>
        <Text style={styles.label}>{messages.home.fullName}</Text>
        <Text style={styles.value}>{user?.fullName}</Text>
        <Text style={styles.label}>{messages.home.rollNumber}</Text>
        <Text style={styles.value}>{profile?.rollNumber ?? messages.home.notAvailable}</Text>
      </View>

      {ready ? (
        <Text style={styles.body}>{messages.setup.ready}</Text>
      ) : (
        <View style={styles.card}>
          <Text style={styles.label}>{messages.setup.title}</Text>
          <Text style={styles.body}>{messages.setup.body}</Text>
          {setup.state === 'checking' || setup.state === 'busy' ? (
            <Text style={styles.body}>{messages.setup.busy}</Text>
          ) : null}
          <InlineError message={setup.error} />
          {setup.state === 'failed' ? (
            <ActionButton title={messages.setup.retry} onPress={runSetup} />
          ) : null}
        </View>
      )}

      <ActionButton
        title={messages.home.markAttendance}
        disabled={!ready}
        onPress={() => router.push('/attendance')}
      />
      <Text style={styles.body}>{messages.home.markAttendanceHint}</Text>
      <InlineError message={error} />
      <ActionButton title={messages.home.logout} secondary onPress={signOut} />
    </Screen>
  );
}
