"""DecisionSnapshotBuilder — reads ONLY the operational DB, and only rows with received_sim_s <= now (no truth, no
future). Computes, deterministically, everything the decision layer needs: plan progress, gap, required rate,
cycle-time / yield estimates, recovery ETA (fault catalogue), per-action forecasts (good units at shift end, shortfall,
energy, peak kW), CP-SAT upper bound of achievable good units, feasible actions with the constraint that removed each
infeasible one, detector incidents, and evidence-quality flags.

Arm switches:
* validated — reconcile counts with the camera stream, check freshness and cross-source consistency (R3+)
* context   — use live feeder headroom and OT/MQTT network statistics (R4+); otherwise the static nameplate limit
* detector  — "rule" (fixed thresholds, R0) or "ml" (EWMA / short-window rates + plan-risk forecast, R1+)
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from collections import deque

from .config import FAULT_CATALOGUE, STABILIZE_S, Action, FactoryV3
from .db import OpsDB

PRODUCING = ("RUNNING", "STARTING", "BLOCKED")
DOWN = ("FAULT", "MAINT", "REPAIR")
VISION_RECALL_MEASURED = None  # filled lazily from results/vision/summary.json (measured on held-out MVTec AD)
FIX_EST_S = 1200.0             # maintenance catalogue estimate for tool-change / quality fix
AMR_OUTAGE_ASSUMED_S = 1800.0  # planning assumption for an unexplained AMR outage


def _vision_recall() -> float:
    global VISION_RECALL_MEASURED
    if VISION_RECALL_MEASURED is None:
        try:
            from ..adapters.factory_vision import measured_defect_recall
            VISION_RECALL_MEASURED = measured_defect_recall(0.75)
        except Exception:  # noqa: BLE001
            VISION_RECALL_MEASURED = 0.75
    return VISION_RECALL_MEASURED


class SnapshotBuilder:
    def __init__(self, ops: OpsDB, f: FactoryV3, validated: bool, context: bool, detector: str):
        self.ops, self.f, self.validated, self.context, self.detector = ops, f, validated, context, detector
        self.ewma: dict[str, float] = {}
        self.drift_ticks = 0
        self._bound_cache: dict = {}
        self._last_ct: dict = {}
        self.incoming_defect_rate: float | None = None   # known incoming-lot defect share (demo setting); quality alarms use the excess over it

    # ---------------------------------------------------------------------------------------------- raw reads
    # Incremental ingestion: each tick reads only rows that became visible since the previous tick
    # (last_rx < received_sim_s <= now) and folds them into per-line state, ordered by source time.
    def _ingest(self, now: float):
        if not hasattr(self, "_st"):
            self._st = dict(rx=-1.0, eq={}, cnt={}, vis={}, fleet={}, buf={}, el=None)
        st, lo = self._st, self._st["rx"]
        for r in self.ops.query("SELECT * FROM equipment_events WHERE received_sim_s > ? AND received_sim_s <= ? ORDER BY simulation_time", (lo, now)):
            e = st["eq"].setdefault(r["line_id"], dict(latest=None, prev_state=None, fault_at=None, ready_at=None, code=None, running_since=0.0, reset_at=-1.0))
            if e["latest"] is None or r["simulation_time"] >= e["latest"]["simulation_time"]:
                ps = e["latest"]["plc_state"] if e["latest"] else None
                if r["plc_state"] in ("FAULT", "MAINT") and ps not in ("FAULT", "MAINT"):
                    e["fault_at"] = r["simulation_time"]
                if r["plc_state"] == "READY" and ps != "READY":
                    e["ready_at"] = r["simulation_time"]
                    if ps in ("FAULT", "MAINT"):
                        e["reset_at"] = r["simulation_time"]      # repair / maintenance completed: restart quality windows
                if r["plc_state"] == "RUNNING" and ps != "RUNNING":
                    e["running_since"] = r["simulation_time"]
                if r["fault_code"]:
                    e["code"] = r["fault_code"]
                e["latest"] = r
        for r in self.ops.query("SELECT line_id, simulation_time, plc_counter_raw FROM production_counts WHERE received_sim_s > ? AND received_sim_s <= ?", (lo, now)):
            c = st["cnt"].setdefault(r["line_id"], dict(rows=[], fin_t=-1.0, fin_epoch=0, fin_prev=0, fin_resets=0))
            bisect.insort(c["rows"], (r["simulation_time"], r["plc_counter_raw"]))
        for r in self.ops.query("SELECT line_id, simulation_time, unit_seq, labels FROM vision_events WHERE received_sim_s > ? AND received_sim_s <= ? ORDER BY simulation_time", (lo, now)):
            v = st["vis"].setdefault(r["line_id"], dict(n=0, acc=0, recent=deque(maxlen=40), ts=deque(maxlen=21)))
            v["n"] += 1
            v["acc"] += r["labels"] == "[]"
            v["recent"].appendleft((r["simulation_time"], r["labels"] != "[]"))
            v["ts"].appendleft(r["simulation_time"])
        for r in self.ops.query("SELECT carrier_id, carrier_type, available, battery_pct, busy, received_sim_s FROM fleet_status WHERE received_sim_s > ? AND received_sim_s <= ? ORDER BY received_sim_s", (lo, now)):
            st["fleet"][r["carrier_id"]] = r
        # buffer rows carry no transport delay and can be written at the same instant AFTER this tick ran, so track the
        # newest row actually consumed instead of the tick time (otherwise same-instant rows would be skipped forever)
        for r in self.ops.query("SELECT station_id, pallets_waiting, simulation_time FROM inventory_buffers WHERE simulation_time > ? AND simulation_time <= ? ORDER BY simulation_time",
                                (st.get("buf_t", -1.0), now)):
            st["buf"][r["station_id"]] = r["pallets_waiting"] or 0
            st["buf_t"] = r["simulation_time"]
        for r in self.ops.query("SELECT * FROM electrical_events WHERE received_sim_s > ? AND received_sim_s <= ? ORDER BY simulation_time", (lo, now)):
            if st["el"] is None or r["simulation_time"] >= st["el"]["simulation_time"]:
                st["el"] = r
        st["rx"] = now

    def _line_obs(self, line_id: str, now: float) -> dict:
        e = self._st["eq"].get(line_id)
        if e is None or e["latest"] is None:
            return dict(plc_state="UNKNOWN", diag_ok=0, fault_code=None, age_s=None, load_kw=None, stale=True, fault_observed_at=None, ready_observed_at=None, last_fault_code=None)
        r = e["latest"]
        age = now - r["simulation_time"]
        return dict(plc_state=r["plc_state"], diag_ok=r["diag_ok"], fault_code=r["fault_code"], age_s=round(age, 1), load_kw=r["load_kw"], current_a=r["current_a"],
                    voltage_v=r["voltage_v"], stale=age > self.f.network.stale_after_s, fault_observed_at=e["fault_at"], ready_observed_at=e["ready_at"], last_fault_code=e["code"])

    def _counts(self, line_id: str, now: float) -> dict:
        c = self._st["cnt"].get(line_id) or dict(rows=[], fin_t=-1.0, fin_epoch=0, fin_prev=0, fin_resets=0)
        rows = c["rows"]
        # rows older than now-120 s are final (late arrivals are bounded by the network delay); fold them once
        i0 = bisect.bisect_right(rows, (c["fin_t"], float("inf")))
        cut = bisect.bisect_right(rows, (now - 120, float("inf")))
        for t, v in rows[i0:cut]:
            if v < c["fin_prev"]:
                c["fin_resets"] += 1
                c["fin_epoch"] += c["fin_prev"]
            c["fin_prev"] = v
            c["fin_t"] = t
        epoch, prev, resets = c["fin_epoch"], c["fin_prev"], c["fin_resets"]
        for t, v in rows[max(cut, i0):]:
            if v < prev:
                resets += 1
                epoch += prev
            prev = v
        raw_last = rows[-1][1] if rows else 0
        v = self._st["vis"].get(line_id) or dict(n=0, acc=0, recent=deque(), ts=deque())
        e = self._st["eq"].get(line_id) or dict(running_since=0.0, reset_at=-1.0)
        recent = [rej for t, rej in v["recent"] if t > e["reset_at"]]
        rej40 = sum(recent) / len(recent) if recent else 0.0
        rej20 = sum(recent[:20]) / min(20, len(recent)) if recent else 0.0
        w0 = bisect.bisect_left(rows, (max(now - 600, e["running_since"]), -1))   # only since the line last started running
        win = rows[w0:]
        ct_naive = None
        if len(win) >= 2 and win[-1][0] - win[0][0] >= 300 and win[-1][1] - win[0][1] >= 8:   # ≥5 min and ≥8 counts, else unknown
            ct_naive = (win[-1][0] - win[0][0]) / (win[-1][1] - win[0][1])
        ts = [t for t in v["ts"] if t >= e["running_since"]]
        ct_vis = statistics.median([a - b for a, b in zip(ts[:-1], ts[1:])]) if len(ts) >= 11 and ts[0] >= now - 900 else None
        rejects_total = v["n"] - v["acc"]
        reconciled_raw = epoch + raw_last
        return dict(raw_counter=raw_last, resets=resets, naive_good=max(0, raw_last - rejects_total), reconciled_raw=reconciled_raw,
                    camera_units=v["n"], camera_accepted=v["acc"], n_recent=len(recent), reject_rate_40=rej40, reject_rate_20=rej20, ct_naive=ct_naive, ct_camera=ct_vis,
                    count_gap=reconciled_raw - v["n"])

    # ---------------------------------------------------------------------------------------------- snapshot
    def build(self, now: float, routing: dict, use_reserve: bool = False) -> dict:
        f = self.f
        main_l, sub_l = f.line("main"), f.line("sub")
        rem = max(0.0, f.plan.shift_s - now)
        self._ingest(now)
        obs = {r: self._line_obs(f.line(r).line_id, now) for r in ("main", "sub")}
        cnt = {r: self._counts(f.line(r).line_id, now) for r in ("main", "sub")}
        good = sum((c["camera_accepted"] if self.validated else c["naive_good"]) for c in cnt.values())
        ct_main = (cnt["main"]["ct_camera"] if self.validated else cnt["main"]["ct_naive"]) or main_l.ct_s
        if obs["main"]["plc_state"] != "RUNNING":       # blocked / starting / down: no fresh cycle-time evidence
            ct_main = self._last_ct.get("main", main_l.ct_s) if obs["main"]["plc_state"] in PRODUCING else max(ct_main, main_l.ct_s)
        else:
            self._last_ct["main"] = ct_main
        y_main = 1 - cnt["main"]["reject_rate_40"] if cnt["main"]["camera_units"] >= 20 else main_l.yield_rate
        # recovery ETA from the maintenance catalogue (estimate, never the injected truth)
        eta = {}
        for r in ("main", "sub"):
            o = obs[r]
            if o["plc_state"] in DOWN:
                code = o["fault_code"] or o["last_fault_code"]
                cat = FAULT_CATALOGUE[code].eta_catalogue_s if code in FAULT_CATALOGUE else FIX_EST_S
                elapsed = now - (o["fault_observed_at"] or now)
                eta[r] = max(0.0, cat - elapsed) + STABILIZE_S
            elif o["plc_state"] == "READY" and not o["diag_ok"]:
                eta[r] = max(0.0, STABILIZE_S - (now - (o["ready_observed_at"] or now)))
            else:
                eta[r] = 0.0
        # electrical / network context
        el = self._st["el"]
        limit = (el["available_kw"] if (self.context and el) else f.power.nameplate_limit_kw)
        net = self._network(now) if self.context else None
        fleet = list(self._st["fleet"].values())
        amr_av = sum(1 for r in fleet if r["carrier_type"] == "AMR" and r["available"])
        res_av = sum(1 for r in fleet if r["carrier_type"] == "AMR_RESERVE" and r["available"])
        if amr_av == 0 and fleet:
            self._amr_down_since = getattr(self, "_amr_down_since", None) or now
        else:
            self._amr_down_since = None
        amr_down_for = (now - self._amr_down_since) if getattr(self, "_amr_down_since", None) is not None else 0.0
        waiting = sum(self._st["buf"].values())
        evidence = self._evidence(now, obs, cnt) if self.validated else None
        feat = dict(now=now, rem_s=rem, target=f.plan.target_good_qty, good_so_far=good, gap=f.plan.target_good_qty - good,
                    required_rate_per_h=max(0, f.plan.target_good_qty - good) / max(1e-6, rem / 3600), ct_main_est=ct_main, ct_main_nominal=main_l.ct_s,
                    ct_sub_nominal=sub_l.ct_s, yield_main_est=y_main, yield_sub_nominal=sub_l.yield_rate, obs=obs, counts=cnt, eta=eta, limit_kw=limit,
                    limit_source="live feeder telemetry" if self.context else "static nameplate", electrical=el if self.context else None, network=net,
                    amr_available=amr_av, amr_down_for_s=amr_down_for, reserve_amr_available=res_av, pallets_waiting=waiting, use_reserve=use_reserve, evidence=evidence, routing=dict(routing))
        feat["incidents"] = self.detect(feat)
        feat["forecast"] = {a.value: self.forecast(feat, a) for a in Action}
        feat["feasible"], feat["infeasible"] = self.feasible(feat)
        feat["max_achievable_good"] = self.cpsat_bound(feat)
        if self.detector == "ml":  # plan-risk detection from the program forecast (R1+); the rule baseline is plan-unaware
            cur = "CONTINUE_MAIN" if obs["main"]["plc_state"] in PRODUCING else "ESCALATE_HUMAN"
            if feat["forecast"][cur]["projected_shortfall"] > 0.02 * f.plan.target_good_qty:
                infeasible = feat["max_achievable_good"] is not None and feat["max_achievable_good"] < f.plan.target_good_qty
                feat["incidents"].append(dict(cause="PLAN", line="plan", signal=f"shortfall={feat['forecast'][cur]['projected_shortfall']:.0f}"
                                              f"{' bound=' + str(feat['max_achievable_good']) if infeasible else ''}", detector="FORECAST", plan_infeasible=infeasible))
        feat["hash"] = hashlib.sha256(json.dumps({k: v for k, v in feat.items() if k != "hash"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return feat

    def _network(self, now: float) -> dict:
        rows = self.ops.query("SELECT delay_ms, dropped, duplicate, topic FROM network_events WHERE sent_at <= ? AND sent_at >= ? ORDER BY sent_at DESC LIMIT 400", (now, now - 300))
        plc = sorted(r["delay_ms"] for r in rows if r["topic"].startswith("plc/") and r["delay_ms"] is not None)
        mq = sorted(r["delay_ms"] for r in rows if r["topic"].startswith("sfaios/") and r["delay_ms"] is not None)
        n = len(rows) or 1
        return dict(plc_p95_ms=plc[int(0.95 * (len(plc) - 1))] if plc else None, mqtt_p95_ms=mq[int(0.95 * (len(mq) - 1))] if mq else None,
                    loss_rate=sum(r["dropped"] for r in rows) / n, dup_rate=sum(r["duplicate"] for r in rows) / n)

    def _evidence(self, now: float, obs: dict, cnt: dict) -> dict:
        stale = [r for r in ("main", "sub") if obs[r]["stale"] and obs[r]["plc_state"] != "UNKNOWN"]
        qr_bad = self.ops.one("SELECT COUNT(*) FROM qr_events WHERE received_sim_s <= ? AND received_sim_s >= ? AND (decode_quality!='OK' OR actual_location!=expected_location OR product_id!=?)",
                              (now, now - 1800, self.f.plan.product_id)) or 0
        gaps = {r: cnt[r]["count_gap"] for r in ("main", "sub")}
        return dict(stale_lines=stale, qr_mismatch_30min=qr_bad, plc_vs_camera_gap=gaps, counter_resets=sum(c["resets"] for c in cnt.values()),
                    count_mismatch=any(abs(g) > 3 for g in gaps.values()) or any(c["resets"] for c in cnt.values()))

    # ---------------------------------------------------------------------------------------------- detection
    def detect(self, ft: dict) -> list[dict]:
        inc = []
        o, c = ft["obs"], ft["counts"]
        for r in ("main", "sub"):
            if o[r]["plc_state"] in DOWN or (o[r]["plc_state"] == "READY" and not o[r]["diag_ok"]):
                inc.append(dict(cause="EQUIPMENT", line=r, signal=f"plc_state={o[r]['plc_state']} code={o[r]['fault_code'] or o[r]['last_fault_code']}", detector="PLC_FAULT_CODE"))
        ratio = (ft["ct_main_est"] / ft["ct_main_nominal"]) if o["main"]["plc_state"] == "RUNNING" else 1.0
        if self.detector == "rule":
            if ratio > 1.30:
                inc.append(dict(cause="PROCESS_DRIFT", line="main", signal=f"ct_ratio={ratio:.2f}", detector="RULE_CT>1.30"))
            base = 0.0 if self.incoming_defect_rate is None else self.incoming_defect_rate * _vision_recall()
            if c["main"]["n_recent"] >= 40 and c["main"]["reject_rate_40"] > 0.15 + base and o["main"]["plc_state"] in PRODUCING:
                inc.append(dict(cause="QUALITY", line="main", signal=f"reject40={c['main']['reject_rate_40']:.2f}", detector="RULE_REJECT>15%"))
        else:
            reset = (self._st["eq"].get(self.f.line("main").line_id) or {}).get("reset_at", -1.0)
            if reset != self.ewma.get("reset_at"):           # repair / maintenance completed: forget pre-maintenance drift evidence
                self.ewma.update(ct=1.0, reset_at=reset)
                self.drift_ticks = 0
            if o["main"]["plc_state"] == "RUNNING":
                e = 0.5 * self.ewma.get("ct", 1.0) + 0.5 * ratio
                self.ewma["ct"] = e
                self.drift_ticks = self.drift_ticks + 1 if e > 1.10 else 0
            if self.drift_ticks >= 3 and o["main"]["plc_state"] == "RUNNING":
                inc.append(dict(cause="PROCESS_DRIFT", line="main", signal=f"ewma_ct_ratio={e:.2f}", detector="ML_EWMA_CT"))
            # binomial false-alarm control at the nominal reject rate (~2-3 %): ≥5/40 and ≥3/20 rejects
            base = 0.0 if self.incoming_defect_rate is None else self.incoming_defect_rate * _vision_recall()
            if c["main"]["n_recent"] >= 20 and c["main"]["reject_rate_20"] >= 0.15 + base and c["main"]["reject_rate_40"] >= 0.125 + base and o["main"]["plc_state"] in PRODUCING:
                inc.append(dict(cause="QUALITY", line="main", signal=f"reject20={c['main']['reject_rate_20']:.2f}", detector="ML_REJECT_RATE"))
        if ft["amr_available"] == 0 and ft["pallets_waiting"] >= 1:
            inc.append(dict(cause="TRANSPORT", line="fleet", signal="amr_available=0", detector="FLEET_STATUS"))
        if self.context and ft["limit_kw"] < self.f.power.nameplate_limit_kw:
            inc.append(dict(cause="POWER", line="feeder", signal=f"available_kw={ft['limit_kw']}", detector="FEEDER_TELEMETRY"))
        if self.context and ft["network"] and (ft["network"]["plc_p95_ms"] or 0) > 2000:
            inc.append(dict(cause="NETWORK", line="ot", signal=f"plc_p95_ms={ft['network']['plc_p95_ms']}", detector="NET_LATENCY"))
        ev = ft["evidence"]
        if ev:
            if ev["stale_lines"]:
                inc.append(dict(cause="NETWORK", line=",".join(ev["stale_lines"]), signal="stale PLC samples", detector="FRESHNESS"))
            if ev["count_mismatch"]:
                inc.append(dict(cause="COUNTER", line="main", signal=f"plc_vs_camera_gap={ev['plc_vs_camera_gap']} resets={ev['counter_resets']}", detector="COUNT_RECONCILE"))
            if ev["qr_mismatch_30min"]:
                inc.append(dict(cause="VISION_QR", line="qr", signal=f"qr_mismatch_30min={ev['qr_mismatch_30min']}", detector="QR_CHECK"))
        return inc

    # ---------------------------------------------------------------------------------------------- forecasting
    def _main_back_s(self, ft) -> float | None:
        st = ft["obs"]["main"]["plc_state"]
        if st in PRODUCING:
            return 0.0
        if st in DOWN or st == "READY":
            return ft["eta"]["main"] + self.f.line("main").startup_s
        if st in ("IDLE", "OFF"):
            return self.f.line("main").startup_s
        return None

    def forecast(self, ft: dict, a: Action) -> dict:
        """Program-computed outcome of one action over the rest of the shift (single-decision, myopic horizon)."""
        f, rem = self.f, ft["rem_s"]
        ml, sl = f.line("main"), f.line("sub")
        o = ft["obs"]
        main_prod = o["main"]["plc_state"] in PRODUCING
        sub_prod = o["sub"]["plc_state"] in PRODUCING
        sub_ok = o["sub"]["plc_state"] in ("OFF", "IDLE", "READY") and not (o["sub"]["plc_state"] == "READY" and not o["sub"]["diag_ok"])
        drift_or_q = any(i["cause"] in ("PROCESS_DRIFT", "QUALITY") for i in ft["incidents"])
        main_rate_now = 3600 / ft["ct_main_est"] * ft["yield_main_est"]
        main_rate_nom = 3600 / ml.ct_s * ml.yield_rate
        sub_rate = 3600 / sl.ct_s * sl.yield_rate
        main_t = sub_t = 0.0          # productive seconds
        main_rate, defect_units, startup_s = main_rate_now, 0.0, 0.0
        back = self._main_back_s(ft)
        if a in (Action.CONTINUE_MAIN, Action.REPLAN, Action.ESCALATE_HUMAN, Action.REQUEST_TRANSPORT):
            main_t = rem if main_prod else 0.0
            sub_t = rem if sub_prod else 0.0
        elif a == Action.RECOVER_MAIN:
            if main_prod and drift_or_q:
                startup_s = FIX_EST_S + STABILIZE_S + ml.startup_s
                main_t, main_rate = max(0.0, rem - startup_s), main_rate_nom
            elif back is not None:
                main_t = max(0.0, rem - back)
                main_rate = main_rate_nom if not main_prod else main_rate_now
                sub_t = min(rem, back) if sub_prod else 0.0
        elif a == Action.SWITCH_TO_SUB:
            if sub_ok or sub_prod:
                startup_s = 0.0 if sub_prod else sl.startup_s
                sub_t = max(0.0, rem - startup_s)
        elif a == Action.RUN_PARALLEL:
            if back is not None:
                main_t = max(0.0, rem - back)
                main_rate = main_rate_now if main_prod else main_rate_nom
            if sub_ok or sub_prod:
                sub_t = max(0.0, rem - (0.0 if sub_prod else sl.startup_s))
        elif a == Action.SAFE_STOP_REQUEST:
            sub_t = rem if sub_prod else 0.0
        if ft["amr_available"] == 0 and not ft["use_reserve"] and a != Action.REQUEST_TRANSPORT and (main_t + sub_t) > 0:
            # assumption (documented): an AMR outage lasts at least AMR_OUTAGE_ASSUMED_S more, or as long again as it has already lasted;
            # output beyond the outlet space blocks the lines meanwhile
            space = (f.fleet.out_buffer_pallets - min(f.fleet.out_buffer_pallets, ft["pallets_waiting"])) * f.fleet.pallet_qty
            rate = (main_rate * (main_t > 0) + sub_rate * (sub_t > 0)) / 3600
            fill_s = space / rate if rate > 0 else rem
            lost = max(0.0, min(rem, max(AMR_OUTAGE_ASSUMED_S, ft.get("amr_down_for_s", 0.0))) - fill_s)
            main_t, sub_t = max(0.0, main_t - lost), max(0.0, sub_t - lost)
        good_main = main_rate * main_t / 3600
        good_sub = sub_rate * sub_t / 3600
        units_main = good_main / max(0.01, ft["yield_main_est"] if main_rate == main_rate_now else ml.yield_rate)
        defect_units = units_main - good_main + good_sub / sl.yield_rate * (1 - sl.yield_rate)
        defects_shipped = defect_units * (1 - _vision_recall())   # camera misses at the measured held-out recall
        kwh = (ml.run_kw * main_t + ml.idle_kw * max(0.0, rem - main_t) + (sl.run_kw * sub_t + (sl.idle_kw if sub_t else sl.off_kw) * max(0.0, rem - sub_t)) + f.power.aux_kw * rem) / 3600
        # peak load over the action's phases: RECOVER_MAIN runs the sub only until main restarts (sequential), others concurrently
        if a == Action.RECOVER_MAIN and sub_t > 0 and main_t > 0:
            peak = f.power.aux_kw + max(ml.idle_kw + sl.run_kw, ml.run_kw + sl.off_kw)
        else:
            peak = f.power.aux_kw + (ml.run_kw if main_t > 0 else ml.idle_kw) + (sl.run_kw if sub_t > 0 else sl.off_kw)
        end_good = ft["good_so_far"] + good_main + good_sub
        return dict(projected_good_end=round(end_good, 1), projected_shortfall=round(max(0.0, ft["target"] - end_good), 1), projected_defects_shipped=round(defects_shipped, 1),
                    energy_kwh=round(kwh, 2), peak_kw=round(peak, 1), main_productive_s=round(main_t), sub_productive_s=round(sub_t), startup_s=round(startup_s))

    def feasible(self, ft: dict) -> tuple[list[str], dict]:
        o = ft["obs"]
        f = self.f
        ml, sl = f.line("main"), f.line("sub")
        main_st, sub_st = o["main"]["plc_state"], o["sub"]["plc_state"]
        main_prod, sub_prod = main_st in PRODUCING, sub_st in PRODUCING
        sub_avail = (sub_st in ("OFF", "IDLE") or (sub_st == "READY" and o["sub"]["diag_ok"])) and sl.material_ready
        main_issue = main_st not in PRODUCING or any(i["cause"] in ("PROCESS_DRIFT", "QUALITY", "EQUIPMENT") and i["line"] == "main" for i in ft["incidents"])
        both_down = main_st in DOWN and sub_st in DOWN
        lim, aux = ft["limit_kw"], f.power.aux_kw
        bad: dict[str, str] = {}
        ok: list[str] = []

        def need(a: Action, cond: bool, why: str):
            if cond:
                ok.append(a.value)
            else:
                bad[a.value] = why
        need(Action.CONTINUE_MAIN, main_prod, "main line is not producing")
        need(Action.RECOVER_MAIN, not both_down and (not main_prod or any(i["cause"] in ("PROCESS_DRIFT", "QUALITY") for i in ft["incidents"])) and main_st != "UNKNOWN",
             "no main-line recovery needed" if main_prod else "both lines down / main state unknown")
        sub_kw = aux + sl.run_kw + (ml.idle_kw if not main_prod else ml.idle_kw)
        need(Action.SWITCH_TO_SUB, not both_down and sub_avail and not sub_prod and main_issue and sub_kw <= lim,
             "sub not available" if not sub_avail else ("sub already running" if sub_prod else ("no main issue" if not main_issue else f"power {sub_kw:.0f} kW > limit {lim:.0f} kW")))
        par_kw = aux + ml.run_kw + sl.run_kw
        need(Action.RUN_PARALLEL, not both_down and (sub_avail or sub_prod) and main_st not in ("UNKNOWN",) and not (main_prod and sub_prod) and par_kw <= lim,
             f"power {par_kw:.0f} kW > limit {lim:.0f} kW" if par_kw > lim else "sub unavailable / already parallel")
        best_motion = min(ft["forecast"][a]["projected_shortfall"] for a in ("CONTINUE_MAIN", "RECOVER_MAIN", "SWITCH_TO_SUB", "RUN_PARALLEL"))
        need(Action.REPLAN, best_motion > 0, "plan reachable")
        need(Action.REQUEST_TRANSPORT, ft["amr_available"] == 0 and ft["reserve_amr_available"] >= 1 and ft["pallets_waiting"] >= 1 and not ft["use_reserve"], "no transport shortage")
        need(Action.SAFE_STOP_REQUEST, main_prod and any(i["cause"] == "QUALITY" for i in ft["incidents"]), "no quality incident on a producing main line")
        ok.append(Action.ESCALATE_HUMAN.value)
        if self.validated and ft["evidence"]:
            ev = ft["evidence"]
            for a in (Action.RECOVER_MAIN, Action.SWITCH_TO_SUB, Action.RUN_PARALLEL):
                lines = {"RECOVER_MAIN": ["main"], "SWITCH_TO_SUB": ["sub"], "RUN_PARALLEL": ["main", "sub"]}[a.value]
                if a.value in ok and any(l in ev["stale_lines"] for l in lines):
                    ok.remove(a.value)
                    bad[a.value] = "evidence stale for the target line (freshness check)"
        return ok, bad

    def cpsat_bound(self, ft: dict) -> float | None:
        """Upper bound of good units achievable by shift end over main/sub on/off schedules in 10-min slots subject to
        availability (ETA estimate), startup and the power limit — solved with OR-Tools CP-SAT. Cached per bucket."""
        key = (round(ft["rem_s"] / 600), round(ft["good_so_far"] / 10), round(ft["eta"]["main"] / 300), ft["obs"]["sub"]["plc_state"], ft["obs"]["main"]["plc_state"],
               round(ft["limit_kw"]), round(ft["ct_main_est"]))
        if key in self._bound_cache:
            return self._bound_cache[key]
        try:
            from ortools.sat.python import cp_model
        except Exception:  # noqa: BLE001
            return None
        f, slot = self.f, 600
        ml, sl = f.line("main"), f.line("sub")
        n = int(math.ceil(ft["rem_s"] / slot))
        if n <= 0:
            return float(ft["good_so_far"])
        o = ft["obs"]
        back = self._main_back_s(ft)
        sub_ok = o["sub"]["plc_state"] not in DOWN and sl.material_ready
        main_units = int(round(slot / ml.ct_s * ml.yield_rate * 10))     # ×10 integer scaling
        sub_units = int(round(slot / sl.ct_s * sl.yield_rate * 10))
        m = cp_model.CpModel()
        xm = [m.NewBoolVar(f"m{t}") for t in range(n)]
        xs = [m.NewBoolVar(f"s{t}") for t in range(n)]
        for t in range(n):
            if back is None or t * slot < back:
                m.Add(xm[t] == 0)
            if not sub_ok or (o["sub"]["plc_state"] not in PRODUCING and t * slot < sl.startup_s):
                m.Add(xs[t] == 0)
            if f.power.aux_kw + ml.run_kw + sl.run_kw > ft["limit_kw"]:
                m.Add(xm[t] + xs[t] <= 1)
        m.Maximize(sum(main_units * xm[t] + sub_units * xs[t] for t in range(n)))
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = 0.5
        s.parameters.num_workers = 1
        res = s.Solve(m)
        val = float(ft["good_so_far"]) + (s.ObjectiveValue() / 10 if res in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0.0)
        self._bound_cache[key] = round(val, 1)
        return round(val, 1)
