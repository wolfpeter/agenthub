from datetime import datetime

from sqlalchemy.inspection import inspect


def iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def row(obj, exclude=()) -> dict:
    return {c.key: iso(getattr(obj, c.key)) for c in inspect(obj).mapper.column_attrs if c.key not in exclude}


def ticket(t, brief=False) -> dict:
    d = row(t, exclude=("result_payload",) if brief else ())
    if brief and d.get("description") and len(d["description"]) > 400:
        d["description"] = d["description"][:400] + "…"
    return d
