"""Contas, códigos de verificação e sessões.

O login não usa senha. São dois caminhos, e os dois terminam num e-mail
comprovadamente do usuário:

- **código por e-mail** — seis dígitos, válidos por 10 minutos, guardados como
  hash e comparados em tempo constante;
- **Google** — o provedor confirma o e-mail e devolve o perfil.

Quem entra pelos dois caminhos com o mesmo e-mail cai na mesma conta.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from .store import (
    agora,
    email_valido,
    hash_token,
    iso,
    normaliza_email,
    novo_token,
    registra_evento,
)

#: Quanto tempo um código vale.
VALIDADE_CODIGO = timedelta(minutes=10)
#: Tentativas erradas antes de queimar o código.
MAX_TENTATIVAS = 5
#: Quantos códigos um e-mail pode pedir por janela.
MAX_PEDIDOS = 5
JANELA_PEDIDOS = timedelta(minutes=15)
#: Duração da sessão. Renovada a cada uso (ver ``validar_sessao``).
VALIDADE_SESSAO = timedelta(days=30)


class ErroAuth(Exception):
    """Falha de autenticação com mensagem pronta para o usuário."""

    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


@dataclass(frozen=True)
class Usuario:
    id: int
    email: str
    nome: str
    avatar_url: Optional[str]
    admin: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "nome": self.nome,
            "avatar_url": self.avatar_url,
            "admin": self.admin,
        }


def _linha_para_usuario(linha: sqlite3.Row) -> Usuario:
    return Usuario(
        id=linha["id"],
        email=linha["email"],
        nome=linha["nome"] or "",
        avatar_url=linha["avatar_url"],
        admin=bool(linha["admin"]),
    )


# --------------------------------------------------------------------------
# Contas
# --------------------------------------------------------------------------


def obter_ou_criar_usuario(
    db: sqlite3.Connection,
    email: str,
    *,
    nome: str = "",
    avatar_url: Optional[str] = None,
) -> Usuario:
    """Busca a conta pelo e-mail; cria se não existir.

    O primeiro usuário do sistema vira administrador — sem isso, uma
    instalação nova não teria ninguém para liberar as demais contas.
    """
    email = normaliza_email(email)
    if not email_valido(email):
        raise ErroAuth("E-mail inválido.")

    linha = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    if linha is not None:
        if not linha["ativo"]:
            raise ErroAuth("Esta conta está desativada.", status=403)
        # Só completa o que está faltando: nome digitado pelo usuário não é
        # sobrescrito por um valor vindo do provedor depois.
        if (nome and not linha["nome"]) or (avatar_url and not linha["avatar_url"]):
            with db:
                db.execute(
                    "UPDATE usuarios SET nome = COALESCE(NULLIF(nome,''), ?),"
                    " avatar_url = COALESCE(avatar_url, ?) WHERE id = ?",
                    (nome, avatar_url, linha["id"]),
                )
            linha = db.execute("SELECT * FROM usuarios WHERE id = ?", (linha["id"],)).fetchone()
        return _linha_para_usuario(linha)

    primeiro = db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0
    with db:
        cursor = db.execute(
            "INSERT INTO usuarios (email, nome, avatar_url, admin) VALUES (?,?,?,?)",
            (email, nome or "", avatar_url, 1 if primeiro else 0),
        )
        registra_evento(db, "conta_criada", email=email, usuario_id=cursor.lastrowid)
    linha = db.execute("SELECT * FROM usuarios WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _linha_para_usuario(linha)


def vincular_identidade(
    db: sqlite3.Connection, usuario_id: int, provedor: str, provedor_id: str, email: str
) -> None:
    """Liga uma identidade externa (Google) à conta, sem duplicar."""
    with db:
        db.execute(
            "INSERT OR IGNORE INTO identidades (usuario_id, provedor, provedor_id, email)"
            " VALUES (?,?,?,?)",
            (usuario_id, provedor, provedor_id, email),
        )


def usuario_por_id(db: sqlite3.Connection, usuario_id: int) -> Optional[Usuario]:
    linha = db.execute(
        "SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (usuario_id,)
    ).fetchone()
    return _linha_para_usuario(linha) if linha else None


# --------------------------------------------------------------------------
# Código de verificação por e-mail
# --------------------------------------------------------------------------


def _hash_codigo(email: str, codigo: str) -> str:
    """Hash com o e-mail junto: o mesmo código para outro e-mail não confere."""
    return hashlib.sha256(f"{email}|{codigo}".encode("utf-8")).hexdigest()


def gerar_codigo(
    db: sqlite3.Connection, email: str, *, ip: Optional[str] = None
) -> str:
    """Cria um código de 6 dígitos e devolve em claro (para enviar por e-mail).

    O banco fica só com o hash. Pedidos são limitados por e-mail para o
    endereço de outra pessoa não virar alvo de flood.
    """
    email = normaliza_email(email)
    if not email_valido(email):
        raise ErroAuth("E-mail inválido.")

    desde = iso(agora() - JANELA_PEDIDOS)
    recentes = db.execute(
        "SELECT COUNT(*) FROM codigos WHERE email = ? AND criado_em > ?", (email, desde)
    ).fetchone()[0]
    if recentes >= MAX_PEDIDOS:
        raise ErroAuth(
            "Muitos códigos pedidos para este e-mail. Aguarde alguns minutos.", status=429
        )

    codigo = f"{secrets.randbelow(1_000_000):06d}"
    with db:
        # Códigos anteriores do mesmo e-mail deixam de valer: só o último serve.
        db.execute(
            "UPDATE codigos SET usado_em = ? WHERE email = ? AND usado_em IS NULL",
            (iso(agora()), email),
        )
        db.execute(
            "INSERT INTO codigos (email, codigo_hash, expira_em, ip) VALUES (?,?,?,?)",
            (email, _hash_codigo(email, codigo), iso(agora() + VALIDADE_CODIGO), ip),
        )
        registra_evento(db, "codigo_solicitado", email=email, ip=ip)
    return codigo


def verificar_codigo(
    db: sqlite3.Connection, email: str, codigo: str, *, ip: Optional[str] = None
) -> Usuario:
    """Confere o código e devolve a conta (criando na primeira vez)."""
    email = normaliza_email(email)
    codigo = (codigo or "").strip().replace(" ", "").replace("-", "")

    linha = db.execute(
        "SELECT * FROM codigos WHERE email = ? AND usado_em IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (email,),
    ).fetchone()
    if linha is None:
        with db:
            registra_evento(db, "codigo_invalido", email=email, sucesso=False,
                            detalhe="sem codigo pendente", ip=ip)
        raise ErroAuth("Código expirado ou já usado. Peça um novo.")

    if linha["expira_em"] < iso(agora()):
        with db:
            db.execute("UPDATE codigos SET usado_em = ? WHERE id = ?", (iso(agora()), linha["id"]))
            registra_evento(db, "codigo_expirado", email=email, sucesso=False, ip=ip)
        raise ErroAuth("Código expirado. Peça um novo.")

    if linha["tentativas"] >= MAX_TENTATIVAS:
        with db:
            db.execute("UPDATE codigos SET usado_em = ? WHERE id = ?", (iso(agora()), linha["id"]))
            registra_evento(db, "codigo_bloqueado", email=email, sucesso=False, ip=ip)
        raise ErroAuth("Muitas tentativas erradas. Peça um novo código.", status=429)

    # compare_digest evita que o tempo de resposta revele quantos dígitos batem.
    if not hmac.compare_digest(linha["codigo_hash"], _hash_codigo(email, codigo)):
        with db:
            db.execute("UPDATE codigos SET tentativas = tentativas + 1 WHERE id = ?", (linha["id"],))
            registra_evento(db, "codigo_incorreto", email=email, sucesso=False, ip=ip)
        restantes = MAX_TENTATIVAS - linha["tentativas"] - 1
        raise ErroAuth(
            f"Código incorreto. {restantes} tentativa(s) restante(s)."
            if restantes > 0
            else "Código incorreto. Peça um novo."
        )

    with db:
        db.execute("UPDATE codigos SET usado_em = ? WHERE id = ?", (iso(agora()), linha["id"]))
    usuario = obter_ou_criar_usuario(db, email)
    with db:
        registra_evento(db, "login_codigo", email=email, usuario_id=usuario.id, ip=ip)
    return usuario


# --------------------------------------------------------------------------
# Sessões
# --------------------------------------------------------------------------


def criar_sessao(
    db: sqlite3.Connection,
    usuario_id: int,
    *,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> Tuple[str, str]:
    """Cria a sessão e devolve ``(token_em_claro, expira_em_iso)``.

    O token só existe em claro aqui e no cookie do navegador; o banco guarda o
    hash.
    """
    token = novo_token()
    expira = iso(agora() + VALIDADE_SESSAO)
    with db:
        db.execute(
            "INSERT INTO sessoes (usuario_id, token_hash, expira_em, user_agent, ip)"
            " VALUES (?,?,?,?,?)",
            (usuario_id, hash_token(token), expira, (user_agent or "")[:300], ip),
        )
        db.execute("UPDATE usuarios SET ultimo_acesso = ? WHERE id = ?", (iso(agora()), usuario_id))
    return token, expira


def validar_sessao(db: sqlite3.Connection, token: Optional[str]) -> Optional[Usuario]:
    """Devolve o usuário da sessão, ou ``None`` se o token não vale.

    Cada uso empurra a expiração para frente: quem usa todo dia não é
    deslogado, quem some por 30 dias precisa entrar de novo.
    """
    if not token:
        return None
    linha = db.execute(
        "SELECT s.id, s.usuario_id, s.expira_em FROM sessoes s"
        " WHERE s.token_hash = ? AND s.encerrada_em IS NULL",
        (hash_token(token),),
    ).fetchone()
    if linha is None or linha["expira_em"] < iso(agora()):
        return None

    usuario = usuario_por_id(db, linha["usuario_id"])
    if usuario is None:
        return None
    with db:
        db.execute(
            "UPDATE sessoes SET ultima_atividade = ?, expira_em = ? WHERE id = ?",
            (iso(agora()), iso(agora() + VALIDADE_SESSAO), linha["id"]),
        )
    return usuario


def encerrar_sessao(db: sqlite3.Connection, token: Optional[str]) -> None:
    if not token:
        return
    with db:
        db.execute(
            "UPDATE sessoes SET encerrada_em = ? WHERE token_hash = ? AND encerrada_em IS NULL",
            (iso(agora()), hash_token(token)),
        )


def encerrar_todas_sessoes(db: sqlite3.Connection, usuario_id: int) -> int:
    """Desconecta a conta de todos os dispositivos. Devolve quantas caíram."""
    with db:
        cursor = db.execute(
            "UPDATE sessoes SET encerrada_em = ? WHERE usuario_id = ? AND encerrada_em IS NULL",
            (iso(agora()), usuario_id),
        )
        registra_evento(db, "sessoes_encerradas", usuario_id=usuario_id,
                        detalhe=f"{cursor.rowcount} sessão(ões)")
    return cursor.rowcount
