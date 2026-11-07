"""Acceptance checks from prompts/CODEX_MASTER_PROMPT.md §[5] that are not covered elsewhere."""
from sfaic2.contracts.enums import CommandStatus, FaultKind
from sfaic2.decision.engine import DecisionEngine, OperatorPolicy
from sfaic2.decision.providers import MockJevProvider
from sfaic2.simulator.production import FactorySim, RunOutput, run_config
from sfaic2.simulator.scenarios import demo_factory, fault_preset


def _coupled(cfg, seed, arm="D", approve=1.0):
    sim = FactorySim(cfg, seed=seed, enable_electrical=False)
    sim.start()
    stub = RunOutput(run_id=sim.run_id, config=cfg, seed=seed, events=[], truth=sim.truth, kpis={}, manifest=dict(sim_seconds=cfg.simulation.duration_s), plc_history=[], network_stats={}, powerflow_failures=0)
    eng = DecisionEngine(stub, arm=arm, provider_override=MockJevProvider(), plc=sim.plc, events_source=sim.observed_events,
                         operator=OperatorPolicy(delay_s=5, approve_rate=approve), auto_operator=True)
    t = 30.0
    while t <= cfg.simulation.duration_s:
        sim.advance(t)
        eng.step(t)
        t += 30.0
    return sim.finalize(), eng.result()


def test_approved_virtual_stop_reduces_production_and_power_with_ack():
    # naive arm A stops PRESS_01 on a stuck current sensor (plant physically fine) -> measurable consequence
    f = fault_preset(FaultKind.SENSOR_FAULT, 300, 900, "PRESS_01")
    cfg = demo_factory(quantity=300, duration_s=2400, faults=[f])
    stopped, res = _coupled(cfg, 7, arm="A")
    clean = run_config(demo_factory(quantity=300, duration_s=2400, faults=[f]), seed=7, enable_electrical=False)
    assert res.gate_stats["executed"] >= 1
    cmd = next(c for c in res.commands if c.status in (CommandStatus.STOPPED, CommandStatus.STOPPING))
    assert cmd.ack_sim_s is not None and cmd.ack_sim_s - cmd.proposed_sim_s >= 5  # operator delay + PLC ack
    assert stopped.kpis["good_count"] < clean.kpis["good_count"]
    assert stopped.kpis["per_machine"]["PRESS_01"]["energy_kwh"] < clean.kpis["per_machine"]["PRESS_01"]["energy_kwh"]
    assert stopped.kpis["per_machine"]["PRESS_01"]["stopped_s"] > 0
    # with the evidence-checked arm the same scenario produces no stop
    careful, res2 = _coupled(cfg, 7, arm="D")
    assert res2.gate_stats["executed"] == 0 and careful.kpis["good_count"] == clean.kpis["good_count"]


def test_three_seed_replay_is_deterministic():
    cfg = demo_factory(quantity=200, duration_s=1200, faults=[fault_preset(FaultKind.NETWORK_DELAY, 300, 600, "PRESS_01")])
    for seed in (1, 2, 3):
        a, ra = _coupled(cfg, seed)
        b, rb = _coupled(cfg, seed)
        assert a.manifest["events_sha256"] == b.manifest["events_sha256"]
        assert [d.recommendation for d in ra.decisions] == [d.recommendation for d in rb.decisions]
        assert a.manifest["seed"] == seed and a.manifest["config_sha256"] == b.manifest["config_sha256"]
