"""Domain services (business logic shared by the API blueprints).

Modules:
- `face_embedding`     - face embedding + cosine comparison (pluggable provider)
- `verification_token` - short-lived attendance verification JWTs
- `webauthn`           - simplified fingerprint assertion check (stub)
- `blockchain`         - AttendanceLedger on-chain client (web3)
- `audit_trail`        - RecordAuditTrail on-chain client (web3, role signers)
- `workload`           - WorkloadLedger on-chain client (web3, backend signer)
- `substitute_token`   - short-lived substitute authorization JWTs
- `ipfs`               - evidence upload to IPFS (local daemon + Web3.Storage)
"""
