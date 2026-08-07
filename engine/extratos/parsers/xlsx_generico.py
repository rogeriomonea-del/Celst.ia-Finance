"""Parser genérico de XLSX/XLSM de extratos — reusa o núcleo do CSV genérico.

Lê com openpyxl em modo ``read_only`` (macros NUNCA são executadas — openpyxl
só lê valores), percorre TODAS as abas visíveis e, em cada uma, aplica a
mesma detecção de cabeçalho/mapeamento do ``csv_generico``. A referência de
origem é ``"Aba!Ln"``.
"""

from __future__ import annotations

import io
from typing import Any, Callable, List, Tuple

from ..modelos import ResultadoParse
from .base import ContextoParse
from .csv_generico import (
    Contadores,
    linha_vazia,
    monta_mapeamento_necessario,
    processa_grade,
)


def _ref_aba(titulo: str) -> Callable[[int], str]:
    """Constrói o formatador de origem_ref ``"Aba!Ln"`` de uma aba."""
    return lambda numero: f"{titulo}!L{numero}"


class AdaptadorXlsxGenerico:
    """Planilha XLSX/XLSM genérica de extrato (multiabas)."""

    nome = "xlsx-generico"
    versao = "1"

    def detecta(self, contexto: ContextoParse) -> float:
        if contexto.formato.formato != "xlsx":
            return 0.0
        # Planilha de configuração tem adaptador próprio com prioridade.
        return 0.2 if contexto.tipo_documento == "planilha_config" else 0.55

    def parse(self, contexto: ContextoParse) -> ResultadoParse:
        resultado = ResultadoParse(adaptador=self.nome, moeda=contexto.moeda)
        try:
            import openpyxl

            pasta = openpyxl.load_workbook(
                io.BytesIO(contexto.conteudo), read_only=True, data_only=True
            )
        except Exception as erro:  # noqa: BLE001 - arquivo ruim nunca derruba
            resultado.avisos.append("planilha XLSX ilegível — arquivo corrompido?")
            resultado.erros.append({
                "origem_ref": "arquivo",
                "mensagem": f"falha ao abrir XLSX: {erro}",
            })
            return resultado

        contadores: Contadores = {}
        alguma_reconhecida = False
        abas_ignoradas: List[Tuple[str, List[Tuple[int, List[Any]]]]] = []
        try:
            for aba in pasta.worksheets:
                if getattr(aba, "sheet_state", "visible") != "visible":
                    continue  # abas ocultas ficam de fora
                grade: List[Tuple[int, List[Any]]] = []
                try:
                    for numero, linha in enumerate(
                        aba.iter_rows(values_only=True), start=1
                    ):
                        grade.append((numero, list(linha)))
                except Exception as erro:  # noqa: BLE001 - aba ruim vira erro
                    resultado.erros.append({
                        "origem_ref": f"{aba.title}!L1",
                        "mensagem": f"falha ao ler a aba: {erro}",
                    })
                    continue
                grade_util = [par for par in grade if not linha_vazia(par[1])]
                if not grade_util:
                    continue
                reconhecida = processa_grade(
                    grade_util,
                    contexto,
                    resultado,
                    ref=_ref_aba(aba.title),
                    contadores=contadores,
                )
                if reconhecida:
                    alguma_reconhecida = True
                else:
                    abas_ignoradas.append((aba.title, grade_util))
        finally:
            pasta.close()

        if alguma_reconhecida:
            for titulo, _ in abas_ignoradas:
                resultado.avisos.append(
                    f"aba '{titulo}' ignorada: cabeçalho não reconhecido"
                )
        elif abas_ignoradas:
            # Nenhuma aba entendida: pede mapeamento pela aba com mais linhas.
            titulo, grade_util = max(abas_ignoradas, key=lambda par: len(par[1]))
            resultado.mapeamento_necessario = monta_mapeamento_necessario(grade_util)
            resultado.mapeamento_necessario["aba"] = titulo
            resultado.avisos.append(
                f"nenhuma aba com cabeçalho reconhecido — informe o mapeamento "
                f"de colunas (aba '{titulo}')"
            )
        else:
            resultado.avisos.append("planilha sem dados nas abas visíveis")
        return resultado


ADAPTADORES = [AdaptadorXlsxGenerico()]
