"""
auth.py — usuários e sessões do SureRadar (SQLite, stdlib apenas).

- Senhas com PBKDF2-HMAC-SHA256 + salt (nunca em texto puro).
- Sessão por token aleatório guardado em cookie httponly.
- Plano do usuário: "free" (entradas até 1%) ou "pro" (tudo).
"""

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).parent / "sureradar.db"
SESSAO_MAX_S = 30 * 24 * 3600  # 30 dias


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            hash BLOB NOT NULL,
            salt BLOB NOT NULL,
            plano TEXT NOT NULL DEFAULT 'free',
            plano_expira REAL,
            criado REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            criado REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS pagamentos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            valor REAL NOT NULL,
            plano TEXT NOT NULL,
            metodo TEXT,
            criado REAL NOT NULL)""")
        # migração leve: adiciona a coluna se o banco for antigo
        try:
            c.execute("ALTER TABLE users ADD COLUMN plano_expira REAL")
        except sqlite3.OperationalError:
            pass


def dias_restantes(user):
    """Dias que faltam no plano pago (None se free ou sem expiração)."""
    exp = user.get("plano_expira")
    if user.get("plano") == "free" or not exp:
        return None
    return max(0, int((exp - time.time()) / 86400))


def _perfil(row):
    return {
        "id": row["id"], "nome": row["nome"], "email": row["email"],
        "plano": row["plano"], "plano_expira": row["plano_expira"],
    }


def _hash(senha: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, 120_000)


def criar_usuario(nome: str, email: str, senha: str):
    """Retorna (user_dict, None) ou (None, mensagem_de_erro)."""
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
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users(nome,email,hash,salt,plano,criado) VALUES(?,?,?,?,?,?)",
                (nome, email, _hash(senha, salt), salt, "free", time.time()),
            )
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return None, "Este e-mail já tem conta. Faça login."
    return {"id": uid, "nome": nome, "email": email, "plano": "free", "plano_expira": None}, None


def autenticar(email: str, senha: str):
    """Retorna user_dict ou None."""
    email = (email or "").strip().lower()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        return None
    if not secrets.compare_digest(_hash(senha or "", row["salt"]), row["hash"]):
        return None
    return _perfil(row)


def criar_sessao(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id,criado) VALUES(?,?,?)",
                  (token, user_id, time.time()))
    return token


def usuario_da_sessao(token: str):
    """Valida o token e retorna o user_dict, ou None."""
    if not token:
        return None
    with _conn() as c:
        row = c.execute(
            """SELECT u.id, u.nome, u.email, u.plano, u.plano_expira, s.criado AS s_criado
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token=?""", (token,)).fetchone()
        if not row:
            return None
        if time.time() - row["s_criado"] > SESSAO_MAX_S:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
    return _perfil(row)


def encerrar_sessao(token: str):
    if token:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ---------------------------------------------------------------------------
# Plano e pagamentos
# ---------------------------------------------------------------------------
def listar_pagamentos(user_id: int):
    with _conn() as c:
        rows = c.execute(
            "SELECT valor, plano, metodo, criado FROM pagamentos WHERE user_id=? ORDER BY criado DESC",
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def ativar_pro(user_id: int, plano: str, dias: int, valor: float, metodo: str = "manual"):
    """Ativa/renova o Pro por N dias e registra o pagamento. Usado pelo checkout."""
    agora = time.time()
    with _conn() as c:
        atual = c.execute("SELECT plano_expira FROM users WHERE id=?", (user_id,)).fetchone()
        base = max(agora, (atual["plano_expira"] or 0)) if atual else agora
        nova_exp = base + dias * 86400
        c.execute("UPDATE users SET plano='pro', plano_expira=? WHERE id=?", (nova_exp, user_id))
        c.execute("INSERT INTO pagamentos(user_id,valor,plano,metodo,criado) VALUES(?,?,?,?,?)",
                  (user_id, valor, plano, metodo, agora))
    return nova_exp
