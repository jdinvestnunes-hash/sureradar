// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e uma vez ao carregar),
// lê as surebets da tela e manda pro service worker, que envia ao painel local.

const INTERVALO_MS = 10 * 60 * 1000; // 10 minutos

function raspar() {
  return [...document.querySelectorAll("tbody.surebet_record")].map((rec) => {
    const legs = [...rec.querySelectorAll("tr")].map((tr) => {
      const book = tr.querySelector(".bookmaker-name");
      const bc = tr.querySelector(".booker");
      const co = tr.querySelector(".coeff");
      const va = tr.querySelector(".value");
      const ev = tr.querySelector(".event");
      const vl = tr.querySelector(".value_link");
      if (!book || !va) return null;
      const odd = parseFloat(va.textContent.trim());
      if (!(odd > 0)) return null;
      return {
        bookmaker: book.textContent.trim(),
        market: co ? co.textContent.trim() : "",
        odd,
        teams: ev ? ((ev.querySelector("a") || ev).textContent || "").trim() : "",
        sport: bc ? bc.textContent.replace(book.textContent, "").trim() : "",
        link: vl ? vl.href : null,
      };
    }).filter(Boolean);
    return {
      id: rec.dataset.id,
      profit: parseFloat(rec.dataset.profit),
      start: parseInt(rec.dataset.startAt),
      legs,
    };
  }).filter((r) => r.legs.length === 2);
}

function ciclo() {
  const records = raspar();
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets ao painel`);
}

// Primeira coleta ~6s após carregar (dá tempo dos dados aparecerem), depois a cada 10 min.
setTimeout(ciclo, 6000);
setInterval(ciclo, INTERVALO_MS);
