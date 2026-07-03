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


@contextmanager
def _db():
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


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
    """Regras automáticas de plano — o BANCO é a fonte da verdade.

    - plano 'pro' SEM expiração (ativação manual direto no banco) -> liga 30 dias
      a partir de agora e registra um pagamento manual (aí o perfil mostra certo).
    - plano 'pro' com expiração VENCIDA -> volta pra 'free' sozinho.

    Recebe a conexão aberta `c` e a row do usuário; devolve (plano, expira) já
    aplicados (e persiste a mudança no banco quando houver)."""
    plano = row["plano"]
    exp = row["plano_expira"]
    uid = row["id"]
    agora = time.time()
    if plano == "pro" and not exp:
        exp = agora + 30 * 86400
        c.execute(_q("UPDATE users SET plano_expira=? WHERE id=?"), (exp, uid))
        c.execute(_q("INSERT INTO pagamentos(user_id,valor,plano,metodo,criado) VALUES(?,?,?,?,?)"),
                  (uid, 0.0, "pro", "ativação manual (banco)", agora))
    elif plano == "pro" and exp and exp < agora:
        plano = "free"
        c.execute(_q("UPDATE users SET plano='free' WHERE id=?"), (uid,))
    return plano, exp


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
def criar_sessao(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _db() as c:
        c.execute(_q("INSERT INTO sessions(token,user_id,criado) VALUES(?,?,?)"),
                  (token, user_id, time.time()))
    return token


def usuario_da_sessao(token: str):
    if not token:
        return None
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
    return {"id": row["id"], "nome": row["nome"], "email": row["email"],
            "plano": plano, "plano_expira": exp}


def encerrar_sessao(token: str):
    if token:
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
    return nova_exp
