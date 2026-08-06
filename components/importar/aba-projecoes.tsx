"use client";

/**
 * Aba "Projeções": o simulador de juros compostos (interativo) e a projeção
 * de patrimônio/renda que o motor calculou a partir do Config da planilha.
 *
 * O simulador refaz a conta no navegador com a mesma fórmula do motor
 * (`engine/calc/simulador.py`): taxa mensal `(1+a)^(1/12)-1` e recursão
 * `saldo = saldo*(1+i) + aporte`. Com os valores originais da planilha ele
 * reproduz exatamente os números do motor; mexer nos campos só muda a
 * simulação local, nunca a análise recebida.
 */

import { useCallback, useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import type {
  Assumptions,
  ChartSeries,
  ProjectionResult,
  SimulationResult,
} from "@/lib/types-planilha";
import { formatBRL, formatNumber } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CartaoNumero } from "./cartao-numero";
import { formatarFracao, formatarMesISO, textoOuTraco } from "./formatar";
import { PlanilhaChart } from "./planilha-chart";

/** Teto de meses simulados — protege o render de um prazo absurdo digitado. */
const MAX_MESES = 720;

interface PontoSimulado {
  label: string;
  month: number;
  balance: number;
  invested: number;
  interest: number;
}

interface ResultadoSimulado {
  taxaMensal: number;
  meses: number;
  saldoFinal: number;
  investido: number;
  juros: number;
  serie: PontoSimulado[];
}

/** Juros compostos com aportes mensais — mesma recursão do motor. */
function simular(
  inicial: number,
  aporte: number,
  taxaAnual: number,
  meses: number
): ResultadoSimulado {
  const totalMeses = Math.max(0, Math.min(Math.round(meses), MAX_MESES));
  // Espelha engine/calc/simulador.py:taxa_mensal — uma taxa anual de -100% ou
  // pior vira -100% ao mês (o capital some), em vez de raiz de número negativo.
  const taxaMensal =
    taxaAnual === 0 ? 0 : taxaAnual <= -1 ? -1 : Math.pow(1 + taxaAnual, 1 / 12) - 1;
  const serie: PontoSimulado[] = [
    { label: "Mês 0", month: 0, balance: inicial, invested: inicial, interest: 0 },
  ];

  let saldo = inicial;
  for (let mes = 1; mes <= totalMeses; mes += 1) {
    saldo = taxaMensal === 0 ? saldo + aporte : saldo * (1 + taxaMensal) + aporte;
    const investido = inicial + aporte * mes;
    serie.push({
      label: `Mês ${mes}`,
      month: mes,
      balance: saldo,
      invested: investido,
      interest: saldo - investido,
    });
  }

  const investido = inicial + aporte * totalMeses;
  return {
    taxaMensal,
    meses: totalMeses,
    saldoFinal: saldo,
    investido,
    juros: saldo - investido,
    serie,
  };
}

/** Lê "120.000,00", "120000" ou "11,207" sem reclamar do formato. */
function paraNumero(texto: string): number {
  const limpo = (texto ?? "").replace(/[^\d,.-]/g, "").trim();
  if (limpo === "" || limpo === "-") return 0;
  const temVirgula = limpo.includes(",");
  const normalizado = temVirgula
    ? limpo.replace(/\./g, "").replace(",", ".")
    : limpo;
  const numero = Number(normalizado);
  return Number.isFinite(numero) ? numero : 0;
}

interface CamposSimulador {
  inicial: string;
  aporte: string;
  taxa: string;
  anos: string;
}

function camposIniciais(
  simulation: SimulationResult | null,
  assumptions: Assumptions
): CamposSimulador {
  const inicial = simulation?.initial ?? assumptions?.initial_capital ?? 0;
  const aporte = simulation?.monthly ?? assumptions?.monthly_contribution ?? 0;
  const taxa = simulation?.annual_rate ?? 0;
  const meses = simulation?.months ?? 48;
  return {
    inicial: String(inicial),
    aporte: String(aporte),
    taxa: String(taxa * 100),
    anos: String(Math.max(1, Math.round(meses / 12))),
  };
}

interface AbaProjecoesProps {
  simulation: SimulationResult | null;
  projection: ProjectionResult | null;
  assumptions: Assumptions;
}

export function AbaProjecoes({
  simulation,
  projection,
  assumptions,
}: AbaProjecoesProps) {
  const iniciais = useMemo(
    () => camposIniciais(simulation, assumptions),
    [simulation, assumptions]
  );
  const [campos, setCampos] = useState<CamposSimulador>(iniciais);

  const alterar = useCallback((chave: keyof CamposSimulador, valor: string) => {
    setCampos((anteriores) => ({ ...anteriores, [chave]: valor }));
  }, []);

  const resultado = useMemo(() => {
    const anos = paraNumero(campos.anos);
    return simular(
      paraNumero(campos.inicial),
      paraNumero(campos.aporte),
      paraNumero(campos.taxa) / 100,
      anos * 12
    );
  }, [campos]);

  const alterado =
    campos.inicial !== iniciais.inicial ||
    campos.aporte !== iniciais.aporte ||
    campos.taxa !== iniciais.taxa ||
    campos.anos !== iniciais.anos;

  const graficoSimulador: ChartSeries = useMemo(
    () => ({
      id: "simulador-interativo",
      title: "Evolução do patrimônio — simulador",
      kind: "line",
      x_key: "label",
      series: [
        { key: "balance", label: "Saldo acumulado" },
        { key: "invested", label: "Total investido" },
        { key: "interest", label: "Juros" },
      ],
      data: resultado.serie.map((ponto) => ({ ...ponto })),
      value_format: "currency",
      note: "",
    }),
    [resultado.serie]
  );

  const pontosProjecao = projection?.series ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">Simulador de juros compostos</CardTitle>
            <CardDescription>
              Começa nos valores da planilha. Ajuste os campos para testar
              cenários — a análise recebida não muda.
            </CardDescription>
          </div>
          {alterado && (
            <Button variant="ghost" size="sm" onClick={() => setCampos(iniciais)}>
              <RotateCcw className="h-3.5 w-3.5" />
              Valores da planilha
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1.5">
              <Label htmlFor="simulador-inicial">Capital inicial (R$)</Label>
              <Input
                id="simulador-inicial"
                inputMode="decimal"
                value={campos.inicial}
                onChange={(evento) => alterar("inicial", evento.target.value)}
                className="tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="simulador-aporte">Aporte mensal (R$)</Label>
              <Input
                id="simulador-aporte"
                inputMode="decimal"
                value={campos.aporte}
                onChange={(evento) => alterar("aporte", evento.target.value)}
                className="tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="simulador-taxa">Taxa anual (%)</Label>
              <Input
                id="simulador-taxa"
                inputMode="decimal"
                value={campos.taxa}
                onChange={(evento) => alterar("taxa", evento.target.value)}
                className="tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="simulador-anos">Prazo (anos)</Label>
              <Input
                id="simulador-anos"
                inputMode="numeric"
                value={campos.anos}
                onChange={(evento) => alterar("anos", evento.target.value)}
                className="tabular-nums"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <CartaoNumero
              rotulo="Patrimônio final"
              valor={formatBRL(resultado.saldoFinal)}
              auxiliar={`${resultado.meses} meses simulados`}
              tom="profit"
            />
            <CartaoNumero
              rotulo="Total investido"
              valor={formatBRL(resultado.investido)}
              auxiliar="Capital inicial + aportes"
            />
            <CartaoNumero
              rotulo="Juros acumulados"
              valor={formatBRL(resultado.juros)}
              auxiliar="Diferença entre patrimônio e investido"
              tom={resultado.juros >= 0 ? "profit" : "loss"}
            />
            <CartaoNumero
              rotulo="Taxa mensal equivalente"
              valor={`${formatNumber(resultado.taxaMensal * 100, 4)}%`}
              auxiliar="(1 + taxa anual) ^ (1/12) − 1"
            />
          </div>

          <PlanilhaChart series={graficoSimulador} />
        </CardContent>
      </Card>

      {projection ? (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <CartaoNumero
              rotulo="Retorno anual — Renda"
              valor={formatarFracao(projection.annual_return_income, 2)}
              auxiliar="Regime voltado a distribuir renda"
            />
            <CartaoNumero
              rotulo="Retorno anual — Valorização"
              valor={formatarFracao(projection.annual_return_growth, 2)}
              auxiliar="Regime voltado a crescimento do principal"
            />
            <CartaoNumero
              rotulo="Patrimônio ao fim"
              valor={formatBRL(projection.final_value)}
              auxiliar={`Cenário do Config: ${textoOuTraco(projection.scenario)}`}
              tom="profit"
            />
            <CartaoNumero
              rotulo="Renda mensal ao fim"
              valor={formatBRL(projection.final_income)}
              auxiliar="Corrigida pela inflação mês a mês"
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Projeção mês a mês</CardTitle>
              <CardDescription>
                {pontosProjecao.length} meses projetados nos dois regimes; a
                coluna “selecionado” segue o cenário definido no Config.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {pontosProjecao.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  A planilha não trouxe horizonte de projeção.
                </p>
              ) : (
                <div className="max-h-[28rem] overflow-y-auto scrollbar-thin">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead>Mês</TableHead>
                        <TableHead className="text-right">Aporte</TableHead>
                        <TableHead className="text-right">Valor — Renda</TableHead>
                        <TableHead className="text-right">Renda mensal</TableHead>
                        <TableHead className="text-right">Valor — Valorização</TableHead>
                        <TableHead className="text-right">Renda mensal</TableHead>
                        <TableHead className="text-right">Selecionado</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {pontosProjecao.map((ponto, indice) => (
                        <TableRow key={`${ponto.month}-${indice}`}>
                          <TableCell className="font-medium">
                            {formatarMesISO(ponto.month)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatBRL(ponto.contribution)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatBRL(ponto.value_income)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatBRL(ponto.income_income)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatBRL(ponto.value_growth)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatBRL(ponto.income_growth)}
                          </TableCell>
                          <TableCell className="text-right font-medium tabular-nums text-profit">
                            {formatBRL(ponto.value_selected)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      ) : (
        <p className="rounded-xl border border-white/[0.06] bg-surface px-4 py-6 text-center text-sm text-muted-foreground">
          A planilha não trouxe os parâmetros de projeção (aba Config /
          Projecao_2030).
        </p>
      )}
    </div>
  );
}
