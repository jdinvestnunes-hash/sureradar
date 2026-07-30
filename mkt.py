"""
mkt.py — trilhos de segurança do e-mail marketing.

Garante que os fluxos de marketing (nudge, recuperação, retenção, semanal) NUNCA
estourem o limite do provedor nem bombardeiem a mesma pessoa:
  - teto por 24h  -> deixa folga pro transacional dentro do cap do Resend
  - teto por ciclo -> cada rodada horária manda no máximo N; o resto espera a próxima
  - frequência por pessoa -> não manda 2 e-mails de marketing em menos de FREQ_H horas
  - pausa entre envios -> respeita o rate limit (~2/s)

Uso nos fluxos (lifecycle, recuperacao, e os novos):
    orc = mkt.Orcamento()
    for u in alvos:
        if not orc.pode():
            break                       # atingiu o teto -> o resto fica pra próxima rodada
        if not mkt.pode_pessoa(u["id"]):
            continue                    # recebeu algo há pouco -> pula (nutre sem irritar)
        if auth.registrar_email(u["id"], tipo):   # dedup: só a 1ª vez
            enviar(...)
            orc.gastou()
            mkt.espacar()
"""
import time

import auth
import config

CAP_24H = int(getattr(config, "EMAIL_MKT_CAP_24H", 80))
CAP_CICLO = int(getattr(config, "EMAIL_MKT_CAP_CICLO", 25))
FREQ_MIN_S = int(getattr(config, "EMAIL_MKT_FREQ_H", 48)) * 3600
PAUSA_S = float(getattr(config, "EMAIL_MKT_PAUSA_S", 0.6))


class Orcamento:
    """Quanto ainda dá pra mandar de marketing NESTA rodada sem furar o teto de 24h."""

    def __init__(self):
        usados = auth.emails_marketing_24h()
        self.restante = max(0, min(CAP_CICLO, CAP_24H - usados))

    def pode(self):
        return self.restante > 0

    def gastou(self):
        self.restante -= 1


def pode_pessoa(user_id):
    """False se a pessoa recebeu algum e-mail de marketing nas últimas FREQ_H horas."""
    ult = auth.ultimo_email_marketing(user_id)
    return (time.time() - ult) >= FREQ_MIN_S if ult else True


def espacar():
    """Pausa curtinha entre um envio e o próximo (respeita o rate limit do provedor)."""
    if PAUSA_S > 0:
        time.sleep(PAUSA_S)
