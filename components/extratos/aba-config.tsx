"use client";

/**
 * Aba "Config": importação da planilha de configuração (o layout da
 * Dashboard_de_Faturas e afins).
 *
 * Fluxo: dropzone própria → `POST /extratos/config/planilha` (prévia, NADA é
 * gravado) → tabela de mapeamento coluna→campo, cartões novos × atualizados
 * (diff campo a campo), faturas novas/atualizadas, conflitos em âmbar com
 * explicação e erros por linha em carmim → "Confirmar e versionar"
 * (`POST /extratos/config/planilha/confirmar`) → nova linha no histórico de
 * versões (`config_versoes`), com quem aprovou e quando.
 *
 * A prévia chega do motor como JSON; aqui tudo é lido de forma defensiva —
 * formato inesperado vira texto exibível, nunca exceção.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  FileSpreadsheet,
  History,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BASE_EXTRATOS, ErroApiExtratos } from "@/components/extratos/api";
import {
  DropzoneArquivo,
  ListaExpansivel,
} from "@/components/extratos/aba-importar";
import {
  formatarDataHoraISO,
  formatarInteiro,
  textoOuTraco,
} from "@/components/extratos/formatar-centavos";

// --------------------------------------------------------------------------
// Chamadas locais (rotas de config ainda não expostas no cliente comum)
// --------------------------------------------------------------------------

const EXTENSOES_CONFIG = [".xlsx", ".xlsm", ".csv"] as const;

const MENSAGEM_REDE =
  "Não foi possível falar com o serviço de extratos. Verifique se o motor está no ar (python3 -m engine.server) e tente de novo.";

async function requisitarConfig<T>(caminho: string, init?: RequestInit): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(`${BASE_EXTRATOS}${caminho}`, init);
  } catch {
    throw new ErroApiExtratos(MENSAGEM_REDE, 0);
  }
  const corpo: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) {
    const registro =
      corpo && typeof corpo === "object" ? (corpo as Record<string, unknown>) : null;
    const mensagem =
      registro && typeof registro.erro === "string" && registro.erro.trim() !== ""
        ? registro.erro
        : `O motor respondeu com erro ${resposta.status}.`;
    throw new ErroApiExtratos(mensagem, resposta.status);
  }
  return corpo as T;
}

/** Envia a planilha de configuração e recebe a prévia (nada é gravado). */
function enviarPlanilhaConfig(arquivo: File): Promise<Record<string, unknown>> {
  const formulario = new FormData();
  formulario.append("file", arquivo, arquivo.name);
  return requisitarConfig("/config/planilha", { method: "POST", body: formulario });
}

/** Confirma a prévia: grava cartões/faturas e cria a versão de configuração. */
function confirmarPlanilhaConfig(
  arquivo: File,
  aprovadoPor: string
): Promise<Record<string, unknown>> {
  const formulario = new FormData();
  formulario.append("file", arquivo, arquivo.name);
  formulario.append("aprovado_por", aprovadoPor);
  return requisitarConfig("/config/planilha/confirmar", {
    method: "POST",
    body: formulario,
  });
}

function listarVersoesConfig(): Promise<unknown> {
  return requisitarConfig("/config/versoes", { method: "GET" });
}

// --------------------------------------------------------------------------
// Leitura defensiva da prévia (o JSON vem do motor; formato pode evoluir)
// --------------------------------------------------------------------------

type Registro = Record<string, unknown>;

function comoRegistro(valor: unknown): Registro | null {
  return valor && typeof valor === "object" && !Array.isArray(valor)
    ? (valor as Registro)
    : null;
}

function comoLista(valor: unknown): unknown[] {
  return Array.isArray(valor) ? valor : [];
}

/** Qualquer coisa vira texto exibível, sem lançar exceção. */
function comoTexto(valor: unknown): string {
  if (valor === null || valor === undefined) return "";
  if (typeof valor === "string") return valor;
  if (typeof valor === "number" || typeof valor === "boolean") return String(valor);
  try {
    return JSON.stringify(valor);
  } catch {
    return String(valor);
  }
}

function primeiroTexto(registro: Registro, chaves: string[]): string | null {
  for (const chave of chaves) {
    const texto = comoTexto(registro[chave]).trim();
    if (texto !== "") return texto;
  }
  return null;
}

/** Item de conflito/erro pode ser string ou objeto — vira uma frase única. */
function fraseDoItem(item: unknown): string {
  if (typeof item === "string") return item;
  const registro = comoRegistro(item);
  if (!registro) return comoTexto(item);
  const linha = primeiroTexto(registro, ["origem_ref", "linha", "celula", "referencia"]);
  const mensagem = primeiroTexto(registro, [
    "mensagem",
    "explicacao",
    "motivo",
    "erro",
    "descricao",
  ]);
  if (linha && mensagem) return `${linha}: ${mensagem}`;
  if (mensagem) return mensagem;
  return Object.entries(registro)
    .map(([chave, valor]) => `${chave}: ${comoTexto(valor)}`)
    .join(" · ");
}

interface ParDeMapeamento {
  coluna: string;
  campo: string;
}

/** Aceita `{coluna: campo}` ou `[{coluna, campo}]`. */
function paresDeMapeamento(bruto: unknown): ParDeMapeamento[] {
  const registro = comoRegistro(bruto);
  if (registro) {
    return Object.entries(registro).map(([coluna, campo]) => ({
      coluna,
      campo: comoTexto(campo),
    }));
  }
  return comoLista(bruto)
    .map((item) => comoRegistro(item))
    .filter((item): item is Registro => item !== null)
    .map((item) => ({
      coluna: primeiroTexto(item, ["coluna", "origem"]) ?? "?",
      campo: primeiroTexto(item, ["campo", "destino", "papel"]) ?? "?",
    }));
}

interface MudancaCampo {
  campo: string;
  de: string;
  para: string;
}

/** Diff campo a campo de um cartão atualizado — tolera vários formatos. */
function mudancasDoItem(item: Registro): MudancaCampo[] {
  const alteracoes = comoRegistro(item.alteracoes ?? item.diff ?? item.mudancas);
  if (alteracoes) {
    return Object.entries(alteracoes).map(([campo, valor]) => {
      const detalhe = comoRegistro(valor);
      if (detalhe) {
        return {
          campo,
          de: comoTexto(detalhe.de ?? detalhe.antes ?? detalhe.anterior),
          para: comoTexto(detalhe.para ?? detalhe.depois ?? detalhe.novo),
        };
      }
      return { campo, de: "", para: comoTexto(valor) };
    });
  }
  const listaDiff = comoLista(item.diffs ?? item.diferencas);
  return listaDiff
    .map((entrada) => comoRegistro(entrada))
    .filter((entrada): entrada is Registro => entrada !== null)
    .map((entrada) => ({
      campo: primeiroTexto(entrada, ["campo"]) ?? "?",
      de: comoTexto(entrada.de ?? entrada.antes),
      para: comoTexto(entrada.para ?? entrada.depois),
    }));
}

/** Pares campo→valor de um cartão novo (pula vazios e chaves estruturais). */
function paresDoRegistro(item: Registro): Array<[string, string]> {
  const ocultas = new Set(["nome", "alteracoes", "diff", "mudancas", "diffs", "diferencas"]);
  return Object.entries(item)
    .filter(([chave]) => !ocultas.has(chave))
    .map(([chave, valor]): [string, string] => [chave, comoTexto(valor)])
    .filter(([, valor]) => valor.trim() !== "" && valor !== "null");
}

interface PreviaConfig {
  mapeamento: ParDeMapeamento[];
  cartoesNovos: Registro[];
  cartoesAtualizados: Registro[];
  faturasNovas: number;
  faturasAtualizadas: number;
  conflitos: string[];
  erros: string[];
  avisos: string[];
}

function contagemOuTamanho(valor: unknown): number {
  if (typeof valor === "number" && Number.isFinite(valor)) return Math.trunc(valor);
  return comoLista(valor).length;
}

function normalizarPrevia(bruta: Record<string, unknown>): PreviaConfig {
  const registros = (valor: unknown): Registro[] =>
    comoLista(valor)
      .map((item) => comoRegistro(item))
      .filter((item): item is Registro => item !== null);
  return {
    mapeamento: paresDeMapeamento(bruta.mapeamento),
    cartoesNovos: registros(bruta.cartoes_novos ?? bruta.novos),
    cartoesAtualizados: registros(
      bruta.cartoes_atualizados ?? bruta.atualizacoes ?? bruta.atualizados
    ),
    faturasNovas: contagemOuTamanho(bruta.faturas_novas),
    faturasAtualizadas: contagemOuTamanho(bruta.faturas_atualizadas),
    conflitos: comoLista(bruta.conflitos).map(fraseDoItem),
    erros: comoLista(bruta.erros).map(fraseDoItem),
    avisos: comoLista(bruta.avisos).map(fraseDoItem),
  };
}

// --------------------------------------------------------------------------
// Histórico de versões (config_versoes)
// --------------------------------------------------------------------------

interface VersaoConfig {
  id: number;
  quando: string;
  aprovadoPor: string;
  resumo: string;
}

function normalizarVersoes(bruto: unknown): VersaoConfig[] {
  const lista = Array.isArray(bruto)
    ? bruto
    : comoLista(comoRegistro(bruto)?.itens ?? comoRegistro(bruto)?.versoes);
  return lista
    .map((item) => comoRegistro(item))
    .filter((item): item is Registro => item !== null)
    .map((item) => {
      let resumo = comoTexto(item.resumo_json ?? item.resumo);
      const registroResumo = comoRegistro(
        typeof item.resumo_json === "string"
          ? (() => {
              try {
                return JSON.parse(item.resumo_json);
              } catch {
                return null;
              }
            })()
          : (item.resumo_json ?? item.resumo)
      );
      if (registroResumo) {
        resumo = Object.entries(registroResumo)
          .filter(([, valor]) => typeof valor === "number" || typeof valor === "string")
          .slice(0, 6)
          .map(([chave, valor]) => `${chave}: ${comoTexto(valor)}`)
          .join(" · ");
      }
      return {
        id: typeof item.id === "number" ? item.id : 0,
        quando: primeiroTexto(item, ["quando", "criado_em"]) ?? "",
        aprovadoPor: primeiroTexto(item, ["aprovado_por", "aprovador"]) ?? "—",
        resumo,
      };
    });
}

// --------------------------------------------------------------------------
// Peças visuais da prévia
// --------------------------------------------------------------------------

function CartaoNovoItem({ item }: { item: Registro }) {
  const nome = primeiroTexto(item, ["nome", "cartao"]) ?? "Cartão sem nome";
  const pares = paresDoRegistro(item);
  return (
    <li className="rounded-lg border border-profit/20 bg-profit/[0.05] px-3 py-2">
      <span className="flex items-center gap-2">
        <Badge>novo</Badge>
        <span className="text-sm font-medium">{nome}</span>
      </span>
      {pares.length > 0 && (
        <span className="mt-1 block text-xs text-muted-foreground">
          {pares.map(([chave, valor]) => `${chave}: ${valor}`).join(" · ")}
        </span>
      )}
    </li>
  );
}

function CartaoAtualizadoItem({ item }: { item: Registro }) {
  const nome = primeiroTexto(item, ["nome", "cartao"]) ?? "Cartão sem nome";
  const mudancas = mudancasDoItem(item);
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
      <span className="flex items-center gap-2">
        <Badge variant="secondary">atualizado</Badge>
        <span className="text-sm font-medium">{nome}</span>
      </span>
      {mudancas.length > 0 ? (
        <ul className="mt-1.5 space-y-0.5">
          {mudancas.map((mudanca) => (
            <li key={mudanca.campo} className="text-xs">
              <span className="text-muted-foreground">{mudanca.campo}: </span>
              {mudanca.de !== "" && (
                <>
                  <span className="text-loss line-through">{textoOuTraco(mudanca.de)}</span>
                  <span className="text-muted-foreground"> → </span>
                </>
              )}
              <span className="text-profit">{textoOuTraco(mudanca.para)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <span className="mt-1 block text-xs text-muted-foreground">
          Sem diferenças campo a campo informadas pelo motor.
        </span>
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// Componente público da aba
// --------------------------------------------------------------------------

type EstadoConfig = "ocioso" | "enviando" | "previa";

export function AbaConfig() {
  const idBase = useId();

  const [estado, setEstado] = useState<EstadoConfig>("ocioso");
  const [previa, setPrevia] = useState<PreviaConfig | null>(null);
  const [arquivo, setArquivo] = useState<File | null>(null);
  const [aprovadoPor, setAprovadoPor] = useState("usuario");
  const [confirmando, setConfirmando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [sucesso, setSucesso] = useState<string | null>(null);

  const [versoes, setVersoes] = useState<VersaoConfig[]>([]);
  const [carregandoVersoes, setCarregandoVersoes] = useState(true);
  const [versoesIndisponiveis, setVersoesIndisponiveis] = useState(false);

  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);

  const recarregarVersoes = useCallback(() => {
    setCarregandoVersoes(true);
    listarVersoesConfig()
      .then((bruto) => {
        if (!montado.current) return;
        setVersoes(normalizarVersoes(bruto));
        setVersoesIndisponiveis(false);
      })
      .catch(() => {
        if (!montado.current) return;
        setVersoesIndisponiveis(true);
      })
      .finally(() => {
        if (montado.current) setCarregandoVersoes(false);
      });
  }, []);

  useEffect(() => {
    recarregarVersoes();
  }, [recarregarVersoes]);

  const enviar = useCallback(async (escolhido: File) => {
    setErro(null);
    setSucesso(null);
    setPrevia(null);
    setArquivo(escolhido);
    setEstado("enviando");
    try {
      const bruta = await enviarPlanilhaConfig(escolhido);
      if (!montado.current) return;
      setPrevia(normalizarPrevia(bruta));
      setEstado("previa");
    } catch (falha) {
      if (!montado.current) return;
      setErro(
        falha instanceof ErroApiExtratos
          ? falha.message
          : "Não foi possível analisar a planilha de configuração."
      );
      setEstado("ocioso");
      setArquivo(null);
    }
  }, []);

  const confirmar = useCallback(async () => {
    if (!arquivo) return;
    setConfirmando(true);
    setErro(null);
    try {
      await confirmarPlanilhaConfig(arquivo, aprovadoPor.trim() || "usuario");
      if (!montado.current) return;
      setSucesso(
        `Configuração de “${arquivo.name}” confirmada e versionada por ${aprovadoPor.trim() || "usuario"}.`
      );
      setPrevia(null);
      setArquivo(null);
      setEstado("ocioso");
      recarregarVersoes();
    } catch (falha) {
      if (!montado.current) return;
      setErro(
        falha instanceof ErroApiExtratos
          ? falha.message
          : "Não foi possível confirmar a configuração."
      );
    } finally {
      if (montado.current) setConfirmando(false);
    }
  }, [aprovadoPor, arquivo, recarregarVersoes]);

  const descartar = useCallback(() => {
    setPrevia(null);
    setArquivo(null);
    setEstado("ocioso");
    setSucesso("Prévia descartada. Nada foi gravado.");
    setErro(null);
  }, []);

  return (
    <div className="space-y-6">
      {sucesso && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-start gap-3 rounded-xl border border-profit/30 bg-profit/[0.07] px-4 py-3"
        >
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-profit" aria-hidden />
          <p className="text-sm text-profit">{sucesso}</p>
        </div>
      )}
      {erro && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-xl border border-loss/30 bg-loss/[0.07] px-4 py-3"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
          <p className="text-sm text-loss">{erro}</p>
        </div>
      )}

      {estado !== "previa" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden />
              Planilha de configuração
            </CardTitle>
            <CardDescription>
              Envie a planilha de cartões e faturas (ex.: Dashboard_de_Faturas). O motor
              monta uma prévia com o mapeamento de colunas, o que será criado ou
              atualizado e os conflitos — nada é gravado antes da confirmação, e cada
              confirmação vira uma versão auditável.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {estado === "enviando" ? (
              <div
                role="status"
                aria-live="polite"
                aria-busy="true"
                className="flex items-center gap-3 rounded-xl border border-white/10 bg-surface px-4 py-6 text-sm"
              >
                <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
                Analisando {arquivo ? `“${arquivo.name}”` : "a planilha"} e montando a
                prévia…
              </div>
            ) : (
              <DropzoneArquivo
                id={`${idBase}-config-arquivo`}
                extensoes={EXTENSOES_CONFIG}
                onArquivo={(escolhido) => void enviar(escolhido)}
                onErro={(mensagem) => {
                  setErro(mensagem);
                  setSucesso(null);
                }}
                titulo="Arraste a planilha de configuração aqui ou clique para escolher"
                descricao=".xlsx, .xlsm ou .csv — macros nunca são executadas, só os valores são lidos"
              />
            )}
          </CardContent>
        </Card>
      )}

      {estado === "previa" && previa && arquivo && (
        <section aria-label="Prévia da configuração" className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold tracking-tight">
              Prévia da configuração
            </h2>
            <Badge variant="muted">{arquivo.name}</Badge>
          </div>

          {previa.mapeamento.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Mapeamento de colunas</CardTitle>
                <CardDescription>
                  Como o motor entendeu cada coluna da planilha.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Coluna na planilha</TableHead>
                      <TableHead>Campo no cadastro</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previa.mapeamento.map((par) => (
                      <TableRow key={`${par.coluna}-${par.campo}`}>
                        <TableCell className="text-xs">{par.coluna}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {par.campo}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Cartões novos ({formatarInteiro(previa.cartoesNovos.length)})
                </CardTitle>
              </CardHeader>
              <CardContent>
                {previa.cartoesNovos.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Nenhum cartão novo nesta planilha.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {previa.cartoesNovos.map((item, indice) => (
                      <CartaoNovoItem key={indice} item={item} />
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Cartões atualizados ({formatarInteiro(previa.cartoesAtualizados.length)})
                </CardTitle>
                <CardDescription>Diferenças campo a campo: antes → depois.</CardDescription>
              </CardHeader>
              <CardContent>
                {previa.cartoesAtualizados.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Nenhum cartão existente muda com esta planilha.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {previa.cartoesAtualizados.map((item, indice) => (
                      <CartaoAtualizadoItem key={indice} item={item} />
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm">
              <span>
                Faturas novas:{" "}
                <span className="font-semibold text-profit">
                  {formatarInteiro(previa.faturasNovas)}
                </span>
              </span>
              <span>
                Faturas atualizadas:{" "}
                <span className="font-semibold">
                  {formatarInteiro(previa.faturasAtualizadas)}
                </span>
              </span>
            </CardContent>
          </Card>

          <div className="space-y-2">
            <ListaExpansivel
              titulo="Conflitos que pedem sua atenção"
              tom="ambar"
              itens={previa.conflitos}
              abertaInicialmente
            />
            <ListaExpansivel titulo="Erros por linha" tom="carmim" itens={previa.erros} />
            <ListaExpansivel titulo="Avisos" tom="ambar" itens={previa.avisos} />
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <label
                htmlFor={`${idBase}-aprovado-por`}
                className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
              >
                Aprovado por
              </label>
              <Input
                id={`${idBase}-aprovado-por`}
                className="w-48"
                value={aprovadoPor}
                onChange={(evento) => setAprovadoPor(evento.target.value)}
                placeholder="usuario"
              />
            </div>
            <Button onClick={() => void confirmar()} disabled={confirmando}>
              {confirmando ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Check className="h-4 w-4" />
              )}
              Confirmar e versionar
            </Button>
            <Button variant="outline" onClick={descartar} disabled={confirmando}>
              <Trash2 className="h-4 w-4" />
              Descartar
            </Button>
            <p className="w-full text-xs text-muted-foreground">
              A confirmação grava cartões e faturas em transação atômica e registra uma
              nova versão em <code>config_versoes</code>.
            </p>
          </div>
        </section>
      )}

      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4 text-primary" aria-hidden />
              Histórico de versões
            </CardTitle>
            <CardDescription>
              Cada confirmação vira uma versão — com quem aprovou e quando.
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={recarregarVersoes}
            disabled={carregandoVersoes}
          >
            <RefreshCw className={cn("h-4 w-4", carregandoVersoes && "animate-spin")} />
            Atualizar
          </Button>
        </CardHeader>
        <CardContent>
          {carregandoVersoes && versoes.length === 0 ? (
            <div className="space-y-2">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-2/3" />
            </div>
          ) : versoesIndisponiveis ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              O histórico de versões não está disponível neste ambiente.
            </p>
          ) : versoes.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              Nenhuma versão de configuração ainda. Confirme a primeira planilha acima.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Versão</TableHead>
                  <TableHead>Quando</TableHead>
                  <TableHead>Aprovada por</TableHead>
                  <TableHead>Resumo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versoes.map((versao, indice) => (
                  <TableRow key={versao.id || indice}>
                    <TableCell className="whitespace-nowrap text-xs font-medium">
                      #{versao.id || "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      {formatarDataHoraISO(versao.quando)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      {textoOuTraco(versao.aprovadoPor)}
                    </TableCell>
                    <TableCell className="max-w-[24rem]">
                      <span className="block truncate text-xs text-muted-foreground">
                        {textoOuTraco(versao.resumo)}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default AbaConfig;
