"""Persist a finished run under results/runs/<run_id>/ (docs/10 provenance rules)."""
from __future__ import annotations

import json
from pathlib import Path

from .decision.engine import EngineResult
from .simulator.production import RunOutput

RESULTS = Path("results/runs")


def _jsonl(path: Path, rows) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def save_run(out: RunOutput, engine: EngineResult | None = None, root: Path = RESULTS, extra: dict | None = None) -> Path:
    d = root / out.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(out.config.model_dump_json(indent=2), encoding="utf-8")
    _jsonl(d / "events.jsonl", (e.to_schema_dict() for e in out.events))
    _jsonl(d / "truth.jsonl", (t.model_dump() for t in out.truth))
    (d / "metrics.json").write_text(json.dumps(out.kpis, indent=2, default=str), encoding="utf-8")
    manifest = dict(out.manifest, network_stats=out.network_stats, plc_history=out.plc_history, **(extra or {}))
    if engine is not None:
        _jsonl(d / "decisions.jsonl", (dict(e.model_dump(), **{}) for e in engine.decisions))
        _jsonl(d / "commands.jsonl", (c.model_dump() for c in engine.commands))
        _jsonl(d / "audit.jsonl", engine.gate_audit)
        manifest.update(arm=engine.arm, arm_config=engine.arm_config, gate_stats=engine.gate_stats, jev_usage=engine.jev_usage,
                        n_decisions=len(engine.decisions), n_commands=len(engine.commands), jev_fallbacks=engine.fallbacks)
    (d / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return d
