# Product Requirements Document (PRD) v2
## EduLedger — Hybrid Blockchain & AI-Powered Decentralized Smart LMS

**Supersedes:** EduLedger_PRD.md (v1, MVP scope)
**Status:** v1's three MVP modules (Zero-Proxy Attendance, Multi-Sig Audit Trail, Faculty Workload/Substitute) are **built, tested, and verified** against the original PRD. This v2 re-scopes the project against a new, authoritative requirements document ("SYSTEM OPERATIONAL WORKFLOW DOCUMENTATION") that the user's institution/supervisor has specified as a **hard requirement**. This document explains what changes, what survives, and the new target architecture.

---

## 1. What Changed and Why

The original PRD described a focused 3-module MVP with hardcoded demo accounts and no real authentication. The new requirements document describes a complete institutional system: real user registration and role governance, a timetable-driven scheduling engine, real-time delivery, a native mobile app with hardware-backed biometrics, and two entirely new modules (Double-Blind Grading, Anonymous Whistleblower).

**This is a hard requirement, not an optional enhancement** — the system must match it closely. This PRD treats the new document as the source of truth for *what* the system must do, while retaining engineering judgment (same as v1) over *how* each piece is built, substituting free/local/achievable tooling where the literal spec would require infrastructure or cryptographic depth beyond what's buildable by a solo developer in a reasonable timeframe — each such substitution is called out explicitly, never silently.

### What survives from v1 unchanged in spirit
- The on-chain enforcement philosophy (smart contracts as the final authority, not a trusted admin) carries through every module.
- The Zero-Proxy Attendance QR/nonce mechanism is the right shape — it gets extended, not replaced.
- The Multi-Sig Audit Trail propose→approve→execute pattern is the right shape for the new Section 5 (Grade Alteration) — it gets narrowed/relabeled, not rebuilt.
- The Substitute Teacher flow (Module 3 in v1) already matches the new Section 7 almost exactly and is the strongest point of alignment in the whole codebase.
- The local-infrastructure philosophy (Hardhat local chain, local IPFS node, local Postgres — all free, all under the developer's control) continues to apply to every new module.

### What's genuinely new
- A real authentication and role-governance system (VC → HOD → Faculty → Student), which nothing in v1 had.
- A timetable ingestion and session time-gating engine.
- Real-time delivery (WebSockets) replacing polling for the live teacher dashboard.
- A second client application: a cross-platform mobile app (Expo/React Native) for students, with real hardware-backed biometrics — replacing the browser-based WebAuthn/server-side face comparison approach from v1.
- Two new modules: Anonymous Assignment + Double-Blind Grading, and Anonymous Whistleblower Complaints.

---

## 2. Target Architecture

```
                         ┌─────────────────────────┐
                         │   Expo App (Students)    │  iOS / Android / Web
                         │  React Native + Expo     │  (installable, one codebase)
                         └────────────┬─────────────┘
                                      │ REST + WebSocket
                         ┌────────────┴─────────────┐
                         │  React Web Portal          │  Faculty / HOD / VC
                         │  (existing codebase,        │  (migrates to Expo later,
                         │   extended)                  │   same API, no backend change)
                         └────────────┬─────────────┘
                                      │
                    ┌─────────────────┴──────────────────┐
                    │        Flask REST API + WebSocket    │
                    │   (Flask-SocketIO added to existing)  │
                    └───┬──────────┬───────────┬──────────┘
                        │          │           │
                 ┌──────┴───┐ ┌───┴────┐ ┌────┴─────────┐
                 │PostgreSQL│ │  Redis   │ │ Local Hardhat │
                 │ (existing)│ │(new — for│ │ chain (existing)│
                 │ + new     │ │ session/  │ │ + new contracts │
                 │ auth,     │ │ pub-sub   │ └──────────────┘
                 │ timetable │ │ backing   │
                 │ tables    │ │ WebSocket)│      ┌──────────────┐
                 └──────────┘ └──────────┘       │ Local IPFS     │
                                                   │ node (existing)│
                                                   └──────────────┘
```

**Key decision — Redis gets activated.** v1's cleanup pass found `REDIS_URL` configured but completely unused, and removed it as dead config. It comes back here for real: Flask-SocketIO's message queue (for multi-worker WebSocket broadcast) is the natural, free, local use for it — same tool, now with a genuine job.

---

## 3. User Roles & Governance Chain

| Role | Onboarding path | Governs |
|---|---|---|
| **VC (Vice-Chancellor) / System Admin** | Initializes system root via web portal; locks the root administrative address on a master smart contract | Node management, institutional overrides, top-level governance |
| **HOD (Head of Department)** | Registers via web portal with institutional credentials (Employee ID, Department, Name, Email); auto-verified against an institutional whitelist | Timetable upload, grade-alteration approval, department oversight |
| **Faculty** | Registers via web portal (Employee ID, Name, Department, Email, password) | Starts attendance sessions, grades assignments, proposes grade changes, issues substitute keys |
| **Student** | Registers via Expo mobile app (Full Name, Roll Number, Batch, Department, institutional email, password) | Marks attendance, submits assignments, flags complaints |

**Email domain validation** (`@gcu.edu.pk` per the doc — adjust to the real institutional domain) and **unique ID checks** are enforced server-side at registration for Faculty and Student. Passwords are hashed with **Argon2id** before storage — replacing any placeholder auth assumptions from v1 (which had none).

---

## 4. Module Specifications (New Scope)

### 4.0 Foundation — Auth, Onboarding & Timetable Engine *(new, blocks all other modules)*

- VC root initialization (one-time, locks a master contract's admin address).
- HOD registration + auto-verification against a seeded institutional whitelist table.
- Faculty/Student registration with domain validation, uniqueness checks, Argon2id hashing.
- JWT-based session auth; every existing and new endpoint requires a valid token and enforces role.
- Timetable upload (spreadsheet) → parsed into `course_code`, `faculty_id`, `room`, `day`, `start_time`, `end_time` → replaces v1's hand-seeded `ScheduledClass` rows with real ingested data.
- **Session time-gating**: a session cannot be started before its scheduled time (server-side check against the timetable, not just a UI restriction).

### 4.1 Module: Zero-Proxy Attendance (retrofit of v1 Module 1)

Carries forward: QR + nonce mechanism, on-chain lock, duplicate-scan rejection — all proven working in v1.

**New in this version:**
- QR rotation window changes to **10–15 seconds** (v1 used 5s — adjust the existing nonce rotation config, not the underlying mechanism).
- A synchronized **rolling numeric code** displayed alongside the QR, for projector failure / camera-focus fallback (teacher reads it aloud; student types it in).
- **WebSocket live push** to the teacher's podium screen (student name, roll number, check-in timestamp) — replacing/supplementing v1's polling-based "live count" gap that was explicitly flagged as missing in the MVP.
- **2-step check reordered**: biometric first (via Expo app's hardware-backed Face/Fingerprint), *then* QR scan/manual code entry — matching the doc's explicit ordering (v1 did identity verification before scan too, so the sequence is preserved; what changes is *where* the biometric match happens — on-device hardware enclave via Expo, not a server-side similarity comparison).
- **Monthly report export** (Excel/CSV) — new endpoint, straightforward given existing `AttendanceRecord`-equivalent data.

### 4.2 Module: Anonymous Assignment + Double-Blind Grading *(new)*

- Student uploads a PDF via the Expo app.
- Backend strips identifying metadata and pins the anonymized file to IPFS (reuses the working IPFS integration from v1 Phase 3.1 directly — same `upload_to_ipfs` service, new call site).
- A secure random **Anonymous ID** (e.g., `ANON-ASG-5021`) is generated and mapped to the real student in an **encrypted mapping table** (not plaintext — this is a new data-protection requirement, worth a short dedicated design pass before building).
- Faculty grading portal shows only Anonymous IDs; grades submitted against the anonymous identity.
- On submission, the backend unmasks via the mapping table, writes the real grade record, and pushes a notification to the student's app.

### 4.3 Module: Post-Deadline Grade Alteration (retrofit/narrowing of v1 Module 2)

v1's Multi-Sig Audit Trail already implements propose → approve → execute → immutable log. This module is the same contract and backend pattern, narrowed and relabeled:

- Scoped specifically to grade corrections (student ID, old grade, new grade, mandatory justification).
- **Database row lock** on the specific grade during the pending-approval window (new — prevents a second conflicting edit from landing while a change is pending; v1's proposals didn't need this since they weren't scoped to a single mutable row other systems might also touch).
- Approval chain is **HOD only** for this module (the doc describes single-approver HOD sign-off, not the 2-of-3 pattern from v1's general RecordAuditTrail — confirm during build whether to keep the 2-of-3 contract and just always route "signature 2" to HOD, or deploy a simplified 1-approver variant for this specific grade-alteration path; recommend the former, since it reuses the already-tested contract unchanged).

### 4.4 Module: Anonymous Whistleblower Complaint *(new — rigor TBD)*

- Student submits a grievance (text + optional evidence) through the app.
- System must cryptographically prove the sender is a validly enrolled student **without revealing identity**.
- Anti-spam: repeated reports flagged/proven false by authorized personnel can trigger an account block.

**Rigor decision deferred to build time, per your request.** Two viable paths, to be decided when this module is actually reached:
1. **Full ZK-SNARK circuit** (Circom + SnarkJS) — cryptographically the "real" answer, consistent with the doc's literal wording and v1's original Future Scope plan, but materially harder to build correctly and more time-risk.
2. **Lighter cryptographic equivalent** (e.g., a Merkle-tree membership proof with a one-time nullifier, or a ring-signature scheme) — still genuinely proves "enrolled student, unknown which one" without a full ZK circuit's complexity, lower implementation risk, still defensible as real cryptography (not a mocked substitute) in a viva/defense setting.

Recommendation when we reach it will depend on how much time remains and how deep the defense is expected to probe this specific module.

### 4.5 Module: Substitute Teacher Delegation (v1 Module 3 — closest match, minimal retrofit)

v1's substitute key issue/redeem/credit-routing flow already matches Section 7 almost exactly: generate a temporary key → colleague redeems it → attendance/workload routes to the **original** assigned teacher's ledger. The only retrofit needed is wiring it to the new real-auth system (replacing the mock professor-ID dropdown with real logged-in faculty accounts) and the new timetable engine (replacing the hand-seeded `ScheduledClass` rows).

---

## 5. Client Applications

### 5.1 Expo App (Students) — new
- React Native + Expo, compiling to iOS, Android, and web from one codebase.
- `expo-local-authentication` for real hardware-backed Face ID / Android biometric prompts (satisfies "hardware security enclave" literally, which a browser cannot).
- `expo-camera` for QR scanning.
- Talks to the same Flask REST API + WebSocket as the web portal — no backend duplication.

### 5.2 Web Portal (Faculty / HOD / VC) — existing, extended
- Remains the current React + Tailwind web app, extended with the new auth flows, timetable upload UI, grading portal, and audit views.
- **Migration path to Expo is deferred, not blocked**: because both are just API clients, HOD/Faculty/VC can move to Expo later with no backend changes — this is an explicit, low-risk future step once the student-facing core is solid.

### 5.3 Desktop
- Deferred until the Expo web build is stable; then wrapped via Tauri or Electron as a thin shell — not a separate development effort.

---

## 6. Tooling Substitutions (free/local, same philosophy as v1)

| Requirement (literal) | What's used | Why |
|---|---|---|
| "Master smart contract locks root address" | A deployed `GovernanceRegistry.sol` on the existing local Hardhat chain, with a single admin-lock function | Same local-chain philosophy as every other contract in this project |
| Institutional whitelist cross-reference | A seeded Postgres table acting as the whitelist, checked at HOD registration | No real university SSO/directory exists to integrate with; a seeded table is a faithful stand-in |
| Real-time WebSocket bridge | Flask-SocketIO + Redis (local) as the message queue | Free, local, well-supported in Flask; Redis is already configured in the project (currently unused — this activates it) |
| Hardware secure enclave biometrics | Expo `expo-local-authentication` (real device API) | This is not a substitution — Expo genuinely reaches the hardware API a browser cannot |
| ZK membership proof (Whistleblower) | TBD — full Circom circuit or a lighter equivalent scheme | Decision deferred to build time per discussion above |

---

## 7. Build Sequencing (high level — detailed in the Build Guide)

1. **Stage 0 — Auth & Timetable Foundation** *(blocks everything else — build guide below covers this in full prompt detail)*
2. **Stage 1 — Retrofit existing modules**: Attendance (rolling code, WebSocket, export, Expo biometric integration), Grade Alteration (narrow/relabel v1's Audit Trail), Substitute Teacher (wire to real auth + timetable)
3. **Stage 2 — New modules**: Anonymous Assignment + Double-Blind Grading, then Whistleblower (rigor decided at that point)
4. **Stage 3 — Polish, demo rehearsal, cold-start verification** (same discipline as v1's Phase 5 — `DEMO_RESET.md` and `smoke_test_full.py` get extended, not replaced, to cover the new modules)

---

## 8. Assumptions & Open Items

- The institutional email domain and whitelist source are placeholders (`@gcu.edu.pk`, a seeded table) pending real institutional data, exactly as v1 treated test accounts as placeholders for a real student body.
- The "master smart contract" governance language is interpreted as a genuine, if simplified, on-chain admin-lock contract — not just a backend flag — to stay consistent with the project's whole on-chain-enforcement philosophy.
- Desktop and Expo-ification of the staff portal are explicitly sequenced *after* the core system is solid, per your instruction — not dropped, just deferred.
- The Whistleblower module's cryptographic rigor is an open decision, to be made with full context when that module is actually reached.
