# Product Requirements Document (PRD)
## EduLedger — Decentralized University Trust & Verification Platform (MVP)

**Version:** 1.0
**Prepared for:** AI build agents (Antigravity / Qoder)
**Scope:** MVP build (3 modules) + documented Future Scope (5 modules)
**Build constraint:** 1-day build sprint, full-featured (no functional compromise), using free/local decentralization tooling

---

## 1. Project Overview

**EduLedger** is a blockchain-backed university management platform that removes single points of human trust from core academic processes. Instead of relying on a clerk, admin, or professor to act honestly, the system enforces honesty structurally — through cryptographic verification, time-based automation, and multi-party on-chain approval — so no single actor can fake, tamper with, or bypass a record.

This PRD covers the **MVP build**, consisting of three modules selected from the full EduLedger vision:

1. **Zero-Proxy Attendance Engine**
2. **Multi-Sig Anti-Tamper Audit Trail**
3. **Faculty Workload Ledger**

All three modules are to be built **at full feature depth as originally specified** — no functionality is descoped. Free and local equivalents of paid/hosted decentralization infrastructure are used only to make a 1-day build feasible, not to reduce functionality (see Section 6, Tooling Substitutions).

Five additional modules from the broader EduLedger vision are documented in **Section 8 (Future Scope)** for context and to guide architecture decisions that keep the system extensible.

---

## 2. Goals & Objectives

- Demonstrate a working, end-to-end decentralized trust system across attendance, record integrity, and faculty accountability.
- Use real blockchain smart contracts, real cryptographic signature verification, and real decentralized file storage (IPFS) — not mocked substitutes.
- Ship a live, demoable product within a single build day using AI-assisted scaffolding (Antigravity/Qoder).
- Keep the codebase modular so Modules 4–8 (Future Scope) can be added later without re-architecting the core.

---

## 3. User Roles

| Role | Description |
|---|---|
| **Student** | Scans attendance QR codes, views their own records. |
| **Instructor** | Starts class sessions, proposes record changes, teaches per timetable. |
| **HOD (Head of Department)** | Approves/co-signs record change proposals. |
| **Exam Controller** | Third authorized signer for record changes (2-of-3 threshold). |
| **Admin** | Manages timetables, department accounts, monitors system-wide status. |

---

## 4. MVP Scope — Module Specifications

### 4.1 Module 1: Zero-Proxy Attendance Engine

**Purpose:** Prevent proxy attendance (students marking present for absent friends) using device-bound identity verification and a rapidly-expiring QR code.

**Functional Requirements:**

- **App-Assigned Device ID:** On first app setup, generate a unique device ID (UUID) and store it securely in the device's keychain/keystore. Bind this ID to the student's account on first registration.
- **Identity Verification (before scanner unlocks):**
  - Primary: Face recognition against the student's enrolled face profile.
  - Niqab/face-covering case: Use a periocular (eye-region) recognition model (lightweight CNN) that verifies identity from the eye/eyebrow region only.
  - Fallback: If face recognition fails (poor lighting, camera issue), prompt for fingerprint verification via WebAuthn.
- **Dynamic QR Nonce:** Instructor's screen/projector displays a QR code embedding a server-generated nonce (random one-time string + timestamp) that **refreshes every 5 seconds**.
- **Scan & Validate Flow:**
  1. Student opens app → identity verified (face/periocular/fingerprint) → scanner unlocks.
  2. Student scans the live projected QR.
  3. Backend validates: nonce not expired (<5s), device ID matches student's registered device, session not already locked for this student.
  4. On success: mark **PRESENT**, permanently **lock** that session for that student (no resubmission).
- **Device re-binding:** If a student reinstalls on a new phone, the new device generates a new ID; an admin approval step is required to re-bind the account before attendance can be submitted from the new device.

**Data Model (indicative):**
- `Student(id, name, deviceId, faceEmbedding, periocularEmbedding)`
- `Session(id, classId, startTime, nonceHistory[])`
- `AttendanceRecord(id, studentId, sessionId, status, timestampLocked)`

**API Endpoints (indicative):**
- `POST /api/v1/session/start` — instructor starts session, server begins nonce rotation
- `GET /api/v1/session/:id/nonce` — current active nonce (polled by projector display)
- `POST /api/v1/attendance/verify-identity` — face/periocular/fingerprint check
- `POST /api/v1/attendance/scan` — submit scanned nonce + device ID for validation

---

### 4.2 Module 2: Multi-Sig Anti-Tamper Audit Trail

**Purpose:** Prevent unilateral tampering of grade/attendance records by requiring 2-of-3 cryptographic sign-off from authorized officials, with all supporting evidence permanently pinned to IPFS.

**Functional Requirements:**

- **Proposal Creation:** Instructor submits a record modification proposal (e.g., "Change Student #102 Attendance from 70% → 75%"), attaching justification evidence (e.g., medical certificate PDF).
- **Evidence Pinning:** Justification document is uploaded to IPFS; the returned Content Identifier (CID) is attached to the on-chain proposal.
- **Multi-Sig Collection (2-of-3):**
  - Authorized signer roles: Instructor (initiator, auto-signs on submission), HOD, Exam Controller.
  - Smart contract enforces `requiredSignatures = 2` out of the 3 authorized addresses.
- **Execution:** Once 2 valid signatures are recorded, the smart contract automatically executes the state change and emits an immutable event: `RecordUpdated(studentId, oldValue, newValue, timestamp, ipfsHash)`.
- **Immutability guarantee:** Any direct database edit that bypasses the smart contract must be detectable — the application layer compares the SQL record against the on-chain ledger state and flags a mismatch if they diverge.

**Data Model (indicative):**
- `Proposal(id, studentId, field, oldValue, newValue, ipfsCid, signatures[], status)`
- `Signature(proposalId, signerAddress, signerRole, timestamp)`

**Smart Contract (indicative functions):**
- `proposeChange(studentId, field, oldValue, newValue, ipfsCid)`
- `approve(proposalId)` — callable only by registered HOD/Exam Controller addresses
- Auto-executes and emits `RecordUpdated` event once 2 signatures are collected

**API Endpoints (indicative):**
- `POST /api/v1/records/propose`
- `POST /api/v1/records/:id/approve`
- `GET /api/v1/records/:id/status`
- `POST /api/v1/ipfs/upload` — uploads evidence file, returns CID

---

### 4.3 Module 3: Faculty Workload Ledger

**Purpose:** Automatically verify whether scheduled lectures were actually conducted, and correctly credit workload — including substitute teaching — without manual paper logs.

**Functional Requirements:**

- **Schedule Ingestion:** Timetable data (class, room, professor, start time) pre-loaded into the system.
- **Session Launch:** Professor connects to the classroom projector and clicks "Start Session." A dynamic QR code (refreshing every 5 seconds, same mechanism as Module 1) is projected.
- **Student Scan Validation:** Any student scan of the projected QR confirms the session is live in that room.
- **15-Minute Automated Countdown:** A background scheduled job monitors each timetable slot. If 15 minutes pass from the scheduled start time with zero valid scans and no session start, the system auto-flags the class as **UNCONDUCTED** and alerts admin.
- **Substitute Transfer:** If a substitute professor covers a class, the original professor issues a short-lived, digitally signed "Substitute Key" token; the substitute redeems it to start the session, and workload credit is logged to the substitute instead.
- **Zero-Attendance Edge Case:** If a professor starts the session correctly (verified via projector connection/university Wi-Fi) but no students scan, the professor can manually declare "Zero Attendance," which is still logged as CONDUCTED with full workload credit.
- **On-Chain Logging:** Final CONDUCTED / UNCONDUCTED / SUBSTITUTE workload entries are logged immutably (same ledger pattern as Module 2) for auditability.

**Data Model (indicative):**
- `ScheduledClass(id, professorId, room, startTime, courseId)`
- `SessionLog(id, classId, status[CONDUCTED|UNCONDUCTED|CONDUCTED_ZERO_STUDENTS], conductedBy, timestamp)`
- `SubstituteToken(id, issuedBy, redeemedBy, expiresAt)`

**API Endpoints (indicative):**
- `POST /api/v1/faculty/session/start`
- `POST /api/v1/faculty/session/scan`
- `POST /api/v1/faculty/substitute/issue`
- `POST /api/v1/faculty/substitute/redeem`
- Background job: `checkUnconductedSessions()` — runs every minute

---

## 5. High-Level System Architecture

```
 [Mobile/Web Client] ──► [Flask REST API] ──► [PostgreSQL/SQL DB]
        │                       │
        │                       ├──► [Smart Contracts on Local Blockchain (Hardhat)]
        │                       │
        │                       ├──► [IPFS Node / Free Pinning Service]
        │                       │
        │                       └──► [Background Job Runner (Celery + Redis)]
```

- **Client:** Handles QR scanning, face/fingerprint capture, session start UI, proposal/approval dashboards.
- **Backend (Flask):** Orchestrates identity verification, talks to the smart contract via a Web3 library (ethers.js/web3.py), manages IPFS uploads, and schedules background checks.
- **Blockchain layer:** Enforces the rules that must never be bypassable by a human (multi-sig thresholds, nonce/device checks logged immutably).
- **IPFS layer:** Stores evidence documents off-chain; only the content hash (CID) is stored on-chain.
- **Background jobs:** Handle time-based automation (nonce rotation, 15-minute uncounted flag).

---

## 6. Tech Stack & Free Tooling Substitutions

All decentralization technologies specified in the original project vision are used in full — **no blockchain, IPFS, or cryptographic functionality is cut**. To make a 1-day build achievable, the following **free/local equivalents of production infrastructure** are used instead of paid hosted services. This is an infrastructure choice, not a feature reduction — the same smart contracts and IPFS logic would run unchanged against a public network later.

| Layer | Technology | Purpose | Free/Local Substitution Used |
|---|---|---|---|
| Blockchain network | Solidity smart contracts | Enforce multi-sig, immutable logging | **Hardhat local blockchain** (instead of a public testnet) — free, instant transactions, pre-funded test accounts |
| Contract libraries | OpenZeppelin | Audited multi-sig/access-control patterns | Free, open-source npm packages |
| Web3 connectivity | ethers.js | Connect backend/frontend to smart contracts | Free |
| Decentralized storage | IPFS | Store evidence files, return tamper-proof CID | **Local IPFS node** (`ipfs daemon`) or **Web3.Storage / Pinata free tier** |
| Backend | Flask (Python) | REST API, orchestration logic | Free |
| Background jobs | Celery + Redis | 5-second nonce rotation, 15-minute session monitor | Free, open-source, run locally |
| Frontend | React | Student, instructor, HOD, admin dashboards | Free |
| Face recognition | Open-source model (e.g., face-api.js, MediaPipe, or a lightweight CNN) | Primary identity check | Free, runs client-side or on a lightweight local inference server |
| Periocular recognition | Compact CNN (CTCNN-style) trained/fine-tuned on eye-region data | Identity check for niqab-wearing students | Free open-source base models fine-tuned locally |
| Fingerprint fallback | WebAuthn | Device biometric fallback | Native OS API — free |
| Digital signatures | ECDSA (via wallet/private key signing) | Authenticate approvals | Free, built into Ethereum tooling |
| Database | PostgreSQL / SQLite | Student, session, and proposal metadata | Free |

**Note for build agent:** Deploy the smart contracts to the local Hardhat network at project start; seed 3–5 test accounts to represent Instructor, HOD, Exam Controller, and student roles for demo purposes. Store deployed contract addresses in a config file the backend reads on startup.

---

## 7. Non-Functional Requirements

- **Reliability during demo:** All blockchain and IPFS calls must run against local/free infrastructure to avoid network dependency failures during a live presentation.
- **Security:** Device IDs and biometric embeddings stored securely (encrypted at rest); private keys for demo accounts never exposed in client-side code.
- **Auditability:** Every state-changing action (attendance lock, record change, workload log) must be traceable to an on-chain transaction hash.
- **Modularity:** Each module's backend logic should be separable (own routes/services) so Future Scope modules can be added independently.
- **Performance:** QR nonce rotation and validation must complete within the 5-second window with margin for network latency.

---

## 8. Future Scope (Not in MVP)

The following modules are part of the full EduLedger vision and documented here to guide extensible architecture decisions, but are **explicitly out of scope for this build**:

| Module | Purpose (brief) |
|---|---|
| **"No Dues" Graduation Clearance Lock** | Cross-departmental event triggers automatically unlock digital degree issuance once Library, Accounts, Hostel, and Labs all confirm no outstanding dues. |
| **Double-Blind SLA Grading Engine** | Masks student identity from graders (via hashing) and enforces grading turnaround deadlines with automatic SLA-breach logging. |
| **Anonymous ZK Whistleblower Engine** | Lets students report corruption/exam leaks using zero-knowledge proofs to verify university membership without revealing identity. |
| **Zero-Knowledge Micro-Credential & Transcript Engine** | Lets students prove specific transcript facts (e.g., "CGPA ≥ 3.0") to employers via ZK range proofs, without revealing full transcripts. |
| **Multi-Party Treasury & Governance DAO** | Requires multi-stakeholder on-chain sign-off (e.g., HOD + Dean) before departmental funds can be disbursed, with a timelock safety delay. |

**Architecture guidance:** Keep the smart contract layer organized so each future module can be added as its own contract (or extension) rather than requiring changes to Modules 1–3's contracts. Keep the IPFS and multi-sig utilities (built for Module 2) generic enough to be reused by Modules 5, 6, 8 later, since they share the same underlying pattern.

---

## 9. Assumptions & Constraints

- Build is completed in a single day using AI-assisted development tools (Antigravity/Qoder) for scaffolding.
- All blockchain activity runs on a local Hardhat network for the MVP/demo; production deployment to a public network is a post-MVP concern.
- Demo devices (phones/laptops) are available and pre-configured with camera/fingerprint access for identity verification testing.
- Test accounts for Instructor, HOD, and Exam Controller roles are pre-seeded rather than requiring live onboarding during the demo.
- Face/periocular recognition models are pre-trained/open-source, not trained from scratch within the 1-day window.

---

## 10. Success Criteria (Demo-Readiness Checklist)

- [ ] Student can register a device ID and complete face (or periocular) verification.
- [ ] QR nonce visibly refreshes every 5 seconds on the instructor/projector view.
- [ ] A scan from an unregistered device or expired nonce is correctly rejected.
- [ ] A successful scan locks attendance and blocks a second submission.
- [ ] A record-change proposal can be created, requires 2-of-3 signatures, and executes on-chain once met.
- [ ] Evidence file uploaded to IPFS returns a valid CID, viewable/linkable from the proposal.
- [ ] A class with zero scans after 15 minutes auto-flags as UNCONDUCTED.
- [ ] A Substitute Key correctly transfers workload credit to a covering professor.
- [ ] All key actions (attendance lock, record change, workload log) show a corresponding blockchain transaction/event.

---

*End of PRD — ready for handoff to Antigravity/Qoder for scaffolding.*
