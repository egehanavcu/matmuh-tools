import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin

import requests

from .. import state
from ..api import Breaker, CmsClient, SystemicError
from ..config import DATA, RAW, REPORTS, ROOT
from ..merge import body_for_put, changes
from ..text import ascii_fold, is_upper_word, name_key, squash, tr_lower, tr_upper

STAGE = 2
KEY = "staff"
SITE = "https://mtm.yildiz.edu.tr"
RAW_FILE = RAW / "2-staff.json"
DATA_FILE = DATA / "2-staff.json"
REPORT_FILE = REPORTS / "2-staff.md"

TITLES = [
    (r"Prof\.\s*Dr\.", "Prof. Dr."),
    (r"Doç\.\s*Dr\.", "Doç. Dr."),
    (r"Dr\.\s*Öğr\.\s*Üyesi", "Dr. Öğr. Üyesi"),
    (r"Ara?ş\.\s*Gör\.\s*Dr\.", "Arş. Gör. Dr."),
    (r"Ara?ş\.\s*Gör\.", "Arş. Gör."),
    (r"Öğr\.\s*Gör\.\s*Dr\.", "Öğr. Gör. Dr."),
    (r"Öğr\.\s*Gör\.", "Öğr. Gör."),
    (r"Bilgisayar\s+İşletmeni", "Bilgisayar İşletmeni"),
    (r"Büro\s+Personeli", "Büro Personeli"),
]
ROLE_EN = {
    "Bölüm Başkanı": "Head of Department",
    "Bölüm Başkan Yardımcısı": "Deputy Head of Department",
    "Anabilim Dalı Başkanı": "Head of Division",
    "Bölüm Sekreteri": "Department Secretary",
    "Öğrenci İşleri": "Student Affairs",
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")


def split_title(text: str) -> tuple[str | None, str]:
    text = squash(text)
    for pattern, canonical in TITLES:
        m = re.match(rf"^{pattern}\s*", text)
        if m:
            return canonical, text[m.end():].strip(" -")
    return None, text


def split_name(text: str) -> tuple[str, str] | None:
    words = text.split()
    cut = len(words)
    while cut > 0 and is_upper_word(words[cut - 1]):
        cut -= 1
    if cut == len(words) or cut == 0:
        return None
    return " ".join(words[:cut]), " ".join(words[cut:])


def phones_of(texts: list[str]) -> list[str]:
    out = []
    for text in texts:
        digits = re.sub(r"\D", "", text)
        for chunk in re.findall(r"0\d{10}", digits):
            formatted = f"{chunk[:4]} {chunk[4:7]} {chunk[7:9]} {chunk[9:]}"
            if formatted not in out:
                out.append(formatted)
    return out


def office_of(text: str | None) -> str | None:
    text = squash(text)
    if not text or not re.search(r"[A-Za-z0-9]", text) or re.fullmatch(r"[-.\s]*", text):
        return None
    text = re.sub(r"\s*-\s*", "-", text)
    return text if re.search(r"\d", text) else None


def labeled(lines: list[str], label: str) -> str | None:
    for line in lines:
        m = re.match(rf"^\s*{label}\s*:\s*(.*)$", line, re.I)
        if m:
            return squash(m.group(1))
    return None


def absolute(src: str | None) -> str | None:
    if not src:
        return None
    url = urljoin(SITE + "/", src.strip())
    url = re.sub(r"(?<!:)//+", "/", url)
    path = PurePosixPath(url.split("://", 1)[1])
    if not path.suffix:
        return None
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{quote(rest, safe='/%()')}"


def avesis_of(links: list[str], cells: list[str]) -> str | None:
    for link in links + cells:
        m = re.search(r"avesis\.yildiz\.edu\.tr/([A-Za-z0-9._-]+)", link or "")
        if m:
            return f"https://avesis.yildiz.edu.tr/{m.group(1)}"
    return None


def scrape(dry_run: bool = False) -> None:
    from ..crawl import crawl
    from ..spiders.staff import StaffSpider

    crawl(StaffSpider, RAW_FILE)
    items = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    people = [i for i in items if i["kind"] == "person"]
    print(f"çekildi: {len(people)} kişi, yönetim sayfası: {'var' if any(i['kind'] == 'management' for i in items) else 'YOK'}")
    print(f"  yazıldı: {RAW_FILE.relative_to(ROOT)}")


def person_from(item: dict) -> dict:
    listing, detail = item["listing"], item["detail"]
    table = detail["table"]
    row = table[1] if len(table) >= 2 else None
    cells = [" ".join(c) for c in row] if row else []
    notes, problems = [], []

    name_source = cells[0] if cells else detail["title"] or listing["name"]
    title, bare = split_title(name_source)
    list_title, list_bare = split_title(listing["name"])
    title = title or list_title
    parts = split_name(bare) or split_name(list_bare)
    if not parts:
        problems.append(f"ad/soyad ayrılamadı: «{name_source}»")
        first, last = bare, ""
    else:
        first, last = parts
        list_words = list_bare.split()
        table_words = f"{first} {last}".split()
        if len(list_words) == len(table_words) and list_words != table_words:
            cut = len(first.split())
            spelled_first, spelled_last = " ".join(list_words[:cut]), tr_upper(" ".join(list_words[cut:]))
            if [tr_upper(w) for w in list_words] != [tr_upper(w) for w in table_words]:
                notes.append(f"yazım listeden: «{spelled_first} {spelled_last}» (kişi sayfasında «{bare}»)")
            elif (spelled_first, spelled_last) != (first, last):
                notes.append(f"soyad kişi sayfasındaki büyük harfe göre: «{spelled_first} | {spelled_last}» (listede «{list_bare}»)")
            first, last = spelled_first, spelled_last
        elif len(list_words) != len(table_words):
            notes.append(f"listede «{list_bare}», kişi sayfasında «{bare}»; kişi sayfası alındı")
    if not title:
        problems.append(f"unvan tanınmadı: «{name_source}»")

    lines = detail["summary"] or listing["lines"]
    email = None
    for candidate in ([cells[1]] if len(cells) > 1 else []) + [labeled(lines, "E-posta adresi") or ""]:
        m = EMAIL.search(candidate or "")
        if m:
            email = tr_lower(m.group(0))
            break

    phone_texts = (row[3] if row and len(row) > 3 else []) + [labeled(lines, "Tel No") or ""]
    if listing["group"] == "ADMINISTRATIVE":
        phone_texts += lines
    phones = phones_of(phone_texts)
    if len(phones) > 1:
        notes.append(f"birden fazla telefon, ilki yazıldı: {', '.join(phones)}")

    office = office_of(" ".join(row[4]) if row and len(row) > 4 else None) or office_of(labeled(lines, "Oda No"))
    list_office = office_of(labeled(listing["lines"], "Oda No"))
    if office and list_office and office != list_office:
        notes.append(f"oda listede {list_office}, kişi sayfasında {office}")

    role = None
    if listing["group"] == "ADMINISTRATIVE":
        units = [l for l in lines if ":" not in l and not re.search(r"\d", l)]
        role = units[0] if units else None

    photo = absolute(detail.get("photo")) or absolute(listing.get("photo"))
    person = {
        "sourceId": item["sourceId"],
        "sourceUrl": item["url"],
        "rawName": listing["name"],
        "academicTitle": title,
        "firstName": first,
        "lastName": last,
        "groups": [listing["group"]],
        "role": role,
        "email": email,
        "phone": phones[0] if phones else None,
        "office": office,
        "avesisLink": avesis_of(detail.get("links") or [], cells),
        "photoSource": photo,
        "notes": notes,
        "problems": problems,
    }
    person["suspicious"] = suspicious(person)
    return person


def suspicious(person: dict) -> str | None:
    local = ascii_fold((person["email"] or "").split("@")[0])
    photo = ascii_fold(unquote(PurePosixPath(person["photoSource"] or "").stem))
    surname_words = [w for w in name_key(person["lastName"]).split() if len(w) > 2]
    if not surname_words or not local:
        return None
    first_words = name_key(person["firstName"]).split()
    compact = photo.replace(" ", "")
    if any(w in compact for w in surname_words) and first_words and len(compact) > len(surname_words[-1]) + 2:
        prefix = compact.split(surname_words[-1])[0]
        if prefix and not any(compact.startswith(f) or f in compact for f in first_words):
            return f"ad fotoğraf dosyasında farklı: «{person['firstName']}» / {photo}"
    if any(w in local or w in photo for w in surname_words):
        return None
    first = name_key(person["firstName"]).split()
    hints = [h for h in (local, photo) if h]
    if first and any(first[0] in h for h in hints):
        return f"soyad ({person['lastName']}) e-postada ve fotoğraf adında geçmiyor: {local}, {photo or '—'}"
    return None


def management_roles(item: dict | None) -> list[dict]:
    if not item:
        return []
    out, role = [], None
    for row in item["rows"]:
        heading = " ".join(row["headings"])
        if re.search(r"Bölüm Başkan Yardımcı", heading):
            role = "Bölüm Başkan Yardımcısı"
            continue
        if re.search(r"Bölüm Başkanı", heading):
            role = "Bölüm Başkanı"
            continue
        for cell in row["cells"]:
            for line in cell:
                title, bare = split_title(line)
                if title and role:
                    out.append({"role": role, "name": bare, "title": title})
    return out


def normalize(dry_run: bool = False) -> None:
    items = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    people = {}
    for item in items:
        if item["kind"] != "person":
            continue
        person = person_from(item)
        if item["sourceId"] in people:
            people[item["sourceId"]]["groups"] = sorted(set(people[item["sourceId"]]["groups"]) | set(person["groups"]))
        else:
            people[item["sourceId"]] = person

    by_name = {name_key(f"{p['firstName']} {p['lastName']}"): p for p in people.values()}
    management = next((i for i in items if i["kind"] == "management"), None)
    unmatched = []
    roles = management_roles(management)
    for entry in roles:
        person = by_name.get(name_key(entry["name"]))
        if not person:
            unmatched.append(entry)
            continue
        person["role"] = entry["role"]
        if "MANAGEMENT" not in person["groups"]:
            person["groups"] = ["MANAGEMENT", *person["groups"]]
    for person in people.values():
        person["roleEn"] = ROLE_EN.get(person["role"]) if person["role"] else None

    emails = {}
    for person in people.values():
        if person["email"]:
            emails.setdefault(person["email"], []).append(person["sourceId"])
    duplicates = {e: ids for e, ids in emails.items() if len(ids) > 1}

    rank = {"MANAGEMENT": 0, "ACADEMIC": 1, "TEACHING_AND_RESEARCH": 2, "ADMINISTRATIVE": 3}
    ordered = sorted(people.values(), key=lambda p: (rank[p["groups"][0]], ascii_fold(p["lastName"]), ascii_fold(p["firstName"])))
    payload = {
        "normalizedAt": datetime.now().isoformat(timespec="seconds"),
        "people": ordered,
        "managementRoles": roles,
        "unmatchedManagement": unmatched,
        "duplicateEmails": duplicates,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    for p in ordered:
        flags = "".join(["!" if p["problems"] else "", "?" if p["suspicious"] else "", "*" if p["notes"] else ""])
        print(f"  {flags:<3} {p['academicTitle'] or '—':<20} {p['firstName']} {p['lastName']:<22} "
              f"{p['email'] or '—':<30} {p['phone'] or '—':<15} {p['office'] or '—':<7} {','.join(p['groups'])} {p['role'] or ''}")
    print(f"\n  yönetim: {', '.join(f'{r['role']} {r['name']}' for r in roles) or 'bulunamadı'}")
    if unmatched:
        print(f"  EŞLEŞMEYEN yönetim: {unmatched}")
    if duplicates:
        print(f"  AYNI E-POSTA: {duplicates}")
    print(f"  yazıldı: {DATA_FILE.relative_to(ROOT)}  (! sorun, ? şüpheli ad, * not)")


def body_of(person: dict, photo_url: str | None) -> dict:
    body = {
        "firstName": person["firstName"],
        "lastName": person["lastName"],
        "groups": person["groups"],
        "rawName": person["rawName"],
    }
    for field in ("academicTitle", "role", "roleEn", "email", "phone", "office", "avesisLink"):
        if person.get(field):
            body[field] = person[field]
    if photo_url:
        body["photo"] = {"src": photo_url, "alt": f"{person['academicTitle'] or ''} {person['firstName']} {person['lastName']}".strip()}
    return body


OWNED = ("firstName", "lastName", "groups", "academicTitle", "role", "roleEn",
         "email", "phone", "office", "avesisLink")


def live_key(data: dict) -> str:
    email = tr_lower(data.get("email") or "")
    return email or name_key(f"{data.get('firstName', '')} {data.get('lastName', '')}")


def upload_photo(client: CmsClient, url: str) -> str:
    res = requests.get(url, timeout=30, headers={"User-Agent": "matmuhbot/0.1"})
    res.raise_for_status()
    content_type = res.headers.get("Content-Type", "").split(";")[0] or "image/jpeg"
    if not content_type.startswith("image/"):
        raise ValueError(f"görsel değil: {content_type}")
    name = PurePosixPath(url.split("?")[0]).name or "photo.jpg"
    return client.upload_media(res.content, name, content_type)


def push(dry_run: bool = False) -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    people = data["people"]
    client = CmsClient(write=True)
    client.probe_service_key()
    live = {live_key(i["data"]): i for i in client.list_items(KEY)}

    blocked = [p for p in people if p["problems"]]
    plan = []
    for person in people:
        if person["problems"]:
            continue
        key = person["email"] or name_key(f"{person['firstName']} {person['lastName']}")
        current = live.get(key)
        if not current:
            plan.append(("oluştur", person, None, {}))
            continue
        patch = changes(current["data"], body_of(person, None), OWNED)
        person["_patch"] = patch
        plan.append((f"güncelle ({', '.join(sorted(patch))})" if patch else "aynı", person, current, patch))
    source_keys = {p["email"] or name_key(f"{p['firstName']} {p['lastName']}") for p in people}
    only_live = [i for k, i in live.items() if k not in source_keys]

    created = failed = updated = failed_updates = 0
    errors = []
    photo_urls = {}
    stopped = None
    breaker = Breaker()
    if not dry_run:
        try:
            for action, person, current, patch in plan:
                if action.startswith("güncelle"):
                    try:
                        client.request("PUT", f"/cms/collections/{KEY}/{current['slug']}", auth=True,
                                       json={"data": body_for_put(KEY, current["data"], patch),
                                             "version": current.get("version")})
                        updated += 1
                        breaker.ok()
                    except Exception as error:
                        failed_updates += 1
                        errors.append(f"güncelleme {person['firstName']} {person['lastName']}: {error}")
                        breaker.record(error)
                    continue
                if action != "oluştur":
                    continue
                photo_url = None
                if person["photoSource"]:
                    try:
                        photo_url = upload_photo(client, person["photoSource"])
                        breaker.ok()
                    except Exception as error:
                        errors.append(f"fotoğraf {person['firstName']} {person['lastName']}: {error}")
                        breaker.record(error)
                photo_urls[person["sourceId"]] = photo_url
                try:
                    client.request("POST", f"/cms/collections/{KEY}", auth=True, json={"data": body_of(person, photo_url)})
                    created += 1
                    breaker.ok()
                except Exception as error:
                    failed += 1
                    errors.append(f"{person['firstName']} {person['lastName']}: {error}")
                    breaker.record(error)
        except SystemicError as error:
            stopped = str(error)
        after = {live_key(i["data"]) for i in client.list_items(KEY)}
        missing = [p for _, p, _, _ in plan if (p["email"] or name_key(f"{p['firstName']} {p['lastName']}")) not in after]
        failed = len(missing) + failed_updates
        if missing:
            errors.append(f"doğrulama: {len(missing)} kişi canlıda yok")

    lines = [
        f"# Aşama 2 — Personel{' (kuru çalıştırma)' if dry_run else ''}",
        "",
        f"Kaynak: eski bölüm sitesi (`{SITE}/personel/…`), {len(people)} kişi. Anahtar: e-posta.",
        "",
        "| | Unvan | Ad | Soyad | Grup | Görev | E-posta | Telefon | Oda | AVESİS | Foto |",
        "| - | ----- | -- | ----- | ---- | ----- | ------- | ------- | --- | ------ | ---- |",
    ]
    for action, person, _, _ in plan:
        foto = "✓" if person["photoSource"] else "—"
        if not dry_run and person["photoSource"] and not photo_urls.get(person["sourceId"]) and action == "oluştur":
            foto = "✗"
        lines.append(
            f"| {action} | {person['academicTitle'] or '—'} | {person['firstName']} | {person['lastName']} | "
            f"{', '.join(person['groups'])} | {person['role'] or ''} | {person['email'] or '—'} | {person['phone'] or '—'} | "
            f"{person['office'] or '—'} | {'✓' if person['avesisLink'] else '—'} | {foto} |"
        )
    flagged = [p for p in people if p["suspicious"] or p["notes"]]
    if flagged:
        lines += ["", "**Kontrol edilecekler** (olduğu gibi yazıldı, düzeltilmedi):", ""]
        for p in flagged:
            for text in ([p["suspicious"]] if p["suspicious"] else []) + p["notes"]:
                lines.append(f"- {p['firstName']} {p['lastName']}: {text}")
    if blocked:
        lines += ["", "**Yazılmadı (sorunlu):**", ""]
        lines += [f"- {p['rawName']}: {'; '.join(p['problems'])}" for p in blocked]
    if data["unmatchedManagement"]:
        lines += ["", "**Yönetim sayfasında olup listede eşleşmeyen:**", ""]
        lines += [f"- {r['role']}: {r['title']} {r['name']}" for r in data["unmatchedManagement"]]
    if data["duplicateEmails"]:
        lines += ["", f"**Aynı e-posta:** {data['duplicateEmails']}"]
    if only_live:
        lines += ["", "**Canlıda olup kaynakta olmayan (silinmedi):**", ""]
        lines += [f"- {i['data'].get('academicTitle', '')} {i['data'].get('firstName')} {i['data'].get('lastName')}" for i in only_live]
    lines += ["", f"Yönetim sayfasından: {', '.join(f'{r['role']} → {r['name']}' for r in data['managementRoles']) or '—'}. "
              "Sayfa elle tutuluyor; güncel olup olmadığını kontrol edin."]
    if stopped:
        lines += ["", f"**DURDU:** {stopped}"]
    if errors:
        lines += ["", "**Hatalar:**", ""] + [f"- {e}" for e in errors]
    REPORTS.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = {}
    for action, _, _, _ in plan:
        label = action.split(" (")[0]
        counts[label] = counts.get(label, 0) + 1
    print(f"  plan: {counts}, sorunlu: {len(blocked)}, canlıda fazla: {len(only_live)}")
    print(f"rapor: {REPORT_FILE.relative_to(ROOT)}")
    for e in errors:
        print(f"  HATA {e}")
    if stopped:
        print(f"  DURDU: {stopped}")
    if not dry_run:
        state.finish(STAGE, created=created, updated=updated,
                     skipped=sum(1 for a, _, _, _ in plan if a == "aynı"), failed=failed,
                     report=str(REPORT_FILE.relative_to(ROOT)))


def run(dry_run: bool = False) -> None:
    scrape()
    normalize()
    push(dry_run=dry_run)
