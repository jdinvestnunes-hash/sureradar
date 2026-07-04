"""
emailer.py — envio de e-mail transacional via Resend.

Usado hoje para RECUPERAR SENHA. Se RESEND_API_KEY não estiver configurada,
as funções apenas logam e retornam False (o site não quebra).

Setup (uma vez): criar conta em resend.com, verificar o domínio sureradar.site
(adicionar os registros DNS que eles mostram) e por a API key em RESEND_API_KEY
no Railway. Enquanto não verificar o domínio, dá pra testar com o remetente
"SureRadar <onboarding@resend.dev>" (EMAIL_FROM).
"""

import requests

import config

_API = "https://api.resend.com/emails"


def enviar(to: str, assunto: str, html: str) -> bool:
    """Envia um e-mail. Retorna True se o Resend aceitou."""
    if not config.RESEND_API_KEY:
        print(f"!! RESEND_API_KEY não configurada — e-mail NÃO enviado: {assunto}")
        return False
    try:
        r = requests.post(
            _API,
            headers={"Authorization": "Bearer " + config.RESEND_API_KEY},
            json={"from": config.EMAIL_FROM, "to": [to], "subject": assunto, "html": html},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"!! Falha de rede ao enviar e-mail: {e}")
        return False
    if not r.ok:
        print(f"!! Resend recusou ({r.status_code}): {r.text[:200]}")
        return False
    return True


def _layout(titulo: str, corpo_html: str) -> str:
    """Casca visual da marca (dark, verde/ciano) para os e-mails."""
    return f"""\
<div style="background:#05070d;padding:32px 16px;font-family:Inter,Arial,sans-serif">
  <div style="max-width:480px;margin:0 auto;background:#0e1421;border:1px solid #1b2740;
              border-radius:18px;padding:34px 30px;color:#f2f6fc">
    <div style="font-size:22px;font-weight:800;letter-spacing:-.02em;margin-bottom:6px">
      Sure<span style="color:#2ee6a8">Radar</span>
    </div>
    <h1 style="font-size:20px;margin:18px 0 10px;color:#f2f6fc">{titulo}</h1>
    {corpo_html}
    <p style="color:#5e6b85;font-size:12px;margin-top:28px;border-top:1px solid #1b2740;padding-top:16px">
      Se você não pediu isso, pode ignorar este e-mail com segurança.
    </p>
  </div>
</div>"""


def enviar_reset_senha(to: str, nome: str, link: str) -> bool:
    """E-mail com o link para redefinir a senha (válido por 1 hora)."""
    corpo = f"""\
    <p style="color:#a3b1c9;font-size:14.5px;line-height:1.6">
      Olá {nome or ''}, recebemos um pedido para redefinir a senha da sua conta SureRadar.
      Clique no botão abaixo para criar uma senha nova. O link vale por <b>1 hora</b>.
    </p>
    <a href="{link}" style="display:inline-block;margin:18px 0;background:#2ee6a8;color:#052015;
       text-decoration:none;font-weight:800;font-size:15px;padding:13px 26px;border-radius:12px">
      Redefinir minha senha
    </a>
    <p style="color:#5e6b85;font-size:12.5px;line-height:1.6">
      Se o botão não funcionar, copie e cole este endereço no navegador:<br>
      <span style="color:#38d4f5;word-break:break-all">{link}</span>
    </p>"""
    return enviar(to, "Redefinir sua senha — SureRadar", _layout("Redefinir senha", corpo))
