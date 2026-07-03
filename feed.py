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
# id -> (surebet_dict, last_seen_ts). Guardamos por id + carimbo de tempo para
# poder MESCLAR raspagens parciais (ex.: view "≤1%" e view "PRO" vêm em ingests
# separados) sem uma apagar a outra, e EXPIRAR as que sumiram da fonte.
_bets: dict = {}
_EXPIRY_SEG = 720             # 12 min sem reaparecer -> a surebet é removida
_ultima_atualizacao: str = None
_ultima_ts: float = 0          # unix time da última atualização (p/ o timer)
_ingest_ts: float = 0          # unix time do último INGEST REAL (extensão/conta)


def _validos(now=None):
    """Surebets ainda 'vivas' (vistas há menos de _EXPIRY_SEG)."""
    now = now or time.time()
    return [b for (b, ts) in _bets.values() if now - ts <= _EXPIRY_SEG]


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
        dados = _validos()

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
            "total": len(_validos()),
            "ultima_atualizacao": _ultima_atualizacao,
            "updated_ts": _ultima_ts,
            "conectado": conectado,
        }


# ---------------------------------------------------------------------------
# ESCRITA (usada pelo pipeline/API no futuro)
# ---------------------------------------------------------------------------
def set_surebets(lista, quando=None):
    """SUBSTITUI todo o conjunto (usado pelo agendador de teste)."""
    global _bets, _ultima_atualizacao, _ultima_ts
    now = time.time()
    with _lock:
        _bets = {b["id"]: (b, now) for b in lista}
        _ultima_atualizacao = quando
        _ultima_ts = now


def merge_surebets(lista, quando=None):
    """MESCLA no conjunto atual (usado pela extensão): adiciona/atualiza por id e
    EXPIRA as que não reaparecem. Assim a raspagem da view '≤1%' e da view 'PRO'
    (ingests separados) se SOMAM, e uma raspagem parcial não zera o resto."""
    global _ultima_atualizacao, _ultima_ts
    now = time.time()
    with _lock:
        for b in lista:
            if b.get("id"):
                _bets[b["id"]] = (b, now)
        mortos = [k for k, (b, ts) in _bets.items() if now - ts > _EXPIRY_SEG]
        for k in mortos:
            del _bets[k]
        _ultima_atualizacao = quando
        _ultima_ts = now


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
