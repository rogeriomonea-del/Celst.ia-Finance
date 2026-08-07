"""Entrar com Google — OAuth 2.0 (Authorization Code), só com a stdlib.

Fluxo: o navegador vai para o Google com um ``state`` que guardamos; o Google
devolve um ``code``; trocamos o code por um access token **do servidor**, e com
ele consultamos o perfil.

Não validamos assinatura de JWT de propósito. Verificar um ``id_token`` exigiria
RSA e o JWKS do Google, que a stdlib não faz bem. Em vez disso perguntamos o
perfil ao próprio Google pelo endpoint ``userinfo``, por HTTPS, com o token que
acabamos de receber: a resposta vem da fonte, autenticada pelo TLS, sem
criptografia caseira. É o caminho recomendado para clientes confidenciais.

| Variável | Para quê |
|---|---|
| `GOOGLE_CLIENT_ID` | ID do cliente OAuth |
| `GOOGLE_CLIENT_SECRET` | Segredo do cliente |
| `CELESTIA_URL_BASE` | URL pública (ex.: `https://financas.celestiaflights.com`) |
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .contas import ErroAuth
from .store import agora, iso, novo_token

AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
PERFIL = "https://openidconnect.googleapis.com/v1/userinfo"

CAMINHO_RETORNO = "/api/py/auth/google/retorno"


@dataclass(frozen=True)
class PerfilGoogle:
    sub: str
    email: str
    nome: str
    avatar_url: Optional[str]
    email_verificado: bool


def configurado() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def url_base() -> str:
    return os.environ.get("CELESTIA_URL_BASE", "http://localhost:3000").rstrip("/")


def url_retorno() -> str:
    """URI de redirecionamento — precisa estar idêntica no console do Google."""
    return f"{url_base()}{CAMINHO_RETORNO}"


def iniciar(db: sqlite3.Connection, redirecionar_para: str = "/") -> str:
    """Guarda o ``state`` e devolve a URL para onde mandar o navegador.

    O ``state`` é a defesa contra CSRF no retorno: quem não passou por aqui não
    tem um válido.
    """
    if not configurado():
        raise ErroAuth("Entrar com Google não está configurado neste servidor.", status=501)

    estado = novo_token(24)
    with db:
        db.execute(
            "INSERT INTO estados_oauth (estado, redirecionar_para) VALUES (?,?)",
            (estado, redirecionar_para or "/"),
        )

    parametros = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": url_retorno(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": estado,
        "access_type": "online",
        # Deixa o usuário escolher a conta em vez de entrar direto na última.
        "prompt": "select_account",
    }
    return f"{AUTORIZACAO}?{urllib.parse.urlencode(parametros)}"


def _consumir_estado(db: sqlite3.Connection, estado: str) -> str:
    """Valida e queima o ``state``; devolve para onde redirecionar depois."""
    linha = db.execute(
        "SELECT redirecionar_para FROM estados_oauth WHERE estado = ?", (estado or "",)
    ).fetchone()
    if linha is None:
        raise ErroAuth("Sessão de login expirada ou inválida. Tente novamente.")
    with db:
        db.execute("DELETE FROM estados_oauth WHERE estado = ?", (estado,))
    return linha["redirecionar_para"] or "/"


def _post_json(url: str, dados: dict, timeout: float = 15.0) -> dict:
    corpo = urllib.parse.urlencode(dados).encode("utf-8")
    req = urllib.request.Request(
        url, data=corpo, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def _get_json(url: str, token: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def concluir(db: sqlite3.Connection, code: str, estado: str) -> tuple[PerfilGoogle, str]:
    """Troca o ``code`` pelo perfil. Devolve ``(perfil, redirecionar_para)``."""
    if not configurado():
        raise ErroAuth("Entrar com Google não está configurado neste servidor.", status=501)
    destino = _consumir_estado(db, estado)
    if not code:
        raise ErroAuth("O Google não devolveu o código de autorização.")

    try:
        resposta = _post_json(
            TOKEN,
            {
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": url_retorno(),
                "grant_type": "authorization_code",
            },
        )
        access_token = resposta.get("access_token")
        if not access_token:
            raise ErroAuth("O Google não devolveu um token de acesso.")
        perfil = _get_json(PERFIL, access_token)
    except urllib.error.HTTPError as erro:
        # A URI de retorno divergente do console é o erro mais comum aqui.
        raise ErroAuth(
            "O Google recusou o login. Confira se a URI de redirecionamento "
            f"cadastrada é exatamente {url_retorno()}.",
            status=502,
        ) from erro
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as erro:
        raise ErroAuth("Não foi possível falar com o Google agora.", status=502) from erro

    email = (perfil.get("email") or "").strip().lower()
    if not email:
        raise ErroAuth("A conta Google não expôs um e-mail.")
    if not perfil.get("email_verified", False):
        # Sem isso, alguém com e-mail não verificado assumiria a conta de outro.
        raise ErroAuth("Este e-mail não está verificado no Google.", status=403)

    return (
        PerfilGoogle(
            sub=str(perfil.get("sub") or ""),
            email=email,
            nome=(perfil.get("name") or "").strip(),
            avatar_url=perfil.get("picture"),
            email_verificado=True,
        ),
        destino,
    )


def diagnostico() -> dict:
    """Estado da configuração, para a tela de administração."""
    return {
        "configurado": configurado(),
        "client_id_presente": bool(os.environ.get("GOOGLE_CLIENT_ID")),
        "segredo_presente": bool(os.environ.get("GOOGLE_CLIENT_SECRET")),
        "url_retorno": url_retorno(),
    }
