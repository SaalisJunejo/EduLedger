"""Simplified WebAuthn assertion verification (STUB - Module 1).

The full ceremony - credential registration, challenge freshness, origin/rpId
checks, and signature verification against the stored public key - is future
work (see docs/PRD.md). For the MVP demo the fingerprint gate itself runs
on-device (Touch ID / Android BiometricPrompt) before the assertion is
produced, so the server only verifies that the assertion belongs to the
student's registered device - the device binding performed at
register-device time is the trust anchor.

Swap this module's `verify_assertion` for a real `webauthn`-library
implementation when the full ceremony is built; the API contract stays.
"""


class WebAuthnError(Exception):
    """Raised when a fingerprint assertion cannot be accepted."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def verify_assertion(student, device_id, assertion) -> None:
    """Validate a fingerprint WebAuthn assertion for `student`.

    Raises WebAuthnError with a demo-friendly message on failure; returns
    None on success.
    """
    if not isinstance(assertion, dict) or not assertion:
        raise WebAuthnError(
            "invalid_webauthn_assertion", "webauthn_assertion must be a non-empty object"
        )
    if not device_id or not str(device_id).strip():
        raise WebAuthnError(
            "missing_fields", "device_id is required together with webauthn_assertion"
        )
    device_id = str(device_id).strip()

    if not student.device_id:
        raise WebAuthnError(
            "device_not_bound",
            f"student {student.id} has no device registered",
            status=403,
        )
    if student.device_id != device_id:
        raise WebAuthnError(
            "device_mismatch",
            f"assertion device does not match the student's registered device ({student.device_id})",
            status=403,
        )

    # TODO(full WebAuthn): verify the assertion signature against the public
    # key registered for this device credential, check challenge freshness,
    # origin and rpId, and counter replay protection.
