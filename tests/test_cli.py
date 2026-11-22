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
    assert any("claude --session-id" in l and "--model sonnet --settings" in l for l in lines)  # globális alapmodell
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


def test_agent_session_cannot_run_admin_commands(env, capsys, monkeypatch):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    run(capsys, "worker", "command", "--project", "demo")
    st = json.loads((home / "data/projects/demo/claude-settings.json").read_text())
    perms = st["permissions"]
    assert "Bash(agenthub:*)" not in perms["allow"]
    assert "Bash(agenthub key:*)" in perms["deny"] and "Bash(agenthub coordinator:*)" in perms["deny"]
    assert "Bash(agenthub worker command:*)" in perms["deny"] and "Bash(agenthub ticket:*)" in perms["allow"]
    assert st["env"] == {"AGENTHUB_ROLE": "worker", "AGENTHUB_PROJECT": "demo", "AGENTHUB_OUTBOUND_APPROVAL": "0"}
    monkeypatch.setenv("AGENTHUB_ROLE", "worker")
    monkeypatch.setenv("AGENTHUB_PROJECT", "demo")
    for args in (["key", "show"], ["config", "export"], ["coordinator", "go", "1", "--reason", "x"],
                 ["worker", "command"], ["ticket", "show", "1", "--project", "other"]):
        with pytest.raises(SystemExit):
            cli.main(args)
    monkeypatch.setenv("AGENTHUB_ROLE", "coordinator")
    with pytest.raises(SystemExit):
        cli.main(["ticket", "show", "1"])


def test_update_stops_on_cli_install_failure(env, monkeypatch, capsys):
    import subprocess
    calls = []
    monkeypatch.setattr(cli, "cmd_backup", lambda *a: "/x/backup.tar.enc")
    monkeypatch.setattr(cli, "compose", lambda *a, **k: calls.append(a))

    def fake_run(cmd, **k):
        return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "uv" else 0, "abc123\n", "uv hiba")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        cli.cmd_update(None)
    err = capsys.readouterr().err
    assert "uv hiba" in err and "checkout abc123" in err and "/x/backup.tar.enc" in err
    assert not calls  # a szolgáltatásokat nem indította újra


def _sha(b: bytes):
    import hashlib
    return hashlib.sha256(b).hexdigest()


def test_file_write_refuses_symlinks_and_is_atomic(tmp_path, monkeypatch):
    wd, outside = tmp_path / "proj", tmp_path / "outside"
    wd.mkdir(), outside.mkdir()
    (outside / "secret.txt").write_text("kint")
    (wd / "link.txt").symlink_to(outside / "secret.txt")
    (wd / "sub").symlink_to(outside)
    w = lambda rel, base, bk=lambda d: None: cli._write_in_project(str(wd), rel, base, "pwned", bk)
    with pytest.raises(ValueError):
        w("link.txt", _sha(b"kint"))  # a cél symlink
    with pytest.raises(ValueError):
        w("sub/secret.txt", _sha(b"kint"))  # a szülőkönyvtár symlink
    assert (outside / "secret.txt").read_text() == "kint"

    (wd / "a.txt").write_text("régi")
    monkeypatch.setattr(cli.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("megszakadt")))
    with pytest.raises(OSError):
        w("a.txt", _sha("régi".encode()))
    assert (wd / "a.txt").read_text() == "régi" and sorted(p.name for p in wd.iterdir()) == ["a.txt", "link.txt", "sub"]
    monkeypatch.undo()
    saved = []
    w("a.txt", _sha("régi".encode()), saved.append)
    assert (wd / "a.txt").read_text() == "pwned" and saved == ["régi".encode()]
    w("uj/b.txt", None)
    assert (wd / "uj" / "b.txt").read_text() == "pwned"


def test_worker_settings_include_project_hooks():
    hooks = {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "npm run lint"}]}]}
    st = cli.claude_settings({"id": "p", "config": {"worker": {"hooks": hooks}}}, [])
    assert st["hooks"] == hooks
    assert "hooks" not in cli.claude_settings({"id": "p", "config": {}}, [])


def test_prune_backups_keeps_recent_and_current(env, monkeypatch):
    home, _ = env
    b = home / "backups"
    b.mkdir()
    old, recent, cur = (b / f"agenthub-{n}.tar.gz.enc" for n in ("old", "recent", "cur"))
    for f in (old, recent, cur):
        f.write_bytes(b"x")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    os.utime(cur, (time.time() - 40 * 86400,) * 2)  # a mostani mentés akkor is marad
    admin = cli.admin()
    g = admin.get("/api/config/global")["current"]
    admin.post("/api/config/global", {"data": {**g, "retention_days": 30}})
    cli.prune_backups(cur)
    assert not old.exists() and recent.exists() and cur.exists()


def test_sse_stream_pushes_changes(env, server):
    import httpx
    got = []

    def listen():
        with httpx.Client(timeout=10).stream("GET", f"{server}/api/stream", headers={"Authorization": f"Bearer {DASH_KEY}"}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                got.append(line)
                if line.startswith("event: change"):
                    return
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    for _ in range(100):
        if got:
            break
        time.sleep(0.05)
    assert got and got[0] == "event: hello"
    cli.admin().post("/api/tickets", {"title": "SSE-teszt"})
    th.join(5)
    assert any(l.startswith("event: change") for l in got)


def test_cli_renews_expired_lease_once(env, capsys, monkeypatch):
    from sqlalchemy import text
    from agenthub import db
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    monkeypatch.chdir(proj)
    run(capsys, "worker", "start")
    t = cli.admin().post("/api/tickets", {"title": "hosszú munka", "project_id": "demo", "start_approved": True})
    run(capsys, "worker", "wait", "--timeout", "5")
    first = cli.load_state("worker-demo")["lease_id"]
    with db.session() as s:  # több mint egy óra jegyzet nélkül: lejárt, de senki nem vette át
        s.execute(text("update tickets set lease_expires_at = now() - interval '1 minute' where id = :i"), {"i": t["id"]})
    assert run(capsys, "ticket", "finish", str(t["id"]), "--summary", "kész")["status"] == "REVIEW"
    assert cli.load_state("worker-demo")["lease_id"] != first


def test_config_export_import_roundtrip(env, capsys, tmp_path):
    """A config import a saját Content-Type-ját az Authorization mellé teszi (korábban TypeError lett belőle)."""
    f = tmp_path / "cfg.yaml"
    cli.main(["config", "export", str(f)])
    f.write_text(f.read_text().replace("coordinator_poll_interval_s: 600", "coordinator_poll_interval_s: 700"))
    capsys.readouterr()
    cli.main(["config", "import", str(f)])
    capsys.readouterr()
    cli.main(["config", "export"])
    assert "coordinator_poll_interval_s: 700" in capsys.readouterr().out


def test_compose_cmd_includes_local_override(env):
    home, _ = env
    assert "compose.override.yaml" not in " ".join(cli.compose_cmd())
    (home / "compose.override.yaml").write_text("services: {}\n")
    assert cli.compose_cmd()[-2:] == ["-f", str(home / "compose.override.yaml")]


def test_worker_gate_holds_work_without_lease(env, capsys, monkeypatch, tmp_path):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    monkeypatch.chdir(proj)
    run(capsys, "worker", "start")
    admin = cli.admin()
    gate = tmp_path / "gate.py"
    gate.write_text("import json,sys\nt=json.load(sys.stdin)\nif t['priority']>1:\n print('5 órás keret 92%')\n sys.exit(10)\n")
    g = admin.get("/api/config/global")["current"]
    g["worker_gate_command"] = ["python3", str(gate)]
    admin.post("/api/config/global", {"data": g})
    t = admin.post("/api/tickets", {"title": "ráér", "project_id": "demo", "priority": 2, "start_approved": True})
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert run(capsys, "worker", "wait", "--timeout", "1") == {"idle": True, "gate": "5 órás keret 92%"}
    assert admin.get(f"/api/tickets/{t['id']}")["status"] == "READY"  # nincs lefoglalva
    admin.call("PATCH", f"/api/tickets/{t['id']}", json={"priority": 1})
    assert run(capsys, "worker", "wait", "--timeout", "5")["ticket"]["id"] == t["id"]
    assert run(capsys, "ticket", "finish", str(t["id"]), "--summary", "UAT-on", "--to", "release")["status"] == "AWAITING_RELEASE"
    assert run(capsys, "release", "list")["awaiting_release"][0]["id"] == t["id"]
    assert run(capsys, "release", "done", str(t["id"]), "--summary", "v2 kint")["status"] == "COMPLETED"


def test_coordinator_wait_reports_updates_once(env, capsys):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    admin = cli.admin()
    g = admin.get("/api/config/global")["current"]
    g["cli_executables"] = ["python3"]
    g["coordinator_instructions"] = "Júlia levele P1."
    g["coordinator_allowed_tools"] = ["mcp__trello__write"]
    g["coordinator_denied_tools"] = ["mcp__gmail__trash_thread"]
    g["coordinator_permission_mode"] = "bypassPermissions"
    admin.post("/api/config/global", {"data": g})
    data = home / "items.json"
    data.write_text('[{"id":"K1","t":"Kártya","l":"Backlog"}]')
    admin.post("/api/config/source", {"data": {"id": "trk", "name": "T", "adapter_type": "cli", "project": "demo",
                                               "poll_interval_s": 60, "settings": {
        "executable": "python3", "args": ["-c", f"print(open('{data}').read())"],
        "fields": {"external_id": "id", "title": "t", "list": "l"}}}})
    lines = run(capsys, "coordinator", "command")
    assert f"--model {g['coordinator_model']} " in str(lines)  # a beállított modell, nem az alapértelmezett
    st = json.loads((home / "data/coordinator-settings.json").read_text())
    assert "mcp__trello__write" in st["permissions"]["allow"] and st["permissions"]["defaultMode"] == "bypassPermissions"
    assert "mcp__gmail__trash_thread" in st["permissions"]["deny"]
    assert run(capsys, "coordinator", "start")["instructions"] == "Júlia levele P1."
    assert run(capsys, "coordinator", "wait", "--timeout", "2")["idle"] is True  # új kártya: szabály szerint besorolva
    data.write_text('[{"id":"K1","t":"Kártya","l":"Review / UAT"}]')
    from agenthub import db
    from agenthub.models import Source
    with db.session() as s:
        s.get(Source, "trk").last_poll_at = None  # a következő wait azonnal lekérdez
    r = run(capsys, "coordinator", "wait", "--timeout", "2")
    assert r["updates"][0]["list"] == "Review / UAT" and r["updates"][0]["list_from"] == "Backlog"
    with db.session() as s:
        s.get(Source, "trk").last_poll_at = None
    assert run(capsys, "coordinator", "wait", "--timeout", "1")["idle"] is True  # egyszer adja ki
    tid = r["updates"][0]["ticket"]
    assert run(capsys, "coordinator", "set", str(tid), "--priority", "1")["priority"] == 1


def test_outbound_approval(env, capsys, monkeypatch):
    home, proj = env
    run(capsys, "project", "add", "--id", "demo", "--dir", str(proj))
    admin = cli.admin()
    g = admin.get("/api/config/global")["current"]
    g["outbound_tools"] = ["mcp__trello__comment"]
    admin.post("/api/config/global", {"data": g})
    run(capsys, "coordinator", "command")
    st = json.loads((home / "data/coordinator-settings.json").read_text())
    assert "mcp__trello__comment" not in st["permissions"]["deny"]  # kikapcsolva: közvetlen írás
    assert admin.post("/api/outbound-approval", {"value": True})["value"] is True  # Dashboard-kapcsoló
    assert admin.get("/api/state")["controls"]["outbound_approval"] is True
    run(capsys, "coordinator", "command")
    st = json.loads((home / "data/coordinator-settings.json").read_text())
    assert "mcp__trello__comment" in st["permissions"]["deny"] and st["env"]["AGENTHUB_OUTBOUND_APPROVAL"] == "1"
    monkeypatch.setenv("AGENTHUB_OUTBOUND_APPROVAL", "1")
    assert run(capsys, "coordinator", "start")["outbound_approval"] is True
    t = admin.post("/api/tickets", {"title": "Kérdés a kártyán", "project_id": "demo"})
    r = run(capsys, "coordinator", "action", str(t["id"]), "--target", "trello", "--type", "comment", "--ref", "abc",
            "--payload-json", '{"text": "Szia! Kint van."}')
    assert r["status"] == "PENDING_APPROVAL"
    d = admin.get(f"/api/tickets/{t['id']}")
    assert any("Szia! Kint van." in (e["payload"] or {}).get("text", "") for e in d["events"] if e["event_type"] == "note")
    a = admin.post(f"/api/actions/{r['action_id']}/decide", {"decision": "approve"})
    assert a["status"] == "APPROVED"  # író adapter nélkül kézi végrehajtásra vár
    lines = run(capsys, "worker", "command", "--project", "demo")  # a Workerre is vonatkozik
    ws = json.loads((home / "data/projects/demo/claude-settings.json").read_text())
    assert "mcp__trello__comment" in ws["permissions"]["deny"] and ws["env"]["AGENTHUB_OUTBOUND_APPROVAL"] == "1"
    admin.post("/api/outbound-approval", {"value": False})  # váltás: a futó session leáll
    w = run(capsys, "coordinator", "wait", "--timeout", "1")
    assert w["stop"] is True and "újra" in w["reason"]
    monkeypatch.chdir(proj)
    run(capsys, "worker", "start")
    w = run(capsys, "worker", "wait", "--timeout", "1")
    assert w["stop"] is True and "worker command" in w["reason"]
