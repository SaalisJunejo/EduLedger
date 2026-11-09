import { useState } from 'react';
import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { api } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { messages } from '../../lib/messages';
import { ActionButton, Field, Heading, InlineError, Screen, styles } from '@/components/student-ui';

const initialForm = {
  full_name: '', roll_number: '', batch: '', department: '', email: '', password: '', confirm: '',
};

const labels = messages.register.fields;
const fields = [
  { key: 'full_name', label: labels.fullName, autoCapitalize: 'words', autoComplete: 'name' },
  { key: 'roll_number', label: labels.rollNumber, autoCapitalize: 'characters' },
  { key: 'batch', label: labels.batch, autoCapitalize: 'none' },
  { key: 'department', label: labels.department, autoCapitalize: 'words' },
  { key: 'email', label: labels.email, autoCapitalize: 'none', keyboardType: 'email-address', autoComplete: 'email' },
  { key: 'password', label: labels.password, autoCapitalize: 'none', secureTextEntry: true, autoComplete: 'new-password' },
  { key: 'confirm', label: labels.confirm, autoCapitalize: 'none', secureTextEntry: true, autoComplete: 'new-password' },
];

export default function Register() {
  const { login, clearMessage } = useAuth();
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [registered, setRegistered] = useState(false);

  async function submit() {
    if (busy || registered) return;
    clearMessage();
    setError('');
    if (Object.entries(form).some(([key, value]) => !(key === 'password' || key === 'confirm' ? value : value.trim()))) {
      setError(messages.register.complete);
      return;
    }
    if (form.password !== form.confirm) {
      setError(messages.register.mismatch);
      return;
    }
    setBusy(true);
    try {
      await api('/auth/register/student', {
        method: 'POST', auth: false,
        body: {
          full_name: form.full_name.trim(), roll_number: form.roll_number.trim(),
          batch: form.batch.trim(), department: form.department.trim(),
          email: form.email.trim(), password: form.password,
        },
      });
      // Never submit registration twice if the subsequent login is interrupted.
      setRegistered(true);
      await login(form.email, form.password);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Heading title={messages.register.title} subtitle={messages.register.subtitle} />
      <View style={styles.form}>
        {fields.map(({ key, ...props }) => (
          <Field key={key} {...props} value={form[key]} editable={!busy && !registered}
            onChangeText={(value) => setForm((previous) => ({ ...previous, [key]: value }))} />
        ))}
        {registered && <Text style={styles.body}>{messages.register.created}</Text>}
        <InlineError message={error} />
        <ActionButton title={busy
          ? (registered ? messages.register.loggingIn : messages.register.submitting)
          : messages.register.submit}
          loading={busy} disabled={registered} onPress={submit} />
        <ActionButton title={registered ? messages.register.continueToLogin : messages.register.haveAccount}
          secondary disabled={busy}
          onPress={() => { clearMessage(); router.replace('/login'); }} />
      </View>
    </Screen>
  );
}
