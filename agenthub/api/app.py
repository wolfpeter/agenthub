"""AgentHub REST API + SSE. Minden végpont szerveroldalon ellenőrzi a szerepet és a projektet (SPECIFICATION 16, 18)."""
import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
import time
from datetime import timedelta
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select, text

from .. import __version__, auth, db, serialize as ser, services as sv, states, writers
from ..auth import Principal
from ..events import bus
from ..models import (AgentInstance, AgentRun, Installation, PendingAction, Project, ProjectKnowledge, Source,
                      Ticket, TicketAnswer, TicketEvent, TicketQuestion, now)

log = logging.getLogger("agenthub")
COOKIE = "agenthub_key"
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app):
    await run_in_threadpool(_startup)
    yield


app = FastAPI(title="AgentHub", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)



@app.exception_handler(sv.Forbidden)
async def _forbidden(_, e):
    return JSONResponse({"detail": str(e)}, 403)


@app.exception_handler(sv.Conflict)
async def _conflict(_, e):
    return JSONResponse({"detail": str(e)}, 409)


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
    return {"role": p.role, "project_id": p.project_id}


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
        projects = []
        for pr in s.scalars(select(Project).order_by(Project.id)):
            d = ser.row(pr, exclude=("inventory", "config"))
            d["worker_control"] = sv.control_state(s, f"worker:{pr.id}")
            d["inventory_count"] = len((pr.inventory or {}).get("files", []))
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
        agents = []
        for a in s.scalars(select(AgentInstance).order_by(AgentInstance.last_heartbeat.desc())):
            if a.status == "running" or a.last_heartbeat >= since:
                d = ser.row(a)
                d["alive"] = sv.instance_alive(s, gc, a)
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
            "controls": {"coordinator": sv.control_state(s, "coordinator")},
            "usage_24h": {"runs": usage[0], "tokens_in": usage[1], "tokens_out": usage[2], "cost_usd": usage[3]},
            "coordinator_poll_interval_s": gc.coordinator_poll_interval_s,
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
    return {"ticket": d, "resumed": resumed, "model": model, "escalate_to": gc.models.escalate_to}


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
                "memory": [{"id": k.id, "category": k.category, "title": k.title} for k in mem]}


class NoteIn(BaseModel):
    text: str


@app.post("/api/tickets/{tid}/note")
def ticket_note(tid: int, body: NoteIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.note(s, p, tid, body.text, instance(request))
    return {"ok": True}


class AskIn(BaseModel):
    question: str
    context: str = ""
    suggested_answer: str | None = None


@app.post("/api/tickets/{tid}/ask")
def ticket_ask(tid: int, body: AskIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        q = sv.ask(s, p, tid, body.question, body.context, body.suggested_answer, instance(request))
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


@app.post("/api/tickets/{tid}/finish")
def ticket_finish(tid: int, body: FinishIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.finish(s, p, tid, body.summary, body.payload, body.run.model_dump() if body.run else None, instance(request))
    return {"ok": True}


class FailIn(BaseModel):
    error: str
    run: RunIn | None = None


@app.post("/api/tickets/{tid}/fail")
def ticket_fail(tid: int, body: FailIn, request: Request, p: Principal = Worker):
    with db.session() as s:
        sv.fail(s, p, tid, body.error, body.run.model_dump() if body.run else None, instance(request))
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
                              instance(request))
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
    """Jóváhagyás után az író adapter a háttérben hajtja végre (SPECIFICATION 10)."""
    def run():
        with db.session() as s:
            a = s.scalar(select(PendingAction).where(PendingAction.id == aid).with_for_update())
            if not a or a.status != "APPROVED" or a.approved_hash != sv.phash(a.proposed_payload):
                return
            cfg = sv.writer_config(s)
            try:
                res = writers.execute(a.target_system, a.action_type, a.target_reference, a.proposed_payload, cfg)
                sv.record_execution(s, auth.SYSTEM, a, True, res)
            except writers.WriteError as e:
                if "nincs beállítva" in str(e):
                    a.execution_result = {"info": str(e)}  # marad APPROVED: kézi végrehajtásra vár
                    sv.changed(what="actions")
                else:
                    sv.record_execution(s, auth.SYSTEM, a, False, {"error": str(e)})
    threading.Thread(target=run, daemon=True).start()


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
                "cli_executables": gc.cli_executables,
                "sources": [ser.row(x) for x in s.scalars(select(Source).where(Source.is_active.is_(True)))],
                "projects": [{"id": x.id, "name": x.name} for x in s.scalars(select(Project).where(Project.is_active.is_(True)))]}


@app.get("/api/coordinator/pending")
def coordinator_pending(p: Principal = Coord):
    with db.session() as s:
        return [ser.ticket(t, brief=True) for t in sv.coordinator_pending(s)]


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
        created = scope == "project" and not s.scalar(select(func.count()).select_from(auth.ApiKey).where(
            auth.ApiKey.project_id == v.scope_id, auth.ApiKey.revoked_at.is_(None)))
        out = ser.row(v)
        if created:  # új projekt: Worker-kulcs egyszer, a hostnak (agenthub project key is kiadja)
            out["worker_key"] = auth.issue(s, "worker", v.scope_id)
        return out


class RollbackIn(BaseModel):
    scope_id: str = ""
    version: int


@app.post("/api/config/{scope}/rollback")
def config_rollback(scope: str, body: RollbackIn, p: Principal = Dash):
    with db.session() as s:
        return ser.row(sv.rollback_config(s, p, scope, body.scope_id, body.version))


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


# ---------------------------------------------------------------- statikus Dashboard

if STATIC.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (STATIC / path).resolve()
        if path and f.is_file() and STATIC in f.parents:
            return FileResponse(f)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
