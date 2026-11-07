"""Two-stage per-part inspection pipeline: YOLO11n (stage 1, perception) → TypeSafe Jev (stage 2, final disposition).

The study measures what the automated pipeline delivers on a plan's worth of parts; it is not a YOLO-vs-Jev contest.

For each run (seed, defective-image share p) the program produces `n_parts` parts (default 400 = a plan quantity). Each
part is good or defective at random (probability p); its image is drawn at random from the held-out public images of
that condition (the same MVTec AD images and the same trained YOLO11n used before — no new YOLO training). YOLO11n
analyses every image (live on Apple MPS by default) and gives the stage-1 verdict (defect box or not). Jev receives the
stage-1 result plus program-computed numbers and makes the final disposition (accept / reject / re-inspect by a person).
For transparency the CSV also records two fixed reference rules on the same evidence (`YOLO_RULE`, `COST_RULE`); they are
reported only as context, the pipeline result is the JEV columns.

Outcomes use the hidden truth only for scoring: escaped defects (accepted defective parts), scrapped good parts,
re-inspections (operator check reveals the truth), and the expected-cost total (25 / 10 / 2 per event).

    python -m sfaic2.v3.unit_study --provider typesafe --seeds 0-4 --ratios 0.1,0.3 --parts 400
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .plant import ReplayPool
from .snapshot import _vision_recall
from .unit_judge import COST_DEFECT_SHIPPED, COST_REINSPECT, COST_SCRAP_GOOD, UnitJudge, unit_state

NOMINAL_DEFECT = 0.03


def _rng(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def make_parts(seed: int, ratio: float, n: int, live: bool) -> list[dict]:
    pool = ReplayPool()
    rng = random.Random(f"unit-study|{seed}|{ratio}")
    yolo = None
    if live:
        from .yolo_live import LiveYolo
        yolo = LiveYolo.get()
    recall = _vision_recall()
    hist: list[bool] = []
    parts = []
    for i in range(n):
        defective = rng.random() < ratio
        rec = rng.choice(pool.bad if defective else pool.good)
        raw = yolo.infer(rec["image"]) if yolo else dict(rec["raw_json"])
        raw["path"] = f"img:{rec['frame_hash'][:16]}"
        dets = raw.get("detections") or []
        recent = hist[-40:]
        rr = sum(recent) / len(recent) if recent else 0.0
        prior = None if len(recent) >= 20 else NOMINAL_DEFECT
        st = unit_state(rec.get("category", "part"), raw, rr, rr > 0.12, recall, 1.0, prior=prior)
        hist.append(bool(dets))
        parts.append(dict(idx=i, image_id=rec["frame_hash"][:16], truth_defective=defective, defect_type=(rec.get("eval_truth") or {}).get("defect_type"),
                          yolo_boxes=len(dets), yolo_max_conf=max((d["confidence"] for d in dets), default=0.0), yolo_ms=(raw.get("speed_ms") or {}).get("inference"),
                          camera="live" if yolo else "replay", state=st))
    return parts


def outcome(disp: str, defective: bool) -> dict:
    if disp == "ACCEPT":
        return dict(escaped=int(defective), scrapped_good=0, reinspected=0, cost=COST_DEFECT_SHIPPED * defective, final_correct=int(not defective))
    if disp == "REJECT":
        return dict(escaped=0, scrapped_good=int(not defective), reinspected=0, cost=COST_SCRAP_GOOD * (not defective), final_correct=int(defective))
    return dict(escaped=0, scrapped_good=0, reinspected=1, cost=COST_REINSPECT, final_correct=1)


def run(provider: str, seeds: list[int], ratios: list[float], n: int, live: bool, threads: int, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    judge = UnitJudge(provider)
    rows, t0 = [], time.time()
    for ratio in ratios:
        for seed in seeds:
            parts = make_parts(seed, ratio, n, live)
            with ThreadPoolExecutor(max(1, threads)) as ex:
                verdicts = list(ex.map(lambda p: judge(p["state"]), parts))
            for p, v in zip(parts, verdicts):
                ec = p["state"]["program_computed"]["expected_cost"]
                pol = dict(YOLO_RULE="REJECT" if p["yolo_boxes"] else "ACCEPT", COST_RULE=min(ec, key=ec.get), JEV=v["disposition"])
                row = dict(seed=seed, ratio=ratio, idx=p["idx"], image_id=p["image_id"], camera=p["camera"], truth_defective=int(p["truth_defective"]), defect_type=p["defect_type"],
                           yolo_boxes=p["yolo_boxes"], yolo_max_conf=round(p["yolo_max_conf"], 4), yolo_ms=p["yolo_ms"],
                           p_defect=p["state"]["program_computed"]["p_defect_given_evidence"], exp_cost=json.dumps(ec), prior=p["state"]["line_context"]["prior_defect_rate"],
                           jev_provider=v["provider"], jev_model=v.get("model"), jev_probs=json.dumps(v.get("probabilities")), jev_latency_ms=v.get("latency_ms"),
                           jev_cached=int(bool(v.get("cached"))), jev_error=v.get("error"))
                for k, d in pol.items():
                    o = outcome(d, p["truth_defective"])
                    row[f"{k}_disp"] = d
                    for m, val in o.items():
                        row[f"{k}_{m}"] = val
                rows.append(row)
            print(f"ratio {ratio} seed {seed}: {len(parts)} parts, {time.time() - t0:.0f}s", flush=True)
    with open(out / "parts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    summary = summarise(rows)
    summary.update(provider=judge.provider if provider != "mock" else "MOCK_JEV", parts=len(rows), seeds=seeds, ratios=ratios, n_per_run=n, camera="live" if live else "replay",
                   jev_calls=judge.calls, jev_cached=judge.cached, jev_errors=judge.errors, wall_s=round(time.time() - t0, 1),
                   jev_latency_p50_ms=sorted(judge.latencies)[len(judge.latencies) // 2] if judge.latencies else None,
                   cost_basis=dict(defect_shipped=COST_DEFECT_SHIPPED, good_scrapped=COST_SCRAP_GOOD, reinspection=COST_REINSPECT),
                   note="truth labels are used only for scoring; the judges see the YOLO result, program-computed defect probability and expected costs")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    figure(rows, out, summary)
    return summary


def summarise(rows: list[dict]) -> dict:
    """Pipeline metrics per defective-image share: stage-1 verdicts, final dispositions, automation, people's load,
    escapes, scrapped good parts, final accuracy, loss; and how the final stage treated each stage-1 verdict."""
    import numpy as np
    out = []
    flows = []
    for ratio in sorted({r["ratio"] for r in rows}):
        g = [r for r in rows if r["ratio"] == ratio]
        n = len(g)
        y_def = [r for r in g if r["yolo_boxes"] > 0]
        y_ok = [r for r in g if r["yolo_boxes"] == 0]
        disp = Counter(r["JEV_disp"] for r in g)
        auto = disp["ACCEPT"] + disp["REJECT"]
        per_run = defaultdict(float)
        for r in g:
            per_run[r["seed"]] += r["JEV_cost"]
        costs = np.array(list(per_run.values()))
        rng = np.random.default_rng(2026)
        boots = [rng.choice(costs, len(costs)).mean() for _ in range(2000)] if len(costs) > 1 else [costs.mean()]
        out.append(dict(ratio=ratio, parts=n, defective=sum(r["truth_defective"] for r in g),
                        stage1_flagged=len(y_def), stage1_accuracy=round(sum((r["yolo_boxes"] > 0) == bool(r["truth_defective"]) for r in g) / n, 4),
                        final_accept=disp["ACCEPT"], final_reject=disp["REJECT"], final_reinspect=disp["REINSPECT"], automation_rate=round(auto / n, 4),
                        escaped=sum(r["JEV_escaped"] for r in g), scrapped_good=sum(r["JEV_scrapped_good"] for r in g),
                        reinspect_found_defects=sum(1 for r in g if r["JEV_disp"] == "REINSPECT" and r["truth_defective"]),
                        final_accuracy=round(sum(r["JEV_final_correct"] for r in g) / n, 4), loss_per_100=round(100 * sum(r["JEV_cost"] for r in g) / n, 2),
                        loss_per_run=round(float(costs.mean()), 1), loss_per_run_ci=[round(float(np.quantile(boots, 0.025)), 1), round(float(np.quantile(boots, 0.975)), 1)],
                        hidden_defects_after_stage1=sum(r["truth_defective"] for r in y_ok),
                        hidden_defects_caught_by_stage2=sum(1 for r in y_ok if r["truth_defective"] and r["JEV_disp"] != "ACCEPT")))
        for s1, grp in (("DEFECT_BOX", y_def), ("NO_BOX", y_ok)):
            c = Counter(r["JEV_disp"] for r in grp)
            for d, k in c.items():
                flows.append(dict(ratio=ratio, stage1=s1, final=d, parts=k, truly_defective=sum(1 for r in grp if r["JEV_disp"] == d and r["truth_defective"])))
    refs = {}
    for ratio in sorted({r["ratio"] for r in rows}):
        g = [r for r in rows if r["ratio"] == ratio]
        refs[str(ratio)] = {p: round(100 * sum(r[f"{p}_cost"] for r in g) / len(g), 2) for p in ("YOLO_RULE", "COST_RULE", "JEV")}
    return dict(pipeline=out, flows=flows, reference_rules_loss_per_100=refs,
                reference_note="fixed rules on the same evidence, context only (not the subject of the study)")


def figure(rows, out: Path, summary: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    P = summary["pipeline"]
    f, ax = plt.subplots(1, 2, figsize=(11, 3.6))
    cols = {"ACCEPT": "#16a34a", "REJECT": "#dc2626", "REINSPECT": "#f59e0b"}
    labels = []
    xi = 0
    for p in P:
        for s1 in ("DEFECT_BOX", "NO_BOX"):
            bottom = 0
            for d in ("ACCEPT", "REJECT", "REINSPECT"):
                v = next((x["parts"] for x in summary["flows"] if x["ratio"] == p["ratio"] and x["stage1"] == s1 and x["final"] == d), 0)
                ax[0].bar(xi, v, bottom=bottom, color=cols[d], label=d if xi == 0 else None)
                bottom += v
            labels.append(f"{p['ratio']:.0%}\n{'YOLO: defect' if s1 == 'DEFECT_BOX' else 'YOLO: no box'}")
            xi += 1
    ax[0].set_xticks(range(xi), labels, fontsize=8); ax[0].set_title("Stage 2 (Jev) final disposition by stage-1 (YOLO) result", fontsize=10); ax[0].legend(fontsize=8)
    x = range(len(P))
    ax[1].bar([i - 0.2 for i in x], [p["automation_rate"] for p in P], 0.4, color="#0b3d63", label="automation rate")
    ax[1].bar([i + 0.2 for i in x], [p["final_accuracy"] for p in P], 0.4, color="#2f7f86", label="final accuracy")
    ax[1].set_xticks(list(x), [f"defective share {p['ratio']:.0%}" for p in P]); ax[1].set_ylim(0, 1.05); ax[1].legend(fontsize=8); ax[1].set_title("Pipeline outcome", fontsize=10)
    f.text(0.01, 0.97, f"{summary['provider']} · YOLO11n ({summary['camera']}) → Jev · {summary['parts']} parts", fontsize=8, color="#264" if summary["provider"] != "MOCK_JEV" else "#a33")
    f.tight_layout(); f.savefig(out / "unit_study.png", dpi=160); plt.close(f)


def resummarise(out: Path) -> dict:
    """Recompute summary + figure from an existing parts.csv (no new model calls)."""
    rows = list(csv.DictReader(open(out / "parts.csv", encoding="utf-8")))
    for r in rows:
        for k, v in list(r.items()):
            if k in ("seed", "idx", "truth_defective", "yolo_boxes") or k.endswith(("_escaped", "_scrapped_good", "_reinspected", "_final_correct")):
                r[k] = int(float(v))
            elif k == "ratio" or k in ("YOLO_RULE_cost", "COST_RULE_cost", "JEV_cost"):
                r[k] = float(v)
    old = json.loads((out / "summary.json").read_text())
    s = summarise(rows)
    keep = {k: old[k] for k in ("provider", "parts", "seeds", "ratios", "n_per_run", "camera", "jev_calls", "jev_cached", "jev_errors", "wall_s", "jev_latency_p50_ms", "cost_basis", "note") if k in old}
    s.update(keep)
    (out / "summary.json").write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")
    figure(rows, out, s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "typesafe"], default="mock")
    ap.add_argument("--seeds", default="0-4")
    ap.add_argument("--ratios", default="0.1,0.3")
    ap.add_argument("--parts", type=int, default=400)
    ap.add_argument("--camera", choices=["live", "replay"], default="live")
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args()
    if a.provider == "typesafe":
        from dotenv import load_dotenv
        load_dotenv(".env")
        from .jev import TypeSafeV3Client
        ok, why = TypeSafeV3Client.opted_in()
        if not ok:
            raise SystemExit(why)
    out = Path("results/v3") / ("real_jev" if a.provider == "typesafe" else "mock") / "unit_study"
    s = run(a.provider, _rng(a.seeds), [float(x) for x in a.ratios.split(",")], a.parts, a.camera == "live", a.threads, out)
    print(json.dumps({k: s[k] for k in ("provider", "parts", "jev_calls", "jev_cached", "jev_errors", "wall_s")}, default=str))
    for t in s["pipeline"]:
        print(t)


if __name__ == "__main__":
    main()
