# DEMO_RESET - EduLedger cold start (PowerShell, local services)

Follow this in order on the local demo machine. Use separate PowerShell
terminals for long-running services; stop at any failed command and read its
error before continuing. No Docker is used.

**Never reset Postgres without redeploying the chain. Never reset users alone.**
WorkloadLedger permits one final entry per ScheduledClass id forever. Rewinding
DB ids against an old chain causes collisions; old proposal mirrors also become
stale after redeployment. The recovery sequence is: fresh Hardhat chain -> all
five deployments -> local-state reset -> seeds -> servers -> real admin bootstrap.
Ordinary smoke cleanup deletes only its own rows, never resets ID sequences, and
does not require a redeploy between runs.

In each new terminal, set:

```powershell
$Repo = "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger"
$Api = "http://127.0.0.1:5000/api/v1"
```

## 0. Prerequisites and stale processes

- Python 3.12 environment at `backend/venv`, with `backend/requirements.txt`
  installed; use `venv\Scripts\python.exe` below, not an unrelated system Python.
- Local PostgreSQL installed and the demo database/user provisioned.
- Kubo (`ipfs`) installed and its repo initialized (`ipfs init` only on first use).
- Redis installed and reachable at `redis://127.0.0.1:6379/0` (Windows Redis
  service, Memurai, or a native `redis-server`). It is the SocketIO message
  queue behind the live attendance panel (D5) and the wrong-code counters.
- Node/npm dependencies installed in `contracts/`, `frontend/` and `mobile/`.
- `backend/.env` present. Set `DB_URL` to the local **PostgreSQL** database, not
  the SQLite default in `.env.example`. The smoke process must use the same DB,
  RPC, email domain and IPFS configuration as the live Flask process.
- Keep local settings: `HARDHAT_RPC_URL=http://127.0.0.1:8545`, `CHAIN_ID=31337`,
  `IPFS_API_URL=http://127.0.0.1:5001`, `REDIS_URL=redis://127.0.0.1:6379/0`,
  `SCHEDULER_ENABLED=true`,
  `TIMETABLE_TIMEZONE=Asia/Karachi`, `SESSION_START_GRACE_MINUTES=5`, and the
  usual `UNCONDUCTED_THRESHOLD_MINUTES=15`. Synthetic smoke images require the
  demo face embedder (`FACE_EMBEDDING_PROVIDER=demo` for this local smoke run).
- Set a nonempty `BOOTSTRAP_ADMIN_SECRET`, a valid nonzero `ADMIN_ROOT_ADDRESS`,
  and `JWT_SECRET` in `.env`. Use your chosen local root address; verify it
  against the node's account list. Real deployments must use their own values.

For a deliberate cold reset, stop Flask and Vite with Ctrl+C, then stop the old
Hardhat node with Ctrl+C. Stop/restart IPFS only if stale. Do not blindly kill
all listeners or remove an IPFS lock while its daemon is alive. Inspect owners:

```powershell
Get-NetTCPConnection -LocalPort 5000, 5173, 8545, 5001, 6379 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalPort, OwningProcess
# Inspect a reported PID before stopping that specific stale demo process:
Get-Process -Id <PID>
# After confirming ownership, if graceful shutdown is unavailable:
Stop-Process -Id <PID>
```

`<PID>` is a placeholder to replace, not a literal PowerShell argument.
Restarting Hardhat loses its in-memory chain, including the governance lock.

## 1. Start PostgreSQL (elevated terminal if required)

```powershell
Get-Service -Name "*postgres*"
$PgService = Read-Host "Enter the installed PostgreSQL service name from the list"
Start-Service -Name $PgService
Get-Service -Name $PgService
```

If already running, leave it running. Verify `DB_URL` points to the provisioned
demo database. Do not drop the database or reset its users independently.

## 2. Start Redis (keep open or as a service)

Redis is required from Stage 1 on: it carries the SocketIO message queue that
pushes the instructor's live check-ins (D5) and the per-student wrong-code
counters. Without it a scan still locks attendance, but the live panel and the
smoke test's M1.8 stay silent.

```powershell
Get-Service -Name "*redis*" -ErrorAction SilentlyContinue
$RedisService = Read-Host "Enter the installed Redis service name (blank to run redis-server)"
if ($RedisService) { Start-Service -Name $RedisService } # otherwise: redis-server
redis-cli ping
```

`redis-cli ping` must print `PONG`. If `redis-cli` is not on PATH, check the
service is running and port 6379 is listening. Memurai answers the same
commands; do not introduce Docker. `backend/.env` may leave `REDIS_URL` at
`redis://127.0.0.1:6379/0`; a different port/database must be set there and
Flask restarted.

## 3. Terminal A - start a fresh Hardhat node (keep open)

```powershell
Set-Location "$Repo\contracts"
npm run node
```

Wait for the JSON-RPC server on `http://127.0.0.1:8545`. Use a fresh node for
this full reset, not one containing earlier deployments or test transactions.

## 4. Terminal B - start IPFS (keep open)

```powershell
ipfs daemon
```

Wait for `Daemon is ready` (API 5001, gateway 8080). Use the same `IPFS_PATH`
repository as any previous local daemon. The smoke run needs local Kubo so its
own evidence can be unpinned during cleanup; do not rely on Web3.Storage fallback.

## 5. Terminal C - deploy all five contracts in this exact order

```powershell
Set-Location "$Repo\contracts"
npm run deploy:audit-trail
npm run deploy:attendance
npm run deploy:local
npm run deploy:workload
npm run deploy:governance
```

Require each command to finish successfully before the next. Governance must be
last. Deploy scripts can also send role-grant transactions, so addresses depend
on all earlier deployer nonces: **copy the printed addresses**, do not assume
or copy old hardcoded addresses. Deployment writes each address/ABI artifact into
`contracts/deployed/` as well.

Update these five variables in `backend/.env`:

| Order | Contract / command | Environment variable |
|---|---|---|
| 1 | RecordAuditTrail / `deploy:audit-trail` | `AUDIT_TRAIL_CONTRACT_ADDRESS` |
| 2 | AttendanceLedger / `deploy:attendance` | `ATTENDANCE_ENGINE_CONTRACT_ADDRESS` |
| 3 | EduLedgerRoles / `deploy:local` | `ROLE_REGISTRY_CONTRACT_ADDRESS` |
| 4 | WorkloadLedger / `deploy:workload` | `WORKLOAD_LEDGER_CONTRACT_ADDRESS` |
| 5 | GovernanceRegistry / `deploy:governance` | `GOVERNANCE_CONTRACT_ADDRESS` |

The configured `BACKEND_SIGNER_PRIVATE_KEY` must be the account that deployed
GovernanceRegistry (the deploy script prints its address), and hold the backend
roles on the attendance/workload contracts. Record signers must match the
instructor/HOD roles granted by `deploy:audit-trail`. Do not copy API passwords
into signer settings. Clear stale artifact-path overrides or point them to the
current exported artifacts. Flask must be restarted after `.env` changes.

## 6. Terminal C - reset local state, then seed whitelist, workload and grades

Keep Flask stopped during reset/seeding, and use only the freshly deployed chain.

```powershell
Set-Location "$Repo\backend"
.\venv\Scripts\python.exe reset_local_state.py
.\venv\Scripts\python.exe seed_whitelist_demo.py
.\venv\Scripts\python.exe seed_workload_demo.py
.\venv\Scripts\python.exe seed_grades_demo.py
```

`reset_local_state.py` clears proposal mirrors, workload logs/tokens/sessions,
scheduled classes and attendance Student rows, restarting those DB sequences.
It preserves auth users/profiles and attendance sessions. `seed_whitelist_demo.py`
replaces the whitelist with the demo directory: do not run it during a test.

`seed_grades_demo.py` gives the three lowest-id STUDENT users BSCS-401 = `B`
and BSCS-402 = `C+`, the rows the grade-correction demo edits. It is idempotent
(existing rows, values and locks are left untouched) and needs at least one
registered STUDENT: on a cold reset run it once the demo student exists
(register on the login page, step 8; re-running later is safe, even while Flask
runs). The smoke test seeds its own throwaway grade row and never touches
BSCS-401/402.

The workload seed creates/reuses real FACULTY accounts and prints their actual
user ids. With the default domain, they are:

- `faculty.workload.demo@gcu.edu.pk` (`EMP-WORKLOAD-DEMO`)
- `faculty.workload.substitute@gcu.edu.pk` (`EMP-WORKLOAD-SUB`)
- `faculty.workload.overdue@gcu.edu.pk` (`EMP-WORKLOAD-OVERDUE`)

New seed accounts use the local demo password `WorkloadDemoFaculty123!`;
existing accounts keep their passwords. Its three main slots are hours in the
future, while the overdue slot will auto-flag after Flask starts. The seeder
still prints obsolete `MOCK_CLASSES` instructions: **ignore them**. The frontend
now loads real classes; do not edit frontend ids, labels or times.

## 7. Terminal C - start Flask (keep open)

```powershell
Set-Location "$Repo\backend"
.\venv\Scripts\python.exe run.py
```

Wait for port 5000. From a separate terminal, inspect configuration:

```powershell
Invoke-RestMethod "$Api/health" | ConvertTo-Json -Depth 6
```

Database/RPC and all five contract configuration flags must be true. These are
configuration checks, not proof that chain or IPFS calls succeed. `run.py` has
no reloader: restart it after any backend code or `.env` change.

`/health` does not report Redis. If Flask started before Redis, restart it so
the SocketIO message queue reconnects, then confirm `redis-cli ping` still
prints `PONG`.

## 8. Terminal D - start frontend (keep open)

```powershell
Set-Location "$Repo\frontend"
npm run dev
```

Open `http://localhost:5173/login`. Real logins route ADMIN/HOD/FACULTY/STUDENT
to their own pages; the old role-picker/mock login no longer exists.

## 9. Terminal E - bootstrap the REAL demo admin BEFORE running any test

Choose the account you will actually use at the demo. With the default
institutional domain, the following example uses `admin.demo@gcu.edu.pk`.
Use the exact secret configured in `backend/.env` (it is not automatically
imported into PowerShell). If an ADMIN with this email survived the reset, use
its original password: bootstrap does not change any existing ADMIN fields.

```powershell
$DemoAdminPassword = Read-Host "Demo admin password (existing account: original password)"
$BootstrapSecret = Read-Host "BOOTSTRAP_ADMIN_SECRET from backend/.env"
$BootstrapBody = @{
    full_name = "EduLedger Demo Admin"
    email = "admin.demo@gcu.edu.pk"
    password = $DemoAdminPassword
    bootstrap_secret = $BootstrapSecret
} | ConvertTo-Json

$Bootstrap = Invoke-RestMethod -Method Post -Uri "$Api/auth/bootstrap/admin" `
    -ContentType "application/json" -Body $BootstrapBody
$Bootstrap | Select-Object locked, rootAdmin, created, transactionHash
Invoke-RestMethod "$Api/governance/status" | ConvertTo-Json
```

Expect `locked=true`, a transaction hash and `adminUserExists=true`. Then
confirm the demo admin can log in:

```powershell
$AdminLogin = Invoke-RestMethod -Method Post -Uri "$Api/auth/login" `
    -ContentType "application/json" -Body (@{
        email = "admin.demo@gcu.edu.pk"; password = $DemoAdminPassword
    } | ConvertTo-Json)
Invoke-RestMethod "$Api/auth/me" -Headers @{ Authorization = "Bearer $($AdminLogin.token)" }
```

The lock is **permanent per chain**. A second bootstrap returns 409
`root_already_locked`; it cannot choose another root or change credentials.
If the intended demo admin is already bootstrapped on this chain, skip the
bootstrap POST and log in. Only a fresh chain/all five redeploys permit a new
bootstrap. **Do not run `test_governance.py` first:** on an unlocked chain it
creates its own throwaway ADMIN and permanently consumes the lock. It does not
delete that ADMIN. After real bootstrap, it skips its success path and tests
only status/rejections. `smoke_test_full.py` only reads governance status.

## 10. Terminal E - log in as HOD and upload the sample timetable

The whitelist seed permits `tariq.cs@gcu.edu.pk`, employee `EMP-HOD-CS-01`.
It does not create a login account. Register it once if absent; if it already
exists, **skip the registration POST** and use its existing password below.
Use the seeded institutional domain (default `gcu.edu.pk`).

```powershell
$HodPassword = Read-Host "Demo HOD password (existing account: original password)"
# First registration only; skip this POST if the HOD account already exists.
Invoke-RestMethod -Method Post -Uri "$Api/auth/register/hod" `
    -ContentType "application/json" -Body (@{
        employee_id = "EMP-HOD-CS-01"
        full_name = "Dr. Tariq Mahmood"
        department = "Computer Science"
        email = "tariq.cs@gcu.edu.pk"
        password = $HodPassword
    } | ConvertTo-Json)

$HodLogin = Invoke-RestMethod -Method Post -Uri "$Api/auth/login" `
    -ContentType "application/json" -Body (@{
        email = "tariq.cs@gcu.edu.pk"; password = $HodPassword
    } | ConvertTo-Json)
Invoke-RestMethod "$Api/auth/me" -Headers @{ Authorization = "Bearer $($HodLogin.token)" }

$TimetablePath = Join-Path $Repo "backend\sample_timetable.csv"
# Windows PowerShell 5.1 lacks Invoke-RestMethod -Form; curl.exe supplies the boundary.
$Upload = curl.exe --silent --show-error `
    -H "Authorization: Bearer $($HodLogin.token)" `
    -F "file=@$TimetablePath" "$Api/hod/timetable/upload" | ConvertFrom-Json
$Upload | ConvertTo-Json -Depth 8
```

Do not use the PowerShell `curl` alias or add a JSON/manual multipart
Content-Type. The file field is `file` and the bearer header must remain.
Alternatively use the HOD page's Timetable upload card after logging in.

Inspect `rows_created`, `classes` (courseCode, professorId, room, startTime),
and `rows_rejected` (spreadsheet row numbers and reasons). The sample's three
employee ids match the workload seed's actual faculty profiles. Expect three
created rows and no rejections on a fresh configured stack. Each row creates
one **dated occurrence**, not weekly recurrence; re-uploading adds more rows.
Times are interpreted in Asia/Karachi and returned as UTC. Sample occurrences
may be days away, so they are not immediately startable. To prepare a live demo
slot, upload a separate local timetable with a current weekday/start/end;
do not expect the smoke test to consume the sample or seeded demo classes.

## 11. Phone - bring up the Expo student app (Android demo)

The student app lives in `mobile/` (Expo SDK 58, Expo Router) and talks to the
same Flask API. It must reach this PC over the LAN: `localhost` on a phone
means the phone itself.

```powershell
Set-Location "$Repo\mobile"
npm install
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
ipconfig   # IPv4 Address of the active Wi-Fi adapter (not VPN/virtual)
```

Edit `mobile/.env` with that address and the `/api/v1` suffix:

```dotenv
EXPO_PUBLIC_API_URL=http://192.168.1.10:5000/api/v1
```

Then start Metro and open its QR code with an SDK-compatible Expo Go (or a
development build):

```powershell
npx expo start
```

- Keep the phone and the PC on the **same Wi-Fi**; guest networks/client
  isolation and VPNs block device-to-device traffic.
- Windows Firewall: allow inbound TCP **5000** (Flask) and **8081** (Metro) on
  the trusted Private network only. Never disable the firewall.
- An Expo tunnel exposes Metro, not Flask: the phone still needs the LAN URL.
- `EXPO_PUBLIC_*` values are embedded in the bundle - never put secrets there.
  Restart Expo after editing `.env`.
- First login runs the one-time device setup (`student/register-device` then
  `students/device/enroll`); **Mark Attendance** stays disabled until it
  succeeds. Clearing app data/reinstalling loses the local secret while the
  server keeps one (`secret_already_enrolled` -> admin rebind).
- Face ID and `requireAuthentication` secure storage need a development build;
  Android fingerprint/face works in Expo Go. SecureStore is unavailable in
  browsers, so demo on a phone, not a browser tab.

## 12. Terminal E - run the full smoke test

Only now, after real admin bootstrap, HOD upload and a reachable Redis:

```powershell
Set-Location "$Repo\backend"
.\venv\Scripts\python.exe smoke_test_full.py
if ($LASTEXITCODE -ne 0) { throw "Smoke test failed; read its FAIL summary before the demo." }
```

Expect `RESULT: PASSED - 27/27 steps`: 2 setup checks, 10 attendance checks
(M1.1-M1.10: QR lock, duplicate, numeric-code check-in by a second student,
live socket event, xlsx export, session end), 6 audit-trail checks (M2.1-M2.6:
IPFS evidence, proposal, HOD approval, pending list, grade-correction
lock/unlock, ADMIN 403), 7 workload checks (M3.1-M3.7), 1 device-credential
check (M4.1) and cleanup. The authenticated health gate checks all five contracts
separately. Failures still print PASS/FAIL/SKIP, all collected transaction
hashes, and exit nonzero. Output is UTF-8 safe.

The script registers unique faculty, a second faculty for substitute coverage,
two students and an HOD (plus its own temporary whitelist row and a throwaway
ADMIN for the D2 check). It uses actual user ids and four owned ScheduledClass
occurrences starting five seconds in the past, and sends login bearer tokens on
all subsequent API requests; public registration/login naturally precede the
first token. Cleanup runs even after failures and removes only this run's local
users/profiles, student identities, classes, sessions, tokens, logs, grade rows
and proposal mirrors, unpinning only the IPFS pins this run created (the
evidence PNG and the grade-correction JSON). The demo admin, HOD, faculty and
uploaded classes remain untouched. Without global IPFS GC or sequence resets,
on-chain transactions and distributed evidence copies remain immutable; a pin
that fell back to external storage cannot be removed locally, so cleanup
reports FAIL with its CID rather than claiming a clean run.

M1.8 fails loudly when Redis is down: the scan still returns 200 but no
`student_checked_in` event arrives, which is exactly the broken live-dashboard
demo - not a test flake.

## 13. Ten-minute demo script

Rehearse once with a clock in view. The stack (steps 1-8, 11) must already be
running and the smoke test green.

1. **0:00 Admin bootstrap (step 9).** POST `/auth/bootstrap/admin`, show
   `locked=true` and the transaction hash, then `GET /governance/status`. One
   line: the root is permanent per chain.
2. **1:30 HOD timetable upload (step 10).** Log in as `tariq.cs@gcu.edu.pk`,
   upload `sample_timetable.csv` (or a today-dated slot for the live class) and
   show `rows_created` / `rows_rejected`.
3. **3:00 Faculty starts the session.** Instructor page -> the class starting
   now -> Start. The projector shows the rotating QR, the 6-digit code and
   **Session #**, with the live panel beside it.
4. **4:00 Student biometric + scan.** On the phone: Mark Attendance -> Face
   Scan/Fingerprint -> scan the QR (or **Type code instead** with the 6-digit
   code and session number). The green check confirms the on-chain lock.
5. **5:30 Live dashboard.** The instructor panel shows the name, roll number
   and local check-in time with the counter incremented - pushed by the socket,
   not a refresh.
6. **6:30 Export.** Instructor/HOD page -> Export report -> this month -> xlsx;
   open it and show the Attendance and Summary sheets.
7. **8:00 Grade correction.** Faculty proposes a correction on a seeded
   BSCS-401 grade (`seed_grades_demo.py`) and the row locks; the HOD approves,
   the value changes and the lock clears. Show an ADMIN approve returning 403.
8. **9:30 Substitute.** Faculty issues a 30-minute substitute key, the
   substitute redeems it and scans once to conclude the class; the workload log
   credits the **primary** professor with the substitute as conductor (D1).

Close on the smoke-test summary: 27/27 with the transaction hash list.

## Troubleshooting

| Symptom | Likely cause | Recovery |
|---|---|---|
| Duplicate-key errors after chain redeploy; stale proposals; `workload_already_logged` | DB mirrors/ids and chain are from different lifecycles | Stop Flask; fresh Hardhat chain; all five deploys in order; copy addresses; `reset_local_state.py`; whitelist, workload then grades seed; restart servers; bootstrap intended admin. Never reset Postgres/users alone. |
| Port 8545 already held / node cannot bind | An old Hardhat process is still listening | Inspect `Get-NetTCPConnection` and its PID; Ctrl+C the confirmed old node (or stop only that PID). Restart a fresh node, then perform the full deploy/reset sequence. |
| IPFS `repo.lock` held / daemon cannot start | Another Kubo daemon uses the same repo | Inspect port 5001/PID and `IPFS_PATH`; use the existing healthy daemon or stop the confirmed stale one gracefully. Never remove `repo.lock` while a daemon is running. Restart Kubo after the owner exits. |
| Bootstrap `not_deployer` ("not deployer") | Backend transaction signer differs from GovernanceRegistry's immutable deployer | Compare the address derived from `BACKEND_SIGNER_PRIVATE_KEY` with the deploy script's printed deployer; correct the local signer/config and restart Flask. If deliberately changing deployer, perform the full cold reset. `ADMIN_ROOT_ADDRESS` is the target root, not the transaction signer. |
| Session start `schedule/too_early` ("too early") | Current UTC time is earlier than scheduled start minus the configured grace - the class genuinely has not opened, the QR is not broken | Wait until the slot opens, or upload a correctly dated local demo occurrence. Check server clock, `TIMETABLE_TIMEZONE=Asia/Karachi` and grace=5. Use the caller's ScheduledClass.id, not course code. Smoke fixtures use UTC now minus five seconds. |
| Redis not running (no `PONG`, port 6379 closed) | The Redis/Memurai service is stopped, or Flask started before it and never reconnected | Start the service (step 2), confirm `redis-cli ping` prints `PONG`, then restart Flask. Scans still succeed without Redis, but the live panel and smoke M1.8 see no `student_checked_in` event. Check `REDIS_URL` in `.env`, not just the service state. |
| Live dashboard/socket silent: no `student_checked_in`; smoke M1.8 times out | Vite dev proxy does not forward websockets, the browser holds no/expired token, or a corporate proxy strips the upgrade | Load the app through the Vite origin (`http://localhost:5173`), not a bare API host; log in again to refresh the JWT passed in `auth.token`; bypass any HTTP proxy for `127.0.0.1` and `ws://`. Confirm Redis is up. A failed `join_session` returns `join_error`, not silence - read the browser console. |
| Fingerprint/face verification fails: `identity/not_enrolled` | This device id has no enrolled secret for that student (app data cleared, reinstall, or a different device/browser) | Run the one-time device setup on the phone (step 11) or re-enroll via `/students/device/enroll`. If the server already holds a secret for another device, expect `secret_already_enrolled` until an admin rebinds. `127.0.0.1`/localhost on the phone is the phone itself. |
| Phone cannot reach the PC: `Network request failed`, axios timeout, Expo QR opens but API calls fail | Wrong LAN IP in `EXPO_PUBLIC_API_URL`, phone on cellular/guest Wi-Fi, Windows Firewall blocking 5000, or Flask bound to loopback only | Use the Wi-Fi adapter's IPv4 from `ipconfig` with `http://<ip>:5000/api/v1`; join the same non-isolated Wi-Fi; allow inbound TCP 5000 and 8081 on the Private profile; open `http://<ip>:5000/api/v1/health` in the phone browser to isolate firewall from app config; restart Expo after editing `.env`. An Expo tunnel forwards Metro only, never Flask. |
| Bootstrap `bootstrap_not_configured` / `invalid_bootstrap_secret` | Empty secret, different POST value, or Flask still uses old `.env` | Set the secret in `.env`, restart Flask, and send exactly that value. Never paste it into a shared report. |
| Bootstrap `root_already_locked` | Real admin already bootstrapped, or governance test ran first | Log in to the intended existing ADMIN if available; otherwise a fresh chain/all five deployments and reset are required. Bootstrap the real demo admin before tests. |
| Smoke `ABORTED`/health failure, missing governance config, or empty RPC calls | Addresses/artifacts do not match the live node, or Flask loaded stale config | Copy all five printed addresses and current artifacts, check RPC URL and restart Flask. Health configuration flags alone do not verify bytecode liveness. |
| IPFS upload or cleanup FAIL | Local Kubo is down or upload used external fallback | Restore the configured daemon; for a retained local CID use `ipfs pin rm <CID>` only for the reported smoke evidence. External fallback pins need removal in that service. Do not globally GC other demo evidence. |
| HOD registration 403 or timetable rejects faculty ids | Missing/mismatched whitelist or faculty profiles | Use the seeded HOD email/employee pair; run whitelist/workload seeds only during cold reset, before tests; inspect per-row reasons. HOD is the only upload role. |
| Browser HTTP 500 or connection refused | Flask is stopped while Vite proxies `/api` to 5000 | Check the Flask terminal/startup error, start it, then retry login. |

Do not rerun reset/seed scripts on the live chain to make a consumed class look
new. Use fresh dated occurrences for routine usage, or repeat this entire
cold-start sequence for a truly clean demo chain.
