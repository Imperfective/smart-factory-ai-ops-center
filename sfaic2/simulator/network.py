"""OT network simulator: per-link latency/jitter, loss, duplication, reordering, outage windows.

A link only affects *observation* (when/whether an event arrives, with what age). It never changes the
physical truth (docs/04 invariant). Deterministic given the seeded RNG.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..contracts.enums import Quality
from ..contracts.events import IndustrialEvent


@dataclass
class LinkSpec:
    median_latency_ms: float = 50.0
    jitter_ms: float = 10.0
    loss_probability: float = 0.0
    duplication_probability: float = 0.0
    reorder_probability: float = 0.0
    tag_ttl_ms: float = 500.0


@dataclass
class NetworkLink:
    spec: LinkSpec
    rng: random.Random
    extra_delay_ms: float = 0.0          # fault injection (gateway delay)
    outage_until_s: float | None = None   # fault injection (gateway offline: events dropped)
    seq: int = 0
    stats: dict = field(default_factory=lambda: dict(sent=0, delivered=0, lost=0, duplicated=0, reordered=0, stale=0))

    def transmit(self, ev: IndustrialEvent) -> list[IndustrialEvent]:
        """Return the copies that *arrive* (0, 1 or 2) with received_sim_s / age_ms / quality filled in."""
        self.stats["sent"] += 1
        self.seq += 1
        ev.seq = self.seq
        now = ev.sim_time_s
        if self.outage_until_s is not None and now < self.outage_until_s:
            self.stats["lost"] += 1
            return []
        if self.rng.random() < self.spec.loss_probability:
            self.stats["lost"] += 1
            return []
        copies = 1 + (1 if self.rng.random() < self.spec.duplication_probability else 0)
        out = []
        for k in range(copies):
            lat = max(0.0, self.rng.gauss(self.spec.median_latency_ms, self.spec.jitter_ms)) + self.extra_delay_ms
            if self.rng.random() < self.spec.reorder_probability:
                lat += 3 * self.spec.median_latency_ms + 2 * self.spec.jitter_ms
                self.stats["reordered"] += 1
            c = ev.model_copy(deep=True)
            c.received_sim_s = now + lat / 1000.0
            c.age_ms = lat
            c.ttl_ms = self.spec.tag_ttl_ms
            c.duplicate = k > 0
            if lat > self.spec.tag_ttl_ms and c.quality == Quality.VALID:
                c.quality = Quality.STALE
                self.stats["stale"] += 1
            if k > 0:
                self.stats["duplicated"] += 1
                c.event_id = f"{ev.event_id}_dup"
            out.append(c)
        self.stats["delivered"] += len(out)
        return out


class EventCollector:
    """Receives transmitted copies, orders them by arrival time and flags out-of-order arrivals per asset."""

    def __init__(self):
        self.received: list[IndustrialEvent] = []
        self._last_seq: dict[tuple[str, str], int] = {}

    def ingest(self, copies: list[IndustrialEvent]) -> None:
        self.received.extend(copies)

    def finalize(self) -> list[IndustrialEvent]:
        self.received.sort(key=lambda e: (e.received_sim_s if e.received_sim_s is not None else e.sim_time_s, e.seq or 0))
        for e in self.received:
            key = (e.asset_id, e.signal)
            last = self._last_seq.get(key, -1)
            if e.seq is not None and e.seq < last:
                e.out_of_order = True
            else:
                self._last_seq[key] = e.seq or last
        return self.received
