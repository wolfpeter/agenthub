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
  const desc = parseDesc(d.description || '')
  // a forrás legutóbbi állapota (a migrált ticketnél a leírás helyi, a kártya szövege csak itt látszik)
  const src = [...d.events].reverse().find(e => e.event_type === 'source_update')?.payload
  const list = src?.list ?? desc.list, labels = src?.labels ?? desc.labels
  const srcComments = splitComments(src?.comments ?? desc.comments)
  const srcDesc = src?.kept_local?.includes('description') ? src?.description : null
  const openQs = d.questions.filter(q => q.status === 'OPEN')
  return (
    <Dialog onClose={onClose} wide>
      <div className="pr-12">
        <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
          <span className="text-mut font-mono">#{d.id}</span>
          <Chip c={stColor(st)}>{STATUS_HU[st] ?? st}</Chip>
          <Chip>{pname ?? 'projekt nélkül'}</Chip>
          <Chip>{TYPE_HU[d.type] ?? d.type}</Chip>
          <Chip c={`var(--p${d.priority})`}>P{d.priority}</Chip>
          {d.start_approved && <Chip c="var(--ok)">Mehet</Chip>}
          {d.hotfix && <Chip c="var(--bad)">HOTFIX</Chip>}
          {list && <Chip>{list}</Chip>}
          {labels && String(labels).split(', ').map(l => <Chip key={l} c={/hiba/i.test(l) ? 'var(--bad)' : undefined}>{l}</Chip>)}
          {d.source_url && <a className="ml-1" href={d.source_url} target="_blank" rel="noreferrer">Forrás ↗</a>}
        </div>
        {edit ? (
          <div className="mt-2">
            <input className="inp font-semibold" aria-label="Cím" value={edit.title} onChange={e => setEdit({ ...edit, title: e.target.value })} />
            <select className="inp mt-2 !w-auto" aria-label="Típus" value={edit.type} onChange={e => setEdit({ ...edit, type: e.target.value })}>
              {[...new Set([...TYPES, edit.type])].map(t => <option key={t}>{t}</option>)}</select>
            <textarea className="inp mt-2" aria-label="Leírás" rows={10} value={edit.description} onChange={e => setEdit({ ...edit, description: e.target.value })} />
            <div className="flex gap-2 mt-2"><button className="btn pri" onClick={saveEdit}>Mentés</button><button className="btn" onClick={() => setEdit(null)}>Mégse</button></div>
          </div>
        ) : (
          <h2 className="text-xl font-semibold m-0 mt-2 leading-snug">{d.title}</h2>
        )}
        <div className="text-mut text-[12px] mt-1">Létrehozta: {ACTOR_HU[d.created_by ?? ''] ?? d.created_by} · {hm(d.created_at)} · frissítve {ago(d.updated_at)}</div>
      </div>

      {openQs.length > 0 && <Sec t={`Rád vár (${openQs.length})`} hl>{openQs.map(q => <Q key={q.id} q={q} onDone={reload} />)}</Sec>}

      {/* döntések és státuszgombok egy helyen */}
      {!final && (
        <Sec t="Döntés">
          <div className="flex flex-wrap gap-3 items-center">
            {['READY', 'NEW', 'NEEDS_TRIAGE'].includes(st) && (
              <label className="flex gap-1 items-center"><input type="checkbox" checked={d.start_approved} onChange={e => run(() => post(`/api/tickets/${id}/go`, { value: e.target.checked })).then(reload)} /> Mehet
                {d.start_approved_by === 'coordinator' && <b className="text-warn" title={d.start_approved_reason ?? ''}> · Coordinator pipálta: {d.start_approved_reason}</b>}</label>)}
            <label className="flex gap-1 items-center" title="Élesítési engedély erre a szállításra: a worker elsőként veszi fel, és teszt után azonnal kiadja">
              <input type="checkbox" checked={d.hotfix} onChange={e => run(() => post(`/api/tickets/${id}/hotfix`, { value: e.target.checked })).then(reload)} /> Hotfix
              {d.hotfix && d.hotfix_by && <span className="text-mut"> · {d.hotfix_by}</span>}</label>
            <span className="flex-1" />
            {(st === 'NEEDS_TRIAGE' || st === 'NEW') && <>
              <select className="inp !w-auto" aria-label="Projekt" value={triageProj || d.project_id || ''} onChange={e => setTriageProj(e.target.value)}>
                <option value="">— projekt —</option>{S.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
              <button className="btn ok" disabled={!(triageProj || d.project_id)} onClick={() => tr('triage', 'Besorolva', { project_id: triageProj || d.project_id })}>Besorol (Indítható)</button></>}
            {(st === 'REVIEW' || st === 'AWAITING_RELEASE') && <>
              <button className="btn ok" onClick={() => tr('accept', 'Elfogadva')}>Elfogad</button>
              <button className="btn" disabled={!note.trim()} title={note.trim() ? '' : 'A visszaküldéshez megjegyzés kell'} onClick={() => tr('return', 'Visszaküldve')}>Visszaküld</button></>}
            {st === 'FAILED' && <button className="btn ok" onClick={() => tr('retry', 'Újrapróbálás indul')}>Újrapróbál</button>}
            {st === 'BLOCKED' && <button className="btn ok" onClick={() => tr('retry', 'Feloldva')}>Feloldva (Indítható)</button>}
            {!edit && <button className="btn" onClick={() => setEdit({ title: d.title, description: d.description, type: d.type })}>Szerkesztés</button>}
            <button className="btn bad" onClick={async () => { if (await confirm(`Megszakítod a(z) #${id} feladatot?`)) tr('cancel', 'Megszakítva') }}>Megszakít</button>
          </div>
          {(st === 'REVIEW' || st === 'AWAITING_RELEASE') && <input className="inp mt-2" placeholder="Megjegyzés (visszaküldéshez kötelező)" value={note} onChange={e => setNote(e.target.value)} />}
        </Sec>)}

      {d.last_error && <Sec t="Utolsó hiba"><div className="txt" style={{ color: 'var(--bad)' }}><Text s={d.last_error} /></div></Sec>}
      {d.result_summary && <Sec t="Eredmény"><div className="txt"><Text s={d.result_summary} /></div></Sec>}

      <Sec t="Leírás">
        <div className="txt" style={{ maxHeight: '40vh' }}>{desc.body ? <Text s={desc.body} /> : <span className="text-mut">(nincs leírás)</span>}</div>
        {desc.ref && <div className="text-mut text-[12px] mt-1">Eredeti hivatkozás: {desc.ref}</div>}
      </Sec>
      {srcDesc && <Sec t="A kártya mostani leírása (a forrásban)"><div className="txt" style={{ maxHeight: '30vh' }}><Text s={srcDesc} /></div></Sec>}
      {srcComments.length > 0 && (
        <Sec t={`Kommentek a forrásban (${srcComments.length})`}>
          <div className="flex flex-col gap-2 overflow-auto" style={{ maxHeight: '45vh' }}>
            {srcComments.map((c, i) => (
              <div key={i} className="txt !max-h-none" style={{ borderLeft: `3px solid ${/wolf/i.test(c.who) ? 'var(--acc)' : 'var(--warn)'}` }}>
                <div className="text-[12px] mb-0.5"><b>{c.who}</b> <span className="text-mut">{hm(c.at)}</span></div><Text s={c.text} /></div>))}
          </div>
        </Sec>)}
      {desc.notes.length > 0 && (
        <Sec t={`Munkajegyzetek (${desc.notes.length})`}>
          <details open={desc.notes.length <= 4}><summary className="cursor-pointer text-[12px] text-mut">megjelenítés</summary>
            <div className="flex flex-col gap-1.5 mt-1">{desc.notes.map((n, i) => (
              <div key={i} className="text-[13px]"><span className="text-mut text-[12px]">{n.at} · {n.who}</span><div style={{ whiteSpace: 'pre-wrap' }}><Text s={n.text} /></div></div>))}</div>
          </details>
        </Sec>)}
      {d.source_refs.length > 0 && <Sec t="Összevont források"><div className="text-[13px] flex flex-wrap gap-3">{d.source_refs.map(r => (
        <span key={r.id}>{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.source_id} {r.external_id}</a> : `${r.source_id} ${r.external_id}`}{r.merged_from_ticket_id && <span className="text-mut"> (#{r.merged_from_ticket_id})</span>}</span>))}</div></Sec>}

      {d.questions.length > openQs.length && <Sec t="Megválaszolt kérdések">{d.questions.filter(q => q.status !== 'OPEN').map(q => <Q key={q.id} q={q} onDone={reload} />)}</Sec>}
      {d.actions.length > 0 && <Sec t="Kapcsolódó műveletek">{d.actions.map(a => <ActionCard key={a.id} a={a} />)}</Sec>}
      {d.runs.length > 0 && (
        <Sec t="Futások">
          <table className="w-full text-[12px]"><thead><tr className="text-left text-mut"><th>Modell</th><th>Token (be/ki)</th><th>Költség</th><th>Eredmény</th><th>Kezdés</th></tr></thead>
            <tbody>{d.runs.map(r => (
              <tr key={r.id}><td>{r.model ?? '—'}</td><td>{num(r.tokens_in)} / {num(r.tokens_out)}</td>
                <td>{r.estimated_cost == null ? '—' : '$' + r.estimated_cost.toFixed(3)} <span className="text-mut">({r.cost_source ?? 'ismeretlen'})</span></td>
                <td>{r.outcome ?? '—'}</td><td>{hm(r.started_at)}</td></tr>))}</tbody></table>
        </Sec>)}
      <History events={d.events} />
    </Dialog>
  )
}

const TYPE_HU: Record<string, string> = { bug: 'hiba', feature: 'fejlesztés', task: 'feladat', question: 'kérdés' }
const ACTOR_HU: Record<string, string> = { human: 'ember', coordinator: 'Coordinator', worker: 'Worker', system: 'rendszer' }
const EV_HU: Record<string, string> = {
  created: 'Létrehozva', classified: 'Besorolva', routed: 'Szabály szerint besorolva', triaged: 'Besorolva', note: 'Megjegyzés',
  legacy: 'Régi napló', migrated: 'Átköltöztetve', status_fix: 'Állapot javítva', source_update: 'A forrás változott',
  possible_duplicate: 'Lehetséges duplikátum', merged_in: 'Ide összevonva', merged: 'Összevonva', go_on: 'Mehet bekapcsolva',
  go_off: 'Mehet kikapcsolva', go_restored: 'Mehet visszaállítva', hotfix_on: 'Hotfix bekapcsolva', hotfix_off: 'Hotfix kikapcsolva',
  claimed: 'A Worker felvette', edited: 'Szerkesztve', answered: 'Megválaszolva', dismissed: 'Elvetve', reopened: 'Újranyitva',
  question: 'Kérdés', answer: 'Válasz', action_proposed: 'Művelet javasolva', blocked: 'Blokkolva', released: 'Kiadva',
  release_failed: 'Kiadás sikertelen', returned: 'Visszaküldve', failed: 'Hiba', retry: 'Újrapróba', auto_retry: 'Automatikus újrapróba',
  awaiting_approval: 'Jóváhagyásra vár', actions_settled: 'Műveletek lezárva', finished: 'Kész', accepted: 'Elfogadva',
}
const stColor = (st: string) => ({ READY: 'var(--ok)', IN_PROGRESS: 'var(--acc)', WAITING_FOR_INPUT: 'var(--warn)', PENDING_APPROVAL: 'var(--warn)',
  NEEDS_TRIAGE: 'var(--warn)', NEW: 'var(--warn)', BLOCKED: 'var(--bad)', FAILED: 'var(--bad)', AWAITING_RELEASE: 'var(--acc)', REVIEW: 'var(--acc)' } as Record<string, string>)[st]

const Chip = ({ c, children }: { c?: string; children: React.ReactNode }) => (
  <span className="tag !text-[12px] !px-1.5 !py-px" style={c ? { color: c, borderColor: c } : undefined}>{children}</span>)

// URL-ek kattinthatók, a markdown-kép egy rövid „kép” link
function Text({ s }: { s: string }) {
  const t = s.replace(/[\u200b-\u200d\ufeff]/g, '').replace(/^#{1,6}\s+/gm, '').replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/\n{3,}/g, '\n\n')
    .replace(/!\[[^\]]*\]\((\S+?)(?:\s+"[^"]*")?\)/g, ' $1 ').replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)(?:\s+"[^"]*")?\)/g, '$1 ($2)')
  return <>{t.split(/(https?:\/\/[^\s)"\]>]+)/).map((p, i) => i % 2
    ? <a key={i} href={p} target="_blank" rel="noreferrer">{/\.(png|jpe?g|webp|gif)|\/download\//i.test(p) ? '[kép]' : p.replace(/^https?:\/\//, '').slice(0, 60) + (p.length > 68 ? '…' : '')}</a>
    : p)}</>
}

// a leírás részei: [Lista · Címkék] fej, „Eredeti hivatkozás”, a szövegbe írt [idő szerző] jegyzetek, a forrás kommentjei
function parseDesc(s: string) {
  let body = s, list: string | undefined, labels: string | undefined, ref: string | undefined, comments: string | undefined
  const m = body.match(/^\[([^\]\n]*(?:Lista|Címkék):[^\]\n]*)\]\s*/)
  if (m) {
    for (const part of m[1].split(' · ')) { const [k, ...v] = part.split(': '); if (k === 'Lista') list = v.join(': '); if (k === 'Címkék') labels = v.join(': ') }
    body = body.slice(m[0].length)
  }
  const ci = body.search(/(?:^|\n\n)---\nKommentek a forrásban:\n/)
  if (ci >= 0) { comments = body.slice(ci).replace(/^\n*---\nKommentek a forrásban:\n/, ''); body = body.slice(0, ci) }
  const r = body.match(/^Eredeti hivatkozás: ([^\n]*)\n*/)
  if (r) { ref = r[1] === '-' ? undefined : r[1]; body = body.slice(r[0].length) }
  const parts = body.split(/\n*(?=\[\d\d-\d\dT\d\d:\d\d [^\]\n]+\] )/)
  const notes: { at: string; who: string; text: string }[] = []
  let text = ''
  for (const p of parts) {
    const n = p.match(/^\[(\d\d-\d\d)T(\d\d:\d\d) ([^\]\n]+)\] ([\s\S]*)$/)
    if (n) notes.push({ at: `${n[1].replace('-', '. ')}. ${n[2]}`, who: n[3], text: n[4].trim() })
    else text += p
  }
  return { body: text.trim(), list, labels, ref, comments, notes }
}

function splitComments(s?: string | null) {
  if (!s) return []
  return s.split(/\n\n(?=[^\n]+ \(\d{4}-\d\d-\d\dT[^)]*\):\n)/).map(c => {
    const m = c.match(/^([^\n]+) \((\d{4}-\d\d-\d\dT[^)]*)\):\n([\s\S]*)$/)
    return m ? { who: m[1], at: m[2], text: m[3].trim() } : { who: '?', at: '', text: c.trim() }
  })
}

function evText(e: Ev): string {
  const p = e.payload || {}
  if (e.event_type === 'source_update') return [p.list_from && `lista: ${p.list_from} → ${p.list}`, p.labels_from !== undefined && `címkék: ${p.labels ?? '—'}`,
    p.comments && 'új komment', p.thread_reply && 'válaszlevél', p.applied?.length && `frissítve: ${p.applied.join(', ')}`].filter(Boolean).join(' · ')
  if (e.event_type === 'merged_in') return `#${p.from} ${p.title ?? ''}`
  if (e.event_type === 'merged') return `→ #${p.into}`
  if (e.event_type === 'possible_duplicate') return (p.similar || []).map((x: any) => `#${x.id}`).join(', ')
  if (e.event_type === 'edited') return Object.entries(p).map(([k, v]) => `${k}: ${v}`).join(', ')
  return String(p.text ?? p.what ?? p.reason ?? p.summary ?? p.note ?? p.question ?? p.answer ?? (p.project ? `projekt: ${p.project}` : ''))
}

function History({ events }: { events: Ev[] }) {
  const [all, setAll] = useState(false)
  const ev = [...events].reverse()
  const shown = all ? ev : ev.slice(0, 15)
  return (
    <Sec t={`Eseménytörténet (${ev.length})`}>
      <div className="text-[12px] flex flex-col gap-1">
        {shown.map(e => {
          const txt = evText(e)
          return (
            <div key={e.id} className="grid gap-x-2" style={{ gridTemplateColumns: '6.5rem 1fr' }}>
              <span className="text-mut">{hm(e.created_at)}</span>
              <div><b>{EV_HU[e.event_type] ?? e.event_type}</b>
                {e.new_status && <span> · {e.old_status ? (STATUS_HU[e.old_status] ?? e.old_status) + ' → ' : ''}{STATUS_HU[e.new_status] ?? e.new_status}</span>}
                <span className="text-mut"> · {ACTOR_HU[e.actor_type] ?? e.actor_type}</span>
                {txt && <div className="text-[12px]" style={{ whiteSpace: 'pre-wrap' }}>{txt.length > 400 ? txt.slice(0, 400) + '…' : txt}</div>}</div>
            </div>)
        })}
      </div>
      {ev.length > 15 && <button className="btn sm mt-1" onClick={() => setAll(!all)}>{all ? 'Kevesebb' : `Mind (${ev.length})`}</button>}
    </Sec>)
}

const Sec = ({ t, children, hl }: { t: string; children: React.ReactNode; hl?: boolean }) => (
  <section className={'mt-3 pt-2 border-t border-line' + (hl ? ' rounded-md p-2 border' : '')} style={hl ? { borderColor: 'var(--warn)', background: 'color-mix(in srgb,var(--warn) 8%,transparent)' } : undefined}>
    <h3 className="text-[12px] uppercase tracking-wide text-mut m-0 mb-1.5 font-semibold" style={hl ? { color: 'var(--warn)' } : undefined}>{t}</h3>{children}</section>)

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
