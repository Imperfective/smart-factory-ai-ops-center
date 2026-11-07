"""Small CLI: offline demo run and a single real TypeSafe Jev smoke call.

  .venv/bin/python -m sfaic2.cli demo --preset S2 --arm E
  JEV_ENABLED=true .venv/bin/python -m sfaic2.cli jev-smoke
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv


def demo(preset: str, arm: str, provider: str, seed: int | None):
    from .decision.engine import DecisionEngine, OperatorPolicy
    from .decision.providers import MockJevProvider
    from .runs import save_run
    from .simulator.production import FactorySim, RunOutput
    from .simulator.scenarios import SCENARIOS, demo_factory
    cfg = demo_factory() if preset == "demo" else SCENARIOS[preset]()
    sim = FactorySim(cfg, seed=seed)
    sim.start()
    stub = RunOutput(run_id=sim.run_id, config=cfg, seed=sim.seed, events=[], truth=sim.truth, kpis={}, manifest=dict(sim_seconds=cfg.simulation.duration_s), plc_history=[], network_stats={}, powerflow_failures=0)
    eng = DecisionEngine(stub, arm=arm, provider_override=MockJevProvider() if provider == "mock" else None, plc=sim.plc,
                         events_source=sim.observed_events, operator=OperatorPolicy(), auto_operator=True)
    t = eng.interval
    while t <= cfg.simulation.duration_s:
        sim.advance(t)
        eng.step(t)
        t += eng.interval
    out = sim.finalize()
    res = eng.result()
    path = save_run(out, res)
    k = out.kpis
    print(json.dumps(dict(run_id=out.run_id, saved=str(path), good=k["good_count"], plan=k["plan_quantity"], attainment=round(k["plan_attainment"], 3),
                          energy_kwh=k["total_energy_kwh"], min_bus_voltage_pu=k["min_bus_voltage_pu"], decisions=len(res.decisions), commands=len(res.commands),
                          gate=res.gate_stats, provider=res.arm_config["provider"], events_sha256=out.manifest["events_sha256"]), indent=2))


def jev_smoke():
    """One real System One call with a redacted, label-free factory state; writes results/jev_smoke/<ts>.json."""
    from .adapters.typesafe_jev import TypeSafeJevProvider
    p = TypeSafeJevProvider(use_cache=False)
    ok, why = p.availability()
    if not ok:
        raise SystemExit(f"Jev unavailable: {why} (set JEV_ENABLED=true and TYPESAFE_API_KEY in .env)")
    state = dict(equipment_id="PRESS_01", sim_time_s=660.0, plc_state="RUNNING", plc_state_quality="STALE", plc_tag_age_ms=1700,
                 motor_current_a=0.0, current_sensor_quality="VALID", temperature_c=58.4, active_power_kw=23.0, max_tag_age_ms=500,
                 stale_signals=["machine_state"], missing_signals=[], conflicts=["PLC_RUNNING_BUT_CURRENT_ZERO"], evidence_reliability=0.375,
                 source_types=["SIMULATED"], vision_part_detected=False, vision_confidence=0.52, network=dict(max_tag_age_ms=1700, n_stale=1),
                 safety_note="Recommendation for a virtual simulator only; never issue physical control commands.")
    t0 = time.time()
    parsed, call = p.call(state)
    rec = dict(timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"), wall_ms=round((time.time() - t0) * 1000), call=call.model_dump(),
               parsed=parsed, state=state, note="single smoke call; not an experiment result")
    out = Path("results/jev_smoke"); out.mkdir(parents=True, exist_ok=True)
    f = out / f"jev_smoke_{int(time.time())}.json"
    f.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    assert "apikey" not in f.read_text()
    print(json.dumps(dict(status=call.status, resolved_model=call.resolved_model, latency_ms=round(call.latency_ms or 0), input_tokens=call.input_tokens,
                          output_tokens=call.output_tokens, estimated_cost_usd=call.estimated_cost_usd, error=call.error,
                          answers=(parsed and {k: v for k, v in parsed.items() if k != "usage"}), saved=str(f)), indent=2, default=str))


def main():
    load_dotenv(Path(".env"), override=False)
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--preset", default="S2")
    d.add_argument("--arm", default="E")
    d.add_argument("--provider", default="mock")
    d.add_argument("--seed", type=int, default=None)
    sub.add_parser("jev-smoke")
    sub.add_parser("vision-smoke")
    sub.add_parser("public-ai4i")
    sub.add_parser("research-db", help="(re)build results/research.sqlite: datasets, measured stats, runs, raw events/decisions/truth, aggregates, paired CIs")
    fv = sub.add_parser("factory-vision", help="MVTec AD → YOLO11n fine-tune (MPS) → held-out perception log for the dashboard")
    fv.add_argument("--step", choices=["fetch", "prepare", "train", "log", "all"], default="all")
    fv.add_argument("--epochs", type=int, default=30)
    fv.add_argument("--imgsz", type=int, default=640)
    fv.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "demo":
        demo(a.preset, a.arm, a.provider, a.seed)
    elif a.cmd == "jev-smoke":
        jev_smoke()
    elif a.cmd == "vision-smoke":
        vision_smoke()
    elif a.cmd == "research-db":
        from .research.db import rebuild
        print(json.dumps(rebuild(), indent=2, default=str))
    elif a.cmd == "public-ai4i":
        from .adapters.ai4i import run_ai4i
        print(json.dumps(run_ai4i(), indent=2, default=str))
    else:
        from .adapters import factory_vision as fv
        step = a.step
        if step == "fetch":
            print(fv.fetch())
        elif step == "prepare":
            print(json.dumps(fv.prepare(), indent=2))
        elif step == "train":
            print(json.dumps(fv.train(epochs=a.epochs, imgsz=a.imgsz, batch=a.batch), indent=2))
        elif step == "log":
            print(json.dumps(fv.log(imgsz=a.imgsz), indent=2, default=str))
        else:
            print(json.dumps(fv.run_all(epochs=a.epochs, imgsz=a.imgsz, batch=a.batch), indent=2, default=str))





def vision_smoke():
    """Real YOLO11n inference on Apple MPS + OpenCV QR decode on a synthetic QR overlay; writes DERIVED events
    (docs/07 contract) to results/vision_smoke/. The sample image ships with ultralytics (bus.jpg); no plant data."""
    import hashlib
    from .adapters.qr import decode_qr, make_test_qr, route_consistent
    from .adapters.vision import YoloVision
    import cv2
    out = Path("results/vision_smoke"); out.mkdir(parents=True, exist_ok=True)
    import ultralytics
    src = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"
    if not src.exists():
        raise SystemExit("ultralytics sample asset bus.jpg not found")
    img = cv2.imread(str(src))
    qr = make_test_qr("PTG300000042", out / "qr.png", size=200)
    q = cv2.imread(str(qr))
    img[20:20 + q.shape[0], 20:20 + q.shape[1]] = q
    frame = out / "frame_with_qr.jpg"
    cv2.imwrite(str(frame), img)
    yv = YoloVision(weights="yolo11n.pt", task="detect")
    res = [yv.infer(frame) for _ in range(3)]  # first call includes warm-up
    qres = decode_qr(frame)
    route = route_consistent(qres.get("payload") or "", "PTG3", "OUTBOUND_GATE", {"PTG3": ["OUTBOUND_GATE"]})
    events = [
        dict(event_id="evt_vis_01", asset_id="CAMERA_GATE_02", sim_time_s=0.0, source_type="DERIVED", detector=res[-1]["detector"],
             model_version=res[-1]["model_version"], device=res[-1]["device"], signal="part_presence", value=res[-1]["value"],
             confidence=res[-1]["confidence"], quality=res[-1]["quality"], source_image_id=res[-1]["source_image_id"],
             detections=res[-1].get("detections"), processing_ms=[r["processing_ms"] for r in res], ground_truth_available=False,
             note="COCO-pretrained detector on a public sample image; not a plant image; no QC truth is created"),
        dict(event_id="evt_qr_01", asset_id="CAMERA_GATE_02", sim_time_s=0.0, source_type="DERIVED", signal="pallet_id", value=qres.get("payload"),
             quality=qres["quality"], decoded=qres["decoded"], expected_route="PRESS_TO_PACK", observed_port="OUTBOUND_GATE",
             route_consistent=route["route_consistent"], detector=qres["detector"], source_image_id=qres["image_sha256"]),
    ]
    import torch
    rec = dict(timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"), device=yv.device, torch=torch.__version__, ultralytics=ultralytics.__version__,
               weights="yolo11n.pt", weights_sha256=yv.weights_sha256, image=str(frame), events=events)
    (out / "vision_smoke.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    print(json.dumps(dict(device=yv.device, weights_sha256=(yv.weights_sha256 or "")[:16], detections=len(res[-1].get("detections") or []),
                          classes=sorted({d["cls"] for d in (res[-1].get("detections") or [])}), processing_ms=[r["processing_ms"] for r in res],
                          qr_payload=qres.get("payload"), qr_decoded=qres["decoded"], route_consistent=route["route_consistent"], saved=str(out / "vision_smoke.json")), indent=2))


if __name__ == "__main__":
    main()
