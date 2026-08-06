"""Função serverless (runtime ``@vercel/python``) que analisa uma planilha.

Endpoint: ``POST /api/planilha``

Aceita dois formatos de corpo:

1. ``multipart/form-data`` com o campo ``file`` (o parse do boundary é feito à
   mão aqui — o módulo ``cgi`` saiu da stdlib no Python 3.13 e ``cgi.FieldStorage``
   escrevia arquivos grandes em disco, o que não queremos);
2. ``application/json`` com ``{"file_base64": "...", "file_name": "carteira.xlsm"}``.

O conteúdo enviado **nunca** toca o disco: fica em memória, vai para
``engine.pipeline.analyze_bytes`` e o resultado sai serializado por
``engine.models.to_dict``.

Erros viram ``{"error": "mensagem em pt-BR"}`` com o status adequado:
400 (arquivo inválido/ausente), 413 (maior que 6 MB) e 500 (inesperado).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote

# A função roda dentro de ``api/``; o pacote ``engine`` mora na raiz do projeto.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

LIMITE_BYTES: int = 6 * 1024 * 1024  # 6 MB
LIMITE_MB: int = LIMITE_BYTES // (1024 * 1024)

EXTENSOES_EXCEL: Tuple[str, ...] = (".xlsx", ".xlsm", ".xltx", ".xltm")
EXTENSOES_TEXTO: Tuple[str, ...] = (".csv", ".tsv", ".txt")
EXTENSOES_ACEITAS: Tuple[str, ...] = EXTENSOES_EXCEL + EXTENSOES_TEXTO

# Nomes de campo aceitos no multipart (o front usa "file").
CAMPOS_ARQUIVO: Tuple[str, ...] = ("file", "arquivo", "planilha", "upload", "files[]")

CABECALHOS_CORS: Dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
}

MSG_SEM_ARQUIVO = (
    'Nenhum arquivo recebido. Envie multipart/form-data com o campo "file" '
    'ou JSON com "file_base64".'
)
MSG_GRANDE = f"Arquivo grande demais: o limite é de {LIMITE_MB} MB."
MSG_FORMATO = "Formato não suportado. Envie uma planilha .xlsx, .xlsm ou .csv."
MSG_CORROMPIDO = "Arquivo inválido: não parece uma planilha do Excel (.xlsx/.xlsm)."
MSG_ILEGIVEL = "Não foi possível ler a planilha enviada. Verifique o arquivo e tente de novo."
MSG_INESPERADO = "Erro inesperado ao analisar a planilha."


class ErroRequisicao(Exception):
    """Erro previsto, já com status HTTP e mensagem em pt-BR para o usuário."""

    def __init__(self, status: int, mensagem: str) -> None:
        super().__init__(mensagem)
        self.status: int = status
        self.mensagem: str = mensagem


@dataclass(frozen=True)
class ParteMultipart:
    """Uma parte do corpo ``multipart/form-data``."""

    nome: str
    nome_arquivo: str
    tipo: str
    conteudo: bytes

    @property
    def e_arquivo(self) -> bool:
        return bool(self.nome_arquivo)


# --------------------------------------------------------------------------
# Cabeçalhos e parâmetros
# --------------------------------------------------------------------------


def dividir_parametros(valor: str) -> List[str]:
    """Divide ``a; b="c;d"; e`` em partes, respeitando aspas e escapes."""
    partes: List[str] = []
    atual: List[str] = []
    em_aspas = False
    escapado = False
    for char in valor or "":
        if escapado:
            atual.append(char)
            escapado = False
            continue
        if char == "\\" and em_aspas:
            escapado = True
            atual.append(char)
            continue
        if char == '"':
            em_aspas = not em_aspas
            atual.append(char)
            continue
        if char == ";" and not em_aspas:
            partes.append("".join(atual))
            atual = []
            continue
        atual.append(char)
    partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]


def parametros_cabecalho(valor: str) -> Tuple[str, Dict[str, str]]:
    """``'form-data; name="file"'`` -> ``("form-data", {"name": "file"})``."""
    partes = dividir_parametros(valor)
    if not partes:
        return "", {}
    principal = partes[0].strip().lower()
    parametros: Dict[str, str] = {}
    for parte in partes[1:]:
        chave, sep, bruto = parte.partition("=")
        if not sep:
            continue
        chave = chave.strip().lower()
        bruto = bruto.strip()
        if len(bruto) >= 2 and bruto[0] == '"' and bruto[-1] == '"':
            bruto = bruto[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if chave.endswith("*"):
            # RFC 5987: filename*=UTF-8''nome%20com%20espaco
            chave = chave[:-1]
            _, _, codificado = bruto.rpartition("'")
            bruto = unquote(codificado or bruto)
        if chave not in parametros or parametros[chave] == "":
            parametros[chave] = bruto
    return principal, parametros


def extrair_boundary(content_type: str) -> str:
    """Boundary do ``multipart/form-data`` (string vazia se não houver)."""
    principal, parametros = parametros_cabecalho(content_type or "")
    if not principal.startswith("multipart/"):
        return ""
    return parametros.get("boundary", "").strip()


def _parse_cabecalhos(bruto: bytes) -> Dict[str, str]:
    """Cabeçalhos de uma parte, em minúsculas, com desdobramento de linhas."""
    texto = bruto.decode("utf-8", "replace").replace("\r\n", "\n")
    cabecalhos: Dict[str, str] = {}
    ultima = ""
    for linha in texto.split("\n"):
        if not linha.strip():
            continue
        if linha[:1] in (" ", "\t") and ultima:
            cabecalhos[ultima] += " " + linha.strip()
            continue
        chave, sep, valor = linha.partition(":")
        if not sep:
            continue
        ultima = chave.strip().lower()
        cabecalhos[ultima] = valor.strip()
    return cabecalhos


# --------------------------------------------------------------------------
# Parse do multipart (sem cgi, sem disco)
# --------------------------------------------------------------------------


def analisar_multipart(corpo: bytes, boundary: str) -> List[ParteMultipart]:
    """Divide o corpo pelo boundary e devolve as partes já tipadas.

    Tolerante a preâmbulo, epílogo, ``\\n`` no lugar de ``\\r\\n`` e ao
    delimitador final ausente. Nunca levanta exceção: corpo estranho -> ``[]``.
    """
    if not corpo or not boundary:
        return []
    delimitador = b"--" + boundary.encode("utf-8", "replace")
    for quebra in (b"\r\n", b"\n"):
        pedacos = (quebra + corpo).split(quebra + delimitador)
        if len(pedacos) > 1:
            return _partes_de(pedacos[1:])
    return []


def _partes_de(pedacos: List[bytes]) -> List[ParteMultipart]:
    partes: List[ParteMultipart] = []
    for pedaco in pedacos:
        if pedaco.startswith(b"--"):  # delimitador de fechamento: acabou
            break
        pedaco = pedaco.lstrip(b" \t")  # padding de transporte
        if pedaco.startswith(b"\r\n"):
            pedaco = pedaco[2:]
        elif pedaco.startswith(b"\n"):
            pedaco = pedaco[1:]
        else:
            continue
        cabecalho_bruto, sep, conteudo = pedaco.partition(b"\r\n\r\n")
        if not sep:
            cabecalho_bruto, sep, conteudo = pedaco.partition(b"\n\n")
        if not sep:
            continue
        cabecalhos = _parse_cabecalhos(cabecalho_bruto)
        _, disposicao = parametros_cabecalho(cabecalhos.get("content-disposition", ""))
        partes.append(
            ParteMultipart(
                nome=disposicao.get("name", ""),
                nome_arquivo=disposicao.get("filename", ""),
                tipo=cabecalhos.get("content-type", ""),
                conteudo=conteudo,
            )
        )
    return partes


def parte_do_arquivo(partes: List[ParteMultipart]) -> Optional[ParteMultipart]:
    """Escolhe a parte que carrega a planilha (campo conhecido > 1º com nome)."""
    if not partes:
        return None
    por_nome = {p.nome.strip().lower(): p for p in reversed(partes)}
    for campo in CAMPOS_ARQUIVO:
        parte = por_nome.get(campo)
        if parte is not None and (parte.e_arquivo or parte.conteudo):
            return parte
    for parte in partes:
        if parte.e_arquivo:
            return parte
    return None


# --------------------------------------------------------------------------
# Extração do arquivo enviado
# --------------------------------------------------------------------------


def nome_seguro(nome: str) -> str:
    """Só o nome do arquivo: sem diretórios, sem caracteres de controle."""
    limpo = (nome or "").replace("\\", "/").split("/")[-1].strip().strip('"')
    limpo = "".join(ch for ch in limpo if ch.isprintable())
    return limpo[:180]


def extensao(nome: str) -> str:
    base = nome_seguro(nome).lower()
    _, ponto, ext = base.rpartition(".")
    return ("." + ext) if ponto else ""


def _nome_provavel(dados: bytes, nome: str) -> str:
    """Completa o nome quando o cliente não mandou nenhum (usa a assinatura)."""
    limpo = nome_seguro(nome)
    if extensao(limpo):
        return limpo
    base = limpo or "planilha"
    return f"{base}.xlsx" if dados[:2] == b"PK" else f"{base}.csv"


def validar_arquivo(dados: bytes, nome: str) -> Tuple[bytes, str]:
    """Valida tamanho, extensão e assinatura. Devolve ``(dados, nome)``."""
    if not dados:
        raise ErroRequisicao(400, MSG_SEM_ARQUIVO)
    if len(dados) > LIMITE_BYTES:
        raise ErroRequisicao(413, MSG_GRANDE)
    nome_final = _nome_provavel(dados, nome)
    ext = extensao(nome_final)
    if ext not in EXTENSOES_ACEITAS:
        raise ErroRequisicao(400, MSG_FORMATO)
    if ext in EXTENSOES_EXCEL and dados[:2] != b"PK":
        # .xlsx/.xlsm são pacotes ZIP; qualquer outra coisa é engano do usuário.
        raise ErroRequisicao(400, MSG_CORROMPIDO)
    return dados, nome_final


def arquivo_do_multipart(corpo: bytes, content_type: str) -> Tuple[bytes, str]:
    boundary = extrair_boundary(content_type)
    if not boundary:
        raise ErroRequisicao(400, "Requisição multipart sem boundary no Content-Type.")
    parte = parte_do_arquivo(analisar_multipart(corpo, boundary))
    if parte is None or not parte.conteudo:
        raise ErroRequisicao(400, MSG_SEM_ARQUIVO)
    return parte.conteudo, parte.nome_arquivo


def arquivo_do_json(corpo: bytes) -> Tuple[bytes, str]:
    try:
        payload = json.loads(corpo.decode("utf-8", "replace") or "{}")
    except (ValueError, AttributeError):
        raise ErroRequisicao(400, "JSON inválido no corpo da requisição.") from None
    if not isinstance(payload, dict):
        raise ErroRequisicao(400, "JSON inválido: esperava um objeto.")
    bruto = payload.get("file_base64") or payload.get("fileBase64") or ""
    nome = payload.get("file_name") or payload.get("fileName") or ""
    if not isinstance(bruto, str) or not bruto.strip():
        raise ErroRequisicao(400, MSG_SEM_ARQUIVO)
    if "base64," in bruto[:200]:  # data URI: "data:...;base64,AAAA"
        bruto = bruto.split("base64,", 1)[1]
    compacto = "".join(bruto.split())
    if len(compacto) > (LIMITE_BYTES // 3 + 1) * 4 + 16:
        raise ErroRequisicao(413, MSG_GRANDE)
    try:
        dados = base64.b64decode(compacto, validate=True)
    except (binascii.Error, ValueError):
        raise ErroRequisicao(400, "Conteúdo base64 inválido.") from None
    return dados, nome if isinstance(nome, str) else ""


def extrair_arquivo(corpo: bytes, content_type: str) -> Tuple[bytes, str]:
    """Descobre o formato do corpo e devolve ``(conteúdo, nome)`` validados."""
    if not corpo:
        raise ErroRequisicao(400, MSG_SEM_ARQUIVO)
    if len(corpo) > LIMITE_BYTES * 2:  # margem para o overhead do multipart/base64
        raise ErroRequisicao(413, MSG_GRANDE)
    tipo = (content_type or "").strip().lower()
    if tipo.startswith("multipart/"):
        dados, nome = arquivo_do_multipart(corpo, content_type)
    elif tipo.startswith("application/json") or corpo.lstrip()[:1] == b"{":
        dados, nome = arquivo_do_json(corpo)
    else:
        raise ErroRequisicao(
            400,
            'Content-Type não suportado. Use multipart/form-data (campo "file") '
            'ou application/json com "file_base64".',
        )
    return validar_arquivo(dados, nome)


# --------------------------------------------------------------------------
# Ponte com o motor
# --------------------------------------------------------------------------

# Erros de leitura previsíveis (planilha corrompida, aba faltando, encoding).
_ERROS_DE_LEITURA: Tuple[type, ...] = (
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    UnicodeDecodeError,
    zipfile.BadZipFile,
)


def _e_erro_de_leitura(exc: BaseException) -> bool:
    if isinstance(exc, _ERROS_DE_LEITURA):
        return True
    # openpyxl.utils.exceptions.InvalidFileException, sem importar openpyxl aqui.
    return type(exc).__name__ in ("InvalidFileException", "ReadOnlyWorkbookException")


def analisar_planilha(dados: bytes, nome: str) -> Dict[str, Any]:
    """Chama o motor e devolve o dicionário JSON da análise."""
    from engine.models import to_dict  # import tardio: mantém o cold start baixo
    from engine.pipeline import analyze_bytes

    return to_dict(analyze_bytes(dados, nome))


def processar(
    corpo: bytes,
    content_type: str,
    analisar: Optional[Callable[[bytes, str], Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Núcleo testável: corpo cru -> ``(status, payload)``. Não usa disco nem rede."""
    executar = analisar or analisar_planilha
    try:
        dados, nome = extrair_arquivo(corpo, content_type)
    except ErroRequisicao as erro:
        return erro.status, {"error": erro.mensagem}
    except Exception:  # noqa: BLE001 - nada pode escapar da função
        return 400, {"error": MSG_ILEGIVEL}

    try:
        resultado = executar(dados, nome)
    except ErroRequisicao as erro:
        return erro.status, {"error": erro.mensagem}
    except MemoryError:
        return 413, {"error": MSG_GRANDE}
    except Exception as exc:  # noqa: BLE001
        if _e_erro_de_leitura(exc):
            return 400, {"error": MSG_ILEGIVEL}
        return 500, {"error": MSG_INESPERADO}

    if not isinstance(resultado, dict):
        return 500, {"error": MSG_INESPERADO}
    return 200, resultado


# --------------------------------------------------------------------------
# Serialização da resposta
# --------------------------------------------------------------------------


def _sanear(valor: Any) -> Any:
    """Remove NaN/Inf e tipos exóticos que quebrariam ``JSON.parse`` no browser."""
    if isinstance(valor, float):
        return valor if valor == valor and valor not in (float("inf"), float("-inf")) else None
    if isinstance(valor, dict):
        return {str(k): _sanear(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sanear(v) for v in valor]
    if valor is None or isinstance(valor, (str, int, bool)):
        return valor
    return str(valor)


def corpo_json(payload: Any) -> bytes:
    """Serializa em UTF-8, sem NaN/Inf. Falha de serialização vira erro 500 legível."""
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        try:
            return json.dumps(_sanear(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            return json.dumps({"error": MSG_INESPERADO}, ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------
# Handler HTTP (padrão @vercel/python)
# --------------------------------------------------------------------------


class handler(BaseHTTPRequestHandler):  # noqa: N801 - nome exigido pelo runtime
    """Recebe a planilha, devolve a análise em JSON. Nada é gravado em disco."""

    server_version = "celestia-engine/1.0"
    protocol_version = "HTTP/1.1"

    # -- utilidades ------------------------------------------------------
    def _responder(self, status: int, payload: Any) -> None:
        corpo = corpo_json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for chave, valor in CABECALHOS_CORS.items():
            self.send_header(chave, valor)
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError):  # cliente desistiu
            pass

    def _cabecalho(self, nome: str, padrao: str = "") -> str:
        cabecalhos = getattr(self, "headers", None)
        if cabecalhos is None:
            return padrao
        valor = cabecalhos.get(nome, padrao)
        return valor if isinstance(valor, str) else padrao

    def _tamanho_declarado(self) -> int:
        try:
            return max(0, int(self._cabecalho("Content-Length", "0") or 0))
        except (TypeError, ValueError):
            return 0

    def _ler_corpo(self) -> bytes:
        tamanho = self._tamanho_declarado()
        if tamanho <= 0:
            return b""
        return self.rfile.read(min(tamanho, LIMITE_BYTES * 2 + 4096)) or b""

    # -- métodos HTTP ----------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802 - assinatura do BaseHTTPRequestHandler
        """Pré-voo CORS."""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for chave, valor in CABECALHOS_CORS.items():
            self.send_header(chave, valor)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Sonda de saúde — útil para conferir se a função subiu."""
        self._responder(200, {"status": "ok", "limite_mb": LIMITE_MB, "metodo": "POST"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self._tamanho_declarado() > LIMITE_BYTES * 2:
                self._responder(413, {"error": MSG_GRANDE})
                return
            corpo = self._ler_corpo()
            status, payload = processar(corpo, self._cabecalho("Content-Type"))
            self._responder(status, payload)
        except Exception:  # noqa: BLE001 - a função nunca pode estourar
            self._responder(500, {"error": MSG_INESPERADO})

    def log_message(self, formato: str, *args: Any) -> None:  # noqa: A003
        """Silencia o log padrão (o runtime da Vercel já registra as chamadas)."""
        return
