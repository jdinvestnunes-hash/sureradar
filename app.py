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

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Mostra os logs do agendador em tempo real (evita buffer em background).
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

import re
from datetime import datetime, timezone

from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import config
import feed
import pipeline

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app):
    """Ao subir o servidor, prepara o banco de usuários e liga o agendador."""
    auth.init()
    pipeline.iniciar_agendador()
    yield
    pipeline.parar_agendador()


app = FastAPI(title="Surebet SaaS", version="0.1.0", lifespan=lifespan)

# Libera o navegador (extensão) a enviar surebets raspadas para /api/ingest.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def _private_network(request, call_next):
    """Acrescenta o header de Private Network Access do Chrome a toda resposta
    (o preflight OPTIONS é montado pelo CORSMiddleware; aqui só anexamos o PNA)."""
    resp = await call_next(request)
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp

# Filtros descobertos via ingestão (raspagem da conta). Espelham a surebet.com.
INGESTED_BOOKS = []
INGESTED_SPORTS = []
INGESTED_PROFIT = {}


# ---------------------------------------------------------------------------
# AUTENTICAÇÃO (login / cadastro / sessão por cookie)
# ---------------------------------------------------------------------------
COOKIE = "sr_session"


def _usuario(request: Request):
    """Usuário logado (ou None), a partir do cookie de sessão."""
    return auth.usuario_da_sessao(request.cookies.get(COOKIE))


def _com_sessao(resp: Response, user_id: int):
    token = auth.criar_sessao(user_id)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSAO_MAX_S, path="/")
    return resp


@app.post("/api/register")
def register(payload: dict = Body(...)):
    user, erro = auth.criar_usuario(
        payload.get("nome", ""), payload.get("email", ""), payload.get("senha", ""))
    if erro:
        return JSONResponse({"erro": erro}, status_code=400)
    resp = JSONResponse({"ok": True, "user": user})
    return _com_sessao(resp, user["id"])


@app.post("/api/login")
def login(payload: dict = Body(...)):
    user = auth.autenticar(payload.get("email", ""), payload.get("senha", ""))
    if not user:
        return JSONResponse({"erro": "E-mail ou senha incorretos."}, status_code=401)
    resp = JSONResponse({"ok": True, "user": user})
    return _com_sessao(resp, user["id"])


@app.post("/api/logout")
def logout(request: Request):
    auth.encerrar_sessao(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


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
def google_callback(request: Request, code: str = "", state: str = ""):
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
    user = auth.pegar_ou_criar_google(email, info.get("name", ""))
    resp = RedirectResponse("/app", status_code=302)
    resp.delete_cookie("g_state", path="/")
    return _com_sessao(resp, user["id"])


@app.get("/api/me")
def me(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    return {"nome": user["nome"], "email": user["email"], "plano": user["plano"],
            "dias": auth.dias_restantes(user)}


@app.get("/api/perfil")
def perfil_dados(request: Request):
    user = _usuario(request)
    if not user:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    return {
        "nome": user["nome"], "email": user["email"], "plano": user["plano"],
        "dias": auth.dias_restantes(user),
        "expira": user.get("plano_expira"),
        "pagamentos": auth.listar_pagamentos(user["id"]),
    }


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
        banca = config.BANCA
        margem = sum(1.0 / o for o in odds)
        pernas = []
        for l, o in zip(legs, odds):
            stake = banca * (1.0 / o) / margem
            pernas.append({
                "outcome": l.get("market", ""),
                "odd": round(o, 3),
                "bookmaker": _slug(l.get("bookmaker", "")),
                "bookmaker_label": l.get("bookmaker", ""),
                "bookmaker_type": _tipo_casa(l.get("bookmaker", "")),
                "stake_pct": round(stake / banca * 100, 1),
                "stake_brl": round(stake, 2),
                "link": l.get("link"),   # link que abre a casa (redirect da surebet)
            })
        teams = max((l.get("teams", "") for l in legs), key=len)
        sport = _norm_sport(legs[0].get("sport", ""))
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
            "market_label": legs[0].get("market", ""),
            "line": None,
            "profit_pct": round(float(r.get("profit", 0)), 2),
            "banca": banca,
            "commence_utc": iso,
            "commence_br": pipeline._horario_brasilia(iso),
            "lucro_brl": round(retorno - banca, 2),
            "updated_at": pipeline._agora_iso(),
            "legs": pernas,
        })
    return contratos


@app.post("/api/ingest")
def ingest(payload: dict = Body(...)):
    """Recebe surebets raspadas da conta (via navegador) e publica no painel.

    Os filtros do painel (casas, esportes, lucro) passam a ESPELHAR o que veio
    na raspagem — nada de casas/esportes que não estão na sua conta.
    """
    global INGESTED_BOOKS, INGESTED_SPORTS, INGESTED_PROFIT
    contratos = _converter_raspagem(payload.get("records", []))
    feed.set_surebets(contratos, quando=pipeline._agora_iso() + " (conta)")

    # Casas = as que apareceram nas apostas raspadas.
    casas = {}
    esportes = {}
    for c in contratos:
        esportes[c["sport"]] = SPORT_LABELS_PT.get(c["sport"], c["sport"])
        for l in c["legs"]:
            casas[l["bookmaker"]] = l["bookmaker_label"]
    INGESTED_BOOKS = [{"key": k, "label": v} for k, v in sorted(casas.items(), key=lambda x: x[1].lower())]
    # Futebol SEMPRE primeiro; depois em ordem alfabética.
    INGESTED_SPORTS = [{"key": k, "label": v} for k, v in
                       sorted(esportes.items(), key=lambda x: (x[0] != "Football", x[1]))]

    # Faixa de lucro (se o navegador mandar; senão deriva do min/max das apostas).
    prof = payload.get("profit") or {}
    if prof.get("min") is not None or prof.get("max") is not None:
        INGESTED_PROFIT = {"min": prof.get("min", 0), "max": prof.get("max", 0)}
    elif contratos:
        vals = [c["profit_pct"] for c in contratos]
        INGESTED_PROFIT = {"min": 0, "max": round(max(vals) + 0.5, 1)}

    return {"ingeridas": len(contratos), "casas": len(INGESTED_BOOKS), "esportes": len(INGESTED_SPORTS)}


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

    # Regra de plano: conta FREE só vê entradas de até 1% (trava no SERVIDOR).
    user = _usuario(request)
    is_free = (not user) or user["plano"] == "free"
    teto = max_profit if max_profit > 0 else None
    if is_free:
        teto = min(teto, 1.0) if teto is not None else 1.0

    resultados = feed.get_surebets(
        min_profit=min_profit,
        max_profit=teto,
        bookmakers=parse(bookmakers),
        sports=parse(sports),
    )

    # Teasers para o FREE: as ENTRADAS REAIS de alto lucro (>1%) que ele NÃO vê.
    # Mostradas borradas no painel para dar vontade de assinar. Sem o link
    # (não dá pra executar sem o Pro), mas com o valor real do lucro.
    locked = []
    if is_free:
        altas = feed.get_surebets(min_profit=1.0001,
                                  bookmakers=parse(bookmakers), sports=parse(sports))
        for s in altas[:6]:
            c = dict(s)
            c["legs"] = [{**l, "link": None} for l in s["legs"]]
            locked.append(c)

    return {
        "surebets": resultados,
        "locked": locked,
        "status": feed.status(),
        "plano": user["plano"] if user else "free",
    }


# --- SEO / ícones ---
@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


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
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           '<url><loc>https://sureradar.site/</loc>'
           '<changefreq>daily</changefreq><priority>1.0</priority></url>'
           '</urlset>')
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


@app.get("/perfil")
def tela_perfil(request: Request):
    if not _usuario(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "perfil.html")


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
