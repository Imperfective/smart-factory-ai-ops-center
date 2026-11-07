"""Human-vs-JEV paired judgment study.

Design (docs/13 controls): after a run, decision points are *sampled* (stratified on evidence flags only — never on the
hidden truth), and each sampled item carries exactly the structured state that JEV saw. A participant answers the same
four questions as JEV (likely cause · review needed · risk 0–3 · stop warranted) at their own pace, blinded to the
model answers and to the truth. Responses, JEV/rules answers and hidden truth are logged to SQLite so that paired
statistics (correct-action rate, cause accuracy, false stops, latency) and per-participant distributions can be
exported. Nobody has to click during the simulation; the queue is reviewed afterwards.
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from pathlib import Path

from ..contracts.enums import Recommendation

DB_PATH = Path("results/study.sqlite")
CAUSES = ["physical_process_issue", "sensor_issue", "communications_issue", "electrical_loading", "process_routing_issue", "quality_issue", "none", "unknown"]
ACTIONS = [r.value for r in Recommendation]
TRUTH_TO_CAUSE = {"PHYSICAL_STATE_ANOMALY": "physical_process_issue", "SENSOR_FALSE_READING": "sensor_issue", "COMMUNICATION_DELAY": "communications_issue",
                  "ELECTRICAL_QUALITY": "electrical_loading", "PROCESS_POLICY_VIOLATION": "process_routing_issue", "PERCEPTION_ERROR": "sensor_issue", "NONE": "none"}


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS study_sessions (session_id TEXT PRIMARY KEY, run_id TEXT, participant TEXT, created REAL, n_items INTEGER, seed INTEGER, arm TEXT, provider TEXT, scenario TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS study_items (item_id TEXT PRIMARY KEY, session_id TEXT, position INTEGER, asset_id TEXT, sim_time_s REAL, stratum TEXT,
                   state_json TEXT, jev_json TEXT, rules_json TEXT, truth_json TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS study_responses (response_id TEXT PRIMARY KEY, item_id TEXT, session_id TEXT, participant TEXT, created REAL,
                   likely_cause TEXT, review_needed INTEGER, risk INTEGER, stop_warranted INTEGER, action TEXT, response_ms REAL, confidence INTEGER)""")
    return con


def action_from_answers(cause: str, review: bool, stop: bool) -> str:
    """Same deterministic mapping the JEV adapter uses, so human and JEV answers become comparable actions."""
    if stop and cause == "physical_process_issue":
        return "REQUEST_SIMULATED_STOP"
    if cause in ("sensor_issue", "communications_issue"):
        return "REQUEST_RECHECK"
    if review or cause in ("unknown", "physical_process_issue", "process_routing_issue", "electrical_loading", "quality_issue"):
        return "REQUEST_OPERATOR_REVIEW"
    return "CONTINUE_MONITORING"


def sample_items(decisions, truth, n: int, seed: int) -> list[dict]:
    """Stratified by *evidence* (non-nominal evidence vs nominal), never by truth. Returns blinded items + hidden keys."""
    rng = random.Random(seed)
    cands = [d for d in decisions if d.rationale.get("components") and d.rationale.get("bundle")]
    flagged = [d for d in cands if d.rationale["bundle"]["conflicts"] or d.rationale["bundle"]["stale"] or d.rationale["bundle"]["missing"]
               or any(c["recommendation"] != "CONTINUE_MONITORING" for c in d.rationale["components"].values())]
    nominal = [d for d in cands if d not in flagged]
    k1 = min(len(flagged), n // 2 + n % 2)
    k2 = min(len(nominal), n - k1)
    pick = rng.sample(flagged, k1) + rng.sample(nominal, k2)
    rng.shuffle(pick)
    items = []
    for i, d in enumerate(pick):
        tr = min((x for x in truth if x.machine_id == d.asset_id), key=lambda x: abs(x.sim_time_s - d.sim_time_s), default=None)
        comp = d.rationale["components"]
        jev = next((v for k, v in comp.items() if k in ("TYPESAFE_JEV", "MOCK_JEV")), None)
        jev_cause = (jev or {}).get("causes", [None])[0] if jev else None
        items.append(dict(item_id=f"it_{uuid.uuid4().hex[:10]}", position=i, asset_id=d.asset_id, sim_time_s=d.sim_time_s,
                          stratum="flagged" if d in flagged else "nominal",
                          state=d.rationale.get("state_for_study") or dict(bundle=d.rationale["bundle"], trace=d.rationale.get("trace", {}).get("signals")),
                          jev=dict(provider=next((k for k in comp if k in ("TYPESAFE_JEV", "MOCK_JEV")), None), cause=jev_cause, action=(jev or {}).get("recommendation"),
                                   raw=(jev or {}).get("rationale")) if jev else None,
                          rules=dict(action=comp.get("RULES", {}).get("recommendation"), causes=comp.get("RULES", {}).get("causes")),
                          truth=dict(classes=tr.truth_classes, expected_action=tr.expected_safe_action, harmful_stop_warranted=tr.harmful_stop_warranted,
                                     cause=TRUTH_TO_CAUSE.get(tr.truth_classes[0], "unknown")) if tr else None))
    return items


def create_session(run_id: str, participant: str, items: list[dict], seed: int, arm: str, provider: str, scenario: str) -> dict:
    sid = f"ss_{uuid.uuid4().hex[:8]}"
    with _db() as con:
        con.execute("INSERT INTO study_sessions VALUES (?,?,?,?,?,?,?,?,?)", (sid, run_id, participant, time.time(), len(items), seed, arm, provider, scenario))
        for it in items:
            con.execute("INSERT INTO study_items VALUES (?,?,?,?,?,?,?,?,?,?)", (it["item_id"], sid, it["position"], it["asset_id"], it["sim_time_s"], it["stratum"],
                        json.dumps(it["state"], default=str), json.dumps(it["jev"], default=str), json.dumps(it["rules"], default=str), json.dumps(it["truth"], default=str)))
    return dict(session_id=sid, run_id=run_id, participant=participant, n_items=len(items))


def next_item(session_id: str) -> dict | None:
    """Blinded item: evidence only. Model answers and truth are withheld until the response is stored."""
    with _db() as con:
        row = con.execute("""SELECT i.item_id, i.position, i.asset_id, i.sim_time_s, i.stratum, i.state_json, s.n_items FROM study_items i JOIN study_sessions s ON s.session_id=i.session_id
                             WHERE i.session_id=? AND i.item_id NOT IN (SELECT item_id FROM study_responses WHERE session_id=?) ORDER BY i.position LIMIT 1""", (session_id, session_id)).fetchone()
        done = con.execute("SELECT COUNT(*) FROM study_responses WHERE session_id=?", (session_id,)).fetchone()[0]
    if not row:
        return None
    return dict(item_id=row[0], position=row[1], asset_id=row[2], sim_time_s=row[3], stratum=row[4], state=json.loads(row[5]), n_items=row[6], answered=done)


def answer(session_id: str, item_id: str, participant: str, likely_cause: str, review_needed: bool, risk: int, stop_warranted: bool, response_ms: float, confidence: int | None) -> dict:
    if likely_cause not in CAUSES:
        raise ValueError(f"cause must be one of {CAUSES}")
    act = action_from_answers(likely_cause, review_needed, stop_warranted)
    rid = f"rs_{uuid.uuid4().hex[:10]}"
    with _db() as con:
        con.execute("INSERT INTO study_responses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (rid, item_id, session_id, participant, time.time(), likely_cause, int(review_needed), int(risk), int(stop_warranted), act, response_ms, confidence))
        row = con.execute("SELECT jev_json, rules_json FROM study_items WHERE item_id=?", (item_id,)).fetchone()
    return dict(response_id=rid, action=act, jev=json.loads(row[0]) if row else None, rules=json.loads(row[1]) if row else None)


def _score(action: str | None, cause: str | None, truth: dict | None) -> dict:
    if not truth or action is None:
        return dict(correct_action=None, correct_cause=None, false_stop=None, harmful_continue=None)
    anomalous = truth["classes"] != ["NONE"]
    return dict(correct_action=int(action == truth["expected_action"]) if anomalous else int(action == "CONTINUE_MONITORING"),
                correct_cause=int(cause == truth["cause"]) if cause is not None else None,
                false_stop=int(action == "REQUEST_SIMULATED_STOP" and not truth["harmful_stop_warranted"]),
                harmful_continue=int(action == "CONTINUE_MONITORING" and truth["harmful_stop_warranted"]))


def stats(participant: str | None = None, n_boot: int = 2000, seed: int = 2026) -> dict:
    """Paired human vs JEV vs rules on identical items; per-participant and per-truth-class breakdown; bootstrap CIs."""
    import numpy as np
    with _db() as con:
        q = """SELECT r.participant, r.likely_cause, r.action, r.response_ms, r.risk, r.review_needed, r.stop_warranted, i.jev_json, i.rules_json, i.truth_json, i.stratum, s.provider, s.scenario, i.item_id
               FROM study_responses r JOIN study_items i ON i.item_id=r.item_id JOIN study_sessions s ON s.session_id=r.session_id"""
        rows = con.execute(q + (" WHERE r.participant=?" if participant else ""), ((participant,) if participant else ())).fetchall()
    recs = []
    for p, hc, ha, ms, risk, rev, stop, jj, rj, tj, stratum, prov, scen, iid in rows:
        jev, rules, truth = json.loads(jj) if jj else None, json.loads(rj) if rj else None, json.loads(tj) if tj else None
        recs.append(dict(participant=p, item_id=iid, stratum=stratum, provider=prov, scenario=scen, truth_class=(truth or {}).get("classes", ["?"])[0],
                         human=_score(ha, hc, truth), jev=_score((jev or {}).get("action"), (jev or {}).get("cause"), truth) if jev else None,
                         rules=_score((rules or {}).get("action"), ((rules or {}).get("causes") or [None])[0], truth), human_ms=ms, human_action=ha, jev_action=(jev or {}).get("action"), truth_expected=(truth or {}).get("expected_action")))
    def agg(key):
        vals = {m: [r[key][m] for r in recs if r.get(key) and r[key][m] is not None] for m in ("correct_action", "correct_cause", "false_stop", "harmful_continue")}
        return {m: (float(np.mean(v)) if v else None) for m, v in vals.items()} | {"n": len([r for r in recs if r.get(key)])}
    out = dict(n_responses=len(recs), participants=sorted({r["participant"] for r in recs}), human=agg("human"), jev=agg("jev"), rules=agg("rules"),
               human_response_ms=dict(mean=float(np.mean([r["human_ms"] for r in recs])) if recs else None, median=float(np.median([r["human_ms"] for r in recs])) if recs else None))
    # paired bootstrap human − jev on correct_action
    pairs = [(r["human"]["correct_action"], r["jev"]["correct_action"]) for r in recs if r.get("jev") and r["human"]["correct_action"] is not None and r["jev"]["correct_action"] is not None]
    if pairs:
        d = np.array([h - j for h, j in pairs], float); rng = np.random.default_rng(seed)
        boots = np.array([rng.choice(d, len(d), replace=True).mean() for _ in range(n_boot)])
        out["paired_human_minus_jev_correct_action"] = dict(n=len(d), mean=float(d.mean()), ci_low=float(np.quantile(boots, .025)), ci_high=float(np.quantile(boots, .975)))
    by_class = {}
    for r in recs:
        c = by_class.setdefault(r["truth_class"], dict(n=0, human=0, jev=0, rules=0, jev_n=0))
        c["n"] += 1; c["human"] += r["human"]["correct_action"] or 0; c["rules"] += r["rules"]["correct_action"] or 0
        if r.get("jev") and r["jev"]["correct_action"] is not None:
            c["jev"] += r["jev"]["correct_action"]; c["jev_n"] += 1
    out["by_truth_class"] = {k: dict(n=v["n"], human=v["human"] / v["n"], rules=v["rules"] / v["n"], jev=(v["jev"] / v["jev_n"]) if v["jev_n"] else None) for k, v in by_class.items()}
    by_p = {}
    for r in recs:
        c = by_p.setdefault(r["participant"], dict(n=0, correct=0, ms=[]))
        c["n"] += 1; c["correct"] += r["human"]["correct_action"] or 0; c["ms"].append(r["human_ms"])
    out["by_participant"] = {k: dict(n=v["n"], correct_action=v["correct"] / v["n"], median_ms=float(np.median(v["ms"]))) for k, v in by_p.items()}
    out["agreement_human_jev"] = (float(np.mean([r["human_action"] == r["jev_action"] for r in recs if r.get("jev")])) if any(r.get("jev") for r in recs) else None)
    return out


def export_rows() -> list[dict]:
    with _db() as con:
        rows = con.execute("""SELECT r.response_id, r.participant, r.created, s.run_id, s.scenario, s.provider, i.asset_id, i.sim_time_s, i.stratum, r.likely_cause, r.review_needed, r.risk, r.stop_warranted, r.action, r.response_ms, r.confidence, i.jev_json, i.rules_json, i.truth_json
                              FROM study_responses r JOIN study_items i ON i.item_id=r.item_id JOIN study_sessions s ON s.session_id=r.session_id ORDER BY r.created""").fetchall()
    out = []
    for row in rows:
        jev, rules, truth = (json.loads(x) if x else None for x in row[16:19])
        out.append(dict(response_id=row[0], participant=row[1], created=row[2], run_id=row[3], scenario=row[4], provider=row[5], asset_id=row[6], sim_time_s=row[7], stratum=row[8],
                        human_cause=row[9], human_review=row[10], human_risk=row[11], human_stop=row[12], human_action=row[13], human_ms=row[14], human_confidence=row[15],
                        jev_action=(jev or {}).get("action"), jev_cause=(jev or {}).get("cause"), rules_action=(rules or {}).get("action"),
                        truth_class=(truth or {}).get("classes", [None])[0], truth_expected_action=(truth or {}).get("expected_action"), truth_harmful_stop=(truth or {}).get("harmful_stop_warranted")))
    return out
