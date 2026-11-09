import { useCallback, useEffect, useRef, useState } from 'react';
import { router } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import {
  authenticateBiometric, describeBiometrics, parseQrPayload,
  submitAttendanceScan, verifyDeviceIdentity,
} from '../../lib/attendance';
import { describeApiError, messages } from '../../lib/messages';
import { ActionButton, Field, Heading, InlineError, Screen, styles } from '@/components/student-ui';

const STEP = { IDENTITY: 'identity', SCAN: 'scan', SUCCESS: 'success' };

function formatTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function biometricChoices(info) {
  if (!info) return [];
  const choices = [];
  if (info.face) choices.push({ key: 'face', label: messages.attendance.faceScan });
  if (info.fingerprint) choices.push({ key: 'fingerprint', label: messages.attendance.fingerprint });
  if (info.iris) choices.push({ key: 'iris', label: messages.attendance.iris });
  return choices;
}

export default function MarkAttendance() {
  const [step, setStep] = useState(STEP.IDENTITY);
  const [biometrics, setBiometrics] = useState(null);
  const [identityBusy, setIdentityBusy] = useState(false);
  const [session, setSession] = useState(null); // {token, deviceId, expiresAt}
  const [remaining, setRemaining] = useState(0);
  const [failure, setFailure] = useState('');
  const [scanHint, setScanHint] = useState('');
  const [busy, setBusy] = useState(false);
  const [codeMode, setCodeMode] = useState(false);
  const [code, setCode] = useState('');
  const [sessionNumber, setSessionNumber] = useState('');
  const [result, setResult] = useState(null);
  const scannedRef = useRef(false);
  const [permission, requestPermission] = useCameraPermissions();

  useEffect(() => {
    let active = true;
    describeBiometrics()
      .then((info) => { if (active) setBiometrics(info); })
      .catch(() => {
        if (active) setBiometrics({ hasHardware: false, enrolled: false });
      });
    return () => { active = false; };
  }, []);

  // The 30 s identity window starts when the token is minted.
  useEffect(() => {
    if (step !== STEP.SCAN || !session) return undefined;
    const tick = () => {
      const left = Math.max(0, Math.ceil((session.expiresAt - Date.now()) / 1000));
      setRemaining(left);
      if (left === 0) {
        scannedRef.current = false;
        setFailure(messages.attendance.tokenExpired);
        setStep(STEP.IDENTITY);
      }
    };
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [step, session]);

  // Open the camera automatically: ask once when the scan step is shown.
  useEffect(() => {
    if (step !== STEP.SCAN || codeMode) return;
    if (permission && !permission.granted && permission.canAskAgain) requestPermission();
  }, [step, codeMode, permission, requestPermission]);

  function resetScanState() {
    scannedRef.current = false;
    setFailure('');
    setScanHint('');
    setCode('');
    setSessionNumber('');
    setCodeMode(false);
  }

  async function startIdentity() {
    if (identityBusy) return;
    setFailure('');
    setIdentityBusy(true);
    try {
      await authenticateBiometric();
      const identity = await verifyDeviceIdentity();
      resetScanState();
      setSession(identity);
      setRemaining(Math.ceil(identity.expiresIn));
      setStep(STEP.SCAN);
    } catch (error) {
      setFailure(describeApiError(error));
    } finally {
      setIdentityBusy(false);
    }
  }

  const sendScan = useCallback(async ({ sessionId, nonce, numericCode }) => {
    if (busy || !session) return;
    setBusy(true);
    setFailure('');
    setScanHint('');
    try {
      const payload = await submitAttendanceScan({
        token: session.token,
        deviceId: session.deviceId,
        sessionId,
        nonce,
        numericCode,
      });
      setResult(payload);
      setStep(STEP.SUCCESS);
    } catch (error) {
      setFailure(describeApiError(error));
      scannedRef.current = false;
    } finally {
      setBusy(false);
    }
  }, [busy, session]);

  async function handleBarcode({ data }) {
    if (busy || scannedRef.current) return;
    const payload = parseQrPayload(data);
    if (!payload) {
      setScanHint(messages.attendance.scan.notAttendanceCode);
      return;
    }
    scannedRef.current = true;
    await sendScan({ sessionId: payload.sessionId, nonce: payload.nonce });
  }

  async function submitCode() {
    if (busy) return;
    if (!/^\d{6}$/.test(code.trim())) {
      setFailure(messages.attendance.scan.badCode);
      return;
    }
    if (sessionNumber.trim() && !/^\d+$/.test(sessionNumber.trim())) {
      setFailure(messages.attendance.scan.sessionInvalid);
      return;
    }
    await sendScan({
      sessionId: sessionNumber.trim() ? Number(sessionNumber.trim()) : undefined,
      numericCode: code.trim(),
    });
  }

  if (step === STEP.SUCCESS) {
    return (
      <Screen>
        <View style={screen.checkCircle}>
          <Text style={screen.check}>✓</Text>
        </View>
        <Text accessibilityRole="header" style={screen.successTitle}>
          {messages.attendance.success.title}
        </Text>
        <Text style={styles.body}>{messages.attendance.success.body}</Text>
        <View style={styles.card}>
          <Text style={styles.label}>{messages.attendance.success.session}</Text>
          <Text style={styles.value}>
            {result?.session_id != null ? `#${result.session_id}` : '—'}
          </Text>
          <Text style={styles.label}>{messages.attendance.success.checkedIn}</Text>
          <Text style={styles.value}>{formatTime(result?.checked_in_at)}</Text>
        </View>
        <ActionButton
          title={messages.attendance.success.done}
          onPress={() => router.replace('/')}
        />
      </Screen>
    );
  }

  if (step === STEP.IDENTITY) {
    const choices = biometricChoices(biometrics);
    return (
      <Screen>
        <Heading title={messages.attendance.title} subtitle={messages.attendance.identitySubtitle} />
        {biometrics === null ? (
          <View style={screen.loading}>
            <ActivityIndicator color="#173f7a" />
            <Text style={styles.body}>{messages.attendance.checking}</Text>
          </View>
        ) : (
          <View style={styles.form}>
            {biometrics.hasHardware === false ? (
              <InlineError message={messages.attendance.noBiometricsHardware} />
            ) : biometrics.enrolled === false ? (
              <InlineError message={messages.attendance.noBiometrics} />
            ) : null}
            <InlineError message={failure} />
            {choices.length > 0 ? (
              choices.map((choice) => (
                <ActionButton
                  key={choice.key}
                  title={choice.label}
                  loading={identityBusy}
                  onPress={startIdentity}
                />
              ))
            ) : (
              <ActionButton
                title={messages.attendance.verify}
                loading={identityBusy}
                onPress={startIdentity}
              />
            )}
            {identityBusy ? <Text style={styles.body}>{messages.attendance.verifying}</Text> : null}
            <ActionButton
              title={messages.attendance.home}
              secondary
              disabled={identityBusy}
              onPress={() => router.replace('/')}
            />
          </View>
        )}
      </Screen>
    );
  }

  return (
    <Screen>
      <Heading title={messages.attendance.title} subtitle={messages.attendance.scanSubtitle} />
      <View style={screen.countdown}>
        <Text style={styles.label}>{messages.attendance.scan.secondsLeft(remaining)}</Text>
      </View>
      <InlineError message={failure} />
      {scanHint ? <Text style={styles.body}>{scanHint}</Text> : null}

      {codeMode ? (
        <View style={styles.form}>
          <Field
            label={messages.attendance.scan.codeLabel}
            value={code}
            onChangeText={setCode}
            keyboardType="number-pad"
            maxLength={6}
            editable={!busy}
          />
          <Text style={styles.body}>{messages.attendance.scan.codeHint}</Text>
          <Field
            label={messages.attendance.scan.sessionLabel}
            value={sessionNumber}
            onChangeText={setSessionNumber}
            keyboardType="number-pad"
            editable={!busy}
          />
          <Text style={styles.body}>{messages.attendance.scan.sessionHint}</Text>
          <ActionButton
            title={busy ? messages.attendance.scan.submitting : messages.attendance.scan.submit}
            loading={busy}
            onPress={submitCode}
          />
          <ActionButton
            title={messages.attendance.scan.scanQr}
            secondary
            disabled={busy}
            onPress={() => { resetScanState(); }}
          />
        </View>
      ) : (
        <View style={styles.form}>
          {permission?.granted ? (
            <CameraView
              style={screen.camera}
              facing="back"
              barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={busy ? undefined : handleBarcode}
            />
          ) : permission?.canAskAgain === false ? (
            <InlineError message={messages.attendance.camera.denied} />
          ) : (
            <View style={[screen.camera, screen.cameraPending]}>
              <ActivityIndicator color="#173f7a" />
              <Text style={styles.body}>{messages.attendance.camera.starting}</Text>
            </View>
          )}
          <Text style={styles.body}>{messages.attendance.scan.hint}</Text>
          {busy ? <Text style={styles.body}>{messages.attendance.scan.submitting}</Text> : null}
          {permission && !permission.granted && permission.canAskAgain ? (
            <ActionButton title={messages.attendance.camera.grant} onPress={requestPermission} />
          ) : null}
          <ActionButton
            title={messages.attendance.scan.typeCode}
            secondary
            disabled={busy}
            onPress={() => setCodeMode(true)}
          />
        </View>
      )}

      {(failure || scanHint) ? (
        <View style={styles.form}>
          <ActionButton
            title={messages.attendance.retryScan}
            secondary
            disabled={busy}
            onPress={() => { setFailure(''); setScanHint(''); scannedRef.current = false; }}
          />
          <ActionButton
            title={messages.attendance.restart}
            secondary
            disabled={busy}
            onPress={() => {
              resetScanState();
              setStep(STEP.IDENTITY);
            }}
          />
        </View>
      ) : null}
    </Screen>
  );
}

const screen = StyleSheet.create({
  loading: { alignItems: 'center', gap: 12 },
  checkCircle: {
    width: 132, height: 132, borderRadius: 66, alignSelf: 'center',
    alignItems: 'center', justifyContent: 'center', backgroundColor: '#dcfce7',
    borderWidth: 3, borderColor: '#16a34a', marginTop: 24,
  },
  check: { color: '#15803d', fontSize: 76, fontWeight: '700', lineHeight: 86 },
  successTitle: {
    color: '#15803d', fontSize: 26, fontWeight: '700', textAlign: 'center',
  },
  countdown: { alignItems: 'flex-end' },
  camera: {
    height: 320, borderRadius: 16, overflow: 'hidden', backgroundColor: '#0f172a',
  },
  cameraPending: {
    backgroundColor: '#e7eef8', alignItems: 'center', justifyContent: 'center', gap: 10,
  },
});
