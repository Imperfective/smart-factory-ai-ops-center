"""In-memory Virtual PLC. Read-only telemetry tags + a STOP command state machine that exists ONLY in the
simulator. There is no driver, socket, OPC UA client, MQTT client or network target anywhere in this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..contracts.enums import CommandStatus, MachineState

_REAL_TARGET = re.compile(r"(\d{1,3}\.){3}\d{1,3}|opc\.tcp://|mqtt://|modbus|s7://|real", re.I)


@dataclass
class TagValue:
    value: object
    sim_time_s: float
    quality: str = "VALID"


@dataclass
class VirtualPLC:
    machine_ids: list[str]
    ack_delay_s: float = 2.0            # STOP_REQUESTED -> STOPPING (simulated controller ack)
    stopping_duration_s: float = 5.0    # STOPPING -> STOPPED (simulated ramp-down)
    tags: dict[str, dict[str, TagValue]] = field(default_factory=dict)
    command_state: dict[str, CommandStatus] = field(default_factory=dict)
    _history: list[dict] = field(default_factory=list)
    _stop_since: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for m in self.machine_ids:
            if _REAL_TARGET.search(m):
                raise ValueError(f"VirtualPLC refuses non-virtual machine id {m!r}")
            self.tags[m] = {}
        self._ids = frozenset(self.machine_ids)

    # ---- telemetry (written by the simulator physics, read by the collector)
    def write_tag(self, machine_id: str, tag: str, value, sim_time_s: float, quality: str = "VALID") -> None:
        self.tags[machine_id][tag] = TagValue(value, sim_time_s, quality)

    def read_tag(self, machine_id: str, tag: str) -> TagValue | None:
        return self.tags.get(machine_id, {}).get(tag)

    def snapshot(self, machine_id: str) -> dict[str, TagValue]:
        return dict(self.tags.get(machine_id, {}))

    # ---- command state machine (simulator only)
    def is_virtual_target(self, target_id: str) -> bool:
        return target_id in self._ids and not _REAL_TARGET.search(target_id)

    def request_stop(self, machine_id: str, sim_time_s: float, command_id: str) -> CommandStatus:
        """Called ONLY by the SafetyGate after validation. Returns the new command state."""
        if not self.is_virtual_target(machine_id):
            raise ValueError("VirtualPLC: target is not a configured virtual machine")
        cur = self.command_state.get(machine_id)
        if cur in (CommandStatus.STOP_REQUESTED, CommandStatus.STOPPING, CommandStatus.STOPPED):
            return cur  # idempotent: already stopping/stopped
        self.command_state[machine_id] = CommandStatus.STOP_REQUESTED
        self._stop_since[machine_id] = sim_time_s
        self._history.append(dict(command_id=command_id, machine_id=machine_id, sim_time_s=sim_time_s,
                                  state=CommandStatus.STOP_REQUESTED.value))
        return CommandStatus.STOP_REQUESTED

    def advance(self, sim_time_s: float) -> list[dict]:
        """Progress STOP_REQUESTED -> STOPPING -> STOPPED by simulated delays. Returns transitions."""
        out = []
        for m, st in list(self.command_state.items()):
            t0 = self._stop_since.get(m, sim_time_s)
            if st == CommandStatus.STOP_REQUESTED and sim_time_s - t0 >= self.ack_delay_s:
                self.command_state[m] = CommandStatus.STOPPING
                out.append(dict(machine_id=m, sim_time_s=sim_time_s, state="STOPPING", ack_latency_s=sim_time_s - t0))
            elif st == CommandStatus.STOPPING and sim_time_s - t0 >= self.ack_delay_s + self.stopping_duration_s:
                self.command_state[m] = CommandStatus.STOPPED
                out.append(dict(machine_id=m, sim_time_s=sim_time_s, state="STOPPED"))
        self._history.extend(out)
        return out

    def machine_command_state(self, machine_id: str) -> MachineState | None:
        st = self.command_state.get(machine_id)
        return {CommandStatus.STOP_REQUESTED: MachineState.STOP_REQUESTED, CommandStatus.STOPPING: MachineState.STOPPING,
                CommandStatus.STOPPED: MachineState.STOPPED}.get(st)

    def is_halted(self, machine_id: str) -> bool:
        return self.command_state.get(machine_id) in (CommandStatus.STOPPING, CommandStatus.STOPPED)

    def release(self, machine_id: str, sim_time_s: float) -> None:
        """Simulated recovery (operator reset) — simulator only."""
        if machine_id in self.command_state:
            del self.command_state[machine_id]
            self._stop_since.pop(machine_id, None)
            self._history.append(dict(machine_id=machine_id, sim_time_s=sim_time_s, state="RELEASED"))

    @property
    def history(self) -> list[dict]:
        return list(self._history)
