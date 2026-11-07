import type { ReactNode } from 'react'

/** Thin React wrappers over the Works Design System primitives (`.btn`, `.toggle`, `.badge`, `.card`, `.nav-link`…).
 * No inline colors; every look comes from the DS CSS classes/tokens. */
export function Button({ children, variant = 'secondary', size = 'md', className = '', ...rest }: { children: ReactNode; variant?: 'primary' | 'secondary' | 'danger' | 'ghost'; size?: 'sm' | 'md' | 'lg' } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : size === 'lg' ? 'btn-lg' : ''} ${className}`} {...rest}>{children}</button>
}
export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return <label className="toggle" style={{ padding: '6px 0' }}><input type="checkbox" role="switch" checked={checked} onChange={e => onChange(e.target.checked)} /><span>{label}</span></label>
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'error' | 'info' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}
export function Chip({ children, active, onClick }: { children: ReactNode; active?: boolean; onClick?: () => void }) {
  return <button type="button" className={`chip ${active ? 'is-active' : ''}`} onClick={onClick}>{children}</button>
}
/** Hover/focus info icon — long explanations live here instead of on the page. */
export function Tip({ text, size = 14 }: { text: ReactNode; size?: number }) {
  if (!text) return null
  return <span className="tip" tabIndex={0} aria-label={typeof text === 'string' ? text : undefined}>
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>
    <span className="tip-body" role="tooltip">{text}</span>
  </span>
}
export function Card({ title, subtitle, right, children, className = '', body = true, icon }: { title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; body?: boolean; icon?: ReactNode }) {
  return <section className={`card ${className}`}>{title && <header className="card-header" style={{ padding: '12px 18px 10px' }}><div className="card-header-info"><h3 className="card-title" style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>{icon && <span className="card-icon">{icon}</span>}{title}{subtitle && <Tip text={subtitle} />}</h3></div>{right}</header>}{body ? <div className="card-body" style={{ padding: '14px 18px' }}>{children}</div> : children}</section>
}
/** Icon button; label is optional (tooltip-only when omitted). */
export function IconBtn({ icon, label, title, variant = 'secondary', active, ...rest }: { icon: ReactNode; label?: ReactNode; title?: string; variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; active?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`btn btn-${variant} icon-btn ${label ? '' : 'icon-only'} ${active ? 'is-active' : ''}`} title={title ?? (typeof label === 'string' ? label : undefined)} aria-label={title ?? (typeof label === 'string' ? label : undefined)} {...rest}>{icon}{label && <span>{label}</span>}</button>
}
/** KPI tile: icon, big value, short label; the explanation is in the info tip. */
export function Kpi({ icon, label, value, sub, tip, tone }: { icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode; tip?: ReactNode; tone?: 'good' | 'warn' | 'bad' | 'info' }) {
  return <div className={`kpi kpi-${tone ?? 'info'}`}><span className="kpi-icon">{icon}</span><div className="kpi-text"><span className="kpi-value">{value}</span><span className="kpi-label">{label}{tip && <Tip text={tip} size={12} />}</span>{sub && <span className="kpi-sub">{sub}</span>}</div></div>
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span className="field-label">{label}</span>{children}</label>
}
export function Select({ className = '', ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) { return <select className={`select ${className}`} {...rest} /> }
export function Input({ className = '', ...rest }: React.InputHTMLAttributes<HTMLInputElement>) { return <input className={`input ${className}`} {...rest} /> }
export function NavLink({ icon, label, active, onClick, title }: { icon: ReactNode; label: string; active?: boolean; onClick?: () => void; title?: string }) {
  return <button className={`nav-link ${active ? 'is-parent-active' : ''}`} onClick={onClick} title={title}><span className="nav-link-icon">{icon}</span><span className="nav-link-label">{label}</span></button>
}
export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'error' | 'success' }) {
  return <div className="stat"><span className="stat-label">{label}</span><span className="stat-value" style={tone === 'error' ? { color: 'var(--color-error)' } : tone === 'success' ? { color: 'var(--color-success)' } : undefined}>{value}</span>{sub && <span className="stat-sub">{sub}</span>}</div>
}
const I = (d: string, extra?: ReactNode) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={d} />{extra}</svg>
export const Icon = {
  twin: I('M3 9l9-5 9 5-9 5-9-5zM3 14l9 5 9-5'),
  design: I('M4 4h16v16H4zM4 10h16M10 4v16'),
  play: I('M6 4l14 8-14 8z'),
  monitor: I('M3 5h18v11H3zM8 20h8M12 16v4'),
  bench: I('M4 20h16M6 20V9h4v11M14 20V4h4v16'),
  home: I('M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z'),
  press: I('M4 20h16M6 20V9h12v11M9 9V4h6v5M9 14h6'),
  weld: I('M4 20h7M7 20v-6l5-5 4 4-4 4M14 7l3-3 3 3-3 3'),
  qa: I('M21 21l-4.3-4.3M8 11l2 2 4-4', <circle cx="11" cy="11" r="7" />),
  power: I('M13 2L4 14h7l-1 8 9-12h-7z'),
  network: I('M12 7v5m0 0l-6 5m6-5l6 5', <><circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /></>),
  alert: I('M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01'),
  brain: I('M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4.5A2.5 2.5 0 0 0 9 4zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1'),
  dice: I('M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', <><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor" /><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /></>),
  pause: I('M7 4h3v16H7zM14 4h3v16h-3z'),
  step: I('M5 4l10 8-10 8zM19 5v14'),
  ffwd: I('M3 5l8 7-8 7zM12 5l8 7-8 7z'),
  reset: I('M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5'),
  gear: I('M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'),
  target: I('M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', <><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>),
  box: I('M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8M12 13v8'),
  trend: I('M3 17l6-6 4 4 8-8M14 7h7v7'),
  gauge: I('M12 14l4-4M3.3 17a9 9 0 1 1 17.4 0'),
  robot: I('M5 9h14v8H5zM8 17v3M16 17v3M12 9V6M10 5h4', <><circle cx="9" cy="13" r="1" fill="currentColor" /><circle cx="15" cy="13" r="1" fill="currentColor" /></>),
  battery: I('M3 8h15v8H3zM21 11v2M7 11v2'),
  list: I('M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'),
  camera: I('M3 8h4l2-3h6l2 3h4v11H3z', <circle cx="12" cy="13" r="3.5" />),
  flask: I('M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3M7 15h10'),
  shield: I('M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4'),
  download: I('M12 3v12M7 10l5 5 5-5M4 21h16'),
  factory: I('M3 21V10l6 4V10l6 4V6l6 4v11zM7 18h2M12 18h2M17 18h2'),
  sigma: I('M18 4H6l6 8-6 8h12'),
  eye: I('M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z', <circle cx="12" cy="12" r="3" />),
  check: I('M5 12l5 5 9-10'),
  plug: I('M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4'),
  db: I('M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'),
}
