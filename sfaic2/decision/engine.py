"""Decision engine: replays a finished simulation's *observed* events in arrival order, builds evidence bundles
at a fixed decision interval, asks the arm's providers, fuses the result, proposes virtual STOP commands through
the SafetyGate (operator approval simulated with a configurable policy) and records decisions/commands.

Arms (docs/13): A rules-naive · B ML+rules-naive · C ML+Jev(simple evidence) · D ML+Jev+evidence checks ·
E = D + electrical/network context. `provider_override` lets MockJev stand in for Jev offline (labelled MOCK).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.typesafe_jev import TypeSafeJevProvider
from ..contracts.decisions import DecisionEnvelope, EvidenceBundle, VirtualCommand
from ..contracts.enums import CommandStatus, Provider, Recommendation, RiskBand
from ..contracts.events import IndustrialEvent
from ..simulator.production import RunOutput
from ..simulator.virtual_plc import VirtualPLC
from .evidence import build_bundle, electrical_context, index_events, network_context
from .providers import DeterministicRuleProvider, MockJevProvider, SimpleMLAnomalyProvider, state_from_bundle
from .safety_gate import SafetyGate

ORDER = [Recommendation.CONTINUE_MONITORING, Recommendation.REQUEST_RECHECK, Recommendation.REQUEST_OPERATOR_REVIEW, Recommendation.REQUEST_SIMULATED_STOP]
ARMS = {
    "N": dict(label="no AI command center (control: faults run unsupervised)", ml=False, jev=False, evidence_checks=False, context=False, none=True),
    "A": dict(label="rules only (naive)", ml=False, jev=False, evidence_checks=False, context=False),
    "B": dict(label="ML + naive rules", ml=True, jev=False, evidence_checks=False, context=False),
    "C": dict(label="ML + Jev, simple evidence", ml=True, jev=True, evidence_checks=False, context=False),
    "D": dict(label="ML + Jev + freshness/cross-source", ml=True, jev=True, evidence_checks=True, context=False),
    "E": dict(label="D + electrical/network context", ml=True, jev=True, evidence_checks=True, context=True),
}


@dataclass
class OperatorPolicy:
    """Simulated operator (docs/13: not a user study). Approves STOP proposals after `delay_s` with `approve_rate`."""
    delay_s: float = 5.0
    approve_rate: float = 1.0
    operator_id: str = "operator_a"


@dataclass
class EngineResult:
    arm: str
    decisions: list[DecisionEnvelope]
    commands: list[VirtualCommand]
    gate_audit: list[dict]
    gate_stats: dict
    jev_usage: dict | None
    bundles: int = 0
    fallbacks: int = 0
    arm_config: dict = field(default_factory=dict)


class DecisionEngine:
    def __init__(self, run: RunOutput, arm: str = "E", decision_interval_s: float = 30.0, window_s: float = 60.0,
                 provider_override=None, operator: OperatorPolicy | None = None, jev_provider: TypeSafeJevProvider | None = None,
                 plc: VirtualPLC | None = None, events_source=None, auto_operator: bool = True):
        self.run, self.arm, self.cfg = run, arm, ARMS[arm]
        self.events_source = events_source or (lambda: run.events)
        self.auto_operator = auto_operator
        self.decisions: list[DecisionEnvelope] = []
        self.commands: list[VirtualCommand] = []
        self.fallbacks = 0
        self.bundles = 0
        self._ml_trained = False
        self.interval, self.window = decision_interval_s, window_s
        self.operator = operator or OperatorPolicy()
        self.rules = DeterministicRuleProvider(evidence_checks=self.cfg["evidence_checks"], electrical_context=self.cfg["context"])
        self.ml = SimpleMLAnomalyProvider() if self.cfg["ml"] else None
        if self.cfg["jev"]:
            self.jev = provider_override or jev_provider or TypeSafeJevProvider()
        else:
            self.jev = None
        self.plc = plc or VirtualPLC(sorted({e.asset_id for e in run.events if e.signal == "machine_state"}))
        self.gate = SafetyGate(self.plc, run.run_id)
        self.pending: list[tuple[float, VirtualCommand, EvidenceBundle]] = []

    def _fuse(self, b: EvidenceBundle, parts: list[DecisionEnvelope]) -> DecisionEnvelope:
        """Evidence-gated fusion: the most conservative *non-stop* recommendation wins; a STOP needs fresh,
        conflict-free evidence AND agreement of the rules engine (Jev alone can never force a STOP)."""
        best = max(parts, key=lambda d: ORDER.index(d.recommendation))
        rules = next(d for d in parts if d.provider == Provider.RULES)
        rec = best.recommendation
        # an ML-only alarm (rules and judgment see nothing) is at most a re-check, never an operator review
        others = [d for d in parts if d.provider != Provider.ML]
        if best.provider == Provider.ML and all(d.recommendation == Recommendation.CONTINUE_MONITORING for d in others) and rec != Recommendation.CONTINUE_MONITORING:
            rec = Recommendation.REQUEST_RECHECK
        if rec == Recommendation.REQUEST_SIMULATED_STOP:
            if self.cfg["evidence_checks"] and (b.stale_signals or b.missing_signals or b.reliability < 0.75):
                rec = Recommendation.REQUEST_OPERATOR_REVIEW
            elif rules.recommendation != Recommendation.REQUEST_SIMULATED_STOP and self.cfg["evidence_checks"]:
                rec = Recommendation.REQUEST_OPERATOR_REVIEW
        risk = max(parts, key=lambda d: ["UNKNOWN", "LOW", "MODERATE", "HIGH", "CRITICAL"].index(d.risk_band.value)).risk_band
        fused = DecisionEnvelope(decision_id=f"{best.decision_id}_fused", run_id=b.run_id, provider=best.provider, recommendation=rec,
                                 risk_band=risk, causes=sorted({c for d in parts for c in d.causes}), evidence_ids=best.evidence_ids,
                                 model_version=best.model_version, asset_id=b.asset_id, sim_time_s=b.window_end_s, bundle_id=b.bundle_id,
                                 context_hash=b.context_hash, arm=self.arm,
                                 rationale=dict(components={d.provider.value: dict(recommendation=d.recommendation.value, risk=d.risk_band.value,
                                                                               causes=d.causes, rationale=d.rationale) for d in parts},
                                                downgraded=(rec != best.recommendation)), jev_call=next((d.jev_call for d in parts if d.jev_call), None))
        return fused

    def _train_ml(self, events):
        if self.ml and not self._ml_trained:
            for e in events:
                if e.signal in ("current_a", "temperature_c", "active_power_kw") and not e.duplicate:
                    self.ml.observe(e.asset_id, e.signal, e.value, e.sim_time_s)
            if events and events[-1].sim_time_s >= self.ml.warmup_s:
                self._ml_trained = True

    def step(self, t: float) -> list[DecisionEnvelope]:
        """One decision tick at sim time t using only events received by t. Returns the fused decisions."""
        events = [e for e in self.events_source() if e.received_sim_s is None or e.received_sim_s <= t]
        self._train_ml(events)
        by_asset = index_events(events)
        machine_assets = sorted(a for a, evs in by_asset.items() if any(e.signal == "machine_state" for e in evs))
        gate_evs = by_asset.get("CAMERA_GATE", [])
        ttl = self.run.config.network.tag_ttl_ms
        if self.auto_operator:
            for due, cmd, b in list(self.pending):
                if t >= due:
                    self.pending.remove((due, cmd, b))
                    approve = (hash((cmd.command_id, self.run.seed)) % 1000) / 1000.0 < self.operator.approve_rate
                    self.gate.approve(cmd.command_id, self.operator.operator_id, t, current_context_hash=b.context_hash, approved=approve)
        self.plc.advance(t)
        self.gate.sync_from_plc(t)
        el = electrical_context(events, t) if self.cfg["context"] else None
        out = []
        prepared = []
        for asset in machine_assets:
            b = build_bundle(self.run.run_id, asset, by_asset[asset], t, self.window, ttl, gate_evs, electrical=el,
                             production=None, freshness_s=2.5 * self.run.config.simulation.sample_interval_s)
            b.network_context = network_context(b.items) if self.cfg["context"] else None
            self.bundles += 1
            prepared.append((asset, b, state_from_bundle(b, include_electrical=self.cfg["context"], include_network=self.cfg["context"])))
        jev_results: dict[str, DecisionEnvelope] = {}
        if self.jev is not None and isinstance(self.jev, TypeSafeJevProvider) and len(prepared) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as ex:
                for asset, d in zip([a for a, _, _ in prepared], ex.map(lambda x: self.jev.decide(x[2], x[1]), prepared)):
                    jev_results[asset] = d
        for asset, b, state in prepared:
            if self.cfg.get("none"):
                d = DecisionEnvelope(decision_id=f"dec_none_{asset}_{int(t)}", run_id=b.run_id, provider=Provider.RULES, recommendation=Recommendation.CONTINUE_MONITORING,
                                     risk_band=RiskBand.UNKNOWN, evidence_ids=[], model_version="none", asset_id=asset, sim_time_s=t, bundle_id=b.bundle_id,
                                     context_hash=b.context_hash, arm=self.arm, rationale=dict(none=True, note="control arm: no supervisory decision layer"))
                self.decisions.append(d); out.append(d)
                continue
            parts = [self.rules.decide(state, b)]
            if self.ml:
                parts.append(self.ml.decide(state, b))
            if self.jev:
                d = jev_results.get(asset) or self.jev.decide(state, b)
                if d.rationale.get("fallback"):
                    self.fallbacks += 1
                parts.append(d)
            fused = self._fuse(b, parts)
            fused.rationale["bundle"] = dict(stale=b.stale_signals, missing=b.missing_signals, conflicts=b.conflicts, reliability=b.reliability)
            fused.rationale["state_for_study"] = {k: v for k, v in state.items() if k not in ("safety_note",)}
            sig_t = [it.sim_time_s for it in b.items]
            jc = fused.jev_call
            fused.rationale["trace"] = dict(signal_first_s=min(sig_t) if sig_t else None, signal_last_s=max(sig_t) if sig_t else None,
                                            max_transport_age_ms=max((it.age_ms or 0) for it in b.items if it.signal in ('machine_state', 'current_a', 'temperature_c', 'active_power_kw')) if any(it.signal in ('machine_state', 'current_a', 'temperature_c', 'active_power_kw') for it in b.items) else None,
                                            evidence_window_end_s=b.window_end_s, decision_s=t,
                                            judgment=dict(provider=(jc.provider.value if jc else None), latency_ms=(jc.latency_ms if jc else None),
                                                          resolved_model=(jc.resolved_model if jc else None), cached=(jc.cached if jc else None),
                                                          status=(jc.status if jc else None)) if jc else None,
                                            signals=[dict(signal=it.signal, source=it.source_type, sim_time_s=it.sim_time_s, age_ms=it.age_ms, quality=it.quality.value) for it in b.items])
            self.decisions.append(fused)
            out.append(fused)
            if fused.recommendation == Recommendation.REQUEST_SIMULATED_STOP and self.plc.command_state.get(asset) is None \
                    and not any(c.target_id == asset and c.status == CommandStatus.PENDING_APPROVAL for c in self.commands):
                cmd = self.gate.propose(fused, asset, "decision_engine", t)
                self.commands.append(cmd)
                if cmd.status == CommandStatus.PENDING_APPROVAL:
                    self.pending.append((t + self.operator.delay_s, cmd, b))
        return out

    def result(self) -> EngineResult:
        usage = self.jev.budget.summary() if isinstance(self.jev, TypeSafeJevProvider) else None
        return EngineResult(arm=self.arm, decisions=self.decisions, commands=self.commands, gate_audit=self.gate.audit, gate_stats=self.gate.stats,
                            jev_usage=usage, bundles=self.bundles, fallbacks=self.fallbacks,
                            arm_config=dict(self.cfg, provider=(self.jev.name.value if self.jev else None)))

    def run_all(self) -> EngineResult:
        t, end = self.interval, self.run.manifest["sim_seconds"]
        while t <= end:
            self.step(t)
            t += self.interval
        return self.result()
