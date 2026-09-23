import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CMS_BASE = os.environ.get("CMS_BASE", "https://matmuh.yusufacmaci.com/api")

SECRETS = ROOT / "secrets"
SCHEMAS = ROOT / "schemas"
SNAPSHOTS = ROOT / "snapshots"
RAW = ROOT / "raw"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
SOURCES = ROOT / "sources"
STATE_FILE = ROOT / "state.json"

COLLECTIONS = [
    "academic-terms",
    "staff",
    "lectures",
    "elective-groups",
    "lecture-offerings",
    "announcements",
    "news",
]
LOCALIZED = {"announcements", "news"}


class MissingSecret(RuntimeError):
    pass


def read_secret(name: str, env: str | None = None) -> str:
    path = SECRETS / name
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    if env and os.environ.get(env):
        return os.environ[env].strip()
    raise MissingSecret(f"{path.relative_to(ROOT)} bulunamadı ya da boş")
