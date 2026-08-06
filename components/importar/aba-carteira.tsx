"use client";

/** Aba "Carteira": resumo por classe e a tabela de posições com peso e proventos. */

import type { PortfolioResult } from "@/lib/types-planilha";
import { cn, formatBRL, formatNumber, formatQuantity } from "@/lib/utils";
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
import {
  formatarFracao,
  formatarFracaoComSinal,
  fracaoOuTraco,
  numeroOuTraco,
  textoOuTraco,
} from "./formatar";

type VarianteBadge = "default" | "secondary" | "muted" | "warning" | "outline";

const VARIANTE_POR_CLASSE: Array<[RegExp, VarianteBadge]> = [
  [/a[çc][õo]es|stock/i, "default"],
  [/fii|fundo imobili/i, "secondary"],
  [/etf/i, "outline"],
  [/bdr/i, "warning"],
  [/tesouro|renda fixa|cra|cdb|deb/i, "muted"],
];

function varianteDaClasse(classe: string): VarianteBadge {
  for (const [padrao, variante] of VARIANTE_POR_CLASSE) {
    if (padrao.test(classe)) return variante;
  }
  return "muted";
}

interface AbaCarteiraProps {
  portfolio: PortfolioResult;
}

export function AbaCarteira({ portfolio }: AbaCarteiraProps) {
  const posicoes = portfolio.positions ?? [];
  const classes = portfolio.by_class ?? [];
  const totalProventos = posicoes.reduce(
    (acumulado, posicao) => acumulado + (posicao.proventos_period ?? 0),
    0
  );

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <CartaoNumero
          rotulo="Valor total"
          valor={formatBRL(portfolio.total_value)}
          auxiliar="Posições a valor de mercado"
        />
        <CartaoNumero
          rotulo="Posições"
          valor={String(portfolio.positions_count ?? posicoes.length)}
          auxiliar={`${classes.length} classes de ativos`}
        />
        <CartaoNumero
          rotulo="Var. 12m ponderada"
          valor={formatarFracaoComSinal(portfolio.weighted_var_12m)}
          auxiliar="Apenas classes listadas com variação informada"
          tom={portfolio.weighted_var_12m >= 0 ? "profit" : "loss"}
        />
        <CartaoNumero
          rotulo="Proventos casados"
          valor={formatBRL(portfolio.total_proventos)}
          auxiliar="Soma dos proventos por ticker da carteira"
        />
      </div>

      {classes.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Distribuição por classe</CardTitle>
            <CardDescription>
              Valor de mercado e peso de cada classe na carteira B3
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Classe</TableHead>
                  <TableHead className="text-right">Posições</TableHead>
                  <TableHead className="text-right">Valor</TableHead>
                  <TableHead className="text-right">Peso</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {classes.map((classe) => (
                  <TableRow key={classe.asset_class}>
                    <TableCell>
                      <Badge variant={varianteDaClasse(classe.asset_class)}>
                        {textoOuTraco(classe.asset_class)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {classe.positions}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {formatBRL(classe.value)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatarFracao(classe.weight, 2)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
              <TableFooter>
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={2} className="text-sm text-muted-foreground">
                    Total
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatBRL(portfolio.total_value)}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatarFracao(
                      classes.reduce((acumulado, classe) => acumulado + classe.weight, 0),
                      2
                    )}
                  </TableCell>
                </TableRow>
              </TableFooter>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Posições</CardTitle>
          <CardDescription>
            {posicoes.length} ativos — peso na carteira, variação em 12 meses e
            proventos recebidos no período
          </CardDescription>
        </CardHeader>
        <CardContent>
          {posicoes.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              A planilha não trouxe posições reconhecíveis.
            </p>
          ) : (
            <div className="max-h-[34rem] overflow-y-auto scrollbar-thin">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Ativo</TableHead>
                    <TableHead>Classe</TableHead>
                    <TableHead className="text-right">Qtde</TableHead>
                    <TableHead className="text-right">Preço</TableHead>
                    <TableHead className="text-right">Posição</TableHead>
                    <TableHead className="text-right">Peso</TableHead>
                    <TableHead className="text-right">Var. 12m</TableHead>
                    <TableHead className="text-right">Proventos</TableHead>
                    <TableHead className="text-right">DY</TableHead>
                    <TableHead className="text-right">P/L</TableHead>
                    <TableHead className="text-right">P/VP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {posicoes.map((posicao, indice) => (
                    <TableRow key={`${posicao.ticker}-${indice}`}>
                      <TableCell>
                        <div className="flex flex-col">
                          <span className="font-medium">
                            {textoOuTraco(posicao.ticker)}
                          </span>
                          <span className="max-w-[200px] truncate text-xs text-muted-foreground">
                            {posicao.name}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={varianteDaClasse(posicao.asset_class)}>
                          {textoOuTraco(posicao.asset_class)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatQuantity(posicao.quantity)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatBRL(posicao.price)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatBRL(posicao.market_value)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatarFracao(posicao.portfolio_weight, 2)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums",
                          posicao.var_12m === null
                            ? "text-muted-foreground"
                            : posicao.var_12m >= 0
                              ? "text-profit"
                              : "text-loss"
                        )}
                      >
                        {posicao.var_12m === null
                          ? "—"
                          : formatarFracaoComSinal(posicao.var_12m)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {posicao.proventos_period > 0
                          ? formatBRL(posicao.proventos_period)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {fracaoOuTraco(posicao.dividend_yield, 2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {numeroOuTraco(posicao.price_earnings, 1)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {numeroOuTraco(posicao.price_to_book, 2)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={4} className="text-sm text-muted-foreground">
                      {posicoes.length} posições
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(portfolio.total_value)}
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatarFracao(
                        posicoes.reduce(
                          (acumulado, posicao) => acumulado + posicao.portfolio_weight,
                          0
                        ),
                        2
                      )}
                    </TableCell>
                    <TableCell />
                    <TableCell className="text-right font-semibold tabular-nums">
                      {formatBRL(totalProventos)}
                    </TableCell>
                    <TableCell colSpan={3} />
                  </TableRow>
                </TableFooter>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {posicoes.some((posicao) => posicao.note.trim() !== "") && (
        <p className="text-xs text-muted-foreground">
          Observações da planilha (ex.: “não atualizar”) são preservadas por
          posição e respeitadas pelo motor ao aplicar cotações —{" "}
          {formatNumber(
            posicoes.filter((posicao) => posicao.note.trim() !== "").length,
            0
          )}{" "}
          posições têm observação.
        </p>
      )}
    </div>
  );
}
