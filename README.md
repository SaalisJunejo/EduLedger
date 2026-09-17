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
> attendance session flow (rotating QR nonces) is live. Contracts, API, and
> UI run end to end; the module logic is wired up incrementally.

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
| `HARDHAT_RPC_URL` | Local chain JSON-RPC endpoint | `http://127.0.0.1:8545` |
| `CHAIN_ID` | Expected chain id | `31337` |
| `ROLE_REGISTRY_CONTRACT_ADDRESS` | Deployed `EduLedgerRoles` address | printed by `deploy:local` |
| `ATTENDANCE_ENGINE_CONTRACT_ADDRESS` | Module 1 contract (not deployed yet) | - |
| `AUDIT_TRAIL_CONTRACT_ADDRESS` | Module 2 contract (not deployed yet) | - |
| `WORKLOAD_LEDGER_CONTRACT_ADDRESS` | Module 3 contract (not deployed yet) | - |
| `IPFS_API_URL` | Local IPFS node API | `http://127.0.0.1:5001` |
| `IPFS_GATEWAY_URL` | Gateway for viewing pinned evidence | `http://127.0.0.1:8080` |
| `JWT_SECRET` | Signing key for API tokens | `change-me` |
| `JWT_EXPIRES_HOURS` | Token lifetime | `12` |
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
2. Wire JWT auth + the remaining database models in the Flask app
   (student check-in against the rotating nonce, audit-trail proposals).
3. Build the module UIs behind the existing role routes.
4. See `docs/PRD.md` section 8 for the documented Future Scope modules.
