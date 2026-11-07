"""Jev relay — runs on the researcher's Mac only.

The public demo server holds no API key. It sends Jev requests to a unix socket that an SSH reverse tunnel connects to
this relay (127.0.0.1 only); the relay checks the request shape, enforces a daily USD cap and a rate limit, adds the key
from the local .env and forwards to TypeSafe System One. Only POST /v1/systemone is relayed; the key is never returned.

    .venv/bin/python -m sfaic2.v3.jev_relay            (or scripts/jev_relay_mac.sh: relay + self-healing tunnel)
Env: JEV_RELAY_DAILY_USD (default 0.50), JEV_RELAY_MAX_PER_MIN (default 240), JEV_RELAY_PORT (default 8790);
JEV_MODEL from the local .env (if pinned, e.g. jev-1.13.0) is enforced on every relayed request.
"""
from __future__ import annotations

import collections
import json
import os
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from ..adapters.typesafe_jev import ENDPOINT_PATH, PRICE_USD_PER_M_INPUT

load_dotenv(Path(".env"), override=False)
LOG = Path("results/jev_relay")
DAILY_USD = float(os.environ.get("JEV_RELAY_DAILY_USD", "0.50"))
MAX_PER_MIN = int(os.environ.get("JEV_RELAY_MAX_PER_MIN", "240"))
BASE = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
_lock = threading.Lock()
_recent: collections.deque = collections.deque()
app = FastAPI(title="Jev relay (local)", docs_url=None, redoc_url=None, openapi_url=None)


def _usage_file() -> Path:
    LOG.mkdir(parents=True, exist_ok=True)
    return LOG / f"usage_{time.strftime('%Y-%m-%d')}.json"


def _usage() -> dict:
    f = _usage_file()
    return json.loads(f.read_text()) if f.exists() else dict(calls=0, input_tokens=0, output_tokens=0, cost_usd=0.0, refused=0)


def _save(u: dict) -> None:
    _usage_file().write_text(json.dumps(u))


@app.get("/health")
def health():
    u = _usage()
    key = os.environ.get("TYPESAFE_API_KEY", "")
    return dict(ok=bool(key) and not key.startswith("REPLACE_"), today=u, daily_cap_usd=DAILY_USD, remaining_usd=round(max(0.0, DAILY_USD - u["cost_usd"]), 4))


@app.post(ENDPOINT_PATH)
async def relay(request: Request):
    body = await request.body()
    if len(body) > 256_000:
        raise HTTPException(413, "request too large")
    try:
        req = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "not JSON") from exc
    if not isinstance(req, dict) or "state" not in req or "questions" not in req:
        raise HTTPException(400, "not a Jev System One request")
    pinned = os.environ.get("JEV_MODEL", "")
    if pinned and "latest" not in pinned:
        req["model"] = pinned        # the researcher's pinned model wins over whatever the server sent
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key or key.startswith("REPLACE_"):
        raise HTTPException(503, "relay has no key")
    with _lock:
        now = time.time()
        while _recent and now - _recent[0] > 60:
            _recent.popleft()
        u = _usage()
        if u["cost_usd"] >= DAILY_USD or len(_recent) >= MAX_PER_MIN:
            u["refused"] += 1
            _save(u)
            raise HTTPException(429, "daily budget reached" if u["cost_usd"] >= DAILY_USD else "rate limit")
        _recent.append(now)
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(BASE + ENDPOINT_PATH, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=req)
    if r.status_code != 200:
        raise HTTPException(r.status_code if r.status_code in (400, 422, 429, 503) else 502, f"upstream HTTP {r.status_code}")
    out = r.json()
    us = out.get("usage") or {}
    with _lock:
        u = _usage()
        u["calls"] += 1
        u["input_tokens"] += int(us.get("input_tokens") or 0)
        u["output_tokens"] += int(us.get("output_tokens") or 0)
        u["cost_usd"] = round(u["cost_usd"] + int(us.get("input_tokens") or 0) / 1e6 * PRICE_USD_PER_M_INPUT, 6)
        _save(u)
    return out


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("JEV_RELAY_PORT", "8790")), log_level="warning")


if __name__ == "__main__":
    main()
