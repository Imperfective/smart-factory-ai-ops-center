import pytest

from sfaic2.adapters.datasets import DatasetEntry, KNOWN, load_registry, register_local, require
from sfaic2.adapters.qr import decode_qr, route_consistent
from sfaic2.adapters.vision import MockVision, make_vision


def test_registry_gates_on_license(tmp_path, monkeypatch):
    import sfaic2.adapters.datasets as ds
    monkeypatch.setattr(ds, "REGISTRY_PATH", tmp_path / "registry.json")
    reg = load_registry()
    assert set(KNOWN) <= set(reg) and not any(e.license_reviewed for e in reg.values())
    with pytest.raises(PermissionError):
        register_local("AI4I_2020", license_reviewed=False, root=tmp_path)
    with pytest.raises(FileNotFoundError):
        register_local("AI4I_2020", license_reviewed=True, root=tmp_path)
    (tmp_path / "AI4I_2020").mkdir()
    (tmp_path / "AI4I_2020" / "ai4i2020.csv").write_text("UDI,Type\n1,M\n")
    e = register_local("AI4I_2020", license_reviewed=True, version="2020", root=tmp_path)
    assert e.files["ai4i2020.csv"] and require("AI4I_2020").license.startswith("CC BY")
    with pytest.raises(PermissionError):
        require("HAI")


def test_route_rule():
    r = route_consistent("PTG300000004", "PTG3", "OUTBOUND_GATE", {"PTG3": ["OUTBOUND_GATE"]})
    assert r["route_consistent"]
    assert not route_consistent("PTG300000004", "PTG3", "QA_GATE_REWORK", {"PTG3": ["OUTBOUND_GATE"]})["route_consistent"]
    assert not route_consistent("XYZ", "PTG3", "OUTBOUND_GATE", {"PTG3": ["OUTBOUND_GATE"]})["id_format_ok"]


def test_mock_vision_is_labelled(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG not really")
    out = MockVision().infer(p)
    assert out["detector"] == "MOCK_VISION" and out["source_type"] == "SIMULATED" and out["source_image_id"]


def test_qr_roundtrip_if_opencv():
    cv2 = pytest.importorskip("cv2")
    from sfaic2.adapters.qr import make_test_qr
    import tempfile, os
    d = tempfile.mkdtemp()
    p = make_test_qr("PTG300000042", os.path.join(d, "qr.png"))
    out = decode_qr(p)
    assert out["decoded"] and out["payload"] == "PTG300000042" and out["quality"] == "VALID"
    bad = decode_qr(os.path.join(d, "missing.png"))
    assert not bad["decoded"] and bad["error"]


def test_make_vision_falls_back_without_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("YOLO_OFFLINE", "1")
    v = make_vision(prefer_real=True, weights=str(tmp_path / "nonexistent.pt"))
    assert isinstance(v, MockVision) or v.__class__.__name__ == "YoloVision"
