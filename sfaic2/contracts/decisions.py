"""Evidence, decision and virtual-command contracts (docs/08, docs/09, schemas/decision.schema.json)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .enums import CommandStatus, GateStatus, Provider, Quality, Recommendation, RiskBand


class EvidenceItem(BaseModel):
    evidence_id: str
    asset_id: str
    signal: str
    value: Any
    unit: str | None = None
    quality: Quality
    source_type: str
    sim_time_s: float
    age_ms: float | None = None
    ttl_ms: float | None = None
    event_ids: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    """Time-window view of evidence for one asset: freshness, conflicts and reliability (docs/03 State/Evidence)."""
    bundle_id: str
    run_id: str
    asset_id: str
    window_start_s: float
    window_end_s: float
    items: list[EvidenceItem]
    stale_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    reliability: float = Field(1.0, ge=0, le=1, description="fraction of expected signals that are fresh & unconflicted")
    context_hash: str
    electrical_context: dict | None = None
    network_context: dict | None = None
    production_context: dict | None = None

    def value(self, signal: str, default=None):
        for it in self.items:
            if it.signal == signal:
                return it.value
        return default

    def quality(self, signal: str) -> Quality | None:
        for it in self.items:
            if it.signal == signal:
                return it.quality
        return None


class JevCall(BaseModel):
    """Audit record of one remote (or mock) structured-judgment call. Never contains the API key."""
    provider: Provider
    model_alias: str | None = None
    resolved_model: str | None = None
    question_set_version: str
    request_hash: str
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    cached: bool = False
    retries: int = 0
    status: str = "ok"
    error: str | None = None
    raw_answers_redacted: dict | None = None


class DecisionEnvelope(BaseModel):
    decision_id: str
    run_id: str
    provider: Provider
    recommendation: Recommendation
    risk_band: RiskBand = RiskBand.UNKNOWN
    causes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    model_version: str | None = None
    safety_gate_status: GateStatus = GateStatus.NOT_REQUESTED
    # extras (not in the minimal JSON schema; exported separately)
    asset_id: str | None = None
    sim_time_s: float | None = None
    bundle_id: str | None = None
    context_hash: str | None = None
    rationale: dict = Field(default_factory=dict)
    jev_call: JevCall | None = None
    arm: str | None = None

    def to_schema_dict(self) -> dict:
        return dict(decision_id=self.decision_id, run_id=self.run_id, provider=self.provider.value,
                    recommendation=self.recommendation.value, risk_band=self.risk_band.value, causes=self.causes,
                    evidence_ids=self.evidence_ids, model_version=self.model_version,
                    safety_gate_status=self.safety_gate_status.value)


class VirtualCommand(BaseModel):
    """Simulator-only STOP request. `virtual_only` is a literal True; real targets are rejected by the gate."""
    command_id: str
    run_id: str
    target_id: str
    action: str = "REQUEST_SIMULATED_STOP"
    requested_by: str
    proposed_sim_s: float
    decision_id: str | None = None
    evidence_context_hash: str | None = None
    evidence_sim_s: float | None = None
    policy_version: str = "gate_v1"
    simulation_mode: bool = True
    virtual_only: bool = True
    approval: dict | None = None
    status: CommandStatus = CommandStatus.PROPOSED
    reason: str | None = None
    transition_history: list[dict] = Field(default_factory=list)
    ack_sim_s: float | None = None
    ack_wall_ms: float | None = None
