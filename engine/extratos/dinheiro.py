"""Dinheiro em centavos inteiros — nunca float.

Todo valor monetário do módulo de extratos trafega como ``int`` de centavos
(mais precisamente, da menor unidade da moeda). O parse é feito por
manipulação de string, sem passar por ``float`` em nenhum momento, para que
``"1.234,56"`` vire exatamente ``123456`` — e não ``123455.99999``.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

#: Moedas com número de casas diferente de 2 (ISO 4217). O restante assume 2.
CASAS_POR_MOEDA = {
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0,
    "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "VND": 0, "VUV": 0, "XAF": 0,
    "XOF": 0, "XPF": 0,
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}

_MOEDA_VALIDA = re.compile(r"^[A-Z]{3}$")
_LIMPEZA = re.compile(r"[^\d,.\-+()]")


def casas_da_moeda(moeda: str) -> int:
    return CASAS_POR_MOEDA.get(moeda.upper(), 2)


def moeda_valida(moeda: str) -> bool:
    return bool(_MOEDA_VALIDA.match(moeda.strip().upper()))


def parse_centavos(texto: object, moeda: str = "BRL") -> Optional[int]:
    """Converte texto monetário em centavos, sem float.

    Aceita os formatos brasileiro (``1.234,56``), internacional (``1,234.56``),
    sinal por ``-``, por parênteses contábeis ``(123,45)`` e símbolos/letras
    coladas (``R$ 1.234,56``, ``BRL 10,00``). Devolve ``None`` quando o texto
    não é um número — quem chama decide se isso é erro ou célula vazia.
    """
    if texto is None:
        return None
    if isinstance(texto, bool):
        return None
    if isinstance(texto, int):
        return texto * (10 ** casas_da_moeda(moeda))
    if isinstance(texto, float):
        # Floats só chegam aqui vindos de células do Excel (openpyxl). A menor
        # perda já aconteceu no arquivo; arredondamos para a casa da moeda de
        # forma determinística via string com folga de precisão.
        texto = f"{texto:.10f}"

    bruto = str(texto).strip()
    if not bruto:
        return None

    negativo = False
    if "(" in bruto and ")" in bruto:  # convenção contábil: (123,45) = -123,45
        negativo = True
    limpo = _LIMPEZA.sub("", bruto).replace("(", "").replace(")", "")
    if not limpo:
        return None
    if limpo.startswith("+"):
        limpo = limpo[1:]
    if limpo.startswith("-"):
        negativo = True
        limpo = limpo[1:]
    limpo = limpo.replace("-", "")
    if not limpo or limpo in (".", ","):
        return None

    inteiro, fracao = _separa_inteiro_fracao(limpo)
    if inteiro is None:
        return None

    casas = casas_da_moeda(moeda)
    fracao = (fracao or "")[: casas + 1]
    # Arredondamento meio-para-cima na casa seguinte, sem float.
    if len(fracao) == casas + 1:
        extra = int(fracao[-1])
        fracao = fracao[:-1]
        base = int(inteiro or "0") * (10**casas) + int(fracao or "0".zfill(casas) or "0")
        if extra >= 5:
            base += 1
    else:
        fracao = fracao.ljust(casas, "0")
        base = int(inteiro or "0") * (10**casas) + (int(fracao) if casas else 0)
    return -base if negativo else base


def _separa_inteiro_fracao(limpo: str) -> Tuple[Optional[str], str]:
    """Decide qual separador é decimal olhando a posição e o padrão de grupos."""
    tem_virgula = "," in limpo
    tem_ponto = "." in limpo
    if tem_virgula and tem_ponto:
        # O separador decimal é o que aparece por último.
        if limpo.rfind(",") > limpo.rfind("."):
            inteiro, _, fracao = limpo.replace(".", "").rpartition(",")
        else:
            inteiro, _, fracao = limpo.replace(",", "").rpartition(".")
        return _apenas_digitos(inteiro), _so_digitos(fracao)
    if tem_virgula or tem_ponto:
        sep = "," if tem_virgula else "."
        partes = limpo.split(sep)
        # "1.234.567" ou "1,234,567" → separadores de milhar, sem fração.
        if len(partes) > 2:
            return _apenas_digitos("".join(partes)), ""
        inteiro, fracao = partes
        # "1.234" é ambíguo: 3 dígitos após um único separador com inteiro
        # não vazio é o padrão clássico de milhar (pt-BR usa vírgula decimal;
        # en-US usa ponto decimal mas raramente com exatamente 3 casas em
        # extratos). Tratamos como milhar SOMENTE para o ponto — vírgula com
        # qualquer coisa é decimal no Brasil.
        if sep == "." and len(fracao) == 3 and inteiro != "":
            return _apenas_digitos(inteiro + fracao), ""
        return _apenas_digitos(inteiro), _so_digitos(fracao)
    return _apenas_digitos(limpo), ""


def _apenas_digitos(texto: str) -> Optional[str]:
    digitos = "".join(ch for ch in texto if ch.isdigit())
    return digitos if digitos or texto == "" else None


def _so_digitos(texto: str) -> str:
    return "".join(ch for ch in texto if ch.isdigit())


def formata_centavos(centavos: Optional[int], moeda: str = "BRL") -> str:
    """``123456`` → ``"1.234,56"`` (pt-BR); prefixa ``R$`` só quando BRL."""
    if centavos is None:
        return "—"
    casas = casas_da_moeda(moeda)
    negativo = centavos < 0
    valor = abs(centavos)
    if casas:
        inteiro, fracao = divmod(valor, 10**casas)
        corpo = f"{inteiro:,}".replace(",", ".") + "," + str(fracao).zfill(casas)
    else:
        corpo = f"{valor:,}".replace(",", ".")
    prefixo = "R$ " if moeda.upper() == "BRL" else f"{moeda.upper()} "
    return f"{'-' if negativo else ''}{prefixo}{corpo}"
