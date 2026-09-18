"""Face embedding and comparison (Module 1 - Zero-Proxy Attendance Engine).

Provider chain, resolved per call via FACE_EMBEDDING_PROVIDER ("auto" by
default):

1. `face_recognition` (dlib 128-d encodings) when installed - the real
   biometric path. It needs Python <= 3.12, so it is an optional dependency:
   install it in the deployment environment and the code upgrades itself.
2. A deterministic Pillow-based demo embedder otherwise (grayscale 32x32,
   L2-normalized). It wires the full flow so the demo runs anywhere, but it
   is NOT biometrically secure - two different natural photos can score
   fairly high. Do not use it for real verification.

Embeddings are dicts: ``{"provider": str, "dims": int, "vector": [floats],
"enrolled_at": iso}``. Comparison is cosine similarity, checked against
FACE_MATCH_THRESHOLD by the caller.
"""

import base64
import binascii
import io
import math
from datetime import datetime, timezone

from flask import current_app
from PIL import Image, UnidentifiedImageError

try:  # optional real provider (Python <= 3.12)
    import face_recognition  # type: ignore
except ImportError:  # pragma: no cover - not installed in the demo env
    face_recognition = None

PROVIDER_FACE_RECOGNITION = "face_recognition"
PROVIDER_DEMO = "demo-pil-32x32"
_DEMO_SIZE = 32


class FaceImageError(ValueError):
    """The submitted image could not be decoded or contained no face."""


class NoFaceDetected(FaceImageError):
    """face_recognition found no face in the image."""


class EmbeddingMismatchError(ValueError):
    """Stored and freshly computed embeddings come from different providers."""


def active_provider() -> str:
    """Resolve the configured embedding provider name."""
    configured = current_app.config["FACE_EMBEDDING_PROVIDER"]
    if configured == PROVIDER_FACE_RECOGNITION and face_recognition is None:
        raise EmbeddingMismatchError(
            "FACE_EMBEDDING_PROVIDER=face_recognition but the package is not installed"
        )
    if configured in {PROVIDER_FACE_RECOGNITION, PROVIDER_DEMO}:
        return configured
    # "auto" (and anything else): prefer the real provider when available.
    return PROVIDER_FACE_RECOGNITION if face_recognition is not None else PROVIDER_DEMO


def _normalize(vector: list[float]) -> list[float]:
    """Scale a vector to unit length so dot product == cosine similarity."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _decode_base64_image(image_b64: str) -> Image.Image:
    """Decode a base64 (or data-URI) image into a loaded PIL image."""
    payload = image_b64.strip()
    if payload.startswith("data:"):  # tolerate the data-URI form
        _, _, payload = payload.partition(",")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FaceImageError("face_image is not valid base64") from exc
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise FaceImageError("face_image is not a decodable image") from exc
    return image


def _demo_vector(image: Image.Image) -> list[float]:
    """Deterministic demo embedding: grayscale 32x32, mean-centered, unit-norm.

    Mean-centering removes the DC (average brightness) component, which
    otherwise dominates the cosine similarity of any two photos and makes
    unrelated images look ~0.9 similar; without it the embedder is useless
    even as a demo. The result is brightness-invariant, like real face
    embeddings.
    """
    small = image.convert("L").resize((_DEMO_SIZE, _DEMO_SIZE))
    values = [float(pixel) / 255.0 for pixel in small.getdata()]
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    return _normalize(centered)


def _face_recognition_vector(image: Image.Image) -> list[float]:
    """Real embedding via the face_recognition library (128-d)."""
    import numpy as np  # face_recognition dependency

    encodings = face_recognition.face_encodings(np.array(image.convert("RGB")))
    if not encodings:
        raise NoFaceDetected("no face found in the submitted image")
    return _normalize([float(value) for value in encodings[0]])


def embed_face_image(image_b64: str) -> dict:
    """Compute the active provider's embedding for a base64 image."""
    image = _decode_base64_image(image_b64)
    provider = active_provider()
    if provider == PROVIDER_FACE_RECOGNITION:
        vector = _face_recognition_vector(image)
    else:
        vector = _demo_vector(image)
    return {
        "provider": provider,
        "dims": len(vector),
        "vector": vector,
        "enrolled_at": datetime.now(timezone.utc).isoformat(),
    }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compare_embeddings(stored: dict, candidate: dict) -> float:
    """Cosine similarity between a stored and a freshly computed embedding.

    Raises EmbeddingMismatchError when the two come from different providers
    (e.g. enrolled with the demo embedder, verified with face_recognition) -
    the student must be re-enrolled in that case.
    """
    if stored.get("provider") != candidate.get("provider") or stored.get("dims") != candidate.get("dims"):
        raise EmbeddingMismatchError(
            f"stored embedding ({stored.get('provider')}) does not match the active "
            f"provider ({candidate.get('provider')}) - re-enroll the student's face"
        )
    return cosine_similarity(stored["vector"], candidate["vector"])
