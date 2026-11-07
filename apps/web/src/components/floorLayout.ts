/** Shared floor layout for the 2D plan and the 3D twin (same composition in both views).
 *  Units: plan pixels; x → right, y → down. The 3D view maps (x, y) to (X, Z) on the floor plane. */
export const W = 940, H = 340
export const SB = { x: 20, y: 110, w: 110, h: 110 }            // switchboard
export const LINE_Y: Record<string, number> = { main: 70, sub: 230 }
export const LINE_X0 = 200, LINE_X1 = 600                        // conveyor span
export const MACHINES: [number, string, string][] = [[LINE_X0 + 20, 'press', '프레스'], [LINE_X0 + 170, 'conveyor', '컨베이어'], [LINE_X0 + 320, 'camera', '카메라(YOLO)']]
export const MACHINE_W = 70
export const OUT_X = 640                                         // outlet (pallets)
export const INS = { x: 820, y: 105, w: 100, h: 120 }            // inspection / warehouse
export const DOCK = { x: 690, y: 300 }                           // AMR parking / charging
export const RESERVE = { x: 860, y: 300 }                        // reserve AMR (another area)
export const STATE_COLOR: Record<string, string> = { RUNNING: '#16a34a', STARTING: '#0ea5e9', BLOCKED: '#d97706', FAULT: '#dc2626', MAINT: '#d97706', REPAIR: '#d97706', READY: '#0ea5e9', IDLE: '#64748b', OFF: '#94a3b8', TRIPPED: '#dc2626' }

export const routePoints = (line: string): [number, number][] => {
  const ly = LINE_Y[line] ?? 150
  const iy = INS.y + (line === 'main' ? 30 : 90)
  return [[OUT_X + 50, ly], [760, ly], [760, iy], [INS.x - 6, iy]]
}
export function routePoint(line: string, phase: string, prog: number): [number, number] {
  const pts = routePoints(line)
  const path = phase === 'to_outlet' ? [...pts].reverse() : pts
  const seg = path.slice(1).map((p, i) => Math.hypot(p[0] - path[i][0], p[1] - path[i][1]))
  let d = seg.reduce((a, b) => a + b, 0) * Math.max(0, Math.min(1, prog))
  for (let i = 0; i < seg.length; i++) {
    if (d <= seg[i]) { const f = seg[i] ? d / seg[i] : 0; return [path[i][0] + (path[i + 1][0] - path[i][0]) * f, path[i][1] + (path[i + 1][1] - path[i][1]) * f] }
    d -= seg[i]
  }
  return path[path.length - 1]
}
/** Power cable from the switchboard to a line (polyline in plan coordinates). */
export const cablePoints = (line: string): [number, number][] => {
  const y = LINE_Y[line]; const yo = SB.y + (line === 'main' ? 30 : 80)
  return [[SB.x + SB.w, yo], [165, yo], [165, y], [LINE_X0, y]]
}
/** Park position of an idle carrier. */
export function parkPoint(c: any, idleLine: any[], idleReserve: any[]): [number, number] {
  if (c.type === 'AMR_RESERVE') { const i = idleReserve.indexOf(c); return [RESERVE.x + i * 50, RESERVE.y - 6] }
  const i = idleLine.indexOf(c); return [DOCK.x + 25 + i * 60, DOCK.y - 6]
}
export function carrierPos(c: any, carriers: any[]): [number, number] {
  if (c.pos) return routePoint(c.pos.line, c.pos.phase, c.pos.progress)
  const idleLine = carriers.filter(x => !x.pos && x.type === 'AMR'), idleReserve = carriers.filter(x => !x.pos && x.type === 'AMR_RESERVE')
  return parkPoint(c, idleLine, idleReserve)
}
