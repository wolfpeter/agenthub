import { useEffect, useState } from 'react'
import { FINAL, STATUS_HU, ago, get, hm, num, patch, post, type Action, type Ev, type Question, type State, type Ticket } from './api'
import { Dialog, Skel, useRun, useUI } from './ui'
import { ActionCard } from './Approval'
import { TYPES } from './NewTicket'

interface Ref { id: number; source_id: string; external_id: string; url: string | null; merged_from_ticket_id: number | null }
interface Detail extends Ticket { events: Ev[]; questions: Question[]; actions: Action[]; runs: any[]; source_refs: Ref[] }

export default function TicketDialog({ id, S, onClose }: { id: number; S: State; onClose: () => void }) {
  const run = useRun()
  const { confirm } = useUI()
  const [d, setD] = useState<Detail | null>(null)
  const [edit, setEdit] = useState<{ title: string; description: string; type: string } | null>(null)
  const [note, setNote] = useState('')
  const [triageProj, setTriageProj] = useState('')
  const reload = () => get<Detail>(`/api/tickets/${id}`).then(setD).catch(e => run(() => Promise.reject(e)))
  useEffect(() => { reload() }, [id, S.now]) // eslint-disable-line react-hooks/exhaustive-deps

  const tr = async (action: string, ok: string, extra: object = {}) => {
    if (await run(() => post(`/api/tickets/${id}/transition`, { action, note: note || undefined, ...extra }), ok)) { setNote(''); reload() }
  }
  const saveEdit = async () => { if (edit && await run(() => patch(`/api/tickets/${id}`, edit), 'Mentve')) { setEdit(null); reload() } }

  if (!d) return <Dialog onClose={onClose}><div className="mt-6"><Skel h={120} /></div></Dialog>
  const st = d.status
  const final = FINAL.includes(st)
  const pname = S.projects.find(p => p.id === d.project_id)?.name
  return (
    <Dialog onClose={onClose}>
      <div className="pr-10">
        <div className="text-mut text-[12px]">#{d.id} · {STATUS_HU[st] ?? st} · {pname ?? 'projekt nélkül'} · {d.type} · P{d.priority} · létrehozta: {d.created_by ?? ''}</div>
        {edit ? (
          <div className="mt-2">
            <input className="inp font-semibold" aria-label="Cím" value={edit.title} onChange={e => setEdit({ ...edit, title: e.target.value })} />
            <select className="inp mt-2 !w-auto" aria-label="Típus" value={edit.type} onChange={e => setEdit({ ...edit, type: e.target.value })}>
              {[...new Set([...TYPES, edit.type])].map(t => <option key={t}>{t}</option>)}</select>
            <textarea className="inp mt-2" aria-label="Leírás" rows={6} value={edit.description} onChange={e => setEdit({ ...edit, description: e.target.value })} />
            <div className="flex gap-2 mt-2"><button className="btn pri" onClick={saveEdit}>Mentés</button><button className="btn" onClick={() => setEdit(null)}>Mégse</button></div>
          </div>
        ) : (
          <>
            <h2 className="text-lg font-semibold m-0 mt-1">{d.title}</h2>
            <pre className="code" style={{ maxHeight: '25vh' }}>{d.description || '(nincs leírás)'}</pre>
            {!final && <button className="btn sm" onClick={() => setEdit({ title: d.title, description: d.description, type: d.type })}>Szerkesztés</button>}
          </>
        )}
        {d.source_url && <div className="mt-1"><a href={d.source_url} target="_blank" rel="noreferrer">Forrás megnyitása</a></div>}
        {d.source_refs.length > 0 && <div className="mt-1 text-[12px] text-mut">Összevont források: {d.source_refs.map(r => (
          <span key={r.id} className="mr-2">{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.source_id} {r.external_id}</a> : `${r.source_id} ${r.external_id}`}{r.merged_from_ticket_id && ` (#${r.merged_from_ticket_id})`}</span>))}</div>}
        <div className="flex flex-wrap gap-2 mt-2 items-center">
          {['READY', 'NEW', 'NEEDS_TRIAGE'].includes(st) && (
            <label className="flex gap-1 items-center"><input type="checkbox" checked={d.start_approved} onChange={e => run(() => post(`/api/tickets/${id}/go`, { value: e.target.checked })).then(reload)} /> Mehet
              {d.start_approved_by === 'coordinator' && <b className="text-warn" title={d.start_approved_reason ?? ''}> · Coordinator pipálta: {d.start_approved_reason}</b>}</label>)}
        </div>
      </div>

      {d.last_error && <Sec t="Utolsó hiba"><pre className="code" style={{ color: 'var(--bad)' }}>{d.last_error}</pre></Sec>}
      {d.result_summary && <Sec t="Eredmény"><pre className="code">{d.result_summary}</pre></Sec>}

      {/* státuszgombok */}
      {!final && (
        <Sec t="Műveletek">
          <div className="flex flex-wrap gap-2 items-center">
            {(st === 'NEEDS_TRIAGE' || st === 'NEW') && <>
              <select className="inp !w-auto" aria-label="Projekt" value={triageProj || d.project_id || ''} onChange={e => setTriageProj(e.target.value)}>
                <option value="">— projekt —</option>{S.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
              <button className="btn ok" disabled={!(triageProj || d.project_id)} onClick={() => tr('triage', 'Besorolva', { project_id: triageProj || d.project_id })}>Besorol (Indítható)</button></>}
            {st === 'REVIEW' && <>
              <button className="btn ok" onClick={() => tr('accept', 'Elfogadva')}>Elfogad</button>
              <button className="btn" disabled={!note.trim()} title={note.trim() ? '' : 'A visszaküldéshez megjegyzés kell'} onClick={() => tr('return', 'Visszaküldve')}>Visszaküld</button></>}
            {st === 'FAILED' && <button className="btn ok" onClick={() => tr('retry', 'Újrapróbálás indul')}>Újrapróbál</button>}
            <button className="btn bad" onClick={async () => { if (await confirm(`Megszakítod a(z) #${id} feladatot?`)) tr('cancel', 'Megszakítva') }}>Megszakít</button>
          </div>
          {st === 'REVIEW' && <input className="inp mt-2" placeholder="Megjegyzés (visszaküldéshez kötelező)" value={note} onChange={e => setNote(e.target.value)} />}
        </Sec>)}

      {d.questions.length > 0 && <Sec t="Kérdések">{d.questions.map(q => <Q key={q.id} q={q} onDone={reload} />)}</Sec>}
      {d.actions.length > 0 && <Sec t="Kapcsolódó műveletek">{d.actions.map(a => <ActionCard key={a.id} a={a} />)}</Sec>}
      {d.runs.length > 0 && (
        <Sec t="Futások">
          <table className="w-full text-[12px]"><thead><tr className="text-left text-mut"><th>Modell</th><th>Token (be/ki)</th><th>Költség</th><th>Eredmény</th><th>Kezdés</th></tr></thead>
            <tbody>{d.runs.map(r => (
              <tr key={r.id}><td>{r.model ?? '—'}</td><td>{num(r.tokens_in)} / {num(r.tokens_out)}</td>
                <td>{r.estimated_cost == null ? '—' : '$' + r.estimated_cost.toFixed(3)} <span className="text-mut">({r.cost_source ?? 'ismeretlen'})</span></td>
                <td>{r.outcome ?? '—'}</td><td>{hm(r.started_at)}</td></tr>))}</tbody></table>
        </Sec>)}
      <Sec t="Eseménytörténet">
        <div className="text-[12px] flex flex-col gap-0.5">
          {[...d.events].reverse().map(e => (
            <div key={e.id}><span className="text-mut">{hm(e.created_at)}</span> <b>{e.event_type}</b>
              {e.new_status && <span> {e.old_status ? e.old_status + ' → ' : ''}{e.new_status}</span>} <span className="text-mut">({e.actor_type}{e.actor_id ? ':' + e.actor_id : ''})</span>
              {e.payload?.note && <span> – {String(e.payload.note)}</span>}{e.payload?.reason && <span> – {String(e.payload.reason)}</span>}</div>))}
        </div>
        <div className="text-mut text-[11px] mt-1">Frissítve: {ago(d.updated_at)}</div>
      </Sec>
    </Dialog>
  )
}

const Sec = ({ t, children }: { t: string; children: React.ReactNode }) => (
  <section className="mt-3 pt-2 border-t border-dashed border-line"><h3 className="text-[11px] uppercase tracking-wide text-mut m-0 mb-1 font-semibold">{t}</h3>{children}</section>)

function Q({ q, onDone }: { q: Question; onDone: () => void }) {
  const run = useRun()
  const [text, setText] = useState('')
  const send = async (answer: string) => { if (await run(() => post(`/api/questions/${q.id}/answer`, { answer }), 'Válasz elküldve')) onDone() }
  return (
    <div className="mb-2">
      <div className="font-semibold">{q.question}</div>
      {q.context && <div className="text-[12px] text-mut whitespace-pre-wrap">{q.context}</div>}
      {q.status === 'OPEN' ? (
        <>
          {q.suggested_answer && (
            <div className="my-1.5 p-2 rounded-md flex flex-col gap-1.5 items-start" style={{ background: 'color-mix(in srgb,var(--ok) 12%,transparent)' }}>
              <span><b>Javaslat:</b> {q.suggested_answer}</span>
              <button className="btn ok" onClick={() => send(q.suggested_answer!)}>Javaslat elfogadása</button></div>)}
          <textarea className="inp" rows={2} placeholder="Saját válasz…" value={text} onChange={e => setText(e.target.value)} />
          <button className="btn mt-1" disabled={!text.trim()} onClick={() => send(text.trim())}>Válaszolok</button>
        </>
      ) : (q.answers ?? []).map((a, i) => <div key={i} className="text-[12px] text-mut">✓ <i>{a.answer}</i> ({a.created_by}, {hm(a.created_at)})</div>)}
    </div>)
}
