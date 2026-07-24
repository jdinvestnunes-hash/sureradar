"""
app.py — servidor web do SaaS de surebets (FastAPI).

Roda com:
    uvicorn app:app --reload
ou simplesmente:
    python app.py

Endpoints:
    GET /                -> o dashboard (static/index.html)
    GET /api/meta        -> opções de filtro (esportes, mercados, tipos de casa)
    GET /api/surebets    -> surebets já filtradas conforme a query do usuário
"""

import hashlib
import hmac
import json
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import requests

# Mostra os logs do agendador em tempo real (evita buffer em background).
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

import re
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Body, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import threading

import unicodedata

import alertas
import auth
import config
import emailer
import feed
import lifecycle
import meta_ads
import notifier
import pipeline
import promo
import recuperacao
import tg_tracker
import valor_feed

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

# Plano de conteúdo de SEO (blog). Cada linha: slug, título, arquivo, data prevista,
# já_publicado. As páginas com arquivo existente e publicado=0 são RASCUNHOS: aparecem
# no calendário do /admin e vão ao ar quando você clica "Publicar". As sem arquivo ainda
# são "a criar". Publicar entra no sitemap automaticamente.
PLANO_SEO = [
    ("calculadora", "Calculadora de Surebet", "calculadora.html", "2026-07-07", 1),
    ("o-que-e-surebet", "O que é surebet?", "o-que-e-surebet.html", "2026-07-07", 1),
    ("arbitragem-esportiva", "Arbitragem esportiva: guia completo", "arbitragem-esportiva.html", "2026-07-07", 1),
    ("aposta-segura", "Aposta segura existe?", "aposta-segura.html", "2026-07-07", 1),
    ("como-fazer-surebet", "Como fazer surebet (passo a passo)", "como-fazer-surebet.html", "2026-07-14", 0),
    ("melhores-casas-de-aposta", "Melhores casas de aposta para surebet", "melhores-casas-de-aposta.html", "2026-07-16", 0),
    ("software-surebet", "Software de surebet: vale a pena?", "software-surebet.html", "2026-07-18", 0),
    ("surebet-ao-vivo", "Surebet ao vivo (live)", "surebet-ao-vivo.html", "2026-07-21", 0),
    ("surebet-bet365", "Surebet na Bet365", "surebet-bet365.html", "2026-07-23", 0),
    ("renda-extra-apostas", "Renda extra com apostas", "renda-extra-apostas.html", "2026-07-25", 0),
    ("surebet-brasil", "Surebet no Brasil", "surebet-brasil.html", "2026-07-28", 0),
    ("apostas-seguras", "Apostas seguras: como escolher", "apostas-seguras.html", "2026-07-30", 0),
    ("grupo-de-surebet", "Grupo de surebet no Telegram", "grupo-de-surebet.html", "2026-08-01", 0),
]


@asynccontextmanager
async def lifespan(app):
    """Ao subir o servidor, prepara o banco de usuários e liga o agendador.
    Se o banco falhar, o site NÃO cai (só a parte de login fica indisponível)."""
    try:
        auth.init()
        print(f">> Banco pronto ({'Postgres' if auth.PG else 'SQLite'}).")
    except Exception as e:
        print(f"!! FALHA ao conectar no banco: {e}\n"
              "   O site sobe, mas login/cadastro ficam indisponíveis até corrigir o DATABASE_URL.")
    try:
        for slug, titulo, arq, data, pub in PLANO_SEO:
            auth.seed_pagina(slug, titulo, arq, data, pub)
    except Exception as e:
        print(f"!! seed do plano de SEO falhou: {e}")
    # Restaura o feed salvo no banco (o feed em memória zera a cada redeploy).
    try:
        _carregar_catalogo()           # casas/esportes acumulados (sempre todas)
        cache = auth.feed_cache_get()
        if cache:
            feed.merge_surebets(cache, quando=pipeline._agora_iso() + " (cache)")
            print(f">> Feed restaurado do cache: {len(cache)} surebets.")
        _recalcular_filtros()          # filtro = catálogo inteiro (mesmo sem cache)
    except Exception as e:
        print(f"!! Falha ao restaurar feed do cache: {e}")

    pipeline.iniciar_agendador()
    try:
        promo.iniciar()               # fluxo de marketing no grupo do Telegram
    except Exception as e:
        print(f"!! Promo Telegram não iniciou: {e}")
    try:
        lifecycle.iniciar()           # fluxo de nutrição por e-mail (nudges pró)
    except Exception as e:
        print(f"!! Lifecycle de e-mail não iniciou: {e}")
    try:
        recuperacao.iniciar()         # régua de recuperação (gerou checkout e não pagou)
    except Exception as e:
        print(f"!! Recuperação de e-mail não iniciou: {e}")
    try:
        tg_tracker.iniciar()          # conta membros por link de campanha (Telegram)
    except Exception as e:
        print(f"!! Telegram tracker não iniciou: {e}")
    try:
        alertas.iniciar()             # alertas personalizados de surebet na DM (PRO)
    except Exception as e:
        print(f"!! Alertas Telegram não iniciaram: {e}")
    try:                              # cria/cacheia produtos p/ o cartão parcelado ser rápido
        import threading as _th        # em thread p/ NÃO travar o boot (healthcheck do Railway)
        _th.Thread(target=_abacate_prewarm_produtos, name="abacate-prewarm", daemon=True).start()
    except Exception as e:
        print(f"!! Prewarm produtos AbacatePay: {e}")
    yield
    pipeline.parar_agendador()
    promo.parar()
    lifecycle.parar()
    recuperacao.parar()
    tg_tracker.parar()
    alertas.parar()


app = FastAPI(title="Surebet SaaS", version="0.1.0", lifespan=lifespan)

# Libera o navegador (extensão) a enviar surebets raspadas para /api/ingest.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def _private_network(request, call_next):
    """Acrescenta o header de Private Network Access do Chrome + headers de
    segurança (anti-clickjacking, anti-sniff, referrer)."""
    resp = await call_next(request)
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["X-Frame-Options"] = "DENY"                # anti-clickjacking
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp

# Filtros descobertos via ingestão (raspagem da conta). Espelham a surebet.com.
INGESTED_BOOKS = []
INGESTED_SPORTS = []
INGESTED_PROFIT = {}
# Catálogo ACUMULADO de casas/esportes já vistos (só cresce, nunca encolhe). O
# filtro sempre mostra TODAS as casas já raspadas — mesmo que a atualização do
# momento traga só algumas. Assim o usuário mantém as 26 casas fixas no controle
# dele (marca/desmarca à vontade) e nunca perde uma casa por causa da raspagem.
CASAS_CAT = {}     # bookmaker_key -> label
SPORTS_CAT = {}    # sport_key -> label


def _carregar_catalogo():
    """Carrega o catálogo acumulado do banco (no startup, antes do 1º ingest)."""
    try:
        cat = auth.catalogo_get()
        CASAS_CAT.update(cat.get("casas", {}))
        SPORTS_CAT.update(cat.get("esportes", {}))
    except Exception as e:
        print("!! catalogo nao carregou:", e)


def _recalcular_filtros(todos=None):
    """Atualiza as opções de filtro (casas/esportes/lucro).

    As casas/esportes ESPELHAM o catálogo ACUMULADO (todas já vistas), não só o
    feed do momento — senão uma raspagem parcial faz casas sumirem do filtro. A
    faixa de lucro reflete o feed atual."""
    global INGESTED_BOOKS, INGESTED_SPORTS, INGESTED_PROFIT
    if todos is None:
        todos = feed.get_surebets()
    # acumula o que veio agora no catálogo (nunca remove)
    mudou = False
    for c in todos:
        if c["sport"] not in SPORTS_CAT:
            SPORTS_CAT[c["sport"]] = SPORT_LABELS_PT.get(c["sport"], c["sport"]); mudou = True
        for l in c["legs"]:
            if l["bookmaker"] not in CASAS_CAT:
                CASAS_CAT[l["bookmaker"]] = l["bookmaker_label"]; mudou = True
    # opções do filtro = catálogo inteiro (todas as casas/esportes já vistos)
    INGESTED_BOOKS = [{"key": k, "label": v} for k, v in
                      sorted(CASAS_CAT.items(), key=lambda x: x[1].lower())]
    INGESTED_SPORTS = [{"key": k, "label": v} for k, v in
                       sorted(SPORTS_CAT.items(), key=lambda x: (x[0] != "Football", x[1]))]
    if todos:
        vals = [c["profit_pct"] for c in todos]
        INGESTED_PROFIT = {"min": 0, "max": round(max(vals) + 0.5, 1)}
    if mudou:                                  # persiste o catálogo que cresceu
        try:
            auth.catalogo_set({"casas": CASAS_CAT, "esportes": SPORTS_CAT})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AUTENTICAÇÃO (login / cadastro / sessão por cookie)
# ---------------------------------------------------------------------------
COOKIE = "sr_session"


def _usuario(request: Request):
    """Usuário logado (ou None), a partir do cookie de sessão."""
    return auth.usuario_da_sessao(request.cookies.get(COOKIE))


def _plano_efetivo(user):
    """Plano do usuário — o BANCO é a fonte da verdade. 'free' se deslogado.

    (Para virar PRO usa-se o painel /admin, que define a duração e SOMA nos dias
    restantes ao renovar. Pro vencido volta pra free sozinho — auth._normalizar_plano.)"""
    return user["plano"] if user else "free"


ADMIN_COOKIE = "sr_admin"
_admin_tokens = set()   # tokens já desbloqueados com a senha (memória; zera no redeploy)


def _admin_email(user):
    """E-mail do usuário está na lista de admins (config.ADMIN_EMAILS)."""
    return bool(user) and (user.get("email", "").strip().lower() in config.ADMIN_EMAILS)


def _admin_desbloqueado(request: Request):
    tok = request.cookies.get(ADMIN_COOKIE)
    return bool(tok) and tok in _admin_tokens


def _admin_ok(request: Request, user):
    """Admin liberado = e-mail admin E senha (ADMIN_PASSWORD) já validada nesta
    sessão. Sem ADMIN_PASSWORD definida, o painel fica BLOQUEADO."""
    return _admin_email(user) and bool(config.ADMIN_PASSWORD) and _admin_desbloqueado(request)


def _client_ip(request: Request):
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


# --- Rate limit simples em memória (anti-abuso: cadastro, reset, reenvio) ---
import time as _time
_rate_hits = {}     # chave -> lista de timestamps


def _rate_ok(chave, maximo, janela_seg):
    """True se ainda pode; registra a tentativa. Janela deslizante em memória."""
    agora = _time.time()
    xs = [t for t in _rate_hits.get(chave, []) if agora - t < janela_seg]
    if len(xs) >= maximo:
        _rate_hits[chave] = xs
        return False
    xs.append(agora)
    _rate_hits[chave] = xs
    return True


def _ip_admin_ok(request: Request):
    """True se NÃO há allowlist de IP, ou se o IP do cliente está nela."""
    return (not config.ADMIN_IPS) or (_client_ip(request) in config.ADMIN_IPS)


def _com_sessao(resp: Response, user_id: int):
    token = auth.criar_sessao(user_id)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSAO_MAX_S, path="/")
    return resp


@app.post("/api/register")
def register(background_tasks: BackgroundTasks, request: Request, payload: dict = Body(...)):
    # anti-abuso: no máx 5 contas por IP a cada hora
    if not _rate_ok("reg:" + _client_ip(request), 5, 3600):
        return JSONResponse({"erro": "Muitas contas criadas desse acesso. Tente mais tarde."},
                            status_code=429)
    user, erro = auth.criar_usuario(
        payload.get("nome", ""), payload.get("email", ""), payload.get("senha", ""),
        payload.get("whatsapp", ""), request.cookies.get("sr_origem", ""),
        request.cookies.get("sr_camp", ""))
    if erro:
        return JSONResponse({"erro": erro}, status_code=400)
    # NÃO loga: manda o e-mail de confirmação; a conta só libera após confirmar.
    token = auth.criar_token_confirmacao(user["id"])
    link = config.SITE_URL + "/confirmar?token=" + token
    background_tasks.add_task(emailer.enviar_confirmacao, user["email"], user["nome"], link)
    return {"ok": True, "precisa_confirmar": True, "email": user["email"]}


@app.post("/api/login")
def login(request: Request, payload: dict = Body(...)):
    # anti brute-force: 10 tentativas por IP a cada 10 min
    if not _rate_ok("login:" + _client_ip(request), 10, 600):
        return JSONResponse({"erro": "Muitas tentativas. Espere alguns minutos."}, status_code=429)
    user = auth.autenticar(payload.get("email", ""), payload.get("senha", ""))
    if not user:
        return JSONResponse({"erro": "E-mail ou senha incorretos."}, status_code=401)
    if not user.get("verificado", True):
        return JSONResponse({"erro": "Confirme seu e-mail antes de entrar. Veja sua caixa de entrada.",
                             "nao_verificado": True, "email": user["email"]}, status_code=403)
    resp = JSONResponse({"ok": True, "user": user})
    return _com_sessao(resp, user["id"])


@app.get("/confirmar")
def confirmar(background_tasks: BackgroundTasks, token: str = ""):
    """Link do e-mail: confirma a conta, manda boas-vindas e já entra logado."""
    u = auth.confirmar_email(token)
    if not u:
        return RedirectResponse("/login?erro=confirmar", status_code=302)
    background_tasks.add_task(emailer.enviar_boas_vindas, u["email"], u["nome"])
    resp = RedirectResponse("/app?confirmado=1", status_code=302)
    return _com_sessao(resp, u["id"])


@app.post("/api/reenviar-confirmacao")
def reenviar_confirmacao(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """Reenvia o e-mail de confirmação (se a conta existir e não estiver confirmada)."""
    email = (payload.get("email") or "").strip().lower()
    if "@" in email and _rate_ok("reenvio:" + email, 3, 900):   # máx 3 a cada 15 min
        uid, nome = auth.user_nao_verificado(email)
        if uid:
            token = auth.criar_token_confirmacao(uid)
            link = config.SITE_URL + "/confirmar?token=" + token
            background_tasks.add_task(emailer.enviar_confirmacao, email, nome, link)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    auth.encerrar_sessao(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.post("/api/senha/esqueci")
def senha_esqueci(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """Pede redefinição de senha: manda e-mail com link (se o e-mail existir).
    Resposta é SEMPRE ok — não revela se o e-mail tem conta (segurança).
    Rate limit: máx 3 pedidos por e-mail a cada 15 min (anti-abuso/spam)."""
    email = (payload.get("email") or "").strip().lower()
    if "@" in email and _rate_ok("reset:" + email, 3, 900):
        token, nome = auth.criar_token_reset(email)
        if token:
            link = config.SITE_URL + "/redefinir?token=" + token
            background_tasks.add_task(emailer.enviar_reset_senha, email, nome, link)
    return {"ok": True}


@app.api_route("/descadastrar", methods=["GET", "POST"])
def descadastrar(u: str = ""):
    """Opt-out dos e-mails de marketing (link do e-mail + 1-clique do Gmail)."""
    email = auth.descadastrar(u)
    msg = (f"Pronto! <b>{email}</b> não vai mais receber e-mails de marketing do SureRadar."
           if email else "Link inválido ou você já havia se descadastrado.")
    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1"><title>Descadastro — SureRadar</title>
      <style>body{{background:#05070d;color:#f2f6fc;font-family:Inter,Arial,sans-serif;display:flex;
      align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;text-align:center}}
      .c{{max-width:440px;background:#0e1421;border:1px solid #1b2740;border-radius:18px;padding:36px}}
      .b{{font-family:Sora,Inter,sans-serif;font-weight:800;font-size:22px;margin-bottom:14px}}
      .g{{color:#2ee6a8}} p{{color:#a3b1c9;line-height:1.6}} a{{color:#38d4f5}}</style></head>
      <body><div class="c"><div class="b">Sure<span class="g">Radar</span></div>
      <p>{msg}</p><p style="margin-top:16px"><a href="/">Voltar ao site</a></p></div></body></html>"""
    return HTMLResponse(html)


@app.post("/api/senha/redefinir")
def senha_redefinir(payload: dict = Body(...)):
    """Troca a senha usando o token do e-mail."""
    ok, erro = auth.redefinir_senha(payload.get("token", ""), payload.get("senha", ""))
    if not ok:
        return JSONResponse({"erro": erro}, status_code=400)
    return {"ok": True}


# --- Login com Google (OAuth) ---
def _base_url(request: Request) -> str:
    """URL pública correta mesmo atrás do proxy do Railway (https + host real)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    return f"{proto}://{host}"


@app.get("/auth/google")
def google_login(request: Request):
    import secrets as _s
    from urllib.parse import urlencode
    if not config.GOOGLE_CLIENT_ID:
        return RedirectResponse("/login?erro=google_off", status_code=302)
    state = _s.token_urlsafe(16)
    params = urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": _base_url(request) + "/auth/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    resp = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + params, status_code=302)
    resp.set_cookie("g_state", state, max_age=600, httponly=True, samesite="lax", path="/")
    return resp


@app.get("/auth/callback")
def google_callback(request: Request, background_tasks: BackgroundTasks, code: str = "", state: str = ""):
    import requests
    if not code or not state or state != request.cookies.get("g_state"):
        return RedirectResponse("/login?erro=google", status_code=302)
    redirect_uri = _base_url(request) + "/auth/callback"
    try:
        tok = requests.post("https://oauth2.googleapis.com/token", timeout=15, data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).json()
        at = tok.get("access_token")
        if not at:
            return RedirectResponse("/login?erro=google", status_code=302)
        info = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=15,
                            headers={"Authorization": f"Bearer {at}"}).json()
    except requests.RequestException:
        return RedirectResponse("/login?erro=google", status_code=302)
    email = info.get("email")
    if not email:
        return RedirectResponse("/login?erro=google", status_code=302)
    user, novo = auth.pegar_ou_criar_google(email, info.get("name", ""),
                                            request.cookies.get("sr_origem", ""),
                                            request.cookies.get("sr_camp", ""))
    if novo:
        background_tasks.add_task(emailer.enviar_boas_vindas, user["email"], user["nome"])
    resp = RedirectResponse("/app", status_code=302)
    resp.delete_cookie("g_state", path="/")
    return _com_sessao(resp, user["id"])


@app.get("/api/health")
def health(fb: int = 0, venda: int = 0):
    """Diagnóstico: qual banco está em uso e se a conexão funciona (sem expor segredos)."""
    info = {"db_type": "postgres" if auth.PG else "sqlite", "db_ok": False}
    try:
        with auth._db() as c:
            c.execute("SELECT 1")
        info["db_ok"] = True
    except Exception as e:
        info["erro"] = type(e).__name__ + ": " + str(e)[:120]
    # Diagnóstico de config (só True/False, nunca o valor do segredo).
    sk = config.STRIPE_SECRET_KEY
    abk = config.ABACATEPAY_API_KEY
    info["pagamentos"] = {
        "stripe_key": bool(sk),
        "stripe_webhook": bool(config.STRIPE_WEBHOOK_SECRET),
        "stripe_mode": ("live" if sk.startswith("sk_live_") else
                        "test" if sk.startswith("sk_test_") else "?"),
        "abacatepay_key": bool(abk),
        "abacatepay_webhook": bool(config.ABACATEPAY_WEBHOOK_SECRET),
        "abacatepay_mode": ("prod" if abk.startswith("abc_prod_") else
                            "dev" if abk.startswith("abc_dev_") else "?"),
    }
    info["email"] = {
        "resend_key": bool(config.RESEND_API_KEY),
        "from": config.EMAIL_FROM,
    }
    info["telegram"] = {
        "bot_token": bool(config.TELEGRAM_BOT_TOKEN),
        "chat_id": bool(config.TELEGRAM_CHAT_ID),
        "admin_chat": bool(config.ADMIN_TELEGRAM_CHAT_ID),
        "promo_ativo": config.PROMO_ATIVO,
    }
    # Teste do aviso de venda: /api/health?venda=1 manda uma mensagem de teste pro admin.
    if venda:
        ok = notifier.enviar_admin("🧪 <b>Teste de aviso de venda</b>\n\n"
                                   "Se você recebeu isso, os avisos de venda estão "
                                   "funcionando! ✅ Toda venda vai cair aqui.")
        info["telegram"]["teste_venda_enviado"] = bool(ok)
    # Facebook Ads: só faz a chamada real à API do Meta com ?fb=1 (não expõe o gasto).
    info["facebook"] = {"configurado": meta_ads.configurado()}
    if fb and meta_ads.configurado():
        t = meta_ads.testar()
        info["facebook"]["token_ok"] = bool(t.get("ok"))
        if t.get("ok"):
            info["facebook"]["conjuntos"] = t.get("conjuntos")
        else:
            info["facebook"]["erro"] = t.get("erro", "")
    return info


# ===========================================================================
# PAGAMENTOS — Stripe (cartão) e AbacatePay (Pix)
# ===========================================================================
def _plano_valido(payload):
    """Plano do payload. 'valor' = add-on das Odds Erradas comprado AVULSO (à parte
    do plano) — tem nome/dias/valor próprios, então cai no mesmo fluxo de checkout."""
    plano = (payload or {}).get("plano", "mensal")
    if plano == "valor":
        return plano, dict(config.ADDON_VALOR)
    return plano, config.PLANOS.get(plano)


def _quer_bump(payload, plano):
    """Marcou a caixinha do order bump (Odds Erradas +R$47) junto do plano?
    Só faz sentido junto de um PLANO — comprando o add-on avulso já é o produto."""
    return bool((payload or {}).get("addon")) and plano != "valor"


def _confirmar_compra_email(user_id):
    """Manda a confirmação de compra UMA vez (dedup), em background."""
    try:
        if auth.registrar_email(user_id, "compra"):
            u = auth.pegar_por_id(user_id)
            if u:
                threading.Thread(target=emailer.enviar_compra,
                                 args=(u["email"], u["nome"]), daemon=True).start()
    except Exception as e:
        print("!! email de compra:", e)


def _avisar_venda_admin(res):
    """Avisa VOCÊ no Telegram (chat privado) sempre que cai uma venda."""
    try:
        u = auth.pegar_por_id(res.get("user_id")) or {}
        valor = float(res.get("valor", 0) or 0)
        msg = (
            "💰 <b>NOVA VENDA!</b> 🎉\n\n"
            f"👤 {notifier._esc(u.get('nome', '?'))}\n"
            f"✉️ {notifier._esc(u.get('email', '?'))}\n"
            f"📦 Plano: <b>{notifier._esc(str(res.get('plano', '?')))}</b>\n"
            f"💵 Valor: <b>R$ {valor:.2f}</b>\n"
            f"💳 {notifier._esc(str(res.get('metodo', '?')))}\n"
            f"📍 Origem: {notifier._esc(str(u.get('origem') or 'direto'))}"
        )
        notifier.enviar_admin(msg)
    except Exception as e:
        print("!! aviso de venda (admin):", e)


@app.post("/api/checkout/stripe")
def checkout_stripe(request: Request, payload: dict = Body(...)):
    """Cria uma sessão de checkout do Stripe (cartão) e devolve a URL."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    plano, p = _plano_valido(payload)
    if not p:
        return JSONResponse({"erro": "plano inválido"}, status_code=400)
    if not config.STRIPE_SECRET_KEY:
        return JSONResponse({"erro": "Stripe não configurado"}, status_code=503)
    # PAGAMENTO ÚNICO com parcelamento no cartão (Stripe Brasil). Mensal = à vista;
    # Trimestral/Semestral/Anual PODEM PARCELAR (a Stripe mostra as parcelas conforme
    # o valor e o cartão, até 12x). O acesso dura p["dias"] e expira (a régua de
    # e-mail chama pra recomprar). Pix segue pagamento único à parte.
    parcelar = plano != "mensal"
    data = {
        "mode": "payment",
        "success_url": config.SITE_URL + "/perfil?pago=1",
        "cancel_url": config.SITE_URL + "/planos",
        "customer_email": user["email"],
        "client_reference_id": str(user["id"]),
        "payment_method_types[0]": "card",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "brl",
        "line_items[0][price_data][unit_amount]": str(int(round(p["valor"] * 100))),
        "line_items[0][price_data][product_data][name]": "SureRadar " + p["nome"],
        "metadata[user_id]": str(user["id"]),
        "metadata[plano]": plano,
        "payment_intent_data[metadata][user_id]": str(user["id"]),
        "payment_intent_data[metadata][plano]": plano,
    }
    if parcelar:
        # parcelamento: só aparece p/ cartão BR e precisa estar ATIVADO no painel Stripe
        # (Configurações → Métodos de pagamento → Cartões → Parcelamento).
        data["payment_method_options[card][installments][enabled]"] = "true"
    try:
        r = requests.post("https://api.stripe.com/v1/checkout/sessions", data=data,
                          auth=(config.STRIPE_SECRET_KEY, ""), timeout=20)
    except requests.RequestException as e:
        return JSONResponse({"erro": "falha de rede", "detalhe": str(e)[:120]}, status_code=502)
    if not r.ok:
        return JSONResponse({"erro": "Stripe recusou", "detalhe": r.text[:200]}, status_code=502)
    sess = r.json()
    auth.checkout_registrar("stripe", sess["id"], user["id"], plano, p["dias"], p["valor"], "stripe")
    return {"url": sess["url"]}


def _verifica_assinatura_stripe(body: bytes, sig_header: str, secret: str) -> bool:
    if not secret or not sig_header:
        return False
    try:
        campos = dict(kv.split("=", 1) for kv in sig_header.split(",") if "=" in kv)
        t, v1 = campos.get("t"), campos.get("v1")
        assinado = f"{t}.".encode() + body
        esperado = hmac.new(secret.encode(), assinado, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, v1 or "")
    except Exception:
        return False


@app.post("/api/assinatura/portal")
def assinatura_portal(request: Request):
    """Abre o portal do Stripe p/ a pessoa gerenciar/cancelar a assinatura."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    a = auth.assinatura_do_user(user["id"])
    if not a or not a.get("customer_id"):
        return JSONResponse({"erro": "sem assinatura ativa"}, status_code=400)
    try:
        r = requests.post("https://api.stripe.com/v1/billing_portal/sessions",
                          data={"customer": a["customer_id"],
                                "return_url": config.SITE_URL + "/perfil"},
                          auth=(config.STRIPE_SECRET_KEY, ""), timeout=20)
    except requests.RequestException as e:
        return JSONResponse({"erro": "falha de rede", "detalhe": str(e)[:120]}, status_code=502)
    if not r.ok:
        return JSONResponse({"erro": "Stripe recusou", "detalhe": r.text[:200]}, status_code=502)
    return {"url": r.json().get("url")}


@app.post("/api/webhook/stripe")
async def webhook_stripe(request: Request):
    body = await request.body()
    if not _verifica_assinatura_stripe(body, request.headers.get("stripe-signature", ""),
                                       config.STRIPE_WEBHOOK_SECRET):
        return JSONResponse({"erro": "assinatura inválida"}, status_code=400)
    try:
        ev = json.loads(body)
    except Exception:
        return JSONResponse({"erro": "payload inválido"}, status_code=400)
    tipo = ev.get("type")
    obj = ev.get("data", {}).get("object", {})
    if tipo == "checkout.session.completed":
        # 1ª cobrança (assinatura recém-criada OU pagamento único de fallback).
        if obj.get("id"):
            res = auth.checkout_pagar("stripe", obj["id"], obj.get("payment_intent"))
            if res:
                _confirmar_compra_email(res["user_id"])
                _avisar_venda_admin(res)
        sub_id = obj.get("subscription")
        if sub_id and obj.get("client_reference_id"):
            plano = (obj.get("metadata") or {}).get("plano", "mensal")
            p = config.PLANOS.get(plano) or {}
            auth.assinatura_set(int(obj["client_reference_id"]), "stripe", sub_id,
                                obj.get("customer"), plano, p.get("dias", 30),
                                p.get("valor", 0.0), "ativa")
    elif tipo == "invoice.paid":
        # RENOVAÇÃO automática (mês/ano seguinte). A 1ª fatura
        # (subscription_create) já foi tratada no checkout.session.completed.
        if obj.get("billing_reason") == "subscription_cycle" and obj.get("subscription"):
            a = auth.assinatura_por_sub(obj["subscription"])
            if a and obj.get("id"):
                auth.checkout_registrar("stripe", obj["id"], a["user_id"], a["plano"],
                                        a["dias"], a["valor"], "stripe")
                auth.checkout_pagar("stripe", obj["id"], obj.get("payment_intent"))
    elif tipo == "customer.subscription.deleted":
        # assinatura encerrada (cancelou / parou de pagar) -> volta pro free
        if obj.get("id"):
            auth.assinatura_cancelar(obj["id"])
    elif tipo in ("charge.refunded", "charge.dispute.created",
                  "charge.dispute.funds_withdrawn"):
        # estorno ou chargeback -> tira o PRO da pessoa
        pi = obj.get("payment_intent")
        if pi:
            auth.checkout_revogar_por_pi(pi)
    return {"ok": True}


@app.post("/api/checkout/pix")
def checkout_pix(request: Request, payload: dict = Body(...)):
    """Cria uma cobrança Pix no AbacatePay e devolve a URL de pagamento."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    plano, p = _plano_valido(payload)
    if not p:
        return JSONResponse({"erro": "plano inválido"}, status_code=400)
    if not config.ABACATEPAY_API_KEY:
        return JSONResponse({"erro": "AbacatePay não configurado"}, status_code=503)
    # A v1 exige customer COMPLETO (name, email, cellphone, taxId/CPF). O front
    # coleta CPF + celular numa telinha antes de chamar aqui.
    cpf = "".join(ch for ch in str(payload.get("cpf", "")) if ch.isdigit())
    celular = "".join(ch for ch in str(payload.get("celular", "")) if ch.isdigit())
    if not celular:                       # fallback: usa o WhatsApp salvo no cadastro
        celular = "".join(ch for ch in str(user.get("whatsapp") or "") if ch.isdigit())
    if len(cpf) != 11:
        return JSONResponse({"erro": "CPF inválido — informe os 11 dígitos."}, status_code=400)
    if len(celular) not in (10, 11):
        return JSONResponse({"erro": "Celular inválido — informe com DDD."}, status_code=400)
    cpf_fmt = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    if len(celular) == 11:
        cel_fmt = f"({celular[:2]}) {celular[2:7]}-{celular[7:]}"
    else:
        cel_fmt = f"({celular[:2]}) {celular[2:6]}-{celular[6:]}"
    bump = _quer_bump(payload, plano)
    produtos = [{
        "externalId": "pro-" + plano,
        "name": "SureRadar " + p["nome"],
        "description": "Assinatura " + p["nome"] + " (" + str(p["dias"]) + " dias)",
        "quantity": 1,
        "price": int(round(p["valor"] * 100)),
    }]
    if bump:                                   # order bump: entra como 2º item da cobrança
        a = config.ADDON_VALOR
        produtos.append({
            "externalId": "addon-valor",
            "name": "SureRadar " + a["nome"],
            "description": a["nome"] + " (" + str(a["dias"]) + " dias)",
            "quantity": 1,
            "price": int(round(a["valor"] * 100)),
        })
    total = p["valor"] + (config.ADDON_VALOR["valor"] if bump else 0)
    body = {
        "frequency": "ONE_TIME",
        "methods": ["PIX"],
        "products": produtos,
        "returnUrl": config.SITE_URL + "/planos",
        "completionUrl": config.SITE_URL + "/perfil?pago=1",
        "customer": {
            "name": user["nome"],
            "email": user["email"],
            "cellphone": cel_fmt,
            "taxId": cpf_fmt,
        },
    }
    try:
        r = requests.post("https://api.abacatepay.com/v1/billing/create", json=body,
                          headers={"Authorization": "Bearer " + config.ABACATEPAY_API_KEY},
                          timeout=20)
    except requests.RequestException as e:
        return JSONResponse({"erro": "falha de rede", "detalhe": str(e)[:120]}, status_code=502)
    if not r.ok:
        return JSONResponse({"erro": "AbacatePay recusou", "detalhe": r.text[:200]}, status_code=502)
    d = (r.json() or {}).get("data") or {}
    bid, url = d.get("id"), d.get("url")
    if not bid or not url:
        return JSONResponse({"erro": "resposta inesperada do AbacatePay"}, status_code=502)
    auth.checkout_registrar("abacatepay", bid, user["id"], plano, p["dias"], total, "pix",
                            addon="valor" if bump else None)
    return {"url": url}


def _max_parcelas(dias):
    """Máximo de parcelas no cartão por plano (AbacatePay exige mín. R$10/parcela)."""
    if dias >= 365:
        return 12
    if dias >= 180:
        return 6
    if dias >= 90:
        return 3
    return 1


_ABACATE_V2 = "https://api.abacatepay.com/v2"
_abacate_prod_cache = {}   # plano -> prod_id (memória; some no redeploy, recria sozinho)


def _abacate_v2_key():
    return config.ABACATEPAY_V2_API_KEY or config.ABACATEPAY_API_KEY


def _abacate_produto_id(plano, p):
    """Garante que o produto do plano existe na AbacatePay (v2) e devolve o prod_id.

    ⚠️ No checkout v2 o PREÇO vem do PRODUTO (não do config). Por isso o externalId
    carrega o preço em centavos: se você mudar o valor no config, o id muda e um
    produto NOVO é criado com o preço certo (senão continuaria cobrando o antigo)."""
    ext = "pro-%s-%d" % (plano, int(round(p["valor"] * 100)))
    if ext in _abacate_prod_cache:
        return _abacate_prod_cache[ext]
    hdr = {"Authorization": "Bearer " + _abacate_v2_key()}
    try:
        r = requests.get(_ABACATE_V2 + "/products/list", headers=hdr, timeout=8)
        if r.ok:
            for prod in ((r.json() or {}).get("data") or []):
                if prod.get("externalId") == ext and prod.get("id"):
                    _abacate_prod_cache[ext] = prod["id"]
                    return prod["id"]
    except requests.RequestException:
        pass
    body = {"externalId": ext, "name": "SureRadar " + p["nome"],
            "description": "Acesso " + p["nome"] + " (" + str(p["dias"]) + " dias)",
            "price": int(round(p["valor"] * 100)), "currency": "BRL"}
    r = requests.post(_ABACATE_V2 + "/products/create", json=body, headers=hdr, timeout=12)
    if not r.ok:
        raise RuntimeError("produto: " + r.text[:160])
    pid = ((r.json() or {}).get("data") or {}).get("id")
    if not pid:
        raise RuntimeError("produto criado sem id")
    _abacate_prod_cache[ext] = pid
    return pid


def _abacate_prewarm_produtos():
    """No boot: garante os produtos dos 4 planos já criados/cacheados, pra o clique
    em 'Cartão' NÃO gastar tempo criando produto. Best-effort (1 listagem + faltantes)."""
    if not _abacate_v2_key():
        return
    hdr = {"Authorization": "Bearer " + _abacate_v2_key()}
    existentes = {}
    try:
        r = requests.get(_ABACATE_V2 + "/products/list", headers=hdr, timeout=8)
        if r.ok:
            for prod in ((r.json() or {}).get("data") or []):
                if prod.get("externalId") and prod.get("id"):
                    existentes[prod["externalId"]] = prod["id"]
    except requests.RequestException:
        pass
    # planos + o add-on das Odds Erradas (order bump) já prontos no boot
    for plano, p in list(config.PLANOS.items()) + [("valor", config.ADDON_VALOR)]:
        ext = "pro-%s-%d" % (plano, int(round(p["valor"] * 100)))   # id carrega o preço
        if ext in existentes:
            _abacate_prod_cache[ext] = existentes[ext]
        elif ext not in _abacate_prod_cache:
            try:
                _abacate_produto_id(plano, p)
            except Exception as e:
                print(f"!! prewarm produto {plano}:", e)


_abacate_cust_cache = {}   # user_id -> customerId (memória; recria no redeploy)


def _abacate_customer_id(user):
    """Cria/reusa um customer na AbacatePay v2 pra PRÉ-PREENCHER email+nome no
    checkout. Best-effort: se falhar, devolve None e o checkout segue sem pré-preencher."""
    uid = user["id"]
    if uid in _abacate_cust_cache:
        return _abacate_cust_cache[uid]
    body = {"email": user["email"], "name": user.get("nome") or "",
            "metadata": {"userId": str(uid)}}
    try:
        r = requests.post(_ABACATE_V2 + "/customers/create", json=body,
                          headers={"Authorization": "Bearer " + _abacate_v2_key()}, timeout=12)
        if r.ok:
            cid = ((r.json() or {}).get("data") or {}).get("id")
            if cid:
                _abacate_cust_cache[uid] = cid
                return cid
        print(">> abacate customer nao ok:", r.status_code, r.text[:120])
    except requests.RequestException as e:
        print(">> abacate customer erro:", str(e)[:80])
    return None


@app.post("/api/checkout/cartao")
def checkout_cartao(request: Request, payload: dict = Body(...)):
    """Checkout no CARTÃO com PARCELAMENTO (AbacatePay API v2). Pagamento único; o
    cliente escolhe as parcelas na própria tela da AbacatePay (Mensal 1x / Trimestral
    3x / Semestral 6x / Anual 12x). A página v2 já coleta CPF + dados do cartão, então
    aqui NÃO pedimos CPF. Libera o PRO no webhook checkout.completed."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    plano, p = _plano_valido(payload)
    if not p:
        return JSONResponse({"erro": "plano inválido"}, status_code=400)
    if not _abacate_v2_key():
        return JSONResponse({"erro": "AbacatePay não configurado"}, status_code=503)
    try:
        prod_id = _abacate_produto_id(plano, p)
    except Exception as e:
        return JSONResponse({"erro": "AbacatePay (produto)", "detalhe": str(e)[:200]}, status_code=502)
    bump = _quer_bump(payload, plano)
    items = [{"id": prod_id, "quantity": 1}]
    if bump:                                   # order bump: 2º produto na mesma cobrança
        try:
            items.append({"id": _abacate_produto_id("valor", config.ADDON_VALOR), "quantity": 1})
        except Exception as e:                 # add-on falhou: vende o plano do mesmo jeito
            print("!! produto do add-on:", e)
            bump = False
    total = p["valor"] + (config.ADDON_VALOR["valor"] if bump else 0)
    body = {
        "items": items,
        "methods": ["CARD"],
        "returnUrl": config.SITE_URL + "/planos",
        "completionUrl": config.SITE_URL + "/perfil?pago=1",
        "externalId": "sr-" + str(user["id"]) + "-" + plano,
        "card": {"maxInstallments": _max_parcelas(p["dias"])},
    }
    cid = _abacate_customer_id(user)      # pré-preenche email+nome na tela (best-effort)
    if cid:
        body["customerId"] = cid
    try:
        r = requests.post(_ABACATE_V2 + "/checkouts/create", json=body,
                          headers={"Authorization": "Bearer " + _abacate_v2_key()},
                          timeout=12)
    except requests.RequestException as e:
        return JSONResponse({"erro": "falha de rede", "detalhe": str(e)[:120]}, status_code=502)
    if not r.ok:
        return JSONResponse({"erro": "AbacatePay recusou", "detalhe": r.text[:300]}, status_code=502)
    d = (r.json() or {}).get("data") or {}
    bid, url = d.get("id"), d.get("url")
    if not bid or not url:
        return JSONResponse({"erro": "resposta inesperada do AbacatePay"}, status_code=502)
    auth.checkout_registrar("abacatepay", bid, user["id"], plano, p["dias"], total, "cartao",
                            addon="valor" if bump else None)
    return {"url": url}


@app.get("/api/checkout/prep")
def checkout_prep(request: Request):
    """Chamado ao abrir /planos: cria/cacheia o customer da AbacatePay em segundo
    plano, pra o clique em 'Cartão' já sair com o customerId (email preenchido) e rápido."""
    user = _usuario(request)
    if not user or not _abacate_v2_key():
        return {"ok": False}
    return {"ok": bool(_abacate_customer_id(user))}


@app.post("/api/webhook/abacatepay")
async def webhook_abacate(request: Request):
    if (not config.ABACATEPAY_WEBHOOK_SECRET or
            request.query_params.get("webhookSecret") != config.ABACATEPAY_WEBHOOK_SECRET):
        return JSONResponse({"erro": "secret inválido"}, status_code=401)
    try:
        ev = await request.json()
    except Exception:
        return JSONResponse({"erro": "payload inválido"}, status_code=400)
    # v1 Pix = billing.paid; v2 cartão (parcelamento) = checkout.completed.
    # A estrutura do data varia entre v1/v2, então tentamos vários caminhos de id.
    tipo = ev.get("event")
    d = ev.get("data") or {}
    cands = []
    for obj in (d, d.get("billing"), d.get("checkout"), d.get("pixQrCode"), d.get("payment")):
        if isinstance(obj, dict) and obj.get("id"):
            cands.append(obj["id"])
    # log sem PII (só chaves + ids) — ajuda a confirmar o payload no 1º pagamento real
    print(">> webhook abacate:", tipo, "| data keys:", list(d.keys()), "| ids:", cands)
    if tipo in ("billing.paid", "billing.completed", "payment.paid",
                "checkout.completed", "transparent.completed"):
        for bid in cands:
            res = auth.checkout_pagar("abacatepay", bid)
            if res:
                _confirmar_compra_email(res["user_id"])
                _avisar_venda_admin(res)
                break
    elif tipo in ("checkout.refunded", "checkout.disputed", "billing.refunded",
                  "transparent.refunded", "transparent.disputed", "payment.refunded"):
        # estorno / chargeback do cartão -> tira o PRO da pessoa
        for bid in cands:
            if auth.checkout_revogar("abacatepay", bid):
                break
    return {"ok": True}


# --- Banca (entradas do usuário) persistida no banco ---
@app.get("/api/banca")
def banca_ler(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    return {"entradas": auth.banca_get(user["id"])}


@app.post("/api/banca")
def banca_salvar(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    entradas = payload.get("entradas")
    if not isinstance(entradas, list) or len(entradas) > 500:
        return JSONResponse({"erro": "formato inválido"}, status_code=400)
    auth.banca_set(user["id"], entradas)
    return {"ok": True, "salvas": len(entradas)}


def _aviso_renovar(dias):
    """True quando faltam poucos dias (<= config.AVISO_RENOVACAO_DIAS) p/ vencer."""
    return dias is not None and 0 <= dias <= config.AVISO_RENOVACAO_DIAS


@app.get("/api/me")
def me(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    dias = auth.dias_restantes(user)
    return {"id": user["id"], "nome": user["nome"], "email": user["email"],
            "plano": _plano_efetivo(user),
            "dias": dias, "aviso_renovar": _aviso_renovar(dias),
            "whatsapp": user.get("whatsapp") or "",
            "admin": _admin_email(user),
            "alertas": _alerta_liberado(user),
            "valor_beta": _valor_liberado(user),
            "valor_dias": auth.valor_dias_restantes(user),
            "valor_preco": config.ADDON_VALOR["valor"],
            "valor_dias_addon": config.ADDON_VALOR["dias"]}


def _valor_liberado(user):
    """Aba 'Odds Erradas das Casas': quem comprou o add-on (avulso ou no order bump)
    e os e-mails do beta em VALUEBET_BETA_EMAILS. A aba aparece pra todo mundo — quem
    não comprou vê só uma amostra, o resto borrado."""
    # Modo vitrine de lançamento: NINGUÉM tem acesso total (nem PRO, nem quem comprou).
    # Todo mundo cai na amostra borrada. Desliga com a env VALOR_TEASER_GERAL=0.
    if config.VALOR_TEASER_GERAL:
        return False
    if not user:
        return False
    if auth.valor_dias_restantes(user):
        return True
    emails = [e.strip().lower() for e in (config.VALUEBET_BETA_EMAILS or "").split(",") if e.strip()]
    return user.get("email", "").strip().lower() in emails


def _alerta_liberado(user):
    """Alertas no Telegram: função PRO (ou e-mails do beta em ALERTA_BETA_EMAILS)."""
    if not user:
        return False
    if _plano_efetivo(user) == "pro":
        return True
    emails = [e.strip().lower() for e in (config.ALERTA_BETA_EMAILS or "").split(",") if e.strip()]
    return user.get("email", "").strip().lower() in emails


@app.get("/api/alerta")
def alerta_config(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    if not _alerta_liberado(user):
        return {"liberado": False, "precisa_pro": True}
    cfg = auth.alerta_get(user["id"]) or {}
    connect_url = ""
    if not cfg.get("conectado"):
        bot = notifier.bot_username()
        if bot:
            connect_url = f"https://t.me/{bot}?start={auth.alerta_token(user['id'])}"
    return {"liberado": True, "conectado": bool(cfg.get("conectado")),
            "ativo": bool(cfg.get("ativo", True)), "casas": cfg.get("casas", []),
            "min_pct": cfg.get("min_pct", 5), "connect_url": connect_url}


@app.post("/api/alerta")
def alerta_salvar_api(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    if not _alerta_liberado(user):
        return JSONResponse({"erro": "indisponível"}, status_code=403)
    auth.alerta_salvar(user["id"], payload.get("casas") or [],
                       payload.get("min_pct", 5), bool(payload.get("ativo", True)))
    return {"ok": True}


@app.post("/api/alerta/desconectar")
def alerta_desconectar_api(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    if not _alerta_liberado(user):
        return JSONResponse({"erro": "indisponível"}, status_code=403)
    auth.alerta_desconectar(user["id"])
    return {"ok": True}


@app.get("/api/perfil")
def perfil_dados(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    dias = auth.dias_restantes(user)
    return {
        "nome": user["nome"], "email": user["email"], "plano": user["plano"],
        "dias": dias,
        "expira": user.get("plano_expira"),
        "aviso_renovar": _aviso_renovar(dias),
        "admin": _admin_email(user),
        "whatsapp": user.get("whatsapp") or "",
        "tem_assinatura": bool(auth.assinatura_do_user(user["id"])),
        "pagamentos": auth.listar_pagamentos(user["id"]),
        # add-on das Odds Erradas (comprado à parte do plano)
        "valor_dias": auth.valor_dias_restantes(user),
        "valor_preco": config.ADDON_VALOR["valor"],
        "valor_dias_addon": config.ADDON_VALOR["dias"],
    }


def _emails_excluidos(user=None):
    """E-mails que NÃO entram em broadcast nem no painel de pendentes: admin, dono e
    o do próprio admin logado (contas de teste)."""
    ex = set(getattr(config, "EMAILS_EXCLUIR", set()) or set())     # env EMAILS_EXCLUIR
    ex |= set(getattr(config, "ADMIN_EMAILS", set()) or set())
    for e in (getattr(config, "OWNER_EMAILS", "") or "").split(","):
        if e.strip():
            ex.add(e.strip().lower())
    if user and user.get("email"):
        ex.add(user["email"].strip().lower())
    return ex


@app.post("/api/admin/avisar-parcelamento")
def admin_avisar_parcelamento(request: Request):
    """Dispara o e-mail 'parcelamento liberado' pra quem gerou checkout e NÃO é PRO.
    Exclui os e-mails de admin/dono e o do próprio admin logado. Dedup: cada pessoa
    recebe só 1x (tipo 'promo_parcelamento_12x'). Envia em 2º plano (não trava)."""
    import threading
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    if not config.RESEND_API_KEY:
        return JSONResponse({"erro": "RESEND_API_KEY não configurada"}, status_code=503)
    excluir = _emails_excluidos(user)
    audiencia = [u for u in auth.usuarios_para_recuperacao(planos=("trimestral", "semestral", "anual"))
                 if (u.get("email") or "").strip().lower() not in excluir]

    def _rodar():
        enviados = 0
        for u in audiencia:
            try:
                if not auth.registrar_email(u["id"], "promo_parcelamento_12x"):
                    continue                     # já recebeu esse aviso
                unsub = config.SITE_URL + "/descadastrar?u=" + auth.unsub_token(u["id"])
                if emailer.enviar_parcelamento(u["email"], u["nome"], unsub):
                    enviados += 1
            except Exception as e:
                print("!! avisar-parcelamento:", e)
        print(f">> aviso parcelamento: {enviados} e-mail(s) enviado(s).")

    threading.Thread(target=_rodar, name="promo-parcelamento", daemon=True).start()
    return {"ok": True, "audiencia": len(audiencia),
            "excluidos": sorted(excluir)}


@app.post("/api/admin/testar-email")
def admin_testar_email(request: Request):
    """Manda um e-mail de teste pro próprio admin e devolve a resposta do Resend."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "só admin"}, status_code=403)
    ok, detalhe = emailer.testar(user["email"])
    return {"ok": ok, "para": user["email"], "from": config.EMAIL_FROM, "detalhe": detalhe}


@app.api_route("/api/admin/testar-telegram", methods=["GET", "POST"])
def admin_testar_telegram(request: Request):
    """Diagnóstico do bot do Telegram (valida token + posta no grupo).
    Aceita GET p/ você abrir direto no navegador (logado como admin)."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "Faça login com seu e-mail de admin primeiro."},
                            status_code=403)
    return notifier.testar()


@app.api_route("/api/admin/telegram-chats", methods=["GET", "POST"])
def admin_telegram_chats(request: Request):
    """Descobre o ID dos grupos onde o bot está — SEM postar nada no grupo."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "Faça login com seu e-mail de admin primeiro."},
                            status_code=403)
    return notifier.descobrir_chats()


_MSG_BOASVINDAS = (
    "🏆 <b>BEM-VINDO AO ALQUIMIA DO GREEN</b> 🏆\n\n\n"
    "Aqui você recebe <b>+30 apostas GRÁTIS todos os dias</b> 📲\n\n\n"
    "✅ Entradas de <b>1% a 5% de lucro garantido</b>\n\n"
    "✅ <b>Surebet</b> = você cobre todos os resultados em casas diferentes e "
    "<b>trava o lucro, dê no que der</b>. Não é sorte, é matemática 🧮\n\n"
    "✅ Cada entrada já vem com as <b>casas, as odds e o link</b> pra apostar\n\n\n"
    "💰 <b>COMO USAR:</b>\n\n"
    "1️⃣ Chegou a entrada → clica no link de cada casa\n\n"
    "2️⃣ Aposta os valores indicados (ou usa a nossa <b>calculadora</b> com a SUA banca)\n\n"
    "3️⃣ Lucro travado ✅\n\n\n"
    "🔓 <b>Quer as entradas de 5% a 15%+?</b>\n\n"
    "Essas são exclusivas do <b>PRO</b> 👉 https://sureradar.site\n\n\n"
    "⚠️ +18 • Aposte com responsabilidade"
)


@app.get("/api/admin/campanhas")
def admin_campanhas(request: Request):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    camps = auth.listar_campanhas()
    for c in camps:
        c["landing"] = config.SITE_URL + "/grupo?c=" + str(c["id"])
        c["membros"] = int(c.get("membros") or 0)
    return {"campanhas": camps}


@app.post("/api/admin/campanhas")
def admin_criar_campanha(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    nome = (payload.get("nome") or "").strip()[:60]
    if len(nome) < 2:
        return JSONResponse({"erro": "Dê um nome à campanha."}, status_code=400)
    link = (payload.get("link") or "").strip()
    if not link:                          # sem link colado -> o bot cria um novo
        link = notifier.criar_invite_link(nome)
        if not link:
            return JSONResponse({"erro": "Não deu pra criar o link no Telegram. "
                                 "Cole um link de convite existente, ou confira se o bot "
                                 "é admin do canal com permissão de convidar."}, status_code=400)
    cid = auth.criar_campanha(nome, link)
    return {"ok": True, "id": cid, "link": link,
            "landing": config.SITE_URL + "/grupo?c=" + str(cid)}


@app.post("/api/admin/campanhas/excluir")
def admin_excluir_campanha(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    try:
        auth.excluir_campanha(int(payload.get("id")))
    except (TypeError, ValueError):
        return JSONResponse({"erro": "id inválido"}, status_code=400)
    return {"ok": True}


@app.get("/api/admin/conteudo")
def admin_conteudo(request: Request):
    """Calendário de conteúdo de SEO: lista as páginas do plano com status."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    pgs = auth.listar_paginas()
    for p in pgs:
        p["pronto"] = (STATIC_DIR / p["arquivo"]).exists()   # rascunho já escrito?
        p["url"] = "https://sureradar.site/" + p["slug"]
    return {"paginas": pgs}


@app.post("/api/admin/conteudo/publicar")
def admin_conteudo_publicar(request: Request, payload: dict = Body(...)):
    """Publica (ou despublica) uma página de SEO pelo /admin com um clique."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    slug = (payload.get("slug") or "").strip()
    publicar = bool(payload.get("publicar", True))
    row = next((p for p in auth.listar_paginas() if p["slug"] == slug), None)
    if not row:
        return JSONResponse({"erro": "página não encontrada"}, status_code=404)
    if publicar and not (STATIC_DIR / row["arquivo"]).exists():
        return JSONResponse({"erro": "essa página ainda não foi criada"}, status_code=400)
    auth.publicar_pagina(slug, publicar)
    return {"ok": True, "publicado": publicar}


def _norm_nome(s):
    """Normaliza nome pra casar campanha interna x conjunto do Facebook.
    Remove acentos, caixa, e os marcadores de duplicata do Facebook ('Cópia'/'Copy'
    e o número que os segue) — assim 'Video CTV 1 — Cópia 2' casa com 'Video CTV 1'."""
    s = unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode().lower()
    out, pular_num = [], False
    for t in s.split():
        if t in ("copia", "copy"):
            pular_num = True
            continue
        if pular_num and t.isdigit():
            pular_num = False
            continue
        pular_num = False
        out.append(t)
    return " ".join(out)


_BR_TZ = timezone(timedelta(hours=-3))


def _periodo(preset):
    """(datas do período p/ membros, desde_ts, ate_ts p/ cadastros/receita).
    'tudo' -> datas=None (sem filtro de dia) e range bem amplo."""
    import time as _t
    if preset == "tudo":
        return None, 0.0, _t.time() + 86400.0
    hoje = datetime.now(_BR_TZ).date()
    if preset == "ontem":
        dias = [hoje - timedelta(days=1)]
    elif preset == "7dias":
        dias = [hoje - timedelta(days=i) for i in range(7)]
    elif preset == "30dias":
        dias = [hoje - timedelta(days=i) for i in range(30)]
    else:
        dias = [hoje]
    dias_set = {d.isoformat() for d in dias}
    menor, maior = min(dias), max(dias)
    desde = datetime(menor.year, menor.month, menor.day, tzinfo=_BR_TZ).timestamp()
    ate = (datetime(maior.year, maior.month, maior.day, tzinfo=_BR_TZ)
           + timedelta(days=1)).timestamp()
    return dias_set, desde, ate


def _membros_no_periodo(camp, dias_set):
    """Membros que entraram nessa campanha no período (dias_set None = total)."""
    if dias_set is None:
        return int(camp.get("membros", 0) or 0)
    return sum(int(d.get("qtd", 0)) for d in (camp.get("por_dia") or [])
               if d.get("dia") in dias_set)


@app.get("/api/admin/fb-gastos")
def admin_fb_gastos(request: Request, preset: str = "hoje", level: str = "campaign",
                    status: str = "ativas"):
    """Dashboard estilo Gerenciador de Anúncios: puxa AS CAMPANHAS DO META
    automaticamente (auto-sync via token) e mostra Gasto, Resultados(leads),
    Impressões, Cliques, Veiculação + cruza com nosso rastreio (membros/cadastros/
    receita) por nome. level: 'campaign' (padrão) ou 'adset'."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    level = "adset" if level == "adset" else "campaign"
    so_ativas = (status or "ativas") != "todas"     # padrão: esconde as pausadas
    alvo = (auth.meta_campanha_get() or {}).get("id")   # campanha DESTE projeto
    fb_on = meta_ads.configurado()
    gastos, fb_erro, st_map = [], None, {}
    if fb_on:
        try:
            gastos = meta_ads.gastos(preset=preset, level=level)
            if alvo:      # só a campanha do projeto (e, no nível conjunto, só os dela)
                gastos = [g for g in gastos if (g.get("campaign_id") or g.get("id")) == alvo]
            st_map = (meta_ads.status_campanhas() if level == "campaign"
                      else meta_ads.status_adsets())
        except Exception as e:
            fb_erro = str(e)
    internas = auth.listar_campanhas()
    por_nome = {_norm_nome(c["nome"]): c for c in internas}
    dias_set, desde, ate = _periodo(preset)
    met = auth.metricas_por_campanha_periodo(desde, ate)   # cadastros/vendas/receita NO PERÍODO

    usados = set()   # ids internos já casados com uma campanha do Meta

    def _enriquecer(nome):
        """Cruza uma campanha do Meta com nosso rastreio interno (por nome)."""
        c = por_nome.get(_norm_nome(nome))
        if not c:
            return None
        usados.add(c["id"])
        m = met.get(str(c["id"]), {})
        return {
            "campanha_id": c["id"],
            "link_site": config.SITE_URL + "/grupo?c=" + str(c["id"]),
            "membros": _membros_no_periodo(c, dias_set), "membros_total": int(c["membros"]),
            "cadastros": int(m.get("cadastros", 0)), "vendas": int(m.get("vendas", 0)),
            "receita": round(float(m.get("receita", 0.0)), 2),
        }

    linhas = []
    # 1) TODAS as campanhas que vieram do Meta (auto-sync) — mesmo sem rastreio nosso
    for g in gastos:
        st = st_map.get(g.get("id", ""), {})
        row = {
            "origem": "meta", "nome": g.get("nome", "—"), "gasto": round(g.get("gasto", 0.0), 2),
            "impressoes": int(g.get("impressoes", 0)), "cliques": int(g.get("cliques", 0)),
            "leads_fb": int(g.get("leads_fb", 0)),
            "status": st.get("status", ""), "objetivo": st.get("objetivo", ""),
            "campanha_id": None, "link_site": None, "membros": None, "membros_total": None,
            "cadastros": None, "vendas": None, "receita": None,
        }
        extra = _enriquecer(g.get("nome", ""))
        if extra:
            row.update(extra)
        linhas.append(row)
    # 2) Nossas campanhas manuais que NÃO têm gasto no Meta (não somem do painel).
    #    Com uma campanha do projeto escolhida, esconde os rastreios soltos (confundem).
    for c in (internas if not alvo else []):
        if c["id"] in usados:
            continue
        m = met.get(str(c["id"]), {})
        linhas.append({
            "origem": "interna", "nome": c["nome"], "gasto": 0.0,
            "impressoes": 0, "cliques": 0, "leads_fb": 0, "status": "", "objetivo": "",
            "campanha_id": c["id"], "link_site": config.SITE_URL + "/grupo?c=" + str(c["id"]),
            "membros": _membros_no_periodo(c, dias_set), "membros_total": int(c["membros"]),
            "cadastros": int(m.get("cadastros", 0)), "vendas": int(m.get("vendas", 0)),
            "receita": round(float(m.get("receita", 0.0)), 2),
        })
    # filtro de veiculação: por padrão só ATIVAS (esconde pausadas/arquivadas).
    # Mantém as internas (rastreio nosso, sem status do Meta) e as de status desconhecido.
    if so_ativas:
        linhas = [r for r in linhas
                  if r.get("origem") == "interna"
                  or (r.get("status") or "") not in meta_ads.PAUSADAS]
    # totais (linha de rodapé estilo Gerenciador) — já refletem o filtro
    t = {"gasto": 0.0, "impressoes": 0, "cliques": 0, "leads_fb": 0,
         "membros": 0, "cadastros": 0, "vendas": 0, "receita": 0.0}
    for r in linhas:
        t["gasto"] += r["gasto"]; t["impressoes"] += r["impressoes"]; t["cliques"] += r["cliques"]
        t["leads_fb"] += r["leads_fb"]; t["membros"] += r["membros"] or 0
        t["cadastros"] += r["cadastros"] or 0; t["vendas"] += r["vendas"] or 0
        t["receita"] += r["receita"] or 0.0
    linhas.sort(key=lambda x: (x["gasto"], x["receita"] or 0), reverse=True)
    return {
        "configurado": True, "fb_conectado": fb_on, "fb_erro": fb_erro,
        "preset": preset, "level": level, "status": "ativas" if so_ativas else "todas",
        "n": len(linhas),
        "kpis": {
            "gasto": round(t["gasto"], 2), "membros": int(t["membros"]),
            "leads_fb": int(t["leads_fb"]), "impressoes": int(t["impressoes"]),
            "cliques": int(t["cliques"]), "cadastros": int(t["cadastros"]),
            "vendas": int(t["vendas"]), "receita": round(t["receita"], 2),
            "custo_por_membro": round(t["gasto"] / t["membros"], 2) if t["membros"] else None,
            "roi": round(t["receita"] - t["gasto"], 2),
            "roas": round(t["receita"] / t["gasto"], 2) if t["gasto"] > 0 else None,
        },
        "gastos": linhas,
    }


@app.get("/api/admin/meta-campanhas")
def admin_meta_campanhas(request: Request):
    """Lista as campanhas do Facebook pro seletor 'qual é a campanha deste projeto'."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    if not meta_ads.configurado():
        return {"campanhas": [], "escolhida": {}}
    itens = []
    for cid, d in (meta_ads.status_campanhas() or {}).items():
        itens.append({"id": cid, "nome": d.get("nome") or cid,
                      "status": d.get("status", ""),
                      "ativa": d.get("status", "") not in meta_ads.PAUSADAS})
    itens.sort(key=lambda x: (not x["ativa"], x["nome"].lower()))
    return {"campanhas": itens, "escolhida": auth.meta_campanha_get()}


@app.post("/api/admin/meta-campanha")
def admin_meta_campanha_salvar(request: Request, payload: dict = Body(...)):
    """Salva qual campanha do Facebook é a DESTE projeto (id vazio = limpar)."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    cid = (payload.get("id") or "").strip()
    nome = (payload.get("nome") or "").strip()
    auth.meta_campanha_set({"id": cid, "nome": nome} if cid else {})
    return {"ok": True, "escolhida": auth.meta_campanha_get()}


@app.get("/api/admin/marketing")
def admin_marketing(request: Request):
    """Gasto com MARKETING (Meta) pra Visão geral — é o que falta pra saber o LUCRO
    real (receita - marketing). Conta SÓ AS CAMPANHAS ATIVAS: a conta de anúncios é
    compartilhada com outros projetos do usuário, e hoje só as ativas são deste
    projeto. Chamado em 2º plano (não trava o painel)."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    if not meta_ads.configurado():
        return {"ok": False, "erro": "Meta não conectado"}
    try:
        sel = auth.meta_campanha_get()          # a campanha DESTE projeto (se escolhida)
        alvo = sel.get("id")
        st_map = meta_ads.status_campanhas() if not alvo else {}

        def _conta(x):
            if alvo:                             # só a campanha escolhida
                return x.get("id") == alvo
            # sem escolha: cai no antigo (todas as ativas) — pode misturar projetos
            return (st_map.get(x.get("id", "")) or {}).get("status", "") not in meta_ads.PAUSADAS

        def _soma(preset):
            return round(sum(x.get("gasto", 0)
                             for x in meta_ads.gastos(preset=preset, level="campaign")
                             if _conta(x)), 2)
        return {"ok": True, "campanha": sel.get("nome", ""), "so_ativas": not alvo,
                "gasto_total": _soma("tudo"), "gasto_30d": _soma("30dias"),
                "gasto_7d": _soma("7dias"), "gasto_hoje": _soma("hoje")}
    except Exception as e:
        return {"ok": False, "erro": str(e)[:160]}


@app.get("/api/admin/meta-test")
def admin_meta_test(request: Request):
    """Diagnóstico da conexão Meta: diz se o token funciona e mostra o erro exato
    (token vencido? conta restrita? conta errada?) sem deixar o admin no escuro."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    aid = (config.META_AD_ACCOUNT_ID or "").strip()
    if not meta_ads.configurado():
        return {"ok": False, "configurado": False, "conta": aid,
                "erro": "Falta META_ACCESS_TOKEN e/ou META_AD_ACCOUNT_ID no Railway."}
    try:
        linhas = meta_ads.gastos(preset="tudo", level="campaign")
        total = round(sum(x.get("gasto", 0) for x in linhas), 2)
        return {"ok": True, "configurado": True, "conta": aid,
                "campanhas": len(linhas), "gasto_total": total,
                "amostra": [x.get("nome", "") for x in linhas[:5]]}
    except Exception as e:
        return {"ok": False, "configurado": True, "conta": aid, "erro": str(e)}


@app.get("/api/admin/pendentes")
def admin_pendentes(request: Request):
    """Quem gerou checkout (Pix + cartão) e NÃO pagou — pra recuperar a venda."""
    import time as _t
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    agora = _t.time()
    excluir = _emails_excluidos(user)     # tira seu e-mail e os de admin do painel
    linhas, tot = [], 0.0
    for c in auth.checkouts_pendentes():
        if (c.get("email") or "").strip().lower() in excluir:
            continue
        val = float(c.get("valor", 0) or 0)
        tot += val
        linhas.append({
            "email": c.get("email"), "nome": c.get("nome"),
            "whatsapp": c.get("whatsapp") or "",
            "plano": c.get("plano"), "valor": val,
            "metodo": (c.get("metodo") or c.get("provider") or "").lower(),
            "horas_atras": round((agora - float(c.get("criado", agora))) / 3600, 1),
            "ja_pro": (c.get("user_plano") == "pro"),
        })
    return {"total_valor": round(tot, 2), "qtd": len(linhas), "pendentes": linhas}


@app.get("/api/campanha-link")
def campanha_link_pub(c: int = 0):
    """Público: a landing /grupo pega o link de convite da campanha pra usar nos botões."""
    return {"link": auth.campanha_link(c) if c else None}


_CAMPANHAS_SEED = ["FB Criativo 1 - Dor", "FB Criativo 2 - Desejo",
                   "FB Criativo 3 - Urgencia", "FB Criativo 4 - Autoridade"]


@app.api_route("/api/admin/seed-campanhas", methods=["GET", "POST"])
def admin_seed_campanhas(request: Request):
    """Cria de uma vez as 4 campanhas dos criativos (não duplica se já existir)."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "Faça login com seu e-mail de admin primeiro."},
                            status_code=403)
    existentes = {c["nome"] for c in auth.listar_campanhas()}
    resultado = []
    for nome in _CAMPANHAS_SEED:
        if nome in existentes:
            resultado.append({"nome": nome, "status": "já existia"})
            continue
        link = notifier.criar_invite_link(nome)
        if link:
            cid = auth.criar_campanha(nome, link)
            resultado.append({"id": cid, "nome": nome,
                              "landing": config.SITE_URL + "/grupo?c=" + str(cid), "link": link})
        else:
            resultado.append({"nome": nome, "erro": "não deu pra criar o link — "
                             "confira se o bot é admin do canal com permissão de convidar."})
    return {"resultado": resultado}


_MSG_VIDEO = (
    "🎬 <b>SAIU VÍDEO NOVO NO CANAL!</b> 🎬\n\n\n"
    "🔥 <b>Série: DOS R$0 AOS R$500 COM SUREBET</b> 🔥\n\n\n"
    "Tô mostrando na prática, passo a passo, como transformar uma banca pequena em "
    "R$500 usando <b>SUREBET</b> — sem achismo, só matemática. 🧮\n\n"
    "📺 É real, é transparente, e você acompanha cada entrada comigo.\n\n\n"
    "👉 <b>ASSISTE AGORA:</b>\n"
    "https://www.youtube.com/@AlquimiadoGreen\n\n\n"
    "💚 Se inscreve no canal e ativa o 🔔 pra não perder os próximos — a meta é "
    "chegar nos R$500 juntos!"
)


@app.api_route("/api/admin/postar-video", methods=["GET", "POST"])
def admin_postar_video(request: Request):
    """Posta o anúncio do vídeo novo (com prévia do YouTube) no canal."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "Faça login com seu e-mail de admin primeiro."},
                            status_code=403)
    ok = notifier.enviar_texto(_MSG_VIDEO, preview=True)
    return {"ok": ok, "postou_em_chat_id": config.TELEGRAM_CHAT_ID or "(não configurado)"}


@app.api_route("/api/admin/postar-boasvindas", methods=["GET", "POST"])
def admin_postar_boasvindas(request: Request):
    """Posta a mensagem de boas-vindas (formatada) no canal configurado."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "Faça login com seu e-mail de admin primeiro."},
                            status_code=403)
    ok = notifier.enviar_texto(_MSG_BOASVINDAS)
    return {"ok": ok, "postou_em_chat_id": config.TELEGRAM_CHAT_ID or "(não configurado)"}


@app.post("/api/perfil/whatsapp")
def perfil_whatsapp(request: Request, payload: dict = Body(...)):
    """Salva/atualiza o WhatsApp (usado tb p/ contas Google que não têm)."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    ok, res = auth.atualizar_whatsapp(user["id"], payload.get("whatsapp", ""))
    if not ok:
        return JSONResponse({"erro": res}, status_code=400)
    return {"ok": True, "whatsapp": res}


# --- Tickets de suporte (usuário) ---
@app.get("/api/tickets")
def tickets_listar(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    return {"tickets": auth.listar_tickets_user(user["id"])}


@app.post("/api/tickets")
def tickets_criar(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    ok, erro = auth.criar_ticket(user["id"], payload.get("mensagem", ""))
    if not ok:
        return JSONResponse({"erro": erro}, status_code=400)
    return {"ok": True}


@app.post("/api/tickets/responder")
def tickets_responder_user(request: Request, payload: dict = Body(...)):
    """Usuário responde ao próprio ticket (só quando é a vez dele)."""
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    try:
        tid = int(payload.get("ticket_id"))
    except (TypeError, ValueError):
        return JSONResponse({"erro": "id inválido"}, status_code=400)
    ok, erro = auth.responder_ticket_user(user["id"], tid, payload.get("mensagem", ""))
    if not ok:
        return JSONResponse({"erro": erro}, status_code=400)
    return {"ok": True}


# --- Painel ADMIN (dar/renovar PRO com a duração escolhida) ---
@app.post("/api/admin/unlock")
def admin_unlock(request: Request, payload: dict = Body(...)):
    """2º fator do admin: valida a senha (ADMIN_PASSWORD) e libera esta sessão."""
    user = _usuario(request)
    if not _admin_email(user):
        return JSONResponse({"erro": "sem permissão"}, status_code=403)
    if not config.ADMIN_PASSWORD:
        return JSONResponse({"erro": "ADMIN_PASSWORD não configurada no servidor."}, status_code=503)
    if not secrets.compare_digest(str(payload.get("senha", "")), config.ADMIN_PASSWORD):
        return JSONResponse({"erro": "Senha incorreta."}, status_code=401)
    token = secrets.token_urlsafe(32)
    _admin_tokens.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(ADMIN_COOKIE, token, httponly=True, samesite="lax", max_age=8 * 3600, path="/")
    return resp


def _guard_admin(request, user):
    """Devolve (None) se ok, ou um JSONResponse de erro com o motivo."""
    if not _ip_admin_ok(request):
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    if not _admin_email(user):
        return JSONResponse({"erro": "sem permissão"}, status_code=403)
    if not config.ADMIN_PASSWORD or not _admin_desbloqueado(request):
        return JSONResponse({"erro": "precisa_senha",
                             "sem_senha_configurada": not config.ADMIN_PASSWORD},
                            status_code=401)
    return None


@app.get("/api/admin/metricas")
def admin_metricas(request: Request):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    return auth.metricas()


@app.get("/api/admin/usuarios")
def admin_usuarios(request: Request):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    lista = []
    for u in auth.listar_usuarios():
        lista.append({**u, "dias": auth.dias_restantes(u),
                      "aviso_renovar": _aviso_renovar(auth.dias_restantes(u)),
                      "valor_dias": auth.valor_dias_restantes(u)})   # add-on Odds Erradas
    return {"usuarios": lista}


@app.post("/api/admin/plano")
def admin_plano(request: Request, payload: dict = Body(...)):
    """Admin ativa/renova PRO (com a duração escolhida — SOMA nos dias restantes)
    ou volta pra Free. Body: {email, acao:'pro'|'free'|'valor'|'valor-off', dias:int}.

    'valor' libera SÓ o add-on das Odds Erradas — é independente do plano, então dá
    pra deixar a pessoa no FREE e mesmo assim com a aba liberada."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    alvo = auth.pegar_por_email(payload.get("email", ""))
    if not alvo:
        return JSONResponse({"erro": "usuário não encontrado"}, status_code=404)
    acao = payload.get("acao", "pro")
    if acao == "free":
        auth.voltar_free(alvo["id"])
        return {"ok": True, "email": alvo["email"], "plano": "free"}
    if acao == "valor-off":
        auth.revogar_valor(alvo["id"])
        return {"ok": True, "email": alvo["email"], "valor_dias": None}
    if acao == "valor":
        try:
            dias_v = int(payload.get("dias", config.ADDON_VALOR["dias"]))
        except (TypeError, ValueError):
            dias_v = config.ADDON_VALOR["dias"]
        dias_v = max(1, min(dias_v, 3650))
        auth.ativar_valor(alvo["id"], dias_v, 0.0, metodo="admin")   # cortesia: R$ 0
        atual = auth.pegar_por_email(alvo["email"])
        return {"ok": True, "email": alvo["email"],
                "valor_dias": auth.valor_dias_restantes(atual)}
    try:
        dias = int(payload.get("dias", 30))
    except (TypeError, ValueError):
        dias = 30
    dias = max(1, min(dias, 3650))
    plano_nome = "anual" if dias >= 365 else "mensal"
    valor = 497.0 if dias >= 365 else 97.0
    nova_exp = auth.ativar_pro(alvo["id"], plano_nome, dias, valor, metodo="admin")
    restantes = max(0, int((nova_exp - __import__("time").time()) / 86400))
    return {"ok": True, "email": alvo["email"], "plano": "pro",
            "dias_adicionados": dias, "dias_totais": restantes}


@app.get("/api/admin/tickets")
def admin_tickets(request: Request, status: str = ""):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    return {"tickets": auth.listar_tickets_admin(status or None)}


@app.post("/api/admin/tickets/resolver")
def admin_tickets_resolver(request: Request, payload: dict = Body(...)):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    try:
        tid = int(payload.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"erro": "id inválido"}, status_code=400)
    auth.resolver_ticket(tid)
    return {"ok": True}


@app.post("/api/admin/tickets/responder")
def admin_tickets_responder(background_tasks: BackgroundTasks, request: Request,
                            payload: dict = Body(...)):
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    try:
        tid = int(payload.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"erro": "id inválido"}, status_code=400)
    dono = auth.responder_ticket(tid, payload.get("resposta", ""))
    if not dono:
        return JSONResponse({"erro": "ticket não encontrado ou resposta vazia"}, status_code=400)
    background_tasks.add_task(emailer.enviar_resposta_ticket,
                             dono["email"], dono["nome"], payload.get("resposta", ""))
    return {"ok": True}


@app.post("/api/admin/excluir")
def admin_excluir(request: Request, payload: dict = Body(...)):
    """Admin exclui a conta de um usuário (irreversível). Body: {email}.
    Se houver assinatura Stripe ativa, cancela lá antes (senão continua cobrando)."""
    user = _usuario(request)
    erro = _guard_admin(request, user)
    if erro:
        return erro
    alvo = auth.pegar_por_email(payload.get("email", ""))
    if not alvo:
        return JSONResponse({"erro": "usuário não encontrado"}, status_code=404)
    # cancela assinatura recorrente no Stripe (evita cobrança fantasma)
    a = auth.assinatura_do_user(alvo["id"])
    if a and a.get("provider") == "stripe" and a.get("sub_id") and config.STRIPE_SECRET_KEY:
        try:
            requests.delete("https://api.stripe.com/v1/subscriptions/" + a["sub_id"],
                            auth=(config.STRIPE_SECRET_KEY, ""), timeout=15)
        except requests.RequestException:
            pass
    auth.excluir_usuario(alvo["id"])
    return {"ok": True, "email": alvo["email"]}


# ATENÇÃO: ativação de teste do Pro. SÓ funciona com ALLOW_DEV_PRO=1 no ambiente
# (desligado em produção). Em produção, o Pro é ativado pelo webhook do checkout.
@app.post("/api/dev/ativar-pro")
def dev_ativar_pro(request: Request, payload: dict = Body(...)):
    import os
    if os.getenv("ALLOW_DEV_PRO") != "1":
        return JSONResponse({"erro": "indisponível"}, status_code=403)
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    plano = payload.get("plano", "mensal")
    dias = 365 if plano == "anual" else 30
    valor = 497.0 if plano == "anual" else 97.0
    auth.ativar_pro(user["id"], plano, dias, valor, metodo="teste")
    return {"ok": True}


def _casas_do_filtro():
    """Lista de casas para o filtro, conforme a fonte de dados ativa."""
    if INGESTED_BOOKS:                       # dados raspados da conta têm prioridade
        return INGESTED_BOOKS
    if config.FONTE_DADOS == "surebet":
        import surebet_provider
        return surebet_provider.casas_disponiveis()
    # The Odds API: usa as casas conhecidas classificadas.
    return [{"key": c, "label": c} for c in sorted(config.CASAS_SHARP)]


# --- Ingestão de surebets raspadas da conta (método €29) ---
_SPORT_PT_ID = {
    "Futebol": "Football", "Tênis": "Tennis", "Tenis": "Tennis",
    "Basquete": "Basketball", "Vôlei": "Volleyball", "Volei": "Volleyball",
    "Tênis de Mesa": "TableTennis", "Tênis de mesa": "TableTennis",
}


def _slug(nm):
    return re.sub(r"[^a-z0-9]+", "_", nm.lower()).strip("_") or "casa"


def _norm_sport(s):
    """Normaliza o nome do esporte raspado (tira lixo tipo 'new', acentos, espaços)."""
    low = re.sub(r"\s+", " ", (s or "")).strip().lower()
    low = re.sub(r"^new\s*", "", low)
    if "mesa" in low:
        return "TableTennis"
    if "futebol" in low:
        return "Football"
    if "basquete" in low or "basket" in low:
        return "Basketball"
    if "volei" in low or "vôlei" in low or "voleibol" in low:
        return "Volleyball"
    if "tênis" in low or "tenis" in low:
        return "Tennis"
    if "hóquei" in low or "hoquei" in low:
        return "Hockey"
    return s.strip() if s else "?"


def _tipo_casa(nm):
    return "sharp" if re.search(r"pinnacle|betfair|smarkets|marathon|sbobet|betdaq", nm, re.I) else "retail"


def _inferir_sport(mercados):
    """Fallback: adivinha o esporte pelo TEXTO DO MERCADO quando a raspagem não
    trouxe o esporte (extensão antiga sem a leitura do .booker).

    Só infere onde há alta confiança (mercados típicos de cada esporte). Casos
    ambíguos ('set', 'período', 'pontos') ficam de fora — melhor '?' que errado.
    A raspagem nova manda o esporte certo e ignora isto."""
    m = " ".join(mercados).lower()
    if "ace" in m or "tie-break" in m or "tiebreak" in m or "aces" in m:
        return "Tennis"
    if any(k in m for k in ("escanteio", "córner", "corner", "impedimento", "gol",
                            "chute", "finaliza", "cartã", "cartao", "cartao",
                            "/ dnb", " dnb", "dnb")):
        return "Football"
    return ""


# Assunto padrão do total (Over/Under) quando o surebet.com o omite (mercado
# principal). Ex.: futebol "Acima 3.5" = gols; basquete = pontos.
_ASSUNTO_TOTAL = {
    "Football": "gols", "Basketball": "pontos", "Volleyball": "pontos",
    "Hockey": "gols", "Handball": "gols",
}


def _mercado_completo(market, sport):
    """Deixa o mercado claro pra apostar. Quando é um total 'cru' (só
    'Acima/Abaixo X.X', sem assunto), acrescenta o assunto do esporte
    (ex.: futebol -> 'Acima 3.5 gols'). Se já traz o assunto (tem '-' ou já cita
    gols/pontos/etc.), devolve como está."""
    m = (market or "").strip()
    if " - " in m:   # já traz o assunto (ex.: "Acima 1.5 - escanteios")
        return m
    if not re.match(r"^(acima|abaixo|mais|menos|over|under|total)\b", m, re.I):
        return m
    if re.search(r"gol|ponto|game|set|ace|escanteio|falta|chute|cart", m, re.I):
        return m
    assunto = _ASSUNTO_TOTAL.get(sport)
    return f"{m} {assunto}" if assunto else m


def _link_casa(link):
    """Devolve o link SÓ se for da casa de aposta. NUNCA deixa passar um link do
    surebet.com (o site que o robô lê) — senão o usuário é mandado pro concorrente."""
    if link and "surebet.com" not in str(link).lower():
        return link
    return None


def _sem_link_surebet(lista):
    """Passa a lista de surebets removendo qualquer link que aponte pro surebet.com."""
    limpos = []
    for s in lista:
        c = dict(s)
        c["legs"] = [{**l, "link": _link_casa(l.get("link"))} for l in s["legs"]]
        limpos.append(c)
    return limpos


def _split_teams(teams):
    """Separa 'Time A – Time B' em (Time A, Time B)."""
    for sep in (" – ", " — ", " vs ", " x ", " X ", " - "):
        if sep in teams:
            a, b = teams.split(sep, 1)
            return a.strip(), b.strip()
    return teams.strip(), ""


_MKT_SUBS = [
    ("classificações", "classificação"), ("classificação", "classificação"),
    ("TE + DP", "prorrogação + pênaltis"), ("TE+DP", "prorrogação + pênaltis"),
    ("sem empate", "empate anula"), ("DNB", "empate anula (DNB)"),
    ("Tempo Extra", "tempo extra"), ("lay (contra)", "lay (aposta contra)"),
    ("Mais de", "acima de"), ("Menos de", "abaixo de"),
    ("Acima", "acima de"), ("Abaixo", "abaixo de"),
    ("escanteios", "escanteios"), (" o time", ""),
]


def _mercado_legivel(code, t1="", t2=""):
    """Deixa o código de mercado do surebet mais claro (fallback quando não veio a
    descrição do balão). Conservador: prefixa o TIME certo e traduz termos conhecidos,
    sem inventar semântica. O código técnico continua visível no painel."""
    if not code:
        return ""
    partes = code.strip().split()
    lado = partes[0].lower() if partes else ""
    nome = {
        "1": t1 or "Casa", "2": t2 or "Fora", "x": "Empate",
        "1x": f"{t1 or 'Casa'} ou empate", "12": f"{t1 or 'Casa'} ou {t2 or 'Fora'}",
        "x2": f"empate ou {t2 or 'Fora'}",
    }.get(lado)
    resto = " ".join(partes[1:]) if (nome and len(partes) > 1) else ("" if nome else code)
    for a, b in _MKT_SUBS:
        resto = resto.replace(a, b)
    resto = resto.replace(" - ", " · ").replace(" / ", " ").strip(" ·-/")
    while "  " in resto:
        resto = resto.replace("  ", " ")
    if nome:
        return f"{nome} — {resto}".strip(" —") if resto else nome
    return resto


def _converter_raspagem(records):
    """Converte os registros raspados do DOM da surebet.com no contrato do painel."""
    contratos = []
    for r in records:
        legs = r.get("legs", [])
        if len(legs) != 2:
            continue
        try:
            odds = [float(l["odd"]) for l in legs]
        except (KeyError, TypeError, ValueError):
            continue
        if any(o <= 1 for o in odds):
            continue
        prof = round(float(r.get("profit", 0)), 2)
        if not (0 < prof <= config.MAX_LUCRO_SANO):
            continue   # descarta anomalias (escanteios bugados de 30-400%)
        banca = config.BANCA
        margem = sum(1.0 / o for o in odds)
        teams = max((l.get("teams", "") for l in legs), key=len)
        t1, t2 = _split_teams(teams)
        pernas = []
        for l, o in zip(legs, odds):
            stake = banca * (1.0 / o) / margem
            code = l.get("market", "")
            # descrição legível: usa o balão do surebet se veio; senão traduz o código
            desc = (l.get("desc") or "").strip() or _mercado_legivel(code, t1, t2)
            pernas.append({
                "outcome": code,
                "desc": desc,
                "odd": round(o, 3),
                "bookmaker": _slug(l.get("bookmaker", "")),
                "bookmaker_label": l.get("bookmaker", ""),
                "bookmaker_type": _tipo_casa(l.get("bookmaker", "")),
                "stake_pct": round(stake / banca * 100, 1),
                "stake_brl": round(stake, 2),
                "link": _link_casa(l.get("link")),   # só link da casa; nunca surebet.com
            })
        sport = _norm_sport(legs[0].get("sport", ""))
        if sport == "?":   # raspagem sem esporte: tenta deduzir pelo mercado
            sport = _inferir_sport([l.get("market", "") for l in legs]) or "?"
        start = r.get("start")
        iso = (datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
               if start else None)
        retorno = banca / margem
        contratos.append({
            "id": r.get("id") or _slug(teams),
            "event": teams,
            "sport": sport,
            "sport_label": legs[0].get("champ", "") or SPORT_LABELS_PT.get(sport, sport),
            "market": "raspagem",
            "market_label": _mercado_completo(legs[0].get("market", ""), sport),
            "line": None,
            "profit_pct": prof,
            "banca": banca,
            "commence_utc": iso,
            "commence_br": pipeline._horario_brasilia(iso),
            "lucro_brl": round(retorno - banca, 2),
            "updated_at": pipeline._agora_iso(),
            "legs": pernas,
        })
    return contratos


@app.get("/api/valuebets")
def valuebets(request: Request):
    """Odds de valor pro painel. As odds são SEMPRE de verdade — inclusive pra quem
    não comprou o add-on: ele vê uma AMOSTRA real (as N mais valiosas) e o painel
    borra parte pra dar vontade de liberar o resto. Quem tem o add-on recebe TODAS,
    com os links diretos das entradas. ISOLADO da surebet."""
    user = _usuario(request)
    tem = _valor_liberado(user)
    itens = valor_feed.get_valuebets()
    if tem:
        return {"itens": itens, "tem": True}
    # Sem add-on: manda só uma amostra real (as de maior % acima do justo), pra não
    # entregar o feed inteiro de graça. Sem o link direto — o atalho da entrada é do
    # pagante; a amostra ainda funciona pelo botão "IR PRA CASA" (site da casa).
    amostra = sorted(itens, key=lambda i: i.get("valor", 0), reverse=True)[:config.VALOR_AMOSTRA_FREE]
    amostra = [{**i, "link": None} for i in amostra]
    return {"itens": amostra, "tem": False}


@app.post("/api/ingest-valor")
def ingest_valor(request: Request, payload: dict = Body(...)):
    """Recebe as ODDS DE VALOR raspadas (via navegador/robô) e guarda no armazém
    próprio (valor_feed). TOTALMENTE separado do /api/ingest da surebet — se algo
    aqui der erro, a surebet continua intacta. Mesma proteção de token do ingest."""
    if config.INGEST_TOKEN:
        enviado = request.headers.get("x-ingest-token") or payload.get("token", "")
        if enviado != config.INGEST_TOKEN:
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
    itens = []
    for r in (payload.get("records") or []):
        try:
            odd = float(r.get("odd") or 0)
            valor = float(r.get("valor") or 0)
            if odd <= 1 or valor <= 0:
                continue
            prob = float(r.get("probabilidade") or 0)
            # odd justa = 1/prob real (a prob vem das casas fortes, referência)
            justa = round(100.0 / prob, 2) if prob > 0 else round(float(r.get("justa") or 0), 2)
            itens.append({
                "ico": r.get("ico") or _sport_ico(r.get("esporte", "")),
                "esporte": r.get("esporte") or "",
                "hora": r.get("hora") or _hora_br(r.get("start")),
                "event": (r.get("event") or "").strip(),
                "mercado": (r.get("mercado") or "").strip(),
                "casa": (r.get("casa") or "").strip(),
                "odd": round(odd, 2),
                "valor": round(valor, 1),
                "justa": justa,
                # chance real do lance (%) — é a referência das casas fortes; o
                # painel mostra pro cliente entender de onde sai a "odd justa"
                "prob": round(prob if prob > 0 else (100.0 / justa if justa > 0 else 0), 1),
                "stake": r.get("stake") or 2,
                "link": _link_casa(r.get("link")),
            })
        except (TypeError, ValueError):
            continue
    valor_feed.set_valuebets(itens)
    return {"ok": True, "recebidos": len(itens)}


def _hora_br(ts):
    """Unix -> 'dd/mm HH:MM' no horário de Brasília (pra mostrar no card)."""
    try:
        ts = int(ts)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts, tz=_BR_TZ).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _sport_ico(s):
    s = (s or "").lower()
    if "ten" in s:
        return "🎾"
    if "basq" in s or "basket" in s:
        return "🏀"
    if "vol" in s or "vôlei" in s:
        return "🏐"
    return "⚽"


@app.post("/api/ingest")
def ingest(request: Request, payload: dict = Body(...)):
    """Recebe surebets raspadas da conta (via navegador) e publica no painel.

    Os filtros do painel (casas, esportes, lucro) passam a ESPELHAR o que veio
    na raspagem — nada de casas/esportes que não estão na sua conta.
    """
    # Se INGEST_TOKEN estiver setado, exige o mesmo token (header ou body).
    if config.INGEST_TOKEN:
        enviado = request.headers.get("x-ingest-token") or payload.get("token", "")
        if enviado != config.INGEST_TOKEN:
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
    global INGESTED_BOOKS, INGESTED_SPORTS, INGESTED_PROFIT
    contratos = _converter_raspagem(payload.get("records", []))
    quando = pipeline._agora_iso() + " (conta)"
    # SNAPSHOT (raspagem completa): SUBSTITUI o feed — o que saiu da conta sai do
    # site, o que ficou permanece. MERGE (parcial/backup): só soma, não remove.
    if payload.get("modo") == "snapshot":
        feed.set_surebets(contratos, quando=quando)
    else:
        feed.merge_surebets(contratos, quando=quando)
    if contratos:
        feed.marcar_ingest()

    # Casas / esportes / faixa de lucro ESPELHAM TODO o feed vivo (não só este
    # ingest, que é parcial).
    todos = feed.get_surebets()
    _recalcular_filtros(todos)

    # persiste o feed pra sobreviver a redeploys
    try:
        auth.feed_cache_set(todos)
    except Exception:
        pass

    return {"ingeridas": len(contratos), "no_feed": len(todos),
            "casas": len(INGESTED_BOOKS), "esportes": len(INGESTED_SPORTS)}


SPORT_LABELS_PT = {
    "Football": "Futebol", "Tennis": "Tênis", "Basketball": "Basquete",
    "Volleyball": "Vôlei", "TableTennis": "Tênis de Mesa", "Hockey": "Hóquei",
    "Handball": "Handebol", "Baseball": "Beisebol",
}


@app.get("/api/meta")
def meta():
    """Opções dos filtros: ESPORTES, CASAS e LUCRO — espelham a fonte/raspagem."""
    if INGESTED_SPORTS:                       # raspagem manda: só os esportes raspados
        sports = INGESTED_SPORTS
    else:
        keys = config.SUREBET_SPORTS if config.FONTE_DADOS == "surebet" else []
        sports = [{"key": s, "label": SPORT_LABELS_PT.get(s, s)} for s in keys]
    return {
        "sports": sports,
        "bookmakers": _casas_do_filtro(),
        "profit": INGESTED_PROFIT or None,
        "refresh_seg": config.DASHBOARD_REFRESH_SEG,
        "status": feed.status(),
    }


@app.get("/api/surebets")
def surebets(
    request: Request,
    min_profit: float = Query(0.0, description="lucro mínimo em %"),
    max_profit: float = Query(0.0, description="lucro máximo em % (0 = sem teto)"),
    bookmakers: str = Query("", description="IDs de casas separados por vírgula"),
    sports: str = Query("", description="IDs de esporte separados por vírgula"),
):
    """Aplica os filtros (esporte + casas + lucro) e devolve as surebets."""
    def parse(csv):
        vals = [x.strip() for x in csv.split(",") if x.strip()]
        return vals or None

    # Regra de plano (trava no SERVIDOR):
    #   FREE -> só uma AMOSTRA de entradas de até 1% (as N mais próximas de 1%).
    #   PRO  -> todas as entradas ACIMA de 1% (as que valem a pena de verdade).
    user = _usuario(request)
    is_free = _plano_efetivo(user) == "free"
    teto = max_profit if max_profit > 0 else None
    casas, esportes = parse(bookmakers), parse(sports)

    if is_free:
        # Até 1% (respeitando um teto menor, se o usuário pediu), ordenadas por
        # lucro desc; mostra só as N primeiras (mais próximas de 1%).
        teto_free = min(teto, 1.0) if teto is not None else 1.0
        resultados = feed.get_surebets(
            min_profit=min_profit, max_profit=teto_free,
            bookmakers=casas, sports=esportes,
        )[: config.FREE_MAX_ENTRADAS]
    else:
        # PRO: piso de 1% (nunca abaixo), mas respeita um mínimo maior do filtro.
        piso = max(min_profit, config.PRO_LUCRO_MIN)
        resultados = feed.get_surebets(
            min_profit=piso, max_profit=teto,
            bookmakers=casas, sports=esportes,
        )

    # Teasers para o FREE: as ENTRADAS REAIS de alto lucro (>1%) que ele NÃO vê.
    # Mostradas borradas no painel para dar vontade de assinar. Sem o link
    # (não dá pra executar sem o Pro), mas com o valor real do lucro.
    locked = []
    if is_free:
        altas = feed.get_surebets(min_profit=config.PRO_LUCRO_MIN,
                                  bookmakers=casas, sports=esportes)
        for s in altas[:6]:
            c = dict(s)
            c["legs"] = [{**l, "link": None} for l in s["legs"]]
            locked.append(c)

    return {
        "surebets": _sem_link_surebet(resultados),   # blindagem: nunca vaza link do surebet.com
        "locked": locked,
        "status": feed.status(),
        "plano": _plano_efetivo(user),
    }


# --- SEO / ícones ---
@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/google86846e3b0041cec2.html")
def google_site_verification():
    """Verificação de posse do site no Google Search Console (método Arquivo HTML)."""
    return Response("google-site-verification: google86846e3b0041cec2.html",
                    media_type="text/html")


@app.get("/robots.txt")
def robots():
    txt = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /app\n"
        "Disallow: /perfil\n"
        "Disallow: /login\n"
        "Disallow: /cadastro\n"
        "Sitemap: https://sureradar.site/sitemap.xml\n"
    )
    return Response(txt, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    urls = ['<url><loc>https://sureradar.site/</loc>'
            '<changefreq>daily</changefreq><priority>1.0</priority></url>',
            '<url><loc>https://sureradar.site/grupo</loc>'
            '<changefreq>weekly</changefreq><priority>0.7</priority></url>']
    try:
        for p in auth.listar_paginas():
            if p["publicado"]:
                prio = "0.9" if p["slug"] == "calculadora" else "0.8"
                urls.append(f'<url><loc>https://sureradar.site/{p["slug"]}</loc>'
                            f'<changefreq>weekly</changefreq><priority>{prio}</priority></url>')
    except Exception:
        pass
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + '</urlset>')
    return Response(xml, media_type="application/xml")


# Páginas. Montadas por último para não engolir as rotas /api.
@app.get("/")
def landing():
    """Página de vendas (landing)."""
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/login")
@app.get("/cadastro")
def tela_auth():
    """Tela de login/cadastro (a mesma página alterna os dois modos)."""
    return FileResponse(STATIC_DIR / "auth.html")


@app.get("/redefinir")
def tela_redefinir():
    """Página para criar uma senha nova (chega pelo link do e-mail)."""
    return FileResponse(STATIC_DIR / "redefinir.html")


@app.get("/calculadora")
def tela_calculadora(request: Request):
    """Página PÚBLICA da calculadora (SEO: 'calculadora surebet'). Funciona pra todo
    mundo, sem login, e aceita odds pela URL (?o1=..&o2=..) vindas do Telegram."""
    return FileResponse(STATIC_DIR / "calculadora.html")


def _seo_page(slug, arquivo):
    """Serve uma página de SEO só se ela estiver PUBLICADA (controlado no /admin).
    Rascunho ainda não publicado responde 404 (não vaza pro Google antes da hora)."""
    if not auth.pagina_publicada(slug):
        return Response("Página não encontrada.", status_code=404, media_type="text/html")
    return FileResponse(STATIC_DIR / arquivo)


@app.get("/o-que-e-surebet")
def artigo_oque():
    return _seo_page("o-que-e-surebet", "o-que-e-surebet.html")


@app.get("/arbitragem-esportiva")
def artigo_arbitragem():
    return _seo_page("arbitragem-esportiva", "arbitragem-esportiva.html")


@app.get("/aposta-segura")
def artigo_aposta_segura():
    return _seo_page("aposta-segura", "aposta-segura.html")


@app.get("/como-fazer-surebet")
def artigo_como_fazer():
    return _seo_page("como-fazer-surebet", "como-fazer-surebet.html")


@app.get("/melhores-casas-de-aposta")
def artigo_casas():
    return _seo_page("melhores-casas-de-aposta", "melhores-casas-de-aposta.html")


@app.get("/software-surebet")
def artigo_software():
    return _seo_page("software-surebet", "software-surebet.html")


@app.get("/grupo")
@app.get("/free")
@app.get("/telegram")
@app.get("/oportunidade")
@app.get("/metodo")
def tela_grupo():
    """Landing do grupo — versão NEUTRA (sem termos de aposta) p/ passar na revisão
    do Facebook. Todos os apelidos (/grupo, /oportunidade, etc.) servem a mesma."""
    return FileResponse(STATIC_DIR / "oportunidade.html")


@app.get("/nao-ser-limitado")
@app.get("/limitacoes")
@app.get("/aprenda/limitacoes")
def tela_limitacoes():
    """Guia: por que as casas limitam e como durar mais (conteúdo + SEO + marketing)."""
    return FileResponse(STATIC_DIR / "limitacoes.html")


@app.get("/termos")
def tela_termos():
    return FileResponse(STATIC_DIR / "termos.html")


@app.get("/privacidade")
def tela_privacidade():
    return FileResponse(STATIC_DIR / "privacidade.html")


@app.get("/perfil")
def tela_perfil(request: Request):
    if not _usuario(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "perfil.html")


@app.get("/planos")
def tela_planos(request: Request):
    if not _usuario(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "planos.html")


@app.get("/admin")
def tela_admin(request: Request):
    """Dashboard admin. Gated por IP (allowlist) + e-mail + senha."""
    # IP fora da allowlist: finge que a página não existe (404).
    if not _ip_admin_ok(request):
        return Response("Not Found", status_code=404)
    user = _usuario(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not _admin_email(user):
        return RedirectResponse("/app", status_code=302)
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/app")
def dashboard(request: Request):
    """O painel — só para quem está logado."""
    if not _usuario(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import os
    import uvicorn
    # Em produção o host injeta a porta em $PORT e escuta em 0.0.0.0.
    porta = int(os.getenv("PORT", "8000"))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    uvicorn.run("app:app", host=host, port=porta, reload=False)
