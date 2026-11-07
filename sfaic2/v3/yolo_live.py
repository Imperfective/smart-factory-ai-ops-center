"""Live YOLO11n inference for the v3 camera gate and for the verification screen.

The fine-tuned weights (results/vision/train/yolo11n_mvtec/weights/best.pt) are loaded once per process and run on
Apple MPS when available. `infer()` returns the same verbatim Ultralytics JSON shape that the replay file stores, so
a live frame and a replayed frame are directly comparable. `verify()` re-runs the model on held-out images right now
and compares the boxes with the stored replay output (reproducibility check)."""
from __future__ import annotations

import base64
import hashlib
import json
import random
import threading
import time
from pathlib import Path

WEIGHTS = Path("results/vision/train/yolo11n_mvtec/weights/best.pt")
IMAGES = Path("data/derived/mvtec_yolo/images/val")
MANIFEST = Path("data/derived/mvtec_yolo/manifest.json")
from .plant import REPLAY_PATH as REPLAY  # noqa: E402  (local file if present, else the published copy)
CONF, IMGSZ = 0.25, 640


class LiveYolo:
    _inst: "LiveYolo | None" = None
    _lock = threading.Lock()

    def __init__(self):
        import torch
        import ultralytics
        from ultralytics import YOLO
        self.model = YOLO(str(WEIGHTS))
        self.sha = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.versions = dict(ultralytics=ultralytics.__version__, torch=torch.__version__)
        self.n = 0
        self.total_ms = 0.0

    @classmethod
    def get(cls) -> "LiveYolo":
        with cls._lock:
            if cls._inst is None:
                cls._inst = LiveYolo()
            return cls._inst

    @staticmethod
    def available() -> tuple[bool, str]:
        if not WEIGHTS.exists():
            return False, f"weights missing: {WEIGHTS}"
        if not IMAGES.exists():
            return False, f"images missing: {IMAGES}"
        try:
            import ultralytics  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return False, f"ultralytics not installed: {exc}"
        return True, "ok"

    def infer(self, image_file: str) -> dict:
        p = IMAGES / Path(image_file).name
        t0 = time.perf_counter()
        with LiveYolo._lock:
            r = self.model.predict(source=str(p), imgsz=IMGSZ, conf=CONF, device=self.device, verbose=False)[0]
        wall = (time.perf_counter() - t0) * 1000
        self.n += 1
        self.total_ms += wall
        return dict(names=r.names, detections=r.summary(), speed_ms=r.speed, orig_shape=list(r.orig_shape), path=p.name, model="YOLO11n", weights_sha256=self.sha,
                    ultralytics=self.versions["ultralytics"], conf=CONF, imgsz=IMGSZ, mode="live", device=self.device, wall_ms=round(wall, 2))

    def annotate(self, image_file: str, raw: dict, max_side: int = 420) -> str:
        import cv2
        img = cv2.imread(str(IMAGES / Path(image_file).name))
        for d in raw.get("detections") or []:
            b = d["box"]
            x1, y1, x2, y2 = int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])
            cv2.rectangle(img, (x1, y1), (x2, y2), (89, 79, 0), 4)
            cv2.putText(img, f"defect {d['confidence']:.2f}", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (89, 79, 0), 2, cv2.LINE_AA)
        h, w = img.shape[:2]
        s = max_side / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _replay_index() -> dict:
    out = {}
    if REPLAY.exists():
        for line in REPLAY.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out[r["image"]] = r
    return out


def _same_boxes(a: list, b: list, tol: float = 2.0) -> bool:
    if len(a) != len(b):
        return False
    key = lambda d: (d["box"]["x1"], d["box"]["y1"])  # noqa: E731
    return all(abs(x["box"][k] - y["box"][k]) <= tol for x, y in zip(sorted(a, key=key), sorted(b, key=key)) for k in ("x1", "y1", "x2", "y2"))


def verify(n: int = 6, seed: int | None = None, images: list[str] | None = None) -> dict:
    """Run the model now on `n` held-out images (half defective, half good when possible) and compare with the stored
    replay output. Ground-truth labels are shown for evaluation only."""
    ok, why = LiveYolo.available()
    if not ok:
        return dict(ok=False, reason=why)
    y = LiveYolo.get()
    idx = _replay_index()
    man = json.loads(MANIFEST.read_text())
    val = [m for m in man["items"] if m["split"] == "val"]
    rng = random.Random(seed if seed is not None else time.time())
    if images:
        pick = [m for m in val if m["file"] in images]
    else:
        bad = [m for m in val if m["truth_defect"]]
        good = [m for m in val if not m["truth_defect"]]
        pick = rng.sample(bad, min(len(bad), (n + 1) // 2)) + rng.sample(good, min(len(good), n // 2))
    rows = []
    for m in pick:
        raw = y.infer(m["file"])
        stored = (idx.get(m["file"]) or {}).get("raw_json") or {}
        dets = raw["detections"]
        rows.append(dict(image=m["file"], category=m["category"], truth_defect=m["truth_defect"], truth_defect_type=m["defect_type"],
                         predicted_defect=len(dets) > 0, correct=(len(dets) > 0) == m["truth_defect"], n_boxes=len(dets),
                         max_confidence=max((d["confidence"] for d in dets), default=None), inference_ms=raw["speed_ms"].get("inference"), wall_ms=raw["wall_ms"],
                         stored_n_boxes=len(stored.get("detections") or []), matches_stored=_same_boxes(dets, stored.get("detections") or []),
                         raw_json=raw, annotated=y.annotate(m["file"], raw)))
    return dict(ok=True, device=y.device, weights=str(WEIGHTS), weights_sha256=y.sha, versions=y.versions, n=len(rows),
                correct=sum(r["correct"] for r in rows), matches_stored=sum(r["matches_stored"] for r in rows), rows=rows,
                note="truth labels come from the MVTec AD masks and are shown for evaluation only; the decision layer never sees them")
