import time

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
    wa.post(f"/api/tickets/{t['id']}/ask", {"question": "Melyik ág?"}, ok=400)  # javaslat nélkül nem kérdezhet
    wa.post(f"/api/tickets/{t['id']}/ask", {"question": "Melyik ág?", "suggested_answer": "  "}, ok=400)
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
    # nincs Trello író adapter beállítva: APPROVED marad, kézi végrehajtásra vár (a háttérszál után)
    for _ in range(100):
        if dash.get(f"/api/tickets/{t['id']}")["actions"][0]["status"] != "EXECUTING":
            break
        time.sleep(0.02)
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
    assert nxt(w1).get("ticket") is None  # a régi példány nem veheti vissza, amíg B lease-e él
    assert dash.get(f"/api/tickets/{tid}")["assigned_worker_id"] == "i2"
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
    dup = ev("mail", external_id="<m1>", thread_id="<m1>", title="Re: a webshop kosár gomb nem működik mobilon",
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
    # ugyanabban a levélszálban érkező új levél (új azonosító) is a meglévő tickethez megy
    reply = coord.post("/api/sources/mail/events", {"items": [{"external_id": "<m2>", "thread_id": "<m1>",
                                                               "title": "Re: Re: kosár", "description": "még mindig"}]})
    assert reply["created"] == [] and reply["updated"] == [orig]
    # lezárt ticketbe és nem NEW ticketet nem lehet összevonni
    coord.post(f"/api/tickets/{orig}/merge", {"into": did}, ok=400)


def test_list_tag_does_not_make_cards_similar(dash, coord):
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": "trk", "name": "T", "adapter_type": "cli",
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})
    lst = "Élesítve, ellenőrizendő (PROD)"
    coord.post("/api/sources/trk/events", {"items": [{"external_id": "A", "title": "Eseménynaptár képei túl sötétek", "list": lst}]})
    r = coord.post("/api/sources/trk/events", {"items": [{"external_id": "B", "title": "Email cím megjelenítése", "list": lst}]})
    assert coord.get("/api/coordinator/pending")[-1]["similar"] == [] and len(r["created"]) == 1


def test_similarity_scores():
    from types import SimpleNamespace as N
    from agenthub import services as sv
    base = N(title="A webshop kosár gomb nem működik mobilon", description="Mobilon a Kosárba gomb megnyomására nem történik semmi.")
    dup = N(title="Mobilon nem működik a kosár gomb a webshopban", description="Androidon Chrome-ban sem reagál a Kosárba gomb, "
            "iPhone Safariban ugyanez. A konzolban 'TypeError: cart is undefined' hiba látszik.")  # hosszú leírás, ragozott alakok
    other = N(title="Nem működik a bejelentkezés", description="Hibás jelszó üzenet jön helyes jelszóra is")
    unrelated = N(title="Új számlázási riport exportja", description="CSV export kell a havi számlákról")
    score = lambda o: sv._similarity(sv._words(base), sv._words(o))
    assert score(dup) >= sv.DUP_THRESHOLD
    assert score(other) < sv.DUP_THRESHOLD and score(unrelated) == 0


def test_budget_agent_stats_cycle_and_ticket_types(dash, coord, two_projects):
    wa, _ = two_projects
    dash.post("/api/config/project", {"data": {"id": "alpha", "name": "ALPHA", "working_directory": "/srv/alpha",
                                               "monthly_budget_usd": 1.0, "ticket_types": ["bug", "task"]}})
    dash.post("/api/tickets", {"title": "x", "project_id": "alpha", "type": "feature"}, ok=400)  # nem engedélyezett típus
    t = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{t['id']}/finish", {"summary": "kész", "run": {"model": "sonnet", "tokens_in": 1000,
                                                                          "tokens_out": 100, "cost_usd": 1.5}})
    st = dash.get("/api/state")
    alpha = next(p for p in st["projects"] if p["id"] == "alpha")
    assert alpha["month_cost_usd"] == 1.5 and alpha["over_budget"] is True
    beta = next(p for p in st["projects"] if p["id"] == "beta")
    assert beta["over_budget"] is False
    stats = next(a for a in st["agents"] if a["id"] == "wa")["stats"]
    assert stats["runs"] == 1 and stats["tokens_in"] == 1000 and stats["cost_usd"] == 1.5
    assert st["last_cycle"] is None
    coord.post("/api/coordinator/cycle", {"sources_checked": 2, "created": 1, "errors": ["x: hiba"]})
    assert dash.get("/api/state")["last_cycle"]["sources_checked"] == 2


def test_config_change_flags_restart_and_hooks(dash):
    base = {"id": "gamma", "name": "G", "working_directory": "/srv/gamma"}
    assert dash.post("/api/config/project", {"data": base})["restart_required"] is None  # új projekt
    r = dash.post("/api/config/project", {"data": {**base, "worker": {"instructions": "x"}}})
    assert r["restart_required"] is None  # az utasítást a Worker a következő ticketnél átveszi
    hooks = {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "npm run lint"}]}]}
    r = dash.post("/api/config/project", {"data": {**base, "worker": {"instructions": "x", "hooks": hooks}}})
    assert "gamma" in r["restart_required"]
    g = dash.get("/api/config/global")["current"]
    assert dash.post("/api/config/global", {"data": {**g, "coordinator_model": "sonnet"}})["restart_required"]


def test_request_log_has_correlation_fields(client, dash, caplog):
    import json as _json
    import logging
    from agenthub.api import app as appmod
    appmod.apply_log_level("DEBUG")
    records = []
    h = logging.Handler()
    h.emit = records.append
    appmod.log.addHandler(h)
    try:
        r = client.get("/api/tickets/999999", headers={**dash.h, "X-Request-ID": "abc123"})
    finally:
        appmod.log.removeHandler(h)
        appmod.apply_log_level("INFO")
    assert r.status_code == 404 and r.headers["x-request-id"] == "abc123"
    ctx = next(x.ctx for x in records if getattr(x, "ctx", {}).get("request_id") == "abc123")
    assert ctx["ticket_id"] == 999999 and ctx["status"] == 404 and ctx["actor"]
    assert _json.loads(appmod._JsonLog().format(records[-1]))["request_id"] == "abc123"


def test_network_guard_blocks_public_clients():
    from agenthub.api.app import _private_client
    for ok in ("127.0.0.1", "10.1.2.3", "172.20.0.1", "192.168.0.151", "100.110.55.26", "::1", "::ffff:192.168.1.5", "testclient"):
        assert _private_client(ok), ok
    for bad in ("8.8.8.8", "172.32.0.1", "100.128.0.1", "2001:4860::1", None, ""):
        assert not _private_client(bad), bad


def test_cost_reported_estimated_or_unknown(dash, two_projects):
    wa, _ = two_projects
    g = dash.get("/api/config/global")["current"]
    g["models"]["prices"] = {"sonnet": {"input": 3.0, "output": 15.0}}
    dash.post("/api/config/global", {"data": g})
    runs = []
    for run in ({"model": "sonnet", "tokens_in": 1_000_000, "tokens_out": 100_000},  # becsült: 3 + 1.5
                {"model": "sonnet", "tokens_in": 10, "tokens_out": 10, "cost_usd": 0.42},  # bejelentett
                {"model": "ismeretlen", "tokens_in": 10, "tokens_out": 10}):  # nincs ár
        t = new(dash, start_approved=True)
        nxt(wa)
        wa.post(f"/api/tickets/{t['id']}/finish", {"summary": "kész", "run": run})
        runs.append(dash.get(f"/api/tickets/{t['id']}")["runs"][-1])
        dash.post(f"/api/tickets/{t['id']}/transition", {"action": "accept"})
    assert [(r["cost_source"], r["estimated_cost"]) for r in runs] == [("estimated", 4.5), ("reported", 0.42), ("unknown", None)]


def test_network_guard_wired(dash):
    from fastapi.testclient import TestClient
    from agenthub.api.app import app
    with TestClient(app, client=("8.8.8.8", 5000)) as public:
        assert public.get("/api/health").status_code == 403
    with TestClient(app, client=("100.110.55.26", 5000)) as tailnet:
        assert tailnet.get("/api/health").status_code == 200


def test_new_source_comment_refreshes_ticket(dash, coord):
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": "trk", "name": "T", "adapter_type": "cli",
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})
    post = lambda **it: coord.post("/api/sources/trk/events", {"items": [{"external_id": "X-1", "title": "Hiba", **it}]})
    tid = post(description="leírás", comments="Anna (1):\nelső")["created"][0]
    assert "első" in dash.get(f"/api/tickets/{tid}")["description"]
    post(description="leírás", comments="Anna (1):\nelső\n\nBéla (2):\nmásodik")
    t = dash.get(f"/api/tickets/{tid}")
    assert "második" in t["description"]
    ev = [e for e in t["events"] if e["event_type"] == "source_update"][-1]["payload"]
    assert "második" in ev["comments"] and ev["applied"] == ["description"]


# ------------------------------------------------------------ release-ciklus, blokkolt, hotfix, Coordinator-frissítések

def test_release_cycle_block_and_self_complete(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{t['id']}/finish", {"summary": "kész", "to": "done"}, ok=400)  # review kötelező
    wa.post(f"/api/tickets/{t['id']}/finish", {"summary": "UAT-on", "to": "release"})
    assert [x["id"] for x in wa.get("/api/release")] == [t["id"]]
    wa.post(f"/api/tickets/{t['id']}/released", {"summary": "v1.2 prodon mérve"})  # lease nélkül
    d = dash.get(f"/api/tickets/{t['id']}")
    assert d["status"] == "COMPLETED" and "v1.2" in d["result_summary"]
    wa.post(f"/api/tickets/{t['id']}/released", {"summary": "x"}, ok=400)
    r = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{r['id']}/finish", {"summary": "UAT-on", "to": "release"})
    wa.post(f"/api/tickets/{r['id']}/released", {"summary": "prodon 500-at ad", "failed": True})
    d = dash.get(f"/api/tickets/{r['id']}")
    assert d["status"] == "READY" and d["priority"] == 1 and d["start_approved"]
    dash.post(f"/api/tickets/{r['id']}/transition", {"action": "cancel"})

    b = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{b['id']}/block", {"reason": "a megrendelő anyagára vár"})
    assert dash.get(f"/api/tickets/{b['id']}")["status"] == "BLOCKED"
    assert nxt(wa) == {"idle": True}

    cfg = dash.get("/api/config/project/alpha")["current"]
    cfg["worker"]["review_required"] = False
    dash.post("/api/config/project", {"data": cfg})
    c = new(dash, start_approved=True)
    nxt(wa)
    wa.post(f"/api/tickets/{c['id']}/finish", {"summary": "megválaszolva", "to": "done"})
    assert dash.get(f"/api/tickets/{c['id']}")["status"] == "COMPLETED"


def test_hotfix_human_only_first_in_queue_and_voided(dash, coord, two_projects):
    wa, _ = two_projects
    first = new(dash, priority=1, start_approved=True)
    hot = new(dash, priority=3)
    wa.post(f"/api/tickets/{hot['id']}/hotfix", {"value": True}, ok=403)
    coord.post(f"/api/tickets/{hot['id']}/hotfix", {"value": True}, ok=403)
    dash.post(f"/api/tickets/{hot['id']}/hotfix", {"value": True})
    d = dash.get(f"/api/tickets/{hot['id']}")
    assert d["hotfix"] and d["start_approved"] and d["priority"] == 1 and d["hotfix_by"]
    assert wa.get("/api/worker/peek")["ticket"]["id"] == hot["id"]
    assert nxt(wa)["ticket"]["id"] == hot["id"] and first["id"] != hot["id"]
    wa.post(f"/api/tickets/{hot['id']}/finish", {"summary": "UAT ok", "to": "release"})
    assert wa.get("/api/release")[0]["hotfix"] is True
    coord.post(f"/api/tickets/{hot['id']}/coordinator", {"action": "reopen", "reason": "új hibajelzés"})
    assert dash.get(f"/api/tickets/{hot['id']}")["hotfix"] is False  # újranyitás után nem él az engedély


def test_coordinator_gets_source_updates_and_acts(dash, coord, two_projects):
    wa, _ = two_projects
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": "trk", "name": "T", "adapter_type": "cli", "project": "alpha",
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})
    post = lambda **it: coord.post("/api/sources/trk/events", {"items": [{"external_id": "C1", "title": "Kártya", **it}]})
    tid = post(list="Backlog")["created"][0]
    cur = coord.get("/api/coordinator/updates")["cursor"]
    assert "Lista: Backlog" in dash.get(f"/api/tickets/{tid}")["description"]
    dash.post(f"/api/tickets/{tid}/go", {"value": True})
    nxt(wa)
    wa.post(f"/api/tickets/{tid}/note", {"text": "Komment a kártyára + mozgatás Review / UAT-ba"})
    wa.post(f"/api/tickets/{tid}/finish", {"summary": "UAT-on", "to": "release"})
    post(list="Review / UAT")  # mi mozgattuk: a Coordinator a recent_notes-ból látja, hogy a visszhangunk
    post(list="Review / UAT", comments="Ügyfél (1):\nnem jó a gomb")
    r = coord.get(f"/api/coordinator/updates?after={cur}")
    assert [u["ticket"] for u in r["updates"]] == [tid, tid]
    assert r["updates"][0]["list_from"] == "Backlog" and "nem jó" in r["updates"][1]["comments"]
    assert "mozgatás Review / UAT-ba" in r["updates"][0]["recent_notes"][0]
    assert coord.get(f"/api/coordinator/updates?after={r['cursor']}")["updates"] == []
    coord.post(f"/api/tickets/{tid}/coordinator", {"action": "reopen", "reason": "UAT-visszajelzés", "priority": 1,
                                                   "title": "[UAT] Kártya"})
    d = dash.get(f"/api/tickets/{tid}")
    assert d["status"] == "READY" and d["priority"] == 1 and d["title"] == "[UAT] Kártya" and d["start_approved"]
    wa.post(f"/api/tickets/{tid}/coordinator", {"action": "reopen", "reason": "x"}, ok=403)
    n = coord.post("/api/tickets", {"title": "Automata értesítő"})
    coord.post(f"/api/tickets/{n['id']}/coordinator", {"action": "dismiss"}, ok=400)  # indoklás nélkül nem
    coord.post(f"/api/tickets/{n['id']}/coordinator", {"action": "dismiss", "reason": "automata levél"})
    q = coord.post("/api/tickets", {"title": "Hol találom?"})
    coord.post(f"/api/tickets/{q['id']}/coordinator", {"action": "done", "reason": "megválaszoltuk a kártyán"})
    assert [dash.get(f"/api/tickets/{x}")["status"] for x in (n["id"], q["id"])] == ["CANCELLED", "COMPLETED"]


def test_reply_to_dismissed_thread_opens_new_item(dash, coord):
    g = dash.get("/api/config/global")["current"]
    g["cli_executables"] = ["echo"]
    dash.post("/api/config/global", {"data": g})
    dash.post("/api/config/source", {"data": {"id": "mail", "name": "M", "adapter_type": "cli",
                                              "settings": {"executable": "echo", "fields": {"external_id": "id", "title": "t"}}}})
    first = coord.post("/api/sources/mail/events", {"items": [{"external_id": "m1", "thread_id": "T", "title": "Hírlevél"}]})["created"][0]
    coord.post(f"/api/tickets/{first}/coordinator", {"action": "dismiss", "reason": "automata"})
    r = coord.post("/api/sources/mail/events", {"items": [{"external_id": "m2", "thread_id": "T", "title": "Re: kérdésem van"}]})
    assert len(r["created"]) == 1 and r["created"][0] != first


def test_project_disable_delete_and_folder_picker(dash, client, tmp_path, monkeypatch):
    from agenthub.api import app as appmod
    make_project(dash, "alpha")
    make_project(dash, "fresh")
    t = new(dash, project_id="alpha")
    # elengedés: nem sorolható be oda, a fejléc a state-ből látja
    assert dash.post("/api/projects/alpha/active", {"value": False})["is_active"] is False
    dash.post(f"/api/tickets/{t['id']}/transition", {"action": "triage", "project_id": "alpha"}, ok=400)
    st = {p["id"]: p for p in dash.get("/api/state")["projects"]}
    assert st["alpha"]["is_active"] is False and st["alpha"]["ticket_count"] == 1 and st["fresh"]["ticket_count"] == 0
    assert dash.post("/api/projects/alpha/active", {"value": True})["is_active"] is True
    # törlés csak nyom nélkül
    assert client.delete("/api/projects/alpha", headers=dash.h).status_code == 400
    assert client.delete("/api/projects/fresh", headers=dash.h).status_code == 200
    assert all(p["id"] != "fresh" for p in dash.get("/api/projects"))
    # mappaválasztó: csak a gyökér alatt, link kifelé nem
    root = tmp_path / "root"
    (root / "repo" / ".git").mkdir(parents=True)
    (root / "plain").mkdir()
    (root / "out").symlink_to(tmp_path)
    monkeypatch.setattr(appmod, "FS_MOUNT", str(root))
    assert dash.get("/api/fs/dirs")["root"] is None  # nincs beállítva
    monkeypatch.setenv("AGENTHUB_PROJECTS_ROOT_HOST", "/home/u/Projektek")
    r = dash.get("/api/fs/dirs")
    assert [(d["name"], d["git"]) for d in r["dirs"]] == [("plain", False), ("repo", True)] and r["parent"] is None
    assert dash.get("/api/fs/dirs?path=/home/u/Projektek/repo")["parent"] == "/home/u/Projektek"
    assert client.get("/api/fs/dirs?path=/home/u/Projektek/../..", headers=dash.h).status_code == 400
    assert client.get("/api/fs/dirs?path=/etc", headers=dash.h).status_code == 400


def test_dashboard_comment_reaches_worker(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, "alpha", start_approved=True)
    dash.post(f"/api/tickets/{t['id']}/comment", {"text": "  Csak a mobil nézetet javítsd.  "})
    dash.post(f"/api/tickets/{t['id']}/comment", {"text": " "}, ok=400)
    r = nxt(wa)
    assert [c["text"] for c in r["ticket"]["comments"]] == ["Csak a mobil nézetet javítsd."]
    wa.post(f"/api/tickets/{t['id']}/note", {"text": "worker note"})
    wa.post(f"/api/tickets/{t['id']}/comment", {"text": "álcázott"}, ok=403)
    assert [c["text"] for c in wa.get(f"/api/tickets/{t['id']}")["comments"]] == ["Csak a mobil nézetet javítsd."]


def test_coordinator_review_never_touches_worker_tickets(dash, coord, two_projects):
    wa, _ = two_projects
    busy = new(dash, "alpha", title="Worker dolgozik", start_approved=True)
    rel = new(dash, "alpha", title="UAT-on kész", start_approved=True)
    idle = new(dash, "alpha", title="Kártya archiválva")
    assert nxt(wa)["ticket"]["id"] == busy["id"]
    # a Workernél lévő ticketet a Coordinator nem zárja, nem nyitja, nem veti el
    for act in ("done", "reopen", "dismiss"):
        coord.post(f"/api/tickets/{busy['id']}/coordinator", {"action": act, "reason": "egyeztetés"}, ok=400)
    coord.post(f"/api/tickets/{busy['id']}/coordinator", {"action": "note", "reason": "a kártya PROD-on"})
    wa.post(f"/api/tickets/{busy['id']}/finish", {"summary": "kész", "to": "release"})
    review = {t["id"]: t["status"] for t in coord.get("/api/coordinator/review")}
    assert review[busy["id"]] == "AWAITING_RELEASE" and review[idle["id"]] == "READY"
    assert nxt(wa)["ticket"]["id"] == rel["id"]
    assert rel["id"] not in {t["id"] for t in coord.get("/api/coordinator/review")}
    # a forrásban már élesített / archivált munka
    coord.post(f"/api/tickets/{busy['id']}/coordinator", {"action": "done", "reason": "a kártya a PROD-listán"})
    coord.post(f"/api/tickets/{idle['id']}/coordinator", {"action": "dismiss", "reason": "a kártya archiválva"})
    assert dash.get(f"/api/tickets/{busy['id']}")["status"] == "COMPLETED"
    assert dash.get(f"/api/tickets/{idle['id']}")["status"] == "CANCELLED"


def test_manual_mail_copy_and_confirm(dash, two_projects):
    wa, _ = two_projects
    t = new(dash, "alpha", start_approved=True)
    nxt(wa)
    url = f"/api/tickets/{t['id']}/actions"
    wa.post(url, {"target_system": "email", "action_type": "send", "payload": {"to": "a@b.hu", "subject": "Tárgy"}}, ok=400)
    a = wa.post(url, {"target_system": "email", "action_type": "send",
                      "payload": {"to": ["a@b.hu"], "subject": "Tárgy", "body": "Szia!\n\nKész."}})
    assert dash.get("/api/state")["controls"]["email_writer"] is False  # nincs küldő adapter: ember küldi
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "PENDING_APPROVAL"
    dash.post(f"/api/actions/{a['id']}/decide", {"decision": "manual", "note": "elküldve"})
    assert dash.get(f"/api/tickets/{t['id']}")["status"] == "READY"
