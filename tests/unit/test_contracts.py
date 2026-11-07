import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sfaic2.contracts import FactoryConfig, IndustrialEvent, Quality, SourceType
from sfaic2.simulator.scenarios import demo_factory

ROOT = Path(__file__).resolve().parents[2]


def test_starter_example_converts_and_validates_against_json_schema():
    import jsonschema
    data = json.loads((ROOT / "examples/press_factory_demo.json").read_text())
    jsonschema.validate(data, json.loads((ROOT / "schemas/factory.schema.json").read_text()))
    cfg = FactoryConfig.from_starter(data)
    assert cfg.scenario_id == "press_shop_demo_v1" and len(cfg.machines) == 2 and cfg.faults[0].kind.value == "MECHANICAL_STOP"


def test_duplicate_machine_id_rejected():
    cfg = demo_factory().model_dump()
    cfg["machines"][1]["machine_id"] = cfg["machines"][0]["machine_id"]
    with pytest.raises(ValidationError, match="duplicate machine_id"):
        FactoryConfig.model_validate(cfg)


def test_real_network_target_rejected():
    cfg = demo_factory().model_dump()
    cfg["machines"][0]["machine_id"] = "PLC-192.168.1.22"
    with pytest.raises(ValidationError, match="real network target"):
        FactoryConfig.model_validate(cfg)


def test_only_simulated_source_type():
    cfg = demo_factory().model_dump()
    cfg["source_type"] = "OBSERVED_PUBLIC"
    with pytest.raises(ValidationError):
        FactoryConfig.model_validate(cfg)


def test_event_schema_dict_matches_json_schema():
    import jsonschema
    ev = IndustrialEvent(event_id="e1", run_id="r", sim_time_s=1.0, asset_id="PRESS_01", source_type=SourceType.SIMULATED,
                         signal="current_a", value=12.3, unit="A", quality=Quality.VALID, received_sim_s=1.05, age_ms=50)
    jsonschema.validate(ev.to_schema_dict(), json.loads((ROOT / "schemas/industrial_event.schema.json").read_text()))


def test_theoretical_capacity_bottleneck():
    cfg = demo_factory(duration_s=3600)
    cap = cfg.theoretical_capacity()
    assert cap["bottleneck_stage"] == "stage_0" and cap["line_capacity"] == min(cap["stage_0"], cap["stage_1"], cap["stage_2"])
