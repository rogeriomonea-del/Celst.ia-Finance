"use client";

/** Aba "Proventos": total recebido, ranking por ativo, por tipo e por mês. */

import type { ProventosResult } from "@/lib/types-planilha";
import { formatBRL } from "@/lib/utils";
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
import { formatarDataISO, formatarFracao, textoOuTraco } from "./formatar";

interface AbaProventosProps {
  proventos: ProventosResult;
}

export function AbaProventos({ proventos }: AbaProventosProps) {
  const porTicker = proventos.by_ticker ?? [];
  const porTipo = proventos.by_kind ?? [];
  const porMes = proventos.by_month ?? [];

  const periodo =
    proventos.period_start || proventos.period_end
      ? `${formatarDataISO(proventos.period_start)} a ${formatarDataISO(proventos.period_end)}`
      : "Período não informado";

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <CartaoNumero
          rotulo="Total no período"
          valor={formatBRL(proventos.total)}
          auxiliar={periodo}
          tom="profit"
        />
        <CartaoNumero
          rotulo="Média mensal"
          valor={formatBRL(proventos.monthly_average)}
          auxiliar={`${porMes.length} meses com pagamento`}
        />
        <CartaoNumero
          rotulo="Yield sobre a carteira"
          valor={formatarFracao(proventos.yield_on_portfolio, 2)}
          auxiliar="Proventos ÷ valor de mercado das posições"
        />
        <CartaoNumero
          rotulo="Ativos pagadores"
          valor={String(porTicker.length)}
          auxiliar={`${porTipo.length} tipos de provento`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Proventos por ativo</CardTitle>
          <CardDescription>
            Soma recebida por ticker no período, do maior para o menor
          </CardDescription>
        </CardHeader>
        <CardContent>
          {porTicker.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              A planilha não trouxe lançamentos de proventos.
            </p>
          ) : (
            <div className="max-h-[28rem] overflow-y-auto scrollbar-thin">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Ativo</TableHead>
                    <TableHead className="text-right">Lançamentos</TableHead>
                    <TableHead className="text-right">Último pagamento</TableHead>
                    <TableHead className="text-right">Participação</TableHead>
                    <TableHead className="text-right">Valor</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {porTicker.map((item, indice) => (
                    <TableRow key={`${item.ticker}-${indice}`}>
                      <TableCell>
                        <div className="flex flex-col">
                          <span className="font-medium">
                            {textoOuTraco(item.label || item.ticker)}
                          </span>
                          {item.product && item.product !== item.label && (
                            <span className="max-w-[220px] truncate text-xs text-muted-foreground">
                              {item.product}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {item.count}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatarDataISO(item.last_paid_at)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
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
                    <TableCell colSpan={4} className="text-sm text-muted-foreground">
                      {porTicker.length} ativos
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(proventos.total)}
                    </TableCell>
                  </TableRow>
                </TableFooter>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Por tipo</CardTitle>
            <CardDescription>
              Dividendos, JCP, rendimentos e demais classificações da planilha
            </CardDescription>
          </CardHeader>
          <CardContent>
            {porTipo.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Sem classificação por tipo.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Tipo</TableHead>
                    <TableHead className="text-right">Lançamentos</TableHead>
                    <TableHead className="text-right">Participação</TableHead>
                    <TableHead className="text-right">Valor</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {porTipo.map((item, indice) => (
                    <TableRow key={`${item.kind}-${indice}`}>
                      <TableCell>
                        <Badge variant="secondary">{textoOuTraco(item.kind)}</Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {item.count}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatarFracao(item.share, 1)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatBRL(item.amount)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Por mês</CardTitle>
            <CardDescription>
              Quanto entrou de proventos em cada mês do período
            </CardDescription>
          </CardHeader>
          <CardContent>
            {porMes.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Sem datas de pagamento na planilha.
              </p>
            ) : (
              <div className="max-h-[22rem] overflow-y-auto scrollbar-thin">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Mês</TableHead>
                      <TableHead className="text-right">Lançamentos</TableHead>
                      <TableHead className="text-right">Participação</TableHead>
                      <TableHead className="text-right">Valor</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {porMes.map((item, indice) => (
                      <TableRow key={`${item.month}-${indice}`}>
                        <TableCell className="font-medium">
                          {textoOuTraco(item.label)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {item.count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatarFracao(item.share, 1)}
                        </TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {formatBRL(item.amount)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
