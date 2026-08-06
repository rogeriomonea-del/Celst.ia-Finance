"""Cenários de retorno da carteira (aba ``Cenarios``).

Reproduz, em Python puro, as três blocos de fórmulas da aba:

* **retorno por cenário** — ``SUMPRODUCT(peso_da_classe; retorno_da_classe)``
  para cada um dos cenários (Péssimo, Ruim, Otimista, Muito Favorável);
* **retorno esperado ponderado** — ``SUMPRODUCT(retorno_do_cenário;
  probabilidade_do_cenário)``, válido só quando as probabilidades somam 100%;
* **indicadores da carteira** — DY, P/L e P/VP médios ponderados pelo valor de
  mercado dentro de cada classe, além das médias de IPCA e Selic projetados.

Módulo **puro**: entram dataclasses de ``engine.models``, saem dataclasses de
``engine.models``. Sem I/O, sem rede, sem openpyxl.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from ..models import (
    PortfolioResult,
    ScenarioClass,
    ScenarioResult,
    WorkbookInputs,
)

# Nomes usados pela planilha quando a aba Cenarios não traz a matriz.
CENARIOS_PADRAO: Tuple[str, ...] = ("Péssimo", "Ruim", "Otimista", "Muito Favorável")

# As probabilidades precisam somar 100% para o retorno esperado fazer sentido.
TOLERANCIA_PROBABILIDADES: float = 1e-6

AVISO_CLASSES_DERIVADAS = (
    "Aba Cenarios sem matriz de classes: classes derivadas da carteira com "
    "retornos zerados — edite os retornos por cenário para obter projeções."
)


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: object) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _opcional(valor: object) -> Optional[float]:
    """Mantém ``None`` como ausência de dado, mas descarta NaN/infinito e lixo."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None
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


_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")


def _chave(texto: object) -> str:
    """Chave de comparação: minúsculas, sem acento, sem pontuação e sem espaço duplo."""
    limpo = _texto(texto).lower().translate(_ACENTOS)
    limpo = "".join(ch if ch.isalnum() else " " for ch in limpo)
    return " ".join(limpo.split())


def _eh_acoes(classe: str) -> bool:
    """Reconhece a classe 'Ações' (e variantes escritas à mão)."""
    chave = _chave(classe)
    return chave.startswith("acoes") or chave.startswith("acao") or chave in ("stocks", "equities")


def _eh_fii(classe: str) -> bool:
    """Reconhece a classe 'FIIs' (inclusive 'Fundos Imobiliários')."""
    chave = _chave(classe)
    return chave.startswith("fii") or "imobiliari" in chave


# --------------------------------------------------------------------------
# Classes da matriz de cenários
# --------------------------------------------------------------------------


def _classes_da_planilha(inputs: Optional[WorkbookInputs]) -> List[ScenarioClass]:
    """Classes lidas da aba, já higienizadas (peso e retornos sempre numéricos)."""
    brutas = getattr(inputs, "scenario_classes", None) or ()
    classes: List[ScenarioClass] = []
    for bruta in brutas:
        if bruta is None:
            continue
        nome = _texto(getattr(bruta, "name", ""))
        if not nome:
            continue
        retornos_brutos = getattr(bruta, "returns", None) or {}
        retornos: Dict[str, float] = {}
        if isinstance(retornos_brutos, dict):
            for cenario, retorno in retornos_brutos.items():
                rotulo = _texto(cenario)
                if rotulo:
                    retornos[rotulo] = _numero(retorno)
        classes.append(
            ScenarioClass(name=nome, weight=_numero(getattr(bruta, "weight", 0.0)), returns=retornos)
        )
    return classes


def _classes_derivadas(
    portfolio: Optional[PortfolioResult], nomes: Sequence[str]
) -> List[ScenarioClass]:
    """Monta a matriz a partir de ``portfolio.by_class``, com retornos zerados.

    Serve para planilhas sem a aba Cenarios: o usuário vê a carteira já
    distribuída e só precisa preencher os retornos de cada cenário.
    """
    resumos = getattr(portfolio, "by_class", None) or ()
    retornos = {nome: 0.0 for nome in nomes}
    derivadas: List[ScenarioClass] = []
    for resumo in resumos:
        if resumo is None:
            continue
        nome = _texto(getattr(resumo, "asset_class", "")) or "Outros"
        derivadas.append(
            ScenarioClass(name=nome, weight=_numero(getattr(resumo, "weight", 0.0)), returns=dict(retornos))
        )
    return derivadas


def _avisar(inputs: Optional[WorkbookInputs], mensagem: str) -> None:
    """Registra o aviso em ``inputs.warnings`` — canal único de avisos do motor.

    Falha em silêncio se o campo não existir ou não aceitar ``append``: um aviso
    jamais pode derrubar o cálculo.
    """
    avisos = getattr(inputs, "warnings", None)
    if not isinstance(avisos, list) or mensagem in avisos:
        return
    avisos.append(mensagem)


# --------------------------------------------------------------------------
# Retorno por cenário e retorno esperado
# --------------------------------------------------------------------------


def _nomes_cenarios(
    classes: Sequence[ScenarioClass], probabilidades: Dict[str, float]
) -> List[str]:
    """Cenários na ordem em que aparecem: primeiro a matriz, depois as probabilidades."""
    nomes: List[str] = []
    vistos: set[str] = set()
    for classe in classes:
        for cenario in getattr(classe, "returns", None) or {}:
            rotulo = _texto(cenario)
            chave = _chave(rotulo)
            if rotulo and chave not in vistos:
                vistos.add(chave)
                nomes.append(rotulo)
    for cenario in probabilidades:
        chave = _chave(cenario)
        if cenario and chave not in vistos:
            vistos.add(chave)
            nomes.append(cenario)
    return nomes


def _retornos_por_cenario(
    classes: Sequence[ScenarioClass], nomes: Sequence[str]
) -> Dict[str, float]:
    """``SUMPRODUCT(peso; retorno)`` por cenário — classes sem o cenário entram com 0."""
    retornos: Dict[str, float] = {}
    for nome in nomes:
        chave_cenario = _chave(nome)
        total = 0.0
        for classe in classes:
            mapa = getattr(classe, "returns", None) or {}
            retorno = 0.0
            for cenario, valor in mapa.items():
                if _chave(cenario) == chave_cenario:
                    retorno = _numero(valor)
                    break
            total += _numero(getattr(classe, "weight", 0.0)) * retorno
        retornos[nome] = total
    return retornos


def _probabilidades(inputs: Optional[WorkbookInputs]) -> Dict[str, float]:
    """Probabilidades da planilha, higienizadas e sem rótulos vazios."""
    brutas = getattr(inputs, "scenario_probabilities", None) or {}
    if not isinstance(brutas, dict):
        return {}
    limpas: Dict[str, float] = {}
    for cenario, probabilidade in brutas.items():
        rotulo = _texto(cenario)
        if rotulo:
            limpas[rotulo] = _numero(probabilidade)
    return limpas


def _retorno_esperado(
    retornos_por_cenario: Dict[str, float], probabilidades: Dict[str, float]
) -> float:
    """Média dos retornos ponderada pelas probabilidades.

    Só faz sentido com uma distribuição completa: se as probabilidades não
    somarem 1 (tolerância :data:`TOLERANCIA_PROBABILIDADES`) — porque faltam
    cenários, porque o usuário digitou 10 em vez de 10% ou porque a linha nem
    existe — devolvemos 0.0 em vez de um número enganoso.
    """
    if not probabilidades:
        return 0.0
    soma = sum(probabilidades.values())
    if abs(soma - 1.0) > TOLERANCIA_PROBABILIDADES:
        return 0.0
    indice = {_chave(nome): retorno for nome, retorno in retornos_por_cenario.items()}
    return sum(
        indice.get(_chave(cenario), 0.0) * probabilidade
        for cenario, probabilidade in probabilidades.items()
    )


# --------------------------------------------------------------------------
# Projeção de inflação (médias simples, como o AVERAGE do Excel)
# --------------------------------------------------------------------------


def _previsao_normalizada(inputs: Optional[WorkbookInputs]) -> List[Dict[str, Any]]:
    """Cópia defensiva de ``inflation_forecast`` (o resultado não deve aliasar a entrada)."""
    bruta = getattr(inputs, "inflation_forecast", None) or ()
    return [dict(item) for item in bruta if isinstance(item, dict)]


def _media_campo(previsao: Sequence[Dict[str, Any]], *chaves: str) -> float:
    """Média simples do primeiro campo encontrado, ignorando linhas sem o dado.

    É o comportamento do ``MÉDIA()``/``AVERAGE()`` do Excel: célula vazia não
    entra no denominador. Lista vazia devolve 0.0.
    """
    valores: List[float] = []
    for linha in previsao:
        for chave in chaves:
            if chave in linha:
                numero = _opcional(linha.get(chave))
                if numero is not None:
                    valores.append(numero)
                break
    return _divide(sum(valores), float(len(valores)))


# --------------------------------------------------------------------------
# Indicadores ponderados da carteira
# --------------------------------------------------------------------------


class _Ativo(NamedTuple):
    """Linha da carteira reduzida ao que os indicadores ponderados precisam."""

    classe: str
    valor: float
    dy: Optional[float]
    pl: Optional[float]
    pvp: Optional[float]


def _ativos(inputs: Optional[WorkbookInputs], portfolio: Optional[PortfolioResult]) -> List[_Ativo]:
    """Posições na ordem da planilha; cai para o ``PortfolioResult`` se não houver.

    A ordem importa por um detalhe de ponto flutuante: somando linha a linha como
    o ``SUMPRODUCT`` faz, os indicadores batem bit a bit com os valores
    congelados na aba Cenarios. ``portfolio.positions`` vem ordenado por valor de
    mercado, o que só muda o último dígito — por isso serve de reserva, para
    quando as posições chegam apenas no resultado da carteira.
    """
    entradas: Iterable[Any] = getattr(inputs, "positions", None) or ()
    ativos = [_ativo_de(item, calcular_valor=True) for item in entradas if item is not None]
    if ativos:
        return ativos
    origem: Iterable[Any] = getattr(portfolio, "positions", None) or ()
    return [_ativo_de(item, calcular_valor=False) for item in origem if item is not None]


def _ativo_de(item: Any, calcular_valor: bool) -> _Ativo:
    if calcular_valor:
        valor = _numero(getattr(item, "quantity", 0.0)) * _numero(getattr(item, "price", 0.0))
    else:
        valor = _numero(getattr(item, "market_value", 0.0))
    return _Ativo(
        classe=_texto(getattr(item, "asset_class", "")),
        valor=valor,
        dy=_opcional(getattr(item, "dividend_yield", None)),
        pl=_opcional(getattr(item, "price_earnings", None)),
        pvp=_opcional(getattr(item, "price_to_book", None)),
    )


def _media_ponderada(
    ativos: Sequence[_Ativo], pertence: Callable[[str], bool], indicador: str
) -> float:
    """Média do indicador ponderada pelo valor de mercado **dentro da classe**.

    Reproduz literalmente o ``SUMPRODUCT(...)/SUMIFS(...)`` da planilha: o
    numerador é ``Σ(valor_de_mercado × indicador)`` e o denominador é
    ``Σ(valor_de_mercado)`` de **toda** a classe. Ou seja, um ativo sem o
    indicador (célula vazia, ``None``) entra com **0 no numerador mas continua
    contando no denominador** — o que puxa a média da classe para baixo. Não é
    um descuido: é o comportamento do Excel que precisamos reproduzir para bater
    com os números congelados da aba Cenarios.

    Classe inexistente ou com valor de mercado total zero devolve 0.0.
    """
    numerador = 0.0
    denominador = 0.0
    for ativo in ativos:
        if not pertence(ativo.classe):
            continue
        denominador += ativo.valor
        valor_indicador = getattr(ativo, indicador, None)
        if valor_indicador is not None:
            numerador += ativo.valor * valor_indicador
    return _divide(numerador, denominador)


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------


def compute_scenarios(inputs: WorkbookInputs, portfolio: PortfolioResult) -> ScenarioResult:
    """Calcula a aba Cenarios: retorno por cenário, retorno esperado e indicadores.

    * ``returns_by_scenario`` — ``Σ(peso × retorno)`` por cenário.
    * ``expected_return`` — ``Σ(retorno_do_cenário × probabilidade)``; 0.0 se as
      probabilidades não somarem 1 (tolerância ``1e-6``).
    * ``inflation_average``/``selic_average`` — média simples da projeção.
    * ``weighted_dy_stocks``/``weighted_dy_fii``/``weighted_pe_stocks``/
      ``weighted_pb_fii`` — média ponderada pelo valor de mercado dentro da
      classe. Como no ``SUMPRODUCT(...)/SUMIFS(...)`` da planilha, ativos sem o
      indicador entram com **0 no numerador** mas **contam no denominador**.

    Sem a matriz de classes (aba ausente ou vazia), as classes são derivadas de
    ``portfolio.by_class`` com retornos zerados e um aviso vai para
    ``inputs.warnings``. Nenhuma entrada — lista vazia, ``None``, denominador
    zero — levanta exceção: o resultado degenera para zeros.
    """
    probabilidades = _probabilidades(inputs)

    classes = _classes_da_planilha(inputs)
    if not classes:
        nomes_base = _nomes_cenarios((), probabilidades) or list(CENARIOS_PADRAO)
        classes = _classes_derivadas(portfolio, nomes_base)
        _avisar(inputs, AVISO_CLASSES_DERIVADAS)

    nomes = _nomes_cenarios(classes, probabilidades)
    retornos_por_cenario = _retornos_por_cenario(classes, nomes)

    previsao = _previsao_normalizada(inputs)
    ativos = _ativos(inputs, portfolio)

    return ScenarioResult(
        classes=classes,
        returns_by_scenario=retornos_por_cenario,
        probabilities=probabilidades,
        expected_return=_retorno_esperado(retornos_por_cenario, probabilidades),
        inflation_average=_media_campo(previsao, "ipca", "inflacao", "inflation"),
        selic_average=_media_campo(previsao, "selic", "juros", "rate"),
        weighted_dy_stocks=_media_ponderada(ativos, _eh_acoes, "dy"),
        weighted_dy_fii=_media_ponderada(ativos, _eh_fii, "dy"),
        weighted_pe_stocks=_media_ponderada(ativos, _eh_acoes, "pl"),
        weighted_pb_fii=_media_ponderada(ativos, _eh_fii, "pvp"),
        inflation_forecast=previsao,
    )
