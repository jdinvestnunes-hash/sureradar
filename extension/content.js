// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e uma vez ao carregar):
//   1) envia JÁ as apostas da página atual (preenche o painel na hora);
//   2) varre as demais páginas (seguindo o cursor "próximo") e reenvia o
//      conjunto COMPLETO — assim o PRO recebe todas as >1% e o FREE as ≤1%.
//
// Envio incremental + timeout por página: se a varredura estiver lenta ou
// travar, a página 1 já foi entregue e o painel não fica vazio.

const INTERVALO_MS = 10 * 60 * 1000; // 10 minutos
const MAX_PAGINAS = 30;              // teto de segurança (30 × 25 = 750 apostas)
const FETCH_TIMEOUT_MS = 12000;      // corta um fetch que travar
const DELAY_ENTRE_PAGINAS = 250;     // respiro entre páginas (não martelar o site)

const dorme = (ms) => new Promise((r) => setTimeout(r, ms));

// Extrai as surebets de um Document (a página ao vivo OU uma buscada).
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
      const nomeCasa = book.textContent.trim();
      // Esporte: no .booker vem "<Casa> ... <Esporte>" (ex.: "Betano (BR)\nTênis").
      let sport = "";
      if (bk) {
        const partes = bk.textContent.split("\n").map((s) => s.trim())
          .filter((s) => s && s !== nomeCasa);
        sport = partes.length ? partes[partes.length - 1] : "";
      }
      return {
        bookmaker: nomeCasa,
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
  const a = [...doc.querySelectorAll("a")].find((x) =>
    /pr[oó]ximo|next/i.test(x.textContent));
  return a ? a.href : null;
}

function enviar(records, label) {
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets (${label})`);
}

async function buscarDoc(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetch(url, { credentials: "include", signal: ctrl.signal });
    if (!resp.ok) return null;
    return new DOMParser().parseFromString(await resp.text(), "text/html");
  } catch (e) {
    console.warn("[SureRadar] falha ao buscar página:", e);
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function ciclo() {
  const vistos = new Set();
  const todos = [];
  const add = (recs) => {
    let n = 0;
    for (const r of recs) {
      if (r.id && !vistos.has(r.id)) { vistos.add(r.id); todos.push(r); n++; }
    }
    return n;
  };

  // 1) Página atual — envia IMEDIATAMENTE (painel enche na hora).
  add(rasparDoc(document));
  enviar(todos.slice(), "página 1");

  // 2) Varre o resto seguindo o cursor e reenvia o conjunto completo.
  let prox = linkProximo(document);
  let pag = 1;
  while (prox && pag < MAX_PAGINAS) {
    await dorme(DELAY_ENTRE_PAGINAS);
    const doc = await buscarDoc(prox);
    if (!doc) break;
    const novos = add(rasparDoc(doc));
    if (!novos) break;              // sem novidades = fim (ou laço)
    prox = linkProximo(doc);
    pag++;
  }

  // Se a varredura trouxe páginas além da 1ª, reenvia o conjunto completo.
  if (pag > 1) {
    enviar(todos, `varredura completa (${pag} págs)`);
  }
}

// Primeira coleta ~6s após carregar; depois a cada 10 min.
setTimeout(ciclo, 6000);
setInterval(ciclo, INTERVALO_MS);
