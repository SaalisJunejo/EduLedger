# EduLedger

Blockchain-backed university trust & verification platform (FYP MVP). Instead of
trusting a single clerk or admin to act honestly, EduLedger enforces honesty
structurally: cryptographic verification, time-based automation, and multi-party
on-chain approval. Full product requirements live in [docs/PRD.md](docs/PRD.md).

**MVP modules (built on top of this scaffold):**

1. **Zero-Proxy Attendance Engine** - device-bound identity + rotating QR nonce
2. **Multi-Sig Anti-Tamper Audit Trail** - 2-of-3 signatures + IPFS evidence
3. **Faculty Workload Ledger** - session verification, substitute credit, on-chain workload

> **Status:** Module 1 and 2 contracts (`AttendanceLedger`,
> `RecordAuditTrail`) deployed on the local chain, and the backend
> attendance flow is live end to end: rotating QR nonces, student identity
> verification (face / WebAuthn stub), and check-in scans that lock
> attendance on-chain. The module logic is wired up incrementally.

## Repository layout

| Path | Purpose |
|---|---|
| `contracts/` | Solidity smart contracts (Hardhat project, OpenZeppelin) |
| `backend/` | Flask REST API (app factory, versioned blueprints, `/api/v1/health`) |
| `frontend/` | React + Vite UI (React Router role dashboards) |
| `scripts/` | Deployment and demo-seeding scripts (run via Hardhat) |
| `docs/` | Product requirements (PRD) |

## Prerequisites

- **Node.js 18+** and npm (tested on Node 24 / npm 11)
- **Python 3.11+** (tested on Python 3.14)
- Optional: **IPFS (Kubo)** daemon for evidence pinning (`ipfs daemon`, API `:5001`, gateway `:8080`)

## 1. Smart contracts (`contracts/`)

```bash
cd contracts
npm install
npm run compile
```

Start the local chain (terminal A - keep it running):

```bash
npm run node          # JSON-RPC at http://127.0.0.1:8545 (chain id 31337)
```

Deploy and seed demo roles (terminal B):

```bash
npm run deploy:local        # deploys EduLedgerRoles -> scripts/deployments/localhost.json
npm run seed:local          # grants demo roles to Hardhat test accounts
npm run seed:accounts       # exports the 5 labeled local accounts -> contracts/deployed/accounts.json
npm run deploy:audit-trail  # deploys RecordAuditTrail (Module 2) + role grants -> contracts/deployed/RecordAuditTrail.json
npm run deploy:attendance  # deploys AttendanceLedger (Module 1) + BACKEND_ROLE grant -> contracts/deployed/AttendanceLedger.json
```

`deploy:local` prints the env values to paste into `backend/.env` in the next
step (the address is deterministic on a fresh local chain:
`0x5FbDB2315678afecb367f032d93F642f64180aa3`).

`deploy:audit-trail` deploys the Module 2 `RecordAuditTrail` contract, grants
`INSTRUCTOR_ROLE` / `HOD_ROLE` / `EXAM_CONTROLLER_ROLE` to the addresses from
`deployed/accounts.json`, and exports the address + ABI to
`contracts/deployed/RecordAuditTrail.json` for the backend.

`deploy:attendance` deploys the Module 1 `AttendanceLedger` contract and
grants `BACKEND_ROLE` to the deployer account, which acts as the trusted
backend signer during local development (the admin can later grant the role
to the real backend signing address). The address + ABI are exported to
`contracts/deployed/AttendanceLedger.json`. The contract anchors one
immutable PRESENT record per student+session pair; a second
`lockAttendance()` call for the same pair reverts with
`AttendanceAlreadyLocked` (the on-chain "no resubmission" rule).

### Contract tests

`test/RecordAuditTrail.test.js` (Mocha/Chai) runs against the in-process
Hardhat network - no local node or deployment required:

```bash
cd contracts
npm test
```

The 14 tests cover role assignment at deployment, the propose -> approve ->
auto-execute happy path (including the `RecordUpdated` event payload),
access control on `proposeChange` / `approve`, duplicate and late approvals,
input validation, and `getProposal` state before and after execution.

## 2. Backend (`backend/`)

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env        # Windows (use: cp on macOS/Linux)
```

Edit `.env` and paste the contract values printed by the deploy step, then:

```bash
python run.py                 # http://127.0.0.1:5000
```

Health check:

```bash
# GET http://127.0.0.1:5000/api/v1/health
curl http://127.0.0.1:5000/api/v1/health
```

```json
{
  "dependencies": {
    "blockchain_rpc_configured": true,
    "contracts_configured": {
      "attendance_engine": false,
      "audit_trail": false,
      "role_registry": true,
      "workload_ledger": false
    },
    "database_configured": true,
    "ipfs_configured": true
  },
  "environment": "development",
  "service": "eduledger-backend",
  "status": "ok",
  "version": "0.1.0"
}
```

(`contracts_configured` flips to `true` for the module contracts once they
are deployed and their addresses are added to `.env`.)

### Attendance sessions (Module 1)

Instructor-driven QR attendance backed by a rotating nonce:

```bash
# instructor starts a session for a class (one live session per class -
# starting a new one closes the previous)
curl -X POST http://127.0.0.1:5000/api/v1/session/start \
  -H "Content-Type: application/json" -d '{"class_id": "BSCS-401"}'

# projector-facing endpoint: poll every 1-2 seconds
curl http://127.0.0.1:5000/api/v1/session/1/qr
```

`POST /session/start` returns the session (`id`, `current_nonce`,
`nonce_expires_at`) plus the `qr_poll_url` to display. `GET /session/<id>/qr`
always reflects the latest nonce and returns `qr_image` (a base64 PNG data
URI encoding `{"session_id": <id>, "nonce": "<current nonce>"}`) alongside
`nonce_expires_at`, `server_time`, and `rotation_seconds`, so a projector page
can poll every 1-2 seconds, swap its `<img src>` to `qr_image`, and re-render
before the QR changes. Responses are sent with `Cache-Control: no-store`.

A background APScheduler job (`app/scheduler.py`) rotates the nonce of every
active session every `NONCE_ROTATION_SECONDS` (default 5 s). Each nonce is a
hex timestamp plus a 24-byte random token, so a photographed QR cannot be
replayed after it rotates; if the scheduler ever lags, the QR endpoint
rotates an expired nonce lazily on read. Unknown sessions return `404`, and
sessions replaced by a newer `start` return `409` (the projector should stop
polling). `python run.py` starts the scheduler automatically (and disables
the debug reloader to keep a single scheduler instance); instructor
authentication on `/session/start` arrives with the JWT layer (roadmap).

### Identity verification & check-in scan (Module 1)

Student check-in in four steps - enroll once, bind one device, then per
class: verify identity and scan the rotating QR:

```bash
# 1. (demo) enroll a student's face - real enrollments happen at admission
curl -X POST http://127.0.0.1:5000/api/v1/student/enroll-face \
  -H "Content-Type: application/json" \
  -d '{"student_id": 2024001, "name": "Ayesha Khan", "face_image": "<base64>"}'

# 2. bind the client-generated deviceId (Keychain/Keystore) - first binding only
curl -X POST http://127.0.0.1:5000/api/v1/student/register-device \
  -H "Content-Type: application/json" \
  -d '{"student_id": 2024001, "device_id": "<device uuid>"}'

# 3. verify identity -> 30-second verification token
curl -X POST http://127.0.0.1:5000/api/v1/attendance/verify-identity \
  -H "Content-Type: application/json" \
  -d '{"student_id": 2024001, "face_image": "<base64 capture>"}'

# 4. check in: verification token + device + scanned QR nonce -> on-chain lock
curl -X POST http://127.0.0.1:5000/api/v1/attendance/scan \
  -H "Content-Type: application/json" \
  -d '{"verification_token": "<jwt>", "device_id": "<device uuid>",
       "session_id": 1, "nonce": "<nonce scanned from the QR>"}'
```

`verify-identity` also accepts `{"student_id", "device_id",
"webauthn_assertion": {...}}` for the fingerprint path - a simplified stub
where the on-device biometric prompt is the real gate (the full WebAuthn
ceremony is on the roadmap). Face matching runs through a pluggable
embedding provider: `FACE_EMBEDDING_PROVIDER=auto` picks the
`face_recognition` library (dlib 128-d encodings) when installed, and falls
back to a built-in demo embedder (mean-centered 32x32 grayscale, cosine
similarity) so the flow works on any Python version. Stored and submitted
embeddings must come from the same provider - otherwise the student is told
to re-enroll (`embedding_provider_mismatch`). The default
`FACE_MATCH_THRESHOLD` (0.85) suits the demo embedder; use ~0.6 with
face_recognition encodings.

`/attendance/scan` validates in a fixed order, and every failure response
carries `check` + `error` codes naming exactly which gate rejected the scan
(for demo debugging, not for end users):

| Order | Check (`check`) | Rejection codes (`error`) |
|---|---|---|
| a | `verification_token` | `verification_token_invalid`, `verification_token_expired`, `verification_token_wrong_purpose` |
| b | `session_nonce` | `session_not_found`, `session_inactive`, `nonce_mismatch`, `nonce_expired` |
| c | `device_match` | `device_not_bound`, `device_mismatch` |
| d | `onchain_lock` | `attendance_already_locked`, `blockchain_unavailable`, `blockchain_not_configured`, `blockchain_error` |

When all four checks pass, the backend signs
`AttendanceLedger.lockAttendance()` with its signer account
(`BACKEND_SIGNER_PRIVATE_KEY`, which must hold `BACKEND_ROLE`; local dev
defaults to Hardhat account #0) and returns the transaction hash, block
number, and lock timestamp. A second scan for the same student+session
fails check (d) with `attendance_already_locked` - the on-chain "no
resubmission" rule.

A device binds to a student exactly once: `register-device` rejects a
different device with `device_already_bound` (409) and points to the admin
override, `POST /api/v1/admin/rebind-device`, which force-replaces the
binding for lost or replaced phones (stub - no admin auth until the JWT
role layer lands).

**Demo caveats (placeholder rails by design):** the demo embedder is
deterministic image comparison, not biometric-grade recognition; the
WebAuthn path is a stub; `rebind-device` is unauthenticated; and the
default signer key is the public Hardhat test key.

## 3. Frontend (`frontend/`)

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Routes: `/login`, `/student`, `/instructor`, `/hod`, `/admin`
(`/` and unknown paths redirect to `/login`). The Vite dev server proxies
`/api/*` to the Flask backend on port 5000, so no CORS setup is needed in dev.

## Ports

| Service | URL | Started by |
|---|---|---|
| Hardhat JSON-RPC | http://127.0.0.1:8545 | `npm run node` (contracts) |
| Flask API | http://127.0.0.1:5000 | `python run.py` (backend) |
| Vite dev server | http://localhost:5173 | `npm run dev` (frontend) |
| IPFS API / Gateway | http://127.0.0.1:5001 / :8080 | `ipfs daemon` (optional) |

## Demo accounts (Hardhat test accounts, local-only)

Roles are seeded by `npm run seed:local`. These are the well-known public
Hardhat development keys - **never use them for anything but local dev**.

`npm run seed:accounts` (in `contracts/`) prints each account's address and
private key to the console and refreshes `contracts/deployed/accounts.json`
with the labeled addresses (never the private keys) for the backend to read.

| # | Role | Address |
|---|---|---|
| 0 | Admin (contract deployer) | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` |
| 1 | Instructor | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` |
| 2 | HOD | `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC` |
| 3 | Exam Controller | `0x90F79bf6EB2c4f870365E785982E1f101E93b906` |
| 4-6 | Students (1-3) | `0x15d3...6A65`, `0x9965...A4dc`, `0x976E...0aa9` |

## Environment variables (`backend/.env`)

Copied from [`backend/.env.example`](backend/.env.example):

| Variable | Purpose | Default |
|---|---|---|
| `FLASK_CONFIG` | Config profile: `development` / `testing` / `production` | `development` |
| `SECRET_KEY` | Flask session signing key | `change-me` |
| `PORT` | Backend port for `python run.py` | `5000` |
| `DB_URL` | Database URL (SQLite default, PostgreSQL later) | `sqlite:///eduledger.db` |
| `NONCE_ROTATION_SECONDS` | QR nonce rotation interval for live sessions | `5` |
| `SCHEDULER_ENABLED` | Run the background nonce-rotation job | `true` |
| `HARDHAT_RPC_URL` | Local chain JSON-RPC endpoint | `http://127.0.0.1:8545` |
| `CHAIN_ID` | Expected chain id | `31337` |
| `ROLE_REGISTRY_CONTRACT_ADDRESS` | Deployed `EduLedgerRoles` address | printed by `deploy:local` |
| `ATTENDANCE_ENGINE_CONTRACT_ADDRESS` | Module 1 `AttendanceLedger` address | printed by `deploy:attendance` |
| `AUDIT_TRAIL_CONTRACT_ADDRESS` | Module 2 `RecordAuditTrail` address | printed by `deploy:audit-trail` |
| `WORKLOAD_LEDGER_CONTRACT_ADDRESS` | Module 3 contract (not built yet) | - |
| `FACE_MATCH_THRESHOLD` | Cosine similarity threshold for a face match | `0.85` |
| `VERIFICATION_TOKEN_SECONDS` | Verification token lifetime (seconds) | `30` |
| `FACE_EMBEDDING_PROVIDER` | Face embedding backend: `auto` / `demo` / `face_recognition` | `auto` |
| `BACKEND_SIGNER_PRIVATE_KEY` | Transaction signer for `lockAttendance()` (needs `BACKEND_ROLE`) | Hardhat #0 (local only) |
| `ATTENDANCE_LEDGER_ARTIFACT` | Optional explicit path to `deployed/AttendanceLedger.json` | repo default |
| `IPFS_API_URL` | Local IPFS node API | `http://127.0.0.1:5001` |
| `IPFS_GATEWAY_URL` | Gateway for viewing pinned evidence | `http://127.0.0.1:8080` |
| `JWT_SECRET` | Signing key for API tokens (>= 32 bytes) | dev default - change it |
| `JWT_EXPIRES_HOURS` | Login token lifetime (auth layer, roadmap) | `12` |
| `CORS_ORIGINS` | Comma-separated allowed browser origins | `http://localhost:5173` |

## Troubleshooting

- **`npm run deploy:local` fails with a connection error** - the Hardhat node
  (terminal A) is not running. Start it, then deploy and seed again.
- **Port already in use** - stop the previous process, or set `PORT` (backend)
  / change `server.port` in `frontend/vite.config.js`.
- **MetaMask connection** - add a custom network `http://127.0.0.1:8545`,
  chain id `31337`, and import the demo accounts listed above.
- **`scripts/deployments/` is gitignored** - it is regenerated by the deploy
  script on every machine; the backend only needs the values in `.env`.

## Roadmap

1. Deploy the remaining MVP module contract (workload ledger) following the
   `RecordAuditTrail` pattern (own script + `deployed/*.json` export).
2. Wire JWT auth + the remaining database models in the Flask app (login,
   role enforcement on `/session/start` and the admin rebind stub,
   audit-trail proposals).
3. Build the module UIs behind the existing role routes.
4. See `docs/PRD.md` section 8 for the documented Future Scope modules.
