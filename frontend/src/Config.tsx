import { useEffect, useState } from 'react'
import YAML from 'yaml'
import { api, get, hm, post, type State } from './api'
import { Dialog, Diff, useRun, useUI } from './ui'
import { ProjectsAdmin } from './Projects'
import Update from './Update'

type Scope = 'global' | 'writers' | 'project' | 'source' | 'update'
const TABS: [Scope, string][] = [['global', 'Globális'], ['writers', 'Író adapterek'], ['project', 'Projektek'], ['source', 'Források'], ['update', 'Frissítés']]
const NEW_TPL: Record<string, string> = {
  project: 'id: uj-projekt\nname: Új projekt\nworking_directory: /abszolut/utvonal\n',
  source: 'id: uj-forras\nname: Új forrás\nadapter_type: cli\nsettings:\n  executable: /usr/bin/pelda\n  fields:\n    external_id: id\n    title: title\n',
}
interface Hist { version: number; comment: string | null; created_by: string; created_at: string; config: any }

export default function Config({ S }: { S: State }) {
  const run = useRun()
  const { confirm } = useUI()
  const [scope, setScope] = useState<Scope>('global')
  const multi = scope === 'project' || scope === 'source'
  const ids = scope === 'project' ? S.projects.map(p => p.id) : scope === 'source' ? S.sources.map(s => s.id) : []
  const [sel, setSel] = useState('')           // kiválasztott azonosító; '+' = új
  const [text, setText] = useState('')
  const [hist, setHist] = useState<Hist[]>([])
  const [comment, setComment] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [res, setRes] = useState<{ ok: boolean; msg?: string; diff?: string } | null>(null)
  const [key, setKey] = useState<{ id: string; key: string } | null>(null)
  const sid = multi ? (sel === '+' ? '' : sel) : ''

  useEffect(() => { setSel(multi ? (ids[0] ?? '+') : '') }, [scope]) // eslint-disable-line react-hooks/exhaustive-deps
  const load = async () => {
    setRes(null)
    if (scope === 'update') return
    if (multi && sel === '+') { setText(NEW_TPL[scope]); setHist([]); return }
    if (multi && !sel) return
    const r = await run(() => get(`/api/config/${scope}${multi ? '/' + sel : ''}`))
    if (r) { setText(YAML.stringify(r.current ?? {})); setHist(r.history) }
  }
  useEffect(() => { load() }, [scope, sel]) // eslint-disable-line react-hooks/exhaustive-deps

  const parse = () => { try { return YAML.parse(text) } catch (e: any) { setRes({ ok: false, msg: 'Hibás YAML: ' + e.message }); return undefined } }
  const validate = async () => {
    const data = parse(); if (data === undefined) return
    const r = await run(() => post(`/api/config/${scope}/validate`, { data }))
    if (r) setRes(r.ok ? { ok: true, diff: r.diff || '(nincs változás)' } : { ok: false, msg: r.error })
  }
  const save = async () => {
    const data = parse(); if (data === undefined) return
    const r = await run(() => post(`/api/config/${scope}`, { data, comment: comment || undefined }), 'Konfiguráció mentve')
    if (!r) return
    setComment('')
    setNotice(r.restart_required ?? null)
    if (r.worker_key) setKey({ id: r.scope_id, key: r.worker_key })
    if (multi) setSel(r.scope_id)
    load()
  }
  const rollback = async (version: number) => {
    if (!(await confirm(`Visszaállítod a v${version} verziót? Új verzióként mentődik.`))) return
    const r = await run(() => post(`/api/config/${scope}/rollback`, { scope_id: sid, version }), 'Visszaállítva')
    setNotice(r?.restart_required ?? null)
    if (r) load()
  }
  const importFile = async (f: File) => {
    if (!(await confirm(`Importálod: ${f.name}? A benne szereplő projektek, források és beállítások új verzióként mentődnek.`))) return
    const body = await f.text()
    const r = await run(() => api('POST', '/api/config-import', body, true), 'Import kész')
    if (r) { setRes({ ok: true, diff: `Importálva: projektek: ${r.projects.join(', ') || '—'}; források: ${r.sources.join(', ') || '—'}` }); load() }
  }

  return (
    <div className="p-4 max-w-5xl">
      <div className="flex flex-wrap gap-2 items-center mb-3">
        {TABS.map(([k, t]) => <button key={k} className={'btn ' + (scope === k ? 'pri' : '')} onClick={() => setScope(k)}>{t}</button>)}
        <span className="ml-auto flex gap-2 items-center">
          <a className="btn no-underline" style={{ color: 'var(--fg)' }} href="/api/config/export" download="agenthub-config.yaml">Export</a>
          <label className="btn cursor-pointer">Import<input type="file" accept=".yaml,.yml,.json" className="hidden" onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) importFile(f) }} /></label>
        </span>
      </div>
      {scope === 'update' ? <Update S={S} /> : <>
      {scope === 'project' && <ProjectsAdmin S={S} onChanged={load} />}
      {scope === 'project' && <h3 className="text-[12px] uppercase tracking-wide text-mut font-semibold m-0 mb-1.5">Részletes beállítás (YAML)</h3>}
      {multi && (
        <div className="flex gap-1 flex-wrap mb-2">
          {ids.map(i => <button key={i} className={'btn sm ' + (sel === i ? 'pri' : '')} onClick={() => setSel(i)}>{i}</button>)}
          <button className={'btn sm ' + (sel === '+' ? 'pri' : '')} onClick={() => setSel('+')}>+ Új</button>
        </div>)}
      <textarea className="inp font-mono text-[12px]" rows={20} spellCheck={false} aria-label="YAML" value={text} onChange={e => { setText(e.target.value); setRes(null) }} />
      <div className="flex flex-wrap gap-2 mt-2 items-center">
        <input className="inp !w-64" placeholder="Megjegyzés a mentéshez" value={comment} onChange={e => setComment(e.target.value)} />
        <button className="btn" onClick={validate}>Ellenőrzés</button><button className="btn pri" onClick={save}>Mentés</button>
      </div>
      {notice && <div className="mt-2 text-warn text-[13px] font-semibold" role="status">Újraindítás szükséges: {notice}</div>}
      {res && (res.ok ? <div className="mt-2"><div className="text-ok text-[13px]">Érvényes konfiguráció. Változás:</div><Diff text={res.diff ?? ''} /></div>
        : <pre className="code mt-2" style={{ color: 'var(--bad)' }} role="alert">{res.msg}</pre>)}
      {hist.length > 0 && (
        <section className="mt-4"><h3 className="text-[11px] uppercase tracking-wide text-mut font-semibold m-0 mb-1">Verziótörténet</h3>
          {hist.map((h, i) => (
            <div key={h.version} className="flex gap-3 items-center text-[13px] py-1 border-t border-line">
              <b>v{h.version}</b><span className="text-mut">{hm(h.created_at)} · {h.created_by}</span><span className="flex-1">{h.comment}</span>
              {i === 0 ? <span className="text-mut text-[12px]">aktuális</span> : <button className="btn sm" onClick={() => rollback(h.version)}>Visszaállít</button>}
            </div>))}
        </section>)}
      </>}
      {key && (
        <Dialog onClose={() => setKey(null)}>
          <h2 className="text-base font-semibold m-0 mb-2 pr-10">Worker-kulcs: {key.id}</h2>
          <p className="text-[13px]">Ez a kulcs csak most látszik, később nem kérhető le újra. Másold ki, vagy a hoston futtasd ezt, amely a secrets-könyvtárba írja:</p>
          <pre className="code">agenthub project key {key.id}</pre>
          <pre className="code select-all" data-testid="worker-key">{key.key}</pre>
          <button className="btn pri" onClick={() => navigator.clipboard?.writeText(key.key).then(() => run(async () => 0, 'Kimásolva'))}>Másolás</button>
        </Dialog>)}
    </div>)
}
