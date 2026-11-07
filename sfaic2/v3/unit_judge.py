"""Per-product inspection judgment: YOLO evidence for ONE part → TypeSafe Jev → disposition.

For every finished part the camera image (a held-out MVTec AD image of the part's hidden condition, re-used at random)
is analysed by YOLO11n; the detection summary (no file name, no label) is sent to Jev, which chooses the part's
disposition: ACCEPT (to the pallet), REJECT (scrap) or REINSPECT (hold for a manual re-inspection, resolved after
120 s). Jev does not see the image itself (Jev is text-only) — it judges the detector's structured output together
with the detector's measured precision/recall and the line context. MockUnitJudge is a labelled deterministic rule."""
from __future__ import annotations

import time

from ..adapters.typesafe_jev import JevBudget
from .jev import TypeSafeV3Client, request_hash

QUESTION_VERSION = "qs_unit_disposition_v1_2026-10-17"
DISPOSITIONS = {
    "ACCEPT": "The camera evidence shows no defect; pass the part to the pallet",
    "REJECT": "The camera evidence shows a defect with sufficient confidence; scrap the part",
    "REINSPECT": "The evidence is weak or ambiguous; hold the part for a manual re-inspection",
}


def _b(v, step):
    return None if v is None else round(round(v / step) * step, 3)


# cost weights (same assumed monetary units as the episode decision-cost "balanced" profile)
COST_DEFECT_SHIPPED = 25.0      # a defective part reaching the customer
COST_SCRAP_GOOD = 10.0          # a good part scrapped (one more unit short of the plan)
COST_REINSPECT = 2.0            # 2 min of operator re-inspection at 1 unit/min
GOOD_FALSE_ALARM_RATE = 1.0 / 38.0   # measured: 0 false alarms on 36 held-out good images, Laplace-smoothed


def posterior_defect(prior: float, detected: bool, recall: float, fpr: float = GOOD_FALSE_ALARM_RATE) -> float:
    """Program-computed P(defect | detector result) from the measured recall / false-alarm rate (Bayes)."""
    if detected:
        num, den = prior * recall, prior * recall + (1 - prior) * fpr
    else:
        num, den = prior * (1 - recall), prior * (1 - recall) + (1 - prior) * (1 - fpr)
    return num / den if den > 0 else prior


def unit_state(part_type: str, raw: dict, line_reject_rate: float, quality_incident_open: bool, recall: float, precision: float, prior: float | None = None) -> dict:
    dets = raw.get("detections") or []
    h, w = (raw.get("orig_shape") or [1, 1])[:2]
    boxes = []
    for d in sorted(dets, key=lambda d: -d["confidence"])[:3]:
        b = d["box"]
        boxes.append(dict(confidence=_b(d["confidence"], 0.05), area_fraction=_b((b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) / max(1.0, w * h), 0.005)))
    if prior is None:   # prior defect rate of this line, estimated from the observed reject rate and the detector recall
        prior = min(0.9, max(0.005, line_reject_rate / max(0.05, recall)))
    post = posterior_defect(prior, bool(dets), recall)
    exp_cost = dict(ACCEPT=round(post * COST_DEFECT_SHIPPED, 2), REJECT=round((1 - post) * COST_SCRAP_GOOD, 2), REINSPECT=COST_REINSPECT)
    return dict(part_type=part_type, detector=dict(model="YOLO11n fine-tuned on public industrial part images", confidence_threshold=raw.get("conf", 0.25),
                                                   measured_recall=_b(recall, 0.01), measured_false_alarm_rate=_b(GOOD_FALSE_ALARM_RATE, 0.005)),
                detections=dict(count=len(dets), max_confidence=_b(max((d["confidence"] for d in dets), default=0.0), 0.05), top_boxes=boxes),
                line_context=dict(recent_reject_rate=_b(line_reject_rate, 0.05), quality_incident_open=quality_incident_open, prior_defect_rate=_b(prior, 0.01)),
                program_computed=dict(p_defect_given_evidence=_b(post, 0.01), expected_cost=exp_cost,
                                      cost_basis=dict(defect_shipped=COST_DEFECT_SHIPPED, good_part_scrapped=COST_SCRAP_GOOD, reinspection=COST_REINSPECT)),
                allowed_dispositions=list(DISPOSITIONS))


def questions(st: dict | None = None) -> dict:
    crit = dict(DISPOSITIONS)
    if st and st.get("program_computed"):
        ec = st["program_computed"]["expected_cost"]
        crit = {k: f"{v}. Program-computed expected cost {ec[k]}" for k, v in DISPOSITIONS.items()}
    return {"disposition": {"type": "choice", "instructions": "Decide the disposition of this single manufactured part using only the camera detector's structured "
                            "result, the program-computed defect probability and expected costs, and the inspection context. Do not recompute measurements "
                            "and do not invent evidence.", "criteria": crit},
            "defect_present": {"type": "noul", "instructions": "Is a real surface or assembly defect present on this part?"}}


class UnitJudge:
    def __init__(self, provider: str = "mock", budget: JevBudget | None = None):
        self.provider = "MOCK_JEV" if provider == "mock" else "TYPESAFE_JEV"
        self.client = None if provider == "mock" else TypeSafeV3Client(budget=budget)
        self.calls = self.cached = self.errors = 0
        self.latencies: list[float] = []

    def __call__(self, st: dict) -> dict:
        t0 = time.perf_counter()
        if self.client is None:
            ec = st["program_computed"]["expected_cost"]
            disp = min(ec, key=ec.get)        # mock: lowest program-computed expected cost
            n = st["detections"]["count"]
            probs = {k: (0.8 if k == disp else 0.1) for k in DISPOSITIONS}
            return dict(provider=self.provider, disposition=disp, probabilities=probs, defect_present_p=0.85 if n else 0.15, latency_ms=0.0, model="mock-rule",
                        request_hash=None, cached=False, error=None)
        req = {"model": self.client.model, "state": st, "questions": questions(st)}
        h = request_hash(req)
        try:
            body = self.client.decide(req)
            lat = (time.perf_counter() - t0) * 1000
            a = body.get("answers") or {}
            d, p = a.get("disposition") or {}, a.get("defect_present") or {}
            if d.get("type") != "choice" or d.get("choice") not in DISPOSITIONS or p.get("type") != "noul":
                raise ValueError(f"malformed answer: {d.get('choice')}")
            cached = bool(body.get("_cached"))
            self.calls += 0 if cached else 1
            self.cached += int(cached)
            if not cached:
                self.latencies.append(lat)
            return dict(provider=self.provider, disposition=d["choice"], probabilities=d.get("probabilities"), confidence=d.get("confidence"), defect_present_p=float(p["noul"]),
                        latency_ms=round(lat, 1), model=body.get("model"), usage=body.get("usage"), request_hash=h, cached=cached, error=None)
        except Exception as exc:  # noqa: BLE001 — fall back to the detector rule, labelled
            self.errors += 1
            n = st["detections"]["count"]
            return dict(provider="FALLBACK_YOLO_RULE", disposition="REJECT" if n else "ACCEPT", probabilities=None, defect_present_p=None,
                        latency_ms=round((time.perf_counter() - t0) * 1000, 1), model=None, request_hash=h, cached=False, error=f"{type(exc).__name__}: {str(exc)[:160]}")
