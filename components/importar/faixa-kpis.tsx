"use client";

/**
 * Faixa de indicadores (`analysis.kpis`).
 *
 * O motor manda os KPIs prontos, mas com liberdade de forma: o valor pode vir
 * como número (aí usamos `format`) ou já formatado em texto, e o ícone é só uma
 * dica. Nada aqui depende de um KPI específico existir — a lista é renderizada
 * como vier, na ordem em que veio.
 */

import {
  Banknote,
  Coins,
  LineChart,
  Percent,
  PiggyBank,
  Scale,
  Sparkles,
  TrendingUp,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type { Kpi } from "@/lib/types-planilha";
import { StatCard } from "@/components/stat-card";
import { formatarValor } from "./formatar";

/** Ícones por palavra-chave do id/rótulo — o motor não precisa saber de ícones. */
const ICONES: Array<[RegExp, LucideIcon]> = [
  [/provento|dividend|renda|yield/i, Coins],
  [/rentab|retorno|var|desempenho|juros/i, TrendingUp],
  [/posic|ativo|carteira/i, PiggyBank],
  [/aporte|fluxo|caixa|poupan|saldo/i, Wallet],
  [/peso|aloca|rebalan|banda/i, Scale],
  [/dy|p\/l|p\/vp|taxa|selic|ipca|infla/i, Percent],
  [/projec|cenario|cenário|simula/i, LineChart],
  [/patrim|total|valor|consolidad/i, Banknote],
];

function iconeDoKpi(kpi: Kpi): LucideIcon {
  const chave = `${kpi.icon ?? ""} ${kpi.id ?? ""} ${kpi.label ?? kpi.title ?? ""}`;
  for (const [padrao, icone] of ICONES) {
    if (padrao.test(chave)) return icone;
  }
  return Sparkles;
}

function tomDoKpi(kpi: Kpi): "default" | "profit" | "loss" {
  const tom = (kpi.tone ?? "").toLowerCase();
  if (/profit|positive|positivo|up|alta|bom/.test(tom)) return "profit";
  if (/loss|negative|negativo|down|baixa|ruim|alerta/.test(tom)) return "loss";
  if (tom) return "default";
  // Sem tom explícito, um número negativo se pinta de carmim sozinho.
  if (typeof kpi.value === "number" && kpi.value < 0) return "loss";
  return "default";
}

function valorDoKpi(kpi: Kpi): string {
  if (typeof kpi.value === "string") return kpi.value.trim() || "—";
  if (typeof kpi.value === "number" && Number.isFinite(kpi.value)) {
    return formatarValor(kpi.value, kpi.format ?? "currency");
  }
  return "—";
}

interface FaixaKpisProps {
  kpis: Kpi[];
}

export function FaixaKpis({ kpis }: FaixaKpisProps) {
  const itens = Array.isArray(kpis) ? kpis.filter(Boolean) : [];
  if (itens.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {itens.map((kpi, indice) => (
        <StatCard
          key={kpi.id ?? `${kpi.label ?? kpi.title ?? "kpi"}-${indice}`}
          label={kpi.label ?? kpi.title ?? "Indicador"}
          value={valorDoKpi(kpi)}
          helper={kpi.helper ?? kpi.hint ?? kpi.note}
          icon={iconeDoKpi(kpi)}
          tone={tomDoKpi(kpi)}
        />
      ))}
    </div>
  );
}
