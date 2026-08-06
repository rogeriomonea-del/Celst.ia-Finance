"use client";

/**
 * Área de envio da planilha: arrastar o arquivo ou clicar para escolher.
 *
 * Acessibilidade: o `<input type="file">` é real e continua no fluxo de foco
 * (apenas visualmente escondido com `sr-only`); o rótulo é um `<label>` de
 * verdade, então clique e Enter/Espaço abrem o seletor sem `role="button"`, e
 * o anel de foco aparece no cartão inteiro via `peer-focus-visible`.
 */

import { useCallback, useRef, useState, type DragEvent } from "react";
import { FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

/** Extensões que o motor Python sabe abrir (`engine/workbook.py`). */
export const EXTENSOES_ACEITAS = [".xlsm", ".xlsx", ".csv"] as const;

const ID_INPUT = "importar-planilha-arquivo";

interface PlanilhaDropzoneProps {
  onArquivo: (arquivo: File) => void;
  onErro: (mensagem: string) => void;
  processando?: boolean;
  nomeArquivo?: string | null;
}

function extensaoValida(nome: string): boolean {
  const minusculo = (nome || "").toLowerCase();
  return EXTENSOES_ACEITAS.some((extensao) => minusculo.endsWith(extensao));
}

export function PlanilhaDropzone({
  onArquivo,
  onErro,
  processando = false,
  nomeArquivo = null,
}: PlanilhaDropzoneProps) {
  const [arrastando, setArrastando] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const receber = useCallback(
    (arquivo: File | undefined | null) => {
      if (!arquivo) return;
      if (!extensaoValida(arquivo.name)) {
        onErro(
          `“${arquivo.name}” não é uma planilha suportada. Envie um arquivo ${EXTENSOES_ACEITAS.join(", ")}.`
        );
        return;
      }
      onArquivo(arquivo);
    },
    [onArquivo, onErro]
  );

  const soltar = useCallback(
    (evento: DragEvent<HTMLLabelElement>) => {
      evento.preventDefault();
      setArrastando(false);
      if (processando) return;
      receber(evento.dataTransfer.files?.[0]);
    },
    [processando, receber]
  );

  return (
    <div className="w-full">
      <input
        ref={inputRef}
        id={ID_INPUT}
        name="file"
        type="file"
        accept={EXTENSOES_ACEITAS.join(",")}
        disabled={processando}
        className="peer sr-only"
        onChange={(evento) => {
          receber(evento.target.files?.[0]);
          evento.target.value = "";
        }}
      />

      <label
        htmlFor={ID_INPUT}
        onDragOver={(evento) => {
          evento.preventDefault();
          if (!processando) setArrastando(true);
        }}
        onDragLeave={() => setArrastando(false)}
        onDrop={soltar}
        className={cn(
          "group flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center transition-colors",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background",
          processando
            ? "cursor-progress border-white/10 bg-surface opacity-80"
            : "cursor-pointer",
          arrastando
            ? "border-primary/60 bg-primary/5"
            : "border-white/15 bg-surface hover:border-white/30 hover:bg-white/[0.02]"
        )}
      >
        <span
          className={cn(
            "flex h-12 w-12 items-center justify-center rounded-2xl ring-1 transition-colors",
            arrastando
              ? "bg-primary/15 text-primary ring-primary/30"
              : "bg-white/5 text-muted-foreground ring-white/10 group-hover:text-foreground"
          )}
          aria-hidden
        >
          {processando ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <UploadCloud className="h-5 w-5" />
          )}
        </span>

        <span className="block space-y-1">
          <span className="block text-sm font-medium">
            {processando
              ? `Analisando ${nomeArquivo ?? "a planilha"}…`
              : "Arraste sua planilha aqui ou clique para escolher"}
          </span>
          <span className="block text-xs text-muted-foreground">
            .xlsm, .xlsx ou .csv — o arquivo é lido em memória, nada fica salvo
            no servidor
          </span>
        </span>

        <span
          className={cn(
            "mt-1 inline-flex h-8 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs font-medium",
            processando ? "opacity-50" : "group-hover:bg-white/5"
          )}
          aria-hidden
        >
          <FileSpreadsheet className="h-4 w-4" />
          Selecionar arquivo
        </span>
      </label>
    </div>
  );
}
