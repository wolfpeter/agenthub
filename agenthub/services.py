"""Üzleti logika. Minden írás itt megy át, hogy az állapotgép, a jogosultság és az eseménynapló egy helyen éljen."""
import difflib
import hashlib
import json
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from . import states
from .auth import SYSTEM, Principal
from .configschema import (FullConfig, GlobalConfig, ProjectConfig, SourceConfig, WriterConfig)
from .events import bus
from .models import (AgentInstance, AgentRun, AuditEvent, ConfigVersion, Control, PendingAction, Project,
                     ProjectKnowledge, Source, SourceEvent, Ticket, TicketAnswer, TicketEvent, TicketQuestion,
                     TicketSourceRef, now)


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(ValueError):
    pass


class Conflict(Exception):
    def __init__(self, msg: str, code: str = "conflict"):
        super().__init__(msg)
        self.code = code  # lease_expired: a CLI magától visszakérheti; lease_lost: átvették, nem


def phash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def audit(s, who: Principal, action: str, rtype: str, rid=None, payload=None):
    s.add(AuditEvent(actor=who.actor_id, action=action, resource_type=rtype,
                     resource_id=None if rid is None else str(rid), payload=payload))


def changed(kind="change", **data):
    bus.publish(kind, data)


# ---------------------------------------------------------------- konfiguráció

def _latest(s, scope: str, scope_id: str = "") -> ConfigVersion | None:
    return s.scalar(select(ConfigVersion).where(ConfigVersion.scope == scope, ConfigVersion.scope_id == scope_id)
                    .order_by(ConfigVersion.version.desc()).limit(1))


def global_config(s) -> GlobalConfig:
    v = _latest(s, "global")
    return GlobalConfig.model_validate(v.config) if v else GlobalConfig()


def writer_config(s) -> WriterConfig:
    v = _latest(s, "writers")
    return WriterConfig.model_validate(v.config) if v else WriterConfig()


SCOPES = {"global": GlobalConfig, "writers": WriterConfig, "project": ProjectConfig, "source": SourceConfig}


def save_config(s, who: Principal, scope: str, data: dict, comment: str | None = None) -> ConfigVersion:
    if who.role != "dashboard":
        raise Forbidden("konfigurációt csak a Dashboard-kulccsal lehet módosítani")
    if scope not in SCOPES:
        raise Invalid(f"ismeretlen scope: {scope}")
    try:
        cfg = SCOPES[scope].model_validate(data)
    except Exception as e:  # pydantic ValidationError -> olvasható hiba, nem aktiválódik
        raise Invalid(str(e)) from e
    scope_id = getattr(cfg, "id", "") or ""
    if scope == "source" and cfg.project and not s.get(Project, cfg.project):
        raise Invalid(f"ismeretlen projekt: {cfg.project}")
    if scope == "source" and cfg.adapter_type == "cli":
        exe = cfg.settings.get("executable")
        if exe not in global_config(s).cli_executables:
            raise Invalid(f"a(z) '{exe}' program nincs a global.cli_executables engedélyezett listában")
    if scope == "global":
        known = {p for (p,) in s.execute(select(Project.id))}
        bad = [r.project for r in cfg.routing_rules if r.project not in known]
        if bad:
            raise Invalid(f"ismeretlen projekt a besorolási szabályban: {bad}")
    if scope == "project":
        _check_project_paths(s, cfg)
    body = cfg.model_dump(mode="json")
    last = _latest(s, scope, scope_id)
    if last and last.config == body:
        return last
    prev = last.config if last else (SCOPES[scope]().model_dump(mode="json") if scope in ("global", "writers") else None)
    notice = restart_notice(scope, scope_id, prev, body)
    v = ConfigVersion(scope=scope, scope_id=scope_id, version=(last.version + 1 if last else 1), config=body,
                      comment=comment, created_by=who.actor_id)
    s.add(v)
    if scope == "project":
        p = s.get(Project, cfg.id) or Project(id=cfg.id)
        p.name, p.working_directory, p.repository = cfg.name, cfg.working_directory, cfg.repository
        p.default_branch, p.is_active, p.config, p.config_version = cfg.default_branch, cfg.is_active, body, v.version
        s.add(p)
    elif scope == "source":
        src = s.get(Source, cfg.id) or Source(id=cfg.id)
        src.name, src.adapter_type, src.is_active = cfg.name, cfg.adapter_type, cfg.is_active
        src.poll_interval_s, src.config, src.config_version = cfg.poll_interval_s, body, v.version
        s.add(src)
    s.flush()
    audit(s, who, "config.save", scope, scope_id or None, {"version": v.version, "comment": comment})
    changed(what="config")
    v.restart_notice = notice  # nem DB-mező: a válaszban jelezzük, ha a futó sessiont újra kell indítani
    return v


def restart_notice(scope: str, scope_id: str, old: dict | None, new: dict) -> str | None:
    """A legtöbb beállítást az agent a következő hívásnál átveszi; ezek a session indításakor rögzülnek (SPECIFICATION 12.3)."""
    if old is None:
        return None
    if scope == "project":
        ow, nw = old.get("worker", {}), new.get("worker", {})
        if any(ow.get(k) != nw.get(k) for k in ("permission_mode", "allowed_tools", "denied_tools", "hooks")) \
                or old.get("working_directory") != new.get("working_directory"):
            return (f"A Worker jogosultságai változtak. Indítsd újra a(z) {scope_id} Worker Claude-sessionjét az "
                    f"`agenthub worker command --project {scope_id}` által kiírt paranccsal. Ha a könyvtár változott, "
                    "a többi projekt Workerét is.")
    if scope == "global" and any(old.get(k) != new.get(k) for k in ("outbound_approval", "outbound_tools")):
        return ("A kimenő kommunikáció beállítása változott: a futó Coordinator és Workerek a következő körben leállnak; "
                "indítsd újra őket az `agenthub coordinator command`, illetve `agenthub worker command` által kiírt paranccsal.")
    if scope == "global" and any(old.get(k) != new.get(k) for k in (
            "coordinator_model", "coordinator_allowed_tools", "coordinator_denied_tools", "coordinator_permission_mode")):
        return ("A Coordinator modellje vagy jogosultságai változtak: az `agenthub coordinator command` paranccsal "
                "indítsd újra a sessiont.")
    return None


def _check_project_paths(s, cfg: ProjectConfig):
    """Projektek working directoryja nem lehet egymásba ágyazva (izoláció, SPECIFICATION 18)."""
    mine = cfg.working_directory + "/"
    for p in s.scalars(select(Project).where(Project.id != cfg.id)):
        other = p.working_directory + "/"
        if mine.startswith(other) or other.startswith(mine):
            raise Invalid(f"a working_directory átfed a(z) {p.id} projektével: {p.working_directory}")


def config_history(s, scope: str, scope_id: str = "") -> list[ConfigVersion]:
    return list(s.scalars(select(ConfigVersion).where(ConfigVersion.scope == scope, ConfigVersion.scope_id == scope_id)
                          .order_by(ConfigVersion.version.desc())))


def config_diff(a: dict, b: dict) -> str:
    import yaml
    da = yaml.safe_dump(a, allow_unicode=True, sort_keys=True).splitlines()
    db = yaml.safe_dump(b, allow_unicode=True, sort_keys=True).splitlines()
    return "\n".join(difflib.unified_diff(da, db, "előző", "új", lineterm=""))


def rollback_config(s, who: Principal, scope: str, scope_id: str, version: int) -> ConfigVersion:
    old = s.scalar(select(ConfigVersion).where(ConfigVersion.scope == scope, ConfigVersion.scope_id == scope_id,
                                               ConfigVersion.version == version))
    if not old:
        raise NotFound("nincs ilyen konfigurációverzió")
    return save_config(s, who, scope, old.config, comment=f"visszaállítás: v{version}")


def export_config(s) -> dict:
    data = FullConfig(global_=global_config(s), writers=writer_config(s),
                      projects=[ProjectConfig.model_validate(p.config) for p in s.scalars(select(Project).order_by(Project.id)) if p.config],
                      sources=[SourceConfig.model_validate(x.config) for x in s.scalars(select(Source).order_by(Source.id)) if x.config])
    return data.model_dump(mode="json", by_alias=True)


def import_config(s, who: Principal, data: dict) -> dict:
    try:
        full = FullConfig.model_validate(data)
    except Exception as e:
        raise Invalid(str(e)) from e
    out = {"projects": [], "sources": []}
    # a sorrend számít: projektek előbb, mert a szabályok és források rájuk hivatkoznak
    for p in full.projects:
        save_config(s, who, "project", p.model_dump(mode="json"), "import")
        out["projects"].append(p.id)
    save_config(s, who, "global", full.global_.model_dump(mode="json"), "import")
    save_config(s, who, "writers", full.writers.model_dump(mode="json"), "import")
    for src in full.sources:
        save_config(s, who, "source", src.model_dump(mode="json"), "import")
        out["sources"].append(src.id)
    return out


def project_config(p: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(p.config) if p.config else ProjectConfig(
        id=p.id, name=p.name, working_directory=p.working_directory)


# ---------------------------------------------------------------- kapcsolók

def control_state(s, name: str) -> str:
    c = s.get(Control, name)
    return c.state if c else "run"


def set_control(s, who: Principal, name: str, state: str):
    if who.role != "dashboard":
        raise Forbidden("a kapcsolót csak a Dashboardról lehet állítani")
    if state not in ("run", "stop") or not (name == "coordinator" or name.startswith("worker:")):
        raise Invalid("hibás kapcsoló")
    c = s.get(Control, name) or Control(name=name)
    c.state, c.updated_at, c.updated_by = state, now(), who.actor_id
    s.add(c)
    audit(s, who, "control.set", "control", name, {"state": state})
    changed(what="control")


# ---------------------------------------------------------------- ticketek

def event(s, t: Ticket, who: Principal, etype: str, old=None, new=None, payload=None):
    s.add(TicketEvent(ticket_id=t.id, event_type=etype, old_status=old, new_status=new, actor_type=who.actor,
                      actor_id=who.actor_id, payload=payload))


def get_ticket(s, who: Principal, tid: int, lock=False) -> Ticket:
    q = select(Ticket).where(Ticket.id == tid)
    if lock:
        q = q.with_for_update()
    t = s.scalar(q)
    if not t:
        raise NotFound(f"nincs #{tid} ticket")
    if who.role == "worker" and t.project_id != who.project_id:
        # másik projekt ticketje: a Worker számára nem létezik (SPECIFICATION 18)
        raise NotFound(f"nincs #{tid} ticket")
    return t


REOPEN_FROM = ("REVIEW", "AWAITING_RELEASE", "COMPLETED", "BLOCKED", "FAILED")
HOTFIX_VOID_FROM = ("REVIEW", "AWAITING_RELEASE", "COMPLETED")  # a leszállított munka újranyitása; retry, feloldás nem


def _move(s, t: Ticket, new: str, who: Principal, etype="status", payload=None):
    old = t.status
    states.check(old, new, who.actor)
    if new == "READY" and not t.project_id:
        raise Invalid("projekt nélkül a ticket nem lehet READY")
    if old == "IN_PROGRESS" and new != "IN_PROGRESS":
        t.lease_expires_at = None
        if new != "PENDING_APPROVAL":  # ugyanabban a körben a lease birtokosa további műveletet javasolhat
            t.current_lease_id = None
        s.execute(update(AgentInstance).where(AgentInstance.active_ticket_id == t.id).values(active_ticket_id=None))
    if t.hotfix and (new in states.FINAL or (new == "READY" and old in HOTFIX_VOID_FROM)):
        # a hotfix egy szállításra szóló élesítési engedély: lezárás vagy újranyitás után nem él tovább
        t.hotfix, t.hotfix_by, t.hotfix_at = False, None, None
        event(s, t, who, "hotfix_off", payload={"reason": f"{old} → {new}"})
    t.status, t.updated_at = new, now()
    event(s, t, who, etype, old, new, payload)
    s.flush()
    changed(ticket=t.id)


def create_ticket(s, who: Principal, data: dict) -> Ticket:
    if who.role not in ("dashboard", "coordinator"):
        raise Forbidden("ticketet a Dashboard vagy a Coordinator hozhat létre")
    title = (data.get("title") or "").strip()
    if not title:
        raise Invalid("a cím kötelező")
    pid = data.get("project_id") or None
    if pid and not s.get(Project, pid):
        raise Invalid(f"ismeretlen projekt: {pid}")
    prio = int(data.get("priority") or 2)
    if prio not in (1, 2, 3):
        raise Invalid("a prioritás 1, 2 vagy 3")
    check_type(s, pid, data.get("type") or "task")
    t = Ticket(project_id=pid, title=title[:500], description=data.get("description") or "",
               type=data.get("type") or "task", priority=prio, status="NEW", source_url=data.get("source_url"),
               created_by=who.actor)
    s.add(t)
    s.flush()
    event(s, t, who, "created", None, "NEW")
    if who.role == "dashboard":
        if pid:
            _move(s, t, "READY", who)
        if data.get("start_approved"):
            set_go(s, who, t, True)
    s.flush()
    changed(ticket=t.id)
    return t


def set_go(s, who: Principal, t: Ticket, value: bool, reason: str | None = None):
    """Mehet jelölés (SPECIFICATION 8.2): ember be/ki, Coordinator csak be és csak indoklással, Worker semmi."""
    reason = (reason or "").strip() or None
    if who.role == "worker":
        raise Forbidden("a Worker nem módosíthatja a Mehet jelölést")
    if who.role == "coordinator":
        if not value:
            raise Forbidden("a Coordinator csak bekapcsolhatja a Mehet jelölést")
        if not reason:
            raise Invalid("a Coordinator csak indoklással kapcsolhatja be a Mehet jelölést")
    if t.status in states.FINAL:
        raise Invalid("lezárt ticket jelölése nem módosítható")
    if t.start_approved == value:
        return
    t.start_approved = value
    t.start_approved_by = who.actor if value else None
    t.start_approved_reason = reason if value else None
    t.updated_at = now()
    event(s, t, who, "go_on" if value else "go_off", payload={"reason": reason} if reason else None)
    audit(s, who, "ticket.go", "ticket", t.id, {"value": value, "reason": reason})
    s.flush()
    changed(ticket=t.id)


def set_hotfix(s, who: Principal, t: Ticket, value: bool):
    """Hotfix = élesítési engedély egy szállításra: csak ember adhatja, és Mehet + P1 jár vele."""
    if who.role != "dashboard":
        raise Forbidden("hotfixet csak ember jelölhet a Dashboardon")
    if t.status in states.FINAL:
        raise Invalid("lezárt ticket nem jelölhető hotfixnek")
    if t.hotfix == value:
        return
    t.hotfix, t.hotfix_by, t.hotfix_at = value, (who.actor_id or who.actor) if value else None, now() if value else None
    if value:
        t.priority = 1
        if not t.start_approved:
            set_go(s, who, t, True)
    t.updated_at = now()
    event(s, t, who, "hotfix_on" if value else "hotfix_off")
    audit(s, who, "ticket.hotfix", "ticket", t.id, {"value": value})
    s.flush()
    changed(ticket=t.id)


def check_type(s, project_id: str | None, ttype: str | None):
    """A projekt engedélyezett ticket-típusai (SPECIFICATION 12.1)."""
    if not ttype or not project_id:
        return
    pr = s.get(Project, project_id)
    allowed = project_config(pr).ticket_types if pr else []
    if allowed and ttype not in allowed:
        raise Invalid(f"a(z) {project_id} projektben nem engedélyezett típus: {ttype} (engedélyezett: {', '.join(allowed)})")


def update_ticket(s, who: Principal, t: Ticket, data: dict):
    if who.role != "dashboard":
        raise Forbidden("csak a Dashboardról szerkeszthető")
    fields = {}
    for k in ("title", "description", "type"):
        if k in data and data[k] is not None and data[k] != getattr(t, k):
            fields[k] = data[k]
    if "priority" in data and data["priority"] is not None:
        p = int(data["priority"])
        if p not in (1, 2, 3):
            raise Invalid("a prioritás 1, 2 vagy 3")
        if p != t.priority:
            fields["priority"] = p
    check_type(s, t.project_id, fields.get("type"))
    for k, v in fields.items():
        setattr(t, k, v)
    if fields:
        t.updated_at = now()
        event(s, t, who, "edited", payload={k: (v if k != "description" else "…") for k, v in fields.items()})
        changed(ticket=t.id)


def human_transition(s, who: Principal, t: Ticket, action: str, note: str | None = None, project_id: str | None = None):
    """Dashboard-műveletek: accept, return, retry, triage, ready, cancel."""
    if who.role != "dashboard":
        raise Forbidden("ez emberi döntés, csak a Dashboardról")
    payload = {"note": note} if note else None
    if action == "accept":
        _move(s, t, "COMPLETED", who, payload=payload)
    elif action == "return":
        if not note:
            raise Invalid("visszaküldéshez megjegyzés kell")
        _move(s, t, "READY", who, "returned", payload)
    elif action == "retry":
        _move(s, t, "READY", who, "retry", payload)
    elif action in ("triage", "ready"):
        if project_id:
            if not s.get(Project, project_id):
                raise Invalid(f"ismeretlen projekt: {project_id}")
            t.project_id = project_id
        if not t.project_id:
            raise Invalid("projekt megadása kötelező")
        _move(s, t, "READY", who, "triaged", {"project": t.project_id, **(payload or {})})
    elif action == "cancel":
        open_actions = s.scalars(select(PendingAction).where(PendingAction.ticket_id == t.id,
                                                             PendingAction.status.in_(("PENDING_APPROVAL", "DRAFT"))))
        for a in open_actions:
            a.status = "CANCELLED"
        _move(s, t, "CANCELLED", who, payload=payload)
    else:
        raise Invalid(f"ismeretlen művelet: {action}")


def classify(s, who: Principal, t: Ticket, project_id: str | None, ttype: str | None, priority: int | None,
             triage: bool = False, reason: str | None = None, go: bool = False, go_reason: str | None = None):
    """A Coordinator modelldöntése egy NEW ticketről."""
    if who.role not in ("coordinator", "dashboard"):
        raise Forbidden("besorolást a Coordinator végez")
    if t.status != "NEW":
        raise Invalid(f"csak NEW ticket sorolható be (most: {t.status})")
    if ttype:
        t.type = ttype
    if priority:
        if priority not in (1, 2, 3):
            raise Invalid("a prioritás 1, 2 vagy 3")
        t.priority = priority
    if triage or not project_id:
        _move(s, t, "NEEDS_TRIAGE", who, "classified", {"reason": reason})
        return
    if not s.get(Project, project_id):
        raise Invalid(f"ismeretlen projekt: {project_id}")
    check_type(s, project_id, t.type)
    t.project_id = project_id
    _move(s, t, "READY", who, "classified", {"project": project_id, "reason": reason})
    if go:
        set_go(s, who, t, True, go_reason)


# ---------------------------------------------------------------- Worker

def instance_alive(s, gc: GlobalConfig, inst: AgentInstance) -> bool:
    """Él, ha friss a jele, vagy érvényes lease-t tart (hosszú ticketmunka közben nincs CLI-hívás)."""
    if inst.status != "running":
        return False
    if (now() - inst.last_heartbeat).total_seconds() < gc.agent_stale_after_s:
        return True
    if inst.active_ticket_id:
        t = s.get(Ticket, inst.active_ticket_id)
        return bool(t and t.status == "IN_PROGRESS" and t.assigned_worker_id == inst.id
                    and t.lease_expires_at and t.lease_expires_at > now())
    return False


def register_agent(s, who: Principal, instance_id: str, session_id: str | None, working_directory: str | None,
                   model: str | None, takeover: bool = False) -> AgentInstance:
    if who.role not in ("worker", "coordinator"):
        raise Forbidden("csak agent regisztrálhat")
    gc = global_config(s)
    q = select(AgentInstance).where(AgentInstance.role == who.role, AgentInstance.id != instance_id,
                                    AgentInstance.status == "running").with_for_update()
    q = q.where(AgentInstance.project_id == who.project_id) if who.project_id else q.where(AgentInstance.project_id.is_(None))
    for other in s.scalars(q):
        if instance_alive(s, gc, other) and not takeover:
            raise Invalid(f"már fut egy {who.role} ({other.id}, utolsó jel: {other.last_heartbeat:%H:%M:%S}). "
                          "Ha az már nem él, indítsd --takeover kapcsolóval.")
        other.status = "stopped"
    if who.role == "worker":
        p = s.get(Project, who.project_id)
        pc = project_config(p)
        if not p.is_active or not pc.worker.enabled:
            raise Invalid("ennél a projektnél a Worker ki van kapcsolva")
        if working_directory and os.path.realpath(working_directory) != os.path.realpath(p.working_directory):
            raise Invalid(f"a Worker nem a projekt könyvtárában fut ({p.working_directory})")
    inst = s.get(AgentInstance, instance_id) or AgentInstance(id=instance_id, role=who.role, project_id=who.project_id)
    inst.status, inst.last_heartbeat, inst.session_id = "running", now(), session_id
    inst.working_directory, inst.model = working_directory, model
    s.add(inst)
    s.flush()
    changed(what="agents")
    return inst


def heartbeat(s, who: Principal, instance_id: str | None, ticket: Ticket | None = None):
    if not instance_id:
        return
    inst = s.get(AgentInstance, instance_id)
    if inst and inst.role == who.role and inst.project_id == who.project_id:
        inst.last_heartbeat = now()
        if inst.status != "running":
            inst.status = "running"
    if ticket is not None and ticket.status == "IN_PROGRESS" and who.role == "worker":
        ticket.lease_expires_at = now() + timedelta(seconds=global_config(s).lease_ttl_s)


def stop_agent(s, who: Principal, instance_id: str):
    inst = s.get(AgentInstance, instance_id)
    if inst and inst.role == who.role and inst.project_id == who.project_id:
        inst.status = "stopped"
        changed(what="agents")


def _queue(project_id: str):
    return (select(Ticket).where(Ticket.project_id == project_id, Ticket.status == "READY", Ticket.start_approved.is_(True))
            .order_by(Ticket.hotfix.desc(), Ticket.priority, Ticket.id))


def peek_ticket(s, who: Principal) -> Ticket | None:
    """A következő jelölt lefoglalás nélkül (a helyi kapu, pl. kvótafigyelés ennek alapján dönt)."""
    if who.role != "worker":
        raise Forbidden("csak Worker")
    return s.scalar(select(Ticket).where(Ticket.project_id == who.project_id, Ticket.status == "IN_PROGRESS")) \
        or s.scalar(_queue(who.project_id).limit(1))


def next_ticket(s, who: Principal, instance_id: str, session_id: str | None = None) -> tuple[Ticket | None, bool]:
    """Folytatandó vagy új ticket a saját projektből. Visszatér: (ticket, resumed)."""
    if who.role != "worker":
        raise Forbidden("ticketet csak Worker vehet fel")
    if control_state(s, f"worker:{who.project_id}") == "stop":
        return None, False
    p = s.get(Project, who.project_id)
    pc = project_config(p)
    gc = global_config(s)
    cur = s.scalar(select(Ticket).where(Ticket.project_id == who.project_id, Ticket.status == "IN_PROGRESS")
                   .with_for_update())
    if cur:
        if not pc.worker.auto_resume:
            cur.last_error = "megszakadt feldolgozás; a projekt beállítása szerint nem folytatódik automatikusan"
            _move(s, cur, "FAILED", SYSTEM, "interrupted")
            return None, False
        valid = bool(cur.lease_expires_at and cur.lease_expires_at > now())
        if valid and cur.assigned_worker_id == instance_id:  # ugyanaz a Worker kérdez újra: csak a lease hosszabbodik
            cur.lease_expires_at = now() + timedelta(seconds=gc.lease_ttl_s)
            return cur, True
        if valid:  # más példány érvényes lease-szel dolgozik rajta: nem vesszük el
            return None, False
        old_lease = cur.current_lease_id
        cur.assigned_worker_id, cur.current_lease_id = instance_id, uuid.uuid4().hex
        cur.lease_expires_at = now() + timedelta(seconds=gc.lease_ttl_s)
        _move(s, cur, "IN_PROGRESS", who, "resumed", {"previous_lease": old_lease, "instance": instance_id})
        _mark_active(s, instance_id, cur.id)
        return cur, True
    t = s.scalar(_queue(who.project_id).limit(1).with_for_update(skip_locked=True))
    if not t:
        return None, False
    t.assigned_worker_id, t.current_lease_id = instance_id, uuid.uuid4().hex
    t.lease_expires_at = now() + timedelta(seconds=gc.lease_ttl_s)
    if session_id:
        t.claude_session_id = session_id
    _move(s, t, "IN_PROGRESS", who, "claimed", {"instance": instance_id, "session": session_id})
    _mark_active(s, instance_id, t.id)
    return t, False


def _mark_active(s, instance_id, tid):
    inst = s.get(AgentInstance, instance_id)
    if inst:
        inst.active_ticket_id = tid


def _fence(t: Ticket, instance_id: str | None, lease_id: str | None, check_expiry: bool = True):
    """Csak az érvényes lease birtokosa írhat: példány + lease-azonosító + nem lejárt. Különben 409,
    és a Worker `agenthub worker wait`-tel kérje vissza a ticketet (ha senki nem vette át, új lease-szel visszakapja)."""
    if not instance_id or t.assigned_worker_id != instance_id or not lease_id or t.current_lease_id != lease_id:
        raise Conflict(f"a #{t.id} ticketet nem ez a Worker-példány/lease tartja (átvették); kérj új ticketet: agenthub worker wait",
                       "lease_lost")
    if check_expiry and (not t.lease_expires_at or t.lease_expires_at <= now()):
        raise Conflict(f"a #{t.id} ticket lease-e lejárt; kérd vissza: agenthub worker wait", "lease_expired")


def _own_in_progress(s, who: Principal, tid: int, instance_id: str | None, lease_id: str | None) -> Ticket:
    t = get_ticket(s, who, tid, lock=True)
    if who.role != "worker":
        raise Forbidden("csak a Worker")
    if t.status != "IN_PROGRESS":
        raise Invalid(f"a #{tid} ticket nincs feldolgozás alatt (most: {t.status})")
    _fence(t, instance_id, lease_id)
    return t


def note(s, who: Principal, tid: int, text: str, instance_id: str | None = None, lease_id: str | None = None):
    t = _own_in_progress(s, who, tid, instance_id, lease_id)
    event(s, t, who, "note", payload={"text": text[:4000]})
    heartbeat(s, who, instance_id, t)
    t.updated_at = now()
    changed(ticket=t.id)


def ask(s, who: Principal, tid: int, question: str, context: str = "", suggested: str | None = None,
        instance_id: str | None = None, lease_id: str | None = None):
    t = _own_in_progress(s, who, tid, instance_id, lease_id)
    if not question.strip():
        raise Invalid("üres kérdés")
    q = TicketQuestion(ticket_id=t.id, question=question.strip(), context=context or "", suggested_answer=suggested,
                       created_by=who.actor)
    s.add(q)
    s.flush()
    _move(s, t, "WAITING_FOR_INPUT", who, "question", {"question_id": q.id, "question": q.question})
    return q


def answer(s, who: Principal, qid: int, text: str) -> TicketQuestion:
    if who.role != "dashboard":
        raise Forbidden("választ ember ad")
    q = s.get(TicketQuestion, qid)
    if not q:
        raise NotFound("nincs ilyen kérdés")
    if q.status == "ANSWERED":
        raise Invalid("erre a kérdésre már érkezett válasz")
    if not text.strip():
        raise Invalid("üres válasz")
    s.add(TicketAnswer(question_id=q.id, answer=text.strip(), created_by=who.actor))
    q.status = "ANSWERED"
    t = s.get(Ticket, q.ticket_id)
    event(s, t, who, "answer", payload={"question_id": q.id, "answer": text.strip()})
    s.flush()
    still_open = s.scalar(select(func.count()).select_from(TicketQuestion)
                          .where(TicketQuestion.ticket_id == t.id, TicketQuestion.status == "OPEN"))
    if t.status == "WAITING_FOR_INPUT" and not still_open:
        _move(s, t, "READY", SYSTEM, "answered")
    changed(ticket=t.id)
    return q


FINISH_TO = {"review": "REVIEW", "release": "AWAITING_RELEASE", "done": "COMPLETED"}


def finish(s, who: Principal, tid: int, summary: str, payload: dict | None, run: dict | None, instance_id=None, lease_id=None,
           to: str = "review"):
    """review: ember veszi át · release: kész, élesítésre vár · done: kész (csak ha a projekt nem kér review-t)."""
    t = _own_in_progress(s, who, tid, instance_id, lease_id)
    if not summary.strip():
        raise Invalid("az eredmény összefoglalója kötelező")
    if to not in FINISH_TO:
        raise Invalid(f"ismeretlen lezárás: {to} (review, release, done)")
    if to == "done" and project_config(s.get(Project, t.project_id)).worker.review_required:
        raise Invalid("ebben a projektben a lezárást ember veszi át: --to review vagy release")
    t.result_summary, t.result_payload, t.last_error = summary.strip(), payload, None
    _record_run(s, who, t, run, to, instance_id)
    _move(s, t, FINISH_TO[to], who, "finished", {"summary": summary.strip()[:2000], "to": to})


def block(s, who: Principal, tid: int, reason: str, instance_id=None, lease_id=None):
    """Harmadik félre vár (megrendelő, külső fél). Emberi döntéshez a kérdés (ask) való."""
    t = _own_in_progress(s, who, tid, instance_id, lease_id)
    if not reason.strip():
        raise Invalid("meg kell adni, mire vár")
    t.last_error = reason.strip()
    _move(s, t, "BLOCKED", who, "blocked", {"reason": reason.strip()[:2000]})


def release_list(s, who: Principal) -> list[Ticket]:
    if who.role != "worker":
        raise Forbidden("csak a projekt kulcsával")
    return list(s.scalars(select(Ticket).where(Ticket.project_id == who.project_id, Ticket.status == "AWAITING_RELEASE")
                          .order_by(Ticket.hotfix.desc(), Ticket.priority, Ticket.id)))


def release_done(s, who: Principal, tid: int, summary: str, failed: bool = False):
    """Élesítés után: lease nélkül, mert a kiadás nem egy ticket feldolgozása. failed: prodon hibás → vissza P1-gyel."""
    if who.role != "worker":
        raise Forbidden("csak a projekt kulcsával")
    t = get_ticket(s, who, tid, lock=True)
    if t.status != "AWAITING_RELEASE":
        raise Invalid(f"a #{tid} ticket nem élesítésre vár (most: {t.status})")
    if not summary.strip():
        raise Invalid("az élesítés eredménye kötelező")
    if failed:
        t.priority, t.last_error = 1, summary.strip()
        _move(s, t, "READY", who, "release_failed", {"summary": summary.strip()[:2000]})
        return
    t.result_summary = f"{t.result_summary or ''}\n\nÉlesítve: {summary.strip()}".strip()
    _move(s, t, "COMPLETED", who, "released", {"summary": summary.strip()[:2000]})


def coordinator_act(s, who: Principal, t: Ticket, action: str, reason: str | None = None, priority: int | None = None,
                    title: str | None = None):
    """A Coordinator döntései egy meglévő ticketen, a forrás új bemenete alapján."""
    if who.role != "coordinator":
        raise Forbidden("csak a Coordinator")
    reason = (reason or "").strip()
    if priority is not None and priority not in (1, 2, 3):
        raise Invalid("a prioritás 1, 2 vagy 3")
    if action in ("reopen", "done", "dismiss") and not reason:
        raise Invalid("indoklás kötelező")
    if action != "note" and (priority or title):
        if t.status in states.FINAL and action != "reopen":
            raise Invalid("lezárt ticket csak újranyitással módosítható")
        if priority:
            t.priority = priority
        if title:
            t.title = title.strip()[:500]
        event(s, t, who, "edited", payload={k: v for k, v in (("priority", priority), ("title", title)) if v})
    if action == "reopen":
        if t.status not in REOPEN_FROM:
            raise Invalid(f"a #{t.id} ticket nem nyitható újra (most: {t.status})")
        t.last_error = None
        _move(s, t, "READY", who, "reopened", {"reason": reason})
    elif action == "done":
        _move(s, t, "COMPLETED", who, "answered", {"summary": reason})
        t.result_summary = reason
    elif action == "dismiss":
        _move(s, t, "CANCELLED", who, "dismissed", {"reason": reason})
    elif action == "note":
        if not reason:
            raise Invalid("üres megjegyzés")
        event(s, t, who, "note", payload={"text": reason[:4000]})
    elif action != "set":
        raise Invalid(f"ismeretlen művelet: {action}")
    t.updated_at = now()
    s.flush()
    changed(ticket=t.id)


def coordinator_updates(s, after: int | None, limit: int = 50) -> dict:
    """A forrás új bemenetei meglévő ticketeken (komment, válaszlevél, listaváltás). after=None: csak a kurzor."""
    last = s.scalar(select(func.max(TicketEvent.id))) or 0
    if after is None:
        return {"cursor": last, "updates": []}
    rows = list(s.execute(select(TicketEvent, Ticket).join(Ticket, Ticket.id == TicketEvent.ticket_id)
                          .where(TicketEvent.id > after, TicketEvent.event_type == "source_update")
                          .order_by(TicketEvent.id).limit(limit)))
    ups = []
    for e, t in rows:
        p = e.payload or {}
        if not p.get("notify"):
            continue
        notes = [n.payload.get("text") for n in s.scalars(
            select(TicketEvent).where(TicketEvent.ticket_id == t.id, TicketEvent.event_type == "note", TicketEvent.id < e.id)
            .order_by(TicketEvent.id.desc()).limit(3)) if n.payload]
        ups.append({"ticket": t.id, "title": t.title, "status": t.status, "project": t.project_id, "priority": t.priority,
                    "source": t.source_id, "url": t.source_url, "list": p.get("list"), "list_from": p.get("list_from"),
                    "labels": p.get("labels"), "labels_from": p.get("labels_from"), "sender": p.get("sender"),
                    "thread_reply": bool(p.get("thread_reply")), "applied": p.get("applied") or [],
                    "comments": (p.get("comments") or "")[-1500:] or None,
                    "description": (p.get("description") or "")[:600] if p.get("thread_reply") else None,
                    "recent_notes": [x[:300] for x in notes]})  # a saját visszhang felismeréséhez (pl. "kártya UAT-ba mozgatva")
    return {"cursor": rows[-1][0].id if rows else after, "updates": ups}


def fail(s, who: Principal, tid: int, error: str, run: dict | None = None, instance_id=None, lease_id=None):
    t = _own_in_progress(s, who, tid, instance_id, lease_id)
    t.last_error = error.strip() or "ismeretlen hiba"
    t.retry_count += 1
    _record_run(s, who, t, run, "failed", instance_id)
    _move(s, t, "FAILED", who, "failed", {"error": t.last_error[:2000]})
    limit = project_config(s.get(Project, t.project_id)).worker.retry_limit
    if t.retry_count <= limit:
        _move(s, t, "READY", SYSTEM, "auto_retry", {"retry": t.retry_count, "limit": limit})


def _record_run(s, who, t, run, outcome, instance_id):
    run = run or {}
    model = run.get("model")
    tin, tout = run.get("tokens_in"), run.get("tokens_out")
    cost, src = run.get("cost_usd"), "reported" if run.get("cost_usd") is not None else "unknown"
    if cost is None and model and tin is not None and tout is not None:
        price = global_config(s).models.prices.get(model)
        if price:
            cost = (tin * price.get("input", 0) + tout * price.get("output", 0)) / 1_000_000
            src = "estimated"
    claimed = s.scalar(select(TicketEvent.created_at).where(TicketEvent.ticket_id == t.id,
                                                             TicketEvent.event_type.in_(("claimed", "resumed")))
                       .order_by(TicketEvent.id.desc()).limit(1))
    s.add(AgentRun(agent_instance_id=instance_id, project_id=t.project_id, ticket_id=t.id, model=model,
                   started_at=claimed or now(), finished_at=now(), tokens_in=tin, tokens_out=tout,
                   estimated_cost=cost, cost_source=src, outcome=outcome))


# ---------------------------------------------------------------- jóváhagyás (SPECIFICATION 10)

TARGETS = {"email", "jira", "trello", "file"}
ACTION_TERMINAL = {"EXECUTED", "MANUALLY_CONFIRMED", "REJECTED", "FAILED", "CANCELLED"}


def propose_action(s, who: Principal, tid: int | None, target: str, action_type: str, reference: str | None,
                   payload: dict, instance_id: str | None = None, lease_id: str | None = None) -> PendingAction:
    if who.role not in ("worker", "coordinator"):
        raise Forbidden("műveletet agent javasol")
    if target not in TARGETS:
        raise Invalid(f"ismeretlen célrendszer: {target}")
    if not isinstance(payload, dict) or not payload:
        raise Invalid("a payload egy nem üres JSON-objektum")
    t = None
    if tid is not None:
        if who.role == "worker":
            t = get_ticket(s, who, tid, lock=True)
            # több javaslat is mehet egy körben: az első után a ticket már PENDING_APPROVAL
            if t.status not in ("IN_PROGRESS", "PENDING_APPROVAL"):
                raise Invalid(f"a #{tid} ticket nincs feldolgozás alatt (most: {t.status})")
            _fence(t, instance_id, lease_id, check_expiry=t.status == "IN_PROGRESS")
        else:
            t = get_ticket(s, who, tid)
    elif who.role == "worker":
        raise Invalid("a Worker csak tickethez kötve javasolhat műveletet")
    if target == "file":
        _check_file_payload(s, who, payload)
    a = PendingAction(ticket_id=t.id if t else None, project_id=t.project_id if t else who.project_id,
                      action_type=action_type, target_system=target, target_reference=reference,
                      proposed_payload=payload, payload_hash=phash(payload), status="PENDING_APPROVAL",
                      created_by=who.actor_id)
    s.add(a)
    s.flush()
    if t is not None:
        event(s, t, who, "action_proposed", payload={"action_id": a.id, "target": target, "type": action_type})
        if True:  # a jóváhagyott írás visszhangja így felismerhető (recent_notes)
            text = payload.get("text") or json.dumps(payload, ensure_ascii=False)
            event(s, t, who, "note", payload={"text": f"Jóváhagyásra javasolva (#{a.id}): {target} {action_type}: {text}"[:4000]})
        if t.status == "IN_PROGRESS":
            _move(s, t, "PENDING_APPROVAL", who, "awaiting_approval", {"action_id": a.id})
    changed(what="actions")
    return a


def _check_file_payload(s, who, payload):
    path = payload.get("path", "")
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise Invalid("fájlműveletnél a path a projekt könyvtárához képest relatív, '..' nélkül")
    if "new_content" not in payload:
        raise Invalid("fájlműveletnél a new_content kötelező")


def decide_action(s, who: Principal, aid: int, decision: str, note: str | None = None,
                  result: dict | None = None) -> PendingAction:
    """approve | reject | manual (kézi végrehajtás visszaigazolása) | cancel — csak ember."""
    if who.role != "dashboard":
        raise Forbidden("jóváhagyni csak ember tud (Dashboard)")
    a = s.scalar(select(PendingAction).where(PendingAction.id == aid).with_for_update())
    if not a:
        raise NotFound("nincs ilyen művelet")
    if decision == "approve":
        if a.status != "PENDING_APPROVAL":
            raise Invalid(f"csak függő művelet hagyható jóvá (most: {a.status})")
        a.status, a.approved_at, a.approved_hash = "APPROVED", now(), a.payload_hash
    elif decision == "reject":
        if a.status not in ("PENDING_APPROVAL", "APPROVED"):
            raise Invalid(f"ez a művelet már nem utasítható el (most: {a.status})")
        a.status = "REJECTED"
    elif decision == "manual":
        if a.status not in ("PENDING_APPROVAL", "APPROVED", "FAILED"):
            raise Invalid(f"kézi végrehajtás most nem rögzíthető (állapot: {a.status})")
        a.status, a.executed_at = "MANUALLY_CONFIRMED", now()
        a.approved_at = a.approved_at or now()
        a.execution_result = result or {"note": note}
    elif decision == "cancel":
        if a.status in ACTION_TERMINAL or a.status == "EXECUTING":
            raise Invalid("lezárt művelet")
        a.status = "CANCELLED"
    else:
        raise Invalid("ismeretlen döntés")
    a.decided_by, a.decision_note = who.actor_id, note
    audit(s, who, f"action.{decision}", "pending_action", a.id, {"note": note})
    _after_action(s, a, who)
    return a


def claim_execution(s, aid: int) -> PendingAction | None:
    """APPROVED → EXECUTING, hogy egy írás csak egyszer induljon el."""
    a = s.scalar(select(PendingAction).where(PendingAction.id == aid).with_for_update())
    if not a or a.status != "APPROVED" or a.target_system == "file" or a.approved_hash != phash(a.proposed_payload):
        return None
    a.status = "EXECUTING"
    changed(what="actions")
    return a


def recover_executions(s) -> list[int]:
    """Induláskor: a félbeszakadt végrehajtás bizonytalan (FAILED + uncertain), ember dönt; a még el nem indultak id-je."""
    for a in s.scalars(select(PendingAction).where(PendingAction.status == "EXECUTING").with_for_update()):
        record_execution(s, SYSTEM, a, False, {"uncertain": True, "error": (
            "Az AgentHub végrehajtás közben leállt; nem tudni, megtörtént-e. Ellenőrizd a célrendszerben, "
            "és ha megtörtént, jelöld kézzel végrehajtottnak.")})
    return list(s.scalars(select(PendingAction.id).where(PendingAction.status == "APPROVED",
                                                         PendingAction.target_system != "file")))


def record_execution(s, who: Principal, a: PendingAction, ok: bool, result: dict):
    """Az író adapter (API) vagy a helyi fájlművelet (Worker CLI) eredménye."""
    if a.status not in ("APPROVED", "EXECUTING"):
        raise Invalid("csak jóváhagyott művelet hajtható végre")
    a.status = "EXECUTED" if ok else "FAILED"
    a.executed_at, a.execution_result = now(), result
    audit(s, who, "action.executed" if ok else "action.failed", "pending_action", a.id, result)
    _after_action(s, a, SYSTEM)


def _after_action(s, a: PendingAction, who):
    if a.ticket_id:
        t = s.get(Ticket, a.ticket_id)
        event(s, t, who, "action_" + a.status.lower(), payload={"action_id": a.id})
        if a.status in ACTION_TERMINAL and t.status == "PENDING_APPROVAL":
            still = s.scalar(select(func.count()).select_from(PendingAction).where(
                PendingAction.ticket_id == t.id, PendingAction.status.in_(("PENDING_APPROVAL", "APPROVED", "EXECUTING", "DRAFT"))))
            if not still:
                _move(s, t, "READY", SYSTEM, "actions_settled")
    s.flush()
    changed(what="actions")


def file_actions_for(s, who: Principal) -> list[PendingAction]:
    if who.role != "worker":
        raise Forbidden("csak Worker")
    return list(s.scalars(select(PendingAction).where(PendingAction.project_id == who.project_id,
                                                      PendingAction.target_system == "file",
                                                      PendingAction.status == "APPROVED")))


def report_file_action(s, who: Principal, aid: int, ok: bool, result: dict):
    a = s.scalar(select(PendingAction).where(PendingAction.id == aid).with_for_update())
    if not a or a.project_id != who.project_id or a.target_system != "file" or who.role != "worker":
        raise NotFound("nincs ilyen fájlművelet")
    record_execution(s, who, a, ok, result)


def action_diff(a: PendingAction) -> str | None:
    if a.target_system != "file":
        return None
    old = (a.proposed_payload.get("old_content") or "").splitlines()
    new = (a.proposed_payload.get("new_content") or "").splitlines()
    path = a.proposed_payload.get("path")
    return "\n".join(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", lineterm=""))


# ---------------------------------------------------------------- memória (SPECIFICATION 13)

def propose_knowledge(s, who: Principal, category: str, title: str, content: str, tid: int | None = None):
    if who.role != "worker":
        raise Forbidden("tudásjavaslatot a projekt Workere tesz")
    if not re.match(r"^[a-z0-9_-]{1,50}$", category or ""):
        raise Invalid("a kategória kisbetű, szám, '-' vagy '_' (pl. architecture, rules, conventions, lessons)")
    if not title.strip() or not content.strip():
        raise Invalid("a cím és a tartalom kötelező")
    if tid is not None:
        get_ticket(s, who, tid)
    k = ProjectKnowledge(project_id=who.project_id, category=category, title=title.strip()[:300],
                         content=content.strip(), source_ticket_id=tid, created_by=who.actor)
    s.add(k)
    s.flush()
    changed(what="knowledge")
    return k


def memory_dir(project_id: str) -> Path:
    return Path(os.environ.get("AGENTHUB_DATA_DIR", "/data")) / "projects" / project_id / "memory"


def decide_knowledge(s, who: Principal, kid: int, decision: str, title: str | None = None, content: str | None = None,
                     category: str | None = None):
    if who.role != "dashboard":
        raise Forbidden("tudást ember hagy jóvá")
    k = s.get(ProjectKnowledge, kid)
    if not k:
        raise NotFound("nincs ilyen tudásjavaslat")
    if decision == "accept":
        if title:
            k.title = title.strip()
        if content:
            k.content = content.strip()
        if category:
            if not re.match(r"^[a-z0-9_-]{1,50}$", category):
                raise Invalid("hibás kategória")
            k.category = category
        k.status = "ACCEPTED"
    elif decision == "reject":
        k.status = "REJECTED"
    elif decision == "retire":  # elfogadott tudás visszavonása
        if k.status != "ACCEPTED":
            raise Invalid("csak elfogadott tudás vonható vissza")
        k.status = "REJECTED"
    else:
        raise Invalid("ismeretlen döntés")
    k.decided_at = now()
    s.flush()
    audit(s, who, f"knowledge.{decision}", "knowledge", k.id)
    sync_memory_files(s, k.project_id)
    changed(what="knowledge")
    return k


def sync_memory_files(s, project_id: str):
    """Elfogadott tudás → Markdown, kategóriánként egy fájl. A DB a forrás, a fájl generált."""
    d = memory_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    rows = list(s.scalars(select(ProjectKnowledge).where(ProjectKnowledge.project_id == project_id,
                                                         ProjectKnowledge.status == "ACCEPTED")
                          .order_by(ProjectKnowledge.category, ProjectKnowledge.id)))
    cats: dict[str, list] = {}
    for k in rows:
        cats.setdefault(k.category, []).append(k)
    for f in d.glob("*.md"):
        if f.stem not in cats:
            f.unlink()
    for cat, items in cats.items():
        f = d / f"{cat}.md"
        body = [f"# {cat}", "", "<!-- Generálta az AgentHub; a Knowledge Review felületen szerkeszd. -->", ""]
        for k in items:
            src = f" (#{k.source_ticket_id})" if k.source_ticket_id else ""
            body += [f"## {k.title}{src}", "", k.content, ""]
        f.write_text("\n".join(body), encoding="utf-8")
        for k in items:
            k.file_path = str(f)


def memory_for(s, who: Principal, project_id: str | None = None) -> list[ProjectKnowledge]:
    pid = who.project_id if who.role == "worker" else project_id
    if not pid:
        raise Invalid("projekt megadása kötelező")
    return list(s.scalars(select(ProjectKnowledge).where(ProjectKnowledge.project_id == pid,
                                                         ProjectKnowledge.status == "ACCEPTED")
                          .order_by(ProjectKnowledge.category, ProjectKnowledge.id)))


def save_inventory(s, who: Principal, inventory: dict):
    if who.role != "worker":
        raise Forbidden("csak Worker")
    p = s.get(Project, who.project_id)
    p.inventory, p.inventory_at = inventory, now()
    changed(what="projects")


# ---------------------------------------------------------------- források (SPECIFICATION 5)

def match_rule(rules, item: dict, source_id: str):
    for r in rules:
        if r.source and r.source != source_id:
            continue
        ok = True
        for field in ("title", "description", "sender"):
            rx = getattr(r, field)
            if rx and not re.search(rx, item.get(field) or "", re.I):
                ok = False
                break
        if ok:
            return r
    return None


SOURCE_FIELDS = {"title": ("title", 500), "description": ("description", None), "url": ("source_url", 1000)}


def _source_desc(item: dict) -> str:
    """A forráselemből képzett leírás: a forrás leírása és a legutóbbi kommentjei (SPECIFICATION 5)."""
    desc = item.get("description") or ""
    tags = [f"{n}: {item[k]}" for n, k in (("Lista", "list"), ("Címkék", "labels")) if item.get(k)]
    if tags:
        desc = f"[{' · '.join(tags)}]\n\n{desc}".rstrip()
    if item.get("comments"):
        desc = f"{desc}\n\n---\nKommentek a forrásban:\n{item['comments']}".lstrip()
    return desc


def _apply_source_fields(t: Ticket, prev: dict, item: dict) -> dict:
    """A forrás írja felül a mezőt, kivéve ha ember/agent közben átírta (a ticket már nem a korábbi forrásértéket mutatja)."""
    applied, kept = [], []
    for key, (attr, limit) in SOURCE_FIELDS.items():
        new, old = (item.get(key) or ""), (prev.get(key) or "")
        if key == "description":
            new, old = _source_desc(item), (_source_desc(prev) if prev else "")
        new, old = (new[:limit], old[:limit]) if limit else (new, old)
        cur = getattr(t, attr) or ""
        if not new or new == cur:
            continue
        if cur == old:
            setattr(t, attr, new)
            applied.append(key)
        else:
            kept.append(key)
    return {"applied": applied, "kept_local": kept}


def ingest(s, who: Principal, source_id: str, items: list[dict], error: str | None = None) -> dict:
    """Normalizált forrásesemények: idempotens, szabályalapú, modell nélkül. Csak a bizonytalan marad NEW."""
    if who.role != "coordinator":
        raise Forbidden("forráseseményt a Coordinator küld")
    src = s.get(Source, source_id)
    if not src:
        raise NotFound(f"ismeretlen forrás: {source_id}")
    src.last_poll_at, src.last_error = now(), error
    out = {"created": [], "updated": [], "skipped": 0, "routed": []}
    if error:
        changed(what="sources")
        return out
    gc = global_config(s)
    sc = SourceConfig.model_validate(src.config) if src.config else None
    for raw in items:
        item = {k: (str(v) if v is not None else None) for k, v in raw.items()}
        ext = item.get("external_id")
        if not ext or not item.get("title"):
            out["skipped"] += 1
            continue
        h = phash(item)
        if s.scalar(select(SourceEvent.id).where(SourceEvent.source_id == src.id, SourceEvent.external_id == ext,
                                                 SourceEvent.payload_hash == h)):
            out["skipped"] += 1
            continue
        prev = s.scalar(select(SourceEvent.payload).where(SourceEvent.source_id == src.id, SourceEvent.external_id == ext)
                        .order_by(SourceEvent.id.desc()).limit(1))
        se = SourceEvent(source_id=src.id, external_id=ext, thread_id=item.get("thread_id"), payload_hash=h,
                         payload=item)
        s.add(se)
        t = s.scalar(select(Ticket).where(Ticket.source_id == src.id, Ticket.external_id == ext))
        if t is None:  # korábban egy meglévő ticketbe összevont elem
            t = s.scalar(select(Ticket).join(TicketSourceRef, TicketSourceRef.ticket_id == Ticket.id)
                         .where(TicketSourceRef.source_id == src.id, TicketSourceRef.external_id == ext))
        if t is None and item.get("thread_id"):
            t = s.scalar(select(Ticket).where(Ticket.source_id == src.id, Ticket.thread_id == item["thread_id"])
                         .order_by(Ticket.id.desc()).limit(1))
            if t is not None and t.status == "CANCELLED":  # a szál ticketjét összevonták: a válasz a célhoz megy
                target = s.scalar(select(TicketSourceRef.ticket_id).where(TicketSourceRef.merged_from_ticket_id == t.id)
                                  .limit(1))
                t = s.get(Ticket, target) if target else None  # elvetett szál (pl. automata levél): új tétel
        if t is not None:
            payload = {k: item.get(k) for k in ("title", "description", "url", "sender", "list", "labels") if item.get(k)}
            p0 = prev or {}
            if item.get("comments") and item.get("comments") != p0.get("comments"):
                payload["comments"] = item["comments"]  # új komment: az eseménytörténetbe, a leírást nem írja át
            for k in ("list", "labels"):
                if prev is not None and (item.get(k) or "") != (p0.get(k) or ""):
                    payload[f"{k}_from"] = p0.get(k)
            if t.external_id == ext:  # ugyanaz az elem változott, nem egy szál új levele
                payload.update(_apply_source_fields(t, p0, item))
            else:
                payload["thread_reply"] = True
            # a Coordinator döntést igénylő változás: új komment vagy levél, lista- vagy címkeváltás, tartalmi módosítás
            payload["notify"] = bool(payload.get("comments") or payload.get("thread_reply") or payload.get("applied")
                                     or "list_from" in payload or "labels_from" in payload)
            event(s, t, who, "source_update", payload=payload)
            t.updated_at = now()
            out["updated"].append(t.id)
        else:
            t = Ticket(title=item["title"][:500], description=_source_desc(item), source_id=src.id,
                       external_id=ext, thread_id=item.get("thread_id"), source_url=item.get("url"),
                       created_by="coordinator", status="NEW")
            s.add(t)
            s.flush()
            event(s, t, who, "created", None, "NEW", {"source": src.id, "sender": item.get("sender"),
                                                       **{k: item[k] for k in ("list", "labels") if item.get(k)}})
            similar = similar_tickets(s, t)
            if similar:  # szemantikus egyezés gyanúja: a Coordinator dönt, nem a szabály
                event(s, t, who, "possible_duplicate", payload={"similar": similar})
                out["created"].append(t.id)
                se.ticket_id, se.processed_at = t.id, now()
                s.flush()
                continue
            rule = match_rule(gc.routing_rules, item, src.id)
            project = rule.project if rule else (sc.project if sc else None)
            if rule and rule.type:
                t.type = rule.type
            if rule and rule.priority:
                t.priority = rule.priority
            if project and s.get(Project, project):
                t.project_id = project
                _move(s, t, "READY", who, "routed", {"project": project, "by": "rule" if rule else "source_default"})
                out["routed"].append(t.id)
            out["created"].append(t.id)
        se.ticket_id, se.processed_at = t.id, now()
        s.flush()
    changed(what="sources")
    return out


_WORD = re.compile(r"\w{3,}")
_STOP = {"nem", "hogy", "sem", "meg", "már", "még", "csak", "van", "vagy", "nincs", "lesz", "kell", "ezt", "azt", "egy",
         "the", "and", "for", "with", "not", "this", "that", "from"}
DUP_THRESHOLD = 0.35


def _stems(text: str) -> set[str]:
    # az első 6 betű: a magyar ragozott alakok (webshop/webshopban) így egyeznek
    return {w[:6] for w in _WORD.findall(text.lower()) if w not in _STOP}


_META = re.compile(r"^\[(?:Lista|Címkék): [^\n]*\]\n*")


def _words(t: Ticket) -> tuple[set[str], set[str]]:
    # a forrás lista/címke sora nem tartalom: azonos listán ülő, eltérő kártyák különben hasonlónak tűnnének
    return _stems(t.title), _stems(f"{t.title} {_META.sub('', t.description or '')[:500]}")


def _similarity(a, b) -> float:
    """A címek Jaccard-egyezése vagy a teljes szöveg átfedése, a nagyobb (hosszú leírás ne hígítsa)."""
    (ta, fa), (tb, fb) = a, b
    title = len(ta & tb) / len(ta | tb) if ta | tb else 0
    full = len(fa & fb) / min(len(fa), len(fb)) if fa and fb else 0
    return max(title, full)


def similar_tickets(s, t: Ticket, limit: int = 3) -> list[dict]:
    """Nyitott ticketek szóegyezés alapján, modell és embedding nélkül.
    shortcut: a legutóbbi 500 nyitott ticketet nézi, szótő-egyezéssel; ha kevés, pg_trgm vagy embedding kell."""
    mine = _words(t)
    if not mine[1]:
        return []
    q = (select(Ticket).where(Ticket.id != t.id, Ticket.status.notin_(states.FINAL))
         .order_by(Ticket.updated_at.desc()).limit(500))
    out = []
    for o in s.scalars(q):
        score = _similarity(mine, _words(o))
        if score >= DUP_THRESHOLD:
            out.append({"id": o.id, "title": o.title, "status": o.status, "project_id": o.project_id,
                        "score": round(score, 2)})
    return sorted(out, key=lambda x: -x["score"])[:limit]


def merge_ticket(s, who: Principal, dup: Ticket, target_id: int, addition: str | None = None) -> Ticket:
    """Szemantikus duplikátum összevonása: a meglévő ticket megkapja a forrásazonosítót és az új információt,
    az új (NEW) ticket CANCELLED lesz. A forrás későbbi frissítései a meglévő tickethez futnak."""
    if who.role not in ("coordinator", "dashboard"):
        raise Forbidden("összevonást a Coordinator vagy ember végez")
    if dup.status != "NEW":
        raise Invalid(f"csak még be nem sorolt (NEW) ticket vonható össze (most: {dup.status})")
    target = s.scalar(select(Ticket).where(Ticket.id == target_id).with_for_update())
    if not target or target.id == dup.id:
        raise Invalid("érvénytelen cél-ticket")
    if target.status in states.FINAL:
        raise Invalid(f"lezárt tickethez nem vonható össze (#{target.id}: {target.status})")
    ref = None
    if dup.source_id and dup.external_id:
        ref = {"source_id": dup.source_id, "external_id": dup.external_id, "url": dup.source_url}
        s.add(TicketSourceRef(ticket_id=target.id, merged_from_ticket_id=dup.id, **ref))
        dup.external_id = None  # a forrás frissítései innentől a cél-tickethez futnak
    for r in s.scalars(select(TicketSourceRef).where(TicketSourceRef.ticket_id == dup.id)):
        r.ticket_id = target.id
    addition = (addition or "").strip()
    if addition:
        label = f"#{dup.id}" + (f", {ref['source_id']} {ref['external_id']}" if ref else "")
        target.description = f"{target.description or ''}\n\n---\nKiegészítés ({label}):\n{addition[:8000]}".lstrip()
    target.updated_at = now()
    s.flush()
    event(s, target, who, "merged_in", payload={"from": dup.id, "title": dup.title, "source": ref,
                                                "addition": addition[:2000] or None})
    _move(s, dup, "CANCELLED", who, "merged", {"into": target.id})
    changed(ticket=target.id)
    return target


def last_cycle(s) -> dict | None:
    e = s.scalar(select(AuditEvent).where(AuditEvent.action == "coordinator.cycle").order_by(AuditEvent.id.desc()).limit(1))
    return {**(e.payload or {}), "at": e.created_at.isoformat()} if e else None


def coordinator_pending(s) -> list[Ticket]:
    """Modelldöntést igénylő ticketek: NEW, projekt nélkül."""
    return list(s.scalars(select(Ticket).where(Ticket.status == "NEW").order_by(Ticket.id).limit(50)))
