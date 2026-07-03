"""
pipeline.py — liga a API real ao dashboard.

Fluxo de cada rodada:
  1. odds_api.buscar_eventos()      -> odds reais de várias casas
  2. para cada evento: acha as melhores odds e calcula a arbitragem
  3. converte cada surebet para o "contrato" que o dashboard entende
  4. feed.set_surebets(...)         -> aparece no painel dos usuários

Também contém o AGENDADOR (loop de fundo) que repete isso a cada
config.POLL_INTERVAL_SEG segundos, com um guarda de créditos.
"""

import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BRASILIA = ZoneInfo("America/Sao_Paulo")

import config
import feed
import notifier
import odds_api
from arbitrage import calcular_surebet, montar_grupos

# Rótulos amigáveis por liga (espelha os do app.py).
SPORT_LABELS = {
    "soccer_brazil_campeonato": "Brasileirão Série A",
    "soccer_brazil_serie_b": "Brasileirão Série B",
    "soccer_conmebol_copa_libertadores": "Libertadores",
    "soccer_epl": "Premier League",
}


def _agora_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _horario_brasilia(commence_utc):
    """Converte o commence_time (UTC ISO) para horário de Brasília, formatado."""
    if not commence_utc:
        return None
    try:
        dt = datetime.fromisoformat(commence_utc.replace("Z", "+00:00"))
        return dt.astimezone(BRASILIA).strftime("%d/%m/%Y %H:%M")
    except (ValueError, AttributeError):
        return None


def _slug(texto):
    return "".join(c.lower() if c.isalnum() else "-" for c in texto).strip("-")


def _para_contrato(sb, sport_key, grupo, commence_utc=None):
    """Converte um Surebet (de arbitrage.py) + o grupo de mercado no dict do front."""
    pernas = []
    for p in sb.pernas:
        pernas.append({
            "outcome": p["resultado"],
            "odd": p["odd"],
            "bookmaker": p["casa"],
            "bookmaker_type": config.classificar_casa(p["casa"]),
            "stake_pct": round(p["stake"] / sb.banca * 100, 1),
            "stake_brl": round(p["stake"], 2),   # quanto apostar em R$ (banca config.BANCA)
            "link": p.get("link"),               # link direto da aposta na casa
        })
    linha = grupo["line"]
    linha_txt = "" if linha is None else f"-{linha:g}"
    return {
        "id": f"{_slug(sb.evento)}-{grupo['market']}{linha_txt}",
        "event": sb.evento,
        "sport": sport_key,
        "sport_label": SPORT_LABELS.get(sport_key, sb.esporte),
        "market": grupo["market"],            # h2h | spreads | totals (p/ filtro)
        "market_label": grupo["label"],       # texto amigável (p/ exibição)
        "line": linha,
        "profit_pct": round(sb.lucro_pct, 2),
        "banca": config.BANCA,
        "commence_utc": commence_utc,
        "commence_br": _horario_brasilia(commence_utc),
        "lucro_brl": round(sb.pernas[0]["retorno"] - config.BANCA, 2),
        "updated_at": _agora_iso(),
        "legs": pernas,
    }


def coletar():
    """Executa UMA rodada de coleta e devolve a lista de surebets (contrato)."""
    # Fonte surebet.com: já entrega as surebets prontas (casas BR + props).
    if config.FONTE_DADOS == "surebet":
        import surebet_provider
        surebets = surebet_provider.coletar()
        # aplica as regras de negócio (2 casas distintas, positiva)
        filtradas = []
        for s in surebets:
            if s["profit_pct"] <= 0:
                continue
            casas = {p["bookmaker"] for p in s["legs"]}
            if config.APENAS_DUAS_CASAS and (len(s["legs"]) != 2 or len(casas) < 2):
                continue
            filtradas.append(s)
        return filtradas

    # Fonte The Odds API: nós calculamos a arbitragem.
    eventos = odds_api.buscar_eventos()
    surebets = []

    for ev in eventos:
        for grupo in montar_grupos(ev["cotacoes"]):
            # Regra: apenas surebets de 2 casas (2 resultados).
            if config.APENAS_DUAS_CASAS and len(grupo["opcoes"]) != 2:
                continue
            sb = calcular_surebet(ev["evento"], ev["esporte"], grupo["opcoes"], config.BANCA)
            if not sb or sb.lucro_pct < config.LUCRO_MINIMO_PCT:
                continue
            # As 2 pernas precisam ser de casas DIFERENTES (arbitragem real).
            casas = {p["casa"] for p in sb.pernas}
            if config.APENAS_DUAS_CASAS and len(casas) < 2:
                continue
            surebets.append(
                _para_contrato(sb, ev.get("sport_key", "?"), grupo,
                               ev.get("commence_time"))
            )

    return surebets


def _vai_para_free(sb):
    """True se a surebet entra na faixa do grupo Free (0–2% por padrão)."""
    return config.FREE_LUCRO_MIN <= sb["profit_pct"] <= config.FREE_LUCRO_MAX


# Guarda os IDs da rodada anterior para detectar o que é NOVO (evita spam).
_ids_anteriores = set()


def rodar_uma_vez():
    """Coleta, publica no feed e alerta no Telegram as surebets NOVAS."""
    global _ids_anteriores
    # Se a EXTENSÃO (conta paga real) alimentou o feed há pouco, NÃO sobrescreve
    # com os dados de teste (≤1%). O teste só reassume se a conta parar >15 min.
    if feed.ingest_recente():
        print(">> Ingest recente da conta — agendador de teste PULA esta rodada.")
        return -1
    surebets = coletar()
    feed.set_surebets(surebets, quando=_agora_iso())

    # Alerta no Telegram: só as NOVAS (não existiam na rodada anterior) E que
    # entram na faixa do grupo Free (0–2%). Positivas por definição.
    ids_atuais = {s["id"] for s in surebets}
    novas = [s for s in surebets
             if s["id"] not in _ids_anteriores and _vai_para_free(s)]
    if novas and notifier.ativo():
        for s in novas:
            notifier.enviar_surebet(s)
        print(f">> {len(novas)} surebet(s) nova(s) enviada(s) ao grupo Free.")
    _ids_anteriores = ids_atuais

    print(f">> Rodada concluída: {len(surebets)} surebet(s) no painel "
          f"({len(novas)} nova(s)).")
    return len(surebets)


# ---------------------------------------------------------------------------
# Agendador em thread de fundo
# ---------------------------------------------------------------------------
_thread = None
_parar = threading.Event()


def _loop():
    while not _parar.is_set():
        # Guarda de créditos: para antes de zerar a conta.
        restante = odds_api.ultimos_creditos_restantes
        if restante is not None and restante < config.MIN_CREDITOS_PARAR:
            print(
                f"!! Créditos baixos ({restante} < {config.MIN_CREDITOS_PARAR}). "
                "Agendador PAUSADO para preservar sua conta. "
                "Aumente o plano ou o intervalo em config.py."
            )
            return
        try:
            rodar_uma_vez()
        except Exception as e:
            print(f"!! Erro na rodada do agendador: {e}")
        # Espera o intervalo, mas acorda cedo se pedirem para parar.
        _parar.wait(config.POLL_INTERVAL_SEG)


def iniciar_agendador():
    """Sobe o loop de fundo (idempotente). Chamado pelo app.py no startup."""
    global _thread
    if not config.AGENDADOR_ATIVO:
        print(">> Agendador DESATIVADO (config.AGENDADOR_ATIVO = False).")
        return
    # Precisa do token da fonte escolhida.
    if config.FONTE_DADOS == "surebet" and not config.SUREBET_API_TOKEN:
        print(">> Sem SUREBET_API_TOKEN: agendador não iniciado.")
        return
    if config.FONTE_DADOS == "theoddsapi" and not config.ODDS_API_KEY:
        print(">> Sem ODDS_API_KEY: agendador não iniciado (feed fica zerado).")
        return
    if _thread and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_loop, name="surebet-scheduler", daemon=True)
    _thread.start()
    print(f">> Agendador iniciado (a cada {config.POLL_INTERVAL_SEG}s).")


def parar_agendador():
    _parar.set()


if __name__ == "__main__":
    # Permite rodar uma coleta única pelo terminal: python pipeline.py
    rodar_uma_vez()
