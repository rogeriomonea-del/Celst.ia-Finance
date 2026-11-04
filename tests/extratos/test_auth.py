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
    # A maioria dos testes quer contas prontas; a aprovação tem testes próprios.
    monkeypatch.setenv("CELESTIA_CADASTRO_ABERTO", "1")
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


# --------------------------------------------------------------------------
# Aprovação de contas e administração
# --------------------------------------------------------------------------


def _sessao(token: str) -> dict:
    return {"cookie": f"{api.NOME_COOKIE}={token}"}


def test_conta_nova_espera_aprovacao_quando_cadastro_fechado(monkeypatch):
    monkeypatch.delenv("CELESTIA_CADASTRO_ABERTO", raising=False)
    admin, _ = _login("primeiro@exemplo.com")  # primeiro entra direto e é admin
    assert admin.admin

    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "segundo@exemplo.com")
        with pytest.raises(contas.ErroAuth) as erro:
            contas.verificar_codigo(db, "segundo@exemplo.com", codigo)
        assert erro.value.status == 403 and "aprovar" in erro.value.mensagem
        linha = db.execute("SELECT ativo, admin FROM usuarios WHERE email = 'segundo@exemplo.com'").fetchone()
        assert linha["ativo"] == 0 and linha["admin"] == 0
        acoes = [l["acao"] for l in db.execute("SELECT acao FROM eventos_acesso").fetchall()]
        assert "conta_aguardando_aprovacao" in acoes


def test_admin_aprova_conta_e_ela_passa_a_entrar(monkeypatch):
    monkeypatch.delenv("CELESTIA_CADASTRO_ABERTO", raising=False)
    admin, token_admin = _login("primeiro@exemplo.com")
    with store.conexao() as db:
        codigo = contas.gerar_codigo(db, "segundo@exemplo.com")
        with pytest.raises(contas.ErroAuth):
            contas.verificar_codigo(db, "segundo@exemplo.com", codigo)

    status, corpo, _, _ = _chamar("GET", "/auth/admin/usuarios", cabecalhos=_sessao(token_admin))
    assert status == 200
    pendente = next(u for u in corpo["usuarios"] if u["email"] == "segundo@exemplo.com")
    assert pendente["ativo"] is False

    status, corpo, _, _ = _chamar(
        "PATCH", f"/auth/admin/usuarios/{pendente['id']}",
        corpo=json.dumps({"ativo": True}).encode(), cabecalhos=_sessao(token_admin),
    )
    assert status == 200 and corpo["usuario"]["ativo"] is True

    segundo, _ = _login("segundo@exemplo.com")
    assert segundo.admin is False


def test_rotas_de_admin_exigem_admin():
    _, token_admin = _login("primeiro@exemplo.com")
    comum, token_comum = _login("segundo@exemplo.com")
    assert _chamar("GET", "/auth/admin/usuarios")[0] == 401
    assert _chamar("GET", "/auth/admin/usuarios", cabecalhos=_sessao(token_comum))[0] == 403
    assert _chamar("GET", "/auth/admin/eventos", cabecalhos=_sessao(token_comum))[0] == 403
    assert _chamar("GET", "/auth/admin/sessoes", cabecalhos=_sessao(token_admin))[0] == 200


def test_admin_nao_se_rebaixa_nem_remove_o_ultimo_admin():
    admin, token_admin = _login("primeiro@exemplo.com")
    status, corpo, _, _ = _chamar(
        "PATCH", f"/auth/admin/usuarios/{admin.id}",
        corpo=json.dumps({"admin": False}).encode(), cabecalhos=_sessao(token_admin),
    )
    assert status == 409
    status, _, _, _ = _chamar(
        "PATCH", f"/auth/admin/usuarios/{admin.id}",
        corpo=json.dumps({"ativo": False}).encode(), cabecalhos=_sessao(token_admin),
    )
    assert status == 409


def test_admin_desativa_conta_e_derruba_sessoes():
    admin, token_admin = _login("primeiro@exemplo.com")
    comum, token_comum = _login("segundo@exemplo.com")
    assert _chamar("GET", "/auth/eu", cabecalhos=_sessao(token_comum))[0] == 200

    status, corpo, _, _ = _chamar(
        "PATCH", f"/auth/admin/usuarios/{comum.id}",
        corpo=json.dumps({"ativo": False}).encode(), cabecalhos=_sessao(token_admin),
    )
    assert status == 200 and corpo["usuario"]["ativo"] is False
    assert _chamar("GET", "/auth/eu", cabecalhos=_sessao(token_comum))[0] == 401


def test_admin_ve_sessoes_e_eventos_e_encerra_sessao():
    admin, token_admin = _login("primeiro@exemplo.com")
    comum, token_comum = _login("segundo@exemplo.com")

    status, corpo, _, _ = _chamar(
        "GET", "/auth/admin/sessoes", params={"usuario_id": str(comum.id)}, cabecalhos=_sessao(token_admin)
    )
    assert status == 200 and len(corpo["sessoes"]) == 1
    sessao = corpo["sessoes"][0]
    assert "token" not in sessao and "token_hash" not in sessao

    status, corpo, _, _ = _chamar(
        "DELETE", f"/auth/admin/sessoes/{sessao['id']}", cabecalhos=_sessao(token_admin)
    )
    assert status == 200
    assert _chamar("GET", "/auth/eu", cabecalhos=_sessao(token_comum))[0] == 401

    status, corpo, _, _ = _chamar("GET", "/auth/admin/eventos", cabecalhos=_sessao(token_admin))
    assert status == 200
    acoes = {e["acao"] for e in corpo["eventos"]}
    assert "admin_encerrou_sessao" in acoes and "login_codigo" in acoes
    assert all("codigo" not in str(e.get("detalhe") or "").lower() or "codigo_" in e["acao"] for e in corpo["eventos"])

    # Derrubar todos os dispositivos de uma conta.
    _, token_novo = _login("segundo@exemplo.com")
    status, corpo, _, _ = _chamar(
        "POST", f"/auth/admin/usuarios/{comum.id}/encerrar-sessoes", cabecalhos=_sessao(token_admin)
    )
    assert status == 200 and corpo["sessoes_encerradas"] >= 1
    assert _chamar("GET", "/auth/eu", cabecalhos=_sessao(token_novo))[0] == 401


# --------------------------------------------------------------------------
# Destino pós-login (plataforma de voos no outro domínio)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "destino, esperado",
    [
        ("/extratos", "/extratos"),
        ("", "/"),
        ("//evil.com", "/"),
        ("https://evil.com/", "/"),
        ("https://celestiaflights.com/", "https://celestiaflights.com/"),
        ("https://www.celestiaflights.com/voos", "https://www.celestiaflights.com/voos"),
        ("https://celestiaflights.com.evil.com/", "/"),
        ("http://celestiaflights.com/", "/"),  # sem TLS fora do localhost
        ("http://localhost:5173/", "http://localhost:5173/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_destino_seguro(destino, esperado):
    assert api.destino_seguro(destino) == esperado


@pytest.mark.parametrize(
    "destino",
    [
        "https://evil.com\\@celestiaflights.com/",
        "https://evil.com\\.celestiaflights.com/",
        "https://user@celestiaflights.com/",
        "https://celestiaflights.com evil/",
        "https://celestiaflights.com/\r\nSet-Cookie: x=1",
        "https://celestiaflights.com:abc/",
    ],
)
def test_destino_seguro_recusa_truques_de_parse(destino):
    assert api.destino_seguro(destino) == "/"


def test_destino_seguro_reconstroi_a_url():
    assert api.destino_seguro("https://celestiaflights.com/voos?x=1#frag") == "https://celestiaflights.com/voos?x=1"
    assert api.destino_seguro("https://celestiaflights.com") == "https://celestiaflights.com/"


def test_ip_de_auditoria_nao_e_forjavel():
    assert api._ip({"x-real-ip": "10.0.0.9", "x-forwarded-for": "8.8.8.8, 10.0.0.9"}) == "10.0.0.9"
    assert api._ip({"x-forwarded-for": "8.8.8.8, 10.0.0.9"}) == "10.0.0.9"
    assert api._ip({}) is None


def test_retorno_do_google_exige_o_navegador_que_iniciou(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-teste")
    from engine.auth import google

    status, _, _, cabecalhos = _chamar("GET", "/auth/google", params={"destino": "/extratos"})
    assert status == 302
    cookie = cabecalhos["Set-Cookie"]
    assert cookie.startswith(f"{api.NOME_COOKIE_OAUTH}=") and "HttpOnly" in cookie
    nonce = cookie.split(";")[0].split("=", 1)[1]
    with store.conexao() as db:
        estado = db.execute("SELECT estado FROM estados_oauth").fetchone()[0]

    # URL de retorno roubada, usada noutro navegador (sem o cookie): recusada,
    # e o state é queimado de qualquer jeito.
    with store.conexao() as db:
        with pytest.raises(contas.ErroAuth) as erro:
            google._consumir_estado(db, estado, nonce="outro")
        assert erro.value.status == 401
        assert db.execute("SELECT COUNT(*) FROM estados_oauth").fetchone()[0] == 0

    # Com o cookie certo passa.
    status, _, _, cabecalhos = _chamar("GET", "/auth/google", params={"destino": "/extratos"})
    nonce = cabecalhos["Set-Cookie"].split(";")[0].split("=", 1)[1]
    with store.conexao() as db:
        estado = db.execute("SELECT estado FROM estados_oauth").fetchone()[0]
        assert google._consumir_estado(db, estado, nonce=nonce) == "/extratos"


def test_erro_no_retorno_do_google_volta_para_a_tela_de_login(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-teste")
    # state inexistente → navegação de topo recebe redirect, nunca JSON cru.
    status, corpo, _, cabecalhos = _chamar(
        "GET", "/auth/google/retorno", params={"code": "abc", "state": "inexistente"}
    )
    assert status == 302 and cabecalhos["Location"].startswith("/entrar?erro=google_")
    assert corpo == b""
    with store.conexao() as db:
        evento = db.execute(
            "SELECT acao, detalhe FROM eventos_acesso WHERE acao = 'login_google_falhou'"
        ).fetchone()
        assert evento is not None and "abc" not in (evento["detalhe"] or "")


def test_primeiro_acesso_simultaneo_nao_quebra():
    """Várias requisições ao mesmo tempo num extratos.db ainda inexistente."""
    import threading
    from engine.extratos import api as extratos_api

    _, token = _login("rogerio@exemplo.com")
    cabecalhos = {"cookie": f"{api.NOME_COOKIE}={token}"}
    resultados = []

    def chama():
        resultados.append(extratos_api.tratar("GET", "/extratos/painel", {}, b"", cabecalhos)[0])

    threads = [threading.Thread(target=chama) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resultados == [200] * 8


def test_migrar_dados_da_raiz_para_a_conta(tmp_path):
    import io
    from engine.auth import migrar_dados
    from engine.extratos import store as extratos_store

    raiz = extratos_store.diretorio_dados()
    with extratos_store.conexao(raiz / "extratos.db") as db:
        with db:
            db.execute("INSERT INTO instituicoes (nome) VALUES ('Banco Antigo')")
    (raiz / "originais" / "ab").mkdir(parents=True)
    (raiz / "originais" / "ab" / "abc.csv").write_bytes(b"x")

    assert migrar_dados.migrar("ninguem@exemplo.com", saida=io.StringIO()) == 2
    usuario, _ = _login("rogerio@exemplo.com")

    saida = io.StringIO()
    assert migrar_dados.migrar("rogerio@exemplo.com", simular=True, saida=saida) == 0
    assert (raiz / "extratos.db").exists() and "simulação" in saida.getvalue()

    assert migrar_dados.migrar(str(usuario.id), saida=io.StringIO()) == 0
    espaco = store.espaco_do_usuario(usuario.id)
    assert not (raiz / "extratos.db").exists() and not (raiz / "originais").exists()
    assert (espaco / "originais" / "ab" / "abc.csv").read_bytes() == b"x"
    with extratos_store.conexao(espaco / "extratos.db") as db:
        assert db.execute("SELECT nome FROM instituicoes").fetchone()[0] == "Banco Antigo"

    # Segunda vez: espaço já ocupado → recusa sem tocar em nada.
    with extratos_store.conexao(raiz / "extratos.db"):
        pass
    assert migrar_dados.migrar(str(usuario.id), saida=io.StringIO()) == 3
