"""Public dataset registry with licence gating and provenance (docs/05). Nothing is downloaded automatically;
the user places files under data/raw/<dataset_id>/ after accepting the source terms. Loaders refuse to run until
`license_reviewed` is set in the registry entry, and every loaded file is hashed."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGISTRY_PATH = Path("data/registry.json")
DATA_ROOT = Path("data/raw")

KNOWN = {
    "HAI": dict(url="https://github.com/icsdataset/hai", nature="OBSERVED_PUBLIC", license="see repository terms", note="attack label = cyberattack, NOT machine failure"),
    "AI4I_2020": dict(url="https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset", nature="OBSERVED_PUBLIC (synthetic by source)", license="CC BY 4.0 (per UCI)", note="synthetic dataset; not a real plant"),
    "MVTEC_LOCO_AD": dict(url="https://www.mvtec.com/research-teaching/datasets/mvtec-loco-ad", nature="OBSERVED_PUBLIC", license="CC BY-NC-SA 4.0 (non-commercial)", note="no PLC/QR alignment; images only"),
    "JOBSHOP": dict(url="https://github.com/ScheduleOpt/benchmarks/tree/main/jobshop", nature="OBSERVED_PUBLIC", license="see repository", note="scheduling instances; not press C/T"),
    "PADERBORN_BEARING": dict(url="https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter/data-sets-and-download", nature="OBSERVED_PUBLIC", license="see source", note="bearing current/vibration"),
    "MVTEC_AD": dict(url="https://www.mvtec.com/company/research/datasets/mvtec-ad (mirror: https://huggingface.co/datasets/foersben/mvtec-ad)", nature="OBSERVED_PUBLIC", license="CC BY-NC-SA 4.0 (non-commercial research only; cite Bergmann et al. CVPR 2019 / IJCV 2021)", note="industrial part images with defect masks (metal_nut, screw, cable used); YOLO perception-layer training/eval; no PLC alignment"),
}


@dataclass
class DatasetEntry:
    dataset_id: str
    url: str
    license: str
    nature: str
    note: str
    license_reviewed: bool = False
    version: str | None = None
    files: dict = field(default_factory=dict)  # relative path -> sha256
    registered_at: str | None = None


def load_registry() -> dict[str, DatasetEntry]:
    if not REGISTRY_PATH.exists():
        return {k: DatasetEntry(dataset_id=k, **v) for k, v in KNOWN.items()}
    raw = json.loads(REGISTRY_PATH.read_text())
    reg = {k: DatasetEntry(**v) for k, v in raw.items()}
    for k, v in KNOWN.items():  # newly known datasets appear even in an older registry file
        reg.setdefault(k, DatasetEntry(dataset_id=k, **v))
    return reg


def save_registry(reg: dict[str, DatasetEntry]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps({k: asdict(v) for k, v in reg.items()}, indent=2), encoding="utf-8")


def register_local(dataset_id: str, license_reviewed: bool, version: str | None = None, root: Path = DATA_ROOT) -> DatasetEntry:
    reg = load_registry()
    if dataset_id not in reg:
        raise KeyError(f"unknown dataset {dataset_id}; add it to KNOWN with its licence first")
    e = reg[dataset_id]
    if not license_reviewed:
        raise PermissionError(f"{dataset_id}: licence must be reviewed and accepted by the user before registration ({e.url})")
    d = root / dataset_id
    if not d.exists():
        raise FileNotFoundError(f"place the official download under {d} (not redistributed with this repo)")
    files = {}
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        files[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    e.license_reviewed, e.version, e.files, e.registered_at = True, version, files, time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_registry(reg)
    return e


def require(dataset_id: str) -> DatasetEntry:
    e = load_registry().get(dataset_id)
    if e is None or not e.license_reviewed or not e.files:
        raise PermissionError(f"dataset {dataset_id} not registered with a reviewed licence; run register_local() after manual download")
    return e


def provenance(dataset_id: str, rel_path: str, row_id: str | int | None = None) -> dict:
    e = require(dataset_id)
    return dict(source_type="OBSERVED_PUBLIC", dataset_id=dataset_id, dataset_version=e.version, original_row_id=row_id,
                sha256=e.files.get(rel_path), license=e.license, source_label=f"PUBLIC:{dataset_id}")
