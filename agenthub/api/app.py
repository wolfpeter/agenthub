"""AgentHub REST API + SSE. Minden végpont szerveroldalon ellenőrzi a szerepet és a projektet (SPECIFICATION 16, 18)."""
import asyncio
import ipaddress
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
import time
import uuid
from datetime import timedelta
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select, text

from .. import __version__, auth, db, secretstore, serialize as ser, services as sv, states, writers
from ..auth import Principal
from ..events import bus
from ..models import (TicketSourceRef, AgentInstance, AgentRun, Installation, PendingAction, Project, ProjectKnowledge, Source,
                      Ticket, TicketAnswer, TicketEvent, TicketQuestion, now)

log = logging.getLogger("agenthub")


class _JsonLog(logging.Formatter):
    """Strukturált backend-log (SPECIFICATION 19): egy JSON-sor eseményenként, korrelációs azonosítóval."""
    def format(self, r):
        d = {"ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S%z"), "level": r.levelname, "msg": secretstore.mask(r.getMessage())}
        d.update(getattr(r, "ctx", {}))
        return json.dumps(d, ensure_ascii=False)


if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(_JsonLog())
    log.addHandler(_h)
    log.propagate = False
log.setLevel(logging.INFO)


def apply_log_level(level: str):
    log.setLevel(getattr(logging, level, logging.INFO))
COOKIE = "agenthub_key"
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app):
    await run_in_threadpool(_startup)
    yield


app = FastAPI(title="AgentHub", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)



_TICKET_PATH = re.compile(r"/api/tickets/(\d+)")


# LAN, Tailscale (CGNAT) és helyi gép; nyilvános internetről alapból nem (SPECIFICATION 15)
_PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                     "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10")]
ALLOW_PUBLIC = os.environ.get("AGENTHUB_ALLOW_PUBLIC", "0") == "1"


def _private_client(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return host in ("testclient", "localhost")
    ip = ip.ipv4_mapped or ip if isinstance(ip, ipaddress.IPv6Address) else ip
    return any(ip in n for n in _PRIVATE_NETS if n.version == ip.version)


@app.middleware("http")
async def _network_guard(request: Request, call_next):
    if not ALLOW_PUBLIC and not _private_client(request.client.host if request.client else None):
        return JSONResponse({"detail": "Az AgentHub nyilvános internetről nem érhető el (AGENTHUB_ALLOW_PUBLIC=1 engedi)."}, 403)
    return await call_next(request)


@app.middleware("http")
async def _request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        resp.headers["X-Request-ID"] = rid
        return resp
    finally:
        if request.url.path.startswith("/api/"):
            p = getattr(request.state, "principal", None)
            m = _TICKET_PATH.match(request.url.path)
            ctx = {"request_id": rid, "method": request.method, "path": request.url.path, "status": status,
                   "ms": round((time.monotonic() - t0) * 1000), "actor": p.actor_id if p else None,
                   "project_id": p.project_id if p else None, "ticket_id": int(m.group(1)) if m else None}
            log.log(logging.WARNING if status >= 500 else logging.INFO if status >= 400 else logging.DEBUG,
                    "request", extra={"ctx": ctx})


@app.exception_handler(sv.Forbidden)
async def _forbidden(_, e):
    return JSONResponse({"detail": str(e)}, 403)


@app.exception_handler(sv.Conflict)
async def _conflict(_, e):
    return JSONResponse({"detail": str(e), "code": e.code}, 409)


@app.exception_handler(sv.NotFound)
async def _notfound(_, e):
    return JSONResponse({"detail": str(e)}, 404)


@app.exception_handler(sv.Invalid)
@app.exception_handler(states.TransitionError)
async def _invalid(_, e):
    return JSONResponse({"detail": str(e)}, 400)


def _startup():
    with db.session() as s:
        if not s.scalar(select(Installation).limit(1)):
            s.add(Installation(name=os.environ.get("AGENTHUB_NAME", "AgentHub"), version=__version__))
        done = auth.bootstrap_from_files(s)
        if done:
            log.warning("kulcs regisztrálva a secrets-könyvtárból: %s", ", ".join(done))
        for p in s.scalars(select(Project)):
            sv.sync_memory_files(s, p.id)
        pending = sv.recover_executions(s)
        apply_log_level(sv.global_config(s).log_level)
    for aid in pending:  # újraindítás előtt jóváhagyott, de el nem indult műveletek
        _execute_async(aid)



# ---------------------------------------------------------------- hitelesítés

def principal(request: Request) -> Principal:
    key, via_cookie = "", False
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        key = h[7:].strip()
    elif COOKIE in request.cookies:
        key, via_cookie = request.cookies[COOKIE], True
    with db.session() as s:
        p = auth.lookup(s, key)
    if p is None:
        raise HTTPException(401, "hiányzó vagy érvénytelen kulcs")
    request.state.principal = p  # a kérésnapló szereplője és projektje
    if via_cookie and request.method not in ("GET", "HEAD"):
        # CSRF: a cookie-s írásnak a saját originről kell jönnie
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if not origin or origin.split("://", 1)[-1] != host:
            raise HTTPException(403, "idegen origin")
    return p


def need(*roles):
    def dep(p: Principal = Depends(principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, f"ehhez {' vagy '.join(roles)} kulcs kell")
        return p
    return dep


Dash = Depends(need("dashboard"))
Worker = Depends(need("worker"))
Coord = Depends(need("coordinator"))
Any = Depends(principal)


def instance(request: Request) -> str | None:
    return request.headers.get("x-agenthub-instance")


def lease(request: Request) -> str | None:
    return request.headers.get("x-agenthub-lease")


class LoginIn(BaseModel):
    key: str


@app.get("/api/health")
def health():
    try:
        with db.session() as s:
            s.execute(text("select 1"))
        return {"ok": True, "version": __version__}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": type(e).__name__}, 503)


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    with db.session() as s:
        p = auth.lookup(s, body.key.strip())
    if not p or p.role != "dashboard":
        time.sleep(1)  # lassítja a próbálgatást
        raise HTTPException(401, "hibás kulcs")
    response.set_cookie(COOKIE, body.key.strip(), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=3600 * 24 * 365)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(p: Principal = Any):
    return {"role": p.role, "project_id": p.project_id, "name": os.environ.get("AGENTHUB_NAME") or None}


class RotateIn(BaseModel):
    role: str


@app.post("/api/keys/rotate")
def rotate(body: RotateIn, p: Principal = Dash):
    if body.role not in ("dashboard", "coordinator"):
        raise HTTPException(400, "dashboard vagy coordinator")
    with db.session() as s:
        key = auth.issue(s, body.role)
        sv.audit(s, p, "key.rotate", "key", body.role)
    return {"key": key}


@app.post("/api/projects/{pid}/key")
def project_key(pid: str, p: Principal = Dash):
    with db.session() as s:
        if not s.get(Project, pid):
            raise sv.NotFound("nincs ilyen projekt")
        key = auth.issue(s, "worker", pid)
        sv.audit(s, p, "key.rotate", "key", f"worker:{pid}")
    return {"key": key}


# ---------------------------------------------------------------- Dashboard-állapot és SSE

@app.get("/api/state")
def state(p: Principal = Dash):
    since = now() - timedelta(hours=24)
    with db.session() as s:
        gc = sv.global_config(s)
        month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_cost = dict(s.execute(select(AgentRun.project_id, func.sum(AgentRun.estimated_cost))
                                    .where(AgentRun.started_at >= month_start).group_by(AgentRun.project_id)).all())
        projects = []
        for pr in s.scalars(select(Project).order_by(Project.id)):
            d = ser.row(pr, exclude=("inventory", "config"))
            budget = (pr.config or {}).get("monthly_budget_usd")
            d["month_cost_usd"], d["monthly_budget_usd"] = month_cost.get(pr.id), budget
            d["over_budget"] = bool(budget is not None and (month_cost.get(pr.id) or 0) >= budget)
            d["worker_control"] = sv.control_state(s, f"worker:{pr.id}")
            d["inventory_count"] = len((pr.inventory or {}).get("files", []))
            d["ticket_count"] = s.scalar(select(func.count()).select_from(Ticket).where(Ticket.project_id == pr.id))
            projects.append(d)
        tickets = s.scalars(select(Ticket).where((Ticket.status.notin_(states.FINAL)) | (Ticket.updated_at >= since))
                            .order_by(Ticket.priority, Ticket.id))
        tlist = []
        for t in tickets:
            d = ser.ticket(t, brief=True)
            d["lease_stale"] = bool(t.status == "IN_PROGRESS" and t.lease_expires_at and t.lease_expires_at < now())
            tlist.append(d)
        qs = []
        for q in s.scalars(select(TicketQuestion).where((TicketQuestion.status == "OPEN") | (TicketQuestion.created_at >= since))
                           .order_by(TicketQuestion.id)):
            d = ser.row(q)
            a = s.scalar(select(TicketAnswer).where(TicketAnswer.question_id == q.id).limit(1))
            d["answer"] = a.answer if a else None
            qs.append(d)
        actions = [_action(a) for a in s.scalars(select(PendingAction).where(
            PendingAction.status.notin_(sv.ACTION_TERMINAL) | (PendingAction.created_at >= since)).order_by(PendingAction.id))]
        knowledge = [ser.row(k) for k in s.scalars(select(ProjectKnowledge).where(ProjectKnowledge.status == "PROPOSED")
                                                   .order_by(ProjectKnowledge.id))]
        run_stats = {r[0]: r[1:] for r in s.execute(
            select(AgentRun.agent_instance_id, func.count(AgentRun.id), func.sum(AgentRun.tokens_in),
                   func.sum(AgentRun.tokens_out), func.sum(AgentRun.estimated_cost),
                   func.sum(func.extract("epoch", AgentRun.finished_at - AgentRun.started_at)))
            .group_by(AgentRun.agent_instance_id)).all()}
        agents = []
        for a in s.scalars(select(AgentInstance).order_by(AgentInstance.last_heartbeat.desc())):
            if a.status == "running" or a.last_heartbeat >= since:
                d = ser.row(a)
                d["alive"] = sv.instance_alive(s, gc, a)
                runs, tin, tout, cost, secs = run_stats.get(a.id, (0, None, None, None, None))
                d["stats"] = {"runs": runs, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
                              "run_seconds": float(secs) if secs is not None else None}
                agents.append(d)
        sources = [ser.row(x, exclude=("config",)) for x in s.scalars(select(Source).order_by(Source.id))]
        events = []
        for e, title, pid in s.execute(select(TicketEvent, Ticket.title, Ticket.project_id)
                                       .join(Ticket, Ticket.id == TicketEvent.ticket_id)
                                       .order_by(TicketEvent.id.desc()).limit(200)):
            d = ser.row(e)
            d["title"], d["project_id"] = title, pid
            events.append(d)
        usage = s.execute(select(func.count(AgentRun.id), func.sum(AgentRun.tokens_in), func.sum(AgentRun.tokens_out),
                                 func.sum(AgentRun.estimated_cost)).where(AgentRun.started_at >= since)).one()
        inst = s.scalar(select(Installation).limit(1))
        return {
            "installation": inst.name if inst else gc.installation_name, "version": __version__, "now": ser.iso(now()),
            "projects": projects, "tickets": tlist, "questions": qs, "actions": actions, "knowledge": knowledge,
            "agents": agents, "sources": sources, "events": events,
            "controls": {"coordinator": sv.control_state(s, "coordinator"),
                         "outbound_approval": gc.outbound_approval},
            "usage_24h": {"runs": usage[0], "tokens_in": usage[1], "tokens_out": usage[2], "cost_usd": usage[3]},
            "coordinator_poll_interval_s": gc.coordinator_poll_interval_s,
            "last_cycle": sv.last_cycle(s),
        }


@app.get("/api/stream")
async def stream(request: Request, p: Principal = Dash):
    sub = bus.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(sub[1].get(), 15)
                    yield f"event: change\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- projektek

@app.get("/api/projects")
def projects(p: Principal = Any):
    with db.session() as s:
        q = select(Project).order_by(Project.id)
        if p.role == "worker":
            q = q.where(Project.id == p.project_id)
        return [ser.row(x) for x in s.scalars(q)]


class ProjectActiveIn(BaseModel):
    value: bool


@app.post("/api/projects/{pid}/active")
def project_active(pid: str, body: ProjectActiveIn, p: Principal = Dash):
    with db.session() as s:
        x = sv.set_project_active(s, p, pid, body.value)
        return ser.row(x)


@app.delete("/api/projects/{pid}")
def project_delete(pid: str, p: Principal = Dash):
    with db.session() as s:
        sv.delete_project(s, p, pid)
    return {"deleted": pid}


# A Dashboard mappaválasztója: a hoston beállított gyökér (AGENTHUB_PROJECTS_ROOT) csak olvasható csatolásként,
# a konténerben /projects-root alatt; csak könyvtárneveket ad ki, tartalmat nem.
FS_MOUNT = "/projects-root"


@app.get("/api/fs/dirs")
def fs_dirs(path: str | None = None, p: Principal = Dash):
    host = (os.environ.get("AGENTHUB_PROJECTS_ROOT_HOST") or "").rstrip("/")
    if not host or not os.path.isdir(FS_MOUNT):
        return {"root": None, "error": "Nincs beállítva a projektek gyökérmappája: a telepítés .env fájljában "
                                       "AGENTHUB_PROJECTS_ROOT=<mappa>, majd agenthub up."}
    path = (path or host).rstrip("/") or host
    if path != host and not path.startswith(host + "/"):
        raise HTTPException(400, "a mappa a projektek gyökérmappáján kívül van")
    base = os.path.realpath(FS_MOUNT)
    inner = os.path.realpath(os.path.join(FS_MOUNT, os.path.relpath(path, host)))
    if inner != base and not inner.startswith(base + "/"):
        raise HTTPException(400, "a mappa a projektek gyökérmappáján kívül van")
    if not os.path.isdir(inner):
        raise HTTPException(404, "nincs ilyen mappa")
    with db.session() as s:
        used = {x.working_directory.rstrip("/"): x.id for x in s.scalars(select(Project))}
    dirs = []
    for e in sorted(os.scandir(inner), key=lambda e: e.name.lower()):
        if e.name.startswith(".") or not e.is_dir():
            continue
        real = os.path.realpath(e.path)
        if not real.startswith(base + "/"):
            continue  # kifelé mutató link
        hp = f"{path}/{e.name}"
        dirs.append({"name": e.name, "path": hp, "git": os.path.isdir(os.path.join(e.path, ".git")), "project": used.get(hp)})
    return {"root": host, "path": path, "parent": None if path == host else path.rsplit("/", 1)[0], "dirs": dirs,
            "project": used.get(path)}


@app.get("/api/projects/{pid}")
def project(pid: str, p: Principal = Any):
    if p.role == "worker" and pid != p.project_id:
        raise HTTPException(404, "nincs ilyen projekt")
    with db.session() as s:
        x = s.get(Project, pid)
        if not x:
            raise HTTPException(404, "nincs ilyen projekt")
        return ser.row(x)


# ---------------------------------------------------------------- ticketek

class TicketIn(BaseModel):
    title: str
    description: str = ""
    project_id: str | None = None
    type: str = "task"
    priority: int = 2
    start_approved: bool = False
    source_url: str | None = None


class TicketPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    type: str | None = None
    priority: int | None = None


@app.get("/api/tickets")
def tickets(project: str | None = None, status: str | None = None, q: str | None = None, limit: int = 200,
            p: Principal = Any):
    with db.session() as s:
        stmt = select(Ticket).order_by(Ticket.id.desc()).limit(min(limit, 1000))
        if p.role == "worker":
            stmt = stmt.where(Ticket.project_id == p.project_id)
        elif project:
            stmt = stmt.where(Ticket.project_id == project)
        if status:
            stmt = stmt.where(Ticket.status.in_(status.split(",")))
        if q:
            stmt = stmt.where(Ticket.title.ilike(f"%{q}%"))
        return [ser.ticket(t, brief=True) for t in s.scalars(stmt)]


@app.post("/api/tickets")
def create_ticket(body: TicketIn, p: Principal = Depends(need("dashboard", "coordinator"))):
    with db.session() as s:
        return ser.ticket(sv.create_ticket(s, p, body.model_dump()))


def _action(a):
    d = ser.row(a)
    d["diff"] = sv.action_diff(a)
    return d


@app.get("/api/tickets/{tid}")
def ticket_detail(tid: int, p: Principal = Any):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid)
        d = ser.ticket(t)
        d["events"] = [ser.row(e) for e in s.scalars(select(TicketEvent).where(TicketEvent.ticket_id == tid)
                                                     .order_by(TicketEvent.id))]
        qs = []
        for q in s.scalars(select(TicketQuestion).where(TicketQuestion.ticket_id == tid).order_by(TicketQuestion.id)):
            qd = ser.row(q)
            qd["answers"] = [ser.row(a) for a in s.scalars(select(TicketAnswer).where(TicketAnswer.question_id == q.id))]
            qs.append(qd)
        d["questions"] = qs
        d["actions"] = [_action(a) for a in s.scalars(select(PendingAction).where(PendingAction.ticket_id == tid)
                                                      .order_by(PendingAction.id))]
        d["runs"] = [ser.row(r) for r in s.scalars(select(AgentRun).where(AgentRun.ticket_id == tid).order_by(AgentRun.id))]
        d["comments"] = sv.comments(s, tid)
        d["source_refs"] = [ser.row(r) for r in s.scalars(select(TicketSourceRef).where(TicketSourceRef.ticket_id == tid)
                                                           .order_by(TicketSourceRef.id))]
        return d


@app.patch("/api/tickets/{tid}")
def patch_ticket(tid: int, body: TicketPatch, p: Principal = Dash):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.update_ticket(s, p, t, body.model_dump())
        return ser.ticket(t)


class GoIn(BaseModel):
    value: bool
    reason: str | None = None


@app.post("/api/tickets/{tid}/go")
def go(tid: int, body: GoIn, p: Principal = Any):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.set_go(s, p, t, body.value, body.reason)
        return ser.ticket(t, brief=True)


class HotfixIn(BaseModel):
    value: bool


@app.post("/api/tickets/{tid}/hotfix")
def hotfix(tid: int, body: HotfixIn, p: Principal = Dash):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.set_hotfix(s, p, t, body.value)
        return ser.ticket(t, brief=True)


class TransitionIn(BaseModel):
    action: str
    note: str | None = None
    project_id: str | None = None


@app.post("/api/tickets/{tid}/transition")
def transition(tid: int, body: TransitionIn, p: Principal = Dash):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.human_transition(s, p, t, body.action, body.note, body.project_id)
        return ser.ticket(t, brief=True)


class ClassifyIn(BaseModel):
    project_id: str | None = None
    type: str | None = None
    priority: int | None = None
    triage: bool = False
    reason: str | None = None
    go: bool = False
    go_reason: str | None = None


@app.post("/api/tickets/{tid}/classify")
def classify(tid: int, body: ClassifyIn, p: Principal = Coord):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.classify(s, p, t, body.project_id, body.type, body.priority, body.triage, body.reason, body.go, body.go_reason)
        return ser.ticket(t, brief=True)


class MergeIn(BaseModel):
    into: int
    addition: str | None = None


@app.post("/api/tickets/{tid}/merge")
def merge(tid: int, body: MergeIn, p: Principal = Depends(need("coordinator", "dashboard"))):
    with db.session() as s:
        dup = sv.get_ticket(s, p, tid, lock=True)
        return ser.ticket(sv.merge_ticket(s, p, dup, body.into, body.addition), brief=True)


# ---------------------------------------------------------------- agentek

class RegisterIn(BaseModel):
    instance_id: str
    session_id: str | None = None
    working_directory: str | None = None
    model: str | None = None
    takeover: bool = False


@app.post("/api/agents/register")
def register(body: RegisterIn, p: Principal = Depends(need("worker", "coordinator"))):
    with db.session() as s:
        inst = sv.register_agent(s, p, body.instance_id, body.session_id, body.working_directory, body.model,
                                 body.takeover)
        out = {"instance": ser.row(inst)}
        if p.role == "worker":
            pr = s.get(Project, p.project_id)
            out["project"] = sv.project_config(pr).model_dump(mode="json")
            cur = s.scalar(select(Ticket).where(Ticket.project_id == p.project_id, Ticket.status == "IN_PROGRESS"))
            out["interrupted_ticket"] = ser.ticket(cur, brief=True) if cur else None
        return out


@app.post("/api/agents/heartbeat")
def hb(request: Request, p: Principal = Depends(need("worker", "coordinator"))):
    with db.session() as s:
        sv.heartbeat(s, p, instance(request))
    return {"ok": True}


@app.post("/api/agents/stop")
def agent_stop(request: Request, p: Principal = Depends(need("worker", "coordinator"))):
    with db.session() as s:
        sv.stop_agent(s, p, instance(request))
    return {"ok": True}


def _worker_payload(s, t, resumed, p):
    gc = sv.global_config(s)
    pc = sv.project_config(s.get(Project, p.project_id))
    model = pc.worker.model or pc.models.get(t.type) or gc.models.by_type.get(t.type) or gc.models.default
    d = ser.ticket(t)
    if len(d.get("description") or "") > 3000:
        d["description"] = d["description"][:3000] + "… (a teljes leírás: agenthub ticket show)"
    d["comments"] = sv.comments(s, t.id)  # Peter megjegyzései: utasítás a Workernek
    return {"ticket": d, "lease_id": t.current_lease_id, "resumed": resumed, "model": model,
            "escalate_to": gc.models.escalate_to}


@app.get("/api/worker/peek")
def worker_peek(request: Request, p: Principal = Worker):
    """A következő jelölt lefoglalás nélkül, a helyi kapunak (worker_gate_command). Életjel is: a kapu mögött várakozó
    Worker így nem tűnik halottnak, és a leállítást is látja."""
    with db.session() as s:
        sv.heartbeat(s, p, instance(request))
        if sv.control_state(s, f"worker:{p.project_id}") == "stop":
            return {"stop": True}
        t = sv.peek_ticket(s, p)
        gc = sv.global_config(s)
        return {"ticket": ser.ticket(t, brief=True) if t else None,
                "gate": gc.worker_gate_command, "outbound_approval": gc.outbound_approval}


@app.get("/api/worker/next")
async def worker_next(request: Request, wait: int = 25, session: str | None = None, p: Principal = Worker):
    inst = instance(request)
    if not inst:
        raise HTTPException(400, "X-AgentHub-Instance fejléc kell (agenthub worker start)")
    deadline = time.monotonic() + max(0, min(wait, 55))
    sub = bus.subscribe()
    try:
        while True:
            def attempt():
                with db.session() as s:
                    sv.heartbeat(s, p, inst)
                    if sv.control_state(s, f"worker:{p.project_id}") == "stop":
                        return {"stop": True}
                    t, resumed = sv.next_ticket(s, p, inst, session)
                    return _worker_payload(s, t, resumed, p) if t else None
            res = await run_in_threadpool(attempt)
            if res:
                return res
            left = deadline - time.monotonic()
            if left <= 0:
                return {"idle": True}
            try:
                await asyncio.wait_for(sub[1].get(), min(left, 10))
            except asyncio.TimeoutError:
                pass
    finally:
        bus.unsubscribe(sub)


@app.get("/api/worker/context")
def worker_context(p: Principal = Worker):
    with db.session() as s:
        pr = s.get(Project, p.project_id)
        mem = sv.memory_for(s, p)
        return {"project": sv.project_config(pr).model_dump(mode="json"),
                "control": sv.control_state(s, f"worker:{p.project_id}"),
                "wait_timeout_s": sv.global_config(s).worker_wait_timeout_s,
                "outbound_approval": sv.global_config(s).outbound_approval,
                "memory": [{"id": k.id, "category": k.category, "title": k.title} for k in mem]}


class NoteIn(BaseModel):
    text: str


@app.post("/api/tickets/{tid}/note")
def ticket_note(tid: int, body: NoteIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.note(s, p, tid, body.text, instance(request), lease(request))
    return {"ok": True}


@app.post("/api/tickets/{tid}/comment")
def ticket_comment(tid: int, body: NoteIn, p: Principal = Dash):
    with db.session() as s:
        sv.comment(s, p, tid, body.text)
    return {"ok": True}


class AskIn(BaseModel):
    question: str
    context: str = ""
    suggested_answer: str | None = None


@app.post("/api/tickets/{tid}/ask")
def ticket_ask(tid: int, body: AskIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        q = sv.ask(s, p, tid, body.question, body.context, body.suggested_answer, instance(request), lease(request))
        return ser.row(q)


class RunIn(BaseModel):
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class FinishIn(BaseModel):
    summary: str
    payload: dict | None = None
    run: RunIn | None = None
    to: str = "review"


@app.post("/api/tickets/{tid}/finish")
def ticket_finish(tid: int, body: FinishIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.finish(s, p, tid, body.summary, body.payload, body.run.model_dump() if body.run else None, instance(request),
                  lease(request), body.to)
    return {"ok": True, "status": sv.FINISH_TO.get(body.to)}


class BlockIn(BaseModel):
    reason: str


@app.post("/api/tickets/{tid}/block")
def ticket_block(tid: int, body: BlockIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.block(s, p, tid, body.reason, instance(request), lease(request))
    return {"ok": True, "status": "BLOCKED"}


@app.get("/api/release")
def release_list(p: Principal = Worker):
    with db.session() as s:
        return [ser.ticket(t, brief=True) | {"result_summary": t.result_summary} for t in sv.release_list(s, p)]


class ReleasedIn(BaseModel):
    summary: str
    failed: bool = False


@app.post("/api/tickets/{tid}/released")
def ticket_released(tid: int, body: ReleasedIn, p: Principal = Worker):
    with db.session() as s:
        sv.release_done(s, p, tid, body.summary, body.failed)
    return {"ok": True, "status": "READY" if body.failed else "COMPLETED"}


class FailIn(BaseModel):
    error: str
    run: RunIn | None = None


@app.post("/api/tickets/{tid}/fail")
def ticket_fail(tid: int, body: FailIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.fail(s, p, tid, body.error, body.run.model_dump() if body.run else None, instance(request), lease(request))
    return {"ok": True}


class ActionIn(BaseModel):
    target_system: str
    action_type: str
    target_reference: str | None = None
    payload: dict


@app.post("/api/tickets/{tid}/actions")
def ticket_action(tid: int, body: ActionIn, request: Request, p: Principal = Depends(need("worker", "coordinator"))):
    with db.session() as s:
        a = sv.propose_action(s, p, tid, body.target_system, body.action_type, body.target_reference, body.payload,
                              instance(request), lease(request))
        return _action(a)


@app.get("/api/worker/file-actions")
def file_actions(p: Principal = Worker):
    with db.session() as s:
        return [ser.row(a) for a in sv.file_actions_for(s, p)]


class ReportIn(BaseModel):
    ok: bool
    result: dict = {}


@app.post("/api/worker/file-actions/{aid}/report")
def file_action_report(aid: int, body: ReportIn, p: Principal = Worker):
    with db.session() as s:
        sv.report_file_action(s, p, aid, body.ok, body.result)
    return {"ok": True}


@app.post("/api/worker/inventory")
def inventory(body: dict, p: Principal = Worker):
    with db.session() as s:
        sv.save_inventory(s, p, body)
    return {"ok": True}


# ---------------------------------------------------------------- kérdések, jóváhagyás

class AnswerIn(BaseModel):
    answer: str


@app.post("/api/questions/{qid}/answer")
def answer(qid: int, body: AnswerIn, p: Principal = Dash):
    with db.session() as s:
        return ser.row(sv.answer(s, p, qid, body.answer))


@app.get("/api/actions")
def actions(status: str | None = None, p: Principal = Any):
    with db.session() as s:
        q = select(PendingAction).order_by(PendingAction.id.desc()).limit(300)
        if p.role == "worker":
            q = q.where(PendingAction.project_id == p.project_id)
        if status:
            q = q.where(PendingAction.status.in_(status.split(",")))
        return [_action(a) for a in s.scalars(q)]


class DecideIn(BaseModel):
    decision: str
    note: str | None = None


def _execute_async(aid: int):
    """Jóváhagyás után az író adapter a háttérben hajtja végre (SPECIFICATION 10).
    Az EXECUTING állapot előbb commitolódik: ha a folyamat közben leáll, induláskor bizonytalanként látszik, nem fut újra."""
    def run():
        with db.session() as s:
            a = sv.claim_execution(s, aid)
            if not a:
                return
            job, cfg = (a.target_system, a.action_type, a.target_reference, a.proposed_payload), sv.writer_config(s)
        uncertain = False
        try:
            res, ok = writers.execute(*job, cfg), True
        except writers.WriteError as e:
            res, ok, uncertain = {"error": str(e), **({"uncertain": True} if e.uncertain else {})}, False, e.uncertain
        except Exception as e:  # váratlan hiba: nem tudni, kiment-e
            res, ok, uncertain = {"error": secretstore.mask(f"{type(e).__name__}: {e}")[:500], "uncertain": True}, False, True
        with db.session() as s:
            a = s.scalar(select(PendingAction).where(PendingAction.id == aid).with_for_update())
            if not ok and not uncertain and "nincs beállítva" in res["error"]:
                a.status, a.execution_result = "APPROVED", {"info": res["error"]}  # kézi végrehajtásra vár
                sv.changed(what="actions")
            else:
                sv.record_execution(s, auth.SYSTEM, a, ok, res)
    # nem daemon: leállításkor a futó végrehajtás befejeződik
    threading.Thread(target=run).start()


@app.post("/api/actions/{aid}/decide")
def decide(aid: int, body: DecideIn, p: Principal = Dash):
    with db.session() as s:
        a = sv.decide_action(s, p, aid, body.decision, body.note)
        out = _action(a)
        run_writer = a.status == "APPROVED" and a.target_system != "file"
    if run_writer:
        _execute_async(aid)
    return out


# ---------------------------------------------------------------- memória

class KnowledgeIn(BaseModel):
    category: str
    title: str
    content: str
    ticket_id: int | None = None


@app.post("/api/knowledge")
def knowledge_propose(body: KnowledgeIn, p: Principal = Worker):
    with db.session() as s:
        return ser.row(sv.propose_knowledge(s, p, body.category, body.title, body.content, body.ticket_id))


@app.get("/api/knowledge")
def knowledge(project: str | None = None, status: str | None = None, p: Principal = Any):
    with db.session() as s:
        q = select(ProjectKnowledge).order_by(ProjectKnowledge.id.desc()).limit(500)
        if p.role == "worker":
            q = q.where(ProjectKnowledge.project_id == p.project_id)
        elif project:
            q = q.where(ProjectKnowledge.project_id == project)
        if status:
            q = q.where(ProjectKnowledge.status.in_(status.split(",")))
        return [ser.row(k) for k in s.scalars(q)]


class KDecideIn(BaseModel):
    decision: str
    title: str | None = None
    content: str | None = None
    category: str | None = None


@app.post("/api/knowledge/{kid}/decide")
def knowledge_decide(kid: int, body: KDecideIn, p: Principal = Dash):
    with db.session() as s:
        return ser.row(sv.decide_knowledge(s, p, kid, body.decision, body.title, body.content, body.category))


@app.get("/api/memory")
def memory(project: str | None = None, p: Principal = Any):
    with db.session() as s:
        return [ser.row(k) for k in sv.memory_for(s, p, project)]


# ---------------------------------------------------------------- források, Coordinator

@app.get("/api/sources")
def sources(p: Principal = Depends(need("dashboard", "coordinator"))):
    with db.session() as s:
        return [ser.row(x) for x in s.scalars(select(Source).order_by(Source.id))]


class IngestIn(BaseModel):
    items: list[dict] = []
    error: str | None = None


@app.post("/api/sources/{sid}/events")
def ingest(sid: str, body: IngestIn, p: Principal = Coord):
    with db.session() as s:
        return sv.ingest(s, p, sid, body.items, body.error)


@app.get("/api/coordinator/context")
def coordinator_context(request: Request, p: Principal = Coord):
    with db.session() as s:
        sv.heartbeat(s, p, instance(request))
        gc = sv.global_config(s)
        return {"control": sv.control_state(s, "coordinator"),
                "poll_interval_s": gc.coordinator_poll_interval_s, "model": gc.coordinator_model,
                "cli_executables": gc.cli_executables, "instructions": gc.coordinator_instructions or None,
                "outbound_approval": gc.outbound_approval,
                "sources": [ser.row(x) for x in s.scalars(select(Source).where(Source.is_active.is_(True)))],
                "projects": [{"id": x.id, "name": x.name} for x in s.scalars(select(Project).where(Project.is_active.is_(True)))]}


class CycleIn(BaseModel):
    sources_checked: int = 0
    created: int = 0
    updated: int = 0
    routed: int = 0
    pending: int = 0
    errors: list[str] = []


@app.post("/api/coordinator/cycle")
def coordinator_cycle(body: CycleIn, p: Principal = Coord):
    """A ciklus rövid operatív összefoglalója (SPECIFICATION 5); a CLI küldi, modell nélkül."""
    with db.session() as s:
        sv.audit(s, p, "coordinator.cycle", "coordinator", None, body.model_dump())
        sv.changed(what="cycle")
    return {"ok": True}


@app.get("/api/coordinator/updates")
def coordinator_updates(after: int | None = None, p: Principal = Coord):
    with db.session() as s:
        return sv.coordinator_updates(s, after)


class CoordActIn(BaseModel):
    action: str
    reason: str | None = None
    priority: int | None = None
    title: str | None = None


@app.post("/api/tickets/{tid}/coordinator")
def coordinator_act(tid: int, body: CoordActIn, p: Principal = Coord):
    with db.session() as s:
        t = sv.get_ticket(s, p, tid, lock=True)
        sv.coordinator_act(s, p, t, body.action, body.reason, body.priority, body.title)
        return ser.ticket(t, brief=True)


@app.get("/api/coordinator/pending")
def coordinator_pending(p: Principal = Coord):
    with db.session() as s:
        return [{**ser.ticket(t, brief=True), "similar": sv.similar_tickets(s, t)} for t in sv.coordinator_pending(s)]


# ---------------------------------------------------------------- konfiguráció

class ConfigIn(BaseModel):
    data: dict
    comment: str | None = None


def _current(s, scope, sid):
    v = sv._latest(s, scope, sid)
    return v.config if v else (sv.SCOPES[scope]().model_dump(mode="json") if scope in ("global", "writers") else None)


@app.get("/api/config")
def config_all(p: Principal = Dash):
    with db.session() as s:
        return sv.export_config(s)


@app.get("/api/config/export", response_class=PlainTextResponse)
def config_export(p: Principal = Dash):
    with db.session() as s:
        return yaml.safe_dump(sv.export_config(s), allow_unicode=True, sort_keys=False)


@app.get("/api/config/{scope}")
@app.get("/api/config/{scope}/{sid}")
def config_get(scope: str, sid: str = "", p: Principal = Dash):
    if scope not in sv.SCOPES:
        raise HTTPException(404, "ismeretlen scope")
    with db.session() as s:
        return {"current": _current(s, scope, sid),
                "history": [ser.row(v) for v in sv.config_history(s, scope, sid)]}


@app.post("/api/config/{scope}/validate")
def config_validate(scope: str, body: ConfigIn, p: Principal = Dash):
    if scope not in sv.SCOPES:
        raise HTTPException(404, "ismeretlen scope")
    try:
        cfg = sv.SCOPES[scope].model_validate(body.data).model_dump(mode="json")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    with db.session() as s:
        cur = _current(s, scope, getattr(sv.SCOPES[scope].model_validate(body.data), "id", "") or "") or {}
    return {"ok": True, "normalized": cfg, "diff": sv.config_diff(cur, cfg)}


@app.post("/api/config/{scope}")
def config_save(scope: str, body: ConfigIn, p: Principal = Dash):
    with db.session() as s:
        v = sv.save_config(s, p, scope, body.data, body.comment)
        if scope == "global":
            apply_log_level(v.config.get("log_level", "INFO"))
        created = scope == "project" and not s.scalar(select(func.count()).select_from(auth.ApiKey).where(
            auth.ApiKey.project_id == v.scope_id, auth.ApiKey.revoked_at.is_(None)))
        out = ser.row(v)
        out["restart_required"] = getattr(v, "restart_notice", None)
        if created:  # új projekt: Worker-kulcs egyszer, a hostnak (agenthub project key is kiadja)
            out["worker_key"] = auth.issue(s, "worker", v.scope_id)
        return out


class RollbackIn(BaseModel):
    scope_id: str = ""
    version: int


@app.post("/api/config/{scope}/rollback")
def config_rollback(scope: str, body: RollbackIn, p: Principal = Dash):
    with db.session() as s:
        v = sv.rollback_config(s, p, scope, body.scope_id, body.version)
        return {**ser.row(v), "restart_required": getattr(v, "restart_notice", None)}


@app.post("/api/config-import")
async def config_import(request: Request, p: Principal = Dash):
    raw = (await request.body()).decode()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"hibás YAML/JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(400, "a konfiguráció egy objektum")

    def run():
        with db.session() as s:
            return sv.import_config(s, p, data)
    return await run_in_threadpool(run)


class ControlIn(BaseModel):
    name: str
    state: str


@app.post("/api/control")
def control(body: ControlIn, p: Principal = Dash):
    with db.session() as s:
        sv.set_control(s, p, body.name, body.state)
    return {"ok": True}


class FlagIn(BaseModel):
    value: bool


@app.post("/api/outbound-approval")
def outbound_approval(body: FlagIn, p: Principal = Dash):
    """Dashboard-kapcsoló: a Coordinator és a Workerek kimenő írása csak jóváhagyással (új konfigverzióként mentve)."""
    with db.session() as s:
        g = sv.global_config(s).model_dump(mode="json")
        if g["outbound_approval"] != body.value:
            g["outbound_approval"] = body.value
            sv.save_config(s, p, "global", g, "kimenő jóváhagyás " + ("be" if body.value else "ki"))
    return {"value": body.value}


# ---------------------------------------------------------------- statikus Dashboard

if STATIC.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (STATIC / path).resolve()
        if path and f.is_file() and STATIC in f.parents:
            return FileResponse(f)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
