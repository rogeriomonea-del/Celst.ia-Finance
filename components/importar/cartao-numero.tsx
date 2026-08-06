/**
 * Cartão compacto de número — o mesmo desenho dos resumos da página de
 * Rebalanceamento, reaproveitado pelas abas da análise da planilha.
 */

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

interface CartaoNumeroProps {
  rotulo: string;
  valor: string;
  auxiliar?: string;
  tom?: "default" | "profit" | "loss";
}

export function CartaoNumero({
  rotulo,
  valor,
  auxiliar,
  tom = "default",
}: CartaoNumeroProps) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-xs uppercase tracking-wider text-muted-foreground">
          {rotulo}
        </p>
        <p
          className={cn(
            "pt-1.5 text-xl font-semibold tabular-nums",
            tom === "profit" && "text-profit",
            tom === "loss" && "text-loss"
          )}
        >
          {valor}
        </p>
        {auxiliar && (
          <p className="pt-1 text-xs text-muted-foreground/80">{auxiliar}</p>
        )}
      </CardContent>
    </Card>
  );
}
