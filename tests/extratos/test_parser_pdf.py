"""Testes do parser de PDF (``pdf_texto``).

Todos os PDFs são gerados sinteticamente AQUI, byte a byte (com
``zlib.compress`` dos content streams) — nenhum dado real de banco/cartão.
"""

from __future__ import annotations

import binascii
import hashlib
import zlib
from datetime import date
from typing import Dict, List, Tuple

import pytest

from engine.extratos.detect import detectar
from engine.extratos.parsers.base import ContextoParse
from engine.extratos.parsers.pdf_texto import AdaptadorPdfTexto


@pytest.fixture(autouse=True)
def _dados_em_tmp(tmp_path, monkeypatch):
    """Nenhum teste pode encostar no diretório de dados real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))


def _contexto(conteudo: bytes, nome: str = "extrato.pdf", **kwargs) -> ContextoParse:
    return ContextoParse(
        conteudo=conteudo,
        formato=detectar(conteudo, nome),
        nome_original=nome,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Montagem de PDFs sintéticos (xref clássica válida, offsets reais)
# ---------------------------------------------------------------------------


def _monta_pdf(corpos: Dict[int, bytes], raiz: int = 1, trailer_extra: bytes = b"") -> bytes:
    saida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: Dict[int, int] = {}
    for numero in sorted(corpos):
        offsets[numero] = len(saida)
        saida += b"%d 0 obj\n" % numero + corpos[numero] + b"\nendobj\n"
    inicio_xref = len(saida)
    maximo = max(corpos)
    saida += b"xref\n0 %d\n" % (maximo + 1)
    saida += b"0000000000 65535 f \n"
    for numero in range(1, maximo + 1):
        if numero in offsets:
            saida += b"%010d 00000 n \n" % offsets[numero]
        else:
            saida += b"0000000000 65535 f \n"
    saida += (
        b"trailer\n<< /Size %d /Root %d 0 R %s>>\nstartxref\n%d\n%%%%EOF\n"
        % (maximo + 1, raiz, trailer_extra, inicio_xref)
    )
    return bytes(saida)


def _objetos_basicos(conteudo_pagina: bytes) -> Dict[int, bytes]:
    comprimido = zlib.compress(conteudo_pagina)
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream"
        % (len(comprimido), comprimido),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }


def _conteudo_linhas(linhas: List[Tuple[int, List[Tuple[int, str]]]]) -> bytes:
    """Cada linha = (y, [(x, texto), ...]) → blocos BT/Tm/(…) Tj/ET."""
    partes: List[bytes] = []
    for y, pedacos in linhas:
        for x, texto in pedacos:
            partes.append(
                b"BT /F1 10 Tf 1 0 0 1 %d %d Tm (%s) Tj ET"
                % (x, y, texto.encode("latin-1"))
            )
    return b"\n".join(partes)


# ---------------------------------------------------------------------------
# Criptografia RC4 (lado gerador do teste — independente do parser)
# ---------------------------------------------------------------------------

_ALMOFADA = bytes(
    (
        0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41, 0x64, 0x00, 0x4E, 0x56,
        0xFF, 0xFA, 0x01, 0x08, 0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
        0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A,
    )
)


def _rc4(chave: bytes, dados: bytes) -> bytes:
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


def _chave_arquivo(senha: str, o: bytes, p: int, id0: bytes) -> bytes:
    """Algoritmo 2 (R3, 128 bits): senha do usuário → chave do arquivo."""
    base = (senha.encode("latin-1") + _ALMOFADA)[:32]
    resumo = hashlib.md5(
        base + o + (p & 0xFFFFFFFF).to_bytes(4, "little") + id0
    ).digest()
    for _ in range(50):
        resumo = hashlib.md5(resumo[:16]).digest()
    return resumo[:16]


def _u_r3(chave: bytes, id0: bytes) -> bytes:
    """Algoritmo 5: valor de /U (16 bytes úteis + 16 de enchimento)."""
    resumo = hashlib.md5(_ALMOFADA + id0).digest()
    saida = _rc4(chave, resumo)
    for i in range(1, 20):
        saida = _rc4(bytes(b ^ i for b in chave), saida)
    return saida + bytes(16)


def _chave_objeto(chave: bytes, numero: int) -> bytes:
    return hashlib.md5(
        chave + numero.to_bytes(3, "little") + (0).to_bytes(2, "little")
    ).digest()[:16]


def _pdf_criptografado(senha_usuario: str) -> bytes:
    id0 = bytes(range(16))
    o = bytes(range(32, 64))  # /O é opaco para o algoritmo 2 — qualquer 32 bytes
    p = -3904
    chave = _chave_arquivo(senha_usuario, o, p, id0)
    u = _u_r3(chave, id0)
    conteudo = _conteudo_linhas(
        [(700, [(50, "07/12/2025"), (150, "COMPRA PROTEGIDA"), (400, "10,00 D")])]
    )
    cifrado = _rc4(_chave_objeto(chave, 4), zlib.compress(conteudo))
    objetos = _objetos_basicos(b"")
    objetos[4] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (
        len(cifrado),
        cifrado,
    )
    objetos[6] = (
        b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P %d /O <%s> /U <%s> >>"
        % (p, binascii.hexlify(o), binascii.hexlify(u))
    )
    trailer_extra = b"/Encrypt 6 0 R /ID [<%s> <%s>] " % (
        binascii.hexlify(id0),
        binascii.hexlify(id0),
    )
    return _monta_pdf(objetos, trailer_extra=trailer_extra)


# ---------------------------------------------------------------------------
# detecta()
# ---------------------------------------------------------------------------


def test_detecta_pdf_e_ignora_outros_formatos():
    adaptador = AdaptadorPdfTexto()
    pdf = _monta_pdf(_objetos_basicos(b""))
    assert adaptador.detecta(_contexto(pdf)) == 0.9
    assert adaptador.detecta(_contexto(b"data;valor\n01/01/2026;10,00\n", "a.csv")) == 0.0


# ---------------------------------------------------------------------------
# Extrato simples: 3 transações + linha de saldo, trechos fora de ordem em X
# ---------------------------------------------------------------------------


def test_extrato_simples_com_saldo():
    conteudo = _conteudo_linhas(
        [
            (780, [(50, "Banco Sintetico S.A. - Extrato de Conta")]),
            (760, [(50, "Período: de 01/12/2025 a 31/12/2025")]),
            # Trechos emitidos fora de ordem de propósito: o parser ordena por X.
            (740, [(430, "1.500,00 C"), (50, "05/12/2025"), (150, "PIX RECEBIDO CLIENTE FICTICIO")]),
            (720, [(50, "10/12/2025"), (150, "PAGAMENTO BOLETO ENERGIA"), (430, "250,10 D")]),
            (700, [(50, "15/12/2025"), (150, "COMPRA SUPERMERCADO SINTETICO"), (430, "89,90 D")]),
            (680, [(50, "31/12/2025"), (150, "SALDO FINAL"), (430, "1.160,00 C")]),
        ]
    )
    resultado = AdaptadorPdfTexto().parse(_contexto(_monta_pdf(_objetos_basicos(conteudo))))

    assert resultado.erros == []
    assert len(resultado.transacoes) == 3
    t1, t2, t3 = resultado.transacoes
    assert (t1.data_operacao, t1.valor_centavos, t1.direcao) == (date(2025, 12, 5), 150000, "credito")
    assert t1.descricao_original == "PIX RECEBIDO CLIENTE FICTICIO"
    assert t1.tipo == "receita"
    assert (t2.data_operacao, t2.valor_centavos, t2.direcao) == (date(2025, 12, 10), 25010, "debito")
    assert (t3.data_operacao, t3.valor_centavos, t3.direcao) == (date(2025, 12, 15), 8990, "debito")
    assert t3.tipo == "despesa"
    # Linha com "saldo" vira SaldoInformado, nunca transação.
    assert len(resultado.saldos) == 1
    assert resultado.saldos[0].valor_centavos == 116000
    assert resultado.saldos[0].data == date(2025, 12, 31)
    assert "saldo" in resultado.saldos[0].rotulo.lower()
    assert resultado.periodo_inicio == date(2025, 12, 5)
    assert resultado.periodo_fim == date(2025, 12, 15)
    assert len(resultado.linhas_brutas) >= 6
    fingerprints = [t.fingerprint for t in resultado.transacoes]
    assert len(set(fingerprints)) == 3
    assert resultado.ok


# ---------------------------------------------------------------------------
# TJ com kerning: número com módulo > 200 vira espaço, menor não
# ---------------------------------------------------------------------------


def test_tj_com_kerning():
    conteudo = (
        b"BT /F1 10 Tf 1 0 0 1 50 700 Tm "
        b"[(03/01/2026 ) (COMPRA) -300 (LOJA) 50 (XY) ( 45,67 D)] TJ ET"
    )
    resultado = AdaptadorPdfTexto().parse(_contexto(_monta_pdf(_objetos_basicos(conteudo))))

    assert resultado.erros == []
    assert len(resultado.transacoes) == 1
    transacao = resultado.transacoes[0]
    # -300 (|.|>200) vira espaço; 50 não separa: "LOJA"+"XY" = "LOJAXY".
    assert transacao.descricao_original == "COMPRA LOJAXY"
    assert transacao.data_operacao == date(2026, 1, 3)
    assert transacao.valor_centavos == 4567
    assert transacao.direcao == "debito"


# ---------------------------------------------------------------------------
# Criptografia RC4-128 (V2/R3): senha certa, senha errada, senha vazia
# ---------------------------------------------------------------------------


def test_pdf_rc4_com_senha_correta():
    pdf = _pdf_criptografado("1234")
    resultado = AdaptadorPdfTexto().parse(_contexto(pdf, senha="1234"))
    assert resultado.erros == []
    assert len(resultado.transacoes) == 1
    transacao = resultado.transacoes[0]
    assert transacao.descricao_original == "COMPRA PROTEGIDA"
    assert (transacao.valor_centavos, transacao.direcao) == (1000, "debito")


def test_pdf_rc4_com_senha_errada():
    pdf = _pdf_criptografado("1234")
    resultado = AdaptadorPdfTexto().parse(_contexto(pdf, senha="9999"))
    assert resultado.transacoes == []
    assert any("senha" in erro["mensagem"].lower() for erro in resultado.erros)


def test_pdf_rc4_senha_vazia_abre_sem_senha():
    """Senha de usuário vazia (comum em PDF de banco) abre sem informar senha."""
    pdf = _pdf_criptografado("")
    resultado = AdaptadorPdfTexto().parse(_contexto(pdf, senha=None))
    assert resultado.erros == []
    assert len(resultado.transacoes) == 1


# ---------------------------------------------------------------------------
# AES (V>=4) não suportado — mensagem clara, sem exceção
# ---------------------------------------------------------------------------


def test_pdf_aes_v4_recusado_com_mensagem_clara():
    objetos = _objetos_basicos(b"BT (07/12/2025 X 10,00 D) Tj ET")
    objetos[6] = (
        b"<< /Filter /Standard /V 4 /R 4 /Length 128 /P -3904 "
        b"/CF << /StdCF << /CFM /AESV2 >> >> /O <00> /U <00> >>"
    )
    pdf = _monta_pdf(objetos, trailer_extra=b"/Encrypt 6 0 R /ID [<00> <00>] ")
    resultado = AdaptadorPdfTexto().parse(_contexto(pdf, senha="1234"))
    assert resultado.transacoes == []
    assert any("AES" in erro["mensagem"] for erro in resultado.erros)
    assert any("AES" in aviso for aviso in resultado.avisos)


# ---------------------------------------------------------------------------
# PDF sem camada de texto (digitalizado) — aviso, nunca exceção
# ---------------------------------------------------------------------------


def test_pdf_sem_texto_avisa_digitalizado():
    conteudo = b"1 0 0 RG 10 10 100 100 re S"  # só desenho, nenhum operador de texto
    resultado = AdaptadorPdfTexto().parse(_contexto(_monta_pdf(_objetos_basicos(conteudo))))
    assert resultado.transacoes == []
    assert any("sem camada de texto" in aviso for aviso in resultado.avisos)
    assert any("digitalizado" in erro["mensagem"] for erro in resultado.erros)


# ---------------------------------------------------------------------------
# Bytes aleatórios — erro de corrompido, nunca exceção
# ---------------------------------------------------------------------------


def test_bytes_aleatorios_erro_de_corrompido():
    dados = bytes(range(256)) * 4  # determinístico, sem %PDF nem "N G obj"
    resultado = AdaptadorPdfTexto().parse(_contexto(dados, "lixo.pdf"))
    assert resultado.transacoes == []
    assert any("corrompido" in erro["mensagem"] for erro in resultado.erros)


# ---------------------------------------------------------------------------
# Datas DD/MM sem ano: ano do período do cabeçalho + virada dez → jan
# ---------------------------------------------------------------------------


def test_virada_de_ano_com_datas_sem_ano():
    conteudo = _conteudo_linhas(
        [
            (760, [(50, "Periodo: de 15/12/2025 a 15/01/2026")]),
            (740, [(50, "20/12"), (150, "COMPRA DEZEMBRO"), (400, "10,00 D")]),
            (720, [(50, "05/01"), (150, "RECEBIMENTO JANEIRO"), (400, "20,00 C")]),
        ]
    )
    resultado = AdaptadorPdfTexto().parse(_contexto(_monta_pdf(_objetos_basicos(conteudo))))

    assert resultado.erros == []
    assert [t.data_operacao for t in resultado.transacoes] == [
        date(2025, 12, 20),
        date(2026, 1, 5),
    ]
    assert resultado.periodo_inicio == date(2025, 12, 20)
    assert resultado.periodo_fim == date(2026, 1, 5)
