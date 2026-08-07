"""Persistência de contas, sessões e códigos — banco separado do financeiro.

Duas decisões estruturais moram aqui:

**Banco de autenticação à parte.** Contas e sessões ficam em ``auth.db``, longe
dos dados financeiros. Um bug numa consulta de extrato não alcança credenciais,
e o backup de cada um pode ter política própria.

**Um banco por usuário.** Os dados financeiros de cada conta vivem em
``usuarios/<id>/extratos.db``, com diretório de originais próprio. O isolamento
é físico, não uma cláusula ``WHERE`` que alguém pode esquecer — a forma mais
comum de vazar dado entre inquilinos simplesmente não existe aqui. Como o
``engine.extratos.store.conexao`` já aceita um caminho, o resto do módulo
funciona sem alteração.

Segredos nunca são guardados em claro: senha não existe (o login é por código
ou Google), o token de sessão é gravado como hash e o código de verificação
também.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..extratos.store import diretorio_dados

VERSAO_SCHEMA = 1

_MIGRACOES = {
    1: """
    CREATE TABLE usuarios (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        nome TEXT NOT NULL DEFAULT '',
        avatar_url TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        ultimo_acesso TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        admin INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE identidades (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        provedor TEXT NOT NULL,
        provedor_id TEXT NOT NULL,
        email TEXT,
        criada_em TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (provedor, provedor_id)
    );
    CREATE TABLE codigos (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        codigo_hash TEXT NOT NULL,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        expira_em TEXT NOT NULL,
        tentativas INTEGER NOT NULL DEFAULT 0,
        usado_em TEXT,
        ip TEXT
    );
    CREATE INDEX idx_codigos_email ON codigos(email, criado_em);
    CREATE TABLE sessoes (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        criada_em TEXT NOT NULL DEFAULT (datetime('now')),
        expira_em TEXT NOT NULL,
        ultima_atividade TEXT NOT NULL DEFAULT (datetime('now')),
        user_agent TEXT,
        ip TEXT,
        encerrada_em TEXT
    );
    CREATE INDEX idx_sessoes_usuario ON sessoes(usuario_id);
    CREATE TABLE estados_oauth (
        estado TEXT PRIMARY KEY,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        redirecionar_para TEXT
    );
    CREATE TABLE eventos_acesso (
        id INTEGER PRIMARY KEY,
        quando TEXT NOT NULL DEFAULT (datetime('now')),
        email TEXT,
        usuario_id INTEGER,
        acao TEXT NOT NULL,
        sucesso INTEGER NOT NULL DEFAULT 1,
        detalhe TEXT,
        ip TEXT
    );
    CREATE INDEX idx_eventos_email ON eventos_acesso(email, quando);
    """,
}


def caminho_banco_auth() -> Path:
    return diretorio_dados() / "auth.db"


@contextmanager
def conexao(caminho: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Conexão com o banco de autenticação, já migrada."""
    db = sqlite3.connect(str(caminho or caminho_banco_auth()))
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA busy_timeout = 5000")
        atual = db.execute("PRAGMA user_version").fetchone()[0]
        for versao in sorted(_MIGRACOES):
            if versao > atual:
                with db:
                    db.executescript(_MIGRACOES[versao])
                    db.execute(f"PRAGMA user_version = {versao}")
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# Espaço de dados por usuário
# --------------------------------------------------------------------------


def espaco_do_usuario(usuario_id: int) -> Path:
    """Diretório exclusivo de um usuário (criado sob demanda, modo 0700).

    É este caminho que separa um usuário do outro. Cada conta tem seu próprio
    ``extratos.db`` e sua própria pasta de originais.
    """
    if not isinstance(usuario_id, int) or usuario_id <= 0:
        raise ValueError("usuario_id inválido")
    destino = diretorio_dados() / "usuarios" / str(usuario_id)
    destino.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(destino, 0o700)
    except OSError:
        pass
    return destino


def banco_do_usuario(usuario_id: int) -> Path:
    return espaco_do_usuario(usuario_id) / "extratos.db"


# --------------------------------------------------------------------------
# Helpers de segurança
# --------------------------------------------------------------------------


def agora() -> datetime:
    return datetime.now(timezone.utc)


def iso(momento: datetime) -> str:
    """Carimbo no MESMO formato do ``datetime('now')`` do SQLite (UTC, com espaço).

    As colunas de data são comparadas como texto (``expira_em < ?``), então os
    dois lados precisam ter exatamente o mesmo formato. Misturar isto com o
    ISO-8601 de ``datetime.isoformat()`` (que usa ``T`` e traz o fuso) faz toda
    comparação sair errada em silêncio: o espaço ordena antes do ``T``, então
    um lado sempre vence.
    """
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hash_token(token: str) -> str:
    """SHA-256 do token. O banco guarda só o hash: vazar o banco não dá acesso."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def novo_token(bytes_: int = 32) -> str:
    return secrets.token_urlsafe(bytes_)


def normaliza_email(email: str) -> str:
    """Minúsculas e sem espaços. Não removemos pontos do Gmail de propósito:
    tratar ``a.b@gmail.com`` e ``ab@gmail.com`` como a mesma conta surpreende
    o usuário e não é verdade em outros provedores."""
    return (email or "").strip().lower()


def email_valido(email: str) -> bool:
    email = normaliza_email(email)
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    local, _, dominio = email.partition("@")
    if not local or not dominio or "." not in dominio:
        return False
    if ".." in email or email.startswith(".") or dominio.startswith("-"):
        return False
    return all(ch.isascii() and (ch.isalnum() or ch in "._%+-@") for ch in email)


def registra_evento(
    db: sqlite3.Connection,
    acao: str,
    *,
    email: Optional[str] = None,
    usuario_id: Optional[int] = None,
    sucesso: bool = True,
    detalhe: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """Trilha de acesso. Nunca grave aqui código, token ou dado financeiro."""
    db.execute(
        "INSERT INTO eventos_acesso (email, usuario_id, acao, sucesso, detalhe, ip)"
        " VALUES (?,?,?,?,?,?)",
        (email, usuario_id, acao, 1 if sucesso else 0, detalhe, ip),
    )


def limpar_expirados(db: sqlite3.Connection) -> None:
    """Remove códigos vencidos, sessões expiradas e estados de OAuth velhos."""
    limite_oauth = iso(agora() - timedelta(minutes=15))
    with db:
        db.execute("DELETE FROM codigos WHERE expira_em < ?", (iso(agora()),))
        db.execute("DELETE FROM sessoes WHERE expira_em < ?", (iso(agora()),))
        db.execute("DELETE FROM estados_oauth WHERE criado_em < ?", (limite_oauth,))
