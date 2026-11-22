import json
import os
import subprocess
import time
from pathlib import Path

from agenthub import hosthelper as hh


def git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def commit(d, msg):
    (d / "f").write_text(msg)
    git(d, "add", "f")
    git(d, "commit", "-qm", msg)


def test_check_states_and_install_only_when_behind(tmp_path):
    up, loc = tmp_path / "up", tmp_path / "loc"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    commit(up, "a")
    git(tmp_path, "clone", "-q", str(up), str(loc))
    assert hh.check(loc)["state"] == "current"
    commit(up, "b")
    c = hh.check(loc)
    assert c["state"] == "behind" and c["behind_count"] == 1 and c["behind"][0].endswith(" b")
    (loc / "f").write_text("local edit")
    assert hh.check(loc)["state"] == "dirty"
    git(loc, "checkout", "-q", "f")

    data = tmp_path / "data"
    data.mkdir()
    usage = tmp_path / "u.json"
    usage.write_text('{"week": 49}')
    (data / hh.REQUEST).write_text('{"action": "install"}')
    assert hh.step(loc, data, str(usage), ["true"]) is True
    st = json.loads((data / hh.STATUS).read_text())
    assert st["install"]["state"] == "ok" and (data / hh.ALIVE).exists()
    assert json.loads((data / hh.USAGE).read_text()) == {"week": 49}
    assert not (data / hh.REQUEST).exists()

    commit(loc, "local")  # előrébb jár (fejlesztői példány): nem telepít
    (data / hh.REQUEST).write_text('{"action": "install"}')
    assert hh.step(loc, data, None, ["false"]) is False
    assert json.loads((data / hh.STATUS).read_text())["install"]["state"] == "refused"


def test_update_endpoints_need_live_helper(dash):
    d = Path(os.environ["AGENTHUB_DATA_DIR"])
    (d / hh.ALIVE).unlink(missing_ok=True)
    assert dash.get("/api/update")["helper"] is False
    dash.post("/api/update", {"action": "check"}, ok=409)
    (d / hh.ALIVE).touch()
    dash.post("/api/update", {"action": "check"})
    assert json.loads((d / hh.REQUEST).read_text()) == {"action": "check"}
    dash.post("/api/update", {"action": "rm -rf"}, ok=422)
    now = int(time.time())
    (d / hh.USAGE).write_text(json.dumps({"week": 49, "week_reset": now + 99, "five": 80, "five_reset": now - 1}))
    u = dash.get("/api/usage")
    assert u["week"] == 49 and u["five"] == 0
    (d / hh.REQUEST).unlink()
