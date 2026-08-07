"""Autenticação multiusuário: contas, códigos, sessões e isolamento."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.auth import api, contas, store  # noqa: E402


@pytest.fixture(autouse=True)
def dados_isolados(tmp_path, monkeypatch):
    """Cada teste roda num diretório de dados próprio."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CELESTIA_URL_BASE", "https://financas.celestiaflights.com")
    monkeypatch.setenv("CELESTIA_COOKIE_DOMINIO", ".celestiaflights.com")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CELESTIA_SMTP_HOST", raising=False)
    yield


def _login(email: str = "rogerio@exemplo.com") -> tuple[contas.Usuario, str]:
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, email)
        usuario = contas.verificar_codigo(db, email, codigo)
        token, _ = contas.criar_sessao(db, usuario.id)
    return usuario, token


# --------------------------------------------------------------------------
# Códigos
# --------------------------------------------------------------------------


def test_codigo_tem_seis_digitos_e_nao_fica_em_claro():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        assert len(codigo) == 6 and codigo.isdigit()
        guardado = db.execute("SELECT codigo_hash FROM codigos").fetchone()["codigo_hash"]
        assert codigo not in guardado and len(guardado) == 64


def test_codigo_serve_uma_vez_so():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        contas.verificar_codigo(db, "rogerio@exemplo.com", codigo)
        with pytest.raises(contas.ErroAuth):
            contas.verificar_codigo(db, "rogerio@exemplo.com", codigo)


def test_codigo_nao_vale_para_outro_email():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        contas.gerar_codigo(db, "invasor@exemplo.com")
        with pytest.raises(contas.ErroAuth):
            contas.verificar_codigo(db, "invasor@exemplo.com", codigo)


def test_tentativas_erradas_queimam_o_codigo():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        for _ in range(contas.MAX_TENTATIVAS):
            with pytest.raises(contas.ErroAuth):
                contas.verificar_codigo(db, "rogerio@exemplo.com", "000000")
        # Mesmo com o código certo, agora está bloqueado.
        with pytest.raises(contas.ErroAuth) as erro:
            contas.verificar_codigo(db, "rogerio@exemplo.com", codigo)
        assert erro.value.status == 429


def test_pedido_de_codigo_tem_limite():
    with store.conexao() as db:
        for _ in range(contas.MAX_PEDIDOS):
            contas.gerar_codigo(db, "flood@exemplo.com")
        with pytest.raises(contas.ErroAuth) as erro:
            contas.gerar_codigo(db, "flood@exemplo.com")
        assert erro.value.status == 429


def test_codigo_novo_invalida_o_anterior():
    with store.conexao() as db:
        primeiro = contas.gerar_codigo(db, "rogerio@exemplo.com")
        segundo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        with pytest.raises(contas.ErroAuth):
            contas.verificar_codigo(db, "rogerio@exemplo.com", primeiro)
        assert contas.verificar_codigo(db, "rogerio@exemplo.com", segundo).email


@pytest.mark.parametrize(
    "email", ["", "sem-arroba", "a@b", "a@@b.com", "espaço@x.com", "a..b@x.com"]
)
def test_email_invalido_recusado(email):
    with store.conexao() as db:
        with pytest.raises(contas.ErroAuth):
            contas.gerar_codigo(db, email)


# --------------------------------------------------------------------------
# Contas e sessões
# --------------------------------------------------------------------------


def test_primeiro_usuario_vira_admin_e_os_demais_nao():
    primeiro, _ = _login("primeiro@exemplo.com")
    segundo, _ = _login("segundo@exemplo.com")
    assert primeiro.admin is True
    assert segundo.admin is False


def test_email_com_caixa_diferente_e_a_mesma_conta():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "  Rogerio@Exemplo.COM ")
        usuario = contas.verificar_codigo(db, "rogerio@exemplo.com", codigo)
        assert usuario.email == "rogerio@exemplo.com"
        assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1


def test_token_de_sessao_nao_fica_em_claro():
    _, token = _login()
    with store.conexao() as db:
        guardado = db.execute("SELECT token_hash FROM sessoes").fetchone()["token_hash"]
    assert token not in guardado


def test_sessao_encerrada_perde_a_validade():
    usuario, token = _login()
    with store.conexao() as db:
        assert contas.validar_sessao(db, token).id == usuario.id
        contas.encerrar_sessao(db, token)
        assert contas.validar_sessao(db, token) is None


def test_token_adulterado_nao_entra():
    _, token = _login()
    with store.conexao() as db:
        assert contas.validar_sessao(db, token + "x") is None
        assert contas.validar_sessao(db, "") is None
        assert contas.validar_sessao(db, None) is None


def test_sair_de_tudo_derruba_todos_os_dispositivos():
    usuario, primeiro = _login()
    with store.conexao() as db:
        segundo, _ = contas.criar_sessao(db, usuario.id)
        assert contas.encerrar_todas_sessoes(db, usuario.id) == 2
        assert contas.validar_sessao(db, primeiro) is None
        assert contas.validar_sessao(db, segundo) is None


def test_conta_desativada_nao_entra():
    usuario, _ = _login()
    with store.conexao() as db:
        with db:
            db.execute("UPDATE usuarios SET ativo = 0 WHERE id = ?", (usuario.id,))
        codigo_novo = contas.gerar_codigo(db, usuario.email)
        with pytest.raises(contas.ErroAuth) as erro:
            contas.verificar_codigo(db, usuario.email, codigo_novo)
        assert erro.value.status == 403


# --------------------------------------------------------------------------
# Isolamento entre usuários — a garantia mais importante
# --------------------------------------------------------------------------


def test_invariante_cada_usuario_tem_banco_proprio():
    primeiro, _ = _login("primeiro@exemplo.com")
    segundo, _ = _login("segundo@exemplo.com")

    banco_um = store.banco_do_usuario(primeiro.id)
    banco_dois = store.banco_do_usuario(segundo.id)
    assert banco_um != banco_dois
    assert banco_um.parent != banco_dois.parent

    # Escrever num não aparece no outro: o isolamento é do sistema de arquivos,
    # não de uma cláusula WHERE que dá para esquecer.
    from engine.extratos import store as extratos_store

    with extratos_store.conexao(banco_um) as db:
        with db:
            db.execute("INSERT INTO instituicoes (nome) VALUES ('Banco do Primeiro')")
    with extratos_store.conexao(banco_dois) as db:
        assert db.execute("SELECT COUNT(*) FROM instituicoes").fetchone()[0] == 0


def test_espaco_do_usuario_recusa_id_invalido():
    for invalido in (0, -1, "1", None):
        with pytest.raises(ValueError):
            store.espaco_do_usuario(invalido)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Rotas HTTP
# --------------------------------------------------------------------------


def _chamar(metodo, caminho, params=None, corpo=b"", cabecalhos=None):
    return api.tratar(metodo, caminho, params or {}, corpo, cabecalhos or {})


def test_fluxo_http_completo_de_login():
    status, corpo, _, _ = _chamar(
        "POST", "/auth/codigo", corpo=json.dumps({"email": "rogerio@exemplo.com"}).encode()
    )
    assert status == 200 and corpo["modo"] == "log"

    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")

    status, corpo, _, cabecalhos = _chamar(
        "POST",
        "/auth/verificar",
        corpo=json.dumps({"email": "rogerio@exemplo.com", "codigo": codigo}).encode(),
    )
    assert status == 200
    cookie = cabecalhos["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    assert "Domain=.celestiaflights.com" in cookie

    token = cookie.split(";")[0].split("=", 1)[1]
    status, corpo, _, _ = _chamar("GET", "/auth/eu", cabecalhos={"cookie": f"{api.NOME_COOKIE}={token}"})
    assert status == 200 and corpo["usuario"]["email"] == "rogerio@exemplo.com"

    status, _, _, saida = _chamar("POST", "/auth/sair", cabecalhos={"cookie": f"{api.NOME_COOKIE}={token}"})
    assert status == 200 and "Max-Age=0" in saida["Set-Cookie"]
    assert _chamar("GET", "/auth/eu", cabecalhos={"cookie": f"{api.NOME_COOKIE}={token}"})[0] == 401


def test_rota_protegida_sem_sessao_devolve_401():
    assert _chamar("GET", "/auth/eu")[0] == 401
    assert _chamar("POST", "/auth/sair-de-tudo")[0] == 401


def test_google_desligado_avisa_em_vez_de_quebrar():
    status, corpo, _, _ = _chamar("GET", "/auth/google")
    assert status == 501 and "não está configurado" in corpo["erro"]
    assert _chamar("GET", "/auth/config")[1]["google"] is False


def test_google_nao_aceita_redirecionamento_externo(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-teste")
    status, _, _, cabecalhos = _chamar("GET", "/auth/google", params={"destino": "https://malicioso.com"})
    assert status == 302
    with store.conexao() as db:
        destino = db.execute("SELECT redirecionar_para FROM estados_oauth").fetchone()[0]
    assert destino == "/"


def test_estado_do_google_serve_uma_vez_so(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-teste")
    from engine.auth import google

    with store.conexao() as db:
        google.iniciar(db, "/extratos")
        estado = db.execute("SELECT estado FROM estados_oauth").fetchone()[0]
        assert google._consumir_estado(db, estado) == "/extratos"
        with pytest.raises(contas.ErroAuth):
            google._consumir_estado(db, estado)


def test_acesso_fica_registrado_sem_expor_o_codigo():
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "rogerio@exemplo.com")
        contas.verificar_codigo(db, "rogerio@exemplo.com", codigo)
        eventos = db.execute("SELECT acao, detalhe FROM eventos_acesso").fetchall()
    acoes = {evento["acao"] for evento in eventos}
    assert {"codigo_solicitado", "login_codigo"} <= acoes
    assert all(codigo not in (evento["detalhe"] or "") for evento in eventos)
