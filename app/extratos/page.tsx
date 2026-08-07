"use client";

/**
 * Rota `/extratos` — Extratos & Cartões.
 *
 * Abas shadcn com a aba ativa espelhada em `?aba=` (visao | transacoes |
 * importar | cartoes | categorias | config). Visão geral e Transações são
 * importadas direto (núcleo); as demais chegam por `next/dynamic` com
 * fallback, porque vivem em chunks próprios. Os filtros moram na URL (barra
 * de filtros) e valem para Visão geral e Transações ao mesmo tempo —
 * drill-down num gráfico aplica o filtro e troca para Transações.
 *
 * Backend: tudo vem de `/api/py/extratos/*`. Se o motor não responder, a
 * página explica como subir (`python3 -m engine.server`) em vez de quebrar.
 */

import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Landmark, ServerOff, TriangleAlert } from "lucide-react";
import type { FiltrosExtratos } from "@/lib/types-extratos";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { verificarBackend } from "@/components/extratos/api";
import {
  BarraFiltros,
  aplicarFiltrosEmParams,
  filtrosDeSearchParams,
} from "@/components/extratos/filtros";
import { AbaTransacoes } from "@/components/extratos/aba-transacoes";
import { VisaoGeral } from "@/components/extratos/visao-geral";

/** Fallback exibido enquanto o chunk de uma aba dinâmica carrega. */
function CarregandoAba() {
  return (
    <div className="space-y-4" aria-busy="true" aria-live="polite">
      <Skeleton className="h-28 rounded-2xl" />
      <Skeleton className="h-72 rounded-2xl" />
      <p className="sr-only">Carregando a aba…</p>
    </div>
  );
}

// Abas construídas por outros módulos: carregadas sob demanda, em chunks
// separados, com o mesmo fallback de esqueleto.
const AbaImportar = dynamic(
  () =>
    import("@/components/extratos/aba-importar").then(
      (modulo) => modulo.AbaImportar
    ),
  { loading: CarregandoAba }
);
const AbaCartoesFaturas = dynamic(
  () =>
    import("@/components/extratos/aba-cartoes-faturas").then(
      (modulo) => modulo.AbaCartoesFaturas
    ),
  { loading: CarregandoAba }
);
const AbaCategoriasRegras = dynamic(
  () =>
    import("@/components/extratos/aba-categorias-regras").then(
      (modulo) => modulo.AbaCategoriasRegras
    ),
  { loading: CarregandoAba }
);
const AbaConfig = dynamic(
  () =>
    import("@/components/extratos/aba-config").then(
      (modulo) => modulo.AbaConfig
    ),
  { loading: CarregandoAba }
);

/** Abas válidas em `?aba=`; qualquer outra coisa cai na visão geral. */
const ABAS = [
  { valor: "visao", rotulo: "Visão geral" },
  { valor: "transacoes", rotulo: "Transações" },
  { valor: "importar", rotulo: "Importar" },
  { valor: "cartoes", rotulo: "Cartões & Faturas" },
  { valor: "categorias", rotulo: "Categorias & Regras" },
  { valor: "config", rotulo: "Config" },
] as const;

type ValorAba = (typeof ABAS)[number]["valor"];

function ehAba(valor: string | null): valor is ValorAba {
  return ABAS.some((aba) => aba.valor === valor);
}

type EstadoBackend = "verificando" | "ok" | "sem_persistencia" | "indisponivel";

/** Aviso quando o motor Python não respondeu ou não pode gravar. */
function AvisoBackend({ estado }: { estado: EstadoBackend }) {
  if (estado === "ok" || estado === "verificando") return null;
  const indisponivel = estado === "indisponivel";
  const Icone = indisponivel ? ServerOff : TriangleAlert;
  return (
    <Card
      role="alert"
      className={
        indisponivel ? "border-loss/30 bg-loss/5" : "border-amber-500/30 bg-amber-500/5"
      }
    >
      <CardContent className="flex items-start gap-3 p-4">
        <Icone
          className={`mt-0.5 h-5 w-5 shrink-0 ${indisponivel ? "text-loss" : "text-amber-400"}`}
          aria-hidden
        />
        <div className="space-y-1 text-sm">
          {indisponivel ? (
            <>
              <p className="font-medium text-foreground">
                O motor de extratos não respondeu.
              </p>
              <p className="text-muted-foreground">
                Rodando localmente? Suba o servidor Python na raiz do projeto e
                recarregue a página:
              </p>
              <code className="mt-1 inline-block rounded-lg bg-black/40 px-2.5 py-1 font-mono text-xs text-foreground">
                python3 -m engine.server
              </code>
            </>
          ) : (
            <>
              <p className="font-medium text-foreground">
                Este ambiente não tem armazenamento durável.
              </p>
              <p className="text-muted-foreground">
                Importações e confirmações ficam bloqueadas em serverless sem
                storage montado. Para uso completo, rode em modo self-host
                (<code className="font-mono text-xs">python3 -m engine.server</code>)
                ou configure <code className="font-mono text-xs">CELESTIA_DATA_DURAVEL=1</code>{" "}
                com um volume durável.
              </p>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ExtratosConteudo() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const abaParam = searchParams.get("aba");
  const abaAtiva: ValorAba = ehAba(abaParam) ? abaParam : "visao";

  // Filtros nascem da URL (link compartilhável) e são atualizados pela barra.
  const [filtros, setFiltros] = useState<FiltrosExtratos>(() =>
    filtrosDeSearchParams(new URLSearchParams(searchParams.toString()))
  );
  const [estadoBackend, setEstadoBackend] = useState<EstadoBackend>("verificando");

  useEffect(() => {
    const controlador = new AbortController();
    verificarBackend(controlador.signal)
      .then((estado) => {
        if (!controlador.signal.aborted) setEstadoBackend(estado);
      })
      .catch(() => {
        if (!controlador.signal.aborted) setEstadoBackend("indisponivel");
      });
    return () => controlador.abort();
  }, []);

  /** Troca de aba mantendo filtros e o resto da querystring. */
  const trocarAba = useCallback(
    (valor: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (valor === "visao") params.delete("aba");
      else params.set("aba", valor);
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams]
  );

  /** Drill-down do painel: aplica o filtro, espelha na URL e abre Transações. */
  const aoDrillDown = useCallback(
    (parcial: Partial<FiltrosExtratos>) => {
      const novos: FiltrosExtratos = { ...filtros, ...parcial };
      setFiltros(novos);
      const params = aplicarFiltrosEmParams(
        new URLSearchParams(searchParams.toString()),
        novos
      );
      params.set("aba", "transacoes");
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [filtros, searchParams, router, pathname]
  );

  const mostraFiltros = abaAtiva === "visao" || abaAtiva === "transacoes";
  const bloqueado = estadoBackend === "indisponivel";

  const conteudoBloqueado = useMemo(
    () => (
      <Card>
        <CardContent className="p-8 text-center text-sm text-muted-foreground">
          Sem conexão com o motor de extratos — os dados aparecem assim que o
          servidor Python estiver no ar.
        </CardContent>
      </Card>
    ),
    []
  );

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Landmark className="h-6 w-6 text-primary" aria-hidden />
          Extratos &amp; Cartões
        </h1>
        <p className="text-sm text-muted-foreground">
          Importe extratos bancários e faturas de cartão, categorize com regras,
          concilie pagamentos e acompanhe o fluxo de caixa em competência,
          realizado e projetado.
        </p>
      </div>

      <AvisoBackend estado={estadoBackend} />

      <Tabs value={abaAtiva} onValueChange={trocarAba}>
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
          {ABAS.map((aba) => (
            <TabsTrigger key={aba.valor} value={aba.valor}>
              {aba.rotulo}
            </TabsTrigger>
          ))}
        </TabsList>

        {mostraFiltros && !bloqueado && (
          <BarraFiltros valor={filtros} onChange={setFiltros} className="mt-4" />
        )}

        <TabsContent value="visao" className="mt-4">
          {bloqueado ? (
            conteudoBloqueado
          ) : (
            <VisaoGeral filtros={filtros} onDrillDown={aoDrillDown} />
          )}
        </TabsContent>
        <TabsContent value="transacoes" className="mt-4">
          {bloqueado ? conteudoBloqueado : <AbaTransacoes filtros={filtros} />}
        </TabsContent>
        <TabsContent value="importar" className="mt-4">
          <AbaImportar />
        </TabsContent>
        <TabsContent value="cartoes" className="mt-4">
          {bloqueado ? conteudoBloqueado : <AbaCartoesFaturas />}
        </TabsContent>
        <TabsContent value="categorias" className="mt-4">
          {bloqueado ? conteudoBloqueado : <AbaCategoriasRegras />}
        </TabsContent>
        <TabsContent value="config" className="mt-4">
          <AbaConfig />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function ExtratosPage() {
  // `useSearchParams` exige um limite de Suspense no App Router.
  return (
    <Suspense fallback={<CarregandoAba />}>
      <ExtratosConteudo />
    </Suspense>
  );
}
