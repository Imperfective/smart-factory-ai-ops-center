"""JevDecisionAdapter (v3) — TypeSafe Jev System One chooses among *program-computed, pre-validated* actions.

* The state holds only operational, as-of-now facts and numbers computed by deterministic code (plan gap, per-action
  forecasts, CP-SAT achievable bound, ETA from the maintenance catalogue). Jev is told not to recompute measurements.
* `preferred_action` criteria are generated per decision from the allowed actions only; `cause_class` and
  `needs_operator` are independent questions (no hidden coupling assumed).
* Real calls need JEV_ENABLED=true + TYPESAFE_API_KEY in .env (explicit opt-in); the key is read at call time and
  never logged. Every request/response is stored sanitised with hash, model version, latency, tokens and cost.
* MockJevV3 is a deterministic software stand-in, always labelled MOCK_JEV, and excluded from real-model results.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time

import httpx

from ..adapters.typesafe_jev import ENDPOINT_PATH, PRICE_USD_PER_M_INPUT, JevBudget, redact
from .config import ACTION_TEXT, Action

QUESTION_VERSION = "qs_v3_plan_action_2026-10-17"
CAUSES = {
    "EQUIPMENT": "A line reports a fault / maintenance state",
    "PROCESS_DRIFT": "Cycle time drifted above nominal (e.g. tool wear)",
    "QUALITY": "Reject / defect rate is elevated",
    "NETWORK": "Telemetry is stale or delayed",
    "VISION_QR": "Vision / QR evidence disagrees with PLC counts or labels",
    "COUNTER": "PLC counter is inconsistent (reset / double counting)",
    "TRANSPORT": "Pallet transport capacity is missing (AMR unavailable)",
    "POWER": "Feeder headroom limits which lines can run",
    "PLAN": "The plan is at risk or unreachable with the available capacity",
    "NONE": "Nothing abnormal; normal operation",
    "UNKNOWN": "Insufficient evidence",
}


def _b(v, step):
    return None if v is None else round(round(v / step) * step, 3)


def build_state(ft: dict, allowed: list[str], validated: bool, context: bool, drop: frozenset = frozenset()) -> dict:
    o = ft["obs"]
    fc = ft["forecast"]
    st = dict(
        plan=dict(target_good=ft["target"], good_so_far=_b(ft["good_so_far"], 5), remaining_shift_s=_b(ft["rem_s"], 300), required_rate_per_h=_b(ft["required_rate_per_h"], 5),
                  max_achievable_good_cpsat=_b(ft["max_achievable_good"], 5)),
        lines=dict(main=dict(state=o["main"]["plc_state"], fault_code=o["main"]["fault_code"] or o["main"]["last_fault_code"], diag_ok=bool(o["main"]["diag_ok"]),
                             ct_s_estimate=_b(ft["ct_main_est"], 1), ct_s_nominal=ft["ct_main_nominal"], yield_estimate=_b(ft["yield_main_est"], 0.01),
                             repair_eta_s_estimate=_b(ft["eta"]["main"], 60)),
                   sub=dict(state=o["sub"]["plc_state"], ct_s_nominal=ft["ct_sub_nominal"], yield_nominal=ft["yield_sub_nominal"], repair_eta_s_estimate=_b(ft["eta"]["sub"], 60))),
        options={a: dict(projected_good_at_end=_b(fc[a]["projected_good_end"], 5), projected_shortfall=_b(fc[a]["projected_shortfall"], 5),
                         projected_defects_shipped=_b(fc[a]["projected_defects_shipped"], 1), energy_kwh=_b(fc[a]["energy_kwh"], 1), peak_kw=fc[a]["peak_kw"],
                         startup_s=fc[a]["startup_s"]) for a in allowed},
        transport=dict(pallets_waiting=ft["pallets_waiting"], amr_available=ft["amr_available"], reserve_amr_available=ft["reserve_amr_available"]),
        open_incidents=sorted({i["cause"] for i in ft["incidents"]}),
        allowed_actions=allowed,
        note="All numbers are computed by the plant program; choose among allowed_actions only.",
    )
    if validated and ft["evidence"]:
        ev = ft["evidence"]
        st["evidence"] = dict(stale_lines=ev["stale_lines"], count_mismatch=ev["count_mismatch"], qr_mismatch_30min=ev["qr_mismatch_30min"],
                              plc_vs_camera_gap={k: _b(v, 2) for k, v in ev["plc_vs_camera_gap"].items()})
    if context:
        if ft["electrical"]:
            st["electrical"] = dict(available_kw=ft["electrical"]["available_kw"], load_kw=_b(ft["electrical"]["load_kw"], 1), nameplate_kw=ft["electrical"]["nameplate_kw"])
        if ft["network"]:
            n = ft["network"]
            st["network"] = dict(plc_p95_ms=_b(n["plc_p95_ms"], 100), mqtt_p95_ms=_b(n["mqtt_p95_ms"], 100), loss_rate=_b(n["loss_rate"], 0.01), dup_rate=_b(n["dup_rate"], 0.01))
    # ablations
    if "plan" in drop:
        st.pop("plan")
        for v in st["options"].values():
            v.pop("projected_good_at_end"); v.pop("projected_shortfall")
    if "ct" in drop:
        st["lines"]["main"].pop("ct_s_estimate"); st["lines"]["main"].pop("ct_s_nominal"); st["lines"]["sub"].pop("ct_s_nominal")
    if "network" in drop:
        st.pop("network", None)
        if "evidence" in st:
            st["evidence"].pop("stale_lines", None)
    if "electrical" in drop:
        st.pop("electrical", None)
        for v in st["options"].values():
            v.pop("peak_kw")
    if "vision_qr" in drop and "evidence" in st:
        for k in ("qr_mismatch_30min", "plc_vs_camera_gap", "count_mismatch"):
            st["evidence"].pop(k, None)
    if "transport" in drop:
        st.pop("transport")
    return st


def build_questions(st: dict, allowed: list[str]) -> dict:
    crit = {}
    for a in allowed:
        txt = ACTION_TEXT[Action(a)]
        op = st["options"].get(a, {})
        bits = []
        if "projected_good_at_end" in op:
            bits.append(f"program forecast {op['projected_good_at_end']} good units by shift end (shortfall {op['projected_shortfall']})")
        if op.get("energy_kwh") is not None:
            bits.append(f"{op['energy_kwh']} kWh")
        if op.get("peak_kw") is not None:
            bits.append(f"peak {op['peak_kw']} kW")
        crit[a] = txt + (". " + ", ".join(bits) if bits else "")
    return {
        "preferred_action": {"type": "choice", "instructions": "Select one operationally feasible response that best meets the production target without unsafe "
                             "or unsupported actions, based only on this state and the program-computed forecasts. Do not recompute measurements and do not "
                             "invent unavailable evidence. Choose ESCALATE_HUMAN when no listed automated option is supported by reliable evidence.", "criteria": crit},
        "cause_class": {"type": "choice", "instructions": "Choose the root-cause category best supported by the evidence, or UNKNOWN.", "criteria": CAUSES},
        "needs_operator": {"type": "noul", "instructions": "Is human intervention necessary because no safe, evidence-supported automated resolution is available?"},
    }


def request_hash(req: dict) -> str:
    return hashlib.sha256(json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()[:20]


class MockJevV3:
    """Deterministic stand-in (software check only). Scores allowed actions from the program forecasts."""
    provider = "MOCK_JEV"

    def decide(self, req: dict) -> dict:
        st, allowed = req["state"], req["state"]["allowed_actions"]
        ev = st.get("evidence") or {}
        risky = bool(ev.get("count_mismatch") or ev.get("qr_mismatch_30min") or ev.get("stale_lines"))
        scores = {}
        for a in allowed:
            op = st["options"].get(a, {})
            sf = op.get("projected_shortfall", 0.0) or 0.0
            sc = -sf - 0.5 * (op.get("projected_defects_shipped") or 0) - 0.02 * (op.get("energy_kwh") or 0)
            if a == "ESCALATE_HUMAN":
                sc -= 5 if not risky else -5
            if a in ("REPLAN",):
                sc -= 3
            scores[a] = sc
        motion = [a for a in allowed if a not in ("ESCALATE_HUMAN", "REPLAN")]
        if not motion:
            scores["ESCALATE_HUMAN"] = max(scores.values()) + 10
        mx = max(scores.values())
        ex = {a: math.exp((v - mx) / 10.0) for a, v in scores.items()}
        z = sum(ex.values())
        probs = {a: round(v / z, 4) for a, v in ex.items()}
        choice = max(probs, key=probs.get)
        inc = st.get("open_incidents") or []
        cause = inc[0] if inc else "NONE"
        return dict(answers=dict(preferred_action=dict(type="choice", choice=choice, probabilities=probs),
                                 cause_class=dict(type="choice", choice=cause, probabilities={cause: 1.0}),
                                 needs_operator=dict(type="noul", noul=0.85 if choice == "ESCALATE_HUMAN" else 0.1)),
                    model="mock-jev-v3", usage=dict(input_tokens=0, output_tokens=0))


SHARED_CACHE: dict = {}   # identical bucketed requests reuse one answer across episodes of a run (deterministic, cost-saving)


class TypeSafeV3Client:
    provider = "TYPESAFE_JEV"

    def __init__(self, budget: JevBudget | None = None, timeout_s: float = 20.0, max_retries: int = 3, cache: dict | None = None, transport=None):
        self.model = os.environ.get("JEV_MODEL", "jev-latest")
        self.base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
        self.budget = budget or JevBudget(int(os.environ.get("JEV_MAX_CALLS_PER_RUN", "200")), float(os.environ.get("JEV_DAILY_BUDGET_USD", "2.0")))
        self.timeout_s, self.max_retries = timeout_s, max_retries
        self.cache = cache if cache is not None else (SHARED_CACHE if transport is None else {})
        self._transport = transport

    @staticmethod
    def relay_socket() -> str | None:
        """Relay mode (public server): requests go through a unix socket that an SSH reverse tunnel connects to the
        researcher's Mac (sfaic2/v3/jev_relay.py), which adds the key there. The server never holds the key."""
        return os.environ.get("JEV_RELAY_SOCKET") or None

    @staticmethod
    def opted_in() -> tuple[bool, str]:
        if os.environ.get("JEV_ENABLED", "false").lower() != "true":
            return False, "JEV_ENABLED is not true (real calls need explicit opt-in)"
        sock = TypeSafeV3Client.relay_socket()
        if sock:
            return (True, "ok (via researcher relay)") if os.path.exists(sock) else (False, "relay offline: the researcher's Mac is not connected")
        k = os.environ.get("TYPESAFE_API_KEY")
        if not k or k.startswith("REPLACE_"):
            return False, "TYPESAFE_API_KEY missing"
        return True, "ok"

    def decide(self, req: dict) -> dict:
        ok, why = self.opted_in()
        if not ok:
            raise RuntimeError(why)
        ok, why = self.budget.allow()
        if not ok:
            raise RuntimeError(why)
        h = request_hash(req)
        if h in self.cache:
            return dict(self.cache[h], _cached=True)
        sock = self.relay_socket()
        if sock:      # no key here: the relay on the researcher's Mac authenticates
            headers, base, transport = {"Content-Type": "application/json"}, "http://jev-relay", self._transport or httpx.HTTPTransport(uds=sock)
        else:
            headers, base, transport = {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json"}, self.base, self._transport
        backoff, last = 1.0, None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_s, transport=transport) as c:
                    r = c.post(base + ENDPOINT_PATH, headers=headers, json=req)
                if r.status_code == 200:
                    body = r.json()
                    u = body.get("usage") or {}
                    self.budget.record(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0))
                    body["_retries"] = attempt
                    self.cache[h] = body
                    return body
                if r.status_code == 429 and sock:
                    raise RuntimeError(f"relay refused: {r.json().get('detail', 'daily budget reached') if r.headers.get('content-type', '').startswith('application/json') else 'daily budget reached'}")
                if r.status_code in (401, 403, 422):
                    raise RuntimeError(f"HTTP {r.status_code}: {redact(r.text[:200]) if r.status_code == 422 else 'auth failed'}")
                last = f"HTTP {r.status_code}"
            except httpx.TimeoutException:
                last = "timeout"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            if attempt < self.max_retries:
                time.sleep(min(backoff, 8.0) if self._transport is None else 0)
                backoff *= 2
        raise RuntimeError(f"retries exhausted: {last}")


def parse(body: dict, allowed: list[str]) -> dict:
    a = (body.get("answers") or {})
    pa, cc, no = a.get("preferred_action") or {}, a.get("cause_class") or {}, a.get("needs_operator") or {}
    if pa.get("type") != "choice" or pa.get("choice") not in allowed:
        raise ValueError(f"preferred_action invalid or outside allowed actions: {pa.get('choice')}")
    probs = pa.get("probabilities") or {}
    if probs and abs(sum(probs.values()) - 1.0) > 0.03:
        raise ValueError("preferred_action probabilities do not sum to 1")
    if cc.get("type") != "choice" or cc.get("choice") not in CAUSES:
        raise ValueError("cause_class invalid")
    if no.get("type") != "noul" or not isinstance(no.get("noul"), (int, float)) or not 0 <= no["noul"] <= 1:
        raise ValueError("needs_operator invalid")
    return dict(action=pa["choice"], action_probs=probs, cause=cc["choice"], cause_probs=cc.get("probabilities"), needs_operator_p=float(no["noul"]),
                resolved_model=body.get("model"), usage=body.get("usage") or {})


class JevDecisionAdapter:
    def __init__(self, provider: str, ops, ctx: dict, budget: JevBudget | None = None, cache: dict | None = None):
        self.provider_name = provider
        self.client = MockJevV3() if provider == "mock" else TypeSafeV3Client(budget=budget, cache=cache)
        self.ops, self.ctx = ops, ctx
        self.calls, self.errors, self.latencies, self.cached = 0, 0, [], 0
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0

    @property
    def label(self) -> str:
        return "MOCK_JEV" if self.provider_name == "mock" else "TYPESAFE_JEV"

    def decide(self, now: float, decision_id: str, ft: dict, allowed: list[str], validated: bool, context: bool, drop: frozenset = frozenset()) -> dict:
        st = build_state(ft, allowed, validated, context, drop)
        req = {"model": getattr(self.client, "model", "mock-jev-v3"), "state": st, "questions": build_questions(st, allowed)}
        rid = f"JREQ-{decision_id}"
        h = request_hash(req)
        self.ops.insert("jev_requests", now, source=self.label, request_id=rid, decision_id=decision_id, provider=self.label, model_version=req["model"],
                        question_version=QUESTION_VERSION, anonymized_payload_hash=h, sanitized_request_json=json.dumps(redact(req), sort_keys=True), created_at=now, status="SENT")
        t0 = time.perf_counter()
        out = dict(ok=False, request_id=rid, request_hash=h)
        try:
            body = self.client.decide(req)
            lat = (time.perf_counter() - t0) * 1000
            p = parse(body, allowed)
            cached = bool(body.get("_cached"))
            self.calls += 0 if cached else 1
            self.cached += int(cached)
            if not cached:
                self.latencies.append(lat)
            u = p["usage"]
            ti, to = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
            cost = 0.0 if (cached or self.label == "MOCK_JEV") else ti / 1e6 * PRICE_USD_PER_M_INPUT
            if not cached:
                self.tokens_in += ti; self.tokens_out += to; self.cost += cost
            out.update(ok=True, latency_ms=lat, cached=cached, **p)
            self.ops.insert("jev_responses", now, source=self.label, request_id=rid, raw_response_sanitized=json.dumps(redact({k: v for k, v in body.items() if not k.startswith("_")}), sort_keys=True),
                            selected_action=p["action"], per_choice_probabilities=p["action_probs"], cause_class=p["cause"], cause_probabilities=p["cause_probs"],
                            needs_operator_p=p["needs_operator_p"], latency_ms=round(lat, 1), error_code=None, token_usage=dict(input=ti, output=to), cost_estimate=cost,
                            cached=int(cached), provider=self.label)
        except Exception as exc:  # noqa: BLE001 — conservative fallback, recorded
            self.errors += 1
            lat = (time.perf_counter() - t0) * 1000
            out.update(error=f"{type(exc).__name__}: {str(exc)[:200]}", latency_ms=lat)
            self.ops.insert("jev_responses", now, source=self.label, request_id=rid, raw_response_sanitized=None, selected_action=None, latency_ms=round(lat, 1),
                            error_code=out["error"], provider=self.label, cached=0)
        return out

    def summary(self) -> dict:
        lat = sorted(self.latencies)
        q = lambda p: round(lat[min(len(lat) - 1, int(p * (len(lat) - 1)))], 1) if lat else None  # noqa: E731
        return dict(provider=self.label, calls=self.calls, cached=self.cached, errors=self.errors, latency_p50_ms=q(0.5), latency_p95_ms=q(0.95), latency_p99_ms=q(0.99),
                    input_tokens=self.tokens_in, output_tokens=self.tokens_out, cost_usd=round(self.cost, 6))
