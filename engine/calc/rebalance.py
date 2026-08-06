"""Rebalanceamento por bandas de tolerância (aba ``Rebalanceamento``).

Reproduz, em Python puro, o monitor por classe da planilha:

* valor e peso atual de cada classe sobre o total da carteira;
* alvo editado pelo usuário e banda de tolerância ``alvo × (1 ∓ band_relative)``;
* ação ``COMPRAR`` / ``VENDER`` / ``OK`` conforme o peso rompa a banda;
* desvio (``peso − alvo``) e o valor em R$ do ajuste;
* plano de aporte (*cash-flow rebalancing*): para onde mandar o próximo aporte
  mensal sem vender nada;
* as diretrizes de rebalanceamento que a planilha traz em texto.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.

Divergência conhecida contra o .xlsm
------------------------------------
A planilha marca **ETFs** como ``VENDER`` (peso 3,2463% contra alvo de 3%, banda
2,4%–3,6%), o que a própria regra dela contradiz. O motivo é um defeito da
planilha: a célula ``Rebalanceamento!G21`` ("Banda máx." dos ETFs) está **vazia**
— todas as outras linhas têm ``=E*1.2``. A fórmula da coluna AÇÃO então avalia
``D21 > G21`` como ``0,032463 > 0`` (o Excel lê célula vazia como zero) e devolve
``VENDER`` sempre. O motor calcula a banda máxima de todas as classes e devolve
``OK`` para os ETFs, que é o resultado correto da regra documentada.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import (
    PortfolioResult,
    RebalanceResult,
    RebalanceRow,
    WorkbookInputs,
)

# Ações possíveis na coluna AÇÃO da planilha.
ACAO_COMPRAR = "COMPRAR"
ACAO_VENDER = "VENDER"
ACAO_OK = "OK"

# Banda de Daryanani: 20% *relativos* sobre o alvo (parente da regra 5/25).
BANDA_PADRAO = 0.20

# A planilha exige que a soma dos alvos dê 100%; meio ponto percentual de folga
# absorve o arredondamento de quem digita "0,325" em vez de "0,32".
TOLERANCIA_SOMA_ALVOS = 0.005

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")
_ESPACOS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: object) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    # Booleano numa coluna numérica é lixo de planilha: ``True`` viraria 1,0
    # (= 100% de alvo), então tratamos como ausência de valor.
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    # NaN não é igual a si mesmo; infinito envenenaria todas as somas.
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _texto(valor: object) -> str:
    return "" if valor is None else str(valor).strip()


def _divide(numerador: float, denominador: float) -> float:
    """Divisão que devolve 0.0 em vez de estourar quando o denominador é zero."""
    if not denominador:
        return 0.0
    resultado = numerador / denominador
    if resultado != resultado or resultado in (float("inf"), float("-inf")):
        return 0.0
    return resultado


def _chave_classe(classe: object) -> str:
    """Chave de comparação de classe: minúsculas, sem acento e sem pontuação.

    ``"Ações"``, ``"ACOES"`` e ``"ações "`` casam entre si — a aba
    Rebalanceamento é digitada à mão e nem sempre bate com a Posicao_B3.
    """
    limpo = _texto(classe).lower().translate(_ACENTOS)
    return _ESPACOS.sub(" ", _NAO_ALFANUM.sub(" ", limpo)).strip()


def _banda(band_relative: Optional[float]) -> float:
    """Largura da banda, sempre positiva; ``None`` cai no padrão de 20%."""
    if band_relative is None:
        return BANDA_PADRAO
    return abs(_numero(band_relative))


# --------------------------------------------------------------------------
# Formatação pt-BR das notas
# --------------------------------------------------------------------------


def _moeda(valor: float) -> str:
    """Formata em real brasileiro: ``1234.5`` → ``"R$ 1.234,50"``."""
    corpo = f"{abs(valor):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"R$ {'-' if valor < 0 else ''}{corpo}"


def _percentual(valor: float, casas: int = 1) -> str:
    """Formata fração como percentual pt-BR: ``0.2`` → ``"20,0%"``."""
    return f"{valor * 100:.{casas}f}".replace(".", ",") + "%"


# --------------------------------------------------------------------------
# Leitura das entradas
# --------------------------------------------------------------------------


def _classes_da_carteira(portfolio: Optional[PortfolioResult]) -> List[Tuple[str, str, float]]:
    """``[(chave, rótulo, valor)]`` na ordem de ``portfolio.by_class``.

    Classes repetidas (rótulos que só diferem em acento ou caixa) são somadas
    numa linha só, mantendo o primeiro rótulo visto.
    """
    ordem: List[str] = []
    rotulos: Dict[str, str] = {}
    valores: Dict[str, float] = {}
    for resumo in (getattr(portfolio, "by_class", None) or ()) if portfolio is not None else ():
        if resumo is None:
            continue
        rotulo = _texto(getattr(resumo, "asset_class", "")) or "Outros"
        chave = _chave_classe(rotulo)
        if chave not in valores:
            ordem.append(chave)
            rotulos[chave] = rotulo
            valores[chave] = 0.0
        valores[chave] += _numero(getattr(resumo, "value", 0.0))
    return [(chave, rotulos[chave], valores[chave]) for chave in ordem]


def _alvos_declarados(inputs: Optional[WorkbookInputs]) -> List[Tuple[str, str, float]]:
    """``[(chave, rótulo, alvo)]`` na ordem da aba Rebalanceamento.

    Linhas repetidas para a mesma classe são somadas — é o que o
    ``SUM(E19:E25)`` da planilha faz com a coluna de alvos.
    """
    alvos = getattr(inputs, "rebalance_targets", None) if inputs is not None else None
    ordem: List[str] = []
    rotulos: Dict[str, str] = {}
    valores: Dict[str, float] = {}
    for alvo in alvos or ():
        if alvo is None:
            continue
        rotulo = _texto(getattr(alvo, "asset_class", ""))
        chave = _chave_classe(rotulo)
        if not chave:
            continue  # alvo sem classe não tem a que se aplicar
        if chave not in valores:
            ordem.append(chave)
            rotulos[chave] = rotulo
            valores[chave] = 0.0
        valores[chave] += _numero(getattr(alvo, "target", 0.0))
    return [(chave, rotulos[chave], valores[chave]) for chave in ordem]


# --------------------------------------------------------------------------
# Monta uma linha do monitor
# --------------------------------------------------------------------------


def _linha(rotulo: str, valor: float, total: float, alvo: float, banda: float) -> RebalanceRow:
    """Uma classe do monitor: peso, banda, ação, desvio e valor do ajuste."""
    peso = _divide(valor, total)
    minimo = alvo * (1.0 - banda)
    maximo = alvo * (1.0 + banda)

    if peso < minimo:
        acao = ACAO_COMPRAR
    elif peso > maximo:
        acao = ACAO_VENDER
    else:
        acao = ACAO_OK

    return RebalanceRow(
        asset_class=rotulo,
        current_value=valor,
        current_weight=peso,
        target_weight=alvo,
        band_min=minimo,
        band_max=maximo,
        action=acao,
        deviation=peso - alvo,
        # Positivo = falta dinheiro na classe (comprar); negativo = sobra (vender).
        amount=(alvo - peso) * total,
    )


# --------------------------------------------------------------------------
# Plano de aporte (cash-flow rebalancing)
# --------------------------------------------------------------------------


def _plano_de_aporte(linhas: Sequence[RebalanceRow], aporte_mensal: float) -> List[Dict[str, Any]]:
    """Divide o próximo aporte entre as classes abaixo do alvo.

    Rebalancear com dinheiro novo custa zero e não gera imposto, então o aporte
    do mês vai só para quem está abaixo do alvo, proporcional ao déficit em R$.
    Sem aporte (ou com retirada) e sem déficit, devolve lista vazia.
    """
    aporte = _numero(aporte_mensal)
    if aporte <= 0:
        return []

    deficits = [(linha, linha.amount) for linha in linhas if linha.amount > 0]
    soma = sum(deficit for _, deficit in deficits)
    if soma <= 0:
        return []

    plano: List[Dict[str, Any]] = []
    for linha, deficit in deficits:
        fatia = _divide(deficit, soma)
        plano.append(
            {
                "asset_class": linha.asset_class,
                "amount": aporte * fatia,
                "share": fatia,
            }
        )
    # ``sorted`` é estável: fatias iguais mantêm a ordem do monitor.
    plano.sort(key=lambda item: item["amount"], reverse=True)
    return plano


# --------------------------------------------------------------------------
# Notas (diretrizes da planilha + avisos do ciclo)
# --------------------------------------------------------------------------


def _diretrizes(banda: float) -> List[str]:
    """As cinco diretrizes que a planilha traz em texto, na mesma ordem."""
    return [
        f"Método: bandas de tolerância de ±{_percentual(banda)} relativos sobre o alvo de cada "
        "classe, não calendário — o gatilho é o desvio, não a data.",
        "Monitore a cada 2 semanas e aja SÓ quando a banda romper: vigilância frequente, "
        "ação rara.",
        "Rebalanceie com APORTES primeiro (cash-flow rebalancing): direcione o aporte novo do mês "
        "para as classes abaixo do alvo antes de vender qualquer coisa — custo e imposto zero.",
        "Impostos mudam a ordem das vendas: vendas de ações são isentas de IR até R$ 20 mil por "
        "mês, enquanto FIIs pagam 20% sobre o ganho sempre. Se precisar vender, use primeiro a "
        "isenção mensal das ações e espalhe as vendas de FII no tempo.",
        "Os sinais técnicos e a leitura fundamentalista por ativo são INFORMAÇÃO para escolher O "
        "QUE ajustar dentro da classe que a banda mandou mexer — não são gatilho.",
    ]


def _nota_de_tolerancia(linhas: Sequence[RebalanceRow], tolerancia: float) -> List[str]:
    """Avisa a partir de que valor a ordem não compensa e quais podem ser ignoradas."""
    if tolerancia <= 0:
        return []

    notas = [
        f"Ordens abaixo de {_moeda(tolerancia)} podem ser ignoradas: o custo operacional não "
        "compensa o ajuste."
    ]
    ignoraveis = [
        f"{linha.asset_class} ({linha.action.lower()} {_moeda(abs(linha.amount))})"
        for linha in linhas
        if linha.action != ACAO_OK and abs(linha.amount) < tolerancia
    ]
    if ignoraveis:
        notas.append(
            "Fora da banda mas abaixo da tolerância neste ciclo, pode ignorar: "
            + "; ".join(ignoraveis)
            + "."
        )
    return notas


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_rebalance(
    inputs: WorkbookInputs,
    portfolio: PortfolioResult,
    band_relative: float = 0.20,
) -> RebalanceResult:
    """Monta o monitor de rebalanceamento por classe.

    Devolve sempre um :class:`RebalanceResult` válido: sem alvos, sem posições
    ou com campos nulos o resultado vem zerado, nunca por exceção.
    """
    banda = _banda(band_relative)
    tolerancia = 0.0
    aporte_mensal = 0.0
    assumptions = getattr(inputs, "assumptions", None) if inputs is not None else None
    if assumptions is not None:
        tolerancia = abs(_numero(getattr(assumptions, "order_tolerance", 0.0)))
        aporte_mensal = _numero(getattr(assumptions, "monthly_contribution", 0.0))

    classes = _classes_da_carteira(portfolio)
    valores = {chave: valor for chave, _, valor in classes}

    total = _numero(getattr(portfolio, "total_value", 0.0)) if portfolio is not None else 0.0
    # Carteira montada à mão sem ``total_value``: o total das classes serve de
    # denominador para os pesos não saírem todos zerados.
    if not total:
        total = sum(valores.values())

    avisos: List[str] = []
    alvos = _alvos_declarados(inputs)
    havia_alvos = bool(alvos)

    if not alvos:
        # Sem alvo declarado não há desvio possível: o peso atual vira o alvo e
        # tudo sai OK. É um ponto de partida, não uma carteira balanceada.
        alvos = [(chave, rotulo, _divide(valor, total)) for chave, rotulo, valor in classes]
        avisos.append(
            "Nenhum alvo definido na aba Rebalanceamento: o peso atual de cada classe foi usado "
            "como alvo, então nada aparece fora da banda. Edite a coluna '% alvo' para o monitor "
            "valer alguma coisa."
        )

    # O rótulo da carteira manda: é o que aparece nos gráficos e na Posicao_B3.
    # Só cai no rótulo da tabela de alvos quando a classe não tem posição.
    rotulos_carteira = {chave: rotulo for chave, rotulo, _ in classes}

    linhas: List[RebalanceRow] = [
        _linha(rotulos_carteira.get(chave, rotulo), valores.get(chave, 0.0), total, alvo, banda)
        for chave, rotulo, alvo in alvos
    ]

    com_alvo = {chave for chave, _, _ in alvos}
    sem_alvo = [(rotulo, valor) for chave, rotulo, valor in classes if chave not in com_alvo]
    for rotulo, valor in sem_alvo:
        # Classe na carteira que ninguém pôs na tabela de alvos: alvo 0% — a
        # banda inteira colapsa em zero e qualquer valor vira VENDER.
        linhas.append(_linha(rotulo, valor, total, 0.0, banda))

    targets_sum = sum(alvo for _, _, alvo in alvos)

    notas: List[str] = list(avisos)
    # Só cobra os 100% de quem realmente declarou alvos: quando o peso atual foi
    # usado como alvo, a soma é consequência da carteira e o aviso seria ruído.
    if havia_alvos and abs(targets_sum - 1.0) > TOLERANCIA_SOMA_ALVOS:
        notas.append(
            f"A soma dos alvos é {_percentual(targets_sum)} e não 100%: revise a coluna '% alvo' "
            "da aba Rebalanceamento — os desvios e os valores em R$ saem distorcidos."
        )
    if sem_alvo:
        notas.append(
            "Sem alvo definido, entram com alvo 0% e viram VENDER: "
            + ", ".join(rotulo for rotulo, _ in sem_alvo)
            + "."
        )
    notas.extend(_diretrizes(banda))
    notas.extend(_nota_de_tolerancia(linhas, tolerancia))

    return RebalanceResult(
        rows=linhas,
        total_value=total,
        targets_sum=targets_sum,
        band_relative=banda,
        contribution_plan=_plano_de_aporte(linhas, aporte_mensal),
        notes=notas,
    )
