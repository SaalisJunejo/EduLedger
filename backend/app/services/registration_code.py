"""Directory-bound codes: hash-only Redis storage, atomic attempts and consume.

The opt-in dev helper uses a bounded process-local plaintext cache; Redis
never holds plaintext or recoverable ciphertext. Dev retrieval must hit the
worker which issued the code. Production should use SMTP and disable it.
"""

import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

import redis
from flask import current_app

ATTEMPT_WINDOW_SECONDS = 900
_ATTEMPT_SCRIPT = """
local n = tonumber(redis.call('GET', KEYS[1]) or '0')
if n >= tonumber(ARGV[1]) then return tonumber(ARGV[1]) + 1 end
n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return n
"""
_CONSUME_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_dev_lock = threading.Lock()
_dev_codes = {}


class RegistrationCodeError(Exception):
    def __init__(self, check, code, message, status):
        super().__init__(message)
        self.check, self.code, self.message, self.status = check, code, message, status


def redis_client():
    url = current_app.config.get("REDIS_URL")
    if not url:
        raise RegistrationCodeError("registration", "code_store_unavailable", "Redis is required for registration", 503)
    return redis.Redis.from_url(url, decode_responses=True,
                               socket_timeout=2, socket_connect_timeout=2)


def code_key(roll):
    # Hash the roll to keep arbitrary delimiters and personal data out of keys.
    return "eduledger:registration:code:" + hashlib.sha256(roll.encode()).hexdigest()


def attempts_key(roll):
    return "eduledger:registration:attempts:" + hashlib.sha256(roll.encode()).hexdigest()


def _digest(entry, code):
    # Bind the hash to the roll AND directory data. Re-imported identities
    # cannot use a code delivered to the old email or for another roll.
    message = json.dumps([entry.roll_number, entry.full_name, entry.university_email,
                          entry.programme, entry.batch_year, entry.section_letter,
                          entry.department, code], ensure_ascii=True, separators=(",", ":"))
    return hmac.new(current_app.config["SECRET_KEY"].encode(), message.encode(), hashlib.sha256).hexdigest()


def _store_error(exc):
    current_app.logger.error("Registration code Redis operation failed (%s)", type(exc).__name__)
    return RegistrationCodeError("registration", "code_store_unavailable", "registration code store unavailable", 503)


def _dev_cache_key(roll):
    return (current_app.config.get("REDIS_URL"), code_key(roll))


def _forget_dev(roll):
    with _dev_lock:
        _dev_codes.pop(_dev_cache_key(roll), None)


def _deliver(entry, code):
    delivery = current_app.config.get("AUTH_CODE_DELIVERY", "log")
    if delivery == "log":
        current_app.logger.warning("Registration code roll=%s recipient=%s code=%s",
                                   entry.roll_number, entry.university_email, code)
        return
    if delivery != "smtp":
        raise RegistrationCodeError("registration", "invalid_configuration", "AUTH_CODE_DELIVERY must be log or smtp", 500)
    config = current_app.config
    if not config.get("SMTP_HOST") or not config.get("SMTP_FROM"):
        raise RegistrationCodeError("registration", "invalid_configuration", "SMTP_HOST and SMTP_FROM are required", 500)
    message = EmailMessage()
    message["Subject"] = "EduLedger registration code"
    message["From"] = config["SMTP_FROM"]
    message["To"] = entry.university_email
    message.set_content(f"Your EduLedger registration code is {code}. It expires in {config['REGISTRATION_CODE_TTL_SECONDS']} seconds.")
    try:
        with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=10) as smtp:
            if config.get("SMTP_USE_TLS", True):
                smtp.starttls(context=ssl.create_default_context())
            if config.get("SMTP_USERNAME"):
                smtp.login(config["SMTP_USERNAME"], config.get("SMTP_PASSWORD", ""))
            smtp.send_message(message, to_addrs=[entry.university_email])
    except (OSError, smtplib.SMTPException) as exc:
        current_app.logger.error("Registration SMTP delivery failed (%s)", type(exc).__name__)
        raise RegistrationCodeError("registration", "code_delivery_failed", "registration code could not be delivered", 503) from exc


def issue(entry):
    ttl = current_app.config.get("REGISTRATION_CODE_TTL_SECONDS", 600)
    if ttl < 1:
        raise RegistrationCodeError("registration", "invalid_configuration", "code TTL must be positive", 500)
    code = f"{secrets.randbelow(1000000):06d}"
    digest = _digest(entry, code)
    client = redis_client()
    try:
        client.set(code_key(entry.roll_number), digest, ex=ttl)
    except redis.RedisError as exc:
        raise _store_error(exc) from exc
    _forget_dev(entry.roll_number)
    try:
        _deliver(entry, code)
    except RegistrationCodeError:
        try:
            client.eval(_CONSUME_SCRIPT, 1, code_key(entry.roll_number), digest)
        except redis.RedisError as exc:
            raise _store_error(exc) from exc
        raise
    if current_app.config.get("ALLOW_DEV_CODE_ENDPOINT", False):
        with _dev_lock:
            now = time.monotonic()
            for key in list(_dev_codes):
                if _dev_codes[key][1] <= now:
                    del _dev_codes[key]
            if len(_dev_codes) >= 5000:
                _dev_codes.pop(next(iter(_dev_codes)))
            _dev_codes[_dev_cache_key(entry.roll_number)] = (code, now + ttl, digest)
    return ttl


def consume(entry, submitted):
    """Increment attempts atomically; compare in constant time; delete via CAS."""
    maximum = current_app.config.get("REGISTRATION_CODE_MAX_ATTEMPTS", 5)
    if maximum < 1:
        raise RegistrationCodeError("registration", "invalid_configuration", "max attempts must be positive", 500)
    client = redis_client()
    try:
        attempts = client.eval(_ATTEMPT_SCRIPT, 1, attempts_key(entry.roll_number), maximum, ATTEMPT_WINDOW_SECONDS)
        if attempts > maximum:
            raise RegistrationCodeError("rate_limit", "code_attempts_exceeded", "too many code attempts; wait 15 minutes", 429)
        stored = client.get(code_key(entry.roll_number))
        if stored is None:
            _forget_dev(entry.roll_number)
            raise RegistrationCodeError("registration", "code_expired", "registration code expired or was already used", 401)
        normalized = submitted if isinstance(submitted, str) else ""
        valid_shape = len(normalized) == 6 and normalized.isascii() and normalized.isdigit()
        matches = hmac.compare_digest(_digest(entry, normalized), stored)
        if not valid_shape or not matches:
            raise RegistrationCodeError("registration", "code_invalid", "invalid registration code", 401)
        if not client.eval(_CONSUME_SCRIPT, 1, code_key(entry.roll_number), stored):
            raise RegistrationCodeError("registration", "code_expired", "registration code expired or was already used", 401)
    except redis.RedisError as exc:
        raise _store_error(exc) from exc
    _forget_dev(entry.roll_number)


def dev_code(entry):
    """Validate process-local development plaintext against the live Redis hash."""
    with _dev_lock:
        cached = _dev_codes.get(_dev_cache_key(entry.roll_number))
    if cached is None or cached[1] <= time.monotonic():
        _forget_dev(entry.roll_number)
        return None
    code, _, digest = cached
    try:
        stored = redis_client().get(code_key(entry.roll_number))
    except redis.RedisError as exc:
        raise _store_error(exc) from exc
    if stored is None or not hmac.compare_digest(stored, digest) or not hmac.compare_digest(_digest(entry, code), digest):
        _forget_dev(entry.roll_number)
        return None
    return code
