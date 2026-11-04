"use client";

/**
 * Seção "Contas pendentes": contas com `ativo=false` que ainda não foram
 * aprovadas (ou foram desativadas). Destaque em âmbar com botão "Aprovar".
 */

import { Loader2, UserCheck } from "lucide-react";
import type { ContaAdmin } from "@/lib/auth/admin-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AvatarConta } from "@/components/admin/avatar-conta";
import { formatarDataUtc } from "@/components/admin/formatar";

interface Props {
  contas: ContaAdmin[];
  /** Id da conta cuja ação está em andamento (desabilita o botão dela). */
  ocupadoId: number | null;
  onAprovar: (conta: ContaAdmin) => void;
}

export function ContasPendentes({ contas, ocupadoId, onAprovar }: Props) {
  if (contas.length === 0) return null;

  return (
    <Card className="border-amber-500/30 bg-amber-500/5" aria-labelledby="titulo-pendentes">
      <CardHeader className="pb-4">
        <CardTitle id="titulo-pendentes" className="flex items-center gap-2 text-base">
          <UserCheck className="h-5 w-5 text-amber-400" aria-hidden />
          Contas aguardando aprovação
          <span className="ml-1 rounded-lg bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-400">
            {contas.length}
          </span>
        </CardTitle>
        <CardDescription>
          Estas pessoas já se identificaram, mas só conseguem entrar depois que um
          administrador aprovar a conta.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        <ul className="divide-y divide-white/[0.06]">
          {contas.map((conta) => {
            const ocupado = ocupadoId === conta.id;
            return (
              <li
                key={conta.id}
                className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0"
              >
                <AvatarConta nome={conta.nome} email={conta.email} avatarUrl={conta.avatar_url} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {conta.nome?.trim() || conta.email}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {conta.nome?.trim() ? `${conta.email} · ` : ""}
                    pediu acesso em {formatarDataUtc(conta.criado_em)}
                  </p>
                </div>
                <Button
                  size="sm"
                  onClick={() => onAprovar(conta)}
                  disabled={ocupado}
                  aria-label={`Aprovar a conta ${conta.email}`}
                >
                  {ocupado ? (
                    <Loader2 className="animate-spin" aria-hidden />
                  ) : (
                    <UserCheck aria-hidden />
                  )}
                  Aprovar
                </Button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
