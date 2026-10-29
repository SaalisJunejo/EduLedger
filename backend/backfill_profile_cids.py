"""Backfill missing profile metadata CIDs (Phase 1.6, decision D7).

Why this exists
---------------
Registration pins a non-sensitive profile document to IPFS and stores its
CID, but a registration that happened while the IPFS daemon was down keeps
``profile_cid`` null (by design - the account is still created). This script
pins those profiles now and stores their CIDs.

It is idempotent: a row that already has a CID is left untouched, so running
it twice pins nothing the second time.

    cd backend
    .\\venv\\Scripts\\python.exe backfill_profile_cids.py

Prerequisite: the local IPFS daemon (`ipfs daemon`) or WEB3_STORAGE_TOKEN in
backend/.env. Rows that still fail to pin stay null and are reported, so the
script can simply be re-run later.
"""

import os
import sys

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import FacultyProfile, StudentProfile, User, UserRole  # noqa: E402
from app.services import profile_metadata  # noqa: E402


def _targets() -> list[tuple[str, object, User]]:
    """(label, row owning profile_cid, user) for every profile still missing a CID."""
    targets: list[tuple[str, object, User]] = []

    for profile in (
        FacultyProfile.query.filter(FacultyProfile.profile_cid.is_(None))
        .order_by(FacultyProfile.user_id)
        .all()
    ):
        user = db.session.get(User, profile.user_id)
        if user is not None:
            targets.append((f"faculty {profile.employee_id}", profile, user))

    for profile in (
        StudentProfile.query.filter(StudentProfile.profile_cid.is_(None))
        .order_by(StudentProfile.user_id)
        .all()
    ):
        user = db.session.get(User, profile.user_id)
        if user is not None:
            targets.append((f"student {profile.roll_number}", profile, user))

    # HOD accounts have no profile row: their CID lives on the User row.
    for user in (
        User.query.filter(User.role == UserRole.HOD, User.profile_cid.is_(None))
        .order_by(User.id)
        .all()
    ):
        targets.append((f"hod user #{user.id}", user, user))

    return targets


def main() -> int:
    """Pin and store every missing profile CID; report each decision."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        print("EduLedger profile metadata backfill (IPFS, D7)")
        print(f"  ipfs api: {app.config['IPFS_API_URL']}")

        targets = _targets()
        if not targets:
            print("[OK] every profile already has a CID - nothing to pin")
            return 0

        pinned = failed = 0
        for label, row, user in targets:
            cid = profile_metadata.pin_for_user(user)
            if cid is None:
                failed += 1
                print(f"  skip   {label:<28} IPFS unavailable - still null")
                continue
            row.profile_cid = cid
            pinned += 1
            print(f"  pin    {label:<28} {cid}")
        db.session.commit()

        print(f"[OK] {pinned} profile CID(s) stored, {failed} still missing")
        # Still-missing rows are not an error: re-run once IPFS is reachable.
    return 0


if __name__ == "__main__":
    sys.exit(main())
