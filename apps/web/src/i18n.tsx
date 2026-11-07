import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

/** App-level strings only; every operations-center label lives in v3labels.ts. */
export type Lang = 'ko' | 'en'
const DICT = {
  ko: { banner: '시뮬레이션 환경입니다. 가상 PLC만 제어하며 실제 설비나 비상정지 기능과 무관합니다.', simOnly: '시뮬레이션 전용 · 가상 PLC', backendOffline: '백엔드 꺼짐 — make api 로 실행', lang: '언어' },
  en: { banner: 'SIMULATED ENVIRONMENT — virtual PLC only; not connected to real equipment or emergency stops.', simOnly: 'SIMULATION ONLY · virtual PLC', backendOffline: 'backend offline — run make api', lang: 'Language' },
} as const

type Dict = { [K in keyof typeof DICT.ko]: string }
const Ctx = createContext<{ lang: Lang; t: Dict; setLang: (l: Lang) => void }>({ lang: 'ko', t: DICT.ko, setLang: () => {} })

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => { try { return (localStorage.getItem('sfaic2.lang') as Lang) || 'ko' } catch { return 'ko' } })
  const setLang = (l: Lang) => { setLangState(l); try { localStorage.setItem('sfaic2.lang', l) } catch { /* ignore */ } }
  useEffect(() => { document.documentElement.lang = lang }, [lang])
  return <Ctx.Provider value={{ lang, t: DICT[lang], setLang }}>{children}</Ctx.Provider>
}
export const useI18n = () => useContext(Ctx)
