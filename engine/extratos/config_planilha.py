"""Importação da planilha de configuração — prévia e confirmação.

Fluxo em duas etapas, como o resto do módulo:

- :func:`previa_config` — lê a planilha (via ``parsers.planilha_config.parse_config``,
  sem passar pelo pipeline de transações) e compara com o banco: o que é novo,
  o que muda campo a campo, o que CONFLITA (valor cadastrado ≠ valor da
  planilha) e o que está duplicado dentro do próprio arquivo. NADA é gravado.
- :func:`confirmar_config` — grava tudo numa transação atômica: upsert de
  instituições/contas/cartões (NUNCA sobrescrevendo campo já preenchido com
  ``None``; conflito real só é aplicado com ``sobrescrever=True``), upsert de
  faturas por ``(cartao_id, vence_em)``, versão em ``config_versoes``,
  auditoria de cada mudança e o arquivo original em ``arquivos``/storage.

Dinheiro sempre em centavos ``int``; datas ISO no banco.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Any, Dict, List, Optional

from . import storage
from .detect import detectar
from .modelos import Fatura, deriva_status_fatura, normaliza_descricao
from .parsers.planilha_config import (
    CartaoConfig,
    ConfigExtraida,
    ContaConfig,
    FaturaConfig,
    parse_config,
)
from .store import registra_auditoria

#: Origem gravada em auditoria/faturas para rastrear de onde veio a mudança.
_ORIGEM = "planilha_config"
#: Identificação do parser gravada em ``arquivos.versao_parser``.
_VERSAO_PARSER = "planilha-config/1"
#: Campos de cartão que a planilha pode preencher (whitelist para UPDATE).
_CAMPOS_CARTAO = (
    "emissor",
    "bandeira",
    "final",
    "dia_fechamento",
    "dia_vencimento",
    "limite_total_centavos",
)
#: Campos de fatura que a confirmação pode atualizar (whitelist para UPDATE).
_CAMPOS_FATURA = ("pago_centavos", "status")


# --------------------------------------------------------------------------
# Buscas tolerantes a caixa/acento (o nome no banco manda)
# --------------------------------------------------------------------------


def _busca_cartao(db: sqlite3.Connection, nome: str) -> Optional[sqlite3.Row]:
    linha = db.execute("SELECT * FROM cartoes WHERE nome = ?", (nome,)).fetchone()
    if linha is not None:
        return linha
    alvo = normaliza_descricao(nome)
    for candidata in db.execute("SELECT * FROM cartoes"):
        if normaliza_descricao(candidata["nome"]) == alvo:
            return candidata
    return None


def _busca_conta(db: sqlite3.Connection, nome: str) -> Optional[sqlite3.Row]:
    linha = db.execute("SELECT * FROM contas WHERE nome = ?", (nome,)).fetchone()
    if linha is not None:
        return linha
    alvo = normaliza_descricao(nome)
    for candidata in db.execute("SELECT * FROM contas"):
        if normaliza_descricao(candidata["nome"]) == alvo:
            return candidata
    return None


# --------------------------------------------------------------------------
# Planejamento (compartilhado entre prévia e confirmação)
# --------------------------------------------------------------------------


def _planeja(
    db: sqlite3.Connection, config: ConfigExtraida, hoje: date
) -> Dict[str, Any]:
    """Compara a extração com o banco — só leitura, nada é gravado.

    As listas ``cartoes_novos``/``contas_novas``/``faturas_novas`` carregam os
    dataclasses extraídos (a confirmação precisa deles); a prévia converte
    para JSON via :func:`_previa_json`.
    """
    plano: Dict[str, Any] = {
        "mapeamento": dict(config.mapeamento),
        "cartoes_novos": [],
        "cartoes_atualizados": [],
        "contas_novas": [],
        "faturas_novas": [],
        "faturas_atualizadas": [],
        "conflitos": [],
        "duplicidades": [],
        "erros": [dict(erro) for erro in config.erros],
        "avisos": list(config.avisos),
    }

    for cartao in config.cartoes:
        existente = _busca_cartao(db, cartao.nome)
        if existente is None:
            plano["cartoes_novos"].append(cartao)
            continue
        diff: Dict[str, Dict[str, Any]] = {}
        for campo in _CAMPOS_CARTAO:
            novo = getattr(cartao, campo)
            if novo is None:
                continue  # a planilha não informa: campo manual fica intocado
            atual = existente[campo]
            if atual is None or atual == "":
                diff[campo] = {"de": atual, "para": novo}
            elif atual != novo:
                plano["conflitos"].append({
                    "entidade": "cartao",
                    "id": existente["id"],
                    "nome": existente["nome"],
                    "campo": campo,
                    "valor_atual": atual,
                    "valor_planilha": novo,
                })
        if diff:
            plano["cartoes_atualizados"].append({
                "id": existente["id"],
                "nome": existente["nome"],
                "diff": diff,
            })

    for conta in config.contas:
        if _busca_conta(db, conta.nome) is None:
            plano["contas_novas"].append(conta)

    # Duplicidade dentro do próprio arquivo: mesma chave (cartão, vencimento).
    unicas: Dict[Any, FaturaConfig] = {}
    for fatura in config.faturas:
        chave = (normaliza_descricao(fatura.cartao), fatura.vence_em.isoformat())
        if chave in unicas:
            plano["duplicidades"].append({
                "cartao": fatura.cartao,
                "vence_em": fatura.vence_em.isoformat(),
                "origem_ref": fatura.origem_ref,
                "mensagem": "linha duplicada na planilha — a última prevalece",
            })
        unicas[chave] = fatura

    for fatura in unicas.values():
        cartao_existente = _busca_cartao(db, fatura.cartao)
        if cartao_existente is None:
            plano["faturas_novas"].append(fatura)  # o cartão nasce junto
            continue
        existente = db.execute(
            "SELECT * FROM faturas WHERE cartao_id = ? AND vence_em = ?",
            (cartao_existente["id"], fatura.vence_em.isoformat()),
        ).fetchone()
        if existente is None:
            plano["faturas_novas"].append(fatura)
            continue
        fecha_em = (
            date.fromisoformat(existente["fecha_em"]) if existente["fecha_em"] else None
        )
        # O status respeita o valor total já conhecido no banco (se houver).
        status_novo = deriva_status_fatura(
            Fatura(
                cartao_id=existente["cartao_id"],
                competencia=existente["competencia"],
                vence_em=fatura.vence_em,
                fecha_em=fecha_em,
                valor_centavos=existente["valor_centavos"],
                pago_centavos=fatura.pago_centavos,
            ),
            hoje,
        )
        diff = {}
        if int(existente["pago_centavos"] or 0) != fatura.pago_centavos:
            diff["pago_centavos"] = {
                "de": existente["pago_centavos"],
                "para": fatura.pago_centavos,
            }
        if existente["status"] != status_novo:
            diff["status"] = {"de": existente["status"], "para": status_novo}
        if diff:
            plano["faturas_atualizadas"].append({
                "id": existente["id"],
                "cartao": cartao_existente["nome"],
                "vence_em": fatura.vence_em.isoformat(),
                "diff": diff,
            })

    return plano


def _cartao_json(cartao: CartaoConfig) -> Dict[str, Any]:
    return asdict(cartao)


def _conta_json(conta: ContaConfig) -> Dict[str, Any]:
    return asdict(conta)


def _fatura_json(fatura: FaturaConfig) -> Dict[str, Any]:
    dados = asdict(fatura)
    dados["vence_em"] = fatura.vence_em.isoformat()
    return dados


def _previa_json(plano: Dict[str, Any]) -> Dict[str, Any]:
    """Plano interno → dict 100% serializável (contrato da API/UI)."""
    previa = dict(plano)
    previa["cartoes_novos"] = [_cartao_json(c) for c in plano["cartoes_novos"]]
    previa["contas_novas"] = [_conta_json(c) for c in plano["contas_novas"]]
    previa["faturas_novas"] = [_fatura_json(f) for f in plano["faturas_novas"]]
    return previa


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------


def previa_config(
    db: sqlite3.Connection,
    conteudo: bytes,
    nome: str,
    *,
    hoje: Optional[date] = None,
) -> Dict[str, Any]:
    """Prévia da importação da planilha de configuração — NADA é gravado.

    Devolve ``{mapeamento, cartoes_novos, cartoes_atualizados (diff campo a
    campo), contas_novas, faturas_novas, faturas_atualizadas, conflitos,
    duplicidades, erros, avisos}``.
    """
    referencia = hoje or date.today()
    config = parse_config(conteudo, nome, hoje=referencia)
    return _previa_json(_planeja(db, config, referencia))


def confirmar_config(
    db: sqlite3.Connection,
    conteudo: bytes,
    nome: str,
    aprovado_por: str = "usuario",
    *,
    sobrescrever: bool = False,
    hoje: Optional[date] = None,
) -> Dict[str, Any]:
    """Grava a configuração da planilha numa transação atômica.

    - Campo já preenchido no banco NUNCA é apagado por ``None`` da planilha.
    - Conflito real (valor cadastrado ≠ valor da planilha) só é aplicado com
      ``sobrescrever=True`` — senão fica em ``conflitos_pendentes``.
    - Cada criação/mudança gera evento de auditoria; a versão vai para
      ``config_versoes`` com resumo e ``aprovado_por``; o arquivo original vai
      para o storage imutável e para ``arquivos`` (status ``confirmado``).
    """
    referencia = hoje or date.today()
    config = parse_config(conteudo, nome, hoje=referencia)
    plano = _planeja(db, config, referencia)
    formato = detectar(conteudo, nome)
    sha256, caminho_objeto = storage.gravar(conteudo, nome)

    conflitos_aplicados: List[Dict[str, Any]] = []
    conflitos_pendentes: List[Dict[str, Any]] = []
    avisos: List[str] = list(plano["avisos"])

    with db:  # transação atômica: ou grava tudo, ou nada
        arquivo_id = _upsert_arquivo(
            db, sha256, nome, formato.mime, len(conteudo), caminho_objeto
        )

        # Instituições e contas primeiro: cartões podem referenciá-las.
        for conta in plano["contas_novas"]:
            instituicao_id = (
                _garante_instituicao(db, conta.instituicao)
                if conta.instituicao
                else None
            )
            cursor = db.execute(
                "INSERT INTO contas (instituicao_id, nome, tipo, moeda)"
                " VALUES (?,?,?,?)",
                (instituicao_id, conta.nome, conta.tipo, conta.moeda),
            )
            registra_auditoria(
                db, "conta", cursor.lastrowid, "criar",
                origem=_ORIGEM, justificativa=f"planilha '{nome}'",
            )

        ids_cartao: Dict[str, int] = {}
        for cartao in plano["cartoes_novos"]:
            instituicao_id = (
                _garante_instituicao(db, cartao.instituicao)
                if cartao.instituicao
                else None
            )
            conta_pagadora_id: Optional[int] = None
            if cartao.conta_pagadora:
                conta_row = _busca_conta(db, cartao.conta_pagadora)
                if conta_row is not None:
                    conta_pagadora_id = int(conta_row["id"])
                else:
                    avisos.append(
                        f"conta pagadora '{cartao.conta_pagadora}' do cartão "
                        f"'{cartao.nome}' não encontrada — deixada em branco"
                    )
            cursor = db.execute(
                "INSERT INTO cartoes (instituicao_id, nome, emissor, bandeira,"
                " final, conta_pagadora_id, dia_fechamento, dia_vencimento,"
                " limite_total_centavos) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    instituicao_id,
                    cartao.nome,
                    cartao.emissor,
                    cartao.bandeira,
                    cartao.final,
                    conta_pagadora_id,
                    cartao.dia_fechamento,
                    cartao.dia_vencimento,
                    cartao.limite_total_centavos,
                ),
            )
            cartao_id = int(cursor.lastrowid or 0)
            ids_cartao[normaliza_descricao(cartao.nome)] = cartao_id
            registra_auditoria(
                db, "cartao", cartao_id, "criar",
                origem=_ORIGEM, justificativa=f"planilha '{nome}'",
            )

        for item in plano["cartoes_atualizados"]:
            for campo, mudanca in item["diff"].items():
                if campo not in _CAMPOS_CARTAO:  # cinto e suspensório
                    continue
                db.execute(
                    f"UPDATE cartoes SET {campo} = ? WHERE id = ?",
                    (mudanca["para"], item["id"]),
                )
                registra_auditoria(
                    db, "cartao", item["id"], "atualizar", campo=campo,
                    valor_anterior=_texto_auditoria(mudanca["de"]),
                    valor_novo=_texto_auditoria(mudanca["para"]),
                    origem=_ORIGEM,
                )

        for conflito in plano["conflitos"]:
            campo = conflito["campo"]
            if sobrescrever and conflito["entidade"] == "cartao" and campo in _CAMPOS_CARTAO:
                db.execute(
                    f"UPDATE cartoes SET {campo} = ? WHERE id = ?",
                    (conflito["valor_planilha"], conflito["id"]),
                )
                registra_auditoria(
                    db, "cartao", conflito["id"], "sobrescrever", campo=campo,
                    valor_anterior=_texto_auditoria(conflito["valor_atual"]),
                    valor_novo=_texto_auditoria(conflito["valor_planilha"]),
                    origem=_ORIGEM,
                    justificativa=f"sobrescrita confirmada por {aprovado_por}",
                )
                conflitos_aplicados.append(conflito)
            else:
                conflitos_pendentes.append(conflito)

        faturas_criadas = 0
        for fatura in plano["faturas_novas"]:
            chave = normaliza_descricao(fatura.cartao)
            cartao_id_fatura = ids_cartao.get(chave)
            if cartao_id_fatura is None:
                cartao_row = _busca_cartao(db, fatura.cartao)
                cartao_id_fatura = int(cartao_row["id"]) if cartao_row else None
            if cartao_id_fatura is None:
                plano["erros"].append({
                    "origem_ref": fatura.origem_ref,
                    "campo": "cartao",
                    "mensagem": (
                        f"cartão '{fatura.cartao}' não encontrado nem criado — "
                        "fatura ignorada"
                    ),
                })
                continue
            cursor = db.execute(
                "INSERT INTO faturas (cartao_id, competencia, vence_em,"
                " pago_centavos, status, origem) VALUES (?,?,?,?,?,?)",
                (
                    cartao_id_fatura,
                    fatura.competencia,
                    fatura.vence_em.isoformat(),
                    fatura.pago_centavos,
                    fatura.status,
                    _ORIGEM,
                ),
            )
            registra_auditoria(
                db, "fatura", cursor.lastrowid, "criar",
                origem=_ORIGEM, justificativa=f"planilha '{nome}'",
            )
            faturas_criadas += 1

        for item in plano["faturas_atualizadas"]:
            for campo, mudanca in item["diff"].items():
                if campo not in _CAMPOS_FATURA:  # cinto e suspensório
                    continue
                db.execute(
                    f"UPDATE faturas SET {campo} = ? WHERE id = ?",
                    (mudanca["para"], item["id"]),
                )
                registra_auditoria(
                    db, "fatura", item["id"], "atualizar", campo=campo,
                    valor_anterior=_texto_auditoria(mudanca["de"]),
                    valor_novo=_texto_auditoria(mudanca["para"]),
                    origem=_ORIGEM,
                )

        resumo = {
            "arquivo_sha256": sha256,
            "layout": plano["mapeamento"].get("layout"),
            "cartoes_criados": len(plano["cartoes_novos"]),
            "cartoes_atualizados": len(plano["cartoes_atualizados"]),
            "contas_criadas": len(plano["contas_novas"]),
            "faturas_criadas": faturas_criadas,
            "faturas_atualizadas": len(plano["faturas_atualizadas"]),
            "conflitos_aplicados": len(conflitos_aplicados),
            "conflitos_pendentes": len(conflitos_pendentes),
            "duplicidades": len(plano["duplicidades"]),
            "erros": len(plano["erros"]),
        }
        cursor = db.execute(
            "INSERT INTO config_versoes (arquivo_id, resumo_json, aprovado_por)"
            " VALUES (?,?,?)",
            (arquivo_id, json.dumps(resumo, ensure_ascii=False), aprovado_por),
        )
        versao_id = int(cursor.lastrowid or 0)
        registra_auditoria(
            db, "config_versao", versao_id, "criar",
            origem=_ORIGEM, justificativa=f"aprovado por {aprovado_por}",
        )

    return {
        "arquivo_id": arquivo_id,
        "versao_id": versao_id,
        "mapeamento": plano["mapeamento"],
        "cartoes_criados": len(plano["cartoes_novos"]),
        "cartoes_atualizados": len(plano["cartoes_atualizados"]),
        "contas_criadas": len(plano["contas_novas"]),
        "faturas_criadas": faturas_criadas,
        "faturas_atualizadas": len(plano["faturas_atualizadas"]),
        "conflitos_aplicados": conflitos_aplicados,
        "conflitos_pendentes": conflitos_pendentes,
        "duplicidades": plano["duplicidades"],
        "erros": plano["erros"],
        "avisos": avisos,
    }


# --------------------------------------------------------------------------
# Escritas auxiliares (sempre dentro da transação de confirmar_config)
# --------------------------------------------------------------------------


def _texto_auditoria(valor: Any) -> Optional[str]:
    return None if valor is None else str(valor)


def _garante_instituicao(db: sqlite3.Connection, nome: str) -> int:
    linha = db.execute(
        "SELECT id FROM instituicoes WHERE nome = ?", (nome,)
    ).fetchone()
    if linha is not None:
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO instituicoes (nome, tipo) VALUES (?, 'banco')", (nome,)
    )
    registra_auditoria(
        db, "instituicao", cursor.lastrowid, "criar", origem=_ORIGEM
    )
    return int(cursor.lastrowid or 0)


def _upsert_arquivo(
    db: sqlite3.Connection,
    sha256: str,
    nome: str,
    mime: str,
    tamanho: int,
    caminho_objeto: str,
) -> int:
    """Registra (ou reaproveita) o arquivo da planilha como ``confirmado``."""
    linha = db.execute(
        "SELECT id FROM arquivos WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if linha is not None:
        db.execute(
            "UPDATE arquivos SET status = 'confirmado', versao_parser = ?"
            " WHERE id = ?",
            (_VERSAO_PARSER, linha["id"]),
        )
        return int(linha["id"])
    cursor = db.execute(
        "INSERT INTO arquivos (sha256, nome_original, mime_real, tamanho,"
        " tipo_documento, status, versao_parser, caminho_objeto)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            sha256,
            nome,
            mime,
            tamanho,
            "planilha_config",
            "confirmado",
            _VERSAO_PARSER,
            caminho_objeto,
        ),
    )
    registra_auditoria(db, "arquivo", cursor.lastrowid, "criar", origem=_ORIGEM)
    return int(cursor.lastrowid or 0)
