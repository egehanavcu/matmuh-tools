(function () {
  window.addEventListener("message", function (event) {
    if (event.data && event.data.type === "START_SCRAPING") {
      window.baslat();
    }
  });

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

    for (let d = 0; d < donemler.length; d++) {
      const donem = donemler[d];
      console.log(`\n=================================================`);
      console.log(
        `📅 DÖNEM DEĞİŞTİRİLİYOR: ${donem.text} (${d + 1}/${donemler.length})`,
      );
      console.log(`=================================================`);

      donemSelect = aktifDokuman.getElementById("cmbDonemler");
      if (!donemSelect) break;
      donemSelect.value = donem.value;

      if (typeof aktifPencere.__doPostBack === "function") {
        setTimeout(function () {
          aktifPencere.__doPostBack("cmbDonemler", "");
        }, 0);
      }

      console.log("⏳ Ders tablosunun yenilenmesi bekleniyor...");
      await new Promise((resolve) => setTimeout(resolve, 4000));

      aktifDokuman = aktifPencere.document;
      let butonlar = aktifDokuman.querySelectorAll(
        'a[id^="grd_not_listesi_btnIstatistik_"]',
      );

      console.log(
        `Bu dönemde ${butonlar.length} adet istatistik butonu bulundu.`,
      );

      for (let i = 0; i < butonlar.length; i++) {
        const href = butonlar[i].getAttribute("href");
        if (href && href.includes("__doPostBack")) {
          const params = href.match(/'([^']*)'/g);
          if (params && params.length >= 2) {
            const eventTarget = params[0].replace(/'/g, "");
            const eventArgument = params[1].replace(/'/g, "");

            if (typeof aktifPencere.__doPostBack === "function") {
              setTimeout(function () {
                aktifPencere.__doPostBack(eventTarget, eventArgument);
              }, 0);
            }

            await new Promise((resolve) => setTimeout(resolve, 2000));

            try {
              const statRes = await fetch(
                "https://obs.yildiz.edu.tr/oibs/acd/new_not_giris_istatistik.aspx",
              );
              const htmlData = await statRes.text();

              window.postMessage(
                {
                  type: "OBS_STAT_RESPONSE",
                  data: htmlData,
                  index: i + 1,
                  donemAd: donem.text,
                },
                "*",
              );
            } catch (err) {
              console.error(`Veri çekilirken hata oluştu:`, err);
            }
          }
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }

    console.log(
      "\n✅✅✅ BÜTÜN DÖNEMLERİN İSTATİSTİKLERİ BAŞARIYLA ÇEKİLDİ! ✅✅✅",
    );
    window.postMessage({ type: "OBS_SCRAPE_COMPLETE" }, "*");
  };
})();
