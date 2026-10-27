"""Governance / system admin bootstrap endpoints (Phase 0.5).

- GET  /api/v1/governance/status      - public: on-chain root lock + ADMIN presence
- POST /api/v1/auth/bootstrap/admin   - one-time System Admin creation

The bootstrap route deliberately has no `require_auth`: it runs before any
ADMIN exists, so nobody could hold a token for it. It is protected by
BOOTSTRAP_ADMIN_SECRET plus the permanent on-chain lock in
GovernanceRegistry - the chain write happens first, so a second attempt is
refused by the contract, not by application logic.
"""

import hmac

from flask import current_app, jsonify, request

from ..extensions import db
from ..models import User, UserRole
from ..services.auth import hash_password
from ..services.blockchain import BlockchainError
from ..services.governance import lock_root, root_status
from . import api_v1
# Same institutional domain rule the self-service registration routes apply.
from .auth import _validate_institution_email
from .errors import api_error

# BlockchainError.code -> HTTP status for the governance routes.
_CHAIN_STATUS = {
    "blockchain_unavailable": 503,
    "blockchain_not_configured": 503,
    "root_already_locked": 409,
    "invalid_root_address": 500,
    "not_deployer": 500,
}


def _chain_error(exc: BlockchainError):
    """Map a BlockchainError onto the shared {check, error, message} shape."""
    return api_error("governance", exc.code, exc.message, status=_CHAIN_STATUS.get(exc.code, 502))


@api_v1.get("/governance/status")
def governance_status():
    """Report the on-chain root lock state and whether an ADMIN account exists.

    Public on purpose: the frontend needs it to decide whether to show the
    one-time bootstrap screen, and no token can exist before bootstrap.

    Returns:
        200: {"locked": bool, "rootAdmin": str, "adminUserExists": bool}
        503: Chain unreachable or GovernanceRegistry address/ABI unknown
    """
    try:
        status = root_status()
    except BlockchainError as exc:
        return _chain_error(exc)

    admin_exists = User.query.filter_by(role=UserRole.ADMIN).first() is not None

    return (
        jsonify(
            {
                "locked": status["locked"],
                "rootAdmin": status["root_admin"],
                "adminUserExists": admin_exists,
            }
        ),
        200,
    )


@api_v1.post("/auth/bootstrap/admin")
def bootstrap_admin():
    """Lock the system root on-chain, then create the System Admin account.

    Accepts:
        full_name (str): Full name of the System Admin
        email (str): Institutional email address
        password (str): Plain text password to hash (Argon2id)
        bootstrap_secret (str): Must match BOOTSTRAP_ADMIN_SECRET

    Chain write first, then the DB: `lockRoot(ADMIN_ROOT_ADDRESS)` is sent
    before the user row is created, so the permanent on-chain lock - not a
    local check - is what makes this a one-time operation. If an ADMIN with
    this email already exists (e.g. the chain was restarted), nothing about
    that account is touched; only the lock is placed.

    Returns:
        201: {"message", "user", "rootAdmin", "locked", "transactionHash", ...}
        400: Missing fields or invalid institutional email domain
        403: Wrong bootstrap secret
        409: Root already locked on-chain, or the email belongs to a non-ADMIN user
        503: BOOTSTRAP_ADMIN_SECRET unset, or the chain is unreachable
    """
    body = request.get_json(silent=True) or {}
    full_name = str(body.get("full_name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    bootstrap_secret = str(body.get("bootstrap_secret") or "")

    if not full_name or not email or not password or not bootstrap_secret:
        return api_error(
            "request",
            "missing_fields",
            "full_name, email, password, and bootstrap_secret are required",
            status=400,
        )

    # 1. Shared secret - the only caller gate available before any ADMIN exists.
    configured_secret = str(current_app.config.get("BOOTSTRAP_ADMIN_SECRET") or "")
    if not configured_secret:
        return api_error(
            "bootstrap",
            "bootstrap_not_configured",
            "BOOTSTRAP_ADMIN_SECRET is not set - admin bootstrap is disabled",
            status=503,
        )
    if not hmac.compare_digest(bootstrap_secret, configured_secret):
        return api_error(
            "bootstrap",
            "invalid_bootstrap_secret",
            "bootstrap_secret does not match BOOTSTRAP_ADMIN_SECRET",
            status=403,
        )

    # 2. Institutional email domain.
    valid_domain, expected_domain = _validate_institution_email(email)
    if not valid_domain:
        return api_error(
            "email",
            "invalid_email_domain",
            f"Email must belong to institutional domain '@{expected_domain}'",
            status=400,
        )

    # 3. An existing ADMIN with this email is reused untouched; any other role
    #    owning the address is a conflict (no password or field is overwritten).
    existing = User.query.filter_by(email=email).first()
    if existing is not None and existing.role != UserRole.ADMIN:
        return api_error(
            "user",
            "email_already_registered",
            f"A {existing.role.value} user with email '{email}' is already registered",
            status=409,
        )

    # 4. Read the lock first so the common "already bootstrapped" case answers
    #    409 without spending a transaction; the contract is still the authority.
    try:
        status = root_status()
        if status["locked"]:
            return api_error(
                "governance",
                "root_already_locked",
                f"the system root is already locked on-chain to {status['root_admin']}",
                status=409,
                rootAdmin=status["root_admin"],
            )

        # 5. Chain write, then DB (project convention).
        receipt = lock_root(current_app.config.get("ADMIN_ROOT_ADDRESS"))
    except BlockchainError as exc:
        return _chain_error(exc)

    # 6. Create the ADMIN account, or keep the existing one exactly as it is.
    created = existing is None
    if created:
        user = User(
            role=UserRole.ADMIN,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            department=None,  # System Admin spans every department
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    else:
        user = existing

    return (
        jsonify(
            {
                "message": (
                    "System root locked on-chain and ADMIN account created"
                    if created
                    else "System root locked on-chain; the existing ADMIN account was left unchanged"
                ),
                "user": user.to_dict(),
                "created": created,
                "locked": True,
                "rootAdmin": receipt["root_admin"],
                "transactionHash": receipt["transaction_hash"],
                "blockNumber": receipt["block_number"],
                "lockedAt": receipt["locked_at"],
                "signer": receipt["signer"],
            }
        ),
        201,
    )
