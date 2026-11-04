"""Testes da importação da planilha de configuração (cartões e faturas).

Fixtures 100% sintéticas — nenhuma planilha real entra no repositório. O
layout reproduz as verdades-base da especificação: tabela
Data|Dia|Mês|Ano|Cartão|Valor|Status + aba oculta "Calc" com a lista de
cartões, dias de vencimento 1/4/15/20/20.
"""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import date, datetime
from typing import Any, Iterator, List, Optional, Sequence, Tuple

import pytest
from openpyxl import Workbook

from engine.extratos import config_planilha, detect, storage, store
from engine.extratos.parsers import planilha_config
from engine.extratos.parsers.base import ContextoParse
from engine.extratos.parsers.planilha_config import parse_config

ADAPTADOR = planilha_config.ADAPTADORES[0]

#: Data de referência fixa — os status de fatura são determinísticos.
HOJE = date(2026, 8, 7)

#: Os 5 cartões da especificação, com dias de vencimento 1/4/15/20/20.
CARTOES_CALC = [
    "Itau The One", "Latam Pass", "XP Infiniti", "C6 Carbon", "Mercado Pago",
]

#: (vencimento, cartão, valor pago na célula) — valores 100% sintéticos.
LINHAS_FATURAS: List[Tuple[date, str, Any]] = [
    (date(2026, 6, 1), "Itau The One", "12.791"),
    (date(2026, 7, 1), "Itau The One", "1.234,56"),
    (date(2026, 9, 1), "Itau The One", None),
    (date(2026, 6, 4), "C6 Carbon", 500),
    (date(2026, 9, 4), "C6 Carbon", None),
    (date(2026, 6, 15), "XP Infiniti", 127.79),
    (date(2026, 7, 15), "XP Infiniti", None),
    (date(2026, 8, 15), "XP Infiniti", None),
    (date(2026, 6, 20), "Latam Pass", "2.000,00"),
    (date(2026, 8, 20), "Latam Pass", None),
    (date(2026, 7, 20), "Mercado Pago", "99,90"),
    (date(2026, 9, 20), "Mercado Pago", None),
]

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


@pytest.fixture(autouse=True)
def _dados_em_tmp(tmp_path, monkeypatch):
    """Nenhum teste pode encostar no diretório de dados real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))


@pytest.fixture()
def db() -> Iterator[sqlite3.Connection]:
    with store.conexao() as conn:
        yield conn


def xlsx_dashboard(
    linhas: Sequence[Tuple[date, str, Any]],
    cartoes_calc: Optional[Sequence[str]] = None,
) -> bytes:
    """Monta um XLSX sintético fiel ao layout Dashboard de Faturas."""
    pasta = Workbook()
    aba = pasta.active
    aba.title = "Faturas"
    aba.append(["Data", "Dia", "Mês", "Ano", "Cartão", "Valor", "Status"])
    for vencimento, cartao, valor in linhas:
        aba.append([
            datetime(vencimento.year, vencimento.month, vencimento.day),
            vencimento.day,
            MESES_PT[vencimento.month],
            vencimento.year,
            cartao,
            valor,
            "",  # na planilha real o status é fórmula — aqui é derivado
        ])
    calc = pasta.create_sheet("Calc")
    calc.append(["Cartões"])
    for nome in cartoes_calc if cartoes_calc is not None else CARTOES_CALC:
        calc.append([nome])
    calc.sheet_state = "hidden"
    buffer = io.BytesIO()
    pasta.save(buffer)
    return buffer.getvalue()


def contexto_de(conteudo: bytes, nome: str, **extras) -> ContextoParse:
    return ContextoParse(
        conteudo=conteudo,
        formato=detect.detectar(conteudo, nome),
        nome_original=nome,
        **extras,
    )


def conta_linhas(db: sqlite3.Connection, tabela: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0])


# --------------------------------------------------------------------------
# Detecção e parse (layout Dashboard)
# --------------------------------------------------------------------------


def test_detecta_alto_somente_para_planilha_config():
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    ctx_config = contexto_de(conteudo, "config.xlsx", tipo_documento="planilha_config")
    assert ADAPTADOR.detecta(ctx_config) == pytest.approx(0.9)
    ctx_extrato = contexto_de(conteudo, "config.xlsx", tipo_documento="extrato_conta")
    assert ADAPTADOR.detecta(ctx_extrato) == 0.0
    ctx_pdf = ContextoParse(
        conteudo=b"%PDF-1.4",
        formato=detect.detectar(b"%PDF-1.4", "doc.pdf"),
        tipo_documento="planilha_config",
    )
    assert ADAPTADOR.detecta(ctx_pdf) == 0.0


def test_parse_config_cartoes_com_dia_de_vencimento_pela_moda():
    config = parse_config(xlsx_dashboard(LINHAS_FATURAS), "config.xlsx", hoje=HOJE)
    assert config.erros == []
    dias = {cartao.nome: cartao.dia_vencimento for cartao in config.cartoes}
    assert dias == {
        "Itau The One": 1,
        "C6 Carbon": 4,
        "XP Infiniti": 15,
        "Latam Pass": 20,
        "Mercado Pago": 20,
    }
    # A planilha NÃO informa emissor/bandeira/final/limite/fechamento:
    # nada é inventado.
    for cartao in config.cartoes:
        assert cartao.emissor is None
        assert cartao.bandeira is None
        assert cartao.final is None
        assert cartao.dia_fechamento is None
        assert cartao.limite_total_centavos is None
    assert config.mapeamento["layout"] == "dashboard_faturas"
    assert config.mapeamento["aba_faturas"] == "Faturas"
    assert config.mapeamento["aba_cartoes"] == "Calc"


def test_parse_config_faturas_centavos_exatos_e_status():
    config = parse_config(xlsx_dashboard(LINHAS_FATURAS), "config.xlsx", hoje=HOJE)
    assert len(config.faturas) == 12
    por_chave = {(f.cartao, f.vence_em): f for f in config.faturas}

    # "12.791" (milhar pt-BR) → 1.279.100 centavos, sem float no caminho.
    paga_itau = por_chave[("Itau The One", date(2026, 6, 1))]
    assert paga_itau.pago_centavos == 1279100
    assert paga_itau.status == "paga"
    assert paga_itau.competencia == "2026-06"

    # célula float 127.79 (como o openpyxl entrega) → exatamente 12779.
    paga_xp = por_chave[("XP Infiniti", date(2026, 6, 15))]
    assert paga_xp.pago_centavos == 12779
    assert paga_xp.status == "paga"

    assert por_chave[("Itau The One", date(2026, 7, 1))].pago_centavos == 123456
    assert por_chave[("Latam Pass", date(2026, 6, 20))].pago_centavos == 200000
    assert por_chave[("Mercado Pago", date(2026, 7, 20))].pago_centavos == 9990

    # Sem pagamento: vencida antes de HOJE, aberta depois.
    assert por_chave[("XP Infiniti", date(2026, 7, 15))].status == "vencida"
    assert por_chave[("XP Infiniti", date(2026, 8, 15))].status == "aberta"
    assert por_chave[("C6 Carbon", date(2026, 9, 4))].status == "aberta"
    assert por_chave[("C6 Carbon", date(2026, 9, 4))].pago_centavos == 0


def test_adaptador_gera_linhas_brutas_e_nenhuma_transacao():
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    resultado = ADAPTADOR.parse(
        contexto_de(conteudo, "config.xlsx", tipo_documento="planilha_config")
    )
    assert resultado.adaptador == "planilha-config"
    assert resultado.transacoes == []
    assert resultado.erros == []
    assert resultado.mapeamento_necessario is None
    # cabeçalho + 12 faturas + cabeçalho da Calc + 5 cartões = 19 linhas
    assert len(resultado.linhas_brutas) == 19
    assert resultado.periodo_inicio == date(2026, 6, 1)
    assert resultado.periodo_fim == date(2026, 9, 20)
    assert any("planilha de configuração" in aviso for aviso in resultado.avisos)


# --------------------------------------------------------------------------
# Layout (b): cadastro genérico
# --------------------------------------------------------------------------


def test_parse_config_cadastro_generico_csv():
    conteudo = (
        "Instituição;Cartão;Emissor;Bandeira;Final;Limite;"
        "Dia de Fechamento;Dia de Vencimento;Conta Pagadora\n"
        "Banco Sintético;Cartão Roxo;Banco Sintético;Visa;9876;15.000,00;28;7;"
        "Conta Sintética\n"
    ).encode("utf-8")
    config = parse_config(conteudo, "cadastro.csv", hoje=HOJE)
    assert config.erros == []
    assert config.mapeamento["layout"] == "cadastro"
    assert len(config.cartoes) == 1
    cartao = config.cartoes[0]
    assert cartao.nome == "Cartão Roxo"
    assert cartao.emissor == "Banco Sintético"
    assert cartao.bandeira == "Visa"
    assert cartao.final == "9876"
    assert cartao.limite_total_centavos == 1500000
    assert cartao.dia_fechamento == 28
    assert cartao.dia_vencimento == 7
    assert cartao.conta_pagadora == "Conta Sintética"
    assert cartao.instituicao == "Banco Sintético"


def test_confirmar_cadastro_generico_cria_instituicao_e_cartao(db):
    conteudo = (
        "Instituição;Cartão;Bandeira;Final;Limite;Dia de Vencimento\n"
        "Banco Sintético;Cartão Roxo;Visa;9876;15.000,00;7\n"
    ).encode("utf-8")
    resultado = config_planilha.confirmar_config(
        db, conteudo, "cadastro.csv", aprovado_por="teste", hoje=HOJE
    )
    assert resultado["cartoes_criados"] == 1
    instituicao = db.execute(
        "SELECT * FROM instituicoes WHERE nome = ?", ("Banco Sintético",)
    ).fetchone()
    assert instituicao is not None
    cartao = db.execute(
        "SELECT * FROM cartoes WHERE nome = ?", ("Cartão Roxo",)
    ).fetchone()
    assert cartao["instituicao_id"] == instituicao["id"]
    assert cartao["bandeira"] == "Visa"
    assert cartao["final"] == "9876"
    assert cartao["limite_total_centavos"] == 1500000
    assert cartao["dia_vencimento"] == 7


# --------------------------------------------------------------------------
# Prévia (nada gravado) e confirmação (tudo gravado)
# --------------------------------------------------------------------------


def test_previa_lista_novidades_sem_gravar_nada(db, tmp_path):
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    previa = config_planilha.previa_config(db, conteudo, "config.xlsx", hoje=HOJE)

    assert previa["erros"] == []
    assert previa["conflitos"] == []
    assert previa["duplicidades"] == []
    assert len(previa["cartoes_novos"]) == 5
    assert len(previa["faturas_novas"]) == 12
    assert previa["cartoes_atualizados"] == []
    assert previa["faturas_atualizadas"] == []
    assert previa["mapeamento"]["layout"] == "dashboard_faturas"

    # NADA foi gravado: banco vazio e storage intocado.
    for tabela in ("cartoes", "faturas", "arquivos", "config_versoes", "auditoria"):
        assert conta_linhas(db, tabela) == 0, tabela
    assert not (tmp_path / "originais").exists()


def test_confirmar_grava_cartoes_faturas_versao_auditoria_e_arquivo(db):
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    resultado = config_planilha.confirmar_config(
        db, conteudo, "config.xlsx", aprovado_por="usuario-teste", hoje=HOJE
    )
    assert resultado["cartoes_criados"] == 5
    assert resultado["faturas_criadas"] == 12
    assert resultado["conflitos_pendentes"] == []
    assert resultado["erros"] == []

    assert conta_linhas(db, "cartoes") == 5
    assert conta_linhas(db, "faturas") == 12

    cartao = db.execute(
        "SELECT * FROM cartoes WHERE nome = ?", ("XP Infiniti",)
    ).fetchone()
    assert cartao["dia_vencimento"] == 15
    assert cartao["emissor"] is None  # nada inventado

    fatura = db.execute(
        "SELECT f.* FROM faturas f JOIN cartoes c ON c.id = f.cartao_id"
        " WHERE c.nome = ? AND f.vence_em = ?",
        ("XP Infiniti", "2026-06-15"),
    ).fetchone()
    assert fatura["pago_centavos"] == 12779
    assert fatura["status"] == "paga"
    assert fatura["competencia"] == "2026-06"

    vencida = db.execute(
        "SELECT f.status FROM faturas f JOIN cartoes c ON c.id = f.cartao_id"
        " WHERE c.nome = ? AND f.vence_em = ?",
        ("XP Infiniti", "2026-07-15"),
    ).fetchone()
    assert vencida["status"] == "vencida"

    versoes = db.execute("SELECT * FROM config_versoes").fetchall()
    assert len(versoes) == 1
    assert versoes[0]["aprovado_por"] == "usuario-teste"
    resumo = json.loads(versoes[0]["resumo_json"])
    assert resumo["cartoes_criados"] == 5
    assert resumo["faturas_criadas"] == 12

    # Auditoria: uma criação por cartão e por fatura, no mínimo.
    criados_cartao = db.execute(
        "SELECT COUNT(*) FROM auditoria WHERE entidade='cartao' AND acao='criar'"
    ).fetchone()[0]
    criadas_fatura = db.execute(
        "SELECT COUNT(*) FROM auditoria WHERE entidade='fatura' AND acao='criar'"
    ).fetchone()[0]
    assert (criados_cartao, criadas_fatura) == (5, 12)

    # Arquivo registrado como confirmado e original legível no storage.
    arquivo = db.execute("SELECT * FROM arquivos").fetchone()
    assert arquivo["tipo_documento"] == "planilha_config"
    assert arquivo["status"] == "confirmado"
    assert storage.ler(arquivo["caminho_objeto"]) == conteudo
    assert resultado["arquivo_id"] == arquivo["id"]
    assert resultado["versao_id"] == versoes[0]["id"]


def test_reimportar_o_mesmo_arquivo_e_idempotente(db):
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    config_planilha.confirmar_config(db, conteudo, "config.xlsx", hoje=HOJE)
    segunda = config_planilha.confirmar_config(db, conteudo, "config.xlsx", hoje=HOJE)

    assert segunda["cartoes_criados"] == 0
    assert segunda["cartoes_atualizados"] == 0
    assert segunda["faturas_criadas"] == 0
    assert segunda["faturas_atualizadas"] == 0
    assert segunda["conflitos_pendentes"] == []

    assert conta_linhas(db, "cartoes") == 5
    assert conta_linhas(db, "faturas") == 12
    assert conta_linhas(db, "arquivos") == 1  # mesmo sha256 = mesmo arquivo
    # Cada confirmação registra a sua versão de configuração.
    assert conta_linhas(db, "config_versoes") == 2


def test_nao_sobrescreve_campo_manual_com_none(db):
    with db:
        db.execute(
            "INSERT INTO cartoes (nome, emissor, dia_vencimento) VALUES (?,?,?)",
            ("Itau The One", "Emissor Manual", 1),
        )
    conteudo = xlsx_dashboard(LINHAS_FATURAS)
    resultado = config_planilha.confirmar_config(db, conteudo, "config.xlsx", hoje=HOJE)
    assert resultado["cartoes_criados"] == 4  # o Itau já existia
    assert resultado["conflitos_pendentes"] == []
    cartao = db.execute(
        "SELECT * FROM cartoes WHERE nome = ?", ("Itau The One",)
    ).fetchone()
    assert cartao["emissor"] == "Emissor Manual"  # None da planilha não apaga
    assert cartao["dia_vencimento"] == 1


def test_conflito_de_dia_vencimento_nao_sobrescreve_sem_flag(db):
    config_planilha.confirmar_config(
        db, xlsx_dashboard(LINHAS_FATURAS), "config.xlsx", hoje=HOJE
    )
    # Segunda planilha diz que o C6 Carbon vence dia 10 (cadastrado: dia 4).
    divergente = xlsx_dashboard(
        [
            (date(2026, 6, 10), "C6 Carbon", "750,00"),
            (date(2026, 9, 10), "C6 Carbon", None),
        ],
        cartoes_calc=["C6 Carbon"],
    )

    previa = config_planilha.previa_config(db, divergente, "config2.xlsx", hoje=HOJE)
    assert len(previa["conflitos"]) == 1
    conflito = previa["conflitos"][0]
    assert conflito["entidade"] == "cartao"
    assert conflito["nome"] == "C6 Carbon"
    assert conflito["campo"] == "dia_vencimento"
    assert conflito["valor_atual"] == 4
    assert conflito["valor_planilha"] == 10

    # Sem a flag: o conflito fica pendente e o cadastro NÃO muda.
    resultado = config_planilha.confirmar_config(
        db, divergente, "config2.xlsx", hoje=HOJE
    )
    assert len(resultado["conflitos_pendentes"]) == 1
    assert resultado["conflitos_aplicados"] == []
    cartao = db.execute(
        "SELECT dia_vencimento FROM cartoes WHERE nome = ?", ("C6 Carbon",)
    ).fetchone()
    assert cartao["dia_vencimento"] == 4
    # As faturas das novas datas entram normalmente (vencimentos diferentes).
    assert resultado["faturas_criadas"] == 2

    # Com sobrescrever=True o conflito é aplicado e auditado.
    resultado = config_planilha.confirmar_config(
        db, divergente, "config2.xlsx", aprovado_por="usuario-teste",
        sobrescrever=True, hoje=HOJE,
    )
    assert len(resultado["conflitos_aplicados"]) == 1
    cartao = db.execute(
        "SELECT dia_vencimento FROM cartoes WHERE nome = ?", ("C6 Carbon",)
    ).fetchone()
    assert cartao["dia_vencimento"] == 10
    sobrescritas = db.execute(
        "SELECT * FROM auditoria WHERE entidade='cartao' AND acao='sobrescrever'"
        " AND campo='dia_vencimento'"
    ).fetchall()
    assert len(sobrescritas) == 1
    assert (sobrescritas[0]["valor_anterior"], sobrescritas[0]["valor_novo"]) == ("4", "10")


def test_linha_duplicada_no_arquivo_ultima_prevalece(db):
    linhas = LINHAS_FATURAS + [(date(2026, 6, 1), "Itau The One", "3.000,00")]
    conteudo = xlsx_dashboard(linhas)

    previa = config_planilha.previa_config(db, conteudo, "config.xlsx", hoje=HOJE)
    assert len(previa["duplicidades"]) == 1
    assert previa["duplicidades"][0]["cartao"] == "Itau The One"
    assert len(previa["faturas_novas"]) == 12  # a duplicada não dobra

    config_planilha.confirmar_config(db, conteudo, "config.xlsx", hoje=HOJE)
    assert conta_linhas(db, "faturas") == 12
    fatura = db.execute(
        "SELECT f.pago_centavos FROM faturas f JOIN cartoes c ON c.id = f.cartao_id"
        " WHERE c.nome = ? AND f.vence_em = ?",
        ("Itau The One", "2026-06-01"),
    ).fetchone()
    assert fatura["pago_centavos"] == 300000  # a última linha prevalece
