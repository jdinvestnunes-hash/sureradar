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


def enviar(to: str, assunto: str, html: str = None, texto: str = None, headers: dict = None) -> bool:
    """Envia um e-mail. `html` p/ e-mails visuais; `texto` p/ plain text (cai
    melhor na aba Principal). `headers` p/ List-Unsubscribe etc. Retorna True se
    o Resend aceitou."""
    if not config.RESEND_API_KEY:
        print(f"!! RESEND_API_KEY não configurada — e-mail NÃO enviado: {assunto}")
        return False
    corpo = {"from": config.EMAIL_FROM, "to": [to], "subject": assunto}
    if html:
        corpo["html"] = html
    if texto:
        corpo["text"] = texto
    if headers:
        corpo["headers"] = headers
    try:
        r = requests.post(
            _API,
            headers={"Authorization": "Bearer " + config.RESEND_API_KEY},
            json=corpo,
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"!! Falha de rede ao enviar e-mail: {e}")
        return False
    if not r.ok:
        print(f"!! Resend recusou ({r.status_code}): {r.text[:200]}")
        return False
    return True


def testar(to: str):
    """Envia um e-mail de teste e devolve (ok, detalhe) — p/ diagnóstico."""
    if not config.RESEND_API_KEY:
        return False, "RESEND_API_KEY não configurada"
    corpo = "<p style='color:#a3b1c9;font-size:14.5px'>Se você recebeu isto, o envio de e-mail está funcionando. 🎯</p>"
    try:
        r = requests.post(
            _API,
            headers={"Authorization": "Bearer " + config.RESEND_API_KEY},
            json={"from": config.EMAIL_FROM, "to": [to],
                  "subject": "Teste de e-mail — SureRadar",
                  "html": _layout("Teste", corpo)},
            timeout=20,
        )
    except requests.RequestException as e:
        return False, "erro de rede: " + str(e)[:150]
    return r.ok, f"HTTP {r.status_code}: {r.text[:250]}"


def _primeiro(nome):
    return (nome or "").strip().split(" ")[0] or "trader"


def enviar_compra(to: str, nome: str) -> bool:
    """Confirmação de compra (plain text, tom pessoal)."""
    texto = (
        f"Parabéns, {_primeiro(nome)}! Seu PRO tá ativo. 🎯\n\n"
        f"Agora você vê TODAS as entradas, de 1% a 15%+, sem limite.\n"
        f"Acesse agora: {config.SITE_URL}/app\n\n"
        f"Bons greens!\nEquipe SureRadar"
    )
    return enviar(to, "Seu PRO está ativo 🎯", texto=texto)


# Sequência de nudges p/ quem se cadastrou e NÃO comprou (plain text de propósito).
_NUDGES = {
    "nudge1": ("viu as entradas de hoje?",
        "Oi {n}, tudo certo?\n\n"
        "Vi que você criou sua conta no SureRadar mas ainda está no plano grátis.\n\n"
        "No grátis você vê só as entradas até 1%. As que valem de verdade — 3%, 5%, 8% — "
        "ficam no PRO. Uma entrada dessas já paga a mensalidade.\n\n"
        "Dá uma olhada: {url}/planos\n\n"
        "Abraço,\nEquipe SureRadar"),
    "nudge2": ("quanto dá pra tirar com surebet",
        "Oi {n},\n\n"
        "Surebet é simples: você cobre todos os resultados em casas diferentes e trava o lucro, "
        "dê no que der. Não é aposta, é matemática.\n\n"
        "Com uma banca de R$1.000 e 2 entradas de 5% por dia, dá pra fazer a banca render bem mais "
        "que qualquer renda fixa — e o PRO te mostra essas entradas prontas.\n\n"
        "Ver os planos: {url}/planos\n\n"
        "Abraço,\nEquipe SureRadar"),
    "nudge3": ("o que te segura?",
        "Oi {n},\n\n"
        "Você entrou no SureRadar mas ainda não assinou o PRO. Ficou alguma dúvida?\n\n"
        "As entradas de maior lucro estão liberando todo dia e quem é PRO já está aproveitando. "
        "O plano se paga com 1 ou 2 entradas.\n\n"
        "Assinar: {url}/planos\n\n"
        "Abraço,\nEquipe SureRadar"),
    "nudge4": ("as entradas travadas continuam aí",
        "Oi {n},\n\n"
        "Só passando pra lembrar: as entradas de 3% a 15% seguem travadas na sua conta grátis.\n\n"
        "Cada dia parado é entrada que passa. Se quiser destravar tudo, é só assinar o PRO — "
        "cartão ou Pix, e dá pra cancelar quando quiser.\n\n"
        "{url}/planos\n\n"
        "Abraço,\nEquipe SureRadar"),
}


def enviar_resposta_ticket(to: str, nome: str, resposta: str) -> bool:
    """Avisa o usuário que o suporte respondeu o ticket dele (plain text)."""
    texto = (
        f"Oi {_primeiro(nome)}, o suporte do SureRadar respondeu seu ticket:\n\n"
        f"\"{(resposta or '').strip()[:800]}\"\n\n"
        f"Você também vê a resposta no seu perfil: {config.SITE_URL}/perfil\n\n"
        f"Abraço,\nEquipe SureRadar"
    )
    return enviar(to, "Resposta do suporte — SureRadar", texto=texto)


def enviar_nudge(to: str, nome: str, tipo: str, unsub_url: str = "") -> bool:
    par = _NUDGES.get(tipo)
    if not par:
        return False
    assunto, corpo = par
    texto = corpo.format(n=_primeiro(nome), url=config.SITE_URL)
    headers = None
    if unsub_url:
        texto += f"\n\n---\nNão quer mais esses e-mails? Descadastre aqui: {unsub_url}"
        # cabeçalho padrão que o Gmail usa p/ o botão de descadastro (1 clique)
        headers = {"List-Unsubscribe": f"<{unsub_url}>",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return enviar(to, assunto, texto=texto, headers=headers)


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


def enviar_boas_vindas(to: str, nome: str) -> bool:
    """E-mail de boas-vindas quando a conta é criada (personalizado com o nome)."""
    primeiro = (nome or "").strip().split(" ")[0] or "trader"
    painel = config.SITE_URL + "/app"
    corpo = f"""\
    <p style="color:#a3b1c9;font-size:14.5px;line-height:1.6">
      Fala, {primeiro}! 🎯 Sua conta no <b style="color:#f2f6fc">SureRadar</b> está pronta.
    </p>
    <p style="color:#a3b1c9;font-size:14.5px;line-height:1.6">
      A gente encontra <b style="color:#2ee6a8">surebets</b> — apostas onde você cobre todos os
      resultados em casas diferentes e trava o <b style="color:#f2f6fc">lucro garantido</b>, dê no que der.
      As oportunidades já estão no seu painel, atualizando o tempo todo.
    </p>
    <a href="{painel}" style="display:inline-block;margin:18px 0;background:#2ee6a8;color:#052015;
       text-decoration:none;font-weight:800;font-size:15px;padding:13px 26px;border-radius:12px">
      Ver as entradas de hoje →
    </a>
    <p style="color:#a3b1c9;font-size:13.5px;line-height:1.6">
      No plano <b style="color:#f2f6fc">Grátis</b> você já vê entradas até 1%. No
      <b style="color:#ffc94d">PRO</b> destrava as de maior lucro (1% a 15%+), sem limite.
    </p>
    <p style="color:#5e6b85;font-size:13px;line-height:1.6">
      Bons greens! 🍀 — Equipe SureRadar
    </p>"""
    return enviar(to, f"Bem-vindo ao SureRadar, {primeiro}! 🎯",
                  _layout("Sua conta está pronta", corpo))


def enviar_confirmacao(to: str, nome: str, link: str) -> bool:
    """E-mail para CONFIRMAR o cadastro. A conta só libera depois do clique."""
    primeiro = (nome or "").strip().split(" ")[0] or "trader"
    corpo = f"""\
    <p style="color:#a3b1c9;font-size:14.5px;line-height:1.6">
      Fala, {primeiro}! Falta <b style="color:#f2f6fc">um clique</b> pra ativar sua conta no
      <b style="color:#f2f6fc">SureRadar</b> e liberar as entradas.
    </p>
    <a href="{link}" style="display:inline-block;margin:18px 0;background:#2ee6a8;color:#052015;
       text-decoration:none;font-weight:800;font-size:15px;padding:13px 26px;border-radius:12px">
      Confirmar meu e-mail →
    </a>
    <p style="color:#5e6b85;font-size:12.5px;line-height:1.6">
      Se o botão não funcionar, copie e cole no navegador:<br>
      <span style="color:#38d4f5;word-break:break-all">{link}</span><br>
      Este link vale por 3 dias.
    </p>"""
    return enviar(to, "Confirme seu e-mail — SureRadar", _layout("Confirme seu cadastro", corpo))


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
