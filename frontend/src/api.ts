// Vékony fetch-réteg: sütis hitelesítés, JSON, magyar hibaüzenetek a szerver `detail` mezőjéből.
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

let on401: () => void = () => {}
export const setOn401 = (f: () => void) => { on401 = f }

export async function api<T = any>(method: string, url: string, body?: unknown, raw = false): Promise<T> {
  const r = await fetch(url, {
    method, credentials: 'same-origin',
    headers: body === undefined ? {} : { 'Content-Type': raw ? 'text/plain' : 'application/json' },
    body: body === undefined ? undefined : raw ? (body as string) : JSON.stringify(body),
  })
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* nem JSON */ }
    if (r.status === 401 && url !== '/api/login') on401()
    throw new ApiError(r.status, msg)
  }
  return r.json()
}
export const get = <T = any>(u: string) => api<T>('GET', u)
export const post = <T = any>(u: string, b: unknown = {}) => api<T>('POST', u, b)
export const patch = <T = any>(u: string, b: unknown) => api<T>('PATCH', u, b)

// ---- típusok (csak a használt mezők)
export interface Ticket {
  id: number; project_id: string | null; title: string; description: string; type: string; priority: number
  status: string; hotfix: boolean; hotfix_by: string | null; start_approved: boolean; start_approved_by: string | null; start_approved_reason: string | null
  source_url: string | null; created_at: string; updated_at: string; last_error: string | null
  result_summary: string | null; lease_stale?: boolean; retry_count: number; created_by?: string; assigned_worker_id: string | null
}
export interface Question {
  id: number; ticket_id: number; question: string; context: string; suggested_answer: string | null
  status: string; created_at: string; answer?: string | null; answers?: { answer: string; created_by: string; created_at: string }[]
}
export interface Action {
  id: number; ticket_id: number | null; project_id: string | null; action_type: string; target_system: string
  target_reference: string | null; proposed_payload: any; status: string; created_by: string; created_at: string
  decision_note: string | null; execution_result: any; diff: string | null
}
export interface Knowledge { id: number; project_id: string; category: string; title: string; content: string; status: string; created_at: string; source_ticket_id: number | null; created_by?: string }
export interface Agent { id: string; project_id: string | null; role: string; status: string; last_heartbeat: string; session_id: string | null; model: string | null; active_ticket_id: number | null; started_at: string; alive: boolean
  stats: { runs: number; tokens_in: number | null; tokens_out: number | null; cost_usd: number | null; run_seconds: number | null } }
export interface Project { id: string; name: string; is_active: boolean; working_directory: string; ticket_count: number; worker_control: string; month_cost_usd: number | null; monthly_budget_usd: number | null; over_budget: boolean }
export interface Source { id: string; name: string; adapter_type: string; is_active: boolean; last_poll_at: string | null; last_error: string | null }
export interface Ev { id: number; ticket_id: number; event_type: string; old_status: string | null; new_status: string | null; actor_type: string; actor_id: string | null; payload: any; created_at: string; title?: string; project_id?: string | null }
export interface State {
  installation: string; version: string; now: string; projects: Project[]; tickets: Ticket[]; questions: Question[]
  actions: Action[]; knowledge: Knowledge[]; agents: Agent[]; sources: Source[]; events: Ev[]
  controls: { coordinator: string; outbound_approval: boolean }; usage_24h: { runs: number; tokens_in: number | null; tokens_out: number | null; cost_usd: number | null }
  last_cycle: { at: string; sources_checked: number; created: number; updated: number; routed: number; pending: number; errors: string[] } | null
}

// ---- formázók
export const ago = (iso?: string | null) => {
  if (!iso) return 'soha'
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  return m < 1 ? 'most' : m < 60 ? `${m} perce` : m < 1440 ? `${Math.round(m / 60)} órája` : `${Math.round(m / 1440)} napja`
}
export const hm = (iso?: string | null) => iso ? new Date(iso).toLocaleString('hu-HU', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
export const num = (n: number | null | undefined) => n == null ? '—' : n.toLocaleString('hu-HU')
export const STATUS_HU: Record<string, string> = {
  NEW: 'Besorolásra vár', NEEDS_TRIAGE: 'Besorolásra vár', READY: 'Indítható', IN_PROGRESS: 'Folyamatban',
  WAITING_FOR_INPUT: 'Blokkolt – rád vár (kérdés)', PENDING_APPROVAL: 'Blokkolt – rád vár (jóváhagyás)', REVIEW: 'Blokkolt – rád vár (ellenőrzés)',
  BLOCKED: 'Blokkolt – külső félre vár', FAILED: 'Blokkolt – hiba', AWAITING_RELEASE: 'Kész – kiadásra vár', COMPLETED: 'Kész', CANCELLED: 'Megszakítva',
}
export const FINAL = ['COMPLETED', 'CANCELLED']

// a ticketek magyar címkéi (tábla, ticketnézet)
export const TYPE_HU: Record<string, string> = { bug: 'hiba', feature: 'fejlesztés', task: 'feladat', question: 'kérdés' }
export const ACTOR_HU: Record<string, string> = { human: 'ember', coordinator: 'Coordinator', worker: 'Worker', system: 'rendszer' }
export const EV_HU: Record<string, string> = {
  created: 'Létrehozva', classified: 'Besorolva', routed: 'Szabály szerint besorolva', triaged: 'Besorolva', note: 'Megjegyzés',
  comment: 'Megjegyzés a Workernek', legacy: 'Régi napló', migrated: 'Átköltöztetve', status_fix: 'Állapot javítva', source_update: 'A forrás változott',
  possible_duplicate: 'Lehetséges duplikátum', merged_in: 'Ide összevonva', merged: 'Összevonva', go_on: 'Mehet bekapcsolva',
  go_off: 'Mehet kikapcsolva', go_restored: 'Mehet visszaállítva', hotfix_on: 'Hotfix bekapcsolva', hotfix_off: 'Hotfix kikapcsolva',
  claimed: 'A Worker felvette', edited: 'Szerkesztve', answered: 'Megválaszolva', dismissed: 'Elvetve', reopened: 'Újranyitva',
  question: 'Kérdés', answer: 'Válasz', action_proposed: 'Művelet javasolva', blocked: 'Blokkolva', released: 'Kiadva',
  release_failed: 'Kiadás sikertelen', returned: 'Visszaküldve', failed: 'Hiba', retry: 'Újrapróba', auto_retry: 'Automatikus újrapróba',
  awaiting_approval: 'Jóváhagyásra vár', actions_settled: 'Műveletek lezárva', finished: 'Kész', accepted: 'Elfogadva',
}
export const stColor = (st: string) => ({ READY: 'var(--ok)', IN_PROGRESS: 'var(--acc)', WAITING_FOR_INPUT: 'var(--warn)', PENDING_APPROVAL: 'var(--warn)',
  NEEDS_TRIAGE: 'var(--warn)', NEW: 'var(--warn)', BLOCKED: 'var(--bad)', FAILED: 'var(--bad)', AWAITING_RELEASE: 'var(--acc)', REVIEW: 'var(--acc)' } as Record<string, string>)[st]

