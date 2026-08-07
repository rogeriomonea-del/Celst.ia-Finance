"""Pipeline de importação de extratos — do upload à confirmação atômica.

Fluxo: ``receber_arquivo`` valida (tamanho, magic bytes, sha256 duplicado),
grava o original imutável, cria o lote com as transações *staged* (somente no
``resumo_json`` — nada em ``transacoes`` ainda) e devolve a prévia.
``confirmar_lote`` grava tudo numa transação SQLite única, com dedup em três
camadas (sha256 → id_banco → fingerprint) e fila de ``duplicidades`` para
casos parecidos. ``reverter_lote`` desfaz as transações preservando arquivo,
linhas brutas e auditoria; ``reprocessar_arquivo`` roda o fluxo de novo sobre
o original guardado.

Regras herdadas do contrato: dinheiro sempre em centavos ``int``; conteúdo
ruim nunca vira exceção não tratada — vira erro por linha na prévia ou um
``dict`` com ``erro`` claro.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..workbook import as_date
from . import storage
from .detect import detectar
from .dinheiro import formata_centavos, moeda_valida
from .modelos import (
    PADRAO_PAGAMENTO_FATURA,
    PADRAO_TRANSFERENCIA,
    TIPOS_DOCUMENTO,
    ResultadoParse,
    Transacao,
)
from .parsers import base
from .store import registra_auditoria

#: Quantas transações staged entram na amostra da prévia.
TAMANHO_AMOSTRA = 10


# --------------------------------------------------------------------------
# Utilitários
# --------------------------------------------------------------------------

def _agora() -> str:
    """Timestamp UTC no mesmo formato do ``datetime('now')`` do SQLite."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _limite_upload_bytes() -> int:
    """Limite de upload em bytes (``CELESTIA_MAX_UPLOAD_MB``, padrão 15)."""
    try:
        megabytes = int(os.environ.get("CELESTIA_MAX_UPLOAD_MB", "15"))
    except ValueError:
        megabytes = 15
    return max(1, megabytes) * 1024 * 1024


def _parse_periodo(periodo: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Interpreta o período informado no upload: ``AAAA-MM``, data única ou
    ``inicio..fim``. Devolve datas ISO (ou ``None`` quando não entendeu)."""
    if not periodo:
        return None, None
    texto = str(periodo).strip()
    if ".." in texto:
        bruto_inicio, bruto_fim = texto.split("..", 1)
        inicio, fim = as_date(bruto_inicio.strip()), as_date(bruto_fim.strip())
        return (inicio.isoformat() if inicio else None), (fim.isoformat() if fim else None)
    competencia = re.fullmatch(r"(\d{4})-(\d{2})", texto)
    if competencia:
        ano, mes = int(competencia.group(1)), int(competencia.group(2))
        if 1 <= mes <= 12:
            ultimo = monthrange(ano, mes)[1]
            return date(ano, mes, 1).isoformat(), date(ano, mes, ultimo).isoformat()
        return None, None
    unica = as_date(texto)
    if unica is not None:
        return unica.isoformat(), unica.isoformat()
    return None, None


def _soma_meses(ano: int, mes: int, delta: int) -> Tuple[int, int]:
    total = ano * 12 + (mes - 1) + delta
    return total // 12, total % 12 + 1


def _dia_no_mes(ano: int, mes: int, dia: int) -> date:
    """Data com o dia limitado ao tamanho do mês (dia 31 em abril → 30)."""
    return date(ano, mes, min(max(1, dia), monthrange(ano, mes)[1]))


# --------------------------------------------------------------------------
# Cadastro: obter-ou-criar instituição / conta / cartão / fatura
# --------------------------------------------------------------------------

def _obter_ou_criar_instituicao(
    db: sqlite3.Connection, nome: str, tipo: str = "banco"
) -> Optional[int]:
    nome = (nome or "").strip()
    if not nome:
        return None
    linha = db.execute("SELECT id FROM instituicoes WHERE nome = ?", (nome,)).fetchone()
    if linha is not None:
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO instituicoes (nome, tipo) VALUES (?, ?)", (nome, tipo)
    )
    return int(cursor.lastrowid)


def _obter_ou_criar_conta(
    db: sqlite3.Connection, nome: str, instituicao_id: Optional[int], moeda: str
) -> int:
    nome = (nome or "").strip() or "Conta importada"
    linha = db.execute(
        "SELECT id FROM contas WHERE nome = ? AND instituicao_id IS ?",
        (nome, instituicao_id),
    ).fetchone()
    if linha is not None:
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO contas (instituicao_id, nome, moeda) VALUES (?, ?, ?)",
        (instituicao_id, nome, moeda),
    )
    return int(cursor.lastrowid)


def _obter_ou_criar_cartao(
    db: sqlite3.Connection, nome: str, instituicao_id: Optional[int], moeda: str
) -> int:
    nome = (nome or "").strip() or "Cartão importado"
    linha = db.execute("SELECT id FROM cartoes WHERE nome = ?", (nome,)).fetchone()
    if linha is not None:
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO cartoes (instituicao_id, nome, moeda) VALUES (?, ?, ?)",
        (instituicao_id, nome, moeda),
    )
    return int(cursor.lastrowid)


def _fatura_do_ciclo(
    db: sqlite3.Connection, cartao: sqlite3.Row, data_compra: date
) -> Optional[int]:
    """Fatura (criando se preciso) do ciclo em que ``data_compra`` cai.

    Compra depois do fechamento entra no ciclo seguinte. O vencimento usa
    ``dia_vencimento`` (no mesmo mês do fechamento quando cai depois dele;
    senão no mês seguinte). Sem ``dia_fechamento`` não há ciclo definível —
    quem chama deixa ``fatura_id`` nulo com aviso.
    """
    dia_fechamento = cartao["dia_fechamento"]
    if not dia_fechamento:
        return None
    fecha_em = _dia_no_mes(data_compra.year, data_compra.month, int(dia_fechamento))
    if data_compra > fecha_em:  # depois do fechamento → ciclo seguinte
        ano, mes = _soma_meses(fecha_em.year, fecha_em.month, 1)
        fecha_em = _dia_no_mes(ano, mes, int(dia_fechamento))
    dia_vencimento = int(cartao["dia_vencimento"] or dia_fechamento)
    if dia_vencimento > int(dia_fechamento):
        vence_em = _dia_no_mes(fecha_em.year, fecha_em.month, dia_vencimento)
    else:
        ano, mes = _soma_meses(fecha_em.year, fecha_em.month, 1)
        vence_em = _dia_no_mes(ano, mes, dia_vencimento)
    linha = db.execute(
        "SELECT id FROM faturas WHERE cartao_id = ? AND vence_em = ?",
        (cartao["id"], vence_em.isoformat()),
    ).fetchone()
    if linha is not None:
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO faturas (cartao_id, competencia, fecha_em, vence_em, origem)"
        " VALUES (?, ?, ?, ?, 'extrato')",
        (cartao["id"], vence_em.strftime("%Y-%m"), fecha_em.isoformat(), vence_em.isoformat()),
    )
    return int(cursor.lastrowid)


# --------------------------------------------------------------------------
# Tipagem automática e staging
# --------------------------------------------------------------------------

#: Tipos-placeholder que parsers baseados em SINAL (csv/xlsx) atribuem antes
#: da tipagem do pipeline. Tipos fora desta lista (compra, estorno, tarifa,
#: juros, transferência…) foram classificados pelo próprio adaptador — o OFX,
#: por exemplo, já entrega direção/tipo na convenção bancária normalizada — e
#: por isso NÃO passam pela inversão de convenção de cartão.
_TIPOS_PLACEHOLDER = ("", "receita", "despesa")


def _tipa_transacao(transacao: Transacao, eh_cartao: bool) -> None:
    """Tipagem determinística de uma transação candidata.

    Em fatura de cartão a convenção do documento (parsers por sinal: csv/xlsx)
    é a inversa da do extrato: valor positivo = compra (débito do titular na
    visão competência) e valor negativo = estorno/crédito — por isso a direção
    é invertida antes da classificação (regra do SPEC: "estorno por valor
    negativo em cartão"). Adaptadores que já normalizam a convenção (OFX marca
    compra=débito e estorno=crédito no próprio parse) chegam com tipo definido
    e são preservados — inverter de novo trocaria compra por estorno.
    """
    ja_classificada = transacao.tipo not in _TIPOS_PLACEHOLDER
    if eh_cartao and not ja_classificada:
        transacao.direcao = "debito" if transacao.direcao == "credito" else "credito"
    descricao = transacao.descricao_original or transacao.descricao_normalizada
    if PADRAO_PAGAMENTO_FATURA.search(descricao):
        transacao.tipo = "pagamento_fatura"
    elif PADRAO_TRANSFERENCIA.search(descricao):
        transacao.tipo = "transferencia"
    elif ja_classificada:
        pass  # o adaptador já deu o tipo definitivo (compra/estorno/tarifa/juros)
    elif eh_cartao:
        transacao.tipo = "compra" if transacao.direcao == "debito" else "estorno"
    else:
        transacao.tipo = "receita" if transacao.direcao == "credito" else "despesa"


def _transacao_para_dict(transacao: Transacao) -> Dict[str, Any]:
    """Serialização estável de uma transação staged para o ``resumo_json``."""
    return {
        "data_operacao": transacao.data_operacao.isoformat(),
        "data_lancamento": (
            transacao.data_lancamento.isoformat() if transacao.data_lancamento else None
        ),
        "data_compensacao": (
            transacao.data_compensacao.isoformat() if transacao.data_compensacao else None
        ),
        "valor_centavos": transacao.valor_centavos,
        "moeda": transacao.moeda,
        "direcao": transacao.direcao,
        "tipo": transacao.tipo,
        "descricao_original": transacao.descricao_original,
        "descricao_normalizada": transacao.descricao_normalizada,
        "contraparte": transacao.contraparte,
        "id_banco": transacao.id_banco,
        "documento": transacao.documento,
        "saldo_apos_centavos": transacao.saldo_apos_centavos,
        "parcela_num": transacao.parcela_num,
        "parcela_total": transacao.parcela_total,
        "fingerprint": transacao.fingerprint,
        "origem_ref": transacao.origem_ref,
        "conta_id": transacao.conta_id,
        "cartao_id": transacao.cartao_id,
    }


# --------------------------------------------------------------------------
# Dedup contra o banco
# --------------------------------------------------------------------------

def _existe_id_banco(
    db: sqlite3.Connection, conta_id: Optional[int], cartao_id: Optional[int], id_banco: str
) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM transacoes WHERE coalesce(conta_id,0)=? AND"
            " coalesce(cartao_id,0)=? AND id_banco=? LIMIT 1",
            (conta_id or 0, cartao_id or 0, id_banco),
        ).fetchone()
        is not None
    )


def _existe_fingerprint(
    db: sqlite3.Connection, conta_id: Optional[int], cartao_id: Optional[int], fingerprint: str
) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM transacoes WHERE coalesce(conta_id,0)=? AND"
            " coalesce(cartao_id,0)=? AND fingerprint=? LIMIT 1",
            (conta_id or 0, cartao_id or 0, fingerprint),
        ).fetchone()
        is not None
    )


def _parecida_com_id_diferente(
    db: sqlite3.Connection, staged: Dict[str, Any]
) -> Optional[str]:
    """Fingerprint de transação já gravada MUITO parecida com a candidata.

    Parecida = mesma data + valor + conta/cartão + descrição normalizada, mas
    ``id_banco`` diferente (ambos presentes). A candidata será inserida mesmo
    assim e entra na fila ``duplicidades`` para revisão humana — nunca se
    apaga nada automaticamente.
    """
    if not staged.get("id_banco"):
        return None
    linhas = db.execute(
        "SELECT fingerprint, id_banco FROM transacoes WHERE coalesce(conta_id,0)=?"
        " AND coalesce(cartao_id,0)=? AND data_operacao=? AND valor_centavos=?"
        " AND descricao_normalizada=?",
        (
            staged.get("conta_id") or 0,
            staged.get("cartao_id") or 0,
            staged["data_operacao"],
            staged["valor_centavos"],
            staged["descricao_normalizada"],
        ),
    ).fetchall()
    for linha in linhas:
        if linha["id_banco"] and linha["id_banco"] != staged["id_banco"]:
            return str(linha["fingerprint"])
    return None


# --------------------------------------------------------------------------
# Prévia
# --------------------------------------------------------------------------

def _valida_saldos(
    resultado: ResultadoParse, staged: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Equação contábil ``saldo_inicial + créditos − débitos = saldo_final``.

    Só roda quando o documento declara os dois saldos; divergência vira item
    visível na prévia (nunca bloqueia sozinha — o usuário decide).
    """
    inicial = next(
        (s for s in resultado.saldos if "inicial" in s.rotulo or "anterior" in s.rotulo),
        None,
    )
    final = next((s for s in resultado.saldos if "final" in s.rotulo), None)
    if inicial is None or final is None:
        return []
    creditos = sum(t["valor_centavos"] for t in staged if t["direcao"] == "credito")
    debitos = sum(t["valor_centavos"] for t in staged if t["direcao"] == "debito")
    esperado = inicial.valor_centavos + creditos - debitos
    if esperado == final.valor_centavos:
        return []
    diferenca = final.valor_centavos - esperado
    return [
        {
            "tipo": "saldo_nao_fecha",
            "esperado_centavos": esperado,
            "informado_centavos": final.valor_centavos,
            "diferenca_centavos": diferenca,
            "mensagem": (
                f"saldo final informado ({formata_centavos(final.valor_centavos)}) difere do"
                f" calculado ({formata_centavos(esperado)}) por {formata_centavos(diferenca)}"
            ),
        }
    ]


def _erros_graves(resultado: ResultadoParse) -> bool:
    """Sem nenhuma transação, ou tantos erros quanto transações → revisar."""
    if not resultado.transacoes:
        return True
    return len(resultado.erros) >= len(resultado.transacoes)


def _monta_previa(
    db: sqlite3.Connection,
    arquivo_id: int,
    lote_id: Optional[int],
    status: str,
    resultado: ResultadoParse,
    staged: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Prévia no formato do SPEC — tudo que a UI mostra antes da confirmação."""
    creditos = [t for t in staged if t["direcao"] == "credito"]
    debitos = [t for t in staged if t["direcao"] == "debito"]

    duplicidades: List[Dict[str, Any]] = []
    for t in staged:
        motivo: Optional[str] = None
        if t.get("id_banco") and _existe_id_banco(
            db, t.get("conta_id"), t.get("cartao_id"), t["id_banco"]
        ):
            motivo = "id_banco já importado — será pulada na confirmação"
        elif _existe_fingerprint(db, t.get("conta_id"), t.get("cartao_id"), t["fingerprint"]):
            motivo = "fingerprint já importada — será pulada na confirmação"
        if motivo:
            duplicidades.append(
                {
                    "origem_ref": t["origem_ref"],
                    "data_operacao": t["data_operacao"],
                    "valor_centavos": t["valor_centavos"],
                    "descricao": t["descricao_original"],
                    "motivo": motivo,
                }
            )

    baixa_confianca = [
        erro for erro in resultado.erros if erro.get("campo") in ("data", "valor")
    ]
    campos_ausentes: List[str] = []
    if staged:
        for campo, rotulo in (
            ("descricao_normalizada", "descricao"),
            ("documento", "documento"),
            ("id_banco", "id_banco"),
            ("saldo_apos_centavos", "saldo"),
        ):
            if all(not t.get(campo) for t in staged):
                campos_ausentes.append(rotulo)

    return {
        "lote_id": lote_id,
        "arquivo_id": arquivo_id,
        "status": status,
        "adaptador": resultado.adaptador,
        "periodo": {
            "inicio": resultado.periodo_inicio.isoformat() if resultado.periodo_inicio else None,
            "fim": resultado.periodo_fim.isoformat() if resultado.periodo_fim else None,
        },
        "saldos": [
            {"data": s.data.isoformat(), "rotulo": s.rotulo, "valor_centavos": s.valor_centavos}
            for s in resultado.saldos
        ],
        "contagens": {
            "linhas": len(resultado.linhas_brutas),
            "transacoes": len(staged),
            "creditos": len(creditos),
            "debitos": len(debitos),
        },
        "totais": {
            "entradas_centavos": sum(t["valor_centavos"] for t in creditos),
            "saidas_centavos": sum(t["valor_centavos"] for t in debitos),
            "compras_cartao_centavos": sum(
                t["valor_centavos"] for t in staged if t["tipo"] == "compra"
            ),
        },
        "duplicidades": duplicidades,
        "baixa_confianca": baixa_confianca,
        "campos_ausentes": campos_ausentes,
        "divergencias": _valida_saldos(resultado, staged),
        "erros": list(resultado.erros),
        "avisos": list(resultado.avisos),
        "amostra": staged[:TAMANHO_AMOSTRA],
        "mapeamento_necessario": resultado.mapeamento_necessario,
    }


# --------------------------------------------------------------------------
# Núcleo: processar um arquivo já gravado → lote staged + prévia
# --------------------------------------------------------------------------

def _falha(db: sqlite3.Connection, arquivo_id: int, mensagem: str) -> Dict[str, Any]:
    """Marca o arquivo como ``falhou`` (com erro claro) e devolve a resposta."""
    with db:
        db.execute(
            "UPDATE arquivos SET status='falhou', erro=? WHERE id=?", (mensagem, arquivo_id)
        )
    return {
        "lote_id": None,
        "arquivo_id": arquivo_id,
        "status": "falhou",
        "erro": mensagem,
        "erros": [{"origem_ref": "arquivo", "mensagem": mensagem}],
        "avisos": [],
        "mapeamento_necessario": None,
    }


def _origem_do_arquivo(
    db: sqlite3.Connection, arquivo: sqlite3.Row
) -> Tuple[str, str]:
    """Rótulo de origem (vai no fingerprint) e moeda da conta/cartão."""
    if arquivo["cartao_id"]:
        cartao = db.execute(
            "SELECT nome, moeda FROM cartoes WHERE id=?", (arquivo["cartao_id"],)
        ).fetchone()
        if cartao is not None:
            return f"cartao:{cartao['nome']}", cartao["moeda"] or "BRL"
    if arquivo["conta_id"]:
        conta = db.execute(
            "SELECT nome, moeda FROM contas WHERE id=?", (arquivo["conta_id"],)
        ).fetchone()
        if conta is not None:
            return f"conta:{conta['nome']}", conta["moeda"] or "BRL"
    return "config", "BRL"


def _processar(
    db: sqlite3.Connection,
    arquivo_id: int,
    conteudo: bytes,
    *,
    senha_pdf: Optional[str] = None,
    mapeamento: Optional[Dict[str, str]] = None,
    lote_anterior: Optional[int] = None,
) -> Dict[str, Any]:
    """Detecta, escolhe adaptador, faz parse e grava lote + linhas brutas.

    As transações candidatas ficam SÓ no ``resumo_json`` do lote (staged);
    nada entra em ``transacoes`` antes de :func:`confirmar_lote`.
    """
    arquivo = db.execute("SELECT * FROM arquivos WHERE id=?", (arquivo_id,)).fetchone()
    if arquivo is None:
        return {"erro": f"arquivo {arquivo_id} não encontrado", "status": "erro"}

    formato = detectar(conteudo, arquivo["nome_original"])
    if formato.formato == "desconhecido":
        detalhe = formato.detalhe or "assinatura de bytes desconhecida"
        return _falha(db, arquivo_id, f"formato de arquivo não reconhecido ({detalhe})")

    with db:
        db.execute(
            "UPDATE arquivos SET status='processando', mime_real=? WHERE id=?",
            (formato.mime, arquivo_id),
        )

    rotulo_origem, moeda = _origem_do_arquivo(db, arquivo)
    contexto = base.ContextoParse(
        conteudo=conteudo,
        formato=formato,
        nome_original=arquivo["nome_original"],
        tipo_documento=arquivo["tipo_documento"],
        moeda=moeda,
        rotulo_origem=rotulo_origem,
        senha=senha_pdf,  # usada e descartada — nunca gravada
        mapeamento=mapeamento,
    )
    adaptador = base.escolher(contexto)
    if adaptador is None:
        return _falha(db, arquivo_id, "nenhum adaptador de parsing reconhece este arquivo")

    try:
        resultado = adaptador.parse(contexto)
    except Exception as exc:  # noqa: BLE001 - contrato diz que parse não estoura; rede de segurança
        return _falha(db, arquivo_id, f"falha interna do adaptador {adaptador.nome}: {exc}")

    eh_cartao = bool(arquivo["cartao_id"])
    for transacao in resultado.transacoes:
        transacao.conta_id = arquivo["conta_id"]
        transacao.cartao_id = arquivo["cartao_id"]
        _tipa_transacao(transacao, eh_cartao)
    staged = [_transacao_para_dict(t) for t in resultado.transacoes]
    saldos_staged = [
        {"data": s.data.isoformat(), "valor_centavos": s.valor_centavos, "rotulo": s.rotulo}
        for s in resultado.saldos
    ]

    status = (
        "aguardando_revisao"
        if resultado.mapeamento_necessario is not None or _erros_graves(resultado)
        else "pronto"
    )

    with db:
        if lote_anterior is not None:
            db.execute(
                "UPDATE lotes SET revertido_em=? WHERE id=? AND revertido_em IS NULL",
                (_agora(), lote_anterior),
            )
        cursor = db.execute(
            "INSERT INTO lotes (arquivo_id, resumo_json) VALUES (?, '{}')", (arquivo_id,)
        )
        lote_id = int(cursor.lastrowid)
        for linha in resultado.linhas_brutas:
            db.execute(
                "INSERT INTO linhas_brutas (lote_id, ordem, origem_ref, conteudo_json)"
                " VALUES (?, ?, ?, ?)",
                (
                    lote_id,
                    linha.ordem,
                    linha.origem_ref,
                    json.dumps(linha.conteudo, ensure_ascii=False, default=str),
                ),
            )
        previa = _monta_previa(db, arquivo_id, lote_id, status, resultado, staged)
        resumo = {
            "previa": previa,
            "transacoes_staged": staged,
            "saldos_staged": saldos_staged,
            "adaptador": f"{adaptador.nome} v{adaptador.versao}",
        }
        db.execute(
            "UPDATE lotes SET resumo_json=? WHERE id=?",
            (json.dumps(resumo, ensure_ascii=False, default=str), lote_id),
        )
        db.execute(
            "UPDATE arquivos SET status=?, versao_parser=?, erro=NULL,"
            " periodo_inicio=coalesce(?, periodo_inicio),"
            " periodo_fim=coalesce(?, periodo_fim) WHERE id=?",
            (
                status,
                f"{adaptador.nome} v{adaptador.versao}",
                previa["periodo"]["inicio"],
                previa["periodo"]["fim"],
                arquivo_id,
            ),
        )
    return previa


# --------------------------------------------------------------------------
# API pública do pipeline
# --------------------------------------------------------------------------

def receber_arquivo(
    db: sqlite3.Connection,
    conteudo: bytes,
    nome: str,
    tipo_documento: str,
    *,
    instituicao: str = "",
    conta: str = "",
    cartao: str = "",
    moeda: str = "BRL",
    senha_pdf: Optional[str] = None,
    periodo: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload → validação → original imutável → parse → prévia staged."""
    if tipo_documento not in TIPOS_DOCUMENTO:
        return {"erro": f"tipo_documento inválido: {tipo_documento!r}", "status": "falhou"}
    if not conteudo:
        return {"erro": "arquivo vazio", "status": "falhou"}
    if not moeda_valida(moeda):
        return {"erro": f"moeda inválida: {moeda!r} (use código ISO 4217)", "status": "falhou"}
    limite = _limite_upload_bytes()
    if len(conteudo) > limite:
        return {
            "erro": (
                f"arquivo com {len(conteudo)} bytes excede o limite de"
                f" {limite // (1024 * 1024)} MB (ajuste CELESTIA_MAX_UPLOAD_MB se preciso)"
            ),
            "status": "falhou",
        }

    sha256 = storage.sha256_de(conteudo)
    existente = db.execute(
        "SELECT id, status FROM arquivos WHERE sha256=?", (sha256,)
    ).fetchone()
    if existente is not None:
        return {
            "erro_duplicado": True,
            "arquivo_id": int(existente["id"]),
            "mensagem": (
                f"este arquivo já foi enviado (arquivo #{existente['id']},"
                f" status '{existente['status']}') — nada foi regravado;"
                " use reprocessar_arquivo para importá-lo de novo"
            ),
            "pode_reprocessar": True,
        }

    moeda = moeda.strip().upper()
    periodo_inicio, periodo_fim = _parse_periodo(periodo)
    with db:
        instituicao_id = _obter_ou_criar_instituicao(db, instituicao)
        conta_id: Optional[int] = None
        cartao_id: Optional[int] = None
        # Conta e cartão são mutuamente exclusivos, conforme o tipo do documento.
        if tipo_documento == "extrato_conta":
            conta_id = _obter_ou_criar_conta(db, conta, instituicao_id, moeda)
        elif tipo_documento == "fatura_cartao":
            cartao_id = _obter_ou_criar_cartao(db, cartao, instituicao_id, moeda)
        digest, caminho_objeto = storage.gravar(conteudo, nome)
        cursor = db.execute(
            "INSERT INTO arquivos (sha256, nome_original, mime_real, tamanho,"
            " tipo_documento, instituicao_id, conta_id, cartao_id, periodo_inicio,"
            " periodo_fim, status, caminho_objeto)"
            " VALUES (?, ?, 'application/octet-stream', ?, ?, ?, ?, ?, ?, ?, 'recebido', ?)",
            (
                digest,
                nome or "arquivo",
                len(conteudo),
                tipo_documento,
                instituicao_id,
                conta_id,
                cartao_id,
                periodo_inicio,
                periodo_fim,
                caminho_objeto,
            ),
        )
        arquivo_id = int(cursor.lastrowid)
    with db:
        db.execute("UPDATE arquivos SET status='validando' WHERE id=?", (arquivo_id,))
    return _processar(db, arquivo_id, conteudo, senha_pdf=senha_pdf)


def aplicar_mapeamento(
    db: sqlite3.Connection, lote_id: int, mapeamento: Dict[str, str]
) -> Dict[str, Any]:
    """Re-parse com o mapeamento manual de colunas; substitui o lote staged."""
    lote = db.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
    if lote is None:
        return {"erro": f"lote {lote_id} não encontrado", "status": "erro"}
    if lote["confirmado_em"]:
        return {"erro": "lote já confirmado — não é possível remapear", "status": "confirmado"}
    arquivo = db.execute(
        "SELECT * FROM arquivos WHERE id=?", (lote["arquivo_id"],)
    ).fetchone()
    conteudo = storage.ler(arquivo["caminho_objeto"]) if arquivo is not None else None
    if conteudo is None:
        return {"erro": "original do arquivo não encontrado no storage", "status": "erro"}
    return _processar(
        db,
        int(arquivo["id"]),
        conteudo,
        mapeamento=mapeamento,
        lote_anterior=lote_id,
    )


def _apos_inserir_transacoes(db: sqlite3.Connection, lote_id: int) -> None:
    """Gancho interno (no-op) chamado DENTRO da transação de confirmação.

    Existe para os testes de atomicidade injetarem uma falha no meio da
    confirmação e provarem que nada fica gravado.
    """


def confirmar_lote(db: sqlite3.Connection, lote_id: int) -> Dict[str, Any]:
    """Grava as transações staged numa transação SQLite única (atômica).

    Dedup: ``id_banco`` existente → pula; fingerprint existente → pula;
    fingerprint nova mas muito parecida com ``id_banco`` diferente → insere e
    registra na fila ``duplicidades``. Depois vincula compras de cartão à
    fatura do ciclo, grava saldos, confirma lote/arquivo e audita. Regras de
    categoria rodam APÓS o commit (import tardio de ``categorias``).
    """
    lote = db.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
    if lote is None:
        return {"erro": f"lote {lote_id} não encontrado", "lote_id": lote_id, "status": "erro"}
    if lote["confirmado_em"]:
        return {"erro": "lote já confirmado", "lote_id": lote_id, "status": "confirmado"}
    if lote["revertido_em"]:
        return {
            "erro": "lote revertido — reprocesse o arquivo antes de confirmar",
            "lote_id": lote_id,
            "status": "revertido",
        }
    try:
        resumo = json.loads(lote["resumo_json"] or "{}")
    except json.JSONDecodeError:
        resumo = {}
    previa = resumo.get("previa") or {}
    staged: List[Dict[str, Any]] = resumo.get("transacoes_staged") or []
    if previa.get("mapeamento_necessario"):
        return {
            "erro": "mapeamento de colunas pendente — chame aplicar_mapeamento antes",
            "lote_id": lote_id,
            "status": "aguardando_revisao",
        }
    if not staged:
        return {
            "erro": "lote sem transações candidatas para confirmar",
            "lote_id": lote_id,
            "status": str(previa.get("status") or "aguardando_revisao"),
        }

    arquivo = db.execute(
        "SELECT * FROM arquivos WHERE id=?", (lote["arquivo_id"],)
    ).fetchone()
    cartao = (
        db.execute("SELECT * FROM cartoes WHERE id=?", (arquivo["cartao_id"],)).fetchone()
        if arquivo["cartao_id"]
        else None
    )

    avisos: List[str] = []
    contagens = {
        "inseridas": 0,
        "puladas_id_banco": 0,
        "puladas_fingerprint": 0,
        "em_duplicidades": 0,
    }
    transacao_ids: List[int] = []
    avisou_sem_fechamento = False

    try:
        with db:  # transação única: ou entra tudo, ou nada
            for t in staged:
                conta_id, cartao_id = t.get("conta_id"), t.get("cartao_id")
                if t.get("id_banco") and _existe_id_banco(db, conta_id, cartao_id, t["id_banco"]):
                    contagens["puladas_id_banco"] += 1
                    continue
                if _existe_fingerprint(db, conta_id, cartao_id, t["fingerprint"]):
                    contagens["puladas_fingerprint"] += 1
                    continue
                fingerprint_parecida = _parecida_com_id_diferente(db, t)

                fatura_id: Optional[int] = None
                if cartao is not None and t["tipo"] in ("compra", "estorno"):
                    if cartao["dia_fechamento"]:
                        fatura_id = _fatura_do_ciclo(
                            db, cartao, date.fromisoformat(t["data_operacao"])
                        )
                    elif not avisou_sem_fechamento:
                        avisou_sem_fechamento = True
                        avisos.append(
                            f"cartão '{cartao['nome']}' sem dia de fechamento —"
                            " compras ficaram sem fatura vinculada (fatura_id nulo)"
                        )

                cursor = db.execute(
                    "INSERT INTO transacoes (lote_id, conta_id, cartao_id, fatura_id,"
                    " data_operacao, data_lancamento, data_compensacao, valor_centavos,"
                    " moeda, direcao, tipo, descricao_original, descricao_normalizada,"
                    " contraparte, id_banco, documento, saldo_apos_centavos, parcela_num,"
                    " parcela_total, status_conciliacao, fingerprint, origem_ref)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pendente',?,?)",
                    (
                        lote_id,
                        conta_id,
                        cartao_id,
                        fatura_id,
                        t["data_operacao"],
                        t.get("data_lancamento"),
                        t.get("data_compensacao"),
                        t["valor_centavos"],
                        t["moeda"],
                        t["direcao"],
                        t["tipo"],
                        t["descricao_original"],
                        t["descricao_normalizada"],
                        t.get("contraparte"),
                        t.get("id_banco"),
                        t.get("documento"),
                        t.get("saldo_apos_centavos"),
                        t.get("parcela_num"),
                        t.get("parcela_total"),
                        t["fingerprint"],
                        t["origem_ref"],
                    ),
                )
                novo_id = int(cursor.lastrowid)
                transacao_ids.append(novo_id)
                contagens["inseridas"] += 1
                if fingerprint_parecida is not None:
                    db.execute(
                        "INSERT INTO duplicidades (transacao_id, candidata_fingerprint,"
                        " motivo) VALUES (?, ?, ?)",
                        (
                            novo_id,
                            fingerprint_parecida,
                            "mesma data, valor e descrição com id_banco diferente",
                        ),
                    )
                    contagens["em_duplicidades"] += 1

            saldos_staged = resumo.get("saldos_staged") or []
            if saldos_staged and not arquivo["conta_id"]:
                avisos.append(
                    "documento de cartão declarou saldos — ignorados (saldo é de conta)"
                )
            elif arquivo["conta_id"]:
                for s in saldos_staged:
                    db.execute(
                        "INSERT OR IGNORE INTO saldos (conta_id, data, valor_centavos,"
                        " origem, lote_id, rotulo) VALUES (?, ?, ?, 'extrato', ?, ?)",
                        (
                            arquivo["conta_id"],
                            s["data"],
                            s["valor_centavos"],
                            lote_id,
                            s.get("rotulo") or "",
                        ),
                    )

            db.execute("UPDATE lotes SET confirmado_em=? WHERE id=?", (_agora(), lote_id))
            db.execute(
                "UPDATE arquivos SET status='confirmado' WHERE id=?", (arquivo["id"],)
            )
            registra_auditoria(
                db,
                "lote",
                lote_id,
                "lote_confirmado",
                valor_novo=json.dumps(contagens, ensure_ascii=False),
                origem="pipeline",
            )
            _apos_inserir_transacoes(db, lote_id)
    except Exception as exc:  # noqa: BLE001 - rollback já aconteceu no `with db`
        return {
            "lote_id": lote_id,
            "arquivo_id": int(lote["arquivo_id"]),
            "status": "erro",
            "erro": f"confirmação interrompida — nada foi gravado: {exc}",
            "inseridas": 0,
            "puladas_id_banco": 0,
            "puladas_fingerprint": 0,
            "em_duplicidades": 0,
        }

    # Categorização por regras roda depois do commit; o módulo pode ainda não
    # existir (é escrito por outro agente) — import tardio, nunca fatal.
    if transacao_ids:
        try:
            from . import categorias  # noqa: PLC0415 - import tardio proposital
        except ImportError:
            avisos.append("módulo de categorias indisponível — regras não aplicadas")
        else:
            try:
                categorias.aplicar_regras(db, transacao_ids=transacao_ids, dry_run=False)
            except Exception as exc:  # noqa: BLE001 - regra ruim não desfaz a confirmação
                avisos.append(f"aplicação de regras de categoria falhou: {exc}")

    return {
        "lote_id": lote_id,
        "arquivo_id": int(lote["arquivo_id"]),
        "status": "confirmado",
        **contagens,
        "transacao_ids": transacao_ids,
        "avisos": avisos,
    }


def reverter_lote(
    db: sqlite3.Connection, lote_id: int, justificativa: str = ""
) -> Dict[str, Any]:
    """Apaga as transações do lote (e dependências) — arquivo e linhas ficam."""
    lote = db.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
    if lote is None:
        return {"erro": f"lote {lote_id} não encontrado", "lote_id": lote_id, "status": "erro"}
    if lote["revertido_em"]:
        return {"erro": "lote já revertido", "lote_id": lote_id, "status": "revertido"}

    with db:
        ids = [
            int(linha["id"])
            for linha in db.execute(
                "SELECT id FROM transacoes WHERE lote_id=?", (lote_id,)
            ).fetchall()
        ]
        if ids:
            marcadores = ",".join("?" * len(ids))
            # Cascata manual: dependências primeiro, depois as transações.
            db.execute(
                f"DELETE FROM divisoes_transacao WHERE transacao_id IN ({marcadores})", ids
            )
            db.execute(
                f"DELETE FROM duplicidades WHERE transacao_id IN ({marcadores})", ids
            )
            db.execute(
                f"DELETE FROM conciliacao_itens WHERE transacao_id IN ({marcadores})", ids
            )
            db.execute(f"DELETE FROM transacoes WHERE id IN ({marcadores})", ids)
        db.execute("DELETE FROM saldos WHERE lote_id=?", (lote_id,))
        db.execute("UPDATE lotes SET revertido_em=? WHERE id=?", (_agora(), lote_id))
        db.execute(
            "UPDATE arquivos SET status='revertido' WHERE id=?", (lote["arquivo_id"],)
        )
        registra_auditoria(
            db,
            "lote",
            lote_id,
            "lote_revertido",
            valor_novo=json.dumps({"transacoes_apagadas": len(ids)}, ensure_ascii=False),
            origem="pipeline",
            justificativa=justificativa or None,
        )
    return {
        "lote_id": lote_id,
        "arquivo_id": int(lote["arquivo_id"]),
        "status": "revertido",
        "transacoes_apagadas": len(ids),
    }


def reprocessar_arquivo(
    db: sqlite3.Connection, arquivo_id: int, senha_pdf: Optional[str] = None
) -> Dict[str, Any]:
    """Lê o original do storage e roda o fluxo de novo (parser pode ter melhorado).

    Se houver lote ativo: confirmado → é revertido (auditado); apenas staged →
    é marcado revertido junto com a criação do novo lote.
    """
    arquivo = db.execute("SELECT * FROM arquivos WHERE id=?", (arquivo_id,)).fetchone()
    if arquivo is None:
        return {"erro": f"arquivo {arquivo_id} não encontrado", "status": "erro"}
    conteudo = storage.ler(arquivo["caminho_objeto"])
    if conteudo is None:
        return {
            "erro": "original do arquivo não encontrado no storage — reenvie o arquivo",
            "arquivo_id": arquivo_id,
            "status": "erro",
        }

    ativo = db.execute(
        "SELECT * FROM lotes WHERE arquivo_id=? AND revertido_em IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (arquivo_id,),
    ).fetchone()
    lote_anterior: Optional[int] = None
    if ativo is not None:
        if ativo["confirmado_em"]:
            revertido = reverter_lote(
                db, int(ativo["id"]), justificativa="reprocessamento do arquivo"
            )
            if revertido.get("erro"):
                return revertido
        else:
            lote_anterior = int(ativo["id"])
    return _processar(
        db, arquivo_id, conteudo, senha_pdf=senha_pdf, lote_anterior=lote_anterior
    )
