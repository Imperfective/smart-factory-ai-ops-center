"""Virtual-only supervisory command gate. NEVER controls a real asset."""
from dataclasses import dataclass

@dataclass(frozen=True)
class VirtualStopRequest:
    command_id: str
    target_id: str
    actor_id: str
    operator_approved: bool
    evidence_fresh: bool
    simulation_mode: bool = True

class VirtualPLC:
    def __init__(self, machine_ids):
        self._virtual_ids = frozenset(machine_ids)
        self.states = {machine_id: "RUNNING" for machine_id in machine_ids}
        self._processed = {}

    def request_stop(self, request: VirtualStopRequest):
        if request.command_id in self._processed:
            return self._processed[request.command_id]
        if not request.simulation_mode:
            response = {"status": "REJECTED", "reason": "SIMULATION_ONLY"}
        elif request.target_id not in self._virtual_ids:
            response = {"status": "REJECTED", "reason": "VIRTUAL_TARGET_REQUIRED"}
        elif not request.actor_id or not request.operator_approved:
            response = {"status": "REJECTED", "reason": "OPERATOR_APPROVAL_REQUIRED"}
        elif not request.evidence_fresh:
            response = {"status": "REJECTED", "reason": "EVIDENCE_STALE"}
        else:
            self.states[request.target_id] = "STOPPED"
            response = {"status": "EXECUTED_VIRTUAL_ONLY", "reason": "VIRTUAL_STOP_ACK"}
        self._processed[request.command_id] = response
        return response
