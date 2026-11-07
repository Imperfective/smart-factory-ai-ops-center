"""TypeSafe Jev (System One) adapter — official HTTP API `POST https://api.typesafe.ai/v1/systemone`.

Verified against docs.typesafe.ai (api.md, concepts/state.md, primitives/*, confidence.md, 2026-10-10):
  request  : {"model": "jev-latest", "state": <str|object|array>, "questions": {id: {type, instructions, criteria}}}
  answers  : choice -> {type, choice, probabilities{opt: p}, confidence}; score -> {type, score, legend, probabilities, confidence};
             noul -> {type, noul (0..1)}  ; top-level: model (resolved, e.g. "jev-1.13.0"), usage{input_tokens, output_tokens}
  errors   : 401 auth, 422 validation, 429 rate limit, 529 overloaded (backoff + retry)
  pricing  : $0.042 per 1M input tokens, output free (docs/models) — used ONLY for the local budget estimate.
Jev accepts text only; images are never sent. The API key is read from the environment (TYPESAFE_API_KEY) at call
time, never stored in objects that get serialised, never logged. `JEV_ENABLED=false` (default) makes the provider
unavailable and the engine falls back to deterministic policy + human review.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid

import httpx

from ..contracts.decisions import DecisionEnvelope, EvidenceBundle, JevCall
from ..contracts.enums import Provider, Recommendation, RiskBand
from .. import decision as _d  # noqa: F401  (package import for typing only)

ENDPOINT_PATH = "/v1/systemone"
PRICE_USD_PER_M_INPUT = 0.042
QUESTION_SET_VERSION = "qs_v2_cause_first_2026-10-10"

QUESTIONS = {
    "likely_cause": {
        "type": "choice",
        "instructions": "Considering the evidence quality flags (stale, missing, conflicting signals) and the observed deviations, "
                        "choose the single most plausible broad explanation of the equipment state. Choose 'unknown' if the "
                        "reliable evidence is insufficient.",
        "criteria": {
            "physical_process_issue": "Fresh, mutually consistent evidence supports a real mechanical, thermal or quality problem",
            "sensor_issue": "One measurement contradicts other fresh measurements, suggesting a faulty or stuck sensor",
            "communications_issue": "Stale timestamps, delays or missing tags explain the inconsistency",
            "electrical_loading": "Bus voltage or transformer loading excursions explain the observations",
            "process_routing_issue": "Material or QR routing is inconsistent with the expected route",
            "quality_issue": "Vision inspection reports defective parts while equipment signals are nominal",
            "none": "Everything looks nominal",
            "unknown": "Insufficient reliable evidence",
        },
    },
    "review_needed": {"type": "noul", "instructions": "Should an operator verify this situation before any change to the simulated "
                                                      "equipment state? Answer yes when evidence is ambiguous, stale or conflicting."},
    "risk_band": {"type": "score", "instructions": "Rate the operational concern using only the evidence quality and observed deviations.",
                  "criteria": ["low", "moderate", "high", "critical"]},
    "stop_warranted": {"type": "noul", "instructions": "Is a simulated equipment stop warranted by fresh, consistent evidence of a "
                                                       "genuine physical problem (not by stale or contradictory sensors)?"},
}


class JevBudget:
    def __init__(self, max_calls: int, daily_budget_usd: float):
        self.max_calls, self.daily_budget_usd = max_calls, daily_budget_usd
        self.calls, self.input_tokens, self.output_tokens, self.cost_usd = 0, 0, 0, 0.0
        self.lock = threading.Lock()

    def allow(self) -> tuple[bool, str]:
        if self.calls >= self.max_calls:
            return False, f"JEV_MAX_CALLS_PER_RUN ({self.max_calls}) reached"
        if self.cost_usd >= self.daily_budget_usd:
            return False, f"JEV_DAILY_BUDGET_USD ({self.daily_budget_usd}) reached"
        return True, "ok"

    def record(self, input_tokens: int, output_tokens: int):
        with self.lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cost_usd += input_tokens / 1e6 * PRICE_USD_PER_M_INPUT

    def summary(self) -> dict:
        return dict(calls=self.calls, input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                    estimated_cost_usd=round(self.cost_usd, 6), max_calls=self.max_calls, daily_budget_usd=self.daily_budget_usd)


def redact(obj):
    """Never let anything that looks like a key or bearer token into logs/results."""
    s = json.dumps(obj, default=str)
    import re
    s = re.sub(r"apikey_[A-Za-z0-9_]+", "apikey_[REDACTED]", s)
    s = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", s)
    return json.loads(s)


class TypeSafeJevProvider:
    name = Provider.TYPESAFE_JEV

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout_s: float = 30.0, max_retries: int = 3,
                 budget: JevBudget | None = None, cache: dict | None = None, use_cache: bool = True, transport=None):
        self.model = model or os.environ.get("JEV_MODEL", "jev-latest")
        self.base_url = (base_url or os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")).rstrip("/")
        self.timeout_s, self.max_retries = timeout_s, max_retries
        self.budget = budget or JevBudget(int(os.environ.get("JEV_MAX_CALLS_PER_RUN", "50")), float(os.environ.get("JEV_DAILY_BUDGET_USD", "2.0")))
        self.cache = cache if cache is not None else {}
        self.use_cache = use_cache
        self._transport = transport  # httpx mock transport for tests
        self.last_error: str | None = None

    # ---- availability
    @property
    def enabled(self) -> bool:
        return os.environ.get("JEV_ENABLED", "false").lower() == "true"

    def _api_key(self) -> str | None:
        k = os.environ.get("TYPESAFE_API_KEY")
        return k if k and not k.startswith("REPLACE_") else None

    def availability(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "JEV_ENABLED is not true"
        if not self._api_key():
            return False, "TYPESAFE_API_KEY missing"
        return self.budget.allow()

    # ---- request / parse
    @staticmethod
    def jev_state_view(state: dict) -> dict:
        """What is actually sent: no simulation clock, numeric fields bucketed (1 °C, 1 A, 1 kW, 100 ms, 0.05) so that
        physically equivalent situations share one request (deterministic result cache; lower cost)."""
        def r(v, step):
            return None if not isinstance(v, (int, float)) else round(round(v / step) * step, 3)
        view = {k: v for k, v in state.items() if k not in ("sim_time_s",)}
        view["plc_tag_age_ms"] = r(state.get("plc_tag_age_ms"), 100)
        view["motor_current_a"] = r(state.get("motor_current_a"), 1)
        view["temperature_c"] = r(state.get("temperature_c"), 1)
        view["active_power_kw"] = r(state.get("active_power_kw"), 1)
        view["evidence_reliability"] = r(state.get("evidence_reliability"), 0.125)
        if isinstance(state.get("vision_confidence"), (int, float)):
            view["vision_confidence"] = r(state["vision_confidence"], 0.1)
        if isinstance(state.get("electrical"), dict):
            e = state["electrical"]
            view["electrical"] = dict(bus_voltage_pu=r(e.get("bus_voltage_pu"), 0.005), transformer_loading_pct=r(e.get("transformer_loading_pct"), 5), age_s=r(e.get("age_s"), 30))
        if isinstance(state.get("network"), dict):
            n = state["network"]
            view["network"] = dict(max_tag_age_ms=r(n.get("max_tag_age_ms"), 100), n_stale=n.get("n_stale"))
        return view

    @classmethod
    def build_request(cls, state: dict, model: str) -> dict:
        return {"model": model, "state": cls.jev_state_view(state), "questions": QUESTIONS}

    @staticmethod
    def request_hash(req: dict) -> str:
        return hashlib.sha256(json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()[:20]

    @staticmethod
    def parse(resp: dict) -> dict:
        """Strict parser for the documented answer schema. Raises ValueError on unexpected shapes."""
        ans = resp.get("answers")
        if not isinstance(ans, dict):
            raise ValueError("response lacks 'answers'")
        out = {"resolved_model": resp.get("model"), "usage": resp.get("usage", {})}
        lc = ans.get("likely_cause", {})
        if lc.get("type") != "choice" or "choice" not in lc or not isinstance(lc.get("probabilities"), dict):
            raise ValueError("likely_cause answer malformed")
        if abs(sum(lc["probabilities"].values()) - 1.0) > 0.02:
            raise ValueError("likely_cause probabilities do not sum to 1")
        out["likely_cause"] = lc["choice"]
        out["likely_cause_probabilities"] = lc["probabilities"]
        out["likely_cause_confidence"] = lc.get("confidence")
        rn = ans.get("review_needed", {})
        if rn.get("type") != "noul" or not isinstance(rn.get("noul"), (int, float)):
            raise ValueError("review_needed answer malformed")
        out["review_needed_p"] = float(rn["noul"])
        sw = ans.get("stop_warranted", {})
        if sw.get("type") != "noul" or not isinstance(sw.get("noul"), (int, float)):
            raise ValueError("stop_warranted answer malformed")
        out["stop_warranted_p"] = float(sw["noul"])
        rb = ans.get("risk_band", {})
        if rb.get("type") != "score" or not isinstance(rb.get("score"), (int, float)):
            raise ValueError("risk_band answer malformed")
        out["risk_score"] = float(rb["score"])
        out["risk_probabilities"] = rb.get("probabilities")
        out["risk_confidence"] = rb.get("confidence")
        for k in ("review_needed_p", "stop_warranted_p"):
            if not 0 <= out[k] <= 1:
                raise ValueError(f"{k} out of range")
        return out

    def call(self, state: dict) -> tuple[dict | None, JevCall]:
        req = self.build_request(state, self.model)
        rh = self.request_hash(req)
        call = JevCall(provider=self.name, model_alias=self.model, question_set_version=QUESTION_SET_VERSION, request_hash=rh)
        ok, why = self.availability()
        if not ok:
            call.status, call.error = "unavailable", why
            return None, call
        if self.use_cache and rh in self.cache:
            cached = self.cache[rh]
            call.cached, call.resolved_model, call.raw_answers_redacted = True, cached.get("resolved_model"), redact(cached)
            call.latency_ms = 0.0
            return cached, call
        headers = {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}
        backoff, last = 1.0, None
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                    r = c.post(self.base_url + ENDPOINT_PATH, headers=headers, json=req)
                call.latency_ms = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    parsed = self.parse(r.json())
                    usage = parsed.get("usage") or {}
                    call.input_tokens, call.output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
                    self.budget.record(int(call.input_tokens or 0), int(call.output_tokens or 0))
                    call.estimated_cost_usd = round(int(call.input_tokens or 0) / 1e6 * PRICE_USD_PER_M_INPUT, 6)
                    call.resolved_model, call.retries = parsed["resolved_model"], attempt
                    call.raw_answers_redacted = redact({k: v for k, v in parsed.items() if k != "usage"})
                    if self.use_cache:
                        self.cache[rh] = parsed
                    return parsed, call
                if r.status_code in (401, 403):
                    call.status, call.error = "auth_error", f"HTTP {r.status_code}: authentication failed (key not logged)"
                    return None, call
                if r.status_code == 422:
                    call.status, call.error = "validation_error", f"HTTP 422: {redact(r.text[:300])}"
                    return None, call
                if r.status_code in (429, 529) or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                else:
                    call.status, call.error = "http_error", f"HTTP {r.status_code}"
                    return None, call
            except httpx.TimeoutException:
                last = "timeout"
            except (httpx.HTTPError, ValueError) as exc:
                last = f"{type(exc).__name__}: {str(exc)[:200]}"
                if isinstance(exc, ValueError):  # malformed response: do not retry blindly
                    call.status, call.error = "invalid_response", last
                    return None, call
            call.retries = attempt + 1
            if attempt < self.max_retries:
                time.sleep(min(backoff, 8.0) if self._transport is None else 0)
                backoff *= 2
        call.status, call.error = "unavailable", f"retries exhausted: {last}"
        return None, call

    # ---- provider interface
    def decide(self, state: dict, b: EvidenceBundle) -> DecisionEnvelope:
        parsed, call = self.call(state)
        if parsed is None:
            # Safe fallback: Jev unavailable => never CONTINUE blindly; hold for human review (docs/08)
            rec, risk, causes = Recommendation.REQUEST_OPERATOR_REVIEW, RiskBand.UNKNOWN, ["jev_unavailable"]
            rationale = dict(fallback=True, reason=call.error)
        else:
            cause = parsed["likely_cause"]
            review_p, stop_p, risk_score = parsed["review_needed_p"], parsed["stop_warranted_p"], parsed["risk_score"]
            risk = [RiskBand.LOW, RiskBand.MODERATE, RiskBand.HIGH, RiskBand.CRITICAL][min(3, max(0, int(round(risk_score))))]
            # policy v2 (cause-first): a sensing/communication explanation asks for a re-check before escalating to a
            # human, even when review_needed is high; genuine physical/process/electrical/quality causes go to review.
            if stop_p >= 0.7 and cause == "physical_process_issue" and not state["stale_signals"]:
                rec = Recommendation.REQUEST_SIMULATED_STOP
            elif cause in ("sensor_issue", "communications_issue"):
                rec = Recommendation.REQUEST_RECHECK
            elif review_p >= 0.5 or cause in ("unknown", "physical_process_issue", "process_routing_issue", "electrical_loading", "quality_issue"):
                rec = Recommendation.REQUEST_OPERATOR_REVIEW
            else:
                rec = Recommendation.CONTINUE_MONITORING
            causes = [cause]
            rationale = dict(review_needed_p=review_p, stop_warranted_p=stop_p, risk_score=risk_score,
                             likely_cause_probabilities=parsed["likely_cause_probabilities"],
                             note="Jev outputs are model-reported distributions; not assumed calibrated (docs/08)")
        env = DecisionEnvelope(decision_id=f"dec_{uuid.uuid4().hex[:12]}", run_id=b.run_id, provider=self.name, recommendation=rec,
                               risk_band=risk, causes=causes, evidence_ids=[it.evidence_id for it in b.items],
                               model_version=call.resolved_model or self.model, asset_id=b.asset_id, sim_time_s=b.window_end_s,
                               bundle_id=b.bundle_id, context_hash=b.context_hash, rationale=rationale, jev_call=call)
        return env
