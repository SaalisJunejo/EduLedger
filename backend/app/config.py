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

    # -- Database ----------------------------------------------------------
    DB_URL = os.getenv("DB_URL", "sqlite:///eduledger.db")

    # -- Attendance sessions -------------------------------------------------
    # How often the background job rotates the QR nonce of active sessions.
    NONCE_ROTATION_SECONDS = int(os.getenv("NONCE_ROTATION_SECONDS", "5"))
    # Set to false to skip starting the background scheduler (tests, scripts).
    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").strip().lower() in {"1", "true", "yes"}

    # -- Blockchain (local Hardhat network) --------------------------------
    HARDHAT_RPC_URL = os.getenv("HARDHAT_RPC_URL", "http://127.0.0.1:8545")
    CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))
    CONTRACT_ADDRESSES = {
        "role_registry": os.getenv("ROLE_REGISTRY_CONTRACT_ADDRESS", ""),
        "attendance_engine": os.getenv("ATTENDANCE_ENGINE_CONTRACT_ADDRESS", ""),
        "audit_trail": os.getenv("AUDIT_TRAIL_CONTRACT_ADDRESS", ""),
        "workload_ledger": os.getenv("WORKLOAD_LEDGER_CONTRACT_ADDRESS", ""),
    }

    # -- IPFS ----------------------------------------------------------------
    IPFS_API_URL = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001")
    IPFS_GATEWAY_URL = os.getenv("IPFS_GATEWAY_URL", "http://127.0.0.1:8080")

    # -- Auth -----------------------------------------------------------------
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-jwt-change-me")
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
