"""Tables T1–T10 and figures for the research report, generated only from a finished experiment directory.

    python -m sfaic2.v3.report results/v3/mock/main
    python -m sfaic2.v3.report results/v3/real_jev/pilot

Every table/figure produced from results/v3/mock/* carries the label "MOCK_JEV — software check, not evidence about
TypeSafe Jev". Nothing here invents numbers: empty inputs produce empty tables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["axes.titlepad"] = 18      # leave room for the provenance banner above each title
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import stats as S  # noqa: E402
from .config import FAULT_CATALOGUE, LOADS, SCENARIOS, default_factory  # noqa: E402
from .control import ARM_LABEL, ARMS  # noqa: E402

ARM_ORDER = ["R0", "R1", "R2", "R3", "R4", "R5"]
COLORS = {"R0": "#9aa5b1", "R1": "#6b7c93", "R2": "#2f7f86", "R3": "#1f5f8b", "R4": "#0b3d63", "R5": "#b5651d"}


def _md(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    if df is None or df.empty:
        return "_(no rows)_\n"
    cols = list(df.columns)
    out = "| " + " | ".join(map(str, cols)) + " |\n|" + "---|" * len(cols) + "\n"
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(format(v, floatfmt) if isinstance(v, (float, np.floating)) and not pd.isna(v) else ("—" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)))
        out += "| " + " | ".join(cells) + " |\n"
    return out


def build(exp_dir: Path, oracle_dir: Path | None = None) -> dict:
    exp_dir = Path(exp_dir)
    man = json.loads((exp_dir / "manifest.json").read_text())
    group = man["provider_group"]
    banner = "MOCK_JEV — software check, not evidence about TypeSafe Jev" if group == "mock" else f"TypeSafe Jev System One ({man.get('jev_model')}) — real calls"
    df_all = pd.read_csv(exp_dir / "episodes.csv")
    if "error" in df_all.columns:
        errors = df_all[df_all["error"].notna()]
        df_all = df_all[df_all["error"].isna()]
    else:
        errors = df_all.iloc[:0]
    base = df_all[df_all.get("forced_first", pd.Series(index=df_all.index, dtype=object)).isna()] if "forced_first" in df_all else df_all
    if "sensitivity" in base:
        sens_df = base[base["sensitivity"].notna()]
        base = base[base["sensitivity"].isna()]
    else:
        sens_df = base.iloc[:0]
    oracle = df_all[df_all["forced_first"].notna()] if "forced_first" in df_all else df_all.iloc[:0]
    if oracle_dir is not None and (Path(oracle_dir) / "episodes.csv").exists():   # separately re-run oracle (records forced_applied)
        o2 = pd.read_csv(Path(oracle_dir) / "episodes.csv")
        oracle = o2[o2["forced_first"].notna()] if "forced_first" in o2 else o2.iloc[:0]
    main = base[base.arm.isin(ARM_ORDER)]
    abl = base[base.arm.str.startswith("R4")]
    out = exp_dir / "paper"
    fig = exp_dir / "figures"
    out.mkdir(exist_ok=True)
    fig.mkdir(exist_ok=True)
    T: dict[str, pd.DataFrame] = {}
    f0 = default_factory()
    # T1 provenance
    T["T1_provenance"] = pd.DataFrame([
        dict(source="Plant / line / fleet / feeder model", nature="SYNTHETIC (SimPy, documented assumptions)", used_for="all episodes", licence="—"),
        dict(source="Camera gate raw JSON", nature=str(main.vision_source.iloc[0]) if len(main) else "—", used_for="vision_events.raw_json (verbatim YOLO11n output replayed per unit)",
             licence="MVTec AD CC BY-NC-SA 4.0 (non-commercial)"),
        dict(source="Fault catalogue E101/E201/E301/E302", nature="SYNTHETIC catalogue; true repair times drawn per seed (hidden)", used_for="ETA estimate vs truth", licence="—"),
        dict(source="Decision provider", nature=banner, used_for="arms R2–R4", licence="TypeSafe terms" if group != "mock" else "—"),
    ])
    # T2 factory / network parameters
    T["T2_parameters"] = pd.DataFrame([dict(parameter=k, value=v) for k, v in [
        ("shift_s", f0.plan.shift_s), ("loads (target = load × main capacity)", json.dumps(LOADS)), ("main CT / yield / run kW", f"{f0.line('main').ct_s} s / {f0.line('main').yield_rate} / {f0.line('main').run_kw}"),
        ("sub CT / yield / run kW / startup", f"{f0.line('sub').ct_s} s / {f0.line('sub').yield_rate} / {f0.line('sub').run_kw} / {f0.line('sub').startup_s} s"),
        ("feeder nameplate / aux kW", f"{f0.power.nameplate_limit_kw} / {f0.power.aux_kw}"), ("pallet qty / outlet buffer", f"{f0.fleet.pallet_qty} / {f0.fleet.out_buffer_pallets}"),
        ("AMR (line / reserve)", f"{f0.fleet.n_amr} + {f0.fleet.n_reserve_amr} @ {f0.fleet.amr_speed_mps} m/s"), ("PLC sample / stale after", f"{f0.network.plc_sample_s} s / {f0.network.stale_after_s} s"),
        ("decision interval / human response", f"{f0.decision_interval_s} s / {f0.human_response_s} s"), ("recurrence p if restarted unstable", f0.recurrence_p_unstable),
        ("fault catalogue ETA (s)", json.dumps({k: v.eta_catalogue_s for k, v in FAULT_CATALOGUE.items()}))]])
    # T3 baselines
    T["T3_baselines"] = pd.DataFrame([dict(arm=a, label=ARM_LABEL.get(a, a), detector=c["detector"], chooser=c["chooser"], validated=c["validated"], gate=c["gate"], context=c["context"],
                                           transport=c["transport"], adaptive_timeout=c["adaptive"], jev_info_removed=",".join(sorted(c["drop"])) or "—") for a, c in ARMS.items()])
    # T4 main paired outcomes (primary)
    con = S.contrasts(main) if len(main) else pd.DataFrame()
    T["T4_main_paired_primary"] = con[con.family == "primary"] if len(con) else con
    arm_tab = S.by_arm(main).reset_index() if len(main) else pd.DataFrame()
    T["T4b_by_arm"] = arm_tab[[c for c in ["arm", "n", "par", "target_met", "raw_attainment", "good_end", "shortfall", "unsafe_authorization_rate_pooled", "forbidden_proposed", "forbidden_allowed",
                                           "cost_balanced", "cost_quality", "cost_energy", "defects_shipped", "unnecessary_stop_min", "action_delay_min"] if c in arm_tab]] if len(arm_tab) else arm_tab
    # T5 detection, cause attribution
    det = main.groupby("arm")[["detection_precision", "detection_recall", "detection_f1", "false_alarms_per_h", "mttd_s"]].mean().reset_index() if len(main) else pd.DataFrame()
    T["T5_detection"] = det.merge(S.cause_macro_f1(main), on="arm", how="left") if len(main) else det
    # T6 decision / recovery
    rec = main.groupby("arm")[["mttr_main_s", "production_recovery_s", "recovery_success", "recurrences", "escalations", "escalations_warranted", "escalation_precision",
                               "escalation_recall", "gate_blocks", "stale_decisions", "decisions", "operator_decisions", "forecast_error_first"]].mean().reset_index() if len(main) else pd.DataFrame()
    reg = S.regret(main, oracle) if len(oracle) else pd.DataFrame()
    T["T6_decision_recovery"] = rec.merge(reg, on="arm", how="left") if len(reg) else rec
    # T7 MQTT transport
    T["T7_transport"] = main.groupby("arm")[["orders", "completed_orders", "order_completion_rate", "duplicate_executions", "duplicates_suppressed", "wrong_pallet", "premature_dispatch",
                                             "battery_violations", "ack_timeouts", "retries", "ack_p50_ms", "ack_p95_ms", "status_regressions", "qr_holds", "transport_errors",
                                             "amr_utilization"]].mean().reset_index() if len(main) else pd.DataFrame()
    # T8 electrical / OT
    T["T8_electrical_ot"] = main.groupby("arm")[["energy_kwh", "kwh_per_good", "max_kw", "overloads", "tripped_min", "msgs_lost", "msgs_dup", "stale_decisions",
                                                 "jev_calls", "jev_latency_p50_ms", "jev_latency_p95_ms", "jev_latency_p99_ms", "jev_errors", "jev_input_tokens", "jev_cost_usd", "fallbacks"]].mean().reset_index() if len(main) else pd.DataFrame()
    # T9 ablation
    if len(abl) and abl.arm.nunique() > 1:
        rows = []
        for a in sorted(abl.arm.unique()):
            if a == "R4":
                continue
            for m in ("par", "target_met", "cost_balanced", "unsafe_authorization_rate", "transport_errors", "overloads"):
                rows.append(dict(ablation=a, **S.paired(abl, a, "R4", m)))
        T["T9_ablation"] = pd.DataFrame(rows)
    else:
        T["T9_ablation"] = pd.DataFrame()
    # T10 all contrasts incl. secondary with Holm
    T["T10_contrasts_all"] = con
    # by scenario
    T["T11_by_scenario_arm"] = main.groupby(["scenario", "arm"])[["target_met", "par", "cost_balanced", "forbidden_allowed", "transport_errors", "overloads", "escalations"]].mean().reset_index() if len(main) else pd.DataFrame()
    if len(sens_df):
        T["T12_sensitivity"] = sens_df.groupby(["sensitivity", "arm"])[["target_met", "par", "cost_balanced", "forbidden_allowed", "overloads"]].mean().reset_index()
    for k, v in T.items():
        v.to_csv(out / f"{k}.csv", index=False)
    md = [f"# SF AI OS v3 results — {exp_dir.name}\n\n**{banner}.** Generated from `{exp_dir}/episodes.csv` ({len(main)} paired episodes, "
          f"{len(oracle)} replay-oracle episodes, {len(errors)} failed episodes). Seeds: {sorted(main.seed.unique().tolist()) if len(main) else []}; "
          f"loads: {sorted(main['load'].unique().tolist()) if len(main) else []}; scenarios: {sorted(main.scenario.unique().tolist()) if len(main) else []}.\n"]
    for k, v in T.items():
        md.append(f"\n## {k}\n\n" + _md(v))
    (out / "RESULTS_TABLES.md").write_text("".join(md), encoding="utf-8")
    figs = figures(main, abl, fig, banner, exp_dir, group)
    return dict(tables=list(T), figures=figs, episodes=len(main), errors=len(errors), banner=banner)


def _label(ax, banner):
    ax.text(0.0, 1.005, banner, transform=ax.transAxes, fontsize=6.5, color="#a33" if banner.startswith("MOCK") else "#264", va="bottom")


def figures(main: pd.DataFrame, abl: pd.DataFrame, fig: Path, banner: str, exp_dir: Path, group: str) -> list[str]:
    made = []
    arms = [a for a in ARM_ORDER if a in set(main.arm)]
    if not arms:
        return made
    # F2 on-time completion and PAR by arm (cluster bootstrap CI)
    f, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    for i, m in enumerate(("target_met", "par")):
        means, los, his = [], [], []
        for a in arms:
            d = main[main.arm == a][["seed", m]].rename(columns={m: "d"})
            means.append(d.d.mean())
            lo, hi = S.cluster_boot(d)
            los.append(d.d.mean() - lo if lo == lo else 0)
            his.append(hi - d.d.mean() if hi == hi else 0)
        ax[i].bar(arms, means, yerr=[los, his], color=[COLORS[a] for a in arms], capsize=3)
        ax[i].set_ylim(0, 1.05)
        ax[i].set_title({"target_met": "On-time plan completion rate", "par": "Plan attainment rate (PAR)"}[m], fontsize=10)
        _label(ax[i], banner)
    f.tight_layout(); f.savefig(fig / "F2_completion_par_by_arm.png", dpi=160); plt.close(f); made.append("F2_completion_par_by_arm.png")
    # F3 heatmap scenario × arm completion
    pv = main.pivot_table(index="scenario", columns="arm", values="target_met", aggfunc="mean").reindex(columns=arms)
    pv = pv.reindex(sorted(pv.index, key=lambda s: int(s[1:])))
    f, ax = plt.subplots(figsize=(6.5, 5.2))
    im = ax.imshow(pv.values, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(arms)), arms); ax.set_yticks(range(len(pv.index)), [f"{s} {SCENARIOS[s].title_en}" for s in pv.index], fontsize=7)
    for (y, x), v in np.ndenumerate(pv.values):
        if v == v:
            ax.text(x, y, f"{v:.2f}", ha="center", va="center", fontsize=7, color="white" if v > 0.6 else "#123")
    f.colorbar(im, ax=ax, fraction=0.04); ax.set_title("On-time completion by scenario", fontsize=10); _label(ax, banner)
    f.tight_layout(); f.savefig(fig / "F3_completion_heatmap.png", dpi=160); plt.close(f); made.append("F3_completion_heatmap.png")
    # F4 unsafe authorisation
    tab = S.by_arm(main)
    f, ax = plt.subplots(1, 2, figsize=(9, 3.2))
    ax[0].bar(arms, [tab.loc[a, "unsafe_authorization_rate_pooled"] if a in tab.index else np.nan for a in arms], color=[COLORS[a] for a in arms])
    ax[0].set_ylim(0, 1.05); ax[0].set_title("Unsafe authorisation rate (pooled)", fontsize=10); _label(ax[0], banner)
    ax[1].bar(arms, [main[main.arm == a].executed_unsafe.sum() for a in arms], color=[COLORS[a] for a in arms]); ax[1].set_title("Executed unsafe commands (total)", fontsize=10)
    f.tight_layout(); f.savefig(fig / "F4_unsafe_by_arm.png", dpi=160); plt.close(f); made.append("F4_unsafe_by_arm.png")
    # F5 cost frontier
    f, ax = plt.subplots(figsize=(5.5, 4))
    for a in arms:
        d = main[main.arm == a]
        ax.scatter(d.shortfall.mean(), d.kwh_per_good.mean(), s=80, color=COLORS[a], label=a)
        ax.annotate(a, (d.shortfall.mean(), d.kwh_per_good.mean()), xytext=(5, 4), textcoords="offset points", fontsize=8)
    ax.set_xlabel("mean shortfall (units)"); ax.set_ylabel("kWh per good unit"); ax.set_title("Production–energy frontier", fontsize=10); _label(ax, banner)
    f.tight_layout(); f.savefig(fig / "F5_cost_frontier.png", dpi=160); plt.close(f); made.append("F5_cost_frontier.png")
    # F6 transport errors
    sc = [s for s in ("S6", "S7", "S8", "S13", "S14") if s in set(main.scenario)]
    if sc:
        f, ax = plt.subplots(figsize=(8, 3.4))
        w = 0.8 / len(arms)
        for i, a in enumerate(arms):
            ax.bar(np.arange(len(sc)) + i * w, [main[(main.arm == a) & (main.scenario == s)].transport_errors.mean() for s in sc], w, color=COLORS[a], label=a)
        ax.set_xticks(np.arange(len(sc)) + 0.4 - w / 2, sc); ax.set_ylabel("transport errors / episode"); ax.legend(fontsize=7, ncol=6); ax.set_title("MQTT transport errors", fontsize=10); _label(ax, banner)
        f.tight_layout(); f.savefig(fig / "F6_transport_errors.png", dpi=160); plt.close(f); made.append("F6_transport_errors.png")
    # F7 ablation
    if len(abl) and abl.arm.nunique() > 1:
        ab = abl.groupby("arm")[["target_met", "cost_balanced"]].mean()
        f, ax = plt.subplots(1, 2, figsize=(9, 3.2))
        ax[0].bar(ab.index, ab.target_met, color="#2f7f86"); ax[0].set_title("Completion rate — R4 ablations", fontsize=10); ax[0].tick_params(axis="x", rotation=40, labelsize=7); _label(ax[0], banner)
        ax[1].bar(ab.index, ab.cost_balanced, color="#6b7c93"); ax[1].set_title("Decision cost (balanced)", fontsize=10); ax[1].tick_params(axis="x", rotation=40, labelsize=7)
        f.tight_layout(); f.savefig(fig / "F7_ablation.png", dpi=160); plt.close(f); made.append("F7_ablation.png")
    # F8 runtime
    f, ax = plt.subplots(1, 2, figsize=(9, 3.2))
    ax[0].boxplot([main[main.arm == a].wall_s.dropna() for a in arms], tick_labels=arms); ax[0].set_title("Episode wall time (s)", fontsize=10); _label(ax[0], banner)
    lat = main[main.jev_latency_p50_ms.notna()] if "jev_latency_p50_ms" in main else main.iloc[:0]
    if len(lat) and group != "mock":
        ax[1].boxplot([lat[lat.arm == a].jev_latency_p50_ms for a in arms if (lat.arm == a).any()], tick_labels=[a for a in arms if (lat.arm == a).any()])
        ax[1].set_title("Jev latency p50 per episode (ms)", fontsize=10)
    else:
        ax[1].text(0.5, 0.5, "no real Jev latency (mock)", ha="center"); ax[1].axis("off")
    f.tight_layout(); f.savefig(fig / "F8_runtime.png", dpi=160); plt.close(f); made.append("F8_runtime.png")
    # F1 + F9 timelines / traces (re-simulated deterministically with series; mock group only to avoid extra API cost)
    if group == "mock":
        from .episode import run_episode
        sc = [s for s in ("S2", "S3", "S9", "S15") if s in set(main.scenario)]
        if sc:
            f, axs = plt.subplots(1, len(sc), figsize=(4 * len(sc), 3.2), squeeze=False)
            for ax, s in zip(axs[0], sc):
                for a in [x for x in ("R1", "R2", "R4") if x in arms]:
                    r = run_episode(s, "medium", 0, a, series=True)
                    ser = pd.DataFrame(r["_series"])
                    ax.plot(ser.t / 3600, ser.good, color=COLORS[a], label=a)
                ax.plot(ser.t / 3600, ser.target, "--", color="#999", label="plan (linear)")
                ax.set_title(f"{s} {SCENARIOS[s].title_en}", fontsize=9); ax.set_xlabel("h"); _label(ax, banner)
            axs[0][0].set_ylabel("cumulative good units"); axs[0][0].legend(fontsize=7)
            f.tight_layout(); f.savefig(fig / "F1_target_vs_actual.png", dpi=160); plt.close(f); made.append("F1_target_vs_actual.png")
            r = run_episode("S3", "medium", 0, "R4", series=True)
            f, ax = plt.subplots(figsize=(10, 3))
            ser = pd.DataFrame(r["_series"])
            ax.plot(ser.t / 3600, ser.good, color="#0b3d63")
            for d in r["_decisions"]:
                ax.axvline(d["t"] / 3600, color="#2f7f86" if d["final"] != "ESCALATE_HUMAN" else "#a33", alpha=0.25)
                ax.text(d["t"] / 3600, ser.good.max() * 0.05, d["final"].replace("_", " ").title(), rotation=90, fontsize=6)
            for c in r["_commands"]:
                ax.plot(c["t"] / 3600, 0, "v", color="#b5651d")
            ax.set_title("S3 R4 — incident → snapshot → Jev choice → gate → virtual PLC command (▼)", fontsize=9); ax.set_xlabel("h"); _label(ax, banner)
            f.tight_layout(); f.savefig(fig / "F9_decision_trace_S3_R4.png", dpi=160); plt.close(f); made.append("F9_decision_trace_S3_R4.png")
    return made


if __name__ == "__main__":
    print(json.dumps(build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None), indent=2))
