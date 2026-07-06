"""
auth.py — usuários, sessões e pagamentos do SureRadar.

Banco de dados: usa POSTGRES (Supabase) se a variável de ambiente DATABASE_URL
estiver definida; senão cai no SQLite local (bom para desenvolvimento).

- Senhas com PBKDF2-HMAC-SHA256 + salt (nunca em texto puro).
- Sessão por token aleatório guardado em cookie httponly.
- Plano do usuário: "free" (entradas até 1%) ou "pro" (tudo).
"""

import hashlib
import math
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
            whatsapp TEXT,
            hash {_BIN} NOT NULL,
            salt {_BIN} NOT NULL,
            plano TEXT NOT NULL DEFAULT 'free',
            plano_expira {_NUM},
            email_verificado {_NUM} NOT NULL DEFAULT 1,
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
        # Checkouts iniciados (Stripe/AbacatePay) — o webhook confirma o pagamento
        # pelo external_id e ativa o PRO. Guarda o que ativar (plano/dias/valor).
        c.execute(f"""CREATE TABLE IF NOT EXISTS checkouts(
            id {_SERIAL},
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            plano TEXT NOT NULL,
            dias BIGINT NOT NULL,
            valor {_NUM} NOT NULL,
            metodo TEXT NOT NULL,
            status TEXT NOT NULL,
            pi TEXT,
            criado {_NUM} NOT NULL)""")
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_checkout_ext ON checkouts(provider, external_id)")
        except Exception:
            pass
        # Assinaturas recorrentes (cartão/Stripe): mapeia sub_id/customer -> usuário
        # p/ processar renovações (invoice.paid) e cancelamentos.
        c.execute(f"""CREATE TABLE IF NOT EXISTS assinaturas(
            id {_SERIAL},
            user_id BIGINT NOT NULL,
            provider TEXT NOT NULL,
            sub_id TEXT NOT NULL,
            customer_id TEXT,
            plano TEXT NOT NULL,
            dias BIGINT NOT NULL,
            valor {_NUM} NOT NULL,
            status TEXT NOT NULL,
            criado {_NUM} NOT NULL)""")
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_assinatura_sub ON assinaturas(sub_id)")
        except Exception:
            pass
        # Tokens de redefinição de senha (recuperar senha por e-mail).
        c.execute(f"""CREATE TABLE IF NOT EXISTS reset_tokens(
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            expira {_NUM} NOT NULL,
            usado {_NUM} NOT NULL DEFAULT 0)""")
        # Tokens de confirmação de e-mail (cadastro só libera após confirmar).
        c.execute(f"""CREATE TABLE IF NOT EXISTS confirm_tokens(
            token TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            expira {_NUM} NOT NULL,
            usado {_NUM} NOT NULL DEFAULT 0)""")
        # Log do fluxo de e-mails (1 linha por user+tipo) — evita reenviar.
        c.execute(f"""CREATE TABLE IF NOT EXISTS email_enviados(
            user_id BIGINT NOT NULL,
            tipo TEXT NOT NULL,
            criado {_NUM} NOT NULL)""")
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_email_env ON email_enviados(user_id, tipo)")
        except Exception:
            pass
        # Tickets de suporte (usuário abre no perfil, admin responde no painel).
        c.execute(f"""CREATE TABLE IF NOT EXISTS tickets(
            id {_SERIAL},
            user_id BIGINT NOT NULL,
            mensagem TEXT NOT NULL,
            resposta TEXT,
            status TEXT NOT NULL,
            criado {_NUM} NOT NULL,
            respondido {_NUM})""")
        # Mensagens dos tickets (conversa ida-e-volta: autor='user'|'admin').
        c.execute(f"""CREATE TABLE IF NOT EXISTS ticket_msgs(
            id {_SERIAL},
            ticket_id BIGINT NOT NULL,
            autor TEXT NOT NULL,
            texto TEXT NOT NULL,
            criado {_NUM} NOT NULL)""")
        # Entradas já postadas no grupo (persiste p/ NUNCA repetir, mesmo após redeploy).
        c.execute(f"""CREATE TABLE IF NOT EXISTS posts_grupo(
            post_id TEXT PRIMARY KEY,
            criado {_NUM} NOT NULL)""")
        # Resumo diário do canal (nº de entradas + lucro somado) p/ o "boa noite".
        c.execute(f"""CREATE TABLE IF NOT EXISTS dia_resumo(
            dia TEXT PRIMARY KEY,
            entradas {_NUM} NOT NULL DEFAULT 0,
            lucro {_NUM} NOT NULL DEFAULT 0)""")
        # Campanhas de tráfego: cada uma tem um link de convite do Telegram e conta
        # quantos MEMBROS entraram por ele (o bot atribui o join ao link usado).
        c.execute(f"""CREATE TABLE IF NOT EXISTS campanhas(
            id {_SERIAL},
            nome TEXT NOT NULL,
            invite_link TEXT,
            membros {_NUM} NOT NULL DEFAULT 0,
            criado {_NUM} NOT NULL)""")
        try:
            c.execute("CREATE INDEX IF NOT EXISTS ix_campanha_link ON campanhas(invite_link)")
        except Exception:
            pass
    # Migrações leves para bancos antigos: cada ALTER na SUA transação (no
    # Postgres, um erro aborta a transação inteira). Erro = coluna já existe.
    for tabela, coluna in [("checkouts", "pi TEXT"),
                           ("users", f"plano_expira {_NUM}"),
                           ("users", "whatsapp TEXT"),
                           ("users", f"email_verificado {_NUM} DEFAULT 1"),
                           ("users", f"email_optout {_NUM} DEFAULT 0"),
                           ("users", "unsub_token TEXT"),
                           ("users", "origem TEXT")]:
        try:
            with _db() as c:
                c.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna}")
        except Exception:
            pass
    # Migra tickets antigos (mensagem/resposta -> conversa em ticket_msgs) + status.
    try:
        with _db() as c:
            c.execute("UPDATE tickets SET status='aguardando' WHERE status='aberto'")
            legacy = c.execute("""SELECT id, mensagem, resposta, criado, respondido FROM tickets t
                WHERE NOT EXISTS (SELECT 1 FROM ticket_msgs m WHERE m.ticket_id=t.id)""").fetchall()
            for t in legacy:
                if t["mensagem"]:
                    c.execute(_q("INSERT INTO ticket_msgs(ticket_id,autor,texto,criado) VALUES(?,?,?,?)"),
                              (t["id"], "user", t["mensagem"], t["criado"]))
                if t["resposta"]:
                    c.execute(_q("INSERT INTO ticket_msgs(ticket_id,autor,texto,criado) VALUES(?,?,?,?)"),
                              (t["id"], "admin", t["resposta"], t["respondido"] or t["criado"]))
    except Exception as e:
        print("!! migracao tickets:", e)


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
    # ARREDONDA PRA CIMA: quem paga 30 dias vê 30 (não 29 só porque já passou
    # parte de hoje). No último dia mostra "1 dia" até vencer de verdade.
    return max(0, math.ceil((exp - time.time()) / 86400))


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


def pegar_por_id(user_id):
    with _db() as c:
        row = c.execute(_q("SELECT * FROM users WHERE id=?"), (user_id,)).fetchone()
    return _perfil(row) if row else None


def registrar_email(user_id, tipo):
    """Marca que o e-mail `tipo` foi enviado a este usuário. Retorna True se é a
    PRIMEIRA vez (ou seja, DEVE enviar); False se já tinha sido enviado."""
    try:
        with _db() as c:
            if PG:
                row = c.execute(_q("""INSERT INTO email_enviados(user_id,tipo,criado)
                    VALUES(?,?,?) ON CONFLICT (user_id,tipo) DO NOTHING RETURNING user_id"""),
                    (user_id, tipo, time.time())).fetchone()
                return row is not None
            cur = c.execute("INSERT OR IGNORE INTO email_enviados(user_id,tipo,criado) VALUES(?,?,?)",
                            (user_id, tipo, time.time()))
            return cur.rowcount == 1
    except Exception as e:
        print("!! registrar_email:", e)
        return False


def usuarios_free_verificados():
    """Grátis + e-mail confirmado + NÃO descadastrado (alvo dos nudges pró)."""
    with _db() as c:
        rows = c.execute(_q("""SELECT id, nome, email, criado FROM users
            WHERE plano='free' AND email_verificado=1
              AND (email_optout IS NULL OR email_optout=0)""")).fetchall()
    return [dict(r) for r in rows]


def unsub_token(user_id):
    """Token estável de descadastro (cria se ainda não tiver)."""
    with _db() as c:
        row = c.execute(_q("SELECT unsub_token FROM users WHERE id=?"), (user_id,)).fetchone()
        if row and row["unsub_token"]:
            return row["unsub_token"]
        tok = secrets.token_urlsafe(16)
        c.execute(_q("UPDATE users SET unsub_token=? WHERE id=?"), (tok, user_id))
    return tok


def descadastrar(token):
    """Marca o usuário como opt-out de e-mails de marketing. Retorna o e-mail."""
    if not token:
        return None
    with _db() as c:
        row = c.execute(_q("SELECT id, email FROM users WHERE unsub_token=?"), (token,)).fetchone()
        if not row:
            return None
        c.execute(_q("UPDATE users SET email_optout=1 WHERE id=?"), (row["id"],))
    return row["email"]


# ---------------------------------------------------------------------------
# Posts do grupo (dedup persistente — nunca repete a mesma entrada)
# ---------------------------------------------------------------------------
def post_ja_enviado(post_id):
    with _db() as c:
        row = c.execute(_q("SELECT 1 FROM posts_grupo WHERE post_id=?"), (str(post_id),)).fetchone()
    return row is not None


def somar_dia(dia, lucro):
    """Acumula +1 entrada e +lucro no resumo do dia (pro 'boa noite')."""
    try:
        with _db() as c:
            if PG:
                c.execute(_q("""INSERT INTO dia_resumo(dia,entradas,lucro) VALUES(?,1,?)
                    ON CONFLICT (dia) DO UPDATE SET entradas=dia_resumo.entradas+1,
                    lucro=dia_resumo.lucro+EXCLUDED.lucro"""), (dia, lucro))
            else:
                c.execute("""INSERT INTO dia_resumo(dia,entradas,lucro) VALUES(?,1,?)
                    ON CONFLICT(dia) DO UPDATE SET entradas=entradas+1, lucro=lucro+?""",
                    (dia, lucro, lucro))
    except Exception as e:
        print("!! somar_dia:", e)


def pegar_dia(dia):
    with _db() as c:
        row = c.execute(_q("SELECT entradas,lucro FROM dia_resumo WHERE dia=?"), (dia,)).fetchone()
    return (int(row["entradas"]), float(row["lucro"])) if row else (0, 0.0)


def criar_campanha(nome, invite_link):
    with _db() as c:
        return _insert(c, "INSERT INTO campanhas(nome,invite_link,membros,criado) VALUES(?,?,?,?)",
                       (nome, invite_link, 0, time.time()))


def listar_campanhas():
    with _db() as c:
        rows = c.execute(_q("""SELECT id,nome,invite_link,membros,criado FROM campanhas
                               ORDER BY criado DESC""")).fetchall()
    return [dict(r) for r in rows]


def campanha_link(cid):
    with _db() as c:
        row = c.execute(_q("SELECT invite_link FROM campanhas WHERE id=?"), (cid,)).fetchone()
    return row["invite_link"] if row else None


def incrementar_membro(invite_link):
    """+1 na campanha cujo link foi usado pra entrar (chamado pelo tracker do bot)."""
    try:
        with _db() as c:
            c.execute(_q("UPDATE campanhas SET membros=membros+1 WHERE invite_link=?"), (invite_link,))
    except Exception as e:
        print("!! incrementar_membro:", e)


def excluir_campanha(cid):
    with _db() as c:
        c.execute(_q("DELETE FROM campanhas WHERE id=?"), (cid,))


def registrar_post(post_id):
    try:
        with _db() as c:
            if PG:
                c.execute(_q("""INSERT INTO posts_grupo(post_id,criado) VALUES(?,?)
                               ON CONFLICT (post_id) DO NOTHING"""), (str(post_id), time.time()))
            else:
                c.execute("INSERT OR IGNORE INTO posts_grupo(post_id,criado) VALUES(?,?)",
                          (str(post_id), time.time()))
    except Exception as e:
        print("!! registrar_post:", e)


# ---------------------------------------------------------------------------
# Tickets de suporte (conversa ida-e-volta, trava por turno)
#   status: 'aguardando' (vez do admin) | 'respondido' (vez do usuário) | 'resolvido'
# ---------------------------------------------------------------------------
def _msg_ticket(c, ticket_id, autor, texto):
    c.execute(_q("INSERT INTO ticket_msgs(ticket_id,autor,texto,criado) VALUES(?,?,?,?)"),
              (ticket_id, autor, texto[:4000], time.time()))


def _threads(ticket_ids):
    if not ticket_ids:
        return {}
    ph = ",".join(["?"] * len(ticket_ids))
    with _db() as c:
        rows = c.execute(_q(f"""SELECT ticket_id, autor, texto, criado FROM ticket_msgs
            WHERE ticket_id IN ({ph}) ORDER BY criado ASC"""), tuple(ticket_ids)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["ticket_id"], []).append(dict(r))
    return out


def criar_ticket(user_id, mensagem):
    """Abre um ticket NOVO. Trava: 1 novo ticket por usuário a cada 24h."""
    msg = (mensagem or "").strip()
    if len(msg) < 5:
        return False, "Escreva sua mensagem (mínimo 5 caracteres)."
    msg = msg[:2000]
    with _db() as c:
        row = c.execute(_q("SELECT criado FROM tickets WHERE user_id=? ORDER BY criado DESC LIMIT 1"),
                        (user_id,)).fetchone()
        if row and (time.time() - row["criado"]) < 86400:
            horas = int((86400 - (time.time() - row["criado"])) / 3600) + 1
            return False, f"Você já abriu um ticket. Pode abrir outro em ~{horas}h."
        tid = _insert(c, "INSERT INTO tickets(user_id,mensagem,status,criado) VALUES(?,?,?,?)",
                      (user_id, msg, "aguardando", time.time()))
        _msg_ticket(c, tid, "user", msg)
    return True, None


def responder_ticket_user(user_id, ticket_id, mensagem):
    """Usuário responde ao PRÓPRIO ticket — só quando é a vez dele (admin já
    respondeu). Depois trava de novo (vez do admin)."""
    msg = (mensagem or "").strip()
    if len(msg) < 2:
        return False, "Escreva sua mensagem."
    with _db() as c:
        t = c.execute(_q("SELECT user_id, status FROM tickets WHERE id=?"), (ticket_id,)).fetchone()
        if not t or t["user_id"] != user_id:
            return False, "Ticket não encontrado."
        if t["status"] != "respondido":
            return False, "Aguarde a resposta do suporte antes de enviar de novo."
        _msg_ticket(c, ticket_id, "user", msg[:4000])
        c.execute(_q("UPDATE tickets SET status='aguardando' WHERE id=?"), (ticket_id,))
    return True, None


def responder_ticket(ticket_id, resposta):
    """Admin responde. Libera a vez do usuário. Retorna {email,nome} ou None."""
    resp = (resposta or "").strip()
    if not resp:
        return None
    with _db() as c:
        row = c.execute(_q("SELECT u.email, u.nome FROM tickets t JOIN users u ON u.id=t.user_id "
                           "WHERE t.id=?"), (ticket_id,)).fetchone()
        if not row:
            return None
        _msg_ticket(c, ticket_id, "admin", resp[:4000])
        c.execute(_q("UPDATE tickets SET resposta=?, status='respondido', respondido=? WHERE id=?"),
                  (resp[:4000], time.time(), ticket_id))
    return dict(row)


def resolver_ticket(ticket_id):
    with _db() as c:
        c.execute(_q("UPDATE tickets SET status='resolvido' WHERE id=?"), (ticket_id,))
    return True


def listar_tickets_user(user_id):
    with _db() as c:
        rows = [dict(r) for r in c.execute(_q("""SELECT id, status, criado FROM tickets
            WHERE user_id=? ORDER BY criado DESC LIMIT 20"""), (user_id,)).fetchall()]
    th = _threads([r["id"] for r in rows])
    for r in rows:
        r["msgs"] = th.get(r["id"], [])
    return rows


def listar_tickets_admin(status=None):
    q = ("SELECT t.id, t.user_id, t.status, t.criado, u.nome, u.email "
         "FROM tickets t JOIN users u ON u.id = t.user_id")
    args = ()
    if status:
        q += " WHERE t.status=?"
        args = (status,)
    q += " ORDER BY t.criado DESC LIMIT 100"
    with _db() as c:
        rows = [dict(r) for r in c.execute(_q(q), args).fetchall()]
    th = _threads([r["id"] for r in rows])
    for r in rows:
        r["msgs"] = th.get(r["id"], [])
    return rows


def excluir_usuario(user_id):
    """Apaga a conta e TODOS os dados ligados a ela (sessões, banca, pagamentos,
    checkouts, assinaturas, tokens). Ação irreversível — usada pelo admin."""
    with _db() as c:
        # apaga as mensagens dos tickets do usuário antes dos tickets
        try:
            c.execute(_q("""DELETE FROM ticket_msgs WHERE ticket_id IN
                (SELECT id FROM tickets WHERE user_id=?)"""), (user_id,))
        except Exception:
            pass
        for tabela in ("sessions", "pagamentos", "user_banca", "checkouts",
                       "assinaturas", "reset_tokens", "confirm_tokens", "email_enviados",
                       "tickets"):
            try:
                c.execute(_q(f"DELETE FROM {tabela} WHERE user_id=?"), (user_id,))
            except Exception:
                pass
        c.execute(_q("DELETE FROM users WHERE id=?"), (user_id,))
    limpar_cache_sessoes()


def listar_usuarios():
    """Todos os usuários (para o painel admin), do mais novo ao mais antigo."""
    with _db() as c:
        rows = c.execute(
            "SELECT id, nome, email, plano, plano_expira, origem, criado FROM users ORDER BY criado DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def voltar_free(user_id: int):
    """Tira o PRO na marra (volta pra free, zera a expiração)."""
    with _db() as c:
        c.execute(_q("UPDATE users SET plano='free', plano_expira=NULL WHERE id=?"), (user_id,))
    limpar_cache_sessoes()


# ---------------------------------------------------------------------------
# Checkouts / pagamentos (Stripe, AbacatePay)
# ---------------------------------------------------------------------------
def checkout_registrar(provider, external_id, user_id, plano, dias, valor, metodo):
    args = (provider, external_id, user_id, plano, dias, valor, metodo, "pendente", time.time())
    with _db() as c:
        if PG:
            c.execute(_q("""INSERT INTO checkouts
                (provider,external_id,user_id,plano,dias,valor,metodo,status,criado)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT (provider,external_id) DO NOTHING"""), args)
        else:
            c.execute("""INSERT OR IGNORE INTO checkouts
                (provider,external_id,user_id,plano,dias,valor,metodo,status,criado)
                VALUES(?,?,?,?,?,?,?,?,?)""", args)


def checkout_pagar(provider, external_id, pi=None):
    """Confirma o pagamento (chamado pelo webhook): acha o checkout pendente,
    ativa o PRO e marca como pago. IDEMPOTENTE (webhook pode vir duplicado).
    `pi` = payment_intent do Stripe (guardado p/ mapear estorno/chargeback)."""
    with _db() as c:
        row = c.execute(_q("SELECT * FROM checkouts WHERE provider=? AND external_id=?"),
                        (provider, external_id)).fetchone()
        if not row:
            return None
        if row["status"] == "pago":
            return dict(row)             # já processado
        c.execute(_q("UPDATE checkouts SET status='pago', pi=? WHERE id=? AND status='pendente'"),
                  (pi, row["id"]))
    ativar_pro(row["user_id"], row["plano"], int(row["dias"]),
               float(row["valor"]), metodo=row["metodo"])
    return dict(row)


def assinatura_set(user_id, provider, sub_id, customer_id, plano, dias, valor, status="ativa"):
    """Registra/atualiza a assinatura recorrente (upsert por sub_id)."""
    with _db() as c:
        row = c.execute(_q("SELECT id FROM assinaturas WHERE sub_id=?"), (sub_id,)).fetchone()
        if row:
            c.execute(_q("UPDATE assinaturas SET status=?, customer_id=? WHERE sub_id=?"),
                      (status, customer_id, sub_id))
        else:
            c.execute(_q("""INSERT INTO assinaturas
                (user_id,provider,sub_id,customer_id,plano,dias,valor,status,criado)
                VALUES(?,?,?,?,?,?,?,?,?)"""),
                (user_id, provider, sub_id, customer_id, plano, dias, valor, status, time.time()))


def assinatura_por_sub(sub_id):
    with _db() as c:
        row = c.execute(_q("SELECT * FROM assinaturas WHERE sub_id=?"), (sub_id,)).fetchone()
    return dict(row) if row else None


def assinatura_do_user(user_id):
    """Assinatura ATIVA mais recente do usuário (p/ o botão de gerenciar/cancelar)."""
    with _db() as c:
        row = c.execute(_q("""SELECT * FROM assinaturas WHERE user_id=? AND status='ativa'
                              ORDER BY criado DESC LIMIT 1"""), (user_id,)).fetchone()
    return dict(row) if row else None


def assinatura_cancelar(sub_id):
    """Assinatura cancelada/encerrada no Stripe -> tira o PRO da pessoa."""
    with _db() as c:
        row = c.execute(_q("SELECT * FROM assinaturas WHERE sub_id=?"), (sub_id,)).fetchone()
        if not row:
            return None
        c.execute(_q("UPDATE assinaturas SET status='cancelada' WHERE sub_id=?"), (sub_id,))
    voltar_free(row["user_id"])
    print(f">> ASSINATURA cancelada: PRO revogado do user {row['user_id']}")
    return dict(row)


def checkout_revogar_por_pi(pi):
    """Estorno/chargeback: acha o checkout pago com esse payment_intent e TIRA o
    PRO da pessoa (volta pra free). Idempotente."""
    if not pi:
        return None
    with _db() as c:
        row = c.execute(_q("SELECT * FROM checkouts WHERE pi=? AND status='pago'"), (pi,)).fetchone()
        if not row:
            return None
        c.execute(_q("UPDATE checkouts SET status='estornado' WHERE id=?"), (row["id"],))
    voltar_free(row["user_id"])
    print(f">> ESTORNO/chargeback: PRO revogado do user {row['user_id']}")
    return dict(row)


# ---------------------------------------------------------------------------
# Métricas de negócio (para o dashboard admin)
# ---------------------------------------------------------------------------
def metricas():
    from datetime import datetime, timedelta
    now = time.time()
    with _db() as c:
        users = [dict(r) for r in c.execute(
            "SELECT id, nome, email, plano, plano_expira, criado FROM users").fetchall()]
        pags = [dict(r) for r in c.execute(
            "SELECT user_id, valor, plano, metodo, criado FROM pagamentos").fetchall()]

    def _ativo(u):
        return u["plano"] == "pro" and (not u["plano_expira"] or u["plano_expira"] > now)

    emails = {u["id"]: u["email"] for u in users}
    total = len(users)
    pro_ativos = [u for u in users if _ativo(u)]
    n_pro = len(pro_ativos)
    n_free = total - n_pro

    # RECEITA só conta pagamento DE VERDADE (checkout real). Ativações manuais
    # feitas pelo admin/teste (metodo 'admin'/'teste'/'manual') NÃO são dinheiro.
    _fake = ("admin", "teste", "manual")
    def _real(p):
        m = (p.get("metodo") or "").lower()
        return (p.get("valor") or 0) > 0 and not any(x in m for x in _fake)
    reais = [p for p in pags if _real(p)]
    receita_total = sum(p["valor"] for p in reais)
    d30, d7 = now - 30 * 86400, now - 7 * 86400
    receita_30 = sum(p["valor"] for p in reais if p["criado"] > d30)
    receita_7 = sum(p["valor"] for p in reais if p["criado"] > d7)
    pagantes = len({p["user_id"] for p in reais})
    ticket = (receita_total / len(reais)) if reais else 0.0
    conversao = (pagantes / total * 100) if total else 0.0
    arpu = (receita_total / total) if total else 0.0
    ltv = (receita_total / pagantes) if pagantes else 0.0

    # MRR: normaliza o último pagamento de cada PRO ativo (anual/12).
    ult_pag = {}
    for p in sorted(reais, key=lambda x: x["criado"]):
        ult_pag[p["user_id"]] = p["valor"]
    mrr = 0.0
    for u in pro_ativos:
        v = ult_pag.get(u["id"])   # só quem tem pagamento REAL entra no MRR
        if v:
            mrr += (v / 12.0) if v >= 300 else v

    novos_7 = len([u for u in users if u["criado"] > d7])
    novos_30 = len([u for u in users if u["criado"] > d30])

    # séries dos últimos 14 dias (novos usuários e receita/dia)
    dias = [(datetime.now() - timedelta(days=i)).strftime("%d/%m") for i in range(13, -1, -1)]
    chave = lambda ts: datetime.fromtimestamp(ts).strftime("%d/%m")
    s_users = {d: 0 for d in dias}
    s_rev = {d: 0.0 for d in dias}
    for u in users:
        k = chave(u["criado"])
        if k in s_users:
            s_users[k] += 1
    for p in reais:
        k = chave(p["criado"])
        if k in s_rev:
            s_rev[k] += p["valor"]

    def dias_rest(u):
        e = u.get("plano_expira")
        return max(0, math.ceil((e - now) / 86400)) if (u["plano"] == "pro" and e) else None

    recentes = [{"nome": u["nome"], "email": u["email"], "plano": ("pro" if _ativo(u) else "free"),
                 "dias": dias_rest(u), "criado": u["criado"]}
                for u in sorted(users, key=lambda x: x["criado"], reverse=True)[:8]]
    pag_rec = [{"email": emails.get(p["user_id"], "?"), "valor": p["valor"],
                "plano": p["plano"], "metodo": p.get("metodo"), "criado": p["criado"]}
               for p in sorted(reais, key=lambda x: x["criado"], reverse=True)[:8]]
    vencendo = sorted(
        [{"nome": u["nome"], "email": u["email"], "dias": dias_rest(u)}
         for u in pro_ativos if dias_rest(u) is not None and dias_rest(u) <= 5],
        key=lambda x: x["dias"])

    return {
        "total_usuarios": total, "pro_ativos": n_pro, "free": n_free,
        "receita_total": round(receita_total, 2), "receita_30d": round(receita_30, 2),
        "receita_7d": round(receita_7, 2), "mrr": round(mrr, 2),
        "arr": round(mrr * 12, 2), "ltv": round(ltv, 2), "arpu": round(arpu, 2),
        "ticket_medio": round(ticket, 2), "conversao": round(conversao, 1),
        "pagantes": pagantes, "novos_7d": novos_7, "novos_30d": novos_30,
        "serie_dias": dias,
        "serie_usuarios": [s_users[d] for d in dias],
        "serie_receita": [round(s_rev[d], 2) for d in dias],
        "usuarios_recentes": recentes, "pagamentos_recentes": pag_rec,
        "vencendo": vencendo,
    }


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


def catalogo_get():
    """Catálogo ACUMULADO de casas/esportes já vistos (linha id=2). O filtro
    sempre mostra TODAS as casas já raspadas — nunca encolhe, mesmo que uma
    atualização traga só algumas casas. Sobrevive a redeploy."""
    import json
    try:
        with _db() as c:
            row = c.execute(_q("SELECT dados FROM feed_cache WHERE id=2")).fetchone()
        if row:
            d = json.loads(row["dados"])
            if isinstance(d, dict):
                return {"casas": d.get("casas", {}), "esportes": d.get("esportes", {})}
    except Exception as e:
        print("!! catalogo_get:", e)
    return {"casas": {}, "esportes": {}}


def catalogo_set(catalogo):
    import json
    dados = json.dumps(catalogo or {}, ensure_ascii=False)
    agora = time.time()
    try:
        with _db() as c:
            if PG:
                c.execute(_q("""INSERT INTO feed_cache(id,dados,atualizado) VALUES(2,?,?)
                               ON CONFLICT (id) DO UPDATE SET dados=EXCLUDED.dados,
                               atualizado=EXCLUDED.atualizado"""), (dados, agora))
            else:
                c.execute("INSERT OR REPLACE INTO feed_cache(id,dados,atualizado) VALUES(2,?,?)",
                          (dados, agora))
    except Exception as e:
        print("!! catalogo_set:", e)


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
def criar_usuario(nome: str, email: str, senha: str, whatsapp: str = "", origem: str = ""):
    nome = (nome or "").strip()
    email = (email or "").strip().lower()
    whats = "".join(ch for ch in (whatsapp or "") if ch.isdigit())
    origem = (origem or "").strip()[:40] or None
    if len(nome) < 2:
        return None, "Digite seu nome."
    if "@" not in email or "." not in email.split("@")[-1]:
        return None, "E-mail inválido."
    if len(whats) not in (10, 11):
        return None, "WhatsApp inválido — informe com DDD (ex.: 11 99999-9999)."
    if len(senha or "") < 6:
        return None, "A senha precisa ter pelo menos 6 caracteres."
    salt = secrets.token_bytes(16)
    try:
        with _db() as c:
            uid = _insert(c,
                "INSERT INTO users(nome,email,whatsapp,origem,hash,salt,plano,email_verificado,criado) VALUES(?,?,?,?,?,?,?,?,?)",
                (nome, email, whats, origem, _hash(senha, salt), salt, "free", 0, time.time()))
    except UNIQUE_ERR:
        return None, "Este e-mail já tem conta. Faça login."
    return {"id": uid, "nome": nome, "email": email, "plano": "free", "plano_expira": None}, None


def pegar_ou_criar_google(email: str, nome: str, origem: str = ""):
    """Login com Google: acha o usuário pelo e-mail ou cria um novo (plano free)."""
    email = (email or "").strip().lower()
    nome = (nome or "").strip() or email.split("@")[0]
    origem = (origem or "").strip()[:40] or None
    with _db() as c:
        row = c.execute(_q("SELECT * FROM users WHERE email=?"), (email,)).fetchone()
        if row:
            return _perfil(row), False       # já existia
        salt = secrets.token_bytes(16)
        uid = _insert(c,
            "INSERT INTO users(nome,email,origem,hash,salt,plano,email_verificado,criado) VALUES(?,?,?,?,?,?,?,?)",
            (nome, email, origem, _hash(secrets.token_hex(24), salt), salt, "free", 1, time.time()))
    return {"id": uid, "nome": nome, "email": email, "plano": "free", "plano_expira": None}, True


def atualizar_whatsapp(user_id, whatsapp):
    """Salva/atualiza o WhatsApp do usuário. Retorna (ok, digitos_ou_erro)."""
    whats = "".join(ch for ch in (whatsapp or "") if ch.isdigit())
    if len(whats) not in (10, 11):
        return False, "WhatsApp inválido — informe com DDD."
    with _db() as c:
        c.execute(_q("UPDATE users SET whatsapp=? WHERE id=?"), (whats, user_id))
    limpar_cache_sessoes()
    return True, whats


def criar_token_confirmacao(user_id):
    """Gera token de confirmação de e-mail (vale 3 dias)."""
    token = secrets.token_urlsafe(32)
    with _db() as c:
        c.execute(_q("INSERT INTO confirm_tokens(token,user_id,expira,usado) VALUES(?,?,?,0)"),
                  (token, user_id, time.time() + 3 * 86400))
    return token


def confirmar_email(token):
    """Valida o token e marca o e-mail como verificado. Retorna {id,nome,email} ou None."""
    with _db() as c:
        row = c.execute(_q("SELECT * FROM confirm_tokens WHERE token=?"), (token or "",)).fetchone()
        if not row or int(row["usado"]) or row["expira"] < time.time():
            return None
        c.execute(_q("UPDATE users SET email_verificado=1 WHERE id=?"), (row["user_id"],))
        c.execute(_q("UPDATE confirm_tokens SET usado=1 WHERE token=?"), (token,))
        u = c.execute(_q("SELECT id, nome, email FROM users WHERE id=?"), (row["user_id"],)).fetchone()
    limpar_cache_sessoes()
    return dict(u) if u else None


def user_nao_verificado(email):
    """Retorna (id, nome) se existe conta NÃO verificada com esse e-mail; senão (None, None)."""
    email = (email or "").strip().lower()
    with _db() as c:
        row = c.execute(_q("SELECT id, nome, email_verificado FROM users WHERE email=?"),
                        (email,)).fetchone()
    if row and not int(row["email_verificado"]):
        return row["id"], row["nome"]
    return None, None


def criar_token_reset(email: str):
    """Gera um token de redefinição de senha p/ o e-mail. Retorna (token, nome) ou
    (None, None) se o e-mail não existe (NÃO revelamos isso ao usuário)."""
    email = (email or "").strip().lower()
    with _db() as c:
        row = c.execute(_q("SELECT id, nome FROM users WHERE email=?"), (email,)).fetchone()
        if not row:
            return None, None
        token = secrets.token_urlsafe(32)
        c.execute(_q("INSERT INTO reset_tokens(token,user_id,expira,usado) VALUES(?,?,?,0)"),
                  (token, row["id"], time.time() + 3600))   # vale 1 hora
    return token, row["nome"]


def redefinir_senha(token: str, nova_senha: str):
    """Valida o token e troca a senha. Retorna (ok, erro)."""
    if len(nova_senha or "") < 6:
        return False, "A senha precisa ter pelo menos 6 caracteres."
    with _db() as c:
        row = c.execute(_q("SELECT * FROM reset_tokens WHERE token=?"), (token or "",)).fetchone()
        if not row or int(row["usado"]) or row["expira"] < time.time():
            return False, "Link inválido ou expirado. Peça um novo."
        salt = secrets.token_bytes(16)
        c.execute(_q("UPDATE users SET hash=?, salt=? WHERE id=?"),
                  (_hash(nova_senha, salt), salt, row["user_id"]))
        c.execute(_q("UPDATE reset_tokens SET usado=1 WHERE token=?"), (token,))
    limpar_cache_sessoes()   # força reautenticação (senha mudou)
    return True, None


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
            "plano": plano, "plano_expira": exp,
            "verificado": bool(row["email_verificado"])}


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
            """SELECT u.id, u.nome, u.email, u.whatsapp, u.plano, u.plano_expira, s.criado AS s_criado
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token=?"""), (token,)).fetchone()
        if not row:
            return None
        if time.time() - row["s_criado"] > SESSAO_MAX_S:
            c.execute(_q("DELETE FROM sessions WHERE token=?"), (token,))
            return None
        plano, exp = _normalizar_plano(c, row)
    perfil = {"id": row["id"], "nome": row["nome"], "email": row["email"],
              "whatsapp": row["whatsapp"], "plano": plano, "plano_expira": exp}
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
