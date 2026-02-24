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

let tumVeriler = [];

window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data) return;

  if (event.data.type === "OBS_STAT_RESPONSE") {
    const htmlString = event.data.data;
    const index = event.data.index;
    const donemAd = event.data.donemAd;

    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlString, "text/html");

    const istatistikVerisi = {
      donem: donemAd,
      fakulteProgram: "",
      dersKodu: "",
      dersAdi: "",
      ogretimElemani: "",
      harfNotlari: {},
      butunlemeHarfNotlari: {},
      finalStandartSapma: null,
      butunlemeStandartSapma: null,
      sinavIstatistikleri: {},
    };

    const dersTablosu = doc.getElementById("grdDers");
    if (dersTablosu) {
      const satirlar = dersTablosu.querySelectorAll("tr");
      satirlar.forEach((satir) => {
        const hucreler = satir.querySelectorAll("td");
        if (hucreler.length === 2) {
          const baslik = hucreler[0].textContent.trim();
          const deger = hucreler[1].textContent.trim();
          if (baslik === "Fakülte Program")
            istatistikVerisi.fakulteProgram = deger;
          if (baslik === "Ders Kodu") istatistikVerisi.dersKodu = deger;
          if (baslik === "Ders Adı") istatistikVerisi.dersAdi = deger;
          if (baslik === "Öğretim Elemanı")
            istatistikVerisi.ogretimElemani = deger;
        }
      });
    }

    const harfTablosuCozumle = (tabloId, hedefObje) => {
      const tablo = doc.getElementById(tabloId);
      if (!tablo) return;

      const satirlar = tablo.querySelectorAll("tr");
      let hbnBasIndex = -1;
      let hbnBitIndex = -1;

      if (satirlar.length > 0) {
        const basliklar = satirlar[0].querySelectorAll("th, td");
        basliklar.forEach((hucre, idx) => {
          const text = hucre.textContent.trim();
          if (text === "Hbn Baş.") hbnBasIndex = idx;
          if (text === "Hbn Bit.") hbnBitIndex = idx;
        });
      }

      satirlar.forEach((satir, i) => {
        if (i > 0) {
          const hucreler = satir.querySelectorAll("td");
          if (hucreler.length >= 4) {
            const harf = hucreler[0].textContent.trim();

            if (/^[A-Z]{2}|F0$/.test(harf)) {
              const ogrSayisiHucresi = hucreler[hucreler.length - 2];

              const veri = {
                baslangic: hucreler[1] ? hucreler[1].textContent.trim() : "",
                bitis: hucreler[2] ? hucreler[2].textContent.trim() : "",
                ogrenciSayisi: ogrSayisiHucresi
                  ? ogrSayisiHucresi.textContent.trim()
                  : "",
              };

              if (hbnBasIndex !== -1 && hucreler[hbnBasIndex]) {
                veri.hbnBaslangic = hucreler[hbnBasIndex].textContent.trim();
              }
              if (hbnBitIndex !== -1 && hucreler[hbnBitIndex]) {
                veri.hbnBitis = hucreler[hbnBitIndex].textContent.trim();
              }

              hedefObje[harf] = veri;
            }
          }
        }
      });
    };

    harfTablosuCozumle("grdNotlar", istatistikVerisi.harfNotlari);
    harfTablosuCozumle("grdNotlarBut", istatistikVerisi.butunlemeHarfNotlari);

    const finalIstTablosu = doc.getElementById("grdIst");
    if (finalIstTablosu) {
      const satirlar = finalIstTablosu.querySelectorAll("tr");
      satirlar.forEach((satir) => {
        const hucreler = satir.querySelectorAll("td");
        if (hucreler.length === 2) {
          const baslik = hucreler[0].textContent.replace(/\n/g, "").trim();
          if (baslik === "Standart Sapma") {
            istatistikVerisi.finalStandartSapma =
              hucreler[1].textContent.trim();
          }
        }
      });
    }

    const butIstTablosu = doc.getElementById("grdIstBut");
    if (butIstTablosu) {
      const satirlar = butIstTablosu.querySelectorAll("tr");
      satirlar.forEach((satir) => {
        const hucreler = satir.querySelectorAll("td");
        if (hucreler.length === 2) {
          const baslik = hucreler[0].textContent.replace(/\n/g, "").trim();
          if (baslik === "Standart Sapma") {
            istatistikVerisi.butunlemeStandartSapma =
              hucreler[1].textContent.trim();
          }
        }
      });
    }

    const sinavTablosu = doc.getElementById("grdIstSnv");
    if (sinavTablosu) {
      let aktifSinav = null;
      const satirlar = sinavTablosu.querySelectorAll("tr");

      satirlar.forEach((satir) => {
        const hucreler = satir.querySelectorAll("td");
        if (hucreler.length >= 2) {
          const baslikHucresi = hucreler[0];
          const deger = hucreler[1].textContent.trim();

          const bEtiketi = baslikHucresi.querySelector("b");
          if (bEtiketi) {
            aktifSinav = bEtiketi.textContent.trim();
            istatistikVerisi.sinavIstatistikleri[aktifSinav] = {};

            const hucreMetni = baslikHucresi.textContent;
            const yuzdeEslesme = hucreMetni.match(/\((%\d+)\)/);
            if (yuzdeEslesme && yuzdeEslesme[1]) {
              istatistikVerisi.sinavIstatistikleri[aktifSinav]["Etki Yüzdesi"] =
                yuzdeEslesme[1];
            }
          } else if (aktifSinav) {
            const metrikAdi = baslikHucresi.textContent.trim();
            if (metrikAdi) {
              istatistikVerisi.sinavIstatistikleri[aktifSinav][metrikAdi] =
                deger;
            }
          }
        }
      });
    }

    const hasHarfNotlari = Object.keys(istatistikVerisi.harfNotlari).length > 0;
    const hasSinavIstatistikleri =
      Object.keys(istatistikVerisi.sinavIstatistikleri).length > 0;

    if (hasHarfNotlari || hasSinavIstatistikleri) {
      tumVeriler.push(istatistikVerisi);
      console.log(
        `✅ [${istatistikVerisi.donem}] ${istatistikVerisi.dersAdi} eklendi. ${hbnBasIndex !== -1 ? "(HBN Tespit Edildi)" : ""}`,
      );
    }
  }

  if (
    event.data.type === "OBS_SCRAPE_COMPLETE" ||
    event.data.type === "OBS_SCRAPE_ERROR"
  ) {
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
