"""Integrációs tesztek valódi PostgreSQL-lel (a claim/lease és a részleges egyedi index Postgres-specifikus).
Indítás: docker run -d --name agenthub-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=agenthub_test \
  -p 127.0.0.1:55432:5432 postgres:17-alpine"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("AGENTHUB_TEST_DATABASE_URL",
                     "postgresql+psycopg://postgres:test@127.0.0.1:55432/agenthub_test")
os.environ["AGENTHUB_DATABASE_URL"] = URL

DASH_KEY = "ah_dash_test-dashboard-key-0123456789"
COORD_KEY = "ah_coor_test-coordinator-key-0123456789"


@pytest.fixture(scope="session", autouse=True)
def _schema(tmp_path_factory):
    from sqlalchemy import create_engine, text
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    eng.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    sec = tmp_path_factory.mktemp("secrets")
    (sec / "dashboard.key").write_text(DASH_KEY)
    (sec / "coordinator.key").write_text(COORD_KEY)
    os.environ["AGENTHUB_SECRETS_DIR"] = str(sec)
    os.environ["AGENTHUB_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))


@pytest.fixture(autouse=True)
def _clean():
    from sqlalchemy import text
    from agenthub import db
    with db.session() as s:
        tables = [r[0] for r in s.execute(text(
            "select tablename from pg_tables where schemaname='public' and tablename not in ('alembic_version','api_keys','installation')"))]
        s.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))
        s.execute(text("DELETE FROM api_keys WHERE role = 'worker'"))
    with db.session() as s:  # a TRUNCATE CASCADE a projects FK miatt az api_keys-t is üríti
        from agenthub import auth
        auth.bootstrap_from_files(s)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from agenthub.api.app import app
    with TestClient(app) as c:
        yield c


class Api:
    def __init__(self, client, key, instance=None):
        self.c, self.h = client, {"Authorization": f"Bearer {key}"}
        if instance:
            self.h["X-AgentHub-Instance"] = instance

    def get(self, path, ok=200, **kw):
        r = self.c.get(path, headers=self.h, **kw)
        assert r.status_code == ok, (r.status_code, r.text)
        return r.json()

    def post(self, path, json=None, ok=200, **kw):
        r = self.c.post(path, json=json, headers=self.h, **kw)
        assert r.status_code == ok, (r.status_code, r.text)
        return r.json()


@pytest.fixture
def dash(client):
    return Api(client, DASH_KEY)


@pytest.fixture
def coord(client):
    return Api(client, COORD_KEY, "coord-1")


def make_project(dash, pid, wd=None):
    r = dash.post("/api/config/project", {"data": {"id": pid, "name": pid.upper(), "working_directory": wd or f"/srv/{pid}"}})
    return r["worker_key"]


@pytest.fixture
def two_projects(client, dash):
    ka, kb = make_project(dash, "alpha"), make_project(dash, "beta")
    wa, wb = Api(client, ka, "wa"), Api(client, kb, "wb")
    wa.post("/api/agents/register", {"instance_id": "wa"})
    wb.post("/api/agents/register", {"instance_id": "wb"})
    return wa, wb
