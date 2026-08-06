"use client";

/**
 * Baixa a análise completa em JSON — o mesmo payload que o motor devolveu,
 * sem nenhum campo removido. Gerado no navegador (Blob + object URL), então
 * nada volta ao servidor.
 */

import { useCallback } from "react";
import { Download } from "lucide-react";
import type { Analysis } from "@/lib/types-planilha";
import { Button } from "@/components/ui/button";

interface BotaoBaixarJsonProps {
  analise: Analysis;
}

/** "Carteira B3.xlsm" vira "analise-carteira-b3.json". */
function nomeDoArquivo(nomeOrigem: string): string {
  const base = (nomeOrigem || "planilha")
    .replace(/\.[^.]+$/, "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return `analise-${base || "planilha"}.json`;
}

export function BotaoBaixarJson({ analise }: BotaoBaixarJsonProps) {
  const baixar = useCallback(() => {
    try {
      const conteudo = JSON.stringify(analise, null, 2);
      const endereco = URL.createObjectURL(
        new Blob([conteudo], { type: "application/json;charset=utf-8" })
      );
      const ancora = document.createElement("a");
      ancora.href = endereco;
      ancora.download = nomeDoArquivo(analise?.file_name ?? "");
      document.body.appendChild(ancora);
      ancora.click();
      ancora.remove();
      // Libera a memória do Blob depois que o download começou.
      setTimeout(() => URL.revokeObjectURL(endereco), 2000);
    } catch {
      // Download bloqueado pelo navegador: nada a fazer, mas nada quebra.
    }
  }, [analise]);

  return (
    <Button variant="outline" size="sm" onClick={baixar}>
      <Download className="h-4 w-4" />
      Baixar JSON da análise
    </Button>
  );
}
