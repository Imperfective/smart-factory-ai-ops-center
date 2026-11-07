import random

from sfaic2.contracts import IndustrialEvent, Quality, SourceType
from sfaic2.simulator.network import EventCollector, LinkSpec, NetworkLink


def _ev(i, t):
    return IndustrialEvent(event_id=f"e{i}", run_id="r", sim_time_s=t, asset_id="M", source_type=SourceType.SIMULATED, signal="s", value=i)


def test_loss_duplication_and_stale_flags():
    link = NetworkLink(LinkSpec(median_latency_ms=100, jitter_ms=0, loss_probability=0.5, duplication_probability=0.5, tag_ttl_ms=50), random.Random(1))
    out = []
    for i in range(200):
        out += link.transmit(_ev(i, i))
    assert link.stats["lost"] > 0 and link.stats["duplicated"] > 0
    assert all(c.quality == Quality.STALE for c in out)  # 100 ms latency > 50 ms ttl
    assert any(c.duplicate for c in out)


def test_extra_delay_makes_stale_and_outage_drops():
    link = NetworkLink(LinkSpec(median_latency_ms=10, jitter_ms=0, tag_ttl_ms=500), random.Random(2))
    assert link.transmit(_ev(1, 0))[0].quality == Quality.VALID
    link.extra_delay_ms = 2000
    assert link.transmit(_ev(2, 1))[0].quality == Quality.STALE
    link.extra_delay_ms = 0
    link.outage_until_s = 100
    assert link.transmit(_ev(3, 5)) == []


def test_collector_orders_by_arrival_and_flags_reorder():
    link = NetworkLink(LinkSpec(median_latency_ms=10, jitter_ms=0, reorder_probability=1.0, tag_ttl_ms=5000), random.Random(3))
    col = EventCollector()
    col.ingest(link.transmit(_ev(1, 0.0)))
    link.spec.reorder_probability = 0.0
    col.ingest(link.transmit(_ev(2, 0.001)))
    got = col.finalize()
    assert [e.event_id for e in got] == ["e2", "e1"] and got[1].out_of_order
