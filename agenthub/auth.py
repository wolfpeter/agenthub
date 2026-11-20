"""Kulcsalapú hitelesítés (SPECIFICATION 18.1). A kulcsot csak hash-ként tároljuk."""
import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from .models import ApiKey, now

ROLE_ACTOR = {"dashboard": "human", "coordinator": "coordinator", "worker": "worker"}


@dataclass(frozen=True)
class Principal:
    role: str  # dashboard | coordinator | worker | system
    project_id: str | None = None

    @property
    def actor(self) -> str:
        return ROLE_ACTOR.get(self.role, "system")

    @property
    def actor_id(self) -> str:
        return f"{self.actor}:{self.project_id}" if self.project_id else self.actor


SYSTEM = Principal("system")


def new_key(role: str) -> str:
    return f"ah_{role[:4]}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def lookup(s, key: str) -> Principal | None:
    if not key:
        return None
    row = s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None)))
    return Principal(row.role, row.project_id) if row else None


def issue(s, role: str, project_id: str | None = None, key: str | None = None) -> str:
    """Új kulcs a szerephez (projekt-Workernél projekthez); a régi visszavonódik."""
    key = key or new_key(role)
    q = select(ApiKey).where(ApiKey.role == role, ApiKey.revoked_at.is_(None))
    q = q.where(ApiKey.project_id == project_id) if project_id else q.where(ApiKey.project_id.is_(None))
    for old in s.scalars(q):
        old.revoked_at = now()
    s.add(ApiKey(role=role, project_id=project_id, key_hash=hash_key(key)))
    s.flush()
    return key


def bootstrap_from_files(s, secrets_dir: str | None = None) -> list[str]:
    """A telepítő által generált dashboard- és coordinator-kulcs regisztrálása induláskor."""
    d = Path(secrets_dir or os.environ.get("AGENTHUB_SECRETS_DIR", "/secrets"))
    done = []
    for role in ("dashboard", "coordinator"):
        f = d / f"{role}.key"
        if not f.is_file():
            continue
        key = f.read_text().strip()
        if key and lookup(s, key) is None:
            issue(s, role, key=key)
            done.append(role)
    return done
