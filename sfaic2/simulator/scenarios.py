"""Demo factory presets and the three representative Command Center scenarios (prompt §[1] F) plus the
eight fault families of docs/04. All SIMULATED; parameter values are illustrative assumptions."""
from __future__ import annotations

from ..contracts.enums import FaultKind, TruthClass
from ..contracts.factory import (Buffer, ElectricalSpec, FactoryConfig, Fault, Machine, NetworkSpec, PowerSpec,
                                 ProductionPlan, SimulationSpec)


def demo_factory(scenario_id: str = "press_line_demo", quantity: int = 1000, duration_s: int = 28800, seed: int = 42,
                 press_ct: float = 45.0, faults: list[Fault] | None = None) -> FactoryConfig:
    """Serial 3-stage line: PRESS (2 machines) → WELD (1) → QA_PACK (1), finite buffers, one LV feeder pair."""
    return FactoryConfig(
        scenario_id=scenario_id, simulation=SimulationSpec(duration_s=duration_s, seed=seed, sample_interval_s=10.0),
        production_plan=ProductionPlan(product_id="PART_A", quantity=quantity, shift_duration_s=28800),
        machines=[
            Machine(machine_id="PRESS_01", work_center_id="WC_PRESS", stage=0, count=1, cycle_time_s=press_ct, yield_rate=0.985,
                    power=PowerSpec(run_kw=23.0, idle_kw=3.0), feeder_id="FEEDER_1", site="PRESS_LINE"),
            Machine(machine_id="PRESS_02", work_center_id="WC_PRESS", stage=0, count=1, cycle_time_s=press_ct + 5, yield_rate=0.985,
                    power=PowerSpec(run_kw=22.0, idle_kw=2.5), feeder_id="FEEDER_1", site="PRESS_LINE"),
            Machine(machine_id="WELD_01", work_center_id="WC_WELD", stage=1, count=1, cycle_time_s=20.0, yield_rate=0.99,
                    power=PowerSpec(run_kw=18.0, idle_kw=1.5), feeder_id="FEEDER_2", site="WELD_CELL"),
            Machine(machine_id="QA_PACK_01", work_center_id="WC_QA", stage=2, count=1, cycle_time_s=18.0, yield_rate=0.995,
                    power=PowerSpec(run_kw=4.0, idle_kw=0.8), feeder_id="FEEDER_2", site="QA_GATE"),
        ],
        buffers=[Buffer(buffer_id="B_IN", before_stage=0, capacity=20), Buffer(buffer_id="B_WELD", before_stage=1, capacity=12),
                 Buffer(buffer_id="B_QA", before_stage=2, capacity=12)],
        faults=faults or [], network=NetworkSpec(median_latency_ms=50, jitter_ms=10, loss_probability=0.01, tag_ttl_ms=500),
        electrical=ElectricalSpec(feeders=["FEEDER_1", "FEEDER_2"], transformer_sn_mva=0.25),
    )


def fault_preset(kind: FaultKind, at_s: float, duration_s: float, machine_id: str = "PRESS_01", severity: float = 1.0,
                 fault_id: str | None = None) -> Fault:
    table = {
        FaultKind.MECHANICAL_OVERLOAD: (TruthClass.PHYSICAL_STATE_ANOMALY, "REQUEST_OPERATOR_REVIEW", False),
        FaultKind.MECHANICAL_STOP: (TruthClass.PHYSICAL_STATE_ANOMALY, "REQUEST_SIMULATED_STOP", True),
        FaultKind.NETWORK_DELAY: (TruthClass.COMMUNICATION_DELAY, "REQUEST_RECHECK", False),
        FaultKind.SENSOR_FAULT: (TruthClass.SENSOR_FALSE_READING, "REQUEST_RECHECK", False),
        FaultKind.ELECTRICAL_LOADING: (TruthClass.ELECTRICAL_QUALITY, "REQUEST_OPERATOR_REVIEW", False),
        FaultKind.QR_MISROUTE: (TruthClass.PROCESS_POLICY_VIOLATION, "REQUEST_OPERATOR_REVIEW", False),
        FaultKind.VISION_FALSE_NEGATIVE: (TruthClass.PERCEPTION_ERROR, "REQUEST_RECHECK", False),
        FaultKind.MULTI_CAUSE: (TruthClass.PHYSICAL_STATE_ANOMALY, "REQUEST_OPERATOR_REVIEW", True),
        FaultKind.VISION_DEFECT: (TruthClass.PHYSICAL_STATE_ANOMALY, "REQUEST_OPERATOR_REVIEW", False),
    }
    tc, action, harmful = table[kind]
    return Fault(fault_id=fault_id or f"{kind.value.lower()}_{int(at_s)}", at_s=at_s, duration_s=duration_s, kind=kind,
                 machine_id=machine_id, severity=severity, truth_class=tc, expected_safe_action=action, harmful_stop_warranted=harmful)


FAULT_FAMILIES = {f"F{i+1:02d}": k for i, k in enumerate([FaultKind.MECHANICAL_OVERLOAD, FaultKind.MECHANICAL_STOP, FaultKind.NETWORK_DELAY,
                                                        FaultKind.SENSOR_FAULT, FaultKind.ELECTRICAL_LOADING, FaultKind.QR_MISROUTE,
                                                        FaultKind.VISION_FALSE_NEGATIVE, FaultKind.MULTI_CAUSE, FaultKind.VISION_DEFECT])}


def scenario_1_overheat(seed=42, duration_s=3600) -> FactoryConfig:
    """(1) 설비 과열/고부하 + 양품 감소: overload on PRESS_01 raises current/temperature and CT."""
    return demo_factory("S1_overload_quality_drop", quantity=400, duration_s=duration_s, seed=seed,
                        faults=[fault_preset(FaultKind.MECHANICAL_OVERLOAD, 600, 1500, "PRESS_01", 1.5)])


def scenario_2_contradiction(seed=42, duration_s=3600) -> FactoryConfig:
    """(2) PLC RUNNING vs current 0 / camera no part + 통신 지연: sensor stuck + gateway delay on PRESS_01."""
    return demo_factory("S2_plc_vs_sensor_delay", quantity=400, duration_s=duration_s, seed=seed,
                        faults=[fault_preset(FaultKind.SENSOR_FAULT, 600, 900, "PRESS_01", 1.0, "sensor_stuck_600"),
                                fault_preset(FaultKind.NETWORK_DELAY, 700, 600, "PRESS_01", 1.0, "gateway_delay_700"),
                                fault_preset(FaultKind.VISION_FALSE_NEGATIVE, 800, 300, "QA_PACK_01", 1.0, "vision_fn_800")])


def scenario_3_qr_misroute(seed=42, duration_s=3600) -> FactoryConfig:
    """(3) QR 정상 디코딩이지만 MES routing 불일치."""
    return demo_factory("S3_qr_routing_mismatch", quantity=400, duration_s=duration_s, seed=seed,
                        faults=[fault_preset(FaultKind.QR_MISROUTE, 900, 600, "QA_PACK_01", 1.0)])


def scenario_4_vision_defect(seed=42, duration_s=3600) -> FactoryConfig:
    """(4) 비전(YOLO) 계층이 표면 결함을 검출, PLC/전류는 정상 → 신호 → PLC → MQTT → JEV 판단 → 재검사/검토."""
    return demo_factory("S4_vision_defect_detected", quantity=400, duration_s=duration_s, seed=seed,
                        faults=[Fault(fault_id="vision_defect_900", at_s=900, duration_s=900, kind=FaultKind.VISION_DEFECT, machine_id="QA_PACK_01",
                                      severity=1.0, truth_class=TruthClass.PHYSICAL_STATE_ANOMALY, expected_safe_action="REQUEST_OPERATOR_REVIEW",
                                      params={"defect_class": "surface_scratch", "defect_recall": _measured_recall()})])


def _measured_recall() -> float:
    """Image-level recall of the fine-tuned YOLO11n on held-out MVTec AD images (results/vision/summary.json); 1.0 if not measured yet."""
    from ..adapters.factory_vision import measured_defect_recall
    return measured_defect_recall(1.0)


def scenario_5_electrical(seed=42, duration_s=3600) -> FactoryConfig:
    """(5) 변압기 과부하·저전압: 피더에 외부 부하가 더해져 LV 전압이 0.95 pu 아래로 내려감. 전기 맥락이 있는 arm E만 원인을 electrical_loading으로 분류할 수 있음."""
    cfg = demo_factory("S5_electrical_undervoltage", quantity=400, duration_s=duration_s, seed=seed,
                       faults=[fault_preset(FaultKind.ELECTRICAL_LOADING, 600, 1500, "PRESS_01", 2.5, "feeder_overload_600")])
    cfg.electrical.transformer_sn_mva = 0.16  # smaller DEMO transformer so the excursion crosses the 0.95 pu / 90 % thresholds
    return cfg


def scenario_6_gateway_outage(seed=42, duration_s=3600) -> FactoryConfig:
    """(6) 게이트웨이 장기 지연 + 패킷 유실 급증 → 모든 PRESS 태그 stale. 물리 상태는 정상. 정지 금지, 재확인."""
    cfg = demo_factory("S6_gateway_outage", quantity=400, duration_s=duration_s, seed=seed,
                       faults=[fault_preset(FaultKind.NETWORK_DELAY, 600, 900, "PRESS_01", 2.0, "gw_delay_press01"),
                               fault_preset(FaultKind.NETWORK_DELAY, 600, 900, "PRESS_02", 2.0, "gw_delay_press02")])
    cfg.network.loss_probability = 0.08
    return cfg


def scenario_7_multi_cause(seed=42, duration_s=3600) -> FactoryConfig:
    """(7) 실제 설비 정지 + 통신 지연이 겹침(F08): 정지는 정당하지만 증거가 stale → 재확인 후 정지 승인 흐름."""
    return demo_factory("S7_stop_with_comms_delay", quantity=400, duration_s=duration_s, seed=seed,
                        faults=[fault_preset(FaultKind.MULTI_CAUSE, 900, 900, "PRESS_01", 1.0, "multi_cause_900")])


def scenario_8_plan_shock(seed=42, duration_s=3600) -> FactoryConfig:
    """(8) 계획 1000→1200 상향 + 과부하: 병목·전력 피크·계획 미달이 동시에 변함(계획 연계 트윈 검증)."""
    return demo_factory("S8_plan_shock_overload", quantity=1200, duration_s=duration_s, seed=seed, press_ct=40.0,
                        faults=[fault_preset(FaultKind.MECHANICAL_OVERLOAD, 1200, 1200, "PRESS_02", 1.2, "overload_press02")])


SCENARIOS = {"S1": scenario_1_overheat, "S2": scenario_2_contradiction, "S3": scenario_3_qr_misroute, "S4": scenario_4_vision_defect,
             "S5": scenario_5_electrical, "S6": scenario_6_gateway_outage, "S7": scenario_7_multi_cause, "S8": scenario_8_plan_shock}

# Rich scenario narratives for the Command Center (signal chain = equipment signal → virtual PLC → MQTT → JEV → decision → MQTT → PLC → equipment)
SCENARIO_INFO = {
    "S1": dict(title_ko="설비 과열·고부하와 양품 감소", title_en="Overheat and overload with quality drop",
               summary_ko="PRESS_01의 토크 과부하로 전류와 온도가 상승하고 사이클타임이 늘어나 양품 생산이 감소합니다. PLC 태그와 전력 모델이 일관되게 이상을 가리키므로 운영자 검토 권고가 기대됩니다.",
               summary_en="Torque overload on PRESS_01 raises current and temperature and stretches the cycle time, lowering good output. PLC tags and the power model agree, so an operator-review recommendation is expected.",
               fault="F01 MECHANICAL_OVERLOAD @ 600 s for 1500 s on PRESS_01", expected="REQUEST_OPERATOR_REVIEW · cause physical_process_issue",
               chain=["센서(전류·온도) → 가상 PLC 태그", "PLC → MQTT 텔레메트리(지연·유실 모델)", "증거 저장소: 신선도·교차 검증", "JEV 판단(원인·검토 필요·위험도)", "Safety Gate → 가상 명령 → PLC ack"]),
    "S2": dict(title_ko="PLC 가동 중 전류 0 A·카메라 미검출·통신 지연", title_en="PLC running, current 0 A, camera no part, network delay",
               summary_ko="전류 센서가 0 A에 고착되고 게이트웨이 지연으로 PLC 태그가 오래되며, 카메라는 부품을 놓칩니다. 물리 설비는 정상이므로 올바른 판단은 정지가 아니라 재확인입니다. 순진한 규칙(arm A)은 가짜 정지를 요청합니다.",
               summary_en="The current sensor sticks at 0 A, gateway delay makes PLC tags stale and the camera misses parts. The plant is physically fine, so the right call is a recheck, not a stop; naive rules (arm A) request a false stop.",
               fault="F04 SENSOR_FAULT @ 600 s + F03 NETWORK_DELAY @ 700 s on PRESS_01, F07 VISION_FALSE_NEGATIVE @ 800 s on QA_PACK_01", expected="REQUEST_RECHECK · cause sensor_issue / communications_issue",
               chain=["센서 고착(0 A) + YOLO 미검출 신호", "PLC 태그가 MQTT를 거치며 stale 처리", "증거 모순 검출: RUNNING vs 0 A, 전력 23 kW", "JEV: communications/sensor issue 우세, stop_warranted 낮음", "Safety Gate: stale 증거로는 정지 불가"]),
    "S3": dict(title_ko="QR 정상 디코딩, MES 라우팅 불일치", title_en="QR decoded, MES routing mismatch",
               summary_ko="QR은 정상 디코딩되지만 팔레트가 기대 포트와 다른 곳에서 관측됩니다. 설비 신호는 정상이므로 공정 규칙 위반으로 분류되어 운영자 검토가 권고됩니다.",
               summary_en="QR decodes correctly but pallets appear at an unexpected port. Equipment signals are nominal, so the case is classified as a process-routing violation and routed to operator review.",
               fault="F06 QR_MISROUTE @ 900 s for 600 s on QA_PACK_01", expected="REQUEST_OPERATOR_REVIEW · cause process_routing_issue",
               chain=["QR 리더 신호(payload·관측 포트)", "라우팅 규칙 검사(DB/규칙 엔진)", "MQTT → 증거 저장소", "JEV: process_routing_issue", "운영자 검토(정지 아님)"]),
    "S4": dict(title_ko="비전(YOLO) 결함 검출 → JEV 판단 → 재검사", title_en="Vision (YOLO) defect detection → JEV judgment → re-inspection",
               summary_ko="카메라 게이트의 YOLO 계층이 표면 결함을 검출합니다. PLC·전류·전력은 모두 정상이므로 설비 정지가 아니라 품질 검토가 적절합니다. 비전 신호가 PLC·MQTT를 거쳐 JEV에 도달하고, 판단이 다시 MQTT·PLC를 거쳐 설비 측 재검사 요청으로 돌아오는 전체 경로를 보여줍니다.",
               summary_en="The YOLO layer at the camera gate reports surface defects while PLC, current and power stay nominal, so quality review rather than a stop is appropriate. Shows the full path: vision signal → PLC → MQTT → JEV → decision → MQTT → PLC → re-inspection request.",
               fault="F09 VISION_DEFECT @ 900 s for 900 s on QA_PACK_01", expected="REQUEST_OPERATOR_REVIEW · cause quality_issue",
               chain=["YOLO 추론(결함 클래스·confidence) → DERIVED 이벤트", "카메라 게이트 → PLC → MQTT", "증거: VISION_DEFECT_REPORTED (설비 신호는 정상)", "JEV: quality_issue, review_needed 높음, stop_warranted 낮음", "판단 → MQTT → PLC: 재검사 요청(정지 없음)"]),
    "S5": dict(title_ko="변압기 과부하·저전압 (전기 맥락)", title_en="Transformer overload and undervoltage (electrical context)",
               summary_ko="피더에 외부 부하가 추가되어 LV 모선 전압이 0.95 pu 아래로 내려가고 변압기 부하율이 90 %를 넘습니다. 설비 신호만 보면 원인이 보이지 않으며, 전력 모델 맥락(arm E)이 있어야 electrical_loading으로 분류됩니다.",
               summary_en="External load on the feeder pulls the LV bus below 0.95 pu and transformer loading above 90 %. Equipment signals alone do not show the cause; only the electrical context (arm E) classifies it as electrical_loading.",
               fault="F05 ELECTRICAL_LOADING severity 2.5 @ 600 s for 1500 s (FEEDER_1), DEMO transformer 160 kVA", expected="REQUEST_OPERATOR_REVIEW · cause electrical_loading",
               chain=["전력계(조류 계산) → bus_voltage_pu·transformer_loading_pct 이벤트", "MQTT → 증거 저장소(전기 맥락)", "규칙: ELECTRICAL_EXCURSION", "JEV: electrical_loading", "운영자 검토(설비 정지 아님)"]),
    "S6": dict(title_ko="게이트웨이 장기 지연·패킷 유실 급증", title_en="Gateway delay with heavy packet loss",
               summary_ko="두 프레스의 PLC 태그가 길게 지연되고 유실률이 8 %로 올라 증거가 대부분 stale 처리됩니다. 물리 상태는 정상이며, 올바른 판단은 재확인이고 정지는 Safety Gate에서 차단되어야 합니다.",
               summary_en="PLC tags from both presses arrive late with 8 % loss, so most evidence is stale. The plant is physically fine; the right call is a recheck and any stop must be blocked by the Safety Gate.",
               fault="F03 NETWORK_DELAY severity 2.0 @ 600 s for 900 s on PRESS_01 and PRESS_02; loss 8 %", expected="REQUEST_RECHECK · cause communications_issue",
               chain=["PLC 태그 → MQTT(지연 3 s, 유실 8 %)", "증거: stale/missing 다수, reliability 저하", "JEV: communications_issue, stop_warranted 낮음", "Safety Gate: stale 증거 → 정지 거절", "재확인 요청"]),
    "S7": dict(title_ko="실제 정지 + 통신 지연 중첩", title_en="Real stop overlapping with communication delay",
               summary_ko="PRESS_01이 실제로 정지했지만 동시에 게이트웨이 지연으로 증거가 stale 상태입니다. 정지는 정당하지만 신선한 증거가 올 때까지 승인되지 않아야 하며, 지연 해소 후 정지 승인·ack가 이어집니다.",
               summary_en="PRESS_01 really stops while gateway delay makes the evidence stale. The stop is warranted but must not be approved until fresh evidence arrives; after the delay clears, approval and PLC ack follow.",
               fault="F08 MULTI_CAUSE @ 900 s for 900 s on PRESS_01", expected="REQUEST_OPERATOR_REVIEW → REQUEST_SIMULATED_STOP after fresh evidence",
               chain=["PLC FAULT 태그(지연 전달)", "증거: FAULT + stale", "JEV: physical_process_issue, review 높음", "Safety Gate: stale 동안 보류 → 신선해지면 승인", "가상 PLC STOP_REQUESTED → STOPPING → STOPPED"]),
    "S8": dict(title_ko="계획 상향(1200) + 프레스 과부하", title_en="Plan increase to 1200 with press overload",
               summary_ko="계획수량을 1200으로 올리고 C/T를 40 s로 당긴 상태에서 PRESS_02가 과부하됩니다. 병목·전력 피크·계획 미달이 동시에 바뀌는 계획 연계 트윈의 거동을 검증합니다.",
               summary_en="With the plan raised to 1200 and C/T tightened to 40 s, PRESS_02 overloads. Verifies the plan-linked twin: bottleneck, power peak and plan gap change together.",
               fault="F01 MECHANICAL_OVERLOAD severity 1.2 @ 1200 s for 1200 s on PRESS_02; plan 1200, press C/T 40 s", expected="REQUEST_OPERATOR_REVIEW · cause physical_process_issue",
               chain=["계획 변경 → 생산 트윈(WIP·병목·plan gap)", "전력 피크 → 조류 계산", "과부하 신호 → PLC → MQTT", "JEV: physical_process_issue", "운영자 검토, 계획 재조정"]),
}
