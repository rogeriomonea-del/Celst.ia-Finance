"""Extração de texto de PDF 100% stdlib para extratos e faturas com camada de texto.

O objetivo NÃO é renderizar PDF: é recuperar as linhas de texto de cada página
para aplicar a heurística de extrato (linhas com data + valor pt-BR no fim).

O que este módulo cobre:

- tokenizador de objetos PDF (dicionários, arrays, strings ``()``/``<>``,
  números, nomes, referências ``N G R``), xref clássica e streams de objetos
  (``/Type /ObjStm``); quando a xref falha, o arquivo inteiro é varrido por
  ``N G obj`` (recuperação de PDF truncado);
- páginas pela árvore ``/Pages`` (com recuperação por ``/Type /Page`` soltos);
  ``/Contents`` simples ou em array (streams concatenados);
- filtros de stream: FlateDecode (zlib, com preditores PNG), ASCIIHexDecode e
  ASCII85Decode — outros filtros marcam a página como ilegível com erro claro;
- operadores de texto BT/ET, Tf, Td, TD, TL, Tm, T*, Tj, TJ (kerning: número
  com módulo > 200 vira espaço), ``'`` e ``"``; trechos são agrupados por
  baseline Y (tolerância) e ordenados por X para reconstruir as linhas;
- decodificação de bytes: ``/ToUnicode`` (CMap bfchar/bfrange) quando existe;
  senão WinAnsi/Standard aproximados por latin-1;
- criptografia ``/Filter /Standard`` V1/V2 (RC4 40–128 bits): chave derivada
  pelo algoritmo 2 da especificação (MD5), testando a senha informada e a
  senha vazia (dona de muitos PDFs de banco). A senha é usada e descartada —
  NUNCA gravada. ``V >= 4`` (AES) devolve erro claro pedindo remover a senha.

Limitações documentadas (viram aviso/erro, nunca exceção):

- PDF sem NENHUM texto extraível é um documento digitalizado (só imagem).
  Não há rasterizador neste ambiente, então mesmo com o binário ``tesseract``
  no PATH não haveria imagem renderizada para OCR — o resultado é vazio com o
  aviso "PDF sem camada de texto (digitalizado)..." e erro estruturado.
- Sem métricas de glifo: a posição de cada trecho vem das matrizes de texto
  (Tm/Td), suficiente para extratos tabulares; texto artístico pode embaralhar.
- WinAnsi/Standard são aproximados por latin-1 (correto para o miolo pt-BR).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ...workbook import as_date
from ..dinheiro import parse_centavos
from ..modelos import (
    LinhaBruta,
    ResultadoParse,
    SaldoInformado,
    Transacao,
    fingerprint_transacao,
    normaliza_descricao,
)
from .base import ContextoParse

# --------------------------------------------------------------------------
# Mensagens de erro estáveis (testadas)
# --------------------------------------------------------------------------

MSG_AES = "PDF com criptografia AES não suportada — remova a senha e reenvie"
MSG_SENHA = "senha do PDF incorreta ou não informada — confira a senha e reenvie"
MSG_DIGITALIZADO = (
    "PDF sem camada de texto (digitalizado). OCR automático indisponível "
    "neste ambiente — converta com OCR externo e reenvie"
)
MSG_CORROMPIDO = (
    "arquivo corrompido ou não é um PDF válido — estrutura de objetos não encontrada"
)

# --------------------------------------------------------------------------
# Tokenizador de sintaxe PDF (objetos E content streams usam o mesmo)
# --------------------------------------------------------------------------

_ESPACOS = frozenset(b"\x00\t\n\x0c\r ")
_ESPACOS_BYTES = b"\x00\t\n\x0c\r "
_DELIMITADORES = frozenset(b"()<>[]{}/%")
_RE_NUMERO = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")

#: Um token: ("num", int|float) | ("str", bytes) | ("nome", str) |
#: ("arr_abre"|"arr_fecha"|"dict_abre"|"dict_fecha", None) | ("palavra", str)
Token = Tuple[str, Any]


class _Ref(NamedTuple):
    """Referência indireta ``N G R``."""

    num: int
    ger: int


class _Palavra(str):
    """Palavra-chave solta encontrada onde se esperava um valor."""


def _pula_brancos(dados: bytes, pos: int) -> int:
    """Avança sobre espaços e comentários ``%...`` até o próximo token."""
    n = len(dados)
    while pos < n:
        c = dados[pos]
        if c in _ESPACOS:
            pos += 1
        elif c == 0x25:  # '%' — comentário até o fim da linha
            fim = dados.find(b"\n", pos)
            if fim < 0:
                return n
            pos = fim + 1
        else:
            break
    return pos


def _le_string_literal(dados: bytes, pos: int) -> Tuple[bytes, int]:
    """Lê ``(...)`` com parênteses aninhados e escapes ``\\``. ``pos`` no '('."""
    saida = bytearray()
    profundidade = 1
    i = pos + 1
    n = len(dados)
    escapes = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
    while i < n and profundidade > 0:
        c = dados[i]
        if c == 0x5C:  # '\'
            i += 1
            if i >= n:
                break
            e = dados[i]
            if e in escapes:
                saida.append(escapes[e])
                i += 1
            elif e in b"()\\":
                saida.append(e)
                i += 1
            elif 0x30 <= e <= 0x37:  # octal de até 3 dígitos
                octal = chr(e)
                i += 1
                while i < n and len(octal) < 3 and 0x30 <= dados[i] <= 0x37:
                    octal += chr(dados[i])
                    i += 1
                saida.append(int(octal, 8) & 0xFF)
            elif e == 0x0D:  # '\' + quebra de linha = continuação
                i += 1
                if dados[i : i + 1] == b"\n":
                    i += 1
            elif e == 0x0A:
                i += 1
            else:
                saida.append(e)
                i += 1
        elif c == 0x28:  # '('
            profundidade += 1
            saida.append(c)
            i += 1
        elif c == 0x29:  # ')'
            profundidade -= 1
            if profundidade:
                saida.append(c)
            i += 1
        else:
            saida.append(c)
            i += 1
    return bytes(saida), i


def _le_string_hex(dados: bytes, pos: int) -> Tuple[bytes, int]:
    """Lê ``<hex>``. ``pos`` aponta para o '<'."""
    fim = dados.find(b">", pos + 1)
    if fim < 0:
        fim = len(dados)
    hexa = bytes(c for c in dados[pos + 1 : fim] if c not in _ESPACOS)
    if len(hexa) % 2:
        hexa += b"0"
    try:
        return binascii.unhexlify(hexa), min(fim + 1, len(dados))
    except binascii.Error:
        return b"", min(fim + 1, len(dados))


def _le_nome(dados: bytes, pos: int) -> Tuple[str, int]:
    """Lê ``/Nome`` (com escapes ``#xx``). ``pos`` aponta para o '/'."""
    i = pos + 1
    n = len(dados)
    saida = bytearray()
    while i < n:
        c = dados[i]
        if c in _ESPACOS or c in _DELIMITADORES:
            break
        if c == 0x23 and i + 2 < n:  # '#'
            try:
                saida.append(int(dados[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        saida.append(c)
        i += 1
    return saida.decode("latin-1"), i


def _proximo_token(dados: bytes, pos: int) -> Tuple[Optional[Token], int]:
    """Próximo token a partir de ``pos`` (ou ``None`` no fim)."""
    pos = _pula_brancos(dados, pos)
    n = len(dados)
    if pos >= n:
        return None, pos
    c = dados[pos]
    if c == 0x28:  # '('
        texto, fim = _le_string_literal(dados, pos)
        return ("str", texto), fim
    if c == 0x3C:  # '<'
        if dados[pos + 1 : pos + 2] == b"<":
            return ("dict_abre", None), pos + 2
        texto, fim = _le_string_hex(dados, pos)
        return ("str", texto), fim
    if c == 0x3E:  # '>'
        if dados[pos + 1 : pos + 2] == b">":
            return ("dict_fecha", None), pos + 2
        return ("palavra", ">"), pos + 1
    if c == 0x5B:  # '['
        return ("arr_abre", None), pos + 1
    if c == 0x5D:  # ']'
        return ("arr_fecha", None), pos + 1
    if c == 0x2F:  # '/'
        nome, fim = _le_nome(dados, pos)
        return ("nome", nome), fim
    if c in b"{}":
        return ("palavra", chr(c)), pos + 1
    if c in b"0123456789+-.":
        m = _RE_NUMERO.match(dados, pos)
        if m:
            bruto = m.group().decode("ascii")
            valor: Any = float(bruto) if b"." in m.group() else int(bruto)
            return ("num", valor), m.end()
    ini = pos
    while pos < n and dados[pos] not in _ESPACOS and dados[pos] not in _DELIMITADORES:
        pos += 1
    if pos == ini:  # delimitador inesperado solto
        return ("palavra", chr(c)), pos + 1
    return ("palavra", dados[ini:pos].decode("latin-1")), pos


def _analisa_valor(dados: bytes, pos: int, profundidade: int = 0) -> Tuple[Any, int]:
    """Analisa UM valor PDF (número, string, nome, array, dicionário, ref)."""
    token, apos = _proximo_token(dados, pos)
    if token is None or profundidade > 48:
        return None, apos
    tipo, valor = token
    if tipo == "num":
        if isinstance(valor, int) and valor >= 0:
            # Pode ser uma referência "N G R" — só confirma com lookahead.
            t2, p2 = _proximo_token(dados, apos)
            if t2 is not None and t2[0] == "num" and isinstance(t2[1], int) and t2[1] >= 0:
                t3, p3 = _proximo_token(dados, p2)
                if t3 == ("palavra", "R"):
                    return _Ref(valor, t2[1]), p3
        return valor, apos
    if tipo in ("str", "nome"):
        return valor, apos
    if tipo == "arr_abre":
        itens: List[Any] = []
        posicao = apos
        while True:
            t, p = _proximo_token(dados, posicao)
            if t is None or t[0] == "arr_fecha":
                return itens, p
            item, posicao = _analisa_valor(dados, posicao, profundidade + 1)
            itens.append(item)
    if tipo == "dict_abre":
        dicionario: Dict[str, Any] = {}
        posicao = apos
        while True:
            t, p = _proximo_token(dados, posicao)
            if t is None or t[0] == "dict_fecha":
                return dicionario, p
            if t[0] != "nome":
                posicao = p  # token estranho no lugar da chave — ignora
                continue
            chave = t[1]
            item, posicao = _analisa_valor(dados, p, profundidade + 1)
            dicionario[chave] = item
    # tipo == "palavra"
    if valor == "true":
        return True, apos
    if valor == "false":
        return False, apos
    if valor == "null":
        return None, apos
    return _Palavra(valor), apos


# --------------------------------------------------------------------------
# Objetos indiretos, xref clássica e varredura de recuperação
# --------------------------------------------------------------------------


@dataclass
class _ObjetoPdf:
    """Objeto indireto: valor analisado + bytes crus do stream (se houver)."""

    valor: Any
    stream_bruto: Optional[bytes] = None
    de_objstm: bool = False  # objetos de ObjStm não são cifrados individualmente


def _extrai_stream(dados: bytes, ini: int, dicionario: Any) -> Tuple[bytes, int]:
    """Bytes crus do stream a partir de ``ini`` (logo após o EOL de ``stream``)."""
    tamanho = dicionario.get("Length") if isinstance(dicionario, dict) else None
    if isinstance(tamanho, int) and 0 <= tamanho <= len(dados) - ini:
        cauda = dados[ini + tamanho : ini + tamanho + 24]
        if cauda.lstrip(_ESPACOS_BYTES).startswith(b"endstream"):
            return dados[ini : ini + tamanho], ini + tamanho
    # /Length indireto ou errado: procura o ``endstream`` (recuperação).
    fim = dados.find(b"endstream", ini)
    if fim < 0:
        return dados[ini:], len(dados)
    bruto = dados[ini:fim]
    if bruto.endswith(b"\r\n"):
        bruto = bruto[:-2]
    elif bruto.endswith((b"\n", b"\r")):
        bruto = bruto[:-1]
    return bruto, fim


def _le_objeto_em(dados: bytes, pos: int) -> Optional[Tuple[int, int, _ObjetoPdf, int]]:
    """Lê ``N G obj ... endobj`` em ``pos`` → ``(num, ger, objeto, fim)``."""
    t1, p1 = _proximo_token(dados, pos)
    if t1 is None or t1[0] != "num" or not isinstance(t1[1], int):
        return None
    t2, p2 = _proximo_token(dados, p1)
    if t2 is None or t2[0] != "num" or not isinstance(t2[1], int):
        return None
    t3, p3 = _proximo_token(dados, p2)
    if t3 != ("palavra", "obj"):
        return None
    valor, p4 = _analisa_valor(dados, p3)
    if isinstance(valor, _Palavra):
        valor = None
    stream_bruto: Optional[bytes] = None
    fim = p4
    t5, p5 = _proximo_token(dados, p4)
    if t5 == ("palavra", "stream"):
        ini = p5
        if dados[ini : ini + 2] == b"\r\n":
            ini += 2
        elif dados[ini : ini + 1] in (b"\n", b"\r"):
            ini += 1
        stream_bruto, fim = _extrai_stream(dados, ini, valor)
    elif t5 == ("palavra", "endobj"):
        fim = p5
    return t1[1], t2[1], _ObjetoPdf(valor=valor, stream_bruto=stream_bruto), fim


def _le_xref_classica(
    dados: bytes, pos: int
) -> Optional[Tuple[Dict[int, int], Dict[str, Any], Any]]:
    """Tabela ``xref`` clássica em ``pos`` → ``(offsets, trailer, prev)``."""
    t, p = _proximo_token(dados, pos)
    if t != ("palavra", "xref"):
        return None
    offsets: Dict[int, int] = {}
    while True:
        t1, p1 = _proximo_token(dados, p)
        if t1 == ("palavra", "trailer"):
            trailer, _ = _analisa_valor(dados, p1)
            if not isinstance(trailer, dict):
                trailer = {}
            return offsets, trailer, trailer.get("Prev")
        if t1 is None or t1[0] != "num":
            return offsets, {}, None
        inicio = int(t1[1])
        t2, p2 = _proximo_token(dados, p1)
        if t2 is None or t2[0] != "num":
            return offsets, {}, None
        contagem = int(t2[1])
        p = p2
        for i in range(max(0, min(contagem, 1_000_000))):
            ta, pa = _proximo_token(dados, p)
            _tb, pb = _proximo_token(dados, pa)
            tc, pc = _proximo_token(dados, pb)
            p = pc
            if ta is not None and ta[0] == "num" and tc == ("palavra", "n"):
                offsets.setdefault(inicio + i, int(ta[1]))


def _le_trailer_e_offsets(dados: bytes) -> Tuple[Dict[int, int], Dict[str, Any]]:
    """Segue ``startxref`` → cadeia de xref (clássica ou stream) → trailer."""
    offsets: Dict[int, int] = {}
    trailer: Dict[str, Any] = {}
    ini = dados.rfind(b"startxref")
    proximo: Optional[int] = None
    if ini >= 0:
        t, _ = _proximo_token(dados, ini + len(b"startxref"))
        if t is not None and t[0] == "num":
            proximo = int(t[1])
    visitados: set = set()
    while proximo is not None and 0 <= proximo < len(dados) and proximo not in visitados:
        visitados.add(proximo)
        pos = _pula_brancos(dados, proximo)
        classica = _le_xref_classica(dados, pos)
        if classica is not None:
            offs, tr, prev = classica
            for chave, valor in offs.items():
                offsets.setdefault(chave, valor)
            for chave, valor in tr.items():
                trailer.setdefault(chave, valor)
            proximo = int(prev) if isinstance(prev, (int, float)) else None
            continue
        # PDF 1.5+: no lugar da tabela há um stream de xref. Não lemos as
        # entradas binárias — a varredura de recuperação acha os objetos —,
        # mas o DICIONÁRIO dele carrega /Root, /Encrypt e /ID.
        lido = _le_objeto_em(dados, pos)
        if lido is not None and isinstance(lido[2].valor, dict):
            for chave in ("Root", "Encrypt", "ID", "Info", "Size"):
                if chave in lido[2].valor:
                    trailer.setdefault(chave, lido[2].valor[chave])
            prev = lido[2].valor.get("Prev")
            proximo = int(prev) if isinstance(prev, (int, float)) else None
            continue
        break
    return offsets, trailer


_RE_OBJ = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")


def _carrega_objetos(
    dados: bytes,
) -> Tuple[Optional[Dict[Tuple[int, int], _ObjetoPdf]], Dict[str, Any], Optional[str]]:
    """Carrega os objetos via xref e completa com varredura ``N G obj``.

    Devolve ``(objetos, trailer, erro)``; ``erro`` preenchido = PDF corrompido.
    """
    offsets, trailer = _le_trailer_e_offsets(dados)
    objetos: Dict[Tuple[int, int], _ObjetoPdf] = {}
    for num, off in offsets.items():
        if not 0 <= off < len(dados):
            continue
        lido = _le_objeto_em(dados, off)
        if lido is not None:
            n, g, obj, _ = lido
            objetos[(n, g)] = obj
    # Varredura de recuperação: acha o que a xref não trouxe (ou tudo, num
    # PDF truncado/sem xref). ``setdefault``: a xref, mais nova, tem prioridade.
    for m in _RE_OBJ.finditer(dados):
        if (int(m.group(1)), int(m.group(2))) in objetos:
            continue
        lido = _le_objeto_em(dados, m.start())
        if lido is not None:
            n, g, obj, _ = lido
            objetos.setdefault((n, g), obj)
    if not objetos:
        return None, {}, MSG_CORROMPIDO
    if "Root" not in trailer:
        for m in re.finditer(rb"\btrailer\b", dados):
            valor, _ = _analisa_valor(dados, m.end())
            if isinstance(valor, dict):
                for chave, item in valor.items():
                    trailer.setdefault(chave, item)
        if "Root" not in trailer:
            for (n, g), obj in objetos.items():
                if isinstance(obj.valor, dict) and obj.valor.get("Type") == "Catalog":
                    trailer["Root"] = _Ref(n, g)
                    break
    return objetos, trailer, None


# --------------------------------------------------------------------------
# Documento + filtros de stream
# --------------------------------------------------------------------------


@dataclass
class _DocumentoPdf:
    objetos: Dict[Tuple[int, int], _ObjetoPdf]
    trailer: Dict[str, Any]
    chave: Optional[bytes] = None  # chave RC4 do arquivo (algoritmo 2); None = sem cifra

    def resolve(self, valor: Any, profundidade: int = 0) -> Any:
        """Segue referências indiretas até o valor final (com guarda de ciclo)."""
        while isinstance(valor, _Ref) and profundidade < 32:
            achado = self.objetos.get((valor.num, valor.ger)) or self.objetos.get(
                (valor.num, 0)
            )
            valor = achado.valor if achado is not None else None
            profundidade += 1
        return valor

    def objeto_com_id(self, ref: Any) -> Optional[Tuple[int, int, _ObjetoPdf]]:
        """Objeto + (num, ger) reais — necessários para a chave de decifra."""
        if not isinstance(ref, _Ref):
            return None
        for chave in ((ref.num, ref.ger), (ref.num, 0)):
            obj = self.objetos.get(chave)
            if obj is not None:
                return chave[0], chave[1], obj
        return None


def _aplica_preditor_png(dados: bytes, colunas: int, cores: int, bits: int) -> bytes:
    """Desfaz os preditores PNG (10–15) usados junto do FlateDecode."""
    bpp = max(1, (cores * bits + 7) // 8)
    largura = max(1, colunas * bpp)
    saida = bytearray()
    anterior = bytearray(largura)
    i = 0
    while i < len(dados):
        tipo = dados[i]
        linha = bytearray(dados[i + 1 : i + 1 + largura])
        i += 1 + largura
        if len(linha) < largura:
            linha.extend(bytes(largura - len(linha)))
        if tipo == 1:  # Sub
            for j in range(bpp, largura):
                linha[j] = (linha[j] + linha[j - bpp]) & 0xFF
        elif tipo == 2:  # Up
            for j in range(largura):
                linha[j] = (linha[j] + anterior[j]) & 0xFF
        elif tipo == 3:  # Average
            for j in range(largura):
                esquerda = linha[j - bpp] if j >= bpp else 0
                linha[j] = (linha[j] + (esquerda + anterior[j]) // 2) & 0xFF
        elif tipo == 4:  # Paeth
            for j in range(largura):
                a = linha[j - bpp] if j >= bpp else 0
                b = anterior[j]
                c = anterior[j - bpp] if j >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                previsto = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                linha[j] = (linha[j] + previsto) & 0xFF
        anterior = linha
        saida += linha
    return bytes(saida)


def _decodifica_stream(
    doc: _DocumentoPdf, num: int, ger: int, obj: _ObjetoPdf
) -> Tuple[Optional[bytes], Optional[str]]:
    """Decifra (se preciso) e aplica os filtros → ``(dados, erro)``."""
    if obj.stream_bruto is None:
        return None, "objeto sem stream"
    dados = obj.stream_bruto
    dicionario = obj.valor if isinstance(obj.valor, dict) else {}
    if doc.chave is not None and not obj.de_objstm and dicionario.get("Type") != "XRef":
        dados = _rc4(_chave_do_objeto(doc.chave, num, ger), dados)
    filtros = doc.resolve(dicionario.get("Filter"))
    parametros = doc.resolve(dicionario.get("DecodeParms"))
    if parametros is None:
        parametros = doc.resolve(dicionario.get("DP"))
    lista = filtros if isinstance(filtros, list) else ([] if filtros is None else [filtros])
    lista_parms = (
        list(parametros)
        if isinstance(parametros, list)
        else [parametros] * max(1, len(lista))
    )
    while len(lista_parms) < len(lista):
        lista_parms.append(None)
    for filtro, parms in zip(lista, lista_parms):
        nome = filtro if isinstance(filtro, str) else ""
        if nome in ("FlateDecode", "Fl"):
            try:
                dados = zlib.decompress(dados)
            except zlib.error:
                try:
                    dados = zlib.decompressobj().decompress(dados)
                except zlib.error:
                    return None, "stream FlateDecode corrompido"
            parms = doc.resolve(parms)
            if isinstance(parms, dict):
                preditor = doc.resolve(parms.get("Predictor")) or 1
                if isinstance(preditor, (int, float)) and preditor >= 10:
                    dados = _aplica_preditor_png(
                        dados,
                        int(doc.resolve(parms.get("Columns")) or 1),
                        int(doc.resolve(parms.get("Colors")) or 1),
                        int(doc.resolve(parms.get("BitsPerComponent")) or 8),
                    )
        elif nome in ("ASCIIHexDecode", "AHx"):
            limpo = dados.split(b">")[0]
            hexa = b"".join(limpo.split())
            if len(hexa) % 2:
                hexa += b"0"
            try:
                dados = binascii.unhexlify(hexa)
            except binascii.Error:
                return None, "stream ASCIIHexDecode inválido"
        elif nome in ("ASCII85Decode", "A85"):
            compacto = b"".join(dados.split())
            if compacto.startswith(b"<~"):
                compacto = compacto[2:]
            if compacto.endswith(b"~>"):
                compacto = compacto[:-2]
            try:
                dados = base64.a85decode(compacto)
            except ValueError:
                return None, "stream ASCII85Decode inválido"
        else:
            return None, f"filtro de stream não suportado: {nome or filtro!r}"
    return dados, None


def _expande_objstm(doc: _DocumentoPdf) -> List[str]:
    """Materializa os objetos guardados dentro de ``/Type /ObjStm``."""
    avisos: List[str] = []
    for (num, ger), obj in list(doc.objetos.items()):
        if not isinstance(obj.valor, dict) or obj.valor.get("Type") != "ObjStm":
            continue
        dados, erro = _decodifica_stream(doc, num, ger, obj)
        if dados is None:
            avisos.append(f"stream de objetos {num} ilegível: {erro}")
            continue
        quantidade = int(doc.resolve(obj.valor.get("N")) or 0)
        primeiro = int(doc.resolve(obj.valor.get("First")) or 0)
        pos = 0
        pares: List[Tuple[int, int]] = []
        for _ in range(max(0, min(quantidade, 100_000))):
            t1, pos = _proximo_token(dados, pos)
            t2, pos = _proximo_token(dados, pos)
            if t1 is None or t2 is None or t1[0] != "num" or t2[0] != "num":
                break
            pares.append((int(t1[1]), int(t2[1])))
        for n_obj, desloc in pares:
            valor, _ = _analisa_valor(dados, primeiro + desloc)
            if isinstance(valor, _Palavra):
                valor = None
            doc.objetos.setdefault((n_obj, 0), _ObjetoPdf(valor=valor, de_objstm=True))
    return avisos


# --------------------------------------------------------------------------
# Criptografia /Standard V1/V2 (RC4) — a senha NUNCA é gravada
# --------------------------------------------------------------------------

_ALMOFADA = bytes(
    (
        0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41, 0x64, 0x00, 0x4E, 0x56,
        0xFF, 0xFA, 0x01, 0x08, 0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
        0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A,
    )
)


def _rc4(chave: bytes, dados: bytes) -> bytes:
    """RC4 clássico (cifrar == decifrar)."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + chave[i % len(chave)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    saida = bytearray()
    for byte in dados:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        saida.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(saida)


def _bytes_de(valor: Any) -> bytes:
    if isinstance(valor, bytes):
        return valor
    if isinstance(valor, str):
        return valor.encode("latin-1", "replace")
    return b""


def _deriva_chave(
    senha: str, o: bytes, p: int, id0: bytes, revisao: int, n_bytes: int
) -> bytes:
    """Algoritmo 2 da especificação: senha → chave do arquivo (MD5)."""
    almofadada = (senha.encode("latin-1", "replace") + _ALMOFADA)[:32]
    resumo = hashlib.md5(
        almofadada + o[:32] + (p & 0xFFFFFFFF).to_bytes(4, "little") + id0
    ).digest()
    if revisao >= 3:
        for _ in range(50):
            resumo = hashlib.md5(resumo[:n_bytes]).digest()
    return resumo[:n_bytes]


def _u_esperado(chave: bytes, id0: bytes, revisao: int) -> bytes:
    """Algoritmos 4 (R2) e 5 (R3): valor de ``/U`` implicado pela chave."""
    if revisao == 2:
        return _rc4(chave, _ALMOFADA)
    resumo = hashlib.md5(_ALMOFADA + id0).digest()
    saida = _rc4(chave, resumo)
    for i in range(1, 20):
        saida = _rc4(bytes(b ^ i for b in chave), saida)
    return saida


def _chave_do_objeto(chave: bytes, num: int, ger: int) -> bytes:
    """Chave RC4 por objeto (algoritmo 1): MD5(chave + num3LE + ger2LE)."""
    resumo = hashlib.md5(
        chave + (num & 0xFFFFFF).to_bytes(3, "little") + (ger & 0xFFFF).to_bytes(2, "little")
    ).digest()
    return resumo[: min(len(chave) + 5, 16)]


def _prepara_criptografia(doc: _DocumentoPdf, senha: Optional[str]) -> Optional[str]:
    """Valida a senha e instala a chave em ``doc``; devolve mensagem de erro."""
    cifra = doc.resolve(doc.trailer.get("Encrypt"))
    if not isinstance(cifra, dict):
        return "dicionário de criptografia ilegível — PDF corrompido"
    filtro = doc.resolve(cifra.get("Filter"))
    if filtro != "Standard":
        return f"criptografia de PDF não suportada (filtro {filtro!r}) — remova a senha e reenvie"
    v = int(doc.resolve(cifra.get("V")) or 0)
    r = int(doc.resolve(cifra.get("R")) or 0)
    if v >= 4 or r >= 4:
        return MSG_AES
    if v not in (1, 2) or r not in (2, 3):
        return f"criptografia de PDF não suportada (V={v}, R={r}) — remova a senha e reenvie"
    comprimento = doc.resolve(cifra.get("Length"))
    if not isinstance(comprimento, (int, float)):
        comprimento = 40
    n_bytes = 5 if v == 1 else max(5, min(16, int(comprimento) // 8))
    o = _bytes_de(doc.resolve(cifra.get("O")))
    u = _bytes_de(doc.resolve(cifra.get("U")))
    p = doc.resolve(cifra.get("P"))
    p = int(p) if isinstance(p, (int, float)) else -1
    identificacao = doc.resolve(doc.trailer.get("ID"))
    id0 = b""
    if isinstance(identificacao, list) and identificacao:
        id0 = _bytes_de(doc.resolve(identificacao[0]))
    candidatas: List[str] = []
    for cand in (senha or "", ""):  # senha vazia = dona de muitos PDFs de banco
        if cand not in candidatas:
            candidatas.append(cand)
    for cand in candidatas:
        chave = _deriva_chave(cand, o, p, id0, r, n_bytes)
        esperado = _u_esperado(chave, id0, r)
        confere = esperado[:16] == u[:16] if r >= 3 else esperado[:32] == u[:32]
        if confere:
            doc.chave = chave
            return None
    return MSG_SENHA


# --------------------------------------------------------------------------
# Páginas e conteúdo
# --------------------------------------------------------------------------


def _anda_paginas(
    doc: _DocumentoPdf,
    ref: Any,
    recursos_herdados: Any,
    saida: List[Dict[str, Any]],
    visitados: set,
    profundidade: int,
) -> None:
    if profundidade > 64:
        return
    if isinstance(ref, _Ref):
        if ref in visitados:
            return
        visitados.add(ref)
    no = doc.resolve(ref)
    if not isinstance(no, dict):
        return
    recursos = no.get("Resources", recursos_herdados)
    filhos = doc.resolve(no.get("Kids"))
    if no.get("Type") == "Page" or ("Contents" in no and not isinstance(filhos, list)):
        pagina = dict(no)
        pagina["Resources"] = recursos
        saida.append(pagina)
        return
    if isinstance(filhos, list):
        for filho in filhos:
            _anda_paginas(doc, filho, recursos, saida, visitados, profundidade + 1)


def _coleta_paginas(doc: _DocumentoPdf) -> List[Dict[str, Any]]:
    """Páginas na ordem da árvore ``/Pages`` (ou ``/Type /Page`` soltos)."""
    paginas: List[Dict[str, Any]] = []
    raiz = doc.resolve(doc.trailer.get("Root"))
    if isinstance(raiz, dict):
        _anda_paginas(doc, raiz.get("Pages"), None, paginas, set(), 0)
    if not paginas:  # recuperação: catálogo perdido, páginas ainda no arquivo
        for obj in doc.objetos.values():
            if isinstance(obj.valor, dict) and obj.valor.get("Type") == "Page":
                paginas.append(dict(obj.valor))
    return paginas


def _conteudo_da_pagina(
    doc: _DocumentoPdf, pagina: Dict[str, Any]
) -> Tuple[bytes, List[str]]:
    """Concatena os streams de ``/Contents`` → ``(bytes, erros da página)``."""
    erros: List[str] = []
    conteudos = pagina.get("Contents")
    if isinstance(conteudos, _Ref):
        alvo = doc.resolve(conteudos)
        refs = alvo if isinstance(alvo, list) else [conteudos]
    elif isinstance(conteudos, list):
        refs = conteudos
    else:
        refs = []
    partes: List[bytes] = []
    for ref in refs:
        achado = doc.objeto_com_id(ref)
        if achado is None:
            erros.append("stream de conteúdo ausente ou inválido")
            continue
        dados, erro = _decodifica_stream(doc, *achado)
        if dados is None:
            erros.append(erro or "stream de conteúdo ilegível")
        else:
            partes.append(dados)
    return b"\n".join(partes), erros


# --------------------------------------------------------------------------
# Fontes e /ToUnicode (CMap bfchar/bfrange)
# --------------------------------------------------------------------------


@dataclass
class _Fonte:
    mapa: Optional[Dict[int, str]] = None  # código → texto (do /ToUnicode)
    tamanho_codigo: int = 1  # bytes por código (1 ou 2)


_RE_HEXPAR = re.compile(rb"<([0-9A-Fa-f]+)>")
_RE_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_RE_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_RE_CODESPACE = re.compile(rb"begincodespacerange(.*?)endcodespacerange", re.S)
_RE_ITEM_BFRANGE = re.compile(
    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)"
)


def _hex_para_texto(hexa: bytes) -> str:
    if len(hexa) % 2:
        hexa += b"0"
    try:
        bruto = binascii.unhexlify(hexa)
    except binascii.Error:
        return ""
    if len(bruto) % 2 == 0:
        try:
            return bruto.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    return bruto.decode("latin-1", "replace")


def _analisa_cmap(dados: bytes) -> Tuple[Dict[int, str], int]:
    """CMap de /ToUnicode → ``(mapa código→texto, bytes por código)``."""
    mapa: Dict[int, str] = {}
    tamanho = 1
    espaco = _RE_CODESPACE.search(dados)
    if espaco is not None:
        pares = _RE_HEXPAR.findall(espaco.group(1))
        if pares:
            tamanho = max(1, len(pares[0]) // 2)
    for bloco in _RE_BFCHAR.findall(dados):
        itens = _RE_HEXPAR.findall(bloco)
        for origem, destino in zip(itens[0::2], itens[1::2]):
            mapa[int(origem, 16)] = _hex_para_texto(destino)
    for bloco in _RE_BFRANGE.findall(dados):
        for item in _RE_ITEM_BFRANGE.finditer(bloco):
            lo = int(item.group(1), 16)
            hi = int(item.group(2), 16)
            destino = item.group(3)
            if hi < lo or hi - lo > 65535:
                continue
            if destino.startswith(b"["):
                lista = _RE_HEXPAR.findall(destino)
                for i, alvo in enumerate(lista):
                    if lo + i <= hi:
                        mapa[lo + i] = _hex_para_texto(alvo)
            else:
                base_hex = destino.strip(b"<>")
                base = int(base_hex, 16)
                largura = len(base_hex)
                for i in range(hi - lo + 1):
                    mapa[lo + i] = _hex_para_texto(
                        format(base + i, "0%dX" % largura).encode("ascii")
                    )
    return mapa, tamanho


def _carrega_fontes(
    doc: _DocumentoPdf, recursos: Any, cache: Dict[Any, _Fonte]
) -> Dict[str, _Fonte]:
    """``/Resources /Font`` → {nome do recurso: _Fonte} (com cache por ref)."""
    fontes: Dict[str, _Fonte] = {}
    recursos = doc.resolve(recursos)
    if not isinstance(recursos, dict):
        return fontes
    dicionario_fontes = doc.resolve(recursos.get("Font"))
    if not isinstance(dicionario_fontes, dict):
        return fontes
    for nome, ref in dicionario_fontes.items():
        chave_cache = ref if isinstance(ref, _Ref) else None
        if chave_cache is not None and chave_cache in cache:
            fontes[nome] = cache[chave_cache]
            continue
        fonte = _Fonte()
        dicionario = doc.resolve(ref)
        if isinstance(dicionario, dict):
            to_unicode = dicionario.get("ToUnicode")
            achado = doc.objeto_com_id(to_unicode)
            if achado is not None:
                dados, _erro = _decodifica_stream(doc, *achado)
                if dados:
                    fonte.mapa, fonte.tamanho_codigo = _analisa_cmap(dados)
        if chave_cache is not None:
            cache[chave_cache] = fonte
        fontes[nome] = fonte
    return fontes


def _decodifica_texto_fonte(dados: bytes, fonte: Optional[_Fonte]) -> str:
    """Bytes de um operador de texto → str (ToUnicode ou latin-1≈WinAnsi)."""
    if fonte is None or not fonte.mapa:
        return dados.decode("latin-1", "replace")
    passo = max(1, fonte.tamanho_codigo)
    pedacos: List[str] = []
    for i in range(0, len(dados), passo):
        codigo = int.from_bytes(dados[i : i + passo], "big")
        texto = fonte.mapa.get(codigo)
        if texto is None:
            texto = chr(codigo) if passo == 1 else ""
        pedacos.append(texto)
    return "".join(pedacos)


# --------------------------------------------------------------------------
# Interpretador dos operadores de texto do content stream
# --------------------------------------------------------------------------


@dataclass
class _Trecho:
    x: float
    y: float
    texto: str


_LIMIAR_KERNING = 200.0  # deslocamento TJ com módulo maior que isto vira espaço
_IDENTIDADE = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiplica(m1: Tuple[float, ...], m2: Tuple[float, ...]) -> Tuple[float, ...]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _ultimos_numeros(pilha: List[Token], quantidade: int) -> List[float]:
    numeros = [float(v) for t, v in pilha if t == "num"]
    return numeros[-quantidade:] if len(numeros) >= quantidade else []


def _interpreta_texto(conteudo: bytes, fontes: Dict[str, _Fonte]) -> List[_Trecho]:
    """Executa só os operadores de texto/matriz e devolve os trechos com (x, y)."""
    ctm = _IDENTIDADE
    pilha_ctm: List[Tuple[float, ...]] = []
    tm = tlm = _IDENTIDADE
    entrelinha = 0.0
    fonte_atual: Optional[_Fonte] = None
    pilha: List[Token] = []
    trechos: List[_Trecho] = []
    pos = 0
    n = len(conteudo)

    def emite(texto: str) -> None:
        if texto:
            m = _multiplica(tm, ctm)
            trechos.append(_Trecho(x=m[4], y=m[5], texto=texto))

    while pos < n:
        token, pos = _proximo_token(conteudo, pos)
        if token is None:
            break
        tipo, valor = token
        if tipo in ("num", "str", "nome"):
            pilha.append(token)
            continue
        if tipo == "arr_abre":
            arranjo: List[Any] = []
            while True:
                t2, pos = _proximo_token(conteudo, pos)
                if t2 is None or t2[0] == "arr_fecha":
                    break
                if t2[0] in ("num", "str"):
                    arranjo.append(t2[1])
            pilha.append(("arr", arranjo))
            continue
        if tipo == "dict_abre":  # dicionários inline (BDC etc.) — consome
            profundidade = 1
            while profundidade and pos < n:
                t2, pos = _proximo_token(conteudo, pos)
                if t2 is None:
                    break
                if t2[0] == "dict_abre":
                    profundidade += 1
                elif t2[0] == "dict_fecha":
                    profundidade -= 1
            continue
        if tipo in ("dict_fecha", "arr_fecha"):
            continue
        op = valor  # tipo == "palavra"
        if op == "BT":
            tm = tlm = _IDENTIDADE
        elif op == "Tf":
            nome_fonte = next((v for t, v in reversed(pilha) if t == "nome"), None)
            fonte_atual = fontes.get(nome_fonte) if nome_fonte else None
        elif op in ("Td", "TD"):
            numeros = _ultimos_numeros(pilha, 2)
            if len(numeros) == 2:
                if op == "TD":
                    entrelinha = -numeros[1]
                tlm = _multiplica((1.0, 0.0, 0.0, 1.0, numeros[0], numeros[1]), tlm)
                tm = tlm
        elif op == "TL":
            numeros = _ultimos_numeros(pilha, 1)
            if numeros:
                entrelinha = numeros[0]
        elif op == "Tm":
            numeros = _ultimos_numeros(pilha, 6)
            if len(numeros) == 6:
                tm = tlm = tuple(numeros)
        elif op == "T*":
            tlm = _multiplica((1.0, 0.0, 0.0, 1.0, 0.0, -entrelinha), tlm)
            tm = tlm
        elif op == "cm":
            numeros = _ultimos_numeros(pilha, 6)
            if len(numeros) == 6:
                ctm = _multiplica(tuple(numeros), ctm)
        elif op == "q":
            pilha_ctm.append(ctm)
        elif op == "Q":
            ctm = pilha_ctm.pop() if pilha_ctm else _IDENTIDADE
        elif op == "Tj":
            bruto = next((v for t, v in reversed(pilha) if t == "str"), None)
            if isinstance(bruto, bytes):
                emite(_decodifica_texto_fonte(bruto, fonte_atual))
        elif op in ("'", '"'):
            tlm = _multiplica((1.0, 0.0, 0.0, 1.0, 0.0, -entrelinha), tlm)
            tm = tlm
            bruto = next((v for t, v in reversed(pilha) if t == "str"), None)
            if isinstance(bruto, bytes):
                emite(_decodifica_texto_fonte(bruto, fonte_atual))
        elif op == "TJ":
            arranjo = next((v for t, v in reversed(pilha) if t == "arr"), None)
            if isinstance(arranjo, list):
                partes: List[str] = []
                for item in arranjo:
                    if isinstance(item, bytes):
                        partes.append(_decodifica_texto_fonte(item, fonte_atual))
                    elif isinstance(item, (int, float)) and abs(item) > _LIMIAR_KERNING:
                        partes.append(" ")  # kerning grande = separação de coluna
                emite("".join(partes))
        elif op == "BI":  # imagem inline: pula os bytes binários até "EI"
            fim_imagem = conteudo.find(b"EI", pos)
            pos = n if fim_imagem < 0 else fim_imagem + 2
        pilha.clear()
    return trechos


def _monta_linhas(trechos: List[_Trecho], tolerancia: float = 2.5) -> List[str]:
    """Agrupa por baseline Y (tolerância) e ordena por X → linhas de texto."""
    if not trechos:
        return []
    grupos: List[Tuple[float, List[Tuple[float, int, str]]]] = []
    for sequencia, trecho in enumerate(trechos):
        alvo: Optional[Tuple[float, List[Tuple[float, int, str]]]] = None
        for grupo in grupos:
            if abs(grupo[0] - trecho.y) <= tolerancia:
                alvo = grupo
                break
        if alvo is None:
            alvo = (trecho.y, [])
            grupos.append(alvo)
        alvo[1].append((trecho.x, sequencia, trecho.texto))
    grupos.sort(key=lambda grupo: -grupo[0])  # topo da página primeiro
    linhas: List[str] = []
    for _, itens in grupos:
        itens.sort(key=lambda item: (item[0], item[1]))
        pedacos: List[str] = []
        x_anterior: Optional[float] = None
        for x, _seq, texto in itens:
            if pedacos and x_anterior is not None and abs(x - x_anterior) < 0.01:
                pedacos[-1] += texto  # Tj consecutivos sem reposicionar: mesmo trecho
            else:
                pedacos.append(texto)
            x_anterior = x
        linha = " ".join(p.strip() for p in pedacos if p.strip())
        linha = re.sub(r"\s+", " ", linha).strip()
        if linha:
            linhas.append(linha)
    return linhas


# --------------------------------------------------------------------------
# Heurística de extrato: linhas de texto → transações/saldos
# --------------------------------------------------------------------------

_RE_INICIO_DATA = re.compile(r"^\s*(\d{2}/\d{2}(?:/\d{2,4})?)\b[\s:.\-]*(.*)$")
_RE_VALOR_CAUDA = re.compile(
    r"(?:R\$\s*)?([+-]?\d{1,3}(?:\.\d{3})*,\d{2}|[+-]?\d+,\d{2})\s*([DCdc])?\s*$"
)
_RE_PERIODO = re.compile(
    r"de\s+(\d{2}/\d{2}/\d{4})\s+(?:at[eé]|a)\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE
)
_RE_DATA_COMPLETA = re.compile(r"\d{2}/\d{2}/((?:19|20)\d{2})")
#: Parcela "3/10" numa descrição, sem casar pedaços de data "05/03/2026".
_RE_PARCELA = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")


def _extrai_parcela(descricao: str) -> Tuple[Optional[int], Optional[int]]:
    for achado in _RE_PARCELA.finditer(descricao):
        numero, total = int(achado.group(1)), int(achado.group(2))
        if 1 <= numero <= total and total >= 2:
            return numero, total
    return None, None


def _detecta_periodo(linhas: List[str]) -> Tuple[Optional[date], Optional[date]]:
    """Procura "de DD/MM/AAAA a DD/MM/AAAA" (ou linha com "período" + datas)."""
    texto = "\n".join(linhas)
    achado = _RE_PERIODO.search(texto)
    if achado is not None:
        return as_date(achado.group(1)), as_date(achado.group(2))
    for linha in linhas:
        if "periodo" in normaliza_descricao(linha):
            datas = re.findall(r"\d{2}/\d{2}/\d{4}", linha)
            if datas:
                inicio = as_date(datas[0])
                fim = as_date(datas[-1]) if len(datas) > 1 else None
                return inicio, fim
    return None, None


def _resolve_data(
    texto: str, ano_atual: int, data_anterior: Optional[date]
) -> Tuple[Optional[date], Optional[str], int]:
    """DD/MM/AAAA direto; DD/MM ganha o ano corrente da sequência.

    Regra "sem viagem no tempo": a sequência de datas do documento não pode
    decrescer — quando o mês cai (dez → jan), o ano incrementa.
    """
    if len(texto) > 5:
        completa = as_date(texto)
        if completa is None:
            return None, f"data inválida '{texto}' — linha ignorada", ano_atual
        return completa, None, completa.year
    try:
        dia, mes = (int(parte) for parte in texto.split("/"))
    except ValueError:
        return None, f"data inválida '{texto}' — linha ignorada", ano_atual
    for _tentativa in range(2):
        try:
            candidata = date(ano_atual, mes, dia)
        except ValueError:
            return (
                None,
                f"data inválida '{texto}' (dia/mês fora do calendário) — linha ignorada",
                ano_atual,
            )
        if (
            data_anterior is not None
            and candidata < data_anterior
            and mes < data_anterior.month
        ):
            ano_atual += 1  # virada dez → jan (ou similar) incrementa o ano
            continue
        return candidata, None, ano_atual
    return candidata, None, ano_atual


def _aplica_sinal(centavos: int, sufixo: str) -> int:
    if sufixo == "D":
        return -abs(centavos)
    if sufixo == "C":
        return abs(centavos)
    return centavos


def _linhas_para_resultado(
    paginas_linhas: List[List[str]], contexto: ContextoParse, resultado: ResultadoParse
) -> None:
    """Aplica a heurística de extrato sobre as linhas já reconstruídas."""
    todas = [linha for pagina in paginas_linhas for linha in pagina]
    periodo_inicio, periodo_fim = _detecta_periodo(todas)
    if periodo_inicio is not None:
        ano_atual = periodo_inicio.year
    else:
        achado_ano = _RE_DATA_COMPLETA.search("\n".join(todas))
        # Último recurso (sem período nem data completa): ano corrente.
        ano_atual = int(achado_ano.group(1)) if achado_ano else date.today().year

    contadores: Dict[Tuple[str, int, str], int] = {}
    datas: List[date] = []
    data_anterior: Optional[date] = None
    saldos_sem_data: List[Tuple[str, int]] = []

    for numero_pagina, linhas in enumerate(paginas_linhas, start=1):
        for numero_linha, linha in enumerate(linhas, start=1):
            ref = f"página {numero_pagina}, linha {numero_linha}"
            resultado.linhas_brutas.append(
                LinhaBruta(
                    ordem=len(resultado.linhas_brutas) + 1,
                    origem_ref=ref,
                    conteudo={"texto": linha},
                )
            )
            eh_saldo = "saldo" in normaliza_descricao(linha)
            achado_data = _RE_INICIO_DATA.match(linha)
            if achado_data is None:
                if eh_saldo:  # "SALDO ANTERIOR 100,00 C" sem data — ancora depois
                    achado_valor = _RE_VALOR_CAUDA.search(linha)
                    if achado_valor is not None:
                        centavos = parse_centavos(achado_valor.group(1), contexto.moeda)
                        if centavos is not None:
                            rotulo = linha[: achado_valor.start()].strip() or "saldo"
                            sufixo = (achado_valor.group(2) or "").upper()
                            saldos_sem_data.append((rotulo, _aplica_sinal(centavos, sufixo)))
                continue
            data_texto, resto = achado_data.group(1), achado_data.group(2)
            achado_valor = _RE_VALOR_CAUDA.search(resto)
            if achado_valor is None:
                continue  # linha com data mas sem valor no fim: não é transação
            valor_texto = achado_valor.group(1)
            sufixo = (achado_valor.group(2) or "").upper()
            corpo = resto[: achado_valor.start()].strip(" .:-\t")
            # Duas colunas numéricas no fim = valor + saldo da linha.
            saldo_apos: Optional[int] = None
            segundo = _RE_VALOR_CAUDA.search(corpo)
            if segundo is not None and not eh_saldo:
                saldo_apos = parse_centavos(valor_texto, contexto.moeda)
                if saldo_apos is not None:
                    saldo_apos = _aplica_sinal(saldo_apos, sufixo)
                valor_texto = segundo.group(1)
                sufixo = (segundo.group(2) or "").upper()
                corpo = corpo[: segundo.start()].strip(" .:-\t")

            data_operacao, erro_data, ano_atual = _resolve_data(
                data_texto, ano_atual, data_anterior
            )
            if data_operacao is None:
                resultado.erros.append(
                    {"origem_ref": ref, "campo": "data", "mensagem": erro_data}
                )
                continue
            centavos = parse_centavos(valor_texto, contexto.moeda)
            if not centavos:
                resultado.erros.append(
                    {
                        "origem_ref": ref,
                        "campo": "valor",
                        "mensagem": "valor ausente, zero ou não numérico — linha ignorada",
                    }
                )
                continue
            data_anterior = data_operacao
            if eh_saldo:
                resultado.saldos.append(
                    SaldoInformado(
                        data=data_operacao,
                        valor_centavos=_aplica_sinal(centavos, sufixo),
                        rotulo=corpo or "saldo",
                    )
                )
                continue
            if sufixo == "D":
                direcao = "debito"
            elif sufixo == "C":
                direcao = "credito"
            else:
                direcao = "debito" if centavos < 0 else "credito"
            valor_abs = abs(centavos)
            parcela_num, parcela_total = _extrai_parcela(corpo)
            chave = (data_operacao.isoformat(), valor_abs, normaliza_descricao(corpo))
            contadores[chave] = contadores.get(chave, 0) + 1
            resultado.transacoes.append(
                Transacao(
                    data_operacao=data_operacao,
                    valor_centavos=valor_abs,
                    moeda=contexto.moeda,
                    direcao=direcao,
                    # Tipo inicial pela direção; o pipeline refina depois.
                    tipo="receita" if direcao == "credito" else "despesa",
                    descricao_original=corpo,
                    descricao_normalizada=normaliza_descricao(corpo),
                    fingerprint=fingerprint_transacao(
                        contexto.rotulo_origem,
                        data_operacao,
                        valor_abs,
                        contexto.moeda,
                        corpo,
                        None,
                        contadores[chave],
                    ),
                    origem_ref=ref,
                    saldo_apos_centavos=saldo_apos,
                    parcela_num=parcela_num,
                    parcela_total=parcela_total,
                )
            )
            datas.append(data_operacao)

    if datas:
        resultado.periodo_inicio = min(datas)
        resultado.periodo_fim = max(datas)
    else:
        resultado.periodo_inicio = periodo_inicio
        resultado.periodo_fim = periodo_fim

    for rotulo, valor in saldos_sem_data:
        if not datas:
            resultado.avisos.append(
                f"linha de saldo '{rotulo}' sem data identificável — saldo ignorado"
            )
            continue
        ancora = max(datas) if "final" in normaliza_descricao(rotulo) else min(datas)
        resultado.saldos.append(
            SaldoInformado(data=ancora, valor_centavos=valor, rotulo=rotulo)
        )


# --------------------------------------------------------------------------
# Adaptador
# --------------------------------------------------------------------------


class AdaptadorPdfTexto:
    """Extratos e faturas em PDF com camada de texto (extrator 100% stdlib)."""

    nome = "pdf-texto"
    versao = "1"

    def detecta(self, contexto: ContextoParse) -> float:
        return 0.9 if contexto.formato.formato == "pdf" else 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:
        resultado = ResultadoParse(adaptador=self.nome, moeda=contexto.moeda)
        try:
            self._parse_interno(contexto, resultado)
        except Exception as excecao:  # noqa: BLE001 - conteúdo ruim nunca derruba
            resultado.erros.append(
                {
                    "origem_ref": "arquivo",
                    "mensagem": f"falha inesperada ao ler o PDF: {excecao}",
                }
            )
            resultado.avisos.append(
                "falha inesperada ao ler o PDF — arquivo possivelmente corrompido"
            )
        return resultado

    def _parse_interno(self, contexto: ContextoParse, resultado: ResultadoParse) -> None:
        dados = contexto.conteudo
        objetos, trailer, erro = _carrega_objetos(dados)
        if erro is not None or objetos is None:
            resultado.erros.append({"origem_ref": "arquivo", "mensagem": erro or MSG_CORROMPIDO})
            resultado.avisos.append(erro or MSG_CORROMPIDO)
            return
        doc = _DocumentoPdf(objetos=objetos, trailer=trailer)
        if "Encrypt" in trailer:
            mensagem = _prepara_criptografia(doc, contexto.senha)
            if mensagem is not None:
                resultado.erros.append({"origem_ref": "arquivo", "mensagem": mensagem})
                resultado.avisos.append(mensagem)
                return
        resultado.avisos.extend(_expande_objstm(doc))
        paginas = _coleta_paginas(doc)
        if not paginas:
            mensagem = "árvore de páginas não encontrada — PDF corrompido ou truncado"
            resultado.erros.append({"origem_ref": "arquivo", "mensagem": mensagem})
            resultado.avisos.append(mensagem)
            return
        cache_fontes: Dict[Any, _Fonte] = {}
        paginas_linhas: List[List[str]] = []
        houve_texto = False
        for numero, pagina in enumerate(paginas, start=1):
            conteudo, erros_pagina = _conteudo_da_pagina(doc, pagina)
            for detalhe in erros_pagina:
                resultado.erros.append(
                    {
                        "origem_ref": f"página {numero}",
                        "mensagem": f"página ilegível: {detalhe}",
                    }
                )
            fontes = _carrega_fontes(doc, pagina.get("Resources"), cache_fontes)
            trechos = _interpreta_texto(conteudo, fontes) if conteudo else []
            if trechos:
                houve_texto = True
            paginas_linhas.append(_monta_linhas(trechos))
        if not houve_texto:
            if not resultado.erros:
                # Nenhum filtro falhou e mesmo assim não há texto: digitalizado.
                resultado.avisos.append(MSG_DIGITALIZADO)
                resultado.erros.append(
                    {"origem_ref": "arquivo", "campo": "texto", "mensagem": MSG_DIGITALIZADO}
                )
            return
        _linhas_para_resultado(paginas_linhas, contexto, resultado)
        if not resultado.transacoes:
            resultado.avisos.append(
                "nenhuma linha de transação (data + valor) reconhecida no texto do PDF"
            )


ADAPTADORES = [AdaptadorPdfTexto()]
