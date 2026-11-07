"""YOLO adapter (opt-in). On Apple Silicon uses PyTorch MPS when available. Task (`detect` vs `classify`) is
explicit; no supervised training on unlabeled data is attempted. Without ultralytics installed, `MockVision`
produces SIMULATED events so the Command Center still runs end-to-end."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path


def _device():
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


class YoloVision:
    def __init__(self, weights: str = "yolo11n.pt", task: str = "detect", imgsz: int = 640, conf: float = 0.25):
        from ultralytics import YOLO  # noqa: WPS433
        self.task, self.imgsz, self.conf = task, imgsz, conf
        self.model = YOLO(weights)
        self.weights_sha256 = hashlib.sha256(Path(self.model.ckpt_path).read_bytes()).hexdigest() if getattr(self.model, "ckpt_path", None) and Path(self.model.ckpt_path).exists() else None
        self.device = _device()

    def infer(self, image_path: str | Path) -> dict:
        p = Path(image_path)
        t0 = time.perf_counter()
        out = dict(detector=f"YOLO11:{self.task}", model_version=self.weights_sha256, device=self.device, source_type="DERIVED",
                   source_image_id=hashlib.sha256(p.read_bytes()).hexdigest(), quality="VALID", error=None, ground_truth_available=False)
        try:
            r = self.model.predict(source=str(p), imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False)[0]
            if self.task == "classify" and r.probs is not None:
                out.update(signal="class_probabilities", value={str(r.names[i]): float(v) for i, v in enumerate(r.probs.data.tolist())},
                           confidence=float(r.probs.top1conf))
            else:
                dets = [dict(cls=str(r.names[int(b.cls)]), conf=float(b.conf), xyxy=[float(x) for x in b.xyxy[0].tolist()]) for b in r.boxes] if r.boxes is not None else []
                out.update(signal="part_presence", value=len(dets) > 0, detections=dets, confidence=max((d["conf"] for d in dets), default=0.0))
        except Exception as exc:  # noqa: BLE001
            out.update(quality="QUESTIONABLE", error=f"{type(exc).__name__}: {exc}", value=None, confidence=0.0)
        out["processing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out


class MockVision:
    """SIMULATED stand-in: deterministic on the image hash; labelled so it is never mistaken for a model result."""
    def infer(self, image_path: str | Path) -> dict:
        p = Path(image_path)
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        return dict(detector="MOCK_VISION", model_version=None, device="none", source_type="SIMULATED", source_image_id=h, quality="VALID",
                    signal="part_presence", value=(int(h[:2], 16) % 10) != 0, confidence=0.5 + (int(h[2:4], 16) % 50) / 100, error=None,
                    processing_ms=0.0, ground_truth_available=False)


def make_vision(prefer_real: bool = True, **kw):
    if prefer_real:
        try:
            return YoloVision(**kw)
        except Exception:  # noqa: BLE001 — ultralytics/torch absent or weights unavailable offline
            pass
    return MockVision()
