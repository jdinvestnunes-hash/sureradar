// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e ao carregar), raspa a
// LISTA INTEIRA (todas as páginas, seguindo "próximo »") e manda pro painel.
// O servidor divide: 0–1% (25 primeiras) = FREE, ≥4% = PRO, e descarta as
// bugadas (>25%). Mescla ingests, então mandamos em lotes (parcial já conta).
//
// Obs.: NÃO usamos o filtro de lucro da surebet.com (exige plano pago). Por isso
// varremos tudo e dividimos por lucro aqui no nosso lado.

const INTERVALO_MS = 10 * 60 * 1000;  // 10 min
const FETCH_TIMEOUT_MS = 15000;
const DELAY_PG = 3500;                // BEM devagar: o site corta (502) se apressar
const RETRY_ESPERA_MS = 15000;        // cortou? espera 15s e tenta de novo
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
    if (!r.ok) {
      console.warn(`[SureRadar] fetch respondeu HTTP ${r.status}`);
      return null;
    }
    return new DOMParser().parseFromString(await r.text(), "text/html");
  } catch (e) {
    console.warn("[SureRadar] falha no fetch:", e);
    return null;
  } finally {
    clearTimeout(t);
  }
}

// Plano B: o site às vezes BLOQUEIA o fetch (403) mas aceita navegação normal.
// Um iframe invisível É uma navegação normal — carrega a página com a sessão
// e a gente lê o conteúdo (mesma origem).
function buscarViaIframe(url) {
  return new Promise((resolve) => {
    const f = document.createElement("iframe");
    f.style.cssText = "position:absolute;width:2px;height:2px;left:-9999px;top:-9999px;visibility:hidden;";
    let fim = (val) => { fim = () => {}; try { f.remove(); } catch (e) {} resolve(val); };
    const timer = setTimeout(() => fim(null), FETCH_TIMEOUT_MS + 8000);
    f.onload = () => {
      clearTimeout(timer);
      try {
        const html = f.contentDocument && f.contentDocument.documentElement.outerHTML;
        fim(html ? new DOMParser().parseFromString(html, "text/html") : null);
      } catch (e) {
        console.warn("[SureRadar] iframe inacessível:", e);
        fim(null);
      }
    };
    f.src = url;
    (document.body || document.documentElement).appendChild(f);
  });
}

// Busca uma página: tenta fetch (rápido); se o site barrar, vai de iframe.
async function obterPagina(url) {
  let doc = await buscarDoc(url);
  if (doc) return doc;
  console.warn("[SureRadar] fetch barrado — tentando via iframe (navegação real)…");
  doc = await buscarViaIframe(url);
  if (doc && doc.querySelectorAll("tbody.surebet_record").length === 0) {
    console.warn("[SureRadar] iframe carregou mas sem registros (bloqueio?)");
  }
  return doc;
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
  let motivo = "sem link próximo na página";

  while (prox && pag < MAX_PAGINAS) {
    await dorme(DELAY_PG);
    let doc = await obterPagina(prox);
    if (!doc) {                         // bloqueio/timeout: espera BEM e tenta de novo
      console.warn(`[SureRadar] p${pag + 1} cortada; esperando ${RETRY_ESPERA_MS / 1000}s pra tentar de novo…`);
      await dorme(RETRY_ESPERA_MS);
      doc = await obterPagina(prox);
      if (!doc) { motivo = `cortado pelo site na página ${pag + 1}`; break; }
    }
    if (!add(rasparDoc(doc))) { motivo = "fim da lista"; break; }
    prox = linkProximo(doc);
    pag++;
    motivo = prox ? motivo : "fim da lista";
    if (pag % LOTE_ENVIO === 0) {       // envia parcial (o servidor mescla)
      enviar(lote.splice(0), `parcial p${pag}`);
    }
  }
  if (pag >= MAX_PAGINAS) motivo = "cap de páginas";
  enviar(lote.splice(0), `final (${pag} págs — ${motivo})`);
}

// Primeira coleta ~4s após carregar; depois a cada 10 min.
setTimeout(ciclo, 4000);
setInterval(ciclo, INTERVALO_MS);
