import { useEffect, useState } from 'react'
import { api } from './api'
import type { Health } from './types'
import { useI18n, type Lang } from './i18n'
import { Badge, Icon } from './components/ui'
import logo from './ds/assets/logo-sf.svg'
import V3 from './pages/V3'

/** One application: the AI operations center (SF AI OS v3). Earlier v2 screens were removed as duplicates. */
export default function App() {
  const { t, lang, setLang } = useI18n()
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => { api.health().then(setHealth).catch(() => setHealth(null)) }, [])
  return (
    <div className="app">
      <header className="topbar" style={{ height: 56 }}>
        <div className="topbar-brand"><img src={logo} className="topbar-logo-mark" alt="" /><span className="wordmark">SF-AIC<small>{lang === 'ko' ? 'AI 운영 센터' : 'AI Operations Center'}</small></span></div>
        <div className="topbar-spacer" />
        <div className="topbar-actions" style={{ gap: 12 }}>
          <span className="pill" title={t.simOnly}>{Icon.shield}{lang === 'ko' ? '가상 설비 전용' : 'Virtual only'}</span>
          {health?.jev_via_relay ? <span className="pill" title={lang === 'ko' ? '실제 TypeSafe Jev는 연구자 Mac의 중계기를 거쳐 호출됩니다. API 키는 Mac에만 있고 하루 사용 한도가 있습니다. Mac이 꺼져 있으면 모의 Jev만 쓸 수 있습니다.' : 'Real Jev is called through the researcher’s Mac relay (key stays there, daily cap). Offline when the Mac is off.'}>
            <span style={{ width: 8, height: 8, borderRadius: 4, background: health.jev_key_present ? '#22c55e' : '#94a3b8' }} />{lang === 'ko' ? (health.jev_key_present ? '실제 Jev 연결됨' : '실제 Jev 꺼짐') : (health.jev_key_present ? 'Real Jev online' : 'Real Jev offline')}</span>
          : health ? <label className="toggle" style={{ fontSize: 12 }} title={health.jev_key_present ? (lang === 'ko' ? 'API 키는 .env에만 있습니다' : 'API key is in .env only') : (lang === 'ko' ? 'API 키 없음' : 'no API key')}>
            <input type="checkbox" role="switch" checked={!!health.jev_enabled} disabled={!health.jev_key_present} onChange={e => api.setJev(e.target.checked).then(() => api.health().then(setHealth))} />
            <span>{lang === 'ko' ? '실제 Jev' : 'Real Jev'}</span><span style={{ width: 8, height: 8, borderRadius: 4, background: health.jev_key_present ? '#22c55e' : '#94a3b8' }} /></label>
            : <Badge tone="error">{t.backendOffline}</Badge>}
          <div className="seg" aria-label={t.lang}>{(['ko', 'en'] as Lang[]).map(l => <button key={l} className={lang === l ? 'is-active' : ''} onClick={() => setLang(l)}>{l === 'ko' ? '한국어' : 'EN'}</button>)}</div>
        </div>
      </header>
      <main className="app-main"><div className="app-content"><V3 /></div></main>
    </div>
  )
}
