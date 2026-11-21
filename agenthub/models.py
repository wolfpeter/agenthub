"""Adatmodell (SPECIFICATION 17). A séma változásai Alembic-migrációban mennek."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


TS = DateTime(timezone=True)


class Installation(Base):
    __tablename__ = "installation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    repository: Mapped[str | None] = mapped_column(String(500))
    working_directory: Mapped[str] = mapped_column(String(1000))
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, default=0)
    inventory: Mapped[dict | None] = mapped_column(JSONType)
    inventory_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(20))  # dashboard | coordinator | worker
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class AgentInstance(Base):
    __tablename__ = "agent_instances"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | stopped
    last_heartbeat: Mapped[datetime] = mapped_column(TS, default=now)
    session_id: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    working_directory: Mapped[str | None] = mapped_column(String(1000))
    active_ticket_id: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(TS, default=now)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    adapter_type: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=600)
    config_version: Mapped[int] = mapped_column(Integer, default=0)
    last_poll_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(30), default="task")
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 sürgős, 2 normál, 3 ráér
    status: Mapped[str] = mapped_column(String(30), default="NEW")
    start_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    start_approved_by: Mapped[str | None] = mapped_column(String(30))
    start_approved_reason: Mapped[str | None] = mapped_column(Text)
    # Hotfix: csak ember jelöli; a Worker elsőként veszi fel, a projekt utasítása szerint teszt után azonnal élesít
    hotfix: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    hotfix_by: Mapped[str | None] = mapped_column(String(30))
    hotfix_at: Mapped[datetime | None] = mapped_column(TS)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str | None] = mapped_column(String(500))
    thread_id: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str] = mapped_column(String(30), default="human")
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=now)
    due_at: Mapped[datetime | None] = mapped_column(TS)
    assigned_worker_id: Mapped[str | None] = mapped_column(String(64))
    current_lease_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(TS)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    result_payload: Mapped[dict | None] = mapped_column(JSONType)
    claude_session_id: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_ticket_source_external"),
        # Egy projekten belül legfeljebb egy aktív feldolgozás (SPECIFICATION 6).
        Index("uq_one_in_progress_per_project", "project_id", unique=True,
              postgresql_where=text("status = 'IN_PROGRESS'"), sqlite_where=text("status = 'IN_PROGRESS'")),
        Index("ix_tickets_queue", "project_id", "status", "priority", "id"),
    )


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    old_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str | None] = mapped_column(String(30))
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


class SourceEvent(Base):
    __tablename__ = "source_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(500))
    thread_id: Mapped[str | None] = mapped_column(String(500))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(TS, default=now)
    processed_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("source_id", "external_id", "payload_hash", name="uq_source_event"),)


class TicketSourceRef(Base):
    """A tickethez összevont további forráselemek (szemantikus duplikátum): a frissítéseik is ide futnak."""
    __tablename__ = "ticket_source_refs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    merged_from_ticket_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_ticket_source_ref"),)


class TicketQuestion(Base):
    __tablename__ = "ticket_questions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    suggested_answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | ANSWERED
    created_by: Mapped[str] = mapped_column(String(30), default="worker")
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class TicketAnswer(Base):
    __tablename__ = "ticket_answers"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("ticket_questions.id", ondelete="CASCADE"), index=True)
    answer: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(30), default="human")
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    action_type: Mapped[str] = mapped_column(String(50))
    target_system: Mapped[str] = mapped_column(String(30))  # email | jira | trello | file
    target_reference: Mapped[str | None] = mapped_column(String(1000))
    proposed_payload: Mapped[dict] = mapped_column(JSONType)
    payload_hash: Mapped[str] = mapped_column(String(64))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL")
    created_by: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    decided_by: Mapped[str | None] = mapped_column(String(30))
    decision_note: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    executed_at: Mapped[datetime | None] = mapped_column(TS)
    execution_result: Mapped[dict | None] = mapped_column(JSONType)


class ProjectKnowledge(Base):
    __tablename__ = "project_knowledge"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    source_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")  # PROPOSED | ACCEPTED | REJECTED
    file_path: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str] = mapped_column(String(30), default="worker")
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(TS)


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str] = mapped_column(String(20))  # global | project | source
    scope_id: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONType)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    __table_args__ = (UniqueConstraint("scope", "scope_id", "version", name="uq_config_version"),)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_instance_id: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    model: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(TS, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger)
    estimated_cost: Mapped[float | None] = mapped_column()
    cost_source: Mapped[str | None] = mapped_column(String(30))  # reported | estimated | unknown
    outcome: Mapped[str | None] = mapped_column(String(30))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


class Control(Base):
    """Leállít/Engedélyez kapcsolók: név = 'coordinator' vagy 'worker:<projekt>'."""
    __tablename__ = "controls"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), default="run")  # run | stop
    updated_at: Mapped[datetime] = mapped_column(TS, default=now)
    updated_by: Mapped[str | None] = mapped_column(String(30))
