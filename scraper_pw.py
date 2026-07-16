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
    try:
        json.dump(LINK_CACHE, open(CACHE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass

SAAS = "https://web-production-a41df.up.railway.app/api/ingest"
SAAS_VALOR = SAAS.replace("/api/ingest", "/api/ingest-valor")   # ODDS DE VALOR (separado)
URL_LISTA = "https://pt.surebet.com/surebets"
URL_VALOR = "https://pt.surebet.com/valuebets"                  # aba "Apostas de valor"
MAX_PAG_VALOR = 6              # valuebets já vêm filtradas nas suas casas — poucas págs
MAX_VALOR = 150               # teto de segurança
PERFIL = "pw_profile"          # sessão do Chrome fica salva aqui (login persiste)
CICLO_MIN = 10                 # minutos entre varreduras
VALOR_ATIVO = False            # liga/desliga a passada de ODDS DE VALOR (deixe True p/ raspar valuebets)
MAX_PAGINAS = 40
MIN_PROFIT = 0.70              # PARA quando o lucro chega aqui (lista é decrescente).
                              # FREE = 0,70–1% · PRO = 1–25% · abaixo de 0,70 ignora.
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
      link: vl?vl.href:null };
  }).filter(Boolean);
  return { id: rec.dataset.id, profit: parseFloat(rec.dataset.profit),
    start: parseInt(rec.dataset.startAt), legs };
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


def _e_surebet(u):
    return bool(u) and "surebet.com" in u


def resolver_link(ctx, pg, nav_url):
    """Segue o redirect do surebet (com a sessão logada) até a URL final da casa.
    Rápido via request (redirects HTTP); se travar em surebet (redirect via JS),
    abre a página. Guarda no cache."""
    if not _e_surebet(nav_url):
        return nav_url
    if nav_url in LINK_CACHE:
        return LINK_CACHE[nav_url]
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
        print(f"   resolvendo {len(faltam)} link(s) novo(s) das casas… (cache: {len(LINK_CACHE)})")
    pg = ctx.new_page()
    try:
        for b in bets:
            for leg in b.get("legs", []):
                if leg.get("link"):
                    r = resolver_link(ctx, pg, leg["link"])
                    leg["link"] = r if (r and not _e_surebet(r)) else None
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


def uma_varredura_valor(page):
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
        time.sleep(2.0 + random.random() * 2)
        try:
            link.click()
            page.wait_for_function(
                "(a) => { const r=document.querySelector('tbody.valuebet_record'); return r && r.dataset.id !== a; }",
                arg=id_antes, timeout=20000)
        except Exception:
            break
    print(f">> Valuebets: {len(todos)} odds de valor em {pag} pág. — enviando.")
    enviar_valor(todos)


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
        # só interessa lucro >= MIN_PROFIT (0,70). Lista decrescente: quando
        # aparecer algo abaixo disso, chegamos no fim útil (raspagem COMPLETA).
        chegou_piso = any(r.get("profit", 99) < MIN_PROFIT for r in recs)
        novos = 0
        for r in recs:
            if r.get("profit", 0) >= MIN_PROFIT and r.get("id") and r["id"] not in vistos:
                vistos.add(r["id"])
                todos.append(r)
                novos += 1
        pag += 1
        print(f"   página {pag}: {len(recs)} na tela, {novos} úteis (acumulado {len(todos)})")
        if chegou_piso:
            print(f"   chegou no piso de {MIN_PROFIT}% — raspagem completa.")
            completo = True
            break
        if pag > 1 and novos == 0:
            print("   fim (sem novidade).")
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
        time.sleep(2.5 + random.random() * 2.5)   # ritmo humano
        try:
            link.click()
            page.wait_for_function(
                "(a) => { const r=document.querySelector('tbody.surebet_record'); return r && r.dataset.id !== a; }",
                arg=id_antes, timeout=25000)
        except Exception:
            print("   página seguinte não carregou (parcial — envio como merge).")
            break
    # resolve os links das casas (redirect surebet -> URL final) antes de enviar
    if todos:
        try:
            resolver_todos(ctx, todos)
        except Exception as e:
            print("   !! erro ao resolver links:", str(e)[:100])
    # COMPLETO -> snapshot (substitui, remove as que sumiram). PARCIAL -> merge.
    modo = "snapshot" if completo else "merge"
    print(f">> Varredura {'COMPLETA' if completo else 'PARCIAL'}: {len(todos)} apostas em {pag} pág. — enviando ({modo}).")
    enviar(todos, modo)
    # volta pra página 1 (não altera filtro)
    try:
        page.goto(URL_LISTA, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PERFIL, headless=HEADLESS,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print("=" * 60)
        print(" ROBÔ SUREBET (Playwright) — deixe a janela aberta.")
        print("=" * 60)
        while True:
            try:
                uma_varredura(page, ctx)                 # PRINCIPAL: surebet
            except Exception as e:
                print("!! erro na varredura:", str(e)[:150])
            if VALOR_ATIVO:                              # EXTRA: odds de valor (isolada, opcional)
                try:
                    uma_varredura_valor(page)
                except Exception as e:
                    print("!! erro nas valuebets (surebet NÃO afetada):", str(e)[:150])
            print(f">> Próxima varredura em {CICLO_MIN} min.\n")
            time.sleep(CICLO_MIN * 60)


if __name__ == "__main__":
    main()
