"""Parser genérico de CSV de extratos bancários — e BASE das demais planilhas.

Toda a lógica de reconhecimento trabalha sobre uma "grade": lista de pares
``(numero_real_da_linha, [células])``. Isso abstrai a origem (CSV, XLSX,
tabela HTML), e por isso ``xlsx_generico`` e ``xls_legado`` importam daqui os
helpers públicos: ``processa_grade``, ``detecta_cabecalho``,
``monta_mapeamento_necessario``, ``neutraliza_formula``, ``identifica_campo``,
``decodifica_conteudo`` e ``linha_vazia``.

Regras herdadas do contrato (base.py): conteúdo ruim NUNCA vira exceção —
vira item em ``ResultadoParse.erros`` (por linha) ou aviso. Dinheiro é sempre
centavos ``int`` via ``dinheiro.parse_centavos``.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...workbook import as_date
from ..detect import decodifica_texto
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
# Vocabulário de mapeamento
# --------------------------------------------------------------------------

#: Campos que uma coluna pode assumir (também aceitos no mapeamento manual).
CAMPOS_DESTINO = (
    "data", "descricao", "valor", "debito", "credito", "saldo",
    "documento", "id_banco",
)
#: Valor especial do mapeamento manual: coluna descartada de propósito.
CAMPO_IGNORAR = "ignorar"
#: Quantas linhas iniciais são vasculhadas em busca do cabeçalho.
LINHAS_BUSCA_CABECALHO = 30
#: Quantas linhas entram na amostra de ``mapeamento_necessario``.
LINHAS_AMOSTRA = 8

#: Grade: (número REAL da linha no arquivo/aba, células da linha).
Grade = Sequence[Tuple[int, List[Any]]]
#: Contador de ocorrências por (data, valor, descrição) — escopo: UM arquivo.
Contadores = Dict[Tuple[str, int, str], int]

#: Sinônimos pt-BR de cabeçalho (já normalizados por ``_norm``).
_SINONIMOS_EXATOS: Dict[str, str] = {
    "data": "data", "dt": "data", "data mov": "data", "data movimento": "data",
    "data lancamento": "data", "data do lancamento": "data",
    "data de lancamento": "data", "data operacao": "data",
    "data da operacao": "data", "data compra": "data", "data da compra": "data",
    "descricao": "descricao", "historico": "descricao",
    "lancamento": "descricao", "lancamentos": "descricao",
    "detalhe": "descricao", "detalhes": "descricao",
    "estabelecimento": "descricao", "memo": "descricao",
    "valor": "valor", "quantia": "valor", "montante": "valor",
    "debito": "debito", "debitos": "debito", "saida": "debito",
    "saidas": "debito",
    "credito": "credito", "creditos": "credito", "entrada": "credito",
    "entradas": "credito",
    "saldo": "saldo",
    "documento": "documento", "doc": "documento", "n doc": "documento",
    "no doc": "documento", "num doc": "documento", "nr doc": "documento",
    "numero documento": "documento", "numero do documento": "documento",
    "identificador": "id_banco", "id": "id_banco", "codigo": "id_banco",
    "id transacao": "id_banco", "id da transacao": "id_banco",
    "codigo da transacao": "id_banco",
    "identificador da transacao": "id_banco",
}

#: Rótulos de linha que declaram saldo (fora da tabela de transações).
_PADRAO_SALDO = re.compile(r"saldo\s+(inicial|anterior|final|do\s+dia|em\s+\d)")
#: Parcela "3/10" numa descrição (sem casar pedaços de data "05/03/2026").
_PADRAO_PARCELA = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")
#: Primeiros caracteres perigosos para injeção de fórmula em planilhas.
_INICIO_FORMULA = ("=", "+", "-", "@")


# --------------------------------------------------------------------------
# Normalização e utilitários de célula
# --------------------------------------------------------------------------

def _norm(texto: Any) -> str:
    """Minúsculas, sem acento, só letras/dígitos — para casar cabeçalhos."""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(ch for ch in plano if not unicodedata.combining(ch))
    plano = re.sub(r"[^a-z0-9]+", " ", plano.lower())
    return re.sub(r"\s+", " ", plano).strip()


def _texto_simples(valor: Any) -> Optional[str]:
    """Célula → texto em uma linha (ou ``None`` quando vazia)."""
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        texto = str(int(valor))
    elif isinstance(valor, (datetime, date)):
        texto = valor.isoformat()
    else:
        texto = str(valor)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def _texto_multilinha(valor: Any) -> str:
    """Descrição possivelmente multilinha → uma linha (partes unidas)."""
    if valor is None:
        return ""
    partes = [parte.strip() for parte in str(valor).splitlines()]
    return " ".join(parte for parte in partes if parte)


def linha_vazia(celulas: Sequence[Any]) -> bool:
    """Linha sem nenhum conteúdo útil."""
    return not any(c is not None and str(c).strip() for c in celulas)


def neutraliza_formula(valor: Any) -> Any:
    """Neutraliza injeção de fórmula ao ecoar célula em ``conteudo_json``.

    Texto começando com ``= + - @`` ganha apóstrofo — EXCETO quando é um
    número legítimo (``-100,00`` continua legível). Números nativos e datas
    não injetam nada e passam como estão (datas viram ISO por serialização).
    """
    if valor is None:
        return ""
    if isinstance(valor, bool) or isinstance(valor, (int, float)):
        return valor
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    texto = str(valor)
    aparado = texto.lstrip()
    if aparado[:1] in _INICIO_FORMULA and parse_centavos(texto) is None:
        return "'" + texto
    return texto


# --------------------------------------------------------------------------
# Reconhecimento de cabeçalho
# --------------------------------------------------------------------------

def identifica_campo(rotulo: Any) -> Optional[str]:
    """Mapeia um rótulo de coluna pt-BR para um campo de ``CAMPOS_DESTINO``."""
    chave = _norm(rotulo)
    if not chave:
        return None
    if chave in _SINONIMOS_EXATOS:
        return _SINONIMOS_EXATOS[chave]
    palavras = set(chave.split())
    if "data" in palavras:
        return "data"
    if "debito" in palavras:
        return "debito"
    if "credito" in palavras:
        return "credito"
    if chave.startswith("saldo"):
        return "saldo"
    if palavras & {"valor", "quantia", "montante"}:
        return "valor"
    if palavras & {"descricao", "historico", "lancamento", "detalhe", "detalhes"}:
        return "descricao"
    if "documento" in palavras:
        return "documento"
    if palavras & {"identificador", "codigo", "id"}:
        return "id_banco"
    return None


def detecta_cabecalho(grade: Grade) -> Optional[Tuple[int, Dict[int, str]]]:
    """Procura a linha de cabeçalho nas primeiras ``LINHAS_BUSCA_CABECALHO``.

    Devolve ``(posição na grade, {índice da coluna: campo})`` quando encontra
    uma linha com ``data`` e pelo menos um lado de valor (``valor`` OU
    ``debito``/``credito``); ``None`` caso contrário.
    """
    for pos, (_, celulas) in enumerate(grade[:LINHAS_BUSCA_CABECALHO]):
        colunas: Dict[int, str] = {}
        for indice, celula in enumerate(celulas):
            if not isinstance(celula, str):
                continue  # cabeçalho é texto; números/datas são dados
            campo = identifica_campo(celula)
            if campo and campo not in colunas.values():
                colunas[indice] = campo
        campos = set(colunas.values())
        if "data" in campos and campos & {"valor", "debito", "credito"}:
            return pos, colunas
    return None


def monta_mapeamento_necessario(grade: Grade) -> Dict[str, Any]:
    """Pacote para a UI montar o mapeador de colunas (nada é importado ainda).

    ``colunas`` vem da primeira linha útil (provável cabeçalho não
    reconhecido); ``sugestoes`` chuta campo por rótulo e, na falta dele, pelo
    conteúdo das linhas seguintes (datas → data, números → valor/saldo,
    texto → descricao).
    """
    amostra_pares = list(grade[:LINHAS_AMOSTRA])
    total_colunas = max((len(celulas) for _, celulas in amostra_pares), default=0)
    primeira = amostra_pares[0][1] if amostra_pares else []
    colunas: List[str] = []
    for indice in range(total_colunas):
        rotulo = _texto_simples(primeira[indice]) if indice < len(primeira) else None
        colunas.append(rotulo or f"coluna {indice + 1}")
    amostra = [
        [neutraliza_formula(celula) for celula in celulas]
        for _, celulas in amostra_pares
    ]

    sugestoes: Dict[str, str] = {}
    for rotulo in colunas:
        campo = identifica_campo(rotulo)
        if campo and campo not in sugestoes.values():
            sugestoes[rotulo] = campo
    corpo = amostra_pares[1:]
    for indice, rotulo in enumerate(colunas):
        if rotulo in sugestoes:
            continue
        valores = [
            celulas[indice]
            for _, celulas in corpo
            if indice < len(celulas) and _texto_simples(celulas[indice])
        ]
        if not valores:
            continue
        datas = sum(1 for v in valores if as_date(v) is not None)
        numeros = sum(
            1 for v in valores
            if as_date(v) is None and parse_centavos(v) is not None
        )
        campo: Optional[str] = None
        if datas * 2 >= len(valores):
            campo = "data"
        elif numeros * 2 >= len(valores):
            campo = "valor" if "valor" not in sugestoes.values() else "saldo"
        elif "descricao" not in sugestoes.values():
            campo = "descricao"
        if campo and campo not in sugestoes.values():
            sugestoes[rotulo] = campo
    return {"colunas": colunas, "amostra": amostra, "sugestoes": sugestoes}


def _resolve_mapeamento(
    grade: Grade,
    mapeamento: Dict[str, str],
    resultado: ResultadoParse,
) -> Optional[Tuple[int, Dict[int, str]]]:
    """Aplica o mapeamento manual (2ª passada) → ``(início dos dados, colunas)``.

    Chaves podem ser índice de coluna (``"0"``, ``"2"``) ou o texto do
    cabeçalho original. Devolve ``None`` (com aviso) quando o mapeamento não
    fecha — o chamador volta a pedir ``mapeamento_necessario``.
    """
    indices: Dict[int, str] = {}
    nomes: Dict[str, str] = {}
    for chave, campo in mapeamento.items():
        campo_norm = str(campo).strip().lower()
        if campo_norm == CAMPO_IGNORAR:
            continue
        if campo_norm not in CAMPOS_DESTINO:
            resultado.avisos.append(
                f"mapeamento: campo desconhecido '{campo}' foi ignorado"
            )
            continue
        chave_texto = str(chave).strip()
        if chave_texto.isdigit():
            indices[int(chave_texto)] = campo_norm
        else:
            nomes[_norm(chave_texto)] = campo_norm

    inicio_dados: Optional[int] = None
    if nomes:
        # Cabeçalho = linha (entre as primeiras 30) com mais nomes casando.
        melhor_pos: Optional[int] = None
        melhor_acertos = 0
        for pos, (_, celulas) in enumerate(grade[:LINHAS_BUSCA_CABECALHO]):
            acertos = sum(
                1 for c in celulas if isinstance(c, str) and _norm(c) in nomes
            )
            if acertos > melhor_acertos:
                melhor_pos, melhor_acertos = pos, acertos
        if melhor_pos is None:
            resultado.avisos.append(
                "mapeamento: nenhuma linha de cabeçalho casa com os nomes informados"
            )
            return None
        for indice, celula in enumerate(grade[melhor_pos][1]):
            if not isinstance(celula, str):
                continue
            campo = nomes.get(_norm(celula))
            if campo and campo not in indices.values():
                indices.setdefault(indice, campo)
        inicio_dados = melhor_pos + 1

    campos = set(indices.values())
    if "data" not in campos or not campos & {"valor", "debito", "credito"}:
        resultado.avisos.append(
            "mapeamento incompleto: informe 'data' e 'valor' (ou 'debito'/'credito')"
        )
        return None

    if inicio_dados is None:
        # Só índices: os dados começam na 1ª linha cuja coluna de data parseia.
        coluna_data = next(i for i, campo in indices.items() if campo == "data")
        for pos, (_, celulas) in enumerate(grade):
            celula = celulas[coluna_data] if coluna_data < len(celulas) else None
            if as_date(celula) is not None:
                inicio_dados = pos
                break
        if inicio_dados is None:
            resultado.avisos.append(
                "mapeamento: nenhuma linha com data válida na coluna indicada"
            )
            return None
    return inicio_dados, indices


# --------------------------------------------------------------------------
# Saldos declarados fora da tabela
# --------------------------------------------------------------------------

def _procura_saldo(celulas: Sequence[Any]) -> Optional[Tuple[str, int, Optional[date]]]:
    """Detecta linha "saldo inicial/anterior/final" → (rótulo, centavos, data?)."""
    texto = " ".join(
        _norm(c) for c in celulas if c is not None and str(c).strip()
    )
    achado = _PADRAO_SALDO.search(texto)
    if not achado:
        return None
    valor: Optional[int] = None
    data_saldo: Optional[date] = None
    for celula in celulas:
        if celula is None or not str(celula).strip():
            continue
        if not isinstance(celula, (int, float)) and as_date(celula) is not None:
            if data_saldo is None:
                data_saldo = as_date(celula)
            continue
        centavos = parse_centavos(celula)
        if centavos is not None:
            valor = centavos  # a última célula numérica é o saldo
    if valor is None:
        return None
    return achado.group(0), valor, data_saldo


def _extrai_parcela(descricao: str) -> Tuple[Optional[int], Optional[int]]:
    """"LOJA PARC 3/10" → (3, 10); sem parcela → (None, None)."""
    for achado in _PADRAO_PARCELA.finditer(descricao):
        numero, total = int(achado.group(1)), int(achado.group(2))
        if 1 <= numero <= total and total >= 2:
            return numero, total
    return None, None


# --------------------------------------------------------------------------
# Núcleo compartilhado: grade → transações
# --------------------------------------------------------------------------

def processa_grade(
    grade: Grade,
    contexto: ContextoParse,
    resultado: ResultadoParse,
    *,
    ref: Callable[[int], str],
    contadores: Contadores,
) -> bool:
    """Processa uma grade inteira para dentro de ``resultado``.

    Devolve ``True`` quando reconheceu cabeçalho (ou aplicou o mapeamento
    manual) e produziu linhas brutas + transações; ``False`` quando não há
    como interpretar a grade — o chamador decide se pede mapeamento.
    ``contadores`` deve ser compartilhado entre TODAS as grades do mesmo
    arquivo (multiabas) para a numeração de ocorrência ficar por arquivo.
    """
    grade = [par for par in grade if not linha_vazia(par[1])]
    if not grade:
        return False

    if contexto.mapeamento:
        resolvido = _resolve_mapeamento(grade, contexto.mapeamento, resultado)
        if resolvido is None:
            return False
        inicio_dados, colunas = resolvido
    else:
        achado = detecta_cabecalho(grade)
        if achado is None:
            return False
        posicao_cabecalho, colunas = achado
        inicio_dados = posicao_cabecalho + 1

    coluna_de = {campo: indice for indice, campo in colunas.items()}

    def celula(campo: str, celulas: Sequence[Any]) -> Any:
        indice = coluna_de.get(campo)
        if indice is None or indice >= len(celulas):
            return None
        return celulas[indice]

    # Espelho auditável: TODA linha útil vira linha bruta (com neutralização).
    for numero, celulas in grade:
        resultado.linhas_brutas.append(
            LinhaBruta(
                ordem=len(resultado.linhas_brutas) + 1,
                origem_ref=ref(numero),
                conteudo={"celulas": [neutraliza_formula(c) for c in celulas]},
            )
        )

    saldos_pendentes: List[Tuple[str, int, Optional[date]]] = []
    datas: List[date] = []

    # Antes dos dados: linhas soltas podem declarar "saldo inicial/anterior".
    for numero, celulas in grade[:inicio_dados]:
        achado_saldo = _procura_saldo(celulas)
        if achado_saldo:
            saldos_pendentes.append(achado_saldo)

    usa_valor_unico = "valor" in coluna_de
    for numero, celulas in grade[inicio_dados:]:
        achado_saldo = _procura_saldo(celulas)
        if achado_saldo:
            saldos_pendentes.append(achado_saldo)
            continue

        data_operacao = as_date(celula("data", celulas))
        if data_operacao is None:
            resultado.erros.append({
                "origem_ref": ref(numero),
                "campo": "data",
                "mensagem": "data inválida ou ausente — linha ignorada",
            })
            continue

        if usa_valor_unico:
            centavos = parse_centavos(celula("valor", celulas), contexto.moeda)
            if not centavos:  # None ou zero: nada a lançar
                resultado.erros.append({
                    "origem_ref": ref(numero),
                    "campo": "valor",
                    "mensagem": "valor ausente, zero ou não numérico — linha ignorada",
                })
                continue
            direcao = "credito" if centavos > 0 else "debito"
            valor_centavos = abs(centavos)
        else:
            credito = parse_centavos(celula("credito", celulas), contexto.moeda)
            debito = parse_centavos(celula("debito", celulas), contexto.moeda)
            if credito and debito:
                resultado.erros.append({
                    "origem_ref": ref(numero),
                    "campo": "valor",
                    "mensagem": "linha com débito E crédito preenchidos — revise o arquivo",
                })
                continue
            if credito:
                direcao, valor_centavos = "credito", abs(credito)
            elif debito:
                direcao, valor_centavos = "debito", abs(debito)
            else:
                resultado.erros.append({
                    "origem_ref": ref(numero),
                    "campo": "valor",
                    "mensagem": "sem valor nas colunas de débito/crédito — linha ignorada",
                })
                continue

        descricao = _texto_multilinha(celula("descricao", celulas))
        documento = _texto_simples(celula("documento", celulas))
        id_banco = _texto_simples(celula("id_banco", celulas))
        saldo_bruto = celula("saldo", celulas)
        saldo_apos = (
            parse_centavos(saldo_bruto, contexto.moeda)
            if saldo_bruto is not None and str(saldo_bruto).strip()
            else None
        )
        parcela_num, parcela_total = _extrai_parcela(descricao)

        chave = (data_operacao.isoformat(), valor_centavos, normaliza_descricao(descricao))
        contadores[chave] = contadores.get(chave, 0) + 1
        ocorrencia = contadores[chave]

        resultado.transacoes.append(
            Transacao(
                data_operacao=data_operacao,
                valor_centavos=valor_centavos,
                moeda=contexto.moeda,
                direcao=direcao,
                # Tipo inicial pela direção; o pipeline refina depois
                # (pagamento_fatura, transferência, compra de cartão…).
                tipo="receita" if direcao == "credito" else "despesa",
                descricao_original=descricao,
                descricao_normalizada=normaliza_descricao(descricao),
                fingerprint=fingerprint_transacao(
                    contexto.rotulo_origem,
                    data_operacao,
                    valor_centavos,
                    contexto.moeda,
                    descricao,
                    documento,
                    ocorrencia,
                ),
                origem_ref=ref(numero),
                documento=documento,
                id_banco=id_banco,
                saldo_apos_centavos=saldo_apos,
                parcela_num=parcela_num,
                parcela_total=parcela_total,
            )
        )
        datas.append(data_operacao)

    if datas:
        inicio, fim = min(datas), max(datas)
        resultado.periodo_inicio = min(resultado.periodo_inicio or inicio, inicio)
        resultado.periodo_fim = max(resultado.periodo_fim or fim, fim)

    for rotulo, valor, data_saldo in saldos_pendentes:
        if data_saldo is None and datas:
            # Sem data explícita: inicial/anterior ancora no começo do
            # período; final, no fim.
            data_saldo = max(datas) if "final" in rotulo else min(datas)
        if data_saldo is None:
            resultado.avisos.append(
                f"linha de '{rotulo}' sem data identificável — saldo ignorado"
            )
            continue
        resultado.saldos.append(
            SaldoInformado(data=data_saldo, valor_centavos=valor, rotulo=rotulo)
        )
    return True


# --------------------------------------------------------------------------
# Decodificação compartilhada (CSV e xls-HTML)
# --------------------------------------------------------------------------

def decodifica_conteudo(contexto: ContextoParse) -> Optional[str]:
    """Decodifica os bytes com o encoding detectado; cai para a heurística."""
    if contexto.formato.encoding:
        try:
            return contexto.conteudo.decode(contexto.formato.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    texto, _ = decodifica_texto(contexto.conteudo)
    return texto


def _delimitador(texto: str, contexto: ContextoParse) -> str:
    """Delimitador do contexto ou contagem simples nas primeiras linhas."""
    if contexto.formato.delimitador:
        return contexto.formato.delimitador
    linhas = [linha for linha in texto.splitlines()[:20] if linha.strip()]
    contagens = {d: sum(linha.count(d) for linha in linhas) for d in (";", ",", "\t", "|")}
    melhor = max(contagens, key=lambda d: contagens[d])
    return melhor if contagens[melhor] else ";"


# --------------------------------------------------------------------------
# Adaptador
# --------------------------------------------------------------------------

class AdaptadorCsvGenerico:
    """CSV genérico de banco (pt-BR e internacional)."""

    nome = "csv-generico"
    versao = "1"

    def detecta(self, contexto: ContextoParse) -> float:
        return 0.6 if contexto.formato.formato == "csv" else 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:
        resultado = ResultadoParse(adaptador=self.nome, moeda=contexto.moeda)
        texto = decodifica_conteudo(contexto)
        if texto is None:
            resultado.avisos.append("não foi possível decodificar o arquivo como texto")
            resultado.erros.append({
                "origem_ref": "arquivo",
                "mensagem": "conteúdo binário ou encoding não reconhecido",
            })
            return resultado

        grade: List[Tuple[int, List[Any]]] = []
        leitor = csv.reader(
            io.StringIO(texto, newline=""),
            delimiter=_delimitador(texto, contexto),
        )
        ultima_linha_fisica = 0
        try:
            for celulas in leitor:
                # Uma célula multilinha ocupa várias linhas físicas: o número
                # REAL da linha onde o registro começa é o que vai na origem.
                inicio = ultima_linha_fisica + 1
                ultima_linha_fisica = leitor.line_num
                grade.append((inicio, list(celulas)))
        except csv.Error as erro:
            resultado.erros.append({
                "origem_ref": f"linha {ultima_linha_fisica + 1}",
                "mensagem": f"CSV malformado: {erro}",
            })

        grade_util = [par for par in grade if not linha_vazia(par[1])]
        if not grade_util:
            resultado.avisos.append("arquivo CSV sem linhas com conteúdo")
            return resultado

        contadores: Contadores = {}
        reconhecido = processa_grade(
            grade_util,
            contexto,
            resultado,
            ref=lambda numero: f"linha {numero}",
            contadores=contadores,
        )
        if not reconhecido and resultado.mapeamento_necessario is None:
            resultado.mapeamento_necessario = monta_mapeamento_necessario(grade_util)
            resultado.avisos.append(
                "cabeçalho não reconhecido — informe o mapeamento de colunas"
            )
        return resultado


ADAPTADORES = [AdaptadorCsvGenerico()]
