"""
notifier.py — envia surebets novas para um grupo do Telegram.

Configuração no .env:
    TELEGRAM_BOT_TOKEN=...   (criado no @BotFather)
    TELEGRAM_CHAT_ID=...     (ID do grupo — use telegram_setup.py para descobrir)

Se as duas variáveis não estiverem preenchidas, o notificador fica inativo
(silencioso) e o resto do sistema segue funcionando normalmente.
"""

from urllib.parse import urlencode

import requests

import config

API = "https://api.telegram.org/bot{token}/{metodo}"


def ativo():
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _post(metodo, payload):
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo)
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"!! Telegram {metodo} falhou: {r.status_code} {r.text[:150]}")
        return r.ok
    except requests.RequestException as e:
        print(f"!! Telegram erro de rede: {e}")
        return False


def enviar_texto(texto, preview=False):
    """`preview=True` mostra a prévia do link (ex.: thumbnail do YouTube)."""
    if not ativo():
        return False
    return _post("sendMessage", {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": not preview,
    })


def testar():
    """Diagnóstico: valida o token (getMe) e tenta postar no grupo (sendMessage).
    Devolve a resposta crua do Telegram — mostra token errado / bot fora do grupo."""
    out = {"tem_token": bool(config.TELEGRAM_BOT_TOKEN),
           "tem_chat_id": bool(config.TELEGRAM_CHAT_ID)}
    if not config.TELEGRAM_BOT_TOKEN:
        out["erro"] = "TELEGRAM_BOT_TOKEN não configurado"
        return out
    try:
        r = requests.get(API.format(token=config.TELEGRAM_BOT_TOKEN, metodo="getMe"), timeout=10)
        out["getMe"] = f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        out["getMe"] = "erro: " + str(e)[:150]
    if config.TELEGRAM_CHAT_ID:
        try:
            r = requests.post(API.format(token=config.TELEGRAM_BOT_TOKEN, metodo="sendMessage"),
                              json={"chat_id": config.TELEGRAM_CHAT_ID,
                                    "text": "✅ Teste do SureRadar — bot funcionando."}, timeout=10)
            out["sendMessage"] = f"{r.status_code}: {r.text[:250]}"
        except Exception as e:
            out["sendMessage"] = "erro: " + str(e)[:150]
    else:
        out["sendMessage"] = "TELEGRAM_CHAT_ID não configurado"
    return out


def _brl(v):
    """Formata número como R$ 1.234,56 (padrão brasileiro)."""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_SPORT_EMOJI = {
    "Football": "⚽", "Soccer": "⚽", "Futebol": "⚽",
    "Tennis": "🎾", "Tênis": "🎾", "TableTennis": "🏓", "Tênis de Mesa": "🏓",
    "Basketball": "🏀", "Basquete": "🏀", "Volleyball": "🏐", "Vôlei": "🏐",
    "Hockey": "🏒", "Baseball": "⚾", "Handball": "🤾", "Handebol": "🤾",
    "Esports": "🎮", "MMA": "🥊", "Boxing": "🥊", "Futsal": "⚽",
    "AmericanFootball": "🏈", "Cricket": "🏏", "Rugby": "🏉",
}


def _sport_emoji(sb):
    for chave in (sb.get("sport"), sb.get("sport_label")):
        e = _SPORT_EMOJI.get(str(chave or "").strip())
        if e:
            return e
    return "🏆"


def formatar_surebet(sb):
    """Monta a mensagem HTML de uma surebet (dict no formato-contrato)."""
    banca = sb.get("banca", 1000)
    linhas = [
        f"🎯 <b>SUREBET {sb['profit_pct']:.2f}%</b>",
        f"{_sport_emoji(sb)} <b>{_esc(sb['event'])}</b>",
        f"🏆 {_esc(sb.get('sport_label', sb['sport']))}  •  {_esc(sb.get('market_label', sb['market']))}",
    ]
    if sb.get("commence_br"):
        linhas.append(f"🕒 {sb['commence_br']} (Brasília)")
    linhas.append(f"💵 Banca: <b>{_brl(banca)}</b>")
    linhas.append("")

    for p in sb["legs"]:
        tipo = "🟦" if p["bookmaker_type"] == "sharp" else "🟨"
        aposta = p.get("stake_brl")
        valor = f"apostar <b>{_brl(aposta)}</b>" if aposta is not None else f"{p['stake_pct']:.1f}%"
        linha = (
            f"{tipo} <b>{_esc(p['outcome'])}</b> @ {p['odd']:.2f} "
            f"— {_esc(p['bookmaker'])}\n     💰 {valor}"
        )
        if p.get("link"):
            linha += f'  •  <a href="{_esc(p["link"])}">➡️ apostar aqui</a>'
        linhas.append(linha)

    if sb.get("lucro_brl") is not None:
        linhas.append("")
        linhas.append(f"✅ Lucro garantido: <b>{_brl(sb['lucro_brl'])}</b>")
    # Calculadora pré-preenchida com as odds da entrada (banca do usuário).
    legs = sb.get("legs", [])[:2]
    if len(legs) == 2 and getattr(config, "SITE_URL", ""):
        qs = urlencode({
            "o1": f"{legs[0]['odd']:.2f}", "o2": f"{legs[1]['odd']:.2f}",
            "n1": str(legs[0].get("outcome") or legs[0].get("bookmaker") or "Casa 1")[:30],
            "n2": str(legs[1].get("outcome") or legs[1].get("bookmaker") or "Casa 2")[:30],
        })
        linhas.append("")
        linhas.append(f'🧮 <b>Tem outra banca?</b> Calcule 👉 '
                      f'<a href="{config.SITE_URL}/calculadora?{qs}&utm_source=telegram">abrir calculadora</a>')
    if getattr(config, "SITE_URL", ""):
        linhas.append("")
        linhas.append(f'🆓 <b>Crie sua conta grátis aqui</b> 👉 '
                      f'<a href="{config.SITE_URL}/cadastro?utm_source=telegram">{config.SITE_URL}</a>')
    linhas.append("")
    linhas.append("❓ Dúvidas? Chama no @alquimistadogreen")
    return "\n".join(linhas)


def enviar_foto(img_bytes, caption):
    """Envia uma imagem (bytes PNG) com legenda HTML ao grupo."""
    if not ativo():
        return False
    url = API.format(token=config.TELEGRAM_BOT_TOKEN, metodo="sendPhoto")
    try:
        r = requests.post(url,
                          data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption,
                                "parse_mode": "HTML"},
                          files={"photo": ("teaser.png", img_bytes, "image/png")},
                          timeout=20)
        if r.status_code != 200:
            print(f"!! Telegram sendPhoto falhou: {r.status_code} {r.text[:150]}")
        return r.ok
    except requests.RequestException as e:
        print(f"!! Telegram foto erro de rede: {e}")
        return False


def enviar_surebet(sb):
    return enviar_texto(formatar_surebet(sb))


def criar_invite_link(nome):
    """Cria um link de convite nomeado no canal (via bot). Retorna o link ou None."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return None
    try:
        r = requests.post(API.format(token=config.TELEGRAM_BOT_TOKEN, metodo="createChatInviteLink"),
                          json={"chat_id": config.TELEGRAM_CHAT_ID, "name": str(nome)[:32]}, timeout=15)
        d = r.json()
    except Exception as e:
        print("!! criar_invite_link:", e)
        return None
    if d.get("ok"):
        return (d.get("result") or {}).get("invite_link")
    print("!! createChatInviteLink recusou:", str(d)[:200])
    return None


def descobrir_chats():
    """Lê os updates do bot (getUpdates) e lista os grupos que ele 'enxergou'
    — SEM postar nada. Quando você ADICIONA o bot a um grupo, o Telegram gera um
    evento (my_chat_member) que aparece aqui com o ID do grupo. Ninguém vê."""
    if not config.TELEGRAM_BOT_TOKEN:
        return {"erro": "TELEGRAM_BOT_TOKEN não configurado"}
    try:
        r = requests.get(API.format(token=config.TELEGRAM_BOT_TOKEN, metodo="getUpdates"), timeout=12)
        data = r.json()
    except Exception as e:
        return {"erro": "rede: " + str(e)[:150]}
    if not data.get("ok"):
        return {"erro": str(data)[:200]}
    chats = {}
    for up in data.get("result", []):
        for chave in ("message", "my_chat_member", "chat_member", "channel_post",
                      "edited_message", "callback_query"):
            obj = up.get(chave) or {}
            ch = obj.get("chat") or (obj.get("message") or {}).get("chat")
            if ch and ch.get("id"):
                chats[ch["id"]] = {"id": ch["id"], "type": ch.get("type"),
                                   "title": ch.get("title") or ch.get("username")
                                   or ch.get("first_name")}
    return {"chats": list(chats.values()), "eventos": len(data.get("result", [])),
            "dica": "Se vazio: adicione o bot ao grupo AGORA (ou remova e adicione de novo) e recarregue."}


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
