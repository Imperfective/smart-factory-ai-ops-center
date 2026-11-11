"""Fig. 3 of the article — feeder load during one shift of the confirmatory experiment with a reduced feeder limit: fixed
rules (R1, nameplate limit only) against the same rules inside the full architecture (R4r, live feeder headroom). The shift
is re-executed here with the released code through the same session runner that produced the stored experiments
(LiveSession: replayed camera, mock part judge, defect-image ratio 0.1), and the script checks that the re-executed run
reproduces the stored rows in data/results (confirmatory/runs.csv for R1, supplement/added_runs.csv for R4r) before it
draws. Both modes are rule-based, so no decision-model calls are made. The traces behind the figure (feeder telemetry,
line states, trips, policy decisions) are written with a manifest (git commit, run summaries, comparison with the stored
rows) to data/results/figures/fig3_feeder_trace/.

    .venv/bin/python scripts/make_fig3_feeder_trace.py [--seed 66315 --load medium]

The default shift was chosen among the confirmatory courses with a reduced-limit event as the simplest one (two random
events, one trip) in which the fixed rules start the backup line while the limit is reduced and trip the protection, and
the gated rules, facing the same event at the same moment, recover the main line alone. Journal figure rules: 174 mm wide, Arial >= 8 pt, no title inside the figure, readable in black and
white (line styles, not colour, carry the meaning), vector EPS/PDF + 600 dpi PNG → docs/v3/figures/fig3_feeder_trace.*"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIG = ROOT / "docs" / "v3" / "figures"
DATA = ROOT / "data" / "results" / "figures" / "fig3_feeder_trace"
STORED = {"R1": ROOT / "data" / "results" / "confirmatory" / "runs.csv", "R4r": ROOT / "data" / "results" / "supplement" / "added_runs.csv"}
ARMS = ("R1", "R4r")
LABEL = {"R1": "(a) R1: fixed rules, gate checks the 60-kW nameplate limit only",
         "R4r": "(b) R4r: the same rules inside the full architecture, gate checks the live feeder headroom"}
COMPARE = ("target", "good_end", "target_met", "overloads", "max_kw", "tripped_min", "energy_kwh", "cost_balanced")


def stored_row(arm: str, seed: int, load: str) -> dict | None:
    rows = list(csv.DictReader(open(STORED[arm], encoding="utf-8-sig")))
    if arm == "R1":
        hits = [r for r in rows if r["arm"] == arm and r["load"] == load and int(float(r["course_seed"])) == seed]
    else:
        hits = [r for r in rows if r["arm"] == arm and r["load"] == load and int(float(r["seed"])) == seed and r.get("experiment") == "ablation" and r.get("overrides") == "{}"]
    return hits[-1] if hits else None


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def simulate(seed: int, load: str) -> dict:
    """Re-execute both modes as the experiments did, extract the traces to CSV, compare with the stored rows, write the manifest."""
    from sfaic2.v3.live import LiveSession

    DATA.mkdir(parents=True, exist_ok=True)
    summary, traces, match = {}, {}, {}
    for arm in ARMS:
        ls = LiveSession("RND", load, seed, arm, "mock", camera_mode="replay", unit_judge="mock", defect_image_ratio=0.1, headless=True,
                         source="batch", autosave=False)
        ls.control("run_to_end")
        res, ops, truth = ls.result, ls.E["ops"], ls.E["truth"]
        summary[arm] = {k: res.get(k) for k in ("scenario", "load", "seed", "arm", "provider", "n_truth_events", *COMPARE)}
        el = ops.query("SELECT received_sim_s, load_kw, available_kw, nameplate_kw, overload FROM electrical_events ORDER BY received_sim_s")
        st = ops.query("SELECT received_sim_s, line_id, plc_state FROM equipment_events ORDER BY received_sim_s")
        dec = ops.query("SELECT simulation_time, decided_by, proposed_action, safety_gate_result, blocked_reason, final_action, cause_label "
                        "FROM policy_decisions ORDER BY simulation_time")
        trips = truth.query("SELECT simulation_time, reason FROM unsafe_events WHERE command = 'FEEDER_OVERLOAD' ORDER BY simulation_time")
        write_csv(DATA / f"{arm}_electrical.csv", el, ["received_sim_s", "load_kw", "available_kw", "nameplate_kw", "overload"])
        write_csv(DATA / f"{arm}_line_states.csv", st, ["received_sim_s", "line_id", "plc_state"])
        write_csv(DATA / f"{arm}_decisions.csv", dec, ["simulation_time", "decided_by", "proposed_action", "safety_gate_result", "blocked_reason", "final_action", "cause_label"])
        write_csv(DATA / f"{arm}_trips.csv", trips, ["simulation_time", "reason"])
        traces[arm] = dict(el=el, st=st, trips=trips)
        row = stored_row(arm, seed, load)
        if row is None:
            match[arm] = dict(stored_file=str(STORED[arm].relative_to(ROOT)), found=False)
        else:
            diffs = {k: (row[k], res.get(k)) for k in COMPARE if row.get(k) not in (None, "") and res.get(k) is not None and abs(float(row[k]) - float(res[k])) > 1e-6}
            match[arm] = dict(stored_file=str(STORED[arm].relative_to(ROOT)), found=True, identical=not diffs, differences=diffs,
                              compared=[k for k in COMPARE if row.get(k) not in (None, "")])
            if diffs:
                raise SystemExit(f"{arm}: re-executed run differs from the stored row: {diffs}")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    json.dump(dict(purpose="Fig. 3 of the article: one shift of the confirmatory experiment re-executed to show the live-headroom gate; "
                           "the re-executed runs reproduce the stored rows exactly (see stored_match)",
                   runner="sfaic2.v3.live.LiveSession(camera_mode='replay', unit_judge='mock', defect_image_ratio=0.1) — the same path as the stored batches",
                   scenario="RND", seed=seed, load=load, arms=list(ARMS), provider="mock (no decision-model calls; both arms are rule-based)",
                   created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), git_commit=commit, summary=summary, stored_match=match,
                   files={a: [f"{a}_electrical.csv", f"{a}_line_states.csv", f"{a}_decisions.csv", f"{a}_trips.csv"] for a in ARMS}),
              open(DATA / "manifest.json", "w"), indent=1)
    return traces


def draw(traces: dict, seed: int, load: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "Arial", "font.size": 8, "ps.fonttype": 42, "pdf.fonttype": 42})
    fig, axes = plt.subplots(2, 1, figsize=(174 / 25.4, 80 / 25.4), sharex=True)
    for ax, arm in zip(axes, ARMS):
        el, st, trips = traces[arm]["el"], traces[arm]["st"], traces[arm]["trips"]
        t = [r["received_sim_s"] / 3600 for r in el]
        load_kw = [r["load_kw"] for r in el]
        avail = [r["available_kw"] for r in el]
        name = [r["nameplate_kw"] for r in el]
        lo = [x for x, a in zip(t, avail) if a < name[0] - 1e-6]          # shaded while the available power is below the nameplate
        if lo:
            ax.axvspan(min(lo), max(lo), color="0.88", lw=0, zorder=0)
        ax.step(t, name, where="post", color="0.45", ls=":", lw=0.9, label="nameplate limit")
        ax.step(t, avail, where="post", color="0.2", ls="--", lw=1.0, label="available power (true)")
        ax.step(t, load_kw, where="post", color="black", lw=1.1, label="feeder load")
        sub_on = [r["received_sim_s"] / 3600 for r in st if r["line_id"] == "SUB" and r["plc_state"] in ("RUNNING", "STARTING", "BLOCKED")]
        if sub_on:
            ax.plot(sub_on, [3.0] * len(sub_on), ls="none", marker="s", ms=1.6, color="0.3", label="backup line on")
        for i, r in enumerate(trips):
            ax.plot(r["simulation_time"] / 3600, max(load_kw) + 2, marker="v", color="black", ms=5, ls="none", label="protection trip" if i == 0 else None)
        ax.set_ylim(0, 66)
        ax.set_yticks([0, 20, 40, 60])
        ax.set_ylabel("feeder load (kW)")
        ax.text(0.0, 1.03, LABEL[arm], transform=ax.transAxes, va="bottom", ha="left", fontsize=8)
        ax.grid(axis="y", color="0.9", lw=0.5)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[1].set_xlabel("time in shift (h)")
    axes[1].set_xlim(0, 8)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=5, frameon=False, fontsize=7, handlelength=2.2, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(h_pad=1.2, rect=(0, 0.06, 1, 1))
    FIG.mkdir(parents=True, exist_ok=True)
    for ext in ("eps", "pdf"):
        fig.savefig(FIG / f"fig3_feeder_trace.{ext}", format=ext)
    fig.savefig(FIG / "fig3_feeder_trace.png", dpi=600)
    print(f"wrote docs/v3/figures/fig3_feeder_trace.{{eps,pdf,png}} (course {seed}, load {load})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=66315)
    ap.add_argument("--load", default="medium")
    a = ap.parse_args()
    traces = simulate(a.seed, a.load)
    draw(traces, a.seed, a.load)
    m = json.load(open(DATA / "manifest.json"))
    print(json.dumps(dict(summary=m["summary"], stored_match=m["stored_match"]), indent=1))


if __name__ == "__main__":
    main()
