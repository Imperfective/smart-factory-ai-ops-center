import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app, SESSIONS


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import apps.api.app.main as m
    m.DB_PATH = tmp_path_factory.mktemp("db") / "t.sqlite"
    return TestClient(app)


def test_health_hides_key(client):
    h = client.get("/api/health").json()
    assert h["simulation_mode"] and h["control_target"] == "VIRTUAL_PLC_ONLY" and "jev_key_present" in h
    assert "apikey" not in json.dumps(h)


def test_factory_crud_and_duplicate_rejected(client):
    cfg = client.get("/api/factories/presets").json()["demo"]
    cfg["scenario_id"] = "api_test_factory"
    r = client.post("/api/factories", json=cfg)
    assert r.status_code == 200 and r.json()["capacity"]["line_capacity"] > 0
    assert client.post("/api/factories", json=cfg).status_code == 409
    bad = dict(cfg); bad["machines"] = cfg["machines"] + [cfg["machines"][0]]
    assert client.post("/api/factories", json=bad).status_code == 422
    assert client.get("/api/factories/api_test_factory").json()["config"]["scenario_id"] == "api_test_factory"


def test_simulation_lifecycle_fault_decision_command(client):
    r = client.post("/api/simulations", json=dict(preset="demo", seed=42, arm="E", provider="mock", speed=100, autostart=False,
                                                  duration_s=1200, quantity=100)).json()
    run_id = r["run_id"]
    # step without wall pacing
    client.post(f"/api/simulations/{run_id}/control", json=dict(action="step", value=300))
    st = client.get(f"/api/simulations/{run_id}/state").json()
    assert st["sim_time_s"] == 300 and st["kpis"]["good_count"] >= 0 and st["machines"]["PRESS_01"]["state"] in ("RUNNING", "IDLE")
    assert st["banner"].startswith("SIMULATED")
    # inject a real stop fault, step, evaluate -> STOP recommendation -> pending command
    f = client.post("/api/faults/inject", json=dict(run_id=run_id, kind="MECHANICAL_STOP", machine_id="PRESS_01", duration_s=600)).json()
    assert f["ok"]
    client.post(f"/api/simulations/{run_id}/control", json=dict(action="step", value=120))
    decs = client.post("/api/decision/evaluate", json=dict(run_id=run_id)).json()
    press = [d for d in decs if d["asset_id"] == "PRESS_01"]
    assert press and press[0]["recommendation"] == "REQUEST_SIMULATED_STOP"
    st = client.get(f"/api/simulations/{run_id}/state").json()
    assert st["pending_commands"], "engine should have proposed a STOP pending approval"
    cmd_id = st["pending_commands"][0]["command_id"]
    # unauthorised approval rejected; authorised approval executes virtual stop
    bad = client.post(f"/api/virtual-commands/{cmd_id}/approve", json=dict(run_id=run_id, operator_id="intruder")).json()
    assert bad["status"] == "REJECTED" and bad["reason"] == "OPERATOR_NOT_AUTHORISED"
    # a new proposal (operator button) then proper approval
    cmd = client.post("/api/virtual-commands/propose", json=dict(run_id=run_id, target_id="PRESS_01", requested_by="operator_a")).json()
    assert cmd["status"] == "PENDING_APPROVAL"
    ok = client.post(f"/api/virtual-commands/{cmd['command_id']}/approve", json=dict(run_id=run_id, operator_id="operator_a")).json()
    assert ok["status"] == "STOP_REQUESTED"
    client.post(f"/api/simulations/{run_id}/control", json=dict(action="step", value=60))
    st = client.get(f"/api/simulations/{run_id}/state").json()
    assert st["machines"]["PRESS_01"]["command"] in ("STOPPING", "STOPPED")
    audit = client.get("/api/audit", params=dict(run_id=run_id)).json()
    assert audit["gate_stats"]["executed"] == 1 and any(a["status"] == "REJECTED" for a in audit["gate_audit"])
    # real target refused
    bad = client.post("/api/virtual-commands/propose", json=dict(run_id=run_id, target_id="plc-10.0.0.9", requested_by="operator_a")).json()
    assert bad["status"] == "REJECTED" and bad["reason"] == "VIRTUAL_TARGET_REQUIRED"
    assert client.post("/api/virtual-commands/propose", json=dict(run_id=run_id, target_id="PRESS_02", requested_by="x", simulation_mode=False)).status_code == 400
    # run to the end and check persistence
    client.post(f"/api/simulations/{run_id}/control", json=dict(action="step", value=2000))
    st = client.get(f"/api/simulations/{run_id}/state").json()
    assert st["state"] == "FINISHED" and st["saved_path"]
    from pathlib import Path
    assert (Path(st["saved_path"]) / "run_manifest.json").exists() and (Path(st["saved_path"]) / "commands.jsonl").exists()
    SESSIONS.pop(run_id, None)


def test_typesafe_provider_refused_in_offline_mode(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")
    r = client.post("/api/simulations", json=dict(preset="S2", provider="typesafe", autostart=False))
    assert r.status_code == 400


def test_websocket_snapshot(client):
    r = client.post("/api/simulations", json=dict(preset="S1", seed=1, provider="mock", speed=1000, autostart=False, duration_s=300)).json()
    with client.websocket_connect(f"/ws/runs/{r['run_id']}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot" and msg["data"]["run_id"] == r["run_id"]
    SESSIONS.pop(r["run_id"], None)
