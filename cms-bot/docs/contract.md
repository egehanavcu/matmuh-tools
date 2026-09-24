# matmuh koleksiyon doldurma botu — veri sözleşmesi

Doğrulandığı an: **2026-09-22**. Kaynak: backend v1.7.0 (`6bdd403`), canlı API
ve frontend `52c6506`. Şema sonradan değişmiş olabilir; bot her çalıştığında
şemayı canlıdan okumalı (bkz. §1.3) ve bu belgeyle çelişirse **canlı şema
kazanır**.

Depolar (2026-09-24'te güncellendi, ikisi de taşındı):

| | |
| --- | --- |
| Backend | <https://github.com/ytumatmuh/matmuh-backend> |
| Frontend | <https://github.com/ytumatmuh/matmuh-frontend> |

---

## 0. Özet

| Koleksiyon | Anahtar | Canlıda | Kaynak | Bağımlı olduğu |
| ---------- | ------- | ------- | ------ | -------------- |
| Akademik dönemler | `academic-terms` | 2 | YTÜ akademik takvim | — |
| Personel | `staff` | 39 | AVESİS, bölüm sayfası | — |
| Dersler | `lectures` | 265 | YTÜ Bologna | — |
| Seçmeli grupları | `elective-groups` | 14 | YTÜ Bologna | `lectures` |
| Açılan dersler | `lecture-offerings` | 78 | OBS, ders programı | `lectures`, `staff`, `academic-terms` |
| Duyurular | `announcements` | 45 | Bölüm sitesi | — |
| Haberler | `news` | 14 | Bölüm sitesi | — |

**Doldurma sırası:** `academic-terms` → `staff` → `lectures` →
`elective-groups` → `lecture-offerings` (ders saatleri, sonra harf sonuçları ve
sınav istatistikleri, sonra sınav ağırlıkları). `announcements` ve `news`
bağımsız. Bot bunları on bir aşamada, her aşamadan sonra durup
izin bekleyerek çalıştırır: §4. İngilizce içerik: §4.7.

**Bot dokunmamalı:** `lecture-notes` (öğrenci yüklemeleri), sayfa blokları
(`/api/cms/content`, `cms-sync` ile yönetiliyor), `schedule-slots` ve
`calendar-events` (koleksiyon değiller; ders saatleri `lecture-offerings`
içinden yazılıyor).

**2026-09-22 10:23 — silme yapıldı.** Yedi koleksiyondan altısı boş;
`academic-terms` 2 kayıtla duruyor. Yedek: `docs/backups/wipe-2026-09-22T10-23-11-541Z`.
Duyuru purge'ü arşivde zaten duran 16 eski duyuruyu da kalıcı sildi (62 = 46 +
16); bunlar yedekte yok. Silinen duyuru ve haberler deneme verisiydi; yedekleri
kaynak olarak kullanılmaz.

**Sitenin önbelleği bot yazmalarını görmüyor.** Frontend koleksiyon okumalarını
süresiz önbellekliyor ve yalnızca sitedeki CMS çekmecesinden yapılan kayıtlarda
tazeliyor (`cms-collection-<key>` etiketi). API'den yapılan yazmalar (silme ve
bot) sitede görünmez; silmeden sonra site hâlâ eski duyuru ve personeli
gösteriyordu. Düzeltmeyi diğer geliştiriciler üstlendi. O zamana kadar bot
sonucu sitede değil **API'den** doğrular (§4.1, push adımı 7); sitede görmek
için frontend konteynerinin yeniden başlatılması gerekir.

**2026-09-21 kararı: bot sıfırdan başlıyor.** Bot çalışmadan önce yedi
koleksiyon, ders saatleri ve ders notları `docs/tools/wipe-collections.mjs`
ile silinecek (önce `docs/backups/` altına tam yedek alınıyor). Bu yüzden
§0'daki "Canlıda" sayıları silmeden önceki durumu gösteriyor. Silme sonrası:

- Duyuru ve haberler önce arşivlenip sonra kalıcı siliniyor (purge); slug'ları
  serbest kalıyor, aynı başlıkla yeniden oluşturulan kayıt aynı adresi alır.
- Ders, personel, seçmeli grup ve açılışlar veritabanında `is_deleted` ile
  kalıyor ama slug/kod kontrolleri onları görmüyor, aynı kodla yeniden
  oluşturulabiliyor. Bunların API'den geri yükleme yolu yok; tek geri dönüş
  yedek dosyaları.
- Açılış silinince backend ders saatlerini, öğrenci ders kayıtlarını ve sınav
  tarihlerini de siliyor; notların yalnızca açılış bağı çözülüyor. Ders
  silinince açılışları ve notları da siliniyor (dosyalar depoda kalır).
- Akademik dönemler servis anahtarıyla silinemiyor; admin token'ı yoksa silme
  onları bırakır ve bot 1. aşamada yıl + dönem anahtarıyla günceller.

Koleksiyonlar dolduktan sonra editörler kayıtları elle düzenleyebilir. Bot
ikinci ve sonraki çalıştırmalarda yalnızca eksik kayıtları oluşturmalı, var
olanı güncellemeyi açık bir kararla yapmalı (bkz. §4).

---

## 1. API

### 1.1 Adres ve kimlik doğrulama

```
BASE = https://matmuh.yusufacmaci.com/api
```

Bot iki yolla kimlik doğrulayabilir. **Önerilen: `CONTENT_WRITE` servis
anahtarı.** Uzun ömürlü, iptal edilebilir, tek başına bota ait. Admin
`POST /api/service-keys` ile üretir (`capabilities: ["CONTENT_WRITE"]`); ham
anahtar yalnızca o yanıtta döner. JWT ile aynı başlıkla gönderilir:

```
Authorization: Bearer <servis anahtarı ya da access token>
```

Bot servis anahtarını `secrets/cms.key`'den okur (§4.2). Anahtar 1 saatte
dolmaz; aşamalar arasındaki kapı yine kullanıcının izniyle çalışır.

Servis anahtarının geçtiği uçlar: `POST/PUT/DELETE /api/cms/collections/**`,
`POST /api/cms/media`, `POST /api/lecture-offerings/import` ve
`DELETE /api/lectures|staff|elective-groups|lecture-offerings/**`. Geçmediği
uçlar: ders notu yönetimi (`/api/lecture-notes/**`) ve akademik dönem/takvim
yönetimi (`/api/calendar-admin/**`); bunlar admin JWT'si ister. Bot bu uçları
kullanmıyor, akademik dönemler koleksiyon API'siyle yazılıyor.

Alternatif: admin kullanıcının JWT access token'ı.

- Access token **1 saat** geçerli (`JWT_ACCESS_VALIDITY_SECONDS=3600`).
- Refresh token 30 gün geçerli ama **her kullanımda dönüyor**, eski bir refresh
  token'ın yeniden kullanılması saldırı sayılıp oturum ailesinin tamamını iptal
  ediyor. Bot refresh ile yenileyecekse (`POST /api/auth/refresh`, çerez
  `refresh_token`, yol `/api/auth`) her yanıttaki yeni çerezi saklamalı ve aynı
  oturumu tarayıcıda kullanmamalı.
- Mevcut `CMS_SYNC_KEY` (`SCHEMA_SYNC`) koleksiyonlarda 403 alır, bot için
  kullanılamaz.

**Yazmadan önce kimliği yoklayın.** Okuma uçları anonim açık olduğu için
geçersiz anahtar ilk yazmaya kadar fark edilmez ve iş yarıda kalır.

```
Servis anahtarı:  POST /lecture-offerings/import  {"rows": []}
                  → 400 "En az bir satır gönderilmelidir."  = geçerli, hiçbir şey yazılmaz
                  → 401 / 403                               = dur
Admin JWT:        GET /cms/collections/me  → 200 değilse dur
```

`GET /cms/collections/me` servis anahtarına **her zaman 403** döner (yalnızca
ADMIN/EDITOR); servis anahtarını onunla yoklamayın.

### 1.2 Okuma

```
GET /cms/collections/{key}?limit=100&offset=0
GET /cms/collections/{key}/{slug}
```

- `limit` en çok 100; tüm liste için `offset` ile sayfalayın, `total`'a bakın.
- `sort=alan:asc|desc` (virgülle çoklu), `q=metin` (şemada `searchable`
  alanlarda), `<alan>=<değer>` (şemada `filterable` alanlarda).
- Yerelleştirilmiş koleksiyonlarda `locale=tr|en`.

Liste yanıtı:

```json
{ "items": [ { "id": "…", "slug": "…", "version": 3, "locale": "tr",
               "translationGroupId": "…", "createdAt": "…", "updatedAt": "…",
               "canEdit": false, "collectionKey": "announcements",
               "data": { "title": "…", "…": "…" } } ],
  "total": 45 }
```

`version` üst seviyede, güncellemede gerekiyor. Yerelleştirilmemiş
koleksiyonlarda `locale` ve `translationGroupId` boş. Özel sağlayıcılı
koleksiyonlarda kaydın kimliği hem `id` hem `data.id` (aynı UUID).

### 1.3 Şema (herkese açık)

```
GET /cms/collections/{key}/schema
```

```json
{ "collectionKey": "lectures", "slugSource": "AutoGenerated", "slugEditable": false,
  "locales": [], "displayName": "Dersler", "displayField": "name",
  "schema": { "fields": [ { "name": "code", "type": "ShortText", "required": true,
      "readOnly": false, "computed": false, "filterable": true,
      "source": null, "itemFields": null, "help": "…" } ] } }
```

`source` seçim alanlarının izinli değerlerini verir:
`{"kind":"static","values":[…]}` ya da `{"kind":"collection","collection":"staff"}`.
Bot her çalıştırmada şemayı okuyup gönderdiği veriyi ona göre doğrulamalı.

### 1.4 Yazma

| İşlem | İstek | Gövde |
| ----- | ----- | ----- |
| Oluştur | `POST /cms/collections/{key}` | `{"data": {…}}` |
| Güncelle | `PUT /cms/collections/{key}/{slug}` | `{"data": {…}, "version": N}` |
| Arşivle | `DELETE /cms/collections/{key}/{slug}?version=N` | — |
| Kalıcı sil (tek) | `DELETE /cms/collections/{key}/{slug}/purge` | — → 204; arşivde değilse 409 |
| Arşivi boşalt | `DELETE /cms/collections/{key}/purge` | — → `{"purged": N}` |

Purge kaydı, eski adlarını (alias) ve taslaklarını siler; slug serbest kalır,
aynı başlıkla yeni kayıt `-2` eki almaz.

Arşivleme ve purge yalnızca `announcements`/`news` için. Sağlayıcılı dördü
kendi REST uçlarıyla silinir: `DELETE /api/lectures|staff|elective-groups|lecture-offerings/{id}`
(servis anahtarı geçer). Akademik dönemler yalnızca admin JWT'siyle,
`DELETE /api/calendar-admin/terms/{id}`.

- Yerelleştirilmiş koleksiyonlarda (`announcements`, `news`) yazmada
  `?locale=tr` **zorunlu**; yoksa 400.
- Var olan kayda PUT **`version` ister**. Eskiyse 409 (sürüm çakışması): kaydı
  yeniden okuyup tekrar deneyin, körlemesine ezmeyin.
- PUT, kayıt yoksa genel koleksiyonlarda oluşturabilir ama `lecture-offerings`
  gibi özel sağlayıcılı koleksiyonlarda 404 döner. **Kural: oluşturmak için POST,
  güncellemek için PUT.**
- Taşınmış (eski adı olan) bir slug'a yazmak `409 reason=moved` ve
  `conflictingSlug` döner; o slug'a yazın.
- PUT'un gönderilmeyen alanı boşalttığı yazıyordu; **2026-09-23'te ölçüldü,
  `lectures` için doğru değil: birleştiriyor.** `tools/put-semantics.py` atılacak
  bir kayıt oluşturup yalnızca `code` + `name` ile PUT attı; `nameEn`, `ects`,
  `about` ve `syllabus` yerinde kaldı. Ölçüm yalnızca sağlayıcılı `lectures`
  koleksiyonunda yapıldı; genel koleksiyonlar (`announcements`, `news`)
  denenmedi.
- **Yine de tamamını gönderin.** Bot öyle yapıyor (aşama 2, 3, 4;
  `matmuhbot/merge.py`): canlı kaydı okuyup yazılabilir alanlarının tamamını,
  değiştirmek istediklerini üstüne yazarak gönderiyor. Birleştirme belgelenmiş
  bir söz değil, ölçülmüş bir davranış; backend sürümü değişince sessizce
  değişebilir. Kısmi gövde göndermek o gün veri kaybına dönerdi.
  Yükseltmelerden sonra betiği tekrar çalıştırmak bunu bir dakikada söylüyor.
- **Okuduğunuz `data`'yı olduğu gibi geri göndermeyin.** İçinde şemada
  `readOnly`/`computed` olan alanlar (`noteCount`, `staff`, `options`,
  `lectureName`, `fullName`, …) var; özel sağlayıcılı koleksiyonlar bunları
  bilinmeyen alan sayıp reddedebilir. Göndermeden önce şemaya göre ayıklayın,
  üst seviyede yalnızca `id` ve `slug` kalabilir.

### 1.5 Değer kuralları (tüm koleksiyonlar)

`announcements` ve `news` yazmaları `CollectionSchemaValidator`'dan geçiyor.
Diğer beş koleksiyonun kendi sağlayıcısı var ve kendi kurallarını uyguluyor
(her birinin bölümünde); değer biçimleri aynı, ama 255 karakter sınırı
genel doğrulayıcıya ait, orada veritabanı kolon uzunlukları geçerli. Güvenli
kural: ShortText'te 255'i aşmayın, istisnalar bölümlerinde yazılı.

| Tür | JSON | Kural |
| --- | ---- | ----- |
| `ShortText`, `Url` | string | **en çok 255 karakter** |
| `LongText`, `RichText` | string | sınır yok; RichText HTML |
| `Number` | number | string olarak `"5"` reddedilir |
| `Bool` | boolean | |
| `Date` | string | ISO: `2025-09-18`, `2025-09-18T10:00:00`, `…Z` |
| `Select` | string | şemadaki `values`'tan biri |
| `StringArray` | string[] | kaynağı varsa her eleman izinli değerlerden |
| `Image` | `{"src": url, "alt": string}` | **`src` ve `alt` ikisi de zorunlu** |
| `File` | `{"url", "name", "mime"?, "size"?}` | `url` ve `name` zorunlu; `previewUrl` salt okunur |
| `ObjectArray` | object[] | her öğe `itemFields`'a göre doğrulanır |

- **Bilinmeyen alan reddedilir** (`Unknown field 'x'.`). Üst seviyede yalnızca
  `id` ve `slug` zarf anahtarları tolere edilir.
- `readOnly` / `computed` alanlar sessizce atılır; göndermeyin.
- Hatalar `application/problem+json`, doğrulama hataları satır satır
  (`Field 'x' is required.`).

### 1.6 Dosya ve görsel yükleme

```
POST /cms/media   (multipart)  file=<dosya>  publicAccess=true|false
→ { "data": { "url": "https://…/cdn/images/…", "previewUrl": "…"? } }
```

- Görseller CDN'e (`/cdn/images/…`), belgeler `publicAccess=true` ise herkese
  açık, `false` ise giriş isteyen alana gider.
- Dosya başına en çok **25 MB**.
- Ofis belgeleri için PDF önizlemesi üretilirse `previewUrl` döner.
- Dönen `url` `Image.src` ya da `File.url` olarak yazılır.

Dış bir adrese bağlamak da geçerli (mevcut eklerin bir kısmı
`https://mtm.yildiz.edu.tr/media/…`'ya işaret ediyor), ama o site kapanırsa
bağlantı kırılır. Kalıcılık için dosyaları `/cms/media` ile yeniden barındırmak
daha güvenli.

---

## 2. Koleksiyonlar

Her tabloda **Z** zorunlu, **S** salt okunur/hesaplanan (göndermeyin).

### 2.1 `academic-terms` — akademik dönemler

Slug `academicYear`'dan otomatik, düzenlenemez. `(academicYear, semester)`
çifti tekil.

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `academicYear` | ShortText | Z | `YYYY-YYYY`, ikinci yıl = birinci + 1, başlangıç ≥ 2000 ve ≤ bu yıl + 1 |
| `semester` | Select | Z | `FALL` · `SPRING` · `SUMMER` |
| `startDate` | Date | Z | Derslerin başladığı ilk gün |
| `endDate` | Date | Z | Derslerin bittiği son gün, `startDate`'ten önce olamaz |

**Sitedeki etkisi:** Ders programı sayfası `/api/calendar/weekly`'yi dönem
vermeden çağırıyor; backend **bugünü kapsayan** dönemi seçiyor ve yanıt
`{term, slots}` biçiminde, sayfa dönem etiketini `term`'den basıyor. Dönem aralıkları
çakışmamalı, yoksa hangi dönemin gösterileceği belirsizleşir. Haftalık dersler
takvim günlerine bu aralık içinde açılıyor.

Canlıda: `2025-2026 SPRING` (2026-02-09 → 2026-06-19, **tarihler tahmini**,
gerçeğiyle düzeltilmeli) ve `2026-2027 FALL` (2026-08-23 → 2026-12-31).

```json
{ "data": { "academicYear": "2026-2027", "semester": "FALL",
            "startDate": "2026-09-21", "endDate": "2027-01-09" } }
```

### 2.2 `staff` — personel

Yerelleştirilmemiş. Slug otomatik, düzenlenemez; canlıdaki biçim ad-soyadın
ASCII hali (`fatma-aydin-akgun`, `inci-albayrak`).

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `firstName` | ShortText | Z | Ad(lar), normal yazım: `Fatma Aydın` |
| `lastName` | ShortText | Z | Soyad, **Türkçe büyük harf**: `AKGÜN`, `BABUŞCU YEŞİL` |
| `groups` | StringArray | Z | En az bir: `MANAGEMENT` · `ACADEMIC` · `TEACHING_AND_RESEARCH` · `ADMINISTRATIVE` |
| `role` | ShortText | | ≤100. Yönetim kartında adın altında: `Bölüm Başkanı` |
| `roleEn` | ShortText | | ≤100. İngilizce görev: `Head of Department` |
| `academicTitle` | ShortText | | Unvan, aşağıdaki listeden |
| `email` | ShortText | | Geçerli e-posta |
| `phone` | ShortText | | ≤40, `0212 383 46 87` biçimi |
| `office` | ShortText | | `A-219` |
| `photo` | Image | | `{src, alt}`; boşsa baş harfler gösterilir |
| `avesisLink` | Url | | `https://avesis.yildiz.edu.tr/<kullanıcı>` |
| `officeHours` | ObjectArray | | Görüşme saatleri, aşağıda |
| `rawName` | ShortText | S | İçe aktarmadan gelen ham ad |
| `fullName` | ShortText | S | Unvan + ad + soyad |

`officeHours` öğesi:

| Alan | Tür | | Kural |
| ---- | --- | - | ----- |
| `dayOfWeek` | Select | Z | `MONDAY` … `SUNDAY` |
| `startTime` | ShortText | Z | `HH:mm`, ör. `10:00` |
| `endTime` | ShortText | Z | `HH:mm`, başlangıçtan sonra |
| `description` | ShortText | | ≤255, ör. `Ofis D-105`, `randevulu` |
| `descriptionEn` | ShortText | | ≤255, İngilizce açıklama |

**İngilizce:** `roleEn` ve `officeHours[].descriptionEn` `/en`'de
gösteriliyor, boşsa Türkçesi. Unvanı sitenin kendi sözlüğü çeviriyor; ad ve
soyad çevrilmez. Bot bu iki alanı §4.7'deki sözlükle doldurur.

**Gelenekler ve sitedeki etkileri:**

- Bölüm başkanı ve yardımcıları hem `MANAGEMENT` hem `ACADEMIC`'te olur;
  `/personel` sekmeleri doğrudan `groups`'a göre kuruluyor.
- `/personel` sayacı öğretim üyelerini **tam eşleşmeyle** sayıyor:
  `Prof. Dr.`, `Doç. Dr.`, `Dr. Öğr. Üyesi`. Başka bir yazım (`Prof.Dr.`,
  `Doç.Dr.`) sayılmaz.
- Canlıda kullanılan unvanlar: `Prof. Dr.`, `Doç. Dr.`, `Dr. Öğr. Üyesi`,
  `Araş. Gör. Dr.`, `Araş. Gör.`, `Arş. Gör.`, `Öğr. Gör.`,
  `Bilgisayar İşletmeni`, `Büro Personeli`. **`Araş. Gör.` ile `Arş. Gör.` ikisi
  birden var**; bot birini seçip tutarlı yazmalı.
- Erasmus ve staj sayfaları kişileri **e-posta adresiyle** buluyor. Var olan bir
  kaydın e-postasını değiştirmek o sayfalardaki eşleşmeyi koparır.
- İdari personelin e-postası olmayabilir; eşleştirmede e-posta yoksa ad + soyada
  düşün.

Canlıda 39 kayıt: `photo` 0 dolu, `officeHours` 0 dolu, `avesisLink` 37 dolu.

```json
{ "data": { "firstName": "İnci", "lastName": "ALBAYRAK",
            "groups": ["ACADEMIC"], "academicTitle": "Prof. Dr.",
            "email": "ibayrak@yildiz.edu.tr", "phone": "0212 383 46 87",
            "office": "A-219", "avesisLink": "https://avesis.yildiz.edu.tr/ibayrak",
            "officeHours": [ { "dayOfWeek": "MONDAY", "startTime": "10:00",
                               "endTime": "12:00", "description": "Ofis A-219" } ] } }
```

### 2.3 `lectures` — dersler

Yerelleştirilmemiş; İngilizce için ayrı alanlar var. Slug = küçük harfli ders
kodu (`mtm3512`), düzenlenemez. Kod aramaları büyük/küçük harfe duyarsız.

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `code` | ShortText | Z | Büyük harf, boşluksuz: `MTM3512` |
| `name` | ShortText | Z | Türkçe ders adı |
| `nameEn` | ShortText | | ≤255; `/en` sayfalarında, boşsa Türkçe ad |
| `languages` | StringArray | | Eğitim dili: `TURKISH` · `ENGLISH`, çoklu seçim. "İngilizce, Türkçe" verilen ders ikisini birden alır. Süzgeç: `?languages=ENGLISH`; `?languages=ENGLISH,TURKISH` herhangi biri; geçersiz değer 400. Açılışın kendi `language`'ından ayrı |
| `about` | LongText | | Ders içeriği, **düz metin** (HTML değil) |
| `aboutEn` | LongText | | İngilizce içerik |
| `gradingPolicy` | LongText | | Değerlendirme açıklaması |
| `gradingPolicyEn` | LongText | | İngilizce değerlendirme |
| `resources` | LongText | | Kaynaklar |
| `resourcesEn` | LongText | | İngilizce kaynaklar |
| `degreeLevels` | StringArray | | `UNDERGRADUATE` · `MASTERS` · `DOCTORATE`; boşsa koddan türetilir |
| `type` | Select | | `REQUIRED` · `ELECTIVE` |
| `category` | Select | | `BASIC_SCIENCE` · `FOREIGN_LANGUAGE` · `COMMON_REQUIRED` · `CORE_PROFESSION` · `SPECIALIZATION` · `GENERAL_CULTURE` |
| `term` | Number | | Müfredattaki yarıyıl, 1–8 |
| `semester` | Select | | `FALL` (tek yarıyıl) · `SPRING` (çift yarıyıl) · `SUMMER` |
| `syllabus` | ObjectArray | | `[{week: Number, topic: ShortText, topicEn?: ShortText}]`, `topic` ve `topicEn` ≤1000 (kolon uzunluğu); hafta sırasıyla döner |
| `midtermWeight` | Number | | 0–100, Bologna varsayılanı |
| `finalWeight` | Number | | 0–100 |
| `theoryHours`, `practiceHours`, `labHours` | Number | | ≥0; sitede `T+U+L` = `3+0+0` |
| `weeklyHours` | Number | | Boşsa üç saatin toplamı |
| `localCredit` | Number | | Yerel kredi |
| `ects` | Number | | AKTS |
| `bolognaLink` | Url | | `https://bologna.yildiz.edu.tr/index.php?r=course/view&id=<bolognaId>&aid=24&pid=37` |
| `notesLink` | Url | | |
| `noteCount`, `electiveGroupCount`, `statisticsTermCount`, `staff` | | S | Hesaplanır |

**`degreeLevels` koddan türetme kuralı** (boş bırakılırsa backend bunu
uyguluyor): numara 1000–4999 → `UNDERGRADUATE`; 5000–5003 → `MASTERS`;
6000–6003 → `DOCTORATE`; diğer ≥5000 → ikisi birden.

**Sitedeki etkiler — bot bunları bilmeli:**

- **Müfredat tamamen bu alanlardan türetiliyor.** `term === n` olan dersler n.
  yarıyıla yazılır; ama bir seçmeli grubun seçeneği olan ders yarıyıl satırı
  olarak **gösterilmez**, grubun içinde görünür. Yarıyıl ECTS toplamı satırların
  toplamıdır: yanlış `term` ya da `ects` resmi 240'ı bozar.
- **`bolognaLink` dolu ve `about` boşsa** ders dışarıya (Bologna'ya) bağlanır ve
  site haritasına girmez. Üniversite havuzundaki 150 ders bu durumda. Böyle bir
  derse `about` yazmak onu sitede kendi sayfası olan bir derse çevirir.
- Ders detayında değerlendirme `Vize %{midtermWeight} + Final %{finalWeight}`
  olarak basılıyor. Kaynakta final ağırlığı 0 ya da boşsa bu "girilmemiş"
  demektir; önceki içe aktarımda `100 - vize` olarak türetildi.
- Haftalık programda satır başına bir hafta, sitede hafta numarasıyla listelenir.
- Program ve istatistik sayfaları `type` ve `degreeLevels`'ı koda göre join
  ederek okuyor; lisansüstü program sayfası `MASTERS`/`DOCTORATE`'e göre süzüyor.

Canlıda 265 ders: 106 bölüm dersi, 150 üniversite seçmelisi, 9 lisansüstü.
`languages` ve İngilizce alanların hiçbiri dolu değil.

**İngilizce alanlar** (`nameEn`, `aboutEn`, `gradingPolicyEn`, `resourcesEn`,
`syllabus[].topicEn`): sitenin `/en` sayfaları bunları gösteriyor, boşsa
Türkçe karşılığına düşüyor. Kaynağı Bologna'nın aynı ders sayfası, `&lang=en`
ile (§6). Eski serbest metin `language` alanı kaldırıldı, yerine `languages`.

```json
{ "data": { "code": "MTM3512", "name": "Kompleks Analiz 1",
            "nameEn": "Complex Analysis 1", "languages": ["TURKISH", "ENGLISH"],
            "about": "Kompleks değişkenli fonksiyonlarla ilgili temel kavramlar, …",
            "degreeLevels": ["UNDERGRADUATE"], "type": "REQUIRED",
            "category": "CORE_PROFESSION", "term": 6, "semester": "SPRING",
            "syllabus": [ { "week": 1, "topic": "Kompleks fonksiyonlar ve elemanter fonksiyonlar" } ],
            "midtermWeight": 60, "finalWeight": 40,
            "theoryHours": 3, "practiceHours": 0, "labHours": 0, "ects": 5 } }
```

### 2.4 `elective-groups` — seçmeli slotları

Slug = küçük harfli slot kodu, düzenlenemez. Müfredattaki bir "seçmeli ders"
satırı ve öğrencinin oradan seçebileceği dersler.

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `code` | ShortText | Z | Bologna slot kodu, aşağıya bakın |
| `name` | ShortText | Z | `Mesleki Seçmeli 2`, `Üniversite Sosyal Seçmeli` ("Dersleri" eki yok) |
| `nameEn` | ShortText | | ≤255, ör. `Professional Elective 2` |
| `about` | LongText | | Grup notu; sitede grup açılınca bilgi kutusunda |
| `aboutEn` | LongText | | İngilizce grup notu |
| `term` | Number | | ≥1, müfredattaki yarıyıl |
| `semester` | Select | | `FALL` · `SPRING` · `SUMMER` |
| `degreeLevels` | StringArray | | Boşsa seçenek derslerden |
| `weeklyHours`, `localCredit`, `ects` | Number | | ≥0; boşsa site seçenekler tekdüzeyse onlardan türetir |
| `selectionCount` | Number | | ≥1, varsayılan 1 |
| `optionLectureIds` | StringArray | | Seçenek derslerin **`lectures` kimlikleri** (UUID) |
| `optionCount`, `options` | | S | Hesaplanır; `options` koda göre sıralı |

- **Slot kodu biçimi:** `MES{n}-{yıl}{G|B}` (G = Güz, B = Bahar), ör. `MES2-3G`
  = 3. yıl güz, Mesleki Seçmeli 2. Üniversite havuzları `USS-2G` (sosyal),
  `UMS-4G` (mesleki).
- **`optionLectureIds` her kayıtta listeyi baştan yazar.** Bir ders çıkarmak için
  listeden silinir; eksik gönderilen ders gruptan düşer. Dersler önce
  `lectures`'ta var olmalı; kod → kimlik eşlemesini çalışma anında yapın.
- Bir ders bir gruba eklendiğinde `type`'ı boşsa otomatik `ELECTIVE` olur.
- Havuz notu (`about`) canlıda iki grupta: *"Üniversite genelindeki sosyal
  seçmeli havuzundan alınır; dersler Matematik Mühendisliği bölümüne ait
  değildir."*

Canlıda 14 grup, 220 ders bağlantısı; MES grupları 5, USS 3, UMS 5 AKTS, hepsi
`3+0+0`.

```json
{ "data": { "code": "MES2-3G", "name": "Mesleki Seçmeli 2",
            "nameEn": "Professional Elective 2", "term": 5, "semester": "FALL",
            "selectionCount": 1,
            "optionLectureIds": ["6c69d970-015f-4c5d-aed8-d347254a52b9", "…"] } }
```

### 2.5 `lecture-offerings` — açılan dersler

Bir dersin belirli bir dönemde belirli bir grupla açılması. Yerelleştirilmemiş.
Slug = `{kod}-{yıl}-{DÖNEM}-{grup}` küçük harfle:
`mtm3512-2025-2026-spring-2`. Tekil anahtar `(lectureCode, academicYear,
semester, groupNumber)`.

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `lectureCode` | ShortText | Z | `lectures`'ta var olmalı, yoksa "Ders bulunamadı" |
| `academicYear` | ShortText | Z | `YYYY-YYYY` (§2.1 kuralı) |
| `semester` | Select | Z | `FALL` · `SPRING` · `SUMMER` |
| `groupNumber` | Number | Z | Şube numarası |
| `staffSlug` | Select | | `staff` kaydının slug'ı |
| `instructorRawName` | ShortText | | ≤255; hoca personelde yoksa ham ad |
| `language` | Select | | `TURKISH` · `ENGLISH` |
| `examWeights` | ObjectArray | | `[{examType, weightPercent 0–100}]`, aynı sınav türü iki kez olamaz |
| `scheduleSlots` | ObjectArray | | Haftalık ders saatleri, aşağıda |
| `lectureName`, `instructorName` | | S | Hesaplanır |

- **`staffSlug` ile `instructorRawName`'den en az biri dolu olmalı.**
- `scheduleSlots` öğesi: `dayOfWeek` (Z, `MONDAY`…`SUNDAY`), `startTime` (Z,
  `HH:mm`), `endTime` (Z), `classroom`, `online` (Bool). `online: true` ise
  derslik kaydedilmez. **Aynı derslik ya da aynı hoca aynı saatte çakışırsa kayıt
  reddedilir** ve çakışan satır bildirilir.
- Sitedeki ızgara 09:00'dan başlayan 50 dakikalık saatlerden oluşuyor: 3 saatlik
  bir ders `09:00`–`11:50` yazılır. `language: ENGLISH` kartta "İng", grup > 1
  "Gr.2" rozeti olarak görünür.
- **Harf sonuçları ve sınav istatistikleri bu koleksiyonda değil** (§3).

```json
{ "data": { "lectureCode": "MTM1512", "academicYear": "2025-2026",
            "semester": "SPRING", "groupNumber": 2, "staffSlug": "birol-aslanyurek",
            "language": "ENGLISH",
            "examWeights": [ { "examType": "MIDTERM_1", "weightPercent": 30 },
                             { "examType": "FINAL", "weightPercent": 40 } ],
            "scheduleSlots": [ { "dayOfWeek": "THURSDAY", "startTime": "09:00",
                                 "endTime": "10:50", "classroom": "KMB-329", "online": false } ] } }
```

### 2.6 `announcements` ve `news` — duyurular ve haberler

İkisi aynı yapıda; `news`'te ek olarak `sourceUrl` var. **Yerelleştirilmiş**
(`tr`, `en`): her yazma `?locale=tr` ister. Slug başlıktan otomatik üretilir,
düzenlenebilir; her dilin slug'ı kendi başlığından.

- **Duyuru:** operasyonel bildirim — sınav programı, mezuniyet, staj, form.
- **Haber:** bölümden haber ve etkinlik.

| Alan | Tür | | Kural / anlam |
| ---- | --- | - | ------------- |
| `title` | ShortText | Z | ≤255, aranabilir |
| `summary` | LongText | | Düz metin; listelerde ve aramada |
| `body` | RichText | | HTML, aşağıdaki etiketlerle |
| `coverImage` | Image | | `{src, alt}` |
| `gallery` | ObjectArray | | `[{image: {src, alt} (Z), caption}]`; gövdeye gömülen görseller buraya konmaz |
| `attachments` | ObjectArray | | `[{file: {url, name, mime, size}}]` |
| `publishedAt` | Date | | `YYYY-MM-DD`; sıralama ve filtre |
| `featured` | Bool | | Öne çıkan, listede en üstte |
| `tags` | StringArray | | Kategori kimlikleri, aşağıda |
| `sourceUrl` | Url | | **Yalnızca `news`**; haberin asıl kaynağı |

- **`attachments` biçimi değişti.** Eskiden düz `{url, name, type, size}` idi,
  şimdi her öğe bir `file` nesnesi taşıyor. `mime`'a uzantı (`pdf`) değil tam
  MIME türü (`application/pdf`) yazın.
- **`tags` yalnızca şu kimliklerle anlam taşır** (sitede filtre çipi ve renk):
  `sinav` (Sınav & Program), `mezuniyet`, `staj`, `kariyer`
  (Kariyer & Etkinlik), `genel`. Ana sayfada `sinav`, `mezuniyet`, `kariyer`
  gösteriliyor. Bunların dışındaki etiketler görünmez.
- **Listeleme sırası:** önce `featured`, sonra `publishedAt` azalan.
- **`body` HTML süzgeci.** Site şu etiketleri tutuyor, diğerlerini atıyor:
  `p br hr strong b em i u s del strike a ul ol li h2 h3 h4 blockquote pre code`.
  Bağlantılar yalnızca `http(s)://`, `/` ya da `mailto:`. Satır içi stil ve
  `<img>` kullanmayın; görseller `coverImage` ya da `gallery`'ye.
- **İngilizce çeviri:** önce `tr` kaydı oluşturulur, sonra
  `POST /cms/collections/{key}?locale=en&translationGroup=<tr kaydının grup
  kimliği>`. Grup kimliği kaydın üst seviyesindeki `translationGroupId`
  alanında. Çevirisi olmayan kayda İngilizce sayfa ve `hreflang` üretilmez.
  Bot çeviriyi aşama 9'da yapar (§4.7).

Silmeden önceki 45 duyuru ve 14 haber deneme verisiydi; hepsi silindi, bot boş
koleksiyona yazıyor.

```json
{ "data": { "title": "2025-2026 Güz Yarıyılı Final Sınav Programı",
            "summary": "Final sınav programı yayımlandı.",
            "body": "<p>Final sınav programı ekte yer almaktadır.</p>",
            "publishedAt": "2025-12-20", "featured": true, "tags": ["sinav"],
            "attachments": [ { "file": { "url": "https://matmuh.yusufacmaci.com/cdn/…/final.pdf",
                                         "name": "Final Sınav Programı",
                                         "mime": "application/pdf", "size": 404392 } } ] } }
```

---

## 3. Harf sonuçları ve sınav istatistikleri

`lecture-offerings` koleksiyonunda değiller, ayrı REST uçlarında. Sitede ders
detayının "Geçmiş İstatistikler" sekmesinde, **yalnızca giriş yapmış
kullanıcılara** gösteriliyor.

### 3.1 Toplu içe aktarma (önerilen)

```
POST /api/lecture-offerings/import      (ADMIN)
{ "rows": [ Row, … ] }
```

Her satır `(lectureCode, academicYear, semester, groupNumber)` anahtarıyla
**upsert** edilir; aynı dörtlü ikinci kez gelirse üzerine yazılır. Her satır kendi
işleminde koşar: bozuk bir satır diğerlerini geri almaz. Yanıt satır bazında
rapor verir: `{total, created, updated, failed, results: [{index, key, status,
offeringId, error, warnings}]}`.

```json
{ "lectureCode": "MTM3512", "academicYear": "2025-2026", "semester": "SPRING",
  "groupNumber": 2,
  "staffId": "981f4076-4f74-4c3d-bddd-8098c10e2e61",
  "instructorRawName": "Prof.Dr. Fatma İnci ALBAYRAK",
  "language": "TURKISH",
  "gradeResults": [ { "examPeriod": "NORMAL", "result": { …GradeResult… } },
                    { "examPeriod": "BUT",    "result": { …GradeResult… } } ],
  "examStatistics": [ { "examType": "MIDTERM_1", "statistic": { …ExamStatistic… } } ],
  "scheduleSlots": [ { "dayOfWeek": "WEDNESDAY", "startTime": "09:00",
                       "endTime": "11:50", "classroom": "KMB-203", "online": false } ] }
```

- Burada eğitmen **`staffId`** (personelin UUID'si), koleksiyondaki gibi
  `staffSlug` değil. `staffId` ya da `instructorRawName`'den biri zorunlu.
- `examPeriod`: `NORMAL` (dönem sonu) · `BUT` (bütünleme).

**Üzerine yazma davranışı — dikkat** (`LectureOfferingImportRowWriter`):

| Satırdaki alan | Gönderilmezse | Gönderilirse |
| -------------- | ------------- | ------------ |
| `scheduleSlots` | Dokunulmaz | Liste **yerine geçer**; listede olmayan mevcut saatler silinir. `[]` hepsini siler |
| `gradeResults` | Dokunulmaz | Gönderilen dönemin (`NORMAL`/`BUT`) sonucu ve harf dağılımı baştan yazılır |
| `examStatistics` | Dokunulmaz | Gönderilen sınav türlerinin istatistiği baştan yazılır |
| `language` | Dokunulmaz | Yazılır |
| `staffId`, `instructorRawName` | **Boşaltılır** | Yazılır |

- Eğitmen alanları **her satırda koşulsuz yazılıyor.** Aynı açılışa iki farklı
  kaynaktan (ders programı ve OBS) satır gönderen iki aşama birbirinin
  eğitmenini ezer. Bkz. §4.4, aşama 6.
- Yalnızca `ADMINISTRATIVE` grubundaki personel ders veremez; `staffId` olarak
  verilirse satır reddedilir.
- Toplu uçta **`examWeights` yok.** Sınav ağırlıkları yalnızca koleksiyon
  üzerinden (`PUT /cms/collections/lecture-offerings/{slug}`) yazılır.

### 3.2 Tek tek yazma

```
PUT /api/lecture-offerings/{offeringId}/grade-results/{NORMAL|BUT}   body: GradeResult
PUT /api/lecture-offerings/{offeringId}/exam-statistics/{examType}   body: ExamStatistic
```

### 3.3 GradeResult

| Alan | Tür | Kural |
| ---- | --- | ----- |
| `evaluationMethod` | enum | `RELATIVE` (bağıl) · `ABSOLUTE` (mutlak) · `MANUAL` |
| `resultStatus` | string | ör. `Sonuçlandırılmış` |
| `resultDate` | date | `YYYY-MM-DD` |
| `examCurriculumName` | string | ör. `Matematik Mühendisliği (İngilizce) Sınav Müfredatı` |
| `participantCount` | int | ≥0 |
| `classAverage` | decimal | |
| `classAverageParticipantCount` | int | |
| `standardDeviation` | decimal | |
| `classLevel` | string | ör. `İyi` |
| `rangesChanged` | bool | |
| `grades` | array | Harf dağılımı |

`grades[]`: `letterGrade` (zorunlu), `minScore` / `maxScore` (0–100,
**opsiyonel**, ikisi de varsa `min ≤ max`), `studentCount` (≥0).
Harfler `AA BA BB CB CC DC DD FD FF F0`; `F0` devamsızlık, aralığı yok. Site
dağılımı `minScore`'a göre azalan sıralıyor, aralıksızları sona koyuyor.

### 3.4 ExamStatistic

| Alan | Tür | Kural |
| ---- | --- | ----- |
| `weightPercent` | int | 0–100 |
| `announcedAt` | datetime | `2026-04-29T09:06:00` |
| `totalStudentCount` | int | **zorunlu**, ≥0 |
| `attendedStudentCount` | int | **zorunlu**, ≥0 |
| `failedByAbsenceCount` | int | ≥0 |
| `averageScore` | decimal | |

Sınav türleri (`examType`): `MIDTERM_1`, `MIDTERM_2`, `MIDTERM_1_MAKEUP`,
`MIDTERM_2_MAKEUP`, `FINAL`, `RESIT`, `QUIZ`, `QUIZ_2`, `ASSIGNMENT`,
`ASSIGNMENT_2`, `PROJECT`. Bir açılışta her tür bir kez. Sitede ad türden
üretiliyor, kaynaktaki ad saklanmıyor.

`QUIZ_2` ve `ASSIGNMENT_2` 2026-09-24'te backend'e eklendi: OBS'te aynı şubede
iki ayrı ödev olabiliyor ve ikisinin de kendi ağırlığı, giren sayısı ve
ortalaması var (2023-2024 Bahar MTM1552 Gr.1: "Ödev 1" %15, "Ödev2" %15;
2025-2026 Bahar MTM3662 Gr.1: "Ödev" %5, "Ödev2" %5). Öncesinde ikincisi
yazılamıyordu.

OBS'teki adlar bu türlere şöyle düşüyordu (aynı tür 6 farklı adla geçiyor):

| OBS adı | `examType` |
| ------- | ---------- |
| Vize, Ara Sınav, Ara Sınav 1, Ara Sınav I, 1. Vize | `MIDTERM_1` |
| Vize 2, Ara Sınav 2, Ara Sınav II | `MIDTERM_2` |
| Vize Mazeret, Vize I Mazeret, Ara Sınav Mazeret | `MIDTERM_1_MAKEUP` |
| Vize 2 Mazeret | `MIDTERM_2_MAKEUP` |
| Final, Yarıyıl Sonu Sınavı | `FINAL` |
| Bütünleme | `RESIT` |
| Kısa Sınav | `QUIZ` |
| Kısa Sınav 2 | `QUIZ_2` |
| Ödev, Ödev 1 | `ASSIGNMENT` |
| Ödev 2, Ödev2 | `ASSIGNMENT_2` |
| Proje | `PROJECT` |

"Ara Sınav Mazeret" hem 1. hem 2. vizenin mazereti için kullanılmış; bağlama göre
karar verin.

2026-09-23'te gönüllü dosyalarında şu adlar da görüldü: `Ara Sınav-1`,
`Ara Sınav1`, `Ara Sınav2`, `Vize2`, `Vize I`, `1. vize`, `Ödev 1`, `Ödev2`,
`Türkçe 1 Vize Sınavı`, `Türkçe 1 Dersi Final`. Bot adı sadeleştirip eşliyor:
"bütünleme" → `RESIT`, "final"/"yarıyıl sonu" → `FINAL`, "mazeret" → mazeret
türü, kalan "vize"/"ara sınav" → vize; adda `2` ya da `II` varsa ikinci vize.

### 3.5 Eğitmen eşleştirme

OBS ham adları (`Prof.Dr. Fatma İnci ALBAYRAK`) personel kaydına bağlanırken:

1. Unvanları at (`Prof`, `Doç`, `Dr`, `Öğr`, `Gör`, `Arş`, …).
2. Son kelimeyi soyad, ilk kelimenin ilk harfini ad baş harfi al.
3. Soyadı tek bir personelle eşleşiyorsa onu seç; birden fazlaysa ad baş
   harfiyle daralt. **Soyad tek başına yetmiyor:** `N. Güler` ve `C. Güler`
   ayrı kişiler.
4. Eşleşme yoksa `staffId` boş, ad `instructorRawName`'e.

Karşılaştırmayı Türkçe büyük harfe çevirerek yapın (`toLocaleUpperCase("tr")`).
Aksi halde İ/ı dönüşümleri eşleşmeyi bozar.

### 3.6 Atlanacak kayıtlar

Sonucu, sınavı ve eğitmeni olmayan tamamen boş açılışları yazmayın. Sitede boş
bir dönem seçeneği olarak görünürler, hiçbir şey göstermezler.

---

## 4. Aşamalı çalışma (Python + Scrapy)

Bot her şeyi tek seferde yazmaz. Her aşama tek bir veri kümesini ele alır,
biter ve **durur**. Sonraki aşama ancak kullanıcı raporu okuyup açıkça izin
verdiğinde başlar; her aşamanın sonucu böylece gözden geçirilir.

Kaynakların nereden ve nasıl çekileceği büyük ölçüde bu belgede **yok**; her
aşama geliştirilirken ayrıca öğretilecek. İncelenmiş kaynakların notları
ilgili aşama kartının altında (personel: aşama 2). Bu bölüm aşamaların sırasını, sözleşmesini
ve aralarındaki kapıyı tanımlar.

### 4.1 Her aşama üç fazdan oluşur

```
scrape  →  raw/<aşama>.json  →  normalize  →  data/<aşama>.json  →  push (dry-run)  →  push  →  rapor  →  DUR
(token gerekmez)                (Gemini, §4.8)                        (token gerekir)
```

1. **scrape** — Scrapy örümceği kaynağı çeker ve ham halini
   `raw/<aşama>.json`'a yazar. Backend'e hiç istek atmaz, token istemez,
   istenildiği kadar tekrar çalışabilir.
2. **normalize** — Ham veriyi kod ve Gemini ile §2'deki biçime getirir,
   `data/<aşama>.json`'a yazar (§4.8). Backend'e istek atmaz.
3. **push** — `data/<aşama>.json`'u okur, canlı şemaya göre doğrular, canlıdaki mevcut
   kayıtlarla karşılaştırır ve yazar. Token yalnızca burada gerekir.

Bu ayrım yazma yarıda kalırsa önemli: yeniden çekmeye gerek
kalmaz, yalnızca push tekrar çalışır ve doğal anahtarlar
sayesinde zaten yazılmış kayıtları atlar.

**push'un adımları:**

1. Anahtar dosyasını oku (§4.2).
2. Kimliği yokla (§1.1): servis anahtarında boş import 400 dönmeli; değilse
   dur.
3. İlgili koleksiyonların şemasını ve mevcut kayıtlarını oku.
4. Her kaydı sınıflandır: **oluşturulacak**, **güncellenecek**, **aynı**,
   **geçersiz** (şemaya uymuyor, nedeniyle), **çakışan** (kaynak ile canlı
   veri anlamlı biçimde farklı).
5. `--dry-run` ise bu raporu yaz ve dur.
6. Değilse yaz. Her 401/403'te **o anda dur**, ilerlemeyi kaydet, kullanıcıya
   haber ver.
7. Yazdıktan sonra canlıyı yeniden oku ve beklenenle karşılaştır.
8. `reports/<aşama>.md` yaz, durum dosyasını güncelle, **dur**.

### 4.2 Anahtarlar

- `secrets/cms.key`: `CONTENT_WRITE` servis anahtarı, tek satır. Uzun ömürlü,
  aşamalar arasında yenilenmez. Bot her push başında dosyadan okur.
- `secrets/gemini.key`: Google AI Studio anahtarı; normalize (§4.8) ve çeviri
  (§4.7) fazları için. Yoksa `GEMINI_API_KEY` ortam değişkeni.
- `secrets/` git'e girmez. Anahtarlar loglara, raporlara ya da hata
  mesajlarına yazılmaz.
- Anahtarın geçmediği uçlar (akademik dönem silme, ders notu yönetimi) admin
  JWT'si ister; bot bunları kullanmaz.

### 4.3 Kapı ve durum dosyası

`state.json` hangi aşamanın bittiğini tutar:

```json
{ "steps": {
    "1-academic-terms": { "status": "done", "finishedAt": "2026-09-21T14:05:00",
                          "created": 1, "updated": 0, "skipped": 1, "failed": 0,
                          "report": "reports/1-academic-terms.md" } } }
```

- Her aşama ayrı bir komutla başlar, örneğin `python bot.py push 4`.
- Bot, ön koşul aşamaları `done` değilse **başlamayı reddeder**.
- Aşama bitince kendiliğinden sonrakine geçmez. Kullanıcı raporu okur ve
  sonraki komutu çalıştırır (ya da çalıştırılmasına izin verir); izin budur.
- `failed > 0` ile biten aşama `done` sayılmaz, `partial` olarak işaretlenir.
- Kimlikler (ders kodu → UUID, e-posta → personel) durum dosyasında önbelleğe
  alınmaz, her aşamanın başında **canlıdan** okunur. Bu sayede aşamalar arasında
  editörün yaptığı değişiklikler de görülür.

### 4.4 Aşamalar

| # | Aşama | Yazdığı | Yöntem | Ön koşul |
| - | ----- | ------- | ------ | -------- |
| 0 | Hazırlık | — | Yalnızca okur | — |
| 1 | Akademik dönemler | `academic-terms` | Koleksiyon POST/PUT | 0 |
| 2 | Personel | `staff` | Koleksiyon POST/PUT, fotoğraf için `/cms/media` | 0 |
| 3 | Dersler | `lectures` | Koleksiyon POST/PUT | 0 |
| 4 | Seçmeli grupları | `elective-groups` | Koleksiyon POST/PUT | 3 |
| 5 | Dönem kayıtları ve ders saatleri | `lecture-offerings` | `POST /api/lecture-offerings/import` | 1, 2, 3 |
| 6 | Harf sonuçları ve sınav istatistikleri | `lecture-offerings` alt verisi | `POST /api/lecture-offerings/import` | 2, 3 |
| 7 | Duyurular | `announcements` | Koleksiyon POST, ekler için `/cms/media` | 0 |
| 8 | Haberler | `news` | Koleksiyon POST, görseller için `/cms/media` | 0 |
| 9 | Duyuru ve haber çevirisi | `announcements`, `news` (`en`) | Gemini API ile çeviri, koleksiyon POST `?locale=en&translationGroup=…` | 7, 8 |

Sınav ağırlıkları (`lecture-offerings.examWeights`) için ayrı bir aşama **yok**;
gerekçesi aşama 6 kartının altında.

Her aşamanın **kaynağı ve çekme yöntemi** geliştirilirken eklenecek; aşağıdaki
kartlar yalnızca sözleşmeyi verir.

**Aşama 0 — Hazırlık.** Servis anahtarını yoklar, yedi şemayı `schemas/` altına, yedi
koleksiyonun mevcut kayıtlarını `snapshots/` altına yazar. Hiçbir şey yazmaz.
Sonraki aşamaların karşılaştırması ve olası geri dönüş için referanstır.

**Aşama 1 — Akademik dönemler.** §2.1. Anahtar `academicYear` + `semester`.
Bitiş ölçütü: her dönem var ve tarih aralıkları çakışmıyor. Kaynakta olmayan
canlı dönemlere dokunulmaz.

- **Ne işe yarıyor:** ders programı sayfaları, personel programı, ders
  sayfasındaki şubeler ve kayıt çakışma kontrolü `/calendar/weekly`'yi dönem
  vermeden çağırıyor; backend **bugünü kapsayan** dönemin ders saatlerini
  döndürüyor. Kişisel takvim ders saatlerini yalnızca `startDate`–`endDate`
  arasındaki günlere açıyor. Bugünü kapsayan dönem yoksa bu sayfalar boş.
- **Tarihlerin anlamı** (canlı şemanın alan açıklaması): `startDate` derslerin
  ilk günü, `endDate` derslerin son günü. Final ve bütünleme dahil değil.
- **Kaynak:** YTÜ akademik takvimi Excel'i, `temp/`'e elle konur; dosya adı
  değişebilir, bot içeriğinden tanır (`bot.py inbox`). **Yalnızca görünür
  sayfalar** okunur: dosyada eski yılların takvimlerini taşıyan gizli sayfalar
  var.
- **Çıkarım Gemini'yle** (`run 1 normalize`): "… YARIYILI DERSLERİNİN
  BAŞLANGICI" ve "… DERSLERİNİN SON GÜNÜ" satırları, kanıt satırıyla.
  Kod doğrular: yıl belgenin yılı mı, başlangıç ayı yarıyıla uyuyor mu, süre
  makul mü, tarih ya da kanıt satırı belgede geçiyor mu. Tutmayan dönem
  yazılmaz, raporda "reddedildi".
- **Resmi tatiller** de çıkarılır ve `data/1-academic-terms.json`'da tutulur,
  ama yazılmaz: takvim etkinliği `/api/calendar-admin/events` ister, servis
  anahtarı geçmez.
- Rapor bugünü kapsayan dönemi gösterir; yoksa uyarır.

**Aşama 2 — Personel.** §2.2. Anahtar `email`, yoksa ad + soyad. Var olan
kayıtlarda **e-postayı değiştirmeyin** (sayfalar kişileri e-postayla buluyor).
Fotoğraflar önce `/cms/media`'ya yüklenir, dönen adres `photo.src`'ye yazılır.
Unvanları tek biçime getirin. `roleEn` ve `descriptionEn` §4.7'deki sözlükle
doldurulur. Rapor, kaynakta olup canlıda olmayanı ve canlıda
olup kaynakta olmayanı ayrı listeler; ikincileri **silmez**.

**Var olan kayıt güncellenir** (2026-09-23'te eklendi; önce yalnızca yeni kişi
oluşturuluyordu, unvan/görev değişiklikleri hiç yazılmıyordu). Botun sahiplendiği
alanlar: `firstName`, `lastName`, `groups`, `academicTitle`, `role`, `roleEn`,
`email`, `phone`, `office`, `avesisLink`. Kurallar:

- **Kaynakta boş olan alan canlıdakini silmez** — elle girilmiş telefon/oda
  kalır.
- `officeHours` ve `photo` botun alanı değil; güncellemede canlıdaki değer
  aynen korunur.
- PUT gövdesi canlı kaydın **yazılabilir alanlarının tamamı** artı değişiklikler
  (`matmuhbot/merge.py`): §1.4'teki "gönderilmeyen alan boşalır" kuralı geçerli
  olsa da olmasa da doğru sonuç verir. Hesaplanan alanlar (`fullName`,
  `rawName`, …) şemadan bakılıp ayıklanır.
- `groups`, `degreeLevels` gibi kümelerde sıra farkı değişiklik sayılmaz.

**Personel kaynağı: eski bölüm sitesi** (2026-09-22'de incelendi). Sayfaların
iskeleti tutarlı, alanların içi tutarsız; yapay zekâ gerekmez, kod yeter.
Yalnızca Yönetim sayfasında isteğe bağlı.

| Sayfa | Adres | Grup |
| ----- | ----- | ---- |
| Akademik personel | `/personel/{sayfa}/1` (26 kişi, 2 sayfa) | `ACADEMIC` |
| Öğretim ve araştırma görevlileri | `/personel/{sayfa}/2` (8 kişi) | `TEACHING_AND_RESEARCH` |
| İdari personel | `/personel/{sayfa}/3` (2 kişi) | `ADMINISTRATIVE` |
| Yönetim | `/sayfa/PERSONEL/Yönetim/118` | `MANAGEMENT` + `role` |
| Kişi sayfası | `/personel/1/<herhangi>/{id}` | — |

Sayfalar `https://mtm.yildiz.edu.tr` altında. Liste sayfası boş gelince
(`div.one-staff` yok) o grubun sonu. Kişi sayfasını yalnızca sondaki `{id}`
belirliyor; aradaki ad parçası önemsiz.

**Liste sayfaları.** Her kişi bir `div.one-staff` kutusunda: fotoğraf
(`img[src]`, göreli `/media/…`), ad bağlantısı (`strong > a`, sonunda `{id}`)
ve altında üç satır. Satır başlıkları sabit: `Tel No :`, `Oda No :`,
`E-posta adresi :`. Satırlar editörde yazılmış, iç içe `span`, `strong`,
`&nbsp;` içeriyor; önce düz metne çevirip başlığa göre bölün.

**Kişi sayfası listeden değerli**, asıl kaynak o olsun. Sayfada bir tablo var:
ad soyad, e-posta, web sayfası (**AVESİS**, `avesisLink` buradan), telefon,
oda, yerleşke. Liste yalnızca kişi `{id}`'lerini ve grubu toplamak için.

**Temizleme kuralları (kod):**

- Telefon yer tutucuları boş sayılır: `0212 383 -- --`, `0212 383 ....`,
  `0212 383 .. ..`, `0212 383 ----`.
- Birden fazla numara (`0212 383 46 03 - 0212 383 45 90`, tabloda boşlukla
  ayrılmış iki numara): ilki `phone`'a, gerisi rapora.
- Telefon biçimi `0212 383 46 03` (§2.2); tablodaki `0212 383 4603` buna
  çevrilir.
- Oda: `A- 231` → `A-231`; `--` → boş. Blok harfi olmayanlar (`108`,
  `106`, `109`, `205`) **olduğu gibi** kalır, harf eklenmez.
- Unvan: fazla boşluk atılır (`Prof. Dr.  Reşat`), `Prof.Dr.` →
  `Prof. Dr.`, `Araş. Gör.` ve `Arş. Gör.` tek biçime (§2.2'deki liste),
  `Araş. Gör. Dr.` gibi birleşikler listedeki karşılığına.
- E-postadaki `&nbsp;` ve boşluklar atılır, küçük harfe çevrilir.
- Fotoğraf: adres `https://mtm.yildiz.edu.tr` ile tamlanır, boşluklu ve
  Türkçe karakterli dosya adları (`SEDA GÖKTEPE.jpg`) URL-kodlanarak
  indirilir, sonra `/cms/media`'ya yüklenir. Adres `/media/images/personel/`
  gibi klasörde bitiyorsa fotoğraf yok.

**Ad ve soyad.** Kişi sayfasındaki tablodan alınır: unvandan sonraki
kelimelerden **tamamı büyük harf olanlar soyad**, gerisi ad. Listede
`Nilgün Güler BAYAZIT` yazan kişi tabloda `Nilgün GÜLER BAYAZIT`; tablo
doğru ayrımı veriyor. Ama tablonun yazımı her zaman doğru değil
(`Kadiriye`, ASCII `TEKERCIOGLU`): **soyadın nerede başladığı tablodan, harf
harf yazım listeden** alınır; kelime sayısı tutmazsa tablo alınır. Fark rapora.
Unvanda `Arş. Gör.` biçimi seçildi (sitenin unvan süzgeci ve İngilizce sözlüğü
bunu kullanıyor); `Araş. Gör. Dr.` → `Arş. Gör. Dr.`.

**Adlar düzeltilmez**, yapay zekâya da verilmez. Kaynakta şüpheli görünenler
var ve olduğu gibi yazılır, yalnızca raporda işaretlenir:

- `Yasemen UÇAN`: fotoğraf dosyası `yaseminucan`.
- `Hülya SERAB`: e-posta `hsahin`, fotoğraf `hulyasahinturk` (büyük olasılıkla
  soyadı değişmiş).
- `Kadriye Şimşek ALAN`: listede ikinci soyad küçük harfli; tabloya bakın.

**İdari personel (grup 3)** aynı kutuda ama farklı: ad bağlantısında unvan
yerine görev tanımı (`Büro Personeli Mehmet Ali ESKİN`,
`Bilgisayar İşletmeni Vedat ÇAKIR`), alt satırlarda başlıksız olarak birim
(`Öğrenci İşleri`, `Bölüm Sekreteri`) ve telefon (`0212-383 45 92`).
Görev tanımı `academicTitle`'a (canlıdaki gelenek, §2.2), birim `role`'e
yazılır (`Bölüm Sekreteri`, `Öğrenci İşleri`). İki kişi;
kural kodlanmadan elle de girilebilir.

**Yönetim sayfası** tek tutarsız kaynak: editörde elle yazılmış bir tablo
(iç içe `span`, `&nbsp;`, boş satırlar). Başlık "Bölüm Başkan Yardımcıları"
ama altında tek kişi var. Bu sayfadan yalnızca "görev → ad" alınır:

- Başlık satırı (`<strong>Bölüm Başkanı</strong>`,
  `<strong>Bölüm Başkan Yardımcıları</strong>`) ile altındaki ilk ad
  eşleşir. Kod bunu yapabilir; yapay zekâya verilirse çıktı yalnızca
  `[{role, name}]` olur.
- Ad, akademik listedeki kişiyle eşlenir (unvan atılıp ad + soyad), o kişiye
  `MANAGEMENT` grubu ve `role` eklenir. Yardımcı başlığı çoğul da olsa her
  kişinin `role`'ü tekil: `Bölüm Başkan Yardımcısı`.
- Eşleşmeyen ad rapora düşer, yeni kayıt açılmaz. Bu sayfadaki telefon ve
  e-posta kullanılmaz; kişinin kendi sayfasındaki geçerli.
- Sonuç raporda ayrıca listelenir; kullanıcı güncel olup olmadığını kontrol
  eder.

**Aşama 3 — Dersler.** §2.3. Anahtar `code`. Var olan kayıt güncellenirken PUT
gövdesi, aşama 2 ve 4 gibi, canlı kaydın **yazılabilir alanlarının tamamı** artı
değişenler (`matmuhbot/merge.py`). 2026-09-23'e kadar yalnızca değişen alanlar +
`code`/`name` gönderiliyordu; §1.4'teki "gönderilmeyen alan boşalır" kuralı
gerçekten geçerliyse bu her güncellemede içerik ve haftalık konuları silerdi.
`lectures`'ta okumada dönmeyen yazılabilir alan yok, ayıklanan hesaplanan
alanlar: `noteCount`, `staff`, `electiveGroupCount`, `statisticsTermCount`.
Kaynak Bologna: lisans
`program/view&id=222&aid=24`, yüksek lisans `program/view&id=181&aid=86`;
programdaki her dersin detay sayfası Türkçe ve `&lang=en` ile İngilizce çekilir
(~600 sayfa, ~12 dk).

- **Müfredattaki yer program sayfasından:** "N.Yıl - Güz/Bahar" satırları
  zorunlu ders (`term` = yarıyıl); `MES{n}-{yıl}{G|B}` yuvaları seçmeli
  seçeneklerine yarıyılını verir; USS/UMS havuzu `term`'siz; yüksek lisans
  dersleri `term`'siz. Güz/bahar müfredattaki yarıyıldan (ders sayfasıyla
  çelişirse müfredat esas). Yüksek lisans programındaki derslere `MASTERS`
  eklenir.
- **Alanlar ders sayfasından:** içerik (`Dersin İçeriği`), kaynaklar, haftalık
  konular, değerlendirme tablosu (`gradingPolicy` metni; toplam %100 ise
  `finalWeight` = Final satırı, `midtermWeight` = 100 − final), saatler, yerel
  kredi, AKTS, dil, kategori (Bologna yalnızca Temel Meslek / Genel Kültür /
  Uzmanlık-Alan kullanıyor).
- **İngilizce:** İngilizce sayfadan; alan boşsa, Türkçesiyle aynıysa ya da
  Türkçe görünüyorsa Gemini çevirir (kaynakçada kitap künyeleri korunur).
  Çeviriler `data/3-lectures.translations.json`'da önbellekte.
- **Havuz dersleri tüm ayrıntılarıyla yazılır** (`bot.json`
  `poolCourseDetails: true`, 2026-09-22 kararı): içeriği olan havuz dersi
  sitede kendi sayfasına kavuşur. Bologna'da içeriği boş olan dersler (16 havuz
  dersi, MTM4991 ve bazı yüksek lisans dersleri) Bologna'ya bağlanmaya devam
  eder.
- **Yüksek lisans yarıyılları** (2026-09-23): program sayfasındaki bölümlerden
  `term` = 1 (1. yıl güz), 2 (1. yıl bahar), 3 (2. yıl güz-bahar; `semester`
  boş). Sitede `/egitim/lisansustu-mufredat` bu üç dönemi gösteriyor.
- **Bilinen kısıt:** backend `ects` alanı tam sayı (`int`), 7.5 yazınca 7
  oluyor. Yüksek lisans derslerinin çoğu 7.5 AKTS; düzeltilene kadar
  lisansüstü müfredatın yarıyıl toplamları eksik görünür (30 yerine 28).
- Bitiş ölçütü: lisans müfredatının sekiz yarıyılı 29/31/29/31/30/30/29/31,
  toplam 240 AKTS.

**Aşama 4 — Seçmeli grupları.** §2.4. Anahtar `code`. Var olan grup da
güncellenir (2026-09-23'te eklendi): aşama 2'deki birleştirme kurallarıyla,
`code`, `name`, `nameEn`, `term`, `semester`, `degreeLevels`, `weeklyHours`,
`localCredit`, `ects`, `selectionCount` ve `optionLectureIds` alanları.
`about`/`aboutEn` elle tutulur, bot dokunmaz. **Dikkat:** okuma tarafında
`optionLectureIds` dönmüyor, yerine hesaplanmış `options` geliyor; karşılaştırma
ve PUT gövdesi id listesini `options[].id`'den üretir, yoksa her çalıştırmada
"değişmiş" görünür ve seçenekler silinebilir. Kaynak Aşama 3'ün ham
verisi (`raw/3-lectures.json`); ayrı çekme yok. Bologna program sayfasındaki
yuva satırları gruba, altlarındaki listeler seçeneklere karşılık gelir.

- **Lisans (14 grup):** `MES{n}-{yıl}{G|B}` yuvaları "Mesleki Seçmeli n"
  gruplarına, `USS-2G` ve `UMS-4G` üniversite havuzlarına. Grubun yarıyılı,
  AKTS'si, saati ve kredisi yuva satırından gelir.
- **Yüksek lisans (7 grup):** `SEC0001`–`SEC0007`, `degreeLevels: ["MASTERS"]`.
  Yedisinin de seçenek listesi aynı: programın "Seçmeli Dersler" bölümündeki
  39 ders. Yuvalar ayrı tutulur, çünkü müfredat sayfası yarıyıl AKTS'sini satır
  satır topluyor (1. yarıyılda 4, 2. yarıyılda 3 yuva).
- `optionLectureIds` canlıdaki `lectures` kayıtlarından koda göre çözülür;
  bulunamayan kod rapora düşer. Liste her yazmada baştan yazılır.
- Yazdıktan sonra bot canlıyı okuyup her grubun seçenek sayısını doğrular.
- **Sitedeki etkisi:** bir ders bir grubun seçeneğiyse müfredatta kendi satırı
  olarak değil, grubun içinde görünür (`getCurriculum`).

**Aşama 5 — Dönem kayıtları ve ders saatleri.** §2.5 ve §3.1. Kaynak, başka bir
botun OBS'ten çektiği JSON dosyaları: `temp/ingilizce-ders-programi.json` ve
`temp/turkce-ders-programi.json` (2026-09-23'te biçim doğrulandı).

- **Dosya biçimi:** `academicTerm` (ör. `{"value":"20261","name":"2026-2027 Güz"}`),
  `program`, `classLevels[].days[].lessons[]`. Her ders saati ayrı bir kayıt
  (50 dakika); bot aynı ders + grup + gün + derslik için ardışık saatleri tek
  `scheduleSlot`'ta birleştirir.
- **Satırlarda `gradeResults` ve `examStatistics` gönderilmez** (yoksa aşama
  6'nın verisine dokunur). `scheduleSlots` o açılışın **tüm** saatlerini
  içermeli, eksik saat silinir.
- **Eğitim dili** (2026-09-23'te bu sıraya çevrildi): dersin Bologna'daki
  `languages` alanı **tek dilliyse o dil** yazılır — servis dersleri İngilizce
  programın listesinde de görünüyor ama Türkçe veriliyor (ör. ATA1031, TDB).
  Ders çok dilliyse (MTM derslerinin çoğu) şubenin geçtiği dosyanın dili
  yazılır. Ders çok dilli ve şube iki dosyada da varsa alan **boş bırakılır**.
  Her sapma rapora düşer.
- **Çevrimiçi:** derslik adında `UZEM`, `SNL` ya da `ONLİNE` geçiyorsa `online: true` ve
  derslik yazılmaz. `SNL` sanal derslik: kapasitesi 999, aynı saatte birden çok
  ders barındırıyor. Bu ayrıca backend'in "aynı derslik aynı saatte" çakışma
  reddini de düşürüyor (ör. cumartesi 11 ayrı MTM4000 danışman grubu).
- **Derslik adı** fakülte önekinden arındırılır: `KMF KMB-202` → `KMB-202`.
- **Eğitmen eşleştirme** (§3.5 yerine): önce kod (ad + soyad; soyadın bir kısmı
  eksikse ve ad tutuyorsa tek aday kabul), kalanlar Gemini'ye sorulur. Sonuç
  `sources/instructor-map.json`'da tutulur: ham ad → personel slug'ı, kaynağı
  (`kod`/`ai`/`elle`) ve notu. **Elle düzeltilen satıra bot dokunmaz.**
  Eşleşmeyen hoca `instructorRawName` olarak yazılır — servis derslerinin
  hocaları ve bölüm dersine giren dışarıdan eğitmenler (ör. Canan Yağmur
  Karakaş, Fettah Kıran) bilinçli olarak böyle kalır.
- **Derslerde olmayan kodlar oluşturulur:** ders programında geçip `lectures`'ta
  bulunmayan dersler (2026-2027 Güz'de DNS1230, MDB2030, SBP3350) kod ve adla
  yazılır; Bologna'da genel ders arama ucu olmadığı için içerik ve
  `bolognaLink` boş kalır, rapora düşer.
- **Cumartesi ve erken saatler** sitede gösteriliyor (2026-09-23 frontend
  değişikliği): ızgara pazartesi–cumartesi, 08:00'den başlıyor; cumartesi
  sütunu yalnızca o gün ders varsa çiziliyor, boş saatler katlanıyor.
- Çakışma uyarıları (`warnings`) rapora aynen yazılır.

**Aşama 6 — Harf sonuçları ve sınav istatistikleri.** §3. Kaynak,
`temp/statistics/` klasörüne gönüllülerin bıraktığı JSON dosyaları. Klasördeki
**her** dosya okunur, uzantı şart değil; yeni dosya eklenip aşama yeniden
çalıştırıldığında yalnızca yeni veri eklenir. 2026-09-23'te iki dosya yazıldı
(`egehan-avcu.json`, `fatih-naz.json`); üçüncüsü `obs` biçimindeydi, şubesiz
olduğu için geri verildi.

- **İki biçim tanınır.** `bot`: `[{code, name, offerings: [{academicYear,
  semester, groupNumber, instructor: {rawName}, finalResult, butResult,
  examStatistics[]}]}]` — sözleşmeye neredeyse birebir uyuyor, bot yalnızca
  `gradeDistributions` → `grades` ve `finalResult`/`butResult` →
  `gradeResults[{examPeriod, result}]` dönüşümünü yapıyor; `absentStudentCount`
  ve sınavın kaynaktaki adı düşüyor (§3.4). `obs`: OBS ekranından ham çekilmiş
  `[{donem, dersKodu, ogretimElemani, harfNotlari, butunlemeHarfNotlari,
  sinavIstatistikleri, …}]` — sayılar Türkçe biçimde (`77,99`, `%40`,
  `64 (%91,43)`) geliyor, kodla çözülüyor.
- **`obs` biçiminde şube numarası yok**, oysa anahtarın parçası (§3.1). Bot
  yalnızca eğitmen adı, aynı ders ve dönem için bilinen tek bir şubenin
  eğitmeniyle birebir tutuyorsa yerleştiriyor. "Dönemde tek şube var"
  varsayımı **kullanılmıyor**: 2025-2026 Bahar'da MTM2592 ve MTM3582 bu
  varsayımla başka hocanın şubesine düşüyordu. Kalanlar rapora "şubesiz"
  yazılıyor; kaynak dosyaya `groupNumber` (ya da `grupNo`, `sube`…) eklenirse
  aşama yeniden çalıştırıldığında yazılır. 2026-09-23'te 31 kaydın 30'u bu
  yüzden yazılamıyordu ve dosya kaynağına geri verildi.
- **Aynı açılış birden çok dosyadaysa** alan alan birleştirilir: ilk dosya
  kazanır, eksik alanlarını sonrakiler tamamlar, aynı alan farklıysa rapora
  düşer.
- **Sınav adları** §3.4'teki türlere kodla eşlenir; eşleşmeyen ad yazılmaz,
  rapora düşer. Adda `2` ya da `II` varsa ikinci tür seçilir ("Ödev2" →
  `ASSIGNMENT_2`). Aynı türe yine de iki sınav düşerse önce ilan edilen kalır,
  diğerinin ağırlığı ve ortalaması rapora yazılır.
- Satırlarda **`scheduleSlots` gönderilmez** (`[]` değil, alanın kendisi yok).
- Eğitmen alanları her satırda üzerine yazıldığı için: açılış canlıda varsa
  **canlıdaki eğitmen aynen geri gönderilir**; kaynak başka birini söylüyorsa
  satır gönderilmez, rapora "çakışan" yazılır. Eşleme aşama 5'in
  `sources/instructor-map.json` dosyasını paylaşır.
- Sonucu ve sınavı olmayan boş açılışlar atlanır (§3.6). 2026-09-23'te 21 açılış
  bu yüzden atlandı ve aşama 5'in yazdığı dokuz açılışın hepsi bunların
  içindeydi: ders saatlerine ve eğitmenlere dokunulmadı.
- Burada geçip `lectures`'ta olmayan kodlar aşama 5 gibi kod ve adla oluşturulur
  (MTM1522, MTM1532).

**Sınav ağırlıkları için aşama yok** (2026-09-23'te kaldırıldı; önce 7. aşama
olarak planlanmıştı). `examWeights` toplu içe aktarma ucunda olmadığı için ayrı
bir aşama gerekiyordu, ama yazılacak bir şey kalmadı: (1) alan sitede hiçbir
yerde okunmuyor — ders detayındaki "Değerlendirme Sistemi" `lectures`
koleksiyonunun `midtermWeight`/`finalWeight` alanlarından geliyor (§2.3,
Bologna, aşama 3), yani dersin şubeden bağımsız resmî ağırlığı; (2) elimizdeki
tek şube bazlı ağırlık kaynağı aşama 6'nın verisi ve o yüzdeler zaten
`examStatistics.weightPercent` olarak yazıldı, istatistik sekmesi sınav başına
gösteriyor; (3) yürüyen dönemin (2026-2027 Güz, 235 açılış) ilan edilmiş
ağırlıkları için kaynak yok, ders programı dosyalarında geçmiyor.

Gerekirse şöyle yazılır: toplu uçta alan olmadığı için her açılış koleksiyondan
okunur, yalnızca `examWeights` değiştirilir, `version` ile PUT edilir (okunan
kaydın hesaplanan alanları ayıklanarak, §1.4). Anlamlı olacağı iki durum:
yürüyen dönemin ağırlıkları için bir kaynak bulunması ya da ders sayfasında
Bologna ağırlığının yanında dönem/şube bazlı ağırlığın gösterilmesine karar
verilmesi.

**Aşama 7 — Duyurular** ve **Aşama 8 — Haberler.** §2.6. `?locale=tr`. Aynı kod
(`s7_announcements.py`, haberler `kind="news"` ile). 2026-09-23'te yazıldı:
486 duyuru, 12 haber.

- **Anahtar eski sitedeki numara.** `state.json` o aşamanın adımında
  `slugs` eşlemesini tutar (numara → slug); tekrar çalıştırmada yalnızca
  eksikler oluşturulur.
- **Eşleme kaybolursa canlıdan geri kurulur** (2026-09-23'te eklendi).
  `state.json` gitignore'lu ve tek nüsha; kaybolsaydı her kayıt "eksik" sayılıp
  yeniden basılacaktı. Artık push, eşlemesi olmayan kayıtları canlıdakilerle
  başlık + yayın tarihinden eşleştiriyor; başlık iki tarafta da tekse tarih
  olmadan da eşleştiriyor. Eşleşme belirsizse kayıt **oluşturulmaz**, rapora
  "belirsiz" diye yazılır. Boş `state.json` ile denendiğinde 486 duyurunun 484'ü
  geri kuruldu, 0 tanesi çiftlenecekti; belirsiz kalan ikisi aynı gün aynı
  başlıkla girilmiş "İstatistik Dersi Hakkında" duyuruları.
- **CMS'te kasıtlı silinen kayıt geri gelir:** bot silmeyi bilmiyor, eksik
  gördüğünü yeniden oluşturur. Bir duyuruyu kalıcı kaldıracaksanız kaynak
  listeden de çıkarın ya da eşlemeyi koruyun.
- **Gövde kodla temizlenir** (`html_clean.py`), yapay zekâ kullanılmaz:
  `span`/`font`/satır içi stiller atılır, `div` gibi bloklar şeffaflaşır,
  yalnızca §2.6'daki etiketler kalır, `<img>` düşer. Metne dokunulmaz —
  kaynaktaki yazım hataları da olduğu gibi kalır.
- **Özet ve etiket Gemini'den** (10'arlı gruplar, `data/<n>-*.llm.json`'da
  önbellekte). Özet tek cümle, en çok 160 karakter; gövde başlıktan fazlasını
  söylemiyorsa boş bırakılır (486 duyurunun 113'ünde boş). Etiket beş
  kimlikten biri.
- **Ek dosyaları yalnızca eski bölüm sitesinden** (`mtm.yildiz.edu.tr/media/…`)
  indirilip `/cms/media`'ya yüklenir, gövdedeki bağlantı yeni adrese çevrilir.
  Başka YTÜ sitelerine giden bağlantılar (fbe, kmm, yildiz.edu.tr) ek sayılmaz,
  gövdede olduğu gibi kalır; bunların bir kısmı zaten 404.
- **İndirilemeyen ya da kabul edilmeyen ek kaydı engellemez**; bağlantı olduğu
  gibi bırakılır, rapora not düşer. `/cms/media` kabul ettiği türler: pdf, doc,
  docx, ppt, pptx, xls, xlsx, txt, png, jpg, jpeg, webp. **`.rtf` reddediliyor**
  (duyuru 762'nin eki bu yüzden eski sitede kaldı).
- **Gövdedeki görseller galeriye** yüklenir (`gallery[].image`), gövdeden
  çıkarılır. Haberlerde 30 görsel bu yolla taşındı.
- **Tarihler `sources/announcement-dates.json`'dan** (§4.7'nin altındaki tablo).
  Tarihsiz kayıt `publishedAt` gönderilmeden yazılır.

**Aşama 9 — Duyuru ve haber çevirisi.** §4.7. 2026-09-23'te yazıldı: 486
duyuru + 12 haber, hepsi tek seferde.

- **Kaynak canlıdaki `tr` kayıtları**, eski site değil; ayrı çekme yok.
- Çeviri Gemini'yle, 5'erli gruplar hâlinde; sonuçlar
  `data/9-translations.cache.json`'da içerik hash'iyle önbellekte. Türkçe
  kayıt değişirse hash değişir ve yeniden çevrilir.
- **Denetimler** (engelleyici): yapısal etiket sayıları (`p`, `li`, `a`, `h2`…),
  bağlantıların birebir aynılığı, ders ve derslik kodlarının korunması, galeri
  sayısı, başlık ≤255, Türkçesinde özet varsa İngilizcesinde de olması.
  Geçemeyen kayıt bir kez tek başına yeniden çevrilir; yine geçmezse yazılmaz.
- **Vurgu etiketleri** (`strong`, `u`, `em`…) engelleyici değil; farklıysa
  rapora not düşer. 498 kaydın 4'ünde böyle bir fark var.
- **Kopyalanan alanlar** (çevrilmez): `publishedAt`, `featured`, `tags`,
  `attachments`, `sourceUrl`, görsel adresleri. Çevrilen: başlık, özet, gövde,
  kapak ve galeri `alt` metinleri, galeri açıklamaları.
- Yazma: `POST /cms/collections/{key}?locale=en&translationGroup=<tr kaydın
  translationGroupId'si>`. Aynı gruba ikinci İngilizce kayıt açılmaz; bot
  başlarken `?locale=en` listesindeki grupları okur.
- İngilizce slug başlıktan üretilir ve Türkçesinden bağımsızdır; slug havuzu
  diller arasında ortak olduğu için çakışırsa `-2` eki alır.

### 4.5 Scrapy yapısı

Öneri, bağlayıcı değil:

```
bot/
  bot.py                 # komut satırı: scrape <n> | push <n> [--dry-run] | status
  secrets/               # cms.key, gemini.key; git'e girmez
  sources/               # elle hazırlanmış kaynaklar (duyuru tarihleri, Wayback önbelleği)
  tools/                 # yardımcı betikler
  state.json
  schemas/  snapshots/  raw/  data/  reports/
  matmuhbot/
    settings.py
    items.py             # §2'deki biçimler
    spiders/             # aşama başına bir örümcek, yalnızca kaynaktan okur
    api.py               # backend istemcisi: token okuma, yoklama, yeniden deneme
    validate.py          # canlı şemaya göre doğrulama
    push/                # aşama başına yazma mantığı
    llm.py               # Gemini istemcisi: anahtar, yeniden deneme, şema
    normalize/           # aşama başına düzenleme kuralları ve şema (§4.8)
    translate.py         # aşama 9: sözlük, çeviri, doğrulama
```

- **Örümcekler backend'e yazmaz.** Kaynaktan okuyup item üretir; item'lar
  `FEEDS` ile `data/<aşama>.json`'a düşer. Yazma, Scrapy dışında `requests`
  ile push fazında yapılır. Item pipeline içinden eşzamanlı HTTP yazmak
  Scrapy'nin olay döngüsünü bloklar ve token dolduğunda yarım kalan bir crawl
  bırakır.
- `FEED_EXPORT_ENCODING = "utf-8"` (Türkçe karakterler).
- Kaynak siteler üniversitenin; nazik olun: `ROBOTSTXT_OBEY = True`,
  `AUTOTHROTTLE_ENABLED = True`, `DOWNLOAD_DELAY` ≥ 1 sn, kendini tanıtan bir
  `USER_AGENT`.
- Geliştirirken `HTTPCACHE_ENABLED = True`: çekmeyi tekrar tekrar denerken
  kaynağa yeniden gitmez.
- Metinleri `strip()` edin, birden çok boşluğu teke indirin; Türkçe büyük harf
  dönüşümünde `upper()` yerine İ/ı'yı doğru çeviren bir yardımcı kullanın
  (`"i".upper()` → `"I"`, olması gereken `"İ"`).

### 4.6 Genel kurallar

1. **Şemayı her push'ta canlıdan okuyun** (§1.3) ve göndermeden önce
   doğrulayın.
2. **Tekrar çalıştırmaya güvenli olsun.** Doğal anahtarlar:

   | Koleksiyon | Anahtar |
   | ---------- | ------- |
   | `academic-terms` | `academicYear` + `semester` |
   | `staff` | `email`; yoksa `firstName` + `lastName` |
   | `lectures` | `code` (büyük/küçük harf duyarsız) |
   | `elective-groups` | `code` |
   | `lecture-offerings` | `lectureCode` + `academicYear` + `semester` + `groupNumber` |
   | `announcements`, `news` | Eski sitedeki numara (`state.json`'da numara → slug) |

3. **Editör düzenlemelerini ezmeyin.** Duyuru ve haberlerde başlığa göre eşleyip
   PUT atmak editörün o kayıttaki değişikliklerini orijinal metinle siler.
   Varsayılan davranış yalnızca eksik kayıt oluşturmak olsun; güncellemeyi
   alan bazında ve açık bir bayrakla yapın.
4. **Sessiz başarısızlık yok.** Başarısız her isteği sayın ve raporlayın;
   `if (!ok) continue` 401'leri "0 kayıt" gibi gösterir.
5. **5xx ve bağlantı zaman aşımında** kısa aralıklarla birkaç kez yeniden
   deneyin (backend zaman zaman 502/504 dönüyor), sonra durun.
6. **Silme yok.** Arşivleme (DELETE) geri alınabilir ama bot bunu yalnızca açık
   bir listeyle yapmalı.

### 4.7 İngilizce içerik

Eski bölüm sitesinin İngilizce bölümü (`https://mtm.yildiz.edu.tr/en`) 2020'den
beri güncellenmiyor: 9 duyuru ve 1 haber var, liste sayfası 500 dönüyor. Güncel
duyuru ve haberlerin İngilizce kaynağı yok. Her içerik türü İngilizcesini
başka bir yoldan alır:

| İçerik | Alanlar | Yol | Aşama |
| ------ | ------- | --- | ----- |
| Dersler | `nameEn`, `aboutEn`, `gradingPolicyEn`, `resourcesEn`, `syllabus[].topicEn` | Bologna, aynı sayfa `&lang=en` ile | 3 |
| Seçmeli grupları | `nameEn` | Sabit eşleme (aşağıda) | 4 |
| Seçmeli grupları | `aboutEn` | Bologna `&lang=en`; yoksa boş | 4 |
| Personel | `roleEn`, `officeHours[].descriptionEn` | Sözlük (aşağıda); sözlükte yoksa boş | 2 |
| Duyurular, haberler | Ayrı `en` kaydı | Gemini API (Google AI Studio) ile çeviri | 10 |

Boş bırakılan İngilizce alan sitede Türkçesine düşer; yanlış çeviri boş
alandan kötüdür. Ad, soyad, unvan, e-posta, ders kodu ve derslik çevrilmez
(unvanı site kendi sözlüğüyle çeviriyor).

**Seçmeli grup adları:**

| `name` | `nameEn` |
| ------ | -------- |
| `Mesleki Seçmeli {n}` | `Professional Elective {n}` |
| `Üniversite Sosyal Seçmeli` | `University Social Elective` |
| `Üniversite Mesleki Seçmeli` | `University Professional Elective` |

Bologna'nın İngilizce sayfası farklı bir ad veriyorsa rapora yazın, tabloyu
değiştirmeden önce sorun.

**Personel görevleri:**

| `role` | `roleEn` |
| ------ | -------- |
| `Bölüm Başkanı` | `Head of Department` |
| `Bölüm Başkan Yardımcısı` | `Deputy Head of Department` |
| `Anabilim Dalı Başkanı` | `Head of Division` |
| `Bölüm Sekreteri` | `Department Secretary` |

Sözlükte olmayan görev `roleEn`'siz yazılır ve raporda "çevrilmedi" diye
listelenir; kullanıcı tabloya ekler. `officeHours[].description` için de aynı
yol: `Ofis` → `Office`, `randevulu` → `by appointment`, `çevrimiçi` →
`online`; tanınmayan açıklama boş kalır.

#### Duyuru ve haber çevirisi (aşama 9)

**Fazlar.** §4.1'deki ayrım burada da geçerli:

```
scrape                       translate                         push
canlıdan tr kayıtları   →    Gemini API  →  data/10-en.json  →  en kayıtları yaz
(token gerekmez)             (GEMINI_API_KEY)                  (CMS token'ı)
```

Türkçe kaynak canlıdaki `tr` kayıtlarıdır, eski site değil: editörün
aşama 7/8'den sonra yaptığı düzeltmeler de çeviriye girer.

**Kapsam.** Canlıdaki **bütün** `tr` duyuru ve haberleri çevrilir, tarih
sınırı yok. `en` karşılığı olmayan her `tr` kaydı iş listesine girer.

**Model ve çağrı.**

- Sağlayıcı Google AI Studio (Gemini API), Python SDK'sı `google-genai`.
  Model adı kodda sabit yazılmaz, ayardan okunur (`GEMINI_MODEL`); AI Studio'daki
  güncel Pro sınıfı model önerilir. Flash daha ucuz ve hızlı ama resmî metinde
  terim ve ton hatası daha olası; seçmeden önce aynı 5 duyuruyu ikisiyle
  çevirip raporları karşılaştırın.
- Kayıt başına tek istek. Kayıt sayısı aşama 7/8'de ne kadar çekildiyse o
  kadar; eski sitede duyuru kimlikleri 1000'i geçiyor, yüzlerce kayıt
  olabilir. Translate fazı başta toplam kayıt sayısını ve tahmini istek
  sayısını yazar. Gemini'nin toplu (batch) modu daha ucuz ve istek
  sınırlarına takılmıyor; kayıt sayısı yüksekse o tercih edilir. Toplu mod
  kullanılırsa sonuçlar Türkçe kaydın `translationGroupId`'siyle eşlenir,
  sıraya güvenilmez.
- Translate fazı parça parça çalışabilir (`--limit N`): ücretsiz katmanın
  günlük sınırı dolarsa ertesi gün kaldığı yerden devam eder. Push fazı
  yalnızca çevirisi bitmiş ve doğrulamadan geçmiş kayıtları yazar.
- **Ücretsiz katman:** dakikalık ve günlük istek sınırları düşük. 429'da
  bekleyip yeniden deneyin (üstel geri çekilme), istekleri sırayla atın.
  Yarıda kalan çeviri `state.json` sayesinde kaldığı yerden devam eder.
  Ücretsiz katmanda gönderilen içerik Google'ın ürün geliştirmesinde
  kullanılabilir; duyuru ve haberler zaten herkese açık, ama bu faza kamuya
  açık olmayan hiçbir veri (e-posta listesi, öğrenci bilgisi) girmemeli.
- Çıktı **yapılandırılmış**: `response_mime_type="application/json"` ve
  aşağıdaki şema. Gemini JSON Schema'nın yalnızca bir alt kümesini kabul
  ediyor; SDK bir anahtarı reddederse (ör. `additionalProperties`) o anahtarı
  şemadan çıkarın. Şema tutsa bile gelen JSON'u bot kendi tarafında
  doğrular: alanlar tam mı, `gallery` uzunluğu Türkçedekiyle aynı mı.
- Kurallar ve sözlük `system_instruction`'a, kayda özgü her şey kullanıcı
  içeriğine. Sıcaklık düşük (`temperature` 0–0.3): çeviri yaratıcılık
  istemiyor.
- `GEMINI_API_KEY` ortam değişkeninden ya da `secrets/gemini.txt`'ten okunur;
  CMS token'ından ayrıdır, loglara yazılmaz, git'e girmez. Translate fazı CMS
  token'ı istemez.
- Yanıtın bitiş nedeni normal değilse (`MAX_TOKENS`, `SAFETY`, boş yanıt)
  ya da JSON ayrıştırılamıyorsa kayıt bir kez yeniden denenir, yine olmazsa
  "çevrilemedi" olarak rapora düşer, yazılmaz.

**Çevrilen alanlar ve şema:**

```json
{ "type": "object", "additionalProperties": false,
  "required": ["title", "summary", "body", "coverAlt", "gallery"],
  "properties": {
    "title":    { "type": "string" },
    "summary":  { "type": "string" },
    "body":     { "type": "string" },
    "coverAlt": { "type": "string" },
    "gallery":  { "type": "array", "items": {
                  "type": "object", "additionalProperties": false,
                  "required": ["alt", "caption"],
                  "properties": { "alt": { "type": "string" },
                                  "caption": { "type": "string" } } } } } }
```

Türkçede boş olan alan İngilizcede de boş dizge döner. `gallery` Türkçedekiyle
aynı sırada ve aynı uzunlukta olmalı.

**Kopyalanan alanlar** (çevrilmez, Türkçe kayıttan aynen): `publishedAt`,
`featured`, `tags`, `coverImage.src`, `gallery[].image.src`, `attachments`
(dosya adları dahil; ekler Türkçe kalır), `sourceUrl`.

**`system_instruction` kuralları:**

1. Yıldız Teknik Üniversitesi Matematik Mühendisliği Bölümü'nün resmî
   duyurusunu İngilizceye çeviriyorsun. Resmî, sade üniversite İngilizcesi;
   özetleme, ekleme yapma, anlamı değiştirme.
2. `body` HTML'dir. Etiketlere ve özniteliklerine dokunma, yeni etiket ekleme,
   yalnızca etiketler arasındaki metni çevir. `href` değerleri aynen kalır.
3. Çevirme, aynen bırak: URL, e-posta, telefon, ders kodu (`MTM1011`),
   derslik ve ofis (`D-105`, `A-219`), kişi adları, unvan kısaltmaları
   (`Prof. Dr.`), form ve belge adları tırnak içindeyse.
4. Tarihler: `12.03.2026` → `12 March 2026`; saat `14.00` → `14:00`.
5. Sözlük (her zaman bu karşılıklar):

| Türkçe | İngilizce |
| ------ | --------- |
| Matematik Mühendisliği Bölümü | Department of Mathematical Engineering |
| Yıldız Teknik Üniversitesi, YTÜ | Yildiz Technical University, YTU |
| Fen-Edebiyat Fakültesi | Faculty of Arts and Sciences |
| vize, ara sınav | midterm exam |
| final, yarıyıl sonu sınavı | final exam |
| bütünleme | make-up exam |
| mazeret sınavı | excuse exam |
| tek ders sınavı | single-course exam |
| ders kaydı | course registration |
| ekle-bırak | add-drop period |
| danışman | academic advisor |
| staj | internship |
| bitirme çalışması | graduation project |
| yarıyıl, dönem | semester |
| güz / bahar / yaz | fall / spring / summer |
| AKTS | ECTS |
| lisansüstü, yüksek lisans, doktora | graduate, master's, PhD |
| kontenjan | quota |
| çift ana dal, yan dal | double major, minor |
| Öğrenci İşleri | Student Affairs |

Sözlük kod içinde tek yerde tutulur; kullanıcı ekledikçe büyür. Sözlük
değişince daha önce çevrilmiş kayıtlar kendiliğinden yeniden çevrilmez.

**Doğrulama** (yazmadan önce, her kayıt için):

- `body`'deki etiket dizisi (ad + sıra, `href` dahil) Türkçedekiyle birebir
  aynı. Değilse bir kez yeniden çevir, yine tutmazsa atla ve raporla.
- Türkçedeki her URL, e-posta ve ders kodu İngilizcede de geçiyor.
- `title` ≤255; boş değil.
- Metinde Türkçeye özgü harf (`ğ ş ı İ`) kalmışsa uyarı (ad olabilir, atlama
  sebebi değil).

**Yazma.**

```
POST /cms/collections/{key}?locale=en&translationGroup=<tr kaydının translationGroupId'si>
{ "data": { …çevrilen + kopyalanan alanlar… } }
```

- Doğrudan yayınlanır. CMS'deki taslaklar kullanıcıya özel
  (`/cms/collections/{key}/drafts` yalnızca yazanın görebildiği tek bir
  taslak tutuyor), editör botun taslağını göremez; gözden geçirme rapor
  üzerinden yapılır.
- Aynı gruba ikinci `en` kaydı açmayın: yazmadan önce
  `?locale=en` listesinde aynı `translationGroupId` var mı bakın.
- `--dry-run` hiçbir şey yazmaz, yalnızca raporu üretir. Kullanıcı raporu
  okuyup onaylamadan gerçek push yapılmaz.

**Durum ve tekrar çalıştırma.** `state.json`'da her grup için:

```json
{ "translations": {
    "<translationGroupId>": { "key": "announcements", "trSlug": "…", "enSlug": "…",
                              "sourceHash": "sha256(tr title+summary+body+alt/caption)",
                              "translatedAt": "…", "status": "written" } } }
```

- `sourceHash` değişmediyse kayıt yeniden çevrilmez.
- Türkçe kayıt değiştiyse (hash farklı) İngilizce kayıt **otomatik
  güncellenmez**; rapora "Türkçesi değişti" diye düşer. `--update-changed`
  bayrağıyla yeniden çevrilir ve `version` ile PUT edilir. Editörün İngilizce
  kayda elle yaptığı düzeltmeler böylece korunur.
- `en` kaydı canlıda varsa ama state'te yoksa (editör elle yazmış) dokunulmaz.

**Rapor** `reports/9-translate.md`: her kayıt için Türkçe ve İngilizce başlık
ve özet yan yana, gövdenin ilk paragrafı, doğrulama uyarıları; en sonda
atlananlar ve "Türkçesi değişti" listesi.

### 4.8 Yapay zekâyla düzenleme (normalize)

Kaynaklar karışık ve tutarsız; bot çektiği ham veriyi yazmadan önce Gemini'den
geçirir. Bu bir **düzenleme** adımıdır, içerik üretme adımı değil. Temel ilke:
**kaynakta olmayan bilgi veriye girmez.**

**Akış.** Ham veri ve düzenlenmiş veri ayrı dosyalarda tutulur:

```
scrape  →  raw/<aşama>.json  →  normalize  →  data/<aşama>.json  →  push …
           (kaynağın aynısı)    (Gemini)       (§2 biçiminde)
```

- `raw/` kaynağın ham halidir (HTML parçası, metin, kaynak URL'si); asla
  düzenlenmez. Normalize istenildiği kadar yeniden çalışabilir, kaynağa
  tekrar gitmez.
- Normalize CMS token'ı istemez; `GEMINI_API_KEY` ister (§4.7'deki ayarlar,
  sınırlar ve hata kuralları burada da geçerli).
- Her kayıt için ham verinin hash'i saklanır; ham veri değişmediyse kayıt
  yeniden gönderilmez.

**Kod mu, yapay zekâ mı.** Kesin ve biçimi belli olanı kod çıkarır;
yapay zekâ yalnızca serbest metni düzenler ve sınıflandırır.

| Kod çıkarır, yapay zekâya bırakılmaz | Yapay zekâ düzenleyebilir |
| ------------------------------------ | ------------------------- |
| Ders kodu, AKTS, kredi, saat, ağırlık yüzdeleri, yarıyıl | Ders içeriği, değerlendirme ve kaynak metinlerinin temizlenmesi |
| Tarihler (`publishedAt`, dönem tarihleri) | Duyuru/haber gövdesinin izinli HTML'e (§2.6) çevrilmesi |
| E-posta, telefon, ofis, URL, dosya adresleri | `summary` yoksa gövdeden 1–2 cümlelik özet |
| Kaynak kimlikleri, harf dağılımları, sınav istatistikleri | `tags`: beş kimlikten seçim (§2.6) |
| Bologna slot kodları, seçenek ders listeleri | Unvanın listedeki biçime getirilmesi, ad/soyad ayrımı ve soyadın Türkçe büyük harfi |
| | Başlıktaki gereksiz büyük harf, fazla boşluk, bozuk karakterin düzeltilmesi |

Sayı ve kodlar modele hiç verilmez ya da verilse bile çıktısından alınmaz; kod
çıkardığı değeri doğrudan `data/`'ya yazar.

**Yapay zekânın kuralları** (`system_instruction`):

1. Yalnızca verilen metni düzenle. Bilgi ekleme, tahmin etme, eksik alanı
   doldurma. Emin olmadığın alanı boş bırak.
2. Anlamı değiştirme, kısaltma. Duyuru ve haber gövdesinde her cümle korunur;
   yalnızca biçim (HTML, boşluk, yazım hatası) düzelir.
3. Türkçe kalır; bu adım çeviri yapmaz (çeviri aşama 9'da).
4. HTML yalnızca §2.6'daki etiketlerle. `<img>`, satır içi stil, `<div>`,
   `<span>`, `<font>`, tablo düzeni atılır; içerikteki metin kalır.
   Bağlantılar ve `href`'ler aynen kalır.
5. Sınıflandırma alanları (`tags`, `groups`) yalnızca verilen kimliklerden.

**Çıktı.** Her aşama için ayrı JSON şeması (`response_mime_type` ile), şema
§2'deki alanların yalnızca yapay zekâya bırakılanları. Her kayıtta ek olarak:

```json
{ "fields": { … }, "uncertain": ["tags", "summary"], "notes": "Tarih metinde iki farklı biçimde geçiyor." }
```

`uncertain` listesindeki alanlar yazılır ama raporda ayrıca işaretlenir;
kullanıcı isterse push'tan önce düzeltir.

**Doğrulama** (normalize sonrası, kodla):

- Ham metindeki her URL, e-posta ve ders kodu çıktıda da var.
- Gövde metninin uzunluğu hamdakinin belirgin biçimde altına düşmemiş
  (ör. %70'in altı uyarı): içerik atılmış olabilir.
- HTML yalnızca izinli etiketlerden oluşuyor.
- `tags`, `groups`, `academicTitle` izinli değerlerden.
- Tutmayan kayıt bir kez yeniden denenir; yine tutmazsa `data/`'ya girmez,
  raporda "düzenlenemedi" olarak ham haliyle listelenir.

**Rapor** `reports/<aşama>-normalize.md`: kaç kayıt düzenlendi, hangileri
`uncertain`, hangileri doğrulamadan kaldı. Duyuru ve haberlerde her kaydın ham
ve düzenlenmiş başlığı yan yana.

---

## 5. Bilinen veri sorunları

Bot düzeltebilir ya da en azından bozmamalı:

| Sorun | Ayrıntı |
| ----- | ------- |
| Unvan tutarsızlığı | `Araş. Gör.` ve `Arş. Gör.` ikisi birden kullanılıyor |
| Eksik `evaluationMethod` | ITB3130 (NORMAL, BUT) ve MDB1031 (NORMAL): kaynakta `MANUAL`, o zaman enum'da yoktu, alan gönderilmedi. Artık yazılabilir |
| MTM3582 eğitmeni | OBS: Fatma İnci Albayrak, eski elle yazılmış program: C. Güler. Not verisi taşıyan açılışta OBS'teki duruyor; bölüme sorulmadan değiştirmeyin |
| TDB1032 | Eski program 1. sınıfta gösteriyordu, müfredat `term=4` |
| Dönem tarihleri | `2025-2026 SPRING` aralığı tahmini |
| Boş alanlar | Silmeden önce: `lectures.language` 159 derste, `nameEn`/`aboutEn` hepsinde, `staff.photo` ve `officeHours` hepsinde boştu |

---

## 6. Kaynaklar

| Veri | Kaynak |
| ---- | ------ |
| Ders içeriği, haftalık program, AKTS, saatler, ağırlıklar, tür, kategori | YTÜ Bologna — program: `https://bologna.yildiz.edu.tr/index.php?r=program/view&id=37&aid=24`, ders: `…?r=course/view&id=<bolognaId>&aid=24&pid=37`. Adrese `&lang=en` eklenince aynı sayfanın İngilizcesi dönüyor (2026-09-21'de doğrulandı): `nameEn`, `aboutEn` ve diğer İngilizce alanlar aynı aşamada buradan çekilir |
| Seçmeli slotları ve seçenekleri | Aynı Bologna program sayfası |
| Personel listesi, unvan, e-posta, telefon, oda, fotoğraf, yönetim görevleri | Eski bölüm sitesi `https://mtm.yildiz.edu.tr/personel/{sayfa}/{grup}` ve kişi sayfaları; ayrıntı §4.4 aşama 2 |
| AVESİS bağlantısı | Kişi sayfasındaki tablo; profil: `https://avesis.yildiz.edu.tr/<kullanıcı>` |
| Harf dağılımları, sınav istatistikleri | OBS |
| Haftalık ders programı | Bölümün yayımladığı ders programı |
| Akademik dönem tarihleri | YTÜ akademik takvimi |
| Duyuru ve haberler | Bölümün eski sitesi `https://mtm.yildiz.edu.tr` (dosyalar `/media/…` altında); ayrıntı §4.4 aşama 7 ve 8. Yayın tarihleri `sources/announcement-dates.json`'da. İngilizce bölümü (`/en`) 2020'den beri güncel değil; çeviri için kaynak alınmaz (§4.7) |

**Referans veri olarak frontend git geçmişi.** Göç öncesi yerel tablolar
silindi ama geçmişte duruyor; frontend deposunda
(<https://github.com/ytumatmuh/matmuh-frontend>) şu komutlarla okunur:

```bash
git show 8b1357f~1:src/data/coursesData.js          # 125 ders, 8 yarıyıl, 14 slot
git show 8b1357f~1:src/data/universityElectives.js  # 150 havuz dersi + bolognaId
git show 8b1357f~1:src/data/obsdata.js              # OBS istatistikleri
git show 8b1357f~1:src/data/staff.js                # 41 personel girdisi
git show 19b8434~1:src/data/contentData.js          # ~57 duyuru
git show 435da1d~1:src/data/scheduleData.js         # 2025-2026 Bahar programı
```

Dikkat: `coursesData.js` ve `obsdata.js`'in geçmişteki hali **temizlenmemiş**
sürüm. İçe aktarmadan önce 19 bozuk ders (türü boş ya da hiçbir yarıyıla bağlı
değil) ve tekrarlanan sınav türleri ayıklanmıştı; temiz hali yalnızca
veritabanında. Bu dosyalardan bir şey yeniden yüklenecekse aynı ayıklama
gerekir.
