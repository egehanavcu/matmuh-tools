import hashlib
import json
import re
from datetime import datetime

from pydantic import BaseModel

from .. import llm, state
from ..api import Breaker, CmsClient, SystemicError
from ..config import DATA, RAW, REPORTS, ROOT
from ..merge import body_for_put, changes

STAGE = 3
KEY = "lectures"
RAW_FILE = RAW / "3-lectures.json"
DATA_FILE = DATA / "3-lectures.json"
CACHE_FILE = DATA / "3-lectures.translations.json"
REPORT_FILE = REPORTS / "3-lectures.md"
BATCH = 6

CATEGORY = {
    "temel meslek dersleri": "CORE_PROFESSION",
    "temel bilim dersleri": "BASIC_SCIENCE",
    "temel bilimler": "BASIC_SCIENCE",
    "yabancı dil dersleri": "FOREIGN_LANGUAGE",
    "yabancı dil": "FOREIGN_LANGUAGE",
    "ortak zorunlu dersler": "COMMON_REQUIRED",
    "ortak zorunlu": "COMMON_REQUIRED",
    "uzmanlık/alan dersleri": "SPECIALIZATION",
    "uzmanlık / alan dersleri": "SPECIALIZATION",
    "alan dersleri": "SPECIALIZATION",
    "sosyal bilimler": "GENERAL_CULTURE",
    "genel kültür dersleri": "GENERAL_CULTURE",
    "genel kültür": "GENERAL_CULTURE",
}
LEVEL = {"lisans": "UNDERGRADUATE", "yüksek lisans": "MASTERS", "doktora": "DOCTORATE"}
SEMESTER = {"güz": "FALL", "bahar": "SPRING", "yaz": "SUMMER"}
EMPTY = {"", "yok", "none", "-", "—"}
TURKISH_CHARS = set("çğıöşüÇĞİÖŞÜ")
TURKISH_WORDS = re.compile(r"\b(ve|ile|bir|için|olarak|bu|da|de|ders|dersin|konular|kitabı|öğrenci|yöntemleri|uygulamaları)\b", re.I)

SYSTEM = """You translate Turkish university course catalogue entries (Yildiz Technical University, Department of Mathematical Engineering) into English.
- Translate faithfully; do not add, drop or summarise information. Keep the formal academic register of a course catalogue.
- Keep mathematical notation, formulas, course codes, proper names, author names and years exactly.
- "resources" is a reading list: keep every bibliographic reference (authors, titles, publishers, years) exactly as written, even Turkish book titles; translate only Turkish labels and connecting words (e.g. "DERS KİTABI" -> "COURSE BOOK", "YARDIMCI KAYNAKLAR" -> "SUPPLEMENTARY READING"). Keep one reference per line as in the input.
- Weekly topics: translate each topic; "Ara Sınav" -> "Midterm Exam", "Final" stays "Final".
- Return an empty string for any field whose input is empty. Return topics for exactly the weeks given, same week numbers."""


class TopicT(BaseModel):
    week: int
    topic: str


class CourseT(BaseModel):
    code: str
    name: str
    about: str
    resources: str
    topics: list[TopicT]


class BatchT(BaseModel):
    courses: list[CourseT]


def clean(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value.casefold() in EMPTY else value


def looks_turkish(text: str) -> bool:
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ratio = sum(c in TURKISH_CHARS for c in letters) / len(letters)
    words = len(TURKISH_WORDS.findall(text))
    return ratio > 0.05 or words >= 2


def needs_translation(tr: str, en: str, *, citations: bool = False) -> bool:
    if not tr:
        return False
    if not en:
        return True
    if " ".join(tr.split()).casefold() == " ".join(en.split()).casefold():
        return not citations and looks_turkish(tr)
    return looks_turkish(en) and not citations


def number(value) -> float | int | None:
    try:
        n = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return int(n) if n.is_integer() else n


def scrape(dry_run: bool = False) -> None:
    from ..crawl import crawl
    from ..spiders.bologna import BolognaSpider

    crawl(BolognaSpider, RAW_FILE)
    items = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    programs = [i for i in items if i["kind"] == "program"]
    courses = [i for i in items if i["kind"] == "course"]
    print(f"çekildi: {len(programs)} program, {len(courses)} ders sayfası")
    print(f"  yazıldı: {RAW_FILE.relative_to(ROOT)}")


def catalogue(programs: list[dict]) -> tuple[dict, dict]:
    slots, placements = {}, {}
    for program in programs:
        for row in program["rows"]:
            code = row["cells"][0].strip().upper()
            section = row["section"] or ""
            m = re.match(r"(\d)\.\s*Yıl\s*-\s*(Güz|Bahar)", section)
            if program["program"] == "undergraduate" and m and not row["courseId"]:
                year, half = int(m.group(1)), m.group(2)
                slots[code] = (year - 1) * 2 + (1 if half == "Güz" else 2)
    rank = {"required": 0, "option": 1, "pool": 2, "masters-required": 3, "masters-elective": 4}
    for program in programs:
        for row in program["rows"]:
            if not row["courseId"]:
                continue
            code = row["cells"][0].strip().upper()
            section = row["section"] or ""
            place = {"courseId": row["courseId"], "url": row["href"], "section": section, "program": program["program"]}
            if program["program"] == "undergraduate":
                m = re.match(r"(\d)\.\s*Yıl\s*-\s*(Güz|Bahar)", section)
                g = re.match(r"Mesleki Seçmeli\s+(\d+)", section)
                if m:
                    place.update(kind="required", term=(int(m.group(1)) - 1) * 2 + (1 if m.group(2) == "Güz" else 2), type="REQUIRED")
                elif g:
                    slot = next((s for s in slots if re.fullmatch(rf"MES{g.group(1)}-\d[GB]", s)), None)
                    place.update(kind="option", term=slots.get(slot), type="ELECTIVE", group=slot)
                elif "Sosyal" in section:
                    place.update(kind="pool", term=None, type="ELECTIVE", group="USS")
                elif "Üniversite Mesleki" in section:
                    place.update(kind="pool", term=None, type="ELECTIVE", group="UMS")
                else:
                    place.update(kind="pool", term=None, type="ELECTIVE", group=section)
            else:
                elective = section.strip() == "Seçmeli Dersler"
                m = re.match(r"(\d)\.\s*Yıl\s*-\s*(Güz-Bahar|Güz|Bahar)", section)
                term = None
                semester = None
                if m and not elective:
                    if m.group(2) == "Güz-Bahar":
                        term = 3
                    else:
                        term = (int(m.group(1)) - 1) * 2 + (1 if m.group(2) == "Güz" else 2)
                        semester = "FALL" if m.group(2) == "Güz" else "SPRING"
                place.update(kind="masters-elective" if elective else "masters-required", term=term,
                             semester=semester, type="ELECTIVE" if elective else "REQUIRED")
            current = placements.get(code)
            if not current or rank[place["kind"]] < rank[current["kind"]]:
                placements[code] = place
    return placements, slots


def grading(rows: list[list[str]]) -> tuple[str, int | None, int | None, list[str]]:
    lines, problems = [], []
    final, total = None, 0
    for row in rows:
        if len(row) != 3:
            continue
        label, count, pct = row
        pct_n = number(pct)
        if not pct_n:
            continue
        total += pct_n
        lines.append(f"{label}: {count} × %{pct_n}" if number(count) else f"{label}: %{pct_n}")
        if label.strip().casefold() == "final":
            final = pct_n
    if total and total != 100:
        problems.append(f"değerlendirme toplamı %{total}")
        return "\n".join(lines), None, None, problems
    midterm = (100 - final) if final is not None else None
    return "\n".join(lines), midterm, final, problems


def lecture_from(code: str, place: dict, tr: dict, en: dict | None) -> dict:
    f, h = tr["fields"], tr["header"] or {}
    fe, he = (en or {}).get("fields", {}), (en or {}).get("header") or {}
    problems, notes = [], []

    languages = []
    lang_text = f.get("Dersin Dili", "")
    if "Türkçe" in lang_text:
        languages.append("TURKISH")
    if "İngilizce" in lang_text:
        languages.append("ENGLISH")
    other = [w.strip() for w in lang_text.split(",") if w.strip() and w.strip() not in ("Türkçe", "İngilizce", "Not set")]
    if other:
        notes.append(f"eğitim dili şemada yok: {', '.join(other)}")

    category_label = f.get("Ders Kategorisi", "").strip()
    category = CATEGORY.get(category_label.casefold())
    if category_label and not category:
        notes.append(f"kategori eşlenmedi: «{category_label}»")

    level = LEVEL.get(f.get("Dersin Seviyesi", "").strip().casefold())
    semester = SEMESTER.get(f.get("Yarıyıl", "").strip().casefold())
    term = place.get("term")
    if place["program"] == "masters":
        semester = place.get("semester")
    elif term:
        by_term = "FALL" if term % 2 else "SPRING"
        if semester and semester != by_term:
            notes.append(f"ders sayfasında yarıyıl {f.get('Yarıyıl')}, müfredatta {term}. yarıyıl; müfredat esas alındı")
        semester = by_term

    grading_tr, midterm, final, grading_problems = grading(tr["assessment"])
    grading_en, *_ = grading((en or {}).get("assessment") or [])
    notes += grading_problems

    weeks_en = {r[0]: clean(r[1]) for r in (en or {}).get("weeks", []) if len(r) >= 2}
    syllabus = []
    for row in tr["weeks"]:
        if len(row) < 2 or not number(row[0]):
            continue
        topic = clean(row[1])
        if not topic:
            continue
        syllabus.append({"week": int(number(row[0])), "topic": topic[:1000], "topicEn": weeks_en.get(row[0], "")[:1000]})

    name = h.get("Ders Adı") or ""
    lecture = {
        "code": code,
        "name": name,
        "nameEn": he.get("Title", ""),
        "languages": languages,
        "about": clean(f.get("Dersin İçeriği")),
        "aboutEn": clean(fe.get("Course Content")),
        "gradingPolicy": grading_tr,
        "gradingPolicyEn": grading_en,
        "resources": clean(f.get("Ders Kitabı / Malzemesi / Önerilen Kaynaklar")),
        "resourcesEn": clean(fe.get("Recommended Or Required Reading")),
        "degreeLevels": [level] if level else [],
        "type": place["type"],
        "category": category,
        "term": term,
        "semester": semester,
        "syllabus": syllabus,
        "midtermWeight": midterm,
        "finalWeight": final,
        "theoryHours": number(h.get("Ders (saat/hafta)")),
        "practiceHours": number(h.get("Uygulama (saat/hafta)")),
        "labHours": number(h.get("Laboratuar (saat/hafta)")),
        "localCredit": number(h.get("Yerel Kredi")),
        "ects": number(h.get("AKTS")),
        "bolognaLink": place["url"],
        "_place": place,
        "_objectives": clean(f.get("Dersin Amacı")),
        "_notes": notes,
        "_problems": problems,
    }
    hours = [lecture[k] for k in ("theoryHours", "practiceHours", "labHours")]
    if all(x is not None for x in hours):
        lecture["weeklyHours"] = sum(hours)
    if not name:
        problems.append("ders adı yok")
    if lecture["ects"] is None:
        problems.append("AKTS yok")
    if not en:
        notes.append("İngilizce sayfa çekilemedi")
    return lecture


def missing_english(lecture: dict) -> dict:
    need = {}
    if needs_translation(lecture["name"], lecture["nameEn"]):
        need["name"] = lecture["name"]
    if needs_translation(lecture["about"], lecture["aboutEn"]):
        need["about"] = lecture["about"]
    if needs_translation(lecture["resources"], lecture["resourcesEn"], citations=True):
        need["resources"] = lecture["resources"]
    topics = [{"week": s["week"], "topic": s["topic"]} for s in lecture["syllabus"] if needs_translation(s["topic"], s["topicEn"])]
    if topics:
        need["topics"] = topics
    return need


def source_hash(code: str, need: dict) -> str:
    return hashlib.sha256(json.dumps([code, need], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def translate(lectures: list[dict], limit: int | None = None) -> dict:
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    todo = []
    for lecture in lectures:
        need = missing_english(lecture)
        if not need:
            continue
        key = source_hash(lecture["code"], need)
        lecture["_translationKey"] = key
        if key not in cache:
            todo.append((lecture, need, key))
    if limit is not None:
        todo = todo[:limit]
    print(f"  çeviri: {len(todo)} ders çevrilecek ({sum(1 for l in lectures if l.get('_translationKey'))} derste eksik İngilizce var)")
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        payload = [
            {"code": l["code"], "name": n.get("name", ""), "about": n.get("about", ""), "resources": n.get("resources", ""),
             "topics": n.get("topics", [])}
            for l, n, _ in chunk
        ]
        result, meta = llm.extract([json.dumps({"courses": payload}, ensure_ascii=False)], BatchT, SYSTEM)
        by_code = {c.code.upper(): c for c in result.courses}
        for lecture, need, key in chunk:
            got = by_code.get(lecture["code"])
            if not got:
                continue
            weeks = {t["week"] for t in need.get("topics", [])}
            if {t.week for t in got.topics} != weeks:
                continue
            cache[key] = got.model_dump()
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    {min(start + BATCH, len(todo))}/{len(todo)}  ({meta['model']}, {meta.get('usage')})")
    return cache


def apply_translation(lecture: dict, cache: dict) -> None:
    key = lecture.get("_translationKey")
    if not key:
        return
    t = cache.get(key)
    lecture["_translated"] = []
    if not t:
        lecture["_notes"].append("İngilizce eksik, çeviri henüz yok")
        return
    for field, target in (("name", "nameEn"), ("about", "aboutEn"), ("resources", "resourcesEn")):
        if t.get(field):
            lecture[target] = t[field]
            lecture["_translated"].append(target)
    topics = {x["week"]: x["topic"] for x in t.get("topics", [])}
    for s in lecture["syllabus"]:
        if s["week"] in topics:
            s["topicEn"] = topics[s["week"]][:1000]
    if topics:
        lecture["_translated"].append("syllabus.topicEn")


def normalize(dry_run: bool = False, limit: int | None = None) -> None:
    items = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    programs = [i for i in items if i["kind"] == "program"]
    pages = {(i["courseId"], i["lang"]): i for i in items if i["kind"] == "course"}
    placements, slots = catalogue(programs)

    lectures, missing = [], []
    for code, place in sorted(placements.items()):
        tr = pages.get((place["courseId"], "tr"))
        if not tr or not tr.get("header"):
            missing.append(code)
            continue
        page_code = (tr["header"].get("Kodu") or "").strip().upper()
        lecture = lecture_from(code, place, tr, pages.get((place["courseId"], "en")))
        if page_code and page_code != code:
            lecture["_notes"].append(f"ders sayfasındaki kod {page_code}")
        lectures.append(lecture)

    cache = translate(lectures, limit=limit)
    for lecture in lectures:
        apply_translation(lecture, cache)

    required = [l for l in lectures if l["_place"]["kind"] == "required"]
    per_term = {}
    for l in required:
        per_term[l["term"]] = per_term.get(l["term"], 0) + (l["ects"] or 0)
    for slot, term in slots.items():
        slot_ects = next((number(r["cells"][7]) for p in programs for r in p["rows"] if r["cells"][0].strip().upper() == slot), 0)
        per_term[term] = per_term.get(term, 0) + (slot_ects or 0)

    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "lectures": lectures,
        "slots": slots,
        "ectsPerTerm": dict(sorted(per_term.items())),
        "missingPages": missing,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    kinds = {}
    for l in lectures:
        kinds[l["_place"]["kind"]] = kinds.get(l["_place"]["kind"], 0) + 1
    print(f"  dersler: {len(lectures)} {kinds}")
    print(f"  yarıyıl AKTS: {payload['ectsPerTerm']}  toplam {sum(per_term.values())}")
    untranslated = [l["code"] for l in lectures if l.get("_translationKey") and not l.get("_translated")]
    print(f"  İngilizcesi eksik kalan: {len(untranslated)}")
    if missing:
        print(f"  sayfası çekilemeyen: {missing}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


FIELDS = ["code", "name", "nameEn", "languages", "about", "aboutEn", "gradingPolicy", "gradingPolicyEn", "resources",
          "resourcesEn", "degreeLevels", "type", "category", "term", "semester", "syllabus", "midtermWeight",
          "finalWeight", "theoryHours", "practiceHours", "labHours", "weeklyHours", "localCredit", "ects", "bolognaLink"]
POOL_FIELDS = ["code", "name", "nameEn", "languages", "degreeLevels", "type", "theoryHours", "practiceHours",
               "labHours", "weeklyHours", "localCredit", "ects", "bolognaLink"]


def pool_details() -> bool:
    return bool(llm.settings().get("poolCourseDetails"))


def body_of(lecture: dict) -> dict:
    place = lecture["_place"]
    fields = POOL_FIELDS if place["kind"] == "pool" and not pool_details() else FIELDS
    body = {}
    for field in fields:
        value = lecture.get(field)
        if value is None or value == "" or value == []:
            continue
        body[field] = value
    levels = set(body.get("degreeLevels") or [])
    if place["program"] == "masters":
        levels.add("MASTERS")
    if levels:
        body["degreeLevels"] = sorted(levels)
    return body


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    lectures = [l for l in data["lectures"] if not l["_problems"]]
    blocked = [l for l in data["lectures"] if l["_problems"]]
    client = CmsClient(write=True)
    client.probe_service_key()
    live = {i["data"]["code"].upper(): i for i in client.list_items(KEY)}

    plan = []
    for lecture in lectures:
        current = live.get(lecture["code"])
        if not current:
            plan.append(("oluştur", lecture, None))
            continue
        body = body_of(lecture)
        lecture["_patch"] = changes(current["data"], body, tuple(body))
        changed = sorted(lecture["_patch"])
        plan.append((f"güncelle ({', '.join(changed)})" if changed else "aynı", lecture, current))
    source_codes = {l["code"] for l in data["lectures"]}
    updated = 0
    only_live = [i for c, i in live.items() if c not in source_codes]

    created = failed = 0
    errors, mismatched, stopped = [], [], None
    breaker = Breaker()
    if not dry_run:
        try:
            for action, lecture, current in plan:
                if action == "aynı":
                    continue
                try:
                    if current:
                        client.request("PUT", f"/cms/collections/{KEY}/{current['slug']}", auth=True,
                                       json={"data": body_for_put(KEY, current["data"], lecture["_patch"]),
                                             "version": current.get("version")})
                        updated += 1
                    else:
                        client.request("POST", f"/cms/collections/{KEY}", auth=True, json={"data": body_of(lecture)})
                        created += 1
                    breaker.ok()
                except Exception as error:
                    failed += 1
                    fields = ", ".join(sorted(lecture["_patch"])) if current else "tam kayıt"
                    errors.append(f"{lecture['code']} ({fields}): {error}")
                    breaker.record(error)
        except SystemicError as error:
            stopped = str(error)
        after = {i["data"]["code"].upper(): i for i in client.list_items(KEY)}
        missing = [l["code"] for _, l, _ in plan if l["code"] not in after]
        failed = len(missing)
        if missing:
            errors.append(f"doğrulama: canlıda olmayan {len(missing)} ders: {', '.join(missing[:20])}")
        for action, lecture, _ in plan:
            got = after.get(lecture["code"])
            if not got or not action.startswith("güncelle"):
                continue
            for field, value in lecture["_patch"].items():
                if got["data"].get(field) != value:
                    mismatched.append(f"{lecture['code']}.{field}: gönderilen {value}, canlıda {got['data'].get(field)}")

    kinds = {"required": "Lisans zorunlu", "option": "Mesleki seçmeli seçeneği", "pool": "Üniversite havuzu (USS/UMS)",
             "masters-required": "Yüksek lisans zorunlu", "masters-elective": "Yüksek lisans seçmeli"}
    lines = [
        f"# Aşama 3 — Dersler{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        "Kaynak: Bologna lisans (`program/view&id=222&aid=24`) ve yüksek lisans (`id=181&aid=86`) programları ve ders sayfaları (TR + EN).",
        "",
        f"Lisans yarıyıl AKTS: {', '.join(f'{t}. {e}' for t, e in data['ectsPerTerm'].items())} — toplam **{sum(data['ectsPerTerm'].values())}**",
        f"Havuz dersleri: {'tüm ayrıntılarıyla (kendi sayfaları olur)' if pool_details() else 'yalnızca temel alanlar + Bologna bağlantısı (sitede dışarı bağlanır)'}",
        "",
    ]
    for kind, label in kinds.items():
        group = [(a, l) for a, l, _ in plan if l["_place"]["kind"] == kind]
        if not group:
            continue
        lines += [f"## {label} ({len(group)})", "", "| | Kod | Ad | İngilizce ad | Yy | AKTS | T+U+L | Dil | Kategori | Vize/Final | Hafta | Çeviri |",
                  "| - | --- | -- | ------------ | -- | ---- | ----- | --- | -------- | ---------- | ----- | ------ |"]
        for action, l in sorted(group, key=lambda x: (x[1]["term"] or 0, x[1]["code"])):
            weights = f"{l['midtermWeight']}/{l['finalWeight']}" if l["finalWeight"] is not None else "—"
            lines.append(
                f"| {action} | {l['code']} | {l['name']} | {l['nameEn']} | {l['term'] or '—'} | {l['ects']} | "
                f"{l['theoryHours']}+{l['practiceHours']}+{l['labHours']} | {','.join({'TURKISH': 'TR', 'ENGLISH': 'EN'}[x] for x in l['languages']) or '—'} | "
                f"{l['category'] or '—'} | {weights} | {len(l['syllabus'])} | {', '.join(l.get('_translated') or []) or ''} |"
            )
        lines.append("")
    if mismatched:
        lines += ["## Canlıya farklı yazıldı", "",
                  "Yazma başarılı ama backend değeri değiştirmiş (ör. yuvarlama):", ""]
        lines += [f"- {m}" for m in mismatched[:50]] + [""]
    noted = [l for l in data["lectures"] if l["_notes"]]
    if noted:
        lines += ["## Notlar", ""] + [f"- {l['code']}: {'; '.join(l['_notes'])}" for l in noted] + [""]
    if blocked:
        lines += ["## Yazılmadı", ""] + [f"- {l['code']}: {'; '.join(l['_problems'])}" for l in blocked] + [""]
    if only_live:
        lines += ["## Canlıda olup kaynakta olmayan (silinmedi)", ""] + [f"- {i['data']['code']} {i['data'].get('name')}" for i in only_live] + [""]
    if stopped:
        lines += [f"**DURDU:** {stopped}", ""]
    if errors:
        lines += ["## Hatalar", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = {}
    for action, *_ in plan:
        label = action.split(" (")[0]
        counts[label] = counts.get(label, 0) + 1
    print(f"  plan: {counts}, yazılmayan: {len(blocked)}, canlıda fazla: {len(only_live)}")
    for action, lecture, _ in plan:
        if action.startswith("güncelle"):
            print(f"    {lecture['code']:<10} {action}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors[:10]:
        print(f"  HATA {e}")
    if stopped:
        print(f"  DURDU: {stopped}")
    if not dry_run:
        state.finish(STAGE, created=created, updated=updated, skipped=counts.get("aynı", 0), failed=failed,
                     report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    scrape()
    normalize()
    push(dry_run=dry_run)
