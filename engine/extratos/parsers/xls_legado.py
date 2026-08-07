"""Parser de ".xls" legado: BIFF binário (recusado com clareza) e xls-HTML.

A maioria dos ".xls" de banco brasileiro é HTML disfarçado — uma página com
``<table>``. Aqui a maior tabela vira grade e reutiliza a detecção de
cabeçalho do ``csv_generico``. O XLS binário de verdade (OLE2/BIFF) não é
suportado: o resultado explica e pede exportação em XLSX ou CSV.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, List, Optional, Tuple

from ..modelos import ResultadoParse
from .base import ContextoParse
from .csv_generico import (
    Contadores,
    decodifica_conteudo,
    linha_vazia,
    monta_mapeamento_necessario,
    processa_grade,
)

AVISO_BIFF = "XLS binário antigo não suportado — exporte como XLSX ou CSV"


class _ExtratorTabelas(HTMLParser):
    """Coleta cada ``<table>`` como lista de linhas de células (texto puro).

    ``convert_charrefs=False`` para as entidades chegarem cruas e serem
    decodificadas explicitamente com ``html.unescape`` (``&oacute;`` → ``ó``).
    ``<br>`` vira quebra de linha dentro da célula (descrição multilinha).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tabelas: List[List[List[str]]] = []
        self._pilha: List[List[List[str]]] = []  # tabelas abertas (aninhadas)
        self._linha: Optional[List[str]] = None
        self._celula: Optional[List[str]] = None

    # -- abertura/fechamento -------------------------------------------------
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._fecha_linha()
            self._pilha.append([])
        elif tag == "tr" and self._pilha:
            self._fecha_linha()
            self._linha = []
        elif tag in ("td", "th") and self._pilha:
            self._fecha_celula()
            if self._linha is None:
                self._linha = []
            self._celula = []
        elif tag == "br" and self._celula is not None:
            self._celula.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th"):
            self._fecha_celula()
        elif tag == "tr":
            self._fecha_linha()
        elif tag == "table" and self._pilha:
            self._fecha_linha()
            self.tabelas.append(self._pilha.pop())

    # -- conteúdo ------------------------------------------------------------
    def handle_data(self, dados: str) -> None:
        if self._celula is not None:
            self._celula.append(dados)

    def handle_entityref(self, nome: str) -> None:
        if self._celula is not None:
            self._celula.append(html.unescape(f"&{nome};"))

    def handle_charref(self, nome: str) -> None:
        if self._celula is not None:
            self._celula.append(html.unescape(f"&#{nome};"))

    # -- montagem ------------------------------------------------------------
    def _fecha_celula(self) -> None:
        if self._celula is None:
            return
        bruto = "".join(self._celula)
        partes = [re.sub(r"\s+", " ", parte).strip() for parte in bruto.split("\n")]
        texto = "\n".join(parte for parte in partes if parte)
        if self._linha is None:
            self._linha = []
        self._linha.append(texto)
        self._celula = None

    def _fecha_linha(self) -> None:
        self._fecha_celula()
        if self._linha is not None and self._pilha:
            self._pilha[-1].append(self._linha)
        self._linha = None


def extrai_tabelas_html(texto: str) -> List[List[List[str]]]:
    """Todas as ``<table>`` do documento como grades de texto."""
    extrator = _ExtratorTabelas()
    try:
        extrator.feed(texto)
        extrator.close()
    except Exception:  # noqa: BLE001 - HTML podre não derruba o parse
        pass
    return [tabela for tabela in extrator.tabelas if tabela]


class AdaptadorXlsLegado:
    """".xls" de banco: HTML disfarçado (suportado) ou BIFF (recusado)."""

    nome = "xls-legado"
    versao = "1"

    def detecta(self, contexto: ContextoParse) -> float:
        formato = contexto.formato.formato
        if formato in ("xls_biff", "xls_html"):
            return 0.8
        if formato == "html":
            return 0.5
        return 0.0

    def parse(self, contexto: ContextoParse) -> ResultadoParse:
        resultado = ResultadoParse(adaptador=self.nome, moeda=contexto.moeda)

        if contexto.formato.formato == "xls_biff":
            resultado.avisos.append(AVISO_BIFF)
            resultado.erros.append({
                "origem_ref": "arquivo",
                "mensagem": (
                    "o arquivo está no formato XLS binário antigo (OLE2/BIFF), "
                    "que este importador não lê; abra no Excel ou LibreOffice e "
                    "salve como XLSX ou CSV antes de importar"
                ),
            })
            return resultado

        texto = decodifica_conteudo(contexto)
        if texto is None:
            resultado.avisos.append("não foi possível decodificar o arquivo como texto")
            resultado.erros.append({
                "origem_ref": "arquivo",
                "mensagem": "conteúdo binário ou encoding não reconhecido",
            })
            return resultado

        tabelas = extrai_tabelas_html(texto)
        if not tabelas:
            resultado.avisos.append("nenhuma <table> encontrada no documento HTML")
            resultado.erros.append({
                "origem_ref": "arquivo",
                "mensagem": "documento HTML sem tabelas — nada para importar",
            })
            return resultado

        # A maior tabela (em linhas) é o extrato; as menores são cabeçalhos
        # visuais do banco (logotipo, titular etc.).
        tabela = max(tabelas, key=len)
        grade: List[Tuple[int, List[Any]]] = [
            (numero, list(linha)) for numero, linha in enumerate(tabela, start=1)
        ]
        grade_util = [par for par in grade if not linha_vazia(par[1])]
        if not grade_util:
            resultado.avisos.append("tabela HTML sem linhas com conteúdo")
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


ADAPTADORES = [AdaptadorXlsLegado()]
