"""Supplementary experiments and tables (stored apart from the confirmatory store results/v3/live_runs).

  sensitivity  the confirmatory random scenarios re-run with a changed plant assumption (default: repeat-fault probability after
               an unstable restart 0.0 and 0.3 instead of 0.6), all six modes, real Jev for R2–R4, same procedure as the main batch
  retest       a random sample of real-Jev requests captured during the sensitivity runs is sent again (fresh cache, twice) to
               measure whether the model returns the same choice for the identical request
  report       tables from the confirmatory store (decision-model latency/cost, cause agreement, per-disruption outcomes, transport)
               plus the two experiments -> results/v3/supplement/*.csv, summary.json and docs/v3/RESULTS_supplement.md

python -m sfaic2.v3.supplement sensitivity --values 0.0,0.3 --n 60 --budget 1.5
python -m sfaic2.v3.supplement retest --k 300 --repeats 2 --budget 0.2
python -m sfaic2.v3.supplement report
"""
from __future__ import annotations

import json
import random
import sqlite3
import statistics
import threading
import time
from pathlib import Path

from . import live_results as LR

OUT = Path("results/v3/supplement")
RUNS, CALLS, RETEST = OUT / "sensitivity_runs.jsonl", OUT / "sensitivity_calls.jsonl", OUT / "retest.jsonl"
ARMS = ["R0", "R1", "R2", "R3", "R4", "R5"]
LOADS = ["medium", "high"]
_W = threading.Lock()


def _append(path: Path, obj: dict) -> None:
    with _W:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, default=str) + "\n")


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def confirmatory_seeds() -> list[int]:
    con = sqlite3.connect(LR.DB)
    try:
        return sorted(int(r[0]) for r in con.execute("SELECT DISTINCT course_seed FROM runs WHERE decision_provider='TYPESAFE_JEV' AND source='batch' "
                                                     "AND COALESCE(data_quality,'ok')='ok'"))
    finally:
        con.close()


def confirmatory_runs(seeds: set | None = None) -> list[dict]:
    """The runs behind the paper's confirmatory experiment: real-Jev R2–R4 and their rule/planner partners on the same scenarios."""
    real = set(confirmatory_seeds())
    keep = real if seeds is None else real & set(seeds)
    rs = [r for r in LR.load_runs() if r["source"] == "batch" and r["decision_provider"] in ("TYPESAFE_JEV", "NONE") and r["unit_judge"] == "mock"
          and r["course_seed"] is not None and int(r["course_seed"]) in keep]
    last = {}
    for r in sorted(rs, key=lambda r: r["finished_at"]):
        last[(int(r["course_seed"]), r["load"], r["arm"], r["decision_provider"])] = r
    return list(last.values())


def _metrics_json(uids: list[str]) -> dict:
    con = sqlite3.connect(LR.DB)
    try:
        out = {}
        for i in range(0, len(uids), 500):
            chunk = uids[i:i + 500]
            out.update({u: json.loads(m) for u, m in con.execute(f"SELECT run_uid, metrics_json FROM runs WHERE run_uid IN ({','.join('?' * len(chunk))})", chunk)})
        return out
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------ experiment 1: sensitivity
def _job(j: dict, budget=None, cache=None) -> dict:
    from .live import LiveSession
    sens = j["overrides"] if "overrides" in j else {j["param"]: j["value"]}
    ls = LiveSession("RND", j["load"], j["seed"], j["arm"], j.get("provider", "typesafe"), camera_mode="replay", unit_judge="mock", defect_image_ratio=0.1, headless=True,
                     source="supplement", autosave=False, jev_budget=budget, jev_cache=cache, sensitivity=sens)
    ls.control("run_to_end")
    p = LR.collect(ls.result, ls.E, source="supplement", course_seed=j["seed"], unit_judge="mock", camera_mode="replay", defect_image_ratio=0.1,
                   wall_s=time.time() - ls._wall0, energy=ls._energy(), batch_id=j["batch_id"])
    calls = ls.E["ops"].query("SELECT q.sanitized_request_json AS req, r.selected_action, r.per_choice_probabilities, r.cause_class, r.needs_operator_p, r.latency_ms "
                              "FROM jev_requests q JOIN jev_responses r ON r.request_id = q.request_id WHERE r.cached = 0 AND r.error_code IS NULL")
    rng = random.Random(f"{j.get('value', json.dumps(j.get('overrides'), sort_keys=True))}-{j['seed']}-{j['load']}-{j['arm']}")
    errs = [r["error_code"] for r in ls.E["ops"].query("SELECT error_code FROM jev_responses WHERE error_code IS NOT NULL")]
    return dict(p=p, calls=rng.sample(calls, min(2, len(calls))), errors=errs)


def sensitivity(values: list[float], n: int, param: str = "recurrence_p_unstable", budget_usd: float = 1.5, workers: int = 4) -> None:
    """Paired re-run of n confirmatory scenarios × 2 loads × 6 modes per value; resumable (finished jobs are skipped)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sfaic2.adapters.typesafe_jev import JevBudget
    from .jev import TypeSafeV3Client
    ok, why = TypeSafeV3Client.opted_in()
    if not ok:
        raise SystemExit(f"real Jev unavailable: {why}")
    seeds = sorted(random.Random(20261107).sample(confirmatory_seeds(), n))
    done = {(r["value"], r["row"]["course_seed"], r["row"]["load"], r["row"]["arm"]) for r in _read(RUNS) if r["row"]["data_quality"] == "ok"}   # flagged runs stay on file, are re-run
    for v in values:
        bid = f"S{time.strftime('%Y%m%d-%H%M%S')}-{param}={v}"
        jobs = [dict(seed=s, load=l, arm=a, param=param, value=v, batch_id=bid) for s in seeds for l in LOADS for a in ARMS if (v, s, l, a) not in done]
        budget, cache, cost = JevBudget(10 ** 6, budget_usd), {}, 0.0
        print(f"{param}={v}: {len(jobs)} runs to do", flush=True)
        with ThreadPoolExecutor(workers) as ex:
            for i, fu in enumerate(as_completed([ex.submit(_job, j, budget, cache) for j in jobs])):
                out = fu.result()
                row = out["p"]["row"]
                row["run_uid"], row["finished_at"] = f"sup-{row['course_seed']}-{row['load']}-{row['arm']}-{v}-{time.strftime('%H%M%S')}", time.strftime("%Y-%m-%d %H:%M:%S")
                _append(RUNS, dict(param=param, value=v, row=row, metrics=out["p"]["metrics"], events=out["p"]["events"], jev_errors=out["errors"]))
                for c in out["calls"]:
                    _append(CALLS, dict(param=param, value=v, seed=row["course_seed"], load=row["load"], arm=row["arm"], **c))
                cost += float(row.get("jev_cost_usd") or 0)
                if i % 50 == 49:
                    print(f"  {i + 1}/{len(jobs)} ${cost:.3f}", flush=True)
        print(f"{param}={v}: done, ${cost:.3f}", flush=True)


# ------------------------------------------------------------------------------------------------ experiment 3: added comparisons
def variant(name: str, arms: list[str], n: int, overrides: dict, budget_usd: float = 1.0, workers: int = 6) -> None:
    """Paired re-run of n confirmatory scenarios × 2 loads for the given modes under plant/operator overrides, stored in
    results/v3/supplement/{name}_runs.jsonl (never pooled with the confirmatory store). Rule-only modes run in parallel
    processes without API calls; decision-model modes call the real Jev with one shared budget, as in the main batch."""
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
    from .control import ARMS as MODES
    path = OUT / f"{name}_runs.jsonl"
    seeds = sorted(random.Random(20261107).sample(confirmatory_seeds(), n))
    okey = json.dumps(overrides, sort_keys=True)
    done = {(r["overrides_key"], r["row"]["course_seed"], r["row"]["load"], r["row"]["arm"]) for r in _read(path) if r["row"]["data_quality"] == "ok"}
    jev = any(MODES[a]["chooser"] == "jev" for a in arms)
    jobs = [dict(seed=s, load=l, arm=a, overrides=overrides, provider="typesafe" if jev else "mock", batch_id=f"V{time.strftime('%Y%m%d-%H%M%S')}-{name}")
            for s in seeds for l in LOADS for a in arms if (okey, s, l, a) not in done]
    print(f"{name} {okey}: {len(jobs)} runs to do", flush=True)
    if jev:
        from sfaic2.adapters.typesafe_jev import JevBudget
        from .jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise SystemExit(f"real Jev unavailable: {why}")
        budget, cache = JevBudget(10 ** 6, budget_usd), {}
        ex, futs = ThreadPoolExecutor(4), None
        futs = [ex.submit(_job, j, budget, cache) for j in jobs]
    else:
        ex = ProcessPoolExecutor(workers)
        futs = [ex.submit(_job, j) for j in jobs]
    cost = 0.0
    try:
        for i, fu in enumerate(as_completed(futs)):
            out = fu.result()
            row = out["p"]["row"]
            row["run_uid"], row["finished_at"] = f"var-{name}-{row['course_seed']}-{row['load']}-{row['arm']}-{time.strftime('%H%M%S')}-{i}", time.strftime("%Y-%m-%d %H:%M:%S")
            _append(path, dict(name=name, overrides=overrides, overrides_key=okey, row=row, metrics=out["p"]["metrics"], events=out["p"]["events"], jev_errors=out["errors"]))
            cost += float(row.get("jev_cost_usd") or 0)
            if i % 100 == 99:
                print(f"  {i + 1}/{len(jobs)} ${cost:.3f}", flush=True)
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)       # never leave queued runs calling the API after a failure
        raise
    ex.shutdown()
    print(f"{name} {okey}: done, ${cost:.3f}", flush=True)


# ------------------------------------------------------------------------------------------------ experiment 2: test–retest
def retest(k: int = 300, repeats: int = 2, budget_usd: float = 0.2) -> None:
    from sfaic2.adapters.typesafe_jev import JevBudget
    from .jev import TypeSafeV3Client, parse, request_hash
    calls = _read(CALLS)
    seen, uniq = set(), []
    for c in calls:                                   # distinct requests only
        h = request_hash(json.loads(c["req"]))
        if h not in seen:
            seen.add(h)
            uniq.append((h, c))
    sample = random.Random(7).sample(uniq, min(k, len(uniq)))
    done = {(r["hash"], r["rep"]) for r in _read(RETEST)}
    budget = JevBudget(10 ** 6, budget_usd)
    for h, c in sample:
        req = json.loads(c["req"])
        for rep in range(1, repeats + 1):
            if (h, rep) in done:
                continue
            cl = TypeSafeV3Client(budget=budget, cache={})
            t0 = time.perf_counter()
            try:
                p = parse(cl.decide(req), req["state"]["allowed_actions"])
                rec = dict(action=p["action"], probs=p["action_probs"], cause=p["cause"], needs_operator_p=p["needs_operator_p"], model=p["resolved_model"], error=None)
            except Exception as exc:  # noqa: BLE001 — recorded, counted as disagreement-unknown
                rec = dict(error=f"{type(exc).__name__}: {str(exc)[:120]}")
            _append(RETEST, dict(hash=h, rep=rep, arm=c["arm"], value=c["value"], n_allowed=len(req["state"]["allowed_actions"]), latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                                 orig=dict(action=c["selected_action"], probs=c["per_choice_probabilities"], cause=c["cause_class"], needs_operator_p=c["needs_operator_p"]), **rec))
    print(f"retest: {len(sample)} requests × {repeats}", flush=True)


# ------------------------------------------------------------------------------------------------ analysis
def _q(xs: list[float], p: float):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1)))] if xs else None


def _boot_diff(pairs: list[tuple[int, float]], n: int = 2000, seed: int = 2026) -> tuple:
    """Mean paired difference with a scenario-clustered bootstrap CI (pairs: (scenario seed, difference))."""
    if len(pairs) < 2:
        return None, None, None
    by: dict[int, list[float]] = {}
    for s, d in pairs:
        by.setdefault(s, []).append(d)
    keys, rng, means = list(by), random.Random(seed), []
    for _ in range(n):
        xs = [d for s in (rng.choice(keys) for _ in keys) for d in by[s]]
        means.append(sum(xs) / len(xs))
    means.sort()
    return sum(d for _, d in pairs) / len(pairs), means[int(0.025 * n)], means[int(0.975 * n) - 1]


def _derive(r: dict) -> dict:
    g, t = r.get("good_end"), r.get("target")
    r["defects_per_1000_good"] = (1000.0 * r["defects_shipped"] / g) if g and r.get("defects_shipped") is not None else None
    r["good_to_target"] = (g / t) if g is not None and t else None
    return r


def decision_model_use(runs: list[dict], mj: dict) -> list[dict]:
    out = []
    for arm in ("R2", "R3", "R4"):
        rs = [r for r in runs if r["arm"] == arm and r["decision_provider"] == "TYPESAFE_JEV"]
        m = [mj[r["run_uid"]] for r in rs]
        calls, cached = sum(x["jev_calls"] for x in m), sum(x["jev_cached"] for x in m)
        out.append(dict(arm=arm, shifts=len(rs), decisions_per_shift=statistics.mean(x["decisions"] for x in m), calls_per_shift=calls / len(m),
                        reused_share=cached / (calls + cached), latency_p50_ms_median=statistics.median(x["jev_latency_p50_ms"] for x in m if x["jev_latency_p50_ms"]),
                        latency_p95_ms_median=statistics.median(x["jev_latency_p95_ms"] for x in m if x["jev_latency_p95_ms"]),
                        latency_p95_ms_max=max(x["jev_latency_p95_ms"] for x in m if x["jev_latency_p95_ms"]),
                        input_tokens_per_call=sum(x["jev_input_tokens"] for x in m) / calls, usd_per_shift=sum(x["jev_cost_usd"] for x in m) / len(m),
                        errors=sum(x["jev_errors"] for x in m), fallbacks=sum(x["fallbacks"] for x in m)))
    return out


def cause_agreement(runs: list[dict], mj: dict) -> tuple[list[dict], list[dict]]:
    """First non-operator decision after each random event: does its cause class match the event's true class?"""
    by_arm, by_cause = [], {}
    for arm in ARMS:
        pairs = [tuple(x) for r in runs if r["arm"] == arm for x in json.loads(mj[r["run_uid"]]["cause_pairs"])]
        by_arm.append(dict(arm=arm, events=len(pairs), agreement=sum(t == p for t, p in pairs) / len(pairs), no_decision=sum(p == "NO_DECISION" for _, p in pairs) / len(pairs)))
        for t, p in pairs:
            by_cause.setdefault((t, arm), []).append(t == p)
    return by_arm, [dict(true_cause=t, arm=a, events=len(v), agreement=sum(v) / len(v)) for (t, a), v in sorted(by_cause.items())]


def transport(runs: list[dict], mj: dict) -> list[dict]:
    keys = ["orders", "duplicate_executions", "duplicates_suppressed", "wrong_pallet", "premature_dispatch", "battery_violations", "timed_out_orders", "transport_errors", "order_completion_rate"]
    out = []
    for arm in ARMS:
        m = [mj[r["run_uid"]] for r in runs if r["arm"] == arm]
        out.append(dict(arm=arm, shifts=len(m), **{k: statistics.mean(float(x[k]) for x in m if x.get(k) is not None) for k in keys}))
    return out


def by_disruption(runs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Per event type: detection and the first automatic decision by mode (stored run_events), and the H1 plan-completion gain on scenarios containing it."""
    uids = {r["run_uid"]: r for r in runs}
    con = sqlite3.connect(LR.DB)
    con.row_factory = sqlite3.Row
    try:
        ev = [dict(e) for e in con.execute("SELECT run_uid, kind, detected, detect_delay_s, first_action_delay_s FROM run_events WHERE arm IS NOT NULL")
              if e["run_uid"] in uids]
    finally:
        con.close()
    det = []
    for kind in sorted({e["kind"] for e in ev}):
        for arm in ARMS:
            xs = [e for e in ev if e["kind"] == kind and uids[e["run_uid"]]["arm"] == arm]
            if xs:
                det.append(dict(kind=kind, arm=arm, events=len(xs), detected=statistics.mean(e["detected"] or 0 for e in xs),
                                detect_delay_s_median=_q([e["detect_delay_s"] for e in xs], 0.5), first_action_delay_s_median=_q([e["first_action_delay_s"] for e in xs], 0.5)))
    tm = {(int(r["course_seed"]), r["load"], r["arm"]): r for r in runs}
    gain = []
    groups = {f"contains {k}": (lambda r, k=k: k in (r["event_kinds"] or "").split("+")) for k in sorted({e["kind"] for e in ev})}
    groups.update({f"{n} event{'s' if n > 1 else ''}": (lambda r, n=n: int(r["n_events"]) == n) for n in (1, 2, 3, 4)})
    for label, pred in groups.items():
        for a, b in (("R4", "R1"), ("R3", "R2"), ("R4", "R3")):
            pr = [(s, tm[(s, l, a)]["target_met"] - tm[(s, l, b)]["target_met"]) for (s, l, arm) in tm if arm == a and (s, l, b) in tm and pred(tm[(s, l, a)])]
            mean, lo, hi = _boot_diff(pr)
            gain.append(dict(group=label, contrast=f"{a}-{b}", pairs=len(pr), plan_completion_diff=mean, ci_low=lo, ci_high=hi))
    return det, gain


SENS_METRICS = {"target_met": "plan completion", "forbidden_allowed": "unsafe commands authorized / shift", "kwh_per_good": "kWh per good part",
                "cost_balanced": "operating loss", "production_recovery_s": "production recovery (s)"}
SENS_CONTRASTS = [("R4", "R1"), ("R2", "R1"), ("R3", "R2"), ("R4", "R3"), ("R5", "R4")]


def _contrasts(rows: list[dict]) -> list[dict]:
    keep = [p for p in LR.paired_contrasts(rows) if p["decision_provider"] == "TYPESAFE_JEV" and p["metric"] in SENS_METRICS and (p["a"], p["b"]) in SENS_CONTRASTS]
    return [dict(a=p["a"], b=p["b"], metric=p["metric"], n_pairs=p["n_pairs"], mean_a=p.get("mean_a"), mean_b=p.get("mean_b"), diff=p.get("mean_diff"),
                 ci_low=p.get("ci_low"), ci_high=p.get("ci_high"), p_holm=p.get("p_holm")) for p in keep]


def sensitivity_tables() -> tuple[list[dict], list[dict], dict]:
    allrs = _read(RUNS)
    if not allrs:
        return [], [], {}
    last = {}                                         # flagged runs (any API error / fallback) are excluded, the re-run counts
    for r in allrs:
        if r["row"]["data_quality"] == "ok":
            last[(r["value"], r["row"]["course_seed"], r["row"]["load"], r["row"]["arm"])] = r
    rs = list(last.values())
    seeds = {int(r["row"]["course_seed"]) for r in rs}
    base = [_derive(dict(r)) for r in confirmatory_runs(seeds)]
    variants = {0.6: base}
    for r in rs:
        row = _derive(dict(r["row"]))
        row["decision_provider"] = row["decision_provider"] or ("TYPESAFE_JEV" if row["arm"] in ("R2", "R3", "R4") else "NONE")
        variants.setdefault(float(r["value"]), []).append(row)
    out, means = [], []
    for v in sorted(variants):
        rows = variants[v]
        for c in _contrasts(rows):
            out.append(dict(recurrence_p=v, **c))
        for arm in ARMS:
            xs = [x for x in rows if x["arm"] == arm]
            means.append(dict(recurrence_p=v, arm=arm, shifts=len(xs), plan_completion=statistics.mean(x["target_met"] for x in xs),
                              unsafe_per_shift=statistics.mean(x["forbidden_allowed"] for x in xs), loss=statistics.mean(x["cost_balanced"] for x in xs)))
    real = [r["row"] for r in rs if r["row"]["decision_provider"] == "TYPESAFE_JEV"]
    meta = dict(param=rs[0]["param"], values=sorted({float(r["value"]) for r in rs}), scenarios=len(seeds), pairs=len(seeds) * len(LOADS),
                shifts=len(rs), real_jev_shifts=len(real), jev_calls=sum(int(r.get("jev_calls") or 0) for r in real),
                usd=round(sum(float(r["row"].get("jev_cost_usd") or 0) for r in allrs), 3), flagged=sum(r["row"]["data_quality"] != "ok" for r in allrs),
                models=sorted({m for r in real for m in (r.get("jev_model") or "").split(",") if m}))
    return out, means, meta


def retest_tables() -> dict:
    rs = _read(RETEST)
    if not rs:
        return {}
    for r in rs:                                      # probabilities read back from the ops DB are JSON text
        if isinstance(r["orig"].get("probs"), str):
            r["orig"]["probs"] = json.loads(r["orig"]["probs"])
    ok = [r for r in rs if not r["error"]]
    by: dict[str, list[dict]] = {}
    for r in ok:
        by.setdefault(r["hash"], []).append(r)
    full = {h: v for h, v in by.items() if len(v) >= 2}
    same_all = [all(x["action"] == v[0]["orig"]["action"] for x in v) for v in full.values()]
    rep_vs_orig = [x["action"] == x["orig"]["action"] for x in ok]
    cause_vs_orig = [x["cause"] == x["orig"]["cause"] for x in ok]
    rep12 = [v[0]["action"] == v[1]["action"] for v in full.values()]
    dno = [abs(x["needs_operator_p"] - x["orig"]["needs_operator_p"]) for x in ok if x["orig"]["needs_operator_p"] is not None]
    dprob = [max(abs(float((x["probs"] or {}).get(a, 0)) - float((x["orig"]["probs"] or {}).get(a, 0))) for a in set(x["probs"] or {}) | set(x["orig"]["probs"] or {}))
             for x in ok if x["probs"] and x["orig"]["probs"]]

    def margin(p: dict) -> float:
        v = sorted((float(x) for x in (p or {}).values()), reverse=True)
        return (v[0] - v[1]) if len(v) > 1 else 1.0
    lo = [x["action"] == x["orig"]["action"] for x in ok if margin(x["orig"]["probs"]) < 0.2]
    hi = [x["action"] == x["orig"]["action"] for x in ok if margin(x["orig"]["probs"]) >= 0.2]
    per_arm = {a: (lambda xs: dict(n=len(xs), action_agreement=sum(xs) / len(xs) if xs else None))([x["action"] == x["orig"]["action"] for x in ok if x["arm"] == a])
               for a in ("R2", "R3", "R4")}
    return dict(requests=len(by), repeats=max(r["rep"] for r in rs), calls=len(rs), errors=len(rs) - len(ok), models=sorted({r["model"] for r in ok}),
                action_agreement_with_original=sum(rep_vs_orig) / len(rep_vs_orig), action_same_in_all_three=sum(same_all) / len(same_all) if same_all else None,
                action_agreement_between_repeats=sum(rep12) / len(rep12) if rep12 else None, cause_agreement_with_original=sum(cause_vs_orig) / len(cause_vs_orig),
                needs_operator_abs_diff_median=_q(dno, 0.5), needs_operator_abs_diff_p95=_q(dno, 0.95), max_choice_prob_abs_diff_median=_q(dprob, 0.5),
                max_choice_prob_abs_diff_p95=_q(dprob, 0.95), agreement_low_margin=dict(n=len(lo), rate=sum(lo) / len(lo) if lo else None),
                agreement_high_margin=dict(n=len(hi), rate=sum(hi) / len(hi) if hi else None), per_arm=per_arm,
                latency_ms_p50=_q([r["latency_ms"] for r in ok], 0.5), latency_ms_p95=_q([r["latency_ms"] for r in ok], 0.95))


# ------------------------------------------------------------------------------------------------ added comparisons (analysis)
ADDED = [("R3r", "R1", "validation + gate, no decision model"), ("R3", "R3r", "decision model inside the gate"), ("R2", "R1", "decision model without the gate"),
         ("R3", "R2", "gate with the decision model"), ("R1s", "R1", "stabilisation wait in the rules"), ("R4", "R1s", "full system vs rules with stabilisation wait"),
         ("R4", "R4r", "decision model vs rules in the full system"), ("R5", "R4r", "planner vs rules in the full system")]
EXTRA_METRICS = ["target_met", "forbidden_allowed", "kwh_per_good", "var_kwh_per_good", "cost_balanced", "overloads", "tripped_min", "factory_kwh_total", "operator_share"]


def _enrich(r: dict, m: dict | None = None) -> dict:
    r = dict(r)
    if m:
        for k in ("tripped_min", "operator_decisions", "decisions", "sub_utilization", "max_kw", "overloads", "unsafe_reasons", "escalations"):
            r.setdefault(k, m.get(k))
    g, t = _f(r.get("good_end")), _f(r.get("target"))
    r["var_kwh_per_good"] = ((_f(r.get("factory_kwh_total")) or 0) - (_f(r.get("aux_kwh")) or 0)) / g if g else None
    r["good_to_target"] = g / t if g is not None and t else None
    d = _f(r.get("decisions"))
    r["operator_share"] = (_f(r.get("operator_decisions")) or 0) / d if d else None
    return r


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def conf_rows(seeds: set | None = None) -> list[dict]:
    runs = confirmatory_runs(seeds)
    mj = _metrics_json([r["run_uid"] for r in runs])
    return [_enrich(r, mj[r["run_uid"]]) for r in runs]


def var_rows(name: str, okey: str = "{}", seeds: set | None = None) -> list[dict]:
    last = {}
    for r in _read(OUT / f"{name}_runs.jsonl"):
        x = r["row"]
        if r["overrides_key"] == okey and x["data_quality"] == "ok" and (seeds is None or int(x["course_seed"]) in seeds):
            x = dict(x, decision_provider=x.get("decision_provider") or "NONE")
            last[(x["course_seed"], x["load"], x["arm"])] = _enrich(x, r["metrics"])
    return list(last.values())


def contrast_family(rows: list[dict], pairs: list[tuple], metrics: list[str]) -> list[dict]:
    """Paired contrasts on identical scenario × load: cluster-bootstrap 95 % CI (2,000, unadjusted) and scenario-level
    sign-flip permutation p (10,000), Holm within each metric over the listed contrasts."""
    import pandas as pd
    from .stats import _pairs, cluster_boot, holm, signflip_p
    df = pd.DataFrame([dict(scenario="RND", load=r["load"], seed=int(float(r["course_seed"])), arm=r["arm"], **{m: _f(r.get(m)) for m in metrics}) for r in rows])
    df = df.drop_duplicates(["load", "seed", "arm"], keep="last")
    out = []
    for m in metrics:
        fam = []
        for a, b, label in pairs:
            if not ({a, b} <= set(df.arm)):
                continue
            pp = _pairs(df, a, b, m)
            if len(pp) < 2:
                continue
            pp["d"] = pp["a"] - pp["b"]
            lo, hi = cluster_boot(pp)
            fam.append(dict(a=a, b=b, label=label, metric=m, n_pairs=len(pp), mean_a=pp["a"].mean(), mean_b=pp["b"].mean(), diff=pp["d"].mean(), ci_low=lo, ci_high=hi, p=signflip_p(pp)))
        for x, h in zip(fam, holm([x["p"] for x in fam]) if fam else []):
            x["p_holm"] = h
        out += fam
    return out


def mode_means(rows: list[dict], keys: list[str]) -> list[dict]:
    out = []
    for arm in sorted({r["arm"] for r in rows}, key=lambda a: (a[:2], a)):
        for load in ("medium", "high", None):
            xs = [r for r in rows if r["arm"] == arm and (load is None or r["load"] == load)]
            if xs:
                out.append(dict(arm=arm, load=load or "both", shifts=len(xs), **{k: statistics.mean(_f(x.get(k)) or 0 for x in xs) for k in keys}))
    return out


def added_tables() -> dict:
    """2 × 2 and rule-baseline comparisons (420 pairs), the stabilisation baseline under the restart assumptions (120 pairs),
    the operator sensitivity (120 pairs), electrical outcomes and the interim / extension split of the confirmatory sample."""
    if not (OUT / "ablation_runs.jsonl").exists():
        return {}
    base = conf_rows()
    main = base + var_rows("ablation")
    res = dict(added=contrast_family(main, ADDED, EXTRA_METRICS[:-1] + ["operator_share"]),
               electrical=contrast_family(main, [("R4", "R1", "H1"), ("R4", "R1s", "full system vs rules with stabilisation wait"), ("R3", "R2", "gate with the decision model"),
                                                 ("R4", "R3", "H3")], ["kwh_per_good", "var_kwh_per_good", "factory_kwh_total", "overloads", "tripped_min", "max_kw"]),
               means=mode_means(main, ["target_met", "forbidden_allowed", "kwh_per_good", "var_kwh_per_good", "cost_balanced", "operator_share", "overloads", "tripped_min",
                                       "factory_kwh_total", "max_kw", "sub_utilization", "good_to_target"]))
    # stabilisation baseline under the restart assumptions (the 60 sensitivity scenarios)
    sens_seeds = {int(r["row"]["course_seed"]) for r in _read(RUNS)}
    sens = []
    for v, okey in ((0.0, '{"recurrence_p_unstable": 0.0}'), (0.3, '{"recurrence_p_unstable": 0.3}'), (0.6, "{}")):
        if v == 0.6:
            rows = conf_rows(sens_seeds) + var_rows("ablation", "{}", sens_seeds)
        else:
            rows = [_enrich(dict(r["row"], decision_provider=r["row"].get("decision_provider") or "NONE"), r["metrics"]) for r in _read(RUNS)
                    if float(r["value"]) == v and r["row"]["data_quality"] == "ok"] + var_rows("ablation", okey)
        for x in contrast_family(rows, [("R4", "R1s", "full system vs rules with stabilisation wait"), ("R1s", "R1", "stabilisation wait in the rules"),
                                        ("R4", "R1", "H1")], ["target_met", "forbidden_allowed", "kwh_per_good"]):
            sens.append(dict(recurrence_p=v, **x))
    res["stabilisation_sensitivity"] = sens
    # operator answers with the fixed rules instead of the planner
    if (OUT / "operator_runs.jsonl").exists():
        opr = var_rows("operator", '{"operator_policy": "rules"}')
        op_seeds = {int(float(r["course_seed"])) for r in opr}
        planner = conf_rows(op_seeds) + var_rows("ablation", "{}", op_seeds)
        pairs = [("R4", "R1", "H1"), ("R2", "R1", "H2"), ("R3", "R2", "gate with the decision model"), ("R4", "R3", "H3"), ("R3", "R3r", "decision model inside the gate"),
                 ("R4", "R4r", "decision model vs rules in the full system")]
        res["operator"] = [dict(operator="planner", **x) for x in contrast_family(planner, pairs, ["target_met", "forbidden_allowed", "cost_balanced"])] + \
                          [dict(operator="rules", **x) for x in contrast_family(opr, pairs, ["target_met", "forbidden_allowed", "cost_balanced"])]
        res["operator_means"] = [dict(operator="planner", **m) for m in mode_means(planner, ["target_met", "operator_share", "forbidden_allowed"]) if m["load"] == "both"] + \
                                [dict(operator="rules", **m) for m in mode_means(opr, ["target_met", "operator_share", "forbidden_allowed"]) if m["load"] == "both"]
        real = [r for r in opr if r["decision_provider"] == "TYPESAFE_JEV"]
        res["operator_meta"] = dict(pairs=len({(r["course_seed"], r["load"]) for r in opr}), shifts=len(opr), real_jev_shifts=len(real),
                                    jev_calls=int(sum(_f(r.get("jev_calls")) or 0 for r in real)), usd=round(sum(_f(r.get("jev_cost_usd")) or 0 for r in real), 3),
                                    models=sorted({m for r in real for m in (r.get("jev_model") or "").split(",") if m}))
    # interim (first 280 pairs) and extension (last 140 pairs) of the confirmatory sample, by real-Jev batch
    by_batch: dict[str, set] = {}
    for r in base:
        if r["decision_provider"] == "TYPESAFE_JEV":
            by_batch.setdefault(r["batch_id"], set()).add(int(float(r["course_seed"])))
    order = sorted(by_batch)
    if len(order) >= 2:
        ext = by_batch[order[-1]]
        first = set().union(*(by_batch[b] for b in order[:-1]))
        res["interim"] = [dict(part="first", **x) for x in contrast_family([r for r in base if int(float(r["course_seed"])) in first], [(a, b, l) for a, b, l in
                          [("R2", "R1", "H2"), ("R4", "R3", "H3"), ("R4", "R1", "H1"), ("R3", "R2", "R3-R2"), ("R5", "R4", "R5-R4")]], ["target_met"])] + \
                         [dict(part="extension", **x) for x in contrast_family([r for r in base if int(float(r["course_seed"])) in ext], [("R4", "R3", "H3")], ["target_met"])]
    unsafe = {}
    for r in main:
        for k, v in json.loads(r.get("unsafe_reasons") or "{}").items():
            unsafe.setdefault(r["arm"], {}).setdefault(k, 0)
            unsafe[r["arm"]][k] += v
    n_arm = {a: sum(1 for r in main if r["arm"] == a) for a in unsafe}
    res["unsafe_reasons"] = {a: {k: v / n_arm[a] for k, v in d.items()} for a, d in unsafe.items()}
    return res


def _csv(name: str, rows: list[dict]) -> None:
    import csv
    if not rows:
        return
    with open(OUT / name, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def report(md: Path = Path("docs/v3/RESULTS_supplement.md")) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    runs = confirmatory_runs()
    mj = _metrics_json([r["run_uid"] for r in runs])
    use = decision_model_use(runs, mj)
    cause_arm, cause_kind = cause_agreement(runs, mj)
    tr = transport(runs, mj)
    det, gain = by_disruption(runs)
    sens, sens_means, sens_meta = sensitivity_tables()
    rt = retest_tables()
    ad = added_tables()
    for name, rows in (("decision_model_use.csv", use), ("cause_agreement_by_mode.csv", cause_arm), ("cause_agreement_by_cause.csv", cause_kind),
                       ("transport_by_mode.csv", tr), ("detection_by_disruption.csv", det), ("plan_gain_by_disruption.csv", gain),
                       ("sensitivity_contrasts.csv", sens), ("sensitivity_means.csv", sens_means), ("added_contrasts.csv", ad.get("added", [])), ("electrical_contrasts.csv", ad.get("electrical", [])),
                       ("added_means.csv", ad.get("means", [])), ("stabilisation_sensitivity.csv", ad.get("stabilisation_sensitivity", [])),
                       ("operator_sensitivity.csv", ad.get("operator", [])), ("interim_extension.csv", ad.get("interim", []))):
        _csv(name, rows)
    summary = dict(generated=time.strftime("%Y-%m-%d %H:%M"), confirmatory_runs=len(runs), decision_model_use=use, cause_agreement=cause_arm, transport=tr,
                   sensitivity=dict(meta=sens_meta, contrasts=sens, means=sens_means), retest=rt,
                   added={k: v for k, v in ad.items()})
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")

    def f(v, d=3):                     # half-up rounding of the exact value (x.5 rounds away from zero)
        if v is None:
            return "—"
        if not isinstance(v, float):
            return str(v)
        from decimal import ROUND_HALF_UP, Decimal
        v = float(v)                   # numpy floats: repr is "np.float64(...)"
        if v != v:
            return "—"
        return str(Decimal(repr(v)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))
    L = ["# Supplementary results (generated by `python -m sfaic2.v3.supplement report` — do not edit by hand)", "",
         f"Generated {summary['generated']}. Confirmatory runs read: {len(runs)} (real Jev R2–R4 + rule/planner partners on the same scenarios). "
         "CSV files: `results/v3/supplement/`.", "",
         "## S1 Decision-model use in the confirmatory experiment", "",
         "| mode | shifts | decisions / shift | real calls / shift | reused share | latency p50 (median over shifts, ms) | latency p95 (median / max, ms) | input tokens / call | USD / shift | errors | fallbacks |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {u['arm']} | {u['shifts']} | {f(u['decisions_per_shift'], 1)} | {f(u['calls_per_shift'], 1)} | {f(u['reused_share'])} | {f(u['latency_p50_ms_median'], 0)} | "
          f"{f(u['latency_p95_ms_median'], 0)} / {f(u['latency_p95_ms_max'], 0)} | {f(u['input_tokens_per_call'], 0)} | {f(u['usd_per_shift'], 4)} | {u['errors']} | {u['fallbacks']} |" for u in use]
    L += ["", "## S2 Cause class of the first automatic decision after each random event", "",
          "Agreement = the cause class of the first non-operator decision after the event equals the event's true class (evaluation truth). "
          "R0, R1 and R5 take the class from rule hints; R2–R4 from the decision model.", "",
          "| mode | events | agreement | no automatic decision |", "|---|---|---|---|"]
    L += [f"| {c['arm']} | {c['events']} | {f(c['agreement'])} | {f(c['no_decision'])} |" for c in cause_arm]
    L += ["", "| true cause | " + " | ".join(ARMS) + " |", "|---|" + "---|" * len(ARMS)]
    for t in sorted({c["true_cause"] for c in cause_kind}):
        cells = {c["arm"]: c for c in cause_kind if c["true_cause"] == t}
        L.append(f"| {t} (n = {cells['R1']['events']}) | " + " | ".join(f(cells[a]["agreement"], 2) if a in cells else "—" for a in ARMS) + " |")
    L += ["", "## S3 Transport per shift (confirmatory experiment)", "",
          "| mode | orders | duplicate executions | duplicates suppressed | wrong pallet | premature dispatch | battery violations | timed-out orders | transport errors | order completion |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {t['arm']} | {f(t['orders'], 1)} | {f(t['duplicate_executions'], 2)} | {f(t['duplicates_suppressed'], 2)} | {f(t['wrong_pallet'], 2)} | {f(t['premature_dispatch'], 2)} | "
          f"{f(t['battery_violations'], 2)} | {f(t['timed_out_orders'], 2)} | {f(t['transport_errors'], 2)} | {f(t['order_completion_rate'])} |" for t in tr]
    L += ["", "## S4 Detection and first automatic decision by disruption type", "",
          "| disruption | mode | events | detected | detection delay (median, s) | first automatic decision after the event (median delay, s) |", "|---|---|---|---|---|---|"]
    L += [f"| {d['kind']} | {d['arm']} | {d['events']} | {f(d['detected'])} | {f(d['detect_delay_s_median'], 0)} | {f(d['first_action_delay_s_median'], 0)} |" for d in det]
    L += ["", "## S5 Plan-completion difference by scenario content (paired, scenario-clustered bootstrap 95 % CI, unadjusted)", "",
          "| scenarios | contrast | pairs | difference | 95 % CI |", "|---|---|---|---|---|"]
    L += [f"| {g['group']} | {g['contrast']} | {g['pairs']} | {f(g['plan_completion_diff'])} | [{f(g['ci_low'])}, {f(g['ci_high'])}] |" for g in gain]
    if sens_meta:
        L += ["", f"## S6 Sensitivity to the repeat-fault probability after an unstable restart (real Jev, {sens_meta['pairs']} pairs)", "",
              f"{sens_meta['scenarios']} of the confirmatory scenarios × 2 loads, all six modes, re-run with {sens_meta['param']} = "
              f"{', '.join(str(v) for v in sens_meta['values'])}; the 0.6 rows are the stored confirmatory runs on the same scenarios. "
              f"New shifts used: {sens_meta['shifts']} ({sens_meta['real_jev_shifts']} with real Jev, {sens_meta['jev_calls']} calls, "
              f"models {', '.join(sens_meta['models'])}); {sens_meta['flagged']} further real-Jev shifts had an API error that led to a fallback "
              f"decision, were excluded and re-run (total cost including them ${sens_meta['usd']}). Holm within each probability and metric.", "",
              "| repeat-fault p | contrast | metric | pairs | difference | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|---|"]
        L += [f"| {s['recurrence_p']} | {s['a']}−{s['b']} | {SENS_METRICS[s['metric']]} | {s['n_pairs']} | {f(s['diff'])} | [{f(s['ci_low'])}, {f(s['ci_high'])}] | {f(s['p_holm'])} |"
              for s in sorted(sens, key=lambda s: (list(SENS_METRICS).index(s["metric"]), SENS_CONTRASTS.index((s["a"], s["b"])), s["recurrence_p"]))]
        L += ["", "| repeat-fault p | mode | shifts | plan completion | unsafe / shift | operating loss |", "|---|---|---|---|---|---|"]
        L += [f"| {m['recurrence_p']} | {m['arm']} | {m['shifts']} | {f(m['plan_completion'])} | {f(m['unsafe_per_shift'], 2)} | {f(m['loss'], 0)} |" for m in sens_means]
    if rt:
        L += ["", "## S7 Test–retest of the decision model (identical requests sent again with no cache)", "",
              f"{rt['requests']} distinct requests sampled from the sensitivity runs, each sent {rt['repeats']} more times ({rt['calls']} calls, "
              f"{rt['errors']} errors, models {', '.join(rt['models'])}).", "",
              "| measure | value |", "|---|---|",
              f"| same action as the original response (per repeat) | {f(rt['action_agreement_with_original'])} |",
              f"| same action in the original and both repeats | {f(rt['action_same_in_all_three'])} |",
              f"| same action between the two repeats | {f(rt['action_agreement_between_repeats'])} |",
              f"| same cause class as the original | {f(rt['cause_agreement_with_original'])} |",
              f"| needs-a-person probability, absolute difference (median / p95) | {f(rt['needs_operator_abs_diff_median'])} / {f(rt['needs_operator_abs_diff_p95'])} |",
              f"| largest change of a choice probability (median / p95) | {f(rt['max_choice_prob_abs_diff_median'])} / {f(rt['max_choice_prob_abs_diff_p95'])} |",
              f"| same action, original top-two margin < 0.2 (n = {rt['agreement_low_margin']['n']}) | {f(rt['agreement_low_margin']['rate'])} |",
              f"| same action, original top-two margin ≥ 0.2 (n = {rt['agreement_high_margin']['n']}) | {f(rt['agreement_high_margin']['rate'])} |",
              f"| call latency p50 / p95 (ms) | {f(rt['latency_ms_p50'], 0)} / {f(rt['latency_ms_p95'], 0)} |"]
        L += [f"| same action as the original, {a} requests (n = {v['n']}) | {f(v['action_agreement'])} |" for a, v in rt["per_arm"].items()]
    if ad:
        lab = dict(target_met="plan completion", forbidden_allowed="unsafe commands authorized / shift", kwh_per_good="kWh per good part",
                   var_kwh_per_good="kWh per good part without the fixed auxiliary load", cost_balanced="operating loss", overloads="feeder overloads / shift",
                   tripped_min="feeder-trip downtime (min / shift)", factory_kwh_total="factory energy (kWh / shift)", operator_share="share of decisions made by the simulated operator")
        P = lambda x: "< 0.001" if x is not None and x < 0.001 else f(x)  # noqa: E731
        L += ["", "## S8 Added comparisons: 2 × 2 design and rule baselines (confirmatory scenarios, 420 pairs)", "",
              "R1s = R1 + waiting for the stabilisation diagnostic before a restart; R3r / R4r = R3 / R4 with the fixed rules instead of the decision model "
              "(no API calls). Added after the confirmatory analysis. 95 % CI from a scenario-clustered bootstrap (2,000, unadjusted); p from a "
              "scenario-level sign-flip permutation test (10,000), Holm-adjusted within each outcome over the contrasts listed.", "",
              "| contrast | outcome | pairs | difference | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|"]
        L += [f"| {x['a']}−{x['b']} ({x['label']}) | {lab[x['metric']]} | {x['n_pairs']} | {f(x['diff'], 4 if 'kwh' in x['metric'] else 3)} | "
              f"[{f(x['ci_low'], 4 if 'kwh' in x['metric'] else 3)}, {f(x['ci_high'], 4 if 'kwh' in x['metric'] else 3)}] | {P(x['p_holm'])} |" for x in ad["added"]]
        L += ["", "## S8b Electrical outcomes, paired (420 pairs; Holm within each outcome over these four contrasts)", "",
              "| contrast | outcome | pairs | difference | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|"]
        L += [f"| {x['a']}−{x['b']} ({x['label']}) | {lab.get(x['metric'], 'peak load (kW)')} | {x['n_pairs']} | {f(x['diff'], 4 if 'kwh_per' in x['metric'] else 2)} | "
              f"[{f(x['ci_low'], 4 if 'kwh_per' in x['metric'] else 2)}, {f(x['ci_high'], 4 if 'kwh_per' in x['metric'] else 2)}] | {P(x['p_holm'])} |" for x in ad["electrical"]]
        L += ["", "## S9 Means per mode incl. the added comparisons and electrical outcomes (420 shifts per mode, both loads)", "",
              "| mode | plan completion | unsafe / shift | operator share | kWh / good | kWh / good w/o aux | factory kWh / shift | peak kW | overloads / shift | trip downtime (min) | output / target | backup-line use |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {m['arm']} | {f(m['target_met'])} | {f(m['forbidden_allowed'], 2)} | {f(m['operator_share'])} | {f(m['kwh_per_good'], 4)} | {f(m['var_kwh_per_good'], 4)} | "
              f"{f(m['factory_kwh_total'], 1)} | {f(m['max_kw'], 1)} | {f(m['overloads'], 3)} | {f(m['tripped_min'], 2)} | {f(m['good_to_target'])} | {f(m['sub_utilization'])} |"
              for m in ad["means"] if m["load"] == "both"]
        L += ["", "| mode | load | plan completion | backup-line use | output / target |", "|---|---|---|---|---|"]
        L += [f"| {m['arm']} | {m['load']} | {f(m['target_met'])} | {f(m['sub_utilization'])} | {f(m['good_to_target'])} |" for m in ad["means"] if m["load"] != "both"]
        L += ["", "## S10 Unsafe commands authorized per shift, by reason (ground-truth oracle)", "",
              "| mode | " + " | ".join(sorted({k for d in ad["unsafe_reasons"].values() for k in d})) + " |", "|---|" + "---|" * len({k for d in ad["unsafe_reasons"].values() for k in d})]
        keys = sorted({k for d in ad["unsafe_reasons"].values() for k in d})
        L += [f"| {a} | " + " | ".join(f(ad["unsafe_reasons"][a].get(k, 0.0), 3) for k in keys) + " |" for a in sorted(ad["unsafe_reasons"], key=lambda a: (a[:2], a))]
        L += ["", "## S11 Rule baseline with stabilisation wait under the restart assumptions (60 scenarios × 2 loads)", "",
              "| repeat-fault p | contrast | outcome | pairs | difference | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|---|"]
        L += [f"| {x['recurrence_p']} | {x['a']}−{x['b']} | {lab[x['metric']]} | {x['n_pairs']} | {f(x['diff'], 4 if 'kwh' in x['metric'] else 3)} | "
              f"[{f(x['ci_low'], 4 if 'kwh' in x['metric'] else 3)}, {f(x['ci_high'], 4 if 'kwh' in x['metric'] else 3)}] | {P(x['p_holm'])} |" for x in ad["stabilisation_sensitivity"]]
        if ad.get("operator"):
            om = ad["operator_meta"]
            L += ["", f"## S12 Simulated operator answering with the fixed rules instead of the planner ({om['pairs']} pairs)", "",
                  f"Same 60 scenarios as the sensitivity experiment; new shifts {om['shifts']} ({om['real_jev_shifts']} with real Jev, {om['jev_calls']} calls, ${om['usd']}, "
                  f"models {', '.join(om['models'])}). 'planner' rows are the stored runs of the same scenarios.", "",
                  "| operator | contrast | outcome | pairs | difference | 95 % CI | p (Holm) |", "|---|---|---|---|---|---|---|"]
            L += [f"| {x['operator']} | {x['a']}−{x['b']} | {lab[x['metric']]} | {x['n_pairs']} | {f(x['diff'])} | [{f(x['ci_low'])}, {f(x['ci_high'])}] | {P(x['p_holm'])} |" for x in ad["operator"]]
            L += ["", "| operator | mode | shifts | plan completion | operator share | unsafe / shift |", "|---|---|---|---|---|---|"]
            L += [f"| {m['operator']} | {m['arm']} | {m['shifts']} | {f(m['target_met'])} | {f(m['operator_share'])} | {f(m['forbidden_allowed'], 2)} |" for m in ad["operator_means"]]
        if ad.get("interim"):
            L += ["", "## S13 Interim analysis (first 280 pairs) and the added 140 pairs, plan completion", "",
                  "| sample | contrast | pairs | difference | 95 % CI | p (Holm within the sample) |", "|---|---|---|---|---|---|"]
            L += [f"| {x['part']} | {x['a']}−{x['b']} | {x['n_pairs']} | {f(x['diff'])} | [{f(x['ci_low'])}, {f(x['ci_high'])}] | {P(x['p_holm'])} |" for x in ad["interim"]]
    md.write_text("\n".join(L) + "\n", encoding="utf-8")
    return summary


def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv(Path(".env"), override=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sensitivity", "retest", "variant", "report"])
    ap.add_argument("--name", default="ablation")
    ap.add_argument("--arms", default="R1s,R3r,R4r")
    ap.add_argument("--set", default="", help="overrides, e.g. recurrence_p_unstable=0.3,operator_policy=rules")
    ap.add_argument("--values", default="0.0,0.3")
    ap.add_argument("--param", default="recurrence_p_unstable")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--k", type=int, default=300)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "sensitivity":
        sensitivity([float(v) for v in a.values.split(",")], a.n, a.param, a.budget, a.workers)
    elif a.cmd == "variant":
        ov = {}
        for kv in filter(None, a.set.split(",")):
            k, v = kv.split("=", 1)
            try:
                ov[k] = float(v)
            except ValueError:
                ov[k] = v
        variant(a.name, a.arms.split(","), a.n, ov, a.budget, a.workers)
        return
    elif a.cmd == "retest":
        retest(a.k, a.repeats, a.budget)
    print(json.dumps({k: v for k, v in report().items() if k in ("confirmatory_runs", "retest")}, default=str)[:1500])


if __name__ == "__main__":
    main()
