"""Authentication endpoints (Stage 0 Foundation).

- POST /api/v1/auth/register/hod     - registers an HOD against the institutional whitelist
- POST /api/v1/auth/register/faculty - registers a faculty member (domain + employee_id uniqueness)
- POST /api/v1/auth/register/student - registers a student (domain + roll_number uniqueness)
- POST /api/v1/auth/login            - logs in with email + password, issues an 8-hour JWT
- GET  /api/v1/auth/me               - returns the current user profile (requires JWT)
"""

import time

import jwt
from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import (
    FacultyProfile,
    InstitutionWhitelist,
    StudentProfile,
    User,
    UserRole,
)
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
        201: {"message": str, "user": dict, "verified": bool}
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

    return (
        jsonify(
            {
                "message": "HOD registered and auto-verified successfully",
                "user": user.to_dict(),
                "verified": True,
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
        201: {"message": str, "user": dict, "profile": dict}
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

    return (
        jsonify(
            {
                "message": "Faculty registered successfully",
                "user": user.to_dict(),
                "profile": faculty_profile.to_dict(),
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
        201: {"message": str, "user": dict, "profile": dict}
        400: Missing fields or invalid institutional email domain
        409: Email or roll_number already registered
    """
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

    return (
        jsonify(
            {
                "message": "Student registered successfully",
                "user": user.to_dict(),
                "profile": student_profile.to_dict(),
            }
        ),
        201,
    )


LOGIN_TOKEN_EXPIRY_HOURS = 8


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

    now = int(time.time())
    expires_at = now + LOGIN_TOKEN_EXPIRY_HOURS * 3600
    payload = {
        "sub": str(user.id),
        "user_id": user.id,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")

    return (
        jsonify(
            {
                "message": "Login successful",
                "token": token,
                "user": user.to_dict(),
                "expiresIn": LOGIN_TOKEN_EXPIRY_HOURS * 3600,
            }
        ),
        200,
    )


@api_v1.get("/auth/me")
@require_auth()
def get_current_user():
    """Return profile information of the currently authenticated user.

    Requires:
        Authorization: Bearer <token>

    Returns:
        200: {"user": dict, "profile": dict | None}
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

    return (
        jsonify(
            {
                "user": user.to_dict(),
                "profile": profile_data,
            }
        ),
        200,
    )


