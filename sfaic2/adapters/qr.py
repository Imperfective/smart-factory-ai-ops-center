"""QR decoding with OpenCV `QRCodeDetector` (optional dependency). Payload/route validity is NOT decided here —
only presence, decode success and a self-defined decode-quality score; business rules live in the decision layer."""
from __future__ import annotations

import hashlib
from pathlib import Path


def decode_qr(image_path: str | Path) -> dict:
    """Return {decoded, payload, points, quality, image_sha256, error}. Never raises on a bad image."""
    p = Path(image_path)
    out = dict(decoded=False, payload=None, points=None, quality="MISSING", image_sha256=None, detector="opencv.QRCodeDetector", error=None)
    try:
        out["image_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        import cv2  # noqa: WPS433
        img = cv2.imread(str(p))
        if img is None:
            out["error"] = "unreadable image"
            return out
        det = cv2.QRCodeDetector()
        payload, points, _ = det.detectAndDecode(img)
        if points is not None and len(points):
            out["points"] = points.reshape(-1, 2).tolist()
        if payload:
            out.update(decoded=True, payload=payload, quality="VALID")
        elif points is not None and len(points):
            out.update(quality="QUESTIONABLE", error="QR located but not decodable")
        else:
            out.update(quality="MISSING", error="no QR found")
    except ImportError:
        out["error"] = "opencv not installed (pip install -e '.[vision]')"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def route_consistent(payload: str, expected_prefix: str, observed_port: str, allowed_ports: dict[str, list[str]]) -> dict:
    """Business rule (deterministic): id format + port allowed for the route. No ML, no truth access."""
    ok_format = payload.startswith(expected_prefix) and payload[len(expected_prefix):].isdigit()
    route = allowed_ports.get(expected_prefix, [])
    return dict(id_format_ok=ok_format, port_allowed=observed_port in route, route_consistent=ok_format and observed_port in route)


def make_test_qr(payload: str, path: str | Path, size: int = 300) -> Path:
    """Create a synthetic QR image for tests (requires opencv-contrib or opencv>=4.8 QRCodeEncoder)."""
    import cv2
    import numpy as np
    enc = cv2.QRCodeEncoder.create()
    img = enc.encode(payload)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(str(path), img)
    return Path(path)
