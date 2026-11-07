# Smart Factory AI Operations Center — research software and data (simulation only)

스마트팩토리 AI 운영 센터 연구용 소프트웨어와 결과 데이터입니다. 가상 공장(가상 PLC·가상 MQTT)에서 YOLO11n(1차 판정)과
TypeSafe Jev(최종 판단)로 제품 검사와 라인 운영을 자동화하고, 무작위 교란 시나리오에서 생산계획·안전·전력을 짝비교로
측정합니다. **실제 설비는 제어하지 않으며 비상정지 시스템이 아닙니다.**

Research software for a closed-loop supervisory "operations center" of a simulated smart factory. Deterministic code computes plan
progress and per-action forecasts from a provenance-tracked event database; a structured decision model (TypeSafe Jev System One,
remote API) chooses only among program-validated feasible actions and makes the final disposition of each part after a YOLO11n first
judgment; an independent safety gate decides what reaches a virtual PLC and a virtual MQTT fleet of AMRs. FastAPI + React, runs
locally (developed on a MacBook Pro M5, 16 GB).

> This repository is the public release of the research code and the stored results. A related article is under review; citation
> details will be added on publication.

## What is here

| path | content |
|---|---|
| `sfaic2/v3/` | plant model (SimPy), random scenarios, virtual PLC, MQTT AMR fleet, decision snapshot, Jev client, control modes R0–R5, safety gate, metrics, results store, statistics, supplementary experiments |
| `apps/api/`, `apps/web/` | FastAPI backend and React operations-center UI (Korean) |
| `data/results/` | the stored results (CSV/JSON with checksums) — see `data/results/README.md` |
| `docs/v3/RESULTS_*.md` | result reports generated from the stored runs |
| `docs/v3/MODEL_ASSUMPTIONS.md` | every plant, energy and decision assumption |
| `tests/` | unit, integration and safety tests |

Operating modes, compared on identical random scenarios: **R0** fixed rules · **R1** ML detection + fixed rules · **R2** + Jev ·
**R3** + evidence validation and safety gate · **R4** full system (+ power/network context) · **R5** deterministic planner (reference).

## Main results (simulation, real Jev `jev-1.13.0`)

210 random disruption scenarios × 2 load levels = 420 pairs, all six modes on every pair (1,260 real-Jev shifts, 32,226 calls,
0 errors, 0 fallbacks). Paired differences, 95 % CI from a scenario-clustered bootstrap, Holm-adjusted p
(`data/results/confirmatory/paired.csv`, `docs/v3/RESULTS_random_courses.md`):

| comparison | plan completion | unsafe commands authorized / shift | kWh per good part |
|---|---|---|---|
| R4 − R1 | +0.114 [0.083, 0.150], p < 0.001 | −2.53 [−2.84, −2.25] | −0.014 |
| R2 − R1 | +0.002 [−0.021, 0.031], n.s. | −0.34 [−0.46, −0.23] | −0.003 |
| R3 − R2 | +0.079 [0.050, 0.110], p < 0.001 | −1.90 [−2.15, −1.66] | −0.009 |
| R4 − R3 | +0.033 [0.017, 0.052], p = 0.002 | −0.30 [−0.41, −0.21] | −0.002 |

Robustness (`docs/v3/RESULTS_supplement.md`, `data/results/supplement/`): with the repeat-fault probability after an unstable restart
set to 0.0 / 0.3 / 0.6, the R4 − R1 plan-completion gain was +0.033 (n.s.) / +0.067 / +0.117 on the same 120 pairs, while the
reductions in unsafe commands and kWh per good part held at every value. Identical Jev requests sent again without a cache got the
same action in 85 % of repeats. Unfavourable results are reported as found in the result reports.

## Run locally

```bash
make install                 # uv venv (.venv, Python 3.12) + python deps + web deps
cp .env.example .env         # TYPESAFE_API_KEY here (local only, never committed); JEV_ENABLED=false for offline work
make api && make web         # http://localhost:5173
make test                    # backend tests;  cd apps/web && npx vitest run
```

Reproduce the results (real Jev needs your own key and costs a few USD; mock Jev is free and is a software check only):

```bash
make v3-batch                # random scenarios × 6 modes × 2 loads with mock Jev (parallel, no API cost)
make v3-batch-real           # the same with the real Jev (JEV_ENABLED=true, --budget USD cap)
make v3-report               # CSVs + docs/v3/RESULTS_random_courses.md from the stored runs
python -m sfaic2.v3.supplement sensitivity --values 0.0,0.3 --n 60 --budget 1.5
python -m sfaic2.v3.supplement retest --k 300 --repeats 2 --budget 0.2
python -m sfaic2.v3.supplement report        # docs/v3/RESULTS_supplement.md
python scripts/export_results_data.py        # copy the stored results into data/results/
```

The camera input replays verbatim YOLO11n output on public MVTec AD images. To rebuild the detector, download MVTec AD yourself
(CC BY-NC-SA 4.0, non-commercial) and run `make factory-vision`; images and weights are not distributed here.

## Safety and research-integrity invariants

* The backend refuses to start unless `SIMULATION_MODE=true` and `CONTROL_TARGET=VIRTUAL_PLC_ONLY`; no code connects to real
  equipment (no OPC UA write, MQTT broker publish, Modbus or IP targets).
* Ground truth (random events, true defects, image labels) lives in an evaluator-only store; image ids are opaque hashes.
* Jev only chooses among feasible options with program-computed forecasts; it never executes anything — the safety gate does.
* Rules, mock Jev and real Jev results are never pooled; a real-Jev run with any API error or fallback is kept but excluded.
* The API key stays in the local `.env`; it is never written to results, logs or the UI.

Data licences: see `LICENSE_NOTICE.md` (MVTec AD CC BY-NC-SA 4.0, AI4I 2020 CC BY 4.0, Ultralytics YOLO11 AGPL-3.0).
