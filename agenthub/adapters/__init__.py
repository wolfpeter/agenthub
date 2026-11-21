"""Olvasó forrásadapterek (SPECIFICATION 11). A hoston futnak, a Coordinator `wait` parancsából, modellhívás nélkül.
Kimenet: normalizált elemek {external_id, title, description, url, thread_id, sender, updated_at}."""
import email
import email.header
import imaplib
import json
import shutil
import subprocess

import httpx

from .. import secretstore
from ..configschema import ADAPTER_CONFIGS


class AdapterError(Exception):
    pass


def pluck(obj, path: str):
    for part in [p for p in path.split(".") if p]:
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def run_cli(cfg, allowlist: list[str]) -> list[dict]:
    if cfg.executable not in allowlist:
        raise AdapterError(f"'{cfg.executable}' nincs az engedélyezett programok között")
    exe = shutil.which(cfg.executable)
    if not exe:
        raise AdapterError(f"'{cfg.executable}' nem található a PATH-on")
    try:
        # argumentumlista, shell nélkül: nincs string-interpoláció
        p = subprocess.run([exe, *cfg.args], capture_output=True, timeout=cfg.timeout_s, shell=False)
    except subprocess.TimeoutExpired:
        raise AdapterError(f"időtúllépés ({cfg.timeout_s} s)") from None
    if len(p.stdout) > cfg.max_output_kb * 1024:
        raise AdapterError(f"túl nagy kimenet (> {cfg.max_output_kb} KB)")
    if p.returncode != 0:
        raise AdapterError(f"kilépési kód {p.returncode}: {secretstore.mask(p.stderr.decode(errors='replace'))[:500]}")
    try:
        data = json.loads(p.stdout or b"null")
    except json.JSONDecodeError as e:
        raise AdapterError(f"a kimenet nem JSON: {e}") from None
    items = pluck(data, cfg.items_path) if cfg.items_path else data
    if not isinstance(items, list):
        raise AdapterError("a kimenetben nem lista áll a megadott items_path alatt")
    out = []
    for it in items:
        row = {k: pluck(it, path) for k, path in cfg.fields.items()}
        if row.get("external_id") is not None and row.get("title"):
            out.append({k: (v if isinstance(v, str) or v is None else json.dumps(v, ensure_ascii=False)
                            if isinstance(v, (dict, list)) else str(v)) for k, v in row.items()})
    return out


def _decode(h):
    return str(email.header.make_header(email.header.decode_header(h or "")))


def run_imap(cfg) -> list[dict]:
    """Csak olvas: BODY.PEEK, így nem jelöli olvasottnak a levelet."""
    try:
        m = imaplib.IMAP4_SSL(cfg.host, cfg.port, timeout=60)
        m.login(secretstore.get(cfg.username_secret), secretstore.get(cfg.password_secret))
        m.select(cfg.folder, readonly=True)
        typ, data = m.search(None, cfg.search)
        ids = data[0].split()[-cfg.max_messages:] if data and data[0] else []
        out = []
        for i in ids:
            typ, msg = m.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg[0][1])
            body = ""
            for part in msg.walk() if msg.is_multipart() else [msg]:
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
                    break
            mid = msg.get("Message-ID") or f"{cfg.folder}:{i.decode()}"
            refs = (msg.get("References") or "").split()
            out.append({"external_id": mid, "thread_id": refs[0] if refs else mid,
                        "title": _decode(msg.get("Subject")) or "(nincs tárgy)", "description": body[:20000],
                        "sender": _decode(msg.get("From")), "updated_at": msg.get("Date")})
        m.logout()
        return out
    except (imaplib.IMAP4.error, OSError) as e:
        raise AdapterError(secretstore.mask(f"IMAP: {e}")) from None


MAX_COMMENTS = 10


def _comments(rows: list[tuple]) -> str | None:
    """Normalizált kommentblokk (szerző, idő, szöveg), a legutóbbi MAX_COMMENTS darab, időrendben."""
    rows = [r for r in rows if r[2]][-MAX_COMMENTS:]
    return "\n\n".join(f"{a or '?'} ({d or '?'}):\n{t}" for a, d, t in rows)[:20000] or None


def _adf_text(node) -> str:
    """Jira Cloud v3: az Atlassian Document Format szöveges tartalma."""
    if isinstance(node, dict):
        return node.get("text", "") + "".join(_adf_text(c) for c in node.get("content", [])) + \
            ("\n" if node.get("type") == "paragraph" else "")
    if isinstance(node, list):
        return "".join(_adf_text(c) for c in node)
    return str(node or "")


def run_trello(cfg, client=None) -> list[dict]:
    c = client or httpx.Client(timeout=60)
    params = {"key": secretstore.get(cfg.key_secret), "token": secretstore.get(cfg.token_secret),
              "fields": "name,desc,shortUrl,shortLink,idList,labels,dateLastActivity",
              "actions": "commentCard", "actions_limit": MAX_COMMENTS}
    r = c.get(f"https://api.trello.com/1/boards/{cfg.board_id}/cards", params=params)
    if r.status_code >= 300:
        raise AdapterError(f"Trello {r.status_code}")
    lr = c.get(f"https://api.trello.com/1/boards/{cfg.board_id}/lists",
               params={"key": params["key"], "token": params["token"], "fields": "name"})
    if lr.status_code >= 300:
        raise AdapterError(f"Trello {lr.status_code}")
    names = {x["id"]: x.get("name") for x in lr.json()}
    out = []
    for card in r.json():
        if cfg.lists and card.get("idList") not in cfg.lists:
            continue
        # shortLink: stabil, a kártya URL-jében is ez szerepel, és az író adapter is elfogadja
        acts = sorted(card.get("actions") or [], key=lambda x: x.get("date") or "")
        comments = _comments([((x.get("memberCreator") or {}).get("fullName"), x.get("date"), (x.get("data") or {}).get("text"))
                              for x in acts if x.get("type") == "commentCard"])
        out.append({"external_id": card.get("shortLink") or card["id"], "title": card.get("name"), "description": card.get("desc") or "",
                    "url": card.get("shortUrl"), "updated_at": card.get("dateLastActivity"), "comments": comments,
                    "list": names.get(card.get("idList")),
                    "labels": ", ".join(x.get("name") or x.get("color") or "" for x in card.get("labels") or []) or None})
    return out


def run_jira(cfg, client=None) -> list[dict]:
    c = client or httpx.Client(timeout=60)
    auth = (secretstore.get(cfg.email_secret), secretstore.get(cfg.token_secret))
    base = cfg.base_url.rstrip("/")
    r = c.get(f"{base}/rest/api/3/search/jql", params={"jql": cfg.jql, "maxResults": cfg.max_results,
                                                       "fields": "summary,description,updated,comment"}, auth=auth)
    if r.status_code >= 300:
        raise AdapterError(f"Jira {r.status_code}")
    out = []
    for it in r.json().get("issues", []):
        f = it.get("fields", {})
        desc = f.get("description")
        cs = (f.get("comment") or {}).get("comments") or []
        comments = _comments([((x.get("author") or {}).get("displayName"), x.get("created"),
                               x["body"] if isinstance(x.get("body"), str) else _adf_text(x.get("body")).strip()) for x in cs])
        out.append({"external_id": it["key"], "title": f.get("summary"),
                    "description": desc if isinstance(desc, str) else _adf_text(desc).strip()[:20000] if desc else "",
                    "url": f"{base}/browse/{it['key']}", "updated_at": f.get("updated"), "comments": comments})
    return out


def poll(source: dict, allowlist: list[str]) -> list[dict]:
    """source: a /api/coordinator/context forráseleme (config = SourceConfig)."""
    sc = source["config"]
    kind = sc["adapter_type"]
    cfg = ADAPTER_CONFIGS[kind].model_validate(sc.get("settings") or {})
    try:
        if kind == "cli":
            return run_cli(cfg, allowlist)
        if kind == "imap":
            return run_imap(cfg)
        if kind == "trello":
            return run_trello(cfg)
        if kind == "jira":
            return run_jira(cfg)
    except httpx.HTTPError as e:  # hálózati hiba: csak ez a forrás hibás, a ciklus megy tovább
        raise AdapterError(secretstore.mask(f"{kind}: {type(e).__name__}: {e}")) from None
    raise AdapterError(f"ismeretlen adapter: {kind}")
