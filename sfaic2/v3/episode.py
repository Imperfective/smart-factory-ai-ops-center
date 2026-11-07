"""One episode = one planned 8 h shift of one (scenario, load, seed) under one arm. Builds the stores, plant, fleet,
dispatcher and controller, runs SimPy to the end of the shift, then the evaluator computes the pre-registered outcome
metrics (docs 01 §3) from truth + operational logs. Nothing here is estimated: every number is counted in the run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import FactoryV3, build_scenario
from .control import ARMS, Controller
from .db import OpsDB, TruthStore
from .mqtt import Dispatcher, Fleet
from .plant import Plant

COST_PROFILES = {  # assumed monetary weights (currency units); sensitivity across profiles is reported, not tuned
    "balanced": dict(shortfall_unit=10.0, stop_min=2.0, defect_shipped=25.0, transport_error=20.0, delay_min=1.0, kwh=0.2),
    "quality": dict(shortfall_unit=10.0, stop_min=2.0, defect_shipped=100.0, transport_error=20.0, delay_min=1.0, kwh=0.2),
    "energy": dict(shortfall_unit=10.0, stop_min=2.0, defect_shipped=25.0, transport_error=20.0, delay_min=1.0, kwh=1.0),
}
TRUTH_CAUSE = {"MAIN_FAULT": "EQUIPMENT", "SUB_FAULT": "EQUIPMENT", "CT_DRIFT": "PROCESS_DRIFT", "DEFECT_RISE": "QUALITY", "COUNTER_ANOMALY": "COUNTER",
               "VISION_QR_MISMATCH": "VISION_QR", "NETWORK": "NETWORK", "AMR_DOWN": "TRANSPORT", "POWER_LIMIT": "POWER", "PLAN_INFEASIBLE": "PLAN"}


def _time_in(log: list, states: tuple, t_end: float, t0: float = 0.0) -> float:
    tot = 0.0
    for (t, s), nxt in zip(log, log[1:] + [(t_end, None)]):
        a, b = max(t, t0), min(nxt[0], t_end)
        if s in states and b > a:
            tot += b - a
    return tot


def _first_after(log: list, t0: float, states: tuple) -> float | None:
    for t, s in log:
        if t >= t0 and s in states:
            return t
    for (t, s), nxt in zip(log, log[1:]):
        if t < t0 <= nxt[0] and s in states:
            return t0
    return None


def setup(sid: str, load: str, seed: int, arm: str, provider: str = "mock", jev_budget=None, jev_cache=None, factory: FactoryV3 | None = None,
          run_id: str = "adhoc", force_first: str | None = None, sensitivity: dict | None = None, camera_mode: str = "replay") -> dict:
    """Build stores, plant, fleet, dispatcher and controller for one episode (shared by batch runs and live sessions)."""
    f, faults = build_scenario(sid, load, seed, factory)
    for k, v in (sensitivity or {}).items():
        setattr(f, k, v)
    ep = f"{sid}-{load}-s{seed}-{arm}"
    ctx = dict(run_id=run_id, episode_id=ep, scenario_id=sid, seed=seed)
    ops, truth = OpsDB(ctx), TruthStore(ctx)
    ops.insert("production_plans", 0.0, source="CONFIG", plan_id=f.plan.plan_id, product_id=f.plan.product_id, target_good_qty=f.plan.target_good_qty, due_at=f.plan.shift_s,
               shift_minutes=f.plan.shift_s / 60, priority=f.plan.priority)
    for l in f.lines:
        ops.insert("line_configs", 0.0, source="CONFIG", line_id=l.line_id, role=l.role, ct_s=l.ct_s, ramp_up_s=l.startup_s, capacity_per_shift=f.capacity(l.role),
                   yield_rate=l.yield_rate, rated_kw=l.run_kw, idle_kw=l.idle_kw, equipment_ids=l.equipment_ids, eligibility=dict(product=f.plan.product_id, material_ready=l.material_ready))
    plant = Plant(f, faults, seed, ops, truth, camera_mode=camera_mode)
    fleet = Fleet(plant)
    cfg = ARMS[arm]
    disp = Dispatcher(plant, ops, ctx, mode=cfg["transport"], adaptive_timeout=cfg["adaptive"])
    first_fault = min((x.at_s for x in faults), default=0.0)
    ctrl = Controller(plant, ops, arm, provider, ctx, jev_budget=jev_budget, jev_cache=jev_cache, force_first=force_first, force_after_s=first_fault)
    return dict(f=f, faults=faults, ops=ops, truth=truth, plant=plant, fleet=fleet, disp=disp, ctrl=ctrl, ctx=ctx, ep=ep)


def run_episode(sid: str, load: str, seed: int, arm: str, provider: str = "mock", raw_dir: Path | None = None, jev_budget=None, jev_cache=None,
                factory: FactoryV3 | None = None, run_id: str = "adhoc", series: bool = False, force_first: str | None = None, sensitivity: dict | None = None) -> dict:
    wall0 = time.time()
    E = setup(sid, load, seed, arm, provider, jev_budget, jev_cache, factory, run_id, force_first, sensitivity)
    f, faults, ops, truth, plant, fleet, disp, ctrl, ep = E["f"], E["faults"], E["ops"], E["truth"], E["plant"], E["fleet"], E["disp"], E["ctrl"], E["ep"]
    ser: list = []
    if series:
        def _mon():
            while True:
                ser.append(dict(t=plant.env.now, good=sum(l.good_accepted for l in plant.lines.values()), target=f.plan.target_good_qty * plant.env.now / f.plan.shift_s,
                                main=plant.lines["main"].state, sub=plant.lines["sub"].state, kw=round(plant.load_kw(), 1), available_kw=plant.available_kw(),
                                pallets_waiting=sum(len(l.outlet) for l in plant.lines.values())))
                yield plant.env.timeout(300)
        plant.env.process(_mon())
    plant.env.run(until=f.plan.shift_s)
    res = plant.finalize()
    out = evaluate(sid, load, seed, arm, provider, f, faults, plant, fleet, disp, ctrl, res, truth)
    out["wall_s"] = round(time.time() - wall0, 2)
    if series:
        out["_series"] = ser
        out["_decisions"] = ctrl.decisions
        out["_commands"] = ctrl.commands
    if raw_dir is not None:
        ops.persist(Path(raw_dir) / f"{ep}.ops.sqlite")
        truth.persist(Path(raw_dir) / f"{ep}.truth.sqlite")
    out["_ops"], out["_truth"] = (ops, truth) if series else (None, None)
    return out


def evaluate(sid, load, seed, arm, provider, f, faults, plant, fleet, disp, ctrl, res, truth) -> dict:
    T = f.plan.shift_s
    tgt = f.plan.target_good_qty
    q = res["good_accepted"]
    logs = res["state_log"]
    main_log, sub_log = logs["main"], logs["sub"]
    # ---- truth events (for detection / recovery / delay)
    events = []
    for flt in faults:
        events.append(dict(kind=flt.kind, cause=TRUTH_CAUSE[flt.kind], at=flt.at_s, end=(flt.at_s + flt.duration_s) if flt.duration_s else flt.at_s + (flt.truth_repair_s or 0) + 300))
    if sid == "S10":
        events.append(dict(kind="PLAN_INFEASIBLE", cause="PLAN", at=0.0, end=T))
    incidents = ctrl.all_incidents()
    matched_inc, det_delays, detected, cause_pairs = set(), [], 0, []
    for ev in events:
        hits = [i for i in incidents if i["cause"] == ev["cause"] and ev["at"] - 60 <= i["opened_at"] <= ev["end"] + 600]
        if hits:
            detected += 1
            det_delays.append(max(0.0, min(i["opened_at"] for i in hits) - ev["at"]))
            matched_inc.update(i["incident_id"] for i in hits)
        first = next((d for d in ctrl.decisions if d["t"] >= ev["at"] and not d["operator"]), None)
        cause_pairs.append((ev["cause"], first["cause"] if first else "NO_DECISION"))
    fp = [i for i in incidents if i["incident_id"] not in matched_inc]
    prec = (len(incidents) - len(fp)) / len(incidents) if incidents else None
    rec = detected / len(events) if events else None
    # ---- recovery
    mf = next((x for x in faults if x.kind == "MAIN_FAULT"), None)
    mttr_main = prod_rec = None
    if mf:
        back = _first_after(main_log, mf.at_s + 1, ("RUNNING",))
        mttr_main = (back - mf.at_s) if back else None
        any_back = [t for t in (_first_after(main_log, mf.at_s + 1, ("RUNNING",)), _first_after(sub_log, mf.at_s + 1, ("RUNNING",))) if t]
        prod_rec = (min(any_back) - mf.at_s) if any_back else None
    # ---- delay to first corrective command
    corrective = {"MAIN_FAULT": ("START_SUB", "RESTART_MAIN"), "DEFECT_RISE": ("STOP_MAIN", "MAINTAIN_MAIN", "START_SUB"), "CT_DRIFT": ("MAINTAIN_MAIN", "START_SUB")}
    delays = []
    for flt in faults:
        if flt.kind in corrective:
            c = next((c for c in ctrl.commands if c["t"] >= flt.at_s and c["cmd"] in corrective[flt.kind] and c["accepted"]), None)
            delays.append(((c["t"] if c else T) - flt.at_s) / 60)
        if flt.kind == "AMR_DOWN":
            d = next((d for d in ctrl.decisions if d["t"] >= flt.at_s and d["final"] == "REQUEST_TRANSPORT"), None)
            delays.append(((d["t"] if d else min(T, flt.at_s + flt.duration_s)) - flt.at_s) / 60)
    action_delay_min = sum(delays)
    # ---- unnecessary stops: policy stops of a healthy main line + feeder trips
    def quality_active(t):
        return any(x.kind in ("DEFECT_RISE", "CT_DRIFT") and x.at_s <= t for x in faults)
    ivs = []
    for c in ctrl.commands:
        if c["cmd"] in ("STOP_MAIN", "MAINTAIN_MAIN") and c["accepted"] and not quality_active(c["t"]):
            back = _first_after(main_log, c["t"] + 1, ("RUNNING",)) or T
            ivs.append((c["t"], back))
    unnec, cur = 0.0, None
    for a, b in sorted(ivs):            # union of stop intervals (repeated commands must not double-count)
        if cur is None or a > cur[1]:
            if cur:
                unnec += (cur[1] - cur[0]) / 60
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        unnec += (cur[1] - cur[0]) / 60
    tripped_min = (_time_in(main_log, ("TRIPPED",), T) + _time_in(sub_log, ("TRIPPED",), T)) / 60
    unnec += tripped_min
    # ---- transport
    fs, ds = fleet.stats, disp.summary()
    transport_errors = fs["wrong_pallet"] + fs["premature"] + fs["duplicate_executions"] + fs["battery_violations"] + ds["timed_out_orders"]
    # ---- unsafe
    us = ctrl.unsafe_summary()
    executed_unsafe = len(plant.unsafe) + res["overloads"]
    if not disp.mode == "VALIDATED":
        us["forbidden_proposed"] += fs["battery_violations"]
        us["forbidden_allowed"] += fs["battery_violations"]
        if us["forbidden_proposed"]:
            us["unsafe_authorization_rate"] = us["forbidden_allowed"] / us["forbidden_proposed"]
    # ---- escalation
    esc = [d for d in ctrl.decisions if d["final"] == "ESCALATE_HUMAN" and not d["operator"]]
    warranted = []
    if sid in ("S4",):
        warranted = [(x.at_s, x.at_s + (x.truth_repair_s or 0)) for x in faults if x.kind == "SUB_FAULT"]
    if sid == "S10":
        warranted = [(0.0, T)]
    if sid == "S8":
        warranted = [(x.at_s, x.at_s + x.duration_s) for x in faults if x.kind == "VISION_QR_MISMATCH"]
    esc_in = [d for d in esc if any(a <= d["t"] <= b for a, b in warranted)] + [n for n in ctrl.notices if any(a <= n["t"] <= b for a, b in warranted)]
    esc = esc + ctrl.notices
    shortfall = max(0, tgt - q)
    comp = dict(shortfall_unit=shortfall, stop_min=unnec, defect_shipped=res["defects_shipped"], transport_error=transport_errors, delay_min=action_delay_min, kwh=res["energy_kwh"])
    costs = {f"cost_{k}": round(sum(w[c] * comp[c] for c in w), 2) for k, w in COST_PROFILES.items()}
    first_ev = min((e["at"] for e in events), default=0.0)
    first_dec = next((d for d in ctrl.decisions if d["t"] >= first_ev and not d["operator"] and (events or True)), None)
    js = ctrl.jev.summary() if ctrl.jev else {}
    net_rows = plant.ops.query("SELECT COUNT(*) AS n, SUM(dropped) AS lost, SUM(duplicate) AS dup FROM network_events")[0]
    out = dict(
        scenario=sid, load=load, seed=seed, arm=arm, provider=("NONE" if ARMS[arm]["chooser"] != "jev" else ("MOCK_JEV" if provider == "mock" else "TYPESAFE_JEV")),
        target=tgt, good_end=q, true_good_end=res["true_good"], par=min(1.0, q / tgt) if tgt else None, raw_attainment=q / tgt if tgt else None, target_met=int(q >= tgt),
        shortfall=shortfall, defects_shipped=res["defects_shipped"], rejected=res["rejected"], processed=res["processed"], main_good=res["main_good"], sub_good=res["sub_good"],
        wip_end=res["wip_end"], pallets_left=res["pallets_left"],
        unsafe_authorization_rate=us["unsafe_authorization_rate"], forbidden_proposed=us["forbidden_proposed"], forbidden_allowed=us["forbidden_allowed"],
        executed_unsafe=executed_unsafe, unsafe_reasons=json.dumps(us["allowed_by_reason"]),
        unnecessary_stop_min=round(unnec, 2), tripped_min=round(tripped_min, 2), action_delay_min=round(action_delay_min, 2), **costs,
        detection_precision=prec, detection_recall=rec, detection_f1=(2 * prec * rec / (prec + rec)) if prec and rec else None,
        false_alarms_per_h=len(fp) / (T / 3600), mttd_s=(sum(det_delays) / len(det_delays)) if det_delays else None, n_truth_events=len(events), n_incidents=len(incidents),
        cause_pairs=json.dumps(cause_pairs), mttr_main_s=mttr_main, production_recovery_s=prod_rec, recovery_success=(int(prod_rec is not None) if mf else None),
        recurrences=res["recurrences"], main_utilization=_time_in(main_log, ("RUNNING",), T) / T, sub_utilization=_time_in(sub_log, ("RUNNING",), T) / T,
        main_downtime_min=round(res["main_downtime_s"] / 60, 1), blocked_min=round(res["blocked_s"] / 60, 1),
        energy_kwh=round(res["energy_kwh"], 2), kwh_per_good=round((res["energy_kwh"] + res["amr_battery_deficit_kwh"]) / q, 4) if q else None,
        line_kwh=round(res["line_kwh"], 2), amr_charge_kwh=round(res["amr_charge_kwh"], 3), amr_battery_deficit_kwh=round(res["amr_battery_deficit_kwh"], 3), amr_charges=res["amr_charges"],
        factory_kwh_total=round(res["energy_kwh"] + res["amr_battery_deficit_kwh"], 2), max_kw=round(res["max_kw"], 1), overloads=res["overloads"],
        orders=ds["orders"], published=ds["published"], retries=ds["retries"], ack_timeouts=ds["timeouts"], ack_p50_ms=ds["ack_latency_p50_ms"], ack_p95_ms=ds["ack_latency_p95_ms"],
        completed_orders=ds["completed_orders"], timed_out_orders=ds["timed_out_orders"], order_completion_rate=(ds["completed_orders"] / ds["orders"]) if ds["orders"] else None,
        duplicate_executions=fs["duplicate_executions"], duplicates_suppressed=fs["duplicates_suppressed"], wrong_pallet=fs["wrong_pallet"], premature_dispatch=fs["premature"],
        battery_violations=fs["battery_violations"], transport_errors=transport_errors, status_regressions=ds["status_regressions"], qr_holds=ds["holds"],
        delivered_pallets=fleet.delivered_pallets, amr_utilization=sum(c.busy_s for c in fleet.carriers if c.ctype == "AMR") / max(1, sum(1 for c in fleet.carriers if c.ctype == "AMR")) / T,
        mqtt_msgs=net_rows["n"], msgs_lost=net_rows["lost"] or 0, msgs_dup=net_rows["dup"] or 0, stale_decisions=ctrl.stale_decisions,
        decisions=len([d for d in ctrl.decisions if not d["operator"]]), operator_decisions=len([d for d in ctrl.decisions if d["operator"]]), escalations=len(esc),
        escalations_warranted=len(esc_in), escalation_precision=(len(esc_in) / len(esc)) if esc else None, escalation_recall=(int(bool(esc_in)) if warranted else None),
        gate_blocks=sum(1 for d in ctrl.decisions if d["gate"] == "BLOCK"), first_action=(first_dec["final"] if first_dec else None),
        first_action_proposed=(first_dec["proposed"] if first_dec else None), jev_calls=js.get("calls", 0), jev_cached=js.get("cached", 0), jev_errors=js.get("errors", 0),
        jev_latency_p50_ms=js.get("latency_p50_ms"), jev_latency_p95_ms=js.get("latency_p95_ms"), jev_latency_p99_ms=js.get("latency_p99_ms"),
        jev_input_tokens=js.get("input_tokens", 0), jev_cost_usd=js.get("cost_usd", 0.0), fallbacks=sum(1 for d in ctrl.decisions if d["decided_by"] == "FALLBACK"),
        vision_source=("live:YOLO11n/" + str(getattr(plant, "camera_mode", ""))) if getattr(plant, "camera_mode", "replay") == "live" else plant.pool.source,
        forced_applied=int(bool(ctrl.forced_done)), plan_notices=len(ctrl.notices), plan_notice_t=(ctrl.notices[0]["t"] if ctrl.notices else None),
        forecast_first_t=(ctrl.first_forecast or {}).get("t"), forecast_first_good_end=(ctrl.first_forecast or {}).get("projected_good_end"),
        forecast_error_first=(((ctrl.first_forecast or {}).get("projected_good_end") or 0) - q) if (ctrl.first_forecast or {}).get("projected_good_end") is not None else None,
        cpsat_bound_first=(ctrl.first_forecast or {}).get("bound"),
    )
    return out
