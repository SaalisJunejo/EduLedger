"""On-chain AttendanceLedger client (Module 1 - Zero-Proxy Attendance Engine).

Reads the artifact written by `npm run deploy:attendance`
(`contracts/deployed/AttendanceLedger.json`); the address can be overridden
via ATTENDANCE_ENGINE_CONTRACT_ADDRESS. Transactions are signed by the
backend signer (BACKEND_SIGNER_PRIVATE_KEY, which must hold BACKEND_ROLE on
the contract - the local demo uses Hardhat account #0, the deployer).
"""

import json
from pathlib import Path

from flask import current_app
from web3 import Web3

# <repo>/contracts/deployed/AttendanceLedger.json
# (this file is backend/app/services/blockchain.py -> parents[3] = repo root)
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "deployed" / "AttendanceLedger.json"


class BlockchainError(Exception):
    """On-chain call failure with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _artifact_path() -> Path:
    configured = current_app.config.get("ATTENDANCE_LEDGER_ARTIFACT")
    return Path(configured) if configured else DEFAULT_ARTIFACT


def _load_ledger():
    """Return ``(w3, contract)`` for AttendanceLedger or raise BlockchainError."""
    rpc = current_app.config["HARDHAT_RPC_URL"]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
    try:
        connected = w3.is_connected()
    except Exception as exc:  # network-level failure (unreachable host, proxy, ...)
        raise BlockchainError(
            "blockchain_unavailable", f"cannot reach the blockchain node at {rpc}: {exc}"
        ) from exc
    if not connected:
        raise BlockchainError(
            "blockchain_unavailable",
            f"blockchain node at {rpc} is not reachable (is `npm run node` running?)",
        )

    artifact_path = _artifact_path()
    address = current_app.config["CONTRACT_ADDRESSES"].get("attendance_engine") or ""
    abi = None
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        abi = artifact.get("abi")
        address = address or artifact.get("address", "")
    if not address:
        raise BlockchainError(
            "blockchain_not_configured",
            "AttendanceLedger address unknown - run `npm run deploy:attendance` "
            "or set ATTENDANCE_ENGINE_CONTRACT_ADDRESS",
        )
    if not abi:
        raise BlockchainError(
            "blockchain_not_configured",
            f"AttendanceLedger ABI not found at {artifact_path}",
        )

    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
    return w3, contract


def is_locked(student_id: int, session_id: int) -> bool:
    """Read AttendanceLedger.isLocked() - the on-chain 'no resubmission' check."""
    _, contract = _load_ledger()
    try:
        return bool(contract.functions.isLocked(student_id, session_id).call())
    except BlockchainError:
        raise
    except Exception as exc:
        raise BlockchainError("blockchain_unavailable", f"isLocked() call failed: {exc}") from exc


def lock_attendance(student_id: int, session_id: int) -> dict:
    """Anchor attendance on-chain from the backend signer.

    Returns the transaction hash, block number, and the block timestamp of
    the lock. Raises BlockchainError on any failure (including an
    AttendanceAlreadyLocked revert from a race with the pre-check).
    """
    w3, contract = _load_ledger()
    private_key = current_app.config["BACKEND_SIGNER_PRIVATE_KEY"]
    signer = w3.eth.account.from_key(private_key)

    try:
        gas = contract.functions.lockAttendance(student_id, session_id).estimate_gas(
            {"from": signer.address}
        )
        transaction = contract.functions.lockAttendance(student_id, session_id).build_transaction(
            {
                "from": signer.address,
                "nonce": w3.eth.get_transaction_count(signer.address),
                "chainId": current_app.config["CHAIN_ID"],
                "gas": int(gas * 1.3) + 21_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = w3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    except BlockchainError:
        raise
    except Exception as exc:
        text = f"{exc} {getattr(exc, 'name', '')}"
        if "AttendanceAlreadyLocked" in text:
            raise BlockchainError(
                "attendance_already_locked",
                "attendance for this student+session is already locked on-chain",
            ) from exc
        raise BlockchainError(
            "blockchain_error", f"lockAttendance() transaction failed: {exc}"
        ) from exc

    if receipt.status != 1:
        raise BlockchainError(
            "blockchain_error",
            "lockAttendance() transaction reverted on-chain "
            "(possible duplicate lock or missing BACKEND_ROLE)",
        )

    block = w3.eth.get_block(receipt.blockNumber)
    return {
        # hexbytes >= 2.0 drops the 0x prefix from .hex(); keep the canonical form.
        "transaction_hash": Web3.to_hex(receipt.transactionHash),
        "block_number": receipt.blockNumber,
        "locked_at": int(block.timestamp),
        "signer": signer.address,
    }
