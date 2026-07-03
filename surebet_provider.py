"""
surebet_provider.py — conector da API surebet.com / apostasseguras.

Esta fonte JÁ ENTREGA as surebets prontas (com casas BR e mercados exóticos:
escanteios, cartões, faltas, chutes...). Aqui a gente só:
  1. chama a API,
  2. traduz cada aposta para texto legível em PT,
  3. calcula o split de R$ da banca,
  4. devolve no mesmo "contrato" que o dashboard/Telegram já usam.

Doc: https://en.surebet.com/wiki/api-documentation
"""

from datetime import datetime, timezone

import requests

import config

# ---------------------------------------------------------------------------
# Tradução dos códigos de mercado (campo "type") para PT
# ---------------------------------------------------------------------------
VARIETY = {
    "goal": "gols", "corner": "escanteios", "card (booking)": "cartões",
    "card (second_yellow_is_yellow_and_red_card)": "cartão (2º amarelo)",
    "foul": "faltas", "throw in": "laterais", "goal kick": "tiro de meta",
    "shot on target": "chutes ao gol", "shot": "finalizações",
    "game": "games", "set": "sets", "point": "pontos",
    "yellowcard": "cartões amarelos", "offside": "impedimentos",
}

TIPO = {
    "over": "Acima", "under": "Abaixo", "e_over": "Acima", "e_under": "Abaixo",
    "exactly": "Exato", "yes": "Sim", "no": "Não", "draw": "Empate",
    "score": "Placar", "win1": "Vitória mandante", "win2": "Vitória visitante",
    "winOnly1": "Mandante vence (sem empate)", "winOnly2": "Visitante vence (sem empate)",
    "win1RetX": "Mandante vence ou reembolso", "win2RetX": "Visitante vence ou reembolso",
    "ah1": "Handicap asiático mandante", "ah2": "Handicap asiático visitante",
    "eh1": "Handicap europeu mandante", "eh2": "Handicap europeu visitante",
}

BASE = {"overall": "total", "home": "mandante", "away": "visitante"}

# Nomes amigáveis das casas (IDs da surebet.com).
BK_LABELS = {
    "bet365": "Bet365", "bet365_bet_br": "Bet365 (BR)",
    "betano": "Betano", "betanobr": "Betano (BR)", "betanopt": "Betano (PT)",
    "superbet": "SuperBet", "novibet": "Novibet", "betnacional": "Betnacional",
    "betfair": "Betfair", "marathonbet": "Marathon", "pinnaclesports": "Pinnacle",
    "22bet": "22Bet", "1xbet": "1xBet", "kto": "KTO", "betsson": "Betsson",
    "stake_bet_br": "Stake (BR)", "pixbet": "PixBet", "estrelabet": "EstrelaBet",
    "esportesdasorte_com": "Esportes da Sorte", "vbet_bet_br": "VBet (BR)",
    "betsul": "Betsul", "netbet": "NetBet", "sbobet": "Sbobet", "smarkets": "Smarkets",
}


def label_casa(bk):
    return BK_LABELS.get(bk, bk.replace("_", " ").title())


def casas_disponiveis():
    """Lista [{key,label}] das casas configuradas — para o filtro do painel."""
    return [{"key": bk, "label": label_casa(bk)} for bk in config.SUREBET_SOURCES]

PERIODO = {
    "regularTime": "", "firstHalf": "1º tempo", "secondHalf": "2º tempo",
    "period1": "1º tempo", "period2": "2º tempo",
    "set1": "1º set", "set2": "2º set", "set3": "3º set",
}


def _traduz_aposta(t):
    """Ex.: {type:over, variety:corner, condition:9.5, base:overall} -> 'Acima 9.5 escanteios'."""
    tipo = TIPO.get(t.get("type"), t.get("type", "?"))
    variety = VARIETY.get(t.get("variety"), t.get("variety", ""))
    cond = t.get("condition", "")
    base = BASE.get(t.get("base"), "")
    per = PERIODO.get(t.get("period"), t.get("period") or "")

    partes = [tipo]
    if cond:
        partes.append(str(cond))
    if variety:
        partes.append(variety)
    if base and base != "total":
        partes.append(f"({base})")
    if per:
        partes.append(f"- {per}")
    return " ".join(partes).strip()


def _rotulo_mercado(t):
    """Rótulo curto do mercado para o cabeçalho (ex.: 'Escanteios')."""
    v = VARIETY.get(t.get("variety"), t.get("variety", "mercado"))
    per = PERIODO.get(t.get("period"), "")
    return v.capitalize() + (f" · {per}" if per else "")


def _iso_de_ms(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _link_da_perna(prong):
    nav = prong.get("preferred_nav") or {}
    for l in nav.get("links", []):
        url = (l.get("link") or {}).get("url")
        if url:
            return url
    return None


def _titulo_evento(prongs):
    """Usa o nome de times mais completo entre as pernas."""
    melhor = ""
    for p in prongs:
        nome = " x ".join(p.get("teams", []))
        if len(nome) > len(melhor):
            melhor = nome
    return melhor or "?"


def _para_contrato(rec):
    """Converte um record da surebet.com no dict-contrato do dashboard/Telegram."""
    prongs = rec.get("prongs", [])
    if len(prongs) != 2:              # regra: só 2 casas
        return None

    banca = config.BANCA
    margem = sum(1.0 / p["value"] for p in prongs)
    if margem <= 0:
        return None

    legs = []
    for p in prongs:
        odd = p["value"]
        stake = banca * (1.0 / odd) / margem
        legs.append({
            "outcome": _traduz_aposta(p.get("type", {})),
            "odd": round(odd, 3),
            "bookmaker": p.get("bk", "?"),
            "bookmaker_label": label_casa(p.get("bk", "?")),
            "bookmaker_type": config.classificar_casa(p.get("bk", "")),
            "stake_pct": round(stake / banca * 100, 1),
            "stake_brl": round(stake, 2),
            "link": _link_da_perna(p),
        })

    commence_utc = _iso_de_ms(rec.get("time"))
    retorno = banca / margem
    return {
        "id": rec.get("id"),
        "event": _titulo_evento(prongs),
        "sport": prongs[0].get("sport_id", "?"),
        "sport_label": prongs[0].get("tournament") or prongs[0].get("sport_id", ""),
        "market": prongs[0].get("type", {}).get("variety", "outros"),
        "market_label": _rotulo_mercado(prongs[0].get("type", {})),
        "line": None,
        "profit_pct": round(rec.get("profit", 0.0), 2),
        "banca": banca,
        "commence_utc": commence_utc,
        "commence_br": _horario_brasilia(commence_utc),
        "lucro_brl": round(retorno - banca, 2),
        "updated_at": _agora_iso(),
        "legs": legs,
    }


# Reaproveita os helpers de horário do pipeline (evita duplicar).
from pipeline import _horario_brasilia, _agora_iso  # noqa: E402


def coletar():
    """Chama a surebet.com e devolve as surebets no formato-contrato."""
    params = {
        "product": "surebets",
        "source": "|".join(config.SUREBET_SOURCES),
        "sport": "|".join(config.SUREBET_SPORTS),
        "limit": 50,
    }
    headers = {"Authorization": f"Bearer {config.SUREBET_API_TOKEN}"}
    try:
        r = requests.get(config.SUREBET_API_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"!! Erro na surebet.com API: {e}")
        return []

    dados = r.json()
    surebets = []
    for rec in dados.get("records", []):
        c = _para_contrato(rec)
        if c:
            surebets.append(c)
    return surebets
