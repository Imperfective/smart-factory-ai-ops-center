"""Demonstration-only deterministic judgment. NOT the TypeSafe Jev model."""
from dataclasses import dataclass

@dataclass(frozen=True)
class MockDecision:
    provider: str
    recommendation: str
    cause: str


def mock_jev_decide(evidence: dict) -> MockDecision:
    if evidence.get("quality") == "STALE":
        return MockDecision("MOCK_JEV", "REQUEST_RECHECK", "UNRELIABLE_EVIDENCE")
    if evidence.get("fault_injected"):
        return MockDecision("MOCK_JEV", "REQUEST_OPERATOR_REVIEW", "SYNTHETIC_MACHINE_FAULT")
    return MockDecision("MOCK_JEV", "CONTINUE_MONITORING", "NO_FAULT_EVIDENCE")
