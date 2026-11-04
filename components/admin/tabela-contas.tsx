"use client";

/**
 * Seção "Contas": tabela de todas as contas com badges Admin/Ativa e ações
 * (aprovar/desativar, tornar/remover admin, encerrar sessões).
 *
 * As ações só disparam callbacks — a confirmação e a chamada ao motor ficam
 * no painel. A própria conta do admin logado tem "Desativar" e "Remover
 * admin" desabilitados (o motor devolve 409 de qualquer jeito).
 */

import { KeyRound, Loader2, LogOut, ShieldCheck, ShieldOff, UserCheck, UserX } from "lucide-react";
import type { ContaAdmin } from "@/lib/auth/admin-api";
import { Badge } from "@/components/ui/badge";
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
import { AvatarConta } from "@/components/admin/avatar-conta";
import { formatarDataUtc, formatarRelativo } from "@/components/admin/formatar";

interface Props {
  contas: ContaAdmin[];
  carregando: boolean;
  usuarioAtualId: number;
  /** Id da conta cuja ação está em andamento. */
  ocupadoId: number | null;
  onAprovar: (conta: ContaAdmin) => void;
  onDesativar: (conta: ContaAdmin) => void;
  onTornarAdmin: (conta: ContaAdmin) => void;
  onRemoverAdmin: (conta: ContaAdmin) => void;
  onEncerrarSessoes: (conta: ContaAdmin) => void;
}

const ROTULOS_PROVEDOR: Record<string, string> = {
  google: "Google",
};

export function TabelaContas({
  contas,
  carregando,
  usuarioAtualId,
  ocupadoId,
  onAprovar,
  onDesativar,
  onTornarAdmin,
  onRemoverAdmin,
  onEncerrarSessoes,
}: Props) {
  return (
    <Card aria-labelledby="titulo-contas">
      <CardHeader className="pb-4">
        <CardTitle id="titulo-contas" className="flex items-center gap-2 text-base">
          <KeyRound className="h-5 w-5 text-primary" aria-hidden />
          Contas
          {!carregando && (
            <span className="ml-1 rounded-lg bg-white/5 px-2 py-0.5 text-xs font-semibold text-muted-foreground">
              {contas.length}
            </span>
          )}
        </CardTitle>
        <CardDescription>
          Quem pode entrar nas plataformas celest.ia. Desativar uma conta derruba
          todas as sessões dela na hora.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {carregando ? (
          <div className="space-y-2" aria-busy="true" aria-live="polite">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <p className="sr-only">Carregando as contas…</p>
          </div>
        ) : contas.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nenhuma conta cadastrada ainda.
          </p>
        ) : (
          <Table className="[&_td]:px-2 [&_th]:px-2">
            <TableHeader>
              <TableRow>
                <TableHead>Conta</TableHead>
                <TableHead>Criada em</TableHead>
                <TableHead>Último acesso</TableHead>
                <TableHead className="text-right">Sessões</TableHead>
                <TableHead>Provedores</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Ações</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {contas.map((conta) => {
                const ehEu = conta.id === usuarioAtualId;
                const ocupado = ocupadoId === conta.id;
                return (
                  <TableRow key={conta.id} className={conta.ativo ? undefined : "opacity-80"}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <AvatarConta
                          nome={conta.nome}
                          email={conta.email}
                          avatarUrl={conta.avatar_url}
                          className="h-8 w-8"
                        />
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-foreground">
                            {conta.nome?.trim() || conta.email}
                            {ehEu && (
                              <span className="ml-2 text-xs font-normal text-muted-foreground">
                                (você)
                              </span>
                            )}
                          </p>
                          {conta.nome?.trim() && (
                            <p className="truncate text-xs text-muted-foreground">{conta.email}</p>
                          )}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatarDataUtc(conta.criado_em)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <span title={formatarDataUtc(conta.ultimo_acesso)}>
                        {conta.ultimo_acesso ? formatarRelativo(conta.ultimo_acesso) : "nunca"}
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm tabular-nums">
                      {conta.sessoes_ativas}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        <Badge variant="muted">E-mail</Badge>
                        {conta.provedores.map((provedor) => (
                          <Badge key={provedor} variant="outline">
                            {ROTULOS_PROVEDOR[provedor] ?? provedor}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {conta.admin && <Badge>Admin</Badge>}
                        {conta.ativo ? (
                          <Badge variant="outline">Ativa</Badge>
                        ) : (
                          <Badge variant="warning">Pendente</Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="ml-auto flex max-w-[15rem] flex-wrap items-center justify-end gap-1">
                        {ocupado && (
                          <Loader2
                            className="h-4 w-4 animate-spin text-muted-foreground"
                            aria-label="Aplicando…"
                          />
                        )}
                        {conta.ativo ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onDesativar(conta)}
                            disabled={ocupado || ehEu}
                            title={ehEu ? "Você não pode desativar a própria conta." : undefined}
                            aria-label={`Desativar a conta ${conta.email}`}
                          >
                            <UserX aria-hidden />
                            Desativar
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-primary hover:text-primary"
                            onClick={() => onAprovar(conta)}
                            disabled={ocupado}
                            aria-label={`Aprovar a conta ${conta.email}`}
                          >
                            <UserCheck aria-hidden />
                            Aprovar
                          </Button>
                        )}
                        {conta.admin ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onRemoverAdmin(conta)}
                            disabled={ocupado || ehEu}
                            title={ehEu ? "Você não pode remover o próprio acesso de admin." : undefined}
                            aria-label={`Remover admin de ${conta.email}`}
                          >
                            <ShieldOff aria-hidden />
                            Remover admin
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onTornarAdmin(conta)}
                            disabled={ocupado || !conta.ativo}
                            title={
                              conta.ativo ? undefined : "Aprove a conta antes de torná-la admin."
                            }
                            aria-label={`Tornar ${conta.email} admin`}
                          >
                            <ShieldCheck aria-hidden />
                            Tornar admin
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onEncerrarSessoes(conta)}
                          disabled={ocupado || conta.sessoes_ativas === 0}
                          title={
                            conta.sessoes_ativas === 0 ? "Esta conta não tem sessões ativas." : undefined
                          }
                          aria-label={`Encerrar as sessões de ${conta.email}`}
                        >
                          <LogOut aria-hidden />
                          Encerrar sessões
                        </Button>
                      </div>
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
