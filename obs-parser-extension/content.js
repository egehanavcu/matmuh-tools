const script = document.createElement("script");
script.src = chrome.runtime.getURL("inject.js");
script.onload = function () {
  this.remove();
};
(document.head || document.documentElement).appendChild(script);

if (
  window === window.top &&
  window.location.href.startsWith(
    "https://obs.yildiz.edu.tr/oibs/std/index.aspx",
  )
) {
  const baslatButonu = document.createElement("button");
  baslatButonu.id = "obs-scrape-btn";
  baslatButonu.innerText = "📊 İstatistikleri İndir";

  Object.assign(baslatButonu.style, {
    position: "fixed",
    bottom: "30px",
    right: "30px",
    zIndex: "999999",
    padding: "15px 25px",
    backgroundColor: "#1D3557",
    color: "#ffffff",
    border: "none",
    borderRadius: "50px",
    boxShadow: "0 4px 10px rgba(0,0,0,0.3)",
    cursor: "pointer",
    fontFamily: "'Open Sans', Tahoma, sans-serif",
    fontSize: "14px",
    fontWeight: "bold",
    transition: "all 0.3s ease",
  });

  baslatButonu.onmouseover = () =>
    (baslatButonu.style.transform = "scale(1.05)");
  baslatButonu.onmouseout = () => (baslatButonu.style.transform = "scale(1)");

  baslatButonu.addEventListener("click", () => {
    baslatButonu.innerText = "⏳ Veriler Toplanıyor...";
    baslatButonu.style.backgroundColor = "#E63946";
    baslatButonu.style.cursor = "wait";
    baslatButonu.disabled = true;

    window.postMessage({ type: "START_SCRAPING" }, "*");
  });

  document.body.appendChild(baslatButonu);
}

let dersVerileri = {};
let eslesmeyenSinavAdlari = new Set();

const SEMESTER_MAP = { Güz: "FALL", Bahar: "SPRING", Yaz: "SUMMER" };
const EVALUATION_METHOD_MAP = {
  Bağıl: "RELATIVE",
  Mutlak: "ABSOLUTE",
  Manuel: "MANUAL",
};

const EXAM_TYPES = [
  [
    /mazeret/i,
    [
      [/\b(2|II)\b/, "MIDTERM_2_MAKEUP"],
      [/.*/, "MIDTERM_1_MAKEUP"],
    ],
  ],
  [/bütünleme|butunleme/i, [[/.*/, "RESIT"]]],
  [/final|yarıyıl sonu|yılsonu/i, [[/.*/, "FINAL"]]],
  [
    /ara\s*sınav|arasınav|vize/i,
    [
      [/2|II/, "MIDTERM_2"],
      [/.*/, "MIDTERM_1"],
    ],
  ],
  [/kısa\s*sınav|quiz/i, [[/.*/, "QUIZ"]]],
  [/ödev|odev/i, [[/.*/, "ASSIGNMENT"]]],
  [/proje/i, [[/.*/, "PROJECT"]]],
];

function examTypeOf(name) {
  for (const [matcher, variants] of EXAM_TYPES) {
    if (!matcher.test(name)) continue;
    for (const [variant, type] of variants) {
      if (variant.test(name)) return type;
    }
  }
  return null;
}

// OBS bazı derslerde ikinci ara sınavın mazeretini de birinciyle aynı adla
// ("Vize Mazeret") gösteriyor; isimden examType ayırt edilemiyor. Aynı tür
// tabloda ikinci kez çıkarsa (aynı ad, aynı regex sonucu), sıradaki muadiline
// kaydırıyoruz — tablodaki sıra, ilgili sınavların sırasını yansıtıyor.
const EXAM_TYPE_ESLESI = {
  MIDTERM_1: "MIDTERM_2",
  MIDTERM_1_MAKEUP: "MIDTERM_2_MAKEUP",
};

function benzersizTurAta(ad, kullanilanTurler) {
  let tur = examTypeOf(ad);
  if (!tur) return null;

  while (kullanilanTurler.has(tur)) {
    const esi = EXAM_TYPE_ESLESI[tur];
    if (!esi || kullanilanTurler.has(esi)) {
      return null;
    }
    tur = esi;
  }

  return tur;
}

function trSayi(metin) {
  if (!metin) return null;
  const temiz = metin.trim().replace(/\./g, "").replace(",", ".");
  const sayi = parseFloat(temiz);
  return isNaN(sayi) ? null : sayi;
}

function trTamsayi(metin) {
  if (!metin) return null;
  const temiz = metin.trim().replace(/\./g, "");
  const eslesme = temiz.match(/^(\d+)/);
  if (!eslesme) return null;
  const sayi = parseInt(eslesme[1], 10);
  return isNaN(sayi) ? null : sayi;
}

function trTarihToISO(dmy) {
  const eslesme = (dmy || "").trim().match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
  if (!eslesme) return null;
  const [, gun, ay, yil] = eslesme;
  return `${yil}-${ay}-${gun}`;
}

function donemCozumle(donemAd) {
  const eslesme = (donemAd || "").trim().match(/^(\d{4}-\d{4})\s+(.+)$/);
  if (!eslesme) {
    return { academicYear: donemAd || "", semester: null };
  }
  const academicYear = eslesme[1];
  const mevsim = eslesme[2].trim();
  return { academicYear, semester: SEMESTER_MAP[mevsim] || null };
}

function instructorOlustur(rawName) {
  if (!rawName) return undefined;
  return { rawName };
}

function harfAraliklariCozumle(doc, tabloId) {
  const tablo = doc.getElementById(tabloId);
  if (!tablo) return [];

  const satirlar = Array.from(tablo.querySelectorAll("tr")).slice(1);
  const dagilim = [];

  satirlar.forEach((satir) => {
    const hucreler = satir.querySelectorAll("td");
    if (hucreler.length < 4) return;

    const letterGrade = hucreler[0].textContent.trim();
    if (!/^([A-Z]{2}|F0)$/.test(letterGrade)) return;

    const minScore = trSayi(hucreler[1].textContent.trim());
    const maxScore = trSayi(hucreler[2].textContent.trim());

    let studentCount = null;
    for (let i = hucreler.length - 1; i >= 0; i--) {
      const metin = hucreler[i].textContent.trim();
      if (/^\d+$/.test(metin)) {
        studentCount = parseInt(metin, 10);
        break;
      }
    }

    dagilim.push({
      letterGrade,
      minScore,
      maxScore,
      studentCount: studentCount === null ? 0 : studentCount,
    });
  });

  return dagilim;
}

function tabloKV(doc, tabloId) {
  const tablo = doc.getElementById(tabloId);
  const kv = {};
  if (!tablo) return kv;

  tablo.querySelectorAll("tr").forEach((satir) => {
    const hucreler = satir.querySelectorAll("td");
    if (hucreler.length === 2) {
      const baslik = hucreler[0].textContent.replace(/\s+/g, " ").trim();
      const deger = hucreler[1].textContent.replace(/\s+/g, " ").trim();
      if (baslik) kv[baslik] = deger;
    }
  });

  return kv;
}

function sonucCozumle(doc, istTabloId, notlarTabloId) {
  const kv = tabloKV(doc, istTabloId);
  if (Object.keys(kv).length === 0) return undefined;

  const sonuc = {};

  const degerlendirme = kv["Değerlendirme Şekli"];
  if (degerlendirme) {
    sonuc.evaluationMethod = EVALUATION_METHOD_MAP[degerlendirme] || degerlendirme;
  }

  const durum = kv["Sonuç Durumu"];
  if (durum) sonuc.resultStatus = durum;

  const tarih = trTarihToISO(kv["Sonuç Durum Tarihi"]);
  if (tarih) sonuc.resultDate = tarih;

  const mufredat = kv["Sınav Müfredat Adı"];
  if (mufredat) sonuc.examCurriculumName = mufredat;

  const katilimci = trTamsayi(kv["Sınava Katılan Öğrenci Sayısı"]);
  if (katilimci !== null) sonuc.participantCount = katilimci;

  const sinifOrt = trSayi(kv["Sınıf Ortalaması"]);
  if (sinifOrt !== null) sonuc.classAverage = sinifOrt;

  const sinifOrtKatilimci = trTamsayi(kv["Sınıf Ort.Katılan Öğr.Sayısı"]);
  if (sinifOrtKatilimci !== null) {
    sonuc.classAverageParticipantCount = sinifOrtKatilimci;
  }

  const stdSapma = trSayi(kv["Standart Sapma"]);
  if (stdSapma !== null) sonuc.standardDeviation = stdSapma;

  const sinifDuzeyi = kv["Sınıf Düzeyi"];
  if (sinifDuzeyi) {
    sonuc.classLevel = sinifDuzeyi.replace(/\s*\[.*?\]\s*$/, "").trim();
  }

  const araliklarDegisti = kv["Harf Aralıkları Değiştirildi"];
  if (araliklarDegisti) {
    sonuc.rangesChanged = araliklarDegisti === "Evet";
  }

  const gradeDistributions = harfAraliklariCozumle(doc, notlarTabloId);
  if (gradeDistributions.length > 0) {
    sonuc.gradeDistributions = gradeDistributions;
  }

  return Object.keys(sonuc).length > 0 ? sonuc : undefined;
}

function sinavlariCozumle(doc, tabloId) {
  const tablo = doc.getElementById(tabloId);
  if (!tablo) return [];

  const sinavlar = [];
  const kullanilanTurler = new Set();
  let aktif = null;

  tablo.querySelectorAll("tr").forEach((satir) => {
    const hucreler = satir.querySelectorAll("td");
    if (hucreler.length < 2) return;

    const baslikHucresi = hucreler[0];
    const deger = hucreler[1].textContent.trim();
    const bEtiketi = baslikHucresi.querySelector("b");

    if (bEtiketi) {
      const ad = bEtiketi.textContent.trim();
      aktif = {};

      const examType = benzersizTurAta(ad, kullanilanTurler);
      if (examType) {
        kullanilanTurler.add(examType);
        aktif.examType = examType;
      } else {
        eslesmeyenSinavAdlari.add(ad);
      }
      aktif.name = ad;

      const hucreMetni = baslikHucresi.textContent;
      const yuzdeEslesme = hucreMetni.match(/\(%(\d+)\)/);
      if (yuzdeEslesme) aktif.weightPercent = parseInt(yuzdeEslesme[1], 10);

      const ilanEslesme = hucreMetni.match(
        /İlan Edildi:(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})/,
      );
      if (ilanEslesme) {
        const isoTarih = trTarihToISO(ilanEslesme[1]);
        if (isoTarih) aktif.announcedAt = `${isoTarih}T${ilanEslesme[2]}:00`;
      }

      sinavlar.push(aktif);
    } else if (aktif) {
      const metrikAdi = baslikHucresi.textContent.trim();
      if (metrikAdi === "Sınav listesinde yer alan toplam öğrenci sayısı") {
        const sayi = trTamsayi(deger);
        if (sayi !== null) aktif.totalStudentCount = sayi;
      } else if (metrikAdi === "Sınava giren öğrenci sayısı") {
        const sayi = trTamsayi(deger);
        if (sayi !== null) aktif.attendedStudentCount = sayi;
      } else if (metrikAdi === "Sınava girmeyen öğrenci sayısı") {
        const sayi = trTamsayi(deger);
        if (sayi !== null) aktif.absentStudentCount = sayi;
      } else if (
        metrikAdi === "Sınavda Devamsızlıktan Kaldı seçilen öğrenci sayısı"
      ) {
        const sayi = trTamsayi(deger);
        if (sayi !== null) aktif.failedByAbsenceCount = sayi;
      } else if (metrikAdi === "Sınava giren öğrencilerin not ortalaması") {
        const sayi = trSayi(deger);
        if (sayi !== null) aktif.averageScore = sayi;
      }
    }
  });

  return sinavlar;
}

window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data) return;

  if (event.data.type === "START_SCRAPING") {
    dersVerileri = {};
    eslesmeyenSinavAdlari = new Set();
    return;
  }

  if (event.data.type === "OBS_STAT_RESPONSE") {
    const htmlString = event.data.data;
    const donemAd = event.data.donemAd;
    const subeNo = event.data.subeNo;

    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlString, "text/html");

    let code = "";
    let name = "";
    let profName = "";

    const dersTablosu = doc.getElementById("grdDers");
    if (dersTablosu) {
      dersTablosu.querySelectorAll("tr").forEach((satir) => {
        const hucreler = satir.querySelectorAll("td");
        if (hucreler.length === 2) {
          const baslik = hucreler[0].textContent.trim();
          const deger = hucreler[1].textContent.trim();
          if (baslik === "Ders Kodu") code = deger;
          if (baslik === "Ders Adı") name = deger;
          if (baslik === "Öğretim Elemanı") profName = deger;
        }
      });
    }

    if (!code) {
      console.warn("⚠️ Ders kodu bulunamadı, bu kayıt atlanıyor.");
      return;
    }

    const { academicYear, semester } = donemCozumle(donemAd);
    const groupNumber = trTamsayi(subeNo);

    const offering = { academicYear, semester };
    if (groupNumber !== null) offering.groupNumber = groupNumber;

    const instructor = instructorOlustur(profName);
    if (instructor) offering.instructor = instructor;

    const finalResult = sonucCozumle(doc, "grdIst", "grdNotlar");
    if (finalResult) offering.finalResult = finalResult;

    const butResult = sonucCozumle(doc, "grdIstBut", "grdNotlarBut");
    if (butResult) offering.butResult = butResult;

    const examStatistics = sinavlariCozumle(doc, "grdIstSnv");
    if (examStatistics.length > 0) offering.examStatistics = examStatistics;

    if (!dersVerileri[code]) {
      dersVerileri[code] = { code, name, offerings: [] };
    }
    dersVerileri[code].offerings.push(offering);

    console.log(`✅ [${donemAd}] ${code} (Şb ${subeNo}) eklendi.`);
  }

  if (
    event.data.type === "OBS_SCRAPE_COMPLETE" ||
    event.data.type === "OBS_SCRAPE_ERROR"
  ) {
    const tumVeriler = Object.values(dersVerileri);

    if (eslesmeyenSinavAdlari.size > 0) {
      console.warn(
        "⚠️ examType eşleşmesi bulunamayan sınav adları (sadece 'name' ile kaydedildi):",
        Array.from(eslesmeyenSinavAdlari).join(", "),
      );
    }

    if (tumVeriler.length > 0) {
      const jsonString = JSON.stringify(tumVeriler, null, 4);
      const blob = new Blob([jsonString], { type: "application/json" });
      const url = URL.createObjectURL(blob);

      const a = document.createElement("a");
      a.href = url;
      a.download = "info.json";
      document.body.appendChild(a);
      a.click();

      setTimeout(() => {
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
      }, 0);
    }

    if (window === window.top) {
      const baslatButonu = document.getElementById("obs-scrape-btn");
      if (baslatButonu) {
        if (event.data.type === "OBS_SCRAPE_ERROR") {
          baslatButonu.innerText = "❌ Hata Oluştu";
          baslatButonu.style.backgroundColor = "#E63946";
        } else {
          baslatButonu.innerText = "✅ Başarıyla İndirildi!";
          baslatButonu.style.backgroundColor = "#2A9D8F";
        }

        baslatButonu.style.cursor = "pointer";

        setTimeout(() => {
          baslatButonu.innerText = "📊 İstatistikleri İndir";
          baslatButonu.style.backgroundColor = "#1D3557";
          baslatButonu.disabled = false;
        }, 3000);
      }
    }
  }
});
