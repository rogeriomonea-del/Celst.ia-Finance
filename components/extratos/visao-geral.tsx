"use client";

/**
 * Aba "Visão geral" de extratos: faixa de KPIs + gráficos do painel.
 *
 * Tudo vem pronto de `GET /extratos/painel` (mesma fonte de filtros da tabela
 * de transações — os totais nunca divergem). Os gráficos chegam com valores em
 * CENTAVOS (`value_format: "centavos"`); como o `<PlanilhaChart />` do módulo
 * /importar não conhece esse formato, o wrapper `GraficoPainelAdaptado`
 * converte os pontos para reais (divisão por 100 SÓ aqui, na exibição) antes
 * de delegar. Drill-down: os itens clicáveis sob cada gráfico aplicam o filtro
 * correspondente e trocam para a aba Transações via `onDrillDown`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarClock,
  Files,
  Link2,
  RefreshCw,
  Scale,
  Tags,
  TrendingDown,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type { ChartSeries, FormatoValor, TipoGrafico } from "@/lib/types-planilha";
import type {
  FiltrosExtratos,
  GraficoPainel,
  KpiPainel,
  Painel,
  PontoGraficoPainel,
} from "@/lib/types-extratos";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PlanilhaChart } from "@/components/importar/planilha-chart";
import { ErroApiExtratos, obterPainel } from "@/components/extratos/api";
import {
  centavosOuTraco,
  formatarCentavos,
  formatarDataISO,
  formatarInteiro,
} from "@/components/extratos/formatar-centavos";

// --------------------------------------------------------------------------
// KPIs
// --------------------------------------------------------------------------

const ICONES_KPI: Record<string, LucideIcon> = {
  entradas: TrendingUp,
  saidas: TrendingDown,
  resultado: Scale,
  "saldo-projetado": Wallet,
  "faturas-a-vencer-30d": CalendarClock,
  "nao-categorizadas": Tags,
  "pendencias-conciliacao": Link2,
  "duplicidades-abertas": Files,
};

type TomKpi = "default" | "profit" | "loss";

function tomDoKpi(kpi: KpiPainel): TomKpi {
  if (kpi.formato === "centavos") {
    const valor = kpi.valor_centavos ?? 0;
    if (kpi.id === "entradas") return valor > 0 ? "profit" : "default";
    if (kpi.id === "saidas") return valor > 0 ? "loss" : "default";
    if (kpi.id === "resultado" || kpi.id === "saldo-projetado") {
      return valor >= 0 ? "profit" : "loss";
    }
  }
  return "default";
}

function valorDoKpi(kpi: KpiPainel): string {
  if (kpi.formato === "centavos") return formatarCentavos(kpi.valor_centavos ?? 0);
  return formatarInteiro(kpi.valor ?? 0);
}

/** CartaoNumero-like do módulo de extratos (com ícone, esmeralda/carmim). */
function CartaoKpiExtrato({ kpi }: { kpi: KpiPainel }) {
  const tom = tomDoKpi(kpi);
  const Icone = ICONES_KPI[kpi.id] ?? Wallet;
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="min-w-0 space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {kpi.label}
          </p>
          <p
            className={cn(
              "break-words text-xl font-semibold leading-tight tabular-nums tracking-tight",
              tom === "profit" && "text-profit",
              tom === "loss" && "text-loss"
            )}
          >
            {valorDoKpi(kpi)}
          </p>
          {kpi.hint && (
            <p className="text-xs text-muted-foreground/80">{kpi.hint}</p>
          )}
        </div>
        <div
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1",
            tom === "profit" && "bg-profit/10 text-profit ring-profit/20",
            tom === "loss" && "bg-loss/10 text-loss ring-loss/20",
            tom === "default" && "bg-white/5 text-muted-foreground ring-white/10"
          )}
        >
          <Icone className="h-4 w-4" aria-hidden />
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Adaptação de gráficos: centavos -> reais para o PlanilhaChart
// --------------------------------------------------------------------------

const KINDS_PLANILHA: readonly TipoGrafico[] = ["donut", "bar", "line", "area"];

function ehKindPlanilha(kind: string): kind is TipoGrafico {
  return (KINDS_PLANILHA as readonly string[]).includes(kind);
}

/**
 * Converte um gráfico do painel para o contrato do `<PlanilhaChart />`:
 * valores em centavos viram reais (chaves plotadas apenas) com formato
 * "currency"; `utilizacao-limite` (percentual ×100) vira fração com formato
 * "percent". `null` devolvido = o gráfico não é desenhável pelo PlanilhaChart.
 */
function adaptarGrafico(grafico: GraficoPainel): ChartSeries | null {
  if (!ehKindPlanilha(grafico.kind)) return null;
  const chaves = grafico.series.map((serie) => serie.key);

  let divisor = 1;
  let formato: FormatoValor = "number";
  if (grafico.id === "utilizacao-limite") {
    divisor = 10000; // percentual ×100 -> fração (1350 -> 0,1350 -> "13,50%")
    formato = "percent";
  } else if (grafico.value_format === "centavos") {
    divisor = 100; // centavos -> reais, SÓ para exibição
    formato = "currency";
  }

  const dados = grafico.data.map((ponto: PontoGraficoPainel) => {
    const convertido: PontoGraficoPainel = { ...ponto };
    for (const chave of chaves) {
      const bruto = ponto[chave];
      if (typeof bruto === "number" && Number.isFinite(bruto)) {
        convertido[chave] = divisor === 1 ? bruto : bruto / divisor;
      }
    }
    return convertido;
  });

  return {
    id: grafico.id,
    title: grafico.title,
    kind: grafico.kind,
    x_key: grafico.x_key,
    series: grafico.series,
    data: dados,
    value_format: formato,
    note: grafico.note,
  };
}

// --------------------------------------------------------------------------
// Drill-down: itens clicáveis por gráfico
// --------------------------------------------------------------------------

interface ItemDrill {
  chave: string;
  rotulo: string;
  filtros: Partial<FiltrosExtratos>;
}

function ultimoDiaDoMes(mes: string): string {
  const [anoTexto, mesTexto] = mes.split("-");
  const ano = Number.parseInt(anoTexto, 10);
  const numero = Number.parseInt(mesTexto, 10);
  if (!Number.isFinite(ano) || !Number.isFinite(numero)) return `${mes}-28`;
  const dias = new Date(ano, numero, 0).getDate();
  return `${mes}-${String(dias).padStart(2, "0")}`;
}

function textoDoPonto(ponto: PontoGraficoPainel, chave: string): string | null {
  const bruto = ponto[chave];
  if (typeof bruto === "string" && bruto.trim() !== "") return bruto;
  return null;
}

function numeroDoPonto(ponto: PontoGraficoPainel, chave: string): number | null {
  const bruto = ponto[chave];
  return typeof bruto === "number" && Number.isFinite(bruto) ? bruto : null;
}

/** Monta os atalhos de drill-down de cada gráfico (barra/fatia → filtro). */
function itensDrill(grafico: GraficoPainel): ItemDrill[] {
  const itens: ItemDrill[] = [];
  grafico.data.forEach((ponto, indice) => {
    const rotulo = textoDoPonto(ponto, grafico.x_key) ?? `item ${indice + 1}`;
    const chave = `${grafico.id}-${indice}`;
    if (grafico.id === "entradas-vs-saidas" || grafico.id === "evolucao-por-ciclo") {
      const mes = textoDoPonto(ponto, "mes");
      if (mes && /^\d{4}-\d{2}$/.test(mes)) {
        itens.push({
          chave,
          rotulo,
          filtros: { periodo_inicio: `${mes}-01`, periodo_fim: ultimoDiaDoMes(mes) },
        });
      }
    } else if (grafico.id === "gastos-por-categoria") {
      const categoriaId = numeroDoPonto(ponto, "categoria_id");
      itens.push({
        chave,
        rotulo,
        filtros:
          categoriaId === null
            ? { status_categoria: "nao_categorizada" }
            : { categoria_id: categoriaId },
      });
    } else if (grafico.id === "gastos-por-cartao" || grafico.id === "utilizacao-limite") {
      const cartaoId = numeroDoPonto(ponto, "cartao_id");
      if (cartaoId !== null) {
        itens.push({ chave, rotulo, filtros: { cartao_id: cartaoId } });
      }
    } else if (grafico.id === "top-contrapartes") {
      itens.push({ chave, rotulo, filtros: { busca: rotulo } });
    }
  });
  // Sem duplicar rótulos idênticos (meses repetidos etc.).
  const vistos = new Set<string>();
  return itens.filter((item) => {
    if (vistos.has(item.rotulo)) return false;
    vistos.add(item.rotulo);
    return true;
  });
}

function FaixaDrill({
  itens,
  onDrillDown,
}: {
  itens: ItemDrill[];
  onDrillDown: (filtros: Partial<FiltrosExtratos>) => void;
}) {
  if (itens.length === 0) return null;
  return (
    <div
      className="mt-3 flex flex-wrap gap-1.5 border-t border-white/[0.06] pt-3"
      role="group"
      aria-label="Ver transações filtradas"
    >
      {itens.map((item) => (
        <button
          key={item.chave}
          type="button"
          onClick={() => onDrillDown(item.filtros)}
          aria-label={`Ver transações de ${item.rotulo}`}
          className={cn(
            "rounded-lg border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] text-muted-foreground",
            "transition-colors hover:bg-white/[0.07] hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          )}
        >
          {item.rotulo}
        </button>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// Próximas faturas (kind "tabela")
// --------------------------------------------------------------------------

const VARIANTE_STATUS_FATURA: Record<string, "default" | "secondary" | "destructive" | "warning" | "muted" | "outline"> = {
  paga: "default",
  parcial: "warning",
  vencida: "destructive",
  fechada: "secondary",
  aberta: "muted",
};

function TabelaProximasFaturas({
  grafico,
  onDrillDown,
}: {
  grafico: GraficoPainel;
  onDrillDown: (filtros: Partial<FiltrosExtratos>) => void;
}) {
  if (grafico.data.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-muted-foreground">
        Nenhuma fatura a vencer com os filtros atuais.
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Vencimento</TableHead>
          <TableHead scope="col">Cartão</TableHead>
          <TableHead scope="col">Competência</TableHead>
          <TableHead scope="col">Status</TableHead>
          <TableHead scope="col" className="text-right">
            Valor previsto
          </TableHead>
          <TableHead scope="col">
            <span className="sr-only">Ações</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {grafico.data.map((ponto, indice) => {
          const faturaId = numeroDoPonto(ponto, "fatura_id");
          const status = textoDoPonto(ponto, "status") ?? "aberta";
          return (
            <TableRow key={faturaId ?? indice}>
              <TableCell className="tabular-nums">
                {formatarDataISO(textoDoPonto(ponto, "data"))}
              </TableCell>
              <TableCell>{textoDoPonto(ponto, "cartao") ?? "—"}</TableCell>
              <TableCell className="tabular-nums">
                {textoDoPonto(ponto, "competencia") ?? "—"}
              </TableCell>
              <TableCell>
                <Badge variant={VARIANTE_STATUS_FATURA[status] ?? "muted"}>
                  {status}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {centavosOuTraco(numeroDoPonto(ponto, "valor_previsto_centavos"))}
              </TableCell>
              <TableCell className="text-right">
                {faturaId !== null && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onDrillDown({ fatura_id: faturaId })}
                    aria-label={`Ver transações da fatura de ${textoDoPonto(ponto, "cartao") ?? "cartão"}`}
                  >
                    Ver transações
                  </Button>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

function ocupaLinhaInteira(grafico: GraficoPainel): boolean {
  if (grafico.kind === "line" || grafico.kind === "area" || grafico.kind === "tabela") {
    return true;
  }
  return grafico.kind === "bar" && grafico.series.length > 1;
}

export interface VisaoGeralProps {
  /** Filtros ativos (os mesmos da aba Transações). */
  filtros: FiltrosExtratos;
  /** Drill-down: aplica o filtro e troca para a aba Transações. */
  onDrillDown: (filtros: Partial<FiltrosExtratos>) => void;
}

export function VisaoGeral({ filtros, onDrillDown }: VisaoGeralProps) {
  const [painel, setPainel] = useState<Painel | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState<boolean>(true);
  const [tentativa, setTentativa] = useState<number>(0);

  useEffect(() => {
    const controlador = new AbortController();
    setCarregando(true);
    setErro(null);
    obterPainel(filtros, controlador.signal)
      .then((resposta) => {
        setPainel(resposta);
        setCarregando(false);
      })
      .catch((falha: unknown) => {
        if (controlador.signal.aborted) return;
        setErro(
          falha instanceof ErroApiExtratos
            ? falha.message
            : "Não foi possível carregar o painel de extratos."
        );
        setCarregando(false);
      });
    return () => controlador.abort();
  }, [filtros, tentativa]);

  const recarregar = useCallback(() => setTentativa((n) => n + 1), []);

  const graficosVisiveis = useMemo(
    () => (painel?.graficos ?? []).filter((grafico) => grafico.data.length > 0),
    [painel]
  );

  if (carregando) {
    return (
      <div className="space-y-6" aria-busy="true" aria-live="polite">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, indice) => (
            <Skeleton key={indice} className="h-28 rounded-2xl" />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Skeleton className="h-72 rounded-2xl lg:col-span-2" />
          <Skeleton className="h-72 rounded-2xl" />
          <Skeleton className="h-72 rounded-2xl" />
        </div>
        <p className="sr-only">Carregando o painel de extratos…</p>
      </div>
    );
  }

  if (erro) {
    return (
      <Card role="alert">
        <CardContent className="flex flex-col items-center gap-3 p-8 text-center">
          <p className="text-sm text-muted-foreground">{erro}</p>
          <Button variant="outline" size="sm" onClick={recarregar}>
            <RefreshCw className="h-4 w-4" aria-hidden />
            Tentar de novo
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (!painel) return null;

  return (
    <div className="space-y-6">
      {painel.avisos.length > 0 && (
        <ul
          aria-live="polite"
          className="space-y-1 rounded-2xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-300"
        >
          {painel.avisos.map((aviso, indice) => (
            <li key={`${indice}-${aviso.slice(0, 24)}`}>· {aviso}</li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {painel.kpis.map((kpi) => (
          <CartaoKpiExtrato key={kpi.id} kpi={kpi} />
        ))}
      </div>

      {graficosVisiveis.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            Nenhum dado para montar os gráficos ainda. Importe um extrato ou uma
            fatura na aba Importar.
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {graficosVisiveis.map((grafico) => {
            const adaptado =
              grafico.kind === "tabela" ? null : adaptarGrafico(grafico);
            return (
              <Card
                key={grafico.id}
                className={cn(ocupaLinhaInteira(grafico) && "lg:col-span-2")}
              >
                <CardHeader>
                  <CardTitle className="text-base">{grafico.title}</CardTitle>
                  {grafico.note && (
                    <CardDescription>{grafico.note}</CardDescription>
                  )}
                </CardHeader>
                <CardContent>
                  {grafico.kind === "tabela" ? (
                    <TabelaProximasFaturas
                      grafico={grafico}
                      onDrillDown={onDrillDown}
                    />
                  ) : adaptado ? (
                    <>
                      <PlanilhaChart series={adaptado} />
                      <FaixaDrill
                        itens={itensDrill(grafico)}
                        onDrillDown={onDrillDown}
                      />
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Formato de gráfico não reconhecido.
                    </p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
