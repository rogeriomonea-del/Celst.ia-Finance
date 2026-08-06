"use client";

/** Erro da análise, sempre em pt-BR, com um caminho claro de volta. */

import { AlertTriangle, RefreshCw, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";

interface EstadoErroProps {
  mensagem: string;
  nomeArquivo?: string | null;
  /** Reenvia o mesmo arquivo (só aparece quando ele ainda está em memória). */
  onTentarDeNovo?: () => void;
  onEscolherOutro: () => void;
}

export function EstadoErro({
  mensagem,
  nomeArquivo,
  onTentarDeNovo,
  onEscolherOutro,
}: EstadoErroProps) {
  return (
    <div
      className="rounded-xl border border-loss/30 bg-loss/[0.07] px-4 py-4"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium text-loss">
            Não foi possível analisar {nomeArquivo ? `“${nomeArquivo}”` : "a planilha"}
          </p>
          <p className="text-sm text-muted-foreground">{mensagem}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 pl-7">
        {onTentarDeNovo && (
          <Button size="sm" onClick={onTentarDeNovo}>
            <RefreshCw className="h-4 w-4" />
            Tentar de novo
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onEscolherOutro}>
          <Upload className="h-4 w-4" />
          Escolher outro arquivo
        </Button>
      </div>
    </div>
  );
}
