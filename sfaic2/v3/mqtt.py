"""Virtual MQTT intralogistics: broker topics, AMR fleet (line AMRs + reserve AMR) and the pallet transport dispatcher.

Topics (virtual only): sfaios/v1/virtual/transport/orders | .../ack | .../status | .../fleet. Every message goes through
the plant's MQTT channel (delay / loss / duplication / reordering) and is logged in network_events.

Dispatch modes
* BASIC     — count-triggered: when the raw PLC counter advanced by one pallet quantity, order the next predicted pallet;
              no QR/quality/destination/battery checks; ack timeout → re-publish as a NEW order (no idempotency key).
* VALIDATED — per pallet: QR decoded + product/location match, camera-confirmed accepted units == pallet qty, quality
              released, destination capacity, carrier available with enough battery; one idempotency key per pallet
              transfer, retries reuse it, the fleet suppresses duplicates; status messages applied in sequence order.
Ack timeout: fixed (BASIC/VALIDATED) or adaptive to observed MQTT latency (network-aware context, arm R4).
States: CREATED → VALIDATED → PUBLISHED → ACKNOWLEDGED → EXECUTING → COMPLETED / FAILED / TIMED_OUT.
"""
from __future__ import annotations

from collections import deque

from .config import FactoryV3
from .plant import Plant, _urand

TOPIC = "sfaios/v1/virtual/transport"


class Carrier:
    def __init__(self, cid: str, ctype: str, speed: float):
        self.cid, self.ctype, self.speed = cid, ctype, speed
        self.busy, self.battery, self.charging = False, 100.0, False
        self.charge_kw = 0.0
        self.busy_s = 0.0
        self.trip: dict | None = None   # for visualisation: line, phase (to_outlet | to_dest), phase start, leg duration


class Fleet:
    def __init__(self, plant: Plant):
        self.p, self.env, fs = plant, plant.env, plant.f.fleet
        self.carriers = [Carrier(f"AMR-{i+1}", "AMR", fs.amr_speed_mps) for i in range(fs.n_amr)] + \
                        [Carrier(f"AMR-R{i+1}", "AMR_RESERVE", fs.amr_speed_mps) for i in range(fs.n_reserve_amr)]
        self.queue: deque = deque()
        self.seen_keys: set = set()
        self.seen_msgs: set = set()
        self.picked: set = set()
        self.stats = dict(received=0, executions=0, duplicate_executions=0, duplicates_suppressed=0, completed=0, failed=0, premature=0,
                          wrong_pallet=0, battery_violations=0, delivered_units=0, delivered_defects=0, queue_wait_s=0.0)
        self.delivered_pallets = 0
        self.charges = 0
        self.wake = self.env.event()
        plant.fleet = self
        self.env.process(self._worker())
        self.env.process(self._status_loop())

    def amr_down(self) -> bool:
        return any(f.kind == "AMR_DOWN" and f.at_s <= self.env.now < f.at_s + f.duration_s for f in self.p.faults)

    def available(self, c: Carrier) -> bool:
        return not c.busy and not c.charging and not (c.ctype == "AMR" and self.amr_down())

    def _status_loop(self):
        fs = self.p.f.fleet
        while True:
            for c in self.carriers:
                if not c.charging:
                    c.battery = max(0.0, c.battery - fs.idle_kw * 10 / 3600 / fs.battery_kwh * 100)
                if not c.busy and not c.charging and (c.battery < fs.battery_min_pct or (c.battery < fs.opportunity_pct and not self.queue)):
                    self.env.process(self._charge(c))
                self.p.send("MQTT", "fleet_status", self.env.now, f"{TOPIC}/fleet", "FLEET_MGR", "DISPATCHER", carrier_id=c.cid, carrier_type=c.ctype,
                            available=int(self.available(c)), battery_pct=round(c.battery, 1), busy=int(c.busy))
            yield self.env.timeout(10)

    def on_order(self, msg: dict, dup: bool):
        self.stats["received"] += 1
        key = msg.get("idempotency_key")
        if key is not None:
            if key in self.seen_keys:
                self.stats["duplicates_suppressed"] += 1
                self.dispatcher_status(msg, "DUPLICATE_IGNORED")
                self.dispatcher_ack(msg)
                return
            self.seen_keys.add(key)
        else:  # no idempotency key: a naive fleet executes every copy it receives
            if msg["order_id"] in self.seen_msgs:
                self.stats["duplicate_executions"] += 1
            self.seen_msgs.add(msg["order_id"])
        self.dispatcher_ack(msg)
        self.queue.append((self.env.now, msg))
        if not self.wake.triggered:
            self.wake.succeed()

    def dispatcher_ack(self, msg):
        self.p.disp.deliver_ack(msg)

    def dispatcher_status(self, msg, state, note=""):
        self.p.disp.deliver_status(msg, state, note)

    def _worker(self):
        while True:
            if not self.queue:
                self.wake = self.env.event()
                yield self.wake | self.env.timeout(5)
                continue
            started = False
            for i, (t_in, msg) in enumerate(list(self.queue)):
                want = msg.get("carrier_id")
                ctype = msg.get("carrier_type", "AMR")
                cands = [c for c in self.carriers if (c.cid == want if want else c.ctype == ctype) and self.available(c)]
                if not cands and self.p.disp.use_reserve:   # reserve AMR called in: take over orders stuck on unavailable line AMRs
                    cands = [c for c in self.carriers if c.ctype == "AMR_RESERVE" and self.available(c)]
                if not cands:
                    continue
                c = cands[0]
                del self.queue[i]
                self.stats["queue_wait_s"] += self.env.now - t_in
                self.env.process(self._execute(c, msg))
                started = True
                break
            if not started:
                yield self.env.timeout(5)

    def _execute(self, c: Carrier, msg: dict):
        fs = self.p.f.fleet
        c.busy = True
        t0 = self.env.now
        self.stats["executions"] += 1
        if c.battery < fs.battery_min_pct:
            self.stats["battery_violations"] += 1
        self.dispatcher_status(msg, "EXECUTING")
        leg = fs.route_m / c.speed + fs.handling_s / 2
        drive = fs.route_m / c.speed * fs.motion_kw / 3600 / fs.battery_kwh * 100   # battery % per driven leg
        c.trip = dict(line=msg["line_role"], phase="to_outlet", t0=self.env.now, leg=leg, loaded=False)
        yield self.env.timeout(leg)
        c.trip = dict(line=msg["line_role"], phase="to_dest", t0=self.env.now, leg=leg, loaded=False)
        line = self.p.lines.get(msg["line_role"])
        if line and msg["payload_id"].startswith("FIFO@"):
            pal = line.pick_pallet(line.outlet[0].pallet_id) if line.outlet else None
        else:
            pal = line.pick_pallet(msg["payload_id"]) if line else None
        c.battery -= drive
        if pal is None:
            if msg["payload_id"] in self.picked or msg.get("_dup_copy"):
                self.stats["duplicate_executions"] += 1
                note = "pallet already moved (duplicate order)"
            else:
                self.stats["premature"] += 1
                note = "pallet not complete at outlet (premature dispatch)"
            self.stats["failed"] += 1
            yield self.env.timeout(leg)
            c.battery -= drive
            self.dispatcher_status(msg, "FAILED", note)
        elif c.battery <= 2.0:
            self.stats["failed"] += 1
            line.outlet.insert(0, pal)
            self.dispatcher_status(msg, "FAILED", "battery depleted en route")
        else:
            self.picked.add(pal.pallet_id)
            c.trip["loaded"] = True
            yield self.env.timeout(leg)
            c.battery -= drive
            wrong = not pal.qr_ok
            self.stats["wrong_pallet"] += int(wrong)
            self.stats["completed"] += 1
            self.stats["delivered_units"] += len(pal.units)
            self.stats["delivered_defects"] += sum(1 for _, d in pal.units if d)
            self.delivered_pallets += 1
            self.dispatcher_status(msg, "COMPLETED", "wrong label delivered" if wrong else "")
        c.busy = False
        c.trip = None
        c.busy_s += self.env.now - t0
        if c.battery < fs.battery_min_pct:
            self.env.process(self._charge(c))
        if not self.wake.triggered:
            self.wake.succeed()

    def _charge(self, c: Carrier):
        """Drive to the charging station and recharge at charger_kw (grid side) until full. The grid draw is part of the
        feeder load and factory energy. Charging is cut short only when an order is waiting, no other carrier of the
        same type is free, and the battery already covers the minimum plus three trips."""
        fs = self.p.f.fleet
        c.charging = True
        self.charges += 1
        step = 10.0
        while c.battery < 100.0:
            c.charge_kw = fs.charger_kw
            yield self.env.timeout(step)
            c.battery = min(100.0, c.battery + fs.charger_kw * fs.charger_eff * step / 3600 / fs.battery_kwh * 100)
            waiting = any(m.get("carrier_type", "AMR") == c.ctype for _, m in self.queue)
            others = any(o is not c and o.ctype == c.ctype and self.available(o) for o in self.carriers)
            if waiting and not others and c.battery >= fs.battery_min_pct + 3 * fs.trip_pct():
                break
        c.charging, c.charge_kw = False, 0.0
        if not self.wake.triggered:
            self.wake.succeed()


class Dispatcher:
    def __init__(self, plant: Plant, ops, ctx: dict, mode: str, adaptive_timeout: bool = False, fixed_timeout_s: float = 5.0):
        self.p, self.env, self.ops, self.ctx = plant, plant.env, ops, ctx
        self.mode, self.adaptive, self.fixed_timeout = mode, adaptive_timeout, fixed_timeout_s
        self.f: FactoryV3 = plant.f
        self.orders: dict[str, dict] = {}
        self.by_pallet: dict[str, str] = {}
        self.dispatched_counter = {r: 0 for r in plant.lines}
        self.next_pallet = {r: 1 for r in plant.lines}
        self.use_reserve = False
        self.held: dict[str, float] = {}
        self.transfer_n: dict[str, int] = {}
        self.confirmed: set = set()
        self._fv: tuple | None = None
        self.n = 0
        self.stats = dict(published=0, retries=0, timeouts=0, acks=0, ack_latency_ms=[], status_regressions=0, holds=0, orders=0)
        plant.disp = self
        self.env.process(self._loop())

    # ---------------------------------------------------------------------- observed inputs (DB, received <= now)
    def _fleet_view(self) -> dict:
        now = self.env.now
        if self._fv and self._fv[0] == now:
            return self._fv[1]
        lo = self._fv[0] if self._fv else -1.0
        view = dict(self._fv[1]) if self._fv else {}
        for r in self.ops.query("SELECT carrier_id, carrier_type, available, battery_pct, busy, received_sim_s AS t FROM fleet_status WHERE received_sim_s > ? AND received_sim_s <= ? ORDER BY received_sim_s", (lo, now)):
            view[r["carrier_id"]] = r
        self._fv = (now, view)
        return view

    def _pick_carrier(self, validated: bool) -> tuple[str | None, str]:
        view = self._fleet_view()
        ctype = "AMR_RESERVE" if self.use_reserve else "AMR"
        cands = [r for r in view.values() if r["carrier_type"] == ctype]
        if not validated:
            return (cands[0]["carrier_id"] if cands else None), ctype
        need = self.f.fleet.battery_min_pct + self.f.fleet.trip_pct()
        ok = [r for r in cands if r["available"] and not r["busy"] and r["battery_pct"] >= need and self.env.now - r["t"] <= 30]
        return (ok[0]["carrier_id"] if ok else None), ctype

    def ack_timeout_s(self) -> float:
        if not self.adaptive:
            return self.fixed_timeout
        rows = self.ops.query("SELECT delay_ms FROM network_events WHERE topic LIKE ? AND received_at <= ? AND dropped=0 ORDER BY sent_at DESC LIMIT 50", (f"{TOPIC}%", self.env.now))
        if len(rows) < 5:
            return self.fixed_timeout
        d = sorted(r["delay_ms"] for r in rows)
        p95 = d[int(0.95 * (len(d) - 1))] / 1000
        return max(self.fixed_timeout, 2.5 * p95 + 1.0)

    # ---------------------------------------------------------------------- publish / receive
    def _publish(self, o: dict, attempt: int):
        now = self.env.now
        o["attempt"], o["state"] = attempt, "PUBLISHED"
        self.stats["published"] += 1
        self.ops.insert("transport_orders", now, order_id=o["order_id"], carrier=o["carrier_type"], carrier_id=o.get("carrier_id"), payload_id=o["payload_id"], qty=o["qty"],
                        source_loc=o["source"], destination=o["destination"], prerequisites=o["preconditions"], state="PUBLISHED", issued_at=now, correlation_id=o["correlation_id"],
                        idempotency_key=o.get("idempotency_key") or f"NOKEY-{o['order_id']}", attempt=attempt, dispatch_mode=self.mode, reason=o["reason"])
        msg = dict(o)
        mid = self.p.next_msg("MQTT")
        deliveries = self.p.ch["MQTT"].transmit(now)
        if not deliveries:
            self.ops.insert("network_events", now, topic=f"{TOPIC}/orders", sender="DISPATCHER", receiver="FLEET_MGR", sent_at=now, received_at=None, delay_ms=None, dropped=1, duplicate=0, out_of_order=0, qos=1, msg_id=mid)
        for d, dup in deliveries:
            self.ops.insert("network_events", now, topic=f"{TOPIC}/orders", sender="DISPATCHER", receiver="FLEET_MGR", sent_at=now, received_at=now + d, delay_ms=round(d * 1000, 1),
                            dropped=0, duplicate=int(dup), out_of_order=int(d > 1.0), qos=1, msg_id=mid)
            self.env.process(self._later(d, lambda m=msg, du=dup: self.p.fleet.on_order(m, du)))
        self.env.process(self._watch_ack(o["order_id"], attempt))

    def _later(self, d, fn):
        yield self.env.timeout(d)
        fn()

    def deliver_ack(self, msg):
        now = self.env.now
        for d, dup in self.p.ch["MQTT"].transmit(now):
            self.ops.insert("network_events", now, topic=f"{TOPIC}/ack", sender="FLEET_MGR", receiver="DISPATCHER", sent_at=now, received_at=now + d, delay_ms=round(d * 1000, 1),
                            dropped=0, duplicate=int(dup), out_of_order=int(d > 1.0), qos=1, msg_id=f"ACK-{msg['order_id']}")
            self.env.process(self._later(d, lambda m=msg: self._on_ack(m)))

    def _on_ack(self, msg):
        o = self.orders.get(msg["order_id"])
        if o and o.get("ack_at") is None:
            o["ack_at"] = self.env.now
            self.stats["acks"] += 1
            self.stats["ack_latency_ms"].append((self.env.now - o["first_issued"]) * 1000)
            if o["state"] == "PUBLISHED":
                o["state"] = "ACKNOWLEDGED"

    def deliver_status(self, msg, state, note=""):
        now = self.env.now
        o = self.orders.get(msg["order_id"])
        if o is None:
            return
        o["seq"] = o.get("seq", 0) + 1
        seq = o["seq"]
        for d, dup in self.p.ch["MQTT"].transmit(now):
            self.ops.insert("transport_status", now, order_id=msg["order_id"], idempotency_key=msg.get("idempotency_key"), state=state, seq=seq, received_sim_s=now + d,
                            carrier_id=msg.get("carrier_id"), note=note)
            self.env.process(self._later(d, lambda s=state, q=seq, n=note: self._on_status(msg["order_id"], s, q, n)))

    RANK = {"PUBLISHED": 0, "ACKNOWLEDGED": 1, "DUPLICATE_IGNORED": 1, "EXECUTING": 2, "COMPLETED": 3, "FAILED": 3, "TIMED_OUT": 3}

    def _on_status(self, oid, state, seq, note):
        o = self.orders[oid]
        if state == "DUPLICATE_IGNORED":
            return
        if self.mode == "VALIDATED":
            if seq <= o.get("applied_seq", 0):
                return
            o["applied_seq"] = seq
        elif self.RANK.get(state, 0) < self.RANK.get(o["state"], 0):
            self.stats["status_regressions"] += 1
        o["state"] = state
        if state in ("COMPLETED", "FAILED"):
            o["completed_at"] = self.env.now
            o["note"] = note
        if state == "FAILED" and self.mode == "VALIDATED" and self.by_pallet.get(o["payload_id"]) == oid:
            self.transfer_n[o["payload_id"]] = self.transfer_n.get(o["payload_id"], 1) + 1   # new transfer, new key
            self.by_pallet.pop(o["payload_id"], None)

    def _watch_ack(self, oid, attempt):
        yield self.env.timeout(self.ack_timeout_s())
        o = self.orders.get(oid)
        if o is None or o.get("ack_at") is not None or o.get("attempt") != attempt:
            return
        self.stats["timeouts"] += 1
        if attempt >= 4:
            o["state"] = "TIMED_OUT"
            if self.mode == "VALIDATED" and self.by_pallet.get(o["payload_id"]) == oid:
                self.transfer_n[o["payload_id"]] = self.transfer_n.get(o["payload_id"], 1) + 1
                self.by_pallet.pop(o["payload_id"], None)
            return
        self.stats["retries"] += 1
        if self.mode == "VALIDATED":
            self._publish(o, attempt + 1)  # same order id + idempotency key
        elif o.get("redispatch", 0) < 3:  # naive re-dispatch: a brand-new order for the same pallet (capped at 3 per trigger)
            n = self._new_order(o["line_role"], o["payload_id"], o["qty"], reason="ACK_TIMEOUT_REDISPATCH", preconditions=o["preconditions"],
                                carrier_id=o.get("carrier_id"), ctype=o.get("carrier_type", "AMR"))
            n["redispatch"] = o.get("redispatch", 0) + 1
        else:
            o["state"] = "TIMED_OUT"

    def _new_order(self, role, payload_id, qty, reason, preconditions, carrier_id=None, ctype="AMR") -> dict:
        self.n += 1
        oid = f"ORD-{self.ctx['episode_id']}-{self.n:05d}"
        tn = self.transfer_n.setdefault(payload_id, 1)
        o = dict(schema_version="1.0", order_id=oid, idempotency_key=(f"{self.ctx['episode_id']}-{payload_id}-T{tn}" if self.mode == "VALIDATED" else None),
                 correlation_id=f"BATCH-{payload_id}", issued_sim_s=self.env.now, first_issued=self.env.now, carrier_type=ctype, carrier_id=carrier_id,
                 pallet_id=payload_id, payload_id=payload_id, product_id=self.f.plan.product_id, qty=qty, source=f"OUTLET_{role.upper()}", destination="INSPECTION_IN",
                 reason=reason, preconditions=preconditions, authorized_by="VirtualSafetyGate" if self.mode == "VALIDATED" else "COUNT_TRIGGER", line_role=role, state="CREATED")
        self.orders[oid] = o
        self.stats["orders"] += 1
        self._publish(o, 1)
        return o

    # ---------------------------------------------------------------------- dispatch loop
    def _loop(self):
        while True:
            yield self.env.timeout(5)
            if self.mode == "BASIC":
                self._basic()
            else:
                self._validated()

    def _basic(self):
        now = self.env.now
        q = self.f.fleet.pallet_qty
        for role, line in self.p.lines.items():
            r = self.ops.query("SELECT plc_counter_raw FROM production_counts WHERE line_id=? AND received_sim_s <= ? ORDER BY received_sim_s DESC LIMIT 1", (line.spec.line_id, now))
            if not r:
                continue
            raw = r[0]["plc_counter_raw"]
            if raw - self.dispatched_counter[role] >= q:
                pid = f"FIFO@OUTLET_{line.spec.line_id}"   # count-triggered: "take the next pallet at this outlet"
                self.dispatched_counter[role] += q
                cid, ctype = self._pick_carrier(False)
                self._new_order(role, pid, q, reason="PLC_COUNT_TRIGGER", preconditions=dict(plc_completed_count=raw, batch_target_count=q), carrier_id=cid, ctype=ctype)

    def _validated(self):
        now = self.env.now
        q = self.f.fleet.pallet_qty
        qr = self.ops.query("SELECT pallet_id, product_id, line_id, decode_quality, expected_location, actual_location, declared_qty, MAX(received_sim_s) AS t FROM qr_events "
                            "WHERE received_sim_s <= ? GROUP BY pallet_id ORDER BY t", (now,))
        delivered = sum(1 for o in self.orders.values() if o["state"] == "COMPLETED")
        for r in qr:
            pid = r["pallet_id"]
            if pid in self.by_pallet:
                continue
            role = "main" if r["line_id"] == self.p.lines["main"].spec.line_id else "sub"
            cam = (self.ops.one("SELECT COUNT(DISTINCT unit_seq) FROM vision_events WHERE pallet_id=? AND received_sim_s <= ?", (pid, now)) or 0) + \
                  (self.ops.one("SELECT COUNT(*) FROM unit_inspections WHERE pallet_id=? AND reinspect_result='GOOD_CONFIRMED'", (pid,)) or 0)
            plc = self.ops.query("SELECT plc_counter_raw FROM production_counts WHERE line_id=? AND received_sim_s <= ? ORDER BY received_sim_s DESC LIMIT 1", (r["line_id"], now))
            pre = dict(plc_completed_count=plc[0]["plc_counter_raw"] if plc else None, batch_target_count=q, camera_accepted=cam,
                       qr_match=(r["decode_quality"] == "OK" and r["product_id"] == self.f.plan.product_id and r["actual_location"] == r["expected_location"]),
                       quality_released=True, destination_capacity_ok=delivered < self.f.fleet.dest_capacity_pallets, route_available=True)
            pre["count_confirmed"] = cam >= q or pid in self.confirmed
            if not pre["qr_match"] or not pre["count_confirmed"]:
                if pid not in self.held:
                    self.held[pid] = now
                    self.stats["holds"] += 1
                    self.env.process(self._recheck(pid, role))
                continue
            cid, ctype = self._pick_carrier(True)
            pre["carrier_available"] = cid is not None
            if cid is None:
                continue
            self.by_pallet[pid] = self._new_order(role, pid, q, reason="PLC_BATCH_COMPLETED", preconditions=pre, carrier_id=cid, ctype=ctype)["order_id"]

    def _recheck(self, pid: str, role: str):
        """Hold → re-measure: an operator relabels / rescans after 300 s; the new QR scan is a fresh observation."""
        yield self.env.timeout(300)
        line = self.p.lines[role]
        pal = next((x for x in line.outlet if x.pallet_id == pid), None)
        if pal is not None:
            if not pal.qr_ok:
                pal.qr_ok, pal.qr_product, pal.qr_location = True, pal.product_id, f"OUTLET_{line.spec.line_id}"
                self.p.scan_qr(pal, _urand(self.p.seed, pid, "rescan"))
            if len(pal.units) == self.f.fleet.pallet_qty:
                self.confirmed.add(pid)   # manual recount confirms the declared quantity (camera dropped frames)
        self.held.pop(pid, None)

    def summary(self) -> dict:
        lat = sorted(self.stats["ack_latency_ms"])
        s = {k: v for k, v in self.stats.items() if k != "ack_latency_ms"}
        s.update(ack_latency_p50_ms=lat[len(lat) // 2] if lat else None, ack_latency_p95_ms=lat[int(0.95 * (len(lat) - 1))] if lat else None,
                 completed_orders=sum(1 for o in self.orders.values() if o["state"] == "COMPLETED"), failed_orders=sum(1 for o in self.orders.values() if o["state"] == "FAILED"),
                 timed_out_orders=sum(1 for o in self.orders.values() if o["state"] == "TIMED_OUT"))
        return s
