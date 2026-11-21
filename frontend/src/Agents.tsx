import { ago, hm, num, type State } from './api'

const dur = (sec: number | null) => sec == null ? '—' : sec < 3600 ? `${Math.round(sec / 60)} perc` : `${(sec / 3600).toFixed(1)} óra`

export default function Agents({ S, onOpen }: { S: State; onOpen: (id: number) => void }) {
  return (
    <div className="p-4 overflow-x-auto">
      <h2 className="text-base font-semibold m-0 mb-2">Ágensek</h2>
      {S.agents.length === 0 ? <div className="text-mut">Még nem jelentkezett ágens.</div> : (
        <table className="w-full text-[13px] card" style={{ borderCollapse: 'collapse' }}>
          <thead><tr className="text-left text-mut text-[12px]">{['Állapot', 'Példány', 'Szerep', 'Projekt', 'Modell', 'Session', 'Aktív ticket', 'Futás', 'Token', 'Költség', 'Futási idő', 'Utolsó heartbeat', 'Indult'].map(h => <th key={h} className="p-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>{S.agents.map(a => (
            <tr key={a.id} className="border-t border-line">
              <td className="p-2"><span className={'dot ' + (a.alive ? 'on' : 'off')} />{a.alive ? 'él' : a.status === 'stopped' ? 'leállt' : 'nem válaszol'}</td>
              <td className="p-2">{a.id}</td><td className="p-2">{a.role}</td><td className="p-2">{a.project_id ?? '—'}</td><td className="p-2">{a.model ?? '—'}</td>
              <td className="p-2 font-mono text-[12px]">{a.session_id ?? '—'}</td>
              <td className="p-2">{a.active_ticket_id ? <a href="#/agents" onClick={e => { e.preventDefault(); onOpen(a.active_ticket_id!) }}>#{a.active_ticket_id}</a> : '—'}</td>
              <td className="p-2">{num(a.stats.runs)}</td>
              <td className="p-2">{a.stats.tokens_in == null && a.stats.tokens_out == null ? '—' : num((a.stats.tokens_in ?? 0) + (a.stats.tokens_out ?? 0))}</td>
              <td className="p-2" title="A költség becsült vagy bejelentett érték">{a.stats.cost_usd == null ? '—' : '~$' + a.stats.cost_usd.toFixed(2)}</td>
              <td className="p-2">{dur(a.stats.run_seconds)}</td>
              <td className="p-2" title={hm(a.last_heartbeat)}>{ago(a.last_heartbeat)}</td><td className="p-2">{hm(a.started_at)}</td></tr>))}</tbody>
        </table>)}
    </div>)
}
