import {
  ActivityIndicator, KeyboardAvoidingView, Platform, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { messages } from '../../lib/messages';

export function Screen({ children }) {
  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView style={styles.fill} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={styles.content}>
          {children}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

export function Heading({ title, subtitle }) {
  return (
    <View style={styles.heading}>
      <Text style={styles.brand}>{messages.brand}</Text>
      <Text accessibilityRole="header" style={styles.title}>{title}</Text>
      <Text style={styles.body}>{subtitle}</Text>
    </View>
  );
}

export function Field({ label, ...props }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor="#64748b"
        style={styles.input}
        autoCorrect={false}
        {...props}
      />
    </View>
  );
}

export function ActionButton({ title, onPress = () => {}, disabled = false, loading = false, secondary = false }) {
  const inactive = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button, secondary && styles.secondaryButton,
        inactive && styles.disabledButton, pressed && !inactive && styles.pressed,
      ]}
    >
      {loading && <ActivityIndicator color={secondary ? '#173f7a' : '#334155'} />}
      <Text style={[styles.buttonText, secondary && styles.secondaryButtonText, inactive && styles.disabledButtonText]}>
        {title}
      </Text>
    </Pressable>
  );
}

export function InlineError({ message }) {
  if (!message) return null;
  return <Text accessibilityRole="alert" accessibilityLiveRegion="polite" style={styles.error}>{message}</Text>;
}

export const styles = StyleSheet.create({
  fill: { flex: 1 },
  safeArea: { flex: 1, backgroundColor: '#f5f7fb' },
  content: { flexGrow: 1, width: '100%', maxWidth: 520, alignSelf: 'center', padding: 24, paddingTop: 40, gap: 20 },
  heading: { gap: 10, marginBottom: 8 },
  brand: { color: '#173f7a', fontSize: 12, fontWeight: '700', letterSpacing: 1.5 },
  title: { color: '#14243d', fontSize: 30, fontWeight: '700' },
  body: { color: '#475569', fontSize: 16, lineHeight: 24 },
  form: { gap: 18 },
  field: { gap: 8 },
  label: { color: '#24344d', fontSize: 15, fontWeight: '600' },
  input: { minHeight: 54, paddingHorizontal: 16, paddingVertical: 14, borderRadius: 12, borderWidth: 1, borderColor: '#94a3b8', backgroundColor: '#fff', color: '#14243d', fontSize: 16 },
  button: { minHeight: 54, borderRadius: 12, paddingHorizontal: 20, paddingVertical: 15, backgroundColor: '#173f7a', flexDirection: 'row', gap: 10, justifyContent: 'center', alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '700', textAlign: 'center' },
  secondaryButton: { backgroundColor: '#e7eef8' },
  secondaryButtonText: { color: '#173f7a' },
  disabledButton: { backgroundColor: '#dbe2ec' },
  disabledButtonText: { color: '#475569' },
  pressed: { opacity: 0.8 },
  error: { color: '#9f1239', backgroundColor: '#fff1f2', borderRadius: 10, padding: 14, fontSize: 15, lineHeight: 22 },
  card: { gap: 12, padding: 22, backgroundColor: '#fff', borderRadius: 16, borderWidth: 1, borderColor: '#dbe2ec' },
  value: { color: '#14243d', fontSize: 20, fontWeight: '600' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16, backgroundColor: '#f5f7fb' },
});
