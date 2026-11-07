"""Comparison arms, choosers (rule / planner / Jev), safety gates, intent-based virtual execution and the simulated
operator. The controller sees the plant only through OpsDB snapshots and the virtual PLC command surface; the
safety oracle (`plant.assess`) is consulted solely to *score* proposals for the unsafe-authorisation metric and never
changes a decision.

R0 rule detection + fixed bypass rules        | basic gate | count-triggered transport
R1 ML/forecast detection + the same rules     | basic gate | count-triggered transport
R2 R1 + TypeSafe Jev choice (else identical)  | basic gate | count-triggered transport
R3 R2 + evidence validation + full safety gate| full gate  | validated transport (idempotent)
R4 R3 + live electrical headroom + network context (Jev state + adaptive MQTT timeouts)
R5 deterministic planner (CP-SAT achievable bound + exhaustive forecast argmax) on R4 information — reference baseline
Ablations (Jev information removed from R4's state): R4-noplan, R4-noct, R4-nonet, R4-noelec, R4-novisionqr; R4-basictransport (H4).
"""
from __future__ import annotations

import json

from .config import MOTION_ACTIONS, Action
from .db import OpsDB
from .jev import JevDecisionAdapter
from .snapshot import DOWN, PRODUCING, SnapshotBuilder

BASE = dict(detector="ml", chooser="rule", validated=False, gate="basic", context=False, transport="BASIC", adaptive=False, drop=frozenset())
ARMS: dict[str, dict] = {
    "R0": dict(BASE, detector="rule"),
    "R1": dict(BASE),
    "R2": dict(BASE, chooser="jev"),
    "R3": dict(BASE, chooser="jev", validated=True, gate="full", transport="VALIDATED"),
    "R4": dict(BASE, chooser="jev", validated=True, gate="full", context=True, transport="VALIDATED", adaptive=True),
    "R5": dict(BASE, chooser="planner", validated=True, gate="full", context=True, transport="VALIDATED", adaptive=True),
}
for _n, _d in (("noplan", "plan"), ("noct", "ct"), ("nonet", "network"), ("noelec", "electrical"), ("novisionqr", "vision_qr")):
    ARMS[f"R4-{_n}"] = dict(ARMS["R4"], drop=frozenset({_d}))
ARMS["R4-basictransport"] = dict(ARMS["R4"], transport="BASIC", adaptive=False)
ARM_LABEL = {"R0": "Rule + schedule", "R1": "ML detection + deterministic policy", "R2": "R1 + TypeSafe Jev", "R3": "R2 + evidence validation + safety gate",
             "R4": "R3 + electrical/network context", "R5": "Planner (CP-SAT bound + exhaustive forecast) — reference"}


def rule_policy(ft: dict, allowed: list[str]) -> tuple[str, str]:
    """Fixed thresholds and pre-defined bypass rules (no Jev). Returns (action, cause label)."""
    o = ft["obs"]
    causes = {i["cause"] for i in ft["incidents"]}
    main_st, sub_st = o["main"]["plc_state"], o["sub"]["plc_state"]
    cand: list[tuple[str, str]] = []
    if main_st in DOWN and sub_st in DOWN:
        cand.append(("ESCALATE_HUMAN", "EQUIPMENT"))
    if main_st == "READY" and sub_st in PRODUCING:
        cand.append(("RECOVER_MAIN", "EQUIPMENT"))
    if main_st in DOWN or (main_st == "READY"):
        cand.append(("RECOVER_MAIN" if ft["eta"]["main"] <= 1800 else "SWITCH_TO_SUB", "EQUIPMENT"))
        cand.append(("RECOVER_MAIN", "EQUIPMENT"))
    if "QUALITY" in causes:
        cand.append(("SAFE_STOP_REQUEST" if main_st in PRODUCING else "RECOVER_MAIN", "QUALITY"))
    if "PROCESS_DRIFT" in causes:
        cand.append(("RECOVER_MAIN", "PROCESS_DRIFT"))
    if "TRANSPORT" in causes:
        cand.append(("REQUEST_TRANSPORT", "TRANSPORT"))
    if "PLAN" in causes:
        cand.append(("RUN_PARALLEL", "PLAN"))
        cand.append(("REPLAN", "PLAN"))
    if main_st in PRODUCING:
        cand.append(("CONTINUE_MAIN", next(iter(sorted(causes)), "NONE")))
    if main_st in ("IDLE", "OFF"):
        cand.append(("RECOVER_MAIN", "NONE"))
    for a, c in cand:
        if a in allowed:
            return a, c
    return "ESCALATE_HUMAN", next(iter(sorted(causes)), "UNKNOWN")


def planner_policy(ft: dict, allowed: list[str]) -> tuple[str, str]:
    """Reference planner: exhaustive argmax of the program forecast over the allowed actions (exact for this action
    set); the CP-SAT bound decides whether the plan is reachable at all."""
    tgt = ft["target"]

    def val(a):
        fc = ft["forecast"][a]
        v = min(fc["projected_good_end"], tgt) - 0.3 * fc["projected_defects_shipped"] - 0.005 * fc["energy_kwh"]
        return v - (8.0 if a == "ESCALATE_HUMAN" else 0.0) - (2.0 if a == "REPLAN" else 0.0)
    best = max(allowed, key=val)
    causes = sorted({i["cause"] for i in ft["incidents"]})
    return best, (causes[0] if causes else "NONE")


class Controller:
    def __init__(self, plant, ops: OpsDB, arm: str, provider: str, ctx: dict, jev_budget=None, jev_cache=None, ablation_drop: frozenset | None = None,
                 force_first: str | None = None, force_after_s: float = 0.0):
        self.p, self.env, self.ops, self.ctx = plant, plant.env, ops, ctx
        self.arm, self.cfg = arm, ARMS[arm]
        self.drop = ablation_drop if ablation_drop is not None else self.cfg["drop"]
        self.b = SnapshotBuilder(ops, plant.f, self.cfg["validated"], self.cfg["context"], self.cfg["detector"])
        self.op_builder = SnapshotBuilder(ops, plant.f, True, True, "ml")
        self.jev = JevDecisionAdapter(provider, ops, ctx, budget=jev_budget, cache=jev_cache) if self.cfg["chooser"] == "jev" else None
        self.intent = dict(main="run", sub="off", parallel=False)
        self.incidents: dict[tuple, dict] = {}
        self.closed_incidents: list[dict] = []
        self.inc_n = 0
        self.dec_n = 0
        self.cmd_n = 0
        self.last_decision_t = -1e9
        self.last_states: tuple | None = None
        self.last_feasible: tuple | None = None
        self.escalation_pending = False
        self.proposals: dict[tuple, dict] = {}
        self.decisions: list[dict] = []
        self.commands: list[dict] = []
        self.stale_decisions = 0
        self.intent_key: str | None = None
        self.notices: list[dict] = []
        self.maint_needed = False
        self.force_first, self.force_after_s, self.forced_done = force_first, force_after_s, False
        self.first_forecast: dict | None = None
        self.env.process(self._loop())

    # ------------------------------------------------------------------------------------------------ loop
    def _loop(self):
        dt = self.p.f.decision_interval_s
        yield self.env.timeout(dt)
        while True:
            ft = self.b.build(self.env.now, self.intent, use_reserve=self.p.disp.use_reserve)
            self._incidents(ft)
            self._intents(ft)
            if self._needs_decision(ft):
                self._decide(ft, decided_by=self.cfg["chooser"].upper())
            self._plan_notice(ft)
            yield self.env.timeout(dt)

    def _incidents(self, ft):
        now = self.env.now
        seen = set()
        for i in ft["incidents"]:
            k = (i["cause"], i["line"])
            seen.add(k)
            if k in self.incidents and self.incidents[k].get("closed_at") is not None:
                self.closed_incidents.append(self.incidents.pop(k))     # same cause again after it cleared: open a NEW incident
            if k not in self.incidents:
                self.inc_n += 1
                iid = f"INC-{self.ctx['episode_id']}-{self.inc_n:03d}"
                self.incidents[k] = dict(incident_id=iid, opened_at=now, cause=i["cause"], line=i["line"], detector=i["detector"], new=True, missing=0)
                self.ops.insert("anomaly_incidents", now, incident_id=iid, opened_at=now, closed_at=None, detection_source=i["detector"], detector=self.cfg["detector"],
                                signal=i["signal"], feature_snapshot=dict(good=ft["good_so_far"], gap=ft["gap"], ct=ft["ct_main_est"]), severity_rule=i.get("detector"), cause_hint=i["cause"])
        for k, inc in list(self.incidents.items()):
            if inc.get("closed_at") is None and k not in seen:
                inc["missing"] += 1
                if inc["missing"] >= 2:
                    inc["closed_at"] = now
                    self.ops.con.execute("UPDATE anomaly_incidents SET closed_at=? WHERE incident_id=?", (now, inc["incident_id"]))
            elif k in seen:
                inc["missing"] = 0

    def _plan_notice(self, ft):
        """Deterministic planning notice (not a control action): when the CP-SAT bound says the target is unreachable
        with any line combination, notify a human once with the forecast shortfall. Plan-aware arms only (R1+)."""
        if getattr(self, "plan_notified", None) is not None:
            return
        inc = next((i for i in ft["incidents"] if i["cause"] == "PLAN" and i.get("plan_infeasible")), None)
        if inc is None:
            return
        self.plan_notified = self.env.now
        self.dec_n += 1
        did = f"DEC-{self.ctx['episode_id']}-{self.dec_n:04d}"
        self.notices.append(dict(t=self.env.now, kind="PLAN_INFEASIBLE", bound=ft["max_achievable_good"], target=ft["target"]))
        self.ops.insert("policy_decisions", self.env.now, decision_id=did, arm=self.arm, decided_by="PLAN_MONITOR", proposed_action="ESCALATE_HUMAN", safety_gate_result="PASS",
                        blocked_reason=None, final_action="ESCALATE_HUMAN", reviewed_by=None, command_id=None, cause_label="PLAN")

    def _needs_decision(self, ft) -> bool:
        now = self.env.now
        states = (ft["obs"]["main"]["plc_state"], ft["obs"]["sub"]["plc_state"])
        feas = tuple(ft["feasible"])
        new_inc = any(i.get("new") for i in self.incidents.values())
        open_inc = any(i.get("closed_at") is None for i in self.incidents.values())
        need = (self.last_states is None or new_inc or states != self.last_states or feas != self.last_feasible
                or (open_inc and now - self.last_decision_t >= 900))
        for i in self.incidents.values():
            i["new"] = False
        self.last_states, self.last_feasible = states, feas
        return need

    # ------------------------------------------------------------------------------------------------ decide
    def _decide(self, ft, decided_by: str, operator: bool = False):
        now = self.env.now
        self.dec_n += 1
        did = f"DEC-{self.ctx['episode_id']}-{self.dec_n:04d}"
        allowed = list(ft["feasible"])
        open_ids = [i["incident_id"] for i in self.incidents.values() if i.get("closed_at") is None]
        stale = any(ft["obs"][r]["stale"] for r in ("main", "sub") if ft["obs"][r]["plc_state"] != "UNKNOWN")
        self.stale_decisions += int(stale)
        self.ops.insert("decision_snapshots", now, decision_id=did, incident_id=",".join(open_ids), arm=self.arm, db_snapshot_hash=ft["hash"],
                        input_features_json=json.dumps({k: v for k, v in ft.items() if k not in ("hash",)}, default=str), available_actions=allowed,
                        constraints=ft["infeasible"], plan_gap=ft["gap"], projected_end_qty=ft["forecast"]["CONTINUE_MAIN" if "CONTINUE_MAIN" in allowed else "ESCALATE_HUMAN"]["projected_good_end"],
                        context_freshness=dict(main_age_s=ft["obs"]["main"]["age_s"], sub_age_s=ft["obs"]["sub"]["age_s"], stale=stale))
        jev_out = None
        if self.force_first and not self.forced_done and not operator and now >= self.force_after_s and self.force_first in allowed:
            action, cause, decided_by = self.force_first, "FORCED", "ORACLE_REPLAY"     # replay-oracle: first incident decision forced
            self.forced_done = True
        elif operator or self.cfg["chooser"] == "planner":
            action, cause = planner_policy(ft, allowed)
        elif self.cfg["chooser"] == "rule":
            action, cause = rule_policy(ft, allowed)
        else:
            jev_out = self.jev.decide(now, did, ft, allowed, self.cfg["validated"], self.cfg["context"], self.drop)
            if jev_out["ok"]:
                action, cause = jev_out["action"], jev_out["cause"]
            else:
                action, cause, decided_by = "ESCALATE_HUMAN", "UNKNOWN", "FALLBACK"
        gate_ok, why = self._gate_action(ft, action, operator)
        final = action if gate_ok else "ESCALATE_HUMAN"
        rec = dict(decision_id=did, t=now, arm=self.arm, decided_by=decided_by, proposed=action, final=final, gate=("PASS" if gate_ok else "BLOCK"), why=why, cause=cause,
                   allowed=allowed, incidents=[i["cause"] for i in ft["incidents"]], jev=jev_out, gap=ft["gap"],
                   forecast={a: ft["forecast"][a]["projected_good_end"] for a in allowed}, operator=operator)
        self.decisions.append(rec)
        if self.first_forecast is None and not operator:
            self.first_forecast = dict(t=now, action=final, projected_good_end=ft["forecast"][final]["projected_good_end"] if final in ft["forecast"] else None,
                                       bound=ft["max_achievable_good"])
        cmd_ids = self._apply(ft, final, did, operator)
        self.ops.insert("policy_decisions", now, decision_id=did, arm=self.arm, decided_by=decided_by, proposed_action=action, safety_gate_result=rec["gate"],
                        blocked_reason=None if gate_ok else why, final_action=final, reviewed_by="OPERATOR_SIM" if operator else None, command_id=",".join(cmd_ids) or None, cause_label=cause)
        self.last_decision_t = now

    def _gate_action(self, ft, action: str, operator: bool) -> tuple[bool, str]:
        if action not in ft["feasible"]:
            return False, "not in the feasible action set"
        if self.cfg["gate"] == "basic" and not operator:
            return True, "basic gate: feasible-list check only"
        o, ev = ft["obs"], ft["evidence"] or {}
        if Action(action) in MOTION_ACTIONS:
            if o["main"]["plc_state"] in DOWN and o["sub"]["plc_state"] in DOWN:
                return False, "both lines down: automatic motion prohibited"
            lines = {"RECOVER_MAIN": ["main"], "SWITCH_TO_SUB": ["sub"], "RUN_PARALLEL": ["main", "sub"], "REQUEST_TRANSPORT": []}[action]
            if any(l in ev.get("stale_lines", []) for l in lines):
                return False, "evidence stale for the target line"
            if action == "REQUEST_TRANSPORT" and (ev.get("qr_mismatch_30min") or ev.get("count_mismatch")):
                return False, "QR/count evidence inconsistent: re-measure before moving pallets"
            if ft["forecast"][action]["peak_kw"] > ft["limit_kw"] + 1e-6:
                return False, f"peak {ft['forecast'][action]['peak_kw']} kW > limit {ft['limit_kw']} kW"
        return True, "full gate: freshness, consistency, interlock and power checks passed"

    # ------------------------------------------------------------------------------------------------ execution
    def _cmd(self, cmd: str, target: str, decision_id: str, intent_key: str) -> str | None:
        """Propose → oracle-score → gate (already decided by caller) → execute on the virtual PLC; log everything."""
        now = self.env.now
        unsafe = self.p.assess(cmd, target)
        key = (cmd, target, intent_key, unsafe)
        if unsafe:
            self.proposals.setdefault(key, dict(forbidden=True, allowed=False, reason=unsafe))["allowed"] = True
            self.p.truth.insert("unsafe_events", now, command_id=None, decision_id=decision_id, command=cmd, reason=unsafe, authorized=1)
        self.cmd_n += 1
        cid = f"CMD-{self.ctx['episode_id']}-{self.cmd_n:04d}"
        res = self.p.command(cmd, target)
        self.commands.append(dict(command_id=cid, t=now, cmd=cmd, target=target, decision_id=decision_id, **res))
        self.ops.insert("command_execution", now, command_id=cid, decision_id=decision_id, target=target, command=cmd, channel="VIRTUAL_PLC", request_at=now, ack_at=now,
                        apply_at=now, verify_at=now + self.p.f.decision_interval_s, before_state=res["before"], after_state=res["after"], confirmed=int(res["accepted"]), timeout=0, virtual_only=1)
        return cid

    def _blocked(self, cmd: str, target: str, intent_key: str):
        unsafe = self.p.assess(cmd, target)
        if unsafe:
            self.proposals.setdefault((cmd, target, intent_key, unsafe), dict(forbidden=True, allowed=False, reason=unsafe))

    def _apply(self, ft, action: str, did: str, operator: bool) -> list[str]:
        o = ft["obs"]
        main_prod, sub_prod = o["main"]["plc_state"] in PRODUCING, o["sub"]["plc_state"] in PRODUCING
        quality_or_drift = any(i["cause"] in ("QUALITY", "PROCESS_DRIFT") for i in ft["incidents"])
        ids: list[str] = []
        self.intent_key = did
        if action == "SWITCH_TO_SUB":
            if main_prod:
                ids.append(self._cmd("STOP_MAIN", "main", did, did))
            ids.append(self._cmd("START_SUB", "sub", did, did))
            self.intent.update(main="off", sub="run", parallel=False)
        elif action == "RUN_PARALLEL":
            if not sub_prod:
                ids.append(self._cmd("START_SUB", "sub", did, did))
            if not main_prod and self.maint_needed and o["main"]["plc_state"] in ("IDLE", "OFF"):
                ids.append(self._cmd("MAINTAIN_MAIN", "main", did, did))   # never restart a line stopped for quality without maintenance
                self.maint_needed = False
            self.intent.update(main="recover" if not main_prod else "run", sub="run", parallel=True)
        elif action == "RECOVER_MAIN":
            idle = o["main"]["plc_state"] in ("IDLE", "OFF")
            if (main_prod and quality_or_drift) or (idle and (quality_or_drift or self.maint_needed)):
                ids.append(self._cmd("MAINTAIN_MAIN", "main", did, did))   # remove the cause before restarting
                self.maint_needed = False
            self.intent.update(main="recover", parallel=False)
        elif action == "SAFE_STOP_REQUEST":
            ids.append(self._cmd("STOP_MAIN", "main", did, did))
            self.intent.update(main="off")
            self.maint_needed = self.maint_needed or quality_or_drift
        elif action == "REQUEST_TRANSPORT":
            self.p.disp.use_reserve = True
        elif action == "ESCALATE_HUMAN" and not self.escalation_pending and not operator:
            self.escalation_pending = True
            self.env.process(self._operator(did))
        return [i for i in ids if i]

    def _intents(self, ft):
        """Standing intents: restart main when the arm's interlock allows; stop the sub once main runs again."""
        o = ft["obs"]
        st = o["main"]["plc_state"]
        if self.intent["main"] == "recover" and st in ("READY", "IDLE", "OFF"):
            if self.cfg["gate"] == "basic":
                allowed = True
            else:
                allowed = (st != "READY" or bool(o["main"]["diag_ok"])) and not o["main"]["stale"] and \
                          (ft["forecast"]["RECOVER_MAIN"]["peak_kw"] if not self.intent["parallel"] else ft["forecast"]["RUN_PARALLEL"]["peak_kw"]) <= ft["limit_kw"] + 1e-6
            key = f"{self.intent_key_for('main')}"
            ml, sl = self.p.f.line("main"), self.p.f.line("sub")
            if allowed and o["sub"]["plc_state"] in PRODUCING and not self.intent["parallel"] and self.p.f.power.aux_kw + ml.run_kw + sl.run_kw > ft["limit_kw"]:
                self._cmd("STOP_SUB", "sub", self.intent_key or "intent", "stop_sub_before_main")   # sequence under the known limit
            if allowed:
                self._cmd("RESTART_MAIN", "main", self.intent_key or "intent", key)
            else:
                self._blocked("RESTART_MAIN", "main", key)
        if self.intent["main"] == "recover" and st in PRODUCING and not self.intent["parallel"]:
            if o["sub"]["plc_state"] in PRODUCING:
                self._cmd("STOP_SUB", "sub", self.intent_key or "intent", "stop_sub")
            self.intent.update(main="run", sub="off")
        if self.p.disp.use_reserve and ft["amr_available"] > 0:
            self.p.disp.use_reserve = False

    def intent_key_for(self, line: str) -> str:
        m = self.p.lines[line]
        return f"{line}-restart-{len(m.state_log)}"

    def _operator(self, did: str):
        yield self.env.timeout(self.p.f.human_response_s)
        ft = self.op_builder.build(self.env.now, self.intent, use_reserve=self.p.disp.use_reserve)
        ft["incidents"] = ft["incidents"] or []
        self._decide(ft, decided_by="OPERATOR_SIM", operator=True)
        self.escalation_pending = False

    # ------------------------------------------------------------------------------------------------ summary
    def all_incidents(self) -> list[dict]:
        return self.closed_incidents + list(self.incidents.values())

    def unsafe_summary(self) -> dict:
        forb = len(self.proposals)
        allowed = sum(1 for v in self.proposals.values() if v["allowed"])
        reasons: dict[str, int] = {}
        for v in self.proposals.values():
            if v["allowed"]:
                reasons[v["reason"]] = reasons.get(v["reason"], 0) + 1
        return dict(forbidden_proposed=forb, forbidden_allowed=allowed, unsafe_authorization_rate=(allowed / forb) if forb else None, allowed_by_reason=reasons)
