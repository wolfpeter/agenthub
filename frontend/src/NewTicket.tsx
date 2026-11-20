import { useState } from 'react'
import { post, type State, type Ticket } from './api'
import { Dialog, useRun } from './ui'

export const TYPES = ['task', 'bug', 'feature', 'question']

export default function NewTicket({ S, proj, onClose, onCreated }: { S: State; proj: string; onClose: () => void; onCreated: (id: number) => void }) {
  const run = useRun()
  const [f, setF] = useState({ title: '', description: '', project_id: S.projects.length === 1 ? S.projects[0].id : proj, type: 'task', priority: 2, start_approved: false })
  const set = (k: string, v: any) => setF(x => ({ ...x, [k]: v }))
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const t = await run(() => post<Ticket>('/api/tickets', { ...f, project_id: f.project_id || null }), 'Feladat létrehozva')
    if (t) onCreated(t.id)
  }
  return (
    <Dialog onClose={onClose}>
      <form onSubmit={submit}>
        <h2 className="text-base font-semibold m-0 mb-2 pr-10">Új feladat</h2>
        <label className="lbl" htmlFor="nt-title">Cím</label>
        <input id="nt-title" className="inp" autoFocus required value={f.title} onChange={e => set('title', e.target.value)} />
        <label className="lbl" htmlFor="nt-desc">Leírás</label>
        <textarea id="nt-desc" className="inp" rows={5} value={f.description} onChange={e => set('description', e.target.value)} />
        <div className="grid grid-cols-3 gap-2">
          <div><label className="lbl" htmlFor="nt-proj">Projekt</label>
            <select id="nt-proj" className="inp" value={f.project_id} onChange={e => set('project_id', e.target.value)}>
              <option value="">— besorolásra vár —</option>{S.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <div><label className="lbl" htmlFor="nt-type">Típus</label>
            <select id="nt-type" className="inp" value={f.type} onChange={e => set('type', e.target.value)}>{TYPES.map(t => <option key={t}>{t}</option>)}</select></div>
          <div><label className="lbl" htmlFor="nt-prio">Prioritás</label>
            <select id="nt-prio" className="inp" value={f.priority} onChange={e => set('priority', +e.target.value)}>
              <option value={1}>1 – sürgős</option><option value={2}>2 – normál</option><option value={3}>3 – ráér</option></select></div>
        </div>
        <label className="flex gap-2 items-center mt-3"><input type="checkbox" checked={f.start_approved} onChange={e => set('start_approved', e.target.checked)} /> Mehet (a worker azonnal felvehetheti)</label>
        <div className="flex justify-end gap-2 mt-4"><button type="button" className="btn" onClick={onClose}>Mégse</button><button className="btn pri" disabled={!f.title.trim()}>Létrehozás</button></div>
      </form>
    </Dialog>
  )
}
