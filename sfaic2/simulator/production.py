"""SimPy discrete-event production twin with fault injection, virtual PLC telemetry, OT network delivery,
electrical power-flow sampling and synthetic QR/vision events. Deterministic for (config, seed).

Routing is serial: stage 0 → stage 1 → … → last stage (QA/pack). Each stage has `count` parallel identical
machines and a finite input buffer (capacity from config.buffers, else unbounded). Good parts are counted
ONLY when they leave the last stage with a passing yield draw (docs/04 "실제 완료량").

Physical truth (what really happens) and observations (what the collector receives after the network) are
kept apart: `truth` records are hidden from every detector/provider; `events` are what the Command Center sees.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import random
import time
from dataclasses import dataclass, field

import simpy

from .. import __version__
from ..contracts.enums import FaultKind, MachineState, Quality, SourceType
from ..contracts.events import IndustrialEvent, Provenance, TruthRecord
from ..contracts.factory import Fault, FactoryConfig, Machine
from .electrical import ElectricalTwin, current_a
from .network import EventCollector, LinkSpec, NetworkLink
from .virtual_plc import VirtualPLC

AMBIENT_C = 25.0


@dataclass
class MachineRuntime:
    spec: Machine
    unit: int
    state: MachineState = MachineState.IDLE
    processed: int = 0
    good: int = 0
    scrap: int = 0
    run_s: float = 0.0
    fault_s: float = 0.0
    idle_s: float = 0.0
    stopped_s: float = 0.0
    energy_kwh: float = 0.0
    temperature_c: float = AMBIENT_C
    active_faults: list[Fault] = field(default_factory=list)
    _last_t: float = 0.0
    _last_kw: float = 0.0

    @property
    def asset_id(self) -> str:
        return self.spec.machine_id if self.spec.count == 1 else f"{self.spec.machine_id}#{self.unit}"


@dataclass
class RunOutput:
    run_id: str
    config: FactoryConfig
    seed: int
    events: list[IndustrialEvent]
    truth: list[TruthRecord]
    kpis: dict
    manifest: dict
    plc_history: list[dict]
    network_stats: dict
    powerflow_failures: int


class FactorySim:
    def __init__(self, config: FactoryConfig, seed: int | None = None, run_id: str | None = None,
                 electrical_interval_s: float = 30.0, enable_electrical: bool = True):
        self.cfg = config
        self.seed = config.simulation.seed if seed is None else seed
        self.run_id = run_id or f"run_{config.scenario_id}_s{self.seed}"
        self.rng = random.Random(self.seed)
        self.env = simpy.Environment()
        self.machines: list[MachineRuntime] = []
        for m in config.machines:
            for u in range(m.count):
                self.machines.append(MachineRuntime(spec=m, unit=u))
        self.by_asset = {mr.asset_id: mr for mr in self.machines}
        self.plc = VirtualPLC([mr.asset_id for mr in self.machines])
        self.net_rng = random.Random(self.seed + 1)
        self.links: dict[str, NetworkLink] = {}
        spec = LinkSpec(config.network.median_latency_ms, config.network.jitter_ms, config.network.loss_probability,
                        config.network.duplication_probability, config.network.reorder_probability, config.network.tag_ttl_ms)
        for mr in self.machines:
            self.links[mr.asset_id] = NetworkLink(spec, self.net_rng)
        self.links["CAMERA_GATE"] = NetworkLink(spec, self.net_rng)
        self.links["POWER_METER"] = NetworkLink(spec, self.net_rng)
        self.collector = EventCollector()
        self.truth: list[TruthRecord] = []
        self.electrical = ElectricalTwin(config.electrical, config.machines) if enable_electrical else None
        self.electrical_interval_s = electrical_interval_s
        self.extra_feeder_kw: dict[str, float] = {}
        self.powerflow_failures = 0
        self._event_n = 0
        self._stop_release_s: float | None = None
        # stores
        nstages = len(config.stages())
        self.buffers: list[simpy.Store] = []
        for s in range(nstages):
            cap = config.buffer_capacity(s)
            self.buffers.append(simpy.Store(self.env, capacity=cap if cap else float("inf")))
        self.outbound: list[dict] = []
        self.good = 0
        self.scrap = 0
        self.released = 0
        self.plan_met_at_s: float | None = None
        self.wip_trace: list[tuple[float, int]] = []
        self.kw_trace: list[tuple[float, float]] = []
        self.powerflow_trace: list[dict] = []
        self.vision_misses = 0
        self.vision_defects = 0
        self.qr_misroutes = 0
        self.last_pf = None
        self.in_process = 0

    # ------------------------------------------------------------------ helpers
    def _emit(self, asset_id: str, signal: str, value, unit=None, quality=Quality.VALID, source=SourceType.SIMULATED,
              link: str | None = None, extra_prov: dict | None = None):
        self._event_n += 1
        ev = IndustrialEvent(event_id=f"ev{self._event_n:07d}", run_id=self.run_id, sim_time_s=float(self.env.now),
                             asset_id=asset_id, source_type=source, signal=signal, value=value, unit=unit, quality=quality,
                             provenance=Provenance(source_label="SYNTHETIC_DEMO", seed=self.seed, **(extra_prov or {})))
        self.collector.ingest(self.links[link or asset_id].transmit(ev))

    def _fault_active(self, mr: MachineRuntime, kind: FaultKind) -> Fault | None:
        for f in mr.active_faults:
            if f.kind == kind or (f.kind == FaultKind.MULTI_CAUSE and kind in (FaultKind.MECHANICAL_STOP, FaultKind.NETWORK_DELAY)):
                return f
        return None

    def _ct(self, mr: MachineRuntime) -> float:
        base = mr.spec.cycle_time_s
        f = self._fault_active(mr, FaultKind.MECHANICAL_OVERLOAD)
        if f:
            base *= 1.0 + 0.25 * f.severity
        cv = mr.spec.cycle_time_cv
        if cv <= 0:
            return base
        sigma = math.sqrt(math.log(1 + cv * cv))
        return base * math.exp(self.rng.gauss(-sigma * sigma / 2, sigma))

    def _kw(self, mr: MachineRuntime) -> float:
        p = mr.spec.power
        if mr.state == MachineState.RUNNING:
            kw = p.run_kw
            f = self._fault_active(mr, FaultKind.MECHANICAL_OVERLOAD)
            return kw * (1.0 + 0.4 * f.severity) if f else kw
        if mr.state in (MachineState.IDLE, MachineState.STOP_REQUESTED, MachineState.STOPPING):
            return p.idle_kw
        if mr.state == MachineState.FAULT:
            return p.idle_kw
        return p.off_kw

    def _accrue(self, mr: MachineRuntime):
        """Integrate energy and state time since last call."""
        now = self.env.now
        dt = now - mr._last_t
        if dt > 0:
            mr.energy_kwh += mr._last_kw * dt / 3600.0
            {MachineState.RUNNING: "run_s", MachineState.FAULT: "fault_s", MachineState.STOPPED: "stopped_s"}
            if mr.state == MachineState.RUNNING:
                mr.run_s += dt
            elif mr.state == MachineState.FAULT:
                mr.fault_s += dt
            elif mr.state in (MachineState.STOPPED, MachineState.STOPPING, MachineState.STOP_REQUESTED):
                mr.stopped_s += dt
            else:
                mr.idle_s += dt
            # first-order thermal model (DEMO): target temp rises with load
            target = AMBIENT_C + 1.2 * mr._last_kw
            tau = 240.0
            mr.temperature_c += (target - mr.temperature_c) * (1 - math.exp(-dt / tau))
        mr._last_t = now
        mr._last_kw = self._kw(mr)

    def _set_state(self, mr: MachineRuntime, st: MachineState):
        self._accrue(mr)
        if mr.state != st:
            mr.state = st
            mr._last_kw = self._kw(mr)
            self.plc.write_tag(mr.asset_id, "machine_state", st.value, self.env.now)
            self._emit(mr.asset_id, "machine_state", st.value)

    # ------------------------------------------------------------------ processes
    def _source(self):
        """Release raw material into the first buffer as long as the plan is not met (plus scrap allowance)."""
        plan = self.cfg.production_plan.quantity
        while True:
            if self.good >= plan:
                yield self.env.timeout(5.0)
                continue
            n = self.released + 1
            yield self.buffers[0].put(dict(token_id=f"T{n:06d}", pallet_id=f"PTG3{n:08d}", released_s=self.env.now))
            self.released = n  # counted only once the token is physically in the first buffer
            yield self.env.timeout(0.1)

    def _machine(self, mr: MachineRuntime):
        stage = mr.spec.stage
        inbuf = self.buffers[stage]
        last = stage == len(self.buffers) - 1
        while True:
            # halted by virtual STOP?
            if self.plc.is_halted(mr.asset_id):
                self._set_state(mr, MachineState.STOPPED)
                yield self.env.timeout(1.0)
                continue
            cmd = self.plc.machine_command_state(mr.asset_id)
            if cmd == MachineState.STOP_REQUESTED:
                self._set_state(mr, MachineState.STOP_REQUESTED)
                yield self.env.timeout(1.0)
                continue
            if self._fault_active(mr, FaultKind.MECHANICAL_STOP):
                self._set_state(mr, MachineState.FAULT)
                yield self.env.timeout(1.0)
                continue
            if self.good >= self.cfg.production_plan.quantity:
                self._set_state(mr, MachineState.IDLE)
                yield self.env.timeout(5.0)
                continue
            self._set_state(mr, MachineState.IDLE)
            get = inbuf.get()
            res = yield get | self.env.timeout(1.0)
            if get not in res:
                if get.triggered:
                    token = get.value
                else:
                    get.cancel()  # starved: drop the pending request so no token is silently consumed
                    continue
            else:
                token = res[get]
            self.in_process += 1
            self._set_state(mr, MachineState.RUNNING)
            yield self.env.timeout(self._ct(mr))
            self._accrue(mr)
            mr.processed += 1
            if self.rng.random() <= mr.spec.yield_rate:
                mr.good += 1
                if last:
                    self.in_process -= 1
                    self.good += 1
                    token["completed_s"] = self.env.now
                    self.outbound.append(token)
                    self._emit(mr.asset_id, "production_good_count", self.good, "parts")
                    self._camera_gate(mr, token)
                    if self.good >= self.cfg.production_plan.quantity and self.plan_met_at_s is None:
                        self.plan_met_at_s = self.env.now
                else:
                    self._set_state(mr, MachineState.IDLE)
                    yield self.buffers[stage + 1].put(token)  # blocking when downstream buffer is full
                    self.in_process -= 1
            else:
                mr.scrap += 1
                self.scrap += 1
                self.in_process -= 1
                self._emit(mr.asset_id, "scrap_count", self.scrap, "parts")

    def _camera_gate(self, mr: MachineRuntime, token: dict):
        """Synthetic vision + QR events at the outbound gate (source_type=DERIVED, provenance SIMULATED)."""
        misroute = self._fault_active(mr, FaultKind.QR_MISROUTE)
        vis_fn = self._fault_active(mr, FaultKind.VISION_FALSE_NEGATIVE)
        prov = dict(transform_id="synthetic_camera_v1")
        observed_port = "OUTBOUND_GATE"
        route_ok = True
        if misroute:
            observed_port, route_ok = "QA_GATE_REWORK", False
            self.qr_misroutes += 1
        self._emit("CAMERA_GATE", "qr_pallet_id", dict(pallet_id=token["pallet_id"], decoded=True, expected_route="PRESS_TO_PACK",
                                                       observed_port=observed_port, route_consistent=route_ok, upstream=mr.asset_id),
                   source=SourceType.DERIVED, extra_prov=prov)
        detected = True
        conf = round(0.80 + 0.15 * self.rng.random(), 3)
        if vis_fn:
            detected, conf = False, round(0.40 + 0.3 * self.rng.random(), 3)
            self.vision_misses += 1
        vdef = self._fault_active(mr, FaultKind.VISION_DEFECT)
        payload = dict(detected=detected, confidence=conf, detector="SYNTHETIC_YOLO_EVENT", model_version=None, upstream=mr.asset_id,
                       defect=False, defect_class=None, defect_confidence=None)
        if vdef and detected and self.rng.random() < float(vdef.params.get("defect_recall", 1.0)):  # measured YOLO recall (factory_vision) when provided
            payload.update(defect=True, defect_class=vdef.params.get("defect_class", "surface_scratch"),
                           defect_confidence=round(0.70 + 0.25 * self.rng.random(), 3))
            self.vision_defects += 1
        self._emit("CAMERA_GATE", "vision_part_presence", payload, source=SourceType.DERIVED,
                   quality=Quality.VALID if detected else Quality.QUESTIONABLE, extra_prov=prov)

    def _fault_scheduler(self, f: Fault):
        yield self.env.timeout(f.at_s)
        targets = [mr for mr in self.machines if mr.spec.machine_id == f.machine_id]
        for mr in targets:
            mr.active_faults.append(f)
        self._apply_fault_side_effects(f, targets, on=True)
        yield self.env.timeout(f.duration_s)
        for mr in targets:
            if f in mr.active_faults:
                mr.active_faults.remove(f)
        self._apply_fault_side_effects(f, targets, on=False)

    def _apply_fault_side_effects(self, f: Fault, targets: list[MachineRuntime], on: bool):
        sev = f.severity
        if f.kind in (FaultKind.NETWORK_DELAY, FaultKind.MULTI_CAUSE):
            for mr in targets:
                self.links[mr.asset_id].extra_delay_ms = (f.params.get("extra_delay_ms", 1500 * sev)) if on else 0.0
        if f.kind == FaultKind.ELECTRICAL_LOADING:
            feeder = targets[0].spec.feeder_id if targets else "FEEDER_1"
            self.extra_feeder_kw[feeder] = (f.params.get("extra_kw", 150 * sev)) if on else 0.0

    def _telemetry(self):
        """Virtual PLC tag publication through the network at the sampling interval."""
        dt = self.cfg.simulation.sample_interval_s
        while True:
            yield self.env.timeout(dt)
            for tr in self.plc.advance(self.env.now):
                mr = self.by_asset[tr["machine_id"]]
                self._set_state(mr, MachineState(tr["state"]))
                self._emit(mr.asset_id, "virtual_plc_ack", tr)
            vpu = self.last_pf.lv_bus_voltage_pu if self.last_pf and self.last_pf.status == "OK" else 1.0
            total_kw = 0.0
            for mr in self.machines:
                self._accrue(mr)
                kw = self._kw(mr)
                total_kw += kw
                amps = current_a(kw, vpu, self.cfg.electrical.lv_kv, mr.spec.power.power_factor)
                sensor_fault = self._fault_active(mr, FaultKind.SENSOR_FAULT)
                reported_a = float(sensor_fault.params.get("stuck_value", 0.0)) if sensor_fault else round(amps, 2)
                self.plc.write_tag(mr.asset_id, "current_a", reported_a, self.env.now)
                self.plc.write_tag(mr.asset_id, "temperature_c", round(mr.temperature_c, 1), self.env.now)
                self.plc.write_tag(mr.asset_id, "active_power_kw", round(kw, 2), self.env.now)
                self._emit(mr.asset_id, "machine_state", mr.state.value)
                self._emit(mr.asset_id, "current_a", reported_a, "A")
                self._emit(mr.asset_id, "temperature_c", round(mr.temperature_c, 1), "degC")
                self._emit(mr.asset_id, "active_power_kw", round(kw, 2), "kW")
                self.truth.append(TruthRecord(run_id=self.run_id, sim_time_s=float(self.env.now), machine_id=mr.asset_id,
                                              physical_state=mr.state.value, active_fault_ids=[x.fault_id for x in mr.active_faults],
                                              truth_classes=sorted({x.truth_class.value for x in mr.active_faults}) or ["NONE"],
                                              expected_safe_action=(mr.active_faults[0].expected_safe_action if mr.active_faults else "CONTINUE_MONITORING"),
                                              harmful_stop_warranted=any(x.harmful_stop_warranted for x in mr.active_faults)))
            wip = sum(len(b.items) for b in self.buffers) + self.in_process
            self.wip_trace.append((self.env.now, wip))
            self.kw_trace.append((self.env.now, total_kw))
            self._emit("LINE", "wip", wip, "parts", link="POWER_METER")
            self._emit("LINE", "plan_gap", self._plan_gap(), "parts", link="POWER_METER")

    def _plan_gap(self) -> int:
        plan = self.cfg.production_plan
        planned_cum = min(plan.quantity, int(plan.quantity * self.env.now / plan.shift_duration_s))
        return planned_cum - self.good

    def _electrical_sampler(self):
        if self.electrical is None:
            return
        dt = self.electrical_interval_s
        while True:
            yield self.env.timeout(dt)
            loads = {}
            for m in self.cfg.machines:
                loads[m.machine_id] = sum(self._kw(mr) for mr in self.machines if mr.spec.machine_id == m.machine_id)
            res = self.electrical.solve(loads, self.extra_feeder_kw)
            self.last_pf = res
            rec = dict(sim_time_s=self.env.now, **res.__dict__)
            self.powerflow_trace.append(rec)
            if res.status != "OK":
                self.powerflow_failures += 1
                self._emit("LV_BUS", "powerflow_status", res.status, quality=Quality.MISSING, link="POWER_METER", source=SourceType.DERIVED)
                continue
            self._emit("LV_BUS", "bus_voltage_pu", round(res.lv_bus_voltage_pu, 4), "pu", link="POWER_METER", source=SourceType.DERIVED)
            self._emit("TR_DEMO", "transformer_loading_pct", round(res.transformer_loading_percent, 2), "%", link="POWER_METER", source=SourceType.DERIVED)
            self._emit("LV_BUS", "total_active_kw", round(res.total_active_kw, 2), "kW", link="POWER_METER", source=SourceType.DERIVED)

    # ------------------------------------------------------------------ run
    def start(self) -> None:
        """Register all processes (call once); then `advance()` incrementally or `run()` to the end."""
        if getattr(self, "_started", False):
            return
        self._started = True
        self._t_wall = time.time()
        for mr in self.machines:
            mr._last_t = 0.0
            mr._last_kw = self._kw(mr)
            self.plc.write_tag(mr.asset_id, "machine_state", mr.state.value, 0.0)
        self.env.process(self._source())
        for mr in self.machines:
            self.env.process(self._machine(mr))
        for f in self.cfg.faults:
            self.env.process(self._fault_scheduler(f))
        self.env.process(self._telemetry())
        if self.electrical is not None:
            self.env.process(self._electrical_sampler())

    def advance(self, until_s: float) -> float:
        """Run the simulation clock up to `until_s` (sim seconds). Returns the new clock value."""
        self.start()
        until_s = min(until_s, self.cfg.simulation.duration_s)
        if until_s > self.env.now:
            self.env.run(until=until_s)
        return float(self.env.now)

    def inject_fault(self, fault: Fault) -> None:
        """Live fault injection (simulator-only): schedule relative to the current clock."""
        self.start()
        f = fault.model_copy(update={"at_s": max(0.0, fault.at_s - self.env.now)})
        self.cfg.faults.append(fault)
        self.env.process(self._fault_scheduler(f))

    def observed_events(self) -> list[IndustrialEvent]:
        """Events received so far, in arrival order (non-destructive view for live decisioning)."""
        return sorted(self.collector.received, key=lambda e: (e.received_sim_s if e.received_sim_s is not None else e.sim_time_s, e.seq or 0))

    @property
    def done(self) -> bool:
        return self.env.now >= self.cfg.simulation.duration_s

    def finalize(self) -> RunOutput:
        for mr in self.machines:
            self._accrue(mr)
        events = self.collector.finalize()
        return RunOutput(run_id=self.run_id, config=self.cfg, seed=self.seed, events=events, truth=self.truth,
                         kpis=self.kpis(), manifest=self.manifest(events, time.time() - self._t_wall), plc_history=self.plc.history,
                         network_stats={k: dict(v.stats) for k, v in self.links.items()}, powerflow_failures=self.powerflow_failures)

    def run(self, until_s: float | None = None) -> RunOutput:
        self.start()
        self.advance(until_s or self.cfg.simulation.duration_s)
        return self.finalize()

    # ------------------------------------------------------------------ KPIs
    def kpis(self) -> dict:
        T = self.env.now
        plan = self.cfg.production_plan
        total_processed_last = sum(mr.processed for mr in self.machines if mr.spec.stage == len(self.buffers) - 1)
        runtime = sum(mr.run_s for mr in self.machines)
        planned_time = T * len(self.machines)
        ideal_ct = {m.machine_id: m.cycle_time_s for m in self.cfg.machines}
        ideal_work = sum(ideal_ct[mr.spec.machine_id] * mr.processed for mr in self.machines)
        availability = runtime / planned_time if planned_time else None
        performance = min(1.0, ideal_work / runtime) if runtime else None
        quality = self.good / (self.good + self.scrap) if (self.good + self.scrap) else None
        oee = availability * performance * quality if None not in (availability, performance, quality) else None
        energy = sum(mr.energy_kwh for mr in self.machines)
        aux_kwh = self.cfg.electrical.auxiliary_kw * T / 3600
        cap = self.cfg.theoretical_capacity(T)
        pf_ok = [p for p in self.powerflow_trace if p["status"] == "OK"]
        wip_end = sum(len(b.items) for b in self.buffers) + self.in_process
        return dict(
            sim_seconds=T, plan_quantity=plan.quantity, good_count=self.good, scrap_count=self.scrap, released=self.released,
            wip_end=wip_end, wip_max=max((w for _, w in self.wip_trace), default=0),
            conservation_ok=(self.released == self.good + self.scrap + wip_end),
            throughput_good_per_h=self.good / (T / 3600) if T else None,
            plan_attainment=self.good / plan.quantity if plan.quantity else None,
            plan_gap_end=self._plan_gap(), plan_met_at_s=self.plan_met_at_s,
            theoretical_line_capacity=cap["line_capacity"], bottleneck_stage=cap["bottleneck_stage"],
            availability=availability, performance=performance, quality=quality, oee=oee,
            machine_energy_kwh=round(energy, 4), auxiliary_energy_kwh=round(aux_kwh, 4), total_energy_kwh=round(energy + aux_kwh, 4),
            energy_kwh_per_good_part=round((energy + aux_kwh) / self.good, 4) if self.good else None,
            peak_machine_kw=max((k for _, k in self.kw_trace), default=0.0),
            unplanned_fault_s=sum(mr.fault_s for mr in self.machines), stopped_s=sum(mr.stopped_s for mr in self.machines),
            min_bus_voltage_pu=min((p["min_bus_voltage_pu"] for p in pf_ok), default=None),
            max_transformer_loading_pct=max((p["transformer_loading_percent"] for p in pf_ok), default=None),
            powerflow_failures=self.powerflow_failures, electrical_status=("UNAVAILABLE" if self.electrical is None or not self.electrical.available else "OK"),
            vision_misses=self.vision_misses, vision_defects=self.vision_defects, qr_misroutes=self.qr_misroutes,
            per_machine={mr.asset_id: dict(state=mr.state.value, processed=mr.processed, good=mr.good, scrap=mr.scrap, run_s=round(mr.run_s, 1),
                                           fault_s=round(mr.fault_s, 1), idle_s=round(mr.idle_s, 1), stopped_s=round(mr.stopped_s, 1),
                                           energy_kwh=round(mr.energy_kwh, 4), temperature_c=round(mr.temperature_c, 1)) for mr in self.machines},
            source_type="SIMULATED", notice="Synthetic DES/power-flow result; not measured in a physical plant")

    def manifest(self, events: list[IndustrialEvent], wall_s: float) -> dict:
        trace = "".join(json.dumps(e.to_schema_dict(), sort_keys=True, ensure_ascii=False, default=str) + "\n" for e in events)
        try:
            import pandapower
            ppv = pandapower.__version__
        except Exception:
            ppv = None
        return dict(run_id=self.run_id, scenario_id=self.cfg.scenario_id, seed=self.seed, config_sha256=self.cfg.config_hash(),
                    events_sha256=hashlib.sha256(trace.encode()).hexdigest(), n_events=len(events), n_truth=len(self.truth),
                    sim_seconds=float(self.env.now), wall_seconds=round(wall_s, 3), sfaic2_version=__version__,
                    python=platform.python_version(), simpy=simpy.__version__, pandapower=ppv, platform=platform.platform(),
                    source_type="SIMULATED", clock_note="sim_time_s is the SimPy clock; wall_seconds is host execution time")


def run_config(config: FactoryConfig, seed: int | None = None, **kw) -> RunOutput:
    return FactorySim(config, seed=seed, **kw).run()
