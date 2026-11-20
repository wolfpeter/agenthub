"""A host-oldali `agenthub` CLI valódi HTTP-n, egy szálban futó API ellen."""
import json
import os
import socket
import threading
import time

import pytest
import uvicorn

from agenthub.cli import main as cli
from conftest import COORD_KEY, DASH_KEY


@pytest.fixture(scope="module")
def server():
    from agenthub.api.app import app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


@pytest.fixture
def env(server, tmp_path, monkeypatch):
    home = tmp_path / "AgentHub"
    (home / "secrets" / "projects").mkdir(parents=True)
    (home / "secrets" / "dashboard.key").write_text(DASH_KEY)
    (home / "secrets" / "coordinator.key").write_text(COORD_KEY)
    (home / "skills").symlink_to(cli.Path(__file__).resolve().parent.parent / "skills")
    monkeypatch.setenv("AGENTHUB_HOME", str(home))
    monkeypatch.setenv("AGENTHUB_URL", server)
    monkeypatch.delenv("AGENTHUB_SESSION_ID", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("régi\n")
    return home, proj


def run(capsys, *args) -> dict:
    cli.main(list(args))
    text = capsys.readouterr().out.strip().splitlines()
    return json.loads(text[-1]) if text and text[-1].startswith("{") else text


def test_worker_end_to_end(env, capsys, monkeypatch):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    assert (home / "secrets/projects/demo.key").stat().st_mode & 0o777 == 0o600
    monkeypatch.chdir(proj)
    started = run(capsys, "worker", "start")
    assert started["project"] == "demo" and "CLAUDE.md" in started["inventory"]
    assert run(capsys, "worker", "wait", "--timeout", "1") == {"idle": True}

    admin = cli.admin()
    t = admin.post("/api/tickets", {"title": "Írd át a CLAUDE.md-t", "project_id": "demo", "start_approved": True})
    got = run(capsys, "worker", "wait", "--timeout", "5")
    assert got["ticket"]["id"] == t["id"] and got["model"] == "sonnet"

    new = proj.parent / "new.md"
    new.write_text("új\n")
    act = run(capsys, "ticket", "file", str(t["id"]), "--path", "CLAUDE.md", "--new-content-file", str(new))
    admin.post(f"/api/actions/{act['action_id']}/decide", {"decision": "approve"})
    resumed = run(capsys, "worker", "wait", "--timeout", "5")  # alkalmazza, majd a ticket visszajön
    assert (proj / "CLAUDE.md").read_text() == "új\n"
    assert list((home / "backups/files/demo").rglob("CLAUDE.md"))[0].read_text() == "régi\n"
    assert resumed["ticket"]["id"] == t["id"]

    run(capsys, "ticket", "note", str(t["id"]), "félúton")
    run(capsys, "memory", "propose", "--category", "lessons", "--title", "T", "--content", "Tartalom", "--ticket", str(t["id"]))
    assert run(capsys, "ticket", "finish", str(t["id"]), "--summary", "kész")["status"] == "REVIEW"


def test_file_action_refused_if_file_changed(env, capsys, monkeypatch):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    monkeypatch.chdir(proj)
    run(capsys, "worker", "start")
    admin = cli.admin()
    t = admin.post("/api/tickets", {"title": "x", "project_id": "demo", "start_approved": True})
    run(capsys, "worker", "wait", "--timeout", "5")
    new = proj.parent / "new.md"
    new.write_text("új\n")
    act = run(capsys, "ticket", "file", str(t["id"]), "--path", "CLAUDE.md", "--new-content-file", str(new))
    (proj / "CLAUDE.md").write_text("közben valaki átírta\n")
    admin.post(f"/api/actions/{act['action_id']}/decide", {"decision": "approve"})
    run(capsys, "worker", "wait", "--timeout", "3")
    assert (proj / "CLAUDE.md").read_text() == "közben valaki átírta\n"
    assert admin.get("/api/actions")[0]["status"] == "FAILED"


def test_worker_command_settings_isolate_other_projects(env, capsys, monkeypatch, tmp_path):
    home, proj = env
    other = tmp_path / "other"
    other.mkdir()
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    run(capsys, "project", "add", "--id", "other", "--dir", str(other))
    lines = run(capsys, "worker", "command", "--project", "demo")
    assert any("claude --session-id" in l and "--settings" in l for l in lines)
    settings = json.loads((home / "data/projects/demo/claude-settings.json").read_text())
    deny = settings["permissions"]["deny"]
    assert f"Read(/{other}/**)" in deny and f"Read(/{home}/secrets/**)" in deny
    assert not any(str(proj) in d for d in deny)
    monkeypatch.chdir(other)
    assert cli.project_for_cwd() == "other"


def test_coordinator_cli_source(env, capsys):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    admin = cli.admin()
    g = admin.get("/api/config/global")["current"]
    g["cli_executables"] = ["python3"]
    admin.post("/api/config/global", {"data": g})
    script = 'import json;print(json.dumps({"items":[{"key":"A-1","summary":"Gomb nem megy","link":"http://x"}]}))'
    admin.post("/api/config/source", {"data": {"id": "tracker", "name": "Tracker", "adapter_type": "cli", "settings": {
        "executable": "python3", "args": ["-c", script], "items_path": "items",
        "fields": {"external_id": "key", "title": "summary", "url": "link"}}}})
    assert run(capsys, "source", "test", "tracker")["count"] == 1
    run(capsys, "coordinator", "start")
    res = run(capsys, "coordinator", "wait", "--timeout", "5")
    assert res["pending"][0]["title"] == "Gomb nem megy" and res["ingested"]["created"] == 1
    tid = res["pending"][0]["id"]
    c = run(capsys, "coordinator", "classify", str(tid), "--project-id", "demo", "--type", "bug", "--priority", "1",
            "--go-reason", "éles hiba, a vásárlás nem működik")
    assert c == {"id": tid, "status": "READY", "project": "demo", "go": True}
    assert run(capsys, "coordinator", "wait", "--timeout", "1")["idle"] is True  # idempotens, nincs újabb


def test_skills_install_refuses_foreign_skill(env, capsys, tmp_path):
    target = tmp_path / "skills"
    (target / "worker").mkdir(parents=True)
    (target / "worker" / "SKILL.md").write_text("régi skill")
    with pytest.raises(SystemExit):
        cli.main(["skills", "install", "--target", str(target)])
    assert (target / "worker" / "SKILL.md").read_text() == "régi skill"
    cli.main(["skills", "install", "--target", str(target), "--force"])
    assert "AgentHub Worker" in (target / "worker" / "SKILL.md").read_text()
    backups = list((env[0] / "backups" / "skills").rglob("SKILL.md"))
    assert backups and backups[0].read_text() == "régi skill"
