"""Pre-registered per-run metrics (docs/13). Truth comes from the simulator's hidden records; decisions from the
engine. Denominators are explicit; undefined ratios are None (never 0)."""
from __future__ import annotations

from ..contracts.enums import Recommendation
from ..decision.engine import EngineResult
from ..simulator.production import RunOutput

ALERT = {Recommendation.REQUEST_RECHECK, Recommendation.REQUEST_OPERATOR_REVIEW, Recommendation.REQUEST_SIMULATED_STOP}


def _truth_at(run: RunOutput, asset: str, t: float, tol: float = 15.0):
    cands = [x for x in run.truth if x.machine_id == asset and abs(x.sim_time_s - t) <= tol]
    return min(cands, key=lambda x: abs(x.sim_time_s - t)) if cands else None


def run_metrics(run: RunOutput, eng: EngineResult, fault_start_s: float | None, fault_asset: str | None) -> dict:
    tp = fp = fn = tn = 0
    harmful_false_continue = unnecessary_review = false_stop_proposals = 0
    correct_action = n_action_eval = 0
    first_alert_s = None
    for d in eng.decisions:
        tr = _truth_at(run, d.asset_id, d.sim_time_s)
        if tr is None:
            continue
        anomalous = tr.truth_classes != ["NONE"]
        alert = d.recommendation in ALERT
        tp += anomalous and alert
        fp += (not anomalous) and alert
        fn += anomalous and (not alert)
        tn += (not anomalous) and (not alert)
        if tr.harmful_stop_warranted and d.recommendation == Recommendation.CONTINUE_MONITORING:
            harmful_false_continue += 1
        if (not anomalous) and d.recommendation == Recommendation.REQUEST_OPERATOR_REVIEW:
            unnecessary_review += 1
        if d.recommendation == Recommendation.REQUEST_SIMULATED_STOP and not tr.harmful_stop_warranted:
            false_stop_proposals += 1
        if anomalous:
            n_action_eval += 1
            correct_action += d.recommendation.value == tr.expected_safe_action
        if fault_start_s is not None and fault_asset and d.asset_id == fault_asset and alert and d.sim_time_s >= fault_start_s and first_alert_s is None:
            first_alert_s = d.sim_time_s
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * prec * rec / (prec + rec) if prec is not None and rec is not None and (prec + rec) else None
    k = run.kpis
    # unnecessary stop minutes: stopped time on machines whose truth did not warrant a stop
    unnecessary_stop_min = 0.0
    stop_warranted_assets = {t.machine_id for t in run.truth if t.harmful_stop_warranted}
    for asset, pm in k["per_machine"].items():
        if asset not in stop_warranted_assets:
            unnecessary_stop_min += pm["stopped_s"] / 60.0
    ack = [c.ack_sim_s - c.proposed_sim_s for c in eng.commands if c.ack_sim_s is not None]
    return dict(
        n_decisions=len(eng.decisions), tp=tp, fp=fp, fn=fn, tn=tn, precision=prec, recall=rec, f1=f1,
        harmful_false_continue=harmful_false_continue, unnecessary_review=unnecessary_review, false_stop_proposals=false_stop_proposals,
        correct_action_rate=(correct_action / n_action_eval) if n_action_eval else None,
        stop_proposals=len(eng.commands), stops_executed=eng.gate_stats["executed"], gate_rejected=eng.gate_stats["rejected_invalid"],
        unsafe_actions_allowed=eng.gate_stats["unsafe_allowed"], unnecessary_stop_min=round(unnecessary_stop_min, 2),
        intervention_delay_s=(first_alert_s - fault_start_s) if (first_alert_s is not None and fault_start_s is not None) else None,
        mean_ack_latency_s=(sum(ack) / len(ack)) if ack else None,
        good_count=k["good_count"], plan_attainment=k["plan_attainment"], throughput_good_per_h=k["throughput_good_per_h"],
        energy_kwh_per_good_part=k["energy_kwh_per_good_part"], total_energy_kwh=k["total_energy_kwh"],
        min_bus_voltage_pu=k["min_bus_voltage_pu"], max_transformer_loading_pct=k["max_transformer_loading_pct"],
        jev_calls=(eng.jev_usage or {}).get("calls"), jev_cost_usd=(eng.jev_usage or {}).get("estimated_cost_usd"), jev_fallbacks=eng.fallbacks,
    )


def composite_loss(m: dict, w: dict) -> float:
    """Pre-registered multi-objective loss (docs/12): λ_FN·C_FN + λ_FS·T_stop + λ_D·delay + λ_E·kWh + λ_Q·unmet. Weights fixed before runs."""
    unmet = max(0.0, 1.0 - (m["plan_attainment"] or 0.0))
    return (w["fn"] * m["harmful_false_continue"] + w["fs"] * m["unnecessary_stop_min"] + w["delay"] * ((m["intervention_delay_s"] or 0) / 60.0)
            + w["energy"] * (m["energy_kwh_per_good_part"] or 0.0) + w["plan"] * unmet)


def live_accuracy(truth, decisions) -> dict:
    """Judgment accuracy against hidden simulator truth for a (possibly running) session, per provider/arm.
    alert = any non-CONTINUE recommendation; truth anomalous = any truth class other than NONE (±15 s match)."""
    from collections import defaultdict
    idx = defaultdict(list)
    for x in truth:
        idx[x.machine_id].append(x)
    tp = fp = fn = tn = 0; correct = n_eval = 0; false_stop = harmful_cont = 0
    by_cause = defaultdict(lambda: dict(n=0, correct=0))
    for d in decisions:
        cands = [x for x in idx.get(d.asset_id, []) if abs(x.sim_time_s - d.sim_time_s) <= 15]
        if not cands:
            continue
        tr = min(cands, key=lambda x: abs(x.sim_time_s - d.sim_time_s))
        anomalous = tr.truth_classes != ["NONE"]; alert = d.recommendation.value != "CONTINUE_MONITORING"
        tp += anomalous and alert; fp += (not anomalous) and alert; fn += anomalous and not alert; tn += (not anomalous) and not alert
        if anomalous:
            n_eval += 1; ok = d.recommendation.value == tr.expected_safe_action; correct += ok
            for c in tr.truth_classes:
                by_cause[c]["n"] += 1; by_cause[c]["correct"] += ok
        if d.recommendation.value == "REQUEST_SIMULATED_STOP" and not tr.harmful_stop_warranted:
            false_stop += 1
        if tr.harmful_stop_warranted and d.recommendation.value == "CONTINUE_MONITORING":
            harmful_cont += 1
    prec = tp / (tp + fp) if (tp + fp) else None; rec = tp / (tp + fn) if (tp + fn) else None
    return dict(n_decisions=len(decisions), tp=tp, fp=fp, fn=fn, tn=tn, precision=prec, recall=rec,
                f1=(2 * prec * rec / (prec + rec)) if prec and rec else None, correct_action_rate=(correct / n_eval) if n_eval else None,
                n_anomalous_ticks=n_eval, false_stop_proposals=false_stop, harmful_false_continue=harmful_cont,
                by_truth_class={k: dict(v, rate=(v["correct"] / v["n"]) if v["n"] else None) for k, v in by_cause.items()})
