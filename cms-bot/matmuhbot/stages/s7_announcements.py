import collections
import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from pydantic import BaseModel

from .. import llm, state
from ..api import Breaker, CmsClient, SystemicError
from ..config import DATA, RAW, REPORTS, ROOT, SOURCES
from ..html_clean import clean_html, text_of
from ..text import name_key

SITE = "https://mtm.yildiz.edu.tr"
BATCH = 10
TAGS = ["sinav", "mezuniyet", "staj", "kariyer", "genel"]
TAG_HELP = {
    "sinav": "sınav programı, vize, final, bütünleme, mazeret, ders kaydı, ders programı, kontenjan",
    "mezuniyet": "mezuniyet, mezuniyet sınavı, diploma, tören",
    "staj": "staj, staj başvurusu, staj defteri",
    "kariyer": "iş ilanı, kariyer, etkinlik, seminer, burs, yarışma, şirket sunumu",
    "genel": "yukarıdakilerin hiçbiri",
}

SYSTEM = f"""Yıldız Teknik Üniversitesi Matematik Mühendisliği Bölümü'nün duyurularını sınıflandırıyor ve özetliyorsun.
Her duyuru için:
- summary: duyurunun ne söylediğini anlatan tek cümlelik Türkçe özet, en çok 160 karakter. Başlığı tekrar etme, yeni bilgi ekleme, tarih ve derslik gibi ayrıntıları metinden aynen kullan. Metin zaten tek cümlelikse ve başlıktan fazlasını söylemiyorsa boş dizge döndür.
- tag: şu kimliklerden tam olarak biri: {", ".join(f"{k} ({v})" for k, v in TAG_HELP.items())}.
Yalnızca verilen metne dayan; tahmin etme."""


class ItemT(BaseModel):
    id: int
    summary: str
    tag: str


class BatchT(BaseModel):
    items: list[ItemT]


def config(kind: str) -> dict:
    if kind == "news":
        return {"key": "news", "raw": RAW / "8-news.json", "data": DATA / "8-news.json",
                "cache": DATA / "8-news.llm.json", "report": REPORTS / "8-news.md", "stage": 8, "dates": "news"}
    return {"key": "announcements", "raw": RAW / "7-announcements.json", "data": DATA / "7-announcements.json",
            "cache": DATA / "7-announcements.llm.json", "report": REPORTS / "7-announcements.md", "stage": 7,
            "dates": "announcements"}


def dates_for(kind: str) -> dict:
    file = SOURCES / "announcement-dates.json"
    payload = json.loads(file.read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["collections"][config(kind)["dates"]]["items"]}


def absolute(url: str) -> str:
    joined = urljoin(SITE + "/", (url or "").strip())
    scheme, _, rest = joined.partition("://")
    return f"{scheme}://{quote(rest, safe='/%:?=&')}"


OWN_HOSTS = {"mtm.yildiz.edu.tr", "www.mtm.yildiz.edu.tr"}


def is_media(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return parsed.netloc.lower() in OWN_HOSTS and "/media/" in path and bool(PurePosixPath(path).suffix)


def media_name(url: str) -> str:
    stem = PurePosixPath(unquote(urlparse(url).path)).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip() or "Ek"


def mime_of(url: str) -> str:
    return mimetypes.guess_type(unquote(urlparse(url).path))[0] or "application/octet-stream"


def scrape(dry_run: bool = False, kind: str = "announcement") -> None:
    from ..crawl import crawl
    from ..spiders.announcements import AnnouncementSpider

    cfg = config(kind)
    crawl(AnnouncementSpider, cfg["raw"], kind=kind)
    items = json.loads(cfg["raw"].read_text(encoding="utf-8"))
    print(f"çekildi: {len(items)} kayıt")
    print(f"  yazıldı: {cfg['raw'].relative_to(ROOT)}")


def prepare(item: dict, dates: dict) -> dict:
    body, links = clean_html(item["bodyHtml"])
    text = text_of(body)
    attachments, images = [], []
    for link in links:
        url = absolute(link)
        if is_media(url) and url not in [a["url"] for a in attachments]:
            attachments.append({"href": link, "url": url, "name": media_name(url), "mime": mime_of(url)})
    for src in item.get("images") or []:
        url = absolute(src)
        if is_media(url) and url not in images:
            images.append(url)

    date = dates.get(item["sourceId"]) or {}
    notes = []
    if not date:
        notes.append("tarih dosyasında yok")
    elif not date.get("exact"):
        notes.append(f"tahmini tarih ({date.get('source')})")
    if item["title"] != item["listTitle"]:
        notes.append(f"listede başlık «{item['listTitle']}»")
    if not text:
        notes.append("gövde boş")
    return {
        "sourceId": item["sourceId"],
        "sourceUrl": item["url"],
        "title": item["title"],
        "publishedAt": (date.get("publishedAt") or None),
        "dateSource": date.get("source"),
        "dateExact": bool(date.get("exact")),
        "body": body,
        "text": text,
        "quote": item.get("quote") or "",
        "attachments": attachments,
        "imageSources": images,
        "notes": notes,
    }


def llm_key(record: dict) -> str:
    payload = json.dumps([record["title"], record["text"][:4000]], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def classify(records: list[dict], cache_file, limit: int | None = None) -> dict:
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    todo = [r for r in records if llm_key(r) not in cache]
    if limit is not None:
        todo = todo[:limit]
    print(f"  özet ve kategori: {len(todo)} kayıt için Gemini çağrılacak ({len(records) - len(todo)} önbellekten)")
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        payload = [{"id": r["sourceId"], "title": r["title"], "text": r["text"][:4000]} for r in chunk]
        result, meta = llm.extract([json.dumps({"items": payload}, ensure_ascii=False)], BatchT, SYSTEM)
        by_id = {i.id: i for i in result.items}
        for record in chunk:
            got = by_id.get(record["sourceId"])
            if not got:
                continue
            tag = got.tag.strip().lower()
            cache[llm_key(record)] = {"summary": got.summary.strip()[:400], "tag": tag if tag in TAGS else "genel"}
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    {min(start + BATCH, len(todo))}/{len(todo)}  ({meta['model']}, {meta.get('usage')})")
    return cache


def normalize(dry_run: bool = False, kind: str = "announcement", limit: int | None = None) -> None:
    cfg = config(kind)
    items = json.loads(cfg["raw"].read_text(encoding="utf-8"))
    dates = dates_for(kind)
    records = [prepare(item, dates) for item in items]
    records.sort(key=lambda r: r["sourceId"])

    cache = classify(records, cfg["cache"], limit=limit)
    for record in records:
        got = cache.get(llm_key(record))
        record["summary"] = got["summary"] if got else ""
        record["tags"] = [got["tag"]] if got else []
        if not got:
            record["notes"].append("özet ve kategori yok")

    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "records": records,
    }
    cfg["data"].parent.mkdir(parents=True, exist_ok=True)
    cfg["data"].write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {}
    for record in records:
        for tag in record["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    print(f"  kayıt: {len(records)}, etiketler: {counts}")
    print(f"  ek dosyası: {sum(len(r['attachments']) for r in records)}, görsel: {sum(len(r['imageSources']) for r in records)}")
    print(f"  tarihsiz: {sum(1 for r in records if not r['publishedAt'])}, tahmini: {sum(1 for r in records if r['publishedAt'] and not r['dateExact'])}")
    print(f"  yazıldı: {cfg['data'].relative_to(ROOT)}")


def upload(client: CmsClient, url: str, cache: dict, cache_file) -> dict:
    if url in cache:
        return cache[url]
    res = requests.get(url, timeout=60, headers={"User-Agent": "matmuhbot/0.1"})
    res.raise_for_status()
    name = PurePosixPath(unquote(urlparse(url).path)).name or "dosya"
    content_type = res.headers.get("Content-Type", "").split(";")[0] or mime_of(url)
    cms_url = client.upload_media(res.content, name, content_type)
    cache[url] = {"url": cms_url, "mime": content_type, "size": len(res.content)}
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache[url]


def body_of(record: dict, media: dict) -> dict:
    body = record["body"]
    attachments = []
    for item in record["attachments"]:
        uploaded = media.get(item["url"])
        if not uploaded:
            continue
        body = body.replace(f'href="{item["href"]}"', f'href="{uploaded["url"]}"')
        attachments.append({"file": {"url": uploaded["url"], "name": item["name"],
                                     "mime": uploaded["mime"], "size": uploaded["size"]}})
    gallery = []
    for source in record["imageSources"]:
        uploaded = media.get(source)
        if uploaded:
            gallery.append({"image": {"src": uploaded["url"], "alt": record["title"]}})

    data = {"title": record["title"][:255], "body": body, "tags": record["tags"]}
    if record["summary"]:
        data["summary"] = record["summary"]
    if record["publishedAt"]:
        data["publishedAt"] = record["publishedAt"]
    if attachments:
        data["attachments"] = attachments
    if gallery:
        data["gallery"] = gallery
    return data


def recover_slugs(records: list[dict], live_items: list[dict], known: dict) -> tuple[dict, list[dict]]:
    """state.json'daki numara-slug eşlemesi eksikse canlıdaki kayıttan geri kurar."""
    taken = set(known.values())
    by_date, by_title = {}, {}
    for item in live_items:
        title = name_key(item["data"].get("title") or "")
        by_date.setdefault((title, (item["data"].get("publishedAt") or "")[:10]), []).append(item["slug"])
        by_title.setdefault(title, []).append(item["slug"])
    source_titles = collections.Counter(name_key(r["title"]) for r in records)

    recovered, ambiguous = {}, []
    for record in records:
        if known.get(str(record["sourceId"])):
            continue
        title = name_key(record["title"])
        if title not in by_title:
            continue
        hits = [s for s in by_date.get((title, (record["publishedAt"] or "")[:10]), []) if s not in taken]
        if len(hits) != 1 and source_titles[title] == 1 and len(by_title[title]) == 1:
            hits = [s for s in by_title[title] if s not in taken]
        if len(hits) == 1:
            known[str(record["sourceId"])] = hits[0]
            taken.add(hits[0])
            recovered[str(record["sourceId"])] = hits[0]
        else:
            ambiguous.append(record)
    return recovered, ambiguous


def push(dry_run: bool = False, kind: str = "announcement") -> None:
    cfg = config(kind)
    data = json.loads(cfg["data"].read_text(encoding="utf-8"))
    records = data["records"]
    client = CmsClient(write=True)
    client.probe_service_key()

    current = state.load()
    known = current["steps"].get(state.STAGES[cfg["stage"]][0], {}).get("slugs") or {}
    live_items = client.list_items(cfg["key"], "tr")
    live_slugs = {i["slug"] for i in live_items}
    recovered, ambiguous = recover_slugs(records, live_items, known)
    unsure = {r["sourceId"] for r in ambiguous}
    media_file = DATA / f"{cfg['stage']}-media.json"
    media = json.loads(media_file.read_text(encoding="utf-8")) if media_file.exists() else {}

    plan = []
    for record in records:
        slug = known.get(str(record["sourceId"]))
        if slug and slug in live_slugs:
            plan.append(("aynı", record))
        elif record["sourceId"] in unsure:
            plan.append(("belirsiz", record))
        else:
            plan.append(("oluştur", record))

    created = failed = 0
    errors, stopped = [], None
    breaker = Breaker()
    if not dry_run:
        try:
            for action, record in plan:
                if action != "oluştur":
                    continue
                try:
                    for item in record["attachments"] + [{"url": u} for u in record["imageSources"]]:
                        try:
                            upload(client, item["url"], media, media_file)
                        except Exception as error:
                            errors.append(f"{record['sourceId']} eki indirilemedi, bağlantı olduğu gibi bırakıldı: "
                                          f"{item['url']} — {error}")
                    result = client.request("POST", f"/cms/collections/{cfg['key']}?locale=tr", auth=True,
                                            json={"data": body_of(record, media)})
                    slug = (result or {}).get("slug")
                    if slug:
                        known[str(record["sourceId"])] = slug
                    created += 1
                    breaker.ok()
                except Exception as error:
                    failed += 1
                    errors.append(f"{record['sourceId']} «{record['title'][:60]}»: {error}")
                    breaker.record(error)
        except SystemicError as error:
            stopped = str(error)
        after = {i["slug"] for i in client.list_items(cfg["key"], "tr")}
        missing = [r["sourceId"] for _, r in plan if known.get(str(r["sourceId"])) not in after]
        if missing:
            errors.append(f"doğrulama: canlıda olmayan {len(missing)} kayıt")

    counts = {}
    for action, _ in plan:
        counts[action] = counts.get(action, 0) + 1
    tags = {}
    for record in records:
        for tag in record["tags"]:
            tags[tag] = tags.get(tag, 0) + 1

    lines = [
        f"# Aşama {cfg['stage']} — {'Haberler' if kind == 'news' else 'Duyurular'}{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        f"Kaynak: eski bölüm sitesi ({SITE}/{'haberler' if kind == 'news' else 'duyurular'}), {len(records)} kayıt.",
        f"Etiketler: {tags}. Ek dosyası: {sum(len(r['attachments']) for r in records)}, görsel: {sum(len(r['imageSources']) for r in records)}.",
        f"Tarihsiz: {sum(1 for r in records if not r['publishedAt'])}, tahmini tarihli: {sum(1 for r in records if r['publishedAt'] and not r['dateExact'])}.",
        "",
        "| | No | Tarih | Başlık | Etiket | Özet | Ek |",
        "| - | -- | ----- | ------ | ------ | ---- | -- |",
    ]
    for action, record in plan:
        date = record["publishedAt"] or "—"
        if record["publishedAt"] and not record["dateExact"]:
            date += " *"
        lines.append(f"| {action} | {record['sourceId']} | {date} | {record['title'][:70]} | {', '.join(record['tags'])} | "
                     f"{record['summary'][:90]} | {len(record['attachments']) or ''} |")
    if recovered:
        lines += ["", f"**Eşlemesi canlıdan geri kurulan kayıtlar** ({len(recovered)}): `state.json` bu "
                      "numaraları bilmiyordu, başlık ve tarihle canlıdaki kayda bağlandı, yeniden "
                      "oluşturulmadı.", ""]
        lines += [f"- {number} → `{slug}`" for number, slug in sorted(recovered.items(), key=lambda x: int(x[0]))[:60]]
    if ambiguous:
        lines += ["", f"**Belirsiz, oluşturulmadı** ({len(ambiguous)}): aynı başlık canlıda birden çok kayıtla "
                      "eşleşiyor, hangisi olduğu ayırt edilemedi. Gerçekten yeniyse elle ekleyin ya da "
                      "`state.json` eşlemesini düzeltin.", ""]
        lines += [f"- {r['sourceId']} {r['title'][:70]} ({r['publishedAt'] or 'tarihsiz'})" for r in ambiguous[:40]]
    flagged = [r for r in records if r["notes"]]
    if flagged:
        lines += ["", "**Notlar** (`*` tahmini tarih):", ""]
        lines += [f"- {r['sourceId']} {r['title'][:60]}: {'; '.join(r['notes'])}" for r in flagged]
    if stopped:
        lines += ["", f"**DURDU:** {stopped}"]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors[:50]]
    REPORTS.mkdir(parents=True, exist_ok=True)
    cfg["report"].write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  plan: {counts}, etiketler: {tags}")
    print(f"rapor: {cfg['report'].relative_to(ROOT)}")
    for e in errors[:5]:
        print(f"  HATA {e}")
    if stopped:
        print(f"  DURDU: {stopped}")
    if not dry_run:
        state.finish(cfg["stage"], created=created, skipped=counts.get("aynı", 0) + len(ambiguous),
                     failed=failed, report=str(cfg["report"].relative_to(ROOT)), slugs=known)


def run(dry_run: bool = False, kind: str = "announcement") -> None:
    scrape(kind=kind)
    normalize(kind=kind)
    push(dry_run=dry_run, kind=kind)
