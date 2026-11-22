import { useCallback, useEffect, useState } from 'react'
import { FINAL, get, post, setOn401, ago, hm, num, type State } from './api'
import { UIProvider, useRun, useUI, Skel } from './ui'
import Board from './Board'
import NewTicket from './NewTicket'
import TicketDialog from './TicketDialog'
import Approval from './Approval'
import Knowledge from './Knowledge'
import Agents from './Agents'
import Config from './Config'

const VIEWS = [['board', 'Tábla'], ['approval', 'Jóváhagyás'], ['knowledge', 'Tudás'], ['agents', 'Ágensek'], ['config', 'Konfiguráció']] as const
type View = typeof VIEWS[number][0]
const viewFromHash = (): View => (VIEWS.find(v => '#/' + v[0] === location.hash)?.[0]) ?? 'board'

export default function App() {
  const [auth, setAuth] = useState<'?' | 'in' | 'out'>('?')
  useEffect(() => {
    setOn401(() => setAuth('out'))
    get('/api/me').then(() => setAuth('in')).catch(() => setAuth('out'))
  }, [])
  return (
    <UIProvider>
      {auth === '?' ? null : auth === 'out' ? <Login onDone={() => setAuth('in')} /> : <Shell onLogout={() => setAuth('out')} />}
    </UIProvider>
  )
}

function Login({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { await post('/api/login', { key }); onDone() } catch (x: any) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="card max-w-sm mx-auto mt-[20vh] p-5 flex flex-col gap-3">
      <h1 className="text-lg font-semibold m-0">AgentHub belépés</h1>
      <label className="text-[12px] text-mut" htmlFor="key">Dashboard-kulcs</label>
      <input id="key" className="inp" type="password" autoFocus autoComplete="current-password" value={key} onChange={e => setKey(e.target.value)} />
      {err && <div className="text-bad text-[13px]" role="alert">{err}</div>}
      <button className="btn pri" disabled={busy || !key.trim()}>Belépés</button>
    </form>
  )
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [S, setS] = useState<State | null>(null)
  const [live, setLive] = useState(false)
  const [view, setView] = useState<View>(viewFromHash)
  const [proj, setProj] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => get<State>('/api/state').then(setS).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    let timer: number
    const es = new EventSource('/api/stream')
    es.onopen = () => setLive(true)
    es.onerror = () => setLive(false) // a böngésző magától újracsatlakozik
    es.addEventListener('hello', () => load())
    es.addEventListener('change', () => { clearTimeout(timer); timer = window.setTimeout(load, 300) })
    return () => { es.close(); clearTimeout(timer) }
  }, [load])
  useEffect(() => {
    const h = () => setView(viewFromHash()); addEventListener('hashchange', h); return () => removeEventListener('hashchange', h)
  }, [])

  const logout = async () => { await post('/api/logout').catch(() => {}); onLogout() }
  const pending = S?.actions.filter(a => a.status === 'PENDING_APPROVAL').length ?? 0

  return (
    <>
      <header className="flex flex-wrap gap-2 items-stretch px-4 py-1.5 border-b border-line bg-card md:sticky top-0 z-10">
        {S ? <HeaderBoxes S={S} reload={load} /> : <div className="flex gap-2 flex-1"><div className="w-40"><Skel h={46} /></div><div className="w-40"><Skel h={46} /></div></div>}
        <div className="flex items-center gap-2 ml-auto">
          <button className="btn pri" onClick={() => setCreating(true)}>+ Új feladat</button>
          <span className="text-[12px] text-mut whitespace-nowrap" title={live ? 'Élő kapcsolat' : 'Újracsatlakozás…'}>
            <span className={'dot ' + (live ? 'on' : 'off')} />{live ? 'élő' : 'újracsatlakozás…'}
          </span>
          <button className="btn" onClick={logout}>Kilépés</button>
        </div>
        {S && S.projects.length > 1 && (
          <nav className="basis-full flex flex-wrap gap-1 pt-1.5 mt-0.5 border-t border-line" aria-label="Projektek">
            {[{ id: '', name: 'Minden projekt' }, ...S.projects].map(p => {
              // várakozó: még nem lezárt ticket (a projekt nélküliek a „Minden projekt” alatt)
              const n = S.tickets.filter(t => !FINAL.includes(t.status) && (!p.id || t.project_id === p.id)).length
              return (
                <button key={p.id} className={'btn !py-0.5 ' + (proj === p.id ? 'pri' : '')} aria-pressed={proj === p.id} onClick={() => setProj(p.id)}>
                  {p.name} <span className={proj === p.id ? '' : 'text-mut'}>({n})</span></button>)
            })}
          </nav>
        )}
      </header>
      <nav className="flex flex-wrap gap-1 px-4 pt-2" aria-label="Nézetek">
        {VIEWS.map(([k, t]) => (
          <a key={k} href={'#/' + k} className={'btn no-underline ' + (view === k ? 'pri' : '')} style={view === k ? undefined : { color: 'var(--fg)' }}>
            {t}{k === 'approval' && pending > 0 && <b className="ml-1">({pending})</b>}{k === 'knowledge' && S && S.knowledge.length > 0 && <b className="ml-1">({S.knowledge.length})</b>}
          </a>
        ))}
      </nav>
      {!S ? <div className="grid grid-cols-4 gap-3 p-4">{[0, 1, 2, 3].map(i => <div key={i} className="flex flex-col gap-2"><Skel h={20} /><Skel /><Skel /></div>)}</div> : (
        <>
          {view === 'board' && <Board S={S} proj={proj} onOpen={setOpenId} />}
          {view === 'approval' && <Approval S={S} onOpen={setOpenId} />}
          {view === 'knowledge' && <Knowledge S={S} proj={proj} />}
          {view === 'agents' && <Agents S={S} onOpen={setOpenId} />}
          {view === 'config' && <Config S={S} />}
          {openId !== null && <TicketDialog id={openId} S={S} onClose={() => setOpenId(null)} />}
          {creating && <NewTicket S={S} proj={proj} onClose={() => setCreating(false)} onCreated={id => { setCreating(false); setOpenId(id) }} />}
        </>
      )}
    </>
  )
}

function HeaderBoxes({ S, reload }: { S: State; reload: () => void }) {
  const run = useRun()
  const { confirm } = useUI()
  const u = S.usage_24h
  const toggle = async (name: string, label: string, state: string) => {
    const stop = state !== 'stop'
    if (stop && !(await confirm(`Leállítod: ${label}? ${name === 'coordinator' ? 'A következő kör elején kilép.' : 'A futó ticketet még befejezi.'}`))) return
    await run(() => post('/api/control', { name, state: stop ? 'stop' : 'run' }), stop ? 'Leállítva' : 'Engedélyezve')
    reload()
  }
  const coord = S.agents.find(a => a.role === 'coordinator' && a.alive)
  const cs = S.controls.coordinator
  const St = ({ alive, control }: { alive: boolean; control: string }) =>
    control === 'stop' ? <b className="text-bad">leállítva</b> : alive ? <b className="text-ok"><span className="dot on" />fut</b> : <b className="text-warn">nem fut</b>
  return (
    <>
      <div className="box"><h3>Használat (24 óra)</h3>
        <div>{num(u.runs)} futás · {u.tokens_in == null && u.tokens_out == null ? '— token' : `${num((u.tokens_in ?? 0) + (u.tokens_out ?? 0))} token`}
          {' · '}<span title="A költség becsült érték">{u.cost_usd == null ? '—' : '~$' + u.cost_usd.toFixed(2)} <span className="text-mut">(becslés)</span></span></div></div>
      <div className="box"><h3>Koordinátor</h3>
        <div className="flex gap-2 items-center"><St alive={!!coord} control={cs} />
          <span className="text-mut">{coord ? ago(coord.last_heartbeat) : ''}</span>
          <button className="btn" onClick={() => toggle('coordinator', 'a koordinátort', cs)}>{cs === 'stop' ? 'Engedélyez' : 'Leállít'}</button></div>

        {S.last_cycle && <div className="text-mut text-[12px]" title={S.last_cycle.errors.join('\n')}>
          utolsó kör {hm(S.last_cycle.at)}: {S.last_cycle.sources_checked} forrás, {S.last_cycle.created} új, {S.last_cycle.updated} frissült, {S.last_cycle.pending} döntésre vár
          {S.last_cycle.errors.length > 0 && <b className="text-bad"> · {S.last_cycle.errors.length} hiba</b>}</div>}</div>
      <div className="box"><h3>Kimenő kommunikáció</h3>
        <label className="flex gap-2 items-center" title="Kikapcsolva a Coordinator és a Workerek kártyakommentet, kártyamozgatást, üzenetet csak a Jóváhagyási központban jóváhagyva küldhetnek ki. Váltás után a futó ágensek leállnak, és újra kell indítani őket.">
          <input type="checkbox" checked={!S.controls.outbound_approval} onChange={async e => {
            const allow = e.target.checked
            if (!(await confirm(allow ? 'Az ágensek ezután jóváhagyás nélkül írhatnak ki (Trello, üzenet). A futó ágensek leállnak; indítsd újra őket.' : 'Az ágensek ezután minden kimenő kommunikációt jóváhagyásra küldenek. A futó ágensek leállnak; indítsd újra őket.'))) return
            await run(() => post('/api/outbound-approval', { value: !allow }), allow ? 'Kimenő kommunikáció engedélyezve' : 'Kimenő kommunikáció csak jóváhagyással')
            reload()
          }} />
          {S.controls.outbound_approval ? <b className="text-warn">csak jóváhagyással</b> : <span className="text-ok">engedélyezve</span>}</label></div>
      <div className="box"><h3>Workerek</h3>
        {S.projects.length === 0 && <span className="text-mut">nincs projekt</span>}
        {S.projects.map(p => {
          const w = S.agents.find(a => a.role === 'worker' && a.project_id === p.id && a.alive)
          return (
            <div key={p.id} className="flex gap-2 items-center flex-wrap">
              {S.projects.length > 1 && <span className="text-mut">{p.name}:</span>}
              <St alive={!!w} control={p.worker_control} />
              <span className="text-mut">{w ? `${w.active_ticket_id ? '#' + w.active_ticket_id : 'tétlen'} · ${ago(w.last_heartbeat)}` : ''}</span>
              {p.monthly_budget_usd != null && <span className={p.over_budget ? 'text-bad font-semibold' : 'text-mut'} title="Havi költség a kerethez képest (becslés)">
                ~${(p.month_cost_usd ?? 0).toFixed(2)} / ${p.monthly_budget_usd.toFixed(2)}{p.over_budget && ' · keret túllépve'}</span>}
              <button className="btn" onClick={() => toggle('worker:' + p.id, `a(z) ${p.name} workert`, p.worker_control)}>{p.worker_control === 'stop' ? 'Engedélyez' : 'Leállít'}</button>
            </div>)
        })}</div>
      <div className="box"><h3>Források</h3>
        {S.sources.length === 0 && <span className="text-mut">nincs forrás</span>}
        {S.sources.map(s => (
          <div key={s.id} className="flex gap-2"><span>{s.name}:</span><span className="text-mut">{s.last_poll_at ? hm(s.last_poll_at) : 'még nem futott'}</span>
            {s.last_error && <span className="text-bad truncate max-w-[220px]" title={s.last_error}>hiba: {s.last_error}</span>}</div>))}</div>
    </>
  )
}
