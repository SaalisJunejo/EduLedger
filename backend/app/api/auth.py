"""Authentication endpoints (Stage 0 Foundation).

- POST /api/v1/auth/register/hod     - registers an HOD against the institutional whitelist
- POST /api/v1/auth/register/faculty - registers a faculty member (domain + employee_id uniqueness)
- POST /api/v1/auth/register/student - legacy registration, explicit opt-in only
- POST /api/v1/auth/register/student/start, /verify - directory-gated registration
- GET  /api/v1/auth/register/student/dev-code/<roll> - explicit dev opt-in only
- POST /api/v1/auth/login            - logs in with email + password, issues an 8-hour JWT
- GET  /api/v1/auth/me               - returns the current user profile (requires JWT)

Every successful registration pins a non-sensitive profile document to IPFS
(Phase 1.6, decision D7) and stores its CID in ``profile_cid`` - on the role
profile for faculty and students, on the User row for an HOD (no profile
row exists). A pin failure never fails the registration: the CID stays null
and ``backfill_profile_cids.py`` fills it in later.
"""

import time

import jwt
from flask import current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db
from ..models import (
    DirectoryEntry,
    FacultyProfile,
    InstitutionWhitelist,
    Section,
    StudentProfile,
    StudentSection,
    User,
    UserRole,
)
from ..services import profile_metadata, registration_code
from ..services.auth import hash_password, require_auth, verify_password
from . import api_v1
from .errors import api_error


def _validate_institution_email(email: str) -> tuple[bool, str]:
    """Validate that the email address ends with the expected institutional domain."""
    domain = current_app.config.get("INSTITUTION_EMAIL_DOMAIN", "gcu.edu.pk").strip().lower()
    if "@" not in email:
        return False, domain
    _, email_domain = email.rsplit("@", 1)
    email_domain = email_domain.lower()
    if email_domain == domain or email_domain.endswith("." + domain):
        return True, domain
    return False, domain


def _store_profile_cid(user: User, profile, document: dict) -> str | None:
    """Pin ``document`` and persist the CID; None when IPFS or the write fails.

    The account row is already committed, so neither an unreachable IPFS nor a
    failed CID write may fail the registration - both leave ``profile_cid``
    null for ``backfill_profile_cids.py`` to pick up.
    """
    cid = profile_metadata.pin(user, document)
    if cid is None:
        return None

    # Faculty / students keep the CID on their profile row; an HOD has none.
    target = profile if profile is not None else user
    target.profile_cid = cid
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning(
            "profile CID %s could not be stored for user %s", cid, user.id
        )
        return None
    return cid


@api_v1.post("/auth/register/hod")
def register_hod():
    """Register an HOD user after auto-verifying against InstitutionWhitelist.

    Accepts:
        employee_id (str): Official employee ID
        department (str): Academic department name
        full_name (str): Full name of the HOD
        email (str): Institutional email address
        password (str): Plain text password to hash

    Returns:
        201: {"message": str, "user": dict, "verified": bool, "profile_cid": str | None}
        400: Missing or invalid request fields
        403: Not on institutional whitelist ({check: "whitelist", error: "not_whitelisted"})
        409: User already registered
    """
    body = request.get_json(silent=True) or {}
    employee_id = str(body.get("employee_id") or "").strip()
    department = str(body.get("department") or "").strip()
    full_name = str(body.get("full_name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    if not employee_id or not department or not full_name or not email or not password:
        return api_error(
            "request",
            "missing_fields",
            "employee_id, department, full_name, email, and password are required",
            status=400,
        )

    # 1. Lookup against InstitutionWhitelist
    whitelist_entry = InstitutionWhitelist.query.filter_by(
        employee_id=employee_id, email=email
    ).first()
    if not whitelist_entry:
        return api_error(
            "whitelist",
            "not_whitelisted",
            "employee_id and email do not match any institutional whitelist record",
            status=403,
        )

    # 2. Check if user with this email is already registered
    if User.query.filter_by(email=email).first():
        return api_error(
            "user",
            "email_already_registered",
            f"A user with email '{email}' is already registered",
            status=409,
        )

    # 3. Hash password with Argon2id and create User(role=HOD)
    user = User(
        role=UserRole.HOD,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        department=department,
        is_active=True,
    )
    db.session.add(user)
    db.session.commit()

    # 4. Pin the non-sensitive profile metadata (D7); an HOD has no profile row.
    profile_cid = _store_profile_cid(
        user, None, profile_metadata.build_document(user, employee_id=employee_id)
    )

    return (
        jsonify(
            {
                "message": "HOD registered and auto-verified successfully",
                "user": user.to_dict(),
                "verified": True,
                "profile_cid": profile_cid,
            }
        ),
        201,
    )


@api_v1.post("/auth/register/faculty")
def register_faculty():
    """Register a faculty member with institutional domain and employee ID checks.

    Accepts:
        employee_id (str): Unique employee ID
        full_name (str): Full name of the faculty member
        department (str): Academic department name
        email (str): Institutional email address
        password (str): Plain text password to hash

    Returns:
        201: {"message": str, "user": dict, "profile": dict, "profile_cid": str | None}
        400: Missing fields or invalid institutional email domain
        409: Email or employee_id already registered
    """
    body = request.get_json(silent=True) or {}
    employee_id = str(body.get("employee_id") or "").strip()
    full_name = str(body.get("full_name") or "").strip()
    department = str(body.get("department") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    if not employee_id or not full_name or not department or not email or not password:
        return api_error(
            "request",
            "missing_fields",
            "employee_id, full_name, department, email, and password are required",
            status=400,
        )

    # 1. Validate email domain against INSTITUTION_EMAIL_DOMAIN
    valid_domain, expected_domain = _validate_institution_email(email)
    if not valid_domain:
        return api_error(
            "email",
            "invalid_email_domain",
            f"Email must belong to institutional domain '@{expected_domain}'",
            status=400,
        )

    # 2. Check uniqueness of employee_id
    if FacultyProfile.query.filter_by(employee_id=employee_id).first():
        return api_error(
            "faculty",
            "employee_id_already_registered",
            f"Faculty member with employee ID '{employee_id}' is already registered",
            status=409,
        )

    # 3. Check uniqueness of email
    if User.query.filter_by(email=email).first():
        return api_error(
            "user",
            "email_already_registered",
            f"A user with email '{email}' is already registered",
            status=409,
        )

    # 4. Hash password and create User + FacultyProfile
    user = User(
        role=UserRole.FACULTY,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        department=department,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    faculty_profile = FacultyProfile(
        user_id=user.id,
        employee_id=employee_id,
    )
    db.session.add(faculty_profile)
    db.session.commit()

    # 5. Pin the non-sensitive profile metadata (D7).
    profile_cid = _store_profile_cid(
        user, faculty_profile, profile_metadata.build_document(user, employee_id=employee_id)
    )

    return (
        jsonify(
            {
                "message": "Faculty registered successfully",
                "user": user.to_dict(),
                "profile": faculty_profile.to_dict(),
                "profile_cid": profile_cid,
            }
        ),
        201,
    )


@api_v1.post("/auth/register/student")
def register_student():
    """Register a student with institutional domain and roll number checks.

    Accepts:
        full_name (str): Full name of the student
        roll_number (str): Unique roll number
        batch (str): Academic batch (e.g., '2022-2026')
        department (str): Academic department name
        email (str): Institutional email address
        password (str): Plain text password to hash

    Returns:
        201: {"message": str, "user": dict, "profile": dict, "profile_cid": str | None}
        400: Missing fields or invalid institutional email domain
        409: Email or roll_number already registered
    """
    if not current_app.config.get("GRANDFATHER_OPEN_REGISTRATION", False):
        return api_error("directory", "open_registration_disabled", "use directory registration start and verify", 403)
    current_app.logger.warning("Legacy open student registration used (GRANDFATHER_OPEN_REGISTRATION)")
    body = request.get_json(silent=True) or {}
    full_name = str(body.get("full_name") or "").strip()
    roll_number = str(body.get("roll_number") or "").strip()
    batch = str(body.get("batch") or "").strip()
    department = str(body.get("department") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    if not full_name or not roll_number or not batch or not department or not email or not password:
        return api_error(
            "request",
            "missing_fields",
            "full_name, roll_number, batch, department, email, and password are required",
            status=400,
        )

    # 1. Validate email domain against INSTITUTION_EMAIL_DOMAIN
    valid_domain, expected_domain = _validate_institution_email(email)
    if not valid_domain:
        return api_error(
            "email",
            "invalid_email_domain",
            f"Email must belong to institutional domain '@{expected_domain}'",
            status=400,
        )

    # 2. Check uniqueness of roll_number
    if StudentProfile.query.filter_by(roll_number=roll_number).first():
        return api_error(
            "student",
            "roll_number_already_registered",
            f"Student with roll number '{roll_number}' is already registered",
            status=409,
        )

    # 3. Check uniqueness of email
    if User.query.filter_by(email=email).first():
        return api_error(
            "user",
            "email_already_registered",
            f"A user with email '{email}' is already registered",
            status=409,
        )

    # 4. Hash password and create User + StudentProfile
    user = User(
        role=UserRole.STUDENT,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        department=department,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    student_profile = StudentProfile(
        user_id=user.id,
        roll_number=roll_number,
        batch=batch,
    )
    db.session.add(student_profile)
    db.session.commit()

    # 5. Pin the non-sensitive profile metadata (D7).
    profile_cid = _store_profile_cid(
        user,
        student_profile,
        profile_metadata.build_document(user, roll_number=roll_number, batch=batch),
    )

    return (
        jsonify(
            {
                "message": "Student registered successfully",
                "user": user.to_dict(),
                "profile": student_profile.to_dict(),
                "profile_cid": profile_cid,
            }
        ),
        201,
    )


LOGIN_TOKEN_EXPIRY_HOURS = 8


def _student_section(user):
    if user.role != UserRole.STUDENT:
        return None
    membership = db.session.get(StudentSection, user.id)
    if membership is None:
        return None
    section = membership.section
    return {"id": section.id, "programme": section.programme,
            "batchYear": section.batch_year, "letter": section.letter}


def _login_payload(user, message="Login successful"):
    now = int(time.time())
    claims = {
        "sub": str(user.id), "user_id": user.id,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "iat": now, "exp": now + LOGIN_TOKEN_EXPIRY_HOURS * 3600,
    }
    result = {"message": message,
              "token": jwt.encode(claims, current_app.config["JWT_SECRET"], algorithm="HS256"),
              "user": user.to_dict(), "expiresIn": LOGIN_TOKEN_EXPIRY_HOURS * 3600}
    if user.role == UserRole.STUDENT:
        result["section"] = _student_section(user)
    return result


@api_v1.post("/auth/login")
def login():
    """Authenticate with email and password, returning an 8-hour JWT.

    Accepts:
        email (str): Registered institutional email
        password (str): Account password

    Returns:
        200: {"message": str, "token": str, "user": dict, "expiresIn": int}
        400: Missing email or password
        401: Invalid credentials (generic for nonexistent email or wrong password)
        403: Account deactivated
    """
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    if not email or not password:
        return api_error(
            "request",
            "missing_fields",
            "email and password are required",
            status=400,
        )

    user = User.query.filter_by(email=email).first()
    if not user or not verify_password(password, user.password_hash):
        return api_error(
            "auth",
            "invalid_credentials",
            "Invalid email or password",
            status=401,
        )

    if not user.is_active:
        return api_error(
            "auth",
            "account_disabled",
            "Account is deactivated",
            status=403,
        )

    return jsonify(_login_payload(user)), 200


@api_v1.get("/auth/me")
@require_auth()
def get_current_user():
    """Return profile information of the currently authenticated user.

    Requires:
        Authorization: Bearer <token>

    Returns:
        200: {"user": dict, "profile": dict | None, "profile_cid": str | None}
        401: Missing, invalid, or expired token
        404: User not found in database
    """
    user = db.session.get(User, g.user_id)
    if not user:
        return api_error("auth", "user_not_found", "User account not found", status=404)

    profile_data = None
    if user.role == UserRole.FACULTY and user.faculty_profile:
        profile_data = user.faculty_profile.to_dict()
    elif user.role == UserRole.STUDENT and user.student_profile:
        profile_data = user.student_profile.to_dict()

    # The role profile owns the CID when there is one; otherwise the User row.
    profile_cid = profile_data["profileCid"] if profile_data else user.profile_cid

    return (
        jsonify(
            {
                "user": user.to_dict(),
                "profile": profile_data,
                "profile_cid": profile_cid,
                **({"section": _student_section(user)} if user.role == UserRole.STUDENT else {}),
            }
        ),
        200,
    )


def _directory_request():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("a JSON object is required")
    roll = body.get("roll_number")
    if not isinstance(roll, str) or not roll.strip() or len(roll.strip()) > 64:
        raise ValueError("roll_number must be nonempty text of at most 64 characters")
    return body, roll.strip()


def _directory_claim_error(entry):
    if entry is None:
        return api_error("directory", "directory_row_missing", "roll number is absent from the directory", 404)
    if entry.claimed_by is not None:
        return api_error("directory", "directory_already_claimed", "directory roll is already claimed", 409)
    return None


def _registration_error(exc):
    return api_error(exc.check, exc.code, exc.message, exc.status)


@api_v1.post("/auth/register/student/start")
def register_student_start():
    try:
        _, roll = _directory_request()
    except ValueError as exc:
        return api_error("request", "missing_fields", str(exc), 400)
    try:
        entry = DirectoryEntry.query.filter_by(roll_number=roll).with_for_update().first()
        error = _directory_claim_error(entry)
        if error is not None:
            db.session.rollback()
            return error
        ttl = registration_code.issue(entry)
        db.session.commit()
    except registration_code.RegistrationCodeError as exc:
        db.session.rollback()
        return _registration_error(exc)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("directory", "database_error", "registration could not be started", 500)
    return jsonify({"message": "Registration code sent to the directory address", "expiresIn": ttl}), 200


@api_v1.post("/auth/register/student/verify")
def register_student_verify():
    try:
        body, roll = _directory_request()
        password = body.get("password")
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise ValueError("password is required and must be at most 1024 characters")
        if "code" not in body:
            raise ValueError("code is required")
    except ValueError as exc:
        return api_error("request", "missing_fields", str(exc), 400)
    try:
        # Serialize claims with imports and other verifiers. Redis consume is
        # atomic too; only one request can create/claim this identity.
        entry = DirectoryEntry.query.filter_by(roll_number=roll).with_for_update().first()
        error = _directory_claim_error(entry)
        if error is not None:
            db.session.rollback()
            return error
        section = Section.query.filter_by(programme=entry.programme, batch_year=entry.batch_year,
                                          letter=entry.section_letter).with_for_update().first()
        if section is None or section.department != entry.department:
            db.session.rollback()
            return api_error("directory", "section_not_found", "directory section is missing or inconsistent", 409)
        if StudentProfile.query.filter_by(roll_number=roll).first():
            db.session.rollback()
            return api_error("student", "roll_number_already_registered", "roll number is already registered", 409)
        if User.query.filter(db.func.lower(User.email) == entry.university_email.lower()).first():
            db.session.rollback()
            return api_error("user", "email_already_registered", "directory email is already registered", 409)
        registration_code.consume(entry, body["code"])
        user = User(role=UserRole.STUDENT, email=entry.university_email.lower(),
                    password_hash=hash_password(password), full_name=entry.full_name,
                    department=entry.department, is_active=True)
        db.session.add(user)
        db.session.flush()
        profile = StudentProfile(user_id=user.id, roll_number=roll,
                                 batch=str(entry.batch_year), programme=entry.programme)
        db.session.add(profile)
        db.session.add(StudentSection(user_id=user.id, section_id=section.id))
        entry.claimed_by = user.id
        db.session.commit()
    except registration_code.RegistrationCodeError as exc:
        db.session.rollback()
        return _registration_error(exc)
    except IntegrityError:
        db.session.rollback()
        return api_error("directory", "master_data_conflict", "registration identity already exists; request a new code if needed", 409)
    except SQLAlchemyError:
        db.session.rollback()
        return api_error("directory", "database_error", "registration failed; request a new code", 500)
    _store_profile_cid(user, profile, profile_metadata.build_document(user, roll_number=roll, batch=profile.batch))
    return jsonify(_login_payload(user, "Student registered successfully")), 201


@api_v1.get("/auth/register/student/dev-code/<roll_number>")
def register_student_dev_code(roll_number):
    # Intentionally public only behind this explicit DEVELOPMENT opt-in.
    current_app.logger.warning("Student dev-code endpoint used roll=%s enabled=%s",
                               roll_number, current_app.config.get("ALLOW_DEV_CODE_ENDPOINT", False))
    if not current_app.config.get("ALLOW_DEV_CODE_ENDPOINT", False):
        return api_error("auth", "dev_code_endpoint_disabled", "development code endpoint is disabled", 404)
    entry = DirectoryEntry.query.filter_by(roll_number=roll_number).first()
    error = _directory_claim_error(entry)
    if error is not None:
        return error
    try:
        code = registration_code.dev_code(entry)
    except registration_code.RegistrationCodeError as exc:
        return _registration_error(exc)
    if code is None:
        return api_error("registration", "code_expired", "no current code in this development worker", 401)
    return jsonify({"code": code})


