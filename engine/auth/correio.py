"""Envio do código de verificação por e-mail (SMTP da stdlib).

Configuração por variáveis de ambiente. Sem SMTP configurado o sistema **não
finge que enviou**: o código vai para o log do serviço e a resposta da API diz
que o envio está em modo de desenvolvimento. Isso permite testar o login antes
de ter e-mail transacional, sem nunca dar a impressão falsa de que a mensagem
chegou.

| Variável | Para quê |
|---|---|
| `CELESTIA_SMTP_HOST` | Servidor (ex.: `smtp.hostinger.com`) |
| `CELESTIA_SMTP_PORT` | 465 (SSL) ou 587 (STARTTLS). Padrão 587 |
| `CELESTIA_SMTP_USER` | Usuário/caixa remetente |
| `CELESTIA_SMTP_SENHA` | Senha da caixa |
| `CELESTIA_SMTP_DE` | Remetente exibido. Padrão: o usuário |
| `CELESTIA_APP_NOME` | Nome nas mensagens. Padrão "celest.ia" |
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional, Tuple

log = logging.getLogger("celestia.correio")


def configurado() -> bool:
    return bool(os.environ.get("CELESTIA_SMTP_HOST") and os.environ.get("CELESTIA_SMTP_USER"))


def _remetente() -> str:
    return os.environ.get("CELESTIA_SMTP_DE") or os.environ.get("CELESTIA_SMTP_USER", "")


def _app() -> str:
    return os.environ.get("CELESTIA_APP_NOME", "celest.ia")


def _monta_mensagem(destino: str, codigo: str) -> EmailMessage:
    app = _app()
    msg = EmailMessage()
    msg["Subject"] = f"{codigo} é o seu código de acesso ao {app}"
    msg["From"] = f"{app} <{_remetente()}>"
    msg["To"] = destino
    # Sem rastreadores, sem link clicável: só o código. Reduz a superfície de
    # phishing e o e-mail não depende de imagens externas.
    msg.set_content(
        f"Seu código de acesso ao {app} é:\n\n"
        f"    {codigo}\n\n"
        "Ele vale por 10 minutos e só pode ser usado uma vez.\n\n"
        "Se você não pediu este código, ignore esta mensagem — ninguém entra "
        "na sua conta sem ele.\n"
    )
    msg.add_alternative(
        f"""<!doctype html>
<html lang="pt-BR"><body style="margin:0;background:#09090b;padding:32px 16px;
font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#e4e4e7">
<div style="max-width:420px;margin:0 auto;background:#121214;border:1px solid #27272a;
border-radius:14px;padding:32px">
<p style="margin:0 0 24px;font-size:15px;color:#a1a1aa">Seu código de acesso ao
<strong style="color:#fafafa">{app}</strong>:</p>
<p style="margin:0 0 24px;font-size:38px;letter-spacing:10px;font-weight:700;
color:#10b981;text-align:center;font-family:ui-monospace,monospace">{codigo}</p>
<p style="margin:0 0 8px;font-size:13px;color:#a1a1aa">Vale por 10 minutos e só
pode ser usado uma vez.</p>
<p style="margin:0;font-size:13px;color:#71717a">Se você não pediu este código,
ignore esta mensagem — ninguém entra na sua conta sem ele.</p>
</div></body></html>""",
        subtype="html",
    )
    return msg


def enviar_codigo(destino: str, codigo: str) -> Tuple[bool, str]:
    """Envia o código. Devolve ``(enviado, modo)``.

    ``modo`` é ``"email"`` quando saiu de verdade, ``"log"`` quando não há SMTP
    configurado (o código foi para o log do serviço) e ``"erro"`` quando o SMTP
    existe mas recusou — nesse caso ``enviado`` é ``False`` e quem chamou avisa
    o usuário em vez de deixá-lo esperando uma mensagem que não vem.
    """
    if not configurado():
        log.warning("SMTP não configurado — código de %s: %s", destino, codigo)
        return True, "log"

    host = os.environ["CELESTIA_SMTP_HOST"]
    porta = int(os.environ.get("CELESTIA_SMTP_PORT", "587"))
    usuario = os.environ["CELESTIA_SMTP_USER"]
    senha = os.environ.get("CELESTIA_SMTP_SENHA", "")
    contexto = ssl.create_default_context()

    try:
        if porta == 465:
            with smtplib.SMTP_SSL(host, porta, context=contexto, timeout=15) as smtp:
                smtp.login(usuario, senha)
                smtp.send_message(_monta_mensagem(destino, codigo))
        else:
            with smtplib.SMTP(host, porta, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=contexto)
                smtp.login(usuario, senha)
                smtp.send_message(_monta_mensagem(destino, codigo))
    except Exception as erro:  # noqa: BLE001 - qualquer falha de SMTP é a mesma para quem chama
        # O tipo do erro ajuda a diagnosticar; o código jamais entra no log aqui.
        log.error("falha ao enviar código para %s: %s", destino, type(erro).__name__)
        return False, "erro"
    return True, "email"


def teste_conexao() -> Optional[str]:
    """Testa as credenciais SMTP. ``None`` = tudo certo; texto = o problema."""
    if not configurado():
        return "SMTP não configurado (CELESTIA_SMTP_HOST e CELESTIA_SMTP_USER)"
    try:
        host = os.environ["CELESTIA_SMTP_HOST"]
        porta = int(os.environ.get("CELESTIA_SMTP_PORT", "587"))
        contexto = ssl.create_default_context()
        if porta == 465:
            with smtplib.SMTP_SSL(host, porta, context=contexto, timeout=15) as smtp:
                smtp.login(os.environ["CELESTIA_SMTP_USER"], os.environ.get("CELESTIA_SMTP_SENHA", ""))
        else:
            with smtplib.SMTP(host, porta, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=contexto)
                smtp.login(os.environ["CELESTIA_SMTP_USER"], os.environ.get("CELESTIA_SMTP_SENHA", ""))
    except Exception as erro:  # noqa: BLE001
        return f"{type(erro).__name__}: {erro}"
    return None
