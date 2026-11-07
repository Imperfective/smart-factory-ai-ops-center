import json
import os

import httpx
import pytest

from sfaic2.adapters.typesafe_jev import QUESTIONS, TypeSafeJevProvider, redact
from sfaic2.contracts.enums import CommandStatus, FaultKind, Provider, Recommendation
from sfaic2.decision.engine import DecisionEngine, OperatorPolicy
from sfaic2.decision.evidence import build_bundle, index_events
from sfaic2.decision.providers import DeterministicRuleProvider, MockJevProvider, state_from_bundle
from sfaic2.decision.safety_gate import SafetyGate
from sfaic2.simulator.production import run_config
from sfaic2.simulator.scenarios import demo_factory, fault_preset, scenario_2_contradiction
from sfaic2.simulator.virtual_plc import VirtualPLC


@pytest.fixture(scope="module")
def s2():
    return run_config(scenario_2_contradiction(duration_s=1800), enable_electrical=False)


def test_bundle_flags_stale_and_conflict(s2):
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 900.0, 60.0, 500.0, by.get("CAMERA_GATE", []))
    assert "PLC_RUNNING_BUT_CURRENT_ZERO" in b.conflicts or b.stale_signals
    st = state_from_bundle(b)
    assert "truth" not in json.dumps(st).lower() and st["equipment_id"] == "PRESS_01"


def test_naive_rules_stop_on_zero_current_but_evidence_rules_recheck(s2):
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    st = state_from_bundle(b)
    if "PLC_RUNNING_BUT_CURRENT_ZERO" in b.conflicts:
        naive = DeterministicRuleProvider(evidence_checks=False).decide(st, b)
        careful = DeterministicRuleProvider(evidence_checks=True).decide(st, b)
        assert naive.recommendation == Recommendation.REQUEST_SIMULATED_STOP
        assert careful.recommendation in (Recommendation.REQUEST_RECHECK, Recommendation.REQUEST_OPERATOR_REVIEW)


def test_mock_provider_is_labelled():
    d = MockJevProvider()
    assert d.name == Provider.MOCK_JEV


def test_engine_offline_arm_e_with_mock(s2):
    res = DecisionEngine(s2, arm="E", provider_override=MockJevProvider()).run_all()
    assert res.bundles > 0 and res.decisions
    assert all(d.arm == "E" for d in res.decisions)
    assert any(d.jev_call and d.jev_call.provider == Provider.MOCK_JEV for d in res.decisions)
    assert res.jev_usage is None  # mock usage never reported as Jev usage


def test_engine_stop_flow_requires_approval_and_fresh_evidence():
    f = fault_preset(FaultKind.MECHANICAL_STOP, 300, 900, "PRESS_01")
    run = run_config(demo_factory(quantity=200, duration_s=1500, faults=[f]), enable_electrical=False)
    res = DecisionEngine(run, arm="D", provider_override=MockJevProvider(), operator=OperatorPolicy(delay_s=5, approve_rate=1.0)).run_all()
    assert res.commands, "a FAULT should produce a STOP proposal"
    statuses = {c.status for c in res.commands}
    assert CommandStatus.STOPPED in statuses or CommandStatus.STOPPING in statuses or CommandStatus.STOP_REQUESTED in statuses
    denied = DecisionEngine(run, arm="D", provider_override=MockJevProvider(), operator=OperatorPolicy(delay_s=5, approve_rate=0.0)).run_all()
    assert all(c.status in (CommandStatus.REJECTED, CommandStatus.PENDING_APPROVAL) for c in denied.commands)
    assert denied.gate_stats["executed"] == 0


def test_gate_rejections():
    plc = VirtualPLC(["PRESS_01"])
    g = SafetyGate(plc, "r1", max_evidence_age_s=60)
    c = g.propose(None, "PRESS_01", "op", 100.0, simulation_mode=False)
    assert c.status == CommandStatus.REJECTED and c.reason == "SIMULATION_ONLY"
    c = g.propose(None, "real-plc-10.1.1.1", "op", 100.0)
    assert c.reason == "VIRTUAL_TARGET_REQUIRED"
    c = g.propose(None, "PRESS_01", "op", 100.0, command_id="k1")
    assert c.status == CommandStatus.PENDING_APPROVAL
    assert g.approve("k1", "intruder", 101.0, evidence_sim_s=100.0).reason == "OPERATOR_NOT_AUTHORISED"
    c2 = g.propose(None, "PRESS_01", "op", 100.0, command_id="k2")
    assert g.approve("k2", "operator_a", 300.0, evidence_sim_s=100.0).reason == "EVIDENCE_STALE"
    c3 = g.propose(None, "PRESS_01", "op", 100.0, command_id="k3")
    ok = g.approve("k3", "operator_a", 110.0, evidence_sim_s=100.0)
    assert ok.status == CommandStatus.STOP_REQUESTED and plc.command_state["PRESS_01"] == CommandStatus.STOP_REQUESTED
    assert g.propose(None, "PRESS_01", "op", 120.0, command_id="k3") is ok  # idempotent
    assert g.propose(None, "PRESS_01", "op", 120.0, command_id="k4").reason == "ALREADY_STOPPING_OR_STOPPED"


def test_gate_refuses_non_simulation_mode(monkeypatch):
    monkeypatch.setenv("SIMULATION_MODE", "false")
    with pytest.raises(RuntimeError):
        SafetyGate(VirtualPLC(["M"]), "r")


# ---------------- TypeSafe adapter contract tests (no network: httpx MockTransport)
GOOD = {"model": "jev-1.13.0", "answers": {
    "likely_cause": {"type": "choice", "choice": "sensor_issue", "probabilities": {"physical_process_issue": 0.1, "sensor_issue": 0.7, "communications_issue": 0.15, "electrical_loading": 0.0, "process_routing_issue": 0.0, "none": 0.0, "unknown": 0.05}, "confidence": 0.65},
    "review_needed": {"type": "noul", "noul": 0.82}, "stop_warranted": {"type": "noul", "noul": 0.05},
    "risk_band": {"type": "score", "score": 1.3, "legend": {"0": "low", "1": "moderate", "2": "high", "3": "critical"}, "probabilities": {"0": 0.1, "1": 0.6, "2": 0.2, "3": 0.1}, "confidence": 0.5}},
    "usage": {"input_tokens": 420, "output_tokens": 0}}


def _provider(handler, monkeypatch, enabled="true", key="apikey_test_000"):
    monkeypatch.setenv("JEV_ENABLED", enabled)
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    return TypeSafeJevProvider(transport=httpx.MockTransport(handler), max_retries=1)


def test_adapter_builds_documented_request_and_parses(monkeypatch, s2):
    monkeypatch.delenv("JEV_MODEL", raising=False)   # the documented default, independent of a pinned local .env
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=GOOD)
    p = _provider(handler, monkeypatch)
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    d = p.decide(state_from_bundle(b), b)
    assert seen["auth"] == "Bearer apikey_test_000" and seen["body"]["model"] == "jev-latest"
    assert set(seen["body"]["questions"]) == set(QUESTIONS) and seen["body"]["questions"]["risk_band"]["criteria"] == ["low", "moderate", "high", "critical"]
    assert d.provider == Provider.TYPESAFE_JEV and d.model_version == "jev-1.13.0"
    assert d.recommendation in (Recommendation.REQUEST_OPERATOR_REVIEW, Recommendation.REQUEST_RECHECK)
    assert d.jev_call.input_tokens == 420 and p.budget.calls == 1 and p.budget.estimated_cost_usd > 0 if hasattr(p.budget, "estimated_cost_usd") else True
    assert "apikey" not in json.dumps(d.model_dump(), default=str)
    # cache hit on identical state
    d2 = p.decide(state_from_bundle(b), b)
    assert d2.jev_call.cached and p.budget.calls == 1


def test_adapter_disabled_or_missing_key_falls_back(monkeypatch, s2):
    p = _provider(lambda r: httpx.Response(200, json=GOOD), monkeypatch, enabled="false")
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    d = p.decide(state_from_bundle(b), b)
    assert d.rationale["fallback"] and d.recommendation == Recommendation.REQUEST_OPERATOR_REVIEW and d.jev_call.status == "unavailable"


@pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
def test_adapter_http_errors_are_safe(monkeypatch, s2, status):
    p = _provider(lambda r: httpx.Response(status, json={"error": "x"}), monkeypatch)
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    d = p.decide(state_from_bundle(b), b)
    assert d.rationale["fallback"] and d.recommendation != Recommendation.CONTINUE_MONITORING


def test_adapter_rejects_malformed_response(monkeypatch, s2):
    bad = {"model": "jev-1.13.0", "answers": {"likely_cause": {"type": "choice", "choice": "x"}}}
    p = _provider(lambda r: httpx.Response(200, json=bad), monkeypatch)
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    d = p.decide(state_from_bundle(b), b)
    assert d.jev_call.status == "invalid_response"


def test_adapter_budget_cap(monkeypatch, s2):
    monkeypatch.setenv("JEV_MAX_CALLS_PER_RUN", "1")
    p = _provider(lambda r: httpx.Response(200, json=GOOD), monkeypatch)
    p.use_cache = False
    by = index_events(s2.events)
    b = build_bundle(s2.run_id, "PRESS_01", by["PRESS_01"], 660.0, 60.0, 500.0)
    p.decide(state_from_bundle(b), b)
    d = p.decide(state_from_bundle(b), b)
    assert d.jev_call.status == "unavailable" and "JEV_MAX_CALLS_PER_RUN" in d.jev_call.error


def test_redact_removes_keys():
    assert "REDACTED" in json.dumps(redact({"h": "Authorization: Bearer apikey_abc_def"}))


def test_jev_state_view_buckets_and_drops_clock():
    base = dict(equipment_id="PRESS_01", sim_time_s=660.0, plc_state="RUNNING", plc_tag_age_ms=1734, motor_current_a=36.9, temperature_c=58.44,
                active_power_kw=23.2, evidence_reliability=0.75, stale_signals=[], missing_signals=[], conflicts=[], source_types=["SIMULATED"],
                network=dict(max_tag_age_ms=1734, n_stale=1), electrical=dict(bus_voltage_pu=0.9939, transformer_loading_pct=24.7, age_s=12.0))
    a = TypeSafeJevProvider.jev_state_view(base)
    b = TypeSafeJevProvider.jev_state_view(dict(base, sim_time_s=690.0, temperature_c=58.2, motor_current_a=37.3, plc_tag_age_ms=1690))
    assert "sim_time_s" not in a and a == b  # physically equivalent states share one cached request
    assert a["temperature_c"] == 58 and a["electrical"]["transformer_loading_pct"] == 25
    c = TypeSafeJevProvider.jev_state_view(dict(base, temperature_c=71.0))
    assert c != a
