"""Extração das abas para os tipos de ``engine.models``.

A leitura é guiada pelos cabeçalhos (não por endereços fixos), então
pequenas mudanças de layout na planilha não quebram o motor.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .models import (
    Assumptions,
    CashflowEntry,
    ConsolidatedAccount,
    ForeignPosition,
    Position,
    Provento,
    RebalanceTarget,
    ScenarioClass,
    WorkbookInputs,
)
from .workbook import SheetGrid, WorkbookData, as_number, normalize

SCENARIO_NAMES = ("Péssimo", "Ruim", "Otimista", "Muito Favorável")

_STOP_LABELS = {
    "total",
    "total geral",
    "total do periodo",
    "resumo por classe",
    "subtotal",
}


def _is_stop(label: str) -> bool:
    norm = normalize(label)
    return norm in _STOP_LABELS or norm.startswith("total ") or norm.startswith("subtotal")


def _find_header(grid: SheetGrid, *required: str, limit: int = 30) -> Optional[int]:
    """Linha cujo conjunto de cabeçalhos contém todos os rótulos pedidos."""
    wanted = [normalize(r) for r in required]
    for row in range(1, min(grid.max_row, limit) + 1):
        headers = grid.header_map(row)
        if all(any(w == h or w in h for h in headers) for w in wanted):
            return row
    return None


def _col(headers: Dict[str, int], *candidates: str) -> Optional[int]:
    for candidate in candidates:
        norm = normalize(candidate)
        if norm in headers:
            return headers[norm]
    for candidate in candidates:
        norm = normalize(candidate)
        for header, col in headers.items():
            if norm and (header.startswith(norm) or norm in header):
                return col
    return None


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def extract_assumptions(book: WorkbookData) -> Assumptions:
    grid = book.sheet("Config")
    if grid is None:
        return Assumptions()

    labels: Dict[str, float] = {}
    texts: Dict[str, str] = {}
    dates: Dict[str, date] = {}
    for row in range(1, grid.max_row + 1):
        label = normalize(grid.text(row, 1))
        if not label:
            continue
        raw = grid.value(row, 2)
        if raw is None:
            continue
        parsed_date = grid.date(row, 2)
        if parsed_date is not None and not isinstance(raw, (int, float)):
            dates[label] = parsed_date
        if isinstance(raw, str):
            texts[label] = raw.strip()
        labels[label] = as_number(raw)

    def num(default: float, *keys: str) -> float:
        for key in keys:
            norm = normalize(key)
            for label, value in labels.items():
                if label.startswith(norm) or norm in label:
                    return value
        return default

    def text(default: str, *keys: str) -> str:
        for key in keys:
            norm = normalize(key)
            for label, value in texts.items():
                if label.startswith(norm) or norm in label:
                    return value
        return default

    def when(*keys: str) -> Optional[date]:
        for key in keys:
            norm = normalize(key)
            for label, value in dates.items():
                if label.startswith(norm) or norm in label:
                    return value
        return None

    return Assumptions(
        scenario=text("Valorizacao", "cenario selecionado"),
        initial_capital=num(0.0, "capital inicial"),
        monthly_contribution=num(0.0, "aporte mensal"),
        monthly_withdrawal=num(0.0, "retirada mensal planejada"),
        inflation_annual=num(0.0414, "inflacao esperada"),
        selic_annual=num(0.1475, "retorno nominal selic"),
        real_rate_annual=num(0.05, "retorno real ipca"),
        real_rate_delta=num(-0.01, "taxa real no cenario de corte", "delta taxa real"),
        duration_years=num(24.0, "duration estimada"),
        dy_fii=num(0.095, "dy alvo fiis"),
        dy_stocks=num(0.07, "dy alvo acoes"),
        price_return_stocks=num(0.06, "retorno preco acoes"),
        price_return_fii=num(0.03, "retorno preco fiis"),
        foreign_return=num(0.04, "retorno exterior"),
        order_tolerance=num(500.0, "tolerancia p ignorar ordens", "tolerancia"),
        start_date=when("data inicial projecoes"),
        end_date=when("data final projecoes"),
        target_monthly_real=num(0.01, "meta % real mes", "meta retorno real liquido mes"),
        tax_and_costs=num(0.10, "taxa efetiva impostos"),
        monthly_income_target=num(0.0, "renda mensal alvo"),
        small_cap_premium=num(0.02, "premio small caps"),
        dy_small_caps=num(0.02, "dy small caps"),
        dy_foreign=num(0.015, "dy exterior"),
    )


# --------------------------------------------------------------------------
# Posicao_B3
# --------------------------------------------------------------------------


def extract_positions(book: WorkbookData) -> List[Position]:
    grid = book.sheet("Posicao_B3", "Posicao B3", "Posição", "Carteira", "Posicoes")
    if grid is None:
        return []
    header_row = _find_header(grid, "ticker", "qtde") or _find_header(grid, "ticker", "quantidade")
    if header_row is None:
        return []
    headers = grid.header_map(header_row)

    c_class = _col(headers, "classe", "categoria", "tipo")
    c_ticker = _col(headers, "ticker", "codigo", "ativo")
    c_name = _col(headers, "ativo", "nome", "produto", "descricao")
    c_qty = _col(headers, "qtde", "quantidade", "qtd")
    c_price = _col(headers, "preco r$", "preco", "preco atual", "cotacao")
    c_var = _col(headers, "var 12m", "variacao 12m", "var12m")
    c_note = _col(headers, "obs", "observacao")
    c_pe = _col(headers, "p l", "p/l", "pl")
    c_pb = _col(headers, "p vp", "p/vp", "pvp")
    c_dy = _col(headers, "dy 12m", "dy", "dividend yield")
    c_signal = _col(headers, "sinal mercado tec", "sinal mercado", "sinal")
    c_read = _col(headers, "leitura fundamentalista", "leitura")

    if c_ticker is None or c_qty is None:
        return []
    if c_name == c_ticker:
        c_name = None

    positions: List[Position] = []
    for row in range(header_row + 1, grid.max_row + 1):
        label = grid.text(row, c_class) if c_class else ""
        ticker = grid.text(row, c_ticker)
        if _is_stop(label) or _is_stop(ticker):
            break
        if not ticker and not label:
            continue
        quantity = grid.number(row, c_qty)
        price = grid.number(row, c_price) if c_price else 0.0
        if not ticker and quantity == 0:
            continue
        positions.append(
            Position(
                asset_class=label or "Outros",
                ticker=ticker,
                name=grid.text(row, c_name) if c_name else ticker,
                quantity=quantity,
                price=price,
                var_12m=grid.number(row, c_var, default=float("nan")) if c_var else None,
                note=grid.text(row, c_note) if c_note else "",
                price_earnings=_optional(grid, row, c_pe),
                price_to_book=_optional(grid, row, c_pb),
                dividend_yield=_optional(grid, row, c_dy),
                technical_signal=grid.text(row, c_signal) if c_signal else "",
                fundamental_read=grid.text(row, c_read) if c_read else "",
                row=row,
            )
        )
    return [_clean_var(p) for p in positions]


def _optional(grid: SheetGrid, row: int, col: Optional[int]) -> Optional[float]:
    if col is None:
        return None
    raw = grid.value(row, col)
    if raw is None or raw == "":
        return None
    return as_number(raw)


def _clean_var(position: Position) -> Position:
    if position.var_12m is not None and position.var_12m != position.var_12m:
        return Position(**{**position.__dict__, "var_12m": None})
    return position


# --------------------------------------------------------------------------
# Proventos
# --------------------------------------------------------------------------


def extract_proventos(book: WorkbookData) -> List[Provento]:
    grid = book.sheet("Proventos", "Dividendos", "Rendimentos")
    if grid is None:
        return []
    header_row = _find_header(grid, "data", "valor") or _find_header(grid, "data", "tipo")
    if header_row is None:
        return []
    headers = grid.header_map(header_row)
    c_date = _col(headers, "data")
    c_kind = _col(headers, "tipo", "evento")
    c_ticker = _col(headers, "ticker codigo", "ticker", "codigo", "ativo")
    c_product = _col(headers, "produto", "descricao", "nome")
    c_value = _col(headers, "valor r$", "valor", "montante")
    if c_date is None or c_value is None:
        return []

    proventos: List[Provento] = []
    for row in range(header_row + 1, grid.max_row + 1):
        first = grid.text(row, 2) or grid.text(row, 1)
        if _is_stop(first):
            break
        amount = grid.number(row, c_value)
        ticker = grid.text(row, c_ticker) if c_ticker else ""
        if amount == 0 and not ticker:
            continue
        proventos.append(
            Provento(
                paid_at=grid.date(row, c_date),
                kind=grid.text(row, c_kind) if c_kind else "",
                ticker=ticker,
                product=grid.text(row, c_product) if c_product else "",
                amount=amount,
            )
        )
    return proventos


# --------------------------------------------------------------------------
# Fluxo de Caixa
# --------------------------------------------------------------------------


def extract_cashflow(book: WorkbookData) -> List[CashflowEntry]:
    grid = book.sheet("Fluxo de Caixa", "FluxoCaixa", "Fluxo")
    if grid is None:
        return []
    header_row = _find_header(grid, "data", "entradas")
    if header_row is None:
        return []
    headers = grid.header_map(header_row)
    c_date = _col(headers, "data") or 3
    # "Data_aux" pode vir antes de "Data": prefira a coluna cujo cabeçalho é exato.
    if normalize(grid.text(header_row, c_date)) != "data":
        for col, label in ((c, normalize(grid.text(header_row, c))) for c in range(1, grid.max_col + 1)):
            if label == "data":
                c_date = col
                break
    c_in = _col(headers, "entradas")
    c_invest = _col(headers, "aporte investimentos")
    c_reserve = _col(headers, "aporte reserva")
    c_card = _col(headers, "fatura cartao")
    c_expense = _col(headers, "gastos")

    entries: List[CashflowEntry] = []
    for row in range(header_row + 1, grid.max_row + 1):
        day = grid.date(row, c_date)
        if day is None:
            continue
        entries.append(
            CashflowEntry(
                day=day,
                inflow=grid.number(row, c_in) if c_in else 0.0,
                invest_contribution=grid.number(row, c_invest) if c_invest else 0.0,
                reserve_contribution=grid.number(row, c_reserve) if c_reserve else 0.0,
                card_bill=grid.number(row, c_card) if c_card else 0.0,
                expenses=grid.number(row, c_expense) if c_expense else 0.0,
            )
        )
    return entries


# --------------------------------------------------------------------------
# Posição Consolidada + Dolarizado
# --------------------------------------------------------------------------


def extract_consolidated(book: WorkbookData) -> List[ConsolidatedAccount]:
    grid = book.sheet("Posicao Consolidada", "Consolidada", "Consolidado")
    if grid is None:
        return []
    header_row = _find_header(grid, "categoria", "liquidez")
    if header_row is None:
        return []
    headers = grid.header_map(header_row)
    c_name = _col(headers, "instituicao classe", "instituicao", "conta")
    c_cat = _col(headers, "categoria")
    c_liq = _col(headers, "liquidez")
    c_value = _col(headers, "valor r$", "valor")
    if c_value is None:
        return []

    accounts: List[ConsolidatedAccount] = []
    for row in range(header_row + 1, grid.max_row + 1):
        name = grid.text(row, c_name) if c_name else ""
        if _is_stop(name):
            break
        category = grid.text(row, c_cat) if c_cat else ""
        if not name and not category:
            continue
        accounts.append(
            ConsolidatedAccount(
                institution=name,
                category=category or "Outros",
                liquidity=grid.text(row, c_liq) if c_liq else "",
                value=grid.number(row, c_value),
            )
        )
    return accounts


def extract_foreign(book: WorkbookData) -> List[ForeignPosition]:
    grid = book.sheet("Dolarizado", "Internacional", "Exterior")
    if grid is None:
        return []
    header_row = _find_header(grid, "corretora", "ticker")
    if header_row is None:
        return []
    headers = grid.header_map(header_row)
    c_broker = _col(headers, "corretora", "instituicao")
    c_ticker = _col(headers, "ticker", "codigo")
    c_desc = _col(headers, "descricao", "nome")
    c_qty = _col(headers, "qtde", "quantidade")
    c_price = _col(headers, "preco us$", "preco")
    c_cost = _col(headers, "custo us$", "custo")

    out: List[ForeignPosition] = []
    for row in range(header_row + 1, grid.max_row + 1):
        broker = grid.text(row, c_broker) if c_broker else ""
        ticker = grid.text(row, c_ticker) if c_ticker else ""
        if _is_stop(broker):
            continue  # subtotais são recalculados pelo motor
        if not ticker:
            continue
        out.append(
            ForeignPosition(
                broker=broker or "Exterior",
                ticker=ticker,
                description=grid.text(row, c_desc) if c_desc else ticker,
                quantity=grid.number(row, c_qty) if c_qty else 0.0,
                price_usd=grid.number(row, c_price) if c_price else 0.0,
                cost_usd=grid.number(row, c_cost) if c_cost else 0.0,
            )
        )
    return out


def extract_consolidated_params(book: WorkbookData) -> Dict[str, float]:
    grid = book.sheet("Posicao Consolidada", "Consolidada")
    params = {"usd_brl": 0.0, "opportunity_reserve": 0.0, "cash_usd": 0.0}
    if grid is None:
        return params
    for row in range(1, min(grid.max_row, 40) + 1):
        label = normalize(grid.text(row, 2))
        if not label:
            continue
        value = grid.number(row, 3)
        if "cambio usd" in label or label.startswith("cambio"):
            params["usd_brl"] = value
        elif "reserva de oportunidade" in label and value:
            params["opportunity_reserve"] = params["opportunity_reserve"] or value
        elif "dolar em especie" in label:
            params["cash_usd"] = value
    return params


# --------------------------------------------------------------------------
# Cenários e Rebalanceamento
# --------------------------------------------------------------------------


def extract_scenarios(book: WorkbookData):
    """(classes, probabilidades, projeção de inflação)."""
    grid = book.sheet("Cenarios", "Cenários", "Scenarios")
    if grid is None:
        return [], {}, []

    header_row = _find_header(grid, "classe", "pessimo", limit=40)
    classes: List[ScenarioClass] = []
    probabilities: Dict[str, float] = {}
    if header_row is not None:
        headers = grid.header_map(header_row)
        c_name = _col(headers, "classe")
        c_weight = _col(headers, "peso na carteira b3", "peso")
        scenario_cols = {}
        for label in SCENARIO_NAMES:
            col = _col(headers, label)
            if col:
                scenario_cols[label] = col

        # A matriz de classes termina na linha "Total"; o que vem depois são
        # linhas-resumo (retorno por cenário, probabilidades, retorno esperado).
        for row in range(header_row + 1, grid.max_row + 1):
            name = grid.text(row, c_name) if c_name else ""
            norm = normalize(name)
            if not name:
                continue
            if norm == "total" or norm.startswith("retorno da carteira") or norm.startswith(
                "probabilidade"
            ):
                break
            weight = grid.number(row, c_weight) if c_weight else 0.0
            returns = {label: grid.number(row, col) for label, col in scenario_cols.items()}
            if weight or any(returns.values()):
                classes.append(ScenarioClass(name=name, weight=weight, returns=returns))

        prob_row = grid.find_row("Probabilidade do cenário", limit=40) or grid.find_row(
            "Probabilidade do cenario", limit=40
        )
        if prob_row:
            probabilities = {
                label: grid.number(prob_row, col) for label, col in scenario_cols.items()
            }

    inflation: List[Dict[str, float]] = []
    infl_header = _find_header(grid, "ano", "ipca proj a a", limit=20) or _find_header(
        grid, "ano", "ipca", limit=20
    )
    if infl_header is not None:
        headers = grid.header_map(infl_header)
        c_year = _col(headers, "ano")
        c_ipca = _col(headers, "ipca proj a a", "ipca")
        c_selic = _col(headers, "selic fim de ano", "selic")
        for row in range(infl_header + 1, min(infl_header + 12, grid.max_row) + 1):
            year_raw = grid.value(row, c_year) if c_year else None
            if year_raw is None:
                continue
            year = as_number(year_raw)
            if year < 1900 or year > 2200:
                continue  # linha "Média 2026-2029"
            inflation.append(
                {
                    "year": int(year),
                    "ipca": grid.number(row, c_ipca) if c_ipca else 0.0,
                    "selic": grid.number(row, c_selic) if c_selic else 0.0,
                }
            )
    return classes, probabilities, inflation


def extract_rebalance_targets(book: WorkbookData) -> List[RebalanceTarget]:
    grid = book.sheet("Rebalanceamento", "Rebalance")
    if grid is None:
        return []
    header_row = _find_header(grid, "classe", "% alvo", limit=40) or _find_header(
        grid, "classe", "alvo", limit=40
    )
    if header_row is None:
        return []
    headers = grid.header_map(header_row)
    c_name = _col(headers, "classe")
    c_target = _col(headers, "% alvo edite", "% alvo", "alvo")
    if c_name is None or c_target is None:
        return []

    targets: List[RebalanceTarget] = []
    for row in range(header_row + 1, grid.max_row + 1):
        name = grid.text(row, c_name)
        norm = normalize(name)
        if not name:
            continue
        if norm.startswith("soma dos alvos") or _is_stop(name):
            break
        target = grid.number(row, c_target)
        if target:
            targets.append(RebalanceTarget(asset_class=name, target=target))
    return targets


# --------------------------------------------------------------------------
# Entrada única
# --------------------------------------------------------------------------


def detect_kind(book: WorkbookData) -> str:
    titles = {normalize(t) for t in book.titles}
    known = {"posicao b3", "proventos", "config", "rebalanceamento", "simulador"}
    matches = len(titles & known)
    if matches >= 3:
        return "carteira_b3"
    if any("posic" in t or "carteira" in t for t in titles):
        return "carteira_generica"
    return "generico"


def extract_all(book: WorkbookData) -> WorkbookInputs:
    warnings: List[str] = list(book.warnings)
    kind = detect_kind(book)

    positions = extract_positions(book)
    if not positions:
        positions = _generic_positions(book, warnings)

    scenario_classes, probabilities, inflation = extract_scenarios(book)
    params = extract_consolidated_params(book)

    inputs = WorkbookInputs(
        positions=positions,
        proventos=extract_proventos(book),
        cashflow=extract_cashflow(book),
        accounts=extract_consolidated(book),
        foreign=extract_foreign(book),
        scenario_classes=scenario_classes,
        scenario_probabilities=probabilities,
        inflation_forecast=inflation,
        rebalance_targets=extract_rebalance_targets(book),
        assumptions=extract_assumptions(book),
        usd_brl=params["usd_brl"],
        opportunity_reserve=params["opportunity_reserve"],
        source_kind=kind,
        warnings=warnings,
    )

    if not inputs.positions:
        warnings.append("Nenhuma posição reconhecida: verifique se há colunas Ticker e Qtde.")
    if inputs.foreign and not inputs.usd_brl:
        warnings.append("Câmbio USD/BRL não encontrado — valores em R$ do exterior ficam zerados.")
    return inputs


def _generic_positions(book: WorkbookData, warnings: List[str]) -> List[Position]:
    """Fallback: procura em qualquer aba uma tabela com ticker + quantidade."""
    for title, grid in book.sheets.items():
        header_row = _find_header(grid, "ticker", "quantidade") or _find_header(
            grid, "codigo", "quantidade"
        )
        if header_row is None:
            continue
        headers = grid.header_map(header_row)
        c_ticker = _col(headers, "ticker", "codigo", "ativo", "papel")
        c_qty = _col(headers, "quantidade", "qtde", "qtd")
        c_price = _col(headers, "preco", "preco medio", "cotacao", "ultimo")
        c_class = _col(headers, "classe", "categoria", "tipo")
        c_name = _col(headers, "nome", "produto", "descricao", "especificacao")
        if c_ticker is None or c_qty is None:
            continue
        out: List[Position] = []
        for row in range(header_row + 1, grid.max_row + 1):
            ticker = grid.text(row, c_ticker)
            if not ticker or _is_stop(ticker):
                continue
            out.append(
                Position(
                    asset_class=(grid.text(row, c_class) if c_class else "") or "Outros",
                    ticker=ticker,
                    name=(grid.text(row, c_name) if c_name else "") or ticker,
                    quantity=grid.number(row, c_qty),
                    price=grid.number(row, c_price) if c_price else 0.0,
                    row=row,
                )
            )
        if out:
            warnings.append(f"Layout não reconhecido: posições lidas da aba '{title}'.")
            return out
    return []
