import { useEffect, useState } from 'react'
import { useI18n } from '../i18n'
import { Badge, Button, Card, Chip, Field, IconBtn, Icon, Input, Kpi, Select, Stat, Tip } from '../components/ui'
import { L as T, clock, fmtDur, reason, type Lang } from '../v3labels'
import Twin from '../components/Twin'

/** SF AI OS v3 — 생산목표 인지형 AI 운영 센터 (가상 PLC·가상 MQTT 전용). 화면은 아이콘·짧은 이름 위주, 긴 설명은 ⓘ 안에 둔다. */
type View = 'live' | 'units' | 'log' | 'incidents' | 'transport' | 'results' | 'experiments' | 'verify' | 'setup'
const VIEWS: View[] = ['live', 'units', 'log', 'incidents', 'transport', 'results', 'experiments', 'verify', 'setup']
const j = (r: Response) => { if (!r.ok) return r.json().then(e => { throw new Error(e.detail ?? r.statusText) }); return r.json() }
const post = (u: string, b: any) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then(j)
const num = (v: any, d = 0) => (v === null || v === undefined || v === '' || Number.isNaN(+v)) ? '—' : Number(v).toFixed(d)
const pct = (v: any, d = 0) => (v === null || v === undefined || v === '') ? '—' : `${(+v * 100).toFixed(d)}%`
const tryJson = (s: any) => { try { return typeof s === 'string' ? JSON.parse(s) : s } catch { return s } }
const TONE: Record<string, any> = { RUNNING: 'success', STARTING: 'info', BLOCKED: 'warning', FAULT: 'error', MAINT: 'warning', READY: 'info', IDLE: 'neutral', OFF: 'neutral', TRIPPED: 'error' }
const Small = ({ children }: { children: any }) => <span className="mono" style={{ fontSize: 10, color: 'var(--color-text-disabled)', marginLeft: 4 }}>{children}</span>
const Bar = ({ v, w = 140 }: { v: number; w?: number }) => <span style={{ display: 'inline-block', width: w * Math.max(0, Math.min(1, v || 0)), height: 8, background: 'var(--color-primary-100)', borderRadius: 4, marginRight: 6, verticalAlign: 'middle' }} />
export const EVENT_NAME: Record<string, [string, string]> = { MAIN_FAULT: ['메인 라인 고장', 'main line fault'], SUB_FAULT: ['예비 라인 고장', 'backup line fault'], CT_DRIFT: ['사이클타임 지연', 'cycle-time drift'], DEFECT_RISE: ['불량 증가', 'defect surge'],
  NETWORK: ['통신 지연·유실', 'network delay / loss'], AMR_DOWN: ['AMR 운행 불가', 'AMR outage'], POWER_LIMIT: ['전력 한도 축소', 'power limit'], COUNTER_ANOMALY: ['PLC 계수 이상', 'PLC counter anomaly'], VISION_QR_MISMATCH: ['영상·QR 불일치', 'vision/QR mismatch'] }
const SPEEDS = [60, 240, 1000, 3000]

export default function V3() {
  const { lang } = useI18n()
  const lg = lang as Lang
  const K = (ko: string, en: string) => (lang === 'ko' ? ko : en)
  const initView = ((window.location.hash.split('/')[1] || 'live').split('?')[0]) as View
  const [view, setViewState] = useState<View>(VIEWS.includes(initView) ? initView : 'live')
  const setView = (v: View) => { setViewState(v); window.history.replaceState(null, '', `#v3/${v}`) }
  const [meta, setMeta] = useState<any>(null)
  const [sid, setSid] = useState<string | null>(null)
  const [ov, setOv] = useState<any>(null)
  const [opts, setOpts] = useState(false)
  const [form, setForm] = useState<any>({ scenario: 'RND', load: 'medium', seed: 0, arm: 'R4', provider: 'mock', speed: 240, unit_judge: 'mock', defect_image_ratio: '0.1', camera_mode: 'live' })
  const [err, setErr] = useState('')
  useEffect(() => {
    fetch('/api/v3/meta').then(j).then(m => { setMeta(m); if (m.real_jev_available) setForm((f: any) => ({ ...f, provider: 'typesafe', unit_judge: f.unit_judge === 'mock' ? 'typesafe' : f.unit_judge })) }).catch(e => setErr(String(e)))
    fetch('/api/v3/sessions').then(j).then((l: any[]) => { if (l.length) { const x = l[l.length - 1]; setSid(x.id); setForm((f: any) => ({ ...f, load: x.load, seed: x.seed, arm: x.arm, provider: x.provider, speed: x.speed, unit_judge: x.unit_judge ?? 'off', camera_mode: x.camera_mode ?? 'live', defect_image_ratio: x.defect_image_ratio ?? '' })) } }).catch(() => {})
  }, [])
  useEffect(() => { if (!sid) return; const t = () => fetch(`/api/v3/sessions/${sid}/overview`).then(j).then(setOv).catch(() => {}); t(); const h = setInterval(t, 1200); return () => clearInterval(h) }, [sid])
  const launch = (seed: number) => { setErr(''); setForm((f: any) => ({ ...f, seed })); post('/api/v3/sessions', { ...form, seed, scenario: 'RND', defect_image_ratio: form.defect_image_ratio === '' ? null : +form.defect_image_ratio }).then(s => { setSid(s.id); if (!['live', 'units', 'log', 'incidents', 'transport'].includes(view)) setView('live') }).catch(e => setErr(String(e))) }
  const ctl = (action: string, value?: number) => sid && post(`/api/v3/sessions/${sid}/control`, { action, value }).catch(e => setErr(String(e)))
  const nav: [View, any, string, string][] = [['live', Icon.factory, '실시간', 'Live'], ['units', Icon.camera, '제품 검사', 'Parts'], ['log', Icon.list, '판단 로그', 'Decisions'], ['incidents', Icon.alert, '사건', 'Incidents'],
    ['transport', Icon.robot, 'AMR 운송', 'Transport'], ['results', Icon.sigma, '누적 결과', 'Results'], ['experiments', Icon.flask, '실험', 'Experiments'], ['verify', Icon.shield, '작동 검증', 'Verify'], ['setup', Icon.gear, '공장 설정', 'Setup']]
  const needsSession = ['live', 'units', 'log', 'incidents', 'transport'].includes(view)
  const st = ov?.status
  const running = st?.state === 'RUNNING'
  return (
    <div className="shell">
      <nav className="side" aria-label="views">{nav.map(([k, ic, ko, en], i) => <div key={k} style={{ display: 'contents' }}>{i === 5 && <div className="side-sep" />}<button className={view === k ? 'is-active' : ''} onClick={() => setView(k)} title={K(ko, en)}>{ic}<span>{K(ko, en)}</span></button></div>)}</nav>
      <div className="flex flex-col gap-4" style={{ minWidth: 0 }}>
        {err && <div className="row caption" style={{ gap: 6, color: 'var(--color-error)' }} title={err}>{Icon.alert}{/offline|Failed to fetch|NetworkError/.test(err) ? K('서버에 연결할 수 없습니다', 'cannot reach the server') : err.replace(/^Error: /, '')}</div>}
        {needsSession && <Card>
          <div className="toolbar">
            <IconBtn variant="primary" icon={Icon.dice} label={K('새 랜덤 운영', 'New random course')} title={K('매번 다른 고장·불량·통신·AMR 상황이 무작위로 만들어집니다', 'a new random course of faults, defects, network and AMR events')} onClick={() => launch(Math.floor(Math.random() * 100000))} />
            {sid && <>
              <IconBtn icon={running ? Icon.pause : Icon.play} title={running ? K('일시정지', 'Pause') : K('재생', 'Play')} onClick={() => ctl(running ? 'pause' : 'resume')} disabled={st?.state === 'FINISHED'} />
              <IconBtn icon={Icon.step} title={K('10분 진행', '+10 min')} onClick={() => ctl('step', 600)} disabled={st?.state === 'FINISHED'} />
              <IconBtn icon={Icon.ffwd} title={K('교대 끝까지', 'Run to end')} onClick={() => ctl('run_to_end')} disabled={st?.state === 'FINISHED'} />
              <IconBtn icon={Icon.reset} title={K('같은 상황으로 처음부터', 'Restart same course')} onClick={() => ctl('reset')} />
              <div className="seg" title={K('배속', 'speed')}>{SPEEDS.map(s => <button key={s} className={Math.round(st?.speed ?? form.speed) === s ? 'is-active' : ''} onClick={() => { setForm((f: any) => ({ ...f, speed: s })); ctl('speed', s) }}>{s}×</button>)}</div>
            </>}
            <span className="grow" />
            {st && <>
              <span className="pill" title={K('운영 번호 — 같은 번호면 같은 상황이 재현됩니다', 'course no. (same no. = same situation)')}>{Icon.dice}#{st.seed}</span>
              <span className="pill" title={K('운영 방식', 'operating mode')}>{Icon.brain}{T.arm(st.arm, lg).split('(')[0].trim()}</span>
              <span className="pill" title={K('카메라', 'camera')}>{Icon.camera}{st.camera_mode === 'live' ? K('YOLO 실시간', 'YOLO live') : K('YOLO 저장값', 'YOLO stored')}</span>
              <span className="pill" title={K('제품별 최종 판정', 'per-part final judgment')}>{Icon.qa}{!st.unit_judge || st.unit_judge === 'off' ? K('YOLO 규칙', 'YOLO rule') : st.unit_judge === 'typesafe' ? K('실제 Jev', 'real Jev') : K('모의 Jev', 'mock Jev')}</span>
              {st.saved_run && <span className="pill ok" title={K('결과가 누적 DB와 CSV에 저장되었습니다', 'saved to the cumulative DB and CSV')}>{Icon.db}{K('저장됨', 'saved')}</span>}
            </>}
            <IconBtn icon={Icon.gear} title={K('실행 옵션', 'Run options')} active={opts} onClick={() => setOpts(!opts)} />
          </div>
          {st && <div style={{ marginTop: 10 }}><div className="row" style={{ justifyContent: 'space-between', fontSize: 11, color: 'var(--color-text-secondary)', marginBottom: 4 }}><span>{st.state === 'FINISHED' ? K('교대 종료', 'finished') : running ? K('진행 중', 'running') : K('멈춤', 'paused')}</span><span className="mono">{fmtDur(st.sim_time_s, lg)} / {fmtDur(st.shift_s, lg)}</span></div><div className="progress"><i style={{ width: `${100 * st.sim_time_s / st.shift_s}%` }} /></div></div>}
          {opts && <div className="drawer" style={{ marginTop: 12 }}><div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <Field label={K('목표 수준', 'Target')}><Select value={form.load} onChange={e => setForm({ ...form, load: e.target.value })} style={{ width: 110 }}><option value="low">{K('낮음', 'low')} 75%</option><option value="medium">{K('보통', 'medium')} 85%</option><option value="high">{K('높음', 'high')} 95%</option></Select></Field>
            <Field label={K('운영 방식', 'Mode')}><Select value={form.arm} onChange={e => setForm({ ...form, arm: e.target.value })} style={{ width: 220 }}>{['R0', 'R1', 'R2', 'R3', 'R4', 'R5'].map(a => <option key={a} value={a}>{T.arm(a, lg)}</option>)}</Select></Field>
            <Field label={K('라인 판단', 'Line decisions')}><Select value={form.provider} onChange={e => setForm({ ...form, provider: e.target.value })} style={{ width: 150 }}><option value="mock">{T.provider('MOCK_JEV', lg)}</option><option value="typesafe" disabled={!meta?.real_jev_available}>{T.provider('TYPESAFE_JEV', lg)}</option></Select></Field>
            <Field label={K('제품 판정', 'Per-part')}><Select value={form.unit_judge} onChange={e => setForm({ ...form, unit_judge: e.target.value })} style={{ width: 150 }}><option value="off">{K('YOLO 규칙', 'YOLO rule')}</option><option value="mock">{T.provider('MOCK_JEV', lg)}</option><option value="typesafe" disabled={!meta?.real_jev_available}>{T.provider('TYPESAFE_JEV', lg)}</option></Select></Field>
            <Field label={K('불량 이미지', 'Defect images')}><Select value={form.defect_image_ratio} onChange={e => setForm({ ...form, defect_image_ratio: e.target.value })} style={{ width: 110 }}><option value="">{K('기본', 'default')} ~3%</option><option value="0.1">10%</option><option value="0.3">30%</option><option value="0.5">50%</option></Select></Field>
            <Field label={K('카메라', 'Camera')}><Select value={form.camera_mode} onChange={e => setForm({ ...form, camera_mode: e.target.value })} style={{ width: 130 }}><option value="live">{K('YOLO 실시간', 'YOLO live')}</option><option value="replay">{K('YOLO 저장값', 'YOLO stored')}</option></Select></Field>
            <Field label={K('운영 번호', 'Course no.')}><Input type="number" value={form.seed} onChange={e => setForm({ ...form, seed: +e.target.value })} style={{ width: 100 }} /></Field>
            <IconBtn icon={Icon.play} label={K('이 번호로 실행', 'Run this no.')} onClick={() => launch(form.seed)} />
          </div></div>}
        </Card>}
        {view === 'live' && <Live ov={ov} sid={sid} K={K} lg={lg} go={setView} />}
        {view === 'units' && sid && <Units sid={sid} K={K} lg={lg} />}
        {view === 'log' && <Log sid={sid} K={K} lg={lg} />}
        {view === 'incidents' && sid && <Incidents sid={sid} K={K} lg={lg} />}
        {view === 'transport' && sid && <Transport sid={sid} K={K} lg={lg} />}
        {view === 'results' && <Results K={K} lg={lg} />}
        {view === 'experiments' && <Experiments K={K} lg={lg} />}
        {view === 'verify' && <Verify sid={sid} K={K} lg={lg} />}
        {view === 'setup' && <Setup K={K} />}
        {needsSession && !sid && view !== 'log' && <Card><div className="row" style={{ gap: 10, alignItems: 'center', color: 'var(--color-text-secondary)' }}>{Icon.dice}<span>{K('"새 랜덤 운영"을 누르면 시작합니다.', 'Press "New random course" to start.')}</span></div></Card>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------------------------------ 실시간 현황 */
function Chart({ series, target, shift, lg }: { series: any[]; target: number; shift: number; lg: Lang }) {
  const W = 640, H = 200, P = 36
  const ymax = Math.max(target * 1.15, ...series.map(s => s.good), 10)
  const x = (t: number) => P + (W - 2 * P) * t / shift
  const y = (v: number) => H - P - (H - 2 * P) * v / ymax
  const path = series.map((s, i) => `${i ? 'L' : 'M'}${x(s.t).toFixed(1)},${y(s.good).toFixed(1)}`).join(' ')
  return <div style={{ overflowX: 'auto' }}><svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: W }} role="img" aria-label="target vs actual">
    {[0, 0.5, 1].map(f => <g key={f}><line x1={P} x2={W - P} y1={y(ymax * f)} y2={y(ymax * f)} stroke="var(--color-divider)" /><text x={4} y={y(ymax * f) + 4} fontSize={10} fill="var(--color-text-secondary)">{Math.round(ymax * f)}</text></g>)}
    {[0, 2, 4, 6, 8].map(h => <text key={h} x={x(h * 3600) - 8} y={H - 12} fontSize={10} fill="var(--color-text-secondary)">{h}{lg === 'ko' ? '시' : 'h'}</text>)}
    <line x1={x(0)} y1={y(0)} x2={x(shift)} y2={y(target)} stroke="var(--color-text-disabled)" strokeDasharray="5 4" strokeWidth={1.5} />
    <line x1={P} x2={W - P} y1={y(target)} y2={y(target)} stroke="#c80" strokeDasharray="2 3" />
    <path d={path} fill="none" stroke="var(--color-primary-100)" strokeWidth={2.5} />
    <text x={W - P - 4} y={y(target) - 6} fontSize={10} textAnchor="end" fill="var(--color-text-secondary)">{lg === 'ko' ? `목표 ${target}` : `target ${target}`}</text>
  </svg></div>
}

const E_COLORS = { line: '#2563eb', aux: '#94a3b8', amr: '#f97316', deficit: '#fdba74' }
function EnergyBar({ e, K }: { e: any; K: any }) {
  const parts: [keyof typeof E_COLORS, number, string][] = [['line', e.line_kwh, K('생산 라인', 'lines')], ['aux', e.aux_kwh, K('부대설비', 'auxiliary')], ['amr', e.amr_charge_kwh, K('AMR 충전', 'AMR charging')], ['deficit', e.amr_battery_deficit_kwh, K('AMR 남은 충전분', 'AMR recharge owed')]]
  const tot = parts.reduce((a, p) => a + Math.max(0, +p[1] || 0), 0) || 1
  return <div>
    <div className="ebar">{parts.map(([k, v, n]) => <span key={k} title={`${n} ${num(v, 2)} kWh`} style={{ width: `${Math.max(+v > 0 ? 0.8 : 0, 100 * (+v || 0) / tot)}%`, background: E_COLORS[k] }} />)}</div>
    <div className="legend" style={{ marginTop: 8 }}>{parts.map(([k, v, n]) => <span key={k}><i style={{ background: E_COLORS[k] }} />{n} <b className="mono">{num(v, k === 'line' || k === 'aux' ? 1 : 2)}</b> kWh</span>)}</div>
  </div>
}

function Live({ ov, sid, K, lg, go }: { ov: any; sid: string | null; K: any; lg: Lang; go: (v: View) => void }) {
  if (!ov) return null
  const st = ov.status
  const proj = ov.projected_end && ov.forecast_basis ? ov.projected_end[ov.forecast_basis] : null
  const kw = ov.series?.[ov.series.length - 1]?.kw
  const e = ov.energy
  return <>
    <div className="kpis">
      <Kpi icon={Icon.target} label={K('목표 양품', 'Target')} value={ov.target} />
      <Kpi icon={Icon.box} label={K('현재 양품', 'Good so far')} value={num(ov.good_so_far)} sub={pct(ov.good_so_far / ov.target)} tip={K('카메라·QR로 확인된 양품 수', 'camera/QR-confirmed good parts')} tone="good" />
      <Kpi icon={Icon.trend} label={K('종료 시 예상', 'Projected')} value={num(proj)} tone={proj != null && proj < ov.target ? 'warn' : 'good'} tip={`${K('현재 운영 그대로일 때 프로그램 예측', 'program forecast for the current routing')}: ${T.action(ov.forecast_basis, lg)}`} />
      <Kpi icon={Icon.gauge} label={K('달성 가능성', 'Chance')} value={pct(ov.target_probability)} tone={ov.target_probability < 0.5 ? 'bad' : ov.target_probability < 0.8 ? 'warn' : 'good'} tip={K('사이클타임 변동만 반영한 추정. 앞으로 생길 고장은 모릅니다', 'cycle-time variation only; future faults unknown')} />
      <Kpi icon={Icon.power} label={K('현재 전력', 'Power')} value={`${num(kw, 0)} kW`} sub={`${K('한도', 'limit')} ${num(ov.power.limit_kw)} kW`} tone={kw > ov.power.limit_kw * 0.9 ? 'warn' : 'info'} tip={ov.power.limit_source === 'live feeder telemetry' ? K('한도: 실시간 계측값', 'limit from live meter') : K('한도: 설계 정격', 'limit: nameplate')} />
      <Kpi icon={Icon.plug} label={K('공장 총 전력량', 'Factory energy')} value={e ? `${num(e.factory_total_kwh, 0)} kWh` : '—'} sub={e && ov.good_so_far ? `${num(e.factory_total_kwh / ov.good_so_far, 3)} kWh/${K('개', 'part')}` : ''} tip={K('라인 + 부대설비 + AMR 충전 + 교대 종료 시 남은 AMR 충전 필요분', 'lines + auxiliary + AMR charging + AMR recharge still owed at shift end')} />
    </div>
    {sid && <Card icon={Icon.twin} title={K('디지털 트윈', 'Digital twin')} subtitle={K('2D·3D는 같은 배치입니다. 주황 막대 = 전력 흐름(굵을수록 부하 큼, 빨강 = 차단), 파랑 = 라인 AMR, 보라 = 예비 AMR, 노랑 = 충전 중, 갈색 상자 = 팔레트(40개)', 'Same layout in 2D/3D. Orange bars = power, blue = line AMR, purple = reserve AMR, yellow = charging, brown = pallet')}><Twin sid={sid} lg={lg} initial={window.location.hash.includes('3d') ? '3d' : '2d'} /></Card>}
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))' }}>
      <Card icon={Icon.trend} title={K('목표 대비 실적', 'Target vs actual')} subtitle={K('파란 실선 = 누적 양품, 회색 점선 = 시간 비례 계획, 주황 점선 = 일일 목표', 'blue = good, grey = plan, orange = target')}><Chart series={ov.series} target={ov.target} shift={st.shift_s} lg={lg} /></Card>
      {e && <Card icon={Icon.power} title={K('전력 구성', 'Energy breakdown')} subtitle={K(`AMR은 배터리가 50% 아래로 내려가고 할 일이 없으면 충전소로 갑니다(25% 미만이면 반드시). 충전기 전력은 배전반 부하에 포함됩니다. 배터리 ${e.battery_kwh} kWh, 충전 효율 ${pct(e.charger_eff)}.`, 'AMRs go to the charger below 50 % when idle (always below 25 %); the charger draws from the feeder')}>
        <EnergyBar e={e} K={K} />
        <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: 'wrap' }}><span className="pill">{Icon.battery}{K(`충전 ${e.amr_charges}회`, `${e.amr_charges} charges`)}</span><span className="pill">{Icon.power}{K('지금 충전', 'charging now')} {num(e.amr_charging_now_kw, 1)} kW</span></div>
      </Card>}
    </div>
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
      <Card icon={Icon.factory} title={K('라인 상태', 'Lines')} subtitle={K('PLC가 보낸 값입니다(통신 지연 포함)', 'values sent by the PLC, incl. network delay')}>
        {Object.entries(ov.lines).map(([k, l]: any) => <div key={k} className="row" style={{ gap: 8, alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--color-outline-300)' }}>
          <b style={{ width: 70, fontSize: 13 }}>{k === 'main' ? K('메인', 'Main') : K('예비', 'Backup')}</b><Badge tone={TONE[l.observed_state] ?? 'neutral'}>{T.state(l.observed_state, lg)}</Badge>{l.stale ? <Badge tone="warning">{K('신호 지연', 'stale')}</Badge> : null}
          {l.fault_code && <span className="caption">{T.fault(l.fault_code, lg)}</span>}<span className="grow" style={{ flex: 1 }} /><span className="caption mono">{l.ct_nominal}{K('초/개', ' s/pc')}</span></div>)}
      </Card>
      <Card icon={Icon.alert} title={K('열린 문제', 'Open issues')}>{ov.incidents_open.length ? <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{ov.incidents_open.map((i: any) => <Badge key={i.incident_id} tone="warning">{T.cause(i.cause, lg)} · {clock(i.opened_at)}</Badge>)}</div> : <span className="row caption" style={{ gap: 6 }}>{Icon.check}{K('없음', 'none')}</span>}</Card>
      <Card icon={Icon.brain} title={K('선택지별 예측', 'Forecast per option')} subtitle={`${K('프로그램이 계산한 교대 종료 시 양품. 최적화로 계산한 최대 가능 양품', 'program forecast of good parts at shift end; optimisation bound')}: ${num(ov.cpsat_bound)}`}>
        {ov.projected_end && Object.entries(ov.projected_end).map(([a, v]: any) => { const ok = ov.feasible?.includes(a); return <div key={a} className="row" style={{ gap: 8, alignItems: 'center', padding: '4px 0', opacity: ok ? 1 : 0.45 }} title={ok ? '' : reason(ov.infeasible?.[a], lg)}>
          <span style={{ fontSize: 12, flex: 1 }}>{T.action(a, lg)}</span><Bar v={(v || 0) / Math.max(ov.target, 1) / 1.2} w={90} /><span className="mono" style={{ width: 44, textAlign: 'right', fontSize: 12 }}>{num(v)}</span>
          {ok ? (v >= ov.target ? <span style={{ color: '#15803d' }}>{Icon.check}</span> : <span style={{ width: 18 }} />) : <span className="caption" style={{ width: 18 }}>—</span>}</div> })}
      </Card>
    </div>
    {sid && <Events sid={sid} K={K} lg={lg} />}
    {ov.result && <Card icon={Icon.sigma} title={K('교대 결과', 'Shift result')} right={<IconBtn icon={Icon.sigma} label={K('누적 결과 보기', 'Open results')} onClick={() => go('results')} />}>
      <div className="kpis">
        <Kpi icon={Icon.target} label={K('목표 달성', 'Target met')} value={ov.result.target_met ? K('달성', 'met') : K('미달', 'missed')} tone={ov.result.target_met ? 'good' : 'bad'} sub={`${num(ov.result.good_end)} / ${ov.result.target}`} />
        <Kpi icon={Icon.shield} label={K('위험 조치 허용', 'Unsafe allowed')} value={num(ov.result.forbidden_allowed)} tone={ov.result.forbidden_allowed ? 'bad' : 'good'} />
        <Kpi icon={Icon.qa} label={K('불량 출하', 'Defects shipped')} value={num(ov.result.defects_shipped)} tone={ov.result.defects_shipped ? 'warn' : 'good'} />
        <Kpi icon={Icon.robot} label={K('운송 오류', 'Transport errors')} value={num(ov.result.transport_errors)} tone={ov.result.transport_errors ? 'warn' : 'good'} />
        <Kpi icon={Icon.plug} label={K('공장 총 전력량', 'Factory energy')} value={`${num(ov.result.factory_kwh_total, 0)} kWh`} sub={`${num(ov.result.kwh_per_good, 3)} kWh/${K('개', 'part')}`} />
        <Kpi icon={Icon.battery} label={K('AMR 충전', 'AMR charging')} value={`${num(ov.result.amr_charge_kwh, 2)} kWh`} sub={K(`${ov.result.amr_charges}회`, `${ov.result.amr_charges}×`)} />
      </div>
    </Card>}
  </>
}

/* ------------------------------------------------------------------------------------------------ 제품별 검사 */
function Units({ sid, K, lg }: { sid: string; K: any; lg: Lang }) {
  const [d, setD] = useState<any>(null)
  const [truth, setTruth] = useState(false)
  useEffect(() => { const t = () => fetch(`/api/v3/sessions/${sid}/units?limit=40&researcher=${truth}`).then(j).then(setD).catch(() => {}); t(); const h = setInterval(t, 2000); return () => clearInterval(h) }, [sid, truth])
  if (!d) return null
  if (d.judge === 'off' || d.judge == null) return <Card><div className="row caption" style={{ gap: 8, alignItems: 'center' }}>{Icon.qa}{K('제품 판정이 꺼져 있습니다(YOLO 규칙). ⚙ 실행 옵션에서 모의/실제 Jev를 고르고 새로 실행하세요.', 'Per-part judgment is off. Pick mock/real Jev in ⚙ options and start again.')}</div></Card>
  const done = d.units.filter((u: any) => u.final_correct !== undefined && u.final_correct !== null)
  return <div className="flex flex-col gap-4">
    <Card icon={Icon.camera} title={<span className="flowsteps"><b>{Icon.camera}{K('촬영', 'image')}</b><span className="arr">→</span><b>YOLO {K('1차', 'stage 1')}</b><span className="arr">→</span><b>{K('불량 확률·손실 계산', 'P(defect) & cost')}</b><span className="arr">→</span><b>{Icon.brain}Jev {K('최종', 'final')}</b><span className="arr">→</span><b>{K('재검사', 're-inspect')}</b></span>} subtitle={K('① 카메라가 공개 산업 부품 이미지(양품·불량 무작위)를 촬영 → ② YOLO가 실제로 분석해 불량 박스를 찾음 → ③ 프로그램이 불량 확률과 처분별 예상 손실을 계산 → ④ Jev가 합격·폐기·재검사 중 하나를 고름 → ⑤ 재검사는 2분 뒤 사람이 확인', '① camera image (good/defective at random) → ② YOLO analyses it → ③ program computes defect probability and expected cost → ④ Jev chooses accept / reject / re-inspect → ⑤ re-inspection confirmed by a person after 2 min')}
      right={<IconBtn icon={Icon.eye} label={K('정답', 'truth')} title={K('정답 보기(평가용)', 'show truth (evaluation)')} active={truth} onClick={() => setTruth(!truth)} />}>
      <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
        {d.summary.map((s: any) => <Stat key={s.judge_provider + s.disposition} label={`${T.provider(s.judge_provider, lg)} · ${T.disp(s.disposition, lg)}`} value={`${s.n}${K('개', '')}`} sub={s.lat != null ? `${K('평균 응답', 'avg')} ${num(s.lat)}ms · ${K('같은 상황 재사용', 'reused')} ${s.cached}` : ''} />)}
        {truth && <Stat label={K('최종 처분 정답률(평가용)', 'Final disposition accuracy')} value={`${done.filter((u: any) => u.final_correct).length}/${done.length}`} />}
      </div>
    </Card>
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))' }}>{d.units.map((u: any) => <UnitCard key={u.line_id + u.unit_seq} u={u} K={K} lg={lg} truth={truth} />)}</div>
  </div>
}

function UnitCard({ u, K, lg, truth }: { u: any; K: any; lg: Lang; truth: boolean }) {
  const raw = u.raw_json || {}
  const [h, w] = raw.orig_shape || [700, 700]
  const S = 140
  const pc = u.state_sent?.program_computed
  const tone = (x: string) => (x === 'ACCEPT' ? 'success' : x === 'REJECT' ? 'error' : 'warning')
  return <Card title={`${K('제품', 'Part')} #${u.unit_seq} · ${clock(u.simulation_time)}`} subtitle={`${u.part_type ?? ''} · ${u.camera_source?.startsWith('live') ? K('YOLO 실시간 분석', 'YOLO live') : K('저장된 YOLO 결과', 'stored YOLO output')}`}>
    <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
      <div style={{ position: 'relative', width: S, height: S, flex: 'none' }}>
        <img src={`/api/v3/images/${u.image_id}?w=280`} loading="lazy" alt="" style={{ width: S, height: S, borderRadius: 6, border: '1px solid var(--color-divider)' }} />
        <svg width={S} height={S} style={{ position: 'absolute', left: 0, top: 0 }}>{(raw.detections || []).map((d: any, i: number) => <rect key={i} x={d.box.x1 / w * S} y={d.box.y1 / h * S} width={(d.box.x2 - d.box.x1) / w * S} height={(d.box.y2 - d.box.y1) / h * S} fill="none" stroke="#e5484d" strokeWidth={2} />)}</svg>
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        <div>② YOLO: {u.yolo_detections ? <b>{K(`불량 의심 ${u.yolo_detections}곳`, `${u.yolo_detections} defect box(es)`)} ({K('최고 신뢰도', 'max conf')} {num(u.yolo_max_conf, 2)})</b> : K('불량 박스 없음', 'no defect box')} <span className="caption">· {num(u.yolo_ms, 1)}ms</span></div>
        {pc && <div>③ {K('불량 확률', 'P(defect)')} <b>{pct(pc.p_defect_given_evidence)}</b> · {K('예상 손실', 'expected loss')} {Object.entries(pc.expected_cost).map(([k, v]: any) => `${T.disp(k, lg).split('(')[0]} ${v}`).join(' / ')}</div>}
        <div>④ {T.provider(u.judge_provider, lg)}: <Badge tone={tone(u.disposition)}>{T.disp(u.disposition, lg)}</Badge>{u.judge_latency_ms != null && <span className="caption"> {num(u.judge_latency_ms)}ms{u.judge_cached ? K(' (같은 상황 재사용)', ' (reused)') : ''}</span>}</div>
        {u.probabilities && <div className="caption">{Object.entries(u.probabilities).sort((a: any, b: any) => b[1] - a[1]).map(([k, v]: any) => `${T.disp(k, lg).split('(')[0]} ${pct(v)}`).join(' · ')}</div>}
        <div>⑤ {K('최종', 'final')}: {u.final_disposition ? <Badge tone={tone(u.final_disposition)}>{T.disp(u.final_disposition, lg)}</Badge> : <Badge tone="warning">{K('재검사 진행 중', 're-inspection pending')}</Badge>}{u.reinspect_result && <span className="caption"> ({u.reinspect_result === 'GOOD_CONFIRMED' ? K('사람 확인: 양품', 'confirmed good') : K('사람 확인: 불량', 'confirmed defect')})</span>}</div>
        {u.judge_error && <div className="caption" style={{ color: 'var(--color-error)' }}>{K('Jev 오류', 'Jev error')}: {u.judge_error}</div>}
        {truth && u.truth_defective !== undefined && <div className="caption">{K('정답', 'truth')}: {u.truth_defective ? `${K('불량', 'defect')} (${u.truth_defect_type})` : K('양품', 'good')} · YOLO {u.yolo_correct ? '✓' : '✗'} · {K('최종', 'final')} {u.final_correct == null ? '…' : u.final_correct ? '✓' : '✗'}</div>}
      </div>
    </div>
  </Card>
}

/* ------------------------------------------------------------------------------------------------ 판단 로그 */
function Log({ sid, K, lg }: { sid: string | null; K: any; lg: Lang }) {
  const [src, setSrc] = useState<string>(sid ? 'session' : '')
  const [arch, setArch] = useState<any[]>([])
  const [log, setLog] = useState<any[]>([])
  const [sel, setSel] = useState<number>(0)
  useEffect(() => { fetch('/api/v3/archive').then(j).then(setArch).catch(() => {}) }, [])
  useEffect(() => { if (!src && sid) setSrc('session') }, [sid])
  useEffect(() => {
    if (!src) return
    const url = src === 'session' ? `/api/v3/sessions/${sid}/decision-log` : `/api/v3/archive/${src}/decision-log`
    const t = () => fetch(url).then(j).then(d => setLog(d.log)).catch(() => {}); t()
    if (src === 'session') { const h = setInterval(t, 3000); return () => clearInterval(h) }
  }, [src, sid])
  const realArch = arch.filter(a => a.group === 'real_jev' && /-R[234]$/.test(a.episode))
  const r = log[sel]
  const sentence = (d: any) => { const s = d.situation; if (!s) return T.by(d.decided_by, lg); return lg === 'ko'
    ? `메인 라인 ${T.state(s.main_state, lg)}${s.main_fault_code ? `(${T.fault(s.main_fault_code, lg)})` : ''} · 예비 라인 ${T.state(s.sub_state, lg)} · 양품 ${num(s.good_so_far)}/${s.target}개 · 남은 시간 ${fmtDur(s.remaining_s, lg)}`
    : `main ${T.state(s.main_state, lg)}${s.main_fault_code ? ` (${T.fault(s.main_fault_code, lg)})` : ''} · backup ${T.state(s.sub_state, lg)} · good ${num(s.good_so_far)}/${s.target} · ${fmtDur(s.remaining_s, lg)} left` }
  return <div className="flex flex-col gap-4">
    <Card icon={Icon.list} title={K('판단 로그', 'Decision log')} subtitle={K('각 기록은 판단 당시 DB에 저장된 값만으로 다시 구성됩니다(나중 정보 없음). 30분 뒤 결과는 검토용으로만 붙입니다.', 'Rebuilt only from what the DB held at decision time; the 30-min outcome is added for review only.')}>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        {sid && <Chip active={src === 'session'} onClick={() => { setSrc('session'); setSel(0) }}>{K('지금 실행 중인 세션', 'Current session')}</Chip>}
        {realArch.map(a => <Chip key={a.episode} active={src === `${a.group}/${a.exp}/${a.episode}`} onClick={() => { setSrc(`${a.group}/${a.exp}/${a.episode}`); setSel(0) }}>{K('실제 Jev 기록', 'real Jev')} · {a.episode.split('-')[0]} · {T.arm(a.episode.split('-').pop()!, lg)}</Chip>)}
      </div>
    </Card>
    <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(300px, 1fr) 2.4fr' }}>
      <Card title={`${K('판단 목록', 'Decisions')} (${log.length})`}><div style={{ maxHeight: 720, overflow: 'auto' }}>{log.map((d, i) => <div key={d.decision_id} onClick={() => setSel(i)} style={{ cursor: 'pointer', padding: '6px 4px', borderBottom: '1px solid var(--color-divider)', background: sel === i ? 'var(--color-primary-50)' : undefined }}>
        <div className="row" style={{ gap: 6, alignItems: 'center' }}><span className="mono" style={{ fontSize: 11 }}>{clock(d.t)}</span><b style={{ fontSize: 12 }}>{T.action(d.final, lg)}</b>{d.gate === 'BLOCK' && <Badge tone="error">{K('차단됨', 'blocked')}</Badge>}</div>
        <div className="caption">{T.by(d.decided_by, lg)}{d.jev ? ` · ${T.provider(d.jev.provider, lg)}` : ''}</div><div className="caption" style={{ fontSize: 11 }}>{sentence(d)}</div></div>)}</div></Card>
      <div className="flex flex-col gap-4">{r && <>
        <Card title={`${clock(r.t)} — ${K('상황', 'Situation')}`} subtitle={sentence(r)}>
          {r.situation && <div className="row" style={{ gap: 18, flexWrap: 'wrap' }}>
            <Stat label={K('목표까지 부족', 'Gap to target')} value={`${num(r.situation.gap)}${K('개', '')}`} sub={`${K('필요 속도', 'needed')} ${num(r.situation.required_rate_per_h)}${K('개/시간', '/h')}`} />
            <Stat label={K('메인 라인 예상 복구', 'Main ETA')} value={fmtDur(r.situation.eta_main_s, lg)} sub={K('정비 기준표 값(실제 수리시간은 모름)', 'maintenance catalogue estimate')} />
            <Stat label={K('사이클타임 추정', 'Cycle time est.')} value={`${num(r.situation.ct_main_est, 1)}${K('초', ' s')}`} sub={`${K('기준', 'nominal')} ${r.situation.ct_main_nominal}${K('초', ' s')}`} />
            <Stat label={K('전력 한도', 'Power limit')} value={`${num(r.situation.limit_kw)} kW`} sub={r.situation.limit_source === 'live feeder telemetry' ? K('실시간 계측', 'live meter') : K('설계 정격(실시간 값 모름)', 'nameplate')} />
            <Stat label={K('최대 가능 양품(최적화)', 'Max achievable')} value={num(r.situation.cpsat_bound)} />
          </div>}
          {r.situation?.incidents?.length > 0 && <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>{r.situation.incidents.map((i: any, k: number) => <Badge key={k} tone="warning">{T.cause(i.cause, lg)}</Badge>)}</div>}
        </Card>
        {r.options && <Card title={K('선택지 비교 — 예측은 프로그램 계산, 확률은 Jev 응답', 'Options — forecasts by the program, probabilities by Jev')}>
          <div style={{ overflowX: 'auto' }}><table className="table"><thead><tr><th>{K('선택지', 'option')}</th><th>{K('교대 종료 시 양품', 'good at end')}</th><th>{K('부족', 'short')}</th><th>{K('전력량', 'energy')}</th><th>{K('최대 전력', 'peak')}</th><th>{K('가능 여부', 'possible?')}</th><th>{K('Jev 확률', 'Jev prob.')}</th></tr></thead>
            <tbody>{[...r.options].sort((a: any, b: any) => (b.jev_probability ?? -1) - (a.jev_probability ?? -1) || Number(b.feasible) - Number(a.feasible)).map((o: any) => <tr key={o.action} style={{ opacity: o.feasible ? 1 : 0.5, background: o.action === r.final ? 'var(--color-primary-50)' : undefined }}>
              <td><b style={{ fontSize: 12 }}>{T.action(o.action, lg)}</b><div className="caption">{T.why(o.action, lg)}</div></td><td>{num(o.projected_good_end)}</td><td>{num(o.projected_shortfall)}</td><td>{num(o.energy_kwh, 1)} kWh</td><td>{num(o.peak_kw)} kW</td>
              <td>{o.feasible ? <Badge tone="success">{K('가능', 'yes')}</Badge> : <span className="caption">{reason(o.excluded_reason, lg)}</span>}</td><td>{o.jev_probability != null ? <><Bar v={o.jev_probability} w={80} />{pct(o.jev_probability)}</> : '—'}</td></tr>)}</tbody></table></div>
        </Card>}
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))' }}>
          <Card title={K('누가 어떻게 골랐나', 'Who decided and how')}>
            <table className="table"><tbody>
              <tr><td>{K('판단 주체', 'decided by')}</td><td>{T.by(r.decided_by, lg)}</td></tr>
              <tr><td>{K('제안된 조치', 'proposed')}</td><td>{T.action(r.proposed, lg)}</td></tr>
              <tr><td>{K('안전 게이트', 'safety gate')}</td><td>{r.gate === 'PASS' ? <Badge tone="success">{K('통과', 'pass')}</Badge> : <Badge tone="error">{K('차단', 'blocked')}</Badge>} <span className="caption">{reason(r.gate_reason, lg)}</span></td></tr>
              <tr><td>{K('최종 조치', 'final action')}</td><td><b>{T.action(r.final, lg)}</b></td></tr>
              <tr><td>{K('보낸 가상 명령', 'virtual commands')}</td><td>{r.commands.length ? r.commands.map((c: any, i: number) => <div key={i} className="caption">{c.command} → {c.target === 'main' ? K('메인', 'main') : K('예비', 'backup')} ({T.state(c.before, lg)} → {T.state(c.after, lg)})</div>) : <span className="caption">{K('없음(조건이 맞으면 나중에 실행)', 'none (executed later when conditions allow)')}</span>}</td></tr>
              {r.outcome_30min && <tr><td>{K('30분 뒤 양품 증가', 'good +30 min')}</td><td>{r.outcome_30min.good_delta == null ? K('아직 30분 안 지남', 'not yet') : `${num(r.outcome_30min.good_delta)}${K('개', '')}`}</td></tr>}
            </tbody></table>
          </Card>
          {r.jev ? <Card title={K('Jev 호출 기록 (증거)', 'Jev call record (evidence)')}>
            <table className="table"><tbody>
              <tr><td>{K('호출 대상', 'provider')}</td><td><b>{T.provider(r.jev.provider, lg)}</b></td></tr>
              <tr><td>{K('응답한 모델 버전', 'resolved model')}</td><td className="mono">{r.jev.resolved_model ?? '—'}</td></tr>
              <tr><td>{K('응답 시간', 'latency')}</td><td>{num(r.jev.latency_ms)} ms{r.jev.cached ? K(' (같은 상황 재사용)', ' (reused)') : ''}</td></tr>
              <tr><td>{K('토큰 / 비용', 'tokens / cost')}</td><td>{r.jev.tokens ? `${r.jev.tokens.input} / ${r.jev.tokens.output}` : '—'} · {r.jev.cost_usd != null ? `$${Number(r.jev.cost_usd).toFixed(6)}` : '—'}</td></tr>
              <tr><td>{K('Jev가 본 원인', 'cause (Jev)')}</td><td>{T.cause(r.jev.cause, lg)}</td></tr>
              <tr><td>{K('사람이 필요하다고 본 확률', 'needs a person')}</td><td>{pct(r.jev.needs_operator_p)}</td></tr>
              <tr><td>{K('선택 확신도', 'confidence')}</td><td>{pct(r.jev.selected_confidence)}</td></tr>
              <tr><td>{K('요청 지문(해시)', 'request hash')}</td><td className="mono" style={{ fontSize: 11 }}>{r.jev.request_hash}</td></tr>
              {r.jev.error && <tr><td>{K('오류', 'error')}</td><td style={{ color: 'var(--color-error)' }}>{r.jev.error}</td></tr>}
            </tbody></table>
          </Card> : <Card title={K('Jev 호출 없음', 'No Jev call')}><div className="caption">{r.decided_by === 'RULE' ? K('이 운영 방식은 고정 규칙으로 판단합니다(비교 기준).', 'This mode decides by fixed rules (baseline).') : K('이 판단은 Jev를 쓰지 않았습니다.', 'Jev was not used for this decision.')}</div></Card>}
        </div>
        {r.jev && <Card title={K('Jev에게 보낸 판단 기준 (원문 그대로)', 'Criteria sent to Jev (verbatim)')} subtitle={K('Jev는 아래 지시문과 각 선택지 설명(프로그램 예측 포함)만 보고 고릅니다. 정답이나 미래 정보는 보내지 않습니다.', 'Jev sees only these instructions and option texts (with program forecasts); no truth or future information is sent.')}>
          {['preferred_action', 'cause_class', 'needs_operator'].filter(q => r.jev.instructions?.[q]).map(q => [q, r.jev.instructions[q]]).map(([q, ins]: any) => <div key={q} style={{ marginBottom: 8 }}><div style={{ fontWeight: 700, fontSize: 12 }}>{q === 'preferred_action' ? K('질문 1 · 어떤 조치를 할까', 'Q1 · which action') : q === 'cause_class' ? K('질문 2 · 원인은 무엇인가', 'Q2 · root cause') : K('질문 3 · 사람이 필요한가', 'Q3 · need a person?')}</div><div className="mono" style={{ fontSize: 11 }}>{ins}</div></div>)}
          <div style={{ fontWeight: 700, fontSize: 12, marginTop: 6 }}>{K('선택지 설명(보낸 그대로)', 'option texts (as sent)')}</div>
          {r.options?.filter((o: any) => o.criteria_sent).map((o: any) => <div key={o.action} className="mono" style={{ fontSize: 11 }}><b>{o.action}</b>: {o.criteria_sent}</div>)}
          <details style={{ marginTop: 8 }}><summary className="caption">{K('보낸 상태 데이터 전체 보기(JSON)', 'full state sent (JSON)')}</summary><pre className="mono" style={{ fontSize: 10, maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(r.jev.state_sent, null, 1)}</pre></details>
        </Card>}
      </>}</div>
    </div>
  </div>
}

/* ------------------------------------------------------------------------------------------------ 사건 상세 */
function Incidents({ sid, K, lg }: { sid: string; K: any; lg: Lang }) {
  const [list, setList] = useState<any[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [d, setD] = useState<any>(null)
  useEffect(() => { const t = () => fetch(`/api/v3/sessions/${sid}/incidents`).then(j).then((l: any[]) => { setList(l); setSel(cur => cur ?? (l.find(i => i.cause_hint === 'EQUIPMENT') ?? l[0])?.incident_id ?? null) }); t(); const h = setInterval(t, 3000); return () => clearInterval(h) }, [sid])
  useEffect(() => { if (sel) fetch(`/api/v3/sessions/${sid}/incidents/${sel}`).then(j).then(setD) }, [sel, sid])
  const jr = d?.jev_responses?.[0], pol = d?.policy?.[0], snap = d?.snapshots?.[0]?.input_features_json
  const stages = d ? [
    [K('① 이상 감지', '① Detected'), `${T.cause(d.incident.cause_hint, lg)}`],
    [K('② 근거 데이터', '② Evidence'), K(`카메라 ${d.vision_raw.length} · QR ${d.qr.length} · PLC ${d.plc.length} · 통신 ${d.network.length}건`, `camera ${d.vision_raw.length} · QR ${d.qr.length} · PLC ${d.plc.length} · network ${d.network.length}`)],
    [K('③ Jev 판단', '③ Jev'), jr ? `${T.provider(jr.provider, lg)}: ${T.action(jr.selected_action, lg)}` : K('Jev 미사용', 'no Jev')],
    [K('④ 안전 게이트', '④ Safety gate'), pol ? `${pol.safety_gate_result === 'PASS' ? K('통과', 'pass') : K('차단', 'blocked')} → ${T.action(pol.final_action, lg)}` : '—'],
    [K('⑤ 가상 제어', '⑤ Virtual control'), d.commands.filter((c: any) => !pol || c.decision_id === pol.decision_id).map((c: any) => `${c.command}`).join(', ') || K('이 판단의 즉시 명령 없음', 'no immediate command')],
    [K('⑥ 계획 영향', '⑥ Plan impact'), snap ? K(`부족 ${num(snap.gap)}개 · 최대 가능 ${num(snap.max_achievable_good)}개`, `gap ${num(snap.gap)} · max ${num(snap.max_achievable_good)}`) : '—'],
  ] : []
  return <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(240px, 1fr) 3fr' }}>
    <Card title={K('문제 목록', 'Issues')}><div style={{ maxHeight: 560, overflow: 'auto' }}>{list.map(i => <div key={i.incident_id} onClick={() => setSel(i.incident_id)} style={{ cursor: 'pointer', padding: '6px 4px', borderBottom: '1px solid var(--color-divider)', background: sel === i.incident_id ? 'var(--color-primary-50)' : undefined }}>
      <Badge tone="warning">{T.cause(i.cause_hint, lg)}</Badge> <span className="caption">{clock(i.opened_at)}{i.closed_at ? ` → ${clock(i.closed_at)}` : K(' (진행 중)', ' (open)')}</span></div>)}</div></Card>
    <div className="flex flex-col gap-4">{d && <>
      <Card title={K('처리 흐름', 'Flow')}><div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{stages.map(([h, s]) => <div key={h} style={{ border: '1px solid var(--color-divider)', borderRadius: 8, padding: '8px 10px', minWidth: 170, maxWidth: 260 }}><div style={{ fontWeight: 700, fontSize: 12 }}>{h}</div><div className="caption" style={{ marginTop: 2 }}>{s}</div></div>)}</div></Card>
      <Card title={K('카메라 원본 결과 (YOLO가 낸 JSON, 수정 불가 저장)', 'Camera raw result (YOLO JSON, immutable)')}>
        <div style={{ maxHeight: 260, overflow: 'auto' }}>{d.vision_raw.slice(0, 3).map((v: any) => <pre key={v.frame_hash + v.unit_seq} className="mono" style={{ fontSize: 10, whiteSpace: 'pre-wrap', borderBottom: '1px solid var(--color-divider)' }}>{`${K('제품', 'part')} #${v.unit_seq} · ${v.source} · ${num(v.processing_ms, 1)}ms\n`}{JSON.stringify(tryJson(v.raw_json), null, 1).slice(0, 700)}</pre>)}</div>
      </Card>
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        <Card title={K('PLC 신호', 'PLC signals')}><div style={{ maxHeight: 220, overflow: 'auto' }}><table className="table"><thead><tr><th>{K('시각', 'time')}</th><th>{K('도착 지연', 'delay')}</th><th>{K('라인', 'line')}</th><th>{K('상태', 'state')}</th><th>{K('누적 계수', 'count')}</th></tr></thead><tbody>{d.plc.map((r: any, i: number) => <tr key={i}><td>{clock(r.simulation_time)}</td><td>{num(r.received_sim_s - r.simulation_time, 1)}{K('초', 's')}</td><td>{r.line_id === 'MAIN' ? K('메인', 'main') : K('예비', 'backup')}</td><td>{T.state(r.plc_state, lg)}</td><td>{r.cycle_count}</td></tr>)}</tbody></table></div></Card>
        <Card title={K('통신 기록', 'Network')}><div style={{ maxHeight: 220, overflow: 'auto' }}><table className="table"><tbody>{d.network.map((r: any, i: number) => <tr key={i}><td className="mono" style={{ fontSize: 10 }}>{r.topic}</td><td>{r.dropped ? <Badge tone="error">{K('유실', 'lost')}</Badge> : `${num(r.delay_ms)}ms`}</td><td>{r.duplicate ? K('중복', 'dup') : ''}</td></tr>)}</tbody></table></div></Card>
      </div>
    </>}</div>
  </div>
}

/* ------------------------------------------------------------------------------------------------ 운송 */
function Transport({ sid, K, lg }: { sid: string; K: any; lg: Lang }) {
  const [d, setD] = useState<any>(null)
  useEffect(() => { const t = () => fetch(`/api/v3/sessions/${sid}/transport`).then(j).then(setD); t(); const h = setInterval(t, 2500); return () => clearInterval(h) }, [sid])
  if (!d) return null
  const ds = d.dispatcher, fs = d.fleet_stats
  return <div className="flex flex-col gap-4">
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
      <Card title={K('가상 운반로봇(AMR)', 'Virtual AMRs')} subtitle={K('라인 담당 AMR 2대 + 다른 구역 예비 AMR 1대 — 모두 가상', '2 line AMRs + 1 reserve AMR from another area — all virtual')}><table className="table"><tbody>{d.fleet_truth_view.map((c: any) => <tr key={c.carrier_id}><td>{c.type === 'AMR' ? K('라인 AMR', 'line AMR') : K('예비 AMR', 'reserve AMR')} <Small>{c.carrier_id}</Small></td><td>{c.available ? <Badge tone="success">{K('사용 가능', 'available')}</Badge> : <Badge tone="warning">{K('사용 불가', 'unavailable')}</Badge>}</td><td>{c.busy ? K('운반 중', 'busy') : K('대기', 'idle')}</td><td>{K('배터리', 'battery')} {c.battery}%{c.charging ? K(' 충전 중', ' charging') : ''}</td></tr>)}</tbody></table>
        <div className="caption" style={{ marginTop: 6 }}>{K('라인 출구 팔레트', 'pallets at outlets')}: {K('메인', 'main')} {d.outlets.main} · {K('예비', 'backup')} {d.outlets.sub}</div></Card>
      <Card title={K('운송 요약', 'Summary')}><table className="table"><tbody>{[['orders', ds.orders], ['completed_orders', ds.completed_orders], ['ack_timeouts', ds.timeouts], ['retries', ds.retries], ['duplicates_suppressed', fs.duplicates_suppressed], ['duplicate_executions', fs.duplicate_executions], ['wrong_pallet', fs.wrong_pallet], ['premature_dispatch', fs.premature], ['qr_holds', ds.holds], ['ack_p50_ms', ds.ack_latency_p50_ms]].map(([k, v]: any) => <tr key={k}><td>{T.metric(k, lg)}</td><td style={{ textAlign: 'right' }}>{num(v)}</td></tr>)}</tbody></table></Card>
    </div>
    <Card title={K('운송 주문 기록', 'Transport orders')} subtitle={K('같은 팔레트에 같은 고유번호(멱등 키)를 쓰므로 통신이 중복돼도 두 번 옮기지 않습니다', 'one idempotency key per pallet transfer: duplicates are never executed twice')}><div style={{ maxHeight: 360, overflow: 'auto' }}><table className="table"><thead><tr><th>{K('시각', 'time')}</th><th>{K('방식', 'mode')}</th><th>{K('운반 장비', 'carrier')}</th><th>{K('팔레트', 'pallet')}</th><th>{K('시도', 'try')}</th><th>{K('출발 조건 확인', 'pre-checks')}</th></tr></thead>
      <tbody>{d.orders.map((o: any) => <tr key={o.order_id + o.attempt}><td>{clock(o.issued_at)}</td><td>{o.dispatch_mode === 'VALIDATED' ? K('검증 후 호출', 'validated') : K('계수 기준 호출', 'count-triggered')}</td><td>{o.carrier_id ?? o.carrier}</td><td className="mono" style={{ fontSize: 11 }}>{o.payload_id}</td><td>{o.attempt}</td>
        <td className="caption">{Object.entries(tryJson(o.prerequisites) || {}).filter(([, v]) => typeof v === 'boolean').map(([k, v]: any) => `${({ qr_match: K('QR 일치', 'QR match'), quality_released: K('품질 합격', 'quality'), destination_capacity_ok: K('도착지 여유', 'destination'), route_available: K('경로', 'route'), carrier_available: K('장비', 'carrier'), count_confirmed: K('수량 확인', 'count') } as any)[k] ?? k} ${v ? '✓' : '✗'}`).join(' · ')}</td></tr>)}</tbody></table></div></Card>
    <Card title={K('운반 상태 메시지', 'Status messages')}><div style={{ maxHeight: 240, overflow: 'auto' }}><table className="table"><tbody>{d.statuses.map((s: any, i: number) => <tr key={i}><td>{clock(s.received_sim_s)}</td><td>#{s.seq}</td><td><Badge tone={s.state === 'COMPLETED' ? 'success' : s.state === 'FAILED' ? 'error' : 'info'}>{T.order(s.state, lg)}</Badge></td><td className="caption">{s.note}</td></tr>)}</tbody></table></div></Card>
  </div>
}

/* ------------------------------------------------------------------------------------------------ 작동 검증 */
function Verify({ sid, K, lg }: { sid: string | null; K: any; lg: Lang }) {
  const [st, setSt] = useState<any>(null)
  const [y, setY] = useState<any>(null)
  const [jv, setJv] = useState<any>(null)
  const [busy, setBusy] = useState('')
  useEffect(() => { fetch('/api/v3/verify/status').then(j).then(setSt); if (window.location.hash.includes('run')) { runY(); runJ() } }, [])
  const runY = () => { setBusy('y'); post('/api/v3/verify/yolo', { n: 6 }).then(setY).finally(() => setBusy('')) }
  const runJ = () => { setBusy('j'); post('/api/v3/verify/jev', { session_id: sid }).then(setJv).finally(() => setBusy('')) }
  return <div className="flex flex-col gap-4">
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <span className="pill" title={K('학습된 모델 파일을 이 컴퓨터(Apple M5 GPU)에서 실제로 실행하고 저장된 결과와 비교합니다', 'runs the trained model on this Mac and compares with stored output')}>{Icon.camera}{K('YOLO 실제 실행', 'YOLO runs for real')}</span>
      <span className="pill" title={K('지금 상황을 TypeSafe 서버에 한 번 보내고 요청·응답 원문을 보여 줍니다. API 키는 표시·기록되지 않습니다', 'sends one request to TypeSafe and shows the exact request/response; the key is never shown')}>{Icon.brain}{K('Jev 실제 호출', 'Real Jev call')}</span>
      <span className="pill" title={K('생산·설비·통신·운반은 시뮬레이션이며 실제 장비 연결 코드는 없습니다', 'everything else is simulated; no code connects to real equipment')}>{Icon.shield}{K('장비는 모두 가상', 'Equipment is virtual')}</span></div>
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))' }}>
      <Card title={K('YOLO 준비 상태', 'YOLO status')}>{st && <table className="table"><tbody>
        <tr><td>{K('사용 가능', 'available')}</td><td>{st.yolo.available ? <Badge tone="success">{K('예', 'yes')}</Badge> : <Badge tone="error">{st.yolo.reason}</Badge>}</td></tr>
        <tr><td>{K('모델 파일', 'weights')}</td><td className="mono" style={{ fontSize: 11 }}>{st.yolo.weights} ({st.yolo.weights_mb} MB)</td></tr>
        <tr><td>{K('파일 지문(SHA-256)', 'file hash')}</td><td className="mono" style={{ fontSize: 10 }}>{st.yolo.weights_sha256}</td></tr>
        <tr><td>{K('검증용 이미지', 'held-out images')}</td><td>{st.yolo.held_out_images}{K('장(학습에 안 쓴 공개 부품 이미지)', ' (not used in training)')}</td></tr>
        {st.yolo.measured && <tr><td>{K('측정 성능', 'measured')}</td><td>{K('불량을 찾아낸 비율', 'recall')} {pct(st.yolo.measured.recall)} · {K('불량 판정의 정확도', 'precision')} {pct(st.yolo.measured.precision)}</td></tr>}
      </tbody></table>}</Card>
      <Card title={K('Jev 준비 상태', 'Jev status')}>{st && <table className="table"><tbody>
        <tr><td>{K('실제 호출 허용', 'real calls enabled')}</td><td>{st.jev.opted_in ? <Badge tone="success">{K('예', 'yes')}</Badge> : <Badge tone="warning">{st.jev.reason}</Badge>}</td></tr>
        <tr><td>{K('서버 주소', 'endpoint')}</td><td className="mono" style={{ fontSize: 11 }}>{st.jev.endpoint}</td></tr>
        {st.jev.pilot && <tr><td>{K('지금까지 실제 실험 기록', 'recorded real experiment')}</td><td>{K(`${st.jev.pilot.episodes}회 교대 · Jev 호출 ${st.jev.pilot.jev_calls}회 · 비용 $${st.jev.pilot.jev_cost_usd} · 실패 ${st.jev.pilot.fallbacks}회`, `${st.jev.pilot.episodes} shifts · ${st.jev.pilot.jev_calls} calls · $${st.jev.pilot.jev_cost_usd} · ${st.jev.pilot.fallbacks} failures`)}</td></tr>}
      </tbody></table>}</Card>
    </div>
    <Card title={K('YOLO 지금 실행해 보기', 'Run YOLO now')} right={<Button variant="primary" onClick={runY} disabled={busy === 'y'}>{busy === 'y' ? K('실행 중…', 'running…') : K('무작위 이미지 6장 분석', 'Analyse 6 random images')}</Button>}>
      {y && (y.ok ? <>
        <div className="caption" style={{ marginBottom: 8 }}>{K(`장치 ${String(y.device).toUpperCase()} · 정답과 일치 ${y.correct}/${y.n} · 저장된 결과와 같은 박스 ${y.matches_stored}/${y.n}`, `device ${y.device} · correct ${y.correct}/${y.n} · same as stored ${y.matches_stored}/${y.n}`)}</div>
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>{y.rows.map((r: any) => <figure key={r.image} style={{ margin: 0 }}>
          <img src={r.annotated} alt="" style={{ width: '100%', borderRadius: 8, border: '1px solid var(--color-divider)' }} />
          <figcaption style={{ fontSize: 12, lineHeight: 1.6 }}><div>{r.predicted_defect ? <Badge tone="error">{K(`YOLO: 불량 ${r.n_boxes}곳 (${num(r.max_confidence, 2)})`, `YOLO: ${r.n_boxes} defect(s)`)}</Badge> : <Badge tone="success">{K('YOLO: 이상 없음', 'YOLO: no defect')}</Badge>} <span className="caption">{num(r.inference_ms, 1)}ms</span></div>
            <div className="caption">{K('정답(평가용)', 'truth')}: {r.truth_defect ? `${K('불량', 'defect')} · ${r.truth_defect_type}` : K('양품', 'good')} · {r.correct ? K('맞음', 'correct') : K('틀림', 'wrong')} · {K('저장 결과와', 'vs stored')} {r.matches_stored ? K('동일', 'same') : K('다름', 'different')}</div></figcaption>
          <details><summary className="caption">{K('YOLO 원본 JSON', 'raw JSON')}</summary><pre className="mono" style={{ fontSize: 10, maxHeight: 200, overflow: 'auto' }}>{JSON.stringify({ ...r.raw_json, path: undefined }, null, 1)}</pre></details></figure>)}</div>
      </> : <div className="caption">{y.reason}</div>)}
    </Card>
    <Card title={K('Jev에 지금 한 번 물어보기', 'Ask Jev once now')} subtitle={sid ? K('현재 세션의 지금 상황을 보냅니다. 같은 상황 재사용 없이 새로 호출합니다.', 'Sends the current situation of the running session; cache bypassed.') : K('실행 중인 세션이 없으면 S3(메인 장기 고장) 상황을 만들어 보냅니다.', 'Without a session an S3 situation is created.')}
      right={<Button variant="primary" onClick={runJ} disabled={busy === 'j'}>{busy === 'j' ? K('질의 중…', 'asking…') : K('실제 Jev 호출', 'Call real Jev')}</Button>}>
      {jv && (jv.ok ? <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))' }}>
        <div><div style={{ fontWeight: 700 }}>{K('받은 응답', 'Response')}</div>
          <table className="table"><tbody><tr><td>{K('응답 모델', 'model')}</td><td className="mono">{jv.response?.model}</td></tr><tr><td>{K('응답 시간', 'latency')}</td><td>{num(jv.latency_ms)} ms</td></tr>
            <tr><td>{K('토큰(입력/출력)', 'tokens')}</td><td>{jv.response?.usage?.input_tokens} / {jv.response?.usage?.output_tokens}</td></tr><tr><td>{K('선택한 조치', 'chosen')}</td><td><b>{T.action(jv.parsed?.action, lg)}</b></td></tr>
            <tr><td>{K('원인', 'cause')}</td><td>{T.cause(jv.parsed?.cause, lg)}</td></tr></tbody></table>
          {Object.entries(jv.parsed?.action_probs || {}).sort((a: any, b: any) => b[1] - a[1]).map(([k, v]: any) => <div key={k} style={{ fontSize: 12 }}><Bar v={v} />{pct(v)} · {T.action(k, lg)}</div>)}
          <details style={{ marginTop: 6 }}><summary className="caption">{K('응답 원문(JSON)', 'raw response')}</summary><pre className="mono" style={{ fontSize: 10, maxHeight: 260, overflow: 'auto' }}>{JSON.stringify(jv.response, null, 1)}</pre></details></div>
        <div><div style={{ fontWeight: 700 }}>{K('보낸 요청 (그대로)', 'Request sent (verbatim)')}</div><div className="caption">{jv.endpoint} · {jv.note}</div>
          <pre className="mono" style={{ fontSize: 10, maxHeight: 420, overflow: 'auto' }}>{JSON.stringify(jv.request, null, 1)}</pre></div>
      </div> : <div className="caption" style={{ color: 'var(--color-error)' }}>{jv.reason ?? jv.error}</div>)}
    </Card>
  </div>
}

/* ------------------------------------------------------------------------------------------------ 공장 설정 */
function Setup({ K }: { K: any }) {
  const [cfg, setCfg] = useState<any>(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { fetch('/api/v3/factory').then(j).then(setCfg) }, [])
  if (!cfg) return null
  const set = (path: (string | number)[], v: any) => { const c = structuredClone(cfg); let o = c; for (const p of path.slice(0, -1)) o = o[p]; o[path[path.length - 1]] = v; setCfg(c) }
  const n = (path: (string | number)[], label: string) => { let v: any = cfg; for (const p of path) v = v[p]; return <Field key={path.join('.')} label={label}><Input type="number" value={v} onChange={e => set(path, +e.target.value)} style={{ width: 120 }} /></Field> }
  return <Card title={K('공장 설정', 'Factory setup')} subtitle={K('모든 값은 시뮬레이션용 가정입니다. 저장하면 다음 실행부터 적용됩니다.', 'All values are simulation assumptions; applied to the next run.')}
    right={<Button variant="primary" onClick={() => fetch('/api/v3/factory', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) }).then(j).then(r => setMsg(K(`저장됨 · 교대당 최대 양품: 메인 ${r.main_capacity}개 / 예비 ${r.sub_capacity}개`, `saved · main ${r.main_capacity} / backup ${r.sub_capacity} per shift`))).catch(e => setMsg(String(e)))}>{K('저장', 'Save')}</Button>}>
    {msg && <div className="caption" style={{ marginBottom: 8 }}>{msg}</div>}
    <div className="caption">{K('생산 계획', 'Production plan')}</div>
    <div className="row" style={{ gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>{n(['plan', 'target_good_qty'], K('목표 양품 수(개)', 'target good parts'))}{n(['plan', 'shift_s'], K('교대 시간(초)', 'shift (s)'))}</div>
    {cfg.lines.map((l: any, i: number) => <div key={l.line_id}><div className="caption">{l.role === 'main' ? K('메인 라인', 'Main line') : K('예비 라인', 'Backup line')}</div><div className="row" style={{ gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
      {n(['lines', i, 'ct_s'], K('1개 만드는 시간(초)', 'cycle time (s)'))}{n(['lines', i, 'yield_rate'], K('양품률(0~1)', 'yield (0–1)'))}{n(['lines', i, 'startup_s'], K('기동 시간(초)', 'start-up (s)'))}{n(['lines', i, 'run_kw'], K('가동 전력(kW)', 'running kW'))}{n(['lines', i, 'idle_kw'], K('대기 전력(kW)', 'idle kW'))}</div></div>)}
    <div className="caption">{K('전력', 'Power')}</div><div className="row" style={{ gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>{n(['power', 'nameplate_limit_kw'], K('배전 한도(kW)', 'feeder limit kW'))}{n(['power', 'aux_kw'], K('부대설비 전력(kW)', 'auxiliary kW'))}{n(['power', 'trip_downtime_s'], K('과부하 차단 시 정지(초)', 'trip outage (s)'))}</div>
    <div className="caption">{K('운반', 'Transport')}</div><div className="row" style={{ gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>{n(['fleet', 'n_amr'], K('운반로봇 대수', 'robots'))}{n(['fleet', 'amr_speed_mps'], K('로봇 속도(m/s)', 'robot m/s'))}{n(['fleet', 'n_reserve_amr'], K('예비 AMR', 'reserve AMRs'))}{n(['fleet', 'route_m'], K('운반 거리(m)', 'route m'))}{n(['fleet', 'pallet_qty'], K('팔레트당 개수', 'parts per pallet'))}{n(['fleet', 'out_buffer_pallets'], K('출구에 둘 수 있는 팔레트', 'outlet pallets'))}{n(['fleet', 'battery_min_pct'], K('필수 충전(%)', 'must charge %'))}{n(['fleet', 'opportunity_pct'], K('대기 중 충전(%)', 'idle charge %'))}{n(['fleet', 'battery_kwh'], K('배터리(kWh)', 'battery kWh'))}{n(['fleet', 'motion_kw'], K('주행 전력(kW)', 'driving kW'))}{n(['fleet', 'idle_kw'], K('대기 전력(kW)', 'standby kW'))}{n(['fleet', 'charger_kw'], K('충전기(kW)', 'charger kW'))}{n(['fleet', 'charger_eff'], K('충전 효율', 'charger eff.'))}</div>
    <div className="caption">{K('통신·판단', 'Network & decisions')}</div><div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>{n(['network', 'median_delay_ms'], K('평균 통신 지연(ms)', 'delay ms'))}{n(['network', 'plc_sample_s'], K('PLC 보고 주기(초)', 'PLC period s'))}{n(['network', 'stale_after_s'], K('오래된 신호 기준(초)', 'stale after s'))}{n(['decision_interval_s'], K('판단 주기(초)', 'decision period s'))}{n(['human_response_s'], K('담당자 응답 시간(초)', 'operator response s'))}</div>
  </Card>
}

/* ------------------------------------------------------------------------------------------------ 실험 결과 */
function Experiments({ K, lg }: { K: any; lg: Lang }) {
  const [list, setList] = useState<any[]>([])
  const [sel, setSel] = useState<any>(null)
  const [d, setD] = useState<any>(null)
  useEffect(() => { fetch('/api/v3/experiments').then(j).then(l => { setList(l); const pref = l.find((x: any) => x.group === 'real_jev') ?? l[0]; if (pref) setSel(pref) }) }, [])
  useEffect(() => { if (sel) fetch(`/api/v3/experiments/${sel.group}/${sel.exp}`).then(j).then(setD) }, [sel])
  const parse = (csv: string) => { const lines = (csv || '').trim().split('\n'); if (lines.length < 2) return []; const split = (l: string) => l.match(/("([^"]|"")*"|[^,]*)(,|$)/g)!.map(c => c.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"')).slice(0, -1); const h = split(lines[0]); return lines.slice(1).map(l => Object.fromEntries(split(l).map((v, i) => [h[i], v]))) }
  const cell = (k: string, v: string) => { if (k === 'arm' || k === 'ablation') return T.arm(v, lg); if (k === 'contrast') return T.contrast(v, lg); if (k === 'metric') return T.metric(v, lg); if (k === 'excludes_zero') return v === 'True' ? K('예', 'yes') : v === 'False' ? K('아니오', 'no') : v; if (v === '' || isNaN(+v)) return v; return (+v).toFixed(Math.abs(+v) >= 100 ? 0 : 3) }
  const Tab = ({ name, cols }: { name: string; cols?: string[] }) => { const rows = parse(d?.tables?.[name]); if (!rows.length) return <div className="caption">—</div>; const c = cols?.filter(x => x in rows[0]) ?? Object.keys(rows[0]); return <div style={{ overflowX: 'auto', maxHeight: 440 }}><table className="table"><thead><tr>{c.map(x => <th key={x}>{T.metric(x, lg)}</th>)}</tr></thead><tbody>{rows.map((r, i) => <tr key={i}>{c.map(x => <td key={x} style={{ fontSize: 12 }}>{cell(x, r[x])}</td>)}</tr>)}</tbody></table></div> }
  const expName: Record<string, string> = { pilot: K('예비 실험', 'pilot'), main: K('본 실험', 'main'), oracle: K('최선 조치 비교', 'oracle'), ablation: K('정보 제거 실험', 'ablation'), sensitivity: K('가정 민감도', 'sensitivity') }
  return <div className="flex flex-col gap-4">
    <UnitStudy K={K} lg={lg} />
    {(['real_jev', 'mock'] as const).map(g => <Card key={g} title={g === 'real_jev' ? K('실제 TypeSafe Jev로 한 실험', 'Experiments with the real TypeSafe Jev') : K('모의 Jev로 한 실험 (프로그램 점검용 — Jev 성능 근거 아님)', 'Mock-Jev experiments (software check — not evidence about Jev)')}>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>{list.filter(x => x.group === g).map(x => <Chip key={x.exp} active={sel?.exp === x.exp && sel?.group === g} onClick={() => setSel(x)}>{expName[x.exp] ?? x.exp} · {x.summary?.episodes ?? '?'}{K('회 교대', ' shifts')}{x.summary?.jev_calls ? K(` · 호출 ${x.summary.jev_calls}회 · $${x.summary.jev_cost_usd}`, ` · ${x.summary.jev_calls} calls`) : ''}</Chip>)}</div>
    </Card>)}
    {d && <>
      <Card title={K('핵심 비교 (같은 고장·같은 난수로 짝지어 비교)', 'Key comparisons (paired: same faults, same random seeds)')} right={<Badge tone={d.group === 'mock' ? 'warning' : 'success'}>{d.group === 'mock' ? K('모의 — 점검용', 'mock') : K('실제 Jev', 'real Jev')}</Badge>}
        subtitle={K('"차이"는 앞 비교군 − 뒤 비교군입니다. 신뢰구간이 0을 포함하지 않으면 "차이가 0이 아님 = 예"입니다. 목표 달성 비교의 유의확률이 0.05 미만이면 우연이 아닐 가능성이 큽니다.', 'Difference = first − second; "excludes 0" means the 95% interval does not contain 0; McNemar p < 0.05 is significant.')}>
        <Tab name="T4_main_paired_primary" cols={['contrast', 'metric', 'n_pairs', 'mean_a', 'mean_b', 'mean_diff', 'ci_low', 'ci_high', 'excludes_zero', 'mcnemar_p']} />
      </Card>
      <Card title={K('운영 방식별 요약', 'By operating mode')}><Tab name="T4b_by_arm" /></Card>
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(440px, 1fr))' }}>
        <Card title={K('판단·복구', 'Decisions & recovery')}><Tab name="T6_decision_recovery" cols={['arm', 'mttr_main_s', 'production_recovery_s', 'recurrences', 'escalations', 'gate_blocks', 'decisions', 'mean_regret', 'oracle_action_agreement']} /></Card>
        <Card title={K('운송', 'Transport')}><Tab name="T7_transport" cols={['arm', 'orders', 'completed_orders', 'order_completion_rate', 'duplicate_executions', 'duplicates_suppressed', 'wrong_pallet', 'premature_dispatch', 'transport_errors']} /></Card>
        <Card title={K('전력·통신·Jev 응답', 'Power, network, Jev runtime')}><Tab name="T8_electrical_ot" cols={['arm', 'energy_kwh', 'kwh_per_good', 'max_kw', 'overloads', 'jev_calls', 'jev_latency_p50_ms', 'jev_latency_p95_ms', 'jev_cost_usd', 'fallbacks']} /></Card>
        <Card title={K('이상 감지·원인 판단', 'Detection & cause')}><Tab name="T5_detection" /></Card>
      </div>
      {d.tables.T9_ablation && <Card title={K('정보를 하나씩 뺐을 때', 'Removing one information group')}><Tab name="T9_ablation" cols={['ablation', 'metric', 'n_pairs', 'mean_a', 'mean_b', 'mean_diff', 'ci_low', 'ci_high']} /></Card>}
      <Card title={K('그래프 (영문)', 'Figures')}><div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>{d.figures.map((f: string) => <figure key={f} style={{ margin: 0 }}><img src={`/api/v3/experiments/${d.group}/${d.exp}/figures/${f}`} alt={f} style={{ maxWidth: 520, border: '1px solid var(--color-divider)', borderRadius: 8 }} /></figure>)}</div></Card>
    </>}
  </div>
}

/* ------------------------------------------------------------------------------------------------ 2단계 판정 파이프라인 실험 */
function UnitStudy({ K, lg }: { K: any; lg: Lang }) {
  const [d, setD] = useState<any>(null)
  const [parts, setParts] = useState<any[]>([])
  useEffect(() => { fetch('/api/v3/unit-study').then(j).then(setD).catch(() => {}); fetch('/api/v3/unit-study/real_jev/parts?limit=200&only_disagree=false').then(j).then((r: any[]) => setParts(r.filter(p => (+p.yolo_boxes > 0) !== (p.JEV_disp === 'REJECT')).slice(0, 12))).catch(() => {}) }, [])
  if (!d || (!d.real_jev && !d.mock)) return null
  const flowName = (s1: string, fin: string) => `${s1 === 'DEFECT_BOX' ? K('YOLO 불량 의심', 'YOLO: defect') : K('YOLO 이상 없음', 'YOLO: no box')} → Jev ${T.disp(fin, lg).split('(')[0]}`
  const Section = ({ g }: { g: 'real_jev' | 'mock' }) => { const s = d[g]; if (!s?.pipeline) return null; return <Card title={g === 'real_jev' ? K('2단계 자동 판정 — YOLO 1차 → 실제 TypeSafe Jev 최종', 'Two-stage inspection — YOLO stage 1 → real TypeSafe Jev final') : K('2단계 자동 판정 — 모의 Jev(점검용)', 'Two-stage inspection — mock Jev (check)')}
    subtitle={K(`계획 수량 ${s.n_per_run}개만큼 양품·불량 이미지를 무작위로 1개씩 흘렸습니다. 기존 YOLO11n이 1차로 불량 박스를 찾고(${s.camera === 'live' ? '실시간 분석' : '저장 결과'}), Jev가 그 결과와 프로그램이 계산한 불량 확률·예상 손실을 받아 최종 처분했습니다. 실행 ${s.seeds.length * s.ratios.length}회 · 제품 ${s.parts}개 · Jev 호출 ${s.jev_calls}회(같은 상황 재사용 ${s.jev_cached}회) · 오류 ${s.jev_errors}회`, `${s.parts} parts · ${s.jev_calls} Jev calls`)}>
    <div style={{ overflowX: 'auto' }}><table className="table"><thead><tr><th>{K('불량 이미지 비율', 'defective share')}</th><th>{K('1차 YOLO가 불량으로 본 수', 'stage-1 flagged')}</th><th>{K('최종: 합격 / 폐기 / 사람 재검사', 'final: accept / reject / re-inspect')}</th><th>{K('자동 처리율', 'automation rate')}</th><th>{K('YOLO가 놓친 불량 → Jev가 잡은 수', 'hidden defects caught by Jev')}</th><th>{K('불량 유출', 'escaped')}</th><th>{K('양품 폐기', 'good scrapped')}</th><th>{K('최종 정확도', 'final accuracy')}</th><th>{K('100개당 예상 손실', 'loss / 100')}</th></tr></thead>
      <tbody>{s.pipeline.map((t: any) => <tr key={t.ratio}><td>{pct(t.ratio)}</td><td>{t.stage1_flagged} / {t.parts}</td><td>{t.final_accept} / {t.final_reject} / {t.final_reinspect}</td><td><b>{pct(t.automation_rate, 1)}</b></td><td>{t.hidden_defects_caught_by_stage2} / {t.hidden_defects_after_stage1}</td><td>{t.escaped}</td><td>{t.scrapped_good}</td><td>{pct(t.final_accuracy, 1)}</td><td>{num(t.loss_per_100, 1)}</td></tr>)}</tbody></table></div>
    <div className="caption" style={{ marginTop: 8 }}>{K('1차 결과별로 Jev가 최종 처리한 내역', 'How Jev finalised each stage-1 result')}: {s.flows.map((f: any) => `${pct(f.ratio)} · ${flowName(f.stage1, f.final)} ${f.parts}${K('개', '')} (${K('실제 불량', 'defective')} ${f.truly_defective})`).join(' · ')}</div>
    <img src={`/api/v3/unit-study/${g}/figure`} alt="" style={{ maxWidth: 900, width: '100%', marginTop: 8, border: '1px solid var(--color-divider)', borderRadius: 8 }} />
  </Card> }
  return <>
    <Section g="real_jev" />
    {parts.length > 0 && <Card title={K('Jev가 1차 결과를 그대로 따르지 않고 재검사로 돌린 제품 (실제 Jev 기록)', 'Parts where Jev did not simply follow stage 1 (real Jev)')}>
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>{parts.map((p: any) => <div key={p.seed + '-' + p.ratio + '-' + p.idx} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
        <img src={`/api/v3/images/${p.image_id}?w=168`} loading="lazy" alt="" style={{ width: 84, height: 84, borderRadius: 6, border: '1px solid var(--color-divider)' }} />
        <div style={{ fontSize: 11, lineHeight: 1.5 }}><div>{K('1차 YOLO', 'stage 1')}: {+p.yolo_boxes ? K(`불량 의심 ${p.yolo_boxes}곳(${num(p.yolo_max_conf, 2)})`, `${p.yolo_boxes} box`) : K('박스 없음', 'no box')}</div><div>{K('불량 확률(계산)', 'P(defect)')} {pct(+p.p_defect)}</div>
          <div><b>{K('최종 Jev', 'final Jev')}: {T.disp(p.JEV_disp, lg).split('(')[0]}</b></div>
          <div className="caption">{K('정답(평가용)', 'truth')}: {+p.truth_defective ? K('불량', 'defect') : K('양품', 'good')}</div></div></div>)}</div>
    </Card>}
    <Section g="mock" />
  </>
}

/* 이번 랜덤 운영에서 실제로 일어난 일 — 평가용 정답(판단 계층에는 전달되지 않음) */
function Events({ sid, K, lg }: { sid: string; K: any; lg: Lang }) {
  const [show, setShow] = useState(false)
  const [t, setT] = useState<any>(null)
  useEffect(() => { if (show) fetch(`/api/v3/sessions/${sid}/truth?researcher=true`).then(j).then(setT) }, [show, sid])
  return <Card icon={Icon.eye} title={K('이번 운영에서 일어난 일', 'What happened this course')} subtitle={K('무작위로 만들어진 사건(평가용 정답)입니다. 판단 AI는 이 목록을 보지 못하고 실시간 관측만으로 판단합니다.', 'Randomly generated events (evaluation truth); the decision layer never sees this list.')}
    right={<IconBtn icon={Icon.eye} label={show ? K('숨기기', 'Hide') : K('보기', 'Show')} active={show} onClick={() => setShow(!show)} />} body={show}>
    {show && t ? <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{t.ground_truth.map((g: any) => <span key={g.fault_id} className="pill" title={g.params}>{Icon.alert}<b className="mono">{clock(g.fault_start)}</b>{(EVENT_NAME[g.hidden_fault_type] ?? [g.hidden_fault_type, g.hidden_fault_type])[lg === 'ko' ? 0 : 1]}
      <span className="caption">{g.true_repair_s ? fmtDur(g.true_repair_s, lg) : g.fault_end ? fmtDur(g.fault_end - g.fault_start, lg) : ''}</span></span>)}</div> : <span />}
  </Card>
}

/* ------------------------------------------------------------------------------------------------ 누적 결과 (DB·CSV) */
const R_METRICS: [string, string, string, number, boolean][] = [   // key, ko, en, decimals, percent
  ['target_met', '목표 달성률', 'target met', 0, true], ['good_end', '양품 수', 'good parts', 0, false], ['defects_per_1000_good', '불량 출하/1000개', 'defects / 1000 good', 1, false], ['good_to_target', '목표 대비 생산', 'output / target', 0, true],
  ['unsafe_authorization_rate', '위험 조치 허용률', 'unsafe allowed', 1, true], ['transport_errors', '운송 오류', 'transport errors', 1, false],
  ['factory_kwh_total', '공장 총 kWh', 'factory kWh', 0, false], ['kwh_per_good', 'kWh/양품', 'kWh/good', 3, false], ['amr_charge_kwh', 'AMR 충전 kWh', 'AMR kWh', 2, false],
  ['automation_rate', '자동 처리율', 'automation', 1, true], ['final_accuracy', '최종 판정 정확도', 'final accuracy', 1, true]]
function Results({ K, lg }: { K: any; lg: Lang }) {
  const [d, setD] = useState<any>(null)
  const [n, setN] = useState(30)
  const [bp, setBp] = useState<'mock' | 'typesafe'>('mock')
  const [pub, setPub] = useState(false)
  useEffect(() => { fetch('/api/v3/meta').then(j).then(m => setPub(!!m.public)).catch(() => {}) }, [])
  const [msg, setMsg] = useState('')
  const load = () => fetch('/api/v3/results?limit=40').then(j).then(setD).catch(e => setMsg(String(e)))
  const running = !!d?.batch?.running
  useEffect(() => { load() }, [])
  useEffect(() => { const h = setInterval(load, running ? 4000 : 30000); return () => clearInterval(h) }, [running])
  if (!d) return <Card><div className="row caption" style={{ gap: 8, alignItems: 'center' }}>{Icon.sigma}{msg ? <span style={{ color: 'var(--color-error)' }}>{msg}</span> : K('누적 결과를 불러오는 중…', 'Loading stored results…')}</div></Card>
  const groups = d.stats.by_group
  const real = groups.filter((g: any) => g.decision_provider === 'TYPESAFE_JEV').reduce((a: number, g: any) => a + g.n_runs, 0)
  const all = groups.reduce((a: number, g: any) => a + g.n_runs, 0)
  const met = groups.reduce((a: number, g: any) => a + (g.target_met.mean ?? 0) * g.target_met.n, 0) / Math.max(1, groups.reduce((a: number, g: any) => a + g.target_met.n, 0))
  const prov = (p: string, u: string) => <span className="row" style={{ gap: 4 }}>{p === 'NONE' ? <Badge>{K('규칙', 'rules')}</Badge> : <Badge tone={p === 'TYPESAFE_JEV' ? 'success' : 'warning'}>{T.provider(p, lg)}</Badge>}{u && u !== 'off' && <Badge tone={u === 'typesafe' ? 'success' : 'neutral'}>{K('제품', 'part')} {u === 'typesafe' ? K('실제', 'real') : K('모의', 'mock')}</Badge>}</span>
  const cell = (m: any, d0: number, p: boolean) => m?.mean == null ? '—' : <span title={m.ci_low != null ? `95% CI ${p ? pct(m.ci_low, d0) : num(m.ci_low, d0)} ~ ${p ? pct(m.ci_high, d0) : num(m.ci_high, d0)} · n=${m.n}` : `n=${m.n}`}>{p ? pct(m.mean, d0) : num(m.mean, d0)}{m.ci_low != null && <span className="caption"> ±{p ? pct((m.ci_high - m.ci_low) / 2, d0) : num((m.ci_high - m.ci_low) / 2, d0)}</span>}</span>
  const b = d.batch
  return <div className="flex flex-col gap-4">
    <div className="kpis">
      <Kpi icon={Icon.db} label={K('누적 실행', 'Runs stored')} value={d.n_runs} tip={`${d.files.db}`} />
      <Kpi icon={Icon.brain} label={K('실제 Jev 실행', 'Real-Jev runs')} value={real} tone="good" tip={K('실제 TypeSafe Jev를 호출한 실행. 모의 실행과 통계가 섞이지 않게 따로 묶고, 중간에 대체 판단(예산 초과·오류)이 생긴 실행은 통계에서 뺍니다.', 'real TypeSafe Jev runs; grouped apart from mock; runs with any fallback are excluded')} />
      <Kpi icon={Icon.target} label={K('전체 목표 달성률', 'Target met (all)')} value={pct(met)} />
      <Kpi icon={Icon.sigma} label={K('비교 그룹', 'Groups')} value={groups.length} sub={`${all} ${K('회', 'runs')}`} />
    </div>
    <Card icon={Icon.download} title={K('파일 · 일괄 실행', 'Files & batch')} subtitle={K('교대가 끝날 때마다 결과가 SQLite DB에 한 줄씩 쌓이고, CSV는 DB 전체로 다시 만들어집니다. 일괄 실행은 같은 운영 번호로 운영 방식들을 짝지어 돌려(모의 Jev, API 비용 없음) 짝비교 통계를 만듭니다.', 'Each finished shift appends a row to the SQLite DB; CSVs are regenerated from the whole DB. Batch runs pair the modes on the same course numbers (mock Jev, no API cost).')}>
      <div className="toolbar">
        {['runs.csv', 'stats_by_group.csv', 'paired.csv', 'run_events.csv'].map(f => <a key={f} className="btn btn-secondary icon-btn" href={`/api/v3/results/csv/${f}`} download>{Icon.download}<span>{({ 'runs.csv': K('실행별', 'runs'), 'stats_by_group.csv': K('그룹 통계', 'group stats'), 'paired.csv': K('짝비교', 'paired'), 'run_events.csv': K('사건', 'events') } as any)[f]}</span></a>)}
        <span className="grow" />
        {pub ? <span className="pill" title={K('공개 데모에서는 연구 데이터를 읽기만 합니다. 일괄 실행과 실제 Jev 호출은 연구자 로컬 환경에서만 합니다.', 'read-only on the public demo; batches and real Jev run only on the researcher’s machine')}>{Icon.shield}{K('공개 데모 · 읽기 전용', 'public demo · read-only')}</span> : <>
        <div className="seg" title={K('라인 판단에 쓸 Jev', 'Jev used for line decisions')}><button className={bp === 'mock' ? 'is-active' : ''} onClick={() => setBp('mock')}>{K('모의', 'mock')}</button><button className={bp === 'typesafe' ? 'is-active' : ''} onClick={() => setBp('typesafe')}>{K('실제 Jev', 'real Jev')}</button></div>
        <div className="seg" title={K('운영 번호(무작위 상황) 수 — 각 번호마다 6개 운영 방식 × 보통·높음 목표', 'number of random courses; each runs 6 modes × 2 loads')}>{[10, 30, 100].map(x => <button key={x} className={n === x ? 'is-active' : ''} onClick={() => setN(x)}>{x}</button>)}</div>
        <IconBtn variant="primary" icon={Icon.play} label={b.running ? `${b.done}/${b.total}${b.cost_usd ? ` · $${b.cost_usd}` : ''}` : K(`일괄 실행 ${n * 12}회`, `Batch ${n * 12} runs`)} title={K('같은 운영 번호로 6개 운영 방식을 짝지어 실행합니다', 'paired: 6 modes on identical courses')} disabled={b.running} onClick={() => { setMsg(''); post('/api/v3/results/batch', { n, provider: bp }).then(load).catch(e => setMsg(String(e))) }} /></>}
      </div>
      {b.running && <div className="progress" style={{ marginTop: 10 }}><i style={{ width: `${100 * b.done / Math.max(1, b.total)}%` }} /></div>}
      {(msg || b.error) && <div className="caption" style={{ color: 'var(--color-error)', marginTop: 6 }}>{msg || b.error}</div>}
    </Card>
    <Card icon={Icon.sigma} title={K('그룹별 통계 (평균 ± 95% 신뢰구간)', 'By group (mean ± 95% CI)')} subtitle={K('운영 방식 × 판단 주체(규칙 / 모의 Jev / 실제 Jev) × 제품 판정 × 목표 수준으로 묶습니다. 값에 마우스를 올리면 구간과 표본 수가 보입니다.', 'Grouped by mode × decision provider × part judge × target level. Hover a value for the interval and n.')}>
      {groups.length ? <div style={{ overflowX: 'auto' }}><table className="table nowrap"><thead><tr><th>{K('운영 방식', 'mode')}</th><th>{K('판단', 'by')}</th><th>{K('목표', 'load')}</th><th>n</th>{R_METRICS.map(m => <th key={m[0]}>{K(m[1], m[2])}</th>)}</tr></thead>
        <tbody>{groups.map((g: any, i: number) => <tr key={i}><td><b>{T.arm(g.arm, lg).split('(')[0]}</b></td><td>{prov(g.decision_provider, g.unit_judge)}</td><td>{({ low: K('낮음', 'low'), medium: K('보통', 'med'), high: K('높음', 'high') } as any)[g.load] ?? g.load}</td><td>{g.n_runs}</td>{R_METRICS.map(m => <td key={m[0]} className="mono">{cell(g[m[0]], m[3], m[4])}</td>)}</tr>)}</tbody></table></div>
        : <div className="row caption" style={{ gap: 8 }}>{Icon.db}{K('아직 저장된 실행이 없습니다. 교대를 끝까지 돌리거나 일괄 실행을 누르세요.', 'No runs yet. Finish a shift or start a batch.')}</div>}
    </Card>
    {d.stats.energy.length > 0 && <Card icon={Icon.power} title={K('전력 사용 구성 (실행당 평균)', 'Energy per run (mean)')}>
      <div className="flex flex-col gap-3">{d.stats.energy.map((e: any) => <div key={e.arm + e.decision_provider} className="row" style={{ gap: 12, alignItems: 'center' }}>
        <div style={{ width: 200, fontSize: 12 }}><b>{T.arm(e.arm, lg).split('(')[0]}</b> {prov(e.decision_provider, '')}<div className="caption">n={e.n_runs} · {num(e.factory_kwh_total, 0)} kWh · {num(e.kwh_per_good, 3)} kWh/{K('개', 'part')}</div></div>
        <div style={{ flex: 1 }}><EnergyBar e={e} K={K} /></div></div>)}</div>
    </Card>}
    {d.stats.paired.length > 0 && <Card icon={Icon.bench} title={K('가설 검정 (같은 운영 번호 짝비교)', 'Hypothesis tests (paired on identical courses)')} subtitle={K('같은 무작위 상황에서 두 운영 방식을 비교합니다. 차이 = 앞 − 뒤. 신뢰구간은 운영 번호 단위 부트스트랩, 목표 달성은 McNemar 정확 검정, 나머지는 부트스트랩 p. 같은 지표 안에서 Holm 보정. "필요 짝"은 관측된 효과를 80% 검정력으로 잡는 데 필요한 운영 번호 수입니다.', 'Difference = first − second; seed-cluster bootstrap CI; McNemar exact / bootstrap p; Holm within a metric; pairs needed for 80% power at the observed effect.')}>
      <div style={{ overflowX: 'auto' }}><table className="table"><thead><tr><th>{K('가설', 'hypothesis')}</th><th>{K('지표', 'metric')}</th><th>{K('짝', 'pairs')}</th><th>{K('앞', 'first')}</th><th>{K('뒤', 'second')}</th><th>{K('차이', 'diff')}</th><th>95% CI</th><th>p (Holm)</th><th>{K('필요 짝', 'needed')}</th></tr></thead>
        <tbody>{d.stats.paired.map((p: any, i: number) => <tr key={i}><td style={{ fontSize: 12 }}><b>{T.arm(p.a, lg).split('(')[0]}</b> − {T.arm(p.b, lg).split('(')[0]} <span className="row" style={{ display: 'inline-flex' }}>{prov(p.decision_provider, '')}</span><div className="caption">{T.contrast(`${p.a}-${p.b}`, lg)}</div></td><td>{T.metric(p.metric, lg)}</td><td>{p.n_pairs}</td><td className="mono">{num(p.mean_a, 2)}</td><td className="mono">{num(p.mean_b, 2)}</td><td className="mono"><b>{num(p.mean_diff, 2)}</b></td>
          <td className="mono">{num(p.ci_low, 2)} ~ {num(p.ci_high, 2)}</td><td>{p.p_holm != null ? (p.p_holm < 0.05 ? <Badge tone="success">{num(p.p_holm, 3)}</Badge> : <span className="mono caption">{num(p.p_holm, 3)}</span>) : '—'}</td>
          <td>{p.pairs_needed_80pct == null ? <span className="caption" title={K('관측된 차이가 0이라 계산하지 않음', 'no observed effect')}>—</span> : p.enough ? <span className="row" style={{ gap: 4, color: '#15803d', display: 'inline-flex' }}>{Icon.check}{p.pairs_needed_80pct}</span> : <span className="mono" style={{ color: '#b45309' }}>{p.pairs_needed_80pct}</span>}</td></tr>)}</tbody></table></div>
    </Card>}
    {d.stats.events?.length > 0 && <Card icon={Icon.alert} title={K('사건 유형별 대응', 'Response by event type')} subtitle={K('무작위 사건마다 감지 여부, 감지까지 걸린 시간, 첫 자동 판단까지 걸린 시간을 저장합니다(평가용).', 'per random event: detected?, time to detection, time to first automatic decision (evaluation only)')}>
      <div style={{ overflowX: 'auto' }}><table className="table nowrap"><thead><tr><th>{K('사건', 'event')}</th><th>{K('운영 방식', 'mode')}</th><th>n</th><th>{K('감지율', 'detected')}</th><th>{K('감지 시간', 'to detect')}</th><th>{K('첫 판단', 'to first decision')}</th></tr></thead>
        <tbody>{d.stats.events.filter((e: any) => e.decision_provider !== 'MOCK_JEV' || true).map((e: any, i: number) => <tr key={i}><td>{(EVENT_NAME[e.kind] ?? [e.kind, e.kind])[lg === 'ko' ? 0 : 1]}</td><td>{T.arm(e.arm, lg).split('(')[0]} {prov(e.decision_provider, '')}</td><td>{e.n}</td><td className="mono">{pct(e.detected_rate)}</td><td className="mono">{e.detect_delay_s != null ? fmtDur(e.detect_delay_s, lg) : '—'}</td><td className="mono">{e.first_action_delay_s != null ? fmtDur(e.first_action_delay_s, lg) : '—'}</td></tr>)}</tbody></table></div>
    </Card>}
    {d.runs.length > 0 && <Card icon={Icon.list} title={K('최근 실행', 'Recent runs')}>
      <div style={{ overflowX: 'auto', maxHeight: 420 }}><table className="table"><thead><tr><th>{K('종료', 'finished')}</th><th>#</th><th>{K('운영 방식', 'mode')}</th><th>{K('판단', 'by')}</th><th>{K('사건', 'events')}</th><th>{K('양품/목표', 'good/target')}</th><th>kWh</th><th>{K('출처', 'source')}</th></tr></thead>
        <tbody>{d.runs.map((r: any) => <tr key={r.run_uid}><td className="mono" style={{ fontSize: 11 }}>{r.finished_at?.slice(5, 16)}</td><td className="mono">{r.course_seed}</td><td>{T.arm(r.arm, lg).split('(')[0]}</td><td>{prov(r.decision_provider, r.unit_judge)}</td>
          <td style={{ fontSize: 11 }} title={r.event_kinds}>{(r.event_kinds || '').split('+').filter(Boolean).map((k: string) => (EVENT_NAME[k] ?? [k, k])[lg === 'ko' ? 0 : 1]).join(', ')}</td>
          <td>{r.target_met ? <span style={{ color: '#15803d' }}>{Icon.check}</span> : <span style={{ color: '#b91c1c' }}>✕</span>} <span className="mono">{num(r.good_end)}/{num(r.target)}</span></td><td className="mono">{num(r.factory_kwh_total, 0)}</td><td><span className="caption">{r.source === 'batch' ? K('일괄', 'batch') : r.source === 'live' ? K('화면 실행', 'live') : r.source}</span></td></tr>)}</tbody></table></div>
    </Card>}
  </div>
}
