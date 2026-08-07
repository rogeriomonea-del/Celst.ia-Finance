"""Rotas HTTP de autenticação, no mesmo formato do resto do motor.

    tratar(metodo, caminho, params, corpo, cabecalhos)
        -> (status, payload, content_type, cabecalhos_extra)

Rotas (prefixo ``/auth``):

| Método | Caminho | O que faz |
|---|---|---|
| POST | `/auth/codigo` | Pede um código de acesso por e-mail |
| POST | `/auth/verificar` | Confere o código e abre a sessão |
| GET  | `/auth/google` | Redireciona para o Google |
| GET  | `/auth/google/retorno` | Retorno do Google; abre a sessão |
| GET  | `/auth/eu` | Quem está logado (ou 401) |
| POST | `/auth/sair` | Encerra a sessão atual |
| POST | `/auth/sair-de-tudo` | Encerra em todos os dispositivos |
| GET  | `/auth/config` | O que está habilitado (para a tela de login) |

O cookie de sessão é ``HttpOnly`` (JavaScript não lê), ``SameSite=Lax`` (não
viaja em requisição de outro site) e ``Secure`` fora do localhost. O ``Domain``
sai de ``CELESTIA_COOKIE_DOMINIO`` — em produção, ``.celestiaflights.com``, o
que faz uma única sessão valer para as duas plataformas.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from . import contas, correio, google
from .store import conexao, limpar_expirados, registra_evento

NOME_COOKIE = "celestia_sessao"

Resposta = Tuple[int, Any, str, Dict[str, str]]


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------


def _dominio_cookie() -> Optional[str]:
    dominio = os.environ.get("CELESTIA_COOKIE_DOMINIO", "").strip()
    return dominio or None


def _seguro() -> bool:
    """HTTPS obrigatório, exceto em desenvolvimento local."""
    base = os.environ.get("CELESTIA_URL_BASE", "")
    if base.startswith("http://localhost") or base.startswith("http://127."):
        return False
    return os.environ.get("CELESTIA_COOKIE_INSEGURO") != "1"


def _monta_cookie(token: str, max_age: int) -> str:
    partes = [
        f"{NOME_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if _seguro():
        partes.append("Secure")
    dominio = _dominio_cookie()
    if dominio:
        partes.append(f"Domain={dominio}")
    return "; ".join(partes)


def cookie_de_saida() -> str:
    return _monta_cookie("", 0)


def token_do_pedido(cabecalhos: Dict[str, str]) -> Optional[str]:
    """Lê o token do cookie ou do cabeçalho ``Authorization: Bearer``."""
    autorizacao = cabecalhos.get("authorization") or cabecalhos.get("Authorization") or ""
    if autorizacao.lower().startswith("bearer "):
        return autorizacao[7:].strip() or None
    bruto = cabecalhos.get("cookie") or cabecalhos.get("Cookie") or ""
    for pedaco in bruto.split(";"):
        nome, _, valor = pedaco.strip().partition("=")
        if nome == NOME_COOKIE:
            return urllib.parse.unquote(valor) or None
    return None


def usuario_do_pedido(cabecalhos: Dict[str, str]) -> Optional[contas.Usuario]:
    """Usuário autenticado do pedido, ou ``None``. Usado pelo módulo de extratos."""
    token = token_do_pedido(cabecalhos)
    if not token:
        return None
    with conexao() as db:
        return contas.validar_sessao(db, token)


# --------------------------------------------------------------------------
# Utilitários
# --------------------------------------------------------------------------


def _json_do_corpo(corpo: bytes) -> Dict[str, Any]:
    if not corpo:
        return {}
    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _ip(cabecalhos: Dict[str, str]) -> Optional[str]:
    encaminhado = cabecalhos.get("x-forwarded-for") or cabecalhos.get("X-Forwarded-For")
    if encaminhado:
        return encaminhado.split(",")[0].strip()[:45]
    return (cabecalhos.get("x-real-ip") or cabecalhos.get("X-Real-IP") or "").strip()[:45] or None


def _erro(mensagem: str, status: int = 400) -> Resposta:
    return status, {"erro": mensagem}, "application/json", {}


# --------------------------------------------------------------------------
# Roteador
# --------------------------------------------------------------------------


def tratar(
    metodo: str,
    caminho: str,
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    rota = "/" + caminho.strip("/")
    if rota.startswith("/auth"):
        rota = rota[5:] or "/"
    metodo = metodo.upper()

    try:
        if metodo == "OPTIONS":
            return 204, b"", "text/plain", {}
        if rota == "/config" and metodo == "GET":
            return _config()
        if rota == "/codigo" and metodo == "POST":
            return _pedir_codigo(corpo, cabecalhos)
        if rota == "/verificar" and metodo == "POST":
            return _verificar(corpo, cabecalhos)
        if rota == "/google" and metodo == "GET":
            return _google_iniciar(params)
        if rota == "/google/retorno" and metodo == "GET":
            return _google_retorno(params, cabecalhos)
        if rota == "/eu" and metodo == "GET":
            return _eu(cabecalhos)
        if rota == "/sair" and metodo == "POST":
            return _sair(cabecalhos)
        if rota == "/sair-de-tudo" and metodo == "POST":
            return _sair_de_tudo(cabecalhos)
        return _erro("Rota de autenticação não encontrada.", 404)
    except contas.ErroAuth as erro:
        return _erro(erro.mensagem, erro.status)
    except Exception:  # noqa: BLE001 - nunca vazar stack trace para o cliente
        return _erro("Erro inesperado na autenticação.", 500)


def _config() -> Resposta:
    return (
        200,
        {
            "codigo_email": True,
            "google": google.configurado(),
            "envio_real": correio.configurado(),
            "url_base": google.url_base(),
        },
        "application/json",
        {},
    )


def _pedir_codigo(corpo: bytes, cabecalhos: Dict[str, str]) -> Resposta:
    dados = _json_do_corpo(corpo)
    email = str(dados.get("email", ""))
    ip = _ip(cabecalhos)

    with conexao() as db:
        limpar_expirados(db)
        codigo = contas.gerar_codigo(db, email, ip=ip)

    enviado, modo = correio.enviar_codigo(email, codigo)
    if not enviado:
        return _erro(
            "Não foi possível enviar o e-mail agora. Confira a configuração de SMTP "
            "ou tente novamente em instantes.",
            502,
        )

    resposta: Dict[str, Any] = {
        "enviado": True,
        "modo": modo,
        "mensagem": (
            "Código enviado. Confira sua caixa de entrada."
            if modo == "email"
            else "SMTP não configurado: o código foi escrito no log do servidor."
        ),
    }
    return 200, resposta, "application/json", {}


def _abrir_sessao(db, usuario: contas.Usuario, cabecalhos: Dict[str, str]) -> Tuple[str, int]:
    token, _ = contas.criar_sessao(
        db,
        usuario.id,
        user_agent=cabecalhos.get("user-agent") or cabecalhos.get("User-Agent"),
        ip=_ip(cabecalhos),
    )
    return token, int(contas.VALIDADE_SESSAO.total_seconds())


def _verificar(corpo: bytes, cabecalhos: Dict[str, str]) -> Resposta:
    dados = _json_do_corpo(corpo)
    with conexao() as db:
        usuario = contas.verificar_codigo(
            db, str(dados.get("email", "")), str(dados.get("codigo", "")), ip=_ip(cabecalhos)
        )
        token, max_age = _abrir_sessao(db, usuario, cabecalhos)
    return (
        200,
        {"usuario": usuario.to_dict()},
        "application/json",
        {"Set-Cookie": _monta_cookie(token, max_age)},
    )


def _google_iniciar(params: Dict[str, Any]) -> Resposta:
    destino = str(params.get("destino") or params.get("redirecionar_para") or "/")
    # Só caminho interno: sem isso o parâmetro viraria redirecionamento aberto.
    if not destino.startswith("/") or destino.startswith("//"):
        destino = "/"
    with conexao() as db:
        limpar_expirados(db)
        url = google.iniciar(db, destino)
    return 302, b"", "text/plain", {"Location": url}


def _google_retorno(params: Dict[str, Any], cabecalhos: Dict[str, str]) -> Resposta:
    if params.get("error"):
        return 302, b"", "text/plain", {"Location": "/entrar?erro=google_cancelado"}

    with conexao() as db:
        perfil, destino = google.concluir(
            db, str(params.get("code", "")), str(params.get("state", ""))
        )
        usuario = contas.obter_ou_criar_usuario(
            db, perfil.email, nome=perfil.nome, avatar_url=perfil.avatar_url
        )
        contas.vincular_identidade(db, usuario.id, "google", perfil.sub, perfil.email)
        token, max_age = _abrir_sessao(db, usuario, cabecalhos)
        with db:
            registra_evento(db, "login_google", email=perfil.email, usuario_id=usuario.id,
                            ip=_ip(cabecalhos))

    return (
        302,
        b"",
        "text/plain",
        {"Location": destino or "/", "Set-Cookie": _monta_cookie(token, max_age)},
    )


def _eu(cabecalhos: Dict[str, str]) -> Resposta:
    usuario = usuario_do_pedido(cabecalhos)
    if usuario is None:
        return _erro("Não autenticado.", 401)
    return 200, {"usuario": usuario.to_dict()}, "application/json", {}


def _sair(cabecalhos: Dict[str, str]) -> Resposta:
    token = token_do_pedido(cabecalhos)
    with conexao() as db:
        contas.encerrar_sessao(db, token)
    return 200, {"ok": True}, "application/json", {"Set-Cookie": cookie_de_saida()}


def _sair_de_tudo(cabecalhos: Dict[str, str]) -> Resposta:
    usuario = usuario_do_pedido(cabecalhos)
    if usuario is None:
        return _erro("Não autenticado.", 401)
    with conexao() as db:
        quantas = contas.encerrar_todas_sessoes(db, usuario.id)
    return 200, {"ok": True, "sessoes_encerradas": quantas}, "application/json", {
        "Set-Cookie": cookie_de_saida()
    }
