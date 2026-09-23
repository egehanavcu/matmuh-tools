import json
import re
from datetime import datetime

from pydantic import BaseModel

from .. import llm, state
from ..api import CmsClient
from ..config import DATA, RAW, REPORTS, ROOT, SOURCES
from ..text import ascii_fold, name_key

STAGE = 5
RAW_FILE = RAW / "5-offerings.json"
DATA_FILE = DATA / "5-offerings.json"
REPORT_FILE = REPORTS / "5-offerings.md"
MAP_FILE = SOURCES / "instructor-map.json"
INBOX = ROOT / "temp"
IMPORT_BATCH = 50

DAYS = {"Pazartesi": "MONDAY", "Salı": "TUESDAY", "Çarşamba": "WEDNESDAY", "Perşembe": "THURSDAY",
        "Cuma": "FRIDAY", "Cumartesi": "SATURDAY", "Pazar": "SUNDAY"}
SEMESTERS = {"güz": "FALL", "bahar": "SPRING", "yaz": "SUMMER"}
FACULTY_PREFIXES = {"KMF", "YDYO", "FEF", "EEF", "MF", "İİBF", "SAN", "BED", "MİM", "İTB"}
ONLINE_ROOM = re.compile(r"UZEM|SNL|ONL[İI]NE", re.I)
OWN_DEPARTMENT = "Matematik Mühendisliği"
OWN_PROGRAM = "Matematik Mühendisliği"
PLACEHOLDER_ROOMS = {"d diger", "diger", "d", "-", "?"}
UNKNOWN_INSTRUCTOR = "Belirtilmemiş"
TITLE_PREFIX = re.compile(
    r"^(Prof\.?|Doç\.?|Dr\.?|Öğr\.?|Arş\.?|Araş\.?|Öğretim|Görevlisi|Gör\.?|Üyesi|Tanımsız|\(657/89\.Madd)[\.\s]*",
    re.I)


class MatchT(BaseModel):
    raw: str
    slug: str
    note: str


class MatchBatchT(BaseModel):
    matches: list[MatchT]


SYSTEM = """Yıldız Teknik Üniversitesi Matematik Mühendisliği Bölümü'nün ders programındaki eğitmen adlarını, bölümün personel listesiyle eşleştiriyorsun.
- Her ham ad için listeden tam olarak bir kişinin slug'ını döndür.
- Emin değilsen ya da kişi listede yoksa slug yerine boş dizge döndür. Tahmin etme, uydurma.
- Ham adlarda unvan, büyük harf ve eksik soyad olabilir: "Prof.Dr. Ülkü YEŞİL" listede "Ülkü BABUŞCU YEŞİL" olabilir (evlilik soyadı). Ad ve soyadın bir kısmı tutuyorsa ve başka aday yoksa eşleştir.
- note alanına kararının tek cümlelik gerekçesini yaz."""


def clean_name(raw: str) -> str:
    text = " ".join((raw or "").replace("\xa0", " ").split())
    while True:
        stripped = TITLE_PREFIX.sub("", text).strip()
        if stripped == text:
            return text
        text = stripped


def classroom_of(name: str) -> tuple[str | None, bool]:
    text = " ".join((name or "").split())
    if not text:
        return None, False
    if ONLINE_ROOM.search(text):
        return None, True
    if ascii_fold(text) in PLACEHOLDER_ROOMS:
        return None, False
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in FACULTY_PREFIXES:
        text = parts[1]
    return text, False


def term_of(name: str) -> tuple[str | None, str | None]:
    m = re.match(r"(\d{4})-(\d{4})\s+(\w+)", name or "")
    if not m:
        return None, None
    return f"{m.group(1)}-{m.group(2)}", SEMESTERS.get(m.group(3).casefold())


def hour(value: str) -> int | None:
    m = re.match(r"(\d{1,2}):(\d{2})", value or "")
    return int(m.group(1)) if m else None


def scrape(dry_run: bool = False) -> None:
    files = []
    for path in sorted(INBOX.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "classLevels" not in payload:
            continue
        program = (payload.get("program") or {}).get("name") or payload.get("departmentName") or ""
        files.append({
            "file": path.name,
            "program": program,
            "language": "ENGLISH" if "İngilizce" in program else "TURKISH",
            "academicTerm": payload.get("academicTerm") or {},
            "payload": payload,
        })
    if not files:
        raise FileNotFoundError("temp/ içinde ders programı JSON'u yok (classLevels alanı aranıyor)")
    RAW.mkdir(parents=True, exist_ok=True)
    RAW_FILE.write_text(json.dumps({"scrapedAt": datetime.now().isoformat(timespec="seconds"), "files": files},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    for f in files:
        lessons = sum(len(d["lessons"]) for cl in f["payload"]["classLevels"] for d in cl["days"])
        print(f"  {f['file']:<40} {f['program']:<40} {f['language']:<8} {lessons} ders saati")
    print(f"  yazıldı: {RAW_FILE.relative_to(ROOT)}")


def lessons_of(entry: dict) -> list[dict]:
    out = []
    for level in entry["payload"]["classLevels"]:
        for day in level["days"]:
            for lesson in day["lessons"]:
                room, online = classroom_of((lesson.get("classroom") or {}).get("name"))
                out.append({
                    "code": (lesson.get("courseCode") or "").strip().upper(),
                    "name": (lesson.get("courseName") or "").strip(),
                    "group": lesson.get("groupNumber"),
                    "day": DAYS.get(day["day"]),
                    "start": lesson.get("startTime"),
                    "end": lesson.get("endTime"),
                    "classroom": room,
                    "online": online,
                    "instructors": [i for i in (lesson.get("instructors") or []) if i.strip()],
                    "department": (lesson.get("courseScope") or {}).get("department"),
                    "scopeProgram": (lesson.get("courseScope") or {}).get("program"),
                    "language": entry["language"],
                })
    return out


def merge_slots(rows: list[dict]) -> list[dict]:
    slots = []
    keyed = {}
    by_time = {}
    for row in rows:
        by_time.setdefault((row["day"], row["start"], row["end"]), []).append(row)
    for candidates in by_time.values():
        best = next((r for r in candidates if r["classroom"]), None)             or next((r for r in candidates if r["online"]), None) or candidates[0]
        key = (best["day"], best["classroom"], best["online"])
        keyed.setdefault(key, []).append(best)
    for (day, classroom, online), items in keyed.items():
        items.sort(key=lambda r: r["start"])
        current = None
        for item in items:
            if current and hour(item["start"]) is not None and hour(current["endTime"]) is not None \
                    and hour(item["start"]) == hour(current["endTime"]) + 1:
                current["endTime"] = item["end"]
                continue
            current = {"dayOfWeek": day, "startTime": item["start"], "endTime": item["end"], "online": online}
            if classroom and not online:
                current["classroom"] = classroom
            slots.append(current)
    return sorted(slots, key=lambda s: (list(DAYS.values()).index(s["dayOfWeek"]), s["startTime"]))


def load_map() -> dict:
    return json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}


def code_match(raw: str, staff: list[dict]) -> tuple[str | None, str]:
    words = set(w for w in name_key(clean_name(raw)).split() if len(w) > 1)
    if not words:
        return None, "ad okunamadı"
    exact, partial = [], []
    for person in staff:
        last = set(w for w in name_key(person.get("lastName", "")).split() if len(w) > 1)
        first = set(w for w in name_key(person.get("firstName", "")).split() if len(w) > 1)
        if not last or not first & words:
            continue
        if last <= words:
            exact.append(person["slug"])
        elif last & words:
            partial.append(person["slug"])
    if len(exact) == 1:
        return exact[0], "ad ve soyad tuttu"
    if not exact and len(partial) == 1:
        return partial[0], "soyadın bir kısmı tuttu"
    if len(exact) > 1 or len(partial) > 1:
        return None, "birden fazla aday"
    return None, "eşleşme yok"


def resolve_instructors(names: list[str], staff: list[dict]) -> dict:
    mapping = load_map()
    roster = [{"slug": p["slug"], "name": f"{p.get('academicTitle') or ''} {p.get('firstName')} {p.get('lastName')}".strip(),
               "groups": p.get("groups")} for p in staff]
    slugs = {p["slug"] for p in staff}
    unresolved = []
    for raw in names:
        entry = mapping.get(raw)
        if entry and entry.get("source") == "elle":
            continue
        slug, note = code_match(raw, staff)
        if slug:
            mapping[raw] = {"slug": slug, "source": "kod", "note": note}
        elif entry and entry.get("source") == "ai":
            continue
        else:
            mapping[raw] = {"slug": None, "source": "kod", "note": note}
            unresolved.append(raw)

    if unresolved:
        print(f"  eğitmen: {len(unresolved)} ad kodla çözülemedi, Gemini'ye soruluyor")
        for start in range(0, len(unresolved), 20):
            chunk = unresolved[start:start + 20]
            payload = {"rawNames": chunk, "staff": roster}
            result, meta = llm.extract([json.dumps(payload, ensure_ascii=False)], MatchBatchT, SYSTEM)
            for match in result.matches:
                if match.raw not in chunk:
                    continue
                slug = match.slug.strip()
                mapping[match.raw] = {"slug": slug if slug in slugs else None, "source": "ai",
                                      "note": match.note.strip()[:200]}
            print(f"    {min(start + 20, len(unresolved))}/{len(unresolved)}  ({meta['model']}, {meta.get('usage')})")

    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAP_FILE.write_text(json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    return mapping


def normalize(dry_run: bool = False) -> None:
    raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    client = CmsClient()
    staff = [i["data"] for i in client.list_items("staff")]
    teachable = [p for p in staff if set(p.get("groups") or []) - {"ADMINISTRATIVE"}]
    lectures = {i["data"]["code"].upper(): i["data"] for i in client.list_items("lectures")}

    years, semesters, notes = set(), set(), []
    rows_by_offering = {}
    for entry in raw["files"]:
        year, semester = term_of((entry.get("academicTerm") or {}).get("name", ""))
        years.add(year)
        semesters.add(semester)
        for lesson in lessons_of(entry):
            if not lesson["code"] or lesson["group"] is None or not lesson["day"]:
                notes.append(f"eksik alanlı ders saati atlandı: {lesson}")
                continue
            rows_by_offering.setdefault((lesson["code"], lesson["group"]), []).append(lesson)

    if len(years) != 1 or len(semesters) != 1 or None in years | semesters:
        raise RuntimeError(f"dosyalar aynı döneme ait değil: {years} {semesters}")
    year, semester = years.pop(), semesters.pop()

    names = sorted({i for rows in rows_by_offering.values() for r in rows for i in r["instructors"]})
    mapping = resolve_instructors(names, teachable)

    offerings, missing_lectures = [], {}
    for (code, group), rows in sorted(rows_by_offering.items()):
        languages = {r["language"] for r in rows}
        lecture = lectures.get(code)
        own = (lecture or {}).get("languages") or []
        scopes = {r["scopeProgram"] for r in rows if r["scopeProgram"]}
        if any("(İngilizce)" in scope for scope in scopes):
            language = "ENGLISH"
        elif len(own) == 1:
            language = own[0]
        elif scopes == {OWN_PROGRAM}:
            language = "TURKISH"
        elif len(languages) == 1:
            language = languages.pop()
        else:
            language = None
            notes.append(f"{code} Gr.{group}: şubenin programı dili söylemiyor ({', '.join(sorted(scopes)) or 'kapsam yok'}), "
                         f"ders çok dilli ve iki dosyada da var; dil yazılmadı")

        instructors = []
        for name in dict.fromkeys(i for r in rows for i in r["instructors"]):
            hit = mapping.get(name) or {}
            instructors.append({"raw": name, "slug": hit.get("slug"), "source": hit.get("source"), "note": hit.get("note")})

        if not lecture:
            missing_lectures[code] = rows[0]["name"] or code

        offerings.append({
            "lectureCode": code,
            "groupNumber": group,
            "academicYear": year,
            "semester": semester,
            "language": language,
            "instructors": instructors,
            "department": rows[0]["department"],
            "lectureName": rows[0]["name"],
            "slots": merge_slots(rows),
        })

    clashes = {}
    for offering in offerings:
        for slot in offering["slots"]:
            if slot.get("online") or not slot.get("classroom"):
                continue
            for h in range(hour(slot["startTime"]), hour(slot["endTime"]) + 1):
                clashes.setdefault((slot["dayOfWeek"], h, slot["classroom"]), set()).add(
                    (offering["lectureCode"], offering["groupNumber"]))
    conflicts = sorted({k[2]: sorted(v) for k, v in clashes.items() if len(v) > 1}.items())

    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "academicYear": year,
        "semester": semester,
        "sources": [{"file": f["file"], "program": f["program"], "language": f["language"]} for f in raw["files"]],
        "offerings": offerings,
        "missingLectures": missing_lectures,
        "classroomConflicts": [{"classroom": room, "offerings": [f"{c} Gr.{g}" for c, g in pairs]} for room, pairs in conflicts],
        "notes": notes,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    mtm = [o for o in offerings if o["department"] == OWN_DEPARTMENT]
    matched = sum(1 for o in mtm for i in o["instructors"] if i["slug"])
    total = sum(len(o["instructors"]) for o in mtm)
    print(f"  dönem: {year} {semester}")
    print(f"  açılan ders: {len(offerings)} (bölüm dersi {len(mtm)}), ders saati: {sum(len(o['slots']) for o in offerings)}")
    print(f"  bölüm derslerinde eğitmen eşleşmesi: {matched}/{total}")
    print(f"  derslerde olmayan kod: {len(missing_lectures)} {sorted(missing_lectures) or ''}")
    print(f"  derslik çakışması: {len(conflicts)}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


def rows_for_import(data: dict, staff_ids: dict) -> list[dict]:
    rows = []
    for offering in data["offerings"]:
        row = {
            "lectureCode": offering["lectureCode"],
            "academicYear": offering["academicYear"],
            "semester": offering["semester"],
            "groupNumber": offering["groupNumber"],
            "scheduleSlots": offering["slots"],
        }
        if offering["language"]:
            row["language"] = offering["language"]
        chosen = next((i for i in offering["instructors"] if i["slug"] and i["slug"] in staff_ids), None)
        if chosen:
            row["staffId"] = staff_ids[chosen["slug"]]
        raw_names = ", ".join(i["raw"] for i in offering["instructors"])
        if not chosen:
            row["instructorRawName"] = (raw_names or UNKNOWN_INSTRUCTOR)[:255]
        rows.append(row)
    return rows


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    client = CmsClient(write=True)
    client.probe_service_key()
    staff_ids = {i["slug"]: i["data"]["id"] for i in client.list_items("staff")}
    live_lectures = {i["data"]["code"].upper() for i in client.list_items("lectures")}

    created_lectures, errors = [], []
    if not dry_run:
        for code, name in sorted(data["missingLectures"].items()):
            if code in live_lectures:
                continue
            try:
                client.request("POST", "/cms/collections/lectures", auth=True,
                               json={"data": {"code": code, "name": name, "type": "ELECTIVE"}})
                created_lectures.append(code)
            except Exception as error:
                errors.append(f"ders oluşturulamadı {code}: {error}")

    rows = rows_for_import(data, staff_ids)
    results, totals = [], {"created": 0, "updated": 0, "failed": 0}
    if not dry_run:
        for start in range(0, len(rows), IMPORT_BATCH):
            chunk = rows[start:start + IMPORT_BATCH]
            try:
                body = client.request("POST", "/lecture-offerings/import", auth=True, json={"rows": chunk})
                report = (body or {}).get("data") or body or {}
                for field in totals:
                    totals[field] += report.get(field) or 0
                results.extend(report.get("results") or [])
                print(f"    {min(start + IMPORT_BATCH, len(rows))}/{len(rows)} satır")
            except Exception as error:
                errors.append(f"içe aktarma {start}-{start + len(chunk)}: {error}")
                break

    failed = [r for r in results if r.get("status") == "FAILED" or r.get("error")]
    warned = [r for r in results if r.get("warnings")]

    lines = [
        f"# Aşama 5 — Dönem kayıtları ve ders saatleri{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        f"Dönem: **{data['academicYear']} {data['semester']}**. Kaynak: "
        + ", ".join(f"`{s['file']}` ({s['language']})" for s in data["sources"]),
        f"Açılan ders: {len(rows)}, ders saati: {sum(len(o['slots']) for o in data['offerings'])}.",
        "",
        "| Ders | Gr | Dil | Eğitmen | Eşleşme | Saatler |",
        "| ---- | -- | --- | ------- | ------- | ------- |",
    ]
    for offering in data["offerings"]:
        who = "; ".join(i["raw"] for i in offering["instructors"]) or "—"
        match = "; ".join(i["slug"] or f"ham ad ({i['note']})" for i in offering["instructors"]) or "—"
        slots = "; ".join(f"{s['dayOfWeek'][:3]} {s['startTime']}–{s['endTime']}"
                          + (" çevrimiçi" if s.get("online") else f" {s.get('classroom', '')}")
                          for s in offering["slots"])
        lines.append(f"| {offering['lectureCode']} | {offering['groupNumber']} | {offering['language'] or '—'} | "
                     f"{who[:60]} | {match[:60]} | {slots[:90]} |")
    if data["missingLectures"]:
        lines += ["", "**Derslerde olmayan kodlar** (kayıt oluşturuldu, içerik ve yarıyıl boş; müfredatta görünmezler):", ""]
        lines += [f"- {code}: {name}" for code, name in sorted(data["missingLectures"].items())]
    unknown = [o for o in data["offerings"] if not o["instructors"]]
    if unknown:
        lines += ["", f"**Kaynakta eğitmeni yazmayan şubeler** (eğitmen alanına «{UNKNOWN_INSTRUCTOR}» yazıldı):", ""]
        lines += [f"- {o['lectureCode']} Gr.{o['groupNumber']} ({o['department']})" for o in unknown]
    if data["classroomConflicts"]:
        lines += ["", "**Derslik çakışması** (backend reddedebilir):", ""]
        lines += [f"- {c['classroom']}: {', '.join(c['offerings'])}" for c in data["classroomConflicts"]]
    if data["notes"]:
        lines += ["", "**Notlar:**", ""] + [f"- {n}" for n in data["notes"][:60]]
    if failed:
        lines += ["", "**Reddedilen satırlar:**", ""] + [f"- {r.get('key')}: {r.get('error')}" for r in failed[:50]]
    if warned:
        lines += ["", "**Uyarılar:**", ""] + [f"- {r.get('key')}: {'; '.join(r.get('warnings') or [])}" for r in warned[:50]]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  satır: {len(rows)}, içe aktarma: {totals}, reddedilen: {len(failed)}, uyarı: {len(warned)}")
    if created_lectures:
        print(f"  oluşturulan ders kaydı: {', '.join(created_lectures)}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors[:5]:
        print(f"  HATA {e}")
    if not dry_run:
        state.finish(STAGE, created=totals["created"], updated=totals["updated"], failed=totals["failed"] + len(errors),
                     report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    scrape()
    normalize()
    push(dry_run=dry_run)
