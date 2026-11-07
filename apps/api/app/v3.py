"""SF AI OS v3 API (mounted under /api/v3). Virtual PLC / virtual MQTT only; ground truth only in researcher mode."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sfaic2.v3.config import LOADS, SCENARIOS, FactoryV3, default_factory
from sfaic2.v3.control import ARM_LABEL, ARMS
from sfaic2.v3.live import LiveSession

router = APIRouter(prefix="/api/v3")
SESSIONS: dict[str, LiveSession] = {}
PUBLIC = os.environ.get("SFAIC_PUBLIC", "0") == "1"      # public demo: read-only research data, capped sessions, no batches
MAX_PUBLIC_SESSIONS = int(os.environ.get("SFAIC_MAX_SESSIONS", "4"))


def _real_jev_ok() -> bool:
    from sfaic2.v3.jev import TypeSafeV3Client
    return TypeSafeV3Client.opted_in()[0]


def _public_block(what: str):
    if PUBLIC:
        raise HTTPException(403, f"공개 데모에서 쓸 수 없는 기능입니다: {what} (연구자 로컬 환경 전용)")
FACTORY_DIR = Path("results/v3/factories")
EXP_ROOT = Path("results/v3")


@router.get("/meta")
def meta():
    return dict(scenarios=[s.model_dump() for s in SCENARIOS.values()], loads=LOADS, arms=[dict(arm=a, label=ARM_LABEL.get(a, a), **{k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in c.items()}) for a, c in ARMS.items()],
                real_jev_available=_real_jev_ok(), jev_via_relay=bool(os.environ.get("JEV_RELAY_SOCKET")), public=PUBLIC,
                banner="SIMULATION ONLY — virtual PLC and virtual MQTT; no physical equipment or AMR is controlled")


# ------------------------------------------------------------------------------------------------ factory setup
@router.get("/factory")
def get_factory(factory_id: str = "default"):
    p = FACTORY_DIR / f"{factory_id}.json"
    return json.loads(p.read_text()) if p.exists() else default_factory().model_dump()


@router.put("/factory")
def put_factory(cfg: dict, factory_id: str = "default"):
    _public_block("공장 설정 저장")
    try:
        f = FactoryV3.model_validate(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, str(exc)) from exc
    FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    (FACTORY_DIR / f"{factory_id}.json").write_text(f.model_dump_json(indent=2), encoding="utf-8")
    return dict(saved=factory_id, main_capacity=round(f.capacity("main")), sub_capacity=round(f.capacity("sub")))


# ------------------------------------------------------------------------------------------------ live sessions
class StartV3(BaseModel):
    scenario: str = "RND"
    load: str = "medium"
    seed: int = 0
    arm: str = "R4"
    provider: str = "mock"
    speed: float = 240.0
    factory_id: str | None = None
    autostart: bool = True
    camera_mode: str = "live"
    unit_judge: str = "off"                 # off | mock | typesafe  (per-product disposition by Jev)
    defect_image_ratio: float | None = None  # e.g. 0.3 = 30 % of parts use a defective image


@router.post("/sessions")
def start(req: StartV3):
    if req.scenario not in SCENARIOS or req.arm not in ARMS or req.load not in LOADS:
        raise HTTPException(422, "unknown scenario / arm / load")
    if req.provider == "typesafe" or req.unit_judge == "typesafe":
        from sfaic2.v3.jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise HTTPException(409, why)
    fac = FactoryV3.model_validate(get_factory(req.factory_id)) if req.factory_id else None
    real = req.provider == "typesafe" or req.unit_judge == "typesafe"
    if PUBLIC and real:                         # one real-Jev session at a time on the public demo (calls are paid by the researcher)
        import time as _t
        for k, x in list(SESSIONS.items()):
            if x.args["provider"] == "typesafe" or x.args["unit_judge"] == "typesafe":
                if x.state == "RUNNING" and _t.time() - x._wall0 < 600:
                    raise HTTPException(409, "다른 방문자가 실제 Jev로 실행 중입니다(동시에 1개). 몇 분 뒤 다시 시도하거나 모의 Jev로 실행하세요.")
                SESSIONS.pop(k).close()
    if PUBLIC:                                  # protect the shared server: replayed YOLO output, few sessions, visitor runs not stored
        while len(SESSIONS) >= MAX_PUBLIC_SESSIONS:
            SESSIONS.pop(next(iter(SESSIONS))).close()
    s = LiveSession(req.scenario, req.load, req.seed, req.arm, req.provider, min(req.speed, 3000.0), fac,
                    camera_mode="replay" if PUBLIC else req.camera_mode, unit_judge=req.unit_judge, defect_image_ratio=req.defect_image_ratio,
                    autosave=not PUBLIC)
    SESSIONS[s.id] = s
    if req.autostart:
        s.control("start")
    return s.status()


def _s(sid: str) -> LiveSession:
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session")
    return SESSIONS[sid]


class Ctl(BaseModel):
    action: str
    value: float | None = None


@router.post("/sessions/{sid}/control")
def control(sid: str, c: Ctl):
    return _s(sid).control(c.action, c.value)


@router.get("/sessions")
def sessions():
    return [s.status() for s in SESSIONS.values()]


@router.get("/sessions/{sid}/overview")
def overview(sid: str):
    return _s(sid).overview()


@router.get("/sessions/{sid}/decisions")
def decisions(sid: str, limit: int = 200):
    return _s(sid).decisions(limit)


@router.get("/sessions/{sid}/incidents")
def incidents(sid: str):
    return _s(sid).incidents()


@router.get("/sessions/{sid}/incidents/{iid}")
def incident(sid: str, iid: str):
    d = _s(sid).incident(iid)
    if not d:
        raise HTTPException(404, iid)
    return d


@router.get("/sessions/{sid}/transport")
def transport(sid: str):
    return _s(sid).transport()


@router.get("/sessions/{sid}/truth")
def truth(sid: str, researcher: bool = False):
    if not researcher:
        raise HTTPException(403, "ground truth is evaluator-only; add ?researcher=true (research mode)")
    return _s(sid).truth_view()


# ------------------------------------------------------------------------------------------------ experiments
@router.get("/experiments")
def experiments():
    out = []
    for group in ("real_jev", "mock"):
        for d in sorted((EXP_ROOT / group).glob("*")) if (EXP_ROOT / group).exists() else []:
            if (d / "manifest.json").exists():
                summ = json.loads((d / "run_summary.json").read_text()) if (d / "run_summary.json").exists() else {}
                out.append(dict(group=group, exp=d.name, manifest=json.loads((d / "manifest.json").read_text()), summary=summ, has_report=(d / "paper").exists()))
    return out


@router.get("/experiments/{group}/{exp}")
def experiment(group: str, exp: str):
    d = EXP_ROOT / group / exp
    if group not in ("mock", "real_jev") or not d.exists():
        raise HTTPException(404, "unknown experiment")
    tables = {p.stem: p.read_text(encoding="utf-8") for p in sorted((d / "paper").glob("*.csv"))} if (d / "paper").exists() else {}
    figs = sorted(p.name for p in (d / "figures").glob("*.png")) if (d / "figures").exists() else []
    return dict(group=group, exp=exp, manifest=json.loads((d / "manifest.json").read_text()), summary=json.loads((d / "run_summary.json").read_text()) if (d / "run_summary.json").exists() else {},
                tables=tables, figures=figs, banner="MOCK_JEV — software check, not evidence about TypeSafe Jev" if group == "mock" else "TypeSafe Jev System One — real calls")


@router.get("/experiments/{group}/{exp}/figures/{name}")
def figure(group: str, exp: str, name: str):
    p = EXP_ROOT / group / exp / "figures" / Path(name).name
    if group not in ("mock", "real_jev") or not p.exists():
        raise HTTPException(404, name)
    return FileResponse(p)


# ------------------------------------------------------------------------------------------------ decision log (live + archived)
def _good_at_fn(s: LiveSession):
    ser = s.series

    def g(t: float):
        if not ser or t > ser[-1]["t"]:
            return None
        best = None
        for x in ser:
            if x["t"] <= t:
                best = x["good"]
            else:
                break
        return best
    return g


@router.get("/sessions/{sid}/decision-log")
def session_decision_log(sid: str, limit: int = 500):
    from sfaic2.v3.decision_log import build
    s = _s(sid)
    with s.lock:
        return dict(session=s.status(), camera_mode=s.args.get("camera_mode"), log=build(s.E["ops"].query, limit, _good_at_fn(s)))


@router.get("/archive")
def archive():
    from sfaic2.v3.decision_log import archive_index
    return archive_index()


@router.get("/archive/{group}/{exp}/{episode}/decision-log")
def archive_log(group: str, exp: str, episode: str):
    from sfaic2.v3.decision_log import from_sqlite
    p = EXP_ROOT / group / exp / "raw" / f"{Path(episode).name}.ops.sqlite"
    if group not in ("mock", "real_jev") or not p.exists():
        raise HTTPException(404, "unknown archived episode")
    return dict(group=group, exp=exp, episode=episode, log=from_sqlite(p))


# ------------------------------------------------------------------------------------------------ verification ("is it real?")
class VerifyYolo(BaseModel):
    n: int = 6
    seed: int | None = None


@router.post("/verify/yolo")
def verify_yolo(req: VerifyYolo):
    from sfaic2.v3.yolo_live import verify
    return verify(max(1, min(req.n, 12)), req.seed)


class VerifyJev(BaseModel):
    session_id: str | None = None


@router.post("/verify/jev")
def verify_jev(req: VerifyJev):
    """Send ONE fresh (uncached) request to TypeSafe Jev System One with the current snapshot of a session and return
    the exact sanitised request, the raw response, latency, resolved model and how it was parsed. Logged in the session DB."""
    import time as _t
    from sfaic2.adapters.typesafe_jev import redact
    from sfaic2.v3.jev import TypeSafeV3Client, build_questions, build_state, parse, request_hash
    ok, why = TypeSafeV3Client.opted_in()
    if not ok:
        return dict(ok=False, reason=why)
    s = SESSIONS.get(req.session_id) if req.session_id else (list(SESSIONS.values())[-1] if SESSIONS else None)
    if s is None:
        s = LiveSession("S3", "medium", 0, "R4", "mock", camera_mode="replay")
        SESSIONS[s.id] = s
        s.control("step", 6000)
    with s.lock:
        ctrl, now = s.E["ctrl"], s.E["plant"].env.now
        ft = ctrl.b.build(now, ctrl.intent, use_reserve=s.E["disp"].use_reserve)
        allowed = ft["feasible"]
        st = build_state(ft, allowed, True, True)
        client = TypeSafeV3Client(cache={})
        body_req = {"model": client.model, "state": st, "questions": build_questions(st, allowed)}
        t0 = _t.perf_counter()
        try:
            body = client.decide(body_req)
            lat = (_t.perf_counter() - t0) * 1000
            parsed = parse(body, allowed)
            err = None
        except Exception as exc:  # noqa: BLE001
            lat, body, parsed, err = (_t.perf_counter() - t0) * 1000, None, None, f"{type(exc).__name__}: {exc}"
        n = sum(1 for _ in s.E["ops"].query("SELECT 1 FROM jev_requests WHERE decision_id LIKE 'VERIFY-%'")) + 1
        did = f"VERIFY-{n:03d}"
        s.E["ops"].insert("jev_requests", now, source="TYPESAFE_JEV", request_id=f"JREQ-{did}", decision_id=did, provider="TYPESAFE_JEV", model_version=body_req["model"],
                          question_version="qs_v3_plan_action_2026-10-17", anonymized_payload_hash=request_hash(body_req), sanitized_request_json=json.dumps(redact(body_req)), created_at=now, status="VERIFY")
        s.E["ops"].insert("jev_responses", now, source="TYPESAFE_JEV", request_id=f"JREQ-{did}", raw_response_sanitized=json.dumps(redact(body)) if body else None,
                          selected_action=(parsed or {}).get("action"), per_choice_probabilities=(parsed or {}).get("action_probs"), cause_class=(parsed or {}).get("cause"),
                          needs_operator_p=(parsed or {}).get("needs_operator_p"), latency_ms=round(lat, 1), error_code=err, provider="TYPESAFE_JEV", cached=0,
                          token_usage=(body or {}).get("usage"))
        return dict(ok=err is None, session=s.status(), sim_time_s=now, request=redact(body_req), response=redact(body) if body else None, parsed=parsed, latency_ms=round(lat, 1), error=err,
                    endpoint=("server → relay on the researcher's Mac → " if os.environ.get("JEV_RELAY_SOCKET") else "") + "POST https://api.typesafe.ai/v1/systemone",
                    note=("fresh call (cache bypassed); the server holds no key — the relay on the researcher's Mac adds it" if os.environ.get("JEV_RELAY_SOCKET")
                          else "fresh call (cache bypassed); API key read from .env at call time and never returned"))


@router.get("/verify/status")
def verify_status():
    import hashlib as _h
    from sfaic2.v3.jev import TypeSafeV3Client
    from sfaic2.v3.yolo_live import IMAGES, REPLAY, WEIGHTS, LiveYolo
    ok_y, why_y = LiveYolo.available()
    ok_j, why_j = TypeSafeV3Client.opted_in()
    pilot = EXP_ROOT / "real_jev" / "pilot" / "run_summary.json"
    return dict(yolo=dict(available=ok_y, reason=why_y, weights=str(WEIGHTS), weights_sha256=_h.sha256(WEIGHTS.read_bytes()).hexdigest() if WEIGHTS.exists() else None,
                          weights_mb=round(WEIGHTS.stat().st_size / 1e6, 1) if WEIGHTS.exists() else None, held_out_images=len(list(IMAGES.glob("*.png"))) if IMAGES.exists() else 0,
                          replay_records=sum(1 for _ in REPLAY.open()) if REPLAY.exists() else 0,
                          measured=json.loads(Path("results/vision/summary.json").read_text()).get("image_level") if Path("results/vision/summary.json").exists() else None),
                jev=dict(opted_in=ok_j, reason=why_j, model_alias=os.environ.get("JEV_MODEL", "jev-latest"), endpoint=("relay → researcher Mac → " if os.environ.get("JEV_RELAY_SOCKET") else "") + "https://api.typesafe.ai/v1/systemone",
                         pilot=json.loads(pilot.read_text()) if pilot.exists() else None),
                simulation_only=True, sessions=[x.status() for x in SESSIONS.values()])


@router.get("/sessions/{sid}/units")
def session_units(sid: str, limit: int = 60, researcher: bool = False):
    return _s(sid).units(limit, researcher)


_IMG_INDEX: dict | None = None
_THUMBS: dict = {}


@router.get("/images/{image_id}")
def image(image_id: str, w: int | None = None):
    """Serve a camera image by its opaque id (first 16 hex of the frame sha256); the file name is never exposed.
    `w` returns a cached JPEG thumbnail of that width (the UI shows 140–280 px; originals are 0.4–1.3 MB PNGs)."""
    global _IMG_INDEX
    from fastapi.responses import Response
    from sfaic2.v3.yolo_live import IMAGES, MANIFEST
    if _IMG_INDEX is None:
        _IMG_INDEX = {m["sha256"][:16]: m["file"] for m in json.loads(MANIFEST.read_text())["items"] if m["split"] == "val"} if MANIFEST.exists() else {}
    f = _IMG_INDEX.get(Path(image_id).name)
    if not f or not (IMAGES / f).exists():
        raise HTTPException(404, "unknown image")
    cache = {"Cache-Control": "public, max-age=86400, immutable"}
    if not w:
        return FileResponse(IMAGES / f, media_type="image/png", headers=cache)
    w = max(64, min(int(w), 1024))
    key = (f, w)
    if key not in _THUMBS:
        import cv2
        img = cv2.imread(str(IMAGES / f))
        h0, w0 = img.shape[:2]
        img = cv2.resize(img, (w, max(1, round(h0 * w / w0))), interpolation=cv2.INTER_AREA)
        _THUMBS[key] = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        if len(_THUMBS) > 600:
            _THUMBS.pop(next(iter(_THUMBS)))
    return Response(_THUMBS[key], media_type="image/jpeg", headers=cache)


@router.get("/sessions/{sid}/floor")
def session_floor(sid: str):
    return _s(sid).floor()


@router.get("/unit-study")
def unit_study():
    out = {}
    for group in ("real_jev", "mock"):
        p = EXP_ROOT / group / "unit_study" / "summary.json"
        if p.exists():
            out[group] = json.loads(p.read_text())
    return out


@router.get("/unit-study/{group}/figure")
def unit_study_figure(group: str):
    p = EXP_ROOT / group / "unit_study" / "unit_study.png"
    if group not in ("mock", "real_jev") or not p.exists():
        raise HTTPException(404, "no figure")
    return FileResponse(p)


@router.get("/unit-study/{group}/parts")
def unit_study_parts(group: str, limit: int = 60, only_disagree: bool = True):
    """Sample of per-part records (truth included: evaluation file)."""
    import csv as _csv
    p = EXP_ROOT / group / "unit_study" / "parts.csv"
    if group not in ("mock", "real_jev") or not p.exists():
        raise HTTPException(404, "no parts log")
    rows = list(_csv.DictReader(p.open(encoding="utf-8")))
    if only_disagree:
        rows = [r for r in rows if len({r["YOLO_RULE_disp"], r["COST_RULE_disp"], r["JEV_disp"]}) > 1]
    return rows[:limit]


# ------------------------------------------------------------------------------------------------ cumulative results (DB + CSV)
from sfaic2.v3 import live_results  # noqa: E402


@router.get("/results")
def results_summary(limit: int = 50):
    runs = live_results.load_runs()
    return dict(n_runs=len(runs), runs=list(reversed(runs))[:limit], stats=live_results.stats(runs), batch=live_results.batch_status(),
                files=dict(db=str(live_results.DB), csv=[str(live_results.ROOT / n) for n in ("runs.csv", "run_events.csv", "stats_by_group.csv", "paired.csv")]))


@router.get("/results/csv/{name}")
def results_csv(name: str):
    if name not in ("runs.csv", "run_events.csv", "stats_by_group.csv", "paired.csv"):
        raise HTTPException(404, "unknown file")
    p = live_results.ROOT / name
    if not p.exists():
        raise HTTPException(404, "no runs yet")
    return FileResponse(p, media_type="text/csv", filename=name)


# ------------------------------------------------------------------------------------------------ research record (data/results, read-only)
from sfaic2.v3 import evidence  # noqa: E402


@router.get("/evidence")
def evidence_summary():
    return evidence.summary()


@router.get("/evidence/runs")
def evidence_runs(experiment: str = "confirmatory", arm: str = "", load: str = "", seed: str = "", value: str = "", met: str = "", q: str = "",
                  offset: int = 0, limit: int = 50):
    if experiment not in ("confirmatory", "sensitivity"):
        raise HTTPException(404, "unknown experiment")
    return evidence.runs(experiment, arm, load, seed, value, met, q, max(0, offset), limit)


@router.get("/evidence/runs/{run_uid}/events")
def evidence_events(run_uid: str):
    return evidence.events(run_uid)


@router.get("/evidence/file/{rel:path}")
def evidence_file(rel: str):
    p = evidence.file_path(rel)
    if p is None:
        raise HTTPException(404, "unknown file")
    media = {".csv": "text/csv", ".json": "application/json", ".jsonl": "application/x-ndjson", ".md": "text/markdown"}.get(p.suffix, "application/octet-stream")
    return FileResponse(p, media_type=media, filename=p.name)


class Batch(BaseModel):
    n: int = 10
    arms: list[str] = ["R0", "R1", "R2", "R3", "R4", "R5"]
    loads: list[str] = ["medium", "high"]
    provider: str = "mock"                  # mock (parallel, free) | typesafe (real Jev, shared budget)
    unit_judge: str = "mock"
    defect_image_ratio: float | None = 0.1
    budget_usd: float = 1.0


@router.post("/results/batch")
def results_batch(b: Batch):
    _public_block("일괄 실행")
    if not 1 <= b.n <= 500 or any(a not in ARMS for a in b.arms) or any(l not in LOADS for l in b.loads) or b.unit_judge not in ("off", "mock") \
            or b.provider not in ("mock", "typesafe") or not 0 < b.budget_usd <= 5:
        raise HTTPException(422, "batch: n 1-500, known modes/loads, part judge mock|off, provider mock|typesafe, budget ≤ $5")
    if b.provider == "typesafe":
        from sfaic2.v3.jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise HTTPException(409, why)
    try:
        return live_results.run_batch(b.n, b.arms, b.loads, b.provider, b.unit_judge, b.defect_image_ratio, budget_usd=b.budget_usd)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
