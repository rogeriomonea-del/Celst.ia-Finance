"""Orquestração do motor: arquivo enviado → :class:`~engine.models.Analysis`.

É aqui que as peças se encaixam. Cada módulo de ``engine.calc`` é puro e não
conhece os outros; o pipeline carrega a pasta de trabalho, extrai as entradas
e chama os cálculos **na ordem em que um depende do outro**:

.. code-block:: text

    workbook.load -> extract.extract_all
        -> carteira (posicao)
        -> proventos          (usa a carteira para o yield)
        -> cenários           (usa a carteira para os pesos e indicadores)
        -> rebalanceamento    (usa a carteira e os alvos)
        -> simulação e projeção
        -> fluxo de caixa     (usa a taxa anual)
        -> consolidado
        -> gráficos           (usa tudo o que deu certo)

Duas regras governam este arquivo:

**Nada derruba a análise.** Cada bloco roda dentro de ``try/except``: se um
módulo falhar, o campo correspondente vira ``None``, um aviso em pt-BR entra em
``Analysis.warnings`` e o resto continua. Uma planilha genérica — só uma tabela
de posições, sem Config, sem Cenarios, sem Fluxo — precisa produzir um
resultado útil, e produz: carteira, gráficos e KPIs do que existe.

**A taxa anual tem uma fonte só.** Simulação e fluxo de caixa usam o retorno
esperado dos cenários (``SUMPRODUCT`` das probabilidades) quando ele existe;
sem a aba Cenarios, caem no retorno derivado dos parâmetros do Config
(:func:`engine.calc.projecao.annual_returns`), escolhendo o regime pelo cenário
selecionado. Assim os dois blocos nunca divergem entre si.

Este módulo faz I/O (lê o arquivo) e pode falar com a rede quando
``refresh_quotes=True``; tudo em ``engine.calc`` continua puro.
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from . import extract, workbook
from .calc.cenarios import compute_scenarios
from .calc.charts import build_charts
from .calc.consolidado import compute_consolidated
from .calc.fluxo import compute_cashflow
from .calc.posicao import compute_portfolio
from .calc.projecao import annual_returns, compute_projection
from .calc.proventos import compute_proventos
from .calc.rebalance import compute_rebalance
from .calc.simulador import compute_simulation
from .models import (
    Analysis,
    Assumptions,
    CashflowResult,
    ChartSeries,
    ConsolidatedResult,
    PortfolioResult,
    ProjectionResult,
    ProventosResult,
    RebalanceResult,
    ScenarioResult,
    SimulationResult,
    WorkbookInputs,
)
from .workbook import Source, WorkbookData

#: Versão do motor, publicada em ``Analysis.engine["version"]``.
VERSAO: str = "1.0.0"

#: Rótulo do cenário que seleciona o regime de renda (Config B3).
CENARIO_RENDA: str = "renda"

_MESES_PT: Tuple[str, ...] = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")

T = TypeVar("T")


# --------------------------------------------------------------------------
# Auxiliares tolerantes (nunca levantam exceção)
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
    """Converte para ``float`` tratando ``None``, texto inválido, NaN e infinito."""
    if valor is None or isinstance(valor, bool):
        return 0.0
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if numero != numero or numero in (float("inf"), float("-inf")):
        return 0.0
    return numero


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _chave(valor: Any) -> str:
    """Minúsculas, sem acento e sem pontuação — só para comparar rótulos."""
    limpo = _texto(valor).lower().translate(_ACENTOS)
    return " ".join("".join(ch if ch.isalnum() else " " for ch in limpo).split())


def _lista(valor: Any) -> List[Any]:
    """Lista sem ``None``; ``[]`` para qualquer coisa que não seja sequência."""
    if not isinstance(valor, (list, tuple)):
        return []
    return [item for item in valor if item is not None]


def _mes_pt(momento: Any) -> str:
    """``date(2026, 7, 1)`` → ``'jul/2026'``; qualquer outra coisa vira texto."""
    if isinstance(momento, date):
        return f"{_MESES_PT[momento.month - 1]}/{momento.year:04d}"
    return _texto(momento)


def _valor_pt(valor: float) -> str:
    """Número no formato pt-BR, com duas casas: ``1234.5`` → ``"1.234,50"``."""
    numero = _numero(valor)
    corpo = f"{abs(numero):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{'-' if numero < 0 else ''}{corpo}"


def _moeda(valor: float) -> str:
    """Formata em real brasileiro: ``1234.5`` → ``"R$ 1.234,50"``."""
    return f"R$ {_valor_pt(valor)}"


def _percentual(valor: float, casas: int = 2) -> str:
    """Fração para percentual pt-BR: ``0.112`` → ``"11,20%"``."""
    return f"{_numero(valor) * 100:.{casas}f}".replace(".", ",") + "%"


def _plural(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


# --------------------------------------------------------------------------
# Execução protegida de cada bloco de cálculo
# --------------------------------------------------------------------------


def _bloco(rotulo: str, avisos: List[str], calcular: Callable[[], T]) -> Optional[T]:
    """Roda um bloco de cálculo; erro vira ``None`` + aviso em pt-BR.

    Nenhum defeito de um módulo pode custar a análise inteira: a planilha é do
    usuário e o resto dos números continua válido. O tipo da exceção entra na
    mensagem porque é o que permite achar o bloco culpado no log.
    """
    try:
        return calcular()
    except Exception as erro:  # noqa: BLE001 - isolar o bloco é o objetivo
        avisos.append(
            f"Não foi possível calcular {rotulo} ({type(erro).__name__}). "
            "O restante da análise continua."
        )
        return None


# --------------------------------------------------------------------------
# Cotações (único ponto do pipeline que fala com a rede)
# --------------------------------------------------------------------------


def _atualizar_cotacoes(
    inputs: WorkbookInputs, token: Optional[str], avisos: List[str]
) -> None:
    """Atualiza os preços das posições pela brapi, respeitando ``price_locked``.

    Roda **antes** de qualquer cálculo, para que valor de mercado, pesos,
    cenários e rebalanceamento saiam todos do preço novo. Renda Fixa privada,
    Tesouro Direto e o resíduo marcado "não atualizar" mantêm o preço da
    planilha — quem decide isso é ``engine.quotes``.
    """
    from . import quotes  # import tardio: só quem pede cotação carrega urllib

    posicoes = _lista(getattr(inputs, "positions", None))
    tickers = quotes.tickers_atualizaveis(posicoes)
    if not tickers:
        avisos.append(
            "Nenhum ticker elegível para cotação: os preços da planilha foram mantidos."
        )
        return

    cotacoes = quotes.fetch_quotes(tickers, token=token)
    if not cotacoes:
        avisos.append(
            "Não foi possível atualizar as cotações (brapi.dev indisponível ou sem token): "
            "os preços da planilha foram mantidos."
        )
        return

    novas, avisos_cotacao = quotes.apply_quotes(posicoes, cotacoes)
    atualizadas = sum(
        1
        for antiga, nova in zip(posicoes, novas)
        if _numero(getattr(antiga, "price", 0.0)) != _numero(getattr(nova, "price", 0.0))
    )
    inputs.positions = novas
    avisos.append(
        f"Cotações atualizadas pela brapi.dev: {atualizadas} de {len(tickers)} "
        f"{'ticker' if len(tickers) == 1 else 'tickers'} elegíveis."
    )
    avisos.extend(avisos_cotacao)


# --------------------------------------------------------------------------
# Taxa anual usada por simulação e fluxo
# --------------------------------------------------------------------------


def _taxa_anual(
    scenarios: Optional[ScenarioResult], assumptions: Assumptions, tem_config: bool
) -> Tuple[float, str]:
    """``(taxa, origem)`` — retorno esperado dos cenários ou o do Config.

    A aba Cenarios só produz retorno esperado quando as probabilidades somam
    100%; fora disso caímos nos parâmetros do Config, pelo regime que o usuário
    selecionou (Renda × Valorização).

    Sem **nenhuma** das duas abas a taxa é 0,0, e não os valores padrão de
    :class:`~engine.models.Assumptions`: projetar 16,5% ao ano em cima de uma
    planilha que só listava posições seria inventar uma previsão que o usuário
    nunca informou.
    """
    esperado = _numero(getattr(scenarios, "expected_return", 0.0)) if scenarios else 0.0
    if esperado:
        return esperado, "cenarios"
    if not tem_config:
        return 0.0, "indisponivel"
    try:
        renda, valorizacao = annual_returns(assumptions)
    except Exception:  # noqa: BLE001 - taxa é insumo, não pode derrubar nada
        return 0.0, "indisponivel"
    prefere_renda = _chave(getattr(assumptions, "scenario", "")) == CENARIO_RENDA
    escolhida = renda if prefere_renda else valorizacao
    return _numero(escolhida), "assumptions"


def _ha_plano(assumptions: Assumptions) -> bool:
    """Há capital ou aporte para simular? Sem isso a série seria zeros."""
    return bool(
        _numero(getattr(assumptions, "initial_capital", 0.0))
        or _numero(getattr(assumptions, "monthly_contribution", 0.0))
        or _numero(getattr(assumptions, "monthly_withdrawal", 0.0))
    )


# --------------------------------------------------------------------------
# Rótulo do período analisado
# --------------------------------------------------------------------------


def _rotulo_periodo(
    inputs: WorkbookInputs,
    proventos: Optional[ProventosResult],
    cashflow: Optional[CashflowResult],
) -> str:
    """Descreve o período coberto pela análise, em pt-BR.

    A planilha não traz esse rótulo pronto, então ele é deduzido: primeiro do
    intervalo dos proventos (é o "período" a que o KPI se refere), depois do
    fluxo de caixa. O rótulo diz de onde veio para não confundir quem lê.
    """
    declarado = _texto(getattr(inputs, "period_label", ""))
    if declarado:
        return declarado

    inicio = getattr(proventos, "period_start", None) if proventos else None
    fim = getattr(proventos, "period_end", None) if proventos else None
    if isinstance(inicio, date) and isinstance(fim, date):
        if _mes_pt(inicio) == _mes_pt(fim):
            return f"Proventos de {_mes_pt(inicio)}"
        return f"Proventos de {_mes_pt(inicio)} a {_mes_pt(fim)}"

    meses = _lista(getattr(cashflow, "monthly", None)) if cashflow else []
    if meses:
        return f"Fluxo de {_mes_pt(meses[0].month)} a {_mes_pt(meses[-1].month)}"
    return ""


# --------------------------------------------------------------------------
# KPIs da faixa de indicadores
# --------------------------------------------------------------------------


def _kpi(
    identificador: str, rotulo: str, valor: Any, formato: str, dica: str
) -> Dict[str, Any]:
    return {
        "id": identificador,
        "label": rotulo,
        "value": valor,
        "format": formato,
        "hint": dica,
    }


def _kpis(
    portfolio: Optional[PortfolioResult],
    proventos: Optional[ProventosResult],
    scenarios: Optional[ScenarioResult],
    projection: Optional[ProjectionResult],
    consolidated: Optional[ConsolidatedResult],
    taxa: float,
    origem_taxa: str,
) -> List[Dict[str, Any]]:
    """Monta a faixa de indicadores, pulando o que a planilha não tem.

    Cada cartão traz o valor **cru** (o front-end formata pelo campo
    ``format``) e uma dica que explica de onde o número saiu.
    """
    cartoes: List[Dict[str, Any]] = []

    if portfolio is not None:
        classes = _lista(getattr(portfolio, "by_class", None))
        cartoes.append(
            _kpi(
                "patrimonio-b3",
                "Patrimônio na B3",
                _numero(getattr(portfolio, "total_value", 0.0)),
                "currency",
                f"{_plural(int(_numero(getattr(portfolio, 'positions_count', 0))), 'posição', 'posições')} "
                f"em {_plural(len(classes), 'classe', 'classes')}.",
            )
        )

    if consolidated is not None and _lista(getattr(consolidated, "accounts", None)):
        contas = len(_lista(getattr(consolidated, "accounts", None)))
        exterior = _numero(getattr(consolidated, "foreign_total_usd", 0.0))
        dica = f"{_plural(contas, 'conta', 'contas')} somadas (R$ e exterior já convertido)."
        if exterior:
            dica += f" Exterior: US$ {_valor_pt(exterior)}."
        cartoes.append(
            _kpi(
                "patrimonio-total",
                "Patrimônio total",
                _numero(getattr(consolidated, "total", 0.0)),
                "currency",
                dica,
            )
        )

    if proventos is not None and _lista(getattr(proventos, "by_ticker", None)):
        inicio = getattr(proventos, "period_start", None)
        fim = getattr(proventos, "period_end", None)
        lancamentos = sum(
            int(_numero(item.get("count"))) for item in _lista(getattr(proventos, "by_ticker", None))
        )
        dica = _plural(lancamentos, "lançamento", "lançamentos")
        if isinstance(inicio, date) and isinstance(fim, date):
            dica += f" entre {_mes_pt(inicio)} e {_mes_pt(fim)}"
        media = _numero(getattr(proventos, "monthly_average", 0.0))
        dica += f". Média mensal de {_moeda(media)}." if media else "."
        cartoes.append(
            _kpi(
                "proventos-periodo",
                "Proventos no período",
                _numero(getattr(proventos, "total", 0.0)),
                "currency",
                dica,
            )
        )

    if portfolio is not None:
        cartoes.append(
            _kpi(
                "ativos",
                "Ativos na carteira",
                int(_numero(getattr(portfolio, "positions_count", 0))),
                "number",
                "Linhas da posição com quantidade e preço.",
            )
        )

        posicoes = _lista(getattr(portfolio, "positions", None))
        com_variacao = sum(1 for p in posicoes if getattr(p, "var_12m", None) is not None)
        if com_variacao:
            cartoes.append(
                _kpi(
                    "var-12m",
                    "Variação 12m ponderada",
                    _numero(getattr(portfolio, "weighted_var_12m", 0.0)),
                    "percent",
                    f"Média ponderada pelo valor de mercado de {_plural(com_variacao, 'ativo', 'ativos')} "
                    "das classes listadas (ações, FIIs, ETFs, BDRs e fundos).",
                )
            )

    if taxa:
        if origem_taxa == "cenarios":
            cenarios_contados = len(getattr(scenarios, "probabilities", None) or {})
            dica = (
                f"Retorno dos {cenarios_contados} cenários ponderado pelas probabilidades da aba Cenarios."
                if cenarios_contados
                else "Retorno ponderado pelas probabilidades da aba Cenarios."
            )
        else:
            dica = "Derivado dos parâmetros do Config (a aba Cenarios não trouxe probabilidades)."
        cartoes.append(_kpi("retorno-esperado", "Retorno esperado a.a.", taxa, "percent", dica))

    if projection is not None and _lista(getattr(projection, "series", None)):
        serie = _lista(getattr(projection, "series", None))
        ultimo = serie[-1]
        cenario = _texto(getattr(projection, "scenario", "")) or "Valorização"
        anual = _numero(
            getattr(projection, "annual_return_income", 0.0)
            if _chave(cenario) == CENARIO_RENDA
            else getattr(projection, "annual_return_growth", 0.0)
        )
        cartoes.append(
            _kpi(
                "patrimonio-projetado",
                "Patrimônio projetado",
                _numero(getattr(projection, "final_value", 0.0)),
                "currency",
                f"Cenário {cenario} ({_percentual(anual)} a.a.) em {_mes_pt(getattr(ultimo, 'month', None))}, "
                f"após {_plural(len(serie), 'mês', 'meses')} de aportes.",
            )
        )

    return cartoes


# --------------------------------------------------------------------------
# Metadados do motor
# --------------------------------------------------------------------------


def _bytes_da_fonte(source: Source) -> Optional[bytes]:
    """Conteúdo bruto do arquivo, para contar as fórmulas — ``None`` se não der.

    É chamado **depois** da leitura: o ``BytesIO`` pode ter sido fechado por
    quem leu, e o caminho pode ter sumido do disco. Nos dois casos ficamos sem
    o metadado, nunca com uma exceção.
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, io.BytesIO):
        try:
            return source.getvalue()
        except ValueError:  # buffer já fechado
            return None
    if isinstance(source, str):
        try:
            with open(source, "rb") as arquivo:
                return arquivo.read()
        except OSError:
            return None
    return None


def contar_formulas(source: Source, file_name: str) -> int:
    """Quantas fórmulas o arquivo tinha — ou seja, quantas o motor substituiu.

    Um .xlsx/.xlsm é um ZIP de XML: cada fórmula é um elemento ``<f>`` dentro
    da planilha. Contar as ocorrências custa ~15 ms no arquivo real (28.432
    fórmulas) e é um número verdadeiro, não uma estimativa. CSV, arquivo
    ilegível ou qualquer erro devolvem 0.
    """
    if file_name.lower().endswith((".csv", ".tsv", ".txt")):
        return 0
    dados = _bytes_da_fonte(source)
    if not dados or dados[:2] != b"PK":
        return 0
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as pacote:
            for nome in pacote.namelist():
                if nome.startswith("xl/worksheets/") and nome.endswith(".xml"):
                    bruto = pacote.read(nome)
                    total += bruto.count(b"<f>") + bruto.count(b"<f ")
    except Exception:  # noqa: BLE001 - metadado nunca derruba a análise
        return 0
    return total


def _sem_repetir(avisos: Sequence[str]) -> List[str]:
    """Remove avisos repetidos preservando a ordem de chegada."""
    vistos: set[str] = set()
    limpos: List[str] = []
    for aviso in avisos:
        texto = _texto(aviso)
        if texto and texto not in vistos:
            vistos.add(texto)
            limpos.append(texto)
    return limpos


# --------------------------------------------------------------------------
# Entradas públicas
# --------------------------------------------------------------------------


def analyze(
    source: Source,
    file_name: str = "planilha.xlsx",
    *,
    quotes_token: Optional[str] = None,
    refresh_quotes: bool = False,
) -> Analysis:
    """Analisa uma pasta de trabalho inteira e devolve o resultado consolidado.

    ``source`` é um caminho, os bytes do arquivo ou um ``BytesIO``;
    ``file_name`` decide o leitor (Excel × CSV) e volta no resultado.

    Com ``refresh_quotes=True`` os preços das posições são atualizados pela
    brapi.dev antes de qualquer cálculo (``quotes_token`` ou a variável de
    ambiente ``BRAPI_TOKEN``); posições travadas mantêm o preço da planilha e
    cada ticker sem cotação vira um aviso.

    Só a leitura do arquivo pode levantar exceção (arquivo corrompido, formato
    não suportado) — daí para frente todo bloco é isolado, e o que falhar vira
    ``None`` mais um aviso em pt-BR.
    """
    inicio = time.perf_counter()
    avisos: List[str] = []

    book: WorkbookData = workbook.load(source, file_name)
    inputs: WorkbookInputs = extract.extract_all(book)

    if refresh_quotes:
        _bloco("as cotações", avisos, lambda: _atualizar_cotacoes(inputs, quotes_token, avisos))

    assumptions: Assumptions = getattr(inputs, "assumptions", None) or Assumptions()

    portfolio: Optional[PortfolioResult] = _bloco(
        "a carteira", avisos, lambda: compute_portfolio(inputs)
    )
    proventos: Optional[ProventosResult] = _bloco(
        "os proventos", avisos, lambda: compute_proventos(inputs, portfolio)
    )
    scenarios: Optional[ScenarioResult] = _bloco(
        "os cenários", avisos, lambda: compute_scenarios(inputs, portfolio)
    )
    rebalance: Optional[RebalanceResult] = _bloco(
        "o rebalanceamento", avisos, lambda: compute_rebalance(inputs, portfolio)
    )

    # Simulação e fluxo compartilham a mesma taxa: cenários primeiro, Config depois.
    tem_config = book.sheet("Config") is not None
    taxa, origem_taxa = _taxa_anual(scenarios, assumptions, tem_config)

    simulation: Optional[SimulationResult] = None
    projection: Optional[ProjectionResult] = None
    if _ha_plano(assumptions):
        simulation = _bloco("a simulação", avisos, lambda: compute_simulation(assumptions, taxa))
        projection = _bloco("a projeção", avisos, lambda: compute_projection(assumptions))
    else:
        avisos.append(
            "Sem capital inicial e sem aporte mensal no Config: simulação e projeção não foram "
            "geradas (a série seria uma linha de zeros)."
        )
    cashflow: Optional[CashflowResult] = _bloco(
        "o fluxo de caixa", avisos, lambda: compute_cashflow(inputs, taxa)
    )
    consolidated: Optional[ConsolidatedResult] = _bloco(
        "o patrimônio consolidado", avisos, lambda: compute_consolidated(inputs)
    )

    charts: List[ChartSeries] = _bloco(
        "os gráficos",
        avisos,
        lambda: build_charts(
            portfolio=portfolio,
            proventos=proventos,
            rebalance=rebalance,
            simulation=simulation,
            projection=projection,
            scenarios=scenarios,
            cashflow=cashflow,
            consolidated=consolidated,
        ),
    ) or []

    kpis: List[Dict[str, Any]] = _bloco(
        "os indicadores",
        avisos,
        lambda: _kpis(
            portfolio, proventos, scenarios, projection, consolidated, taxa, origem_taxa
        ),
    ) or []

    periodo = _bloco(
        "o período analisado", avisos, lambda: _rotulo_periodo(inputs, proventos, cashflow)
    ) or ""

    # Os módulos de cálculo também escrevem em ``inputs.warnings`` (a aba
    # Cenarios avisa quando deriva as classes da carteira, por exemplo), então
    # a leitura fica para o fim.
    todos_avisos = _sem_repetir(
        list(getattr(book, "warnings", None) or [])
        + list(getattr(inputs, "warnings", None) or [])
        + avisos
    )

    return Analysis(
        file_name=file_name,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_kind=_texto(getattr(inputs, "source_kind", "")) or "generico",
        period_label=periodo,
        portfolio=portfolio,
        rebalance=rebalance,
        simulation=simulation,
        projection=projection,
        scenarios=scenarios,
        cashflow=cashflow,
        proventos=proventos,
        consolidated=consolidated,
        charts=charts,
        assumptions=assumptions,
        kpis=kpis,
        warnings=todos_avisos,
        engine={
            "version": VERSAO,
            "elapsed_ms": round((time.perf_counter() - inicio) * 1000.0, 1),
            "sheets_read": len(getattr(book, "sheets", None) or {}),
            "positions": len(_lista(getattr(inputs, "positions", None))),
            "formulas_replaced": contar_formulas(source, file_name),
        },
    )


def analyze_bytes(data: bytes, file_name: str, **kw: Any) -> Analysis:
    """Mesma análise a partir do conteúdo em memória (é o caminho da API).

    O arquivo enviado pelo usuário nunca toca o disco: os bytes vão direto para
    :func:`analyze`, que aceita ``bytes`` como fonte.
    """
    return analyze(data, file_name, **kw)
