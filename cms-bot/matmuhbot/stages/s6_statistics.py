import json
import re
from datetime import datetime

from .. import state
from ..api import CmsClient
from ..config import DATA, RAW, REPORTS, ROOT
from ..text import name_key, squash, tr_upper
from .s5_offerings import UNKNOWN_INSTRUCTOR, clean_name, load_map, resolve_instructors

STAGE = 6
RAW_FILE = RAW / "6-statistics.json"
DATA_FILE = DATA / "6-statistics.json"
REPORT_FILE = REPORTS / "6-statistics.md"
INBOX = ROOT / "temp" / "statistics"
IMPORT_BATCH = 25

SEMESTERS = {"guz": "FALL", "bahar": "SPRING", "yaz": "SUMMER"}
LETTERS = {"AA", "BA", "BB", "CB", "CC", "DC", "DD", "FD", "FF", "F0"}
METHODS = {"RELATIVE", "ABSOLUTE", "MANUAL"}
EXAM_TYPES = {"MIDTERM_1", "MIDTERM_1_MAKEUP", "MIDTERM_2", "MIDTERM_2_MAKEUP",
              "QUIZ", "QUIZ_2", "ASSIGNMENT", "ASSIGNMENT_2", "PROJECT", "FINAL", "RESIT"}
PLACEHOLDER_INSTRUCTORS = {"", UNKNOWN_INSTRUCTOR, "Gösterilmiyor", "Tanımsız"}
RESULT_FIELDS = ("evaluationMethod", "resultStatus", "resultDate", "examCurriculumName", "participantCount",
                 "classAverage", "classAverageParticipantCount", "standardDeviation", "classLevel", "rangesChanged")
INT_FIELDS = {"participantCount", "classAverageParticipantCount", "studentCount",
              "totalStudentCount", "attendedStudentCount", "failedByAbsenceCount", "weightPercent"}
STAT_FIELDS = ("weightPercent", "announcedAt", "totalStudentCount", "attendedStudentCount",
               "failedByAbsenceCount", "averageScore")
OBS_STAT_FIELDS = {
    "weightPercent": "Etki Yüzdesi",
    "totalStudentCount": "Sınav listesinde yer alan toplam öğrenci sayısı",
    "attendedStudentCount": "Sınava giren öğrenci sayısı",
    "failedByAbsenceCount": "Sınavda Devamsızlıktan Kaldı seçilen öğrenci sayısı",
    "averageScore": "Sınava giren öğrencilerin not ortalaması",
}
GROUP_FIELDS = ("groupNumber", "grupNo", "grup", "grupNumarasi", "sube", "subeNo", "subeNumarasi")


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = squash(value).split("(")[0].replace("%", "").replace(",", ".").strip()
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def integer(value):
    parsed = number(value)
    return None if parsed is None else int(parsed)


def term_of(label: str) -> tuple[str | None, str | None]:
    text = squash(label)
    year = re.search(r"(20\d{2}\s*-\s*20\d{2})", text)
    semester = next((v for k, v in SEMESTERS.items() if k in name_key(text)), None)
    return (year.group(1).replace(" ", "") if year else None), semester


def exam_type(name: str) -> str | None:
    if squash(name).upper() in EXAM_TYPES:
        return squash(name).upper()
    text = name_key(name)
    second = bool(re.search(r"2\b|\bii\b", text))
    if "butunleme" in text:
        return "RESIT"
    if "final" in text or "yariyil sonu" in text:
        return "FINAL"
    if "kisa sinav" in text or "quiz" in text:
        return "QUIZ_2" if second else "QUIZ"
    if "odev" in text:
        return "ASSIGNMENT_2" if second else "ASSIGNMENT"
    if "proje" in text:
        return "PROJECT"
    if "mazeret" in text:
        return "MIDTERM_2_MAKEUP" if second else "MIDTERM_1_MAKEUP"
    if "vize" in text or "ara sinav" in text:
        return "MIDTERM_2" if second else "MIDTERM_1"
    return None


def grades_of(items: list[dict], notes: list[str], where: str) -> list[dict]:
    out = []
    for item in items:
        letter = tr_upper(squash(item.get("letterGrade"))).replace(" ", "")
        if letter not in LETTERS:
            notes.append(f"{where}: bilinmeyen harf «{letter}» atlandı")
            continue
        row = {"letterGrade": letter, "studentCount": integer(item.get("studentCount")) or 0}
        low, high = number(item.get("minScore")), number(item.get("maxScore"))
        if low is not None and high is not None and 0 <= low <= high <= 100:
            row["minScore"], row["maxScore"] = low, high
        elif low is not None or high is not None:
            notes.append(f"{where}: {letter} puan aralığı kullanılmadı ({low}-{high})")
        out.append(row)
    return out


def result_of(source: dict, notes: list[str], where: str) -> dict | None:
    result = {}
    for field in RESULT_FIELDS:
        value = source.get(field)
        if value is None or value == "":
            continue
        if field in INT_FIELDS:
            value = integer(value)
        elif field in {"classAverage", "standardDeviation"}:
            value = number(value)
        elif field == "evaluationMethod" and value not in METHODS:
            notes.append(f"{where}: bilinmeyen değerlendirme yöntemi «{value}» yazılmadı")
            continue
        if value is not None:
            result[field] = value
    grades = grades_of(source.get("gradeDistributions") or source.get("grades") or [], notes, where)
    if grades:
        result["grades"] = grades
    return result or None


def statistic_of(source: dict, notes: list[str], where: str) -> dict | None:
    stat = {}
    for field in STAT_FIELDS:
        value = source.get(field)
        if value is None or value == "":
            continue
        if field in INT_FIELDS:
            stat[field] = integer(value)
        elif field == "announcedAt":
            stat[field] = squash(value)
        else:
            stat[field] = number(value)
    if stat.get("totalStudentCount") is None or stat.get("attendedStudentCount") is None:
        notes.append(f"{where}: öğrenci sayısı eksik, sınav istatistiği yazılmadı")
        return None
    if stat.get("weightPercent") is not None and not 0 <= stat["weightPercent"] <= 100:
        notes.append(f"{where}: etki yüzdesi {stat['weightPercent']} kullanılmadı")
        stat.pop("weightPercent")
    return stat


def obs_statistic(source: dict) -> dict:
    return {field: source.get(obs_key) for field, obs_key in OBS_STAT_FIELDS.items()}


def shape_of(payload) -> str | None:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    first = payload[0]
    if "offerings" in first and "code" in first:
        return "bot"
    if "dersKodu" in first and ("harfNotlari" in first or "sinavIstatistikleri" in first):
        return "obs"
    return None


def scrape(dry_run: bool = False) -> None:
    files, skipped = [], []
    for path in sorted(p for p in INBOX.glob("*") if p.is_file()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            skipped.append(f"{path.name}: okunamadı ({error})")
            continue
        shape = shape_of(payload)
        if not shape:
            skipped.append(f"{path.name}: biçim tanınmadı")
            continue
        files.append({"file": path.name, "shape": shape, "records": payload})
    if not files:
        raise FileNotFoundError(f"{INBOX.relative_to(ROOT)} içinde tanınan istatistik dosyası yok")
    RAW.mkdir(parents=True, exist_ok=True)
    RAW_FILE.write_text(json.dumps({"scrapedAt": datetime.now().isoformat(timespec="seconds"),
                                    "files": files, "skipped": skipped}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    for entry in files:
        print(f"  {entry['file']:<40} {entry['shape']:<5} {len(entry['records'])} kayıt")
    for line in skipped:
        print(f"  atlandı: {line}")
    print(f"  yazıldı: {RAW_FILE.relative_to(ROOT)}")


def entry_of(code: str, name: str, year: str, semester: str, group: int, instructor: str | None,
             source: str) -> dict:
    return {"lectureCode": code, "lectureName": name, "academicYear": year, "semester": semester,
            "groupNumber": group, "instructorRaw": instructor or None,
            "results": {}, "stats": {}, "sources": [source]}


def merge_values(current: dict, incoming: dict, notes: list[str], label: str,
                 first: str, other: str) -> None:
    for field, value in incoming.items():
        if field not in current:
            current[field] = value
        elif current[field] != value:
            shown = "harf dağılımı" if field == "grades" else f"{field} ({current[field]} / {value})"
            notes.append(f"{label}: {shown} dosyalar arasında farklı, «{first}» kaldı, «{other}» yok sayıldı")


def merge_entry(entries: dict, entry: dict, notes: list[str]) -> None:
    key = (entry["lectureCode"], entry["academicYear"], entry["semester"], entry["groupNumber"])
    label = f"{entry['lectureCode']} {entry['academicYear']} {entry['semester']} Gr.{entry['groupNumber']}"
    current = entries.get(key)
    if not current:
        entries[key] = entry
        return
    first, incoming_file = current["sources"][0], entry["sources"][0]
    current["sources"] += entry["sources"]
    for holder, incoming, what in (("results", entry["results"], "sonucu"),
                                   ("stats", entry["stats"], "istatistiği")):
        for name, value in incoming.items():
            if name not in current[holder]:
                current[holder][name] = value
                continue
            merge_values(current[holder][name], value, notes,
                         f"{label}: {name} {what}", first, incoming_file)
    if not current["instructorRaw"]:
        current["instructorRaw"] = entry["instructorRaw"]
    elif entry["instructorRaw"] and name_key(clean_name(current["instructorRaw"])) != name_key(
            clean_name(entry["instructorRaw"])):
        notes.append(f"{label}: eğitmen dosyalar arasında farklı ({current['instructorRaw']} / "
                     f"{entry['instructorRaw']}), «{first}» kaldı")


def shown(statistic: dict) -> str:
    return (f"%{statistic.get('weightPercent')} ortalama {statistic.get('averageScore')} "
            f"{squash(statistic.get('announcedAt'))[:10]}")


def add_stats(entry: dict, pairs: list, notes: list[str], label: str, unknown: list[str]) -> None:
    for name, statistic in pairs:
        kind = exam_type(name)
        if not kind:
            unknown.append(f"{label}: «{squash(name)}»")
            continue
        current = entry["stats"].get(kind)
        if current is None:
            entry["stats"][kind] = statistic
            continue
        first = squash(current.get("announcedAt")) <= squash(statistic.get("announcedAt"))
        kept, dropped = (current, statistic) if first else (statistic, current)
        entry["stats"][kind] = kept
        notes.append(f"{label}: «{squash(name)}» da {kind} türüne düşüyor; önce ilan edilen sınav "
                     f"({shown(kept)}) kaldı, diğeri ({shown(dropped)}) yazılmadı")


def read_bot_file(entry_file: dict, entries: dict, notes: list[str], unknown: list[str]) -> None:
    source = entry_file["file"]
    for lecture in entry_file["records"]:
        code = squash(lecture.get("code")).upper()
        for offering in lecture.get("offerings") or []:
            year = squash(offering.get("academicYear"))
            semester = squash(offering.get("semester")).upper()
            group = integer(offering.get("groupNumber"))
            if not code or not year or semester not in {"FALL", "SPRING", "SUMMER"} or group is None:
                notes.append(f"{source}: eksik anahtarlı kayıt atlandı ({code} {year} {semester} Gr.{group})")
                continue
            label = f"{code} {year} {semester} Gr.{group}"
            entry = entry_of(code, squash(lecture.get("name")), year, semester, group,
                             squash((offering.get("instructor") or {}).get("rawName")), source)
            for field, period in (("finalResult", "NORMAL"), ("butResult", "BUT")):
                result = result_of(offering.get(field) or {}, notes, f"{label} {period}")
                if result:
                    entry["results"][period] = result
            pairs = []
            for statistic in offering.get("examStatistics") or []:
                name = statistic.get("examType") or statistic.get("name") or ""
                parsed = statistic_of(statistic, notes, f"{label} {squash(name)}")
                if parsed:
                    pairs.append((name, parsed))
            add_stats(entry, pairs, notes, label, unknown)
            merge_entry(entries, entry, notes)


def infer_group(groups: dict, code: str, year: str, semester: str, instructor: str) -> tuple:
    candidates = groups.get((code, year, semester)) or {}
    if not candidates:
        return None, "bu dönem için başka kaynak da yok"
    wanted = name_key(clean_name(instructor))
    hits = [g for g, raw in candidates.items() if raw and wanted and name_key(clean_name(raw)) == wanted]
    if len(hits) == 1:
        return hits[0], "eğitmen adından"
    return None, (f"eğitmen, bilinen {len(candidates)} şubenin "
                  f"({', '.join(f'Gr.{g}' for g in sorted(candidates))}) hiçbirinin eğitmeniyle tutmuyor")


def read_obs_file(entry_file: dict, entries: dict, groups: dict, notes: list[str],
                  unknown: list[str], unplaceable: list[dict]) -> None:
    source = entry_file["file"]
    for record in entry_file["records"]:
        code = squash(record.get("dersKodu")).upper()
        year, semester = term_of(record.get("donem") or "")
        instructor = squash(record.get("ogretimElemani"))
        if not code or not year or not semester:
            notes.append(f"{source}: dönemi okunamayan kayıt atlandı ({code} «{record.get('donem')}»)")
            continue
        label = f"{code} {year} {semester}"
        group = next((integer(record[f]) for f in GROUP_FIELDS if record.get(f) is not None), None)
        if group is None:
            group, why = infer_group(groups, code, year, semester, instructor)
            if group is None:
                unplaceable.append({"file": source, "lectureCode": code, "academicYear": year,
                                    "semester": semester, "instructor": instructor, "reason": why})
                continue
            notes.append(f"{label}: şube numarası dosyada yok, {why} Gr.{group} kabul edildi")
        entry = entry_of(code, squash(record.get("dersAdi")), year, semester, group, instructor, source)
        for field, deviation, period in (("harfNotlari", "finalStandartSapma", "NORMAL"),
                                         ("butunlemeHarfNotlari", "butunlemeStandartSapma", "BUT")):
            letters = record.get(field) or {}
            if not letters:
                continue
            grades = [{"letterGrade": letter, "minScore": value.get("baslangic"),
                       "maxScore": value.get("bitis"), "studentCount": value.get("ogrenciSayisi")}
                      for letter, value in letters.items()]
            result = result_of({"grades": grades, "standardDeviation": record.get(deviation)},
                               notes, f"{label} Gr.{group} {period}")
            if result:
                entry["results"][period] = result
        pairs = []
        for name, statistic in (record.get("sinavIstatistikleri") or {}).items():
            parsed = statistic_of(obs_statistic(statistic), notes, f"{label} Gr.{group} {squash(name)}")
            if parsed:
                pairs.append((name, parsed))
        add_stats(entry, pairs, notes, f"{label} Gr.{group}", unknown)
        merge_entry(entries, entry, notes)


def instructor_decision(entry: dict, live: dict | None, mapping: dict) -> dict:
    ours_raw = entry["instructorRaw"]
    hit = (mapping.get(ours_raw) or {}) if ours_raw else {}
    ours_slug = hit.get("slug")
    entry["instructorSlug"] = ours_slug
    entry["instructorMatch"] = {"source": hit.get("source"), "note": hit.get("note")} if ours_slug else None
    if not live:
        return {"mode": "kaynak", "staffSlug": ours_slug, "rawName": ours_raw}
    live_slug = live.get("staffSlug")
    live_raw = squash(live.get("instructorRawName"))
    if live_raw in PLACEHOLDER_INSTRUCTORS:
        live_raw = ""
    live_id = live_slug or (mapping.get(live_raw) or {}).get("slug") or name_key(clean_name(live_raw))
    ours_id = ours_slug or name_key(clean_name(ours_raw or ""))
    if ours_id and live_id and ours_id != live_id:
        return {"mode": "çakışan", "staffSlug": live_slug, "rawName": live_raw or None,
                "reason": f"kaynakta «{ours_raw}», sitede «{live_slug or live_raw}»"}
    return {"mode": "canlı", "staffSlug": live_slug, "rawName": live_raw or None}


def normalize(dry_run: bool = False) -> None:
    raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    client = CmsClient()
    staff = [i["data"] for i in client.list_items("staff")]
    teachable = [p for p in staff if set(p.get("groups") or []) - {"ADMINISTRATIVE"}]
    lectures = {i["data"]["code"].upper() for i in client.list_items("lectures")}
    live = {(o["data"]["lectureCode"].upper(), o["data"]["academicYear"], o["data"]["semester"],
             o["data"]["groupNumber"]): o["data"] for o in client.list_items("lecture-offerings")}

    notes, unknown, unplaceable = [], [], []
    entries: dict = {}
    for entry_file in [f for f in raw["files"] if f["shape"] == "bot"]:
        read_bot_file(entry_file, entries, notes, unknown)

    groups: dict = {}
    for (code, year, semester, group), entry in entries.items():
        groups.setdefault((code, year, semester), {})[group] = entry["instructorRaw"]
    for (code, year, semester, group), data in live.items():
        groups.setdefault((code, year, semester), {}).setdefault(group, squash(data.get("instructorRawName")))
    for entry_file in [f for f in raw["files"] if f["shape"] == "obs"]:
        read_obs_file(entry_file, entries, groups, notes, unknown, unplaceable)

    empty = sorted(k for k, e in entries.items() if not e["results"] and not e["stats"])
    for key in empty:
        entries.pop(key)

    names = sorted({e["instructorRaw"] for e in entries.values() if e["instructorRaw"]})
    mapping = resolve_instructors(names, teachable) if names else load_map()

    missing_lectures = {}
    for key, entry in sorted(entries.items()):
        entry["live"] = key in live
        entry["instructor"] = instructor_decision(entry, live.get(key), mapping)
        if entry["lectureCode"] not in lectures:
            missing_lectures[entry["lectureCode"]] = entry["lectureName"] or entry["lectureCode"]

    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "sources": [{"file": f["file"], "shape": f["shape"], "records": len(f["records"])} for f in raw["files"]],
        "offerings": [entries[k] for k in sorted(entries)],
        "missingLectures": missing_lectures,
        "unplaceable": unplaceable,
        "unknownExams": unknown,
        "skippedEmpty": [" ".join(map(str, k)) for k in empty],
        "notes": notes,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    offerings = payload["offerings"]
    clashing = [o for o in offerings if o["instructor"]["mode"] == "çakışan"]
    print(f"  açılış: {len(offerings)} (sitede zaten var olan {sum(1 for o in offerings if o['live'])})")
    print(f"  harf sonucu: {sum(len(o['results']) for o in offerings)}, "
          f"sınav istatistiği: {sum(len(o['stats']) for o in offerings)}")
    print(f"  boş olduğu için atlanan: {len(empty)}, eğitmeni çakışan (gönderilmeyecek): {len(clashing)}")
    print(f"  şubesi belirlenemeyen kayıt: {len(unplaceable)}, eşleşmeyen sınav adı: {len(unknown)}")
    print(f"  derslerde olmayan kod: {len(missing_lectures)} {sorted(missing_lectures) or ''}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


def rows_for_import(offerings: list[dict], staff_ids: dict) -> tuple:
    rows, skipped = [], []
    for entry in offerings:
        decision = entry["instructor"]
        if decision["mode"] == "çakışan":
            skipped.append(entry)
            continue
        row = {
            "lectureCode": entry["lectureCode"],
            "academicYear": entry["academicYear"],
            "semester": entry["semester"],
            "groupNumber": entry["groupNumber"],
            "gradeResults": [{"examPeriod": period, "result": result}
                             for period, result in sorted(entry["results"].items())],
            "examStatistics": [{"examType": kind, "statistic": statistic}
                               for kind, statistic in sorted(entry["stats"].items())],
        }
        slug = decision.get("staffSlug")
        if slug and slug in staff_ids:
            row["staffId"] = staff_ids[slug]
        else:
            row["instructorRawName"] = (decision.get("rawName") or UNKNOWN_INSTRUCTOR)[:255]
        rows.append(row)
    return rows, skipped


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

    rows, skipped = rows_for_import(data["offerings"], staff_ids)
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
        f"# Aşama 6 — Harf sonuçları ve sınav istatistikleri{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        "Kaynak: " + ", ".join(f"`{s['file']}` ({s['shape']}, {s['records']} kayıt)" for s in data["sources"]),
        f"Gönderilen açılış: {len(rows)}, harf sonucu: {sum(len(o['results']) for o in data['offerings'])}, "
        f"sınav istatistiği: {sum(len(o['stats']) for o in data['offerings'])}.",
        "Satırlarda `scheduleSlots` yok; aşama 5'in ders saatlerine dokunulmadı.",
        "",
        "| Ders | Dönem | Gr | Eğitmen | Kaynak | Harf | Sınav |",
        "| ---- | ----- | -- | ------- | ------ | ---- | ----- |",
    ]
    for entry in data["offerings"]:
        decision = entry["instructor"]
        who = decision.get("staffSlug") or decision.get("rawName") or "—"
        lines.append(
            f"| {entry['lectureCode']} | {entry['academicYear']} {entry['semester'][:3]} | {entry['groupNumber']} | "
            f"{who[:40]} | {decision['mode']} | {', '.join(sorted(entry['results'])) or '—'} | "
            f"{', '.join(sorted(entry['stats'])) or '—'} |")
    if skipped:
        lines += ["", "**Eğitmeni çakıştığı için gönderilmeyen açılışlar** (karar sizde; gönderilirse sitedeki "
                      "eğitmen üzerine yazılır):", ""]
        lines += [f"- {e['lectureCode']} {e['academicYear']} {e['semester']} Gr.{e['groupNumber']}: "
                  f"{e['instructor'].get('reason')}" for e in skipped]
    if data["unplaceable"]:
        lines += ["", "**Şube numarası olmadığı için yazılamayan kayıtlar** (kaynak dosyaya `groupNumber` "
                      "eklenirse aşama yeniden çalıştırıldığında yazılır):", ""]
        lines += [f"- `{u['file']}` {u['lectureCode']} {u['academicYear']} {u['semester']} "
                  f"({u['instructor']}) — {u['reason']}" for u in data["unplaceable"]]
    guessed = [o for o in data["offerings"] if (o.get("instructorMatch") or {}).get("source") == "ai"]
    if guessed:
        lines += ["", "**Yapay zekânın eşleştirdiği eğitmenler** (kodla çözülemedi, gözden geçirin; yanlışsa "
                      "`sources/instructor-map.json` satırını `\"source\": \"elle\"` yapın):", ""]
        lines += [f"- {o['lectureCode']} {o['academicYear']} {o['semester']} Gr.{o['groupNumber']}: "
                  f"«{o['instructorRaw']}» → `{o['instructor']['staffSlug']}` — {o['instructorMatch'].get('note')}"
                  for o in guessed]
    if data["unknownExams"]:
        lines += ["", "**Sınav türüne eşleşmeyen adlar** (istatistik yazılmadı):", ""]
        lines += [f"- {u}" for u in data["unknownExams"]]
    if data["missingLectures"]:
        lines += ["", "**Derslerde olmayan kodlar** (kayıt oluşturuldu, içerik ve yarıyıl boş; müfredatta "
                      "görünmezler):", ""]
        lines += [f"- {code}: {name}" for code, name in sorted(data["missingLectures"].items())]
    if data["skippedEmpty"]:
        lines += ["", f"**Sonucu ve sınavı olmayan, atlanan açılışlar** ({len(data['skippedEmpty'])}): "
                      + ", ".join(data["skippedEmpty"])]
    if data["notes"]:
        lines += ["", "**Notlar:**", ""] + [f"- {n}" for n in data["notes"][:80]]
    if failed:
        lines += ["", "**Reddedilen satırlar:**", ""] + [f"- {r.get('key')}: {r.get('error')}" for r in failed[:50]]
    if warned:
        lines += ["", "**Uyarılar:**", ""] + [f"- {r.get('key')}: {'; '.join(r.get('warnings') or [])}"
                                              for r in warned[:50]]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  satır: {len(rows)}, içe aktarma: {totals}, reddedilen: {len(failed)}, uyarı: {len(warned)}")
    print(f"  gönderilmeyen (eğitmen çakışması): {len(skipped)}, şubesiz kayıt: {len(data['unplaceable'])}")
    if created_lectures:
        print(f"  oluşturulan ders kaydı: {', '.join(created_lectures)}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors[:5]:
        print(f"  HATA {e}")
    if not dry_run:
        state.finish(STAGE, created=totals["created"], updated=totals["updated"], skipped=len(skipped),
                     failed=totals["failed"] + len(errors), report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    scrape()
    normalize()
    push(dry_run=dry_run)
