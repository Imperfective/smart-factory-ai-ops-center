"""JEV anomaly-judgment design, served from the code that actually runs (not from prose), plus the measured evidence
for it from the research DB: how equipment anomalies and network/sensing anomalies are told apart, what JEV is asked,
how its answers become actions, and how often each arm got each anomaly domain right."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ..adapters.typesafe_jev import QUESTIONS
from ..contracts.enums import FaultKind
from ..decision.engine import ARMS
from ..decision.providers import HOT_C, OVERLOAD_A
from ..simulator.scenarios import FAULT_FAMILIES, fault_preset
from ..study.core import TRUTH_TO_CAUSE
from .db import DB_PATH, _rows, connect

DOMAIN = {  # anomaly domain each fault kind belongs to (what the paper calls equipment vs network/sensing anomalies)
    "MECHANICAL_OVERLOAD": "equipment", "MECHANICAL_STOP": "equipment", "MULTI_CAUSE": "equipment", "VISION_DEFECT": "equipment",
    "NETWORK_DELAY": "network_sensing", "SENSOR_FAULT": "network_sensing", "VISION_FALSE_NEGATIVE": "network_sensing",
    "ELECTRICAL_LOADING": "electrical", "QR_MISROUTE": "process", "NONE": "normal",
}
DOMAIN_LABEL = {"equipment": ("설비 이상 (물리)", "Equipment anomaly (physical)"), "network_sensing": ("네트워크·센서 이상 (관측)", "Network / sensing anomaly (observation)"),
                "electrical": ("전기 계통 이상", "Electrical anomaly"), "process": ("공정/라우팅 이상", "Process / routing anomaly"), "normal": ("정상", "Normal")}

# Expected evidence signature per fault kind — the design hypothesis each family tests (docs/13). The DB section below
# shows the *observed* signature (stale/conflict counts during the fault window) next to it.
SIGNATURE = {
    "MECHANICAL_OVERLOAD": dict(ko="전류·온도 상승이 신선하고 서로 일관됨 (PLC RUNNING, 모순 없음)", en="Current and temperature rise, fresh and mutually consistent (PLC RUNNING, no conflicts)",
                                cause="physical_process_issue"),
    "MECHANICAL_STOP": dict(ko="PLC FAULT/정지 + 전류 0 이 함께 신선하게 관측 → 실제 정지", en="PLC FAULT/stopped and zero current, both fresh → a real stop", cause="physical_process_issue"),
    "MULTI_CAUSE": dict(ko="물리 이상과 센서 이상이 동시에 — 일부 신호는 모순", en="Physical and sensor faults at once — some signals contradict", cause="physical_process_issue"),
    "VISION_DEFECT": dict(ko="카메라(YOLO) 결함 보고, PLC·전류·전력은 정상", en="Camera (YOLO) reports defects while PLC, current and power are nominal", cause="quality_issue"),
    "NETWORK_DELAY": dict(ko="태그 age 증가·stale 다수, 물리값 자체는 정상 범위", en="Tag age grows and many tags go stale; physical values stay in range", cause="communications_issue"),
    "SENSOR_FAULT": dict(ko="PLC RUNNING·전력 정상인데 전류 0 (PLC_RUNNING_BUT_CURRENT_ZERO / CURRENT_ZERO_BUT_POWER_HIGH)", en="PLC RUNNING and power normal but current 0 (PLC_RUNNING_BUT_CURRENT_ZERO / CURRENT_ZERO_BUT_POWER_HIGH)",
                         cause="sensor_issue"),
    "VISION_FALSE_NEGATIVE": dict(ko="설비 RUNNING인데 카메라가 부품 미검출 (RUNNING_BUT_CAMERA_NO_PART)", en="Equipment RUNNING but camera sees no part (RUNNING_BUT_CAMERA_NO_PART)", cause="sensor_issue"),
    "ELECTRICAL_LOADING": dict(ko="LV 모선 전압 < 0.95 pu 또는 변압기 부하율 > 90 % (전기 맥락이 있어야 보임)", en="LV bus < 0.95 pu or transformer > 90 % (visible only with electrical context)",
                               cause="electrical_loading"),
    "QR_MISROUTE": dict(ko="QR 해독은 정상이나 경로 불일치 (QR_ROUTE_INCONSISTENT)", en="QR decodes but the route is inconsistent (QR_ROUTE_INCONSISTENT)", cause="process_routing_issue"),
}

CONFLICT_RULES = [
    ("PLC_RUNNING_BUT_CURRENT_ZERO", "PLC=RUNNING ∧ current < 0.5 A (same telemetry batch ≤ 0.5 s)"),
    ("PLC_NOT_RUNNING_BUT_CURRENT_HIGH", "PLC ∈ {IDLE, STOPPED, FAULT} ∧ current > idle current"),
    ("CURRENT_ZERO_BUT_POWER_HIGH", "current < 0.5 A ∧ active power > 5 kW (same batch)"),
    ("RUNNING_BUT_CAMERA_NO_PART", "PLC=RUNNING ∧ every camera event in the window reports no part"),
    ("QR_ROUTE_INCONSISTENT", "decoded pallet QR observed at a port outside its MES route"),
    ("VISION_DEFECT_REPORTED", "camera-gate YOLO event with defect=true"),
]
POLICY_V2 = [
    ("P1", "stop_warranted ≥ 0.7 ∧ cause = physical_process_issue ∧ no stale signals", "REQUEST_SIMULATED_STOP (still needs Safety Gate + rules agreement + operator)"),
    ("P2", "cause ∈ {sensor_issue, communications_issue}", "REQUEST_RECHECK (never stop on a suspect observation)"),
    ("P3", "review_needed ≥ 0.5 ∨ cause ∈ {unknown, physical, routing, electrical, quality}", "REQUEST_OPERATOR_REVIEW"),
    ("P4", "otherwise", "CONTINUE_MONITORING"),
]
FUSION = [
    "가장 보수적인 비정지 권고가 채택됨 / the most conservative non-stop recommendation wins",
    "ML 단독 경보는 최대 RECHECK / an ML-only alarm is capped at RECHECK",
    "STOP은 (D/E) 신선·무모순·신뢰도 ≥ 0.75 증거 + 규칙 엔진 동의가 있어야 유지, 아니면 REVIEW로 강등 / STOP survives only with fresh, conflict-free evidence (reliability ≥ 0.75) and rules agreement (D/E), else downgraded to REVIEW",
    "JEV는 실행 권한이 없음: Safety Gate가 가상 PLC 명령 전 증거 해시·운영자 승인·멱등 ID를 검사 / JEV never actuates: the Safety Gate checks evidence hash, operator approval and idempotent id before any virtual PLC command",
]


def design_spec() -> dict:
    kinds = []
    for fam, kind in FAULT_FAMILIES.items():
        f = fault_preset(kind, 0, 1)
        kinds.append(dict(family=fam, kind=kind.value, domain=DOMAIN.get(kind.value), truth_class=f.truth_class.value, expected_action=f.expected_safe_action,
                          harmful_stop_warranted=f.harmful_stop_warranted, expected_cause=SIGNATURE[kind.value]["cause"], signature_ko=SIGNATURE[kind.value]["ko"],
                          signature_en=SIGNATURE[kind.value]["en"]))
    arms = [dict(arm=k, label=v["label"], ml=v["ml"], jev=v["jev"], evidence_checks=v["evidence_checks"], context=v["context"], none=v.get("none", False)) for k, v in ARMS.items()]
    return dict(questions=QUESTIONS, question_set_version="qs_v2_cause_first_2026-10-10", endpoint="POST https://api.typesafe.ai/v1/systemone (model jev-latest → resolved per call)",
                state_fields=[
                    ("equipment_id / plc_state / plc_state_quality / plc_tag_age_ms", "virtual PLC tags via MQTT (OT network model)"),
                    ("motor_current_a / current_sensor_quality / temperature_c / active_power_kw", "equipment sensors (bucketed 1 A / 1 °C / 1 kW)"),
                    ("stale_signals / missing_signals / conflicts / evidence_reliability", "evidence store (freshness 2.5× sampling, same-batch cross-checks)"),
                    ("vision_part_detected / vision_confidence / vision_defect_*", "camera gate — YOLO11n perception layer (MVTec AD-trained)"),
                    ("qr_decoded / qr_route_consistent", "QR decode + MES route rule"),
                    ("electrical.bus_voltage_pu / transformer_loading_pct", "pandapower power flow — arm E only"),
                    ("network.max_tag_age_ms / n_stale", "OT link statistics — arm E only"),
                ],
                privacy="no simulation clock, no hidden truth, no fault labels are sent; numeric fields are bucketed so equal situations share one cached request",
                thresholds=dict(HOT_C=HOT_C, OVERLOAD_A=OVERLOAD_A, stale_rule="sample older than 2.5 × sampling interval or transport age > TTL", same_batch_s=0.5,
                                reliability="fresh expected signals / expected signals, ×0.5 if any conflict"),
                conflict_rules=[dict(code=c, rule=r) for c, r in CONFLICT_RULES], policy=[dict(id=i, condition=c, action=a) for i, c, a in POLICY_V2], fusion=FUSION,
                arms=arms, fault_kinds=kinds, domain_label=DOMAIN_LABEL, truth_to_cause=TRUTH_TO_CAUSE)


def _anomalous(truth_classes_json: str) -> bool:
    try:
        return json.loads(truth_classes_json) != ["NONE"]
    except Exception:  # noqa: BLE001
        return False


def detection_evidence(exp: str = "jev_pilot", path: Path = DB_PATH) -> dict:
    """Measured from the DB: per anomaly domain × arm outcome means; JEV cause confusion vs truth; observed evidence
    signature (stale / conflicts) inside fault windows; the JEV vs rules split on network/sensing anomalies."""
    if not path.exists():
        return dict(exists=False)
    con = connect(path)
    try:
        runs = _rows(con, "SELECT run_id, arm, family, fault_kind, provider FROM runs WHERE exp=?", (exp,))
        if not runs:
            return dict(exists=True, exp=exp, empty=True)
        rid = {r["run_id"]: r for r in runs}
        metrics = ("correct_action_rate", "false_stop_proposals", "harmful_false_continue", "unnecessary_stop_min", "unnecessary_review", "f1", "composite_loss")
        vals = defaultdict(list)
        for run_id, metric, value in con.execute(f"SELECT run_id, metric, value FROM run_metrics WHERE metric IN ({','.join('?' * len(metrics))}) AND value IS NOT NULL "
                                                 f"AND run_id IN (SELECT run_id FROM runs WHERE exp=?)", (*metrics, exp)):
            r = rid[run_id]
            vals[(DOMAIN.get(r["fault_kind"], "other"), r["arm"], metric)].append(value)
        by_domain = []
        for (dom, arm, metric), xs in sorted(vals.items()):
            by_domain.append(dict(domain=dom, arm=arm, metric=metric, n=len(xs), mean=sum(xs) / len(xs)))
        # per-decision join with the truth record at the same instant (truth is never visible to the decision layer)
        q = """SELECT r.arm, r.fault_kind, d.judge_provider, d.judge_cause, d.judge_cause_p, d.judge_stop_p, d.judge_review_p, d.recommendation, d.rules_rec, d.ml_rec, d.judge_rec,
                      d.n_stale, d.n_conflicts, d.conflicts, t.truth_classes, t.expected_safe_action
               FROM decisions d JOIN runs r ON r.run_id=d.run_id
               JOIN truth t ON t.run_id=d.run_id AND t.machine_id=d.asset_id AND t.sim_time_s=d.sim_time_s WHERE r.exp=?"""
        confusion = defaultdict(Counter)            # (arm) -> Counter((truth_cause, judge_cause))
        sig = defaultdict(lambda: dict(n=0, stale=0, conflicts=0, codes=Counter()))
        split = defaultdict(lambda: Counter())       # (domain, arm) -> who flagged
        probs = defaultdict(lambda: dict(n=0, stop=0.0, review=0.0))
        for arm, kind, jp, jc, jcp, jsp, jrp, rec, rules_rec, ml_rec, judge_rec, n_stale, n_conf, conf_json, truth_json, exp_action in con.execute(q, (exp,)):
            truth_classes = json.loads(truth_json)
            anomalous = truth_classes != ["NONE"]
            tkey = kind if anomalous else "NONE"                       # rows of the confusion matrix = injected fault kind
            tcause = SIGNATURE[kind]["cause"] if anomalous and kind in SIGNATURE else ("none" if not anomalous else "unknown")
            dom = DOMAIN.get(kind, "other") if anomalous else "normal"
            if jp:
                confusion[arm][(tkey, jc or "—")] += 1
                if jsp is not None:
                    p = probs[(arm, tcause)]; p["n"] += 1; p["stop"] += jsp; p["review"] += (jrp or 0.0)
            if anomalous and arm == "E":
                s = sig[kind]; s["n"] += 1; s["stale"] += n_stale or 0; s["conflicts"] += n_conf or 0
                for c in json.loads(conf_json or "[]"):
                    s["codes"][c] += 1
            if anomalous:
                ok = rec == exp_action
                split[(dom, arm)]["n"] += 1
                split[(dom, arm)]["correct"] += ok
                split[(dom, arm)]["rules_alone_correct"] += (rules_rec == exp_action)
                split[(dom, arm)]["ml_alone_correct"] += (ml_rec == exp_action)
                if jp:
                    split[(dom, arm)]["judge_alone_correct"] += (judge_rec == exp_action)
        conf_out = {}
        expected = {k: v["cause"] for k, v in SIGNATURE.items()}; expected["NONE"] = "none"
        for arm, c in confusion.items():
            truths = sorted({t for t, _ in c}); preds = sorted({p for _, p in c})
            total = defaultdict(int); diag = defaultdict(int)
            for (t, p), n in c.items():
                total[t] += n; diag[t] += n if expected.get(t) == p else 0
            conf_out[arm] = dict(truths=truths, preds=preds, cells=[dict(truth=t, pred=p, n=n) for (t, p), n in sorted(c.items())],
                                 cause_accuracy={t: dict(n=total[t], correct=diag[t], expected_cause=expected.get(t), domain=DOMAIN.get(t, "other"), rate=diag[t] / total[t] if total[t] else None) for t in truths})
        signature = [dict(kind=k, decisions=v["n"], mean_stale=v["stale"] / v["n"] if v["n"] else None, mean_conflicts=v["conflicts"] / v["n"] if v["n"] else None,
                          top_conflicts=v["codes"].most_common(3)) for k, v in sorted(sig.items())]
        split_out = [dict(domain=d, arm=a, **{k: v for k, v in c.items()}, rate=(c["correct"] / c["n"]) if c["n"] else None) for (d, a), c in sorted(split.items())]
        prob_out = [dict(arm=a, truth_cause=t, n=v["n"], mean_stop_p=v["stop"] / v["n"], mean_review_p=v["review"] / v["n"]) for (a, t), v in sorted(probs.items()) if v["n"]]
        ci = _rows(con, "SELECT metric, contrast, n_pairs, mean_diff, ci_low, ci_high, excludes_zero FROM paired_ci WHERE exp=? AND metric IN ('correct_action_rate','false_stop_proposals','harmful_false_continue','unnecessary_stop_min','composite_loss') ORDER BY metric, contrast", (exp,))
        return dict(exists=True, exp=exp, n_runs=len(runs), by_domain=by_domain, confusion=conf_out, signature=signature, per_decision=split_out, judge_probs=prob_out, paired_ci=ci)
    finally:
        con.close()
