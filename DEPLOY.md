# Colocar o SureRadar no ar (domínio real)

Guia rápido para publicar em produção com HTTPS.

## Visão geral

- **App:** FastAPI (Python) — precisa de um host que rode Python (não serve Vercel/Netlify puro).
- **Recomendado:** [Render.com](https://render.com) — grátis pra começar, HTTPS automático, deploy via GitHub.
- **Alternativas:** Railway, Fly.io, ou um VPS (DigitalOcean/Hetzner) com nginx.

## Passo a passo (Railway) — escolhido

> O repositório Git já está iniciado e com o 1º commit feito.

### 1. Criar o repositório no GitHub e enviar
1. Crie um repo vazio em github.com (ex.: `sureradar`), **privado**.
2. Na pasta do projeto:
```bash
git remote add origin https://github.com/SEU_USUARIO/sureradar.git
git branch -M main
git push -u origin main
```

### 2. Deploy no Railway
1. Entre em [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → escolha `sureradar`.
2. O Railway detecta Python (via `requirements.txt`) e usa o `Procfile` pra subir. Ele injeta a porta em `$PORT` sozinho.
3. Aba **Variables** → adicione os segredos (valores do seu `.env`):
   - `ODDS_API_KEY`, `SUREBET_API_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - **NÃO** adicione `ALLOW_DEV_PRO` (fica desligado em produção, por segurança).
4. Aba **Settings → Networking → Generate Domain** → sai um `https://sureradar.up.railway.app` (HTTPS grátis) pra testar.

### 3. Ligar o seu domínio (registrado na Hostinger)
1. No Railway: **Settings → Networking → Custom Domain** → digite seu domínio → ele mostra um alvo **CNAME**.
2. No painel da **Hostinger → DNS/Nameservers** do seu domínio: crie um registro **CNAME**
   (`www` → o alvo do Railway) e, para o raiz, use o redirect/ALIAS que o Railway indicar.
3. HTTPS é emitido automático em alguns minutos.

### 4. Ajustes pós-deploy
- **Extensão:** troque `SAAS_URL` em `extension/background.js` para `https://SEUDOMINIO/api/ingest`.
- **SEO:** os `canonical`/`og:url` usam `sureradar.com.br` — me diga o domínio real que eu ajusto.

---

## Alternativa (Render — 100% grátis)

### 1. Subir o código pro GitHub
```bash
cd "Pictures/surebet"
git init
git add .
git commit -m "SureRadar MVP"
# crie um repositório no github.com e:
git remote add origin https://github.com/SEU_USUARIO/sureradar.git
git branch -M main
git push -u origin main
```
> O `.gitignore` já protege `.env` e `sureradar.db` (não vão pro GitHub).

### 2. Criar o serviço no Render
1. Entre em render.com → **New +** → **Web Service** → conecte o GitHub e escolha o repo.
2. Render detecta o `render.yaml` (build + start já configurados).
3. Em **Environment**, adicione as variáveis (valores do seu `.env`):
   - `ODDS_API_KEY`, `SUREBET_API_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - (não coloque `ALLOW_DEV_PRO` — ele fica desligado em produção)
4. **Create Web Service.** Em ~2 min sobe em `https://sureradar.onrender.com` (HTTPS grátis).

### 3. Apontar o domínio
1. No Render: **Settings → Custom Domains → Add** → digite `sureradar.com.br`.
2. No seu registrador (Registro.br / Namecheap): crie os registros DNS que o Render mostrar
   (geralmente um `CNAME` para `www` e um `A`/`ALIAS` para o raiz).
3. HTTPS é emitido automático (Let's Encrypt).

### 4. Ajustes pós-deploy
- **Extensão:** troque `SAAS_URL` em `extension/background.js` para `https://SEUDOMINIO/api/ingest`.
- **URLs de SEO:** os `canonical`/`og:url` já usam `sureradar.com.br` — ajuste se o domínio for outro.
- **Google/Supabase:** agora sim, use a URL real nos redirects do OAuth.

## ⚠️ Sobre o banco de dados (importante)

Hoje os usuários ficam num **SQLite** (`sureradar.db`). No plano grátis do Render o disco é
**efêmero** — a cada novo deploy os usuários são apagados. Para dados duráveis:
- **Melhor caminho:** usar o **Postgres do Supabase** (que já vamos conectar) como banco.
- Ou: ativar um **disco persistente** no Render (plano pago).

Enquanto está em fase de testes/pré-lançamento, o SQLite serve. Antes de divulgar de verdade,
migramos os usuários para o Supabase.
