"use client";

/**
 * Renderiza **qualquer** gráfico devolvido pelo motor (`analysis.charts[]`).
 *
 * O componente é genérico de propósito: decide donut/bar/line/area pelo campo
 * `kind`, lê o eixo X em `x_key`, plota as chaves de `series[]` e formata os
 * valores segundo `value_format`. Assim os 9 gráficos da planilha saem do mesmo
 * componente — e um décimo, se o motor passar a emitir, aparece sozinho.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  ChartSeries,
  FormatoValor,
  PontoGrafico,
  SerieGrafico,
} from "@/lib/types-planilha";
import { cn } from "@/lib/utils";
import {
  CHART_AXIS_COLOR,
  CHART_COLORS,
  CHART_GRID_COLOR,
  CHART_SURFACE,
  ChartTooltipFrame,
  LOSS_COLOR,
  NEUTRAL_SERIES_COLOR,
  PROFIT_COLOR,
  PROFIT_SERIES_COLOR,
} from "@/components/charts/chart-tooltip";
import { dividirSeguro, formatarEixo, formatarValor, numeroSeguro } from "./formatar";

/** Cores semânticas por chave de série — o resto cai na paleta do projeto. */
const COR_POR_CHAVE: Record<string, string> = {
  inflow: PROFIT_SERIES_COLOR,
  savings: PROFIT_COLOR,
  expenses: LOSS_COLOR,
  invest_contribution: CHART_COLORS[0],
  balance: PROFIT_COLOR,
  invested: NEUTRAL_SERIES_COLOR,
  interest: CHART_COLORS[1],
  value_income: CHART_COLORS[2],
  value_growth: PROFIT_COLOR,
  value_selected: CHART_COLORS[0],
  contribution: NEUTRAL_SERIES_COLOR,
};

function corDaSerie(chave: string, indice: number): string {
  return COR_POR_CHAVE[chave] ?? CHART_COLORS[indice % CHART_COLORS.length];
}

function valorDoPonto(ponto: PontoGrafico, chave: string): number {
  return numeroSeguro(ponto?.[chave]);
}

function rotuloDoPonto(ponto: PontoGrafico, chaveX: string): string {
  const bruto = ponto?.[chaveX];
  if (bruto === null || bruto === undefined) return "—";
  return String(bruto);
}

// --------------------------------------------------------------------------
// Tooltips
// --------------------------------------------------------------------------

interface ItemPayload {
  dataKey?: string | number;
  value?: number | string;
  color?: string;
  payload?: PontoGrafico;
}

interface TooltipGenericoProps {
  series?: SerieGrafico[];
  formato?: FormatoValor | string;
  active?: boolean;
  label?: string | number;
  payload?: ItemPayload[];
}

/** Uma linha por série plotada, na cor da própria série. */
function TooltipGenerico({
  series = [],
  formato = "currency",
  active,
  label,
  payload,
}: TooltipGenericoProps) {
  if (!active || !payload || payload.length === 0) return null;
  const linhas = series.map((serie, indice) => {
    const item = payload.find((entrada) => entrada.dataKey === serie.key);
    return {
      label: serie.label,
      value: formatarValor(numeroSeguro(item?.value), formato),
      color: item?.color ?? corDaSerie(serie.key, indice),
    };
  });
  if (linhas.length === 0) return null;
  return <ChartTooltipFrame title={label !== undefined ? String(label) : ""} rows={linhas} />;
}

interface TooltipFatiaProps {
  chave?: string;
  formato?: FormatoValor | string;
  total?: number;
  active?: boolean;
  payload?: Array<{ payload?: PontoGrafico & { fill?: string } }>;
  chaveX?: string;
}

/** Tooltip do donut: valor da fatia mais a participação no total. */
function TooltipFatia({
  chave = "value",
  chaveX = "label",
  formato = "currency",
  total = 0,
  active,
  payload,
}: TooltipFatiaProps) {
  if (!active || !payload || payload.length === 0) return null;
  const fatia = payload[0]?.payload;
  if (!fatia) return null;
  const valor = valorDoPonto(fatia, chave);
  const participacao =
    fatia.share !== undefined && fatia.share !== null
      ? numeroSeguro(fatia.share)
      : dividirSeguro(valor, total);
  return (
    <ChartTooltipFrame
      title={rotuloDoPonto(fatia, chaveX)}
      rows={[
        { label: "Valor", value: formatarValor(valor, formato), color: fatia.fill },
        { label: "Participação", value: formatarValor(participacao, "percent") },
      ]}
    />
  );
}

// --------------------------------------------------------------------------
// Peças reutilizadas
// --------------------------------------------------------------------------

function Legenda({ series }: { series: SerieGrafico[] }) {
  if (series.length <= 1) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
      {series.map((serie, indice) => (
        <span key={serie.key} className="flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 rounded-[3px]"
            style={{ backgroundColor: corDaSerie(serie.key, indice) }}
            aria-hidden
          />
          {serie.label}
        </span>
      ))}
    </div>
  );
}

function SemDados({ titulo }: { titulo: string }) {
  return (
    <div className="flex h-56 items-center justify-center rounded-xl border border-dashed border-white/10 px-6 text-center text-sm text-muted-foreground">
      Sem dados para montar “{titulo}”.
    </div>
  );
}

const EIXO = {
  stroke: "transparent",
  tick: { fill: CHART_AXIS_COLOR, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

interface PlanilhaChartProps {
  series: ChartSeries;
  className?: string;
}

export function PlanilhaChart({ series: grafico, className }: PlanilhaChartProps) {
  const dados = Array.isArray(grafico?.data) ? grafico.data : [];
  const plotadas: SerieGrafico[] = Array.isArray(grafico?.series)
    ? grafico.series.filter((serie) => Boolean(serie?.key))
    : [];
  const chaveX = grafico?.x_key || "label";
  const formato: FormatoValor | string = grafico?.value_format || "currency";
  const titulo = grafico?.title || grafico?.id || "gráfico";

  if (dados.length === 0 || plotadas.length === 0) {
    return <SemDados titulo={titulo} />;
  }

  // Funções (e não componentes aninhados): chamadas direto no JSX, elas não
  // criam um tipo de componente novo a cada render — o gráfico não remonta.
  function conteudo() {
    if (grafico.kind === "donut") return renderDonut();
    if (grafico.kind === "line") return renderLinha();
    if (grafico.kind === "area") return renderAreas();
    return renderBarras();
  }

  // ---------------------------------------------------------------- donut
  function renderDonut() {
    const chave = plotadas[0].key;
    const total = dados.reduce((acumulado, ponto) => acumulado + valorDoPonto(ponto, chave), 0);
    // A anotação preserva o índice de `PontoGrafico`: sem ela o spread devolve
    // só `{ fill }` e os campos extras do ponto (share, name…) somem do tipo.
    const fatias: Array<PontoGrafico & { fill: string }> = dados.map(
      (ponto, indice) => ({
        ...ponto,
        fill: CHART_COLORS[indice % CHART_COLORS.length],
      })
    );

    return (
      <div className="flex flex-col items-center gap-6 sm:flex-row">
        <div className="relative h-56 w-56 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={fatias}
                dataKey={chave}
                nameKey={chaveX}
                innerRadius={70}
                outerRadius={100}
                paddingAngle={2}
                strokeWidth={2}
                stroke={CHART_SURFACE}
              >
                {fatias.map((fatia, indice) => (
                  <Cell key={`${rotuloDoPonto(fatia, chaveX)}-${indice}`} fill={fatia.fill} />
                ))}
              </Pie>
              <Tooltip
                content={
                  <TooltipFatia
                    chave={chave}
                    chaveX={chaveX}
                    formato={formato}
                    total={total}
                  />
                }
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Total
            </span>
            <span className="text-lg font-semibold tabular-nums">
              {formatarEixo(total, formato)}
            </span>
          </div>
        </div>

        <ul className="w-full space-y-2.5">
          {fatias.map((fatia, indice) => {
            const valor = valorDoPonto(fatia, chave);
            const participacao =
              fatia.share !== undefined && fatia.share !== null
                ? numeroSeguro(fatia.share)
                : dividirSeguro(valor, total);
            return (
              <li
                key={`${rotuloDoPonto(fatia, chaveX)}-${indice}`}
                className="flex items-center gap-3 text-sm"
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ backgroundColor: fatia.fill }}
                  aria-hidden
                />
                <span className="min-w-0 truncate text-muted-foreground">
                  {rotuloDoPonto(fatia, chaveX)}
                </span>
                <span className="ml-auto font-medium tabular-nums">
                  {formatarValor(participacao, "percent")}
                </span>
                <span className="w-28 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatarEixo(valor, formato)}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  // ---------------------------------------------------------------- barras
  function renderBarras() {
    // Uma série só vira barra horizontal (rótulos longos cabem melhor);
    // várias séries viram barras verticais agrupadas por período.
    if (plotadas.length === 1) {
      const chave = plotadas[0].key;
      const temNegativo = dados.some((ponto) => valorDoPonto(ponto, chave) < 0);
      const maiorRotulo = dados.reduce(
        (maior, ponto) => Math.max(maior, rotuloDoPonto(ponto, chaveX).length),
        0
      );
      const larguraEixo = Math.min(168, Math.max(76, maiorRotulo * 7 + 12));
      const altura = Math.max(224, dados.length * 34 + 24);
      const raio: number | [number, number, number, number] = temNegativo
        ? 4
        : [0, 4, 4, 0];

      return (
        <div style={{ height: altura }} className="w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={dados}
              layout="vertical"
              margin={{ top: 4, right: 24, bottom: 0, left: 4 }}
              barCategoryGap="26%"
            >
              <CartesianGrid stroke={CHART_GRID_COLOR} horizontal={false} />
              <XAxis
                type="number"
                {...EIXO}
                tickFormatter={(valor: number) => formatarEixo(valor, formato)}
              />
              <YAxis
                type="category"
                dataKey={chaveX}
                {...EIXO}
                width={larguraEixo}
                interval={0}
              />
              <Tooltip
                content={<TooltipGenerico series={plotadas} formato={formato} />}
                cursor={{ fill: "rgba(255,255,255,0.04)" }}
              />
              <Bar
                dataKey={chave}
                radius={raio}
                maxBarSize={18}
                fill={corDaSerie(chave, 0)}
              >
                {dados.map((ponto, indice) => (
                  <Cell
                    key={`${rotuloDoPonto(ponto, chaveX)}-${indice}`}
                    fill={
                      temNegativo
                        ? valorDoPonto(ponto, chave) >= 0
                          ? PROFIT_COLOR
                          : LOSS_COLOR
                        : corDaSerie(chave, 0)
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }

    return (
      <div className="space-y-3">
        <Legenda series={plotadas} />
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={dados}
              margin={{ top: 4, right: 8, bottom: 0, left: 4 }}
              barCategoryGap="22%"
              barGap={2}
            >
              <CartesianGrid stroke={CHART_GRID_COLOR} vertical={false} />
              <XAxis dataKey={chaveX} {...EIXO} interval="preserveStartEnd" minTickGap={16} />
              <YAxis
                {...EIXO}
                width={72}
                tickFormatter={(valor: number) => formatarEixo(valor, formato)}
              />
              <Tooltip
                content={<TooltipGenerico series={plotadas} formato={formato} />}
                cursor={{ fill: "rgba(255,255,255,0.04)" }}
              />
              {plotadas.map((serie, indice) => (
                <Bar
                  key={serie.key}
                  dataKey={serie.key}
                  fill={corDaSerie(serie.key, indice)}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={26}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------- linha
  function renderLinha() {
    return (
      <div className="space-y-3">
        <Legenda series={plotadas} />
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={dados} margin={{ top: 4, right: 8, bottom: 0, left: 4 }}>
              <CartesianGrid stroke={CHART_GRID_COLOR} vertical={false} />
              <XAxis dataKey={chaveX} {...EIXO} interval="preserveStartEnd" minTickGap={28} />
              <YAxis
                {...EIXO}
                width={72}
                tickFormatter={(valor: number) => formatarEixo(valor, formato)}
              />
              <Tooltip
                content={<TooltipGenerico series={plotadas} formato={formato} />}
                cursor={{ stroke: "rgba(255,255,255,0.15)", strokeWidth: 1 }}
              />
              {plotadas.map((serie, indice) => (
                <Line
                  key={serie.key}
                  type="monotone"
                  dataKey={serie.key}
                  stroke={corDaSerie(serie.key, indice)}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: CHART_SURFACE }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------- área
  function renderAreas() {
    const idBase = grafico.id || "planilha";
    return (
      <div className="space-y-3">
        <Legenda series={plotadas} />
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={dados} margin={{ top: 4, right: 8, bottom: 0, left: 4 }}>
              <defs>
                {plotadas.map((serie, indice) => (
                  <linearGradient
                    key={serie.key}
                    id={`preenchimento-${idBase}-${serie.key}`}
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop
                      offset="0%"
                      stopColor={corDaSerie(serie.key, indice)}
                      stopOpacity={0.28}
                    />
                    <stop
                      offset="100%"
                      stopColor={corDaSerie(serie.key, indice)}
                      stopOpacity={0.02}
                    />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid stroke={CHART_GRID_COLOR} vertical={false} />
              <XAxis dataKey={chaveX} {...EIXO} interval="preserveStartEnd" minTickGap={28} />
              <YAxis
                {...EIXO}
                width={72}
                tickFormatter={(valor: number) => formatarEixo(valor, formato)}
              />
              <Tooltip
                content={<TooltipGenerico series={plotadas} formato={formato} />}
                cursor={{ stroke: "rgba(255,255,255,0.15)", strokeWidth: 1 }}
              />
              {plotadas.map((serie, indice) => (
                <Area
                  key={serie.key}
                  type="monotone"
                  dataKey={serie.key}
                  stroke={corDaSerie(serie.key, indice)}
                  strokeWidth={2}
                  fill={`url(#preenchimento-${idBase}-${serie.key})`}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: CHART_SURFACE }}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("w-full", className)} role="img" aria-label={`Gráfico ${titulo}`}>
      {conteudo()}
    </div>
  );
}
