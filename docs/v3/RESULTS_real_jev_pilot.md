# SF AI OS v3 results — pilot

**TypeSafe Jev System One (jev-latest) — real calls.** Generated from `results/v3/real_jev/pilot/episodes.csv` (144 paired episodes, 0 replay-oracle episodes, 0 failed episodes). Seeds: [0, 1, 2]; loads: ['medium']; scenarios: ['S10', 'S11', 'S12', 'S13', 'S14', 'S15', 'S2', 'S3', 'S4', 'S5', 'S8', 'S9'].

## T1_provenance

| source | nature | used_for | licence |
|---|---|---|---|
| Plant / line / fleet / feeder model | SYNTHETIC (SimPy, documented assumptions) | all episodes | — |
| Camera gate raw JSON | replay:MVTEC_AD/YOLO11n | vision_events.raw_json (verbatim YOLO11n output replayed per unit) | MVTec AD CC BY-NC-SA 4.0 (non-commercial) |
| Fault catalogue E101/E201/E301/E302 | SYNTHETIC catalogue; true repair times drawn per seed (hidden) | ETA estimate vs truth | — |
| Decision provider | TypeSafe Jev System One (jev-latest) — real calls | arms R2–R4 | TypeSafe terms |

## T2_parameters

| parameter | value |
|---|---|
| shift_s | 28800 |
| loads (target = load × main capacity) | {"low": 0.75, "medium": 0.85, "high": 0.95} |
| main CT / yield / run kW | 30.0 s / 0.97 / 26.0 |
| sub CT / yield / run kW / startup | 42.0 s / 0.95 / 20.0 / 600.0 s |
| feeder nameplate / aux kW | 60.0 / 5.0 |
| pallet qty / outlet buffer | 40 / 3 |
| AMR / AFL | 2 @ 1.2 m/s / 1 @ 0.8 m/s |
| PLC sample / stale after | 10.0 s / 25.0 s |
| decision interval / human response | 60.0 s / 600.0 s |
| recurrence p if restarted unstable | 0.600 |
| fault catalogue ETA (s) | {"E101": 600.0, "E201": 1800.0, "E301": 7200.0, "E302": 10800.0} |

## T3_baselines

| arm | label | detector | chooser | validated | gate | context | transport | adaptive_timeout | jev_info_removed |
|---|---|---|---|---|---|---|---|---|---|
| R0 | Rule + schedule | rule | rule | False | basic | False | BASIC | False | — |
| R1 | ML detection + deterministic policy | ml | rule | False | basic | False | BASIC | False | — |
| R2 | R1 + TypeSafe Jev | ml | jev | False | basic | False | BASIC | False | — |
| R3 | R2 + evidence validation + safety gate | ml | jev | True | full | False | VALIDATED | False | — |
| R4 | R3 + electrical/network context | ml | jev | True | full | True | VALIDATED | True | — |
| R5 | Planner (CP-SAT bound + exhaustive forecast) — reference | ml | planner | True | full | True | VALIDATED | True | — |
| R4-noplan | R4-noplan | ml | jev | True | full | True | VALIDATED | True | plan |
| R4-noct | R4-noct | ml | jev | True | full | True | VALIDATED | True | ct |
| R4-nonet | R4-nonet | ml | jev | True | full | True | VALIDATED | True | network |
| R4-noelec | R4-noelec | ml | jev | True | full | True | VALIDATED | True | electrical |
| R4-novisionqr | R4-novisionqr | ml | jev | True | full | True | VALIDATED | True | vision_qr |
| R4-basictransport | R4-basictransport | ml | jev | True | full | True | BASIC | False | — |

## T4_main_paired_primary

| contrast | hypothesis | family | metric | n_pairs | mean_a | mean_b | mean_diff | ci_low | ci_high | excludes_zero | cohens_dz | discordant_a_only | discordant_b_only | mcnemar_p | forbidden_a | allowed_a | forbidden_b | allowed_b | note | p_boot | p_holm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R2-R1 | H2: Jev increment | primary | par | 36 | 0.946 | 0.895 | 0.051 | 0.036 | 0.070 | True | 0.487 | — | — | — | — | — | — | — | — | — | — |
| R2-R1 | H2: Jev increment | primary | target_met | 36 | 0.694 | 0.500 | 0.194 | 0.083 | 0.333 | True | 0.484 | 7.000 | 0.000 | 0.016 | — | — | — | — | — | — | — |
| R2-R1 | H2: Jev increment | primary | unsafe_authorization_rate | 3 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | 126.000 | 126.000 | 170.000 | 170.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R2-R1 | H2: Jev increment | primary | cost_balanced | 36 | 803.169 | 1209.348 | -406.179 | -538.189 | -324.032 | True | -0.483 | — | — | — | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | primary | par | 36 | 0.985 | 0.997 | -0.012 | -0.016 | -0.006 | True | -0.278 | — | — | — | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | primary | target_met | 36 | 0.833 | 0.917 | -0.083 | -0.083 | -0.083 | True | -0.297 | 0.000 | 3.000 | 0.250 | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | primary | unsafe_authorization_rate | 3 | 0.000 | 0.211 | -0.211 | -0.273 | -0.176 | True | — | — | — | — | 32.000 | 0.000 | 38.000 | 8.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R4-R3 | H3: electrical/network context | primary | cost_balanced | 36 | 438.953 | 347.028 | 91.925 | 39.088 | 134.112 | True | 0.264 | — | — | — | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | par | 36 | 0.985 | 0.895 | 0.089 | 0.050 | 0.116 | True | 0.618 | — | — | — | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | target_met | 36 | 0.833 | 0.500 | 0.333 | 0.333 | 0.333 | True | 0.697 | 12.000 | 0.000 | 0.000 | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | unsafe_authorization_rate | 3 | 0.000 | 1.000 | -1.000 | -1.000 | -1.000 | True | — | — | — | — | 32.000 | 0.000 | 170.000 | 170.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | cost_balanced | 36 | 438.953 | 1209.348 | -770.395 | -995.062 | -488.198 | True | -0.661 | — | — | — | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | primary | par | 36 | 0.997 | 0.946 | 0.051 | 0.030 | 0.061 | True | 0.392 | — | — | — | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | primary | target_met | 36 | 0.917 | 0.694 | 0.222 | 0.083 | 0.333 | True | 0.527 | 8.000 | 0.000 | 0.008 | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | primary | unsafe_authorization_rate | 3 | 0.211 | 1.000 | -0.789 | -0.824 | -0.727 | True | — | — | — | — | 38.000 | 8.000 | 126.000 | 126.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R3-R2 | evidence validation + safety gate | primary | cost_balanced | 36 | 347.028 | 803.169 | -456.141 | -559.447 | -265.995 | True | -0.443 | — | — | — | — | — | — | — | — | — | — |

## T4b_by_arm

| arm | n | par | target_met | raw_attainment | good_end | shortfall | unsafe_authorization_rate_pooled | forbidden_proposed | forbidden_allowed | cost_balanced | cost_quality | cost_energy | defects_shipped | unnecessary_stop_min | action_delay_min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 36 | 0.895 | 0.500 | 0.992 | 850.917 | 88.111 | 1.000 | 170 | 170 | 1209.348 | 1740.598 | 1418.910 | 7.083 | 18.444 | 24.651 |
| R2 | 36 | 0.946 | 0.694 | 1.109 | 946.222 | 45.444 | 1.000 | 126 | 126 | 803.169 | 1396.919 | 1023.795 | 7.917 | 12.111 | 32.540 |
| R3 | 36 | 0.997 | 0.917 | 1.177 | 1000.222 | 5.444 | 0.211 | 38 | 8 | 347.028 | 913.694 | 567.867 | 7.556 | 7.556 | 33.374 |
| R4 | 36 | 0.985 | 0.833 | 1.148 | 977.222 | 14.889 | 0.000 | 32 | 0 | 438.953 | 986.869 | 655.497 | 7.306 | 4.667 | 43.957 |

## T5_detection

| arm | detection_precision | detection_recall | detection_f1 | false_alarms_per_h | mttd_s | n_events | macro_f1 | unknown_or_no_decision_rate |
|---|---|---|---|---|---|---|---|---|
| R1 | 0.386 | 0.653 | 0.583 | 0.191 | 331.824 | 51 | 0.351 | 0.078 |
| R2 | 0.389 | 0.653 | 0.588 | 0.184 | 331.824 | 51 | 0.335 | 0.078 |
| R3 | 0.451 | 0.810 | 0.598 | 0.194 | 512.251 | 51 | 0.547 | 0.000 |
| R4 | 0.503 | 0.917 | 0.674 | 0.184 | 289.524 | 51 | 0.603 | 0.000 |

## T6_decision_recovery

| arm | mttr_main_s | production_recovery_s | recovery_success | recurrences | escalations | escalations_warranted | escalation_precision | escalation_recall | gate_blocks | stale_decisions | decisions | operator_decisions | forecast_error_first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 5372.486 | 1964.486 | 1.000 | 3.444 | 2.139 | 1.056 | 0.316 | 0.667 | 0.000 | 0.000 | 34.000 | 1.389 | 132.425 |
| R2 | 5424.486 | 2848.486 | 1.000 | 2.750 | 12.528 | 2.139 | 0.170 | 0.667 | 0.000 | 0.000 | 25.750 | 6.556 | 37.119 |
| R3 | 5664.486 | 2952.486 | 1.000 | 0.000 | 5.556 | 2.611 | 0.265 | 1.000 | 0.000 | 0.167 | 14.083 | 4.750 | -16.214 |
| R4 | 5544.486 | 4384.486 | 1.000 | 0.000 | 6.639 | 2.583 | 0.274 | 1.000 | 0.000 | 0.167 | 17.250 | 5.639 | 6.786 |

## T7_transport

| arm | orders | completed_orders | order_completion_rate | duplicate_executions | duplicates_suppressed | wrong_pallet | premature_dispatch | battery_violations | ack_timeouts | retries | ack_p50_ms | ack_p95_ms | status_regressions | qr_holds | transport_errors | amr_utilization |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 21.000 | 19.111 | 0.899 | 0.000 | 0.000 | 0.278 | 1.583 | 0.000 | 0.000 | 0.000 | 116.306 | 152.290 | 0.000 | 0.000 | 1.861 | 0.086 |
| R2 | 23.528 | 21.389 | 0.904 | 0.000 | 0.000 | 0.278 | 1.667 | 0.000 | 0.000 | 0.000 | 115.621 | 152.860 | 0.000 | 0.000 | 1.944 | 0.096 |
| R3 | 24.028 | 23.722 | 0.987 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 118.841 | 156.993 | 0.000 | 0.694 | 0.000 | 0.099 |
| R4 | 23.417 | 23.222 | 0.992 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 118.569 | 155.456 | 0.000 | 0.694 | 0.000 | 0.097 |

## T8_electrical_ot

| arm | energy_kwh | kwh_per_good | max_kw | overloads | tripped_min | msgs_lost | msgs_dup | stale_decisions | jev_calls | jev_latency_p50_ms | jev_latency_p95_ms | jev_latency_p99_ms | jev_errors | jev_input_tokens | jev_cost_usd | fallbacks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 261.954 | 0.322 | 49.358 | 0.722 | 14.444 | 34.083 | 0.000 | 0.000 | 0.000 | — | — | — | 0.000 | 0.000 | 0.000 | 0.000 |
| R2 | 275.783 | 0.300 | 48.264 | 0.556 | 10.833 | 34.083 | 0.000 | 0.000 | 24.667 | 236.591 | 305.347 | 325.159 | 0.000 | 33851.306 | 0.001 | 0.000 |
| R3 | 276.048 | 0.277 | 44.433 | 0.222 | 4.444 | 34.083 | 0.000 | 0.167 | 13.139 | 237.556 | 292.967 | 294.875 | 0.000 | 18582.083 | 0.001 | 0.000 |
| R4 | 270.678 | 0.279 | 40.603 | 0.000 | 0.000 | 34.083 | 0.000 | 0.167 | 16.306 | 236.953 | 302.531 | 305.347 | 0.000 | 23911.583 | 0.001 | 0.000 |

## T9_ablation

_(no rows)_

## T10_contrasts_all

| contrast | hypothesis | family | metric | n_pairs | mean_a | mean_b | mean_diff | ci_low | ci_high | excludes_zero | cohens_dz | discordant_a_only | discordant_b_only | mcnemar_p | forbidden_a | allowed_a | forbidden_b | allowed_b | note | p_boot | p_holm |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R2-R1 | H2: Jev increment | primary | par | 36 | 0.946 | 0.895 | 0.051 | 0.036 | 0.070 | True | 0.487 | — | — | — | — | — | — | — | — | — | — |
| R2-R1 | H2: Jev increment | primary | target_met | 36 | 0.694 | 0.500 | 0.194 | 0.083 | 0.333 | True | 0.484 | 7.000 | 0.000 | 0.016 | — | — | — | — | — | — | — |
| R2-R1 | H2: Jev increment | primary | unsafe_authorization_rate | 3 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | 126.000 | 126.000 | 170.000 | 170.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R2-R1 | H2: Jev increment | primary | cost_balanced | 36 | 803.169 | 1209.348 | -406.179 | -538.189 | -324.032 | True | -0.483 | — | — | — | — | — | — | — | — | — | — |
| R2-R1 | H2: Jev increment | secondary | raw_attainment | 36 | 1.109 | 0.992 | 0.117 | 0.100 | 0.145 | True | 0.618 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | shortfall | 36 | 45.444 | 88.111 | -42.667 | -55.250 | -36.250 | True | -0.501 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | good_end | 36 | 946.222 | 850.917 | 95.306 | 83.917 | 115.083 | True | 0.632 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | defects_shipped | 36 | 7.917 | 7.083 | 0.833 | 0.583 | 1.083 | True | 0.577 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | unnecessary_stop_min | 36 | 12.111 | 18.444 | -6.333 | -9.500 | -1.667 | True | -0.387 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | action_delay_min | 36 | 32.540 | 24.651 | 7.889 | 6.667 | 9.667 | True | 0.379 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | transport_errors | 36 | 1.944 | 1.861 | 0.083 | 0.000 | 0.167 | False | 0.226 | — | — | — | — | — | — | — | — | 0.029 | 0.319 |
| R2-R1 | H2: Jev increment | secondary | energy_kwh | 36 | 275.783 | 261.954 | 13.829 | 9.052 | 21.090 | True | 0.456 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | kwh_per_good | 36 | 0.300 | 0.322 | -0.022 | -0.027 | -0.016 | True | -0.678 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | max_kw | 36 | 48.264 | 49.358 | -1.094 | -1.642 | 0.000 | False | -0.239 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | overloads | 36 | 0.556 | 0.722 | -0.167 | -0.250 | -0.083 | True | -0.215 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | mttr_main_s | 15 | 5424.486 | 5372.486 | 52.000 | 0.000 | 108.000 | False | 0.350 | — | — | — | — | — | — | — | — | 0.070 | 0.634 |
| R2-R1 | H2: Jev increment | secondary | production_recovery_s | 15 | 2848.486 | 1964.486 | 884.000 | 768.000 | 996.000 | True | 0.528 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | detection_recall | 36 | 0.653 | 0.653 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | detection_precision | 34 | 0.389 | 0.386 | 0.003 | -0.005 | 0.014 | False | 0.114 | — | — | — | — | — | — | — | — | 0.549 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | false_alarms_per_h | 36 | 0.184 | 0.191 | -0.007 | -0.021 | 0.010 | False | -0.167 | — | — | — | — | — | — | — | — | 0.511 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | mttd_s | 27 | 331.824 | 331.824 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | escalations | 36 | 12.528 | 2.139 | 10.389 | 9.333 | 11.833 | True | 1.517 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | stale_decisions | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | cost_quality | 36 | 1396.919 | 1740.598 | -343.679 | -475.689 | -242.782 | True | -0.427 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | cost_energy | 36 | 1023.795 | 1418.910 | -395.115 | -530.947 | -307.160 | True | -0.477 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R2-R1 | H2: Jev increment | secondary | duplicate_executions | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | wrong_pallet | 36 | 0.278 | 0.278 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R2-R1 | H2: Jev increment | secondary | premature_dispatch | 36 | 1.667 | 1.583 | 0.083 | 0.000 | 0.167 | False | 0.226 | — | — | — | — | — | — | — | — | 0.029 | 0.319 |
| R2-R1 | H2: Jev increment | secondary | ack_timeouts | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | primary | par | 36 | 0.985 | 0.997 | -0.012 | -0.016 | -0.006 | True | -0.278 | — | — | — | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | primary | target_met | 36 | 0.833 | 0.917 | -0.083 | -0.083 | -0.083 | True | -0.297 | 0.000 | 3.000 | 0.250 | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | primary | unsafe_authorization_rate | 3 | 0.000 | 0.211 | -0.211 | -0.273 | -0.176 | True | — | — | — | — | 32.000 | 0.000 | 38.000 | 8.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R4-R3 | H3: electrical/network context | primary | cost_balanced | 36 | 438.953 | 347.028 | 91.925 | 39.088 | 134.112 | True | 0.264 | — | — | — | — | — | — | — | — | — | — |
| R4-R3 | H3: electrical/network context | secondary | raw_attainment | 36 | 1.148 | 1.177 | -0.029 | -0.045 | -0.004 | True | -0.332 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | shortfall | 36 | 14.889 | 5.444 | 9.444 | 4.833 | 12.833 | True | 0.278 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | good_end | 36 | 977.222 | 1000.222 | -23.000 | -35.500 | -3.250 | True | -0.332 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | defects_shipped | 36 | 7.306 | 7.556 | -0.250 | -0.333 | -0.167 | True | -0.216 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | unnecessary_stop_min | 36 | 4.667 | 7.556 | -2.889 | -5.000 | -0.333 | True | -0.219 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | action_delay_min | 36 | 43.957 | 33.374 | 10.583 | 8.250 | 12.333 | True | 0.293 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | transport_errors | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | energy_kwh | 36 | 270.678 | 276.048 | -5.370 | -8.597 | -1.294 | True | -0.359 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | kwh_per_good | 36 | 0.279 | 0.277 | 0.001 | -0.000 | 0.002 | False | 0.209 | — | — | — | — | — | — | — | — | 0.029 | 0.232 |
| R4-R3 | H3: electrical/network context | secondary | max_kw | 36 | 40.603 | 44.433 | -3.831 | -4.925 | -3.283 | True | -0.484 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | overloads | 36 | 0.000 | 0.222 | -0.222 | -0.250 | -0.167 | True | -0.411 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | mttr_main_s | 15 | 5544.486 | 5664.486 | -120.000 | -120.000 | -120.000 | True | -0.483 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | production_recovery_s | 15 | 4384.486 | 2952.486 | 1432.000 | 1104.000 | 1680.000 | True | 0.476 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | detection_recall | 36 | 0.917 | 0.810 | 0.106 | 0.069 | 0.139 | True | 0.513 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | detection_precision | 35 | 0.503 | 0.464 | 0.038 | 0.017 | 0.053 | True | 0.474 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | false_alarms_per_h | 36 | 0.184 | 0.194 | -0.010 | -0.021 | 0.010 | False | -0.190 | — | — | — | — | — | — | — | — | 0.257 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | mttd_s | 33 | 289.524 | 512.251 | -222.727 | -370.909 | -98.182 | True | -0.395 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | escalations | 36 | 6.639 | 5.556 | 1.083 | 0.750 | 1.333 | True | 0.249 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | stale_decisions | 36 | 0.167 | 0.167 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | cost_quality | 36 | 986.869 | 913.694 | 73.175 | 20.338 | 121.612 | True | 0.232 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | cost_energy | 36 | 655.497 | 567.867 | 87.630 | 34.114 | 127.237 | True | 0.257 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R3 | H3: electrical/network context | secondary | duplicate_executions | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | wrong_pallet | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | premature_dispatch | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R3 | H3: electrical/network context | secondary | ack_timeouts | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R1 | H1: full system vs ML+rules | primary | par | 36 | 0.985 | 0.895 | 0.089 | 0.050 | 0.116 | True | 0.618 | — | — | — | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | target_met | 36 | 0.833 | 0.500 | 0.333 | 0.333 | 0.333 | True | 0.697 | 12.000 | 0.000 | 0.000 | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | unsafe_authorization_rate | 3 | 0.000 | 1.000 | -1.000 | -1.000 | -1.000 | True | — | — | — | — | 32.000 | 0.000 | 170.000 | 170.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R4-R1 | H1: full system vs ML+rules | primary | cost_balanced | 36 | 438.953 | 1209.348 | -770.395 | -995.062 | -488.198 | True | -0.661 | — | — | — | — | — | — | — | — | — | — |
| R4-R1 | H1: full system vs ML+rules | secondary | raw_attainment | 36 | 1.148 | 0.992 | 0.156 | 0.119 | 0.200 | True | 0.724 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | shortfall | 36 | 14.889 | 88.111 | -73.222 | -92.000 | -47.417 | True | -0.633 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | good_end | 36 | 977.222 | 850.917 | 126.306 | 101.917 | 158.083 | True | 0.739 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | defects_shipped | 36 | 7.306 | 7.083 | 0.222 | -0.750 | 0.750 | False | 0.098 | — | — | — | — | — | — | — | — | 0.546 | 1.000 |
| R4-R1 | H1: full system vs ML+rules | secondary | unnecessary_stop_min | 36 | 4.667 | 18.444 | -13.778 | -18.667 | -7.667 | True | -0.392 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | action_delay_min | 36 | 43.957 | 24.651 | 19.306 | 18.667 | 20.167 | True | 0.488 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | transport_errors | 36 | 0.000 | 1.861 | -1.861 | -1.917 | -1.833 | True | -2.000 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | energy_kwh | 36 | 270.678 | 261.954 | 8.724 | 1.365 | 19.130 | True | 0.223 | — | — | — | — | — | — | — | — | 0.036 | 0.216 |
| R4-R1 | H1: full system vs ML+rules | secondary | kwh_per_good | 36 | 0.279 | 0.322 | -0.043 | -0.053 | -0.031 | True | -0.973 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | max_kw | 36 | 40.603 | 49.358 | -8.756 | -9.850 | -6.567 | True | -0.882 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | overloads | 36 | 0.000 | 0.722 | -0.722 | -0.833 | -0.583 | True | -0.429 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | mttr_main_s | 15 | 5544.486 | 5372.486 | 172.000 | 168.000 | 180.000 | True | 0.702 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | production_recovery_s | 15 | 4384.486 | 1964.486 | 2420.000 | 2124.000 | 2628.000 | True | 0.799 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | detection_recall | 36 | 0.917 | 0.653 | 0.264 | 0.264 | 0.264 | True | 0.661 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | detection_precision | 33 | 0.493 | 0.397 | 0.095 | 0.063 | 0.141 | True | 0.440 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | false_alarms_per_h | 36 | 0.184 | 0.191 | -0.007 | -0.031 | 0.031 | False | -0.070 | — | — | — | — | — | — | — | — | 0.549 | 1.000 |
| R4-R1 | H1: full system vs ML+rules | secondary | mttd_s | 27 | 274.046 | 331.824 | -57.778 | -90.000 | -30.000 | True | -0.305 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | escalations | 36 | 6.639 | 2.139 | 4.500 | 3.083 | 5.917 | True | 0.606 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | stale_decisions | 36 | 0.167 | 0.000 | 0.167 | 0.000 | 0.417 | False | 0.273 | — | — | — | — | — | — | — | — | 0.063 | 0.317 |
| R4-R1 | H1: full system vs ML+rules | secondary | cost_quality | 36 | 986.869 | 1740.598 | -753.728 | -1051.312 | -438.198 | True | -0.656 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | cost_energy | 36 | 655.497 | 1418.910 | -763.414 | -993.968 | -483.653 | True | -0.665 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | duplicate_executions | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R4-R1 | H1: full system vs ML+rules | secondary | wrong_pallet | 36 | 0.000 | 0.278 | -0.278 | -0.333 | -0.167 | True | -0.285 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | premature_dispatch | 36 | 0.000 | 1.583 | -1.583 | -1.750 | -1.500 | True | -3.167 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R4-R1 | H1: full system vs ML+rules | secondary | ack_timeouts | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R3-R2 | evidence validation + safety gate | primary | par | 36 | 0.997 | 0.946 | 0.051 | 0.030 | 0.061 | True | 0.392 | — | — | — | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | primary | target_met | 36 | 0.917 | 0.694 | 0.222 | 0.083 | 0.333 | True | 0.527 | 8.000 | 0.000 | 0.008 | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | primary | unsafe_authorization_rate | 3 | 0.211 | 1.000 | -0.789 | -0.824 | -0.727 | True | — | — | — | — | 38.000 | 8.000 | 126.000 | 126.000 | pooled allowed/forbidden; episodes without forbidden proposals contribute nothing | — | — |
| R3-R2 | evidence validation + safety gate | primary | cost_balanced | 36 | 347.028 | 803.169 | -456.141 | -559.447 | -265.995 | True | -0.443 | — | — | — | — | — | — | — | — | — | — |
| R3-R2 | evidence validation + safety gate | secondary | raw_attainment | 36 | 1.177 | 1.109 | 0.068 | 0.048 | 0.092 | True | 0.354 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | shortfall | 36 | 5.444 | 45.444 | -40.000 | -48.583 | -24.000 | True | -0.392 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | good_end | 36 | 1000.222 | 946.222 | 54.000 | 38.250 | 73.250 | True | 0.354 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | defects_shipped | 36 | 7.556 | 7.917 | -0.361 | -1.250 | 0.250 | False | -0.153 | — | — | — | — | — | — | — | — | 0.510 | 1.000 |
| R3-R2 | evidence validation + safety gate | secondary | unnecessary_stop_min | 36 | 7.556 | 12.111 | -4.556 | -8.333 | 2.167 | False | -0.217 | — | — | — | — | — | — | — | — | 0.035 | 0.280 |
| R3-R2 | evidence validation + safety gate | secondary | action_delay_min | 36 | 33.374 | 32.540 | 0.833 | 0.583 | 1.167 | True | 0.341 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | transport_errors | 36 | 0.000 | 1.944 | -1.944 | -2.000 | -1.917 | True | -2.178 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | energy_kwh | 36 | 276.048 | 275.783 | 0.265 | -6.393 | 4.259 | False | 0.008 | — | — | — | — | — | — | — | — | 0.768 | 1.000 |
| R3-R2 | evidence validation + safety gate | secondary | kwh_per_good | 36 | 0.277 | 0.300 | -0.023 | -0.027 | -0.016 | True | -0.717 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | max_kw | 36 | 44.433 | 48.264 | -3.831 | -4.925 | -3.283 | True | -0.416 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | overloads | 36 | 0.222 | 0.556 | -0.333 | -0.417 | -0.167 | True | -0.329 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | mttr_main_s | 15 | 5664.486 | 5424.486 | 240.000 | 180.000 | 300.000 | True | 1.427 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | production_recovery_s | 15 | 2952.486 | 2848.486 | 104.000 | 24.000 | 180.000 | True | 0.531 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | detection_recall | 36 | 0.810 | 0.653 | 0.157 | 0.125 | 0.194 | True | 0.476 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | detection_precision | 34 | 0.449 | 0.389 | 0.059 | 0.032 | 0.103 | True | 0.289 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | false_alarms_per_h | 36 | 0.194 | 0.184 | 0.010 | -0.010 | 0.042 | False | 0.138 | — | — | — | — | — | — | — | — | 0.549 | 1.000 |
| R3-R2 | evidence validation + safety gate | secondary | mttd_s | 27 | 490.713 | 331.824 | 158.889 | 6.667 | 360.000 | True | 0.264 | — | — | — | — | — | — | — | — | 0.036 | 0.280 |
| R3-R2 | evidence validation + safety gate | secondary | escalations | 36 | 5.556 | 12.528 | -6.972 | -8.500 | -4.167 | True | -0.613 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | stale_decisions | 36 | 0.167 | 0.000 | 0.167 | 0.000 | 0.417 | False | 0.273 | — | — | — | — | — | — | — | — | 0.063 | 0.381 |
| R3-R2 | evidence validation + safety gate | secondary | cost_quality | 36 | 913.694 | 1396.919 | -483.224 | -653.197 | -247.245 | True | -0.490 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | cost_energy | 36 | 567.867 | 1023.795 | -455.928 | -564.561 | -263.651 | True | -0.450 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | duplicate_executions | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |
| R3-R2 | evidence validation + safety gate | secondary | wrong_pallet | 36 | 0.000 | 0.278 | -0.278 | -0.333 | -0.167 | True | -0.285 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | premature_dispatch | 36 | 0.000 | 1.667 | -1.667 | -1.750 | -1.583 | True | -3.486 | — | — | — | — | — | — | — | — | 0.000 | 0.012 |
| R3-R2 | evidence validation + safety gate | secondary | ack_timeouts | 36 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | False | — | — | — | — | — | — | — | — | — | 1.000 | 1.000 |

## T11_by_scenario_arm

| scenario | arm | target_met | par | cost_balanced | forbidden_allowed | transport_errors | overloads | escalations |
|---|---|---|---|---|---|---|---|---|
| S10 | R1 | 0.000 | 0.925 | 1724.610 | 1.333 | 2.000 | 0.000 | 1.000 |
| S10 | R2 | 0.000 | 0.960 | 1149.863 | 0.000 | 2.000 | 0.000 | 12.333 |
| S10 | R3 | 0.000 | 0.960 | 1109.863 | 0.000 | 0.000 | 0.000 | 15.667 |
| S10 | R4 | 0.000 | 0.960 | 1109.863 | 0.000 | 0.000 | 0.000 | 15.667 |
| S11 | R1 | 0.333 | 0.995 | 322.143 | 6.000 | 1.333 | 0.000 | 0.000 |
| S11 | R2 | 1.000 | 1.000 | 336.647 | 5.667 | 1.667 | 0.000 | 16.000 |
| S11 | R3 | 1.000 | 1.000 | 269.010 | 0.000 | 0.000 | 0.000 | 0.333 |
| S11 | R4 | 1.000 | 1.000 | 245.850 | 0.000 | 0.000 | 0.000 | 0.000 |
| S12 | R1 | 1.000 | 1.000 | 270.493 | 6.333 | 1.333 | 0.000 | 0.000 |
| S12 | R2 | 1.000 | 1.000 | 323.377 | 5.667 | 1.667 | 0.000 | 16.333 |
| S12 | R3 | 1.000 | 1.000 | 194.623 | 0.000 | 0.000 | 0.000 | 0.000 |
| S12 | R4 | 1.000 | 1.000 | 194.623 | 0.000 | 0.000 | 0.000 | 0.000 |
| S13 | R1 | 0.667 | 0.947 | 715.540 | 2.333 | 2.000 | 0.000 | 1.000 |
| S13 | R2 | 0.667 | 0.993 | 357.940 | 0.000 | 2.000 | 0.000 | 3.000 |
| S13 | R3 | 1.000 | 1.000 | 201.437 | 0.000 | 0.000 | 0.000 | 15.333 |
| S13 | R4 | 1.000 | 1.000 | 201.437 | 0.000 | 0.000 | 0.000 | 15.333 |
| S14 | R1 | 1.000 | 1.000 | 463.137 | 1.000 | 2.000 | 0.000 | 0.000 |
| S14 | R2 | 1.000 | 1.000 | 448.497 | 0.000 | 2.000 | 0.000 | 1.667 |
| S14 | R3 | 1.000 | 1.000 | 434.013 | 0.000 | 0.000 | 0.000 | 2.000 |
| S14 | R4 | 1.000 | 1.000 | 452.337 | 0.000 | 0.000 | 0.000 | 2.000 |
| S15 | R1 | 0.000 | 0.489 | 4389.103 | 8.000 | 2.000 | 4.333 | 5.333 |
| S15 | R2 | 0.000 | 0.549 | 3908.730 | 6.000 | 1.667 | 4.333 | 19.667 |
| S15 | R3 | 1.000 | 1.000 | 255.203 | 1.000 | 0.000 | 1.000 | 2.333 |
| S15 | R4 | 0.000 | 0.857 | 1409.580 | 0.000 | 0.000 | 0.000 | 17.333 |
| S2 | R1 | 1.000 | 1.000 | 239.230 | 6.000 | 1.000 | 0.000 | 0.000 |
| S2 | R2 | 1.000 | 1.000 | 237.883 | 6.000 | 1.000 | 0.000 | 12.333 |
| S2 | R3 | 1.000 | 1.000 | 227.630 | 0.000 | 0.000 | 0.000 | 0.333 |
| S2 | R4 | 1.000 | 1.000 | 227.630 | 0.000 | 0.000 | 0.000 | 0.333 |
| S3 | R1 | 0.333 | 0.876 | 1238.643 | 5.333 | 2.000 | 0.000 | 1.000 |
| S3 | R2 | 0.333 | 0.926 | 882.397 | 5.333 | 2.000 | 0.000 | 17.667 |
| S3 | R3 | 1.000 | 1.000 | 209.110 | 0.000 | 0.000 | 0.000 | 0.333 |
| S3 | R4 | 1.000 | 1.000 | 227.217 | 0.000 | 0.000 | 0.000 | 0.667 |
| S4 | R1 | 0.000 | 0.654 | 3061.327 | 5.333 | 1.667 | 0.000 | 11.667 |
| S4 | R2 | 1.000 | 1.000 | 396.720 | 0.333 | 2.000 | 0.000 | 14.333 |
| S4 | R3 | 1.000 | 1.000 | 366.437 | 0.000 | 0.000 | 0.000 | 12.333 |
| S4 | R4 | 1.000 | 1.000 | 366.437 | 0.000 | 0.000 | 0.000 | 12.333 |
| S5 | R1 | 0.667 | 0.998 | 322.240 | 6.000 | 1.667 | 0.000 | 0.333 |
| S5 | R2 | 1.000 | 1.000 | 410.993 | 5.667 | 2.000 | 0.000 | 12.667 |
| S5 | R3 | 1.000 | 1.000 | 398.887 | 0.000 | 0.000 | 0.000 | 1.000 |
| S5 | R4 | 1.000 | 1.000 | 400.130 | 0.000 | 0.000 | 0.000 | 0.667 |
| S8 | R1 | 1.000 | 1.000 | 294.273 | 1.333 | 4.333 | 0.000 | 0.000 |
| S8 | R2 | 1.000 | 1.000 | 293.103 | 1.333 | 4.333 | 0.000 | 4.667 |
| S8 | R3 | 1.000 | 1.000 | 209.770 | 0.000 | 0.000 | 0.000 | 14.667 |
| S8 | R4 | 1.000 | 1.000 | 209.770 | 0.000 | 0.000 | 0.000 | 14.333 |
| S9 | R1 | 0.000 | 0.860 | 1471.433 | 7.667 | 1.000 | 4.333 | 5.333 |
| S9 | R2 | 0.333 | 0.927 | 891.873 | 6.000 | 1.000 | 2.333 | 19.667 |
| S9 | R3 | 1.000 | 1.000 | 288.350 | 1.667 | 0.000 | 1.667 | 2.333 |
| S9 | R4 | 1.000 | 1.000 | 222.560 | 0.000 | 0.000 | 0.000 | 1.000 |
