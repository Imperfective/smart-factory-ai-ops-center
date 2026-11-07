"""SF-AIC2 FastAPI backend. Reads `.env` (TYPESAFE_API_KEY stays server-side; never returned by any endpoint).
Fail-closed: refuses to start unless SIMULATION_MODE=true and CONTROL_TARGET=VIRTUAL_PLC_ONLY."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv(Path(".env"), override=False)
if os.environ.get("SIMULATION_MODE", "true").lower() != "true" or os.environ.get("CONTROL_TARGET", "VIRTUAL_PLC_ONLY") != "VIRTUAL_PLC_ONLY":
    raise SystemExit("REFUSING TO START: SIMULATION_MODE must be true and CONTROL_TARGET must be VIRTUAL_PLC_ONLY")

from sfaic2 import __version__  # noqa: E402
from sfaic2.contracts.enums import FaultKind  # noqa: E402
from sfaic2.contracts.factory import FactoryConfig  # noqa: E402
from sfaic2.simulator.scenarios import SCENARIO_INFO, SCENARIOS, demo_factory, fault_preset  # noqa: E402

from .session import SimulationSession  # noqa: E402

DB_PATH = Path(os.environ.get("SFAIC2_DB", "results/sfaic2.sqlite"))
PUBLIC = os.environ.get("SFAIC_PUBLIC", "0") == "1"
app = FastAPI(title="SF-AIC2 Smart Factory AI Command Center (SIMULATION ONLY)", version=__version__, docs_url=None if PUBLIC else "/api/docs")
if PUBLIC:
    # public demo: only the v3 operations-center API and health are reachable; the legacy v2 API (unbounded simulations,
    # factory/study writes, research-DB rebuild, websockets) is refused so visitors cannot load the shared server
    from starlette.responses import JSONResponse as _JR

    @app.middleware("http")
    async def _public_only_v3(request, call_next):
        p = request.url.path
        if (p.startswith("/api/") or p.startswith("/ws/")) and not (p.startswith("/api/v3/") or p == "/api/health"):
            return _JR({"detail": "not available on the public demo"}, status_code=403)
        return await call_next(request)
from .v3 import router as v3_router  # noqa: E402  SF AI OS v3 (plan-aware command center)
app.include_router(v3_router)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
SESSIONS: dict[str, SimulationSession] = {}


# ----------------------------------------------------------------- tiny sqlite store (configs + run index)
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS factories (id TEXT PRIMARY KEY, created REAL, config TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, created REAL, factory_id TEXT, arm TEXT, provider TEXT, status TEXT, path TEXT)")
    return con


def _mode() -> str:
    return "EXPERIMENT_JEV" if os.environ.get("JEV_ENABLED", "false").lower() == "true" else "DEMO_OFFLINE"


@app.get("/api/health")
def health():
    key = os.environ.get("TYPESAFE_API_KEY", "")
    relay = os.environ.get("JEV_RELAY_SOCKET")
    return dict(status="ok", version=__version__, mode=_mode(), simulation_mode=True, control_target="VIRTUAL_PLC_ONLY",
                jev_enabled=os.environ.get("JEV_ENABLED", "false").lower() == "true", jev_via_relay=bool(relay),
                jev_key_present=(os.path.exists(relay) if relay else bool(key) and not key.startswith("REPLACE_")), jev_model=os.environ.get("JEV_MODEL", "jev-latest"),
                banner="SIMULATED — no physical equipment is controlled; this is not an emergency-stop system")


# ----------------------------------------------------------------- factories
@app.get("/api/factories/presets")
def presets():
    base = demo_factory()
    out = {"demo": base.model_dump()}
    for k, fn in SCENARIOS.items():
        out[k] = fn().model_dump()
    out["_info"] = SCENARIO_INFO
    return out


@app.get("/api/factories")
def list_factories():
    with _db() as con:
        rows = con.execute("SELECT id, created, config FROM factories ORDER BY created DESC").fetchall()
    return [dict(id=r[0], created=r[1], scenario_id=json.loads(r[2])["scenario_id"]) for r in rows]


@app.post("/api/factories")
def create_factory(config: dict):
    try:
        cfg = FactoryConfig.model_validate(config) if "power" in json.dumps(config.get("machines", [{}])[0]) else FactoryConfig.from_starter(config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid factory config: {exc}")
    with _db() as con:
        if con.execute("SELECT 1 FROM factories WHERE id=?", (cfg.scenario_id,)).fetchone():
            raise HTTPException(409, f"factory id '{cfg.scenario_id}' already exists (ids must be unique)")
        con.execute("INSERT INTO factories VALUES (?,?,?)", (cfg.scenario_id, time.time(), cfg.model_dump_json()))
    return dict(id=cfg.scenario_id, config_sha256=cfg.config_hash(), capacity=cfg.theoretical_capacity())


@app.get("/api/factories/{fid}")
def get_factory(fid: str):
    with _db() as con:
        row = con.execute("SELECT config FROM factories WHERE id=?", (fid,)).fetchone()
    if not row:
        raise HTTPException(404, fid)
    cfg = FactoryConfig.model_validate_json(row[0])
    return dict(config=cfg.model_dump(), capacity=cfg.theoretical_capacity(), config_sha256=cfg.config_hash())


@app.post("/api/factories/validate")
def validate_factory(config: dict):
    try:
        cfg = FactoryConfig.model_validate(config)
    except Exception as exc:  # noqa: BLE001
        return dict(valid=False, error=str(exc))
    return dict(valid=True, capacity=cfg.theoretical_capacity(), config_sha256=cfg.config_hash())


# ----------------------------------------------------------------- simulations
class StartRequest(BaseModel):
    factory_id: str | None = None
    preset: str | None = Field(None, description="demo | S1 | S2 | S3")
    config: dict | None = None
    seed: int | None = None
    arm: str = "E"
    provider: str = Field("mock", description="mock | typesafe | none")
    speed: float = 10.0
    decision_interval_s: float = 30.0
    auto_operator: bool = False
    autostart: bool = True
    duration_s: int | None = None
    quantity: int | None = None


@app.post("/api/simulations")
def start_simulation(req: StartRequest):
    if req.config:
        cfg = FactoryConfig.model_validate(req.config)
    elif req.factory_id:
        cfg = FactoryConfig.model_validate(get_factory(req.factory_id)["config"])
    else:
        preset = req.preset or "demo"
        cfg = demo_factory() if preset == "demo" else SCENARIOS[preset]()
    if req.duration_s:
        cfg.simulation.duration_s = req.duration_s
    if req.quantity is not None:
        cfg.production_plan.quantity = req.quantity
    if req.provider == "typesafe" and _mode() != "EXPERIMENT_JEV":
        raise HTTPException(400, "provider=typesafe requires JEV_ENABLED=true in the server .env (DEMO_OFFLINE mode active)")
    arm = req.arm if req.provider != "none" else ("B" if req.arm in "CDE" else req.arm)
    s = SimulationSession(cfg, req.seed, arm=arm, provider=req.provider, speed=req.speed, decision_interval_s=req.decision_interval_s,
                          auto_operator=req.auto_operator)
    SESSIONS[s.run_id] = s
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)", (s.run_id, time.time(), req.factory_id or req.preset or cfg.scenario_id, arm, req.provider, "created", None))
    if req.autostart:
        s.start()
    return dict(run_id=s.run_id, state=s.state, arm=arm, provider=req.provider, duration_s=cfg.simulation.duration_s, seed=s.sim.seed)


def _session(run_id: str) -> SimulationSession:
    s = SESSIONS.get(run_id)
    if not s:
        raise HTTPException(404, f"unknown run {run_id}")
    return s


class ControlRequest(BaseModel):
    action: str = Field(description="pause | resume | step | reset | speed | stop")
    value: float | None = None


@app.post("/api/simulations/{run_id}/control")
def control(run_id: str, req: ControlRequest):
    s = _session(run_id)
    if req.action == "pause":
        s.pause()
    elif req.action in ("resume", "start"):
        s.resume()
    elif req.action == "step":
        s.step(req.value or 10.0)
    elif req.action == "speed":
        s.set_speed(req.value or 1.0)
    elif req.action == "stop":
        s.stop()
    elif req.action == "reset":
        s.stop()
        new = SimulationSession(s.sim.cfg.model_copy(deep=True), s.sim.seed, arm=s.arm, provider=s.provider, speed=s.speed,
                                decision_interval_s=s.engine.interval, auto_operator=s.engine.auto_operator)
        new.sim.cfg.faults = [f for f in new.sim.cfg.faults]  # faults injected live were appended to cfg; keep as configured
        SESSIONS[new.run_id] = new
        return dict(run_id=new.run_id, state=new.state, reset_from=run_id)
    else:
        raise HTTPException(400, "unknown action")
    return dict(run_id=run_id, state=s.state, sim_time_s=s.sim.env.now, speed=s.speed)


@app.get("/api/simulations")
def list_sessions():
    return [dict(run_id=k, state=v.state, sim_time_s=v.sim.env.now, arm=v.arm, provider=v.provider) for k, v in SESSIONS.items()]


@app.get("/api/simulations/{run_id}/state")
def sim_state(run_id: str):
    return _session(run_id).snapshot()


@app.get("/api/simulations/{run_id}/accuracy")
def sim_accuracy(run_id: str):
    """Judgment accuracy of the running session against the hidden simulator truth (research view only)."""
    from sfaic2.experiments.metrics import live_accuracy
    s = _session(run_id)
    with s._lock:
        acc = live_accuracy(s.sim.truth, s.engine.decisions)
    return dict(run_id=run_id, arm=s.arm, provider=s.provider, sim_time_s=s.sim.env.now, **acc,
                note="hidden truth is never visible to the decision layer; this view is for research evaluation")


@app.get("/api/simulations/{run_id}/events")
def sim_events(run_id: str, limit: int = 200, asset_id: str | None = None, signal: str | None = None):
    s = _session(run_id)
    evs = s.sim.observed_events()
    if asset_id:
        evs = [e for e in evs if e.asset_id == asset_id]
    if signal:
        evs = [e for e in evs if e.signal == signal]
    return [dict(e.to_schema_dict(), received_sim_s=e.received_sim_s, age_ms=e.age_ms, duplicate=e.duplicate, out_of_order=e.out_of_order) for e in evs[-limit:]]


@app.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    await ws.accept()
    try:
        s = SESSIONS.get(run_id)
        if not s:
            await ws.send_json(dict(error="unknown run"))
            await ws.close()
            return
        last = None
        while True:
            snap = s.snapshot()
            key = (snap["sim_time_s"], snap["state"], len(snap["timeline"]))
            if key != last:
                await ws.send_json(dict(type="snapshot", data=snap))
                last = key
            if snap["state"] in ("FINISHED", "ERROR"):
                await asyncio.sleep(0.5)
                await ws.send_json(dict(type="final", data=snap))
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


# ----------------------------------------------------------------- faults
class FaultRequest(BaseModel):
    run_id: str
    kind: FaultKind
    machine_id: str
    duration_s: float = 300
    severity: float = 1.0
    delay_s: float = 0.0


@app.post("/api/faults/inject")
def inject(req: FaultRequest):
    s = _session(req.run_id)
    f = fault_preset(req.kind, s.sim.env.now + req.delay_s, req.duration_s, req.machine_id, req.severity,
                     fault_id=f"live_{req.kind.value.lower()}_{int(s.sim.env.now)}")
    if not s.sim.plc.is_virtual_target(req.machine_id) and req.machine_id not in {m.machine_id for m in s.sim.cfg.machines}:
        raise HTTPException(400, "unknown virtual machine")
    s.inject_fault(f)
    return dict(ok=True, fault=f.model_dump(), sim_time_s=s.sim.env.now)


# ----------------------------------------------------------------- decisions
class EvaluateRequest(BaseModel):
    run_id: str


@app.post("/api/decision/evaluate")
def evaluate(req: EvaluateRequest):
    s = _session(req.run_id)
    decs = s.evaluate_now()
    return [d.model_dump() for d in decs]


@app.get("/api/decisions/{decision_id}")
def get_decision(decision_id: str, run_id: str):
    s = _session(run_id)
    d = next((d for d in s.engine.decisions if d.decision_id == decision_id), None)
    if not d:
        raise HTTPException(404, decision_id)
    return d.model_dump()


# ----------------------------------------------------------------- virtual commands
class ProposeRequest(BaseModel):
    run_id: str
    target_id: str
    requested_by: str
    decision_id: str | None = None
    simulation_mode: bool = True


class ApproveRequest(BaseModel):
    run_id: str
    operator_id: str
    approved: bool = True


@app.post("/api/virtual-commands/propose")
def propose(req: ProposeRequest):
    s = _session(req.run_id)
    if not req.simulation_mode:
        raise HTTPException(400, "SIMULATION_ONLY: non-simulated commands are refused")
    cmd = s.propose_stop(req.target_id, req.requested_by, req.decision_id)
    return cmd.model_dump()


@app.post("/api/virtual-commands/{command_id}/approve")
def approve(command_id: str, req: ApproveRequest):
    s = _session(req.run_id)
    try:
        return s.approve(command_id, req.operator_id, req.approved).model_dump()
    except KeyError:
        raise HTTPException(404, command_id)


@app.get("/api/audit")
def audit(run_id: str):
    s = _session(run_id)
    return dict(run_id=run_id, gate_audit=s.engine.gate.audit, plc_history=s.sim.plc.history, gate_stats=s.engine.gate.stats)


# ----------------------------------------------------------------- human-vs-JEV judgment study
class StudyCreate(BaseModel):
    run_id: str
    participant: str = "participant_1"
    n_items: int = 12
    seed: int = 7


def _load_saved_run(run_id: str):
    """Decisions + hidden truth of a persisted run (results/runs/<run_id>/) for post-hoc studies after the live session is gone."""
    from sfaic2.contracts.decisions import DecisionEnvelope
    from sfaic2.contracts.events import TruthRecord
    d = Path("results/runs") / Path(run_id).name
    if not (d / "decisions.jsonl").exists():
        raise HTTPException(404, f"run {run_id} is neither live nor saved")
    decisions = [DecisionEnvelope.model_validate(json.loads(l)) for l in (d / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    truth = [TruthRecord.model_validate(json.loads(l)) for l in (d / "truth.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    man = json.loads((d / "run_manifest.json").read_text()) if (d / "run_manifest.json").exists() else {}
    return decisions, truth, man


@app.get("/api/runs")
def list_saved_runs(limit: int = 50):
    root = Path("results/runs")
    out = []
    if root.exists():
        for d in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            m = d / "run_manifest.json"
            if m.exists():
                man = json.loads(m.read_text())
                out.append(dict(run_id=d.name, scenario_id=man.get("scenario_id"), arm=man.get("arm"), provider=(man.get("arm_config") or {}).get("provider"),
                                n_decisions=man.get("n_decisions"), sim_seconds=man.get("sim_seconds"), live=man.get("live", False), saved=d.stat().st_mtime))
    return out


@app.post("/api/study/sessions")
def study_create(req: StudyCreate):
    from sfaic2.study.core import create_session, sample_items
    s = SESSIONS.get(req.run_id)
    if s is not None:
        with s._lock:
            items = sample_items(s.engine.decisions, s.sim.truth, req.n_items, req.seed)
        arm, provider, scen = s.arm, s.provider, (s.snapshot().get("scenario", {}).get("key") or s.sim.cfg.scenario_id)
    else:
        decisions, truth, man = _load_saved_run(req.run_id)
        items = sample_items(decisions, truth, req.n_items, req.seed)
        arm, provider, scen = man.get("arm", "?"), (man.get("arm_config") or {}).get("provider", "?"), man.get("scenario_id", "?")
    if not items:
        raise HTTPException(400, "no decision points yet; run the simulation first")
    return create_session(req.run_id, req.participant, items, req.seed, arm, provider, scen)


@app.get("/api/study/sessions/{sid}/next")
def study_next(sid: str):
    from sfaic2.study.core import next_item
    it = next_item(sid)
    return it or dict(done=True)


class StudyAnswer(BaseModel):
    item_id: str
    participant: str
    likely_cause: str
    review_needed: bool
    risk: int = Field(ge=0, le=3)
    stop_warranted: bool
    response_ms: float
    confidence: int | None = Field(None, ge=1, le=5)


@app.post("/api/study/sessions/{sid}/answer")
def study_answer(sid: str, req: StudyAnswer):
    from sfaic2.study.core import answer
    try:
        return answer(sid, req.item_id, req.participant, req.likely_cause, req.review_needed, req.risk, req.stop_warranted, req.response_ms, req.confidence)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/study/stats")
def study_stats(participant: str | None = None):
    from sfaic2.study.core import stats
    return stats(participant)


@app.get("/api/study/export.csv")
def study_export():
    import csv, io
    from fastapi.responses import PlainTextResponse
    from sfaic2.study.core import export_rows
    rows = export_rows()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


class JevToggle(BaseModel):
    enabled: bool


@app.post("/api/settings/jev")
def set_jev(req: JevToggle):
    """Runtime JEV on/off for NEW live runs (process env only; the key never leaves the server)."""
    if os.environ.get("SFAIC_PUBLIC", "0") == "1":
        raise HTTPException(403, "public demo: Jev setting is fixed")
    os.environ["JEV_ENABLED"] = "true" if req.enabled else "false"
    return dict(jev_enabled=req.enabled, mode=_mode())


@app.get("/api/vision/log")
def vision_log():
    p = Path("results/vision/vision_log.jsonl")
    if not p.exists():
        return dict(summary=None, rows=[])
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    s = Path("results/vision/summary.json")
    return dict(summary=json.loads(s.read_text()) if s.exists() else None, rows=rows)


@app.get("/api/vision/images/{name}")
def vision_image(name: str):
    from fastapi.responses import FileResponse
    p = Path("results/vision/images") / Path(name).name
    if not p.exists():
        raise HTTPException(404, name)
    return FileResponse(p)


@app.get("/api/public/ai4i")
def public_ai4i():
    p = Path("results/public/ai4i/ai4i_baseline.json")
    if not p.exists():
        raise HTTPException(404, "run: .venv/bin/python -m sfaic2.cli public-ai4i")
    return json.loads(p.read_text())


# ----------------------------------------------------------------- experiments (results produced by sfaic2.experiments)
@app.get("/api/experiments")
def list_experiments():
    root = Path("results/experiments")
    if not root.exists():
        return []
    return [dict(id=p.name, files=sorted(x.name for x in p.iterdir())) for p in sorted(root.iterdir()) if p.is_dir()]


@app.get("/api/experiments/{exp_id}/figures/{name}")
def experiment_figure(exp_id: str, name: str):
    from fastapi.responses import FileResponse
    p = (Path("results/experiments") / exp_id / "figures" / Path(name).name)
    if not p.exists():
        raise HTTPException(404, name)
    return FileResponse(p)


@app.get("/api/experiments/{exp_id}/results")
def experiment_results(exp_id: str):
    d = Path("results/experiments") / exp_id
    if not d.exists():
        raise HTTPException(404, exp_id)
    out: dict[str, Any] = {"id": exp_id}
    for name in ("manifest.json", "metrics_primary.csv", "metrics_by_fault.csv", "paired_ci.csv", "jev_usage.csv", "ablations.csv"):
        p = d / name
        if p.exists():
            out[name] = json.loads(p.read_text()) if name.endswith(".json") else p.read_text()
    out["figures"] = sorted(x.name for x in (d / "figures").iterdir()) if (d / "figures").exists() else []
    return out


# ----------------------------------------------------------------- research database (data → simulation inputs → results → statistics)
_db_build = dict(state="idle", result=None, error=None)


@app.get("/api/research/db")
def research_db_overview():
    from sfaic2.research.db import overview
    return dict(overview(), build=_db_build)


@app.post("/api/research/db/rebuild")
def research_db_rebuild():
    import threading
    from sfaic2.research.db import rebuild
    if _db_build["state"] == "running":
        return _db_build

    def work():
        try:
            _db_build.update(state="running", error=None)
            _db_build.update(result=rebuild(), state="done")
        except Exception as exc:  # noqa: BLE001
            _db_build.update(state="error", error=f"{type(exc).__name__}: {exc}")
    threading.Thread(target=work, daemon=True).start()
    return dict(_db_build, state="running")


@app.get("/api/research/db/aggregates")
def research_db_aggregates(exp: str = "jev_pilot", grp: str = "arm"):
    from sfaic2.research.db import aggregates
    return aggregates(exp, grp)


@app.get("/api/research/db/runs")
def research_db_runs(exp: str | None = None, limit: int = 500):
    from sfaic2.research.db import runs
    return runs(exp, limit)


@app.get("/api/research/db/runs/{run_id:path}")
def research_db_run(run_id: str):
    from sfaic2.research.db import run_detail
    d = run_detail(run_id)
    if not d:
        raise HTTPException(404, run_id)
    return d


@app.get("/api/research/db/export/{table}.csv")
def research_db_export(table: str, exp: str | None = None):
    from fastapi.responses import Response
    from sfaic2.research.db import EXPORTABLE, export_csv
    if table not in EXPORTABLE:
        raise HTTPException(404, f"exportable: {', '.join(EXPORTABLE)}")
    return Response(export_csv(table, exp), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={table}{'_' + exp if exp else ''}.csv"})


@app.get("/api/research/jev-design")
def research_jev_design(exp: str = "jev_pilot"):
    from sfaic2.research.design import design_spec, detection_evidence
    return dict(spec=design_spec(), evidence=detection_evidence(exp))


# ----------------------------------------------------------------- built web UI (production: SFAIC_STATIC_DIR=apps/web/dist)
_STATIC = os.environ.get("SFAIC_STATIC_DIR")
if _STATIC and Path(_STATIC).is_dir():
    from fastapi.responses import FileResponse as _FR
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=Path(_STATIC) / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = Path(_STATIC) / path
        return _FR(f if path and f.is_file() and f.resolve().is_relative_to(Path(_STATIC).resolve()) else Path(_STATIC) / "index.html")
