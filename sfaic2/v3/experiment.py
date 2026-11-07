"""R0–R5 (+ ablations) paired experiment runner for SF AI OS v3.

Every (scenario, load, seed) cell is replayed for every arm with identical fault sequences and per-unit random streams
(common random numbers). Results go to results/v3/<group>/<exp>/ where group is "mock" (MockJev software checks) or
"real_jev" (TypeSafe Jev System One) — the two are never mixed in one directory, table or figure.

    python -m sfaic2.v3.experiment --exp main --provider mock --seeds 0-29 --workers 8
    python -m sfaic2.v3.experiment --exp pilot --provider typesafe --scenarios S2,S3,S5,S9,S15 --loads medium --seeds 0-2 --arms R1,R2,R3,R4 --threads 6
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import LOADS, SCENARIOS
from .control import ARMS
from .episode import run_episode

ROOT = Path("results/v3")


def _rng(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _job(args):
    sid, load, seed, arm, provider, raw_dir, force, sens, tag = args
    try:
        r = run_episode(sid, load, seed, arm, provider=provider, raw_dir=raw_dir, force_first=force, sensitivity=sens, run_id=tag)
        r.pop("_ops", None), r.pop("_truth", None)
        if force:
            r["forced_first"] = force
        if sens:
            r["sensitivity"] = json.dumps(sens)
        return r
    except Exception as exc:  # noqa: BLE001 — failures are recorded, never hidden
        return dict(scenario=sid, load=load, seed=seed, arm=arm, error=f"{type(exc).__name__}: {exc}")


def manifest(exp: str, provider: str, a) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:  # noqa: BLE001
        commit, dirty = None, None
    import importlib.metadata as md
    pk = {}
    for p in ("simpy", "numpy", "pandas", "ortools", "httpx", "pydantic", "matplotlib", "scipy", "ultralytics"):
        try:
            pk[p] = md.version(p)
        except Exception:  # noqa: BLE001
            pk[p] = None
    return dict(exp=exp, provider_group="mock" if provider == "mock" else "real_jev", provider=provider, created=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                git_commit=commit, git_dirty=dirty, python=sys.version.split()[0], platform=platform.platform(), packages=pk, args=vars(a),
                loads=LOADS, jev_model=os.environ.get("JEV_MODEL", "jev-latest") if provider != "mock" else "mock-jev-v3",
                note="MOCK_JEV is a deterministic software stand-in; its numbers are excluded from any claim about TypeSafe Jev." if provider == "mock"
                else "Real TypeSafe Jev System One calls (explicit opt-in JEV_ENABLED=true); sanitised requests/responses stored per decision.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--provider", choices=["mock", "typesafe"], default="mock")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--loads", default="low,medium,high")
    ap.add_argument("--seeds", default="0-4")
    ap.add_argument("--arms", default="R0,R1,R2,R3,R4,R5")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--threads", type=int, default=0, help="thread pool instead of processes (real Jev: shared budget/cache)")
    ap.add_argument("--raw-seeds", default="", help="persist full ops/truth SQLite for these seeds")
    ap.add_argument("--oracle-seeds", default="", help="replay-oracle runs (R5 with the first incident decision forced) for regret")
    ap.add_argument("--sensitivity", default="", help="JSON list of {param: value} dicts applied to R1..R4 (e.g. ct_scale, recurrence_p_unstable)")
    a = ap.parse_args()
    if a.provider == "typesafe":
        from dotenv import load_dotenv
        load_dotenv(".env")
        from .jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise SystemExit(f"real Jev not available: {why}")
    group = "mock" if a.provider == "mock" else "real_jev"
    out = ROOT / group / a.exp
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest(a.exp, a.provider, a), indent=2), encoding="utf-8")
    scen, loads, seeds, arms = a.scenarios.split(","), a.loads.split(","), _rng(a.seeds), a.arms.split(",")
    raw_seeds = set(_rng(a.raw_seeds)) if a.raw_seeds else set()
    jobs = [(s, l, sd, arm, a.provider if ARMS[arm]["chooser"] == "jev" else "mock", str(out / "raw") if sd in raw_seeds else None, None, None, a.exp)
            for s in scen for l in loads for sd in seeds for arm in arms]
    if a.oracle_seeds:
        for s in scen:
            for l in loads:
                for sd in _rng(a.oracle_seeds):
                    for act in ("CONTINUE_MAIN", "RECOVER_MAIN", "SWITCH_TO_SUB", "RUN_PARALLEL", "ESCALATE_HUMAN", "SAFE_STOP_REQUEST", "REQUEST_TRANSPORT"):
                        jobs.append((s, l, sd, "R5", "mock", None, act, None, a.exp))
    if a.sensitivity:
        for sens in json.loads(a.sensitivity):
            for s in scen:
                for l in loads:
                    for sd in seeds:
                        for arm in ("R1", "R2", "R3", "R4"):
                            jobs.append((s, l, sd, arm, a.provider if ARMS[arm]["chooser"] == "jev" else "mock", None, None, sens, a.exp))
    t0 = time.time()
    rows: list[dict] = []
    pool = ThreadPoolExecutor(a.threads) if a.threads else ProcessPoolExecutor(a.workers)
    with pool as ex:
        futs = [ex.submit(_job, j) for j in jobs]
        for i, fu in enumerate(as_completed(futs), 1):
            rows.append(fu.result())
            if i % 200 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} episodes, {time.time() - t0:.0f}s", flush=True)
    rows.sort(key=lambda r: (r.get("scenario"), r.get("load"), r.get("seed"), r.get("arm"), r.get("forced_first") or "", r.get("sensitivity") or ""))
    cols = sorted({k for r in rows for k in r})
    with open(out / "episodes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    errs = [r for r in rows if r.get("error")]
    # episode_outcomes table (docs 01 §4 #17) in the experiment database
    import sqlite3
    db = sqlite3.connect(out / "experiment.sqlite")
    db.execute("DROP TABLE IF EXISTS episode_outcomes")
    db.execute(f"CREATE TABLE episode_outcomes ({', '.join(c + ' ' + ('TEXT' if c in ('scenario', 'load', 'arm', 'provider', 'first_action', 'first_action_proposed', 'unsafe_reasons', 'cause_pairs', 'vision_source', 'error', 'forced_first', 'sensitivity') else 'REAL') for c in cols)})")
    db.executemany(f"INSERT INTO episode_outcomes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [[r.get(c) for c in cols] for r in rows])
    db.execute("CREATE TABLE IF NOT EXISTS run_manifest (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("INSERT OR REPLACE INTO run_manifest VALUES ('manifest', ?)", (json.dumps(manifest(a.exp, a.provider, a)),))
    db.commit(); db.close()
    summ = dict(episodes=len(rows), errors=len(errs), wall_s=round(time.time() - t0, 1), out=str(out))
    if a.provider == "typesafe":
        summ["jev_calls"] = sum(int(r.get("jev_calls") or 0) for r in rows)
        summ["jev_cost_usd"] = round(sum(float(r.get("jev_cost_usd") or 0) for r in rows), 4)
        summ["fallbacks"] = sum(int(r.get("fallbacks") or 0) for r in rows)
    (out / "run_summary.json").write_text(json.dumps(summ, indent=2), encoding="utf-8")
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
