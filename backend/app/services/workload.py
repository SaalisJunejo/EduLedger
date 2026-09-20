"""On-chain WorkloadLedger client (Module 3 - Faculty Workload Ledger).

Reads the artifact written by `npm run deploy:workload`
(`contracts/deployed/WorkloadLedger.json`); the address can be overridden
via WORKLOAD_LEDGER_CONTRACT_ADDRESS. Transactions are signed by the
backend signer (BACKEND_SIGNER_PRIVATE_KEY, which must hold BACKEND_ROLE on
the contract - the same single-trusted-signer pattern as AttendanceLedger
in blockchain.py, not the multi-role signers of audit_trail.py).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from web3 import Web3

from ..models.workload import (
    STATUS_CONDUCTED,
    STATUS_CONDUCTED_ZERO_STUDENTS,
    STATUS_UNCONDUCTED,
)

# <repo>/contracts/deployed/WorkloadLedger.json
# (this file is backend/app/services/workload.py -> parents[3] = repo root)
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "deployed" / "WorkloadLedger.json"

# SessionLog status name -> WorkloadStatus enum index in WorkloadLedger.sol
# (declaration order: CONDUCTED, CONDUCTED_ZERO_STUDENTS, UNCONDUCTED).
STATUS_INDEX = {
    STATUS_CONDUCTED: 0,
    STATUS_CONDUCTED_ZERO_STUDENTS: 1,
    STATUS_UNCONDUCTED: 2,
}

# Enum index -> status name (the inverse view mapping, for reads).
INDEX_STATUS = {index: name for name, index in STATUS_INDEX.items()}


class WorkloadError(Exception):
    """On-chain call failure with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _artifact_path() -> Path:
    configured = current_app.config.get("WORKLOAD_LEDGER_ARTIFACT")
    return Path(configured) if configured else DEFAULT_ARTIFACT


def _load_ledger():
    """Return ``(w3, contract)`` for WorkloadLedger or raise WorkloadError."""
    rpc = current_app.config["HARDHAT_RPC_URL"]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
    try:
        connected = w3.is_connected()
    except Exception as exc:  # network-level failure (unreachable host, proxy, ...)
        raise WorkloadError(
            "blockchain_unavailable", f"cannot reach the blockchain node at {rpc}: {exc}"
        ) from exc
    if not connected:
        raise WorkloadError(
            "blockchain_unavailable",
            f"blockchain node at {rpc} is not reachable (is `npm run node` running?)",
        )

    artifact_path = _artifact_path()
    address = current_app.config["CONTRACT_ADDRESSES"].get("workload_ledger") or ""
    abi = None
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        abi = artifact.get("abi")
        address = address or artifact.get("address", "")
    if not address:
        raise WorkloadError(
            "workload_ledger_not_configured",
            "WorkloadLedger address unknown - run `npm run deploy:workload` "
            "or set WORKLOAD_LEDGER_CONTRACT_ADDRESS",
        )
    if not abi:
        raise WorkloadError(
            "workload_ledger_not_configured",
            f"WorkloadLedger ABI not found at {artifact_path}",
        )

    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
    return w3, contract


def log_workload(class_id: int, professor_id: int, status: str) -> dict:
    """Anchor a class's final workload status on-chain from the backend signer.

    Returns the transaction hash, block number, and the block timestamp of
    the entry. Raises WorkloadError on any failure (including a
    WorkloadAlreadyLogged revert from a race with the pre-check).
    """
    w3, contract = _load_ledger()
    private_key = current_app.config["BACKEND_SIGNER_PRIVATE_KEY"]
    signer = w3.eth.account.from_key(private_key)

    status_index = STATUS_INDEX.get(status)
    if status_index is None:
        raise WorkloadError(
            "invalid_status",
            f"unknown workload status {status!r} (expected one of: "
            f"{', '.join(sorted(STATUS_INDEX))})",
        )

    try:
        gas = contract.functions.logWorkload(class_id, professor_id, status_index).estimate_gas(
            {"from": signer.address}
        )
        transaction = contract.functions.logWorkload(
            class_id, professor_id, status_index
        ).build_transaction(
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
    except WorkloadError:
        raise
    except Exception as exc:
        text = f"{exc} {getattr(exc, 'name', '')}"
        if "WorkloadAlreadyLogged" in text:
            raise WorkloadError(
                "workload_already_logged",
                f"workload for class {class_id} is already logged on-chain",
            ) from exc
        raise WorkloadError(
            "blockchain_error", f"logWorkload() transaction failed: {exc}"
        ) from exc

    if receipt.status != 1:
        raise WorkloadError(
            "blockchain_error",
            "logWorkload() transaction reverted on-chain "
            "(possible duplicate log or missing BACKEND_ROLE)",
        )

    block = w3.eth.get_block(receipt.blockNumber)
    return {
        # hexbytes >= 2.0 drops the 0x prefix from .hex(); keep the canonical form.
        "transaction_hash": Web3.to_hex(receipt.transactionHash),
        "block_number": receipt.blockNumber,
        "logged_at": int(block.timestamp),
        "signer": signer.address,
    }


def get_workload_log(class_id: int) -> dict:
    """Read WorkloadLedger.getWorkloadLog() - the on-chain 'already logged' check."""
    _, contract = _load_ledger()
    try:
        logged, status_index, professor_id, timestamp = contract.functions.getWorkloadLog(
            class_id
        ).call()
    except WorkloadError:
        raise
    except Exception as exc:
        raise WorkloadError("blockchain_unavailable", f"getWorkloadLog() call failed: {exc}") from exc

    return {
        "class_id": int(class_id),
        "logged": bool(logged),
        "status": INDEX_STATUS.get(int(status_index)),
        "professor_id": int(professor_id),
        "timestamp": (
            datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
            if int(timestamp)
            else None
        ),
    }
