"""
guardiao.py — vigia do robô NA NUVEM (Railway).

Faz duas coisas numa thread de fundo, sempre no ar (independe do seu PC):

1) ALERTA DE QUEDA: fica de olho na idade do feed (`feed.status()`). Se passar
   de ROBO_ALERTA_MIN minutos SEM dados novos (e o robô devia estar ligado), te
   manda um aviso no Telegram privado. Quando voltar, avisa que normalizou.

2) COMANDOS PELO TELEGRAM: escuta o bot (getUpdates) e obedece SÓ o seu chat
   (ADMIN_TELEGRAM_CHAT_ID):
       /desligar  -> grava robo_ligado=0  (o vigia do PC para o robô)
       /ligar     -> grava robo_ligado=1  (o vigia do PC sobe o robô)
       /status    -> responde idade do feed + se está ligado

O interruptor fica no banco (app_flags, chave 'robo_ligado'), então o vigia do
PC lê pelo endpoint /api/robo/estado e obedece — e persiste entre deploys.
"""

import threading
import time

import requests

import auth
import config
import feed
import notifier

_API = "https://api.telegram.org/bot{token}/{metodo}"
_thread = None
_stop = threading.Event()

# Estado em memória do alerta (pra não spammar): True = já avisei que caiu.
_caiu_avisado = False
_offset = 0                 # último update_id processado + 1 (getUpdates)


def robo_ligado() -> bool:
    """Interruptor persistente. Default LIGADO (se nunca foi mexido)."""
    return str(auth.flag_get("robo_ligado", "1")) not in ("0", "false", "False")


def _set_ligado(ligado: bool):
    auth.flag_set("robo_ligado", "1" if ligado else "0")


def _idade_seg():
    """Segundos desde o último dado no painel, ou None se nunca teve."""
    try:
        return feed.status().get("idade_seg")
    except Exception:
        return None


# ----------------------------- Telegram --------------------------------------

def _tg(metodo, **params):
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        url = _API.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo)
        r = requests.get(url, params=params, timeout=params.get("timeout", 10) + 8)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _drenar_inicial():
    """No boot, pula os comandos ANTIGOS na fila (senão um /desligar de semana
    passada seria executado agora). Só marca o offset além do que já existe."""
    global _offset
    data = _tg("getUpdates", timeout=0)
    if data and data.get("ok") and data.get("result"):
        _offset = data["result"][-1]["update_id"] + 1


def _responder(texto):
    notifier.enviar_para(config.ADMIN_TELEGRAM_CHAT_ID, texto)


def _tratar_comando(texto):
    cmd = (texto or "").strip().lower().split("@")[0].lstrip("/")
    if cmd in ("desligar", "parar", "off"):
        _set_ligado(False)
        _responder("🔴 <b>Robô DESLIGADO.</b>\nO vigia do PC vai parar ele em até 1 min.\n"
                   "Mande /ligar quando quiser subir de novo.")
    elif cmd in ("ligar", "iniciar", "on"):
        _set_ligado(True)
        _responder("🟢 <b>Robô LIGADO.</b>\nO vigia do PC vai subir ele em até 1 min.")
    elif cmd in ("status", "estado"):
        idade = _idade_seg()
        if idade is None:
            situ = "sem dados ainda"
        elif idade < 120:
            situ = f"✅ no ar (dados de {int(idade)}s atrás)"
        else:
            situ = f"⚠️ {int(idade // 60)} min sem dados"
        estado = "🟢 LIGADO" if robo_ligado() else "🔴 DESLIGADO"
        _responder(f"📊 <b>Status do robô</b>\nInterruptor: {estado}\nPainel: {situ}")
    elif cmd in ("ajuda", "help", "start"):
        _responder("🤖 <b>Comandos do robô</b>\n"
                   "/ligar — sobe o robô\n"
                   "/desligar — para o robô\n"
                   "/status — como está agora")


def _ler_comandos():
    """Um ciclo de getUpdates (long-poll ~20s). Só reage ao chat do admin."""
    global _offset
    admin = str(config.ADMIN_TELEGRAM_CHAT_ID or "")
    if not admin:
        return
    data = _tg("getUpdates", offset=_offset, timeout=20, allowed_updates='["message"]')
    if not data or not data.get("ok"):
        return
    for upd in data.get("result", []):
        _offset = upd["update_id"] + 1
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        texto = msg.get("text") or ""
        if chat_id == admin and texto.startswith("/"):
            try:
                _tratar_comando(texto)
            except Exception as e:
                print("!! guardiao comando:", e)


# ----------------------------- Alerta de queda -------------------------------

def _checar_queda():
    global _caiu_avisado
    if not robo_ligado():          # desligado de propósito -> não alerta
        _caiu_avisado = False
        return
    idade = _idade_seg()
    if idade is None:
        return                     # nunca teve dado (boot) -> não alarma
    limite = config.ROBO_ALERTA_MIN * 60
    if idade > limite and not _caiu_avisado:
        _caiu_avisado = True
        notifier.enviar_admin(
            "⚠️ <b>SURERADAR CAIU</b>\n\n"
            f"O painel está há <b>{int(idade // 60)} min</b> sem dados novos.\n"
            "O robô provavelmente parou. Verifique o PC/robô o quanto antes.\n\n"
            "Comandos: /status  /ligar  /desligar")
    elif idade <= limite and _caiu_avisado:
        _caiu_avisado = False
        notifier.enviar_admin("✅ <b>SureRadar VOLTOU</b>\nO painel está recebendo dados de novo.")


# ----------------------------- Loop / boot -----------------------------------

def _loop():
    _stop.wait(30)                 # respira no boot (deixa o feed carregar do cache)
    try:
        _drenar_inicial()
    except Exception as e:
        print("!! guardiao drenar:", e)
    while not _stop.is_set():
        try:
            _ler_comandos()        # bloqueia até ~20s (long-poll) ou até chegar comando
        except Exception as e:
            print("!! guardiao getUpdates:", e)
            _stop.wait(10)
        try:
            _checar_queda()        # roda a cada ciclo (no máx a cada ~20s)
        except Exception as e:
            print("!! guardiao checar:", e)


def iniciar():
    global _thread
    if not config.GUARDIAO_ATIVO:
        print(">> Guardião do robô DESLIGADO (GUARDIAO_ATIVO=0).")
        return
    if not (config.TELEGRAM_BOT_TOKEN and config.ADMIN_TELEGRAM_CHAT_ID):
        print(">> Guardião do robô: falta TELEGRAM_BOT_TOKEN/ADMIN_TELEGRAM_CHAT_ID.")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="guardiao-robo", daemon=True)
    _thread.start()
    print(f">> Guardião do robô LIGADO (alerta em {config.ROBO_ALERTA_MIN} min + comandos Telegram).")


def parar():
    _stop.set()
