"use client";

/**
 * Grade com todos os gráficos de `analysis.charts[]`.
 *
 * Nada aqui conhece os ids do motor: séries temporais (linha/área) e barras com
 * mais de uma série ocupam a linha inteira, o resto vai em duas colunas.
 */

import type { ChartSeries } from "@/lib/types-planilha";
import { cn } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PlanilhaChart } from "./planilha-chart";

function ocupaLinhaInteira(grafico: ChartSeries): boolean {
  if (grafico.kind === "line" || grafico.kind === "area") return true;
  return grafico.kind === "bar" && (grafico.series?.length ?? 0) > 1;
}

interface GaleriaGraficosProps {
  charts: ChartSeries[];
}

export function GaleriaGraficos({ charts }: GaleriaGraficosProps) {
  const graficos = (Array.isArray(charts) ? charts : []).filter(Boolean);
  if (graficos.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {graficos.map((grafico, indice) => (
        <Card
          key={grafico.id || `grafico-${indice}`}
          className={cn(ocupaLinhaInteira(grafico) && "lg:col-span-2")}
        >
          <CardHeader>
            <CardTitle className="text-base">{grafico.title}</CardTitle>
            {grafico.note && <CardDescription>{grafico.note}</CardDescription>}
          </CardHeader>
          <CardContent>
            <PlanilhaChart series={grafico} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
