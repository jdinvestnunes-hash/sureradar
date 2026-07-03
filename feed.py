"""
feed.py — a "fonte" de surebets que o dashboard lê.

>>> É AQUI QUE A API VAI SE PLUGAR DEPOIS. <<<

Por enquanto o feed está ZERADO: a lista de surebets começa vazia e o painel
mostra "aguardando conexão com a API".

Quando conectarmos a The Odds API, um processo em segundo plano (ver o TODO em
`atualizar_do_provedor`) vai chamar `set_surebets(...)` para empurrar as
oportunidades detectadas para cá — e elas aparecem no dashboard dos usuários.

O formato de cada surebet (dict) é o "contrato" entre back e front:

    {
      "id": "flamengo-palmeiras-h2h",   # identificador estável do evento+mercado
      "event": "Flamengo x Palmeiras",
      "sport": "soccer_brazil_campeonato",
      "sport_label": "Brasileirão",
      "market": "h2h",
      "profit_pct": 4.82,               # lucro garantido em %
      "updated_at": "2026-07-02T15:30:00Z",
      "legs": [
        {
          "outcome": "Flamengo",
          "odd": 2.60,
          "bookmaker": "pinnacle",
          "bookmaker_type": "sharp",    # "sharp" | "BR"
          "stake_pct": 41.2             # % da banca a apostar nesta perna
        },
        ...
      ]
    }
"""

import threading
import time

import config

# Armazém em memória. Protegido por lock porque o pipeline futuro vai escrever
# de outra thread enquanto o servidor lê.
_lock = threading.Lock()
_surebets: list = []          # <- ZERADO. O pipeline preenche isto depois.
_ultima_atualizacao: str = None
_ultima_ts: float = 0          # unix time da última atualização (p/ o timer)
_ingest_ts: float = 0          # unix time do último INGEST REAL (extensão/conta)


# ---------------------------------------------------------------------------
# LEITURA (usada pelo dashboard)
# ---------------------------------------------------------------------------
def get_surebets(min_profit=0.0, max_profit=None, bookmakers=None, sports=None):
    """
    Retorna as surebets que batem com os filtros do usuário.

    min_profit  : lucro mínimo em % (float).
    max_profit  : lucro máximo em % (float) ou None = sem teto.
    bookmakers  : lista de IDs de casas selecionadas, ou None = todas.
                  Uma surebet passa se TODAS as suas casas estão na seleção.
    sports      : lista de IDs de esporte, ou None = todos.
    """
    with _lock:
        dados = list(_surebets)

    sel = set(bookmakers) if bookmakers else None
    esportes = set(sports) if sports else None
    resultado = []
    for sb in dados:
        if sb["profit_pct"] < min_profit:
            continue
        if max_profit is not None and sb["profit_pct"] > max_profit:
            continue
        if esportes is not None and sb.get("sport") not in esportes:
            continue
        if sel is not None:
            casas = {leg["bookmaker"] for leg in sb["legs"]}
            if not casas.issubset(sel):
                continue
        resultado.append(sb)

    # Melhores lucros primeiro.
    resultado.sort(key=lambda s: s["profit_pct"], reverse=True)
    return resultado


def status():
    """Metadados do feed para o dashboard (contagem, se está conectado, etc.)."""
    with _lock:
        if config.FONTE_DADOS == "surebet":
            conectado = bool(config.SUREBET_API_TOKEN)
        else:
            conectado = bool(config.ODDS_API_KEY)
        return {
            "total": len(_surebets),
            "ultima_atualizacao": _ultima_atualizacao,
            "updated_ts": _ultima_ts,
            "conectado": conectado,
        }


# ---------------------------------------------------------------------------
# ESCRITA (usada pelo pipeline/API no futuro)
# ---------------------------------------------------------------------------
def set_surebets(lista, quando=None):
    """
    Substitui todo o conjunto atual de surebets. É isto que o pipeline chama a
    cada rodada de coleta de odds.
    """
    global _surebets, _ultima_atualizacao, _ultima_ts
    with _lock:
        _surebets = list(lista)
        _ultima_atualizacao = quando
        _ultima_ts = time.time()


def marcar_ingest():
    """Registra que a EXTENSÃO (conta paga real) acabou de alimentar o feed.

    O agendador de teste consulta `ingest_recente()` antes de escrever, e PULA
    a rodada se houver ingest recente — assim os dados reais não são
    sobrescritos pelos dados de teste (≤1%)."""
    global _ingest_ts
    with _lock:
        _ingest_ts = time.time()


def ingest_recente(janela_seg=900):
    """True se a extensão alimentou o feed nos últimos `janela_seg` segundos
    (padrão 15 min). Enquanto True, o robô de teste NÃO sobrescreve.

    Se a extensão parar (navegador fechado) por mais que a janela, volta False
    e o teste reassume como backup — o painel nunca fica vazio."""
    with _lock:
        return _ingest_ts > 0 and (time.time() - _ingest_ts) < janela_seg


def atualizar_do_provedor():
    """
    TODO (quando linkarmos a API):
        1. eventos = odds_api.buscar_eventos()
        2. para cada evento: calcular surebets com arbitrage.calcular_surebet
        3. converter para o formato-contrato acima
        4. set_surebets(surebets, quando=agora_iso())

    Este é o ÚNICO ponto que precisa mudar para ligar dados reais.
    Um agendador (APScheduler / loop com sleep) vai chamar esta função a cada
    N segundos. Por enquanto não faz nada — feed permanece zerado.
    """
    pass
