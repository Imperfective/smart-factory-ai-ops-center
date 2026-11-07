"""Factory configuration (ISA-95-inspired Plant → Area → WorkCenter → Machine) — Pydantic 2 models.

Compatible with schemas/factory.schema.json (starter format) via `FactoryConfig.from_starter`.
Everything here is SIMULATED configuration; no real network addresses are accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import FaultKind, SourceType, TruthClass

_REAL_TARGET = re.compile(r"(\d{1,3}\.){3}\d{1,3}|opc\.tcp://|mqtt://|modbus|s7://|plc-\d", re.I)


class ProductionPlan(BaseModel):
    product_id: str
    quantity: int = Field(ge=0)
    shift_duration_s: int = Field(ge=1)
    due_time_sim_s: int | None = None
    planned_breaks_s: int = Field(0, ge=0)


class PowerSpec(BaseModel):
    run_kw: float = Field(ge=0)
    idle_kw: float = Field(ge=0)
    off_kw: float = Field(0.0, ge=0)
    peak_kw: float | None = None
    power_factor: float = Field(0.9, gt=0, le=1)


class Machine(BaseModel):
    machine_id: str
    work_center_id: str = "WC_DEFAULT"
    stage: int = Field(0, ge=0, description="position in the serial routing (0 = first)")
    count: int = Field(1, ge=1, description="parallel identical machines")
    cycle_time_s: float = Field(gt=0)
    cycle_time_cv: float = Field(0.05, ge=0, le=1, description="lognormal coefficient of variation")
    yield_rate: float = Field(1.0, gt=0, le=1)
    mtbf_s: float | None = Field(None, gt=0)
    mttr_s: float | None = Field(None, gt=0)
    power: PowerSpec
    feeder_id: str = "FEEDER_1"
    site: str | None = None
    plc_tags: list[str] = Field(default_factory=lambda: ["machine_state", "current_a", "temperature_c"])

    @field_validator("machine_id", "work_center_id", "feeder_id")
    @classmethod
    def _virtual_only(cls, v: str) -> str:
        if _REAL_TARGET.search(v):
            raise ValueError(f"identifier '{v}' looks like a real network target; only virtual ids are allowed")
        return v


class Buffer(BaseModel):
    buffer_id: str
    before_stage: int = Field(ge=0)
    capacity: int = Field(ge=1)


class Fault(BaseModel):
    fault_id: str
    at_s: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    kind: FaultKind
    machine_id: str
    severity: float = Field(1.0, ge=0, le=3)
    truth_class: TruthClass
    expected_safe_action: Literal["CONTINUE_MONITORING", "REQUEST_RECHECK", "REQUEST_OPERATOR_REVIEW", "REQUEST_SIMULATED_STOP"]
    harmful_stop_warranted: bool = False
    params: dict = Field(default_factory=dict)


class NetworkSpec(BaseModel):
    median_latency_ms: float = Field(50, ge=0)
    jitter_ms: float = Field(10, ge=0)
    loss_probability: float = Field(0.0, ge=0, le=1)
    duplication_probability: float = Field(0.0, ge=0, le=1)
    reorder_probability: float = Field(0.0, ge=0, le=1)
    tag_ttl_ms: float = Field(500, gt=0)
    virtual_only: Literal[True] = True


class ElectricalSpec(BaseModel):
    name: str = "DEMO_400V_LV_BUS"
    load_model: str = "SYNTHETIC_EXAMPLE_NOT_MEASURED"
    hv_kv: float = 20.0
    lv_kv: float = 0.4
    transformer_sn_mva: float = 0.4
    transformer_vk_percent: float = 4.0
    transformer_vkr_percent: float = 1.0
    auxiliary_kw: float = 5.0
    feeders: list[str] = Field(default_factory=lambda: ["FEEDER_1"])
    feeder_length_km: float = 0.05
    nominal_voltage_band_pu: tuple[float, float] = (0.95, 1.05)
    loading_alarm_percent: float = 90.0


class SimulationSpec(BaseModel):
    duration_s: int = Field(ge=1)
    seed: int = 42
    sample_interval_s: float = Field(10.0, gt=0, description="telemetry sampling interval (sim seconds)")


class FactoryConfig(BaseModel):
    scenario_id: str
    source_type: Literal["SIMULATED"] = "SIMULATED"
    simulation: SimulationSpec
    production_plan: ProductionPlan
    machines: list[Machine]
    buffers: list[Buffer] = Field(default_factory=list)
    faults: list[Fault] = Field(default_factory=list)
    network: NetworkSpec = Field(default_factory=NetworkSpec)
    electrical: ElectricalSpec = Field(default_factory=ElectricalSpec)
    provenance: dict = Field(default_factory=lambda: {"dataset_id": None, "label_source": "SIMULATED_SCENARIO"})

    @model_validator(mode="after")
    def _check(self):
        ids = [m.machine_id for m in self.machines]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate machine_id: {sorted({i for i in ids if ids.count(i) > 1})}")
        if not self.machines:
            raise ValueError("at least one machine required")
        known = set(ids)
        for f in self.faults:
            if f.machine_id not in known:
                raise ValueError(f"fault {f.fault_id} targets unknown machine {f.machine_id}")
        fids = [f.fault_id for f in self.faults]
        if len(fids) != len(set(fids)):
            raise ValueError("duplicate fault_id")
        stages = sorted({m.stage for m in self.machines})
        if stages != list(range(len(stages))):
            raise ValueError(f"stages must be contiguous from 0, got {stages}")
        for b in self.buffers:
            if b.before_stage > max(stages):
                raise ValueError(f"buffer {b.buffer_id} before unknown stage {b.before_stage}")
        if self.source_type != "SIMULATED":
            raise ValueError("only SIMULATED factories are accepted")
        return self

    # ---- helpers
    def stages(self) -> list[list[Machine]]:
        n = max(m.stage for m in self.machines) + 1
        return [[m for m in self.machines if m.stage == s] for s in range(n)]

    def buffer_capacity(self, stage: int) -> int | None:
        for b in self.buffers:
            if b.before_stage == stage:
                return b.capacity
        return None

    def config_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json(exclude_none=True).encode()).hexdigest()

    def theoretical_capacity(self, available_s: float | None = None) -> dict[str, float]:
        """Ideal upper bound per stage: floor(T / CT) * count (docs/04); bottleneck = min over stages."""
        T = available_s if available_s is not None else self.simulation.duration_s
        caps = {}
        for stage in self.stages():
            caps[f"stage_{stage[0].stage}"] = sum(int(T // m.cycle_time_s) * m.count for m in stage)
        caps["bottleneck_stage"] = min(caps, key=caps.get) if caps else None
        caps["line_capacity"] = min(v for k, v in caps.items() if k.startswith("stage_")) if caps else 0
        return caps

    @classmethod
    def from_starter(cls, data: dict) -> "FactoryConfig":
        """Accept the starter examples/press_factory_demo.json layout (flat machines, run_kw/idle_kw)."""
        d = json.loads(json.dumps(data))
        machines = []
        for i, m in enumerate(d.get("machines", [])):
            if "power" not in m:
                m["power"] = {"run_kw": m.pop("run_kw"), "idle_kw": m.pop("idle_kw")}
            m.setdefault("stage", 0 if len(d["machines"]) > 1 and m.get("site") else i)
            machines.append(m)
        d["machines"] = machines
        faults = []
        for f in d.get("faults", []):
            if "kind" not in f:
                kind = {"SIMULATED_STOP": "MECHANICAL_STOP"}.get(f.pop("fault_type", "MECHANICAL_STOP"), "MECHANICAL_STOP")
                f.update(kind=kind, truth_class="PHYSICAL_STATE_ANOMALY", expected_safe_action="REQUEST_OPERATOR_REVIEW")
            faults.append(f)
        d["faults"] = faults
        if "simulation" in d:
            d["simulation"].setdefault("sample_interval_s", 10.0)
        return cls.model_validate(d)
