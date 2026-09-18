"""Domain services (business logic shared by the API blueprints).

Modules:
- `face_embedding`     - face embedding + cosine comparison (pluggable provider)
- `verification_token` - short-lived attendance verification JWTs
- `webauthn`           - simplified fingerprint assertion check (stub)
- `blockchain`         - AttendanceLedger on-chain client (web3)
"""
