import { useEffect, useState } from 'react'
import { ago, get, post, type Knowledge as K, type State } from './api'
import { useRun, useUI } from './ui'

function Proposal({ k }: { k: K }) {
  const run = useRun()
  const [f, setF] = useState({ title: k.title, category: k.category, content: k.content })
  const decide = (decision: string, ok: string) => run(() => post(`/api/knowledge/${k.id}/decide`, decision === 'accept' ? { decision, ...f } : { decision }), ok)
  return (
    <div className="card p-2.5 mb-2" data-knowledge={k.id}>
      <div className="text-[12px] text-mut">{k.project_id} · javasolta: {k.created_by ?? 'worker'} · {ago(k.created_at)}{k.source_ticket_id ? ` · #${k.source_ticket_id}` : ''}</div>
      <div className="grid grid-cols-[1fr_200px] gap-2 mt-1">
        <input className="inp font-semibold" aria-label="Cím" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} />
        <input className="inp" aria-label="Kategória" value={f.category} onChange={e => setF({ ...f, category: e.target.value })} />
      </div>
      <textarea className="inp mt-2" aria-label="Tartalom" rows={5} value={f.content} onChange={e => setF({ ...f, content: e.target.value })} />
      <div className="flex gap-2 mt-2"><button className="btn ok" onClick={() => decide('accept', 'Elfogadva')}>Elfogad</button><button className="btn bad" onClick={() => decide('reject', 'Elutasítva')}>Elutasít</button></div>
    </div>)
}

export default function Knowledge({ S, proj }: { S: State; proj: string }) {
  const run = useRun()
  const { confirm } = useUI()
  const [p, setP] = useState(proj || S.projects[0]?.id || '')
  const [mem, setMem] = useState<K[] | null>(null)
  const load = () => p ? get<K[]>(`/api/memory?project=${encodeURIComponent(p)}`).then(setMem).catch(() => setMem([])) : setMem([])
  useEffect(() => { load() }, [p, S.now]) // eslint-disable-line react-hooks/exhaustive-deps
  const props = S.knowledge.filter(k => !proj || k.project_id === proj)
  return (
    <div className="p-4 grid gap-6 lg:grid-cols-2 items-start">
      <section>
        <h2 className="text-base font-semibold m-0 mb-2">Javasolt tudás ({props.length})</h2>
        {props.length === 0 && <div className="text-mut">Nincs elbírálandó javaslat.</div>}
        {props.map(k => <Proposal key={k.id} k={k} />)}
      </section>
      <section>
        <div className="flex gap-2 items-center mb-2"><h2 className="text-base font-semibold m-0">Elfogadott memória</h2>
          <select className="inp !w-auto" aria-label="Projekt" value={p} onChange={e => setP(e.target.value)}>{S.projects.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>
        {mem === null ? null : mem.length === 0 && <div className="text-mut">Nincs elfogadott bejegyzés.</div>}
        {mem?.map(k => (
          <div key={k.id} className="card p-2.5 mb-2">
            <div className="flex gap-2 items-center"><span className="tag">{k.category}</span><b>{k.title}</b>
              <button className="btn bad sm ml-auto" onClick={async () => { if (await confirm(`Visszavonod: ${k.title}?`)) { await run(() => post(`/api/knowledge/${k.id}/decide`, { decision: 'retire' }), 'Visszavonva'); load() } }}>Visszavon</button></div>
            <pre className="code">{k.content}</pre>
          </div>))}
      </section>
    </div>)
}
