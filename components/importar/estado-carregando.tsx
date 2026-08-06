"use client";

/**
 * Estado de carregamento da análise: os três passos que o motor executa.
 *
 * Os passos avançam por tempo (a requisição é uma só, sem streaming), mas o
 * último fica girando até a resposta chegar — nunca mostramos "pronto" antes
 * do tempo. O bloco é um `role="status"` com `aria-live="polite"`, então leitor
 * de tela anuncia cada etapa sem interromper o que o usuário estiver ouvindo.
 */

import { useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

const PASSOS = [
  "Lendo a pasta de trabalho",
  "Recalculando fórmulas",
  "Montando gráficos",
] as const;

/** Quando cada passo entra em cena (ms desde o início do envio). */
const MARCOS_MS = [1100, 2600] as const;

interface EstadoCarregandoProps {
  nomeArquivo?: string | null;
}

export function EstadoCarregando({ nomeArquivo }: EstadoCarregandoProps) {
  const [passoAtual, setPassoAtual] = useState(0);

  useEffect(() => {
    const relogios = MARCOS_MS.map((atraso, indice) =>
      setTimeout(() => setPassoAtual(indice + 1), atraso)
    );
    return () => relogios.forEach(clearTimeout);
  }, []);

  const progresso = ((passoAtual + 0.5) / PASSOS.length) * 100;

  return (
    <Card>
      <CardContent
        className="space-y-5 p-6"
        role="status"
        aria-live="polite"
        aria-busy="true"
      >
        <div className="space-y-1.5">
          <p className="text-sm font-medium">
            Analisando {nomeArquivo ? `“${nomeArquivo}”` : "a planilha"}…
          </p>
          <p className="text-xs text-muted-foreground">
            {PASSOS[passoAtual]} — isso costuma levar poucos segundos.
          </p>
        </div>

        <Progress value={progresso} aria-hidden />

        <ol className="space-y-2.5">
          {PASSOS.map((passo, indice) => {
            const concluido = indice < passoAtual;
            const ativo = indice === passoAtual;
            return (
              <li
                key={passo}
                className={cn(
                  "flex items-center gap-3 text-sm transition-colors",
                  concluido && "text-muted-foreground",
                  ativo && "text-foreground",
                  !concluido && !ativo && "text-muted-foreground/50"
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-lg ring-1",
                    concluido && "bg-profit/10 text-profit ring-profit/20",
                    ativo && "bg-primary/10 text-primary ring-primary/25",
                    !concluido && !ativo && "bg-white/5 text-muted-foreground ring-white/10"
                  )}
                  aria-hidden
                >
                  {concluido ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : ativo ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  )}
                </span>
                <span>{passo}</span>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
