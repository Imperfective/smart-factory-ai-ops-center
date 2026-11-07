"""Industrial event / telemetry contracts (docs/10). Compatible with schemas/industrial_event.schema.json."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .enums import Quality, SourceType


class Provenance(BaseModel):
    dataset_id: str | None = None
    dataset_version: str | None = None
    original_row_id: str | None = None
    source_label: str = "SYNTHETIC_DEMO"
    transform_id: str | None = None
    seed: int | None = None
    sha256: str | None = None
    label_source: str | None = None
    license: str | None = None


class IndustrialEvent(BaseModel):
    """One observation as *received* by the event collector.

    `sim_time_s`      : simulation clock when the physical/virtual source produced the value.
    `received_sim_s`  : simulation clock when the collector received it (after network delay).
    `age_ms`          : (received - source) in ms; `quality=STALE` when age > ttl_ms.
    Wall-clock fields stay None for purely synthetic runs (docs/10 timing contract).
    """
    event_id: str
    run_id: str
    sim_time_s: float = Field(ge=0)
    asset_id: str
    source_type: SourceType
    signal: str
    value: Any
    unit: str | None = None
    quality: Quality = Quality.VALID
    source_timestamp: str | None = None
    received_timestamp: str | None = None
    received_sim_s: float | None = None
    age_ms: float | None = None
    ttl_ms: float | None = None
    seq: int | None = None
    duplicate: bool = False
    out_of_order: bool = False
    evidence_id: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    def to_schema_dict(self) -> dict:
        """Exactly the fields allowed by schemas/industrial_event.schema.json (additionalProperties=false)."""
        return dict(event_id=self.event_id, run_id=self.run_id, sim_time_s=self.sim_time_s, asset_id=self.asset_id,
                    source_type=self.source_type.value, signal=self.signal, value=self.value, unit=self.unit,
                    quality=self.quality.value, source_timestamp=self.source_timestamp,
                    received_timestamp=self.received_timestamp, evidence_id=self.evidence_id,
                    provenance=self.provenance.model_dump())


class TruthRecord(BaseModel):
    """Hidden simulator truth at a sim time (never visible to detectors/providers)."""
    run_id: str
    sim_time_s: float
    machine_id: str
    physical_state: str
    active_fault_ids: list[str] = Field(default_factory=list)
    truth_classes: list[str] = Field(default_factory=list)
    expected_safe_action: str = "CONTINUE_MONITORING"
    harmful_stop_warranted: bool = False
