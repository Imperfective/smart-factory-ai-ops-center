"""Decision providers: deterministic rules (research baseline), simple ML anomaly baseline, MockJev (offline demo,
clearly labelled, never counted as Jev performance) and the TypeSafe Jev adapter (adapters/typesafe_jev.py).

All providers receive the SAME structured state (`state_from_bundle`) so added value can be isolated.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Protocol

from ..contracts.decisions import DecisionEnvelope, EvidenceBundle, JevCall
from ..contracts.enums import Provider, Quality, Recommendation, RiskBand

QUESTION_SET_VERSION = "qs_v1_2026-10-10"
HOT_C, OVERLOAD_A = 65.0, 45.0


def state_from_bundle(b: EvidenceBundle, include_electrical: bool = True, include_network: bool = True) -> dict:
    """Structured, label-free factory state handed to every provider (and to TypeSafe Jev as `state`)."""
    sig = {it.signal: it for it in b.items if it.signal in ("machine_state", "current_a", "temperature_c", "active_power_kw")}
    st = dict(
        equipment_id=b.asset_id, sim_time_s=b.window_end_s,
        plc_state=sig["machine_state"].value if "machine_state" in sig else None,
        plc_state_quality=sig["machine_state"].quality.value if "machine_state" in sig else "MISSING",
        plc_tag_age_ms=round(sig["machine_state"].age_ms or 0) if "machine_state" in sig else None,
        motor_current_a=sig["current_a"].value if "current_a" in sig else None,
        current_sensor_quality=sig["current_a"].quality.value if "current_a" in sig else "MISSING",
        temperature_c=sig["temperature_c"].value if "temperature_c" in sig else None,
        active_power_kw=sig["active_power_kw"].value if "active_power_kw" in sig else None,
        max_tag_age_ms=500, stale_signals=b.stale_signals, missing_signals=b.missing_signals, conflicts=b.conflicts,
        evidence_reliability=b.reliability,
        source_types=sorted({it.source_type for it in b.items}),
        safety_note="Recommendation for a virtual simulator only; never issue physical control commands.",
    )
    vis = [it for it in b.items if it.signal == "vision_part_presence"]
    if vis:
        st["vision_part_detected"] = all(it.value.get("detected") for it in vis)
        st["vision_confidence"] = round(sum(it.value.get("confidence", 0) for it in vis) / len(vis), 3)
        defects = [it for it in vis if it.value.get("defect")]
        st["vision_defect_reported"] = bool(defects)
        if defects:
            st["vision_defect_class"] = defects[-1].value.get("defect_class")
            st["vision_defect_confidence"] = defects[-1].value.get("defect_confidence")
    qr = [it for it in b.items if it.signal == "qr_pallet_id"]
    if qr:
        st["qr_decoded"] = all(it.value.get("decoded") for it in qr)
        st["qr_route_consistent"] = all(it.value.get("route_consistent", True) for it in qr)
    if include_electrical and b.electrical_context:
        st["electrical"] = b.electrical_context
    if include_network:
        st["network"] = b.network_context
    if b.production_context:
        st["production"] = b.production_context
    return st


def state_hash(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:16]


class DecisionProvider(Protocol):
    name: Provider

    def decide(self, state: dict, bundle: EvidenceBundle) -> DecisionEnvelope: ...


def _envelope(provider: Provider, b: EvidenceBundle, rec: Recommendation, risk: RiskBand, causes: list[str], rationale: dict,
              model_version: str | None = None, jev_call: JevCall | None = None) -> DecisionEnvelope:
    return DecisionEnvelope(decision_id=f"dec_{uuid.uuid4().hex[:12]}", run_id=b.run_id, provider=provider, recommendation=rec,
                            risk_band=risk, causes=causes, evidence_ids=[it.evidence_id for it in b.items], model_version=model_version,
                            asset_id=b.asset_id, sim_time_s=b.window_end_s, bundle_id=b.bundle_id, context_hash=b.context_hash,
                            rationale=rationale, jev_call=jev_call)


class DeterministicRuleProvider:
    """Research baseline. `evidence_checks=False` reproduces a naive single-sensor rule set (arms A/B/C);
    `evidence_checks=True` adds freshness/cross-source reasoning (arms D/E)."""
    name = Provider.RULES

    def __init__(self, evidence_checks: bool = True, electrical_context: bool = True, version: str = "rules_v1"):
        self.evidence_checks, self.electrical_context, self.version = evidence_checks, electrical_context, version

    def decide(self, state: dict, b: EvidenceBundle) -> DecisionEnvelope:
        causes, rec, risk = [], Recommendation.CONTINUE_MONITORING, RiskBand.LOW
        cur, temp, plc = state.get("motor_current_a"), state.get("temperature_c"), state.get("plc_state")
        fired = []
        if self.evidence_checks and (state["stale_signals"] or state["missing_signals"]):
            fired.append("EVIDENCE_STALE_OR_MISSING")
            rec, risk, causes = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, ["communications_issue"]
        if plc == "FAULT":
            fired.append("PLC_FAULT")
            rec, risk, causes = Recommendation.REQUEST_SIMULATED_STOP, RiskBand.HIGH, ["physical_process_issue"]
        if isinstance(temp, (int, float)) and temp > HOT_C or (isinstance(cur, (int, float)) and cur > OVERLOAD_A):
            fired.append("OVERLOAD_OR_HOT")
            if rec != Recommendation.REQUEST_SIMULATED_STOP:
                rec, risk = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.HIGH
            causes = ["physical_process_issue"]
        if "PLC_RUNNING_BUT_CURRENT_ZERO" in state["conflicts"]:
            fired.append("RUNNING_CURRENT_ZERO")
            if self.evidence_checks:
                # fresh power reading agrees with the PLC => current sensor is the odd one out
                kw = state.get("active_power_kw")
                if isinstance(kw, (int, float)) and kw > 5 and state["current_sensor_quality"] != "STALE":
                    rec, risk, causes = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, ["sensor_issue"]
                elif state["stale_signals"]:
                    rec, risk, causes = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, ["communications_issue"]
                else:
                    rec, risk, causes = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.HIGH, ["unknown"]
            else:  # naive rule: treats zero current as a stopped machine
                rec, risk, causes = Recommendation.REQUEST_SIMULATED_STOP, RiskBand.HIGH, ["physical_process_issue"]
        if "RUNNING_BUT_CAMERA_NO_PART" in state["conflicts"]:
            fired.append("CAMERA_NO_PART")
            if rec == Recommendation.CONTINUE_MONITORING:
                rec, risk, causes = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, ["sensor_issue"]
        if "VISION_DEFECT_REPORTED" in state["conflicts"]:
            fired.append("VISION_DEFECT")
            if rec in (Recommendation.CONTINUE_MONITORING, Recommendation.REQUEST_RECHECK):
                rec, risk = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.MODERATE
            causes = causes + ["quality_issue"]
        if "QR_ROUTE_INCONSISTENT" in state["conflicts"]:
            fired.append("QR_ROUTE")
            if rec in (Recommendation.CONTINUE_MONITORING, Recommendation.REQUEST_RECHECK):
                rec, risk = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.MODERATE
            causes = ["process_routing_issue"]
        el = state.get("electrical") if self.electrical_context else None
        if el and isinstance(el.get("bus_voltage_pu"), (int, float)) and (el["bus_voltage_pu"] < 0.95 or (el.get("transformer_loading_pct") or 0) > 90):
            fired.append("ELECTRICAL_EXCURSION")
            if rec == Recommendation.CONTINUE_MONITORING:
                rec, risk = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.MODERATE
            causes = causes + ["electrical_loading"]
        return _envelope(self.name, b, rec, risk, causes, dict(rules_fired=fired, evidence_checks=self.evidence_checks,
                                                            electrical_context=self.electrical_context), self.version)


class SimpleMLAnomalyProvider:
    """Baseline 'ML' detector: robust z-score of current/temperature/power against a per-asset running baseline
    learned ONLY from the first `warmup_s` of the run (no truth labels, no test tuning)."""
    name = Provider.ML

    def __init__(self, warmup_s: float = 600.0, z_threshold: float = 4.5, version: str = "ml_zscore_v2"):
        self.warmup_s, self.z, self.version = warmup_s, z_threshold, version
        self._hist: dict[tuple[str, str], list[float]] = {}

    def observe(self, asset_id: str, signal: str, value: float, sim_time_s: float) -> None:
        if sim_time_s <= self.warmup_s and isinstance(value, (int, float)):
            self._hist.setdefault((asset_id, signal), []).append(float(value))

    def score(self, asset_id: str, signal: str, value) -> float | None:
        h = self._hist.get((asset_id, signal))
        if not h or not isinstance(value, (int, float)) or len(h) < 5:
            return None
        med = sorted(h)[len(h) // 2]
        mad = sorted(abs(x - med) for x in h)[len(h) // 2]
        floor = 0.05 * abs(med) + {"current_a": 1.0, "temperature_c": 1.0, "active_power_kw": 0.5}.get(signal, 0.5)  # avoid hypersensitivity on near-constant signals
        return abs(float(value) - med) / (1.4826 * max(mad, floor))

    def decide(self, state: dict, b: EvidenceBundle) -> DecisionEnvelope:
        zs = {s: self.score(b.asset_id, s, state.get(k)) for s, k in (("current_a", "motor_current_a"), ("temperature_c", "temperature_c"), ("active_power_kw", "active_power_kw"))}
        zs = {k: round(v, 2) for k, v in zs.items() if v is not None}
        mx = max(zs.values(), default=0.0)
        if mx > 2 * self.z:
            rec, risk = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.HIGH
        elif mx > self.z:
            rec, risk = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE
        else:
            rec, risk = Recommendation.CONTINUE_MONITORING, RiskBand.LOW
        return _envelope(self.name, b, rec, risk, ["anomaly_score"] if mx > self.z else [], dict(z_scores=zs, threshold=self.z), self.version)


class MockJevProvider:
    """MOCK PROVIDER for offline demos. Deterministic on the state hash; MUST NOT be reported as Jev results."""
    name = Provider.MOCK_JEV

    def decide(self, state: dict, b: EvidenceBundle) -> DecisionEnvelope:
        h = state_hash(state)
        conflicts, stale = state["conflicts"], state["stale_signals"]
        if stale and not conflicts:
            rec, risk, cause = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, "communications_issue"
        elif "PLC_RUNNING_BUT_CURRENT_ZERO" in conflicts:
            rec, risk, cause = Recommendation.REQUEST_RECHECK, RiskBand.MODERATE, "sensor_issue" if not stale else "communications_issue"
        elif state.get("plc_state") == "FAULT":
            rec, risk, cause = Recommendation.REQUEST_SIMULATED_STOP, RiskBand.CRITICAL, "physical_process_issue"
        elif isinstance(state.get("temperature_c"), (int, float)) and state["temperature_c"] > HOT_C:
            rec, risk, cause = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.HIGH, "physical_process_issue"
        elif "VISION_DEFECT_REPORTED" in conflicts:
            rec, risk, cause = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.MODERATE, "quality_issue"
        elif conflicts:
            rec, risk, cause = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.MODERATE, "unknown"
        else:
            rec, risk, cause = Recommendation.CONTINUE_MONITORING, RiskBand.LOW, "none"
        call = JevCall(provider=Provider.MOCK_JEV, model_alias="MOCK", resolved_model="MOCK_JEV_v1", question_set_version=QUESTION_SET_VERSION,
                       request_hash=h, latency_ms=0.0, raw_answers_redacted=dict(likely_cause=cause, mock=True))
        return _envelope(self.name, b, rec, risk, [cause], dict(mock=True, note="MOCK provider — not TypeSafe Jev"), "MOCK_JEV_v1", call)
