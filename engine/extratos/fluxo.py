"""Fluxo de caixa em três visões — competência, realizado e projetado.

Regras contábeis (as mesmas da especificação, na íntegra):

* **Competência** — o gasto pertence ao mês da operação (``data_operacao``):
  receitas = ``tipo='receita'``; despesas = despesa + compra + tarifa + juros,
  com estornos como REDUÇÃO de despesa. Compras de cartão contam aqui;
  transferências, pagamentos de fatura e ajustes ficam fora.
* **Realizado** — só o caixa bancário efetivo (transações de CONTA, isto é,
  ``cartao_id IS NULL``): créditos − débitos por mês. Pagamento de fatura
  conta como débito aqui; compras de cartão NÃO (senão a despesa duplicaria).
* **Projetado** — realizado + cada fatura não paga lançada como débito na
  data de vencimento contra a conta pagadora. Fatura parcial projeta só o
  restante (``valor − pago``); fatura sem valor conhecido usa a soma das
  compras vinculadas; sem valor e sem compras → pulada, com aviso. Fatura
  paga NUNCA projeta — o débito real do pagamento já está no realizado.

Série mensal: ``{mes, entradas_centavos, saidas_centavos, resultado_centavos,
saldo_acumulado_centavos, projetado}``, com meses contínuos (buracos viram
zeros) e ``saldo_acumulado_centavos`` cumulativo a partir de zero — é um
acumulado RELATIVO ao início do horizonte, não o saldo bancário absoluto.

Módulo só de leitura: nunca escreve no banco, nunca levanta exceção por dado
ruim (vira aviso).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import consultas
from .consultas import Filtros

#: Visões suportadas.
VISOES: Tuple[str, ...] = ("competencia", "realizado", "projetado")

#: Tipos que compõem "despesa" na visão competência (estorno entra negativo).
TIPOS_DESPESA_COMPETENCIA: Tuple[str, ...] = ("despesa", "compra", "tarifa", "juros")

#: Status de fatura que ainda ocupam caixa futuro (tudo que não está paga).
STATUS_FATURA_NAO_PAGA_SQL = "f.status != 'paga'"

_MESES_PT: Tuple[str, ...] = (
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
)


# --------------------------------------------------------------------------
# Auxiliares de mês (chave "AAAA-MM")
# --------------------------------------------------------------------------


def _rotulo_mes(mes: str) -> str:
    """``"2026-08"`` → ``"ago/2026"``; qualquer coisa fora do padrão volta como veio."""
    try:
        ano, numero = mes.split("-")
        return f"{_MESES_PT[int(numero) - 1]}/{ano}"
    except (ValueError, IndexError):
        return mes


def _intervalo_meses(inicio: str, fim: str) -> List[str]:
    """Todos os meses de ``inicio`` a ``fim`` inclusive (chaves ``AAAA-MM``)."""
    try:
        ano_i, mes_i = (int(parte) for parte in inicio.split("-")[:2])
        ano_f, mes_f = (int(parte) for parte in fim.split("-")[:2])
    except (ValueError, IndexError):
        return [inicio] if inicio == fim else [inicio, fim]
    meses: List[str] = []
    ano, mes = ano_i, mes_i
    while (ano, mes) <= (ano_f, mes_f) and len(meses) < 1200:  # trava de sanidade
        meses.append(f"{ano:04d}-{mes:02d}")
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    return meses


# --------------------------------------------------------------------------
# Faturas — valor previsto e restante (reutilizado pelo painel)
# --------------------------------------------------------------------------


def valor_total_fatura(db: sqlite3.Connection, fatura: Mapping[str, Any]) -> Optional[int]:
    """Valor total da fatura: o declarado, ou a soma das compras vinculadas.

    Estornos vinculados reduzem a soma. Sem valor declarado e sem transações
    vinculadas devolve ``None`` — quem chama decide avisar/pular.
    """
    valor = fatura["valor_centavos"]
    if valor is not None:
        return int(valor)
    linha = db.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN tipo = 'estorno' THEN -valor_centavos
                                 ELSE valor_centavos END), 0) AS soma,
               COUNT(*) AS quantas
        FROM transacoes
        WHERE fatura_id = ? AND tipo != 'pagamento_fatura'
        """,
        (fatura["id"],),
    ).fetchone()
    if not linha or not linha["quantas"]:
        return None
    return int(linha["soma"])


def valor_restante_fatura(db: sqlite3.Connection, fatura: Mapping[str, Any]) -> Optional[int]:
    """Quanto ainda falta pagar da fatura (total − pago); ``None`` se o total é desconhecido."""
    total = valor_total_fatura(db, fatura)
    if total is None:
        return None
    return total - int(fatura["pago_centavos"] or 0)


def faturas_nao_pagas(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> List[sqlite3.Row]:
    """Faturas com ``status != 'paga'`` respeitando os filtros aplicáveis.

    Filtros honrados: ``cartao_id`` (a fatura do cartão), ``conta_id`` (a conta
    pagadora), ``instituicao_id`` (do cartão) e ``periodo_inicio/fim`` sobre o
    vencimento. Os demais filtros são de transação e não se aplicam a faturas.
    """
    f = filtros or Filtros()
    condicoes = [STATUS_FATURA_NAO_PAGA_SQL]
    params: List[Any] = []
    if f.cartao_id is not None:
        condicoes.append("f.cartao_id = ?")
        params.append(f.cartao_id)
    if f.conta_id is not None:
        condicoes.append("k.conta_pagadora_id = ?")
        params.append(f.conta_id)
    if f.instituicao_id is not None:
        condicoes.append("k.instituicao_id = ?")
        params.append(f.instituicao_id)
    if f.periodo_inicio:
        condicoes.append("f.vence_em >= ?")
        params.append(f.periodo_inicio)
    if f.periodo_fim:
        condicoes.append("f.vence_em <= ?")
        params.append(f.periodo_fim)
    return db.execute(
        f"""
        SELECT f.id, f.cartao_id, f.competencia, f.fecha_em, f.vence_em,
               f.valor_centavos, f.pago_centavos, f.status,
               k.nome AS cartao_nome, k.conta_pagadora_id,
               co.nome AS conta_nome
        FROM faturas f
        JOIN cartoes k ON k.id = f.cartao_id
        LEFT JOIN contas co ON co.id = k.conta_pagadora_id
        WHERE {' AND '.join(condicoes)}
        ORDER BY f.vence_em, f.id
        """,
        params,
    ).fetchall()


# --------------------------------------------------------------------------
# Agregações mensais por visão
# --------------------------------------------------------------------------


def _mensal_competencia(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Dict[str, int]]:
    where, params = consultas.where_transacoes(filtros)
    marcadores = ",".join("?" for _ in TIPOS_DESPESA_COMPETENCIA)
    linhas = db.execute(
        f"""
        SELECT substr(t.data_operacao, 1, 7) AS mes,
               COALESCE(SUM(CASE WHEN t.tipo = 'receita'
                                 THEN t.valor_centavos ELSE 0 END), 0) AS entradas,
               COALESCE(SUM(CASE WHEN t.tipo IN ({marcadores})
                                 THEN t.valor_centavos
                                 WHEN t.tipo = 'estorno' THEN -t.valor_centavos
                                 ELSE 0 END), 0) AS saidas
        FROM transacoes t {where}
        GROUP BY mes ORDER BY mes
        """,
        [*TIPOS_DESPESA_COMPETENCIA, *params],
    ).fetchall()
    return {
        linha["mes"]: {"entradas": int(linha["entradas"]), "saidas": int(linha["saidas"])}
        for linha in linhas
    }


def _mensal_realizado(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Dict[str, int]]:
    where, params = consultas.where_transacoes(filtros)
    linhas = db.execute(
        f"""
        SELECT substr(t.data_operacao, 1, 7) AS mes,
               COALESCE(SUM(CASE WHEN t.direcao = 'credito'
                                 THEN t.valor_centavos ELSE 0 END), 0) AS entradas,
               COALESCE(SUM(CASE WHEN t.direcao = 'debito'
                                 THEN t.valor_centavos ELSE 0 END), 0) AS saidas
        FROM transacoes t {where} AND t.cartao_id IS NULL
        GROUP BY mes ORDER BY mes
        """,
        params,
    ).fetchall()
    return {
        linha["mes"]: {"entradas": int(linha["entradas"]), "saidas": int(linha["saidas"])}
        for linha in linhas
    }


# --------------------------------------------------------------------------
# Entrada principal
# --------------------------------------------------------------------------


def fluxo(
    db: sqlite3.Connection,
    visao: str,
    filtros: Optional[Filtros],
    granularidade: str = "mensal",
) -> Dict[str, Any]:
    """Série de fluxo de caixa na visão pedida (ver regras no topo do módulo).

    Devolve ``{visao, granularidade, serie, projecoes, avisos}`` — ``serie`` é
    a lista mensal contínua; ``projecoes`` detalha cada fatura projetada (só na
    visão projetado); dado ruim vira aviso, nunca exceção.
    """
    avisos: List[str] = []
    if granularidade != "mensal":
        avisos.append(
            f"granularidade '{granularidade}' não suportada — usando mensal"
        )
        granularidade = "mensal"
    if visao not in VISOES:
        return {
            "visao": visao,
            "granularidade": granularidade,
            "serie": [],
            "projecoes": [],
            "avisos": [f"visão desconhecida: '{visao}' (use {', '.join(VISOES)})"],
        }

    if visao == "competencia":
        buckets = _mensal_competencia(db, filtros)
    else:
        buckets = _mensal_realizado(db, filtros)

    meses_projetados: set = set()
    projecoes: List[Dict[str, Any]] = []

    if visao == "projetado":
        for fatura in faturas_nao_pagas(db, filtros):
            restante = valor_restante_fatura(db, fatura)
            if restante is None:
                avisos.append(
                    f"fatura {fatura['competencia']} de {fatura['cartao_nome']}: "
                    "sem valor conhecido e sem compras vinculadas — fora da projeção"
                )
                continue
            if restante <= 0:
                continue  # já coberta por pagamentos registrados
            mes = str(fatura["vence_em"])[:7]
            bucket = buckets.setdefault(mes, {"entradas": 0, "saidas": 0})
            bucket["saidas"] += restante
            meses_projetados.add(mes)
            projecoes.append(
                {
                    "fatura_id": int(fatura["id"]),
                    "cartao_id": int(fatura["cartao_id"]),
                    "cartao": fatura["cartao_nome"],
                    "competencia": fatura["competencia"],
                    "conta_id": fatura["conta_pagadora_id"],
                    "conta": fatura["conta_nome"] or "sem conta",
                    "mes": mes,
                    "vence_em": fatura["vence_em"],
                    "valor_centavos": restante,
                }
            )

    serie: List[Dict[str, Any]] = []
    if buckets:
        meses = sorted(buckets)
        acumulado = 0
        for mes in _intervalo_meses(meses[0], meses[-1]):
            bucket = buckets.get(mes, {"entradas": 0, "saidas": 0})
            resultado = bucket["entradas"] - bucket["saidas"]
            acumulado += resultado
            serie.append(
                {
                    "mes": mes,
                    "label": _rotulo_mes(mes),
                    "entradas_centavos": bucket["entradas"],
                    "saidas_centavos": bucket["saidas"],
                    "resultado_centavos": resultado,
                    "saldo_acumulado_centavos": acumulado,
                    "projetado": mes in meses_projetados,
                }
            )

    return {
        "visao": visao,
        "granularidade": granularidade,
        "serie": serie,
        "projecoes": projecoes,
        "avisos": avisos,
    }
