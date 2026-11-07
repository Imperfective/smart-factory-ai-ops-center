"""Run the pre-registered matrix: fault families × seeds × arms (A–E) with common random numbers (same seed ⇒
identical physics until a virtual STOP diverges the trajectory). Each arm runs *coupled* (decisions feed the
virtual PLC) with a simulated operator. Outputs results/experiments/<exp_id>/.

  .venv/bin/python -m sfaic2.experiments.runner --exp smoke --seeds 3 --provider mock
  .venv/bin/python -m sfaic2.experiments.runner --exp jev_pilot --seeds 2 --provider typesafe --arms C D E   (JEV_ENABLED=true)
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import __version__
from ..adapters.typesafe_jev import TypeSafeJevProvider
from ..contracts.enums import FaultKind
from ..decision.engine import ARMS, DecisionEngine, OperatorPolicy
from ..decision.providers import MockJevProvider
from ..runs import save_run
from ..simulator.production import FactorySim, RunOutput
from ..simulator.scenarios import FAULT_FAMILIES, demo_factory, fault_preset
from .metrics import composite_loss, run_metrics

WEIGHTS = dict(fn=10.0, fs=0.5, delay=0.2, energy=1.0, plan=20.0)  # pre-registered; see docs/13
PRIMARY = ["composite_loss", "unnecessary_stop_min"]
SECONDARY = ["f1", "harmful_false_continue", "false_stop_proposals", "unnecessary_review", "correct_action_rate", "intervention_delay_s",
             "plan_attainment", "energy_kwh_per_good_part", "unsafe_actions_allowed"]
FAULT_TARGET = {FaultKind.QR_MISROUTE: "QA_PACK_01", FaultKind.VISION_FALSE_NEGATIVE: "QA_PACK_01", FaultKind.VISION_DEFECT: "QA_PACK_01"}


def build_config(family: str, seed: int, duration_s: int, quantity: int, severity: float):
    """Fault family (F01..F09 / normal) or a named scenario (S1..S8) as an experiment unit."""
    from ..simulator.scenarios import SCENARIOS
    if family in SCENARIOS:
        cfg = SCENARIOS[family](seed=seed, duration_s=duration_s)
        cfg.scenario_id = f"exp_{family}"
        target = cfg.faults[0].machine_id if cfg.faults else None
        return cfg, target
    faults = []
    kind = FAULT_FAMILIES.get(family)
    target = None
    if kind is not None:
        target = FAULT_TARGET.get(kind, "PRESS_01")
        faults = [fault_preset(kind, 600, 900, target, severity, fault_id=f"{family}_{kind.value.lower()}")]
    cfg = demo_factory(f"exp_{family}", quantity=quantity, duration_s=duration_s, seed=seed, faults=faults)
    return cfg, target


def run_coupled(cfg, seed: int, arm: str, provider: str, jev_cache: dict, operator: OperatorPolicy, interval_s: float = 30.0,
                enable_electrical: bool = True) -> tuple[RunOutput, object]:
    sim = FactorySim(cfg, seed=seed, run_id=f"exp_{cfg.scenario_id}_s{seed}_{arm}_{provider}", enable_electrical=enable_electrical)
    sim.start()
    stub = RunOutput(run_id=sim.run_id, config=cfg, seed=seed, events=[], truth=sim.truth, kpis={}, manifest=dict(sim_seconds=cfg.simulation.duration_s),
                     plc_history=[], network_stats={}, powerflow_failures=0)
    override = MockJevProvider() if provider == "mock" else None
    jev = TypeSafeJevProvider(cache=jev_cache) if provider == "typesafe" else None
    eng = DecisionEngine(stub, arm=arm, decision_interval_s=interval_s, provider_override=override, jev_provider=jev, plc=sim.plc,
                         events_source=sim.observed_events, operator=operator, auto_operator=True)
    t = interval_s
    while t <= cfg.simulation.duration_s:
        sim.advance(t)
        eng.step(t)
        t += interval_s
    out = sim.finalize()
    return out, eng.result()


def bootstrap_paired(df: pd.DataFrame, metric: str, arm_a: str, arm_b: str, n: int = 2000, seed: int = 2026) -> dict:
    """Paired (fault × seed) bootstrap of mean(arm_a − arm_b)."""
    a = df[df.arm == arm_a].set_index(["family", "seed"])[metric]
    b = df[df.arm == arm_b].set_index(["family", "seed"])[metric]
    idx = a.index.intersection(b.index)
    d = (a.loc[idx] - b.loc[idx]).dropna().to_numpy(float)
    if len(d) == 0:
        return dict(metric=metric, contrast=f"{arm_a}-{arm_b}", n_pairs=0, mean_diff=None, ci_low=None, ci_high=None)
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(d, len(d), replace=True).mean() for _ in range(n)])
    return dict(metric=metric, contrast=f"{arm_a}-{arm_b}", n_pairs=int(len(d)), mean_diff=float(d.mean()),
                ci_low=float(np.quantile(boots, 0.025)), ci_high=float(np.quantile(boots, 0.975)),
                excludes_zero=bool(np.quantile(boots, 0.025) > 0 or np.quantile(boots, 0.975) < 0))


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(".env"), override=False)  # key stays in the local .env; never echoed
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exp", default="smoke")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed-start", type=int, default=100)
    ap.add_argument("--arms", nargs="+", default=list(ARMS))
    ap.add_argument("--families", nargs="+", default=["normal"] + list(FAULT_FAMILIES))
    ap.add_argument("--provider", choices=["mock", "typesafe"], default="mock")
    ap.add_argument("--duration-s", type=int, default=2400)
    ap.add_argument("--quantity", type=int, default=300)
    ap.add_argument("--severity", type=float, default=1.0)
    ap.add_argument("--operator-delay-s", type=float, default=5.0)
    ap.add_argument("--operator-approve-rate", type=float, default=1.0)
    ap.add_argument("--no-electrical", action="store_true")
    ap.add_argument("--save-runs", action="store_true", help="persist every run's events/decisions (large)")
    ap.add_argument("--jev-max-calls-total", type=int, default=6000, help="abort new Jev runs once this many real calls were made")
    a = ap.parse_args()
    if a.provider == "typesafe":
        ok, why = TypeSafeJevProvider().availability()
        if not ok:
            raise SystemExit(f"ABORT: TypeSafe Jev unavailable before the matrix started ({why}); refusing to run a fallback-only experiment")
    out = Path("results/experiments") / a.exp
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "paper").mkdir(exist_ok=True)
    rows, jev_rows, cache = [], [], {}
    total_jev_calls = 0
    t0 = time.time()
    seeds = list(range(a.seed_start, a.seed_start + a.seeds))
    for family in a.families:
        for seed in seeds:
            for arm in a.arms:
                if a.provider == "typesafe" and not ARMS[arm]["jev"]:
                    prov = "none"
                else:
                    prov = a.provider if ARMS[arm]["jev"] else "none"
                cfg, target = build_config(family, seed, a.duration_s, a.quantity, a.severity)
                run, eng = run_coupled(cfg, seed, arm, prov if prov != "none" else "mock", cache,
                                       OperatorPolicy(delay_s=a.operator_delay_s, approve_rate=a.operator_approve_rate),
                                       enable_electrical=not a.no_electrical)
                fault_start = min((f.at_s for f in cfg.faults), default=None)
                m = run_metrics(run, eng, fault_start, target)
                m["composite_loss"] = round(composite_loss(m, WEIGHTS), 4)
                m.update(exp=a.exp, family=family, fault_kind=(FAULT_FAMILIES[family].value if family in FAULT_FAMILIES else (cfg.faults[0].kind.value if cfg.faults else "NONE")), seed=seed, arm=arm,
                         arm_label=ARMS[arm]["label"], provider=eng.arm_config.get("provider"), run_id=run.run_id, events_sha256=run.manifest["events_sha256"],
                         source_type="SIMULATED", jev_resolved_model=next((d.jev_call.resolved_model for d in eng.decisions if d.jev_call and d.jev_call.resolved_model), None))
                rows.append(m)
                if eng.jev_usage:
                    jev_rows.append(dict(run_id=run.run_id, arm=arm, family=family, seed=seed, **eng.jev_usage))
                    total_jev_calls += eng.jev_usage["calls"]
                    if total_jev_calls >= a.jev_max_calls_total:
                        print(f"JEV call cap {a.jev_max_calls_total} reached; remaining Jev runs are skipped (marked incomplete)", flush=True)
                        a.arms = [x for x in a.arms if not ARMS[x]["jev"]]
                if a.save_runs:
                    save_run(run, eng, root=out / "runs")
                print(f"{family:7s} seed {seed} arm {arm} [{m['provider']}] good {m['good_count']:4d} f1 {m['f1'] if m['f1'] is None else round(m['f1'],2)} "
                      f"stops {m['stops_executed']} unnec_stop_min {m['unnecessary_stop_min']} loss {m['composite_loss']}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "runs_all.csv", index=False)
    num = [c for c in PRIMARY + SECONDARY + ["good_count", "stops_executed", "stop_proposals", "gate_rejected", "jev_fallbacks"] if c in df]
    g = df.groupby(["arm", "arm_label", "provider"])[num]
    prim = g.mean().join(g.std().add_suffix("_sd")).join(g.size().rename("n_runs")).reset_index()
    prim.to_csv(out / "metrics_primary.csv", index=False)
    df.groupby(["arm", "family", "fault_kind"])[num].mean().reset_index().to_csv(out / "metrics_by_fault.csv", index=False)
    ci = []
    for metric in PRIMARY + SECONDARY:
        if metric not in df:
            continue
        for ref in ("N", "A", "B", "C", "D"):
            if "E" in a.arms and ref in a.arms:
                ci.append(bootstrap_paired(df, metric, "E", ref))
        if "C" in a.arms and "N" in a.arms:
            ci.append(bootstrap_paired(df, metric, "C", "N"))
        if "D" in a.arms and "C" in a.arms:
            ci.append(bootstrap_paired(df, metric, "D", "C"))
    pd.DataFrame(ci).to_csv(out / "paired_ci.csv", index=False)
    abl = []
    for (x, y, what) in (("E", "D", "electrical/network context"), ("D", "C", "evidence freshness/cross-source checks"), ("C", "B", "structured judgment provider"), ("B", "A", "ML anomaly baseline"), ("A", "N", "any supervisory layer vs none")):
        if x in a.arms and y in a.arms:
            for metric in PRIMARY + ["f1", "false_stop_proposals", "harmful_false_continue"]:
                r = bootstrap_paired(df, metric, x, y)
                abl.append(dict(ablation=f"{what} ({x} vs {y})", **r))
    pd.DataFrame(abl).to_csv(out / "ablations.csv", index=False)
    pd.DataFrame(jev_rows).to_csv(out / "jev_usage.csv", index=False)
    manifest = dict(exp=a.exp, created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), seeds=seeds, arms=a.arms, families=a.families, provider=a.provider,
                    duration_s=a.duration_s, quantity=a.quantity, severity=a.severity, operator=dict(delay_s=a.operator_delay_s, approve_rate=a.operator_approve_rate),
                    weights=WEIGHTS, primary_endpoints=PRIMARY, n_runs=len(df), wall_seconds=round(time.time() - t0, 1), sfaic2_version=__version__,
                    python=platform.python_version(), platform=platform.platform(), source_type="SIMULATED",
                    judgment_provider_note=("MOCK_JEV — NOT TypeSafe Jev; do not report as Jev performance" if a.provider == "mock" else "TypeSafe Jev via official API"),
                    jev_models=sorted({str(x) for x in df.jev_resolved_model.dropna().unique()}) if "jev_resolved_model" in df else [])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    from .report import make_figures, write_paper_tables
    make_figures(df, out / "figures")
    write_paper_tables(df, prim, pd.DataFrame(ci), pd.DataFrame(abl), manifest, out / "paper")
    print(f"DONE {len(df)} runs in {manifest['wall_seconds']} s -> {out}")


if __name__ == "__main__":
    main()
