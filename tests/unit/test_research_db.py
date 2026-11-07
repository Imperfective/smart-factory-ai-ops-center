"""Research DB: a real (tiny) coupled run is saved, ingested, aggregated and its paired CI recomputed from DB rows."""
import csv
import json

from sfaic2.experiments.metrics import composite_loss, run_metrics
from sfaic2.experiments.runner import OperatorPolicy, run_coupled
from sfaic2.research import db
from sfaic2.research.design import DOMAIN, SIGNATURE, design_spec, detection_evidence
from sfaic2.runs import save_run
from sfaic2.simulator.scenarios import FAULT_FAMILIES, demo_factory, fault_preset


def _exp(tmp_path):
    root = tmp_path / "experiments" / "t"; (root / "runs").mkdir(parents=True)
    rows = []
    for seed in (1, 2):
        for arm in ("A", "D"):
            cfg = demo_factory("exp_F04", quantity=60, duration_s=900, seed=seed, faults=[fault_preset(FAULT_FAMILIES["F04"], 300, 300, "PRESS_01")])
            run, eng = run_coupled(cfg, seed, arm, "mock", {}, OperatorPolicy())
            m = run_metrics(run, eng, 300, "PRESS_01")
            m.update(composite_loss=composite_loss(m, dict(fn=10, fs=0.5, delay=0.2, energy=1.0, plan=20)), family="F04", fault_kind="SENSOR_FAULT", seed=seed, arm=arm,
                     provider="MOCK_JEV" if arm == "D" else "RULES", run_id=run.run_id)
            save_run(run, eng, root=root / "runs")
            rows.append(m)
    with open(root / "runs_all.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r})); w.writeheader(); w.writerows(rows)
    (root / "manifest.json").write_text(json.dumps(dict(exp="t", created="now", provider="mock", n_runs=4, seeds=[1, 2], arms=["A", "D"], families=["F04"])))
    return tmp_path


def test_rebuild_ingests_runs_metrics_and_raw_rows(tmp_path, monkeypatch):
    base = _exp(tmp_path)
    monkeypatch.setattr(db, "RESULTS", base)
    out = db.rebuild(path=base / "r.sqlite", exp_root=base / "experiments", runs_root=base / "none")
    assert out["experiments"]["t"]["runs"] == 4
    con = db.connect(base / "r.sqlite")
    assert con.execute("SELECT COUNT(*) FROM runs WHERE exp='t'").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 100
    assert con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] > 0
    assert con.execute("SELECT COUNT(*) FROM sim_inputs WHERE grp LIKE 'fault:%' AND key='kind' AND value LIKE '%SENSOR_FAULT%'").fetchone()[0] == 4
    # aggregates are computed from the DB rows and match a direct mean
    vals = [r[0] for r in con.execute("SELECT m.value FROM run_metrics m JOIN runs r ON r.run_id=m.run_id WHERE r.arm='D' AND m.metric='composite_loss'")]
    agg = con.execute("SELECT n, mean FROM aggregates WHERE exp='t' AND grp='arm' AND grp_key='D' AND metric='composite_loss'").fetchone()
    assert agg[0] == 2 and abs(agg[1] - sum(vals) / 2) < 1e-9
    ci = con.execute("SELECT n_pairs FROM paired_ci WHERE exp='t' AND contrast='E-D'").fetchall()
    assert ci == []  # no arm E in this experiment → no fabricated contrast
    csv_text = db.export_csv("runs", exp="t", path=base / "r.sqlite")
    assert csv_text.count("\n") == 5


def test_design_spec_is_read_from_code_and_complete():
    s = design_spec()
    assert set(s["questions"]) == {"likely_cause", "review_needed", "risk_band", "stop_warranted"}
    assert {k["kind"] for k in s["fault_kinds"]} == set(SIGNATURE) and all(k["kind"] in DOMAIN for k in s["fault_kinds"])
    assert [a["arm"] for a in s["arms"]] == ["N", "A", "B", "C", "D", "E"]


def test_detection_evidence_handles_missing_db(tmp_path):
    assert detection_evidence("x", path=tmp_path / "none.sqlite") == dict(exists=False)
