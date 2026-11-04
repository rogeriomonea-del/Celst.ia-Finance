"use client";

/**
 * Seção "Acessos recentes": os últimos 100 eventos de acesso, com filtro por
 * e-mail (exato, como o motor espera) e ícone de sucesso/falha por linha.
 */

import { type FormEvent, useId } from "react";
import { Activity, CheckCircle2, Loader2, Search, TriangleAlert, XCircle } from "lucide-react";
import type { EventoAcesso } from "@/lib/auth/admin-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatarDataUtc, rotuloAcao } from "@/components/admin/formatar";

interface Props {
  eventos: EventoAcesso[];
  carregando: boolean;
  erro: string | null;
  filtroEmail: string;
  onFiltroEmailChange: (valor: string) => void;
  /** Dispara a busca com o filtro atual (Enter ou botão). */
  onBuscar: () => void;
}

export function AcessosRecentes({
  eventos,
  carregando,
  erro,
  filtroEmail,
  onFiltroEmailChange,
  onBuscar,
}: Props) {
  const idFiltro = useId();

  function aoEnviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    onBuscar();
  }

  return (
    <Card aria-labelledby="titulo-acessos">
      <CardHeader className="pb-4">
        <CardTitle id="titulo-acessos" className="flex items-center gap-2 text-base">
          <Activity className="h-5 w-5 text-primary" aria-hidden />
          Acessos recentes
        </CardTitle>
        <CardDescription>
          Últimos 100 eventos de login, códigos e alterações feitas por administradores.
          Códigos e tokens nunca são registrados.
        </CardDescription>
        <form
          onSubmit={aoEnviar}
          className="flex flex-wrap items-end gap-2 pt-2"
          role="search"
          aria-label="Filtrar acessos por e-mail"
        >
          <div className="min-w-[14rem] flex-1 space-y-1.5">
            <Label htmlFor={idFiltro} className="text-xs text-muted-foreground">
              Filtrar por e-mail
            </Label>
            <Input
              id={idFiltro}
              type="email"
              inputMode="email"
              autoComplete="off"
              placeholder="pessoa@exemplo.com"
              value={filtroEmail}
              onChange={(evento) => onFiltroEmailChange(evento.target.value)}
            />
          </div>
          <Button type="submit" variant="outline" disabled={carregando}>
            {carregando ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <Search aria-hidden />
            )}
            Buscar
          </Button>
          {filtroEmail && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onFiltroEmailChange("");
                onBuscar();
              }}
              disabled={carregando}
            >
              Limpar
            </Button>
          )}
        </form>
      </CardHeader>
      <CardContent className="pt-0">
        {erro ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-foreground">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
            {erro}
          </p>
        ) : carregando && eventos.length === 0 ? (
          <div className="space-y-2" aria-busy="true" aria-live="polite">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <p className="sr-only">Carregando os acessos…</p>
          </div>
        ) : eventos.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {filtroEmail
              ? "Nenhum evento para este e-mail."
              : "Nenhum evento de acesso registrado ainda."}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <span className="sr-only">Resultado</span>
                </TableHead>
                <TableHead>Quando</TableHead>
                <TableHead>E-mail</TableHead>
                <TableHead>Ação</TableHead>
                <TableHead>Detalhe</TableHead>
                <TableHead>IP</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {eventos.map((evento) => (
                <TableRow key={evento.id} className={evento.sucesso ? undefined : "bg-loss/[0.03]"}>
                  <TableCell>
                    {evento.sucesso ? (
                      <CheckCircle2
                        className="h-4 w-4 text-primary"
                        role="img"
                        aria-label="Sucesso"
                      />
                    ) : (
                      <XCircle className="h-4 w-4 text-loss" role="img" aria-label="Falha" />
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                    {formatarDataUtc(evento.quando)}
                  </TableCell>
                  <TableCell className="max-w-[16rem] truncate text-sm text-foreground">
                    {evento.email || "—"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm">
                    {rotuloAcao(evento.acao)}
                  </TableCell>
                  <TableCell className="max-w-[20rem] truncate text-sm text-muted-foreground">
                    <span title={evento.detalhe ?? undefined}>{evento.detalhe || "—"}</span>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {evento.ip || "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
