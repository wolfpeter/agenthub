import { useState, type ReactNode } from 'react'
import { LayoutGroup, motion, AnimatePresence } from 'motion/react'
import { ago, patch, post, hm, type State, type Ticket } from './api'
import { useRun } from './ui'

const COLS: { key: string; title: string; st: string[]; fold?: boolean }[] = [
  { key: 'inbox', title: 'Beérkező', st: ['NEW', 'NEEDS_TRIAGE'] },
  { key: 'ready', title: 'Indítható', st: ['READY'] },
  { key: 'wip', title: 'Folyamatban', st: ['IN_PROGRESS'] },
  { key: 'wait', title: 'Rád vár', st: ['WAITING_FOR_INPUT', 'PENDING_APPROVAL'] },
  { key: 'review', title: 'Ellenőrzésre vár', st: ['REVIEW'] },
  { key: 'blocked', title: 'Blokkolt', st: ['FAILED'] },
  { key: 'done', title: 'Kész (24 óra)', st: ['COMPLETED', 'CANCELLED'], fold: true },
]
const GO_STATES = ['READY', 'NEW', 'NEEDS_TRIAGE']
const PCOL = ['', 'var(--p1)', 'var(--p2)', 'var(--p3)']

function useFold(k: string): [boolean, () => void] {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('fold-' + k) === '1' } catch { return false } })
  return [open, () => setOpen(o => { try { localStorage.setItem('fold-' + k, o ? '0' : '1') } catch { /* tiltott tároló */ } return !o })]
}

export default function Board({ S, proj, onOpen }: { S: State; proj: string; onOpen: (id: number) => void }) {
  const tickets = S.tickets.filter(t => !proj || !t.project_id || t.project_id === proj)
  const pname = (id: string | null) => S.projects.find(p => p.id === id)?.name ?? (id ?? 'projekt nélkül')
  const showProj = S.projects.length > 1
  const events = S.events.filter(e => !proj || !e.project_id || e.project_id === proj)
  return (
    <LayoutGroup>
      <main className="flex gap-3 px-4 py-3 items-start flex-wrap">
        {COLS.map(c => {
          const ts = tickets.filter(t => c.st.includes(t.status))
          return (
            <Column key={c.key} id={c.key} title={c.title} fold={c.fold}
              count={ts.length} extra={c.key === 'ready' ? `${ts.filter(t => t.start_approved).length} bepipálva` : undefined}>
              <AnimatePresence initial={false}>
                {ts.map(t => <Card key={t.id} t={t} proj={showProj ? pname(t.project_id) : null} onOpen={onOpen} />)}
              </AnimatePresence>
              {ts.length === 0 && <div className="text-mut text-[12px] px-1">nincs ticket</div>}
            </Column>
          )
        })}
        <Column id="events" title="Események" fold count={events.length} wide>
          <div className="flex flex-col gap-1.5 max-h-[calc(100vh-150px)] overflow-auto pr-0.5">
            {events.slice(0, 60).map(e => (
              <div key={e.id} className="card px-2 py-1 text-[12px] cursor-pointer break-words" style={{ borderLeft: '4px solid var(--mut)' }} onClick={() => onOpen(e.ticket_id)}>
                <div className="flex gap-1.5 text-mut text-[11px] flex-wrap"><b className="text-fg">{e.event_type}</b>
                  <span>{e.actor_type}{e.actor_id ? `:${e.actor_id}` : ''}</span><span>{hm(e.created_at)}</span>
                  {e.new_status && <span>{e.old_status ? e.old_status + ' → ' : ''}{e.new_status}</span>}</div>
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
    <motion.section layout="position" className={closed ? 'flex-none w-9' : wide ? 'flex-[0_1_340px] min-w-[240px]' : 'flex-1 min-w-[190px]'}>
      <h2 className={'text-[13px] text-mut m-0 mb-2 flex justify-between gap-2 ' + (fold ? 'cursor-pointer select-none ' : '') + (closed ? 'card py-2.5 px-1.5 min-h-40 [writing-mode:vertical-rl] justify-start hover:border-acc' : '')}
        onClick={fold ? toggle : undefined} role={fold ? 'button' : undefined} aria-expanded={fold ? !closed : undefined} title={fold ? 'Kattintásra kinyílik / becsukódik' : undefined}>
        <span>{fold ? (closed ? '▸ ' : '▾ ') : ''}{title}</span>
        <span className="flex gap-2">{extra && !closed && <span className="text-ok">{extra}</span>}
          <motion.span key={count} initial={{ scale: 1.5 }} animate={{ scale: 1 }}>{count}</motion.span></span>
      </h2>
      {!closed && children}
    </motion.section>
  )
}

function Card({ t, proj, onOpen }: { t: Ticket; proj: string | null; onOpen: (id: number) => void }) {
  const run = useRun()
  const coord = t.start_approved_by === 'coordinator'
  return (
    <motion.div layout layoutId={'t' + t.id} initial={{ opacity: 0, scale: .94 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: .94 }}
      whileHover={{ y: -2, boxShadow: '0 4px 14px rgb(0 0 0 / .12)' }} transition={{ duration: .25 }}
      className="card px-2.5 py-2 mb-2 cursor-pointer" style={{ borderLeft: `4px solid ${PCOL[t.priority]}` }}
      onClick={() => onOpen(t.id)} data-ticket={t.id}>
      <div className="font-medium">{t.title}</div>
      <div className="flex flex-wrap gap-1.5 mt-1 text-[12px] text-mut items-center">
        <span className="font-semibold" style={{ color: PCOL[t.priority] }} title="Prioritás">P{t.priority}</span>
        {proj && <span className="tag">{proj}</span>}<span className="tag">{t.type}</span>
        {t.status === 'NEEDS_TRIAGE' && <span className="tag text-warn" style={{ borderColor: 'var(--warn)' }}>besorolás kell</span>}
        {t.status === 'PENDING_APPROVAL' && <span className="tag">jóváhagyásra vár</span>}
        {t.status === 'WAITING_FOR_INPUT' && <span className="tag">kérdés</span>}
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
