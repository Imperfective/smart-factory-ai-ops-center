"""Live v3 session for the Command Center UI: steps the SimPy closed loop in a background thread at a chosen speed
(simulated seconds per wall second) and exposes read-only views over the operational DB. Ground truth is exposed only
through `truth_view()`, which the API serves in researcher mode only."""
from __future__ import annotations

import json
import math
import threading
import time
import uuid

from .config import SCENARIOS, FactoryV3
from .episode import evaluate, setup
from .snapshot import PRODUCING


class LiveSession:
    def __init__(self, sid: str, load: str, seed: int, arm: str, provider: str = "mock", speed: float = 120.0, factory: FactoryV3 | None = None,
                 camera_mode: str = "live", unit_judge: str = "off", defect_image_ratio: float | None = None, headless: bool = False, source: str = "live",
                 autosave: bool = True, jev_budget=None, jev_cache=None, sensitivity: dict | None = None):
        self.id = f"v3_{sid}_{arm}_{uuid.uuid4().hex[:6]}"
        from .yolo_live import LiveYolo
        if camera_mode == "live" and not LiveYolo.available()[0]:
            camera_mode = "replay"
        self.args = dict(sid=sid, load=load, seed=seed, arm=arm, provider=provider, camera_mode=camera_mode, unit_judge=unit_judge, defect_image_ratio=defect_image_ratio)
        self.factory = factory
        self.speed = speed
        self.lock = threading.RLock()
        self.state = "PAUSED"
        self.result: dict | None = None
        self.error: str | None = None
        self.source, self.autosave, self.jev_budget, self.jev_cache = source, autosave, jev_budget, jev_cache
        self.sensitivity = sensitivity or {}      # plant-assumption overrides (robustness experiments only)
        self.saved_run: str | None = None
        self.save_error: str | None = None
        self._build()
        if not headless:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def _build(self):
        a = self.args
        self.E = setup(a["sid"], a["load"], a["seed"], a["arm"], a["provider"], factory=self.factory, run_id=self.id, camera_mode=a["camera_mode"],
                       jev_budget=self.jev_budget, jev_cache=self.jev_cache, sensitivity=self.sensitivity)
        if a["unit_judge"] in ("mock", "typesafe"):
            from .unit_judge import UnitJudge
            self.E["plant"].unit_judge = UnitJudge(a["unit_judge"])
        self.E["plant"].defect_image_ratio = a["defect_image_ratio"]
        if a["defect_image_ratio"] is not None:     # the configured incoming lot quality is operational knowledge, not a fault
            self.E["ctrl"].b.incoming_defect_rate = a["defect_image_ratio"]
            self.E["ctrl"].op_builder.incoming_defect_rate = a["defect_image_ratio"]
        self.series: list[dict] = []
        self.result = None
        self.saved_run = None
        self._wall0 = time.time()
        self._sample()

    # ---------------------------------------------------------------------------------------------- control
    def control(self, action: str, value: float | None = None):
        with self.lock:
            if action == "start" or action == "resume":
                self.state = "RUNNING" if self.result is None else self.state
            elif action == "pause":
                self.state = "PAUSED" if self.state == "RUNNING" else self.state
            elif action == "speed" and value:
                self.speed = max(1.0, min(5000.0, float(value)))
            elif action == "step":
                self._advance(float(value or 300.0))
            elif action == "reset":
                self._build()
                self.state = "PAUSED"
            elif action == "run_to_end":
                self._advance(self.E["f"].plan.shift_s)
        return self.status()

    def _advance(self, dt: float):
        env, f = self.E["plant"].env, self.E["f"]
        target = min(f.plan.shift_s, env.now + dt)
        while env.now < target - 1e-9:
            nxt = min(target, env.now + 60.0)
            env.run(until=nxt)
            self._sample()
        if env.now >= f.plan.shift_s - 1e-9 and self.result is None:
            self._finish()

    def close(self):
        self.closed = True

    def _loop(self):
        while not getattr(self, "closed", False):
            time.sleep(0.25)
            try:
                with self.lock:
                    if self.state == "RUNNING" and self.result is None:
                        self._advance(self.speed * 0.25)
            except Exception as exc:  # noqa: BLE001
                self.error, self.state = f"{type(exc).__name__}: {exc}", "ERROR"

    def _sample(self):
        p = self.E["plant"]
        self.series.append(dict(t=round(p.env.now), good=sum(l.good_accepted for l in p.lines.values()), target_line=round(self.E["f"].plan.target_good_qty * p.env.now / self.E["f"].plan.shift_s, 1),
                                kw=round(p.load_kw(), 1), main=p.lines["main"].state, sub=p.lines["sub"].state))

    def _finish(self):
        E = self.E
        res = E["plant"].finalize()
        self.result = evaluate(self.args["sid"], self.args["load"], self.args["seed"], self.args["arm"], self.args["provider"], E["f"], E["faults"], E["plant"], E["fleet"],
                               E["disp"], E["ctrl"], res, E["truth"])
        self.state = "FINISHED"
        if not self.autosave:
            return
        try:                         # append to the cumulative results DB + CSV (statistics cover every run so far)
            from . import live_results
            a = self.args
            self.saved_run = live_results.record(self.result, E, source=self.source, course_seed=a["seed"], unit_judge=a["unit_judge"], camera_mode=a["camera_mode"],
                                                 defect_image_ratio=a["defect_image_ratio"], wall_s=time.time() - self._wall0, energy=self._energy())
        except Exception as exc:  # noqa: BLE001
            self.save_error = f"{type(exc).__name__}: {exc}"

    # ---------------------------------------------------------------------------------------------- views (operational data only)
    def status(self) -> dict:
        E, now = self.E, self.E["plant"].env.now
        return dict(id=self.id, state=self.state, sim_time_s=round(now, 1), speed=self.speed, shift_s=E["f"].plan.shift_s, error=self.error, saved_run=self.saved_run, save_error=self.save_error, **self.args)

    def overview(self) -> dict:
        with self.lock:
            E = self.E
            ctrl, f = E["ctrl"], E["f"]
            now = E["plant"].env.now
            ft = ctrl.b.build(now, ctrl.intent, use_reserve=E["disp"].use_reserve) if now > 0 else None
            fc = None
            prob = None
            if ft:
                it = ctrl.intent
                cur = ("RUN_PARALLEL" if it["parallel"] else "RECOVER_MAIN" if it["main"] == "recover" else
                       "SWITCH_TO_SUB" if it["sub"] == "run" and it["main"] == "off" else "CONTINUE_MAIN" if ft["obs"]["main"]["plc_state"] in PRODUCING else "ESCALATE_HUMAN")
                fc = ft["forecast"]
                mean = fc[cur]["projected_good_end"]
                rem_units = max(1.0, mean - ft["good_so_far"])
                sd = max(1.0, math.sqrt(rem_units) * 0.05 * 3)
                prob = 0.5 * (1 + math.erf((mean - f.plan.target_good_qty) / (sd * math.sqrt(2))))
            by_state: dict = {}
            for o in E["disp"].orders.values():
                by_state[o["state"]] = by_state.get(o["state"], 0) + 1
            backlog = sum(1 for o in E["disp"].orders.values() if o["state"] in ("PUBLISHED", "ACKNOWLEDGED", "EXECUTING"))
            lines = {}
            for role in ("main", "sub"):
                o = (ft or {}).get("obs", {}).get(role) if ft else None
                lines[role] = dict(observed_state=o["plc_state"] if o else None, age_s=o["age_s"] if o else None, stale=o["stale"] if o else None,
                                   fault_code=(o or {}).get("fault_code"), ct_nominal=f.line(role).ct_s, run_kw=f.line(role).run_kw)
            return dict(status=self.status(), scenario=SCENARIOS[self.args["sid"]].model_dump(), target=f.plan.target_good_qty,
                        good_so_far=ft["good_so_far"] if ft else 0, projected_end=fc and {a: v["projected_good_end"] for a, v in fc.items()},
                        feasible=ft["feasible"] if ft else [], infeasible=ft["infeasible"] if ft else {}, cpsat_bound=ft["max_achievable_good"] if ft else None,
                        target_probability=None if prob is None else round(prob, 3), forecast_basis=cur if ft else None,
                        target_probability_note="model estimate from the program forecast with cycle-time noise only; excludes future unplanned faults",
                        routing=dict(ctrl.intent), lines=lines, power=dict(load_kw=(ft or {}).get("electrical", {}) and ft["electrical"]["load_kw"] if ft and ft.get("electrical") else None,
                                                                           limit_kw=ft["limit_kw"] if ft else f.power.nameplate_limit_kw, limit_source=ft["limit_source"] if ft else None),
                        mqtt=dict(orders_by_state=by_state, backlog=backlog, use_reserve=E["disp"].use_reserve),
                        incidents_open=[dict(cause=i["cause"], line=i["line"], opened_at=i["opened_at"], incident_id=i["incident_id"]) for i in ctrl.incidents.values() if i.get("closed_at") is None],
                        incidents_closed=len(ctrl.closed_incidents) + sum(1 for i in ctrl.incidents.values() if i.get("closed_at") is not None),
                        energy=self._energy(), series=self.series[-600:], result=self._public_result())

    def _energy(self) -> dict:
        p = self.E["plant"]; fs = p.f.fleet
        deficit = sum((100.0 - c.battery) / 100.0 * fs.battery_kwh / fs.charger_eff for c in p.fleet.carriers) if p.fleet else 0.0
        aux = p.energy_kwh - p.line_kwh - p.amr_charge_kwh
        return dict(total_grid_kwh=round(p.energy_kwh, 2), line_kwh=round(p.line_kwh, 2), aux_kwh=round(aux, 2), amr_charge_kwh=round(p.amr_charge_kwh, 3),
                    amr_battery_deficit_kwh=round(deficit, 3), factory_total_kwh=round(p.energy_kwh + deficit, 2), amr_charges=p.fleet.charges if p.fleet else 0,
                    amr_charging_now_kw=round(p.amr_charge_kw(), 2), battery_kwh=fs.battery_kwh, charger_eff=fs.charger_eff)

    def _public_result(self):
        if not self.result:
            return None
        return {k: v for k, v in self.result.items() if not k.startswith("_")}

    def decisions(self, limit: int = 200) -> list[dict]:
        with self.lock:
            ops = self.E["ops"]
            rows = ops.query("SELECT p.decision_id, p.simulation_time AS t, p.decided_by, p.proposed_action, p.safety_gate_result, p.blocked_reason, p.final_action, p.reviewed_by, p.command_id, p.cause_label, "
                             "s.available_actions, s.constraints, s.plan_gap, s.projected_end_qty, s.context_freshness, s.incident_id, "
                             "r.selected_action AS jev_action, r.per_choice_probabilities AS jev_probs, r.cause_class AS jev_cause, r.needs_operator_p, r.latency_ms, r.error_code, r.provider AS jev_provider "
                             "FROM policy_decisions p LEFT JOIN decision_snapshots s ON s.decision_id=p.decision_id LEFT JOIN jev_responses r ON r.request_id='JREQ-' || p.decision_id "
                             "ORDER BY p.simulation_time DESC LIMIT ?", (limit,))
            for r in rows:
                for k in ("available_actions", "constraints", "context_freshness", "jev_probs"):
                    if r.get(k):
                        try:
                            r[k] = json.loads(r[k])
                        except Exception:  # noqa: BLE001
                            pass
            return rows

    def incidents(self) -> list[dict]:
        with self.lock:
            return self.E["ops"].query("SELECT incident_id, opened_at, closed_at, detection_source, detector, signal, cause_hint, feature_snapshot FROM anomaly_incidents ORDER BY opened_at")

    def incident(self, iid: str) -> dict:
        """Evidence package for one incident: raw YOLO JSON, QR, PLC, network rows around the opening time, the decision
        snapshots that referenced it, Jev request/response, policy decision and virtual commands."""
        with self.lock:
            ops = self.E["ops"]
            inc = ops.query("SELECT * FROM anomaly_incidents WHERE incident_id=?", (iid,))
            if not inc:
                return {}
            t0 = inc[0]["opened_at"]
            w = (t0 - 300, t0 + 60)
            decs = ops.query("SELECT * FROM decision_snapshots WHERE incident_id LIKE ? ORDER BY simulation_time LIMIT 5", (f"%{iid}%",))
            dids = [d["decision_id"] for d in decs]
            q = ",".join("?" * len(dids)) or "''"
            return dict(incident=inc[0],
                        vision_raw=ops.query("SELECT simulation_time, received_sim_s, camera_id, unit_seq, frame_hash, model_name, model_version, labels, score, image_uri, processing_ms, raw_json, source "
                                             "FROM vision_events WHERE simulation_time BETWEEN ? AND ? ORDER BY simulation_time DESC LIMIT 8", w),
                        qr=ops.query("SELECT * FROM qr_events WHERE simulation_time BETWEEN ? AND ? ORDER BY simulation_time DESC LIMIT 8", (t0 - 1800, t0 + 60)),
                        plc=ops.query("SELECT simulation_time, received_sim_s, line_id, plc_state, diag_ok, cycle_count, fault_code, current_a, voltage_v, load_kw FROM equipment_events "
                                      "WHERE simulation_time BETWEEN ? AND ? ORDER BY simulation_time DESC LIMIT 20", w),
                        network=ops.query("SELECT topic, sent_at, received_at, delay_ms, dropped, duplicate, out_of_order FROM network_events WHERE sent_at BETWEEN ? AND ? ORDER BY sent_at DESC LIMIT 20", w),
                        snapshots=[dict(d, input_features_json=json.loads(d["input_features_json"])) for d in decs],
                        jev_requests=ops.query(f"SELECT * FROM jev_requests WHERE decision_id IN ({q})", tuple(dids)),
                        jev_responses=ops.query(f"SELECT * FROM jev_responses WHERE request_id IN ({','.join('?' * len(dids)) or chr(39) * 2})", tuple(f"JREQ-{d}" for d in dids)),
                        policy=ops.query(f"SELECT * FROM policy_decisions WHERE decision_id IN ({q})", tuple(dids)),
                        commands=ops.query(f"SELECT * FROM command_execution WHERE decision_id IN ({q})", tuple(dids)))

    def units(self, limit: int = 60, researcher: bool = False) -> dict:
        """Per-product inspection chain: image → YOLO result → judge (Jev / mock) → disposition → re-inspection.
        The true condition is attached only in researcher mode (evaluation)."""
        with self.lock:
            rows = self.E["ops"].query("SELECT * FROM unit_inspections ORDER BY simulation_time DESC LIMIT ?", (limit,))
            vis = {(r["line_id"], r["unit_seq"]): r for r in self.E["ops"].query(
                f"SELECT line_id, unit_seq, raw_json, source FROM vision_events WHERE unit_seq IN ({','.join(str(int(r['unit_seq'])) for r in rows) or '-1'})")}
            truth = {}
            if researcher:
                truth = {(t["line_id"], t["unit_seq"]): t for t in self.E["truth"].query(
                    f"SELECT line_id, unit_seq, truly_defective, defect_type FROM truth_units WHERE unit_seq IN ({','.join(str(int(r['unit_seq'])) for r in rows) or '-1'})")}
            out = []
            for r in rows:
                v = vis.get((r["line_id"], r["unit_seq"])) or {}
                x = dict(r, probabilities=json.loads(r["probabilities"]) if r.get("probabilities") else None, state_sent=json.loads(r["state_sent"]) if r.get("state_sent") else None,
                         raw_json=json.loads(v["raw_json"]) if v.get("raw_json") else None, camera_source=v.get("source"))
                if researcher and (r["line_id"], r["unit_seq"]) in truth:
                    t = truth[(r["line_id"], r["unit_seq"])]
                    x["truth_defective"] = bool(t["truly_defective"])
                    x["truth_defect_type"] = t["defect_type"]
                    fin = r["final_disposition"]
                    x["final_correct"] = None if fin is None else ((fin == "REJECT") == bool(t["truly_defective"]))
                    x["yolo_correct"] = (r["yolo_verdict"] == "DEFECT") == bool(t["truly_defective"])
                out.append(x)
            agg = self.E["ops"].query("SELECT judge_provider, disposition, COUNT(*) n, AVG(judge_latency_ms) lat, SUM(judge_cached) cached FROM unit_inspections GROUP BY judge_provider, disposition")
            return dict(units=out, summary=agg, judge=self.args.get("unit_judge"), camera_mode=self.args.get("camera_mode"), defect_image_ratio=self.args.get("defect_image_ratio"))

    def floor(self) -> dict:
        """Visual state for the floor plan: line states and kW, feeder load/limit, carrier positions along their routes."""
        with self.lock:
            p, fleet = self.E["plant"], self.E["fleet"]
            now = p.env.now
            cars = []
            for c in fleet.carriers:
                pos = None
                if c.trip:
                    prog = max(0.0, min(1.0, (now - c.trip["t0"]) / max(1e-6, c.trip["leg"])))
                    pos = dict(line=c.trip["line"], phase=c.trip["phase"], progress=round(prog, 3), loaded=c.trip["loaded"])
                cars.append(dict(id=c.cid, type=c.ctype, busy=c.busy, charging=c.charging, available=fleet.available(c), battery=round(c.battery), pos=pos))
            return dict(t=now, lines={r: dict(state=l.state, kw=round(l.kw(), 1), run_kw=l.spec.run_kw, outlet=len(l.outlet), wip=len(l.current.units)) for r, l in p.lines.items()},
                        feeder=dict(load_kw=round(p.load_kw(), 1), available_kw=p.available_kw(), nameplate_kw=p.f.power.nameplate_limit_kw, aux_kw=p.f.power.aux_kw),
                        carriers=cars, delivered=fleet.delivered_pallets)

    def transport(self) -> dict:
        with self.lock:
            E = self.E
            ops = E["ops"]
            fleet = [dict(carrier_id=c.cid, type=c.ctype, busy=c.busy, battery=round(c.battery, 1), charging=c.charging, available=E["fleet"].available(c)) for c in E["fleet"].carriers]
            return dict(orders=ops.query("SELECT order_id, carrier, carrier_id, payload_id, qty, state, issued_at, idempotency_key, attempt, dispatch_mode, reason, prerequisites FROM transport_orders ORDER BY issued_at DESC LIMIT 120"),
                        statuses=ops.query("SELECT order_id, state, seq, received_sim_s, carrier_id, note FROM transport_status ORDER BY received_sim_s DESC LIMIT 120"),
                        fleet_truth_view=fleet, dispatcher=E["disp"].summary(), fleet_stats=E["fleet"].stats,
                        outlets={r: len(l.outlet) for r, l in E["plant"].lines.items()})

    def truth_view(self) -> dict:
        """Researcher mode only: injected faults with true repair times and the safety-oracle events."""
        with self.lock:
            t = self.E["truth"]
            return dict(ground_truth=t.query("SELECT * FROM ground_truth"), unsafe_events=t.query("SELECT * FROM unsafe_events ORDER BY simulation_time"),
                        warning="evaluator data — never shown to the decision layer")
