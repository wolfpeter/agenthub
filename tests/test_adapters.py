"""Adapter-szerződés: kérésforma, hibakezelés, titokkitakarás (HTTP-hívás nélkül, MockTransport-tal)."""
import httpx
import pytest

from agenthub import adapters, secretstore, writers
from agenthub.configschema import CliAdapterConfig, JiraAdapterConfig, TrelloAdapterConfig, WriterConfig


@pytest.fixture(autouse=True)
def fake_secrets(tmp_path, monkeypatch):
    (tmp_path / "sources.env").write_text("TRELLO_KEY=kkkkkkkk\nTRELLO_TOKEN=tttttttttt\nJIRA_EMAIL=a@b.c\nJIRA_TOKEN=jjjjjjjj\n")
    monkeypatch.setenv("AGENTHUB_SECRETS_DIR", str(tmp_path))


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_cli_adapter_no_shell_and_limits():
    cfg = CliAdapterConfig(executable="python3", args=["-c", "print('[{\"k\": 1, \"t\": \"a; rm -rf /\"}]')"],
                           fields={"external_id": "k", "title": "t"})
    assert adapters.run_cli(cfg, ["python3"]) == [{"external_id": "1", "title": "a; rm -rf /"}]
    with pytest.raises(adapters.AdapterError):
        adapters.run_cli(cfg, [])  # nincs engedélyezve
    slow = CliAdapterConfig(executable="python3", args=["-c", "import time; time.sleep(5)"], timeout_s=1,
                            fields={"external_id": "k", "title": "t"})
    with pytest.raises(adapters.AdapterError, match="időtúllépés"):
        adapters.run_cli(slow, ["python3"])
    bad = CliAdapterConfig(executable="python3", args=["-c", "print('nem json')"], fields={"external_id": "k", "title": "t"})
    with pytest.raises(adapters.AdapterError, match="nem JSON"):
        adapters.run_cli(bad, ["python3"])


def test_trello_reader_filters_lists():
    def h(req):
        assert req.url.params["token"] == "tttttttttt"
        return httpx.Response(200, json=[{"id": "c1", "shortLink": "AbCd1234", "name": "A", "idList": "L1", "shortUrl": "u"},
                                         {"id": "c2", "name": "B", "idList": "L2"}])
    items = adapters.run_trello(TrelloAdapterConfig(board_id="b", lists=["L1"]), client(h))
    assert [i["external_id"] for i in items] == ["AbCd1234"]


def test_jira_reader():
    def h(req):
        return httpx.Response(200, json={"issues": [{"key": "ABC-1", "fields": {"summary": "S", "description": None}}]})
    items = adapters.run_jira(JiraAdapterConfig(base_url="https://x.atlassian.net", jql="project=ABC"), client(h))
    assert items[0]["url"] == "https://x.atlassian.net/browse/ABC-1"


def test_writers_shape_and_masking():
    seen = []

    def ok(req):
        seen.append((req.method, str(req.url)))
        return httpx.Response(200, json={})
    cfg = WriterConfig(trello={}, jira={"base_url": "https://x.atlassian.net"})
    writers.execute("trello", "comment", "card1", {"text": "kész"}, cfg, client(ok))
    writers.execute("jira", "transition", "ABC-1", {"transition_id": 31}, cfg, client(ok))
    assert seen[0][0] == "POST" and "/cards/card1/actions/comments" in seen[0][1]
    assert seen[1][1].endswith("/rest/api/3/issue/ABC-1/transitions")

    def boom(req):
        raise httpx.ConnectError("hiba tttttttttt")
    with pytest.raises(writers.WriteError) as e:
        writers.execute("trello", "comment", "card1", {"text": "x"}, cfg, client(boom))
    assert "tttttttttt" not in str(e.value)
    with pytest.raises(writers.WriteError, match="nincs beállítva"):
        writers.execute("email", "send", None, {"to": "a", "subject": "s", "body": "b"}, WriterConfig())
    assert secretstore.mask("kulcs: kkkkkkkk") == "kulcs: ***"


def test_writer_timeout_marked_uncertain():
    def slow(req):
        raise httpx.ReadTimeout("lassú", request=req)

    def refused(req):
        raise httpx.ConnectError("nem elérhető", request=req)
    cfg = WriterConfig(jira={"base_url": "https://x.atlassian.net"})
    with pytest.raises(writers.WriteError) as e:
        writers.execute("jira", "comment", "ABC-1", {"text": "x"}, cfg, client(slow))
    assert e.value.uncertain
    with pytest.raises(writers.WriteError) as e:
        writers.execute("jira", "comment", "ABC-1", {"text": "x"}, cfg, client(refused))
    assert not e.value.uncertain
