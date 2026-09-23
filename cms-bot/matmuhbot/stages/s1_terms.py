import json
import re
from datetime import date, datetime

from pydantic import BaseModel

from .. import inbox, llm, state
from ..api import CmsClient
from ..config import DATA, RAW, REPORTS, ROOT

STAGE = 1
KEY = "academic-terms"
RAW_FILE = RAW / "1-academic-terms.json"
DATA_FILE = DATA / "1-academic-terms.json"
REPORT_FILE = REPORTS / "1-academic-terms.md"
SEMESTERS = ("FALL", "SPRING", "SUMMER")
START_MONTHS = {"FALL": (8, 9, 10), "SPRING": (1, 2, 3), "SUMMER": (6, 7, 8)}
LENGTH_DAYS = {"FALL": (60, 150), "SPRING": (60, 150), "SUMMER": (20, 80)}

SYSTEM = """Sen Yıldız Teknik Üniversitesi (YTÜ) akademik takviminden veri çıkaran bir yardımcısın.
Girdi, takvim Excel dosyasının görünür sayfalarının düz metnidir; her satır hücreleri " | " ile ayrılmış hâlidir.
Tarihler ya YYYY-MM-DD ya da "28 Aralık 2026-07 Ocak 2027" gibi Türkçe metindir.

Görevin:
1. Belgenin kapsadığı eğitim-öğretim yılını bul (ör. "2026-2027").
2. O yılın her yarıyılı için lisans derslerinin BAŞLANGIÇ günü ile SON GÜNÜNÜ çıkar:
   - Güz = FALL, Bahar = SPRING, Yaz Okulu = SUMMER.
   - Başlangıç: "... YARIYILI DERSLERİNİN BAŞLANGICI" satırı. Yaz için "YAZ OKULU BAŞLANGICI".
   - Bitiş: "... YARIYILI DERSLERİNİN SON GÜNÜ" satırı. Yaz için "YAZ OKULU DERSLERİNİN SON GÜNÜ".
   - Final, bütünleme, kayıt, YDYO/hazırlık tarihlerini bitiş olarak KULLANMA.
   - Bir sonraki eğitim-öğretim yılına ait önizleme satırlarını (ör. "2027-2028 Güz Yarıyılı Başlangıcı") alma.
   - Belgede başlangıcı ve son günü açıkça yazmayan yarıyılı listeye koyma; tahmin etme.
3. Her tarih için kanıt olarak belgedeki satırı olduğu gibi kopyala.
4. Resmi tatilleri (tarih ve ad) listele; birden çok güne yayılan tatilin her gününü ayrı yaz.

Yalnızca belgede yazanı kullan. Tarihleri YYYY-MM-DD biçiminde ver."""


class Term(BaseModel):
    academicYear: str
    semester: str
    startDate: str
    startEvidence: str
    endDate: str
    endEvidence: str


class Holiday(BaseModel):
    date: str
    name: str


class CalendarExtraction(BaseModel):
    documentAcademicYear: str
    terms: list[Term]
    holidays: list[Holiday]
    notes: str


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def scrape(dry_run: bool = False) -> None:
    source = inbox.pick("academic-calendar")
    RAW.mkdir(parents=True, exist_ok=True)
    year = re.search(r"(\d{4}-\d{4})\s+EĞİTİM-ÖĞRETİM YILI\s+AKADEMİK TAKVİMİ", source.text, re.I)
    payload = {
        "file": source.name,
        "sha256": source.sha256,
        "scrapedAt": datetime.now().isoformat(timespec="seconds"),
        "documentAcademicYear": year.group(1) if year else None,
        "text": source.text,
    }
    RAW_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"kaynak: temp/{source.name}")
    print(f"  görünür sayfaların metni: {len(source.text.splitlines())} satır, belge yılı: {payload['documentAcademicYear']}")
    print(f"  yazıldı: {RAW_FILE.relative_to(ROOT)}")


def validate(term: Term, raw_text: str, doc_year: str | None) -> list[str]:
    problems = []
    m = re.fullmatch(r"(\d{4})-(\d{4})", term.academicYear)
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        return [f"akademik yıl biçimi hatalı: {term.academicYear}"]
    if doc_year and term.academicYear != doc_year:
        problems.append(f"belgenin yılı {doc_year}, bu {term.academicYear}")
    if term.semester not in SEMESTERS:
        return problems + [f"yarıyıl hatalı: {term.semester}"]
    try:
        start, end = date.fromisoformat(term.startDate), date.fromisoformat(term.endDate)
    except ValueError:
        return problems + ["tarih biçimi hatalı"]
    y1, y2 = int(m.group(1)), int(m.group(2))
    expected_year = y1 if term.semester == "FALL" else y2
    if start.year != expected_year or start.month not in START_MONTHS[term.semester]:
        problems.append(f"başlangıç {start} bu yarıyıl için beklenmedik")
    lo, hi = LENGTH_DAYS[term.semester]
    if not lo <= (end - start).days <= hi:
        problems.append(f"süre {(end - start).days} gün, beklenen {lo}–{hi}")
    haystack = _norm(raw_text)
    for label, iso, evidence in (("başlangıç", term.startDate, term.startEvidence), ("bitiş", term.endDate, term.endEvidence)):
        if iso not in raw_text and _norm(evidence) not in haystack:
            problems.append(f"{label} ({iso}) belgede bulunamadı")
    return problems


def normalize(dry_run: bool = False) -> None:
    raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    result, meta = llm.extract([raw["text"]], CalendarExtraction, SYSTEM)
    doc_year = raw.get("documentAcademicYear") or result.documentAcademicYear

    terms, rejected = [], []
    for term in result.terms:
        problems = validate(term, raw["text"], doc_year)
        (rejected if problems else terms).append({**term.model_dump(), "problems": problems})

    DATA.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": {"file": raw["file"], "sha256": raw["sha256"]},
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "llm": meta,
        "documentAcademicYear": doc_year,
        "terms": terms,
        "rejected": rejected,
        "holidays": [h.model_dump() for h in result.holidays],
        "notes": result.notes,
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"model: {meta['model']}  token: {meta.get('usage')}")
    for t in terms:
        print(f"  {t['academicYear']} {t['semester']:<6} {t['startDate']} → {t['endDate']}")
    for t in rejected:
        print(f"  REDDEDİLDİ {t['academicYear']} {t['semester']}: {'; '.join(t['problems'])}")
    print(f"  resmi tatil: {len(payload['holidays'])}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    if data["rejected"]:
        print("Uyarı: reddedilen dönemler var, yazılmayacak (rapora bakın).")
    client = CmsClient(write=True)
    client.probe_service_key()
    live = {f"{i['data']['academicYear']}|{i['data']['semester']}": i for i in client.list_items(KEY)}

    plan = []
    for term in data["terms"]:
        body = {k: term[k] for k in ("academicYear", "semester", "startDate", "endDate")}
        current = live.get(f"{term['academicYear']}|{term['semester']}")
        if not current:
            plan.append(("oluştur", body, None))
        elif (current["data"]["startDate"], current["data"]["endDate"]) != (body["startDate"], body["endDate"]):
            plan.append(("güncelle", body, current))
        else:
            plan.append(("aynı", body, current))
    source_keys = {f"{t['academicYear']}|{t['semester']}" for t in data["terms"]}
    untouched = [i for k, i in live.items() if k not in source_keys]

    after = {f"{b['academicYear']}|{b['semester']}": b for _, b, _ in plan}
    for key, item in live.items():
        after.setdefault(key, item["data"])
    today = date.today().isoformat()
    covering = [f"{v['academicYear']} {v['semester']}" for v in after.values() if v["startDate"] <= today <= v["endDate"]]

    created = updated = failed = 0
    errors = []
    if not dry_run:
        for action, body, current in plan:
            if action == "aynı":
                continue
            try:
                if action == "oluştur":
                    client.request("POST", f"/cms/collections/{KEY}", auth=True, json={"data": body})
                    created += 1
                else:
                    payload = {"data": body}
                    if current.get("version") is not None:
                        payload["version"] = current["version"]
                    client.request("PUT", f"/cms/collections/{KEY}/{current['slug']}", auth=True, json=payload)
                    updated += 1
            except Exception as error:
                failed += 1
                errors.append(f"{body['academicYear']} {body['semester']}: {error}")
        verify = {f"{i['data']['academicYear']}|{i['data']['semester']}": i["data"] for i in client.list_items(KEY)}
        for _, body, _ in plan:
            got = verify.get(f"{body['academicYear']}|{body['semester']}")
            if not got or (got["startDate"], got["endDate"]) != (body["startDate"], body["endDate"]):
                errors.append(f"doğrulama: {body['academicYear']} {body['semester']} canlıda beklenen gibi değil")
                failed += 1

    lines = [
        f"# Aşama 1 — Akademik dönemler{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        f"Kaynak: `temp/{data['source']['file']}` (belge yılı {data['documentAcademicYear']}), model `{data['llm']['model']}`",
        "",
        "| Dönem | Canlıdaki | Kaynaktaki | İşlem |",
        "| ----- | --------- | ---------- | ----- |",
    ]
    for action, body, current in plan:
        old = f"{current['data']['startDate']} → {current['data']['endDate']}" if current else "—"
        lines.append(f"| {body['academicYear']} {body['semester']} | {old} | {body['startDate']} → {body['endDate']} | {action} |")
    for item in untouched:
        d = item["data"]
        lines.append(f"| {d['academicYear']} {d['semester']} | {d['startDate']} → {d['endDate']} | kaynakta yok | dokunulmadı |")
    lines += ["", "**Kanıtlar:**", ""]
    for term in data["terms"]:
        lines.append(f"- {term['academicYear']} {term['semester']}: başlangıç «{term['startEvidence'][:140]}», bitiş «{term['endEvidence'][:140]}»")
    if data["rejected"]:
        lines += ["", "**Reddedilen:**", ""]
        lines += [f"- {t['academicYear']} {t['semester']}: {'; '.join(t['problems'])}" for t in data["rejected"]]
    lines += ["", f"**Bugün ({today}) kapsayan dönem:** {', '.join(covering) or 'YOK — ders programı sayfaları bu aralıkta boş görünür'}"]
    lines += [
        "",
        f"**Resmi tatiller ({len(data['holidays'])}):** `{DATA_FILE.relative_to(ROOT)}` içinde. Takvim etkinliği admin yetkisi",
        "istediği için bot yazmıyor.",
    ]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for action, body, _ in plan:
        print(f"  {action:<9} {body['academicYear']} {body['semester']:<6} {body['startDate']} → {body['endDate']}")
    for item in untouched:
        print(f"  dokunulmadı {item['data']['academicYear']} {item['data']['semester']}")
    print(f"  bugünü kapsayan: {', '.join(covering) or 'YOK'}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    if not dry_run:
        state.finish(STAGE, created=created, updated=updated, skipped=sum(1 for a, *_ in plan if a == "aynı"),
                     failed=failed, report=str(REPORT_FILE.relative_to(ROOT)), source=data["source"])
        for e in errors:
            print(f"  HATA {e}")


def run(dry_run: bool = False) -> None:
    scrape()
    normalize()
    push(dry_run=dry_run)
