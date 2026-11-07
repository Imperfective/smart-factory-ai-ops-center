"""Decision log: one structured record per supervisory decision, assembled only from what was stored in the
operational DB at decision time (snapshot, Jev request/response, policy decision, commands) plus, for review, the
production observed afterwards. Works on a live session DB and on archived raw episode DBs (results/v3/*/*/raw)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from .config import ACTION_TEXT, FAULT_CATALOGUE, Action


def _j(s):
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return s


def build(query: Callable[[str, tuple], list[dict]], limit: int = 500, good_at: Callable[[float], float] | None = None) -> list[dict]:
    pol = query("SELECT * FROM policy_decisions ORDER BY simulation_time, rowid LIMIT ?", (limit,))
    snaps = {r["decision_id"]: r for r in query("SELECT decision_id, input_features_json, available_actions, constraints, incident_id FROM decision_snapshots", ())}
    reqs = {r["decision_id"]: r for r in query("SELECT * FROM jev_requests", ())}
    resps = {r["request_id"]: r for r in query("SELECT * FROM jev_responses", ())}
    cmds: dict[str, list] = {}
    for c in query("SELECT * FROM command_execution ORDER BY simulation_time", ()):
        cmds.setdefault(c["decision_id"], []).append(dict(command=c["command"], target=c["target"], accepted=bool(c["confirmed"]), before=c["before_state"], after=c["after_state"],
                                                          t=c["simulation_time"], channel=c["channel"]))
    out = []
    for p in pol:
        did = p["decision_id"]
        s = snaps.get(did)
        ft = _j(s["input_features_json"]) if s else None
        rec = dict(decision_id=did, t=p["simulation_time"], decided_by=p["decided_by"], proposed=p["proposed_action"], gate=p["safety_gate_result"], gate_reason=p["blocked_reason"],
                   final=p["final_action"], reviewed_by=p["reviewed_by"], cause_label=p["cause_label"], commands=cmds.get(did, []))
        if ft:
            o = ft["obs"]
            code = o["main"].get("fault_code") or o["main"].get("last_fault_code")
            rec["situation"] = dict(
                target=ft["target"], good_so_far=round(ft["good_so_far"], 1), gap=round(ft["gap"], 1), remaining_s=ft["rem_s"], required_rate_per_h=round(ft["required_rate_per_h"], 1),
                main_state=o["main"]["plc_state"], main_fault_code=code if o["main"]["plc_state"] in ("FAULT", "MAINT", "READY") else None,
                main_fault_text=FAULT_CATALOGUE[code].description if code in FAULT_CATALOGUE and o["main"]["plc_state"] in ("FAULT", "MAINT", "READY") else None,
                main_diag_ok=bool(o["main"].get("diag_ok")), main_age_s=o["main"].get("age_s"), sub_state=o["sub"]["plc_state"], eta_main_s=ft["eta"]["main"],
                ct_main_est=ft["ct_main_est"], ct_main_nominal=ft["ct_main_nominal"], yield_main_est=ft["yield_main_est"], limit_kw=ft["limit_kw"], limit_source=ft["limit_source"],
                amr_available=ft["amr_available"], reserve_amr_available=ft["reserve_amr_available"], pallets_waiting=ft["pallets_waiting"], evidence=ft.get("evidence"),
                network=ft.get("network"), incidents=[dict(cause=i["cause"], line=i["line"], signal=i["signal"], detector=i["detector"]) for i in ft["incidents"]],
                cpsat_bound=ft.get("max_achievable_good"))
            allowed = ft["feasible"]
            rec["options"] = [dict(action=a, feasible=a in allowed, excluded_reason=(ft["infeasible"] or {}).get(a), meaning=ACTION_TEXT[Action(a)],
                                   **{k: ft["forecast"][a][k] for k in ("projected_good_end", "projected_shortfall", "projected_defects_shipped", "energy_kwh", "peak_kw", "startup_s")})
                              for a in ft["forecast"]]
        rq = reqs.get(did)
        if rq:
            req = _j(rq["sanitized_request_json"]) or {}
            rs = resps.get(rq["request_id"]) or {}
            raw = _j(rs.get("raw_response_sanitized")) or {}
            probs = _j(rs.get("per_choice_probabilities")) or {}
            for op in rec.get("options", []):
                op["jev_probability"] = probs.get(op["action"])
                op["criteria_sent"] = ((req.get("questions") or {}).get("preferred_action") or {}).get("criteria", {}).get(op["action"])
            ans = raw.get("answers") or {}
            rec["jev"] = dict(provider=rs.get("provider") or rq["provider"], requested_model=rq["model_version"], resolved_model=raw.get("model"), question_version=rq["question_version"],
                              request_hash=rq["anonymized_payload_hash"], latency_ms=rs.get("latency_ms"), cached=bool(rs.get("cached")), error=rs.get("error_code"),
                              tokens=_j(rs.get("token_usage")), cost_usd=rs.get("cost_estimate"), selected=rs.get("selected_action"),
                              selected_confidence=(ans.get("preferred_action") or {}).get("confidence"), action_probabilities=probs,
                              cause=rs.get("cause_class"), cause_probabilities=_j(rs.get("cause_probabilities")), needs_operator_p=rs.get("needs_operator_p"),
                              instructions={k: v.get("instructions") for k, v in (req.get("questions") or {}).items()}, state_sent=req.get("state"))
        if good_at is not None and ft:
            g0, g1 = good_at(p["simulation_time"]), good_at(p["simulation_time"] + 1800)
            rec["outcome_30min"] = dict(good_delta=None if g0 is None or g1 is None else round(g1 - g0, 1))
        out.append(rec)
    return out


def from_sqlite(path: Path, limit: int = 500) -> list[dict]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    def q(sql, args=()):
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    try:
        return build(q, limit)
    finally:
        con.close()


def archive_index(root: Path = Path("results/v3")) -> list[dict]:
    out = []
    for group in ("real_jev", "mock"):
        for p in sorted((root / group).glob("*/raw/*.ops.sqlite")):
            out.append(dict(group=group, exp=p.parent.parent.name, episode=p.name.replace(".ops.sqlite", ""), size_kb=round(p.stat().st_size / 1024)))
    return out
