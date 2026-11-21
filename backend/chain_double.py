"""In-process stand-in for both ledger clients, for automated tests.

AttendanceLedger and WorkloadLedger are append-only, so a test that writes to
the shared Hardhat node leaves entries behind forever. This module replaces
the four client entry points with an in-memory ledger and turns both
``_load_ledger`` helpers into tripwires, so a code path that bypasses the
double fails loudly instead of appending to the real chain.

Nothing here opens a socket. It is not a contract test: it proves the backend
flow (chain-first ordering, one entry per class, duplicate refusal), not the
Solidity behaviour. Real contract integration must be exercised separately
against a disposable node.

Usage (before the first request that can write):

    double = ChainDouble(owns_class=..., owns_session=...)
    with double:
        run_steps()

``owns_class``/``owns_session`` are optional callables that return False for a
row outside the test's fixtures; a write for such a row raises immediately.
"""

import hashlib
from contextlib import ExitStack
from unittest.mock import patch

from app.services import blockchain, workload
from app.util import utcnow


class ChainDouble:
    """An in-memory pair of ledgers with the same observable behaviour."""

    def __init__(self, owns_class=None, owns_session=None):
        self.workload_logs = {}
        self.attendance_locks = {}
        self.writes = []
        self.owns_class = owns_class
        self.owns_session = owns_session
        self._stack = None

    # -- guards -------------------------------------------------------------
    def _check_class(self, class_id):
        if self.owns_class is not None and not self.owns_class(class_id):
            raise AssertionError(
                f"chain double: workload write for class {class_id}, which is not one of this run's fixtures"
            )

    def _check_session(self, session_id):
        if self.owns_session is not None and not self.owns_session(session_id):
            raise AssertionError(
                f"chain double: attendance write for session {session_id}, "
                "which is not one of this run's fixtures"
            )

    @staticmethod
    def _tx(kind, *parts):
        material = ":".join([kind, *(str(part) for part in parts)])
        return "0x" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    # -- WorkloadLedger -----------------------------------------------------
    def log_workload(self, class_id, professor_id, status):
        if status not in workload.STATUS_INDEX:
            raise workload.WorkloadError(
                "invalid_status", f"unknown workload status {status!r}",
            )
        self._check_class(class_id)
        if class_id in self.workload_logs:
            raise workload.WorkloadError(
                "workload_already_logged",
                f"workload for class {class_id} is already logged on-chain",
            )
        logged_at = utcnow()
        result = {
            "transaction_hash": self._tx("workload", class_id, professor_id, status),
            "block_number": len(self.writes) + 1,
            "logged_at": int(logged_at.timestamp()),
            "signer": "0x" + "00" * 20,
        }
        self.workload_logs[class_id] = {
            "class_id": int(class_id), "logged": True, "status": status,
            "professor_id": int(professor_id), "timestamp": logged_at.isoformat(),
        }
        self.writes.append({"ledger": "workload", "classId": class_id,
                            "professorId": professor_id, "status": status})
        return result

    def get_workload_log(self, class_id):
        row = self.workload_logs.get(class_id)
        if row is None:
            return {"class_id": int(class_id), "logged": False, "status": None,
                    "professor_id": 0, "timestamp": None}
        return dict(row)

    # -- AttendanceLedger ---------------------------------------------------
    def lock_attendance(self, student_id, session_id):
        self._check_session(session_id)
        key = (student_id, session_id)
        if key in self.attendance_locks:
            raise blockchain.BlockchainError(
                "attendance_already_locked",
                "attendance for this student+session is already locked on-chain",
            )
        locked_at = utcnow()
        result = {
            "transaction_hash": self._tx("attendance", student_id, session_id),
            "block_number": len(self.writes) + 1,
            "locked_at": int(locked_at.timestamp()),
            "signer": "0x" + "00" * 20,
        }
        self.attendance_locks[key] = result
        self.writes.append({"ledger": "attendance", "studentId": student_id,
                            "sessionId": session_id})
        return result

    def is_locked(self, student_id, session_id):
        return (student_id, session_id) in self.attendance_locks

    # -- installation -------------------------------------------------------
    def _tripwire(self, *_args, **_kwargs):
        raise AssertionError(
            "a ledger call bypassed chain_double: refusing to touch the shared Hardhat node"
        )

    def __enter__(self):
        if self._stack is not None:
            raise RuntimeError("chain_double is already installed")
        stack = ExitStack()
        stack.enter_context(patch.object(workload, "log_workload", self.log_workload))
        stack.enter_context(patch.object(workload, "get_workload_log", self.get_workload_log))
        stack.enter_context(patch.object(blockchain, "lock_attendance", self.lock_attendance))
        stack.enter_context(patch.object(blockchain, "is_locked", self.is_locked))
        stack.enter_context(patch.object(workload, "_load_ledger", self._tripwire))
        stack.enter_context(patch.object(blockchain, "_load_ledger", self._tripwire))
        self._stack = stack
        return self

    def __exit__(self, exc_type, exc, traceback):
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
        return False

    # -- inspection ---------------------------------------------------------
    def summary(self):
        workload_writes = [row for row in self.writes if row["ledger"] == "workload"]
        attendance_writes = [row for row in self.writes if row["ledger"] == "attendance"]
        return {
            "workloadWrites": len(workload_writes),
            "attendanceWrites": len(attendance_writes),
            "classes": sorted({row["classId"] for row in workload_writes}),
        }
