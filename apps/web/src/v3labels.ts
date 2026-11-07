/** Plain-language labels for SF AI OS v3 screens (Korean first, English second). Internal codes stay in the data;
 *  every screen shows these names, with the code only as small secondary text where a reader may need it. */
export type Lang = 'ko' | 'en'
type Pair = [string, string]
const pick = (m: Record<string, Pair>, k: string | null | undefined, lang: Lang) => (k && m[k] ? m[k][lang === 'ko' ? 0 : 1] : (k ?? '—'))

export const ACTION: Record<string, Pair> = {
  CONTINUE_MAIN: ['메인 라인 계속 가동', 'Keep main line running'],
  RECOVER_MAIN: ['메인 라인 복구 후 재가동', 'Recover and restart main line'],
  SWITCH_TO_SUB: ['예비 라인으로 전환', 'Switch to backup line'],
  RUN_PARALLEL: ['메인·예비 라인 동시 가동', 'Run main and backup together'],
  REPLAN: ['계획 조정(부족 수용·계획 부서 통보)', 'Re-plan (accept shortfall, notify planning)'],
  REQUEST_TRANSPORT: ['예비 AMR 호출(다른 구역)', 'Call the reserve AMR'],
  SAFE_STOP_REQUEST: ['메인 라인 안전 정지', 'Safe stop of main line'],
  ESCALATE_HUMAN: ['담당자 호출(자동 판단 보류)', 'Call the operator (no automatic action)'],
}
export const ACTION_WHY: Record<string, Pair> = {
  CONTINUE_MAIN: ['지금 경로 그대로 생산', 'produce on the current route'],
  RECOVER_MAIN: ['수리·정비가 끝나고 안정화 확인이 되면 메인 라인을 다시 켬', 'restart the main line once repair is done and stability is confirmed'],
  SWITCH_TO_SUB: ['예비 라인을 켜서 생산을 이어감(기동 10분, 더 느림)', 'start the backup line (10 min start-up, slower)'],
  RUN_PARALLEL: ['두 라인을 같이 돌려 부족분을 메움(전력 더 씀)', 'run both lines to close the gap (more power)'],
  REPLAN: ['설비는 그대로 두고 부족을 계획 부서에 알림', 'no equipment change; notify planning'],
  REQUEST_TRANSPORT: ['라인 AMR이 모두 멈췄을 때 다른 구역의 예비 AMR로 팔레트를 옮김', 'move pallets with the reserve AMR while line AMRs are down'],
  SAFE_STOP_REQUEST: ['불량이 계속 나오면 라인을 멈춤', 'stop the line while it produces defects'],
  ESCALATE_HUMAN: ['근거가 부족하거나 자동 조치가 위험할 때 사람에게 넘김', 'hand over to a person when evidence is weak or automation is unsafe'],
}
export const STATE: Record<string, Pair> = {
  RUNNING: ['가동 중', 'Running'], STARTING: ['기동 중', 'Starting'], BLOCKED: ['출구 적체로 대기', 'Blocked (outlet full)'], FAULT: ['고장', 'Fault'],
  MAINT: ['정비·수리 중', 'Maintenance'], REPAIR: ['수리 중', 'Repair'], READY: ['수리 완료·안정화 확인 중', 'Repaired, stabilising'], IDLE: ['정지(대기)', 'Stopped'],
  OFF: ['꺼짐', 'Off'], TRIPPED: ['전력 보호 차단', 'Power trip'], UNKNOWN: ['신호 미수신', 'No signal'],
}
export const CAUSE: Record<string, Pair> = {
  EQUIPMENT: ['설비 고장', 'Equipment fault'], PROCESS_DRIFT: ['사이클타임 지연(공구 마모 등)', 'Cycle-time drift'], QUALITY: ['불량 증가', 'Quality (defects)'],
  NETWORK: ['통신 지연·끊김', 'Network delay / loss'], VISION_QR: ['영상·QR 불일치', 'Vision / QR mismatch'], COUNTER: ['PLC 생산 계수 이상', 'PLC counter anomaly'],
  TRANSPORT: ['운반로봇(AMR) 부족', 'AMR shortage'], POWER: ['전력 한도', 'Power limit'], PLAN: ['생산계획 위험', 'Plan at risk'], NONE: ['이상 없음', 'Nothing abnormal'],
  UNKNOWN: ['판단 근거 부족', 'Insufficient evidence'], FORCED: ['비교 실험용 강제 선택', 'forced (experiment)'],
}
export const DISPOSITION: Record<string, Pair> = { ACCEPT: ['합격(팔레트 적재)', 'Accept'], REJECT: ['폐기', 'Reject'], REINSPECT: ['재검사 대기', 'Re-inspect'] }
export const ORDER: Record<string, Pair> = {
  CREATED: ['생성', 'Created'], PUBLISHED: ['발행됨', 'Published'], ACKNOWLEDGED: ['수신 확인', 'Acknowledged'], EXECUTING: ['운반 중', 'Executing'], COMPLETED: ['완료', 'Completed'],
  FAILED: ['실패', 'Failed'], TIMED_OUT: ['응답 없음', 'Timed out'], DUPLICATE_IGNORED: ['중복 주문 무시', 'Duplicate ignored'],
}
export const BY: Record<string, Pair> = {
  JEV: ['Jev', 'Jev'], RULE: ['고정 규칙', 'Fixed rules'], PLANNER: ['최적화 계획기', 'Planner'], OPERATOR_SIM: ['담당자(모의)', 'Operator (simulated)'],
  FALLBACK: ['Jev 응답 실패 → 담당자 호출', 'Jev failed → operator'], PLAN_MONITOR: ['계획 감시(자동 알림)', 'Plan monitor'], ORACLE_REPLAY: ['비교 실험용', 'experiment'],
}
export const PROVIDER: Record<string, Pair> = {
  MOCK_JEV: ['모의 Jev(실제 호출 아님)', 'Mock Jev (no real call)'], TYPESAFE_JEV: ['실제 TypeSafe Jev', 'Real TypeSafe Jev'],
  FALLBACK_YOLO_RULE: ['Jev 실패 → YOLO 규칙으로 대체', 'Jev failed → YOLO rule'], NONE: ['Jev 미사용', 'no Jev'],
}
export const ARM: Record<string, Pair> = {
  R0: ['기준 A · 고정 규칙', 'Baseline A · fixed rules'], R1: ['기준 B · 이상탐지 + 고정 규칙', 'Baseline B · detection + fixed rules'],
  R2: ['Jev 판단 추가', '+ Jev decisions'], R3: ['Jev + 증거 검증 + 안전 게이트', 'Jev + evidence checks + safety gate'],
  R4: ['전체 시스템(전력·통신 반영)', 'Full system (power & network aware)'], R5: ['참고 · 최적화 계획기', 'Reference · planner'],
  'R4-noplan': ['전체 − 계획 정보', 'full − plan info'], 'R4-noct': ['전체 − 사이클타임 정보', 'full − cycle time'], 'R4-nonet': ['전체 − 통신 정보', 'full − network'],
  'R4-noelec': ['전체 − 전력 정보', 'full − power'], 'R4-novisionqr': ['전체 − 영상·QR 정보', 'full − vision/QR'], 'R4-basictransport': ['전체 + 단순 운송 방식', 'full + basic transport'],
}
export const FAULT: Record<string, Pair> = { E101: ['자재 걸림', 'material jam'], E201: ['안전 가드·센서 이상', 'guard / sensor'], E301: ['서보 드라이브 고장', 'servo drive'], E302: ['예비 라인 유압 고장', 'backup hydraulics'] }
export const METRIC: Record<string, Pair> = {
  target_met: ['목표 달성 비율', 'On-time completion'], par: ['계획 달성률', 'Plan attainment'], raw_attainment: ['계획 대비 생산 비율', 'Raw attainment'],
  good_end: ['최종 양품 수', 'Final good units'], shortfall: ['부족 수량', 'Shortfall'], defects_shipped: ['불량 유출 수', 'Defects shipped'],
  unsafe_authorization_rate: ['위험 명령 허용률', 'Unsafe authorisation rate'], unsafe_authorization_rate_pooled: ['위험 명령 허용률', 'Unsafe authorisation rate'],
  forbidden_proposed: ['위험 명령 제안 수', 'Unsafe proposals'], forbidden_allowed: ['위험 명령 허용 수', 'Unsafe allowed'], executed_unsafe: ['실행된 위험 사건', 'Executed unsafe events'],
  cost_balanced: ['운영 손실(기본 가중치)', 'Operating loss (balanced)'], factory_kwh_total: ['공장 총 전력량(kWh)', 'Factory energy (kWh)'], defects_per_1000_good: ['불량 출하(양품 1000개당)', 'Shipped defects per 1000 good'], good_to_target: ['목표 대비 생산량', 'Output / target'], amr_charge_kwh: ['AMR 충전 전력량(kWh)', 'AMR charging (kWh)'], cost_quality: ['운영 손실(품질 가중)', 'Loss (quality-weighted)'], cost_energy: ['운영 손실(에너지 가중)', 'Loss (energy-weighted)'],
  unnecessary_stop_min: ['불필요 정지(분)', 'Unnecessary stop (min)'], action_delay_min: ['조치 지연(분)', 'Action delay (min)'], energy_kwh: ['전력 사용량(kWh)', 'Energy (kWh)'],
  kwh_per_good: ['양품 1개당 전력(kWh)', 'kWh per good unit'], max_kw: ['최대 전력(kW)', 'Peak kW'], overloads: ['전력 과부하 차단 수', 'Feeder trips'], tripped_min: ['차단으로 멈춘 시간(분)', 'Trip downtime (min)'],
  transport_errors: ['운송 오류 수', 'Transport errors'], duplicate_executions: ['중복 운반', 'Duplicate trips'], wrong_pallet: ['잘못된 팔레트 운반', 'Wrong pallet'],
  premature_dispatch: ['미완성 팔레트 호출', 'Premature dispatch'], orders: ['운송 주문 수', 'Orders'], completed_orders: ['완료 주문 수', 'Completed orders'],
  order_completion_rate: ['주문 완료율', 'Order completion rate'], ack_timeouts: ['응답 시간초과', 'Ack timeouts'], retries: ['재전송', 'Retries'], ack_p50_ms: ['응답 지연 중앙값(ms)', 'Ack p50 (ms)'],
  ack_p95_ms: ['응답 지연 95%(ms)', 'Ack p95 (ms)'], duplicates_suppressed: ['중복 주문 차단', 'Duplicates suppressed'], battery_violations: ['배터리 부족 출동', 'Battery violations'],
  status_regressions: ['상태 역행', 'Status regressions'], qr_holds: ['QR 불일치 보류', 'QR holds'], amr_utilization: ['로봇 가동률', 'Robot utilisation'],
  mttr_main_s: ['메인 라인 복구 시간(초)', 'Main recovery time (s)'], production_recovery_s: ['생산 재개까지 시간(초)', 'Time to resume production (s)'], recovery_success: ['복구 성공률', 'Recovery success'],
  recurrences: ['재가동 직후 재고장', 'Re-faults after restart'], escalations: ['담당자 호출 수', 'Operator calls'], escalations_warranted: ['필요했던 호출 수', 'Warranted calls'],
  escalation_precision: ['호출 정확도', 'Call precision'], escalation_recall: ['필요 시 호출 비율', 'Call recall'], gate_blocks: ['안전 게이트 차단 수', 'Gate blocks'],
  stale_decisions: ['오래된 데이터로 한 판단', 'Decisions on stale data'], decisions: ['판단 횟수', 'Decisions'], operator_decisions: ['담당자 판단', 'Operator decisions'],
  forecast_error_first: ['첫 예측 오차(개)', 'First forecast error'], mean_regret: ['최선 대비 손실 차(평균)', 'Regret (mean)'], median_regret: ['최선 대비 손실 차(중앙값)', 'Regret (median)'],
  share_nonneg: ['최선 이상 비율', 'Share ≥ 0'], oracle_action_agreement: ['최선 조치 일치율', 'Agreement with best action'], n: ['에피소드 수', 'Episodes'],
  detection_precision: ['탐지 정밀도', 'Detection precision'], detection_recall: ['탐지 재현율', 'Detection recall'], detection_f1: ['탐지 F1', 'Detection F1'],
  false_alarms_per_h: ['시간당 오경보', 'False alarms / h'], mttd_s: ['탐지까지 시간(초)', 'Time to detect (s)'], macro_f1: ['원인 분류 정확도(F1)', 'Cause F1'],
  unknown_or_no_decision_rate: ['원인 미상 비율', 'Unknown-cause rate'], n_events: ['사건 수', 'Events'], jev_calls: ['Jev 호출 수', 'Jev calls'],
  jev_latency_p50_ms: ['Jev 응답시간 중앙값(ms)', 'Jev latency p50'], jev_latency_p95_ms: ['Jev 응답시간 95%(ms)', 'Jev latency p95'], jev_latency_p99_ms: ['Jev 응답시간 99%(ms)', 'Jev latency p99'],
  jev_errors: ['Jev 오류', 'Jev errors'], jev_input_tokens: ['Jev 입력 토큰', 'Jev input tokens'], jev_cost_usd: ['Jev 비용(USD)', 'Jev cost (USD)'], fallbacks: ['Jev 실패 대체', 'Fallbacks'],
  msgs_lost: ['유실 메시지', 'Messages lost'], msgs_dup: ['중복 메시지', 'Duplicate messages'], arm: ['비교군', 'Arm'], metric: ['지표', 'Metric'], contrast: ['비교', 'Contrast'],
  n_pairs: ['짝 수', 'Pairs'], mean_a: ['앞 비교군 평균', 'Mean (first)'], mean_b: ['뒤 비교군 평균', 'Mean (second)'], mean_diff: ['차이', 'Difference'],
  ci_low: ['95% 신뢰구간 하한', 'CI low'], ci_high: ['95% 신뢰구간 상한', 'CI high'], excludes_zero: ['차이가 0이 아님', 'Excludes 0'], mcnemar_p: ['유의확률(McNemar)', 'p (McNemar)'],
  hypothesis: ['가설', 'Hypothesis'], ablation: ['제거한 정보', 'Removed'], scenario: ['시나리오', 'Scenario'],
}
export const CONTRAST: Record<string, Pair> = {
  'R2-R1': ['Jev를 넣으면 나아지는가 (Jev 판단 − 기준 B)', 'Does Jev help? (R2 − R1)'], 'R4-R3': ['전력·통신 정보를 넣으면 나아지는가 (전체 − 안전게이트)', 'Power/network context (R4 − R3)'],
  'R4-R1': ['전체 시스템 대 기준 B', 'Full system vs baseline B'], 'R3-R2': ['증거 검증·안전 게이트 효과', 'Evidence checks + gate'], 'R1-R0': ['이상탐지 개선 효과', 'Detection improvement'],
  'R5-R4': ['최적화 계획기 대 전체 시스템', 'Planner vs full system'],
}
export const L = {
  action: (k: string, lang: Lang) => pick(ACTION, k, lang), why: (k: string, lang: Lang) => pick(ACTION_WHY, k, lang), state: (k: string, lang: Lang) => pick(STATE, k, lang),
  cause: (k: string, lang: Lang) => pick(CAUSE, k, lang), disp: (k: string, lang: Lang) => pick(DISPOSITION, k, lang), order: (k: string, lang: Lang) => pick(ORDER, k, lang),
  by: (k: string, lang: Lang) => pick(BY, k, lang), provider: (k: string, lang: Lang) => pick(PROVIDER, k, lang), arm: (k: string, lang: Lang) => pick(ARM, k, lang),
  fault: (k: string, lang: Lang) => pick(FAULT, k, lang), metric: (k: string, lang: Lang) => pick(METRIC, k, lang), contrast: (k: string, lang: Lang) => pick(CONTRAST, k, lang),
}
export const fmtDur = (s: number | null | undefined, lang: Lang) => {
  if (s === null || s === undefined || Number.isNaN(s)) return '—'
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60)
  return lang === 'ko' ? (h ? `${h}시간 ${m}분` : `${m}분`) : (h ? `${h} h ${m} min` : `${m} min`)
}
export const clock = (s: number) => `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`

/** Korean rendering of the feasibility / gate reasons produced by the backend (English source kept in the DB). */
export const reason = (r: string | null | undefined, lang: Lang): string => {
  if (!r || lang !== 'ko') return r ?? ''
  const rules: [RegExp, (m: RegExpMatchArray) => string][] = [
    [/^power ([\d.]+) kW > limit ([\d.]+) kW$/, m => `전력 ${m[1]}kW가 한도 ${m[2]}kW를 넘음`],
    [/^peak ([\d.]+) kW > limit ([\d.]+) kW$/, m => `최대 전력 ${m[1]}kW가 한도 ${m[2]}kW를 넘음`],
    [/^main line is not producing$/, () => '메인 라인이 생산 중이 아님'], [/^no main-line recovery needed$/, () => '메인 라인 복구가 필요 없음'],
    [/^both lines down \/ main state unknown$/, () => '두 라인 모두 정지 또는 메인 상태 미확인'], [/^sub not available$/, () => '예비 라인을 쓸 수 없음'],
    [/^sub already running$/, () => '예비 라인이 이미 가동 중'], [/^no main issue$/, () => '메인 라인에 문제가 없음'], [/^sub unavailable \/ already parallel$/, () => '예비 라인 불가 또는 이미 동시 가동'],
    [/^plan reachable$/, () => '계획 달성 가능(조정 불필요)'], [/^no transport shortage$/, () => '운반 수단 부족 없음'], [/^no quality incident on a producing main line$/, () => '불량 문제가 없음'],
    [/^evidence stale for the target line.*$/, () => '해당 라인 신호가 오래됨(신선도 검사)'], [/^not in the feasible action set$/, () => '실행 가능한 선택지가 아님'],
    [/^basic gate: feasible-list check only$/, () => '기본 게이트: 실행 가능 목록만 확인'], [/^both lines down: automatic motion prohibited$/, () => '두 라인 모두 정지: 자동 동작 금지'],
    [/^QR\/count evidence inconsistent.*$/, () => 'QR·수량 근거 불일치: 다시 확인 후 이동'],
    [/^full gate: .*passed$/, () => '전체 게이트: 신선도·정합성·인터록·전력 확인 통과'],
  ]
  for (const [re, f] of rules) { const m = r.match(re); if (m) return f(m) }
  return r
}
