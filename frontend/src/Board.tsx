import { useState, type ReactNode } from 'react'
import { LayoutGroup, motion, AnimatePresence } from 'motion/react'
import { ACTOR_HU, EV_HU, STATUS_HU, TYPE_HU, ago, patch, post, hm, type State, type Ticket } from './api'
import { useRun } from './ui'

const COLS: { key: string; title: string; st: string[]; fold?: boolean }[] = [
  { key: 'inbox', title: 'Besorolásra vár', st: ['NEW', 'NEEDS_TRIAGE'] },
  { key: 'ready', title: 'Indítható', st: ['READY'] },
  // minden, ami áll: rád vár (kérdés, jóváhagyás, ellenőrzés), külső félre vár, hiba — a rád várók elöl
  { key: 'blocked', title: 'Blokkolt', st: ['WAITING_FOR_INPUT', 'PENDING_APPROVAL', 'REVIEW', 'BLOCKED', 'FAILED'] },
  { key: 'wip', title: 'Folyamatban', st: ['IN_PROGRESS'] },
  { key: 'release', title: 'Kész – kiadásra vár', st: ['AWAITING_RELEASE'] },
  { key: 'done', title: 'Kész (24 óra)', st: ['COMPLETED', 'CANCELLED'], fold: true },
]
const YOURS = ['WAITING_FOR_INPUT', 'PENDING_APPROVAL', 'REVIEW']
const GO_STATES = ['READY', 'NEW', 'NEEDS_TRIAGE']
const PCOL = ['', 'var(--p1)', 'var(--p2)', 'var(--p3)']

function useFold(k: string): [boolean, () => void] {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('fold-' + k) === '1' } catch { return false } })
  return [open, () => setOpen(o => { try { localStorage.setItem('fold-' + k, o ? '0' : '1') } catch { /* tiltott tároló */ } return !o })]
}

export default function Board({ S, proj, onOpen }: { S: State; proj: string; onOpen: (id: number) => void }) {
  const tickets = S.tickets.filter(t => !proj || !t.project_id || t.project_id === proj)
  const pname = (id: string | null) => id ? S.projects.find(p => p.id === id)?.name ?? id : null
  const showProj = S.projects.length > 1 && !proj  // a kiválasztott projektfülön fölösleges
  const mailFor = new Set(S.actions.filter(a => a.target_system === 'email' && ['PENDING_APPROVAL', 'APPROVED', 'FAILED'].includes(a.status)).map(a => a.ticket_id))
  const events = S.events.filter(e => !proj || !e.project_id || e.project_id === proj)
  return (
    <LayoutGroup>
      <main className="flex gap-3 px-4 py-3 items-start overflow-x-auto">
        {COLS.map(c => {
          const ts = tickets.filter(t => c.st.includes(t.status)).sort((a, b) => +YOURS.includes(b.status) - +YOURS.includes(a.status))
          return (
            <Column key={c.key} id={c.key} title={c.title} fold={c.fold}
              count={ts.length} extra={c.key === 'ready' ? `${ts.filter(t => t.start_approved).length} bepipálva` : c.key === 'blocked' && ts.some(t => YOURS.includes(t.status)) ? `${ts.filter(t => YOURS.includes(t.status)).length} rád vár` : undefined}>
              <AnimatePresence initial={false}>
                {ts.map(t => <Card key={t.id} t={t} proj={showProj ? pname(t.project_id) : null} onOpen={onOpen} mail={mailFor.has(t.id)} />)}
              </AnimatePresence>
              {ts.length === 0 && <div className="text-mut text-[12px] px-1">nincs ticket</div>}
            </Column>
          )
        })}
        <Column id="events" title="Események" fold count={events.length} wide>
          <div className="flex flex-col gap-1.5 max-h-[calc(100vh-150px)] overflow-auto pr-0.5">
            {events.slice(0, 60).map(e => (
              <div key={e.id} className="card px-2 py-1 text-[12px] cursor-pointer break-words" style={{ borderLeft: '4px solid var(--mut)' }} onClick={() => onOpen(e.ticket_id)}>
                <div className="flex gap-1.5 text-mut text-[11px] flex-wrap"><b className="text-fg">{EV_HU[e.event_type] ?? e.event_type}</b>
                  <span>{ACTOR_HU[e.actor_type] ?? e.actor_type}</span><span>{hm(e.created_at)}</span>
                  {e.new_status && <span>{e.old_status ? (STATUS_HU[e.old_status] ?? e.old_status) + ' → ' : ''}{STATUS_HU[e.new_status] ?? e.new_status}</span>}</div>
                <div>#{e.ticket_id} {e.title}</div>
                {e.payload?.note && <div className="text-mut">{String(e.payload.note)}</div>}
              </div>))}
            {events.length === 0 && <div className="text-mut text-[12px]">nincs esemény</div>}
          </div>
        </Column>
      </main>
    </LayoutGroup>
  )
}

function Column({ id, title, count, extra, fold, wide, children }: { id: string; title: string; count: number; extra?: string; fold?: boolean; wide?: boolean; children: ReactNode }) {
  const [open, toggle] = useFold(id)
  const closed = fold && !open
  return (
    <motion.section layout="position" className={closed ? 'flex-none w-9' : wide ? 'flex-[0_1_320px] min-w-[220px]' : 'flex-1 min-w-[175px]'}>
      <h2 className={'text-[13px] text-mut m-0 mb-2 flex justify-between gap-2 ' + (fold ? 'cursor-pointer select-none ' : '') + (closed ? 'card py-2.5 px-1.5 min-h-40 [writing-mode:vertical-rl] justify-start hover:border-acc' : '')}
        onClick={fold ? toggle : undefined} role={fold ? 'button' : undefined} aria-expanded={fold ? !closed : undefined} title={fold ? 'Kattintásra kinyílik / becsukódik' : undefined}>
        <span>{fold ? (closed ? '▸ ' : '▾ ') : ''}{title}</span>
        <span className="flex gap-2">{extra && !closed && <span className={id === 'blocked' ? 'text-warn font-semibold' : 'text-ok'}>{extra}</span>}
          <motion.span key={count} initial={{ scale: 1.5 }} animate={{ scale: 1 }}>{count}</motion.span></span>
      </h2>
      {!closed && children}
    </motion.section>
  )
}

function Card({ t, proj, onOpen, mail }: { t: Ticket; proj: string | null; onOpen: (id: number) => void; mail?: boolean }) {
  const run = useRun()
  const coord = t.start_approved_by === 'coordinator'
  return (
    <motion.div layout layoutId={'t' + t.id} initial={{ opacity: 0, scale: .94 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: .94 }}
      whileHover={{ y: -2, boxShadow: '0 4px 14px rgb(0 0 0 / .12)' }} transition={{ duration: .25 }}
      className="card px-2.5 py-2 mb-2 cursor-pointer" style={{ borderLeft: `4px solid ${PCOL[t.priority]}` }}
      onClick={() => onOpen(t.id)} data-ticket={t.id}>
      <div className="font-medium leading-snug"><span className="text-mut font-normal text-[12px] mr-1">#{t.id}</span>{t.title}</div>
      <div className="flex flex-wrap gap-1.5 mt-1 text-[12px] text-mut items-center">
        <span className="font-semibold" style={{ color: PCOL[t.priority] }} title="Prioritás">P{t.priority}</span>
        {proj && <span className="tag">{proj}</span>}<span className="tag">{TYPE_HU[t.type] ?? t.type}</span>
        {t.hotfix && <span className="tag text-bad" style={{ borderColor: 'var(--bad)' }} title="Teszt után azonnal élesítendő">HOTFIX</span>}
        {t.status === 'BLOCKED' && <span className="tag" title={t.last_error ?? ''}>külső félre vár</span>}
        {t.status === 'FAILED' && <span className="tag text-bad" title={t.last_error ?? ''}>hiba</span>}
        {t.status === 'NEEDS_TRIAGE' && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>besorolás kell</span>}
        {mail ? <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>rád vár: levél elküldése</span>
          : t.status === 'PENDING_APPROVAL' && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>rád vár: jóváhagyás</span>}
        {t.status === 'WAITING_FOR_INPUT' && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>rád vár: kérdés</span>}
        {t.status === 'REVIEW' && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>rád vár: ellenőrzés</span>}
        {t.status === 'CANCELLED' && <span className="tag">megszakítva</span>}
        {t.lease_stale && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }} title="A lease lejárt, a worker valószínűleg nem él">⚠ lejárt lease</span>}
        {t.source_url && <a href={t.source_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>forrás</a>}
        <span title={hm(t.updated_at)}>{ago(t.updated_at)}</span>
      </div>
      <div className="flex gap-2 mt-1.5 items-center justify-between" onClick={e => e.stopPropagation()}>
        {GO_STATES.includes(t.status) ? (
          <label className="text-[12px] flex flex-wrap gap-x-1.5 items-center cursor-pointer min-w-0"
            title={coord ? (t.start_approved_reason ?? 'a koordinátor indoklás nélkül') : undefined}>
            <input type="checkbox" checked={t.start_approved} onChange={e => run(() => post(`/api/tickets/${t.id}/go`, { value: e.target.checked }))} />
            Mehet{coord && <b className="text-warn" title={t.start_approved_reason ?? ''}>Coordinator pipálta</b>}
          </label>) : <span />}
        <span className="flex gap-0.5" role="group" aria-label="Prioritás">
          {[1, 2, 3].map(p => <button key={p} className={'btn sm ' + (p === t.priority ? 'pri' : '')} onClick={() => p !== t.priority && run(() => patch(`/api/tickets/${t.id}`, { priority: p }))}>{p}</button>)}
        </span>
      </div>
    </motion.div>
  )
}
