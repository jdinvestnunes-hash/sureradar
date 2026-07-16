"""
valor_feed.py — armazém EM MEMÓRIA das "odds de valor" (valuebets), TOTALMENTE
separado do feed de surebets (feed.py). Se algo aqui falhar, a surebet não é afetada.

O scraper posta a lista completa a cada ciclo (set_valuebets); expira sozinho se
parar de chegar (rede de segurança). Nada de banco/estado compartilhado com o feed.
"""

import time

_ITENS = []          # lista de dicts (contrato do painel)
_TS = 0.0            # quando chegou a última leva
_EXPIRY_SEG = 1800   # 30 min sem atualização -> considera vazio


def set_valuebets(itens):
    """Substitui as odds de valor pelo que veio da raspagem (snapshot)."""
    global _ITENS, _TS
    _ITENS = list(itens or [])
    _TS = time.time()


def get_valuebets():
    """Odds de valor vivas (ou [] se expirou / nunca chegou)."""
    if not _ITENS or (time.time() - _TS) > _EXPIRY_SEG:
        return []
    return _ITENS


def status():
    return {"qtd": len(get_valuebets()), "atualizado_ts": _TS}
