"""Testes de categorização (regras/divisões) e conciliação pagamento↔fatura.

Fixtures 100% sintéticas — nenhum dado real de banco, cartão ou pessoa.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Optional

import pytest

from engine.extratos import categorias, conciliacao, store
from engine.extratos.modelos import normaliza_descricao

# --------------------------------------------------------------------------
# Infra dos testes
# --------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Banco SQLite isolado em tmp_path — nunca o diretório real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))
    with store.conexao() as conn:
        yield conn


def cria_conta(db: sqlite3.Connection, nome: str = "Conta Sintética") -> int:
    with db:
        return int(db.execute("INSERT INTO contas (nome) VALUES (?)", (nome,)).lastrowid)


def cria_cartao(
    db: sqlite3.Connection, nome: str, conta_pagadora_id: Optional[int] = None
) -> int:
    with db:
        return int(
            db.execute(
                "INSERT INTO cartoes (nome, conta_pagadora_id) VALUES (?,?)",
                (nome, conta_pagadora_id),
            ).lastrowid
        )


def cria_fatura(
    db: sqlite3.Connection,
    cartao_id: int,
    vence_em: str,
    valor_centavos: Optional[int],
    pago_centavos: int = 0,
    status: str = "aberta",
) -> int:
    with db:
        return int(
            db.execute(
                "INSERT INTO faturas (cartao_id, competencia, vence_em,"
                " valor_centavos, pago_centavos, status) VALUES (?,?,?,?,?,?)",
                (cartao_id, vence_em[:7], vence_em, valor_centavos, pago_centavos, status),
            ).lastrowid
        )


def cria_lote(db: sqlite3.Connection) -> int:
    sha = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex sintéticos
    with db:
        arquivo_id = int(
            db.execute(
                "INSERT INTO arquivos (sha256, nome_original, mime_real, tamanho,"
                " tipo_documento, caminho_objeto) VALUES (?,?,?,?,?,?)",
                (sha, "sintetico.csv", "text/csv", 1, "extrato_conta", f"orig/{sha}"),
            ).lastrowid
        )
        return int(
            db.execute("INSERT INTO lotes (arquivo_id) VALUES (?)", (arquivo_id,)).lastrowid
        )


def cria_transacao(
    db: sqlite3.Connection,
    lote_id: int,
    *,
    data: str = "2026-06-01",
    valor: int = 10_000,
    direcao: str = "debito",
    tipo: str = "despesa",
    descricao: str = "COMPRA SINTETICA",
    conta_id: Optional[int] = None,
    cartao_id: Optional[int] = None,
    contraparte: Optional[str] = None,
    categoria_id: Optional[int] = None,
    origem_categoria: Optional[str] = None,
) -> int:
    with db:
        return int(
            db.execute(
                "INSERT INTO transacoes (lote_id, conta_id, cartao_id, data_operacao,"
                " valor_centavos, moeda, direcao, tipo, descricao_original,"
                " descricao_normalizada, contraparte, categoria_id, origem_categoria,"
                " status_conciliacao, fingerprint, origem_ref)"
                " VALUES (?,?,?,?,?,'BRL',?,?,?,?,?,?,?,'pendente',?,?)",
                (
                    lote_id,
                    conta_id,
                    cartao_id,
                    data,
                    valor,
                    direcao,
                    tipo,
                    descricao,
                    normaliza_descricao(descricao),
                    contraparte,
                    categoria_id,
                    origem_categoria,
                    uuid.uuid4().hex,
                    "linha sintética",
                ),
            ).lastrowid
        )


def cria_categoria(db: sqlite3.Connection, nome: str, pai_id: Optional[int] = None) -> int:
    with db:
        return int(
            db.execute(
                "INSERT INTO categorias (nome, pai_id) VALUES (?,?)", (nome, pai_id)
            ).lastrowid
        )


def cria_saldo(
    db: sqlite3.Connection,
    conta_id: int,
    lote_id: int,
    data: str,
    valor: int,
    rotulo: str,
) -> None:
    with db:
        db.execute(
            "INSERT INTO saldos (conta_id, data, valor_centavos, origem, lote_id, rotulo)"
            " VALUES (?,?,?,'extrato',?,?)",
            (conta_id, data, valor, lote_id, rotulo),
        )


def transacao_row(db: sqlite3.Connection, transacao_id: int) -> sqlite3.Row:
    return db.execute("SELECT * FROM transacoes WHERE id=?", (transacao_id,)).fetchone()


def fatura_row(db: sqlite3.Connection, fatura_id: int) -> sqlite3.Row:
    return db.execute("SELECT * FROM faturas WHERE id=?", (fatura_id,)).fetchone()


def conta_auditoria(db: sqlite3.Connection, acao: str) -> int:
    return int(
        db.execute("SELECT COUNT(*) FROM auditoria WHERE acao=?", (acao,)).fetchone()[0]
    )


# --------------------------------------------------------------------------
# Sementes
# --------------------------------------------------------------------------


def test_semear_categorias_padrao_idempotente(db):
    categorias.semear_categorias_padrao(db)
    raizes = int(
        db.execute("SELECT COUNT(*) FROM categorias WHERE pai_id IS NULL").fetchone()[0]
    )
    total = int(db.execute("SELECT COUNT(*) FROM categorias").fetchone()[0])
    assert raizes == 12  # as 12 raízes do SPEC

    # Rodar de novo não duplica nada (NULL em UNIQUE não protege sozinho).
    categorias.semear_categorias_padrao(db)
    assert int(db.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]) == total

    moradia = db.execute(
        "SELECT id FROM categorias WHERE nome='Moradia' AND pai_id IS NULL"
    ).fetchone()
    aluguel = db.execute(
        "SELECT id FROM categorias WHERE nome='Aluguel' AND pai_id=?", (moradia["id"],)
    ).fetchone()
    assert aluguel is not None


# --------------------------------------------------------------------------
# Regras — operadores, prioridade e restrições
# --------------------------------------------------------------------------


def test_regras_todos_os_operadores(db):
    lote = cria_lote(db)
    cat_mercado = cria_categoria(db, "Mercado")
    cat_energia = cria_categoria(db, "Energia")
    cat_transporte = cria_categoria(db, "Transporte")
    cat_streaming = cria_categoria(db, "Streaming")

    t_contem = cria_transacao(db, lote, descricao="COMPRA MERCADO BOM PREÇO SP")
    t_igual = cria_transacao(db, lote, descricao="Pix Energia")
    t_comeca = cria_transacao(db, lote, descricao="UBER TRIP 12345")
    t_regex = cria_transacao(db, lote, descricao="NETFLIX.COM 0999")

    # `contem` normaliza os dois lados: acento/caixa não importam.
    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "mercado bom preco", "categoria_id": cat_mercado})
    categorias.criar_regra(db, {"campo": "descricao", "operador": "igual",
                                "valor": "PIX ENERGIA", "categoria_id": cat_energia})
    categorias.criar_regra(db, {"campo": "descricao", "operador": "comeca",
                                "valor": "Uber", "categoria_id": cat_transporte})
    categorias.criar_regra(db, {"campo": "descricao", "operador": "regex",
                                "valor": r"netflix\.com", "categoria_id": cat_streaming})

    resposta = categorias.aplicar_regras(db, dry_run=False)
    assert resposta["aplicadas"] == 4
    assert transacao_row(db, t_contem)["categoria_id"] == cat_mercado
    assert transacao_row(db, t_igual)["categoria_id"] == cat_energia
    assert transacao_row(db, t_comeca)["categoria_id"] == cat_transporte
    assert transacao_row(db, t_regex)["categoria_id"] == cat_streaming
    for tid in (t_contem, t_igual, t_comeca, t_regex):
        linha = transacao_row(db, tid)
        assert linha["origem_categoria"] == "regra"
        assert linha["confianca"] is not None


def test_prioridade_menor_vence(db):
    lote = cria_lote(db)
    cat_a = cria_categoria(db, "Prioritária")
    cat_b = cria_categoria(db, "Genérica")
    transacao = cria_transacao(db, lote, descricao="PADARIA DO BAIRRO")

    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "padaria", "categoria_id": cat_b, "prioridade": 200})
    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "padaria", "categoria_id": cat_a, "prioridade": 10})

    resposta = categorias.aplicar_regras(db, dry_run=False)
    assert resposta["aplicadas"] == 1  # primeira que casa vence — só uma aplicação
    assert transacao_row(db, transacao)["categoria_id"] == cat_a


def test_restricao_por_cartao(db):
    lote = cria_lote(db)
    cartao_x = cria_cartao(db, "Cartão Sintético X")
    cartao_y = cria_cartao(db, "Cartão Sintético Y")
    cat = cria_categoria(db, "Só do X")
    t_x = cria_transacao(db, lote, descricao="LOJA IGUAL", cartao_id=cartao_x, tipo="compra")
    t_y = cria_transacao(db, lote, descricao="LOJA IGUAL", cartao_id=cartao_y, tipo="compra")

    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "loja igual", "categoria_id": cat,
                                "cartao_id": cartao_x})
    categorias.aplicar_regras(db, dry_run=False)
    assert transacao_row(db, t_x)["categoria_id"] == cat
    assert transacao_row(db, t_y)["categoria_id"] is None


def test_restricao_por_faixa_de_valor(db):
    lote = cria_lote(db)
    cat = cria_categoria(db, "Gastos grandes")
    t_pequena = cria_transacao(db, lote, descricao="LOJA VALOR", valor=50_000)
    t_grande = cria_transacao(db, lote, descricao="LOJA VALOR", valor=150_000)

    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "loja valor", "categoria_id": cat,
                                "valor_min_centavos": 100_000})
    categorias.aplicar_regras(db, dry_run=False)
    assert transacao_row(db, t_pequena)["categoria_id"] is None
    assert transacao_row(db, t_grande)["categoria_id"] == cat


def test_dry_run_nao_grava_nada(db):
    lote = cria_lote(db)
    cat = cria_categoria(db, "Prévia")
    transacao = cria_transacao(db, lote, descricao="FARMACIA CENTRAL")
    regra = categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                        "valor": "farmacia", "categoria_id": cat})

    resposta = categorias.aplicar_regras(db, dry_run=True)
    assert resposta["dry_run"] is True
    assert resposta["aplicadas"] == 0
    assert resposta["previa"] == [
        {
            "transacao_id": transacao,
            "categoria_atual": None,
            "categoria_sugerida": cat,
            "regra_id": regra["id"],
            "confianca": categorias.CONFIANCA_POR_OPERADOR["contem"],
        }
    ]
    # Nada gravado: transação intacta e zero auditoria de aplicação.
    assert transacao_row(db, transacao)["categoria_id"] is None
    assert conta_auditoria(db, "categoria_aplicada") == 0


def test_aplicacao_gera_auditoria_por_transacao(db):
    lote = cria_lote(db)
    cat = cria_categoria(db, "Auditada")
    transacao = cria_transacao(db, lote, descricao="POSTO COMBUSTIVEL")
    regra = categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                        "valor": "combustivel", "categoria_id": cat})

    categorias.aplicar_regras(db, dry_run=False)
    evento = db.execute(
        "SELECT * FROM auditoria WHERE entidade='transacao' AND entidade_id=?"
        " AND acao='categoria_aplicada'",
        (transacao,),
    ).fetchone()
    assert evento is not None
    assert evento["campo"] == "categoria_id"
    assert evento["valor_anterior"] is None
    assert evento["valor_novo"] == str(cat)
    assert evento["origem"] == f"regra:{regra['id']}"


def test_categoria_manual_nunca_e_sobrescrita_por_regra(db):
    lote = cria_lote(db)
    cat_manual = cria_categoria(db, "Escolha Humana")
    cat_regra = cria_categoria(db, "Escolha da Máquina")
    transacao = cria_transacao(db, lote, descricao="RESTAURANTE SINTETICO")

    resultado = categorias.definir_categoria(
        db, transacao, cat_manual, justificativa="decisão do usuário"
    )
    assert resultado["origem_categoria"] == "manual"
    assert conta_auditoria(db, "categoria_definida") == 1

    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "restaurante", "categoria_id": cat_regra})
    resposta = categorias.aplicar_regras(db, dry_run=False)
    assert resposta["previa"] == []  # manual nem entra na prévia
    linha = transacao_row(db, transacao)
    assert linha["categoria_id"] == cat_manual
    assert linha["origem_categoria"] == "manual"


def test_regra_de_origem_regra_pode_ser_recategorizada(db):
    lote = cria_lote(db)
    cat_velha = cria_categoria(db, "Antiga")
    cat_nova = cria_categoria(db, "Nova")
    transacao = cria_transacao(db, lote, descricao="ASSINATURA MENSAL XYZ")
    with db:
        db.execute(
            "UPDATE transacoes SET categoria_id=?, origem_categoria='regra' WHERE id=?",
            (cat_velha, transacao),
        )
    categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                "valor": "assinatura", "categoria_id": cat_nova})
    categorias.aplicar_regras(db, dry_run=False)
    assert transacao_row(db, transacao)["categoria_id"] == cat_nova


def test_regex_invalida_e_pulada_com_aviso(db):
    lote = cria_lote(db)
    cat = cria_categoria(db, "Nunca Aplicada")
    transacao = cria_transacao(db, lote, descricao="QUALQUER COISA")

    # criar_regra recusa regex inválida na entrada...
    recusa = categorias.criar_regra(db, {"campo": "descricao", "operador": "regex",
                                         "valor": "([", "categoria_id": cat})
    assert "erro" in recusa

    # ...e uma regra ruim pré-existente (legado) é pulada sem exceção.
    with db:
        db.execute(
            "INSERT INTO regras_categoria (campo, operador, valor, categoria_id)"
            " VALUES ('descricao','regex','([',?)",
            (cat,),
        )
    resposta = categorias.aplicar_regras(db, dry_run=False)
    assert any("regex inválida" in aviso for aviso in resposta["avisos"])
    assert transacao_row(db, transacao)["categoria_id"] is None


def test_crud_de_regras_com_auditoria(db):
    cat = cria_categoria(db, "CRUD")
    regra = categorias.criar_regra(db, {"campo": "descricao", "operador": "contem",
                                        "valor": "algo", "categoria_id": cat})
    assert "erro" not in regra
    assert conta_auditoria(db, "regra_criada") == 1

    editada = categorias.editar_regra(db, regra["id"], {"prioridade": 5})
    assert editada["prioridade"] == 5
    assert conta_auditoria(db, "regra_editada") == 1

    desativada = categorias.desativar_regra(db, regra["id"], justificativa="obsoleta")
    assert desativada["ativo"] is False
    assert conta_auditoria(db, "regra_desativada") == 1
    assert categorias.listar_regras(db, apenas_ativas=True) == []


# --------------------------------------------------------------------------
# Divisão de transação
# --------------------------------------------------------------------------


def test_divisao_exige_soma_exata_e_substitui_anteriores(db):
    lote = cria_lote(db)
    cat_a = cria_categoria(db, "Metade A")
    cat_b = cria_categoria(db, "Metade B")
    transacao = cria_transacao(db, lote, valor=10_000)

    # Soma errada → erro claro, nada gravado.
    erro = categorias.dividir_transacao(
        db, transacao,
        [{"categoria_id": cat_a, "valor_centavos": 6_000},
         {"categoria_id": cat_b, "valor_centavos": 3_999}],
    )
    assert "erro" in erro and "difere" in erro["erro"]
    assert db.execute("SELECT COUNT(*) FROM divisoes_transacao").fetchone()[0] == 0

    # Float é proibido em dinheiro.
    erro_float = categorias.dividir_transacao(
        db, transacao, [{"categoria_id": cat_a, "valor_centavos": 100.0}]
    )
    assert "erro" in erro_float

    # Soma exata → grava e audita.
    ok = categorias.dividir_transacao(
        db, transacao,
        [{"categoria_id": cat_a, "valor_centavos": 6_000},
         {"categoria_id": cat_b, "valor_centavos": 4_000}],
    )
    assert len(ok["divisoes"]) == 2
    assert conta_auditoria(db, "transacao_dividida") == 1

    # Nova divisão SUBSTITUI a anterior (não soma).
    ok2 = categorias.dividir_transacao(
        db, transacao, [{"categoria_id": cat_a, "valor_centavos": 10_000}]
    )
    assert len(ok2["divisoes"]) == 1
    total = db.execute(
        "SELECT COUNT(*), SUM(valor_centavos) FROM divisoes_transacao WHERE transacao_id=?",
        (transacao,),
    ).fetchone()
    assert (total[0], total[1]) == (1, 10_000)


def test_arvore_categorias_com_contagem_de_uso(db):
    categorias.semear_categorias_padrao(db)
    lote = cria_lote(db)
    mercado = db.execute(
        "SELECT id FROM categorias WHERE nome='Mercado'"
    ).fetchone()["id"]
    cria_transacao(db, lote, categoria_id=mercado, origem_categoria="manual")

    arvore = categorias.arvore_categorias(db)
    assert len(arvore) == 12
    alimentacao = next(no for no in arvore if no["nome"] == "Alimentação")
    no_mercado = next(f for f in alimentacao["filhas"] if f["nome"] == "Mercado")
    assert no_mercado["uso"] == 1


# --------------------------------------------------------------------------
# Sugestões de conciliação
# --------------------------------------------------------------------------


def _cenario_fatura(db, vence_em="2026-07-10", valor=50_000):
    conta = cria_conta(db, f"Conta Sintética {uuid.uuid4().hex[:8]}")
    cartao = cria_cartao(db, f"Cartão Sintético {uuid.uuid4().hex[:8]}", conta_pagadora_id=conta)
    fatura = cria_fatura(db, cartao, vence_em, valor)
    return conta, cartao, fatura


def _pagamento(db, lote, conta, data, valor):
    return cria_transacao(
        db, lote, data=data, valor=valor, direcao="debito",
        tipo="pagamento_fatura", descricao="PAGAMENTO FATURA CARTAO",
        conta_id=conta,
    )


def test_sugestao_pagamento_integral(db):
    conta, _, fatura = _cenario_fatura(db)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-10", 50_000)

    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert len(sugestoes) == 1
    s = sugestoes[0]
    assert s["transacao_id"] == transacao
    assert s["faturas"] == [{"fatura_id": fatura, "valor_sugerido_centavos": 50_000}]
    assert s["confianca"] == 1.0
    assert s["motivo"].startswith("pagamento_integral")


def test_sugestao_dentro_da_tolerancia_de_valor(db):
    conta, _, fatura = _cenario_fatura(db)  # restante 50_000 → tolerância 500
    lote = cria_lote(db)
    _pagamento(db, lote, conta, "2026-07-10", 50_300)
    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert len(sugestoes) == 1
    assert sugestoes[0]["motivo"].startswith("pagamento_integral")
    assert sugestoes[0]["confianca"] == 0.9


def test_sugestao_pagamento_parcial(db):
    conta, _, fatura = _cenario_fatura(db)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-10", 20_000)

    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert len(sugestoes) == 1
    s = sugestoes[0]
    assert s["transacao_id"] == transacao
    assert s["faturas"] == [{"fatura_id": fatura, "valor_sugerido_centavos": 20_000}]
    assert s["motivo"].startswith("pagamento_parcial")


def test_sugestao_antecipado_e_atrasado(db):
    conta, _, _ = _cenario_fatura(db, vence_em="2026-07-10")
    lote = cria_lote(db)
    antecipado = _pagamento(db, lote, conta, "2026-06-30", 50_000)  # vence_em − 10d
    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert [s["transacao_id"] for s in sugestoes] == [antecipado]

    conta2, _, _ = _cenario_fatura(db, vence_em="2026-08-10")
    atrasado = _pagamento(db, lote, conta2, "2026-08-25", 50_000)  # vence_em + 15d
    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert atrasado in [s["transacao_id"] for s in sugestoes]


def test_sem_sugestao_fora_da_janela_ou_conta_errada(db):
    conta, _, _ = _cenario_fatura(db, vence_em="2026-07-10")
    lote = cria_lote(db)
    _pagamento(db, lote, conta, "2026-06-20", 50_000)  # vence_em − 20d: fora da janela
    assert conciliacao.sugerir_conciliacoes(db) == []

    # Conta pagadora diferente também não sugere.
    outra_conta = cria_conta(db, "Outra Conta")
    _pagamento(db, lote, outra_conta, "2026-07-10", 50_000)
    assert conciliacao.sugerir_conciliacoes(db) == []


def test_sugestao_pagamento_agrupado_duas_faturas(db):
    conta = cria_conta(db)
    cartao_x = cria_cartao(db, "Cartão X", conta_pagadora_id=conta)
    cartao_y = cria_cartao(db, "Cartão Y", conta_pagadora_id=conta)
    fatura_x = cria_fatura(db, cartao_x, "2026-07-20", 30_000)
    fatura_y = cria_fatura(db, cartao_y, "2026-07-20", 20_000)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-20", 50_000)

    sugestoes = conciliacao.sugerir_conciliacoes(db)
    assert len(sugestoes) == 1
    s = sugestoes[0]
    assert s["transacao_id"] == transacao
    assert s["motivo"].startswith("pagamento_agrupado")
    assert sorted(f["fatura_id"] for f in s["faturas"]) == sorted([fatura_x, fatura_y])
    assert sum(f["valor_sugerido_centavos"] for f in s["faturas"]) == 50_000


# --------------------------------------------------------------------------
# Conciliar / desfazer
# --------------------------------------------------------------------------


def test_conciliar_muitos_para_muitos_atualiza_faturas_e_status(db):
    conta = cria_conta(db)
    cartao_x = cria_cartao(db, "Cartão X", conta_pagadora_id=conta)
    cartao_y = cria_cartao(db, "Cartão Y", conta_pagadora_id=conta)
    fatura_x = cria_fatura(db, cartao_x, "2026-07-20", 30_000)
    fatura_y = cria_fatura(db, cartao_y, "2026-07-20", 20_000)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-20", 50_000)

    resposta = conciliacao.conciliar(
        db,
        [
            {"transacao_id": transacao, "papel": "pagamento"},
            {"fatura_id": fatura_x, "papel": "obrigacao", "valor_centavos": 30_000},
            {"fatura_id": fatura_y, "papel": "obrigacao", "valor_centavos": 20_000},
        ],
        observacao="pagamento agrupado do dia 20",
    )
    assert "erro" not in resposta
    assert resposta["status_conciliacao"] == "conciliada"
    assert resposta["sobra_centavos"] == 0
    assert resposta["avisos"] == []

    fx, fy = fatura_row(db, fatura_x), fatura_row(db, fatura_y)
    assert (fx["pago_centavos"], fx["status"]) == (30_000, "paga")
    assert (fy["pago_centavos"], fy["status"]) == (20_000, "paga")
    assert transacao_row(db, transacao)["status_conciliacao"] == "conciliada"
    assert conta_auditoria(db, "pagamento_conciliado") == 2
    assert conta_auditoria(db, "conciliacao_criada") == 1


def test_conciliar_pagamento_maior_que_o_devido_vira_divergente(db):
    conta, _, fatura = _cenario_fatura(db, valor=30_000)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-10", 35_000)

    resposta = conciliacao.conciliar(
        db,
        [
            {"transacao_id": transacao, "papel": "pagamento"},
            {"fatura_id": fatura, "papel": "obrigacao"},
        ],
    )
    assert resposta["status_conciliacao"] == "divergente"
    assert resposta["sobra_centavos"] == 5_000
    assert any("maior que o devido" in aviso for aviso in resposta["avisos"])
    linha = fatura_row(db, fatura)
    assert (linha["pago_centavos"], linha["status"]) == (30_000, "paga")
    assert transacao_row(db, transacao)["status_conciliacao"] == "divergente"


def test_desfazer_conciliacao_restaura_fatura_e_transacao(db):
    conta, _, fatura = _cenario_fatura(db)
    lote = cria_lote(db)
    transacao = _pagamento(db, lote, conta, "2026-07-10", 50_000)
    resposta = conciliacao.conciliar(
        db,
        [
            {"transacao_id": transacao, "papel": "pagamento"},
            {"fatura_id": fatura, "papel": "obrigacao", "valor_centavos": 50_000},
        ],
    )
    assert fatura_row(db, fatura)["status"] == "paga"

    desfeita = conciliacao.desfazer_conciliacao(db, resposta["conciliacao_id"])
    assert desfeita["status"] == "desfeita"
    linha = fatura_row(db, fatura)
    assert linha["pago_centavos"] == 0
    assert linha["status"] != "paga"
    assert transacao_row(db, transacao)["status_conciliacao"] == "pendente"
    assert db.execute("SELECT COUNT(*) FROM conciliacoes").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM conciliacao_itens").fetchone()[0] == 0
    assert conta_auditoria(db, "pagamento_estornado") == 1


def test_conciliar_item_invalido_devolve_erro_sem_gravar(db):
    resposta = conciliacao.conciliar(db, [{"papel": "qualquer"}])
    assert "erro" in resposta
    resposta = conciliacao.conciliar(
        db, [{"transacao_id": 999, "papel": "pagamento"}]
    )
    assert "erro" in resposta
    assert db.execute("SELECT COUNT(*) FROM conciliacoes").fetchone()[0] == 0


# --------------------------------------------------------------------------
# Validação contábil de extrato (equação de saldo)
# --------------------------------------------------------------------------


def _lote_com_saldos(db, final_centavos: int):
    """Lote com virada de mês E ano bissexto (crédito em 29/02/2028)."""
    conta = cria_conta(db)
    lote = cria_lote(db)
    cria_saldo(db, conta, lote, "2028-02-27", 100_000, "saldo inicial")
    cria_saldo(db, conta, lote, "2028-03-01", final_centavos, "saldo final")
    cria_transacao(db, lote, data="2028-02-29", valor=50_000,
                   direcao="credito", tipo="receita", conta_id=conta,
                   descricao="CREDITO BISSEXTO")
    cria_transacao(db, lote, data="2028-03-01", valor=30_000,
                   direcao="debito", tipo="despesa", conta_id=conta,
                   descricao="DEBITO VIRADA DE MES")
    return lote


def test_validar_extrato_saldo_fecha_exato(db):
    lote = _lote_com_saldos(db, final_centavos=120_000)  # 100000 + 50000 − 30000
    assert conciliacao.validar_extrato(db, lote) == []


def test_validar_extrato_saldo_divergente_em_centavos(db):
    lote = _lote_com_saldos(db, final_centavos=121_000)
    divergencias = conciliacao.validar_extrato(db, lote)
    assert len(divergencias) == 1
    d = divergencias[0]
    assert d["tipo"] == "saldo_divergente"
    assert d["esperado"] == 121_000
    assert d["calculado"] == 120_000
    assert d["diferenca"] == -1_000


def test_validar_extrato_sem_saldos_nao_tem_o_que_validar(db):
    lote = cria_lote(db)
    cria_transacao(db, lote, valor=1_000)
    assert conciliacao.validar_extrato(db, lote) == []
