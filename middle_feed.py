"""
middle_feed.py — armazém EM MEMÓRIA das "apostas de intervalo" (middles), TOTALMENTE
separado do feed de surebets (feed.py) e do de valuebets (valor_feed.py). Se algo aqui
falhar, nem a surebet nem as odds erradas são afetadas.

Mesma lógica dos outros feeds: o robô manda a leva a cada ciclo e a gente MESCLA por id
(merge_middles) — aposta nova entra, a que mudou de preço atualiza no lugar, e a que
sumiu só cai depois de _EXPIRY_SEG sem reaparecer. Assim o painel NUNCA pisca vazio por
causa de um ciclo magro / soluço de rede. Nada de banco (o cache no Postgres é à parte).
"""

import threading
import time

_lock = threading.Lock()
# id -> (item_dict, last_seen_ts)
_ITENS: dict = {}
_EXPIRY_SEG = 1800   # 30 min sem reaparecer -> sai da lista (maior que o ciclo do robô)
_TS = 0.0            # quando chegou a última leva


def _validos(now=None):
    now = now or time.time()
    return [it for (it, ts) in _ITENS.values() if now - ts <= _EXPIRY_SEG]


def merge_middles(itens):
    """MESCLA a leva no conjunto atual: adiciona/atualiza por id e EXPIRA as que não
    reaparecem. Cada item precisa de 'id' estável."""
    global _TS
    now = time.time()
    with _lock:
        for it in (itens or []):
            iid = it.get("id")
            if iid:
                _ITENS[iid] = (it, now)
        mortos = [k for k, (it, ts) in _ITENS.items() if now - ts > _EXPIRY_SEG]
        for k in mortos:
            del _ITENS[k]
        _TS = now


def get_middles():
    """Apostas de intervalo vivas (ou [] se todas expiraram / nunca chegou)."""
    with _lock:
        return _validos()


def status():
    return {"qtd": len(get_middles()), "atualizado_ts": _TS}
