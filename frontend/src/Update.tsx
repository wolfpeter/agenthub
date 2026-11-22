import { useEffect, useState } from 'react'
import { get, post, hm, type State } from './api'
import { useRun, useUI } from './ui'

interface Check { state: string; current?: string; latest?: string; behind?: string[]; behind_count?: number; ahead?: number; error?: string; at?: string }
interface St { helper: boolean; pending: boolean; check?: Check; install?: { state: string; log: string; started_at?: string; finished_at?: string } }

const STATE_HU: Record<string, string> = {
  current: 'Naprakész.', behind: 'Van frissítés.', ahead: 'A helyi példány előrébb jár a GitHubnál (fejlesztői példány): nincs mit telepíteni.',
  diverged: 'A helyi példány és a GitHub elágazott: kézzel kell rendezni.', dirty: 'A helyi mappában nem mentett változás van: a telepítés nem indítható.',
  error: 'Az ellenőrzés nem sikerült.',
}

export default function Update({ S }: { S: State }) {
  const run = useRun()
  const { confirm } = useUI()
  const [st, setSt] = useState<St | null>(null)
  const [waiting, setWaiting] = useState(false)  // kérés elküldve, vagy telepítés közben (az API újraindulhat)
  const load = () => get<St>('/api/update').then(r => { setSt(r); return r }).catch(() => null)
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!waiting) return
    const t = setInterval(async () => {
      const r = await load()
      if (!r || r.pending || r.install?.state === 'running') return
      setWaiting(false)
      if (r.install?.state === 'ok' && r.install.finished_at && Date.now() - Date.parse(r.install.finished_at) < 120000) location.reload()
    }, 3000)
    return () => clearInterval(t)
  }, [waiting])

  const ask = async (action: 'check' | 'install') => {
    if (action === 'install') {
      const busy = S.agents.filter(a => a.alive && a.active_ticket_id).map(a => `${a.project_id ?? a.role} (#${a.active_ticket_id})`)
      if (!(await confirm('Telepíted a frissítést? Előtte mentés készül, a szolgáltatás közben újraindul.'
        + (busy.length ? ` Most dolgozik: ${busy.join(', ')}.` : '') + ' Utána a Coordinatort és a Workereket indítsd újra.'))) return
    }
    if (await run(() => post('/api/update', { action }))) { setWaiting(true); load() }
  }

  if (!st) return null
  const c = st.check, ins = st.install
  const running = waiting || st.pending || ins?.state === 'running'
  return (
    <section className="card p-4 max-w-3xl text-[13px] flex flex-col gap-3">
      <h3 className="text-base font-semibold m-0">AgentHub frissítése</h3>
      {!st.helper ? (
        <div className="text-warn">A gépoldali segéd nem fut, ezért innen nem lehet frissíteni. Egyszer kell telepíteni a gépen:
          <pre className="code mt-1">agenthub helper install</pre></div>
      ) : <>
        <div className="flex gap-2 items-center">
          <button className="btn" disabled={running} onClick={() => ask('check')}>Frissítés keresése</button>
          {c?.state === 'behind' && <button className="btn pri" disabled={running} onClick={() => ask('install')}>Telepítés ({c.behind_count} változás)</button>}
          {running && <span className="text-mut">{ins?.state === 'running' ? 'Telepítés folyamatban… (az oldal a végén újratölt)' : 'Folyamatban…'}</span>}
        </div>
        {c && <div>
          <div><b>{STATE_HU[c.state] ?? c.state}</b> {c.current && <span className="text-mut">Telepítve: <code>{c.current}</code>, GitHub: <code>{c.latest}</code>{c.at && ` · ellenőrizve ${hm(c.at)}`}</span>}</div>
          {c.error && <pre className="code mt-1">{c.error}</pre>}
          {!!c.behind?.length && <ul className="mt-1 mb-0 pl-5">{c.behind.map(l => <li key={l}><code>{l.slice(0, 7)}</code>{l.slice(7)}</li>)}</ul>}
        </div>}
        {ins && ins.state !== 'running' && <div>
          <b style={{ color: ins.state === 'ok' ? 'var(--ok)' : 'var(--bad)' }}>
            {ins.state === 'ok' ? 'Az utolsó telepítés sikerült.' : ins.state === 'refused' ? 'A telepítés nem indult.' : 'Az utolsó telepítés nem sikerült.'}</b>
          {ins.finished_at && <span className="text-mut"> {hm(ins.finished_at)}</span>}
          {ins.state === 'ok' && <div className="text-warn font-semibold">A Coordinatort és a Workereket indítsd újra, hogy az új szabályokkal fussanak.</div>}
          {ins.log && <pre className="code mt-1 max-h-72 overflow-auto">{ins.log}</pre>}
        </div>}
      </>}
    </section>)
}
