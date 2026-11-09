import { useState } from 'react';
import { router } from 'expo-router';
import { View } from 'react-native';

import { useAuth } from '../../lib/auth';
import { messages } from '../../lib/messages';
import { ActionButton, Field, Heading, InlineError, Screen, styles } from '@/components/student-ui';

export default function Login() {
  const { login, message, clearMessage } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy) return;
    setError('');
    clearMessage();
    if (!email.trim() || !password) {
      setError(messages.login.missing);
      return;
    }
    setBusy(true);
    try {
      await login(email, password);
      // The root guard opens Home only after /auth/me validates this student.
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Heading title={messages.login.title} subtitle={messages.login.subtitle} />
      <View style={styles.form}>
        <Field label={messages.login.email} value={email} onChangeText={setEmail} keyboardType="email-address"
          autoCapitalize="none" autoComplete="email" textContentType="username" editable={!busy} />
        <Field label={messages.login.password} value={password} onChangeText={setPassword} secureTextEntry
          autoCapitalize="none" autoComplete="current-password" textContentType="password"
          returnKeyType="go" onSubmitEditing={submit} editable={!busy} />
        <InlineError message={message || error} />
        <ActionButton title={busy ? messages.login.submitting : messages.login.submit} loading={busy} onPress={submit} />
        <ActionButton title={messages.login.register} secondary disabled={busy} onPress={() => {
          clearMessage();
          setError('');
          router.push('/register');
        }} />
      </View>
    </Screen>
  );
}
