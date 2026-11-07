# SF AI OS v3 — model, contracts and assumptions

Everything below is a **synthetic model assumption** unless marked MEASURED. All values are editable in the Factory
Setup screen (`PUT /api/v3/factory`) and recorded per run in `sim`/manifest files. No physical PLC, OPC UA server,
MQTT broker or AMR is ever addressed; the PLC and the broker are in-process simulators.

## 1. Plant (sfaic2/v3/plant.py)

| Item | Value | Note |
|---|---|---|
| Shift | 8 h (28,800 s), 1 episode = 1 planned shift | |
| Main line | C/T 30 s (lognormal CV 0.05), yield 0.97, run 26 kW / idle 6 kW, restart 120 s | |
| Sub (backup) line | C/T 42 s (CV 0.06), yield 0.95, run 20 kW / idle 3 kW, cold start 600 s, initially off | |
| Daily target | load × main-line nominal good capacity; low 0.75, medium 0.85, high 0.95 | S10 sets 1.05 × (main + sub) capacity |
| Feeder | nameplate 60 kW, aux 5 kW; a load above the *true* available kW trips feeder protection: running lines stop 600 s, main auto-restarts, sub locked out 1,800 s | |
| Repair | crew response 60 s; true repair time drawn per seed from the fault catalogue range; stabilisation (diagnostic) window 300 s after repair / maintenance | |
| Restart inside the stabilisation window | re-fault with probability 0.6 (`recurrence_p_unstable`), repair 0.5 × catalogue minimum | dominant assumption behind the safety-gate effect; varied in the sensitivity run |
| Tool change / quality fix | 1,200 s maintenance removes C/T drift and defect surge | |
| Camera gate | every unit gets one raw YOLO JSON. MEASURED: verbatim YOLO11n outputs on 129 held-out MVTec AD images (`results/vision/replay_raw.jsonl`), picked by the unit's hidden defect state; reject = any detection | held-out recall 0.753, precision 1.00 |
| Pallet / outlet | 40 accepted units per pallet, QR scanned at pallet close; 3 pallets fit at a line outlet before the line blocks | |
| Network | PLC sampling 10 s, median delay 60 ms ± 20 ms; stale after 25 s; fault windows override delay / loss / duplication / reordering per channel (PLC, MQTT) | |
| Fleet | 2 line AMRs + 1 reserve AMR from another area (all 1.2 m/s), no manned forklift; 120 m route, 40 s handling | |
| AMR energy | battery 1.0 kWh; driving 0.40 kW; standby 0.05 kW (always on); an idle AMR below 50 % with no waiting order goes to the charger, below 25 % it must go after its trip; charger 0.6 kW grid-side, efficiency 0.9; charging stops early only if an order waits, no other AMR of that type is free and the battery covers 25 % + 3 trips. Charger power is part of the feeder load and factory kWh; the recharge still owed at shift end (battery deficit ÷ efficiency) is added to the factory total | all assumptions, editable in 공장 설정 |

Fault catalogue (operational side sees the code and the catalogue ETA only; the true duration is evaluator data):

| Code | Meaning | Catalogue ETA | True range |
|---|---|---|---|
| E101 | material jam | 600 s | 360–900 s |
| E201 | guard / sensor fault | 1,800 s | 900–3,600 s |
| E301 | servo drive failure | 7,200 s | 5,400–9,000 s |
| E302 | backup hydraulic failure | 10,800 s | 9,000–14,400 s |

## 2. Scenarios S1–S15 (sfaic2/v3/config.py — `build_scenario(sid, load, seed)`)
S1 normal · S2 main fault, fast recovery (E101) · S3 main long fault (E301) · S4 main and sub down · S5 main recovers
mid-shift while the sub runs (E201, 60–80 min) · S6 batch-count transport · S7 MQTT delay 4.5 s / duplicates 25 % /
reorder 20 % · S8 QR mismatch 50 % + camera drops 15 % + PLC delay 3.5 s · S9 C/T drift ×1.30 + feeder limited to 44 kW
· S10 infeasible plan · S11 C/T drift ×1.35 · S12 defect surge to 30 % · S13 PLC double counting 35 % + counter reset ·
S14 AMR fleet down 2 h · S15 main long fault + PLC delay + feeder limited to 40 kW.
The first incident starts 1.0–1.5 h into the shift (seeded). The same (sid, load, seed) gives the same fault sequence
and per-unit random streams for every arm (common random numbers).

**Random course `RND` (operations center default).** The UI has no scenario picker. Each run draws 1–4 events from the
course number (seed): main fault with a random catalogue code, sub fault, C/T drift, defect surge, PLC or MQTT network
degradation, AMR outage, feeder limit, PLC counter anomaly, vision/QR mismatch — random start in [30 min, T − 60 min],
random duration and severity. The same course number reproduces the same course for every mode (paired design); the
event list is evaluation truth and never reaches the decision layer.

## 3. Decision snapshot (sfaic2/v3/snapshot.py)
Built only from the operational DB rows with `received_sim_s <= now` (tested by `test_snapshot_uses_only_rows_received_up_to_now`).
Computed by the program: good so far (raw PLC counter − camera rejects for non-validated arms; camera-accepted units
for validated arms), gap, required rate, C/T estimate (PLC counter slope or camera timestamps, only while RUNNING),
yield estimate (camera, since the last repair/maintenance), recovery ETA (catalogue − elapsed + stabilisation), per-action
forecast over the rest of the shift (good units, shortfall, defects shipped at the measured camera recall, kWh, peak kW,
startup), an OR-Tools CP-SAT upper bound of achievable good units (10-min main/sub on/off slots under availability and
the power limit), feasible actions with the constraint that removed each other action, detector incidents and evidence
flags. AMR outages are assumed to last at least 30 min in the forecast (longer once the outage has already lasted longer) unless the reserve AMR is requested.

## 4. Jev request (sfaic2/v3/jev.py, question set `qs_v3_plan_action_2026-10-17`)
`state` = plan numbers, line states, ETA estimates, per-allowed-action forecasts, transport counts, open incident
classes, `allowed_actions`; validated arms add evidence flags; R4 adds feeder headroom and network statistics.
Numbers are bucketed so equal situations reuse one answer. Questions: `preferred_action` (choice; criteria generated
from the allowed actions only, each carrying the program forecast), `cause_class` (choice: EQUIPMENT, PROCESS_DRIFT,
QUALITY, NETWORK, VISION_QR, COUNTER, TRANSPORT, POWER, PLAN, NONE, UNKNOWN), `needs_operator` (noul). A response is
rejected if the chosen action is not allowed, probabilities do not sum to 1, or fields are malformed; any failure falls
back to ESCALATE_HUMAN and is logged.

## 5. Arms (sfaic2/v3/control.py)
R0 fixed-threshold detection + fixed bypass rules · R1 ML/forecast detection + the same rules · R2 = R1 with Jev
choosing among the feasible actions (nothing else changes) · R3 = R2 + evidence validation + full safety gate +
validated idempotent transport · R4 = R3 + live feeder headroom + network context + adaptive MQTT timeouts · R5
deterministic planner (exhaustive forecast argmax + CP-SAT bound) on R4 information. Ablations remove one information
group from R4's Jev state (plan, C/T, network, electrical, vision/QR) or use basic transport.
Basic gate (R0–R2): feasible-list check; main restart as soon as the PLC reports READY. Full gate (R3+): fresh evidence
for the target line, interlock diagnostic OK, no motion when both lines are down, peak kW within the known limit,
no pallet moves on inconsistent QR/count evidence. A blocked action becomes ESCALATE_HUMAN; the simulated operator
answers after 600 s with the planner's choice under the full gate.

## 6. Virtual MQTT transport (sfaic2/v3/mqtt.py)
Topics `sfaios/v1/virtual/transport/{orders,ack,status,fleet}`. States CREATED → PUBLISHED → ACKNOWLEDGED → EXECUTING →
COMPLETED / FAILED / TIMED_OUT. BASIC = count-triggered FIFO pickup, re-dispatch as a new order on ack timeout (max 3).
VALIDATED = per pallet QR decoded + product/location match, camera-confirmed quantity (or manual recount after a 300 s
hold), destination capacity, carrier with enough battery, one idempotency key per transfer reused by retries, fleet
suppresses duplicate keys, statuses applied in sequence order. `transport_orders` has a UNIQUE(run, episode, key, attempt).

## 7. Metrics (sfaic2/v3/episode.py) and cost weights
PAR = min(1, Q_good/Q_plan) and raw attainment; on-time completion; unsafe-authorisation rate = forbidden proposals
that were authorised / forbidden proposals (safety oracle on the simulator truth; NA when none); decision cost = w1·
shortfall units + w2·unnecessary stop min + w3·defects shipped + w4·transport errors + w5·action delay min + w6·kWh.
Weight profiles (assumed monetary units, sensitivity reported, never tuned on results): balanced (10, 2, 25, 20, 1, 0.2),
quality (10, 2, 100, 20, 1, 0.2), energy (10, 2, 25, 20, 1, 1.0). Plus detection P/R/F1, MTTD, MTTR, cause macro-F1,
replay-oracle regret, escalations, MQTT validity/duplicates/wrong pallets/premature dispatches/timeouts/ack latency,
kWh, kWh per good unit, peak kW, overloads, stale decisions, Jev latency p50/p95/p99, tokens and cost.

## 8. Camera, per-part judgment and evidence logging (added 2026-10-17)
* **Camera modes.** `live` (default in the UI): for every finished part the program draws, at random, a held-out MVTec AD
  image of the part's hidden condition and runs the already-trained YOLO11n on it now (Apple MPS, ~3–15 ms). `replay`
  (batch experiments): the stored verbatim output of the same model on the same image is used; the verification screen
  re-runs the model and confirmed identical boxes on every image checked. No new YOLO training is done in v3.
* **Defective-image share.** By default a part is defective with the line's nominal rate (3 %); the live screen and the
  per-part study can set 10 / 30 / 50 % so that good and defective images are visibly mixed. That share is treated as
  known incoming-lot quality: line quality alarms fire only on rejects above share × measured recall.
* **Per-part judgment (`sfaic2/v3/unit_judge.py`).** State sent to Jev: part type, YOLO detections (count, max confidence,
  box area fractions), the detector's measured recall and false-alarm rate, line reject rate, prior defect rate and the
  program-computed P(defect | evidence) and expected cost per disposition (defect shipped 25, good part scrapped 10,
  re-inspection 2). Jev chooses ACCEPT / REJECT / REINSPECT; REINSPECT is resolved by a simulated operator after 120 s.
  Jev never sees the image or its file name (Jev is text-only).
* **No label leakage.** Image file names encode the ground-truth defect type, so the operational DB stores only an opaque
  image id (first 16 hex of the image sha256); the `path` field of the raw YOLO JSON is replaced by that id (the only
  change made to the raw JSON). File names live only in the evaluator truth store.
* **Decision log (`sfaic2/v3/decision_log.py`).** Rebuilt from DB rows written at decision time: situation, every option
  with program forecast and feasibility reason, the exact Jev instructions and option texts sent, Jev probabilities,
  resolved model, latency, tokens, cost, safety-gate result, virtual commands, and (review only) good parts 30 min later.

## 9. Cumulative results store (sfaic2/v3/live_results.py, added 2026-10-17)

Every finished live shift and every batch run appends one row to `results/v3/live_runs/live_runs.sqlite` (local only,
git-ignored); `runs.csv`, `run_events.csv`, `stats_by_group.csv` and `paired.csv` are regenerated from the whole DB.
Each row carries provenance (git commit, factory-config hash, YOLO weights hash, resolved Jev model) and a data-quality
flag: a real-Jev run in which any decision fell back (budget cap, API error) is stored but excluded from statistics.
Groups never mix rules, mock Jev and real Jev. Paired contrasts follow the paper hypotheses (R2−R1, R4−R3, R4−R1, R3−R2,
R1−R0, R5−R4) on identical course numbers within a load: seed-cluster bootstrap CI, McNemar exact p (target met) or
bootstrap p, Holm within metric × provider, and the pairs needed for 80 % power at the observed effect (Connor 1987 for
McNemar, paired normal approximation otherwise). Batches: `python -m sfaic2.v3.live_results --n 30 --provider mock|typesafe
--budget 1.0` or the 누적 결과 screen (mock runs in parallel processes; real Jev uses one shared call budget and cache).
