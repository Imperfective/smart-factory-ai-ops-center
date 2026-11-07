import pytest

from sfaic2.contracts.enums import FaultKind
from sfaic2.simulator.production import run_config
from sfaic2.simulator.scenarios import demo_factory, fault_preset, scenario_2_contradiction


@pytest.fixture(scope="module")
def base():
    return run_config(demo_factory(quantity=200, duration_s=3600), enable_electrical=False)


def test_determinism_same_seed_same_trace(base):
    again = run_config(demo_factory(quantity=200, duration_s=3600), enable_electrical=False)
    assert base.manifest["events_sha256"] == again.manifest["events_sha256"]
    assert base.kpis["good_count"] == again.kpis["good_count"]


def test_different_seed_different_trace(base):
    other = run_config(demo_factory(quantity=200, duration_s=3600, seed=7), enable_electrical=False)
    assert base.manifest["events_sha256"] != other.manifest["events_sha256"]


def test_conservation_and_nonnegative_wip(base):
    k = base.kpis
    assert k["conservation_ok"], k
    assert k["wip_end"] >= 0 and k["wip_max"] >= 0
    assert k["good_count"] <= k["released"]
    assert k["good_count"] <= k["theoretical_line_capacity"]


def test_cycle_time_changes_capacity(base):
    faster = run_config(demo_factory(quantity=200, duration_s=3600, press_ct=40.0), enable_electrical=False).kpis["good_count"]
    slower = run_config(demo_factory(quantity=200, duration_s=3600, press_ct=60.0), enable_electrical=False).kpis["good_count"]
    assert faster > base.kpis["good_count"] > slower


def test_plan_increase_changes_gap(base):
    bigger = run_config(demo_factory(quantity=1200, duration_s=3600), enable_electrical=False).kpis
    assert bigger["plan_gap_end"] > base.kpis["plan_gap_end"]
    assert bigger["plan_attainment"] < base.kpis["plan_attainment"]


def test_plan_met_then_idle():
    out = run_config(demo_factory(quantity=10, duration_s=3600), enable_electrical=False)
    assert out.kpis["good_count"] == 10 and out.kpis["plan_met_at_s"] is not None


def test_energy_units_and_states(base):
    k = base.kpis
    assert k["total_energy_kwh"] > 0 and k["peak_machine_kw"] > 0
    assert abs(k["machine_energy_kwh"] - sum(v["energy_kwh"] for v in k["per_machine"].values())) < 1e-2
    for v in k["per_machine"].values():
        assert v["run_s"] + v["idle_s"] + v["fault_s"] + v["stopped_s"] <= k["sim_seconds"] + 1e-6


def test_mechanical_stop_fault_reduces_output_and_truth_logged():
    f = fault_preset(FaultKind.MECHANICAL_STOP, 300, 1200, "PRESS_01")
    out = run_config(demo_factory(quantity=200, duration_s=3600, faults=[f]), enable_electrical=False)
    assert out.kpis["unplanned_fault_s"] > 1000
    assert any("PHYSICAL_STATE_ANOMALY" in t.truth_classes for t in out.truth)
    states = {e.value for e in out.events if e.asset_id == "PRESS_01" and e.signal == "machine_state"}
    assert "FAULT" in states


def test_network_delay_stale_but_physical_truth_unchanged():
    clean = run_config(demo_factory(quantity=200, duration_s=2400), enable_electrical=False)
    f = fault_preset(FaultKind.NETWORK_DELAY, 600, 900, "PRESS_01")
    delayed = run_config(demo_factory(quantity=200, duration_s=2400, faults=[f]), enable_electrical=False)
    assert delayed.network_stats["PRESS_01"]["stale"] > 0 and clean.network_stats["PRESS_01"]["stale"] == 0
    # observation-only fault: production truth identical (same seed, same physics)
    assert delayed.kpis["good_count"] == clean.kpis["good_count"]
    assert delayed.kpis["unplanned_fault_s"] == 0


def test_sensor_fault_reports_zero_current_while_running():
    f = fault_preset(FaultKind.SENSOR_FAULT, 300, 600, "PRESS_01")
    out = run_config(demo_factory(quantity=200, duration_s=1200, faults=[f]), enable_electrical=False)
    zeros = [e for e in out.events if e.asset_id == "PRESS_01" and e.signal == "current_a" and 300 <= e.sim_time_s < 900 and e.value == 0.0]
    running = [t for t in out.truth if t.machine_id == "PRESS_01" and 300 <= t.sim_time_s < 900 and t.physical_state == "RUNNING"]
    assert zeros and running  # contradiction visible to observers, truth says RUNNING


def test_scenario_2_has_qr_vision_and_stale_evidence():
    out = run_config(scenario_2_contradiction(duration_s=1800), enable_electrical=False)
    assert out.kpis["vision_misses"] > 0 and out.network_stats["PRESS_01"]["stale"] > 0
    assert any(e.signal == "qr_pallet_id" for e in out.events)
