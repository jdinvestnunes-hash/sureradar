// SureRadar Bridge — content script v4 (NAVEGAÇÃO REAL).
//
// O surebet.com bloqueia (403) buscas de página feitas por script (fetch e
// iframe). Então o robô agora FOLHEIA DE VERDADE: navega esta própria aba pelo
// link "próximo »", raspa cada página que carrega e envia ao painel. Para o
// site, é indistinguível de um humano paginando.
//
// A varredura sobrevive às navegações guardando o estado no sessionStorage.
// Ao terminar, volta para a página inicial (o filtro do usuário fica intacto —
// paginar não mexe em filtro). Roda a cada 10 min.
//
// ⚠️ Esta aba vira a "aba do robô": deixe-a aberta no filtro certo e não
// navegue nela manualmente.

const CICLO_MS = 10 * 60 * 1000;   // intervalo entre varreduras
const MAX_PAGINAS = 40;            // teto de segurança (40 × 25 = 1000 apostas)
const DELAY_HUMANO = () => 2500 + Math.random() * 2500;  // 2,5–5s por página

// ---------- raspagem da página atual ----------
function rasparPagina() {
  return [...document.querySelectorAll("tbody.surebet_record")].map((rec) => {
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

function linkProximo() {
  const a = [...document.querySelectorAll("a")].find((x) => /pr[oó]ximo|next/i.test(x.textContent));
  return a ? a.href : null;
}

// ---------- estado (sobrevive às navegações da varredura) ----------
const S = {
  get scan() { try { return JSON.parse(sessionStorage.getItem("sr_scan")); } catch (e) { return null; } },
  set scan(v) { v ? sessionStorage.setItem("sr_scan", JSON.stringify(v)) : sessionStorage.removeItem("sr_scan"); },
  get lastTs() { return parseInt(localStorage.getItem("sr_last_scan") || "0"); },
  set lastTs(v) { localStorage.setItem("sr_last_scan", String(v)); },
};

function enviar(records, label) {
  if (!records.length) return;
  chrome.runtime.sendMessage({ tipo: "ingest", records });
  console.log(`[SureRadar] enviadas ${records.length} surebets (${label})`);
}

// ---------- um passo da varredura (roda a cada página carregada) ----------
function passo() {
  const scan = S.scan;
  if (!scan || !scan.ativo) return;

  const recs = rasparPagina();
  const vistos = new Set(scan.ids || []);
  const novos = recs.filter((r) => r.id && !vistos.has(r.id));
  novos.forEach((r) => vistos.add(r.id));
  scan.ids = [...vistos];
  scan.pag = (scan.pag || 0) + 1;

  // envia JÁ o que achou nesta página (o servidor mescla; nada se perde)
  enviar(novos, `página ${scan.pag}`);

  const prox = linkProximo();
  const fim = !prox || scan.pag >= MAX_PAGINAS || (scan.pag > 1 && novos.length === 0);

  if (fim) {
    const motivo = !prox ? "fim da lista" : (scan.pag >= MAX_PAGINAS ? "cap de páginas" : "sem novidade");
    console.log(`[SureRadar] varredura concluída: ${scan.ids.length} apostas em ${scan.pag} página(s) — ${motivo}. Voltando ao início…`);
    const volta = scan.volta;
    S.scan = null;
    S.lastTs = Date.now();
    if (volta && location.href !== volta) {
      setTimeout(() => { location.href = volta; }, 1500);
    }
    return;
  }

  S.scan = scan;
  setTimeout(() => { location.href = prox; }, DELAY_HUMANO());
}

function iniciarScan() {
  if (!document.querySelector("tbody.surebet_record")) return;  // página sem lista
  console.log("[SureRadar] iniciando varredura (navegação real)…");
  S.scan = { ativo: true, pag: 0, ids: [], volta: location.href };
  passo();
}

// ---------- agendador ----------
(function aoCarregar() {
  const scan = S.scan;
  if (scan && scan.ativo) {
    // estamos no meio de uma varredura: continua nesta página
    setTimeout(passo, 1800);
    return;
  }
  const check = () => {
    if (Date.now() - S.lastTs >= CICLO_MS) iniciarScan();
  };
  setTimeout(check, 6000);          // primeira chance ao abrir
  setInterval(check, 60 * 1000);    // re-checa a cada minuto
})();
