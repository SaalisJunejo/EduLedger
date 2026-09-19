"""On-chain RecordAuditTrail client (Module 2 - Multi-Sig Audit Trail).

Reads the artifact written by `npm run deploy:audit-trail`
(`contracts/deployed/RecordAuditTrail.json`); the address can be overridden
via AUDIT_TRAIL_CONTRACT_ADDRESS. Three role signers mirror the labeled
accounts in `contracts/deployed/accounts.json`: proposals are sent from the
instructor signer (INSTRUCTOR_ROLE) and approvals from the HOD or Exam
Controller signer, so every transaction is attributable to a role rather
than to the backend's own key (unlike AttendanceLedger's BACKEND_ROLE
signer in blockchain.py).
"""

import json
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from web3 import Web3

from ..models.proposal import STATUS_EXECUTED, STATUS_PENDING

# <repo>/contracts/deployed/RecordAuditTrail.json
# (this file is backend/app/services/audit_trail.py -> parents[3] = repo root)
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "deployed" / "RecordAuditTrail.json"

# Approval roles -> config key holding that role's signer private key.
APPROVER_ROLES = {
    "hod": "HOD_SIGNER_PRIVATE_KEY",
    "exam_controller": "EXAM_CONTROLLER_SIGNER_PRIVATE_KEY",
}

# Solidity custom errors -> AuditTrailError codes. Matched against the
# exception text web3.py raises (it surfaces custom error names in the
# message, the same trick blockchain.py uses for AttendanceAlreadyLocked);
# anything unmatched becomes blockchain_error.
_REVERT_CODES = {
    "ProposalNotFound": "proposal_not_found",
    "ProposalNotPending": "proposal_not_pending",
    "AlreadySigned": "already_signed",
    "NotAnApprover": "not_an_approver",
    "EmptyField": "empty_field",
    "EmptyIpfsCid": "empty_ipfs_cid",
}

_REVERT_MESSAGES = {
    "proposal_not_found": "proposal does not exist on RecordAuditTrail",
    "proposal_not_pending": "proposal is no longer Pending (it already executed)",
    "already_signed": "this address has already signed the proposal",
    "not_an_approver": "signer holds neither HOD_ROLE nor EXAM_CONTROLLER_ROLE",
    "not_instructor": "signer does not hold INSTRUCTOR_ROLE",
    "empty_field": "the record field cannot be empty",
    "empty_ipfs_cid": "the IPFS evidence CID cannot be empty",
}


class AuditTrailError(Exception):
    """On-chain call failure with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _artifact_path() -> Path:
    configured = current_app.config.get("AUDIT_TRAIL_ARTIFACT")
    return Path(configured) if configured else DEFAULT_ARTIFACT


def _load_audit_trail():
    """Return ``(w3, contract)`` for RecordAuditTrail or raise AuditTrailError."""
    rpc = current_app.config["HARDHAT_RPC_URL"]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
    try:
        connected = w3.is_connected()
    except Exception as exc:  # network-level failure (unreachable host, proxy, ...)
        raise AuditTrailError(
            "blockchain_unavailable", f"cannot reach the blockchain node at {rpc}: {exc}"
        ) from exc
    if not connected:
        raise AuditTrailError(
            "blockchain_unavailable",
            f"blockchain node at {rpc} is not reachable (is `npm run node` running?)",
        )

    artifact_path = _artifact_path()
    address = current_app.config["CONTRACT_ADDRESSES"].get("audit_trail") or ""
    abi = None
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        abi = artifact.get("abi")
        address = address or artifact.get("address", "")
    if not address:
        raise AuditTrailError(
            "audit_trail_not_configured",
            "RecordAuditTrail address unknown - run `npm run deploy:audit-trail` "
            "or set AUDIT_TRAIL_CONTRACT_ADDRESS",
        )
    if not abi:
        raise AuditTrailError(
            "audit_trail_not_configured",
            f"RecordAuditTrail ABI not found at {artifact_path}",
        )

    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
    return w3, contract


def _iso_from_unix(timestamp: int) -> str | None:
    """Format a unix timestamp as an ISO-8601 UTC string (None for 0 = unset)."""
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _revert_code(exc: Exception, unauthorized_code: str) -> str:
    """Map a web3 revert exception to an AuditTrailError code.

    Custom errors surface either decoded in the exception message or as raw
    revert data on the exception; scanning both keeps the mapping stable
    across web3 patch versions. ``unauthorized_code`` covers OpenZeppelin's
    AccessControlUnauthorizedAccount, whose meaning depends on the call site
    (INSTRUCTOR_ROLE for proposeChange, approver roles for approve).
    """
    text = f"{exc} {getattr(exc, 'name', '')} {getattr(exc, 'data', '')}"
    if "AccessControlUnauthorizedAccount" in text:
        return unauthorized_code
    for name, code in _REVERT_CODES.items():
        if name in text:
            return code
    return "blockchain_error"


def _revert_detail(exc: Exception) -> str:
    """Concise one-line description of a web3 exception for error messages.

    Prefers the decoded custom error (e.g. ``ProposalNotPending(3)``) over
    the full raw exception, which repeats the message and revert data.
    """
    text = f"{exc} {getattr(exc, 'message', '')} {getattr(exc, 'data', '')}"
    match = re.search(r"custom error '([^']+)'", text)
    if match:
        return match.group(1)
    detail = str(exc).strip()
    return detail[:160] if detail else type(exc).__name__


def _event_args(receipt, contract, name: str) -> dict | None:
    """Decoded args of the first `name` event in `receipt`, or None."""
    try:
        with warnings.catch_warnings():
            # One tx emits several events (e.g. ProposalCreated +
            # ProposalSigned); web3 warns while discarding the logs that do
            # not match `name`, which is expected here - silence it.
            warnings.simplefilter("ignore", UserWarning)
            logs = getattr(contract.events, name)().process_receipt(receipt)
    except Exception:
        return None
    for log in logs or []:
        try:
            return dict(log["args"])
        except Exception:
            continue
    return None


def _send_transaction(w3, function_call, private_key: str, call_name: str, unauthorized_code: str) -> dict:
    """Sign, send and wait for `function_call` from the given key; return the receipt.

    Reverts are decoded into AuditTrailError codes (see _REVERT_CODES);
    infrastructure failures become blockchain_error / blockchain_unavailable.
    """
    signer = w3.eth.account.from_key(private_key)
    try:
        gas = function_call.estimate_gas({"from": signer.address})
        transaction = function_call.build_transaction(
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
    except Exception as exc:
        code = _revert_code(exc, unauthorized_code)
        base = _REVERT_MESSAGES.get(code) or f"{call_name} transaction failed"
        raise AuditTrailError(code, f"{base}: {_revert_detail(exc)}") from exc

    if receipt.status != 1:
        raise AuditTrailError(
            "blockchain_error",
            f"{call_name} transaction reverted on-chain "
            "(missing role or invalid proposal state)",
        )
    return receipt


# RecordAuditTrail.Proposal struct fields in declaration order - used to
# normalize positional (tuple) returns from getProposal(); see _read_proposal.
_PROPOSAL_FIELDS = (
    "id",
    "studentId",
    "field",
    "oldValue",
    "newValue",
    "ipfsCid",
    "proposer",
    "status",
    "signatureCount",
    "createdAt",
    "executedAt",
)


def _read_proposal(contract, proposal_id: int) -> dict:
    """Read one proposal via getProposal() and normalize it to JSON shape.

    web3.py returns struct outputs either as a name-keyed mapping or as a
    plain tuple in struct-declaration order, depending on the artifact's ABI
    encoding - normalize both.
    """
    try:
        data = contract.functions.getProposal(proposal_id).call()
    except Exception as exc:
        code = _revert_code(exc, "not_an_approver")
        base = _REVERT_MESSAGES.get(code) or "getProposal() call failed"
        raise AuditTrailError(code, f"{base}: {_revert_detail(exc)}") from exc

    if isinstance(data, (tuple, list)):
        data = dict(zip(_PROPOSAL_FIELDS, data))
    return {
        "proposalId": int(data["id"]),
        "studentId": int(data["studentId"]),
        "field": data["field"],
        "oldValue": data["oldValue"],
        "newValue": data["newValue"],
        "ipfsCid": data["ipfsCid"],
        "proposer": data["proposer"],
        "status": STATUS_EXECUTED if int(data["status"]) == 1 else STATUS_PENDING,
        "signatureCount": int(data["signatureCount"]),
        "createdAt": _iso_from_unix(int(data["createdAt"])),
        "executedAt": _iso_from_unix(int(data["executedAt"])),
    }


def propose_change(student_id: int, field: str, old_value: str, new_value: str, ipfs_cid: str) -> dict:
    """Create a record-change proposal on RecordAuditTrail from the instructor signer.

    Returns the on-chain proposal id, transaction hash, the decoded
    ProposalCreated event, and the proposal state read back from the
    contract. Raises AuditTrailError on any failure (chain unreachable,
    missing INSTRUCTOR_ROLE, contract revert, ...).
    """
    w3, contract = _load_audit_trail()
    private_key = current_app.config["INSTRUCTOR_SIGNER_PRIVATE_KEY"]
    signer = w3.eth.account.from_key(private_key)

    call = contract.functions.proposeChange(student_id, field, old_value, new_value, ipfs_cid)
    receipt = _send_transaction(w3, call, private_key, "proposeChange()", "not_instructor")

    created = _event_args(receipt, contract, "ProposalCreated")
    if created and created.get("proposalId"):
        proposal_id = int(created["proposalId"])
    else:  # event decoding failed - the proposal just created is the latest
        proposal_id = int(contract.functions.proposalCount().call())

    proposal = _read_proposal(contract, proposal_id)
    return {
        "proposal_id": proposal_id,
        "transaction_hash": Web3.to_hex(receipt.transactionHash),
        "block_number": receipt.blockNumber,
        "signer": signer.address,
        "status": proposal["status"],
        "event": created,
        "proposal": proposal,
    }


def approve_proposal(proposal_id: int, role: str) -> dict:
    """Co-sign (and auto-execute) a proposal from the HOD or Exam Controller signer.

    Returns the transaction hash, the updated on-chain status and signature
    count, and the decoded RecordUpdated event when the approval reached the
    2-of-2 threshold. Raises AuditTrailError for an unknown role, a missing
    proposal, an already-executed proposal, or any chain failure.
    """
    normalized_role = str(role).strip().lower()
    config_key = APPROVER_ROLES.get(normalized_role)
    if config_key is None:
        raise AuditTrailError(
            "invalid_role",
            f"role must be one of: {', '.join(sorted(APPROVER_ROLES))} (got {role!r})",
        )

    w3, contract = _load_audit_trail()
    private_key = current_app.config[config_key]
    signer = w3.eth.account.from_key(private_key)

    call = contract.functions.approve(proposal_id)
    receipt = _send_transaction(w3, call, private_key, "approve()", "not_an_approver")

    executed_event = _event_args(receipt, contract, "RecordUpdated")
    proposal = _read_proposal(contract, proposal_id)
    return {
        "proposal_id": proposal_id,
        "transaction_hash": Web3.to_hex(receipt.transactionHash),
        "block_number": receipt.blockNumber,
        "signer": signer.address,
        "role": normalized_role,
        "status": proposal["status"],
        "signature_count": proposal["signatureCount"],
        "executed_at": proposal["executedAt"],
        "record_updated": executed_event is not None,
        "event": executed_event,
    }


def get_proposal(proposal_id: int) -> dict:
    """Read a proposal straight from the contract - the source of truth.

    Never touches the local Proposal table; a proposal that exists only on
    chain (e.g. proposed by another client) is still returned in full.
    """
    _, contract = _load_audit_trail()
    return _read_proposal(contract, proposal_id)
