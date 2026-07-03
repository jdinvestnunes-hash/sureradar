"""
Cliente da The Odds API + dados de demonstração.

Se ODDS_API_KEY estiver preenchida, busca odds reais.
Se estiver vazia, devolve um jogo de exemplo (com um surebet plantado) para
você testar o detector sem gastar requisições nem precisar de conta.
"""

import requests

import config

# Últimos créditos restantes reportados pela API (atualizado a cada chamada).
# O agendador usa isto para parar antes de zerar a conta. None = desconhecido.
ultimos_creditos_restantes = None


def _normalizar_evento(evento_api):
    """
    Converte um evento da The Odds API para o formato interno:
      {evento, sport_key, esporte, cotacoes: [{resultado, odd, casa}, ...]}.
    Usa TODAS as casas (a classificação sharp/retail é feita depois).
    """
    nome = f"{evento_api['home_team']} x {evento_api['away_team']}"
    sport_key = evento_api.get("sport_key", "?")
    esporte = evento_api.get("sport_title", sport_key)
    cotacoes = []

    for casa in evento_api.get("bookmakers", []):
        casa_key = casa["key"]
        link_casa = casa.get("link")
        for mercado in casa.get("markets", []):
            mk = mercado["key"]
            if mk not in config.MERCADOS:
                continue
            link_mercado = mercado.get("link") or link_casa
            for outcome in mercado.get("outcomes", []):
                cotacoes.append({
                    "resultado": outcome["name"],
                    "odd": float(outcome["price"]),
                    "casa": casa_key,
                    "market": mk,
                    "point": outcome.get("point"),  # linha (handicap/total); None p/ h2h
                    "link": outcome.get("link") or link_mercado,  # aposta na casa
                })

    return {
        "evento": nome,
        "sport_key": sport_key,
        "esporte": esporte,
        "commence_time": evento_api.get("commence_time"),  # UTC ISO
        "cotacoes": cotacoes,
    }


def buscar_eventos():
    """
    Retorna uma lista de eventos normalizados:
      [{evento, esporte, cotacoes: [{resultado, odd, casa}, ...]}, ...]
    """
    if not config.ODDS_API_KEY:
        print(">> MODO DEMO (sem ODDS_API_KEY): usando dados de exemplo.\n")
        return _eventos_demo()

    eventos = []
    for esporte in config.ESPORTES:
        url = f"{config.API_BASE_URL}/sports/{esporte}/odds"
        params = {
            "apiKey": config.ODDS_API_KEY,
            "regions": config.REGIOES,
            "markets": ",".join(config.MERCADOS),
            "oddsFormat": config.FORMATO_ODDS,
            "includeLinks": "true",  # traz o link direto da aposta na casa
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"!! Erro ao buscar {esporte}: {e}")
            continue

        # Cabeçalhos úteis: quota restante da API.
        restante = resp.headers.get("x-requests-remaining")
        if restante is not None:
            global ultimos_creditos_restantes
            ultimos_creditos_restantes = int(restante)
            print(f"   [{esporte}] créditos restantes na API: {restante}")

        for ev in resp.json():
            eventos.append(_normalizar_evento(ev))

    return eventos


def _eventos_demo():
    """Dois eventos de exemplo. O primeiro tem um surebet de ~4,8%."""
    exemplos = [
        {
            "home_team": "Flamengo",
            "away_team": "Palmeiras",
            "sport_title": "Brasileirão (DEMO)",
            "sport_key": "soccer_brazil_campeonato",
            "bookmakers": [
                {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Flamengo", "price": 2.60},
                    {"name": "Draw", "price": 3.10},
                    {"name": "Palmeiras", "price": 3.20},
                ]}]},
                {"key": "betano", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Flamengo", "price": 2.45},
                    {"name": "Draw", "price": 3.60},   # melhor empate
                    {"name": "Palmeiras", "price": 3.00},
                ]}]},
                {"key": "betfair_ex_eu", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Flamengo", "price": 2.55},
                    {"name": "Draw", "price": 3.30},
                    {"name": "Palmeiras", "price": 3.70},  # melhor fora
                ]}]},
            ],
        },
        {
            "home_team": "Manchester City",
            "away_team": "Liverpool",
            "sport_title": "Premier League (DEMO)",
            "sport_key": "soccer_epl",
            "bookmakers": [
                {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Manchester City", "price": 1.90},
                    {"name": "Draw", "price": 3.80},
                    {"name": "Liverpool", "price": 4.00},
                ]}]},
                {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Manchester City", "price": 1.95},
                    {"name": "Draw", "price": 3.70},
                    {"name": "Liverpool", "price": 3.90},
                ]}]},
            ],
        },
    ]
    return [_normalizar_evento(e) for e in exemplos]
