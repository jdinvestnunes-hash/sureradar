"""
valor_feed.py — armazém EM MEMÓRIA das "odds de valor" (valuebets), TOTALMENTE
separado do feed de surebets (feed.py). Se algo aqui falhar, a surebet não é afetada.

Mesma lógica das surebets (feed.py): o robô manda a leva a cada ciclo e a gente
MESCLA por id (merge_valuebets) — odd nova entra, odd que mudou de preço atualiza
no lugar, e odd que sumiu só cai depois de _EXPIRY_SEG sem reaparecer. Assim o
painel NUNCA pisca vazio por causa de um ciclo magro / soluço de rede: só muda
quando entram novas ou quando as velhas realmente saem. Nada de banco.
"""

import threading
import time

_lock = threading.Lock()
# id -> (item_dict, last_seen_ts). Guardar por id + carimbo de tempo deixa MESCLAR
# raspagens (a odd que reaparece renova o tempo) e EXPIRAR as que sumiram da fonte.
_ITENS: dict = {}
# Rede de segurança: só remove por TEMPO se o robô parar de mandar. A remoção
# normal (a casa corrigiu o preço) acontece porque a odd não reaparece e expira.
# Tem que ser MAIOR que o ciclo do robô (~10 min) senão some antes da próxima leva.
_EXPIRY_SEG = 1800   # 30 min sem reaparecer -> aí sim sai da lista
_TS = 0.0            # quando chegou a última leva (qualquer uma)


def _validos(now=None):
    now = now or time.time()
    return [it for (it, ts) in _ITENS.values() if now - ts <= _EXPIRY_SEG]


def merge_valuebets(itens):
    """MESCLA a leva no conjunto atual: adiciona/atualiza por id e EXPIRA as que
    não reaparecem. Igual ao merge_surebets. Cada item precisa de 'id' estável."""
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


def set_valuebets(itens):
    """SUBSTITUI todo o conjunto (compat — evite; prefira merge_valuebets)."""
    global _ITENS, _TS
    now = time.time()
    with _lock:
        _ITENS = {it.get("id") or str(i): (it, now) for i, it in enumerate(itens or [])}
        _TS = now


def get_valuebets():
    """Odds de valor vivas (ou [] se todas expiraram / nunca chegou)."""
    with _lock:
        return _validos()


def status():
    return {"qtd": len(get_valuebets()), "atualizado_ts": _TS}
