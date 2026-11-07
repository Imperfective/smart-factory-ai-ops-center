const j = async (r: Response) => { if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: r.statusText }))).detail ?? r.statusText); return r.json() }
const post = (url: string, body: any) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(j)
/** Top-bar calls only; the operations center talks to /api/v3/* directly. */
export const api = {
  health: () => fetch('/api/health').then(j),
  setJev: (enabled: boolean) => post('/api/settings/jev', { enabled }),
}
