import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nProvider, useI18n } from '../i18n'
import V3 from '../pages/V3'
import { reason } from '../v3labels'

function Probe() { const { t, lang, setLang } = useI18n(); return <div><span data-testid="banner">{t.banner}</span><span data-testid="lang">{lang}</span><button onClick={() => setLang('en')}>en</button></div> }

describe('i18n + operations center', () => {
  it('renders the operations center offline in Korean', async () => {
    ;(globalThis as any).fetch = () => Promise.reject(new Error('offline'))
    render(<I18nProvider><V3 /></I18nProvider>)
    expect(await screen.findByText('새 랜덤 운영')).toBeTruthy()
    expect(screen.getByText('작동 검증')).toBeTruthy()
    expect(screen.getByText('누적 결과')).toBeTruthy()
    expect(screen.queryByText('시나리오', { exact: false })).toBeNull()
  })
  it('translates backend reasons into plain Korean', () => {
    expect(reason('power 51 kW > limit 40 kW', 'ko')).toBe('전력 51kW가 한도 40kW를 넘음')
    expect(reason('main line is not producing', 'ko')).toBe('메인 라인이 생산 중이 아님')
  })
  it('switches language live and persists it', () => {
    render(<I18nProvider><Probe /></I18nProvider>)
    expect(screen.getByTestId('banner').textContent).toMatch(/시뮬레이션 환경/)
    fireEvent.click(screen.getByText('en'))
    expect(screen.getByTestId('lang').textContent).toBe('en')
  })
})
