"""Development utility: clear local mirror/state tables after a redeploy.

Why this exists
---------------
`proposals.onchain_proposal_id` is UNIQUE, and RecordAuditTrail numbers
proposals 1, 2, 3, ... from zero on every deployment. After redeploying the
contract, the fresh chain therefore re-issues ids that the local Postgres
table still holds from the previous chain state.

The same applies to the Module 3 workload tables: a freshly deployed
WorkloadLedger has no entries, so leftover `session_logs` rows (and the
scheduled classes they hang off) would reference on-chain state that no
longer exists, and re-issued `scheduled_classes` ids would collide with the
contract's one-entry-per-class rule.

POST /api/v1/records/propose already copes with the proposal side (it
upserts the mirror: a leftover row is refreshed to the new on-chain state
instead of failing with a duplicate-key error), but repeated redeploys still
leave stale rows behind - e.g. rows for proposals the new chain will never
create, or a Pending row for a proposal that no longer exists on-chain. Run
this after every fresh `npm run deploy:audit-trail` /
`npm run deploy:workload` for a clean slate:

    cd backend
    py -3.12 reset_local_state.py

What it does
------------
- empties the `proposals` table (Module 2 mirror) and resets its id
  sequence (Postgres: TRUNCATE ... RESTART IDENTITY; SQLite: a plain
  DELETE - INTEGER PRIMARY KEY ids start from 1 again once the table is
  empty)
- empties the Module 3 workload tables (`session_logs`,
  `substitute_tokens`, `workload_sessions`, `scheduled_classes`) together,
  in FK-safe order, resetting their sequences
- touches nothing else: students and attendance sessions are left alone,
  and on-chain state is reset by redeploying the contracts, not by this
  script

Development only - never run this against a database you care about.
"""

import os
import sys

# One-off script: no Flask server, so no background jobs either. Must be
# set before `app` is imported (the config reads it at import time).
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from sqlalchemy import text  # noqa: E402 (import after the env tweak)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Proposal,
    ScheduledClass,
    SessionLog,
    SubstituteToken,
    WorkloadSession,
)

# Truncation order: children before parents, so FK constraints hold on
# dialects that enforce them (Postgres truncates the whole set in one
# statement instead, which is FK-safe by definition).
_TABLES = [
    (Proposal.__tablename__, Proposal),
    (SessionLog.__tablename__, SessionLog),
    (SubstituteToken.__tablename__, SubstituteToken),
    (WorkloadSession.__tablename__, WorkloadSession),
    (ScheduledClass.__tablename__, ScheduledClass),
]


def main() -> int:
    """Truncate the proposal mirror and workload tables, reset sequences."""
    # Windows consoles often default to a legacy code page (cp1252); force
    # UTF-8 so output never dies on a print (same guard as the test scripts).
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with app.app_context():
        print("EduLedger local-state reset (development only)")
        print(f"  dialect: {db.engine.dialect.name}")

        if db.engine.dialect.name == "postgresql":
            # One statement for the whole related set: empties the tables
            # and rewinds every id sequence at once.
            names = ", ".join(table for table, _ in _TABLES)
            db.session.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY"))
            db.session.commit()
        else:
            # SQLite has no sequences to reset; emptied INTEGER PRIMARY KEY
            # tables hand out ids from 1 again on their own. Delete in
            # child-before-parent order.
            for _, model in _TABLES:
                db.session.query(model).delete()
            db.session.commit()

        for table, model in _TABLES:
            remaining = model.query.count()
            print(f"  {(table + ' rows'):<22}: {remaining}")
            assert remaining == 0, f"{table} was not cleared"

        print("  identity sequences  : restarted (next local id = 1)")
        print("[OK] proposals + workload tables cleared - safe to test against the fresh deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
