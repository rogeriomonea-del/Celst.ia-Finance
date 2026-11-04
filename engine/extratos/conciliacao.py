"""Conciliação pagamento ↔ fatura e validação contábil de extrato.

Sugestões cruzam transações ``tipo=pagamento_fatura`` ainda pendentes com
faturas em aberto pela CONTA PAGADORA dos cartões + proximidade de valor e
data. A conciliação em si é muitos-para-muitos (N transações × M faturas numa
mesma conciliação), atualiza ``faturas.pago_centavos``, recalcula o status via
``deriva_status_fatura`` e audita tudo. Pagamento maior que o devido NUNCA é
escondido: vira ``status_conciliacao='divergente'`` + aviso no retorno.

Dinheiro sempre em centavos ``int`` — nunca float.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .dinheiro import formata_centavos
from .modelos import Fatura, deriva_status_fatura
from .store import registra_auditoria

# --------------------------------------------------------------------------
# Constantes de matching
# --------------------------------------------------------------------------

#: Janela aceita em torno do vencimento: pagamento antecipado até 15 dias
#: antes e atrasado até 20 dias depois.
JANELA_ANTES_DIAS = 15
JANELA_DEPOIS_DIAS = 20

#: Tolerância de valor: 1% do restante OU 200 centavos, o que for maior.
TOLERANCIA_MINIMA_CENTAVOS = 200

#: Pagamento agrupado: combinações de até 3 faturas do mesmo dia de vencimento.
MAX_FATURAS_AGRUPADAS = 3

#: Teto de faturas por grupo de vencimento ao gerar combinações (proteção
#: contra explosão combinatória com dados degenerados).
MAX_CANDIDATAS_POR_GRUPO = 12


def _tolerancia(restante_centavos: int) -> int:
    return max(restante_centavos // 100, TOLERANCIA_MINIMA_CENTAVOS)


def _restante(fatura: sqlite3.Row) -> Optional[int]:
    """Quanto ainda falta pagar (centavos); None quando o valor é desconhecido."""
    if fatura["valor_centavos"] is None:
        return None
    return max(int(fatura["valor_centavos"]) - int(fatura["pago_centavos"] or 0), 0)


def _status_fatura(fatura: sqlite3.Row, pago_novo: int, hoje: date) -> str:
    """Status derivado pela regra única do domínio (``deriva_status_fatura``)."""
    modelo = Fatura(
        cartao_id=int(fatura["cartao_id"]),
        competencia=str(fatura["competencia"]),
        vence_em=date.fromisoformat(str(fatura["vence_em"])),
        fecha_em=(
            date.fromisoformat(str(fatura["fecha_em"])) if fatura["fecha_em"] else None
        ),
        valor_centavos=(
            None if fatura["valor_centavos"] is None else int(fatura["valor_centavos"])
        ),
        pago_centavos=pago_novo,
    )
    return deriva_status_fatura(modelo, hoje)


# --------------------------------------------------------------------------
# Sugestões
# --------------------------------------------------------------------------


def sugerir_conciliacoes(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Sugere conciliações para pagamentos de fatura ainda pendentes.

    Para cada transação ``tipo=pagamento_fatura`` com
    ``status_conciliacao='pendente'``:

    - candidatas = faturas não pagas de cartões cuja conta pagadora bate com a
      conta da transação (conta pagadora desconhecida não elimina — só reduz o
      universo quando as duas pontas são conhecidas), com ``data_operacao``
      entre ``vence_em − 15d`` e ``vence_em + 20d``;
    - **integral**: ``|valor − restante| ≤ max(1% do restante, 200 centavos)``;
    - **parcial**: ``valor < restante`` (fora da tolerância) → sugere quitação
      parcial com o valor da transação;
    - **agrupado**: valor ≈ soma dos restantes de 2 a 3 faturas com o MESMO dia
      de vencimento (pagamento único de várias faturas).

    Devolve ``[{transacao_id, faturas:[{fatura_id, valor_sugerido_centavos}],
    confianca, motivo}]`` — nada é gravado aqui.
    """
    pagamentos = db.execute(
        "SELECT * FROM transacoes WHERE tipo='pagamento_fatura'"
        " AND status_conciliacao='pendente' ORDER BY data_operacao, id"
    ).fetchall()
    if not pagamentos:
        return []
    faturas = db.execute(
        "SELECT f.*, c.conta_pagadora_id FROM faturas f"
        " JOIN cartoes c ON c.id = f.cartao_id"
        " WHERE f.status != 'paga' ORDER BY f.vence_em, f.id"
    ).fetchall()

    sugestoes: List[Dict[str, Any]] = []
    for pagamento in pagamentos:
        try:
            data_operacao = date.fromisoformat(str(pagamento["data_operacao"]))
        except ValueError:
            continue  # data corrompida não derruba as demais sugestões
        valor = int(pagamento["valor_centavos"])

        candidatas: List[Tuple[sqlite3.Row, date, int]] = []
        for fatura in faturas:
            restante = _restante(fatura)
            if restante is None or restante <= 0:
                continue
            if (
                pagamento["conta_id"] is not None
                and fatura["conta_pagadora_id"] is not None
                and int(fatura["conta_pagadora_id"]) != int(pagamento["conta_id"])
            ):
                continue
            try:
                vence_em = date.fromisoformat(str(fatura["vence_em"]))
            except ValueError:
                continue
            inicio = vence_em - timedelta(days=JANELA_ANTES_DIAS)
            fim = vence_em + timedelta(days=JANELA_DEPOIS_DIAS)
            if not (inicio <= data_operacao <= fim):
                continue
            candidatas.append((fatura, vence_em, restante))

        locais: List[Dict[str, Any]] = []
        for fatura, vence_em, restante in candidatas:
            diferenca = abs(valor - restante)
            if diferenca <= _tolerancia(restante):
                locais.append(
                    {
                        "faturas": [
                            {"fatura_id": int(fatura["id"]), "valor_sugerido_centavos": valor}
                        ],
                        "confianca": 1.0 if diferenca == 0 else 0.9,
                        "motivo": (
                            "pagamento_integral"
                            f" (fatura {fatura['id']} vence em {vence_em.isoformat()})"
                        ),
                    }
                )
            elif valor < restante:
                locais.append(
                    {
                        "faturas": [
                            {"fatura_id": int(fatura["id"]), "valor_sugerido_centavos": valor}
                        ],
                        "confianca": 0.6,
                        "motivo": (
                            "pagamento_parcial"
                            f" ({formata_centavos(valor)} de {formata_centavos(restante)}"
                            f" restantes da fatura {fatura['id']})"
                        ),
                    }
                )

        # Pagamento agrupado: 2..3 faturas com o mesmo dia de vencimento.
        por_vencimento: Dict[date, List[Tuple[sqlite3.Row, int]]] = {}
        for fatura, vence_em, restante in candidatas:
            por_vencimento.setdefault(vence_em, []).append((fatura, restante))
        for vence_em, grupo in por_vencimento.items():
            grupo = grupo[:MAX_CANDIDATAS_POR_GRUPO]
            if len(grupo) < 2:
                continue
            for tamanho in range(2, MAX_FATURAS_AGRUPADAS + 1):
                for combinacao in itertools.combinations(grupo, tamanho):
                    soma = sum(restante for _, restante in combinacao)
                    if abs(valor - soma) <= _tolerancia(soma):
                        locais.append(
                            {
                                "faturas": [
                                    {
                                        "fatura_id": int(fatura["id"]),
                                        "valor_sugerido_centavos": restante,
                                    }
                                    for fatura, restante in combinacao
                                ],
                                "confianca": 0.8,
                                "motivo": (
                                    "pagamento_agrupado"
                                    f" ({len(combinacao)} faturas vencendo em"
                                    f" {vence_em.isoformat()})"
                                ),
                            }
                        )

        locais.sort(key=lambda s: (-s["confianca"], s["faturas"][0]["fatura_id"]))
        for sugestao in locais:
            sugestoes.append({"transacao_id": int(pagamento["id"]), **sugestao})
    return sugestoes


# --------------------------------------------------------------------------
# Conciliação (muitos-para-muitos)
# --------------------------------------------------------------------------


def conciliar(
    db: sqlite3.Connection, itens: List[Dict[str, Any]], observacao: str = ""
) -> Dict[str, Any]:
    """Concilia N transações de pagamento com M faturas numa transação SQLite.

    ``itens`` = ``[{transacao_id?, fatura_id?, papel, valor_centavos?}]`` com
    ``papel ∈ {pagamento, obrigacao}``. Para obrigações sem ``valor_centavos``
    declarado, o valor aplicado é alocado na ordem dos itens, limitado ao
    restante da fatura. Se sobrar ou faltar dinheiro (pagamento maior/menor que
    o alocado), a(s) transação(ões) ficam ``divergente`` e o retorno traz o
    aviso — a divergência nunca é escondida.
    """
    if not itens:
        return {"erro": "conciliação vazia — informe itens com papel pagamento/obrigacao"}
    invalidos = [i for i in itens if i.get("papel") not in ("pagamento", "obrigacao")]
    if invalidos:
        return {"erro": "papel inválido em item — use 'pagamento' ou 'obrigacao'"}

    pagamentos: List[Tuple[sqlite3.Row, int]] = []
    for item in (i for i in itens if i["papel"] == "pagamento"):
        transacao_id = item.get("transacao_id")
        transacao = (
            db.execute("SELECT * FROM transacoes WHERE id=?", (transacao_id,)).fetchone()
            if transacao_id is not None
            else None
        )
        if transacao is None:
            return {"erro": f"transação {transacao_id!r} não encontrada"}
        valor = item.get("valor_centavos")
        if valor is not None and (isinstance(valor, bool) or not isinstance(valor, int)):
            return {"erro": "valor_centavos de pagamento deve ser inteiro em centavos"}
        pagamentos.append((transacao, int(valor) if valor is not None else int(transacao["valor_centavos"])))

    obrigacoes: List[Tuple[sqlite3.Row, Optional[int]]] = []
    for item in (i for i in itens if i["papel"] == "obrigacao"):
        fatura_id = item.get("fatura_id")
        fatura = (
            db.execute("SELECT * FROM faturas WHERE id=?", (fatura_id,)).fetchone()
            if fatura_id is not None
            else None
        )
        if fatura is None:
            return {"erro": f"fatura {fatura_id!r} não encontrada"}
        valor = item.get("valor_centavos")
        if valor is not None and (isinstance(valor, bool) or not isinstance(valor, int)):
            return {"erro": "valor_centavos de obrigação deve ser inteiro em centavos"}
        obrigacoes.append((fatura, None if valor is None else int(valor)))

    if not pagamentos or not obrigacoes:
        return {
            "erro": (
                "conciliação exige ao menos um item 'pagamento' (transação)"
                " e um 'obrigacao' (fatura)"
            )
        }

    total_pagamentos = sum(valor for _, valor in pagamentos)
    hoje = date.today()
    avisos: List[str] = []

    # Alocação: declarado vence; sem declaração, aloca na ordem até o restante.
    alocacoes: List[Tuple[sqlite3.Row, int]] = []
    sobra = total_pagamentos
    for fatura, declarado in obrigacoes:
        restante = _restante(fatura)
        if declarado is not None:
            aplicado = declarado
        elif restante is None:
            aplicado = max(sobra, 0)
        else:
            aplicado = min(restante, max(sobra, 0))
        sobra -= aplicado
        alocacoes.append((fatura, aplicado))

    divergente = sobra != 0
    if sobra > 0:
        avisos.append(
            "pagamento maior que o devido: sobra de"
            f" {formata_centavos(sobra)} sem fatura correspondente"
        )
    elif sobra < 0:
        avisos.append(
            "faturas receberam"
            f" {formata_centavos(-sobra)} a mais do que o pagamento informado"
        )

    try:
        with db:  # transação única: ou grava tudo, ou nada
            cursor = db.execute(
                "INSERT INTO conciliacoes (tipo, observacao)"
                " VALUES ('pagamento_fatura', ?)",
                (observacao or None,),
            )
            conciliacao_id = int(cursor.lastrowid)

            resultado_faturas: List[Dict[str, Any]] = []
            for fatura, aplicado in alocacoes:
                db.execute(
                    "INSERT INTO conciliacao_itens (conciliacao_id, fatura_id, papel,"
                    " valor_centavos) VALUES (?,?,'obrigacao',?)",
                    (conciliacao_id, fatura["id"], aplicado),
                )
                pago_anterior = int(fatura["pago_centavos"] or 0)
                pago_novo = pago_anterior + aplicado
                status_novo = _status_fatura(fatura, pago_novo, hoje)
                db.execute(
                    "UPDATE faturas SET pago_centavos=?, status=? WHERE id=?",
                    (pago_novo, status_novo, fatura["id"]),
                )
                registra_auditoria(
                    db,
                    "fatura",
                    int(fatura["id"]),
                    "pagamento_conciliado",
                    campo="pago_centavos",
                    valor_anterior=str(pago_anterior),
                    valor_novo=str(pago_novo),
                    origem=f"conciliacao:{conciliacao_id}",
                    justificativa=observacao or None,
                )
                if fatura["status"] != status_novo:
                    registra_auditoria(
                        db,
                        "fatura",
                        int(fatura["id"]),
                        "status_recalculado",
                        campo="status",
                        valor_anterior=str(fatura["status"]),
                        valor_novo=status_novo,
                        origem=f"conciliacao:{conciliacao_id}",
                    )
                if fatura["valor_centavos"] is not None and pago_novo > int(
                    fatura["valor_centavos"]
                ):
                    excesso = pago_novo - int(fatura["valor_centavos"])
                    divergente = True
                    avisos.append(
                        f"fatura {fatura['id']} recebeu"
                        f" {formata_centavos(excesso)} acima do valor devido"
                    )
                resultado_faturas.append(
                    {
                        "fatura_id": int(fatura["id"]),
                        "aplicado_centavos": aplicado,
                        "pago_centavos": pago_novo,
                        "status": status_novo,
                    }
                )

            status_transacao = "divergente" if divergente else "conciliada"
            resultado_transacoes: List[Dict[str, Any]] = []
            for transacao, valor in pagamentos:
                db.execute(
                    "INSERT INTO conciliacao_itens (conciliacao_id, transacao_id,"
                    " papel, valor_centavos) VALUES (?,?,'pagamento',?)",
                    (conciliacao_id, transacao["id"], valor),
                )
                db.execute(
                    "UPDATE transacoes SET status_conciliacao=? WHERE id=?",
                    (status_transacao, transacao["id"]),
                )
                registra_auditoria(
                    db,
                    "transacao",
                    int(transacao["id"]),
                    "conciliada",
                    campo="status_conciliacao",
                    valor_anterior=str(transacao["status_conciliacao"]),
                    valor_novo=status_transacao,
                    origem=f"conciliacao:{conciliacao_id}",
                    justificativa=observacao or None,
                )
                resultado_transacoes.append(
                    {
                        "transacao_id": int(transacao["id"]),
                        "status_conciliacao": status_transacao,
                    }
                )

            registra_auditoria(
                db,
                "conciliacao",
                conciliacao_id,
                "conciliacao_criada",
                valor_novo=json.dumps(
                    {
                        "transacoes": [t["transacao_id"] for t in resultado_transacoes],
                        "faturas": [f["fatura_id"] for f in resultado_faturas],
                        "total_pagamentos_centavos": total_pagamentos,
                        "sobra_centavos": sobra,
                    },
                    ensure_ascii=False,
                ),
                origem="conciliacao",
                justificativa=observacao or None,
            )
    except sqlite3.Error as exc:
        return {"erro": f"conciliação não gravada (nada foi alterado): {exc}"}

    return {
        "conciliacao_id": conciliacao_id,
        "status_conciliacao": status_transacao,
        "sobra_centavos": sobra,
        "faturas": resultado_faturas,
        "transacoes": resultado_transacoes,
        "avisos": avisos,
    }


def desfazer_conciliacao(db: sqlite3.Connection, conciliacao_id: int) -> Dict[str, Any]:
    """Desfaz uma conciliação: estorna ``pago_centavos`` das faturas, recalcula
    o status e devolve as transações a ``pendente`` (quando não participam de
    outra conciliação). Tudo auditado; os itens são removidos ao final."""
    conciliacao = db.execute(
        "SELECT * FROM conciliacoes WHERE id=?", (conciliacao_id,)
    ).fetchone()
    if conciliacao is None:
        return {"erro": f"conciliação {conciliacao_id} não encontrada"}
    itens = db.execute(
        "SELECT * FROM conciliacao_itens WHERE conciliacao_id=? ORDER BY id",
        (conciliacao_id,),
    ).fetchall()
    hoje = date.today()

    try:
        with db:
            resultado_faturas: List[Dict[str, Any]] = []
            resultado_transacoes: List[Dict[str, Any]] = []
            for item in itens:
                if item["papel"] == "obrigacao" and item["fatura_id"] is not None:
                    fatura = db.execute(
                        "SELECT * FROM faturas WHERE id=?", (item["fatura_id"],)
                    ).fetchone()
                    if fatura is None:
                        continue
                    pago_anterior = int(fatura["pago_centavos"] or 0)
                    pago_novo = max(pago_anterior - int(item["valor_centavos"] or 0), 0)
                    status_novo = _status_fatura(fatura, pago_novo, hoje)
                    db.execute(
                        "UPDATE faturas SET pago_centavos=?, status=? WHERE id=?",
                        (pago_novo, status_novo, fatura["id"]),
                    )
                    registra_auditoria(
                        db,
                        "fatura",
                        int(fatura["id"]),
                        "pagamento_estornado",
                        campo="pago_centavos",
                        valor_anterior=str(pago_anterior),
                        valor_novo=str(pago_novo),
                        origem=f"conciliacao:{conciliacao_id}",
                    )
                    resultado_faturas.append(
                        {
                            "fatura_id": int(fatura["id"]),
                            "pago_centavos": pago_novo,
                            "status": status_novo,
                        }
                    )
                elif item["papel"] == "pagamento" and item["transacao_id"] is not None:
                    outras = int(
                        db.execute(
                            "SELECT COUNT(*) FROM conciliacao_itens"
                            " WHERE transacao_id=? AND conciliacao_id != ?",
                            (item["transacao_id"], conciliacao_id),
                        ).fetchone()[0]
                    )
                    if outras == 0:
                        transacao = db.execute(
                            "SELECT status_conciliacao FROM transacoes WHERE id=?",
                            (item["transacao_id"],),
                        ).fetchone()
                        db.execute(
                            "UPDATE transacoes SET status_conciliacao='pendente'"
                            " WHERE id=?",
                            (item["transacao_id"],),
                        )
                        registra_auditoria(
                            db,
                            "transacao",
                            int(item["transacao_id"]),
                            "conciliacao_desfeita",
                            campo="status_conciliacao",
                            valor_anterior=(
                                str(transacao["status_conciliacao"]) if transacao else None
                            ),
                            valor_novo="pendente",
                            origem=f"conciliacao:{conciliacao_id}",
                        )
                    resultado_transacoes.append(
                        {
                            "transacao_id": int(item["transacao_id"]),
                            "status_conciliacao": "pendente" if outras == 0 else None,
                        }
                    )
            db.execute(
                "DELETE FROM conciliacao_itens WHERE conciliacao_id=?", (conciliacao_id,)
            )
            db.execute("DELETE FROM conciliacoes WHERE id=?", (conciliacao_id,))
            registra_auditoria(
                db,
                "conciliacao",
                conciliacao_id,
                "conciliacao_desfeita",
                origem="conciliacao",
            )
    except sqlite3.Error as exc:
        return {"erro": f"conciliação não desfeita (nada foi alterado): {exc}"}

    return {
        "conciliacao_id": int(conciliacao_id),
        "status": "desfeita",
        "faturas": resultado_faturas,
        "transacoes": resultado_transacoes,
    }


# --------------------------------------------------------------------------
# Validação contábil de extrato
# --------------------------------------------------------------------------


def validar_extrato(db: sqlite3.Connection, lote_id: int) -> List[Dict[str, Any]]:
    """Equação contábil do lote confirmado, centavo a centavo.

    Usa os saldos gravados pelo lote (rótulos contendo "inicial"/"anterior" e
    "final") e as transações confirmadas do lote:
    ``inicial + créditos − débitos == final``. Sem os dois saldos, não há o que
    validar → ``[]``. Divergência devolve ``[{tipo:'saldo_divergente',
    esperado, calculado, diferenca}]`` onde ``esperado`` é o saldo final que o
    documento informou e ``calculado`` é o que as transações produzem.
    """
    saldos = db.execute(
        "SELECT * FROM saldos WHERE lote_id=? ORDER BY data, id", (lote_id,)
    ).fetchall()
    inicial = next(
        (
            s
            for s in saldos
            if "inicial" in str(s["rotulo"] or "") or "anterior" in str(s["rotulo"] or "")
        ),
        None,
    )
    final = next((s for s in saldos if "final" in str(s["rotulo"] or "")), None)
    if inicial is None or final is None:
        return []

    creditos = 0
    debitos = 0
    for linha in db.execute(
        "SELECT direcao, COALESCE(SUM(valor_centavos), 0) AS total"
        " FROM transacoes WHERE lote_id=? GROUP BY direcao",
        (lote_id,),
    ).fetchall():
        if linha["direcao"] == "credito":
            creditos = int(linha["total"])
        elif linha["direcao"] == "debito":
            debitos = int(linha["total"])

    calculado = int(inicial["valor_centavos"]) + creditos - debitos
    esperado = int(final["valor_centavos"])
    if calculado == esperado:
        return []
    diferenca = calculado - esperado
    return [
        {
            "tipo": "saldo_divergente",
            "esperado": esperado,
            "calculado": calculado,
            "diferenca": diferenca,
            "mensagem": (
                f"saldo final informado ({formata_centavos(esperado)}) difere do"
                f" calculado ({formata_centavos(calculado)}) por"
                f" {formata_centavos(diferenca)}"
            ),
        }
    ]
