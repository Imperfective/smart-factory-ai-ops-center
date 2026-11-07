"""Research record, served read-only from data/results/ (the stored results published with the code).

Everything here is computed from those files at request time, so the page shows exactly what the files contain:
per-experiment provenance (dates, code versions, model versions, calls, cost), a searchable run log, per-run events,
and checksummed file downloads. The experiments were run as batches on the researcher's machine, not on a demo server.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path("data/results")
RUN_COLS = ["run_uid", "finished_at", "batch_id", "course_seed", "load", "arm", "decision_provider", "jev_model", "n_events", "event_kinds", "target",
            "good_end", "target_met", "forbidden_allowed", "kwh_per_good", "cost_balanced", "jev_calls", "jev_cost_usd", "data_quality", "code_version"]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=None)
def _csv(rel: str) -> tuple:
    p = ROOT / rel
    if not p.exists():
        return ()
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return tuple(csv.DictReader(fh))


@lru_cache(maxsize=None)
def _json(rel: str) -> dict:
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@lru_cache(maxsize=1)
def confirmatory() -> tuple:
    """The runs behind the confirmatory experiment: real-Jev R2–R4 and the rule/planner modes on the same scenarios (latest run per key)."""
    rows = _csv("confirmatory/runs.csv")
    real = {r["course_seed"] for r in rows if r["decision_provider"] == "TYPESAFE_JEV" and r["source"] == "batch" and r["data_quality"] == "ok"}
    last: dict = {}
    for r in sorted(rows, key=lambda r: r["finished_at"]):
        if r["source"] == "batch" and r["decision_provider"] in ("TYPESAFE_JEV", "NONE") and r["unit_judge"] == "mock" and r["course_seed"] in real and r["data_quality"] == "ok":
            last[(r["course_seed"], r["load"], r["arm"], r["decision_provider"])] = r
    return tuple(last.values())


@lru_cache(maxsize=1)
def sensitivity() -> tuple:
    return tuple(r for r in _csv("supplement/sensitivity_runs.csv"))


def _span(rows) -> list:
    ts = sorted(r["finished_at"] for r in rows if r.get("finished_at"))
    return [ts[0], ts[-1]] if ts else [None, None]


def _real(rows) -> list:
    return [r for r in rows if r.get("decision_provider") == "TYPESAFE_JEV"]


def _models(rows) -> list:
    return sorted({m for r in rows for m in (r.get("jev_model") or "").split(",") if m and m != "mock"})


@lru_cache(maxsize=1)                   # the published files only change with a deploy
def files() -> tuple:
    """Every published file with size, sha256 (first 16 hex, as in data/results/README.md) and its description."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
    desc = {m.group(1): m.group(2) for m in re.finditer(r"^\| `([^`]+)` \| (.*?) \| `[0-9a-f]+` \|$", readme, flags=re.M)}
    out = []
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            rel = p.relative_to(ROOT).as_posix()
            out.append(dict(path=rel, bytes=p.stat().st_size, sha256_16=hashlib.sha256(p.read_bytes()).hexdigest()[:16], description=desc.get(rel, "")))
    return tuple(out)


def summary() -> dict:
    conf, sens = confirmatory(), sensitivity()
    c_real, s_real = _real(conf), _real(sens)
    s_ok = [r for r in sens if r["data_quality"] == "ok"]
    rt = (_json("supplement/summary.json") or {}).get("retest") or {}
    unit = _json("two_stage_inspection/summary.json")
    pilot_m, pilot_s = _json("pilot/manifest.json"), _json("pilot/run_summary.json")
    pilot = _csv("pilot/episodes.csv")
    exps = [
        dict(key="confirmatory", runs=len(conf), pairs=len({(r["course_seed"], r["load"]) for r in conf}), scenarios=len({r["course_seed"] for r in conf}),
             modes=sorted({r["arm"] for r in conf}), real_jev_runs=len(c_real), jev_calls=int(sum(_f(r["jev_calls"]) or 0 for r in c_real)),
             usd=round(sum(_f(r["jev_cost_usd"]) or 0 for r in c_real), 3), models=_models(c_real), span=_span(conf),
             code_versions=sorted({r["code_version"] for r in conf}), batches=sorted({r["batch_id"] for r in c_real}), excluded_flagged=0,
             files=["confirmatory/runs.csv", "confirmatory/run_events.csv", "confirmatory/paired.csv", "confirmatory/stats_by_group.csv"]),
        dict(key="sensitivity", runs=len(s_ok), runs_recorded=len(sens), excluded_flagged=len(sens) - len(s_ok), values=sorted({r["value"] for r in sens}),
             pairs_per_value=len({(r["course_seed"], r["load"]) for r in sens}), real_jev_runs=len([r for r in s_real if r["data_quality"] == "ok"]),
             jev_calls=int(sum(_f(r["jev_calls"]) or 0 for r in s_real if r["data_quality"] == "ok")),
             usd=round(sum(_f(r["jev_cost_usd"]) or 0 for r in s_real), 3), models=_models(s_real),      # cost includes the excluded, re-run shifts
             span=_span(sens), code_versions=sorted({r["code_version"] for r in sens}), files=["supplement/sensitivity_runs.csv", "supplement/sensitivity_contrasts.csv", "supplement/sensitivity_means.csv"]),
        dict(key="retest", requests=rt.get("requests"), calls=rt.get("calls"), errors=rt.get("errors"), models=rt.get("models") or [],
             same_action=rt.get("action_agreement_with_original"), between_repeats=rt.get("action_agreement_between_repeats"), files=["supplement/retest.csv", "supplement/summary.json"]),
        dict(key="two_stage", parts=unit.get("parts"), jev_calls=unit.get("jev_calls"), reused=unit.get("jev_cached"), errors=unit.get("jev_errors"),
             models=sorted({r["jev_model"] for r in _csv("two_stage_inspection/parts.csv") if r.get("jev_model")}), files=["two_stage_inspection/parts.csv", "two_stage_inspection/summary.json"]),
        dict(key="pilot", runs=len(pilot), jev_calls=pilot_s.get("jev_calls"), usd=pilot_s.get("jev_cost_usd"), fallbacks=pilot_s.get("fallbacks"),
             created=pilot_m.get("created"), code_versions=[(pilot_m.get("git_commit") or "")[:7]], files=["pilot/episodes.csv", "pilot/run_summary.json", "pilot/manifest.json"]),
    ]
    return dict(source=str(ROOT), experiments=exps, files=list(files()),
                provenance="Experiments were run as batches on the researcher's machine with the same code that runs this site; runs started on the public demo are not stored.")


def runs(experiment: str = "confirmatory", arm: str = "", load: str = "", seed: str = "", value: str = "", met: str = "", q: str = "",
         offset: int = 0, limit: int = 50) -> dict:
    rows = list(confirmatory() if experiment == "confirmatory" else sensitivity() if experiment == "sensitivity" else ())
    if arm:
        rows = [r for r in rows if r["arm"] == arm]
    if load:
        rows = [r for r in rows if r["load"] == load]
    if seed:
        rows = [r for r in rows if r["course_seed"].split(".")[0] == seed.strip()]
    if value and experiment == "sensitivity":
        rows = [r for r in rows if r["value"] == value]
    if met in ("0", "1"):
        rows = [r for r in rows if (_f(r["target_met"]) or 0) == float(met)]
    if q:
        rows = [r for r in rows if q.lower() in (r["run_uid"] + " " + (r.get("event_kinds") or "")).lower()]
    rows.sort(key=lambda r: (r["finished_at"], r["run_uid"]))
    cols = RUN_COLS + (["param", "value"] if experiment == "sensitivity" else [])
    return dict(total=len(rows), offset=offset, rows=[{c: r.get(c) for c in cols} for r in rows[offset:offset + max(1, min(limit, 200))]])


def events(run_uid: str) -> list[dict]:
    keep = ["kind", "at_s", "duration_s", "cause", "detected", "detect_delay_s", "first_action", "first_action_by", "first_action_delay_s"]
    return [{k: e.get(k) for k in keep} for e in _csv("confirmatory/run_events.csv") if e["run_uid"] == run_uid]


def file_path(rel: str) -> Path | None:
    """A published file, or None (no paths outside data/results)."""
    p = (ROOT / rel).resolve()
    return p if p.is_file() and ROOT.resolve() in p.parents else None
