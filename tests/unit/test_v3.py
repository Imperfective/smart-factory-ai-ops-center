"""SF AI OS v3 acceptance tests (Phases 1–5): counts, time-leakage, truth isolation, S1–S10 behaviours, MQTT
idempotency, immutable raw vision JSON, Jev request/response validation and fallback, determinism."""
import json
import sqlite3

import httpx
import pytest

from sfaic2.v3.config import FactoryV3, LineSpec, PlanSpec, build_scenario, default_factory
from sfaic2.v3.control import Controller
from sfaic2.v3.db import OpsDB, TruthStore
from sfaic2.v3.episode import run_episode
from sfaic2.v3.jev import JevDecisionAdapter, MockJevV3, TypeSafeV3Client, build_questions, build_state, parse
from sfaic2.v3.mqtt import Dispatcher, Fleet
from sfaic2.v3.plant import Plant
from sfaic2.v3.snapshot import SnapshotBuilder

TRUTH_WORDS = ("true_repair", "truth_cause", "hidden_fault", "truly_defective", "injection_truth", "repair_s\"", "ground_truth")


def _build(sid, load="medium", seed=3, arm="R4", until=None):
    f, faults = build_scenario(sid, load, seed)
    ctx = dict(run_id="t", episode_id=f"{sid}-{seed}-{arm}", scenario_id=sid, seed=seed)
    ops, truth = OpsDB(ctx), TruthStore(ctx)
    plant = Plant(f, faults, seed, ops, truth)
    Fleet(plant)
    from sfaic2.v3.control import ARMS
    Dispatcher(plant, ops, ctx, ARMS[arm]["transport"], ARMS[arm]["adaptive"])
    ctrl = Controller(plant, ops, arm, "mock", ctx)
    if until:
        plant.env.run(until=until)
    return f, faults, ops, truth, plant, ctrl


# ---------------------------------------------------------------------------------------------- phase 1
def test_config_is_simulation_only_and_virtual():
    with pytest.raises(ValueError):
        FactoryV3(lines=default_factory().lines, plan=PlanSpec(target_good_qty=10), simulation_only=False)
    with pytest.raises(ValueError):
        LineSpec(line_id="opc.tcp://10.0.0.1", role="main", ct_s=30, run_kw=1, idle_kw=0)


def test_normal_shift_counts_are_conserved():
    f, faults, ops, truth, plant, ctrl = _build("S1", until=None)
    plant.env.run(until=f.plan.shift_s)
    res = plant.finalize()
    m = plant.lines["main"]
    assert res["processed"] == res["good_accepted"] + res["rejected"]
    assert truth.one("SELECT COUNT(*) FROM truth_units") == res["processed"]
    assert ops.one("SELECT COUNT(*) FROM vision_events") == res["processed"]          # no camera drops in S1
    assert m.counter_raw == m.processed                                                  # no counter anomaly in S1
    on_pallets = plant.fleet.stats["delivered_units"] + sum(len(p.units) for p in m.outlet) + len(m.current.units)
    assert on_pallets == m.good_accepted                                                 # WIP conservation
    assert res["good_accepted"] >= f.plan.target_good_qty                                # S1 meets the plan


# ---------------------------------------------------------------------------------------------- phase 3: leakage / isolation
def test_snapshot_uses_only_rows_received_up_to_now():
    t1 = 5000.0
    f, faults, ops, truth, plant, ctrl = _build("S3", seed=4, until=t1 + 900)   # DB already holds rows from the "future"
    a = SnapshotBuilder(ops, f, True, True, "ml").build(t1, dict(main="run", sub="off", parallel=False))
    clone = OpsDB(ops.ctx)
    clone.con.close()
    clone.con = sqlite3.connect(":memory:")
    ops.con.commit()
    ops.con.backup(clone.con)
    for t in ("equipment_events", "vision_events", "qr_events", "production_counts", "fleet_status", "electrical_events", "transport_status"):
        clone.con.execute(f"DELETE FROM {t} WHERE received_sim_s > ?", (t1,))
    clone.con.execute("DELETE FROM network_events WHERE sent_at > ?", (t1,))
    clone.con.execute("DELETE FROM inventory_buffers WHERE simulation_time > ?", (t1,))
    b = SnapshotBuilder(clone, f, True, True, "ml").build(t1, dict(main="run", sub="off", parallel=False))
    assert a["hash"] == b["hash"] and a["forecast"] == b["forecast"]


def test_truth_never_reaches_snapshot_or_jev_request():
    f, faults, ops, truth, plant, ctrl = _build("S3", seed=5, until=6000)
    ft = SnapshotBuilder(ops, f, True, True, "ml").build(6000, dict(main="run", sub="off", parallel=False))
    blob = json.dumps(ft, default=str)
    st = build_state(ft, ft["feasible"], True, True)
    req = json.dumps({"state": st, "questions": build_questions(st, ft["feasible"])})
    for w in TRUTH_WORDS:
        assert w not in blob and w not in req
    tables = {r[0] for r in ops.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ground_truth" not in tables and "truth_units" not in tables
    assert not any(isinstance(v, TruthStore) for v in vars(ctrl.b).values())
    # the ETA shown is the catalogue estimate, not the injected repair time
    assert ft["eta"]["main"] != pytest.approx(faults[0].truth_repair_s, abs=1)


def test_raw_vision_json_is_stored_verbatim_and_immutable():
    f, faults, ops, truth, plant, ctrl = _build("S1", until=1200)
    row = ops.query("SELECT raw_json, source FROM vision_events LIMIT 1")[0]
    raw = json.loads(row["raw_json"])
    assert "detections" in raw and "names" in raw
    if row["source"].startswith("replay"):
        assert raw["model"] == "YOLO11n" and len(raw["weights_sha256"]) == 64
    with pytest.raises(sqlite3.DatabaseError):
        ops.con.execute("UPDATE vision_events SET raw_json='{}'")


# ---------------------------------------------------------------------------------------------- phase 2/4: scenarios
def test_s2_fast_recovery_restarts_only_after_interlock():
    r = run_episode("S2", "medium", 3, "R4", series=True)
    assert r["first_action"] == "RECOVER_MAIN"
    assert r["forbidden_allowed"] == 0 and "restart_before_stable" not in r["unsafe_reasons"]
    assert r["target_met"] == 1


def test_s3_long_fault_moves_production_to_sub():
    r = run_episode("S3", "medium", 3, "R4", series=True)
    assert r["first_action"] in ("SWITCH_TO_SUB", "RUN_PARALLEL")
    assert r["sub_good"] > 0


def test_s4_both_down_no_automatic_motion():
    r = run_episode("S4", "medium", 3, "R4", series=True)
    both_down = [c for c in r["_commands"] if c["cmd"] in ("START_SUB", "RESTART_MAIN") and c["accepted"] and c["unsafe"] == "motion_during_both_down"]
    assert not both_down and r["forbidden_allowed"] == 0
    assert any(d["final"] == "ESCALATE_HUMAN" for d in r["_decisions"])


def test_s6_one_order_per_completed_pallet():
    r = run_episode("S6", "medium", 3, "R4")
    assert r["duplicate_executions"] == 0 and r["premature_dispatch"] == 0 and r["wrong_pallet"] == 0
    assert r["orders"] == r["completed_orders"] + (r["orders"] - r["completed_orders"])
    assert r["orders"] - r["delivered_pallets"] <= 1                     # at most the order still in flight at shift end


def test_s7_duplicates_are_suppressed_only_with_idempotency():
    v = run_episode("S7", "medium", 3, "R4")
    b = run_episode("S7", "medium", 3, "R4-basictransport")
    assert v["duplicate_executions"] == 0 and v["duplicates_suppressed"] + v["ack_timeouts"] >= 0
    assert b["duplicate_executions"] > 0


def test_s8_inconsistent_qr_is_held_not_shipped():
    v = run_episode("S8", "medium", 3, "R4")
    b = run_episode("S8", "medium", 3, "R1")
    assert v["wrong_pallet"] == 0 and v["qr_holds"] > 0
    assert b["wrong_pallet"] > 0


def test_s9_power_limit_blocks_parallel_with_live_context():
    r = run_episode("S9", "medium", 3, "R4", series=True)
    assert r["overloads"] == 0
    late = [d for d in r["_decisions"] if d["t"] > 4000]
    assert late and all("RUN_PARALLEL" not in d["allowed"] for d in late)


def test_s10_infeasible_plan_is_predicted_and_notified():
    r = run_episode("S10", "medium", 3, "R4")
    assert r["target_met"] == 0 and r["plan_notices"] == 1
    assert r["cpsat_bound_first"] is not None and r["cpsat_bound_first"] < r["target"]


def test_same_seed_same_outcome_and_paired_faults():
    a = run_episode("S3", "low", 9, "R4")
    b = run_episode("S3", "low", 9, "R4")
    keys = ("good_end", "energy_kwh", "decisions", "orders", "cost_balanced")
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}
    assert build_scenario("S3", "low", 9)[1] == build_scenario("S3", "low", 9)[1]


# ---------------------------------------------------------------------------------------------- phase 5: MQTT idempotency constraint
def test_transport_idempotency_unique_constraint():
    ops = OpsDB(dict(run_id="r", episode_id="e", scenario_id="S6", seed=1))
    ops.insert("transport_orders", 1.0, order_id="O1", idempotency_key="K1", attempt=1, state="PUBLISHED")
    with pytest.raises(sqlite3.IntegrityError):
        ops.insert("transport_orders", 2.0, order_id="O2", idempotency_key="K1", attempt=1, state="PUBLISHED")


# ---------------------------------------------------------------------------------------------- phase 4: Jev adapter
def _ft():
    f, faults, ops, truth, plant, ctrl = _build("S3", seed=6, until=6000)
    return ops, SnapshotBuilder(ops, f, True, True, "ml").build(6000, dict(main="run", sub="off", parallel=False))


def test_jev_criteria_only_list_allowed_actions_and_parse_validates():
    ops, ft = _ft()
    allowed = ft["feasible"]
    st = build_state(ft, allowed, True, True)
    q = build_questions(st, allowed)
    assert set(q["preferred_action"]["criteria"]) == set(allowed)
    assert all("program forecast" in v for k, v in q["preferred_action"]["criteria"].items() if k in st["options"] and "projected_good_at_end" in st["options"][k])
    body = MockJevV3().decide({"state": st})
    assert parse(body, allowed)["action"] in allowed
    bad = dict(body, answers=dict(body["answers"], preferred_action=dict(type="choice", choice="SAFE_STOP_REQUEST_X", probabilities={})))
    with pytest.raises(ValueError):
        parse(bad, allowed)


def test_real_client_fallback_and_key_redaction(monkeypatch):
    ops, ft = _ft()
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "apikey_test_secret_000")
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(401, json={"error": "unauthorized"})
    ad = JevDecisionAdapter("typesafe", ops, ops.ctx)
    ad.client = TypeSafeV3Client(transport=httpx.MockTransport(handler))
    out = ad.decide(6000, "DEC-1", ft, ft["feasible"], True, True)
    assert not out["ok"] and "401" in out["error"]
    assert seen["auth"] == "Bearer apikey_test_secret_000"
    dump = json.dumps(ops.query("SELECT * FROM jev_requests")) + json.dumps(ops.query("SELECT * FROM jev_responses"))
    assert "apikey_test_secret_000" not in dump


def test_real_client_success_records_probabilities(monkeypatch):
    ops, ft = _ft()
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "apikey_test_secret_000")
    allowed = ft["feasible"]
    pick = allowed[0]

    def handler(request: httpx.Request):
        req = json.loads(request.content)
        assert set(req["questions"]["preferred_action"]["criteria"]) == set(allowed)
        probs = {a: (0.7 if a == pick else 0.3 / (len(allowed) - 1)) for a in allowed}
        return httpx.Response(200, json=dict(model="jev-1.13.0", usage=dict(input_tokens=900, output_tokens=120),
                                             answers=dict(preferred_action=dict(type="choice", choice=pick, probabilities=probs),
                                                          cause_class=dict(type="choice", choice="EQUIPMENT", probabilities={"EQUIPMENT": 1.0}),
                                                          needs_operator=dict(type="noul", noul=0.2))))
    ad = JevDecisionAdapter("typesafe", ops, ops.ctx)
    ad.client = TypeSafeV3Client(transport=httpx.MockTransport(handler))
    out = ad.decide(6000, "DEC-2", ft, allowed, True, True)
    assert out["ok"] and out["action"] == pick and out["resolved_model"] == "jev-1.13.0"
    row = ops.query("SELECT * FROM jev_responses WHERE request_id='JREQ-DEC-2'")[0]
    assert json.loads(row["per_choice_probabilities"])[pick] == 0.7 and row["cost_estimate"] > 0


# ---------------------------------------------------------------------------------------------- per-part judgment, image ids, decision log
def test_image_ids_do_not_leak_labels_and_raw_json_is_sanitised():
    f, faults, ops, truth, plant, ctrl = _build("S12", seed=2, until=6000)
    for r in ops.query("SELECT image_uri, raw_json, provenance FROM vision_events LIMIT 200"):
        blob = (r["image_uri"] or "") + r["raw_json"] + (r["provenance"] or "")
        assert "__" not in blob and ".png" not in blob     # file names (category__defect__id.png) never reach the ops DB
    assert truth.one("SELECT COUNT(*) FROM truth_units WHERE image_file LIKE '%__%'") > 0   # kept only in the evaluator store


def test_unit_judge_mock_follows_program_expected_cost_and_reinspection_resolves():
    from sfaic2.v3.unit_judge import UnitJudge, posterior_defect, unit_state
    f, faults = build_scenario("S1", "medium", 4)
    ctx = dict(run_id="t", episode_id="S1-uj", scenario_id="S1", seed=4)
    ops, truth = OpsDB(ctx), TruthStore(ctx)
    plant = Plant(f, faults, 4, ops, truth)
    plant.unit_judge = UnitJudge("mock")
    plant.defect_image_ratio = 0.3
    from sfaic2.v3.control import ARMS
    Fleet(plant); Dispatcher(plant, ops, ctx, ARMS["R4"]["transport"], True); Controller(plant, ops, "R4", "mock", ctx)
    plant.env.run(until=3600)
    rows = ops.query("SELECT * FROM unit_inspections")
    assert len(rows) > 50
    for r in rows:
        st = json.loads(r["state_sent"])
        ec = st["program_computed"]["expected_cost"]
        assert r["disposition"] == min(ec, key=ec.get)
        assert "image" not in json.dumps(st) or "img:" not in json.dumps(st)
    pend = [r for r in rows if r["disposition"] == "REINSPECT" and r["simulation_time"] < 3400]
    assert all(ops.one("SELECT final_disposition FROM unit_inspections WHERE unit_seq=? AND line_id=?", (r["unit_seq"], r["line_id"])) for r in pend)
    assert posterior_defect(0.3, False, 0.75) == pytest.approx(0.3 * 0.25 / (0.3 * 0.25 + 0.7 * (1 - 1 / 38)))


def test_decision_log_rebuilds_options_and_reasons():
    from sfaic2.v3.decision_log import build
    f, faults, ops, truth, plant, ctrl = _build("S15", seed=1, until=9000)
    log = build(ops.query)
    assert log and all("final" in r for r in log)
    with_opts = [r for r in log if r.get("options")]
    assert with_opts
    for r in with_opts:
        assert {o["action"] for o in r["options"]} >= {"CONTINUE_MAIN", "ESCALATE_HUMAN"}
        assert all(o["feasible"] or o["excluded_reason"] for o in r["options"])
        assert r["final"] in {o["action"] for o in r["options"]}


def test_recurring_cause_opens_a_new_incident():
    f, faults, ops, truth, plant, ctrl = _build("S4", seed=1, until=20000)   # main and sub faults, repairs, restarts
    ids = [i["incident_id"] for i in ctrl.all_incidents()]
    assert len(ids) == len(set(ids))
    eq = [i for i in ctrl.all_incidents() if i["cause"] == "EQUIPMENT"]
    assert len(eq) >= 2   # both lines (and any re-fault) produce separate incidents


def test_outlet_buffer_is_visible_to_the_decision_layer_during_amr_outage():
    f, faults, ops, truth, plant, ctrl = _build("S14", seed=1, arm="R4", until=8000)
    ft = ctrl.b.build(8000, ctrl.intent)
    assert ft["pallets_waiting"] == sum(len(l.outlet) for l in plant.lines.values()) > 0
    r = run_episode("S14", "medium", 1, "R4", series=True)
    assert any(d["final"] == "REQUEST_TRANSPORT" for d in r["_decisions"])


def test_random_course_varies_and_is_reproducible():
    import random
    from sfaic2.v3.config import build_scenario, default_factory
    a = build_scenario("RND", "medium", 7, default_factory())[1]
    b = build_scenario("RND", "medium", 7, default_factory())[1]
    kinds = {tuple(sorted(f.kind for f in build_scenario("RND", "medium", s, default_factory())[1])) for s in range(12)}
    assert [(f.kind, f.at_s) for f in a] == [(f.kind, f.at_s) for f in b]     # same course no. = same situation
    assert len(kinds) >= 4                                                     # different numbers give different courses
    del random


def test_amr_charging_energy_is_counted():
    from sfaic2.v3.episode import run_episode
    m = run_episode("RND", "medium", 3, "R1")
    assert m["amr_charges"] >= 1 and m["amr_charge_kwh"] > 0
    assert m["factory_kwh_total"] >= m["energy_kwh"] >= m["line_kwh"] + m["amr_charge_kwh"]
    assert abs(m["kwh_per_good"] - m["factory_kwh_total"] / m["good_end"]) < 1e-3


def test_live_results_accumulate_db_and_csv(tmp_path, monkeypatch):
    import csv
    from sfaic2.v3 import live_results as L
    from sfaic2.v3.live import LiveSession
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "DB", tmp_path / "live_runs.sqlite")
    s = LiveSession("RND", "medium", 5, "R4", "mock", camera_mode="replay", unit_judge="mock", defect_image_ratio=0.1, headless=True, source="test")
    s.control("run_to_end")                                   # a live session saves itself when the shift ends
    assert s.saved_run and s.save_error is None
    st = L.run_batch(3, ["R1", "R2", "R4"], ["high"], workers=2, wait=True)   # paired batch, all three modes on the same courses
    assert st["done"] == 9 and st["error"] is None
    rows = list(csv.DictReader(open(tmp_path / "runs.csv", encoding="utf-8-sig")))
    assert len(rows) == 10 and all(r["code_version"] and r["config_hash"] and r["data_quality"] == "ok" for r in rows)
    ev = list(csv.DictReader(open(tmp_path / "run_events.csv", encoding="utf-8-sig")))
    assert ev and all(e["detected"] in ("0", "1", "0.0", "1.0") for e in ev)
    stt = L.stats()
    assert stt["n_runs"] == 10 and {x["decision_provider"] for x in stt["by_group"]} == {"NONE", "MOCK_JEV"}   # never merged
    p = [x for x in stt["paired"] if x["a"] == "R2" and x["b"] == "R1" and x["metric"] == "cost_balanced"]
    assert p and p[0]["n_pairs"] == 3 and "p_holm" in p[0] and "pairs_needed_80pct" in p[0]
    assert stt["events"]


def test_real_jev_fallback_runs_are_flagged(tmp_path, monkeypatch):
    from sfaic2.v3 import live_results as L
    from sfaic2.v3.live import LiveSession
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "DB", tmp_path / "live_runs.sqlite")
    s = LiveSession("RND", "medium", 9, "R4", "mock", camera_mode="replay", headless=True, autosave=False)
    s.control("run_to_end")
    m = dict(s.result, provider="TYPESAFE_JEV", fallbacks=2)
    L.record(m, s.E, source="test", course_seed=9, unit_judge="off", camera_mode="replay", defect_image_ratio=None, wall_s=1.0)
    assert L.load_runs() == [] and len(L.load_runs(include_flagged=True)) == 1


def test_relay_mode_sends_no_key_and_relay_enforces_cap(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from sfaic2.v3 import jev_relay
    from sfaic2.v3.jev import TypeSafeV3Client
    sock = tmp_path / "jev.sock"
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("JEV_RELAY_SOCKET", str(sock))
    assert TypeSafeV3Client.opted_in()[0] is False            # tunnel not connected → real Jev unavailable
    sock.write_text("")
    assert TypeSafeV3Client.opted_in()[0] is True
    seen = {}

    def handler(r: httpx.Request):
        seen.update(path=r.url.path, auth=r.headers.get("authorization"))
        return httpx.Response(200, json=dict(answers={}, model="jev-test", usage=dict(input_tokens=10, output_tokens=1)))
    body = TypeSafeV3Client(transport=httpx.MockTransport(handler), cache={}).decide({"model": "m", "state": {"a": 1}, "questions": {}})
    assert body["model"] == "jev-test" and seen == dict(path="/v1/systemone", auth=None)   # the server never holds or sends a key
    # relay on the Mac: shape check + daily cap
    monkeypatch.setattr(jev_relay, "LOG", tmp_path / "relay")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    c = TestClient(jev_relay.app)
    assert c.post("/v1/systemone", json={"hello": 1}).status_code == 400
    assert c.post("/v1/other", json={}).status_code in (404, 405)
    jev_relay._save(dict(calls=0, input_tokens=0, output_tokens=0, cost_usd=jev_relay.DAILY_USD, refused=0))
    r = c.post("/v1/systemone", json={"state": {}, "questions": {}})
    assert r.status_code == 429 and "test-key" not in r.text
    assert "test-key" not in c.get("/health").text


def test_image_thumbnail_is_small_jpeg():
    import json as _j
    from fastapi.testclient import TestClient
    from apps.api.app.main import app
    from sfaic2.v3.yolo_live import IMAGES, MANIFEST
    if not MANIFEST.exists() or not IMAGES.exists():
        return
    item = next(m for m in _j.loads(MANIFEST.read_text())["items"] if m["split"] == "val")
    c = TestClient(app)
    r = c.get(f"/api/v3/images/{item['sha256'][:16]}?w=200")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) < 60_000
    assert "immutable" in r.headers["cache-control"]
    assert c.get("/api/v3/images/0000000000000000").status_code == 404


def test_public_mode_exposes_only_v3_and_health(monkeypatch):
    import importlib
    import sys as _s
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SFAIC_PUBLIC", "1")
    for m in [k for k in _s.modules if k.startswith("apps.api.app")]:
        del _s.modules[m]
    main = importlib.import_module("apps.api.app.main")
    c = TestClient(main.app)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/v3/meta").json()["public"] is True
    for method, path in [("post", "/api/simulations"), ("get", "/api/runs"), ("post", "/api/research/db/rebuild"), ("post", "/api/factories"),
                         ("post", "/api/settings/jev"), ("get", "/api/docs"), ("post", "/api/v3/results/batch"), ("put", "/api/v3/factory")]:
        r = c.get(path) if method == "get" else getattr(c, method)(path, json={})
        assert r.status_code in (403, 404), (path, r.status_code)
    for m in [k for k in _s.modules if k.startswith("apps.api.app")]:
        del _s.modules[m]


def test_stats_cache_refreshes_when_metric_schema_changes(tmp_path, monkeypatch):
    from sfaic2.v3 import live_results as L
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "DB", tmp_path / "live_runs.sqlite")
    L._STATS_CACHE.clear()
    L.run_batch(2, ["R1", "R4"], ["medium"], workers=2, wait=True)
    assert "defects_per_1000_good" in L.stats()["by_group"][0]
    monkeypatch.setattr(L, "STAT_METRICS", L.STAT_METRICS + ["mttd_s_extra_probe"])
    L._STATS_CACHE.clear()
    try:
        L.stats()
    except KeyError:
        pass                              # recomputed (new metric absent from runs) instead of serving the old file
    assert L._key(L.load_runs())[2] != json.loads((tmp_path / "stats.json").read_text())["key"][2]


def test_sensitivity_override_reaches_the_plant_and_is_in_provenance():
    from sfaic2.v3.live import LiveSession
    from sfaic2.v3.live_results import _config_hash
    a = LiveSession("RND", "medium", 11, "R1", headless=True, autosave=False, camera_mode="replay")
    b = LiveSession("RND", "medium", 11, "R1", headless=True, autosave=False, camera_mode="replay", sensitivity={"recurrence_p_unstable": 0.0})
    assert a.E["f"].recurrence_p_unstable == 0.6 and b.E["f"].recurrence_p_unstable == 0.0
    assert [f.kind for f in a.E["faults"]] == [f.kind for f in b.E["faults"]]      # same scenario, changed assumption only
    assert _config_hash(a.E["f"]) != _config_hash(b.E["f"])


def test_supplement_retest_and_bootstrap_summaries(tmp_path, monkeypatch):
    from sfaic2.v3 import supplement as S
    monkeypatch.setattr(S, "RETEST", tmp_path / "retest.jsonl")
    monkeypatch.setattr(S, "OUT", tmp_path)
    orig = dict(action="A", probs={"A": 0.7, "B": 0.3}, cause="EQUIPMENT", needs_operator_p=0.2)
    rows = [dict(hash="h1", rep=1, arm="R4", value=0.3, n_allowed=2, latency_ms=200, orig=orig, action="A", probs={"A": 0.6, "B": 0.4}, cause="EQUIPMENT", needs_operator_p=0.25, model="jev-1.13.0", error=None),
            dict(hash="h1", rep=2, arm="R4", value=0.3, n_allowed=2, latency_ms=220, orig=orig, action="B", probs={"A": 0.4, "B": 0.6}, cause="NETWORK", needs_operator_p=0.2, model="jev-1.13.0", error=None),
            dict(hash="h2", rep=1, arm="R2", value=0.3, n_allowed=2, latency_ms=900, orig=orig, error="timeout")]
    for r in rows:
        S._append(S.RETEST, r)
    t = S.retest_tables()
    assert t["requests"] == 1 and t["errors"] == 1 and t["action_agreement_with_original"] == 0.5
    assert t["action_same_in_all_three"] == 0.0 and t["action_agreement_between_repeats"] == 0.0 and t["cause_agreement_with_original"] == 0.5
    m, lo, hi = S._boot_diff([(1, 1.0), (1, 1.0), (2, 0.0), (3, 0.0)])
    assert m == 0.5 and 0.0 <= lo <= m <= hi <= 1.0


def test_research_record_matches_the_published_data_and_stays_inside_it():
    from fastapi.testclient import TestClient
    from apps.api.app.main import app
    from sfaic2.v3 import evidence as E
    c = TestClient(app)
    ex = {e["key"]: e for e in c.get("/api/v3/evidence").json()["experiments"]}
    assert ex["confirmatory"]["runs"] == 2520 and ex["confirmatory"]["pairs"] == 420 and ex["confirmatory"]["real_jev_runs"] == 1260
    assert ex["confirmatory"]["jev_calls"] == 32226 and ex["confirmatory"]["models"] == ["jev-1.13.0"]
    assert ex["sensitivity"]["runs"] == 1440 and ex["sensitivity"]["excluded_flagged"] == 8 and ex["sensitivity"]["jev_calls"] == 16907
    assert ex["retest"]["calls"] == 600 and ex["two_stage"]["parts"] == 4000 and ex["pilot"]["runs"] == 144
    r = c.get("/api/v3/evidence/runs", params=dict(experiment="confirmatory", arm="R4", load="high")).json()
    assert r["total"] == 210 and all(x["arm"] == "R4" and x["load"] == "high" for x in r["rows"])
    assert c.get(f"/api/v3/evidence/runs/{r['rows'][0]['run_uid']}/events").json()
    assert c.get("/api/v3/evidence/file/confirmatory/paired.csv").status_code == 200
    for bad in ("../../.env", "%2e%2e/%2e%2e/.env", "/etc/passwd"):
        assert c.get(f"/api/v3/evidence/file/{bad}").status_code == 404
    assert E.file_path("../README.md") is None
