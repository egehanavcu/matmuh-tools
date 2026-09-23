import hashlib
import json
import re
from collections import Counter
from datetime import datetime

from pydantic import BaseModel

from .. import llm, state
from ..api import Breaker, CmsClient, SystemicError
from ..config import DATA, REPORTS, ROOT

STAGE = 9
KEYS = ("announcements", "news")
DATA_FILE = DATA / "9-translations.json"
CACHE_FILE = DATA / "9-translations.cache.json"
REPORT_FILE = REPORTS / "9-translate.md"
BATCH = 5
STRUCTURAL = {"p", "br", "hr", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "pre", "code", "a"}
INLINE = {"strong", "b", "em", "i", "u", "s", "del", "strike"}

GLOSSARY = """Matematik Mühendisliği Bölümü = Department of Mathematical Engineering
Yıldız Teknik Üniversitesi = Yildiz Technical University; YTÜ = YTU
Fen-Edebiyat Fakültesi = Faculty of Arts and Sciences
Fen Bilimleri Enstitüsü = Graduate School of Natural and Applied Sciences
vize / ara sınav = midterm exam; final / yarıyıl sonu sınavı = final exam
bütünleme = make-up exam; mazeret sınavı = excuse exam; tek ders sınavı = single-course exam
mezuniyet sınavı = graduation exam; doktora yeterlik = PhD qualifying exam
ders kaydı = course registration; ekle-bırak = add-drop period; kontenjan = quota
danışman = academic advisor; staj = internship; bitirme çalışması = graduation project
yarıyıl / dönem = semester; güz = fall; bahar = spring; yaz okulu = summer school
AKTS = ECTS; lisansüstü = graduate; yüksek lisans = master's; doktora = PhD
çift ana dal = double major; yan dal = minor; yatay geçiş = lateral transfer
Öğrenci İşleri = Student Affairs; Bölüm Sekreterliği = Department Secretariat
öğretim üyesi = faculty member; öğretim elemanı = instructor"""

SYSTEM = f"""You translate official announcements of the Department of Mathematical Engineering at Yildiz Technical University from Turkish into English.

Rules:
- Translate faithfully into plain, formal university English. Do not summarise, add or drop information.
- "body" is HTML. Keep every tag and attribute exactly as given, in the same order and nesting; translate only the text between tags. Never add or remove tags. Keep href values byte-for-byte identical.
- Do NOT translate: URLs, e-mail addresses, phone numbers, course codes (MTM1011), classroom and office codes (KMB-202, A-219), person names, file names, form codes.
- Dates: "12.03.2026" stays "12.03.2026"; "12 Mart 2026" becomes "12 March 2026". Times: "14.00" becomes "14:00". Weekday names are translated.
- Use these fixed terms:
{GLOSSARY}
- Return an empty string for any field whose input is empty. Translate gallery entries in the given order and return the same number of entries."""


class GalleryT(BaseModel):
    alt: str
    caption: str


class RecordT(BaseModel):
    id: int
    title: str
    summary: str
    body: str
    coverAlt: str
    gallery: list[GalleryT]


class BatchT(BaseModel):
    records: list[RecordT]


def tag_sequence(html: str) -> list[str]:
    return [t.lower() for t in re.findall(r"<\s*(/?\w+)", html or "")]


def links_of(html: str) -> list[str]:
    return sorted(re.findall(r'href="([^"]+)"', html or ""))


def codes_of(text: str) -> set[str]:
    return set(re.findall(r"\b[A-ZİÖÜÇŞĞ]{2,4}\d{3,4}\b", text or ""))


def source_hash(data: dict) -> str:
    payload = json.dumps([data.get("title"), data.get("summary"), data.get("body"),
                          (data.get("coverImage") or {}).get("alt"),
                          [[(g.get("image") or {}).get("alt"), g.get("caption")] for g in data.get("gallery") or []]],
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def collect(client: CmsClient) -> list[dict]:
    rows = []
    for key in KEYS:
        english = {}
        for item in client.list_items(key, "en"):
            group = item.get("translationGroupId")
            if group:
                english[group] = item["slug"]
        for item in client.list_items(key, "tr"):
            data = item["data"]
            rows.append({
                "key": key,
                "slug": item["slug"],
                "translationGroupId": item.get("translationGroupId"),
                "englishSlug": english.get(item.get("translationGroupId")),
                "hash": source_hash(data),
                "data": data,
            })
    return rows


def payload_of(row: dict, index: int) -> dict:
    data = row["data"]
    return {
        "id": index,
        "title": data.get("title") or "",
        "summary": data.get("summary") or "",
        "body": data.get("body") or "",
        "coverAlt": (data.get("coverImage") or {}).get("alt") or "",
        "gallery": [{"alt": (g.get("image") or {}).get("alt") or "", "caption": g.get("caption") or ""}
                    for g in data.get("gallery") or []],
    }


def counts(html: str, wanted: set[str]) -> Counter:
    return Counter(t for t in tag_sequence(html) if t.lstrip("/") in wanted)


def notes_of(row: dict, got: dict) -> list[str]:
    source = counts(row["data"].get("body"), INLINE)
    target = counts(got.get("body"), INLINE)
    if source == target:
        return []
    diff = ", ".join(f"{tag}: {source.get(tag, 0)}→{target.get(tag, 0)}"
                     for tag in sorted(set(source) | set(target)) if not tag.startswith("/") and source.get(tag) != target.get(tag))
    return [f"vurgu etiketleri farklı ({diff})"]


def problems_of(row: dict, got: dict) -> list[str]:
    data = row["data"]
    problems = []
    if not got.get("title"):
        problems.append("başlık boş")
    if len(got.get("title", "")) > 255:
        problems.append("başlık 255 karakterden uzun")
    if counts(data.get("body"), STRUCTURAL) != counts(got.get("body"), STRUCTURAL):
        problems.append("HTML yapısı tutmuyor (paragraf, liste ya da bağlantı sayısı farklı)")
    source_len, target_len = len(data.get("body") or ""), len(got.get("body") or "")
    if source_len > 80 and not 0.5 <= target_len / source_len <= 2.0:
        problems.append(f"gövde uzunluğu şüpheli: {source_len} → {target_len} karakter")
    if links_of(data.get("body")) != links_of(got.get("body")):
        problems.append("bağlantılar değişmiş")
    missing = codes_of(f"{data.get('title')} {data.get('body')}") - codes_of(f"{got.get('title')} {got.get('body')}")
    if missing:
        problems.append(f"ders/derslik kodu kayıp: {', '.join(sorted(missing))}")
    if len(got.get("gallery") or []) != len(data.get("gallery") or []):
        problems.append("galeri sayısı tutmuyor")
    if (data.get("summary") or "") and not (got.get("summary") or ""):
        problems.append("özet boş döndü")
    return problems


def retry(row: dict):
    result, _ = llm.extract([json.dumps({"records": [payload_of(row, 0)]}, ensure_ascii=False)], BatchT, SYSTEM)
    got = next((r for r in result.records), None)
    if got:
        print(f"    yeniden çevrildi: {row['slug'][:48]}")
    return got


def translate(rows: list[dict], limit: int | None = None) -> dict:
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    todo = [r for r in rows if r["hash"] not in cache and not r["englishSlug"]]
    if limit is not None:
        todo = todo[:limit]
    print(f"  çeviri: {len(todo)} kayıt (önbellekte {sum(1 for r in rows if r['hash'] in cache)}, "
          f"İngilizcesi olan {sum(1 for r in rows if r['englishSlug'])})")
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        payload = [payload_of(row, index) for index, row in enumerate(chunk)]
        result, meta = llm.extract([json.dumps({"records": payload}, ensure_ascii=False)], BatchT, SYSTEM)
        by_index = {r.id: r for r in result.records}
        for index, row in enumerate(chunk):
            got = by_index.get(index)
            if got and problems_of(row, got.model_dump()):
                got = retry(row)
            if not got:
                continue
            cache[row["hash"]] = got.model_dump()
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    {min(start + BATCH, len(todo))}/{len(todo)}  ({meta['model']}, {meta.get('usage')})")
    return cache


def english_body(row: dict, got: dict) -> dict:
    data = row["data"]
    body = {
        "title": got["title"][:255],
        "body": got["body"],
        "tags": data.get("tags") or [],
    }
    if got.get("summary"):
        body["summary"] = got["summary"]
    if data.get("publishedAt"):
        body["publishedAt"] = data["publishedAt"]
    if data.get("featured"):
        body["featured"] = data["featured"]
    if data.get("attachments"):
        body["attachments"] = data["attachments"]
    if data.get("sourceUrl"):
        body["sourceUrl"] = data["sourceUrl"]
    if data.get("coverImage"):
        body["coverImage"] = {**data["coverImage"], "alt": got.get("coverAlt") or data["coverImage"].get("alt") or ""}
    gallery = []
    for index, item in enumerate(data.get("gallery") or []):
        translated = (got.get("gallery") or [])[index] if index < len(got.get("gallery") or []) else {}
        entry = {"image": {**(item.get("image") or {}), "alt": translated.get("alt") or (item.get("image") or {}).get("alt") or ""}}
        if translated.get("caption") or item.get("caption"):
            entry["caption"] = translated.get("caption") or item.get("caption")
        gallery.append(entry)
    if gallery:
        body["gallery"] = gallery
    return body


def normalize(dry_run: bool = False, limit: int | None = None) -> None:
    client = CmsClient(write=True)
    rows = collect(client)
    cache = translate(rows, limit=limit)

    prepared, blocked, waiting = [], [], []
    for row in rows:
        got = cache.get(row["hash"])
        if row["englishSlug"]:
            continue
        if not got:
            waiting.append(row)
            continue
        problems = problems_of(row, got)
        entry = {**{k: row[k] for k in ("key", "slug", "translationGroupId", "hash")},
                 "title": row["data"].get("title"), "english": got, "problems": problems,
                 "notes": notes_of(row, got), "body": english_body(row, got)}
        (blocked if problems else prepared).append(entry)

    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "prepared": prepared,
        "blocked": blocked,
        "waiting": [{"key": r["key"], "slug": r["slug"], "title": r["data"].get("title")} for r in waiting],
        "alreadyTranslated": [{"key": r["key"], "slug": r["slug"], "englishSlug": r["englishSlug"]}
                              for r in rows if r["englishSlug"]],
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  hazır: {len(prepared)}, sorunlu: {len(blocked)}, çevrilmeyi bekleyen: {len(waiting)}, "
          f"İngilizcesi zaten var: {len(payload['alreadyTranslated'])}")
    for entry in blocked[:10]:
        print(f"    SORUN {entry['slug']}: {'; '.join(entry['problems'])}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    prepared = data["prepared"]
    client = CmsClient(write=True)
    client.probe_service_key()

    created = failed = 0
    errors, stopped = [], None
    breaker = Breaker()
    if not dry_run:
        try:
            for entry in prepared:
                try:
                    client.request("POST", f"/cms/collections/{entry['key']}?locale=en&translationGroup={entry['translationGroupId']}",
                                   auth=True, json={"data": entry["body"]})
                    created += 1
                    breaker.ok()
                except Exception as error:
                    failed += 1
                    errors.append(f"{entry['slug']}: {error}")
                    breaker.record(error)
        except SystemicError as error:
            stopped = str(error)
        for key in KEYS:
            groups = {i.get("translationGroupId") for i in client.list_items(key, "en")}
            missing = [e["slug"] for e in prepared if e["key"] == key and e["translationGroupId"] not in groups]
            if missing:
                errors.append(f"doğrulama: {key} için canlıda olmayan {len(missing)} çeviri")

    lines = [
        f"# Aşama 9 — Duyuru ve haber çevirisi{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        f"Hazır: {len(prepared)}, sorunlu: {len(data['blocked'])}, bekleyen: {len(data['waiting'])}, "
        f"İngilizcesi zaten olan: {len(data['alreadyTranslated'])}.",
        "",
        "| Koleksiyon | Türkçe başlık | İngilizce başlık |",
        "| ---------- | ------------- | ---------------- |",
    ]
    for entry in prepared:
        lines.append(f"| {entry['key']} | {(entry['title'] or '')[:60]} | {entry['english']['title'][:60]} |")
    noted = [e for e in prepared if e.get("notes")]
    if noted:
        lines += ["", "**Notlar** (yazıldı, yalnızca biçimlendirme farkı):", ""]
        lines += [f"- {e['key']}/{e['slug']}: {'; '.join(e['notes'])}" for e in noted]
    if data["blocked"]:
        lines += ["", "**Yazılmadı (denetimden geçmedi):**", ""]
        lines += [f"- {e['key']}/{e['slug']}: {'; '.join(e['problems'])}" for e in data["blocked"]]
    if stopped:
        lines += ["", f"**DURDU:** {stopped}"]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors[:50]]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  plan: {len(prepared)} çeviri yazılacak, {len(data['blocked'])} sorunlu")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors[:5]:
        print(f"  HATA {e}")
    if stopped:
        print(f"  DURDU: {stopped}")
    if not dry_run:
        state.finish(STAGE, created=created, failed=failed, report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    normalize()
    push(dry_run=dry_run)


def scrape(dry_run: bool = False) -> None:
    print("Aşama 9 kaynağı canlıdaki Türkçe kayıtlar; ayrı çekme yok.")
