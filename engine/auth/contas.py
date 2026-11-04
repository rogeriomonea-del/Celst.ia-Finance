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
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

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

MSG_CONTA_INATIVA = (
    "Sua conta ainda não foi liberada. Um administrador precisa aprovar o acesso"
    " na tela de administração."
)


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
    ativo: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "nome": self.nome,
            "avatar_url": self.avatar_url,
            "admin": self.admin,
            "ativo": self.ativo,
        }


def _linha_para_usuario(linha: sqlite3.Row) -> Usuario:
    return Usuario(
        id=linha["id"],
        email=linha["email"],
        nome=linha["nome"] or "",
        avatar_url=linha["avatar_url"],
        admin=bool(linha["admin"]),
        ativo=bool(linha["ativo"]),
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

    Aprovação de contas: por padrão (``CELESTIA_CADASTRO_ABERTO`` diferente de
    ``1``) toda conta nova depois da primeira nasce **inativa**, esperando um
    administrador aprovar na tela de administração. Enquanto isso o login
    termina em 403 com a mensagem de "aguardando aprovação" — a conta existe,
    aparece para o admin, mas não abre sessão nem espaço de dados.
    """
    email = normaliza_email(email)
    if not email_valido(email):
        raise ErroAuth("E-mail inválido.")

    linha = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    if linha is not None:
        if not linha["ativo"]:
            raise ErroAuth(MSG_CONTA_INATIVA, status=403)
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
    ativo = 1 if (primeiro or cadastro_aberto()) else 0
    with db:
        cursor = db.execute(
            "INSERT INTO usuarios (email, nome, avatar_url, admin, ativo) VALUES (?,?,?,?,?)",
            (email, nome or "", avatar_url, 1 if primeiro else 0, ativo),
        )
        registra_evento(
            db,
            "conta_criada" if ativo else "conta_aguardando_aprovacao",
            email=email,
            usuario_id=cursor.lastrowid,
        )
    if not ativo:
        raise ErroAuth(MSG_CONTA_INATIVA, status=403)
    linha = db.execute("SELECT * FROM usuarios WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _linha_para_usuario(linha)


def cadastro_aberto() -> bool:
    """``CELESTIA_CADASTRO_ABERTO=1`` ativa contas novas na hora, sem aprovação."""
    return os.environ.get("CELESTIA_CADASTRO_ABERTO", "").strip() == "1"


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


def usuario_por_id(
    db: sqlite3.Connection, usuario_id: int, *, incluir_inativos: bool = False
) -> Optional[Usuario]:
    """Conta pelo id. Por padrão só contas ativas (é o que uma sessão precisa)."""
    if incluir_inativos:
        linha = db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    else:
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


# --------------------------------------------------------------------------
# Administração (só para contas com ``admin``; a API confere antes de chamar)
# --------------------------------------------------------------------------


def listar_usuarios(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Todas as contas com contagem de sessões ativas e último acesso."""
    linhas = db.execute(
        """
        SELECT u.id, u.email, u.nome, u.avatar_url, u.criado_em, u.ultimo_acesso,
               u.ativo, u.admin,
               (SELECT COUNT(*) FROM sessoes s
                 WHERE s.usuario_id = u.id AND s.encerrada_em IS NULL
                   AND s.expira_em > ?) AS sessoes_ativas,
               (SELECT GROUP_CONCAT(provedor) FROM identidades i
                 WHERE i.usuario_id = u.id) AS provedores
          FROM usuarios u
         ORDER BY u.ativo ASC, u.admin DESC, u.criado_em DESC
        """,
        (iso(agora()),),
    ).fetchall()
    return [
        {
            "id": linha["id"],
            "email": linha["email"],
            "nome": linha["nome"] or "",
            "avatar_url": linha["avatar_url"],
            "criado_em": linha["criado_em"],
            "ultimo_acesso": linha["ultimo_acesso"],
            "ativo": bool(linha["ativo"]),
            "admin": bool(linha["admin"]),
            "sessoes_ativas": int(linha["sessoes_ativas"] or 0),
            "provedores": sorted(set((linha["provedores"] or "").split(","))) if linha["provedores"] else [],
        }
        for linha in linhas
    ]


def atualizar_usuario(
    db: sqlite3.Connection,
    usuario_id: int,
    *,
    por: Usuario,
    ativo: Optional[bool] = None,
    admin: Optional[bool] = None,
) -> Usuario:
    """Aprova/desativa uma conta ou dá/tira o papel de administrador.

    Regras que impedem a instalação de ficar sem ninguém no comando:
    um admin não pode se desativar nem tirar o próprio papel, e o último
    administrador ativo não pode ser rebaixado nem desativado.
    """
    alvo = usuario_por_id(db, usuario_id, incluir_inativos=True)
    if alvo is None:
        raise ErroAuth("Conta não encontrada.", status=404)
    if ativo is None and admin is None:
        raise ErroAuth("Nada para alterar: envie 'ativo' e/ou 'admin'.")

    if alvo.id == por.id and (ativo is False or admin is False):
        raise ErroAuth("Você não pode desativar nem rebaixar a própria conta.", status=409)

    perde_admin = alvo.admin and (admin is False or ativo is False)
    if perde_admin:
        outros_admins = db.execute(
            "SELECT COUNT(*) FROM usuarios WHERE admin = 1 AND ativo = 1 AND id != ?",
            (alvo.id,),
        ).fetchone()[0]
        if outros_admins == 0:
            raise ErroAuth("Este é o último administrador ativo.", status=409)

    campos, valores = [], []
    if ativo is not None:
        campos.append("ativo = ?")
        valores.append(1 if ativo else 0)
    if admin is not None:
        campos.append("admin = ?")
        valores.append(1 if admin else 0)
    valores.append(alvo.id)
    with db:
        db.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", valores)
        if ativo is False:
            # Conta desativada não pode continuar logada em lugar nenhum.
            db.execute(
                "UPDATE sessoes SET encerrada_em = ? WHERE usuario_id = ? AND encerrada_em IS NULL",
                (iso(agora()), alvo.id),
            )
        detalhe = ", ".join(
            parte
            for parte in (
                None if ativo is None else ("aprovada" if ativo else "desativada"),
                None if admin is None else ("admin" if admin else "sem admin"),
            )
            if parte
        )
        registra_evento(
            db, "admin_alterou_conta", email=alvo.email, usuario_id=alvo.id,
            detalhe=f"por {por.email}: {detalhe}",
        )
    atualizado = usuario_por_id(db, alvo.id, incluir_inativos=True)
    assert atualizado is not None
    return atualizado


def listar_eventos(
    db: sqlite3.Connection, *, limite: int = 100, email: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Eventos de acesso mais recentes (sem código, sem token — nunca houve)."""
    limite = max(1, min(int(limite), 500))
    if email:
        linhas = db.execute(
            "SELECT * FROM eventos_acesso WHERE email = ? ORDER BY id DESC LIMIT ?",
            (normaliza_email(email), limite),
        ).fetchall()
    else:
        linhas = db.execute(
            "SELECT * FROM eventos_acesso ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [
        {
            "id": linha["id"],
            "quando": linha["quando"],
            "email": linha["email"],
            "usuario_id": linha["usuario_id"],
            "acao": linha["acao"],
            "sucesso": bool(linha["sucesso"]),
            "detalhe": linha["detalhe"],
            "ip": linha["ip"],
        }
        for linha in linhas
    ]


def listar_sessoes(
    db: sqlite3.Connection, *, usuario_id: Optional[int] = None, apenas_ativas: bool = True
) -> List[Dict[str, Any]]:
    """Sessões (sem o hash do token) — de uma conta ou de todas."""
    condicoes, valores = [], []
    if usuario_id is not None:
        condicoes.append("s.usuario_id = ?")
        valores.append(usuario_id)
    if apenas_ativas:
        condicoes.append("s.encerrada_em IS NULL AND s.expira_em > ?")
        valores.append(iso(agora()))
    where = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    linhas = db.execute(
        f"""
        SELECT s.id, s.usuario_id, u.email, s.criada_em, s.expira_em,
               s.ultima_atividade, s.user_agent, s.ip, s.encerrada_em
          FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id
          {where}
         ORDER BY s.ultima_atividade DESC
         LIMIT 500
        """,
        valores,
    ).fetchall()
    return [
        {
            "id": linha["id"],
            "usuario_id": linha["usuario_id"],
            "email": linha["email"],
            "criada_em": linha["criada_em"],
            "expira_em": linha["expira_em"],
            "ultima_atividade": linha["ultima_atividade"],
            "user_agent": (linha["user_agent"] or "")[:200],
            "ip": linha["ip"],
            "ativa": linha["encerrada_em"] is None,
        }
        for linha in linhas
    ]


def encerrar_sessao_por_id(db: sqlite3.Connection, sessao_id: int, *, por: Usuario) -> bool:
    """Encerra UMA sessão pelo id (ação de administrador). ``False`` se não existia."""
    linha = db.execute(
        "SELECT s.id, u.email, s.usuario_id FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id"
        " WHERE s.id = ? AND s.encerrada_em IS NULL",
        (sessao_id,),
    ).fetchone()
    if linha is None:
        return False
    with db:
        db.execute("UPDATE sessoes SET encerrada_em = ? WHERE id = ?", (iso(agora()), sessao_id))
        registra_evento(
            db, "admin_encerrou_sessao", email=linha["email"], usuario_id=linha["usuario_id"],
            detalhe=f"por {por.email}",
        )
    return True
