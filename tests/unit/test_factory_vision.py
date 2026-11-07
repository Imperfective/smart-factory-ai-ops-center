"""factory_vision: mask→box derivation and dataset preparation on a synthetic MVTec-like tree (no model, no download)."""
import json
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from sfaic2.adapters import factory_vision as fv


def _tree(root, cat="metal_nut", n_good_test=4, n_defect=6, n_train_good=5):
    for sub in ("train/good", "test/good", "test/scratch", "ground_truth/scratch"):
        (root / cat / sub).mkdir(parents=True)
    img = np.full((64, 64, 3), 200, np.uint8)
    for i in range(n_train_good):
        cv2.imwrite(str(root / cat / "train/good" / f"{i:03d}.png"), img)
    for i in range(n_good_test):
        cv2.imwrite(str(root / cat / "test/good" / f"{i:03d}.png"), img)
    for i in range(n_defect):
        cv2.imwrite(str(root / cat / "test/scratch" / f"{i:03d}.png"), img)
        m = np.zeros((64, 64), np.uint8); m[10:20, 10:30] = 255; m[40:50, 45:55] = 255
        cv2.imwrite(str(root / cat / "ground_truth/scratch" / f"{i:03d}_mask.png"), m)


def test_mask_to_boxes_two_components(tmp_path):
    m = np.zeros((64, 64), np.uint8); m[10:20, 10:30] = 255; m[40:50, 45:55] = 255
    cv2.imwrite(str(tmp_path / "m.png"), m)
    boxes = fv.mask_to_boxes(tmp_path / "m.png", dilate_px=3)
    assert len(boxes) == 2 and all(0 < x < 1 and 0 < y < 1 and 0 < w <= 1 and 0 < h <= 1 for x, y, w, h in boxes)
    assert fv.mask_to_boxes(tmp_path / "m.png", dilate_px=40) == [pytest.approx(b, abs=0.05) for b in [(0.5, 0.47, 0.72, 0.65)]] or len(fv.mask_to_boxes(tmp_path / "m.png", dilate_px=40)) == 1


def test_prepare_split_and_manifest(tmp_path):
    _tree(tmp_path / "raw")
    out = tmp_path / "derived"
    info = fv.prepare(categories=("metal_nut",), seed=1, val_frac=0.5, extra_good_train=2, root=tmp_path / "raw", out=out)
    assert info == dict(yaml=str(out / "data.yaml"), n_train_defect=3, n_train_good=4, n_val_defect=3, n_val_good=2)
    man = json.loads((out / "manifest.json").read_text())
    assert len(man["items"]) == 12 and all(len(it["sha256"]) == 64 for it in man["items"])
    defect_lbl = [l for l in (out / "labels/val").glob("metal_nut__scratch__*.txt")]
    assert defect_lbl and all(len(l.read_text().splitlines()) == 2 for l in defect_lbl)  # two boxes per defect image
    assert "names:\n  0: defect" in (out / "data.yaml").read_text()
    # idempotent (symlinks already present)
    assert fv.prepare(categories=("metal_nut",), seed=1, val_frac=0.5, extra_good_train=2, root=tmp_path / "raw", out=out)["n_val_defect"] == 3


def test_measured_recall_default(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "OUT", tmp_path)
    monkeypatch.setattr(fv, "PUBLISHED", tmp_path / "none")
    assert fv.measured_defect_recall(1.0) == 1.0
    (tmp_path / "summary.json").write_text(json.dumps(dict(image_level=dict(recall=0.87))))
    assert fv.measured_defect_recall() == 0.87
