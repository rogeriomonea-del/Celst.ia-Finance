"use client";

/**
 * Aba "Importe suas Planilhas" (`/importar`).
 *
 * Envia a planilha para o motor Python (`POST /api/py/planilha`, multipart com
 * o campo `file`) e desenha o JSON que volta: faixa de KPIs, os gráficos de
 * `charts[]` e as tabelas por assunto. A página não recalcula nada da análise —
 * a única conta feita aqui é a do simulador interativo, que é local.
 */

import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { FileSpreadsheet, RotateCcw, ShieldCheck } from "lucide-react";
import type { Analysis } from "@/lib/types-planilha";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AbaCarteira } from "@/components/importar/aba-carteira";
import { AbaConsolidado } from "@/components/importar/aba-consolidado";
import { AbaFluxo } from "@/components/importar/aba-fluxo";
import { AbaProjecoes } from "@/components/importar/aba-projecoes";
import { AbaProventos } from "@/components/importar/aba-proventos";
import { AbaRebalanceamento } from "@/components/importar/aba-rebalanceamento";
import { BotaoBaixarJson } from "@/components/importar/botao-baixar-json";
import { EstadoCarregando } from "@/components/importar/estado-carregando";
import { EstadoErro } from "@/components/importar/estado-erro";
import { FaixaKpis } from "@/components/importar/faixa-kpis";
import { GaleriaGraficos } from "@/components/importar/galeria-graficos";
import { ListaAvisos } from "@/components/importar/lista-avisos";
import { PlanilhaDropzone } from "@/components/importar/planilha-dropzone";

/** Endpoint do motor: reescrito para a função Python em `next.config.mjs`. */
const ENDPOINT = "/api/py/planilha";

type Estado = "ocioso" | "enviando" | "pronto" | "erro";

const MENSAGENS_POR_STATUS: Record<number, string> = {
  400: "O arquivo enviado não pôde ser lido como planilha. Confira se é o arquivo certo e tente de novo.",
  404: "O serviço de análise não respondeu neste endereço. Se você está rodando localmente, suba o motor Python antes.",
  413: "Arquivo grande demais: o limite é de 6 MB.",
  429: "Muitas análises seguidas. Espere alguns segundos e tente de novo.",
  500: "O motor encontrou um erro inesperado ao analisar a planilha.",
  504: "A análise demorou mais do que o servidor permite. Tente novamente com um arquivo menor.",
};

const MENSAGEM_PADRAO =
  "Não foi possível analisar a planilha agora. Tente de novo em instantes.";

/** Extrai a mensagem em pt-BR que a função Python devolve em `{"error": ...}`. */
function mensagemDeErro(corpo: unknown, status: number): string {
  if (corpo && typeof corpo === "object") {
    const registro = corpo as Record<string, unknown>;
    if (typeof registro.error === "string" && registro.error.trim() !== "") {
      return registro.error;
    }
  }
  return MENSAGENS_POR_STATUS[status] ?? MENSAGEM_PADRAO;
}

/** Confere que a resposta tem cara de análise antes de renderizar. */
function ehAnalise(valor: unknown): valor is Analysis {
  if (!valor || typeof valor !== "object") return false;
  const registro = valor as Record<string, unknown>;
  return (
    Array.isArray(registro.charts) ||
    Array.isArray(registro.kpis) ||
    typeof registro.file_name === "string"
  );
}

/** "2026-08-05T13:40:00" vira "05/08/2026, 13:40" (ou o texto cru, se estranho). */
function formatarGeradoEm(iso: string): string {
  if (!iso) return "";
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(data);
}

interface AbaDisponivel {
  valor: string;
  rotulo: string;
  conteudo: ReactNode;
}

export default function ImportarPage() {
  const [estado, setEstado] = useState<Estado>("ocioso");
  const [analise, setAnalise] = useState<Analysis | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [nomeArquivo, setNomeArquivo] = useState<string | null>(null);
  const [abaEscolhida, setAbaEscolhida] = useState<string | null>(null);
  const ultimoArquivo = useRef<File | null>(null);

  const enviar = useCallback(async (arquivo: File) => {
    ultimoArquivo.current = arquivo;
    setNomeArquivo(arquivo.name);
    setErro(null);
    setAnalise(null);
    setAbaEscolhida(null);
    setEstado("enviando");

    try {
      const formulario = new FormData();
      formulario.append("file", arquivo, arquivo.name);
      const resposta = await fetch(ENDPOINT, {
        method: "POST",
        body: formulario,
      });

      const corpo: unknown = await resposta.json().catch(() => null);

      if (!resposta.ok) {
        setErro(mensagemDeErro(corpo, resposta.status));
        setEstado("erro");
        return;
      }
      if (!ehAnalise(corpo)) {
        setErro(
          "O motor respondeu, mas em um formato que esta página não reconhece."
        );
        setEstado("erro");
        return;
      }

      setAnalise(corpo);
      setEstado("pronto");
    } catch {
      setErro(
        "Não foi possível falar com o serviço de análise. Verifique sua conexão e tente de novo."
      );
      setEstado("erro");
    }
  }, []);

  const aoErroLocal = useCallback((mensagem: string) => {
    ultimoArquivo.current = null;
    setNomeArquivo(null);
    setErro(mensagem);
    setEstado("erro");
  }, []);

  const recomecar = useCallback(() => {
    ultimoArquivo.current = null;
    setAnalise(null);
    setErro(null);
    setNomeArquivo(null);
    setAbaEscolhida(null);
    setEstado("ocioso");
  }, []);

  const tentarDeNovo = useCallback(() => {
    const arquivo = ultimoArquivo.current;
    if (arquivo) void enviar(arquivo);
  }, [enviar]);

  const abas = useMemo<AbaDisponivel[]>(() => {
    if (!analise) return [];
    const chaveSimulador = analise.generated_at || analise.file_name || "analise";
    const possiveis: Array<AbaDisponivel | null> = [
      analise.portfolio
        ? {
            valor: "carteira",
            rotulo: "Carteira",
            conteudo: <AbaCarteira portfolio={analise.portfolio} />,
          }
        : null,
      analise.rebalance
        ? {
            valor: "rebalanceamento",
            rotulo: "Rebalanceamento",
            conteudo: <AbaRebalanceamento rebalance={analise.rebalance} />,
          }
        : null,
      analise.simulation || analise.projection
        ? {
            valor: "projecoes",
            rotulo: "Projeções",
            conteudo: (
              <AbaProjecoes
                key={chaveSimulador}
                simulation={analise.simulation}
                projection={analise.projection}
                assumptions={analise.assumptions}
              />
            ),
          }
        : null,
      analise.cashflow
        ? {
            valor: "fluxo",
            rotulo: "Fluxo de caixa",
            conteudo: <AbaFluxo cashflow={analise.cashflow} />,
          }
        : null,
      analise.proventos
        ? {
            valor: "proventos",
            rotulo: "Proventos",
            conteudo: <AbaProventos proventos={analise.proventos} />,
          }
        : null,
      analise.consolidated
        ? {
            valor: "consolidado",
            rotulo: "Consolidado",
            conteudo: <AbaConsolidado consolidated={analise.consolidated} />,
          }
        : null,
    ];
    return possiveis.filter((aba): aba is AbaDisponivel => aba !== null);
  }, [analise]);

  const abaAtiva =
    abaEscolhida && abas.some((aba) => aba.valor === abaEscolhida)
      ? abaEscolhida
      : (abas[0]?.valor ?? "");

  const geradoEm = analise ? formatarGeradoEm(analise.generated_at) : "";

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <FileSpreadsheet className="h-6 w-6 text-primary" aria-hidden />
            Importe suas Planilhas
          </h1>
          <p className="text-sm text-muted-foreground">
            Suba a planilha da sua carteira e receba, no lugar das macros e das
            fórmulas, a análise completa: carteira, rebalanceamento, projeções,
            fluxo de caixa, proventos e patrimônio consolidado.
          </p>
        </div>
        {estado === "pronto" && analise && (
          <div className="flex flex-wrap items-center gap-2">
            <BotaoBaixarJson analise={analise} />
            <Button variant="ghost" size="sm" onClick={recomecar}>
              <RotateCcw className="h-4 w-4" />
              Analisar outra
            </Button>
          </div>
        )}
      </div>

      {estado === "erro" && erro && (
        <EstadoErro
          mensagem={erro}
          nomeArquivo={nomeArquivo}
          onTentarDeNovo={ultimoArquivo.current ? tentarDeNovo : undefined}
          onEscolherOutro={recomecar}
        />
      )}

      {estado === "enviando" && <EstadoCarregando nomeArquivo={nomeArquivo} />}

      {(estado === "ocioso" || estado === "erro") && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr]">
          <PlanilhaDropzone
            onArquivo={(arquivo) => void enviar(arquivo)}
            onErro={aoErroLocal}
          />
          <Card className="flex flex-col justify-center">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-primary" aria-hidden />
                O que acontece com o arquivo
              </CardTitle>
              <CardDescription>
                A planilha é lida em memória por um motor Python, que refaz as
                contas das abas de posição, proventos, cenários, projeção, fluxo
                de caixa e posição consolidada. Nada é gravado em disco e nada
                sai do servidor — o resultado volta como JSON só para esta
                página.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1.5 text-sm text-muted-foreground">
                <li>· Formatos aceitos: .xlsm, .xlsx e .csv (até 6 MB)</li>
                <li>· Fórmulas e macros são recalculadas, não executadas</li>
                <li>· Os gráficos da planilha são remontados aqui</li>
              </ul>
            </CardContent>
          </Card>
        </div>
      )}

      {estado === "pronto" && analise && (
        <div className="space-y-6 animate-fade-in">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="muted">{analise.file_name || "planilha"}</Badge>
            {analise.period_label && (
              <Badge variant="outline">{analise.period_label}</Badge>
            )}
            {analise.source_kind && (
              <Badge variant="secondary">{analise.source_kind}</Badge>
            )}
            {geradoEm && <span>Analisada em {geradoEm}</span>}
          </div>

          <ListaAvisos avisos={analise.warnings ?? []} />

          <FaixaKpis kpis={analise.kpis ?? []} />

          <GaleriaGraficos charts={analise.charts ?? []} />

          {abas.length > 0 ? (
            <Tabs
              value={abaAtiva}
              onValueChange={(valor) => setAbaEscolhida(valor)}
            >
              <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1">
                {abas.map((aba) => (
                  <TabsTrigger key={aba.valor} value={aba.valor}>
                    {aba.rotulo}
                  </TabsTrigger>
                ))}
              </TabsList>
              {abas.map((aba) => (
                <TabsContent key={aba.valor} value={aba.valor} className="mt-4">
                  {aba.conteudo}
                </TabsContent>
              ))}
            </Tabs>
          ) : (
            <Card>
              <CardContent className="p-8 text-center text-sm text-muted-foreground">
                A planilha foi lida, mas nenhuma das abas conhecidas (posição,
                rebalanceamento, projeções, fluxo de caixa, proventos ou posição
                consolidada) trouxe dados suficientes para montar as tabelas.
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
