"use client";

/**
 * Rota `/admin` — Administração de contas, sessões e acessos.
 *
 * Guarda de acesso feita no cliente contra `/api/py/auth/eu`:
 * - 401 → `/entrar?voltar=/admin`;
 * - usuário sem `admin` → card "Só administradores" com link para `/`;
 * - motor fora do ar → aviso com "tentar de novo".
 * Com admin confirmado, renderiza o `PainelAdmin`.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Lock, ServerOff, ShieldCheck } from "lucide-react";
import { ErroAdmin, obterUsuarioAtual, type UsuarioAtual } from "@/lib/auth/admin-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PainelAdmin } from "@/components/admin/painel-admin";

const ROTA_LOGIN = "/entrar?voltar=%2Fadmin";

type Estado =
  | { tipo: "verificando" }
  | { tipo: "sem_admin"; usuario: UsuarioAtual }
  | { tipo: "indisponivel"; mensagem: string }
  | { tipo: "ok"; usuario: UsuarioAtual };

function Cabecalho() {
  return (
    <div className="space-y-1">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <ShieldCheck className="h-6 w-6 text-primary" aria-hidden />
        Administração
      </h1>
      <p className="text-sm text-muted-foreground">
        Aprove contas, controle quem é administrador, encerre sessões e acompanhe
        os acessos às plataformas celest.ia.
      </p>
    </div>
  );
}

export default function PaginaAdmin() {
  const router = useRouter();
  const [estado, setEstado] = useState<Estado>({ tipo: "verificando" });

  const irParaLogin = useCallback(() => {
    router.replace(ROTA_LOGIN);
  }, [router]);

  const verificar = useCallback(
    async (signal?: AbortSignal) => {
      setEstado({ tipo: "verificando" });
      try {
        const usuario = await obterUsuarioAtual(signal);
        setEstado(usuario.admin ? { tipo: "ok", usuario } : { tipo: "sem_admin", usuario });
      } catch (falha) {
        if (falha instanceof DOMException && falha.name === "AbortError") return;
        if (falha instanceof ErroAdmin && falha.status === 401) {
          irParaLogin();
          return;
        }
        setEstado({
          tipo: "indisponivel",
          mensagem:
            falha instanceof Error && falha.message
              ? falha.message
              : "Não foi possível verificar sua sessão.",
        });
      }
    },
    [irParaLogin]
  );

  useEffect(() => {
    const controlador = new AbortController();
    void verificar(controlador.signal);
    return () => controlador.abort();
  }, [verificar]);

  return (
    <div className="space-y-6">
      <Cabecalho />

      {estado.tipo === "verificando" && (
        <div className="space-y-4" aria-busy="true" aria-live="polite">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-48 rounded-2xl" />
          <Skeleton className="h-40 rounded-2xl" />
          <p className="sr-only">Verificando sua sessão…</p>
        </div>
      )}

      {estado.tipo === "indisponivel" && (
        <Card role="alert" className="border-loss/30 bg-loss/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ServerOff className="h-5 w-5 text-loss" aria-hidden />
              Não foi possível verificar sua sessão
            </CardTitle>
            <CardDescription>{estado.mensagem}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button onClick={() => void verificar()}>Tentar de novo</Button>
            <Button variant="outline" asChild>
              <Link href="/">
                <ArrowLeft aria-hidden />
                Voltar ao início
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {estado.tipo === "sem_admin" && (
        <Card className="mx-auto max-w-lg">
          <CardHeader className="items-center text-center">
            <span className="mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-amber-500/15">
              <Lock className="h-6 w-6 text-amber-400" aria-hidden />
            </span>
            <CardTitle>Só administradores</CardTitle>
            <CardDescription>
              Sua conta ({estado.usuario.email}) está ativa, mas não tem permissão de
              administrador. Peça a um administrador para liberar o acesso, se precisar.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button asChild>
              <Link href="/">
                <ArrowLeft aria-hidden />
                Voltar ao início
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {estado.tipo === "ok" && (
        <PainelAdmin usuarioAtual={estado.usuario} onSessaoExpirada={irParaLogin} />
      )}
    </div>
  );
}
