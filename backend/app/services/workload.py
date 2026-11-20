"""On-chain WorkloadLedger client (Module 3 - Faculty Workload Ledger).

Reads the artifact written by `npm run deploy:workload`
(`contracts/deployed/WorkloadLedger.json`); the address can be overridden
via WORKLOAD_LEDGER_CONTRACT_ADDRESS. Transactions are signed by the
backend signer (BACKEND_SIGNER_PRIVATE_KEY, which must hold BACKEND_ROLE on
the contract - the same single-trusted-signer pattern as AttendanceLedger
in blockchain.py, not the multi-role signers of audit_trail.py).

It also owns the single place where a class's workload outcome is derived
from attendance evidence (Phase 2.1e): `ensure_workload_outcome` and
`resolve_class_outcome`, shared by POST /session/<id>/end and the
scheduler's overdue sweep so both flows agree.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from web3 import Web3

from ..extensions import db
from ..models import (
    AttendanceCheckIn,
    AttendanceSession,
    ScheduledClass,
    SessionLog,
    WorkloadSession,
)
from ..models.workload import (
    STATUS_CONDUCTED,
    STATUS_CONDUCTED_ZERO_STUDENTS,
    STATUS_UNCONDUCTED,
)
from ..util import utcnow

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


def anchor_session_log(scheduled_class: ScheduledClass, conductor_id: int, status: str) -> tuple[SessionLog, dict]:
    """Log the class outcome on-chain first, then persist the local row.

    Same order as the Module 2 propose endpoint: a failed transaction must
    never leave a local row behind. The scheduled professor is credited and
    the conductor recorded separately (D1). Returns (session_log,
    chain_result). Callers commit.
    """
    from .occurrences import is_cancelled

    if is_cancelled(scheduled_class):
        raise WorkloadError("occurrence_cancelled", "a cancelled occurrence has no workload outcome")
    credited_professor_id = scheduled_class.professor_id
    result = log_workload(scheduled_class.id, credited_professor_id, status)
    session_log = SessionLog(
        class_id=scheduled_class.id,
        status=status,
        professor_id=credited_professor_id,
        conducted_by=conductor_id,
        timestamp=utcnow(),
        onchain_tx_hash=result["transaction_hash"],
    )
    db.session.add(session_log)
    return session_log, result


def class_session_ids(scheduled_class: ScheduledClass) -> list[int]:
    """Every attendance session opened for a class.

    Plain attendance sessions carry str(ScheduledClass.id) and only count
    when they started inside that class's start window (cold resets keep old
    session rows whose class ids may now name a different class); workload
    sessions resolve through their WorkloadSession row.
    """
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    session_ids = [row.id for row in db.session.query(AttendanceSession.id).filter(
        AttendanceSession.class_id == str(scheduled_class.id),
        AttendanceSession.start_time >= scheduled_class.start_time - grace,
    )]
    session_ids += [row.attendance_session_id for row in db.session.query(
        WorkloadSession.attendance_session_id,
    ).filter(WorkloadSession.class_id == scheduled_class.id)]
    return session_ids


def session_scheduled_class(attendance_session: AttendanceSession) -> ScheduledClass | None:
    """The ScheduledClass an attendance or workload session was opened for."""
    workload_session = WorkloadSession.query.filter_by(
        attendance_session_id=attendance_session.id
    ).first()
    if workload_session is not None:
        return db.session.get(ScheduledClass, workload_session.class_id)
    name = (attendance_session.class_id or "").strip()
    if not (name.isascii() and name.isdigit() and len(name) <= 10):
        return None
    scheduled_class = db.session.get(ScheduledClass, int(name))
    grace = timedelta(minutes=current_app.config["SESSION_START_GRACE_MINUTES"])
    if scheduled_class is None or attendance_session.start_time < scheduled_class.start_time - grace:
        return None
    return scheduled_class


def outcome_conductor_id(scheduled_class: ScheduledClass) -> int:
    """Who conducted the class: the latest WorkloadSession's conductor (the
    substitute once a token was redeemed), otherwise the user who started the
    attendance session - /session/start admits only the scheduled professor.
    """
    workload_session = (
        WorkloadSession.query.filter_by(class_id=scheduled_class.id)
        .order_by(WorkloadSession.id.desc())
        .first()
    )
    if workload_session is not None:
        return workload_session.conductor_id
    return scheduled_class.professor_id


def _anchor_once(scheduled_class: ScheduledClass, status: str) -> SessionLog | None:
    """Anchor `status`; a chain that already holds the class is nothing to do."""
    try:
        session_log, _result = anchor_session_log(
            scheduled_class, outcome_conductor_id(scheduled_class), status
        )
    except WorkloadError as exc:
        if exc.code == "workload_already_logged":
            return None
        raise
    return session_log


def _has_check_in(session_ids: list[int]) -> bool:
    return bool(session_ids) and AttendanceCheckIn.query.filter(
        AttendanceCheckIn.session_id.in_(session_ids)
    ).first() is not None


def ensure_workload_outcome(scheduled_class: ScheduledClass) -> SessionLog | None:
    """Derive CONDUCTED from attendance evidence (Phase 2.1e).

    Nothing when the class already has a SessionLog. Otherwise, if any of
    the class's attendance sessions holds at least one AttendanceCheckIn,
    the class is anchored CONDUCTED through `anchor_session_log` (scheduled
    professor credited, `outcome_conductor_id` recorded) and the local
    SessionLog is added. Zero check-ins write nothing here; that case
    belongs to the zero-student path in `resolve_class_outcome`. Returns the
    new SessionLog or None. Raises WorkloadError when the chain write fails.
    Callers commit.
    """
    from .occurrences import is_cancelled

    if is_cancelled(scheduled_class):
        return None
    if SessionLog.query.filter_by(class_id=scheduled_class.id).first() is not None:
        return None
    if not _has_check_in(class_session_ids(scheduled_class)):
        return None
    return _anchor_once(scheduled_class, STATUS_CONDUCTED)


def resolve_class_outcome(scheduled_class: ScheduledClass, *, require_closed: bool) -> SessionLog | None:
    """The one entry point that concludes a started class (2.1c + 2.1e).

    CONDUCTED via `ensure_workload_outcome` when check-ins exist; otherwise,
    if a session was started, no SessionLog exists and no capture was made,
    CONDUCTED_ZERO_STUDENTS through the same helper as
    /faculty/session/zero-attendance (Phase 2.1c). `require_closed` defers
    the zero-student entry while any session of the class is still live
    (the end route); the overdue sweep passes False because end_time has
    passed. Idempotent: never writes a second entry. Callers commit.
    """
    from .occurrences import is_cancelled

    if is_cancelled(scheduled_class):
        return None
    session_log = ensure_workload_outcome(scheduled_class)
    if session_log is not None:
        return session_log
    if SessionLog.query.filter_by(class_id=scheduled_class.id).first() is not None:
        return None
    session_ids = class_session_ids(scheduled_class)
    if not session_ids:
        return None  # never started: the UNCONDUCTED rule's case
    if require_closed and AttendanceSession.query.filter(
        AttendanceSession.id.in_(session_ids), AttendanceSession.is_active.is_(True),
    ).first() is not None:
        return None  # a newer session of the class is still live
    if _has_check_in(session_ids):
        return None  # captured, but the chain already held the class
    return _anchor_once(scheduled_class, STATUS_CONDUCTED_ZERO_STUDENTS)


def resolve_ended_session(attendance_session: AttendanceSession) -> SessionLog | None:
    """POST /session/<id>/end: resolve the class behind the closed session."""
    scheduled_class = session_scheduled_class(attendance_session)
    if scheduled_class is None:
        return None
    return resolve_class_outcome(scheduled_class, require_closed=True)
