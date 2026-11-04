"""Função serverless (runtime ``@vercel/python``) da API de autenticação.

Endpoint: ``/api/auth*`` — handler fino que delega TODO o roteamento para
``engine.auth.api.tratar`` (o mesmo contrato usado pelo servidor de
desenvolvimento ``engine/server.py``). Aqui só mora o transporte HTTP:
ler o corpo com limite, montar params/cabeçalhos, escrever a resposta e o CORS.

Honestidade operacional: o filesystem da Vercel é efêmero, e o banco de
contas/sessões (``auth.db``) mora nele — sem storage durável as sessões somem a
cada execução. O deploy de verdade é o self-host (``docs/deploy-hostinger.md``).
Nenhum log é emitido com e-mail, código ou token.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict
from urllib.parse import parse_qs, urlsplit

# A função roda dentro de ``api/``; o pacote ``engine`` mora na raiz do projeto.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from engine.auth import api as motor  # noqa: E402 - depende do sys.path acima
from engine.extratos import api as extratos_api  # noqa: E402

TIPO_JSON = extratos_api.TIPO_JSON
LIMITE_CORPO_AUTH = 64 * 1024

# Sem ``Allow-Origin: *``: o cookie de sessão só viaja para a própria origem
# (o rewrite do Next deixa a chamada same-origin), então CORS nem entra.
CABECALHOS_CORS: Dict[str, str] = {
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
}


class handler(BaseHTTPRequestHandler):  # noqa: N801 - nome exigido pelo runtime
    """Transporte HTTP fino sobre ``engine.extratos.api.tratar``."""

    server_version = "celestia-auth/1.0"
    protocol_version = "HTTP/1.1"

    # -- utilidades ------------------------------------------------------
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
        return self.rfile.read(min(tamanho, LIMITE_CORPO_AUTH)) or b""

    def _escrever(self, resposta: Any) -> None:
        status, payload, tipo = resposta[0], resposta[1], resposta[2]
        extras: Dict[str, str] = resposta[3] if len(resposta) > 3 else {}
        corpo = (
            bytes(payload)
            if isinstance(payload, (bytes, bytearray))
            else extratos_api.corpo_json(payload)
        )
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for chave, valor in extras.items():
            self.send_header(chave, valor)
        for chave, valor in CABECALHOS_CORS.items():
            self.send_header(chave, valor)
        self.end_headers()
        try:
            self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError):  # cliente desistiu
            pass

    def _atender(self) -> None:
        try:
            partes = urlsplit(self.path or "/")
            params = {
                chave: valores[0] if len(valores) == 1 else valores
                for chave, valores in parse_qs(partes.query or "").items()
            }
            corpo = (
                self._ler_corpo()
                if self.command in ("POST", "PATCH", "PUT", "DELETE")
                else b""
            )
            resposta = motor.tratar(
                self.command, partes.path, params, corpo, dict(self.headers)
            )
        except Exception:  # noqa: BLE001 - a função nunca pode estourar
            resposta = (
                500,
                {"erro": "erro inesperado na autenticação"},
                TIPO_JSON,
                {},
            )
        self._escrever(resposta)

    # -- métodos HTTP ----------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802 - assinatura do BaseHTTPRequestHandler
        """Pré-voo CORS."""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for chave, valor in CABECALHOS_CORS.items():
            self.send_header(chave, valor)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._atender()

    def do_POST(self) -> None:  # noqa: N802
        self._atender()

    def do_PATCH(self) -> None:  # noqa: N802
        self._atender()

    def do_DELETE(self) -> None:  # noqa: N802
        self._atender()

    def log_message(self, formato: str, *args: Any) -> None:  # noqa: A003
        """Silencioso: o runtime já registra as chamadas — e nunca logamos
        descrição de transação nem corpo de upload."""
        return
