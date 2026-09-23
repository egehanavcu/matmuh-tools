import json
import re
from datetime import datetime

from .. import state
from ..api import Breaker, CmsClient, SystemicError
from ..config import DATA, RAW, REPORTS, ROOT
from ..merge import body_for_put, changes
from .s3_lectures import number

OWNED = ("code", "name", "nameEn", "term", "semester", "degreeLevels", "weeklyHours",
         "localCredit", "ects", "selectionCount", "optionLectureIds")


def live_view(data: dict) -> dict:
    """Okumada `optionLectureIds` yerine hesaplanmış `options` dönüyor; yazılabilir hâle çevirir."""
    return {**data, "optionLectureIds": [o["id"] for o in data.get("options") or [] if o.get("id")]}

STAGE = 4
KEY = "elective-groups"
RAW_FILE = RAW / "3-lectures.json"
DATA_FILE = DATA / "4-elective-groups.json"
REPORT_FILE = REPORTS / "4-elective-groups.md"

NAME_EN = {
    "Üniversite Sosyal Seçmeli": "University Social Elective",
    "Üniversite Mesleki Seçmeli": "University Professional Elective",
}


def group_of(section: str) -> tuple[str, str] | None:
    m = re.match(r"Mesleki Seçmeli\s+(\d+)\s*Dersleri", section)
    if m:
        n = m.group(1)
        return f"Mesleki Seçmeli {n}", f"Professional Elective {n}"
    if "Üniversite Sosyal" in section:
        return "Üniversite Sosyal Seçmeli", NAME_EN["Üniversite Sosyal Seçmeli"]
    if "Üniversite Mesleki" in section:
        return "Üniversite Mesleki Seçmeli", NAME_EN["Üniversite Mesleki Seçmeli"]
    return None


def slot_code_for(name: str, slots: dict) -> str | None:
    m = re.fullmatch(r"Mesleki Seçmeli (\d+)", name)
    if m:
        return next((code for code in slots if re.fullmatch(rf"MES{m.group(1)}-\d[GB]", code)), None)
    if name == "Üniversite Sosyal Seçmeli":
        return next((code for code in slots if code.startswith("USS-")), None)
    if name == "Üniversite Mesleki Seçmeli":
        return next((code for code in slots if code.startswith("UMS-")), None)
    return None


def masters_groups(items: list[dict]) -> list[dict]:
    program = next((i for i in items if i["kind"] == "program" and i["program"] == "masters"), None)
    if not program:
        return []
    options = sorted({r["cells"][0].strip().upper() for r in program["rows"]
                      if (r["section"] or "").strip() == "Seçmeli Dersler" and r["courseId"]})
    groups = []
    for row in program["rows"]:
        code = row["cells"][0].strip().upper()
        m = re.match(r"(\d)\.\s*Yıl\s*-\s*(Güz|Bahar)", row["section"] or "")
        if row["courseId"] or not m or not re.fullmatch(r"SEC\d+", code):
            continue
        cells = row["cells"]
        n = re.search(r"(\d+)$", cells[2])
        term = (int(m.group(1)) - 1) * 2 + (1 if m.group(2) == "Güz" else 2)
        groups.append({
            "code": code,
            "name": cells[2],
            "nameEn": f"Elective {n.group(1)}" if n else "Elective",
            "term": term,
            "semester": "FALL" if m.group(2) == "Güz" else "SPRING",
            "degreeLevels": ["MASTERS"],
            "weeklyHours": sum(number(cells[i]) or 0 for i in (3, 4, 5)),
            "localCredit": number(cells[6]),
            "ects": number(cells[7]),
            "selectionCount": 1,
            "optionCodes": options,
            "slotLabel": cells[2],
        })
    return groups


def normalize(dry_run: bool = False) -> None:
    items = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    program = next(i for i in items if i["kind"] == "program" and i["program"] == "undergraduate")
    rows = program["rows"]

    slots = {}
    for row in rows:
        code = row["cells"][0].strip().upper()
        m = re.match(r"(\d)\.\s*Yıl\s*-\s*(Güz|Bahar)", row["section"] or "")
        if m and not row["courseId"]:
            slots[code] = {
                "term": (int(m.group(1)) - 1) * 2 + (1 if m.group(2) == "Güz" else 2),
                "cells": row["cells"],
                "label": row["cells"][2],
            }

    groups, problems = [], []
    seen_options = {}
    for row in rows:
        names = group_of(row["section"] or "")
        if not names or not row["courseId"]:
            continue
        name, name_en = names
        seen_options.setdefault(name, []).append(row["cells"][0].strip().upper())

    for name, options in seen_options.items():
        code = slot_code_for(name, slots)
        if not code:
            problems.append(f"{name}: müfredatta yuva satırı bulunamadı")
            continue
        slot = slots[code]
        cells = slot["cells"]
        hours = [number(cells[i]) or 0 for i in (3, 4, 5)]
        groups.append({
            "code": code,
            "name": name,
            "nameEn": group_of(f"{name} Dersleri")[1] if name.startswith("Mesleki") else NAME_EN[name],
            "term": slot["term"],
            "semester": "FALL" if slot["term"] % 2 else "SPRING",
            "degreeLevels": ["UNDERGRADUATE"],
            "weeklyHours": sum(hours),
            "localCredit": number(cells[6]),
            "ects": number(cells[7]),
            "selectionCount": 1,
            "optionCodes": sorted(set(options)),
            "slotLabel": slot["label"],
        })

    groups += masters_groups(items)
    unused = [code for code in slots if code not in {g["code"] for g in groups}]
    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "groups": sorted(groups, key=lambda g: ("MASTERS" in g["degreeLevels"], g["term"], g["code"])),
        "unusedSlots": unused,
        "problems": problems,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    for g in payload["groups"]:
        level = "YL" if "MASTERS" in g["degreeLevels"] else "LS"
        print(f"  {level} {g['code']:<10} {g['name']:<28} {g['term']}. yy  {g['ects']} AKTS  {len(g['optionCodes'])} seçenek")
    if unused:
        print(f"  kullanılmayan yuva: {unused}")
    for p in problems:
        print(f"  SORUN {p}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}")


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    client = CmsClient(write=True)
    client.probe_service_key()
    lectures = {i["data"]["code"].upper(): i["data"]["id"] for i in client.list_items("lectures")}
    live = {i["data"]["code"].upper(): i for i in client.list_items(KEY)}

    plan, unmatched = [], {}
    for group in data["groups"]:
        missing = [c for c in group["optionCodes"] if c not in lectures]
        if missing:
            unmatched[group["code"]] = missing
        body = {k: group[k] for k in ("code", "name", "nameEn", "term", "semester", "degreeLevels", "weeklyHours",
                                      "localCredit", "ects", "selectionCount") if group.get(k) is not None}
        body["optionLectureIds"] = [lectures[c] for c in group["optionCodes"] if c in lectures]
        current = live.get(group["code"])
        if not current:
            plan.append(("oluştur", group, body, None, {}))
            continue
        current = {**current, "data": live_view(current["data"])}
        patch = changes(current["data"], body, OWNED)
        plan.append((f"güncelle ({', '.join(sorted(patch))})" if patch else "aynı", group, body, current, patch))

    created = updated = failed = 0
    errors, stopped = [], None
    breaker = Breaker()
    if not dry_run:
        try:
            for action, group, body, current, patch in plan:
                if action == "aynı":
                    continue
                try:
                    if current:
                        client.request("PUT", f"/cms/collections/{KEY}/{current['slug']}", auth=True,
                                       json={"data": body_for_put(KEY, current["data"], patch),
                                             "version": current.get("version")})
                        updated += 1
                    else:
                        client.request("POST", f"/cms/collections/{KEY}", auth=True, json={"data": body})
                        created += 1
                    breaker.ok()
                except Exception as error:
                    failed += 1
                    errors.append(f"{group['code']}: {error}")
                    breaker.record(error)
        except SystemicError as error:
            stopped = str(error)
        after = {i["data"]["code"].upper(): i["data"] for i in client.list_items(KEY)}
        for _, group, body, _, _ in plan:
            got = after.get(group["code"])
            if not got:
                failed += 1
                errors.append(f"doğrulama: {group['code']} canlıda yok")
            elif len(got.get("options") or []) != len(body["optionLectureIds"]):
                errors.append(f"doğrulama: {group['code']} seçenek sayısı {len(got.get('options') or [])}, beklenen {len(body['optionLectureIds'])}")

    lines = [
        f"# Aşama 4 — Seçmeli grupları{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        "Kaynak: Bologna lisans programı; yuva satırları (`MES{n}-{yıl}{G|B}`, `USS-…`, `UMS-…`) ve altlarındaki seçenek listeleri.",
        "",
        "| | Kod | Ad | İngilizce | Yy | AKTS | Saat | Seçim | Seçenek |",
        "| - | --- | -- | --------- | -- | ---- | ---- | ----- | ------- |",
    ]
    for action, group, body, _, _ in plan:
        lines.append(f"| {action} | {group['code']} | {group['name']} | {group['nameEn']} | {group['term']} | "
                     f"{group['ects']} | {group['weeklyHours']} | {group['selectionCount']} | {len(body['optionLectureIds'])} |")
    lines += ["", "**Seçenekler:**", ""]
    lines.append("")
    for _, group, *_ in plan:
        lines.append(f"- {group['code']}: {', '.join(group['optionCodes'])}")
    if unmatched:
        lines += ["", "**Canlıda bulunamayan ders kodları:**", ""] + [f"- {k}: {', '.join(v)}" for k, v in unmatched.items()]
    if data["unusedSlots"]:
        lines += ["", f"**Grup açılmayan yuva satırları:** {', '.join(data['unusedSlots'])}"]
    if data["problems"]:
        lines += ["", "**Sorunlar:**", ""] + [f"- {p}" for p in data["problems"]]
    if stopped:
        lines += ["", f"**DURDU:** {stopped}"]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = {}
    for action, *_ in plan:
        label = action.split(" (")[0]
        counts[label] = counts.get(label, 0) + 1
    print(f"  plan: {counts}, eşleşmeyen ders kodu olan grup: {len(unmatched)}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors[:10]:
        print(f"  HATA {e}")
    if stopped:
        print(f"  DURDU: {stopped}")
    if not dry_run:
        state.finish(STAGE, created=created, updated=updated, skipped=counts.get("aynı", 0), failed=failed,
                     report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    normalize()
    push(dry_run=dry_run)


def scrape(dry_run: bool = False) -> None:
    print("Aşama 4 ayrı çekme yapmaz; Aşama 3'ün ham verisini kullanır (raw/3-lectures.json).")
