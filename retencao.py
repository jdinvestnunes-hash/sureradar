"""
retencao.py — retenção do PRO por e-mail.

Dois fluxos, rodando na mesma thread horária (igual lifecycle/recuperacao),
passando pelos trilhos de segurança (mkt.py) e com dedup via email_enviados:

  VENCIMENTO (quem é PRO e está pra vencer — só mensal/tri/semestral, anual NÃO):
      D-5 -> venc_5    D-3 -> venc_3    D-0 (último dia) -> venc_0
  WIN-BACK (quem venceu e não renovou):
      D+3 -> winback_1    D+10 -> winback_2

Renovação é sempre preço cheio (sem desconto). Respeita descadastro (email_optout).
"""

import math
import threading
import time

import auth
import config
import emailer
import mkt

_CHECK_SEG = 3600            # verifica de hora em hora
_thread = None
_stop = threading.Event()


def _enviar(u, tipo, dias=0):
    unsub = config.SITE_URL + "/descadastrar?u=" + auth.unsub_token(u["id"])
    ok = emailer.enviar_retencao(u["email"], u["nome"], tipo, unsub, dias)
    print(f">> retencao {tipo} -> {u['email']} ({'ok' if ok else 'falhou'})")
    return ok


def _rodar_uma_vez():
    if not config.RESEND_API_KEY:
        return
    orc = mkt.Orcamento()               # teto por 24h / por ciclo (trilhos de segurança)
    agora = time.time()

    # --- VENCIMENTO: PRO ativo que vence nos próximos dias (não anual) ---
    for u in auth.usuarios_pro_vencendo(dias=5):
        if not orc.pode():
            break
        if not mkt.pode_pessoa(u["id"]):
            continue
        d = math.ceil((float(u["plano_expira"]) - agora) / 86400)   # 1..5
        if d >= 5:
            tipo = "venc_5"
        elif d == 3:
            tipo = "venc_3"
        elif d <= 1:
            tipo = "venc_0"
        else:
            continue                     # d==4 ou d==2: janela morta, espera o dia certo
        if auth.registrar_email(u["id"], tipo):
            _enviar(u, tipo, d)
            orc.gastou()
            mkt.espacar()

    # --- WIN-BACK: venceu há alguns dias e não renovou (não anual) ---
    for u in auth.usuarios_pro_vencidos(dias=11):
        if not orc.pode():
            break
        if not mkt.pode_pessoa(u["id"]):
            continue
        dv = math.floor((agora - float(u["plano_expira"])) / 86400)  # dias vencido
        if dv >= 10:
            tipo = "winback_2"
        elif dv >= 3:
            tipo = "winback_1"
        else:
            continue                     # 0..2 dias: ainda cedo, dá um tempo
        if auth.registrar_email(u["id"], tipo):
            _enviar(u, tipo, dv)
            orc.gastou()
            mkt.espacar()


def _loop():
    _stop.wait(120)             # espera o boot (depois de lifecycle/recuperacao)
    while not _stop.is_set():
        try:
            _rodar_uma_vez()
        except Exception as e:
            print("!! retencao:", e)
        _stop.wait(_CHECK_SEG)


def iniciar():
    global _thread
    if not getattr(config, "RETENCAO_ATIVO", True):
        print(">> Retenção do PRO por e-mail DESLIGADA (RETENCAO_ATIVO=0).")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="retencao", daemon=True)
    _thread.start()
    print(">> Retenção do PRO LIGADA (vencimento D-5/D-3/D-0 + win-back D+3/D+10).")


def parar():
    _stop.set()
