"""IPFS evidence endpoints (record-change justification documents).

- POST /api/v1/ipfs/upload - multipart upload -> CID + public gateway URL

Evidence documents are pinned on IPFS (see services/ipfs.py) so later
modules can anchor a content address on-chain. MVP policy: common evidence
document types (PDF, PNG, JPG) capped at 10 MB per file.
"""

import os

from flask import jsonify, request

from ..services.ipfs import IPFSUploadError, upload_to_ipfs
from .errors import api_error
from . import api_v1

# MVP evidence policy: common document types only, capped at 10 MB.
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@api_v1.post("/ipfs/upload")
def upload():
    """Pin an evidence document on IPFS and return its CID + gateway URL.

    Body: ``multipart/form-data`` with a ``file`` field. The upload goes to
    the local IPFS daemon first, then Web3.Storage's free tier (see
    services/ipfs.py). Success answers 201 with
    ``{"cid": "...", "gateway_url": "https://ipfs.io/ipfs/<cid>"}``;
    validation failures answer 400 and upload failures 502, both in the
    shared ``{error, check, message}`` shape.
    """
    file = request.files.get("file")
    if file is None or not file.filename:
        return api_error("request", "missing_fields", "multipart field 'file' is required")

    filename = file.filename
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        return api_error(
            "request",
            "invalid_file_type",
            f"file type {extension or '(no extension)'} is not allowed - "
            f"evidence must be one of: {allowed}",
        )
    # The extension is the source of truth (curl sends octet-stream by
    # default); a *contradicting* declared MIME type is still rejected.
    mimetype = (file.mimetype or "").lower()
    if mimetype and mimetype not in ALLOWED_TYPES.values() and mimetype != "application/octet-stream":
        return api_error(
            "request",
            "invalid_file_type",
            f"declared content type {mimetype!r} does not match an allowed "
            "evidence type (PDF, PNG, JPG)",
        )

    # Read at most limit+1 bytes so oversized uploads are rejected without
    # buffering the whole body in memory.
    data = file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        return api_error(
            "request",
            "file_too_large",
            f"file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB evidence limit",
        )
    if not data:
        return api_error("request", "invalid_file", "uploaded file is empty")

    try:
        cid = upload_to_ipfs(data, filename)
    except IPFSUploadError as exc:
        return api_error("ipfs", exc.code, exc.message, 502)

    return jsonify({"cid": cid, "gateway_url": f"https://ipfs.io/ipfs/{cid}"}), 201
