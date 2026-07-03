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

import random
import time
import requests
from playwright.sync_api import sync_playwright

SAAS = "https://web-production-a41df.up.railway.app/api/ingest"
URL_LISTA = "https://pt.surebet.com/surebets"
PERFIL = "pw_profile"          # sessão do Chrome fica salva aqui (login persiste)
CICLO_MIN = 10                 # minutos entre varreduras
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
    return { bookmaker: nome, market: co?co.textContent.trim():"", odd,
      teams: ev?((ev.querySelector("a")||ev).textContent||"").trim():"", sport,
      link: vl?vl.href:null };
  }).filter(Boolean);
  return { id: rec.dataset.id, profit: parseFloat(rec.dataset.profit),
    start: parseInt(rec.dataset.startAt), legs };
}).filter(r => r.legs.length === 2)
"""


def enviar(records, modo="merge"):
    if not records:
        return
    try:
        r = requests.post(SAAS, json={"records": records, "modo": modo}, timeout=25)
        print(f"   -> enviadas {len(records)} ao painel ({modo}, HTTP {r.status_code})")
    except Exception as e:
        print("   !! erro ao enviar:", e)


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


def uma_varredura(page):
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
                uma_varredura(page)
            except Exception as e:
                print("!! erro na varredura:", str(e)[:150])
            print(f">> Próxima varredura em {CICLO_MIN} min.\n")
            time.sleep(CICLO_MIN * 60)


if __name__ == "__main__":
    main()
