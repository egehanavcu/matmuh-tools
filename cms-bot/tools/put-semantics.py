"""PUT kısmi gövdeyle gönderilince gönderilmeyen alanlar siliniyor mu?

Sözleşme §1.4 "PUT kaydın tamamını değiştirir, gönderilmeyen alan boşalır" diyor;
aşama 3 ise yalnızca değişen alanları gönderiyor. Bu betik atılacak bir ders
kaydı oluşturup kısmi PUT atıyor, geri okuyup alanlar duruyor mu diye bakıyor ve
kaydı siliyor. Canlıya yazar, kendi temizliğini yapar.

    .venv/Scripts/python tools/put-semantics.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matmuhbot.api import CmsClient  # noqa: E402

CODE = "ZZZ0000"
WATCHED = ("nameEn", "ects", "about", "syllabus")
FULL = {
    "code": CODE,
    "name": "PUT davranış testi",
    "nameEn": "PUT behaviour test",
    "type": "ELECTIVE",
    "ects": 3,
    "about": "Bu kayıt bir testtir, betiğin sonunda silinir.",
    "syllabus": [{"week": 1, "topic": "Test"}],
}
PATCH = {"code": CODE, "name": "PUT davranış testi (güncellendi)"}


def find(client: CmsClient) -> dict | None:
    return next((i for i in client.list_items("lectures") if i["data"]["code"].upper() == CODE), None)


def main() -> int:
    client = CmsClient(write=True)
    client.probe_service_key()
    if find(client):
        print(f"{CODE} zaten var; önce onu silin.")
        return 1

    client.request("POST", "/cms/collections/lectures", auth=True, json={"data": FULL})
    item = find(client)
    print(f"oluşturuldu: {item['slug']}")

    client.request("PUT", f"/cms/collections/lectures/{item['slug']}", auth=True,
                   json={"data": PATCH, "version": item.get("version")})
    print(f"kısmi PUT gönderildi, gövdede yalnızca: {sorted(PATCH)}")

    after = find(client)["data"]
    kept = [f for f in WATCHED if after.get(f) not in (None, "", [])]
    lost = [f for f in WATCHED if after.get(f) in (None, "", [])]
    print(f"  korunan: {', '.join(kept) or '—'}")
    print(f"  silinen: {', '.join(lost) or '—'}")
    print("SONUÇ: " + ("PUT birleştiriyor; aşama 3'ün kısmi gövdesi güvenli."
                       if not lost else
                       "PUT kaydın tamamını değiştiriyor; aşama 3 kısmi gövde göndermemeli, "
                       "canlı kaydı okuyup üstüne yazarak tamamını göndermeli."))

    client.request("DELETE", f"/lectures/{after['id']}", auth=True)
    print("test kaydı silindi." if not find(client) else f"DİKKAT: {CODE} silinemedi, elle silin.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
