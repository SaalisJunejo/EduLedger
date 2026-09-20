"""Backend configuration profiles.

Every value is sourced from environment variables so the same code runs
locally and in deployment. `python-dotenv` loads `backend/.env` (create it
from `.env.example`) on import, keeping local setup to a single copy step.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def _env_list(name: str, default: str) -> list[str]:
    """Read a comma-separated env var into a trimmed list."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class BaseConfig:
    """Settings shared by every environment."""

    # -- Flask ------------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    # >= 32 bytes so HS256 JWTs meet the RFC 7518 recommendation.
    JWT_SECRET_DEFAULT = "dev-jwt-secret-please-change-me-32-chars-min"

    # -- Database ----------------------------------------------------------
    DB_URL = os.getenv("DB_URL", "sqlite:///eduledger.db")

    # -- Attendance sessions -------------------------------------------------
    # How often the background job rotates the QR nonce of active sessions.
    NONCE_ROTATION_SECONDS = int(os.getenv("NONCE_ROTATION_SECONDS", "5"))
    # Set to false to skip starting the background scheduler (tests, scripts).
    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").strip().lower() in {"1", "true", "yes"}

    # -- Faculty workload ledger (Module 3) ----------------------------------
    # Minutes after a ScheduledClass's startTime with no SessionLog before the
    # background job auto-flags the class UNCONDUCTED on-chain. The default
    # follows the PRD's 15-minute grace period; tests set it to 1 (or pass an
    # in-process override) so the auto-flag can be exercised without waiting.
    UNCONDUCTED_THRESHOLD_MINUTES = int(os.getenv("UNCONDUCTED_THRESHOLD_MINUTES", "15"))
    # Lifetime of substitute authorization tokens, in minutes.
    SUBSTITUTE_TOKEN_MINUTES = int(os.getenv("SUBSTITUTE_TOKEN_MINUTES", "30"))
    # Optional explicit path to contracts/deployed/WorkloadLedger.json
    # (default: <repo>/contracts/deployed/WorkloadLedger.json).
    WORKLOAD_LEDGER_ARTIFACT = os.getenv("WORKLOAD_LEDGER_ARTIFACT", "")

    # -- Identity verification & attendance scan (Module 1) -----------------
    # Cosine similarity above which a face match is accepted. 0.85 suits the
    # built-in demo embedder; use ~0.6 with face_recognition 128-d encodings.
    FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.85"))
    # Lifetime of the short-lived verification token issued by verify-identity.
    VERIFICATION_TOKEN_SECONDS = int(os.getenv("VERIFICATION_TOKEN_SECONDS", "30"))
    # Face embedding provider: "auto" (face_recognition when installed, else
    # the built-in demo embedder), "demo", or "face_recognition".
    FACE_EMBEDDING_PROVIDER = os.getenv("FACE_EMBEDDING_PROVIDER", "auto")

    # -- Blockchain signer ----------------------------------------------------
    # Signs AttendanceLedger transactions; must hold BACKEND_ROLE. Defaults to
    # Hardhat account #0 (the contract deployer) - its key is PUBLIC test
    # material, fine for the local demo, never for anything real.
    BACKEND_SIGNER_PRIVATE_KEY = os.getenv(
        "BACKEND_SIGNER_PRIVATE_KEY",
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    )
    # Optional explicit path to contracts/deployed/AttendanceLedger.json
    # (default: <repo>/contracts/deployed/AttendanceLedger.json).
    ATTENDANCE_LEDGER_ARTIFACT = os.getenv("ATTENDANCE_LEDGER_ARTIFACT", "")

    # -- Blockchain (local Hardhat network) --------------------------------
    HARDHAT_RPC_URL = os.getenv("HARDHAT_RPC_URL", "http://127.0.0.1:8545")
    CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))
    CONTRACT_ADDRESSES = {
        "role_registry": os.getenv("ROLE_REGISTRY_CONTRACT_ADDRESS", ""),
        "attendance_engine": os.getenv("ATTENDANCE_ENGINE_CONTRACT_ADDRESS", ""),
        "audit_trail": os.getenv("AUDIT_TRAIL_CONTRACT_ADDRESS", ""),
        "workload_ledger": os.getenv("WORKLOAD_LEDGER_CONTRACT_ADDRESS", ""),
    }

    # -- Audit trail signers (Module 2 - RecordAuditTrail) ------------------
    # Three role signers matching the labeled accounts in
    # contracts/deployed/accounts.json (instructor #1, HOD #2, exam
    # controller #3). Proposals are sent from the instructor signer and
    # approvals from the HOD / Exam Controller signer, so transactions are
    # attributable to a role. Defaults are the PUBLIC Hardhat test keys -
    # LOCAL TESTING ONLY, exactly like BACKEND_SIGNER_PRIVATE_KEY above.
    INSTRUCTOR_SIGNER_PRIVATE_KEY = os.getenv(
        "INSTRUCTOR_SIGNER_PRIVATE_KEY",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    )
    HOD_SIGNER_PRIVATE_KEY = os.getenv(
        "HOD_SIGNER_PRIVATE_KEY",
        "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    )
    EXAM_CONTROLLER_SIGNER_PRIVATE_KEY = os.getenv(
        "EXAM_CONTROLLER_SIGNER_PRIVATE_KEY",
        "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    )
    # Optional explicit path to contracts/deployed/RecordAuditTrail.json
    # (default: <repo>/contracts/deployed/RecordAuditTrail.json).
    AUDIT_TRAIL_ARTIFACT = os.getenv("AUDIT_TRAIL_ARTIFACT", "")

    # -- IPFS ----------------------------------------------------------------
    IPFS_API_URL = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001")
    IPFS_GATEWAY_URL = os.getenv("IPFS_GATEWAY_URL", "http://127.0.0.1:8080")
    # Free-tier pinning fallback when the local daemon is unreachable
    # (upload API bearer token from https://web3.storage).
    WEB3_STORAGE_TOKEN = os.getenv("WEB3_STORAGE_TOKEN", "")

    # -- Auth -----------------------------------------------------------------
    JWT_SECRET = os.getenv("JWT_SECRET", JWT_SECRET_DEFAULT)
    JWT_EXPIRES = timedelta(hours=int(os.getenv("JWT_EXPIRES_HOURS", "12")))

    # -- HTTP -----------------------------------------------------------------
    CORS_ORIGINS = _env_list("CORS_ORIGINS", "http://localhost:5173")


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SCHEDULER_ENABLED = False
    DB_URL = "sqlite:///:memory:"


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
