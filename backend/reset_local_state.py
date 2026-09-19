"""Development utility: clear the local Proposal mirror after a redeploy.

Why this exists
---------------
`proposals.onchain_proposal_id` is UNIQUE, and RecordAuditTrail numbers
proposals 1, 2, 3, ... from zero on every deployment. After redeploying the
contract, the fresh chain therefore re-issues ids that the local Postgres
table still holds from the previous chain state.

POST /api/v1/records/propose already copes with that (it upserts the mirror:
a leftover row is refreshed to the new on-chain state instead of failing with
a duplicate-key error), but repeated redeploys still leave stale rows behind -
e.g. rows for proposals the new chain will never create, or a Pending row for
a proposal that no longer exists on-chain. Run this after every fresh
`npm run deploy:audit-trail` for a clean slate:

    cd backend
    py -3.12 reset_local_state.py

What it does
------------
- empties the `proposals` table and resets its id sequence (Postgres:
  TRUNCATE ... RESTART IDENTITY; SQLite: a plain DELETE - INTEGER PRIMARY KEY
  ids start from 1 again once the table is empty)
- touches nothing else: students, attendance sessions and any other table are
  left alone, and on-chain state is reset by redeploying the contract, not by
  this script

Development only - never run this against a database you care about.
"""

import os
import sys

# One-off script: no Flask server, so no attendance nonce rotation either.
# Must be set before `app` is imported (the config reads it at import time).
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from sqlalchemy import text  # noqa: E402 (import after the env tweak)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Proposal  # noqa: E402


def main() -> int:
    """Truncate the proposal mirror and reset its identity sequence."""
    # Windows consoles often default to a legacy code page (cp1252); force
    # UTF-8 so output never dies on a print (same guard as the test scripts).
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        table = Proposal.__tablename__
        before = Proposal.query.count()

        if db.engine.dialect.name == "postgresql":
            # TRUNCATE + RESTART IDENTITY: empties the table and rewinds the
            # id sequence in one statement.
            db.session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY"))
        else:
            # SQLite has no sequences to reset; an emptied INTEGER PRIMARY KEY
            # table hands out ids from 1 again on its own.
            db.session.query(Proposal).delete()
        db.session.commit()

        after = Proposal.query.count()
        print("EduLedger local-state reset (development only)")
        print(f"  dialect          : {db.engine.dialect.name}")
        print(f"  {(table + ' rows'):<17}: {before} -> {after}")
        print("  identity sequence: restarted (next local id = 1)")
        print(f"[OK] {table} cleared - safe to test against the fresh deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
