from sfaic2.simulator.electrical import ElectricalTwin, current_a
from sfaic2.simulator.scenarios import demo_factory


def test_powerflow_runs_and_load_changes_voltage():
    cfg = demo_factory()
    twin = ElectricalTwin(cfg.electrical, cfg.machines)
    assert twin.available, twin.error
    idle = twin.solve({m.machine_id: m.power.idle_kw for m in cfg.machines})
    run = twin.solve({m.machine_id: m.power.run_kw for m in cfg.machines})
    heavy = twin.solve({m.machine_id: m.power.run_kw for m in cfg.machines}, {"FEEDER_1": 150.0})
    assert idle.status == run.status == heavy.status == "OK"
    assert idle.lv_bus_voltage_pu > run.lv_bus_voltage_pu > heavy.lv_bus_voltage_pu
    assert idle.transformer_loading_percent < run.transformer_loading_percent < heavy.transformer_loading_percent
    assert abs(run.total_active_kw - (sum(m.power.run_kw for m in cfg.machines) + cfg.electrical.auxiliary_kw)) < 5.0  # losses


def test_powerflow_failure_is_reported_not_faked():
    cfg = demo_factory()
    twin = ElectricalTwin(cfg.electrical, cfg.machines)
    res = twin.solve({m.machine_id: 1e6 for m in cfg.machines})  # absurd load => non-convergence
    assert res.status == "POWERFLOW_FAILED" and res.lv_bus_voltage_pu is None


def test_current_formula():
    assert current_a(0, 1.0, 0.4, 0.9) == 0.0
    i = current_a(23.0, 1.0, 0.4, 0.9)
    assert 36 < i < 38  # 23 kW / (sqrt3 * 400 V * 0.9) ≈ 36.9 A
