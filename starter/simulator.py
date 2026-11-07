"""Tiny offline, deterministic, time-stepped factory demo.

This is intentionally NOT the planned full SimPy DES or pandapower model.
It NEVER opens a network connection or controls real equipment.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .decision import mock_jev_decide


def simulate(scenario: dict):
    assert scenario["source_type"] == "SIMULATED", "Only synthetic demo scenario is supported"
    duration = int(scenario["simulation"]["duration_s"])
    machines = {m["machine_id"]: m for m in scenario["machines"]}
    fault_map = {m: [] for m in machines}
    for fault in scenario["faults"]:
        assert fault["machine_id"] in machines
        fault_map[fault["machine_id"]].append(fault)
    state = {m: {"good": 0, "accumulator_s": 0.0, "energy_kwh": 0.0, "last_state": None} for m in machines}
    events = []
    def event(t, machine_id, signal, value, unit=None, quality="VALID"):
        events.append({"event_id": f"ev{len(events)+1:06}", "run_id":scenario["scenario_id"],
                       "sim_time_s":t,"asset_id":machine_id,"source_type":"SIMULATED",
                       "signal":signal,"value":value,"unit":unit,"quality":quality,
                       "source_timestamp":None,"received_timestamp":None,"evidence_id":None,
                       "provenance":{"dataset_id":None,"source_label":"SYNTHETIC_DEMO"}})
    # Time-stepped *starter* at 1 second resolution. Codex must replace with DES.
    for t in range(duration):
        for machine_id, m in machines.items():
            s = state[machine_id]
            fault_present = any(f["at_s"] <= t < f["at_s"] + f["duration_s"] for f in fault_map[machine_id])
            machine_on = (not fault_present and s["good"] < scenario["production_plan"]["quantity"])
            observed = "RUNNING" if machine_on else ("FAULT" if fault_present else "IDLE")
            if observed != s["last_state"]:
                event(t,machine_id,"machine_state",observed)
                s["last_state"] = observed
                if fault_present:
                    decision = mock_jev_decide({"fault_injected":True,"quality":"VALID"})
                    event(t,machine_id,"mock_decision",decision.recommendation)
            pkw = m["run_kw"] if machine_on else m["idle_kw"]
            s["energy_kwh"] += pkw/3600.0
            if machine_on:
                s["accumulator_s"] += 1.0
                if s["accumulator_s"] >= float(m["cycle_time_s"]):
                    s["accumulator_s"] -= float(m["cycle_time_s"])
                    s["good"] += 1
                    event(t,machine_id,"production_good_count",s["good"],"parts")
            # Only low-volume state events by default
            if t % 60 == 0:
                event(t,machine_id,"active_power_kw",pkw,"kW")
    summary={"run_id":scenario["scenario_id"],"source_type":"SIMULATED",
             "sim_seconds":duration,"total_good":sum(v["good"] for v in state.values()),
             "total_energy_kwh":round(sum(v["energy_kwh"] for v in state.values()),4),
             "per_machine":{k:{"good":v["good"],"energy_kwh":round(v["energy_kwh"],4)} for k,v in state.items()},
             "plan_quantity":scenario["production_plan"]["quantity"],
             "notice":"Synthetic time-stepped engineering demo, not published experimental result"}
    return events,summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario",type=Path,default=Path("examples/press_factory_demo.json"))
    parser.add_argument("--output",type=Path,default=Path("results/demo"))
    args=parser.parse_args()
    scenario=json.loads(args.scenario.read_text(encoding="utf-8"))
    events,summary=simulate(scenario)
    args.output.mkdir(parents=True,exist_ok=True)
    trace="".join(json.dumps(e,ensure_ascii=False,sort_keys=True)+"\n" for e in events)
    (args.output/"events.jsonl").write_text(trace,encoding="utf-8")
    summary["trace_sha256"]=hashlib.sha256(trace.encode()).hexdigest()
    (args.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
