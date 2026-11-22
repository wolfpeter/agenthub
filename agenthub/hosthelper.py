"""Gépoldali segéd: a Dashboard kéréseit hajtja végre, amit a konténer nem tud (frissítés), és átadja a gép
adatait (kvóta). A Dashboarddal a data/ mappán át beszél: kérés be, állapot ki — az API újraindulását is túléli."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REQUEST, STATUS, ALIVE, USAGE = "update-request.json", "update-status.json", "helper.alive", "usage.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check(repo: Path, fetch=True) -> dict:
    """A helyi példány és a GitHub (upstream) összevetése; csak a 'behind' állapot telepíthető."""
    def g(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if fetch and (r := g("fetch", "-q")).returncode:
        return {"state": "error", "error": r.stderr.strip()[-500:]}
    head, up = g("rev-parse", "HEAD"), g("rev-parse", "@{u}")
    if head.returncode or up.returncode:
        return {"state": "error", "error": (up.stderr or head.stderr).strip()[-500:]}
    behind = g("log", "--format=%h %s", "HEAD..@{u}").stdout.splitlines()
    ahead = int(g("rev-list", "--count", "@{u}..HEAD").stdout.strip() or 0)
    dirty = bool(g("status", "--porcelain", "--untracked-files=no").stdout.strip())
    state = ("dirty" if dirty else "diverged" if behind and ahead else "behind" if behind
             else "ahead" if ahead else "current")
    return {"state": state, "current": head.stdout.strip()[:7], "latest": up.stdout.strip()[:7],
            "behind": behind[:30], "behind_count": len(behind), "ahead": ahead}


def _save(data: Path, **kw):
    f = data / STATUS
    try:
        cur = json.loads(f.read_text())
    except (OSError, ValueError):
        cur = {}
    cur.update(kw)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False))
    tmp.replace(f)


def step(repo: Path, data: Path, usage_file: str | None, update_cmd: list[str]) -> bool:
    """Egy kör. True: telepítés történt (a hívó lépjen ki, hogy az új kóddal induljon újra)."""
    (data / ALIVE).touch()
    if usage_file:
        try:
            raw = Path(usage_file).expanduser().read_text()
            json.loads(raw)
            if not (data / USAGE).exists() or (data / USAGE).read_text() != raw:
                (data / USAGE).write_text(raw)
        except (OSError, ValueError):
            pass
    req = data / REQUEST
    if not req.exists():
        return False
    try:
        action = json.loads(req.read_text() or "{}").get("action")
    except ValueError:
        action = None
    req.unlink(missing_ok=True)
    if action == "check":
        _save(data, check={**check(repo), "at": _now()})
    elif action == "install":
        c = check(repo)
        _save(data, check={**c, "at": _now()})
        if c["state"] != "behind":
            _save(data, install={"state": "refused", "log": f"nem telepíthető: {c['state']}", "finished_at": _now()})
            return False
        _save(data, install={"state": "running", "started_at": _now(), "log": ""})
        r = subprocess.run(update_cmd, capture_output=True, text=True)
        log = (r.stdout + r.stderr)[-6000:]
        _save(data, install={"state": "ok" if r.returncode == 0 else "failed", "log": log, "finished_at": _now()},
              check={**check(repo, fetch=False), "at": _now()})
        return True
    return False


def serve(repo: Path, usage_file: str | None, interval=3.0):
    data = repo / "data"
    update_cmd = [sys.argv[0], "update"]
    while True:
        if step(repo, data, usage_file, update_cmd):
            return  # systemd újraindítja, már a frissített kóddal
        time.sleep(interval)


UNIT = """[Unit]
Description=AgentHub gépoldali segéd (Dashboard: frissítés, kvóta)

[Service]
ExecStart={exe} helper serve
Restart=always
RestartSec=5
Environment=PATH={path}
Environment=AGENTHUB_HOME={home}

[Install]
WantedBy=default.target
"""
