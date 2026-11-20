"""Folyamaton belüli eseményszórás: SSE a Dashboardnak és ébresztés a long-pollnak.
shortcut: egyetlen API-folyamatot feltételez (uvicorn 1 worker); több példánynál Postgres LISTEN/NOTIFY kell."""
import asyncio
import threading


class Broadcaster:
    def __init__(self):
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
        sub = (asyncio.get_running_loop(), asyncio.Queue(maxsize=100))
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub):
        with self._lock:
            self._subs.discard(sub)

    def publish(self, kind: str, data: dict | None = None):
        msg = {"kind": kind, **(data or {})}
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            def put(q=q):
                if not q.full():
                    q.put_nowait(msg)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:  # lezárt event loop
                self.unsubscribe((loop, q))


bus = Broadcaster()
