"""
recuperacao.py — régua de e-mail pra quem GEROU checkout (Pix/cartão) e NÃO pagou.

Fluxo (para na hora que a pessoa vira PRO — o público já exclui PRO ativo):
  1) 7 e-mails na régua: o 1º ~1h após o checkout, os seguintes espaçados ~2 dias.
  2) Depois dos 7: 2 e-mails/mês (metade A: dia<=15, metade B: dia>15), pra sempre,
     em rodízio de conteúdo, até a pessoa comprar.

Roda numa thread de fundo (a cada hora), igual ao lifecycle. Dedup via
email_enviados (registrar_email). Respeita descadastro (email_optout).
"""

import threading
import time
from datetime import datetime

import auth
import config
import emailer

_CHECK_SEG = 3600            # verifica de hora em hora
_GAP_SERIE = 2 * 86400       # ~2 dias entre um e-mail da série e o próximo
_GAP_MENSAL = 12 * 86400     # ~12 dias de folga mínima na fase mensal (=> ~2/mês)
_thread = None
_stop = threading.Event()


def _enviar(u, tipo, idx=0):
    unsub = config.SITE_URL + "/descadastrar?u=" + auth.unsub_token(u["id"])
    ok = emailer.enviar_recup(u["email"], u["nome"], tipo, unsub, idx)
    print(f">> recup {tipo} -> {u['email']} ({'ok' if ok else 'falhou'})")


def _rodar_uma_vez():
    if not config.RESEND_API_KEY:
        return
    agora = time.time()
    for u in auth.usuarios_para_recuperacao():
        try:
            tipos, ultimo = auth.recup_status(u["id"])
            n = sum(1 for i in range(1, 8) if f"recup_{i}" in tipos)   # quantos da série já foram
            if n < 7:
                if n == 0:
                    # 1º e-mail: manda ~1h após o checkout (pra novos; imediato p/ antigos)
                    if agora - float(u.get("primeiro_checkout") or agora) >= 3600:
                        if auth.registrar_email(u["id"], "recup_1"):
                            _enviar(u, "recup_1")
                elif agora - ultimo >= _GAP_SERIE:
                    proximo = f"recup_{n + 1}"
                    if auth.registrar_email(u["id"], proximo):
                        _enviar(u, proximo)
            else:
                # fase mensal: 2/mês, com folga mínima
                if agora - ultimo >= _GAP_MENSAL:
                    d = datetime.now()
                    metade = "A" if d.day <= 15 else "B"
                    tipo = f"recup_m_{d.year}-{d.month:02d}_{metade}"
                    if tipo not in tipos and auth.registrar_email(u["id"], tipo):
                        idx = d.year * 2 + d.month + (0 if metade == "A" else 1)
                        _enviar(u, tipo, idx)
        except Exception as e:
            print("!! recuperacao usuario:", e)


def _loop():
    _stop.wait(90)             # espera o boot
    while not _stop.is_set():
        try:
            _rodar_uma_vez()
        except Exception as e:
            print("!! recuperacao:", e)
        _stop.wait(_CHECK_SEG)


def iniciar():
    global _thread
    if not getattr(config, "RECUP_ATIVO", True):
        print(">> Recuperação de e-mail DESLIGADA (RECUP_ATIVO=0).")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="recuperacao", daemon=True)
    _thread.start()
    print(">> Recuperação de e-mail LIGADA (7 e-mails + 2/mês pra quem gerou checkout e não pagou).")


def parar():
    _stop.set()
