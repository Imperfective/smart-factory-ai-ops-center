"""AI4I 2020 Predictive Maintenance Dataset (UCI, CC BY 4.0) — a *synthetic-by-source* public dataset used as an
independent predictive-maintenance modality reference (docs/05). Downloaded only on explicit request, hashed and
registered with the licence; evaluated with a fixed stratified split (fit on train only) and a simple baseline.
Nothing here is joined to the factory simulation timeline."""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

URL = "https://archive.ics.uci.edu/static/public/601/ai4i+2020+predictive+maintenance+dataset.zip"
DATASET_ID = "AI4I_2020"


def download(root: Path = Path("data/raw")) -> Path:
    import httpx
    d = root / DATASET_ID
    d.mkdir(parents=True, exist_ok=True)
    csv = d / "ai4i2020.csv"
    if not csv.exists():
        r = httpx.get(URL, timeout=120, follow_redirects=True)
        r.raise_for_status()
        (d / "ai4i_2020.zip").write_bytes(r.content)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            csv.write_bytes(z.read(name))
    return csv


def run_ai4i(seed: int = 42, out_dir: Path = Path("results/public/ai4i")) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    from .datasets import register_local, provenance
    csv = download()
    entry = register_local(DATASET_ID, license_reviewed=True, version="UCI-601 (2020), CC BY 4.0")
    df = pd.read_csv(csv)
    feats = ["Air temperature [K]", "Process temperature [K]", "Rotational speed [rpm]", "Torque [Nm]", "Tool wear [min]"]
    X = df[feats].to_numpy(float)
    y = df["Machine failure"].to_numpy(int)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, stratify=y, random_state=seed)
    sc = StandardScaler().fit(Xtr)                       # fit on train only
    clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(sc.transform(Xtr), ytr)
    p = clf.predict_proba(sc.transform(Xte))[:, 1]
    thr = 0.5                                           # fixed a priori; no test tuning
    pred = (p >= thr).astype(int)
    # robust z-score detector (same family as the twin's ML baseline), threshold fixed a priori at 3.5 on any feature
    med = np.median(Xtr, axis=0); mad = np.median(np.abs(Xtr - med), axis=0) + 1e-9
    z = np.max(np.abs(Xte - med) / (1.4826 * mad), axis=1)
    zpred = (z > 3.5).astype(int)
    metrics = dict(n_train=int(len(ytr)), n_test=int(len(yte)), prevalence_test=float(yte.mean()),
                   logreg=dict(auroc=float(roc_auc_score(yte, p)), auprc=float(average_precision_score(yte, p)), precision=float(precision_score(yte, pred, zero_division=0)),
                               recall=float(recall_score(yte, pred, zero_division=0)), f1=float(f1_score(yte, pred, zero_division=0)), threshold=thr),
                   robust_z=dict(auroc=float(roc_auc_score(yte, z)), auprc=float(average_precision_score(yte, z)), precision=float(precision_score(yte, zpred, zero_division=0)),
                                 recall=float(recall_score(yte, zpred, zero_division=0)), f1=float(f1_score(yte, zpred, zero_division=0)), threshold=3.5),
                   failure_modes=df[["TWF", "HDF", "PWF", "OSF", "RNF"]].sum().to_dict())
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = dict(dataset=DATASET_ID, url=URL, license=entry.license, sha256=entry.files["ai4i2020.csv"], n_rows=int(len(df)), seed=seed,
               split="stratified 70/30, scaler and model fit on train only, thresholds fixed a priori", timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               provenance_example=provenance(DATASET_ID, "ai4i2020.csv", row_id=int(df.UDI.iloc[0])), metrics=metrics,
               note="Public synthetic-by-source dataset; separate modality reference; NOT joined to the factory twin timeline")
    (out_dir / "ai4i_baseline.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    pd.DataFrame([dict(model=k, **v) for k, v in metrics.items() if isinstance(v, dict) and "auroc" in v]).to_csv(out_dir / "metrics.csv", index=False)
    return rec
