// SureRadar Bridge — content script.
// Roda na aba logada da surebet.com. A cada 10 min (e uma vez ao carregar),
// VARRE TODAS as páginas de apostas (seguindo o cursor "próximo") e manda a
// lista completa pro service worker, que envia ao painel.
//
// Por que varrer tudo: a surebet.com pagina de 25 em 25 (são centenas de
// apostas). Se raspássemos só a página atual, o PRO perderia entradas >1% que
// estão em outras páginas e o FREE nem teria as ≤1%. Então seguimos o cursor
// até acabar (com um teto de segurança de páginas).

const INTERVALO_MS = 10 * 60 * 1000; // 10 minutos
const MAX_PAGINAS = 40;              // teto de segurança (40 × 25 = 1000 apostas)

// Extrai as surebets de um Document (a página ao vivo OU uma página buscada).
function rasparDoc(doc) {
  return [...doc.querySelectorAll("tbody.surebet_record")].map((rec) => {
    const legs = [...rec.querySelectorAll("tr")].map((tr) => {
      const book = tr.querySelector(".bookmaker-name");
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
        sport: "",
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

// Acha o link "próximo »" dentro de um Document.
function linkProximo(doc) {
  const a = [...doc.querySelectorAll("a")].find((x) =>
    /pr[oó]ximo|next/i.test(x.textContent));
  return a ? a.href : null;
}

// Varre da página atual até o fim, deduplicando por id.
async function rasparTudo() {
  const vistos = new Set();
  const todos = [];
  const add = (recs) => {
    let novos = 0;
    for (const r of recs) {
      if (r.id && !vistos.has(r.id)) { vistos.add(r.id); todos.push(r); novos++; }
    }
    return novos;
  };

  add(rasparDoc(document));                 // página ao vivo
  let prox = linkProximo(document);
  let pag = 1;

  while (prox && pag < MAX_PAGINAS) {
    let doc;
    try {
      const resp = await fetch(prox, { credentials: "include" });
      if (!resp.ok) break;
      doc = new DOMParser().parseFromString(await resp.text(), "text/html");
    } catch (e) {
      console.warn("[SureRadar] falha ao buscar página:", e);
      break;
    }
    const novos = add(rasparDoc(doc));
    if (!novos) break;                       // sem novidades = fim (ou laço)
    prox = linkProximo(doc);
    pag++;
  }

  return todos;
}

async function ciclo() {
  let records = [];
  try {
    records = await rasparTudo();
  } catch (e) {
    console.warn("[SureRadar] erro na varredura:", e);
    return;
  }
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets (varredura completa) ao painel`);
}

// Primeira coleta ~6s após carregar (dá tempo dos dados aparecerem), depois a cada 10 min.
setTimeout(ciclo, 6000);
setInterval(ciclo, INTERVALO_MS);
