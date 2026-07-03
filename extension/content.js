// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e ao carregar), raspa a
// LISTA INTEIRA (todas as páginas, seguindo "próximo »") e manda pro painel.
// O servidor divide: 0–1% (25 primeiras) = FREE, ≥4% = PRO, e descarta as
// bugadas (>25%). Mescla ingests, então mandamos em lotes (parcial já conta).
//
// Obs.: NÃO usamos o filtro de lucro da surebet.com (exige plano pago). Por isso
// varremos tudo e dividimos por lucro aqui no nosso lado.

const INTERVALO_MS = 10 * 60 * 1000;  // 10 min
const FETCH_TIMEOUT_MS = 12000;
const DELAY_PG = 1100;                // DEVAGAR: rápido demais o site corta (502)
const MAX_PAGINAS = 60;               // varre bastante (60 × 25 = 1500 apostas)
const LOTE_ENVIO = 4;                 // envia a cada 4 páginas (parcial já vale)

const dorme = (ms) => new Promise((r) => setTimeout(r, ms));

function rasparDoc(doc) {
  return [...doc.querySelectorAll("tbody.surebet_record")].map((rec) => {
    const legs = [...rec.querySelectorAll("tr")].map((tr) => {
      const book = tr.querySelector(".bookmaker-name");
      const bk = tr.querySelector(".booker");
      const co = tr.querySelector(".coeff");
      const va = tr.querySelector(".value");
      const ev = tr.querySelector(".event");
      const vl = tr.querySelector(".value_link");
      if (!book || !va) return null;
      const odd = parseFloat(va.textContent.trim());
      if (!(odd > 0)) return null;
      const nome = book.textContent.trim();
      let sport = "";
      if (bk) {
        const p = bk.textContent.split("\n").map((s) => s.trim()).filter((s) => s && s !== nome);
        sport = p.length ? p[p.length - 1] : "";
      }
      return {
        bookmaker: nome,
        market: co ? co.textContent.trim() : "",
        odd,
        teams: ev ? ((ev.querySelector("a") || ev).textContent || "").trim() : "",
        sport,
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

function linkProximo(doc) {
  const a = [...doc.querySelectorAll("a")].find((x) => /pr[oó]ximo|next/i.test(x.textContent));
  return a ? a.href : null;
}

async function buscarDoc(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch(url, { credentials: "include", signal: ctrl.signal });
    if (!r.ok) return null;
    return new DOMParser().parseFromString(await r.text(), "text/html");
  } catch (e) {
    console.warn("[SureRadar] falha ao buscar:", e);
    return null;
  } finally {
    clearTimeout(t);
  }
}

function enviar(records, label) {
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets (${label})`);
}

async function ciclo() {
  const vistos = new Set();
  let lote = [];       // acumula desde o último envio
  const add = (recs) => {
    let n = 0;
    for (const r of recs) if (r.id && !vistos.has(r.id)) { vistos.add(r.id); lote.push(r); n++; }
    return n;
  };

  // página 1 = DOM ao vivo (rápido)
  add(rasparDoc(document));
  let prox = linkProximo(document);
  let pag = 1;

  while (prox && pag < MAX_PAGINAS) {
    await dorme(DELAY_PG);
    let doc = await buscarDoc(prox);
    if (!doc) {                         // 502/timeout: espera e tenta 1x de novo
      await dorme(4000);
      doc = await buscarDoc(prox);
      if (!doc) break;
    }
    if (!add(rasparDoc(doc))) break;   // fim da lista
    prox = linkProximo(doc);
    pag++;
    if (pag % LOTE_ENVIO === 0) {       // envia parcial (o servidor mescla)
      enviar(lote.splice(0), `parcial p${pag}`);
    }
  }
  enviar(lote.splice(0), `final (${pag} págs)`);
}

// Primeira coleta ~4s após carregar; depois a cada 10 min.
setTimeout(ciclo, 4000);
setInterval(ciclo, INTERVALO_MS);
