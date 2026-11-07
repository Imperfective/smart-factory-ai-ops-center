import pytest

from sfaic2.contracts.enums import CommandStatus
from sfaic2.simulator.virtual_plc import VirtualPLC


def test_rejects_real_looking_targets():
    with pytest.raises(ValueError):
        VirtualPLC(["PRESS_01", "real-plc-10.0.0.5"])
    plc = VirtualPLC(["PRESS_01"])
    assert not plc.is_virtual_target("opc.tcp://10.0.0.5:4840") and not plc.is_virtual_target("PRESS_99")
    with pytest.raises(ValueError):
        plc.request_stop("PRESS_99", 0.0, "c1")


def test_stop_state_machine_and_idempotency():
    plc = VirtualPLC(["PRESS_01"], ack_delay_s=2, stopping_duration_s=5)
    assert plc.request_stop("PRESS_01", 10.0, "c1") == CommandStatus.STOP_REQUESTED
    assert plc.request_stop("PRESS_01", 10.5, "c2") == CommandStatus.STOP_REQUESTED  # no double execution
    assert plc.advance(11.0) == []
    tr = plc.advance(12.0)
    assert tr and tr[0]["state"] == "STOPPING" and tr[0]["ack_latency_s"] == 2.0
    assert plc.advance(17.0)[0]["state"] == "STOPPED"
    assert plc.is_halted("PRESS_01")
    plc.release("PRESS_01", 20.0)
    assert not plc.is_halted("PRESS_01")


def test_no_network_modules_imported():
    import sfaic2.simulator.virtual_plc as m
    src = open(m.__file__, encoding="utf-8").read()
    for banned in ("import socket", "opcua", "paho", "pymodbus", "snap7", "requests", "httpx"):
        assert banned not in src
