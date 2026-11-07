"""SF AI OS v3 — factory, plan, fleet, power and network inputs + seed-based scenario generator (S1–S15).

Every number here is a SYNTHETIC model assumption (documented in docs/v3/MODEL_ASSUMPTIONS.md), editable through the
Factory Setup form / API. Fault truth (actual repair duration etc.) is drawn from the seed and stored only in the
evaluator truth store; the operational side only ever sees the fault *code* and its catalogue ETA.
"""
from __future__ import annotations

import random
from enum import Enum

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "v3.0"
SHIFT_S = 8 * 3600


class Action(str, Enum):
    CONTINUE_MAIN = "CONTINUE_MAIN"
    RECOVER_MAIN = "RECOVER_MAIN"
    SWITCH_TO_SUB = "SWITCH_TO_SUB"
    RUN_PARALLEL = "RUN_PARALLEL"
    REPLAN = "REPLAN"
    REQUEST_TRANSPORT = "REQUEST_TRANSPORT"
    SAFE_STOP_REQUEST = "SAFE_STOP_REQUEST"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


ACTION_TEXT = {
    Action.CONTINUE_MAIN: "Keep the current routing; main line keeps producing",
    Action.RECOVER_MAIN: "Commit to main-line recovery: restart main as soon as the interlock reports a stable ready state (sub stops when main restarts)",
    Action.SWITCH_TO_SUB: "Start the validated backup (sub) line now; main stays off until a later decision",
    Action.RUN_PARALLEL: "Run main and sub lines at the same time to close the plan gap",
    Action.REPLAN: "Accept the forecast shortfall and notify planning; no equipment change",
    Action.REQUEST_TRANSPORT: "Call the reserve AMR from another area for pallets waiting because the line's AMRs are unavailable",
    Action.SAFE_STOP_REQUEST: "Request a controlled virtual stop of the main line (e.g. to stop producing defects)",
    Action.ESCALATE_HUMAN: "No reliable automated decision; page the operator with the incident and forecast",
}
MOTION_ACTIONS = {Action.RECOVER_MAIN, Action.SWITCH_TO_SUB, Action.RUN_PARALLEL, Action.REQUEST_TRANSPORT}


class LineSpec(BaseModel):
    line_id: str
    role: str = Field(description="main | sub")
    ct_s: float = Field(gt=0, description="nominal cycle time per unit [s]")
    ct_cv: float = Field(0.05, ge=0, le=0.5)
    yield_rate: float = Field(0.97, gt=0, le=1)
    startup_s: float = Field(0, ge=0, description="cold start / ramp-up before first unit")
    run_kw: float = Field(ge=0)
    idle_kw: float = Field(ge=0)
    off_kw: float = Field(0.0, ge=0)
    equipment_ids: list[str] = Field(default_factory=list)
    material_ready: bool = True
    initially_on: bool = True

    @field_validator("line_id")
    @classmethod
    def _virtual(cls, v: str) -> str:
        if any(ch in v for ch in (":", "/", "\\")) or v.lower().startswith(("opc", "mqtt", "tcp", "modbus")):
            raise ValueError("line ids must be virtual names, not addresses")
        return v


class PlanSpec(BaseModel):
    plan_id: str = "PLAN-1"
    product_id: str = "P01"
    target_good_qty: int = Field(ge=0)
    shift_s: int = Field(SHIFT_S, ge=600)
    priority: int = 1


class FleetSpec(BaseModel):
    n_amr: int = Field(2, ge=0)
    amr_speed_mps: float = Field(1.2, gt=0)
    n_reserve_amr: int = Field(1, ge=0, description="AMRs of another area that can be called in (REQUEST_TRANSPORT); unaffected by a local AMR outage")
    route_m: float = Field(120.0, gt=0, description="line-out → inspection/warehouse distance")
    handling_s: float = Field(40.0, ge=0)
    battery_kwh: float = Field(1.0, gt=0, description="AMR battery capacity (assumption, small indoor AMR)")
    motion_kw: float = Field(0.40, ge=0, description="traction + payload draw while driving (assumption)")
    idle_kw: float = Field(0.05, ge=0, description="standby draw of sensors/computer, always on (assumption)")
    battery_min_pct: float = Field(25.0, ge=0, le=100, description="below this the AMR must go to the charger after its trip")
    opportunity_pct: float = Field(50.0, ge=0, le=100, description="an idle AMR below this goes to the charger when no order is waiting")
    charger_kw: float = Field(0.6, gt=0, description="charger grid-side power (assumption)")
    charger_eff: float = Field(0.9, gt=0, le=1)

    def trip_pct(self) -> float:
        """Battery % used by one round trip (driving both legs at motion_kw)."""
        return 2 * self.route_m / self.amr_speed_mps * self.motion_kw / 3600 / self.battery_kwh * 100
    pallet_qty: int = Field(40, ge=1)
    out_buffer_pallets: int = Field(3, ge=1, description="pallets that fit at a line outlet before the line blocks")
    dest_capacity_pallets: int = Field(60, ge=1)


class PowerSpec(BaseModel):
    nameplate_limit_kw: float = Field(60.0, gt=0, description="static design limit known to every arm")
    aux_kw: float = Field(5.0, ge=0)
    trip_downtime_s: float = Field(600.0, ge=0, description="simulated feeder protection trip outage")


class NetworkSpec(BaseModel):
    median_delay_ms: float = 60.0
    jitter_ms: float = 20.0
    loss_p: float = 0.0
    dup_p: float = 0.0
    reorder_p: float = 0.0
    plc_sample_s: float = 10.0
    stale_after_s: float = 25.0


class FaultCode(BaseModel):
    code: str
    description: str
    eta_catalogue_s: float = Field(description="maintenance catalogue ETA shown to the decision layer")
    true_min_s: float
    true_max_s: float


FAULT_CATALOGUE = {
    "E101": FaultCode(code="E101", description="Material jam at press feed", eta_catalogue_s=600, true_min_s=360, true_max_s=900),
    "E201": FaultCode(code="E201", description="Safety guard / sensor fault", eta_catalogue_s=1800, true_min_s=900, true_max_s=3600),
    "E301": FaultCode(code="E301", description="Servo drive failure", eta_catalogue_s=7200, true_min_s=5400, true_max_s=9000),
    "E302": FaultCode(code="E302", description="Backup line hydraulic failure", eta_catalogue_s=10800, true_min_s=9000, true_max_s=14400),
}
STABILIZE_S = 300.0       # post-repair diagnostic window during which a restart is unsafe (interlock)
RECURRENCE_P_UNSTABLE = 0.6


class Fault(BaseModel):
    """Injected event. `truth_*` fields are evaluator-only (never copied into operational DB rows)."""
    fault_id: str
    kind: str
    at_s: float
    duration_s: float = 0.0
    target: str = "MAIN"
    params: dict = Field(default_factory=dict)
    truth_cause: str = "EQUIPMENT"
    truth_repair_s: float | None = None


class FactoryV3(BaseModel):
    factory_id: str = "SF-V3-DEMO"
    schema_version: str = SCHEMA_VERSION
    lines: list[LineSpec]
    plan: PlanSpec
    fleet: FleetSpec = FleetSpec()
    power: PowerSpec = PowerSpec()
    network: NetworkSpec = NetworkSpec()
    decision_interval_s: float = 60.0
    human_response_s: float = 600.0
    operator_policy: str = Field("planner", pattern="^(planner|rules)$", description="how the simulated operator answers an escalation (sensitivity: rules)")
    recurrence_p_unstable: float = Field(RECURRENCE_P_UNSTABLE, ge=0, le=1, description="re-fault probability after a restart inside the stabilisation window")
    ct_scale: float = Field(1.0, gt=0, description="sensitivity multiplier on every line's nominal cycle time")
    simulation_only: bool = True

    @field_validator("simulation_only")
    @classmethod
    def _sim_only(cls, v: bool) -> bool:
        if not v:
            raise ValueError("SF AI OS v3 is simulation-only: no physical equipment may be connected")
        return v

    def line(self, role: str) -> LineSpec:
        return next(l for l in self.lines if l.role == role)

    def capacity(self, role: str, seconds: float | None = None) -> float:
        l = self.line(role)
        return (seconds or self.plan.shift_s) / l.ct_s * l.yield_rate


def default_factory(target: int | None = None) -> FactoryV3:
    lines = [LineSpec(line_id="MAIN", role="main", ct_s=30.0, ct_cv=0.05, yield_rate=0.97, startup_s=120, run_kw=26.0, idle_kw=6.0, off_kw=0.5,
                      equipment_ids=["PRESS_M1", "CONV_M1", "CAM_M1"], initially_on=True),
             LineSpec(line_id="SUB", role="sub", ct_s=42.0, ct_cv=0.06, yield_rate=0.95, startup_s=600, run_kw=20.0, idle_kw=3.0, off_kw=0.3,
                      equipment_ids=["PRESS_S1", "CONV_S1", "CAM_S1"], initially_on=False)]
    f = FactoryV3(lines=lines, plan=PlanSpec(target_good_qty=target or 800))
    return f


LOADS = {"low": 0.75, "medium": 0.85, "high": 0.95}  # target = load × main-line nominal good capacity over the shift


class ScenarioInfo(BaseModel):
    sid: str
    title_ko: str
    title_en: str
    truth_cause: str
    expected: str
    note: str = ""


SCENARIOS: dict[str, ScenarioInfo] = {s.sid: s for s in [
    ScenarioInfo(sid="S1", title_ko="정상 가동", title_en="Normal operation", truth_cause="NONE", expected="CONTINUE_MAIN; periodic pallet transport"),
    ScenarioInfo(sid="S2", title_ko="메인 고장 — 빠른 복구", title_en="Main fault, fast recovery", truth_cause="EQUIPMENT", expected="RECOVER_MAIN (switching costs more than waiting)"),
    ScenarioInfo(sid="S3", title_ko="메인 장기 고장", title_en="Main long fault", truth_cause="EQUIPMENT", expected="SWITCH_TO_SUB (material/power OK)"),
    ScenarioInfo(sid="S4", title_ko="메인·서브 동시 불가", title_en="Main and sub both down", truth_cause="EQUIPMENT", expected="ESCALATE_HUMAN; no automatic motion"),
    ScenarioInfo(sid="S5", title_ko="메인 복구 후 복귀 판단", title_en="Main recovers while sub runs", truth_cause="EQUIPMENT", expected="return to main or run parallel after the interlock reports stable"),
    ScenarioInfo(sid="S6", title_ko="카운트 완료 → 운송", title_en="Batch count complete → transport", truth_cause="NONE", expected="exactly one MQTT transport order per completed pallet"),
    ScenarioInfo(sid="S7", title_ko="MQTT 중복·순서 뒤바뀜", title_en="MQTT duplicate / out-of-order", truth_cause="NETWORK", expected="no duplicate transport execution"),
    ScenarioInfo(sid="S8", title_ko="영상/QR/PLC 불일치 + 3초 지연", title_en="Vision/QR/PLC mismatch + 3 s delay", truth_cause="VISION_QR", expected="re-measure / human check; block risky actions"),
    ScenarioInfo(sid="S9", title_ko="전력 제한", title_en="Power limit", truth_cause="POWER", expected="no RUN_PARALLEL beyond the feeder limit"),
    ScenarioInfo(sid="S10", title_ko="과대 생산계획", title_en="Infeasible plan", truth_cause="PLAN", expected="predict the shortfall correctly and escalate"),
    ScenarioInfo(sid="S11", title_ko="C/T 상승(공구 마모)", title_en="Cycle-time drift (tool wear)", truth_cause="PROCESS_DRIFT", expected="RUN_PARALLEL or SWITCH when the plan is at risk"),
    ScenarioInfo(sid="S12", title_ko="불량 증가", title_en="Defect-rate surge", truth_cause="QUALITY", expected="stop producing defects: SAFE_STOP / RECOVER_MAIN / SWITCH_TO_SUB"),
    ScenarioInfo(sid="S13", title_ko="PLC 카운터 이상", title_en="PLC counter anomaly", truth_cause="COUNTER", expected="reconcile counts; no premature dispatch"),
    ScenarioInfo(sid="S14", title_ko="AMR 운행 불가", title_en="AMR fleet unavailable", truth_cause="TRANSPORT", expected="REQUEST_TRANSPORT (reserve AMR) before the outlet blocks"),
    ScenarioInfo(sid="RND", title_ko="랜덤 운영(매번 다른 상황)", title_en="Random course (different every run)", truth_cause="MIXED",
                 expected="whatever the random events require", note="1-4 random events of random type, time and severity"),
    ScenarioInfo(sid="S15", title_ko="복합 고장", title_en="Compound fault", truth_cause="EQUIPMENT", expected="SWITCH_TO_SUB within power headroom despite network delay"),
]}


def _repair(rng: random.Random, code: str) -> float:
    c = FAULT_CATALOGUE[code]
    return round(rng.uniform(c.true_min_s, c.true_max_s), 1)


def random_course(f: "FactoryV3", rng: random.Random) -> list[Fault]:
    """Seed-driven random operating course: 1–4 events drawn from the fault families with random start time, duration
    and severity. The decision layer never sees this list (evaluator truth only)."""
    T = f.plan.shift_s
    kinds = ["MAIN_FAULT", "CT_DRIFT", "DEFECT_RISE", "NETWORK_PLC", "NETWORK_MQTT", "AMR_DOWN", "POWER_LIMIT", "COUNTER_ANOMALY", "VISION_QR_MISMATCH", "SUB_FAULT"]
    weights = [0.28, 0.11, 0.14, 0.09, 0.06, 0.10, 0.08, 0.05, 0.05, 0.04]
    k = rng.choices([1, 2, 3, 4], weights=[0.25, 0.35, 0.25, 0.15])[0]
    picked: list[str] = []
    while len(picked) < k:
        x = rng.choices(kinds, weights=weights)[0]
        if x not in picked:
            picked.append(x)
    out: list[Fault] = []
    for i, kind in enumerate(picked):
        at = round(rng.uniform(1800, T - 3600), 1)
        if kind == "MAIN_FAULT":
            code = rng.choices(["E101", "E201", "E301"], weights=[0.4, 0.35, 0.25])[0]
            out.append(Fault(fault_id=f"R{i}-MAIN-{code}", kind="MAIN_FAULT", at_s=at, params=dict(code=code), truth_repair_s=_repair(rng, code)))
        elif kind == "SUB_FAULT":
            out.append(Fault(fault_id=f"R{i}-SUB-E302", kind="SUB_FAULT", at_s=at, target="SUB", params=dict(code="E302"), truth_repair_s=_repair(rng, "E302")))
        elif kind == "CT_DRIFT":
            out.append(Fault(fault_id=f"R{i}-CT", kind="CT_DRIFT", at_s=at, duration_s=T, params=dict(factor=round(rng.uniform(1.15, 1.4), 2), ramp_s=rng.uniform(1200, 3000), fixable_by_repair_s=1200), truth_cause="PROCESS_DRIFT"))
        elif kind == "DEFECT_RISE":
            out.append(Fault(fault_id=f"R{i}-DEFECT", kind="DEFECT_RISE", at_s=at, duration_s=T, params=dict(defect_p=round(rng.uniform(0.12, 0.35), 2), fixable_by_repair_s=1200), truth_cause="QUALITY"))
        elif kind in ("NETWORK_PLC", "NETWORK_MQTT"):
            tgt = "PLC" if kind == "NETWORK_PLC" else "MQTT"
            out.append(Fault(fault_id=f"R{i}-NET-{tgt}", kind="NETWORK", at_s=at, duration_s=rng.uniform(1200, 5400), target=tgt,
                             params=dict(delay_ms=rng.uniform(1500, 5000), jitter_ms=rng.uniform(300, 1500), loss_p=rng.uniform(0.0, 0.08), dup_p=rng.uniform(0.0, 0.2) if tgt == "MQTT" else 0.0,
                                         reorder_p=rng.uniform(0.0, 0.2) if tgt == "MQTT" else 0.0), truth_cause="NETWORK"))
        elif kind == "AMR_DOWN":
            out.append(Fault(fault_id=f"R{i}-AMR", kind="AMR_DOWN", at_s=at, duration_s=rng.uniform(1800, 7200), target="FLEET", truth_cause="TRANSPORT"))
        elif kind == "POWER_LIMIT":
            out.append(Fault(fault_id=f"R{i}-POWER", kind="POWER_LIMIT", at_s=at, duration_s=rng.uniform(3600, T), target="FEEDER", params=dict(available_kw=round(rng.uniform(38, 50))), truth_cause="POWER"))
        elif kind == "COUNTER_ANOMALY":
            out.append(Fault(fault_id=f"R{i}-COUNTER", kind="COUNTER_ANOMALY", at_s=at, duration_s=rng.uniform(1800, 7200), params=dict(dup_edge_p=round(rng.uniform(0.15, 0.4), 2), reset_at_s=at + rng.uniform(300, 1800)), truth_cause="COUNTER"))
        elif kind == "VISION_QR_MISMATCH":
            out.append(Fault(fault_id=f"R{i}-VQR", kind="VISION_QR_MISMATCH", at_s=at, duration_s=rng.uniform(1800, 5400), params=dict(qr_mismatch_p=round(rng.uniform(0.2, 0.6), 2), vision_miss_p=round(rng.uniform(0.05, 0.2), 2)), truth_cause="VISION_QR"))
    return out


def build_scenario(sid: str, load: str = "medium", seed: int = 0, factory: FactoryV3 | None = None) -> tuple[FactoryV3, list[Fault]]:
    """Same (sid, load, seed) ⇒ identical factory and fault sequence for every arm (paired replay)."""
    f = (factory or default_factory()).model_copy(deep=True)
    rng = random.Random(f"{sid}|{load}|{seed}")
    f.plan.target_good_qty = int(round(LOADS[load] * f.capacity("main")))
    t0 = rng.uniform(3600, 5400)  # first incident between 1.0 h and 1.5 h into the shift
    faults: list[Fault] = []
    if sid == "S1" or sid == "S6":
        pass
    elif sid == "S2":
        faults.append(Fault(fault_id="F-MAIN-E101", kind="MAIN_FAULT", at_s=t0, params=dict(code="E101"), truth_repair_s=_repair(rng, "E101")))
    elif sid == "S3":
        faults.append(Fault(fault_id="F-MAIN-E301", kind="MAIN_FAULT", at_s=t0, params=dict(code="E301"), truth_repair_s=_repair(rng, "E301")))
    elif sid == "S4":
        faults.append(Fault(fault_id="F-MAIN-E301", kind="MAIN_FAULT", at_s=t0, params=dict(code="E301"), truth_repair_s=_repair(rng, "E301")))
        faults.append(Fault(fault_id="F-SUB-E302", kind="SUB_FAULT", at_s=t0 - 60, target="SUB", params=dict(code="E302"), truth_repair_s=_repair(rng, "E302")))
    elif sid == "S5":
        rep = rng.uniform(3600, 4800)  # main comes back mid-shift → the return-to-main decision matters
        faults.append(Fault(fault_id="F-MAIN-E201", kind="MAIN_FAULT", at_s=t0, params=dict(code="E201"), truth_repair_s=round(rep, 1)))
    elif sid == "S7":
        faults.append(Fault(fault_id="F-NET-MQTT", kind="NETWORK", at_s=1800, duration_s=f.plan.shift_s, target="MQTT",
                            params=dict(delay_ms=4500, jitter_ms=1500, dup_p=0.25, reorder_p=0.2, loss_p=0.05), truth_cause="NETWORK"))
    elif sid == "S8":
        faults.append(Fault(fault_id="F-VQR", kind="VISION_QR_MISMATCH", at_s=t0, duration_s=5400, params=dict(qr_mismatch_p=0.5, vision_miss_p=0.15), truth_cause="VISION_QR"))
        faults.append(Fault(fault_id="F-NET-PLC", kind="NETWORK", at_s=t0, duration_s=5400, target="PLC", params=dict(delay_ms=3500, jitter_ms=800, loss_p=0.05), truth_cause="NETWORK"))
    elif sid == "S9":
        faults.append(Fault(fault_id="F-CT-DRIFT", kind="CT_DRIFT", at_s=t0 - 1800, duration_s=f.plan.shift_s, params=dict(factor=1.30, ramp_s=1800, fixable_by_repair_s=1200), truth_cause="PROCESS_DRIFT"))
        faults.append(Fault(fault_id="F-POWER", kind="POWER_LIMIT", at_s=t0 - 600, duration_s=f.plan.shift_s, target="FEEDER", params=dict(available_kw=44.0), truth_cause="POWER"))
    elif sid == "S10":
        f.plan.target_good_qty = int(round(1.05 * (f.capacity("main") + f.capacity("sub", f.plan.shift_s - 900))))
    elif sid == "S11":
        faults.append(Fault(fault_id="F-CT-DRIFT", kind="CT_DRIFT", at_s=t0 - 1800, duration_s=f.plan.shift_s, params=dict(factor=1.35, ramp_s=2400, fixable_by_repair_s=1200), truth_cause="PROCESS_DRIFT"))
    elif sid == "S12":
        faults.append(Fault(fault_id="F-DEFECT", kind="DEFECT_RISE", at_s=t0, duration_s=f.plan.shift_s, params=dict(defect_p=0.30, fixable_by_repair_s=1200), truth_cause="QUALITY"))
    elif sid == "S13":
        faults.append(Fault(fault_id="F-COUNTER", kind="COUNTER_ANOMALY", at_s=t0, duration_s=7200, target="MAIN", params=dict(dup_edge_p=0.35, reset_at_s=t0 + 1800), truth_cause="COUNTER"))
    elif sid == "S14":
        faults.append(Fault(fault_id="F-AMR", kind="AMR_DOWN", at_s=t0, duration_s=7200, target="FLEET", truth_cause="TRANSPORT"))
    elif sid == "RND":
        faults += random_course(f, rng)
    elif sid == "S15":
        faults.append(Fault(fault_id="F-MAIN-E301", kind="MAIN_FAULT", at_s=t0, params=dict(code="E301"), truth_repair_s=_repair(rng, "E301")))
        faults.append(Fault(fault_id="F-NET-PLC", kind="NETWORK", at_s=t0 - 300, duration_s=7200, target="PLC", params=dict(delay_ms=2500, jitter_ms=900, loss_p=0.08), truth_cause="NETWORK"))
        faults.append(Fault(fault_id="F-POWER", kind="POWER_LIMIT", at_s=t0 - 300, duration_s=f.plan.shift_s, target="FEEDER", params=dict(available_kw=40.0), truth_cause="POWER"))
    else:
        raise KeyError(sid)
    return f, faults
