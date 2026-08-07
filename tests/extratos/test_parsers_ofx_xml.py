"""Testes dos parsers OFX (1.x SGML e 2.x XML) e XML genérico.

Fixtures 100% sintéticas — nenhum dado real de banco ou cartão.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine.extratos.detect import detectar
from engine.extratos.parsers.base import ContextoParse
from engine.extratos.parsers.ofx import AdaptadorOfx, _amt_para_centavos
from engine.extratos.parsers.xml_generico import AdaptadorXmlGenerico


@pytest.fixture(autouse=True)
def _dados_em_tmp(tmp_path, monkeypatch):
    """Nenhum teste pode encostar no diretório de dados real do usuário."""
    monkeypatch.setenv("CELESTIA_DATA_DIR", str(tmp_path))


def _contexto(conteudo: bytes, nome: str, **kwargs) -> ContextoParse:
    return ContextoParse(
        conteudo=conteudo,
        formato=detectar(conteudo, nome),
        nome_original=nome,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# OFX 1.x — SGML de conta corrente, header CP1252, datas com fuso, saldos
# ---------------------------------------------------------------------------

OFX_SGML_CONTA = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>20260201120000[-3:BRT]
<LANGUAGE>POR
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS>
<CURDEF>BRL
<BANKACCTFROM>
<BANKID>0001
<ACCTID>99999-9
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260101
<DTEND>20260131
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260105120000[-3:BRT]
<TRNAMT>1234.56
<FITID>F001
<NAME>SALARIO EMPRESA FICTICIA
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260110
<TRNAMT>-15.9
<FITID>F002
<CHECKNUM>000123
<NAME>COMPRA DEBITO
<MEMO>Padaria São João
</STMTTRN>
<STMTTRN>
<TRNTYPE>FEE
<DTPOSTED>20260115
<TRNAMT>-35.00
<FITID>F003
<NAME>TARIFA PACOTE SERVICOS
</STMTTRN>
<STMTTRN>
<TRNTYPE>INT
<DTPOSTED>20260120
<TRNAMT>3.21
<FITID>F004
<NAME>RENDIMENTO POUPANCA
</STMTTRN>
<STMTTRN>
<TRNTYPE>XFER
<DTPOSTED>20260125
<TRNAMT>-500.00
<FITID>F005
<NAME>TED PARA CONTA PROPRIA
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260130090000[-3:BRT]
<TRNAMT>-50.00
<FITID>F006
<NAME>SINAL DIVERGE DO TRNTYPE
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>1000.00
<DTASOF>20260131
</LEDGERBAL>
<AVAILBAL>
<BALAMT>950.55
<DTASOF>20260131
</AVAILBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def test_ofx_sgml_conta_completo():
    conteudo = OFX_SGML_CONTA.encode("cp1252")  # CHARSET:1252 declarado no header
    ctx = _contexto(conteudo, "extrato.ofx", rotulo_origem="conta:Banco Sintético CC")

    assert ctx.formato.formato == "ofx"
    adaptador = AdaptadorOfx()
    assert adaptador.detecta(ctx) == pytest.approx(0.95)

    r = adaptador.parse(ctx)
    assert r.adaptador == "ofx"
    assert r.erros == []
    assert r.moeda == "BRL"
    assert r.periodo_inicio == date(2026, 1, 1)
    assert r.periodo_fim == date(2026, 1, 31)
    assert len(r.transacoes) == 6
    assert len(r.linhas_brutas) == 6

    t1 = r.transacoes[0]
    assert (t1.data_operacao, t1.valor_centavos) == (date(2026, 1, 5), 123456)
    assert (t1.direcao, t1.tipo) == ("credito", "receita")
    assert t1.id_banco == "F001"
    assert t1.origem_ref == "STMTTRN 1"

    t2 = r.transacoes[1]
    assert (t2.valor_centavos, t2.direcao, t2.tipo) == (1590, "debito", "despesa")
    assert t2.documento == "000123"
    # CP1252 respeitado: acento do MEMO chega íntegro, concatenado ao NAME.
    assert t2.descricao_original == "COMPRA DEBITO - Padaria São João"

    assert r.transacoes[2].tipo == "tarifa"
    assert r.transacoes[3].tipo == "juros"
    assert r.transacoes[3].direcao == "credito"
    assert r.transacoes[3].valor_centavos == 321
    assert r.transacoes[4].tipo == "transferencia"
    assert r.transacoes[4].direcao == "debito"

    # TRNTYPE diz CREDIT mas TRNAMT é -50.00: o sinal manda.
    t6 = r.transacoes[5]
    assert (t6.direcao, t6.tipo, t6.valor_centavos) == ("debito", "despesa", 5000)
    assert t6.origem_ref == "STMTTRN 6"

    rotulos = {s.rotulo: s for s in r.saldos}
    assert set(rotulos) == {"saldo final", "saldo disponível"}
    assert rotulos["saldo final"].valor_centavos == 100000
    assert rotulos["saldo disponível"].valor_centavos == 95055
    assert rotulos["saldo final"].data == date(2026, 1, 31)

    # Fingerprints todas distintas (ocorrência incremental protege duplicatas).
    assert len({t.fingerprint for t in r.transacoes}) == 6


# ---------------------------------------------------------------------------
# OFX 2.x — XML de cartão (CCSTMTRS): compra/estorno pelo sinal
# ---------------------------------------------------------------------------

OFX_XML_CARTAO = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <CREDITCARDMSGSRSV1>
    <CCSTMTTRNRS>
      <TRNUID>1</TRNUID>
      <STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
      <CCSTMTRS>
        <CURDEF>BRL</CURDEF>
        <CCACCTFROM><ACCTID>XXXXXXXX1234</ACCTID></CCACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20260201</DTSTART>
          <DTEND>20260228</DTEND>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260205</DTPOSTED>
            <TRNAMT>-250.00</TRNAMT>
            <FITID>C1</FITID>
            <NAME>LOJA A &amp; B</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20260210</DTPOSTED>
            <TRNAMT>99.90</TRNAMT>
            <FITID>C2</FITID>
            <NAME>ESTORNO LOJA A</NAME>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL>
          <BALAMT>-150.10</BALAMT>
          <DTASOF>20260228</DTASOF>
        </LEDGERBAL>
      </CCSTMTRS>
    </CCSTMTTRNRS>
  </CREDITCARDMSGSRSV1>
</OFX>
"""


def test_ofx_xml_cartao_compra_e_estorno():
    conteudo = OFX_XML_CARTAO.encode("utf-8")
    ctx = _contexto(conteudo, "fatura.ofx", tipo_documento="fatura_cartao",
                    rotulo_origem="cartao:Cartão Sintético")

    assert ctx.formato.formato == "ofx"
    r = AdaptadorOfx().parse(ctx)
    assert r.erros == []
    assert len(r.transacoes) == 2

    compra = r.transacoes[0]
    assert (compra.valor_centavos, compra.direcao, compra.tipo) == (25000, "debito", "compra")
    assert compra.id_banco == "C1"
    assert compra.descricao_original == "LOJA A & B"  # &amp; desfeito

    # Estorno com TRNAMT positivo em contexto CCSTMTRS.
    estorno = r.transacoes[1]
    assert (estorno.valor_centavos, estorno.direcao, estorno.tipo) == (9990, "credito", "estorno")

    assert len(r.saldos) == 1
    assert r.saldos[0].rotulo == "saldo final"
    assert r.saldos[0].valor_centavos == -15010  # saldo devedor negativo, exato
    assert (r.periodo_inicio, r.periodo_fim) == (date(2026, 2, 1), date(2026, 2, 28))


def test_ofx_trnamt_centavos_exatos_sem_float():
    # Conversão por string: nada de 123455.99999….
    assert _amt_para_centavos("1234.56", "BRL") == 123456
    assert _amt_para_centavos("-15.9", "BRL") == -1590
    assert _amt_para_centavos("0.01", "BRL") == 1
    assert _amt_para_centavos("(35.00)", "BRL") == -3500
    assert _amt_para_centavos("+7", "BRL") == 700
    assert _amt_para_centavos("abc", "BRL") is None
    assert _amt_para_centavos("", "BRL") is None
    assert _amt_para_centavos(None, "BRL") is None


def test_ofx_data_invalida_vira_erro_por_linha_nao_excecao():
    ruim = OFX_SGML_CONTA.replace("<DTPOSTED>20260110", "<DTPOSTED>SEMDATA")
    ctx = _contexto(ruim.encode("cp1252"), "extrato.ofx")
    r = AdaptadorOfx().parse(ctx)
    assert len(r.transacoes) == 5  # as demais seguem valendo
    assert any(e.get("campo") == "DTPOSTED" and e["origem_ref"] == "STMTTRN 2" for e in r.erros)
    assert len(r.linhas_brutas) == 6  # a linha ruim continua auditável


# ---------------------------------------------------------------------------
# XML genérico — heurística de lançamentos
# ---------------------------------------------------------------------------

XML_LANCAMENTOS = """<?xml version="1.0" encoding="UTF-8"?>
<extrato>
  <lancamentos>
    <lancamento>
      <data>05/01/2026</data>
      <historico>PIX RECEBIDO CLIENTE FICTICIO</historico>
      <valor>1.500,00</valor>
      <documento>DOC1</documento>
    </lancamento>
    <lancamento>
      <data>06/01/2026</data>
      <historico>SUPERMERCADO SINTETICO</historico>
      <valor>-234,56</valor>
      <documento>DOC2</documento>
    </lancamento>
    <lancamento>
      <data>07/01/2026</data>
      <historico>TARIFA PACOTE</historico>
      <valor>-29,90</valor>
      <documento>DOC3</documento>
    </lancamento>
    <lancamento>
      <data>07/01/2026</data>
      <historico>TARIFA PACOTE</historico>
      <valor>-29,90</valor>
      <documento>DOC3</documento>
    </lancamento>
  </lancamentos>
</extrato>
"""


def test_xml_generico_reconhece_lancamentos():
    conteudo = XML_LANCAMENTOS.encode("utf-8")
    ctx = _contexto(conteudo, "extrato.xml", rotulo_origem="conta:Banco Sintético CC")

    assert ctx.formato.formato == "xml"
    adaptador = AdaptadorXmlGenerico()
    assert adaptador.detecta(ctx) > 0.0
    assert AdaptadorOfx().detecta(ctx) == 0.0  # não disputa com o OFX

    r = adaptador.parse(ctx)
    assert r.mapeamento_necessario is None
    assert r.erros == []
    assert len(r.transacoes) == 4

    t1 = r.transacoes[0]
    assert (t1.data_operacao, t1.valor_centavos) == (date(2026, 1, 5), 150000)
    assert (t1.direcao, t1.tipo) == ("credito", "receita")
    assert t1.origem_ref == "extrato/lancamentos/lancamento[1]"

    t2 = r.transacoes[1]
    assert (t2.valor_centavos, t2.direcao, t2.tipo) == (23456, "debito", "despesa")
    assert t2.documento == "DOC2"
    assert t2.origem_ref == "extrato/lancamentos/lancamento[2]"

    assert (r.periodo_inicio, r.periodo_fim) == (date(2026, 1, 5), date(2026, 1, 7))

    # Dois lançamentos legítimos idênticos: os dois entram, fingerprints distintas.
    assert r.transacoes[2].valor_centavos == r.transacoes[3].valor_centavos == 2990
    assert len({t.fingerprint for t in r.transacoes}) == 4


@pytest.mark.parametrize(
    "malicioso",
    [
        (
            "<?xml version=\"1.0\"?>\n"
            "<!DOCTYPE extrato [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]>\n"
            "<extrato><lancamento><data>01/01/2026</data><valor>10,00</valor>"
            "<texto>&xxe;</texto></lancamento></extrato>"
        ),
        (
            "<?xml version=\"1.0\"?>\n"
            "<!DOCTYPE extrato SYSTEM \"http://exemplo.invalido/extrato.dtd\">\n"
            "<extrato><lancamento><data>01/01/2026</data><valor>10,00</valor></lancamento></extrato>"
        ),
    ],
    ids=["entidade-externa", "dtd-externa"],
)
def test_xml_com_doctype_e_recusado_xxe(malicioso: str):
    ctx = _contexto(malicioso.encode("utf-8"), "extrato.xml")
    r = AdaptadorXmlGenerico().parse(ctx)
    assert r.transacoes == []
    assert r.linhas_brutas == []
    assert r.mapeamento_necessario is None
    assert len(r.erros) == 1
    mensagem = r.erros[0]["mensagem"].lower()
    assert "xxe" in mensagem and "doctype" in mensagem


XML_IRRECONHECIVEL = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <item><chave>tema</chave><conteudo>escuro</conteudo></item>
  <item><chave>idioma</chave><conteudo>pt-BR</conteudo></item>
  <item><chave>fuso</chave><conteudo>America/Sao_Paulo</conteudo></item>
</config>
"""


def test_xml_irreconhecivel_pede_mapeamento():
    ctx = _contexto(XML_IRRECONHECIVEL.encode("utf-8"), "config.xml")
    r = AdaptadorXmlGenerico().parse(ctx)
    assert r.transacoes == []
    assert r.mapeamento_necessario is not None

    caminhos = {info["caminho"]: info for info in r.mapeamento_necessario["caminhos"]}
    assert "config/item" in caminhos
    assert caminhos["config/item"]["ocorrencias"] == 3
    assert set(caminhos["config/item"]["campos"]) == {"chave", "conteudo"}
    assert r.mapeamento_necessario["amostra"]  # amostra dos registros achados
    assert r.mapeamento_necessario["amostra"][0] == {"chave": "tema", "conteudo": "escuro"}


def test_xml_generico_aceita_mapeamento_manual():
    # Mesmo XML "irreconhecível", mas o usuário mapeou os campos na UI.
    xml = """<?xml version="1.0"?>
    <regs>
      <reg><quando>02/03/2026</quando><quanto>-45,00</quanto><oque>ASSINATURA STREAMING</oque></reg>
      <reg><quando>03/03/2026</quando><quanto>120,00</quanto><oque>REEMBOLSO AMIGO</oque></reg>
    </regs>
    """
    ctx = _contexto(
        xml.encode("utf-8"),
        "extrato.xml",
        mapeamento={"quando": "data", "quanto": "valor", "oque": "descricao"},
    )
    r = AdaptadorXmlGenerico().parse(ctx)
    assert r.mapeamento_necessario is None
    assert [(t.valor_centavos, t.direcao) for t in r.transacoes] == [
        (4500, "debito"),
        (12000, "credito"),
    ]
    assert r.transacoes[0].descricao_original == "ASSINATURA STREAMING"
