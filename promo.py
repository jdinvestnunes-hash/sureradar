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
    """PNG (bytes) de um card estilo 'print' da entrada, com o mercado e a casa
    borrados — só o lucro, o jogo e as odds ficam à mostra."""
    from PIL import Image, ImageDraw, ImageFilter
    W, H = 900, 500
    GREEN, CYAN, GOLD = (46, 230, 168), (56, 212, 245), (255, 201, 77)
    WHITE, DIM, CARD, LINE = (242, 246, 252), (150, 165, 190), (14, 20, 33), (39, 57, 92)
    img = Image.new("RGB", (W, H), (5, 7, 13))
    d = ImageDraw.Draw(img)

    # marca
    d.text((44, 34), "Sure", font=_fonte(34), fill=WHITE)
    wsure = d.textlength("Sure", font=_fonte(34))
    d.text((44 + wsure, 34), "Radar", font=_fonte(34), fill=GREEN)
    # selo VIP
    d.rounded_rectangle([W - 210, 34, W - 44, 80], 22, fill=(38, 28, 6), outline=GOLD, width=2)
    d.text((W - 188, 46), "ENTRADA VIP", font=_fonte(20), fill=GOLD)

    # lucro gigante
    d.text((44, 112), f"+{float(sb['profit_pct']):.2f}%", font=_fonte(96), fill=GOLD)
    d.text((48, 222), "DE LUCRO GARANTIDO", font=_fonte(28), fill=GREEN)

    # jogo (visível)
    d.text((44, 278), str(sb.get("event", ""))[:44], font=_fonte(26), fill=WHITE)

    # pernas — mercado e casa BORRADOS; odd à mostra
    blur = []
    y = 322
    for leg in sb.get("legs", [])[:2]:
        d.rounded_rectangle([44, y, W - 44, y + 68], 14, fill=CARD, outline=LINE, width=1)
        d.text((66, y + 10), str(leg.get("outcome", ""))[:36], font=_fonte(21), fill=DIM)
        blur.append((60, y + 6, 600, y + 38))
        d.text((66, y + 40), str(leg.get("bookmaker_label", leg.get("bookmaker", ""))), font=_fonte(18, False), fill=DIM)
        blur.append((60, y + 38, 340, y + 64))
        try:
            odd = f"@ {float(leg.get('odd', 0)):.2f}"
        except Exception:
            odd = "@ ?"
        d.text((W - 190, y + 20), odd, font=_fonte(28), fill=CYAN)
        y += 80

    # barra de CTA
    d.rounded_rectangle([44, H - 58, W - 44, H - 16], 12, fill=(8, 28, 20), outline=GREEN, width=1)
    d.text((66, H - 48), "Assine o PRO e destrave a entrada completa  -  sureradar.site", font=_fonte(20), fill=GREEN)

    # aplica o desfoque nas regioes do mercado + casa
    for (x1, y1, x2, y2) in blur:
        x2, y2 = min(x2, W), min(y2, H)
        reg = img.crop((x1, y1, x2, y2)).filter(ImageFilter.GaussianBlur(6))
        img.paste(reg, (x1, y1))

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
