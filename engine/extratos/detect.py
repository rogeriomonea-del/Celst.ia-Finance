"""Detecção do formato REAL de um arquivo — por conteúdo, nunca por extensão.

Bancos brasileiros exportam ".xls" que é HTML, ".csv" em cp1252, OFX SGML sem
XML e PDFs com o MIME errado. A extensão é só uma dica de última instância;
quem manda é a assinatura dos bytes.
"""

from __future__ import annotations

import csv as _csv
import io
import zipfile
from dataclasses import dataclass
from typing import Optional, Tuple

#: Formatos que o pipeline conhece (valores estáveis, gravados em ``arquivos.mime_real``).
FORMATOS = (
    "xlsx", "xls_biff", "xls_html", "pdf", "ofx", "xml", "html", "csv", "desconhecido",
)

MIME_POR_FORMATO = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls_biff": "application/vnd.ms-excel",
    "xls_html": "text/html",
    "pdf": "application/pdf",
    "ofx": "application/x-ofx",
    "xml": "application/xml",
    "html": "text/html",
    "csv": "text/csv",
    "desconhecido": "application/octet-stream",
}


@dataclass(frozen=True)
class FormatoDetectado:
    formato: str  # um de FORMATOS
    mime: str
    encoding: Optional[str] = None  # só para formatos textuais
    delimitador: Optional[str] = None  # só para csv
    detalhe: str = ""


def detectar(conteudo: bytes, nome_original: str = "") -> FormatoDetectado:
    """Classifica o conteúdo. Nunca levanta exceção — devolve ``desconhecido``."""
    if not conteudo:
        return FormatoDetectado("desconhecido", MIME_POR_FORMATO["desconhecido"], detalhe="arquivo vazio")

    cabeca = conteudo[:4096]

    if cabeca.startswith(b"%PDF"):
        return FormatoDetectado("pdf", MIME_POR_FORMATO["pdf"])

    if cabeca.startswith(b"PK\x03\x04"):
        return _classifica_zip(conteudo)

    if cabeca.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2 clássico: quase sempre XLS BIFF (não suportado — pedir re-export).
        return FormatoDetectado("xls_biff", MIME_POR_FORMATO["xls_biff"])

    texto, encoding = decodifica_texto(conteudo)
    if texto is None:
        return FormatoDetectado("desconhecido", MIME_POR_FORMATO["desconhecido"], detalhe="binário não reconhecido")

    inicio = texto[:8192].lstrip("﻿ \t\r\n").lower()

    if inicio.startswith("ofxheader") or "<ofx>" in inicio or inicio.startswith("<?ofx"):
        return FormatoDetectado("ofx", MIME_POR_FORMATO["ofx"], encoding=encoding)

    if inicio.startswith("<?xml"):
        if "<ofx" in inicio:
            return FormatoDetectado("ofx", MIME_POR_FORMATO["ofx"], encoding=encoding, detalhe="ofx 2.x (xml)")
        return FormatoDetectado("xml", MIME_POR_FORMATO["xml"], encoding=encoding)

    if inicio.startswith(("<!doctype html", "<html")) or "<table" in inicio:
        formato = "xls_html" if nome_original.lower().endswith((".xls", ".xlsx")) else "html"
        return FormatoDetectado(formato, MIME_POR_FORMATO["xls_html"], encoding=encoding)

    if inicio.startswith("<"):
        return FormatoDetectado("xml", MIME_POR_FORMATO["xml"], encoding=encoding, detalhe="xml sem prólogo")

    delimitador = _farejar_delimitador(texto)
    return FormatoDetectado("csv", MIME_POR_FORMATO["csv"], encoding=encoding, delimitador=delimitador)


def _classifica_zip(conteudo: bytes) -> FormatoDetectado:
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
            nomes = set(pacote.namelist())
    except zipfile.BadZipFile:
        return FormatoDetectado("desconhecido", MIME_POR_FORMATO["desconhecido"], detalhe="zip corrompido")
    if any(nome.startswith("xl/") for nome in nomes):
        # .xlsx e .xlsm têm a mesma estrutura; macros NUNCA são executadas
        # (openpyxl só lê valores), então tratamos os dois como planilha.
        return FormatoDetectado("xlsx", MIME_POR_FORMATO["xlsx"])
    return FormatoDetectado("desconhecido", MIME_POR_FORMATO["desconhecido"], detalhe="zip sem planilha")


def decodifica_texto(conteudo: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Decodifica priorizando UTF-8; cai para cp1252 e latin-1 (padrões BR).

    A ordem importa: cp1252 é superconjunto prático do latin-1 em extratos
    brasileiros (aspas curvas, travessão). latin-1 nunca falha, então fica por
    último como rede de segurança — e binário de verdade é barrado antes pelo
    teste de bytes de controle.
    """
    amostra = conteudo[:65536]
    # Heurística anti-binário: proporção alta de bytes de controle não-texto.
    controle = sum(1 for b in amostra if b < 9 or (13 < b < 32))
    if amostra and controle / len(amostra) > 0.05:
        return None, None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return conteudo.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, None


def _farejar_delimitador(texto: str) -> str:
    """Escolhe entre ``;``, ``,``, tab e ``|`` pelas primeiras linhas úteis."""
    linhas = [l for l in texto.splitlines()[:40] if l.strip()]
    if not linhas:
        return ";"
    try:
        dialeto = _csv.Sniffer().sniff("\n".join(linhas[:10]), delimiters=";,\t|")
        return dialeto.delimiter
    except _csv.Error:
        pass
    contagens = {d: sum(l.count(d) for l in linhas) for d in (";", ",", "\t", "|")}
    melhor = max(contagens, key=lambda d: contagens[d])
    return melhor if contagens[melhor] else ";"
