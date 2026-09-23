# matmuhbot

matmuh.yildiz.edu.tr CMS koleksiyonlarını eski bölüm sitesi, Bologna, OBS ve
AVESİS'ten dolduran bot. Neyin, hangi biçimde, hangi sırayla yazılacağı
[`docs/contract.md`](docs/contract.md)'de.

## Kurulum

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Anahtarlar `secrets/` altında, tek satır (git'e girmez):

- `secrets/cms.key`: `CONTENT_WRITE` servis anahtarı
- `secrets/gemini.key`: Google AI Studio anahtarı (normalize ve çeviri için)

## Kullanım

```bash
.venv/Scripts/python bot.py status
.venv/Scripts/python bot.py run 0
.venv/Scripts/python bot.py run 2 scrape
.venv/Scripts/python bot.py run 2 push --dry-run
```

Her aşama bitince durur; sonraki aşama ön koşulları `done` olmadan başlamaz.
Sonuç `reports/<aşama>.md`'de.

## Klasörler

| Klasör | İçerik |
| ------ | ------ |
| `matmuhbot/` | Kod: API istemcisi, durum dosyası, aşamalar, Scrapy örümcekleri |
| `sources/` | Elle hazırlanmış kaynaklar: duyuru/haber yayın tarihleri, Wayback önbelleği |
| `tools/` | Yardımcı betikler (`node tools/announcement-dates.mjs`) |
| `schemas/`, `snapshots/` | Aşama 0'ın canlıdan okuduğu şema ve kayıtlar |
| `raw/`, `data/`, `reports/` | Aşama çıktıları: ham, düzenlenmiş, rapor |
