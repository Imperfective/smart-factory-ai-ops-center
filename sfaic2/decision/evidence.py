"""Evidence Store: builds a per-asset, event-time window view with staleness, missing signals, conflicts and a
reliability score. Only *observed* events are used — never the simulator truth."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from ..contracts.decisions import EvidenceBundle, EvidenceItem
from ..contracts.enums import Quality
from ..contracts.events import IndustrialEvent

EXPECTED_SIGNALS = ("machine_state", "current_a", "temperature_c", "active_power_kw")
IDLE_CURRENT_A = 8.0        # above this the machine is considered electrically loaded (DEMO assumption)
HOT_C = 65.0


def _latest_per_signal(events: list[IndustrialEvent]) -> dict[str, IndustrialEvent]:
    best: dict[str, IndustrialEvent] = {}
    for e in events:
        if e.duplicate:
            continue
        cur = best.get(e.signal)
        if cur is None or (e.sim_time_s, e.seq or 0) > (cur.sim_time_s, cur.seq or 0):
            best[e.signal] = e
    return best


def build_bundle(run_id: str, asset_id: str, events: list[IndustrialEvent], now_s: float, window_s: float = 60.0,
                 ttl_ms: float = 500.0, gate_events: list[IndustrialEvent] | None = None,
                 electrical: dict | None = None, network: dict | None = None, production: dict | None = None,
                 freshness_s: float = 25.0) -> EvidenceBundle:
    """`events` = everything the collector received for this asset with received_sim_s <= now_s.

    Two staleness notions are kept apart: *transport* staleness (network age > ttl_ms, flagged by the link) and
    *sampling* staleness (latest sample older than `freshness_s` ≈ 2.5 sampling intervals at decision time)."""
    win = [e for e in events if e.received_sim_s is not None and now_s - window_s <= e.received_sim_s <= now_s]
    latest = _latest_per_signal(win)
    items, stale, missing, conflicts = [], [], [], []
    for sig in EXPECTED_SIGNALS:
        e = latest.get(sig)
        if e is None:
            missing.append(sig)
            continue
        age_now_ms = (now_s - e.sim_time_s) * 1000.0
        q = e.quality
        if (now_s - e.sim_time_s) > freshness_s and q == Quality.VALID:
            q = Quality.STALE
        if q == Quality.STALE:
            stale.append(sig)
        items.append(EvidenceItem(evidence_id=f"ev_{asset_id}_{sig}_{int(now_s)}", asset_id=asset_id, signal=sig, value=e.value,
                                  unit=e.unit, quality=q, source_type=e.source_type.value, sim_time_s=e.sim_time_s,
                                  age_ms=age_now_ms, ttl_ms=ttl_ms, event_ids=[e.event_id]))
    # gate (camera) evidence tied to this asset
    for e in (gate_events or []):
        if e.received_sim_s is None or not (now_s - window_s <= e.received_sim_s <= now_s):
            continue
        if isinstance(e.value, dict) and e.value.get("upstream") == asset_id:
            items.append(EvidenceItem(evidence_id=f"ev_{asset_id}_{e.signal}_{e.event_id}", asset_id=asset_id, signal=e.signal,
                                      value=e.value, quality=e.quality, source_type=e.source_type.value, sim_time_s=e.sim_time_s,
                                      age_ms=(now_s - e.sim_time_s) * 1000.0, ttl_ms=window_s * 1000, event_ids=[e.event_id]))
    # cross-signal conflicts (all observation-side; no truth)
    # cross-signal checks only between samples taken at (nearly) the same simulation time
    def aligned(a, b):
        ea, eb = latest.get(a), latest.get(b)
        return ea is not None and eb is not None and abs(ea.sim_time_s - eb.sim_time_s) <= 0.5  # same telemetry batch
    state = latest.get("machine_state").value if latest.get("machine_state") else None
    cur = latest.get("current_a").value if latest.get("current_a") and aligned("machine_state", "current_a") else None
    kw = latest.get("active_power_kw").value if latest.get("active_power_kw") and aligned("current_a", "active_power_kw") else None
    if state == "RUNNING" and isinstance(cur, (int, float)) and cur < 0.5:
        conflicts.append("PLC_RUNNING_BUT_CURRENT_ZERO")
    if state in ("IDLE", "STOPPED", "FAULT") and isinstance(cur, (int, float)) and cur > IDLE_CURRENT_A:
        conflicts.append("PLC_NOT_RUNNING_BUT_CURRENT_HIGH")
    if isinstance(cur, (int, float)) and isinstance(kw, (int, float)) and cur < 0.5 and kw > 5.0:
        conflicts.append("CURRENT_ZERO_BUT_POWER_HIGH")
    vis = [it for it in items if it.signal == "vision_part_presence"]
    if vis and state == "RUNNING" and all(not it.value.get("detected", True) for it in vis):
        conflicts.append("RUNNING_BUT_CAMERA_NO_PART")
    qr = [it for it in items if it.signal == "qr_pallet_id" and not it.value.get("route_consistent", True)]
    if qr:
        conflicts.append("QR_ROUTE_INCONSISTENT")
    if any(it.value.get("defect") for it in vis):
        conflicts.append("VISION_DEFECT_REPORTED")
    fresh = sum(1 for it in items if it.signal in EXPECTED_SIGNALS and it.quality == Quality.VALID)
    reliability = fresh / len(EXPECTED_SIGNALS)
    if conflicts:
        reliability *= 0.5
    payload = dict(asset_id=asset_id, now=now_s, items=[(i.signal, i.value, i.quality.value, round(i.age_ms or 0)) for i in items],
                   stale=stale, missing=missing, conflicts=conflicts)
    ctx_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return EvidenceBundle(bundle_id=f"eb_{asset_id}_{int(now_s)}", run_id=run_id, asset_id=asset_id, window_start_s=now_s - window_s,
                          window_end_s=now_s, items=items, stale_signals=stale, missing_signals=missing, conflicts=conflicts,
                          reliability=round(reliability, 3), context_hash=ctx_hash, electrical_context=electrical,
                          network_context=network, production_context=production)


def index_events(events: list[IndustrialEvent]) -> dict[str, list[IndustrialEvent]]:
    by_asset: dict[str, list[IndustrialEvent]] = defaultdict(list)
    for e in events:
        by_asset[e.asset_id].append(e)
    return by_asset


def electrical_context(events: list[IndustrialEvent], now_s: float, window_s: float = 120.0) -> dict | None:
    v = [e for e in events if e.asset_id == "LV_BUS" and e.signal == "bus_voltage_pu" and e.received_sim_s is not None and now_s - window_s <= e.received_sim_s <= now_s]
    l = [e for e in events if e.asset_id == "TR_DEMO" and e.signal == "transformer_loading_pct" and e.received_sim_s is not None and now_s - window_s <= e.received_sim_s <= now_s]
    if not v and not l:
        return None
    return dict(bus_voltage_pu=v[-1].value if v else None, transformer_loading_pct=l[-1].value if l else None,
                age_s=round(now_s - (v[-1].sim_time_s if v else l[-1].sim_time_s), 1))


def network_context(bundle_items: list[EvidenceItem]) -> dict:
    ages = [it.age_ms for it in bundle_items if it.age_ms is not None]
    return dict(max_tag_age_ms=round(max(ages), 1) if ages else None, n_stale=sum(1 for it in bundle_items if it.quality == Quality.STALE))
