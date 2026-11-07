"""Copy the stored research results into data/results/ (tracked in git, unlike results/).

    python scripts/export_results_data.py      (after make v3-report and python -m sfaic2.v3.supplement report)

Only tabular outcomes, statistics and provenance are exported — no API keys, no raw model responses, no images.
"""
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "data" / "results"
FILES = {   # source (relative to the repo) -> destination name, what it backs
    "results/v3/live_runs/runs.csv": ("confirmatory/runs.csv", "every stored random-scenario shift (real Jev, rules, planner, mock) with metrics and provenance"),
    "results/v3/live_runs/run_events.csv": ("confirmatory/run_events.csv", "each random event of each shift: detected, delays, first automatic decision"),
    "results/v3/live_runs/paired.csv": ("confirmatory/paired.csv", "paired contrasts with bootstrap CI, McNemar/bootstrap p, Holm p, pairs needed for 80 % power"),
    "results/v3/live_runs/stats_by_group.csv": ("confirmatory/stats_by_group.csv", "mean/sd/95 % CI per mode × provider × part judge × load"),
    "results/v3/supplement/decision_model_use.csv": ("supplement/decision_model_use.csv", "see the supplement report"),
    "results/v3/supplement/cause_agreement_by_mode.csv": ("supplement/cause_agreement_by_mode.csv", "see the supplement report"),
    "results/v3/supplement/cause_agreement_by_cause.csv": ("supplement/cause_agreement_by_cause.csv", "see the supplement report"),
    "results/v3/supplement/transport_by_mode.csv": ("supplement/transport_by_mode.csv", "see the supplement report"),
    "results/v3/supplement/detection_by_disruption.csv": ("supplement/detection_by_disruption.csv", "see the supplement report"),
    "results/v3/supplement/plan_gain_by_disruption.csv": ("supplement/plan_gain_by_disruption.csv", "see the supplement report"),
    "results/v3/supplement/sensitivity_contrasts.csv": ("supplement/sensitivity_contrasts.csv", "see the supplement report"),
    "results/v3/supplement/sensitivity_means.csv": ("supplement/sensitivity_means.csv", "see the supplement report"),
    "results/v3/supplement/summary.json": ("supplement/summary.json", "all supplementary results incl. test–retest"),
    "results/v3/real_jev/unit_study/parts.csv": ("two_stage_inspection/parts.csv", "4,000 parts: YOLO output, program posterior, real-Jev and rule dispositions"),
    "results/v3/real_jev/unit_study/summary.json": ("two_stage_inspection/summary.json", "see the supplement report"),
    "results/v3/real_jev/pilot/episodes.csv": ("pilot/episodes.csv", "144 pilot shifts"),
    "results/v3/real_jev/pilot/run_summary.json": ("pilot/run_summary.json", "pilot run summary"),
    "results/v3/real_jev/pilot/manifest.json": ("pilot/manifest.json", "pilot provenance (commit, packages, model)"),
    "results/vision/replay_raw.jsonl": ("vision/replay_raw.jsonl", "verbatim YOLO11n output on the 129 held-out MVTec AD test images, replayed as the camera input (derived from MVTec AD, CC BY-NC-SA 4.0; no images)"),
    "results/vision/summary.json": ("vision/summary.json", "detector evaluation on the held-out images (image-level precision/recall, latency, weights hash)"),
}


def sensitivity_runs_csv(dst: Path) -> int:
    """Flatten results/v3/supplement/sensitivity_runs.jsonl (one row per shift; request texts are not exported)."""
    src = ROOT / "results/v3/supplement/sensitivity_runs.jsonl"
    rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    cols = ["param", "value"] + list(rows[0]["row"].keys())
    with open(dst, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"param": r["param"], "value": r["value"], **r["row"]})
    return len(rows)


def retest_csv(dst: Path) -> int:
    """Flatten results/v3/supplement/retest.jsonl: one row per repeated call (request texts are not exported)."""
    rows = [json.loads(x) for x in (ROOT / "results/v3/supplement/retest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    cols = ["request_hash", "repeat", "arm", "repeat_fault_p", "allowed_actions", "latency_ms", "model", "error", "original_action", "repeat_action", "same_action",
            "original_cause", "repeat_cause", "original_needs_person_p", "repeat_needs_person_p"]
    with open(dst, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            o = r["orig"]
            w.writerow([r["hash"], r["rep"], r["arm"], r["value"], r["n_allowed"], r["latency_ms"], r.get("model"), r.get("error"), o.get("action"), r.get("action"),
                        int(r.get("action") == o.get("action")) if not r.get("error") else None, o.get("cause"), r.get("cause"), o.get("needs_operator_p"), r.get("needs_operator_p")])
    return len(rows)


def main() -> None:
    missing = [s for s in FILES if not (ROOT / s).exists()]
    if missing:
        sys.exit(f"missing: {missing}")
    if DST.exists():
        shutil.rmtree(DST)
    lines = []
    for src, (name, what) in FILES.items():
        out = DST / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / src, out)
        lines.append((name, what))
    n = sensitivity_runs_csv(DST / "supplement" / "sensitivity_runs.csv")
    lines.append(("supplement/sensitivity_runs.csv", f"{n} shifts of the sensitivity experiment (repeat-fault probability 0.0 / 0.3)"))
    n = retest_csv(DST / "supplement" / "retest.csv")
    lines.append(("supplement/retest.csv", f"{n} repeated calls of the test-retest experiment: original vs repeated action, cause and needs-a-person probability (request texts not exported)"))
    for name, _ in lines:                                   # nothing secret may leave results/
        txt = (DST / name).read_text(encoding="utf-8-sig", errors="ignore")
        assert "apikey_" not in txt and "Bearer " not in txt, name
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    sha = {name: hashlib.sha256((DST / name).read_bytes()).hexdigest()[:16] for name, _ in lines}
    md = ["# Research data (generated by `scripts/export_results_data.py` — do not edit by hand)", "",
          f"Exported from `results/` at commit {commit}. The result reports in `docs/v3/RESULTS_*.md` are computed from these files "
          "(simulation outputs; no API keys, raw model responses or images). Regenerate the derived tables with `make v3-report` and "
          "`python -m sfaic2.v3.supplement report`. Mode codes R0–R5 and metric names are defined in `sfaic2/v3/live_results.py` and `docs/v3/MODEL_ASSUMPTIONS.md`.", "",
          "Rows with `decision_provider = MOCK_JEV` in `confirmatory/runs.csv` are a software check and are not evidence about the decision model; "
          "rows with `data_quality` other than `ok` are excluded from all statistics. Camera input derives from MVTec AD (CC BY-NC-SA 4.0); image ids are opaque hashes.", "",
          "| file | content | sha256 (first 16) |", "|---|---|---|"]
    md += [f"| `{name}` | {what} | `{sha[name]}` |" for name, what in lines]
    (DST / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"exported {len(lines)} files to {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
