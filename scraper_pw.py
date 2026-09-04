"""
scraper_pw.py — robô de raspagem com Playwright (Chrome REAL automatizado).

É o jeito do Caio: um navegador de verdade, controlado por código, logado na
conta do surebet.com, que folheia as páginas e manda pro painel. Como é um Chrome
real (não fetch), o site não bloqueia (403) como bloqueava a extensão.

RODAR:  python scraper_pw.py
- Abre uma janela do Chrome. Se pedir login, VOCÊ loga uma vez (a sessão fica
  salva na pasta pw_profile/ — nas próximas vezes já entra direto).
- Deixa a janela aberta. Ele folheia sozinho a cada CICLO_MIN minutos.

Depois que provar aqui, a gente move isso pra um servidor (VPS) que roda 24h.
"""

import json
import os
import random
import signal
import sys
import time
import requests
from playwright.sync_api import sync_playwright

# Cache dos links já resolvidos (redirect do surebet -> URL final na casa).
# Persiste em arquivo pra não re-resolver a cada varredura.
CACHE_FILE = "link_cache.json"
try:
    LINK_CACHE = json.load(open(CACHE_FILE, encoding="utf-8"))
except Exception:
    LINK_CACHE = {}


def _salvar_cache():
    # Gravação ATÔMICA: escreve num .tmp e troca de uma vez. Como salvamos parcial
    # (a cada N links) e o arquivo tem dezenas de MB, um kill no meio de um write
    # direto poderia deixar o cache corrompido -> perderíamos TODOS os links.
    try:
        tmp = CACHE_FILE + ".tmp"
        json.dump(LINK_CACHE, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass

SAAS = "https://web-production-a41df.up.railway.app/api/ingest"
SAAS_VALOR = SAAS.replace("/api/ingest", "/api/ingest-valor")   # ODDS DE VALOR (separado)
SAAS_MIDDLE = SAAS.replace("/api/ingest", "/api/ingest-middle") # APOSTAS DE INTERVALO (separado)
URL_LISTA = "https://pt.surebet.com/surebets"
URL_VALOR = "https://pt.surebet.com/valuebets"                  # aba "Apostas de valor"
URL_MIDDLE = "https://pt.surebet.com/middles"                   # aba "Apostas de intervalo"
MAX_PAG_VALOR = 1              # valuebets: só a 1ª página (os 10 primeiros cabem nela)
MAX_VALOR = 10                # SÓ AS 10 PRIMEIRAS odds de valor (decisão do jardel 04/09)
MAX_PAG_MIDDLE = 1            # middles: só a 1ª página (os 10 primeiros cabem nela)
MAX_MIDDLE = 10               # SÓ AS 10 PRIMEIRAS apostas de intervalo (decisão do jardel 04/09)
PERFIL = "pw_profile"          # sessão do Chrome fica salva aqui (login persiste)
CICLO_MIN = 30                 # minutos entre varreduras FUNDAS (decisão do jardel 04/09).
                               # Se mudar, ajuste junto: vigia LIMITE_ZUMBI_SEG (45 min),
                               # feed._EXPIRY_SEG e config.ROBO_OFFLINE_SEG (40 min), ROBO_ALERTA_MIN.
FAST_SEG = 45                  # segundos entre passadas RÁPIDAS (só a página 1 = as de
                               # maior lucro/mais frescas). É o "quase ao vivo".
FAST_ATIVO = False             # MODO MANSO: só a FUNDA de CICLO_MIN em CICLO_MIN. As rápidas
                               # de 45s cheiram a robô (dispararam o throttle) — desligadas.
                               # Volte a True se um dia quiser o "quase ao vivo" de novo.
VALOR_ATIVO = True             # liga/desliga a passada de ODDS DE VALOR (deixe True p/ raspar valuebets)
MIDDLE_ATIVO = True            # liga/desliga a passada de APOSTAS DE INTERVALO (middles)
MAX_PAGINAS = 2                # SÓ AS 2 PRIMEIRAS PÁGINAS (decisão do jardel 04/09): alimenta o
                               # PRO e para. Não desce mais atrás da faixa FREE (1–2%) — o FREE
                               # só recebe o que por acaso estiver nessas 2 págs.
MIN_PROFIT = 1.0               # PARA quando o lucro chega aqui (lista é decrescente).
                              # FREE = 1–2% · PRO = 2–25% · abaixo de 1% ignora.
# --- Regra de corte das SUREBETS (leve, mas nunca deixa o FREE vazio) ---
# 1) Págs 1..PRO_PAGS: pega TUDO (o topo = as de maior lucro = PRO).
# 2) Depois da pág PRO_PAGS: desce SÓ pra completar a faixa FREE (1–2%), pegando
#    FREE_ALVO surebets (prioriza as de ~2%; se faltar, completa com as de 1%).
# 3) Para quando junta FREE_ALVO do FREE (ou quando o lucro cai abaixo de 1%).
PRO_PAGS = 2                   # nº de páginas do topo que alimentam o PRO (era 5; modo manso)
FREE_ALVO = 6                  # quantas surebets da faixa FREE (1–2%) garantir (era 10)
FREE_MIN = 1.0                 # piso da faixa FREE (= FREE_LUCRO_MIN do backend)
FREE_MAX = 2.0                 # teto da faixa FREE (= FREE_LUCRO_MAX do backend)
HEADLESS = False               # janela visível (pra você logar). Vira True no servidor.

# Raspagem — mesma lógica da extensão, roda dentro da página.
JS_RASPAR = r"""
() => [...document.querySelectorAll("tbody.surebet_record")].map((rec) => {
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
    if (bk) { const p = bk.textContent.split("\n").map(s=>s.trim()).filter(s=>s&&s!==nome); sport = p.length?p[p.length-1]:""; }
    // descrição humana do mercado = tooltip do <abbr> dentro do .coeff.
    // Bootstrap 5 guarda o texto em data-bs-original-title (antes de iniciar, em title).
    const ab = co ? co.querySelector("abbr") : null;
    const tip = (e)=> e ? (e.getAttribute("data-bs-original-title")||e.getAttribute("title")||e.getAttribute("aria-label")||"") : "";
    let desc = tip(ab) || tip(co);
    return { bookmaker: nome, market: co?co.textContent.trim():"", odd, desc: (desc||"").trim(),
      teams: ev?((ev.querySelector("a")||ev).textContent||"").trim():"", sport,
      link: vl?vl.href:null, ev_href: ev?((ev.querySelector("a")||{}).href||null):null };
  }).filter(Boolean);
  legs.forEach((l,i)=> l.idx = i);
  const evA = rec.querySelector(".event a");
  return { id: rec.dataset.id, profit: parseFloat(rec.dataset.profit) || 0,
    start: parseInt(rec.dataset.startAt) || 0, legs, ev_href: evA?evA.href:null };
}).filter(r => r.legs.length === 2)
"""


# Raspagem das ODDS DE VALOR (valuebets). 1 perna por registro. Os números vêm
# nos data-attributes do tbody (confiáveis): data-value=odd, data-overvalue=valor%,
# data-probability=prob real. Casa/evento/mercado nos mesmos seletores da surebet.
JS_RASPAR_VALOR = r"""
() => [...document.querySelectorAll("tbody.valuebet_record")].map((rec) => {
  const txt = (s) => { const e = rec.querySelector(s); return e ? e.textContent.trim().replace(/\s+/g," ") : ""; };
  const num = (v) => { const n = parseFloat(v); return isFinite(n) ? n : 0; };
  const casa = txt(".bookmaker-name");
  const bk = rec.querySelector(".booker");
  let esporte = "";
  if (bk) { esporte = bk.textContent.trim().replace(casa, "").replace(/\s+/g," ").trim(); }
  const ev = rec.querySelector(".event");
  const event = ev ? ((ev.querySelector("a")||ev).textContent||"").trim().replace(/\s+/g," ") : "";
  const vl = rec.querySelector(".value_link");
  return {
    casa, esporte, event,
    mercado: txt(".coeff"),
    odd: num(rec.dataset.value),
    valor: num(rec.dataset.overvalue),
    probabilidade: num(rec.dataset.probability),
    start: parseInt(rec.dataset.startAt) || 0,
    link: vl ? vl.href : null,
  };
}).filter(r => r.odd > 1 && r.valor > 0)
"""


# Raspagem das APOSTAS DE INTERVALO (middles). Estrutura IGUAL à surebet: 2 pernas por
# registro (ex.: "Acima 80.5" numa casa + "Abaixo 81.5" na outra), cada uma com casa,
# mercado, odd e link. Só muda o tbody: middle_record em vez de surebet_record.
# data-profit = lucro MÁXIMO se o placar cair no intervalo (o "meio").
JS_RASPAR_MIDDLE = r"""
() => [...document.querySelectorAll("tbody.middle_record")].map((rec) => {
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
    if (bk) { const p = bk.textContent.split("\n").map(s=>s.trim()).filter(s=>s&&s!==nome); sport = p.length?p[p.length-1]:""; }
    const ab = co ? co.querySelector("abbr") : null;
    const tip = (e)=> e ? (e.getAttribute("data-bs-original-title")||e.getAttribute("title")||e.getAttribute("aria-label")||"") : "";
    let desc = tip(ab) || tip(co);
    return { bookmaker: nome, market: co?co.textContent.trim():"", odd, desc: (desc||"").trim(),
      teams: ev?((ev.querySelector("a")||ev).textContent||"").trim():"", sport,
      link: vl?vl.href:null, ev_href: ev?((ev.querySelector("a")||{}).href||null):null };
  }).filter(Boolean);
  legs.forEach((l,i)=> l.idx = i);
  const evA = rec.querySelector(".event a");
  return { id: rec.dataset.id, profit: parseFloat(rec.dataset.profit) || 0,
    start: parseInt(rec.dataset.startAt) || 0, legs, ev_href: evA?evA.href:null };
}).filter(r => r.legs.length === 2)
"""


def _e_surebet(u):
    return bool(u) and "surebet.com" in u


# --- RITMO da resolução de links (a parte que mais pesa no site) ---------------
# Antes: resolvia TODOS os links novos em rajada (centenas de redirects seguidos,
# sem pausa) — foi isso que disparou o throttle. Agora: um de cada vez, com pausa
# entre um e outro, e um ORÇAMENTO de links novos por ciclo compartilhado entre
# surebet + valuebets + middles. O que não couber fica pro próximo ciclo (o cache
# guarda o que já foi resolvido, então o backlog escoa devagar e nunca se perde).
LINKS_POR_CICLO = 15           # máx. de links NOVOS (não cacheados) tentados por funda
                               # (pior caso 15 × ~60s ≈ 15 min, cabe folgado no ciclo de 30)
LINK_PAUSA_SEG = (10.0, 25.0)  # pausa (min, máx) entre uma resolução e a próxima
_ORC = {"restam": 0}           # orçamento vivo do ciclo atual (zerado em cada funda)


def orcamento_novo_ciclo():
    """Chamado no INÍCIO de cada funda: recarrega o orçamento de links novos."""
    _ORC["restam"] = LINKS_POR_CICLO


def _tem_orcamento():
    return _ORC["restam"] > 0


def _gastar_orcamento():
    """Conta 1 tentativa de link novo e faz a pausa 'um de cada vez'."""
    _ORC["restam"] -= 1
    time.sleep(random.uniform(*LINK_PAUSA_SEG))


def resolver_link(ctx, pg, nav_url):
    """Segue o redirect do surebet (com a sessão logada) até a URL final da casa.
    Rápido via request (redirects HTTP); se travar em surebet (redirect via JS),
    abre a página. Guarda no cache."""
    if not _e_surebet(nav_url):
        return nav_url
    if nav_url in LINK_CACHE:
        return LINK_CACHE[nav_url]
    if not _tem_orcamento():      # acabou a cota deste ciclo: fica pro próximo
        return None
    _gastar_orcamento()           # conta a tentativa (mesmo se falhar) + pausa
    final = nav_url
    try:
        resp = ctx.request.get(nav_url, max_redirects=20, timeout=15000)
        if resp.url and not _e_surebet(resp.url):
            final = resp.url
    except Exception:
        pass
    if _e_surebet(final):   # ainda no surebet -> resolve via navegação (JS redirect)
        try:
            pg.goto(nav_url, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(1500)
            if not _e_surebet(pg.url):
                final = pg.url
        except Exception:
            pass
    if _e_surebet(final):   # NÃO resolveu: não vaza link do surebet; tenta de novo depois
        return None
    LINK_CACHE[nav_url] = final
    return final


def resolver_todos(ctx, bets):
    """Resolve os links de todas as pernas (usa cache; só resolve os novos)."""
    faltam = [leg for b in bets for leg in b.get("legs", [])
              if _e_surebet(leg.get("link")) and leg["link"] not in LINK_CACHE]
    if faltam:
        print(f"   resolvendo {min(len(faltam), _ORC['restam'])} de {len(faltam)} link(s) novo(s) "
              f"das casas, um por vez (cota do ciclo: {_ORC['restam']} · cache: {len(LINK_CACHE)})")
    pg = ctx.new_page()
    resolvidos = 0
    try:
        for b in bets:
            for leg in b.get("legs", []):
                if leg.get("link"):
                    antes = len(LINK_CACHE)
                    r = resolver_link(ctx, pg, leg["link"])
                    leg["link"] = r if (r and not _e_surebet(r)) else None
                    if len(LINK_CACHE) > antes:          # resolveu um link NOVO
                        resolvidos += 1
                        if resolvidos % 40 == 0:         # salva parcial: se morrer no meio, não perde o backlog já feito
                            _salvar_cache()
    finally:
        pg.close()
    if faltam:
        _salvar_cache()


INGEST_TOKEN = os.getenv("INGEST_TOKEN", "").strip()   # mesmo valor do Railway


def enviar(records, modo="merge"):
    if not records:
        return
    headers = {"X-Ingest-Token": INGEST_TOKEN} if INGEST_TOKEN else {}
    try:
        r = requests.post(SAAS, json={"records": records, "modo": modo},
                          headers=headers, timeout=25)
        print(f"   -> enviadas {len(records)} ao painel ({modo}, HTTP {r.status_code})")
    except Exception as e:
        print("   !! erro ao enviar:", e)


def enviar_valor(records):
    """Manda as ODDS DE VALOR pro endpoint SEPARADO (/api/ingest-valor)."""
    if not records:
        print("   valuebets: nada pra enviar.")
        return
    headers = {"X-Ingest-Token": INGEST_TOKEN} if INGEST_TOKEN else {}
    try:
        r = requests.post(SAAS_VALOR, json={"records": records}, headers=headers, timeout=25)
        print(f"   -> {len(records)} odds de valor enviadas (HTTP {r.status_code})")
    except Exception as e:
        print("   !! erro ao enviar valuebets:", str(e)[:100])


def resolver_todos_valor(ctx, recs):
    """Igual ao resolver_todos das surebets, mas pra 1 link por registro. Sem isso o
    link continua sendo do surebet.com e o painel joga fora (_link_casa), deixando o
    botão 'ABRIR NA CASA' morto."""
    faltam = [r for r in recs if _e_surebet(r.get("link")) and r["link"] not in LINK_CACHE]
    if faltam:
        print(f"   valuebets: resolvendo até {min(len(faltam), _ORC['restam'])} de {len(faltam)} "
              f"link(s) novo(s), um por vez (cota restante: {_ORC['restam']})")
    pg = ctx.new_page()
    try:
        for r in recs:
            if r.get("link"):
                final = resolver_link(ctx, pg, r["link"])
                r["link"] = final if (final and not _e_surebet(final)) else None
    finally:
        pg.close()
    if faltam:
        _salvar_cache()


def uma_varredura_valor(page, ctx):
    """Passada das ODDS DE VALOR — roda DEPOIS da surebet e é TOTALMENTE isolada:
    qualquer erro aqui não afeta a surebet (que já foi enviada). Usa o filtro que
    você salvou na página de valuebets (mesmas casas)."""
    page.goto(URL_VALOR, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("tbody.valuebet_record", timeout=20000)
    except Exception:
        print("   valuebets: sem registros (filtro vazio ou sem acesso).")
        return
    page.wait_for_timeout(1000)
    vistos, todos, pag = set(), [], 0
    while pag < MAX_PAG_VALOR and len(todos) < MAX_VALOR:
        try:
            page.wait_for_selector("tbody.valuebet_record", timeout=15000)
        except Exception:
            break
        recs = page.evaluate(JS_RASPAR_VALOR)
        novos = 0
        for r in recs:
            key = (r.get("casa"), r.get("event"), r.get("mercado"), r.get("odd"))
            if len(todos) >= MAX_VALOR:
                break
            if r.get("odd", 0) > 1 and key not in vistos:
                vistos.add(key); todos.append(r); novos += 1
        pag += 1
        print(f"   valuebets pág {pag}: {len(recs)} na tela, {novos} novas (acum {len(todos)})")
        if pag > 1 and novos == 0:
            break
        link = page.query_selector("a:has-text('próximo'), a:has-text('Próximo'), a:has-text('next')")
        if not link:
            break
        id_antes = page.evaluate(
            "() => { const r=document.querySelector('tbody.valuebet_record'); return r?r.dataset.id:''; }")
        time.sleep(15.0 + random.random() * 15.0)  # clique bem aos poucos (15–30s)
        try:
            link.click()
            page.wait_for_function(
                "(a) => { const r=document.querySelector('tbody.valuebet_record'); return r && r.dataset.id !== a; }",
                arg=id_antes, timeout=20000)
        except Exception:
            break
    try:                       # link da casa: se falhar, manda sem link (não perde a odd)
        resolver_todos_valor(ctx, todos)
    except Exception as e:
        print("   !! valuebets: falha ao resolver links:", str(e)[:100])
    com_link = sum(1 for r in todos if r.get("link"))
    print(f">> Valuebets: {len(todos)} odds de valor em {pag} pág. "
          f"({com_link} com link da casa) — enviando.")
    enviar_valor(todos)


def _sem_nan(o):
    """Troca qualquer NaN/Infinity por 0.0 (recursivo). JSON não aceita NaN e um único
    valor quebrado (ex.: data-profit vazio) derrubava o envio de TODAS as apostas."""
    if isinstance(o, float):
        return o if (o == o and o not in (float("inf"), float("-inf"))) else 0.0
    if isinstance(o, dict):
        return {k: _sem_nan(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sem_nan(v) for v in o]
    return o


def enviar_middle(records):
    """Manda as APOSTAS DE INTERVALO pro endpoint SEPARADO (/api/ingest-middle)."""
    if not records:
        print("   middles: nada pra enviar.")
        return
    records = _sem_nan(records)
    headers = {"X-Ingest-Token": INGEST_TOKEN} if INGEST_TOKEN else {}
    try:
        r = requests.post(SAAS_MIDDLE, json={"records": records}, headers=headers, timeout=25)
        print(f"   -> {len(records)} apostas de intervalo enviadas (HTTP {r.status_code})")
    except Exception as e:
        print("   !! erro ao enviar middles:", str(e)[:100])


def resolver_todos_middle(ctx, recs):
    """Resolve o link da casa das DUAS pernas de cada middle (mesmo cache das surebets).
    Sem isso o link continua sendo do surebet.com e o painel joga fora."""
    faltam = [g for r in recs for g in r.get("legs", [])
              if _e_surebet(g.get("link")) and g["link"] not in LINK_CACHE]
    if faltam:
        print(f"   middles: resolvendo até {min(len(faltam), _ORC['restam'])} de {len(faltam)} "
              f"link(s) novo(s), um por vez (cota restante: {_ORC['restam']})")
    pg = ctx.new_page()
    try:
        for r in recs:
            for g in r.get("legs", []):
                if g.get("link"):
                    final = resolver_link(ctx, pg, g["link"])
                    g["link"] = final if (final and not _e_surebet(final)) else None
    finally:
        pg.close()
    if faltam:
        _salvar_cache()


def uma_varredura_middle(page, ctx):
    """Passada das APOSTAS DE INTERVALO — roda DEPOIS da surebet e das valuebets, e é
    TOTALMENTE isolada: qualquer erro aqui não afeta as outras. Usa o filtro que você
    salvou na página de middles (mesmas casas)."""
    page.goto(URL_MIDDLE, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("tbody.middle_record", timeout=20000)
    except Exception:
        print("   middles: sem registros (filtro vazio, sem acesso ou seletor mudou).")
        return
    page.wait_for_timeout(1000)
    vistos, todos, pag = set(), [], 0
    while pag < MAX_PAG_MIDDLE and len(todos) < MAX_MIDDLE:
        try:
            page.wait_for_selector("tbody.middle_record", timeout=15000)
        except Exception:
            break
        recs = page.evaluate(JS_RASPAR_MIDDLE)
        novos = 0
        for r in recs:
            key = r.get("id") or tuple(sorted((g.get("bookmaker", ""), g.get("market", ""))
                                              for g in r.get("legs", [])))
            if len(todos) >= MAX_MIDDLE:
                break
            if key not in vistos:
                vistos.add(key); todos.append(r); novos += 1
        pag += 1
        print(f"   middles pág {pag}: {len(recs)} na tela, {novos} novas (acum {len(todos)})")
        if pag > 1 and novos == 0:
            break
        link = page.query_selector("a:has-text('próximo'), a:has-text('Próximo'), a:has-text('next')")
        if not link:
            break
        id_antes = page.evaluate(
            "() => { const r=document.querySelector('tbody.middle_record'); return r?r.dataset.id:''; }")
        time.sleep(15.0 + random.random() * 15.0)  # clique bem aos poucos (15–30s)
        try:
            link.click()
            page.wait_for_function(
                "(a) => { const r=document.querySelector('tbody.middle_record'); return r && r.dataset.id !== a; }",
                arg=id_antes, timeout=20000)
        except Exception:
            break
    try:                       # link das casas: se falhar, manda sem link (não perde a aposta)
        resolver_todos_middle(ctx, todos)
    except Exception as e:
        print("   !! middles: falha ao resolver links:", str(e)[:100])
    com_link = sum(1 for r in todos for g in r.get("legs", []) if g.get("link"))
    print(f">> Middles: {len(todos)} apostas de intervalo em {pag} pág. "
          f"({com_link} pernas com link da casa) — enviando.")
    enviar_middle(todos)


def esperar_login(page):
    """Espera ESTAR LOGADO de verdade — não basta ter lista (a versão pública
    também tem). Detecta o botão 'Fazer login': se ele some, está logado."""
    print(">> Aguardando LOGIN na sua conta do surebet.com (entre na janela)...")
    avisou = False
    for _ in range(400):  # ~20 min de tolerância
        try:
            deslogado = page.query_selector("text=Fazer login") is not None
            tem_lista = page.query_selector("tbody.surebet_record") is not None
            if tem_lista and not deslogado:
                print(">> Logado! Iniciando varredura.")
                return True
            if deslogado and not avisou:
                print(">> A janela está DESLOGADA — faça login na sua conta paga.")
                avisou = True
        except Exception:
            pass
        time.sleep(3)
    return False


def uma_varredura_rapida(page, ctx):
    """Passada RÁPIDA — SÓ a página 1 (as de MAIOR lucro = as mais frescas), a cada
    FAST_SEG. Manda em modo 'snapshot_acima': o site troca só a faixa de topo
    (lucro >= o menor lucro visto na página 1). Efeito: surebet nova aparece na
    hora, a que expirou no topo some, e as de baixo (página 2+) NÃO são tocadas —
    quem cuida delas é a varredura funda. Nada duplicado, nada velho no dashboard.

    É UM carregamento de página (sem folhear) — leve, não pesa no surebet.com."""
    try:
        page.goto(URL_LISTA, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("tbody.surebet_record", timeout=15000)
    except Exception:
        return                      # página 1 não carregou: não manda nada (não zera)
    page.wait_for_timeout(700)
    recs = page.evaluate(JS_RASPAR)
    uteis, vistos = [], set()
    for r in recs:
        if r.get("profit", 0) >= MIN_PROFIT and r.get("id") and r["id"] not in vistos:
            vistos.add(r["id"])
            uteis.append(r)
    if not uteis:
        return                      # sem nada útil na página 1: não mexe no feed
    try:                            # resolve link só dos NOVOS (cache cobre o resto)
        resolver_todos(ctx, uteis)
    except Exception as e:
        print("   !! rápida: erro ao resolver links:", str(e)[:80])
    print(f">> Passada RÁPIDA (pág 1): {len(uteis)} apostas — enviando (snapshot_acima).")
    enviar(uteis, modo="snapshot_acima")


def uma_varredura(page, ctx):
    page.goto(URL_LISTA, wait_until="domcontentloaded", timeout=60000)
    if not esperar_login(page):
        print("!! Sem lista/login. Faça login na janela e ele tenta no próximo ciclo.")
        return
    vistos, todos, pag, completo = set(), [], 0, False
    while pag < MAX_PAGINAS:
        # os registros carregam via JS DEPOIS do load — espera aparecerem
        try:
            page.wait_for_selector("tbody.surebet_record", timeout=20000)
        except Exception:
            print("   sem registros nesta página (fim ou bloqueio).")
            break
        page.wait_for_timeout(1000)
        recs = page.evaluate(JS_RASPAR)
        # só interessa lucro >= MIN_PROFIT (1,0). Lista decrescente: quando
        # aparecer algo abaixo disso, chegamos no fim útil (raspagem COMPLETA).
        chegou_piso = any(r.get("profit", 99) < MIN_PROFIT for r in recs)
        novos = 0        # realmente adicionados (respeita PRO/FREE)
        brutos = 0       # ids novos vistos na página (detecta fim real da lista)
        for r in recs:
            rid = r.get("id")
            if not (r.get("profit", 0) >= MIN_PROFIT and rid and rid not in vistos):
                continue
            brutos += 1
            eh_free = FREE_MIN <= r.get("profit", 0) <= FREE_MAX
            # Depois das PRO_PAGS páginas do topo, só completa a faixa FREE (1–2%):
            # ignora o PRO extra do fundo (marca como visto pra não recontar).
            if pag >= PRO_PAGS and not eh_free:
                vistos.add(rid)
                continue
            vistos.add(rid)
            todos.append(r)
            novos += 1
        pag += 1
        free_ct = sum(1 for r in todos if FREE_MIN <= r.get("profit", 0) <= FREE_MAX)
        print(f"   página {pag}: {len(recs)} na tela, {novos} úteis "
              f"(acum {len(todos)} · FREE 1–2%: {free_ct}/{FREE_ALVO})")
        if chegou_piso:
            print(f"   chegou no piso de {MIN_PROFIT}% — raspagem completa.")
            completo = True
            break
        # PRO já garantido (>= PRO_PAGS págs) E FREE completo -> para aqui (leve).
        if pag >= PRO_PAGS and free_ct >= FREE_ALVO:
            print(f"   PRO (págs 1–{PRO_PAGS}) + {free_ct} do FREE (1–2%) — suficiente, parando.")
            completo = True
            break
        if pag > 1 and brutos == 0:
            print("   fim (sem novidade).")
            completo = True
            break
        if pag >= MAX_PAGINAS:
            print(f"   {MAX_PAGINAS} página(s) — teto do modo manso, parando (snapshot).")
            completo = True
            break
        # próxima página: CLICA no "próximo »" e espera a lista TROCAR
        link = page.query_selector("a:has-text('próximo'), a:has-text('Próximo'), a:has-text('next')")
        if not link:
            print("   fim (sem página seguinte).")
            completo = True
            break
        id_antes = page.evaluate(
            "() => { const r=document.querySelector('tbody.surebet_record'); return r?r.dataset.id:''; }")
        time.sleep(15.0 + random.random() * 15.0)  # clique bem aos poucos (15–30s entre páginas)
        try:
            link.click()
            page.wait_for_function(
                "(a) => { const r=document.querySelector('tbody.surebet_record'); return r && r.dataset.id !== a; }",
                arg=id_antes, timeout=25000)
        except Exception:
            print("   página seguinte não carregou (parcial — envio como merge).")
            break
    # COMPLETO -> snapshot (substitui, remove as que sumiram). PARCIAL -> merge.
    modo = "snapshot" if completo else "merge"
    # POSTA OS NOMES JÁ (sem esperar os links): resolver centenas de redirects leva
    # ~20 min e o cliente ficaria vendo o painel velho/embaralhado esse tempo todo.
    # 1ª postagem = nomes na hora (links ainda do surebet -> o servidor zera com
    # _link_casa, botão fica desligado); depois resolvo e reposto com os links.
    print(f">> Varredura {'COMPLETA' if completo else 'PARCIAL'}: {len(todos)} apostas em {pag} pág. — enviando nomes JÁ ({modo}).")
    enviar(todos, modo)
    # agora resolve os links das casas (redirect surebet -> URL final) e reposta
    if todos:
        try:
            resolver_todos(ctx, todos)
            print(f">> Links resolvidos — repostando com os links ({modo}).")
            enviar(todos, modo)
        except Exception as e:
            print("   !! erro ao resolver links:", str(e)[:100])
    # volta pra página 1 (não altera filtro)
    try:
        page.goto(URL_LISTA, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass


def _limpar_lock():
    """Remove os locks do perfil (SingletonLock etc.). Se o Chromium travou/caiu,
    esses arquivos ficam presos e impedem reabrir com o mesmo perfil."""
    for nome in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(PERFIL, nome))
        except OSError:
            pass


# ANTI-ANTI-SCRAPING: o surebet EMBARALHA os nomes dos eventos (vira anagrama,
# ex.: "Real Madrid" -> "Aerl Maddri") quando acha que a aba está SEM FOCO /
# ESCONDIDA — proteção contra robô. Como o robô roda em SEGUNDO PLANO (janela
# atrás de outras), ele caía nessa e mandava nomes embaralhados pro painel.
# Este script força a página a SEMPRE se achar visível e com foco, e engole os
# eventos de blur/visibilitychange. Roda ANTES de qualquer script do site, em
# toda navegação (add_init_script no contexto).
_JS_SEMPRE_VISIVEL = r"""
(() => {
  const fixar = (obj, chave, valor) => {
    try { Object.defineProperty(obj, chave, {configurable: true, get: () => valor}); } catch (e) {}
  };
  // Trava as flags de visibilidade tanto na INSTÂNCIA (document) quanto no
  // PROTÓTIPO (Document.prototype) — algumas detecções leem o getter direto do
  // protótipo pra furar o override só-de-instância.
  const alvos = [document];
  try { alvos.push(Document.prototype); } catch (e) {}
  try { const pr = Object.getPrototypeOf(document); if (pr && alvos.indexOf(pr) < 0) alvos.push(pr); } catch (e) {}
  for (const t of alvos) {
    fixar(t, 'hidden', false);
    fixar(t, 'visibilityState', 'visible');
    fixar(t, 'webkitHidden', false);
    fixar(t, 'webkitVisibilityState', 'visible');
    fixar(t, 'wasDiscarded', false);
  }
  try { document.hasFocus = () => true; } catch (e) {}
  try { Document.prototype.hasFocus = () => true; } catch (e) {}
  // Engole os eventos que sinalizam "perdi o foco/fiquei escondido" (captura,
  // stopImmediatePropagation) — pega addEventListener E handlers via propriedade.
  const engolir = ['visibilitychange', 'webkitvisibilitychange', 'mozvisibilitychange',
                   'msvisibilitychange', 'blur', 'focusout', 'pagehide', 'freeze'];
  const parar = (e) => { try { e.stopImmediatePropagation(); } catch (_) {} };
  for (const ev of engolir) {
    try { window.addEventListener(ev, parar, true); } catch (e) {}
    try { document.addEventListener(ev, parar, true); } catch (e) {}
  }
  // Neutraliza handlers atribuídos por propriedade (ex.: document.onvisibilitychange = fn):
  // o get devolve null e o set é no-op, então o site nunca registra o embaralhador.
  for (const t of [window, document]) {
    for (const p of ['onblur', 'onvisibilitychange', 'onwebkitvisibilitychange',
                     'onpagehide', 'onfreeze']) {
      try { Object.defineProperty(t, p, {configurable: true, get: () => null, set: () => {} }); } catch (e) {}
    }
  }
})();
"""


def _abrir_ctx(p):
    """Abre (ou reabre) o navegador persistente e devolve (ctx, page)."""
    _limpar_lock()
    args = [
        "--disable-blink-features=AutomationControlled",
        # Impedem o Chromium de marcar a janela como "em segundo plano/oculta"
        # quando ela está atrás de outras — é isso que fazia o surebet embaralhar.
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
    ]
    if sys.platform.startswith("linux"):
        # No VPS (Linux, rodando como root) o Chromium EXIGE --no-sandbox; e
        # --disable-dev-shm-usage evita travadas/crash por /dev/shm pequeno no
        # servidor. No Windows nada disso entra (comportamento idêntico ao de antes).
        args += ["--no-sandbox", "--disable-setuid-sandbox",
                 "--disable-dev-shm-usage", "--disable-gpu"]
    ctx = p.chromium.launch_persistent_context(
        PERFIL, headless=HEADLESS,
        viewport={"width": 1280, "height": 900},
        args=args,
    )
    # Aplica o "sempre visível" em TODA página/navegação deste contexto.
    try:
        ctx.add_init_script(_JS_SEMPRE_VISIVEL)
    except Exception:
        pass
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def _ctx_vivo(page):
    """True se o navegador ainda responde. Janela fechada/travada -> False,
    e aí o loop reabre sozinho (blindagem contra a janela ser fechada sem querer)."""
    try:
        if page is None or page.is_closed():
            return False
        page.evaluate("1")   # navegador morto lança aqui
        return True
    except Exception:
        return False


def main():
    with sync_playwright() as p:
        ctx, page = _abrir_ctx(p)
        # SIGTERM (systemd stop/restart) -> encerra COM CALMA: vira KeyboardInterrupt
        # e o finally fecha o navegador (ctx.close), SALVANDO a sessão no pw_profile.
        # Sem isso o Chromium morre a seco e o próximo start pede login de novo.
        def _sair(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _sair)
        print("=" * 60)
        print(" ROBÔ SUREBET (Playwright) — deixe a janela aberta.")
        print("=" * 60)
        prox_funda = 0.0            # 0 = faz a FUNDA já na 1ª volta (igual antes)
        ciclos = 0                  # nº de varreduras FUNDAS feitas nesta sessão
        RECICLA_A_CADA = 5          # recicla o navegador a cada N fundas (~50 min):
                                    # fecha e reabre p/ liberar a memória que o Chromium
                                    # acumula. É o que evita a "morte silenciosa" por OOM
                                    # (o Windows matando o processo inteiro, sem erro).
        try:
            while True:
                # BLINDAGEM: se a janela foi fechada / o navegador travou, reabre sozinho
                # em vez de ficar errando pra sempre ou derrubar o processo.
                if not _ctx_vivo(page):
                    print("!! navegador caiu (janela fechada/travou) — reabrindo sozinho...")
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    try:
                        ctx, page = _abrir_ctx(p)
                        print(">> navegador reaberto.")
                    except Exception as e:
                        print("!! não consegui reabrir agora:", str(e)[:150], "— tento em 30s.")
                        time.sleep(30)
                        continue

                if time.time() >= prox_funda:
                    # --- VARREDURA FUNDA (completa): surebet + valor + middle ---
                    # Agenda a PRÓXIMA já AQUI (início), não no fim: assim o intervalo de
                    # CICLO_MIN é contado do começo de uma funda ao começo da próxima —
                    # batimento honesto de 10 em 10 min. Se a funda passar de CICLO_MIN
                    # (ex.: resolução de links longa), prox_funda já expirou e a próxima
                    # roda na hora, sem esticar o ciclo.
                    prox_funda = time.time() + CICLO_MIN * 60
                    orcamento_novo_ciclo()                   # cota de links novos deste ciclo
                    try:
                        uma_varredura(page, ctx)             # PRINCIPAL: surebet (todas as págs)
                    except Exception as e:
                        print("!! erro na varredura:", str(e)[:150])
                    if VALOR_ATIVO:                          # EXTRA: odds de valor (isolada)
                        try:
                            uma_varredura_valor(page, ctx)
                        except Exception as e:
                            print("!! erro nas valuebets (surebet NÃO afetada):", str(e)[:150])
                    if MIDDLE_ATIVO and _ctx_vivo(page):     # EXTRA: apostas de intervalo (isolada)
                        try:
                            uma_varredura_middle(page, ctx)
                        except Exception as e:
                            print("!! erro nas middles (surebet NÃO afetada):", str(e)[:150])
                    ciclos += 1
                    # RECICLA o navegador de tempos em tempos pra não acumular memória
                    # (evita o OOM que mata o processo silenciosamente). O login persiste
                    # no pw_profile, então reabrir NÃO pede login de novo.
                    if ciclos % RECICLA_A_CADA == 0:
                        print(">> reciclando o navegador (libera memória, evita OOM)...")
                        try:
                            ctx.close()
                        except Exception:
                            pass
                        try:
                            ctx, page = _abrir_ctx(p)
                            print(">> navegador reciclado.")
                        except Exception as e:
                            print("!! falha ao reciclar o navegador:", str(e)[:150])
                    if FAST_ATIVO:
                        print(f">> Funda ok. Passadas RÁPIDAS a cada {FAST_SEG}s; "
                              f"próxima funda em {CICLO_MIN} min.\n")
                    else:
                        print(f">> Funda ok (MODO MANSO: sem rápidas). "
                              f"Próxima funda em {CICLO_MIN} min.\n")
                elif FAST_ATIVO:
                    # --- PASSADA RÁPIDA (só página 1) — o "quase ao vivo" ---
                    try:
                        uma_varredura_rapida(page, ctx)
                    except Exception as e:
                        print("!! erro na passada rápida (surebet NÃO afetada):", str(e)[:150])

                # Se o navegador morreu no meio, reabre JÁ (não espera o timer).
                if not _ctx_vivo(page):
                    print("!! navegador morreu durante a varredura — reabrindo já.")
                    continue
                # Modo manso: dorme até a próxima funda (acorda no máx. a cada 60s pra
                # notar se o navegador caiu). Com rápidas ligadas, espera FAST_SEG.
                if FAST_ATIVO:
                    time.sleep(FAST_SEG)
                else:
                    time.sleep(max(5.0, min(prox_funda - time.time(), 60.0)))
        except KeyboardInterrupt:
            print(">> Encerrando (SIGTERM): fechando o navegador p/ SALVAR a sessão…")
        finally:
            try:
                ctx.close()   # flush dos cookies -> próximo start NÃO pede login
            except Exception:
                pass


if __name__ == "__main__":
    main()
