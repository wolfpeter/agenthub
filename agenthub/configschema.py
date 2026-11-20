"""Konfiguráció sémái (SPECIFICATION 12). Hibás konfiguráció nem aktiválódhat: minden mentés ezen megy át."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingRule(Strict):
    """Szabályalapú projektbesorolás: ha minden megadott regex illeszkedik, a ticket a projekthez kerül."""
    project: str
    source: str | None = None
    title: str | None = None
    description: str | None = None
    sender: str | None = None
    type: str | None = None
    priority: int | None = Field(default=None, ge=1, le=3)

    @field_validator("title", "description", "sender")
    @classmethod
    def _regex(cls, v):
        if v is not None:
            re.compile(v)
        return v


class ModelStrategy(Strict):
    default: str = "sonnet"
    by_type: dict[str, str] = Field(default_factory=lambda: {"bug": "sonnet", "feature": "opus", "question": "haiku",
                                                             "task": "sonnet"})
    escalate_to: str | None = "opus"
    # USD / millió token, becsléshez; üres = a költség "ismeretlen"
    prices: dict[str, dict[str, float]] = Field(default_factory=dict)


class GlobalConfig(Strict):
    installation_name: str = "AgentHub"
    coordinator_poll_interval_s: int = Field(default=600, ge=60, le=86400)
    coordinator_model: str = "haiku"
    worker_wait_timeout_s: int = Field(default=540, ge=30, le=3600)
    lease_ttl_s: int = Field(default=3600, ge=300, le=86400)
    agent_stale_after_s: int = Field(default=900, ge=60, le=86400)
    models: ModelStrategy = Field(default_factory=ModelStrategy)
    routing_rules: list[RoutingRule] = Field(default_factory=list)
    retention_days: int | None = Field(default=None, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class WorkerConfig(Strict):
    enabled: bool = True
    auto_resume: bool = True
    retry_limit: int = Field(default=1, ge=0, le=10)
    model: str | None = None
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "acceptEdits"
    allowed_tools: list[str] = Field(default_factory=list)
    instructions: str = ""


class ProjectConfig(Strict):
    id: str
    name: str
    working_directory: str
    repository: str | None = None
    default_branch: str = "main"
    is_active: bool = True
    ticket_types: list[str] = Field(default_factory=lambda: ["bug", "feature", "task", "question"])
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    models: dict[str, str] = Field(default_factory=dict)
    monthly_budget_usd: float | None = Field(default=None, ge=0)

    @field_validator("id")
    @classmethod
    def _slug(cls, v):
        if not SLUG.match(v):
            raise ValueError("a projektazonosító kisbetű, szám, '-' vagy '_' lehet (max 64)")
        return v

    @field_validator("working_directory")
    @classmethod
    def _abs(cls, v):
        if not v.startswith("/") or "/../" in v + "/" or v.rstrip("/") in ("", "/"):
            raise ValueError("a working_directory abszolút, '..' nélküli útvonal kell legyen, és nem lehet '/'")
        return v.rstrip("/")


class CliAdapterConfig(Strict):
    """Általános CLI-adapter (SPECIFICATION 11): csak engedélyezett executable, argumentumlista, JSON-kimenet."""
    executable: str
    args: list[str] = Field(default_factory=list)
    items_path: str = ""  # pontokkal elválasztott út a JSON-ban a lista felé; üres = a gyökér a lista
    fields: dict[str, str] = Field(default_factory=dict)  # normalizált mező -> JSON-kulcs (pontozott út)
    timeout_s: int = Field(default=60, ge=1, le=600)
    max_output_kb: int = Field(default=2048, ge=1, le=65536)

    @field_validator("fields")
    @classmethod
    def _fields(cls, v):
        if "external_id" not in v or "title" not in v:
            raise ValueError("a 'fields' mezőben kötelező az external_id és a title hozzárendelése")
        allowed = {"external_id", "title", "description", "url", "thread_id", "sender", "updated_at"}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"ismeretlen mező(k): {sorted(bad)}")
        return v


class ImapAdapterConfig(Strict):
    host: str
    port: int = 993
    username_secret: str
    password_secret: str
    folder: str = "INBOX"
    search: str = "UNSEEN"
    max_messages: int = Field(default=50, ge=1, le=500)


class TrelloAdapterConfig(Strict):
    board_id: str
    key_secret: str = "TRELLO_KEY"
    token_secret: str = "TRELLO_TOKEN"
    lists: list[str] = Field(default_factory=list)  # üres = minden lista


class JiraAdapterConfig(Strict):
    base_url: str
    jql: str
    email_secret: str = "JIRA_EMAIL"
    token_secret: str = "JIRA_TOKEN"
    max_results: int = Field(default=50, ge=1, le=200)


ADAPTER_CONFIGS = {"cli": CliAdapterConfig, "imap": ImapAdapterConfig, "trello": TrelloAdapterConfig,
                   "jira": JiraAdapterConfig}


class SourceConfig(Strict):
    id: str
    name: str
    adapter_type: Literal["cli", "imap", "trello", "jira"]
    is_active: bool = True
    poll_interval_s: int = Field(default=600, ge=60, le=86400)
    project: str | None = None  # alapértelmezett projekt-hozzárendelés
    settings: dict = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _slug(cls, v):
        if not SLUG.match(v):
            raise ValueError("a forrásazonosító kisbetű, szám, '-' vagy '_' lehet (max 64)")
        return v

    @model_validator(mode="after")
    def _settings(self):
        ADAPTER_CONFIGS[self.adapter_type].model_validate(self.settings)
        return self


class WriterConfig(Strict):
    """Író adapterek a jóváhagyott PendingActionök végrehajtásához (SPECIFICATION 10)."""
    email: dict | None = None   # {host, port, starttls, username_secret, password_secret, from}
    jira: dict | None = None    # {base_url, email_secret, token_secret}
    trello: dict | None = None  # {key_secret, token_secret}


class FullConfig(Strict):
    """Import/export formátum."""
    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    writers: WriterConfig = Field(default_factory=WriterConfig)
    projects: list[ProjectConfig] = Field(default_factory=list)
    sources: list[SourceConfig] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
