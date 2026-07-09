"""
alertas.py — envia surebets personalizadas na DM dos usuários que conectaram o
Telegram e escolheram CASAS + LUCRO MÍNIMO. Roda numa thread de fundo.

Nunca repete a mesma entrada pro mesmo usuário (dedup persistente por (user, id)).
"""
import threading

import auth
import config
import feed
import notifier

_stop = threading.Event()
_thread = None
INTERVALO_SEG = 60          # confere o feed a cada 60s
MAX_POR_CICLO = 3           # no máx. N alertas por usuário por ciclo (anti-flood)


def _casas_batem(sb, casas_set):
    """True se TODAS as pernas são de casas escolhidas (vazio = qualquer casa)."""
    if not casas_set:
        return True
    for leg in sb.get("legs", []):
        if leg.get("bookmaker") not in casas_set:
            return False
    return True


def _uma_rodada():
    for cfg in auth.alerta_ativos():
        try:
            chat = cfg.get("chat_id")
            uid = cfg["user_id"]
            minp = float(cfg.get("min_pct") or 0)
            casas = set(cfg.get("casas") or [])
            enviados = 0
            for sb in feed.get_surebets(min_profit=minp):
                if enviados >= MAX_POR_CICLO:
                    break
                if not _casas_batem(sb, casas):
                    continue
                sid = str(sb.get("id"))
                if auth.alerta_ja_enviou(uid, sid):
                    continue
                msg = "🔔 <b>ALERTA — surebet no seu filtro</b>\n\n" + notifier.formatar_surebet(sb)
                if notifier.enviar_para(chat, msg):
                    auth.alerta_marcar(uid, sid)
                    enviados += 1
        except Exception as e:
            print("!! alerta (usuário):", e)


def _loop():
    while not _stop.is_set():
        try:
            if config.TELEGRAM_BOT_TOKEN:
                _uma_rodada()
        except Exception as e:
            print("!! alertas loop:", e)
        _stop.wait(INTERVALO_SEG)


def iniciar():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="alertas-tg", daemon=True)
    _thread.start()
    print(">> Alertas personalizados no Telegram iniciados.")


def parar():
    _stop.set()
