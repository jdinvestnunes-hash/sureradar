"""
promo.py — fluxo de marketing automático no grupo FREE do Telegram.

Estratégia (definida pelo usuário):
- 5 posts de aposta por dia, em horários fixos de Brasília: 08, 12, 15, 18, 21h.
  * 4 são surebets REAIS de até 1% (com os links das casas) — valor de graça.
  * 1 (um slot que gira a cada dia) é um TEASER VIP: uma entrada de 4-10% com
    IMAGEM "printada" e o MERCADO + a CASA BORRADOS — gera desejo -> vai pro site.
- Entre os posts, mensagens de PROVA SOCIAL / FOMO puxando para o PRO.

Roda numa thread de fundo iniciada pelo app.py no startup.
"""

import io
import threading
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _BR = ZoneInfo("America/Sao_Paulo")
except Exception:                       # pragma: no cover
    _BR = None

import config
import feed
import notifier

# Horários (Brasília). 4 viram surebet ≤1%; 1 (gira por dia) vira teaser VIP.
SLOTS = ["08:00", "12:00", "15:00", "18:00", "21:00"]
# Prova social entre os posts.
SOCIAL_TIMES = ["10:00", "13:30", "16:30", "19:30"]

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


def _slot_vip(agora):
    """Qual slot do dia é o teaser VIP (gira todo dia)."""
    return SLOTS[agora.timetuple().tm_yday % len(SLOTS)]


def _pegar(cands):
    for c in cands:
        if c["id"] not in _estado["postados"]:
            return c
    return None


def _pegar_low():
    return _pegar(feed.get_surebets(min_profit=0.0, max_profit=1.0))


def _pegar_vip():
    alvo = feed.get_surebets(min_profit=4.0, max_profit=10.0)
    if not alvo:                        # sem 4-10% agora: pega a maior >1%
        alvo = feed.get_surebets(min_profit=1.0001)
    return _pegar(alvo)


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
def postar_low():
    sb = _pegar_low()
    if not sb:
        return False
    notifier.enviar_surebet(sb)      # já leva os links das casas + CTA no rodapé
    _estado["postados"].add(sb["id"])
    return True


def postar_vip():
    sb = _pegar_vip()
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
    while not _parar.is_set():
        try:
            a = _agora()
            hhmm = a.strftime("%H:%M")
            dia = a.strftime("%Y-%m-%d")
            if dia != _estado["dia"]:
                _reset_dia(dia)
            if notifier.ativo():
                if hhmm in SLOTS and hhmm not in _estado["slots"]:
                    _estado["slots"].add(hhmm)
                    if hhmm == _slot_vip(a):
                        postar_vip()
                    else:
                        postar_low()
                elif hhmm in SOCIAL_TIMES and hhmm not in _estado["social"]:
                    _estado["social"].add(hhmm)
                    postar_social()
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
    print(f">> Promo Telegram iniciado — posts em {', '.join(SLOTS)} (Brasília).")


def parar():
    _parar.set()
