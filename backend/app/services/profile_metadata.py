"""Profile metadata pinning (Phase 1.6 - requirements doc 1.3, decision D7).

Registration pins a small JSON document describing the account to IPFS and
stores its CID (``profile_cid``). The document carries only non-sensitive
directory data - role, name, department, roll number + batch (students) or
employee id (faculty / HOD) and the registration timestamp. The email
address, the password and the password hash are NEVER included.

Pinning is best effort: when the IPFS daemon (and the Web3.Storage
fallback) are unreachable, ``pin`` logs a warning and returns None so
registration still succeeds with a null CID. ``backfill_profile_cids.py``
fills those gaps later.
"""

import json

from flask import current_app

from ..models import InstitutionWhitelist, UserRole
from ..util import iso_z
from .ipfs import IPFSUploadError, upload_to_ipfs


def build_document(
    user,
    *,
    employee_id: str | None = None,
    roll_number: str | None = None,
    batch: str | None = None,
) -> dict:
    """Non-sensitive profile document for ``user`` (never email or password)."""
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    document = {
        "role": role,
        "full_name": user.full_name,
        "department": user.department,
        "registered_at": iso_z(user.created_at) if user.created_at else None,
    }
    if role == UserRole.STUDENT.value:
        document["roll_number"] = roll_number
        document["batch"] = batch
    else:
        document["employee_id"] = employee_id
    return document


def pin(user, document: dict) -> str | None:
    """Pin ``document`` for ``user``; None (with a warning) when IPFS is down."""
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        return upload_to_ipfs(payload, f"profile-{user.id}.json")
    except IPFSUploadError as exc:
        current_app.logger.warning(
            "profile metadata for user %s was not pinned (%s): %s",
            user.id,
            exc.code,
            exc.message,
        )
        return None


def pin_for_user(user) -> str | None:
    """Rebuild and pin the document of an already stored user (backfill).

    Faculty and students take their identifiers from the role profile; an HOD
    has no profile row, so the employee id comes from the whitelist entry that
    verified the account (looked up by email, which is never pinned).
    """
    if user.role == UserRole.STUDENT:
        profile = user.student_profile
        if profile is None:
            return None
        document = build_document(user, roll_number=profile.roll_number, batch=profile.batch)
    elif user.role == UserRole.FACULTY:
        profile = user.faculty_profile
        if profile is None:
            return None
        document = build_document(user, employee_id=profile.employee_id)
    else:
        entry = InstitutionWhitelist.query.filter_by(email=user.email).first()
        document = build_document(user, employee_id=entry.employee_id if entry else None)
    return pin(user, document)
