"""SimPy closed-loop plant for SF AI OS v3: main/sub lines, virtual PLC, camera gate replaying real YOLO JSON, QR/pallets,
feeder power, OT network emulation and a virtual MQTT AMR fleet.

Truth vs observation:
* The Plant owns the physical truth (line states, true repair times, which units are defective, true feeder limit).
  It writes evaluator rows to TruthStore and *observations* to OpsDB. Every observed row carries received_sim_s (send
  time + network delay); readers must filter on received_sim_s <= now. Lost messages never appear; duplicates appear twice.
* Commands arrive only through `command()` (virtual PLC) or the virtual MQTT broker. Nothing here talks to a network.

Common random numbers: per-unit randomness (cycle-time noise, defect truth, camera replay record) is derived from
(seed, line, unit index) so every arm sees the same unit stream; network randomness uses per-channel streams.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import simpy

from .config import FAULT_CATALOGUE, STABILIZE_S, FactoryV3, Fault, LineSpec
from .db import OpsDB, TruthStore

REPLAY_LOCAL = Path("results/vision/replay_raw.jsonl")          # written by `make factory-vision`
REPLAY_PUBLISHED = Path("data/results/vision/replay_raw.jsonl")  # the same records, shipped with the repository
REPLAY_PATH = REPLAY_LOCAL if REPLAY_LOCAL.exists() else REPLAY_PUBLISHED
CREW_RESPONSE_S = 60.0
VOLT_NOM = 400.0


def _urand(*key) -> random.Random:
    return random.Random(hashlib.blake2b("|".join(map(str, key)).encode(), digest_size=8).hexdigest())


# ------------------------------------------------------------------------------------------------ YOLO replay pool
class ReplayPool:
    """Verbatim YOLO11n outputs on held-out MVTec AD images (REPLAY_PATH). If the file is missing a
    synthetic generator is used and every row is labelled source='synthetic' (never presented as model output)."""
    _cache: dict | None = None

    def __init__(self, path: Path = REPLAY_PATH):
        if ReplayPool._cache is None:
            good, bad = [], []
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    r = json.loads(line)
                    (bad if r["eval_truth"]["truth_defect"] else good).append(r)
            ReplayPool._cache = dict(good=good, bad=bad)
        self.good, self.bad = ReplayPool._cache["good"], ReplayPool._cache["bad"]
        self.real = bool(self.good and self.bad)
        self.source = "replay:MVTEC_AD/YOLO11n" if self.real else "synthetic"

    def pick(self, defective: bool, rng: random.Random) -> dict:
        if self.real:
            return rng.choice(self.bad if defective else self.good)
        det = [dict(name="defect", **{"class": 0}, confidence=round(0.4 + 0.5 * rng.random(), 5), box=dict(x1=100.0, y1=100.0, x2=180.0, y2=170.0))] if (defective and rng.random() < 0.75) else []
        raw = dict(names={"0": "defect"}, detections=det, speed_ms=dict(inference=3.0), orig_shape=[700, 700], model="SYNTHETIC")
        return dict(image=f"synthetic_{rng.randrange(10**6)}", frame_hash=hashlib.sha256(json.dumps(raw).encode()).hexdigest(), raw_json=raw, category="synthetic",
                    eval_truth=dict(truth_defect=defective))


# ------------------------------------------------------------------------------------------------ network emulator
class Channel:
    """Delay/jitter/loss/duplication/reordering per channel; fault windows override the base spec."""

    def __init__(self, name: str, base: dict, faults: list[Fault], seed: int):
        self.name, self.base = name, base
        self.faults = [f for f in faults if f.kind == "NETWORK" and f.target == name]
        self.rng = _urand(seed, "net", name)
        self.n = 0

    def params(self, t: float) -> dict:
        p = dict(self.base)
        for f in self.faults:
            if f.at_s <= t < f.at_s + f.duration_s:
                p.update({k: v for k, v in f.params.items()})
        return p

    def transmit(self, t: float) -> list[tuple[float, bool]]:
        """Returns a list of (delay_s, duplicate_flag); empty list = dropped."""
        p = self.params(t)
        self.n += 1
        if self.rng.random() < p.get("loss_p", 0.0):
            return []
        d = max(0.005, self.rng.gauss(p.get("delay_ms", 50.0), p.get("jitter_ms", 10.0)) / 1000.0)
        if self.rng.random() < p.get("reorder_p", 0.0):
            d += self.rng.uniform(0.5, 3.0)
        out = [(d, False)]
        if self.rng.random() < p.get("dup_p", 0.0):
            out.append((d + self.rng.uniform(0.05, 2.0), True))
        return out


# ------------------------------------------------------------------------------------------------ line
@dataclass
class Pallet:
    pallet_id: str
    line_id: str
    product_id: str
    units: list = field(default_factory=list)       # (unit_seq, truly_defective)
    qr_product: str | None = None
    qr_location: str | None = None
    qr_ok: bool = True
    completed_at: float | None = None
    picked: bool = False


class Line:
    def __init__(self, plant: "Plant", spec: LineSpec):
        self.p, self.spec, self.env = plant, spec, plant.env
        self.state = "RUNNING" if spec.initially_on else "OFF"
        self.stable = True
        self.fault: dict | None = None
        self.counter_raw = 0
        self.unit_seq = 0
        self.ct_factor_base = 1.0
        self.maint_fixed = False
        self.defect_override: float | None = None
        self.pallet_n = 0
        self.current = Pallet(self._pid(), spec.line_id, plant.f.plan.product_id)
        self.outlet: list[Pallet] = []
        self.good_accepted = 0
        self.rejected = 0
        self.true_good_accepted = 0
        self.defects_shipped = 0
        self.processed = 0
        self.blocked_s = 0.0
        self.downtime_s = 0.0
        self.recur = 0
        self.proc = self.env.process(self._run())
        self.last_change = 0.0
        self.state_log: list[tuple[float, str]] = [(0.0, self.state)]

    def _pid(self) -> str:
        self.pallet_n += 1
        return f"PAL-{self.spec.line_id}-{self.pallet_n:04d}"

    # --- physics
    def kw(self) -> float:
        s = self.state
        if s in ("RUNNING", "STARTING"):
            return self.spec.run_kw
        if s in ("BLOCKED", "READY", "IDLE"):
            return self.spec.idle_kw
        if s in ("FAULT", "REPAIR", "MAINT", "TRIPPED"):
            return self.spec.idle_kw * 0.5
        return self.spec.off_kw

    def ct_factor(self) -> float:
        fac = self.ct_factor_base
        for f in self.p.faults:
            if f.kind == "CT_DRIFT" and f.target == self.spec.role.upper() and not self.maint_fixed and self.env.now >= f.at_s:
                ramp = max(1.0, f.params.get("ramp_s", 1.0))
                fac *= 1 + (f.params["factor"] - 1) * min(1.0, (self.env.now - f.at_s) / ramp)
        return fac

    def defect_p(self) -> float:
        base = 1 - self.spec.yield_rate if self.p.defect_image_ratio is None else self.p.defect_image_ratio
        if self.maint_fixed:
            return base
        for f in self.p.faults:
            if f.kind == "DEFECT_RISE" and f.target == self.spec.role.upper() and f.at_s <= self.env.now < f.at_s + f.duration_s:
                return f.params["defect_p"]
        return base

    def set_state(self, s: str):
        if s == self.state:
            return
        now = self.env.now
        if self.state in ("FAULT", "REPAIR", "MAINT", "TRIPPED", "READY") and self.spec.role == "main":
            self.downtime_s += now - self.last_change
        if self.state == "BLOCKED":
            self.blocked_s += now - self.last_change
        self.state, self.last_change = s, now
        self.state_log.append((now, s))
        if self.proc.is_alive and self.proc.target is not None and self.env.active_process is not self.proc:
            self.proc.interrupt("state")

    def _run(self):
        while True:
            try:
                if self.state == "RUNNING":
                    if len(self.outlet) >= self.p.f.fleet.out_buffer_pallets:
                        self.set_state("BLOCKED")
                        continue
                    rng = _urand(self.p.seed, self.spec.line_id, "ct", self.unit_seq + 1)
                    sigma = math.sqrt(math.log(1 + self.spec.ct_cv ** 2))
                    dt = self.spec.ct_s * self.p.f.ct_scale * self.ct_factor() * rng.lognormvariate(-sigma * sigma / 2, sigma)
                    yield self.env.timeout(dt)
                    self._finish_unit()
                else:
                    yield self.env.timeout(3600 * 24)
            except simpy.Interrupt:
                continue

    def _finish_unit(self):
        self.unit_seq += 1
        self.processed += 1
        u = self.unit_seq
        now = self.env.now
        rng = _urand(self.p.seed, self.spec.line_id, "unit", u)
        defective = rng.random() < self.defect_p()
        # PLC rising edge(s): counter anomaly may double-count; a reset zeroes the raw counter
        inc = 1
        for f in self.p.faults:
            if f.kind == "COUNTER_ANOMALY" and f.target == self.spec.role.upper() and f.at_s <= now < f.at_s + f.duration_s:
                if rng.random() < f.params.get("dup_edge_p", 0.0):
                    inc = 2
                rs = f.params.get("reset_at_s")
                if rs is not None and not getattr(self, "_reset_done", False) and now >= rs:
                    self.counter_raw = 0
                    self._reset_done = True
        self.counter_raw += inc
        # camera gate: an image of the part's hidden condition (held-out MVTec AD, re-used at random) analysed by YOLO11n
        rec = self.p.pool.pick(defective, rng)
        if self.p.camera_mode == "live" and self.p.pool.real:
            from .yolo_live import LiveYolo
            rec = dict(rec, raw_json=LiveYolo.get().infer(rec["image"]))
        img_id = str(rec["frame_hash"])[:16]
        raw = dict(rec["raw_json"])
        if "path" in raw:
            raw["path"] = f"img:{img_id}"   # the file name encodes the ground-truth label; replaced by an opaque id (only change to the raw JSON)
        dets = raw.get("detections") or []
        yolo_defect = len(dets) > 0
        miss = any(f.kind == "VISION_QR_MISMATCH" and f.at_s <= now < f.at_s + f.duration_s and rng.random() < f.params.get("vision_miss_p", 0) for f in self.p.faults)
        verdict = None
        if self.p.unit_judge is not None and not miss:
            verdict = self.p.judge_unit(self, u, rec, raw, img_id)
            disposition = verdict["disposition"]
        else:
            disposition = "REJECT" if yolo_defect else "ACCEPT"
        rejected = disposition != "ACCEPT"
        pallet_for_unit = None if rejected else self.current.pallet_id
        if not miss:
            delay = 0.05 + rng.random() * 0.15
            top = max(dets, key=lambda d: d["confidence"]) if dets else None
            self.p.ops.insert("vision_events", now, source=("live:YOLO11n/" + raw.get("device", "")) if self.p.camera_mode == "live" else self.p.pool.source,
                              provenance=dict(image_id=img_id, dataset="MVTEC_AD" if self.p.pool.real else None),
                              received_sim_s=now + delay, camera_id=f"CAM_{self.spec.line_id}", line_id=self.spec.line_id, unit_seq=u, frame_hash=rec["frame_hash"],
                              model_name=raw.get("model", "YOLO11n"), model_version=str(raw.get("weights_sha256", ""))[:16],
                              labels=[d["name"] for d in dets], bbox=(top or {}).get("box"), score=(top or {}).get("confidence", 0.0),
                              image_uri=f"img://{img_id}", processing_ms=(raw.get("speed_ms") or {}).get("inference"),
                              raw_json=json.dumps(raw, ensure_ascii=False, sort_keys=True), pallet_id=pallet_for_unit, ground_truth_ref=f"{self.spec.line_id}:{u}")
        self.p.truth.insert("truth_units", now, line_id=self.spec.line_id, unit_seq=u, finished_at=now, truly_defective=int(defective), accepted=int(not rejected),
                            pallet_id=pallet_for_unit, image_file=rec.get("image"), image_id=img_id, defect_type=(rec.get("eval_truth") or {}).get("defect_type"))
        if verdict is not None:
            self.p.ops.insert("unit_inspections", now, source=verdict["provider"], line_id=self.spec.line_id, unit_seq=u, image_id=img_id, part_type=rec.get("category"),
                              yolo_detections=len(dets), yolo_max_conf=max((d["confidence"] for d in dets), default=0.0), yolo_verdict="DEFECT" if yolo_defect else "OK",
                              yolo_ms=(raw.get("speed_ms") or {}).get("inference"), judge_provider=verdict["provider"], judge_model=verdict.get("model"),
                              disposition=disposition, probabilities=verdict.get("probabilities"), defect_present_p=verdict.get("defect_present_p"),
                              judge_latency_ms=verdict.get("latency_ms"), judge_cached=int(bool(verdict.get("cached"))), judge_error=verdict.get("error"),
                              request_hash=verdict.get("request_hash"), state_sent=verdict.get("state"), final_disposition=None if disposition == "REINSPECT" else disposition,
                              pallet_id=pallet_for_unit)
        if disposition == "REINSPECT":
            self.p.reinspections += 1
            self.env.process(self._reinspect(u, defective))
            return
        if rejected:
            self.rejected += 1
            return
        self.good_accepted += 1
        self.true_good_accepted += 0 if defective else 1
        self.defects_shipped += 1 if defective else 0
        self.current.units.append((u, defective))
        if len(self.current.units) >= self.p.f.fleet.pallet_qty:
            self._close_pallet()

    def _reinspect(self, u: int, defective: bool):
        """Manual re-inspection (simulated operator, 120 s): reveals the true condition of the held part."""
        yield self.env.timeout(120.0)
        final = "REJECT" if defective else "ACCEPT"
        pid = None
        if defective:
            self.rejected += 1
        else:
            pid = self.current.pallet_id
            self.good_accepted += 1
            self.true_good_accepted += 1
            self.current.units.append((u, False))
            if len(self.current.units) >= self.p.f.fleet.pallet_qty:
                self._close_pallet()
        self.p.ops.con.execute("UPDATE unit_inspections SET final_disposition=?, reinspect_result=?, pallet_id=? WHERE line_id=? AND unit_seq=?",
                               (final, "DEFECT_CONFIRMED" if defective else "GOOD_CONFIRMED", pid, self.spec.line_id, u))

    def _close_pallet(self):
        now = self.env.now
        pal = self.current
        pal.completed_at = now
        rng = _urand(self.p.seed, pal.pallet_id, "qr")
        pal.qr_product, pal.qr_location, pal.qr_ok = pal.product_id, f"OUTLET_{self.spec.line_id}", True
        for f in self.p.faults:
            if f.kind == "VISION_QR_MISMATCH" and f.at_s <= now < f.at_s + f.duration_s and rng.random() < f.params.get("qr_mismatch_p", 0):
                pal.qr_ok = False
                pal.qr_product = "P02" if rng.random() < 0.5 else pal.product_id
                pal.qr_location = "OUTLET_X" if pal.qr_product == pal.product_id else pal.qr_location
        self.p.scan_qr(pal, rng)
        self.outlet.append(pal)
        self.current = Pallet(self._pid(), self.spec.line_id, self.p.f.plan.product_id)

    # --- commands / faults
    def start(self, startup_s: float):
        if self.state in ("RUNNING", "STARTING", "BLOCKED"):
            return
        self.set_state("STARTING")
        self.env.process(self._startup(startup_s))

    def _startup(self, d):
        yield self.env.timeout(d)
        if self.state == "STARTING":
            self.set_state("RUNNING")
            if self.recur_pending():
                self.env.process(self._recur())

    def recur_pending(self) -> bool:
        return getattr(self, "_restart_unstable", False)

    def _recur(self):
        self._restart_unstable = False
        rng = _urand(self.p.seed, self.spec.line_id, "recur", self.recur)
        if rng.random() < self.p.f.recurrence_p_unstable:
            yield self.env.timeout(rng.uniform(60, 600))
            self.recur += 1
            code = (self.fault or {}).get("code", "E201")
            self.inject_fault(code, repair_s=FAULT_CATALOGUE[code].true_min_s * 0.5, recurrence=True)

    def stop(self, to="IDLE"):
        if self.state in ("RUNNING", "STARTING", "BLOCKED", "READY", "IDLE", "OFF"):
            self.set_state(to)

    def inject_fault(self, code: str, repair_s: float, recurrence: bool = False):
        self.fault = dict(code=code, at=self.env.now, repair_s=repair_s, recurrence=recurrence)
        self.stable = False
        self.set_state("FAULT")
        self.env.process(self._repair(repair_s))

    def _repair(self, repair_s):
        yield self.env.timeout(CREW_RESPONSE_S)
        if self.state == "FAULT":
            self.set_state("REPAIR")
        yield self.env.timeout(max(0.0, repair_s - CREW_RESPONSE_S))
        self.set_state("READY")
        self.p.truth.insert("truth_states", self.env.now, line_id=self.spec.line_id, state="REPAIRED", kw=self.kw(), available_kw=self.p.available_kw(), stable=0)
        yield self.env.timeout(STABILIZE_S)
        if self.state == "READY":
            self.stable = True
            self.fault = None

    def maintain(self, fix_s: float):
        """Controlled stop + maintenance that removes drift / quality causes; ends READY (stabilising)."""
        self.stable = False
        self.set_state("MAINT")
        self.env.process(self._maint(fix_s))

    def _maint(self, fix_s):
        yield self.env.timeout(fix_s)
        self.maint_fixed = True
        self.set_state("READY")
        yield self.env.timeout(STABILIZE_S)
        if self.state == "READY":
            self.stable = True

    def pick_pallet(self, pallet_id: str) -> Pallet | None:
        for i, pal in enumerate(self.outlet):
            if pal.pallet_id == pallet_id:
                self.outlet.pop(i)
                pal.picked = True
                if self.state == "BLOCKED":
                    self.set_state("RUNNING")
                return pal
        return None


# ------------------------------------------------------------------------------------------------ plant
class Plant:
    def __init__(self, f: FactoryV3, faults: list[Fault], seed: int, ops: OpsDB, truth: TruthStore, camera_mode: str = "replay"):
        self.f, self.faults, self.seed, self.ops, self.truth = f, faults, seed, ops, truth
        self.camera_mode = camera_mode   # "replay" (stored real outputs, batch experiments) | "live" (run YOLO11n per unit)
        self.unit_judge = None           # optional per-part judge (Jev / mock) — see unit_judge.py
        self.defect_image_ratio: float | None = None
        self.reinspections = 0
        self.env = simpy.Environment()
        self.pool = ReplayPool()
        n = f.network
        base = dict(delay_ms=n.median_delay_ms, jitter_ms=n.jitter_ms, loss_p=n.loss_p, dup_p=n.dup_p, reorder_p=n.reorder_p)
        self.ch = {k: Channel(k, base, faults, seed) for k in ("PLC", "MQTT")}
        self.lines = {l.role: Line(self, l) for l in f.lines}
        self.energy_kwh = 0.0
        self.amr_charge_kwh = 0.0
        self.line_kwh = 0.0
        self.max_kw = 0.0
        self.overloads: list[dict] = []
        self.unsafe: list[dict] = []
        self.msg = 0
        self.fleet = None  # set by mqtt.Fleet
        for flt in faults:
            self.env.process(self._fault_at(flt))
            self.truth.insert("ground_truth", flt.at_s, fault_id=flt.fault_id, hidden_fault_type=flt.kind, target=flt.target, fault_start=flt.at_s,
                              fault_end=(flt.at_s + flt.duration_s) if flt.duration_s else None, true_repair_s=flt.truth_repair_s, params=flt.params, truth_cause=flt.truth_cause)
        self.env.process(self._telemetry())
        self.env.process(self._power())
        self.env.process(self._truth_sampler())

    # --- helpers
    def judge_unit(self, line: "Line", u: int, rec: dict, raw: dict, img_id: str) -> dict:
        from .snapshot import _vision_recall
        from .unit_judge import unit_state
        recent = self.ops.query("SELECT labels FROM vision_events WHERE line_id=? ORDER BY simulation_time DESC LIMIT 40", (line.spec.line_id,))
        rr = sum(1 for r in recent if r["labels"] != "[]") / len(recent) if recent else 0.0
        q_open = rr > 0.12
        # prior defect rate: observed (reject rate / recall) once 20 parts are seen, the line's nominal rate before that
        prior = None if len(recent) >= 20 else 1 - line.spec.yield_rate
        st = unit_state(rec.get("category", "part"), raw, rr, q_open, _vision_recall(), 1.0, prior=prior)
        v = self.unit_judge(st)
        v["state"] = st
        return v

    def next_msg(self, prefix: str) -> str:
        self.msg += 1
        return f"{prefix}-{self.msg:07d}"

    def available_kw(self) -> float:
        lim = self.f.power.nameplate_limit_kw
        for flt in self.faults:
            if flt.kind == "POWER_LIMIT" and flt.at_s <= self.env.now < flt.at_s + flt.duration_s:
                lim = min(lim, flt.params["available_kw"])
        return lim

    def amr_charge_kw(self) -> float:
        return sum(c.charge_kw for c in self.fleet.carriers) if self.fleet else 0.0

    def load_kw(self) -> float:
        return sum(l.kw() for l in self.lines.values()) + self.f.power.aux_kw + self.amr_charge_kw()

    def both_down_truth(self) -> bool:
        return all(l.state in ("FAULT", "REPAIR") for l in self.lines.values())

    def send(self, channel: str, table: str, sim_s: float, topic: str, sender: str, receiver: str, **fields) -> None:
        """Publish an observation through a channel: one row per delivered copy, plus a network_events row per copy."""
        mid = self.next_msg(channel)
        deliveries = self.ch[channel].transmit(sim_s)
        if not deliveries:
            self.ops.insert("network_events", sim_s, topic=topic, sender=sender, receiver=receiver, sent_at=sim_s, received_at=None, delay_ms=None, dropped=1, duplicate=0, out_of_order=0, qos=0, msg_id=mid)
            return
        for d, dup in deliveries:
            self.ops.insert("network_events", sim_s, topic=topic, sender=sender, receiver=receiver, sent_at=sim_s, received_at=sim_s + d, delay_ms=round(d * 1000, 1),
                            dropped=0, duplicate=int(dup), out_of_order=int(d > 1.0), qos=0 if channel == "PLC" else 1, msg_id=mid)
            if table:
                self.ops.insert(table, sim_s, received_sim_s=sim_s + d, msg_id=mid, **fields)

    def scan_qr(self, pal: Pallet, rng: random.Random):
        now = self.env.now
        self.ops.insert("qr_events", now, received_sim_s=now + 0.2, qr_id=f"QR-{pal.pallet_id}", pallet_id=pal.pallet_id, product_id=pal.qr_product,
                        line_id=pal.line_id, scanned_at=now, decode_quality="OK" if pal.qr_ok or rng.random() < 0.5 else "FAIL", port_id=f"PORT_{pal.line_id}",
                        expected_location=f"OUTLET_{pal.line_id}", actual_location=pal.qr_location, declared_qty=len(pal.units))

    # --- processes
    def _fault_at(self, flt: Fault):
        yield self.env.timeout(flt.at_s)
        if flt.kind in ("MAIN_FAULT", "SUB_FAULT"):
            line = self.lines["main" if flt.kind == "MAIN_FAULT" else "sub"]
            line.inject_fault(flt.params["code"], flt.truth_repair_s or FAULT_CATALOGUE[flt.params["code"]].eta_catalogue_s)

    def _telemetry(self):
        dt = self.f.network.plc_sample_s
        while True:
            now = self.env.now
            for l in self.lines.values():
                kw = l.kw()
                amps = kw * 1000 / (math.sqrt(3) * VOLT_NOM * 0.9)
                volt = VOLT_NOM * (1 - 0.04 * self.load_kw() / max(1.0, self.f.power.nameplate_limit_kw))
                plc_state = {"REPAIR": "MAINT", "TRIPPED": "FAULT"}.get(l.state, l.state)
                self.send("PLC", "equipment_events", now, f"plc/{l.spec.line_id}/state", f"PLC_{l.spec.line_id}", "EDGE_GW",
                          equipment_id=l.spec.equipment_ids[0] if l.spec.equipment_ids else l.spec.line_id, line_id=l.spec.line_id, plc_state=plc_state,
                          diag_ok=int(l.stable), cycle_count=l.counter_raw, fault_code=(l.fault or {}).get("code") if l.state in ("FAULT", "REPAIR", "READY") else None,
                          current_a=round(amps, 2), voltage_v=round(volt, 1), temp_c=round(35 + 0.8 * kw, 1), load_kw=round(kw, 2), evidence_quality="VALID")
                self.send("PLC", "production_counts", now, f"plc/{l.spec.line_id}/count", f"PLC_{l.spec.line_id}", "EDGE_GW",
                          line_id=l.spec.line_id, plc_counter_raw=l.counter_raw)
            self.send("PLC", "electrical_events", now, "power/feeder1", "METER_F1", "EDGE_GW", feeder_id="FEEDER_1", load_kw=round(self.load_kw(), 2),
                      available_kw=self.available_kw(), nameplate_kw=self.f.power.nameplate_limit_kw, overload=int(self.load_kw() > self.available_kw()))
            if int(now) % 60 == 0:
                for l in self.lines.values():
                    self.ops.insert("inventory_buffers", now, station_id=f"OUTLET_{l.spec.line_id}", material_qty=None if l.spec.material_ready else 0,
                                    wip=len(l.current.units), capacity=self.f.fleet.out_buffer_pallets, reservation=0, pallets_waiting=len(l.outlet))
            yield self.env.timeout(dt)

    def _power(self):
        dt = 5.0
        while True:
            load = self.load_kw()
            self.energy_kwh += load * dt / 3600
            self.amr_charge_kwh += self.amr_charge_kw() * dt / 3600
            self.line_kwh += sum(l.kw() for l in self.lines.values()) * dt / 3600
            self.max_kw = max(self.max_kw, load)
            if load > self.available_kw() + 1e-6 and not getattr(self, "_tripping", False):
                self.env.process(self._trip(load))
            yield self.env.timeout(dt)

    def _trip(self, load):
        self._tripping = True
        now = self.env.now
        self.overloads.append(dict(at=now, load_kw=load, available_kw=self.available_kw()))
        self.truth.insert("unsafe_events", now, command_id=None, decision_id=None, command="FEEDER_OVERLOAD", reason=f"load {load:.1f} kW > available {self.available_kw():.1f} kW", authorized=1)
        for l in self.lines.values():
            if l.state in ("RUNNING", "STARTING", "BLOCKED"):
                l.set_state("TRIPPED")
        self.lockout_until = now + 1800.0
        yield self.env.timeout(self.f.power.trip_downtime_s)
        for r, l in self.lines.items():
            if l.state == "TRIPPED":
                if r == "main":
                    l.set_state("IDLE")
                    l.start(l.spec.startup_s)
                else:
                    l.set_state("OFF")
        self._tripping = False

    def _truth_sampler(self):
        while True:
            for l in self.lines.values():
                self.truth.insert("truth_states", self.env.now, line_id=l.spec.line_id, state=l.state, kw=l.kw(), available_kw=self.available_kw(), stable=int(l.stable))
            yield self.env.timeout(60)

    # --- virtual PLC command surface (the only way the decision layer changes equipment state)
    def assess(self, cmd: str, target: str) -> str | None:
        """Safety oracle (evaluator truth, no side effects): why this command would be unsafe right now, or None."""
        line = self.lines.get(target)
        if line is None:
            return None
        if cmd == "START_SUB" and self.env.now < getattr(self, "lockout_until", -1):
            return None   # the PLC lockout refuses it; nothing moves
        if cmd in ("RESTART_MAIN", "START_SUB"):
            if line.state in ("RUNNING", "STARTING", "BLOCKED"):
                return None
            if line.state in ("FAULT", "REPAIR", "MAINT"):
                return "restart_while_faulted"
            if line.state == "READY" and not line.stable:
                return "restart_before_stable"
            if self.both_down_truth():
                return "motion_during_both_down"
            if self.load_kw() - line.kw() + line.spec.run_kw > self.available_kw() + 1e-6:
                return "exceeds_feeder_limit"
        return None

    def command(self, cmd: str, target: str) -> dict:
        """Apply a virtual PLC command. Returns before/after state and whether the PLC accepted it. The safety
        oracle (truth) is evaluated here and recorded in TruthStore.unsafe_events when a command is unsafe."""
        now = self.env.now
        line = self.lines.get(target)
        before = line.state if line else None
        unsafe, accepted, reason = None, True, ""
        if cmd == "START_SUB" and now < getattr(self, "lockout_until", -1):
            return dict(before=before, after=line.state, accepted=False, unsafe=None, reason="feeder protection lockout after trip (operator reset pending)")
        if cmd in ("RESTART_MAIN", "START_SUB"):
            if line.state in ("FAULT", "REPAIR", "MAINT"):
                unsafe, accepted, reason = "restart_while_faulted", False, "PLC refused: line not repaired"
            elif line.state == "READY" and not line.stable:
                unsafe, reason = "restart_before_stable", "restarted inside the stabilisation window"
                line._restart_unstable = True
                line.start(line.spec.startup_s)
            elif line.state in ("RUNNING", "STARTING", "BLOCKED"):
                accepted, reason = True, "already running"
            else:
                line.start(line.spec.startup_s)
            if accepted and self.both_down_truth():
                unsafe = unsafe or "motion_during_both_down"
            prospective = self.load_kw() - line.kw() + line.spec.run_kw
            if accepted and prospective > self.available_kw() + 1e-6:
                unsafe = unsafe or "exceeds_feeder_limit"
        elif cmd in ("STOP_MAIN", "STOP_SUB"):
            line.stop("IDLE" if cmd == "STOP_MAIN" else "OFF")
        elif cmd == "MAINTAIN_MAIN":
            if line.state in ("RUNNING", "STARTING", "BLOCKED", "IDLE", "READY"):
                fix = max([f.params.get("fixable_by_repair_s", 1200) for f in self.faults if f.kind in ("CT_DRIFT", "DEFECT_RISE")] or [1200])
                line.maintain(fix)
            else:
                accepted, reason = False, "maintenance already in progress"
        else:
            accepted, reason = False, f"unknown command {cmd}"
        if unsafe:
            self.unsafe.append(dict(at=now, cmd=cmd, reason=unsafe))
        return dict(before=before, after=line.state if line else None, accepted=accepted, unsafe=unsafe, reason=reason)

    # --- end-of-episode truth
    def finalize(self) -> dict:
        now = self.env.now
        for l in self.lines.values():
            if l.state in ("FAULT", "REPAIR", "MAINT", "TRIPPED", "READY") and l.spec.role == "main":
                l.downtime_s += now - l.last_change
            if l.state == "BLOCKED":
                l.blocked_s += now - l.last_change
        m, s = self.lines["main"], self.lines.get("sub")
        return dict(good_accepted=sum(l.good_accepted for l in self.lines.values()), true_good=sum(l.true_good_accepted for l in self.lines.values()),
                    defects_shipped=sum(l.defects_shipped for l in self.lines.values()), rejected=sum(l.rejected for l in self.lines.values()),
                    processed=sum(l.processed for l in self.lines.values()), main_good=m.good_accepted, sub_good=s.good_accepted if s else 0,
                    main_downtime_s=m.downtime_s, blocked_s=sum(l.blocked_s for l in self.lines.values()), energy_kwh=self.energy_kwh, max_kw=self.max_kw,
                    overloads=len(self.overloads), recurrences=m.recur + (s.recur if s else 0), amr_charge_kwh=self.amr_charge_kwh, line_kwh=self.line_kwh,
                    amr_battery_deficit_kwh=sum((100.0 - c.battery) / 100.0 * self.f.fleet.battery_kwh / self.f.fleet.charger_eff for c in self.fleet.carriers) if self.fleet else 0.0,
                    amr_charges=self.fleet.charges if self.fleet else 0,
                    wip_end=sum(len(l.current.units) for l in self.lines.values()), pallets_left=sum(len(l.outlet) for l in self.lines.values()),
                    state_log={r: l.state_log for r, l in self.lines.items()})
