"""
auth.py — usuários, sessões e pagamentos do SureRadar.

Banco de dados: usa POSTGRES (Supabase) se a variável de ambiente DATABASE_URL
estiver definida; senão cai no SQLite local (bom para desenvolvimento).

- Senhas com PBKDF2-HMAC-SHA256 + salt (nunca em texto puro).
- Sessão por token aleatório guardado em cookie httponly.
- Plano do usuário: "free" (entradas até 1%) ou "pro" (tudo).
"""

import hashlib
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SESSAO_MAX_S = 30 * 24 * 3600  # 30 dias

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PG = DATABASE_URL.startswith("postgres")

if PG:
    import psycopg
    from psycopg.rows import dict_row

    def _conn():
        # prepare_threshold=None -> compatível com o pooler de transação do Supabase.
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)

    UNIQUE_ERR = psycopg.errors.UniqueViolation
    _SERIAL = "BIGSERIAL PRIMARY KEY"
    _BIN = "BYTEA"
    _NUM = "DOUBLE PRECISION"
else:
    import sqlite3

    DB = Path(__file__).parent / "sureradar.db"

    def _conn():
        c = sqlite3.connect(DB, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    UNIQUE_ERR = sqlite3.IntegrityError
    _SERIAL = "INTEGER PRIMARY KEY AUTOINCREMENT"
    _BIN = "BLOB"
    _NUM = "REAL"


def _q(sql):
    """Converte placeholders '?' para o estilo do Postgres ('%s') quando preciso."""
    return sql.replace("?", "%s") if PG else sql


# Reúso de conexão por thread — evita reabrir TLS com o Supabase a cada request
# (era a maior fonte de lentidão). Cada thread do FastAPI guarda a sua conexão.
_local = threading.local()


def _get_conn():
    c = getattr(_local, "conn", None)
    if c is not None and PG:
        try:
            if c.closed or c.broken:
                c = None
        except Exception:
            c = None
    if c is None:
        c = _conn()
        _local.conn = c
    return c


@contextmanager
def _db():
    c = _get_conn()
    try:
        yield c
        c.commit()
    except Exception:
        # conexão pode ter quebrado: descarta pra reconectar na próxima
        try:
            c.rollback()
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
        _local.conn = None
        raise
    # mantém a conexão aberta para reúso (NÃO fecha aqui)


def _insert(c, sql, params):
    """INSERT que devolve o id novo (RETURNING no Postgres, lastrowid no SQLite)."""
    if PG:
        row = c.execute(_q(sql) + " RETURNING id", params).fetchone()
        return row["id"]
    return c.execute(sql, params).lastrowid


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init():
    with _db() as c:
        c.execute(f"""CREATE TABLE IF NOT EXISTS users(
            id {_SERIAL},
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            hash {_BIN} NOT NULL,
            salt {_BIN} NOT NULL,
            plano TEXT NOT NULL DEFAULT 'free',
            plano_expira {_NUM},
            criado {_NUM} NOT NULL)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            criado {_NUM} NOT NULL)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS pagamentos(
            id {_SERIAL},
            user_id BIGINT NOT NULL,
            valor {_NUM} NOT NULL,
            plano TEXT NOT NULL,
            metodo TEXT,
            criado {_NUM} NOT NULL)""")
        # Banca do usuário (entradas lançadas) — 1 linha por usuário, JSON com a
        # lista completa (espelha o formato do front; simples e suficiente).
        c.execute(f"""CREATE TABLE IF NOT EXISTS user_banca(
            user_id BIGINT PRIMARY KEY,
            dados TEXT NOT NULL,
            atualizado {_NUM} NOT NULL)""")
        # Cache do FEED (surebets ao vivo) — 1 linha (id=1). Sobrevive a redeploys
        # (o feed em memória zera; aqui a gente restaura no startup).
        c.execute(f"""CREATE TABLE IF NOT EXISTS feed_cache(
            id BIGINT PRIMARY KEY,
            dados TEXT NOT NULL,
            atualizado {_NUM} NOT NULL)""")
        if not PG:  # migração leve do SQLite antigo
            try:
                c.execute("ALTER TABLE users ADD COLUMN plano_expira REAL")
            except sqlite3.OperationalError:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash(senha: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes(salt), 120_000)


def _perfil(row):
    return {
        "id": row["id"], "nome": row["nome"], "email": row["email"],
        "plano": row["plano"], "plano_expira": row["plano_expira"],
    }


def dias_restantes(user):
    exp = user.get("plano_expira")
    if user.get("plano") == "free" or not exp:
        return None
    return max(0, int((exp - time.time()) / 86400))


def _normalizar_plano(c, row):
    """Regra automática — o BANCO é a fonte da verdade: plano 'pro' com
    expiração VENCIDA volta pra 'free' sozinho.

    (Ativar/renovar PRO com a DURAÇÃO que você escolher é pelo painel admin, via
    `ativar_pro`, que SOMA nos dias restantes ao renovar.)"""
    plano = row["plano"]
    exp = row["plano_expira"]
    if plano == "pro" and exp and exp < time.time():
        plano = "free"
        c.execute(_q("UPDATE users SET plano='free' WHERE id=?"), (row["id"],))
    return plano, exp


def pegar_por_email(email):
    email = (email or "").strip().lower()
    with _db() as c:
        row = c.execute(_q("SELECT * FROM users WHERE email=?"), (email,)).fetchone()
    return _perfil(row) if row else None


def listar_usuarios():
    """Todos os usuários (para o painel admin), do mais novo ao mais antigo."""
    with _db() as c:
        rows = c.execute(
            "SELECT id, nome, email, plano, plano_expira, criado FROM users ORDER BY criado DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def voltar_free(user_id: int):
    """Tira o PRO na marra (volta pra free, zera a expiração)."""
    with _db() as c:
        c.execute(_q("UPDATE users SET plano='free', plano_expira=NULL WHERE id=?"), (user_id,))
    limpar_cache_sessoes()


# ---------------------------------------------------------------------------
# Cache do FEED (sobrevive a redeploys)
# ---------------------------------------------------------------------------
def feed_cache_get():
    import json
    try:
        with _db() as c:
            row = c.execute(_q("SELECT dados FROM feed_cache WHERE id=1")).fetchone()
        if row:
            d = json.loads(row["dados"])
            return d if isinstance(d, list) else []
    except Exception as e:
        print("!! feed_cache_get:", e)
    return []


def feed_cache_set(bets):
    import json
    dados = json.dumps(bets or [], ensure_ascii=False)
    agora = time.time()
    try:
        with _db() as c:
            if PG:
                c.execute(_q("""INSERT INTO feed_cache(id,dados,atualizado) VALUES(1,?,?)
                               ON CONFLICT (id) DO UPDATE SET dados=EXCLUDED.dados,
                               atualizado=EXCLUDED.atualizado"""), (dados, agora))
            else:
                c.execute("INSERT OR REPLACE INTO feed_cache(id,dados,atualizado) VALUES(1,?,?)",
                          (dados, agora))
    except Exception as e:
        print("!! feed_cache_set:", e)


# ---------------------------------------------------------------------------
# Banca (entradas lançadas pelo usuário) — persistida no banco
# ---------------------------------------------------------------------------
def banca_get(user_id: int):
    import json
    with _db() as c:
        row = c.execute(_q("SELECT dados FROM user_banca WHERE user_id=?"), (user_id,)).fetchone()
    if not row:
        return []
    try:
        dados = json.loads(row["dados"])
        return dados if isinstance(dados, list) else []
    except Exception:
        return []


def banca_set(user_id: int, entradas: list):
    import json
    dados = json.dumps(entradas or [], ensure_ascii=False)
    agora = time.time()
    with _db() as c:
        if PG:
            c.execute(_q(
                """INSERT INTO user_banca(user_id, dados, atualizado) VALUES(?,?,?)
                   ON CONFLICT (user_id) DO UPDATE SET dados=EXCLUDED.dados,
                   atualizado=EXCLUDED.atualizado"""), (user_id, dados, agora))
        else:
            c.execute("INSERT OR REPLACE INTO user_banca(user_id, dados, atualizado) VALUES(?,?,?)",
                      (user_id, dados, agora))


# ---------------------------------------------------------------------------
# Cadastro / login
# ---------------------------------------------------------------------------
def criar_usuario(nome: str, email: str, senha: str):
    nome = (nome or "").strip()
    email = (email or "").strip().lower()
    if len(nome) < 2:
        return None, "Digite seu nome."
    if "@" not in email or "." not in email.split("@")[-1]:
        return None, "E-mail inválido."
    if len(senha or "") < 6:
        return None, "A senha precisa ter pelo menos 6 caracteres."
    salt = secrets.token_bytes(16)
    try:
        with _db() as c:
            uid = _insert(c,
                "INSERT INTO users(nome,email,hash,salt,plano,criado) VALUES(?,?,?,?,?,?)",
                (nome, email, _hash(senha, salt), salt, "free", time.time()))
    except UNIQUE_ERR:
        return None, "Este e-mail já tem conta. Faça login."
    return {"id": uid, "nome": nome, "email": email, "plano": "free", "plano_expira": None}, None


def pegar_ou_criar_google(email: str, nome: str):
    """Login com Google: acha o usuário pelo e-mail ou cria um novo (plano free)."""
    email = (email or "").strip().lower()
    nome = (nome or "").strip() or email.split("@")[0]
    with _db() as c:
        row = c.execute(_q("SELECT * FROM users WHERE email=?"), (email,)).fetchone()
        if row:
            return _perfil(row)
        salt = secrets.token_bytes(16)
        uid = _insert(c,
            "INSERT INTO users(nome,email,hash,salt,plano,criado) VALUES(?,?,?,?,?,?)",
            (nome, email, _hash(secrets.token_hex(24), salt), salt, "free", time.time()))
    return {"id": uid, "nome": nome, "email": email, "plano": "free", "plano_expira": None}


def autenticar(email: str, senha: str):
    email = (email or "").strip().lower()
    with _db() as c:
        row = c.execute(_q("SELECT * FROM users WHERE email=?"), (email,)).fetchone()
        if not row:
            return None
        if not secrets.compare_digest(_hash(senha or "", row["salt"]), bytes(row["hash"])):
            return None
        plano, exp = _normalizar_plano(c, row)
    return {"id": row["id"], "nome": row["nome"], "email": row["email"],
            "plano": plano, "plano_expira": exp}


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------
# Cache em memória do lookup de sessão (token -> perfil). Evita ir ao banco a
# cada request/poll (o painel consulta de 30 em 30s). TTL curto p/ refletir
# mudanças de plano rápido; limpo em logout e em ações de plano.
_sess_cache = {}
_SESS_TTL = 45
_sess_lock = threading.Lock()


def limpar_cache_sessoes():
    with _sess_lock:
        _sess_cache.clear()


def criar_sessao(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _db() as c:
        c.execute(_q("INSERT INTO sessions(token,user_id,criado) VALUES(?,?,?)"),
                  (token, user_id, time.time()))
    return token


def usuario_da_sessao(token: str):
    if not token:
        return None
    with _sess_lock:
        ent = _sess_cache.get(token)
        if ent and (time.time() - ent[1] < _SESS_TTL):
            return dict(ent[0])
    with _db() as c:
        row = c.execute(_q(
            """SELECT u.id, u.nome, u.email, u.plano, u.plano_expira, s.criado AS s_criado
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token=?"""), (token,)).fetchone()
        if not row:
            return None
        if time.time() - row["s_criado"] > SESSAO_MAX_S:
            c.execute(_q("DELETE FROM sessions WHERE token=?"), (token,))
            return None
        plano, exp = _normalizar_plano(c, row)
    perfil = {"id": row["id"], "nome": row["nome"], "email": row["email"],
              "plano": plano, "plano_expira": exp}
    with _sess_lock:
        _sess_cache[token] = (dict(perfil), time.time())
    return perfil


def encerrar_sessao(token: str):
    if token:
        with _sess_lock:
            _sess_cache.pop(token, None)
        with _db() as c:
            c.execute(_q("DELETE FROM sessions WHERE token=?"), (token,))


# ---------------------------------------------------------------------------
# Plano e pagamentos
# ---------------------------------------------------------------------------
def listar_pagamentos(user_id: int):
    with _db() as c:
        rows = c.execute(_q(
            "SELECT valor, plano, metodo, criado FROM pagamentos WHERE user_id=? ORDER BY criado DESC"),
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def ativar_pro(user_id: int, plano: str, dias: int, valor: float, metodo: str = "manual"):
    agora = time.time()
    with _db() as c:
        atual = c.execute(_q("SELECT plano_expira FROM users WHERE id=?"), (user_id,)).fetchone()
        base = max(agora, (atual["plano_expira"] or 0)) if atual else agora
        nova_exp = base + dias * 86400
        c.execute(_q("UPDATE users SET plano='pro', plano_expira=? WHERE id=?"),
                  (nova_exp, user_id))
        c.execute(_q("INSERT INTO pagamentos(user_id,valor,plano,metodo,criado) VALUES(?,?,?,?,?)"),
                  (user_id, valor, plano, metodo, agora))
    limpar_cache_sessoes()   # reflete o novo plano na hora
    return nova_exp
