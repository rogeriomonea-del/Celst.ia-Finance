"""Testes de consultas, fluxo (3 visões) e painel — banco 100% sintético.

O cenário semeado cobre: 2 instituições, 2 contas, 2 cartões (com limite,
fechamento e vencimento), faturas paga/aberta/parcial/sem-valor, ~27
transações com todos os tipos (receita, despesa, transferência em par,
compra, estorno em conta e cartão, tarifa, juros, pagamento de fatura
conciliado e pendente, ajuste em USD, parcelas), divisão de transação e uma
duplicidade aberta. Nenhum dado real.

Data de referência fixa: 2026-08-07 (mesma do restante da suíte).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, Iterator, List, Optional

import pytest

from engine.extratos import consultas, fluxo as fluxo_mod, modelos, painel as painel_mod, store
from engine.extratos.consultas import Filtros

HOJE = date(2026, 8, 7)

# ---------------------------------------------------------------------------
# Valores esperados (centavos), calculados à mão a partir do seed abaixo.
# ---------------------------------------------------------------------------

TOTAIS_ENTRADAS = 1_635_123
TOTAIS_SAIDAS = 714_490
TOTAIS_QUANTIDADE = 27

COMPETENCIA_ESPERADA = {
    "2026-06": (620_000, 380_000),
    "2026-07": (500_000, 232_990),
    "2026-08": (500_000, 86_500),
}
REALIZADO_ESPERADO = {
    "2026-06": (620_000, 230_000),
    "2026-07": (600_000, 322_990),
    "2026-08": (505_123, 31_500),
}
PROJETADO_ESPERADO = {
    "2026-06": (620_000, 230_000),
    "2026-07": (600_000, 322_990),
    "2026-08": (505_123, 81_500),   # +50.000 projetados da fatura Beta parcial
    "2026-09": (0, 170_000),        # 135.000 (Alfa) + 35.000 (Beta sem valor)
}
SALDO_PROJETADO_FINAL = 920_633

COMPRAS_CARTAO_LIQUIDAS = 400_000   # 285.000 (Alfa) + 115.000 (Beta)
PAGAMENTOS_FATURA = 180_000         # 150.000 conciliado + 30.000 parcial pendente
TRANSFERENCIA_SAIDA = 100_000
ESTORNO_CONTA = 5_000
TRANSFERENCIA_ENTRADA = 100_000
AJUSTE_CREDITO = 123


@pytest.fixture(autouse=True)
def _dados_em_tmp(tmp_path, monkeypatch) -> None:
    """Nenhum teste pode encostar no diretório de dados real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))


def _insere_transacao(
    db: sqlite3.Connection,
    *,
    lote_id: int,
    data: date,
    valor: int,
    direcao: str,
    tipo: str,
    descricao: str,
    conta_id: Optional[int] = None,
    cartao_id: Optional[int] = None,
    fatura_id: Optional[int] = None,
    contraparte: Optional[str] = None,
    moeda: str = "BRL",
    categoria_id: Optional[int] = None,
    status_conciliacao: str = "pendente",
    ocorrencia: int = 1,
    parcela_num: Optional[int] = None,
    parcela_total: Optional[int] = None,
    id_banco: Optional[str] = None,
) -> int:
    chave = f"conta:{conta_id}" if conta_id else f"cartao:{cartao_id}"
    fingerprint = modelos.fingerprint_transacao(
        chave, data, valor, moeda, descricao, None, ocorrencia
    )
    cursor = db.execute(
        """
        INSERT INTO transacoes (
            lote_id, conta_id, cartao_id, fatura_id, data_operacao,
            valor_centavos, moeda, direcao, tipo, descricao_original,
            descricao_normalizada, contraparte, id_banco, parcela_num,
            parcela_total, categoria_id, status_conciliacao, fingerprint,
            origem_ref
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            lote_id, conta_id, cartao_id, fatura_id, data.isoformat(),
            valor, moeda, direcao, tipo, descricao,
            modelos.normaliza_descricao(descricao), contraparte, id_banco,
            parcela_num, parcela_total, categoria_id, status_conciliacao,
            fingerprint, f"linha {ocorrencia}",
        ),
    )
    return int(cursor.lastrowid)


def _semeia(db: sqlite3.Connection) -> None:
    """Cenário sintético completo (ver docstring do módulo)."""
    with db:
        db.execute(
            "INSERT INTO instituicoes (id, nome, tipo) VALUES"
            " (1, 'Banco Azul', 'banco'), (2, 'Banco Verde', 'banco')"
        )
        db.execute(
            "INSERT INTO contas (id, instituicao_id, nome) VALUES"
            " (1, 1, 'Conta Corrente Azul'), (2, 2, 'Conta Corrente Verde')"
        )
        db.execute(
            "INSERT INTO cartoes (id, instituicao_id, nome, conta_pagadora_id,"
            " dia_fechamento, dia_vencimento, limite_total_centavos) VALUES"
            " (1, 1, 'Cartao Alfa', 1, 28, 5, 1000000),"
            " (2, 2, 'Cartao Beta', NULL, 10, 20, 500000)"
        )
        db.execute(
            "INSERT INTO categorias (id, nome) VALUES"
            " (1, 'Renda'), (2, 'Moradia'), (3, 'Alimentação'), (4, 'Lazer')"
        )
        db.execute(
            "INSERT INTO arquivos (id, sha256, nome_original, mime_real,"
            " tamanho, tipo_documento, caminho_objeto) VALUES"
            " (1, ?, 'extrato-sintetico.ofx', 'application/x-ofx', 10,"
            "  'extrato_conta', 'aa/a'),"
            " (2, ?, 'fatura-sintetica.csv', 'text/csv', 10,"
            "  'fatura_cartao', 'bb/b')",
            ("a" * 64, "b" * 64),
        )
        db.execute(
            "INSERT INTO lotes (id, arquivo_id) VALUES (1, 1), (2, 2)"
        )
        # Faturas: F1 paga · F2 aberta (valor conhecido) · F3 parcial ·
        # F4 aberta sem valor (usa compras) · F5 aberta sem valor e sem compras.
        db.execute(
            "INSERT INTO faturas (id, cartao_id, competencia, fecha_em,"
            " vence_em, valor_centavos, pago_centavos, status) VALUES"
            " (1, 1, '2026-07', '2026-06-28', '2026-07-05', 150000, 150000, 'paga'),"
            " (2, 1, '2026-09', '2026-08-28', '2026-09-05', 135000, 0, 'aberta'),"
            " (3, 2, '2026-08', '2026-08-10', '2026-08-20', 80000, 30000, 'parcial'),"
            " (4, 2, '2026-09', '2026-09-10', '2026-09-20', NULL, 0, 'aberta'),"
            " (5, 2, '2026-10', '2026-10-10', '2026-10-20', NULL, 0, 'aberta')"
        )

        # ---- Conta 1 (Banco Azul) — lote 1 ------------------------------
        t = _insere_transacao
        t(db, lote_id=1, conta_id=1, data=date(2026, 6, 5), valor=500_000,
          direcao="credito", tipo="receita", descricao="Salario mensal",
          contraparte="Empresa X", categoria_id=1, id_banco="FIT001")       # t1
        t(db, lote_id=1, conta_id=1, data=date(2026, 7, 6), valor=500_000,
          direcao="credito", tipo="receita", descricao="Salario mensal",
          contraparte="Empresa X", categoria_id=1)                          # t2
        t(db, lote_id=1, conta_id=1, data=date(2026, 8, 5), valor=500_000,
          direcao="credito", tipo="receita", descricao="Salario mensal",
          contraparte="Empresa X", categoria_id=1)                          # t3
        t(db, lote_id=1, conta_id=1, data=date(2026, 6, 10), valor=200_000,
          direcao="debito", tipo="despesa", descricao="Aluguel apto",
          contraparte="Imobiliaria Y", categoria_id=2)                      # t4
        t(db, lote_id=1, conta_id=1, data=date(2026, 7, 12), valor=35_000,
          direcao="debito", tipo="despesa", descricao="Compra supermercado",
          contraparte="Mercado Z", categoria_id=3)                          # t5
        t(db, lote_id=1, conta_id=1, data=date(2026, 7, 12), valor=35_000,
          direcao="debito", tipo="despesa", descricao="Compra supermercado",
          contraparte="Mercado Z", categoria_id=3, ocorrencia=2)            # t6
        t(db, lote_id=1, conta_id=1, data=date(2026, 7, 15), valor=2_990,
          direcao="debito", tipo="tarifa", descricao="Tarifa pacote servicos")  # t7
        t(db, lote_id=1, conta_id=1, data=date(2026, 8, 3), valor=1_500,
          direcao="debito", tipo="juros", descricao="Juros cheque especial")    # t8
        t(db, lote_id=1, conta_id=1, data=date(2026, 7, 20), valor=100_000,
          direcao="debito", tipo="transferencia",
          descricao="TED entre contas propria")                             # t9
        transacao_pgto = t(
            db, lote_id=1, conta_id=1, data=date(2026, 7, 5), valor=150_000,
            direcao="debito", tipo="pagamento_fatura",
            descricao="Pagamento fatura cartao Alfa",
            status_conciliacao="conciliada")                                # t10
        t(db, lote_id=1, conta_id=1, data=date(2026, 8, 4), valor=5_000,
          direcao="credito", tipo="estorno", descricao="Estorno tarifa")    # t11
        t(db, lote_id=1, conta_id=1, data=date(2026, 8, 6), valor=123,
          direcao="credito", tipo="ajuste",
          descricao="Ajuste de arredondamento", moeda="USD")                # t12

        # ---- Conta 2 (Banco Verde) — lote 1 -----------------------------
        t(db, lote_id=1, conta_id=2, data=date(2026, 7, 20), valor=100_000,
          direcao="credito", tipo="transferencia",
          descricao="TED entre contas propria")                             # t13
        t(db, lote_id=1, conta_id=2, data=date(2026, 6, 15), valor=120_000,
          direcao="credito", tipo="receita", descricao="Projeto freelance",
          contraparte="Cliente W", categoria_id=1)                          # t14
        t(db, lote_id=1, conta_id=2, data=date(2026, 6, 20), valor=30_000,
          direcao="debito", tipo="despesa",
          descricao="Assinatura anual software", contraparte="Software K")  # t15

        # ---- Cartão Alfa — lote 2 (fatura 1 paga; fatura 2 aberta) ------
        t(db, lote_id=2, cartao_id=1, fatura_id=1, data=date(2026, 6, 10),
          valor=90_000, direcao="debito", tipo="compra",
          descricao="Passagem aerea", contraparte="Cia Aerea D",
          categoria_id=4)                                                   # t16
        t(db, lote_id=2, cartao_id=1, fatura_id=1, data=date(2026, 6, 12),
          valor=60_000, direcao="debito", tipo="compra",
          descricao="Hotel centro", contraparte="Hotel E", categoria_id=4)  # t17
        t(db, lote_id=2, cartao_id=1, fatura_id=2, data=date(2026, 7, 10),
          valor=40_000, direcao="debito", tipo="compra",
          descricao="Notebook parcela", contraparte="Loja A",
          parcela_num=1, parcela_total=10)                                  # t18
        t(db, lote_id=2, cartao_id=1, fatura_id=2, data=date(2026, 7, 15),
          valor=30_000, direcao="debito", tipo="compra",
          descricao="Jantar restaurante", contraparte="Restaurante B",
          categoria_id=3)                                                   # t19
        transacao_dividida = t(
            db, lote_id=2, cartao_id=1, fatura_id=2, data=date(2026, 7, 18),
            valor=50_000, direcao="debito", tipo="compra",
            descricao="Compra mista mercado", contraparte="Mercado Z",
            categoria_id=3)                                                 # t20
        t(db, lote_id=2, cartao_id=1, fatura_id=2, data=date(2026, 7, 20),
          valor=10_000, direcao="credito", tipo="estorno",
          descricao="Estorno compra duplicada", contraparte="Loja A")       # t21
        t(db, lote_id=2, cartao_id=1, fatura_id=2, data=date(2026, 8, 1),
          valor=25_000, direcao="debito", tipo="compra",
          descricao="Eletro parcela", contraparte="Eletro I",
          parcela_num=1, parcela_total=3)                                   # t22

        # ---- Cartão Beta — lote 2 (fatura 3 parcial; fatura 4 sem valor) -
        t(db, lote_id=2, cartao_id=2, fatura_id=3, data=date(2026, 7, 25),
          valor=50_000, direcao="debito", tipo="compra",
          descricao="Racao e petshop", contraparte="Petshop E")             # t23
        t(db, lote_id=2, cartao_id=2, fatura_id=3, data=date(2026, 8, 1),
          valor=30_000, direcao="debito", tipo="compra",
          descricao="Farmacia remedios", contraparte="Farmacia F")          # t24
        t(db, lote_id=2, cartao_id=2, fatura_id=4, data=date(2026, 8, 15),
          valor=20_000, direcao="debito", tipo="compra",
          descricao="Roupas", contraparte="Loja G", categoria_id=4)         # t25
        t(db, lote_id=2, cartao_id=2, fatura_id=4, data=date(2026, 8, 18),
          valor=15_000, direcao="debito", tipo="compra",
          descricao="Livros", contraparte="Livraria H")                     # t26

        # ---- Pagamento parcial da fatura Beta (pendente de conciliação) -
        t(db, lote_id=1, conta_id=2, data=date(2026, 8, 5), valor=30_000,
          direcao="debito", tipo="pagamento_fatura",
          descricao="Pagamento parcial fatura Beta")                        # t27

        # Divisão da compra mista: 20.000 Moradia + 30.000 Lazer.
        db.execute(
            "INSERT INTO divisoes_transacao (transacao_id, categoria_id,"
            " valor_centavos) VALUES (?, 2, 20000), (?, 4, 30000)",
            (transacao_dividida, transacao_dividida),
        )
        # Conciliação do pagamento da fatura 1 (muitos-para-muitos).
        db.execute("INSERT INTO conciliacoes (id, tipo) VALUES (1, 'pagamento_fatura')")
        db.execute(
            "INSERT INTO conciliacao_itens (conciliacao_id, transacao_id,"
            " fatura_id, papel, valor_centavos) VALUES"
            " (1, ?, NULL, 'pagamento', 150000), (1, NULL, 1, 'obrigacao', 150000)",
            (transacao_pgto,),
        )
        # Uma duplicidade aberta na fila de revisão.
        db.execute(
            "INSERT INTO duplicidades (transacao_id, candidata_fingerprint,"
            " motivo) VALUES (6, 'ff00', 'fingerprint igual, id_banco diferente')"
        )


@pytest.fixture()
def db() -> Iterator[sqlite3.Connection]:
    with store.conexao() as conexao:
        _semeia(conexao)
        yield conexao


def _por_mes(resposta: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {ponto["mes"]: ponto for ponto in resposta["serie"]}


def _grafico(resposta_painel: Dict[str, Any], id_: str) -> Dict[str, Any]:
    por_id = {grafico["id"]: grafico for grafico in resposta_painel["graficos"]}
    assert id_ in por_id, f"gráfico '{id_}' ausente do painel"
    return por_id[id_]


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


def test_from_params_ignora_lixo_e_converte_inteiros() -> None:
    filtros = Filtros.from_params(
        {
            "conta_id": "1",
            "valor_min": "5000",           # alias da API
            "valor_max_centavos": "abc",   # inteiro inválido → ignorado
            "tipo": "compra",
            "busca": ["mercado"],          # parse_qs entrega lista
            "cartao_id": "",               # vazio → ignorado
            "desconhecido": "x",           # chave desconhecida → ignorada
        }
    )
    assert filtros.conta_id == 1
    assert filtros.valor_min_centavos == 5000
    assert filtros.valor_max_centavos is None
    assert filtros.tipo == "compra"
    assert filtros.busca == "mercado"
    assert filtros.cartao_id is None


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        ({"conta_id": 1}, 12),
        ({"conta_id": 2}, 4),
        ({"cartao_id": 1}, 7),
        ({"cartao_id": 2}, 4),
        ({"instituicao_id": 1}, 19),
        ({"instituicao_id": 2}, 8),
        ({"fatura_id": 2}, 5),
        ({"categoria_id": 3}, 4),
        ({"tipo": "compra"}, 10),
        ({"tipo": "transferencia"}, 2),
        ({"direcao": "credito"}, 8),
        ({"periodo_inicio": "2026-07-01", "periodo_fim": "2026-07-31"}, 12),
        ({"valor_min_centavos": 100_000}, 8),
        ({"valor_max_centavos": 2_000}, 2),
        ({"moeda": "usd"}, 1),
        ({"status_conciliacao": "conciliada"}, 1),
        ({"status_categoria": "nao_categorizada"}, 15),
        ({"status_categoria": "categorizada"}, 12),
        ({"lote_id": 2}, 11),
        ({"arquivo_id": 1}, 16),
        ({"busca": "mercado"}, 3),
    ],
)
def test_filtros_um_a_um(db: sqlite3.Connection, campos: Dict[str, Any], esperado: int) -> None:
    resposta = consultas.listar_transacoes(db, Filtros(**campos), por_pagina=200)
    assert resposta["total"] == esperado
    assert len(resposta["itens"]) == esperado


def test_busca_case_e_acento_insensivel(db: sqlite3.Connection) -> None:
    # "SALÁRIO" precisa achar "Salario mensal" (normalização no termo).
    assert consultas.listar_transacoes(db, Filtros(busca="SALÁRIO"))["total"] == 3
    # Contraparte por LIKE case-insensível.
    assert consultas.listar_transacoes(db, Filtros(busca="MERCADO Z"))["total"] == 3
    # Curinga do LIKE é literal, não vira "casa com tudo".
    assert consultas.listar_transacoes(db, Filtros(busca="100%"))["total"] == 0


def test_filtros_combinados(db: sqlite3.Connection) -> None:
    filtros = Filtros(
        conta_id=1, tipo="receita",
        periodo_inicio="2026-07-01", periodo_fim="2026-07-31",
    )
    resposta = consultas.listar_transacoes(db, filtros)
    assert resposta["total"] == 1
    assert resposta["itens"][0]["descricao_original"] == "Salario mensal"
    assert resposta["itens"][0]["data_operacao"] == "2026-07-06"


def test_paginacao_e_ordenacao(db: sqlite3.Connection) -> None:
    ids: List[int] = []
    valores: List[int] = []
    for pagina in (1, 2, 3):
        resposta = consultas.listar_transacoes(
            db, None, pagina=pagina, por_pagina=10,
            ordenar="valor_centavos", dir="asc",
        )
        assert resposta["pagina"] == pagina
        assert resposta["total"] == TOTAIS_QUANTIDADE
        ids.extend(item["id"] for item in resposta["itens"])
        valores.extend(item["valor_centavos"] for item in resposta["itens"])
    assert len(ids) == TOTAIS_QUANTIDADE
    assert len(set(ids)) == TOTAIS_QUANTIDADE  # páginas não se sobrepõem
    assert valores == sorted(valores)

    # por_pagina estoura o teto → 200; ordenação fora da lista branca → padrão.
    resposta = consultas.listar_transacoes(
        db, None, por_pagina=9999, ordenar="id; DROP TABLE transacoes", dir="hack"
    )
    assert resposta["por_pagina"] == 200
    datas = [item["data_operacao"] for item in resposta["itens"]]
    assert datas == sorted(datas, reverse=True)  # caiu em data_operacao desc


def test_itens_json_prontos_com_rastreio_ao_original(db: sqlite3.Connection) -> None:
    resposta = consultas.listar_transacoes(db, Filtros(cartao_id=1), por_pagina=200)
    for item in resposta["itens"]:
        assert item["arquivo_id"] == 2
        assert item["arquivo_nome"] == "fatura-sintetica.csv"
        assert item["origem_ref"].startswith("linha ")
        assert isinstance(item["data_operacao"], str)  # ISO, JSON-pronto


# ---------------------------------------------------------------------------
# Totais e invariantes contábeis
# ---------------------------------------------------------------------------


def test_totais_excluem_transferencia_e_pagamento_fatura(db: sqlite3.Connection) -> None:
    totais = consultas.totais(db, None)
    assert totais == {
        "entradas_centavos": TOTAIS_ENTRADAS,
        "saidas_centavos": TOTAIS_SAIDAS,
        "resultado_centavos": TOTAIS_ENTRADAS - TOTAIS_SAIDAS,
        "quantidade": TOTAIS_QUANTIDADE,
    }
    # Filtrando só transferências: contam em quantidade, nunca nas somas.
    so_transferencias = consultas.totais(db, Filtros(tipo="transferencia"))
    assert so_transferencias["quantidade"] == 2
    assert so_transferencias["entradas_centavos"] == 0
    assert so_transferencias["saidas_centavos"] == 0
    so_pagamentos = consultas.totais(db, Filtros(tipo="pagamento_fatura"))
    assert so_pagamentos["quantidade"] == 2
    assert so_pagamentos["entradas_centavos"] == 0
    assert so_pagamentos["saidas_centavos"] == 0


def test_invariante_transferencia_neutra(db: sqlite3.Connection) -> None:
    """Apagar o par de transferências não muda nenhum total monetário."""
    antes = consultas.totais(db, None)
    competencia_antes = fluxo_mod.fluxo(db, "competencia", None)["serie"]
    with db:
        db.execute("DELETE FROM transacoes WHERE tipo = 'transferencia'")
    depois = consultas.totais(db, None)
    assert depois["entradas_centavos"] == antes["entradas_centavos"]
    assert depois["saidas_centavos"] == antes["saidas_centavos"]
    assert depois["quantidade"] == antes["quantidade"] - 2
    assert fluxo_mod.fluxo(db, "competencia", None)["serie"] == competencia_antes
    # No realizado o par se cancela: o resultado de julho é o mesmo sem elas.
    julho = _por_mes(fluxo_mod.fluxo(db, "realizado", None))["2026-07"]
    assert julho["resultado_centavos"] == (
        REALIZADO_ESPERADO["2026-07"][0] - REALIZADO_ESPERADO["2026-07"][1]
    )


def test_invariante_pagamento_fatura_nao_duplica(db: sqlite3.Connection) -> None:
    """Nenhuma visão conta compra E pagamento de fatura como despesa juntos."""
    competencia = _por_mes(fluxo_mod.fluxo(db, "competencia", None))
    realizado = _por_mes(fluxo_mod.fluxo(db, "realizado", None))
    # Competência: julho tem as compras (232.990) e NÃO os 150.000 do pagamento.
    assert competencia["2026-07"]["saidas_centavos"] == 232_990
    # Realizado: julho tem o pagamento (dentro dos 322.990) e NENHUMA compra.
    assert realizado["2026-07"]["saidas_centavos"] == 322_990
    # Apagar os pagamentos não muda a competência nem os totais da tabela.
    totais_antes = consultas.totais(db, None)
    with db:
        db.execute("DELETE FROM conciliacao_itens")
        db.execute("DELETE FROM transacoes WHERE tipo = 'pagamento_fatura'")
    assert consultas.totais(db, None)["saidas_centavos"] == totais_antes["saidas_centavos"]
    assert _por_mes(fluxo_mod.fluxo(db, "competencia", None)) == competencia
    # E o realizado perde exatamente os pagamentos — nada de compras escondidas.
    realizado_sem = _por_mes(fluxo_mod.fluxo(db, "realizado", None))
    total_saidas_antes = sum(p["saidas_centavos"] for p in realizado.values())
    total_saidas_depois = sum(p["saidas_centavos"] for p in realizado_sem.values())
    assert total_saidas_antes - total_saidas_depois == PAGAMENTOS_FATURA


# ---------------------------------------------------------------------------
# Fluxo — três visões
# ---------------------------------------------------------------------------


def test_fluxo_competencia(db: sqlite3.Connection) -> None:
    resposta = fluxo_mod.fluxo(db, "competencia", None)
    por_mes = _por_mes(resposta)
    assert set(por_mes) == set(COMPETENCIA_ESPERADA)
    for mes, (entradas, saidas) in COMPETENCIA_ESPERADA.items():
        assert por_mes[mes]["entradas_centavos"] == entradas, mes
        assert por_mes[mes]["saidas_centavos"] == saidas, mes
        assert por_mes[mes]["projetado"] is False


def test_fluxo_realizado_so_conta(db: sqlite3.Connection) -> None:
    resposta = fluxo_mod.fluxo(db, "realizado", None)
    por_mes = _por_mes(resposta)
    assert set(por_mes) == set(REALIZADO_ESPERADO)
    for mes, (entradas, saidas) in REALIZADO_ESPERADO.items():
        assert por_mes[mes]["entradas_centavos"] == entradas, mes
        assert por_mes[mes]["saidas_centavos"] == saidas, mes
    # Saldo acumulado é a soma corrente dos resultados.
    saldos = [ponto["saldo_acumulado_centavos"] for ponto in resposta["serie"]]
    assert saldos == [390_000, 667_010, 1_140_633]


def test_fluxo_projetado(db: sqlite3.Connection) -> None:
    resposta = fluxo_mod.fluxo(db, "projetado", None)
    por_mes = _por_mes(resposta)
    assert set(por_mes) == set(PROJETADO_ESPERADO)
    for mes, (entradas, saidas) in PROJETADO_ESPERADO.items():
        assert por_mes[mes]["entradas_centavos"] == entradas, mes
        assert por_mes[mes]["saidas_centavos"] == saidas, mes
    assert por_mes["2026-08"]["projetado"] is True
    assert por_mes["2026-09"]["projetado"] is True
    assert por_mes["2026-07"]["projetado"] is False
    assert resposta["serie"][-1]["saldo_acumulado_centavos"] == SALDO_PROJETADO_FINAL

    projecoes = {p["fatura_id"]: p for p in resposta["projecoes"]}
    # Fatura paga (F1) NUNCA projeta; parcial projeta só o restante.
    assert 1 not in projecoes
    assert projecoes[3]["valor_centavos"] == 50_000
    assert projecoes[2]["valor_centavos"] == 135_000
    # Fatura sem valor usa a soma das compras vinculadas.
    assert projecoes[4]["valor_centavos"] == 35_000
    # Conta pagadora rastreada; sem conta vira o agregado "sem conta".
    assert projecoes[2]["conta"] == "Conta Corrente Azul"
    assert projecoes[3]["conta"] == "sem conta"
    # Fatura sem valor e sem compras (F5) sai com aviso, não com exceção.
    assert 5 not in projecoes
    assert any("2026-10" in aviso for aviso in resposta["avisos"])


def test_fluxo_visoes_batem_entre_si(db: sqlite3.Connection) -> None:
    """Competência e realizado diferem exatamente pelas compras de cartão
    menos o que já saiu do caixa por outros tipos (pagamentos, transferências,
    estornos de conta e ajustes)."""
    competencia = fluxo_mod.fluxo(db, "competencia", None)["serie"]
    realizado = fluxo_mod.fluxo(db, "realizado", None)["serie"]
    saidas_competencia = sum(p["saidas_centavos"] for p in competencia)
    saidas_realizado = sum(p["saidas_centavos"] for p in realizado)
    assert saidas_competencia - saidas_realizado == (
        COMPRAS_CARTAO_LIQUIDAS - PAGAMENTOS_FATURA
        - TRANSFERENCIA_SAIDA - ESTORNO_CONTA
    )
    entradas_competencia = sum(p["entradas_centavos"] for p in competencia)
    entradas_realizado = sum(p["entradas_centavos"] for p in realizado)
    assert entradas_realizado - entradas_competencia == (
        TRANSFERENCIA_ENTRADA + ESTORNO_CONTA + AJUSTE_CREDITO
    )


def test_projetado_substitui_projecao_por_debito_real(db: sqlite3.Connection) -> None:
    """Quando o pagamento real entra e a fatura vira paga, a projeção sai —
    o saldo final do horizonte não muda (sem dupla subtração)."""
    antes = fluxo_mod.fluxo(db, "projetado", None)
    saldo_antes = antes["serie"][-1]["saldo_acumulado_centavos"]
    assert any(p["fatura_id"] == 3 for p in antes["projecoes"])

    with db:
        _insere_transacao(
            db, lote_id=1, conta_id=2, data=date(2026, 8, 20), valor=50_000,
            direcao="debito", tipo="pagamento_fatura",
            descricao="Pagamento final fatura Beta",
            status_conciliacao="conciliada",
        )
        db.execute(
            "UPDATE faturas SET pago_centavos = 80000, status = 'paga' WHERE id = 3"
        )

    depois = fluxo_mod.fluxo(db, "projetado", None)
    assert all(p["fatura_id"] != 3 for p in depois["projecoes"])
    assert depois["serie"][-1]["saldo_acumulado_centavos"] == saldo_antes
    agosto = _por_mes(depois)["2026-08"]
    assert agosto["saidas_centavos"] == PROJETADO_ESPERADO["2026-08"][1]  # real no lugar da projeção


def test_fluxo_visao_invalida_e_granularidade_viram_aviso(db: sqlite3.Connection) -> None:
    resposta = fluxo_mod.fluxo(db, "trimestral", None)
    assert resposta["serie"] == []
    assert any("visão desconhecida" in aviso for aviso in resposta["avisos"])
    resposta = fluxo_mod.fluxo(db, "realizado", None, granularidade="semanal")
    assert resposta["granularidade"] == "mensal"
    assert any("granularidade" in aviso for aviso in resposta["avisos"])
    assert len(resposta["serie"]) == 3


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


def _kpi(resposta: Dict[str, Any], id_: str) -> Dict[str, Any]:
    por_id = {kpi["id"]: kpi for kpi in resposta["kpis"]}
    assert id_ in por_id, f"KPI '{id_}' ausente"
    return por_id[id_]


def test_painel_kpis(db: sqlite3.Connection) -> None:
    resposta = painel_mod.painel(db, None, hoje=HOJE)
    assert _kpi(resposta, "entradas")["valor_centavos"] == TOTAIS_ENTRADAS
    assert _kpi(resposta, "saidas")["valor_centavos"] == TOTAIS_SAIDAS
    assert _kpi(resposta, "resultado")["valor_centavos"] == TOTAIS_ENTRADAS - TOTAIS_SAIDAS
    assert _kpi(resposta, "saldo-projetado")["valor_centavos"] == SALDO_PROJETADO_FINAL
    # 30 dias a partir de 2026-08-07: F3 (50.000 restantes) + F2 (135.000).
    a_vencer = _kpi(resposta, "faturas-a-vencer-30d")
    assert a_vencer["valor_centavos"] == 185_000
    assert "2 fatura(s)" in a_vencer["hint"]
    assert _kpi(resposta, "nao-categorizadas")["valor"] == 15
    assert _kpi(resposta, "pendencias-conciliacao")["valor"] == 1
    assert _kpi(resposta, "duplicidades-abertas")["valor"] == 1
    assert [g["id"] for g in resposta["graficos"]] == list(painel_mod.GRAFICO_IDS)
    for grafico in resposta["graficos"]:
        assert grafico["value_format"] == "centavos"


@pytest.mark.parametrize(
    "campos",
    [{}, {"conta_id": 1}, {"cartao_id": 2}, {"periodo_inicio": "2026-07-01"}],
)
def test_invariante_graficos_batem_com_tabela(db: sqlite3.Connection, campos: Dict[str, Any]) -> None:
    """KPIs e gráfico entradas×saídas do painel == consultas.totais, sempre."""
    filtros = Filtros(**campos)
    totais = consultas.totais(db, filtros)
    resposta = painel_mod.painel(db, filtros, hoje=HOJE)
    assert _kpi(resposta, "entradas")["valor_centavos"] == totais["entradas_centavos"]
    assert _kpi(resposta, "saidas")["valor_centavos"] == totais["saidas_centavos"]
    assert _kpi(resposta, "resultado")["valor_centavos"] == totais["resultado_centavos"]
    grafico = _grafico(resposta, "entradas-vs-saidas")
    assert sum(p["entradas_centavos"] for p in grafico["data"]) == totais["entradas_centavos"]
    assert sum(p["saidas_centavos"] for p in grafico["data"]) == totais["saidas_centavos"]


def test_gastos_por_categoria_respeita_divisoes(db: sqlite3.Connection) -> None:
    resposta = painel_mod.painel(db, None, hoje=HOJE)
    grafico = _grafico(resposta, "gastos-por-categoria")
    por_label = {p["label"]: p["value"] for p in grafico["data"]}
    # A compra dividida (50.000, categoria Alimentação) conta pelas PARTES:
    # 20.000 em Moradia e 30.000 em Lazer — e não nos 50.000 originais.
    assert por_label["Moradia"] == 220_000       # 200.000 aluguel + 20.000 divisão
    assert por_label["Lazer"] == 200_000         # 150.000 + 20.000 + 30.000 divisão
    assert por_label["Alimentação"] == 100_000   # sem a compra dividida
    assert por_label["Sem categoria"] == 179_490
    # A soma do donut fecha com as despesas da visão competência.
    saidas_competencia = sum(
        p["saidas_centavos"] for p in fluxo_mod.fluxo(db, "competencia", None)["serie"]
    )
    assert sum(por_label.values()) == saidas_competencia


def test_graficos_cartoes_ciclos_limite_e_proximas_faturas(db: sqlite3.Connection) -> None:
    resposta = painel_mod.painel(db, None, hoje=HOJE)

    por_cartao = {p["label"]: p["value"] for p in _grafico(resposta, "gastos-por-cartao")["data"]}
    assert por_cartao == {"Cartao Alfa": 285_000, "Cartao Beta": 115_000}

    ciclo = _grafico(resposta, "evolucao-por-ciclo")
    por_competencia = {p["mes"]: p for p in ciclo["data"]}
    assert por_competencia["2026-07"]["cartao_1"] == 150_000
    assert por_competencia["2026-08"]["cartao_2"] == 80_000
    assert por_competencia["2026-09"]["cartao_1"] == 135_000
    assert por_competencia["2026-09"]["cartao_2"] == 35_000  # soma das compras
    assert "2026-10" not in por_competencia  # fatura sem valor e sem compras
    assert {s["key"] for s in ciclo["series"]} == {"cartao_1", "cartao_2"}

    top = _grafico(resposta, "top-contrapartes")["data"]
    assert top[0] == {"label": "Imobiliaria Y", "value": 200_000}
    assert len(top) == 10  # a 11ª (Livraria H, 15.000) fica de fora
    assert all(p["label"] != "Livraria H" for p in top)

    limite = {p["label"]: p for p in _grafico(resposta, "utilizacao-limite")["data"]}
    # Alfa: 135.000 / 1.000.000 = 13,50% → 1350; Beta: 85.000 / 500.000 = 17% → 1700.
    assert limite["Cartao Alfa"]["value"] == 1_350
    assert limite["Cartao Alfa"]["usado_centavos"] == 135_000
    assert limite["Cartao Beta"]["value"] == 1_700
    assert limite["Cartao Beta"]["usado_centavos"] == 85_000

    proximas = _grafico(resposta, "proximas-faturas")["data"]
    assert [p["fatura_id"] for p in proximas] == [3, 2, 4, 5]
    assert proximas[0]["valor_previsto_centavos"] == 50_000
    assert proximas[0]["status"] == "parcial"
    assert proximas[0]["data"] == "2026-08-20"
    assert proximas[-1]["valor_previsto_centavos"] is None  # sem valor conhecido
