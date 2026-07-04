"""
lifecycle.py — fluxo de nutrição por e-mail para quem se cadastrou e NÃO comprou.

A cada hora, olha os usuários GRÁTIS (com e-mail confirmado) e, conforme os dias
desde o cadastro, manda o nudge daquela JANELA (uma vez só, sem reenviar):

    dia 1–2  -> nudge1   dia 3–4 -> nudge2   dia 5–6 -> nudge3   dia 7–8 -> nudge4

Usar JANELAS (e não "dias >= X") evita disparar 4 e-mails de uma vez para contas
antigas: quem já passou de 9 dias não recebe nada.

O e-mail de boas-vindas (dia 0) sai no cadastro/confirmação; a confirmação de
compra sai no webhook do pagamento. Aqui é só a régua de conversão do grátis.
"""

import threading
import time

import auth
import config
import emailer

_CHECK_SEG = 3600          # verifica de hora em hora
_thread = None
_stop = threading.Event()

# (dia_min, dia_max, tipo) — janelas de 2 dias
_JANELAS = [(1, 3, "nudge1"), (3, 5, "nudge2"), (5, 7, "nudge3"), (7, 9, "nudge4")]


def _rodar_uma_vez():
    if not config.RESEND_API_KEY:
        return
    agora = time.time()
    for u in auth.usuarios_free_verificados():
        dias = (agora - u["criado"]) / 86400
        for dmin, dmax, tipo in _JANELAS:
            if dmin <= dias < dmax:
                if auth.registrar_email(u["id"], tipo):
                    ok = emailer.enviar_nudge(u["email"], u["nome"], tipo)
                    print(f">> nudge {tipo} -> {u['email']} ({'ok' if ok else 'falhou'})")
                break      # no máximo 1 nudge por usuário por rodada


def _loop():
    # espera um pouco no boot pra não competir com o startup
    _stop.wait(60)
    while not _stop.is_set():
        try:
            _rodar_uma_vez()
        except Exception as e:
            print("!! lifecycle:", e)
        _stop.wait(_CHECK_SEG)


def iniciar():
    global _thread
    if not config.LIFECYCLE_ATIVO:
        print(">> Lifecycle de e-mail DESLIGADO (LIFECYCLE_ATIVO=0).")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    print(">> Lifecycle de e-mail LIGADO (nudges pró a cada hora).")


def parar():
    _stop.set()
