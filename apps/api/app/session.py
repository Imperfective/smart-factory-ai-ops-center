"""Live simulation session: paces the SimPy clock against wall time (1x/10x/100x/max), runs the decision engine
at its interval, exposes snapshots, accepts simulator-only fault injection and operator approvals."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque

from sfaic2.adapters.typesafe_jev import TypeSafeJevProvider
from sfaic2.contracts.enums import CommandStatus
from sfaic2.contracts.factory import FactoryConfig, Fault
from sfaic2.decision.engine import DecisionEngine, OperatorPolicy
from sfaic2.decision.providers import MockJevProvider
from sfaic2.runs import save_run
from sfaic2.simulator.production import FactorySim, RunOutput


class SimulationSession:
    def __init__(self, config: FactoryConfig, seed: int | None, arm: str = "E", provider: str = "mock",
                 speed: float = 10.0, decision_interval_s: float = 30.0, auto_operator: bool = False, run_id: str | None = None):
        self.run_id = run_id or f"live_{config.scenario_id}_{uuid.uuid4().hex[:6]}"
        self.sim = FactorySim(config, seed=seed, run_id=self.run_id)
        self.sim.start()
        jev = TypeSafeJevProvider() if provider == "typesafe" else None
        override = MockJevProvider() if provider == "mock" else None
        self._run_stub = RunOutput(run_id=self.run_id, config=config, seed=self.sim.seed, events=[], truth=[], kpis={},
                                   manifest=dict(sim_seconds=config.simulation.duration_s), plc_history=[], network_stats={}, powerflow_failures=0)
        self.engine = DecisionEngine(self._run_stub, arm=arm, decision_interval_s=decision_interval_s, provider_override=override,
                                     jev_provider=jev, plc=self.sim.plc, events_source=self.sim.observed_events,
                                     operator=OperatorPolicy(delay_s=5.0, approve_rate=1.0), auto_operator=auto_operator)
        self.provider, self.arm = provider, arm
        self.speed = speed
        self.state = "PAUSED"
        self.wall_started = time.time()
        self.wall_sim_s = 0.0
        self._next_decision = decision_interval_s
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.timeline: deque = deque(maxlen=500)
        self.final: RunOutput | None = None
        self.saved_path = None
        self.error: str | None = None

    # ---- control
    def start(self):
        with self._lock:
            if self.state in ("FINISHED", "ERROR"):
                return
            self.state = "RUNNING"
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True, name=f"sim-{self.run_id}")
                self._thread.start()

    def pause(self):
        with self._lock:
            if self.state == "RUNNING":
                self.state = "PAUSED"

    def resume(self):
        self.start()

    def step(self, sim_seconds: float = 10.0):
        with self._lock:
            if self.state in ("FINISHED", "ERROR"):
                return
            self._advance(min(self.sim.env.now + sim_seconds, self.sim.cfg.simulation.duration_s))

    def set_speed(self, speed: float):
        self.speed = max(0.1, float(speed))

    def stop(self):
        self._stop.set()
        self.pause()

    # ---- internals
    def _advance(self, until: float):
        self.sim.advance(until)
        while self._next_decision <= self.sim.env.now:
            for d in self.engine.step(self._next_decision):
                self.timeline.append(dict(kind="decision", sim_time_s=self._next_decision, asset_id=d.asset_id,
                                          recommendation=d.recommendation.value, risk=d.risk_band.value, decision_id=d.decision_id,
                                          provider=d.provider.value, causes=d.causes))
            self._next_decision += self.engine.interval
        if self.sim.done and self.state != "FINISHED":
            self._finish()

    def _finish(self):
        self.state = "FINISHED"
        self.final = self.sim.finalize()
        self.saved_path = str(save_run(self.final, self.engine.result(), extra=dict(live=True, provider=self.provider, speed=self.speed)))
        try:  # every finished live run is appended to the research DB (data + metrics + live aggregates)
            from pathlib import Path as _P
            from sfaic2.research.db import append_live_run
            append_live_run(_P(self.saved_path))
        except Exception as exc:  # noqa: BLE001 — the run itself is already saved; DB append is best-effort and logged
            self.timeline.append(dict(kind="db", sim_time_s=float(self.sim.env.now), message=f"research DB append failed: {exc}"))

    def _loop(self):
        tick = 0.25
        try:
            while not self._stop.is_set():
                if self.state != "RUNNING":
                    time.sleep(0.1)
                    continue
                t0 = time.time()
                with self._lock:
                    if self.state == "RUNNING":
                        self._advance(self.sim.env.now + tick * self.speed)
                if self.state == "FINISHED":
                    break
                if self.speed < 1000:
                    time.sleep(max(0.0, tick - (time.time() - t0)))
        except Exception as exc:  # noqa: BLE001
            self.state, self.error = "ERROR", f"{type(exc).__name__}: {exc}"

    # ---- simulator-only actions
    def inject_fault(self, fault: Fault):
        with self._lock:
            self.sim.inject_fault(fault)
            self.timeline.append(dict(kind="fault_injected", sim_time_s=self.sim.env.now, fault=fault.model_dump()))

    def evaluate_now(self):
        with self._lock:
            t = self.sim.env.now
            return self.engine.step(t)

    def propose_stop(self, target_id: str, requested_by: str, decision_id: str | None = None):
        with self._lock:
            if decision_id:
                dec = next((d for d in self.engine.decisions if d.decision_id == decision_id), None)
            else:  # operator-initiated: attach the latest evaluation for this asset (the gate checks its freshness)
                dec = next((d for d in reversed(self.engine.decisions) if d.asset_id == target_id), None)
            cmd = self.engine.gate.propose(dec, target_id, requested_by, self.sim.env.now)
            if cmd not in self.engine.commands:
                self.engine.commands.append(cmd)
            self.timeline.append(dict(kind="command", sim_time_s=self.sim.env.now, command_id=cmd.command_id, status=cmd.status.value, reason=cmd.reason))
            return cmd

    def approve(self, command_id: str, operator_id: str, approved: bool):
        with self._lock:
            cmd = self.engine.gate.commands.get(command_id)
            if cmd is None:
                raise KeyError(command_id)
            ctx = None
            if cmd.decision_id:
                dec = next((d for d in self.engine.decisions if d.decision_id == cmd.decision_id), None)
                ctx = dec.context_hash if dec else None
            res = self.engine.gate.approve(command_id, operator_id, self.sim.env.now, current_context_hash=ctx, approved=approved)
            self.timeline.append(dict(kind="approval", sim_time_s=self.sim.env.now, command_id=command_id, operator=operator_id,
                                      approved=approved, status=res.status.value, reason=res.reason))
            return res

    # ---- views
    def snapshot(self) -> dict:
        with self._lock:
            sim = self.sim
            k = sim.kpis() if sim.env.now > 0 else {}
            machines = {}
            for mr in sim.machines:
                tags = {t: dict(value=v.value, sim_time_s=v.sim_time_s) for t, v in sim.plc.snapshot(mr.asset_id).items()}
                machines[mr.asset_id] = dict(state=mr.state.value, stage=mr.spec.stage, work_center=mr.spec.work_center_id,
                                             tags=tags, command=sim.plc.command_state.get(mr.asset_id).value if sim.plc.command_state.get(mr.asset_id) else None,
                                             active_faults=[f.fault_id for f in mr.active_faults], temperature_c=round(mr.temperature_c, 1),
                                             kw=round(sim._kw(mr), 2), good=mr.good, scrap=mr.scrap, processed=mr.processed)
            pf = sim.last_pf.__dict__ if sim.last_pf else None
            pending = [c.model_dump() for c in self.engine.gate.commands.values() if c.status == CommandStatus.PENDING_APPROVAL]
            recent = [d.model_dump() for d in self.engine.decisions[-12:]]
            from sfaic2.simulator.scenarios import SCENARIO_INFO
            net_tot = {k: sum(l.stats[k] for l in sim.links.values()) for k in ("sent", "delivered", "lost", "stale", "duplicated")}
            last_dec = self.engine.decisions[-1] if self.engine.decisions else None
            last_cmd = self.engine.commands[-1] if self.engine.commands else None
            jev_calls = self.engine.jev.budget.calls if isinstance(self.engine.jev, TypeSafeJevProvider) else sum(1 for d in self.engine.decisions if d.jev_call)
            pipeline = dict(signals_emitted=sim._event_n, plc_tag_writes=sum(len(v) for v in sim.plc.tags.values()),
                            mqtt_delivered=net_tot["delivered"], mqtt_lost=net_tot["lost"], mqtt_stale=net_tot["stale"],
                            vision_events=sum(1 for e in sim.collector.received if e.signal == "vision_part_presence"),
                            qr_events=sum(1 for e in sim.collector.received if e.signal == "qr_pallet_id"),
                            evidence_bundles=self.engine.bundles, judgments=len(self.engine.decisions), jev_calls=jev_calls,
                            jev_fallbacks=self.engine.fallbacks, commands_proposed=self.engine.gate.stats["proposed"],
                            commands_executed=self.engine.gate.stats["executed"], plc_acks=sum(1 for h in sim.plc.history if h.get("state") == "STOPPING"),
                            last_decision=(dict(asset_id=last_dec.asset_id, recommendation=last_dec.recommendation.value, sim_time_s=last_dec.sim_time_s, provider=last_dec.provider.value) if last_dec else None),
                            last_command=(dict(target_id=last_cmd.target_id, status=last_cmd.status.value, sim_time_s=last_cmd.proposed_sim_s) if last_cmd else None))
            scen_key = next((k for k in SCENARIO_INFO if sim.cfg.scenario_id.startswith(k)), None)
            return dict(run_id=self.run_id, state=self.state, error=self.error, sim_time_s=float(sim.env.now), speed=self.speed,
                        pipeline=pipeline, scenario=(dict(key=scen_key, **SCENARIO_INFO[scen_key]) if scen_key else dict(key=None, title_ko=sim.cfg.scenario_id, title_en=sim.cfg.scenario_id)),
                        duration_s=sim.cfg.simulation.duration_s, wall_elapsed_s=round(time.time() - self.wall_started, 1), arm=self.arm,
                        provider=self.provider, kpis=k, machines=machines, buffers=[dict(stage=i, items=len(b.items), capacity=(None if b.capacity == float("inf") else b.capacity)) for i, b in enumerate(sim.buffers)],
                        electrical=pf, network={a: dict(l.stats) for a, l in sim.links.items()},
                        pending_commands=pending, commands=[c.model_dump() for c in self.engine.commands[-20:]],
                        recent_decisions=recent, gate_stats=self.engine.gate.stats, jev_usage=(self.engine.jev.budget.summary() if isinstance(self.engine.jev, TypeSafeJevProvider) else None),
                        jev_fallbacks=self.engine.fallbacks, timeline=list(self.timeline)[-60:], saved_path=self.saved_path,
                        banner="SIMULATED — virtual PLC only; no physical equipment is controlled")
