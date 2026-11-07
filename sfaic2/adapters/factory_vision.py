"""Factory-image YOLO perception layer ("limbs") on MVTec AD industrial defect images.

Pipeline (all measured locally on Apple MPS; nothing is invented):
  1. prepare(): MVTec AD categories (metal_nut, screw, cable) → YOLO detection dataset. Defect boxes are derived from the
     official pixel masks (connected components after a small dilation), single class `defect`; good images are
     background-only. The MVTec *test* split (good + defect) is split 70/30 per (category, defect_type) with a fixed seed
     — a supervised few-shot protocol, NOT the unsupervised anomaly-detection protocol of the original benchmark; this
     is documented in the summary and the paper. A slice of MVTec train/good is added as extra background.
  2. train(): fine-tune YOLO11n (COCO init) on the derived set; ultralytics val metrics (mAP50, mAP50-95, P, R) are kept.
  3. log(): run the fine-tuned model on every held-out image, write annotated frames (truth box vs prediction) and a JSONL
     of DERIVED `vision_part_presence` events in the same shape the simulator's camera gate emits (docs/07), plus a
     summary with image-level defect precision/recall and per-image latency. Dashboard reads these files.

Licence: MVTec AD is CC BY-NC-SA 4.0 (non-commercial research only; Bergmann et al., CVPR 2019 / IJCV 2021). The data is
not redistributed; it is downloaded by the user from a public mirror into data/raw/MVTEC_AD and registered with hashes.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from collections import defaultdict
from pathlib import Path

RAW = Path("data/raw/MVTEC_AD")
DERIVED = Path("data/derived/mvtec_yolo")
OUT = Path("results/vision")
PUBLISHED = Path("data/results/vision")   # measurement + replay records shipped with the repository
CATEGORIES = ("metal_nut", "screw", "cable")
LICENCE = "MVTec AD — CC BY-NC-SA 4.0 (non-commercial research only); Bergmann, Fauser, Sattlegger, Steger: MVTec AD — A Comprehensive Real-World Dataset for Unsupervised Anomaly Detection, CVPR 2019; IJCV 2021"
HF_MIRROR = "https://huggingface.co/datasets/foersben/mvtec-ad"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fetch(categories=CATEGORIES, repo_id: str = "foersben/mvtec-ad") -> Path:
    """Download the selected categories from the public Hugging Face mirror (user action; licence accepted by the user)."""
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(repo_id=repo_id, repo_type="dataset", local_dir=str(RAW), allow_patterns=["README.md", *[f"{c}/*" for c in categories]]))


def mask_to_boxes(mask_path: Path, dilate_px: int = 15, min_area: int = 16) -> list[tuple[float, float, float, float]]:
    """Official pixel mask → normalised xywh boxes (one per connected component after dilation)."""
    import cv2
    import numpy as np
    m = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    h, w = m.shape
    binary = (m > 0).astype(np.uint8)
    if dilate_px > 0:
        binary = cv2.dilate(binary, np.ones((dilate_px, dilate_px), np.uint8))
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area:
            continue
        pad = 4
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        boxes.append(((x0 + x1) / 2 / w, (y0 + y1) / 2 / h, (x1 - x0) / w, (y1 - y0) / h))
    return boxes


def prepare(categories=CATEGORIES, seed: int = 42, val_frac: float = 0.3, extra_good_train: int = 40, root: Path = RAW, out: Path = DERIVED) -> dict:
    """Build the YOLO dataset + split manifest (image sha256, category, defect_type, split, n_boxes)."""
    rng = random.Random(seed)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    manifest = []

    def add(img: Path, split: str, category: str, defect_type: str, boxes):
        name = f"{category}__{defect_type}__{img.stem}.png"
        dst = out / "images" / split / name
        if not dst.exists():
            dst.symlink_to(img.resolve())
        lbl = out / "labels" / split / name.replace(".png", ".txt")
        lbl.write_text("".join(f"0 {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n" for x, y, w, h in boxes), encoding="utf-8")
        manifest.append(dict(file=name, source=str(img.relative_to(root)), sha256=sha(img), category=category, defect_type=defect_type,
                             truth_defect=defect_type != "good", split=split, n_boxes=len(boxes)))

    for cat in categories:
        cdir = root / cat
        if not cdir.exists():
            raise FileNotFoundError(f"{cdir} missing — run fetch() / place the MVTec AD category there")
        for ddir in sorted((cdir / "test").iterdir()):
            if not ddir.is_dir():
                continue
            imgs = sorted(ddir.glob("*.png"))
            rng.shuffle(imgs)
            n_val = max(1, round(len(imgs) * val_frac))
            for k, img in enumerate(imgs):
                boxes = [] if ddir.name == "good" else mask_to_boxes(cdir / "ground_truth" / ddir.name / f"{img.stem}_mask.png")
                add(img, "val" if k < n_val else "train", cat, ddir.name, boxes)
        goods = sorted((cdir / "train" / "good").glob("*.png"))
        rng.shuffle(goods)
        for img in goods[:extra_good_train]:
            add(img, "train", cat, "good", [])
    yaml = out / "data.yaml"
    yaml.write_text(f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: defect\n", encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(dict(seed=seed, val_frac=val_frac, extra_good_train=extra_good_train, categories=list(categories),
                                                       protocol="supervised split of the MVTec AD test set (70/30 per category×defect_type) + extra train/good backgrounds; NOT the unsupervised benchmark protocol",
                                                       items=manifest), indent=1), encoding="utf-8")
    n = defaultdict(int)
    for m in manifest:
        n[(m["split"], "defect" if m["truth_defect"] else "good")] += 1
    return dict(yaml=str(yaml), n_train_defect=n[("train", "defect")], n_train_good=n[("train", "good")], n_val_defect=n[("val", "defect")], n_val_good=n[("val", "good")])


def train(epochs: int = 30, imgsz: int = 640, batch: int = 8, seed: int = 42, base: str = "yolo11n.pt", out: Path = DERIVED, project: Path = OUT / "train") -> dict:
    from ultralytics import YOLO
    from .vision import _device
    t0 = time.time()
    project = Path(project).resolve()  # absolute, else ultralytics nests it under runs/detect/
    project.mkdir(parents=True, exist_ok=True)
    model = YOLO(base)
    res = model.train(data=str(out / "data.yaml"), epochs=epochs, imgsz=imgsz, batch=batch, device=_device(), workers=2, seed=seed, deterministic=True,
                      project=str(project), name="yolo11n_mvtec", exist_ok=True, plots=True, verbose=False, cache=False)
    best = Path(res.save_dir) / "weights" / "best.pt"
    m = YOLO(str(best)).val(data=str(out / "data.yaml"), imgsz=imgsz, device=_device(), plots=False, verbose=False)
    metrics = dict(mAP50=round(float(m.box.map50), 4), mAP50_95=round(float(m.box.map), 4), precision=round(float(m.box.mp), 4), recall=round(float(m.box.mr), 4))
    info = dict(best=str(best), best_sha256=sha(best), base=base, epochs=epochs, imgsz=imgsz, batch=batch, seed=seed, device=_device(), train_s=round(time.time() - t0, 1), box_metrics=metrics)
    (project / "train_summary.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def log(weights: str | Path | None = None, conf: float = 0.25, imgsz: int = 640, out: Path = DERIVED, result: Path = OUT) -> dict:
    """Held-out inference → annotated frames + JSONL DERIVED events + summary (image-level defect P/R, latency)."""
    import cv2
    import torch
    import ultralytics
    from .datasets import register_local
    from .vision import YoloVision
    weights = Path(weights or (result / "train" / "yolo11n_mvtec" / "weights" / "best.pt"))
    train_info = json.loads((result / "train" / "train_summary.json").read_text()) if (result / "train" / "train_summary.json").exists() else {}
    entry = register_local("MVTEC_AD", license_reviewed=True, version=f"hf:{HF_MIRROR.rsplit('/', 1)[-1]}")
    manifest = json.loads((out / "manifest.json").read_text())
    items = [m for m in manifest["items"] if m["split"] == "val"]
    (result / "images").mkdir(parents=True, exist_ok=True)
    yv = YoloVision(weights=str(weights), task="detect", imgsz=imgsz, conf=conf)
    yv.infer(out / "images" / "val" / items[0]["file"])  # warm-up, excluded
    rows, conf_mat = [], defaultdict(int)
    per_cat = defaultdict(lambda: defaultdict(int))
    with open(result / "vision_log.jsonl", "w", encoding="utf-8") as fh:
        for it in items:
            p = out / "images" / "val" / it["file"]
            res = yv.infer(p)
            dets = res.get("detections") or []
            pred = len(dets) > 0
            truth = bool(it["truth_defect"])
            cell = "TP" if pred and truth else "FP" if pred else "FN" if truth else "TN"
            conf_mat[cell] += 1
            per_cat[it["category"]][cell] += 1
            img = cv2.imread(str(p))
            h, w = img.shape[:2]
            for line in (out / "labels" / "val" / it["file"].replace(".png", ".txt")).read_text().splitlines():
                _, cx, cy, bw, bh = map(float, line.split())
                x0, y0, x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h), int((cx + bw / 2) * w), int((cy + bh / 2) * h)
                cv2.rectangle(img, (x0, y0), (x1, y1), (214, 142, 44), 2)  # truth box (blue-ish in BGR)
            for d in dets:
                x0, y0, x1, y1 = [int(v) for v in d["xyxy"]]
                cv2.rectangle(img, (x0, y0), (x1, y1), (89, 79, 0), 3)  # prediction (DS primary teal in BGR)
                cv2.putText(img, f"defect {d['conf']:.2f}", (x0, max(18, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (89, 79, 0), 2, cv2.LINE_AA)
            banner = f"{it['category']} / {it['defect_type']}  truth={'DEFECT' if truth else 'GOOD'}  pred={'DEFECT' if pred else 'GOOD'}  [{cell}]"
            cv2.rectangle(img, (0, 0), (w, 34), (255, 255, 255), -1)
            cv2.putText(img, banner, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 2, cv2.LINE_AA)
            out_name = it["file"].replace(".png", "_det.jpg")
            cv2.imwrite(str(result / "images" / out_name), img, [cv2.IMWRITE_JPEG_QUALITY, 82])
            row = dict(event_id=f"evt_vis_{it['file'][:-4]}", asset_id="CAMERA_GATE", sim_time_s=None, source_type="DERIVED", signal="vision_part_presence",
                       detector=res["detector"], model_version=res["model_version"], device=res["device"], quality=res["quality"], error=res["error"],
                       detected=True, confidence=res.get("confidence"), defect=pred, defect_class="defect" if pred else None,
                       defect_confidence=max((d["conf"] for d in dets), default=None), n_boxes_pred=len(dets), n_boxes_truth=it["n_boxes"],
                       truth_defect=truth, truth_defect_type=it["defect_type"], category=it["category"], outcome=cell, processing_ms=res["processing_ms"],
                       source_image_id=res["source_image_id"], image=it["file"], annotated=out_name,
                       provenance=dict(dataset_id="MVTEC_AD", source=it["source"], license=LICENCE, sha256=entry.files.get(it["source"])),
                       ground_truth_available=True, timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
    tp, fp, fn, tn = (conf_mat[k] for k in ("TP", "FP", "FN", "TN"))
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    lat = sorted(r["processing_ms"] for r in rows)
    summary = dict(dataset_id="MVTEC_AD", categories=manifest["categories"], protocol=manifest["protocol"], n_images=len(rows), n_defect=tp + fn, n_good=fp + tn,
                   image_level=dict(TP=tp, FP=fp, FN=fn, TN=tn, precision=None if prec is None else round(prec, 4), recall=None if rec is None else round(rec, 4),
                                    accuracy=round((tp + tn) / len(rows), 4), f1=None if not prec or not rec else round(2 * prec * rec / (prec + rec), 4), conf_threshold=conf),
                   per_category={c: dict(v, recall=round(v["TP"] / (v["TP"] + v["FN"]), 4) if v["TP"] + v["FN"] else None) for c, v in per_cat.items()},
                   box_metrics=train_info.get("box_metrics"), train=train_info, weights=str(weights), weights_sha256=yv.weights_sha256, device=yv.device,
                   torch=torch.__version__, ultralytics=ultralytics.__version__, median_ms=lat[len(lat) // 2], p95_ms=lat[int(len(lat) * 0.95) - 1],
                   licence=LICENCE, source=HF_MIRROR,
                   role="perception layer (limbs): detects surface/assembly defects on parts at the camera gate and emits DERIVED vision_part_presence events to PLC → MQTT → JEV; makes no supervisory decision")
    (result / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def measured_defect_recall(default: float = 1.0) -> float:
    """Image-level recall measured by log(); used by scenario S4 so the simulated camera gate misses defects at the
    measured rate instead of an assumed one. Falls back to `default` when no measurement exists."""
    p = OUT / "summary.json"
    if not p.exists():
        p = PUBLISHED / "summary.json"
    try:
        r = json.loads(p.read_text())["image_level"]["recall"]
        return float(r) if r is not None else default
    except Exception:  # noqa: BLE001
        return default


def summarize_train(run_dir: Path = OUT / "train" / "yolo11n_mvtec", imgsz: int = 640, out: Path = DERIVED, project: Path = OUT / "train", **meta) -> dict:
    """Re-validate an existing training run (best.pt) and write train_summary.json (used when a run already exists)."""
    from ultralytics import YOLO
    from .vision import _device
    best = Path(run_dir) / "weights" / "best.pt"
    m = YOLO(str(best)).val(data=str(out / "data.yaml"), imgsz=imgsz, device=_device(), plots=False, verbose=False)
    metrics = dict(mAP50=round(float(m.box.map50), 4), mAP50_95=round(float(m.box.map), 4), precision=round(float(m.box.mp), 4), recall=round(float(m.box.mr), 4))
    info = dict(best=str(best), best_sha256=sha(best), imgsz=imgsz, device=_device(), box_metrics=metrics, **meta)
    Path(project).mkdir(parents=True, exist_ok=True)
    (Path(project) / "train_summary.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def run_all(epochs: int = 30, imgsz: int = 640, batch: int = 8, seed: int = 42) -> dict:
    prep = prepare(seed=seed)
    tr = train(epochs=epochs, imgsz=imgsz, batch=batch, seed=seed)
    return dict(prepare=prep, train=tr, log=log(weights=tr["best"], imgsz=imgsz))


def export_replay(weights: str | Path | None = None, conf: float = 0.25, imgsz: int = 640, out: Path = DERIVED, result: Path = OUT) -> dict:
    """Run the fine-tuned YOLO11n on every held-out image and store the *verbatim* Ultralytics result JSON
    (`Results.summary()` list + speed + image shape) so the v3 camera gate can replay real detector output as an
    immutable raw event. Truth labels are kept in a separate field that the operational pipeline never reads."""
    import ultralytics
    from ultralytics import YOLO
    from .vision import _device
    weights = Path(weights or (result / "train" / "yolo11n_mvtec" / "weights" / "best.pt"))
    model = YOLO(str(weights))
    wsha = sha(weights)
    manifest = json.loads((out / "manifest.json").read_text())
    items = [m for m in manifest["items"] if m["split"] == "val"]
    model.predict(source=str(out / "images" / "val" / items[0]["file"]), imgsz=imgsz, conf=conf, device=_device(), verbose=False)  # warm-up
    n = 0
    with open(result / "replay_raw.jsonl", "w", encoding="utf-8") as fh:
        for it in items:
            p = out / "images" / "val" / it["file"]
            r = model.predict(source=str(p), imgsz=imgsz, conf=conf, device=_device(), verbose=False)[0]
            raw = dict(names=r.names, detections=r.summary(), speed_ms=r.speed, orig_shape=list(r.orig_shape), path=it["file"],
                       model="YOLO11n", weights_sha256=wsha, ultralytics=ultralytics.__version__, conf=conf, imgsz=imgsz)
            fh.write(json.dumps(dict(image=it["file"], category=it["category"], frame_hash=it["sha256"], raw_json=raw,
                                     eval_truth=dict(truth_defect=bool(it["truth_defect"]), defect_type=it["defect_type"])), ensure_ascii=False) + "\n")
            n += 1
    return dict(n=n, path=str(result / "replay_raw.jsonl"), weights_sha256=wsha)
