"""Categorização de transações — sementes, regras determinísticas e divisões.

Regras são 100% determinísticas (nenhuma IA, nenhuma rede): ordenadas por
``prioridade ASC`` e a primeira que casa vence. A aplicação NUNCA sobrescreve
categoria definida manualmente (nem por importação): regra só preenche
transação sem categoria ou re-categoriza o que outra regra já preencheu
(``origem_categoria='regra'``). Toda escrita registra auditoria.

Dinheiro sempre em centavos ``int`` (ver ``dinheiro.py``) — nunca float.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Pattern, Sequence, Tuple

from .dinheiro import formata_centavos
from .modelos import ORIGENS_CATEGORIA, normaliza_descricao
from .store import registra_auditoria

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

#: Limite de texto avaliado por regex de regra — barreira simples contra
#: backtracking catastrófico (ReDoS): nenhum texto passa de 500 caracteres.
LIMITE_TEXTO_REGEX = 500

#: Confiança determinística por operador (mais específico = mais confiança).
CONFIANCA_POR_OPERADOR: Dict[str, float] = {
    "igual": 1.0,
    "comeca": 0.9,
    "contem": 0.85,
    "regex": 0.75,
}

CAMPOS_REGRA = ("descricao", "contraparte")
OPERADORES_REGRA = ("contem", "igual", "comeca", "regex")

#: Hierarquia padrão — 12 raízes (SPEC) com subcategorias de uso comum.
CATEGORIAS_PADRAO: Dict[str, Tuple[str, ...]] = {
    "Moradia": ("Aluguel", "Condomínio", "Energia", "Água", "Internet"),
    "Alimentação": ("Mercado", "Restaurantes", "Delivery"),
    "Transporte": ("Combustível", "Apps", "Estacionamento", "Transporte público"),
    "Saúde": ("Farmácia", "Consultas", "Plano de saúde"),
    "Lazer": ("Viagens", "Cinema e shows", "Hobbies"),
    "Educação": ("Cursos", "Livros", "Mensalidades"),
    "Assinaturas": ("Streaming", "Software", "Notícias"),
    "Impostos e Taxas": ("Impostos", "Tarifas bancárias", "Juros e multas"),
    "Investimentos": ("Aportes", "Resgates"),
    "Renda": ("Salário", "Rendimentos", "Reembolsos"),
    "Transferências": ("Entre contas próprias", "Terceiros"),
    "Outros": (),
}


# --------------------------------------------------------------------------
# Sementes
# --------------------------------------------------------------------------


def _garante_categoria(
    db: sqlite3.Connection, nome: str, pai_id: Optional[int]
) -> Tuple[int, bool]:
    """Devolve (id, criada). ``UNIQUE(nome, pai_id)`` não bloqueia raízes
    duplicadas no SQLite (NULLs são distintos no índice), então a existência
    é verificada explicitamente com o operador ``IS``."""
    linha = db.execute(
        "SELECT id FROM categorias WHERE nome=? AND pai_id IS ?", (nome, pai_id)
    ).fetchone()
    if linha is not None:
        return int(linha["id"]), False
    cursor = db.execute(
        "INSERT INTO categorias (nome, pai_id) VALUES (?, ?)", (nome, pai_id)
    )
    return int(cursor.lastrowid), True


def semear_categorias_padrao(db: sqlite3.Connection) -> None:
    """Semeia a hierarquia padrão de categorias. Idempotente: rodar de novo
    não duplica nada nem mexe em categorias criadas pelo usuário."""
    criadas = 0
    with db:
        for raiz, filhas in CATEGORIAS_PADRAO.items():
            raiz_id, nova = _garante_categoria(db, raiz, None)
            criadas += int(nova)
            for filha in filhas:
                _, nova_filha = _garante_categoria(db, filha, raiz_id)
                criadas += int(nova_filha)
        if criadas:
            registra_auditoria(
                db,
                "categorias",
                None,
                "categorias_semeadas",
                valor_novo=str(criadas),
                origem="sistema",
            )


# --------------------------------------------------------------------------
# Regras — avaliação
# --------------------------------------------------------------------------


def _compila_regras(
    db: sqlite3.Connection, avisos: List[str]
) -> List[Tuple[sqlite3.Row, Optional[Pattern[str]]]]:
    """Regras ativas em ordem de prioridade; regex inválida é PULADA com aviso
    (conteúdo ruim nunca vira exceção)."""
    regras = db.execute(
        "SELECT * FROM regras_categoria WHERE ativo=1 ORDER BY prioridade ASC, id ASC"
    ).fetchall()
    compiladas: List[Tuple[sqlite3.Row, Optional[Pattern[str]]]] = []
    for regra in regras:
        padrao: Optional[Pattern[str]] = None
        if regra["operador"] == "regex":
            try:
                padrao = re.compile(regra["valor"], re.IGNORECASE)
            except re.error as exc:
                avisos.append(
                    f"regra {regra['id']} pulada: regex inválida ({exc})"
                )
                continue
        compiladas.append((regra, padrao))
    return compiladas


def _texto_do_campo(regra: sqlite3.Row, transacao: sqlite3.Row) -> str:
    if regra["campo"] == "contraparte":
        return str(transacao["contraparte"] or "")
    return str(
        transacao["descricao_original"] or transacao["descricao_normalizada"] or ""
    )


def _regra_casa(
    regra: sqlite3.Row, padrao: Optional[Pattern[str]], transacao: sqlite3.Row
) -> bool:
    """A regra casa com a transação? Restrições de conta/cartão/faixa de valor
    são eliminatórias; depois o operador decide sobre o texto do campo."""
    if regra["conta_id"] is not None and regra["conta_id"] != transacao["conta_id"]:
        return False
    if regra["cartao_id"] is not None and regra["cartao_id"] != transacao["cartao_id"]:
        return False
    valor = int(transacao["valor_centavos"])
    if regra["valor_min_centavos"] is not None and valor < int(regra["valor_min_centavos"]):
        return False
    if regra["valor_max_centavos"] is not None and valor > int(regra["valor_max_centavos"]):
        return False

    bruto = _texto_do_campo(regra, transacao)
    operador = str(regra["operador"])
    if operador == "regex":
        if padrao is None:
            return False
        # Texto truncado: timeout implícito contra regex patológica.
        return padrao.search(bruto[:LIMITE_TEXTO_REGEX]) is not None

    alvo = normaliza_descricao(bruto)
    referencia = normaliza_descricao(str(regra["valor"]))
    if not referencia:
        return False  # regra vazia não casa com tudo por acidente
    if operador == "contem":
        return referencia in alvo
    if operador == "igual":
        return alvo == referencia
    if operador == "comeca":
        return alvo.startswith(referencia)
    return False


def aplicar_regras(
    db: sqlite3.Connection,
    transacao_ids: Optional[Sequence[int]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Aplica as regras ativas às transações elegíveis.

    Elegível = sem categoria OU com ``origem_categoria='regra'`` (recategorizável).
    Categoria de origem ``manual`` ou ``importacao`` NUNCA é tocada — decisão
    humana vence máquina, sempre.

    ``dry_run=True`` devolve só a prévia, sem gravar nada. ``dry_run=False``
    grava ``categoria_id``/``origem_categoria='regra'``/``confianca`` e audita
    cada transação com origem ``regra:<id>``.
    """
    avisos: List[str] = []
    regras = _compila_regras(db, avisos)

    sql = (
        "SELECT id, conta_id, cartao_id, valor_centavos, descricao_original,"
        " descricao_normalizada, contraparte, categoria_id, origem_categoria"
        " FROM transacoes"
        " WHERE (categoria_id IS NULL OR origem_categoria='regra')"
    )
    params: List[Any] = []
    if transacao_ids is not None:
        ids = [int(i) for i in transacao_ids]
        if not ids:
            return {"dry_run": dry_run, "previa": [], "aplicadas": 0, "avisos": avisos}
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    sql += " ORDER BY id"
    transacoes = db.execute(sql, params).fetchall()

    previa: List[Dict[str, Any]] = []
    for transacao in transacoes:
        for regra, padrao in regras:
            if _regra_casa(regra, padrao, transacao):
                previa.append(
                    {
                        "transacao_id": int(transacao["id"]),
                        "categoria_atual": transacao["categoria_id"],
                        "categoria_sugerida": int(regra["categoria_id"]),
                        "regra_id": int(regra["id"]),
                        "confianca": CONFIANCA_POR_OPERADOR.get(
                            str(regra["operador"]), 0.5
                        ),
                    }
                )
                break  # primeira regra que casa vence

    if dry_run:
        return {"dry_run": True, "previa": previa, "aplicadas": 0, "avisos": avisos}

    aplicadas = 0
    try:
        with db:
            for item in previa:
                db.execute(
                    "UPDATE transacoes SET categoria_id=?, origem_categoria='regra',"
                    " confianca=? WHERE id=?",
                    (item["categoria_sugerida"], item["confianca"], item["transacao_id"]),
                )
                registra_auditoria(
                    db,
                    "transacao",
                    item["transacao_id"],
                    "categoria_aplicada",
                    campo="categoria_id",
                    valor_anterior=(
                        None
                        if item["categoria_atual"] is None
                        else str(item["categoria_atual"])
                    ),
                    valor_novo=str(item["categoria_sugerida"]),
                    origem=f"regra:{item['regra_id']}",
                )
                aplicadas += 1
    except sqlite3.Error as exc:
        return {
            "dry_run": False,
            "previa": previa,
            "aplicadas": 0,
            "avisos": avisos + [f"aplicação interrompida — nada gravado: {exc}"],
            "erro": f"aplicação de regras falhou: {exc}",
        }
    return {"dry_run": False, "previa": previa, "aplicadas": aplicadas, "avisos": avisos}


# --------------------------------------------------------------------------
# Categoria manual e divisões
# --------------------------------------------------------------------------


def definir_categoria(
    db: sqlite3.Connection,
    transacao_id: int,
    categoria_id: Optional[int],
    origem: str = "manual",
    justificativa: Optional[str] = None,
) -> Dict[str, Any]:
    """Define (ou limpa, com ``categoria_id=None``) a categoria de uma
    transação. Sempre auditada — com justificativa quando fornecida."""
    if origem not in ORIGENS_CATEGORIA:
        return {"erro": f"origem inválida: {origem!r} (use {', '.join(ORIGENS_CATEGORIA)})"}
    transacao = db.execute(
        "SELECT id, categoria_id, origem_categoria FROM transacoes WHERE id=?",
        (transacao_id,),
    ).fetchone()
    if transacao is None:
        return {"erro": f"transação {transacao_id} não encontrada"}
    if categoria_id is not None:
        categoria = db.execute(
            "SELECT id FROM categorias WHERE id=?", (categoria_id,)
        ).fetchone()
        if categoria is None:
            return {"erro": f"categoria {categoria_id} não encontrada"}

    origem_nova = origem if categoria_id is not None else None
    with db:
        db.execute(
            "UPDATE transacoes SET categoria_id=?, origem_categoria=?, confianca=?"
            " WHERE id=?",
            (
                categoria_id,
                origem_nova,
                1.0 if categoria_id is not None else None,
                transacao_id,
            ),
        )
        registra_auditoria(
            db,
            "transacao",
            transacao_id,
            "categoria_definida",
            campo="categoria_id",
            valor_anterior=(
                None if transacao["categoria_id"] is None else str(transacao["categoria_id"])
            ),
            valor_novo=None if categoria_id is None else str(categoria_id),
            origem=origem,
            justificativa=justificativa,
        )
    return {
        "transacao_id": int(transacao_id),
        "categoria_id": categoria_id,
        "origem_categoria": origem_nova,
    }


def dividir_transacao(
    db: sqlite3.Connection, transacao_id: int, partes: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Divide uma transação em partes categorizadas.

    A soma das partes deve bater EXATAMENTE com ``valor_centavos`` da
    transação (centavo a centavo) — senão devolve erro claro, sem gravar.
    Divisões anteriores são substituídas (não somadas). Auditado.
    """
    transacao = db.execute(
        "SELECT id, valor_centavos, moeda FROM transacoes WHERE id=?", (transacao_id,)
    ).fetchone()
    if transacao is None:
        return {"erro": f"transação {transacao_id} não encontrada"}
    if not partes:
        return {"erro": "divisão exige ao menos uma parte {categoria_id, valor_centavos}"}

    normalizadas: List[Tuple[int, int]] = []
    for indice, parte in enumerate(partes, start=1):
        valor = parte.get("valor_centavos")
        if isinstance(valor, bool) or not isinstance(valor, int):
            return {
                "erro": (
                    f"parte {indice}: valor_centavos deve ser inteiro em centavos"
                    f" (recebido {valor!r}) — float é proibido em dinheiro"
                )
            }
        if valor <= 0:
            return {"erro": f"parte {indice}: valor_centavos deve ser positivo"}
        categoria_id = parte.get("categoria_id")
        categoria = (
            db.execute("SELECT id FROM categorias WHERE id=?", (categoria_id,)).fetchone()
            if categoria_id is not None
            else None
        )
        if categoria is None:
            return {"erro": f"parte {indice}: categoria {categoria_id!r} não encontrada"}
        normalizadas.append((int(categoria_id), valor))

    soma = sum(valor for _, valor in normalizadas)
    valor_total = int(transacao["valor_centavos"])
    moeda = str(transacao["moeda"] or "BRL")
    if soma != valor_total:
        return {
            "erro": (
                f"soma das partes ({formata_centavos(soma, moeda)}) difere do valor da"
                f" transação ({formata_centavos(valor_total, moeda)}) — diferença de"
                f" {formata_centavos(soma - valor_total, moeda)}"
            )
        }

    anteriores = db.execute(
        "SELECT categoria_id, valor_centavos FROM divisoes_transacao"
        " WHERE transacao_id=? ORDER BY id",
        (transacao_id,),
    ).fetchall()
    with db:
        db.execute("DELETE FROM divisoes_transacao WHERE transacao_id=?", (transacao_id,))
        divisoes: List[Dict[str, Any]] = []
        for categoria_id, valor in normalizadas:
            cursor = db.execute(
                "INSERT INTO divisoes_transacao (transacao_id, categoria_id,"
                " valor_centavos) VALUES (?,?,?)",
                (transacao_id, categoria_id, valor),
            )
            divisoes.append(
                {
                    "id": int(cursor.lastrowid),
                    "categoria_id": categoria_id,
                    "valor_centavos": valor,
                }
            )
        registra_auditoria(
            db,
            "transacao",
            transacao_id,
            "transacao_dividida",
            campo="divisoes",
            valor_anterior=(
                json.dumps(
                    [
                        {"categoria_id": a["categoria_id"], "valor_centavos": a["valor_centavos"]}
                        for a in anteriores
                    ],
                    ensure_ascii=False,
                )
                if anteriores
                else None
            ),
            valor_novo=json.dumps(
                [
                    {"categoria_id": c, "valor_centavos": v}
                    for c, v in normalizadas
                ],
                ensure_ascii=False,
            ),
            origem="manual",
        )
    return {"transacao_id": int(transacao_id), "divisoes": divisoes}


# --------------------------------------------------------------------------
# Árvore
# --------------------------------------------------------------------------


def arvore_categorias(db: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Árvore aninhada de categorias com contagem de uso (transações inteiras
    + partes de divisões que apontam para a categoria)."""
    linhas = db.execute(
        "SELECT c.id, c.nome, c.pai_id, c.cor,"
        " (SELECT COUNT(*) FROM transacoes t WHERE t.categoria_id = c.id)"
        " + (SELECT COUNT(*) FROM divisoes_transacao d WHERE d.categoria_id = c.id)"
        " AS uso"
        " FROM categorias c ORDER BY c.nome COLLATE NOCASE, c.id"
    ).fetchall()
    nos: Dict[int, Dict[str, Any]] = {}
    for linha in linhas:
        nos[int(linha["id"])] = {
            "id": int(linha["id"]),
            "nome": str(linha["nome"]),
            "cor": linha["cor"],
            "uso": int(linha["uso"]),
            "filhas": [],
        }
    raizes: List[Dict[str, Any]] = []
    for linha in linhas:
        no = nos[int(linha["id"])]
        pai_id = linha["pai_id"]
        if pai_id is not None and int(pai_id) in nos:
            nos[int(pai_id)]["filhas"].append(no)
        else:
            raizes.append(no)
    return raizes


# --------------------------------------------------------------------------
# CRUD de regras (criar/editar/desativar) — tudo auditado
# --------------------------------------------------------------------------

_CAMPOS_EDITAVEIS_REGRA = (
    "prioridade",
    "ativo",
    "campo",
    "operador",
    "valor",
    "conta_id",
    "cartao_id",
    "valor_min_centavos",
    "valor_max_centavos",
    "categoria_id",
)


def _valida_regra(db: sqlite3.Connection, dados: Dict[str, Any]) -> Optional[str]:
    """Devolve a mensagem de erro, ou None quando os dados são válidos."""
    if dados.get("campo") not in CAMPOS_REGRA:
        return f"campo inválido: {dados.get('campo')!r} (use {', '.join(CAMPOS_REGRA)})"
    if dados.get("operador") not in OPERADORES_REGRA:
        return (
            f"operador inválido: {dados.get('operador')!r}"
            f" (use {', '.join(OPERADORES_REGRA)})"
        )
    valor = dados.get("valor")
    if not isinstance(valor, str) or not valor.strip():
        return "valor da regra é obrigatório (texto não vazio)"
    if dados["operador"] == "regex":
        try:
            re.compile(valor)
        except re.error as exc:
            return f"regex inválida: {exc}"
    categoria_id = dados.get("categoria_id")
    if (
        categoria_id is None
        or db.execute("SELECT id FROM categorias WHERE id=?", (categoria_id,)).fetchone()
        is None
    ):
        return f"categoria {categoria_id!r} não encontrada"
    return None


def criar_regra(db: sqlite3.Connection, dados: Dict[str, Any]) -> Dict[str, Any]:
    """Cria uma regra de categorização. Auditada."""
    erro = _valida_regra(db, dados)
    if erro:
        return {"erro": erro}
    try:
        with db:
            cursor = db.execute(
                "INSERT INTO regras_categoria (prioridade, ativo, campo, operador,"
                " valor, conta_id, cartao_id, valor_min_centavos, valor_max_centavos,"
                " categoria_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    int(dados.get("prioridade", 100)),
                    1 if dados.get("ativo", True) else 0,
                    dados["campo"],
                    dados["operador"],
                    dados["valor"],
                    dados.get("conta_id"),
                    dados.get("cartao_id"),
                    dados.get("valor_min_centavos"),
                    dados.get("valor_max_centavos"),
                    int(dados["categoria_id"]),
                ),
            )
            regra_id = int(cursor.lastrowid)
            registra_auditoria(
                db,
                "regra_categoria",
                regra_id,
                "regra_criada",
                valor_novo=json.dumps(
                    {k: dados.get(k) for k in _CAMPOS_EDITAVEIS_REGRA},
                    ensure_ascii=False,
                ),
                origem="manual",
            )
    except sqlite3.Error as exc:
        return {"erro": f"regra não criada: {exc}"}
    linha = db.execute(
        "SELECT * FROM regras_categoria WHERE id=?", (regra_id,)
    ).fetchone()
    return dict(linha)


def editar_regra(
    db: sqlite3.Connection, regra_id: int, mudancas: Dict[str, Any]
) -> Dict[str, Any]:
    """Edita campos de uma regra existente; cada campo alterado vira um evento
    de auditoria (valor_anterior → valor_novo)."""
    atual = db.execute(
        "SELECT * FROM regras_categoria WHERE id=?", (regra_id,)
    ).fetchone()
    if atual is None:
        return {"erro": f"regra {regra_id} não encontrada"}
    invalidos = [c for c in mudancas if c not in _CAMPOS_EDITAVEIS_REGRA]
    if invalidos:
        return {"erro": f"campos não editáveis: {', '.join(sorted(invalidos))}"}

    projetada = {c: atual[c] for c in _CAMPOS_EDITAVEIS_REGRA}
    projetada.update(mudancas)
    erro = _valida_regra(db, projetada)
    if erro:
        return {"erro": erro}

    alterados = {
        campo: valor for campo, valor in mudancas.items() if valor != atual[campo]
    }
    if alterados:
        try:
            with db:
                atribuicoes = ", ".join(f"{campo}=?" for campo in alterados)
                db.execute(
                    f"UPDATE regras_categoria SET {atribuicoes} WHERE id=?",
                    [*alterados.values(), regra_id],
                )
                for campo, valor in alterados.items():
                    registra_auditoria(
                        db,
                        "regra_categoria",
                        regra_id,
                        "regra_editada",
                        campo=campo,
                        valor_anterior=(
                            None if atual[campo] is None else str(atual[campo])
                        ),
                        valor_novo=None if valor is None else str(valor),
                        origem="manual",
                    )
        except sqlite3.Error as exc:
            return {"erro": f"regra não editada: {exc}"}
    linha = db.execute(
        "SELECT * FROM regras_categoria WHERE id=?", (regra_id,)
    ).fetchone()
    return dict(linha)


def desativar_regra(
    db: sqlite3.Connection, regra_id: int, justificativa: Optional[str] = None
) -> Dict[str, Any]:
    """Desativa uma regra (nunca apaga — histórico e auditoria ficam)."""
    atual = db.execute(
        "SELECT * FROM regras_categoria WHERE id=?", (regra_id,)
    ).fetchone()
    if atual is None:
        return {"erro": f"regra {regra_id} não encontrada"}
    if atual["ativo"]:
        with db:
            db.execute("UPDATE regras_categoria SET ativo=0 WHERE id=?", (regra_id,))
            registra_auditoria(
                db,
                "regra_categoria",
                regra_id,
                "regra_desativada",
                campo="ativo",
                valor_anterior="1",
                valor_novo="0",
                origem="manual",
                justificativa=justificativa,
            )
    return {"id": int(regra_id), "ativo": False}


def listar_regras(db: sqlite3.Connection, apenas_ativas: bool = False) -> List[Dict[str, Any]]:
    """Regras em ordem de aplicação (prioridade ASC, id ASC)."""
    sql = "SELECT * FROM regras_categoria"
    if apenas_ativas:
        sql += " WHERE ativo=1"
    sql += " ORDER BY prioridade ASC, id ASC"
    return [dict(linha) for linha in db.execute(sql).fetchall()]
