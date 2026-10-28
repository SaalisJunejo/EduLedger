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
- Node/npm dependencies installed in `contracts/` and `frontend/`.
- Local PostgreSQL installed and the demo database/user provisioned.
- Kubo (`ipfs`) installed and its repo initialized (`ipfs init` only on first use).
- `backend/.env` present. Set `DB_URL` to the local **PostgreSQL** database, not
  the SQLite default in `.env.example`. The smoke process must use the same DB,
  RPC, email domain and IPFS configuration as the live Flask process.
- Keep local settings: `HARDHAT_RPC_URL=http://127.0.0.1:8545`, `CHAIN_ID=31337`,
  `IPFS_API_URL=http://127.0.0.1:5001`, `SCHEDULER_ENABLED=true`,
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
Get-NetTCPConnection -LocalPort 5000, 5173, 8545, 5001 -State Listen -ErrorAction SilentlyContinue |
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

Redis is **currently unused**; no Redis process is needed for Stage 0 or the
smoke test. Only if a later configuration actually uses it, start your installed
local Redis service or keep `redis-server` running in another terminal. Do not
introduce Docker or Redis as a new smoke prerequisite.

## 2. Terminal A - start a fresh Hardhat node (keep open)

```powershell
Set-Location "$Repo\contracts"
npm run node
```

Wait for the JSON-RPC server on `http://127.0.0.1:8545`. Use a fresh node for
this full reset, not one containing earlier deployments or test transactions.

## 3. Terminal B - start IPFS (keep open)

```powershell
ipfs daemon
```

Wait for `Daemon is ready` (API 5001, gateway 8080). Use the same `IPFS_PATH`
repository as any previous local daemon. The smoke run needs local Kubo so its
own evidence can be unpinned during cleanup; do not rely on Web3.Storage fallback.

## 4. Terminal C - deploy all five contracts in this exact order

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

## 5. Terminal C - reset local state, then seed whitelist and workload

Keep Flask stopped during reset/seeding, and use only the freshly deployed chain.

```powershell
Set-Location "$Repo\backend"
.\venv\Scripts\python.exe reset_local_state.py
.\venv\Scripts\python.exe seed_whitelist_demo.py
.\venv\Scripts\python.exe seed_workload_demo.py
```

`reset_local_state.py` clears proposal mirrors, workload logs/tokens/sessions,
scheduled classes and attendance Student rows, restarting those DB sequences.
It preserves auth users/profiles and attendance sessions. `seed_whitelist_demo.py`
replaces the whitelist with the demo directory: do not run it during a test.

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

## 6. Terminal C - start Flask (keep open)

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

## 7. Terminal D - start frontend (keep open)

```powershell
Set-Location "$Repo\frontend"
npm run dev
```

Open `http://localhost:5173/login`. Real logins route ADMIN/HOD/FACULTY/STUDENT
to their own pages; the old role-picker/mock login no longer exists.

## 8. Terminal E - bootstrap the REAL demo admin BEFORE running any test

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

## 9. Terminal E - log in as HOD and upload the sample timetable

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

## 10. Terminal E - run the full smoke test

Only now, after real admin bootstrap and HOD upload:

```powershell
Set-Location "$Repo\backend"
.\venv\Scripts\python.exe smoke_test_full.py
if ($LASTEXITCODE -ne 0) { throw "Smoke test failed; read its FAIL summary before the demo." }
```

Expect `RESULT: PASSED - 20/20 steps`: the 17 original end-to-end module checks,
inline auth setup, governance status 200, and cleanup. The authenticated health
gate checks all five contracts separately. Failures still print PASS/FAIL/SKIP,
all collected transaction hashes, and exit nonzero. Output is UTF-8 safe.

The script registers unique faculty, a second faculty for substitute coverage,
student and HOD accounts (including its own temporary whitelist row). It uses
actual user ids and three owned ScheduledClass occurrences starting five seconds
in the past, and sends login bearer tokens on all subsequent API requests.
Public registration/login requests naturally precede the first token.
Cleanup runs even after failures, removing only this run's local users/profiles,
student identity, classes, sessions, tokens, logs and proposal mirror; the demo
admin, HOD, faculty and uploaded classes remain untouched. It unpins only its
unique local evidence, without global IPFS GC or sequence resets. On-chain
transactions and distributed evidence copies remain immutable. If upload falls
back to external storage, local unpin cannot clean that pin: cleanup reports
FAIL with its CID for manual removal rather than claiming a clean run.

## Troubleshooting

| Symptom | Likely cause | Recovery |
|---|---|---|
| Duplicate-key errors after chain redeploy; stale proposals; `workload_already_logged` | DB mirrors/ids and chain are from different lifecycles | Stop Flask; fresh Hardhat chain; all five deploys in order; copy addresses; `reset_local_state.py`; whitelist then workload seed; restart servers; bootstrap intended admin. Never reset Postgres/users alone. |
| Port 8545 already held / node cannot bind | An old Hardhat process is still listening | Inspect `Get-NetTCPConnection` and its PID; Ctrl+C the confirmed old node (or stop only that PID). Restart a fresh node, then perform the full deploy/reset sequence. |
| IPFS `repo.lock` held / daemon cannot start | Another Kubo daemon uses the same repo | Inspect port 5001/PID and `IPFS_PATH`; use the existing healthy daemon or stop the confirmed stale one gracefully. Never remove `repo.lock` while a daemon is running. Restart Kubo after the owner exits. |
| Bootstrap `not_deployer` ("not deployer") | Backend transaction signer differs from GovernanceRegistry's immutable deployer | Compare the address derived from `BACKEND_SIGNER_PRIVATE_KEY` with the deploy script's printed deployer; correct the local signer/config and restart Flask. If deliberately changing deployer, perform the full cold reset. `ADMIN_ROOT_ADDRESS` is the target root, not the transaction signer. |
| Session start `schedule/too_early` | Current UTC time is earlier than scheduled start minus the configured grace | Wait until the slot opens, or upload a correctly dated local demo occurrence. Check server clock, `TIMETABLE_TIMEZONE=Asia/Karachi` and grace=5. Use the caller's ScheduledClass.id, not course code. Smoke fixtures use UTC now minus five seconds. |
| Bootstrap `bootstrap_not_configured` / `invalid_bootstrap_secret` | Empty secret, different POST value, or Flask still uses old `.env` | Set the secret in `.env`, restart Flask, and send exactly that value. Never paste it into a shared report. |
| Bootstrap `root_already_locked` | Real admin already bootstrapped, or governance test ran first | Log in to the intended existing ADMIN if available; otherwise a fresh chain/all five deployments and reset are required. Bootstrap the real demo admin before tests. |
| Smoke `ABORTED`/health failure, missing governance config, or empty RPC calls | Addresses/artifacts do not match the live node, or Flask loaded stale config | Copy all five printed addresses and current artifacts, check RPC URL and restart Flask. Health configuration flags alone do not verify bytecode liveness. |
| IPFS upload or cleanup FAIL | Local Kubo is down or upload used external fallback | Restore the configured daemon; for a retained local CID use `ipfs pin rm <CID>` only for the reported smoke evidence. External fallback pins need removal in that service. Do not globally GC other demo evidence. |
| HOD registration 403 or timetable rejects faculty ids | Missing/mismatched whitelist or faculty profiles | Use the seeded HOD email/employee pair; run whitelist/workload seeds only during cold reset, before tests; inspect per-row reasons. HOD is the only upload role. |
| Browser HTTP 500 or connection refused | Flask is stopped while Vite proxies `/api` to 5000 | Check the Flask terminal/startup error, start it, then retry login. |

Do not rerun reset/seed scripts on the live chain to make a consumed class look
new. Use fresh dated occurrences for routine usage, or repeat this entire
cold-start sequence for a truly clean demo chain.
