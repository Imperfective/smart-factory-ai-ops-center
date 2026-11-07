"""Research database — one SQLite file that makes the whole evidence chain queryable:

    public datasets ──► measured statistics ──► simulation inputs ──► runs ──► per-run metrics ──► aggregates / paired CIs
    (registry, licence)  (AI4I, MVTec AD,       (scenario.json +     (experiment  (metrics.json +   (computed HERE, in SQL /
                          Jev smoke)             provenance)          + live)      decision metrics)  bootstrap from the DB rows)

Everything that lands in a paper table is re-derivable from the rows in this file; raw events / truth / decisions /
commands of every saved run are loaded too, so the data behind every number is in the database and not only in JSONL.
Rebuild is idempotent (`rebuild()`); live sessions append themselves when they finish (`ingest_run_dir`).
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

DB_PATH = Path("results/research.sqlite")
RESULTS = Path("results")
PRIMARY_METRICS = ["composite_loss", "correct_action_rate", "harmful_false_continue", "false_stop_proposals", "unnecessary_stop_min", "unnecessary_review",
                   "f1", "precision", "recall", "plan_attainment", "energy_kwh_per_good_part", "intervention_delay_s", "jev_calls", "jev_fallbacks",
                   "good_count", "oee", "total_energy_kwh", "min_bus_voltage_pu", "max_transformer_loading_pct", "vision_defects", "vision_misses", "qr_misroutes"]
CONTRASTS = [("E", "N"), ("E", "A"), ("E", "B"), ("E", "C"), ("E", "D"), ("C", "N"), ("C", "B"), ("D", "C")]
EXPORTABLE = ("datasets", "public_stats", "lineage", "experiments", "runs", "run_metrics", "sim_inputs", "aggregates", "paired_ci", "decisions", "commands", "truth", "events", "study_responses")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS datasets (dataset_id TEXT PRIMARY KEY, url TEXT, licence TEXT, nature TEXT, note TEXT, license_reviewed INTEGER, version TEXT, n_files INTEGER, registered_at TEXT, used_for TEXT);
CREATE TABLE IF NOT EXISTS public_stats (stat_id TEXT PRIMARY KEY, dataset_id TEXT, scope TEXT, metric TEXT, value REAL, text TEXT, measured_at TEXT, source_file TEXT, feeds TEXT);
CREATE TABLE IF NOT EXISTS lineage (edge_id INTEGER PRIMARY KEY AUTOINCREMENT, from_kind TEXT, from_id TEXT, to_kind TEXT, to_id TEXT, relation TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS experiments (exp TEXT PRIMARY KEY, created TEXT, provider TEXT, n_runs INTEGER, seeds TEXT, arms TEXT, families TEXT, wall_seconds REAL, jev_models TEXT, primary_endpoints TEXT, weights TEXT, severity REAL, duration_s REAL, quantity INTEGER);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, exp TEXT, family TEXT, fault_kind TEXT, seed INTEGER, arm TEXT, arm_label TEXT, provider TEXT, scenario_id TEXT, source_type TEXT,
    sim_seconds REAL, wall_seconds REAL, n_events INTEGER, n_truth INTEGER, n_decisions INTEGER, n_commands INTEGER, events_sha256 TEXT, config_sha256 TEXT,
    jev_resolved_model TEXT, jev_calls INTEGER, jev_cost_usd REAL, jev_fallbacks INTEGER, live INTEGER, ingested_at TEXT, path TEXT);
CREATE TABLE IF NOT EXISTS run_metrics (run_id TEXT, metric TEXT, value REAL, PRIMARY KEY (run_id, metric));
CREATE TABLE IF NOT EXISTS sim_inputs (run_id TEXT, grp TEXT, key TEXT, value TEXT, source TEXT, PRIMARY KEY (run_id, grp, key));
CREATE TABLE IF NOT EXISTS events (run_id TEXT, event_id TEXT, sim_time_s REAL, asset_id TEXT, source_type TEXT, signal TEXT, value TEXT, value_num REAL, unit TEXT, quality TEXT, source_label TEXT);
CREATE TABLE IF NOT EXISTS truth (run_id TEXT, sim_time_s REAL, machine_id TEXT, physical_state TEXT, truth_classes TEXT, expected_safe_action TEXT, harmful_stop_warranted INTEGER, active_fault_ids TEXT);
CREATE TABLE IF NOT EXISTS decisions (run_id TEXT, decision_id TEXT, sim_time_s REAL, asset_id TEXT, provider TEXT, arm TEXT, recommendation TEXT, risk_band TEXT, causes TEXT, model_version TEXT,
    safety_gate_status TEXT, context_hash TEXT, jev_latency_ms REAL, jev_resolved_model TEXT, n_conflicts INTEGER, n_stale INTEGER, n_missing INTEGER, downgraded TEXT,
    conflicts TEXT, judge_provider TEXT, judge_rec TEXT, judge_cause TEXT, judge_cause_p REAL, judge_review_p REAL, judge_stop_p REAL, judge_status TEXT, judge_cached INTEGER, rules_rec TEXT, ml_rec TEXT);
CREATE TABLE IF NOT EXISTS commands (run_id TEXT, command_id TEXT, target_id TEXT, action TEXT, requested_by TEXT, proposed_sim_s REAL, decision_id TEXT, status TEXT, reason TEXT, ack_sim_s REAL, policy_version TEXT);
CREATE TABLE IF NOT EXISTS aggregates (exp TEXT, grp TEXT, grp_key TEXT, metric TEXT, n INTEGER, mean REAL, sd REAL, min REAL, max REAL, PRIMARY KEY (exp, grp, grp_key, metric));
CREATE TABLE IF NOT EXISTS paired_ci (exp TEXT, metric TEXT, contrast TEXT, n_pairs INTEGER, mean_diff REAL, ci_low REAL, ci_high REAL, excludes_zero INTEGER, PRIMARY KEY (exp, metric, contrast));
CREATE TABLE IF NOT EXISTS study_responses (response_id TEXT PRIMARY KEY, session_id TEXT, run_id TEXT, participant TEXT, created REAL, item_id TEXT, asset_id TEXT, sim_time_s REAL, stratum TEXT,
    human_action TEXT, human_cause TEXT, jev_action TEXT, rules_action TEXT, expected_safe_action TEXT, truth_classes TEXT, human_correct INTEGER, jev_correct INTEGER, rules_correct INTEGER, response_ms REAL);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, asset_id, signal);
CREATE INDEX IF NOT EXISTS ix_truth_run ON truth(run_id, machine_id, sim_time_s);
CREATE INDEX IF NOT EXISTS ix_dec_run ON decisions(run_id, asset_id);
CREATE INDEX IF NOT EXISTS ix_rm_metric ON run_metrics(metric);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------------------------- public data + stats
def ingest_datasets(con: sqlite3.Connection) -> int:
    from ..adapters.datasets import load_registry
    used = {"AI4I_2020": "ML anomaly baseline reference (robust-z / logistic) on an independent public modality",
            "MVTEC_AD": "YOLO11n perception layer: fine-tune + held-out image-level recall → scenario S4 defect_recall"}
    n = 0
    for k, e in load_registry().items():
        con.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (k, e.url, e.license, e.nature, e.note, int(e.license_reviewed), e.version, len(e.files or {}), e.registered_at, used.get(k, "registered only (not loaded)")))
        n += 1
    return n


def ingest_public_stats(con: sqlite3.Connection) -> int:
    rows = []

    def add(ds, scope, metric, value, feeds=None, text=None, measured_at=None, src=None):
        rows.append((f"{ds}:{scope}:{metric}", ds, scope, metric, _num(value), text, measured_at, src, feeds))

    p = RESULTS / "public/ai4i/ai4i_baseline.json"
    if p.exists():
        d = json.loads(p.read_text())
        m = d["metrics"]
        add("AI4I_2020", "test_split", "n_train", m["n_train"], measured_at=d["timestamp"], src=str(p))
        add("AI4I_2020", "test_split", "n_test", m["n_test"], measured_at=d["timestamp"], src=str(p))
        add("AI4I_2020", "test_split", "prevalence", m["prevalence_test"], measured_at=d["timestamp"], src=str(p))
        for model in ("logreg", "robust_z"):
            for k, v in m[model].items():
                add("AI4I_2020", model, k, v, text="independent modality reference (same estimator family as the arm-B robust-z ML); NOT a simulation input",
                    measured_at=d["timestamp"], src=str(p))
    p = RESULTS / "vision/summary.json"
    if p.exists():
        d = json.loads(p.read_text())
        il = d["image_level"]
        for k in ("TP", "FP", "FN", "TN", "precision", "recall", "accuracy", "f1", "conf_threshold"):
            add("MVTEC_AD", "image_level", k, il.get(k), feeds="scenario S4 fault param defect_recall" if k == "recall" else None, src=str(p))
        for c, v in d.get("per_category", {}).items():
            add("MVTEC_AD", f"category:{c}", "recall", v.get("recall"), src=str(p))
            add("MVTEC_AD", f"category:{c}", "n", (v.get("TP", 0) + v.get("FN", 0) + v.get("TN", 0) + v.get("FP", 0)), src=str(p))
        for k, v in (d.get("box_metrics") or {}).items():
            add("MVTEC_AD", "box", k, v, src=str(p))
        add("MVTEC_AD", "latency_mps", "median_ms", d.get("median_ms"), src=str(p))
        add("MVTEC_AD", "latency_mps", "p95_ms", d.get("p95_ms"), src=str(p))
        add("MVTEC_AD", "dataset", "n_val_images", d.get("n_images"), text=d.get("protocol"), src=str(p))
        tr = d.get("train") or {}
        add("MVTEC_AD", "train", "epochs", tr.get("epochs"), text=f"{tr.get('base')} → {str(tr.get('best_sha256'))[:12]}", src=str(p))
        add("MVTEC_AD", "train", "train_s", tr.get("train_s"), src=str(p))
    for p in sorted((RESULTS / "jev_smoke").glob("*.json")):
        d = json.loads(p.read_text())
        c = d.get("call") or {}
        add("TYPESAFE_JEV", "smoke", "latency_ms", c.get("latency_ms") or d.get("wall_ms"), text=c.get("resolved_model"), measured_at=d.get("timestamp"), src=str(p))
        u = c.get("usage") or {}
        for k in ("input_tokens", "output_tokens"):
            if k in u:
                add("TYPESAFE_JEV", "smoke", k, u[k], measured_at=d.get("timestamp"), src=str(p))
        break
    con.executemany("INSERT OR REPLACE INTO public_stats VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# ----------------------------------------------------------------------------------------------- runs
def describe_inputs(cfg: dict, seed, arm: str | None, provider: str | None, extra: dict | None = None) -> list[dict]:
    """Flatten a FactoryConfig (dict) into (group, key, value, source) rows so the UI can show *what the simulation used*
    and where each number comes from (synthetic DEMO spec, pre-registered design, or a MEASURED public statistic)."""
    rows = []

    def add(grp, key, value, source):
        rows.append(dict(grp=grp, key=key, value=value if isinstance(value, str) else json.dumps(value, default=str), source=source))

    add("run", "scenario_id", cfg.get("scenario_id"), "design")
    add("run", "seed", seed, "common random numbers (pre-registered)")
    add("run", "arm", arm, "pre-registered arm set N/A/B/C/D/E")
    add("run", "judgment_provider", provider, "runtime setting")
    sim = cfg.get("simulation") or {}
    for k, v in sim.items():
        add("simulation", k, v, "design (docs/04)")
    plan = cfg.get("plan") or {}
    for k, v in plan.items():
        add("plan", k, v, "SYNTHETIC production plan (docs/04 DEMO)")
    for m in cfg.get("machines") or []:
        mid = m.get("machine_id")
        for k in ("stage", "count", "cycle_time_s", "cycle_time_cv", "yield_rate", "mtbf_s", "mttr_s", "feeder_id"):
            add(f"machine:{mid}", k, m.get(k), "SYNTHETIC equipment spec (not measured)")
        for k, v in (m.get("power") or {}).items():
            add(f"machine:{mid}", f"power.{k}", v, "SYNTHETIC load model (docs/06)")
    for b in cfg.get("buffers") or []:
        add("buffers", b.get("buffer_id"), dict(before_stage=b.get("before_stage"), capacity=b.get("capacity")), "SYNTHETIC")
    for k, v in (cfg.get("network") or {}).items():
        add("network", k, v, "OT network model (docs/06), scenario-specific")
    for k, v in (cfg.get("electrical") or {}).items():
        add("electrical", k, v, "pandapower DEMO topology (docs/06), SYNTHETIC_EXAMPLE_NOT_MEASURED")
    for f in cfg.get("faults") or []:
        fid = f.get("fault_id")
        for k in ("kind", "machine_id", "at_s", "duration_s", "severity", "truth_class", "expected_safe_action", "harmful_stop_warranted"):
            add(f"fault:{fid}", k, f.get(k), "fault family design (docs/13), hidden truth")
        for k, v in (f.get("params") or {}).items():
            src = "MEASURED: MVTEC_AD image_level.recall (results/vision/summary.json)" if k == "defect_recall" else "fault family design (docs/13)"
            add(f"fault:{fid}", f"params.{k}", v, src)
    for k, v in (extra or {}).items():
        add("runtime", k, v, "runtime")
    return rows


def _decision_rows(run_id: str, path: Path):
    out = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            r = d.get("rationale") or {}
            b = r.get("bundle") or {}
            jc = d.get("jev_call") or {}
            comps = r.get("components") or {}
            jp = next((k for k in ("TYPESAFE_JEV", "MOCK_JEV") if k in comps), None)
            j = comps.get(jp) or {}
            jr = j.get("rationale") or {}
            probs = jr.get("likely_cause_probabilities") or {}
            cause = (j.get("causes") or [None])[0]
            out.append((run_id, d.get("decision_id"), d.get("sim_time_s"), d.get("asset_id"), d.get("provider"), d.get("arm"), d.get("recommendation"), d.get("risk_band"),
                        json.dumps(d.get("causes")), d.get("model_version"), d.get("safety_gate_status"), d.get("context_hash"),
                        _num(jc.get("latency_ms")), jc.get("resolved_model"), len(b.get("conflicts") or []), len(b.get("stale") or []), len(b.get("missing") or []),
                        json.dumps(r.get("downgraded")) if r.get("downgraded") else None,
                        json.dumps(b.get("conflicts") or []), jp, j.get("recommendation"), cause, _num(probs.get(cause)) if cause else None,
                        _num(jr.get("review_needed_p")), _num(jr.get("stop_warranted_p")), jc.get("status"), int(bool(jc.get("cached"))) if jc else None,
                        (comps.get("RULES") or {}).get("recommendation"), (comps.get("ML") or {}).get("recommendation")))
    return out


def run_key(exp: str, run_id: str) -> str:
    """DB primary key: experiments reuse run ids across matrices (e.g. the rules-only arms), so keys are scoped by experiment."""
    return run_id if exp == "live" else f"{exp}:{run_id}"


def ingest_run_dir(con: sqlite3.Connection, d: Path, exp: str, csv_row: dict | None = None, live: bool = False, with_raw: bool = True) -> str | None:
    """Load one saved run directory (scenario.json, run_manifest.json, metrics.json, *.jsonl) into the DB."""
    man_p, met_p, sc_p = d / "run_manifest.json", d / "metrics.json", d / "scenario.json"
    if not (man_p.exists() and met_p.exists() and sc_p.exists()):
        return None
    man = json.loads(man_p.read_text()); met = json.loads(met_p.read_text()); cfg = json.loads(sc_p.read_text())
    run_id = run_key(exp, man["run_id"])
    csv_row = csv_row or {}
    faults = cfg.get("faults") or []
    family = csv_row.get("family") or (cfg.get("scenario_id") or "").replace("exp_", "") or "live"
    fault_kind = csv_row.get("fault_kind") or (faults[0]["kind"] if faults else "NONE")
    jev_usage = man.get("jev_usage") or {}
    con.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, exp, family, fault_kind, int(man.get("seed") if man.get("seed") is not None else (csv_row.get("seed") or 0)), man.get("arm") or csv_row.get("arm"),
                 csv_row.get("arm_label") or (man.get("arm_config") or {}).get("label"), csv_row.get("provider") or man.get("provider") or (man.get("arm_config") or {}).get("provider"),
                 cfg.get("scenario_id"), man.get("source_type", "SIMULATED"), _num(man.get("sim_seconds")), _num(man.get("wall_seconds")), man.get("n_events"), man.get("n_truth"),
                 man.get("n_decisions"), man.get("n_commands"), man.get("events_sha256"), man.get("config_sha256"), csv_row.get("jev_resolved_model") or jev_usage.get("resolved_model"),
                 int(_num(csv_row.get("jev_calls")) or jev_usage.get("calls") or 0), _num(csv_row.get("jev_cost_usd")) or _num(jev_usage.get("estimated_cost_usd")) or 0.0,
                 int(_num(csv_row.get("jev_fallbacks")) or man.get("jev_fallbacks") or 0), int(live), _now(), str(d)))
    # metrics: KPIs from metrics.json + decision metrics from the experiment csv row (or recomputed for live runs)
    con.execute("DELETE FROM run_metrics WHERE run_id=?", (run_id,))
    mrows = {}
    for k, v in met.items():
        if _num(v) is not None and not isinstance(v, bool):
            mrows[k] = _num(v)
    for k, v in csv_row.items():
        if k in ("seed",) or k in mrows:
            continue
        if _num(v) is not None:
            mrows[k] = _num(v)
    if not csv_row:  # live run: recompute decision-level accuracy from saved truth/decisions
        acc = _live_accuracy_from_files(d)
        for k, v in acc.items():
            if _num(v) is not None:
                mrows[k] = _num(v)
    con.executemany("INSERT OR REPLACE INTO run_metrics VALUES (?,?,?)", [(run_id, k, v) for k, v in mrows.items()])
    con.execute("DELETE FROM sim_inputs WHERE run_id=?", (run_id,))
    con.executemany("INSERT OR REPLACE INTO sim_inputs VALUES (?,?,?,?,?)",
                    [(run_id, r["grp"], r["key"], r["value"], r["source"]) for r in describe_inputs(cfg, man.get("seed"), man.get("arm"), csv_row.get("provider") or man.get("provider"),
                                                                                                    extra=dict(live=live, speed=man.get("speed")) if live else None)])
    if with_raw:
        for t in ("events", "truth", "decisions", "commands"):
            con.execute(f"DELETE FROM {t} WHERE run_id=?", (run_id,))
        if (d / "events.jsonl").exists():
            with open(d / "events.jsonl", encoding="utf-8") as fh:
                batch = []
                for line in fh:
                    e = json.loads(line)
                    v = e.get("value")
                    batch.append((run_id, e.get("event_id"), e.get("sim_time_s"), e.get("asset_id"), e.get("source_type"), e.get("signal"),
                                  v if isinstance(v, str) else json.dumps(v), _num(v) if not isinstance(v, (str, dict, list)) else None, e.get("unit"), e.get("quality"),
                                  (e.get("provenance") or {}).get("source_label")))
                    if len(batch) >= 5000:
                        con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch); batch = []
                if batch:
                    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
        if (d / "truth.jsonl").exists():
            with open(d / "truth.jsonl", encoding="utf-8") as fh:
                con.executemany("INSERT INTO truth VALUES (?,?,?,?,?,?,?,?)",
                                [(run_id, t.get("sim_time_s"), t.get("machine_id"), t.get("physical_state"), json.dumps(t.get("truth_classes")), t.get("expected_safe_action"),
                                  int(bool(t.get("harmful_stop_warranted"))), json.dumps(t.get("active_fault_ids"))) for t in map(json.loads, fh)])
        con.executemany("INSERT INTO decisions VALUES (" + ",".join("?" * 29) + ")", _decision_rows(run_id, d / "decisions.jsonl"))
        if (d / "commands.jsonl").exists():
            with open(d / "commands.jsonl", encoding="utf-8") as fh:
                con.executemany("INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                [(run_id, c.get("command_id"), c.get("target_id"), c.get("action"), c.get("requested_by"), c.get("proposed_sim_s"), c.get("decision_id"),
                                  c.get("status"), c.get("reason"), c.get("ack_sim_s"), c.get("policy_version")) for c in map(json.loads, fh)])
    return run_id


def _live_accuracy_from_files(d: Path) -> dict:
    """Decision-level accuracy for a live run, recomputed from the saved truth/decisions (same rule as live_accuracy)."""
    from collections import defaultdict
    if not (d / "truth.jsonl").exists() or not (d / "decisions.jsonl").exists():
        return {}
    idx = defaultdict(list)
    for t in map(json.loads, open(d / "truth.jsonl", encoding="utf-8")):
        idx[t["machine_id"]].append(t)
    tp = fp = fn = tn = 0; correct = n_eval = 0; false_stop = harmful = 0; n = 0
    for dec in map(json.loads, open(d / "decisions.jsonl", encoding="utf-8")):
        n += 1
        cands = [x for x in idx.get(dec["asset_id"], []) if abs(x["sim_time_s"] - dec["sim_time_s"]) <= 15]
        if not cands:
            continue
        tr = min(cands, key=lambda x: abs(x["sim_time_s"] - dec["sim_time_s"]))
        anomalous = tr["truth_classes"] != ["NONE"]; alert = dec["recommendation"] != "CONTINUE_MONITORING"
        tp += anomalous and alert; fp += (not anomalous) and alert; fn += anomalous and not alert; tn += (not anomalous) and not alert
        if anomalous:
            n_eval += 1; correct += dec["recommendation"] == tr["expected_safe_action"]
        if dec["recommendation"] == "REQUEST_SIMULATED_STOP" and not tr["harmful_stop_warranted"]:
            false_stop += 1
        if tr["harmful_stop_warranted"] and dec["recommendation"] == "CONTINUE_MONITORING":
            harmful += 1
    prec = tp / (tp + fp) if tp + fp else None; rec = tp / (tp + fn) if tp + fn else None
    return dict(n_decisions=n, tp=tp, fp=fp, fn=fn, tn=tn, precision=prec, recall=rec, f1=(2 * prec * rec / (prec + rec)) if prec and rec else None,
                correct_action_rate=(correct / n_eval) if n_eval else None, false_stop_proposals=false_stop, harmful_false_continue=harmful)


def ingest_experiment(con: sqlite3.Connection, exp_dir: Path, with_raw: bool = True) -> int:
    man_p = exp_dir / "manifest.json"
    if not man_p.exists():
        return 0
    man = json.loads(man_p.read_text())
    exp = man.get("exp") or exp_dir.name
    con.execute("INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (exp, man.get("created"), man.get("provider"), man.get("n_runs"), json.dumps(man.get("seeds")), json.dumps(man.get("arms")), json.dumps(man.get("families")),
                 _num(man.get("wall_seconds")), json.dumps(man.get("jev_models")), json.dumps(man.get("primary_endpoints")), json.dumps(man.get("weights")),
                 _num(man.get("severity")), _num(man.get("duration_s")), man.get("quantity")))
    rows = {}
    if (exp_dir / "runs_all.csv").exists():
        with open(exp_dir / "runs_all.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["run_id"]] = r
    n = 0
    runs_dir = exp_dir / "runs"
    for src_id, r in rows.items():
        d = runs_dir / src_id
        run_id = run_key(exp, src_id)
        if d.exists():
            ingest_run_dir(con, d, exp, csv_row=r, with_raw=with_raw); n += 1
        else:  # csv-only (runs not saved): still record the run + its metrics
            con.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            con.execute("INSERT INTO runs (run_id, exp, family, fault_kind, seed, arm, arm_label, provider, source_type, events_sha256, jev_resolved_model, jev_calls, jev_cost_usd, jev_fallbacks, live, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                        (run_id, exp, r.get("family"), r.get("fault_kind"), int(_num(r.get("seed")) or 0), r.get("arm"), r.get("arm_label"), r.get("provider"), r.get("source_type"),
                         r.get("events_sha256"), r.get("jev_resolved_model"), int(_num(r.get("jev_calls")) or 0), _num(r.get("jev_cost_usd")) or 0.0, int(_num(r.get("jev_fallbacks")) or 0), _now()))
            con.execute("DELETE FROM run_metrics WHERE run_id=?", (run_id,))
            con.executemany("INSERT OR REPLACE INTO run_metrics VALUES (?,?,?)", [(run_id, k, _num(v)) for k, v in r.items() if _num(v) is not None and k != "seed"])
            n += 1
    con.commit()
    return n


def ingest_live_runs(con: sqlite3.Connection, root: Path = RESULTS / "runs", with_raw: bool = True) -> int:
    n = 0
    for d in sorted(p for p in root.glob("*") if p.is_dir()):
        if ingest_run_dir(con, d, "live", live=True, with_raw=with_raw):
            n += 1
    con.commit()
    return n


def ingest_study(con: sqlite3.Connection, study_db: Path = RESULTS / "study.sqlite") -> int:
    if not study_db.exists():
        return 0
    src = sqlite3.connect(study_db)
    rows = []
    q = """SELECT r.response_id, r.session_id, s.run_id, r.participant, r.created, r.item_id, i.asset_id, i.sim_time_s, i.stratum, r.action, r.likely_cause,
                  i.jev_json, i.rules_json, i.truth_json, r.response_ms FROM study_responses r JOIN study_items i ON i.item_id=r.item_id JOIN study_sessions s ON s.session_id=r.session_id"""
    for rid, sid, run_id, part, created, item, asset, t, stratum, action, cause, jev_j, rules_j, truth_j, ms in src.execute(q):
        jev = json.loads(jev_j or "{}"); rules = json.loads(rules_j or "{}"); truth = json.loads(truth_j or "{}")
        exp_a = truth.get("expected_safe_action") or truth.get("expected_action"); ja = jev.get("action") or jev.get("recommendation"); ra = rules.get("action") or rules.get("recommendation")
        rows.append((rid, sid, run_id, part, created, item, asset, t, stratum, action, cause, ja, ra, exp_a, json.dumps(truth.get("truth_classes") or truth.get("classes")),
                     int(action == exp_a) if exp_a else None, int(ja == exp_a) if exp_a and ja else None, int(ra == exp_a) if exp_a and ra else None, ms))
    con.executemany("INSERT OR REPLACE INTO study_responses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


# ----------------------------------------------------------------------------------------------- statistics from the DB
def compute_aggregates(con: sqlite3.Connection, exp: str) -> int:
    con.execute("DELETE FROM aggregates WHERE exp=?", (exp,))
    con.execute("DELETE FROM paired_ci WHERE exp=?", (exp,))
    runs = con.execute("SELECT run_id, arm, family, seed FROM runs WHERE exp=?", (exp,)).fetchall()
    if not runs:
        return 0
    rid_to = {r[0]: r[1:] for r in runs}
    vals = defaultdict(dict)  # metric -> run_id -> value
    for run_id, metric, value in con.execute(f"SELECT run_id, metric, value FROM run_metrics WHERE run_id IN ({','.join('?' * len(rid_to))}) AND value IS NOT NULL", list(rid_to)):
        vals[metric][run_id] = value
    import statistics
    agg = []
    for metric, by_run in vals.items():
        groups = defaultdict(list)
        for run_id, v in by_run.items():
            arm, fam, _ = rid_to[run_id]
            groups[("arm", arm)].append(v); groups[("arm_family", f"{arm}|{fam}")].append(v); groups[("family", fam)].append(v)
        for (g, key), xs in groups.items():
            agg.append((exp, g, key, metric, len(xs), statistics.fmean(xs), statistics.stdev(xs) if len(xs) > 1 else 0.0, min(xs), max(xs)))
    con.executemany("INSERT OR REPLACE INTO aggregates VALUES (?,?,?,?,?,?,?,?,?)", agg)
    # paired bootstrap recomputed from DB rows (same estimator as the experiment runner)
    import numpy as np
    ci = []
    for metric in [m for m in PRIMARY_METRICS if m in vals]:
        for a, b in CONTRASTS:
            pa = {(fam, seed): v for run_id, v in vals[metric].items() for (arm, fam, seed) in [rid_to[run_id]] if arm == a}
            pb = {(fam, seed): v for run_id, v in vals[metric].items() for (arm, fam, seed) in [rid_to[run_id]] if arm == b}
            keys = sorted(set(pa) & set(pb))
            d = np.array([pa[k] - pb[k] for k in keys], float)
            if len(d) == 0:
                continue
            rng = np.random.default_rng(2026)
            boots = np.array([rng.choice(d, len(d), replace=True).mean() for _ in range(2000)])
            lo, hi = float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))
            ci.append((exp, metric, f"{a}-{b}", int(len(d)), float(d.mean()), lo, hi, int(lo > 0 or hi < 0)))
    con.executemany("INSERT OR REPLACE INTO paired_ci VALUES (?,?,?,?,?,?,?,?)", ci)
    con.commit()
    return len(agg)


def build_lineage(con: sqlite3.Connection) -> int:
    con.execute("DELETE FROM lineage")
    edges = []
    for ds, in con.execute("SELECT dataset_id FROM datasets WHERE license_reviewed=1"):
        for sid, scope, metric in con.execute("SELECT stat_id, scope, metric FROM public_stats WHERE dataset_id=?", (ds,)):
            edges.append(("dataset", ds, "stat", sid, "measured", f"{scope}.{metric}"))
    for sid, feeds in con.execute("SELECT stat_id, feeds FROM public_stats WHERE feeds IS NOT NULL"):
        edges.append(("stat", sid, "sim_input", feeds, "parameterises", None))
    for exp, n in con.execute("SELECT exp, COUNT(*) FROM runs GROUP BY exp"):
        edges.append(("sim_input", "scenario.json (per run)", "runs", exp, "configures", f"{n} runs"))
        edges.append(("runs", exp, "run_metrics", exp, "metrics.json + decision metrics", None))
        edges.append(("run_metrics", exp, "aggregates", exp, "mean/sd by arm, arm×family; paired bootstrap CI", None))
        edges.append(("aggregates", exp, "paper", f"results/experiments/{exp}/paper/Table_3_5.md", "fills table shells", None))
    edges.append(("runs", "live", "study", "results/study.sqlite", "sampled decision points → human vs JEV", None))
    con.executemany("INSERT INTO lineage (from_kind, from_id, to_kind, to_id, relation, note) VALUES (?,?,?,?,?,?)", edges)
    con.commit()
    return len(edges)


def rebuild(path: Path = DB_PATH, with_raw: bool = True, exp_root: Path = RESULTS / "experiments", runs_root: Path = RESULTS / "runs") -> dict:
    t0 = time.time()
    if path.exists():
        path.unlink()
    con = connect(path)
    con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF")
    out = dict(datasets=ingest_datasets(con), public_stats=ingest_public_stats(con), experiments={}, live_runs=0, study_responses=0)
    for exp_dir in sorted(p for p in exp_root.glob("*") if p.is_dir()):
        n = ingest_experiment(con, exp_dir, with_raw=with_raw)
        if n:
            out["experiments"][exp_dir.name] = dict(runs=n, aggregates=compute_aggregates(con, json.loads((exp_dir / "manifest.json").read_text()).get("exp") or exp_dir.name))
    out["live_runs"] = ingest_live_runs(con, runs_root, with_raw=with_raw)
    if out["live_runs"]:
        out["experiments"]["live"] = dict(runs=out["live_runs"], aggregates=compute_aggregates(con, "live"))
    out["study_responses"] = ingest_study(con)
    out["lineage_edges"] = build_lineage(con)
    out["built_at"] = _now(); out["seconds"] = round(time.time() - t0, 1)
    for k in ("built_at", "seconds"):
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(out[k])))
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("summary", json.dumps(out, default=str)))
    con.commit(); con.close()
    out["path"] = str(path); out["size_mb"] = round(path.stat().st_size / 1e6, 1)
    return out


def append_live_run(d: Path, path: Path = DB_PATH) -> str | None:
    """Called when a live session finishes: load that run and refresh the 'live' aggregates (DB is created if absent)."""
    con = connect(path)
    try:
        rid = ingest_run_dir(con, d, "live", live=True)
        compute_aggregates(con, "live")
        build_lineage(con)
        con.commit()
        return rid
    finally:
        con.close()


# ----------------------------------------------------------------------------------------------- queries for the API
def _rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def overview(path: Path = DB_PATH) -> dict:
    if not path.exists():
        return dict(exists=False, path=str(path))
    con = connect(path)
    try:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in EXPORTABLE}
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        return dict(exists=True, path=str(path), size_mb=round(path.stat().st_size / 1e6, 1), built_at=meta.get("built_at"), build_seconds=meta.get("seconds"), counts=counts,
                    datasets=_rows(con, "SELECT * FROM datasets ORDER BY license_reviewed DESC, dataset_id"),
                    public_stats=_rows(con, "SELECT * FROM public_stats ORDER BY (feeds IS NULL), dataset_id, scope, metric"),
                    experiments=_rows(con, "SELECT e.*, (SELECT COUNT(*) FROM runs r WHERE r.exp=e.exp) AS runs_in_db FROM experiments e ORDER BY created"),
                    run_groups=_rows(con, "SELECT exp, provider, arm, COUNT(*) AS n, SUM(jev_calls) AS jev_calls, ROUND(SUM(jev_cost_usd),4) AS jev_cost_usd FROM runs GROUP BY exp, provider, arm ORDER BY exp, arm"),
                    lineage=_rows(con, "SELECT * FROM lineage ORDER BY edge_id"),
                    stat_feeds=_rows(con, "SELECT p.stat_id, p.dataset_id, p.scope, p.metric, p.value, p.feeds, COUNT(s.run_id) AS n_runs_using FROM public_stats p LEFT JOIN sim_inputs s ON s.source LIKE '%' || p.dataset_id || '%' AND s.key LIKE 'params.%' WHERE p.feeds IS NOT NULL GROUP BY p.stat_id"),
                    study=_rows(con, "SELECT participant, COUNT(*) AS n, AVG(human_correct) AS human_correct, AVG(jev_correct) AS jev_correct, AVG(rules_correct) AS rules_correct, AVG(response_ms) AS mean_ms FROM study_responses GROUP BY participant"))
    finally:
        con.close()


def aggregates(exp: str, grp: str = "arm", metrics: list[str] | None = None, path: Path = DB_PATH) -> dict:
    con = connect(path)
    try:
        metrics = metrics or ["composite_loss", "correct_action_rate", "harmful_false_continue", "false_stop_proposals", "unnecessary_stop_min", "unnecessary_review", "f1", "plan_attainment", "energy_kwh_per_good_part"]
        q = f"SELECT grp_key, metric, n, mean, sd, min, max FROM aggregates WHERE exp=? AND grp=? AND metric IN ({','.join('?' * len(metrics))}) ORDER BY grp_key, metric"
        rows = _rows(con, q, (exp, grp, *metrics))
        ci = _rows(con, "SELECT metric, contrast, n_pairs, mean_diff, ci_low, ci_high, excludes_zero FROM paired_ci WHERE exp=? ORDER BY metric, contrast", (exp,))
        return dict(exp=exp, grp=grp, metrics=metrics, rows=rows, paired_ci=ci)
    finally:
        con.close()


def runs(exp: str | None = None, limit: int = 500, path: Path = DB_PATH) -> list[dict]:
    con = connect(path)
    try:
        where, args = ("WHERE r.exp=?", (exp,)) if exp else ("", ())
        return _rows(con, f"""SELECT r.run_id, r.exp, r.family, r.fault_kind, r.seed, r.arm, r.provider, r.live, r.n_events, r.n_decisions, r.n_commands, r.jev_calls, r.jev_fallbacks, r.jev_resolved_model,
                                (SELECT value FROM run_metrics m WHERE m.run_id=r.run_id AND m.metric='composite_loss') AS composite_loss,
                                (SELECT value FROM run_metrics m WHERE m.run_id=r.run_id AND m.metric='correct_action_rate') AS correct_action_rate,
                                (SELECT value FROM run_metrics m WHERE m.run_id=r.run_id AND m.metric='harmful_false_continue') AS harmful_false_continue,
                                (SELECT value FROM run_metrics m WHERE m.run_id=r.run_id AND m.metric='unnecessary_stop_min') AS unnecessary_stop_min,
                                (SELECT value FROM run_metrics m WHERE m.run_id=r.run_id AND m.metric='plan_attainment') AS plan_attainment
                             FROM runs r {where} ORDER BY r.exp, r.family, r.seed, r.arm LIMIT ?""", (*args, limit))
    finally:
        con.close()


def run_detail(run_id: str, path: Path = DB_PATH) -> dict:
    con = connect(path)
    try:
        r = _rows(con, "SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not r:
            return {}
        return dict(run=r[0], metrics=_rows(con, "SELECT metric, value FROM run_metrics WHERE run_id=? ORDER BY metric", (run_id,)),
                    inputs=_rows(con, "SELECT grp, key, value, source FROM sim_inputs WHERE run_id=? ORDER BY rowid", (run_id,)),
                    counts=dict(events=con.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0], truth=con.execute("SELECT COUNT(*) FROM truth WHERE run_id=?", (run_id,)).fetchone()[0],
                                decisions=con.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (run_id,)).fetchone()[0], commands=con.execute("SELECT COUNT(*) FROM commands WHERE run_id=?", (run_id,)).fetchone()[0]),
                    signals=_rows(con, "SELECT signal, source_type, COUNT(*) AS n, MIN(sim_time_s) AS t0, MAX(sim_time_s) AS t1 FROM events WHERE run_id=? GROUP BY signal, source_type ORDER BY n DESC", (run_id,)),
                    decisions_by=_rows(con, "SELECT provider, recommendation, COUNT(*) AS n FROM decisions WHERE run_id=? GROUP BY provider, recommendation ORDER BY n DESC", (run_id,)))
    finally:
        con.close()


def export_csv(table: str, exp: str | None = None, path: Path = DB_PATH, limit: int = 200000) -> str:
    if table not in EXPORTABLE:
        raise KeyError(table)
    con = connect(path)
    try:
        has_exp = table in ("runs", "experiments", "aggregates", "paired_ci")
        has_run = table in ("run_metrics", "sim_inputs", "events", "truth", "decisions", "commands")
        if exp and has_exp:
            cur = con.execute(f"SELECT * FROM {table} WHERE exp=? LIMIT ?", (exp, limit))
        elif exp and has_run:
            cur = con.execute(f"SELECT * FROM {table} WHERE run_id IN (SELECT run_id FROM runs WHERE exp=?) LIMIT ?", (exp, limit))
        else:
            cur = con.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur.fetchall())
        return buf.getvalue()
    finally:
        con.close()
