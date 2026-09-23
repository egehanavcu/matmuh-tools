import json
from functools import lru_cache

from .config import SCHEMAS

EMPTY = (None, "", [], {})
UNORDERED = {"groups", "degreeLevels", "tags", "languages"}


class MissingSchema(RuntimeError):
    pass


@lru_cache(maxsize=None)
def writable_fields(key: str) -> tuple[str, ...]:
    path = SCHEMAS / f"{key}.json"
    if not path.exists():
        raise MissingSchema(f"{path.name} yok; şemalar gitignore'lu, önce `bot.py run 0` çalıştırın")
    fields = (json.loads(path.read_text(encoding="utf-8")).get("schema") or {}).get("fields") or []
    return tuple(f["name"] for f in fields if not f.get("readOnly") and not f.get("computed"))


def changes(live: dict, source: dict, owned: tuple[str, ...]) -> dict:
    """Botun sahiplendiği alanlardan canlıdakinden farklı olanlar.

    Kaynakta değer yoksa canlıdaki değere dokunulmaz: elle eklenen bilgiyi
    boş bir kaynak silemesin.
    """
    out = {}
    for field in owned:
        value = source.get(field)
        if value in EMPTY:
            continue
        current = live.get(field)
        if field in UNORDERED and isinstance(value, list) and isinstance(current, list):
            if sorted(current) != sorted(value):
                out[field] = value
            continue
        if current != value:
            out[field] = value
    return out


def body_for_put(key: str, live: dict, updates: dict) -> dict:
    """Canlı kaydın yazılabilir alanlarının tamamı, üstüne değişiklikler.

    Sözleşme §1.4 PUT'un kaydın tamamını değiştirdiğini söylüyor; tamamını
    göndermek, birleştiren bir uçta da doğru sonucu veriyor.
    """
    body = {f: live[f] for f in writable_fields(key) if f in live and live[f] not in EMPTY}
    body.update(updates)
    return body
