"""Linha de comando do motor: analisa uma planilha e imprime o resumo.

.. code-block:: console

    $ python3 -m engine.cli carteira.xlsm
    $ python3 -m engine.cli carteira.xlsm --json analise.json
    $ python3 -m engine.cli carteira.xlsm --quotes --token SEU_TOKEN_BRAPI

Serve para conferir o motor contra o arquivo real sem subir o site: o mesmo
:func:`engine.pipeline.analyze` que a API chama, com a saída em pt-BR no
terminal (indicadores, maiores posições e o que o rebalanceamento manda fazer).

``--json`` grava a análise completa — é exatamente o corpo que ``POST
/api/planilha`` devolve, útil para diferenciar duas execuções ou alimentar o
front-end sem servidor.

Código de saída: 0 quando a análise sai, 1 quando o arquivo não pôde ser lido.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import warnings
from typing import Any, Dict, List, Optional, Sequence

from .models import Analysis, to_dict

#: Quantas posições a tabela do terminal mostra.
TOP_POSICOES: int = 10

#: Largura da régua que separa as seções.
LARGURA: int = 78


# --------------------------------------------------------------------------
# Formatação pt-BR
# --------------------------------------------------------------------------


def _numero(valor: Any) -> float:
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


def formatar(valor: Any, formato: str = "currency") -> str:
    """Formata um valor conforme o ``format`` que o motor sugeriu para ele."""
    if isinstance(valor, str):
        return valor
    numero = _numero(valor)
    if formato == "percent":
        return f"{numero * 100:.2f}".replace(".", ",") + "%"
    if formato == "number":
        return f"{numero:,.0f}".replace(",", ".")
    corpo = f"{abs(numero):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"R$ {'-' if numero < 0 else ''}{corpo}"


def _regua(titulo: str = "") -> str:
    if not titulo:
        return "-" * LARGURA
    return f"{titulo} " + "-" * max(0, LARGURA - len(titulo) - 1)


def _lista(valor: Any) -> List[Any]:
    if not isinstance(valor, (list, tuple)):
        return []
    return [item for item in valor if item is not None]


# --------------------------------------------------------------------------
# Seções do relatório
# --------------------------------------------------------------------------


def _cabecalho(analise: Analysis) -> List[str]:
    motor: Dict[str, Any] = getattr(analise, "engine", None) or {}
    linhas = [
        "=" * LARGURA,
        f"Análise de {_texto(analise.file_name) or 'planilha'}",
        "=" * LARGURA,
        f"Tipo de planilha : {_texto(analise.source_kind) or 'genérico'}",
        f"Gerado em        : {_texto(analise.generated_at)}",
    ]
    if _texto(analise.period_label):
        linhas.append(f"Período          : {_texto(analise.period_label)}")
    linhas.append(
        "Motor            : v{versao} · {abas} abas · {posicoes} posições · "
        "{formulas} fórmulas substituídas · {ms} ms".format(
            versao=_texto(motor.get("version")) or "?",
            abas=int(_numero(motor.get("sheets_read"))),
            posicoes=int(_numero(motor.get("positions"))),
            formulas=f"{int(_numero(motor.get('formulas_replaced'))):,}".replace(",", "."),
            ms=formatar(motor.get("elapsed_ms"), "number"),
        )
    )
    return linhas


def _secao_kpis(analise: Analysis) -> List[str]:
    kpis = _lista(getattr(analise, "kpis", None))
    if not kpis:
        return []
    largura_rotulo = max(len(_texto(kpi.get("label"))) for kpi in kpis)
    linhas = ["", _regua("INDICADORES")]
    for kpi in kpis:
        rotulo = _texto(kpi.get("label")).ljust(largura_rotulo)
        valor = formatar(kpi.get("value"), _texto(kpi.get("format")) or "currency")
        linhas.append(f"  {rotulo}  {valor:>18}")
        dica = _texto(kpi.get("hint"))
        if dica:
            recuo = " " * (largura_rotulo - 8)
            linhas.extend(
                textwrap.wrap(dica, width=LARGURA, initial_indent=recuo, subsequent_indent=recuo)
            )
    return linhas


def _secao_posicoes(analise: Analysis) -> List[str]:
    portfolio = getattr(analise, "portfolio", None)
    posicoes = _lista(getattr(portfolio, "positions", None))
    if not posicoes:
        return []
    linhas = [
        "",
        _regua(f"TOP {TOP_POSICOES} POSIÇÕES"),
        f"  {'Ticker':<12}{'Classe':<20}{'Valor':>18}{'Peso':>9}{'12m':>9}",
    ]
    for posicao in posicoes[:TOP_POSICOES]:
        variacao = getattr(posicao, "var_12m", None)
        # Renda Fixa e Tesouro não têm ticker: o nome do papel identifica a linha.
        identificacao = _texto(getattr(posicao, "ticker", "")) or _texto(
            getattr(posicao, "name", "")
        )
        linhas.append(
            "  {ticker:<12}{classe:<20}{valor:>18}{peso:>9}{var:>9}".format(
                ticker=identificacao[:11] or "-",
                classe=_texto(getattr(posicao, "asset_class", ""))[:19],
                valor=formatar(getattr(posicao, "market_value", 0.0)),
                peso=formatar(getattr(posicao, "portfolio_weight", 0.0), "percent"),
                var="-" if variacao is None else formatar(variacao, "percent"),
            )
        )
    total = _numero(getattr(portfolio, "total_value", 0.0))
    mostradas = sum(_numero(getattr(p, "market_value", 0.0)) for p in posicoes[:TOP_POSICOES])
    fatia = mostradas / total if total else 0.0
    linhas.append(
        f"  {len(posicoes)} posições no total; as {min(TOP_POSICOES, len(posicoes))} acima somam "
        f"{formatar(mostradas)} ({formatar(fatia, 'percent')} da carteira)."
    )
    return linhas


def _secao_rebalance(analise: Analysis) -> List[str]:
    rebalance = getattr(analise, "rebalance", None)
    linhas_rebalance = _lista(getattr(rebalance, "rows", None))
    if not linhas_rebalance:
        return []

    banda = _numero(getattr(rebalance, "band_relative", 0.0))
    linhas = [
        "",
        _regua("REBALANCEAMENTO"),
        f"  Bandas de ±{formatar(banda, 'percent')} relativos sobre o alvo de cada classe.",
        f"  {'Classe':<20}{'Atual':>9}{'Alvo':>9}{'Ação':>10}{'Ajuste':>18}",
    ]
    for linha in linhas_rebalance:
        acao = _texto(getattr(linha, "action", ""))
        ajuste = _numero(getattr(linha, "amount", 0.0))
        linhas.append(
            "  {classe:<20}{atual:>9}{alvo:>9}{acao:>10}{ajuste:>18}".format(
                classe=_texto(getattr(linha, "asset_class", ""))[:19],
                atual=formatar(getattr(linha, "current_weight", 0.0), "percent"),
                alvo=formatar(getattr(linha, "target_weight", 0.0), "percent"),
                acao=acao or "-",
                ajuste="-" if acao == "OK" else formatar(ajuste),
            )
        )

    plano = _lista(getattr(rebalance, "contribution_plan", None))
    if plano:
        linhas.append("  Para onde mandar o próximo aporte (sem vender nada):")
        for item in plano:
            linhas.append(
                "    - {classe}: {valor} ({fatia})".format(
                    classe=_texto(item.get("asset_class")),
                    valor=formatar(item.get("amount")),
                    fatia=formatar(item.get("share"), "percent"),
                )
            )
    return linhas


def _secao_avisos(analise: Analysis) -> List[str]:
    avisos = [_texto(a) for a in _lista(getattr(analise, "warnings", None)) if _texto(a)]
    if not avisos:
        return []
    linhas = ["", _regua("AVISOS")]
    for aviso in avisos:
        linhas.extend(
            textwrap.wrap(aviso, width=LARGURA, initial_indent="  * ", subsequent_indent="    ")
        )
    return linhas


def formatar_resumo(analise: Analysis) -> str:
    """Monta o relatório inteiro em texto — testável sem tocar em ``stdout``."""
    linhas: List[str] = []
    linhas.extend(_cabecalho(analise))
    linhas.extend(_secao_kpis(analise))
    linhas.extend(_secao_posicoes(analise))
    linhas.extend(_secao_rebalance(analise))
    linhas.extend(_secao_avisos(analise))
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Entrada de linha de comando
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine.cli",
        description="Analisa uma planilha (.xlsm/.xlsx/.csv) com o motor do Celestia.",
    )
    parser.add_argument("arquivo", help="caminho da planilha a analisar")
    parser.add_argument(
        "--json",
        dest="saida_json",
        metavar="SAIDA.json",
        default=None,
        help="grava a análise completa em JSON no caminho informado ('-' para a saída padrão)",
    )
    parser.add_argument(
        "--quotes",
        action="store_true",
        help="atualiza os preços pela brapi.dev antes de calcular (respeita os travados)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="token da brapi.dev (sem ele vale a variável de ambiente BRAPI_TOKEN)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Executa a CLI. Devolve o código de saída do processo."""
    argumentos = _parser().parse_args(list(argv) if argv is not None else None)

    # Import tardio: ``--help`` não precisa carregar openpyxl.
    from .pipeline import analyze

    try:
        with warnings.catch_warnings():
            # openpyxl reclama de extensões que não entende (formatação
            # condicional, por exemplo). É ruído: o motor lê só os valores.
            warnings.simplefilter("ignore", UserWarning)
            analise = analyze(
                argumentos.arquivo,
                argumentos.arquivo.replace("\\", "/").split("/")[-1],
                quotes_token=argumentos.token,
                refresh_quotes=bool(argumentos.quotes),
            )
    except FileNotFoundError:
        print(f"Arquivo não encontrado: {argumentos.arquivo}", file=sys.stderr)
        return 1
    except Exception as erro:  # noqa: BLE001 - a CLI reporta, não estoura
        print(
            f"Não foi possível analisar a planilha ({type(erro).__name__}): {erro}",
            file=sys.stderr,
        )
        return 1

    print(formatar_resumo(analise))

    if argumentos.saida_json:
        corpo = json.dumps(to_dict(analise), ensure_ascii=False, indent=2, allow_nan=False)
        if argumentos.saida_json == "-":
            print(corpo)
        else:
            try:
                with open(argumentos.saida_json, "w", encoding="utf-8") as arquivo:
                    arquivo.write(corpo + "\n")
            except OSError as erro:
                print(f"Não foi possível gravar o JSON: {erro}", file=sys.stderr)
                return 1
            print(f"\nJSON gravado em {argumentos.saida_json}")
    return 0


if __name__ == "__main__":  # pragma: no cover - ponto de entrada
    raise SystemExit(main())
