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
# ADMIN: e-mails que acessam o painel /admin (dar/renovar PRO com os dias que
# você escolher). NÃO dá PRO automático — só libera o painel. Env ADMIN_EMAILS.
# ---------------------------------------------------------------------------
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.getenv("ADMIN_EMAILS", "jdinvestnunes@gmail.com").split(",")
    if e.strip()
}

# E-mails de TESTE do dono — EXCLUÍDOS de broadcasts (aviso de parcelamento etc.) e
# do painel "geraram e não pagaram". Env EMAILS_EXCLUIR (vírgula). Não cravar e-mails
# aqui: o repositório é público. Ex.: EMAILS_EXCLUIR="a@x.com,b@y.com".
EMAILS_EXCLUIR = {
    e.strip().lower()
    for e in os.getenv("EMAILS_EXCLUIR", "").split(",")
    if e.strip()
}

# Senha do painel admin (2º fator, além do e-mail). Defina ADMIN_PASSWORD no
# Railway. Se ficar vazia, o painel admin fica BLOQUEADO (segurança).
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

# IPs liberados a acessar o /admin (allowlist). Separe por vírgula em ADMIN_IPS.
# Se VAZIO, não filtra por IP (vale só e-mail+senha). Se preenchido, SÓ esses IPs
# passam — quem não estiver na lista nem enxerga a página (404).
ADMIN_IPS = {
    ip.strip() for ip in os.getenv("ADMIN_IPS", "").split(",") if ip.strip()
}

# Avisar no perfil para renovar quando faltar este nº de dias (ou menos).
AVISO_RENOVACAO_DIAS = 5

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
# faixa; acima de FREE_LUCRO_MAX (= piso do PRO) fica reservado ao PRO.
FREE_LUCRO_MIN = 1.0
FREE_LUCRO_MAX = 2.0

# --- Divisão dos planos no painel ---
# FREE: vê só uma AMOSTRA de entradas de 1% a 2% (as N mais próximas de 2%).
# PRO:  vê TODAS as entradas com lucro > PRO_LUCRO_MIN (%).
FREE_MAX_ENTRADAS = 25     # FREE vê as 25 primeiras de 1% a 2%
PRO_LUCRO_MIN = 2.0001     # PRO vê TUDO acima de 2% (até o teto de 25%)

# Odds Erradas (add-on avulso): o não-pagante vê a lista TODA, mas só as N de MENOR
# valor abertas (valor real de graça) — as maiores vêm borradas, o prêmio de quem
# paga. Esse número mora no front (VALOR_ABERTAS em app.js), aqui fica só de
# referência. Quem tem o add-on recebe todas abertas com os links.

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
DASHBOARD_REFRESH_SEG = 60    # 60s — casa com a passada rápida do robô (~75s):
                              # surebet nova aparece na tela do cliente em ~1-2 min

# CATÁLOGO DE FILTROS (casas/esportes do dashboard): uma casa/esporte só fica no
# filtro enquanto aparece na raspagem. Se ficar mais que isto SEM aparecer em
# nenhuma surebet, sai do filtro sozinho. Assim, quando você TIRA uma casa lá na
# fonte (surebet.com), ela some do dashboard depois desta janela — sem catálogo
# eterno. Curto demais faz casa "quieta" piscar; 6h é folga segura.
CATALOGO_TTL_SEG = 6 * 3600    # 6 horas

# ROBÔ OFFLINE: se a última raspagem chegou há MAIS que isto, o painel entende
# que o robô parou e mostra o aviso "reconferindo as apostas". O ciclo real do
# robô é ~13-14 min (raspagem + envio), então 20 min dá folga pra não dar
# falso-alarme entre ciclos, e ainda avisa ANTES do feed expirar (25 min).
ROBO_OFFLINE_SEG = 1200        # 20 minutos sem raspagem -> avisa que está offline

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
# Chat PRIVADO do admin (você) — recebe aviso de cada VENDA. Vazio = desligado.
# Pegue o seu chat_id mandando qualquer mensagem pro bot e vendo em /api/admin/telegram-chats.
ADMIN_TELEGRAM_CHAT_ID = os.getenv("ADMIN_TELEGRAM_CHAT_ID", "").strip()

# --- GUARDIÃO do robô (vigia na nuvem: alerta de queda + comandos /ligar /desligar) ---
# Liga o guardião (thread na Railway que fica de olho na idade do feed e escuta o bot).
GUARDIAO_ATIVO = os.getenv("GUARDIAO_ATIVO", "1").strip() not in ("0", "false", "False", "")
# Minutos SEM dados novos até te alertar "CAIU" no Telegram. 15 = seguro (o robô
# atualiza a cada ~1 min, então 15 min parado é certeza de queda, sem alarme falso).
ROBO_ALERTA_MIN = int(os.getenv("ROBO_ALERTA_MIN", "15"))

# Alertas personalizados no Telegram (surebet que bate com os filtros do usuário
# cai na DM dele). BETA: liberado só para estes e-mails (separados por vírgula).
# Ex.: ALERTA_BETA_EMAILS=leosaper12@gmail.com
ALERTA_BETA_EMAILS = os.getenv("ALERTA_BETA_EMAILS", "").strip()

# Aba "Odds de Valor" (valuebets) em BETA: só aparece pros e-mails desta lista, pra
# testar o visual antes de liberar geral. Env VALUEBET_BETA_EMAILS (vírgula).
VALUEBET_BETA_EMAILS = os.getenv("VALUEBET_BETA_EMAILS", "").strip()

# Aba "Apostas de Intervalo" (middles) em BETA FECHADO: a aba SÓ existe pros e-mails
# desta lista (é onde o Leo vê pra entender o produto). Ninguém mais vê a aba. Env
# MIDDLE_BETA_EMAILS (vírgula). Pôr leosapper12@gmail.com no Railway.
MIDDLE_BETA_EMAILS = os.getenv("MIDDLE_BETA_EMAILS", "").strip()

# MODO VITRINE das Odds Erradas: enquanto ligado, NINGUÉM tem acesso total — todo
# mundo (inclusive quem comprou o add-on, PRO e você) vê só a AMOSTRA real borrada,
# pra criar desejo de desbloquear. É o estado de LANÇAMENTO. Pra liberar de verdade
# quem paga, põe a env VALOR_TEASER_GERAL=0 no Railway (não precisa mexer no código).
VALOR_TEASER_GERAL = os.getenv("VALOR_TEASER_GERAL", "1").strip().lower() not in ("0", "false", "nao", "não", "off", "")

# Grupo FREE do Telegram: recebe as surebets até este lucro (%), no máximo N por
# ciclo (evita flood). As de maior lucro ficam pro PRO (funil).
TELEGRAM_LUCRO_MAX = float(os.getenv("TELEGRAM_LUCRO_MAX", "1.0"))
TELEGRAM_MAX_POR_RODADA = int(os.getenv("TELEGRAM_MAX_POR_RODADA", "3"))

# URL do site (usada no rodapé/CTA das mensagens do Telegram e nos checkouts).
SITE_URL = os.getenv("SITE_URL", "https://sureradar.site").strip()

# ---------------------------------------------------------------------------
# PAGAMENTOS — planos e gateways (Stripe = cartão, AbacatePay = Pix)
# ---------------------------------------------------------------------------
# Pagamento único que libera N dias de PRO (renovação manual, com aviso). Não é
# assinatura recorrente por enquanto — casa com o modelo de dias_restantes.
PLANOS = {
    "mensal":     {"nome": "Pro Mensal",     "dias": 30,  "valor": 147.0},
    "trimestral": {"nome": "Pro Trimestral", "dias": 90,  "valor": 237.0},
    "semestral":  {"nome": "Pro Semestral",  "dias": 180, "valor": 387.0},
    "anual":      {"nome": "Pro Anual",      "dias": 365, "valor": 497.0},
}

# ADD-ON "Odds Erradas das Casas" (valuebets). Vendido À PARTE do plano: como
# ORDER BUMP no checkout (caixinha marcável) ou avulso. Pagamento único que libera
# N dias de acesso à aba — SOMA nos dias que já tiver, igual o PRO.
ADDON_VALOR = {"nome": "Odds Erradas das Casas", "dias": 30, "valor": 47.0}

# ADD-ON "Apostas de Intervalo" (middles). Também vendido À PARTE: avulso ou dentro
# do COMBO "Completo" junto do plano. Pagamento único, SOMA nos dias — igual o PRO.
# Avulso é PREMIUM (R$97): sozinho custa bem mais do que dentro do combo (incentivo).
ADDON_MIDDLE = {"nome": "Apostas de Intervalo", "dias": 30, "valor": 97.0}

# COMBO "Completo" = plano + os DOIS add-ons (Odds Erradas + Apostas de Intervalo)
# no MESMO checkout, por um EXTRA promocional (bem menor que os R$47+R$97 avulsos) e
# válido pelos MESMOS dias do plano. É o "pulo do gato" do checkout. Por plano:
#   mensal +50 (=197) · trimestral +80 (=317) · semestral +130 (=517) · anual +0.
# No ANUAL os dois já vêm de BRINDE (extra 0) — ver _liberar_compra no auth.py.
COMBO_EXTRA = {"mensal": 97.0, "trimestral": 127.0, "semestral": 177.0, "anual": 0.0}

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

ABACATEPAY_API_KEY = os.getenv("ABACATEPAY_API_KEY", "").strip()
# Chave da API v2 (Checkout — necessária p/ PARCELAMENTO no cartão). Se vazia, cai
# na chave v1 acima (algumas contas usam a mesma). Gere no painel da AbacatePay.
ABACATEPAY_V2_API_KEY = os.getenv("ABACATEPAY_V2_API_KEY", "").strip()
ABACATEPAY_WEBHOOK_SECRET = os.getenv("ABACATEPAY_WEBHOOK_SECRET", "").strip()

# E-mail transacional (Resend) — recuperar senha, etc.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
# Fluxo de nutrição por e-mail (boas-vindas + nudges pró p/ quem não comprou).
LIFECYCLE_ATIVO = os.getenv("LIFECYCLE_ATIVO", "1") not in ("0", "false", "False", "no")
# Régua de recuperação por e-mail (quem gerou checkout Pix/cartão e não pagou).
RECUP_ATIVO = os.getenv("RECUP_ATIVO", "1") not in ("0", "false", "False", "no")
# Retenção do PRO por e-mail: lembrete de vencimento (D-5/D-3/D-0, só mensal/tri/sem)
# + win-back de quem venceu (D+3/D+10). Anual não recebe. Preço cheio (sem desconto).
RETENCAO_ATIVO = os.getenv("RETENCAO_ATIVO", "1") not in ("0", "false", "False", "no")
# --- Trilhos de segurança do e-mail marketing (mkt.py) ---
# Teto de e-mails de MARKETING nas últimas 24h. Deixa folga pro transacional dentro
# do cap do provedor (Resend free = 100/dia). O excedente espera a próxima rodada.
EMAIL_MKT_CAP_24H = int(os.getenv("EMAIL_MKT_CAP_24H", "80"))
# Teto por rodada (a thread roda de hora em hora) — espalha o envio ao longo do dia.
EMAIL_MKT_CAP_CICLO = int(os.getenv("EMAIL_MKT_CAP_CICLO", "25"))
# Frequência mínima por pessoa: não manda 2 e-mails de marketing em menos de X horas.
EMAIL_MKT_FREQ_H = int(os.getenv("EMAIL_MKT_FREQ_H", "48"))
# Pausa entre um envio e o próximo (segundos) — respeita o rate limit (~2/s).
EMAIL_MKT_PAUSA_S = float(os.getenv("EMAIL_MKT_PAUSA_S", "0.6"))

# Segredo do /api/ingest: só o robô (que sabe o token) pode publicar surebets.
# VAZIO = endpoint aberto (compatível com o robô atual). Setar p/ exigir o token.
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "").strip()
# Remetente. Precisa ser de um domínio verificado no Resend (ex.: sureradar.site).
# Enquanto não verificar o domínio, use "SureRadar <onboarding@resend.dev>" (teste).
EMAIL_FROM = os.getenv("EMAIL_FROM", "SureRadar <nao-responda@sureradar.site>").strip()

# Fluxo de marketing no grupo. Desligue com PROMO_ATIVO=0.
PROMO_ATIVO = os.getenv("PROMO_ATIVO", "1") not in ("0", "false", "False", "no")
# Intervalo entre as entradas postadas no grupo/canal — sorteado a cada envio
# entre MIN e MAX minutos (fica natural, não robótico).
TELEGRAM_POST_MIN_MIN = int(os.getenv("TELEGRAM_POST_MIN_MIN", "80"))
TELEGRAM_POST_MAX_MIN = int(os.getenv("TELEGRAM_POST_MAX_MIN", "100"))

# Teto de lucro "são": surebets acima disso são ANOMALIAS (ex.: escanteios com
# odd bugada dando 30-400%) e são descartadas na ingestão. Real vai até ~25%.
MAX_LUCRO_SANO = float(os.getenv("MAX_LUCRO_SANO", "25"))

# ---------------------------------------------------------------------------
# FACEBOOK / META — Marketing API (gasto dos anúncios por campanha/conjunto)
# ---------------------------------------------------------------------------
# Cruza com o contador de membros pra mostrar o CUSTO POR MEMBRO no /admin.
#   META_ACCESS_TOKEN  -> token com permissão ads_read (gere no Meta Business).
#   META_AD_ACCOUNT_ID -> ID da conta de anúncios (só os números OU "act_123...").
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "").strip()
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "").strip()
META_API_VER = os.getenv("META_API_VER", "v21.0").strip()
