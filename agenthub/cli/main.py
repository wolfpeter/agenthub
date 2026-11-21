"""`agenthub` parancssor a hoston: telepítés/üzemeltetés, a Coordinator és Worker skill eszközei.
A kimenet szándékosan tömör (egysoros JSON), hogy a Claude-session kevés tokent fogyasszon (SPECIFICATION 5.1, 14)."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .. import __version__


# ---------------------------------------------------------------- környezet

def home() -> Path:
    return Path(os.environ.get("AGENTHUB_HOME") or Path.home() / "AgentHub").expanduser()


def secrets() -> Path:
    return home() / "secrets"


def envfile() -> dict:
    out = {}
    f = home() / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def api_url() -> str:
    return os.environ.get("AGENTHUB_URL") or f"http://127.0.0.1:{envfile().get('AGENTHUB_PORT', '8790')}"


def die(msg: str, code: int = 1):
    print(f"agenthub: {msg}", file=sys.stderr)
    sys.exit(code)


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def read_key(name: str) -> str:
    f = secrets() / name
    if not f.is_file():
        die(f"hiányzó kulcs: {f}")
    return f.read_text().strip()


def write_secret(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")


class Api:
    def __init__(self, key: str, instance: str | None = None):
        self.h = {"Authorization": f"Bearer {key}"}
        if instance:
            self.h["X-AgentHub-Instance"] = instance
        self.c = httpx.Client(base_url=api_url(), timeout=httpx.Timeout(30, read=90))

    def call(self, method, path, **kw):
        for attempt in range(4):  # átmeneti hiba: exponenciális visszalépés (SPECIFICATION 20)
            try:
                r = self.c.request(method, path, headers=self.h, **kw)
            except httpx.TransportError as e:
                if attempt == 3:
                    die(f"az API nem érhető el ({api_url()}): {type(e).__name__}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail")
                except ValueError:
                    detail = r.text[:300]
                die(f"{r.status_code}: {detail}")
            return r.json() if "json" in r.headers.get("content-type", "") else r.text
        die("az API nem válaszol")

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, json=None, **kw):
        return self.call("POST", path, json=json, **kw)


def admin() -> Api:
    return Api(read_key("dashboard.key"))


# ---------------------------------------------------------------- állapotfájlok

def state_file(name: str) -> Path:
    return home() / "data" / "agents" / f"{name}.json"


def load_state(name: str) -> dict:
    f = state_file(name)
    return json.loads(f.read_text()) if f.is_file() else {}


def save_state(name: str, data: dict):
    f = state_file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))


# ---------------------------------------------------------------- projekt feloldása a könyvtárból

def project_for_cwd(explicit: str | None = None) -> str:
    bound = os.environ.get("AGENTHUB_PROJECT")
    if bound and explicit and explicit != bound:
        die(f"ez a session a(z) {bound} projekthez tartozik; másik projekt nem érhető el")
    if explicit or bound:
        return explicit or bound
    keys = secrets() / "projects"
    cwd = os.path.realpath(os.getcwd())
    best = None
    for f in keys.glob("*.json") if keys.is_dir() else []:
        meta = json.loads(f.read_text())
        wd = os.path.realpath(meta["working_directory"])
        if (cwd == wd or cwd.startswith(wd + "/")) and (best is None or len(wd) > len(best[1])):
            best = (meta["id"], wd)
    if not best:
        die("ez a könyvtár egyik regisztrált projekthez sem tartozik (agenthub project add / project key)")
    return best[0]


def worker_api(project: str) -> Api:
    st = load_state(f"worker-{project}")
    api = Api(read_key(f"projects/{project}.key"), st.get("instance_id"))
    if st.get("lease_id"):  # fencing token: csak az aktuális lease birtokosa írhat a ticketre
        api.h["X-AgentHub-Lease"] = st["lease_id"]
    return api


def coord_api() -> Api:
    return Api(read_key("coordinator.key"), load_state("coordinator").get("instance_id"))


# ---------------------------------------------------------------- init / telepítés

def cmd_init(a):
    h = home()
    import secrets as pysecrets
    env = h / ".env"
    if not env.exists():
        lines = [
            "# AgentHub telepítés — ne kerüljön Gitbe",
            f"POSTGRES_PASSWORD={pysecrets.token_urlsafe(24)}",
            f"AGENTHUB_PORT={a.port}",
            f"AGENTHUB_BIND={a.bind}",
            f"AGENTHUB_INSTANCE={a.instance}",
            f"AGENTHUB_UID={os.getuid()}",
            f"AGENTHUB_GID={os.getgid()}",
        ]
        env.write_text("\n".join(lines) + "\n")
        os.chmod(env, 0o600)
    for d in ("secrets", "secrets/projects", "data", "backups"):
        (h / d).mkdir(parents=True, exist_ok=True)
    os.chmod(h / "secrets", 0o700)
    from ..auth import new_key
    created = []
    for role in ("dashboard", "coordinator"):
        f = secrets() / f"{role}.key"
        if not f.exists():
            write_secret(f, new_key(role))
            created.append(role)
    bk = secrets() / "backup.key"
    if not bk.exists():
        from cryptography.fernet import Fernet
        write_secret(bk, Fernet.generate_key().decode())
    se = secrets() / "sources.env"
    if not se.exists():
        write_secret(se, "# Forrás- és író adapterek titkai, pl.:\n# TRELLO_KEY=...\n# TRELLO_TOKEN=...")
    out({"home": str(h), "created_keys": created})


def addresses(port: str) -> list[str]:
    ips = []
    try:
        ips += subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pass
    ips = [i for i in ips if ":" not in i and not i.startswith(("172.17.", "172.18.", "172.19.", "172.2", "172.3"))]
    return [f"http://{i}:{port}" for i in ["127.0.0.1", *ips]]


def cmd_welcome(a):
    """A telepítés végén egyszer kiírja a Dashboard címét és kulcsát (SPECIFICATION 3.1, 18.1)."""
    port = envfile().get("AGENTHUB_PORT", "8790")
    print("\nAgentHub kész.\n\nDashboard:")
    for u in addresses(port):
        print(f"  {u}")
    print(f"\nDashboard-kulcs (első belépéshez; később: agenthub key show):\n  {read_key('dashboard.key')}\n")
    print("Következő lépések:\n  agenthub project add --id <azonosító> --name <név> --dir <projekt könyvtára>")
    print("  Coordinator:  agenthub coordinator command   (kiírja az indítóparancsot)")
    print("  Worker:       agenthub worker command --project <azonosító>  (kiírja az indítóparancsot)\n")


def compose(*args, check=True, capture=False):
    h = home()
    cmd = ["docker", "compose", "--project-directory", str(h), "-f", str(h / "compose.yaml"), *args]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def wait_health(timeout=120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(api_url() + "/api/health", timeout=5).json().get("ok"):
                return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(2)
    return False


def cmd_up(a):
    compose("up", "-d", "--build")
    if not wait_health():
        die("az API nem lett egészséges; nézd meg: docker compose logs api")
    out({"ok": True, "url": api_url()})


def cmd_key(a):
    if a.action == "show":
        print(read_key("dashboard.key"))
        return
    role = a.role
    key = admin().post("/api/keys/rotate", {"role": role})["key"]
    write_secret(secrets() / f"{role}.key", key)
    out({"rotated": role, "file": str(secrets() / f"{role}.key")})
    if role == "dashboard":
        print(key)


# ---------------------------------------------------------------- projektek

def _write_project_key(pid: str, key: str, wd: str):
    write_secret(secrets() / "projects" / f"{pid}.key", key)
    write_secret(secrets() / "projects" / f"{pid}.json", json.dumps({"id": pid, "working_directory": wd}))


def cmd_project(a):
    api = admin()
    if a.action == "list":
        for p in api.get("/api/projects"):
            print(f"{p['id']:20} {p['working_directory']}  {'aktív' if p['is_active'] else 'inaktív'}")
        return
    if a.action == "add":
        wd = os.path.realpath(os.path.expanduser(a.dir))
        if not os.path.isdir(wd):
            die(f"nem létező könyvtár: {wd}")
        data = {"id": a.id, "name": a.name or a.id, "working_directory": wd, "repository": a.repo,
                "default_branch": a.branch}
        r = api.post("/api/config/project", {"data": data, "comment": "agenthub project add"})
        key = r.get("worker_key") or api.post(f"/api/projects/{a.id}/key")["key"]
        _write_project_key(a.id, key, wd)
        out({"project": a.id, "working_directory": wd})
        return
    if a.action == "key":
        p = api.get(f"/api/projects/{a.id}")
        key = api.post(f"/api/projects/{a.id}/key")["key"]
        _write_project_key(a.id, key, p["working_directory"])
        out({"project": a.id, "key_file": str(secrets() / "projects" / f"{a.id}.key")})


# ---------------------------------------------------------------- Worker

AGENT_COMMANDS = {"worker": ("worker", "ticket", "memory"), "coordinator": ("coordinator",)}
ADMIN_COMMANDS = ("init", "welcome", "up", "doctor", "key", "project", "worker command", "coordinator command",
                  "source", "config", "skills", "backup", "restore", "update")


def _agent_rules(role: str) -> tuple[list[str], list[str]]:
    """Az agent csak a saját szerepének agenthub-parancsait futtathatja; az admin parancsokat nem (kulcsok!)."""
    allow = [f"Bash(agenthub {c}:*)" for c in AGENT_COMMANDS[role]]
    deny = [f"Bash(agenthub {c}:*)" for c in ADMIN_COMMANDS]
    deny += [f"Bash(agenthub {c}:*)" for r, cs in AGENT_COMMANDS.items() if r != role for c in cs]
    h = home()
    for path in [str(secrets()), str(h / "data"), str(h / "backups")]:
        deny += [f"Read(/{path}/**)", f"Edit(/{path}/**)"]
    return allow, deny


def claude_settings(project: dict, all_projects: list[dict]) -> dict:
    """Projektizoláció Claude Code permission-szabályokkal (SPECIFICATION 18)."""
    allow, deny = _agent_rules("worker")
    for other in all_projects:
        if other["id"] != project["id"]:
            wd = other["working_directory"]
            deny += [f"Read(/{wd}/**)", f"Edit(/{wd}/**)"]
    wc = (project.get("config") or {}).get("worker", {})
    perms = {"deny": deny, "allow": [*allow, *wc.get("allowed_tools", [])],
             "defaultMode": wc.get("permission_mode", "acceptEdits")}
    return {"permissions": perms, "env": {"AGENTHUB_ROLE": "worker", "AGENTHUB_PROJECT": project["id"]}}


def cmd_worker(a):
    if a.action == "command":
        pid = project_for_cwd(a.project)
        api = admin()
        projects = api.get("/api/projects")
        p = next((x for x in projects if x["id"] == pid), None)
        if not p:
            die(f"nincs ilyen projekt: {pid}")
        sf = home() / "data" / "projects" / pid / "claude-settings.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(claude_settings(p, projects), indent=2))
        running = api.get(f"/api/tickets?project={pid}&status=IN_PROGRESS")
        sess = running[0].get("claude_session_id") if running else None
        if sess:
            print(f"# Félbehagyott ticket: #{running[0]['id']} — a Claude-session folytatása:")
            print(f"cd {p['working_directory']} && AGENTHUB_SESSION_ID={sess} claude --resume {sess} --settings {sf}")
            print("# ...majd a sessionben: /worker")
            print("# Ha a session nem folytatható, új session:")
        new = str(uuid.uuid4())
        print(f"cd {p['working_directory']} && AGENTHUB_SESSION_ID={new} claude --session-id {new} --settings {sf}")
        print("# ...majd a sessionben: /worker")
        return

    pid = project_for_cwd(a.project)
    name = f"worker-{pid}"
    if a.action == "start":
        st = load_state(name)
        if a.takeover or not st.get("instance_id"):
            st["instance_id"] = f"w-{pid}-{uuid.uuid4().hex[:8]}"
        st["session_id"] = os.environ.get("AGENTHUB_SESSION_ID") or st.get("session_id")
        save_state(name, st)
        api = Api(read_key(f"projects/{pid}.key"), st["instance_id"])
        reg = api.post("/api/agents/register", {"instance_id": st["instance_id"], "session_id": st["session_id"],
                                                "working_directory": os.path.realpath(os.getcwd()),
                                                "model": os.environ.get("ANTHROPIC_MODEL"), "takeover": a.takeover})
        inv = inventory(reg["project"]["working_directory"])
        api.post("/api/worker/inventory", inv)
        ctx = api.get("/api/worker/context")
        t = reg.get("interrupted_ticket")
        out({"project": pid, "instance": st["instance_id"], "control": ctx["control"],
             "interrupted_ticket": {"id": t["id"], "title": t["title"]} if t else None,
             "instructions": reg["project"]["worker"].get("instructions") or None,
             "memory": [f"{m['category']}: {m['title']}" for m in ctx["memory"]],
             "inventory": [f["path"] for f in inv["files"] if f["kind"] != "doc"][:30]})
        return
    api = worker_api(pid)
    if a.action == "wait":
        st = load_state(name)
        if not st.get("instance_id"):
            die("előbb: agenthub worker start")
        timeout = a.timeout if a.timeout is not None else api.get("/api/worker/context")["wait_timeout_s"]
        end = time.time() + timeout
        while True:
            apply_file_actions(api, pid)
            left = int(end - time.time())
            r = api.get(f"/api/worker/next?wait={max(0, min(left, 50))}"
                        + (f"&session={st['session_id']}" if st.get("session_id") else ""))
            if r.get("ticket"):
                st = load_state(f"worker-{pid}")
                st["lease_id"] = r.get("lease_id")
                save_state(f"worker-{pid}", st)
            if r.get("ticket") or r.get("stop"):
                out(r)
                return
            if time.time() >= end:
                out({"idle": True})
                return
    if a.action == "stop":
        api.post("/api/agents/stop")
        out({"stopped": True})


def inventory(wd: str) -> dict:
    """Meglévő Claude-környezet leltára (csak olvas, SPECIFICATION 13)."""
    root = Path(wd)
    files = []

    def add(p: Path, kind):
        try:
            data = p.read_bytes()
        except OSError:
            return
        files.append({"path": str(p.relative_to(root)), "kind": kind, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    for name in ("CLAUDE.md", "AGENTS.md", ".mcp.json", ".claude/settings.json", ".claude/CLAUDE.md"):
        if (root / name).is_file():
            add(root / name, "instructions" if name.endswith(".md") else "config")
    for p in sorted(root.glob(".claude/skills/*/SKILL.md")):
        add(p, "skill")
    for p in sorted(root.glob(".claude/commands/*.md")):
        add(p, "command")
    for p in sorted(root.glob("docs/**/*.md"))[:200]:
        add(p, "doc")
    return {"scanned_at": datetime.now(timezone.utc).isoformat(), "files": files}


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _write_in_project(wd: str, rel: str, base_sha: str | None, content: str, backup) -> None:
    """Ellenőrzés és írás ugyanazon a könyvtár-descriptoron: a symlink-csere nem viheti ki az írást a projektből.
    Ideiglenes fájl + fsync + rename: megszakításkor nem marad félig írt fájl. A csere előtt backup(régi tartalom)."""
    parent = os.path.realpath(os.path.join(wd, os.path.dirname(rel)))
    if not _inside(parent, wd):
        raise ValueError("a projekten kívüli útvonal")
    os.makedirs(parent, exist_ok=True)
    dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if not _inside(os.path.realpath(f"/proc/self/fd/{dfd}"), wd):
            raise ValueError("a projekten kívüli útvonal")
        name = os.path.basename(rel)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        except FileNotFoundError:
            cur = None
        except OSError:
            raise ValueError("a cél symlink vagy nem olvasható fájl") from None
        else:
            with os.fdopen(fd, "rb") as f:
                cur = f.read()
        if (hashlib.sha256(cur).hexdigest() if cur is not None else None) != base_sha:
            raise ValueError("a fájl a javaslat óta megváltozott; új javaslat kell")
        if cur is not None:
            backup(cur)
        tmp = f".{name}.agenthub-{uuid.uuid4().hex[:8]}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
            raise
        os.fsync(dfd)
    finally:
        os.close(dfd)


def apply_file_actions(api: Api, pid: str):
    """Jóváhagyott fájlmódosítás végrehajtása mentéssel és alapverzió-ellenőrzéssel (SPECIFICATION 13)."""
    acts = api.get("/api/worker/file-actions")
    if not acts:
        return
    wd = os.path.realpath(api.get(f"/api/projects/{pid}")["working_directory"])
    for act in acts:
        p = act["proposed_payload"]
        report = f"/api/worker/file-actions/{act['id']}/report"
        saved = []

        def backup(data: bytes):
            b = home() / "backups" / "files" / pid / datetime.now().strftime("%Y%m%d-%H%M%S") / p["path"]
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_bytes(data)
            saved.append(str(b))
        try:
            _write_in_project(wd, p["path"], p.get("base_sha256"), p["new_content"], backup)
        except (ValueError, OSError) as e:
            api.post(report, {"ok": False, "result": {"error": str(e), "backup": saved[0] if saved else None}})
            continue
        api.post(report, {"ok": True, "result": {"backup": saved[0] if saved else None}})


# ---------------------------------------------------------------- ticket-műveletek (Worker)

def _run(a) -> dict | None:
    r = {k: getattr(a, k) for k in ("model", "tokens_in", "tokens_out", "cost_usd") if getattr(a, k, None) is not None}
    return r or None


def _text(v: str | None) -> str | None:
    """'-' = stdin, '@fájl' = fájl tartalma; hosszú szöveg így nem kerül a parancssorba."""
    if v == "-":
        return sys.stdin.read()
    if v and v.startswith("@"):
        return Path(v[1:]).read_text()
    return v


def cmd_ticket(a):
    pid = project_for_cwd(a.project)
    api = worker_api(pid)
    tid = a.id
    if a.action == "show":
        t = api.get(f"/api/tickets/{tid}")
        t["events"] = [{k: e[k] for k in ("event_type", "new_status", "actor_type", "payload", "created_at")}
                       for e in t["events"][-30:]]
        out(t)
    elif a.action == "note":
        api.post(f"/api/tickets/{tid}/note", {"text": _text(a.text)})
        out({"ok": True})
    elif a.action == "ask":
        q = api.post(f"/api/tickets/{tid}/ask", {"question": _text(a.question), "context": _text(a.context) or "",
                                                 "suggested_answer": _text(a.suggest)})
        out({"question_id": q["id"], "status": "WAITING_FOR_INPUT"})
    elif a.action == "finish":
        payload = json.loads(_text(a.payload_json)) if a.payload_json else None
        api.post(f"/api/tickets/{tid}/finish", {"summary": _text(a.summary), "payload": payload, "run": _run(a)})
        out({"ok": True, "status": "REVIEW"})
    elif a.action == "fail":
        api.post(f"/api/tickets/{tid}/fail", {"error": _text(a.error), "run": _run(a)})
        out({"ok": True})
    elif a.action == "action":
        r = api.post(f"/api/tickets/{tid}/actions", {"target_system": a.target, "action_type": a.type,
                                                     "target_reference": a.ref, "payload": json.loads(_text(a.payload_json))})
        out({"action_id": r["id"], "status": r["status"]})
    elif a.action == "file":
        wd = os.path.realpath(api.get(f"/api/projects/{pid}")["working_directory"])
        target = os.path.realpath(os.path.join(wd, a.path))
        if not target.startswith(wd + "/"):
            die("a fájl a projekt könyvtárán kívül van")
        cur = Path(target).read_bytes() if os.path.exists(target) else None
        payload = {"path": os.path.relpath(target, wd), "new_content": Path(a.new_content_file).read_text(),
                   "old_content": cur.decode(errors="replace") if cur is not None else "",
                   "base_sha256": hashlib.sha256(cur).hexdigest() if cur is not None else None}
        r = api.post(f"/api/tickets/{tid}/actions", {"target_system": "file", "action_type": "edit",
                                                     "target_reference": payload["path"], "payload": payload})
        out({"action_id": r["id"], "status": r["status"]})


def cmd_memory(a):
    pid = project_for_cwd(a.project)
    api = worker_api(pid)
    if a.action == "show":
        items = api.get("/api/memory")
        if a.category:
            items = [m for m in items if m["category"] == a.category]
        for m in items:
            print(f"## [{m['category']}] {m['title']}\n{m['content']}\n")
        if not items:
            print("(nincs elfogadott projektmemória)")
    else:
        r = api.post("/api/knowledge", {"category": a.category, "title": a.title, "content": _text(a.content),
                                        "ticket_id": a.ticket})
        out({"knowledge_id": r["id"], "status": r["status"]})


# ---------------------------------------------------------------- Coordinator

def _due(src: dict, now: float) -> bool:
    last = src.get("last_poll_at")
    if not last:
        return True
    return now - datetime.fromisoformat(last).timestamp() >= src["poll_interval_s"]


def cmd_coordinator(a):
    if a.action == "command":
        allow, deny = _agent_rules("coordinator")
        sf = home() / "data" / "coordinator-settings.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"permissions": {"allow": allow, "deny": deny},
                                  "env": {"AGENTHUB_ROLE": "coordinator"}}, indent=2))
        sid = str(uuid.uuid4())
        print(f"cd {home()} && AGENTHUB_SESSION_ID={sid} claude --session-id {sid} --settings {sf}")
        print("# ...majd a sessionben: /coordinator")
        return
    if a.action == "start":
        st = load_state("coordinator")
        if a.takeover or not st.get("instance_id"):
            st["instance_id"] = f"c-{uuid.uuid4().hex[:8]}"
        st["session_id"] = os.environ.get("AGENTHUB_SESSION_ID") or st.get("session_id")
        save_state("coordinator", st)
        api = Api(read_key("coordinator.key"), st["instance_id"])
        api.post("/api/agents/register", {"instance_id": st["instance_id"], "session_id": st["session_id"],
                                          "working_directory": os.getcwd(), "takeover": a.takeover})
        ctx = api.get("/api/coordinator/context")
        out({"instance": st["instance_id"], "control": ctx["control"], "model": ctx["model"],
             "sources": [x["id"] for x in ctx["sources"]], "projects": ctx["projects"]})
        return
    api = coord_api()
    if a.action == "wait":
        from ..adapters import AdapterError, poll
        end = time.time() + a.timeout
        totals = {"created": 0, "updated": 0, "routed": 0, "errors": []}
        while True:
            ctx = api.get("/api/coordinator/context")
            if ctx["control"] == "stop":
                out({"stop": True})
                return
            next_due = end
            cycle = {"sources_checked": 0, "created": 0, "updated": 0, "routed": 0, "errors": []}
            for src in ctx["sources"]:
                if _due(src, time.time()):
                    cycle["sources_checked"] += 1
                    try:
                        items, err = poll(src, ctx.get("cli_executables", [])), None
                    except (AdapterError, KeyError, ValueError) as e:
                        items, err = [], str(e)[:500]
                    r = api.post(f"/api/sources/{src['id']}/events", {"items": items, "error": err})
                    for k in ("created", "updated", "routed"):
                        cycle[k] += len(r[k])
                        totals[k] += len(r[k])
                    if err:
                        cycle["errors"].append(f"{src['id']}: {err}")
                        totals["errors"].append(f"{src['id']}: {err}")
                    next_due = min(next_due, time.time() + src["poll_interval_s"])
                else:
                    last = datetime.fromisoformat(src["last_poll_at"]).timestamp()
                    next_due = min(next_due, last + src["poll_interval_s"])
            pending = api.get("/api/coordinator/pending")
            if cycle["sources_checked"]:  # rövid ciklus-összefoglaló a Dashboardra (SPECIFICATION 5)
                api.post("/api/coordinator/cycle", {**cycle, "pending": len(pending)})
            if pending:
                out({"pending": [{"id": t["id"], "title": t["title"], "description": (t.get("description") or "")[:400],
                                  "source": t.get("source_id"), "url": t.get("source_url"),
                                  **({"similar": t["similar"]} if t.get("similar") else {})} for t in pending],
                     "projects": ctx["projects"], "ingested": totals})
                return
            if time.time() >= end:
                out({"idle": True, "ingested": totals})
                return
            time.sleep(max(1, min(30, next_due - time.time(), end - time.time())))
    elif a.action == "classify":
        body = {"project_id": a.project_id, "type": a.type, "priority": a.priority, "triage": a.triage,
                "reason": a.reason, "go": bool(a.go_reason), "go_reason": a.go_reason}
        t = api.post(f"/api/tickets/{a.id}/classify", body)
        out({"id": t["id"], "status": t["status"], "project": t["project_id"], "go": t["start_approved"]})
    elif a.action == "merge":
        t = api.post(f"/api/tickets/{a.id}/merge", {"into": a.into, "addition": _text(a.add)})
        out({"merged": a.id, "into": t["id"], "status": t["status"]})
    elif a.action == "go":
        t = api.post(f"/api/tickets/{a.id}/go", {"value": True, "reason": a.reason})
        out({"id": t["id"], "go": t["start_approved"]})
    elif a.action == "stop":
        api.post("/api/agents/stop")
        out({"stopped": True})


def cmd_source(a):
    from ..adapters import AdapterError, poll
    api = admin()
    srcs = {x["id"]: x for x in api.get("/api/sources")}
    if a.id not in srcs:
        die(f"nincs ilyen forrás: {a.id}")
    allow = api.get("/api/config/global")["current"].get("cli_executables", [])
    try:
        items = poll(srcs[a.id], allow)
    except (AdapterError, KeyError, ValueError) as e:
        die(f"hiba: {e}")
    out({"count": len(items), "sample": items[:3]})


# ---------------------------------------------------------------- konfiguráció

def cmd_config(a):
    api = admin()
    if a.action == "export":
        text = api.get("/api/config/export")
        if a.file:
            Path(a.file).write_text(text)
            out({"written": a.file})
        else:
            print(text)
    else:
        r = api.call("POST", "/api/config-import", content=Path(a.file).read_text().encode(),
                     headers={**api.h, "Content-Type": "application/yaml"})
        out(r)


# ---------------------------------------------------------------- skillek

SKILLS = ("coordinator", "worker")
MARK = ".agenthub-managed"


def cmd_skills(a):
    """Az AgentHub skilljei a felhasználói skillkönyvtárba. Meglévő, nem AgentHub-skillt csak --force-szal, mentéssel ír felül."""
    src_root = home() / "skills"
    dst_root = Path(a.target or Path.home() / ".claude" / "skills").expanduser()
    dst_root.mkdir(parents=True, exist_ok=True)
    done = []
    for name in SKILLS:
        src, dst = src_root / name, dst_root / name
        if dst.exists() and not (dst / MARK).exists():
            if not a.force:
                die(f"{dst} már létezik és nem az AgentHub-é; felülíráshoz: agenthub skills install --force (előtte mentés készül)")
            b = home() / "backups" / "skills" / datetime.now().strftime("%Y%m%d-%H%M%S") / name
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dst, b)
            done.append(f"{name}: a régi mentve ide: {b}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        (dst / MARK).write_text(__version__)
        done.append(f"{name}: telepítve ({dst})")
    print("\n".join(done))


# ---------------------------------------------------------------- mentés, visszaállítás, frissítés

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(read_key("backup.key").encode())


def cmd_backup(a=None) -> Path:
    import io
    import tarfile
    env = envfile()
    dump = compose("exec", "-T", "db", "pg_dump", "-U", "agenthub", "-d", "agenthub", "--clean", "--if-exists",
                   capture=True).stdout
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("db.sql")
        data = dump.encode()
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for rel in (".env", "secrets", "data/projects"):
            p = home() / rel
            if p.exists():
                tar.add(p, arcname=rel, filter=lambda ti: None if ti.name.endswith("backup.key") else ti)
        meta = json.dumps({"version": __version__, "created": datetime.now(timezone.utc).isoformat(),
                           "instance": env.get("AGENTHUB_INSTANCE")}).encode()
        info = tarfile.TarInfo("meta.json")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    f = home() / "backups" / f"agenthub-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz.enc"
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(_fernet().encrypt(buf.getvalue()))
    print(f"mentés: {f}\n(titkosítva a secrets/backup.key kulccsal — azt külön, biztonságos helyen is őrizd meg)")
    return f


def cmd_restore(a):
    import io
    import tarfile
    from cryptography.fernet import Fernet, InvalidToken
    key = Path(a.key_file).read_text().strip() if a.key_file else read_key("backup.key")
    try:
        raw = Fernet(key.encode()).decrypt(Path(a.file).read_bytes())
    except InvalidToken:
        die("a mentés nem fejthető vissza ezzel a kulccsal")
    tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    meta = json.loads(tar.extractfile("meta.json").read())
    if not a.yes:
        ans = input(f"Visszaállítod a {meta['created']} mentést? A jelenlegi adatbázis felülíródik. [igen/nem] ")
        if ans.strip().lower() != "igen":
            die("megszakítva")
    dump = tar.extractfile("db.sql").read().decode()
    compose("stop", "api")
    compose("up", "-d", "db")
    time.sleep(3)
    p = subprocess.run(["docker", "compose", "--project-directory", str(home()), "-f", str(home() / "compose.yaml"),
                        "exec", "-T", "db", "psql", "-q", "-v", "ON_ERROR_STOP=1", "-U", "agenthub", "-d", "agenthub"],
                       input=dump, text=True, capture_output=True)
    if p.returncode != 0:
        die(f"adatbázis-visszaállítás hiba: {p.stderr[:500]}")
    members = [m for m in tar.getmembers() if m.name.startswith("data/projects/")
               or (a.with_secrets and (m.name.startswith("secrets/") or m.name == ".env"))]
    tar.extractall(home(), members=members, filter="data")
    compose("up", "-d", "api")
    out({"restored": a.file, "healthy": wait_health()})


def cmd_update(a):
    h = home()
    print("1/5 mentés…")
    backup = cmd_backup()
    print("2/5 frissítés a GitHubról (main/latest)…")
    old = subprocess.run(["git", "-C", str(h), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    undo = (f"Visszaállítás: git -C {h} checkout {old or '<előző commit>'} && uv tool install --force --reinstall {h}\n"
            f"  majd: agenthub up && agenthub restore {backup}")
    r = subprocess.run(["git", "-C", str(h), "pull", "--ff-only"], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"git pull nem sikerült: {r.stderr.strip()}\nA rendszer változatlan.")
    print("3/5 parancssor frissítése…")
    r = subprocess.run(["uv", "tool", "install", "--force", "--reinstall", str(h)], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"a parancssor telepítése nem sikerült (a szolgáltatások nem indultak újra):\n{r.stderr.strip()[-2000:]}\n{undo}")
    print("4/5 szolgáltatások újraépítése és migráció…")
    if compose("up", "-d", "--build", check=False).returncode != 0:
        die(f"a szolgáltatások újraépítése nem sikerült.\n{undo}")
    print("5/5 health check…")
    if not wait_health(180):
        die(f"a frissítés utáni health check sikertelen.\n{undo}")
    skills = Path.home() / ".claude" / "skills"
    if all((skills / n / MARK).exists() for n in SKILLS):
        cmd_skills(argparse.Namespace(target=None, force=False))
    out({"updated": True, "backup": str(backup)})


def cmd_doctor(a):
    checks = {}
    checks["docker"] = shutil.which("docker") is not None
    checks["claude"] = shutil.which("claude") is not None
    try:
        checks["api"] = httpx.get(api_url() + "/api/health", timeout=5).json().get("ok", False)
    except (httpx.HTTPError, ValueError):
        checks["api"] = False
    for k in ("dashboard.key", "coordinator.key", "backup.key"):
        checks[k] = (secrets() / k).is_file()
    skills = Path.home() / ".claude" / "skills"
    checks["skills"] = all((skills / n / MARK).exists() for n in SKILLS)
    out(checks)
    if not all(checks.values()):
        sys.exit(1)


# ---------------------------------------------------------------- argparse

def main(argv=None):
    p = argparse.ArgumentParser(prog="agenthub", description="AgentHub parancssor")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    x = sub.add_parser("init", help="telepítés: .env, kulcsok, könyvtárak")
    x.add_argument("--port", default="8790")
    x.add_argument("--bind", default="0.0.0.0")
    x.add_argument("--instance", default="agenthub")
    x.set_defaults(fn=cmd_init)
    sub.add_parser("welcome", help="Dashboard-cím és kulcs kiírása").set_defaults(fn=cmd_welcome)
    sub.add_parser("up", help="szolgáltatások indítása/építése").set_defaults(fn=cmd_up)
    sub.add_parser("doctor", help="állapot-ellenőrzés").set_defaults(fn=cmd_doctor)

    x = sub.add_parser("key", help="Dashboard-kulcs megjelenítése vagy cseréje")
    xs = x.add_subparsers(dest="action", required=True)
    xs.add_parser("show")
    r = xs.add_parser("rotate")
    r.add_argument("role", choices=["dashboard", "coordinator"])
    x.set_defaults(fn=cmd_key)

    x = sub.add_parser("project", help="projektek")
    xs = x.add_subparsers(dest="action", required=True)
    xs.add_parser("list")
    r = xs.add_parser("add")
    r.add_argument("--id", required=True)
    r.add_argument("--name")
    r.add_argument("--dir", required=True)
    r.add_argument("--repo")
    r.add_argument("--branch", default="main")
    r = xs.add_parser("key", help="új Worker-kulcs a projekthez, a secrets-könyvtárba")
    r.add_argument("id")
    x.set_defaults(fn=cmd_project)

    x = sub.add_parser("worker", help="Worker-eszközök")
    xs = x.add_subparsers(dest="action", required=True)
    r = xs.add_parser("command", help="a Worker Claude-sessionjének indítóparancsa")
    r.add_argument("--project")
    r = xs.add_parser("start")
    r.add_argument("--project")
    r.add_argument("--takeover", action="store_true")
    r = xs.add_parser("wait")
    r.add_argument("--project")
    r.add_argument("--timeout", type=int, help="másodperc (alap: worker_wait_timeout_s)")
    r = xs.add_parser("stop")
    r.add_argument("--project")
    x.set_defaults(fn=cmd_worker)

    x = sub.add_parser("ticket", help="ticket-műveletek a Workernek")
    xs = x.add_subparsers(dest="action", required=True)

    def tparser(name, **kw):
        r = xs.add_parser(name, **kw)
        r.add_argument("id", type=int)
        r.add_argument("--project")
        return r

    tparser("show")
    r = tparser("note")
    r.add_argument("text")
    r = tparser("ask")
    r.add_argument("--question", required=True)
    r.add_argument("--context")
    r.add_argument("--suggest")
    for name in ("finish", "fail"):
        r = tparser(name)
        if name == "finish":
            r.add_argument("--summary", required=True)
            r.add_argument("--payload-json")
        else:
            r.add_argument("--error", required=True)
        r.add_argument("--model")
        r.add_argument("--tokens-in", type=int)
        r.add_argument("--tokens-out", type=int)
        r.add_argument("--cost-usd", type=float)
    r = tparser("action", help="külső írási javaslat (jóváhagyásra vár)")
    r.add_argument("--target", required=True, choices=["email", "jira", "trello"])
    r.add_argument("--type", required=True)
    r.add_argument("--ref")
    r.add_argument("--payload-json", required=True)
    r = tparser("file", help="meglévő projektfájl (CLAUDE.md, skill) módosításának javaslata")
    r.add_argument("--path", required=True)
    r.add_argument("--new-content-file", required=True)
    x.set_defaults(fn=cmd_ticket)

    x = sub.add_parser("memory", help="projektmemória")
    xs = x.add_subparsers(dest="action", required=True)
    r = xs.add_parser("show")
    r.add_argument("--project")
    r.add_argument("--category")
    r = xs.add_parser("propose")
    r.add_argument("--project")
    r.add_argument("--category", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--content", required=True)
    r.add_argument("--ticket", type=int)
    x.set_defaults(fn=cmd_memory)

    x = sub.add_parser("coordinator", help="Coordinator-eszközök")
    xs = x.add_subparsers(dest="action", required=True)
    xs.add_parser("command", help="a Coordinator Claude-sessionjének indítóparancsa")
    r = xs.add_parser("start")
    r.add_argument("--takeover", action="store_true")
    r = xs.add_parser("wait")
    r.add_argument("--timeout", type=int, default=540)
    r = xs.add_parser("classify")
    r.add_argument("id", type=int)
    r.add_argument("--project-id")
    r.add_argument("--type")
    r.add_argument("--priority", type=int)
    r.add_argument("--triage", action="store_true")
    r.add_argument("--reason")
    r.add_argument("--go-reason", help="Mehet bekapcsolása sürgős, halaszthatatlan hibánál (indoklás)")
    r = xs.add_parser("go")
    r.add_argument("id", type=int)
    r.add_argument("--reason", required=True)
    r = xs.add_parser("merge", help="szemantikus duplikátum összevonása egy meglévő tickettel")
    r.add_argument("id", type=int)
    r.add_argument("--into", type=int, required=True)
    r.add_argument("--add", help="új információ a meglévő tickethez ('-' = stdin, '@fájl')")
    xs.add_parser("stop")
    x.set_defaults(fn=cmd_coordinator)

    x = sub.add_parser("source", help="forrásadapter kipróbálása (nem küld adatot)")
    xs = x.add_subparsers(dest="action", required=True)
    r = xs.add_parser("test")
    r.add_argument("id")
    x.set_defaults(fn=cmd_source)

    x = sub.add_parser("config", help="konfiguráció export/import")
    xs = x.add_subparsers(dest="action", required=True)
    r = xs.add_parser("export")
    r.add_argument("file", nargs="?")
    r = xs.add_parser("import")
    r.add_argument("file")
    x.set_defaults(fn=cmd_config)

    x = sub.add_parser("skills", help="skillek telepítése")
    xs = x.add_subparsers(dest="action", required=True)
    r = xs.add_parser("install")
    r.add_argument("--force", action="store_true")
    r.add_argument("--target")
    x.set_defaults(fn=cmd_skills)

    sub.add_parser("backup", help="titkosított mentés").set_defaults(fn=cmd_backup)
    x = sub.add_parser("restore", help="visszaállítás mentésből")
    x.add_argument("file")
    x.add_argument("--key-file")
    x.add_argument("--with-secrets", action="store_true")
    x.add_argument("--yes", action="store_true")
    x.set_defaults(fn=cmd_restore)
    sub.add_parser("update", help="mentés + frissítés a GitHubról + migráció + health check").set_defaults(fn=cmd_update)

    a = p.parse_args(argv)
    role = os.environ.get("AGENTHUB_ROLE")
    if role:  # agent-sessionből csak a saját szerep parancsai (a permission-szabályok mellett, második védvonal)
        full = f"{a.cmd} {getattr(a, 'action', '')}".strip()
        if a.cmd not in AGENT_COMMANDS.get(role, ()) or full in ADMIN_COMMANDS:
            die(f"'agenthub {full}' agent-sessionből ({role}) nem futtatható")
    a.fn(a)


if __name__ == "__main__":
    main()
