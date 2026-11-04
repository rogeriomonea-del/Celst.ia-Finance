"use client";

/**
 * Seção "Sessões ativas": uma linha por cookie vivo, com e-mail, IP,
 * user-agent resumido, última atividade e botão "Encerrar".
 */

import { Loader2, Monitor, RefreshCw, TriangleAlert, X } from "lucide-react";
import type { SessaoAdmin } from "@/lib/auth/admin-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatarDataUtc, formatarRelativo, resumirUserAgent } from "@/components/admin/formatar";

interface Props {
  sessoes: SessaoAdmin[];
  carregando: boolean;
  erro: string | null;
  /** Id da sessão cuja ação está em andamento. */
  ocupadoId: number | null;
  onEncerrar: (sessao: SessaoAdmin) => void;
  onRecarregar: () => void;
}

export function SessoesAtivas({
  sessoes,
  carregando,
  erro,
  ocupadoId,
  onEncerrar,
  onRecarregar,
}: Props) {
  return (
    <Card aria-labelledby="titulo-sessoes">
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0 pb-4">
        <div className="space-y-1.5">
          <CardTitle id="titulo-sessoes" className="flex items-center gap-2 text-base">
            <Monitor className="h-5 w-5 text-primary" aria-hidden />
            Sessões ativas
            {!carregando && (
              <span className="ml-1 rounded-lg bg-white/5 px-2 py-0.5 text-xs font-semibold text-muted-foreground">
                {sessoes.length}
              </span>
            )}
          </CardTitle>
          <CardDescription>
            Dispositivos com login válido agora. Encerrar uma sessão obriga a pessoa a
            entrar de novo naquele dispositivo.
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onRecarregar}
          disabled={carregando}
          aria-label="Recarregar sessões"
        >
          <RefreshCw className={carregando ? "animate-spin" : undefined} aria-hidden />
          Atualizar
        </Button>
      </CardHeader>
      <CardContent className="pt-0">
        {erro ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-foreground">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
            {erro}
          </p>
        ) : carregando ? (
          <div className="space-y-2" aria-busy="true" aria-live="polite">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <p className="sr-only">Carregando as sessões…</p>
          </div>
        ) : sessoes.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nenhuma sessão ativa no momento.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Conta</TableHead>
                <TableHead>Dispositivo</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>Última atividade</TableHead>
                <TableHead>Expira em</TableHead>
                <TableHead className="text-right">Ação</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessoes.map((sessao) => {
                const ocupado = ocupadoId === sessao.id;
                return (
                  <TableRow key={sessao.id}>
                    <TableCell className="max-w-[16rem] truncate text-sm font-medium text-foreground">
                      {sessao.email}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <span title={sessao.user_agent || undefined}>
                        {resumirUserAgent(sessao.user_agent)}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {sessao.ip || "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      <span title={formatarDataUtc(sessao.ultima_atividade)}>
                        {formatarRelativo(sessao.ultima_atividade)}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      {formatarDataUtc(sessao.expira_em)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onEncerrar(sessao)}
                        disabled={ocupado}
                        aria-label={`Encerrar a sessão de ${sessao.email}`}
                      >
                        {ocupado ? (
                          <Loader2 className="animate-spin" aria-hidden />
                        ) : (
                          <X aria-hidden />
                        )}
                        Encerrar
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
