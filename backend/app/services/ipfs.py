"""IPFS evidence pinning client.

Evidence files (record-change justifications, Phase 3) are pinned on IPFS
before their CID is anchored on-chain. This project runs local
infrastructure rather than Docker, so uploads go to the local Kubo daemon
first (IPFS_API_URL, default http://127.0.0.1:5001 - `ipfs daemon`); when
that daemon is unreachable, Web3.Storage's free tier is used as a fallback
(bearer token WEB3_STORAGE_TOKEN, see https://web3.storage). Every failure
raises IPFSUploadError with a machine-readable code, mirroring
BlockchainError in services/blockchain.py.
"""

import io

import requests
from flask import current_app

# Kubo RPC: multipart POST /api/v0/add -> {"Name", "Hash", "Size"}.
LOCAL_ADD_PATH = "/api/v0/add"
# Web3.Storage upload API: multipart POST -> {"cid": "..."}.
WEB3_STORAGE_UPLOAD_URL = "https://api.web3.storage/upload"
# (connect, read) timeouts in seconds - uploads of the <=10 MB evidence
# files stay well below the read budget even on slow connections.
LOCAL_TIMEOUT_SECONDS = (5, 60)
WEB3_STORAGE_TIMEOUT_SECONDS = (10, 120)


class IPFSUploadError(Exception):
    """IPFS upload failure with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _add_to_local_daemon(file_bytes: bytes, filename: str) -> str:
    """`ipfs add` via the local Kubo RPC; returns the content CID ("Hash")."""
    api_url = current_app.config["IPFS_API_URL"].rstrip("/") + LOCAL_ADD_PATH
    try:
        response = requests.post(
            api_url,
            files={"file": (filename, io.BytesIO(file_bytes))},
            timeout=LOCAL_TIMEOUT_SECONDS,
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise IPFSUploadError(
            "ipfs_daemon_unreachable",
            f"cannot reach the local IPFS daemon at {api_url} (is `ipfs daemon` running?)",
        ) from exc
    except requests.RequestException as exc:
        raise IPFSUploadError(
            "ipfs_daemon_error", f"request to the local IPFS daemon failed: {exc}"
        ) from exc

    if response.status_code != 200:
        raise IPFSUploadError(
            "ipfs_daemon_error",
            f"local IPFS daemon returned HTTP {response.status_code}: {response.text[:200]}",
        )
    try:
        cid = str(response.json()["Hash"]).strip()
    except (ValueError, KeyError) as exc:
        raise IPFSUploadError(
            "ipfs_daemon_error",
            f"unexpected response from the local IPFS daemon: {response.text[:200]}",
        ) from exc
    if not cid:
        raise IPFSUploadError("ipfs_daemon_error", "local IPFS daemon returned an empty CID")
    return cid


def _upload_to_web3_storage(file_bytes: bytes, filename: str) -> str:
    """Upload via Web3.Storage's free-tier API; returns the content CID."""
    token = current_app.config.get("WEB3_STORAGE_TOKEN", "")
    if not token:
        raise IPFSUploadError(
            "web3_storage_not_configured",
            "WEB3_STORAGE_TOKEN is not set - get a free token at https://web3.storage "
            "and add it to backend/.env to enable the fallback",
        )
    try:
        response = requests.post(
            WEB3_STORAGE_UPLOAD_URL,
            headers={"Authorization": f"Bearer {token}", "X-Name": filename},
            files={"file": (filename, io.BytesIO(file_bytes))},
            timeout=WEB3_STORAGE_TIMEOUT_SECONDS,
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise IPFSUploadError(
            "web3_storage_unreachable",
            f"cannot reach {WEB3_STORAGE_UPLOAD_URL}: {exc}",
        ) from exc
    except requests.RequestException as exc:
        raise IPFSUploadError(
            "web3_storage_error", f"request to Web3.Storage failed: {exc}"
        ) from exc

    if response.status_code in (401, 403):
        raise IPFSUploadError(
            "web3_storage_unauthorized",
            f"Web3.Storage rejected the API token (HTTP {response.status_code}) - "
            "check WEB3_STORAGE_TOKEN in backend/.env",
        )
    if response.status_code != 200:
        raise IPFSUploadError(
            "web3_storage_error",
            f"Web3.Storage upload failed (HTTP {response.status_code}): {response.text[:200]}",
        )
    try:
        cid = str(response.json()["cid"]).strip()
    except (ValueError, KeyError) as exc:
        raise IPFSUploadError(
            "web3_storage_error",
            f"unexpected response from Web3.Storage: {response.text[:200]}",
        ) from exc
    if not cid:
        raise IPFSUploadError("web3_storage_error", "Web3.Storage returned an empty CID")
    return cid


def upload_to_ipfs(file_bytes: bytes, filename: str) -> str:
    """Pin ``file_bytes`` on IPFS and return the content CID.

    The local Kubo daemon is tried first (this project targets local
    infrastructure); only a connection-level daemon failure falls back to
    Web3.Storage - a daemon that answers with an error is surfaced as-is,
    since it is reachable but broken. Raises IPFSUploadError
    ("ipfs_upload_failed") naming both failures when neither method works.
    """
    local_failure = None
    try:
        return _add_to_local_daemon(file_bytes, filename)
    except IPFSUploadError as exc:
        if exc.code != "ipfs_daemon_unreachable":
            raise
        local_failure = exc

    try:
        return _upload_to_web3_storage(file_bytes, filename)
    except IPFSUploadError as exc:
        raise IPFSUploadError(
            "ipfs_upload_failed",
            "both IPFS upload methods failed - "
            f"local daemon: {local_failure.message}; fallback: {exc.message}",
        ) from exc
