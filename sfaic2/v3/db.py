"""v3 databases.

* OpsDB  — the operational store (docs 01 §4 tables 1–15). Everything the decision layer may read. Rows carry the common
  columns run_id, episode_id, scenario_id, seed, simulation_time, wall_time, source, provenance, schema_version, and
  observed rows also carry received_sim_s (when the row became visible after network delay).
* TruthStore — evaluator-only (tables 16 ground_truth + incident_truth). A separate SQLite connection; the snapshot
  builder and the Jev adapter are never given it (tests assert this).
* Episode outcomes (table 17) are written by the experiment runner into the experiment database.

Per-episode stores are in-memory (fast); `persist()` copies them to disk with the SQLite backup API when raw
retention is requested. Raw YOLO JSON is stored verbatim and `vision_events` rows are never updated (immutable).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import SCHEMA_VERSION

COMMON = "run_id TEXT, episode_id TEXT, scenario_id TEXT, seed INTEGER, simulation_time REAL, wall_time REAL, source TEXT, provenance TEXT, schema_version TEXT"

OPS_DDL = f"""
CREATE TABLE production_plans ({COMMON}, plan_id TEXT, product_id TEXT, target_good_qty INTEGER, due_at REAL, shift_minutes REAL, priority INTEGER);
CREATE TABLE line_configs ({COMMON}, line_id TEXT, role TEXT, ct_s REAL, ramp_up_s REAL, capacity_per_shift REAL, yield_rate REAL, rated_kw REAL, idle_kw REAL, equipment_ids TEXT, eligibility TEXT);
CREATE TABLE equipment_events ({COMMON}, received_sim_s REAL, equipment_id TEXT, line_id TEXT, plc_state TEXT, diag_ok INTEGER, cycle_count INTEGER, fault_code TEXT,
    current_a REAL, voltage_v REAL, temp_c REAL, load_kw REAL, evidence_quality TEXT, msg_id TEXT);
CREATE TABLE vision_events ({COMMON}, received_sim_s REAL, camera_id TEXT, line_id TEXT, unit_seq INTEGER, frame_hash TEXT, model_name TEXT, model_version TEXT, labels TEXT, bbox TEXT,
    score REAL, image_uri TEXT, processing_ms REAL, raw_json TEXT NOT NULL, pallet_id TEXT, ground_truth_ref TEXT);
CREATE TABLE qr_events ({COMMON}, received_sim_s REAL, qr_id TEXT, pallet_id TEXT, product_id TEXT, line_id TEXT, scanned_at REAL, decode_quality TEXT, port_id TEXT,
    expected_location TEXT, actual_location TEXT, declared_qty INTEGER);
CREATE TABLE network_events ({COMMON}, topic TEXT, sender TEXT, receiver TEXT, sent_at REAL, received_at REAL, delay_ms REAL, dropped INTEGER, duplicate INTEGER, out_of_order INTEGER, qos INTEGER, msg_id TEXT);
CREATE TABLE production_counts ({COMMON}, received_sim_s REAL, line_id TEXT, plc_counter_raw INTEGER, reset_epoch INTEGER, counter_delta INTEGER, accepted_good INTEGER, rejected INTEGER, reconciliation_status TEXT);
CREATE TABLE inventory_buffers ({COMMON}, station_id TEXT, material_qty INTEGER, wip INTEGER, capacity INTEGER, reservation INTEGER, pallets_waiting INTEGER);
CREATE TABLE transport_orders ({COMMON}, order_id TEXT, carrier TEXT, carrier_id TEXT, payload_id TEXT, qty INTEGER, source_loc TEXT, destination TEXT, prerequisites TEXT, state TEXT,
    issued_at REAL, ack_at REAL, completed_at REAL, correlation_id TEXT, idempotency_key TEXT, attempt INTEGER, dispatch_mode TEXT, reason TEXT);
CREATE TABLE transport_status ({COMMON}, order_id TEXT, idempotency_key TEXT, state TEXT, seq INTEGER, received_sim_s REAL, carrier_id TEXT, note TEXT);
CREATE TABLE fleet_status ({COMMON}, received_sim_s REAL, carrier_id TEXT, carrier_type TEXT, available INTEGER, battery_pct REAL, busy INTEGER);
CREATE TABLE electrical_events ({COMMON}, received_sim_s REAL, feeder_id TEXT, load_kw REAL, available_kw REAL, nameplate_kw REAL, overload INTEGER);
CREATE TABLE anomaly_incidents ({COMMON}, incident_id TEXT, opened_at REAL, closed_at REAL, detection_source TEXT, detector TEXT, signal TEXT, feature_snapshot TEXT, severity_rule TEXT, cause_hint TEXT);
CREATE TABLE decision_snapshots ({COMMON}, decision_id TEXT, incident_id TEXT, arm TEXT, db_snapshot_hash TEXT, input_features_json TEXT, available_actions TEXT, constraints TEXT,
    plan_gap REAL, projected_end_qty REAL, context_freshness TEXT);
CREATE TABLE jev_requests ({COMMON}, request_id TEXT, decision_id TEXT, provider TEXT, model_version TEXT, question_version TEXT, anonymized_payload_hash TEXT, sanitized_request_json TEXT, created_at REAL, status TEXT);
CREATE TABLE jev_responses ({COMMON}, request_id TEXT, raw_response_sanitized TEXT, selected_action TEXT, per_choice_probabilities TEXT, cause_class TEXT, cause_probabilities TEXT,
    needs_operator_p REAL, latency_ms REAL, error_code TEXT, token_usage TEXT, cost_estimate REAL, cached INTEGER, provider TEXT);
CREATE TABLE policy_decisions ({COMMON}, decision_id TEXT, arm TEXT, decided_by TEXT, proposed_action TEXT, safety_gate_result TEXT, blocked_reason TEXT, final_action TEXT, reviewed_by TEXT, command_id TEXT, cause_label TEXT);
CREATE TABLE command_execution ({COMMON}, command_id TEXT, decision_id TEXT, target TEXT, command TEXT, channel TEXT, request_at REAL, ack_at REAL, apply_at REAL, verify_at REAL,
    before_state TEXT, after_state TEXT, confirmed INTEGER, timeout INTEGER, virtual_only INTEGER NOT NULL DEFAULT 1);
CREATE TABLE unit_inspections ({COMMON}, line_id TEXT, unit_seq INTEGER, image_id TEXT, part_type TEXT, yolo_detections INTEGER, yolo_max_conf REAL, yolo_verdict TEXT,
    yolo_ms REAL, judge_provider TEXT, judge_model TEXT, disposition TEXT, probabilities TEXT, defect_present_p REAL, judge_latency_ms REAL, judge_cached INTEGER,
    judge_error TEXT, request_hash TEXT, state_sent TEXT, final_disposition TEXT, reinspect_result TEXT, pallet_id TEXT);
CREATE INDEX ix_unit ON unit_inspections(simulation_time);
CREATE UNIQUE INDEX ux_transport_idem ON transport_orders(run_id, episode_id, idempotency_key, attempt);
CREATE INDEX ix_eq ON equipment_events(received_sim_s);
CREATE INDEX ix_vis ON vision_events(received_sim_s);
CREATE INDEX ix_vis_pal ON vision_events(pallet_id);
CREATE INDEX ix_cnt ON production_counts(received_sim_s);
CREATE INDEX ix_fleet ON fleet_status(received_sim_s);
CREATE INDEX ix_el ON electrical_events(received_sim_s);
CREATE INDEX ix_qr ON qr_events(received_sim_s);
CREATE INDEX ix_net ON network_events(sent_at);
CREATE INDEX ix_buf ON inventory_buffers(simulation_time);
CREATE INDEX ix_ord ON transport_orders(state);
CREATE TRIGGER vision_immutable BEFORE UPDATE ON vision_events BEGIN SELECT RAISE(ABORT, 'vision_events are immutable raw events'); END;
"""

TRUTH_DDL = f"""
CREATE TABLE ground_truth ({COMMON}, fault_id TEXT, hidden_fault_type TEXT, target TEXT, fault_start REAL, fault_end REAL, true_repair_s REAL, params TEXT, truth_cause TEXT,
    feasible_actions TEXT, oracle_action TEXT, expected_outcome TEXT, safety_oracle TEXT);
CREATE TABLE incident_truth ({COMMON}, incident_id TEXT, injection_truth_hidden_from_jev TEXT);
CREATE TABLE truth_units ({COMMON}, line_id TEXT, unit_seq INTEGER, finished_at REAL, truly_defective INTEGER, accepted INTEGER, pallet_id TEXT, image_file TEXT, image_id TEXT, defect_type TEXT);
CREATE TABLE truth_states ({COMMON}, line_id TEXT, state TEXT, kw REAL, available_kw REAL, stable INTEGER);
CREATE TABLE unsafe_events ({COMMON}, command_id TEXT, decision_id TEXT, command TEXT, reason TEXT, authorized INTEGER);
"""


class _Store:
    DDL = ""

    def __init__(self, ctx: dict, path: str = ":memory:"):
        self.ctx = dict(ctx)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=MEMORY" if path == ":memory:" else "PRAGMA journal_mode=WAL")
        self.con.executescript(self.DDL)
        self._cols: dict[str, list[str]] = {}

    def cols(self, table: str) -> list[str]:
        if table not in self._cols:
            self._cols[table] = [r[1] for r in self.con.execute(f"PRAGMA table_info({table})")]
        return self._cols[table]

    def insert(self, table: str, sim_s: float, source: str = "SIMULATED", provenance: dict | str | None = None, **fields) -> None:
        row = dict(self.ctx, simulation_time=sim_s, wall_time=time.time(), source=source,
                   provenance=provenance if isinstance(provenance, str) or provenance is None else json.dumps(provenance), schema_version=SCHEMA_VERSION)
        for k, v in fields.items():
            row[k] = json.dumps(v, default=str) if isinstance(v, (dict, list)) else v
        cols = [c for c in self.cols(table) if c in row]
        self.con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])

    def query(self, sql: str, args=()) -> list[dict]:
        cur = self.con.execute(sql, args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def one(self, sql: str, args=()):
        r = self.con.execute(sql, args).fetchone()
        return r[0] if r else None

    def persist(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self.con.commit()          # an open write transaction would block the backup API indefinitely
        dst = sqlite3.connect(path)
        self.con.backup(dst)
        dst.close()


class OpsDB(_Store):
    DDL = OPS_DDL


class TruthStore(_Store):
    DDL = TRUTH_DDL
