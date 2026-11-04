"""Painel de extratos — KPIs e séries de gráficos, na mesma fonte da tabela.

Todos os números monetários saem de :mod:`engine.extratos.consultas`
(:func:`consultas.totais` / :func:`consultas.where_transacoes`) com os MESMOS
filtros da tabela de transações — invariante: painel e tabela nunca divergem.

Formato de cada gráfico: dict ChartSeries-like, o mesmo contrato do módulo
``/importar`` (``id``, ``kind``, ``title``, ``x_key``, ``series``, ``data``,
``value_format``, ``note``) — com valores em CENTAVOS e ``value_format``
``"centavos"`` (a UI divide por 100 na exibição). No gráfico
``utilizacao-limite`` o valor é o percentual ×100 (duas casas), pelo mesmo
contrato de inteiros.

Ids fixos (o front procura por eles): ``entradas-vs-saidas``,
``saldo-realizado-projetado``, ``gastos-por-categoria``, ``gastos-por-cartao``,
``evolucao-por-ciclo``, ``top-contrapartes``, ``utilizacao-limite``,
``proximas-faturas``.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import consultas, fluxo as fluxo_mod
from .consultas import Filtros

#: Ids dos gráficos, na ordem em que o painel os devolve.
GRAFICO_IDS: Tuple[str, ...] = (
    "entradas-vs-saidas",
    "saldo-realizado-projetado",
    "gastos-por-categoria",
    "gastos-por-cartao",
    "evolucao-por-ciclo",
    "top-contrapartes",
    "utilizacao-limite",
    "proximas-faturas",
)

#: Tipos somados como "gasto" nos gráficos de despesa (estorno entra negativo).
_TIPOS_GASTO = fluxo_mod.TIPOS_DESPESA_COMPETENCIA  # despesa, compra, tarifa, juros

#: Quantas contrapartes entram em ``top-contrapartes``.
TOP_CONTRAPARTES = 10
#: Quantas faturas entram em ``proximas-faturas``.
PROXIMAS_FATURAS = 10

_ROTULO_SEM_CATEGORIA = "Sem categoria"


def _grafico(
    identificador: str,
    kind: str,
    titulo: str,
    series: List[Dict[str, str]],
    dados: List[Dict[str, Any]],
    x_key: str = "label",
    value_format: str = "centavos",
    note: str = "",
) -> Dict[str, Any]:
    return {
        "id": identificador,
        "kind": kind,
        "title": titulo,
        "x_key": x_key,
        "series": series,
        "data": dados,
        "value_format": value_format,
        "note": note,
    }


# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------


def _grafico_entradas_vs_saidas(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    """Barras mensais de entradas × saídas — MESMA regra de ``consultas.totais``."""
    where, params = consultas.where_transacoes(filtros)
    marcadores = ",".join("?" for _ in consultas.TIPOS_FORA_DO_RESULTADO)
    linhas = db.execute(
        f"""
        SELECT substr(t.data_operacao, 1, 7) AS mes,
               COALESCE(SUM(CASE WHEN t.direcao = 'credito'
                                  AND t.tipo NOT IN ({marcadores})
                                 THEN t.valor_centavos ELSE 0 END), 0) AS entradas,
               COALESCE(SUM(CASE WHEN t.direcao = 'debito'
                                  AND t.tipo NOT IN ({marcadores})
                                 THEN t.valor_centavos ELSE 0 END), 0) AS saidas
        FROM transacoes t {where}
        GROUP BY mes ORDER BY mes
        """,
        [
            *consultas.TIPOS_FORA_DO_RESULTADO,
            *consultas.TIPOS_FORA_DO_RESULTADO,
            *params,
        ],
    ).fetchall()
    dados = [
        {
            "label": fluxo_mod._rotulo_mes(linha["mes"]),
            "mes": linha["mes"],
            "entradas_centavos": int(linha["entradas"]),
            "saidas_centavos": int(linha["saidas"]),
        }
        for linha in linhas
    ]
    return _grafico(
        "entradas-vs-saidas",
        "bar",
        "Entradas × Saídas por mês",
        [
            {"key": "entradas_centavos", "label": "Entradas"},
            {"key": "saidas_centavos", "label": "Saídas"},
        ],
        dados,
        note="Sem transferências e pagamentos de fatura — os mesmos totais da tabela.",
    )


def _grafico_saldo_realizado_projetado(
    realizado: Dict[str, Any], projetado: Dict[str, Any]
) -> Dict[str, Any]:
    """Linha com 2 séries: saldo acumulado realizado × projetado."""
    saldo_realizado = {
        ponto["mes"]: ponto["saldo_acumulado_centavos"]
        for ponto in realizado.get("serie", [])
    }
    dados = [
        {
            "label": ponto["label"],
            "mes": ponto["mes"],
            "realizado_centavos": saldo_realizado.get(ponto["mes"]),
            "projetado_centavos": ponto["saldo_acumulado_centavos"],
        }
        for ponto in projetado.get("serie", [])
    ]
    return _grafico(
        "saldo-realizado-projetado",
        "line",
        "Saldo acumulado: realizado × projetado",
        [
            {"key": "realizado_centavos", "label": "Realizado"},
            {"key": "projetado_centavos", "label": "Projetado"},
        ],
        dados,
        note="Acumulado relativo ao início do horizonte; projeção inclui faturas não pagas.",
    )


def _grafico_gastos_por_categoria(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    """Donut de gastos por categoria — divisões substituem a categoria da transação."""
    where, params = consultas.where_transacoes(filtros)
    marcadores = ",".join("?" for _ in _TIPOS_GASTO)
    somas: Dict[Optional[int], Dict[str, Any]] = {}

    # Transações SEM divisão: valem a própria categoria (estorno reduz).
    sem_divisao = db.execute(
        f"""
        SELECT t.categoria_id AS categoria_id,
               COALESCE(c.nome, ?) AS nome,
               SUM(CASE WHEN t.tipo = 'estorno' THEN -t.valor_centavos
                        ELSE t.valor_centavos END) AS total
        FROM transacoes t
        LEFT JOIN categorias c ON c.id = t.categoria_id
        {where}
          AND t.tipo IN ({marcadores}, 'estorno')
          AND NOT EXISTS (SELECT 1 FROM divisoes_transacao d
                          WHERE d.transacao_id = t.id)
        GROUP BY t.categoria_id
        """,
        [_ROTULO_SEM_CATEGORIA, *params, *_TIPOS_GASTO],
    ).fetchall()
    # Transações COM divisão: as partes substituem a categoria da transação.
    com_divisao = db.execute(
        f"""
        SELECT d.categoria_id AS categoria_id,
               COALESCE(c.nome, ?) AS nome,
               SUM(d.valor_centavos) AS total
        FROM divisoes_transacao d
        JOIN transacoes t ON t.id = d.transacao_id
        LEFT JOIN categorias c ON c.id = d.categoria_id
        {where}
          AND t.tipo IN ({marcadores})
        GROUP BY d.categoria_id
        """,
        [_ROTULO_SEM_CATEGORIA, *params, *_TIPOS_GASTO],
    ).fetchall()
    for linha in [*sem_divisao, *com_divisao]:
        chave = linha["categoria_id"]
        item = somas.setdefault(
            chave, {"categoria_id": chave, "label": linha["nome"], "value": 0}
        )
        item["value"] += int(linha["total"] or 0)

    dados = sorted(
        (item for item in somas.values() if item["value"] != 0),
        key=lambda item: item["value"],
        reverse=True,
    )
    return _grafico(
        "gastos-por-categoria",
        "donut",
        "Gastos por categoria",
        [{"key": "value", "label": "Gastos"}],
        dados,
        note="Transações divididas contam pelas partes; sem categoria é fatia própria.",
    )


def _grafico_gastos_por_cartao(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    where, params = consultas.where_transacoes(filtros)
    marcadores = ",".join("?" for _ in _TIPOS_GASTO)
    linhas = db.execute(
        f"""
        SELECT t.cartao_id AS cartao_id, k.nome AS nome,
               SUM(CASE WHEN t.tipo = 'estorno' THEN -t.valor_centavos
                        ELSE t.valor_centavos END) AS total
        FROM transacoes t
        JOIN cartoes k ON k.id = t.cartao_id
        {where}
          AND t.cartao_id IS NOT NULL
          AND t.tipo IN ({marcadores}, 'estorno')
        GROUP BY t.cartao_id
        ORDER BY total DESC
        """,
        [*params, *_TIPOS_GASTO],
    ).fetchall()
    dados = [
        {
            "label": linha["nome"],
            "cartao_id": int(linha["cartao_id"]),
            "value": int(linha["total"] or 0),
        }
        for linha in linhas
    ]
    return _grafico(
        "gastos-por-cartao",
        "bar",
        "Gastos por cartão",
        [{"key": "value", "label": "Gastos"}],
        dados,
    )


def _grafico_evolucao_por_ciclo(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    """Barra por competência de fatura, uma série por cartão."""
    f = filtros or Filtros()
    condicoes = ["1=1"]
    params: List[Any] = []
    if f.cartao_id is not None:
        condicoes.append("f.cartao_id = ?")
        params.append(f.cartao_id)
    if f.instituicao_id is not None:
        condicoes.append("k.instituicao_id = ?")
        params.append(f.instituicao_id)
    faturas = db.execute(
        f"""
        SELECT f.id, f.competencia, f.valor_centavos, f.pago_centavos,
               f.cartao_id, k.nome AS cartao_nome
        FROM faturas f JOIN cartoes k ON k.id = f.cartao_id
        WHERE {' AND '.join(condicoes)}
        ORDER BY f.competencia, f.cartao_id
        """,
        params,
    ).fetchall()

    por_competencia: Dict[str, Dict[str, Any]] = {}
    cartoes_vistos: Dict[int, str] = {}
    for fatura in faturas:
        total = fluxo_mod.valor_total_fatura(db, fatura)
        if total is None:
            continue  # fatura sem valor e sem compras não tem o que mostrar
        chave_serie = f"cartao_{fatura['cartao_id']}"
        cartoes_vistos[int(fatura["cartao_id"])] = fatura["cartao_nome"]
        ponto = por_competencia.setdefault(
            fatura["competencia"],
            {"label": fatura["competencia"], "mes": fatura["competencia"]},
        )
        ponto[chave_serie] = ponto.get(chave_serie, 0) + total

    series = [
        {"key": f"cartao_{cartao_id}", "label": nome}
        for cartao_id, nome in sorted(cartoes_vistos.items())
    ]
    dados = [por_competencia[chave] for chave in sorted(por_competencia)]
    return _grafico(
        "evolucao-por-ciclo",
        "bar",
        "Evolução por ciclo de fatura",
        series,
        dados,
        note="Valor da fatura por competência; sem valor declarado usa a soma das compras.",
    )


def _grafico_top_contrapartes(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    where, params = consultas.where_transacoes(filtros)
    marcadores = ",".join("?" for _ in _TIPOS_GASTO)
    linhas = db.execute(
        f"""
        SELECT t.contraparte AS contraparte,
               SUM(CASE WHEN t.tipo = 'estorno' THEN -t.valor_centavos
                        ELSE t.valor_centavos END) AS total
        FROM transacoes t
        {where}
          AND t.tipo IN ({marcadores}, 'estorno')
          AND t.contraparte IS NOT NULL AND t.contraparte != ''
        GROUP BY lower(t.contraparte)
        ORDER BY total DESC
        LIMIT ?
        """,
        [*params, *_TIPOS_GASTO, TOP_CONTRAPARTES],
    ).fetchall()
    dados = [
        {"label": linha["contraparte"], "value": int(linha["total"] or 0)}
        for linha in linhas
        if int(linha["total"] or 0) > 0
    ]
    return _grafico(
        "top-contrapartes",
        "bar",
        "Maiores contrapartes por despesa",
        [{"key": "value", "label": "Despesas"}],
        dados,
    )


def _grafico_utilizacao_limite(
    db: sqlite3.Connection, filtros: Optional[Filtros]
) -> Dict[str, Any]:
    """% do limite ocupado por faturas não quitadas, por cartão com limite."""
    f = filtros or Filtros()
    condicoes = ["k.limite_total_centavos IS NOT NULL", "k.limite_total_centavos > 0"]
    params: List[Any] = []
    if f.cartao_id is not None:
        condicoes.append("k.id = ?")
        params.append(f.cartao_id)
    if f.instituicao_id is not None:
        condicoes.append("k.instituicao_id = ?")
        params.append(f.instituicao_id)
    cartoes = db.execute(
        f"SELECT k.id, k.nome, k.limite_total_centavos FROM cartoes k"
        f" WHERE {' AND '.join(condicoes)} ORDER BY k.nome",
        params,
    ).fetchall()

    dados: List[Dict[str, Any]] = []
    for cartao in cartoes:
        faturas = db.execute(
            "SELECT f.id, f.valor_centavos, f.pago_centavos FROM faturas f"
            " WHERE f.cartao_id = ? AND f.status IN ('aberta','fechada','parcial')",
            (cartao["id"],),
        ).fetchall()
        usado = 0
        for fatura in faturas:
            restante = fluxo_mod.valor_restante_fatura(db, fatura)
            if restante is not None and restante > 0:
                usado += restante
        limite = int(cartao["limite_total_centavos"])
        # Percentual ×100 (duas casas) em inteiro — a UI divide por 100.
        percentual_x100 = (usado * 10000 + limite // 2) // limite
        dados.append(
            {
                "label": cartao["nome"],
                "cartao_id": int(cartao["id"]),
                "value": percentual_x100,
                "usado_centavos": usado,
                "limite_centavos": limite,
            }
        )
    return _grafico(
        "utilizacao-limite",
        "bar",
        "Utilização do limite por cartão",
        [{"key": "value", "label": "% do limite"}],
        dados,
        note="value = percentual ×100 (ex.: 1350 = 13,50%); faturas abertas/fechadas/parciais.",
    )


def _grafico_proximas_faturas(
    db: sqlite3.Connection, filtros: Optional[Filtros], hoje: date
) -> Dict[str, Any]:
    faturas = [
        fatura
        for fatura in fluxo_mod.faturas_nao_pagas(db, filtros)
        if str(fatura["vence_em"]) >= hoje.isoformat()
    ][:PROXIMAS_FATURAS]
    dados: List[Dict[str, Any]] = []
    for fatura in faturas:
        restante = fluxo_mod.valor_restante_fatura(db, fatura)
        dados.append(
            {
                "label": fatura["cartao_nome"],
                "data": fatura["vence_em"],
                "cartao": fatura["cartao_nome"],
                "cartao_id": int(fatura["cartao_id"]),
                "fatura_id": int(fatura["id"]),
                "competencia": fatura["competencia"],
                "valor_previsto_centavos": restante,
                "status": fatura["status"],
            }
        )
    return _grafico(
        "proximas-faturas",
        "tabela",
        "Próximas faturas",
        [{"key": "valor_previsto_centavos", "label": "Valor previsto"}],
        dados,
        note="Faturas não pagas com vencimento a partir de hoje, em ordem de vencimento.",
    )


# --------------------------------------------------------------------------
# KPIs
# --------------------------------------------------------------------------


def _kpi_centavos(id_: str, label: str, valor: int, hint: str = "") -> Dict[str, Any]:
    return {"id": id_, "label": label, "valor_centavos": int(valor),
            "formato": "centavos", "hint": hint}


def _kpi_numero(id_: str, label: str, valor: int, hint: str = "") -> Dict[str, Any]:
    return {"id": id_, "label": label, "valor": int(valor),
            "formato": "numero", "hint": hint}


def painel(
    db: sqlite3.Connection,
    filtros: Optional[Filtros],
    *,
    hoje: Optional[date] = None,
) -> Dict[str, Any]:
    """KPIs + gráficos do painel, todos na mesma fonte de filtros da tabela.

    ``hoje`` existe para testes determinísticos; em produção é a data atual.
    """
    f = filtros or Filtros()
    hoje = hoje or date.today()

    totais = consultas.totais(db, f)
    realizado = fluxo_mod.fluxo(db, "realizado", f)
    projetado = fluxo_mod.fluxo(db, "projetado", f)
    serie_projetada = projetado.get("serie", [])
    saldo_projetado = (
        serie_projetada[-1]["saldo_acumulado_centavos"] if serie_projetada else 0
    )

    # Faturas a vencer nos próximos 30 dias (valor previsto restante).
    limite_30d = (hoje + timedelta(days=30)).isoformat()
    a_vencer_valor = 0
    a_vencer_qtd = 0
    for fatura in fluxo_mod.faturas_nao_pagas(db, f):
        vence = str(fatura["vence_em"])
        if not (hoje.isoformat() <= vence <= limite_30d):
            continue
        restante = fluxo_mod.valor_restante_fatura(db, fatura)
        if restante is not None and restante > 0:
            a_vencer_valor += restante
            a_vencer_qtd += 1

    where, params = consultas.where_transacoes(f)
    nao_categorizadas = int(
        db.execute(
            f"""
            SELECT COUNT(*) FROM transacoes t {where}
              AND t.categoria_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM divisoes_transacao d
                              WHERE d.transacao_id = t.id)
            """,
            params,
        ).fetchone()[0]
    )
    pendencias_conciliacao = int(
        db.execute(
            f"""
            SELECT COUNT(*) FROM transacoes t {where}
              AND t.tipo = 'pagamento_fatura'
              AND t.status_conciliacao IN ('pendente', 'divergente')
            """,
            params,
        ).fetchone()[0]
    )
    duplicidades_abertas = int(
        db.execute(
            "SELECT COUNT(*) FROM duplicidades WHERE resolucao IS NULL"
        ).fetchone()[0]
    )

    kpis = [
        _kpi_centavos(
            "entradas", "Entradas", totais["entradas_centavos"],
            "Créditos no período (sem transferências e pagamentos de fatura)",
        ),
        _kpi_centavos(
            "saidas", "Saídas", totais["saidas_centavos"],
            "Débitos no período (sem transferências e pagamentos de fatura)",
        ),
        _kpi_centavos(
            "resultado", "Resultado", totais["resultado_centavos"],
            "Entradas − saídas com os mesmos filtros da tabela",
        ),
        _kpi_centavos(
            "saldo-projetado", "Saldo projetado", saldo_projetado,
            "Saldo acumulado ao fim do horizonte, incluindo faturas não pagas",
        ),
        _kpi_centavos(
            "faturas-a-vencer-30d", "Faturas a vencer (30d)", a_vencer_valor,
            f"{a_vencer_qtd} fatura(s) vencendo nos próximos 30 dias",
        ),
        _kpi_numero(
            "nao-categorizadas", "Não categorizadas", nao_categorizadas,
            "Transações sem categoria e sem divisão",
        ),
        _kpi_numero(
            "pendencias-conciliacao", "Pendências de conciliação",
            pendencias_conciliacao,
            "Pagamentos de fatura ainda não conciliados",
        ),
        _kpi_numero(
            "duplicidades-abertas", "Duplicidades abertas", duplicidades_abertas,
            "Possíveis duplicidades aguardando revisão",
        ),
    ]

    graficos = [
        _grafico_entradas_vs_saidas(db, f),
        _grafico_saldo_realizado_projetado(realizado, projetado),
        _grafico_gastos_por_categoria(db, f),
        _grafico_gastos_por_cartao(db, f),
        _grafico_evolucao_por_ciclo(db, f),
        _grafico_top_contrapartes(db, f),
        _grafico_utilizacao_limite(db, f),
        _grafico_proximas_faturas(db, f, hoje),
    ]

    avisos = [*realizado.get("avisos", []), *projetado.get("avisos", [])]
    return {"kpis": kpis, "graficos": graficos, "avisos": avisos}
