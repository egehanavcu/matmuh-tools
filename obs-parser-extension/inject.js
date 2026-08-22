(function () {
  window.addEventListener("message", function (event) {
    if (event.data && event.data.type === "START_SCRAPING") {
      window.baslat();
    }
  });

  const FORM_ID = "form1";
  const SCRIPT_MANAGER_ID = "ScriptManager1";
  const UPDATE_PANEL_ID = "UpdatePanel1";
  const ISTATISTIK_URL =
    "https://obs.yildiz.edu.tr/oibs/acd/new_not_giris_istatistik.aspx";

  // Sayfanın form alanlarının "canlı" durumu. Başlangıçta gerçek DOM'dan
  // okunur, her async postback'ten sonra sunucunun döndürdüğü hiddenField
  // bloklarıyla güncellenir. Bir daha DOM'a dokunulmaz.
  let formAlanlari = new Map();
  let sayfaUrl = "";

  // Microsoft AJAX UpdatePanel'in "delta" yanıtı, uzunluk öncelikli
  // bloklardan oluşur: "<uzunluk>|<tip>|<id>|<içerik>|" ...
  function deltaYanitiCozumle(metin) {
    const bloklar = [];
    let i = 0;
    const n = metin.length;

    while (i < n) {
      const uzunlukSonu = metin.indexOf("|", i);
      if (uzunlukSonu === -1) break;
      const uzunluk = parseInt(metin.slice(i, uzunlukSonu), 10);
      if (isNaN(uzunluk)) break;

      const tipSonu = metin.indexOf("|", uzunlukSonu + 1);
      if (tipSonu === -1) break;
      const tip = metin.slice(uzunlukSonu + 1, tipSonu);

      const idSonu = metin.indexOf("|", tipSonu + 1);
      if (idSonu === -1) break;
      const id = metin.slice(tipSonu + 1, idSonu);

      const icerikBaslangic = idSonu + 1;
      const icerik = metin.substr(icerikBaslangic, uzunluk);

      bloklar.push({ tip, id, icerik });

      i = icerikBaslangic + uzunluk;
      if (metin[i] === "|") i++;
    }

    return bloklar;
  }

  function hiddenAlanlariGuncelle(bloklar) {
    bloklar.forEach((blok) => {
      if (blok.tip === "hiddenField" && blok.id) {
        formAlanlari.set(blok.id, blok.icerik);
      }
    });
  }

  // __doPostBack(eventTarget, eventArgument)'ın gerçek DOM/iframe üzerinden
  // değil, doğrudan fetch ile taklit edilmiş hali. Sunucu isteği tamamen
  // işleyip yanıtı döndürene kadar await ile beklenir; sabit bir süre
  // tahmini yoktur.
  async function asyncPostback(eventTarget, eventArgument) {
    const govde = new URLSearchParams();
    for (const [ad, deger] of formAlanlari) {
      govde.set(ad, deger);
    }
    govde.set("__EVENTTARGET", eventTarget);
    govde.set("__EVENTARGUMENT", eventArgument || "");
    govde.set(SCRIPT_MANAGER_ID, `${UPDATE_PANEL_ID}|${eventTarget}`);
    govde.set("__ASYNCPOST", "true");

    const yanit = await fetch(sayfaUrl, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-MicrosoftAjax": "Delta=true",
        "Cache-Control": "no-cache",
        Pragma: "no-cache",
      },
      body: govde.toString(),
    });

    const metin = await yanit.text();
    const bloklar = deltaYanitiCozumle(metin);

    if (bloklar.some((b) => b.tip === "pageRedirect")) {
      throw new Error(
        "Sunucu oturumu sonlandırıp yönlendirme istedi (muhtemelen oturum süresi doldu).",
      );
    }

    hiddenAlanlariGuncelle(bloklar);
    return bloklar;
  }

  function dersKoduOku(doc) {
    const dersTablosu = doc.getElementById("grdDers");
    if (!dersTablosu) return "";
    let kod = "";
    dersTablosu.querySelectorAll("tr").forEach((satir) => {
      const hucreler = satir.querySelectorAll("td");
      if (hucreler.length === 2) {
        const baslik = hucreler[0].textContent.trim();
        if (baslik === "Ders Kodu") kod = hucreler[1].textContent.trim();
      }
    });
    return kod;
  }

  // İstatistik butonunun postback'ini atar, ardından istatistik sayfasını
  // çeker. Gelen sayfadaki Ders Kodu, tıklanan satırın beklenen ders
  // koduyla eşleşmezse (sunucu bir önceki seçimin verisini döndürmüş
  // olabilir) bir kez daha dener; yine tutmazsa null döner ve kayıt
  // yazılmaz.
  async function istatistikGetirVeDogrula(
    beklenenDersKodu,
    eventTarget,
    eventArgument,
    maxDeneme = 2,
  ) {
    for (let deneme = 1; deneme <= maxDeneme; deneme++) {
      await asyncPostback(eventTarget, eventArgument);

      const statRes = await fetch(ISTATISTIK_URL, { credentials: "include" });
      const htmlData = await statRes.text();

      const dogrulamaDoc = new DOMParser().parseFromString(
        htmlData,
        "text/html",
      );
      const gelenDersKodu = dersKoduOku(dogrulamaDoc);

      if (!beklenenDersKodu || gelenDersKodu === beklenenDersKodu) {
        return htmlData;
      }

      console.warn(
        `⚠️ Kimlik uyuşmazlığı: beklenen "${beklenenDersKodu}", gelen "${gelenDersKodu}" ` +
          `(deneme ${deneme}/${maxDeneme}).` +
          (deneme < maxDeneme
            ? " Yeniden deneniyor..."
            : " Bu kayıt atlanıyor."),
      );
    }

    return null;
  }

  window.baslat = async function () {
    console.log("🚀 Otomatik Tarama Başlatılıyor...");

    try {
      let linkler = Array.from(window.top.document.querySelectorAll("a"));
      let notListesiLink = linkler.find(
        (a) => a.textContent.trim() === "Not Listesi",
      );

      if (notListesiLink) {
        console.log("📂 'Not Listesi' menüsü bulundu, açılıyor...");
        notListesiLink.click();

        console.log("⏳ Not Listesi sayfasının yüklenmesi bekleniyor...");
        await new Promise((resolve) => setTimeout(resolve, 4000));
      } else {
        console.log(
          "⚠️ 'Not Listesi' menüsü bulunamadı. Zaten açık olduğu varsayılıyor.",
        );
      }
    } catch (e) {
      console.warn("Menü araması sırasında bir hata oluştu:", e);
    }

    let aktifPencere = window.top;
    let aktifDokuman = window.top.document;
    let donemSelect = aktifDokuman.getElementById("cmbDonemler");

    if (!donemSelect) {
      const iframes = aktifDokuman.querySelectorAll("iframe, frame");
      for (let iframe of iframes) {
        try {
          const icDokuman =
            iframe.contentDocument || iframe.contentWindow.document;
          donemSelect = icDokuman.getElementById("cmbDonemler");
          if (donemSelect) {
            aktifPencere = iframe.contentWindow;
            aktifDokuman = icDokuman;
            break;
          }
        } catch (e) {}
      }
    }

    if (!donemSelect) {
      console.error(
        "❌ Dönem seçici (cmbDonemler) bulunamadı! Sayfanın tam yüklendiğinden emin olun.",
      );
      window.postMessage({ type: "OBS_SCRAPE_ERROR" }, "*");
      return;
    }

    const donemler = Array.from(donemSelect.options).map((opt) => ({
      text: opt.textContent.trim(),
      value: opt.value,
    }));

    console.log(
      `Toplam ${donemler.length} adet dönem bulundu. Sırayla taranacak.`,
    );

    // Bu noktadan sonra iframe/DOM'a bir daha dokunulmuyor: form durumu
    // canlı sayfadan tek seferlik okunuyor, gerisi fetch ile yürütülüyor.
    const form = aktifDokuman.getElementById(FORM_ID) || aktifDokuman.forms[0];
    if (!form) {
      console.error("❌ Sayfa formu (form1) bulunamadı.");
      window.postMessage({ type: "OBS_SCRAPE_ERROR" }, "*");
      return;
    }

    formAlanlari = new Map();
    for (const [ad, deger] of new FormData(form).entries()) {
      formAlanlari.set(ad, typeof deger === "string" ? deger : "");
    }
    sayfaUrl = aktifDokuman.location.href;

    try {
      for (let d = 0; d < donemler.length; d++) {
        const donem = donemler[d];
        console.log(`\n=================================================`);
        console.log(
          `📅 DÖNEM DEĞİŞTİRİLİYOR: ${donem.text} (${d + 1}/${donemler.length})`,
        );
        console.log(`=================================================`);

        formAlanlari.set("cmbDonemler", donem.value);
        const donemBloklari = await asyncPostback("cmbDonemler", "");

        const panel = donemBloklari.find(
          (b) => b.tip === "updatePanel" && b.id === UPDATE_PANEL_ID,
        );
        if (!panel) {
          console.warn(
            `⚠️ ${donem.text} için tablo güncellemesi alınamadı, bu dönem atlanıyor.`,
          );
          continue;
        }

        const parcaDoc = new DOMParser().parseFromString(
          panel.icerik,
          "text/html",
        );
        const butonlar = Array.from(
          parcaDoc.querySelectorAll(
            'a[id^="grd_not_listesi_btnIstatistik_"]',
          ),
        );

        console.log(
          `Bu dönemde ${butonlar.length} adet istatistik butonu bulundu.`,
        );

        for (const buton of butonlar) {
          const href = buton.getAttribute("href");
          if (!href || !href.includes("__doPostBack")) continue;

          const params = href.match(/'([^']*)'/g);
          if (!params || params.length < 2) continue;

          const eventTarget = params[0].replace(/'/g, "");
          const eventArgument = params[1].replace(/'/g, "");

          const satir = buton.closest("tr");
          const ilkHucre = satir ? satir.querySelector("td") : null;
          const subeNo = ilkHucre ? ilkHucre.textContent.trim() : "";
          const dersKoduSpani = satir
            ? satir.querySelector('span[id^="grd_not_listesi_lblDersKod_"]')
            : null;
          const beklenenDersKodu = dersKoduSpani
            ? dersKoduSpani.textContent.trim()
            : "";

          try {
            const htmlData = await istatistikGetirVeDogrula(
              beklenenDersKodu,
              eventTarget,
              eventArgument,
            );

            if (!htmlData) {
              console.error(
                `❌ ${beklenenDersKodu || "(bilinmeyen ders)"} (Şb ${subeNo}) ` +
                  `için istatistik doğrulanamadı, kayıt atlandı.`,
              );
              continue;
            }

            window.postMessage(
              {
                type: "OBS_STAT_RESPONSE",
                data: htmlData,
                donemAd: donem.text,
                donemValue: donem.value,
                subeNo: subeNo,
              },
              "*",
            );
          } catch (err) {
            console.error("Veri çekilirken hata oluştu:", err);
          }
        }
      }
    } catch (err) {
      console.error("❌ Tarama sırasında beklenmeyen bir hata oluştu:", err);
      window.postMessage({ type: "OBS_SCRAPE_ERROR" }, "*");
      return;
    }

    console.log(
      "\n✅✅✅ BÜTÜN DÖNEMLERİN İSTATİSTİKLERİ BAŞARIYLA ÇEKİLDİ! ✅✅✅",
    );
    window.postMessage({ type: "OBS_SCRAPE_COMPLETE" }, "*");
  };
})();
