import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app, SESSIONS


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import apps.api.app.main as m
    import sfaic2.study.core as core
    m.DB_PATH = tmp_path_factory.mktemp("db") / "t.sqlite"
    core.DB_PATH = tmp_path_factory.mktemp("study") / "study.sqlite"
    return TestClient(app)


def test_study_blinded_queue_and_stats(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")
    r = client.post("/api/simulations", json=dict(preset="S2", seed=3, arm="E", provider="mock", speed=1000, autostart=False, duration_s=1500)).json()
    run_id = r["run_id"]
    client.post(f"/api/simulations/{run_id}/control", json=dict(action="step", value=1500))
    s = client.post("/api/study/sessions", json=dict(run_id=run_id, participant="p1", n_items=6, seed=1)).json()
    assert s["n_items"] == 6
    answered = 0
    while True:
        it = client.get(f"/api/study/sessions/{s['session_id']}/next").json()
        if it.get("done"):
            break
        assert "truth" not in json.dumps(it) and "jev" not in json.dumps(it).lower().replace("jev_", "")  # blinded
        a = client.post(f"/api/study/sessions/{s['session_id']}/answer", json=dict(item_id=it["item_id"], participant="p1", likely_cause="communications_issue", review_needed=True, risk=2, stop_warranted=False, response_ms=1200, confidence=3)).json()
        assert a["action"] == "REQUEST_RECHECK" and a["jev"] is not None  # cause-first policy; model answers revealed only after answering
        answered += 1
    assert answered == 6
    st = client.get("/api/study/stats").json()
    assert st["n_responses"] == 6 and "human" in st and "jev" in st and st["human"]["correct_action"] is not None
    csv = client.get("/api/study/export.csv").text
    assert csv.splitlines()[0].startswith("response_id,participant") and len(csv.splitlines()) == 7
    assert client.post("/api/study/sessions/x/answer", json=dict(item_id="x", participant="p1", likely_cause="bogus", review_needed=False, risk=0, stop_warranted=False, response_ms=1)).status_code == 422
    SESSIONS.pop(run_id, None)


def test_jev_runtime_toggle(client):
    assert client.post("/api/settings/jev", json=dict(enabled=False)).json()["mode"] == "DEMO_OFFLINE"
    assert client.post("/api/settings/jev", json=dict(enabled=True)).json()["jev_enabled"] is True
    client.post("/api/settings/jev", json=dict(enabled=False))
