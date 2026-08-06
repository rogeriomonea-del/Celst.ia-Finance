"""Testes de ``api/planilha.py`` — parser de multipart e roteamento de erros.

Nenhum teste sobe servidor HTTP nem lê arquivo do disco: o handler é exercitado
com um objeto falso (``rfile``/``wfile`` em memória) e o motor é injetado.
"""

from __future__ import annotations

import base64
import builtins
import email.message
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

# ``api/`` não é pacote (o runtime da Vercel carrega o arquivo direto).
_spec = importlib.util.spec_from_file_location("api_planilha", RAIZ / "api" / "planilha.py")
assert _spec and _spec.loader
planilha = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = planilha  # @dataclass exige o módulo registrado
_spec.loader.exec_module(planilha)


# --------------------------------------------------------------------------
# Ajudantes: corpos sintéticos
# --------------------------------------------------------------------------

BOUNDARY = "----CelestiaBoundary123"

# Menor .xlsx plausível para os testes: basta começar com a assinatura ZIP.
XLSX_FAKE = b"PK\x03\x04" + b"conteudo-binario\r\n--nao-e-boundary\r\n" + bytes(range(256))
CSV_FAKE = "Ticker;Valor\r\nPETR4;30,50\r\n".encode("utf-8")


def _parte(nome: str, conteudo: bytes, nome_arquivo: str = "", tipo: str = "") -> bytes:
    """Monta uma parte multipart com CRLF, como fazem os browsers."""
    disposicao = f'Content-Disposition: form-data; name="{nome}"'
    if nome_arquivo:
        disposicao += f'; filename="{nome_arquivo}"'
    cabecalhos = [disposicao]
    if tipo:
        cabecalhos.append(f"Content-Type: {tipo}")
    return ("\r\n".join(cabecalhos) + "\r\n\r\n").encode("utf-8") + conteudo


def montar_multipart(
    partes: List[bytes],
    boundary: str = BOUNDARY,
    quebra: bytes = b"\r\n",
    fechar: bool = True,
    preambulo: bytes = b"",
) -> bytes:
    delim = b"--" + boundary.encode()
    corpo = bytearray(preambulo)
    for parte in partes:
        corpo += delim + quebra + parte + quebra
    if fechar:
        corpo += delim + b"--" + quebra
    return bytes(corpo)


def corpo_arquivo(
    conteudo: bytes = XLSX_FAKE,
    nome_arquivo: str = "Carteira.xlsm",
    campo: str = "file",
) -> Tuple[bytes, str]:
    corpo = montar_multipart(
        [
            _parte("periodo", b"2025"),
            _parte(campo, conteudo, nome_arquivo, "application/vnd.ms-excel.sheet.macroEnabled.12"),
        ]
    )
    return corpo, f"multipart/form-data; boundary={BOUNDARY}"


def motor_falso(registro: Dict[str, Any]) -> Any:
    """Analisador injetável que só registra o que recebeu."""

    def _analisar(dados: bytes, nome: str) -> Dict[str, Any]:
        registro["dados"] = dados
        registro["nome"] = nome
        return {"file_name": nome, "bytes": len(dados), "portfolio": {"total_value": 1.5}}

    return _analisar


# --------------------------------------------------------------------------
# Cabeçalhos e boundary
# --------------------------------------------------------------------------


def test_dividir_parametros_respeita_aspas() -> None:
    partes = planilha.dividir_parametros('form-data; name="file"; filename="a;b.xlsx"')
    assert partes == ["form-data", 'name="file"', 'filename="a;b.xlsx"']


def test_parametros_cabecalho_com_escape_e_rfc5987() -> None:
    principal, params = planilha.parametros_cabecalho(
        'form-data; name="file"; filename="Carteira \\"2025\\".xlsm"'
    )
    assert principal == "form-data"
    assert params["name"] == "file"
    assert params["filename"] == 'Carteira "2025".xlsm'

    _, params = planilha.parametros_cabecalho(
        "form-data; name=file; filename*=UTF-8''Posi%C3%A7%C3%A3o%20B3.xlsx"
    )
    assert params["filename"] == "Posição B3.xlsx"


@pytest.mark.parametrize(
    "content_type,esperado",
    [
        (f"multipart/form-data; boundary={BOUNDARY}", BOUNDARY),
        (f'multipart/form-data; charset=utf-8; boundary="{BOUNDARY}"', BOUNDARY),
        ("MULTIPART/FORM-DATA; BOUNDARY=abc", "abc"),
        ("multipart/form-data", ""),
        ("application/json", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_extrair_boundary(content_type: Any, esperado: str) -> None:
    assert planilha.extrair_boundary(content_type) == esperado


# --------------------------------------------------------------------------
# Parser de multipart
# --------------------------------------------------------------------------


def test_multipart_preserva_bytes_e_le_campos() -> None:
    corpo, _ = corpo_arquivo()
    partes = planilha.analisar_multipart(corpo, BOUNDARY)
    assert [p.nome for p in partes] == ["periodo", "file"]
    assert partes[0].conteudo == b"2025"
    assert partes[0].e_arquivo is False
    arquivo = partes[1]
    assert arquivo.nome_arquivo == "Carteira.xlsm"
    assert arquivo.conteudo == XLSX_FAKE  # binário com CRLF interno, byte a byte
    assert arquivo.tipo.startswith("application/")


def test_multipart_aceita_lf_preambulo_e_sem_fechamento() -> None:
    corpo = montar_multipart(
        [_parte("file", CSV_FAKE, "dados.csv")],
        quebra=b"\n",
        fechar=False,
        preambulo=b"texto que clientes antigos mandam\n",
    ).replace(b"\r\n\r\n", b"\n\n")
    partes = planilha.analisar_multipart(corpo, BOUNDARY)
    assert len(partes) == 1
    assert partes[0].nome_arquivo == "dados.csv"
    assert partes[0].conteudo.startswith(b"Ticker;Valor")


def test_multipart_degenerado_devolve_lista_vazia() -> None:
    assert planilha.analisar_multipart(b"", BOUNDARY) == []
    assert planilha.analisar_multipart(b"qualquer coisa", "") == []
    assert planilha.analisar_multipart(b"sem nenhum delimitador", BOUNDARY) == []
    assert planilha.analisar_multipart(b"--" + BOUNDARY.encode() + b"--\r\n", BOUNDARY) == []


def test_parte_do_arquivo_prioriza_campos_conhecidos() -> None:
    partes = planilha.analisar_multipart(*_desmonta(corpo_arquivo(campo="arquivo")))
    assert planilha.parte_do_arquivo(partes).nome == "arquivo"

    # Sem campo conhecido: cai na primeira parte que tem filename.
    corpo = montar_multipart(
        [_parte("obs", b"nada"), _parte("anexo", XLSX_FAKE, "x.xlsx")]
    )
    partes = planilha.analisar_multipart(corpo, BOUNDARY)
    assert planilha.parte_do_arquivo(partes).nome == "anexo"

    # Só campos de texto: nenhuma parte de arquivo.
    partes = planilha.analisar_multipart(montar_multipart([_parte("obs", b"nada")]), BOUNDARY)
    assert planilha.parte_do_arquivo(partes) is None
    assert planilha.parte_do_arquivo([]) is None


def _desmonta(par: Tuple[bytes, str]) -> Tuple[bytes, str]:
    """(corpo, content_type) -> (corpo, boundary)."""
    corpo, content_type = par
    return corpo, planilha.extrair_boundary(content_type)


# --------------------------------------------------------------------------
# Nome, extensão e validação
# --------------------------------------------------------------------------


def test_nome_seguro_remove_caminho_e_controle() -> None:
    assert planilha.nome_seguro("C:\\Users\\rog\\Carteira.xlsm") == "Carteira.xlsm"
    assert planilha.nome_seguro("../../etc/passwd") == "passwd"
    assert planilha.nome_seguro("a\x00b.csv") == "ab.csv"
    assert planilha.nome_seguro("") == ""
    assert planilha.nome_seguro(None) == ""
    assert planilha.extensao("Carteira.XLSM") == ".xlsm"
    assert planilha.extensao("sem_extensao") == ""


def test_validar_arquivo_infere_nome_quando_falta() -> None:
    dados, nome = planilha.validar_arquivo(XLSX_FAKE, "")
    assert dados is XLSX_FAKE and nome == "planilha.xlsx"
    _, nome = planilha.validar_arquivo(CSV_FAKE, "")
    assert nome == "planilha.csv"
    _, nome = planilha.validar_arquivo(CSV_FAKE, "extrato")
    assert nome == "extrato.csv"


@pytest.mark.parametrize(
    "dados,nome,status",
    [
        (b"", "a.xlsx", 400),
        (b"%PDF-1.7 nada a ver", "relatorio.pdf", 400),
        (b"%PDF-1.7 nada a ver", "relatorio.xlsx", 400),  # extensão ok, assinatura não
        (b"PK\x03\x04" * 10, "a.xlsx", 0),
        (CSV_FAKE, "a.csv", 0),
        (CSV_FAKE, "a.tsv", 0),
        (b"P" * (planilha.LIMITE_BYTES + 1), "grande.csv", 413),
    ],
)
def test_validar_arquivo_status(dados: bytes, nome: str, status: int) -> None:
    if status == 0:
        assert planilha.validar_arquivo(dados, nome)[0] == dados
        return
    with pytest.raises(planilha.ErroRequisicao) as exc:
        planilha.validar_arquivo(dados, nome)
    assert exc.value.status == status


# --------------------------------------------------------------------------
# Corpo JSON
# --------------------------------------------------------------------------


def test_json_aceita_base64_puro_e_data_uri() -> None:
    b64 = base64.b64encode(XLSX_FAKE).decode()
    corpo = json.dumps({"file_base64": b64, "file_name": "Carteira.xlsm"}).encode()
    assert planilha.arquivo_do_json(corpo) == (XLSX_FAKE, "Carteira.xlsm")

    data_uri = "data:application/vnd.ms-excel;base64," + b64
    dados, nome = planilha.arquivo_do_json(
        json.dumps({"fileBase64": data_uri, "fileName": "b.xlsx"}).encode()
    )
    assert dados == XLSX_FAKE and nome == "b.xlsx"


@pytest.mark.parametrize(
    "corpo,status",
    [
        (b"{isso nao e json", 400),
        (b"[]", 400),
        (b"{}", 400),
        (json.dumps({"file_base64": "   "}).encode(), 400),
        (json.dumps({"file_base64": "@@@nao-e-base64@@@"}).encode(), 400),
        (json.dumps({"file_base64": "A" * (planilha.LIMITE_BYTES // 3 * 4 + 4096)}).encode(), 413),
    ],
)
def test_json_invalido(corpo: bytes, status: int) -> None:
    with pytest.raises(planilha.ErroRequisicao) as exc:
        planilha.arquivo_do_json(corpo)
    assert exc.value.status == status
    assert exc.value.mensagem  # mensagem sempre preenchida, em pt-BR


# --------------------------------------------------------------------------
# extrair_arquivo: roteamento por Content-Type
# --------------------------------------------------------------------------


def test_extrair_arquivo_multipart_e_json() -> None:
    corpo, tipo = corpo_arquivo()
    assert planilha.extrair_arquivo(corpo, tipo) == (XLSX_FAKE, "Carteira.xlsm")

    payload = json.dumps(
        {"file_base64": base64.b64encode(CSV_FAKE).decode(), "file_name": "x.csv"}
    ).encode()
    assert planilha.extrair_arquivo(payload, "application/json; charset=utf-8") == (CSV_FAKE, "x.csv")
    # Sem Content-Type, mas o corpo é JSON: aceita mesmo assim.
    assert planilha.extrair_arquivo(payload, "")[1] == "x.csv"


@pytest.mark.parametrize(
    "corpo,tipo,status",
    [
        (b"", "multipart/form-data; boundary=x", 400),
        (b"algo", "multipart/form-data", 400),  # sem boundary
        (montar_multipart([_parte("obs", b"1")]), f"multipart/form-data; boundary={BOUNDARY}", 400),
        (b"conteudo solto", "text/plain", 400),
        (b"P" * (planilha.LIMITE_BYTES * 2 + 1), "application/json", 413),
    ],
)
def test_extrair_arquivo_erros(corpo: bytes, tipo: str, status: int) -> None:
    with pytest.raises(planilha.ErroRequisicao) as exc:
        planilha.extrair_arquivo(corpo, tipo)
    assert exc.value.status == status


def test_multipart_com_arquivo_acima_do_limite_da_413() -> None:
    grande = b"PK\x03\x04" + b"0" * planilha.LIMITE_BYTES
    corpo, tipo = corpo_arquivo(conteudo=grande)
    with pytest.raises(planilha.ErroRequisicao) as exc:
        planilha.extrair_arquivo(corpo, tipo)
    assert exc.value.status == 413
    assert "MB" in exc.value.mensagem


# --------------------------------------------------------------------------
# processar: contrato (status, payload)
# --------------------------------------------------------------------------


def test_processar_sucesso_repassa_bytes_e_nome() -> None:
    registro: Dict[str, Any] = {}
    corpo, tipo = corpo_arquivo()
    status, payload = planilha.processar(corpo, tipo, motor_falso(registro))
    assert status == 200
    assert payload["file_name"] == "Carteira.xlsm"
    assert registro["dados"] == XLSX_FAKE and registro["nome"] == "Carteira.xlsm"


@pytest.mark.parametrize(
    "corpo,tipo,status",
    [
        (b"", "", 400),
        (b"nada", "text/plain", 400),
        (b"P" * (planilha.LIMITE_BYTES * 2 + 1), "application/json", 413),
    ],
)
def test_processar_erros_de_entrada(corpo: bytes, tipo: str, status: int) -> None:
    chamou: Dict[str, Any] = {}
    resposta_status, payload = planilha.processar(corpo, tipo, motor_falso(chamou))
    assert resposta_status == status
    assert isinstance(payload["error"], str) and payload["error"]
    assert not chamou  # motor nunca é chamado com entrada inválida


@pytest.mark.parametrize(
    "excecao,status",
    [
        (ValueError("aba ausente"), 400),
        (KeyError("Posicao_B3"), 400),
        (UnicodeDecodeError("utf-8", b"", 0, 1, "ruim"), 400),
        (MemoryError(), 413),
        (RuntimeError("bug do motor"), 500),
        (planilha.ErroRequisicao(413, "grande"), 413),
    ],
)
def test_processar_traduz_excecoes_do_motor(excecao: BaseException, status: int) -> None:
    def _explode(dados: bytes, nome: str) -> Dict[str, Any]:
        raise excecao

    corpo, tipo = corpo_arquivo()
    resposta_status, payload = planilha.processar(corpo, tipo, _explode)
    assert resposta_status == status
    assert "error" in payload


def test_processar_motor_com_retorno_invalido_vira_500() -> None:
    corpo, tipo = corpo_arquivo()
    status, payload = planilha.processar(corpo, tipo, lambda d, n: "isso não é um dicionário")
    assert status == 500 and payload["error"] == planilha.MSG_INESPERADO


def test_processar_nunca_grava_em_disco(monkeypatch: pytest.MonkeyPatch) -> None:
    def _proibido(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a função não pode abrir arquivos")

    monkeypatch.setattr(builtins, "open", _proibido)
    corpo, tipo = corpo_arquivo()
    status, _ = planilha.processar(corpo, tipo, motor_falso({}))
    assert status == 200


def test_processar_usa_o_motor_real_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem injeção, ``processar`` chama ``analisar_planilha`` (engine.pipeline)."""
    chamadas: List[Tuple[bytes, str]] = []
    monkeypatch.setattr(
        planilha, "analisar_planilha", lambda d, n: chamadas.append((d, n)) or {"ok": True}
    )
    corpo, tipo = corpo_arquivo()
    status, payload = planilha.processar(corpo, tipo)
    assert status == 200 and payload == {"ok": True}
    assert chamadas == [(XLSX_FAKE, "Carteira.xlsm")]


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------


def test_corpo_json_remove_nan_e_mantem_acentos() -> None:
    bruto = planilha.corpo_json({"valor": float("nan"), "inf": float("inf"), "t": "Ações"})
    assert json.loads(bruto.decode("utf-8")) == {"valor": None, "inf": None, "t": "Ações"}
    assert "Ações".encode("utf-8") in bruto


def test_corpo_json_tolera_objeto_nao_serializavel() -> None:
    class Estranho:
        def __repr__(self) -> str:
            return "<estranho>"

    dados = json.loads(planilha.corpo_json({"x": Estranho()}).decode("utf-8"))
    assert dados["x"] == "<estranho>"


# --------------------------------------------------------------------------
# Handler HTTP (sem subir servidor)
# --------------------------------------------------------------------------


class HandlerFalso(planilha.handler):  # type: ignore[misc]
    """Instancia o handler sem socket: rfile/wfile em memória."""

    def __init__(self, corpo: bytes = b"", content_type: str = "", tamanho: int = -1) -> None:
        self.rfile = io.BytesIO(corpo)
        self.wfile = io.BytesIO()
        self.headers = email.message.Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(corpo) if tamanho < 0 else tamanho)
        self.status: int = 0
        self.cabecalhos: Dict[str, str] = {}

    def send_response(self, code: int, message: str | None = None) -> None:
        self.status = code

    def send_header(self, keyword: str, value: str) -> None:
        self.cabecalhos[keyword] = str(value)

    def end_headers(self) -> None:
        return

    @property
    def json(self) -> Any:
        bruto = self.wfile.getvalue()
        return json.loads(bruto.decode("utf-8")) if bruto else None


def test_handler_options_responde_cors() -> None:
    req = HandlerFalso()
    req.do_OPTIONS()
    assert req.status == 204
    assert req.cabecalhos["Access-Control-Allow-Origin"] == "*"
    assert "POST" in req.cabecalhos["Access-Control-Allow-Methods"]


def test_handler_get_e_sonda_de_saude() -> None:
    req = HandlerFalso()
    req.do_GET()
    assert req.status == 200 and req.json["status"] == "ok"


def test_handler_post_sucesso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planilha, "analisar_planilha", lambda d, n: {"file_name": n})
    corpo, tipo = corpo_arquivo()
    req = HandlerFalso(corpo, tipo)
    req.do_POST()
    assert req.status == 200
    assert req.json == {"file_name": "Carteira.xlsm"}
    assert req.cabecalhos["Content-Type"] == "application/json; charset=utf-8"
    assert req.cabecalhos["Content-Length"] == str(len(req.wfile.getvalue()))
    assert req.cabecalhos["Access-Control-Allow-Origin"] == "*"


def test_handler_post_sem_corpo_da_400() -> None:
    req = HandlerFalso(b"", "multipart/form-data; boundary=x")
    req.do_POST()
    assert req.status == 400 and "error" in req.json


def test_handler_post_content_length_gigante_nao_le_o_corpo() -> None:
    req = HandlerFalso(b"", "multipart/form-data; boundary=x", tamanho=planilha.LIMITE_BYTES * 4)
    req.do_POST()
    assert req.status == 413
    assert req.json["error"] == planilha.MSG_GRANDE
    assert req.rfile.tell() == 0  # nem tentou ler


def test_handler_post_content_length_invalido_vira_400() -> None:
    req = HandlerFalso(b"algo", "application/json")
    req.headers.replace_header("Content-Length", "abacaxi")
    req.do_POST()
    assert req.status == 400


def test_handler_post_erro_interno_vira_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(planilha, "processar", _explode)
    corpo, tipo = corpo_arquivo()
    req = HandlerFalso(corpo, tipo)
    req.do_POST()
    assert req.status == 500 and req.json["error"] == planilha.MSG_INESPERADO
