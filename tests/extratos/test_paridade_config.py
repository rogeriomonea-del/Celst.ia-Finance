"""Paridade da importação de config com a regra de status da planilha.

A planilha do usuário deriva o status com ``SE(Data>HOJE;"A vencer";
SE(Valor>0;"Pago";"Vencido"))`` — a DATA vem antes do valor: fatura futura
fica "a vencer" (aberta) mesmo com pagamento anotado na coluna Valor.

Fixture 100% sintética (nunca dados reais). O teste de paridade contra o
arquivo real roda só quando ele existe fora do repo (senão é pulado).
"""

from __future__ import annotations

import io
import os
import sqlite3
from datetime import date
from typing import Iterator, List, Tuple

import pytest
from openpyxl import Workbook

from engine.extratos import config_planilha, store

#: Data de referência fixa dos testes (independe do relógio da máquina).
HOJE = date(2026, 8, 7)

#: Caminho do arquivo REAL (fora do repo — NUNCA copiar para cá).
ARQUIVO_REAL = (
    "/root/.claude/uploads/6aea3c47-376a-58c6-94cd-d131738fe2b6/"
    "b7134f38-Dashboard_de_Faturas.xlsx"
)


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Iterator[sqlite3.Connection]:
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))
    with store.conexao() as conexao:
        yield conexao


def _planilha_sintetica(linhas: List[Tuple[str, str, int]]) -> bytes:
    """Monta um xlsx no layout Dashboard de Faturas: (data ISO, cartão, valor R$ texto)."""
    wb = Workbook()
    aba = wb.active
    aba.title = "Faturas"
    aba.append(["Data", "Dia", "Mês", "Ano", "Cartão", "Valor", "Status"])
    for data_iso, cartao, valor_centavos in linhas:
        quando = date.fromisoformat(data_iso)
        valor = (
            f"{valor_centavos // 100},{valor_centavos % 100:02d}"
            if valor_centavos else ""
        )
        aba.append(
            [quando.strftime("%d/%m/%Y"), quando.day, quando.month,
             quando.year, cartao, valor, ""]
        )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_status_segue_regra_da_planilha_data_antes_do_valor(db) -> None:
    """Futura com pagamento anotado = "a vencer" (aberta), nunca "paga"."""
    conteudo = _planilha_sintetica([
        ("2026-05-15", "Cartao Teste", 100_00),   # passada, paga
        ("2026-06-15", "Cartao Teste", 0),        # passada, sem pagamento → vencida
        ("2026-09-15", "Cartao Teste", 200_00),   # FUTURA com valor → a vencer
        ("2026-10-15", "Cartao Teste", 0),        # futura sem valor → a vencer
    ])
    resultado = config_planilha.confirmar_config(
        db, conteudo, "sintetica.xlsx", hoje=HOJE
    )
    assert resultado["faturas_criadas"] == 4
    por_vencimento = {
        linha["vence_em"]: linha["status"]
        for linha in db.execute("SELECT vence_em, status FROM faturas")
    }
    assert por_vencimento == {
        "2026-05-15": "paga",
        "2026-06-15": "vencida",
        "2026-09-15": "aberta",
        "2026-10-15": "aberta",
    }
    # KPIs por status batem com a fórmula da planilha.
    soma = {
        linha["status"]: linha["s"]
        for linha in db.execute(
            "SELECT status, SUM(pago_centavos) s FROM faturas GROUP BY status"
        )
    }
    assert soma["paga"] == 100_00
    assert soma["aberta"] == 200_00  # "a vencer" carrega o valor anotado
    assert soma["vencida"] == 0


def test_reimportacao_e_idempotente_com_fatura_futura_paga(db) -> None:
    """Reimportar a mesma planilha não muda nada — nem o status de futuras."""
    conteudo = _planilha_sintetica([
        ("2026-09-15", "Cartao Teste", 200_00),
        ("2026-05-15", "Cartao Teste", 100_00),
    ])
    config_planilha.confirmar_config(db, conteudo, "sintetica.xlsx", hoje=HOJE)
    previa = config_planilha.previa_config(db, conteudo, "sintetica.xlsx", hoje=HOJE)
    assert previa["cartoes_novos"] == []
    assert previa["cartoes_atualizados"] == []
    assert previa["faturas_novas"] == []
    assert previa["faturas_atualizadas"] == []
    assert previa["conflitos"] == []


@pytest.mark.skipif(
    not os.path.exists(ARQUIVO_REAL),
    reason="planilha real fora do repo indisponível neste ambiente",
)
def test_paridade_com_planilha_real(db) -> None:
    """Verdades-base da Dashboard_de_Faturas real (HOJE fixo = 2026-08-07)."""
    with open(ARQUIVO_REAL, "rb") as arquivo:
        conteudo = arquivo.read()
    resultado = config_planilha.confirmar_config(
        db, conteudo, "Dashboard_de_Faturas.xlsx", hoje=HOJE
    )
    assert resultado["cartoes_criados"] == 5
    assert resultado["erros"] == []

    dias = {
        linha["nome"]: linha["dia_vencimento"]
        for linha in db.execute("SELECT nome, dia_vencimento FROM cartoes")
    }
    assert dias == {
        "Itau The One": 1, "C6 Carbon": 4, "XP Infiniti": 15,
        "Latam Pass": 20, "Mercado Pago": 20,
    }

    total = db.execute("SELECT SUM(pago_centavos) s FROM faturas").fetchone()["s"]
    assert total == 15054591  # TOTAL GERAL R$ 150.545,91

    soma = {
        linha["status"]: linha["s"] or 0
        for linha in db.execute(
            "SELECT status, SUM(pago_centavos) s FROM faturas GROUP BY status"
        )
    }
    assert soma.get("paga", 0) == 9551511          # PAGO
    assert soma.get("aberta", 0) == 5503080        # A VENCER
    assert soma.get("vencida", 0) == 0             # VENCIDO

    maio = {
        linha["nome"]: linha["pago_centavos"]
        for linha in db.execute(
            "SELECT c.nome, f.pago_centavos FROM faturas f"
            " JOIN cartoes c ON c.id = f.cartao_id"
            " WHERE f.competencia = '2026-05' AND f.pago_centavos > 0"
        )
    }
    assert maio == {
        "XP Infiniti": 1279100, "Latam Pass": 722500, "Mercado Pago": 12779,
    }
