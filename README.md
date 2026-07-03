# Detector de Surebets (BR + Internacional)

Detecta oportunidades de arbitragem esportiva comparando odds de várias casas.
Roda em **modo demo** (sem chave) ou com **dados reais** via [The Odds API](https://the-odds-api.com/).

## O que é surebet?

Apostar em **todos os resultados** de um jogo, em casas diferentes, quando as odds
combinadas garantem lucro dê no que der. O detector faz a conta:

```
margem = 1/odd_1 + 1/odd_2 + 1/odd_3
margem < 1  ->  tem lucro garantido = (1/margem - 1) * 100 %
```

## Instalação

```bash
pip install -r requirements.txt
```

## Rodar em modo demo (sem chave, grátis)

```bash
python main.py
```

Usa dois jogos de exemplo — um deles tem um surebet de ~7% pra você ver a saída.

## Rodar com dados reais

1. Crie conta grátis em https://the-odds-api.com/ (free tier: 500 requisições/mês).
2. Copie `.env.example` para `.env` e cole sua chave em `ODDS_API_KEY`.
3. `python main.py`

## Onde mexer (config.py)

| O quê | Variável | Detalhe |
|-------|----------|---------|
| Casas consideradas | `CASAS_SHARP`, `CASAS_BR` | As "boas" que você pediu |
| Lucro mínimo | `LUCRO_MINIMO_PCT` | Padrão 1% |
| Banca | `BANCA` | Base pro cálculo das stakes |
| Esportes/ligas | `ESPORTES` | Keys da The Odds API |

## Casas: entenda o filtro

- **sharp** (Pinnacle, Betfair Exchange, Matchbook, SBOBET): toleram arbitragem,
  odds de referência. É o seu lado internacional forte.
- **BR** (Betano, Bet365, Superbet, KTO, Novibet...): ótimas odds de varejo, MAS
  costumam **limitar/fechar** contas de arbitradores. Use com moderação e disfarce
  (stakes "redondas", não só jogos de arbitragem).

## Limitações honestas (leia antes de apostar dinheiro)

- **Odds mudam em segundos** — este script tira uma "foto"; na hora de apostar a
  odd pode já ter sumido (isso se chama *odds movement / slippage*).
- **Limites de stake** — casas limitam quanto você pode apostar em mercados frágeis.
- **Só cobre o mercado h2h** (1x2). Over/Under, handicap etc. dão pra adicionar.
- Não executa apostas — só **detecta e calcula**. A entrada é manual (proposital).

## SaaS (dashboard web)

Além do CLI, há um painel web onde o usuário escolhe filtros e vê as surebets.

```bash
python app.py
```
Abrir http://127.0.0.1:8000

- Filtros (lucro mínimo, ligas, mercados, tipo de casa) salvos no navegador.
- `pipeline.py` busca odds reais, calcula arbitragem e alimenta o painel.
- O **agendador** (loop de fundo) repete a coleta a cada `POLL_INTERVAL_SEG`.

### ⚠️ Créditos da API (leia isto)

Free tier = **500 créditos/mês**. Cada rodada custa `nº de ligas × nº de regiões`.
Ex.: 3 ligas × 2 regiões (eu,uk) = **6 créditos por rodada**.

A 1 rodada/30min isso são ~288 créditos/dia — **o free tier acaba em ~2 dias.**
Para um SaaS ao vivo você precisa de um plano pago. Enquanto isso:

- Ajuste `POLL_INTERVAL_SEG` (maior = menos gasto) em `config.py`.
- Ou desligue o loop com `AGENDADOR_ATIVO = False` e rode coletas manuais:
  `python pipeline.py`.
- O guarda `MIN_CREDITOS_PARAR` pausa o agendador antes de zerar sua conta.

## Alertas no Telegram

Envia cada surebet nova para um grupo do Telegram.

1. No Telegram, fale com **@BotFather** → `/newbot` → escolha nome e @usuario →
   copie o **token**.
2. No `.env`, adicione: `TELEGRAM_BOT_TOKEN=seu_token`
3. Adicione o bot ao seu **grupo** e mande qualquer mensagem lá.
4. Rode `python telegram_setup.py` e copie o ID que aparecer.
5. No `.env`, adicione: `TELEGRAM_CHAT_ID=o_id` (grupos costumam ser negativos).
6. Suba o servidor (`python app.py`). Toda surebet nova cai no grupo.

Só as surebets **novas** são enviadas (sem repetir a cada rodada).

## Mercados suportados

- **h2h** — Resultado (1X2 / Moneyline)
- **spreads** — Handicap (só linhas .5, sem risco de push)
- **totals** — Over/Under (só linhas .5)

Configurável em `config.MERCADOS`. Mais mercados = mais custo de API.

## Próximos passos possíveis

- [ ] Alerta no Telegram/Discord quando surgir surebet
- [ ] Suportar mais mercados (totais, handicap asiático) — mais oportunidades
- [ ] Guardar histórico de surebets num CSV/banco
- [ ] Login + planos (SaaS de verdade)
- [ ] Calculadora de stake para odds "lay" (Betfair Exchange)
