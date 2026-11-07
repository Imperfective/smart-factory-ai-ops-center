"""Cumulative results store for live / batch runs of the operations center.

Every finished run (live session reaching the end of the shift, or a headless batch run) is appended to one SQLite DB
and the CSV exports are regenerated from it, so statistics always cover every run made so far:

    results/v3/live_runs/live_runs.sqlite   runs (one row per run), run_events (each random event + how the system
                                            handled it), run_units (per-part pipeline)
    results/v3/live_runs/runs.csv           one row per run (all metrics + provenance)
    results/v3/live_runs/run_events.csv     events with detection / first response (evaluation only)
    results/v3/live_runs/stats_by_group.csv mean / sd / 95 % CI per group (mode × decision provider × part judge × load)
    results/v3/live_runs/paired.csv         paper contrasts on identical courses: bootstrap CI, McNemar / bootstrap p,
                                            Holm-adjusted p, pairs needed for 80 % power vs pairs available

Mock and real TypeSafe Jev runs are kept apart by `decision_provider` / `unit_judge`; real-Jev runs in which any
decision fell back (budget cap, API error) are flagged `data_quality = jev_fallback` and left out of the statistics.
Nothing here is ever sent to the decision layer.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path("results/v3/live_runs")
DB = ROOT / "live_runs.sqlite"
_LOCK = threading.RLock()

PROV_COLS = ["code_version", "config_hash", "yolo_weights_sha", "jev_model", "data_quality"]
RUN_COLS = ["run_uid", "finished_at", "source", "batch_id", "course_seed", "scenario", "load", "arm", "decision_provider", "unit_judge", "camera_mode", "vision_source",
            "defect_image_ratio", "n_events", "event_kinds", "target", "good_end", "target_met", "par", "shortfall", "defects_shipped", "rejected",
            "unsafe_authorization_rate", "forbidden_proposed", "forbidden_allowed", "executed_unsafe", "unnecessary_stop_min", "action_delay_min",
            "mttr_main_s", "production_recovery_s", "detection_recall", "detection_precision", "false_alarms_per_h", "mttd_s", "escalations",
            "transport_errors", "order_completion_rate", "delivered_pallets", "line_kwh", "aux_kwh", "amr_charge_kwh", "amr_battery_deficit_kwh",
            "amr_charges", "energy_kwh", "factory_kwh_total", "kwh_per_good", "max_kw", "overloads", "cost_balanced", "cost_quality", "cost_energy",
            "decisions", "jev_calls", "jev_cached", "jev_errors", "jev_cost_usd", "fallbacks",
            "parts", "yolo_flagged", "final_accept", "final_reject", "final_reinspect", "automation_rate", "final_accuracy",
            "unit_defects_shipped", "good_scrapped", "hidden_defects_caught", "unit_judge_calls", "wall_s"] + PROV_COLS
TEXT = {"run_uid", "finished_at", "source", "batch_id", "scenario", "load", "arm", "decision_provider", "unit_judge", "camera_mode", "vision_source", "event_kinds",
        "code_version", "config_hash", "yolo_weights_sha", "jev_model", "data_quality"}
EVENT_COLS = ["run_uid", "fault_id", "kind", "at_s", "duration_s", "target", "params", "cause", "detected", "detect_delay_s", "first_action", "first_action_by",
              "first_action_delay_s", "arm", "decision_provider", "load", "course_seed"]
DERIVED = ["defects_per_1000_good", "good_to_target"]   # rates computed from stored columns (volume-independent)
STAT_METRICS = ["target_met", "par", "good_end", "good_to_target", "defects_per_1000_good", "shortfall", "defects_shipped", "unsafe_authorization_rate", "forbidden_allowed", "production_recovery_s",
                "unnecessary_stop_min", "transport_errors", "factory_kwh_total", "kwh_per_good", "amr_charge_kwh", "cost_balanced", "mttd_s", "detection_recall",
                "automation_rate", "final_accuracy", "unit_defects_shipped", "hidden_defects_caught"]
PAIRED_METRICS = ["target_met", "cost_balanced", "forbidden_allowed", "kwh_per_good", "defects_per_1000_good", "good_to_target", "defects_shipped",
                  "factory_kwh_total", "transport_errors", "production_recovery_s"]
GROUP = ["arm", "decision_provider", "unit_judge", "load"]
STATS_METHOD = "signflip-10000/cluster-boot-2000"     # part of the cache key: a changed method must never reuse old statistics


# ------------------------------------------------------------------------------------------------ provenance
_PROV: dict = {}


def _provenance_static() -> dict:
    if not _PROV:
        try:
            rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--", "sfaic2", "apps/api"], capture_output=True, text=True, timeout=5).stdout.strip()
            _PROV["code_version"] = (rev or "unknown") + ("+local" if dirty else "")
        except Exception:  # noqa: BLE001
            _PROV["code_version"] = "unknown"
        try:
            from .yolo_live import WEIGHTS
            _PROV["yolo_weights_sha"] = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            _PROV["yolo_weights_sha"] = None
    return dict(_PROV)


def _config_hash(f) -> str:
    return hashlib.sha256(json.dumps(f.model_dump(), sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------ schema
def _con() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    cols = ", ".join(f"{c} {'TEXT' if c in TEXT else 'REAL'}" for c in RUN_COLS)
    con.executescript(f"""
CREATE TABLE IF NOT EXISTS runs ({cols}, metrics_json TEXT, PRIMARY KEY (run_uid));
CREATE TABLE IF NOT EXISTS run_events (run_uid TEXT, fault_id TEXT, kind TEXT, at_s REAL, duration_s REAL, target TEXT, params TEXT);
CREATE TABLE IF NOT EXISTS run_units (run_uid TEXT, line_id TEXT, unit_seq INTEGER, yolo_verdict TEXT, disposition TEXT, final_disposition TEXT,
    defect_present_p REAL, judge_provider TEXT, truly_defective INTEGER);
CREATE INDEX IF NOT EXISTS ix_run_events_uid ON run_events(run_uid);
CREATE INDEX IF NOT EXISTS ix_run_units_uid ON run_units(run_uid);
""")
    for table, want in (("runs", RUN_COLS), ("run_events", EVENT_COLS)):      # migrate older DBs in place
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for c in want:
            if c not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {'TEXT' if c in TEXT or c in ('fault_id', 'kind', 'target', 'params', 'cause', 'first_action', 'first_action_by') else 'REAL'}")
    return con


# ------------------------------------------------------------------------------------------------ collect (runs in any process)
def unit_summary(ops, truth) -> tuple[dict, list[dict]]:
    """Per-part pipeline (YOLO stage 1 → judge stage 2 → final) joined with the evaluator's truth."""
    rows = ops.query("SELECT line_id, unit_seq, yolo_verdict, disposition, final_disposition, defect_present_p, judge_provider FROM unit_inspections")
    tr = {(t["line_id"], t["unit_seq"]): t["truly_defective"] for t in truth.query("SELECT line_id, unit_seq, truly_defective FROM truth_units")}
    for r in rows:
        r["truly_defective"] = tr.get((r["line_id"], r["unit_seq"]))
    n = len(rows)
    if not n:
        return dict(parts=0), []
    fin = [(r["final_disposition"] or r["disposition"]) for r in rows]
    known = [(f, r) for f, r in zip(fin, rows) if r["truly_defective"] is not None and f]
    out = dict(parts=n, yolo_flagged=sum(r["yolo_verdict"] == "DEFECT" for r in rows), final_accept=fin.count("ACCEPT"), final_reject=fin.count("REJECT"),
               final_reinspect=sum(r["disposition"] == "REINSPECT" for r in rows), unit_judge_calls=sum(1 for r in rows if r["judge_provider"]))
    out["automation_rate"] = round(1 - out["final_reinspect"] / n, 4)
    if known:
        out["final_accuracy"] = round(sum((f == "REJECT") == bool(r["truly_defective"]) for f, r in known) / len(known), 4)
        out["unit_defects_shipped"] = sum(1 for f, r in known if f == "ACCEPT" and r["truly_defective"])
        out["good_scrapped"] = sum(1 for f, r in known if f == "REJECT" and not r["truly_defective"])
        out["hidden_defects_caught"] = sum(1 for f, r in known if r["yolo_verdict"] != "DEFECT" and r["truly_defective"] and f != "ACCEPT")
    return out, rows


def event_outcomes(faults, ctrl) -> list[dict]:
    """How each random event was handled: detected (incident of the matching cause), detection delay, first automatic decision after it."""
    from .episode import TRUTH_CAUSE
    incidents = ctrl.all_incidents()
    out = []
    for f in faults:
        cause = TRUTH_CAUSE.get(f.kind)
        end = (f.at_s + f.duration_s) if f.duration_s else f.at_s + (f.truth_repair_s or 0) + 300
        hits = [i for i in incidents if i["cause"] == cause and f.at_s - 60 <= i["opened_at"] <= end + 600]
        first = next((d for d in ctrl.decisions if d["t"] >= f.at_s and not d["operator"]), None)
        out.append(dict(fault_id=f.fault_id, kind=f.kind, at_s=f.at_s, duration_s=f.duration_s, target=getattr(f, "target", None),
                        params=json.dumps(getattr(f, "params", {}) or {}, default=str), cause=cause, detected=int(bool(hits)),
                        detect_delay_s=(max(0.0, min(i["opened_at"] for i in hits) - f.at_s) if hits else None),
                        first_action=first["final"] if first else None, first_action_by=first.get("decided_by") if first else None,
                        first_action_delay_s=(first["t"] - f.at_s) if first else None))
    return out


def collect(metrics: dict, E: dict, *, source: str, course_seed: int, unit_judge: str, camera_mode: str, defect_image_ratio, wall_s: float,
            energy: dict | None = None, batch_id: str | None = None) -> dict:
    """Everything one finished run contributes, as plain data (picklable, so batch workers can return it)."""
    us, unit_rows = unit_summary(E["ops"], E["truth"])
    row = dict(metrics)
    row.update(us)
    faults = E["faults"]
    row.update(source=source, batch_id=batch_id, course_seed=course_seed, decision_provider=metrics.get("provider"), unit_judge=unit_judge or "off",
               camera_mode=camera_mode, defect_image_ratio=defect_image_ratio, n_events=len(faults), event_kinds="+".join(sorted(f.kind for f in faults)),
               wall_s=round(wall_s, 1), config_hash=_config_hash(E["f"]), **_provenance_static())
    models = [r["m"] for r in E["ops"].query("SELECT DISTINCT json_extract(raw_response_sanitized, '$.model') AS m FROM jev_responses") if r.get("m")] \
        if metrics.get("provider") == "TYPESAFE_JEV" else []
    row["jev_model"] = ",".join(sorted(set(models))) or ("mock" if metrics.get("provider") == "MOCK_JEV" else None)
    real = metrics.get("provider") == "TYPESAFE_JEV" or unit_judge == "typesafe"
    row["data_quality"] = "jev_fallback" if real and ((metrics.get("fallbacks") or 0) > 0 or (metrics.get("jev_errors") or 0) > 0) else "ok"
    row["aux_kwh"] = energy.get("aux_kwh") if energy else round((row.get("energy_kwh") or 0) - (row.get("line_kwh") or 0) - (row.get("amr_charge_kwh") or 0), 2)
    return dict(row=row, metrics={k: v for k, v in metrics.items() if not k.startswith("_")}, events=event_outcomes(faults, E["ctrl"]), units=unit_rows)


def insert(payload: dict, export_now: bool = True) -> str:
    row = payload["row"]
    uid = row.get("run_uid") or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    row["run_uid"], row["finished_at"] = uid, row.get("finished_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        con = _con()
        try:
            con.execute(f"INSERT INTO runs ({', '.join(RUN_COLS)}, metrics_json) VALUES ({', '.join('?' * (len(RUN_COLS) + 1))})",
                        [row.get(c) for c in RUN_COLS] + [json.dumps(payload["metrics"], default=str)])
            ctx = dict(run_uid=uid, arm=row.get("arm"), decision_provider=row.get("decision_provider"), load=row.get("load"), course_seed=row.get("course_seed"))
            con.executemany(f"INSERT INTO run_events ({', '.join(EVENT_COLS)}) VALUES ({', '.join('?' * len(EVENT_COLS))})",
                            [[{**e, **ctx}.get(c) for c in EVENT_COLS] for e in payload["events"]])
            con.executemany("INSERT INTO run_units VALUES (?,?,?,?,?,?,?,?,?)",
                            [(uid, r["line_id"], r["unit_seq"], r["yolo_verdict"], r["disposition"], r["final_disposition"], r["defect_present_p"], r["judge_provider"], r["truly_defective"])
                             for r in payload["units"]])
            con.commit()
        finally:
            con.close()
        if export_now:
            export()
    return uid


def record(metrics: dict, E: dict, **kw) -> str:
    return insert(collect(metrics, E, **kw))


# ------------------------------------------------------------------------------------------------ statistics
_T = {2: 12.71, 3: 4.30, 4: 3.18, 5: 2.78, 6: 2.57, 7: 2.45, 8: 2.36, 9: 2.31, 10: 2.26, 15: 2.14, 20: 2.09, 30: 2.05, 60: 2.00}


def _ci(xs: list[float]) -> tuple:
    n = len(xs)
    if n == 0:
        return None, None, None, None
    m = sum(xs) / n
    if n < 2:
        return m, None, None, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    h = next((v for k, v in sorted(_T.items()) if n <= k), 1.96) * sd / math.sqrt(n)
    return m, sd, m - h, m + h


def load_runs(include_flagged: bool = False) -> list[dict]:
    if not DB.exists():
        return []
    with _LOCK:
        con = _con()
        try:
            q = f"SELECT {', '.join(RUN_COLS)} FROM runs" + ("" if include_flagged else " WHERE COALESCE(data_quality, 'ok') = 'ok'") + " ORDER BY finished_at"
            out = [dict(r) for r in con.execute(q)]
            for r in out:
                g, t = r.get("good_end"), r.get("target")
                r["defects_per_1000_good"] = (1000.0 * r["defects_shipped"] / g) if g and r.get("defects_shipped") is not None else None
                r["good_to_target"] = (g / t) if g is not None and t else None
            return out
        finally:
            con.close()


_STATS_CACHE: dict = {}


def _key(runs: list[dict]) -> list:
    """Cache key: run count, newest run, and the metric schema and method (a change must never reuse old statistics)."""
    schema = hashlib.sha256(json.dumps([STAT_METRICS, PAIRED_METRICS, GROUP, STATS_METHOD]).encode()).hexdigest()[:12]
    return [len(runs), runs[-1]["run_uid"] if runs else None, schema]


def stats(runs: list[dict] | None = None) -> dict:
    """Statistics over the stored runs. Cached in memory and in stats.json (written by export), keyed by run count and
    newest run, so a page poll never recomputes the bootstrap unless new runs arrived."""
    runs = load_runs() if runs is None else runs
    key = _key(runs)
    if _STATS_CACHE.get("key") == key:
        return _STATS_CACHE["value"]
    f = ROOT / "stats.json"
    if f.exists():
        try:
            d = json.loads(f.read_text())
            if d.get("key") == key:
                _STATS_CACHE.update(key=key, value=d["value"])
                return d["value"]
        except (ValueError, KeyError):
            pass
    out = _stats(runs)
    _STATS_CACHE.update(key=key, value=out)
    return out


def _stats(runs: list[dict]) -> dict:
    groups: dict[tuple, list[dict]] = {}
    for r in runs:
        groups.setdefault(tuple(r.get(g) for g in GROUP), []).append(r)
    by_group = []
    for key, rs in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
        g = dict(zip(GROUP, key), n_runs=len(rs))
        for m in STAT_METRICS:
            xs = [float(r[m]) for r in rs if r.get(m) is not None]
            mean, sd, lo, hi = _ci(xs)
            g[m] = dict(n=len(xs), mean=mean, sd=sd, ci_low=lo, ci_high=hi)
        by_group.append(g)
    return dict(n_runs=len(runs), by_group=by_group, paired=paired_contrasts(runs), energy=energy_breakdown(runs), events=event_stats())


def energy_breakdown(runs: list[dict]) -> list[dict]:
    out = []
    for key in sorted({(r["arm"], r["decision_provider"]) for r in runs}, key=str):
        rs = [r for r in runs if (r["arm"], r["decision_provider"]) == key]
        avg = lambda c: (sum(float(r[c] or 0) for r in rs) / len(rs)) if rs else None  # noqa: E731
        out.append(dict(arm=key[0], decision_provider=key[1], n_runs=len(rs), line_kwh=avg("line_kwh"), aux_kwh=avg("aux_kwh"), amr_charge_kwh=avg("amr_charge_kwh"),
                        amr_battery_deficit_kwh=avg("amr_battery_deficit_kwh"), factory_kwh_total=avg("factory_kwh_total"), kwh_per_good=avg("kwh_per_good")))
    return out


def event_stats() -> list[dict]:
    """Per event type × mode × provider: how often it was detected, mean detection delay and first-response delay."""
    if not DB.exists():
        return []
    with _LOCK:
        con = _con()
        try:
            return [dict(r) for r in con.execute(
                "SELECT e.kind, e.arm, e.decision_provider, COUNT(*) AS n, AVG(e.detected) AS detected_rate, AVG(e.detect_delay_s) AS detect_delay_s, "
                "AVG(e.first_action_delay_s) AS first_action_delay_s FROM run_events e JOIN runs r USING (run_uid) "
                "WHERE e.arm IS NOT NULL AND COALESCE(r.data_quality,'ok')='ok' GROUP BY e.kind, e.arm, e.decision_provider ORDER BY e.kind, e.arm")]
        finally:
            con.close()


def _py(v):
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and math.isnan(v) else v


def pairs_needed(r: dict, alpha: float = 0.05, power: float = 0.8) -> int | None:
    """Pairs needed to detect the observed paired effect: McNemar (Connor 1987) for target met, paired normal approximation otherwise."""
    za, zb = 1.959964, 0.841621
    if r.get("discordant_a_only") is not None and r.get("n_pairs"):
        n = r["n_pairs"]
        p10, p01 = r["discordant_a_only"] / n, r["discordant_b_only"] / n
        pd_, d = p10 + p01, p10 - p01
        if d == 0 or pd_ - d * d <= 0:
            return None
        return math.ceil((za * math.sqrt(pd_) + zb * math.sqrt(pd_ - d * d)) ** 2 / (d * d))
    if r.get("cohens_dz"):
        return math.ceil(((za + zb) / abs(r["cohens_dz"])) ** 2) + 1
    return None


def paired_contrasts(runs: list[dict]) -> list[dict]:
    """Paper contrasts (stats.CONTRASTS) on identical random courses — same course seed, load, part judge and image share.
    Seed-cluster bootstrap CI; scenario-level sign-flip permutation p for every outcome (McNemar kept as a field for reference);
    Holm across contrasts per metric and provider."""
    import pandas as pd
    from .control import ARMS
    from .stats import CONTRASTS, _pairs, holm, paired, signflip_p
    jev_arm = {a for a, v in ARMS.items() if v.get("chooser") == "jev"}
    if not runs:
        return []
    df = pd.DataFrame(runs)
    df["scenario"] = df["scenario"].fillna("RND") + "|" + df["unit_judge"].astype(str) + "|" + df["defect_image_ratio"].astype(str)
    df["seed"] = df["course_seed"]
    df = df.sort_values("finished_at").drop_duplicates(["scenario", "load", "seed", "arm", "decision_provider"], keep="last")
    out = []
    provs = sorted(p for p in df["decision_provider"].dropna().unique() if p != "NONE") + ["NONE"]
    for prov in provs:
        d = pd.concat([df[df.decision_provider == prov], df[df.decision_provider == "NONE"]]).drop_duplicates(["scenario", "load", "seed", "arm"], keep="first")
        for m in PAIRED_METRICS:
            rows = []
            for a, b, label in CONTRASTS:
                if not ({a, b} <= set(d["arm"])) or ((prov == "NONE") == bool({a, b} & jev_arm)):
                    continue                        # Jev contrasts per Jev provider; rule-only contrasts once, under NONE
                r = paired(d, a, b, m)
                if r.get("n_pairs", 0) < 2:
                    continue
                pp = _pairs(d, a, b, m)
                pp["d"] = pp["a"] - pp["b"]
                if m == "forbidden_allowed":
                    sd = pp["d"].std(ddof=1)
                    r.update(mean_a=pp["a"].mean(), mean_b=pp["b"].mean(), mean_diff=pp["d"].mean(), cohens_dz=(pp["d"].mean() / sd) if sd else None)
                r["p"] = signflip_p(pp)             # scenario-level sign-flip permutation test (10,000), all outcomes
                rows.append(dict(decision_provider=prov, a=a, b=b, hypothesis=label, **{k: _py(v) for k, v in r.items()}))
            ps = [x["p"] for x in rows]
            idx = [i for i, p in enumerate(ps) if p is not None]
            adj = holm([ps[i] for i in idx]) if idx else []
            for i, h in zip(idx, adj):
                rows[i]["p_holm"] = _py(h)
            for x in rows:
                x["pairs_needed_80pct"] = pairs_needed(x)
                x["enough"] = None if x["pairs_needed_80pct"] is None else bool(x["n_pairs"] >= x["pairs_needed_80pct"])
            out += rows
    return out


def export() -> None:
    allr = load_runs(include_flagged=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "runs.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_COLS + DERIVED)
        w.writeheader()
        w.writerows(allr)
    con = _con()
    try:
        ev = [dict(r) for r in con.execute(f"SELECT {', '.join(EVENT_COLS)} FROM run_events ORDER BY run_uid, at_s")]
    finally:
        con.close()
    with open(ROOT / "run_events.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=EVENT_COLS)
        w.writeheader()
        w.writerows(ev)
    st = stats(load_runs())
    (ROOT / "stats.json").write_text(json.dumps(dict(key=_key(load_runs()), value=st), default=str))
    flat = []
    for g in st["by_group"]:
        for m in STAT_METRICS:
            flat.append(dict(**{k: g[k] for k in GROUP}, n_runs=g["n_runs"], metric=m, **g[m]))
    with open(ROOT / "stats_by_group.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=GROUP + ["n_runs", "metric", "n", "mean", "sd", "ci_low", "ci_high"])
        w.writeheader()
        w.writerows(flat)
    keys = ["decision_provider", "hypothesis", "a", "b", "metric", "n_pairs", "mean_a", "mean_b", "mean_diff", "ci_low", "ci_high", "excludes_zero", "cohens_dz",
            "discordant_a_only", "discordant_b_only", "p", "p_holm", "pairs_needed_80pct", "enough"]
    with open(ROOT / "paired.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(st["paired"])


# ------------------------------------------------------------------------------------------------ headless batch
_BATCH: dict = dict(running=False, done=0, total=0, error=None)
ARMS_ALL = ["R0", "R1", "R2", "R3", "R4", "R5"]


def batch_status() -> dict:
    return {k: v for k, v in _BATCH.items() if k != "seeds"}


def _job(args: dict, jev_budget=None, jev_cache=None) -> dict:
    from .live import LiveSession
    ls = LiveSession("RND", args["load"], args["seed"], args["arm"], args["provider"], camera_mode="replay", unit_judge=args["unit_judge"],
                     defect_image_ratio=args["ratio"], headless=True, source="batch", autosave=False, jev_budget=jev_budget, jev_cache=jev_cache)
    ls.control("run_to_end")
    a = ls.args
    return collect(ls.result, ls.E, source="batch", course_seed=a["seed"], unit_judge=a["unit_judge"], camera_mode=a["camera_mode"],
                   defect_image_ratio=a["defect_image_ratio"], wall_s=time.time() - ls._wall0, energy=ls._energy(), batch_id=args["batch_id"])


def run_batch(n: int, arms: list[str] | None = None, loads: list[str] | None = None, provider: str = "mock", unit_judge: str = "mock",
              defect_image_ratio: float | None = 0.1, seed0: int | None = None, workers: int = 6, budget_usd: float = 1.0, wait: bool = False) -> dict:
    """n random courses × loads × modes on identical course numbers (paired design). provider 'mock' runs in parallel
    processes (no API cost); 'typesafe' calls the real Jev with one shared call budget/cache (threads) and stops at budget_usd."""
    import random
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
    if _BATCH["running"]:
        raise RuntimeError("batch already running")
    arms, loads = arms or ARMS_ALL, loads or ["medium", "high"]
    rng = random.Random(seed0)
    seeds = rng.sample(range(100000), n)
    bid = f"B{time.strftime('%Y%m%d-%H%M%S')}-{provider}"
    jobs = [dict(load=l, seed=s, arm=a, provider=provider, unit_judge=unit_judge, ratio=defect_image_ratio, batch_id=bid) for s in seeds for l in loads for a in arms]
    _BATCH.update(running=True, done=0, total=len(jobs), error=None, batch_id=bid, provider=provider, arms=arms, loads=loads, n=n, flagged=0, cost_usd=0.0)

    def work():
        try:
            if provider == "mock":
                ex, submit = ProcessPoolExecutor(workers), None
                futs = [ex.submit(_job, j) for j in jobs]
            else:
                from sfaic2.adapters.typesafe_jev import JevBudget
                budget, cache = JevBudget(10 ** 6, budget_usd), {}
                ex = ThreadPoolExecutor(min(workers, 4))
                futs = [ex.submit(_job, j, budget, cache) for j in jobs]
            for i, fu in enumerate(as_completed(futs)):
                p = fu.result()
                insert(p, export_now=False)
                _BATCH["done"] += 1
                _BATCH["flagged"] += int(p["row"]["data_quality"] != "ok")
                _BATCH["cost_usd"] = round(_BATCH["cost_usd"] + float(p["row"].get("jev_cost_usd") or 0), 6)
                if i % 100 == 99:
                    export()
            ex.shutdown()
            export()
        except Exception as exc:  # noqa: BLE001
            _BATCH["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _BATCH["running"] = False
    if wait:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()
    return batch_status()


def report(out: Path = Path("docs/v3/RESULTS_random_courses.md")) -> str:
    """Markdown summary generated only from the stored runs (no hand-typed numbers)."""
    runs = load_runs()
    st = _stats(runs)
    allr = load_runs(include_flagged=True)
    real = [r for r in runs if r["decision_provider"] == "TYPESAFE_JEV" and r["source"] == "batch"]
    seeds = {(r["course_seed"], r["load"]) for r in real}
    cost = sum(float(r["jev_cost_usd"] or 0) for r in real)
    calls = sum(int(r["jev_calls"] or 0) for r in real)
    models = sorted({m for r in real for m in (r["jev_model"] or "").split(",") if m})
    f = lambda v, d=3: "—" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))  # noqa: E731
    L = ["# Random-course results (generated from results/v3/live_runs — do not edit by hand)", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M')}. Stored runs: {len(allr)} (excluded for data quality: {len(allr) - len(runs)}).",
         f"Real TypeSafe Jev batch runs: {len(real)} on {len(seeds)} course×load pairs, {calls} Jev calls, ${cost:.3f}, models {', '.join(models) or '—'}; "
         "per-part judge in batches is the mock judge (the real per-part study is `results/v3/real_jev/unit_study`).", "",
         "Design: each random course (1–4 random faults / defect surges / network / AMR / power events) is run by every mode on the "
         "same course number at medium (85 %) and high (95 %) target load. Rule modes (R0, R1) and the planner reference (R5) "
         "never call Jev; they are paired with the real-Jev modes (R2–R4) on the same courses.", "",
         "## Paired contrasts — real TypeSafe Jev", "",
         "| contrast | metric | pairs | first | second | diff | 95 % CI | p (Holm) | pairs needed (80 %) |", "|---|---|---|---|---|---|---|---|---|"]
    for p in st["paired"]:
        if p["decision_provider"] != "TYPESAFE_JEV":
            continue
        L.append(f"| {p['a']}−{p['b']} ({p['hypothesis']}) | {p['metric']} | {p['n_pairs']} | {f(p.get('mean_a'))} | {f(p.get('mean_b'))} | {f(p.get('mean_diff'))} | "
                 f"[{f(p.get('ci_low'))}, {f(p.get('ci_high'))}] | {f(p.get('p_holm'))} | {f(p.get('pairs_needed_80pct'))}{' ✓' if p.get('enough') else ''} |")
    L += ["", "## Rule-only contrasts", "", "| contrast | metric | pairs | diff | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|"]
    for p in st["paired"]:
        if p["decision_provider"] == "NONE":
            L.append(f"| {p['a']}−{p['b']} | {p['metric']} | {p['n_pairs']} | {f(p.get('mean_diff'))} | [{f(p.get('ci_low'))}, {f(p.get('ci_high'))}] | {f(p.get('p_holm'))} |")
    L += ["", "## Group means (real Jev groups and their rule/planner partners)", "",
          "| mode | provider | load | n | target met | output / target | unsafe allowed | shipped defects per 1000 good | loss (balanced) | factory kWh | kWh/good | AMR charging kWh |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in st["by_group"]:
        if g["decision_provider"] == "MOCK_JEV" or g["unit_judge"] != "mock":
            continue
        m = lambda k, d=3: f(g[k]["mean"], d)  # noqa: E731
        L.append(f"| {g['arm']} | {g['decision_provider']} | {g['load']} | {g['n_runs']} | {m('target_met')} | {m('good_to_target')} | {m('forbidden_allowed', 2)} | {m('defects_per_1000_good', 2)} | "
                 f"{m('cost_balanced', 1)} | {m('factory_kwh_total', 1)} | {m('kwh_per_good', 4)} | {m('amr_charge_kwh')} |")
    L += ["", "Reading note: every mode produces more than the plan once it is on track (output / target above 1); none of the decision "
          "options rewards stopping the backup line after the target is secured. Absolute factory kWh and absolute defects shipped therefore "
          "grow with output volume; compare modes on kWh per good part and shipped defects per 1000 good parts (volume-independent).", "",
          "Mock-Jev groups (software check, not evidence about Jev) are in `stats_by_group.csv` / `paired.csv` under MOCK_JEV.", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    return str(out)


def main():
    """python -m sfaic2.v3.live_results --n 30 --provider typesafe --budget 1.0   (real Jev needs JEV_ENABLED=true + key in .env)"""
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(".env"), override=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--provider", default="mock", choices=["mock", "typesafe"])
    ap.add_argument("--arms", default=",".join(ARMS_ALL))
    ap.add_argument("--loads", default="medium,high")
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed0", type=int, default=None)
    ap.add_argument("--report", action="store_true", help="only regenerate CSVs and docs/v3/RESULTS_random_courses.md")
    a = ap.parse_args()
    if a.report:
        export()
        print(report())
        return
    if a.provider == "typesafe":
        from .jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise SystemExit(why)
    st = run_batch(a.n, a.arms.split(","), a.loads.split(","), a.provider, "mock", 0.1, seed0=a.seed0, workers=a.workers, budget_usd=a.budget, wait=True)
    print(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
