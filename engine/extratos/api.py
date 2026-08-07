"""API HTTP do módulo de extratos — roteador puro, sem framework.

Contrato único usado pelo servidor de desenvolvimento (``engine/server.py``)
e pela função serverless (``api/extratos.py``):

    tratar(metodo, caminho, params, corpo, cabecalhos)
        -> (status, payload, content_type)
        -> (status, bytes, content_type, cabecalhos_extras)   # só no download

* ``payload`` é um ``dict`` JSON-pronto (o adaptador HTTP serializa com
  :func:`corpo_json`) ou ``bytes`` crus no download do original — nesse caso a
  tupla ganha um 4º elemento com cabeçalhos extras (``Content-Disposition``).
* Erros SEMPRE viram ``{"erro": mensagem em pt-BR}`` com o status adequado
  (400/401/404/409/413/503) — conteúdo ruim nunca vira exceção não tratada.

Segurança:

* ``CELESTIA_API_TOKEN`` definido → TODAS as rotas exigem
  ``Authorization: Bearer <token>`` (comparação em tempo constante); sem o
  token correto a resposta é 401.
* Limite de upload ``CELESTIA_MAX_UPLOAD_MB`` (padrão 15) → 413.
* Ids são sempre ``int(...)`` validados (400 quando inválidos); caminhos de
  arquivo derivam do sha256 no storage — nunca do caminho da URL (sem path
  traversal por construção).
* Mutações (POST/PATCH/DELETE) exigem storage durável: em serverless
  (``VERCEL`` setado) sem ``CELESTIA_DATA_DURAVEL=1`` a resposta é 503 com
  explicação honesta; leituras respondem normalmente.
* Nada aqui escreve log — em particular, nunca descrição de transação nem
  corpo de upload.

O parse de ``multipart/form-data`` é REUSADO de ``api/planilha.py`` (carregado
pelo caminho, como ``engine/server.py`` já faz): um parser só, testado uma vez.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import importlib.util
import json
import os
import re
import sqlite3
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote, urlsplit

from . import conciliacao, config_planilha, storage, store
from . import categorias as categorias_mod
from . import consultas
from . import fluxo as fluxo_mod
from . import painel as painel_mod
from . import pipeline
from .consultas import Filtros
from .modelos import TIPOS_DOCUMENTO, TIPOS_TRANSACAO
from .store import registra_auditoria

#: Content-Type padrão das respostas JSON.
TIPO_JSON: str = "application/json; charset=utf-8"

#: Resposta do roteador: (status, payload, content_type[, cabecalhos_extras]).
Resposta = Union[
    Tuple[int, Union[Dict[str, Any], bytes], str],
    Tuple[int, bytes, str, Dict[str, str]],
]

MSG_SEM_STORAGE = (
    "gravação indisponível neste deploy: o ambiente serverless (Vercel) tem"
    " filesystem efêmero, então os dados de extratos seriam perdidos a cada"
    " execução. Para gravar de verdade, rode o motor self-host"
    " (python3 -m engine.server) com CELESTIA_DATA_DIR apontando para um disco"
    " persistente — ou monte storage durável no deploy e declare"
    " CELESTIA_DATA_DURAVEL=1. As leituras continuam funcionando."
)
MSG_NAO_AUTORIZADO = (
    "não autorizado: envie o cabeçalho 'Authorization: Bearer <token>'"
    " com o valor de CELESTIA_API_TOKEN"
)
MSG_SEM_ARQUIVO = (
    'nenhum arquivo recebido — envie multipart/form-data com o campo "file"'
    ' ou JSON com "file_base64"'
)
MSG_INESPERADO = "erro inesperado na API de extratos"

#: Campos de texto aceitos junto do arquivo no upload (multipart ou JSON).
_CAMPOS_UPLOAD: Tuple[str, ...] = (
    "instituicao", "conta", "cartao", "tipo_documento", "periodo",
    "senha_pdf", "moeda", "aprovado_por", "sobrescrever",
)

#: Campos editáveis de um cartão (lista branca — nunca interpolar livre).
_CAMPOS_CARTAO: Tuple[str, ...] = (
    "nome", "emissor", "bandeira", "final", "conta_pagadora_id",
    "instituicao_id", "dia_fechamento", "dia_vencimento",
    "limite_total_centavos",
)

_RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_API_PLANILHA: Optional[ModuleType] = None


# --------------------------------------------------------------------------
# Infra: reuse do parser multipart, limites, serialização
# --------------------------------------------------------------------------


def _api_planilha() -> ModuleType:
    """Carrega ``api/planilha.py`` (parser multipart + nome_seguro) uma vez.

    ``api/`` não é pacote Python (o runtime da Vercel carrega o arquivo solto),
    então o módulo é carregado pelo caminho — o mesmo truque de
    ``engine/server.py``, reaproveitando a instância se ela já existir.
    """
    global _API_PLANILHA
    if _API_PLANILHA is not None:
        return _API_PLANILHA
    existente = sys.modules.get("api_planilha")
    if existente is not None:
        _API_PLANILHA = existente
        return existente
    caminho = os.path.join(_RAIZ, "api", "planilha.py")
    spec = importlib.util.spec_from_file_location("api_planilha", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - projeto quebrado
        raise RuntimeError(f"não foi possível carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    _API_PLANILHA = modulo
    return modulo


def limite_upload_mb() -> int:
    """Limite de upload em MB (``CELESTIA_MAX_UPLOAD_MB``, padrão 15)."""
    try:
        megabytes = int(os.environ.get("CELESTIA_MAX_UPLOAD_MB", "15"))
    except ValueError:
        megabytes = 15
    return max(1, megabytes)


def limite_upload_bytes() -> int:
    return limite_upload_mb() * 1024 * 1024


def limite_corpo_bytes() -> int:
    """Teto do corpo HTTP: o upload + folga para o overhead do multipart/base64."""
    return limite_upload_bytes() * 2 + 4096


def _sanear(valor: Any) -> Any:
    """Remove NaN/Inf e tipos exóticos que quebrariam o JSON no navegador."""
    if isinstance(valor, float):
        return valor if valor == valor and valor not in (float("inf"), float("-inf")) else None
    if isinstance(valor, dict):
        return {str(chave): _sanear(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sanear(item) for item in valor]
    if valor is None or isinstance(valor, (str, int, bool)):
        return valor
    return str(valor)


def corpo_json(payload: Any) -> bytes:
    """Serializa a resposta em UTF-8; falha de serialização vira erro legível."""
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        try:
            return json.dumps(
                _sanear(payload), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            return json.dumps({"erro": MSG_INESPERADO}, ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------
# Infra: helpers de requisição
# --------------------------------------------------------------------------


def _erro(status: int, mensagem: str) -> Resposta:
    return status, {"erro": mensagem}, TIPO_JSON


def _ok(payload: Dict[str, Any]) -> Resposta:
    return 200, payload, TIPO_JSON


def _param(params: Dict[str, Any], chave: str, padrao: str = "") -> str:
    """Valor textual de um parâmetro de querystring (``parse_qs`` dá listas)."""
    bruto = (params or {}).get(chave, padrao)
    if isinstance(bruto, (list, tuple)):
        bruto = bruto[0] if bruto else padrao
    if bruto is None:
        return padrao
    return str(bruto).strip()


def _verdadeiro(texto: Any) -> bool:
    if isinstance(texto, bool):
        return texto
    return str(texto or "").strip().lower() in ("1", "true", "sim", "yes", "on")


def _int_de(valor: Any) -> Optional[int]:
    """Inteiro estrito: bool/float/texto não numérico viram ``None``."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str):
        try:
            return int(valor.strip())
        except ValueError:
            return None
    return None


def _json_do_corpo(corpo: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Corpo → dict JSON. Corpo vazio vale ``{}``; inválido devolve mensagem."""
    if not corpo or not corpo.strip():
        return {}, None
    try:
        dados = json.loads(corpo.decode("utf-8", "replace"))
    except ValueError:
        return None, "JSON inválido no corpo da requisição"
    if not isinstance(dados, dict):
        return None, "JSON inválido: esperava um objeto"
    return dados, None


def _token_ok(cabecalhos: Dict[str, str]) -> bool:
    """Sem ``CELESTIA_API_TOKEN`` a API é aberta (dev local); com ele, todas as
    rotas exigem Bearer correto — comparação em tempo constante."""
    esperado = os.environ.get("CELESTIA_API_TOKEN", "")
    if not esperado:
        return True
    bruto = cabecalhos.get("authorization", "")
    esquema, _, credencial = bruto.partition(" ")
    if esquema.strip().lower() != "bearer":
        return False
    return hmac.compare_digest(credencial.strip().encode("utf-8"), esperado.encode("utf-8"))


def _serverless_sem_duravel() -> bool:
    """Deploy serverless (``VERCEL`` setado) sem storage durável declarado."""
    vercel = os.environ.get("VERCEL", "").strip()
    if vercel in ("", "0"):
        return False
    return os.environ.get("CELESTIA_DATA_DURAVEL", "").strip() != "1"


def _status_de_erro(resposta: Dict[str, Any]) -> int:
    """Mapeia a mensagem de erro dos módulos de domínio para o status HTTP."""
    if resposta.get("erro_duplicado"):
        return 409
    mensagem = str(resposta.get("erro") or resposta.get("mensagem") or "").lower()
    if "não encontrad" in mensagem or "nao encontrad" in mensagem:
        return 404
    conflitos = (
        "já confirmado", "já revertido", "já existe", "já foi enviado",
        "reprocesse o arquivo antes",
    )
    if any(marca in mensagem for marca in conflitos):
        return 409
    if "excede o limite" in mensagem or "grande demais" in mensagem:
        return 413
    return 400


def _resposta_dominio(resultado: Dict[str, Any]) -> Resposta:
    """Resultado de módulo de domínio → resposta HTTP (erro mapeado)."""
    if isinstance(resultado, dict) and resultado.get("erro"):
        return _status_de_erro(resultado), resultado, TIPO_JSON
    if isinstance(resultado, dict) and resultado.get("erro_duplicado"):
        return 409, resultado, TIPO_JSON
    return _ok(resultado)


def _id_ou_erro(texto: str, rotulo: str) -> Tuple[Optional[int], Optional[Resposta]]:
    """Id numérico do caminho; qualquer coisa que não seja inteiro → 400."""
    try:
        valor = int(str(texto).strip())
    except (TypeError, ValueError):
        return None, _erro(400, f"{rotulo} inválido: {texto!r} (esperava um inteiro)")
    if valor <= 0:
        return None, _erro(400, f"{rotulo} inválido: {valor} (esperava um inteiro positivo)")
    return valor, None


# --------------------------------------------------------------------------
# Upload: multipart (reusado de api/planilha.py) ou JSON base64
# --------------------------------------------------------------------------


def _extrai_upload(
    params: Dict[str, Any], corpo: bytes, cabecalhos: Dict[str, str]
) -> Tuple[Optional[bytes], str, Dict[str, str], Optional[Resposta]]:
    """Extrai ``(dados, nome, campos_texto)`` do corpo do upload.

    Aceita ``multipart/form-data`` (campo ``file`` + campos de texto) e
    ``application/json`` com ``file_base64``/``file_name``. Campos ausentes no
    corpo caem para a querystring. Erro → 4º elemento preenchido.
    """
    api_p = _api_planilha()
    campos: Dict[str, str] = {
        chave: _param(params, chave) for chave in _CAMPOS_UPLOAD if _param(params, chave)
    }
    tipo = cabecalhos.get("content-type", "")

    if tipo.strip().lower().startswith("multipart/"):
        boundary = api_p.extrair_boundary(tipo)
        if not boundary:
            return None, "", campos, _erro(
                400, "requisição multipart sem boundary no Content-Type"
            )
        partes = api_p.analisar_multipart(corpo, boundary)
        parte = api_p.parte_do_arquivo(partes)
        if parte is None or not parte.conteudo:
            return None, "", campos, _erro(400, MSG_SEM_ARQUIVO)
        for outra in partes:
            nome_campo = (outra.nome or "").strip().lower()
            if not outra.e_arquivo and nome_campo in _CAMPOS_UPLOAD:
                campos[nome_campo] = outra.conteudo.decode("utf-8", "replace").strip()
        nome = api_p.nome_seguro(parte.nome_arquivo) or "arquivo"
        return parte.conteudo, nome, campos, None

    dados_json, erro_json = _json_do_corpo(corpo)
    if dados_json is None:
        return None, "", campos, _erro(400, str(erro_json))
    bruto = dados_json.get("file_base64") or dados_json.get("fileBase64") or ""
    if not isinstance(bruto, str) or not bruto.strip():
        return None, "", campos, _erro(400, MSG_SEM_ARQUIVO)
    if "base64," in bruto[:200]:  # data URI
        bruto = bruto.split("base64,", 1)[1]
    compacto = "".join(bruto.split())
    if len(compacto) > (limite_upload_bytes() // 3 + 1) * 4 + 16:
        return None, "", campos, _erro(
            413, f"arquivo grande demais: o limite é de {limite_upload_mb()} MB"
        )
    try:
        dados = base64.b64decode(compacto, validate=True)
    except (binascii.Error, ValueError):
        return None, "", campos, _erro(400, "conteúdo base64 inválido")
    for chave in _CAMPOS_UPLOAD:
        valor = dados_json.get(chave)
        if isinstance(valor, (str, int)) and str(valor).strip():
            campos[chave] = str(valor).strip()
    nome_bruto = dados_json.get("file_name") or dados_json.get("fileName") or ""
    nome = api_p.nome_seguro(str(nome_bruto)) or "arquivo"
    return dados, nome, campos, None


def _disposicao_download(nome_original: str) -> str:
    """``Content-Disposition`` com nome saneado (sem diretórios/controle/aspas)."""
    api_p = _api_planilha()
    nome = api_p.nome_seguro(nome_original) or "arquivo.bin"
    nome = nome.replace('"', "").replace(";", "").strip() or "arquivo.bin"
    ascii_nome = nome.encode("ascii", "ignore").decode("ascii").strip() or "arquivo.bin"
    return f"attachment; filename=\"{ascii_nome}\"; filename*=UTF-8''{quote(nome)}"


# --------------------------------------------------------------------------
# Rotas: arquivos e lotes
# --------------------------------------------------------------------------


def _rotas_arquivos(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    if not resto:
        if metodo == "POST":
            return _upload_arquivo(db, params, corpo, cabecalhos)
        if metodo == "GET":
            return _listar_arquivos(db, params)
        return _erro(405, "use GET (lista) ou POST (upload) em /extratos/arquivos")

    arquivo_id, falha = _id_ou_erro(resto[0], "id de arquivo")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "GET":
        return _detalhe_arquivo(db, int(arquivo_id))
    if len(resto) == 2 and resto[1] == "original" and metodo == "GET":
        return _download_original(db, int(arquivo_id))
    if len(resto) == 2 and resto[1] == "reprocessar" and metodo == "POST":
        dados, erro_json = _json_do_corpo(corpo)
        if dados is None:
            return _erro(400, str(erro_json))
        senha = dados.get("senha_pdf") or _param(params, "senha_pdf") or None
        return _resposta_dominio(
            pipeline.reprocessar_arquivo(db, int(arquivo_id), senha_pdf=senha)
        )
    return _erro(404, "rota desconhecida em /extratos/arquivos")


def _upload_arquivo(
    db: sqlite3.Connection,
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    dados, nome, campos, falha = _extrai_upload(params, corpo, cabecalhos)
    if falha is not None:
        return falha
    assert dados is not None  # _extrai_upload garante
    if len(dados) > limite_upload_bytes():
        return _erro(
            413,
            f"arquivo grande demais: o limite é de {limite_upload_mb()} MB"
            " (ajuste CELESTIA_MAX_UPLOAD_MB no self-host, se preciso)",
        )
    tipo_documento = campos.get("tipo_documento") or "extrato_conta"
    if tipo_documento not in TIPOS_DOCUMENTO:
        return _erro(
            400,
            f"tipo_documento inválido: {tipo_documento!r}"
            f" (use {', '.join(TIPOS_DOCUMENTO)})",
        )
    resultado = pipeline.receber_arquivo(
        db,
        dados,
        nome,
        tipo_documento,
        instituicao=campos.get("instituicao", ""),
        conta=campos.get("conta", ""),
        cartao=campos.get("cartao", ""),
        moeda=campos.get("moeda") or "BRL",
        senha_pdf=campos.get("senha_pdf") or None,
        periodo=campos.get("periodo") or None,
    )
    return _resposta_dominio(resultado)


def _listar_arquivos(db: sqlite3.Connection, params: Dict[str, Any]) -> Resposta:
    pagina = max(1, _int_de(_param(params, "pagina", "1")) or 1)
    por_pagina = min(max(1, _int_de(_param(params, "por_pagina", "50")) or 50), 200)
    total = int(db.execute("SELECT COUNT(*) FROM arquivos").fetchone()[0])
    linhas = db.execute(
        """
        SELECT a.id, a.sha256, a.nome_original, a.mime_real, a.tamanho,
               a.tipo_documento, a.instituicao_id, a.conta_id, a.cartao_id,
               a.periodo_inicio, a.periodo_fim, a.enviado_em, a.status,
               a.versao_parser, a.erro,
               (SELECT COUNT(*) FROM lotes l WHERE l.arquivo_id = a.id) AS lotes,
               (SELECT MAX(l2.id) FROM lotes l2
                 WHERE l2.arquivo_id = a.id AND l2.revertido_em IS NULL) AS lote_ativo_id
        FROM arquivos a
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
        """,
        (por_pagina, (pagina - 1) * por_pagina),
    ).fetchall()
    return _ok(
        {
            "itens": [dict(linha) for linha in linhas],
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
        }
    )


def _detalhe_arquivo(db: sqlite3.Connection, arquivo_id: int) -> Resposta:
    arquivo = db.execute("SELECT * FROM arquivos WHERE id=?", (arquivo_id,)).fetchone()
    if arquivo is None:
        return _erro(404, f"arquivo {arquivo_id} não encontrado")
    item = dict(arquivo)
    item.pop("caminho_objeto", None)  # caminho interno do storage não é público
    lotes = db.execute(
        "SELECT id, criado_em, confirmado_em, revertido_em FROM lotes"
        " WHERE arquivo_id=? ORDER BY id DESC",
        (arquivo_id,),
    ).fetchall()
    item["lotes"] = [dict(lote) for lote in lotes]
    previa: Optional[Dict[str, Any]] = None
    ativo = db.execute(
        "SELECT resumo_json FROM lotes WHERE arquivo_id=? AND revertido_em IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (arquivo_id,),
    ).fetchone()
    if ativo is not None:
        try:
            previa = (json.loads(ativo["resumo_json"] or "{}") or {}).get("previa")
        except json.JSONDecodeError:
            previa = None
    item["previa"] = previa
    return _ok(item)


def _download_original(db: sqlite3.Connection, arquivo_id: int) -> Resposta:
    arquivo = db.execute(
        "SELECT nome_original, mime_real, caminho_objeto FROM arquivos WHERE id=?",
        (arquivo_id,),
    ).fetchone()
    if arquivo is None:
        return _erro(404, f"arquivo {arquivo_id} não encontrado")
    conteudo = storage.ler(arquivo["caminho_objeto"])
    if conteudo is None:
        return _erro(404, "original do arquivo não encontrado no storage")
    mime = str(arquivo["mime_real"] or "application/octet-stream")
    extras = {
        "Content-Disposition": _disposicao_download(str(arquivo["nome_original"] or "")),
        "X-Content-Type-Options": "nosniff",
    }
    return 200, bytes(conteudo), mime, extras


def _rotas_lotes(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    corpo: bytes,
) -> Resposta:
    if len(resto) != 2 or metodo != "POST":
        return _erro(404, "rota desconhecida em /extratos/lotes")
    lote_id, falha = _id_ou_erro(resto[0], "id de lote")
    if falha is not None:
        return falha
    acao = resto[1]
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    if acao == "confirmar":
        return _resposta_dominio(pipeline.confirmar_lote(db, int(lote_id)))
    if acao == "reverter":
        justificativa = str(dados.get("justificativa") or "")
        return _resposta_dominio(
            pipeline.reverter_lote(db, int(lote_id), justificativa)
        )
    if acao == "mapeamento":
        mapeamento = dados.get("mapeamento") if "mapeamento" in dados else dados
        if not isinstance(mapeamento, dict) or not mapeamento:
            return _erro(
                400,
                'mapeamento ausente — envie {"mapeamento": {"Coluna": "campo", ...}}',
            )
        limpo = {str(chave): str(valor) for chave, valor in mapeamento.items()}
        return _resposta_dominio(pipeline.aplicar_mapeamento(db, int(lote_id), limpo))
    return _erro(404, f"ação desconhecida em /extratos/lotes: {acao!r}")


# --------------------------------------------------------------------------
# Rotas: transações
# --------------------------------------------------------------------------


def _rotas_transacoes(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
) -> Resposta:
    if not resto:
        if metodo != "GET":
            return _erro(405, "use GET em /extratos/transacoes (PATCH é por id)")
        filtros = Filtros.from_params(params)
        resultado = consultas.listar_transacoes(
            db,
            filtros,
            pagina=_int_de(_param(params, "pagina", "1")) or 1,
            por_pagina=_int_de(_param(params, "por_pagina", "50")) or 50,
            ordenar=_param(params, "ordenar", "data_operacao"),
            dir=_param(params, "dir", "desc"),
        )
        resultado["totais"] = consultas.totais(db, filtros)
        return _ok(resultado)

    transacao_id, falha = _id_ou_erro(resto[0], "id de transação")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "PATCH":
        return _editar_transacao(db, int(transacao_id), corpo)
    if len(resto) == 2 and resto[1] == "dividir" and metodo == "POST":
        dados, erro_json = _json_do_corpo(corpo)
        if dados is None:
            return _erro(400, str(erro_json))
        partes = dados.get("partes")
        if not isinstance(partes, list) or not partes:
            return _erro(
                400,
                'partes ausentes — envie {"partes": [{"categoria_id": n,'
                ' "valor_centavos": n}, ...]}',
            )
        return _resposta_dominio(
            categorias_mod.dividir_transacao(db, int(transacao_id), partes)
        )
    return _erro(404, "rota desconhecida em /extratos/transacoes")


def _editar_transacao(
    db: sqlite3.Connection, transacao_id: int, corpo: bytes
) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    atual = db.execute(
        "SELECT id, tipo FROM transacoes WHERE id=?", (transacao_id,)
    ).fetchone()
    if atual is None:
        return _erro(404, f"transação {transacao_id} não encontrada")
    if "categoria_id" not in dados and "tipo" not in dados:
        return _erro(400, "nada para alterar — envie categoria_id e/ou tipo")
    justificativa = dados.get("justificativa")
    justificativa = str(justificativa) if justificativa is not None else None

    if "categoria_id" in dados:
        categoria_id = dados.get("categoria_id")
        if categoria_id is not None:
            categoria_id = _int_de(categoria_id)
            if categoria_id is None:
                return _erro(400, "categoria_id inválido (esperava inteiro ou null)")
        resultado = categorias_mod.definir_categoria(
            db, transacao_id, categoria_id, origem="manual", justificativa=justificativa
        )
        if resultado.get("erro"):
            return _status_de_erro(resultado), resultado, TIPO_JSON

    if "tipo" in dados:
        tipo = str(dados.get("tipo") or "")
        if tipo not in TIPOS_TRANSACAO:
            return _erro(
                400, f"tipo inválido: {tipo!r} (use {', '.join(TIPOS_TRANSACAO)})"
            )
        if tipo != atual["tipo"]:
            with db:
                db.execute(
                    "UPDATE transacoes SET tipo=? WHERE id=?", (tipo, transacao_id)
                )
                registra_auditoria(
                    db,
                    "transacao",
                    transacao_id,
                    "tipo_alterado",
                    campo="tipo",
                    valor_anterior=str(atual["tipo"]),
                    valor_novo=tipo,
                    origem="manual",
                    justificativa=justificativa,
                )

    linha = db.execute(
        "SELECT t.*, c.nome AS categoria_nome FROM transacoes t"
        " LEFT JOIN categorias c ON c.id = t.categoria_id WHERE t.id=?",
        (transacao_id,),
    ).fetchone()
    return _ok(dict(linha))


# --------------------------------------------------------------------------
# Rotas: categorias e regras
# --------------------------------------------------------------------------


def _rotas_categorias(
    db: sqlite3.Connection, metodo: str, resto: List[str], corpo: bytes
) -> Resposta:
    if not resto:
        if metodo == "GET":
            return _ok({"arvore": categorias_mod.arvore_categorias(db)})
        if metodo == "POST":
            return _criar_categoria(db, corpo)
        return _erro(405, "use GET, POST ou PATCH /extratos/categorias/{id}")
    categoria_id, falha = _id_ou_erro(resto[0], "id de categoria")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "PATCH":
        return _editar_categoria(db, int(categoria_id), corpo)
    return _erro(404, "rota desconhecida em /extratos/categorias")


def _valida_categoria(
    db: sqlite3.Connection,
    dados: Dict[str, Any],
    categoria_id: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Resposta]]:
    """Normaliza nome/pai_id/cor; devolve (mudanças, erro)."""
    mudancas: Dict[str, Any] = {}
    if "nome" in dados:
        nome = dados.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            return None, _erro(400, "nome da categoria é obrigatório (texto não vazio)")
        mudancas["nome"] = nome.strip()
    if "pai_id" in dados:
        pai_id = dados.get("pai_id")
        if pai_id is not None:
            pai_id = _int_de(pai_id)
            if pai_id is None:
                return None, _erro(400, "pai_id inválido (esperava inteiro ou null)")
            if categoria_id is not None and pai_id == categoria_id:
                return None, _erro(400, "uma categoria não pode ser pai de si mesma")
            existe = db.execute(
                "SELECT id FROM categorias WHERE id=?", (pai_id,)
            ).fetchone()
            if existe is None:
                return None, _erro(404, f"categoria pai {pai_id} não encontrada")
        mudancas["pai_id"] = pai_id
    if "cor" in dados:
        cor = dados.get("cor")
        mudancas["cor"] = str(cor).strip() if cor is not None else None
    return mudancas, None


def _criar_categoria(db: sqlite3.Connection, corpo: bytes) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    if "nome" not in dados:
        return _erro(400, "nome da categoria é obrigatório (texto não vazio)")
    mudancas, falha = _valida_categoria(db, dados)
    if falha is not None:
        return falha
    assert mudancas is not None
    nome = mudancas["nome"]
    pai_id = mudancas.get("pai_id")
    repetida = db.execute(
        "SELECT id FROM categorias WHERE nome=? AND pai_id IS ?", (nome, pai_id)
    ).fetchone()
    if repetida is not None:
        return _erro(409, f"categoria {nome!r} já existe nesse nível")
    with db:
        cursor = db.execute(
            "INSERT INTO categorias (nome, pai_id, cor) VALUES (?,?,?)",
            (nome, pai_id, mudancas.get("cor")),
        )
        categoria_id = int(cursor.lastrowid)
        registra_auditoria(
            db, "categoria", categoria_id, "categoria_criada",
            valor_novo=nome, origem="manual",
        )
    linha = db.execute("SELECT * FROM categorias WHERE id=?", (categoria_id,)).fetchone()
    return _ok(dict(linha))


def _editar_categoria(
    db: sqlite3.Connection, categoria_id: int, corpo: bytes
) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    atual = db.execute(
        "SELECT * FROM categorias WHERE id=?", (categoria_id,)
    ).fetchone()
    if atual is None:
        return _erro(404, f"categoria {categoria_id} não encontrada")
    mudancas, falha = _valida_categoria(db, dados, categoria_id=categoria_id)
    if falha is not None:
        return falha
    assert mudancas is not None
    if not mudancas:
        return _erro(400, "nada para alterar — envie nome, pai_id e/ou cor")
    nome = mudancas.get("nome", atual["nome"])
    pai_id = mudancas.get("pai_id", atual["pai_id"])
    repetida = db.execute(
        "SELECT id FROM categorias WHERE nome=? AND pai_id IS ? AND id != ?",
        (nome, pai_id, categoria_id),
    ).fetchone()
    if repetida is not None:
        return _erro(409, f"categoria {nome!r} já existe nesse nível")
    with db:
        for campo, valor in mudancas.items():
            if valor == atual[campo]:
                continue
            db.execute(  # campo vem da lista branca de _valida_categoria
                f"UPDATE categorias SET {campo}=? WHERE id=?", (valor, categoria_id)
            )
            registra_auditoria(
                db, "categoria", categoria_id, "categoria_editada",
                campo=campo,
                valor_anterior=None if atual[campo] is None else str(atual[campo]),
                valor_novo=None if valor is None else str(valor),
                origem="manual",
            )
    linha = db.execute("SELECT * FROM categorias WHERE id=?", (categoria_id,)).fetchone()
    return _ok(dict(linha))


def _rotas_regras(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
) -> Resposta:
    if not resto:
        if metodo == "GET":
            apenas_ativas = _verdadeiro(_param(params, "apenas_ativas"))
            return _ok({"itens": categorias_mod.listar_regras(db, apenas_ativas)})
        if metodo == "POST":
            dados, erro_json = _json_do_corpo(corpo)
            if dados is None:
                return _erro(400, str(erro_json))
            return _resposta_dominio(categorias_mod.criar_regra(db, dados))
        return _erro(405, "use GET, POST, PATCH/{id}, DELETE/{id} ou POST /aplicar")
    if resto[0] == "aplicar":
        if metodo != "POST" or len(resto) != 1:
            return _erro(404, "rota desconhecida em /extratos/regras")
        return _aplicar_regras(db, params, corpo)
    regra_id, falha = _id_ou_erro(resto[0], "id de regra")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "PATCH":
        dados, erro_json = _json_do_corpo(corpo)
        if dados is None:
            return _erro(400, str(erro_json))
        return _resposta_dominio(categorias_mod.editar_regra(db, int(regra_id), dados))
    if len(resto) == 1 and metodo == "DELETE":
        dados, erro_json = _json_do_corpo(corpo)
        justificativa = None if dados is None else dados.get("justificativa")
        return _resposta_dominio(
            categorias_mod.desativar_regra(
                db,
                int(regra_id),
                justificativa=str(justificativa) if justificativa else None,
            )
        )
    return _erro(404, "rota desconhecida em /extratos/regras")


def _aplicar_regras(
    db: sqlite3.Connection, params: Dict[str, Any], corpo: bytes
) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    confirmar = _verdadeiro(_param(params, "confirmar")) or _verdadeiro(
        dados.get("confirmar")
    )
    transacao_ids: Optional[List[int]] = None
    if dados.get("transacao_ids") is not None:
        brutos = dados["transacao_ids"]
        if not isinstance(brutos, list):
            return _erro(400, "transacao_ids deve ser uma lista de inteiros")
        transacao_ids = []
        for item in brutos:
            valor = _int_de(item)
            if valor is None:
                return _erro(400, f"transacao_ids contém valor inválido: {item!r}")
            transacao_ids.append(valor)
    return _resposta_dominio(
        categorias_mod.aplicar_regras(
            db, transacao_ids=transacao_ids, dry_run=not confirmar
        )
    )


# --------------------------------------------------------------------------
# Rotas: cartões e faturas
# --------------------------------------------------------------------------


def _valida_cartao(
    db: sqlite3.Connection, dados: Dict[str, Any], cartao_id: Optional[int] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[Resposta]]:
    """Normaliza e valida os campos editáveis de um cartão."""
    desconhecidos = [chave for chave in dados if chave not in _CAMPOS_CARTAO]
    if desconhecidos:
        return None, _erro(
            400, f"campos não editáveis de cartão: {', '.join(sorted(desconhecidos))}"
        )
    mudancas: Dict[str, Any] = {}
    if "nome" in dados:
        nome = dados.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            return None, _erro(400, "nome do cartão é obrigatório (texto não vazio)")
        nome = nome.strip()
        repetido = db.execute(
            "SELECT id FROM cartoes WHERE nome=? AND id IS NOT ?", (nome, cartao_id)
        ).fetchone()
        if repetido is not None:
            return None, _erro(409, f"já existe um cartão chamado {nome!r}")
        mudancas["nome"] = nome
    for campo in ("emissor", "bandeira"):
        if campo in dados:
            valor = dados.get(campo)
            mudancas[campo] = str(valor).strip() if valor is not None else None
    if "final" in dados:
        final = dados.get("final")
        if final is not None:
            final = str(final).strip()
            if not re.fullmatch(r"\d{4}", final):
                return None, _erro(
                    400,
                    "final do cartão deve ter exatamente 4 dígitos"
                    " (nunca o número completo)",
                )
        mudancas["final"] = final
    for campo in ("dia_fechamento", "dia_vencimento"):
        if campo in dados:
            valor = dados.get(campo)
            if valor is not None:
                valor = _int_de(valor)
                if valor is None or not 1 <= valor <= 31:
                    return None, _erro(
                        400, f"{campo} inválido (esperava inteiro entre 1 e 31, ou null)"
                    )
            mudancas[campo] = valor
    if "limite_total_centavos" in dados:
        valor = dados.get("limite_total_centavos")
        if valor is not None:
            if isinstance(valor, float):
                return None, _erro(
                    400,
                    "limite_total_centavos deve ser inteiro em centavos"
                    " — float é proibido em dinheiro",
                )
            valor = _int_de(valor)
            if valor is None or valor < 0:
                return None, _erro(
                    400, "limite_total_centavos inválido (inteiro >= 0 em centavos, ou null)"
                )
        mudancas["limite_total_centavos"] = valor
    for campo, tabela in (("conta_pagadora_id", "contas"), ("instituicao_id", "instituicoes")):
        if campo in dados:
            valor = dados.get(campo)
            if valor is not None:
                valor = _int_de(valor)
                if valor is None:
                    return None, _erro(400, f"{campo} inválido (esperava inteiro ou null)")
                existe = db.execute(
                    f"SELECT id FROM {tabela} WHERE id=?", (valor,)
                ).fetchone()
                if existe is None:
                    rotulo = "conta pagadora" if campo == "conta_pagadora_id" else "instituição"
                    return None, _erro(404, f"{rotulo} {valor} não encontrada")
            mudancas[campo] = valor
    return mudancas, None


def _rotas_cartoes(
    db: sqlite3.Connection, metodo: str, resto: List[str], corpo: bytes
) -> Resposta:
    if not resto:
        if metodo == "GET":
            linhas = db.execute(
                """
                SELECT c.*, i.nome AS instituicao_nome, cp.nome AS conta_pagadora_nome,
                       (SELECT COUNT(*) FROM faturas f WHERE f.cartao_id = c.id) AS faturas
                FROM cartoes c
                LEFT JOIN instituicoes i ON i.id = c.instituicao_id
                LEFT JOIN contas cp ON cp.id = c.conta_pagadora_id
                ORDER BY c.nome COLLATE NOCASE
                """
            ).fetchall()
            return _ok({"itens": [dict(linha) for linha in linhas]})
        if metodo == "POST":
            return _criar_cartao(db, corpo)
        return _erro(405, "use GET, POST ou PATCH /extratos/cartoes/{id}")
    cartao_id, falha = _id_ou_erro(resto[0], "id de cartão")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "PATCH":
        return _editar_cartao(db, int(cartao_id), corpo)
    return _erro(404, "rota desconhecida em /extratos/cartoes")


def _criar_cartao(db: sqlite3.Connection, corpo: bytes) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    if "nome" not in dados:
        return _erro(400, "nome do cartão é obrigatório (texto não vazio)")
    mudancas, falha = _valida_cartao(db, dados)
    if falha is not None:
        return falha
    assert mudancas is not None
    with db:
        cursor = db.execute(
            "INSERT INTO cartoes (nome, emissor, bandeira, final, conta_pagadora_id,"
            " instituicao_id, dia_fechamento, dia_vencimento, limite_total_centavos)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                mudancas["nome"],
                mudancas.get("emissor"),
                mudancas.get("bandeira"),
                mudancas.get("final"),
                mudancas.get("conta_pagadora_id"),
                mudancas.get("instituicao_id"),
                mudancas.get("dia_fechamento"),
                mudancas.get("dia_vencimento"),
                mudancas.get("limite_total_centavos"),
            ),
        )
        cartao_id = int(cursor.lastrowid)
        registra_auditoria(
            db, "cartao", cartao_id, "cartao_criado",
            valor_novo=mudancas["nome"], origem="manual",
        )
    linha = db.execute("SELECT * FROM cartoes WHERE id=?", (cartao_id,)).fetchone()
    return _ok(dict(linha))


def _editar_cartao(db: sqlite3.Connection, cartao_id: int, corpo: bytes) -> Resposta:
    dados, erro_json = _json_do_corpo(corpo)
    if dados is None:
        return _erro(400, str(erro_json))
    atual = db.execute("SELECT * FROM cartoes WHERE id=?", (cartao_id,)).fetchone()
    if atual is None:
        return _erro(404, f"cartão {cartao_id} não encontrado")
    mudancas, falha = _valida_cartao(db, dados, cartao_id=cartao_id)
    if falha is not None:
        return falha
    assert mudancas is not None
    if not mudancas:
        return _erro(
            400,
            "nada para alterar — envie emissor, bandeira, final, limites, dias"
            " e/ou conta_pagadora_id",
        )
    with db:
        for campo, valor in mudancas.items():
            if valor == atual[campo]:
                continue
            db.execute(  # campo vem da lista branca _CAMPOS_CARTAO
                f"UPDATE cartoes SET {campo}=? WHERE id=?", (valor, cartao_id)
            )
            registra_auditoria(
                db, "cartao", cartao_id, "cartao_editado",
                campo=campo,
                valor_anterior=None if atual[campo] is None else str(atual[campo]),
                valor_novo=None if valor is None else str(valor),
                origem="manual",
            )
    linha = db.execute("SELECT * FROM cartoes WHERE id=?", (cartao_id,)).fetchone()
    return _ok(dict(linha))


def _rotas_faturas(db: sqlite3.Connection, params: Dict[str, Any]) -> Resposta:
    sql = (
        "SELECT f.*, c.nome AS cartao_nome FROM faturas f"
        " JOIN cartoes c ON c.id = f.cartao_id WHERE 1=1"
    )
    argumentos: List[Any] = []
    cartao_bruto = _param(params, "cartao_id")
    if cartao_bruto:
        cartao_id = _int_de(cartao_bruto)
        if cartao_id is None:
            return _erro(400, f"cartao_id inválido: {cartao_bruto!r}")
        sql += " AND f.cartao_id = ?"
        argumentos.append(cartao_id)
    status = _param(params, "status")
    if status:
        sql += " AND f.status = ?"
        argumentos.append(status)
    competencia = _param(params, "competencia")
    if competencia:
        sql += " AND f.competencia = ?"
        argumentos.append(competencia)
    sql += " ORDER BY f.vence_em DESC, f.id DESC"
    linhas = db.execute(sql, argumentos).fetchall()
    return _ok({"itens": [dict(linha) for linha in linhas]})


# --------------------------------------------------------------------------
# Rotas: conciliações, duplicidades, fluxo, painel, config
# --------------------------------------------------------------------------


def _rotas_conciliacoes(
    db: sqlite3.Connection, metodo: str, resto: List[str], corpo: bytes
) -> Resposta:
    if not resto:
        if metodo != "POST":
            return _erro(
                405,
                "use POST (criar), GET /sugerir ou DELETE /extratos/conciliacoes/{id}",
            )
        dados, erro_json = _json_do_corpo(corpo)
        if dados is None:
            return _erro(400, str(erro_json))
        itens = dados.get("itens")
        if not isinstance(itens, list) or not itens:
            return _erro(
                400,
                'itens ausentes — envie {"itens": [{"transacao_id"|"fatura_id",'
                ' "papel": "pagamento"|"obrigacao"}, ...]}',
            )
        observacao = str(dados.get("observacao") or "")
        return _resposta_dominio(conciliacao.conciliar(db, itens, observacao))
    if resto == ["sugerir"] and metodo == "GET":
        return _ok({"sugestoes": conciliacao.sugerir_conciliacoes(db)})
    conciliacao_id, falha = _id_ou_erro(resto[0], "id de conciliação")
    if falha is not None:
        return falha
    if len(resto) == 1 and metodo == "DELETE":
        return _resposta_dominio(
            conciliacao.desfazer_conciliacao(db, int(conciliacao_id))
        )
    return _erro(404, "rota desconhecida em /extratos/conciliacoes")


def _rotas_duplicidades(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
) -> Resposta:
    if not resto:
        if metodo != "GET":
            return _erro(405, "use GET ou POST /extratos/duplicidades/{id}/resolver")
        sql = (
            "SELECT d.id, d.transacao_id, d.candidata_fingerprint, d.motivo,"
            " d.resolucao, d.resolvida_em, t.data_operacao, t.valor_centavos,"
            " t.moeda, t.descricao_original, t.conta_id, t.cartao_id"
            " FROM duplicidades d LEFT JOIN transacoes t ON t.id = d.transacao_id"
        )
        argumentos: List[Any] = []
        if _verdadeiro(_param(params, "pendentes")):
            sql += " WHERE d.resolucao IS NULL"
        sql += " ORDER BY (d.resolucao IS NOT NULL), d.id DESC"
        linhas = db.execute(sql, argumentos).fetchall()
        return _ok({"itens": [dict(linha) for linha in linhas]})
    duplicidade_id, falha = _id_ou_erro(resto[0], "id de duplicidade")
    if falha is not None:
        return falha
    if len(resto) == 2 and resto[1] == "resolver" and metodo == "POST":
        dados, erro_json = _json_do_corpo(corpo)
        if dados is None:
            return _erro(400, str(erro_json))
        resolucao = dados.get("resolucao")
        if not isinstance(resolucao, str) or not resolucao.strip():
            return _erro(
                400,
                'resolucao é obrigatória — envie {"resolucao": "manter" | "ignorar"'
                ' | texto livre}',
            )
        atual = db.execute(
            "SELECT * FROM duplicidades WHERE id=?", (duplicidade_id,)
        ).fetchone()
        if atual is None:
            return _erro(404, f"duplicidade {duplicidade_id} não encontrada")
        if atual["resolucao"]:
            return _erro(409, f"duplicidade {duplicidade_id} já resolvida")
        justificativa = dados.get("justificativa")
        with db:
            db.execute(
                "UPDATE duplicidades SET resolucao=?, resolvida_em=datetime('now')"
                " WHERE id=?",
                (resolucao.strip(), duplicidade_id),
            )
            registra_auditoria(
                db, "duplicidade", int(duplicidade_id), "duplicidade_resolvida",
                campo="resolucao", valor_anterior=None, valor_novo=resolucao.strip(),
                origem="manual",
                justificativa=str(justificativa) if justificativa else None,
            )
        linha = db.execute(
            "SELECT * FROM duplicidades WHERE id=?", (duplicidade_id,)
        ).fetchone()
        return _ok(dict(linha))
    return _erro(404, "rota desconhecida em /extratos/duplicidades")


def _rotas_config(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    if metodo != "POST" or not resto or resto[0] != "planilha":
        return _erro(404, "rota desconhecida em /extratos/config")
    dados, nome, campos, falha = _extrai_upload(params, corpo, cabecalhos)
    if falha is not None:
        return falha
    assert dados is not None
    if len(dados) > limite_upload_bytes():
        return _erro(413, f"arquivo grande demais: o limite é de {limite_upload_mb()} MB")
    if resto == ["planilha"]:
        return _ok(config_planilha.previa_config(db, dados, nome))
    if resto == ["planilha", "confirmar"]:
        return _ok(
            config_planilha.confirmar_config(
                db,
                dados,
                nome,
                aprovado_por=campos.get("aprovado_por") or "usuario",
                sobrescrever=_verdadeiro(campos.get("sobrescrever")),
            )
        )
    return _erro(404, "rota desconhecida em /extratos/config")


# --------------------------------------------------------------------------
# Roteador
# --------------------------------------------------------------------------


def _despachar(
    db: sqlite3.Connection,
    metodo: str,
    resto: List[str],
    params: Dict[str, Any],
    corpo: bytes,
    cabecalhos: Dict[str, str],
) -> Resposta:
    if not resto:
        if metodo == "GET":
            return _ok(
                {
                    "status": "ok",
                    "modulo": "extratos",
                    "limite_upload_mb": limite_upload_mb(),
                    "recursos": [
                        "arquivos", "lotes", "transacoes", "categorias", "regras",
                        "cartoes", "faturas", "conciliacoes", "fluxo", "painel",
                        "duplicidades", "config/planilha",
                    ],
                }
            )
        return _erro(404, "rota desconhecida em /extratos")
    recurso, sobra = resto[0], resto[1:]
    if recurso == "arquivos":
        return _rotas_arquivos(db, metodo, sobra, params, corpo, cabecalhos)
    if recurso == "lotes":
        return _rotas_lotes(db, metodo, sobra, corpo)
    if recurso == "transacoes":
        return _rotas_transacoes(db, metodo, sobra, params, corpo)
    if recurso == "categorias":
        return _rotas_categorias(db, metodo, sobra, corpo)
    if recurso == "regras":
        return _rotas_regras(db, metodo, sobra, params, corpo)
    if recurso == "cartoes":
        return _rotas_cartoes(db, metodo, sobra, corpo)
    if recurso == "faturas":
        if metodo == "GET" and not sobra:
            return _rotas_faturas(db, params)
        return _erro(404, "rota desconhecida em /extratos/faturas (use GET)")
    if recurso == "conciliacoes":
        return _rotas_conciliacoes(db, metodo, sobra, corpo)
    if recurso == "fluxo":
        if metodo == "GET" and not sobra:
            visao = _param(params, "visao", "realizado")
            if visao not in fluxo_mod.VISOES:
                return _erro(
                    400,
                    f"visão inválida: {visao!r} (use {', '.join(fluxo_mod.VISOES)})",
                )
            return _ok(
                fluxo_mod.fluxo(
                    db,
                    visao,
                    Filtros.from_params(params),
                    granularidade=_param(params, "granularidade", "mensal"),
                )
            )
        return _erro(404, "rota desconhecida em /extratos/fluxo (use GET ?visao=...)")
    if recurso == "painel":
        if metodo == "GET" and not sobra:
            return _ok(painel_mod.painel(db, Filtros.from_params(params)))
        return _erro(404, "rota desconhecida em /extratos/painel (use GET)")
    if recurso == "duplicidades":
        return _rotas_duplicidades(db, metodo, sobra, params, corpo)
    if recurso == "config":
        return _rotas_config(db, metodo, sobra, params, corpo, cabecalhos)
    return _erro(404, f"rota desconhecida: /extratos/{recurso}")


def tratar(
    metodo: str,
    caminho: str,
    params: Optional[Dict[str, Any]],
    corpo: bytes,
    cabecalhos: Optional[Dict[str, str]],
) -> Resposta:
    """Ponto único de entrada da API de extratos (ver docstring do módulo).

    ``params`` aceita valores simples ou listas (``parse_qs``); ``cabecalhos``
    é case-insensitive. Nunca levanta exceção: qualquer falha interna vira
    ``500 {"erro": ...}`` sem vazar conteúdo do usuário.
    """
    metodo = (metodo or "").strip().upper()
    params = params or {}
    corpo = corpo or b""
    cabecalhos_baixos = {
        str(chave).lower(): str(valor) for chave, valor in (cabecalhos or {}).items()
    }

    try:
        caminho_limpo = urlsplit(str(caminho or "/")).path or "/"
    except ValueError:
        return _erro(400, "caminho de requisição inválido")
    for prefixo in ("/api/py", "/api"):
        if caminho_limpo.startswith(prefixo + "/extratos"):
            caminho_limpo = caminho_limpo[len(prefixo):]
            break
    segmentos = [parte for parte in caminho_limpo.split("/") if parte]
    if not segmentos or segmentos[0] != "extratos":
        return _erro(404, f"rota desconhecida: {caminho_limpo}")

    if not _token_ok(cabecalhos_baixos):
        return _erro(401, MSG_NAO_AUTORIZADO)
    if metodo not in ("GET", "POST", "PATCH", "DELETE"):
        return _erro(405, f"método {metodo or '?'} não suportado")
    if len(corpo) > limite_corpo_bytes():
        return _erro(
            413,
            f"corpo da requisição grande demais — o limite de upload é de"
            f" {limite_upload_mb()} MB",
        )
    if metodo in ("POST", "PATCH", "DELETE") and _serverless_sem_duravel():
        return 503, {"erro": MSG_SEM_STORAGE}, TIPO_JSON

    try:
        with store.conexao() as db:
            return _despachar(
                db, metodo, segmentos[1:], params, corpo, cabecalhos_baixos
            )
    except Exception:  # noqa: BLE001 - a API nunca estoura para o cliente
        # Sem detalhes na resposta e sem log: nada de descrição de transação
        # nem corpo de upload sai daqui.
        return _erro(500, MSG_INESPERADO)
