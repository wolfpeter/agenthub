"""Titkok: `<secrets>/sources.env` (KULCS=érték soronként). Titok nem kerül Gitbe, logba, ticketbe."""
import os
from pathlib import Path


def secrets_dir() -> Path:
    return Path(os.environ.get("AGENTHUB_SECRETS_DIR") or Path(os.environ.get("AGENTHUB_HOME", Path.home() / "AgentHub")) / "secrets")


def load() -> dict[str, str]:
    out = {}
    f = secrets_dir() / "sources.env"
    if f.is_file():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_issued: set[str] = set()  # environmentből kiadott titkok, hogy a mask() is ismerje őket


def get(name: str) -> str:
    v = load().get(name) or os.environ.get(name)
    if not v:
        raise KeyError(f"hiányzó titok: {name} (add meg a secrets/sources.env fájlban)")
    _issued.add(v)
    return v


def mask(text: str) -> str:
    """Ismert titkok kitakarása szövegből (logok, hibaüzenetek)."""
    for v in sorted({*load().values(), *_issued}, key=len, reverse=True):
        if v and len(v) >= 6:
            text = text.replace(v, "***")
    return text
