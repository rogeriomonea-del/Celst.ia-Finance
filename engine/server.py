"""Back-end HTTP de desenvolvimento local.

.. code-block:: console

    $ python3 -m engine.server                 # http://127.0.0.1:8787
    $ python3 -m engine.server --port 8000
    $ NEXT_PUBLIC_ENGINE_URL=http://127.0.0.1:8787 npm run dev

Em produção quem atende a aba "Importe suas Planilhas" é a função serverless
``api/planilha.py``. Em desenvolvimento o ``next dev`` não executa funções
Python, então o ``next.config.mjs`` reescreve ``/api/py/:path*`` para
``NEXT_PUBLIC_ENGINE_URL`` — e é este servidor que responde do outro lado.

Rotas:

``POST /planilha``
    Mesmo contrato da função serverless: ``multipart/form-data`` com o campo
    ``file`` ou ``application/json`` com ``file_base64``. A resposta é a
    análise completa (``engine.models.to_dict``) ou ``{"error": "..."}`` em
    pt-BR, com 400/413/500. Também atende ``/api/planilha`` e ``/api/py/planilha``,
    para o caso de a variável de ambiente apontar para a raiz do servidor.

``GET /health``
    Sonda de saúde: versão do motor, limite de upload e rotas disponíveis.

O parse do corpo (multipart, base64, validação de tamanho e assinatura) e o
roteamento de erros são **reaproveitados de** ``api/planilha.py``: um contrato
só, testado uma vez. Como ``api/`` não é um pacote Python (o runtime da Vercel
carrega o arquivo direto), ele é carregado pelo caminho, do mesmo jeito que os
testes fazem.

O servidor é de desenvolvimento: escuta em ``127.0.0.1`` por padrão e libera
CORS para as portas de localhost.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

#: Porta padrão do servidor local.
PORTA_PADRAO: int = 8787
#: Endereço padrão: só a máquina local — isto não é um servidor de produção.
HOST_PADRAO: str = "127.0.0.1"

#: Caminhos aceitos para a análise (o rewrite do Next pode manter o prefixo).
ROTAS_PLANILHA: Tuple[str, ...] = ("/planilha", "/api/planilha", "/api/py/planilha")
#: Caminhos da sonda de saúde.
ROTAS_SAUDE: Tuple[str, ...] = ("/health", "/api/health", "/api/py/health", "/")

#: Prefixos delegados por inteiro para ``engine.extratos.api.tratar``.
PREFIXOS_EXTRATOS: Tuple[str, ...] = ("/extratos", "/api/extratos", "/api/py/extratos")

#: Hosts cuja origem é liberada no CORS quando o navegador manda ``Origin``.
HOSTS_LOCAIS: Tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Reaproveitamento de api/planilha.py
# --------------------------------------------------------------------------


def carregar_api(raiz: str = _RAIZ) -> ModuleType:
    """Carrega ``api/planilha.py`` pelo caminho e devolve o módulo.

    ``api/`` não tem ``__init__.py`` de propósito (o runtime da Vercel carrega
    o arquivo solto), então ``import api.planilha`` dependeria do diretório de
    trabalho. Carregar pelo caminho absoluto funciona de qualquer lugar.
    """
    caminho = os.path.join(raiz, "api", "planilha.py")
    spec = importlib.util.spec_from_file_location("api_planilha", caminho)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    # ``@dataclass`` exige o módulo registrado antes de executar o código.
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _cabecalhos_cors(origem: str) -> Dict[str, str]:
    """CORS liberado para localhost — e só para ele.

    O ``next dev`` roda em ``http://localhost:3000`` e o motor em outra porta,
    então o navegador exige CORS. Ecoamos a origem quando ela é local (assim
    vale para qualquer porta do dev server) e **omitimos** o cabeçalho quando
    não é: qualquer site aberto no navegador enxerga ``127.0.0.1``, e um ``*``
    deixaria essa página ler a análise devolvida por este servidor.

    Sem ``Origin`` (curl, testes, o próprio Next chamando pelo servidor) a
    resposta sai com ``*`` — aí não há navegador para proteger.
    """
    cabecalhos = {
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "86400",
        # Um cache intermediário não pode servir a resposta de uma origem a outra.
        "Vary": "Origin",
    }
    limpa = (origem or "").strip()
    if not limpa:
        cabecalhos["Access-Control-Allow-Origin"] = "*"
    elif _e_local(limpa):
        cabecalhos["Access-Control-Allow-Origin"] = limpa
        cabecalhos["Access-Control-Allow-Credentials"] = "true"
    return cabecalhos


def _e_local(origem: str) -> bool:
    """A origem é uma porta de localhost?"""
    try:
        partes = urlsplit(origem)
    except ValueError:
        return False
    if partes.scheme not in ("http", "https"):
        return False
    return (partes.hostname or "") in HOSTS_LOCAIS


def _rota(caminho: str) -> str:
    """Caminho da requisição sem query string e sem barra final."""
    try:
        limpo = urlsplit(caminho or "/").path or "/"
    except ValueError:
        limpo = "/"
    if len(limpo) > 1 and limpo.endswith("/"):
        limpo = limpo.rstrip("/") or "/"
    return limpo


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------


def criar_handler(api: ModuleType) -> type:
    """Monta a classe do handler amarrada ao módulo da API já carregado."""

    class HandlerMotor(BaseHTTPRequestHandler):
        """Servidor de desenvolvimento do motor. Nada é gravado em disco."""

        server_version = "celestia-engine-dev/1.0"
        protocol_version = "HTTP/1.1"

        # -- utilidades --------------------------------------------------
        def _responder(self, status: int, payload: Any) -> None:
            corpo = api.corpo_json(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            for chave, valor in _cabecalhos_cors(self._cabecalho("Origin")).items():
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
            return self.rfile.read(min(tamanho, api.LIMITE_BYTES * 2 + 4096)) or b""

        # -- extratos ----------------------------------------------------
        def _e_extratos(self) -> bool:
            """A rota pertence ao módulo de extratos?"""
            rota = _rota(self.path)
            return any(
                rota == prefixo or rota.startswith(prefixo + "/")
                for prefixo in PREFIXOS_EXTRATOS
            )

        def _tratar_extratos(self) -> None:
            """Delega todo ``/extratos*`` para ``engine.extratos.api.tratar``."""
            from .extratos import api as extratos_api

            try:
                partes = urlsplit(self.path or "/")
                params = parse_qs(partes.query or "")
                corpo = b""
                if self.command in ("POST", "PATCH", "PUT", "DELETE"):
                    tamanho = self._tamanho_declarado()
                    if tamanho > 0:
                        corpo = (
                            self.rfile.read(
                                min(tamanho, extratos_api.limite_corpo_bytes())
                            )
                            or b""
                        )
                resposta = extratos_api.tratar(
                    self.command, partes.path, params, corpo, dict(self.headers)
                )
            except Exception:  # noqa: BLE001 - o servidor nunca pode cair
                self._responder(500, {"erro": "erro inesperado na API de extratos"})
                return
            status, payload, tipo = resposta[0], resposta[1], resposta[2]
            extras: Dict[str, str] = resposta[3] if len(resposta) > 3 else {}
            corpo_resposta = (
                bytes(payload)
                if isinstance(payload, (bytes, bytearray))
                else extratos_api.corpo_json(payload)
            )
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo_resposta)))
            self.send_header("Cache-Control", "no-store")
            for chave, valor in extras.items():
                self.send_header(chave, valor)
            for chave, valor in _cabecalhos_cors(self._cabecalho("Origin")).items():
                self.send_header(chave, valor)
            self.end_headers()
            try:
                self.wfile.write(corpo_resposta)
            except (BrokenPipeError, ConnectionResetError):  # cliente desistiu
                pass

        # -- métodos HTTP ------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802 - assinatura do BaseHTTPRequestHandler
            """Pré-voo CORS."""
            self.send_response(204)
            self.send_header("Content-Length", "0")
            for chave, valor in _cabecalhos_cors(self._cabecalho("Origin")).items():
                self.send_header(chave, valor)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self._e_extratos():
                self._tratar_extratos()
                return
            rota = _rota(self.path)
            if rota in ROTAS_SAUDE:
                from .pipeline import VERSAO

                self._responder(
                    200,
                    {
                        "status": "ok",
                        "engine": VERSAO,
                        "limite_mb": api.LIMITE_MB,
                        "rotas": {"analise": list(ROTAS_PLANILHA), "saude": list(ROTAS_SAUDE)},
                    },
                )
                return
            if rota in ROTAS_PLANILHA:
                self._responder(
                    405,
                    {"error": "Use POST para enviar a planilha (GET /health mostra o estado)."},
                )
                return
            self._responder(404, {"error": f"Rota desconhecida: {rota}"})

        def do_POST(self) -> None:  # noqa: N802
            if self._e_extratos():
                self._tratar_extratos()
                return
            rota = _rota(self.path)
            if rota not in ROTAS_PLANILHA:
                self._responder(404, {"error": f"Rota desconhecida: {rota}"})
                return
            try:
                if self._tamanho_declarado() > api.LIMITE_BYTES * 2:
                    self._responder(413, {"error": api.MSG_GRANDE})
                    return
                corpo = self._ler_corpo()
                with warnings.catch_warnings():
                    # openpyxl avisa sobre extensões que não entende; é ruído.
                    warnings.simplefilter("ignore", UserWarning)
                    status, payload = api.processar(corpo, self._cabecalho("Content-Type"))
                self._responder(status, payload)
            except Exception:  # noqa: BLE001 - o servidor nunca pode cair
                self._responder(500, {"error": api.MSG_INESPERADO})

        def do_PATCH(self) -> None:  # noqa: N802
            if self._e_extratos():
                self._tratar_extratos()
                return
            self._responder(404, {"error": f"Rota desconhecida: {_rota(self.path)}"})

        def do_DELETE(self) -> None:  # noqa: N802
            if self._e_extratos():
                self._tratar_extratos()
                return
            self._responder(404, {"error": f"Rota desconhecida: {_rota(self.path)}"})

        def log_message(self, formato: str, *args: Any) -> None:  # noqa: A003
            """Log enxuto de uma linha por requisição (é um servidor de dev)."""
            sys.stderr.write(f"  {self.address_string()} {formato % args}\n")

    return HandlerMotor


# --------------------------------------------------------------------------
# Entrada de linha de comando
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine.server",
        description="Servidor HTTP local do motor (back-end de desenvolvimento).",
    )
    parser.add_argument(
        "--port", type=int, default=PORTA_PADRAO, help=f"porta TCP (padrão: {PORTA_PADRAO})"
    )
    parser.add_argument(
        "--host", default=HOST_PADRAO, help=f"endereço de escuta (padrão: {HOST_PADRAO})"
    )
    return parser


def criar_servidor(host: str = HOST_PADRAO, port: int = PORTA_PADRAO) -> ThreadingHTTPServer:
    """Cria o servidor já configurado (sem iniciar o laço de atendimento)."""
    servidor = ThreadingHTTPServer((host, port), criar_handler(carregar_api()))
    servidor.daemon_threads = True
    return servidor


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Sobe o servidor até ``Ctrl+C``. Devolve o código de saída do processo."""
    argumentos = _parser().parse_args(list(argv) if argv is not None else None)

    try:
        servidor = criar_servidor(argumentos.host, argumentos.port)
    except OSError as erro:
        print(
            f"Não foi possível abrir {argumentos.host}:{argumentos.port} ({erro}). "
            "A porta pode estar ocupada — tente --port com outro número.",
            file=sys.stderr,
        )
        return 1
    except Exception as erro:  # noqa: BLE001 - falha de carga da API
        print(f"Não foi possível iniciar o servidor: {erro}", file=sys.stderr)
        return 1

    host, porta = servidor.server_address[:2]
    base = f"http://{host}:{porta}"
    print(f"Motor Celestia ouvindo em {base}")
    print(f"  POST {base}/planilha   (multipart 'file' ou JSON 'file_base64')")
    print(f"  *    {base}/extratos/* (API de extratos bancários e cartões)")
    print(f"  GET  {base}/health")
    print(f"  No Next.js: NEXT_PUBLIC_ENGINE_URL={base} npm run dev")
    print("  Ctrl+C para encerrar.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - ponto de entrada
    raise SystemExit(main())
