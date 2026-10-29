"""End-to-end test of the Phase 1.4a post-deadline grade correction flow.

Run it against a live stack (from backend/):

    .\\venv\\Scripts\\python.exe test_grade_correction.py

Prerequisites:
    - Hardhat node running          (contracts/: npm run node)
    - RecordAuditTrail deployed     (contracts/: npm run deploy:audit-trail)
    - IPFS daemon running           (ipfs daemon) - propose pins the
                                    justification JSON before the chain call
    - Flask backend running         (backend/: python run.py), same
                                    backend/.env as this script

Steps:
     0. preflight          - GET /health (DB, RPC, audit-trail contract)
     1. auth setup         - unique faculty A + B, an HOD (whitelisted), a
                             DB-created ADMIN and two students
     2. seed grades        - Grade rows written directly: student 1 gets
                             COURSE_1 = "B" and COURSE_2 = "C+"
     3. bad proposals      - justification < 10 chars -> 400, same grade ->
                             400, unknown student / course -> 404; the row
                             stays unlocked
     4. propose            - faculty A: COURSE_1 B -> A-; 201 Pending, the
                             row is locked by the new correction
     5. second propose     - faculty B on the locked row -> 409 grade_locked
     6. race               - faculty A and B propose on COURSE_2 from two
                             threads at once ->
                             exactly one 201 and one 409 grade_locked
     7. wrong approvers    - FACULTY, ADMIN and STUDENT approve -> 403; the
                             chain still shows 1 signature, row still locked
     8. HOD approve        - 200 Executed on-chain (2 signatures,
                             RecordUpdated), grade = A-, lock cleared
     9. read grades        - student 1 sees A-; student 2 gets 403 on
                             student 1's grades
    10. reject             - HOD rejects the race winner (reason required):
                             lock cleared, grade unchanged, chain still Pending
    11. after reject       - approve and reject again -> 409 correction_rejected
    12. propose again      - faculty B on COURSE_2 -> 201 (the row is free)
    13. lists              - faculty A / B see only their own corrections;
                             HOD and ADMIN see all three with status, course,
                             old, new, justification and proposer name;
                             STUDENT -> 403
    Cleanup removes this run's users, profiles, whitelist row, Grade rows and
    Proposal mirror rows. The on-chain proposals stay (the chain is
    permanent); the one left open in step 12 stays Pending on-chain.
"""

import os
import sys
import threading
from uuid import uuid4

import requests

os.environ["SCHEDULER_ENABLED"] = "false"

from app import create_app
from app.extensions import db
from app.models import (
    FacultyProfile,
    Grade,
    InstitutionWhitelist,
    Proposal,
    StudentProfile,
    User,
    UserRole,
)
from app.services.auth import hash_password

# Windows consoles often default to cp1252, which cannot print the marks below.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:5000/api/v1"
RUN_ID = uuid4().hex[:10]
PASSWORD = "GradeTestPass123!"
DEPARTMENT = "Computer Science"
FACULTY_A_EMAIL = f"faculty.grade.a.{RUN_ID}@gcu.edu.pk"
FACULTY_B_EMAIL = f"faculty.grade.b.{RUN_ID}@gcu.edu.pk"
HOD_EMAIL = f"hod.grade.{RUN_ID}@gcu.edu.pk"
ADMIN_EMAIL = f"admin.grade.{RUN_ID}@gcu.edu.pk"
STUDENT_1_EMAIL = f"student.grade.1.{RUN_ID}@gcu.edu.pk"
STUDENT_2_EMAIL = f"student.grade.2.{RUN_ID}@gcu.edu.pk"
ALL_EMAILS = [FACULTY_A_EMAIL, FACULTY_B_EMAIL, HOD_EMAIL, ADMIN_EMAIL, STUDENT_1_EMAIL, STUDENT_2_EMAIL]
FACULTY_A_NAME = "Dr. Grade A"
FACULTY_B_NAME = "Dr. Grade B"
HOD_NAME = "Dr. Grade HOD"
HOD_EMPLOYEE_ID = f"EMP-GRD-HOD-{RUN_ID}"

COURSE_1 = f"TEST-GRADE-{RUN_ID}-1"
COURSE_2 = f"TEST-GRADE-{RUN_ID}-2"
COURSE_CODES = [COURSE_1, COURSE_2]
GRADE_FIELDS = [f"grade:{code}" for code in COURSE_CODES]

JUSTIFICATION = "Re-marked question 4 after the script review."
# RecordAuditTrail's HOD signer (contracts/deployed/accounts.json).
HOD_ADDRESS = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

APP = create_app()
AUTH = {}
STATE = {
    "faculty_a_id": None,
    "faculty_b_id": None,
    "hod_id": None,
    "admin_id": None,
    "student_1_id": None,
    "student_2_id": None,
    "missing_user_id": None,
    "approved_id": None,      # on-chain id of faculty A's COURSE_1 correction
    "race_id": None,          # on-chain id of the race winner (COURSE_2)
    "race_who": None,
    "race_new_grade": None,
    "second_id": None,        # on-chain id of faculty B's COURSE_2 correction
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def api(method: str, path: str, token: str | None = None, **kwargs) -> tuple[int, dict]:
    """Call the API; returns (status, decoded JSON body). Never raises on HTTP errors."""
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Allow the IPFS pin + chain receipt, including waiting for a row lock.
    response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=240, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text}
    return response.status_code, payload


def propose(who: str, student_id: int, course_code: str, new_grade: str,
            justification: str = JUSTIFICATION) -> tuple[int, dict]:
    return api(
        "POST", "/grades/corrections", token=AUTH[who],
        json={
            "student_id": student_id,
            "course_code": course_code,
            "new_grade": new_grade,
            "justification": justification,
        },
    )


def assert_error(status: int, payload: dict, expected_status: int, check: str, error: str, what: str):
    assert status == expected_status, f"{what}: expected {expected_status}, got {status}: {payload}"
    assert payload.get("check") == check, f"{what}: unexpected check: {payload}"
    assert payload.get("error") == error, f"{what}: unexpected error: {payload}"


def grade_row(course_code: str) -> tuple[str, int | None]:
    """(grade_value, locked_by_proposal_id) of student 1's row, read fresh."""
    with APP.app_context():
        row = Grade.query.filter_by(student_id=STATE["student_1_id"], course_code=course_code).one()
        return row.grade_value, row.locked_by_proposal_id


def local_id(onchain_id: int) -> int:
    with APP.app_context():
        return Proposal.query.filter_by(onchain_proposal_id=onchain_id).one().id


def chain_view(onchain_id: int) -> dict:
    status, payload = api("GET", f"/records/{onchain_id}", token=AUTH["hod"])
    assert status == 200, f"GET /records/{onchain_id} returned {status}: {payload}"
    return payload


def register_and_login(kind: str, email: str, body: dict) -> dict:
    status, payload = api("POST", f"/auth/register/{kind}", json={**body, "email": email, "password": PASSWORD})
    assert status == 201, f"{kind} registration ({email}) returned {status}: {payload}"
    return login(email)


def login(email: str) -> dict:
    status, payload = api("POST", "/auth/login", json={"email": email, "password": PASSWORD})
    assert status == 200, f"login {email} failed ({status}): {payload}"
    return payload


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
def step_preflight():
    status, payload = api("GET", "/health")
    assert status == 200, f"/health returned {status}: {payload}"
    assert payload.get("status") == "ok", f"health status is not 'ok': {payload}"
    deps = payload["dependencies"]
    assert deps["database_configured"], "database is not configured"
    assert deps["blockchain_rpc_configured"], "blockchain RPC is not configured"
    assert deps["contracts_configured"]["audit_trail"], (
        "audit trail contract address is not configured (run `npm run deploy:audit-trail`)"
    )
    print(f"   run_id={RUN_ID}")


def step_auth_setup():
    for key, email, name in (
        ("faculty_a", FACULTY_A_EMAIL, FACULTY_A_NAME),
        ("faculty_b", FACULTY_B_EMAIL, FACULTY_B_NAME),
    ):
        payload = register_and_login(
            "faculty", email,
            {"employee_id": f"EMP-GRD-{key[-1].upper()}-{RUN_ID}", "full_name": name, "department": DEPARTMENT},
        )
        AUTH[key] = payload["token"]
        STATE[f"{key}_id"] = payload["user"]["id"]

    # HOD registration only accepts whitelisted staff and ADMIN cannot
    # self-register, so both fixtures are written directly (the same
    # pattern as test_record_proposal.py).
    with APP.app_context():
        db.session.add(InstitutionWhitelist(
            employee_id=HOD_EMPLOYEE_ID, email=HOD_EMAIL, name=HOD_NAME, department=DEPARTMENT,
        ))
        db.session.add(User(
            role=UserRole.ADMIN, email=ADMIN_EMAIL, password_hash=hash_password(PASSWORD),
            full_name="Grade Test ADMIN", department=None, is_active=True,
        ))
        db.session.commit()
    payload = register_and_login(
        "hod", HOD_EMAIL,
        {"employee_id": HOD_EMPLOYEE_ID, "full_name": HOD_NAME, "department": DEPARTMENT},
    )
    AUTH["hod"] = payload["token"]
    STATE["hod_id"] = payload["user"]["id"]
    payload = login(ADMIN_EMAIL)
    assert payload["user"]["role"] == "ADMIN", f"fixture is not ADMIN: {payload}"
    AUTH["admin"] = payload["token"]
    STATE["admin_id"] = payload["user"]["id"]

    for key, email, roll in (
        ("student_1", STUDENT_1_EMAIL, f"ROLL-GRD-{RUN_ID}-1"),
        ("student_2", STUDENT_2_EMAIL, f"ROLL-GRD-{RUN_ID}-2"),
    ):
        payload = register_and_login(
            "student", email,
            {"full_name": f"Grade Student {key[-1]}", "roll_number": roll,
             "batch": "2022-2026", "department": DEPARTMENT},
        )
        AUTH[key] = payload["token"]
        STATE[f"{key}_id"] = payload["user"]["id"]
    print(
        f"   faculty A={STATE['faculty_a_id']} B={STATE['faculty_b_id']} hod={STATE['hod_id']} "
        f"admin={STATE['admin_id']} students={STATE['student_1_id']},{STATE['student_2_id']}"
    )


def step_seed_grades():
    with APP.app_context():
        db.session.add(Grade(student_id=STATE["student_1_id"], course_code=COURSE_1, grade_value="B"))
        db.session.add(Grade(student_id=STATE["student_1_id"], course_code=COURSE_2, grade_value="C+"))
        db.session.commit()
        STATE["missing_user_id"] = User.query.order_by(User.id.desc()).first().id + 1000
    assert grade_row(COURSE_1) == ("B", None), f"unexpected seed row: {grade_row(COURSE_1)}"
    assert grade_row(COURSE_2) == ("C+", None), f"unexpected seed row: {grade_row(COURSE_2)}"
    print(f"   student {STATE['student_1_id']}: {COURSE_1}=B, {COURSE_2}=C+")


def step_bad_proposals():
    s1 = STATE["student_1_id"]
    status, payload = propose("faculty_a", s1, COURSE_1, "A-", justification="too short")  # 9 chars
    assert_error(status, payload, 400, "request", "justification_too_short", "short justification")
    status, payload = propose("faculty_a", s1, COURSE_1, "B")
    assert_error(status, payload, 400, "request", "grade_unchanged", "same grade")
    status, payload = propose("faculty_a", STATE["missing_user_id"], COURSE_1, "A-")
    assert_error(status, payload, 404, "grade", "grade_not_found", "unknown student")
    status, payload = propose("faculty_a", s1, f"{COURSE_1}-NOPE", "A-")
    assert_error(status, payload, 404, "grade", "grade_not_found", "unknown course")
    status, payload = api(
        "POST", "/records/propose", token=AUTH["faculty_a"],
        json={"studentId": s1, "field": f"grade:{COURSE_1}", "oldValue": "B",
              "newValue": "A-", "ipfsCid": "bafy-test-reserved-field"},
    )
    assert_error(status, payload, 400, "request", "use_grade_corrections", "generic grade propose")
    assert grade_row(COURSE_1) == ("B", None), f"a rejected proposal touched the row: {grade_row(COURSE_1)}"
    print("   400 short / 400 unchanged / 404 student / 404 course; row untouched")


def step_propose():
    status, payload = propose("faculty_a", STATE["student_1_id"], COURSE_1, "A-")
    assert status == 201, f"propose returned {status}: {payload}"
    assert payload.get("status") == "Pending", f"correction is not Pending: {payload}"
    correction = payload["correction"]
    assert correction["courseCode"] == COURSE_1, f"unexpected course: {correction}"
    assert correction["oldGrade"] == "B" and correction["newGrade"] == "A-", f"unexpected values: {correction}"
    assert correction["justification"] == JUSTIFICATION, f"unexpected justification: {correction}"
    assert correction["proposerId"] == STATE["faculty_a_id"], f"proposer not from the token: {correction}"
    assert correction["proposerName"] == FACULTY_A_NAME, f"unexpected proposer name: {correction}"
    assert payload.get("ipfs_cid") and correction["ipfsCid"] == payload["ipfs_cid"], (
        f"evidence CID missing: {payload}"
    )
    assert str(payload.get("transaction_hash", "")).startswith("0x"), f"bad tx hash: {payload}"
    onchain_id = payload["onchain_proposal_id"]

    chain = chain_view(onchain_id)
    assert chain["status"] == "Pending" and chain["signatureCount"] == 1, f"unexpected chain state: {chain}"
    assert chain["field"] == f"grade:{COURSE_1}", f"unexpected on-chain field: {chain}"
    assert chain["oldValue"] == "B" and chain["newValue"] == "A-", f"unexpected on-chain values: {chain}"
    assert chain["ipfsCid"] == payload["ipfs_cid"], f"on-chain CID differs: {chain}"

    value, lock = grade_row(COURSE_1)
    assert value == "B", f"grade changed before approval: {value}"
    assert lock == local_id(onchain_id), f"row not locked by the new correction: lock={lock}"
    status, grades = api("GET", f"/grades/{STATE['student_1_id']}", token=AUTH["faculty_a"])
    assert status == 200, f"GET grades returned {status}: {grades}"
    row = next(entry for entry in grades["grades"] if entry["courseCode"] == COURSE_1)
    assert row["locked"] is True and row["gradeValue"] == "B", f"unexpected grade view: {row}"
    STATE["approved_id"] = onchain_id
    print(f"   correction {onchain_id} Pending on-chain; row locked by mirror {lock}")


def step_second_propose_locked():
    status, payload = propose("faculty_b", STATE["student_1_id"], COURSE_1, "A")
    assert_error(status, payload, 409, "row_lock", "grade_locked", "second propose")
    assert grade_row(COURSE_1)[1] == local_id(STATE["approved_id"]), "the lock moved"
    print(f"   rejected: {payload['message']}")


def step_race():
    barrier = threading.Barrier(2, timeout=10)
    cases = [("faculty_a", "B-"), ("faculty_b", "B")]
    results = [None, None]

    def worker(index: int):
        try:
            barrier.wait()
            who, new_grade = cases[index]
            results[index] = propose(who, STATE["student_1_id"], COURSE_2, new_grade)
        except Exception as exc:  # surfaced below as a failed result
            results[index] = (0, {"exception": repr(exc)})

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    # Each HTTP request has a finite timeout; finish both workers before
    # any assertion can trigger cleanup, preventing late fixture writes.
    for thread in threads:
        thread.join()
    statuses = sorted(result[0] for result in results)
    assert statuses == [201, 409], f"expected exactly one 201 and one 409, got {results}"
    winner_index = next(index for index, (status, _) in enumerate(results) if status == 201)
    winner = results[winner_index][1]
    loser = next(payload for status, payload in results if status == 409)
    assert loser.get("check") == "row_lock" and loser.get("error") == "grade_locked", f"unexpected 409: {loser}"
    STATE["race_id"] = winner["onchain_proposal_id"]
    STATE["race_who"], STATE["race_new_grade"] = cases[winner_index]
    assert winner["correction"]["proposerId"] == STATE[f"{STATE['race_who']}_id"], (
        f"race winner has the wrong proposer: {winner}"
    )
    assert winner["correction"]["newGrade"] == STATE["race_new_grade"], f"wrong winning grade: {winner}"
    value, lock = grade_row(COURSE_2)
    assert value == "C+" and lock == local_id(STATE["race_id"]), f"unexpected row after the race: {value}, {lock}"
    with APP.app_context():
        assert Proposal.query.filter_by(field=f"grade:{COURSE_2}").count() == 1, (
            "the race created more than one correction"
        )
    print(f"   one 201 (correction {STATE['race_id']}), one 409 grade_locked")


def step_wrong_approvers():
    onchain_id = STATE["approved_id"]
    for who in ("faculty_a", "admin", "student_1"):
        status, payload = api("POST", f"/grades/corrections/{onchain_id}/approve", token=AUTH[who])
        assert_error(status, payload, 403, "auth", "insufficient_role", f"{who} approve")
    status, payload = api("POST", f"/records/{onchain_id}/approve", token=AUTH["hod"])
    assert_error(status, payload, 400, "request", "use_grade_corrections", "generic grade approve")
    chain = chain_view(onchain_id)
    assert chain["status"] == "Pending" and chain["signatureCount"] == 1, f"a wrong approver signed: {chain}"
    assert grade_row(COURSE_1) == ("B", local_id(onchain_id)), f"row changed: {grade_row(COURSE_1)}"
    print("   FACULTY / ADMIN / STUDENT -> 403; chain still 1 signature")


def step_hod_approve():
    onchain_id = STATE["approved_id"]
    status, payload = api("POST", f"/grades/corrections/{onchain_id}/approve", token=AUTH["hod"],
                          json={"role": "exam_controller"})  # body role must be ignored
    assert status == 200, f"HOD approve returned {status}: {payload}"
    assert payload.get("status") == "Executed", f"correction did not execute: {payload}"
    assert payload.get("role") == "hod", f"signer role not from the token: {payload}"
    assert str(payload.get("signer", "")).lower() == HOD_ADDRESS.lower(), f"not the HOD signer: {payload}"
    assert payload.get("signature_count") == 2, f"expected 2 signatures: {payload}"
    assert payload.get("record_updated") is True, f"RecordUpdated missing: {payload}"
    assert str(payload.get("transaction_hash", "")).startswith("0x"), f"bad approval hash: {payload}"
    event = payload["event"]
    assert event["studentId"] == STATE["student_1_id"] and event["field"] == f"grade:{COURSE_1}", (
        f"approval executed a different record: {event}"
    )
    assert event["oldValue"] == "B" and event["newValue"] == "A-", f"wrong executed grades: {event}"
    assert payload["grade"]["gradeValue"] == "A-" and payload["grade"]["locked"] is False, (
        f"unexpected grade in the response: {payload['grade']}"
    )
    chain = chain_view(onchain_id)
    assert chain["status"] == "Executed" and chain["signatureCount"] == 2, f"unexpected chain state: {chain}"
    assert grade_row(COURSE_1) == ("A-", None), f"grade not applied: {grade_row(COURSE_1)}"
    print(f"   executed: tx={payload['transaction_hash']}; {COURSE_1} = A-, unlocked")


def step_read_grades():
    s1 = STATE["student_1_id"]
    status, payload = api("GET", f"/grades/{s1}", token=AUTH["student_1"])
    assert status == 200, f"student GET own grades returned {status}: {payload}"
    by_course = {entry["courseCode"]: entry for entry in payload["grades"]}
    assert set(by_course) == set(COURSE_CODES), f"unexpected courses: {sorted(by_course)}"
    assert by_course[COURSE_1]["gradeValue"] == "A-" and by_course[COURSE_1]["locked"] is False, (
        f"student does not see the new grade: {by_course[COURSE_1]}"
    )
    status, payload = api("GET", f"/grades/{s1}", token=AUTH["student_2"])
    assert_error(status, payload, 403, "student", "not_own_grades", "other student")
    for who in ("faculty_b", "hod", "admin"):
        status, payload = api("GET", f"/grades/{s1}", token=AUTH[who])
        assert status == 200 and payload["count"] == 2, f"{who} GET grades returned {status}: {payload}"
    print("   student 1 sees A-; student 2 -> 403; faculty / HOD / ADMIN read")


def step_reject():
    onchain_id = STATE["race_id"]
    path = f"/grades/corrections/{onchain_id}/reject"
    status, payload = api("POST", path, token=AUTH["hod"], json={})
    assert_error(status, payload, 400, "request", "missing_fields", "reject without reason")
    for who in ("faculty_a", "admin"):
        status, payload = api("POST", path, token=AUTH[who], json={"reason": "not allowed"})
        assert_error(status, payload, 403, "auth", "insufficient_role", f"{who} reject")
    assert grade_row(COURSE_2)[1] == local_id(onchain_id), "a refused reject released the lock"

    reason = "Marks were already moderated by the committee."
    status, payload = api("POST", path, token=AUTH["hod"], json={"reason": reason})
    assert status == 200, f"reject returned {status}: {payload}"
    assert payload.get("status") == "Rejected", f"unexpected status: {payload}"
    assert payload["correction"]["rejectionReason"] == reason, f"reason not stored: {payload}"
    assert grade_row(COURSE_2) == ("C+", None), f"reject did not just release the lock: {grade_row(COURSE_2)}"
    chain = chain_view(onchain_id)
    assert chain["status"] == "Pending" and chain["signatureCount"] == 1, (
        f"reject must be off-chain only: {chain}"
    )
    print(f"   correction {onchain_id} Rejected locally; lock cleared; on-chain still Pending")


def step_after_reject():
    onchain_id = STATE["race_id"]
    status, payload = api("POST", f"/grades/corrections/{onchain_id}/approve", token=AUTH["hod"])
    assert_error(status, payload, 409, "proposal", "correction_rejected", "approve after reject")
    status, payload = api("POST", f"/grades/corrections/{onchain_id}/reject", token=AUTH["hod"],
                          json={"reason": "second time"})
    assert_error(status, payload, 409, "proposal", "correction_rejected", "reject after reject")
    status, payload = api("POST", f"/records/{onchain_id}/approve", token=AUTH["hod"])
    assert_error(status, payload, 409, "proposal", "correction_rejected", "generic approve after reject")
    chain = chain_view(onchain_id)
    assert chain["status"] == "Pending" and chain["signatureCount"] == 1, f"approve reached the chain: {chain}"
    print("   approve / reject after reject -> 409; chain untouched")


def step_propose_again():
    status, payload = propose("faculty_b", STATE["student_1_id"], COURSE_2, "B+")
    assert status == 201, f"propose after reject returned {status}: {payload}"
    STATE["second_id"] = payload["onchain_proposal_id"]
    assert payload["correction"]["oldGrade"] == "C+", f"unexpected old grade: {payload['correction']}"
    assert grade_row(COURSE_2) == ("C+", local_id(STATE["second_id"])), f"row not relocked: {grade_row(COURSE_2)}"
    print(f"   correction {STATE['second_id']} Pending; row locked again")


def step_lists():
    expected = {
        STATE["approved_id"]: ("Executed", COURSE_1, "B", "A-", STATE["faculty_a_id"], FACULTY_A_NAME),
        STATE["race_id"]: (
            "Rejected", COURSE_2, "C+", STATE["race_new_grade"],
            STATE[f"{STATE['race_who']}_id"],
            FACULTY_A_NAME if STATE["race_who"] == "faculty_a" else FACULTY_B_NAME,
        ),
        STATE["second_id"]: ("Pending", COURSE_2, "C+", "B+", STATE["faculty_b_id"], FACULTY_B_NAME),
    }

    def ours(who: str) -> tuple[dict, list]:
        status, payload = api("GET", "/grades/corrections", token=AUTH[who])
        assert status == 200, f"{who} list returned {status}: {payload}"
        assert payload["count"] == len(payload["corrections"]), f"{who}: count mismatch"
        return {entry["onchainProposalId"]: entry for entry in payload["corrections"]
                if entry["courseCode"] in COURSE_CODES}, payload["corrections"]

    for who in ("hod", "admin"):
        rows, _ = ours(who)
        assert set(rows) == set(expected), f"{who} sees {sorted(rows)}, expected {sorted(expected)}"
        for onchain_id, (status, course, old, new, proposer_id, proposer_name) in expected.items():
            row = rows[onchain_id]
            assert row["status"] == status and row["courseCode"] == course, f"{who}: unexpected row {row}"
            assert row["oldGrade"] == old, f"{who}: unexpected old grade {row}"
            assert row["newGrade"] == new, f"{who}: unexpected new grade {row}"
            assert row["justification"] == JUSTIFICATION, f"{who}: unexpected justification {row}"
            assert row["proposerId"] == proposer_id and row["proposerName"] == proposer_name, (
                f"{who}: unexpected proposer {row}"
            )

    for who in ("faculty_a", "faculty_b"):
        own = {onchain_id for onchain_id, values in expected.items()
               if values[4] == STATE[f"{who}_id"]}
        rows, every = ours(who)
        assert set(rows) == own, f"{who} sees {sorted(rows)}, expected {sorted(own)}"
        others = [entry for entry in every if entry["proposerId"] != STATE[f"{who}_id"]]
        assert not others, f"{who} sees other faculty's corrections: {others}"

    status, payload = api("GET", "/grades/corrections", token=AUTH["student_1"])
    assert_error(status, payload, 403, "auth", "insufficient_role", "student list")
    print("   HOD/ADMIN: all 3; each faculty: exactly their own; student -> 403")


# --------------------------------------------------------------------------
# Cleanup - only this run's rows
# --------------------------------------------------------------------------
def cleanup() -> str:
    removed = 0
    with APP.app_context():
        user_ids = [u.id for u in User.query.filter(User.email.in_(ALL_EMAILS)).all()]
        removed += Proposal.query.filter(Proposal.field.in_(GRADE_FIELDS)).delete(synchronize_session=False)
        removed += Grade.query.filter(Grade.course_code.in_(COURSE_CODES)).delete(synchronize_session=False)
        for model in (FacultyProfile, StudentProfile):
            removed += model.query.filter(model.user_id.in_(user_ids)).delete(synchronize_session=False)
        removed += User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        removed += InstitutionWhitelist.query.filter_by(email=HOD_EMAIL).delete(synchronize_session=False)
        db.session.commit()
        assert User.query.filter(User.email.in_(ALL_EMAILS)).count() == 0
        assert Grade.query.filter(Grade.course_code.in_(COURSE_CODES)).count() == 0
        assert Proposal.query.filter(Proposal.field.in_(GRADE_FIELDS)).count() == 0
        assert InstitutionWhitelist.query.filter_by(email=HOD_EMAIL).count() == 0
    return f"removed {removed} local rows (on-chain proposals are permanent)"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
STEPS = [
    ("Preflight: backend /health", step_preflight),
    ("Auth setup: faculty A + B, HOD, ADMIN, two students", step_auth_setup),
    ("Seed Grade rows directly", step_seed_grades),
    ("Bad proposals -> 400 / 404, row untouched", step_bad_proposals),
    ("Faculty A proposes -> Pending on-chain, row locked", step_propose),
    ("Second propose on the locked row -> 409 grade_locked", step_second_propose_locked),
    ("Two simultaneous proposals -> one 201, one 409", step_race),
    ("FACULTY / ADMIN / STUDENT approve -> 403", step_wrong_approvers),
    ("HOD approves -> Executed, grade written, lock cleared", step_hod_approve),
    ("Student reads own grades; other student -> 403", step_read_grades),
    ("HOD rejects -> lock cleared, off-chain only", step_reject),
    ("Approve / reject after reject -> 409", step_after_reject),
    ("Propose again on the released row -> 201", step_propose_again),
    ("Correction lists per role", step_lists),
]


def main() -> int:
    print("=" * 60)
    print("   GRADE CORRECTION TESTS (test_grade_correction.py)")
    print("=" * 60)

    failed = None
    for index, (title, action) in enumerate(STEPS):
        print(f"\n{index}. {title}")
        try:
            action()
        except requests.RequestException as exc:
            print(f"   \u274c FAIL: cannot reach the backend at {BASE_URL} ({exc})")
            failed = (index, title)
            break
        except AssertionError as exc:
            print(f"   \u274c FAIL: {exc}")
            failed = (index, title)
            break
        except Exception as exc:
            print(f"   \u274c FAIL: unexpected {type(exc).__name__}: {exc}")
            failed = (index, title)
            break
        print("   \u2705 PASS")

    print("\nCleanup")
    cleanup_ok = True
    try:
        print(f"   \u2705 PASS: {cleanup()}")
    except Exception as exc:
        cleanup_ok = False
        print(f"   \u274c FAIL: cleanup failed: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    if failed is not None:
        print(f"   TEST RUN FAILED AT STEP {failed[0]}: {failed[1]}")
    elif not cleanup_ok:
        print("   TEST RUN FAILED DURING CLEANUP")
    else:
        print(f"   ALL GRADE CORRECTION TESTS PASSED ({len(STEPS)} steps)")
    print("=" * 60)
    return 0 if failed is None and cleanup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
