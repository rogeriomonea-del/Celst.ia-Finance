"use client";

/** Aba "Fluxo de caixa": consolidação mensal de entradas, gastos e aportes. */

import type { CashflowResult } from "@/lib/types-planilha";
import { cn, formatBRL } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CartaoNumero } from "./cartao-numero";
import { formatarFracao, formatarMesISO } from "./formatar";

interface AbaFluxoProps {
  cashflow: CashflowResult;
}

export function AbaFluxo({ cashflow }: AbaFluxoProps) {
  const meses = cashflow.monthly ?? [];
  const diario = cashflow.daily_balance ?? [];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <CartaoNumero
          rotulo="Entradas no período"
          valor={formatBRL(cashflow.total_inflow)}
          auxiliar={`${meses.length} meses consolidados`}
          tom="profit"
        />
        <CartaoNumero
          rotulo="Gastos no período"
          valor={formatBRL(cashflow.total_expenses)}
          auxiliar="Cartão de crédito + demais despesas"
          tom="loss"
        />
        <CartaoNumero
          rotulo="Taxa de poupança"
          valor={formatarFracao(cashflow.savings_rate, 1)}
          auxiliar="(entradas − gastos) ÷ entradas"
          tom={cashflow.savings_rate >= 0 ? "profit" : "loss"}
        />
        <CartaoNumero
          rotulo="Saldo investido ao fim"
          valor={formatBRL(cashflow.final_invested)}
          auxiliar={`Aportes: ${formatBRL(cashflow.total_contributions)}`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Fluxo mensal</CardTitle>
          <CardDescription>
            Entradas, gastos e aportes por mês, com o saldo de caixa acumulado e
            o patrimônio investido projetado dia a dia
            {diario.length > 0 ? ` (${diario.length} dias na base diária)` : ""}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {meses.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              A planilha não trouxe lançamentos de fluxo de caixa.
            </p>
          ) : (
            <div className="max-h-[32rem] overflow-y-auto scrollbar-thin">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Mês</TableHead>
                    <TableHead className="text-right">Entradas</TableHead>
                    <TableHead className="text-right">Gastos</TableHead>
                    <TableHead className="text-right">Aporte investido</TableHead>
                    <TableHead className="text-right">Sobra</TableHead>
                    <TableHead className="text-right">Saldo em caixa</TableHead>
                    <TableHead className="text-right">Saldo investido</TableHead>
                    <TableHead className="text-right">Patrimônio</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {meses.map((mes, indice) => (
                    <TableRow key={`${mes.month}-${indice}`}>
                      <TableCell className="font-medium">
                        {formatarMesISO(mes.month)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-profit">
                        {mes.inflow > 0 ? formatBRL(mes.inflow) : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-loss">
                        {mes.expenses > 0 ? formatBRL(mes.expenses) : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {mes.invest_contribution > 0
                          ? formatBRL(mes.invest_contribution)
                          : "—"}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums",
                          mes.savings >= 0 ? "text-profit" : "text-loss"
                        )}
                      >
                        {formatBRL(mes.savings)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatBRL(mes.cash_balance)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatBRL(mes.invested_balance)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatBRL(mes.net_worth)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow className="hover:bg-transparent">
                    <TableCell className="text-sm text-muted-foreground">Total</TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(cashflow.total_inflow)}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(cashflow.total_expenses)}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(cashflow.total_contributions)}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(cashflow.total_inflow - cashflow.total_expenses)}
                    </TableCell>
                    <TableCell />
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(cashflow.final_invested)}
                    </TableCell>
                    <TableCell />
                  </TableRow>
                </TableFooter>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
