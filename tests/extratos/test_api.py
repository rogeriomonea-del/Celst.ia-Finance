"""Testes da API de extratos (``engine.extratos.api.tratar`` chamado direto).

Sem servidor HTTP: o contrato é a função pura ``tratar(metodo, caminho,
params, corpo, cabecalhos)``. Fixtures 100% sintéticas — nenhum dado real.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import pytest

from engine.extratos import api

# --------------------------------------------------------------------------
# Infra dos testes
# --------------------------------------------------------------------------


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    """Dados isolados em tmp_path e ambiente limpo (sem token, sem Vercel)."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CELESTIA_API_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CELESTIA_DATA_DURAVEL", raising=False)
    monkeypatch.delenv("CELESTIA_MAX_UPLOAD_MB", raising=False)
    return tmp_path


CSV_FELIZ = (
    "Data;Histórico;Valor;Documento\n"
    "05/03/2026;PIX RECEBIDO CLIENTE;1.000,00;\n"
    "06/03/2026;PAGAMENTO FATURA CARTAO XPTO;-400,00;\n"
    "07/03/2026;MERCADO BOM PRECO;-100,00;\n"
).encode("utf-8")

FRONTEIRA = "fronteira-teste-123"


def multipart(
    campos: Dict[str, str],
    arquivo: Optional[Tuple[str, bytes]] = None,
) -> Tuple[bytes, Dict[str, str]]:
    """Monta um corpo multipart/form-data sintético (campos + arquivo)."""
    pedacos = []
    for nome, valor in campos.items():
        pedacos.append(
            (
                f"--{FRONTEIRA}\r\n"
                f'Content-Disposition: form-data; name="{nome}"\r\n\r\n'
                f"{valor}\r\n"
            ).encode("utf-8")
        )
    if arquivo is not None:
        nome_arquivo, dados = arquivo
        pedacos.append(
            (
                f"--{FRONTEIRA}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{nome_arquivo}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            + dados
            + b"\r\n"
        )
    corpo = b"".join(pedacos) + f"--{FRONTEIRA}--\r\n".encode("utf-8")
    return corpo, {"Content-Type": f"multipart/form-data; boundary={FRONTEIRA}"}


def chama(
    metodo: str,
    caminho: str,
    params: Optional[Dict[str, Any]] = None,
    corpo: bytes = b"",
    cabecalhos: Optional[Dict[str, str]] = None,
) -> Tuple[Any, ...]:
    return tuple(api.tratar(metodo, caminho, params or {}, corpo, cabecalhos or {}))


def chama_json(
    metodo: str,
    caminho: str,
    params: Optional[Dict[str, Any]] = None,
    dados: Optional[Dict[str, Any]] = None,
    cabecalhos: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    corpo = b"" if dados is None else json.dumps(dados).encode("utf-8")
    cabecalhos = dict(cabecalhos or {})
    if dados is not None:
        cabecalhos.setdefault("Content-Type", "application/json")
    resposta = chama(metodo, caminho, params, corpo, cabecalhos)
    return int(resposta[0]), resposta[1]


def envia_csv(
    conteudo: bytes = CSV_FELIZ, nome: str = "extrato.csv"
) -> Tuple[int, Dict[str, Any]]:
    corpo, cabecalhos = multipart(
        {
            "tipo_documento": "extrato_conta",
            "instituicao": "Banco Teste",
            "conta": "Conta Corrente",
        },
        (nome, conteudo),
    )
    status, payload = chama("POST", "/extratos/arquivos", {}, corpo, cabecalhos)[:2]
    return int(status), payload


# --------------------------------------------------------------------------
# Fluxo completo: upload → prévia → confirmar → filtrar → categorizar → painel
# --------------------------------------------------------------------------


def test_fluxo_completo_da_api(ambiente):
    status, previa = envia_csv()
    assert status == 200
    assert previa["status"] == "pronto"
    assert previa["contagens"]["transacoes"] == 3
    lote_id = previa["lote_id"]

    status, resposta = chama_json("POST", f"/extratos/lotes/{lote_id}/confirmar")
    assert status == 200
    assert resposta["status"] == "confirmado"
    assert resposta["inseridas"] == 3

    # GET com filtros: só débitos; pagamento de fatura fica fora das saídas.
    status, lista = chama_json(
        "GET", "/extratos/transacoes", params={"direcao": ["debito"]}
    )
    assert status == 200
    assert lista["total"] == 2
    assert lista["totais"]["saidas_centavos"] == 10_000
    assert lista["totais"]["entradas_centavos"] == 0

    # Busca combinada acha o mercado.
    status, busca = chama_json(
        "GET", "/extratos/transacoes", params={"busca": "mercado"}
    )
    assert status == 200
    assert busca["total"] == 1

    # Cria categoria e aplica na transação de despesa (PATCH auditado).
    status, categoria = chama_json(
        "POST", "/extratos/categorias", dados={"nome": "Mercado Teste"}
    )
    assert status == 200
    despesa = next(item for item in lista["itens"] if item["tipo"] == "despesa")
    status, editada = chama_json(
        "PATCH",
        f"/extratos/transacoes/{despesa['id']}",
        dados={"categoria_id": categoria["id"], "justificativa": "compra de mercado"},
    )
    assert status == 200
    assert editada["categoria_id"] == categoria["id"]
    assert editada["categoria_nome"] == "Mercado Teste"

    # Painel responde com KPIs e gráficos (mesma fonte de filtros da tabela).
    status, painel = chama_json("GET", "/extratos/painel")
    assert status == 200
    assert "kpis" in painel and "graficos" in painel

    # Fluxo nas visões válidas responde; visão inválida é 400.
    status, fluxo = chama_json(
        "GET", "/extratos/fluxo", params={"visao": ["realizado"]}
    )
    assert status == 200
    assert fluxo["visao"] == "realizado"
    status, erro = chama_json("GET", "/extratos/fluxo", params={"visao": ["mensalona"]})
    assert status == 400
    assert "erro" in erro


# --------------------------------------------------------------------------
# Token: com CELESTIA_API_TOKEN, TODAS as rotas exigem Bearer
# --------------------------------------------------------------------------


def test_token_exigido_quando_configurado(ambiente, monkeypatch):
    monkeypatch.setenv("CELESTIA_API_TOKEN", "segredo-sintetico")

    status, resposta = chama_json("GET", "/extratos/transacoes")
    assert status == 401
    assert "erro" in resposta

    status, _ = chama_json(
        "GET",
        "/extratos/transacoes",
        cabecalhos={"Authorization": "Bearer errado"},
    )
    assert status == 401

    status, lista = chama_json(
        "GET",
        "/extratos/transacoes",
        cabecalhos={"Authorization": "Bearer segredo-sintetico"},
    )
    assert status == 200
    assert lista["itens"] == []

    # Mutação também é bloqueada sem o token.
    status, _ = chama_json("POST", "/extratos/lotes/1/confirmar")
    assert status == 401


def test_sem_token_configurado_api_aberta(ambiente):
    status, _ = chama_json("GET", "/extratos/transacoes")
    assert status == 200


# --------------------------------------------------------------------------
# Limite de upload → 413
# --------------------------------------------------------------------------


def test_upload_acima_do_limite_413(ambiente, monkeypatch):
    monkeypatch.setenv("CELESTIA_MAX_UPLOAD_MB", "1")
    grande = b"a" * (1024 * 1024 + 10)
    status, resposta = envia_csv(conteudo=grande, nome="grande.csv")
    assert status == 413
    assert "erro" in resposta


def test_corpo_gigante_recusado_413(ambiente, monkeypatch):
    monkeypatch.setenv("CELESTIA_MAX_UPLOAD_MB", "1")
    corpo = b"x" * (2 * 1024 * 1024 + 8192)  # acima de limite*2 + folga
    status, resposta = chama("POST", "/extratos/arquivos", {}, corpo, {})[:2]
    assert status == 413
    assert "erro" in resposta


# --------------------------------------------------------------------------
# Ids: inexistente → 404; inválido → 400
# --------------------------------------------------------------------------


def test_404_para_ids_inexistentes(ambiente):
    assert chama_json("GET", "/extratos/arquivos/9999")[0] == 404
    assert chama_json("POST", "/extratos/lotes/9999/confirmar")[0] == 404
    assert (
        chama_json(
            "PATCH", "/extratos/transacoes/9999", dados={"tipo": "receita"}
        )[0]
        == 404
    )
    assert (
        chama_json("PATCH", "/extratos/cartoes/9999", dados={"final": "1234"})[0] == 404
    )


def test_400_para_ids_invalidos(ambiente):
    status, resposta = chama_json("GET", "/extratos/arquivos/abc")
    assert status == 400
    assert "erro" in resposta
    assert chama_json("POST", "/extratos/lotes/xyz/confirmar")[0] == 400
    assert (
        chama_json("PATCH", "/extratos/transacoes/1.5", dados={"tipo": "receita"})[0]
        == 400
    )


# --------------------------------------------------------------------------
# Download do original: bytes exatos + Content-Disposition saneado
# --------------------------------------------------------------------------


def test_download_original_devolve_bytes_exatos(ambiente):
    status, previa = envia_csv()
    assert status == 200
    arquivo_id = previa["arquivo_id"]

    resposta = chama("GET", f"/extratos/arquivos/{arquivo_id}/original")
    assert resposta[0] == 200
    assert resposta[1] == CSV_FELIZ  # bytes idênticos, sem transformação
    assert isinstance(resposta[2], str) and resposta[2]
    assert len(resposta) == 4
    disposicao = resposta[3]["Content-Disposition"]
    assert disposicao.startswith("attachment;")
    assert "extrato.csv" in disposicao
    assert "\r" not in disposicao and "\n" not in disposicao


def test_download_com_nome_malicioso_sai_saneado(ambiente):
    status, previa = envia_csv(
        conteudo=CSV_FELIZ + b"\n", nome='../../etc/passwd";x.csv'
    )
    assert status == 200
    resposta = chama("GET", f"/extratos/arquivos/{previa['arquivo_id']}/original")
    assert resposta[0] == 200
    disposicao = resposta[3]["Content-Disposition"]
    assert "../" not in disposicao
    assert '"passwd' not in disposicao.replace('filename="', "", 1)


# --------------------------------------------------------------------------
# Serverless sem storage durável: mutação → 503; leitura → 200
# --------------------------------------------------------------------------


def test_vercel_sem_duravel_bloqueia_mutacao_mas_le(ambiente, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")

    status, resposta = chama_json("POST", "/extratos/lotes/1/confirmar")
    assert status == 503
    assert "efêmero" in resposta["erro"]
    assert "self-host" in resposta["erro"] or "engine.server" in resposta["erro"]

    assert chama_json("PATCH", "/extratos/transacoes/1", dados={"tipo": "receita"})[0] == 503
    assert chama_json("DELETE", "/extratos/conciliacoes/1")[0] == 503

    status, lista = chama_json("GET", "/extratos/transacoes")
    assert status == 200
    assert lista["itens"] == []


def test_vercel_com_duravel_declarado_grava(ambiente, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CELESTIA_DATA_DURAVEL", "1")
    # A mutação passa do bloqueio 503 (aqui: 404 porque o lote não existe).
    assert chama_json("POST", "/extratos/lotes/1/confirmar")[0] == 404


# --------------------------------------------------------------------------
# Sem path traversal
# --------------------------------------------------------------------------


def test_caminho_com_traversal_e_recusado(ambiente):
    status, resposta = chama_json("GET", "/extratos/arquivos/../../etc")
    assert status in (400, 404)
    assert "erro" in resposta
    status, _ = chama_json("GET", "/extratos/arquivos/..")
    assert status in (400, 404)
    status, _ = chama_json("GET", "/extratos/arquivos/../original")
    assert status in (400, 404)


def test_rota_desconhecida_404(ambiente):
    assert chama_json("GET", "/outra/coisa")[0] == 404
    assert chama_json("GET", "/extratos/naoexiste")[0] == 404


# --------------------------------------------------------------------------
# Cartões: validações do PATCH (final com 4 dígitos, dias, limite)
# --------------------------------------------------------------------------


def test_cartao_crud_com_validacoes(ambiente):
    status, cartao = chama_json(
        "POST", "/extratos/cartoes", dados={"nome": "Cartao Sintetico"}
    )
    assert status == 200
    cartao_id = cartao["id"]

    # final inválido → 400; nunca aceita o número completo.
    assert (
        chama_json(
            "PATCH", f"/extratos/cartoes/{cartao_id}", dados={"final": "12"}
        )[0]
        == 400
    )
    assert (
        chama_json(
            "PATCH",
            f"/extratos/cartoes/{cartao_id}",
            dados={"final": "4111111111111111"},
        )[0]
        == 400
    )
    # dia fora de 1..31 → 400; float em dinheiro → 400.
    assert (
        chama_json(
            "PATCH", f"/extratos/cartoes/{cartao_id}", dados={"dia_vencimento": 42}
        )[0]
        == 400
    )
    assert (
        chama_json(
            "PATCH",
            f"/extratos/cartoes/{cartao_id}",
            dados={"limite_total_centavos": 10.5},
        )[0]
        == 400
    )
    # conta pagadora inexistente → 404.
    assert (
        chama_json(
            "PATCH",
            f"/extratos/cartoes/{cartao_id}",
            dados={"conta_pagadora_id": 999},
        )[0]
        == 404
    )

    status, editado = chama_json(
        "PATCH",
        f"/extratos/cartoes/{cartao_id}",
        dados={
            "final": "1234",
            "emissor": "Banco Sintético",
            "bandeira": "BandeiraX",
            "dia_fechamento": 10,
            "dia_vencimento": 20,
            "limite_total_centavos": 500_000,
        },
    )
    assert status == 200
    assert editado["final"] == "1234"
    assert editado["limite_total_centavos"] == 500_000

    status, lista = chama_json("GET", "/extratos/cartoes")
    assert status == 200
    assert [item["nome"] for item in lista["itens"]] == ["Cartao Sintetico"]

    status, faturas = chama_json(
        "GET", "/extratos/faturas", params={"cartao_id": [str(cartao_id)]}
    )
    assert status == 200
    assert faturas["itens"] == []


# --------------------------------------------------------------------------
# Regras: CRUD + aplicar em dry-run e com confirmação
# --------------------------------------------------------------------------


def test_regras_aplicar_dry_run_e_confirmar(ambiente):
    status, previa = envia_csv()
    assert status == 200
    chama_json("POST", f"/extratos/lotes/{previa['lote_id']}/confirmar")

    status, categoria = chama_json(
        "POST", "/extratos/categorias", dados={"nome": "Alimentação Teste"}
    )
    assert status == 200
    status, regra = chama_json(
        "POST",
        "/extratos/regras",
        dados={
            "campo": "descricao",
            "operador": "contem",
            "valor": "mercado",
            "categoria_id": categoria["id"],
        },
    )
    assert status == 200

    # Dry-run: prévia sem gravar nada.
    status, seco = chama_json("POST", "/extratos/regras/aplicar")
    assert status == 200
    assert seco["dry_run"] is True
    assert len(seco["previa"]) == 1
    status, lista = chama_json(
        "GET", "/extratos/transacoes", params={"status_categoria": ["categorizada"]}
    )
    assert lista["total"] == 0

    # Com ?confirmar=1 grava e audita.
    status, aplicado = chama_json(
        "POST", "/extratos/regras/aplicar", params={"confirmar": ["1"]}
    )
    assert status == 200
    assert aplicado["aplicadas"] == 1
    status, lista = chama_json(
        "GET", "/extratos/transacoes", params={"status_categoria": ["categorizada"]}
    )
    assert lista["total"] == 1

    # DELETE desativa (nunca apaga) a regra.
    status, desativada = chama_json("DELETE", f"/extratos/regras/{regra['id']}")
    assert status == 200
    assert desativada["ativo"] is False


# --------------------------------------------------------------------------
# Reenvio idêntico → 409 com orientação de reprocesso
# --------------------------------------------------------------------------


def test_upload_duplicado_responde_409(ambiente):
    status, previa = envia_csv()
    assert status == 200
    status, repetido = envia_csv(nome="outro_nome.csv")  # bytes idênticos
    assert status == 409
    assert repetido["erro_duplicado"] is True
    assert repetido["arquivo_id"] == previa["arquivo_id"]
    assert repetido["pode_reprocessar"] is True
