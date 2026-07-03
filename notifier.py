"""
notifier.py — envia surebets novas para um grupo do Telegram.

Configuração no .env:
    TELEGRAM_BOT_TOKEN=...   (criado no @BotFather)
    TELEGRAM_CHAT_ID=...     (ID do grupo — use telegram_setup.py para descobrir)

Se as duas variáveis não estiverem preenchidas, o notificador fica inativo
(silencioso) e o resto do sistema segue funcionando normalmente.
"""

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


def enviar_texto(texto):
    if not ativo():
        return False
    return _post("sendMessage", {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def _brl(v):
    """Formata número como R$ 1.234,56 (padrão brasileiro)."""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_surebet(sb):
    """Monta a mensagem HTML de uma surebet (dict no formato-contrato)."""
    banca = sb.get("banca", 1000)
    linhas = [
        f"🎯 <b>SUREBET {sb['profit_pct']:.2f}%</b>",
        f"⚽ <b>{_esc(sb['event'])}</b>",
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
    if getattr(config, "SITE_URL", ""):
        linhas.append("")
        linhas.append("🔓 Entradas de <b>5% a 15%+</b> são exclusivas do PRO")
        linhas.append(f'👉 <a href="{config.SITE_URL}">{config.SITE_URL}</a>')
    return "\n".join(linhas)


def enviar_surebet(sb):
    return enviar_texto(formatar_surebet(sb))


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
