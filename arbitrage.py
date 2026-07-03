"""
A matemática da arbitragem (surebet).

Conceito central:
  Para um evento com N resultados possíveis (ex.: casa vence / empate / fora vence),
  pegamos a MELHOR odd de cada resultado (mesmo que venham de casas diferentes) e
  somamos os inversos:

        margem = 1/odd_1 + 1/odd_2 + ... + 1/odd_N

  - margem  < 1.0  ->  EXISTE surebet. Lucro garantido = (1/margem - 1) * 100 %
  - margem >= 1.0  ->  não há arbitragem.

Depois calculamos quanto apostar em cada resultado para travar o mesmo lucro
independente de quem ganha (stakes proporcionais a 1/odd).
"""

from dataclasses import dataclass


@dataclass
class MelhorOdd:
    """A melhor odd encontrada para um resultado específico."""
    resultado: str   # ex.: "Flamengo", "Draw", "Palmeiras"
    odd: float
    casa: str        # de qual casa veio essa odd
    link: str = None # link direto do mercado na casa (se a API fornecer)


@dataclass
class Surebet:
    """Um surebet detectado, já com as stakes calculadas."""
    evento: str
    esporte: str
    pernas: list      # lista de dicts: {resultado, odd, casa, stake, retorno}
    margem: float     # soma dos inversos (< 1 significa lucro)
    lucro_pct: float  # lucro garantido em %
    banca: float


def calcular_surebet(evento, esporte, melhores_odds, banca):
    """
    Recebe as melhores odds de cada resultado e devolve um Surebet se houver
    arbitragem, ou None caso contrário.

    melhores_odds: lista de MelhorOdd (uma por resultado possível).
    """
    if not melhores_odds:
        return None

    # margem = soma dos inversos das melhores odds.
    margem = sum(1.0 / mo.odd for mo in melhores_odds)

    # Sem arbitragem se a margem não for menor que 1.
    if margem >= 1.0:
        return None

    lucro_pct = (1.0 / margem - 1.0) * 100.0

    # Stake de cada perna, proporcional a (1/odd), normalizada pela banca.
    # Assim todos os resultados retornam o MESMO valor -> lucro travado.
    pernas = []
    for mo in melhores_odds:
        stake = banca * (1.0 / mo.odd) / margem
        retorno = stake * mo.odd
        pernas.append({
            "resultado": mo.resultado,
            "odd": mo.odd,
            "casa": mo.casa,
            "link": mo.link,
            "stake": round(stake, 2),
            "retorno": round(retorno, 2),
        })

    return Surebet(
        evento=evento,
        esporte=esporte,
        pernas=pernas,
        margem=margem,
        lucro_pct=lucro_pct,
        banca=banca,
    )


def _melhor_por_nome(itens):
    """Dado uma lista de cotações, devolve {nome: MelhorOdd} (a maior odd)."""
    melhor = {}
    for c in itens:
        r = c["resultado"]
        if r not in melhor or c["odd"] > melhor[r].odd:
            melhor[r] = MelhorOdd(resultado=r, odd=c["odd"], casa=c["casa"],
                                  link=c.get("link"))
    return melhor


def _eh_meia_linha(p):
    """True se a linha é .5 (ex.: 2.5, -1.5) — evita empate/reembolso (push)."""
    return p is not None and (abs(p) * 2) % 2 == 1


def montar_grupos(cotacoes):
    """
    Agrupa as cotações de UM evento em conjuntos de resultados mutuamente
    exclusivos e completos (uma "aposta de arbitragem" possível cada).

    Retorna lista de dicts:
        {market, line, label, opcoes: [MelhorOdd, ...]}

    - h2h:     um grupo (todos os resultados 1X2).
    - totals:  um grupo por linha (Over/Under na MESMA linha .5).
    - spreads: um grupo por linha (times em handicaps complementares .5).
    """
    from collections import defaultdict

    por_mercado = defaultdict(list)
    for c in cotacoes:
        por_mercado[c.get("market", "h2h")].append(c)

    grupos = []

    # ---- h2h (1X2 / moneyline) ----
    if por_mercado.get("h2h"):
        melhor = _melhor_por_nome(por_mercado["h2h"])
        if len(melhor) >= 2:
            grupos.append({
                "market": "h2h",
                "line": None,
                "label": "Resultado (1X2)",
                "opcoes": list(melhor.values()),
            })

    # ---- totals (Over/Under) — casar a MESMA linha ----
    por_linha = defaultdict(list)
    for c in por_mercado.get("totals", []):
        if _eh_meia_linha(c.get("point")):
            por_linha[c["point"]].append(c)
    for linha, itens in por_linha.items():
        melhor = _melhor_por_nome(itens)
        if "Over" in melhor and "Under" in melhor:
            grupos.append({
                "market": "totals",
                "line": linha,
                "label": f"Over/Under {linha:g}",
                "opcoes": [melhor["Over"], melhor["Under"]],
            })

    # ---- spreads (handicap) — casar lados complementares .5 ----
    best = {}   # (nome, point) -> MelhorOdd
    nomes = set()
    for c in por_mercado.get("spreads", []):
        p = c.get("point")
        if not _eh_meia_linha(p):
            continue
        nomes.add(c["resultado"])
        chave = (c["resultado"], p)
        if chave not in best or c["odd"] > best[chave].odd:
            best[chave] = MelhorOdd(resultado=c["resultado"], odd=c["odd"],
                                    casa=c["casa"], link=c.get("link"))
    nomes = list(nomes)
    if len(nomes) == 2:
        n1, n2 = nomes
        vistos = set()
        for (nome, p), mo in best.items():
            if nome != n1:
                continue
            comp = best.get((n2, -p))          # lado oposto na linha espelhada
            if comp is None or abs(p) in vistos:
                continue
            vistos.add(abs(p))
            grupos.append({
                "market": "spreads",
                "line": p,
                "label": f"Handicap {p:+g}",
                "opcoes": [mo, comp],
            })

    return grupos


def melhores_odds_por_resultado(cotacoes):
    """
    A partir de todas as cotações de um evento (de várias casas), escolhe a
    MAIOR odd para cada resultado.

    cotacoes: lista de dicts {resultado, odd, casa}.
    Retorna: lista de MelhorOdd (uma por resultado distinto).
    """
    melhor = {}  # resultado -> MelhorOdd
    for c in cotacoes:
        r = c["resultado"]
        if r not in melhor or c["odd"] > melhor[r].odd:
            melhor[r] = MelhorOdd(resultado=r, odd=c["odd"], casa=c["casa"])
    return list(melhor.values())
