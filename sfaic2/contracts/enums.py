from enum import Enum


class SourceType(str, Enum):
    SIMULATED = "SIMULATED"
    OBSERVED_PUBLIC = "OBSERVED_PUBLIC"
    DERIVED = "DERIVED"


class Quality(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICTED = "CONFLICTED"
    QUESTIONABLE = "QUESTIONABLE"


class MachineState(str, Enum):
    OFF = "OFF"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FAULT = "FAULT"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class Provider(str, Enum):
    RULES = "RULES"
    ML = "ML"
    TYPESAFE_JEV = "TYPESAFE_JEV"
    MOCK_JEV = "MOCK_JEV"


class Recommendation(str, Enum):
    CONTINUE_MONITORING = "CONTINUE_MONITORING"
    REQUEST_RECHECK = "REQUEST_RECHECK"
    REQUEST_OPERATOR_REVIEW = "REQUEST_OPERATOR_REVIEW"
    REQUEST_SIMULATED_STOP = "REQUEST_SIMULATED_STOP"


class RiskBand(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class GateStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    REJECTED = "REJECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTED_VIRTUAL_ONLY = "EXECUTED_VIRTUAL_ONLY"


class CommandStatus(str, Enum):
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    VALIDATED = "VALIDATED"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class FaultKind(str, Enum):
    """Hidden simulator truth classes (docs/04 F01..F08)."""
    MECHANICAL_OVERLOAD = "MECHANICAL_OVERLOAD"      # F01 torque overload: current up, CT drift
    MECHANICAL_STOP = "MECHANICAL_STOP"              # F02 real stop: FAULT, current down
    NETWORK_DELAY = "NETWORK_DELAY"                  # F03 gateway delay: stale PLC tags
    SENSOR_FAULT = "SENSOR_FAULT"                    # F04 stuck-at sensor, plant unaffected
    ELECTRICAL_LOADING = "ELECTRICAL_LOADING"        # F05 transformer loading / undervoltage
    QR_MISROUTE = "QR_MISROUTE"                      # F06 material routed to wrong stage
    VISION_FALSE_NEGATIVE = "VISION_FALSE_NEGATIVE"  # F07 detector miss
    MULTI_CAUSE = "MULTI_CAUSE"                      # F08 equipment + communication overlap
    VISION_DEFECT = "VISION_DEFECT"                  # F09 vision (YOLO) reports defective parts while PLC looks nominal


class TruthClass(str, Enum):
    """Ground-truth family used for evaluation (never a single binary label)."""
    NONE = "NONE"
    PHYSICAL_STATE_ANOMALY = "PHYSICAL_STATE_ANOMALY"
    SENSOR_FALSE_READING = "SENSOR_FALSE_READING"
    COMMUNICATION_DELAY = "COMMUNICATION_DELAY"
    PROCESS_POLICY_VIOLATION = "PROCESS_POLICY_VIOLATION"
    ELECTRICAL_QUALITY = "ELECTRICAL_QUALITY"
    PERCEPTION_ERROR = "PERCEPTION_ERROR"
    CYBER_INTRUSION = "CYBER_INTRUSION"
