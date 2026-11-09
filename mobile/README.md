# EduLedger Student

Expo Router student app (Expo SDK 58), generated with `npx create-expo-app@latest mobile` from the default template. It talks to the existing Flask API only; no backend change was made for these screens.

- Phase 1.7b: Login, Register, Home, student-only route guard.
- Phase 1.7c: device-bound identity (D4) and **Mark Attendance** (biometric → QR / numeric code → on-chain lock).

## Run on a phone

From the repository root, in PowerShell:

```powershell
cd mobile
npm install
Copy-Item .env.example .env
ipconfig
```

Only copy `.env.example` on first setup; do not overwrite an existing configuration. In `ipconfig`, find **IPv4 Address** under the active Wi-Fi adapter (not a VPN or virtual adapter). Edit `mobile/.env`, replacing the example with your PC's address:

```dotenv
EXPO_PUBLIC_API_URL=http://192.168.1.10:5000/api/v1
```

Then, from `mobile/`:

```powershell
npx expo start
```

Open the QR code with an SDK-compatible Expo Go installation (Expo Go's scanner on Android; the Camera app on iPhone), or use a compatible development build. Restart Expo after changing the API URL.

- Keep the phone and PC on the **same Wi-Fi**. Guest networks/client isolation and VPNs can block device-to-device connections.
- `localhost` and `127.0.0.1` on a physical phone mean the phone, not your PC. `0.0.0.0` is a server bind address, not a client URL.
- The backend must already be running on `0.0.0.0:5000`. Allow inbound TCP 5000 for the backend and the Expo/Metro port shown in the terminal (normally 8081) through Windows Firewall **on the trusted Private network only**. Do not disable the firewall.
- An Expo tunnel exposes Metro, not your Flask API. The phone must still reach the configured backend URL.
- Plain HTTP is only for trusted local development. Use HTTPS for deployment. Native release builds may require explicit platform networking configuration for LAN HTTP; this app does not relax production transport security.
- `EXPO_PUBLIC_*` values are embedded in the bundle and are public. Never put credentials or device secrets in them. `.env` is ignored; `.env.example` is safe to commit.

## Demo script (check by hand)

Attendance needs the chain and the backend; IPFS is not involved.

1. **Chain:** in `contracts/`, `npm run node`. **Backend:** in `backend/`, `python run.py` (port 5000, `0.0.0.0`).
2. **Web portal (instructor):** log in and start a class whose scheduled start is **within about 2 minutes of now** (the server's start gate is 5 minutes; starting much later triggers the permanent UNCONDUCTED auto-flag). The projector shows the rotating QR, the 6-digit code, and **Session #**.
3. **Phone:** log in as a student account. On first login Home runs one-time setup automatically: it creates a device id, calls `student/register-device`, then `students/device/enroll`, and stores the returned secret. **Mark Attendance** stays disabled until that succeeds; a failed setup shows a retry button and the reason.
4. **Step 1 — biometrics:** tap **Face Scan**, **Fingerprint** (whichever the phone reports), or **Verify**. The OS prompt runs; on success the app reads the secret, requests a single-use challenge, computes `HMAC_SHA256(secret, challenge + ":" + device_id)` hex, and exchanges it for a 30-second identity token. The countdown starts then.
5. **Step 2 — scan:** the camera opens automatically. Scan the projector QR (payload `{"session_id": <int>, "nonce": "<hex>"}`). Alternatively tap **Type code instead** and enter the 6-digit code; the session number is optional because the backend also resolves a code on its own.
6. **Success:** a big green check with the session number and the check-in time. The instructor's live panel should show the name, roll number, and check-in time, and the lock is on-chain.
7. **Negative checks:**
   - Scan the same QR again → “You’re already marked present.”
   - Let the QR rotate (about 10 s) and scan the stale one → “QR changed, scan again.”
   - Wait for the step-2 countdown to reach zero → back to Step 1 with “Your 30-second identity window ended.”
   - Type the wrong code repeatedly → “Too many wrong codes…” after the backend's limit.
   - Ask the instructor to end the session, then scan → “Your instructor ended this session.”
8. **Rebinding:** an administrator uses `POST /api/v1/admin/rebind-device`. That clears the server's device secret, so the next Home visit re-enrolls this phone, and the old phone's secret stops working (`not_enrolled`).

Re-running the demo with the same student on the same phone skips setup. Clearing app storage (or reinstalling on Android) loses the local secret while the server still has one: enrollment then returns `secret_already_enrolled`, which requires an admin rebind.

## Implemented flow

- Login: email/password → `POST /auth/login` → `GET /auth/me`. Register: **Full Name, Roll Number, Batch, Department, Official University Email, Password, Confirm Password** → `POST /auth/register/student`, then login.
- Home: `user.fullName`, `profile.rollNumber`, the one-time device setup state, **Mark Attendance** (enabled only after setup), and Logout.
- Device identity: a random device id (`expo-crypto` `randomUUID`) and the one-time HMAC secret live in `expo-secure-store`. The secret uses `requireAuthentication: true` when `SecureStore.canUseBiometricAuthentication()` is true, so the OS releases it only after a biometric/passcode prompt. The device id is a plain keychain item; a small plain marker records that setup completed, so Home never triggers a biometric prompt just to check state.
- Step 1 fetches the challenge **after** the biometric prompt, so the 30-second challenge TTL is not spent while the prompt is open. Challenges are single-use; a second verify always needs a new one.
- Step 2 accepts the QR payload or the typed code. Camera permission is requested when the scan step opens; `CameraView` scans QR only.
- `lib/api.js` attaches the bearer token and rejects with `ApiError` (`status`, `check`, `error`, `message`); requests time out after 20 s. A **401 signs the student out only when `check` is `auth`** (the JWT session). Device-identity and verification-token 401s stay on the attendance screen as retryable failures.
- All user-visible strings and the `{check, error}` → message map live in `lib/messages.js`.

## Biometric and platform limits

- **iPhone Face ID does not work in Expo Go.** It needs a development build (`NSFaceIDUsageDescription` is already configured via the `expo-local-authentication` plugin). Expo Go also limits SecureStore's `requireAuthentication`, so biometric-protected secret storage needs a device build.
- **Android can prompt twice** in Step 1: once for `LocalAuthentication.authenticateAsync` and again when SecureStore reads the biometric-protected secret (Android requires authentication for every protected-key operation). iOS prompts only on read. Do not treat the second prompt as an error.
- `LocalAuthentication` cannot select a modality: **Face Scan** and **Fingerprint** are labels for what the phone reports; the OS prompt uses whatever is enrolled. With no enrolled biometrics, the app shows one **Verify** button and the OS rejection is surfaced.
- Device fallback to the passcode is disabled in the biometric prompt (`disableDeviceFallback: true`) so the check-in matches D4's "biometric released the secret".
- SecureStore is unavailable in browsers, so this app targets **Android and iOS**. The secret is stored in plain form on the server (known FYP gap).
- **The success screen cannot show the class name.** `/attendance/scan` returns `session_id`, `checked_in_at`, `transaction_hash`, and `block_number`, but no course/class name, and every class lookup (`/session/:id/qr`, `/faculty/classes`, `/timetable/classes`) is FACULTY/HOD/ADMIN-only. The app shows **Session #N** plus the check-in time; showing the class name needs a new field on the scan response (or a student session-detail route).

## Files

- `lib/api.js`: fetch client, bearer token, structured errors, 401 policy.
- `lib/auth.js`: secure session persistence, login, loadUser, logout, shared auth state.
- `lib/device.js`: device id, one-time enrollment, biometric-protected secret storage.
- `lib/attendance.js`: HMAC-SHA256 via `expo-crypto`, biometric prompt, challenge/verify, QR parse, scan.
- `lib/messages.js`: every user-facing string and the error-code map.
- `src/app/_layout.tsx`: session restore and protected routes (`index`, `attendance` for STUDENT).
- `src/app/index.tsx`, `login.jsx`, `register.jsx`, `attendance.jsx`: Home, Login, Register, Mark Attendance.
- `src/components/student-ui.jsx`: plain React Native controls, keyboard-safe scrolling, 54-point minimum input/button targets; no added design library.

The generated Explore route was removed. Avoid the template's `reset-project` command: it replaces the app screens.

## Verification notes

No app, server, tests, or chain were run during this phase. Static checks only.

- Dependencies: `expo-crypto` added with `npx expo install`. `npm` now reports **10 vulnerabilities (1 low, 7 moderate, 2 high)**; no automatic audit fixes were applied.
- A static `tsc --noEmit` run reports three errors. Two are pre-existing in the unused default-template web tab bar (`src/components/app-tabs.web.tsx`: an `/explore` href and a `Pressable` ref mismatch). The third is typed-route staleness: `.expo/types/router.d.ts` is generated by `expo start` and still lists only `index`/`login`/`register`, so `router.push('/attendance')` in `src/app/index.tsx` fails the type check until the first `expo start` regenerates it. The route itself is registered in `src/app/_layout.tsx` and works. The `.js`/`.jsx` files are not type-checked by this config.
