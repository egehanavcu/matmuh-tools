import json
from datetime import datetime

from ..api import CmsClient
from ..config import COLLECTIONS, REPORTS, ROOT, SCHEMAS, SNAPSHOTS
from .. import state


def run(dry_run: bool = False) -> None:
    client = CmsClient(write=True)
    client.probe_service_key()
    print("servis anahtarı: geçerli")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_dir = SNAPSHOTS / stamp
    for d in (SCHEMAS, snap_dir, REPORTS):
        d.mkdir(parents=True, exist_ok=True)

    rows = []
    for key in COLLECTIONS:
        schema = client.schema(key)
        (SCHEMAS / f"{key}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
        items = client.all_items(key)
        (snap_dir / f"{key}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        fields = len((schema.get("schema") or {}).get("fields") or [])
        rows.append((key, fields, len(items)))
        print(f"  {key:<18} {fields:>3} alan  {len(items):>4} kayıt")

    report = REPORTS / "0-prepare.md"
    lines = [
        "# Aşama 0 — Hazırlık",
        "",
        f"Zaman: {datetime.now().isoformat(timespec='seconds')}",
        f"Şemalar: `{SCHEMAS.relative_to(ROOT)}`, anlık görüntü: `{snap_dir.relative_to(ROOT)}`",
        "",
        "| Koleksiyon | Şema alanı | Canlıdaki kayıt |",
        "| ---------- | ---------- | --------------- |",
        *[f"| `{k}` | {f} | {n} |" for k, f, n in rows],
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    state.finish(0, report=str(report.relative_to(ROOT)), snapshot=str(snap_dir.relative_to(ROOT)))
    print(f"\nrapor: {report.relative_to(ROOT)}")
