import { useState } from 'react'
import { ago, post, type Action, type State } from './api'
import { Diff, useRun } from './ui'

const ST_HU: Record<string, string> = {
  PENDING_APPROVAL: 'jóváhagyásra vár', APPROVED: 'jóváhagyva', EXECUTING: 'végrehajtás alatt', EXECUTED: 'végrehajtva', MANUALLY_CONFIRMED: 'kézzel végrehajtva',
  REJECTED: 'elutasítva', FAILED: 'sikertelen', CANCELLED: 'megszakítva', DRAFT: 'tervezet',
}
const OPEN = ['PENDING_APPROVAL', 'APPROVED', 'FAILED']

export function ActionCard({ a, onOpen }: { a: Action; onOpen?: (id: number) => void }) {
  const run = useRun()
  const [note, setNote] = useState('')
  const decide = (decision: string, ok: string) => run(() => post(`/api/actions/${a.id}/decide`, { decision, note: note || undefined }), ok)
  const info = a.execution_result?.info
  const p = a.proposed_payload ?? {}
  return (
    <div className="card p-2.5 mb-2" data-action={a.id}>
      <div className="flex flex-wrap gap-2 items-center text-[12px]">
        <b>{a.target_system}</b><span className="tag">{a.action_type}</span>
        <span className="text-mut">{a.target_reference ?? ''}</span>
        <span className={'tag ' + (a.status === 'PENDING_APPROVAL' ? 'text-warn' : '')}>{ST_HU[a.status] ?? a.status}</span>
        <span className="text-mut ml-auto">{ago(a.created_at)}{a.ticket_id && onOpen ? <> · <a href="#/approval" onClick={e => { e.preventDefault(); onOpen(a.ticket_id!) }}>#{a.ticket_id}</a></> : ''}</span>
      </div>
      {a.target_system === 'file' ? (
        <>{p.path && <div className="text-[12px] text-mut mt-1">Fájl: {p.path}</div>}<Diff text={a.diff || '(nincs eltérés)'} /></>
      ) : typeof p.text === 'string' ? (
        <>{Object.keys(p).length > 1 && <pre className="code">{JSON.stringify({ ...p, text: undefined }, null, 2)}</pre>}
          <div className="card p-2 my-1 text-[13px]" style={{ whiteSpace: 'pre-wrap' }}>{p.text}</div></>
      ) : <pre className="code">{JSON.stringify(p, null, 2)}</pre>}
      {info && <div className="text-[12px] p-2 rounded-md my-1" style={{ background: 'color-mix(in srgb,var(--warn) 15%,transparent)' }}>{String(info)}</div>}
      {a.execution_result && !info && Object.values(a.execution_result).some(v => v != null) && <pre className="code">{JSON.stringify(a.execution_result, null, 2)}</pre>}
      {a.decision_note && <div className="text-[12px] text-mut">Megjegyzés: {a.decision_note}</div>}
      {OPEN.includes(a.status) && (
        <div className="flex flex-wrap gap-2 items-center mt-1">
          <input className="inp !w-56" placeholder="Megjegyzés (opcionális)" value={note} onChange={e => setNote(e.target.value)} />
          {a.status === 'PENDING_APPROVAL' && <button className="btn ok" onClick={() => decide('approve', 'Jóváhagyva')}>Jóváhagy</button>}
          {a.status !== 'FAILED' && <button className="btn bad" onClick={() => decide('reject', 'Elutasítva')}>Elutasít</button>}
          <button className="btn" onClick={() => decide('manual', 'Kézi végrehajtás rögzítve')}>Kézzel végrehajtottam</button>
        </div>)}
    </div>)
}

export default function Approval({ S, onOpen }: { S: State; onOpen: (id: number) => void }) {
  const rank = (a: Action) => (a.status === 'PENDING_APPROVAL' ? 0 : a.status === 'APPROVED' || a.status === 'FAILED' ? 1 : 2)
  const list = [...S.actions].sort((a, b) => rank(a) - rank(b) || b.id - a.id)
  return (
    <div className="p-4 max-w-4xl">
      <h2 className="text-base font-semibold m-0 mb-2">Jóváhagyási központ</h2>
      {list.length === 0 && <div className="text-mut">Nincs művelet.</div>}
      {list.map(a => <ActionCard key={a.id} a={a} onOpen={onOpen} />)}
    </div>)
}
