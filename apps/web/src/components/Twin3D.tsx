import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { L as T, type Lang } from '../v3labels'
import { amrColor, amrLabel } from './FloorV3'
import { DOCK, H, INS, LINE_X0, LINE_X1, LINE_Y, MACHINES, MACHINE_W, OUT_X, RESERVE, SB, STATE_COLOR, W, cablePoints, carrierPos, routePoints } from './floorLayout'

/** 3D twin with exactly the 2D composition: plan (x, y) → floor (X, Z) = (x − W/2, y − H/2) × S.
 *  Default camera looks straight down like the 2D plan; drag to orbit, wheel to zoom. */
const S = 0.1
const X = (x: number) => (x - W / 2) * S
const Z = (y: number) => (y - H / 2) * S

function label(text: string, color = '#0f172a', bg = 'rgba(255,255,255,0.92)', size = 28): THREE.Sprite {
  const c = document.createElement('canvas'); const g = c.getContext('2d')!
  g.font = `600 ${size}px Pretendard, sans-serif`
  const w = Math.ceil(g.measureText(text).width) + 20; c.width = w; c.height = size + 16
  g.font = `600 ${size}px Pretendard, sans-serif`; g.fillStyle = bg; g.fillRect(0, 0, w, c.height); g.fillStyle = color; g.textBaseline = 'middle'; g.fillText(text, 10, c.height / 2)
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }))
  sp.scale.set(w / 45, c.height / 45, 1); sp.renderOrder = 10
  return sp
}
const box = (w: number, h: number, d: number, color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...opts }))

function makeAmr(color: string) {
  const g = new THREE.Group()
  const body = box(2.6, 0.9, 1.8, color); body.position.y = 0.75; g.add(body)
  const top = box(1.2, 0.15, 1.0, '#e2e8f0'); top.position.set(-0.3, 1.27, 0); g.add(top)
  const lidar = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.3, 16), new THREE.MeshStandardMaterial({ color: '#fde047' })); lidar.position.set(0.9, 1.35, 0); g.add(lidar)
  for (const [x, z] of [[-0.85, 0.95], [0.85, 0.95], [-0.85, -0.95], [0.85, -0.95]]) {
    const wh = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.2, 16), new THREE.MeshStandardMaterial({ color: '#0f172a' })); wh.rotation.x = Math.PI / 2; wh.position.set(x, 0.32, z); g.add(wh)
  }
  const pallet = box(2.2, 0.7, 1.6, '#a16207'); pallet.position.y = 1.65; pallet.visible = false; pallet.name = 'pallet'; g.add(pallet)
  return g
}

export default function Twin3D({ d, lg, height = 460 }: { d: any; lg: Lang; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const data = useRef<any>(d)
  const api = useRef<{ view: (v: 'top' | 'tilt') => void } | null>(null)
  data.current = d
  useEffect(() => {
    const el = ref.current!; const width = el.clientWidth || 900
    const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(2, window.devicePixelRatio)); renderer.setSize(width, height); el.appendChild(renderer.domElement)
    const scene = new THREE.Scene(); scene.background = new THREE.Color('#eef2f7')
    const camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 500)
    const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, 0); controls.enableDamping = true
    const fit = () => Math.max(H * S / (2 * Math.tan((camera.fov / 2) * Math.PI / 180)), W * S / (2 * Math.tan((camera.fov / 2) * Math.PI / 180) * camera.aspect)) * 1.08
    const views = { top: () => { camera.position.set(0, fit(), 0.01); controls.target.set(0, 0, 0) }, tilt: () => { const h = fit(); camera.position.set(0, h * 0.72, h * 0.72); controls.target.set(0, 0, 2) } }
    views.top(); controls.update()
    api.current = { view: (v) => { views[v](); controls.update() } }
    scene.add(new THREE.HemisphereLight('#ffffff', '#94a3b8', 1.6)); const sun = new THREE.DirectionalLight('#ffffff', 1.4); sun.position.set(-30, 60, 20); scene.add(sun)
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W * S, H * S), new THREE.MeshStandardMaterial({ color: '#f8fafc' })); floor.rotation.x = -Math.PI / 2; scene.add(floor)
    // switchboard
    const sb = box(SB.w * S, 6, SB.h * S, '#1e293b'); sb.position.set(X(SB.x + SB.w / 2), 3, Z(SB.y + SB.h / 2)); scene.add(sb)
    const sbLabel = label(lg === 'ko' ? '배전반' : 'Switchboard', '#fff', 'rgba(15,23,42,0.9)'); sbLabel.position.set(X(SB.x + SB.w / 2), 7.5, Z(SB.y + SB.h / 2)); scene.add(sbLabel)
    // lines, machines, outlets
    const machines: Record<string, THREE.Mesh[]> = {}; const stateLabels: Record<string, THREE.Sprite> = {}; const pallets: Record<string, THREE.Mesh[]> = {}
    for (const role of ['main', 'sub']) {
      const y = LINE_Y[role]
      const conv = box((LINE_X1 - LINE_X0) * S, 0.8, 2.8, '#cbd5e1'); conv.position.set(X((LINE_X0 + LINE_X1) / 2), 0.4, Z(y)); scene.add(conv)
      machines[role] = MACHINES.map(([x, kind]) => { const h = kind === 'press' ? 5 : kind === 'camera' ? 3.5 : 2; const m = box(MACHINE_W * S, h, 4.8, '#ffffff'); m.position.set(X(x + MACHINE_W / 2), h / 2, Z(y)); scene.add(m); return m })
      MACHINES.forEach(([x, , ko]) => { const l = label(ko, '#0f172a', 'rgba(255,255,255,0.85)', 22); l.position.set(X(x + MACHINE_W / 2), 6.2, Z(y)); scene.add(l) })
      const name = label(role === 'main' ? (lg === 'ko' ? '메인 라인' : 'Main line') : (lg === 'ko' ? '예비 라인' : 'Backup line')); name.position.set(X(LINE_X0) + 2, 1.2, Z(y - 34)); scene.add(name)
      const out = box(4, 0.2, 3.6, '#fed7aa'); out.position.set(X(OUT_X + 20), 0.1, Z(y)); scene.add(out)
      pallets[role] = [0, 1, 2].map(i => { const p = box(2.8, 0.8, 2.8, '#a16207'); p.position.set(X(OUT_X + 20), 0.6 + i * 0.9, Z(y)); p.visible = false; scene.add(p); return p })
    }
    // AMR routes, inspection, dock, reserve area
    for (const role of ['main', 'sub']) {
      const pts = routePoints(role)
      for (let i = 0; i < pts.length - 1; i++) {
        const [a, b] = [pts[i], pts[i + 1]]; const len = Math.hypot(b[0] - a[0], b[1] - a[1]) * S
        const strip = new THREE.Mesh(new THREE.PlaneGeometry(a[1] === b[1] ? len : 1, a[1] === b[1] ? 1 : len), new THREE.MeshStandardMaterial({ color: '#93c5fd', transparent: true, opacity: 0.55 }))
        strip.rotation.x = -Math.PI / 2; strip.position.set(X((a[0] + b[0]) / 2), 0.02, Z((a[1] + b[1]) / 2)); scene.add(strip)
      }
    }
    const ins = box(INS.w * S, 4, INS.h * S, '#bfdbfe', { transparent: true, opacity: 0.75 }); ins.position.set(X(INS.x + INS.w / 2), 2, Z(INS.y + INS.h / 2)); scene.add(ins)
    const insL = label(lg === 'ko' ? '검사장·창고' : 'Inspection', '#1e3a8a'); insL.position.set(X(INS.x + INS.w / 2), 5.5, Z(INS.y + INS.h / 2)); scene.add(insL)
    const dock = box(14, 0.1, 3.4, '#e2e8f0'); dock.position.set(X(DOCK.x + 60), 0.05, Z(DOCK.y - 5)); scene.add(dock)
    const res = box(9, 0.1, 3.4, '#ede9fe'); res.position.set(X(RESERVE.x + 15), 0.05, Z(RESERVE.y - 5)); scene.add(res)
    const resL = label(lg === 'ko' ? '다른 구역(예비 AMR)' : 'other area (reserve AMR)', '#6d28d9', 'rgba(245,243,255,0.9)', 20); resL.position.set(X(RESERVE.x + 15), 1.5, Z(RESERVE.y - 26)); scene.add(resL)
    // power cables + moving bars
    const cables: Record<string, { curve: THREE.CurvePath<THREE.Vector3>; bars: THREE.Mesh[]; tube: THREE.Mesh }> = {}
    for (const role of ['main', 'sub']) {
      const pts = cablePoints(role).map(([x, y]) => new THREE.Vector3(X(x), 0.35, Z(y)))
      const path = new THREE.CurvePath<THREE.Vector3>(); for (let i = 0; i < pts.length - 1; i++) path.add(new THREE.LineCurve3(pts[i], pts[i + 1]))
      const tube = new THREE.Mesh(new THREE.TubeGeometry(path as any, 64, 0.18, 8, false), new THREE.MeshStandardMaterial({ color: '#cbd5e1' })); scene.add(tube)
      const bars = Array.from({ length: 9 }).map(() => { const b = box(1.1, 0.35, 0.35, '#f97316', { emissive: '#7c2d12' }); scene.add(b); return b })
      cables[role] = { curve: path, bars, tube }
    }
    // AMRs
    const amrs: Record<string, { g: THREE.Group; label: THREE.Sprite; pos: THREE.Vector3 }> = {}
    let raf = 0; let t0 = performance.now()
    const tick = () => {
      const dd = data.current; const t = (performance.now() - t0) / 1000
      if (dd?.lines) {
        for (const role of ['main', 'sub']) {
          const l = dd.lines[role]; const col = new THREE.Color(STATE_COLOR[l.state] ?? '#64748b')
          machines[role].forEach(m => (m.material as THREE.MeshStandardMaterial).color.copy(new THREE.Color('#ffffff').lerp(col, 0.55)))
          if (!stateLabels[role] || stateLabels[role].userData.text !== l.state) {
            if (stateLabels[role]) scene.remove(stateLabels[role])
            const s = label(T.state(l.state, lg), '#fff', STATE_COLOR[l.state] ?? '#64748b', 24); s.userData.text = l.state; s.position.set(X(LINE_X0 + 165), 8, Z(LINE_Y[role])); scene.add(s); stateLabels[role] = s
          }
          pallets[role].forEach((p, i) => (p.visible = i < Math.min(3, l.outlet)))
          const on = l.kw > l.run_kw * 0.5; const trip = l.state === 'TRIPPED'
          const c = cables[role]; const thick = 0.6 + 1.4 * Math.min(1, l.kw / 30)
          c.bars.forEach((b, i) => { b.visible = on || trip; const u = ((t * 0.35 + i / c.bars.length) % 1); const p = c.curve.getPointAt(u); const tan = c.curve.getTangentAt(u)
            b.position.copy(p); b.scale.set(1, thick, thick); b.rotation.y = Math.atan2(-tan.z, tan.x); (b.material as THREE.MeshStandardMaterial).color.set(trip ? '#dc2626' : '#f97316') })
        }
        for (const c of dd.carriers) {
          let a = amrs[c.id]
          if (!a) { const g = makeAmr(amrColor(c)); scene.add(g); const lb = label(amrLabel(c, lg), '#334155', 'rgba(255,255,255,0.85)', 18); scene.add(lb); a = amrs[c.id] = { g, label: lb, pos: new THREE.Vector3() } }
          const [x, y] = carrierPos(c, dd.carriers); const target = new THREE.Vector3(X(x), 0, Z(y))
          if (a.pos.lengthSq() === 0) a.pos.copy(target)
          const dir = target.clone().sub(a.pos); if (dir.length() > 0.05) a.g.rotation.y = Math.atan2(-dir.z, dir.x)
          a.pos.lerp(target, 0.06); a.g.position.copy(a.pos); a.label.position.set(a.pos.x, 3.2, a.pos.z)
          ;((a.g.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial).color.set(amrColor(c))
          const pal = a.g.getObjectByName('pallet'); if (pal) pal.visible = !!c.pos?.loaded
        }
      }
      controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(tick)
    }
    tick()
    const onResize = () => { const w2 = el.clientWidth || width; renderer.setSize(w2, height); camera.aspect = w2 / height; camera.updateProjectionMatrix() }
    window.addEventListener('resize', onResize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize); controls.dispose(); renderer.dispose(); el.removeChild(renderer.domElement); t0 = 0 }
  }, [lg, height])
  return <div>
    <div className="row" style={{ gap: 6, marginBottom: 6 }}>
      <button className="btn btn-secondary btn-sm" onClick={() => api.current?.view('top')}>{lg === 'ko' ? '2D와 같은 시점(위에서)' : 'Top view (same as 2D)'}</button>
      <button className="btn btn-secondary btn-sm" onClick={() => api.current?.view('tilt')}>{lg === 'ko' ? '비스듬히 보기' : 'Angled view'}</button>
      <span className="caption">{lg === 'ko' ? '마우스로 끌어 회전, 휠로 확대·축소' : 'drag to orbit, wheel to zoom'}</span>
    </div>
    <div ref={ref} style={{ width: '100%', height, borderRadius: 8, overflow: 'hidden', border: '1px solid var(--color-divider)' }} />
  </div>
}
