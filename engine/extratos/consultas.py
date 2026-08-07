"""Consultas de transações — fonte ÚNICA de filtros do módulo de extratos.

Todo lugar que lê ``transacoes`` com filtros (tabela da UI, fluxo de caixa,
painel) passa por :func:`where_transacoes`, para que os números do painel
sejam, por construção, os mesmos da tabela com os mesmos filtros.

Convenções:

* O SQL devolvido referencia a tabela pelo alias ``t`` — quem consome escreve
  ``FROM transacoes t {where}`` e pode acrescentar ``" AND ..."`` livremente,
  porque a cláusula sempre começa com ``WHERE 1=1``.
* Dinheiro é sempre ``int`` de centavos; datas são TEXT ISO no banco e saem
  como chegaram (JSON-prontas).
* Entrada ruim nunca vira exceção: parâmetro inválido é ignorado, ordenação
  fora da lista branca cai no padrão.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Tuple

from .modelos import normaliza_descricao

#: Colunas permitidas em ``ORDER BY`` (lista branca — nunca interpolar livre).
COLUNAS_ORDENACAO: Tuple[str, ...] = (
    "data_operacao", "valor_centavos", "descricao_normalizada", "id",
)

#: Teto de itens por página (o mesmo da API).
POR_PAGINA_MAX = 200

#: Tipos que NÃO entram nas somas de receita/despesa: transferência entre
#: contas próprias não é renda nem gasto, e pagamento de fatura é quitação de
#: um passivo cuja despesa já foi contada nas compras do cartão.
TIPOS_FORA_DO_RESULTADO: Tuple[str, ...] = ("transferencia", "pagamento_fatura")

_CAMPOS_INT = {
    "instituicao_id", "conta_id", "cartao_id", "fatura_id", "categoria_id",
    "valor_min_centavos", "valor_max_centavos", "lote_id", "arquivo_id",
}
#: Aliases aceitos na querystring da API (``valor_min`` → ``valor_min_centavos``).
_ALIASES = {"valor_min": "valor_min_centavos", "valor_max": "valor_max_centavos"}


@dataclass
class Filtros:
    """Filtros combináveis de transações — todos opcionais."""

    periodo_inicio: Optional[str] = None  # ISO AAAA-MM-DD (inclusive)
    periodo_fim: Optional[str] = None  # ISO AAAA-MM-DD (inclusive)
    busca: Optional[str] = None  # descrição/contraparte, case/acento-insensível
    instituicao_id: Optional[int] = None
    conta_id: Optional[int] = None
    cartao_id: Optional[int] = None
    fatura_id: Optional[int] = None
    categoria_id: Optional[int] = None
    tipo: Optional[str] = None
    direcao: Optional[str] = None
    valor_min_centavos: Optional[int] = None
    valor_max_centavos: Optional[int] = None
    moeda: Optional[str] = None
    status_conciliacao: Optional[str] = None
    status_categoria: Optional[str] = None  # categorizada | nao_categorizada
    lote_id: Optional[int] = None
    arquivo_id: Optional[int] = None

    @classmethod
    def from_params(cls, params: Optional[Dict[str, Any]]) -> "Filtros":
        """Constrói a partir de parâmetros de querystring (tudo texto).

        Ignora chaves desconhecidas, valores vazios e inteiros inválidos —
        parâmetro ruim nunca derruba a requisição.
        """
        valores: Dict[str, Any] = {}
        nomes = {campo.name for campo in fields(cls)}
        for chave, bruto in (params or {}).items():
            nome = _ALIASES.get(str(chave), str(chave))
            if nome not in nomes:
                continue
            if isinstance(bruto, (list, tuple)):  # parse_qs entrega listas
                bruto = bruto[0] if bruto else None
            if bruto is None:
                continue
            texto = str(bruto).strip()
            if not texto:
                continue
            if nome in _CAMPOS_INT:
                try:
                    valores[nome] = int(texto)
                except ValueError:
                    continue
            else:
                valores[nome] = texto
        return cls(**valores)


def _escapa_like(termo: str) -> str:
    """Escapa curingas do LIKE (``%`` e ``_``) — busca literal, com ESCAPE ``\\``."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def where_transacoes(filtros: Optional[Filtros]) -> Tuple[str, List[Any]]:
    """Monta a cláusula WHERE parametrizada (alias ``t`` para ``transacoes``).

    Sempre começa com ``WHERE 1=1``, então quem consome pode concatenar
    ``" AND ..."`` sem se preocupar se há ou não filtros ativos.
    """
    f = filtros or Filtros()
    condicoes: List[str] = ["1=1"]
    params: List[Any] = []

    if f.periodo_inicio:
        condicoes.append("t.data_operacao >= ?")
        params.append(f.periodo_inicio)
    if f.periodo_fim:
        condicoes.append("t.data_operacao <= ?")
        params.append(f.periodo_fim)
    if f.busca:
        # O termo passa pela MESMA normalização usada para gravar
        # descricao_normalizada — busca case/acento-insensível na descrição;
        # na contraparte o lower() cobre o caso (o termo já sai sem acento).
        termo = normaliza_descricao(f.busca)
        if termo:
            padrao = f"%{_escapa_like(termo)}%"
            condicoes.append(
                "(t.descricao_normalizada LIKE ? ESCAPE '\\'"
                " OR lower(coalesce(t.contraparte, '')) LIKE ? ESCAPE '\\')"
            )
            params.extend([padrao, padrao])
    if f.instituicao_id is not None:
        condicoes.append(
            "(t.conta_id IN (SELECT id FROM contas WHERE instituicao_id = ?)"
            " OR t.cartao_id IN (SELECT id FROM cartoes WHERE instituicao_id = ?))"
        )
        params.extend([f.instituicao_id, f.instituicao_id])
    if f.conta_id is not None:
        condicoes.append("t.conta_id = ?")
        params.append(f.conta_id)
    if f.cartao_id is not None:
        condicoes.append("t.cartao_id = ?")
        params.append(f.cartao_id)
    if f.fatura_id is not None:
        condicoes.append("t.fatura_id = ?")
        params.append(f.fatura_id)
    if f.categoria_id is not None:
        condicoes.append("t.categoria_id = ?")
        params.append(f.categoria_id)
    if f.tipo:
        condicoes.append("t.tipo = ?")
        params.append(f.tipo)
    if f.direcao:
        condicoes.append("t.direcao = ?")
        params.append(f.direcao)
    if f.valor_min_centavos is not None:
        condicoes.append("t.valor_centavos >= ?")
        params.append(f.valor_min_centavos)
    if f.valor_max_centavos is not None:
        condicoes.append("t.valor_centavos <= ?")
        params.append(f.valor_max_centavos)
    if f.moeda:
        condicoes.append("upper(t.moeda) = ?")
        params.append(f.moeda.upper())
    if f.status_conciliacao:
        condicoes.append("t.status_conciliacao = ?")
        params.append(f.status_conciliacao)
    if f.status_categoria == "nao_categorizada":
        condicoes.append("t.categoria_id IS NULL")
    elif f.status_categoria == "categorizada":
        condicoes.append("t.categoria_id IS NOT NULL")
    if f.lote_id is not None:
        condicoes.append("t.lote_id = ?")
        params.append(f.lote_id)
    if f.arquivo_id is not None:
        condicoes.append("t.lote_id IN (SELECT id FROM lotes WHERE arquivo_id = ?)")
        params.append(f.arquivo_id)

    return "WHERE " + " AND ".join(condicoes), params


def _int_seguro(valor: Any, padrao: int) -> int:
    """Inteiro tolerante — texto inválido vira o padrão, nunca exceção."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def listar_transacoes(
    db: sqlite3.Connection,
    filtros: Optional[Filtros],
    pagina: int = 1,
    por_pagina: int = 50,
    ordenar: str = "data_operacao",
    dir: str = "desc",
) -> Dict[str, Any]:
    """Lista paginada de transações, JSON-pronta.

    Cada item traz todas as colunas da transação (datas já em ISO) mais
    ``arquivo_id``/``arquivo_nome`` (via lote → arquivo, para rastrear até o
    documento original junto com ``origem_ref``) e ``categoria_nome``.
    """
    where, params = where_transacoes(filtros)
    if ordenar not in COLUNAS_ORDENACAO:
        ordenar = "data_operacao"
    sentido = "ASC" if str(dir).lower() == "asc" else "DESC"
    pagina = max(1, _int_seguro(pagina, 1))
    por_pagina = min(max(1, _int_seguro(por_pagina, 50)), POR_PAGINA_MAX)

    total = db.execute(
        f"SELECT COUNT(*) FROM transacoes t {where}", params
    ).fetchone()[0]
    linhas = db.execute(
        f"""
        SELECT t.*, l.arquivo_id AS arquivo_id, a.nome_original AS arquivo_nome,
               c.nome AS categoria_nome
        FROM transacoes t
        LEFT JOIN lotes l ON l.id = t.lote_id
        LEFT JOIN arquivos a ON a.id = l.arquivo_id
        LEFT JOIN categorias c ON c.id = t.categoria_id
        {where}
        ORDER BY t.{ordenar} {sentido}, t.id {sentido}
        LIMIT ? OFFSET ?
        """,
        [*params, por_pagina, (pagina - 1) * por_pagina],
    ).fetchall()

    return {
        "itens": [dict(linha) for linha in linhas],
        "total": int(total),
        "pagina": pagina,
        "por_pagina": por_pagina,
    }


def totais(db: sqlite3.Connection, filtros: Optional[Filtros]) -> Dict[str, int]:
    """Totais do conjunto filtrado — a MESMA fonte que a tabela e o painel.

    Transferências e pagamentos de fatura ficam FORA de entradas/saídas
    (transferência não infla o resultado; pagamento de fatura não é despesa —
    a despesa são as compras do cartão), mas contam em ``quantidade``.
    """
    where, params = where_transacoes(filtros)
    marcadores = ",".join("?" for _ in TIPOS_FORA_DO_RESULTADO)
    linha = db.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.direcao = 'credito'
                             AND t.tipo NOT IN ({marcadores})
                            THEN t.valor_centavos ELSE 0 END), 0) AS entradas,
          COALESCE(SUM(CASE WHEN t.direcao = 'debito'
                             AND t.tipo NOT IN ({marcadores})
                            THEN t.valor_centavos ELSE 0 END), 0) AS saidas,
          COUNT(*) AS quantidade
        FROM transacoes t {where}
        """,
        [*TIPOS_FORA_DO_RESULTADO, *TIPOS_FORA_DO_RESULTADO, *params],
    ).fetchone()
    entradas = int(linha["entradas"])
    saidas = int(linha["saidas"])
    return {
        "entradas_centavos": entradas,
        "saidas_centavos": saidas,
        "resultado_centavos": entradas - saidas,
        "quantidade": int(linha["quantidade"]),
    }
