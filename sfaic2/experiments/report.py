"""Figures (validated five-slot categorical palette, one axis each) and paper table shells from the metrics CSVs.
No number appears here that is not read from the DataFrames produced by the runner."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PALETTE = {"N": "#6b7280", "A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a", "D": "#eda100", "E": "#e87ba4"}


def make_figures(df: pd.DataFrame, out: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25})
    out.mkdir(parents=True, exist_ok=True)
    made = []
    arms = sorted(df.arm.unique())
    for metric, title in (("composite_loss", "Composite loss (pre-registered weights)"), ("unnecessary_stop_min", "Unnecessary virtual-stop minutes"),
                          ("f1", "Alert F1 vs hidden truth"), ("false_stop_proposals", "False STOP proposals"), ("plan_attainment", "Plan attainment"),
                          ("energy_kwh_per_good_part", "Energy per good part (kWh)")):
        if metric not in df:
            continue
        fig, ax = plt.subplots(figsize=(4.2, 3.0), dpi=200)
        g = df.groupby("arm")[metric]
        means, sds = g.mean().reindex(arms), g.std().reindex(arms)
        ax.bar(arms, means.to_numpy(float), yerr=sds.fillna(0).to_numpy(float), color=[PALETTE[a] for a in arms], width=0.6, capsize=3, linewidth=0)
        for i, v in enumerate(means.to_numpy(float)):
            ax.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=7, color="#0b0b0b")
        ax.set(title=title + " — SIMULATED", ylabel=metric, xlabel="arm (N none · A rules · B +ML · C +judgment · D +evidence · E +context)")
        fig.tight_layout()
        fig.savefig(out / f"{metric}_by_arm.png")
        plt.close(fig)
        made.append(f"{metric}_by_arm.png")
    # heatmap-free matrix: unnecessary stop minutes by fault family × arm (bars grouped)
    if "unnecessary_stop_min" in df:
        piv = df.pivot_table(index="family", columns="arm", values="unnecessary_stop_min", aggfunc="mean")
        fig, ax = plt.subplots(figsize=(6.5, 3.2), dpi=200)
        import numpy as np
        x = np.arange(len(piv.index))
        w = 0.8 / max(1, len(piv.columns))
        for i, a in enumerate(piv.columns):
            ax.bar(x + i * w - 0.4 + w / 2, piv[a].to_numpy(float), width=w, color=PALETTE[a], label=f"arm {a}", linewidth=0)
        ax.set(xticks=x, xticklabels=list(piv.index), ylabel="unnecessary stop min", title="Unnecessary stop minutes by fault family — SIMULATED")
        ax.legend(fontsize=7, frameon=False, ncol=5, loc="upper left")
        fig.tight_layout()
        fig.savefig(out / "unnecessary_stop_by_family.png")
        plt.close(fig)
        made.append("unnecessary_stop_by_family.png")
    return made


def _md(df: pd.DataFrame, fmt=".3f") -> str:
    if df is None or df.empty:
        return "_(no rows)_"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(("—" if pd.isna(v) else (format(v, fmt) if isinstance(v, float) else str(v))) for v in r) + " |")
    return "\n".join(lines)


def write_paper_tables(df, prim, ci, abl, manifest, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    note = ("All values are from SIMULATED runs (digital twin + fault injection); provider = "
            f"{manifest['judgment_provider_note']}. Source CSVs: metrics_primary.csv, metrics_by_fault.csv, paired_ci.csv, ablations.csv; "
            f"experiment manifest: manifest.json (n_runs={manifest['n_runs']}, seeds={manifest['seeds']}).")
    t3 = prim[["arm", "arm_label", "provider", "n_runs", "f1", "f1_sd", "harmful_false_continue", "false_stop_proposals", "unnecessary_review",
               "correct_action_rate", "unsafe_actions_allowed", "composite_loss", "composite_loss_sd"]] if "f1" in prim else prim
    t4 = prim[["arm", "n_runs", "plan_attainment", "unnecessary_stop_min", "unnecessary_stop_min_sd", "energy_kwh_per_good_part", "intervention_delay_s"]] if "plan_attainment" in prim else prim
    text = [f"# Paper table shells — experiment `{manifest['exp']}` ({manifest['created']})", "", note, "",
            "## Table 3 — Safety and decision performance per arm (mean over fault families × seeds)", "", _md(t3), "",
            "## Table 4 — Operational effects per arm", "", _md(t4), "",
            "## Table 5 — Ablations (paired bootstrap, 95% CI of the difference)", "", _md(abl), "",
            "## Paired contrasts vs arm E", "", _md(ci), ""]
    (out / "Table_3_5.md").write_text("\n".join(text), encoding="utf-8")
    res = ["# Results — draft statements generated from measured simulation outputs only", "", note, ""]
    g = df.groupby("arm")
    for arm in sorted(df.arm.unique()):
        m = g.get_group(arm)
        res.append(f"- Arm {arm} ({m.arm_label.iloc[0]}, provider {m.provider.iloc[0]}): composite loss {m.composite_loss.mean():.3f} ± {m.composite_loss.std():.3f}, "
                   f"unnecessary stop {m.unnecessary_stop_min.mean():.2f} min, false STOP proposals {m.false_stop_proposals.mean():.2f}, harmful false-continue "
                   f"{m.harmful_false_continue.mean():.2f}, alert F1 {m.f1.mean():.3f}, plan attainment {m.plan_attainment.mean():.3f}, "
                   f"unsafe actions allowed {int(m.unsafe_actions_allowed.sum())} (n={len(m)} runs).")
    if not ci.empty:
        res.append("")
        for _, r in ci[ci.metric.isin(["composite_loss", "unnecessary_stop_min"])].iterrows():
            if r.n_pairs:
                res.append(f"- Paired difference {r.contrast} in {r.metric}: {r.mean_diff:+.3f} [95% CI {r.ci_low:+.3f}, {r.ci_high:+.3f}] "
                           f"({'excludes' if r.excludes_zero else 'includes'} zero; n_pairs={r.n_pairs}).")
    res += ["", "Statements about TypeSafe Jev performance are valid only for runs with provider TYPESAFE_JEV; MOCK_JEV rows are software checks. "
            "Real-plant performance: TBD—NOT MEASURED."]
    (out / "Results_TBD.md").write_text("\n".join(res), encoding="utf-8")
