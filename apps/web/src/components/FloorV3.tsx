import { L as T, type Lang } from '../v3labels'
import { DOCK, H, INS, LINE_X0, LINE_X1, LINE_Y, MACHINES, MACHINE_W, OUT_X, RESERVE, SB, STATE_COLOR, W, cablePoints, carrierPos, routePoints } from './floorLayout'

/** 2D top-down plan. Power = orange bars flowing switchboard → line (thicker = more kW, grey = off, red = trip).
 *  AMRs move along each line's route at the trip progress reported by the simulator; the reserve AMR waits in another area. */
export function Amr2D({ loaded, color, label }: { loaded: boolean; color: string; label: string }) {
  return <g>
    <rect x={-15} y={-10} width={30} height={20} rx={5} fill={color} stroke="#0f172a" strokeWidth={1} />
    <circle cx={-9} cy={11} r={3} fill="#0f172a" /><circle cx={9} cy={11} r={3} fill="#0f172a" />
    <circle cx={10} cy={-5} r={2.5} fill="#fde047" stroke="#0f172a" strokeWidth={.6} />
    <rect x={-11} y={-6} width={14} height={4} rx={1} fill="#e2e8f0" />
    {loaded && <rect x={-12} y={-20} width={24} height={9} rx={1.5} fill="#a16207" stroke="#422006" strokeWidth={.8} />}
    <text y={26} textAnchor="middle" fontSize={9} fill="#334155">{label}</text>
  </g>
}

export const amrColor = (c: any) => (!c.available && !c.pos ? '#94a3b8' : c.charging ? '#facc15' : c.type === 'AMR_RESERVE' ? '#a78bfa' : '#38bdf8')
export const amrLabel = (c: any, lg: Lang) => `${c.charging ? '⚡' : ''}${c.type === 'AMR_RESERVE' ? (lg === 'ko' ? '예비' : 'R') : 'AMR'} ${c.id.split('-')[1]} · ${Math.round(c.battery)}%`

export default function FloorV3({ d, lg }: { d: any; lg: Lang }) {
  if (!d?.lines) return null
  const K = (ko: string, en: string) => (lg === 'ko' ? ko : en)
  const f = d.feeder
  const loadFrac = Math.min(1.2, f.load_kw / f.available_kw)
  const P = (pts: [number, number][]) => pts.map(p => p.join(',')).join(' ')
  return <div style={{ overflowX: 'auto' }}>
    <style>{`@keyframes flowbar { to { stroke-dashoffset: -36; } } .pwr { animation: flowbar 1.1s linear infinite; } .car { transition: transform 1s linear; }`}</style>
    <svg width={W} height={H} role="img" aria-label="virtual factory floor (2D)">
      <rect x={0} y={0} width={W} height={H} fill="var(--color-bg-1, #f8fafc)" rx={8} />
      <rect x={SB.x} y={SB.y} width={SB.w} height={SB.h} rx={6} fill="#1e293b" />
      <text x={SB.x + 10} y={SB.y + 18} fontSize={11} fill="#fff" fontWeight={700}>{K('배전반', 'Switchboard')}</text>
      <text x={SB.x + 10} y={SB.y + 36} fontSize={10} fill="#cbd5e1">{K('부하', 'load')} {f.load_kw} kW</text>
      <text x={SB.x + 10} y={SB.y + 50} fontSize={10} fill="#cbd5e1">{K('한도', 'limit')} {f.available_kw} kW</text>
      <rect x={SB.x + 10} y={SB.y + 62} width={SB.w - 20} height={10} rx={3} fill="#334155" />
      <rect x={SB.x + 10} y={SB.y + 62} width={(SB.w - 20) * Math.min(1, loadFrac)} height={10} rx={3} fill={loadFrac > 1 ? '#dc2626' : loadFrac > 0.85 ? '#f59e0b' : '#22c55e'} />
      <text x={SB.x + 10} y={SB.y + 90} fontSize={9} fill="#94a3b8">{f.available_kw < f.nameplate_kw ? K(`한도 축소(정격 ${f.nameplate_kw} kW)`, `reduced (nameplate ${f.nameplate_kw})`) : K('정격 한도', 'nameplate limit')}</text>
      {Object.entries(d.lines).map(([role, l]: any) => {
        const on = l.kw > l.run_kw * 0.5; const trip = l.state === 'TRIPPED'
        const color = trip ? '#dc2626' : on ? '#f97316' : '#94a3b8'; const w = 2 + 6 * Math.min(1, l.kw / 30)
        const pts = P(cablePoints(role))
        return <g key={`p${role}`}><polyline points={pts} fill="none" stroke="#e2e8f0" strokeWidth={w + 4} strokeLinejoin="round" />
          <polyline className={on ? 'pwr' : ''} points={pts} fill="none" stroke={color} strokeWidth={w} strokeDasharray={on ? '18 18' : '4 6'} strokeLinejoin="round" />
          <text x={140} y={LINE_Y[role] - 10} fontSize={9} fill="#9a3412">{l.kw} kW</text></g>
      })}
      {Object.entries(d.lines).map(([role, l]: any) => {
        const y = LINE_Y[role]; const c = STATE_COLOR[l.state] ?? '#64748b'
        return <g key={role}>
          <rect x={LINE_X0} y={y - 14} width={LINE_X1 - LINE_X0} height={28} rx={4} fill="#e2e8f0" stroke="#cbd5e1" />
          {Array.from({ length: 16 }).map((_, i) => <line key={i} x1={LINE_X0 + 10 + i * 25} y1={y - 12} x2={LINE_X0 + 10 + i * 25} y2={y + 12} stroke="#cbd5e1" />)}
          {MACHINES.map(([x, , ko]) => <g key={ko}><rect x={x} y={y - 24} width={MACHINE_W} height={48} rx={6} fill="#fff" stroke={c} strokeWidth={2} /><text x={x + MACHINE_W / 2} y={y + 4} textAnchor="middle" fontSize={10} fill="#0f172a">{ko}</text></g>)}
          <text x={LINE_X0} y={y - 32} fontSize={12} fontWeight={700} fill="#0f172a">{role === 'main' ? K('메인 라인', 'Main line') : K('예비 라인', 'Backup line')}</text>
          <rect x={LINE_X0 + 100} y={y - 44} width={130} height={18} rx={9} fill={c} /><text x={LINE_X0 + 165} y={y - 31} textAnchor="middle" fontSize={10} fill="#fff" fontWeight={700}>{T.state(l.state, lg)}</text>
          <rect x={OUT_X} y={y - 18} width={40} height={36} rx={4} fill="#fff7ed" stroke="#fdba74" />
          {Array.from({ length: Math.min(3, l.outlet) }).map((_, i) => <rect key={i} x={OUT_X + 6} y={y + 10 - i * 9} width={28} height={7} rx={1} fill="#a16207" />)}
          <text x={OUT_X + 20} y={y + 30} textAnchor="middle" fontSize={9} fill="#9a3412">{K(`출구 ${l.outlet}팔레트`, `${l.outlet} pallets`)}</text>
          <text x={OUT_X - 6} y={y - 22} textAnchor="end" fontSize={9} fill="#64748b">{K(`적재 중 ${l.wip}/40`, `loading ${l.wip}/40`)}</text>
        </g>
      })}
      {['main', 'sub'].map(role => <polyline key={`r${role}`} points={P(routePoints(role))} fill="none" stroke="#93c5fd" strokeWidth={10} strokeLinejoin="round" opacity={0.35} />)}
      <rect x={INS.x} y={INS.y} width={INS.w} height={INS.h} rx={6} fill="#eff6ff" stroke="#60a5fa" />
      <text x={INS.x + 10} y={INS.y + 18} fontSize={11} fontWeight={700} fill="#1e3a8a">{K('검사장·창고', 'Inspection')}</text>
      <text x={INS.x + 10} y={INS.y + INS.h - 10} fontSize={10} fill="#1e3a8a">{K(`도착 ${d.delivered}팔레트`, `${d.delivered} pallets in`)}</text>
      <rect x={DOCK.x - 10} y={DOCK.y - 22} width={140} height={34} rx={6} fill="#f1f5f9" stroke="#cbd5e1" />
      <text x={DOCK.x - 4} y={DOCK.y - 26} fontSize={9} fill="#64748b">{K('AMR 대기·충전', 'AMR parking / charging')}</text>
      {[0, 1].map(i => <g key={`chg${i}`} transform={`translate(${DOCK.x + 25 + i * 60 + 17}, ${DOCK.y - 19})`}><rect width={12} height={10} rx={2} fill="#facc15" stroke="#a16207" strokeWidth={.6} /><path d="M7 1 L3 6 H6 L5 9 L9 4 H6 Z" fill="#422006" /></g>)}
      <rect x={RESERVE.x - 30} y={RESERVE.y - 22} width={90} height={34} rx={6} fill="#f5f3ff" stroke="#c4b5fd" strokeDasharray="4 3" />
      <text x={RESERVE.x - 26} y={RESERVE.y - 26} fontSize={9} fill="#6d28d9">{K('다른 구역(예비 AMR)', 'other area (reserve)')}</text>
      {d.carriers.map((c: any) => { const [x, y] = carrierPos(c, d.carriers); return <g key={c.id} className="car" style={{ transform: `translate(${x}px, ${y}px)` }}><Amr2D loaded={!!c.pos?.loaded} color={amrColor(c)} label={amrLabel(c, lg)} /></g> })}
    </svg>
  </div>
}
