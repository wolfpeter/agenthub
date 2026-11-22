import { useEffect, useState, type ReactNode } from 'react'
import { ACTOR_HU, EV_HU, FINAL, STATUS_HU, TYPE_HU, ago, get, hm, num, patch, post, stColor, type Action, type Ev, type Question, type State, type Ticket } from './api'
import { Dialog, Skel, useRun, useUI } from './ui'
import { ActionCard } from './Approval'
import { TYPES } from './NewTicket'

interface Ref { id: number; source_id: string; external_id: string; url: string | null; merged_from_ticket_id: number | null }
interface Detail extends Ticket { events: Ev[]; questions: Question[]; actions: Action[]; runs: any[]; source_refs: Ref[]; comments: { text: string; at: string }[] }

export default function TicketDialog({ id, S, onClose }: { id: number; S: State; onClose: () => void }) {
  const run = useRun()
  const [d, setD] = useState<Detail | null>(null)
  const [edit, setEdit] = useState<{ title: string; description: string; type: string } | null>(null)
  const [tab, setTab] = useState('')
  const reload = () => get<Detail>(`/api/tickets/${id}`).then(setD).catch(e => run(() => Promise.reject(e)))
  useEffect(() => { reload() }, [id, S.now]) // eslint-disable-line react-hooks/exhaustive-deps
  const saveEdit = async () => { if (edit && await run(() => patch(`/api/tickets/${id}`, edit), 'Mentve')) { setEdit(null); reload() } }

  if (!d) return <Dialog onClose={onClose} wide><div className="mt-6"><Skel h={120} /></div></Dialog>
  const st = d.status
  const pname = S.projects.find(p => p.id === d.project_id)?.name
  const desc = parseDesc(d.description || '')
  // a forrás legutóbbi állapota (a migrált ticketnél a leírás helyi, a kártya szövege csak itt látszik)
  const src = [...d.events].reverse().find(e => e.event_type === 'source_update')?.payload
  const list = src?.list ?? desc.list, labels = src?.labels ?? desc.labels
  const srcComments = splitComments(src?.comments ?? desc.comments)
  const srcDesc = src?.kept_local?.includes('description') ? src?.description?.trim() : null
  const openQs = d.questions.filter(q => q.status === 'OPEN')
  const doneQs = d.questions.filter(q => q.status !== 'OPEN')
  const mails = d.actions.filter(a => a.target_system === 'email' && ['PENDING_APPROVAL', 'APPROVED', 'FAILED'].includes(a.status))
  const tabs: [string, string, number | null][] = [
    ['desc', 'Leírás', null], ['comments', 'Kommentek a forrásban', srcComments.length], ['notes', 'Munkajegyzetek', desc.notes.length],
    ['qs', 'Megválaszolt kérdések', doneQs.length], ['actions', 'Műveletek', d.actions.length], ['history', 'Előzmények', d.events.length],
    ['runs', 'Futások', d.runs.length]]
  const shown = tabs.filter(([k, , n]) => k === 'desc' || k === 'history' || (n ?? 0) > 0)
  // üres leírásnál az első tartalmas fül nyílik (pl. a munkajegyzetek)
  const def = srcDesc || desc.body ? 'desc' : shown.find(([k, , n]) => k !== 'history' && (n ?? 0) > 0)?.[0] ?? 'desc'
  const cur = shown.some(([k]) => k === tab) ? tab : def
  return (
    <Dialog onClose={onClose} wide>
      {/* fej: azonosító, cím, a legfontosabb jellemzők */}
      <div className="pr-12">
        {edit ? (
          <div>
            <input className="inp font-semibold" aria-label="Cím" value={edit.title} onChange={e => setEdit({ ...edit, title: e.target.value })} />
            <select className="inp mt-2 !w-auto" aria-label="Típus" value={edit.type} onChange={e => setEdit({ ...edit, type: e.target.value })}>
              {[...new Set([...TYPES, edit.type])].map(t => <option key={t}>{t}</option>)}</select>
            <textarea className="inp mt-2" aria-label="Leírás" rows={10} value={edit.description} onChange={e => setEdit({ ...edit, description: e.target.value })} />
            <div className="flex gap-2 mt-2"><button className="btn pri" onClick={saveEdit}>Mentés</button><button className="btn" onClick={() => setEdit(null)}>Mégse</button></div>
          </div>
        ) : (
          <h2 className="text-xl font-semibold m-0 leading-snug"><span className="text-mut font-normal mr-2">#{d.id}</span>{d.title}</h2>
        )}
        <div className="flex flex-wrap gap-1.5 items-center text-[12px] mt-2">
          <Chip c={stColor(st)} strong>{STATUS_HU[st] ?? st}</Chip>
          <Chip>{pname ?? 'projekt nélkül'}</Chip>
          <Chip>{TYPE_HU[d.type] ?? d.type}</Chip>
          <Chip c={`var(--p${d.priority})`}>P{d.priority}</Chip>
          {d.start_approved && <Chip c="var(--ok)">Mehet</Chip>}
          {d.hotfix && <Chip c="var(--bad)">HOTFIX</Chip>}
          {list && <Chip><span className="text-mut">Trello-lista:</span> {list}</Chip>}
          {labels && String(labels).split(', ').map(l => <Chip key={l} c={/hiba/i.test(l) ? 'var(--bad)' : undefined}>{l}</Chip>)}
          {d.source_url && <a className="ml-1" href={d.source_url} target="_blank" rel="noreferrer">Megnyitás a forrásban ↗</a>}
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-4 mt-3">
        {/* bal: amit olvasni kell */}
        <div className="flex-1 min-w-0">
          {mails.length > 0 && <Box t={`Elküldendő levél (${mails.length})`} c="var(--warn)">
            <div className="text-[12px] text-mut mb-1">Másold ki, küldd el a leveleződből, majd nyomd meg az „Elküldtem” gombot.</div>
            {mails.map(a => <ActionCard key={a.id} a={a} emailWriter={S.controls.email_writer} />)}</Box>}
          {openQs.length > 0 && <Box t={`Rád vár (${openQs.length})`} c="var(--warn)">{openQs.map(q => <Q key={q.id} q={q} onDone={reload} />)}</Box>}
          {d.last_error && <Box t={st === 'BLOCKED' ? 'Mire vár' : 'Utolsó hiba'} c="var(--bad)"><Text s={d.last_error} /></Box>}
          {d.result_summary && <Box t="Eredmény" c="var(--ok)"><Text s={d.result_summary} /></Box>}
          <div className="flex flex-wrap gap-1 border-b border-line mb-2" role="tablist">
            {shown.map(([k, t, n]) => (
              <button key={k} role="tab" aria-selected={cur === k} onClick={() => setTab(k)}
                className={'px-2.5 py-1 text-[13px] -mb-px border-b-2 ' + (cur === k ? 'border-acc text-fg font-semibold' : 'border-transparent text-mut hover:text-fg')}>
                {t}{n ? <span className="text-mut font-normal"> {n}</span> : null}</button>))}
          </div>
          {cur === 'desc' && <>
            <div className="txt" style={{ maxHeight: '55vh' }}>{(srcDesc || desc.body) ? <Text s={srcDesc || desc.body} /> : <span className="text-mut">(nincs leírás)</span>}</div>
            {srcDesc && desc.body && desc.body !== srcDesc && (
              <details className="mt-2"><summary className="cursor-pointer text-[12px] text-mut">Helyi (régebbi) leírás</summary>
                <div className="txt mt-1"><Text s={desc.body} /></div></details>)}
          </>}
          {cur === 'comments' && <div className="flex flex-col gap-2">
            {srcComments.map((c, i) => (
              <div key={i} className="txt !max-h-none" style={{ borderLeft: `3px solid ${/wolf/i.test(c.who) ? 'var(--acc)' : 'var(--warn)'}` }}>
                <div className="text-[12px] mb-0.5"><b>{c.who}</b> <span className="text-mut">{hm(c.at)}</span></div><Text s={c.text} /></div>))}</div>}
          {cur === 'notes' && <div className="flex flex-col gap-2">{[...desc.notes].reverse().map((n, i) => (
            <div key={i} className="text-[13px]"><span className="text-mut text-[12px]">{n.at} · {n.who}</span><div className="whitespace-pre-wrap break-words"><Text s={n.text} /></div></div>))}</div>}
          {cur === 'qs' && doneQs.map(q => <Q key={q.id} q={q} onDone={reload} />)}
          {cur === 'actions' && d.actions.map(a => <ActionCard key={a.id} a={a} emailWriter={S.controls.email_writer} />)}
          {cur === 'history' && <History events={d.events} />}
          {cur === 'runs' && (
            <table className="w-full text-[12px]"><thead><tr className="text-left text-mut"><th>Modell</th><th>Token (be/ki)</th><th>Költség</th><th>Eredmény</th><th>Kezdés</th></tr></thead>
              <tbody>{d.runs.map(r => (
                <tr key={r.id}><td>{r.model ?? '—'}</td><td>{num(r.tokens_in)} / {num(r.tokens_out)}</td>
                  <td>{r.estimated_cost == null ? '—' : '$' + r.estimated_cost.toFixed(3)} <span className="text-mut">({r.cost_source ?? 'ismeretlen'})</span></td>
                  <td>{r.outcome ?? '—'}</td><td>{hm(r.started_at)}</td></tr>))}</tbody></table>)}
        </div>

        {/* jobb: döntés és megjegyzés */}
        <aside className="md:w-[300px] flex-none flex flex-col gap-3">
          <Decide d={d} S={S} reload={reload} onEdit={() => setEdit({ title: d.title, description: d.description, type: d.type })} editing={!!edit} />
          <Comments d={d} reload={reload} />
          <div className="text-mut text-[12px] flex flex-col gap-0.5">
            <span>Létrehozta: {ACTOR_HU[d.created_by ?? ''] ?? d.created_by}, {hm(d.created_at)}</span>
            <span>Frissítve: {ago(d.updated_at)}</span>
            {desc.ref && <span className="break-all">Eredeti hivatkozás: {desc.ref}</span>}
            {d.source_refs.length > 0 && <span>Összevont források: {d.source_refs.map(r => (
              <span key={r.id} className="mr-2">{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.source_id}</a> : r.source_id}{r.merged_from_ticket_id && ` (#${r.merged_from_ticket_id})`}</span>))}</span>}
          </div>
        </aside>
      </div>
    </Dialog>
  )
}

// döntések és státuszgombok egy helyen
function Decide({ d, S, reload, onEdit, editing }: { d: Detail; S: State; reload: () => void; onEdit: () => void; editing: boolean }) {
  const run = useRun()
  const { confirm } = useUI()
  const [note, setNote] = useState('')
  const [triageProj, setTriageProj] = useState('')
  const st = d.status, id = d.id
  const tr = async (action: string, ok: string, extra: object = {}) => {
    if (await run(() => post(`/api/tickets/${id}/transition`, { action, note: note || undefined, ...extra }), ok)) { setNote(''); reload() }
  }
  if (FINAL.includes(st)) return null
  return (
    <Box t="Döntés">
      <div className="flex flex-col gap-2">
        {['READY', 'NEW', 'NEEDS_TRIAGE'].includes(st) && (
          <label className="flex gap-1.5 items-start"><input className="mt-1" type="checkbox" checked={d.start_approved} onChange={e => run(() => post(`/api/tickets/${id}/go`, { value: e.target.checked })).then(reload)} />
            <span><b>Mehet</b> <span className="text-mut text-[12px]">— a Worker elkezdheti</span>
              {d.start_approved_by === 'coordinator' && <span className="block text-warn text-[12px]">Coordinator pipálta: {d.start_approved_reason}</span>}</span></label>)}
        <label className="flex gap-1.5 items-start" title="Élesítési engedély erre a szállításra: a worker elsőként veszi fel, és teszt után azonnal kiadja">
          <input className="mt-1" type="checkbox" checked={d.hotfix} onChange={e => run(() => post(`/api/tickets/${id}/hotfix`, { value: e.target.checked })).then(reload)} />
          <span><b>Hotfix</b> <span className="text-mut text-[12px]">— teszt után azonnal élesítheti</span>{d.hotfix && d.hotfix_by && <span className="text-mut text-[12px]"> ({d.hotfix_by})</span>}</span></label>
        {(st === 'NEEDS_TRIAGE' || st === 'NEW') && <div className="flex gap-1.5">
          <select className="inp" aria-label="Projekt" value={triageProj || d.project_id || ''} onChange={e => setTriageProj(e.target.value)}>
            <option value="">— projekt —</option>{S.projects.filter(p => p.is_active).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          <button className="btn ok whitespace-nowrap" disabled={!(triageProj || d.project_id)} onClick={() => tr('triage', 'Besorolva', { project_id: triageProj || d.project_id })}>Besorol</button></div>}
        {(st === 'REVIEW' || st === 'AWAITING_RELEASE') && <>
          <input className="inp" placeholder="Megjegyzés (visszaküldéshez kötelező)" value={note} onChange={e => setNote(e.target.value)} />
          <div className="flex gap-1.5"><button className="btn ok" onClick={() => tr('accept', 'Elfogadva')}>Elfogad</button>
            <button className="btn" disabled={!note.trim()} title={note.trim() ? '' : 'A visszaküldéshez megjegyzés kell'} onClick={() => tr('return', 'Visszaküldve')}>Visszaküld</button></div></>}
        {st === 'FAILED' && <button className="btn ok" onClick={() => tr('retry', 'Újrapróbálás indul')}>Újrapróbál</button>}
        {st === 'BLOCKED' && <button className="btn ok" onClick={() => tr('retry', 'Feloldva')}>Feloldva (Indítható)</button>}
        <div className="flex gap-1.5 pt-1 border-t border-line">
          {!editing && <button className="btn" onClick={onEdit}>Szerkesztés</button>}
          <button className="btn bad ml-auto" onClick={async () => { if (await confirm(`Megszakítod a(z) #${id} feladatot?`)) tr('cancel', 'Megszakítva') }}>Megszakít</button></div>
      </div>
    </Box>)
}

// Peter megjegyzései: a Worker utasításként kapja (a ticket állapotát nem változtatja)
function Comments({ d, reload }: { d: Detail; reload: () => void }) {
  const run = useRun()
  const [text, setText] = useState('')
  const add = async () => { if (await run(() => post(`/api/tickets/${d.id}/comment`, { text }), 'Megjegyzés hozzáadva')) { setText(''); reload() } }
  return (
    <Box t={`Megjegyzés a Workernek${d.comments.length ? ` (${d.comments.length})` : ''}`} c="var(--acc)">
      {d.comments.map((c, i) => <div key={i} className="text-[13px] mb-1.5 whitespace-pre-wrap break-words"><span className="text-mut text-[11px] block">{hm(c.at)}</span>{c.text}</div>)}
      <textarea className="inp" rows={3} placeholder="Pl. csak a mobilnézetet javítsd; a kártyára ne írj." value={text} onChange={e => setText(e.target.value)} />
      <div className="flex items-center gap-2 mt-1">
        <button className="btn pri" disabled={!text.trim()} onClick={add}>Hozzáadás</button>
        <span className="text-mut text-[11px] leading-tight">A Worker utasításként olvassa; az állapotot nem változtatja.</span></div>
    </Box>)
}

const Box = ({ t, c, children }: { t: string; c?: string; children: ReactNode }) => (
  <section className="rounded-md border p-2.5 mb-3 last:mb-0 min-w-0" style={{ borderColor: c ?? 'var(--line)', background: c ? `color-mix(in srgb,${c} 7%,transparent)` : undefined }}>
    <h3 className="text-[12px] uppercase tracking-wide m-0 mb-1.5 font-semibold" style={{ color: c ?? 'var(--mut)' }}>{t}</h3>
    <div className="text-[13.5px] break-words">{children}</div></section>)

const Chip = ({ c, strong, children }: { c?: string; strong?: boolean; children: ReactNode }) => (
  <span className={'tag !text-[12px] !px-1.5 !py-px' + (strong ? ' font-semibold' : '')}
    style={c ? { color: c, borderColor: c, background: strong ? `color-mix(in srgb,${c} 12%,transparent)` : undefined } : undefined}>{children}</span>)

// URL-ek kattinthatók, a markdown-kép egy rövid „kép” link
function Text({ s }: { s: string }) {
  const t = s.replace(/[\u200b-\u200d\ufeff]/g, '').replace(/^#{1,6}\s+/gm, '').replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/\n{3,}/g, '\n\n')
    .replace(/!\[[^\]]*\]\((\S+?)(?:\s+"[^"]*")?\)/g, ' $1 ').replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)(?:\s+"[^"]*")?\)/g, (_, l: string, u: string) => u.includes(l.replace(/^https?:\/\//, '').replace(/…$/, '')) ? u : `${l} (${u})`)
    .trim()
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
  const [legacy, setLegacy] = useState(false)
  const nLegacy = events.filter(e => e.event_type === 'legacy').length
  const ev = [...events].reverse().filter(e => legacy || e.event_type !== 'legacy')
  const shown = all ? ev : ev.slice(0, 15)
  return (
    <div>
      {nLegacy > 0 && <label className="flex gap-1 items-center text-[12px] text-mut mb-1.5"><input type="checkbox" checked={legacy} onChange={e => setLegacy(e.target.checked)} /> régi feladatlap-napló is ({nLegacy})</label>}
      <div className="text-[12px] flex flex-col gap-1">
        {shown.map(e => {
          const txt = evText(e)
          return (
            <div key={e.id} className="grid gap-x-2" style={{ gridTemplateColumns: '6.5rem 1fr' }}>
              <span className="text-mut">{hm(e.created_at)}</span>
              <div><b>{EV_HU[e.event_type] ?? e.event_type}</b>
                {e.new_status && <span> · {e.old_status ? (STATUS_HU[e.old_status] ?? e.old_status) + ' → ' : ''}{STATUS_HU[e.new_status] ?? e.new_status}</span>}
                <span className="text-mut"> · {ACTOR_HU[e.actor_type] ?? e.actor_type}</span>
                {txt && <div className="text-[12px] whitespace-pre-wrap break-words">{txt.length > 400 ? txt.slice(0, 400) + '…' : txt}</div>}</div>
            </div>)
        })}
      </div>
      {ev.length > 15 && <button className="btn sm mt-1" onClick={() => setAll(!all)}>{all ? 'Kevesebb' : `Mind (${ev.length})`}</button>}
    </div>)
}

function Q({ q, onDone }: { q: Question; onDone: () => void }) {
  const run = useRun()
  const [text, setText] = useState('')
  const send = async (answer: string) => { if (await run(() => post(`/api/questions/${q.id}/answer`, { answer }), 'Válasz elküldve')) onDone() }
  return (
    <div className="mb-3 last:mb-0">
      <div className="font-semibold">{q.question}</div>
      {q.context && <div className="text-[12px] text-mut whitespace-pre-wrap break-words"><Text s={q.context} /></div>}
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
