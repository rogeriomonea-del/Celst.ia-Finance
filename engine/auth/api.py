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
| GET  | `/auth/admin/usuarios` | (admin) Contas, com sessões ativas e pendências |
| PATCH| `/auth/admin/usuarios/<id>` | (admin) `{"ativo": bool, "admin": bool}` — aprova, desativa, promove |
| GET  | `/auth/admin/eventos?limite=&email=` | (admin) Eventos de acesso recentes |
| GET  | `/auth/admin/sessoes?usuario_id=` | (admin) Sessões ativas |
| DELETE | `/auth/admin/sessoes/<id>` | (admin) Encerra uma sessão |
| POST | `/auth/admin/usuarios/<id>/encerrar-sessoes` | (admin) Derruba a conta de todos os dispositivos |

O ``destino`` do login com Google pode ser um caminho interno (``/extratos``)
ou uma URL absoluta de um host permitido — qualquer host que termine com
``CELESTIA_COOKIE_DOMINIO`` (ex.: ``https://celestiaflights.com/``), o host de
``CELESTIA_URL_BASE`` ou localhost em desenvolvimento. É isso que deixa a
plataforma de voos iniciar o login e voltar para ela mesma, com o cookie
válido nos dois domínios.

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


def _inteiro(valor: Any) -> Optional[int]:
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _parametro(params: Dict[str, Any], nome: str, padrao: str = "") -> str:
    valor = params.get(nome, padrao)
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else padrao
    return str(valor if valor is not None else padrao)


def hosts_permitidos() -> Tuple[str, ...]:
    """Sufixos de host para onde o login pode redirecionar depois de concluir."""
    sufixos = []
    dominio = _dominio_cookie()
    if dominio:
        sufixos.append(dominio.lstrip(".").lower())
    base = os.environ.get("CELESTIA_URL_BASE", "")
    host_base = (urllib.parse.urlsplit(base).hostname or "").lower()
    if host_base:
        sufixos.append(host_base)
    extras = os.environ.get("CELESTIA_HOSTS_PERMITIDOS", "")
    sufixos.extend(h.strip().lstrip(".").lower() for h in extras.split(",") if h.strip())
    return tuple(dict.fromkeys(sufixos))


def destino_seguro(destino: Any) -> str:
    """Normaliza o destino pós-login; qualquer coisa suspeita vira ``/``.

    Aceita caminho interno (começa com ``/`` e não com ``//``) ou URL absoluta
    ``https://`` (``http://`` só para localhost) cujo host é um dos permitidos
    ou subdomínio dele. Sem isso o parâmetro seria um redirecionamento aberto.
    """
    texto = str(destino or "").strip()
    if not texto:
        return "/"
    if texto.startswith("/") and not texto.startswith("//") and not texto.startswith("/\\"):
        return texto
    try:
        partes = urllib.parse.urlsplit(texto)
    except ValueError:
        return "/"
    host = (partes.hostname or "").lower()
    if not host or partes.scheme not in ("http", "https"):
        return "/"
    local = host in ("localhost", "127.0.0.1", "::1")
    if partes.scheme == "http" and not local:
        return "/"
    if local:
        return texto
    for sufixo in hosts_permitidos():
        if host == sufixo or host.endswith("." + sufixo):
            return texto
    return "/"


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
    rota = "/" + str(caminho or "").strip("/")
    # O rewrite do Next e o nginx podem manter o prefixo /api/py ou /api.
    for prefixo in ("/api/py", "/api"):
        if rota.startswith(prefixo + "/auth"):
            rota = rota[len(prefixo):]
            break
    if rota == "/auth" or rota.startswith("/auth/"):
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
        if rota == "/admin" or rota.startswith("/admin/"):
            return _admin(metodo, rota[6:] or "/", params, corpo, cabecalhos)
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
    destino = destino_seguro(
        _parametro(params, "destino") or _parametro(params, "redirecionar_para") or "/"
    )
    with conexao() as db:
        limpar_expirados(db)
        url = google.iniciar(db, destino)
    return 302, b"", "text/plain", {"Location": url}


def _google_retorno(params: Dict[str, Any], cabecalhos: Dict[str, str]) -> Resposta:
    if _parametro(params, "error"):
        return 302, b"", "text/plain", {"Location": "/entrar?erro=google_cancelado"}

    with conexao() as db:
        perfil, destino = google.concluir(
            db, _parametro(params, "code"), _parametro(params, "state")
        )
        try:
            usuario = contas.obter_ou_criar_usuario(
                db, perfil.email, nome=perfil.nome, avatar_url=perfil.avatar_url
            )
        except contas.ErroAuth as erro:
            # Conta aguardando aprovação (ou desativada): volta para a tela de
            # login com a explicação, em vez de uma página de erro crua.
            if erro.status == 403:
                return 302, b"", "text/plain", {"Location": "/entrar?erro=conta_inativa"}
            raise
        contas.vincular_identidade(db, usuario.id, "google", perfil.sub, perfil.email)
        token, max_age = _abrir_sessao(db, usuario, cabecalhos)
        with db:
            registra_evento(db, "login_google", email=perfil.email, usuario_id=usuario.id,
                            ip=_ip(cabecalhos))

    return (
        302,
        b"",
        "text/plain",
        {"Location": destino_seguro(destino), "Set-Cookie": _monta_cookie(token, max_age)},
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


# --------------------------------------------------------------------------
# Administração
# --------------------------------------------------------------------------


def _admin(
    metodo: str,
    rota: str,
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    """Rotas ``/auth/admin/*``: exigem sessão válida E ``admin``."""
    usuario = usuario_do_pedido(cabecalhos)
    if usuario is None:
        return _erro("Não autenticado.", 401)
    if not usuario.admin:
        return _erro("Só administradores podem acessar esta área.", 403)

    segmentos = [parte for parte in rota.split("/") if parte]

    with conexao() as db:
        if segmentos == ["usuarios"] and metodo == "GET":
            return 200, {"usuarios": contas.listar_usuarios(db)}, "application/json", {}

        if len(segmentos) == 2 and segmentos[0] == "usuarios" and metodo == "PATCH":
            alvo = _inteiro(segmentos[1])
            if alvo is None:
                return _erro("Id de conta inválido.")
            dados = _json_do_corpo(corpo)
            ativo = dados.get("ativo")
            admin = dados.get("admin")
            if ativo is not None and not isinstance(ativo, bool):
                return _erro("'ativo' precisa ser true ou false.")
            if admin is not None and not isinstance(admin, bool):
                return _erro("'admin' precisa ser true ou false.")
            atualizado = contas.atualizar_usuario(db, alvo, por=usuario, ativo=ativo, admin=admin)
            return 200, {"usuario": atualizado.to_dict()}, "application/json", {}

        if (
            len(segmentos) == 3
            and segmentos[0] == "usuarios"
            and segmentos[2] == "encerrar-sessoes"
            and metodo == "POST"
        ):
            alvo = _inteiro(segmentos[1])
            if alvo is None:
                return _erro("Id de conta inválido.")
            if contas.usuario_por_id(db, alvo, incluir_inativos=True) is None:
                return _erro("Conta não encontrada.", 404)
            quantas = contas.encerrar_todas_sessoes(db, alvo)
            with db:
                registra_evento(db, "admin_encerrou_sessoes", usuario_id=alvo,
                                detalhe=f"por {usuario.email}: {quantas} sessão(ões)")
            return 200, {"ok": True, "sessoes_encerradas": quantas}, "application/json", {}

        if segmentos == ["eventos"] and metodo == "GET":
            limite = _inteiro(_parametro(params, "limite", "100")) or 100
            email = _parametro(params, "email").strip() or None
            return (
                200,
                {"eventos": contas.listar_eventos(db, limite=limite, email=email)},
                "application/json",
                {},
            )

        if segmentos == ["sessoes"] and metodo == "GET":
            usuario_id = _inteiro(_parametro(params, "usuario_id")) if _parametro(params, "usuario_id") else None
            return (
                200,
                {"sessoes": contas.listar_sessoes(db, usuario_id=usuario_id)},
                "application/json",
                {},
            )

        if len(segmentos) == 2 and segmentos[0] == "sessoes" and metodo == "DELETE":
            sessao = _inteiro(segmentos[1])
            if sessao is None:
                return _erro("Id de sessão inválido.")
            if not contas.encerrar_sessao_por_id(db, sessao, por=usuario):
                return _erro("Sessão não encontrada ou já encerrada.", 404)
            return 200, {"ok": True}, "application/json", {}

    return _erro("Rota de administração não encontrada.", 404)
