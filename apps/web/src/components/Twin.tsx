import { lazy, Suspense, useEffect, useState } from 'react'
import { type Lang } from '../v3labels'
import FloorV3 from './FloorV3'

const Twin3D = lazy(() => import('./Twin3D'))

/** Digital twin with a 2D / 3D tab. Both views share one data poll and the same layout (floorLayout.ts). */
export default function Twin({ sid, lg, initial = '2d' }: { sid: string; lg: Lang; initial?: '2d' | '3d' }) {
  const [mode, setMode] = useState<'2d' | '3d'>(initial)
  const [d, setD] = useState<any>(null)
  useEffect(() => { const t = () => fetch(`/api/v3/sessions/${sid}/floor`).then(r => r.json()).then(setD).catch(() => {}); t(); const h = setInterval(t, 1000); return () => clearInterval(h) }, [sid])
  const K = (ko: string, en: string) => (lg === 'ko' ? ko : en)
  return <div>
    <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
      <div className="seg">{(['2d', '3d'] as const).map(m => <button key={m} className={mode === m ? 'is-active' : ''} onClick={() => setMode(m)}>{m === '2d' ? K('2D 평면', '2D plan') : K('3D 입체', '3D')}</button>)}</div>
      <div className="legend"><span><i style={{ background: '#f97316' }} />{K('전력 흐름', 'power')}</span><span><i style={{ background: '#38bdf8' }} />{K('라인 AMR', 'line AMR')}</span><span><i style={{ background: '#a78bfa' }} />{K('예비 AMR', 'reserve AMR')}</span><span><i style={{ background: '#facc15' }} />{K('충전 중', 'charging')}</span><span><i style={{ background: '#a16207' }} />{K('팔레트', 'pallet')}</span></div>
    </div>
    {mode === '2d' ? <FloorV3 d={d} lg={lg} /> : <Suspense fallback={<div className="caption">{K('3D 불러오는 중…', 'loading 3D…')}</div>}><Twin3D d={d} lg={lg} /></Suspense>}
  </div>
}
