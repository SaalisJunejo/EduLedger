# DEMO_RESET - EduLedger: from dead/stale stack to demo-ready

One page, one pass, exact commands. Follow it top to bottom whenever the stack
is stale, half-running, or the on-chain demo data was consumed. Everything
runs in PowerShell from the repo root
(`d:\Study\GCUH\FYP\EduLedger Everything\EduLedger`).

Why a full reset is sometimes REQUIRED (not optional):

- `WorkloadLedger` allows **one entry per class id, forever**. Concluding a
  class (scan / zero-attendance / substitute) is permanent on that chain.
- `reset_local_state.py` rewinds the local ids to 1..N, but a fresh chain
  also re-issues ids 1..N. Re-seeding the DB **without** redeploying the
  contracts collides with the old chain state (`workload_already_logged`).
- The fix is always the same: fresh chain -> 4 deploys -> DB reset -> seed.

Total time: ~5 minutes.

---

## 0. One-time prerequisites (already true on this machine)

| Need | Check | Notes |
|---|---|---|
| Python 3.12 with backend deps | `py -3.12 -c "import requests, PIL"` | plain `python` is 3.14 WITHOUT Flask - always `py -3.12` |
| Node 18+ | `node --version` | Hardhat + Vite |
| PostgreSQL running | `Get-Service *postgres*` | service, DB `eduledger_db` (see `backend/.env`) |
| Kubo IPFS installed | `ipfs version` | 0.43.1; Module 2 evidence upload needs the daemon |
| `backend/.env` present | - | contract addresses below must match it |

## 1. Stop everything stale

```powershell
# Kill whatever holds the demo ports (empty output = nothing was running).
# Vite (5173) is stateless - only kill it if the browser misbehaves.
foreach ($port in 5000, 8545, 5001) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }
}
```

## 2. Terminal A - Hardhat node (keep open)

```powershell
cd "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger\contracts"
npm run node
```

Wait for: `Started HTTP and WebSocket JSON-RPC server at ... http://127.0.0.1:8545`

## 3. Terminal B - IPFS daemon (keep open; Module 2 needs it)

```powershell
ipfs daemon
```

Wait for: `Daemon is ready`. (First-ever run needs `ipfs init` - already done here.)

## 4. Terminal C - deploy all 4 contracts, CANONICAL ORDER

The order is NOT free: deployment order determines the deterministic
addresses, and `backend/.env` already expects exactly these:

| # | command (from `contracts/`) | contract | expected address (= backend/.env) |
|---|---|---|---|
| 1 | `npm run deploy:audit-trail` | RecordAuditTrail | `0x5FbDB2315678afecb367f032d93F642f64180aa3` |
| 2 | `npm run deploy:attendance` | AttendanceLedger | `0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9` |
| 3 | `npm run deploy:local` | EduLedgerRoles | `0x0165878A594ca255338adfa4d48449f69242Eb8F` |
| 4 | `npm run deploy:workload` | WorkloadLedger | `0xa513E6E4b8f2a923D98304ec87F64353C4D5C853` |

```powershell
cd "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger\contracts"
npm run deploy:audit-trail
npm run deploy:attendance
npm run deploy:local
npm run deploy:workload
```

- Each deploy prints its address - eyeball it against the table above.
- Addresses don't match? The chain already had deploys on it. Restart from
  step 1 (a fresh `npm run node` gives a fresh chain).
- A Windows libuv line `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)
  ... async.c` may print after a deploy - **benign noise**; ignore it if the
  address printed.
- `git status` will show `contracts/deployed/*.json` as modified - expected
  (deployedAt timestamps).

## 5. Terminal C - reset local DB + seed the demo timetable

```powershell
cd "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger\backend"
py -3.12 reset_local_state.py      # empties proposals + all Module 3 tables
py -3.12 seed_workload_demo.py     # classes #1-4 (runs BEFORE any demo data)
```

The seeder prints each class's start time, e.g. `starts 10:15`. Sync the
three labels in `frontend/src/lib/workload.js` (`MOCK_CLASSES` `start`
fields) to those times so the "My Classes" cards are honest (Vite
hot-reloads the edit; it's cosmetic but takes 20 seconds):

```js
{ id: 1, courseId: "BSCS-401", title: "Distributed Systems", room: "A-101", start: "10:15", professorId: 101 },
```

What the seed creates: 3 future classes for Prof. 101 (the UI demo roster,
untouched by the smoke test) + 1 overdue class for Prof. 103 that the live
scheduler auto-flags UNCONDUCTED on-chain within a minute (the admin
Workload Audit Log gets a realistic first row all by itself).

## 6. Terminal C - start Flask (keep open)

```powershell
py -3.12 run.py
```

(`run.py` uses `use_reloader=False` - after ANY backend code or `.env`
change, kill it (step 1) and start it again.)

Quick check - every flag must be true:

```powershell
curl.exe -s http://localhost:5000/api/v1/health
```

## 7. Terminal D - Vite (skip if already running on :5173)

```powershell
cd "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger\frontend"
npm run dev          # http://localhost:5173
```

## 8. The safety net - run the smoke test

```powershell
cd "d:\Study\GCUH\FYP\EduLedger Everything\EduLedger\backend"
py -3.12 smoke_test_full.py
```

`RESULT: PASSED - 17/17 steps` = demo-ready. The script creates its own
student/classes and never consumes the seeded demo roster, so it can be
re-run any time, including right before walking in.

## 9. The demo itself (pointers)

- Instructor - http://localhost:5173/instructor - three tabs:
  "Attendance Session" (Module 1 QR projector), "My Classes - Workload"
  (start/zero-attendance/substitute keys), "Propose Change" (Module 2).
- Simulate a student scanning a workload QR (concludes the class, flips the
  badge): `py -3.12 simulate_workload_scan.py` from `backend/`.
- Admin - http://localhost:5173/admin - Workload Audit Log table (auto-flag
  row appears within ~1 min of Flask starting).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Smoke test: `ABORTED - contract '...' is not configured` | deploy order wrong / `.env` mismatch / Flask started before `.env` was fixed | redo step 4 in order; restart Flask |
| Smoke test / UI: `409 workload_already_logged` or `class_already_logged` | DB was reset but the chain wasn't | full reset from step 1 |
| Smoke test M2.1 FAIL `ipfs_daemon_unreachable` | Kubo daemon down | start `ipfs daemon` (step 3), re-run |
| Browser shows HTTP 500 | Flask is down (Vite proxies `/api` to :5000) | step 6 |
| `not_instructor` / `not_an_approver` / role errors on Module 2 | `contracts/deployed/accounts.json` stale | from `contracts/`: `npm run seed:accounts` (signers are Hardhat's deterministic accounts; then redo step 4) |
| Nothing listens on a port after step 6/7 | process died on startup | re-run the command and read its terminal output |
| Garbled console output | cp1252 code page | `chcp 65001` |
| Deploy prints addresses that don't match the table | chain wasn't fresh | back to step 1 (fresh node = fresh chain) |
