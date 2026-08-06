"use client";

/** Aba "Rebalanceamento": bandas por classe, ação colorida e plano de aporte. */

import { ArrowDownRight, ArrowUpRight, Info } from "lucide-react";
import type { RebalanceResult } from "@/lib/types-planilha";
import { cn, formatBRL } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
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
import { formatarFracao, formatarFracaoComSinal, textoOuTraco } from "./formatar";

type Acao = "COMPRAR" | "VENDER" | "OK";

/** Normaliza a ação vinda do motor (pode chegar com acento ou minúscula). */
function acaoDe(bruta: string): Acao {
  const texto = (bruta ?? "").trim().toUpperCase();
  if (texto.startsWith("COMPR")) return "COMPRAR";
  if (texto.startsWith("VEND")) return "VENDER";
  return "OK";
}

function BadgeAcao({ acao }: { acao: Acao }) {
  if (acao === "COMPRAR") {
    return (
      <Badge variant="default">
        <ArrowUpRight className="mr-1 h-3 w-3" aria-hidden />
        COMPRAR
      </Badge>
    );
  }
  if (acao === "VENDER") {
    return (
      <Badge variant="destructive">
        <ArrowDownRight className="mr-1 h-3 w-3" aria-hidden />
        VENDER
      </Badge>
    );
  }
  return <Badge variant="muted">OK</Badge>;
}

interface AbaRebalanceamentoProps {
  rebalance: RebalanceResult;
}

export function AbaRebalanceamento({ rebalance }: AbaRebalanceamentoProps) {
  const linhas = rebalance.rows ?? [];
  const plano = rebalance.contribution_plan ?? [];
  const notas = rebalance.notes ?? [];

  const totalComprar = linhas
    .filter((linha) => acaoDe(linha.action) === "COMPRAR")
    .reduce((acumulado, linha) => acumulado + Math.abs(linha.amount), 0);
  const totalVender = linhas
    .filter((linha) => acaoDe(linha.action) === "VENDER")
    .reduce((acumulado, linha) => acumulado + Math.abs(linha.amount), 0);
  const totalPlano = plano.reduce((acumulado, item) => acumulado + item.amount, 0);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <CartaoNumero
          rotulo="Patrimônio avaliado"
          valor={formatBRL(rebalance.total_value)}
          auxiliar={`Alvos somam ${formatarFracao(rebalance.targets_sum, 2)}`}
        />
        <CartaoNumero
          rotulo="Banda de tolerância"
          valor={`± ${formatarFracao(rebalance.band_relative, 0)}`}
          auxiliar="Relativa ao alvo de cada classe"
        />
        <CartaoNumero
          rotulo="Total a comprar"
          valor={formatBRL(totalComprar)}
          auxiliar="Classes abaixo da banda mínima"
          tom="profit"
        />
        <CartaoNumero
          rotulo="Total a vender"
          valor={formatBRL(totalVender)}
          auxiliar="Classes acima da banda máxima"
          tom="loss"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Classes fora da banda</CardTitle>
          <CardDescription>
            Banda de {formatarFracao(rebalance.band_relative, 0)} relativa: uma
            classe só entra em ação quando o peso sai do intervalo do alvo.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {linhas.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              A planilha não trouxe alvos de rebalanceamento.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Classe</TableHead>
                  <TableHead className="text-right">Valor atual</TableHead>
                  <TableHead className="text-right">Peso atual</TableHead>
                  <TableHead className="text-right">Alvo</TableHead>
                  <TableHead className="text-right">Banda</TableHead>
                  <TableHead className="text-right">Desvio</TableHead>
                  <TableHead className="text-right">Ajuste (R$)</TableHead>
                  <TableHead className="text-right">Ação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {linhas.map((linha) => {
                  const acao = acaoDe(linha.action);
                  return (
                    <TableRow key={linha.asset_class}>
                      <TableCell className="font-medium">
                        {textoOuTraco(linha.asset_class)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatBRL(linha.current_value)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatarFracao(linha.current_weight, 2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatarFracao(linha.target_weight, 2)}
                      </TableCell>
                      <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                        {formatarFracao(linha.band_min, 2)} –{" "}
                        {formatarFracao(linha.band_max, 2)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums",
                          acao === "OK"
                            ? "text-muted-foreground"
                            : linha.deviation > 0
                              ? "text-loss"
                              : "text-profit"
                        )}
                      >
                        {formatarFracaoComSinal(linha.deviation)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-medium tabular-nums",
                          acao === "COMPRAR" && "text-profit",
                          acao === "VENDER" && "text-loss",
                          acao === "OK" && "text-muted-foreground"
                        )}
                      >
                        {acao === "OK" ? "—" : formatBRL(Math.abs(linha.amount))}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end">
                          <BadgeAcao acao={acao} />
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
              <TableFooter>
                <TableRow className="hover:bg-transparent">
                  <TableCell className="text-sm text-muted-foreground">Total</TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatBRL(rebalance.total_value)}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatarFracao(
                      linhas.reduce((acumulado, linha) => acumulado + linha.current_weight, 0),
                      2
                    )}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatarFracao(rebalance.targets_sum, 2)}
                  </TableCell>
                  <TableCell colSpan={4} />
                </TableRow>
              </TableFooter>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Plano de aporte</CardTitle>
          <CardDescription>
            Como distribuir o próximo aporte para reaproximar a carteira dos
            alvos, sem precisar vender nada.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {plano.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              Nenhum aporte a distribuir: a carteira está dentro das bandas ou a
              planilha não informou aporte mensal.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Classe</TableHead>
                  <TableHead className="text-right">Participação do aporte</TableHead>
                  <TableHead className="text-right">Valor</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {plano.map((item, indice) => (
                  <TableRow key={`${item.asset_class}-${indice}`}>
                    <TableCell className="font-medium">
                      {textoOuTraco(item.asset_class)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatarFracao(item.share, 1)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums text-profit">
                      {formatBRL(item.amount)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
              <TableFooter>
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={2} className="text-sm text-muted-foreground">
                    Aporte total distribuído
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatBRL(totalPlano)}
                  </TableCell>
                </TableRow>
              </TableFooter>
            </Table>
          )}
        </CardContent>
      </Card>

      {notas.length > 0 && (
        <div className="space-y-2 rounded-xl border border-white/[0.06] bg-surface px-4 py-3">
          {notas.map((nota, indice) => (
            <p
              key={`${nota}-${indice}`}
              className="flex items-start gap-2 text-xs text-muted-foreground"
            >
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {nota}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
