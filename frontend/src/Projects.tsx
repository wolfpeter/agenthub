import { useEffect, useState } from 'react'
import { api, get, post, type State } from './api'
import { Dialog, useRun, useUI } from './ui'

interface Dir { name: string; path: string; git: boolean; project: string | null }
interface Listing { root: string | null; path?: string; parent?: string | null; dirs?: Dir[]; project?: string | null; error?: string }

// a projektazonosító a mappa nevéből: kisbetű, ékezet nélkül, csak a-z0-9-_
const slug = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)

export function AddProject({ onClose, onDone }: { onClose: () => void; onDone?: (id: string) => void }) {
  const run = useRun()
  const [ls, setLs] = useState<Listing | null>(null)
  const [dir, setDir] = useState<Dir | null>(null)
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const open = (path?: string) => run(() => get<Listing>('/api/fs/dirs' + (path ? '?path=' + encodeURIComponent(path) : ''))).then(r => r && setLs(r))
  useEffect(() => { open() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (d: Dir) => { setDir(d); setId(slug(d.name)); setName(d.name) }
  const save = async () => {
    if (!dir) return
    const r = await run(() => post('/api/config/project', { data: { id, name, working_directory: dir.path }, comment: 'Dashboardon felvéve' }), 'Projekt felvéve')
    if (r) { onDone?.(id); onClose() }
  }
  const crumbs = ls?.root && ls.path ? [ls.root, ...ls.path.slice(ls.root.length).split('/').filter(Boolean)] : []
  return (
    <Dialog onClose={onClose}>
      <h2 className="text-base font-semibold m-0 mb-2 pr-10">Új projekt</h2>
      {ls?.error ? <div className="text-warn text-[13px]">{ls.error}</div> : <>
        <div className="text-[12px] text-mut mb-1">Válaszd ki a projekt mappáját:</div>
        <div className="flex flex-wrap gap-1 items-center text-[13px] mb-1">
          {crumbs.map((c, i) => {
            const path = [ls!.root, ...crumbs.slice(1, i + 1)].join('/').replace(/\/+/g, '/')
            return <span key={i}>{i > 0 && <span className="text-mut"> / </span>}<a href="#" onClick={e => { e.preventDefault(); open(i === 0 ? ls!.root! : path) }}>{i === 0 ? c : c}</a></span>
          })}
        </div>
        <div className="card overflow-auto" style={{ maxHeight: '40vh' }}>
          {ls?.parent && <button className="w-full text-left px-3 py-1.5 border-b border-line hover:bg-[var(--line)]" onClick={() => open(ls.parent!)}>⬑ ..</button>}
          {ls?.dirs?.length === 0 && <div className="px-3 py-2 text-mut text-[13px]">nincs almappa</div>}
          {ls?.dirs?.map(d => (
            <div key={d.path} className={'flex items-center gap-2 px-3 py-1.5 border-b border-line text-[13px] ' + (dir?.path === d.path ? 'bg-[color-mix(in_srgb,var(--acc)_15%,transparent)]' : '')}>
              <button className="flex-1 text-left" title="Megnyitás" onClick={() => open(d.path)}>📁 {d.name}</button>
              {d.git && <span className="tag">git</span>}
              {d.project ? <span className="text-mut text-[12px]">már projekt: {d.project}</span>
                : <button className={'btn sm ' + (dir?.path === d.path ? 'pri' : '')} onClick={() => pick(d)}>Kiválaszt</button>}
            </div>))}
        </div>
      </>}
      {dir && (
        <div className="mt-3 flex flex-col gap-2">
          <div className="text-[13px]">Mappa: <b>{dir.path}</b></div>
          <label className="text-[12px] text-mut">Név<input className="inp mt-0.5" value={name} onChange={e => setName(e.target.value)} /></label>
          <label className="text-[12px] text-mut">Azonosító (parancsokban: <code>--project {id || '…'}</code>)
            <input className="inp mt-0.5 font-mono" value={id} onChange={e => setId(slug(e.target.value))} /></label>
          <div className="flex gap-2"><button className="btn pri" disabled={!id || !name.trim()} onClick={save}>Felvétel</button><button className="btn" onClick={onClose}>Mégse</button></div>
          <div className="text-[12px] text-mut">A Worker indítása a gépen: <code>agenthub worker run --project {id || '…'}</code> (a kulcsát első indításkor magától létrehozza).</div>
        </div>)}
    </Dialog>)
}

export function ProjectsAdmin({ S, onChanged }: { S: State; onChanged: () => void }) {
  const run = useRun()
  const { confirm } = useUI()
  const [adding, setAdding] = useState(false)
  const toggle = async (id: string, value: boolean) => {
    if (!value && !(await confirm(`Elengeded a(z) ${id} projektet? Nem indul rá Worker, és nem sorol be oda senki; a ticketjei megmaradnak.`))) return
    if (await run(() => post(`/api/projects/${id}/active`, { value }), value ? 'Projekt aktiválva' : 'Projekt elengedve')) onChanged()
  }
  const del = async (id: string) => {
    if (!(await confirm(`Véglegesen törlöd a(z) ${id} projektet? (Nincs ticketje; a mappához nem nyúlunk.)`))) return
    if (await run(() => api('DELETE', `/api/projects/${id}`), 'Projekt törölve')) onChanged()
  }
  return (
    <section className="mb-4">
      <div className="flex items-center gap-2 mb-1.5">
        <h3 className="text-[12px] uppercase tracking-wide text-mut font-semibold m-0">Projektek</h3>
        <button className="btn sm pri ml-auto" onClick={() => setAdding(true)}>+ Új projekt</button>
      </div>
      <div className="card">
        {S.projects.length === 0 && <div className="p-3 text-mut text-[13px]">Még nincs projekt.</div>}
        {S.projects.map(p => (
          <div key={p.id} className={'flex flex-wrap items-center gap-3 px-3 py-2 border-b border-line text-[13px] ' + (p.is_active ? '' : 'opacity-60')}>
            <div className="flex-1 min-w-[220px]"><b>{p.name}</b> <span className="text-mut font-mono text-[12px]">{p.id}</span>
              <div className="text-mut text-[12px]">{p.working_directory} · {p.ticket_count} ticket</div></div>
            {!p.is_active && <span className="tag">inaktív</span>}
            <label className="flex gap-1 items-center"><input type="checkbox" checked={p.is_active} onChange={e => toggle(p.id, e.target.checked)} /> aktív</label>
            {p.ticket_count === 0 && <button className="btn sm bad" onClick={() => del(p.id)}>Törlés</button>}
          </div>))}
      </div>
      {adding && <AddProject onClose={() => setAdding(false)} onDone={onChanged} />}
    </section>)
}
