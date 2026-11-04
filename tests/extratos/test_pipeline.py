"""Testes do pipeline de importação (receber → prévia → confirmar → reverter).

Fixtures 100% sintéticas — nenhum dado real de banco, cartão ou pessoa.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict

import pytest

from engine.extratos import pipeline, store

# --------------------------------------------------------------------------
# Infra dos testes
# --------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Banco SQLite isolado em tmp_path — nunca o diretório real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))
    with store.conexao() as conn:
        yield conn


CSV_FELIZ = (
    "Data;Histórico;Valor;Documento\n"
    "05/03/2026;PIX RECEBIDO CLIENTE;1.000,00;\n"
    "06/03/2026;PAGAMENTO FATURA CARTAO XPTO;-400,00;\n"
    "07/03/2026;MERCADO BOM PRECO;-100,00;\n"
).encode("utf-8")


def recebe(
    db: sqlite3.Connection,
    conteudo: bytes = CSV_FELIZ,
    nome: str = "extrato.csv",
    tipo: str = "extrato_conta",
    **kw: Any,
) -> Dict[str, Any]:
    kw.setdefault("instituicao", "Banco Teste")
    if tipo == "extrato_conta":
        kw.setdefault("conta", "Conta Corrente")
    return pipeline.receber_arquivo(db, conteudo, nome, tipo, **kw)


def conta_linhas(db: sqlite3.Connection, tabela: str) -> int:
    return int(db.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0])


# --------------------------------------------------------------------------
# Fluxo feliz: CSV → prévia → confirmar → transações no banco
# --------------------------------------------------------------------------


def test_fluxo_feliz_csv_previa_e_confirmacao(db):
    previa = recebe(db)
    assert "erro" not in previa
    assert previa["status"] == "pronto"
    assert previa["mapeamento_necessario"] is None
    assert previa["adaptador"] == "csv-generico"
    assert previa["contagens"]["transacoes"] == 3
    assert previa["contagens"]["creditos"] == 1
    assert previa["contagens"]["debitos"] == 2
    assert previa["totais"]["entradas_centavos"] == 100_000
    assert previa["totais"]["saidas_centavos"] == 50_000
    assert previa["periodo"] == {"inicio": "2026-03-05", "fim": "2026-03-07"}
    assert previa["duplicidades"] == []
    assert len(previa["amostra"]) == 3

    # Staged: nada em transacoes antes da confirmação explícita.
    assert conta_linhas(db, "transacoes") == 0
    assert conta_linhas(db, "linhas_brutas") == 4  # cabeçalho + 3 linhas

    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert resposta["status"] == "confirmado"
    assert resposta["inseridas"] == 3
    assert resposta["puladas_fingerprint"] == 0
    assert resposta["em_duplicidades"] == 0

    linhas = db.execute(
        "SELECT tipo, direcao, valor_centavos FROM transacoes ORDER BY data_operacao"
    ).fetchall()
    assert [l["tipo"] for l in linhas] == ["receita", "pagamento_fatura", "despesa"]
    assert [l["direcao"] for l in linhas] == ["credito", "debito", "debito"]
    assert [l["valor_centavos"] for l in linhas] == [100_000, 40_000, 10_000]

    arquivo = db.execute("SELECT status FROM arquivos").fetchone()
    assert arquivo["status"] == "confirmado"
    lote = db.execute(
        "SELECT confirmado_em FROM lotes WHERE id=?", (previa["lote_id"],)
    ).fetchone()
    assert lote["confirmado_em"] is not None
    auditoria = db.execute(
        "SELECT count(*) FROM auditoria WHERE entidade='lote' AND acao='lote_confirmado'"
    ).fetchone()[0]
    assert auditoria == 1


# --------------------------------------------------------------------------
# Dedup camada 1: sha256 do arquivo
# --------------------------------------------------------------------------


def test_reimportar_mesmo_arquivo_erro_duplicado(db):
    previa = recebe(db)
    resposta = recebe(db, nome="outro_nome.csv")  # bytes idênticos
    assert resposta["erro_duplicado"] is True
    assert resposta["arquivo_id"] == previa["arquivo_id"]
    assert resposta["pode_reprocessar"] is True
    assert "mensagem" in resposta
    assert conta_linhas(db, "arquivos") == 1  # nada foi regravado


# --------------------------------------------------------------------------
# Dedup camada 3: fingerprints já confirmadas por OUTRO arquivo
# --------------------------------------------------------------------------


def test_invariante_reimportacao_zero(db):
    previa_a = recebe(db)
    assert pipeline.confirmar_lote(db, previa_a["lote_id"])["inseridas"] == 3

    # Mesmo período/transações em bytes diferentes (linha em branco no fim).
    previa_b = recebe(db, CSV_FELIZ + b"\n;;;\n", nome="extrato_repetido.csv")
    assert "erro" not in previa_b
    assert len(previa_b["duplicidades"]) == 3  # avisadas já na prévia

    resposta = pipeline.confirmar_lote(db, previa_b["lote_id"])
    assert resposta["status"] == "confirmado"
    assert resposta["inseridas"] == 0
    assert resposta["puladas_fingerprint"] == 3
    assert conta_linhas(db, "transacoes") == 3  # zero novas — idempotente


def test_duas_identicas_legitimas_no_mesmo_arquivo(db):
    conteudo = (
        "Data;Histórico;Valor\n"
        "05/03/2026;PADARIA DOCE;-25,00\n"
        "05/03/2026;PADARIA DOCE;-25,00\n"
    ).encode("utf-8")
    previa = recebe(db, conteudo, nome="dia_de_padaria.csv")
    assert previa["contagens"]["transacoes"] == 2
    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert resposta["inseridas"] == 2  # ocorrência distingue as fingerprints
    assert resposta["em_duplicidades"] == 0
    assert conta_linhas(db, "transacoes") == 2
    assert conta_linhas(db, "duplicidades") == 0  # sem falso positivo


def test_parecidas_com_id_banco_diferente_entram_na_fila(db):
    cabecalho = "Data;Histórico;Valor;Documento;Identificador\n"
    arquivo_a = (cabecalho + "10/03/2026;ASSINATURA STREAMING;-49,90;111;A1\n").encode()
    arquivo_b = (cabecalho + "10/03/2026;ASSINATURA STREAMING;-49,90;222;B7\n").encode()

    previa_a = recebe(db, arquivo_a, nome="a.csv")
    assert pipeline.confirmar_lote(db, previa_a["lote_id"])["inseridas"] == 1

    previa_b = recebe(db, arquivo_b, nome="b.csv")
    resposta = pipeline.confirmar_lote(db, previa_b["lote_id"])
    # Fingerprint nova (documento difere) mas muito parecida: insere E enfileira.
    assert resposta["inseridas"] == 1
    assert resposta["em_duplicidades"] == 1
    assert conta_linhas(db, "transacoes") == 2
    fila = db.execute("SELECT * FROM duplicidades").fetchall()
    assert len(fila) == 1
    assert "id_banco" in fila[0]["motivo"]
    assert fila[0]["resolucao"] is None  # nunca resolvida automaticamente


# --------------------------------------------------------------------------
# Invariante: falha no meio da confirmação não deixa NADA gravado
# --------------------------------------------------------------------------


def test_invariante_atomicidade(db, monkeypatch):
    previa = recebe(db)

    def explode(db_, lote_id):
        raise RuntimeError("falha simulada no meio da confirmação")

    monkeypatch.setattr(pipeline, "_apos_inserir_transacoes", explode)
    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert "erro" in resposta
    assert resposta["inseridas"] == 0

    assert conta_linhas(db, "transacoes") == 0
    assert conta_linhas(db, "saldos") == 0
    lote = db.execute(
        "SELECT confirmado_em FROM lotes WHERE id=?", (previa["lote_id"],)
    ).fetchone()
    assert lote["confirmado_em"] is None
    arquivo = db.execute("SELECT status FROM arquivos").fetchone()
    assert arquivo["status"] == "pronto"  # rollback devolveu o estado anterior
    confirmados = db.execute(
        "SELECT count(*) FROM auditoria WHERE acao='lote_confirmado'"
    ).fetchone()[0]
    assert confirmados == 0


# --------------------------------------------------------------------------
# Reverter e reprocessar
# --------------------------------------------------------------------------


def test_reverter_lote_preserva_arquivo_linhas_e_auditoria(db):
    previa = recebe(db)
    pipeline.confirmar_lote(db, previa["lote_id"])
    resposta = pipeline.reverter_lote(db, previa["lote_id"], "importei o mês errado")
    assert resposta["status"] == "revertido"
    assert resposta["transacoes_apagadas"] == 3

    assert conta_linhas(db, "transacoes") == 0
    assert conta_linhas(db, "linhas_brutas") == 4  # espelho auditável fica
    assert conta_linhas(db, "arquivos") == 1
    arquivo = db.execute("SELECT status FROM arquivos").fetchone()
    assert arquivo["status"] == "revertido"
    reversao = db.execute(
        "SELECT justificativa FROM auditoria WHERE acao='lote_revertido'"
    ).fetchone()
    assert reversao["justificativa"] == "importei o mês errado"

    # Reverter de novo é recusado com mensagem, não exceção.
    assert "erro" in pipeline.reverter_lote(db, previa["lote_id"])


def test_reprocessar_cria_novo_lote_e_reverte_o_anterior(db):
    previa = recebe(db)
    pipeline.confirmar_lote(db, previa["lote_id"])
    nova = pipeline.reprocessar_arquivo(db, previa["arquivo_id"])
    assert "erro" not in nova
    assert nova["lote_id"] != previa["lote_id"]
    assert nova["status"] == "pronto"

    antigo = db.execute(
        "SELECT revertido_em FROM lotes WHERE id=?", (previa["lote_id"],)
    ).fetchone()
    assert antigo["revertido_em"] is not None
    assert conta_linhas(db, "transacoes") == 0  # staged de novo, nada confirmado

    resposta = pipeline.confirmar_lote(db, nova["lote_id"])
    assert resposta["inseridas"] == 3


# --------------------------------------------------------------------------
# Mapeamento manual em duas passadas
# --------------------------------------------------------------------------


def test_mapeamento_em_duas_passadas(db):
    conteudo = (
        "Quando;Coisa;Quanto\n"
        "05/03/2026;CINEMA;-50,00\n"
        "06/03/2026;SALARIO;3.000,00\n"
    ).encode("utf-8")
    previa = recebe(db, conteudo, nome="estranho.csv")
    assert previa["status"] == "aguardando_revisao"
    assert previa["mapeamento_necessario"] is not None
    assert "colunas" in previa["mapeamento_necessario"]

    # Confirmar antes do mapeamento é recusado.
    assert "erro" in pipeline.confirmar_lote(db, previa["lote_id"])

    nova = pipeline.aplicar_mapeamento(
        db,
        previa["lote_id"],
        {"Quando": "data", "Coisa": "descricao", "Quanto": "valor"},
    )
    assert "erro" not in nova
    assert nova["lote_id"] != previa["lote_id"]
    assert nova["status"] == "pronto"
    assert nova["contagens"]["transacoes"] == 2
    antigo = db.execute(
        "SELECT revertido_em FROM lotes WHERE id=?", (previa["lote_id"],)
    ).fetchone()
    assert antigo["revertido_em"] is not None

    resposta = pipeline.confirmar_lote(db, nova["lote_id"])
    assert resposta["inseridas"] == 2


# --------------------------------------------------------------------------
# Validação contábil dos saldos declarados
# --------------------------------------------------------------------------


def test_divergencia_de_saldo_aparece_na_previa(db):
    conteudo = (
        "Saldo inicial;;1.000,00\n"
        "Data;Histórico;Valor\n"
        "05/03/2026;PIX RECEBIDO;500,00\n"
        "Saldo final;;2.000,00\n"
    ).encode("utf-8")
    previa = recebe(db, conteudo, nome="saldo_torto.csv")
    assert len(previa["saldos"]) == 2
    assert len(previa["divergencias"]) == 1
    divergencia = previa["divergencias"][0]
    assert divergencia["tipo"] == "saldo_nao_fecha"
    assert divergencia["esperado_centavos"] == 150_000
    assert divergencia["informado_centavos"] == 200_000
    assert divergencia["diferenca_centavos"] == 50_000

    # Divergência não bloqueia sozinha; saldos vão para a tabela na confirmação.
    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert resposta["status"] == "confirmado"
    assert conta_linhas(db, "saldos") == 2


def test_saldo_que_fecha_nao_gera_divergencia(db):
    conteudo = (
        "Saldo inicial;;1.000,00\n"
        "Data;Histórico;Valor\n"
        "05/03/2026;PIX RECEBIDO;500,00\n"
        "Saldo final;;1.500,00\n"
    ).encode("utf-8")
    previa = recebe(db, conteudo, nome="saldo_ok.csv")
    assert previa["divergencias"] == []


# --------------------------------------------------------------------------
# Cartão: tipagem, ciclo de fatura e ausência de fechamento
# --------------------------------------------------------------------------


def test_cartao_compras_vinculadas_a_fatura_do_ciclo(db):
    with db:
        db.execute(
            "INSERT INTO cartoes (nome, dia_fechamento, dia_vencimento)"
            " VALUES ('Cartao Roxo', 10, 20)"
        )
    conteudo = (
        "Data;Estabelecimento;Valor\n"
        "05/03/2026;LIVRARIA CENTRAL;120,00\n"  # antes do fechamento → vence 20/03
        "15/03/2026;RESTAURANTE BOM;80,00\n"  # depois do fechamento → vence 20/04
        "16/03/2026;LIVRARIA CENTRAL;-20,00\n"  # estorno
    ).encode("utf-8")
    previa = pipeline.receber_arquivo(
        db, conteudo, "fatura.csv", "fatura_cartao", cartao="Cartao Roxo"
    )
    assert "erro" not in previa
    tipos = [t["tipo"] for t in previa["amostra"]]
    assert tipos == ["compra", "compra", "estorno"]
    assert previa["totais"]["compras_cartao_centavos"] == 20_000

    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert resposta["inseridas"] == 3

    faturas = db.execute("SELECT vence_em FROM faturas ORDER BY vence_em").fetchall()
    assert [f["vence_em"] for f in faturas] == ["2026-03-20", "2026-04-20"]
    linhas = db.execute(
        "SELECT tipo, direcao, fatura_id FROM transacoes ORDER BY data_operacao"
    ).fetchall()
    assert all(l["fatura_id"] is not None for l in linhas)
    assert (linhas[0]["tipo"], linhas[0]["direcao"]) == ("compra", "debito")
    assert (linhas[2]["tipo"], linhas[2]["direcao"]) == ("estorno", "credito")


def test_cartao_sem_dia_fechamento_deixa_fatura_nula_com_aviso(db):
    conteudo = (
        "Data;Estabelecimento;Valor\n05/03/2026;LOJA DE FERRAMENTAS;90,00\n"
    ).encode("utf-8")
    previa = pipeline.receber_arquivo(
        db, conteudo, "fatura2.csv", "fatura_cartao", cartao="Cartao Cinza"
    )
    resposta = pipeline.confirmar_lote(db, previa["lote_id"])
    assert resposta["inseridas"] == 1
    assert any("fechamento" in aviso for aviso in resposta["avisos"])
    linha = db.execute("SELECT fatura_id FROM transacoes").fetchone()
    assert linha["fatura_id"] is None
    assert conta_linhas(db, "faturas") == 0


# --------------------------------------------------------------------------
# Validações de entrada do upload
# --------------------------------------------------------------------------


def test_formato_desconhecido_vira_status_falhou(db):
    lixo = bytes([0, 1, 2, 3]) * 100  # binário sem assinatura conhecida
    resposta = recebe(db, lixo, nome="binario.bin")
    assert resposta["status"] == "falhou"
    assert "erro" in resposta
    arquivo = db.execute("SELECT status, erro FROM arquivos").fetchone()
    assert arquivo["status"] == "falhou"
    assert arquivo["erro"]


def test_limite_de_tamanho_recusa_sem_gravar(db, monkeypatch):
    monkeypatch.setenv("CELESTIA_MAX_UPLOAD_MB", "1")
    resposta = recebe(db, b"a" * (1024 * 1024 + 1), nome="grande.csv")
    assert resposta["status"] == "falhou"
    assert "CELESTIA_MAX_UPLOAD_MB" in resposta["erro"]
    assert conta_linhas(db, "arquivos") == 0


def test_tipo_documento_invalido(db):
    resposta = pipeline.receber_arquivo(db, CSV_FELIZ, "x.csv", "nota_fiscal")
    assert resposta["status"] == "falhou"
    assert "tipo_documento" in resposta["erro"]
    assert conta_linhas(db, "arquivos") == 0
