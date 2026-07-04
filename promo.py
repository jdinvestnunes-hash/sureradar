"""
promo.py — fluxo de marketing automático no grupo FREE do Telegram.

Estratégia (definida pelo usuário, 03/07/2026):
- 2 entradas REAIS por dia (10h e 19h de Brasília), com lucro entre 3% e 8%,
  e SOMENTE nas casas: Betano, Bet365, SuperBet, Stake e Novibet.
  Vão completas (com links) — é a amostra grátis que prova o valor.
- Entre elas, mensagens de PROVA SOCIAL / FOMO puxando para o PRO.

Roda numa thread de fundo iniciada pelo app.py no startup.
"""

import io
import threading
import time as _time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _BR = ZoneInfo("America/Sao_Paulo")
except Exception:                       # pragma: no cover
    _BR = None

import config
import feed
import notifier

# Intervalo entre as entradas normais (minutos). TESTE=10, PRODUÇÃO=60.
INTERVALO_MIN = getattr(config, "TELEGRAM_POST_INTERVAL_MIN", 10)
# Horários (Brasília) das 2 entradas de ~5% do dia.
HORARIOS_5PCT = ["17:00", "18:00"]
# Faixas de lucro (min, max) — normal (a cada intervalo) e as 2 de 5% do dia.
FAIXA_NORMAL = (1.0001, 3.0)
FAIXA_5PCT = (4.0, 6.5)
# Preferência de casas (as 2 pernas); se não achar, relaxa.
import re as _re
_CASAS_OK = _re.compile(r"^(betano|bet365|superbet|stake|novibet)", _re.I)


def _casas_permitidas(sb):
    """True se TODAS as pernas são de casas permitidas (Betano/Bet365/SuperBet/
    Stake/Novibet — inclui variações '(BR)')."""
    for leg in sb.get("legs", []):
        nome = str(leg.get("bookmaker_label") or leg.get("bookmaker") or "")
        nome = nome.replace(" ", "").replace("(BR)", "").strip()
        if not _CASAS_OK.match(nome):
            return False
    return True

SOCIAL_MSGS = [
    "🔥 Mais um dia de green no automático pra quem é PRO.",
    "💚 No grátis você pega as pequenas. No PRO, as de 8%, 9%, 15%...",
    "📈 UMA entrada de 5% já paga a mensalidade do PRO. Faça as contas.",
    "⏰ As entradas gordas duram minutos — no PRO você recebe na hora.",
    "🤑 Enquanto você espera, os assinantes já fecharam o lucro de hoje.",
    "🎯 Surebet não é sorte, é matemática. E o PRO te entrega mastigado.",
    "🚀 A virada começa quando você para de apostar no achismo.",
    "💸 Aposta com LUCRO GARANTIDO existe — e tá tudo no PRO.",
]

_parar = threading.Event()
_thread = None
_estado = {"dia": None, "slots": set(), "social": set(), "postados": set()}
_social_i = 0


def _agora():
    return datetime.now(_BR) if _BR else datetime.utcnow()


def _reset_dia(dia):
    _estado.update({"dia": dia, "slots": set(), "social": set(), "postados": set()})


def _pegar(cands):
    for c in cands:
        if c["id"] not in _estado["postados"]:
            return c
    return None


def _pegar_faixa(lo, hi):
    """Melhor entrada não-postada-hoje na faixa [lo, hi]. Prefere as casas
    conhecidas; se não achar, relaxa casas e depois a faixa — sempre tenta postar."""
    cands = [s for s in feed.get_surebets(min_profit=lo, max_profit=hi) if _casas_permitidas(s)]
    if not cands:                                   # relaxa casas
        cands = feed.get_surebets(min_profit=lo, max_profit=hi)
    if not cands:                                   # relaxa a faixa (pega o que tiver >=1%)
        cands = feed.get_surebets(min_profit=1.0001)
    if not cands:                                   # último recurso: qualquer uma
        cands = feed.get_surebets(min_profit=0.0)
    return _pegar(cands)


# ---------------------------------------------------------------------------
# Imagem "printada" com mercado + casa BORRADOS (teaser VIP)
# ---------------------------------------------------------------------------
def _fonte(sz, bold=True):
    from PIL import ImageFont
    nomes = (["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"]
             if bold else
             ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"])
    for n in nomes:
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def gerar_teaser(sb):
    """PNG (bytes) que REPLICA fielmente o card do painel (mesmo layout, cores e
    a barra verde 'RETORNO CERTO') — parece um print do site. Só o MERCADO e a
    CASA ficam BORRADOS; jogo, lucro e odds à mostra (gera desejo)."""
    from PIL import Image, ImageDraw, ImageFilter

    # cores EXATAS do style.css
    BG, SURF, SURF2 = (10, 14, 23), (19, 27, 43), (24, 34, 54)
    BORDER, BSOFT = (34, 48, 73), (26, 37, 55)
    TEXT, DIM, MUTE = (238, 242, 248), (154, 167, 189), (100, 116, 139)
    CYAN, GREEN, BARBG = (34, 211, 238), (61, 220, 151), (11, 23, 48)

    W, H = 768, 492
    x0, y0, cardW = 24, 24, 720
    x1, yB = x0 + cardW, 468
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    blur = []

    # card
    d.rounded_rectangle([x0, y0, x1, yB], radius=26, fill=SURF, outline=BSOFT, width=2)

    # ---- cabeçalho (liga + horário) ----
    hH = 54
    d.rectangle([x0 + 2, y0 + 2, x1 - 2, y0 + hH], fill=SURF2)
    d.line([x0 + 2, y0 + hH, x1 - 2, y0 + hH], fill=BSOFT, width=2)
    cx, cy = x0 + 28, y0 + hH // 2 + 1
    d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline=CYAN, width=2)
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=CYAN)
    liga = str(sb.get("sport_label") or sb.get("sport") or "FUTEBOL").upper()[:34]
    d.text((x0 + 48, y0 + hH // 2 - 10), liga, font=_fonte(19), fill=DIM)
    tt = str(sb.get("commence_br", "") or "")
    if tt:
        tw = d.textlength(tt, font=_fonte(18, False))
        d.text((x1 - 26 - tw, y0 + hH // 2 - 9), tt, font=_fonte(18, False), fill=MUTE)

    # ---- corpo ----
    bx = x0 + 28
    y = y0 + hH + 22
    d.text((bx, y), str(sb.get("event", ""))[:40], font=_fonte(30), fill=TEXT)
    y += 46
    # mercado (BORRADO)
    mkt = str(sb.get("market_label", "") or (sb.get("legs", [{}])[0].get("outcome", "")))
    d.text((bx, y), mkt[:44], font=_fonte(24), fill=CYAN)
    blur.append((bx - 4, y - 4, bx + 540, y + 32))
    y += 44

    # ---- 2 caixas de aposta ----
    for leg in sb.get("legs", [])[:2]:
        boxT, boxB = y, y + 90
        d.rounded_rectangle([bx, boxT, x1 - 28, boxB], 18, fill=BG, outline=BORDER, width=2)
        # outcome (mercado) BORRADO — cobre até antes da odd
        d.text((bx + 22, boxT + 17), str(leg.get("outcome", ""))[:34], font=_fonte(25), fill=TEXT)
        blur.append((bx + 14, boxT + 12, x1 - 150, boxT + 46))
        # casa BORRADA
        casa = str(leg.get("bookmaker_label", leg.get("bookmaker", "")))
        d.text((bx + 22, boxT + 52), casa, font=_fonte(21), fill=CYAN)
        blur.append((bx + 14, boxT + 48, bx + 300, boxT + 82))
        # odd (À MOSTRA)
        odd = f"{float(leg.get('odd', 0)):.2f}"
        ow = d.textlength(odd, font=_fonte(40))
        d.text((x1 - 50 - ow, boxT + 24), odd, font=_fonte(40), fill=TEXT)
        y = boxB + 16

    # ---- barra "RETORNO CERTO" (ancorada no rodapé do card) ----
    barB = yB - 3
    barT = barB - 52
    gx2 = int(x0 + cardW * 0.60)
    d.polygon([(x0 + 3, barT), (gx2, barT), (gx2 - 16, barB), (x0 + 3, barB)], fill=GREEN)
    d.text((x0 + 22, barT + 14), f"{float(sb['profit_pct']):.2f}% RETORNO CERTO",
           font=_fonte(23), fill=(255, 255, 255))
    d.rectangle([gx2 - 15, barT, x1 - 3, barB], fill=BARBG)
    d.text((gx2 + 28, barT + 15), "CALCULAR", font=_fonte(22), fill=(255, 255, 255))

    # ---- aplica o desfoque no mercado + casa ----
    for (a, b, c, e) in blur:
        a, b, c, e = max(0, a), max(0, b), min(W, c), min(H, e)
        reg = img.crop((a, b, c, e)).filter(ImageFilter.GaussianBlur(7))
        img.paste(reg, (a, b))

    # redesenha a borda arredondada por cima (corners limpos)
    d.rounded_rectangle([x0, y0, x1, yB], radius=26, outline=BSOFT, width=2)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------
def postar_faixa(lo, hi, rotulo):
    """Posta UMA entrada da faixa no grupo (completa, com links)."""
    sb = _pegar_faixa(lo, hi)
    if not sb:
        print(f">> promo: sem entrada {rotulo} agora.")
        return False
    notifier.enviar_surebet(sb)      # já leva os links das casas + CTA no rodapé
    _estado["postados"].add(sb["id"])
    print(f">> promo: postou {rotulo} — {float(sb['profit_pct']):.2f}% {sb.get('event','')}")
    return True


def postar_vip():
    """(Fora do fluxo diário — mantido p/ uso manual/futuro.) Teaser borrado."""
    sb = _pegar(feed.get_surebets(min_profit=8.0))
    if not sb:
        return False
    cap = (
        f"🚨 <b>ENTRADA VIP LIBERADA — +{float(sb['profit_pct']):.2f}% DE LUCRO</b> 🚨\n"
        f"⚽ {notifier._esc(str(sb.get('event','')))}\n\n"
        f"🔒 O <b>mercado</b> e a <b>casa</b> estão bloqueados nessa...\n"
        f"É de graça pra quem é <b>PRO</b>. Destrave TODAS as entradas de 5% a 15%+ 👇\n"
        f"👉 {config.SITE_URL}"
    )
    try:
        img = gerar_teaser(sb)
        ok = notifier.enviar_foto(img, cap)
        if not ok:
            notifier.enviar_texto(cap)
    except Exception as e:
        print("!! teaser imagem falhou, enviando texto:", e)
        notifier.enviar_texto(cap)
    _estado["postados"].add(sb["id"])
    return True


def postar_social():
    global _social_i
    msg = SOCIAL_MSGS[_social_i % len(SOCIAL_MSGS)]
    _social_i += 1
    notifier.enviar_texto(f"{msg}\n\n👉 <a href=\"{config.SITE_URL}\">{config.SITE_URL}</a>")


# ---------------------------------------------------------------------------
# Agendador
# ---------------------------------------------------------------------------
def _loop():
    intervalo_seg = max(60, INTERVALO_MIN * 60)
    ultimo = 0.0                      # ts do último post normal (0 = posta logo)
    while not _parar.is_set():
        try:
            a = _agora()
            dia = a.strftime("%Y-%m-%d")
            hhmm = a.strftime("%H:%M")
            if dia != _estado["dia"]:
                _reset_dia(dia)
                ultimo = 0.0          # novo dia: pode postar já
            if notifier.ativo():
                agora = _time.time()
                # as 2 entradas de ~5% do dia (17:00 e 18:00, uma vez cada)
                if hhmm in HORARIOS_5PCT and hhmm not in _estado["slots"]:
                    _estado["slots"].add(hhmm)
                    if postar_faixa(*FAIXA_5PCT, "5%"):
                        ultimo = agora
                # entrada normal (1-3%) a cada INTERVALO
                elif agora - ultimo >= intervalo_seg:
                    postar_faixa(*FAIXA_NORMAL, "1-3%")
                    ultimo = agora    # respeita o intervalo mesmo se não achou
        except Exception as e:
            print("!! promo loop erro:", e)
        _parar.wait(30)


def iniciar():
    """Sobe a thread do fluxo (idempotente)."""
    global _thread
    if not getattr(config, "PROMO_ATIVO", True):
        print(">> Promo Telegram DESATIVADO (config.PROMO_ATIVO=0).")
        return
    if _thread and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_loop, name="promo-telegram", daemon=True)
    _thread.start()
    print(f">> Promo Telegram iniciado — 1 entrada a cada {INTERVALO_MIN} min "
          f"+ 5% em {', '.join(HORARIOS_5PCT)} (Brasília).")


def parar():
    _parar.set()
