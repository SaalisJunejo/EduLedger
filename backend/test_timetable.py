"""Standalone timetable integration checks using only Flask's test client.

Run from backend/: .\\venv\\Scripts\\python.exe test_timetable.py
Targets the new timetable API, with TIMETABLE_TIMEZONE=Asia/Karachi and
SESSION_START_GRACE_MINUTES=5. Requires the backend and openpyxl dependencies.
No external server, persistent database, scheduler, chain, or deployment is used.
Boundary checks patch the timetable service's clock, never scheduled timestamps.
"""

import os

# Force this before importing application/configuration or third-party modules.
os.environ["SCHEDULER_ENABLED"] = "false"

import csv
import io
import sys
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

API = "/api/v1"
UPLOAD = "/hod/timetable/upload"
COLUMNS = (
    "course_code", "faculty_employee_id", "room", "day_of_week",
    "start_time", "end_time",
)
CLASS_KEYS = {
    "id", "professorId", "room", "courseCode", "courseId",
    "startTime", "endTime", "dayOfWeek",
}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
KARACHI = timezone(timedelta(hours=5))
# Local Tuesday, just after midnight; UTC is still Monday. Today's 00:15
# class must stay on today even though its local start has already passed.
UPLOAD_LOCAL_NOW = datetime(2030, 1, 8, 1, 30, tzinfo=KARACHI)
UPLOAD_UTC_NOW = UPLOAD_LOCAL_NOW.astimezone(timezone.utc).replace(tzinfo=None)
FIXTURE_START = datetime(2035, 1, 2, 9, 30)
GRACE_KEY = "SESSION_START_GRACE_MINUTES"
ROUTES = (
    ("attendance-int", "/session/start", int),
    ("attendance-string", "/session/start", str),
    ("workload", "/faculty/session/start", int),
)


class TimetableTests:
    def __init__(self, app, database, models):
        self.app, self.db, self.m = app, database, models
        self.client = app.test_client()
        self.run_id = uuid4().hex
        self.accounts = {}
        self.auth = {}
        self.codes = set()
        self.attendance_ids = set()
        self.fixtures = {}

    def post(self, path, persona="hod", **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if persona is not None:
            headers["Authorization"] = f"Bearer {self.auth[persona]['token']}"
        response = self.client.post(API + path, headers=headers, **kwargs)
        payload = response.get_json(silent=True)
        assert isinstance(payload, dict), (
            f"POST {path}: expected JSON object, got {response.status_code}: "
            f"{response.get_data(as_text=True)}"
        )
        return response.status_code, payload

    @staticmethod
    def expect_error(result, status, check, error=None):
        actual, payload = result
        assert actual == status, f"Expected HTTP {status}, got {actual}: {payload}"
        assert payload.get("check") == check, f"Expected check {check!r}: {payload}"
        assert isinstance(payload.get("error"), str) and payload["error"], payload
        if error is not None:
            assert payload["error"] == error, f"Expected error {error!r}: {payload}"

    def setup_auth(self):
        with self.app.app_context():
            assert self.db.engine.url.get_backend_name() == "sqlite"
            assert self.db.engine.url.database == ":memory:", "Refusing a persistent database"
        assert self.app.testing and self.app.config["SCHEDULER_ENABLED"] is False
        domain = self.app.config["INSTITUTION_EMAIL_DOMAIN"].strip().lower()
        password = "TimetableTestPassword123!"
        for persona in ("hod", "owner", "stranger", "student", "admin"):
            account = {
                "email": f"timetable.{persona}.{self.run_id}@{domain}",
                "password": password,
                "full_name": f"Timetable Test {persona.title()}",
                "department": "Computer Science",
            }
            if persona == "student":
                account.update(roll_number=f"TT-ST-{self.run_id}", batch="2026-2030")
            else:
                account["employee_id"] = f"TT-{persona.upper()}-{self.run_id}"
            # Track credentials before registration so partial setup is cleanable.
            self.accounts[persona] = account
            if persona == "hod":
                with self.app.app_context():
                    self.db.session.add(self.m.InstitutionWhitelist(
                        employee_id=account["employee_id"], email=account["email"],
                        name=account["full_name"], department=account["department"],
                    ))
                    self.db.session.commit()
            registration_role = persona if persona in ("hod", "student") else "faculty"
            status, payload = self.post(
                f"/auth/register/{registration_role}", persona=None, json=account,
            )
            assert status == 201, f"{persona} registration: {status}: {payload}"
            user_id = payload["user"]["id"]
            assert payload["user"]["role"] == registration_role.upper(), payload
            if persona == "admin":
                # There is no public ADMIN registration: promote only our own
                # registered fixture, then obtain its token through real login.
                with self.app.app_context():
                    self.db.session.get(self.m.User, user_id).role = self.m.UserRole.ADMIN
                    self.db.session.commit()
            status, payload = self.post(
                "/auth/login", persona=None,
                json={"email": account["email"], "password": password},
            )
            expected_role = "ADMIN" if persona == "admin" else registration_role.upper()
            assert status == 200, f"{persona} login: {status}: {payload}"
            assert payload["user"]["id"] == user_id, payload
            assert payload["user"]["role"] == expected_role, payload
            assert isinstance(payload.get("token"), str) and payload["token"], payload
            self.auth[persona] = {"id": user_id, "token": payload["token"]}

    def row(self, label, day="Tuesday", start="09:30", end="10:30", **changes):
        code = f"TT-{self.run_id}-{label}"
        self.codes.add(code)
        result = dict(zip(COLUMNS, (
            code, self.accounts["owner"]["employee_id"], "Room 204", day, start, end,
        )))
        result.update(changes)
        return result

    @staticmethod
    def csv_bytes(rows, columns=COLUMNS):
        text = io.StringIO(newline="")
        writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return text.getvalue().encode("utf-8")

    @staticmethod
    def xlsx_bytes(rows, columns=COLUMNS):
        from openpyxl import Workbook

        workbook = Workbook()
        output = io.BytesIO()
        try:
            sheet = workbook.active
            sheet.append(list(columns))
            for row in rows:
                sheet.append([row.get(column) for column in columns])
            # Real time cells round-trip through openpyxl as datetime.time.
            for cells in sheet.iter_rows(min_row=2):
                for cell in cells:
                    if isinstance(cell.value, time):
                        cell.number_format = "hh:mm:ss"
            workbook.save(output)
            return output.getvalue()
        finally:
            workbook.close()
            output.close()

    def upload(self, rows, extension="csv", persona="hod", columns=COLUMNS):
        contents = (self.csv_bytes(rows, columns) if extension == "csv"
                    else self.xlsx_bytes(rows, columns))
        with patch("app.api.timetable.utcnow", return_value=UPLOAD_UTC_NOW):
            return self.post(
                UPLOAD, persona=persona, content_type="multipart/form-data",
                data={"file": (io.BytesIO(contents), f"timetable.{extension}")},
            )

    def class_count(self):
        with self.app.app_context():
            return self.m.ScheduledClass.query.count()

    def assert_upload(self, result, created, rejected_rows=()):
        status, payload = result
        assert status == (201 if created else 200), f"Unexpected upload status {status}: {payload}"
        assert type(payload.get("rows_created")) is int, payload
        assert payload["rows_created"] == created, payload
        assert payload.get("timezone") == "Asia/Karachi", payload
        classes = payload.get("classes")
        assert isinstance(classes, list) and len(classes) == created, payload
        assert all(isinstance(item, dict) for item in classes), payload
        rejected = payload.get("rows_rejected")
        assert isinstance(rejected, list), payload
        for item in rejected:
            assert isinstance(item, dict) and type(item.get("row")) is int, item
            reasons = item.get("reasons")
            assert isinstance(reasons, list) and reasons, item
            assert all(isinstance(reason, str) and reason.strip() for reason in reasons), item
        assert sorted(item["row"] for item in rejected) == sorted(rejected_rows), payload
        return payload

    @staticmethod
    def expected_utc(day, value):
        if isinstance(value, str):
            value = time.fromisoformat(value)
        today = UPLOAD_LOCAL_NOW.date()
        target = today + timedelta(days=(day - today.weekday()) % 7)
        return datetime.combine(target, value, tzinfo=KARACHI).astimezone(
            timezone.utc,
        ).replace(tzinfo=None)

    @staticmethod
    def assert_utc_json(value, expected):
        assert isinstance(value, str), f"Expected timestamp string, got {value!r}"
        actual = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert actual.tzinfo is not None and actual.utcoffset() == timedelta(0), value
        assert actual.replace(tzinfo=None) == expected, f"Expected UTC {expected}, got {value}"

    def assert_class(self, item, row, day):
        assert CLASS_KEYS <= set(item), f"Missing class keys: {item}"
        assert type(item["id"]) is int and item["id"] > 0, item
        assert item["professorId"] == self.auth["owner"]["id"], item
        assert item["room"] == row["room"], item
        assert item["courseCode"] == item["courseId"] == row["course_code"], item
        assert type(item["dayOfWeek"]) is int and item["dayOfWeek"] == day, item
        start = self.expected_utc(day, row["start_time"])
        end = self.expected_utc(day, row["end_time"])
        self.assert_utc_json(item["startTime"], start)
        self.assert_utc_json(item["endTime"], end)
        with self.app.app_context():
            saved = self.db.session.get(self.m.ScheduledClass, item["id"])
            assert saved is not None, f"Class {item['id']} was not persisted"
            assert saved.professor_id == self.auth["owner"]["id"]
            assert saved.course_code == saved.course_id == row["course_code"]
            assert saved.room == row["room"] and saved.day_of_week == day
            assert saved.start_time == start and saved.start_time.tzinfo is None
            assert saved.end_time == end and saved.end_time.tzinfo is None
            assert saved.to_dict() == item, f"Upload differs from model serialization: {item}"

    def successful_csv(self):
        rows, expected_days = [], {}
        for day, name in enumerate(DAYS):
            for kind, value in (("name", name.swapcase()), ("number", str(day))):
                row = self.row(f"CSV-{kind}-{day}", value, "00:15", "01:00:07")
                rows.append(row)
                expected_days[row["course_code"]] = day
        before = self.class_count()
        payload = self.assert_upload(self.upload(rows), len(rows))
        assert self.class_count() == before + len(rows)
        by_code = {item["courseCode"]: item for item in payload["classes"]}
        assert set(by_code) == set(expected_days), payload
        for row in rows:
            self.assert_class(by_code[row["course_code"]], row, expected_days[row["course_code"]])
        # Numeric weekdays and weekday names must resolve to identical instants.
        for day in range(7):
            values = [by_code[row["course_code"]]["startTime"] for row in rows
                      if expected_days[row["course_code"]] == day]
            assert len(set(values)) == 1, values

    def successful_xlsx(self):
        rows = [self.row(f"XLSX-time-{day}", day, time(0, 15, 11), time(1, 0, 19))
                for day in range(7)]
        rows.extend((
            self.row("XLSX-text-short", "tUeSdAy", "09:30", "10:30"),
            self.row("XLSX-text-seconds", "Sunday", "09:30:17", "10:30:23"),
        ))
        before = self.class_count()
        payload = self.assert_upload(self.upload(rows, "xlsx"), len(rows))
        assert self.class_count() == before + len(rows)
        by_code = {item["courseCode"]: item for item in payload["classes"]}
        assert set(by_code) == {row["course_code"] for row in rows}, payload
        for row, day in zip(rows, list(range(7)) + [1, 6]):
            self.assert_class(by_code[row["course_code"]], row, day)

    def partial_success(self):
        for extension in ("csv", "xlsx"):
            rows = [
                self.row(f"PART-{extension}-good-1"),
                self.row(f"PART-{extension}-unknown", faculty_employee_id=f"UNKNOWN-{self.run_id}"),
                self.row(f"PART-{extension}-good-2", "Sunday", "13:00:09", "14:00:11"),
                self.row(f"PART-{extension}-hod", faculty_employee_id=self.accounts["hod"]["employee_id"]),
                self.row(f"PART-{extension}-admin", faculty_employee_id=self.accounts["admin"]["employee_id"]),
            ]
            before = self.class_count()
            payload = self.assert_upload(self.upload(rows, extension), 2, (3, 5, 6))
            assert self.class_count() == before + 2
            for rejection in payload["rows_rejected"]:
                reason_text = "must belong to a FACULTY user" if rejection["row"] == 6 else "unknown faculty_employee_id"
                assert any(reason_text.lower() in reason.lower()
                           for reason in rejection["reasons"]), rejection
            by_code = {item["courseCode"]: item for item in payload["classes"]}
            assert set(by_code) == {rows[0]["course_code"], rows[2]["course_code"]}, payload
            self.assert_class(by_code[rows[0]["course_code"]], rows[0], 1)
            self.assert_class(by_code[rows[2]["course_code"]], rows[2], 6)

    def upload_permissions(self):
        row = self.row("PERMISSIONS")
        before = self.class_count()
        contents = self.csv_bytes([row])
        for persona, status, error in (
            (None, 401, "missing_token"), ("owner", 403, "insufficient_role"),
            ("stranger", 403, "insufficient_role"), ("student", 403, "insufficient_role"),
            ("admin", 403, "insufficient_role"),
        ):
            self.expect_error(self.post(
                UPLOAD, persona=persona, content_type="multipart/form-data",
                data={"file": (io.BytesIO(contents), "permission.csv")},
            ), status, "auth", error)
            assert self.class_count() == before, "Unauthorized upload created a class"

    def malformed_files_and_headers(self):
        before = self.class_count()
        self.expect_error(self.post(UPLOAD, data={}, content_type="multipart/form-data"),
                          400, "timetable")
        samples = (
            ("bad.txt", self.csv_bytes([self.row("BAD-extension")])),
            ("empty.csv", b""),
            ("corrupt.xlsx", b"This is not an Excel workbook"),
        )
        for filename, contents in samples:
            self.expect_error(self.post(
                UPLOAD, content_type="multipart/form-data",
                data={"file": (io.BytesIO(contents), filename)},
            ), 400, "timetable")
            assert self.class_count() == before, f"Invalid file {filename} created classes"
        for extension in ("csv", "xlsx"):
            for missing in COLUMNS:
                columns = tuple(column for column in COLUMNS if column != missing)
                self.expect_error(self.upload(
                    [self.row(f"BAD-header-{extension}-{missing}")], extension, columns=columns,
                ), 400, "timetable")
                assert self.class_count() == before, f"Missing header {missing} created classes"

    def malformed_rows(self):
        rows = [self.row(f"BAD-day-{index}", day) for index, day in enumerate(("Funday", -1, 7))]
        rows.extend((
            self.row("BAD-time-format", start="9am"),
            self.row("BAD-time-range", start="25:00"),
            self.row("BAD-end-format", end="not-a-time"),
            self.row("BAD-equal", start="09:30", end="09:30"),
            self.row("BAD-reversed", start="10:30", end="09:30"),
            self.row("BAD-overnight", start="23:00", end="01:00"),
            self.row("BAD-unknown-only", faculty_employee_id=f"UNKNOWN-{self.run_id}"),
        ))
        for column in COLUMNS:
            row = self.row(f"BAD-blank-{column}")
            row[column] = ""
            rows.append(row)
        for extension in ("csv", "xlsx"):
            before = self.class_count()
            payload = self.assert_upload(self.upload(rows, extension), 0, range(2, len(rows) + 2))
            assert self.class_count() == before, f"Invalid {extension} rows were persisted"
            unknown = next(item for item in payload["rows_rejected"] if item["row"] == 11)
            assert any("unknown faculty_employee_id" in reason.lower()
                       for reason in unknown["reasons"]), unknown

    def seed_gate_fixtures(self):
        with self.app.app_context():
            for legacy in (False, True):
                for label, _, _ in ROUTES:
                    course = f"TT-{self.run_id}-GATE-{label}-{'old' if legacy else 'new'}"
                    self.codes.add(course)
                    kwargs = {
                        "professor_id": self.auth["owner"]["id"], "room": "Gate Room",
                        "start_time": FIXTURE_START,
                        "end_time": None if legacy else FIXTURE_START + timedelta(hours=1),
                        "day_of_week": None if legacy else FIXTURE_START.weekday(),
                    }
                    # Exercise the legacy course_id constructor synonym too.
                    kwargs["course_id" if legacy else "course_code"] = course
                    saved = self.m.ScheduledClass(**kwargs)
                    self.db.session.add(saved)
                    self.db.session.flush()
                    self.fixtures[(legacy, label)] = saved.id
                    assert saved.course_code == saved.course_id == course
                    assert CLASS_KEYS <= set(saved.to_dict())
                    if legacy:
                        assert saved.to_dict()["endTime"] is None
                        assert saved.to_dict()["dayOfWeek"] is None
            self.db.session.commit()

    def sessions_snapshot(self):
        with self.app.app_context():
            attendance = tuple((row.id, row.class_id, row.is_active, row.current_nonce)
                               for row in self.m.AttendanceSession.query.order_by(self.m.AttendanceSession.id))
            workload = tuple((row.id, row.class_id, row.attendance_session_id,
                              row.conductor_id, row.is_active)
                             for row in self.m.WorkloadSession.query.order_by(self.m.WorkloadSession.id))
            return attendance, workload

    def schedule_snapshot(self, class_id):
        with self.app.app_context():
            saved = self.db.session.get(self.m.ScheduledClass, class_id)
            return (saved.start_time, saved.end_time, saved.day_of_week,
                    saved.professor_id, saved.course_code)

    def start_at(self, route, class_id, now, persona="owner", spoof_id=None):
        _, path, encode_id = route
        body = {"class_id": encode_id(class_id)}
        if spoof_id is not None:
            body["professor_id"] = spoof_id
        scheduled_before = self.schedule_snapshot(class_id)
        with patch("app.services.timetable.utcnow", return_value=now) as clock:
            result = self.post(path, persona=persona, json=body)
            # Ownership/auth failures can precede the clock; owner requests must
            # actually consult the patched helper clock, not a route-local now.
            if persona == "owner":
                assert clock.called, f"{path} bypassed the timetable clock"
                clock.assert_called_with()
        assert self.schedule_snapshot(class_id) == scheduled_before, "Start changed schedule timestamps"
        session_id = result[1].get("session", {}).get("id")
        if isinstance(session_id, int):
            self.attendance_ids.add(session_id)
        return result

    def start_permissions(self):
        for route in ROUTES:
            label, _, _ = route
            class_id = self.fixtures[(False, label)]
            for persona in (None, "hod", "student", "admin", "stranger"):
                before = self.sessions_snapshot()
                result = self.start_at(
                    route, class_id, FIXTURE_START, persona=persona,
                    spoof_id=self.auth["owner"]["id"],
                )
                if persona == "stranger":
                    self.expect_error(result, 403, "professor", "not_class_professor")
                else:
                    self.expect_error(result, 401 if persona is None else 403, "auth",
                                      "missing_token" if persona is None else "insufficient_role")
                assert self.sessions_snapshot() == before, "Rejected start changed session rows"

    def assert_started(self, result, route, class_id, before):
        status, payload = result
        assert status == 201, f"{route[0]} start: expected 201, got {status}: {payload}"
        session = payload.get("session") or {}
        assert type(session.get("id")) is int and session.get("is_active") is True, payload
        after = self.sessions_snapshot()
        assert len(after[0]) == len(before[0]) + 1, f"Start did not create exactly one attendance row: {payload}"
        is_workload = route[0] == "workload"
        assert len(after[1]) == len(before[1]) + int(is_workload), payload
        with self.app.app_context():
            saved = self.db.session.get(self.m.AttendanceSession, session["id"])
            assert saved is not None and saved.is_active
            if is_workload:
                item = payload.get("workload_session") or {}
                assert item.get("classId") == class_id, payload
                assert item.get("conductorId") == self.auth["owner"]["id"], payload
                assert item.get("attendanceSessionId") == saved.id, payload
                workload = self.db.session.get(self.m.WorkloadSession, item.get("id"))
                assert workload is not None and workload.is_active
                assert workload.class_id == class_id
                assert workload.conductor_id == self.auth["owner"]["id"]
                assert workload.attendance_session_id == saved.id

    def gate_boundaries(self, legacy=False):
        start = FIXTURE_START
        for route in ROUTES:
            class_id = self.fixtures[(legacy, route[0])]
            before = self.sessions_snapshot()
            result = self.start_at(route, class_id, start - timedelta(minutes=5, seconds=1))
            self.expect_error(result, 403, "schedule", "too_early")
            assert self.sessions_snapshot() == before, "Early start created or changed a session"
            for label, now in (
                ("exact grace", start - timedelta(minutes=5)),
                ("exact start", start), ("after start", start + timedelta(seconds=1)),
            ):
                before = self.sessions_snapshot()
                result = self.start_at(route, class_id, now, spoof_id=self.auth["stranger"]["id"])
                self.assert_started(result, route, class_id, before)
                print(f"    PASS: {route[0]}, {'absolute/null-day' if legacy else 'timetable'}, {label}")
            # A denial must also leave already-active sessions untouched.
            before = self.sessions_snapshot()
            self.expect_error(self.start_at(route, class_id, start - timedelta(minutes=5, seconds=1)),
                              403, "schedule", "too_early")
            assert self.sessions_snapshot() == before, "Early retry deactivated an existing session"
            before = self.sessions_snapshot()
            self.expect_error(self.start_at(route, class_id, start, persona="stranger",
                                            spoof_id=self.auth["owner"]["id"]),
                              403, "professor", "not_class_professor")
            assert self.sessions_snapshot() == before, "Non-owner changed an existing session"

    def uploaded_class_gates(self):
        for route in ROUTES:
            row = self.row(f"LIVE-{route[0]}", "Tuesday", "00:15", "01:00")
            payload = self.assert_upload(self.upload([row]), 1)
            item = payload["classes"][0]
            self.assert_class(item, row, 1)
            start = self.expected_utc(1, row["start_time"])
            class_id = item["id"]
            before = self.sessions_snapshot()
            self.expect_error(self.start_at(route, class_id, start - timedelta(minutes=5, seconds=1)),
                              403, "schedule", "too_early")
            assert self.sessions_snapshot() == before
            for now in (start, start + timedelta(seconds=1)):
                before = self.sessions_snapshot()
                self.assert_started(self.start_at(route, class_id, now), route, class_id, before)

    def substitute_start_gate(self):
        row = self.row("SUB-GATE")
        item = self.assert_upload(self.upload([row]), 1)["classes"][0]
        start = self.expected_utc(1, row["start_time"])
        status, payload = self.post("/faculty/substitute/issue", persona="owner", json={
            "class_id": item["id"], "substitute_id": self.auth["stranger"]["id"],
        })
        assert status == 201, payload
        token = payload["substitute_token"]
        before = self.sessions_snapshot()
        with patch("app.services.timetable.utcnow", return_value=start - timedelta(minutes=5, seconds=1)):
            self.expect_error(self.post("/faculty/substitute/redeem", persona="stranger", json={"token": token}),
                              403, "schedule", "too_early")
        assert self.sessions_snapshot() == before
        with self.app.app_context():
            saved = self.m.SubstituteToken.query.filter_by(token=token).one()
            assert saved.redeemed_at is None and saved.redeemed_by is None
        with patch("app.services.timetable.utcnow", return_value=start):
            status, payload = self.post("/faculty/substitute/redeem", persona="stranger", json={"token": token})
        assert status == 201 and payload["status"] == "redeemed", payload
        assert payload["workload_session"]["conductorId"] == self.auth["stranger"]["id"], payload
        self.attendance_ids.add(payload["session"]["id"])

    def configurable_grace(self):
        self.app.config[GRACE_KEY] = 0
        try:
            for route in ROUTES:
                class_id = self.fixtures[(False, route[0])]
                before = self.sessions_snapshot()
                self.expect_error(self.start_at(route, class_id, FIXTURE_START - timedelta(seconds=1)),
                                  403, "schedule", "too_early")
                assert self.sessions_snapshot() == before, "Zero-grace rejection changed sessions"
                result = self.start_at(route, class_id, FIXTURE_START)
                self.assert_started(result, route, class_id, before)
        finally:
            self.app.config[GRACE_KEY] = 5

    def cleanup(self):
        """Delete only this run's owned rows, children first; never drop tables."""
        with self.app.app_context():
            try:
                self.db.session.rollback()
                emails = [account["email"] for account in self.accounts.values()]
                users = self.m.User.query.filter(self.m.User.email.in_(emails)).all()
                user_ids = [user.id for user in users]
                # course_id exists on both old and new models, so cleanup also
                # works when a missing timetable implementation fails a step.
                classes = self.m.ScheduledClass.query.filter(
                    self.m.ScheduledClass.course_id.in_(self.codes)
                    | self.m.ScheduledClass.professor_id.in_(user_ids),
                ).all()
                class_ids = [row.id for row in classes]
                linked = self.m.WorkloadSession.query.filter(
                    self.m.WorkloadSession.class_id.in_(class_ids),
                ).all()
                attendance_ids = self.attendance_ids | {row.attendance_session_id for row in linked}
                attendance_keys = [str(value) for value in class_ids]
                attendance_keys += [f"workload-{value}" for value in class_ids]
                attendance_keys += list(self.codes)
                self.m.WorkloadSession.query.filter(
                    self.m.WorkloadSession.class_id.in_(class_ids),
                ).delete(synchronize_session=False)
                self.m.AttendanceSession.query.filter(
                    self.m.AttendanceSession.id.in_(attendance_ids)
                    | self.m.AttendanceSession.class_id.in_(attendance_keys),
                ).delete(synchronize_session=False)
                self.m.SubstituteToken.query.filter(
                    self.m.SubstituteToken.class_id.in_(class_ids),
                ).delete(synchronize_session=False)
                self.m.ScheduledClass.query.filter(
                    self.m.ScheduledClass.id.in_(class_ids),
                ).delete(synchronize_session=False)
                for model in (self.m.StudentProfile, self.m.FacultyProfile):
                    model.query.filter(model.user_id.in_(user_ids)).delete(synchronize_session=False)
                self.m.User.query.filter(self.m.User.id.in_(user_ids)).delete(synchronize_session=False)
                hod = self.accounts.get("hod")
                if hod:
                    self.m.InstitutionWhitelist.query.filter_by(
                        employee_id=hod["employee_id"], email=hod["email"],
                    ).delete(synchronize_session=False)
                self.db.session.commit()
            except Exception:
                self.db.session.rollback()
                raise
            finally:
                try:
                    self.db.session.remove()
                finally:
                    for engine in self.db.engines.values():
                        engine.dispose()


def main():
    suite = None
    failed = False
    passed = 0
    print("TIMETABLE UPLOAD AND START-GATE TESTS (isolated SQLite memory)")
    try:
        # Application creation and imports happen only when explicitly run.
        from app import create_app, models
        from app.config import TestingConfig
        from app.extensions import db

        # The factory initializes SQLAlchemy immediately: isolate BEFORE it runs.
        with patch.multiple(TestingConfig, DB_URL="sqlite:///:memory:", SCHEDULER_ENABLED=False):
            app = create_app("testing")
        app.config.update(TIMETABLE_TIMEZONE="Asia/Karachi", SESSION_START_GRACE_MINUTES=5,
                          SCHEDULER_ENABLED=False)
        suite = TimetableTests(app, db, models)
        steps = (
            ("Inline registration/login with real Bearer tokens", suite.setup_auth),
            ("CSV weekdays, both time formats, inclusive local date and exact UTC", suite.successful_csv),
            ("XLSX numeric weekdays, text times and datetime.time cells", suite.successful_xlsx),
            ("CSV/XLSX partial success, unknown faculty and spreadsheet row numbers", suite.partial_success),
            ("HOD-only uploads; anonymous/FACULTY/STUDENT/ADMIN denied", suite.upload_permissions),
            ("Malformed files and every missing required header", suite.malformed_files_and_headers),
            ("Malformed rows: zero created returns 200 with rejection reasons", suite.malformed_rows),
            ("Immutable timetable and null-day absolute fixtures; course_id synonym", suite.seed_gate_fixtures),
            ("Both start routes enforce FACULTY ownership, ignoring spoofed body IDs", suite.start_permissions),
            ("Timetable boundary clocks on both routes, attendance int and string", suite.gate_boundaries),
            ("Null-day absolute compatibility, nullable end, same boundary clocks",
             lambda: suite.gate_boundaries(legacy=True)),
            ("Uploaded local-midnight classes gate both routes at the parsed UTC instant", suite.uploaded_class_gates),
            ("Substitute redemption cannot bypass the gate or consume an early token", suite.substitute_start_gate),
            ("Changing configured grace to zero changes both route gates", suite.configurable_grace),
        )
        for index, (title, action) in enumerate(steps, start=1):
            print(f"\n{index:02d}. {title}")
            try:
                action()
            except Exception as exc:
                print(f"    FAIL: {type(exc).__name__}: {exc}")
                failed = True
                break
            print("    PASS")
            passed += 1
    except Exception as exc:
        print(f"FAIL: setup: {type(exc).__name__}: {exc}")
        failed = True
    finally:
        if suite is not None:
            try:
                suite.cleanup()
                print("\nPASS: owned-row cleanup; session removed and memory engine disposed")
            except Exception as exc:
                print(f"\nFAIL: cleanup: {type(exc).__name__}: {exc}")
                failed = True
    print(f"\n{'FAIL' if failed else 'PASS'}: {passed} test steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
