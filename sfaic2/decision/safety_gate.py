"""Independent Safety / Policy Gate (docs/09). Deterministic, provider-agnostic, fail-closed.

A virtual STOP can reach the VirtualPLC only through `SafetyGate.approve()` after:
  simulation_mode is on  ·  target is a configured virtual machine  ·  an authorised operator approved it  ·
  the evidence context is fresh (not older than `max_evidence_age_s`) and unchanged (hash match)  ·
  the command id was not processed before (idempotent)  ·  plan invariants hold (not already stopped/stopping).
AI output never bypasses this gate; the gate never claims to be an emergency-stop function.
"""
from __future__ import annotations

import os
import uuid

from ..contracts.decisions import DecisionEnvelope, VirtualCommand
from ..contracts.enums import CommandStatus, GateStatus, Recommendation
from ..simulator.virtual_plc import VirtualPLC

POLICY_VERSION = "gate_v1"


class SafetyGate:
    def __init__(self, plc: VirtualPLC, run_id: str, authorised_operators: set[str] | None = None,
                 max_evidence_age_s: float = 120.0, approval_timeout_s: float = 600.0):
        if os.environ.get("SIMULATION_MODE", "true").lower() != "true":
            raise RuntimeError("SafetyGate refuses to start: SIMULATION_MODE must be 'true' (virtual PLC only)")
        if os.environ.get("CONTROL_TARGET", "VIRTUAL_PLC_ONLY") != "VIRTUAL_PLC_ONLY":
            raise RuntimeError("SafetyGate refuses to start: CONTROL_TARGET must be VIRTUAL_PLC_ONLY")
        self.plc, self.run_id = plc, run_id
        self.operators = authorised_operators or {"operator_a", "operator_b"}
        self.max_evidence_age_s, self.approval_timeout_s = max_evidence_age_s, approval_timeout_s
        self.commands: dict[str, VirtualCommand] = {}
        self.audit: list[dict] = []
        self.stats = dict(proposed=0, rejected_invalid=0, pending=0, approved=0, executed=0, unsafe_allowed=0)

    # ---- helpers
    def _log(self, cmd: VirtualCommand, status: CommandStatus, reason: str, sim_s: float, actor: str | None = None):
        cmd.status, cmd.reason = status, reason
        rec = dict(command_id=cmd.command_id, status=status.value, reason=reason, sim_time_s=sim_s, actor=actor,
                   target_id=cmd.target_id, decision_id=cmd.decision_id, context_hash=cmd.evidence_context_hash,
                   policy_version=POLICY_VERSION)
        cmd.transition_history.append(rec)
        self.audit.append(rec)

    # ---- propose (from a decision or an operator button)
    def propose(self, decision: DecisionEnvelope | None, target_id: str, requested_by: str, sim_s: float,
                command_id: str | None = None, simulation_mode: bool = True, virtual_only: bool = True) -> VirtualCommand:
        cid = command_id or f"cmd_{uuid.uuid4().hex[:10]}"
        if cid in self.commands:
            return self.commands[cid]  # idempotent replay returns the earlier state, no re-execution
        cmd = VirtualCommand(command_id=cid, run_id=self.run_id, target_id=target_id, requested_by=requested_by, proposed_sim_s=sim_s,
                             decision_id=decision.decision_id if decision else None,
                             evidence_context_hash=decision.context_hash if decision else None,
                             evidence_sim_s=decision.sim_time_s if decision else None, simulation_mode=simulation_mode,
                             virtual_only=virtual_only)
        self.commands[cid] = cmd
        self.stats["proposed"] += 1
        self._log(cmd, CommandStatus.PROPOSED, "proposed", sim_s, requested_by)
        if not simulation_mode or not virtual_only:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "SIMULATION_ONLY", sim_s)
            return cmd
        if not self.plc.is_virtual_target(target_id):
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "VIRTUAL_TARGET_REQUIRED", sim_s)
            return cmd
        if decision is not None and decision.recommendation != Recommendation.REQUEST_SIMULATED_STOP:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "DECISION_DID_NOT_RECOMMEND_STOP", sim_s)
            return cmd
        if self.plc.command_state.get(target_id) is not None:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "ALREADY_STOPPING_OR_STOPPED", sim_s)
            return cmd
        self.stats["pending"] += 1
        self._log(cmd, CommandStatus.PENDING_APPROVAL, "operator approval required", sim_s)
        if decision is not None:
            decision.safety_gate_status = GateStatus.PENDING_APPROVAL
        return cmd

    # ---- operator decision
    def approve(self, command_id: str, operator_id: str, sim_s: float, current_context_hash: str | None = None,
                approved: bool = True, evidence_sim_s: float | None = None) -> VirtualCommand:
        cmd = self.commands.get(command_id)
        if cmd is None:
            raise KeyError(command_id)
        if cmd.status != CommandStatus.PENDING_APPROVAL:
            return cmd  # already decided: idempotent
        if not approved:
            self._log(cmd, CommandStatus.REJECTED, "OPERATOR_DENIED", sim_s, operator_id)
            return cmd
        if operator_id not in self.operators:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "OPERATOR_NOT_AUTHORISED", sim_s, operator_id)
            return cmd
        if sim_s - cmd.proposed_sim_s > self.approval_timeout_s:
            self._log(cmd, CommandStatus.REJECTED, "APPROVAL_TIMEOUT", sim_s, operator_id)
            return cmd
        ev_t = evidence_sim_s if evidence_sim_s is not None else cmd.evidence_sim_s
        if ev_t is None or sim_s - ev_t > self.max_evidence_age_s:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "EVIDENCE_STALE", sim_s, operator_id)
            return cmd
        if current_context_hash is not None and cmd.evidence_context_hash is not None and current_context_hash != cmd.evidence_context_hash:
            self.stats["rejected_invalid"] += 1
            self._log(cmd, CommandStatus.REJECTED, "EVIDENCE_CHANGED_SINCE_PROPOSAL", sim_s, operator_id)
            return cmd
        cmd.approval = dict(operator_id=operator_id, approved_sim_s=sim_s, context_hash=cmd.evidence_context_hash)
        self.stats["approved"] += 1
        self._log(cmd, CommandStatus.VALIDATED, "approved + fresh evidence", sim_s, operator_id)
        state = self.plc.request_stop(cmd.target_id, sim_s, cmd.command_id)
        self.stats["executed"] += 1
        self._log(cmd, state, "VIRTUAL_PLC_STOP_REQUESTED", sim_s, "safety_gate")
        return cmd

    def sync_from_plc(self, sim_s: float) -> None:
        """Mirror STOPPING/STOPPED transitions of the virtual PLC into the command records (ack tracking).
        The PLC may advance several states between two gate syncs, so transitions are taken from its history."""
        for cmd in self.commands.values():
            if cmd.status not in (CommandStatus.STOP_REQUESTED, CommandStatus.STOPPING):
                continue
            hist = [h for h in self.plc.history if h.get("machine_id") == cmd.target_id and h.get("sim_time_s", -1) >= cmd.proposed_sim_s]
            if cmd.status == CommandStatus.STOP_REQUESTED:
                ack = next((h for h in hist if h.get("state") == "STOPPING"), None)
                if ack:
                    cmd.ack_sim_s = ack["sim_time_s"]
                    self._log(cmd, CommandStatus.STOPPING, "virtual PLC ack", ack["sim_time_s"], "virtual_plc")
            if cmd.status == CommandStatus.STOPPING:
                done = next((h for h in hist if h.get("state") == "STOPPED"), None)
                if done:
                    self._log(cmd, CommandStatus.STOPPED, "virtual stop complete", done["sim_time_s"], "virtual_plc")
