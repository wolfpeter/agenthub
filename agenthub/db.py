import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_Session = None


def database_url() -> str:
    url = os.environ.get("AGENTHUB_DATABASE_URL")
    if not url:
        raise RuntimeError("AGENTHUB_DATABASE_URL nincs beállítva")
    return url


def engine():
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True, pool_size=10, max_overflow=10)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine(url: str | None = None):
    """Teszteknek: más adatbázisra váltás."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    if url:
        os.environ["AGENTHUB_DATABASE_URL"] = url


@contextmanager
def session() -> Session:
    engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()
