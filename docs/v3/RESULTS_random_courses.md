# Random-course results (generated from results/v3/live_runs — do not edit by hand)

Generated 2026-10-18 07:48. Stored runs: 3721 (excluded for data quality: 0).
Real TypeSafe Jev batch runs: 1260 on 420 course×load pairs, 32226 Jev calls, $1.868, models jev-1.13.0; per-part judge in batches is the mock judge (the real per-part study is `results/v3/real_jev/unit_study`).

Design: each random course (1–4 random faults / defect surges / network / AMR / power events) is run by every mode on the same course number at medium (85 %) and high (95 %) target load. Rule modes (R0, R1) and the planner reference (R5) never call Jev; they are paired with the real-Jev modes (R2–R4) on the same courses.

## Paired contrasts — real TypeSafe Jev

| contrast | metric | pairs | first | second | diff | 95 % CI | p (Holm) | pairs needed (80 %) |
|---|---|---|---|---|---|---|---|---|
| R2−R1 (H2: Jev increment) | target_met | 420 | 0.874 | 0.871 | 0.002 | [-0.021, 0.031] | 1.000 | 82411 |
| R4−R3 (H3: electrical/network context) | target_met | 420 | 0.986 | 0.952 | 0.033 | [0.017, 0.052] | 0.002 | 267 ✓ |
| R4−R1 (H1: full system vs ML+rules) | target_met | 420 | 0.986 | 0.871 | 0.114 | [0.083, 0.150] | 0.000 | 70 ✓ |
| R3−R2 (evidence validation + safety gate) | target_met | 420 | 0.952 | 0.874 | 0.079 | [0.050, 0.110] | 0.000 | 122 ✓ |
| R5−R4 (planner reference vs R4) | target_met | 420 | 0.974 | 0.986 | -0.012 | [-0.024, 0.000] | 0.250 | 921 |
| R2−R1 (H2: Jev increment) | cost_balanced | 420 | 1013.262 | 1016.281 | -3.019 | [-29.548, 21.968] | 1.000 | 52091 |
| R4−R3 (H3: electrical/network context) | cost_balanced | 420 | 917.889 | 924.707 | -6.818 | [-29.248, 12.906] | 1.000 | 8623 |
| R4−R1 (H1: full system vs ML+rules) | cost_balanced | 420 | 917.889 | 1016.281 | -98.392 | [-143.999, -59.172] | 0.005 | 152 ✓ |
| R3−R2 (evidence validation + safety gate) | cost_balanced | 420 | 924.707 | 1013.262 | -88.555 | [-122.972, -55.048] | 0.005 | 127 ✓ |
| R5−R4 (planner reference vs R4) | cost_balanced | 420 | 859.434 | 917.889 | -58.455 | [-71.522, -46.470] | 0.005 | 35 ✓ |
| R2−R1 (H2: Jev increment) | forbidden_allowed | 420 | 2.198 | 2.536 | -0.338 | [-0.460, -0.231] | 0.005 | 98 ✓ |
| R4−R3 (H3: electrical/network context) | forbidden_allowed | 420 | 0.002 | 0.300 | -0.298 | [-0.405, -0.205] | 0.005 | 100 ✓ |
| R4−R1 (H1: full system vs ML+rules) | forbidden_allowed | 420 | 0.002 | 2.536 | -2.533 | [-2.840, -2.248] | 0.005 | 9 ✓ |
| R3−R2 (evidence validation + safety gate) | forbidden_allowed | 420 | 0.300 | 2.198 | -1.898 | [-2.152, -1.655] | 0.005 | 12 ✓ |
| R5−R4 (planner reference vs R4) | forbidden_allowed | 420 | 0.000 | 0.002 | -0.002 | [-0.007, 0.000] | 0.281 | 3298 |
| R2−R1 (H2: Jev increment) | kwh_per_good | 420 | 0.300 | 0.303 | -0.003 | [-0.005, -0.002] | 0.005 | 98 ✓ |
| R4−R3 (H3: electrical/network context) | kwh_per_good | 420 | 0.289 | 0.291 | -0.002 | [-0.003, -0.001] | 0.005 | 91 ✓ |
| R4−R1 (H1: full system vs ML+rules) | kwh_per_good | 420 | 0.289 | 0.303 | -0.014 | [-0.017, -0.012] | 0.005 | 19 ✓ |
| R3−R2 (evidence validation + safety gate) | kwh_per_good | 420 | 0.291 | 0.300 | -0.009 | [-0.011, -0.007] | 0.005 | 29 ✓ |
| R5−R4 (planner reference vs R4) | kwh_per_good | 420 | 0.291 | 0.289 | 0.002 | [0.001, 0.003] | 0.005 | 57 ✓ |
| R2−R1 (H2: Jev increment) | defects_per_1000_good | 420 | 27.324 | 27.281 | 0.043 | [-0.113, 0.195] | 1.000 | 10199 |
| R4−R3 (H3: electrical/network context) | defects_per_1000_good | 420 | 27.340 | 27.509 | -0.169 | [-0.340, -0.004] | 0.265 | 760 |
| R4−R1 (H1: full system vs ML+rules) | defects_per_1000_good | 420 | 27.340 | 27.281 | 0.059 | [-0.157, 0.287] | 1.000 | 12448 |
| R3−R2 (evidence validation + safety gate) | defects_per_1000_good | 420 | 27.509 | 27.324 | 0.184 | [-0.012, 0.400] | 0.364 | 1127 |
| R5−R4 (planner reference vs R4) | defects_per_1000_good | 420 | 27.310 | 27.340 | -0.029 | [-0.204, 0.159] | 1.000 | 28246 |
| R2−R1 (H2: Jev increment) | good_to_target | 420 | 1.185 | 1.201 | -0.016 | [-0.029, -0.003] | 0.014 | 500 |
| R4−R3 (H3: electrical/network context) | good_to_target | 420 | 1.331 | 1.276 | 0.055 | [0.044, 0.067] | 0.005 | 42 ✓ |
| R4−R1 (H1: full system vs ML+rules) | good_to_target | 420 | 1.331 | 1.201 | 0.130 | [0.113, 0.148] | 0.005 | 19 ✓ |
| R3−R2 (evidence validation + safety gate) | good_to_target | 420 | 1.276 | 1.185 | 0.091 | [0.073, 0.109] | 0.005 | 35 ✓ |
| R5−R4 (planner reference vs R4) | good_to_target | 420 | 1.236 | 1.331 | -0.095 | [-0.111, -0.080] | 0.005 | 21 ✓ |
| R2−R1 (H2: Jev increment) | defects_shipped | 420 | 27.198 | 27.460 | -0.262 | [-0.541, 0.019] | 0.065 | 1019 |
| R4−R3 (H3: electrical/network context) | defects_shipped | 420 | 30.574 | 29.431 | 1.143 | [0.871, 1.440] | 0.005 | 59 ✓ |
| R4−R1 (H1: full system vs ML+rules) | defects_shipped | 420 | 30.574 | 27.460 | 3.114 | [2.655, 3.588] | 0.005 | 23 ✓ |
| R3−R2 (evidence validation + safety gate) | defects_shipped | 420 | 29.431 | 27.198 | 2.233 | [1.790, 2.683] | 0.005 | 38 ✓ |
| R5−R4 (planner reference vs R4) | defects_shipped | 420 | 28.336 | 30.574 | -2.238 | [-2.650, -1.857] | 0.005 | 25 ✓ |
| R2−R1 (H2: Jev increment) | factory_kwh_total | 420 | 295.634 | 302.368 | -6.734 | [-9.297, -4.343] | 0.005 | 119 ✓ |
| R4−R3 (H3: electrical/network context) | factory_kwh_total | 420 | 321.148 | 309.734 | 11.414 | [9.093, 13.913] | 0.005 | 42 ✓ |
| R4−R1 (H1: full system vs ML+rules) | factory_kwh_total | 420 | 321.148 | 302.368 | 18.780 | [15.292, 22.351] | 0.005 | 33 ✓ |
| R3−R2 (evidence validation + safety gate) | factory_kwh_total | 420 | 309.734 | 295.634 | 14.100 | [10.729, 17.557] | 0.005 | 54 ✓ |
| R5−R4 (planner reference vs R4) | factory_kwh_total | 420 | 300.754 | 321.148 | -20.394 | [-23.671, -17.314] | 0.005 | 20 ✓ |
| R2−R1 (H2: Jev increment) | transport_errors | 420 | 4.045 | 4.043 | 0.002 | [-0.110, 0.114] | 1.000 | 1787672 |
| R4−R3 (H3: electrical/network context) | transport_errors | 420 | 0.000 | 0.000 | 0.000 | [0.000, 0.000] | 1.000 | — |
| R4−R1 (H1: full system vs ML+rules) | transport_errors | 420 | 0.000 | 4.043 | -4.043 | [-4.460, -3.660] | 0.005 | 9 ✓ |
| R3−R2 (evidence validation + safety gate) | transport_errors | 420 | 0.000 | 4.045 | -4.045 | [-4.452, -3.671] | 0.005 | 9 ✓ |
| R5−R4 (planner reference vs R4) | transport_errors | 420 | 0.000 | 0.000 | 0.000 | [0.000, 0.000] | 1.000 | — |
| R2−R1 (H2: Jev increment) | production_recovery_s | 232 | 1503.946 | 1491.922 | 12.024 | [-186.035, 211.406] | 1.000 | 133260 |
| R4−R3 (H3: electrical/network context) | production_recovery_s | 225 | 1830.809 | 1491.179 | 339.630 | [127.844, 547.152] | 0.005 | 191 ✓ |
| R4−R1 (H1: full system vs ML+rules) | production_recovery_s | 225 | 1830.809 | 1524.155 | 306.654 | [16.489, 593.179] | 0.129 | 430 |
| R3−R2 (evidence validation + safety gate) | production_recovery_s | 230 | 1509.931 | 1517.015 | -7.084 | [-256.001, 219.811] | 1.000 | 514416 |
| R5−R4 (planner reference vs R4) | production_recovery_s | 225 | 1508.844 | 1830.809 | -321.965 | [-590.947, -53.164] | 0.040 | 334 |

## Rule-only contrasts

| contrast | metric | pairs | diff | 95 % CI | p (Holm) |
|---|---|---|---|---|---|
| R1−R0 | target_met | 620 | 0.571 | [0.532, 0.608] | 0.000 |
| R1−R0 | cost_balanced | 620 | -539.606 | [-622.812, -457.132] | 0.001 |
| R1−R0 | forbidden_allowed | 620 | 0.453 | [0.339, 0.576] | 0.001 |
| R1−R0 | kwh_per_good | 620 | -0.016 | [-0.019, -0.013] | 0.001 |
| R1−R0 | defects_per_1000_good | 620 | -0.423 | [-0.753, -0.093] | 0.012 |
| R1−R0 | good_to_target | 620 | 0.285 | [0.269, 0.301] | 0.001 |
| R1−R0 | defects_shipped | 620 | 6.215 | [5.794, 6.661] | 0.001 |
| R1−R0 | factory_kwh_total | 620 | 64.760 | [61.459, 68.063] | 0.001 |
| R1−R0 | transport_errors | 620 | 1.121 | [0.968, 1.297] | 0.001 |
| R1−R0 | production_recovery_s | 343 | 639.006 | [440.058, 857.318] | 0.001 |

## Group means (real Jev groups and their rule/planner partners)

| mode | provider | load | n | target met | output / target | unsafe allowed | shipped defects per 1000 good | loss (balanced) | factory kWh | kWh/good | AMR charging kWh |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R0 | NONE | high | 310 | 0.100 | 0.867 | 1.91 | 27.65 | 1880.2 | 237.5 | 0.3184 | 0.575 |
| R0 | NONE | medium | 310 | 0.506 | 0.963 | 2.17 | 27.59 | 1227.0 | 236.8 | 0.3190 | 0.571 |
| R1 | NONE | high | 310 | 0.877 | 1.236 | 2.49 | 27.15 | 1135.5 | 323.9 | 0.2995 | 0.742 |
| R1 | NONE | medium | 310 | 0.871 | 1.165 | 2.50 | 27.24 | 892.4 | 279.9 | 0.3057 | 0.636 |
| R2 | TYPESAFE_JEV | high | 210 | 0.890 | 1.226 | 2.15 | 27.42 | 1124.8 | 319.0 | 0.2969 | 0.743 |
| R2 | TYPESAFE_JEV | medium | 210 | 0.857 | 1.144 | 2.24 | 27.23 | 901.8 | 272.3 | 0.3024 | 0.619 |
| R3 | TYPESAFE_JEV | high | 210 | 0.948 | 1.321 | 0.31 | 27.54 | 1012.1 | 334.4 | 0.2878 | 0.837 |
| R3 | TYPESAFE_JEV | medium | 210 | 0.957 | 1.232 | 0.29 | 27.48 | 837.3 | 285.0 | 0.2935 | 0.684 |
| R4 | TYPESAFE_JEV | high | 210 | 0.990 | 1.369 | 0.00 | 27.40 | 992.1 | 345.0 | 0.2858 | 0.886 |
| R4 | TYPESAFE_JEV | medium | 210 | 0.981 | 1.293 | 0.00 | 27.28 | 843.7 | 297.3 | 0.2916 | 0.718 |
| R5 | NONE | high | 310 | 0.990 | 1.289 | 0.00 | 27.24 | 935.9 | 326.9 | 0.2878 | 0.816 |
| R5 | NONE | medium | 310 | 0.965 | 1.182 | 0.00 | 27.32 | 782.8 | 274.1 | 0.2937 | 0.670 |

Reading note: every mode produces more than the plan once it is on track (output / target above 1); none of the decision options rewards stopping the backup line after the target is secured. Absolute factory kWh and absolute defects shipped therefore grow with output volume; compare modes on kWh per good part and shipped defects per 1000 good parts (volume-independent).

Mock-Jev groups (software check, not evidence about Jev) are in `stats_by_group.csv` / `paired.csv` under MOCK_JEV.
