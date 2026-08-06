/**
 * Avisos do motor (`analysis.warnings`): abas faltando, colunas não
 * reconhecidas, preços congelados. Nunca são erro — a análise saiu mesmo assim.
 */

import { AlertTriangle } from "lucide-react";

interface ListaAvisosProps {
  avisos: string[];
}

export function ListaAvisos({ avisos }: ListaAvisosProps) {
  const itens = (Array.isArray(avisos) ? avisos : []).filter(
    (aviso) => typeof aviso === "string" && aviso.trim() !== ""
  );
  if (itens.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-medium text-amber-300">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        {itens.length === 1
          ? "1 aviso na leitura da planilha"
          : `${itens.length} avisos na leitura da planilha`}
      </p>
      <ul className="mt-2 space-y-1 pl-6">
        {itens.map((aviso, indice) => (
          <li
            key={`${aviso}-${indice}`}
            className="list-disc text-xs text-amber-300/90"
          >
            {aviso}
          </li>
        ))}
      </ul>
    </div>
  );
}
