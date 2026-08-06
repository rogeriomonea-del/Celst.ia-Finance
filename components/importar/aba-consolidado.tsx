"use client";

/** Aba "Consolidado": patrimônio por categoria, liquidez, contas e exterior. */

import type { ConsolidatedResult } from "@/lib/types-planilha";
import { cn, formatBRL, formatNumber, formatQuantity } from "@/lib/utils";
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
import {
  formatarFracao,
  formatarFracaoComSinal,
  formatarUSD,
  textoOuTraco,
} from "./formatar";

interface AbaConsolidadoProps {
  consolidated: ConsolidatedResult;
}

export function AbaConsolidado({ consolidated }: AbaConsolidadoProps) {
  const contas = consolidated.accounts ?? [];
  const porCategoria = consolidated.by_category ?? [];
  const porLiquidez = consolidated.by_liquidity ?? [];
  const exterior = consolidated.foreign ?? [];
  const porCorretora = consolidated.foreign_by_broker ?? [];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <CartaoNumero
          rotulo="Patrimônio consolidado"
          valor={formatBRL(consolidated.total)}
          auxiliar={`${contas.length} contas em ${porCategoria.length} categorias`}
        />
        <CartaoNumero
          rotulo="Exterior (US$)"
          valor={formatarUSD(consolidated.foreign_total_usd)}
          auxiliar={`${exterior.length} posições em ${porCorretora.length} corretoras`}
        />
        <CartaoNumero
          rotulo="Exterior (R$)"
          valor={formatBRL(consolidated.foreign_total_brl)}
          auxiliar={`Câmbio USD/BRL ${formatNumber(consolidated.usd_brl, 2)}`}
        />
        <CartaoNumero
          rotulo="Resultado no exterior"
          valor={formatarUSD(consolidated.foreign_result_usd)}
          auxiliar="Valor de mercado menos custo, em dólar"
          tom={consolidated.foreign_result_usd >= 0 ? "profit" : "loss"}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Por categoria</CardTitle>
            <CardDescription>
              Investimentos, caixa, reserva e exterior já convertidos em reais
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TabelaGrupos grupos={porCategoria} total={consolidated.total} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Por liquidez</CardTitle>
            <CardDescription>
              Em quanto tempo cada parte do patrimônio vira dinheiro na conta
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TabelaGrupos grupos={porLiquidez} total={consolidated.total} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Contas</CardTitle>
          <CardDescription>
            Cada instituição da aba “Posição Consolidada”, com categoria e prazo
            de resgate
          </CardDescription>
        </CardHeader>
        <CardContent>
          {contas.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              A planilha não trouxe a posição consolidada.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Instituição</TableHead>
                  <TableHead>Categoria</TableHead>
                  <TableHead>Liquidez</TableHead>
                  <TableHead className="text-right">Participação</TableHead>
                  <TableHead className="text-right">Valor</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {contas.map((conta, indice) => (
                  <TableRow key={`${conta.institution}-${indice}`}>
                    <TableCell className="font-medium">
                      {textoOuTraco(conta.institution)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {textoOuTraco(conta.category)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {textoOuTraco(conta.liquidity)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatarFracao(conta.share, 1)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {formatBRL(conta.value)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
              <TableFooter>
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={4} className="text-sm text-muted-foreground">
                    {contas.length} contas
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatBRL(consolidated.total)}
                  </TableCell>
                </TableRow>
              </TableFooter>
            </Table>
          )}
        </CardContent>
      </Card>

      {exterior.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Carteira no exterior</CardTitle>
            <CardDescription>
              Posições dolarizadas ao câmbio de {formatNumber(consolidated.usd_brl, 2)}{" "}
              — resultado calculado sobre o custo em dólar
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="max-h-[26rem] overflow-y-auto scrollbar-thin">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Ativo</TableHead>
                    <TableHead>Corretora</TableHead>
                    <TableHead className="text-right">Qtde</TableHead>
                    <TableHead className="text-right">Preço (US$)</TableHead>
                    <TableHead className="text-right">Posição (US$)</TableHead>
                    <TableHead className="text-right">Resultado</TableHead>
                    <TableHead className="text-right">Posição (R$)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {exterior.map((posicao, indice) => (
                    <TableRow key={`${posicao.ticker}-${indice}`}>
                      <TableCell>
                        <div className="flex flex-col">
                          <span className="font-medium">
                            {textoOuTraco(posicao.ticker)}
                          </span>
                          <span className="max-w-[200px] truncate text-xs text-muted-foreground">
                            {posicao.description}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {textoOuTraco(posicao.broker)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatQuantity(posicao.quantity)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatarUSD(posicao.price_usd)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatarUSD(posicao.value_usd)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums",
                          posicao.result_usd >= 0 ? "text-profit" : "text-loss"
                        )}
                      >
                        <span className="flex flex-col items-end leading-tight">
                          <span>{formatarUSD(posicao.result_usd)}</span>
                          <span className="text-[11px] opacity-80">
                            {formatarFracaoComSinal(posicao.result_percent)}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatBRL(posicao.value_brl)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={4} className="text-sm text-muted-foreground">
                      {exterior.length} posições
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatarUSD(consolidated.foreign_total_usd)}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right font-semibold tabular-nums",
                        consolidated.foreign_result_usd >= 0 ? "text-profit" : "text-loss"
                      )}
                    >
                      {formatarUSD(consolidated.foreign_result_usd)}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(consolidated.foreign_total_brl)}
                    </TableCell>
                  </TableRow>
                </TableFooter>
              </Table>
            </div>

            {porCorretora.length > 0 && (
              <div className="space-y-3">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Subtotais por corretora
                </p>
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Corretora</TableHead>
                      <TableHead className="text-right">Posições</TableHead>
                      <TableHead className="text-right">Participação</TableHead>
                      <TableHead className="text-right">Resultado</TableHead>
                      <TableHead className="text-right">Valor (US$)</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {porCorretora.map((item, indice) => (
                      <TableRow key={`${item.broker}-${indice}`}>
                        <TableCell className="font-medium">
                          {textoOuTraco(item.label || item.broker)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {item.positions}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatarFracao(item.share, 1)}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right tabular-nums",
                            item.result_usd >= 0 ? "text-profit" : "text-loss"
                          )}
                        >
                          {formatarUSD(item.result_usd)}
                        </TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {formatarUSD(item.value_usd)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** Tabela curta de grupos (categoria ou liquidez), com barra de participação. */
function TabelaGrupos({
  grupos,
  total,
}: {
  grupos: ConsolidatedResult["by_category"];
  total: number;
}) {
  if (grupos.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Sem agrupamento disponível.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Grupo</TableHead>
          <TableHead className="text-right">Contas</TableHead>
          <TableHead className="text-right">Participação</TableHead>
          <TableHead className="text-right">Valor</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {grupos.map((grupo, indice) => (
          <TableRow key={`${grupo.label}-${indice}`}>
            <TableCell className="font-medium">{textoOuTraco(grupo.label)}</TableCell>
            <TableCell className="text-right tabular-nums text-muted-foreground">
              {grupo.accounts}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatarFracao(grupo.share, 1)}
            </TableCell>
            <TableCell className="text-right font-medium tabular-nums">
              {formatBRL(grupo.value)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
      <TableFooter>
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={3} className="text-sm text-muted-foreground">
            Total
          </TableCell>
          <TableCell className="text-right font-semibold tabular-nums">
            {formatBRL(total)}
          </TableCell>
        </TableRow>
      </TableFooter>
    </Table>
  );
}
