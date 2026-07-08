// SureRadar — dashboard

const FILTERS_KEY = "sureradar_filtros_v6";
const BANK_KEY = "sureradar_banca_v1";

let META = null, REFRESH_SEC = 600, LAST_TS = 0;
let filtros = load(FILTERS_KEY, {});
let banca = load(BANK_KEY, []);
let SUREBETS = [];
let LOCKED = [];    // entradas reais de alto lucro (>1%) borradas para o FREE
let PLANO = "free"; // plano do usuário logado (free | pro)

// Ícones SVG de traço (estilo profissional, sem emoji)
const SVG = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICONS = {
  globe: SVG('<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a13.5 13.5 0 0 1 0 18a13.5 13.5 0 0 1 0-18z"/>'),
  football: SVG('<circle cx="12" cy="12" r="9"/><path d="M12 8l3.8 2.8-1.45 4.4h-4.7L8.2 10.8 12 8z"/><path d="M12 3v5M4.7 9.5l3.5 1.3M6.4 17.8l2.85-2.6M14.75 15.2l2.85 2.6M15.8 10.8l3.5-1.3"/>'),
  tennis: SVG('<circle cx="12" cy="12" r="9"/><path d="M5.2 5.8C8 8 8 16 5.2 18.2M18.8 5.8C16 8 16 16 18.8 18.2"/>'),
  basketball: SVG('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18M5.8 5.8C8.2 8.2 8.2 15.8 5.8 18.2M18.2 5.8C15.8 8.2 15.8 15.8 18.2 18.2"/>'),
  volleyball: SVG('<circle cx="12" cy="12" r="9"/><path d="M12 3c1.2 3.6.6 7.2-1.8 9.6M21 12c-3.6 1.2-7.2.6-9.6-1.8M6 18.6c2.4-3 6-4.2 9.6-3"/>'),
  tabletennis: SVG('<circle cx="17" cy="7" r="2.2"/><path d="M4 20l3.2-3.2M7.2 16.8a6.5 6.5 0 1 1 9.2-9.2 6.5 6.5 0 0 1-9.2 9.2z"/>'),
  chart: SVG('<path d="M4 19V5M4 19h16"/><path d="M8 15l3.5-4 2.5 2 4.5-5.5"/>'),
  lock: SVG('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
  rocket: SVG('<path d="M12 15c-2 0-5-1-5-1s1.5-6 5-9.5C15.5 8 17 14 17 14s-3 1-5 1z"/><path d="M9 15l-2 4 4-2M15 15l2 4-4-2M12 15v5"/><circle cx="12" cy="9" r="1.4"/>'),
};
const SPORTS_UI = {
  Football: { label: "Futebol", ico: ICONS.football },
  Tennis: { label: "Tênis", ico: ICONS.tennis },
  Basketball: { label: "Basquete", ico: ICONS.basketball },
  Volleyball: { label: "Vôlei", ico: ICONS.volleyball },
  TableTennis: { label: "Tênis de Mesa", ico: ICONS.tabletennis },
};

// Teasers premium (borrados) — iscas de alto lucro para o upgrade
const TEASERS = [
  { profit_pct: 9.14, event: "★★★★★★ x ★★★★★★", market_label: "Escanteios", sport: "Football", sport_label: "Liga Premium", commence_br: "—",
    legs: [{ outcome: "Acima 8.5", odd: 2.10, bookmaker_label: "SuperBet" }, { outcome: "Abaixo 8.5", odd: 2.05, bookmaker_label: "Betano" }] },
  { profit_pct: 8.32, event: "★★★★★ x ★★★★★", market_label: "Cartões", sport: "Football", sport_label: "Liga Premium", commence_br: "—",
    legs: [{ outcome: "Acima 3.5", odd: 2.30, bookmaker_label: "Novibet" }, { outcome: "Abaixo 3.5", odd: 1.95, bookmaker_label: "Bet365" }] },
  { profit_pct: 7.86, event: "★★★★ x ★★★★", market_label: "Chutes ao gol", sport: "Tennis", sport_label: "ATP Premium", commence_br: "—",
    legs: [{ outcome: "Acima 0.5", odd: 2.20, bookmaker_label: "PixBet" }, { outcome: "Abaixo 0.5", odd: 2.00, bookmaker_label: "Betsul" }] },
];

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h !== undefined) e.innerHTML = h; return e; };
const brl = (v) => "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function load(k, def) { try { return JSON.parse(localStorage.getItem(k)) ?? def; } catch { return def; } }
function saveFiltros() { localStorage.setItem(FILTERS_KEY, JSON.stringify(filtros)); }
// Banca: salva no navegador (cache) E no SERVIDOR (banco de dados) — assim as
// entradas sobrevivem a troca de PC/celular e limpeza do navegador.
let _bancaSyncTimer = null;
function saveBanca() {
  localStorage.setItem(BANK_KEY, JSON.stringify(banca));
  renderBankBadge();
  clearTimeout(_bancaSyncTimer);            // debounce: agrupa edições rápidas
  _bancaSyncTimer = setTimeout(() => {
    fetch("/api/banca", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entradas: banca }) }).catch(() => {});
  }, 600);
}

// Ao abrir: puxa a banca do servidor. Se o servidor estiver vazio e o navegador
// tiver entradas antigas (localStorage), MIGRA elas pro banco.
async function syncBancaDoServidor() {
  try {
    const r = await fetch("/api/banca");
    if (!r.ok) return;
    const j = await r.json();
    const doServidor = j.entradas || [];
    if (doServidor.length) {
      banca = doServidor;
      localStorage.setItem(BANK_KEY, JSON.stringify(banca));
    } else if (banca.length) {
      // migração: primeiras entradas (do localStorage) sobem pro banco
      fetch("/api/banca", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entradas: banca }) }).catch(() => {});
    }
    renderBankBadge();
  } catch { /* offline: segue com o localStorage */ }
}
syncBancaDoServidor();
function sportUI(id) { return SPORTS_UI[id] || { label: id, ico: ICONS.globe }; }
function dataDe(br) { return br && br.includes(" ") ? br.split(" ")[0] : ""; }        // "dd/mm/yyyy"
function ddmm(d) { const p = d.split("/"); return p.length >= 2 ? p[0] + "/" + p[1] : d; }
function shortOutcome(o) { let s = o.split(" - ")[0].split(" (")[0].trim(); return s.length > 20 ? s.slice(0, 19) + "…" : s; }

const ICON_CALC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="8" y2="10"/><line x1="12" y1="10" x2="12" y2="10"/><line x1="16" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="8" y2="14"/><line x1="12" y1="14" x2="12" y2="14"/><line x1="16" y1="14" x2="16" y2="18"/><line x1="8" y1="18" x2="12" y2="18"/></svg>';

// ---------- Meta ----------
async function initMeta() {
  META = await (await fetch("/api/meta")).json();
  REFRESH_SEC = META.refresh_seg || 600; restante = REFRESH_SEC;
  if (filtros.min_profit === undefined) filtros.min_profit = 0;
  if (filtros.max_profit === undefined) filtros.max_profit = 0;
  // Espelha a conta: marca TODAS as casas por padrão. Mantém a seleção manual do
  // usuário, MAS auto-marca casas NOVAS que apareceram na fonte (senão o usuário
  // perde arbs de casas novas — ex.: BetBoom só aparece em apostas de alto lucro).
  const metaKeys = META.bookmakers.map((b) => b.key);
  const conhecidas = filtros._casas_conhecidas;   // casas já exibidas antes
  if (!filtros.bookmakers || !filtros.bookmakers.length || !conhecidas) {
    filtros.bookmakers = metaKeys.slice();         // 1ª vez / vazio -> todas
  } else {
    const sel = new Set(filtros.bookmakers.filter((k) => metaKeys.includes(k)));
    metaKeys.filter((k) => !conhecidas.includes(k)).forEach((k) => sel.add(k)); // novas -> marca
    filtros.bookmakers = metaKeys.filter((k) => sel.has(k));
  }
  filtros._casas_conhecidas = metaKeys.slice();
  if (filtros.sport === undefined) filtros.sport = "";
  if (filtros.date === undefined) filtros.date = "";
  saveFiltros();
  // Faixa dos sliders espelha o lucro da raspagem (ex.: conta de 1% a 15%).
  if (META.profit && META.profit.max) {
    const top = Math.max(2, Math.ceil(META.profit.max));
    $("#min-profit").max = top; $("#max-profit").max = top;
    if (filtros.max_profit > top) filtros.max_profit = 0;
  }
  renderChips(); renderBookmakers();
  $("#min-profit").value = filtros.min_profit;
  $("#max-profit").value = filtros.max_profit;
  updateOutputs();
}

function renderChips() {
  const box = $("#sport-chips"); box.innerHTML = "";
  const chips = [{ key: "", label: "Todos", ico: ICONS.globe }].concat((META.sports || []).map((s) => ({ key: s.key, ...sportUI(s.key) })));
  chips.forEach((c) => {
    const chip = el("button", "chip" + (filtros.sport === c.key ? " active" : ""), `<span class="ci">${c.ico}</span> ${c.label}`);
    chip.addEventListener("click", () => { filtros.sport = c.key; saveFiltros(); renderChips(); carregar(); });
    box.appendChild(chip);
  });
}

function renderBookmakers() {
  const box = $("#bookmakers-list"); box.innerHTML = "";
  META.bookmakers.forEach((b) => {
    const label = el("label", "check");
    const input = el("input"); input.type = "checkbox"; input.checked = filtros.bookmakers.includes(b.key);
    input.addEventListener("change", () => {
      const set = new Set(filtros.bookmakers);
      input.checked ? set.add(b.key) : set.delete(b.key);
      filtros.bookmakers = [...set]; saveFiltros(); carregar();
    });
    label.appendChild(input); label.appendChild(el("span", null, b.label)); box.appendChild(label);
  });
}

function updateOutputs() {
  $("#min-profit-out").textContent = Number(filtros.min_profit).toFixed(1).replace(/\.0$/, "") + "%";
  $("#max-profit-out").textContent = filtros.max_profit > 0 ? Number(filtros.max_profit).toFixed(1).replace(/\.0$/, "") + "%" : "sem teto";
}

// ---------- Carregar ----------
async function carregar() {
  const p = new URLSearchParams({
    min_profit: filtros.min_profit ?? 0, max_profit: filtros.max_profit ?? 0,
    bookmakers: (filtros.bookmakers || []).join(","), sports: filtros.sport || "",
  });
  let data;
  try { data = await (await fetch("/api/surebets?" + p)).json(); } catch { return; }
  setStatus(data.status);
  SUREBETS = data.surebets || [];
  LOCKED = data.locked || [];
  PLANO = data.plano || "free";
  render();
}

function setStatus(status) {
  const dot = $("#status-dot"), txt = $("#status-text");
  if (status.conectado) { dot.className = "dot on"; txt.textContent = "Ao vivo"; }
  else { dot.className = "dot off"; txt.textContent = "Offline"; }
  if (status.ultima_atualizacao) $("#updated-at").textContent = "fonte: " + status.ultima_atualizacao;
  if (status.updated_ts) {
    if (LAST_TS && status.updated_ts > LAST_TS) flashNovas();   // chegou raspagem nova
    LAST_TS = status.updated_ts;
  }
}

function hoje() { const d = new Date(); return String(d.getDate()).padStart(2, "0") + "/" + String(d.getMonth() + 1).padStart(2, "0"); }

function renderDateBar() {
  const bar = $("#date-bar"); bar.innerHTML = "";
  const datas = [...new Set(SUREBETS.map((s) => ddmm(dataDe(s.commence_br))).filter(Boolean))].sort((a, b) => {
    const [da, ma] = a.split("/").map(Number), [db, mb] = b.split("/").map(Number);
    return ma - mb || da - db;
  });
  const hj = hoje();
  const chips = [{ k: "", l: "Todos" }].concat(datas.map((d) => ({ k: d, l: d === hj ? "Hoje " + d : d })));
  chips.forEach((c) => {
    const chip = el("button", "date-chip" + (filtros.date === c.k ? " active" : ""), c.l);
    chip.addEventListener("click", () => { filtros.date = c.k; saveFiltros(); render(); });
    bar.appendChild(chip);
  });
}

function render() {
  renderDateBar();
  let visible = SUREBETS;
  if (filtros.date) visible = SUREBETS.filter((s) => ddmm(dataDe(s.commence_br)) === filtros.date);

  const list = $("#list"), empty = $("#empty");
  list.innerHTML = "";
  $("#count-label").textContent = visible.length + (visible.length === 1 ? " oportunidade" : " oportunidades");

  // Teasers de upgrade só pro FREE: mostra as entradas REAIS de alto lucro (>1%)
  // borradas; se não houver nenhuma no momento, usa uma amostra pra manter o incentivo.
  const promo = $("#promo"); promo.innerHTML = "";
  if (PLANO === "free") {
    const teasers = LOCKED.length ? LOCKED : TEASERS;
    promo.appendChild(bannerEl());
    teasers.forEach((t) => list.appendChild(teaserEl(t)));
  }

  if (!visible.length) {
    empty.classList.remove("hidden");
    const on = META?.status?.conectado;
    $("#empty-title").textContent = on ? "Nada nos seus filtros agora" : "Aguardando conexão…";
    $("#empty-text").textContent = on ? "Amplie o lucro, troque o esporte/data ou marque mais casas." : "Assim que a fonte conectar, as entradas aparecem aqui.";
    $(".empty-icon").textContent = on ? "🔍" : "📡";
  } else {
    empty.classList.add("hidden");
    visible.forEach((sb) => list.appendChild(opEl(sb)));
  }
}

function bannerEl() {
  const b = el("div", "teaser-banner");
  const info = el("div");
  info.appendChild(el("div", "tb-t", "Entradas de alto lucro disponíveis no VIP"));
  info.appendChild(el("div", "tb-s", "Surebets de 8% a 9%+ liberadas só para assinantes."));
  b.appendChild(info);
  const btn = el("button", "upgrade-btn", `<span class="ci" style="width:15px;height:15px">${ICONS.rocket}</span> Fazer upgrade`);
  btn.addEventListener("click", openUpgrade);
  b.appendChild(btn);
  return b;
}

function teaserEl(t) {
  const wrap = el("div", "teaser");
  wrap.appendChild(opEl(t, true));
  const lock = el("div", "teaser-lock");
  lock.appendChild(el("div", "tl-txt",
    `<span class="ci" style="width:14px;height:14px;margin-right:6px;vertical-align:-2px">${ICONS.lock}</span>Entrada de <b>+${t.profit_pct.toFixed(2)}%</b> — exclusiva VIP`));
  const btn = el("button", "upgrade-btn", "Desbloquear");
  btn.addEventListener("click", openUpgrade);
  lock.appendChild(btn);
  wrap.appendChild(lock);
  return wrap;
}

// Linha de oportunidade (estilo referência)
function opEl(sb, teaser) {
  const op = el("div", "op");
  const head = el("div", "op-head");
  const league = el("div", "op-league");
  league.appendChild(el("span", "ci", sportUI(sb.sport).ico));
  league.appendChild(el("span", null, (sb.sport_label || sportUI(sb.sport).label).slice(0, 42)));
  head.appendChild(league);
  head.appendChild(el("div", "op-time", sb.commence_br || ""));
  op.appendChild(head);

  const body = el("div", "op-body");
  const teams = el("div", "op-teams");
  teams.appendChild(el("div", "op-event", sb.event));
  teams.appendChild(el("div", "op-market", sb.market_label || ""));
  body.appendChild(teams);

  const odds = el("div", "op-odds");
  sb.legs.forEach((l) => {
    const box = el("div", "op-box");
    const main = el("div", "op-box-main");
    main.appendChild(el("div", "op-box-label", l.outcome));
    const book = el("div", "op-box-book");
    book.appendChild(el("span", null, l.bookmaker_label || l.bookmaker));
    // só mostra "ir para a casa" se o link for da CASA (nunca surebet.com)
    const linkCasa = (l.link && !/surebet\.com/i.test(l.link)) ? l.link : null;
    if (linkCasa && !teaser) book.appendChild(el("span", "ext", "↗ ir para a casa"));
    main.appendChild(book);
    box.appendChild(main);
    box.appendChild(el("div", "op-box-odd", Number(l.odd).toFixed(2)));
    if (linkCasa && !teaser) box.addEventListener("click", () => window.open(linkCasa, "_blank", "noopener"));
    odds.appendChild(box);
  });
  body.appendChild(odds);
  op.appendChild(body);

  const bar = el("div", "op-bar");
  bar.appendChild(el("div", "op-return",
    `<span class="ci" style="width:15px;height:15px;margin-right:7px">${ICONS.chart}</span>${Number(sb.profit_pct).toFixed(2)}% RETORNO CERTO`));
  const calc = el("button", "op-calc", "CALCULAR " + ICON_CALC);
  if (!teaser) calc.addEventListener("click", () => openCalc(sb));
  bar.appendChild(calc);
  op.appendChild(bar);
  return op;
}

// ---------- Indicador de atualização ----------
// Sem contador "00:00": mostra que atualiza a cada 10 min, e pisca "Novas apostas!"
// quando chega raspagem nova.
let _flashUntil = 0;
function tickTimer() {
  if (Date.now() < _flashUntil) return;              // deixa o "Novas apostas!" na tela
  $("#timer-text").textContent = "Atualiza a cada 10 min";
}
function flashNovas() {
  $("#timer-text").textContent = "✓ Novas apostas!";
  _flashUntil = Date.now() + 4000;
}

// ---------- Upgrade ----------
function openUpgrade() { $("#up-overlay").classList.remove("hidden"); }
function closeUpgrade() { $("#up-overlay").classList.add("hidden"); }

// ---------- Calculadora ----------
let CALC_SB = null;
let CALC_STAKES = [];   // valor apostado em cada perna (editável)
let CALC_ODDS = [];     // odds editáveis (a odd pode ter mudado na casa)

// Split de lucro igual usando as ODDS ATUAIS (editadas ou não).
function splitEquilibrado(total) {
  const margem = CALC_ODDS.reduce((s, o) => s + 1 / (o || 1), 0);
  return CALC_ODDS.map((o) => total * (1 / (o || 1)) / margem);
}

function openCalc(sb) {
  CALC_SB = sb;
  $("#calc-event").textContent = sb.event;
  $("#calc-market").textContent = (sb.market_label || "") + "  ·  +" + Number(sb.profit_pct).toFixed(2) + "%";
  const total = sb.banca || 1000;
  $("#calc-total").value = total;
  CALC_ODDS = sb.legs.map((l) => Number(l.odd));
  CALC_STAKES = splitEquilibrado(total);        // split equilibrado inicial
  renderCalc();
  $("#calc-launch").textContent = "＋ Lançar na banca";
  $("#calc-overlay").classList.remove("hidden");
}
function closeCalc() { $("#calc-overlay").classList.add("hidden"); CALC_SB = null; }

// Split que dá lucro IGUAL dos dois lados (o ótimo) para um valor total.
function calcStakes(sb, total) {
  const odds = sb.legs.map((l) => Number(l.odd));
  const margem = odds.reduce((s, o) => s + 1 / o, 0);
  const stakes = odds.map((o) => total * (1 / o) / margem);
  const retorno = total / margem;
  return { stakes, retorno, lucro: retorno - total };
}

// A partir das apostas atuais (podem ter sido arredondadas/editadas): total
// investido, retorno por resultado e o lucro GARANTIDO (o menor dos lucros).
// Usa as ODDS EDITADAS (CALC_ODDS) quando existirem.
function calcResumo(legs, stakes) {
  const total = stakes.reduce((s, v) => s + (Number(v) || 0), 0);
  const retornos = legs.map((l, i) =>
    (Number(stakes[i]) || 0) * (CALC_ODDS[i] || Number(l.odd)));
  const lucros = retornos.map((r) => r - total);
  return { total, retornos, lucros, garantido: lucros.length ? Math.min(...lucros) : 0 };
}

function renderCalc() {
  if (!CALC_SB) return;
  const legs = CALC_SB.legs;
  const box = $("#calc-legs"); box.innerHTML = "";
  legs.forEach((leg, i) => {
    const item = el("div", "calc-leg");
    const t = el("div", "calc-leg-top");
    const name = el("div");
    name.appendChild(el("div", "calc-leg-name", leg.outcome));
    name.appendChild(el("div", "calc-leg-book", leg.bookmaker_label || leg.bookmaker));
    t.appendChild(name);
    // ODD EDITÁVEL: se a odd mudou na casa, corrige aqui e vê se ainda compensa.
    const oddWrap = el("div", "calc-odd-edit");
    oddWrap.appendChild(el("span", "calc-odd-at", "@"));
    const oddInp = el("input");
    oddInp.type = "number"; oddInp.min = "1.01"; oddInp.step = "0.01"; oddInp.className = "calc-odd-input";
    oddInp.value = (CALC_ODDS[i] || Number(leg.odd)).toFixed(2);
    oddInp.addEventListener("input", () => {
      CALC_ODDS[i] = parseFloat(oddInp.value) || 0;
      updateCalcTotals();          // mantém os valores; mostra o lucro novo
    });
    oddWrap.appendChild(oddInp);
    t.appendChild(oddWrap);
    item.appendChild(t);

    const st = el("div", "calc-stake");
    st.appendChild(el("div", "calc-stake-label", "Apostar na " + (leg.bookmaker_label || leg.bookmaker)));
    const edit = el("div", "calc-stake-edit");
    edit.appendChild(el("span", "calc-stake-cur", "R$"));
    const inp = el("input");
    inp.type = "number"; inp.min = "0"; inp.step = "1"; inp.className = "calc-stake-input";
    inp.value = Math.round((Number(CALC_STAKES[i]) || 0) * 100) / 100;
    inp.addEventListener("input", () => {
      const v = parseFloat(inp.value) || 0;
      CALC_STAKES[i] = v;
      // AJUSTE AUTOMÁTICO: digitou o valor de UMA casa -> as outras se ajustam
      // para lucro igual (s_j = v * o_i / o_j) e o total acompanha.
      const oi = CALC_ODDS[i] || Number(leg.odd);
      CALC_SB.legs.forEach((lj, j) => {
        if (j === i) return;
        const oj = CALC_ODDS[j] || Number(lj.odd);
        CALC_STAKES[j] = oj > 0 ? (v * oi / oj) : 0;
        const outroInp = $("#calc-legs").children[j] &&
          $("#calc-legs").children[j].querySelector(".calc-stake-input");
        if (outroInp) outroInp.value = Math.round(CALC_STAKES[j] * 100) / 100;
      });
      updateCalcTotals();
    });
    edit.appendChild(inp);
    st.appendChild(edit);
    item.appendChild(st);

    const info = el("div", "calc-leg-info");
    info.appendChild(el("div", "calc-leg-ret", ""));
    info.appendChild(el("div", "calc-leg-lucro", ""));
    item.appendChild(info);
    box.appendChild(item);
  });
  updateCalcTotals();
}

// Atualiza só os números (não recria os inputs, pra não perder o foco ao digitar).
function updateCalcTotals() {
  if (!CALC_SB) return;
  const { total, retornos, lucros, garantido } = calcResumo(CALC_SB.legs, CALC_STAKES);
  const items = $("#calc-legs").children;
  CALC_SB.legs.forEach((leg, i) => {
    const info = items[i] && items[i].querySelector(".calc-leg-info");
    if (!info) return;
    info.children[0].textContent = "retorno " + brl(retornos[i]);
    const lc = info.children[1];
    lc.textContent = "se sair: " + (lucros[i] >= 0 ? "+" : "") + brl(lucros[i]);
    lc.className = "calc-leg-lucro" + (lucros[i] >= 0 ? " ok" : " neg");
  });
  $("#calc-total").value = Math.round(total * 100) / 100;
  const pct = total > 0 ? (garantido / total * 100) : 0;
  $("#calc-return").textContent = brl(total + garantido);
  $("#calc-profit").textContent = (garantido >= 0 ? "+" : "") + brl(garantido) +
    "  (" + (garantido >= 0 ? "+" : "") + pct.toFixed(2) + "%)";
  $("#calc-profit").className = "calc-result-val " + (garantido >= 0 ? "green" : "red");
}

// Total digitado -> refaz o split equilibrado (com as odds atuais).
function onTotalInput() {
  if (!CALC_SB) return;
  CALC_STAKES = splitEquilibrado(parseFloat($("#calc-total").value) || 0);
  renderCalc();
}

// Arredonda cada aposta ao múltiplo (1/5/10) e recalcula o lucro.
function arredondarCalc(mult) {
  if (!CALC_SB) return;
  CALC_STAKES = CALC_STAKES.map((v) => Math.round((Number(v) || 0) / mult) * mult);
  renderCalc();
}

// Reequilibra (lucro igual dos dois lados) mantendo o total atual.
function equilibrarCalc() {
  if (!CALC_SB) return;
  const total = CALC_STAKES.reduce((s, v) => s + (Number(v) || 0), 0);
  CALC_STAKES = splitEquilibrado(total);
  renderCalc();
}

function launchToBank() {
  if (!CALC_SB) return;
  const { total, garantido } = calcResumo(CALC_SB.legs, CALC_STAKES);
  banca.push({
    id: (CALC_SB.id || "t") + "-" + Date.now(),
    event: CALC_SB.event, market: CALC_SB.market_label || "", sport: CALC_SB.sport,
    profit_pct: CALC_SB.profit_pct, total, expected: garantido, status: "pendente",
    legs: CALC_SB.legs.map((l, i) => ({ outcome: l.outcome, odd: CALC_ODDS[i] || l.odd, book: l.bookmaker_label || l.bookmaker, stake: CALC_STAKES[i] })),
    jogo: CALC_SB.commence_br || "",            // dia + horário do JOGO
    created: new Date().toLocaleDateString("pt-BR"),
  });
  saveBanca();
  $("#calc-launch").textContent = "✓ Lançado na banca!";
  setTimeout(closeCalc, 700);
}

// ---------- Banca ----------
function renderBankBadge() { $("#bank-count").textContent = banca.length; }
function renderBanca() {
  const list = $("#bank-list"), empty = $("#bank-empty");
  const apostado = banca.reduce((s, e) => s + e.total, 0);
  const previsto = banca.reduce((s, e) => s + e.expected, 0);
  const realizado = banca.filter((e) => e.status === "concluida").reduce((s, e) => s + e.expected, 0);
  $("#bank-metrics").innerHTML = "";
  [["Entradas", banca.length, ""], ["Total apostado", brl(apostado), "cyan"],
   ["Lucro previsto", brl(previsto), previsto < 0 ? "red" : "green"],
   ["Lucro realizado", brl(realizado), realizado < 0 ? "red" : "green"]]
    .forEach(([k, v, cls]) => { const c = el("div", "metric"); c.appendChild(el("div", "metric-label", k)); c.appendChild(el("div", "metric-val " + cls, v)); $("#bank-metrics").appendChild(c); });

  list.innerHTML = "";
  if (!banca.length) { empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  banca.slice().reverse().forEach((e) => {
    const row = el("div", "bank-row");
    const ev = el("div", "bank-ev"); ev.appendChild(document.createTextNode(e.event));
    ev.appendChild(el("small", null, e.market + " · " + sportUI(e.sport).label));
    // Dia + horário do JOGO (bem visível pra saber quando é)
    if (e.jogo) ev.appendChild(el("div", "bank-jogo", "🕒 Jogo: " + e.jogo));
    // Pernas: casa onde apostar + quanto em cada uma
    if (e.legs && e.legs.length) {
      const legsBox = el("div", "bank-legs");
      e.legs.forEach((l) => {
        const leg = el("div", "bank-leg");
        const info = el("div", "bank-leg-info");
        info.appendChild(el("span", "bank-leg-book", l.book || l.bookmaker || "—"));
        info.appendChild(el("span", "bank-leg-out", (l.outcome || "") + "  @ " + Number(l.odd).toFixed(2)));
        leg.appendChild(info);
        leg.appendChild(el("div", "bank-leg-stake", brl(l.stake || 0)));
        legsBox.appendChild(leg);
      });
      ev.appendChild(legsBox);
    }
    row.appendChild(ev);
    const stakeCol = el("div", "bank-col"); stakeCol.appendChild(el("div", "k", "Apostado"));
    const inp = el("input", "bank-edit"); inp.type = "number"; inp.value = e.total.toFixed(2); inp.step = "10";
    inp.addEventListener("change", () => {
      const nv = parseFloat(inp.value) || 0;
      const { stakes, lucro } = calcStakes({ legs: e.legs.map((l) => ({ odd: l.odd })) }, nv);
      e.total = nv; e.expected = lucro;
      e.legs.forEach((l, i) => { l.stake = stakes[i]; });   // atualiza o valor por casa
      saveBanca(); renderBanca();
    });
    stakeCol.appendChild(inp); row.appendChild(stakeCol);
    const profCol = el("div", "bank-col"); profCol.appendChild(el("div", "k", "Lucro"));
    const neg = (e.expected || 0) < 0;
    profCol.appendChild(el("div", "v " + (neg ? "red" : "green"), (neg ? "" : "+") + brl(e.expected)));
    row.appendChild(profCol);
    const stBtn = el("button", "bank-status" + (e.status === "concluida" ? " done" : ""), e.status === "concluida" ? "✓ Concluída" : "Pendente");
    stBtn.addEventListener("click", () => { e.status = e.status === "concluida" ? "pendente" : "concluida"; saveBanca(); renderBanca(); });
    row.appendChild(stBtn);
    const del = el("button", "bank-del", "🗑"); del.title = "Excluir";
    del.addEventListener("click", () => { banca = banca.filter((x) => x.id !== e.id); saveBanca(); renderBanca(); });
    row.appendChild(del); list.appendChild(row);
  });
}

// ---------- Abas ----------
function switchView(v) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
  $("#view-ops").classList.toggle("hidden", v !== "ops");
  $("#view-bank").classList.toggle("hidden", v !== "bank");
  const vc = document.getElementById("view-calc");
  if (vc) vc.classList.toggle("hidden", v !== "calc");
  if (v === "bank") renderBanca();
}

// ---------- Eventos ----------
$("#min-profit").addEventListener("input", (e) => { filtros.min_profit = parseFloat(e.target.value); updateOutputs(); saveFiltros(); carregar(); });
$("#max-profit").addEventListener("input", (e) => { filtros.max_profit = parseFloat(e.target.value); updateOutputs(); saveFiltros(); carregar(); });
$("#sel-all").addEventListener("click", () => { filtros.bookmakers = META.bookmakers.map((b) => b.key); saveFiltros(); renderBookmakers(); carregar(); });
$("#sel-none").addEventListener("click", () => { filtros.bookmakers = []; saveFiltros(); renderBookmakers(); carregar(); });
$("#calc-close").addEventListener("click", closeCalc);
$("#calc-total").addEventListener("input", onTotalInput);
$("#calc-launch").addEventListener("click", launchToBank);
$("#calc-overlay").addEventListener("click", (e) => { if (e.target.id === "calc-overlay") closeCalc(); });
document.querySelectorAll(".calc-round-btn").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.balance) equilibrarCalc();
  else arredondarCalc(parseInt(b.dataset.round));
}));
$("#up-close").addEventListener("click", closeUpgrade);
$("#up-cta").addEventListener("click", () => { location.href = "/planos"; });
$("#up-overlay").addEventListener("click", (e) => { if (e.target.id === "up-overlay") closeUpgrade(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeCalc(); closeUpgrade(); } });
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

// ---------- Sessão do usuário ----------
async function initUser() {
  let me;
  try {
    const r = await fetch("/api/me");
    if (r.status === 401) { location.href = "/login"; return false; }
    me = await r.json();
  } catch { return true; }           // sem rede: deixa o painel abrir
  const chip = $("#user-chip");
  if (chip && me && me.nome) {
    chip.style.display = "flex";
    $("#user-avatar").textContent = me.nome.trim()[0].toUpperCase();
    $("#user-name").textContent = me.nome.split(" ")[0];
    $("#user-avatar").style.cursor = "pointer";
    $("#user-name").style.cursor = "pointer";
    $("#user-avatar").title = $("#user-name").title = "Ver perfil";
    $("#user-avatar").onclick = $("#user-name").onclick = $("#user-perfil").onclick = () => location.href = "/perfil";
    const plan = $("#user-plan");
    plan.textContent = me.plano === "pro" ? "PRO" : "FREE";
    plan.classList.toggle("pro", me.plano === "pro");
    $("#user-out").addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" });
      location.href = "/login";
    });
  }
  return true;
}

// ---------- Boot ----------
(async function () {
  if (!(await initUser())) return;   // não logado -> /login
  renderBankBadge();
  await initMeta();
  await carregar();
  setInterval(tickTimer, 1000);      // atualiza o mostrador do timer
  setInterval(carregar, 30000);      // busca dados novos a cada 30s (pega novas raspagens)
})();
