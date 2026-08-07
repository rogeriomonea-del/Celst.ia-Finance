"""Persistência do módulo de extratos — SQLite (stdlib), transacional.

Um único arquivo ``extratos.db`` dentro de :func:`diretorio_dados`, com WAL e
``foreign_keys`` ligados. Todas as escritas relevantes acontecem dentro de
``with conexao() as db:`` — o ``with`` do sqlite3 faz COMMIT no sucesso e
ROLLBACK na exceção, o que garante o invariante "nenhuma falha deixa dados
parcialmente confirmados".

O schema é versionado por ``PRAGMA user_version``; :func:`migrar` aplica as
migrações pendentes em ordem e é idempotente.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

#: Versão atual do schema (incrementar ao adicionar migração).
VERSAO_SCHEMA = 1

_MIGRACOES = {
    1: """
    CREATE TABLE instituicoes (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL UNIQUE,
        tipo TEXT NOT NULL DEFAULT 'banco'
    );
    CREATE TABLE contas (
        id INTEGER PRIMARY KEY,
        instituicao_id INTEGER REFERENCES instituicoes(id),
        nome TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'corrente',
        moeda TEXT NOT NULL DEFAULT 'BRL',
        apelido TEXT NOT NULL DEFAULT '',
        criada_em TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (instituicao_id, nome)
    );
    CREATE TABLE cartoes (
        id INTEGER PRIMARY KEY,
        instituicao_id INTEGER REFERENCES instituicoes(id),
        nome TEXT NOT NULL UNIQUE,
        emissor TEXT,
        bandeira TEXT,
        final TEXT,
        conta_pagadora_id INTEGER REFERENCES contas(id),
        dia_fechamento INTEGER,
        dia_vencimento INTEGER,
        limite_total_centavos INTEGER,
        moeda TEXT NOT NULL DEFAULT 'BRL',
        criado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE faturas (
        id INTEGER PRIMARY KEY,
        cartao_id INTEGER NOT NULL REFERENCES cartoes(id),
        competencia TEXT NOT NULL,
        fecha_em TEXT,
        vence_em TEXT NOT NULL,
        valor_centavos INTEGER,
        pago_centavos INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'aberta',
        origem TEXT NOT NULL DEFAULT 'importacao',
        UNIQUE (cartao_id, vence_em)
    );
    CREATE TABLE arquivos (
        id INTEGER PRIMARY KEY,
        sha256 TEXT NOT NULL UNIQUE,
        nome_original TEXT NOT NULL,
        mime_real TEXT NOT NULL,
        tamanho INTEGER NOT NULL,
        tipo_documento TEXT NOT NULL,
        instituicao_id INTEGER REFERENCES instituicoes(id),
        conta_id INTEGER REFERENCES contas(id),
        cartao_id INTEGER REFERENCES cartoes(id),
        periodo_inicio TEXT,
        periodo_fim TEXT,
        enviado_em TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL DEFAULT 'recebido',
        versao_parser TEXT NOT NULL DEFAULT '',
        caminho_objeto TEXT NOT NULL,
        erro TEXT
    );
    CREATE TABLE lotes (
        id INTEGER PRIMARY KEY,
        arquivo_id INTEGER NOT NULL REFERENCES arquivos(id),
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        confirmado_em TEXT,
        revertido_em TEXT,
        resumo_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE linhas_brutas (
        id INTEGER PRIMARY KEY,
        lote_id INTEGER NOT NULL REFERENCES lotes(id),
        ordem INTEGER NOT NULL,
        origem_ref TEXT NOT NULL,
        conteudo_json TEXT NOT NULL
    );
    CREATE INDEX idx_linhas_lote ON linhas_brutas(lote_id, ordem);
    CREATE TABLE transacoes (
        id INTEGER PRIMARY KEY,
        lote_id INTEGER NOT NULL REFERENCES lotes(id),
        conta_id INTEGER REFERENCES contas(id),
        cartao_id INTEGER REFERENCES cartoes(id),
        fatura_id INTEGER REFERENCES faturas(id),
        data_operacao TEXT NOT NULL,
        data_lancamento TEXT,
        data_compensacao TEXT,
        valor_centavos INTEGER NOT NULL CHECK (valor_centavos >= 0),
        moeda TEXT NOT NULL DEFAULT 'BRL',
        direcao TEXT NOT NULL CHECK (direcao IN ('credito','debito')),
        tipo TEXT NOT NULL,
        descricao_original TEXT NOT NULL DEFAULT '',
        descricao_normalizada TEXT NOT NULL DEFAULT '',
        contraparte TEXT,
        id_banco TEXT,
        documento TEXT,
        saldo_apos_centavos INTEGER,
        parcela_num INTEGER,
        parcela_total INTEGER,
        categoria_id INTEGER REFERENCES categorias(id),
        origem_categoria TEXT,
        confianca REAL,
        status_conciliacao TEXT NOT NULL DEFAULT 'pendente',
        fingerprint TEXT NOT NULL,
        origem_ref TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_trans_fingerprint ON transacoes(fingerprint);
    CREATE INDEX idx_trans_data ON transacoes(data_operacao);
    CREATE INDEX idx_trans_conta ON transacoes(conta_id, data_operacao);
    CREATE INDEX idx_trans_cartao ON transacoes(cartao_id, data_operacao);
    CREATE INDEX idx_trans_categoria ON transacoes(categoria_id);
    CREATE INDEX idx_trans_lote ON transacoes(lote_id);
    CREATE INDEX idx_trans_desc ON transacoes(descricao_normalizada);
    CREATE UNIQUE INDEX uq_trans_id_banco
        ON transacoes(coalesce(conta_id,0), coalesce(cartao_id,0), id_banco)
        WHERE id_banco IS NOT NULL;
    CREATE UNIQUE INDEX uq_trans_fingerprint
        ON transacoes(coalesce(conta_id,0), coalesce(cartao_id,0), fingerprint);
    CREATE TABLE saldos (
        id INTEGER PRIMARY KEY,
        conta_id INTEGER NOT NULL REFERENCES contas(id),
        data TEXT NOT NULL,
        valor_centavos INTEGER NOT NULL,
        origem TEXT NOT NULL DEFAULT 'extrato',
        lote_id INTEGER REFERENCES lotes(id),
        rotulo TEXT NOT NULL DEFAULT '',
        UNIQUE (conta_id, data, origem, rotulo)
    );
    CREATE TABLE categorias (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        pai_id INTEGER REFERENCES categorias(id),
        cor TEXT,
        UNIQUE (nome, pai_id)
    );
    CREATE TABLE regras_categoria (
        id INTEGER PRIMARY KEY,
        prioridade INTEGER NOT NULL DEFAULT 100,
        ativo INTEGER NOT NULL DEFAULT 1,
        campo TEXT NOT NULL CHECK (campo IN ('descricao','contraparte')),
        operador TEXT NOT NULL CHECK (operador IN ('contem','igual','regex','comeca')),
        valor TEXT NOT NULL,
        conta_id INTEGER REFERENCES contas(id),
        cartao_id INTEGER REFERENCES cartoes(id),
        valor_min_centavos INTEGER,
        valor_max_centavos INTEGER,
        categoria_id INTEGER NOT NULL REFERENCES categorias(id),
        criada_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE divisoes_transacao (
        id INTEGER PRIMARY KEY,
        transacao_id INTEGER NOT NULL REFERENCES transacoes(id),
        categoria_id INTEGER NOT NULL REFERENCES categorias(id),
        valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0)
    );
    CREATE INDEX idx_divisoes_trans ON divisoes_transacao(transacao_id);
    CREATE TABLE conciliacoes (
        id INTEGER PRIMARY KEY,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        tipo TEXT NOT NULL DEFAULT 'pagamento_fatura',
        observacao TEXT
    );
    CREATE TABLE conciliacao_itens (
        id INTEGER PRIMARY KEY,
        conciliacao_id INTEGER NOT NULL REFERENCES conciliacoes(id) ON DELETE CASCADE,
        transacao_id INTEGER REFERENCES transacoes(id),
        fatura_id INTEGER REFERENCES faturas(id),
        papel TEXT NOT NULL CHECK (papel IN ('pagamento','obrigacao')),
        valor_centavos INTEGER
    );
    CREATE INDEX idx_concitens_conc ON conciliacao_itens(conciliacao_id);
    CREATE INDEX idx_concitens_trans ON conciliacao_itens(transacao_id);
    CREATE INDEX idx_concitens_fatura ON conciliacao_itens(fatura_id);
    CREATE TABLE auditoria (
        id INTEGER PRIMARY KEY,
        quando TEXT NOT NULL DEFAULT (datetime('now')),
        entidade TEXT NOT NULL,
        entidade_id INTEGER,
        acao TEXT NOT NULL,
        campo TEXT,
        valor_anterior TEXT,
        valor_novo TEXT,
        origem TEXT NOT NULL DEFAULT 'sistema',
        justificativa TEXT
    );
    CREATE INDEX idx_auditoria_entidade ON auditoria(entidade, entidade_id);
    CREATE TABLE duplicidades (
        id INTEGER PRIMARY KEY,
        transacao_id INTEGER NOT NULL REFERENCES transacoes(id),
        candidata_fingerprint TEXT NOT NULL,
        motivo TEXT NOT NULL,
        resolucao TEXT,
        resolvida_em TEXT
    );
    CREATE TABLE config_versoes (
        id INTEGER PRIMARY KEY,
        arquivo_id INTEGER REFERENCES arquivos(id),
        quando TEXT NOT NULL DEFAULT (datetime('now')),
        resumo_json TEXT NOT NULL,
        aprovado_por TEXT
    );
    """,
}


def diretorio_dados() -> Path:
    """Raiz durável dos dados (``CELESTIA_DATA_DIR``; padrão ``~/.celestia-financeiro``).

    Em self-host isso é um diretório real e os dados sobrevivem a reinício e
    deploy. Em serverless o filesystem é efêmero — quem opera declara storage
    durável montado via ``CELESTIA_DATA_DURAVEL=1``; sem isso a API de extratos
    se recusa a gravar (ver ``api.py``), em vez de fingir persistência.
    """
    raiz = os.environ.get("CELESTIA_DATA_DIR")
    caminho = Path(raiz).expanduser() if raiz else Path.home() / ".celestia-financeiro"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def caminho_banco() -> Path:
    return diretorio_dados() / "extratos.db"


def _configura(db: sqlite3.Connection) -> None:
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 5000")


def migrar(db: sqlite3.Connection) -> int:
    """Aplica migrações pendentes; devolve a versão final do schema."""
    atual = db.execute("PRAGMA user_version").fetchone()[0]
    for versao in sorted(_MIGRACOES):
        if versao > atual:
            with db:  # cada migração é atômica
                db.executescript(_MIGRACOES[versao])
                db.execute(f"PRAGMA user_version = {versao}")
            atual = versao
    return atual


@contextmanager
def conexao(caminho: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Conexão pronta (configurada + migrada). Feche sempre via contexto.

    Escritas devem usar ``with db:`` internamente para delimitar transações;
    esta função não abre transação por conta própria.
    """
    db = sqlite3.connect(str(caminho or caminho_banco()))
    try:
        _configura(db)
        migrar(db)
        yield db
    finally:
        db.close()


def registra_auditoria(
    db: sqlite3.Connection,
    entidade: str,
    entidade_id: Optional[int],
    acao: str,
    campo: Optional[str] = None,
    valor_anterior: Optional[str] = None,
    valor_novo: Optional[str] = None,
    origem: str = "sistema",
    justificativa: Optional[str] = None,
) -> None:
    """Grava um evento de auditoria (sempre dentro da transação de quem chama)."""
    db.execute(
        "INSERT INTO auditoria (entidade, entidade_id, acao, campo, valor_anterior,"
        " valor_novo, origem, justificativa) VALUES (?,?,?,?,?,?,?,?)",
        (entidade, entidade_id, acao, campo, valor_anterior, valor_novo, origem, justificativa),
    )
