// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e uma vez ao carregar),
// raspa DUAS views e manda pro painel (o servidor MESCLA as duas):
//   • FREE : lucro ATÉ 1%  -> pega as ~50 melhores (grupo/plano grátis)
//   • PRO  : lucro >= 2%   -> pega todas (poucas, as boas)
//
// Por que assim: a fonte tem MILHARES de surebets. Paginar do topo (maiores)
// nunca chega nas ≤1%. Usando o filtro de lucro na URL, cada view já vem pronta.

const INTERVALO_MS = 10 * 60 * 1000;  // 10 min
const FETCH_TIMEOUT_MS = 12000;
const DELAY_PG = 220;

const BASE = "https://pt.surebet.com/surebets";
const VIEW_FREE = BASE + "?selector%5Bmin_profit%5D=0&selector%5Bmax_profit%5D=1&selector%5Border%5D=profit_desc";
const VIEW_PRO  = BASE + "?selector%5Bmin_profit%5D=2&selector%5Border%5D=profit_desc";
const MAX_PG_FREE = 2;   // ~50 entradas ≤1%
const MAX_PG_PRO  = 6;   // as boas (>1%), poucas páginas

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

// Varre uma view (seguindo "próximo »") até maxPag, deduplicando por id.
async function varrerView(startUrl, maxPag) {
  const vistos = new Set();
  const todos = [];
  const add = (recs) => {
    let n = 0;
    for (const r of recs) if (r.id && !vistos.has(r.id)) { vistos.add(r.id); todos.push(r); n++; }
    return n;
  };
  let doc = await buscarDoc(startUrl);
  if (!doc) return todos;
  add(rasparDoc(doc));
  let prox = linkProximo(doc), pag = 1;
  while (prox && pag < maxPag) {
    await dorme(DELAY_PG);
    doc = await buscarDoc(prox);
    if (!doc) break;
    if (!add(rasparDoc(doc))) break;
    prox = linkProximo(doc);
    pag++;
  }
  return todos;
}

function enviar(records, label) {
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets (${label})`);
}

async function ciclo() {
  try {
    const free = await varrerView(VIEW_FREE, MAX_PG_FREE);   // ≤1%
    enviar(free.slice(0, 50), "FREE ≤1%");                   // 50 melhores
    const pro = await varrerView(VIEW_PRO, MAX_PG_PRO);      // >1% (boas)
    enviar(pro, "PRO >1%");
  } catch (e) {
    console.warn("[SureRadar] erro no ciclo:", e);
  }
}

// Primeira coleta ~4s após carregar; depois a cada 10 min.
setTimeout(ciclo, 4000);
setInterval(ciclo, INTERVALO_MS);
