"""Író adapterek: kizárólag APPROVED PendingAction, pontosan a jóváhagyott payloaddal (SPECIFICATION 10)."""
import smtplib
from email.message import EmailMessage

import httpx

from . import secretstore
from .configschema import WriterConfig


class WriteError(Exception):
    def __init__(self, msg: str, uncertain: bool = False):
        super().__init__(msg)
        self.uncertain = uncertain  # a kérés kimehetett: vakon újraküldeni tilos


# a kérés elküldése után szakadt meg: nem tudni, megtörtént-e
_UNCERTAIN = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, TimeoutError, smtplib.SMTPServerDisconnected)


def execute(target: str, action_type: str, reference: str | None, payload: dict, cfg: WriterConfig,
            client: httpx.Client | None = None) -> dict:
    fn = {"email": _email, "jira": _jira, "trello": _trello}.get(target)
    if fn is None:
        raise WriteError(f"nincs író adapter ehhez: {target}")
    conf = getattr(cfg, target)
    if conf is None:  # {} = beállítva, alapértelmezett titoknevekkel
        raise WriteError(f"a(z) {target} író adapter nincs beállítva; hajtsd végre kézzel és rögzítsd")
    try:
        return fn(action_type, reference, payload, conf, client)
    except WriteError:
        raise
    except Exception as e:  # a titok nem kerülhet a hibaüzenetbe
        raise WriteError(secretstore.mask(f"{type(e).__name__}: {e}"), isinstance(e, _UNCERTAIN)) from None


def _email(action_type, reference, p, conf, client):
    if action_type != "send":
        raise WriteError("e-mailnél csak 'send' művelet van")
    for k in ("to", "subject", "body"):
        if not p.get(k):
            raise WriteError(f"hiányzó mező: {k}")
    msg = EmailMessage()
    msg["From"] = conf["from"]
    msg["To"] = p["to"] if isinstance(p["to"], str) else ", ".join(p["to"])
    if p.get("cc"):
        msg["Cc"] = p["cc"] if isinstance(p["cc"], str) else ", ".join(p["cc"])
    msg["Subject"] = p["subject"]
    if p.get("in_reply_to"):
        msg["In-Reply-To"] = msg["References"] = p["in_reply_to"]
    msg.set_content(p["body"])
    with smtplib.SMTP(conf["host"], int(conf.get("port", 587)), timeout=30) as smtp:
        if conf.get("starttls", True):
            smtp.starttls()
        smtp.login(secretstore.get(conf["username_secret"]), secretstore.get(conf["password_secret"]))
        smtp.send_message(msg)
    return {"sent_to": msg["To"]}


def _http(client):
    return client or httpx.Client(timeout=30)


def _jira(action_type, reference, p, conf, client):
    if not reference:
        raise WriteError("Jira-műveletnél a target_reference az issue kulcsa (pl. ABC-123)")
    base = conf["base_url"].rstrip("/")
    auth = (secretstore.get(conf.get("email_secret", "JIRA_EMAIL")), secretstore.get(conf.get("token_secret", "JIRA_TOKEN")))
    c = _http(client)
    if action_type == "comment":
        body = {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p["text"]}]}]}}
        r = c.post(f"{base}/rest/api/3/issue/{reference}/comment", json=body, auth=auth)
    elif action_type == "transition":
        r = c.post(f"{base}/rest/api/3/issue/{reference}/transitions", json={"transition": {"id": str(p["transition_id"])}}, auth=auth)
    elif action_type == "update":
        r = c.put(f"{base}/rest/api/3/issue/{reference}", json={"fields": p["fields"]}, auth=auth)
    else:
        raise WriteError(f"ismeretlen Jira-művelet: {action_type}")
    if r.status_code >= 300:
        raise WriteError(f"Jira válasz {r.status_code}: {r.text[:300]}")
    return {"status": r.status_code}


def _trello(action_type, reference, p, conf, client):
    if not reference:
        raise WriteError("Trello-műveletnél a target_reference a kártya azonosítója")
    auth = {"key": secretstore.get(conf.get("key_secret", "TRELLO_KEY")),
            "token": secretstore.get(conf.get("token_secret", "TRELLO_TOKEN"))}
    c = _http(client)
    base = "https://api.trello.com/1"
    if action_type == "comment":
        r = c.post(f"{base}/cards/{reference}/actions/comments", params={**auth, "text": p["text"]})
    elif action_type == "move":
        r = c.put(f"{base}/cards/{reference}", params={**auth, "idList": p["list_id"]})
    elif action_type == "update":
        allowed = {k: v for k, v in p.items() if k in ("name", "desc", "due", "closed")}
        if not allowed:
            raise WriteError("Trello update: name, desc, due vagy closed mező kell")
        r = c.put(f"{base}/cards/{reference}", params={**auth, **allowed})
    else:
        raise WriteError(f"ismeretlen Trello-művelet: {action_type}")
    if r.status_code >= 300:
        raise WriteError(f"Trello válasz {r.status_code}: {r.text[:300]}")
    return {"status": r.status_code}
