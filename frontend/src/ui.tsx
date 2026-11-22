import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'

// ---- Dialog: natív <dialog>, Esc és háttérkattintás zár
export function Dialog({ onClose, children, wide }: { onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  return (
    <dialog ref={ref} onClose={onClose} onMouseDown={e => { if (e.target === ref.current) ref.current?.close() }}
      style={wide ? { maxWidth: 1000 } : undefined}>
      <div className="p-4 relative overflow-auto" style={{ maxHeight: 'calc(100vh - 34px)' }}>
        <button className="btn absolute right-3 top-3 z-10" onClick={() => ref.current?.close()} title="Bezár (Esc)" aria-label="Bezár">✕</button>
        {children}
      </div>
    </dialog>
  )
}

// ---- toast + megerősítő párbeszéd
interface Ctx { toast: (m: string, err?: boolean) => void; confirm: (m: string) => Promise<boolean> }
const UI = createContext<Ctx>(null!)
export const useUI = () => useContext(UI)

export function UIProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; m: string; err?: boolean }[]>([])
  const [ask, setAsk] = useState<{ m: string; res: (v: boolean) => void } | null>(null)
  const toast = useCallback((m: string, err?: boolean) => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t, { id, m, err }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), err ? 6000 : 3000)
  }, [])
  const confirm = useCallback((m: string) => new Promise<boolean>(res => setAsk({ m, res })), [])
  const done = (v: boolean) => { ask?.res(v); setAsk(null) }
  return (
    <UI.Provider value={{ toast, confirm }}>
      {children}
      {ask && (
        <Dialog onClose={() => done(false)}>
          <p className="mt-4 mb-4 pr-8 whitespace-pre-wrap">{ask.m}</p>
          <div className="flex gap-2 justify-end">
            <button className="btn" onClick={() => done(false)}>Mégse</button>
            <button className="btn pri" autoFocus onClick={() => done(true)}>Rendben</button>
          </div>
        </Dialog>
      )}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2" role="status" aria-live="polite">
        <AnimatePresence>
          {toasts.map(t => (
            <motion.div key={t.id} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 40 }}
              className="card px-3 py-2 shadow-lg max-w-sm text-[13px]"
              style={{ borderLeft: `4px solid var(${t.err ? '--bad' : '--ok'})` }}>{t.m}</motion.div>
          ))}
        </AnimatePresence>
      </div>
    </UI.Provider>
  )
}

// ---- futtatás hibakezeléssel: hibára toast, sikerre opcionális toast
export function useRun() {
  const { toast } = useUI()
  return useCallback(async <T,>(f: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    try { const r = await f(); if (ok) toast(ok); return r } catch (e: any) { toast(e.message || 'Hiba', true) }
  }, [toast])
}

// vágólap: http-n (pl. Tailscale-címről) a navigator.clipboard nem érhető el, ott a régi út
export async function copyText(t: string) {
  try { await navigator.clipboard.writeText(t); return } catch { /* nem biztonságos környezet */ }
  const ta = Object.assign(document.createElement('textarea'), { value: t })
  ta.style.position = 'fixed'; ta.style.opacity = '0'
  ;(document.querySelector('dialog[open]') ?? document.body).appendChild(ta)
  ta.select(); const ok = document.execCommand('copy'); ta.remove()
  if (!ok) throw new Error('A másolás nem sikerült; jelöld ki kézzel')
}

export function CopyBtn({ text, label = 'Másolás' }: { text: string; label?: string }) {
  const run = useRun()
  const [done, setDone] = useState(false)
  return <button className="btn sm" onClick={async () => { if (await run(() => copyText(text).then(() => true))) { setDone(true); setTimeout(() => setDone(false), 1500) } }}>{done ? '✓ Másolva' : label}</button>
}

export const Skel = ({ h = 60 }: { h?: number }) => <div className="skel" style={{ height: h }} />

export function Diff({ text }: { text: string }) {
  return (
    <pre className="code">{text.split('\n').map((l, i) => (
      <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'diff-add' : l.startsWith('-') && !l.startsWith('---') ? 'diff-del' : ''}>{l || ' '}</div>
    ))}</pre>
  )
}
