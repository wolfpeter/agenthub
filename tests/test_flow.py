import pytest

from agenthub import states
from conftest import COORD_KEY, DASH_KEY, Api, make_project


def new(dash, project="alpha", **kw):
    return dash.post("/api/tickets", {"title": kw.pop("title", "Hiba"), "project_id": project, **kw})


def nxt(w, wait=0):
    """Mint a CLI: a kapott lease-azonosítót a további ticketírásokhoz elküldi."""
    r = w.get(f"/api/worker/next?wait={wait}")
    if r.get("lease_id"):
        w.h["X-AgentHub-Lease"] = r["lease_id"]
    return r


# ------------------------------------------------------------ állapotgép

def test_state_table_rejects_unknown_and_wrong_actor():
    states.check("READY", "IN_PROGRESS", "worker")
    with pytest.raises(states.TransitionError):
        states.check("READY", "COMPLETED", "human")
    with pytest.raises(states.TransitionError):
        states.check("REVIEW", "COMPLETED", "worker")  # lezárás csak emberi elfogadással
    with pytest.raises(states.TransitionError):
        states.check("COMPLETED", "CANCELLED", "human")  # végállapot


# ------------------------------------------------------------ Mehet jelölés

def test_ticket_not_started_without_go(dash, two_projects):
    wa, _ = two_projects
    t = new(dash)
    assert t["status"] == "READY" and t["start_approved"] is False
    assert nxt(wa) == {"idle": True}
    dash.post(f"/api/tickets/{t['id']}/go", {"value": True})
    got = nxt(wa)
    assert got["ticket"]["id"] == t["id"] and got["resumed"] is False


def test_go_permissions(dash, coord, two_projects):
    wa, _ = two_projects
    t = new(dash)
    wa.post(f"/api/tickets/{t['id']}/go", {"value": True}, ok=403)
    coord.post(f"/api/tickets/{t['id']}/go", {"value": True}, ok=400)  # indoklás nélkül nem
    coord.post(f"/api/tickets/{t['id']}/go", {"value": True, "reason": "éles leállás"})
    coord.post(f"/api/tickets/{t['id']}/go", {"value": False, "reason": "x"}, ok=403)
    d = dash.get(f"/api/tickets/{t['id']}")
    assert d["start_approved_by"] == "coordinator" and d["start_approved_reason"] == "éles leállás"
    assert any(e["event_type"] == "go_on" and e["actor_type"] == "coordinator" for e in d["events"])
    dash.post(f"/api/tickets/{t['id']}/go", {"value": False})
    assert dash.get(f"/api/tickets/{t['id']}")["start_approved"] is False


# ------------------------------------------------------------ Worker, izoláció

def test_priority_order_and_single_in_progress(dash, two_projects):
    wa, _ = two_projects
    low = new(dash, priority=3, start_approved=True)
    high = new(dash, priority=1, start_approved=True)
    assert nxt(wa)["ticket"]["id"] == high["id"]
    again = nxt(wa)  # amíg fut, ugyanazt kapja vissza folytatásként, nem újat
    assert again["ticket"]["id"] == high["id"] and again["resumed"] is True
    wa.post(f"/api/tickets/{high['id']}/finish", {"summary": "kész"})
    assert nxt(wa)["ticket"]["id"] == low["id"]


def test_project_isolation(dash, two_projects):
    wa, wb = two_projects
    t = new(dash, "alpha", start_approved=True)
    assert nxt(wb) == {"idle": True}
    wb.get(f"/api/tickets/{t['id']}", ok=404)
    assert wb.get("/api/tickets") == []
    nxt(wa)
    wb.post(f"/api/tickets/{t['id']}/note", {"text": "x"}, ok=404)
    wb.post(f"/api/tickets/{t['id']}/finish", {"summary": "x"}, ok=404)
    wb.get("/api/projects/alpha", ok=404)


def test_second_live_worker_refused(client, dash):
    k = make_project(dash, "solo")
    w1, w2 = Api(client, k, "i1"), Api(client, k, "i2")
    w1.post("/api/agents/register", {"instance_id": "i1"})
    w2.post("/api/agents/register", {"instance_id": "i2"}, ok=400)
    w2.post("/api/agents/register", {"instance_id": "i2", "takeover": True})


def test_question_answer_resume_review_accept(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    q = wa.post(f"/api/tickets/{t['id']}/ask", {"question": "Melyik ág?", "suggested_answer": "main"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "WAITING_FOR_INPUT"
    assert nxt(wa) == {"idle": True}
    dash.post(f"/api/questions/{q['id']}/answer", {"answer": "main"})
    got = nxt(wa)  # a jelölés megmaradt, újrajelölés nélkül folytatódik
    assert got["ticket"]["id"] == t["id"]
    wa.post(f"/api/tickets/{t['id']}/finish", {"summary": "megcsinálva", "run": {"model": "sonnet", "tokens_in": 10, "tokens_out": 5}})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "REVIEW"
    wa.post(f"/api/tickets/{t['id']}/transition", {"action": "accept"}, ok=403)
    dash.post(f"/api/tickets/{t['id']}/transition", {"action": "accept"})
    d = dash.get(f"/api/tickets/{t['id']}")
    assert d["status"] == "COMPLETED" and d["runs"][0]["tokens_in"] == 10


def test_fail_auto_retry_until_limit(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{t['id']}/fail", {"error": "build hiba"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "READY"  # retry_limit=1
    nxt(wa)
    wa.post(f"/api/tickets/{t['id']}/fail", {"error": "megint"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "FAILED"
    dash.post(f"/api/tickets/{t['id']}/transition", {"action": "retry"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "READY"


def test_stop_switch(dash, two_projects):
    wa, _ = two_projects
    new(dash, start_approved=True)
    dash.post("/api/control", {"name": "worker:alpha", "state": "stop"})
    assert nxt(wa) == {"stop": True}
    dash.post("/api/control", {"name": "worker:alpha", "state": "run"})
    assert "ticket" in nxt(wa)


# ------------------------------------------------------------ jóváhagyás

def test_action_requires_human_approval(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    a = wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "trello", "action_type": "comment",
                                                    "target_reference": "abc", "payload": {"text": "kész"}})
    assert a["status"] == "PENDING_APPROVAL"
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "PENDING_APPROVAL"
    a2 = wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "email", "action_type": "send",
                                                     "payload": {"to": "x@example.com", "subject": "s", "body": "b"}})
    dash.post(f"/api/actions/{a2['id']}/decide", {"decision": "reject", "note": "nem kell"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "PENDING_APPROVAL"  # az első még nyitott
    wa.post(f"/api/actions/{a['id']}/decide", {"decision": "approve"}, ok=403)
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "approve"})
    # nincs Trello író adapter beállítva: APPROVED marad, kézi végrehajtásra vár
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "manual", "note": "kézzel kitettem"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "READY"
    assert nxt(wa)["ticket"]["id"] == t["id"]


def test_approved_action_executed_by_writer(monkeypatch, dash, two_projects):
    from agenthub import writers
    calls = []
    monkeypatch.setattr(writers, "execute", lambda *a, **k: calls.append(a) or {"status": 200})
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    a = wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "jira", "action_type": "comment",
                                                    "target_reference": "ABC-1", "payload": {"text": "ok"}})
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "approve"})
    import time
    for _ in range(50):
        if dash.get(f"/api/tickets/{t['id']}")["status"] == "READY":
            break
        time.sleep(0.05)
    assert calls and calls[0][3] == {"text": "ok"}
    assert dash.get("/api/actions")[0]["status"] == "EXECUTED"


def test_file_action_flow(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "file", "action_type": "edit",
                                                "payload": {"path": "../x", "new_content": "a"}}, ok=400)
    a = wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "file", "action_type": "edit",
                                                    "payload": {"path": "CLAUDE.md", "old_content": "a\n",
                                                                "new_content": "a\nb\n", "base_sha256": "x"}})
    assert "+b" in a["diff"]
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "approve"})
    fa = wa.get("/api/worker/file-actions")
    assert [x["id"] for x in fa] == [a["id"]]
    wa.post(f"/api/worker/file-actions/{a['id']}/report", {"ok": True, "result": {"backup": "/b"}})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "READY"


# ------------------------------------------------------------ memória

def test_knowledge_review_and_isolation(dash, two_projects):
    import os
    from pathlib import Path
    wa, wb = two_projects
    k = wa.post("/api/knowledge", {"category": "conventions", "title": "Commit", "content": "Magyar commitüzenet."})
    assert wa.get("/api/memory") == []  # jóváhagyás nélkül nem kerül be
    dash.post(f"/api/knowledge/{k['id']}/decide", {"decision": "accept", "content": "Angol commitüzenet."})
    mem = wa.get("/api/memory")
    assert [m["content"] for m in mem] == ["Angol commitüzenet."]
    assert wb.get("/api/memory") == [] and wb.get("/api/knowledge") == []
    f = Path(os.environ["AGENTHUB_DATA_DIR"]) / "projects/alpha/memory/conventions.md"
    assert "Angol commitüzenet." in f.read_text()


# ------------------------------------------------------------ források, Coordinator

def test_ingest_idempotent_routing_and_triage(dash, coord, two_projects):
    src = {"data": {"id": "mail", "name": "Levél", "adapter_type": "cli",
                    "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}}
    dash.post("/api/config/source", src, ok=400)  # nincs az engedélyezett listán
    g = dash.get("/api/config/global")["current"]
    g["routing_rules"] = [{"project": "beta", "title": "^\\[beta\\]"}]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", src)
    items = [{"external_id": "1", "title": "[beta] leállt"}, {"external_id": "2", "title": "Valami más"}]
    r = coord.post("/api/sources/mail/events", {"items": items})
    assert len(r["created"]) == 2 and len(r["routed"]) == 1
    r2 = coord.post("/api/sources/mail/events", {"items": items})
    assert r2["created"] == [] and r2["skipped"] == 2  # idempotens
    r3 = coord.post("/api/sources/mail/events", {"items": [{"external_id": "1", "title": "[beta] leállt", "description": "új komment"}]})
    assert r3["updated"] == r["created"][:1]
    pending = coord.get("/api/coordinator/pending")
    assert [p["title"] for p in pending] == ["Valami más"]
    tid = pending[0]["id"]
    coord.post(f"/api/tickets/{tid}/classify", {"triage": True, "reason": "nem egyértelmű"})
    t = dash.get(f"/api/tickets/{tid}")
    assert t["status"] == "NEEDS_TRIAGE" and t["project_id"] is None
    dash.post(f"/api/tickets/{tid}/transition", {"action": "triage"}, ok=400)  # projekt nélkül nem
    dash.post(f"/api/tickets/{tid}/transition", {"action": "triage", "project_id": "alpha"})
    assert dash.get(f"/api/tickets/{tid}")["status"] == "READY"


def test_coordinator_classify_with_go(dash, coord, two_projects):
    t = dash.post("/api/tickets", {"title": "Leállt az éles oldal"})
    assert t["status"] == "NEW"
    coord.post(f"/api/tickets/{t['id']}/classify", {"project_id": "alpha", "type": "bug", "priority": 1, "go": True}, ok=400)
    # indoklás nélküli Mehet: az egész besorolás visszagördül
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "NEW"


def test_coordinator_classify_with_go_reason(dash, coord, two_projects):
    wa, _ = two_projects
    t = dash.post("/api/tickets", {"title": "Leállt az éles oldal"})
    coord.post(f"/api/tickets/{t['id']}/classify", {"project_id": "alpha", "type": "bug", "priority": 1,
                                                    "go": True, "go_reason": "éles leállás"})
    assert nxt(wa)["ticket"]["id"] == t["id"]


# ------------------------------------------------------------ konfiguráció, hitelesítés

def test_invalid_config_not_activated_and_rollback(dash):
    make_project(dash, "alpha")
    dash.post("/api/config/project", {"data": {"id": "alpha", "name": "A", "working_directory": "relative"}}, ok=400)
    dash.post("/api/config/project", {"data": {"id": "gamma", "name": "G", "working_directory": "/srv/alpha/sub"}}, ok=400)
    g = dash.get("/api/config/global")["current"]
    dash.post("/api/config/global", {"data": {**g, "coordinator_poll_interval_s": 5}}, ok=400)
    dash.post("/api/config/global", {"data": {**g, "coordinator_poll_interval_s": 900}})
    dash.post("/api/config/global", {"data": {**g, "coordinator_poll_interval_s": 1200}})
    v = dash.post("/api/config/global/validate", {"data": {**g, "coordinator_poll_interval_s": 300}})
    assert v["ok"] and "-coordinator_poll_interval_s: 1200" in v["diff"]
    dash.post("/api/config/global/rollback", {"version": 1})
    cur = dash.get("/api/config/global")
    assert cur["current"]["coordinator_poll_interval_s"] == 900 and cur["history"][0]["version"] == 3


def test_config_export_import_roundtrip(client, dash):
    make_project(dash, "alpha")
    exported = client.get("/api/config/export", headers=dash.h).text
    r = client.post("/api/config-import", content=exported.replace("name: ALPHA", "name: Alfa"), headers=dash.h)
    assert r.status_code == 200, r.text
    assert dash.get("/api/projects/alpha")["name"] == "Alfa"


def test_cookie_login_and_csrf(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json={"key": "rossz"}).status_code == 401
    assert client.post("/api/login", json={"key": COORD_KEY}).status_code == 401  # csak dashboard-kulcs
    assert client.post("/api/login", json={"key": DASH_KEY}).status_code == 200
    assert client.get("/api/state").status_code == 200
    r = client.post("/api/control", json={"name": "coordinator", "state": "stop"})
    assert r.status_code == 403  # Origin nélkül nem
    r = client.post("/api/control", json={"name": "coordinator", "state": "stop"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def _age(sql, **params):
    from sqlalchemy import text
    from agenthub import db
    with db.session() as s:
        s.execute(text(sql), params)


def test_worker_with_valid_lease_counts_alive(client, dash):
    k = make_project(dash, "solo")
    w1, w2 = Api(client, k, "i1"), Api(client, k, "i2")
    w1.post("/api/agents/register", {"instance_id": "i1"})
    t = new(dash, "solo", start_approved=True)
    nxt(w1)
    _age("update agent_instances set last_heartbeat = now() - interval '2 hours' where id = 'i1'")
    w2.post("/api/agents/register", {"instance_id": "i2"}, ok=400)  # lease él: nem halott
    assert next(a for a in dash.get("/api/state")["agents"] if a["id"] == "i1")["alive"] is True
    _age("update tickets set lease_expires_at = now() - interval '1 minute' where id = :i", i=t["id"])
    w2.post("/api/agents/register", {"instance_id": "i2"})
    got = nxt(w2)  # lejárt lease: az új példány folytatja, eseménnyel
    assert got["ticket"]["id"] == t["id"] and got["resumed"] is True
    assert [e["event_type"] for e in dash.get(f"/api/tickets/{t['id']}")["events"]].count("resumed") == 1


def test_stale_worker_fenced_after_takeover(client, dash):
    k = make_project(dash, "solo")
    w1, w2 = Api(client, k, "i1"), Api(client, k, "i2")
    w1.post("/api/agents/register", {"instance_id": "i1"})
    t = new(dash, "solo", start_approved=True)
    nxt(w1)
    _age("update tickets set lease_expires_at = now() - interval '1 minute' where id = :i", i=t["id"])
    _age("update agent_instances set last_heartbeat = now() - interval '2 hours' where id = 'i1'")
    w2.post("/api/agents/register", {"instance_id": "i2"})
    assert nxt(w2)["ticket"]["id"] == t["id"]
    tid = t["id"]
    w1.post(f"/api/tickets/{tid}/note", {"text": "késő"}, ok=409)
    w1.post(f"/api/tickets/{tid}/ask", {"question": "?"}, ok=409)
    w1.post(f"/api/tickets/{tid}/finish", {"summary": "kész"}, ok=409)
    w1.post(f"/api/tickets/{tid}/fail", {"error": "x"}, ok=409)
    w1.post(f"/api/tickets/{tid}/actions", {"target_system": "trello", "action_type": "comment", "payload": {"text": "x"}}, ok=409)
    Api(client, k).post(f"/api/tickets/{tid}/note", {"text": "fejléc nélkül"}, ok=409)
    assert dash.get(f"/api/tickets/{tid}")["status"] == "IN_PROGRESS"
    w2.h["X-AgentHub-Lease"] = "old"
    w2.post(f"/api/tickets/{tid}/note", {"text": "rossz lease"}, ok=409)
    nxt(w2)  # ugyanaz a példány, érvényes lease: ugyanazt a lease-t kapja vissza
    w2.post(f"/api/tickets/{tid}/finish", {"summary": "kész"})
    assert dash.get(f"/api/tickets/{tid}")["status"] == "REVIEW"


def _approved_jira_action(dash, wa):
    t = new(dash, start_approved=True)
    nxt(wa)
    a = wa.post(f"/api/tickets/{t['id']}/actions", {"target_system": "jira", "action_type": "comment",
                                                    "target_reference": "ABC-1", "payload": {"text": "ok"}})
    return t, a


def _wait_action(dash, aid, statuses=("EXECUTED", "FAILED")):
    import time
    for _ in range(100):
        a = next(x for x in dash.get("/api/actions") if x["id"] == aid)
        if a["status"] in statuses:
            return a
        time.sleep(0.05)
    return a


def test_restart_recovers_approved_and_flags_interrupted(monkeypatch, dash, two_projects):
    from agenthub import writers
    from agenthub.api import app as appmod
    wa, _ = two_projects
    _, a1 = _approved_jira_action(dash, wa)
    monkeypatch.setattr(appmod, "_execute_async", lambda aid: None)  # "leállt" az approval után, mielőtt elindult
    dash.post(f"/api/actions/{a1['id']}/decide", {"decision": "approve"})
    _age("update pending_actions set status = 'EXECUTING' where id = :i", i=a1["id"])  # végrehajtás közben leállt
    monkeypatch.undo()
    calls = []
    monkeypatch.setattr(writers, "execute", lambda *a, **k: calls.append(a) or {"status": 200})
    appmod._startup()  # újraindulás
    got = _wait_action(dash, a1["id"])
    assert got["status"] == "FAILED" and got["execution_result"]["uncertain"] is True and not calls


def test_restart_runs_approved_not_started(monkeypatch, dash, two_projects):
    from agenthub import writers
    from agenthub.api import app as appmod
    wa, _ = two_projects
    _, a = _approved_jira_action(dash, wa)
    monkeypatch.setattr(appmod, "_execute_async", lambda aid: None)
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "approve"})
    monkeypatch.undo()
    calls = []
    monkeypatch.setattr(writers, "execute", lambda *x, **k: calls.append(x) or {"status": 200})
    appmod._startup()
    assert _wait_action(dash, a["id"])["status"] == "EXECUTED" and len(calls) == 1
    appmod._startup()  # második indulás: nem fut újra
    assert len(calls) == 1



def test_source_update_refreshes_fields_but_keeps_local_edits(dash, coord):
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": "trk", "name": "T", "adapter_type": "cli",
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})
    post = lambda **it: coord.post("/api/sources/trk/events", {"items": [{"external_id": "X-1", **it}]})
    tid = post(title="Régi cím", description="régi leírás", url="http://a")["created"][0]
    post(title="Új cím", description="új leírás", url="http://b")
    t = dash.get(f"/api/tickets/{tid}")
    assert (t["title"], t["description"], t["source_url"]) == ("Új cím", "új leírás", "http://b")
    r = dash.c.patch(f"/api/tickets/{tid}", json={"title": "Saját cím"}, headers=dash.h)
    assert r.status_code == 200, r.text
    post(title="Még újabb cím", description="harmadik", url="http://b")
    t = dash.get(f"/api/tickets/{tid}")
    assert t["title"] == "Saját cím" and t["description"] == "harmadik"  # az ember szerkesztése megmarad
    ev = [e for e in t["events"] if e["event_type"] == "source_update"][-1]["payload"]
    assert ev["kept_local"] == ["title"] and ev["applied"] == ["description"]


def test_expired_lease_fenced_until_reclaimed(client, dash):
    k = make_project(dash, "solo")
    w1 = Api(client, k, "i1")
    w1.post("/api/agents/register", {"instance_id": "i1"})
    t = new(dash, "solo", start_approved=True)
    first = nxt(w1)["lease_id"]
    _age("update tickets set lease_expires_at = now() - interval '1 minute' where id = :i", i=t["id"])
    # lejárt, de még senki nem vette át: a régi lease-szel sem írhat
    for path, body in (("note", {"text": "x"}), ("ask", {"question": "?"}), ("finish", {"summary": "k"}),
                       ("fail", {"error": "x"}),
                       ("actions", {"target_system": "trello", "action_type": "comment", "payload": {"text": "x"}})):
        w1.post(f"/api/tickets/{t['id']}/{path}", body, ok=409)
    again = nxt(w1)  # visszakéri: új lease-szel folytathatja
    assert again["ticket"]["id"] == t["id"] and again["lease_id"] != first
    w1.post(f"/api/tickets/{t['id']}/finish", {"summary": "kész"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "REVIEW"


def _src(dash, sid="trk", project=None):
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    g["routing_rules"] = [{"project": "alpha", "title": "webshop"}]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": sid, "name": sid, "adapter_type": "cli", "project": project,
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})


def test_semantic_duplicate_merged_into_existing(dash, coord, two_projects):
    _src(dash)
    _src(dash, "mail")
    ev = lambda sid, **it: coord.post(f"/api/sources/{sid}/events", {"items": [it]})
    first = ev("trk", external_id="T-1", title="A webshop kosár gomb nem működik mobilon", url="http://t/1")
    assert first["routed"]  # nincs hasonló: a szabály besorolja
    orig = first["created"][0]
    dup = ev("mail", external_id="<m1>", title="Re: a webshop kosár gomb nem működik mobilon",
             description="Androidon Chrome-ban is.")
    assert not dup["routed"]  # hasonló van: a Coordinator dönt, nem a szabály
    did = dup["created"][0]
    pending = coord.get("/api/coordinator/pending")
    assert pending[0]["id"] == did and pending[0]["similar"][0]["id"] == orig
    coord.post(f"/api/tickets/{did}/merge", {"into": orig, "addition": "Androidon Chrome-ban is előjön."})
    o, d = dash.get(f"/api/tickets/{orig}"), dash.get(f"/api/tickets/{did}")
    assert d["status"] == "CANCELLED" and "Androidon Chrome-ban is előjön." in o["description"]
    assert [(r["source_id"], r["external_id"]) for r in o["source_refs"]] == [("mail", "<m1>")]
    # az összevont forráselem későbbi frissítése a meglévő tickethez fut, új ticket nélkül
    later = ev("mail", external_id="<m1>", title="Re: a webshop kosár gomb nem működik mobilon", description="Már iOS-en is.")
    assert later["created"] == [] and later["updated"] == [orig]
    assert dash.get(f"/api/tickets/{orig}")["title"] == "A webshop kosár gomb nem működik mobilon"  # másodlagos forrás nem írja felül
    # lezárt ticketbe és nem NEW ticketet nem lehet összevonni
    coord.post(f"/api/tickets/{orig}/merge", {"into": did}, ok=400)


def test_similarity_ignores_unrelated_and_closed():
    from types import SimpleNamespace as N
    from agenthub import services as sv
    a, b = N(title="A webshop kosár gomb nem működik", description=""), N(title="Új számlázási riport exportja", description="")
    wa, wb = sv._words(a), sv._words(b)
    assert len(wa & wb) / len(wa | wb) < sv.DUP_THRESHOLD
