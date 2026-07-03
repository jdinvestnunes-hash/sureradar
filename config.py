"""
Configuração central do detector de surebets.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Chave da API (The Odds API). Vazia => MODO DEMO (dados de exemplo).
# ---------------------------------------------------------------------------
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
API_BASE_URL = "https://api.the-odds-api.com/v4"

# ---------------------------------------------------------------------------
# Login com Google (OAuth). Credenciais do Google Cloud Console.
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

# ---------------------------------------------------------------------------
# DONO(S) do sistema: e-mails sempre tratados como PRO (acesso total ao painel),
# independente do plano no banco. Separe por vírgula na env OWNER_EMAILS.
# ---------------------------------------------------------------------------
OWNER_EMAILS = {
    e.strip().lower()
    for e in os.getenv("OWNER_EMAILS", "jdinvestnunes@gmail.com").split(",")
    if e.strip()
}

# ---------------------------------------------------------------------------
# FONTE DE DADOS
#   "surebet"    -> surebet.com / apostasseguras (casas BR + mercados exóticos:
#                   escanteios, cartões, faltas... Já entrega a surebet PRONTA)
#   "theoddsapi" -> The Odds API (mainstream internacional; nós calculamos)
# ---------------------------------------------------------------------------
FONTE_DADOS = "surebet"

# --- surebet.com API (apostasseguras) ---
# Token de TESTE por padrão (surebets até 1%, 1 req/min). Troque no .env pelo
# seu token pago quando assinar (SUREBET_API_TOKEN=...).
SUREBET_API_TOKEN = os.getenv("SUREBET_API_TOKEN", "57cd1f13-fd58-4556-a5b9-05f2bcc2eab5").strip()
SUREBET_API_URL = "https://api.apostasseguras.com/request"

# Casas a monitorar (IDs da surebet.com). Máximo 10 no plano pago.
# SÓ casas que operam no BRASIL (removidas Marathon, Pinnacle, 1xBet, 22Bet,
# Betfair exchange — que não funcionam/limitam no Brasil).
SUREBET_SOURCES = [
    "bet365_bet_br",       # Bet365 (BR)
    "betano",              # Betano
    "betanobr",            # Betano (BR)
    "superbet",            # SuperBet (BR)
    "novibet",             # Novibet (BR)
    "betnacional",         # Betnacional (BR)
    "betsson",             # Betsson
    "pixbet",              # PixBet (BR)
    "betsul",              # Betsul (BR)
    "esportesdasorte_com", # Esportes da Sorte (BR)
]

# Esportes (IDs da surebet.com). Até 20 no plano pago.
SUREBET_SPORTS = ["Football", "Tennis", "Basketball", "Volleyball", "TableTennis"]

# ---------------------------------------------------------------------------
# CLASSIFICAÇÃO DAS CASAS ("realmente boas" vs varejo)
#
# A The Odds API (regiões eu/uk) NÃO traz casas BR de varejo (Betano, KTO...).
# Ela traz casas internacionais. Então classificamos cada casa em dois grupos:
#
#   - "sharp":  casas de referência que TOLERAM arbitragem (Pinnacle e as
#               exchanges). São as ideais para operar de verdade.
#   - "retail": demais casas (William Hill, Unibet, etc.). Servem para achar
#               surebets, mas podem limitar contas de arbitradores.
#
# Usamos TODAS as casas para detectar oportunidades; a etiqueta serve para o
# usuário filtrar no painel e saber onde está pisando.
# ---------------------------------------------------------------------------
CASAS_SHARP = {
    # IDs da The Odds API
    "pinnacle",
    "betfair_ex_eu",
    "betfair_ex_uk",
    "smarkets",
    "matchbook",
    "marathonbet",
    "sbobet",
    # IDs da surebet.com / apostasseguras
    "pinnaclesports",
    "betfair",
    "betdaq",
    "orbitxch",
    "sbobet",
    "sportmarket",
    "betinasia",
}


def classificar_casa(casa_key):
    """Devolve 'sharp' ou 'retail' para uma casa."""
    return "sharp" if casa_key in CASAS_SHARP else "retail"


# ---------------------------------------------------------------------------
# Parâmetros da estratégia
# ---------------------------------------------------------------------------
LUCRO_MINIMO_PCT = 0.0      # captura toda surebet POSITIVA (margem < 100%)
BANCA = 1000.0              # banca por entrada (cada aposta divide estes R$)

# REGRA: enviar apenas surebets de DUAS casas (2 resultados) — ex.: Over/Under,
# Handicap. Exclui mercados de 3 vias (1X2 com empate).
APENAS_DUAS_CASAS = True

# Faixa de lucro do GRUPO FREE (%). O grupo grátis recebe só surebets nesta
# faixa; acima de FREE_LUCRO_MAX fica reservado (futuro grupo pago/VIP).
FREE_LUCRO_MIN = 0.0
FREE_LUCRO_MAX = 2.0

# --- Divisão dos planos no painel ---
# FREE: vê só uma AMOSTRA de entradas de até 1% (as N mais próximas de 1%).
# PRO:  vê TODAS as entradas com lucro > PRO_LUCRO_MIN (%).
FREE_MAX_ENTRADAS = 12     # quantas entradas ≤1% o free enxerga
PRO_LUCRO_MIN = 1.0001     # piso do PRO: só acima de 1%

# Ligas a monitorar (keys da The Odds API). Ajustadas para o que está EM
# TEMPORADA agora (jul/2026).
ESPORTES = [
    "soccer_brazil_campeonato",          # Brasileirão Série A
    "soccer_brazil_serie_b",             # Brasileirão Série B
    "soccer_conmebol_copa_libertadores", # Libertadores
]

# Regiões = quais casas entram. Mais regiões = mais casas, MAS +custo.
# Disponíveis na The Odds API: eu, uk, us, us2, au. (Não há região BR.)
REGIOES = "eu,uk,us"

# MERCADOS monitorados. Cada mercado a mais MULTIPLICA o custo por região.
#   h2h     = Resultado (1X2 / Moneyline)
#   spreads = Handicap (asiático/europeu)
#   totals  = Over/Under (total de gols)
MERCADOS = ["h2h", "spreads", "totals"]

FORMATO_ODDS = "decimal"

# CUSTO POR RODADA ≈ len(ESPORTES) × len(MERCADOS) × (nº de regiões).
# Ex.: 3 ligas × 3 mercados × 3 regiões = 27 créditos POR RODADA.
# No free tier (500/mês) isso são só ~18 rodadas. Ajuste com cuidado.

# ---------------------------------------------------------------------------
# AGENDADOR (loop de fundo que alimenta o dashboard)
# ---------------------------------------------------------------------------
# Liga/desliga o loop automático de coleta. Em plano pago, deixe True.
# Liga/desliga o robô que raspa a FONTE DE TESTE (token surebet.com ≤1%).
# Em produção, quando a fonte real vem pela EXTENSÃO (conta paga), desligue
# isto com a variável AGENDADOR_ATIVO=0 no Railway — senão o teste sobrescreve.
AGENDADOR_ATIVO = os.getenv("AGENDADOR_ATIVO", "1") not in ("0", "false", "False", "no")

# Intervalo entre coletas, em segundos.
#   - surebet.com (teste): mínimo 60 (a API atualiza no máx. 1x/min no teste).
#   - The Odds API (créditos): use alto (ex.: 600) para não zerar a conta.
POLL_INTERVAL_SEG = 60

# Timer do PAINEL (dashboard) — de quanto em quanto tempo a tela se atualiza
# sozinha (tira as que sumiram, adiciona as novas). Independe do polling acima.
DASHBOARD_REFRESH_SEG = 600   # 10 minutos

# Guarda de segurança: se os créditos restantes na API caírem abaixo disto,
# o agendador PARA sozinho para não zerar sua conta.
MIN_CREDITOS_PARAR = 40

# ---------------------------------------------------------------------------
# TELEGRAM (alerta de surebets novas)
# ---------------------------------------------------------------------------
# Preencha no arquivo .env:
#   TELEGRAM_BOT_TOKEN=123456:ABC...   (do @BotFather)
#   TELEGRAM_CHAT_ID=-1001234567890    (ID do grupo; negativo p/ grupos)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
