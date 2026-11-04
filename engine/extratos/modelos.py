"""Tipos de domínio do módulo de extratos.

Dataclasses puros e enums de texto (guardados como TEXT no SQLite). Dinheiro é
sempre ``int`` de centavos (ver ``dinheiro.py``); datas são ``datetime.date``
no domínio e ISO-8601 no banco.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Vocabulários (valores gravados no banco — não renomear sem migração)
# --------------------------------------------------------------------------

TIPOS_DOCUMENTO = ("extrato_conta", "fatura_cartao", "planilha_config")
STATUS_ARQUIVO = (
    "recebido", "validando", "processando", "aguardando_revisao",
    "pronto", "confirmado", "falhou", "revertido",
)
DIRECOES = ("credito", "debito")
TIPOS_TRANSACAO = (
    "receita", "despesa", "transferencia", "compra", "estorno",
    "tarifa", "juros", "pagamento_fatura", "ajuste",
)
STATUS_CONCILIACAO = ("pendente", "conciliada", "divergente", "ignorada")
STATUS_FATURA = ("aberta", "fechada", "vencida", "paga", "parcial")
ORIGENS_CATEGORIA = ("regra", "manual", "importacao")

#: Palavras que identificam pagamento de fatura numa descrição de extrato.
PADRAO_PAGAMENTO_FATURA = re.compile(
    r"pagamento.{0,20}(fatura|cart[aã]o)|pgto.{0,12}(fatura|cart[aã]o)|"
    r"fatura.{0,12}(paga|pagamento)|deb.{0,10}autom.{0,10}fatura",
    re.IGNORECASE,
)
#: Palavras que sugerem transferência entre contas próprias.
PADRAO_TRANSFERENCIA = re.compile(
    r"\b(transfer[eê]ncia|transf|ted|doc|pix)\b.{0,30}\b(entre contas|mesma titularidade|pr[oó]pri[ao])\b|"
    r"\baplica[cç][aã]o\b|\bresgate\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Normalização de texto (uma definição só, usada por dedup e busca)
# --------------------------------------------------------------------------

def normaliza_descricao(texto: Optional[str]) -> str:
    """Minúsculas, sem acento, espaços colapsados — estável para fingerprint."""
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(ch for ch in plano if not unicodedata.combining(ch))
    plano = re.sub(r"\s+", " ", plano.lower()).strip()
    return plano


def fingerprint_transacao(
    conta_ou_cartao: str,
    data_operacao: date,
    valor_centavos: int,
    moeda: str,
    descricao: Optional[str],
    documento: Optional[str] = None,
    ocorrencia: int = 1,
) -> str:
    """Impressão digital estável de uma transação para deduplicação.

    ``ocorrencia`` distingue lançamentos legítimos idênticos dentro do mesmo
    documento (duas compras iguais no mesmo dia): o normalizador atribui 1, 2,
    3… na ordem do arquivo, e dois arquivos do mesmo período geram as mesmas
    fingerprints — o que torna a reimportação idempotente.
    """
    base = "|".join(
        [
            conta_ou_cartao,
            data_operacao.isoformat(),
            str(valor_centavos),
            moeda.upper(),
            normaliza_descricao(descricao),
            normaliza_descricao(documento),
            str(ocorrencia),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Entidades
# --------------------------------------------------------------------------


@dataclass
class Instituicao:
    nome: str
    tipo: str = "banco"  # banco | corretora | emissor_cartao | outro
    id: Optional[int] = None


@dataclass
class Conta:
    nome: str
    instituicao_id: Optional[int] = None
    tipo: str = "corrente"  # corrente | poupanca | pagamento | investimento
    moeda: str = "BRL"
    apelido: str = ""
    id: Optional[int] = None


@dataclass
class Cartao:
    nome: str
    instituicao_id: Optional[int] = None
    emissor: Optional[str] = None
    bandeira: Optional[str] = None
    final: Optional[str] = None  # só os últimos 4 dígitos, nunca o número completo
    conta_pagadora_id: Optional[int] = None
    dia_fechamento: Optional[int] = None
    dia_vencimento: Optional[int] = None
    limite_total_centavos: Optional[int] = None
    moeda: str = "BRL"
    id: Optional[int] = None


@dataclass
class Fatura:
    cartao_id: int
    competencia: str  # AAAA-MM (mês do vencimento)
    vence_em: date
    fecha_em: Optional[date] = None
    valor_centavos: Optional[int] = None  # total da fatura, quando conhecido
    pago_centavos: int = 0
    status: str = "aberta"
    origem: str = "importacao"
    id: Optional[int] = None


@dataclass
class ArquivoImportado:
    sha256: str
    nome_original: str
    mime_real: str
    tamanho: int
    tipo_documento: str
    caminho_objeto: str
    instituicao_id: Optional[int] = None
    conta_id: Optional[int] = None
    cartao_id: Optional[int] = None
    periodo_inicio: Optional[date] = None
    periodo_fim: Optional[date] = None
    status: str = "recebido"
    versao_parser: str = ""
    erro: Optional[str] = None
    id: Optional[int] = None


@dataclass
class LinhaBruta:
    """Espelho auditável de uma linha/registro do documento original."""

    ordem: int
    origem_ref: str  # "Aba!L12" | "página 3" | "linha 45"
    conteudo: Dict[str, Any]
    id: Optional[int] = None


@dataclass
class Transacao:
    data_operacao: date
    valor_centavos: int  # sempre positivo; o lado vem de ``direcao``
    moeda: str
    direcao: str  # credito | debito
    tipo: str  # TIPOS_TRANSACAO
    descricao_original: str
    descricao_normalizada: str
    fingerprint: str
    origem_ref: str
    conta_id: Optional[int] = None
    cartao_id: Optional[int] = None
    fatura_id: Optional[int] = None
    lote_id: Optional[int] = None
    data_lancamento: Optional[date] = None
    data_compensacao: Optional[date] = None
    contraparte: Optional[str] = None
    id_banco: Optional[str] = None  # FITID e afins
    documento: Optional[str] = None
    saldo_apos_centavos: Optional[int] = None
    parcela_num: Optional[int] = None
    parcela_total: Optional[int] = None
    categoria_id: Optional[int] = None
    origem_categoria: Optional[str] = None
    confianca: Optional[float] = None
    status_conciliacao: str = "pendente"
    id: Optional[int] = None


@dataclass
class SaldoInformado:
    """Saldo que o próprio documento declara (inicial/final)."""

    data: date
    valor_centavos: int
    origem: str = "extrato"  # extrato | calculado
    rotulo: str = ""  # "saldo inicial" | "saldo final" | livre


@dataclass
class ResultadoParse:
    """O que um adaptador devolve — nada disso toca o banco ainda."""

    linhas_brutas: List[LinhaBruta] = field(default_factory=list)
    transacoes: List[Transacao] = field(default_factory=list)
    saldos: List[SaldoInformado] = field(default_factory=list)
    periodo_inicio: Optional[date] = None
    periodo_fim: Optional[date] = None
    moeda: str = "BRL"
    avisos: List[str] = field(default_factory=list)
    erros: List[Dict[str, Any]] = field(default_factory=list)  # {origem_ref, campo?, mensagem}
    mapeamento_necessario: Optional[Dict[str, Any]] = None  # colunas + amostra p/ UI
    adaptador: str = ""

    @property
    def ok(self) -> bool:
        return self.mapeamento_necessario is None and bool(self.transacoes)


@dataclass
class Categoria:
    nome: str
    pai_id: Optional[int] = None
    cor: Optional[str] = None
    id: Optional[int] = None


@dataclass
class RegraCategoria:
    campo: str  # descricao | contraparte
    operador: str  # contem | igual | regex | comeca
    valor: str
    categoria_id: int
    prioridade: int = 100
    ativo: bool = True
    conta_id: Optional[int] = None
    cartao_id: Optional[int] = None
    valor_min_centavos: Optional[int] = None
    valor_max_centavos: Optional[int] = None
    id: Optional[int] = None


def deriva_status_fatura(fatura: Fatura, hoje: date) -> str:
    """Regra única de status de fatura (a mesma da planilha do usuário).

    Paga se o pagamento cobre o valor; parcial se cobre parte; vencida se
    passou do vencimento sem quitar; fechada entre fechamento e vencimento;
    aberta no restante. Fatura sem valor conhecido e sem pagamento vence
    "em aberto" — vencida depois do prazo, como o Excel fazia.
    """
    valor = fatura.valor_centavos
    pago = fatura.pago_centavos or 0
    if valor is not None and valor > 0 and pago >= valor:
        return "paga"
    if valor is None and pago > 0:
        # A planilha do usuário registra só o pagamento: pago sem valor = paga.
        return "paga"
    if 0 < pago < (valor or 0):
        return "parcial"
    if fatura.vence_em < hoje:
        return "vencida"
    if fatura.fecha_em is not None and fatura.fecha_em <= hoje:
        return "fechada"
    return "aberta"
