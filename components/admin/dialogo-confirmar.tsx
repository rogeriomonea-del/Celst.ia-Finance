"use client";

/**
 * Diálogo de confirmação genérico da área de administração.
 *
 * Quem abre passa `pedido` (título, descrição, rótulo do botão e se é
 * destrutivo) e `onConfirmar`; o diálogo mostra o erro do motor (409 etc.)
 * como chegou, sem fechar, para o admin ler e desistir ou tentar de novo.
 */

import { useEffect, useState } from "react";
import { Loader2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface PedidoConfirmacao {
  titulo: string;
  descricao: string;
  rotuloConfirmar: string;
  destrutivo?: boolean;
  /** Executa a ação; deve lançar `Error` com mensagem pt-BR em falha. */
  confirmar: () => Promise<void>;
}

interface Props {
  pedido: PedidoConfirmacao | null;
  onFechar: () => void;
}

export function DialogoConfirmar({ pedido, onFechar }: Props) {
  const [executando, setExecutando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  // Limpa o estado a cada novo pedido.
  useEffect(() => {
    setErro(null);
    setExecutando(false);
  }, [pedido]);

  async function confirmar() {
    if (!pedido || executando) return;
    setExecutando(true);
    setErro(null);
    try {
      await pedido.confirmar();
      onFechar();
    } catch (falha) {
      setErro(falha instanceof Error ? falha.message : "Não foi possível concluir a ação.");
    } finally {
      setExecutando(false);
    }
  }

  return (
    <Dialog open={pedido !== null} onOpenChange={(abrir) => !abrir && !executando && onFechar()}>
      <DialogContent aria-describedby="descricao-confirmacao-admin">
        <DialogHeader>
          <DialogTitle>{pedido?.titulo}</DialogTitle>
          <DialogDescription id="descricao-confirmacao-admin">
            {pedido?.descricao}
          </DialogDescription>
        </DialogHeader>

        {erro && (
          <p
            role="alert"
            aria-live="assertive"
            className="flex items-start gap-2 rounded-xl border border-loss/30 bg-loss/5 p-3 text-sm text-foreground"
          >
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
            <span>{erro}</span>
          </p>
        )}

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onFechar} disabled={executando}>
            Cancelar
          </Button>
          <Button
            variant={pedido?.destrutivo ? "destructive" : "default"}
            onClick={confirmar}
            disabled={executando}
            autoFocus
          >
            {executando && <Loader2 className="animate-spin" aria-hidden />}
            {pedido?.rotuloConfirmar}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
